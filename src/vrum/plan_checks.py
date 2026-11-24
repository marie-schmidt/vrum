"""What a person should know about a drafted plan before deciding (M50), worked out when the plan is
read whole and never stored.

Three checks, none of which makes a decision or changes what the graph allows:

- **circle**: whether the plan's needs_first links, with the ones that already exist, make a circle
  of waiting. The database refuses to make one (a trigger in schema.sql), so this is the same rule
  read again, with the items named; `circle_through` is the one piece of code that finds a circle,
  and the plan check of `create_plan` (workflow.py) uses it too.
- **unsized**: the stories and tasks with no size. It is the filter language's own `no:size`
  (query.py), so it can never say something different from the board's filter, and it never
  guesses a size.
- **duplicate**: existing epics and stories that look like one the plan makes, by meaning, from
  the same index and threshold as the check `create_plan` makes. When meaning cannot be used (no
  service, an index that is not built, a service that cannot be reached) the check says so and is
  `unavailable`: it is never reported as "no duplicates".

A check's state is ok, warning, failed or unavailable. A warning never stops an approval and a
failure does not either: the page shows them before the person decides, the person decides."""

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

from vrum.common import fetch_all, split_node_ref
from vrum.query import compile_query
from vrum.search import Searcher

CIRCLE = "circle"
UNSIZED = "unsized"
DUPLICATE = "duplicate"
OK = "ok"
WARNING = "warning"
FAILED = "failed"
UNAVAILABLE = "unavailable"
# How many existing items are named for one look-alike item.
MOST_RELATED = 3


class PlanRelated(BaseModel):
    ref: str
    kind: str
    title: str
    status: str
    similarity: float | None = Field(description="How alike in meaning, from 0 to 1.")


class PlanFinding(BaseModel):
    items: list[str] = Field(description="The items of the plan this is about, by reference.")
    words: str = Field(description="What is the matter, in plain words.")
    related: list[PlanRelated] = Field(
        description="The existing work it concerns, for a possible duplicate. Otherwise empty."
    )


class PlanCheck(BaseModel):
    kind: str = Field(description="circle, unsized or duplicate.")
    state: str = Field(
        description="ok, warning (something to weigh), failed (the plan breaks a rule the "
        "database keeps) or unavailable (it could not be checked, and the words say why)."
    )
    words: str = Field(description="The result in one plain sentence.")
    findings: list[PlanFinding]


def code(ref: str) -> str:
    kind, number = split_node_ref(ref)
    return f"{kind[0].upper()}-{number}"


def circle_through(graph: dict[str, set[str]], source: str, target: str) -> list[str] | None:
    """The circle that the link source -> target makes (source needs target), as the names along
    it from source and back to source, or None when target cannot get back to source. A name is
    whatever the graph's nodes are called."""
    path: dict[str, str | None] = {target: None}
    queue = [target]
    while queue and source not in path:
        here = queue.pop(0)
        for following in sorted(graph.get(here, ())):
            if following not in path:
                path[following] = here
                queue.append(following)
    if source not in path:
        return None
    route: list[str] = []
    step: str | None = source
    while step is not None:
        route.append(step)
        step = path[step]
    route.reverse()
    return [source, *route]


def circles_among(edges: Iterable[tuple[str, str]], among: set[str]) -> list[list[str]]:
    """Every circle of waiting that goes through one of `among`, once, over these needs_first
    links (source, target), each as the names along it."""
    pairs = set(edges)
    graph: dict[str, set[str]] = {}
    for source, target in pairs:
        graph.setdefault(source, set()).add(target)
    seen: set[frozenset[str]] = set()
    found: list[list[str]] = []
    for source, target in sorted(pairs):
        if source not in among:
            continue
        names = circle_through(graph, source, target)
        if names is None or frozenset(names) in seen:
            continue
        seen.add(frozenset(names))
        found.append(names)
    return found


def _many(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _codes(refs: list[str]) -> str:
    names = [code(ref) for ref in refs]
    if len(names) <= 4:
        return ", ".join(names)
    return f"{', '.join(names[:4])} and {len(names) - 4} more"


async def _circle_check(conn: Any, refs: list[str]) -> PlanCheck:
    rows = await fetch_all(conn, "SELECT source, target FROM links WHERE link_type = 'needs_first'")
    circles = circles_among(((r["source"], r["target"]) for r in rows), set(refs))
    if not circles:
        return PlanCheck(
            kind=CIRCLE,
            state=OK,
            words="Nothing waits, directly or through others, for itself.",
            findings=[],
        )
    findings = [
        PlanFinding(
            items=sorted({name for name in names if name in refs}, key=split_node_ref),
            words=f"{code(names[0])} needs "
            + ", which needs ".join(code(name) for name in names[1:])
            + ". None of them could ever start.",
            related=[],
        )
        for names in circles
    ]
    return PlanCheck(
        kind=CIRCLE,
        state=FAILED,
        words=_many(len(circles), "circle", "circles")
        + " of waiting, so work in it could never start.",
        findings=findings,
    )


async def _unsized_check(conn: Any, refs: list[str]) -> PlanCheck:
    condition, params = compile_query("no:size")
    rows = await fetch_all(
        conn,
        f"SELECT ref, kind FROM node_state WHERE ref = ANY(%(plan_refs)s) AND {condition} "
        "ORDER BY kind_rank, number",
        {**params, "plan_refs": refs},
    )
    if not rows:
        return PlanCheck(
            kind=UNSIZED,
            state=OK,
            words="Every story and task that needs a size has one.",
            findings=[],
        )
    unsized = [r["ref"] for r in rows]
    tasks = sum(1 for r in rows if r["kind"] == "task")
    stories = len(rows) - tasks
    what = " and ".join(
        part
        for part in (
            _many(stories, "story", "stories") if stories else "",
            _many(tasks, "task", "tasks") if tasks else "",
        )
        if part
    )
    return PlanCheck(
        kind=UNSIZED,
        state=WARNING,
        words=f"{what} with no size: {_codes(unsized)}.",
        findings=[
            PlanFinding(items=[ref], words=f"{code(ref)} has no size.", related=[])
            for ref in unsized
        ],
    )


async def unindexed_elsewhere(conn: Any, refs: list[str]) -> int:
    """How many existing epics and stories (not the plan's own) are waiting to be indexed, so that
    the duplicate check could not compare the plan with them yet."""
    built = await fetch_all(conn, "SELECT to_regclass('semantic_index') IS NOT NULL AS built")
    if not built[0]["built"]:
        return 0
    row = await fetch_all(
        conn,
        "SELECT count(*) AS waiting FROM index_source s WHERE s.item_type IN ('epic', 'story') "
        "AND s.ref <> ALL(%s) AND NOT EXISTS (SELECT 1 FROM semantic_index i "
        "WHERE i.project_id = s.project_id AND i.item_type = s.item_type "
        "AND i.item_id = s.id AND i.content_hash = s.content_hash)",
        (refs,),
    )
    return int(row[0]["waiting"])


async def _duplicate_check(
    searcher: Searcher, items: list[tuple[str, str, str, str]], behind: int
) -> PlanCheck:
    sized = [one for one in items if one[1] in ("epic", "story")]
    found, note = await searcher.possible_duplicates(
        [f"{title}\n{body}".strip() for _, _, title, body in sized],
        types=["epic", "story"],
        exclude={(kind, split_node_ref(ref)[1]) for ref, kind, _, _ in items},
    )
    if note.meaning != "used":
        return PlanCheck(
            kind=DUPLICATE,
            state=UNAVAILABLE,
            words=f"Not checked for work that already exists. {note.note}",
            findings=[],
        )
    findings: list[PlanFinding] = []
    for (ref, _, _, _), matches in zip(sized, found, strict=True):
        if not matches:
            continue
        related = [
            PlanRelated(
                ref=m.ref,
                kind=m.item_type,
                title=m.title,
                status=m.status,
                similarity=m.similarity,
            )
            for m in matches[:MOST_RELATED]
        ]
        best = related[0]
        percent = "" if best.similarity is None else f" {round(best.similarity * 100)}%"
        findings.append(
            PlanFinding(
                items=[ref],
                words=(
                    f"{code(ref)} looks{percent} like {code(best.ref)} {best.title}, "
                    f"which already exists ({best.status.replace('_', ' ')})."
                ),
                related=related,
            )
        )
    later = (
        f" {_many(behind, 'existing epic or story is', 'existing epics and stories are')} not "
        f"indexed yet, so {'it was' if behind == 1 else 'they were'} not compared."
        if behind > 0
        else ""
    )
    if not findings:
        return PlanCheck(
            kind=DUPLICATE,
            state=OK,
            words="No indexed epic or story looks like one this plan makes." + later,
            findings=[],
        )
    return PlanCheck(
        kind=DUPLICATE,
        state=WARNING,
        words=(
            f"{_many(len(findings), 'epic or story', 'epics and stories')} may duplicate work "
            "that exists: " + _codes([f.items[0] for f in findings]) + "." + later
        ),
        findings=findings,
    )


async def local_checks(conn: Any, refs: list[str]) -> list[PlanCheck]:
    """The checks that read only the graph: circles and unsized work."""
    return [await _circle_check(conn, refs), await _unsized_check(conn, refs)]


async def duplicate_check(
    searcher: Searcher, items: list[tuple[str, str, str, str]], behind: int
) -> PlanCheck:
    """Whether existing epics or stories look like the plan's (reference, kind, title, body);
    `behind` is how many existing ones are not indexed yet (unindexed_elsewhere). Goes to the
    embedding service, so call it with no transaction open; never refuses."""
    return await _duplicate_check(searcher, items, behind)
