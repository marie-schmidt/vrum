"""The database refuses bad data on its own, even if the server's own checks were bypassed.

These run SQL directly inside the real database container (see ./check.sh, which names the compose
project and password through the environment).
"""

import pytest
from mcp import Client

from tests.helpers import (
    day,
    make_belief,
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


def test_the_database_can_be_reached_directly() -> None:
    result = run_sql("SELECT count(*) FROM nodes", as_project=True)
    assert result.returncode == 0, result.stderr


async def test_the_database_refuses_a_hierarchy_that_is_not_epic_story_task(client: Client) -> None:
    epic, story, _ = await make_tree(client)
    attempts = {
        "a story with no parent": (
            "INSERT INTO nodes (kind, title) VALUES ('story', 'x')",
            "node_has_a_parent_unless_epic",
        ),
        "a task with no parent": (
            "INSERT INTO nodes (kind, title) VALUES ('task', 'x')",
            "node_has_a_parent_unless_epic",
        ),
        "an epic with a parent": (
            f"INSERT INTO nodes (kind, parent_id, title) VALUES ('epic', {epic['id']}, 'x')",
            "node_has_a_parent_unless_epic",
        ),
        "a task under an epic": (
            f"INSERT INTO nodes (kind, parent_id, title) VALUES ('task', {epic['id']}, 'x')",
            "node_parent_has_the_right_kind",
        ),
        "a story under a story": (
            f"INSERT INTO nodes (kind, parent_id, title) VALUES ('story', {story['id']}, 'x')",
            "node_parent_has_the_right_kind",
        ),
        "a story under nothing that exists": (
            "INSERT INTO nodes (kind, parent_id, title) VALUES ('story', 987654321, 'x')",
            "node_parent_has_the_right_kind",
        ),
        "an unknown kind": (
            f"INSERT INTO nodes (kind, parent_id, title) VALUES ('saga', {epic['id']}, 'x')",
            "node_kind_is_known",
        ),
        "a blank title": (
            "INSERT INTO nodes (kind, title) VALUES ('epic', '  ')",
            "node_title_is_not_blank",
        ),
        "the stored status ready": (
            f"UPDATE nodes SET status = 'ready', version = version + 1 "
            f"WHERE {mine()} AND id = {story['id']}",
            "node_status_is_known",
        ),
        "a story made its own parent": (
            f"UPDATE nodes SET parent_id = {story['id']}, version = version + 1 "
            f"WHERE {mine()} AND id = {story['id']}",
            "node_parent_has_the_right_kind",
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    after = await ok(client, "get_node", node_id=story["id"])
    assert (after["status"], after["parent_id"], after["version"]) == ("ready", epic["id"], 1)


async def test_the_database_refuses_a_kind_change_and_a_change_without_a_new_version(
    client: Client,
) -> None:
    _, story, _ = await make_tree(client)
    where = f"WHERE {mine()} AND id = {story['id']}"
    result = run_sql(f"UPDATE nodes SET kind = 'epic', parent_id = NULL {where}")
    assert "cannot change its kind" in result.stderr
    result = run_sql(f"UPDATE nodes SET title = 'quiet edit' {where}")
    assert "raise its version by exactly one" in result.stderr
    result = run_sql(f"UPDATE nodes SET title = 'x', version = 5 {where}")
    assert "raise its version by exactly one" in result.stderr
    assert (await ok(client, "get_node", node_id=story["id"]))["title"] == "A story"


async def test_the_event_log_cannot_be_rewritten_and_nodes_with_history_cannot_be_deleted(
    client: Client,
) -> None:
    epic, story, _ = await make_tree(client)
    assert (
        "append-only"
        in run_sql(
            f"UPDATE events SET actor = 'someone' WHERE {mine()} AND node_id = {epic['id']}"
        ).stderr
    )
    assert (
        "append-only"
        in run_sql(f"DELETE FROM events WHERE {mine()} AND node_id = {epic['id']}").stderr
    )
    assert (
        "violates foreign key constraint"
        in run_sql(f"DELETE FROM nodes WHERE {mine()} AND id = {story['id']}").stderr
    )
    assert (
        "violates check constraint"
        in run_sql(
            f"INSERT INTO events (node_id, action, actor, after) "
            f"VALUES ({epic['id']}, 'create', ' ', '{{}}')"
        ).stderr
    )

    history = (await ok(client, "node_history", node_id=story["id"]))["items"]
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
        parent_id=story["id"],
        start_on=day(2),
        target_on=day(10),
    )
    attempts = {
        "a new node that starts after its target": (
            "INSERT INTO nodes (kind, parent_id, title, start_on, target_on) VALUES "
            f"('task', {story['id']}, 'x', '{day(9)}', '{day(3)}')"
        ),
        "a start moved past the target": (
            f"UPDATE nodes SET start_on = '{day(30)}', version = version + 1 "
            f"WHERE {mine()} AND id = {task['id']}"
        ),
        "a target moved before the start": (
            f"UPDATE nodes SET target_on = '{day(1)}', version = version + 1 "
            f"WHERE {mine()} AND id = {task['id']}"
        ),
    }
    for what, sql in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert "node_start_is_not_after_target" in result.stderr, f"{what}: {result.stderr}"

    # Either date alone, or none, is fine, and the same day for both.
    fine = run_sql(
        f"UPDATE nodes SET start_on = NULL, version = version + 1 "
        f"WHERE {mine()} AND id = {task['id']}"
    )
    assert fine.returncode == 0, fine.stderr
    unchanged = await ok(client, "get_node", node_id=task["id"])
    assert (unchanged["start_on"], unchanged["target_on"]) == (None, day(10))


async def test_the_database_refuses_bad_links(client: Client) -> None:
    first, second = await make_tasks(client, 2)
    a, b = first["id"], second["id"]
    await needs_first(client, first, second)
    await ok(client, "add_link", actor="marie", source_id=a, target_id=b, link_type="relates_to")

    def link(source: int, target: int, link_type: str) -> str:
        return (
            "INSERT INTO links (source_id, target_id, link_type) "
            f"VALUES ({source}, {target}, '{link_type}')"
        )

    attempts = {
        "a link to the same node": (link(a, a, "relates_to"), "link_is_not_to_the_same_node"),
        "waiting for the same node": (link(a, a, "needs_first"), "link_is_not_to_the_same_node"),
        "an unknown link type": (link(a, b, "blocks"), "link_type_is_known"),
        "a link to a node that does not exist": (
            link(a, 987654321, "needs_first"),
            "violates foreign key constraint",
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

    links = (await ok(client, "list_links", node_id=a))["items"]
    assert sorted(link["link_type"] for link in links) == ["needs_first", "relates_to"]


async def test_the_database_refuses_bad_claims_and_a_second_holder(client: Client) -> None:
    epic, story, task = await make_tree(client)
    await ok(client, "claim_node", actor="ann", node_id=task["id"])

    def claim(node: int, kind: str, actor: str, expires: str) -> str:
        return (
            "INSERT INTO claims (node_id, node_kind, actor, expires_at) "
            f"VALUES ({node}, '{kind}', '{actor}', now() + interval '{expires}')"
        )

    attempts = {
        "a claim on an epic": (
            claim(epic["id"], "epic", "bob", "1 hour"),
            "claim_is_for_a_story_or_task",
        ),
        "a claim that names the wrong kind": (
            claim(story["id"], "task", "bob", "1 hour"),
            "claim_is_on_a_real_node",
        ),
        "a claim with no holder": (
            claim(story["id"], "story", " ", "1 hour"),
            "claim_actor_is_not_blank",
        ),
        "a claim that is over before it starts": (
            claim(story["id"], "story", "bob", "-1 hour"),
            "claim_lease_is_in_the_future",
        ),
        "a second holder of one task": (
            claim(task["id"], "task", "bob", "1 hour"),
            'duplicate key value violates unique constraint "claims_pkey"',
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    assert (await ok(client, "get_node", node_id=task["id"]))["claimed_by"] == "ann"
    assert (await ok(client, "get_node", node_id=story["id"]))["claimed_by"] is None
    result = run_sql(
        "INSERT INTO events (node_id, action, actor, after) "
        f"VALUES ({task['id']}, 'delete', 'x', '{{}}')"
    )
    assert "event_action_is_known" in result.stderr


async def test_the_database_refuses_bad_rules_and_keeps_a_rule_in_one_column(
    client: Client,
) -> None:
    epic, story, task = await make_tree(client)
    rule = await make_rule(client, story_id=story["id"])

    def new_rule(story_id: object, title: str = "x", status: str = "held") -> str:
        return (
            "INSERT INTO rules (story_id, title, status) "
            f"VALUES ({story_id}, '{title}', '{status}')"
        )

    attempts = {
        "a rule in an epic": (new_rule(epic["id"]), "rule_belongs_to_a_story"),
        "a rule in a task": (new_rule(task["id"]), "rule_belongs_to_a_story"),
        "a rule in a story that does not exist": (new_rule(987654321), "rule_belongs_to_a_story"),
        "a rule with a blank title": (new_rule("NULL", title=" "), "rule_title_is_not_blank"),
        "a rule with an unknown status": (
            new_rule("NULL", status="open"),
            "rule_status_is_known",
        ),
        "moving a rule into a task": (
            f"UPDATE rules SET story_id = {task['id']}, version = version + 1 "
            f"WHERE {mine()} AND id = {rule['id']}",
            "rule_belongs_to_a_story",
        ),
        "a change to a rule without a new version": (
            f"UPDATE rules SET title = 'quiet edit' WHERE {mine()} AND id = {rule['id']}",
            "raise its version by exactly one",
        ),
        "a rule event that names no rule": (
            f"INSERT INTO events (node_id, action, actor, after) "
            f"VALUES ({story['id']}, 'rule_moved', 'x', '{{}}')",
            "event_names_what_it_is_about",
        ),
        "a node event that names a rule": (
            f"INSERT INTO events (node_id, rule_id, action, actor, after) "
            f"VALUES ({story['id']}, {rule['id']}, 'update', 'x', '{{}}')",
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
    assert (unchanged["story_id"], unchanged["title"], unchanged["version"]) == (
        story["id"],
        "A rule",
        1,
    )

    # One column says which story owns a rule, and it is on the rules table itself. No other table
    # names a story together with a rule, so a rule cannot be in two stories at once.
    result = run_sql(
        "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_name = 'story_id'",
        plain=True,
    )
    assert result.returncode == 0 and result.stdout.strip() == "rules"


async def test_a_rule_event_cannot_be_rewritten_either(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    rule = await make_rule(client, story_id=story["id"])
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


async def test_the_database_keeps_decisions_as_a_record(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    old = await make_decision(client, "Old", node_ids=[story["id"]])
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

    def new_decision(**changes: str) -> str:
        fields = {
            "kind": "decision",
            "title": "x",
            "text": "x",
            "reason": "x",
            "decided_by": "x",
            "status": "active",
        }
        fields.update(changes)
        columns = ", ".join(fields)
        values = ", ".join(f"'{value}'" for value in fields.values())
        return f"INSERT INTO decisions ({columns}, decided_on) VALUES ({values}, current_date)"

    attempts = {
        "an unknown kind": (new_decision(kind="rule"), "decision_kind_is_known"),
        "an unknown status": (new_decision(status="open"), "decision_status_is_known"),
        "a blank title": (new_decision(title=" "), "decision_title_is_not_blank"),
        "blank text": (new_decision(text=" "), "decision_text_is_not_blank"),
        "a blank reason": (new_decision(reason=" "), "decision_reason_is_not_blank"),
        "nobody who decided it": (new_decision(decided_by=" "), "decision_decided_by_is_not_blank"),
        "superseded by nothing": (
            new_decision(status="superseded"),
            "decision_is_superseded_exactly_when_replaced",
        ),
        "replaced while still marked active": (
            f"UPDATE decisions SET superseded_by_id = {other['id']} "
            f"WHERE {mine()} AND id = {new['id']}",
            "decision_is_superseded_exactly_when_replaced",
        ),
        "replaced by an earlier decision": (
            f"UPDATE decisions SET status = 'superseded', superseded_by_id = {old['id']} "
            f"WHERE {mine()} AND id = {new['id']}",
            "decision_is_replaced_by_a_later_one",
        ),
        "an edit to what was decided": (
            f"UPDATE decisions SET text = 'something else' WHERE {mine()} AND id = {other['id']}",
            "A decision cannot be edited",
        ),
        "an edit to who decided it": (
            f"UPDATE decisions SET decided_by = 'someone else' "
            f"WHERE {mine()} AND id = {other['id']}",
            "A decision cannot be edited",
        ),
        "bringing a superseded decision back": (
            f"UPDATE decisions SET status = 'active', superseded_by_id = NULL "
            f"WHERE {mine()} AND id = {old['id']}",
            "A superseded decision cannot change again",
        ),
        "pointing a superseded decision at another replacement": (
            f"UPDATE decisions SET superseded_by_id = {other['id']} "
            f"WHERE {mine()} AND id = {old['id']}",
            "A superseded decision cannot change again",
        ),
        "deleting a decision": (
            f"DELETE FROM decisions WHERE {mine()} AND id = {other['id']}",
            "cannot be deleted",
        ),
        "a link to a node that does not exist": (
            f"INSERT INTO decision_nodes (decision_id, node_id) VALUES ({other['id']}, 987654321)",
            "violates foreign key constraint",
        ),
        "a link to a rule that does not exist": (
            f"INSERT INTO decision_rules (decision_id, rule_id) VALUES ({other['id']}, 987654321)",
            "violates foreign key constraint",
        ),
        "the same link twice": (
            f"INSERT INTO decision_nodes (decision_id, node_id) "
            f"VALUES ({new['id']}, {story['id']})",
            "decision_nodes_pkey",
        ),
        "a new link on a superseded decision": (
            f"INSERT INTO decision_nodes (decision_id, node_id) "
            f"VALUES ({old['id']}, {story['parent_id']})",
            "keeps the links it had",
        ),
        "removing a link from a superseded decision": (
            f"DELETE FROM decision_nodes WHERE {mine()} AND decision_id = {old['id']}",
            "keeps the links it had",
        ),
        "a decision event that names no decision": (
            "INSERT INTO events (action, actor, after) VALUES ('decision_recorded', 'x', '{}')",
            "event_names_what_it_is_about",
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    kept = await ok(client, "get_decision", decision_id=old["id"])
    assert (kept["status"], kept["superseded_by_id"], kept["node_ids"]) == (
        "superseded",
        new["id"],
        [story["id"]],
    )
    assert (await ok(client, "get_decision", decision_id=other["id"]))["text"] == other["text"]


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


async def test_the_database_keeps_facts_and_beliefs_as_a_record(client: Client) -> None:
    (story,) = await make_stories(client, 1)
    early = await make_fact(client, "Earlier than the rest")
    fact = await make_fact(client, "Kept", node_ids=[story["id"]])
    belief = await make_belief(client, [fact], "A belief")
    confirmed = await make_belief(client, [fact], "A confirmed belief")
    await ok(client, "confirm_belief", actor="a", memory_id=confirmed["id"], confirmed_by="Marie")
    decision = await make_decision(client, "A decision")
    old = await make_fact(client, "Old", node_ids=[story["id"]])
    change = await ok(
        client,
        "supersede_memory",
        actor="a",
        replaces=[old["id"]],
        reason="Changed",
        text="New",
        source="s",
    )
    new = change["replacement"]
    retracted = await make_fact(client, "Wrong")
    await ok(client, "retract_memory", actor="a", memory_ids=[retracted["id"]], reason="Wrong")

    def memory(**changes: str) -> str:
        fields = {"type": "fact", "kind": "world", "text": "x", "source": "x", "recorded_by": "x"}
        fields.update(changes)
        columns = ", ".join(fields)
        values = ", ".join("NULL" if value == "NULL" else f"'{value}'" for value in fields.values())
        return f"INSERT INTO memories ({columns}) VALUES ({values})"

    new_belief = (
        "WITH b AS (INSERT INTO memories (type, text, recorded_by) "
        "VALUES ('belief', 'x', 'x') RETURNING id) "
        "INSERT INTO belief_evidence (belief_id, {column}) SELECT id, {value} FROM b"
    )
    attempts = {
        "an unknown type": (memory(type="rumour"), "memory_type_is_known"),
        "an unknown kind": (memory(kind="rumour"), "memory_kind_is_known"),
        "a fact with no source": (memory(source="NULL"), "fact_has_a_kind_and_a_source"),
        "a fact with a blank source": (memory(source=" "), "fact_has_a_kind_and_a_source"),
        "a fact with no kind": (memory(kind="NULL"), "fact_has_a_kind_and_a_source"),
        "blank text": (memory(text=" "), "memory_text_is_not_blank"),
        "nobody who recorded it": (memory(recorded_by=" "), "memory_recorded_by_is_not_blank"),
        "an unknown origin": (memory(origin="guessed"), "memory_origin_is_known"),
        "a fact a model drew up as a belief": (
            memory(origin="derived"),
            "memory_origin_matches_its_type",
        ),
        "a belief with a source": (
            memory(type="belief", kind="NULL"),
            "belief_has_no_fact_fields",
        ),
        "an active fact with a reason": (
            memory(status_reason="because"),
            "memory_has_a_reason_once_it_has_ended",
        ),
        "a retracted fact with no reason": (
            memory(status="retracted"),
            "memory_has_a_reason_once_it_has_ended",
        ),
        "a superseded fact with no replacement": (
            memory(status="superseded", status_reason="because"),
            "memory_is_superseded_exactly_when_replaced",
        ),
        "a fact confirmed by someone": (
            f"UPDATE memories SET confirmed_by = 'Ann', confirmed_at = now() "
            f"WHERE {mine()} AND id = {fact['id']}",
            "only_a_belief_is_confirmed",
        ),
        "replaced by an earlier item": (
            f"UPDATE memories SET status = 'superseded', superseded_by_id = {early['id']}, "
            f"status_reason = 'r' WHERE {mine()} AND id = {fact['id']}",
            "memory_is_replaced_by_a_later_one",
        ),
        "a fact replaced by a belief": (
            f"UPDATE memories SET status = 'superseded', superseded_by_id = {belief['id']}, "
            f"status_reason = 'r' WHERE {mine()} AND id = {fact['id']}",
            "memory_is_replaced_by_its_own_type",
        ),
        "editing the text": (
            f"UPDATE memories SET text = 'something else' WHERE {mine()} AND id = {fact['id']}",
            "A fact or belief cannot be edited",
        ),
        "editing the source": (
            f"UPDATE memories SET source = 'someone else' WHERE {mine()} AND id = {fact['id']}",
            "A fact or belief cannot be edited",
        ),
        "editing who recorded it": (
            f"UPDATE memories SET recorded_by = 'someone else' "
            f"WHERE {mine()} AND id = {fact['id']}",
            "A fact or belief cannot be edited",
        ),
        "deleting it": (
            f"DELETE FROM memories WHERE {mine()} AND id = {fact['id']}",
            "cannot be deleted",
        ),
        "bringing a retracted fact back": (
            f"UPDATE memories SET status = 'active', status_reason = NULL "
            f"WHERE {mine()} AND id = {retracted['id']}",
            "cannot change again",
        ),
        "pointing a superseded fact at another replacement": (
            f"UPDATE memories SET superseded_by_id = {new['id']} "
            f"WHERE {mine()} AND id = {old['id']}",
            "cannot change again",
        ),
        "changing who confirmed a belief": (
            f"UPDATE memories SET confirmed_by = 'Bob' WHERE {mine()} AND id = {confirmed['id']}",
            "A confirmation cannot be changed",
        ),
        "confirming a belief in the step that retracts it": (
            f"UPDATE memories SET status = 'retracted', status_reason = 'r', "
            f"confirmed_by = 'Ann', confirmed_at = now() "
            f"WHERE {mine()} AND id = {belief['id']}",
            "cannot be confirmed in the same step",
        ),
        "a belief with no evidence": (
            "INSERT INTO memories (type, text, recorded_by) VALUES ('belief', 'x', 'x')",
            "A belief needs evidence",
        ),
        "evidence that is a belief": (
            new_belief.format(column="fact_id", value=belief["id"]),
            "evidence_is_never_a_belief",
        ),
        "evidence that is a retracted fact": (
            new_belief.format(column="fact_id", value=retracted["id"]),
            "must be an active fact or decision",
        ),
        "evidence that is a fact and a decision at once": (
            f"INSERT INTO belief_evidence (belief_id, fact_id, decision_id) "
            f"VALUES ({belief['id']}, {fact['id']}, {decision['id']})",
            "evidence_is_a_fact_or_a_decision",
        ),
        "evidence that is a decision that does not exist": (
            new_belief.format(column="decision_id", value=987654321),
            "must be an active fact or decision",
        ),
        "changing the evidence of a belief": (
            f"UPDATE belief_evidence SET fact_id = {early['id']} "
            f"WHERE {mine()} AND belief_id = {belief['id']}",
            "cannot be changed or removed",
        ),
        "removing the evidence of a belief": (
            f"DELETE FROM belief_evidence WHERE {mine()} AND belief_id = {belief['id']}",
            "cannot be changed or removed",
        ),
        "a belief that concerns a decision": (
            f"INSERT INTO memory_decisions (memory_id, decision_id) "
            f"VALUES ({belief['id']}, {decision['id']})",
            "only_a_fact_concerns_a_decision",
        ),
        "a link to a node that does not exist": (
            f"INSERT INTO memory_nodes (memory_id, node_id) VALUES ({fact['id']}, 987654321)",
            "violates foreign key constraint",
        ),
        "a link to a rule that does not exist": (
            f"INSERT INTO memory_rules (memory_id, rule_id) VALUES ({fact['id']}, 987654321)",
            "violates foreign key constraint",
        ),
        "the same link twice": (
            f"INSERT INTO memory_nodes (memory_id, node_id) VALUES ({fact['id']}, {story['id']})",
            "memory_nodes_pkey",
        ),
        "a new link on a superseded fact": (
            f"INSERT INTO memory_nodes (memory_id, node_id) "
            f"VALUES ({old['id']}, {story['parent_id']})",
            "keeps the links it had",
        ),
        "removing a link from a superseded fact": (
            f"DELETE FROM memory_nodes WHERE {mine()} AND memory_id = {old['id']}",
            "keeps the links it had",
        ),
        "a decision promoted from a fact": (
            f"INSERT INTO decisions (kind, title, text, reason, decided_by, decided_on, "
            f"promoted_from_id) VALUES ('decision', 'x', 'x', 'x', 'x', current_date, "
            f"{fact['id']})",
            "decision_is_promoted_from_a_belief",
        ),
        "changing where a decision came from": (
            f"UPDATE decisions SET promoted_from_id = {belief['id']} "
            f"WHERE {mine()} AND id = {decision['id']}",
            "A decision cannot be edited",
        ),
        "a fact event that names no fact": (
            "INSERT INTO events (action, actor, after) VALUES ('memory_recorded', 'x', '{}')",
            "event_names_what_it_is_about",
        ),
    }
    for what, (sql, expected) in attempts.items():
        result = run_sql(sql)
        assert result.returncode != 0, f"The database accepted {what}."
        assert expected in result.stderr, f"{what}: {result.stderr}"

    # None of it changed anything.
    kept = await ok(client, "get_memory", memory_id=fact["id"])
    assert (kept["text"], kept["source"], kept["status"], kept["node_ids"]) == (
        "Kept",
        "The team call",
        "active",
        [story["id"]],
    )
    assert (await ok(client, "get_memory", memory_id=belief["id"]))["evidence_count"] == 1
    assert (await ok(client, "get_memory", memory_id=retracted["id"]))["status"] == "retracted"
    replaced = await ok(client, "get_memory", memory_id=old["id"])
    assert (replaced["status"], replaced["superseded_by_id"], replaced["node_ids"]) == (
        "superseded",
        new["id"],
        [story["id"]],
    )
    assert (await ok(client, "get_memory", memory_id=confirmed["id"]))["confirmed_by"] == "Marie"


async def test_a_fact_or_belief_event_cannot_be_rewritten_either(client: Client) -> None:
    fact = await make_fact(client, "Logged")
    assert (
        "append-only"
        in run_sql(
            f"UPDATE events SET actor = 'someone' WHERE {mine()} AND memory_id = {fact['id']}"
        ).stderr
    )
    assert (
        "append-only"
        in run_sql(f"DELETE FROM events WHERE {mine()} AND memory_id = {fact['id']}").stderr
    )
