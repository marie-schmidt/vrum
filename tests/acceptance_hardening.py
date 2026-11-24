"""Chapter 7 of the acceptance scenario: being careful (M6, kept by M16): the size of a request,
secrets in what is written, and the logs. Vrum has no language model, so there is nothing that
could propose a secret to it; what a person or an agent writes is all there is to guard."""

import json
import os
import random
import re
import string

from mcp.shared.exceptions import MCPError

from tests.acceptance_news import webhook_limits
from tests.acceptance_scenario import (
    MAX_REQUEST_BYTES,
    URL,
    chapter,
    clip,
    http_request,
    proof,
    rows,
    sql_value,
    tree,
)
from tests.helpers import compose, connect, make_fact, ok, refused, the_token, unique_word
from tests.world import join_project, make_project, register


def stored_anywhere(text: str) -> int:
    """How many places in the database hold this text."""
    like = f"'%{text}%'"
    return int(
        sql_value(
            f"SELECT (SELECT count(*) FROM events WHERE after::text LIKE {like} "
            f"OR before::text LIKE {like} OR actor LIKE {like}) "
            f"+ (SELECT count(*) FROM nodes WHERE title LIKE {like} OR body LIKE {like}) "
            f"+ (SELECT count(*) FROM rules WHERE title LIKE {like} OR body LIKE {like}) "
            f"+ (SELECT count(*) FROM notes WHERE text LIKE {like} OR why LIKE {like} "
            f"OR decided_by LIKE {like} OR status_reason LIKE {like})"
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
                    "create_node", {"kind": "epic", "title": "Too big", "body": body}
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

        fact = await make_fact(client, "A note to be changed")
        before = rows("nodes", "notes", "rules", "events")
        cases = (
            ("create_node", {"kind": "epic", "title": "Keys", "body": f"use {secret}"}),
            ("add_note", {"text": f"the key is {secret}", "why": "a chat"}),
            ("add_note", {"text": "t", "decided_by": "Marie", "why": f"because {secret}"}),
            ("create_plan", {"items": [
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
            and [f.group(1) for f in fields if f] == ["body", "text", "why", "items[1].body"]
            and not repeated
            and rows("nodes", "notes", "rules", "events") == before
            and stored_anywhere(secret) == 0,
            "fields named: " + ", ".join(f.group(1) for f in fields if f),
        )
        words = (
            ("set_confirmation", {"note_id": fact["id"], "confirmation": "confirmed",
                                  "person": "Marie", "quote": f"I checked {secret}"}),
            ("withdraw_note", {"note_ids": [fact["id"]], "reason": f"it was {secret}"}),
        )  # fmt: skip
        named = []
        for word_tool, word_arguments in words:
            message = await refused(client, word_tool, **word_arguments)
            named.append(re.search(r"The (\S+) field contains", message))
            repeated = repeated or secret in message or secret[:12] in message
        proof(
            "M16",
            "a secret is just as refused in the words of a person's confirmation and of a "
            "withdrawal, with no model anywhere to blame",
            all(named)
            and [n.group(1) for n in named if n] == ["quote", "reason"]
            and not repeated
            and (await ok(client, "get_note", note_id=fact["id"]))["status"] == "unconfirmed"
            and stored_anywhere(secret) == 0,
            "fields named: " + ", ".join(n.group(1) for n in named if n),
        )

        # -- M45: an ask and its answer are texts like any other, and only a person may answer
        own = make_project(register(), "Asks and secrets")
        viewer = join_project(own, "viewer")
        answer_path = f"/board/api/projects/{own.id}/tool/answer_ask"
        async with connect(own.mcp_url, own.key) as asker:
            _, _, waiting_work = await tree(asker, 1)
            await ok(asker, "claim_node", node=waiting_work["ref"])
            before = rows("nodes", "notes", "rules", "events")
            refusals = [
                await refused(
                    asker, "finish_work", node=waiting_work["ref"], outcome="blocked",
                    note="Waiting.", ask=ask,
                )
                for ask in (
                    {"question": f"Use {secret}?"},
                    {"question": "Which key?", "choices": [secret, "None"]},
                )
            ]  # fmt: skip
            after_refused = rows("nodes", "notes", "rules", "events")
            still_held = await ok(asker, "get_node", node=waiting_work["ref"])
            await ok(
                asker, "finish_work", node=waiting_work["ref"], outcome="blocked",
                note="Waiting for a decision.",
                ask={"question": "Which provider?", "choices": ["Stripe", "Adyen"]},
            )  # fmt: skip
            (waiting,) = (await ok(asker, "list_asks", status="open"))["items"]
            before_answers = rows("events")
            leaked = own.owner.call(
                "POST", answer_path,
                {"ask": waiting["id"], "answer": "Stripe", "comment": f"Use {secret}"},
            )  # fmt: skip
            as_viewer = viewer.call("POST", answer_path, {"ask": waiting["id"], "answer": "Stripe"})
            viewer_reads = [
                viewer.call("POST", f"/board/api/projects/{own.id}/tool/{name}", {}).status
                for name in ("list_asks", "needs_a_person")
            ]
            still_open = await ok(asker, "list_asks", status="open")
        proof(
            "M45",
            "a secret in the question or a choice of an ask, or in the comment of an answer, is "
            "refused in words that do not repeat it and nothing is stored (the work stays held, "
            "the ask stays open); the viewer of the project can read what is waiting and is "
            "refused when answering, and nothing is stored for that either",
            all(secret not in text and secret[:12] not in text for text in refusals)
            and after_refused == before
            and still_held["status"] != "blocked"
            and still_held["claimed_by"] is not None
            and leaked.status == 400
            and secret not in str(leaked.body)
            and "contains" in leaked.body["message"]
            and as_viewer.status in (400, 403)
            and viewer_reads == [200, 200]
            and rows("events") == before_answers
            and [a["id"] for a in still_open["items"]] == [waiting["id"]]
            and stored_anywhere(secret) == 0,
            f'"{clip(refusals[0], 70)}"',
        )
        ordinary = await ok(
            client, "create_node", kind="epic", title="Passwords",
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
    await webhook_limits()
