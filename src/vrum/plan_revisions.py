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
from typing import Any, Literal

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
    approved: list[str] = Field(
        description="The items the person approved as they were in that revision (an item it "
        "removed counts when the person agreed with the removal)."
    )


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


class PlanReview(BaseModel):
    state: Literal["approved", "asked", "changed", "none"] = Field(
        description="approved (a person approved the item as it is now), asked (a person asked "
        "for a change to it and it has not changed since), changed (a person reviewed it and it "
        "has changed since: it needs a fresh look) or none (nobody has said anything about it)."
    )
    revision: int | None = Field(description="The revision the person's latest word was about.")
    by: str | None = Field(description="The person who said it.")
    at: datetime | None
    feedback: int | None = Field(description="The number of the feedback it was said in.")


class PlanBlame(BaseModel):
    """Who last changed an item of a plan, in which revision and with what note, and what a person
    has said about it since: the review of each line, like git blame is for code."""

    ref: str
    change: Literal["added", "changed", "removed"] = Field(
        description="How the item came to be as it is: added in that revision, changed in it, or "
        "(for an item the shown revision took out) removed by it."
    )
    revision: int = Field(description="The revision that added, changed or removed it.")
    by: str = Field(description="Who made that revision.")
    by_kind: str
    at: datetime
    note: str | None = Field(description="What that revision says changed, in its author's words.")
    review: PlanReview


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


def _known(reviewed: list[dict[str, Any]], removed: list[dict[str, Any]]) -> dict[str, Any]:
    """What a person can say something about in a revision: its items and the items it took out
    (they can be approved as removed, or asked to come back)."""
    items = {one["ref"]: one for one in removed}
    items.update({one["ref"]: one for one in reviewed})
    return items


def clean_comments(
    comments: list[PlanCommentInput] | None,
    reviewed: list[dict[str, Any]],
    removed: list[dict[str, Any]] | None = None,
) -> list[PlanComment]:
    """The comments of a request for changes, each checked against the revision that was reviewed
    (or an item it took out) and given the words it is about. Every problem is listed together."""
    wanted = comments or []
    if len(wanted) > MOST_COMMENTS:
        raise Refusal(f"A request may have at most {MOST_COMMENTS} comments. Nothing was changed.")
    items = _known(reviewed, removed or [])
    problems: list[str] = []
    made: list[PlanComment] = []
    for number, comment in enumerate(wanted, start=1):
        item = comment.item.strip().lower()
        text = comment.text.strip()
        if item not in items:
            problems.append(
                f"Comment {number}: {item} is not an item of the revision reviewed, or one it "
                "took out."
            )
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


def clean_approved(
    approved: list[str] | None,
    comments: list[PlanComment],
    reviewed: list[dict[str, Any]],
    removed: list[dict[str, Any]] | None = None,
) -> list[str]:
    """The items a person approves as they are, checked against the revision reviewed (and the
    items it took out). An item cannot be approved and also have a comment asking for a change.
    Every problem is listed together; the answer is sorted and has each item once."""
    known = _known(reviewed, removed or [])
    asked = {one.item for one in comments}
    chosen: list[str] = []
    problems: list[str] = []
    for number, raw in enumerate(approved or [], start=1):
        item = raw.strip().lower()
        if item not in known:
            problems.append(
                f"Approved item {number}: {item} is not an item of the revision reviewed, or one "
                "it took out."
            )
        elif item in asked:
            problems.append(
                f"{item} is approved and also has a comment asking for a change. Approve it or "
                "ask for the change, not both."
            )
        elif item not in chosen:
            chosen.append(item)
    if problems:
        shown = "\n".join(f"{n}. {problem}" for n, problem in enumerate(problems, 1))
        raise Refusal(
            f"The request was not sent, because of {len(problems)} "
            f"problem{'s' if len(problems) != 1 else ''}:\n{shown}\nNothing was changed."
        )
    return sorted(chosen, key=split_node_ref_order)


def blame_of(
    snapshots: dict[int, list[dict[str, Any]]],
    revisions: list[PlanRevisionInfo],
    feedback: list[PlanFeedback],
    shown: int,
) -> list[PlanBlame]:
    """For each item of the revision shown (and each item it took out), who last changed it and
    what a person has said about it. `snapshots` holds every revision up to `shown`, as it was
    submitted. An approval or a request for a change stands for an item while the item is as it
    was when the person said it; once it changes the item is `changed`: it needs a fresh look."""
    by_revision = {one.revision: one for one in revisions}
    values = {
        revision: {item["ref"]: values_of(item) for item in items}
        for revision, items in snapshots.items()
    }
    last: dict[str, tuple[int, str]] = {}
    for revision in range(1, shown + 1):
        now = values.get(revision, {})
        before = values.get(revision - 1, {})
        for ref, then in now.items():
            if ref not in before:
                last[ref] = (revision, "added")
            elif before[ref] != then:
                last[ref] = (revision, "changed")
    wanted = [item["ref"] for item in snapshots.get(shown, [])]
    gone: list[str] = []
    if shown > 1:
        gone = [ref for ref in values.get(shown - 1, {}) if ref not in values.get(shown, {})]
    mentions: dict[str, list[tuple[PlanFeedback, str]]] = {}
    for one in feedback:
        if one.revision > shown:
            continue
        for ref in one.approved:
            mentions.setdefault(ref, []).append((one, "approved"))
        for comment in one.comments:
            mentions.setdefault(comment.item, []).append((one, "asked"))
    found: list[PlanBlame] = []
    for ref in sorted(wanted + gone, key=split_node_ref_order):
        removed = ref in gone
        revision, how = (shown, "removed") if removed else last[ref]
        info = by_revision[revision]
        review = PlanReview(state="none", revision=None, by=None, at=None, feedback=None)
        said = mentions.get(ref, [])
        if said:
            one, word = max(said, key=lambda found_one: (found_one[0].id, found_one[1] == "asked"))
            if removed:
                stands = one.revision == shown
            else:
                stands = values[one.revision].get(ref) == values[shown][ref]
            state = word if stands else "changed"
            if not (removed and not stands):
                review = PlanReview(
                    state=state,  # type: ignore[arg-type]
                    revision=one.revision,
                    by=one.by,
                    at=one.at,
                    feedback=one.id,
                )
        found.append(
            PlanBlame(
                ref=ref,
                change=how,  # type: ignore[arg-type]
                revision=revision,
                by=info.by,
                by_kind=info.by_kind,
                at=info.at,
                note=info.note,
                review=review,
            )
        )
    return found
