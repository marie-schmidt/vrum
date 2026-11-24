"""./acceptance.sh runs this: the whole acceptance scenario, chapter by chapter, through a real MCP
client and a real headless browser against the real containers (see tests/acceptance_scenario.py
for what it is).

    uv run python -m tests.acceptance                 every chapter, in order
    uv run python -m tests.acceptance finding board   only these (each needs a stack that has what
                                                      its chapter builds on: a fresh one is safest)

The first proof that does not hold stops it with a plain message and exit code 1."""

import asyncio
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable

from playwright.async_api import expect

from tests.acceptance_accounts import accounts
from tests.acceptance_appearance import appearance
from tests.acceptance_assist import assist
from tests.acceptance_board import board
from tests.acceptance_context import context
from tests.acceptance_demo import demo
from tests.acceptance_finding import finding
from tests.acceptance_gates import discovered, gates
from tests.acceptance_graph import graph
from tests.acceptance_hardening import hardening
from tests.acceptance_moving import moving
from tests.acceptance_news import the_news_arrives
from tests.acceptance_numbering import numbering
from tests.acceptance_pages import pages
from tests.acceptance_quickstart import quickstart
from tests.acceptance_review import review
from tests.acceptance_revising import revising
from tests.acceptance_scenario import (
    PROOFS,
    Failed,
    arriving,
    leaves,
    planning,
    remembering,
    requirements_and_decisions,
    working_together,
)
from tests.acceptance_search import searching
from tests.acceptance_sorting import sorting
from tests.acceptance_standard import standard
from tests.acceptance_utilities import utilities
from tests.acceptance_ways_in import ways_in
from tests.board_helpers import SLOW


async def working() -> None:
    """Working together, and then what is sent when work needs someone (M48: webhooks and email)."""
    await working_together()
    await the_news_arrives()


# `moving` is last: it destroys the database it started with. `demo` runs ./demo.sh, which is a
# separate stack of its own, and so needs nothing from the walk's stack.
CHAPTERS: dict[str, Callable[[], Awaitable[None]]] = {
    "arriving": arriving,
    "planning": planning,
    "working": working,
    "decisions": requirements_and_decisions,
    "remembering": remembering,
    "finding": finding,
    "hardening": hardening,
    "board": board,
    "graph": graph,
    "context": context,
    "searching": searching,
    "utilities": utilities,
    "pages": pages,
    "accounts": accounts,
    "appearance": appearance,
    "quickstart": quickstart,
    "numbering": numbering,
    "ways_in": ways_in,
    "discovered": discovered,
    "sorting": sorting,
    "gates": gates,
    "review": review,
    "assist": assist,
    "revising": revising,
    "standard": standard,
    "demo": demo,
    "moving": moving,
}


async def main(only: list[str]) -> None:
    unknown = [name for name in only if name not in CHAPTERS]
    if unknown:
        raise SystemExit(
            f"No chapter called {unknown[0]}. The chapters are: {', '.join(CHAPTERS)}."
        )
    # The shared project grows through the walk: a page of it may take longer to draw than the
    # five seconds a browser check waits by default.
    expect.set_options(timeout=SLOW)
    started = time.monotonic()
    for name, run in CHAPTERS.items():
        if not only or name in only:
            await run()
    by_milestone = Counter(milestone for milestone, _ in PROOFS)

    def order(milestone: str) -> tuple[int, str]:
        """M2 before M10, and M1A right after M1."""
        number, suffix = re.fullmatch(r"M(\d+)(\w*)", milestone).groups()  # type: ignore[union-attr]
        return int(number), suffix

    listed = ", ".join(
        f"{m} {n}" for m, n in sorted(by_milestone.items(), key=lambda x: order(x[0]))
    )
    what = "ACCEPTANCE PASSED" if not only else f"CHAPTERS PASSED ({', '.join(only)})"
    print(
        f"\n{what}: {len(PROOFS)} proofs in {time.monotonic() - started:.0f} seconds ({listed})."
        + (" M8 is skipped: no ledger path was given." if not only else ""),
        flush=True,
    )


if __name__ == "__main__":
    for needed in ("VRUM_TEST_URL", "COMPOSE_PROJECT_NAME"):
        if not os.environ.get(needed):
            raise SystemExit(f"{needed} is not set. Run this through ./acceptance.sh.")
    try:
        asyncio.run(main(sys.argv[1:]))
    except BaseException as failure:
        # A failure inside a client's block arrives wrapped in groups: find the proof that failed.
        found = [leaf for leaf in leaves(failure) if isinstance(leaf, Failed | AssertionError)]
        if not found:
            raise
        print(f"\nACCEPTANCE FAILED: {found[0]}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
