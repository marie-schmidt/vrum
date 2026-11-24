"""A drafted plan is reviewed as revisions (M51). The models and the pure functions of that:

A plan is proposed as its first revision. A person reads it and either decides (approve or reject:
final) or asks for changes with a summary and comments on its lines; the plan then stays held, and
the agent that drafted it sends a revision, which is read again. Every revision is kept whole as a
*snapshot* in the event that made it (the plan's own proposal for the first), so what a person was
shown, and what they said about it, can always be read exactly as it was, whatever the items have
become since. A comment names the revision, the item and, when it is about one part, the field, and
keeps the words it was about (`quoted`): a later edit can never attach it to other words.

Nothing here reads the database: the models, how two snapshots differ, how live items have drifted
from the snapshot a person reviewed, and how the comments of a request are checked and quoted."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from vrum.common import Ref, Refusal, split_node_ref

MOST_SUMMARY_CHARS = 2000
MOST_COMMENT_CHARS = 2000
MOST_COMMENTS = 100
MOST_NOTE_CHARS = 1000

# The parts of an item a comment can be about, and the parts two revisions are compared by.
FIELDS = (
    "title",
    "body",
    "priority",
    "size",
    "start_on",
    "target_on",
    "parent",
    "links",
    "criteria",
)
# The links that make work wait: a link of another kind never changes what a person approves.
_BLOCKING = "needs_first"


class PlanCriterion(BaseModel):
    id: int
    title: str
    body: str
    status: str = Field(description="open or met.")


class PlanItemLink(BaseModel):
    link_type: str
    target: str = Field(description="The reference of the item it points to.")


class PlanItemRead(BaseModel):
    ref: str
    kind: str
    parent: str | None = Field(description="Its parent: an item of this plan or one that exists.")
    title: str
    body: str
    priority: str
    size: str | None
    start_on: date | None
    target_on: date | None
    links: list[PlanItemLink] = Field(description="The links that start at it.")
    criteria: list[PlanCriterion] = Field(
        description="Its acceptance criteria that are not dropped."
    )


class PlanContext(BaseModel):
    ref: str
    kind: str
    title: str
    why: str = Field(description="parent (an item of the plan hangs under it) or linked.")


class PlanCommentInput(BaseModel):
    item: Ref = Field(
        description="The item the comment is about, by reference: an item of the plan."
    )
    field: str | None = Field(
        default=None,
        description=f"The part it is about, one of {', '.join(FIELDS)}. Leave out when it is about "
        "the item as a whole.",
    )
    text: str = Field(description="What the person says.")


class PlanComment(BaseModel):
    item: str
    field: str | None
    text: str
    quoted: str = Field(
        description="The words the comment is about, as they were in that revision: the field's "
        "text, or the item's title when the comment is about the item as a whole."
    )


class PlanFeedback(BaseModel):
    id: int = Field(description="The feedback's number: the id of the event that recorded it.")
    revision: int = Field(description="The revision it is about.")
    by: str
    by_kind: str
    at: datetime
    summary: str
    comments: list[PlanComment]


class PlanRevisionInfo(BaseModel):
    revision: int
    by: str = Field(description="Who made it: the agent that proposed the plan, or revised it.")
    by_kind: str
    at: datetime
    note: str | None = Field(description="What the revision says changed, in its author's words.")
    items: int = Field(description="How many items it holds.")
    removed: list[str] = Field(description="The items it took out of the revision before it.")
    feedback: int | None = Field(
        description="The number of the feedback a person gave on it, when they asked for changes."
    )


class PlanItemChange(BaseModel):
    ref: str
    change: str = Field(description="added, removed or changed, compared with the revision before.")
    fields: list[str] = Field(description="For a change, which parts changed.")
    before: dict[str, Any] = Field(description="For a change, those parts as they were.")


class PlanDrift(BaseModel):
    ref: str
    fields: list[str] = Field(
        description="What differs from the revision that was reviewed: parts of the item, or "
        "dropped."
    )


def _day(value: Any) -> str | None:
    return None if value is None else str(value)


def values_of(item: dict[str, Any], *, blocking_only: bool = False) -> dict[str, Any]:
    """The parts of an item that revisions are compared by, in a form that can be compared. A link
    of every kind counts, or with `blocking_only` only the links that make work wait."""
    links = sorted(
        f"{one['link_type']} {one['target']}"
        for one in item.get("links", [])
        if not blocking_only or one["link_type"] == _BLOCKING
    )
    criteria = sorted((one["id"], one["title"], one["body"]) for one in item.get("criteria", []))
    return {
        "title": item["title"],
        "body": item["body"],
        "priority": item["priority"],
        "size": item.get("size"),
        "start_on": _day(item.get("start_on")),
        "target_on": _day(item.get("target_on")),
        "parent": item.get("parent"),
        "links": links,
        "criteria": [list(one) for one in criteria],
    }


def diff_revisions(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[PlanItemChange]:
    """How a revision differs from the one before it: items added, items removed, and for the ones
    in both which parts changed with those parts as they were. Epics first, by number."""
    before = {one["ref"]: one for one in old}
    after = {one["ref"]: one for one in new}
    changes: list[PlanItemChange] = []
    for ref in sorted({*before, *after}, key=lambda one: split_node_ref_order(one)):
        if ref not in before:
            changes.append(PlanItemChange(ref=ref, change="added", fields=[], before={}))
        elif ref not in after:
            changes.append(PlanItemChange(ref=ref, change="removed", fields=[], before={}))
        else:
            then, now = values_of(before[ref]), values_of(after[ref])
            fields = [name for name in FIELDS if then[name] != now[name]]
            if fields:
                changes.append(
                    PlanItemChange(
                        ref=ref,
                        change="changed",
                        fields=fields,
                        before={name: then[name] for name in fields},
                    )
                )
    return changes


def split_node_ref_order(ref: str) -> tuple[int, int]:
    kind, number = split_node_ref(ref)
    return ("epic", "story", "task").index(kind), number


def drift_between(
    live: list[dict[str, Any]], reviewed: list[dict[str, Any]], dropped: set[str]
) -> list[PlanDrift]:
    """What has been changed in the items of a plan since the revision a person reviewed, outside
    a revision: an item that is dropped, and the parts that decide what is approved (a link that
    is not about waiting does not count). Empty when the items are as they were reviewed."""
    now = {one["ref"]: one for one in live}
    found: list[PlanDrift] = []
    for one in reviewed:
        ref = one["ref"]
        fields: list[str] = []
        if ref in dropped:
            fields.append("dropped")
        if ref not in now:
            fields.append("missing")
        else:
            then, here = values_of(one, blocking_only=True), values_of(now[ref], blocking_only=True)
            fields.extend(name for name in FIELDS if then[name] != here[name])
        if fields:
            found.append(PlanDrift(ref=ref, fields=fields))
    return sorted(found, key=lambda one: split_node_ref_order(one.ref))


def quoted_of(item: dict[str, Any], field: str | None) -> str:
    """The words a comment on this part of the item is about."""
    if field is None:
        return str(item["title"])
    if field == "links":
        return (
            "; ".join(f"{one['link_type']} {one['target']}" for one in item["links"]) or "no links"
        )
    if field == "criteria":
        return "; ".join(one["title"] for one in item["criteria"]) or "no acceptance criteria"
    value = item.get(field)
    return "none" if value is None or value == "" else str(value)


def clean_summary(summary: str) -> str:
    cleaned = summary.strip()
    if not cleaned:
        raise Refusal(
            "Say in summary what you want changed, so that whoever revises the plan knows. "
            "Nothing was changed."
        )
    if len(cleaned) > MOST_SUMMARY_CHARS:
        raise Refusal(
            f"The summary may be at most {MOST_SUMMARY_CHARS:,} characters. Nothing was changed."
        )
    return cleaned


def clean_comments(
    comments: list[PlanCommentInput] | None, reviewed: list[dict[str, Any]]
) -> list[PlanComment]:
    """The comments of a request for changes, each checked against the revision that was reviewed
    and given the words it is about. Every problem is listed together."""
    wanted = comments or []
    if len(wanted) > MOST_COMMENTS:
        raise Refusal(f"A request may have at most {MOST_COMMENTS} comments. Nothing was changed.")
    items = {one["ref"]: one for one in reviewed}
    problems: list[str] = []
    made: list[PlanComment] = []
    for number, comment in enumerate(wanted, start=1):
        item = comment.item.strip().lower()
        text = comment.text.strip()
        if item not in items:
            problems.append(f"Comment {number}: {item} is not an item of the revision reviewed.")
            continue
        if comment.field is not None and comment.field not in FIELDS:
            problems.append(
                f"Comment {number}: field must be one of {', '.join(FIELDS)}, but it is "
                f"{comment.field!r}."
            )
            continue
        if not text:
            problems.append(f"Comment {number}: say what you want in text.")
            continue
        if len(text) > MOST_COMMENT_CHARS:
            problems.append(
                f"Comment {number}: text may be at most {MOST_COMMENT_CHARS:,} characters."
            )
            continue
        made.append(
            PlanComment(
                item=item,
                field=comment.field,
                text=text,
                quoted=quoted_of(items[item], comment.field),
            )
        )
    if problems:
        shown = "\n".join(f"{n}. {problem}" for n, problem in enumerate(problems, 1))
        raise Refusal(
            f"The request was not sent, because of {len(problems)} "
            f"problem{'s' if len(problems) != 1 else ''}:\n{shown}\nNothing was changed."
        )
    return made
