"""How the scripts in this folder connect to a running Vrum: with the agent key of one project, read
from the VRUM_AGENT_KEY environment variable. The project's owner makes the key on the board
(Users, then Agents, on the project's page), and names it for the agent that will use it: that
name is who the scripts are in the project's history. A key reaches that project and no other. The
export and import scripts also need the key to be allowed `export_graph` and `import_graph`, which
are not among the tools agents are offered by default (see docs/backup-and-export.md)."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import TextContent

# So that the scripts run from a checkout whether or not the project is installed in the
# environment (an editable install is silently skipped by some Python setups).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vrum.exchange import FORMAT, FORMAT_VERSION, SECTIONS  # noqa: E402
from vrum.importers import ImportProblems  # noqa: E402


@asynccontextmanager
async def connect(url: str) -> AsyncIterator[Client]:
    key = os.environ.get("VRUM_AGENT_KEY", "").strip()
    if not key:
        raise SystemExit(
            "VRUM_AGENT_KEY is not set. Every request to the server needs the agent key of a "
            "project: make one on the project's board (Users, then Agents), then run "
            "`export VRUM_AGENT_KEY=<the key>` first."
        )
    async with (
        create_mcp_http_client(headers={"Authorization": f"Bearer {key}"}) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        yield client


def said(result: Any) -> str:
    """What a tool's answer says, as text."""
    return " ".join(block.text for block in result.content if isinstance(block, TextContent))


async def fetch_export(client: Client) -> dict[str, Any]:
    """Every page of export_graph, put together as one file. It stops if the graph changed while it
    was being read (what was read would not fit together), saying so."""
    document: dict[str, Any] = {"format": FORMAT, "version": FORMAT_VERSION, "counts": None}
    for section in SECTIONS:
        document[section] = []
    after: str | None = None
    while True:
        result = await client.call_tool("export_graph", {"after": after, "limit": 200})
        if result.is_error or result.structured_content is None:
            raise SystemExit(f"The server refused to export: {said(result)}")
        page = result.structured_content
        if page["counts"] is not None:
            document["counts"] = page["counts"]
        if page["section"] is not None:
            document[page["section"]].extend(page["items"])
        after = page["next_after"]
        if after is None:
            break
    for section in SECTIONS:
        if len(document[section]) != document["counts"][section]:
            raise SystemExit(
                f"The graph changed while it was being read ({section}: "
                f"{document['counts'][section]} at the start, {len(document[section])} read). "
                "Run it again when nothing is writing."
            )
    return document


async def send_plan(url: str, imported: Any) -> int:
    """Create what an importer read (vrum.importers.Imported) in a running Vrum as one plan, then
    give each item the status it had. The plan is all or nothing: when the server refuses it, it
    says why and nothing was created. A status the server refuses (an agent may not set `done`
    while the definition of done has an unticked check) is reported, and the item stays as it was
    created. Returns the exit status: 0, or 1 when the plan was refused."""
    async with connect(url) as client:
        made = await client.call_tool(
            "create_plan",
            {"items": imported.items, "links": imported.links, "rules": imported.rules},
        )
        if made.is_error or made.structured_content is None:
            print(said(made), file=sys.stderr)
            return 1
        refs = {c["key"]: c["ref"] for c in made.structured_content["created"]}
        changes = [
            {"node": refs[key], "expected_version": 1, "status": status}
            for key, status in imported.statuses.items()
        ]
        refused: list[str] = []
        for start in range(0, len(changes), 100):
            result = await client.call_tool(
                "update_nodes", {"changes": changes[start : start + 100]}
            )
            if result.is_error or result.structured_content is None:
                refused.append(said(result))
                continue
            refused.extend(
                f"{one['node']}: {one['refusal']}"
                for one in result.structured_content["results"]
                if not one["applied"]
            )
    counts = ", ".join(f"{count} {name}" for name, count in imported.counts().items() if count)
    print(f"Created {counts} from {imported.source}.")
    for note in imported.left_out:
        print(note)
    if refused:
        many = "1 status" if len(refused) == 1 else f"{len(refused)} statuses"
        print(f"{many} could not be set, so those items start as not started:")
        for line in refused[:10]:
            print(f"  {line}")
    return 0


def run_import(
    reader: Callable[[str], Any], url: str, source: Path, *, dry_run: bool = False
) -> int:
    """What an importer script does: read the file with `reader`, say every problem when it does
    not fit (and change nothing), and otherwise create it in the project with `send_plan` (or, with
    `dry_run`, only say what would be created). Returns the exit status."""
    try:
        imported = reader(source.read_text(encoding="utf-8"))
    except ImportProblems as refused:
        print(
            f"Nothing was created: the file has {len(refused.problems)} problems.", file=sys.stderr
        )
        for number, problem in enumerate(refused.problems, start=1):
            print(f"{number}. {problem}", file=sys.stderr)
        return 1
    if dry_run:
        counts = ", ".join(f"{count} {name}" for name, count in imported.counts().items() if count)
        print(f"The file fits. It would create {counts}. Nothing was created.")
        for note in imported.left_out:
            print(note)
        return 0
    return asyncio.run(send_plan(url, imported))
