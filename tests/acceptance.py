"""./acceptance.sh runs this: the whole acceptance scenario, chapter by chapter, through a real MCP
client against the real containers (see tests/acceptance_scenario.py for what it is).

    uv run python -m tests.acceptance                 every chapter, in order
    uv run python -m tests.acceptance finding model   only these (each needs a stack that has what
                                                      its chapter builds on: a fresh one is safest)

The first proof that does not hold stops it with a plain message and exit code 1."""

import asyncio
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable

from tests.acceptance_finding import finding
from tests.acceptance_model import hardening, model_help
from tests.acceptance_moving import moving
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

CHAPTERS: dict[str, Callable[[], Awaitable[None]]] = {
    "arriving": arriving,
    "planning": planning,
    "working": working_together,
    "decisions": requirements_and_decisions,
    "remembering": remembering,
    "finding": finding,
    "model": model_help,
    "hardening": hardening,
    "moving": moving,
}


async def main(only: list[str]) -> None:
    unknown = [name for name in only if name not in CHAPTERS]
    if unknown:
        raise SystemExit(
            f"No chapter called {unknown[0]}. The chapters are: {', '.join(CHAPTERS)}."
        )
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
    for needed in ("VRUM_TEST_URL", "VRUM_TOKEN", "COMPOSE_PROJECT_NAME"):
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
