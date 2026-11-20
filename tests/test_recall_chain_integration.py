"""M26's proof, for notes (M27): `recall` follows the parent chain. What is linked to an epic or a
story reaches the stories, tasks and acceptance criteria under it, each entry saying where it comes
from;
nothing reaches upwards or sideways; a note linked at several levels appears once, named by the
nearest; ended notes stay in the past section; a project-wide note is not repeated; another project
is never reached; and `recall` and `next_work` give the same answer for the same item."""

from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    connect,
    make_decision,
    make_fact,
    make_note,
    make_rule,
    make_tree,
    ok,
    unique_word,
)
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def recalled(client: Client, **arguments: Any) -> dict[str, Any]:
    return await ok(client, "recall", limit=200, **arguments)


def notes_in(result: dict[str, Any], section: str) -> dict[int, dict[str, Any]]:
    """The entries of one note section, by note id."""
    return {entry["note"]["id"]: entry for entry in result[section]["items"]}


async def test_what_is_linked_to_an_epic_and_a_story_comes_down_to_the_work_under_them(
    client: Client,
) -> None:
    word = unique_word()
    epic, story, task = await make_tree(client)
    epic_decision = await make_decision(client, f"For the epic {word}", nodes=[epic["ref"]])
    story_decision = await make_decision(client, f"For the story {word}", nodes=[story["ref"]])
    task_decision = await make_decision(client, f"For the task {word}", nodes=[task["ref"]])
    epic_note = await make_fact(client, f"Epic note {word}", nodes=[epic["ref"]])
    story_note = await make_fact(client, f"Story note {word}", nodes=[story["ref"]])

    as_task = await recalled(client, node=task["ref"])
    found = notes_in(as_task, "notes")
    assert set(found) >= {
        epic_decision["id"],
        story_decision["id"],
        task_decision["id"],
        epic_note["id"],
        story_note["id"],
    }
    assert found[epic_decision["id"]]["inherited_from"] == epic["ref"]
    assert found[epic_decision["id"]]["why"] == (
        f"linked to {epic['ref']}, which this task belongs to"
    )
    assert found[story_decision["id"]]["inherited_from"] == story["ref"]
    assert found[story_decision["id"]]["why"] == (
        f"linked to {story['ref']}, which this task belongs to"
    )
    assert found[task_decision["id"]]["inherited_from"] is None
    assert found[task_decision["id"]]["why"] == "linked directly to this node"
    assert found[epic_note["id"]]["inherited_from"] == epic["ref"]
    assert found[story_note["id"]]["inherited_from"] == story["ref"]
    # Each entry still says what it is: its status, so an unconfirmed note is never shown as
    # confirmed.
    assert all(entry["note"]["status"] == "unconfirmed" for entry in found.values())
    assert all("confirmation" not in entry["note"] for entry in found.values())
    assert found[epic_decision["id"]]["note"]["decided"] is True
    assert found[epic_note["id"]]["note"]["decided"] is False

    as_story = await recalled(client, node=story["ref"])
    found = notes_in(as_story, "notes")
    assert found[epic_decision["id"]]["why"] == (
        f"linked to {epic['ref']}, which this story belongs to"
    )
    assert found[story_decision["id"]]["inherited_from"] is None
    assert task_decision["id"] not in found
    assert found[epic_note["id"]]["inherited_from"] == epic["ref"]

    # It goes down only: the epic gets nothing that is linked to a story or a task under it.
    as_epic = await recalled(client, node=epic["ref"])
    found = notes_in(as_epic, "notes")
    assert found[epic_decision["id"]]["inherited_from"] is None
    assert not {story_decision["id"], task_decision["id"], story_note["id"]} & set(found)
    # And a count says how many there are in all, the inherited ones included.
    assert as_task["notes"]["count"] == len(notes_in(as_task, "notes"))


async def test_a_note_linked_at_several_levels_appears_once_named_by_the_nearest(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    word = unique_word()
    both = await make_decision(client, f"Epic and task {word}", nodes=[epic["ref"], task["ref"]])
    other = await make_fact(client, f"Story and epic {word}", nodes=[epic["ref"], story["ref"]])

    as_task = await recalled(client, node=task["ref"])
    entries = [e for e in as_task["notes"]["items"] if e["note"]["id"] == both["id"]]
    assert len(entries) == 1 and entries[0]["inherited_from"] is None
    assert entries[0]["why"] == "linked directly to this node"
    kept = [e for e in as_task["notes"]["items"] if e["note"]["id"] == other["id"]]
    assert len(kept) == 1 and kept[0]["inherited_from"] == story["ref"]

    as_story = await recalled(client, node=story["ref"])
    kept = [e for e in as_story["notes"]["items"] if e["note"]["id"] == other["id"]]
    assert len(kept) == 1 and kept[0]["inherited_from"] is None


async def test_an_ended_note_of_the_epic_is_a_past_note_of_its_work_never_active(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    word = unique_word()
    old = await make_decision(client, f"Old rule {word}", nodes=[epic["ref"]])
    old_note = await make_fact(client, f"Old note {word}", nodes=[epic["ref"]])
    replaced = await make_fact(client, f"Replaced {word}", nodes=[epic["ref"]])
    await ok(client, "withdraw_note", note_ids=[old["id"]], reason="No longer true")
    await ok(client, "withdraw_note", note_ids=[old_note["id"]], reason="It was wrong")
    new = (
        await ok(client, "supersede_note", replaces=[replaced["id"]], text="Newer", why="Checked")
    )["replacement"]

    for node in (story, task):
        result = await recalled(client, node=node["ref"])
        active = notes_in(result, "notes")
        assert not {old["id"], old_note["id"], replaced["id"]} & set(active)
        assert active[new["id"]]["inherited_from"] == epic["ref"]
        past = notes_in(result, "past_notes")
        assert past[old["id"]]["inherited_from"] == epic["ref"]
        assert past[old["id"]]["why"] == (
            f"linked to {epic['ref']}, which this {node['kind']} belongs to; "
            "withdrawn: No longer true"
        )
        assert past[old_note["id"]]["inherited_from"] == epic["ref"]
        assert past[replaced["id"]]["why"] == (
            f"linked to {epic['ref']}, which this {node['kind']} belongs to; "
            f"replaced by note {new['id']}"
        )


async def test_a_project_wide_note_is_not_repeated_when_it_is_also_linked_to_the_epic(
    client: Client,
) -> None:
    epic, _story, task = await make_tree(client)
    wide = await make_decision(
        client, f"Everywhere {unique_word()}", project_wide=True, nodes=[epic["ref"]]
    )
    result = await recalled(client, node=task["ref"])
    assert wide["id"] not in notes_in(result, "notes")
    entry = notes_in(result, "project_wide_notes")[wide["id"]]
    assert entry["inherited_from"] is None
    assert entry["why"].startswith("a project-wide note")
    assert [e["note"]["id"] for e in result["project_wide_notes"]["items"]].count(wide["id"]) == 1


async def test_an_acceptance_criterion_gets_what_is_linked_to_where_it_is_and_above(
    client: Client,
) -> None:
    epic, story, _task = await make_tree(client)
    word = unique_word()
    rule = await make_rule(client, f"Rule {word}", node=story["ref"])
    elsewhere = await make_rule(client, f"Unplaced {word}")
    from_epic = await make_decision(client, f"Epic {word}", nodes=[epic["ref"]])
    from_story = await make_decision(client, f"Story {word}", nodes=[story["ref"]])
    own = await make_decision(client, f"Rule {word}", rule_ids=[rule["id"]])
    own_note = await make_fact(client, f"Rule note {word}", rule_ids=[rule["id"]])
    epic_note = await make_fact(client, f"Epic note {word}", nodes=[epic["ref"]])

    result = await recalled(client, rule_id=rule["id"])
    found = notes_in(result, "notes")
    assert found[own["id"]]["inherited_from"] is None
    assert found[own["id"]]["why"] == "linked directly to this acceptance criterion"
    assert found[from_story["id"]]["inherited_from"] == story["ref"]
    assert found[from_story["id"]]["why"] == (
        f"linked to {story['ref']}, which this acceptance criterion is placed on"
    )
    assert found[from_epic["id"]]["inherited_from"] == epic["ref"]
    assert found[from_epic["id"]]["why"] == (
        f"linked to {epic['ref']}, which holds the story or task this acceptance criterion is on"
    )
    assert found[own_note["id"]]["inherited_from"] is None
    assert found[epic_note["id"]]["inherited_from"] == epic["ref"]

    # An acceptance criterion placed nowhere has no story or task to take anything from.
    loose = await recalled(client, rule_id=elsewhere["id"])
    assert not {from_epic["id"], epic_note["id"]} & set(notes_in(loose, "notes"))


async def test_a_criterion_on_a_task_gets_what_is_linked_to_the_task_its_story_and_its_epic(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    word = unique_word()
    rule = await make_rule(client, f"Rule {word}", node=task["ref"])
    on_task = await make_decision(client, f"Task {word}", nodes=[task["ref"]])
    on_story = await make_decision(client, f"Story {word}", nodes=[story["ref"]])
    on_epic = await make_decision(client, f"Epic {word}", nodes=[epic["ref"]])

    found = notes_in(await recalled(client, rule_id=rule["id"]), "notes")

    assert found[on_task["id"]]["inherited_from"] == task["ref"]
    assert found[on_task["id"]]["why"] == (
        f"linked to {task['ref']}, which this acceptance criterion is placed on"
    )
    assert found[on_story["id"]]["inherited_from"] == story["ref"]
    assert found[on_epic["id"]]["inherited_from"] == epic["ref"]
    suffix = "which holds the story or task this acceptance criterion is on"
    assert found[on_story["id"]]["why"].endswith(suffix)
    assert found[on_epic["id"]]["why"].endswith(suffix)


async def test_recall_and_next_work_give_the_same_answer_for_the_same_item(client: Client) -> None:
    epic, story, task = await make_tree(client)
    word = unique_word()
    await make_decision(client, f"Epic {word}", nodes=[epic["ref"]])
    await make_decision(client, f"Story {word}", nodes=[story["ref"]])
    ended = await make_decision(client, f"Ended {word}", nodes=[epic["ref"]])
    await ok(client, "withdraw_note", note_ids=[ended["id"]], reason="Not any more")
    await make_decision(client, f"Wide {word}", project_wide=True)
    await make_note(client, f"Wide plain {word}", "w", project_wide=True)
    await make_fact(client, f"Epic note {word}", nodes=[epic["ref"]])
    await make_fact(client, f"Task note {word}", nodes=[task["ref"]])

    work = await ok(client, "next_work", within_node=epic["ref"], peek=True)
    assert work["found"] and work["work"]["ref"] == task["ref"], work["message"]
    context = work["context"]
    result = await recalled(client, node=task["ref"])

    def ids(entries: list[dict[str, Any]], key: str | None = None) -> list[int]:
        return [(entry[key] if key else entry)["id"] for entry in entries]

    # The same notes, in the same order: decisions first, then the others, each by id.
    for section in ("notes", "project_wide_notes"):
        assert ids(result[section]["items"], "note") == ids(context[section]), section
    assert set(ids(result["past_notes"]["items"], "note")) == set(ids(context["past_notes"]))
    decided = [n["decided"] for n in context["notes"]]
    assert decided == sorted(decided, reverse=True) and any(decided) and not all(decided)
    # What an entry of the briefing says is what the note says.
    by_id = {entry["note"]["id"]: entry["note"] for entry in result["notes"]["items"]}
    for entry in context["notes"]:
        note = by_id[entry["id"]]
        for field in ("text", "why", "decided_by", "decided_on", "status", "confirmed_by"):
            assert entry[field] == note[field], field
        assert entry["linked_to"] and set(entry["linked_to"]) <= {
            epic["ref"],
            story["ref"],
            task["ref"],
        }


async def test_another_project_is_never_reached() -> None:
    owner = register()
    mine, theirs = make_project(owner, "Mine"), make_project(owner, "Theirs")
    async with (
        connect(mine.mcp_url, mine.key) as mine_client,
        connect(theirs.mcp_url, theirs.key) as theirs_client,
    ):
        my_epic, my_story, _ = await make_tree(mine_client)
        their_epic, their_story, _ = await make_tree(theirs_client)
        assert my_epic["ref"] == their_epic["ref"]  # each project numbers its own
        note = await make_decision(
            mine_client, f"Mine only {unique_word()}", nodes=[my_epic["ref"]]
        )
        mine_sees = notes_in(await recalled(mine_client, node=my_story["ref"]), "notes")
        theirs_sees = notes_in(await recalled(theirs_client, node=their_story["ref"]), "notes")
        assert mine_sees[note["id"]]["inherited_from"] == my_epic["ref"]
        assert theirs_sees == {}


async def test_a_task_has_its_own_acceptance_criteria_and_its_storys_and_an_epic_has_none(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    word = unique_word()
    still_open = await make_rule(client, f"Open {word}", node=story["ref"])
    met = await make_rule(client, f"Met {word}", node=story["ref"])
    await ok(client, "update_rule", rule_id=met["id"], expected_version=1, status="met")
    dropped = await make_rule(client, f"Dropped {word}", node=story["ref"])
    await ok(client, "update_rule", rule_id=dropped["id"], expected_version=1, status="dropped")
    on_task = await make_rule(client, f"On the task {word}", node=task["ref"])
    other_story = await ok(
        client, "create_node", kind="story", title="Another story", parent=epic["ref"]
    )
    elsewhere = await make_rule(client, f"Elsewhere {word}", node=other_story["ref"])
    unplaced = await make_rule(client, f"Unplaced {word}")

    def criterion_ids(result: dict[str, Any]) -> list[int]:
        return [entry["criterion"]["id"] for entry in result["acceptance_criteria"]["items"]]

    # A story has its own, and nothing comes up from its tasks.
    as_story = await recalled(client, node=story["ref"])
    assert criterion_ids(as_story) == [still_open["id"], met["id"]]
    assert all(e["inherited_from"] is None for e in as_story["acceptance_criteria"]["items"])
    assert all(e["on"] == story["ref"] for e in as_story["acceptance_criteria"]["items"])
    assert as_story["acceptance_criteria"]["items"][0]["why"] == "placed on this story"

    # A task has its story's too, each saying which item it is on.
    as_task = await recalled(client, node=task["ref"])
    assert criterion_ids(as_task) == [still_open["id"], met["id"], on_task["id"]]
    first, _, own = as_task["acceptance_criteria"]["items"]
    assert (first["on"], first["inherited_from"]) == (story["ref"], story["ref"])
    assert first["why"] == f"placed on {story['ref']}, which this task belongs to"
    assert (own["on"], own["inherited_from"], own["why"]) == (
        task["ref"],
        None,
        "placed on this task",
    )
    assert as_task["acceptance_criteria"]["count"] == 3
    # The detail stays on the criterion's own page: a recall carries the title and a preview.
    assert first["criterion"]["body"] is None and first["criterion"]["title"] == f"Open {word}"

    # An epic has none of its own, and nothing comes up from the stories under it.
    as_epic = await recalled(client, node=epic["ref"])
    assert as_epic["acceptance_criteria"] == {"items": [], "count": 0, "next_after_id": None}
    # Nor does a criterion get criteria, nor a story another story's.
    for rule in (still_open, unplaced):
        assert (await recalled(client, rule_id=rule["id"]))["acceptance_criteria"]["count"] == 0
    assert elsewhere["id"] not in criterion_ids(as_task)
    # A page of just the acceptance criteria.
    only = await ok(client, "recall", node=task["ref"], show="acceptance_criteria", limit=1)
    assert (
        criterion_ids(only) == [still_open["id"]]
        and only["acceptance_criteria"]["next_after_id"] == still_open["id"]
    )
    rest = await ok(
        client,
        "recall",
        node=task["ref"],
        show="acceptance_criteria",
        limit=2,
        after_id=still_open["id"],
    )
    assert criterion_ids(rest) == [met["id"], on_task["id"]]
    assert rest["acceptance_criteria"]["next_after_id"] is None

    # next_work hands the same acceptance criteria to the agent that takes the task, its own first.
    work = await ok(client, "next_work", within_node=story["ref"], peek=True)
    assert work["found"] and work["work"]["ref"] == task["ref"], work["message"]
    briefed = work["context"]["acceptance_criteria"]
    assert [(c["id"], c["on"]) for c in briefed] == [
        (on_task["id"], task["ref"]),
        (still_open["id"], story["ref"]),
        (met["id"], story["ref"]),
    ]
