"""A steward for unsorted work: it reads the stories in the Inbox epic, asks the server where each
one most likely belongs (`suggest_parent`), and proposes the moves a person should look at
(`propose_placement`). It changes nothing itself: a proposal waits on the board's Needs a person
page until a person accepts or rejects it, and accepting makes the move as that person.

    VRUM_AGENT_KEY=<the key of the steward> uv run python scripts/steward.py \\
        http://127.0.0.1:8000/mcp --dry-run

It uses no language model (Vrum has none), so it proposes only where the model of meaning is sure:
the best epic must be ahead of the second by the server's own margin (`sure`) and score at least
`--min-score`. Work it is not sure about stays in the Inbox for a person, or for an agent that can
read it and name a new epic for it (see docs/agents.md, "A steward"). One proposal is made for each
epic that stories are sent to (at most 20 stories in one), at most `--max-proposals` in a run; the
server also refuses more than 25 waiting from one key. Run it as often as you like: work that is
already in a waiting proposal is left alone.

The steward's key is an ordinary agent key (Users, then Agents) allowed `list_nodes`,
`suggest_parent`, `list_placements` and `propose_placement`, and nothing that changes work or
confirms anything: whatever it proposes, it cannot make happen. Call it `steward`, so that the
history says who proposed what."""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from vrum_client import connect, said

MOST_IN_A_PROPOSAL = 20
MOST_REASON_CHARS = 1000


@dataclass
class Sent:
    ref: str
    title: str
    parent: str
    parent_title: str
    score: float
    margin: float
    because: list[str]

    def line(self) -> str:
        like = f", like {', '.join(self.because)}" if self.because else ""
        return f"{self.ref} {self.title!r}: {self.score:.2f}, {self.margin:.2f} ahead{like}"


def code(ref: str) -> str:
    kind, _, number = ref.partition(":")
    return f"{kind[:1].upper()}-{number}"


async def call(client: Any, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    if result.is_error or result.structured_content is None:
        raise SystemExit(f"{tool} was refused: {said(result)}")
    return dict(result.structured_content)


async def loose_stories(client: Any) -> tuple[str | None, list[dict[str, Any]]]:
    """The Inbox epic and the stories in it that are not finished."""
    epics = (await call(client, "list_nodes", kind="epic", limit=200))["items"]
    inbox = next((e["ref"] for e in epics if e["title"].strip().lower() == "inbox"), None)
    if inbox is None:
        return None, []
    stories = (await call(client, "list_nodes", kind="story", parent=inbox, limit=200))["items"]
    return inbox, [s for s in stories if s["status"] not in ("done", "dropped")]


async def already_proposed(client: Any) -> set[str]:
    waiting = (await call(client, "list_placements", status="proposed", limit=200))["items"]
    return {item["ref"] for one in waiting for item in one["items"]}


async def steward(url: str, *, most: int, floor: float, dry_run: bool, look_at: int) -> int:
    async with connect(url) as client:
        inbox, loose = await loose_stories(client)
        if inbox is None:
            print("There is no epic called Inbox, so there is no unsorted work to look at.")
            return 0
        waiting = await already_proposed(client)
        todo = [s for s in loose if s["ref"] not in waiting][:look_at]
        print(f"{len(loose)} stories in {code(inbox)} Inbox; looking at {len(todo)}.")
        by_epic: dict[str, list[Sent]] = {}
        left = 0
        for story in todo:
            found = await call(client, "suggest_parent", node=story["ref"], limit=3)
            best = found["suggestions"][0] if found["suggestions"] else None
            if best is None or not found["sure"] or best["score"] < floor:
                left += 1
                print(f"  {code(story['ref'])} {story['title']!r}: not sure, left for a person.")
                continue
            by_epic.setdefault(best["ref"], []).append(
                Sent(
                    story["ref"],
                    story["title"],
                    best["ref"],
                    best["title"],
                    best["score"],
                    found["margin"],
                    [f"{code(one['ref'])} ({one['similarity']:.2f})" for one in best["because"]],
                )
            )
        made = 0
        for parent, sent in by_epic.items():
            for start in range(0, len(sent), MOST_IN_A_PROPOSAL):
                chunk = sent[start : start + MOST_IN_A_PROPOSAL]
                title = chunk[0].parent_title
                reason = f"Nearest by meaning to {code(parent)} {title}. " + "; ".join(
                    one.line() for one in chunk
                )
                reason = reason[:MOST_REASON_CHARS]
                names = ", ".join(code(one.ref) for one in chunk)
                if made >= most:
                    print(f"  stopped at {most} proposals: {names} wait for the next run.")
                    continue
                if dry_run:
                    print(f"  would propose {names} under {code(parent)} {title}")
                    made += 1
                    continue
                reply = await client.call_tool(
                    "propose_placement",
                    {"items": [one.ref for one in chunk], "parent": parent, "reason": reason},
                )
                if reply.is_error:
                    print(f"  {names}: the server said: {said(reply)}")
                    return 1
                made += 1
                print(f"  proposed {names} under {code(parent)} {title}")
        verb = "would make" if dry_run else "made"
        print(f"Done: {verb} {made} proposals, left {left} stories for a person.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Propose where unsorted work should go.")
    parser.add_argument("url", help="the server's MCP address, ending in /mcp")
    parser.add_argument("--dry-run", action="store_true", help="say what it would propose")
    parser.add_argument("--max-proposals", type=int, default=10, help="at most this many a run")
    parser.add_argument("--look-at", type=int, default=50, help="at most this many stories a run")
    parser.add_argument(
        "--min-score", type=float, default=0.30, help="the lowest score it will act on"
    )
    args = parser.parse_args()
    if args.max_proposals < 1 or args.look_at < 1:
        print("--max-proposals and --look-at must be 1 or more.", file=sys.stderr)
        return 2
    return asyncio.run(
        steward(
            args.url,
            most=args.max_proposals,
            floor=args.min_score,
            dry_run=args.dry_run,
            look_at=args.look_at,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
