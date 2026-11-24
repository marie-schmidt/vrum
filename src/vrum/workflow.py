"""The agent workflow: what an agent needs to do a day's work in few calls and small answers.

`next_work` takes the most urgent free work and returns it with everything needed to start, in one
call. `finish_work` ends work and leaves a handoff note. `create_plan` creates a whole plan or
nothing. `update_nodes` changes many nodes at once. `changes_since` says what is new. Creating a
node or a plan also says whether something like it already exists.

Everything in the context of a piece of work comes by exact link, from the graph. The only thing
that comes by meaning is the list of similar items, and it is labelled as suggestions."""

import base64
import binascii
import json
from datetime import date
from typing import Any

import psycopg
from psycopg import AsyncConnection
from pydantic import BaseModel, Field, ValidationError

from vrum.asks import AskState, asks_of, raise_ask
from vrum.checks import checks_of, refuse_while_unticked
from vrum.claims import (
    HANDOFF_OUTCOMES,
    Claim,
    clean_note,
    current_claim,
    lease_seconds_ok,
    release_in,
    take_claim,
)
from vrum.common import (
    Ref,
    Refusal,
    check_choice,
    fetch_all,
    fetch_one,
    is_node_ref,
    node_order,
    node_words,
    page_size,
    parse_day,
    parse_node_ref,
    record_event,
    refuse_secrets,
    split_node_ref,
    split_page,
)
from vrum.db import Database
from vrum.definition_of_done import read_definition
from vrum.gates import MOST_TITLE_CHARS as MOST_PLAN_TITLE_CHARS
from vrum.gates import PLAN_PROPOSED, plan_snapshot
from vrum.graph import (
    EVENT_COLUMNS,
    KINDS,
    PRIORITIES,
    SIZES,
    Event,
    Graph,
    Handoff,
    Node,
    change_status,
    check_dates,
    clean_title,
    handoffs_of,
    insert_node,
    node_state,
    require_node,
)
from vrum.lineage import (
    CRITERIA_IN_FORCE,
    CRITERIA_ORDER,
    chain_of,
    criteria_holders,
    note_in_chain,
)
from vrum.links import LINK_TYPES, insert_link
from vrum.notes import require_note
from vrum.plan_checks import circle_through
from vrum.plan_templates import (
    MOST_TEMPLATE_BYTES,
    PlanTemplateInfo,
    PlanTemplatePage,
    TemplateProblems,
    clean_name,
    expand,
    parse_template,
    sample_refs,
    sample_values,
)
from vrum.records import IN_FORCE
from vrum.rules import RULE_STATUSES, insert_rule, require_rule
from vrum.search import Searcher, SearchResult

# What a story or task is picked by: urgent first, then the earliest target date, then the oldest.
_URGENCY = (
    "CASE s.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, "
    "s.target_on NULLS LAST, s.created_at, s.kind_rank, s.number"
)
# Free work: a story or task nobody holds that can be started (ready), or that someone took and
# left when their claim ran out. A story whose tasks are still open is worked through those tasks,
# so it is not handed out as well.
_FREE_WORK = (
    "s.kind IN ('story', 'task') AND s.claimed_by IS NULL AND cardinality(s.waiting_on) = 0 "
    "AND cardinality(s.held_by) = 0 "
    "AND (s.status = 'ready' OR (s.status = 'in_progress' AND s.lapsed_claim_by IS NOT NULL)) "
    "AND (s.kind = 'task' OR NOT EXISTS (SELECT 1 FROM nodes t WHERE t.parent = s.ref "
    "AND t.status NOT IN ('done', 'dropped'))) "
    "AND (%(within)s::text IS NULL OR s.ref = %(within)s OR s.parent = %(within)s "
    "OR s.parent IN (SELECT ref FROM nodes WHERE parent = %(within)s))"
)
DEFAULT_CONTEXT_CHARS = 10_000
_PARENT_BODY_CHARS = 600
_RULE_BODY_CHARS = 800
_SIMILAR_WANTED = 5
# What each section is called in the sentence that says what was left out.
_SECTION_NAMES = {
    "parents": "parents",
    "acceptance_criteria": "acceptance criteria",
    "asks": "asks",
    "handoffs": "handoff notes",
    "prerequisites": "prerequisites",
    "discovered_from": "items it was discovered from",
    "notes": "notes",
    "project_wide_notes": "project-wide notes",
    "past_notes": "past notes",
    "unblocks": "items it unblocks",
    "similar": "similar items",
}
# The order the context is filled in. What is cut, when it is too large, is cut from the end.
_SECTION_ORDER = tuple(_SECTION_NAMES)


class Brief(BaseModel):
    ref: str = Field(description="Its reference, such as story:12.")
    kind: str = Field(description="epic, story or task.")
    title: str
    status: str
    size: str | None = Field(
        default=None, description="Its size (xs, s, m, l or xl), when someone has said."
    )


class ContextParent(BaseModel):
    ref: str = Field(description="Its reference, such as epic:3.")
    kind: str
    title: str
    status: str
    size: str | None = Field(
        description="Its size (xs, s, m, l or xl), when someone has said. An epic has none."
    )
    body_preview: str = Field(description="The first characters of its body.")
    body_chars: int = Field(description="How long its whole body is. Read it with get_node.")


class DefinitionCheck(BaseModel):
    item: str = Field(description="The check's key: what set_check takes.")
    text: str = Field(description="What the check says: do this before the item is done.")
    person_only: bool = Field(
        description="True when only a person can judge this check: do not tick it. Say in your "
        "handoff note what they should look at."
    )
    verifier_only: bool = Field(
        description="True when only a verifier that did not do the work on this item may tick "
        "this check (and a person): if that is not you, do not tick it. Finish the item as "
        "blocked and say in the handoff note that it is ready for a verifier."
    )
    github: bool = Field(
        description="True when a verifier's tick of this check starts its evidence with a GitHub "
        "pull request address, then says what was seen there."
    )
    checked: bool = Field(description="True when it is already ticked on this item.")


class ContextCriterion(BaseModel):
    id: int
    on: str = Field(
        description="The reference of the story or task it is placed on, such as story:12 or "
        "task:40."
    )
    title: str
    status: str = Field(description="open or met. Dropped acceptance criteria are not shown.")
    body: str = Field(description="Its body, cut if it is long.")
    body_chars: int = Field(description="How long the whole body is.")


class ContextNote(BaseModel):
    id: int
    text: str
    why: str = Field(description="Why it is so, or why it was chosen.")
    decided: bool = Field(description="True when a person decided it.")
    decided_by: str | None = Field(description="The person who decided it, if one did.")
    decided_on: date | None
    project_wide: bool = Field(description="True when it applies throughout the project.")
    status: str = Field(
        description="unconfirmed or confirmed (in force; confirmed means a person reviewed it), "
        "superseded or withdrawn."
    )
    superseded_by_id: int | None
    status_reason: str | None = Field(description="Why it was withdrawn.")
    confirmed_by: str | None = Field(description="The person who confirmed it.")
    recorded_by: str = Field(description="The name of the person or agent that recorded it.")
    recorded_by_kind: str = Field(description="person or agent (unknown in an old import).")
    linked_to: list[str] = Field(
        description="Which of this item and its parents it is linked to, by reference. Empty for a "
        "project-wide note that is not linked to any of them."
    )


class Prerequisite(BaseModel):
    node: Brief = Field(description="Something this has to wait for, or waited for.")
    handoffs: list[Handoff] = Field(
        description="The notes left by whoever worked on it: what the next person must know."
    )


class Dependent(BaseModel):
    node: Brief
    becomes_ready_when_done: bool = Field(
        description="True when finishing this item is all it is waiting for."
    )


class WorkContext(BaseModel):
    parents: list[ContextParent] = Field(
        description="The epic and story it belongs to, top first, with the start of their bodies."
    )
    acceptance_criteria: list[ContextCriterion] = Field(
        description="The acceptance criteria that apply to it: its own, and for a task its "
        "story's too, each saying which item it is on. Finish it only when they are met."
    )
    definition_of_done: str = Field(
        description="What every story or task of the project must satisfy before it is done, on "
        "top of its own acceptance criteria. Empty when nobody has set one. Cut to fit; "
        "definition_of_done_chars says how long it is in all."
    )
    definition_of_done_chars: int = Field(
        description="How long the whole definition of done is. More than the text shown when it "
        "was cut (see cut_note)."
    )
    definition_checks: list[DefinitionCheck] = Field(
        description="The checks of the definition of done (its list lines) for this item, each "
        "ticked or not. Before you finish it as done, do what each says (run the tests, and so "
        "on), then tick each one with set_check, saying how you checked. A check with "
        "person_only is not yours to tick: finish the item as blocked and say in the handoff "
        "note what a person should look at. One with verifier_only is for a verifier that did "
        "not do the work on this item: if that is not you, finish the item as blocked and say "
        "in the handoff note that it is ready for a verifier."
    )
    asks: list[AskState] = Field(
        description="What was asked of a person about this very item, the open ones first and "
        "then the latest answered, each with the person's answer when there is one. An answer is "
        "final: read it before you start. An ask still open means you are waiting for a person."
    )
    handoffs: list[Handoff] = Field(
        description="Notes left by earlier holders of this very item, oldest first."
    )
    prerequisites: list[Prerequisite] = Field(
        description="What it needs first (needs_first), with the notes left on each."
    )
    discovered_from: list[Brief] = Field(
        description="The work this was found while doing (a discovered_from link): where it came "
        "from, so you can read why it exists. It never made this wait."
    )
    notes: list[ContextNote] = Field(
        description="Notes in force linked to this item or its parents, other than project-wide "
        "ones. Notes a person decided come first. Each has a status that says whether a person "
        "confirmed it."
    )
    project_wide_notes: list[ContextNote] = Field(
        description="Every project-wide note in force. They apply throughout the project. "
        "Notes a person decided come first."
    )
    past_notes: list[ContextNote] = Field(
        description="Notes linked to this item or its parents that were superseded or "
        "withdrawn. Shown so you can see what changed. They are not in force."
    )
    unblocks: list[Dependent] = Field(description="What is waiting for this item to be done.")
    similar: list[SearchResult] = Field(
        description="Existing stories and notes alike in meaning. Suggestions only: "
        "they were found by similarity, not by any link."
    )
    similar_note: str | None = Field(
        description="Why there are no similar items, when meaning could not be used."
    )
    characters: int = Field(description="How much of the size limit this context uses.")
    max_characters: int = Field(description="The size limit. The work itself is never cut.")
    left_out: dict[str, int] = Field(
        description="What was cut to stay within the limit, by section. Empty when nothing was."
    )
    cut_note: str | None = Field(description="In words, what was cut and how to read the rest.")


class NextWork(BaseModel):
    found: bool = Field(description="False when nothing is free. Then message says why.")
    message: str
    peeked: bool = Field(description="True when you asked to look without claiming.")
    work: Node | None = Field(description="The story or task, in full.")
    claim: Claim | None = Field(
        description="Your claim on it. Renew it with renew_claim to keep it. Empty when peeked."
    )
    taken_over_from: str | None = Field(
        description="Set when someone claimed this earlier and let the claim run out with no "
        "handoff note. They may have left it half done: check its history and its state."
    )
    taken_over_from_kind: str | None = Field(
        description="person or agent: what that earlier holder is. Empty when taken_over_from is."
    )
    context: WorkContext | None


class FinishResult(BaseModel):
    node: Node
    handoff: Handoff
    unblocked: list[Brief] = Field(
        description="Work that was waiting for this and is now ready to be taken."
    )


class CreatedNode(Node):
    similar: list[SearchResult] = Field(
        description="Existing epics or stories that look like the same thing. Suggestions: "
        "nothing was merged or linked, and the new item was created anyway."
    )
    similar_checked: bool = Field(
        description="False when no check was made: tasks are not checked, and nor is anything "
        "when the embedding service cannot be reached (see similar_note)."
    )
    similar_note: str | None


class PlanItem(BaseModel):
    key: str = Field(
        description="A name for this item that only this plan uses, so others can "
        "refer to it. Not stored. It must not look like a reference (such as story:12): those "
        "mean existing items."
    )
    kind: str = Field(description="epic, story or task.")
    title: str
    body: str = ""
    priority: str = Field(default="normal", description="low, normal, high or urgent.")
    size: str | None = Field(
        default=None,
        description="xs, s, m, l or xl: how big a story or task is, effort and context "
        "together. Not for an epic, which adds up.",
    )
    start_on: str | None = Field(default=None, description="YYYY-MM-DD.")
    target_on: str | None = Field(default=None, description="YYYY-MM-DD.")
    parent: Ref | None = Field(
        default=None,
        description="Required for a story (an epic) and a task (a story): the key of an item in "
        "this plan, or the reference (such as epic:3) of one that exists. Leave empty for an "
        "epic.",
    )


class PlanLink(BaseModel):
    source: Ref = Field(
        description="A key in this plan, or the reference (such as story:12) of an existing node."
    )
    target: Ref = Field(
        description="A key in this plan, or the reference (such as story:12) of an existing node."
    )
    link_type: str = Field(
        description="needs_first, supersedes, duplicates, relates_to or discovered_from."
    )


class PlanRule(BaseModel):
    title: str = Field(description="The acceptance criterion, stated briefly.")
    body: str = ""
    status: str = Field(default="open", description="open, met or dropped.")
    node: Ref | None = Field(
        default=None,
        description="The story or task it is on: a key in this plan, or the reference (such as "
        "story:12 or task:40) of an existing one. Not an epic. Leave empty to place it later.",
    )


class PlannedItem(BaseModel):
    key: str
    ref: str = Field(description="The reference it was given, such as story:12.")
    kind: str
    title: str


class PlanSimilar(BaseModel):
    key: str
    ref: str = Field(description="The reference of the new item.")
    similar: list[SearchResult]


class PlanResult(BaseModel):
    created: list[PlannedItem] = Field(description="Every node created, with its new reference.")
    link_ids: list[int] = Field(description="The new links, in the order you gave them.")
    rule_ids: list[int] = Field(
        description="The new acceptance criteria (rules), in the order you gave them."
    )
    counts: dict[str, int]
    plan: int | None = Field(
        description="For a draft, the plan's number (list_plans, approve_plan). Empty otherwise."
    )
    draft: bool = Field(
        description="True when the plan was proposed: its work is held until a person approves "
        "the plan, and no agent can claim it before."
    )
    similar: list[PlanSimilar] = Field(
        description="For the epics and stories that look like something that already exists: "
        "what it looks like. Suggestions. Everything was created anyway."
    )
    similar_note: str | None


class NodeChange(BaseModel):
    node: Ref = Field(description="The reference of the node to change, such as story:12.")
    expected_version: int = Field(description="The version you last read of this node.")
    title: str | None = None
    body: str | None = None
    status: str | None = Field(
        default=None, description="new, in_progress, blocked, done or dropped."
    )
    priority: str | None = Field(default=None, description="low, normal, high or urgent.")
    size: str | None = Field(
        default=None, description="xs, s, m, l or xl. Not for an epic, which adds up."
    )
    start_on: str | None = Field(default=None, description="YYYY-MM-DD.")
    target_on: str | None = Field(default=None, description="YYYY-MM-DD.")
    clear_start_on: bool = False
    clear_target_on: bool = False
    clear_size: bool = False


class ChangeResult(BaseModel):
    node: str = Field(description="The reference of the node the change was for.")
    applied: bool
    version: int | None = Field(description="The node's version after the change.")
    status: str | None = Field(description="The node's status after the change.")
    refusal: str | None = Field(description="Why it was refused, in the words update_node uses.")


class UpdateNodesResult(BaseModel):
    results: list[ChangeResult] = Field(description="One for each change, in the order given.")
    applied: int
    refused: int


# The feed's cursor says where to continue, and says nothing else: it is opaque on purpose, so that
# what it holds can change without anyone reading it.
_CURSOR_PREFIX = "vrum-feed:1:"
DEFAULT_POLL_SECONDS = 5.0


def feed_cursor(event_id: int) -> str:
    """The opaque cursor that continues the change feed after an event."""
    return base64.urlsafe_b64encode(f"{_CURSOR_PREFIX}{event_id}".encode()).decode().rstrip("=")


def feed_position(cursor: str) -> int:
    """The event a cursor continues after, or a refusal in words when it is not one of ours."""
    try:
        text = base64.urlsafe_b64decode(cursor.strip() + "=" * (-len(cursor.strip()) % 4)).decode()
        if text.startswith(_CURSOR_PREFIX) and text[len(_CURSOR_PREFIX) :].isdigit():
            return int(text[len(_CURSOR_PREFIX) :])
    except (binascii.Error, UnicodeDecodeError, ValueError):
        pass
    raise Refusal(
        "That cursor is not one this server gave. Pass the cursor of the last answer unchanged, "
        "or start again with after_event_id 0."
    )


class ChangesPage(BaseModel):
    items: list[Event]
    next_after_id: int | None = Field(
        description="Pass this as after_event_id for the next page. Empty when there are no more, "
        "and always empty when you asked for the newest changes."
    )
    next_before_id: int | None = Field(
        description="Only when you asked for the newest changes: pass this as before_event_id to "
        "read the changes before these. Empty when there are no older ones."
    )
    latest_event_id: int = Field(
        description="The newest event that is final. Once next_after_id is empty, keep this and "
        "pass it as after_event_id next time to see only what is new. Events still being written "
        "by another call are not counted until that call is done, so nothing is ever skipped."
    )
    cursor: str = Field(
        description="Opaque: pass it as cursor next time to read only what comes after this "
        "answer. It continues from the end of this page when there is more, and from "
        "latest_event_id when there is not."
    )
    truncated: bool = Field(
        description="True when this page does not hold everything: there is more to read right "
        "now, so ask again at once with cursor."
    )
    poll_after_seconds: float = Field(
        description="How long to wait before asking again for what is new when truncated is false."
    )


# -- planning: checking a plan before anything is created --------------------------------------


def _listed(lead: str, problems: list[str], verb: str) -> str:
    shown = "\n".join(f"{number}. {problem}" for number, problem in enumerate(problems, 1))
    return (
        f"{lead}, because of {len(problems)} problem{'s' if len(problems) != 1 else ''}:\n{shown}\n"
        f"Nothing was {verb}. Fix them and send it again."
    )


def _model_problems(what: str, number: int, error: ValidationError) -> list[str]:
    return [
        f"{what} {number}: {'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
        for problem in error.errors()
    ]


def _plan_models(
    items: list[dict[str, Any]], links: list[dict[str, Any]], rules: list[dict[str, Any]]
) -> tuple[list[PlanItem], list[PlanLink], list[PlanRule]]:
    """The plan models for expanded template dictionaries. A field the plan does not take is a
    problem, listed with the rest."""
    problems: list[str] = []
    made_items: list[PlanItem] = []
    made_links: list[PlanLink] = []
    made_rules: list[PlanRule] = []
    for number, raw in enumerate(items, start=1):
        try:
            made_items.append(PlanItem.model_validate(raw))
        except ValidationError as error:
            problems.extend(_model_problems("Item", number, error))
    for number, raw in enumerate(links, start=1):
        try:
            made_links.append(PlanLink.model_validate(raw))
        except ValidationError as error:
            problems.extend(_model_problems("Link", number, error))
    for number, raw in enumerate(rules, start=1):
        try:
            made_rules.append(PlanRule.model_validate(raw))
        except ValidationError as error:
            problems.extend(_model_problems("Acceptance criterion", number, error))
    if problems:
        raise TemplateProblems(problems)
    return made_items, made_links, made_rules


def _existing(value: str) -> str | None:
    """The reference of an existing item, when the text is written as one (a plan key never is)."""
    return value.strip().lower() if is_node_ref(value) else None


def _label(value: str) -> str:
    found = _existing(value)
    return f"existing {found}" if found else f"the key {value!r}"


def _a(kind: str) -> str:
    return f"an {kind}" if kind == "epic" else f"a {kind}"


def _plan_problems(
    items: list[PlanItem],
    links: list[PlanLink],
    rules: list[PlanRule],
    existing: set[str],
    edges: set[tuple[str, str]],
) -> list[str]:
    """Everything wrong with a plan, each saying which item it is about. `existing` is the
    references the plan gives that exist in the project (the others do not exist) and `edges` the
    needs_first links that already exist."""
    problems: list[str] = []
    kinds: dict[str, str] = {}
    for position, item in enumerate(items, start=1):
        where = f"Item {position} (key {item.key!r}, {item.title.strip()[:40]!r})"
        if not item.key.strip():
            problems.append(f"Item {position}: key cannot be empty.")
        elif _existing(item.key):
            problems.append(
                f"{where}: the key looks like the reference of an existing item (a kind, a colon "
                "and a number, such as story:12), which a plan uses for items that already "
                "exist. Use another name, such as 'login-story'."
            )
        elif item.key in kinds:
            problems.append(f"{where}: the key is used by more than one item.")
        else:
            kinds[item.key] = item.kind
        if item.kind not in KINDS:
            problems.append(
                f"{where}: kind must be one of {', '.join(KINDS)}, but it is {item.kind!r}."
            )
        if not item.title.strip():
            problems.append(f"{where}: title cannot be empty.")
        if item.priority not in PRIORITIES:
            problems.append(
                f"{where}: priority must be one of {', '.join(PRIORITIES)}, but it is "
                f"{item.priority!r}."
            )
        if item.size is not None:
            if item.kind == "epic":
                problems.append(
                    f"{where}: an epic has no size of its own: it adds up the size of what is in "
                    "it. Leave size out."
                )
            elif item.size not in SIZES:
                problems.append(
                    f"{where}: size must be one of {', '.join(SIZES)}, but it is {item.size!r}."
                )
        try:
            check_dates(
                parse_day("start_on", item.start_on), parse_day("target_on", item.target_on)
            )
        except Refusal as refusal:
            problems.append(f"{where}: {refusal}")
    for position, item in enumerate(items, start=1):
        where = f"Item {position} (key {item.key!r}, {item.title.strip()[:40]!r})"
        if item.kind == "epic":
            if item.parent is not None:
                problems.append(f"{where}: an epic has no parent. Leave parent out.")
            continue
        wanted = "epic" if item.kind == "story" else "story" if item.kind == "task" else None
        if wanted is None:
            continue
        if item.parent is None:
            problems.append(
                f"{where}: {_a(item.kind).capitalize()} needs a parent {wanted}. Give parent: "
                f"the key of {_a(wanted)} in this plan, or the reference of one that exists, "
                f"such as {wanted}:3."
            )
            continue
        reference = _existing(item.parent)
        if reference is None:
            found = kinds.get(item.parent)
            if found is None:
                problems.append(
                    f"{where}: parent {item.parent!r} is not the key of any item in this plan. "
                    "If you meant an existing item, give its reference, a kind and a number, "
                    "such as epic:3."
                )
            elif found != wanted:
                problems.append(
                    f"{where}: its parent {item.parent!r} is {_a(found)}, but "
                    f"{_a(item.kind)} belongs under {_a(wanted)}."
                )
        elif split_node_ref(reference)[0] != wanted:
            problems.append(
                f"{where}: {reference} is {_a(split_node_ref(reference)[0])}, but "
                f"{_a(item.kind)} belongs under {_a(wanted)}."
            )
        elif reference not in existing:
            problems.append(
                f"{where}: there is no {node_words(reference)} in this project to be its parent."
            )
    seen: set[tuple[str, str, str]] = set()
    for position, link in enumerate(links, start=1):
        where = f"Link {position} ({_label(link.source)} to {_label(link.target)})"
        if link.link_type not in LINK_TYPES:
            problems.append(
                f"{where}: link_type must be one of {', '.join(LINK_TYPES)}, but it is "
                f"{link.link_type!r}."
            )
        for end, value in (("source", link.source), ("target", link.target)):
            reference = _existing(value)
            if reference is None:
                if value not in kinds:
                    problems.append(
                        f"{where}: the {end} {value!r} is not the key of any item in this plan. "
                        "If you meant an existing node, give its reference, a kind and a number, "
                        "such as story:12."
                    )
            elif reference not in existing:
                problems.append(
                    f"{where}: there is no {node_words(reference)} in this project for the {end}."
                )
        source, target = _ends(link)
        if source == target:
            problems.append(f"{where}: a node cannot be linked to itself.")
        pair = (source, target, link.link_type)
        flipped = (target, source, link.link_type)
        if pair in seen or (link.link_type in ("duplicates", "relates_to") and flipped in seen):
            problems.append(f"{where}: the same link is given more than once.")
        seen.add(pair)
    for position, rule in enumerate(rules, start=1):
        where = f"Acceptance criterion {position} ({rule.title.strip()[:40]!r})"
        if not rule.title.strip():
            problems.append(f"Acceptance criterion {position}: title cannot be empty.")
        if rule.status not in RULE_STATUSES:
            problems.append(
                f"{where}: status must be one of {', '.join(RULE_STATUSES)}, but it is "
                f"{rule.status!r}."
            )
        if rule.node is None:
            continue
        reference = _existing(rule.node)
        holds = "an acceptance criterion belongs to a story or a task"
        if reference is None:
            found = kinds.get(rule.node)
            if found is None:
                problems.append(
                    f"{where}: node {rule.node!r} is not the key of any item in this plan. "
                    "If you meant an existing story or task, give its reference, such as "
                    "story:12 or task:40."
                )
            elif found == "epic":
                problems.append(f"{where}: {rule.node!r} is an epic, and {holds}.")
        elif split_node_ref(reference)[0] == "epic":
            problems.append(f"{where}: {reference} is an epic, and {holds}.")
        elif reference not in existing:
            problems.append(
                f"{where}: there is no {node_words(reference)} in this project to hold it."
            )
    problems.extend(_circles(links, edges))
    return problems


def _ends(link: PlanLink) -> tuple[str, str]:
    """The two ends of a plan link, each as its key or, for an existing item, its reference."""
    return _end(link.source), _end(link.target)


def _end(value: str) -> str:
    return _existing(value) or value


def _circles(links: list[PlanLink], edges: set[tuple[str, str]]) -> list[str]:
    """The circles of waiting the plan's needs_first links would make, with each other and with
    the links that already exist. A node here is a plan key or the reference of an existing item,
    and the two never look alike."""
    graph: dict[str, set[str]] = {}
    for old_source, old_target in edges:
        graph.setdefault(old_source, set()).add(old_target)
    own = [_ends(link) for link in links if link.link_type == "needs_first"]
    for source, target in own:
        graph.setdefault(source, set()).add(target)
    reported: set[frozenset[str]] = set()
    problems: list[str] = []
    for source, target in own:
        names = circle_through(graph, source, target)
        if names is None:
            continue
        nodes = frozenset(names)
        if nodes in reported:
            continue
        reported.add(nodes)
        problems.append(
            "These needs_first links make a circle of waiting, so none of them could ever start: "
            f"{_label(names[0])} needs "
            + ", which needs ".join(_label(node) for node in names[1:])
            + ". Remove one of them."
        )
    return problems


class Workflow:
    def __init__(
        self,
        database: Database,
        graph: Graph,
        searcher: Searcher,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self._database = database
        self._graph = graph
        self._searcher = searcher
        self._poll_seconds = poll_seconds

    # -- creating -----------------------------------------------------------------------------

    async def create_node(
        self,
        *,
        actor: str,
        kind: str,
        title: str,
        parent: str | None,
        body: str,
        priority: str,
        size: str | None,
        start_on: date | None,
        target_on: date | None,
    ) -> CreatedNode:
        node = await self._graph.create_node(
            actor=actor,
            kind=kind,
            title=title,
            parent=parent,
            body=body,
            priority=priority,
            size=size,
            start_on=start_on,
            target_on=target_on,
        )
        similar: list[SearchResult] = []
        note: str | None = None
        checked = False
        if kind in ("epic", "story"):
            found, index = await self._searcher.possible_duplicates(
                [f"{title.strip()}\n{body}".strip()],
                types=[kind],
                exclude={(kind, node.number)},
            )
            similar = found[0]
            checked = index.meaning == "used"
            note = None if checked else index.note
        return CreatedNode(
            **node.model_dump(), similar=similar, similar_checked=checked, similar_note=note
        )

    async def create_plan(
        self,
        *,
        actor: str,
        items: list[PlanItem],
        links: list[PlanLink],
        rules: list[PlanRule],
        draft: bool = False,
        title: str | None = None,
        template: str | None = None,
        values: dict[str, Any] | None = None,
    ) -> PlanResult:
        if template is not None and (items or links or rules):
            raise Refusal(
                "Give either a template (with values) or the plan's own items, links and "
                "acceptance criteria, not both. Nothing was created."
            )
        if template is None and values:
            raise Refusal(
                "values go with a template: name one, or leave values out. Nothing was created."
            )
        plan_title = None if title is None or not title.strip() else title.strip()
        if plan_title is not None and len(plan_title) > MOST_PLAN_TITLE_CHARS:
            raise Refusal(
                f"A plan's title may be at most {MOST_PLAN_TITLE_CHARS} characters. Nothing was "
                "created."
            )
        if plan_title is not None and not draft:
            raise Refusal(
                "A title is for a draft plan, which a person approves. Nothing was created."
            )
        async with self._database.transaction() as conn:
            if template is not None:
                items, links, rules = await self._template_plan(conn, template, values or {})
            if not items and not links and not rules:
                raise Refusal(
                    "The plan is empty: give at least one item, link or acceptance criterion."
                )
            referenced = sorted(
                {
                    reference
                    for value in (
                        *(item.parent for item in items),
                        *(end for link in links for end in (link.source, link.target)),
                        *(rule.node for rule in rules),
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
            edges: set[tuple[str, str]] = set()
            if any(
                link.link_type == "needs_first"
                and (_existing(link.source) or _existing(link.target))
                for link in links
            ):
                edges = {
                    (row["source"], row["target"])
                    for row in await fetch_all(
                        conn,
                        "SELECT source, target FROM links WHERE link_type = 'needs_first'",
                    )
                }
            problems = _plan_problems(items, links, rules, existing, edges)
            if problems:
                shown = "\n".join(
                    f"{number}. {problem}" for number, problem in enumerate(problems, 1)
                )
                raise Refusal(
                    f"The plan was not created, because of {len(problems)} "
                    f"problem{'s' if len(problems) != 1 else ''}:\n{shown}\nNothing was created. "
                    "Fix them and send the whole plan again."
                )
            try:
                created, link_ids, rule_ids = await self._build(conn, actor, items, links, rules)
            except psycopg.errors.RaiseException as error:
                raise Refusal(
                    f"The plan was not created: {error.diag.message_primary} Nothing was created."
                ) from None
            except psycopg.errors.UniqueViolation:
                raise Refusal(
                    "The plan was not created: one of its links already exists between those "
                    "nodes. Nothing was created."
                ) from None
            plan_id: int | None = None
            if draft:
                first_epic = next((c.title for c in created if c.kind == "epic"), None)
                named = plan_title or first_epic or template or f"Plan of {len(created)} items"
                plan_id = await record_event(
                    conn,
                    action=PLAN_PROPOSED,
                    actor=actor,
                    before=None,
                    after={
                        "title": named[:MOST_PLAN_TITLE_CHARS],
                        "items": [c.ref for c in created],
                        "snapshot": await plan_snapshot(conn, [c.ref for c in created]),
                        **({"template": template} if template else {}),
                    },
                )
        bodies = {item.key: item.body for item in items}
        indexed = [item for item in created if item.kind in ("epic", "story")]
        found, note = await self._searcher.possible_duplicates(
            [f"{item.title}\n{bodies[item.key]}".strip() for item in indexed],
            types=["epic", "story"],
            exclude={(item.kind, split_node_ref(item.ref)[1]) for item in created},
        )
        similar = [
            PlanSimilar(key=item.key, ref=item.ref, similar=matches)
            for item, matches in zip(indexed, found, strict=True)
            if matches
        ]
        counts = {kind: sum(1 for item in created if item.kind == kind) for kind in KINDS}
        counts.update({"links": len(link_ids), "rules": len(rule_ids)})
        return PlanResult(
            created=created,
            link_ids=link_ids,
            rule_ids=rule_ids,
            counts=counts,
            plan=plan_id,
            draft=draft,
            similar=similar,
            similar_note=None if note.meaning == "used" else note.note,
        )

    # -- plan templates -------------------------------------------------------------------------

    async def _template_plan(
        self, conn: AsyncConnection, name: str, values: dict[str, Any]
    ) -> tuple[list[PlanItem], list[PlanLink], list[PlanRule]]:
        """The plan a saved template makes for these values, ready for the plan checks (which are
        the very ones a plan written by hand meets)."""
        row = await fetch_one(
            conn,
            "SELECT template FROM plan_template_state "
            "WHERE name = %s AND last_action = 'plan_template_saved'",
            (name,),
        )
        if row is None:
            raise Refusal(
                f"There is no plan template called {name!r}. list_plan_templates shows the ones "
                "this project has. Nothing was created."
            )
        try:
            made = expand(parse_template(row["template"]), values)
            return _plan_models(*made)
        except TemplateProblems as refused:
            raise Refusal(
                _listed("The plan was not created", refused.problems, "created")
            ) from None

    async def save_template(
        self, *, actor: str, name: str, template: dict[str, Any]
    ) -> PlanTemplateInfo:
        """Save a plan template (a newer save of the same name replaces it, and every version stays
        in the history). It is checked whole: its shape, its variables and the plan it makes, and
        every problem is listed at once."""
        name = clean_name(name)
        if len(json.dumps(template, default=str)) > MOST_TEMPLATE_BYTES:
            raise Refusal(
                f"A template may be at most {MOST_TEMPLATE_BYTES:,} characters. Nothing was saved."
            )
        refuse_secrets(template)
        try:
            parsed = parse_template(template)
            problems: list[str] = []
            if not parsed.items:
                problems.append("A template needs at least one item.")
            else:
                items, links, rules = _plan_models(*expand(parsed, sample_values(parsed)))
                problems = _plan_problems(items, links, rules, sample_refs(parsed), set())
            if problems:
                raise TemplateProblems(problems)
        except TemplateProblems as refused:
            raise Refusal(
                _listed("The template was not saved", refused.problems, "saved")
            ) from None
        stored = parsed.model_dump(mode="json", exclude_defaults=True)
        async with self._database.transaction() as conn:
            await record_event(
                conn,
                action="plan_template_saved",
                actor=actor,
                before=None,
                after={"name": name, "template": stored},
            )
            info = await self._template_info(conn, name, detail=True)
        assert info is not None
        return info

    async def delete_template(self, *, actor: str, name: str) -> PlanTemplateInfo:
        name = clean_name(name)
        async with self._database.transaction() as conn:
            info = await self._template_info(conn, name, detail=False)
            if info is None:
                raise Refusal(
                    f"There is no plan template called {name!r}. list_plan_templates shows the "
                    "ones this project has. Nothing was changed."
                )
            await record_event(
                conn,
                action="plan_template_deleted",
                actor=actor,
                before=None,
                after={"name": name},
            )
        return info

    async def list_templates(self, *, name: str | None, detail: bool) -> PlanTemplatePage:
        """The project's plan templates by name; with a name (or `detail`) each carries its whole
        template."""
        async with self._database.transaction() as conn:
            if name is not None:
                found = await self._template_info(conn, clean_name(name), detail=True)
                if found is None:
                    raise Refusal(
                        f"There is no plan template called {name!r}. list_plan_templates without "
                        "a name shows the ones this project has."
                    )
                return PlanTemplatePage(items=[found], total=1)
            names = [
                row["name"]
                for row in await fetch_all(
                    conn,
                    "SELECT name FROM plan_template_state "
                    "WHERE last_action = 'plan_template_saved' ORDER BY name",
                )
            ]
            infos = [await self._template_info(conn, n, detail=detail) for n in names]
        return PlanTemplatePage(items=[i for i in infos if i is not None], total=len(names))

    async def _template_info(
        self, conn: AsyncConnection, name: str, *, detail: bool
    ) -> PlanTemplateInfo | None:
        row = await fetch_one(
            conn,
            "SELECT name, template, saved_by, saved_by_kind, saved_at, "
            "(SELECT count(*) FROM events v WHERE v.action = 'plan_template_saved' "
            "AND v.after->>'name' = s.name) AS version "
            "FROM plan_template_state s WHERE name = %s AND last_action = 'plan_template_saved'",
            (name,),
        )
        if row is None:
            return None
        parsed = parse_template(row["template"])
        kinds = [item.kind for item in parsed.items]
        return PlanTemplateInfo(
            name=row["name"],
            description=parsed.description,
            variables=parsed.variables,
            counts={
                "epics": kinds.count("epic"),
                "stories": kinds.count("story"),
                "tasks": kinds.count("task"),
                "links": len(parsed.links),
                "acceptance criteria": len(parsed.rules),
            },
            version=int(row["version"]),
            saved_by=row["saved_by"],
            saved_by_kind=row["saved_by_kind"],
            saved_at=row["saved_at"],
            template=parsed.model_dump(mode="json", exclude_defaults=True) if detail else None,
        )

    async def _build(
        self,
        conn: AsyncConnection,
        actor: str,
        items: list[PlanItem],
        links: list[PlanLink],
        rules: list[PlanRule],
    ) -> tuple[list[PlannedItem], list[int], list[int]]:
        refs: dict[str, str] = {}
        created: list[PlannedItem] = []

        def resolve(value: str) -> str:
            return _existing(value) or refs[value]

        # Parents before what is under them, whatever order the plan was written in. Each kind is
        # numbered in the order the plan lists it.
        for kind in KINDS:
            for item in (item for item in items if item.kind == kind):
                row = await insert_node(
                    conn,
                    actor=actor,
                    kind=kind,
                    title=clean_title(item.title),
                    parent=None if item.parent is None else resolve(item.parent),
                    body=item.body,
                    priority=item.priority,
                    size=item.size,
                    start_on=parse_day("start_on", item.start_on),
                    target_on=parse_day("target_on", item.target_on),
                )
                refs[item.key] = row["ref"]
                created.append(
                    PlannedItem(key=item.key, ref=row["ref"], kind=kind, title=row["title"])
                )
        created.sort(key=lambda planned: [item.key for item in items].index(planned.key))

        link_ids = []
        for link in links:
            row = await insert_link(
                conn,
                actor=actor,
                source=resolve(link.source),
                target=resolve(link.target),
                link_type=link.link_type,
            )
            link_ids.append(int(row["id"]))
        rule_ids = []
        for rule in rules:
            rule_row = await insert_rule(
                conn,
                actor=actor,
                title=rule.title.strip(),
                body=rule.body,
                status=rule.status,
                node=None if rule.node is None else resolve(rule.node),
            )
            rule_ids.append(int(rule_row["id"]))
        return created, link_ids, rule_ids

    # -- changing many at once ----------------------------------------------------------------

    async def update_nodes(self, *, actor: str, changes: list[NodeChange]) -> UpdateNodesResult:
        if not changes:
            raise Refusal("There is nothing to change: give at least one change.")
        nodes = [parse_node_ref(change.node, "node") for change in changes]
        results: list[ChangeResult] = []
        for node, change in zip(nodes, changes, strict=True):
            try:
                updated = await self._graph.update_node(
                    actor=actor,
                    node=node,
                    expected_version=change.expected_version,
                    title=change.title,
                    body=change.body,
                    status=change.status,
                    priority=change.priority,
                    size=change.size,
                    parent=None,
                    start_on=parse_day("start_on", change.start_on),
                    target_on=parse_day("target_on", change.target_on),
                    clear_start_on=change.clear_start_on,
                    clear_target_on=change.clear_target_on,
                    clear_size=change.clear_size,
                )
            except Refusal as refusal:
                results.append(
                    ChangeResult(
                        node=node, applied=False, version=None, status=None, refusal=str(refusal)
                    )
                )
            else:
                results.append(
                    ChangeResult(
                        node=node,
                        applied=True,
                        version=updated.version,
                        status=updated.status,
                        refusal=None,
                    )
                )
        applied = sum(1 for result in results if result.applied)
        return UpdateNodesResult(results=results, applied=applied, refused=len(results) - applied)

    # -- what changed -------------------------------------------------------------------------

    async def changes_since(
        self,
        *,
        after_event_id: int,
        actor: str | None,
        node: str | None,
        rule_id: int | None,
        note_id: int | None,
        limit: int,
        newest: bool = False,
        before_event_id: int | None = None,
        cursor: str | None = None,
    ) -> ChangesPage:
        limit = page_size(limit)
        if cursor is not None and cursor.strip():
            if after_event_id:
                raise Refusal("Give either cursor or after_event_id, not both.")
            after_event_id = feed_position(cursor)
        if after_event_id < 0:
            raise Refusal(f"after_event_id must be 0 or more, but it is {after_event_id}.")
        if before_event_id is not None and before_event_id < 1:
            raise Refusal(f"before_event_id must be 1 or more, but it is {before_event_id}.")
        backwards = newest or before_event_id is not None
        given = [
            name
            for name, value in (
                ("node", node),
                ("rule_id", rule_id),
                ("note_id", note_id),
            )
            if value is not None
        ]
        if len(given) > 1:
            raise Refusal(
                "Give at most one of node, rule_id or note_id: the one item to follow. Leave "
                "them all out to see every change."
            )
        async with self._database.transaction(snapshot=True) as conn:
            # One snapshot for the page and for latest_event_id: an event that finishes in
            # between must not be counted in the latest id without being in the page.
            if node is not None:
                await require_node(conn, node)
            if rule_id is not None:
                await require_rule(conn, rule_id)
            if note_id is not None:
                await require_note(conn, note_id)
            position = {"xid": "0", "id": 0}
            if after_event_id:
                found = await fetch_one(
                    conn, "SELECT xid::text AS xid, id FROM events WHERE id = %s", (after_event_id,)
                )
                if found is None:
                    raise Refusal(
                        f"There is no event with id {after_event_id}. Pass 0 to start from the "
                        "beginning, or the latest_event_id or next_after_id an earlier answer "
                        "gave you."
                    )
                position = {"xid": found["xid"], "id": found["id"]}
            ceiling = None
            if before_event_id is not None:
                top = await fetch_one(
                    conn,
                    "SELECT xid::text AS xid, id FROM events WHERE id = %s",
                    (before_event_id,),
                )
                if top is None:
                    raise Refusal(
                        f"There is no event with id {before_event_id}. Pass the next_before_id of "
                        "an earlier answer, or the id of an event you have seen."
                    )
                ceiling = {"bxid": top["xid"], "bid": top["id"]}
            # Only events of transactions that have all finished are shown, in the order their
            # transactions began, so that an event written late is never behind the cursor.
            final = "e.xid < pg_snapshot_xmin(pg_current_snapshot())"
            order = "DESC" if backwards else "ASC"
            rows = await fetch_all(
                conn,
                "SELECT "
                + ", ".join(f"e.{column.strip()}" for column in EVENT_COLUMNS.split(","))
                + f" FROM events e WHERE {final} "
                "AND (e.xid, e.id) > (%(xid)s::text::xid8, %(id)s) "
                "AND (%(bid)s::bigint IS NULL OR (e.xid, e.id) < (%(bxid)s::text::xid8, %(bid)s)) "
                "AND (%(actor)s::text IS NULL OR e.actor = %(actor)s) "
                "AND (%(node)s::text IS NULL OR e.node = %(node)s "
                "OR e.other_node = %(node)s) "
                "AND (%(rule)s::bigint IS NULL OR e.rule_id = %(rule)s) "
                "AND (%(note)s::bigint IS NULL OR e.note_id = %(note)s "
                "OR e.other_note_id = %(note)s) "
                f"ORDER BY e.xid {order}, e.id {order} LIMIT %(fetch)s",
                {
                    **position,
                    **(ceiling or {"bxid": "0", "bid": None}),
                    "actor": actor.strip() if actor and actor.strip() else None,
                    "node": node,
                    "rule": rule_id,
                    "note": note_id,
                    "fetch": limit + 1,
                },
            )
            latest = await fetch_one(
                conn,
                f"SELECT id FROM events e WHERE {final} ORDER BY e.xid DESC, e.id DESC LIMIT 1",
            )
        if backwards:
            shown = rows[:limit]
            older = shown[-1]["id"] if len(rows) > limit else None
            return ChangesPage(
                items=[Event.model_validate(row) for row in reversed(shown)],
                next_after_id=None,
                next_before_id=older,
                latest_event_id=int(latest["id"]) if latest else 0,
                cursor=feed_cursor(int(latest["id"]) if latest else 0),
                truncated=older is not None,
                poll_after_seconds=self._poll_seconds,
            )
        page, next_after_id = split_page(rows, limit)
        newest_id = int(latest["id"]) if latest else 0
        return ChangesPage(
            items=[Event.model_validate(row) for row in page],
            next_after_id=next_after_id,
            next_before_id=None,
            latest_event_id=newest_id,
            cursor=feed_cursor(next_after_id if next_after_id is not None else newest_id),
            truncated=next_after_id is not None,
            poll_after_seconds=self._poll_seconds,
        )

    # -- ending work --------------------------------------------------------------------------

    async def finish_work(
        self,
        *,
        actor: str,
        node: str,
        outcome: str,
        note: str,
        where: dict[str, str] | None = None,
        ask: dict[str, Any] | None = None,
    ) -> FinishResult:
        check_choice("outcome", outcome, HANDOFF_OUTCOMES)
        note = clean_note(note)
        if ask is not None and outcome not in ("blocked", "released"):
            raise Refusal(
                "An ask is for work that is not finished: give it with outcome blocked (the work "
                "waits for the answer) or released (it stays free to take), or leave ask out. "
                "Nothing was changed."
            )
        async with self._database.transaction() as conn:
            current = await require_node(conn, node, lock=True)
            await release_in(
                conn,
                actor=actor,
                node=node,
                outcome=outcome,
                note=note,
                verb="finish",
                where=where,
            )
            if outcome == "done":
                await refuse_while_unticked(
                    conn,
                    node,
                    instead="finish it as released or blocked, and say why in the note",
                    block="finish it as blocked, saying in the handoff note",
                )
            # Released work goes back to not started, if it was in progress. The other outcomes
            # are the status itself.
            status = (
                outcome
                if outcome != "released"
                else ("new" if current["status"] == "in_progress" else current["status"])
            )
            if status != current["status"]:
                await change_status(conn, actor, current, status)
            if ask is not None:
                await raise_ask(
                    conn,
                    actor=actor,
                    node=node,
                    ask=ask,
                    effect="blocks" if outcome == "blocked" else "parks",
                )
            finished = await node_state(conn, node)
            handoff = (await handoffs_of(conn, node, newest_first=True))[0]
            unblocked = [
                Brief.model_validate(row)
                for row in await fetch_all(
                    conn,
                    "SELECT s.ref, s.kind, s.title, s.status, s.size FROM links l "
                    "JOIN node_state s ON s.ref = l.source "
                    "WHERE l.target = %s AND l.link_type = 'needs_first' "
                    "AND s.status = 'ready' ORDER BY s.kind_rank, s.number",
                    (node,),
                )
            ]
        return FinishResult(node=finished, handoff=handoff, unblocked=unblocked)

    # -- starting work ------------------------------------------------------------------------

    async def next_work(
        self,
        *,
        actor: str,
        within_node: str | None,
        peek: bool,
        lease_seconds: int,
        context_chars: int,
        where: dict[str, str] | None = None,
    ) -> NextWork:
        lease_seconds = lease_seconds_ok(lease_seconds)
        if not 1000 <= context_chars <= 1_000_000:
            raise Refusal(
                f"context_chars must be a whole number from 1000 to 1000000, but it is "
                f"{context_chars}."
            )
        taken_over_from: str | None = None
        taken_over_from_kind: str | None = None
        async with self._database.transaction() as conn:
            if within_node is not None:
                await require_node(conn, within_node)
            excluded: list[str] = []
            claim: Claim | None = None
            while True:
                picked = await fetch_one(
                    conn,
                    "SELECT n.ref FROM nodes n JOIN node_state s ON s.ref = n.ref "
                    f"WHERE {_FREE_WORK} AND n.ref <> ALL(%(excluded)s) ORDER BY {_URGENCY} "
                    f"LIMIT 1{'' if peek else ' FOR UPDATE OF n SKIP LOCKED'}",
                    {"within": within_node, "excluded": excluded},
                )
                if picked is None:
                    return await self._nothing_free(conn, within_node, peek)
                ref = str(picked["ref"])
                if peek:
                    break
                # The row is ours now. Look again, because the node may have been taken in the
                # moment between the search and the lock.
                row = await fetch_one(
                    conn,
                    f"SELECT s.ref FROM node_state s WHERE s.ref = %(ref)s AND {_FREE_WORK}",
                    {"ref": ref, "within": within_node},
                )
                if row is None:
                    excluded.append(ref)
                    continue
                current = await current_claim(conn, ref)
                lapsed = current if current is not None and not current["live"] else None
                stored = await require_node(conn, ref)
                if stored["status"] == "new":
                    await change_status(conn, actor, stored, "in_progress")
                claim_row = await take_claim(
                    conn,
                    actor=actor,
                    node=ref,
                    lease_seconds=lease_seconds,
                    current=current,
                    where=where,
                )
                claim = Claim.model_validate(claim_row)
                if lapsed is not None:
                    taken_over_from = lapsed["actor"]
                    taken_over_from_kind = lapsed["actor_kind"]
                break
            work = await node_state(conn, ref)
            parts = await self._exact_context(conn, work)
        similar, similar_note = await self._similar_items(work, parts)
        context = self._fit(parts, similar, similar_note, context_chars)
        message = f"Here is {work.ref}, {work.title!r}, with what you need to start. " + (
            "You asked to look without claiming, so nobody holds it."
            if peek
            else f"It is yours until {claim.expires_at:%Y-%m-%d %H:%M:%S} UTC; renew it with "
            "renew_claim to keep it longer, and end it with finish_work."
            if claim
            else ""
        )
        if taken_over_from:
            message += (
                f" {taken_over_from} claimed it earlier and let the claim run out with no handoff "
                "note: it may be half done."
            )
        return NextWork(
            found=True,
            message=message,
            peeked=peek,
            work=work,
            claim=claim,
            taken_over_from=taken_over_from,
            taken_over_from_kind=taken_over_from_kind,
            context=context,
        )

    async def _nothing_free(
        self, conn: AsyncConnection, within_node: str | None, peek: bool
    ) -> NextWork:
        counts = await fetch_one(
            conn,
            "SELECT count(*) FILTER (WHERE s.claimed_by IS NOT NULL) AS held, "
            "count(*) FILTER (WHERE s.status = 'new' AND cardinality(s.waiting_on) > 0) "
            "AS waiting, "
            "count(*) FILTER (WHERE cardinality(s.held_by) > 0) AS held_back, "
            "count(*) FILTER (WHERE s.status = 'blocked') AS blocked, "
            # Free as far as this answer can see, yet not handed out: another agent has it in
            # the same moment.
            f"count(*) FILTER (WHERE {_FREE_WORK}) AS being_taken "
            "FROM node_state s WHERE s.kind IN ('story', 'task') "
            "AND s.status NOT IN ('done', 'dropped') "
            "AND (%(within)s::text IS NULL OR s.ref = %(within)s OR s.parent = %(within)s "
            "OR s.parent IN (SELECT ref FROM nodes WHERE parent = %(within)s))",
            {"within": within_node},
        )
        assert counts is not None
        reasons = [
            text
            for number, text in (
                (counts["held"], f"{counts['held']} being worked on by someone"),
                (
                    counts["being_taken"],
                    f"{counts['being_taken']} being picked up by someone else this very moment",
                ),
                (counts["waiting"], f"{counts['waiting']} waiting for other work to be done"),
                (
                    counts["held_back"],
                    f"{counts['held_back']} held by a gate or a plan waiting for approval",
                ),
                (counts["blocked"], f"{counts['blocked']} marked blocked"),
            )
            if number
        ]
        where = f" under {within_node}" if within_node is not None else ""
        message = (
            f"Nothing is free right now{where}."
            + (f" The open work is {', '.join(reasons)}." if reasons else " There is no open work.")
            + " Try again later, or create the next piece of work."
        )
        return NextWork(
            found=False,
            message=message,
            peeked=peek,
            work=None,
            claim=None,
            taken_over_from=None,
            taken_over_from_kind=None,
            context=None,
        )

    # -- the context of a piece of work -------------------------------------------------------

    async def _exact_context(self, conn: AsyncConnection, work: Node) -> dict[str, Any]:
        """Everything in a work item's context that comes by exact link."""
        refs = await chain_of(conn, work.ref)
        chain = [await node_state(conn, ref) for ref in reversed(refs[1:])]
        chain_refs = refs
        parents = [
            ContextParent(
                ref=node.ref,
                kind=node.kind,
                title=node.title,
                status=node.status,
                size=node.size,
                body_preview=(node.body or "")[:_PARENT_BODY_CHARS],
                body_chars=node.body_chars,
            )
            for node in chain
        ]
        criteria = [
            ContextCriterion(
                id=row["id"],
                on=row["node"],
                title=row["title"],
                status=row["status"],
                body=row["body"][:_RULE_BODY_CHARS],
                body_chars=len(row["body"]),
            )
            for row in await fetch_all(
                conn,
                "SELECT id, node, title, body, status FROM rules WHERE "
                f"{CRITERIA_IN_FORCE} ORDER BY {CRITERIA_ORDER}",
                {"holders": criteria_holders(chain_refs)},
            )
        ]
        note_rows = await fetch_all(
            conn,
            "SELECT n.id, n.text, n.why, n.decided, n.decided_by, n.decided_on, n.project_wide, "
            "n.status, n.superseded_by_id, n.status_reason, n.confirmed_by, n.confirmed_at, "
            "n.recorded_by, n.recorded_by_kind, "
            "ARRAY(SELECT nn.node FROM note_nodes nn "
            "WHERE nn.note_id = n.id AND nn.node = ANY(%(chain)s)) AS linked_to "
            "FROM note_state n WHERE " + note_in_chain("n.id") + " "
            "OR (n.project_wide AND n.status IN ('unconfirmed', 'confirmed')) "
            "ORDER BY (NOT n.decided), n.id",
            {"chain": chain_refs},
        )
        notes = [
            ContextNote.model_validate(
                {**row, "linked_to": sorted(row["linked_to"], key=node_order)}
            )
            for row in note_rows
        ]
        prerequisites = [
            Prerequisite(
                node=Brief.model_validate(row), handoffs=await handoffs_of(conn, row["ref"])
            )
            for row in await fetch_all(
                conn,
                "SELECT t.ref, t.kind, t.title, t.status, t.size FROM links l "
                "JOIN node_state t ON t.ref = l.target "
                "WHERE l.source = %s AND l.link_type = 'needs_first' "
                "ORDER BY t.kind_rank, t.number",
                (work.ref,),
            )
        ]
        found_in = [
            Brief.model_validate(row)
            for row in await fetch_all(
                conn,
                "SELECT t.ref, t.kind, t.title, t.status, t.size FROM links l "
                "JOIN node_state t ON t.ref = l.target "
                "WHERE l.source = %s AND l.link_type = 'discovered_from' "
                "ORDER BY t.kind_rank, t.number",
                (work.ref,),
            )
        ]
        dependents = [
            Dependent(
                node=Brief.model_validate(row),
                becomes_ready_when_done=row["becomes_ready_when_done"],
            )
            for row in await fetch_all(
                conn,
                "SELECT s.ref, s.kind, s.title, s.status, s.size, "
                "NOT EXISTS (SELECT 1 FROM links l2 "
                "JOIN nodes t2 ON t2.ref = l2.target WHERE l2.source = s.ref "
                "AND l2.link_type = 'needs_first' AND l2.target <> %(work)s "
                "AND t2.status <> 'done') AS becomes_ready_when_done "
                "FROM links l JOIN node_state s ON s.ref = l.source "
                "WHERE l.target = %(work)s AND l.link_type = 'needs_first' "
                "AND s.status NOT IN ('done', 'dropped') ORDER BY s.kind_rank, s.number",
                {"work": work.ref},
            )
        ]
        shown = {
            *((node.kind, node.number) for node in chain),
            (work.kind, work.number),
            *(("note", n.id) for n in notes),
        }
        return {
            "parents": parents,
            "acceptance_criteria": criteria,
            "definition_of_done": (await read_definition(conn)).body,
            "definition_checks": [
                DefinitionCheck(
                    item=check.item,
                    text=check.text,
                    person_only=check.person_only,
                    verifier_only=check.verifier_only,
                    github=check.github,
                    checked=check.checked,
                )
                for check in await checks_of(conn, work.ref)
            ],
            "asks": await asks_of(conn, work.ref),
            "handoffs": await handoffs_of(conn, work.ref),
            "prerequisites": prerequisites,
            "discovered_from": found_in,
            "notes": [n for n in notes if n.status in IN_FORCE and not n.project_wide],
            "project_wide_notes": [n for n in notes if n.status in IN_FORCE and n.project_wide],
            "past_notes": [n for n in notes if n.status not in IN_FORCE],
            "unblocks": dependents,
            "shown": shown,
        }

    async def _similar_items(
        self, work: Node, parts: dict[str, Any]
    ) -> tuple[list[SearchResult], str | None]:
        """Existing items alike in meaning to this work, apart from what is already in its
        context. Suggestions, and never worth waiting for: it says so when it cannot be had."""
        found, _, note = await self._searcher.similar(
            node=work.ref,
            wanted_types=["story", "note"],
            exclude=parts["shown"],
            limit=_SIMILAR_WANTED,
            timeout=5.0,
        )
        return found, None if note.meaning == "used" else note.note

    def _fit(
        self,
        parts: dict[str, Any],
        similar: list[SearchResult],
        similar_note: str | None,
        max_chars: int,
    ) -> WorkContext:
        """Put the context together within the size limit. The definition of done comes first, cut
        to at most a quarter of the room, with its checks, which are never cut, and then the
        sections are filled in order; what does not fit is left out, and counted."""
        sections: dict[str, list[Any]] = {
            name: list(parts.get(name, [])) for name in _SECTION_ORDER if name != "similar"
        }
        sections["similar"] = similar
        definition: str = parts["definition_of_done"]
        checks: list[DefinitionCheck] = parts["definition_checks"]
        shown_definition = definition[: max_chars // 4]
        used = len(shown_definition) + sum(len(check.model_dump_json()) for check in checks)
        full = False
        kept: dict[str, list[Any]] = {}
        left_out: dict[str, int] = {}
        for name in _SECTION_ORDER:
            keep: list[Any] = []
            for item in sections[name]:
                size = len(item.model_dump_json())
                # Once something does not fit, nothing after it is kept: what is cut is the end.
                if full or used + size > max_chars:
                    full = True
                    break
                keep.append(item)
                used += size
            kept[name] = keep
            if len(keep) < len(sections[name]):
                left_out[name] = len(sections[name]) - len(keep)
        cut_words = []
        if left_out:
            cut_words.append(
                f"To stay within {max_chars:,} characters, this left out "
                + ", ".join(f"{count} {_SECTION_NAMES[name]}" for name, count in left_out.items())
                + ". Use recall for the item to read them all, or ask again with a larger "
                "context_chars."
            )
        if len(shown_definition) < len(definition):
            cut_words.append(
                f"The definition of done is {len(definition):,} characters and only the first "
                f"{len(shown_definition):,} are shown: ask again with a larger context_chars to "
                "read all of it."
            )
        return WorkContext(
            **kept,
            definition_of_done=shown_definition,
            definition_of_done_chars=len(definition),
            definition_checks=checks,
            similar_note=similar_note,
            characters=used,
            max_characters=max_chars,
            left_out=left_out,
            cut_note=" ".join(cut_words) or None,
        )
