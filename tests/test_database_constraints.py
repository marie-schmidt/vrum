"""The database refuses bad data on its own, even if the server's own checks were bypassed.

These run SQL directly inside the real database container (see ./check.sh, which names the compose
project and password through the environment).
"""

import pytest
from mcp import Client

from tests.helpers import (
    confirm,
    day,
    make_decision,
    make_fact,
    make_rule,
    make_stories,
    make_tasks,
    make_tree,
    needs_first,
    ok,
    run_sql,
)
from tests.world import the_project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


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
    epic, story, _ = await make_tree(client)
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
            f"INSERT INTO events (node, action, actor, after) "
            f"VALUES ('{epic['ref']}', 'create', ' ', '{{}}')"
        ).stderr
    )
    assert (
        "violates foreign key constraint"
        in run_sql(
            "INSERT INTO events (node, action, actor, after) "
            "VALUES ('story:987654321', 'create', 'x', '{}')"
        ).stderr
    )
    assert (
        "violates foreign key constraint"
        in run_sql(
            f"INSERT INTO events (node, other_node, action, actor, after) "
            f"VALUES ('{epic['ref']}', 'task:987654321', 'link_added', 'x', '{{}}')"
        ).stderr
    )

    history = (await ok(client, "node_history", node=story["ref"]))["items"]
    assert [event["actor"] for event in history] == ["marie"]


async def test_the_database_refuses_a_start_date_after_the_target_date_on_its_own(
    client: Client,
) -> None:
    (story,) = await make_stories(client, 1)
    task = await ok(
        client,
        "create_node",
        actor="marie",
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
    await ok(client, "add_link", actor="marie", source=a, target=b, link_type="relates_to")

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
    epic, story, task = await make_tree(client)
    await ok(client, "claim_node", actor="ann", node=task["ref"])

    def claim(node: str, actor: str, expires: str) -> str:
        return (
            "INSERT INTO claims (node, actor, expires_at) "
            f"VALUES ('{node}', '{actor}', now() + interval '{expires}')"
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

    assert (await ok(client, "get_node", node=task["ref"]))["claimed_by"] == "ann"
    assert (await ok(client, "get_node", node=story["ref"]))["claimed_by"] is None
    result = run_sql(
        "INSERT INTO events (node, action, actor, after) "
        f"VALUES ('{task['ref']}', 'delete', 'x', '{{}}')"
    )
    assert "event_action_is_known" in result.stderr


async def test_the_database_refuses_bad_rules_and_keeps_a_rule_in_one_column(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    rule = await make_rule(client, story=story["ref"])

    def new_rule(story_ref: str | None, title: str = "x", status: str = "held") -> str:
        story_sql = "NULL" if story_ref is None else f"'{story_ref}'"
        return (
            f"INSERT INTO rules (story, title, status) VALUES ({story_sql}, '{title}', '{status}')"
        )

    attempts = {
        "a rule in an epic": (new_rule(epic["ref"]), "rule_is_in_a_story"),
        "a rule in a task": (new_rule(task["ref"]), "rule_is_in_a_story"),
        "a rule in a story that does not exist": (
            new_rule("story:987654321"),
            "rule_belongs_to_a_story",
        ),
        "a rule in a story named by its number alone": (
            new_rule(str(story["number"])),
            "rule_is_in_a_story",
        ),
        "a rule with a blank title": (new_rule(None, title=" "), "rule_title_is_not_blank"),
        "a rule with an unknown status": (
            new_rule(None, status="open"),
            "rule_status_is_known",
        ),
        "moving a rule into a task": (
            f"UPDATE rules SET story = '{task['ref']}', version = version + 1 "
            f"WHERE {mine()} AND id = {rule['id']}",
            "rule_is_in_a_story",
        ),
        "moving a rule into an epic": (
            f"UPDATE rules SET story = '{epic['ref']}', version = version + 1 "
            f"WHERE {mine()} AND id = {rule['id']}",
            "rule_is_in_a_story",
        ),
        "a change to a rule without a new version": (
            f"UPDATE rules SET title = 'quiet edit' WHERE {mine()} AND id = {rule['id']}",
            "raise its version by exactly one",
        ),
        "a rule event that names no rule": (
            f"INSERT INTO events (node, action, actor, after) "
            f"VALUES ('{story['ref']}', 'rule_moved', 'x', '{{}}')",
            "event_names_what_it_is_about",
        ),
        "a node event that names a rule": (
            f"INSERT INTO events (node, rule_id, action, actor, after) "
            f"VALUES ('{story['ref']}', {rule['id']}, 'update', 'x', '{{}}')",
            "event_names_what_it_is_about",
        ),
        "an event about nothing": (
            "INSERT INTO events (action, actor, after) VALUES ('update', 'x', '{}')",
            "event_names_what_it_is_about",
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    unchanged = await ok(client, "get_rule", rule_id=rule["id"])
    assert (unchanged["story"], unchanged["title"], unchanged["version"]) == (
        story["ref"],
        "A rule",
        1,
    )

    # One column says which story owns a rule, and it is on the rules table itself. No other table
    # names a story together with a rule, so a rule cannot be in two stories at once.
    result = run_sql(
        "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'story'",
        plain=True,
    )
    assert result.returncode == 0 and result.stdout.strip() == "rules"


async def test_a_rule_event_cannot_be_rewritten_either(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story=story["ref"])
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


async def test_the_database_keeps_decisions_as_a_record(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    old = await make_decision(client, "Old", nodes=[story["ref"]])
    new = await ok(
        client,
        "supersede_decision",
        actor="a",
        decision_id=old["id"],
        title="New",
        text="t",
        reason="r",
        decided_by="Ann",
    )
    other = await make_decision(client, "Standing")
    gone = await make_decision(client, "Given up", nodes=[story["ref"]])
    await ok(client, "withdraw_decision", actor="a", decision_id=gone["id"], reason="Not needed")
    confirmed = await make_decision(client, "Confirmed")
    await confirm(client, "decision", confirmed, "Marie")
    ended = await make_decision(client, "Confirmed, then given up")
    await confirm(client, "decision", ended, "Ann")
    await ok(
        client,
        "withdraw_decision",
        actor="a",
        decision_id=ended["id"],
        reason="Dropped",
        said_by="Ann",
        quote="Drop it",
    )
    later = await make_decision(client, "The newest one")

    def new_decision(**changes: str) -> str:
        fields = {
            "title": "x",
            "text": "x",
            "reason": "x",
            "decided_by": "x",
            "status": "active",
        }
        fields.update(changes)
        columns = ", ".join(fields)
        values = ", ".join("NULL" if value == "NULL" else f"'{value}'" for value in fields.values())
        return f"INSERT INTO decisions ({columns}, decided_on) VALUES ({values}, current_date)"

    def update(decision: dict[str, object], assignments: str) -> str:
        return f"UPDATE decisions SET {assignments} WHERE {mine()} AND id = {decision['id']}"

    attempts = {
        "an unknown status": (new_decision(status="open"), "decision_status_is_known"),
        "a blank title": (new_decision(title=" "), "decision_title_is_not_blank"),
        "blank text": (new_decision(text=" "), "decision_text_is_not_blank"),
        "a blank reason": (new_decision(reason=" "), "decision_reason_is_not_blank"),
        "no reason at all": (new_decision(reason="NULL"), "violates not-null constraint"),
        "nobody who decided it": (new_decision(decided_by=" "), "decision_decided_by_is_not_blank"),
        "no date": (
            "INSERT INTO decisions (title, text, reason, decided_by) VALUES ('x', 'x', 'x', 'x')",
            "violates not-null constraint",
        ),
        "superseded by nothing": (
            new_decision(status="superseded"),
            "decision_is_superseded_exactly_when_replaced",
        ),
        "withdrawn with no reason": (
            new_decision(status="withdrawn"),
            "decision_is_withdrawn_with_a_reason",
        ),
        "withdrawn with a blank reason": (
            new_decision(status="withdrawn", status_reason=" "),
            "decision_is_withdrawn_with_a_reason",
        ),
        "an active decision with a reason for ending": (
            new_decision(status_reason="because"),
            "decision_is_withdrawn_with_a_reason",
        ),
        "a decision that is superseded and has a withdrawal reason": (
            update(
                other,
                f"status = 'superseded', superseded_by_id = {later['id']}, status_reason = 'r'",
            ),
            "decision_is_withdrawn_with_a_reason",
        ),
        "replaced while still marked active": (
            update(other, f"superseded_by_id = {later['id']}"),
            "decision_is_superseded_exactly_when_replaced",
        ),
        "replaced by an earlier decision": (
            update(new, f"status = 'superseded', superseded_by_id = {old['id']}"),
            "decision_is_replaced_by_a_later_one",
        ),
        "an edit to what was decided": (
            update(other, "text = 'something else'"),
            "A decision cannot be edited",
        ),
        "an edit to its title": (
            update(other, "title = 'something else'"),
            "A decision cannot be edited",
        ),
        "an edit to why": (
            update(other, "reason = 'something else'"),
            "A decision cannot be edited",
        ),
        "an edit to who decided it": (
            update(other, "decided_by = 'someone else'"),
            "A decision cannot be edited",
        ),
        "an edit to the day it was decided": (
            update(other, "decided_on = decided_on + 1"),
            "A decision cannot be edited",
        ),
        "an edit to whether it is project-wide": (
            update(other, "project_wide = NOT project_wide"),
            "A decision cannot be edited",
        ),
        "an edit to when it was recorded": (
            update(other, "recorded_at = recorded_at + interval '1 day'"),
            "A decision cannot be edited",
        ),
        "bringing a superseded decision back": (
            update(old, "status = 'active', superseded_by_id = NULL"),
            "A superseded or withdrawn decision cannot change again",
        ),
        "pointing a superseded decision at another replacement": (
            update(old, f"superseded_by_id = {other['id']}"),
            "A superseded or withdrawn decision cannot change again",
        ),
        "bringing a withdrawn decision back": (
            update(gone, "status = 'active', status_reason = NULL"),
            "A superseded or withdrawn decision cannot change again",
        ),
        "changing why a decision was withdrawn": (
            update(gone, "status_reason = 'another reason'"),
            "A superseded or withdrawn decision cannot change again",
        ),
        "deleting a decision": (
            f"DELETE FROM decisions WHERE {mine()} AND id = {other['id']}",
            "cannot be deleted",
        ),
        "deleting a decision that has ended": (
            f"DELETE FROM decisions WHERE {mine()} AND id = {gone['id']}",
            "cannot be deleted",
        ),
        "a confirmation with a person and no time": (
            update(other, "confirmed_by = 'Ann'"),
            "decision_confirmation_names_a_person_and_a_time",
        ),
        "a confirmation with a time and no person": (
            update(other, "confirmed_at = now()"),
            "decision_confirmation_names_a_person_and_a_time",
        ),
        "a confirmation by a blank name": (
            update(other, "confirmed_by = ' ', confirmed_at = now()"),
            "decision_confirmation_names_a_person_and_a_time",
        ),
        "taking away only the person of a confirmation": (
            update(confirmed, "confirmed_by = NULL"),
            "decision_confirmation_names_a_person_and_a_time",
        ),
        "confirming a decision in the step that gives it up": (
            update(
                other,
                "status = 'withdrawn', status_reason = 'r', confirmed_by = 'Ann', "
                "confirmed_at = now()",
            ),
            "cannot be confirmed or unconfirmed in the same step that ends it",
        ),
        "unconfirming a decision in the step that gives it up": (
            update(
                confirmed,
                "status = 'withdrawn', status_reason = 'r', confirmed_by = NULL, "
                "confirmed_at = NULL",
            ),
            "cannot be confirmed or unconfirmed in the same step that ends it",
        ),
        "confirming a decision that was given up": (
            update(gone, "confirmed_by = 'Ann', confirmed_at = now()"),
            "A superseded or withdrawn decision cannot change again",
        ),
        "taking the confirmation from a decision that was given up": (
            update(ended, "confirmed_by = NULL, confirmed_at = NULL"),
            "A superseded or withdrawn decision cannot change again",
        ),
        "confirming a decision that was superseded": (
            update(old, "confirmed_by = 'Ann', confirmed_at = now()"),
            "A superseded or withdrawn decision cannot change again",
        ),
        "a link to a node that does not exist": (
            f"INSERT INTO decision_nodes (decision_id, node) "
            f"VALUES ({other['id']}, 'story:987654321')",
            "violates foreign key constraint",
        ),
        "a link to a node named by its number alone": (
            f"INSERT INTO decision_nodes (decision_id, node) "
            f"VALUES ({other['id']}, '{story['number']}')",
            "violates foreign key constraint",
        ),
        "a link to a rule that does not exist": (
            f"INSERT INTO decision_rules (decision_id, rule_id) VALUES ({other['id']}, 987654321)",
            "violates foreign key constraint",
        ),
        "a link from a decision that does not exist": (
            f"INSERT INTO decision_nodes (decision_id, node) VALUES (987654321, '{story['ref']}')",
            "violates foreign key constraint",
        ),
        "the same link twice": (
            f"INSERT INTO decision_nodes (decision_id, node) "
            f"VALUES ({new['id']}, '{story['ref']}')",
            "decision_nodes_pkey",
        ),
        "a new link on a superseded decision": (
            f"INSERT INTO decision_nodes (decision_id, node) "
            f"VALUES ({old['id']}, '{story['parent']}')",
            "keeps the links it had",
        ),
        "removing a link from a superseded decision": (
            f"DELETE FROM decision_nodes WHERE {mine()} AND decision_id = {old['id']}",
            "keeps the links it had",
        ),
        "a new link on a withdrawn decision": (
            f"INSERT INTO decision_nodes (decision_id, node) "
            f"VALUES ({gone['id']}, '{story['parent']}')",
            "keeps the links it had",
        ),
        "removing a link from a withdrawn decision": (
            f"DELETE FROM decision_nodes WHERE {mine()} AND decision_id = {gone['id']}",
            "keeps the links it had",
        ),
        "a decision event that names no decision": (
            "INSERT INTO events (action, actor, after) VALUES ('decision_recorded', 'x', '{}')",
            "event_names_what_it_is_about",
        ),
        "a decision event that names a fact": (
            f"INSERT INTO events (decision_id, fact_id, action, actor, after) "
            f"VALUES ({other['id']}, {other['id']}, 'decision_withdrawn', 'x', '{{}}')",
            "event_names_what_it_is_about",
        ),
        "an event of the old promotion": (
            f"INSERT INTO events (decision_id, action, actor, after) "
            f"VALUES ({other['id']}, 'decision_promoted', 'x', '{{}}')",
            "event_action_is_known",
        ),
    }
    refused_by_the_database(attempts)

    kept = await ok(client, "get_decision", decision_id=old["id"])
    assert (kept["status"], kept["superseded_by_id"], kept["nodes"]) == (
        "superseded",
        new["id"],
        [story["ref"]],
    )
    assert await ok(client, "get_decision", decision_id=other["id"]) == other
    assert (await ok(client, "get_decision", decision_id=confirmed["id"]))["confirmed_by"] == (
        "Marie"
    )
    withdrawn = await ok(client, "get_decision", decision_id=ended["id"])
    assert (withdrawn["status"], withdrawn["confirmed_by"]) == ("withdrawn", "Ann")
    assert (await ok(client, "get_decision", decision_id=gone["id"]))["nodes"] == [story["ref"]]

    # While a decision is in force only its confirmation can change, and the database allows that.
    changed = run_sql(update(later, "confirmed_by = 'Bob', confirmed_at = now()"))
    assert changed.returncode == 0, changed.stderr
    assert (await ok(client, "get_decision", decision_id=later["id"]))["confirmation"] == (
        "confirmed"
    )
    cleared = run_sql(update(later, "confirmed_by = NULL, confirmed_at = NULL"))
    assert cleared.returncode == 0, cleared.stderr
    assert (await ok(client, "get_decision", decision_id=later["id"]))["confirmed_by"] is None


async def test_a_decision_event_cannot_be_rewritten_either(client: Client) -> None:
    decision = await make_decision(client, "Logged")
    assert (
        "append-only"
        in run_sql(
            f"UPDATE events SET actor = 'someone' WHERE {mine()} AND decision_id = {decision['id']}"
        ).stderr
    )
    assert (
        "append-only"
        in run_sql(f"DELETE FROM events WHERE {mine()} AND decision_id = {decision['id']}").stderr
    )


async def test_the_database_keeps_facts_as_a_record(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story=story["ref"])
    decision = await make_decision(client, "A decision")
    early = await make_fact(client, "Earlier than the rest")
    fact = await make_fact(
        client,
        "Kept",
        nodes=[story["ref"]],
        rule_ids=[rule["id"]],
        decision_ids=[decision["id"]],
    )
    confirmed = await make_fact(client, "A confirmed fact")
    await confirm(client, "fact", confirmed, "Marie")
    old = await make_fact(
        client, "Old", nodes=[story["ref"]], rule_ids=[rule["id"]], decision_ids=[decision["id"]]
    )
    change = await ok(
        client,
        "supersede_fact",
        actor="a",
        replaces=[old["id"]],
        reason="Changed",
        text="New",
        source="s",
    )
    new = change["replacement"]
    withdrawn = await make_fact(client, "Wrong", nodes=[story["ref"]])
    await ok(client, "withdraw_fact", actor="a", fact_ids=[withdrawn["id"]], reason="Wrong")
    ended = await make_fact(client, "Confirmed, then wrong")
    await confirm(client, "fact", ended, "Ann")
    await ok(
        client,
        "withdraw_fact",
        actor="a",
        fact_ids=[ended["id"]],
        reason="Wrong",
        said_by="Ann",
        quote="It was wrong",
    )
    later = await make_fact(client, "The newest one")

    def new_fact(**changes: str) -> str:
        fields = {"text": "x", "source": "x", "recorded_by": "x"}
        fields.update(changes)
        columns = ", ".join(fields)
        values = ", ".join("NULL" if value == "NULL" else f"'{value}'" for value in fields.values())
        return f"INSERT INTO facts ({columns}) VALUES ({values})"

    def update(item: dict[str, object], assignments: str) -> str:
        return f"UPDATE facts SET {assignments} WHERE {mine()} AND id = {item['id']}"

    attempts = {
        "an unknown status": (
            new_fact(status="open", status_reason="because"),
            "fact_status_is_known",
        ),
        "a fact with no source": (new_fact(source="NULL"), "violates not-null constraint"),
        "a fact with a blank source": (new_fact(source=" "), "fact_source_is_not_blank"),
        "blank text": (new_fact(text=" "), "fact_text_is_not_blank"),
        "nobody who recorded it": (new_fact(recorded_by=" "), "fact_recorded_by_is_not_blank"),
        "an active fact with a reason for ending": (
            new_fact(status_reason="because"),
            "fact_has_a_reason_once_it_has_ended",
        ),
        "a withdrawn fact with no reason": (
            new_fact(status="withdrawn"),
            "fact_has_a_reason_once_it_has_ended",
        ),
        "a withdrawn fact with a blank reason": (
            new_fact(status="withdrawn", status_reason=" "),
            "fact_has_a_reason_once_it_has_ended",
        ),
        "a superseded fact with no replacement": (
            new_fact(status="superseded", status_reason="because"),
            "fact_is_superseded_exactly_when_replaced",
        ),
        "replaced while still marked active": (
            update(fact, f"superseded_by_id = {later['id']}"),
            "fact_is_superseded_exactly_when_replaced",
        ),
        "superseded with no reason": (
            update(fact, f"status = 'superseded', superseded_by_id = {later['id']}"),
            "fact_has_a_reason_once_it_has_ended",
        ),
        "replaced by an earlier fact": (
            update(
                fact,
                f"status = 'superseded', superseded_by_id = {early['id']}, status_reason = 'r'",
            ),
            "fact_is_replaced_by_a_later_one",
        ),
        "replaced by a fact that does not exist": (
            update(
                fact, "status = 'superseded', superseded_by_id = 987654321, status_reason = 'r'"
            ),
            "violates foreign key constraint",
        ),
        "editing the text": (
            update(fact, "text = 'something else'"),
            "A fact cannot be edited",
        ),
        "editing the source": (
            update(fact, "source = 'someone else'"),
            "A fact cannot be edited",
        ),
        "editing who recorded it": (
            update(fact, "recorded_by = 'someone else'"),
            "A fact cannot be edited",
        ),
        "editing when it was recorded": (
            update(fact, "recorded_at = recorded_at + interval '1 day'"),
            "A fact cannot be edited",
        ),
        "deleting it": (
            f"DELETE FROM facts WHERE {mine()} AND id = {fact['id']}",
            "cannot be deleted",
        ),
        "deleting one that has ended": (
            f"DELETE FROM facts WHERE {mine()} AND id = {withdrawn['id']}",
            "cannot be deleted",
        ),
        "bringing a withdrawn fact back": (
            update(withdrawn, "status = 'active', status_reason = NULL"),
            "A superseded or withdrawn fact cannot change again",
        ),
        "changing why a fact was withdrawn": (
            update(withdrawn, "status_reason = 'another reason'"),
            "A superseded or withdrawn fact cannot change again",
        ),
        "pointing a superseded fact at another replacement": (
            update(old, f"superseded_by_id = {later['id']}"),
            "A superseded or withdrawn fact cannot change again",
        ),
        "a confirmation with a person and no time": (
            update(fact, "confirmed_by = 'Ann'"),
            "confirmation_names_a_person_and_a_time",
        ),
        "a confirmation with a time and no person": (
            update(fact, "confirmed_at = now()"),
            "confirmation_names_a_person_and_a_time",
        ),
        "a confirmation by a blank name": (
            update(fact, "confirmed_by = ' ', confirmed_at = now()"),
            "confirmation_names_a_person_and_a_time",
        ),
        "confirming a fact in the step that withdraws it": (
            update(
                fact,
                "status = 'withdrawn', status_reason = 'r', confirmed_by = 'Ann', "
                "confirmed_at = now()",
            ),
            "cannot be confirmed or unconfirmed in the same step that ends it",
        ),
        "unconfirming a fact in the step that withdraws it": (
            update(
                confirmed,
                "status = 'withdrawn', status_reason = 'r', confirmed_by = NULL, "
                "confirmed_at = NULL",
            ),
            "cannot be confirmed or unconfirmed in the same step that ends it",
        ),
        "confirming a fact that was withdrawn": (
            update(withdrawn, "confirmed_by = 'Ann', confirmed_at = now()"),
            "A superseded or withdrawn fact cannot change again",
        ),
        "taking the confirmation from a fact that was withdrawn": (
            update(ended, "confirmed_by = NULL, confirmed_at = NULL"),
            "A superseded or withdrawn fact cannot change again",
        ),
        "confirming a fact that was superseded": (
            update(old, "confirmed_by = 'Ann', confirmed_at = now()"),
            "A superseded or withdrawn fact cannot change again",
        ),
        "a link to a node that does not exist": (
            f"INSERT INTO fact_nodes (fact_id, node) VALUES ({fact['id']}, 'task:987654321')",
            "violates foreign key constraint",
        ),
        "a link to a node named by its number alone": (
            f"INSERT INTO fact_nodes (fact_id, node) VALUES ({fact['id']}, '{story['number']}')",
            "violates foreign key constraint",
        ),
        "a link to a rule that does not exist": (
            f"INSERT INTO fact_rules (fact_id, rule_id) VALUES ({fact['id']}, 987654321)",
            "violates foreign key constraint",
        ),
        "a link to a decision that does not exist": (
            f"INSERT INTO fact_decisions (fact_id, decision_id) VALUES ({fact['id']}, 987654321)",
            "violates foreign key constraint",
        ),
        "a link from a fact that does not exist": (
            f"INSERT INTO fact_nodes (fact_id, node) VALUES (987654321, '{story['ref']}')",
            "violates foreign key constraint",
        ),
        "the same link twice": (
            f"INSERT INTO fact_nodes (fact_id, node) VALUES ({fact['id']}, '{story['ref']}')",
            "fact_nodes_pkey",
        ),
        "the same rule link twice": (
            f"INSERT INTO fact_rules (fact_id, rule_id) VALUES ({fact['id']}, {rule['id']})",
            "fact_rules_pkey",
        ),
        "the same decision link twice": (
            f"INSERT INTO fact_decisions (fact_id, decision_id) "
            f"VALUES ({fact['id']}, {decision['id']})",
            "fact_decisions_pkey",
        ),
        "a new node link on a superseded fact": (
            f"INSERT INTO fact_nodes (fact_id, node) VALUES ({old['id']}, '{story['parent']}')",
            "keeps the links it had",
        ),
        "removing a node link from a superseded fact": (
            f"DELETE FROM fact_nodes WHERE {mine()} AND fact_id = {old['id']}",
            "keeps the links it had",
        ),
        "removing a rule link from a superseded fact": (
            f"DELETE FROM fact_rules WHERE {mine()} AND fact_id = {old['id']}",
            "keeps the links it had",
        ),
        "removing a decision link from a superseded fact": (
            f"DELETE FROM fact_decisions WHERE {mine()} AND fact_id = {old['id']}",
            "keeps the links it had",
        ),
        "a new link on a withdrawn fact": (
            f"INSERT INTO fact_nodes (fact_id, node) "
            f"VALUES ({withdrawn['id']}, '{story['parent']}')",
            "keeps the links it had",
        ),
        "removing a link from a withdrawn fact": (
            f"DELETE FROM fact_nodes WHERE {mine()} AND fact_id = {withdrawn['id']}",
            "keeps the links it had",
        ),
        "a fact event that names no fact": (
            "INSERT INTO events (action, actor, after) VALUES ('fact_recorded', 'x', '{}')",
            "event_names_what_it_is_about",
        ),
        "a fact event that names a fact that does not exist": (
            "INSERT INTO events (fact_id, action, actor, after) "
            "VALUES (987654321, 'fact_recorded', 'x', '{}')",
            "violates foreign key constraint",
        ),
        "a node event that names a fact": (
            f"INSERT INTO events (node, fact_id, action, actor, after) "
            f"VALUES ('{story['ref']}', {fact['id']}, 'update', 'x', '{{}}')",
            "event_names_what_it_is_about",
        ),
        "a rule event that names a fact": (
            f"INSERT INTO events (rule_id, fact_id, action, actor, after) "
            f"VALUES ({rule['id']}, {fact['id']}, 'rule_updated', 'x', '{{}}')",
            "event_names_what_it_is_about",
        ),
        "an event of the old memory": (
            f"INSERT INTO events (fact_id, action, actor, after) "
            f"VALUES ({fact['id']}, 'memory_recorded', 'x', '{{}}')",
            "event_action_is_known",
        ),
        "an event of the old belief confirmation": (
            f"INSERT INTO events (fact_id, action, actor, after) "
            f"VALUES ({fact['id']}, 'memory_confirmed', 'x', '{{}}')",
            "event_action_is_known",
        ),
    }
    refused_by_the_database(attempts)

    # None of it changed anything.
    kept = await ok(client, "get_fact", fact_id=fact["id"])
    assert kept == fact
    replaced = await ok(client, "get_fact", fact_id=old["id"])
    assert (replaced["status"], replaced["superseded_by_id"], replaced["nodes"]) == (
        "superseded",
        new["id"],
        [story["ref"]],
    )
    assert (replaced["rule_ids"], replaced["decision_ids"]) == ([rule["id"]], [decision["id"]])
    assert (await ok(client, "get_fact", fact_id=withdrawn["id"]))["status"] == "withdrawn"
    assert (await ok(client, "get_fact", fact_id=confirmed["id"]))["confirmed_by"] == "Marie"
    gone = await ok(client, "get_fact", fact_id=ended["id"])
    assert (gone["status"], gone["confirmed_by"]) == ("withdrawn", "Ann")

    # While a fact is in force only its confirmation can change, and the database allows that.
    changed = run_sql(update(later, "confirmed_by = 'Bob', confirmed_at = now()"))
    assert changed.returncode == 0, changed.stderr
    assert (await ok(client, "get_fact", fact_id=later["id"]))["confirmation"] == "confirmed"
    cleared = run_sql(update(later, "confirmed_by = NULL, confirmed_at = NULL"))
    assert cleared.returncode == 0, cleared.stderr
    assert (await ok(client, "get_fact", fact_id=later["id"]))["confirmed_by"] is None


async def test_a_fact_event_cannot_be_rewritten_either(client: Client) -> None:
    fact = await make_fact(client, "Logged")
    assert (
        "append-only"
        in run_sql(
            f"UPDATE events SET actor = 'someone' WHERE {mine()} AND fact_id = {fact['id']}"
        ).stderr
    )
    assert (
        "append-only"
        in run_sql(f"DELETE FROM events WHERE {mine()} AND fact_id = {fact['id']}").stderr
    )


async def test_a_projects_own_role_cannot_delete_a_record_or_rewrite_the_log(
    client: Client,
) -> None:
    fact = await make_fact(client, "Kept")
    decision = await make_decision(client, "Kept too")

    # What the server itself runs as is not even allowed to try.
    for sql in (
        f"DELETE FROM facts WHERE id = {fact['id']}",
        f"DELETE FROM decisions WHERE id = {decision['id']}",
        "UPDATE events SET actor = 'someone'",
        "DELETE FROM events",
    ):
        result = run_sql(sql, as_project=True)
        assert result.returncode != 0, f"The project's role was allowed to run: {sql}"
        assert "permission denied" in result.stderr, f"{sql}: {result.stderr}"
    # It sees its own records, and only its own.
    result = run_sql(
        "SELECT count(*) FILTER (WHERE project_id <> current_project()) FROM facts",
        plain=True,
        as_project=True,
    )
    assert result.returncode == 0 and result.stdout.strip() == "0", result.stderr
    assert (await ok(client, "get_fact", fact_id=fact["id"]))["text"] == "Kept"
    assert (await ok(client, "get_decision", decision_id=decision["id"]))["title"] == "Kept too"


def test_the_project_is_part_of_every_key_and_every_work_table_is_walled() -> None:
    # Every reference between two tables that hold a project's work names the project too, so a
    # fact, a decision, a link or an event cannot refer to a row of another project.
    result = run_sql(
        "SELECT string_agg(c.conrelid::regclass::text || '.' || c.conname, ', ' "
        "ORDER BY c.conname) FROM pg_constraint c "
        "WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace "
        "AND c.confrelid <> 'projects'::regclass "
        "AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.conrelid "
        "AND a.attname = 'project_id' AND NOT a.attisdropped) "
        "AND NOT EXISTS (SELECT 1 FROM unnest(c.conkey) k JOIN pg_attribute a "
        "ON a.attrelid = c.conrelid AND a.attnum = k WHERE a.attname = 'project_id')",
        plain=True,
        project=None,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"These references do not name the project: {result.stdout}"

    # And every table with a project_id shows only that project's rows to the project's role.
    result = run_sql(
        "SELECT string_agg(c.relname, ', ' ORDER BY c.relname) FROM pg_class c "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'project_id' "
        "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' "
        "AND c.relname NOT IN ('sessions', 'agent_keys') "
        "AND NOT (c.relrowsecurity "
        "AND EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid))",
        plain=True,
        project=None,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"These tables have no row filter: {result.stdout}"


def test_the_schema_holds_only_the_two_context_records() -> None:
    tables = run_sql(
        "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables "
        "WHERE table_schema = 'public'",
        plain=True,
        project=None,
    ).stdout.split(",")
    assert {"facts", "decisions", "fact_nodes", "fact_rules", "fact_decisions"} <= set(tables)
    assert {"decision_nodes", "decision_rules", "fact_state", "decision_state"} <= set(tables)
    for gone in (
        "memories",
        "memory_nodes",
        "memory_rules",
        "memory_decisions",
        "belief_evidence",
        "memory_state",
        "model_calls",
    ):
        assert gone not in tables, f"{gone} is still in the database"

    # No column of the old taxonomy is left: no kind of fact or decision, no origin, no promotion.
    result = run_sql(
        "SELECT string_agg(table_name || '.' || column_name, ', ' ORDER BY table_name) "
        "FROM information_schema.columns WHERE table_schema = 'public' AND ("
        "column_name IN ('memory_id', 'promoted_from_id', 'origin', 'happened_on', 'type') "
        "OR (table_name IN ('decisions', 'decision_state', 'facts', 'fact_state') "
        "AND column_name = 'kind'))",
        plain=True,
        project=None,
    )
    assert result.returncode == 0 and result.stdout.strip() == "", result.stdout


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
        "'decision_nodes', 'fact_nodes', 'events', 'nodes') "
        "AND column_name IN ('source', 'target', 'node', 'other_node', 'story', 'parent')",
        plain=True,
        project=None,
    )
    assert result.returncode == 0, result.stderr
    columns = result.stdout.strip().split(", ")
    assert columns and all(column.endswith(":text") for column in columns), columns
    assert {
        "claims.node",
        "decision_nodes.node",
        "events.node",
        "events.other_node",
        "fact_nodes.node",
        "links.source",
        "links.target",
        "nodes.parent",
        "rules.story",
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
    assert {f"{kind}_ids_{project}" for kind in ("epic", "story", "task")} <= names
    assert f"nodes_ids_{project}" not in names


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
