"""Chapter 7 of the acceptance scenario: being careful (M6, kept by M16): the size of a request,
secrets in what is written, and the logs. Vrum has no language model, so there is nothing that
could propose a secret to it; what a person or an agent writes is all there is to guard."""

import json
import os
import random
import re
import string

from mcp.shared.exceptions import MCPError

from tests.acceptance_scenario import (
    MAX_REQUEST_BYTES,
    URL,
    chapter,
    clip,
    http_request,
    proof,
    rows,
    sql_value,
)
from tests.helpers import compose, connect, make_fact, ok, refused, the_token, unique_word


def stored_anywhere(text: str) -> int:
    """How many places in the database hold this text."""
    like = f"'%{text}%'"
    return int(
        sql_value(
            f"SELECT (SELECT count(*) FROM events WHERE after::text LIKE {like} "
            f"OR before::text LIKE {like} OR actor LIKE {like}) "
            f"+ (SELECT count(*) FROM nodes WHERE title LIKE {like} OR body LIKE {like}) "
            f"+ (SELECT count(*) FROM rules WHERE title LIKE {like} OR body LIKE {like}) "
            f"+ (SELECT count(*) FROM decisions WHERE title LIKE {like} OR text LIKE {like} "
            f"OR reason LIKE {like}) "
            f"+ (SELECT count(*) FROM facts WHERE text LIKE {like} OR source LIKE {like} "
            f"OR status_reason LIKE {like})"
        )
    )


async def hardening() -> None:
    chapter("Being careful: the size of a request, secrets in what is written, and the logs")
    secret = "ghp_" + "".join(random.choices(string.ascii_letters + string.digits, k=36))
    wrong = "vrk_a-wrong-key-" + unique_word() + "-0123456789"
    async with connect(URL, the_token()) as client:
        before = rows("nodes", "events")
        body = "word " * (MAX_REQUEST_BYTES // 5 + 200)
        async with connect(URL, the_token()) as sender:
            try:
                await sender.call_tool(
                    "create_node",
                    {"actor": "ann", "kind": "epic", "title": "Too big", "body": body},
                )
                raise AssertionError("the oversized request was accepted")
            except MCPError as error:
                told = error
        proof(
            "M6",
            "an oversized request is refused with a plain message, and nothing is stored",
            told.code == -32002
            and "most this server accepts" in told.message
            and "VRUM_MAX_REQUEST_BYTES" in told.message
            and "Nothing was done" in told.message
            and rows("nodes", "events") == before,
            f'"{clip(told.message, 100)}"',
        )
        proof(
            "M6",
            "the server carries on afterwards",
            (await ok(client, "server_status"))["vrum_version"],
        )

        fact = await make_fact(client, "A fact to be changed")
        before = rows("nodes", "facts", "rules", "decisions", "events")
        cases = (
            ("create_node", {"actor": "ann", "kind": "epic", "title": "Keys",
                             "body": f"use {secret}"}),
            ("retain_fact", {"actor": "ann", "text": f"the key is {secret}", "source": "a chat"}),
            ("record_decision", {"actor": "ann", "title": "t", "text": "x", "decided_by": "Marie",
                                 "reason": f"because {secret}"}),
            ("create_plan", {"actor": "ann", "items": [
                {"key": "e", "kind": "epic", "title": "An epic"},
                {"key": "s", "kind": "story", "title": "A story", "parent": "e", "body": secret}]}),
        )  # fmt: skip
        fields = []
        repeated = False
        for tool, arguments in cases:
            message = await refused(client, tool, **arguments)
            fields.append(re.search(r"The (\S+) field contains", message))
            repeated = repeated or secret in message or secret[:12] in message
        proof(
            "M6",
            "a body containing a token in a well-known format is refused in every field that can "
            "hold text, nothing is stored, and the refusal does not repeat the token",
            all(fields)
            and [f.group(1) for f in fields if f] == ["body", "text", "reason", "items[1].body"]
            and not repeated
            and rows("nodes", "facts", "rules", "decisions", "events")[:4] == before[:4]
            and stored_anywhere(secret) == 0,
            "fields named: " + ", ".join(f.group(1) for f in fields if f),
        )
        words = (
            ("set_confirmation", {"actor": "ann", "kind": "fact", "record_id": fact["id"],
                                  "confirmation": "confirmed", "person": "Marie",
                                  "quote": f"I checked {secret}"}),
            ("withdraw_fact", {"actor": "ann", "fact_ids": [fact["id"]],
                               "reason": f"it was {secret}"}),
        )  # fmt: skip
        named = []
        for tool, arguments in words:
            message = await refused(client, tool, **arguments)
            named.append(re.search(r"The (\S+) field contains", message))
            repeated = repeated or secret in message or secret[:12] in message
        proof(
            "M16",
            "a secret is just as refused in the words of a person's confirmation and of a "
            "withdrawal, the new fields of the two records, with no model anywhere to blame",
            all(named)
            and [n.group(1) for n in named if n] == ["quote", "reason"]
            and not repeated
            and (await ok(client, "get_fact", fact_id=fact["id"]))["confirmation"] == "unconfirmed"
            and (await ok(client, "get_fact", fact_id=fact["id"]))["status"] == "active"
            and stored_anywhere(secret) == 0,
            "fields named: " + ", ".join(n.group(1) for n in named if n),
        )
        ordinary = await ok(
            client, "create_node", actor="ann", kind="epic", title="Passwords",
            body="Reset the password by email; the API key is shown once; a GitHub token "
            "starts with ghp_.",
        )  # fmt: skip
        proof("M6", "ordinary text that only talks about secrets is kept", ordinary["ref"])

        http_request(URL, token=wrong)
        http_request(URL)
        result = compose("logs", "--no-log-prefix", "--no-color", "app")
        log = result.stdout + result.stderr
        lines = [json.loads(line) for line in log.splitlines() if line.strip()]
        events = {line["event"] for line in lines}
        forbidden = {
            "the agent key": the_token(),
            "the wrong key": wrong,
            "the database password": os.environ["VRUM_DB_PASSWORD"],
            "the secret that was pasted": secret,
            "the word Authorization": "Authorization",
            "the word Bearer": "Bearer",
        }
        leaks = [f"{name} in the log" for name, value in forbidden.items() if value in log]
        proof(
            "M6",
            "the logs say what happened, as JSON, and contain no key, no password and no secret",
            not leaks
            and {"server_started", "tool_call", "request_refused"} <= events
            and any(
                line["event"] == "tool_call"
                and line["outcome"] == "refused"
                and line["actor"] is None
                for line in lines
            ),
            f"{len(lines)} lines: {sum(x['event'] == 'tool_call' for x in lines)} tool calls, "
            f"{sum(x['event'] == 'request_refused' for x in lines)} refused requests",
        )
