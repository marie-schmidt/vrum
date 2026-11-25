"""Topics for the Needs a person list (M52): which of the things waiting for a person are about the
same thing, so that a person can settle them together instead of jumping between subjects.

A row's *subject* is the work it is about (the story or task of a question, the items of a plan or
of a move). Subjects that are close in meaning, by the same index search by meaning uses (stored
vectors only: nothing here calls the embedding service), are put in one topic by average-link
clustering. A topic is named by the epic that most of its rows are under when that is most of them,
and otherwise by the title of the work that is closest to the others; a row that is alone in its
meaning, or has no vector yet, takes the epic it is under, so that two rows under one epic are
always found together. A row with neither a vector nor an epic has no topic.

The clustering is a pure function of a table of similarities and is tested on its own; the database
only supplies the similarities and the epics."""

from typing import Any, Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import fetch_all

# Texts about one idea measure near 1 and texts about different ideas near 0 (the scripted service
# of the checks gives exactly that; the built-in model puts related work between 0.35 and 0.55 and
# unrelated work under 0.35). Subjects join a topic when, on average, they are at least this alike.
TOPIC_SIMILARITY = 0.45


class NeedsTopic(BaseModel):
    id: str = Field(
        description="The epic's reference when the topic is named by an epic, else topic:N. Rows "
        "with the same id are about the same thing."
    )
    label: str = Field(description="What to call the topic: an epic's title or a work's title.")
    basis: Literal["meaning", "epic"] = Field(
        description="meaning when the rows were put together by how alike their work is, epic "
        "when they only share the epic they are under."
    )


def clusters(keys: list[int], similarity: dict[tuple[int, int], float]) -> list[list[int]]:
    """Group `keys` by average-link clustering: the two groups whose members are most alike on
    average join while that average reaches TOPIC_SIMILARITY. `similarity[(a, b)]` is given for
    a < b; a pair that is missing is not alike at all. Groups come back in the order of their
    first key, each in key order."""
    groups: dict[int, list[int]] = {key: [key] for key in keys}
    alike: dict[tuple[int, int], float] = {}
    for first in keys:
        for second in keys:
            if first < second:
                alike[(first, second)] = similarity.get((first, second), 0.0)
    while len(groups) > 1:
        best = max(
            alike.items(), key=lambda found: (found[1], -found[0][0], -found[0][1]), default=None
        )
        if best is None or best[1] < TOPIC_SIMILARITY:
            break
        (left, right), _ = best
        size_left, size_right = len(groups[left]), len(groups[right])
        groups[left] = sorted(groups[left] + groups.pop(right))
        del alike[(left, right)]
        for other in list(groups):
            if other in (left, right):
                continue
            low_left, low_right = (left, other) if left < other else (other, left)
            high_left, high_right = (right, other) if right < other else (other, right)
            merged = (
                size_left * alike[(low_left, low_right)]
                + size_right * alike.pop((high_left, high_right))
            ) / (size_left + size_right)
            alike[(low_left, low_right)] = merged
    return sorted(groups.values(), key=lambda members: members[0])


class Subject(BaseModel):
    """What one row is about: the references of the work (stories, tasks or epics) and, when the
    row is about where work is going, the reference it is going under."""

    refs: list[str]
    under: str | None = None


def _central(members: list[int], similarity: dict[tuple[int, int], float]) -> int:
    """The member most alike the others on average (the first of equals)."""
    if len(members) == 1:
        return members[0]

    def mean(key: int) -> float:
        total = sum(
            similarity.get((min(key, other), max(key, other)), 0.0)
            for other in members
            if other != key
        )
        return total / (len(members) - 1)

    return max(members, key=lambda key: (mean(key), -key))


def name_topics(
    epics: list[tuple[str, str] | None],
    titles: list[str],
    groups: list[list[int]],
    similarity: dict[tuple[int, int], float],
) -> list[NeedsTopic | None]:
    """The topic of every row. `epics[i]` is the epic (reference, title) row i is under, `titles[i]`
    the title of the work it is about, and `groups` the clusters of the rows that have a vector."""
    topics: list[NeedsTopic | None] = [None] * len(epics)
    clustered: set[int] = set()
    for members in groups:
        if len(members) < 2:
            continue
        clustered.update(members)
        under: dict[str, int] = {}
        names: dict[str, str] = {}
        for key in members:
            found = epics[key]
            if found is not None:
                under[found[0]] = under.get(found[0], 0) + 1
                names[found[0]] = found[1]
        ranked = sorted(under.items(), key=lambda entry: (-entry[1], entry[0]))
        if ranked and ranked[0][1] * 2 >= len(members):
            ref = ranked[0][0]
            topic = NeedsTopic(id=ref, label=names[ref], basis="meaning")
        else:
            topic = NeedsTopic(
                id=f"topic:{members[0] + 1}",
                label=titles[_central(members, similarity)],
                basis="meaning",
            )
        for key in members:
            topics[key] = topic
    for key, found in enumerate(epics):
        if key not in clustered and found is not None:
            topics[key] = NeedsTopic(id=found[0], label=found[1], basis="epic")
    return topics


# The epic a piece of work is under: itself for an epic, its parent for a story, its parent's
# parent for a task. Also returns the node's own title so callers do not query nodes twice.
_EPICS = (
    "SELECT n.ref, n.title, e.ref AS epic, e.title AS epic_title "
    "FROM nodes n LEFT JOIN nodes p ON p.ref = n.parent "
    "JOIN nodes e ON e.ref = CASE n.kind WHEN 'epic' THEN n.ref WHEN 'story' THEN n.parent "
    "ELSE p.parent END "
    "WHERE n.ref = ANY(%s)"
)

# The mean of the up to date vectors of each subject's work, and how alike each pair of subjects is.
_VECTORS = (
    "WITH subject(k, ref) AS (SELECT * FROM unnest(%(keys)s::int[], %(refs)s::text[])), "
    "vec AS (SELECT s.k, l2_normalize(avg(l2_normalize(i.embedding))) AS e "
    "FROM subject s JOIN nodes n ON n.ref = s.ref "
    "JOIN semantic_index i ON i.project_id = n.project_id AND i.item_type = n.kind "
    "AND i.item_id = n.number "
    "JOIN index_source x ON x.project_id = i.project_id AND x.item_type = i.item_type "
    "AND x.id = i.item_id AND x.content_hash = i.content_hash GROUP BY s.k) "
    "SELECT a.k AS first, b.k AS second, 1 - (a.e <=> b.e) AS alike "
    "FROM vec a JOIN vec b ON a.k <= b.k"
)


async def topics_of(conn: AsyncConnection, subjects: list[Subject]) -> list[NeedsTopic | None]:
    """The topic of each subject, in order."""
    if not subjects:
        return []
    every_ref = sorted({ref for subject in subjects for ref in subject.refs})
    anchors = sorted({subject.under for subject in subjects if subject.under})
    node_rows = await fetch_all(conn, _EPICS, (sorted(set(every_ref + anchors)),))
    found = {row["ref"]: (row["epic"], row["epic_title"]) for row in node_rows}
    by_ref = {row["ref"]: row["title"] for row in node_rows}
    epics: list[tuple[str, str] | None] = []
    titles: list[str] = []
    for subject in subjects:
        anchor = subject.under or (subject.refs[0] if subject.refs else None)
        epics.append(found.get(anchor) if anchor else None)
        titles.append(by_ref.get(subject.refs[0], "") if subject.refs else "")
    similarity: dict[tuple[int, int], float] = {}
    with_vector: list[int] = []
    built = await fetch_all(
        conn,
        "SELECT to_regclass('semantic_index') IS NOT NULL AS built",
    )
    if built[0]["built"]:
        keys: list[int] = []
        refs: list[str] = []
        for key, subject in enumerate(subjects):
            for ref in subject.refs:
                keys.append(key)
                refs.append(ref)
        rows: list[dict[str, Any]] = await fetch_all(conn, _VECTORS, {"keys": keys, "refs": refs})
        seen: set[int] = set()
        for row in rows:
            seen.update((row["first"], row["second"]))
            if row["first"] < row["second"]:
                similarity[(row["first"], row["second"])] = float(row["alike"])
        with_vector = sorted(seen)
    groups = clusters(with_vector, similarity)
    return name_topics(epics, titles, groups, similarity)
