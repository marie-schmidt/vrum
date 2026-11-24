"""Bring a Spec Kit `tasks.md` into a running Vrum: make the feature an epic, each phase a story and
each task line a task, with the dependencies it states, as one plan. All of it is created or none of
it: a file that does not fit is refused with every problem listed.

    VRUM_AGENT_KEY=<the agent key of the project> uv run python scripts/import_speckit.py \\
        http://127.0.0.1:8000/mcp specs/001-photo-albums/tasks.md

A task is a line such as `- [ ] T012 [P] [US1] Create the album model`; one marked `[x]` is done; a
`[US1]` label puts it under the phase whose heading says User Story 1; `(depends on T010, T011)`
makes it wait for those tasks. The task's id is kept in its details. Nothing is kept in step
afterwards: this is one way in. Add --dry-run to see what would be created. The key must be allowed
create_plan and update_nodes."""

import argparse
import sys
from pathlib import Path

from vrum_client import run_import

from vrum.importers import from_spec_kit  # noqa: E402, I001


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="import_speckit.py", description="Import a Spec Kit tasks.md."
    )
    parser.add_argument("url", help="the server's MCP address, ending in /mcp")
    parser.add_argument("file", type=Path, help="the tasks.md file")
    parser.add_argument(
        "--dry-run", action="store_true", help="say what would be created, create nothing"
    )
    given = parser.parse_args(argv)
    return run_import(from_spec_kit, given.url, given.file, dry_run=given.dry_run)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
