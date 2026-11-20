"""Try a running Vrum with a real embedding model and print what it does.

    VRUM_AGENT_KEY=<the agent key of the project> uv run python scripts/real_embeddings_check.py http://127.0.0.1:8000/mcp

It adds a small, clearly named set of epics and stories (reworded requirements, related but
different ones, and unrelated ones) to the graph it is pointed at, waits for the server's own job
to index them, then reports what the overlap finder and search by meaning make of them. Use it on a
stack you do not mind adding a few items to: ./check.sh uses a scripted stand-in instead, which
proves what Vrum does with an embedding service but says nothing about a model's quality.
"""

import asyncio
import statistics
import sys
import time
from datetime import UTC, datetime
from typing import Any

from mcp import Client
from vrum_client import connect

from vrum.common import split_node_ref

DUPLICATES = [
    (
        (
            "Email invoices to customers",
            "After a payment succeeds, the customer gets an invoice PDF by email.",
        ),
        (
            "Send receipts by mail to clients",
            "Once we have charged a client, they should be mailed a bill as a PDF.",
        ),
    ),
    (
        ("Export reports as CSV", "Users can download the monthly report as a CSV file."),
        (
            "Download monthly summaries in CSV format",
            "Let people get the month's report as a .csv export.",
        ),
    ),
    (
        ("Password login", "Users sign in with an email address and a password."),
        ("Authenticate with credentials", "Customers log in using their email and password."),
    ),
    (
        ("Dark mode", "The app has a dark colour theme that can be switched on."),
        ("Night theme for the interface", "Offer a dark appearance option in the application."),
    ),
    (
        ("Nightly database backups", "Back up the database every night and keep seven copies."),
        (
            "Daily snapshots of the data store",
            "Take a snapshot of the DB each night; retain a week of them.",
        ),
    ),
    (
        ("Cancel subscription", "A customer can cancel their subscription from the account page."),
        (
            "Let members end their plan",
            "Account settings should allow a member to terminate their membership.",
        ),
    ),
    (
        (
            "Search is slow on big projects",
            "Searching a project with thousands of items takes many seconds.",
        ),
        (
            "Slow lookups for large projects",
            "Queries over a very large project are sluggish and take ages.",
        ),
    ),
    (
        ("Translate the UI", "The interface should be available in French and German."),
        ("Localise the application", "Provide French and German translations of the screens."),
    ),
    (
        ("Audit log of changes", "Record who changed what and when, and let admins read it."),
        (
            "History trail for edits",
            "Keep a log of every edit with the author and time, visible to admins.",
        ),
    ),
    (
        ("Role based access", "Admins can give people roles that control what they can do."),
        ("Permissions per user", "Let administrators assign privileges that limit user actions."),
    ),
]
# In the same area as one story above, but a different requirement. They go in the second epic.
RELATED = [
    (
        "Email password reset links",
        "When a user forgets their password they get a reset link by email.",
    ),
    ("Import customers from CSV", "Admins can upload a CSV file to create many customers at once."),
    ("Two factor login", "Users confirm sign in with a code from an authenticator app."),
    (
        "Restore the database from a backup",
        "An admin can restore the data from a chosen backup file.",
    ),
]
# What a person might type, and the story it is about (an index into the first of each pair).
QUERIES = [
    ("customers should receive their bill by mail after paying", 0),
    ("how do users get data out as spreadsheets", 1),
    ("making the interface dark", 3),
    ("what happens if the database is lost", 4),
]


async def call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    if result.is_error or result.structured_content is None:
        raise SystemExit(f"{tool} was refused: {result.content}")
    return dict(result.structured_content)


def by_number(pair: tuple[str, str]) -> tuple[int, int]:
    """Stories of one kind in the order they were made: `story:2` before `story:10`."""
    return split_node_ref(pair[0])[1], split_node_ref(pair[1])[1]


async def story(client: Client, epic: str, text: tuple[str, str]) -> str:
    made = await call(
        client,
        "create_node",
        kind="story",
        title=text[0],
        body=text[1],
        parent=epic,
    )
    return str(made["ref"])


async def main(url: str) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    async with connect(url) as client:
        report = await call(client, "reindex")
        print(f"model {report['model']}, vectors of {report['dimensions']} numbers")
        epic_a = await call(
            client,
            "create_node",
            kind="epic",
            title=f"Real-model check A ({stamp})",
        )
        epic_b = await call(
            client,
            "create_node",
            kind="epic",
            title=f"Real-model check B ({stamp})",
        )
        firsts = [await story(client, epic_a["ref"], pair[0]) for pair in DUPLICATES]
        seconds = [await story(client, epic_b["ref"], pair[1]) for pair in DUPLICATES]
        related = [await story(client, epic_b["ref"], text) for text in RELATED]

        # No reindex is asked for: the server's own job should index them.
        started = time.monotonic()
        while True:
            page = await call(client, "search", query="zzzqqq", mode="words")
            waiting = page["index"]["waiting_to_index"]
            if waiting == 0:
                break
            if time.monotonic() - started > 180:
                raise SystemExit(f"{waiting} items were still waiting after 3 minutes.")
            await asyncio.sleep(1)
        print(
            f"the server's own job indexed everything in {time.monotonic() - started:.0f} seconds"
        )

        pairs: dict[tuple[str, str], float] = {}
        offset = 0
        while True:
            overlaps = await call(
                client, "find_overlaps", epics=[epic_a["ref"]], limit=200, offset=offset
            )
            for item in overlaps["items"]:
                pairs[(item["first"]["ref"], item["second"]["ref"])] = item["similarity"]
            if overlaps["next_offset"] is None:
                break
            offset = overlaps["next_offset"]
        threshold = overlaps["threshold"]
        duplicates = set(zip(firsts, seconds, strict=True))
        # A related story is in the same area as one of the first stories (by index in
        # DUPLICATES), but is a different requirement.
        about = {0: 0, 1: 1, 2: 2, 3: 4}
        close = {(firsts[about[i]], related[i]) for i in about}
        # "Password login" and "Email password reset links" share a lot, too.
        close.add((firsts[2], related[0]))
        everyone = {*firsts, *seconds, *related}
        unrelated = {(a, b) for a in firsts for b in [*seconds, *related]} - duplicates - close
        flagged = {pair for pair in pairs if pair[0] in everyone and pair[1] in everyone}
        print(
            f"\noverlap finder at the default threshold {threshold}: "
            f"{len(duplicates & flagged)} of {len(duplicates)} reworded duplicates flagged, "
            f"{len(close & flagged)} of {len(close)} related but different flagged, "
            f"{len(unrelated & flagged)} of {len(unrelated)} unrelated pairs flagged"
        )
        scored = [pairs[p] for p in sorted(duplicates & flagged, key=by_number)]
        if scored:
            print(
                f"  flagged duplicates scored {min(scored):.2f} to {max(scored):.2f}, "
                f"mean {statistics.mean(scored):.2f}"
            )
        titles = {ref: (await call(client, "get_node", node=ref))["title"] for ref in everyone}
        for first, second in sorted(duplicates - flagged, key=by_number):
            print(f"  missed: {titles[first]!r} and {titles[second]!r}")
        for first, second in sorted(unrelated & flagged, key=by_number):
            print(
                f"  flagged, though not a duplicate: {titles[first]!r} and {titles[second]!r} "
                f"({pairs[(first, second)]:.2f})"
            )

        print("\nsearch by meaning (what a person might type):")
        for query, target in QUERIES:
            answer = await call(client, "search", query=query, mode="meaning", limit=100)
            ranked = [item["ref"] for item in answer["items"] if item["ref"] in everyone]
            wanted = firsts[target]
            place = ranked.index(wanted) + 1 if wanted in ranked else None
            score = next((i["similarity"] for i in answer["items"] if i["ref"] == wanted), None)
            print(f"  {query!r}: its story is result {place} with score {score}")

        phrase = await call(client, "search", query='"Role based access"', mode="words")
        print(
            f"\nexact words still work: the phrase finds {phrase['total']} item(s), labelled "
            f"{[i['matched'] for i in phrase['items']]}"
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: uv run python scripts/real_embeddings_check.py <MCP URL>")
    asyncio.run(main(sys.argv[1]))
