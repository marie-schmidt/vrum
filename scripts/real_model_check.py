"""Try a running Vrum with a real language model and print what it does.

    VRUM_TOKEN=<the token in .env> uv run python scripts/real_model_check.py http://127.0.0.1:8000/mcp

The server has to be started with LLM_BASE_URL and LLM_MODEL pointing at the model (see
.env.example). The script adds a small, clearly named set of notes, facts, beliefs and decisions to
the graph it is pointed at, then asks the four tools that need a model (extract_facts,
consolidate_memory, find_contradictions, reflect) the same questions every time, and reports what
the model proposed and what the server dropped because it did not check out. Use it on a stack you
do not mind adding a few items to. ./check.sh uses a scripted stand-in model instead, which proves
what Vrum does with a model's output but says nothing about how good a model is.

What it prints is a trial on a handful of examples, to see whether a model follows the output rules
at all and how often the server has to correct it. It is not a benchmark."""

import asyncio
import sys
import time
from collections import Counter
from typing import Any

from mcp import Client
from vrum_client import connect

ACTOR = "real-model-check"
PERSON = "Marie"

NOTES = [
    (
        "invoice handoff",
        "Handoff from Ann, task 12: I moved invoice emails to the new mail provider. The provider "
        "limits us to 500 emails per minute, so sending is now batched. Bounce handling still goes "
        "through the old service. Next person: check that bounced invoices are retried.",
    ),
    (
        "call notes",
        "Call with Marie on 3 March. She decided that refunds above 500 euros need her approval. "
        "Finance exports run every night at 02:00 and are kept for seven days. The customer portal "
        "was slow last week because of the report page.",
    ),
    (
        "migration note",
        "The database migration took 40 minutes on staging. It failed once because the disk was "
        "full, so we freed 20 GB first. The production window is Saturday night. Nobody has tested "
        "the rollback script yet.",
    ),
    (
        "review note",
        "Review of the login story: password resets expire after 30 minutes. Single sign-on is out "
        "of scope for this release. The team prefers PostgreSQL for everything.",
    ),
]

# (statement, the belief it should contradict or None)
STATEMENTS = [
    ("Finance exports no longer run at night: they now run every hour.", "exports"),
    ("Refunds of 800 euros are approved by the support team, without Marie.", "refunds"),
    ("Finance exports are also copied to the archive every Sunday.", None),
    ("The customer portal has a new colour theme.", None),
]

# (question, a word the right answer contains, or None if the memory does not answer it)
QUESTIONS = [
    ("How many emails per minute can the mail provider take?", "500"),
    ("Who has to approve refunds above 500 euros?", "Marie"),
    ("What colour is the company logo?", None),
]


async def call(client: Client, tool: str, **arguments: Any) -> tuple[dict[str, Any] | None, str]:
    """The answer of a tool, or None and the refusal."""
    result = await client.call_tool(tool, arguments)
    if result.is_error:
        text = " ".join(block.text for block in result.content if hasattr(block, "text"))
        return None, text.removeprefix(f"Error executing tool {tool}: ")
    assert result.structured_content is not None
    return dict(result.structured_content), ""


async def ok(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    answer, why = await call(client, tool, **arguments)
    if answer is None:
        raise SystemExit(f"{tool} was refused: {why}")
    return answer


def short(text: str, length: int = 100) -> str:
    text = " ".join(text.split())
    return text if len(text) <= length else text[: length - 1] + "…"


async def main(url: str) -> None:
    async with connect(url) as client:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        epic = await ok(
            client,
            "create_node",
            actor=ACTOR,
            kind="epic",
            title=f"Real model check {stamp}",
        )
        story = await ok(
            client,
            "create_node",
            actor=ACTOR,
            kind="story",
            title="Notes read by the model",
            parent_id=epic["id"],
        )
        print(f"Added epic {epic['id']} and story {story['id']}.")

        print("\n== extract_facts: each note, shown only")
        totals: Counter[str] = Counter()
        for name, text in NOTES:
            started = time.monotonic()
            answer, why = await call(client, "extract_facts", actor=ACTOR, text=text)
            seconds = time.monotonic() - started
            if answer is None:
                print(f"  {name}: refused: {why}")
                totals["refused"] += 1
                continue
            print(
                f"  {name}: {len(answer['proposals'])} facts kept, "
                f"{len(answer['dropped'])} dropped ({seconds:.1f} s)"
            )
            for proposal in answer["proposals"]:
                print(f"    + [{proposal['kind']}] {short(proposal['text'])}")
            for dropped in answer["dropped"]:
                print(f"    - {short(dropped['what'], 60)}: {dropped['why']}")
                totals[dropped["why"]] += 1
            totals["kept"] += len(answer["proposals"])
        print(f"  Totals: {dict(totals)}")

        print("\n== extract_facts with store, then consolidate_memory by item")
        for _name, text in NOTES[:2]:
            await ok(
                client,
                "extract_facts",
                actor=ACTOR,
                text=text,
                store=True,
                node_ids=[story["id"]],
            )
        answer, why = await call(client, "consolidate_memory", actor=ACTOR, node_id=story["id"])
        if answer is None:
            print(f"  refused: {why}")
        else:
            print(f"  {answer['message']}")
            for belief in answer["recorded"]:
                print(f"    + belief {belief['id']}: {short(belief['text'])}")
            for dropped in answer["dropped"]:
                print(f"    - {short(dropped['what'], 60)}: {dropped['why']}")

        print("\n== find_contradictions against two beliefs and some noise")
        facts = {
            "exports": await ok(
                client,
                "retain_fact",
                actor=ACTOR,
                text="Finance exports run every night at 02:00.",
                source="Call notes, 3 March",
                node_ids=[story["id"]],
            ),
            "refunds": await ok(
                client,
                "retain_fact",
                actor=ACTOR,
                text="Refunds above 500 euros need Marie's approval.",
                source="Call notes, 3 March",
                node_ids=[story["id"]],
            ),
        }
        beliefs = {
            name: await ok(
                client,
                "record_belief",
                actor=ACTOR,
                text=text,
                evidence_fact_ids=[facts[name]["id"]],
            )
            for name, text in (
                ("exports", "Finance exports happen once a day, at night."),
                ("refunds", "Large refunds are approved by Marie."),
            )
        }
        right = wrong = 0
        for statement, should in STATEMENTS:
            answer, why = await call(
                client, "find_contradictions", actor=ACTOR, statement=statement
            )
            if answer is None:
                print(f"  refused: {why}")
                continue
            flagged = {p["belief_id"] for p in answer["proposals"]}
            expected = {beliefs[should]["id"]} if should else set()
            # Only the two beliefs this check made count: others in the graph are noise.
            mine = {b["id"] for b in beliefs.values()}
            verdict = "right" if flagged & mine == expected else "WRONG"
            right, wrong = right + (verdict == "right"), wrong + (verdict == "WRONG")
            print(
                f"  {verdict}: {short(statement, 60)!r} flagged "
                f"{sorted(flagged & mine)} (expected {sorted(expected)}); "
                f"{len(answer['dropped'])} dropped"
            )
            for dropped in answer["dropped"]:
                print(f"    - {dropped['why']}")
        print(f"  {right} right, {wrong} wrong")

        print("\n== reflect")
        await ok(
            client,
            "record_decision",
            actor=ACTOR,
            title="Refund approval",
            text="Refunds above 500 euros need Marie's approval.",
            reason="Large refunds are risky.",
            decided_by=PERSON,
            node_ids=[story["id"]],
        )
        await ok(
            client,
            "retain_fact",
            actor=ACTOR,
            text="The mail provider limits sending to 500 emails per minute.",
            source="Handoff from Ann, task 12",
            node_ids=[story["id"]],
        )
        good = 0
        for question, needle in QUESTIONS:
            started = time.monotonic()
            answer, why = await call(
                client, "reflect", actor=ACTOR, question=question, node_id=story["id"]
            )
            seconds = time.monotonic() - started
            if answer is None:
                print(f"  refused: {short(question, 50)!r}: {short(why, 160)}")
                good += needle is None
                continue
            if needle is None:
                verdict = "right" if not answer["answered"] else "WRONG (it answered)"
            else:
                verdict = (
                    "right"
                    if answer["answered"] and needle in (answer["answer"] or "")
                    else "WRONG"
                )
            good += verdict == "right"
            cited = [f"{r['item_type']}:{r['id']}" for r in answer["relied_on"]]
            said = short(answer["answer"] or "(no answer)", 90)
            print(
                f"  {verdict}: {short(question, 50)!r} -> {said!r} cited {cited} ({seconds:.1f} s)"
            )
        print(f"  {good} of {len(QUESTIONS)} right")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: uv run python scripts/real_model_check.py http://127.0.0.1:8000/mcp"
        )
    asyncio.run(main(sys.argv[1]))
