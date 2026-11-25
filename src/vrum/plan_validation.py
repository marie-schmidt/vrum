"""Plan data models and pure validation (hierarchy, dates, sizes, links, and wait circles)."""

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from vrum.common import (
    Ref,
    Refusal,
    is_node_ref,
    node_words,
    parse_day,
    split_node_ref,
)
from vrum.graph import KINDS, PRIORITIES, SIZES, check_dates
from vrum.links import LINK_TYPES
from vrum.plan_checks import circle_through
from vrum.plan_templates import TemplateProblems
from vrum.rules import RULE_STATUSES
from vrum.search import SearchResult


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
