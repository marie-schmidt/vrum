"""Recall: everything known about one node or rule, by exact links only, in separate labelled
sections, each saying why an entry is there. An epic, story or task also gets what is linked to the
story and epic it belongs to, and an acceptance criterion (a rule) what is linked to the story or
task it is on and to the story and epic above that (the same lookup `next_work` uses, in
lineage.py); each such entry says which item it comes down from. Every entry carries its status,
so an unconfirmed note is never presented as confirmed and an ended one never as in force."""

from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    check_choice,
    fetch_all,
    fetch_one,
    page_size,
    parse_node_ref,
    split_node_ref,
    split_page,
)
from vrum.db import Database
from vrum.graph import require_node
from vrum.lineage import (
    CRITERIA_IN_FORCE,
    chain_of,
    criteria_holders,
    nearest_links,
    note_in_chain,
)
from vrum.notes import Note, count_notes, page_notes
from vrum.records import IN_FORCE
from vrum.rules import Rule, require_rule, rule_list_columns
from vrum.search import IndexNote, Searcher, SearchResult

RECALL_LISTS = ("all", "notes", "project_wide_notes", "past_notes", "acceptance_criteria")
# The most characters of notes and criteria one recall gives back, unless it is asked for more. The
# entries that do not fit are left out and the section says how to read on (its next_after_id).
DEFAULT_RECALL_CHARS = 40_000
LEAST_RECALL_CHARS = 1_000
MOST_RECALL_CHARS = 1_000_000


class RecalledNote(BaseModel):
    why: str = Field(description="Why this note is in the recall.")
    inherited_from: str | None = Field(
        description="The reference of the epic or story this note is linked to, when it is not "
        "linked to this item itself: it comes down from there. Empty when it is linked to this "
        "item directly, and for a project-wide note."
    )
    note: Note


class NoteSection(BaseModel):
    items: list[RecalledNote]
    count: int = Field(description="How many there are in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass as after_id, with show set to this section, for the next page."
    )


class RecalledCriterion(BaseModel):
    why: str = Field(description="Why this acceptance criterion is in the recall.")
    on: str = Field(
        description="The reference of the story or task this acceptance criterion is placed on, "
        "such as story:12 or task:40."
    )
    inherited_from: str | None = Field(
        description="The reference of the story it is placed on, when this item is a task under "
        "that story rather than the story itself. Empty when it is on this item itself."
    )
    criterion: Rule


class CriteriaSection(BaseModel):
    items: list[RecalledCriterion]
    count: int = Field(description="How many there are in all, across pages.")
    next_after_id: int | None = Field(
        description="Pass as after_id, with show set to acceptance_criteria, for the next page."
    )


class SimilarSection(BaseModel):
    items: list[SearchResult] = Field(
        description="Notes in force (unconfirmed or confirmed) that are alike in meaning but not "
        "linked to this item. Suggestions only: nothing here was linked by anyone."
    )
    count: int = Field(description="How many there are in all.")
    index: IndexNote


class Recall(BaseModel):
    notes: NoteSection = Field(
        description="Notes in force (unconfirmed or confirmed) linked to this item, or to the "
        "story or epic it belongs to (each says which), other than project-wide ones. Notes a "
        "person decided come first, then the others, each by id. Only what is linked, nothing "
        "inferred. Each has a status, so you can see whether a person confirmed it."
    )
    project_wide_notes: NoteSection = Field(
        description="Every project-wide note in force, linked to this item or not: they apply "
        "throughout the project. Notes a person decided come first."
    )
    past_notes: NoteSection = Field(
        description="Notes linked to this item, or to the story or epic it belongs to, that were "
        "superseded or withdrawn, with what replaced them or why they were given up. Shown so "
        "you can see what changed; they are no longer in force."
    )
    acceptance_criteria: CriteriaSection = Field(
        description="The acceptance criteria that are open or met and apply to this item: its "
        "own, and for a task its story's too (each says which item it is on). An epic has none "
        "of its own, and nothing comes up from the stories and tasks under it. Bodies are left "
        "out: ask list_rules for detail. Empty for an acceptance criterion itself."
    )
    similar: SimilarSection | None = Field(
        description="Only when you ask for it with with_similar: what is alike in meaning but "
        "not linked. Everything else here is exact links only."
    )
    max_characters: int = Field(
        default=DEFAULT_RECALL_CHARS,
        description="The most characters of entries this answer was allowed to hold (max_chars). "
        "A section always gives at least its first entry, so paging on makes progress.",
    )
    left_out: dict[str, int] = Field(
        default_factory=dict,
        description="For each section that was cut to fit, how many entries were left out of this "
        "page. Empty when nothing was cut.",
    )
    cut_note: str | None = Field(
        default=None, description="In words, what was cut and how to read the rest."
    )


_SHOWN_NAMES = {
    "notes": "notes",
    "project_wide_notes": "project-wide notes",
    "acceptance_criteria": "acceptance criteria",
    "past_notes": "past notes",
}


def _fit(
    sections: dict[str, NoteSection],
    criteria: CriteriaSection,
    similar: SimilarSection | None,
    budget: int,
) -> dict[str, int]:
    """Keep whole entries, turn by turn from each section, while they fit in `budget` characters;
    the rest are left out of this page and the section's next_after_id says where to read on. A
    section always keeps its first entry (so reading on always moves forward, and one very long
    note is still given, whole). Gives back how many each section lost."""
    lists: dict[str, NoteSection | CriteriaSection] = {
        "notes": sections["notes"],
        "project_wide_notes": sections["project_wide_notes"],
        "acceptance_criteria": criteria,
        "past_notes": sections["past_notes"],
    }
    sizes = {
        name: [len(entry.model_dump_json()) for entry in section.items]
        for name, section in lists.items()
    }
    used = len(similar.model_dump_json()) if similar is not None else 0
    kept = {name: 0 for name in lists}
    open_sections = [name for name in lists if sizes[name]]
    while open_sections:
        for name in list(open_sections):
            size = sizes[name][kept[name]]
            if kept[name] > 0 and used + size > budget:
                open_sections.remove(name)
                continue
            used += size
            kept[name] += 1
            if kept[name] == len(sizes[name]):
                open_sections.remove(name)
    left: dict[str, int] = {}
    for name, section in lists.items():
        dropped = len(sizes[name]) - kept[name]
        if not dropped:
            continue
        left[name] = dropped
        section.items = section.items[: kept[name]]
        last = section.items[-1]
        section.next_after_id = (
            last.criterion.id if isinstance(last, RecalledCriterion) else last.note.id
        )
    return left


def _comes_down_words(via: str, thing: str | int, chain: list[str]) -> str:
    """Where an inherited note comes from, in plain words. For an acceptance criterion the chain
    starts at the story or task it is on."""
    if isinstance(thing, str):
        return f"linked to {via}, which this {split_node_ref(thing)[0]} belongs to"
    if via == chain[0]:
        return f"linked to {via}, which this acceptance criterion is placed on"
    return f"linked to {via}, which holds the story or task this acceptance criterion is on"


def _note_why(note: Note, what: str, words: str | None) -> str:
    if note.project_wide and note.status in IN_FORCE:
        return "a project-wide note, so it applies throughout the project"
    base = words or f"linked directly to this {'acceptance criterion' if what == 'rule' else what}"
    if note.status == "superseded":
        return f"{base}; replaced by note {note.superseded_by_id}"
    if note.status == "withdrawn":
        return f"{base}; withdrawn: {note.status_reason}"
    return base


async def _comes_down_from(
    conn: AsyncConnection, ids: list[int], *, thing: str | int, chain: list[str]
) -> dict[int, str]:
    """For each note that is not linked to the thing itself, the nearest epic or story above it
    that the note is linked to."""
    if not ids or not chain:
        return {}
    nearest = await nearest_links(conn, ids, chain)
    if isinstance(thing, str):
        return {note: ref for note, ref in nearest.items() if ref != thing}
    direct = {
        row["note_id"]
        for row in await fetch_all(
            conn,
            "SELECT note_id FROM note_rules WHERE rule_id = %(thing)s AND note_id = ANY(%(ids)s)",
            {"thing": thing, "ids": ids},
        )
    }
    return {note: ref for note, ref in nearest.items() if note not in direct}


class Recaller:
    def __init__(self, database: Database, searcher: Searcher) -> None:
        self._database = database
        self._searcher = searcher

    async def recall(
        self,
        *,
        node: str | None,
        rule_id: int | None,
        show: str,
        limit: int,
        after_id: int | None,
        with_similar: bool,
        max_chars: int = DEFAULT_RECALL_CHARS,
    ) -> Recall:
        """What is known about one node or rule, by exact links, and, if asked, what is alike."""
        limit = page_size(limit)
        if not LEAST_RECALL_CHARS <= max_chars <= MOST_RECALL_CHARS:
            raise Refusal(
                f"max_chars must be a whole number from {LEAST_RECALL_CHARS} to "
                f"{MOST_RECALL_CHARS}, but it is {max_chars}."
            )
        check_choice("show", show, RECALL_LISTS)
        if (node is None) == (rule_id is None):
            raise Refusal(
                "Give exactly one of node (an epic, story or task, such as story:12) or rule_id: "
                "the thing to recall for."
            )
        if after_id is not None and show == "all":
            raise Refusal(
                "after_id only works for one section at a time: set show to one of "
                f"{', '.join(RECALL_LISTS[1:])}."
            )
        thing: str | int
        if node is not None:
            node = parse_node_ref(node, "node")
            what, thing = "node", node
        else:
            assert rule_id is not None
            what, thing = "rule", rule_id
        async with self._database.transaction() as conn:
            # What is above the item: its own links count, and so do the links of its story and
            # epic. An acceptance criterion's own links come first, then those of the story or
            # task it is on and of the items above that.
            chain: list[str] = []
            if isinstance(thing, str):
                await require_node(conn, thing)
                chain = await chain_of(conn, thing)
            else:
                rule = await require_rule(conn, thing)
                if rule["node"] is not None:
                    chain = await chain_of(conn, rule["node"])
            params = {"thing": thing, "chain": chain}
            linked = note_in_chain("n.id")
            if what == "rule":
                linked = (
                    "(n.id IN (SELECT note_id FROM note_rules WHERE rule_id = %(thing)s) "
                    f"OR {linked})"
                )
            # The notes a person decided come before the other notes of each list.
            in_force = "n.status IN ('unconfirmed', 'confirmed')"
            note_lists = {
                "notes": (f"{linked} AND {in_force} AND NOT n.project_wide", True),
                "project_wide_notes": (f"n.project_wide AND {in_force}", True),
                "past_notes": (f"{linked} AND n.status IN ('superseded', 'withdrawn')", False),
            }
            sections: dict[str, NoteSection] = {}
            for name, (where, decided_first) in note_lists.items():
                found, next_note_id = (
                    await page_notes(
                        conn, where, params, limit, after_id, decided_first=decided_first
                    )
                    if show in ("all", name)
                    else ([], None)
                )
                came_from = await _comes_down_from(
                    conn, [item.id for item in found], thing=thing, chain=chain
                )
                sections[name] = NoteSection(
                    items=[
                        RecalledNote(
                            why=_note_why(
                                item,
                                what,
                                _comes_down_words(came_from[item.id], thing, chain)
                                if item.id in came_from
                                else None,
                            ),
                            inherited_from=None
                            if item.project_wide and item.status in IN_FORCE
                            else came_from.get(item.id),
                            note=item,
                        )
                        for item in found
                    ],
                    count=await count_notes(conn, where, params),
                    next_after_id=next_note_id,
                )
            criteria = CriteriaSection(items=[], count=0, next_after_id=None)
            holders = criteria_holders(chain) if isinstance(thing, str) else []
            if holders:
                criteria_params = {"holders": holders, "after": after_id or 0}
                total = await fetch_one(
                    conn,
                    f"SELECT count(*) AS total FROM rules WHERE {CRITERIA_IN_FORCE}",
                    criteria_params,
                )
                assert total is not None
                found_rules: list[dict[str, Any]] = []
                next_rule_id: int | None = None
                if show in ("all", "acceptance_criteria"):
                    rows = await fetch_all(
                        conn,
                        f"SELECT {rule_list_columns(False)} FROM rules "
                        f"WHERE {CRITERIA_IN_FORCE} AND id > %(after)s ORDER BY id "
                        "LIMIT %(fetch)s",
                        {**criteria_params, "fetch": limit + 1},
                    )
                    found_rules, next_rule_id = split_page(rows, limit)
                criteria = CriteriaSection(
                    items=[
                        RecalledCriterion(
                            why=(
                                f"placed on this {split_node_ref(str(thing))[0]}"
                                if row["node"] == thing
                                else f"placed on {row['node']}, which this "
                                f"{split_node_ref(str(thing))[0]} belongs to"
                            ),
                            on=row["node"],
                            inherited_from=None if row["node"] == thing else row["node"],
                            criterion=Rule.model_validate(row),
                        )
                        for row in found_rules
                    ],
                    count=int(total["total"]),
                    next_after_id=next_rule_id,
                )
            exclude: set[tuple[str, int]] = set()
            if with_similar:
                # Whatever is linked or already shown is not "similar": it is known.
                for row in await fetch_all(
                    conn,
                    f"SELECT n.id FROM notes n WHERE {linked} OR (n.project_wide AND {in_force})",
                    params,
                ):
                    exclude.add(("note", row["id"]))
        similar: SimilarSection | None = None
        if with_similar:
            similar_items, count, note = await self._searcher.similar(
                node=node,
                rule_id=rule_id,
                wanted_types=["note"],
                exclude=exclude,
                limit=limit,
            )
            similar = SimilarSection(items=similar_items, count=count, index=note)
        left_out = _fit(sections, criteria, similar, max_chars)
        cut_note = None
        if left_out:
            cut_note = (
                f"To stay within {max_chars:,} characters this left out "
                + ", ".join(f"{count} {_SHOWN_NAMES[name]}" for name, count in left_out.items())
                + ". Read on with show set to a section and after_id set to its next_after_id, or "
                "ask again with a larger max_chars."
            )
        return Recall(
            notes=sections["notes"],
            project_wide_notes=sections["project_wide_notes"],
            past_notes=sections["past_notes"],
            acceptance_criteria=criteria,
            similar=similar,
            max_characters=max_chars,
            left_out=left_out,
            cut_note=cut_note,
        )
