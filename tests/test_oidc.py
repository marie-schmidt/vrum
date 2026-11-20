"""OpenID Connect with no containers: the ID token is verified against tokens that were minted
locally (good ones, and every way a token can lie), the provider's description and keys are
checked and cached, and the code is swapped for a token the way the provider's description says."""

import asyncio
import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from tests import sso_fakes as fakes
from tests.sso_fakes import FakeWeb, claims, mint, reply
from vrum.config import OidcSettings
from vrum.oidc import (
    ALLOWED_ALGORITHMS,
    OidcClient,
    choose_key,
    parse_endpoints,
    parse_keys,
    person_from_claims,
    verified_claims,
)
from vrum.sso_http import Call, ProviderProblem

KEY = fakes.RSA_KEY.public_key()


def rejected(token: str, **kwargs: str) -> str:
    """The short reason a token is refused for, which holds nothing the token said."""
    wanted = {"issuer": fakes.ISSUER, "client_id": fakes.CLIENT_ID, "nonce": fakes.NONCE}
    with pytest.raises(ProviderProblem) as problem:
        verified_claims(token, KEY, **{**wanted, **kwargs})
    assert problem.value.code == "failed"
    return problem.value.reason


def accepted(token: str, **kwargs: str) -> dict[str, object]:
    wanted = {"issuer": fakes.ISSUER, "client_id": fakes.CLIENT_ID, "nonce": fakes.NONCE}
    return verified_claims(token, KEY, **{**wanted, **kwargs})


# -- the ID token ------------------------------------------------------------------------------


def test_a_good_token_is_accepted_and_says_who_the_person_is() -> None:
    found = accepted(mint())
    person = person_from_claims(found)
    assert (person.provider, person.subject) == ("oidc", "ada-subject-1")
    assert (person.email, person.email_confirmed) == ("ada@example.com", True)
    assert (person.name, person.shown) == ("Ada Lovelace", "ada@example.com")


def test_an_elliptic_curve_signature_is_accepted_too() -> None:
    token = mint(key=fakes.EC_KEY, algorithm="ES256", kid="ec-1")
    assert accepted_with(token, fakes.EC_KEY.public_key())["sub"] == "ada-subject-1"


def accepted_with(token: str, key: object) -> dict[str, object]:
    return verified_claims(
        token, key, issuer=fakes.ISSUER, client_id=fakes.CLIENT_ID, nonce=fakes.NONCE
    )


def test_only_the_three_safe_signature_algorithms_are_allowed() -> None:
    assert ALLOWED_ALGORITHMS == ("RS256", "ES256", "PS256")


def test_a_token_for_another_nonce_is_refused() -> None:
    assert rejected(mint(claims(nonce="someone else's"))) == "wrong_nonce"
    assert rejected(mint(claims(nonce=None))) == "wrong_nonce"
    assert rejected(mint(claims(nonce=12345))) == "wrong_nonce"


def test_a_token_for_another_audience_is_refused() -> None:
    assert rejected(mint(claims(aud="another-client"))) == "wrong_audience"
    assert rejected(mint(claims(aud=["a", "b"]))) == "wrong_audience"


def test_a_token_from_another_issuer_is_refused() -> None:
    assert rejected(mint(claims(iss="https://evil.example.com"))) == "wrong_issuer"
    assert rejected(mint(claims(iss=fakes.ISSUER + "/"))) == "wrong_issuer"


def test_an_expired_token_is_refused_but_a_minute_of_clock_drift_is_not() -> None:
    now = int(time.time())
    assert rejected(mint(claims(exp=now - 3600))) == "token_expired"
    assert accepted(mint(claims(exp=now - 30)))["sub"] == "ada-subject-1"
    assert rejected(mint(claims(exp=now - 120))) == "token_expired"


def test_a_token_that_is_not_valid_yet_is_refused() -> None:
    now = int(time.time())
    assert rejected(mint(claims(iat=now + 3600, exp=now + 7200))) == "token_not_yet_valid"
    assert rejected(mint(claims(nbf=now + 3600))) == "token_not_yet_valid"


@pytest.mark.parametrize("missing", ["exp", "iat", "iss", "aud", "sub"])
def test_a_token_without_a_required_claim_is_refused(missing: str) -> None:
    assert rejected(mint(claims(**{missing: None}))) == "claim_missing"


def test_a_token_with_the_algorithm_none_is_refused() -> None:
    assert rejected(fakes.unsigned()) == "algorithm_not_allowed"


def test_a_token_signed_with_the_providers_public_key_as_a_shared_secret_is_refused() -> None:
    assert rejected(fakes.hmac_signed_with_the_public_key()) == "algorithm_not_allowed"


def test_a_tampered_token_is_refused() -> None:
    header, body, signature = mint().split(".")
    forged = claims(sub="the-admin")
    forged_body = fakes.b64(json.dumps(forged).encode())
    assert rejected(f"{header}.{forged_body}.{signature}") == "bad_signature"
    flipped = signature[:-4] + ("AAAA" if signature[-4:] != "AAAA" else "BBBB")
    assert rejected(f"{header}.{body}.{flipped}") == "bad_signature"


def test_a_token_signed_by_another_key_is_refused() -> None:
    assert rejected(mint(key=fakes.OTHER_RSA_KEY)) == "bad_signature"


@pytest.mark.parametrize("garbage", ["", "not-a-token", "a.b.c", "....", "x" * 100])
def test_a_token_that_is_not_a_token_is_refused(garbage: str) -> None:
    assert rejected(garbage) in ("token_not_valid", "algorithm_not_allowed")


def test_several_audiences_need_the_authorized_party_to_be_vrum() -> None:
    both = ["other-service", fakes.CLIENT_ID]
    assert rejected(mint(claims(aud=both))) == "wrong_authorized_party"
    assert rejected(mint(claims(aud=both, azp="other-service"))) == "wrong_authorized_party"
    assert accepted(mint(claims(aud=both, azp=fakes.CLIENT_ID)))["sub"] == "ada-subject-1"
    # One audience and a stray azp for someone else is refused too.
    assert rejected(mint(claims(azp="other-service"))) == "wrong_authorized_party"
    assert accepted(mint(claims(azp=fakes.CLIENT_ID)))["sub"] == "ada-subject-1"


@pytest.mark.parametrize(
    ("said", "confirmed"),
    [
        (True, True),
        ("true", True),
        ("TRUE", True),
        (False, False),
        ("false", False),
        ("yes", False),
        (1, False),
        (None, False),
        ([True], False),
    ],
)
def test_an_address_counts_as_confirmed_only_when_the_provider_says_true(
    said: object, confirmed: bool
) -> None:
    person = person_from_claims(claims(email_verified=said))
    assert person.email_confirmed is confirmed


def test_there_is_no_confirmed_address_without_an_address() -> None:
    person = person_from_claims(claims(email=None, email_verified=True))
    assert (person.email, person.email_confirmed) == (None, False)
    assert person.shown == "ada"


@pytest.mark.parametrize("subject", ["", None, 12, "x" * 256, ["a"]])
def test_a_subject_that_cannot_identify_anyone_is_refused(subject: object) -> None:
    with pytest.raises(ProviderProblem):
        person_from_claims({**claims(), "sub": subject})


# -- the keys ----------------------------------------------------------------------------------


def test_a_key_is_chosen_by_its_id_and_never_for_the_wrong_use_or_algorithm() -> None:
    keys = parse_keys(
        {
            "keys": [
                fakes.rsa_jwk(fakes.RSA_KEY, "rsa-1", use="sig"),
                fakes.rsa_jwk(fakes.OTHER_RSA_KEY, "rsa-enc", use="enc"),
                fakes.rsa_jwk(fakes.OTHER_RSA_KEY, "rsa-2", alg="RS512"),
                fakes.ec_jwk(fakes.EC_KEY, "ec-1"),
                {"kty": "oct", "kid": "secret", "k": "AAAA"},
                {"kty": "RSA", "kid": "broken"},
                "not a key",
            ]
        }
    )
    assert [one.kid for one in keys] == ["rsa-1", "rsa-enc", "rsa-2", "ec-1"]
    assert choose_key(keys, "rsa-1", "RS256") is keys[0].key
    assert choose_key(keys, "rsa-1", "PS256") is keys[0].key
    assert choose_key(keys, "rsa-1", "ES256") is None
    assert choose_key(keys, "ec-1", "ES256") is keys[3].key
    assert choose_key(keys, "ec-1", "RS256") is None
    assert choose_key(keys, "rsa-enc", "RS256") is None
    assert choose_key(keys, "rsa-2", "RS256") is None
    assert choose_key(keys, "unknown", "RS256") is None


def test_a_token_without_a_key_id_needs_exactly_one_possible_key() -> None:
    one = parse_keys({"keys": [fakes.rsa_jwk(fakes.RSA_KEY, "a")]})
    two = parse_keys(
        {"keys": [fakes.rsa_jwk(fakes.RSA_KEY, "a"), fakes.rsa_jwk(fakes.OTHER_RSA_KEY, "b")]}
    )
    assert choose_key(one, None, "RS256") is one[0].key
    assert choose_key(two, None, "RS256") is None


# -- what the provider says about itself -------------------------------------------------------


def described(**changes: object) -> object:
    return parse_endpoints(fakes.discovery(**changes), fakes.ISSUER, allow_insecure_http=False)


def test_a_description_that_names_another_issuer_is_refused() -> None:
    for issuer in ("https://evil.example.com", fakes.ISSUER + "/", None):
        with pytest.raises(ProviderProblem) as problem:
            described(issuer=issuer)
        assert problem.value.reason == "issuer_mismatch"


def test_an_endpoint_that_is_not_https_is_refused_unless_http_is_allowed() -> None:
    with pytest.raises(ProviderProblem) as problem:
        described(token_endpoint="http://login.example.com/token")
    assert problem.value.reason == "endpoint_not_allowed"
    plain = fakes.discovery(token_endpoint="http://login.example.com/token")
    assert parse_endpoints(plain, fakes.ISSUER, allow_insecure_http=True).token.startswith("http:")
    for bad in ("javascript:alert(1)", "https://user:pw@login.example.com/token", None, 5):
        with pytest.raises(ProviderProblem):
            described(token_endpoint=bad)


def test_a_provider_that_cannot_do_what_vrum_needs_is_refused() -> None:
    with pytest.raises(ProviderProblem) as problem:
        described(id_token_signing_alg_values_supported=["HS256", "none"])
    assert problem.value.reason == "no_usable_algorithm"
    with pytest.raises(ProviderProblem) as problem:
        described(code_challenge_methods_supported=["plain"])
    assert problem.value.reason == "no_pkce"


# -- the whole exchange against a stand-in for the provider ------------------------------------


def client(
    web: FakeWeb, settings: OidcSettings | None = None, now: list[float] | None = None
) -> OidcClient:
    clock = now or [0.0]
    return OidcClient(
        settings or fakes.oidc_settings(),
        redirect_uri=fakes.REDIRECT,
        allow_insecure_http=False,
        fetch=web,
        clock=lambda: clock[0],
    )


def test_the_authorization_address_asks_for_the_code_flow_with_pkce_a_state_and_a_nonce() -> None:
    web = FakeWeb()
    web.provider()
    url = asyncio.run(client(web).authorization_url(state="S", nonce="N", challenge="C"))
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{fakes.ISSUER}/authorize"
    assert query == {
        "response_type": ["code"],
        "client_id": [fakes.CLIENT_ID],
        "redirect_uri": [fakes.REDIRECT],
        "scope": ["openid email profile"],
        "state": ["S"],
        "nonce": ["N"],
        "code_challenge": ["C"],
        "code_challenge_method": ["S256"],
    }


def test_the_code_is_swapped_for_a_token_with_the_verifier_and_the_secret_as_basic_auth() -> None:
    web = FakeWeb()
    web.provider(id_token=mint())
    person = asyncio.run(client(web).person("the-code", "the-verifier", fakes.NONCE))
    assert person.subject == "ada-subject-1"
    (call,) = web.asked(f"{fakes.ISSUER}/token")
    assert call.form == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "redirect_uri": fakes.REDIRECT,
        "code_verifier": "the-verifier",
    }
    sent = call.headers["Authorization"].removeprefix("Basic ")
    assert base64.b64decode(sent).decode() == f"{fakes.CLIENT_ID}:{fakes.CLIENT_SECRET}"


def test_a_provider_that_only_takes_the_secret_in_the_body_gets_it_there() -> None:
    web = FakeWeb()
    web.provider(id_token=mint())
    web.on(
        f"{fakes.ISSUER}/.well-known/openid-configuration",
        reply(fakes.discovery(token_endpoint_auth_methods_supported=["client_secret_post"])),
    )
    asyncio.run(client(web).person("c", "v", fakes.NONCE))
    (call,) = web.asked(f"{fakes.ISSUER}/token")
    assert call.form is not None
    assert call.form["client_secret"] == fakes.CLIENT_SECRET
    assert "Authorization" not in call.headers


def test_a_provider_that_wants_another_kind_of_client_login_is_refused() -> None:
    web = FakeWeb()
    web.provider(id_token=mint())
    web.on(
        f"{fakes.ISSUER}/.well-known/openid-configuration",
        reply(fakes.discovery(token_endpoint_auth_methods_supported=["private_key_jwt"])),
    )
    with pytest.raises(ProviderProblem) as problem:
        asyncio.run(client(web).person("c", "v", fakes.NONCE))
    assert problem.value.reason == "client_authentication_not_supported"


def test_without_a_client_secret_vrum_is_a_public_client_that_sends_only_its_id() -> None:
    web = FakeWeb()
    web.provider(id_token=mint())
    asyncio.run(client(web, fakes.oidc_settings(client_secret=None)).person("c", "v", fakes.NONCE))
    (call,) = web.asked(f"{fakes.ISSUER}/token")
    assert call.form is not None and call.form["client_id"] == fakes.CLIENT_ID
    assert "client_secret" not in call.form and "Authorization" not in call.headers


def test_a_token_endpoint_that_refuses_the_code_fails_and_one_that_is_gone_is_unavailable() -> None:
    web = FakeWeb()
    web.provider()
    web.on(f"{fakes.ISSUER}/token", reply({"error": "invalid_grant"}, 400))
    with pytest.raises(ProviderProblem) as refused:
        asyncio.run(client(web).person("c", "v", fakes.NONCE))
    assert (refused.value.code, refused.value.reason) == ("failed", "token_request_refused")
    web.on(f"{fakes.ISSUER}/token", fakes.unreachable())
    with pytest.raises(ProviderProblem) as gone:
        asyncio.run(client(web).person("c", "v", fakes.NONCE))
    assert gone.value.code == "unavailable"
    web.on(f"{fakes.ISSUER}/token", reply({"access_token": "only this"}))
    with pytest.raises(ProviderProblem) as no_token:
        asyncio.run(client(web).person("c", "v", fakes.NONCE))
    assert no_token.value.reason == "no_id_token"


def test_a_provider_that_cannot_be_asked_about_itself_is_unavailable() -> None:
    web = FakeWeb()
    web.on(f"{fakes.ISSUER}/.well-known/openid-configuration", fakes.unreachable())
    with pytest.raises(ProviderProblem) as problem:
        asyncio.run(client(web).authorization_url(state="s", nonce="n", challenge="c"))
    assert problem.value.code == "unavailable"
    web.on(f"{fakes.ISSUER}/.well-known/openid-configuration", reply({}, 500))
    with pytest.raises(ProviderProblem) as problem:
        asyncio.run(client(web).authorization_url(state="s", nonce="n", challenge="c"))
    assert problem.value.code == "unavailable"


def test_what_the_provider_says_about_itself_is_remembered_for_an_hour() -> None:
    web = FakeWeb()
    web.provider(id_token=mint())
    now = [0.0]
    one = client(web, now=now)
    asyncio.run(one.person("c", "v", fakes.NONCE))
    asyncio.run(one.person("c", "v", fakes.NONCE))
    description = f"{fakes.ISSUER}/.well-known/openid-configuration"
    assert len(web.asked(description)) == 1 and len(web.asked(f"{fakes.ISSUER}/keys")) == 1
    now[0] = 3601.0
    asyncio.run(one.person("c", "v", fakes.NONCE))
    assert len(web.asked(description)) == 2 and len(web.asked(f"{fakes.ISSUER}/keys")) == 2


def test_an_unknown_key_id_makes_vrum_look_again_but_only_once_a_minute() -> None:
    web = FakeWeb()
    web.provider(id_token=mint(kid="rotated"))
    now = [0.0]
    one = client(web, now=now)
    for _ in range(3):
        with pytest.raises(ProviderProblem) as problem:
            asyncio.run(one.person("c", "v", fakes.NONCE))
        assert problem.value.reason == "key_not_found"
    assert len(web.asked(f"{fakes.ISSUER}/keys")) == 1
    # The provider rotates its keys; a minute later the new key is found.
    web.provider(id_token=mint(kid="rotated"), keys=[fakes.rsa_jwk(fakes.RSA_KEY, "rotated")])
    now[0] = 61.0
    assert asyncio.run(one.person("c", "v", fakes.NONCE)).subject == "ada-subject-1"
    assert len(web.asked(f"{fakes.ISSUER}/keys")) == 2


def test_the_client_never_calls_anything_but_the_providers_own_addresses() -> None:
    web = FakeWeb()
    web.provider(id_token=mint())
    asyncio.run(client(web).person("c", "v", fakes.NONCE))
    asked = {call.url.split("?")[0] for call in web.calls}
    assert asked == {
        f"{fakes.ISSUER}/.well-known/openid-configuration",
        f"{fakes.ISSUER}/keys",
        f"{fakes.ISSUER}/token",
    }
    assert all(isinstance(call, Call) for call in web.calls)
