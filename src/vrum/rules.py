"""Rules (requirements) and the coverage report. This module owns creating, changing, placing and
moving rules, and the question of which rules and stories are not yet matched up."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    check_choice,
    clean_actor,
    fetch_all,
    fetch_one,
    page_size,
    record_event,
    save_changes,
    snapshot,
    split_node_ref,
    split_page,
    stale_version,
)
from vrum.db import Database
from vrum.graph import (
    AFTER_NODE,
    BODY_PREVIEW_CHARS,
    EVENT_COLUMNS,
    NODE_ORDER,
    Event,
    EventPage,
    Node,
    after_position,
    bodies_left_out,
    list_columns,
)

RULE_STATUSES = ("held", "settled", "dropped")
_RULE_COLUMNS = (
    "id, story, title, body, length(body) AS body_chars, status, version, created_at, updated_at"
)


def _rule_list_columns(detail: bool) -> str:
    """The columns of a list of rules. Without `detail` the body is left out, and a short preview
    and its full length are given instead."""
    if detail:
        return _RULE_COLUMNS
    return _RULE_COLUMNS.replace(
        " body,", f" NULL::text AS body, left(body, {BODY_PREVIEW_CHARS}) AS body_preview,", 1
    )


COVERAGE_LISTS = ("both", "unplaced_rules", "stories_without_rules")
_STORY_WITHOUT_RULES = (
    "kind = 'story' AND status <> 'dropped' AND NOT EXISTS ("
    "SELECT 1 FROM rules WHERE rules.story = node_state.ref AND rules.status <> 'dropped')"
)


class Rule(BaseModel):
    id: int = Field(description="The rule's id. Rules have their own ids, separate from nodes.")
    story: str | None = Field(
        description="The reference of the one story that owns this rule, such as story:12. Empty "
        "while the rule is unplaced."
    )
    title: str = Field(description="The rule, stated briefly.")
    body: str | None = Field(
        description="Plain text or Markdown: the detail, or where it came from. Empty in a list "
        "that left bodies out: see body_preview, and ask for detail or use get_rule."
    )
    body_chars: int = Field(description="How many characters the body has.")
    body_preview: str | None = Field(
        default=None,
        description="Only in a list that left bodies out: the first characters of the body.",
    )
    status: str = Field(description="held, settled or dropped.")
    version: int = Field(
        description="Starts at 1 and rises by one with every change. Send it back as "
        "expected_version when you change the rule."
    )
    created_at: datetime
    updated_at: datetime


class RulePage(BaseModel):
    items: list[Rule]
    total: int = Field(description="How many rules match in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass this as after_id to get the next page. Empty when there are no more."
    )
    left_out: str | None = Field(
        description="Says what the list left out: bodies, unless you asked for detail. Empty when "
        "nothing was left out."
    )


class CoverageReport(BaseModel):
    unplaced_rule_count: int = Field(
        description="How many rules are not in any story. Dropped rules are not counted."
    )
    unplaced_rules: list[Rule] = Field(description="One page of them, oldest first.")
    next_unplaced_after_id: int | None = Field(
        description="Pass this as unplaced_after_id for the next page of unplaced rules."
    )
    story_without_rules_count: int = Field(
        description="How many stories have no rule that is held or settled. Dropped stories are "
        "not counted."
    )
    stories_without_rules: list[Node] = Field(description="One page of them, by story number.")
    next_stories_after: str | None = Field(
        description="Pass this as stories_after for the next page of stories: the reference of "
        "the last story of this page."
    )
    left_out: str | None = Field(
        description="Says what the lists left out: bodies, unless you asked for detail. Empty "
        "when nothing was left out."
    )


async def require_rule(
    conn: AsyncConnection, rule_id: int, *, lock: bool = False
) -> dict[str, Any]:
    row = await fetch_one(
        conn,
        f"SELECT {_RULE_COLUMNS} FROM rules WHERE id = %s" + (" FOR UPDATE" if lock else ""),
        (rule_id,),
    )
    if row is None:
        raise Refusal(
            f"There is no rule with id {rule_id}. Use list_rules to find the id you want."
        )
    return row


async def _require_story(conn: AsyncConnection, story: str) -> None:
    kind, number = split_node_ref(story)
    if kind != "story":
        raise Refusal(
            f"A rule belongs to a story, but {story} is {'an' if kind == 'epic' else 'a'} {kind}. "
            "Use list_nodes with kind=story to find the story you want."
        )
    found = await fetch_one(conn, "SELECT 1 FROM nodes WHERE ref = %s", (story,))
    if found is None:
        raise Refusal(
            f"There is no story {number} in this project, so a rule cannot be placed there. "
            "Use list_nodes with kind=story to find the story you want."
        )


def _clean_title(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        raise Refusal("A rule needs a title: title cannot be empty.")
    return cleaned


def _event_nodes(story: str | None, left: str | None = None) -> dict[str, Any]:
    """The stories an event shows in the history of: the one the rule is in, and the one it left."""
    return {"node": story, "other_node": left if left != story else None}


async def insert_rule(
    conn: AsyncConnection,
    *,
    actor: str,
    title: str,
    body: str,
    status: str,
    story: str | None,
) -> dict[str, Any]:
    """Insert a rule that has been checked, and record it. Returns the stored row."""
    row = await fetch_one(
        conn,
        "INSERT INTO rules (story, title, body, status) VALUES (%s, %s, %s, %s) "
        f"RETURNING {_RULE_COLUMNS}",
        (story, title, body, status),
    )
    assert row is not None
    await record_event(
        conn,
        rule_id=row["id"],
        action="rule_created",
        actor=actor,
        before=None,
        after=snapshot(row, ("story", "title", "body", "status", "version")),
        **_event_nodes(story),
    )
    return row


class Rules:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_rule(
        self, *, actor: str, title: str, body: str, status: str, story: str | None
    ) -> Rule:
        actor = clean_actor(actor)
        title = _clean_title(title)
        check_choice("status", status, RULE_STATUSES)
        async with self._database.transaction() as conn:
            if story is not None:
                await _require_story(conn, story)
            row = await insert_rule(
                conn, actor=actor, title=title, body=body, status=status, story=story
            )
        return Rule.model_validate(row)

    async def get_rule(self, rule_id: int) -> Rule:
        async with self._database.transaction() as conn:
            return Rule.model_validate(await require_rule(conn, rule_id))

    async def list_rules(
        self,
        *,
        story: str | None,
        unplaced: bool,
        status: str | None,
        detail: bool,
        limit: int,
        after_id: int | None,
    ) -> RulePage:
        limit = page_size(limit)
        if status is not None:
            check_choice("status", status, RULE_STATUSES)
        if unplaced and story is not None:
            raise Refusal(
                "Give story or unplaced, not both: a rule that is in a story is not unplaced."
            )
        where = (
            "(%(story)s::text IS NULL OR story = %(story)s) "
            "AND (NOT %(unplaced)s OR story IS NULL) "
            "AND (%(status)s::text IS NULL OR status = %(status)s)"
        )
        params = {"story": story, "unplaced": unplaced, "status": status}
        async with self._database.transaction() as conn:
            if story is not None:
                await _require_story(conn, story)
            counted = await fetch_one(
                conn, f"SELECT count(*) AS total FROM rules WHERE {where}", params
            )
            assert counted is not None
            rows = await fetch_all(
                conn,
                f"SELECT {_rule_list_columns(detail)} FROM rules "
                f"WHERE {where} AND id > %(after)s ORDER BY id LIMIT %(fetch)s",
                {**params, "after": after_id or 0, "fetch": limit + 1},
            )
        page, next_after_id = split_page(rows, limit)
        items = [Rule.model_validate(row) for row in page]
        return RulePage(
            items=items,
            total=int(counted["total"]),
            next_after_id=next_after_id,
            left_out=None if detail else bodies_left_out(items),
        )

    async def update_rule(
        self,
        *,
        actor: str,
        rule_id: int,
        expected_version: int,
        title: str | None,
        body: str | None,
        status: str | None,
    ) -> Rule:
        actor = clean_actor(actor)
        if title is not None:
            title = _clean_title(title)
        if status is not None:
            check_choice("status", status, RULE_STATUSES)
        async with self._database.transaction() as conn:
            current = await require_rule(conn, rule_id, lock=True)
            if current["version"] != expected_version:
                raise stale_version(
                    "Rule", "get_rule", rule_id, current["version"], expected_version
                )
            wanted = {"title": title, "body": body, "status": status}
            changes = {
                field: value
                for field, value in wanted.items()
                if value is not None and value != current[field]
            }
            if not changes:
                raise Refusal(
                    f"Nothing to change: rule {rule_id} already has the values you gave, or you "
                    "gave none. Give at least one of title, body or status with a new value."
                )
            row = await save_changes(conn, "rules", rule_id, changes, _RULE_COLUMNS)
            await record_event(
                conn,
                rule_id=rule_id,
                action="rule_updated",
                actor=actor,
                before={**snapshot(current, tuple(changes)), "version": current["version"]},
                after={**changes, "version": row["version"]},
                **_event_nodes(current["story"]),
            )
        return Rule.model_validate(row)

    async def place_rule(
        self, *, actor: str, rule_id: int, expected_version: int, story: str
    ) -> Rule:
        actor = clean_actor(actor)
        async with self._database.transaction() as conn:
            current = await require_rule(conn, rule_id, lock=True)
            if current["version"] != expected_version:
                raise stale_version(
                    "Rule", "get_rule", rule_id, current["version"], expected_version
                )
            if current["story"] is not None:
                raise Refusal(
                    f"Rule {rule_id} is already placed in {current['story']}, and a rule "
                    f"belongs to at most one story. To put it in {story} instead, use "
                    "move_rule."
                )
            await _require_story(conn, story)
            row = await save_changes(conn, "rules", rule_id, {"story": story}, _RULE_COLUMNS)
            await record_event(
                conn,
                rule_id=rule_id,
                action="rule_placed",
                actor=actor,
                before={"story": None, "version": current["version"]},
                after={"story": story, "version": row["version"]},
                **_event_nodes(story),
            )
        return Rule.model_validate(row)

    async def move_rule(
        self, *, actor: str, rule_id: int, expected_version: int, new_story: str
    ) -> Rule:
        """Take a rule out of its story and put it in another, as one step: it is never in two
        stories, and never in none, along the way."""
        actor = clean_actor(actor)
        async with self._database.transaction() as conn:
            current = await require_rule(conn, rule_id, lock=True)
            if current["version"] != expected_version:
                raise stale_version(
                    "Rule", "get_rule", rule_id, current["version"], expected_version
                )
            if current["story"] is None:
                raise Refusal(
                    f"Rule {rule_id} is not in any story yet, so there is nothing to move. Use "
                    "place_rule to put it in a story."
                )
            await _require_story(conn, new_story)
            if new_story == current["story"]:
                raise Refusal(f"Rule {rule_id} is already in {new_story}, so nothing moved.")
            row = await save_changes(conn, "rules", rule_id, {"story": new_story}, _RULE_COLUMNS)
            await record_event(
                conn,
                rule_id=rule_id,
                action="rule_moved",
                actor=actor,
                before={"story": current["story"], "version": current["version"]},
                after={"story": new_story, "version": row["version"]},
                **_event_nodes(new_story, current["story"]),
            )
        return Rule.model_validate(row)

    async def rule_history(self, *, rule_id: int, limit: int, after_id: int | None) -> EventPage:
        limit = page_size(limit)
        async with self._database.transaction() as conn:
            await require_rule(conn, rule_id)
            rows = await fetch_all(
                conn,
                f"SELECT {EVENT_COLUMNS} FROM events "
                "WHERE rule_id = %s AND id > %s ORDER BY id LIMIT %s",
                (rule_id, after_id or 0, limit + 1),
            )
        page, next_after_id = split_page(rows, limit)
        return EventPage(
            items=[Event.model_validate(row) for row in page], next_after_id=next_after_id
        )

    async def coverage_report(
        self,
        *,
        show: str,
        detail: bool,
        limit: int,
        unplaced_after_id: int | None,
        stories_after: str | None,
    ) -> CoverageReport:
        """Which rules are in no story, and which stories have no rule. A dropped rule is no
        longer a requirement, so it is not unplaced and does not cover a story; a dropped story
        needs no rules."""
        limit = page_size(limit)
        check_choice("show", show, COVERAGE_LISTS)
        unplaced_page: list[dict[str, Any]] = []
        stories_page: list[dict[str, Any]] = []
        next_unplaced = next_stories = None
        async with self._database.transaction() as conn:
            if show in ("both", "unplaced_rules"):
                rows = await fetch_all(
                    conn,
                    f"SELECT {_rule_list_columns(detail)} FROM rules "
                    "WHERE story IS NULL AND status <> 'dropped' AND id > %s "
                    "ORDER BY id LIMIT %s",
                    (unplaced_after_id or 0, limit + 1),
                )
                unplaced_page, next_unplaced = split_page(rows, limit)
            if show in ("both", "stories_without_rules"):
                rows = await fetch_all(
                    conn,
                    f"SELECT {list_columns(detail)} FROM node_state "
                    f"WHERE {_STORY_WITHOUT_RULES} AND {AFTER_NODE} "
                    f"ORDER BY {NODE_ORDER} LIMIT %(fetch)s",
                    {**after_position(stories_after), "fetch": limit + 1},
                )
                stories_page, next_stories = split_page(rows, limit, key="ref")
            unplaced_total = await fetch_one(
                conn,
                "SELECT count(*) AS total FROM rules WHERE story IS NULL AND status <> 'dropped'",
            )
            stories_total = await fetch_one(
                conn, f"SELECT count(*) AS total FROM node_state WHERE {_STORY_WITHOUT_RULES}"
            )
        assert unplaced_total is not None and stories_total is not None
        unplaced_rules = [Rule.model_validate(row) for row in unplaced_page]
        stories = [Node.model_validate(row) for row in stories_page]
        return CoverageReport(
            unplaced_rule_count=unplaced_total["total"],
            unplaced_rules=unplaced_rules,
            next_unplaced_after_id=next_unplaced,
            story_without_rules_count=stories_total["total"],
            stories_without_rules=stories,
            next_stories_after=next_stories,
            left_out=None if detail else bodies_left_out([*unplaced_rules, *stories]),
        )
