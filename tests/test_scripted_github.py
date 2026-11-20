"""The scripted GitHub (tests/scripted_github.py): that the answers have the shapes GitHub documents
(including the surprising ones: errors that arrive as HTTP 200, form encoding unless JSON is asked
for, a User-Agent that must be there, a missing scope that makes an endpoint "not found") and that
its people, controls and log work, over real HTTP on this machine. No containers."""

import base64
import hashlib
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from tests import scripted_github as github

CLIENT = "vrum-github-check"
SECRET = "scripted-github-secret"
CALLBACK = "http://localhost:21610/board/auth/github/callback"
SCOPE = "read:user user:email"


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
    sent = {"User-Agent": "unit-test", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode()
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(base + path, data=data, method=method, headers=sent)
    try:
        with OPENER.open(request, timeout=20) as response:
            return Answer(response.status, response.headers, response.read())
    except urllib.error.HTTPError as problem:
        return Answer(problem.code, problem.headers, problem.read())


@pytest.fixture
def stand_in() -> Iterator[str]:
    provider = github.Provider(
        {"PORT": "0", "CLIENT_ID": CLIENT, "CLIENT_SECRET": SECRET, "REDIRECT_URIS": CALLBACK}
    )
    server = github.serve(provider)
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def pkce() -> tuple[str, str]:
    verifier = "v" * 43
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def approve(base: str, person: str = "gwen", *, scope: str = SCOPE, **more: str) -> dict[str, str]:
    """Press a person's button and return what the browser is sent back with."""
    verifier, challenge = pkce()
    query = {
        "client_id": CLIENT,
        "redirect_uri": CALLBACK,
        "scope": scope,
        "state": "the-state",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "person": person,
        **more,
    }
    answer = ask(base, "GET", "/login/oauth/authorize?" + urllib.parse.urlencode(query))
    assert answer.status == 303, answer.raw
    back = urllib.parse.parse_qs(urllib.parse.urlsplit(answer.location).query)
    return {**{name: values[0] for name, values in back.items()}, "verifier": verifier}


def exchange(base: str, back: dict[str, str], *, accept: str | None = "application/json") -> Answer:
    form = {
        "client_id": CLIENT,
        "client_secret": SECRET,
        "code": back["code"],
        "redirect_uri": CALLBACK,
        "code_verifier": back["verifier"],
    }
    return ask(
        base, "POST", "/login/oauth/access_token", form=form, headers={"Accept": accept or ""}
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_of(base: str, person: str = "gwen", **more: str) -> str:
    return str(exchange(base, approve(base, person, **more)).json["access_token"])


def test_a_person_signs_in_and_the_token_shows_who_they_are_and_every_address_they_have(
    stand_in: str,
) -> None:
    back = approve(stand_in, "gwen")
    assert back["state"] == "the-state" and len(back["code"]) == 20
    answer = exchange(stand_in, back)
    assert answer.status == 200
    body = answer.json
    assert body["token_type"] == "bearer" and body["scope"] == "read:user,user:email"
    assert body["access_token"].startswith("gho_")
    user = ask(stand_in, "GET", "/api/v3/user", headers=bearer(body["access_token"]))
    assert user.status == 200 and user.headers["X-OAuth-Scopes"] == "read:user, user:email"
    assert user.json["id"] == 1001 and user.json["login"] == "gwen-archer"
    assert user.json["name"] == "Gwen Archer" and user.json["type"] == "User"
    for name in ("node_id", "avatar_url", "html_url", "url", "created_at", "site_admin"):
        assert name in user.json
    emails = ask(stand_in, "GET", "/api/v3/user/emails", headers=bearer(body["access_token"]))
    assert emails.status == 200
    assert [(one["email"], one["verified"], one["primary"]) for one in emails.json] == [
        ("gwen.archer@example.com", True, True),
        ("gwen@workshop.example.net", True, False),
        ("gwen.old@example.org", False, False),
    ]
    assert all(set(one) == {"email", "verified", "primary", "visibility"} for one in emails.json)


def test_the_people_are_the_ones_the_checks_and_the_demo_need(stand_in: str) -> None:
    people = {one["key"]: one for one in ask(stand_in, "GET", "/_people").json}
    primary = {
        key: [one["email"] for one in person["emails"] if one["primary"]]
        for key, person in people.items()
    }
    assert primary["ines"] == ["ines.park@example.com"]
    ines = next(one for one in people["ines"]["emails"] if one["primary"])
    assert ines["verified"] is False and any(
        one["verified"] and not one["primary"] for one in people["ines"]["emails"]
    )
    assert not any(one["verified"] for one in people["jonas"]["emails"])
    assert people["kira"]["emails"] == []
    # The public address of the profile is not proof of anything.
    assert people["mallory"]["email"] == "ava.stone@example.com"
    assert [one["verified"] for one in people["mallory"]["emails"]] == [True, False]
    assert primary["priya"] == ["priya@example.com"] and primary["tom"] == ["tom@example.com"]
    assert len({one["id"] for one in people.values()}) == len(people)


def test_without_json_asked_for_the_token_comes_back_form_encoded(stand_in: str) -> None:
    answer = exchange(stand_in, approve(stand_in), accept=None)
    assert answer.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
    fields = urllib.parse.parse_qs(answer.raw.decode())
    assert fields["token_type"] == ["bearer"] and fields["access_token"][0].startswith("gho_")


def test_a_wrong_or_used_code_is_an_error_in_an_http_200_as_github_does_it(
    stand_in: str,
) -> None:
    back = approve(stand_in)
    first = exchange(stand_in, back)
    again = exchange(stand_in, back)
    assert first.status == 200 and "access_token" in first.json
    assert again.status == 200 and again.json["error"] == "bad_verification_code"
    assert "error_description" in again.json and "access_token" not in again.json
    back = approve(stand_in)
    back["verifier"] = "w" * 43
    assert exchange(stand_in, back).json["error"] == "bad_verification_code"
    back = approve(stand_in)
    wrong = ask(
        stand_in,
        "POST",
        "/login/oauth/access_token",
        form={
            "client_id": CLIENT,
            "client_secret": "nope",
            "code": back["code"],
            "code_verifier": back["verifier"],
        },
        headers={"Accept": "application/json"},
    )
    assert wrong.status == 200 and wrong.json["error"] == "incorrect_client_credentials"


def test_a_callback_address_that_is_not_registered_is_never_redirected_to(stand_in: str) -> None:
    path = "/login/oauth/authorize?" + urllib.parse.urlencode(
        {"client_id": CLIENT, "redirect_uri": "https://evil.example.com/x", "person": "gwen"}
    )
    answer = ask(stand_in, "GET", path)
    assert answer.status == 404 and "Location" not in answer.headers


def test_the_page_lists_every_person_and_names_the_scope_asked_for(stand_in: str) -> None:
    path = "/login/oauth/authorize?" + urllib.parse.urlencode(
        {"client_id": CLIENT, "redirect_uri": CALLBACK, "scope": SCOPE, "state": "s"}
    )
    page = ask(stand_in, "GET", path).raw.decode()
    for person in github.DEFAULT_PEOPLE:
        assert f'data-person="{person["key"]}"' in page
    assert "read:user user:email" in page and 'data-person="cancel"' in page


def test_the_api_wants_a_user_agent_a_good_token_and_the_email_scope(stand_in: str) -> None:
    token = token_of(stand_in)
    no_agent = ask(stand_in, "GET", "/api/v3/user", headers={**bearer(token), "User-Agent": ""})
    assert no_agent.status == 403 and "User-Agent" in no_agent.json["message"]
    assert ask(stand_in, "GET", "/api/v3/user").json["message"] == "Requires authentication"
    bad = ask(stand_in, "GET", "/api/v3/user", headers=bearer("gho_nope"))
    assert bad.status == 401 and bad.json["message"] == "Bad credentials"
    old_style = ask(stand_in, "GET", "/api/v3/user", headers={"Authorization": f"token {token}"})
    assert old_style.status == 200
    narrow = token_of(stand_in, scope="read:user")
    assert ask(stand_in, "GET", "/api/v3/user", headers=bearer(narrow)).status == 200
    emails = ask(stand_in, "GET", "/api/v3/user/emails", headers=bearer(narrow))
    assert emails.status == 404 and emails.json["message"] == "Not Found"


def test_a_person_with_no_confirmed_address_and_one_with_none_are_what_they_say(
    stand_in: str,
) -> None:
    for person, expected in (("jonas", [False]), ("kira", []), ("ines", [False, True])):
        token = token_of(stand_in, person)
        emails = ask(stand_in, "GET", "/api/v3/user/emails", headers=bearer(token)).json
        assert [one["verified"] for one in emails] == expected
    mallory = token_of(stand_in, "mallory")
    profile = ask(stand_in, "GET", "/api/v3/user", headers=bearer(mallory)).json
    assert profile["email"] == "ava.stone@example.com"


def test_a_renamed_login_keeps_the_numeric_id(stand_in: str) -> None:
    before = token_of(stand_in, "hugo")
    ask(
        stand_in,
        "POST",
        "/_people",
        body={"key": "hugo", "id": 1002, "login": "hugo-renamed", "emails": []},
    )
    user = ask(stand_in, "GET", "/api/v3/user", headers=bearer(token_of(stand_in, "hugo"))).json
    assert user["id"] == 1002 and user["login"] == "hugo-renamed" and user["name"] is None
    assert ask(stand_in, "GET", "/api/v3/user", headers=bearer(before)).status == 200
    assert ask(stand_in, "POST", "/_people", body={"key": "x"}).status == 400


def test_an_approval_can_come_back_denied_with_markup_or_with_a_forged_state(
    stand_in: str,
) -> None:
    ask(stand_in, "POST", "/_control", body={"authorize": "access_denied"})
    back = approve(stand_in)
    assert back["error"] == "access_denied" and back["state"] == "the-state"
    ask(stand_in, "POST", "/_control", body={"authorize": "server_error"})
    assert "<script>" in approve(stand_in)["error_description"]
    ask(stand_in, "POST", "/_control", body={"authorize": "wrong_state"})
    assert approve(stand_in)["state"] == "forged-state"
    assert approve(stand_in)["state"] == "the-state"
    path = "/login/oauth/authorize?" + urllib.parse.urlencode(
        {"client_id": CLIENT, "redirect_uri": CALLBACK, "state": "s", "cancel": "1"}
    )
    cancelled = urllib.parse.parse_qs(
        urllib.parse.urlsplit(ask(stand_in, "GET", path).location).query
    )
    assert cancelled["error"] == ["access_denied"]


def test_the_token_endpoint_and_the_api_can_be_made_to_fail(stand_in: str) -> None:
    ask(stand_in, "POST", "/_control", body={"token": "error"})
    assert exchange(stand_in, approve(stand_in)).json["error"] == "bad_verification_code"
    ask(stand_in, "POST", "/_control", body={"token": "endpoint_500"})
    assert exchange(stand_in, approve(stand_in)).status == 500
    ask(stand_in, "POST", "/_control", body={"token": "no_access_token"})
    assert "access_token" not in exchange(stand_in, approve(stand_in)).json
    ask(stand_in, "POST", "/_control", body={"token": "endpoint_redirect"})
    redirected = exchange(stand_in, approve(stand_in))
    assert redirected.status == 302 and redirected.location == "/elsewhere"
    ask(stand_in, "POST", "/_control", body={"token": "endpoint_huge"})
    assert len(exchange(stand_in, approve(stand_in)).raw) > 2 * 1024 * 1024
    token = token_of(stand_in)
    ask(stand_in, "POST", "/_control", body={"user": "down", "emails": "unauthorized"})
    assert ask(stand_in, "GET", "/api/v3/user", headers=bearer(token)).status == 500
    assert ask(stand_in, "GET", "/api/v3/user/emails", headers=bearer(token)).status == 401
    ask(stand_in, "POST", "/_control", body={"user": "ok", "emails": "not_found"})
    assert ask(stand_in, "GET", "/api/v3/user/emails", headers=bearer(token)).status == 404
    ask(stand_in, "POST", "/_control", body={"rate_limited": True})
    limited = ask(stand_in, "GET", "/api/v3/user", headers=bearer(token))
    assert limited.status == 403 and limited.headers["X-RateLimit-Remaining"] == "0"
    state = ask(stand_in, "POST", "/_control", body={"reset": True}).json
    assert state["emails"] == "ok" and state["rate_limited"] is False
    assert ask(stand_in, "POST", "/_control", body={"token": "nonsense"}).status == 400


def test_the_log_says_what_was_asked_and_never_keeps_the_secret(stand_in: str) -> None:
    token = token_of(stand_in)
    ask(stand_in, "GET", "/api/v3/user", headers={**bearer(token), "User-Agent": "Vrum/test"})
    log = ask(stand_in, "GET", "/_log").json
    authorize = next(one for one in log if one["path"].endswith("/authorize"))
    assert authorize["query"]["scope"] == SCOPE and authorize["query"]["state"] == "the-state"
    assert authorize["query"]["code_challenge_method"] == "S256"
    exchanged = next(one for one in log if one["path"].endswith("/access_token"))
    assert exchanged["accept"] == "application/json" and "client_secret" not in exchanged["form"]
    assert exchanged["issued"]["access_token"] == token
    assert authorize["redirect"].startswith(CALLBACK + "?code=")
    api = next(one for one in log if one["path"] == "/api/v3/user")
    assert api["user_agent"] == "Vrum/test" and api["authorization"] == "bearer"
    assert ask(stand_in, "GET", f"/_log?since={log[-1]['id'] + 1}").json == []
    assert ask(stand_in, "DELETE", "/_log").status == 200
    assert ask(stand_in, "GET", "/_log").json == []
