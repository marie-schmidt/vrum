"""Chapters 7 and 8 of the acceptance scenario: asking a language model for help without ever
mistaking what it says for a record (M5C), and being careful: the size of a request, secrets in
what is written, and the logs (M6).

The model is the scripted stand-in of the check stack: a test tells it what to answer and later asks
what it was sent. So these prove what Vrum does with a model's output, not how good a model is."""

import json
import os
import random
import re
import string
from typing import Any

from mcp import Client
from mcp.shared.exceptions import MCPError

from tests.acceptance_scenario import (
    MAX_REQUEST_BYTES,
    NO_MODEL_URL,
    URL,
    chapter,
    clip,
    http_request,
    ids,
    proof,
    rows,
    sql_value,
    stories,
)
from tests.helpers import (
    ScriptedModel,
    compose,
    connect,
    make_belief,
    make_decision,
    make_fact,
    model_stopped,
    ok,
    refused,
    the_token,
    unique_word,
)

# Put together in pieces so that nothing in this file looks like a real secret.
SECRET = "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5"

FOUR_TOOLS: dict[str, dict[str, Any]] = {
    "extract_facts": {"text": "The export runs at night."},
    "consolidate_memory": {"query": "anything at all"},
    "find_contradictions": {"statement": "The export never runs."},
    "reflect": {"question": "When does the export run?"},
}


def written() -> tuple[int, ...]:
    """How many facts and beliefs, decisions and events there are: what a refusal must not change
    (apart from the one event that logs a call the model answered)."""
    return rows("memories", "decisions", "events")


def belief_reply(*beliefs: dict[str, Any]) -> dict[str, Any]:
    return {"beliefs": list(beliefs)}


def contradiction(belief: dict[str, Any], marker: str, **changes: Any) -> dict[str, Any]:
    return {
        "belief_id": belief["id"],
        "reason": "They cannot both be true.",
        "belief_quote": marker,
        "statement_quote": marker,
        **changes,
    }


async def beliefs_about(client: Client, marker: str, count: int) -> list[dict[str, Any]]:
    fact = await make_fact(client, f"{marker} the evidence")
    return [
        await make_belief(client, [fact], f"{marker} {unique_word()} {unique_word()}")
        for _ in range(count)
    ]


async def model_calls(client: Client, actor: str) -> list[dict[str, Any]]:
    """The calls to the model this actor made, from the event log."""
    found: list[dict[str, Any]] = []
    after = 0
    while after is not None:
        page = await ok(client, "changes_since", after_event_id=after, actor=actor, limit=200)
        found += [e for e in page["items"] if e["action"] == "model_call"]
        after = page["next_after_id"]
    return found


async def model_help() -> None:
    chapter("Asking a model for help: what it proposes is checked, labelled, and never a record")
    model = ScriptedModel()
    async with connect(URL, the_token()) as client, connect(NO_MODEL_URL, the_token()) as bare:
        # -- 1. no model set up
        said = [
            "No language model is set up on this server, so " + tool + " cannot run"
            in await refused(bare, tool, actor="ann", **arguments)
            for tool, arguments in FOUR_TOOLS.items()
        ]
        word = unique_word()
        kept = await make_fact(bare, f"The {word} service is slow")
        found = await ok(bare, "search", query=word, mode="words")
        with_model = await ok(client, "server_status")
        without = await ok(bare, "server_status")
        proof(
            "M5C",
            "with no model configured the four tools say so and every other tool works; "
            "server_status says which model there is, if any",
            all(said)
            and found["total"] == 1
            and found["items"][0]["id"] == kept["id"]
            and without["language_model"] is None
            and with_model["language_model"] == "scripted-model",
            f"model: {with_model['language_model']} here, none on the second server",
        )

        actor = f"ann-{unique_word()}"
        markers: list[str] = []

        def mark() -> str:
            markers.append(unique_word())
            return markers[-1]

        # -- 2. extract_facts
        marker = mark()
        (story,) = (await stories(client, 1))[1:]
        text = f"The nightly export {marker} runs at 02:00.\nIt writes to the shared drive."
        passage = f"The nightly export {marker} runs at 02:00."
        model.script(
            marker,
            {
                "facts": [
                    {"text": f"The export {marker} runs at 02:00", "kind": "world",
                     "passage": passage},
                    {"text": "Exports run twice a day", "kind": "world",
                     "passage": f"it runs twice a day {marker}"},
                ]
            },
        )  # fmt: skip
        before = written()
        result = await ok(client, "extract_facts", actor=actor, text=text)
        after = written()
        proof(
            "M5C",
            "extract_facts returns the scripted facts with their passages and stores nothing by "
            "default; a fact whose passage is not in the text is dropped",
            [p["passage"] for p in result["proposals"]] == [passage]
            and len(result["dropped"]) == 1
            and any("passage is not in the text" in d["why"] for d in result["dropped"])
            and result["stored"] is False
            and after[:2] == before[:2]
            and after[2] == before[2] + 1,
            "; ".join(d["why"] for d in result["dropped"]) or "nothing was dropped",
        )
        stored = await ok(
            client, "extract_facts", actor=actor, text=text, store=True, node_ids=[story["id"]]
        )
        (memory,) = stored["memories"]
        proof(
            "M5C",
            "with the store option it keeps them with origin extracted and the passage as source",
            (memory["origin"], memory["type"], memory["recorded_by"])
            == ("extracted", "fact", actor)
            and memory["source"] == passage
            and memory["node_ids"] == [story["id"]]
            and "proposed by a language model" in memory["label"],
            memory["label"][:70],
        )

        # -- 3. consolidate_memory
        marker = mark()
        facts = [
            await make_fact(client, f"{marker} fact {n}: the team uses tool {n}") for n in (1, 2, 3)
        ]
        one, two, three = facts
        outsider = await make_fact(client, f"Something else, not given to the model {marker}x")
        model.script(
            marker,
            belief_reply(
                {"text": f"{marker}: the team is standardising",
                 "evidence_fact_ids": [one["id"], two["id"]]},
                {"text": "Rests on a fact it was not given",
                 "evidence_fact_ids": [one["id"], outsider["id"]]},
            ),
        )  # fmt: skip
        result = await ok(client, "consolidate_memory", actor=actor, fact_ids=ids(facts))
        (belief,) = result["recorded"]
        proof(
            "M5C",
            "consolidate_memory records the scripted belief with origin derived and its evidence, "
            "and drops one that names a fact it was not given",
            (belief["origin"], belief["status"]) == ("derived", "active")
            and [(e["type"], e["id"]) for e in belief["evidence"]]
            == [("fact", one["id"]), ("fact", two["id"])]
            and f"fact {outsider['id']}, which it was not given" in result["dropped"][0]["why"],
            result["dropped"][0]["why"],
        )
        await ok(client, "retract_memory", actor="bob", memory_ids=[one["id"]], reason="A typo")
        stale = await ok(client, "get_memory", memory_id=belief["id"])
        model.script(
            marker,
            belief_reply(
                {"text": f"{marker}: revised conclusion", "evidence_fact_ids": [two["id"]]}
            ),
        )
        revised = await ok(client, "consolidate_memory", actor=actor, revise_belief_id=belief["id"])
        (new,) = revised["recorded"]
        old = await ok(client, "get_memory", memory_id=belief["id"])
        proof(
            "M5C",
            "retracting one piece of evidence makes the belief stale, and a revised belief "
            "supersedes it",
            stale["stale"] is True
            and revised["replaced_id"] == belief["id"]
            and (new["origin"], new["stale"]) == ("derived", False)
            and (old["status"], old["superseded_by_id"]) == ("superseded", new["id"]),
        )

        # -- 4. reflect
        marker = mark()
        (story2,) = (await stories(client, 1))[1:]
        d = await make_decision(
            client, f"Use PDF {marker}", text=f"Invoices {marker} are PDFs", node_ids=[story2["id"]]
        )
        f = await make_fact(client, f"The {marker} library is at version two")
        question = f"{marker} {unique_word()}"
        model.script(
            marker,
            {
                "answer": f"{marker} invoices are PDFs [decision:{d['id']}], on library two.",
                "cited": [f"decision:{d['id']}", f"fact:{f['id']}"],
            },
        )
        before = written()
        answer = await ok(client, "reflect", actor=actor, question=question)
        after = written()
        proof(
            "M5C",
            "reflect returns the scripted answer with the items it relied on, labelled generated, "
            "and stores nothing",
            answer["answered"] is True
            and [(r["item_type"], r["id"]) for r in answer["relied_on"]]
            == [("decision", d["id"]), ("fact", f["id"])]
            and "Generated by the language model scripted-model" in answer["label"]
            and "not a record, it was not stored" in answer["label"]
            and after[:2] == before[:2],
            answer["label"][:60],
        )
        model.script(marker, {"answer": "Yes.", "cited": [f"decision:{d['id']}", "fact:999999999"]})
        message = await refused(client, "reflect", actor=actor, question=question)
        proof(
            "M5C",
            "an answer that cites an item that was not retrieved is refused",
            "fact:999999999" in message and "refused and nothing was stored" in message,
            f'"{clip(message, 90)}"',
        )

        # -- 5. find_contradictions
        marker = mark()
        one, two, three = await beliefs_about(client, marker, 3)
        elsewhere = await make_belief(
            client, [await make_fact(client, "Unrelated basis")], "Nothing to do with the statement"
        )
        statement = f"{marker} {unique_word()}"
        active = int(
            sql_value(
                "SELECT count(*) FROM memory_state WHERE type = 'belief' AND status = 'active'",
                as_project=True,
            )
        )
        model.script(
            marker,
            {
                "contradictions": [
                    contradiction(one, marker, reason="  It is the opposite. "),
                    contradiction(two, marker, belief_quote="words that are not in the belief"),
                    contradiction(elsewhere, marker),
                ]
            },
        )
        before = written()
        result = await ok(client, "find_contradictions", actor=actor, statement=statement)
        after = written()
        whys = [x["why"] for x in result["dropped"]]
        (proposal,) = result["proposals"]
        proof(
            "M5C",
            "find_contradictions returns the scripted belief with the model's reason and the "
            "clashing quotes and changes nothing; a belief that is not in the batch, or a quote "
            "that is not there, is dropped",
            proposal["belief_id"] == one["id"]
            and proposal["reason"] == "It is the opposite."
            and any("quote of the belief is not in the belief" in why for why in whys)
            and any("not one of the beliefs that were shown" in why for why in whys)
            and after[:2] == before[:2],
            f"{len(whys)} dropped: {clip(whys[0], 60)}",
        )
        proof(
            "M5C",
            "with scope related it reports how many beliefs it looked at out of all active ones",
            (result["scope"], result["looked_at"], result["active_beliefs"])
            == ("related", 3, active)
            and f"Looked at 3 of {active} active beliefs" in result["message"],
            result["message"][:70],
        )
        replaced = await ok(
            client, "supersede_memory", actor=actor, replaces=[proposal["belief_id"]],
            reason="Contradicted", text=f"{marker} replacement",
            evidence_fact_ids=[one["evidence"][0]["id"]],
        )  # fmt: skip
        await ok(client, "confirm_belief", actor="ann", memory_id=three["id"], confirmed_by="Marie")
        model.script(marker, {"contradictions": [contradiction(three, marker)]})
        again = await ok(client, "find_contradictions", actor=actor, statement=statement)
        change = {
            "actor": actor, "replaces": [three["id"]], "reason": "Contradicted",
            "text": f"{marker} second replacement",
            "evidence_fact_ids": [three["evidence"][0]["id"]],
        }  # fmt: skip
        without_word = await refused(client, "supersede_memory", **change)
        with_word = await ok(
            client, "supersede_memory", **change, said_by="Marie", quote="That is no longer true"
        )
        proof(
            "M5C",
            "passing the returned ids to supersede_memory replaces them, and for a confirmed "
            "belief only when said_by and quote are given",
            replaced["superseded_ids"] == [one["id"]]
            and again["proposals"][0]["confirmed_by"] == "Marie"
            and "confirmed by Marie" in without_word
            and with_word["superseded_ids"] == [three["id"]],
        )

        # -- 6. every belief, in batches
        marker = mark()
        base = int(sql_value("SELECT coalesce(max(id), 0) FROM memories", as_project=True))
        passed = int(
            sql_value(
                "SELECT count(*) FROM memory_state WHERE type = 'belief' AND status = 'active' "
                f"AND id <= {base}",
                as_project=True,
            )
        )
        mine = await beliefs_about(client, marker, 45)
        sweep = f"{marker} {unique_word()}"
        model.script(marker, {"contradictions": [contradiction(mine[-1], marker)]})
        seen: list[dict[str, Any]] = []
        cursor = base
        late = None
        for call in range(1, 6):
            part = await ok(
                client, "find_contradictions", actor=actor, statement=sweep, scope="all",
                batch=10, after_id=cursor,
            )  # fmt: skip
            seen.append(part)
            if call == 4:
                late = await make_belief(
                    client, [await make_fact(client, f"{marker} late")], f"{marker} {unique_word()}"
                )
            cursor = part["next_after_id"]
            if cursor is None:
                break
        shown_ids = [
            int(shown)
            for request in model.received(marker)
            for shown in re.findall(r"\[belief:(\d+)\]", request["user"])
        ]
        assert late is not None
        proof(
            "M5C",
            "with 45 active beliefs and a batch of 10, scope all takes five calls, each says how "
            "many it has looked at and how many remain, a late belief is reached, the service "
            "was shown every belief exactly once, and the contradiction in the last batch is found",
            len(seen) == 5
            and [r["shown"] for r in seen] == [10, 10, 10, 10, 6]
            and [r["remaining"] for r in seen[:4]] == [35, 25, 15, 5]
            and seen[-1]["remaining"] == 0
            and all(r["looked_at"] + r["remaining"] == r["active_beliefs"] for r in seen)
            and [len(r["proposals"]) for r in seen] == [0, 0, 0, 0, 1]
            and sorted(shown_ids) == sorted(ids(mine) + [late["id"]])
            and passed >= 0,
            "remaining " + ", ".join(str(r["remaining"]) for r in seen),
        )

        # -- 7. a secret is never sent
        marker = mark()
        before = written()
        model.script(marker, {"facts": []})
        message = await refused(
            client, "extract_facts", actor=actor, text=f"{marker} the deploy key is {SECRET}"
        )
        proof(
            "M5C",
            "text containing a well-known secret format is not sent to the model: the service "
            "records that it did not receive it, and nothing was written",
            "contains what looks like a GitHub token" in message
            and "was not sent to the language model" in message
            and SECRET not in message
            and model.received(marker) == []
            and written() == before,
            f'"{clip(message, 80)}"',
        )

        # -- 8. the model goes away
        marker = mark()
        fact = await make_fact(client, f"{marker} a fact")
        await make_belief(client, [fact], f"{marker} a belief")
        calls: dict[str, dict[str, Any]] = {
            "consolidate_memory": {"fact_ids": [fact["id"]]},
            "find_contradictions": {"statement": f"{marker} x"},
            "reflect": {"question": marker},
            "extract_facts": {"text": f"{marker} the export runs at night."},
        }
        before = written()
        # What the stand-in was asked is lost when it is stopped, so count it first.
        asked = sum(len(model.received(m)) for m in markers)
        with model_stopped():
            texts = {t: await refused(client, t, actor=actor, **a) for t, a in calls.items()}
            unchanged = written() == before
            still_works = (await ok(client, "search", query=marker, mode="words"))["total"] >= 2
        proof(
            "M5C",
            "with the model service stopped each tool says plainly that it cannot reach it and "
            "nothing is written; everything else keeps working",
            all("cannot be reached" in t and "Nothing was changed" in t for t in texts.values())
            and unchanged
            and still_works,
            f'"{clip(texts["reflect"], 80)}"',
        )
        logged = await model_calls(client, actor)
        tools = {c["after"]["tool"] for c in logged}
        proof(
            "M5C",
            "every call that was answered is in the event log, about no one item, and the calls "
            "that were not answered are not",
            len(logged) == asked
            and tools == {"extract_facts", "consolidate_memory", "reflect", "find_contradictions"}
            and all(c["node_id"] is None and c["memory_id"] is None for c in logged),
            f"the model answered {asked} requests, the log holds {len(logged)} calls "
            f"({', '.join(sorted(tools))})",
        )


# ---------------------------------------------------------------------------------------------
# 8. Being careful: size, secrets and logs (M6)
# ---------------------------------------------------------------------------------------------


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
            f"+ (SELECT count(*) FROM memories WHERE text LIKE {like} OR source LIKE {like})"
        )
    )


async def hardening() -> None:
    chapter("Being careful: the size of a request, secrets in what is written, and the logs")
    model = ScriptedModel()
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

        before = rows("nodes", "memories", "rules", "decisions", "events")
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
        marker = unique_word()
        model.script(
            marker,
            {"facts": [{"text": f"The deploy key is {secret}", "kind": "world",
                        "passage": f"The nightly export {marker} runs at 02:00."}]},
        )  # fmt: skip
        proposed = await refused(
            client, "extract_facts", actor="ann", store=True,
            text=f"The nightly export {marker} runs at 02:00.",
        )  # fmt: skip
        proof(
            "M6",
            "a body containing a token in a well-known format is refused in every field that can "
            "hold text, nothing is stored, and the refusal does not repeat the token",
            all(fields)
            and [f.group(1) for f in fields if f] == ["body", "text", "reason", "items[1].body"]
            and not repeated
            and rows("nodes", "memories", "rules", "decisions", "events")[:4] == before[:4]
            and stored_anywhere(secret) == 0,
            "fields named: " + ", ".join(f.group(1) for f in fields if f),
        )
        proof(
            "M6",
            "the same when it is a model that proposes the secret: it is refused where the write "
            "is recorded, and nothing is kept",
            "contains what looks like a GitHub token" in proposed
            and secret not in proposed
            and stored_anywhere(secret) == 0,
            f'"{clip(proposed, 80)}"',
        )
        ordinary = await ok(
            client, "create_node", actor="ann", kind="epic", title="Passwords",
            body="Reset the password by email; the API key is shown once; a GitHub token "
            "starts with ghp_.",
        )  # fmt: skip
        proof("M6", "ordinary text that only talks about secrets is kept", ordinary["id"])

        http_request(URL, token=wrong)
        http_request(URL)
        logs = {}
        for service in ("app", "app-no-model"):
            result = compose("logs", "--no-log-prefix", "--no-color", service)
            logs[service] = result.stdout + result.stderr
        lines = [json.loads(line) for line in logs["app"].splitlines() if line.strip()]
        events = {line["event"] for line in lines}
        forbidden = {
            "the agent key": the_token(),
            "the wrong key": wrong,
            "the database password": os.environ["VRUM_DB_PASSWORD"],
            "the secret that was pasted": secret,
            "the word Authorization": "Authorization",
            "the word Bearer": "Bearer",
        }
        leaks = [
            f"{name} in the {service} log"
            for service, text in logs.items()
            for name, value in forbidden.items()
            if value in text
        ]
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
