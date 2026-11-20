"""A scripted OpenID Connect provider for the check stack and the demo.

It lets ./check.sh prove what Vrum does when a person signs in with an outside OpenID provider, and
lets the demo show it, without an account at a real provider. It is not a model of any real
provider: Vrum is also proven against a real one (Dex, in the check stack), and the cases a real
provider cannot be made to produce (a token for another audience, a token with no signature, a
tampered signature, an address that is not confirmed) are made here on purpose, with the control
endpoint below. Nothing in the product knows this file exists. Standard library only; the RSA key
is generated when it starts, with plain integer arithmetic, and signs with RS256.

Run as `python scripted_oidc.py`. Settings, from the environment:
  PORT           the port it listens on (default 8080)
  ISSUER         the address other containers use for it, which is the issuer in the discovery
                 document and in every token (default http://scripted-oidc:8080)
  BROWSER_URL    the address a browser uses for it, which is where the discovery document sends a
                 person to sign in (default http://127.0.0.1:8080)
  CLIENT_ID      the one client it knows (default vrum-check)
  CLIENT_SECRET  its secret; empty makes it a public client that is told apart by PKCE alone
  REDIRECT_URIS  the redirect addresses it accepts, exactly, comma separated (empty: any)
  KEY_BITS       the size of the RSA key (default 2048)
  TITLE          the name on its sign-in page (default Scripted OpenID)

What it serves:
  GET  /.well-known/openid-configuration   the discovery document
  GET  /jwks                               the public key
  GET  /authorize                          a page of fictional people as buttons (the browser's
                                           address); a button sends the browser back to the
                                           redirect address with a code and the state. With
                                           `person=<key>` in the query it does that at once.
  POST /token                              the code exchange: client authentication (basic, post
                                           or none), the redirect address and the PKCE verifier
                                           are all checked; answers with an id token
  GET  /userinfo                           the claims for an access token (Vrum must not need it)
  GET  /_people, POST /_people             the fictional people, and add or replace one:
                                           {key, label?, sub, email?, email_verified?, name?,
                                           preferred_username?}; whatever is given is a claim
  GET  /_log, DELETE /_log                 what was asked of it (`?since=<id>` for later entries)
  GET  /_control, POST /_control           make the next thing misbehave, or reset (below)
  GET  /health

POST /_control takes a JSON object with any of:
  token       what the NEXT id token (one code exchange) gets wrong: one of TOKEN_MODES below
              (a wrong nonce, audience or issuer, expired, not yet valid, `alg: none`, a public key
              used as an HMAC secret, a broken or tampered signature, an unknown key id, an address
              that is not confirmed or is missing, two audiences without `azp`, ...), or "ok"
  authorize   what the NEXT approval at /authorize does instead: "access_denied", "server_error"
              (with an error description that holds markup) or "wrong_state"
  jwks        "down" makes the key set answer 500; "ok" brings it back
  discovery   "down", "wrong_issuer" (the document names another issuer) or "ok"
  auth_methods  the token endpoint authentication methods the discovery document offers and the
              token endpoint accepts, such as ["client_secret_post"]
  seconds     how long "endpoint_slow" waits (default 15)
  reset       true forgets every control and the log (the people stay)
"""

import base64
import hashlib
import hmac
import html
import json
import math
import os
import secrets
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, parse_qsl, unquote_plus, urlencode, urlsplit

LONGEST_BODY = 1024 * 1024
CODE_LIFETIME = 60
TOKEN_LIFETIME = 300
OTHER_AUDIENCE = "another-client"

TOKEN_MODES = {
    "ok": "a good token",
    "wrong_nonce": "the nonce is not the one Vrum sent",
    "wrong_aud": "the audience is another client",
    "wrong_iss": "the issuer is another address",
    "expired": "it ran out an hour ago",
    "not_yet_valid": "it is not valid until an hour from now",
    "two_audiences": "two audiences and no azp",
    "wrong_azp": "two audiences and an azp that is not Vrum's client",
    "two_audiences_with_azp": "two audiences and an azp that is Vrum's client (a good token)",
    "email_unverified": "email_verified is false",
    "email_unverified_string": 'email_verified is the string "false"',
    "email_verified_string": 'email_verified is the string "true" (a good token)',
    "no_email": "no email and no email_verified",
    "missing_sub": "no sub",
    "alg_none": "alg none and no signature",
    "alg_hs256": "HS256, with the public key as the secret",
    "bad_signature": "a signature with a byte changed",
    "tampered_payload": "claims changed after the signature was made",
    "unknown_kid": "signed by the right key under a key id the key set does not have",
    "no_id_token": "the token endpoint answers without an id token",
    "endpoint_error": "the token endpoint answers invalid_grant",
    "endpoint_slow": "the token endpoint waits before it answers",
    "endpoint_redirect": "the token endpoint answers with a redirect",
    "endpoint_huge": "the token endpoint answers with two megabytes",
}
AUTHORIZE_MODES = ("ok", "access_denied", "server_error", "wrong_state")

# People of both the checks and the demo. The last three are the demo's: an address that already
# has a confirmed account there, one whose account was never confirmed, and one who is new.
DEFAULT_PEOPLE: list[dict[str, Any]] = [
    {
        "key": "ava",
        "label": "Ava Stone",
        "sub": "oidc-ava",
        "email": "ava.stone@example.com",
        "email_verified": True,
        "name": "Ava Stone",
        "preferred_username": "ava",
    },
    {
        "key": "ben",
        "label": "Ben Okoye",
        "sub": "oidc-ben",
        "email": "ben.okoye@example.com",
        "email_verified": True,
        "name": "Ben Okoye",
        "preferred_username": "ben",
    },
    {
        "key": "cleo",
        "label": "Cleo Marsh (address not confirmed)",
        "sub": "oidc-cleo",
        "email": "cleo.marsh@example.com",
        "email_verified": False,
        "name": "Cleo Marsh",
        "preferred_username": "cleo",
    },
    {
        "key": "dan",
        "label": "Dan Reyes (no address)",
        "sub": "oidc-dan",
        "name": "Dan Reyes",
        "preferred_username": "dan",
    },
    {
        "key": "eli",
        "label": 'Eli Novak (confirmed, said as the text "true")',
        "sub": "oidc-eli",
        "email": "eli.novak@example.com",
        "email_verified": "true",
        "name": "Eli Novak",
        "preferred_username": "eli",
    },
    {
        "key": "priya",
        "label": "Priya Nair",
        "sub": "oidc-priya",
        "email": "priya@example.com",
        "email_verified": True,
        "name": "Priya Nair",
        "preferred_username": "priya",
    },
    {
        "key": "tom",
        "label": "Tom Berg",
        "sub": "oidc-tom",
        "email": "tom@example.com",
        "email_verified": True,
        "name": "Tom Berg",
        "preferred_username": "tom",
    },
    {
        "key": "noor",
        "label": "Noor Haddad",
        "sub": "oidc-noor",
        "email": "noor@example.com",
        "email_verified": True,
        "name": "Noor Haddad",
        "preferred_username": "noor",
    },
]


# -- the key and the tokens ------------------------------------------------------------------------

_SMALL_PRIMES = [
    p for p in range(3, 2000) if all(p % divisor for divisor in range(2, int(p**0.5) + 1))
]
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_RSA_ENCRYPTION = bytes.fromhex("06092a864886f70d0101010500")
PUBLIC_EXPONENT = 65537


def _probably_prime(candidate: int) -> bool:
    for small in _SMALL_PRIMES:
        if candidate == small:
            return True
        if candidate % small == 0:
            return False
    odd, halvings = candidate - 1, 0
    while odd % 2 == 0:
        odd //= 2
        halvings += 1
    for _ in range(40):
        base = 2 + secrets.randbelow(candidate - 3)
        value = pow(base, odd, candidate)
        if value in (1, candidate - 1):
            continue
        for _ in range(halvings - 1):
            value = pow(value, 2, candidate)
            if value == candidate - 1:
                break
        else:
            return False
    return True


def _random_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _probably_prime(candidate):
            return candidate


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _der(tag: int, content: bytes) -> bytes:
    size = len(content)
    if size < 128:
        length = bytes([size])
    else:
        counted = size.to_bytes((size.bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(counted)]) + counted
    return bytes([tag]) + length + content


def _der_integer(value: int) -> bytes:
    return _der(0x02, value.to_bytes((value.bit_length() + 8) // 8, "big"))


def _padded_digest(message: bytes, length: int) -> bytes:
    """The SHA-256 digest of the message in the shape RSASSA-PKCS1-v1_5 signs (RFC 8017, 9.2)."""
    tail = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    return b"\x00\x01" + b"\xff" * (length - len(tail) - 3) + b"\x00" + tail


class SigningKey:
    """An RSA key that signs with RS256 (RSASSA-PKCS1-v1_5 and SHA-256)."""

    def __init__(self, bits: int = 2048) -> None:
        while True:
            p, q = _random_prime(bits // 2), _random_prime(bits // 2)
            totient = (p - 1) * (q - 1)
            if p != q and (p * q).bit_length() == bits and math.gcd(PUBLIC_EXPONENT, totient) == 1:
                break
        self.n = p * q
        self.e = PUBLIC_EXPONENT
        self._d = pow(PUBLIC_EXPONENT, -1, totient)
        self.size = (self.n.bit_length() + 7) // 8
        self.kid = hashlib.sha256(self.n.to_bytes(self.size, "big")).hexdigest()[:16]

    def sign(self, message: bytes) -> bytes:
        encoded = _padded_digest(message, self.size)
        return pow(int.from_bytes(encoded, "big"), self._d, self.n).to_bytes(self.size, "big")

    def jwk(self) -> dict[str, str]:
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.kid,
            "n": _b64(self.n.to_bytes(self.size, "big")),
            "e": _b64(self.e.to_bytes((self.e.bit_length() + 7) // 8, "big")),
        }

    def public_pem(self) -> bytes:
        """The public key as a PEM (SubjectPublicKeyInfo), which is what a careless verifier
        could be tricked into using as an HMAC secret."""
        key = _der(0x30, _der_integer(self.n) + _der_integer(self.e))
        info = _der(0x30, _der(0x30, _RSA_ENCRYPTION) + _der(0x03, b"\x00" + key))
        body = base64.encodebytes(info).decode("ascii").replace("\n", "")
        lines = [body[start : start + 64] for start in range(0, len(body), 64)]
        return (
            "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----\n"
        ).encode()


def _segment(value: dict[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":")).encode())


def id_token_claims(
    person: dict[str, Any], *, issuer: str, client_id: str, nonce: str | None, now: int, mode: str
) -> dict[str, Any]:
    """The claims of an id token for a person, with what `mode` gets wrong in the claims (the
    modes that spoil the signature or the answer are applied elsewhere)."""
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": client_id,
        "exp": now + TOKEN_LIFETIME,
        "iat": now,
        "auth_time": now,
        **{key: value for key, value in person.items() if key not in ("key", "label")},
    }
    if nonce is not None:
        claims["nonce"] = nonce
    if mode == "wrong_nonce":
        claims["nonce"] = "not-the-nonce-" + secrets.token_hex(8)
    elif mode == "wrong_aud":
        claims["aud"] = OTHER_AUDIENCE
    elif mode == "wrong_iss":
        claims["iss"] = "https://evil.example.com"
    elif mode == "expired":
        claims["exp"], claims["iat"] = now - 3600, now - 7200
    elif mode == "not_yet_valid":
        claims["nbf"], claims["iat"], claims["exp"] = now + 3600, now + 3600, now + 7200
    elif mode == "two_audiences":
        claims["aud"] = [client_id, OTHER_AUDIENCE]
    elif mode == "wrong_azp":
        claims["aud"], claims["azp"] = [client_id, OTHER_AUDIENCE], OTHER_AUDIENCE
    elif mode == "two_audiences_with_azp":
        claims["aud"], claims["azp"] = [client_id, OTHER_AUDIENCE], client_id
    elif mode == "email_unverified":
        claims["email_verified"] = False
    elif mode == "email_unverified_string":
        claims["email_verified"] = "false"
    elif mode == "email_verified_string":
        claims["email_verified"] = "true"
    elif mode == "no_email":
        claims.pop("email", None)
        claims.pop("email_verified", None)
    elif mode == "missing_sub":
        claims.pop("sub", None)
    return claims


def sign_id_token(claims: dict[str, Any], key: SigningKey, mode: str = "ok") -> str:
    """The id token as a compact JWT, with what `mode` gets wrong in the signature or the header."""
    header: dict[str, Any] = {"alg": "RS256", "typ": "JWT", "kid": key.kid}
    if mode == "unknown_kid":
        header["kid"] = "unknown-key"
    if mode == "alg_none":
        header["alg"] = "none"
        header.pop("kid")
        return f"{_segment(header)}.{_segment(claims)}."
    if mode == "alg_hs256":
        header["alg"] = "HS256"
        signing_input = f"{_segment(header)}.{_segment(claims)}"
        mac = hmac.new(key.public_pem(), signing_input.encode(), hashlib.sha256).digest()
        return f"{signing_input}.{_b64(mac)}"
    signing_input = f"{_segment(header)}.{_segment(claims)}"
    signature = key.sign(signing_input.encode())
    if mode == "bad_signature":
        signature = signature[:-1] + bytes([signature[-1] ^ 1])
    elif mode == "tampered_payload":
        other = {
            **claims,
            "sub": str(claims.get("sub", "")) + "-other",
            "email": "attacker@example.com",
        }
        signing_input = f"{_segment(header)}.{_segment(other)}"
    return f"{signing_input}.{_b64(signature)}"


# -- the provider ----------------------------------------------------------------------------------


class Settings:
    def __init__(self, environment: dict[str, str]) -> None:
        self.port = int(environment.get("PORT", "8080"))
        self.issuer = environment.get("ISSUER", "http://scripted-oidc:8080").rstrip("/")
        self.browser_url = environment.get("BROWSER_URL", "http://127.0.0.1:8080").rstrip("/")
        self.client_id = environment.get("CLIENT_ID", "vrum-check")
        self.client_secret = environment.get("CLIENT_SECRET", "")
        self.redirect_uris = [
            one.strip() for one in environment.get("REDIRECT_URIS", "").split(",") if one.strip()
        ]
        self.key_bits = int(environment.get("KEY_BITS", "2048"))
        self.title = environment.get("TITLE", "Scripted OpenID")


class Provider:
    def __init__(self, settings: Settings, key: SigningKey | None = None) -> None:
        self.settings = settings
        self.key = key or SigningKey(settings.key_bits)
        self.lock = threading.Lock()
        self.people = {one["key"]: dict(one) for one in DEFAULT_PEOPLE}
        self.codes: dict[str, dict[str, Any]] = {}
        self.access_tokens: dict[str, str] = {}
        self.log: list[dict[str, Any]] = []
        self.auth_methods = ["client_secret_basic", "client_secret_post"]
        self.next_token = "ok"
        self.next_authorize = "ok"
        self.jwks_state = "ok"
        self.discovery_state = "ok"
        self.slow_seconds = 15.0

    def record(self, entry: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            entry = {
                "id": len(self.log) + 1,
                "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                **entry,
            }
            self.log.append(entry)
            return entry

    def discovery(self) -> dict[str, Any]:
        issuer = (
            "https://evil.example.com"
            if self.discovery_state == "wrong_issuer"
            else self.settings.issuer
        )
        server = self.settings.issuer
        return {
            "issuer": issuer,
            "authorization_endpoint": self.settings.browser_url + "/authorize",
            "token_endpoint": server + "/token",
            "jwks_uri": server + "/jwks",
            "userinfo_endpoint": server + "/userinfo",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email", "profile"],
            "token_endpoint_auth_methods_supported": self.auth_methods,
            "code_challenge_methods_supported": ["S256"],
            "claims_supported": ["sub", "email", "email_verified", "name", "preferred_username"],
        }

    def control(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("reset"):
            self.next_token = self.next_authorize = "ok"
            self.jwks_state = self.discovery_state = "ok"
            self.auth_methods = ["client_secret_basic", "client_secret_post"]
            self.slow_seconds = 15.0
            with self.lock:
                self.log.clear()
                self.codes.clear()
                self.access_tokens.clear()
        if "token" in body:
            if body["token"] not in TOKEN_MODES:
                return {"error": f"token must be one of {sorted(TOKEN_MODES)}"}
            self.next_token = body["token"]
        if "authorize" in body:
            if body["authorize"] not in AUTHORIZE_MODES:
                return {"error": f"authorize must be one of {list(AUTHORIZE_MODES)}"}
            self.next_authorize = body["authorize"]
        if "jwks" in body:
            self.jwks_state = "down" if body["jwks"] == "down" else "ok"
        if "discovery" in body:
            if body["discovery"] not in ("ok", "down", "wrong_issuer"):
                return {"error": 'discovery must be "ok", "down" or "wrong_issuer"'}
            self.discovery_state = body["discovery"]
        if "auth_methods" in body:
            self.auth_methods = [str(one) for one in body["auth_methods"]]
        if "seconds" in body:
            self.slow_seconds = float(body["seconds"])
        return self.state()

    def state(self) -> dict[str, Any]:
        return {
            "token": self.next_token,
            "authorize": self.next_authorize,
            "jwks": self.jwks_state,
            "discovery": self.discovery_state,
            "auth_methods": self.auth_methods,
            "seconds": self.slow_seconds,
            "token_modes": TOKEN_MODES,
        }

    def add_person(self, body: dict[str, Any]) -> dict[str, Any] | None:
        key = body.get("key")
        if not isinstance(key, str) or not key:
            return None
        person = {name: value for name, value in body.items() if value is not None}
        person.setdefault("label", str(body.get("name") or key))
        with self.lock:
            self.people[key] = person
        return person

    # -- authorize ---------------------------------------------------------------------------------

    def check_authorize(self, params: dict[str, str]) -> str | None:
        """What is wrong with an authorization request so badly that the browser must not be sent
        back to the client (an unknown client or redirect address), or None."""
        if params.get("client_id") != self.settings.client_id:
            return "The client_id is not one this provider knows."
        allowed = self.settings.redirect_uris
        if allowed and params.get("redirect_uri") not in allowed:
            return "The redirect_uri is not registered for this client."
        if not params.get("redirect_uri"):
            return "There is no redirect_uri."
        return None

    def back(self, params: dict[str, str], **answer: str) -> str:
        sent = {name: value for name, value in answer.items() if value}
        if params.get("state") and "state" not in sent:
            sent["state"] = params["state"]
        address = params["redirect_uri"]
        return address + ("&" if "?" in address else "?") + urlencode(sent)

    def approve(self, params: dict[str, str], person_key: str) -> tuple[str, str]:
        """Where to send the browser after `person_key` approved, and what happened."""
        mode, self.next_authorize = self.next_authorize, "ok"
        if mode == "access_denied":
            return self.back(params, error="access_denied", error_description="No."), "denied"
        if mode == "server_error":
            noted = "<script>alert('from the provider')</script> something went wrong"
            return self.back(params, error="server_error", error_description=noted), "error"
        if params.get("response_type") != "code":
            return self.back(params, error="unsupported_response_type"), "refused"
        if "openid" not in params.get("scope", "").split():
            return self.back(params, error="invalid_scope"), "refused"
        if not params.get("code_challenge") or params.get("code_challenge_method") != "S256":
            return self.back(params, error="invalid_request"), "refused (no PKCE S256)"
        code = secrets.token_urlsafe(32)
        with self.lock:
            self.codes[code] = {
                "person": person_key,
                "redirect_uri": params["redirect_uri"],
                "nonce": params.get("nonce"),
                "challenge": params["code_challenge"],
                "at": time.time(),
                "used": False,
            }
        state = "forged-state" if mode == "wrong_state" else None
        return self.back(params, code=code, state=state or ""), "approved"

    # -- token -------------------------------------------------------------------------------------

    def authenticate(self, form: dict[str, str], authorization: str) -> tuple[str, str | None]:
        """The way the client proved itself, and what is wrong with that, if anything."""
        settings = self.settings
        if authorization.lower().startswith("basic "):
            try:
                raw = base64.b64decode(authorization[6:]).decode()
            except ValueError:
                return "basic", "invalid_client"
            name, _, secret = raw.partition(":")
            given = (unquote_plus(name), unquote_plus(secret))
            way = "basic"
        elif "client_secret" in form:
            given, way = (form.get("client_id", ""), form["client_secret"]), "post"
        else:
            given, way = (form.get("client_id", ""), ""), "none"
        if given[0] != settings.client_id:
            return way, "invalid_client"
        if not settings.client_secret:
            return way, None if way == "none" else "invalid_client"
        if way == "none" or f"client_secret_{way}" not in self.auth_methods:
            return way, "invalid_client"
        if not hmac.compare_digest(given[1].encode(), settings.client_secret.encode()):
            return way, "invalid_client"
        return way, None

    def exchange(self, form: dict[str, str], authorization: str) -> dict[str, Any]:
        """Answer a code exchange: status, body, result and client_auth, and the delay or the
        redirect when there is one."""
        way, problem = self.authenticate(form, authorization)
        outcome: dict[str, Any] = {"client_auth": way}
        if problem:
            return {**outcome, "status": 401, "body": {"error": problem}, "result": problem}
        if form.get("grant_type") != "authorization_code":
            return {
                **outcome,
                "status": 400,
                "body": {"error": "unsupported_grant_type"},
                "result": "unsupported_grant_type",
            }
        with self.lock:
            issued = self.codes.get(form.get("code", ""))
            replay = bool(issued and issued["used"])
            if issued:
                issued["used"] = True
        invalid = {**outcome, "status": 400, "body": {"error": "invalid_grant"}}
        if not issued or replay or time.time() - issued["at"] > CODE_LIFETIME:
            return {**invalid, "result": "invalid_grant (replayed)" if replay else "invalid_grant"}
        if form.get("redirect_uri") != issued["redirect_uri"]:
            return {**invalid, "result": "invalid_grant (redirect_uri differs)"}
        verifier = form.get("code_verifier", "")
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        if not verifier or not hmac.compare_digest(challenge, issued["challenge"]):
            return {**invalid, "result": "invalid_grant (PKCE mismatch)"}
        mode, self.next_token = self.next_token, "ok"
        if mode == "endpoint_error":
            return {**invalid, "result": "invalid_grant (scripted)"}
        if mode == "endpoint_redirect":
            return {
                **outcome,
                "status": 302,
                "body": {},
                "redirect": self.settings.issuer + "/elsewhere",
                "result": "redirect",
            }
        person = self.people[issued["person"]]
        access_token = secrets.token_urlsafe(24)
        with self.lock:
            self.access_tokens[access_token] = issued["person"]
        answer: dict[str, Any] = {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": TOKEN_LIFETIME,
        }
        if mode != "no_id_token":
            claims = id_token_claims(
                person,
                issuer=self.settings.issuer,
                client_id=self.settings.client_id,
                nonce=issued["nonce"],
                now=int(time.time()),
                mode=mode,
            )
            answer["id_token"] = sign_id_token(claims, self.key, mode)
        if mode == "endpoint_huge":
            answer["padding"] = "x" * (2 * LONGEST_BODY)
        delay = self.slow_seconds if mode == "endpoint_slow" else 0.0
        return {
            **outcome,
            "status": 200,
            "body": answer,
            "delay": delay,
            "result": f"issued ({mode})",
        }


# -- the web side ----------------------------------------------------------------------------------


def _page(title: str, body: str) -> bytes:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>"
        "body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:2rem auto;padding:0 1rem}"
        "button{display:block;width:100%;text-align:left;font:inherit;padding:.6rem .8rem;"
        "margin:.5rem 0;border:1px solid #888;border-radius:8px;background:#fff;cursor:pointer}"
        "button:hover,button:focus-visible{background:#eef}small{color:#555}p.note{color:#555}"
        f"</style></head><body>{body}</body></html>"
    ).encode()


def authorize_page(provider: Provider, params: dict[str, str]) -> bytes:
    fields = "".join(
        f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(value)}">'
        for name, value in params.items()
    )
    buttons = "".join(
        f'<button type="submit" name="person" value="{html.escape(key)}" '
        f'data-person="{html.escape(key)}">{html.escape(str(one.get("label", key)))}'
        f"<br><small>{html.escape(str(one.get('email', 'no email address')))}</small></button>"
        for key, one in provider.people.items()
    )
    title = provider.settings.title
    return _page(
        f"{title}: sign in",
        f"<h1>{html.escape(title)}</h1><p>Choose who you are. This is a stand-in provider for "
        "tests and the demo: nothing real is contacted, and there is no password.</p>"
        f'<form method="post" action="/authorize">{fields}{buttons}'
        '<button type="submit" name="cancel" value="1" data-person="cancel">Cancel</button></form>',
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    provider: Provider

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def _send(
        self,
        status: int,
        body: bytes,
        kind: str = "application/json",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _json(self, status: int, value: Any, headers: dict[str, str] | None = None) -> None:
        self._send(status, json.dumps(value).encode(), headers=headers)

    def _body(self) -> bytes:
        size = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(min(size, LONGEST_BODY))

    def _form(self) -> dict[str, str]:
        return dict(parse_qsl(self._body().decode("utf-8", "replace"), keep_blank_values=True))

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def do_DELETE(self) -> None:
        self._route("DELETE")

    def _route(self, method: str) -> None:
        provider = self.provider
        url = urlsplit(self.path)
        query = {name: values[-1] for name, values in parse_qs(url.query).items()}
        path = url.path
        if path == "/health" and method == "GET":
            self._json(200, {"status": "ok"})
        elif path.startswith("/_"):
            self._control(method, path, query)
        elif path == "/" and method == "GET":
            self._send(
                200, _page(provider.settings.title, self._home()), "text/html; charset=utf-8"
            )
        elif path == "/.well-known/openid-configuration" and method == "GET":
            provider.record({"method": method, "path": path})
            if provider.discovery_state == "down":
                self._json(500, {"error": "scripted: discovery is down"})
            else:
                self._json(200, provider.discovery())
        elif path == "/jwks" and method == "GET":
            provider.record({"method": method, "path": path})
            if provider.jwks_state == "down":
                self._json(500, {"error": "scripted: the key set is down"})
            else:
                self._json(200, {"keys": [provider.key.jwk()]})
        elif path == "/authorize" and method in ("GET", "POST"):
            self._authorize(method, query)
        elif path == "/token" and method == "POST":
            self._token()
        elif path == "/userinfo" and method in ("GET", "POST"):
            self._userinfo()
        elif path == "/elsewhere":
            provider.record(
                {"method": method, "path": path, "result": "somebody followed a redirect"}
            )
            self._json(200, {"followed": True})
        else:
            self._json(404, {"error": "not found"})

    def _home(self) -> str:
        provider = self.provider
        return (
            f"<h1>{html.escape(provider.settings.title)}</h1>"
            '<p class="note">A stand-in OpenID provider for tests and the demo. Nothing real is '
            'contacted.</p><p><a href="/.well-known/openid-configuration">Discovery document</a>'
            ' · <a href="/jwks">Keys</a></p><p>People: '
            + ", ".join(
                html.escape(str(one.get("label", key))) for key, one in provider.people.items()
            )
            + "</p>"
        )

    def _authorize(self, method: str, query: dict[str, str]) -> None:
        provider = self.provider
        params = {**query, **(self._form() if method == "POST" else {})}
        shown = {name: value for name, value in params.items() if name not in ("person", "cancel")}
        problem = provider.check_authorize(shown)
        if problem:
            provider.record(
                {"method": method, "path": "/authorize", "query": shown, "result": problem}
            )
            self._send(
                400,
                _page("Cannot sign in", f"<h1>Cannot sign in</h1><p>{html.escape(problem)}</p>"),
                "text/html; charset=utf-8",
            )
            return
        if params.get("cancel"):
            where = provider.back(
                shown, error="access_denied", error_description="The person said no."
            )
            provider.record(
                {"method": method, "path": "/authorize", "query": shown, "result": "cancelled"}
            )
            self._send(303, b"", headers={"Location": where})
        elif params.get("person") in provider.people:
            where, result = provider.approve(shown, params["person"])
            provider.record(
                {
                    "method": method,
                    "path": "/authorize",
                    "query": shown,
                    "person": params["person"],
                    "result": result,
                    "redirect": where,
                }
            )
            self._send(303, b"", headers={"Location": where})
        else:
            provider.record(
                {"method": method, "path": "/authorize", "query": shown, "result": "page"}
            )
            self._send(200, authorize_page(provider, shown), "text/html; charset=utf-8")

    def _token(self) -> None:
        provider = self.provider
        form = self._form()
        answer = provider.exchange(form, self.headers.get("Authorization", ""))
        shown = {name: value for name, value in form.items() if name != "client_secret"}
        provider.record(
            {
                "method": "POST",
                "path": "/token",
                "form": shown,
                "client_auth": answer["client_auth"],
                "status": answer["status"],
                "result": answer["result"],
                # What was handed out, so that a check can look for it where it must never be.
                "issued": {
                    name: answer["body"][name]
                    for name in ("access_token", "id_token")
                    if name in answer["body"]
                },
            }
        )
        if answer.get("delay"):
            time.sleep(answer["delay"])
        headers = {"Location": answer["redirect"]} if answer.get("redirect") else None
        self._json(answer["status"], answer["body"], headers)

    def _userinfo(self) -> None:
        provider = self.provider
        token = self.headers.get("Authorization", "")[7:]
        key = provider.access_tokens.get(token)
        provider.record(
            {
                "method": self.command,
                "path": "/userinfo",
                "result": "known token" if key else "unknown token",
            }
        )
        if key is None:
            self._json(401, {"error": "invalid_token"})
            return
        claims = {
            name: value
            for name, value in provider.people[key].items()
            if name not in ("key", "label")
        }
        self._json(200, claims)

    def _control(self, method: str, path: str, query: dict[str, str]) -> None:
        provider = self.provider
        if path == "/_log" and method == "GET":
            since = int(query.get("since", "0"))
            self._json(200, [one for one in list(provider.log) if one["id"] > since])
        elif path == "/_log" and method == "DELETE":
            with provider.lock:
                provider.log.clear()
            self._json(200, {"deleted": True})
        elif path == "/_people" and method == "GET":
            self._json(200, list(provider.people.values()))
        elif path in ("/_people", "/_control") and method == "POST":
            try:
                body = json.loads(self._body() or b"{}")
            except ValueError:
                body = None
            if not isinstance(body, dict):
                self._json(400, {"error": "send a JSON object"})
            elif path == "/_people":
                person = provider.add_person(body)
                self._json(201 if person else 400, person or {"error": "a person needs a key"})
            else:
                answer = provider.control(body)
                self._json(400 if "error" in answer else 200, answer)
        elif path == "/_control" and method == "GET":
            self._json(200, provider.state())
        else:
            self._json(404, {"error": "not found"})


def serve(provider: Provider) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"provider": provider})
    server = ThreadingHTTPServer(("0.0.0.0", provider.settings.port), handler)
    server.daemon_threads = True
    return server


if __name__ == "__main__":
    configured = Settings(dict(os.environ))
    print(f"scripted-oidc: making a {configured.key_bits}-bit key", flush=True)
    serve(Provider(configured)).serve_forever()
