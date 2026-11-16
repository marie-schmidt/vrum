"""Write a Markdown copy of the graph of a running Vrum into a folder: one file per epic, story,
task, requirement (a rule), decision and fact, with its fields at the top and its links written
as [[references]], to read in Obsidian or keep in git. A decision or fact says whether it still
counts and whether a person confirmed it.

    VRUM_AGENT_KEY=<the agent key of the project> uv run python scripts/export_markdown.py \\
        http://127.0.0.1:8000/mcp vrum-notes

The copy is read-only: it is made again from the graph each time. This replaces the folders epics,
stories, tasks, rules, decisions and facts inside the target folder and leaves anything
else in it alone. A target folder that has something else in it and was not made by this script is
refused, so that nothing of yours is overwritten. The same graph gives the same files, so a copy
kept in git only changes when the graph does."""

import asyncio
import shutil
import sys
from pathlib import Path

from vrum_client import connect, fetch_export

from vrum.markdown import FOLDERS, render

MARKER = ".vrum-export"
MARKER_TEXT = (
    "This folder holds a Markdown copy of a Vrum graph, written by scripts/export_markdown.py.\n"
    "The folders " + ", ".join(FOLDERS) + " are replaced each time it runs: do not edit them.\n"
    "Anything else in this folder is left alone.\n"
)


def prepare(target: Path) -> None:
    """Make the target ready to be written, or refuse if it holds something that is not ours."""
    if target.exists() and not target.is_dir():
        raise SystemExit(f"{target} is a file, not a folder.")
    if target.exists() and any(target.iterdir()) and not (target / MARKER).exists():
        raise SystemExit(
            f"{target} already has files in it that this script did not write, so nothing was "
            "done. Give it an empty folder, or a new name."
        )
    target.mkdir(parents=True, exist_ok=True)
    for folder in FOLDERS:
        shutil.rmtree(target / folder, ignore_errors=True)
    (target / MARKER).write_text(MARKER_TEXT, encoding="utf-8")


async def main(url: str, target: Path) -> None:
    async with connect(url) as client:
        document = await fetch_export(client)
    files = render(document)
    prepare(target)
    for path, text in files.items():
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    print(f"Wrote {len(files)} files in {target}.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: export_markdown.py <server url, ending in /mcp> <folder>")
    asyncio.run(main(sys.argv[1], Path(sys.argv[2])))
