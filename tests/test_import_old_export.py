"""M27 and M30 against the running containers: an export made before decisions and facts became
notes (format version 2, or version 1), before a note had one status (version 3), or before a rule
could be on a task (version 4), is still loaded, and what it held is notes and acceptance criteria
that mean what they meant. The file is built by
hand, as the servers before wrote it (see tests/documents.py), and loaded into an empty project
through the real `import_graph`."""

from typing import Any

import pytest
from mcp import Client

from tests.documents import (
    a_legacy_file,
    a_version_3_file,
    a_version_4_file,
    a_version_5_file,
    as_version_1,
    decision,
    definition,
    event,
    fact,
    legacy_event,
    link,
    node,
    note,
    rule,
)
from tests.helpers import connect, export_everything, ok, refused
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

LATER = "2026-11-16T12:30:00.000000Z"


def the_old_file() -> dict[str, Any]:
    """Four decisions then four facts, with every kind of link, ending and confirmation, and the
    events that recorded them."""
    nodes = [node("epic", 1), node("story", 1, "epic:1"), node("task", 1, "story:1")]
    decisions = [
        decision(
            1,
            title="Use email",
            text="Use email",
            reason="Everybody has it",
            nodes=["story:1"],
            rule_ids=[1],
            confirmed_by="Priya",
            confirmed_at=LATER,
        ),
        decision(
            2,
            title="Use codes",
            text="Codes by SMS",
            reason="Cheap",
            status="superseded",
            superseded_by_id=3,
            nodes=["story:1"],
            decided_on="2026-11-10",
        ),
        decision(
            3,
            title="Use codes by email",
            text="Codes by email",
            reason="SMS costs money",
            project_wide=True,
            nodes=["story:1", "task:1"],
        ),
        decision(
            4,
            title="Use a password manager",
            text="Everybody installs one",
            reason="Safer",
            status="withdrawn",
            status_reason="Not needed after all",
            confirmed_by="Marie",
            confirmed_at=LATER,
        ),
    ]
    facts = [
        fact(
            1,
            text="People log in on phones",
            source="Analytics, October",
            nodes=["story:1"],
            rule_ids=[1],
            decision_ids=[1, 3],
            confirmed_by="Ann",
            confirmed_at=LATER,
        ),
        fact(
            2,
            text="Codes arrive in 10 seconds",
            source="A test",
            status="superseded",
            superseded_by_id=3,
            status_reason="Measured again",
            recorded_by="bob",
            recorded_by_kind="person",
        ),
        fact(3, text="Codes arrive in 3 seconds", source="A measurement", nodes=["task:1"]),
        fact(
            4,
            text="The office has a dog",
            source="Somebody said",
            status="withdrawn",
            status_reason="It is a cat",
        ),
    ]
    events = [
        legacy_event(1, node="epic:1", action="create"),
        legacy_event(
            2,
            action="decision_recorded",
            decision_id=1,
            actor="ann",
            after={
                "title": "Use email",
                "text": "Use email",
                "reason": "Everybody has it",
                "decided_by": "Marie",
                "decided_on": "2026-11-15",
                "project_wide": False,
            },
        ),
        legacy_event(
            3,
            action="decision_linked",
            decision_id=1,
            node="story:1",
            actor="ann",
            after={"decision_id": 1},
        ),
        legacy_event(
            4,
            action="decision_confirmed",
            decision_id=1,
            actor="Priya",
            actor_kind="person",
            before={"confirmed_by": None},
            after={"confirmed_by": "Priya", "confirmed_at": LATER, "quote": "Fine"},
        ),
        legacy_event(
            5,
            action="decision_recorded",
            decision_id=2,
            actor="bob",
            after={
                "title": "Use codes",
                "text": "Codes by SMS",
                "reason": "Cheap",
                "decided_by": "Marie",
                "decided_on": "2026-11-10",
                "project_wide": False,
            },
        ),
        legacy_event(
            6,
            action="decision_recorded",
            decision_id=3,
            actor="Marie",
            actor_kind="person",
            after={
                "title": "Use codes by email",
                "text": "Codes by email",
                "reason": "SMS costs money",
                "decided_by": "Marie",
                "decided_on": "2026-11-15",
                "project_wide": True,
                "supersedes_id": 2,
            },
        ),
        legacy_event(
            7,
            action="decision_superseded",
            decision_id=2,
            actor="bob",
            before={"status": "active", "superseded_by_id": None},
            after={"status": "superseded", "superseded_by_id": 3},
        ),
        legacy_event(
            8,
            action="fact_recorded",
            fact_id=1,
            actor="ann",
            after={"text": "People log in on phones", "source": "Analytics, October"},
        ),
        legacy_event(
            9,
            action="fact_linked",
            fact_id=1,
            decision_id=1,
            actor="ann",
            after={"fact_id": 1},
        ),
        legacy_event(
            10,
            action="fact_recorded",
            fact_id=3,
            actor="bob",
            after={
                "text": "Codes arrive in 3 seconds",
                "source": "A measurement",
                "supersedes_ids": [2],
            },
        ),
        legacy_event(
            11,
            action="fact_superseded",
            fact_id=2,
            actor="bob",
            before={"status": "active", "superseded_by_id": None},
            after={"status": "superseded", "superseded_by_id": 3, "reason": "Measured again"},
        ),
        legacy_event(12, node="task:1", action="update"),
    ]
    return a_legacy_file(
        nodes=nodes,
        links=[link(1, "task:1", "story:1", "relates_to")],
        rules=[rule(1, "story:1", status="held"), rule(2, None, status="settled")],
        decisions=decisions,
        facts=facts,
        events=events,
    )


async def history(client: Client, note_id: int) -> list[dict[str, Any]]:
    return list((await ok(client, "note_history", note_id=note_id))["items"])


async def test_an_old_export_of_decisions_and_facts_loads_as_notes_that_mean_the_same() -> None:
    project = make_project(register())
    old = the_old_file()
    async with connect(project.mcp_url, project.key) as client:
        result = await ok(client, "import_graph", document=old)
        # Four decisions and four facts are eight notes; the graph around them is as it was.
        assert result["counts"] == {
            "nodes": 3,
            "links": 1,
            "rules": 2,
            "notes": 8,
            "definition_of_done": 0,
            "checks": 0,
            "events": 12,
        }
        notes = {n["id"]: n for n in (await export_everything(client))["notes"]}
        assert sorted(notes) == list(range(1, 9))
        # The acceptance criteria are in the words of now: held is open, settled is met.
        rules = (await ok(client, "list_rules", limit=50))["items"]
        assert [(r["id"], r["status"]) for r in rules] == [(1, "open"), (2, "met")]

        # Decisions first (ids 1 to 4), then facts (5 to 8), each in the order they had.
        one = await ok(client, "get_note", note_id=1)
        # A title equal to the text is not repeated; otherwise it is the first line.
        assert one["text"] == "Use email" and one["headline"] == "Use email"
        assert (one["why"], one["decided_by"], one["decided_on"]) == (
            "Everybody has it",
            "Marie",
            "2026-11-15",
        )
        assert one["decided"] is True and one["nodes"] == ["story:1"] and one["rule_ids"] == [1]
        # A decision that was active and that a person confirmed is a confirmed note, and who
        # confirmed it is kept.
        assert (one["status"], one["confirmed_by"]) == ("confirmed", "Priya")
        assert (one["recorded_by"], one["recorded_by_kind"]) == ("ann", "agent")

        two = await ok(client, "get_note", note_id=2)
        assert two["text"] == "Use codes\n\nCodes by SMS" and two["headline"] == "Use codes"
        assert (two["status"], two["superseded_by_id"], two["decided_on"]) == (
            "superseded",
            3,
            "2026-11-10",
        )
        assert (two["recorded_by"], two["recorded_by_kind"]) == ("bob", "agent")
        three = await ok(client, "get_note", note_id=3)
        assert (three["supersedes_ids"], three["project_wide"], three["status"]) == (
            [2],
            True,
            "unconfirmed",
        )
        assert three["recorded_by_kind"] == "person" and three["nodes"] == ["story:1", "task:1"]
        # A decision nobody recorded an event for has no recorder to name.
        four = await ok(client, "get_note", note_id=4)
        assert (four["recorded_by"], four["recorded_by_kind"]) == ("unknown", "unknown")
        assert (four["status"], four["status_reason"]) == ("withdrawn", "Not needed after all")
        # A note that ended keeps who confirmed it.
        assert (four["confirmed_by"], four["decided_by"]) == ("Marie", "Marie")

        # A fact has no decided fields, its source is its why, and what it concerned among
        # decisions is a link to the notes those decisions are now.
        five = await ok(client, "get_note", note_id=5)
        assert (five["text"], five["why"]) == ("People log in on phones", "Analytics, October")
        assert (five["decided"], five["decided_by"], five["decided_on"]) == (False, None, None)
        assert (
            five["note_ids"] == [1, 3] and five["rule_ids"] == [1] and five["nodes"] == ["story:1"]
        )
        assert (five["status"], five["confirmed_by"]) == ("confirmed", "Ann")
        six = await ok(client, "get_note", note_id=6)
        assert (six["status"], six["superseded_by_id"], six["status_reason"]) == (
            "superseded",
            7,
            "Measured again",
        )
        assert (six["recorded_by"], six["recorded_by_kind"]) == ("bob", "person")
        seven = await ok(client, "get_note", note_id=7)
        assert seven["supersedes_ids"] == [6] and seven["nodes"] == ["task:1"]
        eight = await ok(client, "get_note", note_id=8)
        assert (eight["status"], eight["status_reason"]) == ("withdrawn", "It is a cat")

        # The history came with them, on the new ids, with the new words.
        first = await history(client, 1)
        assert [(e["action"], e["actor"]) for e in first] == [
            ("note_recorded", "ann"),
            ("note_linked", "ann"),
            ("note_confirmed", "Priya"),
            ("note_linked", "ann"),  # the fact that concerns it, linked from the other side
        ]
        assert first[0]["after"] == {
            "text": "Use email",
            "why": "Everybody has it",
            "decided_by": "Marie",
            "decided_on": "2026-11-15",
            "project_wide": False,
        }
        assert (first[3]["note_id"], first[3]["other_note_id"]) == (5, 1)
        assert first[3]["after"] == {"note_id": 5}
        # A confirmation says the status it changed, in the words of now.
        assert (first[2]["before"], first[2]["after"]["status"]) == (
            {"status": "unconfirmed", "confirmed_by": None},
            "confirmed",
        )
        recorded_three = (await history(client, 3))[0]
        assert recorded_three["after"]["supersedes_ids"] == [2]
        assert recorded_three["after"]["text"] == "Use codes by email\n\nCodes by email"
        superseded = (await history(client, 2))[-1]
        assert superseded["action"] == "note_superseded"
        assert superseded["before"] == {"status": "unconfirmed", "superseded_by_id": None}
        assert superseded["after"] == {"status": "superseded", "superseded_by_id": 3}
        assert (await history(client, 6))[-1]["after"]["superseded_by_id"] == 7
        assert (await history(client, 7))[0]["after"]["supersedes_ids"] == [6]
        # Events of everything else are as they were.
        feed = await ok(client, "changes_since", node="epic:1", limit=50)
        assert [e["action"] for e in feed["items"]] == ["create"]

        # It works as notes work: recall, search, and the next note continues after the highest.
        recalled = await ok(client, "recall", node="story:1")
        assert [e["note"]["id"] for e in recalled["notes"]["items"]] == [1, 5]
        assert [e["note"]["id"] for e in recalled["project_wide_notes"]["items"]] == [3]
        assert [e["note"]["id"] for e in recalled["past_notes"]["items"]] == [2]
        found = await ok(client, "search", query="phones")
        assert [i["ref"] for i in found["items"]] == ["note:5"]
        fresh = await ok(client, "add_note", text="After the import", why="Now")
        assert fresh["id"] == 9


async def test_an_old_export_keeps_what_it_said_in_its_texts_and_the_current_form_round_trips() -> (
    None
):
    project = make_project(register())
    old = the_old_file()
    old["decisions"][0]["reason"] = "As [[decision:3]] and [[fact:2]] say"
    old["facts"][0]["text"] = "See [[decision:1]]"
    async with connect(project.mcp_url, project.key) as client:
        await ok(client, "import_graph", document=old)
        assert (await ok(client, "get_note", note_id=1))["why"] == (
            "As [[decision:3]] and [[fact:2]] say"
        )
        assert (await ok(client, "get_note", note_id=5))["text"] == "See [[decision:1]]"
        # Those references name nothing in a project of notes, and `find_mentions` says nothing.
        assert (await ok(client, "find_mentions", limit=50))["items"] == []
        exported = await export_everything(client)
    assert exported["version"] == 6
    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=exported)
        assert await export_everything(client) == exported


async def test_a_version_1_file_loads_with_the_kinds_it_never_said_as_unknown() -> None:
    project = make_project(register())
    older = as_version_1(the_old_file())
    async with connect(project.mcp_url, project.key) as client:
        result = await ok(client, "import_graph", document=older)
        assert result["counts"]["notes"] == 8
        facts = [await ok(client, "get_note", note_id=i) for i in (5, 6, 7, 8)]
        assert {f["recorded_by_kind"] for f in facts} == {"unknown"}
        assert {e["actor_kind"] for e in await history(client, 1)} == {"unknown"}


async def test_an_old_export_with_problems_is_refused_with_all_of_them_and_loads_nothing() -> None:
    project = make_project(register())
    old = the_old_file()
    old["decisions"][1]["superseded_by_id"] = 9
    old["facts"][0]["decision_ids"] = [1, 7]
    old["facts"].append(fact(2, text="A second fact 2"))
    old["events"][2]["decision_id"] = 8
    async with connect(project.mcp_url, project.key) as client:
        said = await refused(client, "import_graph", document=old)
        assert "nothing was imported" in said
        assert "decisions[1] (id 2): superseded_by_id=9 is not a decision in the file" in said
        assert "facts[0] (id 1): decision_ids=7 is not a decision in the file" in said
        assert "facts[4] (id 2): id 2 is used twice" in said
        assert "events[2] (id 3): decision_id=8 is not a decision in the file" in said
        assert (await ok(client, "server_status"))["counts"]["notes"] == 0

        # The current form's own checks apply to what came out of the conversion: a decision that
        # is withdrawn with no reason cannot be loaded as a note.
        flawed = the_old_file()
        flawed["decisions"][3]["status_reason"] = None
        said = await refused(client, "import_graph", document=flawed)
        assert "notes[3] (id 4, from decision 4): a withdrawn note needs status_reason" in said
        # A wrong count is told as the file's own, in the old names.
        counted = the_old_file()
        counted["counts"] = {
            "nodes": 3,
            "links": 1,
            "rules": 2,
            "decisions": 5,
            "facts": 4,
            "events": 12,
        }
        assert "counts.decisions says 5 but the file holds 4" in await refused(
            client, "import_graph", document=counted
        )


def the_version_3_file() -> dict[str, Any]:
    """A file as the server wrote it just before a note had one status: notes that are `active`
    (confirmed or not, the confirmation kept apart) or ended, and requirements that are held,
    settled or dropped, with the events that recorded some of them."""
    return a_version_3_file(
        nodes=[node("epic", 1), node("story", 1, "epic:1")],
        rules=[
            rule(1, "story:1", status="held"),
            rule(2, "story:1", status="settled"),
            rule(3, None, status="dropped"),
        ],
        notes=[
            note(
                1,
                status="active",
                decided_by="Marie",
                decided_on="2026-11-15",
                confirmed_by="Priya",
                confirmed_at=LATER,
            ),
            note(2, status="active", nodes=["story:1"]),
            note(
                3, status="superseded", superseded_by_id=4, confirmed_by="Ann", confirmed_at=LATER
            ),
            note(4, status="active", supersedes_ids=[3], project_wide=True),
            note(
                5,
                status="withdrawn",
                status_reason="No longer so",
                confirmed_by="Tom",
                confirmed_at=LATER,
            ),
            note(6, status="withdrawn", status_reason="It was a cat"),
        ],
        events=[
            event(
                1,
                action="rule_created",
                rule_id=1,
                node="story:1",
                after={"story": "story:1", "title": "rule 1", "status": "held", "version": 1},
            ),
            event(
                2,
                action="rule_updated",
                rule_id=2,
                node="story:1",
                before={"status": "held", "version": 1},
                after={"status": "settled", "version": 2},
            ),
            event(3, action="note_recorded", note_id=3, after={"text": "note 3", "why": "a chat"}),
            event(
                4,
                action="note_confirmed",
                note_id=3,
                actor="Ann",
                actor_kind="person",
                before={"confirmed_by": None},
                after={"confirmed_by": "Ann", "confirmed_at": LATER},
            ),
            event(
                5,
                action="note_superseded",
                note_id=3,
                before={"status": "active", "superseded_by_id": None},
                after={"status": "superseded", "superseded_by_id": 4},
            ),
            event(
                6,
                action="note_withdrawn",
                note_id=6,
                before={"status": "active"},
                after={"status": "withdrawn", "reason": "It was a cat"},
            ),
        ],
    )


async def test_a_version_3_file_loads_with_one_status_for_each_note_and_met_requirements() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        result = await ok(client, "import_graph", document=the_version_3_file())
        assert result["counts"] == {
            "nodes": 2,
            "links": 0,
            "rules": 3,
            "notes": 6,
            "definition_of_done": 0,
            "checks": 0,
            "events": 6,
        }

        # An active note that a person confirmed is confirmed, any other active note is
        # unconfirmed, and one that ended is as it ended, with who confirmed it kept.
        notes = [await ok(client, "get_note", note_id=n) for n in range(1, 7)]
        assert [(n["status"], n["confirmed_by"]) for n in notes] == [
            ("confirmed", "Priya"),
            ("unconfirmed", None),
            ("superseded", "Ann"),
            ("unconfirmed", None),
            ("withdrawn", "Tom"),
            ("withdrawn", None),
        ]
        assert all("confirmation" not in n and "label" not in n for n in notes)
        in_force = await ok(client, "list_notes", status="in_force", limit=50)
        assert [n["id"] for n in in_force["items"]] == [1, 2, 4]
        assert (await ok(client, "list_notes", status="confirmed"))["total"] == 1

        # Held acceptance criteria are open and settled ones are met.
        rules = (await ok(client, "list_rules", limit=50))["items"]
        assert [(r["id"], r["status"]) for r in rules] == [(1, "open"), (2, "met"), (3, "dropped")]
        recalled = await ok(client, "recall", node="story:1")
        assert [e["criterion"]["status"] for e in recalled["acceptance_criteria"]["items"]] == [
            "open",
            "met",
        ]

        # The history says the same words, and a note that ended says what it was when it did.
        (rule_one,) = (await ok(client, "rule_history", rule_id=1))["items"]
        assert rule_one["after"]["status"] == "open"
        # A rule was in a `story` once: what its history says is `node` now.
        assert rule_one["after"]["node"] == "story:1" and "story" not in rule_one["after"]
        updated = (await ok(client, "rule_history", rule_id=2))["items"][-1]
        assert (updated["before"]["status"], updated["after"]["status"]) == ("open", "met")
        replaced = await history(client, 3)
        assert [e["action"] for e in replaced] == [
            "note_recorded",
            "note_confirmed",
            "note_superseded",
        ]
        assert replaced[1]["before"] == {"status": "unconfirmed", "confirmed_by": None}
        assert replaced[1]["after"]["status"] == "confirmed"
        assert replaced[2]["before"] == {"status": "confirmed", "superseded_by_id": None}
        assert (await history(client, 6))[-1]["before"] == {"status": "unconfirmed"}

        exported = await export_everything(client)
    assert exported["version"] == 6
    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=exported)
        assert await export_everything(client) == exported


def the_version_4_file() -> dict[str, Any]:
    """A file as the server wrote it just before a rule could be on a task: its rules are on a
    `story` (or on none), it has no definition of done, and its events say `story` too."""
    return a_version_4_file(
        nodes=[node("epic", 1), node("story", 1, "epic:1"), node("story", 2, "epic:1")],
        rules=[rule(1, "story:1"), rule(2, None, status="met"), rule(3, "story:2")],
        notes=[note(1, nodes=["story:1"], rule_ids=[1])],
        events=[
            event(
                1,
                action="rule_created",
                rule_id=1,
                node="story:1",
                after={"story": "story:1", "title": "rule 1", "status": "open", "version": 1},
            ),
            event(
                2,
                action="rule_created",
                rule_id=3,
                node="story:1",
                after={"story": "story:1", "title": "rule 3", "status": "open", "version": 1},
            ),
            event(
                3,
                action="rule_moved",
                rule_id=3,
                node="story:2",
                other_node="story:1",
                before={"story": "story:1", "version": 1},
                after={"story": "story:2", "version": 2},
            ),
        ],
    )


async def test_a_version_4_file_loads_with_each_rule_on_the_node_it_was_in() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        result = await ok(client, "import_graph", document=the_version_4_file())
        assert result["counts"] == {
            "nodes": 3,
            "links": 0,
            "rules": 3,
            "notes": 1,
            "definition_of_done": 0,
            "checks": 0,
            "events": 3,
        }
        rules = {r["id"]: r for r in (await ok(client, "list_rules", limit=50))["items"]}
        assert [(i, rules[i]["node"]) for i in (1, 2, 3)] == [
            (1, "story:1"),
            (2, None),
            (3, "story:2"),
        ]
        # What a rule's history said about the story it was in says `node`, so a history reads the
        # same whichever server wrote it.
        moved = (await ok(client, "rule_history", rule_id=3))["items"][-1]
        assert moved["before"] == {"node": "story:1", "version": 1}
        assert moved["after"] == {"node": "story:2", "version": 2}
        assert (await ok(client, "get_node", node="story:1"))["criteria_total"] == 1
        # No definition of done came with it, and one can be set.
        assert (await ok(client, "get_definition_of_done"))["version"] == 1
        exported = await export_everything(client)
    assert exported["version"] == 6 and exported["definition_of_done"] == []
    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=exported)
        assert await export_everything(client) == exported


async def test_a_version_5_file_loads_with_no_sizes_and_no_ticks_and_its_checks_can_be_ticked() -> (
    None
):
    document = a_version_5_file(
        nodes=[node("epic", 1), node("story", 1, "epic:1"), node("task", 1, "story:1")],
        rules=[rule(1, "task:1")],
        definition_of_done=[definition(body="- Tests pass\n- Docs are updated")],
        events=[event(1, node="epic:1")],
    )
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        result = await ok(client, "import_graph", document=document)
        assert result["counts"]["checks"] == 0 and result["counts"]["nodes"] == 3
        shown = await ok(client, "get_node", node="task:1")
        # No sizes: nothing is sized, and the task is one that still needs one.
        assert (shown["size"], shown["points"], shown["points_total"], shown["unsized"]) == (
            None,
            None,
            0,
            1,
        )
        # The checks come from the definition of done it held, with none ticked.
        assert [(c["text"], c["checked"]) for c in shown["checks"]] == [
            ("Tests pass", False),
            ("Docs are updated", False),
        ]
        assert (shown["checks_total"], shown["checks_ticked"]) == (2, 0)
        await ok(client, "set_check", node="task:1", item="Tests pass", evidence="212 tests pass")
        exported = await export_everything(client)
    assert exported["version"] == 6 and len(exported["checks"]) == 1
    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=exported)
        assert await export_everything(client) == exported
        assert (await ok(client, "get_node", node="task:1"))["checks_ticked"] == 1


async def test_a_version_3_file_that_uses_the_new_words_or_breaks_a_rule_is_refused() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        mixed = the_version_3_file()
        mixed["rules"][0]["status"] = "open"
        mixed["notes"][1]["status"] = "unconfirmed"
        said = await refused(client, "import_graph", document=mixed)
        assert "nothing was imported" in said
        assert "rules[0]: status must be held, settled or dropped in a file of version 3" in said
        assert (
            "notes[1]: status must be active, superseded or withdrawn in a file of version 3"
            in said
        )

        # What is checked in the files of today is checked in these too, once they are converted.
        flawed = the_version_3_file()
        flawed["notes"][5]["status_reason"] = None
        assert "notes[5] (id 6): a withdrawn note needs status_reason" in await refused(
            client, "import_graph", document=flawed
        )
        assert (await ok(client, "server_status"))["counts"]["notes"] == 0
