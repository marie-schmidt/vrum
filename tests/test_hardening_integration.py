"""M6 against the running containers: the agent key, the size limit, secrets in what is written,
plain refusals, paging everywhere, and the logs (see ./check.sh).

Raw HTTP is used where what matters is what the server answers a caller that is not an MCP client
(no key, a wrong one, a body that is too big), and a real MCP client where what matters is what a
client shows its user."""

import http.client
import json
import os
import random
import string
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

import anyio
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from tests.helpers import (
    compose,
    confirm,
    connect,
    make_decision,
    make_fact,
    ok,
    refused,
    run_sql,
    the_token,
    unique_word,
)
from tests.world import api, make_project, register, the_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MAX_REQUEST_BYTES = 4 * 1024 * 1024


def a_secret() -> str:
    """A GitHub-token-shaped string that nothing else in the database or the logs can contain."""
    return "ghp_" + "".join(random.choices(string.ascii_letters + string.digits, k=36))


def different_words(characters: int) -> str:
    """About this many characters of words that are all different, which is the worst case for
    how much PostgreSQL has to keep to search a text."""
    words: set[str] = set()
    size = 0
    while size < characters:
        word = "".join(random.choices(string.ascii_lowercase, k=8))
        if word not in words:
            words.add(word)
            size += 9
    return " ".join(words)


def request(
    url: str,
    *,
    method: str = "POST",
    key: str | None = None,
    authorization: str | None = None,
    body: bytes | None = b"{}",
) -> tuple[int, dict[str, str], bytes]:
    """One HTTP request, answered whatever the status."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    if authorization is not None:
        headers["Authorization"] = authorization
    call = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def open_connection(url: str) -> http.client.HTTPConnection:
    parts = urlsplit(url)
    assert parts.hostname is not None
    return http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)


def message_of(body: bytes) -> str:
    """The plain message in a JSON-RPC error."""
    return str(json.loads(body)["error"]["message"])


def declaring(url: str, length: int, key: str | None) -> tuple[int, bytes]:
    """What the server answers a request that says its body is this long, and sends none of it."""
    connection = open_connection(url)
    connection.putrequest("POST", urlsplit(url).path)
    if key is not None:
        connection.putheader("Authorization", f"Bearer {key}")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", str(length))
    connection.endheaders()
    reply = connection.getresponse()
    answer = reply.read()
    connection.close()
    return reply.status, answer


def leaves(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for part in error.exceptions for leaf in leaves(part)]
    return [error]


async def told_when_connecting(url: str, key: str | None) -> MCPError:
    """What an MCP client is told when it cannot get in."""
    try:
        async with connect(url, key):
            pytest.fail("the client got in without a valid agent key")
    except BaseException as error:
        found = [leaf for leaf in leaves(error) if isinstance(leaf, MCPError)]
        if not found:
            raise
        return found[0]
    raise AssertionError("unreachable")


def count_everything() -> str:
    """How much of everything the shared project holds, to see that a refused write changed
    nothing."""
    result = run_sql(
        "SELECT (SELECT count(*) FROM nodes) || '/' || (SELECT count(*) FROM links) || '/' || "
        "(SELECT count(*) FROM rules) || '/' || (SELECT count(*) FROM decisions) || '/' || "
        "(SELECT count(*) FROM facts) || '/' || (SELECT count(*) FROM claims) || '/' || "
        "(SELECT count(*) FROM events)",
        plain=True,
        as_project=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def stored_anywhere(text: str) -> int:
    """How many places in the whole database, in every project, hold this text."""
    like = f"'%{text}%'"
    result = run_sql(
        f"SELECT (SELECT count(*) FROM events WHERE after::text LIKE {like} "
        f"OR before::text LIKE {like} OR actor LIKE {like}) "
        f"+ (SELECT count(*) FROM nodes WHERE title LIKE {like} OR body LIKE {like}) "
        f"+ (SELECT count(*) FROM rules WHERE title LIKE {like} OR body LIKE {like}) "
        f"+ (SELECT count(*) FROM decisions WHERE title LIKE {like} OR text LIKE {like} "
        f"OR reason LIKE {like} OR decided_by LIKE {like} OR status_reason LIKE {like} "
        f"OR confirmed_by LIKE {like}) "
        f"+ (SELECT count(*) FROM facts WHERE text LIKE {like} OR source LIKE {like} "
        f"OR status_reason LIKE {like} OR confirmed_by LIKE {like} OR recorded_by LIKE {like}) "
        f"+ (SELECT count(*) FROM claims WHERE actor LIKE {like})",
        plain=True,
        project=None,
    )
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip())


# -- the agent key -------------------------------------------------------------------------------


def a_revoked_key() -> str:
    """An agent key that was good for the shared project and has been revoked."""
    project = the_project()
    made = project.owner.call(
        "POST", f"/board/api/projects/{project.id}/keys", {"label": "revoked soon"}
    )
    assert made.status == 201, made.body
    revoked = project.owner.call(
        "DELETE", f"/board/api/projects/{project.id}/keys/{made.body['key']['id']}"
    )
    assert revoked.status == 200, revoked.body
    return str(made.body["secret"])


async def test_a_request_with_no_key_or_a_wrong_one_is_refused_in_plain_words(
    server_url: str,
) -> None:
    url = server_url
    wrong = "vrk_wrong-" + unique_word()
    revoked = a_revoked_key()
    none = "needs an agent key for a project, and none was sent"
    invalid = "The agent key is not valid"

    cases: list[tuple[dict[str, Any], str]] = [
        ({}, none),
        ({"key": wrong}, invalid),
        ({"key": the_token() + "x"}, invalid),
        ({"key": the_token()[:-1]}, invalid),
        ({"key": revoked}, invalid),
        ({"authorization": f"Basic {the_token()}"}, "not a bearer token"),
        ({"authorization": the_token()}, "not a bearer token"),
        ({"authorization": ""}, none),
    ]
    for arguments, expected in cases:
        status, headers, body = request(url, **arguments)
        text = message_of(body)
        assert status == 401 and expected in text, (arguments, status, text)
        assert "Bearer <key>" in text
        assert headers["www-authenticate"] == 'Bearer realm="vrum"'
        shown = body.decode()
        assert the_token() not in shown and wrong not in shown and revoked not in shown
    # Every way of asking the MCP endpoint is the same.
    for method in ("GET", "DELETE", "POST"):
        status, _, _ = request(url, method=method, body=b"{}" if method == "POST" else None)
        assert status == 401, method
    assert request(url + "/anything", method="GET", body=None)[0] == 401
    # A key in the address is not a key.
    status, _, body = request(
        f"{url}?key={the_token()}&token={the_token()}&access_token={the_token()}"
    )
    assert status == 401 and the_token() not in body.decode()
    # The rest of the server holds nothing for a caller with no key to ask for.
    root = url.removesuffix("/mcp")
    for method, path in (("POST", "/"), ("GET", "/health/x"), ("GET", "/no/such/path")):
        status, _, body = request(
            root + path, method=method, body=b"{}" if method == "POST" else None
        )
        assert status == 404 and the_token() not in body.decode(), (method, path, status)
    # With a right key the request is let in (this one is not a real request, and says so).
    status, _, _ = request(url, key=the_token())
    assert status not in (401, 413)


async def test_an_mcp_client_without_a_key_or_with_a_wrong_one_is_told_why(
    server_url: str,
) -> None:
    none = await told_when_connecting(server_url, None)
    wrong = await told_when_connecting(server_url, "vrk_wrong-" + unique_word())
    revoked = await told_when_connecting(server_url, a_revoked_key())

    assert "needs an agent key for a project, and none was sent" in none.message
    assert none.code == -32001
    for told in (wrong, revoked):
        assert "The agent key is not valid" in told.message and "Bearer <key>" in told.message


async def test_the_health_check_and_the_page_files_are_the_only_open_doors_and_hold_no_data(
    server_url: str,
) -> None:
    root = server_url.removesuffix("/mcp")
    health = root + "/health"

    status, headers, body = request(health, method="GET", body=None)

    assert (status, body) == (200, b'{"status":"ok"}')
    assert headers["content-type"].startswith("application/json")
    assert request(health, method="POST", body=b"{}")[0] == 405
    assert request(health, method="DELETE", body=None)[0] == 405
    # The board's page files are open and hold no data. The sign-in page says no one is signed in.
    for path in ("/board", "/board/p/1", "/board/app.js"):
        status, _, body = request(root + path, method="GET", body=None)
        assert status == 200, path
        assert the_project().name.encode() not in body and the_token().encode() not in body
    status, _, body = request(root + "/board/api/session", method="GET", body=None)
    assert status == 200 and json.loads(body) == {"account": None, "csrf": None}
    # Everything else the board answers needs a person who is signed in and owns the project, and
    # there is no way in with an agent key: it is for agents, not for people.
    project = the_project().id
    for method, path in (
        ("GET", "/board/api/projects"),
        ("GET", f"/board/api/projects/{project}"),
        ("GET", f"/board/api/projects/{project}/keys"),
        ("GET", f"/board/api/projects/{project}/index"),
        ("POST", f"/board/api/projects/{project}/tool/list_nodes"),
        ("POST", f"/board/api/projects/{project}/try/list_nodes"),
        ("GET", "/board/api/tools"),
        ("GET", "/board/api/info"),
    ):
        status, _, body = request(
            root + path, method=method, key=the_token(), body=b"{}" if method == "POST" else None
        )
        assert status == 401, (method, path, status)
        assert "not signed in" in body.decode() and the_token() not in body.decode()
    # The tool route of the old, install-wide board is gone.
    assert request(root + "/board/api/tool/list_nodes", key=the_token())[0] in (404, 405)


# -- the size of a request -------------------------------------------------------------------------


async def test_a_request_that_is_too_big_is_refused_with_a_plain_message_and_the_server_goes_on(
    client: Client, server_url: str
) -> None:
    before = count_everything()
    body = "word " * (MAX_REQUEST_BYTES // 5 + 200)

    async with connect(server_url, the_token()) as sender:
        with pytest.raises(MCPError) as refusal:
            await sender.call_tool(
                "create_node", {"actor": "ann", "kind": "epic", "title": "Too big", "body": body}
            )

    text = refusal.value.message
    assert refusal.value.code == -32002
    assert f"{MAX_REQUEST_BYTES:,} bytes" in text and "most this server accepts" in text
    assert "VRUM_MAX_REQUEST_BYTES" in text and "Nothing was done" in text
    assert count_everything() == before
    # It carries on as if nothing had happened.
    assert (await ok(client, "server_status"))["vrum_version"]


async def test_a_request_is_refused_by_what_it_says_it_holds_before_its_body_is_read(
    server_url: str,
) -> None:
    status, answer = declaring(server_url, 50 * 1024 * 1024, the_token())

    assert status == 413
    assert f"{50 * 1024 * 1024:,} bytes" in message_of(answer)
    # Without a valid key it is not even looked at: the answer is the one about the key.
    status, answer = declaring(server_url, 50 * 1024 * 1024, "vrk_wrong-" + unique_word())
    assert status == 401 and "The agent key is not valid" in message_of(answer)


async def test_a_request_that_does_not_say_how_long_it_is_is_counted_as_it_arrives(
    server_url: str,
) -> None:
    connection = open_connection(server_url)
    chunk = b" " * 65536
    connection.request(
        "POST",
        urlsplit(server_url).path,
        body=(chunk for _ in range(MAX_REQUEST_BYTES // 65536 + 1)),
        headers={"Authorization": f"Bearer {the_token()}", "Content-Type": "application/json"},
    )

    reply = connection.getresponse()

    assert reply.status == 413 and "too big" in message_of(reply.read())
    connection.close()


async def test_a_request_just_under_the_limit_is_accepted(client: Client) -> None:
    # The same word again and again: a lot of text that is easy for the database to search.
    body = "word " * ((MAX_REQUEST_BYTES - 10_000) // 5)

    node = await ok(client, "create_node", actor="ann", kind="epic", title="A long one", body=body)

    assert node["body"] == body
    assert (await ok(client, "get_node", node_id=node["id"]))["body"] == body


async def test_a_text_too_long_to_be_searched_is_refused_plainly_and_not_as_a_broken_database(
    client: Client,
) -> None:
    before = count_everything()

    message = await refused(
        client,
        "create_node",
        actor="ann",
        kind="epic",
        title="Too long",
        body=different_words(2_000_000),
    )

    assert "too long to be searched" in message and "about 1 MB of different words" in message
    assert "Nothing was changed" in message and "Split the text" in message
    assert "cannot reach its database" not in message
    assert count_everything() == before
    assert (await ok(client, "server_status"))["postgres_version"]


# -- secrets in what is written ------------------------------------------------------------------


async def a_world(client: Client) -> dict[str, Any]:
    """One of everything a write can name, with a claim held by ann, for the cases below."""
    epic = await ok(client, "create_node", actor="ann", kind="epic", title="An epic")
    story = await ok(
        client, "create_node", actor="ann", kind="story", title="A story", parent_id=epic["id"]
    )
    task = await ok(
        client, "create_node", actor="ann", kind="task", title="A task", parent_id=story["id"]
    )
    await ok(client, "claim_node", actor="ann", node_id=task["id"])
    rule = await ok(client, "create_rule", actor="ann", title="A rule", story_id=story["id"])
    decision = await make_decision(client, "A decision", node_ids=[story["id"]])
    fact = await make_fact(client, "A fact")
    return {
        "story": story,
        "task": task,
        "rule": rule,
        "decision": decision,
        "fact": fact,
    }


def secret_cases(world: dict[str, Any]) -> list[tuple[str, Any, str]]:
    """(tool, a function from a text to the arguments with that text in the field, the field the
    refusal should name)."""
    story, task, rule = world["story"]["id"], world["task"]["id"], world["rule"]["id"]
    decision, fact = world["decision"]["id"], world["fact"]["id"]

    def plan(secret: str, where: str) -> dict[str, Any]:
        items = [
            {"key": "e", "kind": "epic", "title": "An epic"},
            {"key": "s", "kind": "story", "title": "A story", "parent": "e"},
        ]
        rules = [{"title": "A rule", "story": "s"}]
        if where == "items[0].title":
            items[0]["title"] = secret
        elif where == "items[1].body":
            items[1]["body"] = secret
        elif where == "rules[0].title":
            rules[0]["title"] = secret
        else:
            rules[0]["body"] = secret
        return {"actor": "a", "items": items, "rules": rules}

    def edit(tool: str, field: str, **fixed: Any) -> tuple[str, Any, str]:
        return tool, lambda secret: {**fixed, field: secret}, field

    record = {"actor": "a", "title": "t", "text": "x", "reason": "r", "decided_by": "Marie"}
    replace = {**record, "decision_id": decision}
    supersede = {"actor": "a", "replaces": [fact], "reason": "r", "text": "x", "source": "x"}
    withdraw_decision = {"actor": "a", "decision_id": decision, "reason": "r"}
    withdraw_fact = {"actor": "a", "fact_ids": [fact], "reason": "r"}
    confirm_fact = {"actor": "a", "kind": "fact", "record_id": fact, "confirmation": "confirmed"}
    unconfirm_decision = {
        "actor": "a",
        "kind": "decision",
        "record_id": decision,
        "confirmation": "unconfirmed",
    }
    task_edit = {"actor": "a", "node_id": task, "expected_version": 1}
    rule_edit = {"actor": "a", "rule_id": rule, "expected_version": 1}
    claimed = {"actor": "ann", "node_id": task}
    created = {"actor": "a", "kind": "epic", "title": "t"}
    return [
        edit("create_node", "title", actor="a", kind="epic"),
        edit("create_node", "body", **created),
        edit("create_node", "actor", kind="epic", title="t"),
        edit("update_node", "title", **task_edit),
        edit("update_node", "body", **task_edit),
        (
            "update_nodes",
            lambda s: {
                "actor": "a",
                "changes": [
                    {"node_id": task, "expected_version": 1, "priority": "high"},
                    {"node_id": task, "expected_version": 1, "title": s},
                ],
            },
            "changes[1].title",
        ),
        (
            "update_nodes",
            lambda s: {
                "actor": "a",
                "changes": [{"node_id": task, "expected_version": 1, "body": s}],
            },
            "changes[0].body",
        ),
        ("create_plan", lambda s: plan(s, "items[0].title"), "items[0].title"),
        ("create_plan", lambda s: plan(s, "items[1].body"), "items[1].body"),
        ("create_plan", lambda s: plan(s, "rules[0].title"), "rules[0].title"),
        ("create_plan", lambda s: plan(s, "rules[0].body"), "rules[0].body"),
        edit("create_rule", "title", actor="a"),
        edit("create_rule", "body", actor="a", title="t"),
        edit("update_rule", "title", **rule_edit),
        edit("update_rule", "body", **rule_edit),
        *(edit("record_decision", field, **record) for field in ("title", "text", "reason")),
        edit("record_decision", "decided_by", **record),
        *(edit("supersede_decision", f, **replace) for f in ("title", "text", "reason")),
        edit("supersede_decision", "decided_by", **replace),
        edit("retain_fact", "text", actor="a", source="x"),
        edit("retain_fact", "source", actor="a", text="x"),
        edit("supersede_decision", "said_by", **replace, quote="q"),
        edit("withdraw_decision", "reason", actor="a", decision_id=decision),
        edit("withdraw_decision", "quote", **withdraw_decision, said_by="Marie"),
        edit("supersede_fact", "text", **supersede),
        edit("supersede_fact", "reason", **supersede),
        edit("supersede_fact", "source", **supersede),
        edit("supersede_fact", "said_by", **supersede, quote="q"),
        edit("withdraw_fact", "reason", actor="a", fact_ids=[fact]),
        edit("withdraw_fact", "quote", **withdraw_fact, said_by="Marie"),
        edit("set_confirmation", "person", **confirm_fact),
        edit("set_confirmation", "quote", **confirm_fact, person="Marie"),
        edit("set_confirmation", "person", **unconfirm_decision, quote="q"),
        edit("set_confirmation", "quote", **unconfirm_decision, person="Marie"),
        edit("finish_work", "note", **claimed, outcome="done"),
        edit("release_claim", "note", **claimed),
        edit("claim_node", "actor", node_id=task),
        edit("add_link", "actor", source_id=story, target_id=task, link_type="relates_to"),
        edit("search", "query"),
        edit("similar_items", "text"),
    ]


async def test_a_secret_in_any_free_text_field_is_refused_and_nothing_is_stored_or_repeated(
    client: Client,
) -> None:
    world = await a_world(client)
    secret = a_secret()
    cases = secret_cases(world)
    assert len(cases) >= 40
    task_before = await ok(client, "get_node", node_id=world["task"]["id"])
    before = count_everything()

    for tool, build, path in cases:
        message = await refused(client, tool, **build(f"before {secret} after"))
        where = f"{tool} with {path}"
        assert f"The {path} field contains what looks like a GitHub token" in message, where
        assert "was not stored" in message and "nothing was changed" in message, where
        assert "was not sent to any other service" in message, where
        assert "Take the secret out and try again" in message, where
        assert secret not in message and secret[:12] not in message, where

    assert count_everything() == before
    assert stored_anywhere(secret) == 0
    assert await ok(client, "get_node", node_id=world["task"]["id"]) == task_before


async def test_ordinary_text_that_talks_about_secrets_is_not_refused(client: Client) -> None:
    text = (
        "Users reset their password by email. The API key is shown once in settings, and the "
        "token expires after 15 minutes. A GitHub token starts with ghp_, and sk- starts "
        "OpenAI keys. Set password = 'hunter2' in the example."
    )

    node = await ok(client, "create_node", actor="ann", kind="epic", title="Passwords", body=text)
    fact = await make_fact(client, text)

    assert (await ok(client, "get_node", node_id=node["id"]))["body"] == text
    assert fact["text"] == text
    decision = await make_decision(client, "Passwords", text=text, reason=text)
    assert (decision["text"], decision["reason"]) == (text, text)


# -- plain refusals ------------------------------------------------------------------------------


async def test_arguments_that_do_not_fit_are_refused_in_plain_words_without_repeating_them(
    client: Client,
) -> None:
    word = unique_word()
    big_body = "padding " * 20_000 + word

    missing = await refused(client, "create_node", kind="epic", title="t", body=big_body)
    wrong_type = await refused(client, "list_nodes", limit=f"not a number {word}")
    several = await refused(client, "get_node", node_id=[1, 2], extra=word)

    assert "the arguments are not valid" in missing and "actor: Field required" in missing
    assert word not in missing and len(missing) < 500
    assert "limit: Input should be a valid integer" in wrong_type and word not in wrong_type
    assert "Nothing was changed" in wrong_type and "against the tool's description" in wrong_type
    assert "node_id:" in several and word not in several
    for message in (missing, wrong_type, several):
        assert "pydantic" not in message and "input_value" not in message
    # A secret in a field of the wrong type is a secret first, and is not repeated either.
    secret = a_secret()
    message = await refused(client, "list_nodes", limit=secret)
    assert "contains what looks like a GitHub token" in message and secret not in message
    assert "Unknown tool" in await refused(client, "no_such_tool_" + word)


# -- paging everywhere ---------------------------------------------------------------------------

# The tools that return a list a page at a time. Each needs the id of something real.
PAGED = (
    "list_nodes",
    "node_history",
    "list_links",
    "ready_work",
    "changes_since",
    "list_rules",
    "rule_history",
    "coverage_report",
    "list_decisions",
    "decision_history",
    "list_facts",
    "fact_history",
    "recall",
    "search",
    "similar_items",
    "find_overlaps",
    "find_mentions",
    "export_graph",
)
# The tools whose lists are not paged, because the list is only as long as what was sent in, or is
# one item's own links. A new tool with a list that is in none of these groups fails the test
# below, so that it is decided on purpose.
BOUNDED = {
    "create_node",
    "get_node",
    "update_node",
    "move_node",
    "finish_work",
    "create_plan",
    "update_nodes",
    "record_decision",
    "get_decision",
    "link_decision",
    "unlink_decision",
    "supersede_decision",
    "withdraw_decision",
    "retain_fact",
    "get_fact",
    "link_fact",
    "unlink_fact",
    "supersede_fact",
    "withdraw_fact",
    "reindex",
    # One entry for each person or agent who has taken work: as many as there are names.
    "who_is_working",
}


async def test_every_tool_that_returns_a_list_pages_it_or_is_known_to_be_bounded(
    client: Client,
) -> None:
    pages: set[str] = set()
    lists: set[str] = set()
    for tool in (await client.list_tools()).tools:
        schema = json.dumps(tool.output_schema or {})
        properties = (tool.output_schema or {}).get("properties", {})
        if '"next_' in schema:
            pages.add(tool.name)
            assert "limit" in tool.input_schema["properties"], tool.name
        elif any(spec.get("type") == "array" for spec in properties.values()):
            lists.add(tool.name)

    assert pages == set(PAGED), sorted(pages ^ set(PAGED))
    assert lists <= BOUNDED, f"lists with no paging, not known to be bounded: {lists - BOUNDED}"


@pytest.mark.parametrize("name", PAGED)
async def test_a_page_size_outside_one_to_two_hundred_is_refused_in_plain_words(
    client: Client, name: str
) -> None:
    world = await a_world(client)
    needs: dict[str, dict[str, Any]] = {
        "node_history": {"node_id": world["task"]["id"]},
        "list_links": {"node_id": world["task"]["id"]},
        "rule_history": {"rule_id": world["rule"]["id"]},
        "decision_history": {"decision_id": world["decision"]["id"]},
        "fact_history": {"fact_id": world["fact"]["id"]},
        "recall": {"node_id": world["story"]["id"]},
        "search": {"query": "anything"},
        "similar_items": {"text": "anything"},
    }
    arguments = needs.get(name, {})

    for size in (0, 201, -5):
        message = await refused(client, name, limit=size, **arguments)
        assert "limit must be a whole number from 1 to 200" in message, (name, size, message)
    await ok(client, name, limit=200, **arguments)
    await ok(client, name, limit=1, **arguments)


async def test_a_reindex_report_lists_a_few_of_what_failed_and_counts_all_of_them(
    client: Client,
) -> None:
    report = await ok(client, "reindex")
    assert report["failed_total"] == len(report["failed"]) <= 50


# -- the logs ------------------------------------------------------------------------------------


def log_of(service: str) -> str:
    result = compose("logs", "--no-log-prefix", "--no-color", service)
    assert result.returncode == 0, result.stderr
    return result.stdout + result.stderr


async def test_the_logs_say_what_happened_and_hold_no_key_no_password_no_session_and_no_text(
    client: Client, server_url: str
) -> None:
    content = unique_word()
    actor = f"logger-{unique_word()}"
    secret = a_secret()
    wrong = "vrk_wrong-" + unique_word()
    wrong_password = "wrong-password-" + unique_word()
    body = f"a body that only this test writes {content}"
    shared = the_project()

    await ok(client, "create_node", actor=actor, kind="epic", title=f"Title {content}", body=body)
    # A fact and a decision, and a person's confirmation with their words, are not logged either.
    recorded = await make_fact(client, f"A fact {content}", source=f"A source {content}")
    await confirm(client, "fact", recorded, person=f"Reviewer {content}", quote=f"Quote {content}")
    await make_decision(client, f"Decision {content}", reason=f"Reason {content}")
    await refused(client, "claim_node", actor=actor, node_id=999999999)
    await refused(client, "create_node", actor=actor, kind="epic", title="t", body=secret)
    await refused(client, "create_node", actor=secret, kind="epic", title="t")
    await refused(client, "get_node", node_id=999999999)
    await refused(client, "list_nodes", limit="x")
    revoked = a_revoked_key()
    request(server_url, key=wrong)
    request(server_url, key=revoked)
    request(server_url)
    declaring(server_url, MAX_REQUEST_BYTES + 100, the_token())
    # A person registers, signs in twice (once with a wrong password), makes a project and a key,
    # uses the key, and signs out.
    person = register()
    signed_in = person.sign_in_again()
    failed = api(
        person.base,
        "POST",
        "/board/api/session",
        {"email": person.email, "password": wrong_password},
    )
    assert failed.status == 401
    own = make_project(person, f"Own {content}")
    async with connect(own.mcp_url, own.key) as own_client:
        await ok(own_client, "server_status")
    assert signed_in.call("DELETE", "/board/api/session").status == 200
    await anyio.sleep(0.5)

    log = log_of("app")
    lines = [json.loads(line) for line in log.splitlines() if line.strip()]
    # Every line is JSON with a time, a level and what happened.
    assert lines and all({"time", "level", "event"} <= line.keys() for line in lines)
    mine = [line for line in lines if line.get("actor") == actor]
    assert [(line["tool"], line["outcome"]) for line in mine] == [
        ("create_node", "ok"),
        ("claim_node", "refused"),
    ]
    # A call says which project it was for, by number.
    assert all(line["project"] == shared.id for line in mine)
    # A call refused for holding a secret is logged with no actor, as the secret may be the actor.
    assert any(
        line["event"] == "tool_call" and line["outcome"] == "refused" and line["actor"] is None
        for line in lines
    )
    assert all(isinstance(line["ms"], int) and line["ms"] >= 0 for line in mine)
    outcomes = {line["outcome"] for line in lines if line["event"] == "tool_call"}
    assert {"ok", "refused", "invalid"} <= outcomes
    refusals = [line for line in lines if line["event"] == "request_refused"]
    assert {"no_key", "wrong_key", "too_large"} <= {line["reason"] for line in refusals}
    assert all({"status", "method", "path", "client"} <= line.keys() for line in refusals)
    assert any(line["event"] == "server_started" for line in lines)
    # What must never be in them is in none of them.
    for forbidden in (
        the_token(),
        revoked,
        own.key,
        shared.owner.password,
        shared.owner.cookie,
        shared.owner.csrf,
        person.password,
        wrong_password,
        person.cookie,
        person.csrf,
        signed_in.cookie,
        signed_in.csrf,
        os.environ["VRUM_DB_PASSWORD"],
        wrong,
        secret,
        secret[:12],
        content,
        "Authorization",
        "Bearer",
    ):
        assert forbidden not in log, f"the log holds {forbidden[:8]}..."
