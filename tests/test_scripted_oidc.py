"""The scripted OpenID provider (tests/scripted_oidc.py): that the tokens it makes are what its
documentation says, checked against verifiers that are not its own code: PyJWT and `cryptography`
for the signature and the claims, and plain integer arithmetic from the RFC for the signature alone.
And that its endpoints (discovery, keys, authorize, token, controls, log) behave, over real HTTP on
this machine. No containers."""

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tests import scripted_oidc as oidc

ISSUER = "http://scripted-oidc:8080"
CLIENT = "vrum-check"
SECRET = "s3cret-for-tests"
CALLBACK = "http://localhost:21610/board/auth/oidc/callback"
NOW = int(time.time())
PERSON = {
    "key": "ava",
    "label": "Ava Stone",
    "sub": "oidc-ava",
    "email": "ava.stone@example.com",
    "email_verified": True,
    "name": "Ava Stone",
}


@pytest.fixture(scope="module")
def key() -> oidc.SigningKey:
    return oidc.SigningKey(2048)


def b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def parts(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    head, body, signature = token.split(".")
    return (
        json.loads(b64decode(head)),
        json.loads(b64decode(body)),
        b64decode(signature),
        f"{head}.{body}".encode(),
    )


def token_for(key: oidc.SigningKey, mode: str, nonce: str = "n-0S6_WzA2Mj") -> str:
    claims = oidc.id_token_claims(
        PERSON, issuer=ISSUER, client_id=CLIENT, nonce=nonce, now=NOW, mode=mode
    )
    return oidc.sign_id_token(claims, key, mode)


# -- what a verifier that is not ours says -------------------------------------------------------


class Refused(Exception):
    pass


def verified(token: str, key: oidc.SigningKey, nonce: str = "n-0S6_WzA2Mj") -> dict[str, Any]:
    """What a strict relying party does with the token: the key by its id from the published key
    set, PyJWT for the signature, the allowed algorithms, the issuer, the audience and the times
    (a minute of leeway), then the nonce by hand."""
    header = jwt.get_unverified_header(token)
    if header.get("kid", key.kid) != key.kid:
        raise Refused("unknown key id")
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            jwt.PyJWK(key.jwk()).key,
            algorithms=["RS256", "ES256", "PS256"],
            audience=CLIENT,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=60,
        )
    except jwt.PyJWTError as problem:
        raise Refused(type(problem).__name__) from problem
    if claims.get("nonce") != nonce:
        raise Refused("nonce")
    return claims


# -- the signature, by the RFC's own arithmetic ----------------------------------------------------


def rs256_is_valid(token: str, key: oidc.SigningKey) -> bool:
    """RSASSA-PKCS1-v1_5 verification as RFC 8017 describes it: raise the signature to e, and see
    that what comes out is 0x00 0x01, 0xff padding, 0x00 and the DigestInfo of SHA-256."""
    _, _, signature, signing_input = parts(token)
    if len(signature) != key.size:
        return False
    plain = pow(int.from_bytes(signature, "big"), key.e, key.n).to_bytes(key.size, "big")
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420")
    expected = digest_info + hashlib.sha256(signing_input).digest()
    return plain == b"\x00\x01" + b"\xff" * (key.size - len(expected) - 3) + b"\x00" + expected


def test_the_key_is_a_real_2048_bit_rsa_key_and_the_jwk_and_pem_describe_it(
    key: oidc.SigningKey,
) -> None:
    assert key.n.bit_length() == 2048 and key.e == 65537
    public = serialization.load_pem_public_key(key.public_pem())
    assert isinstance(public, rsa.RSAPublicKey)
    numbers = public.public_numbers()
    assert (numbers.n, numbers.e) == (key.n, key.e)
    jwk = key.jwk()
    assert jwk["kty"] == "RSA" and jwk["alg"] == "RS256" and jwk["use"] == "sig"
    assert int.from_bytes(b64decode(jwk["n"]), "big") == key.n
    assert int.from_bytes(b64decode(jwk["e"]), "big") == key.e
    assert jwk["kid"] == key.kid and len(key.kid) == 16


def test_a_good_token_passes_the_rfc_arithmetic_and_a_strict_library(key: oidc.SigningKey) -> None:
    token = token_for(key, "ok")
    header, claims, _, _ = parts(token)
    assert header == {"alg": "RS256", "typ": "JWT", "kid": key.kid}
    assert rs256_is_valid(token, key)
    checked = verified(token, key)
    assert checked["sub"] == "oidc-ava" and checked["email"] == "ava.stone@example.com"
    assert checked["email_verified"] is True and checked["nonce"] == "n-0S6_WzA2Mj"
    assert claims["iss"] == ISSUER and claims["aud"] == CLIENT
    assert claims["exp"] - claims["iat"] == oidc.TOKEN_LIFETIME
    assert "label" not in claims and "key" not in claims


def test_changing_one_byte_of_a_good_token_breaks_the_arithmetic_check(
    key: oidc.SigningKey,
) -> None:
    head, body, signature = token_for(key, "ok").split(".")
    broken = signature[:-2] + ("A" if signature[-2] != "A" else "B") + signature[-1]
    assert not rs256_is_valid(f"{head}.{body}.{broken}", key)


@pytest.mark.parametrize(
    ("mode", "why"),
    [
        ("wrong_aud", {"InvalidAudienceError"}),
        ("wrong_iss", {"InvalidIssuerError"}),
        ("wrong_nonce", {"nonce"}),
        ("expired", {"ExpiredSignatureError"}),
        ("not_yet_valid", {"InvalidIssuedAtError", "ImmatureSignatureError"}),
        ("bad_signature", {"InvalidSignatureError"}),
        ("tampered_payload", {"InvalidSignatureError"}),
        ("unknown_kid", {"unknown key id"}),
        ("alg_none", {"InvalidAlgorithmError"}),
        ("alg_hs256", {"InvalidAlgorithmError"}),
        ("missing_sub", {"MissingRequiredClaimError"}),
    ],
)
def test_a_strict_library_refuses_what_the_stand_in_spoiled(
    key: oidc.SigningKey, mode: str, why: set[str]
) -> None:
    with pytest.raises(Refused) as refused:
        verified(token_for(key, mode), key)
    assert str(refused.value) in why


@pytest.mark.parametrize(
    "mode",
    [
        "ok",
        "two_audiences",
        "wrong_azp",
        "two_audiences_with_azp",
        "email_unverified",
        "email_unverified_string",
        "email_verified_string",
        "no_email",
    ],
)
def test_a_strict_library_accepts_what_is_for_the_application_to_judge(
    key: oidc.SigningKey, mode: str
) -> None:
    # The signature, issuer, audience list and times are fine; the azp rule and what an
    # unconfirmed or missing address means are Vrum's to decide, and the checks are Vrum's tests.
    assert verified(token_for(key, mode), key)["sub"] == "oidc-ava"


def test_the_signatures_that_should_be_good_are_and_the_broken_ones_are_not(
    key: oidc.SigningKey,
) -> None:
    for mode in ("ok", "wrong_nonce", "wrong_aud", "wrong_iss", "expired", "not_yet_valid"):
        assert rs256_is_valid(token_for(key, mode), key), mode
    for mode in ("email_unverified", "no_email", "two_audiences", "wrong_azp", "missing_sub"):
        assert rs256_is_valid(token_for(key, mode), key), mode
    for mode in ("bad_signature", "tampered_payload", "alg_none", "alg_hs256"):
        assert not rs256_is_valid(token_for(key, mode), key), mode


def test_the_times_the_stand_in_spoils_are_beyond_a_minutes_leeway(key: oidc.SigningKey) -> None:
    expired = parts(token_for(key, "expired"))[1]
    assert NOW - expired["exp"] >= 3600
    early = parts(token_for(key, "not_yet_valid"))[1]
    assert early["nbf"] - NOW >= 3600 and early["iat"] - NOW >= 3600


def test_the_unsigned_token_has_alg_none_and_nothing_after_the_second_dot(
    key: oidc.SigningKey,
) -> None:
    token = token_for(key, "alg_none")
    assert token.endswith(".") and token.count(".") == 2
    assert parts(token)[0] == {"alg": "none", "typ": "JWT"}
    assert parts(token)[2] == b""


def test_the_hs256_token_is_signed_with_the_public_key_as_the_secret(key: oidc.SigningKey) -> None:
    token = token_for(key, "alg_hs256")
    header, _, signature, signing_input = parts(token)
    assert header["alg"] == "HS256" and header["kid"] == key.kid
    # The confusion attack: a verifier that trusted the header would take the public key (as a
    # PEM) for an HMAC secret, and this is the signature it would be happy with.
    pem = key.public_pem()
    assert pem.startswith(b"-----BEGIN PUBLIC KEY-----\n")
    assert hmac.compare_digest(signature, hmac.new(pem, signing_input, hashlib.sha256).digest())


@pytest.mark.parametrize(
    ("mode", "check"),
    [
        ("wrong_nonce", lambda c: c["nonce"] != "n-0S6_WzA2Mj"),
        ("email_unverified", lambda c: c["email_verified"] is False),
        ("email_unverified_string", lambda c: c["email_verified"] == "false"),
        ("email_verified_string", lambda c: c["email_verified"] == "true"),
        ("no_email", lambda c: "email" not in c and "email_verified" not in c),
        ("missing_sub", lambda c: "sub" not in c),
        ("two_audiences", lambda c: c["aud"] == [CLIENT, oidc.OTHER_AUDIENCE] and "azp" not in c),
        ("wrong_azp", lambda c: c["azp"] == oidc.OTHER_AUDIENCE),
        ("two_audiences_with_azp", lambda c: c["azp"] == CLIENT and CLIENT in c["aud"]),
    ],
)
def test_each_claim_mode_changes_exactly_what_it_says(
    key: oidc.SigningKey, mode: str, check: Any
) -> None:
    claims = parts(token_for(key, mode))[1]
    assert check(claims)
    assert claims["iss"] == ISSUER or mode == "wrong_iss"


def test_the_tampered_payload_keeps_the_header_and_the_old_signature(key: oidc.SigningKey) -> None:
    good, tampered = token_for(key, "ok"), token_for(key, "tampered_payload")
    assert good.split(".")[0] == tampered.split(".")[0]
    assert good.split(".")[2] == tampered.split(".")[2]
    assert parts(tampered)[1]["email"] == "attacker@example.com"


def test_every_documented_mode_exists_and_the_odd_ones_do_not_break_minting(
    key: oidc.SigningKey,
) -> None:
    for mode in oidc.TOKEN_MODES:
        token = token_for(key, mode)
        assert token.count(".") == 2


# -- the web side, over real HTTP ------------------------------------------------------------------


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **more: Any) -> None:
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class Answer:
    def __init__(self, status: int, headers: Any, raw: bytes) -> None:
        self.status, self.headers, self.raw = status, headers, raw

    @property
    def json(self) -> Any:
        return json.loads(self.raw)

    @property
    def location(self) -> str:
        return str(self.headers["Location"])


def ask(
    base: str,
    method: str,
    path: str,
    *,
    body: Any = None,
    form: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Answer:
    data = None
    sent = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        sent["Content-Type"] = "application/json"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        sent["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(base + path, data=data, method=method, headers=sent)
    try:
        with OPENER.open(request, timeout=20) as response:
            return Answer(response.status, response.headers, response.read())
    except urllib.error.HTTPError as problem:
        return Answer(problem.code, problem.headers, problem.read())


@pytest.fixture
def provider(key: oidc.SigningKey) -> Iterator[str]:
    settings = oidc.Settings(
        {
            "PORT": "0",
            "ISSUER": ISSUER,
            "BROWSER_URL": "http://127.0.0.1:9",
            "CLIENT_ID": CLIENT,
            "CLIENT_SECRET": SECRET,
            "REDIRECT_URIS": CALLBACK,
        }
    )
    server = oidc.serve(oidc.Provider(settings, key))
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def pkce() -> tuple[str, str]:
    verifier = "v" * 43
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(
        b"="
    ).decode()


def basic(secret: str = SECRET) -> dict[str, str]:
    raw = base64.b64encode(f"{CLIENT}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def authorization(challenge: str, **more: str) -> str:
    return "/authorize?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT,
            "redirect_uri": CALLBACK,
            "scope": "openid email profile",
            "state": "the-state",
            "nonce": "the-nonce",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **more,
        }
    )


def approved(base: str, person: str = "ava", **more: str) -> tuple[dict[str, list[str]], str]:
    verifier, challenge = pkce()
    answer = ask(base, "GET", authorization(challenge, person=person, **more))
    assert answer.status == 303, answer.raw
    return urllib.parse.parse_qs(urllib.parse.urlsplit(answer.location).query), verifier


def exchange(base: str, code: str, verifier: str, **more: str) -> Answer:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": CALLBACK,
        "code_verifier": verifier,
        **more,
    }
    return ask(base, "POST", "/token", form=form, headers=basic())


def test_the_discovery_document_gives_the_browser_one_address_and_the_server_another(
    provider: str,
) -> None:
    document = ask(provider, "GET", "/.well-known/openid-configuration").json
    assert document["issuer"] == ISSUER
    assert document["authorization_endpoint"] == "http://127.0.0.1:9/authorize"
    assert document["token_endpoint"] == ISSUER + "/token"
    assert document["jwks_uri"] == ISSUER + "/jwks"
    assert document["id_token_signing_alg_values_supported"] == ["RS256"]
    assert document["code_challenge_methods_supported"] == ["S256"]
    keys = ask(provider, "GET", "/jwks").json["keys"]
    assert [one["kid"] for one in keys] and keys[0]["alg"] == "RS256"


def test_the_sign_in_page_lists_every_person_as_a_button_and_a_button_sends_the_browser_back(
    provider: str,
) -> None:
    _, challenge = pkce()
    page = ask(provider, "GET", authorization(challenge))
    assert page.status == 200
    text = page.raw.decode()
    for person in oidc.DEFAULT_PEOPLE:
        assert f'data-person="{person["key"]}"' in text
    assert 'data-person="cancel"' in text and 'value="the-state"' in text
    # What the button posts is what the page held, and it goes back with a code and the state.
    answer = ask(
        provider,
        "POST",
        "/authorize",
        form={
            "response_type": "code",
            "client_id": CLIENT,
            "redirect_uri": CALLBACK,
            "scope": "openid",
            "state": "abc",
            "nonce": "n",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "person": "ben",
        },
    )
    back = urllib.parse.urlsplit(answer.location)
    assert answer.status == 303 and f"{back.scheme}://{back.netloc}{back.path}" == CALLBACK
    query = urllib.parse.parse_qs(back.query)
    assert query["state"] == ["abc"] and len(query["code"][0]) >= 40


def test_a_redirect_address_that_is_not_registered_is_never_redirected_to(provider: str) -> None:
    _, challenge = pkce()
    path = authorization(challenge, redirect_uri="https://evil.example.com/steal", person="ava")
    answer = ask(provider, "GET", path)
    assert answer.status == 400 and "Location" not in answer.headers
    assert ask(provider, "GET", authorization(challenge, client_id="other")).status == 400


def test_the_code_exchange_gives_an_id_token_for_the_person_with_the_nonce_asked_for(
    provider: str, key: oidc.SigningKey
) -> None:
    query, verifier = approved(provider, "ava")
    answer = exchange(provider, query["code"][0], verifier)
    assert answer.status == 200 and answer.headers["Cache-Control"] == "no-store"
    claims = verified(answer.json["id_token"], key, "the-nonce")
    assert claims["nonce"] == "the-nonce" and claims["sub"] == "oidc-ava"
    assert answer.json["token_type"] == "Bearer"


def test_a_code_works_once_and_only_with_the_right_verifier_secret_and_address(
    provider: str,
) -> None:
    query, verifier = approved(provider)
    code = query["code"][0]
    assert exchange(provider, code, "w" * 43).status == 400
    # The failed try used the code up, as a careful provider does.
    assert exchange(provider, code, verifier).json == {"error": "invalid_grant"}
    query, verifier = approved(provider)
    wrong_secret = ask(
        provider,
        "POST",
        "/token",
        form={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
        },
        headers=basic("nope"),
    )
    assert wrong_secret.status == 401 and wrong_secret.json == {"error": "invalid_client"}
    query, verifier = approved(provider)
    other = exchange(provider, query["code"][0], verifier, redirect_uri=CALLBACK + "2")
    assert other.status == 400
    query, verifier = approved(provider)
    assert exchange(provider, query["code"][0], verifier).status == 200
    assert exchange(provider, query["code"][0], verifier).status == 400


def test_the_secret_may_come_in_the_form_when_the_document_offers_that(provider: str) -> None:
    ask(provider, "POST", "/_control", body={"auth_methods": ["client_secret_post"]})
    query, verifier = approved(provider)
    form = {
        "grant_type": "authorization_code",
        "code": query["code"][0],
        "redirect_uri": CALLBACK,
        "code_verifier": verifier,
        "client_id": CLIENT,
        "client_secret": SECRET,
    }
    assert ask(provider, "POST", "/token", form=form).status == 200
    query, verifier = approved(provider)
    assert exchange(provider, query["code"][0], verifier).status == 401
    document = ask(provider, "GET", "/.well-known/openid-configuration").json
    assert document["token_endpoint_auth_methods_supported"] == ["client_secret_post"]


def test_a_request_without_pkce_is_refused_at_the_authorization_endpoint(provider: str) -> None:
    path = authorization("x", person="ava").replace("&code_challenge_method=S256", "")
    back = urllib.parse.urlsplit(ask(provider, "GET", path).location)
    assert urllib.parse.parse_qs(back.query)["error"] == ["invalid_request"]


def test_the_next_token_misbehaves_once_and_then_it_is_good_again(
    provider: str, key: oidc.SigningKey
) -> None:
    assert ask(provider, "POST", "/_control", body={"token": "wrong_aud"}).status == 200
    query, verifier = approved(provider)
    spoiled = exchange(provider, query["code"][0], verifier).json["id_token"]
    with pytest.raises(Refused, match="InvalidAudienceError"):
        verified(spoiled, key, "the-nonce")
    query, verifier = approved(provider)
    assert verified(
        exchange(provider, query["code"][0], verifier).json["id_token"], key, "the-nonce"
    )
    assert ask(provider, "POST", "/_control", body={"token": "nonsense"}).status == 400


def test_the_token_endpoint_can_be_made_to_fail_in_the_ways_a_client_must_survive(
    provider: str,
) -> None:
    ask(provider, "POST", "/_control", body={"token": "no_id_token"})
    query, verifier = approved(provider)
    assert "id_token" not in exchange(provider, query["code"][0], verifier).json
    ask(provider, "POST", "/_control", body={"token": "endpoint_error"})
    query, verifier = approved(provider)
    assert exchange(provider, query["code"][0], verifier).status == 400
    ask(provider, "POST", "/_control", body={"token": "endpoint_redirect"})
    query, verifier = approved(provider)
    redirected = exchange(provider, query["code"][0], verifier)
    assert redirected.status == 302 and redirected.location.endswith("/elsewhere")
    ask(provider, "POST", "/_control", body={"token": "endpoint_huge"})
    query, verifier = approved(provider)
    assert len(exchange(provider, query["code"][0], verifier).raw) > 2 * 1024 * 1024


def test_an_approval_can_be_made_to_come_back_denied_or_with_a_forged_state(provider: str) -> None:
    ask(provider, "POST", "/_control", body={"authorize": "access_denied"})
    query, _ = approved(provider)
    assert query["error"] == ["access_denied"] and query["state"] == ["the-state"]
    ask(provider, "POST", "/_control", body={"authorize": "server_error"})
    query, _ = approved(provider)
    assert query["error"] == ["server_error"] and "<script>" in query["error_description"][0]
    ask(provider, "POST", "/_control", body={"authorize": "wrong_state"})
    query, _ = approved(provider)
    assert query["state"] == ["forged-state"] and "code" in query
    query, _ = approved(provider)
    assert query["state"] == ["the-state"]


def test_the_key_set_and_the_discovery_document_can_be_taken_away_or_made_wrong(
    provider: str,
) -> None:
    ask(provider, "POST", "/_control", body={"jwks": "down"})
    assert ask(provider, "GET", "/jwks").status == 500
    ask(provider, "POST", "/_control", body={"jwks": "ok", "discovery": "wrong_issuer"})
    assert ask(provider, "GET", "/jwks").status == 200
    assert ask(provider, "GET", "/.well-known/openid-configuration").json["issuer"] != ISSUER
    ask(provider, "POST", "/_control", body={"discovery": "down"})
    assert ask(provider, "GET", "/.well-known/openid-configuration").status == 500
    assert ask(provider, "POST", "/_control", body={"reset": True}).json["discovery"] == "ok"


def test_people_can_be_added_and_each_claim_given_is_a_claim(
    provider: str, key: oidc.SigningKey
) -> None:
    made = ask(
        provider,
        "POST",
        "/_people",
        body={"key": "zed", "sub": "s-1", "email": "z@example.com", "email_verified": "yes"},
    )
    assert made.status == 201 and made.json["label"] == "zed"
    assert ask(provider, "POST", "/_people", body={"sub": "no key"}).status == 400
    query, verifier = approved(provider, "zed")
    claims = verified(
        exchange(provider, query["code"][0], verifier).json["id_token"], key, "the-nonce"
    )
    assert claims["email_verified"] == "yes" and "name" not in claims
    assert "zed" in [one["key"] for one in ask(provider, "GET", "/_people").json]


def test_the_log_says_what_was_asked_and_how_the_client_proved_itself(provider: str) -> None:
    query, verifier = approved(provider)
    exchange(provider, query["code"][0], verifier)
    log = ask(provider, "GET", "/_log").json
    authorize = next(one for one in log if one["path"] == "/authorize")
    assert authorize["query"]["code_challenge_method"] == "S256"
    assert authorize["query"]["state"] == "the-state" and authorize["person"] == "ava"
    assert authorize["redirect"].startswith(CALLBACK + "?code=")
    token = next(one for one in log if one["path"] == "/token")
    assert token["client_auth"] == "basic" and token["status"] == 200
    assert token["issued"]["id_token"].count(".") == 2 and token["issued"]["access_token"]
    assert token["form"]["code_verifier"] == verifier and "client_secret" not in token["form"]
    assert ask(provider, "GET", f"/_log?since={log[-1]['id']}").json == []
    assert ask(provider, "DELETE", "/_log").status == 200
    assert ask(provider, "GET", "/_log").json == []


def test_userinfo_is_there_and_is_logged_so_that_a_test_can_see_it_was_not_asked(
    provider: str,
) -> None:
    query, verifier = approved(provider)
    access = exchange(provider, query["code"][0], verifier).json["access_token"]
    info = ask(provider, "GET", "/userinfo", headers={"Authorization": f"Bearer {access}"})
    assert info.json["email"] == "ava.stone@example.com"
    assert ask(provider, "GET", "/userinfo").status == 401
    assert [one for one in ask(provider, "GET", "/_log").json if one["path"] == "/userinfo"]
