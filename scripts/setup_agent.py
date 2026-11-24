"""Set up a coding agent to use a Vrum project: write the client's MCP configuration and a short
guide for the agent, in the project folder it works in.

    uv run python scripts/setup_agent.py claude http://127.0.0.1:8000/mcp
    uv run python scripts/setup_agent.py codex  http://127.0.0.1:8000/mcp --dir ~/work/my-project
    uv run python scripts/setup_agent.py cursor http://127.0.0.1:8000/mcp --check

The clients are claude (Claude Code), codex (Codex), cursor (Cursor) and copilot (GitHub Copilot
CLI and VS Code). Nothing it writes holds the agent key: each configuration says to read it from
the environment variable VRUM_AGENT_KEY (the owner makes the key on the project's board, Users,
then Agents), so the files are safe to commit and the key stays where only you can read it. Set
the variable before starting the client:

    export VRUM_AGENT_KEY=<the key from the Users page>

An existing configuration file is kept: the vrum entry is added to it (and replaced if it was
there), and a file that is not valid JSON is left alone and refused. The guide is a block between
markers in the file the client reads (CLAUDE.md, AGENTS.md or .github/copilot-instructions.md): it
is the server's own agent guide, and running this again replaces only that block. With --check and
VRUM_AGENT_KEY set, it also connects with the key and says which tools the key is offered."""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from vrum_client import connect

from vrum.server import INSTRUCTIONS  # noqa: E402, I001

START = "<!-- vrum-guide:start -->"
END = "<!-- vrum-guide:end -->"
CLIENTS = ("claude", "codex", "cursor", "copilot")
# The file each client reads its guide from, and the file its configuration is in.
GUIDE_FILE = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "cursor": "AGENTS.md",
    "copilot": ".github/copilot-instructions.md",
}
CONFIG_FILE = {
    "claude": ".mcp.json",
    "codex": ".codex/config.toml",
    "cursor": ".cursor/mcp.json",
    "copilot": ".mcp.json",
}


def check_url(url: str) -> str:
    """The server's MCP address, or a refusal: it has to be http or https, name a host, end in /mcp
    and hold no login and no key."""
    cleaned = url.strip()
    found = re.fullmatch(r"(https?)://([^\s/@?#]+)(/[^\s?#]*)?", cleaned)
    if found is None or not (found[3] or "").rstrip("/").endswith("/mcp"):
        raise SystemExit(
            "Give the server's MCP address, such as http://127.0.0.1:8000/mcp: http or https, a "
            "host, no login in it, and ending in /mcp."
        )
    if "vrk_" in cleaned:
        raise SystemExit("The address holds what looks like an agent key. Leave the key out of it.")
    return cleaned


def json_entry(client: str, url: str) -> dict[str, Any]:
    """The `vrum` server entry of a client that keeps its servers as JSON. The key is read from the
    environment by the client, never written."""
    if client == "cursor":
        return {"url": url, "headers": {"Authorization": "Bearer ${env:VRUM_AGENT_KEY}"}}
    entry: dict[str, Any] = {
        "type": "http",
        "url": url,
        "headers": {"Authorization": "Bearer ${VRUM_AGENT_KEY}"},
    }
    if client == "copilot":
        entry["tools"] = ["*"]
    return entry


def merged_json(existing: str | None, client: str, url: str) -> str:
    """The configuration file with the vrum entry in it and everything else left as it was."""
    document: dict[str, Any] = {}
    if existing is not None and existing.strip():
        try:
            document = json.loads(existing)
        except ValueError:
            raise SystemExit(
                f"{CONFIG_FILE[client]} is not valid JSON, so it was left alone. Fix or move it, "
                "and run this again."
            ) from None
        if not isinstance(document, dict) or not isinstance(document.get("mcpServers", {}), dict):
            raise SystemExit(
                f"{CONFIG_FILE[client]} has no mcpServers object, so it was left alone."
            )
    servers = dict(document.get("mcpServers", {}))
    servers["vrum"] = json_entry(client, url)
    document["mcpServers"] = servers
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def codex_toml(existing: str | None, url: str) -> str:
    """Codex's configuration with a [mcp_servers.vrum] table added (it reads the key from the
    environment variable named in bearer_token_env_var). A table that is already there is left as
    it is: it is the person's."""
    table = f'[mcp_servers.vrum]\nurl = "{url}"\nbearer_token_env_var = "VRUM_AGENT_KEY"\n'
    if existing is None or not existing.strip():
        return table
    if re.search(r"^\[mcp_servers\.vrum\]", existing, re.M):
        raise SystemExit(
            f"{CONFIG_FILE['codex']} already has a [mcp_servers.vrum] table, so it was left "
            "alone. Edit it by hand if the address changed."
        )
    return existing.rstrip("\n") + "\n\n" + table


def guide_block() -> str:
    return f"{START}\n## Vrum\n\n{INSTRUCTIONS.strip()}\n{END}\n"


def with_guide(existing: str | None) -> str:
    """The guide file with Vrum's block in it: replaced when it is already there, added at the end
    otherwise, and the rest untouched."""
    block = guide_block()
    if existing is None or not existing.strip():
        return block
    if START in existing and END in existing:
        before, _, rest = existing.partition(START)
        _, _, after = rest.partition(END)
        return before + block.rstrip("\n") + after
    return existing.rstrip("\n") + "\n\n" + block


def files_for(client: str, url: str, root: Path) -> dict[Path, str]:
    """Every file to write, by path, with the content it will have."""
    config = root / CONFIG_FILE[client]
    guide = root / GUIDE_FILE[client]
    current = config.read_text(encoding="utf-8") if config.exists() else None
    text = codex_toml(current, url) if client == "codex" else merged_json(current, client, url)
    old_guide = guide.read_text(encoding="utf-8") if guide.exists() else None
    return {config: text, guide: with_guide(old_guide)}


async def offered_tools(url: str) -> list[str]:
    async with connect(url) as client:
        return sorted(tool.name for tool in (await client.list_tools()).tools)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="setup_agent.py", description="Write a coding agent's Vrum configuration and guide."
    )
    parser.add_argument("client", choices=CLIENTS, help="the coding agent to set up")
    parser.add_argument("url", help="the server's MCP address, ending in /mcp")
    parser.add_argument("--dir", type=Path, default=Path.cwd(), help="the project folder")
    parser.add_argument(
        "--check", action="store_true", help="connect with VRUM_AGENT_KEY and list the tools"
    )
    given = parser.parse_args(argv)
    url = check_url(given.url)
    root = given.dir.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"{root} is not a folder.")
    written = files_for(given.client, url, root)
    for path, text in written.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"Wrote {path}")
    print("The agent key is not in any of it. Before you start the client, run:")
    print("    export VRUM_AGENT_KEY=<the key from the project's Users page>")
    if given.check:
        tools = asyncio.run(offered_tools(url))
        print(f"The key is accepted and offered {len(tools)} tools: {', '.join(tools)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
