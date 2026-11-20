"""Stand-ins for the web and for an OpenID provider, for the unit tests of signing in with another
provider (no containers): a pair of keys that sign ID tokens, a function that mints a token the way
a provider would (and ones that misbehave), and a fake `fetch` that answers the provider's
description, its keys and its token endpoint."""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from vrum.config import OidcSettings
from vrum.sso_http import Call, ProviderUnreachable, Reply

ISSUER = "https://login.example.com/realm"
CLIENT_ID = "vrum-client"
CLIENT_SECRET = "a-client-secret-value"
NONCE = "n" * 43
REDIRECT = "https://vrum.example.com/board/auth/oidc/callback"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _number(value: int) -> str:
    return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_KEY = ec.generate_private_key(ec.SECP256R1())
RSA_PUBLIC_PEM = RSA_KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)


def rsa_jwk(key: rsa.RSAPrivateKey, kid: str, **extra: str) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "n": _number(numbers.n),
        "e": _number(numbers.e),
        **extra,
    }


def ec_jwk(key: ec.EllipticCurvePrivateKey, kid: str) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "kid": kid,
        "x": b64(numbers.x.to_bytes(32, "big")),
        "y": b64(numbers.y.to_bytes(32, "big")),
    }


def claims(**changes: Any) -> dict[str, Any]:
    """What a provider puts in an ID token for Ada, who confirmed her address. A change of None
    leaves the claim out."""
    now = int(time.time())
    base: dict[str, Any] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "ada-subject-1",
        "iat": now,
        "exp": now + 600,
        "nonce": NONCE,
        "email": "ada@example.com",
        "email_verified": True,
        "name": "Ada Lovelace",
        "preferred_username": "ada",
    }
    base.update(changes)
    return {key: value for key, value in base.items() if value is not None}


def mint(
    payload: dict[str, Any] | None = None,
    *,
    key: Any = RSA_KEY,
    algorithm: str = "RS256",
    kid: str | None = "rsa-1",
) -> str:
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(payload or claims(), key, algorithm=algorithm, headers=headers)


def unsigned(payload: dict[str, Any] | None = None) -> str:
    """A token with the algorithm `none`, which anyone can write."""
    header = b64(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    return f"{header}.{b64(json.dumps(payload or claims()).encode())}."


def hmac_signed_with_the_public_key(payload: dict[str, Any] | None = None) -> str:
    """The classic attack on a verifier that trusts the token's own algorithm: sign with HS256,
    using the provider's public key (which everyone has) as the secret."""
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "rsa-1"}).encode())
    body = b64(json.dumps(payload or claims()).encode())
    signature = hmac.new(RSA_PUBLIC_PEM, f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{b64(signature)}"


def oidc_settings(**changes: Any) -> OidcSettings:
    values: dict[str, Any] = {
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "name": "Example login",
        "scopes": ("openid", "email", "profile"),
        "allowed_domains": (),
    }
    values.update(changes)
    return OidcSettings(**values)


def discovery(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/keys",
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
    }
    document.update(changes)
    return document


def reply(body: Any, status: int = 200) -> Reply:
    return Reply(status, json.dumps(body).encode())


class FakeWeb:
    """Answers the calls Vrum makes to a provider, from a table of address to answer, and keeps
    the calls it was asked."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.answers: dict[str, Reply | Callable[[Call], Reply] | Exception] = {}

    def on(self, url: str, answer: Reply | Callable[[Call], Reply] | Exception) -> None:
        self.answers[url] = answer

    def provider(
        self, *, id_token: str | None = None, keys: list[dict[str, str]] | None = None
    ) -> None:
        """Set up a working OpenID provider whose token endpoint hands out `id_token`."""
        self.on(f"{ISSUER}/.well-known/openid-configuration", reply(discovery()))
        self.on(f"{ISSUER}/keys", reply({"keys": keys or [rsa_jwk(RSA_KEY, "rsa-1")]}))
        if id_token is not None:
            self.on(f"{ISSUER}/token", reply({"id_token": id_token, "access_token": "ignored"}))

    async def __call__(self, call: Call) -> Reply:
        self.calls.append(call)
        answer = self.answers.get(call.url.split("?")[0])
        if answer is None:
            return Reply(404, b"{}")
        if isinstance(answer, Exception):
            raise answer
        return answer(call) if callable(answer) else answer

    def asked(self, url: str) -> list[Call]:
        return [call for call in self.calls if call.url.split("?")[0] == url]


def unreachable() -> ProviderUnreachable:
    return ProviderUnreachable()
