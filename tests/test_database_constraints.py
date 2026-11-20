"""The database refuses bad data on its own, even if the server's own checks were bypassed.

These run SQL directly inside the real database container (see ./check.sh, which names the compose
project and password through the environment).
"""

import pytest
from mcp import Client

from tests.helpers import (
    confirm_on_board,
    connect_as,
    day,
    make_decision,
    make_fact,
    make_note,
    make_rule,
    make_stories,
    make_tasks,
    make_tree,
    needs_first,
    ok,
    run_sql,
    unique_word,
)
from tests.world import make_project, register, the_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# The tables about people and access. None of them is graph content: the role a project's work
# runs as cannot reach any of them, and (the three that name a project) they are held apart by
# that, not by the row filter the graph's tables have.
ACCOUNT_TABLES = (
    "accounts",
    "account_identities",
    "sessions",
    "email_tokens",
    "projects",
    "agent_keys",
    "project_members",
    "project_invites",
)


def mine() -> str:
    """What keeps a statement to the shared test project's rows. These statements run as the
    database's owner, who is not held back by the walls between projects, and every project
    numbers its rows from 1, so a bare `WHERE id = 5` would reach every project's row 5."""
    return f"project_id = {the_project().id}"


def new_node(
    kind: str,
    parent: str | None = None,
    title: str = "x",
    number: int | None = None,
    **more: str,
) -> str:
    """An INSERT of one node. Its number is the next one of its kind unless one is given, as the
    server's own insert does."""
    columns = ["kind", "number", "parent", "title", *more]
    values = [
        f"'{kind}'",
        f"next_id('{kind}')" if number is None else str(number),
        "NULL" if parent is None else f"'{parent}'",
        f"'{title}'",
        *(f"'{value}'" for value in more.values()),
    ]
    return f"INSERT INTO nodes ({', '.join(columns)}) VALUES ({', '.join(values)})"


def test_the_database_can_be_reached_directly() -> None:
    result = run_sql("SELECT count(*) FROM nodes", as_project=True)
    assert result.returncode == 0, result.stderr


async def test_the_database_refuses_a_hierarchy_that_is_not_epic_story_task(client: Client) -> None:
    epic, story, _ = await make_tree(client)
    attempts = {
        "a story with no parent": (new_node("story"), "node_has_a_parent_unless_epic"),
        "a task with no parent": (new_node("task"), "node_has_a_parent_unless_epic"),
        "an epic with a parent": (
            new_node("epic", epic["ref"]),
            "node_has_a_parent_unless_epic",
        ),
        "a task under an epic": (
            new_node("task", epic["ref"]),
            "node_parent_has_the_right_kind",
        ),
        "a story under a story": (
            new_node("story", story["ref"]),
            "node_parent_has_the_right_kind",
        ),
        "a story under an epic that does not exist": (
            new_node("story", "epic:987654321"),
            "node_parent_exists",
        ),
        "a story under a bare number": (
            new_node("story", str(epic["number"])),
            "node_parent_has_the_right_kind",
        ),
        "an unknown kind": (new_node("saga", epic["ref"], number=1), "node_kind_is_known"),
        "no number of its own": (
            new_node("epic", number=0),
            "node_number_starts_at_one",
        ),
        "a blank title": (new_node("epic", title="  "), "node_title_is_not_blank"),
        "the stored status ready": (
            f"UPDATE nodes SET status = 'ready', version = version + 1 "
            f"WHERE {mine()} AND ref = '{story['ref']}'",
            "node_status_is_known",
        ),
        "a story made its own parent": (
            f"UPDATE nodes SET parent = '{story['ref']}', version = version + 1 "
            f"WHERE {mine()} AND ref = '{story['ref']}'",
            "node_parent_has_the_right_kind",
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    after = await ok(client, "get_node", node=story["ref"])
    assert (after["status"], after["parent"], after["version"]) == ("ready", epic["ref"], 1)


async def test_the_database_keeps_a_number_once_in_its_kind_and_writes_the_reference_itself(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    attempts = {
        "a number that is already an epic's": (
            new_node("epic", number=epic["number"]),
            "duplicate key value violates unique constraint",
        ),
        "a number that is already a story's": (
            new_node("story", epic["ref"], number=story["number"]),
            "duplicate key value violates unique constraint",
        ),
        "a number that is already a task's": (
            new_node("task", story["ref"], number=task["number"]),
            "duplicate key value violates unique constraint",
        ),
        "a reference written by hand": (
            new_node("epic", ref="epic:999999999"),
            "cannot insert a non-DEFAULT value into column",
        ),
        "a position written by hand": (
            new_node("epic", kind_rank="1"),
            "cannot insert a non-DEFAULT value into column",
        ),
        "a reference changed afterwards": (
            f"UPDATE nodes SET ref = 'story:999999999', version = version + 1 "
            f"WHERE {mine()} AND ref = '{story['ref']}'",
            "can only be updated to DEFAULT",
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    # A number is only ever one kind's: the same number can be an epic, a story and a task. This
    # is tried in a transaction that is rolled back, so that no number is taken for good.
    shared = 7_000_000_001
    tried = run_sql(
        "BEGIN; "
        + "; ".join(
            (
                new_node("epic", number=shared),
                new_node("story", f"epic:{shared}", number=shared),
                new_node("task", f"story:{shared}", number=shared),
                "SELECT string_agg(ref, ',' ORDER BY kind_rank) "
                f"FROM nodes WHERE number = {shared}",
            )
        )
        + "; ROLLBACK",
        plain=True,
    )
    assert tried.returncode == 0, tried.stderr
    assert f"epic:{shared},story:{shared},task:{shared}" in tried.stdout
    gone = run_sql(f"SELECT count(*) FROM nodes WHERE number = {shared}", plain=True)
    assert gone.stdout.strip() == "0"
    assert (await ok(client, "get_node", node=story["ref"]))["number"] == story["number"]


async def test_the_database_refuses_a_kind_or_number_change_and_a_change_without_a_new_version(
    client: Client,
) -> None:
    _, story, _ = await make_tree(client)
    where = f"WHERE {mine()} AND ref = '{story['ref']}'"
    result = run_sql(f"UPDATE nodes SET kind = 'epic', parent = NULL {where}")
    assert "cannot change its kind or its number" in result.stderr
    result = run_sql(f"UPDATE nodes SET number = number + 1000000, version = version + 1 {where}")
    assert "cannot change its kind or its number" in result.stderr
    result = run_sql(f"UPDATE nodes SET title = 'quiet edit' {where}")
    assert "raise its version by exactly one" in result.stderr
    result = run_sql(f"UPDATE nodes SET title = 'x', version = 5 {where}")
    assert "raise its version by exactly one" in result.stderr
    after = await ok(client, "get_node", node=story["ref"])
    assert (after["title"], after["ref"]) == ("A story", story["ref"])


async def test_the_event_log_cannot_be_rewritten_and_nodes_with_history_cannot_be_deleted(
    client: Client,
) -> None:
    name = f"marie-{unique_word()}"
    async with connect_as(name) as marie:
        epic, story, _ = await make_tree(marie)
    assert (
        "append-only"
        in run_sql(
            f"UPDATE events SET actor = 'someone' WHERE {mine()} AND node = '{epic['ref']}'"
        ).stderr
    )
    assert (
        "append-only"
        in run_sql(f"DELETE FROM events WHERE {mine()} AND node = '{epic['ref']}'").stderr
    )
    assert (
        "violates foreign key constraint"
        in run_sql(f"DELETE FROM nodes WHERE {mine()} AND ref = '{story['ref']}'").stderr
    )
    assert (
        "violates check constraint"
        in run_sql(
            f"INSERT INTO events (node, action, actor, actor_kind, after) "
            f"VALUES ('{epic['ref']}', 'create', ' ', 'agent', '{{}}')"
        ).stderr
    )
    assert (
        "violates check constraint"
        in run_sql(
            f"INSERT INTO events (node, action, actor, actor_kind, after) "
            f"VALUES ('{epic['ref']}', 'create', 'x', 'robot', '{{}}')"
        ).stderr
    )
    assert (
        "violates foreign key constraint"
        in run_sql(
            "INSERT INTO events (node, action, actor, actor_kind, after) "
            "VALUES ('story:987654321', 'create', 'x', 'agent', '{}')"
        ).stderr
    )
    assert (
        "violates foreign key constraint"
        in run_sql(
            f"INSERT INTO events (node, other_node, action, actor, actor_kind, after) "
            f"VALUES ('{epic['ref']}', 'task:987654321', 'link_added', 'x', 'agent', '{{}}')"
        ).stderr
    )

    history = (await ok(client, "node_history", node=story["ref"]))["items"]
    assert [(event["actor"], event["actor_kind"]) for event in history] == [(name, "agent")]


async def test_the_database_refuses_a_start_date_after_the_target_date_on_its_own(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    task = await ok(
        client,
        "create_node",
        kind="task",
        title="Dated",
        parent=story["ref"],
        start_on=day(2),
        target_on=day(10),
    )
    attempts = {
        "a new node that starts after its target": new_node(
            "task", story["ref"], start_on=day(9), target_on=day(3)
        ),
        "a start moved past the target": (
            f"UPDATE nodes SET start_on = '{day(30)}', version = version + 1 "
            f"WHERE {mine()} AND ref = '{task['ref']}'"
        ),
        "a target moved before the start": (
            f"UPDATE nodes SET target_on = '{day(1)}', version = version + 1 "
            f"WHERE {mine()} AND ref = '{task['ref']}'"
        ),
    }
    for what, sql in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert "node_start_is_not_after_target" in result.stderr, f"{what}: {result.stderr}"

    # Either date alone, or none, is fine, and the same day for both.
    fine = run_sql(
        f"UPDATE nodes SET start_on = NULL, version = version + 1 "
        f"WHERE {mine()} AND ref = '{task['ref']}'"
    )
    assert fine.returncode == 0, fine.stderr
    unchanged = await ok(client, "get_node", node=task["ref"])
    assert (unchanged["start_on"], unchanged["target_on"]) == (None, day(10))


async def test_the_database_refuses_bad_links(client: Client) -> None:
    first, second = await make_tasks(client, 2)
    a, b = first["ref"], second["ref"]
    await needs_first(client, first, second)
    await ok(client, "add_link", source=a, target=b, link_type="relates_to")

    def link(source: str, target: str, link_type: str) -> str:
        return (
            "INSERT INTO links (source, target, link_type) "
            f"VALUES ('{source}', '{target}', '{link_type}')"
        )

    attempts = {
        "a link to the same node": (link(a, a, "relates_to"), "link_is_not_to_the_same_node"),
        "waiting for the same node": (link(a, a, "needs_first"), "link_is_not_to_the_same_node"),
        "an unknown link type": (link(a, b, "blocks"), "link_type_is_known"),
        "a link to a node that does not exist": (
            link(a, "task:987654321", "needs_first"),
            "link_ends_at_a_node",
        ),
        "a link from a node that does not exist": (
            link("story:987654321", b, "relates_to"),
            "link_starts_at_a_node",
        ),
        "a link that names a node by its number alone": (
            link(a, str(second["number"]), "relates_to"),
            "link_ends_at_a_node",
        ),
        "a circle of waiting": (link(b, a, "needs_first"), "circle of waiting"),
        "the same link twice": (link(a, b, "needs_first"), "link_is_not_repeated"),
        "a two-way link again in the other direction": (
            link(b, a, "relates_to"),
            "link_two_way_is_not_repeated",
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    links = (await ok(client, "list_links", node=a))["items"]
    assert sorted(link["link_type"] for link in links) == ["needs_first", "relates_to"]


async def test_the_database_refuses_a_circle_of_waiting_through_nodes_of_every_kind(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)

    def waits(source: dict[str, object], target: dict[str, object]) -> str:
        return (
            "INSERT INTO links (source, target, link_type) "
            f"VALUES ('{source['ref']}', '{target['ref']}', 'needs_first')"
        )

    for statement in (waits(task, story), waits(story, epic)):
        assert run_sql(statement).returncode == 0
    result = run_sql(waits(epic, task))
    assert result.returncode != 0
    assert "circle of waiting" in result.stderr
    assert f"{epic['ref']} would need {task['ref']}" in result.stderr
    assert (await ok(client, "get_node", node=task["ref"]))["waiting_on"] == [story["ref"]]


async def test_the_database_refuses_bad_claims_and_a_second_holder(client: Client) -> None:
    holder = f"ann-{unique_word()}"
    async with connect_as(holder) as ann:
        epic, story, task = await make_tree(ann)
        await ok(ann, "claim_node", node=task["ref"])

    def claim(node: str, actor: str, expires: str, kind: str = "agent") -> str:
        return (
            "INSERT INTO claims (node, actor, actor_kind, expires_at) "
            f"VALUES ('{node}', '{actor}', '{kind}', now() + interval '{expires}')"
        )

    attempts = {
        "a claim on an epic": (
            claim(epic["ref"], "bob", "1 hour"),
            "claim_is_for_a_story_or_task",
        ),
        "a claim on a node that does not exist": (
            claim("story:987654321", "bob", "1 hour"),
            "claim_is_on_a_real_node",
        ),
        "a claim that names its node by its number alone": (
            claim(str(task["number"]), "bob", "1 hour"),
            "claim_is_for_a_story_or_task",
        ),
        "a claim with no holder": (
            claim(story["ref"], " ", "1 hour"),
            "claim_actor_is_not_blank",
        ),
        "a claim held by neither a person nor an agent": (
            claim(story["ref"], "bob", "1 hour", kind="robot"),
            "claim_actor_kind_is_known",
        ),
        "a claim that is over before it starts": (
            claim(story["ref"], "bob", "-1 hour"),
            "claim_lease_is_in_the_future",
        ),
        "a second holder of one task": (
            claim(task["ref"], "bob", "1 hour"),
            'duplicate key value violates unique constraint "claims_pkey"',
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    held = await ok(client, "get_node", node=task["ref"])
    assert (held["claimed_by"], held["claimed_by_kind"]) == (holder, "agent")
    assert (await ok(client, "get_node", node=story["ref"]))["claimed_by"] is None
    result = run_sql(
        "INSERT INTO events (node, action, actor, actor_kind, after) "
        f"VALUES ('{task['ref']}', 'delete', 'x', 'agent', '{{}}')"
    )
    assert "event_action_is_known" in result.stderr


async def test_the_database_refuses_bad_rules_and_keeps_a_rule_in_one_column(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    rule = await make_rule(client, node=story["ref"])

    def new_rule(node_ref: str | None, title: str = "x", status: str = "open") -> str:
        node_sql = "NULL" if node_ref is None else f"'{node_ref}'"
        return f"INSERT INTO rules (node, title, status) VALUES ({node_sql}, '{title}', '{status}')"

    attempts = {
        "a rule on an epic": (new_rule(epic["ref"]), "rule_is_on_a_story_or_task"),
        "a rule on a story that does not exist": (
            new_rule("story:987654321"),
            "rule_belongs_to_a_node",
        ),
        "a rule on a task that does not exist": (
            new_rule("task:987654321"),
            "rule_belongs_to_a_node",
        ),
        "a rule on a story named by its number alone": (
            new_rule(str(story["number"])),
            "rule_is_on_a_story_or_task",
        ),
        "a rule with a blank title": (new_rule(None, title=" "), "rule_title_is_not_blank"),
        "a rule with an unknown status": (
            new_rule(None, status="held"),
            "rule_status_is_known",
        ),
        "moving a rule onto an epic": (
            f"UPDATE rules SET node = '{epic['ref']}', version = version + 1 "
            f"WHERE {mine()} AND id = {rule['id']}",
            "rule_is_on_a_story_or_task",
        ),
        "a change to a rule without a new version": (
            f"UPDATE rules SET title = 'quiet edit' WHERE {mine()} AND id = {rule['id']}",
            "raise its version by exactly one",
        ),
        "a rule event that names no rule": (
            f"INSERT INTO events (node, action, actor, actor_kind, after) "
            f"VALUES ('{story['ref']}', 'rule_moved', 'x', 'agent', '{{}}')",
            "event_names_what_it_is_about",
        ),
        "a node event that names a rule": (
            f"INSERT INTO events (node, rule_id, action, actor, actor_kind, after) "
            f"VALUES ('{story['ref']}', {rule['id']}, 'update', 'x', 'agent', '{{}}')",
            "event_names_what_it_is_about",
        ),
        "an event about nothing": (
            "INSERT INTO events (action, actor, actor_kind, after) "
            "VALUES ('update', 'x', 'agent', '{}')",
            "event_names_what_it_is_about",
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    unchanged = await ok(client, "get_rule", rule_id=rule["id"])
    assert (unchanged["node"], unchanged["title"], unchanged["version"]) == (
        story["ref"],
        "A rule",
        1,
    )

    # A rule is on a story or a task, and the one column that says which is on the rules table
    # itself. The only tables that name a rule are the links notes make and the event log: no table
    # pairs a rule with a node, so a rule cannot be on two at once.
    result = run_sql(
        "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'rule_id'",
        plain=True,
    )
    assert result.returncode == 0 and result.stdout.strip() == "events,note_rules"
    # A task can hold one: nothing in the database refuses a rule on a task.
    assert (await make_rule(client, node=task["ref"]))["node"] == task["ref"]


async def test_a_rule_event_cannot_be_rewritten_either(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, node=story["ref"])
    assert (
        "append-only"
        in run_sql(
            f"UPDATE events SET actor = 'someone' WHERE {mine()} AND rule_id = {rule['id']}"
        ).stderr
    )
    assert (
        "violates foreign key constraint"
        in run_sql(f"DELETE FROM rules WHERE {mine()} AND id = {rule['id']}").stderr
    )


def refused_by_the_database(attempts: dict[str, tuple[str, str]]) -> None:
    """Each statement must fail, and say what the database's own rule says."""
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"


def new_note(**changes: str) -> str:
    """An INSERT of one note, recorded by an agent, with what `changes` says instead."""
    fields = {"text": "x", "why": "x", "recorded_by": "x", "recorded_by_kind": "agent"}
    fields.update(changes)
    columns = ", ".join(fields)
    values = ", ".join("NULL" if value == "NULL" else f"'{value}'" for value in fields.values())
    return f"INSERT INTO notes ({columns}) VALUES ({values})"


def update_note(note: dict[str, object], assignments: str) -> str:
    return f"UPDATE notes SET {assignments} WHERE {mine()} AND id = {note['id']}"


async def test_the_database_keeps_notes_as_a_record(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, node=story["ref"])
    early = await make_fact(client, "Earlier than the rest")
    decision = await make_decision(client, "A decision")
    note = await make_note(
        client,
        "Kept",
        "Seen",
        nodes=[story["ref"]],
        rule_ids=[rule["id"]],
        note_ids=[decision["id"]],
    )
    confirmed = await make_fact(client, "A confirmed note")
    confirm_on_board("note", confirmed, "Marie")
    old = await make_fact(
        client,
        "Old",
        nodes=[story["ref"]],
        rule_ids=[rule["id"]],
        note_ids=[decision["id"]],
    )
    change = await ok(client, "supersede_note", replaces=[old["id"]], text="New", why="Changed")
    new = change["replacement"]
    withdrawn = await make_fact(client, "Wrong", nodes=[story["ref"]])
    await ok(client, "withdraw_note", note_ids=[withdrawn["id"]], reason="Wrong")
    ended = await make_decision(client, "Confirmed, then given up")
    confirm_on_board("note", ended, "Ann")
    await ok(
        client,
        "withdraw_note",
        note_ids=[ended["id"]],
        reason="Dropped",
        said_by="Ann",
        quote="Drop it",
    )
    later = await make_fact(client, "The newest one")

    attempts = {
        "an unknown status": (new_note(status="active"), "note_status_is_known"),
        "blank text": (new_note(text=" "), "note_text_is_not_blank"),
        "no text at all": (new_note(text="NULL"), "violates not-null constraint"),
        "a blank why": (new_note(why=" "), "note_why_is_not_blank"),
        "no why at all": (new_note(why="NULL"), "violates not-null constraint"),
        "nobody who recorded it": (new_note(recorded_by=" "), "note_recorded_by_is_not_blank"),
        "a recorder that is neither a person nor an agent": (
            new_note(recorded_by_kind="robot"),
            "note_recorded_by_kind_is_known",
        ),
        "a decider and no day": (
            new_note(decided_by="Ann"),
            "note_is_decided_by_someone_on_a_day",
        ),
        "a day and no decider": (
            new_note(decided_on="2026-11-01"),
            "note_is_decided_by_someone_on_a_day",
        ),
        "a blank decider": (
            new_note(decided_by=" ", decided_on="2026-11-01"),
            "note_decided_by_is_not_blank",
        ),
        "superseded by nothing": (
            new_note(status="superseded"),
            "note_is_superseded_exactly_when_replaced",
        ),
        "withdrawn with no reason": (
            new_note(status="withdrawn"),
            "note_reason_matches_how_it_ended",
        ),
        "withdrawn with a blank reason": (
            new_note(status="withdrawn", status_reason=" "),
            "note_reason_matches_how_it_ended",
        ),
        "a note in force with a reason for ending": (
            new_note(status_reason="because"),
            "note_reason_matches_how_it_ended",
        ),
        "a confirmation with a person and no time": (
            new_note(confirmed_by="Ann"),
            "note_confirmation_names_a_person_and_a_time",
        ),
        "replaced while still marked active": (
            update_note(early, f"superseded_by_id = {later['id']}"),
            "note_is_superseded_exactly_when_replaced",
        ),
        "replaced by an earlier note": (
            update_note(new, f"status = 'superseded', superseded_by_id = {old['id']}"),
            "note_is_replaced_by_a_later_one",
        ),
        "replaced by a note that does not exist": (
            update_note(note, "status = 'superseded', superseded_by_id = 987654321"),
            "violates foreign key constraint",
        ),
        "an edit to the text": (
            update_note(note, "text = 'something else'"),
            "A note cannot be edited",
        ),
        "an edit to why": (
            update_note(note, "why = 'something else'"),
            "A note cannot be edited",
        ),
        "an edit to who decided it": (
            update_note(decision, "decided_by = 'someone else'"),
            "A note cannot be edited",
        ),
        "an edit to the day it was decided": (
            update_note(decision, "decided_on = decided_on + 1"),
            "A note cannot be edited",
        ),
        "making a note a decision after the fact": (
            update_note(note, "decided_by = 'Ann', decided_on = current_date"),
            "A note cannot be edited",
        ),
        "an edit to whether it is project-wide": (
            update_note(note, "project_wide = NOT project_wide"),
            "A note cannot be edited",
        ),
        "an edit to who recorded it": (
            update_note(note, "recorded_by = 'someone else'"),
            "A note cannot be edited",
        ),
        "an edit to what kind of recorder it was": (
            update_note(note, "recorded_by_kind = 'person'"),
            "A note cannot be edited",
        ),
        "an edit to when it was recorded": (
            update_note(note, "recorded_at = recorded_at + interval '1 day'"),
            "A note cannot be edited",
        ),
        "deleting a note": (
            f"DELETE FROM notes WHERE {mine()} AND id = {note['id']}",
            "cannot be deleted",
        ),
        "deleting a note that has ended": (
            f"DELETE FROM notes WHERE {mine()} AND id = {withdrawn['id']}",
            "cannot be deleted",
        ),
        "bringing a superseded note back": (
            update_note(old, "status = 'unconfirmed', superseded_by_id = NULL"),
            "A superseded or withdrawn note cannot change again",
        ),
        "pointing a superseded note at another replacement": (
            update_note(old, f"superseded_by_id = {later['id']}"),
            "A superseded or withdrawn note cannot change again",
        ),
        "bringing a withdrawn note back": (
            update_note(withdrawn, "status = 'confirmed', status_reason = NULL"),
            "A superseded or withdrawn note cannot change again",
        ),
        "changing why a note was withdrawn": (
            update_note(withdrawn, "status_reason = 'another reason'"),
            "A superseded or withdrawn note cannot change again",
        ),
        "a confirmation with a person and no time, afterwards": (
            update_note(early, "confirmed_by = 'Ann'"),
            "note_confirmation_names_a_person_and_a_time",
        ),
        "a confirmation with a time and no person": (
            update_note(early, "confirmed_at = now()"),
            "note_confirmation_names_a_person_and_a_time",
        ),
        "a confirmation by a blank name": (
            update_note(early, "confirmed_by = ' ', confirmed_at = now()"),
            "note_confirmation_names_a_person_and_a_time",
        ),
        "taking away only the person of a confirmation": (
            update_note(confirmed, "confirmed_by = NULL"),
            "note_confirmation_names_a_person_and_a_time",
        ),
        # A note in force is confirmed exactly when a person is named, whichever way it is asked.
        "a confirmed note that names nobody": (
            update_note(early, "status = 'confirmed'"),
            "note_is_confirmed_exactly_when_a_person_confirmed",
        ),
        "a new note that is confirmed and names nobody": (
            new_note(status="confirmed"),
            "note_is_confirmed_exactly_when_a_person_confirmed",
        ),
        "an unconfirmed note that names who confirmed it": (
            update_note(early, "confirmed_by = 'Ann', confirmed_at = now()"),
            "note_is_confirmed_exactly_when_a_person_confirmed",
        ),
        "taking the confirmation away and leaving the status confirmed": (
            update_note(confirmed, "confirmed_by = NULL, confirmed_at = NULL"),
            "note_is_confirmed_exactly_when_a_person_confirmed",
        ),
        "taking the status away and leaving who confirmed it": (
            update_note(confirmed, "status = 'unconfirmed'"),
            "note_is_confirmed_exactly_when_a_person_confirmed",
        ),
        "confirming a note in the step that withdraws it": (
            update_note(
                early,
                "status = 'withdrawn', status_reason = 'r', confirmed_by = 'Ann', "
                "confirmed_at = now()",
            ),
            "cannot be confirmed or unconfirmed in the same step that ends it",
        ),
        "unconfirming a note in the step that withdraws it": (
            update_note(
                confirmed,
                "status = 'withdrawn', status_reason = 'r', confirmed_by = NULL, "
                "confirmed_at = NULL",
            ),
            "cannot be confirmed or unconfirmed in the same step that ends it",
        ),
        "confirming a note that was withdrawn": (
            update_note(withdrawn, "confirmed_by = 'Ann', confirmed_at = now()"),
            "A superseded or withdrawn note cannot change again",
        ),
        "taking the confirmation from a note that was withdrawn": (
            update_note(ended, "confirmed_by = NULL, confirmed_at = NULL"),
            "A superseded or withdrawn note cannot change again",
        ),
        "confirming a note that was superseded": (
            update_note(old, "confirmed_by = 'Ann', confirmed_at = now()"),
            "A superseded or withdrawn note cannot change again",
        ),
        "a link to a node that does not exist": (
            f"INSERT INTO note_nodes (note_id, node) VALUES ({note['id']}, 'task:987654321')",
            "violates foreign key constraint",
        ),
        "a link to a node named by its number alone": (
            f"INSERT INTO note_nodes (note_id, node) VALUES ({note['id']}, '{story['number']}')",
            "violates foreign key constraint",
        ),
        "a link to a rule that does not exist": (
            f"INSERT INTO note_rules (note_id, rule_id) VALUES ({note['id']}, 987654321)",
            "violates foreign key constraint",
        ),
        "a link to a note that does not exist": (
            f"INSERT INTO note_notes (note_id, about_id) VALUES ({note['id']}, 987654321)",
            "violates foreign key constraint",
        ),
        "a note about itself": (
            f"INSERT INTO note_notes (note_id, about_id) VALUES ({note['id']}, {note['id']})",
            "note_is_not_about_itself",
        ),
        "a link from a note that does not exist": (
            f"INSERT INTO note_nodes (note_id, node) VALUES (987654321, '{story['ref']}')",
            "violates foreign key constraint",
        ),
        "the same node link twice": (
            f"INSERT INTO note_nodes (note_id, node) VALUES ({note['id']}, '{story['ref']}')",
            "note_nodes_pkey",
        ),
        "the same rule link twice": (
            f"INSERT INTO note_rules (note_id, rule_id) VALUES ({note['id']}, {rule['id']})",
            "note_rules_pkey",
        ),
        "the same note link twice": (
            f"INSERT INTO note_notes (note_id, about_id) VALUES ({note['id']}, {decision['id']})",
            "note_notes_pkey",
        ),
        "a new node link on a superseded note": (
            f"INSERT INTO note_nodes (note_id, node) VALUES ({old['id']}, '{story['parent']}')",
            "keeps the links it had",
        ),
        "removing a node link from a superseded note": (
            f"DELETE FROM note_nodes WHERE {mine()} AND note_id = {old['id']}",
            "keeps the links it had",
        ),
        "removing a rule link from a superseded note": (
            f"DELETE FROM note_rules WHERE {mine()} AND note_id = {old['id']}",
            "keeps the links it had",
        ),
        "removing a note link from a superseded note": (
            f"DELETE FROM note_notes WHERE {mine()} AND note_id = {old['id']}",
            "keeps the links it had",
        ),
        "a new link on a withdrawn note": (
            f"INSERT INTO note_nodes (note_id, node) "
            f"VALUES ({withdrawn['id']}, '{story['parent']}')",
            "keeps the links it had",
        ),
        "removing a link from a withdrawn note": (
            f"DELETE FROM note_nodes WHERE {mine()} AND note_id = {withdrawn['id']}",
            "keeps the links it had",
        ),
        "a note event that names no note": (
            "INSERT INTO events (action, actor, actor_kind, after) "
            "VALUES ('note_recorded', 'x', 'agent', '{}')",
            "event_names_what_it_is_about",
        ),
        "a note event that names a note that does not exist": (
            "INSERT INTO events (note_id, action, actor, actor_kind, after) "
            "VALUES (987654321, 'note_recorded', 'x', 'agent', '{}')",
            "violates foreign key constraint",
        ),
        "a node event that names a note": (
            f"INSERT INTO events (node, note_id, action, actor, actor_kind, after) "
            f"VALUES ('{story['ref']}', {note['id']}, 'update', 'x', 'agent', '{{}}')",
            "event_names_what_it_is_about",
        ),
        "a node event that names the other end of a note link": (
            f"INSERT INTO events (node, other_note_id, action, actor, actor_kind, after) "
            f"VALUES ('{story['ref']}', {note['id']}, 'update', 'x', 'agent', '{{}}')",
            "event_names_what_it_is_about",
        ),
        "a rule event that names a note": (
            f"INSERT INTO events (rule_id, note_id, action, actor, actor_kind, after) "
            f"VALUES ({rule['id']}, {note['id']}, 'rule_updated', 'x', 'agent', '{{}}')",
            "event_names_what_it_is_about",
        ),
        "an event of the old decisions": (
            f"INSERT INTO events (node, action, actor, actor_kind, after) "
            f"VALUES ('{story['ref']}', 'decision_recorded', 'x', 'agent', '{{}}')",
            "event_action_is_known",
        ),
        "an event of the old facts": (
            f"INSERT INTO events (node, action, actor, actor_kind, after) "
            f"VALUES ('{story['ref']}', 'fact_recorded', 'x', 'agent', '{{}}')",
            "event_action_is_known",
        ),
        "an event of the old memory": (
            f"INSERT INTO events (node, action, actor, actor_kind, after) "
            f"VALUES ('{story['ref']}', 'memory_recorded', 'x', 'agent', '{{}}')",
            "event_action_is_known",
        ),
    }
    refused_by_the_database(attempts)

    # None of it changed anything.
    assert await ok(client, "get_note", note_id=note["id"]) == note
    replaced = await ok(client, "get_note", note_id=old["id"])
    assert (replaced["status"], replaced["superseded_by_id"], replaced["nodes"]) == (
        "superseded",
        new["id"],
        [story["ref"]],
    )
    assert (replaced["rule_ids"], replaced["note_ids"]) == ([rule["id"]], [decision["id"]])
    assert (await ok(client, "get_note", note_id=withdrawn["id"]))["status"] == "withdrawn"
    assert (await ok(client, "get_note", note_id=confirmed["id"]))["confirmed_by"] == "Marie"
    gone = await ok(client, "get_note", note_id=ended["id"])
    assert (gone["status"], gone["confirmed_by"]) == ("withdrawn", "Ann")
    assert (await ok(client, "get_note", note_id=decision["id"]))["decided_by"] == "Marie"
    assert (await ok(client, "get_note", note_id=early["id"]))["confirmed_by"] is None

    # While a note is in force only its status between unconfirmed and confirmed, with who
    # confirmed it, can change, and the database allows that.
    changed = run_sql(
        update_note(later, "status = 'confirmed', confirmed_by = 'Bob', confirmed_at = now()")
    )
    assert changed.returncode == 0, changed.stderr
    assert (await ok(client, "get_note", note_id=later["id"]))["status"] == "confirmed"
    cleared = run_sql(
        update_note(later, "status = 'unconfirmed', confirmed_by = NULL, confirmed_at = NULL")
    )
    assert cleared.returncode == 0, cleared.stderr
    after = await ok(client, "get_note", note_id=later["id"])
    assert (after["status"], after["confirmed_by"]) == ("unconfirmed", None)


async def test_a_note_event_cannot_be_rewritten_either(client: Client) -> None:
    note = await make_note(client, "Logged", "To see")
    assert (
        "append-only"
        in run_sql(
            f"UPDATE events SET actor = 'someone' WHERE {mine()} AND note_id = {note['id']}"
        ).stderr
    )
    assert (
        "append-only"
        in run_sql(f"DELETE FROM events WHERE {mine()} AND note_id = {note['id']}").stderr
    )


async def test_a_projects_own_role_cannot_delete_a_note_or_rewrite_the_log(
    client: Client,
) -> None:
    note = await make_fact(client, "Kept")
    decision = await make_decision(client, "Kept too")
    other_project = the_project().id + 100_000

    # What the server itself runs as is not even allowed to try.
    for sql in (
        f"DELETE FROM notes WHERE id = {note['id']}",
        f"DELETE FROM notes WHERE id = {decision['id']}",
        "UPDATE events SET actor = 'someone'",
        "DELETE FROM events",
    ):
        result = run_sql(sql, as_project=True)
        assert result.returncode != 0, f"The project's role was allowed to run: {sql}"
        assert "permission denied" in result.stderr, f"{sql}: {result.stderr}"
    # It may confirm a note, as the server does for a person, but the database still holds it to
    # the record: what a note says cannot be rewritten.
    rewritten = run_sql(
        f"UPDATE notes SET text = 'rewritten' WHERE id = {note['id']}", as_project=True
    )
    assert rewritten.returncode != 0 and "A note cannot be edited" in rewritten.stderr
    # It sees its own notes and what they concern, and only its own.
    for table in ("notes", "note_nodes", "note_rules", "note_notes", "note_state"):
        result = run_sql(
            f"SELECT count(*) FILTER (WHERE project_id <> current_project()) FROM {table}",
            plain=True,
            as_project=True,
        )
        assert result.returncode == 0 and result.stdout.strip() == "0", f"{table}: {result.stderr}"
    nothing = run_sql(
        f"SELECT count(*) FROM notes WHERE id = {note['id']}",
        plain=True,
        project=None,
        as_project=True,
    )
    assert nothing.returncode == 0 and nothing.stdout.strip() == "0", nothing.stderr
    # It cannot write a note, or what a note concerns, into another project.
    for sql in (
        f"INSERT INTO notes (project_id, id, text, why, recorded_by, recorded_by_kind) "
        f"VALUES ({other_project}, 1, 'forged', 'forged', 'x', 'agent')",
        f"INSERT INTO note_notes (project_id, note_id, about_id) VALUES ({other_project}, 1, 2)",
    ):
        forged = run_sql(sql, as_project=True)
        assert forged.returncode != 0 and "row-level security" in forged.stderr, forged.stderr
    assert (await ok(client, "get_note", note_id=note["id"]))["text"] == "Kept"
    assert (await ok(client, "get_note", note_id=decision["id"]))["headline"] == "Kept too"


def test_a_projects_role_cannot_reach_the_tables_about_people_and_access() -> None:
    for table in ACCOUNT_TABLES:
        for sql in (f"SELECT count(*) FROM {table}", f"DELETE FROM {table}"):
            result = run_sql(sql, as_project=True)
            assert result.returncode != 0, f"The project's role was allowed to run: {sql}"
            assert "permission denied" in result.stderr, f"{sql}: {result.stderr}"
    # Not by a right of any kind, and not by reading a graph table's view of one.
    result = run_sql(
        "SELECT string_agg(c.relname, ', ' ORDER BY c.relname) FROM pg_class c "
        "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'v') "
        "AND has_table_privilege('vrum_project', c.oid, "
        "'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')",
        plain=True,
        project=None,
    )
    assert result.returncode == 0, result.stderr
    reachable = set(result.stdout.strip().split(", "))
    assert reachable.isdisjoint(ACCOUNT_TABLES), reachable & set(ACCOUNT_TABLES)
    assert {
        "nodes",
        "links",
        "claims",
        "rules",
        "notes",
        "note_nodes",
        "note_rules",
        "note_notes",
        "events",
    } <= reachable


def test_the_project_is_part_of_every_key_and_every_work_table_is_walled() -> None:
    # Every reference between two tables that hold a project's work names the project too, so a
    # note, a link or an event cannot refer to a row of another project. (The tables about people
    # and access name a project too, but they are not a project's work: the project's own role
    # cannot reach them at all.)
    people = ", ".join(f"'{table}'" for table in ACCOUNT_TABLES)
    result = run_sql(
        "SELECT string_agg(c.conrelid::regclass::text || '.' || c.conname, ', ' "
        "ORDER BY c.conname) FROM pg_constraint c "
        "WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace "
        "AND c.confrelid <> 'projects'::regclass "
        f"AND c.conrelid::regclass::text NOT IN ({people}) "
        "AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.conrelid "
        "AND a.attname = 'project_id' AND NOT a.attisdropped) "
        "AND NOT EXISTS (SELECT 1 FROM unnest(c.conkey) k JOIN pg_attribute a "
        "ON a.attrelid = c.conrelid AND a.attnum = k WHERE a.attname = 'project_id')",
        plain=True,
        project=None,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"These references do not name the project: {result.stdout}"

    # Everything that points at a note is in that walk, and names the project: the notes and what
    # they concern, and the events about them.
    result = run_sql(
        "SELECT string_agg(DISTINCT c.conrelid::regclass::text, ', ' "
        "ORDER BY c.conrelid::regclass::text) FROM pg_constraint c "
        "WHERE c.contype = 'f' AND c.confrelid = 'notes'::regclass "
        "AND EXISTS (SELECT 1 FROM unnest(c.conkey) k JOIN pg_attribute a "
        "ON a.attrelid = c.conrelid AND a.attnum = k WHERE a.attname = 'project_id')",
        plain=True,
        project=None,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "events, note_nodes, note_notes, note_rules, notes"
    result = run_sql(
        "SELECT count(*) FROM pg_constraint c WHERE c.contype = 'f' "
        "AND c.confrelid = 'notes'::regclass "
        "AND NOT EXISTS (SELECT 1 FROM unnest(c.conkey) k JOIN pg_attribute a "
        "ON a.attrelid = c.conrelid AND a.attnum = k WHERE a.attname = 'project_id')",
        plain=True,
        project=None,
    )
    assert result.returncode == 0 and result.stdout.strip() == "0", result.stderr

    # And every table of a project's work shows only that project's rows to the project's role.
    result = run_sql(
        "SELECT string_agg(c.relname, ', ' ORDER BY c.relname) FROM pg_class c "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'project_id' "
        "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' "
        f"AND c.relname NOT IN ({people}) "
        "AND NOT (c.relrowsecurity "
        "AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid))",
        plain=True,
        project=None,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"These tables have no row filter: {result.stdout}"
    result = run_sql(
        "SELECT string_agg(c.relname, ', ' ORDER BY c.relname) FROM pg_class c "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'project_id' "
        "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' "
        "AND c.relrowsecurity AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)",
        plain=True,
        project=None,
    )
    assert result.returncode == 0, result.stderr
    assert {
        "nodes",
        "links",
        "claims",
        "rules",
        "notes",
        "note_nodes",
        "note_rules",
        "note_notes",
        "events",
    } <= set(result.stdout.strip().split(", "))


def test_the_schema_holds_notes_and_neither_of_the_two_records_they_replaced() -> None:
    tables = (
        run_sql(
            "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables "
            "WHERE table_schema = 'public'",
            plain=True,
            project=None,
        )
        .stdout.strip()
        .split(",")
    )
    assert {"notes", "note_nodes", "note_rules", "note_notes", "note_state"} <= set(tables)
    for gone in (
        "decisions",
        "decision_nodes",
        "decision_rules",
        "decision_state",
        "facts",
        "fact_nodes",
        "fact_rules",
        "fact_decisions",
        "fact_state",
        "memories",
        "memory_nodes",
        "memory_rules",
        "memory_decisions",
        "belief_evidence",
        "memory_state",
        "model_calls",
    ):
        assert gone not in tables, f"{gone} is still in the database"

    # No column of the old taxonomy is left: no kind of note, no title, no origin, no promotion,
    # and nothing that names a decision or a fact (an event names a note, and the other note).
    result = run_sql(
        "SELECT string_agg(table_name || '.' || column_name, ', ' ORDER BY table_name) "
        "FROM information_schema.columns WHERE table_schema = 'public' AND ("
        "column_name IN ('memory_id', 'promoted_from_id', 'origin', 'happened_on', 'type', "
        "'decision_id', 'fact_id') "
        "OR (table_name IN ('notes', 'note_state') "
        "AND column_name IN ('kind', 'title', 'reason', 'source')))",
        plain=True,
        project=None,
    )
    assert result.returncode == 0 and result.stdout.strip() == "", result.stdout
    result = run_sql(
        "SELECT string_agg(column_name, ',' ORDER BY column_name) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'events' "
        "AND column_name IN ('note_id', 'other_note_id')",
        plain=True,
        project=None,
    )
    assert result.stdout.strip() == "note_id,other_note_id", result.stderr


def test_an_epic_story_or_task_has_no_integer_id_anywhere_in_the_database() -> None:
    # A node is named by its kind and number, held as a reference (text) everywhere it is pointed
    # at. No table or view has an integer id for it, and nothing points at one by number alone.
    result = run_sql(
        "SELECT string_agg(table_name || '.' || column_name, ', ' "
        "ORDER BY table_name, column_name) "
        "FROM information_schema.columns WHERE table_schema = 'public' AND ("
        "column_name IN ('node_id', 'parent_id', 'other_node_id', 'source_id', 'target_id', "
        "'story_id', 'node_kind', 'new_story_id', 'within_node_id') "
        "OR (table_name IN ('nodes', 'node_state') AND column_name = 'id'))",
        plain=True,
        project=None,
    )
    assert result.returncode == 0 and result.stdout.strip() == "", result.stdout

    result = run_sql(
        "SELECT string_agg(table_name || '.' || column_name || ':' || data_type, ', ' "
        "ORDER BY table_name, column_name) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name IN ('links', 'claims', 'rules', "
        "'note_nodes', 'events', 'nodes') "
        "AND column_name IN ('source', 'target', 'node', 'other_node', 'parent')",
        plain=True,
        project=None,
    )
    assert result.returncode == 0, result.stderr
    columns = result.stdout.strip().split(", ")
    assert columns and all(column.endswith(":text") for column in columns), columns
    assert {
        "claims.node",
        "events.node",
        "events.other_node",
        "links.source",
        "links.target",
        "note_nodes.node",
        "nodes.parent",
        "rules.node",
    } <= {column.removesuffix(":text") for column in columns}


def test_each_kind_has_its_own_counter_in_every_project_and_there_is_no_shared_one() -> None:
    result = run_sql(
        "SELECT string_agg(sequencename, ',' ORDER BY sequencename) FROM pg_sequences "
        f"WHERE schemaname = 'public' AND sequencename ~ '_{the_project().id}$'",
        plain=True,
        project=None,
    )
    assert result.returncode == 0, result.stderr
    names = set(result.stdout.strip().split(","))
    project = the_project().id
    kinds = ("epic", "story", "task", "links", "rules", "notes", "events")
    assert {f"{kind}_ids_{project}" for kind in kinds} <= names
    # Decisions and facts were two kinds with a counter each; notes are one, with one counter.
    for gone in ("nodes", "decisions", "facts"):
        assert f"{gone}_ids_{project}" not in names


async def test_a_projects_role_sees_and_writes_only_its_own_nodes_and_what_points_at_them(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    project = the_project().id

    def seen_by_the_role(sql: str) -> str:
        result = run_sql(sql, plain=True, as_project=True)
        assert result.returncode == 0, result.stderr
        return str(result.stdout.strip())

    # It sees its own nodes by reference, through the table and through the view.
    for table in ("nodes", "node_state"):
        assert seen_by_the_role(f"SELECT count(*) FROM {table} WHERE ref = '{story['ref']}'") == "1"
        assert (
            seen_by_the_role(f"SELECT count(*) FROM {table} WHERE project_id <> {project}") == "0"
        )
    assert (
        seen_by_the_role(f"SELECT waiting_on FROM node_state WHERE ref = '{task['ref']}'") == "{}"
    )
    # With no project named it sees none of them, whatever it asks for by reference.
    nothing = run_sql(
        f"SELECT count(*) FROM nodes WHERE ref = '{story['ref']}'",
        plain=True,
        project=None,
        as_project=True,
    )
    assert nothing.returncode == 0 and nothing.stdout.strip() == "0", nothing.stderr
    # It may write a node of its own project, and the database still holds it to the rules: a
    # task cannot be moved under an epic, and the reference cannot be changed.
    moved = run_sql(
        f"UPDATE nodes SET parent = '{epic['ref']}', version = version + 1 "
        f"WHERE ref = '{task['ref']}'",
        as_project=True,
    )
    assert moved.returncode != 0 and "node_parent_has_the_right_kind" in moved.stderr
    renumbered = run_sql(
        f"UPDATE nodes SET number = number + 1000000, version = version + 1 "
        f"WHERE ref = '{task['ref']}'",
        as_project=True,
    )
    assert renumbered.returncode != 0
    assert "cannot change its kind or its number" in renumbered.stderr
    # It cannot delete a node, a claim's node, or write a node into another project.
    assert (
        "permission denied"
        in run_sql(f"DELETE FROM nodes WHERE ref = '{task['ref']}'", as_project=True).stderr
    )
    other = project + 100_000
    forged = run_sql(
        f"INSERT INTO nodes (project_id, kind, number, title) "
        f"VALUES ({other}, 'epic', next_id('epic'), 'forged')",
        as_project=True,
    )
    assert forged.returncode != 0 and "row-level security" in forged.stderr
    assert (await ok(client, "get_node", node=task["ref"]))["parent"] == story["ref"]


def test_the_database_keeps_the_definition_of_done_to_one_text_set_by_a_person() -> None:
    first, second = make_project(register()), make_project(register())

    def definition(
        project: int, by: str = "Marie", kind: str = "person", version: int = 2, body: str = "x"
    ) -> str:
        return (
            "INSERT INTO definition_of_done (project_id, body, version, updated_by, "
            f"updated_by_kind) VALUES ({project}, '{body}', {version}, '{by}', '{kind}')"
        )

    attempts = {
        "a definition set by an agent": (
            definition(first.id, kind="agent"),
            "definition_is_set_by_a_named_person",
        ),
        "a definition set by nobody": (
            definition(first.id, by=" "),
            "definition_is_set_by_a_named_person",
        ),
        "a definition at version 0": (
            definition(first.id, version=0),
            "definition_version_starts_at_one",
        ),
        "a change of the definition that names an item": (
            "INSERT INTO events (project_id, node, action, actor, actor_kind, after) "
            f"VALUES ({first.id}, 'epic:1', 'definition_of_done_changed', 'x', 'person', '{{}}')",
            "event_names_what_it_is_about",
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql, project=first.id)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    assert run_sql(definition(first.id), project=None).returncode == 0
    twice = run_sql(definition(first.id, body="y"), project=None)
    assert twice.returncode != 0 and "definition_of_done_pkey" in twice.stderr

    # A project's own role sees its own definition and nobody else's.
    count = "SELECT count(*) FROM definition_of_done"
    assert run_sql(count, plain=True, project=first.id, as_project=True).stdout.strip() == "1"
    assert run_sql(count, plain=True, project=second.id, as_project=True).stdout.strip() == "0"
    forged = run_sql(definition(first.id), project=second.id, as_project=True)
    assert forged.returncode != 0 and "row-level security" in forged.stderr
    assert (
        "permission denied"
        in run_sql("DELETE FROM definition_of_done", project=first.id, as_project=True).stderr
    )
