"""M6 against the running containers: the token, the size limit, secrets in what is written, plain
refusals, paging everywhere, and the logs (see ./check.sh).

Raw HTTP is used where what matters is what the server answers a caller that is not an MCP client
(no token, a wrong one, a body that is too big), and a real MCP client where what matters is what a
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
    ScriptedModel,
    compose,
    connect,
    make_belief,
    make_decision,
    make_fact,
    ok,
    refused,
    run_sql,
    the_token,
    unique_word,
)

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
    token: str | None = None,
    authorization: str | None = None,
    body: bytes | None = b"{}",
) -> tuple[int, dict[str, str], bytes]:
    """One HTTP request, answered whatever the status."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
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


def declaring(url: str, length: int, token: str | None) -> tuple[int, bytes]:
    """What the server answers a request that says its body is this long, and sends none of it."""
    connection = open_connection(url)
    connection.putrequest("POST", urlsplit(url).path)
    if token is not None:
        connection.putheader("Authorization", f"Bearer {token}")
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


async def told_when_connecting(url: str, token: str | None) -> MCPError:
    """What an MCP client is told when it cannot get in."""
    try:
        async with connect(url, token):
            pytest.fail("the client got in without a valid token")
    except BaseException as error:
        found = [leaf for leaf in leaves(error) if isinstance(leaf, MCPError)]
        if not found:
            raise
        return found[0]
    raise AssertionError("unreachable")


def count_everything() -> str:
    """How much of everything there is, to see that a refused write changed nothing."""
    result = run_sql(
        "SELECT (SELECT count(*) FROM nodes) || '/' || (SELECT count(*) FROM links) || '/' || "
        "(SELECT count(*) FROM rules) || '/' || (SELECT count(*) FROM decisions) || '/' || "
        "(SELECT count(*) FROM memories) || '/' || (SELECT count(*) FROM claims) || '/' || "
        "(SELECT count(*) FROM events)",
        plain=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def stored_anywhere(text: str) -> int:
    """How many places in the database hold this text."""
    like = f"'%{text}%'"
    result = run_sql(
        f"SELECT (SELECT count(*) FROM events WHERE after::text LIKE {like} "
        f"OR before::text LIKE {like} OR actor LIKE {like}) "
        f"+ (SELECT count(*) FROM nodes WHERE title LIKE {like} OR body LIKE {like}) "
        f"+ (SELECT count(*) FROM rules WHERE title LIKE {like} OR body LIKE {like}) "
        f"+ (SELECT count(*) FROM decisions WHERE title LIKE {like} OR text LIKE {like} "
        f"OR reason LIKE {like} OR decided_by LIKE {like}) "
        f"+ (SELECT count(*) FROM memories WHERE text LIKE {like} OR source LIKE {like} "
        f"OR status_reason LIKE {like} OR confirmed_by LIKE {like}) "
        f"+ (SELECT count(*) FROM claims WHERE actor LIKE {like})",
        plain=True,
    )
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip())


@pytest.fixture
def no_model_url() -> str:
    return os.environ["VRUM_NO_MODEL_TEST_URL"]


# -- the token -----------------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["with_model", "without_model"])
async def test_a_request_with_no_token_or_a_wrong_one_is_refused_in_plain_words(
    which: str, server_url: str, no_model_url: str
) -> None:
    url = server_url if which == "with_model" else no_model_url
    wrong = "a-wrong-token-" + unique_word()

    cases: list[tuple[dict[str, Any], str]] = [
        ({}, "needs a token and none was sent"),
        ({"token": wrong}, "token is not right"),
        ({"token": the_token() + "x"}, "token is not right"),
        ({"token": the_token()[:-1]}, "token is not right"),
        ({"authorization": f"Basic {the_token()}"}, "not a bearer token"),
        ({"authorization": the_token()}, "not a bearer token"),
        ({"authorization": ""}, "needs a token and none was sent"),
    ]
    for arguments, expected in cases:
        status, headers, body = request(url, **arguments)
        text = message_of(body)
        assert status == 401 and expected in text, (arguments, status, text)
        assert "Bearer <token>" in text
        assert headers["www-authenticate"] == 'Bearer realm="vrum"'
        assert the_token() not in body.decode() and wrong not in body.decode()
    # Any other way of asking gets the same: nothing but the health check is open.
    root = url.removesuffix("/mcp")
    for method, path in (("GET", "/mcp"), ("DELETE", "/mcp"), ("POST", "/"), ("GET", "/health/x")):
        status, _, _ = request(root + path, method=method, body=b"{}" if method == "POST" else None)
        assert status == 401, (method, path)
    # A token in the address is not a token.
    status, _, body = request(f"{url}?token={the_token()}&access_token={the_token()}")
    assert status == 401 and the_token() not in body.decode()
    # With the right token the request is let in (this one is not a real request, and says so).
    status, _, _ = request(url, token=the_token())
    assert status not in (401, 413)


async def test_an_mcp_client_without_the_token_or_with_a_wrong_one_is_told_why(
    server_url: str,
) -> None:
    none = await told_when_connecting(server_url, None)
    wrong = await told_when_connecting(server_url, "wrong-token-" + unique_word())

    assert "needs a token and none was sent" in none.message and none.code == -32001
    assert "token is not right" in wrong.message and "Bearer <token>" in wrong.message


async def test_the_health_check_needs_no_token_says_only_ok_and_is_the_only_open_door(
    server_url: str,
) -> None:
    health = server_url.removesuffix("/mcp") + "/health"

    status, headers, body = request(health, method="GET", body=None)

    assert (status, body) == (200, b'{"status":"ok"}')
    assert headers["content-type"].startswith("application/json")
    assert request(health, method="POST", body=b"{}")[0] == 401
    assert request(health, method="DELETE", body=None)[0] == 401


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
    # Without a valid token it is not even looked at: the answer is the one about the token.
    status, answer = declaring(server_url, 50 * 1024 * 1024, "wrong-" + unique_word())
    assert status == 401 and "token is not right" in message_of(answer)


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
    belief = await make_belief(client, [fact], "A belief")
    return {
        "story": story,
        "task": task,
        "rule": rule,
        "decision": decision,
        "fact": fact,
        "belief": belief,
    }


def secret_cases(world: dict[str, Any]) -> list[tuple[str, Any, str]]:
    """(tool, a function from a text to the arguments with that text in the field, the field the
    refusal should name)."""
    story, task, rule = world["story"]["id"], world["task"]["id"], world["rule"]["id"]
    decision, fact, belief = world["decision"]["id"], world["fact"]["id"], world["belief"]["id"]

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
    promote = {"actor": "a", "memory_id": belief, "title": "t", "reason": "r", "decided_by": "M"}
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
        edit("record_belief", "text", actor="a", evidence_fact_ids=[fact]),
        edit("supersede_memory", "text", **supersede),
        edit("supersede_memory", "reason", **supersede),
        edit("supersede_memory", "source", **supersede),
        edit("supersede_memory", "said_by", **supersede, quote="q"),
        edit("retract_memory", "reason", actor="a", memory_ids=[fact]),
        edit("retract_memory", "quote", actor="a", memory_ids=[fact], reason="r", said_by="Marie"),
        edit("confirm_belief", "confirmed_by", actor="a", memory_id=belief),
        edit("promote_belief", "title", **promote),
        edit("promote_belief", "text", **promote),
        edit("promote_belief", "reason", **promote),
        edit("finish_work", "note", **claimed, outcome="done"),
        edit("release_claim", "note", **claimed),
        edit("claim_node", "actor", node_id=task),
        edit("add_link", "actor", source_id=story, target_id=task, link_type="relates_to"),
        edit("search", "query"),
        edit("similar_items", "text"),
        edit("extract_facts", "text", actor="a"),
        edit("consolidate_memory", "query", actor="a"),
        edit("find_contradictions", "statement", actor="a"),
        edit("reflect", "question", actor="a"),
    ]


async def test_a_secret_in_any_free_text_field_is_refused_and_nothing_is_stored_or_repeated(
    client: Client,
) -> None:
    world = await a_world(client)
    secret = a_secret()
    cases = secret_cases(world)
    assert len(cases) >= 45
    task_before = await ok(client, "get_node", node_id=world["task"]["id"])
    before = count_everything()

    for tool, build, path in cases:
        message = await refused(client, tool, **build(f"before {secret} after"))
        where = f"{tool} with {path}"
        assert f"The {path} field contains what looks like a GitHub token" in message, where
        assert "was not stored" in message and "nothing was changed" in message, where
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


async def test_a_secret_a_model_proposes_is_refused_when_it_is_written_and_nothing_is_kept(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    secret = a_secret()
    text = f"The nightly export {marker} runs at 02:00."
    model.script(
        marker,
        {"facts": [{"text": f"The deploy key is {secret}", "kind": "world", "passage": text}]},
    )
    before = count_everything()

    message = await refused(client, "extract_facts", actor="ann", text=text, store=True)

    assert "The text field contains what looks like a GitHub token" in message
    assert secret not in message
    assert count_everything() == before and stored_anywhere(secret) == 0
    # The same for a belief a model draws up.
    fact = await make_fact(client, f"{marker} a fact to draw on")
    model.script(
        marker,
        {"beliefs": [{"text": f"The key is {secret}", "evidence_fact_ids": [fact["id"]]}]},
    )
    message = await refused(client, "consolidate_memory", actor="ann", fact_ids=[fact["id"]])
    assert "contains what looks like a GitHub token" in message and secret not in message
    assert stored_anywhere(secret) == 0


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
    "list_memories",
    "memory_history",
    "recall",
    "search",
    "similar_items",
    "find_overlaps",
)
# find_contradictions goes through its beliefs a batch at a time (batch, after_id).
BATCHED = ("find_contradictions",)
# The tools whose lists are not paged, because the list is only as long as what was sent in, or is
# one item's own links, or is what a model said in one answer. A new tool with a list that is in
# none of these groups fails the test below, so that it is decided on purpose.
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
    "retain_fact",
    "record_belief",
    "supersede_memory",
    "retract_memory",
    "confirm_belief",
    "promote_belief",
    "link_memory",
    "unlink_memory",
    "get_memory",
    "extract_facts",
    "consolidate_memory",
    "reflect",
    "reindex",
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
            assert "limit" in tool.input_schema["properties"] or tool.name in BATCHED, tool.name
        elif any(spec.get("type") == "array" for spec in properties.values()):
            lists.add(tool.name)

    assert pages == set(PAGED) | set(BATCHED), sorted(pages ^ (set(PAGED) | set(BATCHED)))
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
        "memory_history": {"memory_id": world["fact"]["id"]},
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


async def test_the_logs_say_what_happened_and_hold_no_token_no_password_and_no_text(
    client: Client, server_url: str
) -> None:
    content = unique_word()
    actor = f"logger-{unique_word()}"
    secret = a_secret()
    wrong = "a-wrong-token-" + unique_word()
    body = f"a body that only this test writes {content}"

    await ok(client, "create_node", actor=actor, kind="epic", title=f"Title {content}", body=body)
    await refused(client, "claim_node", actor=actor, node_id=999999999)
    await refused(client, "create_node", actor=actor, kind="epic", title="t", body=secret)
    await refused(client, "create_node", actor=secret, kind="epic", title="t")
    await refused(client, "get_node", node_id=999999999)
    await refused(client, "list_nodes", limit="x")
    request(server_url, token=wrong)
    request(server_url)
    declaring(server_url, MAX_REQUEST_BYTES + 100, the_token())
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
    # A call refused for holding a secret is logged with no actor, as the secret may be the actor.
    assert any(
        line["event"] == "tool_call" and line["outcome"] == "refused" and line["actor"] is None
        for line in lines
    )
    assert all(isinstance(line["ms"], int) and line["ms"] >= 0 for line in mine)
    outcomes = {line["outcome"] for line in lines if line["event"] == "tool_call"}
    assert {"ok", "refused", "invalid"} <= outcomes
    refusals = [line for line in lines if line["event"] == "request_refused"]
    assert {"no_token", "wrong_token", "too_large"} <= {line["reason"] for line in refusals}
    assert all({"status", "method", "path", "client"} <= line.keys() for line in refusals)
    assert any(line["event"] == "server_started" for line in lines)
    # What must never be in them is in none of them, on either server.
    for service in ("app", "app-no-model"):
        text = log if service == "app" else log_of(service)
        for forbidden in (
            the_token(),
            os.environ["VRUM_DB_PASSWORD"],
            wrong,
            secret,
            secret[:12],
            content,
            "Authorization",
            "Bearer",
        ):
            assert forbidden not in text, f"the {service} log holds {forbidden[:8]}..."
