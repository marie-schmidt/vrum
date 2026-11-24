"""Bring GitHub issues into a running Vrum: read a JSON list of issues and make their epics, stories
and tasks as one plan. All of it is created or none of it: a file that does not fit is refused with
every problem listed.

    gh issue list --repo OWNER/REPO --state all --limit 1000 \\
        --json number,title,body,state,stateReason,labels,milestone,url > issues.json
    VRUM_AGENT_KEY=<the agent key of the project> uv run python scripts/import_github.py \\
        http://127.0.0.1:8000/mcp issues.json

The REST API's list (`GET /repos/OWNER/REPO/issues?state=all`) works too; pull requests in it are
left out. A milestone is an epic and an issue is a story under its milestone (or under an epic
called Inbox). An issue whose body has a task list of `- [ ] #12` makes those issues its tasks. A
closed issue is done, or dropped when it was closed as not planned; labels that say urgent, high or
low set the priority; the issue's own text, its labels and its address are kept in the details.
Nothing is kept in step afterwards: this is one way in, and Vrum never talks to GitHub. Add
--dry-run to see what would be created. The key must be allowed create_plan and update_nodes."""

import argparse
import sys
from pathlib import Path

from vrum_client import run_import

from vrum.importers import from_github_issues  # noqa: E402, I001


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="import_github.py", description="Import GitHub issues.")
    parser.add_argument("url", help="the server's MCP address, ending in /mcp")
    parser.add_argument("file", type=Path, help="the JSON list of issues")
    parser.add_argument(
        "--dry-run", action="store_true", help="say what would be created, create nothing"
    )
    given = parser.parse_args(argv)
    return run_import(from_github_issues, given.url, given.file, dry_run=given.dry_run)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
