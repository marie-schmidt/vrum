"""Sign in with OpenID Connect: the authorization code flow with PKCE, for any provider that serves
/.well-known/openid-configuration (Google, Microsoft, Okta, Keycloak, Dex and others).

Vrum sends the person to the provider's sign-in page, gets a code back, swaps it for an ID token at
the provider's token endpoint, and reads from the verified token who the person is. The access
token the provider also sends is not used and not kept.

The ID token is verified by PyJWT, not by code of ours: its signature against the provider's
published keys, with only the algorithms RS256, ES256 and PS256 allowed (never `none`, never a
shared-secret algorithm, which is how a public key gets passed off as a secret), its issuer, its
audience, and that it has not expired, with a minute of leeway for clocks. What PyJWT does not
check is added here as two plain comparisons: the nonce this sign-in started with, and `azp` when
there is more than one audience. PyJWT was chosen because the MCP SDK already depends on it (so
no new code is trusted), it makes the algorithm allow-list a required argument, and it is small
enough to read; Authlib would add a larger surface and is moving its JOSE code to another package.
Nothing a provider sends is logged or shown."""

import base64
import hmac
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

import jwt
from jwt import PyJWK

from vrum.accounts import ProviderPerson
from vrum.config import OidcSettings
from vrum.sso_http import (
    Call,
    Fetch,
    ProviderProblem,
    ProviderUnreachable,
    allowed_address,
    with_query,
)
from vrum.sso_http import fetch as real_fetch

log = logging.getLogger("vrum.oidc")

ALLOWED_ALGORITHMS = ("RS256", "ES256", "PS256")
LEEWAY_SECONDS = 60
# How long what a provider says about itself is believed before it is asked again.
DISCOVERY_SECONDS = 3600
KEYS_SECONDS = 3600
# A key id that is not known makes Vrum ask for the keys again, but not more often than this.
KEYS_REFETCH_SECONDS = 60
MOST_KEYS = 50
MOST_TOKEN_CHARS = 16384
_KEY_TYPES = {"RS256": "RSA", "PS256": "RSA", "ES256": "EC"}


@dataclass(frozen=True)
class Endpoints:
    authorization: str
    token: str
    jwks: str
    auth_methods: tuple[str, ...]


@dataclass(frozen=True)
class Jwk:
    kid: str | None
    alg: str | None
    use: str | None
    kty: str
    curve: str | None
    key: Any


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_endpoints(document: Any, issuer: str, *, allow_insecure_http: bool) -> Endpoints:
    """What a provider's description says, checked: its issuer is exactly the one Vrum was set up
    with (so a provider cannot be made to describe another), the addresses it names are https, and
    it can do what Vrum needs."""
    if not isinstance(document, dict) or document.get("issuer") != issuer:
        raise ProviderProblem("failed", "issuer_mismatch")
    addresses: dict[str, str] = {}
    for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        value = document.get(name)
        if not isinstance(value, str) or not allowed_address(
            value, allow_insecure_http=allow_insecure_http
        ):
            raise ProviderProblem("failed", "endpoint_not_allowed")
        addresses[name] = value
    algorithms = document.get("id_token_signing_alg_values_supported")
    if isinstance(algorithms, list) and not set(ALLOWED_ALGORITHMS) & set(algorithms):
        raise ProviderProblem("failed", "no_usable_algorithm")
    challenges = document.get("code_challenge_methods_supported")
    if isinstance(challenges, list) and "S256" not in challenges:
        raise ProviderProblem("failed", "no_pkce")
    methods = document.get("token_endpoint_auth_methods_supported")
    return Endpoints(
        authorization=addresses["authorization_endpoint"],
        token=addresses["token_endpoint"],
        jwks=addresses["jwks_uri"],
        auth_methods=tuple(m for m in methods if isinstance(m, str))
        if isinstance(methods, list)
        else ("client_secret_basic",),
    )


def parse_keys(document: Any) -> list[Jwk]:
    """The signing keys in a provider's key set. A key that is not one Vrum could use is left
    out."""
    found: list[Jwk] = []
    keys = document.get("keys") if isinstance(document, dict) else None
    for data in (keys if isinstance(keys, list) else [])[:MOST_KEYS]:
        if not isinstance(data, dict) or data.get("kty") not in ("RSA", "EC"):
            continue
        try:
            key = PyJWK.from_dict(data).key
        except Exception:
            continue
        found.append(
            Jwk(
                kid=_text(data.get("kid")),
                alg=_text(data.get("alg")),
                use=_text(data.get("use")),
                kty=data["kty"],
                curve=_text(data.get("crv")),
                key=key,
            )
        )
    return found


def choose_key(keys: Sequence[Jwk], kid: str | None, alg: str) -> Any | None:
    """The key that signed a token with this key id and algorithm, or None. A key that says it is
    for something other than signing, or for another algorithm, or is the wrong kind of key for
    the algorithm, is never chosen. With no key id there has to be exactly one candidate."""
    wanted = _KEY_TYPES.get(alg)
    candidates = [
        one
        for one in keys
        if one.kty == wanted
        and one.use in (None, "sig")
        and one.alg in (None, alg)
        and (alg != "ES256" or one.curve == "P-256")
        and (kid is None or one.kid == kid)
    ]
    if kid is None and len(candidates) != 1:
        return None
    return candidates[0].key if candidates else None


def verified_claims(
    token: str, key: Any, *, issuer: str, client_id: str, nonce: str
) -> dict[str, Any]:
    """The claims of an ID token that is signed by `key`, was issued by `issuer` for `client_id`,
    has not expired, and answers the `nonce` this sign-in started with. Raises ProviderProblem
    (with a reason that holds nothing the token said) for anything else."""
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key=key,
            algorithms=list(ALLOWED_ALGORITHMS),
            audience=client_id,
            issuer=issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise ProviderProblem("failed", "token_expired") from None
    except jwt.InvalidSignatureError:
        raise ProviderProblem("failed", "bad_signature") from None
    except jwt.InvalidAudienceError:
        raise ProviderProblem("failed", "wrong_audience") from None
    except jwt.InvalidIssuerError:
        raise ProviderProblem("failed", "wrong_issuer") from None
    except jwt.InvalidAlgorithmError:
        raise ProviderProblem("failed", "algorithm_not_allowed") from None
    except jwt.MissingRequiredClaimError:
        raise ProviderProblem("failed", "claim_missing") from None
    except (jwt.ImmatureSignatureError, jwt.InvalidIssuedAtError):
        raise ProviderProblem("failed", "token_not_yet_valid") from None
    except (jwt.PyJWTError, ValueError, TypeError):
        raise ProviderProblem("failed", "token_not_valid") from None
    # An ID token for several audiences names, in azp, the one it was issued to: it is Vrum.
    audience = claims["aud"]
    authorized = claims.get("azp")
    if (isinstance(audience, list) and len(audience) > 1 and authorized is None) or (
        authorized is not None and authorized != client_id
    ):
        raise ProviderProblem("failed", "wrong_authorized_party")
    sent = claims.get("nonce")
    if not isinstance(sent, str) or not hmac.compare_digest(sent.encode(), nonce.encode()):
        raise ProviderProblem("failed", "wrong_nonce")
    return claims


def person_from_claims(claims: Mapping[str, Any]) -> ProviderPerson:
    """Who a verified ID token says the person is. The address counts as confirmed only when the
    provider says so with `email_verified` true (a real true, or the text "true")."""
    subject = claims.get("sub")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
        raise ProviderProblem("failed", "subject_not_usable")
    email = _text(claims.get("email"))
    said = claims.get("email_verified")
    confirmed = said is True or (isinstance(said, str) and said.strip().lower() == "true")
    name = _text(claims.get("name"))
    username = _text(claims.get("preferred_username"))
    return ProviderPerson(
        provider="oidc",
        subject=subject,
        email=email,
        email_confirmed=email is not None and confirmed,
        name=name or username,
        shown=email or username or name,
    )


class OidcClient:
    def __init__(
        self,
        settings: OidcSettings,
        *,
        redirect_uri: str,
        allow_insecure_http: bool,
        fetch: Fetch = real_fetch,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._redirect_uri = redirect_uri
        self._insecure = allow_insecure_http
        self._fetch = fetch
        self._clock = clock
        self._endpoints: Endpoints | None = None
        self._endpoints_at = 0.0
        self._keys: list[Jwk] | None = None
        self._keys_at = 0.0

    @property
    def discovery_url(self) -> str:
        return self._settings.issuer.rstrip("/") + "/.well-known/openid-configuration"

    async def _get(self, url: str) -> Any:
        """A provider's JSON document. A provider that cannot be reached, or answers with
        anything but 200 and JSON, is unavailable."""
        try:
            reply = await self._fetch(Call(url, {"Accept": "application/json"}))
            if reply.status != 200:
                raise ProviderProblem("unavailable", "bad_status")
            return reply.json()
        except ProviderUnreachable:
            raise ProviderProblem("unavailable", "unreachable") from None

    async def endpoints(self) -> Endpoints:
        now = self._clock()
        if self._endpoints is not None and now - self._endpoints_at < DISCOVERY_SECONDS:
            return self._endpoints
        try:
            document = await self._get(self.discovery_url)
        except ProviderProblem:
            if self._endpoints is not None:
                return self._endpoints
            raise
        self._endpoints = parse_endpoints(
            document, self._settings.issuer, allow_insecure_http=self._insecure
        )
        self._endpoints_at = now
        return self._endpoints

    async def authorization_url(self, *, state: str, nonce: str, challenge: str) -> str:
        endpoints = await self.endpoints()
        return with_query(
            endpoints.authorization,
            {
                "response_type": "code",
                "client_id": self._settings.client_id,
                "redirect_uri": self._redirect_uri,
                "scope": " ".join(self._settings.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )

    async def exchange(self, code: str, verifier: str) -> str:
        """Swap the code for the ID token. The access token is dropped on the floor."""
        endpoints = await self.endpoints()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "code_verifier": verifier,
        }
        headers = {"Accept": "application/json"}
        secret = self._settings.client_secret
        client_id = self._settings.client_id
        if secret is None:
            form["client_id"] = client_id
        elif "client_secret_basic" in endpoints.auth_methods:
            pair = f"{quote_plus(client_id)}:{quote_plus(secret)}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
        elif "client_secret_post" in endpoints.auth_methods:
            form["client_id"], form["client_secret"] = client_id, secret
        else:
            raise ProviderProblem("failed", "client_authentication_not_supported")
        try:
            reply = await self._fetch(Call(endpoints.token, headers, form))
        except ProviderUnreachable:
            raise ProviderProblem("unavailable", "unreachable") from None
        if reply.status != 200:
            raise ProviderProblem("failed", "token_request_refused")
        try:
            body = reply.json()
        except ProviderUnreachable:
            raise ProviderProblem("failed", "token_answer_not_json") from None
        token = body.get("id_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not 0 < len(token) <= MOST_TOKEN_CHARS:
            raise ProviderProblem("failed", "no_id_token")
        return token

    async def _key_for(self, kid: str | None, alg: str) -> Any | None:
        now = self._clock()
        if self._keys is None or now - self._keys_at >= KEYS_SECONDS:
            await self._load_keys(now)
        assert self._keys is not None
        found = choose_key(self._keys, kid, alg)
        if found is None and now - self._keys_at >= KEYS_REFETCH_SECONDS:
            # The provider may have started using a new key since: look again, but not more
            # than once a minute, so that a made-up key id cannot make Vrum ask on every attempt.
            await self._load_keys(now)
            found = choose_key(self._keys, kid, alg)
        return found

    async def _load_keys(self, now: float) -> None:
        endpoints = await self.endpoints()
        self._keys = parse_keys(await self._get(endpoints.jwks))
        self._keys_at = now

    async def person(self, code: str, verifier: str, nonce: str) -> ProviderPerson:
        """Everything after the person came back: swap the code, verify the ID token, and say who
        it is for."""
        token = await self.exchange(code, verifier)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise ProviderProblem("failed", "token_not_valid") from None
        alg = header.get("alg")
        if alg not in ALLOWED_ALGORITHMS:
            raise ProviderProblem("failed", "algorithm_not_allowed")
        kid = header.get("kid")
        key = await self._key_for(kid if isinstance(kid, str) else None, alg)
        if key is None:
            raise ProviderProblem("failed", "key_not_found")
        claims = verified_claims(
            token,
            key,
            issuer=self._settings.issuer,
            client_id=self._settings.client_id,
            nonce=nonce,
        )
        return person_from_claims(claims)
