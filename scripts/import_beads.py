"""Bring a Beads project into a running Vrum: read a `bd export` file (one JSON issue per line) and
make its epics, stories and tasks, its dependencies as links and its acceptance criteria, as one
plan. All of it is created or none of it: a file that does not fit is refused with every problem
listed.

    bd export > beads-export.jsonl
    VRUM_AGENT_KEY=<the agent key of the project> uv run python scripts/import_beads.py \\
        http://127.0.0.1:8000/mcp beads-export.jsonl

An epic or milestone is an epic, what is under it a story, and what is under that a task; an issue
with no epic goes under an epic called Inbox. A `blocks` dependency becomes needs_first, `related`
relates_to, `discovered-from` discovered_from, and the old id is kept in the details. Statuses and
priorities carry over. Nothing is kept in step afterwards: this is one way in. Add --skip-not-work
to leave out messages, events, molecules and gates, which are not work; --dry-run says what would
be created and creates nothing. The key must be allowed create_plan and update_nodes."""

import argparse
import sys
from pathlib import Path

from vrum_client import run_import

from vrum.importers import from_beads  # noqa: E402, I001


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="import_beads.py", description="Import a Beads export.")
    parser.add_argument("url", help="the server's MCP address, ending in /mcp")
    parser.add_argument("file", type=Path, help="the file `bd export` wrote")
    parser.add_argument(
        "--skip-not-work", action="store_true", help="leave out messages, events and gates"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="say what would be created, create nothing"
    )
    given = parser.parse_args(argv)
    return run_import(
        lambda text: from_beads(text, skip_not_work=given.skip_not_work),
        given.url,
        given.file,
        dry_run=given.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
