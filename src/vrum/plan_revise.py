"""An agent revises a drafted plan that a person has read (M51).

A plan that a person asked changes to stays held; the agent that drafted it sends a *revision*: the
whole plan as it should be now, in the same words `create_plan` takes, with two additions. An item
that continues one the plan already has names it (`ref`), and an acceptance criterion that continues
one names its `id`; an entry with none is new, and anything the plan has that no entry continues is
removed (an item is dropped with a note saying so, a criterion is dropped). Links and criteria are
given whole too, or left out to keep what the kept items have.

Vrum checks the whole revision together before it changes anything, with the very checks a new plan
meets (hierarchy, sizes, dates, links, circles of waiting) and the rules of revising (what is
removed cannot still be referred to, needed by something outside the plan or hold work outside it).
A revision that is refused changes nothing. An accepted one is made in one step: the items, links
and criteria are brought to what it says, and one event keeps it whole as the next revision, to be
read again by a person before any of its work can be taken.

Removing is the agent's act, never the reviewer's: a reviewer asks for it in a comment, the agent
sends the revision, and the person reads what changed. A removed item is dropped and stays in the
revision it was in; no later approval can free it."""

from typing import Any

import psycopg
from pydantic import BaseModel, Field

from vrum.common import (
    Ref,
    Refusal,
    fetch_all,
    fetch_one,
    is_node_ref,
    parse_day,
    record_event,
    refuse_secrets,
)
from vrum.db import Database
from vrum.gates import PLAN_COLUMNS, PLAN_REVISED, plan_snapshot
from vrum.graph import (
    KINDS,
    apply_node_changes,
    change_status,
    clean_title,
    insert_node,
    move_in,
    require_node,
)
from vrum.links import delete_link, insert_link
from vrum.plan_revisions import MOST_NOTE_CHARS
from vrum.rules import apply_rule_changes, insert_rule
from vrum.workflow import (
    PlanItem,
    PlanLink,
    PlannedItem,
    PlanRule,
    _a,
    _existing,
    _listed,
    _plan_problems,
)

_TWO_WAY = ("duplicates", "relates_to")


class RevisionItem(PlanItem):
    ref: Ref | None = Field(
        default=None,
        description="The reference of the item of this plan that this entry continues, such as "
        "story:61. Leave out for a new item. An item of the plan that no entry continues is "
        "removed.",
    )


class RevisionRule(BaseModel):
    id: int | None = Field(
        default=None,
        description="The id of the acceptance criterion of this plan that this entry continues. "
        "Leave out for a new one. One that no entry continues is dropped.",
    )
    title: str = Field(description="The acceptance criterion, stated briefly.")
    body: str = ""
    status: str | None = Field(
        default=None,
        description="open or met. Leave out to keep a continued criterion as it is (a new one is "
        "open).",
    )
    node: Ref = Field(
        description="The story or task it is on: a key in this revision, or the reference of an "
        "item that continues one. A continued criterion stays on the item it was on."
    )


class PlanRevised(BaseModel):
    plan: int = Field(description="The plan's number: the same plan, revised.")
    revision: int = Field(description="The revision this made: a person reads it next.")
    items: list[PlannedItem] = Field(
        description="Every item of the revision, in the order you gave them, with its key and "
        "reference: use them in the next revision."
    )
    created: list[str] = Field(description="The references of the items this revision added.")
    removed: list[str] = Field(description="The references of the items it took out (dropped).")
    counts: dict[str, int] = Field(
        description="items, created, kept, removed, links_added, links_removed, criteria_added "
        "and criteria_dropped."
    )


def _clean_note(note: str | None) -> str | None:
    if note is None or not note.strip():
        return None
    cleaned = note.strip()
    if len(cleaned) > MOST_NOTE_CHARS:
        raise Refusal(f"note may be at most {MOST_NOTE_CHARS:,} characters. Nothing was changed.")
    refuse_secrets({"note": cleaned})
    return cleaned


def _norm(link_type: str, source: str, target: str) -> tuple[str, str, str]:
    """A link as one thing, whichever way a two-way link was written."""
    if link_type in _TWO_WAY:
        low, high = sorted((source, target))
        return link_type, low, high
    return link_type, source, target


class PlanReviser:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def revise(
        self,
        *,
        actor: str,
        plan: int,
        items: list[RevisionItem],
        links: list[PlanLink] | None,
        rules: list[RevisionRule] | None,
        note: str | None,
    ) -> PlanRevised:
        said = _clean_note(note)
        if not items:
            raise Refusal(
                "A revision lists every item the plan holds now: give at least one item. "
                "Nothing was changed."
            )
        async with self._database.transaction() as conn:
            found = await fetch_one(
                conn, f"SELECT {PLAN_COLUMNS} FROM plan_state WHERE id = %s", (plan,)
            )
            if found is None:
                raise Refusal(f"There is no plan {plan} in this project. list_plans shows them.")
            locked: dict[str, dict[str, Any]] = {}
            for ref in sorted(found["items"] or []):
                locked[ref] = await require_node(conn, ref, lock=True)
            again = await fetch_one(
                conn, f"SELECT {PLAN_COLUMNS} FROM plan_state WHERE id = %s", (plan,)
            )
            assert again is not None
            for ref in sorted(again["items"] or []):
                if ref not in locked:
                    locked[ref] = await require_node(conn, ref, lock=True)
            if again["state"] in ("approved", "rejected"):
                raise Refusal(
                    f"Plan {plan} was already {again['state']}, so it cannot be revised: a plan "
                    "that is decided is final. Draft a new one. Nothing was changed."
                )
            current_refs: list[str] = list(again["items"] or [])
            current = set(current_refs)
            revision = int(again["revision"]) + 1
            context = await self._context(conn, current_refs)
            problems: list[str] = []

            # Which entries continue which items.
            kept: dict[str, RevisionItem] = {}
            for position, item in enumerate(items, start=1):
                if item.ref is None:
                    continue
                where = f"Item {position} (key {item.key!r}, {item.title.strip()[:40]!r})"
                ref = item.ref.strip().lower()
                if not is_node_ref(ref):
                    problems.append(
                        f"{where}: ref must be the reference of an item of Plan {plan}, such as "
                        "story:12."
                    )
                elif ref not in current:
                    problems.append(
                        f"{where}: {ref} is not an item of Plan {plan}, so a revision cannot "
                        "continue it. Leave ref out to add a new item."
                    )
                elif ref in kept:
                    problems.append(f"{where}: {ref} is continued by more than one entry.")
                else:
                    if locked[ref]["kind"] != item.kind:
                        problems.append(
                            f"{where}: {ref} is {_a(locked[ref]['kind'])}, and what an item is "
                            "cannot change: remove it and add a new one."
                        )
                    kept[ref] = item
            removed = [ref for ref in current_refs if ref not in kept]
            key_of = {ref: item.key for ref, item in kept.items()}

            # Links and criteria left out are kept, for the items that are kept.
            wanted_links = (
                links
                if links is not None
                else [
                    PlanLink(source=row["source"], target=row["target"], link_type=row["link_type"])
                    for row in context["links"]
                    if row["source"] in kept and row["target"] not in removed
                ]
            )
            wanted_rules = (
                rules
                if rules is not None
                else [
                    RevisionRule(
                        id=row["id"], title=row["title"], body=row["body"], node=row["node"]
                    )
                    for row in context["rules"]
                    if row["node"] in kept
                ]
            )

            def rewrite(value: str | None, where: str) -> str | None:
                """A reference to an item of the plan, as that item's key in this revision."""
                if value is None:
                    return None
                reference = _existing(value)
                if reference is None:
                    return value
                if reference in key_of:
                    return key_of[reference]
                if reference in current:
                    problems.append(f"{where}: refers to {reference}, which this revision removes.")
                return value

            checked_items: list[PlanItem] = []
            for position, item in enumerate(items, start=1):
                where = f"Item {position} (key {item.key!r}, {item.title.strip()[:40]!r})"
                checked_items.append(
                    PlanItem.model_validate(
                        {**item.model_dump(exclude={"ref"}), "parent": rewrite(item.parent, where)}
                    )
                )
            checked_links = [
                PlanLink(
                    source=str(rewrite(link.source, f"Link {position}")),
                    target=str(rewrite(link.target, f"Link {position}")),
                    link_type=link.link_type,
                )
                for position, link in enumerate(wanted_links, start=1)
            ]
            context_rules = {row["id"]: row for row in context["rules"]}
            kept_rules: dict[int, RevisionRule] = {}
            checked_rules: list[PlanRule] = []
            for position, rule in enumerate(wanted_rules, start=1):
                where = f"Acceptance criterion {position} ({rule.title.strip()[:40]!r})"
                holder = rewrite(rule.node, where)
                if rule.id is not None:
                    if rule.id not in context_rules:
                        problems.append(
                            f"{where}: {rule.id} is not an acceptance criterion of Plan {plan}, "
                            "so a revision cannot continue it. Leave id out to add a new one."
                        )
                    elif rule.id in kept_rules:
                        problems.append(f"{where}: {rule.id} is continued by more than one entry.")
                    else:
                        kept_rules[rule.id] = rule
                        before = context_rules[rule.id]
                        if holder != key_of.get(before["node"]):
                            problems.append(
                                f"{where}: it is on {before['node']}, and a criterion stays "
                                "where it is: leave it out and add a new one on the other item."
                            )
                status = rule.status or (
                    context_rules[rule.id]["status"] if rule.id in context_rules else "open"
                )
                checked_rules.append(
                    PlanRule(title=rule.title, body=rule.body, status=status, node=holder)
                )

            referenced = sorted(
                {
                    reference
                    for value in (
                        *(item.parent for item in checked_items),
                        *(end for link in checked_links for end in (link.source, link.target)),
                        *(rule.node for rule in checked_rules),
                    )
                    if value is not None and (reference := _existing(value)) is not None
                }
            )
            existing = {
                row["ref"]
                for row in await fetch_all(
                    conn, "SELECT ref FROM nodes WHERE ref = ANY(%s)", (referenced,)
                )
            }
            # What waits on what outside the plan, with the plan's own items by their keys, so that
            # a circle through the plan is seen. The links that start at the plan's items are what
            # this revision replaces.
            edges = {
                (key_of.get(row["source"], row["source"]), key_of.get(row["target"], row["target"]))
                for row in await fetch_all(
                    conn, "SELECT source, target FROM links WHERE link_type = 'needs_first'"
                )
                if row["source"] not in current
            }
            problems.extend(
                _plan_problems(checked_items, checked_links, checked_rules, existing, edges)
            )
            problems.extend(await self._removal_problems(conn, removed, current_refs))
            if problems:
                raise Refusal(_listed("The plan was not revised", problems, "changed"))

            try:
                result = await self._apply(
                    conn,
                    actor=actor,
                    plan=plan,
                    revision=revision,
                    said=said,
                    items=items,
                    checked_items=checked_items,
                    checked_links=checked_links,
                    kept=kept,
                    removed=removed,
                    kept_rules=kept_rules,
                    checked_rules=checked_rules,
                    wanted_rules=wanted_rules,
                    context=context,
                    current=current,
                    locked=locked,
                )
            except psycopg.errors.RaiseException as error:
                raise Refusal(
                    f"The plan was not revised: {error.diag.message_primary} Nothing was changed."
                ) from None
            except psycopg.errors.UniqueViolation:
                raise Refusal(
                    "The plan was not revised: one of its links already exists between those "
                    "nodes. Nothing was changed."
                ) from None
        return result

    async def _context(self, conn: Any, refs: list[str]) -> dict[str, list[dict[str, Any]]]:
        """The links that start at the plan's items and its acceptance criteria that are not
        dropped, as they are now, with their rows held."""
        return {
            "links": await fetch_all(
                conn,
                "SELECT id, source, target, link_type FROM links "
                "WHERE source = ANY(%(refs)s) OR target = ANY(%(refs)s) ORDER BY id",
                {"refs": refs},
            ),
            "rules": await fetch_all(
                conn,
                "SELECT id, node, title, body, status, version FROM rules "
                "WHERE node = ANY(%s) AND status <> 'dropped' ORDER BY id FOR UPDATE",
                (refs,),
            ),
        }

    async def _removal_problems(
        self, conn: Any, removed: list[str], current_refs: list[str]
    ) -> list[str]:
        """What stops items being removed: work outside the plan that waits for them or hangs
        under them."""
        if not removed:
            return []
        problems = []
        for row in await fetch_all(
            conn,
            "SELECT source, target FROM links WHERE link_type = 'needs_first' "
            "AND target = ANY(%s) AND NOT (source = ANY(%s)) ORDER BY id",
            (removed, current_refs),
        ):
            problems.append(
                f"{row['source']} needs {row['target']} first, and this revision removes "
                f"{row['target']}: it would wait for dropped work for ever. Remove that link, or "
                f"keep {row['target']}."
            )
        for row in await fetch_all(
            conn,
            "SELECT ref, parent FROM nodes WHERE parent = ANY(%s) AND status <> 'dropped' "
            "AND NOT (ref = ANY(%s)) ORDER BY kind_rank, number",
            (removed, current_refs),
        ):
            problems.append(
                f"{row['ref']} is under {row['parent']}, which this revision removes, and it is "
                f"not part of the plan. Move it first, or keep {row['parent']}."
            )
        return problems

    async def _apply(
        self,
        conn: Any,
        *,
        actor: str,
        plan: int,
        revision: int,
        said: str | None,
        items: list[RevisionItem],
        checked_items: list[PlanItem],
        checked_links: list[PlanLink],
        kept: dict[str, RevisionItem],
        removed: list[str],
        kept_rules: dict[int, RevisionRule],
        checked_rules: list[PlanRule],
        wanted_rules: list[RevisionRule],
        context: dict[str, list[dict[str, Any]]],
        current: set[str],
        locked: dict[str, dict[str, Any]],
    ) -> PlanRevised:
        refs: dict[str, str] = {item.key: ref for ref, item in kept.items()}

        def resolve(value: str) -> str:
            return _existing(value) or refs[value]

        # New items, parents before what is under them.
        created: list[str] = []
        for kind in KINDS:
            for entry, checked in zip(items, checked_items, strict=True):
                if entry.ref is not None or entry.kind != kind:
                    continue
                row = await insert_node(
                    conn,
                    actor=actor,
                    kind=kind,
                    title=clean_title(entry.title),
                    parent=None if checked.parent is None else resolve(checked.parent),
                    body=entry.body,
                    priority=entry.priority,
                    size=entry.size,
                    start_on=parse_day("start_on", entry.start_on),
                    target_on=parse_day("target_on", entry.target_on),
                )
                refs[entry.key] = row["ref"]
                created.append(row["ref"])

        # Items that continue one: where they hang, then what they say.
        for ref, entry in kept.items():
            checked = checked_items[items.index(entry)]
            parent = None if checked.parent is None else resolve(checked.parent)
            if parent is not None and parent != locked[ref]["parent"]:
                await move_in(conn, actor=actor, node=ref, new_parent=parent)
            now = await require_node(conn, ref, lock=True)
            fields = {
                "title": clean_title(entry.title),
                "body": entry.body,
                "priority": entry.priority,
                "size": entry.size,
                "start_on": parse_day("start_on", entry.start_on),
                "target_on": parse_day("target_on", entry.target_on),
            }
            changes = {field: value for field, value in fields.items() if value != now[field]}
            if changes:
                await apply_node_changes(conn, actor, now, changes)

        # Items that nothing continues are dropped, and say why.
        why = f"Removed in revision {revision} of Plan {plan}" + (f": {said}" if said else ".")
        for ref in removed:
            now = await require_node(conn, ref, lock=True)
            if now["status"] != "dropped":
                await change_status(conn, actor, now, "dropped")
            await record_event(
                conn,
                node=ref,
                action="handoff",
                actor=actor,
                before=None,
                after={"outcome": "dropped", "note": why},
            )

        # Links: the ones that start at the plan's items become what the revision says.
        link_wanted = {
            _norm(link.link_type, resolve(link.source), resolve(link.target)): (
                resolve(link.source),
                resolve(link.target),
                link.link_type,
            )
            for link in checked_links
        }
        have = {
            _norm(row["link_type"], row["source"], row["target"]): row for row in context["links"]
        }
        links_removed = 0
        for key, row in have.items():
            if key not in link_wanted and row["source"] in current:
                await delete_link(conn, actor=actor, link_id=row["id"])
                links_removed += 1
        links_added = 0
        for key, (source, target, link_type) in link_wanted.items():
            if key not in have:
                await insert_link(
                    conn, actor=actor, source=source, target=target, link_type=link_type
                )
                links_added += 1

        # Acceptance criteria.
        by_id = {row["id"]: row for row in context["rules"]}
        criteria_dropped = 0
        for rule_id, rule in kept_rules.items():
            before = by_id[rule_id]
            wanted_rule = {
                "title": rule.title.strip(),
                "body": rule.body,
                "status": rule.status or before["status"],
            }
            changes = {
                field: value for field, value in wanted_rule.items() if value != before[field]
            }
            if changes:
                await apply_rule_changes(conn, actor, before, changes)
                criteria_dropped += 1 if changes.get("status") == "dropped" else 0
        for rule_id, before in by_id.items():
            if rule_id not in kept_rules:
                await apply_rule_changes(conn, actor, before, {"status": "dropped"})
                criteria_dropped += 1
        criteria_added = 0
        for rule, checked_rule in zip(wanted_rules, checked_rules, strict=True):
            if rule.id is None:
                await insert_rule(
                    conn,
                    actor=actor,
                    title=checked_rule.title.strip(),
                    body=checked_rule.body,
                    status=checked_rule.status,
                    node=None if checked_rule.node is None else resolve(checked_rule.node),
                )
                criteria_added += 1

        new_refs = [refs[entry.key] for entry in items]
        await record_event(
            conn,
            action=PLAN_REVISED,
            actor=actor,
            before=None,
            after={
                "plan": plan,
                "revision": revision,
                "items": new_refs,
                "removed": removed,
                "snapshot": await plan_snapshot(conn, new_refs),
                **({"note": said} if said else {}),
            },
        )
        titles = {
            row["ref"]: row["title"]
            for row in await fetch_all(
                conn, "SELECT ref, title FROM nodes WHERE ref = ANY(%s)", (new_refs,)
            )
        }
        return PlanRevised(
            plan=plan,
            revision=revision,
            items=[
                PlannedItem(
                    key=entry.key,
                    ref=refs[entry.key],
                    kind=entry.kind,
                    title=titles[refs[entry.key]],
                )
                for entry in items
            ],
            created=created,
            removed=removed,
            counts={
                "items": len(items),
                "created": len(created),
                "kept": len(kept),
                "removed": len(removed),
                "links_added": links_added,
                "links_removed": links_removed,
                "criteria_added": criteria_added,
                "criteria_dropped": criteria_dropped,
            },
        )
