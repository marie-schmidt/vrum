"""Accounts, sessions, projects and agent keys, through the board's own HTTP API and a real MCP
client, against the real containers (M15). Nothing is faked: people are registered, sign in, make
projects and keys exactly as the board's page does.

The things that have to hold: an account cannot be overwritten, passwords and sessions and keys are
kept only as hashes, a session ends on the server when it is signed out or expires, a guesser is
slowed down, another website cannot make a signed-in browser change anything, a person sees only
their own projects, and a key reaches one project and stops working the moment it is revoked."""

import json
import re
from typing import Any

import pytest
from mcp import Client

from tests.helpers import compose, connect, ok, run_sql, unique_word
from tests.world import Person, Reply, api, make_project, register, server_base

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

PASSWORD = "a long enough password"


def set_cookie(reply: Reply) -> str:
    lines = [
        line
        for line in reply.headers.get_all("Set-Cookie") or []
        if line.startswith("vrum_session=")
    ]
    assert len(lines) == 1, lines
    return str(lines[0])


def said(reply: Reply) -> str:
    return str(reply.body["message"])


def messages(error: BaseException) -> str:
    """Everything an exception, or the exceptions grouped inside it, says."""
    inner = getattr(error, "exceptions", None)
    parts = [str(error)] + [messages(one) for one in inner or []]
    return " ".join(parts)


def sign_in(base: str, email: str, password: str, **more: Any) -> Reply:
    return api(base, "POST", "/board/api/session", {"email": email, "password": password}, **more)


def hashed(secret: str) -> str:
    """How the database holds a session or a key: a SHA-256 of it."""
    return f"encode(sha256('{secret}'::bytea), 'hex')"


def held(sql: str) -> str:
    result = run_sql(sql, plain=True, project=None)
    assert result.returncode == 0, result.stderr
    return str(result.stdout.strip())


# -- registering and signing in ----------------------------------------------------------------


def test_registering_signs_the_person_in_with_a_cookie_the_page_cannot_read() -> None:
    base = server_base()
    word = unique_word()

    reply = api(
        base,
        "POST",
        "/board/api/accounts",
        {"email": f"Ada.{word}@Example.TEST", "name": "Ada", "password": PASSWORD},
    )

    assert reply.status == 201
    assert reply.body["account"]["email"] == f"ada.{word}@example.test"
    cookie = set_cookie(reply)
    for attribute in ("HttpOnly", "SameSite=strict", "Path=/board", "Max-Age=43200"):
        assert attribute in cookie, cookie
    # Plain http on this machine, so no Secure flag; the value is never in the body.
    assert "Secure" not in cookie and reply.cookie and reply.cookie not in json.dumps(reply.body)
    assert PASSWORD not in json.dumps(reply.body)
    who = api(base, "GET", "/board/api/session", cookie=reply.cookie)
    assert who.status == 200 and who.body["account"]["name"] == "Ada"
    assert who.body["csrf"] == reply.body["csrf"]
    nobody = api(base, "GET", "/board/api/session")
    # Signed out, the answer says nobody is here and what the sign-in page may offer: whether mail
    # is set up and which providers there are (their names, never a setting or a secret).
    assert nobody.status == 200 and nobody.body["account"] is None and nobody.body["csrf"] is None
    assert set(nobody.body) == {"account", "csrf", "mail", "providers", "sso_create_accounts"}
    assert all(set(provider) == {"id", "name", "kind"} for provider in nobody.body["providers"])


def test_a_second_registration_with_the_same_address_is_refused_and_changes_nothing() -> None:
    person = register()
    other_password = "another long password"

    for again in (person.email, person.email.upper(), f"  {person.email}  "):
        reply = api(
            person.base,
            "POST",
            "/board/api/accounts",
            {"email": again, "name": "Someone else", "password": other_password},
        )
        assert reply.status == 409 and "already exists" in said(reply)
        assert reply.cookie is None and other_password not in json.dumps(reply.body)

    # The account is as it was: its own password signs in, the other does not, the name is kept.
    signed = sign_in(person.base, person.email, person.password)
    assert signed.status == 200 and signed.body["account"]["name"] == person.name
    assert sign_in(person.base, person.email, other_password).status == 401
    assert held(f"SELECT count(*) FROM accounts WHERE email = '{person.email}'") == "1"


@pytest.mark.parametrize(
    ("fields", "words"),
    [
        (
            {"email": "not-an-email", "name": "A", "password": PASSWORD},
            "does not look like an email",
        ),
        ({"email": "no@dot", "name": "A", "password": PASSWORD}, "does not look like an email"),
        ({"email": "@", "name": "A", "password": PASSWORD}, "does not look like an email"),
        ({"name": "  ", "password": PASSWORD}, "Say what to call you"),
        ({"name": "A", "password": "short"}, "10 to 200 characters"),
        ({"name": "A", "password": "x" * 201}, "10 to 200 characters"),
        ({"name": "A", "password": ""}, "10 to 200 characters"),
    ],
)
def test_a_registration_that_cannot_be_accepted_says_what_is_wrong_without_repeating_the_password(
    fields: dict[str, str], words: str
) -> None:
    base = server_base()
    sent = {"email": f"{unique_word()}@example.test", **fields}

    reply = api(base, "POST", "/board/api/accounts", sent)

    assert reply.status == 400 and words in said(reply)
    assert reply.cookie is None
    if sent["password"]:
        assert sent["password"] not in json.dumps(reply.body)
    assert held(f"SELECT count(*) FROM accounts WHERE email = '{sent['email']}'") == "0"


def test_a_registration_that_is_not_json_is_refused_in_plain_words() -> None:
    base = server_base()
    for body in (None, ["a", "list"], "text"):
        reply = api(base, "POST", "/board/api/accounts", body)
        assert reply.status == 400 and "JSON object" in said(reply)


def test_a_wrong_password_and_an_address_with_no_account_are_refused_in_the_same_words() -> None:
    person = register()

    wrong = sign_in(person.base, person.email, "not the password")
    unknown = sign_in(person.base, f"nobody-{unique_word()}@example.test", "not the password")

    assert wrong.status == unknown.status == 401
    assert wrong.body == unknown.body and "not right" in said(wrong)
    assert "not the password" not in json.dumps(wrong.body) and wrong.cookie is None
    # An address typed in capitals, or with spaces round it, is the same address.
    assert sign_in(person.base, f" {person.email.upper()} ", person.password).status == 200


def test_signing_out_ends_the_session_on_the_server_and_replaying_it_reads_and_writes_nothing() -> (
    None
):
    person = register()
    project = make_project(person, "Before sign-out")
    old_cookie, old_csrf = person.cookie, person.csrf
    assert person.call("GET", "/board/api/projects").status == 200

    out = person.call("DELETE", "/board/api/session")

    assert out.status == 200 and out.body == {"signed_out": True}
    assert "Max-Age=0" in set_cookie(out)
    replay = api(person.base, "GET", "/board/api/projects", cookie=old_cookie)
    assert replay.status == 401 and "not signed in" in said(replay)
    mine = f"/board/api/projects/{project.id}"
    for method, path, body in (
        ("POST", "/board/api/projects", {"name": "Sneaky"}),
        ("POST", f"{mine}/tool/create_node", {"actor": "x", "kind": "epic", "title": "Sneaky"}),
        ("POST", f"{mine}/keys", {"name": "sneaky-agent"}),
        ("DELETE", "/board/api/session", None),
    ):
        again = api(person.base, method, path, body, cookie=old_cookie, csrf=old_csrf)
        assert again.status == 401, (path, again.body)
    # Nothing was written, as a fresh sign-in shows.
    fresh = person.sign_in_again()
    names = [one["name"] for one in fresh.call("GET", "/board/api/projects").body["projects"]]
    assert names == ["Before sign-out"]
    assert fresh.call("POST", f"{mine}/tool/list_nodes", {}).body["items"] == []
    assert len(fresh.call("GET", f"{mine}/keys").body["keys"]) == 1


def test_an_expired_session_is_refused_like_a_signed_out_one() -> None:
    person = register()
    assert person.call("GET", "/board/api/projects").status == 200

    expired = run_sql(
        "UPDATE sessions SET created_at = now() - interval '13 hours', "
        f"expires_at = now() - interval '1 hour' WHERE token_hash = {hashed(person.cookie)}",
        project=None,
    )
    assert expired.returncode == 0, expired.stderr

    for method, path in (("GET", "/board/api/projects"), ("POST", "/board/api/projects")):
        reply = person.call(method, path, {"name": "Late"})
        assert reply.status == 401 and "session has ended" in said(reply)
    assert (
        api(person.base, "GET", "/board/api/session", cookie=person.cookie).body["account"] is None
    )
    # Signing in again makes a new one.
    assert person.sign_in_again().call("GET", "/board/api/projects").status == 200


def test_repeated_failed_sign_ins_are_rate_limited_for_that_address_only() -> None:
    person, other = register(), register()

    for _ in range(5):
        assert sign_in(person.base, person.email, "wrong guess").status == 401
    blocked = sign_in(person.base, person.email, "wrong guess")
    right_but_blocked = sign_in(person.base, person.email, person.password)

    for reply in (blocked, right_but_blocked):
        assert reply.status == 429 and "too many failed attempts" in said(reply)
        assert re.search(r"Wait \d+ seconds", said(reply)) and reply.cookie is None
    # Someone else is not held up by it, and a person signed in already keeps their session.
    assert other.sign_in_again().call("GET", "/board/api/projects").status == 200
    assert person.call("GET", "/board/api/projects").status == 200


def test_a_good_sign_in_forgives_the_failures_before_it() -> None:
    person = register()
    for _ in range(4):
        assert sign_in(person.base, person.email, "wrong guess").status == 401
    assert sign_in(person.base, person.email, person.password).status == 200
    for _ in range(4):
        assert sign_in(person.base, person.email, "wrong guess").status == 401
    assert sign_in(person.base, person.email, person.password).status == 200


def test_changes_from_another_site_are_refused_and_change_nothing() -> None:
    person = register()
    mine = "/board/api/projects"
    body = {"name": "From another site"}

    attempts = {
        "no session secret": api(person.base, "POST", mine, body, cookie=person.cookie),
        "a wrong session secret": api(
            person.base, "POST", mine, body, cookie=person.cookie, csrf="a-wrong-secret"
        ),
        "another site's origin": api(
            person.base,
            "POST",
            mine,
            body,
            cookie=person.cookie,
            csrf=person.csrf,
            origin="https://evil.example",
        ),
        "a browser that says it is cross-site": api(
            person.base,
            "POST",
            mine,
            body,
            cookie=person.cookie,
            csrf=person.csrf,
            extra={"Sec-Fetch-Site": "cross-site"},
        ),
    }
    for what, reply in attempts.items():
        assert reply.status == 403, (what, reply.body)
    assert "session secret" in said(attempts["no session secret"])
    assert "own page" in said(attempts["another site's origin"])
    assert person.call("GET", mine).body == {"projects": []}

    # Signing in or registering from another site is refused too, and so is a plain form post.
    word = unique_word()
    evil = "https://evil.example"
    for path, form in (
        ("/board/api/session", {"email": person.email, "password": person.password}),
        (
            "/board/api/accounts",
            {"email": f"{word}@example.test", "name": "X", "password": PASSWORD},
        ),
    ):
        refused = api(person.base, "POST", path, form, origin=evil)
        assert refused.status == 403 and refused.cookie is None
        as_form = api(
            person.base,
            "POST",
            path,
            form,
            extra={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert as_form.status == 415 and "JSON" in said(as_form)
    assert held(f"SELECT count(*) FROM accounts WHERE email = '{word}@example.test'") == "0"
    # The board's own page, with the secret and its own origin, is let in.
    assert person.call("POST", mine, body).status == 201


def test_passwords_sessions_and_keys_are_stored_only_as_hashes() -> None:
    person = register()
    project = make_project(person)

    stored = held(f"SELECT password_hash FROM accounts WHERE email = '{person.email}'")
    assert stored.startswith("scrypt$32768$8$1$") and person.password not in stored
    assert held(f"SELECT count(*) FROM sessions WHERE token_hash = '{person.cookie}'") == "0"
    assert held(f"SELECT count(*) FROM sessions WHERE token_hash = {hashed(person.cookie)}") == "1"
    key = project.key
    assert (
        held(f"SELECT count(*) FROM agent_keys WHERE key_hash = '{key}' OR key_hint = '{key}'")
        == "0"
    )
    assert held(f"SELECT count(*) FROM agent_keys WHERE key_hash = {hashed(key)}") == "1"
    assert held(f"SELECT key_hint FROM agent_keys WHERE key_hash = {hashed(key)}") == key[:8]
    # Not anywhere in the whole database: a dump of it holds none of the three.
    dump = compose("exec", "-T", "db", "pg_dump", "-U", "vrum", "-d", "vrum")
    assert dump.returncode == 0, dump.stderr
    for secret in (person.password, person.cookie, key):
        assert secret not in dump.stdout


# -- projects ------------------------------------------------------------------------------------


def test_a_new_account_has_no_projects_and_each_account_sees_only_its_own() -> None:
    a, b = register(), register()
    assert a.call("GET", "/board/api/projects").body == {"projects": []}

    make_project(a, "Alpha")
    make_project(a, "Beta")
    make_project(b, "Gamma")

    mine = [one["name"] for one in a.call("GET", "/board/api/projects").body["projects"]]
    theirs = [one["name"] for one in b.call("GET", "/board/api/projects").body["projects"]]
    assert mine == ["Alpha", "Beta"] and theirs == ["Gamma"]
    assert api(a.base, "GET", "/board/api/projects").status == 401


def test_a_project_name_is_unique_for_its_owner_and_a_blank_one_is_refused() -> None:
    a, b = register(), register()
    assert a.call("POST", "/board/api/projects", {"name": "Alpha"}).status == 201

    again = a.call("POST", "/board/api/projects", {"name": " alpha "})
    assert again.status == 409 and "already have a project called" in said(again)
    assert b.call("POST", "/board/api/projects", {"name": "Alpha"}).status == 201
    for name in ("", "   ", "x" * 101):
        refused = a.call("POST", "/board/api/projects", {"name": name})
        assert refused.status == 400 and "Give the project a name" in said(refused)
    assert [one["name"] for one in a.call("GET", "/board/api/projects").body["projects"]] == [
        "Alpha"
    ]


def test_another_persons_project_is_refused_in_the_same_words_as_one_that_does_not_exist() -> None:
    a, b = register(), register()
    project = make_project(a, "Private")
    absent = project.id + 100_000
    first = f"/board/api/projects/{project.id}"

    for tail in ("", "/keys", "/index"):
        owned = a.call("GET", f"{first}{tail}")
        theirs = b.call("GET", f"{first}{tail}")
        nothing = b.call("GET", f"/board/api/projects/{absent}{tail}")
        assert owned.status == 200
        assert theirs.status == nothing.status == 404 and theirs.body == nothing.body
        assert "no project with that address among yours" in said(theirs)
    not_a_number = b.call("GET", "/board/api/projects/abc")
    assert not_a_number.status == 404 and not_a_number.body == theirs.body

    for method, path, body in (
        ("POST", f"{first}/tool/create_node", {"actor": "b", "kind": "epic", "title": "Theirs"}),
        ("POST", f"{first}/tool/list_nodes", {}),
        ("POST", f"{first}/try/list_nodes", {}),
        ("POST", f"{first}/keys", {"name": "stolen-agent"}),
        ("DELETE", f"{first}/keys/1", None),
    ):
        refused = b.call(method, path, body)
        assert refused.status == 404 and refused.body == theirs.body, (path, refused.body)
    # Nothing of it was done.
    assert a.call("POST", f"{first}/tool/list_nodes", {}).body["items"] == []
    assert len(a.call("GET", f"{first}/keys").body["keys"]) == 1
    # A person who is not signed in is told that, not that the project is somebody else's.
    assert api(a.base, "GET", first).status == 401


# -- agent keys ----------------------------------------------------------------------------------


async def test_a_key_is_shown_once_and_revoking_it_stops_a_connection_that_is_already_open() -> (
    None
):
    owner = register()
    project = make_project(owner, "Keyed")
    path = f"/board/api/projects/{project.id}"
    second = owner.call("POST", f"{path}/keys", {"name": "second-agent"})
    assert second.status == 201
    secret, key_id = second.body["secret"], second.body["key"]["id"]
    assert secret.startswith("vrk_") and secret not in json.dumps(second.body["key"])

    # Listed later, a key shows its name and its first characters, never itself.
    listed = owner.call("GET", f"{path}/keys")
    assert secret not in json.dumps(listed.body)
    row = next(one for one in listed.body["keys"] if one["id"] == key_id)
    assert row["name"] == "second-agent" and row["hint"] == secret[:8]
    assert row["revoked_at"] is None and row["last_used_at"] is None

    async with connect(project.mcp_url, secret) as client:
        made = await ok(client, "create_node", actor="agent", kind="epic", title="Made with a key")
        assert (await ok(client, "list_nodes"))["items"][0]["ref"] == made["ref"]
        used = owner.call("GET", f"{path}/keys").body["keys"]
        assert next(one for one in used if one["id"] == key_id)["last_used_at"] is not None

        revoked = owner.call("DELETE", f"{path}/keys/{key_id}")
        assert revoked.status == 200 and revoked.body["key"]["revoked_at"] is not None

        # The connection is open and was initialised with the key: its next request is refused.
        with pytest.raises(BaseException) as already_open:
            await client.call_tool("list_nodes", {})
        assert "agent key is not valid" in messages(already_open.value)
    # A new client with it is refused as it connects, and the first key still works.
    with pytest.raises(BaseException) as new_client:
        async with connect(project.mcp_url, secret) as client:
            await client.call_tool("list_nodes", {})
    assert "agent key is not valid" in messages(new_client.value)
    async with connect(project.mcp_url, project.key) as client:
        assert len((await ok(client, "list_nodes"))["items"]) == 1
    # Revoking again is harmless, and the list says it is revoked.
    assert owner.call("DELETE", f"{path}/keys/{key_id}").status == 200
    ended = owner.call("GET", f"{path}/keys").body["keys"]
    assert next(one for one in ended if one["id"] == key_id)["revoked_at"] is not None


async def test_another_account_can_neither_list_make_nor_revoke_a_projects_keys() -> None:
    a, b = register(), register()
    project = make_project(a, "Mine")
    mine = f"/board/api/projects/{project.id}"
    key_id = a.call("GET", f"{mine}/keys").body["keys"][0]["id"]

    for method, tail, body in (
        ("GET", "/keys", None),
        ("POST", "/keys", {"name": "stolen-agent"}),
        ("DELETE", f"/keys/{key_id}", None),
    ):
        assert b.call(method, f"{mine}{tail}", body).status == 404
    async with connect(project.mcp_url, project.key) as client:
        assert (await ok(client, "server_status"))["vrum_version"]
    assert len(a.call("GET", f"{mine}/keys").body["keys"]) == 1
    # Even the owner cannot revoke a key by guessing its number in one of their other projects.
    other = make_project(a, "Other")
    refused = a.call("DELETE", f"/board/api/projects/{other.id}/keys/{key_id}")
    assert refused.status == 404 and "no such key" in said(refused)
    async with connect(project.mcp_url, project.key) as client:
        assert (await ok(client, "server_status"))["vrum_version"]


async def test_a_key_name_is_required() -> None:
    owner = register()
    project = make_project(owner)
    for name in ("", "  ", "x" * 101):
        reply = owner.call("POST", f"/board/api/projects/{project.id}/keys", {"name": name})
        assert reply.status == 400 and "Give the key the name of the agent" in said(reply)
    assert len(owner.call("GET", f"/board/api/projects/{project.id}/keys").body["keys"]) == 1


# -- secrets stay out of everything ----------------------------------------------------------------


async def test_a_key_a_cookie_or_a_password_is_in_no_log_no_event_and_cannot_be_stored() -> None:
    person = register()
    project = make_project(person, "Careful")
    base = person.base
    wrong = f"a-wrong-password-{unique_word()}"
    sign_in(base, person.email, wrong)
    api(
        base, "POST", "/board/api/accounts", {"email": person.email, "name": "x", "password": wrong}
    )
    async with connect(project.mcp_url, project.key) as client:
        await ok(client, "create_node", actor="agent", kind="epic", title="Quiet")
        # Writing a key or a session value into the graph is refused as a secret, naming only its
        # kind, and nothing is stored.
        for secret, kind in ((project.key, "Vrum agent key"), (person.cookie, "Vrum session")):
            for tool, fields in (
                ("create_node", {"kind": "epic", "title": f"leaked {secret}"}),
                ("retain_fact", {"text": f"it is {secret}", "source": "somewhere"}),
                (
                    "record_decision",
                    {
                        "title": "d",
                        "text": "t",
                        "reason": f"because {secret}",
                        "decided_by": "Marie",
                    },
                ),
            ):
                refusal = await client.call_tool(tool, {"actor": "agent", **fields})
                assert refusal.is_error and kind in str(refusal.content)
                assert secret not in str(refusal.content)
        assert [one["title"] for one in (await ok(client, "list_nodes"))["items"]] == ["Quiet"]
        events = await ok(client, "changes_since", after_event_id=0, limit=200)
    everything = json.dumps(events)
    for secret in (project.key, person.cookie, person.password, wrong):
        assert secret not in everything
    logs = compose("logs", "app", "--no-color")
    assert logs.returncode == 0, logs.stderr
    for secret in (project.key, person.cookie, person.csrf, person.password, wrong):
        assert secret not in logs.stdout + logs.stderr
    # The log does say that a sign-in was refused, and which project a tool call was for.
    assert '"event": "tool_call"' in logs.stdout or '"event":"tool_call"' in logs.stdout


def test_what_an_account_and_its_session_are_never_leave_with_a_projects_export() -> None:
    person = register()
    project = make_project(person, "Exported")
    path = f"/board/api/projects/{project.id}/tool"
    person.call("POST", f"{path}/create_node", {"actor": "x", "kind": "epic", "title": "One"})
    pages: list[Any] = []
    after: str | None = None
    while True:
        page = person.call("POST", f"{path}/export_graph", {"after": after, "limit": 200}).body
        pages.append(page)
        after = page["next_after"]
        if after is None:
            break
    text = json.dumps(pages)
    for secret in (person.password, person.cookie, person.csrf, project.key, person.email):
        assert secret not in text
    assert "accounts" not in {key for page in pages for key in page["items"][0]} if pages else True
    assert pages[0]["counts"]["nodes"] == 1


async def test_a_second_person_registered_in_one_client_is_a_different_account() -> None:
    # A smoke test of the helpers themselves: two people never share a session or a project.
    a, b = register(), register()
    assert isinstance(a, Person) and a.cookie != b.cookie and a.email != b.email
    pa, pb = make_project(a, "Same name"), make_project(b, "Same name")
    assert pa.id != pb.id and pa.key != pb.key
    async with connect(pa.mcp_url, pa.key) as one, connect(pb.mcp_url, pb.key) as two:
        await ok(one, "create_node", actor="a", kind="epic", title="Only in a")
        assert (await ok(two, "list_nodes"))["items"] == []
        assert isinstance(one, Client) and isinstance(two, Client)
