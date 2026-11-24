"""Measure how well meaning finds where a piece of work belongs (M43).

For each story and task of a project, hide where it is, and ask which epic (or story) the embedding
model finds nearest. If the model usually names the right one, a read-only `suggest_parent` and a
steward agent that proposes where loose work goes are worth building; if it does not, they are not,
and a person keeps sorting. This script is the test, and it is meant to be run again on your own
project and on any other embedding model.

    uv run python scripts/parent_accuracy.py --demo
    uv run python scripts/parent_accuracy.py --export vrum-export.json

`--demo` is the invented Riverside Bike Share project of ./demo.sh (read from tests/demo_data.py:
no server needed). `--export` is a file written by scripts/export_graph.py, so your own project can
be measured with `VRUM_AGENT_KEY=... uv run python scripts/export_graph.py <url> vrum-export.json`.
It talks to the embedding service the server would use: EMBEDDING_BASE_URL and EMBEDDING_MODEL (and
EMBEDDING_API_KEY, EMBEDDING_MAX_CHARS), or --base-url and --model. It reads nothing else and
changes nothing; it never sends your texts anywhere but that service.

Each item is hidden in turn (and, for a story, its own tasks with it), then compared with every
candidate parent in three ways: by what the parent says about itself (`own`), by what the work it
already holds says (`held`, the average of its other stories and tasks), and by both together
(`both`). It prints, for each question, how often the right parent came first and in the first
three, and how often the model was sure (the best parent clearly ahead of the second) and right."""

import argparse
import json
import math
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from vrum.config import EmbeddingSettings
from vrum.embeddings import BATCH_SIZE, EmbeddingClient, EmbeddingRejected, EmbeddingUnavailable

ROOT = Path(__file__).resolve().parent.parent
# How far ahead of the second best the best parent has to be to count as "sure".
MARGINS = (0.0, 0.02, 0.05, 0.10)
VIEWS = ("own", "held", "both")


@dataclass(frozen=True)
class Work:
    ref: str
    kind: str
    title: str
    body: str
    parent: str | None

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}".strip()


def from_demo() -> list[Work]:
    sys.path.insert(0, str(ROOT))
    from tests.demo_data import EPICS, Item  # noqa: PLC0415

    found: list[Work] = []
    counts = {"epic": 0, "story": 0, "task": 0}

    def walk(item: Item, parent: str | None) -> None:
        counts[item.kind] += 1
        ref = f"{item.kind}:{counts[item.kind]}"
        found.append(Work(ref, item.kind, item.title, item.body, parent))
        for child in item.children:
            walk(child, ref)

    for epic in EPICS:
        walk(epic, None)
    return found


def from_export(path: Path) -> list[Work]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return [
        Work(
            f"{node['kind']}:{node['number']}",
            node["kind"],
            node["title"],
            node.get("body") or "",
            node.get("parent"),
        )
        for node in document["nodes"]
    ]


def unit(vector: Sequence[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / length for value in vector]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def add(a: list[float], b: Sequence[float], sign: float = 1.0) -> None:
    for index, value in enumerate(b):
        a[index] += sign * value


@dataclass
class Tally:
    asked: int = 0
    first: int = 0
    three: int = 0
    sure: dict[float, int] | None = None
    sure_right: dict[float, int] | None = None

    def __post_init__(self) -> None:
        self.sure = {margin: 0 for margin in MARGINS}
        self.sure_right = {margin: 0 for margin in MARGINS}

    def record(self, ranked: list[tuple[float, str]], right: str) -> None:
        """`ranked` is the candidates, best first, as (score, ref)."""
        assert self.sure is not None and self.sure_right is not None
        self.asked += 1
        order = [ref for _, ref in ranked]
        if order[0] == right:
            self.first += 1
        if right in order[:3]:
            self.three += 1
        gap = ranked[0][0] - ranked[1][0] if len(ranked) > 1 else ranked[0][0]
        for margin in MARGINS:
            if gap >= margin:
                self.sure[margin] += 1
                if order[0] == right:
                    self.sure_right[margin] += 1


def percent(part: int, whole: int) -> str:
    return "-" if whole == 0 else f"{100 * part / whole:3.0f}%"


class Measure:
    def __init__(self, work: list[Work], vectors: dict[str, list[float]]) -> None:
        self.by_ref = {item.ref: item for item in work}
        self.vector = {ref: unit(vector) for ref, vector in vectors.items()}
        self.children: dict[str, list[str]] = {item.ref: [] for item in work}
        for item in work:
            if item.parent in self.children:
                self.children[item.parent].append(item.ref)
        self.dimension = len(next(iter(self.vector.values())))
        # Everything under a parent, at any depth, and the sum of those vectors.
        self.under: dict[str, set[str]] = {}
        self.sum_under: dict[str, list[float]] = {}
        for item in work:
            below = self._below(item.ref)
            self.under[item.ref] = below
            total = [0.0] * self.dimension
            for ref in below:
                add(total, self.vector[ref])
            self.sum_under[item.ref] = total

    def _below(self, ref: str) -> set[str]:
        found: set[str] = set()
        for child in self.children[ref]:
            found.add(child)
            found |= self._below(child)
        return found

    def in_inbox(self, item: Work) -> bool:
        parent = self.by_ref.get(item.parent) if item.parent else None
        return (
            item.title.strip().lower() == "inbox"
            and item.kind == "epic"
            or (
                parent is not None
                and parent.kind == "epic"
                and parent.title.strip().lower() == "inbox"
            )
        )

    def ancestors(self, ref: str) -> list[str]:
        chain = []
        parent = self.by_ref[ref].parent
        while parent is not None:
            chain.append(parent)
            parent = self.by_ref[parent].parent
        return chain

    def score(self, hidden: str, candidate: str, view: str) -> float:
        """How near the hidden item is to the candidate parent, by one of the three views."""
        query = self.vector[hidden]
        own = self.vector[candidate]
        gone = {hidden} | self.under[hidden]
        held = list(self.sum_under[candidate])
        count = len(self.under[candidate])
        for ref in gone & self.under[candidate]:
            add(held, self.vector[ref], -1.0)
            count -= 1
        if view == "own":
            return dot(query, own)
        if count <= 0:
            # Nothing else to go on: the parent is known only by what it says about itself.
            return dot(query, own)
        if view == "held":
            return dot(query, unit(held))
        both = list(own)
        add(both, unit(held))
        return dot(query, unit(both))

    def ask(self, hidden_kind: str, parent_kind: str, grandparent: bool) -> dict[str, Tally]:
        """Every item of `hidden_kind`, hidden in turn, asked for its `parent_kind` parent."""
        tallies = {view: Tally() for view in VIEWS}
        # An epic called Inbox is where unsorted work waits, and so are the stories in it: never a
        # place to send work (as in suggest_parent).
        candidates = [
            item.ref
            for item in self.by_ref.values()
            if item.kind == parent_kind and not self.in_inbox(item)
        ]
        for item in self.by_ref.values():
            if item.kind != hidden_kind:
                continue
            chain = self.ancestors(item.ref)
            right = next((ref for ref in chain if self.by_ref[ref].kind == parent_kind), None)
            if right is None or (not grandparent and chain[0] != right):
                continue
            for view in VIEWS:
                ranked = sorted(
                    ((self.score(item.ref, c, view), c) for c in candidates), reverse=True
                )
                tallies[view].record(ranked, right)
        return tallies


QUESTIONS = (
    ("a story, which epic", "story", "epic", False),
    ("a task, which story", "task", "story", False),
    ("a task, which epic", "task", "epic", True),
)


def report(measure: Measure) -> str:
    lines: list[str] = []
    for label, hidden, parent, grandparent in QUESTIONS:
        tallies = measure.ask(hidden, parent, grandparent)
        asked = tallies["own"].asked
        candidates = sum(1 for item in measure.by_ref.values() if item.kind == parent)
        lines.append(
            f"{label} ({asked} asked, {candidates} to choose from; chance is "
            f"{percent(1, candidates)})"
        )
        lines.append(
            f"  {'view':<6} {'first':>6} {'in 3':>6}   "
            + "   ".join(f"sure>={margin:.2f}: share right" for margin in MARGINS[1:])
        )
        for view in VIEWS:
            tally = tallies[view]
            assert tally.sure is not None and tally.sure_right is not None
            sure = "   ".join(
                f"{percent(tally.sure[m], tally.asked):>4} of items, "
                f"{percent(tally.sure_right[m], tally.sure[m]):>4}   "
                for m in MARGINS[1:]
            )
            lines.append(
                f"  {view:<6} {percent(tally.first, tally.asked):>6} "
                f"{percent(tally.three, tally.asked):>6}   {sure}"
            )
        lines.append("")
    return "\n".join(lines)


def embed_all(client: EmbeddingClient, work: list[Work], max_chars: int) -> dict[str, list[float]]:
    import asyncio  # noqa: PLC0415

    async def run() -> dict[str, list[float]]:
        vectors: dict[str, list[float]] = {}
        for start in range(0, len(work), BATCH_SIZE):
            batch = work[start : start + BATCH_SIZE]
            found = await client.embed([item.text[:max_chars] for item in batch])
            vectors.update({item.ref: vector for item, vector in zip(batch, found, strict=True)})
        return vectors

    return asyncio.run(run())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="the invented Riverside Bike Share")
    source.add_argument("--export", type=Path, help="a file written by scripts/export_graph.py")
    parser.add_argument("--base-url", default=os.environ.get("EMBEDDING_BASE_URL"))
    parser.add_argument("--model", default=os.environ.get("EMBEDDING_MODEL"))
    parser.add_argument(
        "--max-chars", type=int, default=int(os.environ.get("EMBEDDING_MAX_CHARS", 2000))
    )
    args = parser.parse_args()
    if not args.base_url or not args.model:
        print(
            "Say which embedding service to use: set EMBEDDING_BASE_URL and EMBEDDING_MODEL (the "
            "ones the server uses), or pass --base-url and --model.",
            file=sys.stderr,
        )
        return 2
    work = from_demo() if args.demo else from_export(args.export)
    epics = sum(1 for item in work if item.kind == "epic")
    stories = sum(1 for item in work if item.kind == "story")
    tasks = sum(1 for item in work if item.kind == "task")
    if epics < 2 or stories + tasks < 10:
        print(
            "There is too little work here to measure anything (it needs at least 2 epics and 10 "
            "stories and tasks).",
            file=sys.stderr,
        )
        return 2
    client = EmbeddingClient(
        EmbeddingSettings(
            base_url=args.base_url.rstrip("/"),
            model=args.model,
            api_key=os.environ.get("EMBEDDING_API_KEY") or None,
            max_chars=args.max_chars,
            min_similarity=0.0,
            overlap_threshold=0.0,
            reindex_seconds=0.0,
        )
    )
    try:
        vectors = embed_all(client, work, args.max_chars)
    except (EmbeddingUnavailable, EmbeddingRejected) as problem:
        print(str(problem), file=sys.stderr)
        return 1
    print(f"{epics} epics, {stories} stories, {tasks} tasks, model {args.model}\n")
    print(report(Measure(work, vectors)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
