"""M5C against the running containers: the four tools that need a language model (extract_facts,
consolidate_memory, find_contradictions, reflect), through a real MCP client (see ./check.sh).

The language model of the check stack is a scripted stand-in (tests/scripted_model.py): a test
tells it what to answer, and later asks what it was sent. So these tests prove what Vrum does with
a model's output (keeping what checks out, dropping what does not, never sending a secret), not
how good a model is. A second server with no language model set up is the `no_model_client`."""

import re
from typing import Any

import anyio
import pytest
from mcp import Client

from tests.helpers import (
    ScriptedModel,
    make_belief,
    make_decision,
    make_fact,
    make_stories,
    make_tasks,
    model_stopped,
    ok,
    refused,
    run_sql,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# Put together in pieces so that nothing in this file looks like a real secret.
SECRET = "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5"


def count(sql: str) -> int:
    result = run_sql(sql, plain=True)
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip().splitlines()[0])


def written() -> tuple[int, int, int]:
    """How many memories, decisions and events there are: what a refused call must not change."""
    return (
        count("SELECT count(*) FROM memories"),
        count("SELECT count(*) FROM decisions"),
        count("SELECT count(*) FROM events"),
    )


def ids(items: list[dict[str, Any]]) -> list[int]:
    return [item["id"] for item in items]


async def model_calls(client: Client, actor: str, wanted: int = 1) -> list[dict[str, Any]]:
    """The calls to the model that this actor made, from the event log. Waits a moment: the feed
    shows an event only once every earlier change has finished."""
    for _ in range(60):
        page = await ok(client, "changes_since", after_event_id=0, actor=actor, limit=200)
        calls = [event for event in page["items"] if event["action"] == "model_call"]
        if len(calls) >= wanted:
            return calls
        await anyio.sleep(0.1)
    pytest.fail(f"{actor} made fewer than {wanted} calls to the model, as far as the log says")


def legacy_fact(text: str) -> int:
    """A fact written straight into the database, as one stored before a secret check existed
    would be. The tools cannot make one with a secret in it."""
    result = run_sql(
        "INSERT INTO memories (type, kind, text, source, recorded_by) "
        f"VALUES ('fact', 'world', '{text}', 'An old note', 'sql') RETURNING id",
        plain=True,
    )
    assert result.returncode == 0, result.stderr
    return int(result.stdout.splitlines()[0])


def legacy_belief(text: str) -> int:
    """A belief written straight into the database, resting on a fact of its own."""
    result = run_sql(
        "WITH f AS (INSERT INTO memories (type, kind, text, source, recorded_by) "
        "VALUES ('fact', 'world', 'An old fact', 'An old note', 'sql') RETURNING id), "
        f"b AS (INSERT INTO memories (type, text, recorded_by) VALUES ('belief', '{text}', 'sql') "
        "RETURNING id), e AS (INSERT INTO belief_evidence (belief_id, fact_id) "
        "SELECT b.id, f.id FROM b, f RETURNING belief_id) SELECT belief_id FROM e",
        plain=True,
    )
    assert result.returncode == 0, result.stderr
    return int(result.stdout.splitlines()[0])


TOOL_CALLS: dict[str, dict[str, Any]] = {
    "extract_facts": {"text": "The export runs at night."},
    "consolidate_memory": {"query": "anything at all"},
    "find_contradictions": {"statement": "The export never runs."},
    "reflect": {"question": "When does the export run?"},
}

# -- no model set up -----------------------------------------------------------------------------


async def test_with_no_model_the_four_tools_say_so_and_every_other_tool_works(
    no_model_client: Client, client: Client
) -> None:
    for tool, arguments in TOOL_CALLS.items():
        message = await refused(no_model_client, tool, actor="ann", **arguments)
        assert f"No language model is set up on this server, so {tool} cannot run" in message
        assert "LLM_BASE_URL" in message and "Everything else works" in message
    assert "retain_fact" in await refused(no_model_client, "extract_facts", actor="a", text="x")
    assert "record_belief" in await refused(no_model_client, "consolidate_memory", actor="a")
    assert "list_memories" in await refused(no_model_client, "find_contradictions", actor="a")
    assert "recall and search" in await refused(no_model_client, "reflect", actor="a", question="?")

    # Everything else works, on the same database as the server that has a model.
    word = unique_word()
    status = await ok(no_model_client, "server_status")
    assert status["vrum_version"] and status["language_model"] is None
    assert (await ok(client, "server_status"))["language_model"] == "scripted-model"
    (story,) = await make_stories(no_model_client, 1)
    fact = await make_fact(no_model_client, f"The {word} service is slow", node_ids=[story["id"]])
    found = await ok(no_model_client, "search", query=word, mode="words")
    assert [(item["item_type"], item["id"]) for item in found["items"]] == [("fact", fact["id"])]
    recalled = await ok(no_model_client, "recall", node_id=story["id"])
    assert ids([entry["memory"] for entry in recalled["facts"]["items"]]) == [fact["id"]]
    assert (await ok(client, "get_memory", memory_id=fact["id"]))["text"] == fact["text"]
    tools = {tool.name for tool in (await no_model_client.list_tools()).tools}
    assert set(TOOL_CALLS) <= tools


# -- extract_facts -------------------------------------------------------------------------------


def a_text(marker: str) -> str:
    return (
        f"The nightly export {marker} runs at 02:00.\n"
        "It writes to the shared drive.\n"
        f"Marie asked on Monday that exports keep seven days {marker}."
    )


def scripted_facts(marker: str) -> dict[str, Any]:
    return {
        "facts": [
            {
                "text": f"The export {marker} runs at 02:00",
                "kind": "world",
                "passage": f"The nightly export {marker} runs at 02:00.",
            },
            {
                # Written on one line, though the text has a line break there.
                "text": "Exports are kept for seven days",
                "kind": "experience",
                "passage": f"It writes to the shared drive. Marie asked on Monday that exports "
                f"keep seven days {marker}.",
            },
            {
                "text": "Exports run twice a day",
                "kind": "world",
                "passage": f"it runs twice a day {marker}",
            },
            {
                "text": "Exports are slow",
                "kind": "opinion",
                "passage": "It writes to the shared drive.",
            },
            {"kind": "world", "passage": "It writes to the shared drive."},
            {
                "text": f"THE EXPORT {marker} RUNS AT 02:00",
                "kind": "world",
                "passage": "It writes to the shared drive.",
            },
            "just a string",
        ]
    }


async def test_extract_facts_returns_the_checked_facts_with_their_passages_and_stores_nothing(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    actor = f"ann-{marker}"
    text = a_text(marker)
    model.script(marker, scripted_facts(marker))
    before = written()

    result = await ok(client, "extract_facts", actor=actor, text=text)

    assert [(p["kind"], p["text"]) for p in result["proposals"]] == [
        ("world", f"The export {marker} runs at 02:00"),
        ("experience", "Exports are kept for seven days"),
    ]
    assert [p["passage"] for p in result["proposals"]][0] == (
        f"The nightly export {marker} runs at 02:00."
    )
    reasons = [d["why"] for d in result["dropped"]]
    assert len(reasons) == 5
    assert any("passage is not in the text" in why for why in reasons)
    assert any("kind is not world or experience" in why for why in reasons)
    assert any("has no text" in why for why in reasons)
    assert any("repeats an earlier proposal" in why for why in reasons)
    assert any("not a fact object" in why for why in reasons)
    assert result["stored"] is False and result["memories"] == []
    assert "Nothing was stored" in result["message"]
    assert (
        result["model"] == "scripted-model" and "Proposed by the language model" in result["label"]
    )
    # Nothing was written, except the log of the call.
    after = written()
    assert after[:2] == before[:2] and after[2] == before[2] + 1
    assert count(f"SELECT count(*) FROM memories WHERE source LIKE '%{marker}%'") == 0
    # The model was sent the text, once.
    (sent,) = model.received(marker)
    assert text in sent["user"] and "exact words" in sent["system"]
    # And the call is in the log: who asked, what was asked, which items were used.
    (call,) = await model_calls(client, actor)
    assert call["after"]["tool"] == "extract_facts" and call["after"]["model"] == "scripted-model"
    assert "Facts from a text of" in call["after"]["asked"] and marker in call["after"]["asked"]
    assert call["after"]["used"] == []
    assert call["after"]["result"] == {"proposed": 2, "dropped": 5, "stored_ids": []}
    assert call["node_id"] is None and call["memory_id"] is None


async def test_with_store_the_checked_facts_are_kept_as_extracted_with_the_passage_as_source(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    actor = f"ann-{marker}"
    (story,) = await make_stories(client, 1)
    model.script(marker, scripted_facts(marker))

    result = await ok(
        client,
        "extract_facts",
        actor=actor,
        text=a_text(marker),
        store=True,
        node_ids=[story["id"]],
    )

    stored = result["memories"]
    assert len(stored) == 2 and result["stored"] is True
    assert "2 stored as facts with origin extracted" in result["message"]
    for memory, proposal in zip(stored, result["proposals"], strict=True):
        assert (memory["type"], memory["origin"], memory["status"]) == (
            "fact",
            "extracted",
            "active",
        )
        assert memory["recorded_by"] == actor and memory["kind"] == proposal["kind"]
        assert memory["source"] == proposal["passage"] and memory["text"] == proposal["text"]
        assert memory["node_ids"] == [story["id"]]
        assert "proposed by a language model from a quoted passage" in memory["label"]
        assert await ok(client, "get_memory", memory_id=memory["id"]) == memory
    # They show in recall as extracted facts, and in the list of extracted ones.
    recalled = await ok(client, "recall", node_id=story["id"])
    assert ids([entry["memory"] for entry in recalled["facts"]["items"]]) == ids(stored)
    listed = await ok(client, "list_memories", origin="extracted", node_id=story["id"], limit=200)
    assert ids(listed["items"]) == ids(stored)
    (call,) = await model_calls(client, actor)
    assert call["after"]["result"]["stored_ids"] == ids(stored)
    # What was written and the call that wrote it are one change: the log reads in order.
    history = (await ok(client, "memory_history", memory_id=stored[0]["id"]))["items"]
    assert [event["action"] for event in history][:1] == ["memory_recorded"]
    assert history[0]["actor"] == actor and history[0]["after"]["origin"] == "extracted"


async def test_extract_facts_refuses_what_it_cannot_do_without_asking_the_model(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    before = written()

    assert "text cannot be empty" in await refused(client, "extract_facts", actor="a", text="  ")
    assert "only work with store set to true" in await refused(
        client, "extract_facts", actor="a", text=marker, node_ids=[1]
    )
    assert "no node with id 999999999" in await refused(
        client, "extract_facts", actor="a", text=marker, store=True, node_ids=[999999999]
    )
    assert "no decision with id 999999999" in await refused(
        client, "extract_facts", actor="a", text=marker, store=True, decision_ids=[999999999]
    )
    too_long = await refused(client, "extract_facts", actor="a", text=marker + "x" * 12000)
    assert "12,0" in too_long and "LLM_MAX_INPUT_CHARS" in too_long and "in parts" in too_long
    assert "Say who is making this change" in await refused(
        client, "extract_facts", actor=" ", text=marker
    )

    assert model.received(marker) == [] and written() == before


# -- consolidate_memory --------------------------------------------------------------------------


async def three_facts(client: Client, marker: str, **links: Any) -> list[dict[str, Any]]:
    return [
        await make_fact(client, f"{marker} fact {number}: the team uses tool {number}", **links)
        for number in range(1, 4)
    ]


def belief_reply(*beliefs: dict[str, Any]) -> dict[str, Any]:
    return {"beliefs": list(beliefs)}


async def test_consolidate_records_the_beliefs_whose_evidence_it_was_given_and_drops_the_rest(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    actor = f"ann-{marker}"
    one, two, three = await three_facts(client, marker)
    outsider = await make_fact(client, f"Something else, not given to the model {marker}x")
    model.script(
        marker,
        belief_reply(
            {
                "text": f"{marker}: the team is standardising",
                "evidence_fact_ids": [one["id"], two["id"]],
            },
            {
                "text": "Rests on a fact it was not given",
                "evidence_fact_ids": [one["id"], outsider["id"]],
            },
            {"text": "Rests on nothing", "evidence_fact_ids": []},
            {"text": "Rests on a made-up id", "evidence_fact_ids": [999999999]},
            {"text": "Rests on a word", "evidence_fact_ids": ["fact one"]},
            {"evidence_fact_ids": [one["id"]]},
            {
                "text": "Rests on a decision",
                "evidence_decision_ids": [1],
                "evidence_fact_ids": [one["id"]],
            },
            {
                "text": f"{marker}: strings are accepted as ids",
                "evidence_fact_ids": [str(three["id"])],
            },
        ),
    )

    result = await ok(
        client,
        "consolidate_memory",
        actor=actor,
        fact_ids=[one["id"], two["id"], three["id"]],
    )

    recorded = result["recorded"]
    assert [b["text"] for b in recorded] == [
        f"{marker}: the team is standardising",
        f"{marker}: strings are accepted as ids",
    ]
    first = recorded[0]
    assert (first["type"], first["origin"], first["status"]) == ("belief", "derived", "active")
    assert first["recorded_by"] == actor and first["stale"] is False
    assert [(e["type"], e["id"]) for e in first["evidence"]] == [
        ("fact", one["id"]),
        ("fact", two["id"]),
    ]
    assert first["evidence_count"] == 2 and first["confirmed_by"] is None
    assert "Suggestion (drawn up by a language model)" in first["label"]
    assert (result["facts_found"], result["facts_sent"], result["left_out"]) == (3, 3, 0)
    assert "2 beliefs recorded from 3 facts, origin derived" in result["message"]
    whys = [d["why"] for d in result["dropped"]]
    assert len(whys) == 6
    assert any(f"fact {outsider['id']}, which it was not given" in why for why in whys)
    assert any("names no evidence" in why for why in whys)
    assert any("fact 999999999, which it was not given" in why for why in whys)
    assert any("not a list of ids" in why for why in whys)
    assert any("has no text" in why for why in whys)
    assert any("decision 1, which it was not given" in why for why in whys)
    # Only what was given to the model is in what it was sent.
    (sent,) = model.received(marker)
    for fact in (one, two, three):
        assert f"[fact:{fact['id']}]" in sent["user"]
    assert f"[fact:{outsider['id']}]" not in sent["user"]
    # The beliefs are real beliefs: recall shows them as suggestions, resting on their facts.
    belief = await ok(client, "get_memory", memory_id=first["id"])
    assert belief == first
    (call,) = await model_calls(client, actor)
    assert call["after"]["tool"] == "consolidate_memory"
    assert call["after"]["used"] == [{"type": "fact", "id": f["id"]} for f in (one, two, three)]
    assert call["after"]["result"]["recorded_ids"] == ids(recorded)
    # Asking again with the same answer records nothing twice.
    again = await ok(
        client, "consolidate_memory", actor=actor, fact_ids=[one["id"], two["id"], three["id"]]
    )
    assert again["recorded"] == []
    assert sum("is already recorded as belief" in d["why"] for d in again["dropped"]) == 2


async def test_facts_can_be_chosen_by_item_by_search_or_by_ids_and_exactly_one_way(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    story, other = await make_stories(client, 2)
    (task,) = await make_tasks(client, 1)
    inside = await make_fact(client, f"{marker} in the story", node_ids=[story["id"]])
    elsewhere = await make_fact(client, f"{marker} in another story", node_ids=[other["id"]])
    searched = await make_fact(client, f"{marker} found by a search")
    model.script(marker, belief_reply())

    by_item = await ok(client, "consolidate_memory", actor="ann", node_id=story["id"])
    assert (by_item["facts_found"], by_item["facts_sent"]) == (1, 1)
    sent = model.received(marker)[-1]["user"]
    assert f"[fact:{inside['id']}]" in sent and f"[fact:{elsewhere['id']}]" not in sent

    by_search = await ok(client, "consolidate_memory", actor="ann", query=marker)
    assert by_search["facts_sent"] == 3
    sent = model.received(marker)[-1]["user"]
    assert all(f"[fact:{fact['id']}]" in sent for fact in (inside, elsewhere, searched))

    # Nothing to draw on: the model is not asked.
    asked = len(model.received(marker))
    empty = await ok(client, "consolidate_memory", actor="ann", node_id=task["id"])
    assert empty["facts_sent"] == 0 and "was not asked" in empty["message"]
    assert len(model.received(marker)) == asked

    for arguments, message in (
        ({}, "exactly one of fact_ids, node_id"),
        ({"node_id": story["id"], "query": marker}, "You gave 2"),
        ({"fact_ids": []}, "fact_ids is empty"),
        ({"fact_ids": [999999999]}, "no fact with id 999999999"),
        ({"node_id": 999999999}, "no node with id 999999999"),
        ({"query": marker, "max_facts": 0}, "max_facts must be a whole number from 1 to 200"),
        ({"query": marker, "said_by": "Marie", "quote": "yes"}, "only for revising"),
    ):
        assert message in await refused(client, "consolidate_memory", actor="ann", **arguments)
    belief_fact = await make_fact(client, f"{marker} a fact for a belief")
    belief = await make_belief(client, [belief_fact], f"{marker} a belief")
    assert "a belief, not a fact" in await refused(
        client, "consolidate_memory", actor="ann", fact_ids=[belief["id"]]
    )
    await ok(
        client,
        "retract_memory",
        actor="ann",
        memory_ids=[searched["id"]],
        reason="It was wrong",
    )
    assert "retracted" in await refused(
        client, "consolidate_memory", actor="ann", fact_ids=[searched["id"]]
    )


async def test_only_as_many_facts_as_asked_are_shown_and_the_rest_are_counted(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    facts = [await make_fact(client, f"{marker} fact {n}") for n in range(5)]
    model.script(marker, belief_reply())

    result = await ok(client, "consolidate_memory", actor="ann", fact_ids=ids(facts), max_facts=2)

    assert (result["facts_found"], result["facts_sent"], result["left_out"]) == (5, 2, 3)
    assert "3 facts were not shown to the model" in result["message"]
    (sent,) = model.received(marker)
    assert [f"[fact:{fact['id']}]" in sent["user"] for fact in facts] == [
        True,
        True,
        False,
        False,
        False,
    ]


async def revised_setup(
    client: Client, model: ScriptedModel, marker: str, actor: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    one, two, three = await three_facts(client, marker)
    model.script(
        marker,
        belief_reply(
            {"text": f"{marker}: conclusion", "evidence_fact_ids": [one["id"], two["id"]]}
        ),
    )
    result = await ok(client, "consolidate_memory", actor=actor, fact_ids=[one["id"], two["id"]])
    (belief,) = result["recorded"]
    return [one, two, three], belief


async def test_a_stale_belief_can_be_revised_and_the_revision_is_again_only_a_suggestion(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    actor = f"ann-{marker}"
    (one, two, _), belief = await revised_setup(client, model, marker, actor)
    assert "is not stale" in await refused(
        client, "consolidate_memory", actor=actor, revise_belief_id=belief["id"]
    )
    await ok(
        client,
        "retract_memory",
        actor="bob",
        memory_ids=[one["id"]],
        reason="It was a typo",
    )
    assert (await ok(client, "get_memory", memory_id=belief["id"]))["stale"] is True

    # First the model names the fact that was retracted, which it was not given: nothing changes.
    model.script(
        marker,
        belief_reply({"text": f"{marker}: bad revision", "evidence_fact_ids": [one["id"]]}),
    )
    unchanged = await ok(client, "consolidate_memory", actor=actor, revise_belief_id=belief["id"])
    assert unchanged["recorded"] == [] and unchanged["replaced_id"] is None
    assert f"fact {one['id']}, which it was not given" in unchanged["dropped"][0]["why"]
    assert "unchanged and still stale" in unchanged["message"]
    assert (await ok(client, "get_memory", memory_id=belief["id"]))["status"] == "active"

    model.script(
        marker,
        belief_reply(
            {"text": f"{marker}: revised conclusion", "evidence_fact_ids": [two["id"]]},
            {"text": "A second one is not taken", "evidence_fact_ids": [two["id"]]},
        ),
    )
    revised = await ok(client, "consolidate_memory", actor=actor, revise_belief_id=belief["id"])

    (new,) = revised["recorded"]
    assert revised["replaced_id"] == belief["id"]
    assert f"Belief {belief['id']} was replaced by belief {new['id']}" in revised["message"]
    assert (new["origin"], new["status"], new["stale"]) == ("derived", "active", False)
    assert [(e["type"], e["id"]) for e in new["evidence"]] == [("fact", two["id"])]
    assert new["supersedes_ids"] == [belief["id"]]
    assert "Suggestion (drawn up by a language model)" in new["label"]
    old = await ok(client, "get_memory", memory_id=belief["id"])
    assert (old["status"], old["superseded_by_id"]) == ("superseded", new["id"])
    assert (
        "Revised by the language model scripted-model because its evidence changed"
        in (old["status_reason"])
    )
    assert f"fact {one['id']} was retracted: It was a typo" in old["status_reason"]
    assert any("only one revised belief is taken" in d["why"] for d in revised["dropped"])
    # The model was told what changed, and shown only the evidence that is still valid.
    sent = model.received(marker)[-1]["user"]
    assert f"fact {one['id']} was retracted: It was a typo" in sent
    assert f"[fact:{two['id']}]" in sent and f"[fact:{one['id']}]" not in sent
    assert "went stale" in sent
    # Now it is no longer stale, so it cannot be revised again; and the old one is not the one.
    assert "was superseded and replaced by belief" in await refused(
        client, "consolidate_memory", actor=actor, revise_belief_id=belief["id"]
    )
    assert "is not stale" in await refused(
        client, "consolidate_memory", actor=actor, revise_belief_id=new["id"]
    )


async def test_a_belief_whose_evidence_is_all_gone_is_not_revised_by_guessing(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    (one, two, _), belief = await revised_setup(client, model, marker, "ann")
    await ok(
        client, "retract_memory", actor="bob", memory_ids=[one["id"], two["id"]], reason="Wrong"
    )
    asked = len(model.received(marker))

    result = await ok(client, "consolidate_memory", actor="ann", revise_belief_id=belief["id"])

    assert result["recorded"] == [] and "None of the evidence" in result["message"]
    assert "retract_memory" in result["message"]
    assert len(model.received(marker)) == asked
    assert "is a fact, not a belief" in await refused(
        client, "consolidate_memory", actor="ann", revise_belief_id=one["id"]
    )


async def test_a_belief_a_person_confirmed_is_only_revised_with_that_persons_word(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    (one, two, _), belief = await revised_setup(client, model, marker, "ann")
    await ok(client, "confirm_belief", actor="ann", memory_id=belief["id"], confirmed_by="Marie")
    await ok(client, "retract_memory", actor="bob", memory_ids=[one["id"]], reason="A typo")
    model.script(
        marker,
        belief_reply({"text": f"{marker}: revised", "evidence_fact_ids": [two["id"]]}),
    )
    asked = len(model.received(marker))

    message = await refused(
        client, "consolidate_memory", actor="ann", revise_belief_id=belief["id"]
    )

    assert "confirmed by Marie" in message and "said_by" in message and "quote" in message
    assert len(model.received(marker)) == asked
    assert (await ok(client, "get_memory", memory_id=belief["id"]))["status"] == "active"

    revised = await ok(
        client,
        "consolidate_memory",
        actor="ann",
        revise_belief_id=belief["id"],
        said_by="Marie",
        quote="Yes, replace it, the typo changes things",
    )

    (new,) = revised["recorded"]
    assert new["confirmed_by"] is None and new["origin"] == "derived"
    history = (await ok(client, "memory_history", memory_id=belief["id"]))["items"]
    superseded = [event for event in history if event["action"] == "memory_superseded"]
    assert superseded[0]["after"]["said_by"] == "Marie"
    assert superseded[0]["after"]["quote"].startswith("Yes, replace it")


async def test_a_belief_resting_on_a_replaced_decision_is_revised_from_its_replacement(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    fact = await make_fact(client, f"{marker} the team is small")
    decision = await make_decision(client, f"Keep it simple {marker}")
    belief = await ok(
        client,
        "record_belief",
        actor="ann",
        text=f"{marker}: simple wins",
        evidence_fact_ids=[fact["id"]],
        evidence_decision_ids=[decision["id"]],
    )
    replacement = await ok(
        client,
        "supersede_decision",
        actor="ann",
        decision_id=decision["id"],
        title=f"Keep it simple, still {marker}",
        text="Still simple",
        reason="Reconsidered",
        decided_by="Marie",
    )
    assert (await ok(client, "get_memory", memory_id=belief["id"]))["stale"] is True
    model.script(
        marker,
        belief_reply(
            {
                "text": f"{marker}: simple still wins",
                "evidence_fact_ids": [fact["id"]],
                "evidence_decision_ids": [replacement["id"]],
            }
        ),
    )

    revised = await ok(client, "consolidate_memory", actor="ann", revise_belief_id=belief["id"])

    (new,) = revised["recorded"]
    assert [(e["type"], e["id"]) for e in new["evidence"]] == [
        ("fact", fact["id"]),
        ("decision", replacement["id"]),
    ]
    sent = model.received(marker)[-1]["user"]
    assert f"decision {decision['id']} was replaced by decision {replacement['id']}" in sent
    assert f"[decision:{replacement['id']}]" in sent


# -- find_contradictions -------------------------------------------------------------------------


def contradiction(belief: dict[str, Any], marker: str, **changes: Any) -> dict[str, Any]:
    return {
        "belief_id": belief["id"],
        "reason": "They cannot both be true.",
        "belief_quote": marker,
        "statement_quote": marker,
        **changes,
    }


async def beliefs_for(client: Client, marker: str, count_: int) -> list[dict[str, Any]]:
    fact = await make_fact(client, f"{marker} the evidence")
    return [
        await make_belief(client, [fact], f"{marker} {unique_word()} {unique_word()}")
        for _ in range(count_)
    ]


async def test_contradictions_are_proposals_with_checked_quotes_and_change_nothing(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    actor = f"ann-{marker}"
    one, two, three = await beliefs_for(client, marker, 3)
    elsewhere = await make_belief(
        client, [await make_fact(client, "x")], f"{unique_word()} {unique_word()}"
    )
    statement = f"{marker} {unique_word()}"
    model.script(
        marker,
        {
            "contradictions": [
                contradiction(one, marker, reason="  It is the opposite. "),
                contradiction(two, marker, belief_quote="words that are not in the belief"),
                contradiction(three, marker, statement_quote="words that are not in the statement"),
                contradiction(elsewhere, marker),
                contradiction(one, marker),
                {**contradiction(two, marker), "reason": ""},
                {**contradiction(three, marker), "belief_id": "three"},
                "not an object",
            ]
        },
    )
    active = count("SELECT count(*) FROM memory_state WHERE type = 'belief' AND status = 'active'")
    before = written()

    result = await ok(client, "find_contradictions", actor=actor, statement=statement)

    (proposal,) = result["proposals"]
    assert proposal["belief_id"] == one["id"] and proposal["belief_text"] == one["text"]
    assert proposal["reason"] == "It is the opposite."
    assert proposal["belief_quote"] == marker and proposal["statement_quote"] == marker
    assert proposal["confirmed_by"] is None and proposal["stale"] is False
    whys = [d["why"] for d in result["dropped"]]
    assert len(whys) == 7
    assert any("quote of the belief is not in the belief" in why for why in whys)
    assert any("quote of the statement is not in the statement" in why for why in whys)
    assert any("not one of the beliefs that were shown" in why for why in whys)
    assert any("named more than once" in why for why in whys)
    assert any("gives no reason" in why for why in whys)
    assert any("not an object" in why for why in whys)
    # It says how many it looked at out of how many there are: the three that may clash.
    assert (result["scope"], result["shown"], result["looked_at"]) == ("related", 3, 3)
    assert (result["related_found"], result["remaining"]) == (3, 0)
    assert result["active_beliefs"] == active and result["next_after_id"] is None
    assert f"Looked at 3 of {active} active beliefs" in result["message"]
    assert "proposals: nothing was changed" in result["message"]
    assert result["statement"] == statement and "Nothing was changed" in result["label"]
    # It changed nothing except the log of the call.
    after = written()
    assert after[:2] == before[:2] and after[2] == before[2] + 1
    for belief in (one, two, three):
        assert (await ok(client, "get_memory", memory_id=belief["id"]))["status"] == "active"
    # The beliefs shown were the three that match, and not the one that is unrelated.
    (sent,) = model.received(marker)
    assert all(f"[belief:{b['id']}]" in sent["user"] for b in (one, two, three))
    assert f"[belief:{elsewhere['id']}]" not in sent["user"] and statement in sent["user"]
    (call,) = await model_calls(client, actor)
    assert call["after"]["tool"] == "find_contradictions"
    # In the order they were found, which is how alike they are: not the order they were made in.
    used = sorted(call["after"]["used"], key=lambda item: item["id"])
    assert used == [{"type": "belief", "id": b["id"]} for b in (one, two, three)]
    assert call["after"]["result"]["proposed"] == [one["id"]]
    # Applying a proposal is a call of the person's or agent's own: here, a replacement.
    replaced = await ok(
        client,
        "supersede_memory",
        actor=actor,
        replaces=[proposal["belief_id"]],
        reason=f"Contradicted by: {statement}",
        text=f"{marker} the new belief",
        evidence_fact_ids=[one["evidence"][0]["id"]],
    )
    assert replaced["superseded_ids"] == [one["id"]]


async def test_a_belief_a_person_confirmed_says_so_and_needs_their_word_to_change(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    (belief,) = await beliefs_for(client, marker, 1)
    await ok(client, "confirm_belief", actor="ann", memory_id=belief["id"], confirmed_by="Marie")
    model.script(marker, {"contradictions": [contradiction(belief, marker)]})

    result = await ok(
        client, "find_contradictions", actor="ann", statement=f"{marker} {unique_word()}"
    )

    (proposal,) = result["proposals"]
    assert proposal["confirmed_by"] == "Marie"
    assert "said_by and quote" in result["message"]
    assert "confirmed by Marie" in await refused(
        client,
        "supersede_memory",
        actor="ann",
        replaces=[proposal["belief_id"]],
        reason="Contradicted",
        text=f"{marker} replacement",
        evidence_fact_ids=[belief["evidence"][0]["id"]],
    )
    replaced = await ok(
        client,
        "supersede_memory",
        actor="ann",
        replaces=[proposal["belief_id"]],
        reason="Contradicted",
        text=f"{marker} replacement",
        evidence_fact_ids=[belief["evidence"][0]["id"]],
        said_by="Marie",
        quote="That is no longer true",
    )
    assert replaced["superseded_ids"] == [belief["id"]]


async def test_a_recorded_item_can_be_the_statement_and_is_never_checked_against_itself(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    one, two = await beliefs_for(client, marker, 2)
    model.script(
        marker, {"contradictions": [contradiction(one, marker), contradiction(two, marker)]}
    )

    result = await ok(client, "find_contradictions", actor="ann", memory_id=one["id"])

    assert result["statement"] == one["text"]
    assert [p["belief_id"] for p in result["proposals"]] == [two["id"]]
    assert any("not one of the beliefs that were shown" in d["why"] for d in result["dropped"])
    (sent,) = model.received(marker)
    assert f"[belief:{two['id']}]" in sent["user"] and f"[belief:{one['id']}]" not in sent["user"]
    # A fact works too: the beliefs resting on it are the ones that may clash.
    fact = await make_fact(client, f"{marker} a new fact {unique_word()}")
    beliefs = await beliefs_for(client, unique_word(), 1)
    new = await ok(client, "find_contradictions", actor="ann", memory_id=fact["id"])
    assert new["shown"] >= 1 or new["related_found"] == 0
    # The statement has to be active, and exactly one of the two ways has to be given.
    await ok(client, "retract_memory", actor="ann", memory_ids=[fact["id"]], reason="Wrong")
    assert "is retracted" in await refused(
        client, "find_contradictions", actor="ann", memory_id=fact["id"]
    )
    assert "exactly one of statement" in await refused(client, "find_contradictions", actor="ann")
    assert "exactly one of statement" in await refused(
        client, "find_contradictions", actor="ann", statement="x", memory_id=one["id"]
    )
    assert beliefs


async def test_the_beliefs_that_may_clash_are_found_by_item_by_shared_evidence_and_by_words(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    (story,) = await make_stories(client, 1)
    shared = await make_fact(client, f"{marker} a shared fact", node_ids=[story["id"]])
    on_item = await make_belief(
        client, [await make_fact(client, "unrelated")], f"{unique_word()} a"
    )
    await ok(client, "link_memory", actor="ann", memory_id=on_item["id"], node_ids=[story["id"]])
    shares = await make_belief(client, [shared], f"{unique_word()} b")
    by_words = await make_belief(client, [await make_fact(client, "unrelated")], f"{marker} c")
    unrelated = await make_belief(
        client, [await make_fact(client, "unrelated")], f"{unique_word()} d"
    )
    statement_fact = await make_fact(client, f"{marker} the new fact", node_ids=[story["id"]])
    model.script(marker, {"contradictions": []})

    result = await ok(client, "find_contradictions", actor="ann", memory_id=statement_fact["id"])

    (sent,) = model.received(marker)
    shown = {int(i) for i in re.findall(r"\[belief:(\d+)\]", sent["user"])}
    assert {on_item["id"], shares["id"], by_words["id"]} <= shown
    assert unrelated["id"] not in shown
    assert result["related_found"] == len(shown) and result["shown"] == len(shown)
    assert result["proposals"] == [] and "No contradiction found" in result["message"]


async def test_scope_related_shows_only_a_batch_and_says_how_many_were_left(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    await beliefs_for(client, marker, 6)
    model.script(marker, {"contradictions": []})

    result = await ok(client, "find_contradictions", actor="ann", statement=f"{marker} x", batch=4)

    assert (result["shown"], result["related_found"], result["remaining"]) == (4, 6, 2)
    assert "2 more related ones were not shown" in result["message"]
    assert "after_id only works with scope all" in await refused(
        client, "find_contradictions", actor="ann", statement="x", after_id=1
    )
    assert "batch must be a whole number from 1 to 200" in await refused(
        client, "find_contradictions", actor="ann", statement="x", batch=0
    )
    assert "scope must be one of" in await refused(
        client, "find_contradictions", actor="ann", statement="x", scope="some"
    )


async def test_scope_all_goes_through_every_belief_in_batches_and_reaches_a_late_one(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    actor = f"ann-{marker}"
    base = count("SELECT coalesce(max(id), 0) FROM memories")
    passed = count(
        f"SELECT count(*) FROM memory_state WHERE type = 'belief' AND status = 'active' "
        f"AND id <= {base}"
    )
    mine = await beliefs_for(client, marker, 45)
    statement = f"{marker} {unique_word()}"
    # The model finds a contradiction with the last of the 45, which is in the fifth batch.
    model.script(marker, {"contradictions": [contradiction(mine[-1], marker)]})

    seen: list[dict[str, Any]] = []
    after_id = base
    late: dict[str, Any] | None = None
    for call in range(1, 6):
        result = await ok(
            client,
            "find_contradictions",
            actor=actor,
            statement=statement,
            scope="all",
            batch=10,
            after_id=after_id,
        )
        seen.append(result)
        if call == 4:
            # A belief recorded between two calls has a higher id and is reached by the last call.
            late = await make_belief(
                client, [await make_fact(client, f"{marker} late")], f"{marker} {unique_word()}"
            )
        after_id = result["next_after_id"]
        if after_id is None:
            break

    assert len(seen) == 5
    assert [r["shown"] for r in seen] == [10, 10, 10, 10, 6]
    # Each count is what the call saw: the late belief was recorded after the fourth call.
    assert [r["remaining"] for r in seen[:4]] == [35, 25, 15, 5]
    assert seen[-1]["remaining"] == 0 and seen[-1]["next_after_id"] is None
    assert [r["looked_at"] for r in seen[:4]] == [
        passed + 10,
        passed + 20,
        passed + 30,
        passed + 40,
    ]
    assert all(r["looked_at"] + r["remaining"] == r["active_beliefs"] for r in seen)
    assert seen[-1]["looked_at"] == seen[-1]["active_beliefs"]
    assert (
        "none remain" in seen[-1]["message"]
        and "call again with next_after_id" in seen[0]["message"]
    )
    # The scripted contradiction is in the last batch, and only there.
    assert [len(r["proposals"]) for r in seen] == [0, 0, 0, 0, 1]
    assert seen[-1]["proposals"][0]["belief_id"] == mine[-1]["id"]
    assert all(
        any("not one of the beliefs that were shown" in d["why"] for d in r["dropped"])
        for r in seen[:4]
    )
    # The model was shown every one of the 46 beliefs exactly once.
    assert late is not None
    shown_ids = [
        int(shown)
        for request in model.received(marker)
        for shown in re.findall(r"\[belief:(\d+)\]", request["user"])
    ]
    assert sorted(shown_ids) == sorted(ids(mine) + [late["id"]])
    assert len(model.received(marker)) == 5
    assert len(await model_calls(client, actor, wanted=5)) == 5
    # Passing the end gives nothing and asks nobody.
    over = await ok(
        client,
        "find_contradictions",
        actor=actor,
        statement=statement,
        scope="all",
        after_id=count("SELECT max(id) FROM memories"),
    )
    assert over["shown"] == 0 and over["remaining"] == 0
    assert len(model.received(marker)) == 5


# -- reflect -------------------------------------------------------------------------------------


async def reflect_setup(client: Client, marker: str) -> dict[str, Any]:
    (story,) = await make_stories(client, 1)
    decision = await make_decision(
        client, f"Use PDF {marker}", text=f"Invoices {marker} are PDFs", node_ids=[story["id"]]
    )
    old = await make_decision(client, f"The old way {marker}", text=f"Text {marker}")
    new = await ok(
        client,
        "supersede_decision",
        actor="ann",
        decision_id=old["id"],
        title=f"The new way {marker}",
        text=f"HTML {marker}",
        reason="Changed",
        decided_by="Marie",
    )
    principle = await make_decision(client, f"Always log {marker}", kind="principle")
    fact = await make_fact(client, f"The {marker} library is at version two")
    belief = await make_belief(client, [fact], f"{marker} version two is probably stable")
    gone = await make_fact(client, f"{marker} a fact that was wrong")
    stale = await make_belief(client, [gone], f"{marker} a stale conclusion")
    await ok(client, "retract_memory", actor="ann", memory_ids=[gone["id"]], reason="Wrong")
    return {
        "story": story,
        "decision": decision,
        "old": old,
        "new": new,
        "principle": principle,
        "fact": fact,
        "belief": belief,
        "stale": stale,
    }


async def test_reflect_answers_from_what_was_found_and_lists_what_it_relied_on(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    actor = f"ann-{marker}"
    items = await reflect_setup(client, marker)
    d, p, f, b = (items[name] for name in ("decision", "principle", "fact", "belief"))
    question = f"{marker} {unique_word()}"
    model.script(
        marker,
        {
            "answer": f"{marker} invoices are PDFs [decision:{d['id']}], on library two.",
            "cited": [f"decision:{d['id']}", f"fact:{f['id']}"],
        },
    )
    before = written()

    result = await ok(client, "reflect", actor=actor, question=question)

    assert result["answered"] is True and result["question"] == question
    assert result["answer"] == f"{marker} invoices are PDFs [decision:{d['id']}], on library two."
    assert [(r["item_type"], r["id"]) for r in result["relied_on"]] == [
        ("decision", d["id"]),
        ("fact", f["id"]),
    ]
    decision_entry, fact_entry = result["relied_on"]
    assert decision_entry["suggestion"] is False and "Marie" in decision_entry["label"]
    assert fact_entry["suggestion"] is False and fact_entry["status"] == "active"
    assert "Generated by the language model scripted-model" in result["label"]
    assert "not a record, it was not stored" in result["label"]
    assert result["retrieved"] == 5 and result["left_out"] == 0 and result["skipped_secret"] == 0
    assert "from 2 of the 5 items" in result["message"]
    # What was retrieved: the two active decisions (the first, and the one that replaced another),
    # the principle, the fact and the belief. Not the decision that was replaced, and not the
    # belief whose evidence is gone.
    (sent,) = model.received(marker)
    prompt = sent["user"]
    assert f"[decision:{d['id']}]" in prompt and f"[principle:{p['id']}]" in prompt
    assert f"[decision:{items['new']['id']}]" in prompt
    assert f"[fact:{f['id']}]" in prompt and f"[belief:{b['id']}]" in prompt
    assert f"[decision:{items['old']['id']}]" not in prompt
    assert f"[belief:{items['stale']['id']}]" not in prompt
    assert "authoritative" in sent["system"] and "decided by Marie" in prompt
    assert "Belief (a suggestion, not settled)" in prompt
    # Nothing was stored but the log of the call.
    after = written()
    assert after[:2] == before[:2] and after[2] == before[2] + 1
    (call,) = await model_calls(client, actor)
    assert call["after"]["tool"] == "reflect" and call["after"]["asked"] == question
    assert {(u["type"], u["id"]) for u in call["after"]["used"]} == {
        ("decision", d["id"]),
        ("decision", items["new"]["id"]),
        ("principle", p["id"]),
        ("fact", f["id"]),
        ("belief", b["id"]),
    }
    assert call["after"]["result"]["cited"] == [f"decision:{d['id']}", f"fact:{f['id']}"]


async def test_an_answer_that_cites_anything_that_was_not_retrieved_is_refused(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    actor = f"ann-{marker}"
    items = await reflect_setup(client, marker)
    d, f = items["decision"], items["fact"]
    question = f"{marker} {unique_word()}"
    before = written()

    bad_answers: list[tuple[dict[str, Any] | str, str]] = [
        ({"answer": "Yes.", "cited": [f"decision:{d['id']}", "fact:999999999"]}, "fact:999999999"),
        ({"answer": "Yes [belief:999999999].", "cited": [f"fact:{f['id']}"]}, "belief:999999999"),
        # The replaced decision exists, but it was not retrieved: it is not in force.
        ({"answer": "Yes.", "cited": [f"decision:{items['old']['id']}"]}, "was not one of the"),
        ({"answer": "Yes.", "cited": []}, "cites nothing"),
        ({"answer": "Yes."}, "cites nothing"),
        ({"answer": 42, "cited": []}, "was not text"),
        ({"answer": "Yes.", "cited": "fact:1"}, "not a list of keys"),
    ]
    for reply, expected in bad_answers:
        model.script(marker, reply)
        message = await refused(client, "reflect", actor=actor, question=question)
        assert expected in message, (reply, message)
        assert "refused and nothing was stored" in message
    assert written()[:2] == before[:2]
    # The calls were made, so they are in the log, with why they were refused.
    calls = await model_calls(client, actor, wanted=len(bad_answers))
    assert all("refused" in call["after"]["result"] for call in calls)

    # An answer of null says the memory does not answer: that is an answer too, not a refusal.
    model.script(marker, {"answer": None, "cited": []})
    none = await ok(client, "reflect", actor=actor, question=question)
    assert none["answered"] is False and none["answer"] is None and none["relied_on"] == []
    assert "do not answer the question" in none["message"]
    model.script(marker, {"answer": "  ", "cited": []})
    assert (await ok(client, "reflect", actor=actor, question=question))["answered"] is False


async def test_what_is_linked_to_the_item_asked_about_is_used_even_when_no_word_matches(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    (story,) = await make_stories(client, 1)
    linked = await make_fact(client, f"{unique_word()} only linked", node_ids=[story["id"]])
    elsewhere = await make_fact(client, f"{unique_word()} not linked")
    question = f"{marker} {unique_word()}"
    model.script(marker, {"answer": "Linked.", "cited": [f"fact:{linked['id']}"]})

    nothing = await ok(client, "reflect", actor="ann", question=question)
    assert nothing["retrieved"] == 0 and "was not asked" in nothing["message"]
    assert model.received(marker) == []

    result = await ok(client, "reflect", actor="ann", question=question, node_id=story["id"])

    assert result["answered"] is True and result["retrieved"] >= 1
    (sent,) = model.received(marker)
    assert (
        f"[fact:{linked['id']}]" in sent["user"] and f"[fact:{elsewhere['id']}]" not in sent["user"]
    )
    assert "no node with id 999999999" in await refused(
        client, "reflect", actor="ann", question=question, node_id=999999999
    )
    assert "at most one of node_id and rule_id" in await refused(
        client, "reflect", actor="ann", question=question, node_id=story["id"], rule_id=1
    )
    assert "question cannot be empty" in await refused(client, "reflect", actor="ann", question=" ")


async def test_reflect_shows_the_model_only_as_many_items_as_asked(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    facts = [await make_fact(client, f"{marker} fact {n}") for n in range(6)]
    model.script(marker, {"answer": "Some.", "cited": [f"fact:{facts[0]['id']}"]})

    result = await ok(client, "reflect", actor="ann", question=marker, limit=3)

    assert result["retrieved"] == 3
    (sent,) = model.received(marker)
    assert len(re.findall(r"\[fact:\d+\]", sent["user"])) == 3


# -- care: secrets, a model that is gone, a model that answers badly -----------------------------


async def test_text_with_a_secret_in_a_well_known_format_is_never_sent_to_the_model(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    for tool, arguments in (
        ("extract_facts", {"text": f"{marker} the deploy key is {SECRET}"}),
        ("find_contradictions", {"statement": f"{marker} {SECRET}"}),
        ("reflect", {"question": f"{marker} what is {SECRET}?"}),
    ):
        model.script(marker, {"facts": [], "contradictions": [], "answer": None})
        before = written()
        message = await refused(client, tool, actor="ann", **arguments)
        assert "contains what looks like a GitHub token" in message
        assert "was not sent to the language model" in message and "nothing was changed" in message
        assert SECRET not in message
        assert written() == before
    assert model.received(marker) == []
    # Stored items that hold one are left out, counted, and never sent either.
    clean = await make_fact(client, f"{marker} a clean fact")
    leaky = legacy_fact(f"{marker} the key is {SECRET}")
    model.script(marker, belief_reply())
    result = await ok(client, "consolidate_memory", actor="ann", fact_ids=[clean["id"], leaky])
    assert (result["facts_found"], result["facts_sent"], result["left_out"]) == (2, 1, 1)
    assert "1 contain a secret and were never sent" in result["message"]
    only = await ok(client, "consolidate_memory", actor="ann", fact_ids=[leaky])
    assert only["facts_sent"] == 0 and "was not asked" in only["message"]
    model.script(marker, {"answer": "A clean fact.", "cited": [f"fact:{clean['id']}"]})
    answer = await ok(client, "reflect", actor="ann", question=marker)
    assert answer["skipped_secret"] == 1 and answer["retrieved"] == 1
    model.script(marker, {"contradictions": []})
    leaky_belief = legacy_belief(f"{marker} {SECRET}")
    swept = await ok(
        client,
        "find_contradictions",
        actor="ann",
        statement=f"{marker} x",
        scope="all",
        batch=1,
        after_id=leaky_belief - 1,
    )
    assert swept["skipped_secret"] == 1 and swept["shown"] == 0
    assert all(SECRET not in request["text"] for request in model.received(marker))
    assert model.received(marker)


async def test_with_the_model_gone_each_tool_says_so_and_nothing_is_written(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    fact = await make_fact(client, f"{marker} a fact")
    belief = await make_belief(client, [fact], f"{marker} a belief")
    calls: dict[str, dict[str, Any]] = {
        "consolidate_memory": {"fact_ids": [fact["id"]]},
        "find_contradictions": {"statement": f"{marker} x"},
        "reflect": {"question": marker},
    }
    assert belief
    before = written()

    with model_stopped():
        for tool, arguments in calls.items():
            message = await refused(client, tool, actor="ann", **arguments)
            assert (
                "The language model at http://scripted-model:8080/v1 cannot be reached" in message
            )
            assert "Nothing was changed" in message
        assert written() == before
        # Everything else works while the model is gone.
        assert (
            await ok(client, "recall", node_id=(await make_stories(client, 1))[0]["id"])
        ) is not None
        assert (await ok(client, "search", query=marker, mode="words"))["total"] >= 2

    # And back again, it works.
    model.script(marker, {"facts": []})
    again = await ok(
        client, "extract_facts", actor="ann", text=f"{marker} the export runs at night."
    )
    assert again["proposals"] == []


async def test_a_model_that_answers_badly_changes_nothing(
    client: Client, model: ScriptedModel
) -> None:
    marker = unique_word()
    fact = await make_fact(client, f"{marker} a fact")
    arguments = {"actor": "ann", "fact_ids": [fact["id"]]}
    before = written()

    model.script(marker, "Sure! Here are your beliefs: there are none.")
    assert "did not answer with JSON" in await refused(client, "consolidate_memory", **arguments)
    model.script(marker, "[1, 2, 3]")
    assert "JSON that is not an object" in await refused(client, "consolidate_memory", **arguments)
    model.script(marker, {"beliefs": "none"})
    assert 'has "beliefs" that is not a list' in await refused(
        client, "consolidate_memory", **arguments
    )
    model.script(marker, {}, status=503)
    assert "answered 503" in await refused(client, "consolidate_memory", **arguments)
    assert written() == before
    # A code fence around the JSON is forgiven, and an empty object is no beliefs, not an error.
    model.script(marker, '```json\n{"beliefs": []}\n```')
    assert (await ok(client, "consolidate_memory", **arguments))["recorded"] == []
    model.script(marker, {})
    assert (await ok(client, "consolidate_memory", **arguments))["recorded"] == []


# -- the database keeps the log of model calls honest on its own ---------------------------------


async def test_the_database_refuses_a_model_call_event_that_names_an_item_or_asks_nothing(
    client: Client,
) -> None:
    (task,) = await make_tasks(client, 1)
    before = count("SELECT count(*) FROM events")

    named = run_sql(
        "INSERT INTO events (node_id, action, actor, after) VALUES "
        f'({task["id"]}, \'model_call\', \'sql\', \'{{"tool": "reflect", "asked": "Why?"}}\')'
    )
    assert named.returncode != 0 and "event_names_what_it_is_about" in named.stderr
    for after in ('{"tool": "reflect", "asked": "  "}', '{"tool": "reflect"}', '{"asked": "Why?"}'):
        blank = run_sql(
            f"INSERT INTO events (action, actor, after) VALUES ('model_call', 'sql', '{after}')"
        )
        assert blank.returncode != 0 and "model_call_says_what_was_asked" in blank.stderr
    assert count("SELECT count(*) FROM events") == before
    # The origin of a memory has to fit its type, whatever the tools do.
    bad = run_sql(
        "INSERT INTO memories (type, kind, text, source, origin, recorded_by) "
        "VALUES ('fact', 'world', 'x', 'y', 'derived', 'sql')"
    )
    assert bad.returncode != 0 and "memory_origin_matches_its_type" in bad.stderr
