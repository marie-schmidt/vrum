"""M27 against the running containers: notes, the one record that decisions and facts became. A
note is a text and why it is so, and when a person decided it, who and when; it is never edited,
replaced or withdrawn by what a caller names, confirmed only by a person, linked to nodes,
acceptance criteria and other notes, and found again by recall, search, mentions, history and
export. Through a real MCP client and the board's own API (see ./check.sh)."""

from datetime import datetime
from typing import Any

import anyio
import pytest
from mcp import Client

from tests.helpers import (
    confirm_on_board,
    connect,
    connect_as,
    day,
    export_everything,
    make_decision,
    make_fact,
    make_note,
    make_rule,
    make_stories,
    make_tree,
    ok,
    race_mixed,
    race_results,
    refused,
    reindexed,
    rich_world,
    run_sql,
    unique_word,
)
from tests.world import make_project, register, the_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NOTE_FIELDS = {
    "id",
    "headline",
    "text",
    "why",
    "decided",
    "decided_by",
    "decided_on",
    "project_wide",
    "status",
    "superseded_by_id",
    "supersedes_ids",
    "status_reason",
    "confirmed_by",
    "confirmed_at",
    "recorded_by",
    "recorded_by_kind",
    "recorded_at",
    "nodes",
    "rule_ids",
    "note_ids",
}


def ids(items: list[dict[str, Any]]) -> list[int]:
    return [item["id"] for item in items]


def in_section(section: dict[str, Any]) -> list[dict[str, Any]]:
    """The notes in one section of a recall."""
    return [entry["note"] for entry in section["items"]]


def instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


async def get(client: Client, note: dict[str, Any]) -> dict[str, Any]:
    return await ok(client, "get_note", note_id=note["id"])


async def history(client: Client, note: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await ok(client, "note_history", note_id=note["id"]))["items"])


async def every(client: Client, **filters: Any) -> tuple[list[dict[str, Any]], int]:
    """Every note matching the filters, in pages, with the total the first page gave."""
    found: list[dict[str, Any]] = []
    after_id = None
    total = None
    while True:
        page = await ok(client, "list_notes", limit=25, after_id=after_id, **filters)
        total = page["total"] if total is None else total
        found.extend(page["items"])
        after_id = page["next_after_id"]
        if after_id is None:
            return found, total


async def tally(client: Client) -> tuple[int, int]:
    """How many notes and events the project holds: a refused call must leave both as they were."""
    counts = (await ok(client, "server_status"))["counts"]
    return counts["notes"], counts["events"]


async def replace(client: Client, *notes: dict[str, Any], **arguments: Any) -> dict[str, Any]:
    """Supersede the notes with a new one. Returns the change (replacement, superseded_ids...)."""
    fields: dict[str, Any] = {
        "replaces": [note["id"] for note in notes],
        "text": "The newer note",
        "why": "It changed",
    }
    return await ok(client, "supersede_note", **{**fields, **arguments})


async def withdraw(client: Client, *notes: dict[str, Any], **arguments: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"note_ids": [note["id"] for note in notes], "reason": "It was wrong"}
    return await ok(client, "withdraw_note", **{**fields, **arguments})


def by_a_person(tool: str, **arguments: Any) -> tuple[int, dict[str, Any]]:
    """What the board answers when the person signed in (the owner of the shared project) calls a
    tool: the HTTP status, and what the tool returned or the message that refused it."""
    project = the_project()
    reply = project.owner.call("POST", f"/board/api/projects/{project.id}/tool/{tool}", arguments)
    return reply.status, dict(reply.body)


def person_refused(tool: str, **arguments: Any) -> str:
    status, body = by_a_person(tool, **arguments)
    assert status == 400, (status, body)
    return str(body["message"])


# -- recording a note -------------------------------------------------------------------------


async def test_a_note_is_recorded_with_its_why_who_recorded_it_and_what_it_concerns(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, node=story["ref"])
    other = await make_note(client, "Another note")
    name = f"scribe-{unique_word()}"
    async with connect_as(name) as scribe:
        note = await ok(
            scribe,
            "add_note",
            text="Logins use an email address.\n\nThe longer story follows.",
            why="Marie said so in the call",
            nodes=[story["ref"]],
            rule_ids=[rule["id"]],
            note_ids=[other["id"]],
        )
    assert set(note) == NOTE_FIELDS
    assert note["text"] == "Logins use an email address.\n\nThe longer story follows."
    assert note["headline"] == "Logins use an email address."
    assert note["why"] == "Marie said so in the call"
    # Nobody decided it: it is a plain note, unconfirmed, in force, recorded by the key's name.
    assert (note["decided"], note["decided_by"], note["decided_on"]) == (False, None, None)
    assert (note["status"], note["confirmed_by"], note["confirmed_at"]) == (
        "unconfirmed",
        None,
        None,
    )
    assert (note["recorded_by"], note["recorded_by_kind"]) == (name, "agent")
    assert note["recorded_at"]
    assert "label" not in note and "confirmation" not in note
    assert note["project_wide"] is False
    assert (note["nodes"], note["rule_ids"], note["note_ids"]) == (
        [story["ref"]],
        [rule["id"]],
        [other["id"]],
    )
    assert (note["superseded_by_id"], note["supersedes_ids"], note["status_reason"]) == (
        None,
        [],
        None,
    )

    assert await get(client, note) == note
    assert (await every(client, node=story["ref"]))[0] == [note]
    assert (await every(client, rule_id=rule["id"]))[0] == [note]
    # "Concerns exactly this note": what is about `other`, which is `note`.
    assert (await every(client, note_id=other["id"]))[0] == [note]

    events = await history(client, note)
    assert [event["action"] for event in events] == ["note_recorded"] + ["note_linked"] * 3
    recorded = events[0]
    assert (recorded["actor"], recorded["actor_kind"], recorded["note_id"]) == (
        name,
        "agent",
        note["id"],
    )
    assert recorded["after"] == {
        "text": note["text"],
        "why": note["why"],
        "decided_by": None,
        "decided_on": None,
        "project_wide": False,
    }
    # Each link event names what it links to, and the note-to-note one names the other note, so
    # that it is in the history of both.
    assert [(e["node"], e["rule_id"], e["other_note_id"]) for e in events[1:]] == [
        (story["ref"], None, None),
        (None, rule["id"], None),
        (None, None, other["id"]),
    ]
    assert [e["action"] for e in await history(client, other)] == ["note_recorded", "note_linked"]


async def test_a_note_someone_decided_says_who_and_when_as_a_detail(client: Client) -> None:
    today = day()
    decision = await make_note(client, "Use PostgreSQL.", decided_by="Marie")
    assert decision["decided"] is True
    assert (decision["decided_by"], decision["decided_on"]) == ("Marie", today)
    assert decision["status"] == "unconfirmed"
    # The person who decided it is not who recorded it: the key is.
    assert decision["recorded_by"] != "Marie" and decision["recorded_by_kind"] == "agent"

    dated = await make_note(client, "Use Redis.", decided_by="Priya", decided_on="2026-11-01")
    assert dated["decided_on"] == "2026-11-01"
    assert dated["status"] == "unconfirmed"
    # Decided or not is its own question.
    decided, _ = await every(client, decided=True, project_wide=False, node=None)
    assert {decision["id"], dated["id"]} <= set(ids(decided))
    plain = await make_note(client, "Nobody decided this.")
    assert plain["id"] not in ids(decided)
    undecided, _ = await every(client, decided=False)
    assert plain["id"] in ids(undecided) and decision["id"] not in ids(undecided)

    # A blank name is no name: the note is a plain one. A day with nobody to have decided it is
    # refused, and so is a day that is not a day.
    assert (await make_note(client, "A plain one.", decided_by="  "))["decided"] is False
    before = await tally(client)
    assert "decided_on needs decided_by" in await refused(
        client, "add_note", text="x", why="y", decided_on="2026-11-01"
    )
    assert "decided_on" in await refused(
        client, "add_note", text="x", why="y", decided_by="Marie", decided_on="next tuesday"
    )
    assert await tally(client) == before

    # Replacing a decision with a plain note, and the other way round, is allowed: it is a new
    # note, and says what it is.
    change = await replace(client, decision, decided_by="Ann")
    assert change["replacement"]["decided"] is True
    assert (
        change["replacement"]["decided_by"] == "Ann"
        and change["replacement"]["decided_on"] == today
    )
    again = await replace(client, change["replacement"])
    assert again["replacement"]["decided"] is False


async def test_a_notes_headline_is_its_first_line_that_is_not_blank_cut_to_120_characters(
    client: Client,
) -> None:
    padded = await make_note(client, "\n\n   The first real line   \nand a second line")
    assert padded["headline"] == "The first real line"
    exactly = "e" * 120
    assert (await make_note(client, exactly + "\nmore"))["headline"] == exactly
    cut = await make_note(client, "c" * 121 + "\nmore")
    assert cut["headline"] == "c" * 119 + "\u2026" and len(cut["headline"]) == 120
    assert cut["text"].startswith("c" * 121)


async def test_a_note_that_is_blank_unexplained_or_about_nothing_is_refused_and_leaves_nothing(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    base: dict[str, Any] = {"text": "Something", "why": "Someone said so"}
    before = await tally(client)

    for change, expected in (
        ({"why": "  "}, "A note needs its reason: why cannot be empty"),
        ({"why": ""}, "A note needs its reason: why cannot be empty"),
        ({"text": " "}, "A note needs its text: text cannot be empty"),
        (
            {"nodes": ["story:987654321"]},
            "There is no story 987654321 in this project, so the note cannot be",
        ),
        ({"rule_ids": [987654321]}, "There is no acceptance criterion with id 987654321"),
        ({"note_ids": [987654321]}, "There is no note with id 987654321"),
        ({"nodes": ["story"]}, "nodes must name an epic, story or task"),
    ):
        assert expected in await refused(client, "add_note", **{**base, **change})
    # What the old tools asked for is not asked for now: a call written for them says what is
    # missing, not something else.
    assert "why: Field required" in await refused(
        client, "add_note", text="Old style", reason="Because", title="A title"
    )

    # A refused note leaves nothing behind, not even a link made before the bad one was found.
    mixed = {**base, "nodes": [story["ref"]], "note_ids": [987654321]}
    await refused(client, "add_note", **mixed)
    assert await tally(client) == before
    assert (await ok(client, "list_notes", node=story["ref"]))["total"] == 0


async def test_a_secret_in_what_a_note_says_is_refused_and_nothing_is_stored(
    client: Client,
) -> None:
    secret = "ghp_" + "a1B2c3D4" * 5
    note = await make_note(client, "A calm note")
    before = await tally(client)
    cases: list[tuple[str, dict[str, Any]]] = [
        ("add_note", {"text": f"The key is {secret}", "why": "w"}),
        ("add_note", {"text": "t", "why": f"Because {secret}"}),
        ("add_note", {"text": "t", "why": "w", "decided_by": secret}),
        ("supersede_note", {"replaces": [note["id"]], "text": "t", "why": secret}),
        ("withdraw_note", {"note_ids": [note["id"]], "reason": secret}),
        (
            "set_confirmation",
            {"note_id": note["id"], "confirmation": "confirmed", "person": "M", "quote": secret},
        ),
    ]
    for tool, arguments in cases:
        message = await refused(client, tool, **arguments)
        assert "contains what looks like a GitHub token" in message, message
        assert secret not in message and secret[:12] not in message
    assert await tally(client) == before
    assert await get(client, note) == note
    for column in ("text", "why", "decided_by"):
        found = run_sql(
            f"SELECT count(*) FROM notes WHERE {column} LIKE '%{secret[:12]}%'",
            plain=True,
            as_project=True,
        )
        assert found.stdout.strip() == "0", found.stderr


# -- recall: what is known about an item, by exact links ------------------------------------------


async def test_recall_gives_what_the_exact_links_give_and_says_why_each_note_is_there(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    word = unique_word()
    # The plain note is recorded before the decision: decisions still come first.
    plain = await make_note(client, f"Plain {word}", nodes=[story["ref"]])
    decision = await make_decision(client, f"Decided {word}", nodes=[story["ref"]])
    elsewhere = await make_note(client, f"Linked to the epic only {word}", nodes=[epic["ref"]])
    wide = await make_note(client, f"Everywhere {word}", project_wide=True)
    old = await make_note(client, f"Old {word}", nodes=[story["ref"]])
    replacement = (await replace(client, old, text=f"New {word}"))["replacement"]
    gone = await make_note(client, f"Gone {word}", nodes=[story["ref"]])
    await withdraw(client, gone, reason="No longer true")
    other_story = (await make_stories(client, 1))[0]
    unrelated = await make_note(client, f"Elsewhere {word}", nodes=[other_story["ref"]])

    result = await ok(client, "recall", node=story["ref"])
    assert set(result) == {
        "notes",
        "project_wide_notes",
        "past_notes",
        "acceptance_criteria",
        "similar",
    }
    assert result["similar"] is None
    found = in_section(result["notes"])
    assert ids(found) == [decision["id"], plain["id"], elsewhere["id"], replacement["id"]]
    assert result["notes"]["count"] == 4 and result["notes"]["next_after_id"] is None
    entries = {entry["note"]["id"]: entry for entry in result["notes"]["items"]}
    assert entries[plain["id"]]["why"] == "linked directly to this node"
    assert entries[plain["id"]]["inherited_from"] is None
    assert entries[elsewhere["id"]]["inherited_from"] == epic["ref"]
    assert entries[elsewhere["id"]]["why"] == (
        f"linked to {epic['ref']}, which this story belongs to"
    )
    found_entries = [e["note"] for e in result["notes"]["items"]]
    assert entries[plain["id"]]["note"]["status"] == "unconfirmed"
    assert entries[decision["id"]]["note"]["decided_by"] == "Marie"
    assert all("label" not in note and "confirmation" not in note for note in found_entries)
    assert unrelated["id"] not in ids(found)

    wide_entries = {e["note"]["id"]: e for e in result["project_wide_notes"]["items"]}
    assert wide["id"] in wide_entries and wide["id"] not in ids(found)
    assert wide_entries[wide["id"]]["why"] == (
        "a project-wide note, so it applies throughout the project"
    )
    assert wide_entries[wide["id"]]["inherited_from"] is None

    past = {e["note"]["id"]: e for e in result["past_notes"]["items"]}
    assert set(past) == {old["id"], gone["id"]}
    assert past[old["id"]]["why"] == (
        f"linked directly to this node; replaced by note {replacement['id']}"
    )
    assert past[gone["id"]]["why"] == "linked directly to this node; withdrawn: No longer true"
    assert (past[old["id"]]["note"]["status"], past[gone["id"]]["note"]["status"]) == (
        "superseded",
        "withdrawn",
    )
    assert past[old["id"]]["note"]["superseded_by_id"] == replacement["id"]
    assert past[gone["id"]]["note"]["status_reason"] == "No longer true"

    # A task has what its story and epic have; the epic has only its own.
    below = await ok(client, "recall", node=task["ref"])
    assert ids(in_section(below["notes"])) == [
        decision["id"],
        plain["id"],
        elsewhere["id"],
        replacement["id"],
    ]
    assert {e["inherited_from"] for e in below["notes"]["items"]} == {story["ref"], epic["ref"]}
    top = await ok(client, "recall", node=epic["ref"])
    assert ids(in_section(top["notes"])) == [elsewhere["id"]]


async def test_each_recall_section_pages_on_its_own_decisions_first_and_bad_asks_are_refused(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    word = unique_word()
    first_plain = await make_note(client, f"Plain one {word}", nodes=[story["ref"]])
    second_plain = await make_note(client, f"Plain two {word}", nodes=[story["ref"]])
    first_decision = await make_decision(client, f"Decided one {word}", nodes=[story["ref"]])
    second_decision = await make_decision(client, f"Decided two {word}", nodes=[story["ref"]])
    expected = [
        first_decision["id"],
        second_decision["id"],
        first_plain["id"],
        second_plain["id"],
    ]

    walked: list[int] = []
    after: int | None = None
    while True:
        page = await ok(client, "recall", node=story["ref"], show="notes", limit=1, after_id=after)
        walked.extend(ids(in_section(page["notes"])))
        # Only the section asked for is filled in; the others are empty and still count.
        assert page["past_notes"]["items"] == []
        assert page["acceptance_criteria"]["items"] == []
        assert page["notes"]["count"] == 4
        after = page["notes"]["next_after_id"]
        if after is None:
            break
    assert walked == expected

    ended = [await make_note(client, f"Ended {n} {word}", nodes=[story["ref"]]) for n in (1, 2)]
    await withdraw(client, *ended)
    first = await ok(client, "recall", node=story["ref"], show="past_notes", limit=1)
    assert ids(in_section(first["past_notes"])) == [ended[0]["id"]]
    assert first["past_notes"]["count"] == 2 and first["notes"]["items"] == []
    second = await ok(
        client,
        "recall",
        node=story["ref"],
        show="past_notes",
        limit=1,
        after_id=first["past_notes"]["next_after_id"],
    )
    assert ids(in_section(second["past_notes"])) == [ended[1]["id"]]
    assert second["past_notes"]["next_after_id"] is None

    assert "after_id only works for one section at a time" in await refused(
        client, "recall", node=story["ref"], after_id=1
    )
    assert (
        "show must be one of all, notes, project_wide_notes, past_notes, acceptance_criteria"
        in (await refused(client, "recall", node=story["ref"], show="facts"))
    )
    assert "Give exactly one of node" in await refused(client, "recall")
    assert "Give exactly one of node" in await refused(
        client, "recall", node=story["ref"], rule_id=1
    )
    assert "There is no story 987654321" in await refused(client, "recall", node="story:987654321")


async def test_notes_can_be_recalled_for_an_acceptance_criterion(client: Client) -> None:
    epic, story, _task = await make_tree(client)
    word = unique_word()
    rule = await make_rule(client, f"Rule {word}", node=story["ref"])
    loose = await make_rule(client, f"Unplaced {word}")
    own = await make_note(client, f"Own {word}", rule_ids=[rule["id"]])
    from_story = await make_note(client, f"Story {word}", nodes=[story["ref"]])
    from_epic = await make_decision(client, f"Epic {word}", nodes=[epic["ref"]])

    result = await ok(client, "recall", rule_id=rule["id"])
    entries = {e["note"]["id"]: e for e in result["notes"]["items"]}
    assert set(entries) == {own["id"], from_story["id"], from_epic["id"]}
    assert entries[own["id"]]["why"] == "linked directly to this acceptance criterion"
    assert entries[from_story["id"]]["why"] == (
        f"linked to {story['ref']}, which this acceptance criterion is placed on"
    )
    assert entries[from_epic["id"]]["why"] == (
        f"linked to {epic['ref']}, which holds the story or task this acceptance criterion is on"
    )
    # An acceptance criterion placed nowhere has only what is linked to it.
    assert (await ok(client, "recall", rule_id=loose["id"]))["notes"]["items"] == []
    criteria = (await ok(client, "recall", node=story["ref"]))["acceptance_criteria"]
    assert [e["criterion"]["id"] for e in criteria["items"]] == [rule["id"]]
    assert criteria["items"][0]["why"] == "placed on this story"
    assert criteria["items"][0]["on"] == story["ref"]


async def test_a_project_wide_note_is_in_every_recall_while_it_is_in_force(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    word = unique_word()
    wide = await make_decision(client, f"Always {word}", project_wide=True, nodes=[epic["ref"]])
    plain_wide = await make_note(client, f"Also always {word}", project_wide=True)
    assert wide["decided_by"] == "Marie" and wide["project_wide"] is True
    assert plain_wide["decided_by"] is None and plain_wide["status"] == "unconfirmed"

    for node in (epic, story, task):
        result = await ok(client, "recall", node=node["ref"])
        found = {e["note"]["id"]: e for e in result["project_wide_notes"]["items"]}
        assert {wide["id"], plain_wide["id"]} <= set(found)
        # Linked to the epic as well, it is still there once, and not in the other section.
        assert wide["id"] not in ids(in_section(result["notes"]))
        assert found[wide["id"]]["inherited_from"] is None
        # Decisions come first among the project-wide ones too.
        order = ids(in_section(result["project_wide_notes"]))
        assert order.index(wide["id"]) < order.index(plain_wide["id"])
    # The same for what is in the briefing.
    work = await ok(client, "next_work", within_node=epic["ref"], peek=True)
    assert work["found"], work["message"]
    briefed = {n["id"] for n in work["context"]["project_wide_notes"]}
    assert {wide["id"], plain_wide["id"]} <= briefed
    assert wide["id"] not in {n["id"] for n in work["context"]["notes"]}

    # Once replaced, it is a past note where it was linked, and no longer project-wide anywhere.
    change = await replace(client, wide, text=f"Not always {word}", project_wide=False)
    assert change["replacement"]["project_wide"] is False
    after = await ok(client, "recall", node=task["ref"])
    assert wide["id"] not in ids(in_section(after["project_wide_notes"]))
    assert wide["id"] in ids(in_section(after["past_notes"]))
    # A replacement keeps a note project-wide when nothing is said.
    kept = await replace(client, plain_wide, text=f"Still always {word}")
    assert kept["replacement"]["project_wide"] is True


# -- replacing and withdrawing ---------------------------------------------------------------------


async def test_the_briefing_holds_the_notes_and_says_what_it_left_out_when_it_must_cut(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    word = unique_word()
    plain = await make_note(client, f"Plain {word}", "Seen once", nodes=[story["ref"]])
    decision = await make_decision(client, f"Decided {word}", nodes=[task["ref"]])
    gone = await make_note(client, f"Gone {word}", "w", nodes=[epic["ref"]])
    await withdraw(client, gone)
    for number in range(6):
        await make_note(
            client, f"Padding {number} {word} " + "long words " * 20, "w", nodes=[task["ref"]]
        )

    work = await ok(client, "next_work", within_node=epic["ref"], peek=True, context_chars=200_000)
    assert work["found"] and work["work"]["ref"] == task["ref"], work["message"]
    context = work["context"]
    assert set(context) >= {
        "notes",
        "project_wide_notes",
        "past_notes",
        "acceptance_criteria",
        "definition_of_done",
        "parents",
    }
    assert "rules" not in context
    assert not {"active_decisions", "facts", "past_decisions", "project_wide_decisions"} & set(
        context
    )
    notes = context["notes"]
    # Decisions first, then the others, each by id; each says what it is and where it is linked.
    assert ids(notes)[:2] == [decision["id"], plain["id"]]
    assert [n["decided"] for n in notes][:2] == [True, False]
    first = notes[0]
    assert first["why"] == "Because"
    assert (first["decided_by"], first["status"], first["linked_to"]) == (
        "Marie",
        "unconfirmed",
        [task["ref"]],
    )
    assert "label" not in first and "confirmation" not in first
    assert notes[1]["linked_to"] == [story["ref"]] and notes[1]["decided_by"] is None
    assert [n["id"] for n in context["past_notes"]] == [gone["id"]]
    assert (context["past_notes"][0]["status"], context["past_notes"][0]["status_reason"]) == (
        "withdrawn",
        "It was wrong",
    )
    assert context["left_out"] == {} and context["cut_note"] is None

    # A small briefing keeps the work whole and says plainly what it left out, notes included.
    small = (await ok(client, "next_work", within_node=epic["ref"], peek=True, context_chars=1500))[
        "context"
    ]
    assert small["left_out"].get("notes", 0) > 0 or small["left_out"].get("past_notes", 0) > 0
    assert "notes" in small["cut_note"] and "context_chars" in small["cut_note"]
    assert small["characters"] <= small["max_characters"] == 1500
    # What is kept is still the front of each section: the decision first.
    if small["notes"]:
        assert small["notes"][0]["id"] == decision["id"]


async def test_a_note_is_never_edited_a_replaced_one_points_at_its_replacement(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, node=story["ref"])
    about = await make_note(client, "What this is about")
    old = await make_note(
        client,
        "The limit is 10.",
        "The team call",
        nodes=[story["ref"]],
        rule_ids=[rule["id"]],
        note_ids=[about["id"]],
    )
    name = f"editor-{unique_word()}"
    async with connect_as(name) as editor:
        change = await replace(editor, old, text="The limit is 20.", why="Release notes, version 4")

    new = change["replacement"]
    assert change["superseded_ids"] == [old["id"]] and change["withdrawn_ids"] == []
    assert new["status"] == "unconfirmed" and new["supersedes_ids"] == [old["id"]]
    assert (new["text"], new["why"], new["recorded_by"]) == (
        "The limit is 20.",
        "Release notes, version 4",
        name,
    )
    # What the old one concerned carries over.
    assert (new["nodes"], new["rule_ids"], new["note_ids"]) == (
        [story["ref"]],
        [rule["id"]],
        [about["id"]],
    )
    gone = await get(client, old)
    assert (gone["status"], gone["superseded_by_id"], gone["status_reason"]) == (
        "superseded",
        new["id"],
        None,
    )
    # What it said and where it came from are kept exactly as they were.
    assert (gone["text"], gone["why"]) == ("The limit is 10.", "The team call")
    # It keeps the links it had, as a record.
    assert (gone["nodes"], gone["rule_ids"], gone["note_ids"]) == (
        [story["ref"]],
        [rule["id"]],
        [about["id"]],
    )
    assert (await every(client, node=story["ref"], status="in_force"))[0] == [new]

    events = await history(client, old)
    assert events[-1]["action"] == "note_superseded" and events[-1]["actor"] == name
    assert events[-1]["before"] == {"status": "unconfirmed", "superseded_by_id": None}
    assert events[-1]["after"] == {"status": "superseded", "superseded_by_id": new["id"]}
    new_events = await history(client, new)
    assert new_events[0]["action"] == "note_recorded"
    assert new_events[0]["after"]["supersedes_ids"] == [old["id"]]

    # The replacement can start from nothing, and can say what else it concerns.
    third = (
        await replace(
            client, new, keep_links=False, nodes=[story["parent"]], text="The limit is 30."
        )
    )["replacement"]
    assert (third["nodes"], third["rule_ids"], third["note_ids"]) == ([story["parent"]], [], [])
    # With the links kept, the ids it is given are added to them.
    fourth = (
        await replace(client, third, nodes=[story["ref"]], rule_ids=[rule["id"]], text="Forty.")
    )["replacement"]
    assert fourth["nodes"] == [story["parent"], story["ref"]] and fourth["rule_ids"] == [rule["id"]]

    # An ended note cannot be replaced again, and the refusal says what to do.
    message = await refused(client, "supersede_note", replaces=[old["id"]], text="t", why="w")
    assert f"Note {old['id']} was already superseded by note {new['id']}" in message


async def test_one_note_replaces_several_in_one_step_and_a_call_can_replace_thirty(
    client: Client,
) -> None:
    story, other = await make_stories(client, 2)
    rule = await make_rule(client, node=story["ref"])
    first = await make_note(client, "One", "w", nodes=[story["ref"]])
    second = await make_decision(client, "Two", rule_ids=[rule["id"]])
    third = await make_note(client, "Three", "w", nodes=[other["ref"]], project_wide=True)
    change = await replace(client, first, second, third, text="All of it", why="Merged")
    new = change["replacement"]
    assert change["superseded_ids"] == [first["id"], second["id"], third["id"]]
    assert new["supersedes_ids"] == [first["id"], second["id"], third["id"]]
    assert set(new["nodes"]) == {story["ref"], other["ref"]} and new["rule_ids"] == [rule["id"]]
    # Project-wide when any of them was; the new note is decided by nobody unless it says so.
    assert new["project_wide"] is True and new["decided"] is False
    for old in (first, second, third):
        gone = await get(client, old)
        assert (gone["status"], gone["superseded_by_id"]) == ("superseded", new["id"])

    many = [await make_note(client, f"Many {number}", "w") for number in range(30)]
    merged = await replace(client, *many, text="Thirty in one", why="Tidy")
    assert merged["superseded_ids"] == ids(many)
    assert merged["replacement"]["supersedes_ids"] == ids(many)


async def test_a_replacement_that_is_incomplete_or_wrong_changes_nothing(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    live = await make_note(client, "Still live", "w", nodes=[story["ref"]])
    ended = await withdraw(client, await make_note(client, "Already gone", "w"))
    ended_id = ended["withdrawn_ids"][0]
    before = await tally(client)

    for change, expected in (
        ({"replaces": []}, "Say what is being replaced"),
        ({"replaces": [live["id"], 987654321]}, "There is no note with id 987654321"),
        ({"replaces": [live["id"], ended_id]}, f"Note {ended_id} was withdrawn (It was wrong)"),
        ({"why": " "}, "The new note needs its reason: why cannot be empty"),
        ({"text": ""}, "The new note needs its text: text cannot be empty"),
        ({"nodes": ["story:987654321"]}, "There is no story 987654321 in this project"),
        ({"note_ids": [987654321]}, "There is no note with id 987654321"),
        ({"said_by": "Marie"}, "Give both said_by"),
        ({"decided_on": "2026-11-01"}, "decided_on needs decided_by"),
    ):
        call: dict[str, Any] = {"replaces": [live["id"]], "text": "New", "why": "Because", **change}
        assert expected in await refused(client, "supersede_note", **call)
    assert await tally(client) == before
    assert await get(client, live) == live
    assert (await every(client, node=story["ref"]))[0] == [live]


async def test_withdrawing_needs_a_reason_covers_several_and_keeps_the_record(
    client: Client,
) -> None:
    story = (await make_stories(client, 1))[0]
    rule = await make_rule(client, node=story["ref"])
    first = await make_note(client, "Wrong one", "w", nodes=[story["ref"]], rule_ids=[rule["id"]])
    second = await make_decision(client, "Wrong two")
    kept = await make_note(client, "Stays", "w", nodes=[story["ref"]])
    before = await tally(client)

    for change, expected in (
        ({"reason": " "}, "A withdrawal needs a reason"),
        ({"reason": ""}, "A withdrawal needs a reason"),
        ({"said_by": "Marie"}, "Give both said_by"),
        ({"quote": "She said so"}, "Give both said_by"),
        ({"note_ids": []}, "Say what is being withdrawn"),
        ({"note_ids": [first["id"], 987654321]}, "There is no note with id 987654321"),
    ):
        call: dict[str, Any] = {"note_ids": [first["id"]], "reason": "Not so", **change}
        assert expected in await refused(client, "withdraw_note", **call)
    assert await tally(client) == before

    name = f"remover-{unique_word()}"
    async with connect_as(name) as remover:
        change = await withdraw(remover, first, second, reason="It was never true")
    assert change["replacement"] is None
    assert change["withdrawn_ids"] == [first["id"], second["id"]] and change["superseded_ids"] == []
    for note in (first, second):
        gone = await get(client, note)
        assert (gone["status"], gone["status_reason"], gone["superseded_by_id"]) == (
            "withdrawn",
            "It was never true",
            None,
        )
        assert gone["text"] == note["text"]
        event = (await history(client, note))[-1]
        assert (event["action"], event["actor"]) == ("note_withdrawn", name)
        assert event["before"] == {"status": "unconfirmed"}
        assert event["after"] == {"status": "withdrawn", "reason": "It was never true"}
    # The record stays and keeps its links; what is left in force is what was not withdrawn.
    assert (await get(client, first))["rule_ids"] == [rule["id"]]
    assert (await every(client, node=story["ref"], status="withdrawn"))[0][0]["id"] == first["id"]
    assert ids((await every(client, node=story["ref"], status="in_force"))[0]) == [kept["id"]]
    # It cannot be withdrawn again, replaced, or linked to anything.
    assert "was withdrawn (It was never true), so it cannot be withdrawn" in await refused(
        client, "withdraw_note", note_ids=[first["id"]], reason="again"
    )
    assert "so it cannot be replaced" in await refused(
        client, "supersede_note", replaces=[first["id"]], text="t", why="w"
    )
    assert "It is withdrawn, so its links stay as they were" in await refused(
        client, "link_note", note_id=first["id"], nodes=[story["parent"]]
    )


async def test_two_replacements_of_the_same_note_at_once_let_only_one_win(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    for _ in range(4):
        old = await make_note(client, "Contested", "w", nodes=[story["ref"]])
        results = await race_results(
            client,
            "supersede_note",
            [{"replaces": [old["id"]], "text": f"Replacement {n}", "why": "r"} for n in range(6)],
        )
        assert [accepted for accepted, _ in results].count(True) == 1, results
        # Whoever loses is told so in plain words, not given a database error.
        for accepted, said in results:
            assert accepted or "already superseded by note" in said, said
        replacements = [
            n
            for n in (await every(client, status="in_force"))[0]
            if n["supersedes_ids"] == [old["id"]]
        ]
        assert len(replacements) == 1
        assert (await get(client, old))["superseded_by_id"] == replacements[0]["id"]


async def test_a_withdrawal_and_replacements_at_once_end_the_note_only_once(client: Client) -> None:
    for _ in range(4):
        old = await make_note(client, "Raced", "w")
        results = await race_mixed(
            client,
            [
                ("withdraw_note", {"note_ids": [old["id"]], "reason": "wrong"}),
                ("supersede_note", {"replaces": [old["id"]], "text": "a", "why": "w"}),
                ("supersede_note", {"replaces": [old["id"]], "text": "b", "why": "w"}),
                ("withdraw_note", {"note_ids": [old["id"]], "reason": "wrong again"}),
            ],
        )
        assert [accepted for accepted, _ in results].count(True) == 1, results
        final = await get(client, old)
        assert final["status"] in ("superseded", "withdrawn")
        ending = [e for e in await history(client, old) if e["action"] != "note_recorded"]
        assert len(ending) == 1


# -- confirmation: only a person ---------------------------------------------------------------


async def test_only_a_person_confirms_and_recording_a_note_never_confirms_it(
    client: Client,
) -> None:
    named = f"scribe-{unique_word()}"
    async with connect_as(named) as scribe:
        # Recording as an agent, whatever is claimed, is not a confirmation.
        note = await make_note(
            scribe, "Needs a look", "w", confirmed_by="Marie", status="confirmed"
        )
        assert (note["status"], note["confirmed_by"]) == ("unconfirmed", None)
        refusal = await refused(
            scribe, "set_confirmation", note_id=note["id"], confirmation="confirmed", person="Marie"
        )
        # An agent key can never be given this tool, so its own guard refuses it first (the check
        # that only a person confirms is also made inside the tool, for the board's calls).
        assert "is not allowed to use set_confirmation" in refusal
        assert "Nothing was changed" in refusal
    assert (await get(client, note))["status"] == "unconfirmed"
    before = await tally(client)

    for arguments, expected in (
        ({"person": " "}, "A confirmation needs the person who reviewed the note"),
        ({"note_id": 987654321}, "There is no note with id 987654321"),
        ({"confirmation": "maybe"}, "confirmation must be one of confirmed, unconfirmed"),
        ({"confirmation": "unconfirmed"}, "is not confirmed, so there is nothing to take away"),
    ):
        said = person_refused(
            "set_confirmation",
            **{"note_id": note["id"], "confirmation": "confirmed", "person": "Marie", **arguments},
        )
        assert expected in said, said
    assert await tally(client) == before

    status, reviewed = by_a_person(
        "set_confirmation",
        note_id=note["id"],
        confirmation="confirmed",
        person="Marie",
        quote="I read it",
    )
    assert status == 200, reviewed
    assert set(reviewed) == NOTE_FIELDS
    # Confirming is the note's one status changing, and who vouched is kept with it.
    assert (reviewed["status"], reviewed["confirmed_by"]) == ("confirmed", "Marie")
    assert reviewed["confirmed_at"]
    # Everything but the status and who confirmed it is as it was.
    changed_by_confirming = {"status", "confirmed_by", "confirmed_at"}
    assert {k: v for k, v in reviewed.items() if k not in changed_by_confirming} == {
        k: v for k, v in note.items() if k not in changed_by_confirming
    }
    assert await get(client, note) == reviewed
    assert reviewed["id"] in ids((await every(client, status="confirmed"))[0])
    assert reviewed["id"] in ids((await every(client, status="in_force"))[0])
    assert reviewed["id"] not in ids((await every(client, status="unconfirmed"))[0])

    # The change is logged with who made it (the person signed in), who the reviewer is, and when.
    event = (await history(client, note))[-1]
    assert (event["action"], event["actor"], event["actor_kind"]) == (
        "note_confirmed",
        the_project().owner.name,
        "person",
    )
    assert event["before"] == {"status": "unconfirmed", "confirmed_by": None}
    assert event["after"]["status"] == "confirmed"
    assert event["after"]["confirmed_by"] == "Marie" and event["after"]["quote"] == "I read it"
    assert instant(event["after"]["confirmed_at"]) == instant(reviewed["confirmed_at"])
    # Once confirmed, it cannot be confirmed again, by the same person or another.
    for person in ("Marie", "Ann"):
        said = person_refused(
            "set_confirmation", note_id=note["id"], confirmation="confirmed", person=person
        )
        assert f"Note {note['id']} was already confirmed by Marie" in said
    # The quote is optional when confirming.
    plain = confirm_on_board("note", await make_note(client, "Another", "w"), "Ann")
    assert plain["confirmed_by"] == "Ann"
    assert "quote" not in (await history(client, plain))[-1]["after"]


async def test_taking_a_confirmation_away_needs_the_person_and_their_words(client: Client) -> None:
    note = await make_decision(client, "Reviewed, then doubted")
    reviewed = confirm_on_board("note", note, "Marie")
    assert (reviewed["status"], reviewed["decided_by"]) == ("confirmed", "Marie")
    away: dict[str, Any] = {"note_id": note["id"], "confirmation": "unconfirmed"}
    before = await tally(client)

    assert "needs what the person said" in person_refused(
        "set_confirmation", person="Marie", **away
    )
    assert "needs what the person said" in person_refused(
        "set_confirmation", person="Marie", quote="  ", **away
    )
    assert "needs the person who says so" in person_refused(
        "set_confirmation", person=" ", quote="I no longer think so", **away
    )
    assert await get(client, note) == reviewed and await tally(client) == before

    status, open_again = by_a_person(
        "set_confirmation", person="Marie", quote="Standup: I am no longer sure", **away
    )
    assert status == 200
    assert (open_again["status"], open_again["confirmed_by"], open_again["confirmed_at"]) == (
        "unconfirmed",
        None,
        None,
    )
    event = (await history(client, note))[-1]
    assert (event["action"], event["actor_kind"]) == ("note_unconfirmed", "person")
    assert event["before"]["status"] == "confirmed"
    assert event["before"]["confirmed_by"] == "Marie" and event["before"]["confirmed_at"]
    assert event["after"] == {
        "status": "unconfirmed",
        "confirmed_by": None,
        "said_by": "Marie",
        "quote": "Standup: I am no longer sure",
    }
    # Nothing left to take away; and another person can confirm it afterwards.
    assert "nothing to take away" in person_refused(
        "set_confirmation", person="Marie", quote="again", **away
    )
    assert confirm_on_board("note", note, "Ann")["confirmed_by"] == "Ann"
    assert [e["action"] for e in await history(client, note)] == [
        "note_recorded",
        "note_confirmed",
        "note_unconfirmed",
        "note_confirmed",
    ]


async def test_an_ended_note_keeps_who_confirmed_it_and_cannot_be_confirmed_again(
    client: Client,
) -> None:
    replaced = confirm_on_board("note", await make_note(client, "Replaced one", "w"), "Marie")
    withdrawn = confirm_on_board("note", await make_note(client, "Withdrawn one", "w"), "Ann")
    never = await make_note(client, "Never confirmed", "w")
    new = (await replace(client, replaced, said_by="Marie", quote="Change it"))["replacement"]
    await withdraw(client, withdrawn, said_by="Ann", quote="It was wrong")
    await withdraw(client, never)
    # The new note starts unconfirmed, and who confirmed the old ones stays on them: their status
    # says they ended, and the record still shows they had been confirmed.
    assert new["status"] == "unconfirmed"
    old = await get(client, replaced)
    assert (old["status"], old["confirmed_by"]) == ("superseded", "Marie")
    assert old["confirmed_at"]
    ended = await get(client, withdrawn)
    assert (ended["status"], ended["confirmed_by"]) == ("withdrawn", "Ann")
    assert (await get(client, never))["confirmed_by"] is None
    # They show as confirmed once, for as long as they were in force, in the history.
    assert (await history(client, replaced))[-1]["before"]["status"] == "confirmed"
    assert (await history(client, never))[-1]["before"]["status"] == "unconfirmed"

    for note in (replaced, withdrawn):
        for confirmation in ("confirmed", "unconfirmed"):
            said = person_refused(
                "set_confirmation",
                note_id=note["id"],
                confirmation=confirmation,
                person="Zed",
                quote="q",
            )
            assert "so its confirmation cannot change" in said and "Nothing was changed" in said
    assert (await get(client, replaced)) == old and (await get(client, withdrawn)) == ended


async def test_a_confirmed_note_can_only_be_changed_with_that_persons_word(client: Client) -> None:
    story, _ = await make_stories(client, 2)
    note = await make_decision(client, "Confirmed choice", project_wide=True, nodes=[story["ref"]])
    reviewed = confirm_on_board("note", note, "Marie")
    before = await tally(client)
    events_before = await history(client, note)

    attempts: list[tuple[str, dict[str, Any]]] = [
        ("supersede_note", {"replaces": [note["id"]], "text": "t", "why": "w"}),
        ("withdraw_note", {"note_ids": [note["id"]], "reason": "r"}),
    ]
    for tool, arguments in attempts:
        # Not even an agent that goes by the person's own name: a name alone grants nothing.
        message = await refused(client, tool, **arguments)
        assert f"note {note['id']} (confirmed by Marie)" in message
        assert "needs that person's word" in message
        assert "said_by" in message and "quote" in message and "Nothing was changed" in message
        for partial in ({"said_by": "Marie"}, {"quote": "She said so"}):
            assert "Give both said_by" in await refused(client, tool, **arguments, **partial)
        assert await get(client, note) == reviewed
        assert await history(client, note) == events_before
        assert await tally(client) == before

    # With the person's name and words it goes through, and the history keeps both people.
    name = f"passer-{unique_word()}"
    async with connect_as(name) as passer:
        new = (
            await replace(
                passer,
                note,
                text="The revised choice",
                why="Marie changed her mind",
                said_by="Marie",
                quote="In the planning call: 'that is no longer how we think'",
            )
        )["replacement"]
    event = (await history(client, note))[-1]
    assert (event["action"], event["actor"]) == ("note_superseded", name)
    assert event["after"]["said_by"] == "Marie"
    assert event["after"]["quote"].startswith("In the planning call")
    assert new["status"] == "unconfirmed" and new["project_wide"] is True
    assert new["nodes"] == [story["ref"]]
    assert (await get(client, note))["confirmed_by"] == "Marie"

    # A note nobody confirmed can be withdrawn by an agent alone.
    unconfirmed = await make_note(client, "Just an idea", "w")
    await withdraw(client, unconfirmed)
    assert "said_by" not in (await history(client, unconfirmed))[-1]["after"]
    # And a confirmed one by the person's word, which the history keeps with the agent's name.
    third = confirm_on_board("note", await make_note(client, "Another confirmed", "w"), "Ann")
    await withdraw(client, third, said_by="Ann", quote="It was never right")
    event = (await history(client, third))[-1]
    assert (event["action"], event["after"]["said_by"]) == ("note_withdrawn", "Ann")
    assert event["after"]["quote"] == "It was never right"


async def race_people(calls: list[tuple[str, dict[str, Any]]]) -> list[tuple[int, dict[str, Any]]]:
    """The person signed in makes all these calls at the same moment, through the board."""
    results: list[tuple[int, dict[str, Any]]] = []

    async def one(tool: str, arguments: dict[str, Any]) -> None:
        results.append(await anyio.to_thread.run_sync(lambda: by_a_person(tool, **arguments)))

    async with anyio.create_task_group() as group:
        for tool, arguments in calls:
            group.start_soon(one, tool, arguments)
    return results


async def test_confirming_and_withdrawing_at_once_never_leave_a_withdrawn_note_confirmed(
    client: Client,
) -> None:
    for _ in range(4):
        note = await make_note(client, "Raced", "w")
        confirming = (
            "set_confirmation",
            {"note_id": note["id"], "confirmation": "confirmed", "person": "Marie"},
        )
        results = await race_people(
            [
                confirming,
                (
                    "withdraw_note",
                    {"note_ids": [note["id"]], "reason": "wrong", "said_by": "Marie", "quote": "q"},
                ),
            ]
        )
        assert {status for status, _ in results} <= {200, 400}
        final = await get(client, note)
        # Whichever came first, the database never holds both at once for a note that was ended in
        # the same step: a withdrawn note that was confirmed was confirmed before it was ended.
        events = [e["action"] for e in await history(client, note)]
        if final["status"] == "withdrawn" and final["confirmed_by"]:
            assert events.index("note_confirmed") < events.index("note_withdrawn")
        if final["status"] in ("unconfirmed", "confirmed"):
            assert events.count("note_withdrawn") == 0


async def test_two_confirmations_at_once_are_recorded_once(client: Client) -> None:
    note = await make_note(client, "Confirmed twice at once", "w")
    results = await race_people(
        [
            (
                "set_confirmation",
                {"note_id": note["id"], "confirmation": "confirmed", "person": name},
            )
            for name in ("Marie", "Ann", "Priya", "Tom")
        ]
    )
    assert sorted(status for status, _ in results) == [200, 400, 400, 400]
    assert [e["action"] for e in await history(client, note)].count("note_confirmed") == 1


# -- what a note concerns -----------------------------------------------------------------------


async def test_what_a_note_concerns_can_be_changed_while_it_is_in_force(client: Client) -> None:
    story, other_story = await make_stories(client, 2)
    rule = await make_rule(client, node=story["ref"])
    about = await make_note(client, "What it is about", "w")
    note = await make_note(client, "A note", "w", nodes=[story["ref"]])

    linked = await ok(
        client,
        "link_note",
        note_id=note["id"],
        nodes=[other_story["ref"]],
        rule_ids=[rule["id"]],
        note_ids=[about["id"]],
    )
    assert linked["nodes"] == [story["ref"], other_story["ref"]]
    assert (linked["rule_ids"], linked["note_ids"]) == ([rule["id"]], [about["id"]])
    assert await get(client, note) == linked
    assert [e["action"] for e in await history(client, note)] == ["note_recorded"] + [
        "note_linked"
    ] * 4

    unlinked = await ok(
        client, "unlink_note", note_id=note["id"], nodes=[story["ref"]], note_ids=[about["id"]]
    )
    assert (unlinked["nodes"], unlinked["rule_ids"], unlinked["note_ids"]) == (
        [other_story["ref"]],
        [rule["id"]],
        [],
    )
    gone = (await history(client, note))[-2:]
    assert [e["action"] for e in gone] == ["note_unlinked", "note_unlinked"]
    assert (gone[0]["node"], gone[0]["before"]) == (story["ref"], {"note_id": note["id"]})
    assert (gone[1]["other_note_id"], gone[1]["after"]) == (about["id"], {})
    # The note it was about has the link and the unlink in its history too.
    assert [e["action"] for e in await history(client, about)] == [
        "note_recorded",
        "note_linked",
        "note_unlinked",
    ]
    # The story it left no longer lists it, and the one it joined does.
    assert (await every(client, node=story["ref"]))[0] == []
    assert (await every(client, node=other_story["ref"]))[0] == [unlinked]
    # The node's own history shows the events that name it.
    node_events = (await ok(client, "node_history", node=story["ref"]))["items"]
    assert [e["action"] for e in node_events if e["note_id"] == note["id"]] == [
        "note_linked",
        "note_unlinked",
    ]


async def test_link_changes_that_make_no_sense_change_nothing(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, node=story["ref"])
    about = await make_note(client, "About", "w")
    note = await make_note(
        client, "Linked", "w", nodes=[story["ref"]], rule_ids=[rule["id"]], note_ids=[about["id"]]
    )
    fresh = await make_stories(client, 1)
    before = await tally(client)
    for tool, arguments, expected in (
        ("link_note", {"nodes": [story["ref"]]}, f"is already linked to {story['ref']}"),
        ("link_note", {"rule_ids": [rule["id"]]}, f"already linked to rule {rule['id']}"),
        ("link_note", {"note_ids": [about["id"]]}, f"already linked to note {about['id']}"),
        ("unlink_note", {"nodes": ["story:987654321"]}, "is not linked to story:987654321"),
        ("unlink_note", {"rule_ids": [987654321]}, "is not linked to rule 987654321"),
        ("link_note", {"nodes": ["story:987654321"]}, "There is no story 987654321"),
        (
            "link_note",
            {"rule_ids": [987654321]},
            "There is no acceptance criterion with id 987654321",
        ),
        ("link_note", {"note_ids": [987654321]}, "There is no note with id 987654321"),
        ("link_note", {"note_ids": [note["id"]]}, "cannot be about itself"),
        ("link_note", {}, "Nothing to link"),
        ("unlink_note", {"nodes": []}, "Nothing to link"),
        ("link_note", {"nodes": ["story"]}, "nodes must name an epic, story or task"),
    ):
        said = await refused(client, tool, note_id=note["id"], **arguments)
        assert expected in said, said
    # One wrong among right ones changes nothing at all.
    assert "already linked" in await refused(
        client, "link_note", note_id=note["id"], nodes=[fresh[0]["ref"], story["ref"]]
    )
    assert "There is no note with id 987654321" in await refused(
        client, "link_note", note_id=987654321, nodes=[story["ref"]]
    )
    assert await get(client, note) == note and await tally(client) == before


async def test_a_replaced_notes_links_are_the_record_of_what_it_concerned(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    old = await make_note(client, "Old", "w", nodes=[story["ref"]])
    new = (await replace(client, old))["replacement"]
    said = await refused(client, "link_note", note_id=old["id"], nodes=[story["parent"]])
    assert f"Note {old['id']} was already superseded by note {new['id']}" in said
    assert f"Change the links of note {new['id']}, which replaced it, instead" in said
    assert "keeps the links it had" in said
    # The database says the same when asked directly, as the role every project's work runs as.
    for statement in (
        f"INSERT INTO note_nodes (note_id, node) VALUES ({old['id']}, '{story['parent']}')",
        f"DELETE FROM note_nodes WHERE note_id = {old['id']}",
    ):
        result = run_sql(statement, as_project=True)
        assert result.returncode != 0
        assert "A superseded or withdrawn note keeps the links it had" in result.stderr
    assert (await get(client, old))["nodes"] == [story["ref"]]


async def test_what_concerns_an_area_is_found_with_one_walk(client: Client) -> None:
    epic, story, task = await make_tree(client)
    other_epic, other_story, _ = await make_tree(client)
    rule = await make_rule(client, node=story["ref"])
    on_epic = await make_note(client, "On the epic", "w", nodes=[epic["ref"]])
    on_story = await make_note(client, "On the story", "w", nodes=[story["ref"]])
    on_task = await make_note(client, "On the task", "w", nodes=[task["ref"]])
    on_rule = await make_note(client, "On the rule", "w", rule_ids=[rule["id"]])
    outside = await make_note(client, "Outside", "w", nodes=[other_story["ref"]])
    del other_epic

    area = ids((await every(client, within_node=epic["ref"]))[0])
    assert area == [on_epic["id"], on_story["id"], on_task["id"], on_rule["id"]]
    assert ids((await every(client, within_node=story["ref"]))[0]) == [
        on_story["id"],
        on_task["id"],
        on_rule["id"],
    ]
    assert ids((await every(client, within_node=task["ref"]))[0]) == [on_task["id"]]
    assert outside["id"] not in area
    # "Exactly this one" is stricter than the walk.
    assert ids((await every(client, node=story["ref"]))[0]) == [on_story["id"]]
    assert "There is no story 987654321" in await refused(
        client, "list_notes", within_node="story:987654321"
    )


# -- listing, history and the database ------------------------------------------------------------


async def test_listing_walks_every_page_once_and_the_count_matches(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    made = [
        await make_note(client, f"Listed {n}", "w", nodes=[story["ref"]], project_wide=n % 2 == 0)
        for n in range(7)
    ]
    await withdraw(client, made[0])
    confirm_on_board("note", made[1], "Marie")
    decided = await make_decision(client, "Decided and listed", nodes=[story["ref"]])

    everything, total = await every(client, node=story["ref"])
    assert ids(everything) == ids(made) + [decided["id"]] and total == 8
    # Walked in small pages, the same notes come once each and the total says so every time.
    walked: list[int] = []
    after: int | None = None
    pages = 0
    while True:
        page = await ok(client, "list_notes", node=story["ref"], limit=3, after_id=after)
        assert page["total"] == 8
        walked.extend(ids(page["items"]))
        pages += 1
        after = page["next_after_id"]
        if after is None:
            break
    assert walked == ids(everything) and pages == 3

    def only(**filters: Any) -> Any:
        return every(client, node=story["ref"], **filters)

    assert ids((await only(status="withdrawn"))[0]) == [made[0]["id"]]
    assert ids((await only(status="confirmed"))[0]) == [made[1]["id"]]
    assert ids((await only(decided=True))[0]) == [decided["id"]]
    assert (await only(decided=False))[1] == 7
    assert ids((await only(project_wide=True, status="in_force"))[0]) == [
        made[n]["id"] for n in (2, 4, 6)
    ]
    assert (await only(project_wide=False))[1] == 4
    assert ids((await only(status="unconfirmed", decided=False))[0]) == [
        made[n]["id"] for n in range(2, 7)
    ]
    # In force is unconfirmed and confirmed together: everything that was not withdrawn.
    assert ids((await only(status="in_force"))[0]) == ids(made[1:]) + [decided["id"]]

    for arguments, expected in (
        ({"limit": 0}, "limit must be a whole number from 1 to 200"),
        ({"limit": 201}, "limit must be a whole number from 1 to 200"),
        (
            {"status": "gone"},
            "status must be one of unconfirmed, confirmed, superseded, withdrawn, in_force",
        ),
        (
            {"status": "active"},
            "status must be one of unconfirmed, confirmed, superseded, withdrawn, in_force",
        ),
        ({"node": "story:987654321"}, "There is no story 987654321"),
        ({"rule_id": 987654321}, "There is no acceptance criterion with id 987654321"),
        ({"note_id": 987654321}, "There is no note with id 987654321"),
    ):
        assert expected in await refused(client, "list_notes", **arguments)


async def test_a_notes_history_lists_everything_in_order_with_who_said_what(
    client: Client,
) -> None:
    note = await make_note(client, "A history", "w")
    first = f"first-{unique_word()}"
    async with connect_as(first) as one:
        await ok(
            one, "link_note", note_id=note["id"], nodes=[(await make_stories(one, 1))[0]["ref"]]
        )
    confirm_on_board("note", note, "Marie", quote="Read it")
    async with connect_as(f"second-{unique_word()}") as two:
        change = await replace(two, note, said_by="Marie", quote="Changed my mind")
    events = await history(client, note)
    assert [e["action"] for e in events] == [
        "note_recorded",
        "note_linked",
        "note_confirmed",
        "note_superseded",
    ]
    assert [e["actor_kind"] for e in events] == ["agent", "agent", "person", "agent"]
    assert events[1]["actor"] == first
    assert (
        events[3]["after"]["said_by"] == "Marie"
        and events[3]["after"]["superseded_by_id"] == (change["replacement"]["id"])
    )
    assert ids(events) == sorted(ids(events))
    # A page at a time, in the same order, and bad asks refused.
    page = await ok(client, "note_history", note_id=note["id"], limit=2)
    assert ids(page["items"]) == ids(events[:2]) and page["next_after_id"] == events[1]["id"]
    rest = await ok(
        client, "note_history", note_id=note["id"], limit=2, after_id=page["next_after_id"]
    )
    assert ids(rest["items"]) == ids(events[2:]) and rest["next_after_id"] is None
    assert "There is no note with id 987654321" in await refused(
        client, "note_history", note_id=987654321
    )
    assert "limit must be a whole number" in await refused(
        client, "note_history", note_id=note["id"], limit=0
    )


async def test_the_database_refuses_to_edit_or_delete_a_note_whoever_asks(client: Client) -> None:
    note = await make_decision(client, "Fixed in stone")
    ended = await make_note(client, "Ended", "w")
    await withdraw(client, ended)
    wants = {
        f"UPDATE notes SET text = 'changed' WHERE id = {note['id']}": "A note cannot be edited",
        f"UPDATE notes SET why = 'changed' WHERE id = {note['id']}": "A note cannot be edited",
        f"UPDATE notes SET decided_by = 'Ann' WHERE id = {note['id']}": "A note cannot be edited",
        f"UPDATE notes SET project_wide = true WHERE id = {note['id']}": "A note cannot be edited",
        f"DELETE FROM notes WHERE id = {note['id']}": "permission denied for table notes",
        f"UPDATE notes SET confirmed_by = 'M', confirmed_at = now() WHERE id = {ended['id']}": (
            "A superseded or withdrawn note cannot change again"
        ),
        f"UPDATE notes SET status = 'withdrawn' WHERE id = {note['id']}": (
            "note_reason_matches_how_it_ended"
        ),
        # A note in force is confirmed exactly when a person is named, so neither is changed alone.
        f"UPDATE notes SET status = 'confirmed' WHERE id = {note['id']}": (
            "note_is_confirmed_exactly_when_a_person_confirmed"
        ),
        f"UPDATE notes SET confirmed_by = 'M', confirmed_at = now() WHERE id = {note['id']}": (
            "note_is_confirmed_exactly_when_a_person_confirmed"
        ),
        f"UPDATE notes SET status = 'withdrawn', status_reason = 'x', confirmed_by = 'M', "
        f"confirmed_at = now() WHERE id = {note['id']}": (
            "cannot be confirmed or unconfirmed in the same step that ends it"
        ),
        f"INSERT INTO note_notes (note_id, about_id) VALUES ({note['id']}, {note['id']})": (
            "note_is_not_about_itself"
        ),
    }
    for statement, expected in wants.items():
        result = run_sql(statement, as_project=True)
        assert result.returncode != 0, statement
        assert expected in result.stderr, (statement, result.stderr)
    # Deciding needs a day and a person together, and a note says something and why.
    for columns, values, expected in (
        ("decided_by", "'Ann'", "note_is_decided_by_someone_on_a_day"),
        ("decided_on", "'2026-11-01'", "note_is_decided_by_someone_on_a_day"),
        ("text", "' '", "note_text_is_not_blank"),
        ("why", "' '", "note_why_is_not_blank"),
    ):
        fields = {"text": "'t'", "why": "'w'", columns: values}
        insert = (
            f"INSERT INTO notes (text, why, recorded_by, recorded_by_kind"
            f"{'' if columns in ('text', 'why') else ', ' + columns}) VALUES ("
            f"{fields['text']}, {fields['why']}, 'a', 'agent'"
            f"{'' if columns in ('text', 'why') else ', ' + values})"
        )
        result = run_sql(insert, as_project=True)
        assert result.returncode != 0 and expected in result.stderr, (columns, result.stderr)
    assert await get(client, note) == note
    # Even the database's owner, who is not held back by the walls between projects, cannot
    # delete a note: the record is kept by the database itself.
    project_id = the_project().id
    owner = run_sql(
        f"DELETE FROM notes WHERE project_id = {project_id} AND id = {note['id']}", project=False
    )
    assert owner.returncode != 0 and "A note is a record and cannot be deleted" in owner.stderr
    assert await get(client, note) == note
    # What the project's role sees is this project's notes and no other's.
    counted = run_sql("SELECT count(*) FROM notes", plain=True, as_project=True)
    assert int(counted.stdout.strip()) == (await tally(client))[0]


# -- finding notes ------------------------------------------------------------------------------


async def test_notes_are_found_by_words_in_their_text_and_why_and_say_what_they_are(
    client: Client,
) -> None:
    word = unique_word()
    decision = await make_decision(client, f"Pick the {word} way")
    plain = await make_note(client, "A plain statement", f"Observed with {word} on a phone")
    story = (await make_stories(client, 1))[0]

    found = await ok(client, "search", query=word)
    assert found["counts_by_type"] == {"note": 2}
    results = {item["id"]: item for item in found["items"]}
    assert set(results) == {decision["id"], plain["id"]}
    chosen = results[decision["id"]]
    assert (chosen["item_type"], chosen["ref"]) == ("note", f"note:{decision['id']}")
    assert chosen["title"] == f"Pick the {word} way"
    assert chosen["status"] == "unconfirmed" and chosen["matched"] in ("words", "both")
    assert "label" not in chosen and "confirmation" not in chosen
    assert word in chosen["words_matched"] and "«" + word + "»" in chosen["snippet"]
    # A word only in the why is found too, and the statement is its title.
    assert results[plain["id"]]["title"] == "A plain statement"
    confirm_on_board("note", plain, "Marie")
    assert (await ok(client, "search", query=word, statuses=["confirmed"]))["total"] == 1
    assert (await ok(client, "search", query=word, statuses=["unconfirmed"]))["total"] == 1

    assert (await ok(client, "search", query=word, types=["note"]))["total"] == 2
    assert (await ok(client, "search", query=word, types=["story", "rule"]))["total"] == 0
    assert "types must be one of epic, story, task, rule, note" in await refused(
        client, "search", query=word, types=["decision"]
    )
    assert (await ok(client, "search", query=word, statuses=["withdrawn"]))["total"] == 0
    # The old words are not statuses any more.
    assert "statuses must be one of" in await refused(
        client, "search", query=word, statuses=["active"]
    )
    await withdraw(client, plain, said_by="Marie", quote="No")
    ended = await ok(client, "search", query=word, statuses=["withdrawn"])
    assert [item["id"] for item in ended["items"]] == [plain["id"]]
    assert ended["items"][0]["status"] == "withdrawn"
    assert ended["counts_by_status"] == {"unconfirmed": 1, "withdrawn": 1}
    # A note is not a story: nothing in the answer says otherwise.
    assert story["ref"] not in {item["ref"] for item in found["items"]}


async def test_a_reference_to_a_note_in_a_text_is_found_by_find_mentions(client: Client) -> None:
    word = unique_word()
    (story,) = await make_stories(client, 1)
    target = await make_decision(client, f"Target {word}")
    by_text = await make_note(client, f"See [[note:{target['id']}]] and [[{story['ref']}]]", "w")
    by_why = await make_note(client, f"Second {word}", f"As [[note:{target['id']}]] says")
    both = await make_note(
        client,
        f"Third [[note:{target['id']}]]",
        f"Also [[note:{target['id']}]] and [[note:987654]]",
    )
    selfish = await make_note(client, "Mentions itself [[note:1]]", "w")

    page = await ok(client, "find_mentions", note_id=target["id"], limit=50)
    sources = {item["source"]["id"]: item for item in page["items"]}
    assert set(sources) == {by_text["id"], by_why["id"], both["id"]}
    assert sources[by_text["id"]]["fields"] == ["text"]
    assert sources[by_why["id"]]["fields"] == ["why"]
    assert sources[both["id"]]["fields"] == ["text", "why"]
    assert sources[by_text["id"]]["source"]["title"] == by_text["headline"]
    assert sources[by_text["id"]]["target"] == {
        "item_type": "note",
        "ref": f"note:{target['id']}",
        "id": target["id"],
        "exists": True,
        "title": f"Target {word}",
    }
    assert selfish["id"] not in sources
    # What the story is mentioned by, and the references that name nothing are plainly missing.
    by_story = await ok(client, "find_mentions", node=story["ref"], limit=50)
    assert [m["source"]["ref"] for m in by_story["items"]] == [f"note:{by_text['id']}"]
    missing = await ok(client, "find_mentions", limit=200)
    nowhere = [m for m in missing["items"] if m["target"]["ref"] == "note:987654"]
    assert (
        nowhere
        and nowhere[0]["target"]["exists"] is False
        and nowhere[0]["target"]["title"] is None
    )
    # An old-style reference is only text: it names nothing now.
    old_style = await make_note(client, f"Old [[decision:{target['id']}]] style", "w")
    assert old_style["id"] not in {
        m["source"]["id"]
        for m in (await ok(client, "find_mentions", note_id=target["id"], limit=50))["items"]
    }
    assert "no note" not in "" and "There is no note with id 987654321" in await refused(
        client, "find_mentions", note_id=987654321
    )
    assert "at most one of node, rule_id or note_id" in await refused(
        client, "find_mentions", note_id=target["id"], node=story["ref"]
    )


async def test_the_server_counts_notes_and_the_feed_follows_one(client: Client) -> None:
    before = await ok(client, "server_status")
    assert "notes" in before["counts"] and "decisions" not in before["counts"]
    assert "facts" not in before["counts"]
    note = await make_note(client, "Counted", "w")
    after = await ok(client, "server_status")
    assert after["counts"]["notes"] == before["counts"]["notes"] + 1
    assert after["counts"]["events"] == before["counts"]["events"] + 1

    # The feed names notes by note_id (and, for a link between notes, other_note_id).
    about = await make_note(client, "About it", "w", note_ids=[note["id"]])
    feed = await ok(client, "changes_since", note_id=note["id"], limit=50)
    assert [(e["action"], e["note_id"], e["other_note_id"]) for e in feed["items"]] == [
        ("note_recorded", note["id"], None),
        ("note_linked", about["id"], note["id"]),
    ]
    assert "Give at most one of node, rule_id or note_id" in await refused(
        client, "changes_since", note_id=note["id"], rule_id=1
    )
    assert "There is no note with id 987654321" in await refused(
        client, "changes_since", note_id=987654321
    )
    newest = await ok(client, "changes_since", newest=True, limit=1)
    assert newest["items"][0]["action"] == "note_linked"


async def test_notes_are_in_the_semantic_index_and_found_by_meaning(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    first = await make_note(client, "Clients receive a bill by mail after charge", "w")
    second = await make_decision(client, "Customers get an invoice by email after payment")
    unrelated = await make_note(client, "The office kitchen has a new kettle", "w")
    linked = await make_note(client, "A client gets a bill by mail", "w", nodes=[story["ref"]])
    report = await reindexed(client)
    assert report["error"] is None

    page = await ok(client, "similar_items", note_id=first["id"], min_similarity=0.5, limit=50)
    found = {item["ref"]: item for item in page["items"]}
    assert f"note:{first['id']}" not in found
    assert f"note:{second['id']}" in found and found[f"note:{second['id']}"]["suggestion"] is True
    assert found[f"note:{second['id']}"]["status"] == "unconfirmed"
    assert "label" not in found[f"note:{second['id']}"]
    assert f"note:{unrelated['id']}" not in found
    assert page["index"]["meaning"] == "used"
    only = await ok(
        client, "similar_items", note_id=first["id"], types=["note"], min_similarity=0.5
    )
    assert {item["item_type"] for item in only["items"]} == {"note"}
    assert "Give exactly one of node, rule_id, note_id or text" in await refused(
        client, "similar_items", note_id=first["id"], text="x"
    )
    assert "There is no note with id 987654321" in await refused(
        client, "similar_items", note_id=987654321
    )
    assert (await ok(client, "server_status"))["index"]["waiting_to_index"] == 0

    # Recall adds what is alike but not linked, as suggestions: never what is linked already.
    answer = await ok(client, "recall", node=story["ref"], with_similar=True)
    assert ids(in_section(answer["notes"])) == [linked["id"]]
    similar = {item["ref"] for item in answer["similar"]["items"]}
    assert f"note:{linked['id']}" not in similar
    assert answer["similar"]["index"]["meaning"] == "used"
    assert all(item["suggestion"] for item in answer["similar"]["items"])


# -- notes belong to their project ----------------------------------------------------------------


async def test_a_project_wide_note_is_in_its_projects_briefing_and_recall_and_no_others() -> None:
    owner = register()
    mine, theirs = make_project(owner, "Mine"), make_project(owner, "Theirs")
    word = unique_word()
    async with (
        connect(mine.mcp_url, mine.key) as ours,
        connect(theirs.mcp_url, theirs.key) as others,
    ):
        for client, name in ((ours, "mine"), (others, "theirs")):
            await make_decision(client, f"An ordinary decision of {name}")
        wide = await make_decision(
            ours,
            f"Everything stays plain {word}",
            text=f"Applies to all of mine {word}",
            project_wide=True,
        )
        # Both projects number from 1, so the other project's own first note has the same id as
        # an ordinary note here, and nothing of one is found by the other.
        assert wide["id"] == 2
        assert (await ok(others, "list_notes"))["total"] == 1

        epic, _, _ = await make_tree(ours)
        other_epic, _, other_task = await make_tree(others)

        # Ours: in the briefing of work, and in the recall of an item nothing is linked to.
        work = await ok(ours, "next_work", within_node=epic["ref"], peek=True)
        context = work["context"]
        assert ids(context["project_wide_notes"]) == [wide["id"]]
        assert context["project_wide_notes"][0]["text"] == wide["text"]
        assert context["project_wide_notes"][0]["status"] == "unconfirmed"
        assert context["notes"] == []
        recalled = await ok(ours, "recall", node=epic["ref"])
        assert [e["note"] for e in recalled["project_wide_notes"]["items"]] == [wide]
        assert recalled["notes"]["items"] == []

        # Theirs has none: nothing of ours is in their briefing, recall, lists or search, and
        # ours is not theirs to read, confirm or end.
        their_work = await ok(others, "next_work", within_node=other_epic["ref"], peek=True)
        assert their_work["context"]["project_wide_notes"] == []
        their_recall = await ok(others, "recall", node=other_task["ref"])
        assert their_recall["project_wide_notes"]["items"] == []
        assert (await ok(others, "list_notes", project_wide=True))["total"] == 0
        assert (await ok(others, "search", query=word, mode="words"))["items"] == []
        assert "There is no note with id 2" in await refused(others, "get_note", note_id=2)
        assert "There is no note with id 2" in await refused(
            others, "withdraw_note", note_ids=[2], reason="Not mine"
        )
        for tool, arguments in (
            ("set_confirmation", {"confirmation": "confirmed", "person": "Marie"}),
        ):
            reply = theirs.owner.call(
                "POST", f"/board/api/projects/{theirs.id}/tool/{tool}", {"note_id": 2, **arguments}
            )
            assert reply.status == 400 and "There is no note with id 2" in str(reply.body)
        assert (await ok(ours, "get_note", note_id=wide["id"]))["status"] == "unconfirmed"

        # Theirs has its own, and it is theirs alone, though it has the same number.
        own = await make_decision(others, f"Theirs stays plain {word}", project_wide=True)
        assert own["id"] == 2 and own["text"] != wide["text"]
        theirs_now = await ok(others, "next_work", within_node=other_epic["ref"], peek=True)
        assert ids(theirs_now["context"]["project_wide_notes"]) == [own["id"]]
        ours_now = await ok(ours, "next_work", within_node=epic["ref"], peek=True)
        assert ours_now["context"]["project_wide_notes"][0]["text"] == wide["text"]

        # Ending ours takes it out of ours and changes nothing of theirs.
        await withdraw(ours, wide, reason="No longer")
        gone = await ok(ours, "next_work", within_node=epic["ref"], peek=True)
        assert gone["context"]["project_wide_notes"] == []
        assert (await ok(ours, "recall", node=epic["ref"]))["project_wide_notes"]["items"] == []
        still = await ok(others, "next_work", within_node=other_epic["ref"], peek=True)
        assert ids(still["context"]["project_wide_notes"]) == [own["id"]]

        # The database's own wall: the rows the project's role sees are the project's.
        mine_rows = run_sql(
            "SELECT count(*) FROM notes", plain=True, project=mine.id, as_project=True
        )
        theirs_rows = run_sql(
            "SELECT count(*) FROM notes", plain=True, project=theirs.id, as_project=True
        )
        assert (mine_rows.stdout.strip(), theirs_rows.stdout.strip()) == ("2", "2")
        nothing = run_sql("SELECT count(*) FROM notes", plain=True, project=False, as_project=True)
        assert nothing.stdout.strip() == "0"


async def test_notes_linked_to_items_with_the_same_number_stay_with_their_own_item() -> None:
    """In a new project the first epic, the first story and the first task are epic:1, story:1 and
    task:1: three items. What is linked to one of them, or written about it, is not found for
    another."""
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        epic, story, task = await make_tree(client)
        items = {"epic": epic, "story": story, "task": task}
        assert [item["ref"] for item in items.values()] == ["epic:1", "story:1", "task:1"]
        decisions = {
            kind: await make_decision(client, f"About the {kind}", nodes=[item["ref"]])
            for kind, item in items.items()
        }
        facts = {
            kind: await make_fact(client, f"A fact about the {kind}", nodes=[item["ref"]])
            for kind, item in items.items()
        }
        for kind, item in items.items():
            assert decisions[kind]["nodes"] == facts[kind]["nodes"] == [item["ref"]]
            assert (await every(client, node=item["ref"]))[0] == [decisions[kind], facts[kind]]
            history = (await ok(client, "node_history", node=item["ref"]))["items"]
            assert [e["note_id"] for e in history if e["action"] == "note_linked"] == [
                decisions[kind]["id"],
                facts[kind]["id"],
            ]
        # Everything under an item is its own area: the epic holds all three, the task only its own.
        for within, kinds in (
            ("epic:1", ["epic", "story", "task"]),
            ("story:1", ["story", "task"]),
            ("task:1", ["task"]),
        ):
            found = await every(client, within_node=within)
            wanted = [n for kind in kinds for n in (decisions[kind]["id"], facts[kind]["id"])]
            assert ids(found[0]) == sorted(wanted), within
        # A text that names one of them is a mention of that one only.
        await make_note(client, "See [[task:1]] and nothing else", "w")
        page = await ok(client, "find_mentions", node="task:1", limit=50)
        assert [m["source"]["item_type"] for m in page["items"]] == ["note"]
        assert (await ok(client, "find_mentions", node="story:1", limit=50))["items"] == []
        assert (await ok(client, "find_mentions", node="epic:1", limit=50))["items"] == []


# -- the whole graph moves as data ----------------------------------------------------------------


async def test_an_export_loads_into_an_empty_project_and_gives_the_same_file() -> None:
    source = make_project(register())
    async with connect(source.mcp_url, source.key) as client:
        world = await rich_world(client, project=source)
        file = await export_everything(client)
        # In small pages the same file comes out, notes and their cursor included.
        assert await export_everything(client, limit=4) == file
    assert file["version"] == 6
    assert file["counts"]["notes"] == len(file["notes"]) == 10
    assert set(file) == {
        "format",
        "version",
        "counts",
        "nodes",
        "links",
        "rules",
        "notes",
        "definition_of_done",
        "checks",
        "events",
    }
    assert len(file["definition_of_done"]) == 1
    # Sizes and the ticks of the checks travel with it, and come out the same.
    assert [node["size"] for node in file["nodes"] if node["kind"] == "epic"] == [None]
    assert sorted(node["size"] for node in file["nodes"] if node["size"]) == ["l", "m", "s"]
    assert len(file["checks"]) == 3
    notes = {note["id"]: note for note in file["notes"]}
    assert set(notes[world["decision1"]["id"]]) == {
        "id",
        "text",
        "why",
        "decided_by",
        "decided_on",
        "project_wide",
        "recorded_by",
        "recorded_by_kind",
        "recorded_at",
        "status",
        "superseded_by_id",
        "supersedes_ids",
        "status_reason",
        "confirmed_by",
        "confirmed_at",
        "nodes",
        "rule_ids",
        "note_ids",
    }
    old, new = notes[world["decision1"]["id"]], notes[world["decision2"]["id"]]
    assert (old["status"], old["superseded_by_id"], old["confirmed_by"]) == (
        "superseded",
        new["id"],
        "Marie",
    )
    assert new["supersedes_ids"] == [old["id"]] and new["decided_by"] == "Marie"
    assert notes[world["fact1"]["id"]]["note_ids"] == [world["decision2"]["id"]]
    assert notes[world["fact1"]["id"]]["decided_by"] is None
    # A note has one status, and who confirmed it is kept apart from it: a note in force is
    # confirmed exactly when somebody is named, and one that ended keeps who it was.
    assert [
        (notes[world[name]["id"]]["status"], notes[world[name]["id"]]["confirmed_by"])
        for name in ("wide", "fact1", "fact2", "fact5", "fact6")
    ] == [
        ("confirmed", "Marie"),
        ("confirmed", "Marie"),
        ("unconfirmed", None),
        ("withdrawn", None),
        ("withdrawn", "Marie"),
    ]
    assert {rule["status"] for rule in file["rules"]} == {"open", "met"}
    assert {event["action"] for event in file["events"]} >= {
        "note_recorded",
        "note_linked",
        "note_confirmed",
        "note_superseded",
        "note_withdrawn",
    }

    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        result = await ok(client, "import_graph", document=file)
        assert result["counts"]["notes"] == 10
        assert await export_everything(client) == file
        # The next note continues after the highest id, and a second import is refused.
        fresh = await make_note(client, "After the import", "w")
        assert fresh["id"] == max(notes) + 1
        assert "already holds data" in await refused(client, "import_graph", document=file)
        # What came with it works: the history of a replaced note, and recall.
        events = await history(client, {"id": world["decision1"]["id"]})
        assert [e["action"] for e in events][-1] == "note_superseded"
        recalled = await ok(client, "recall", node=world["story1"]["ref"])
        assert world["decision2"]["id"] in ids(in_section(recalled["notes"]))


async def test_an_export_that_is_wrong_is_refused_in_plain_words_and_loads_nothing() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as client:
        file = await export_everything(client)
        broken = {
            **file,
            "notes": [
                {**make_the_note(1), "decided_on": "2026-11-01"},
                {**make_the_note(2), "superseded_by_id": 3, "status": "superseded"},
                {**make_the_note(3), "note_ids": [3, 9], "supersedes_ids": []},
            ],
            "counts": None,
        }
        said = await refused(client, "import_graph", document=broken)
        assert "nothing was imported" in said
        assert "decided_by and decided_on go together" in said
        assert "supersedes_ids must be the notes whose superseded_by_id is 3, [2]" in said
        assert "a note cannot be about itself" in said and "note_ids=9 is not in the file" in said
        assert (await ok(client, "server_status"))["counts"]["notes"] == 0


def make_the_note(note_id: int) -> dict[str, Any]:
    return {
        "id": note_id,
        "text": f"note {note_id}",
        "why": "a chat",
        "decided_by": None,
        "decided_on": None,
        "project_wide": False,
        "recorded_by": "ann",
        "recorded_by_kind": "agent",
        "recorded_at": "2026-11-15T10:00:00.000000Z",
        "status": "unconfirmed",
        "superseded_by_id": None,
        "supersedes_ids": [],
        "status_reason": None,
        "confirmed_by": None,
        "confirmed_at": None,
        "nodes": [],
        "rule_ids": [],
        "note_ids": [],
    }
