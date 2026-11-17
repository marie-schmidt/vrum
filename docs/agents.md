# Connecting an agent

Any MCP client that speaks streamable HTTP can use Vrum. MCP (the Model Context Protocol) is the
standard way for AI coding tools to call a service. A client needs two things: the address of the
server and the key of one project.

- The address is `http://127.0.0.1:8000/mcp`. It is on the same port as the board. Change the port
  with `VRUM_PORT` in `.env` (see [configuration](configuration.md)).
- The key belongs to one project. It reaches that project and no other.

## Make a key

1. Open the project's board and choose **Settings & tools**, then **Agent access**, in the sidebar.
2. Give the key a label, such as the name of the agent that will use it, and choose **Create key**.
3. **Copy the key now.** It is shown once. Vrum keeps only a hash of it, so a lost key cannot be
   shown again: revoke it and make another.

Make one key for each agent, so that you can revoke just that one. Revoking works on the agent's
next request, even on a connection that is already open. An agent's name is only what its changes
are recorded with. It proves nothing and opens nothing: the key is what lets it in.

Keep keys out of files you commit. Every example below reads the key from the `VRUM_AGENT_KEY`
environment variable.

## Configure a client

**Claude Code** keeps the key in its own settings, so do this where only you can read them:

```
export VRUM_AGENT_KEY=<the key from the Agent access page>
claude mcp add --transport http vrum http://127.0.0.1:8000/mcp --header "Authorization: Bearer $VRUM_AGENT_KEY"
```

**A project file** that any of the clients below can read, as `.mcp.json` at the root of the
project. It holds no key: each client reads `VRUM_AGENT_KEY` from the environment.

```json
{
  "mcpServers": {
    "vrum": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer ${VRUM_AGENT_KEY}" }
    }
  }
}
```

**GitHub Copilot** (the CLI and VS Code) reads the same `mcpServers` shape from
`~/.copilot/mcp-config.json` (for all your projects) or from `.mcp.json` in the workspace, with an
added list of the tools to allow:

```json
{
  "mcpServers": {
    "vrum": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer ${VRUM_AGENT_KEY}" },
      "tools": ["*"]
    }
  }
}
```

**From Python**, with the official MCP SDK (this is what the scripts in `scripts/` and the checks
use; see `scripts/vrum_client.py`):

```python
import asyncio, os
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client


async def main() -> None:
    headers = {"Authorization": f"Bearer {os.environ['VRUM_AGENT_KEY']}"}
    async with (
        create_mcp_http_client(headers=headers) as http,
        Client(streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http)) as client,
    ):
        print(await client.call_tool("server_status", {}))


asyncio.run(main())
```

The Python example is run against a real server by `./acceptance.sh`, so it works. The other three
follow each client's own documentation for remote HTTP servers (Claude Code: "Connect Claude Code to
tools via MCP"; Copilot and VS Code: "MCP configuration reference"); the checks do not run them. If
a client does not fill in `${VRUM_AGENT_KEY}` for you, put the key in that client's own user-level
settings rather than in a file you commit.

If a client says it cannot connect, the server's answer says why: a missing, wrong or revoked key
is refused in plain words. Passwords and keys travel as plain text over HTTP, so use this on one
machine, or put Vrum behind something that adds TLS (see the limitations in the
[README](../README.md#limitations)).

## Try it

Ask the agent to call `server_status`, then to take the next piece of work with `next_work`. The
task it takes shows as held on the Board, and the claim and the handoff note it leaves when it
finishes appear in the **Activity** drawer. Every tool describes itself: its description says what
it does, what it needs and what it refuses, and the client lists them all. The board's **Tools**
page (also under **Settings & tools**) shows the same list, read from the server.

## The agent guide

This is the text the server hands to every agent when it connects (its MCP instructions). It is
the only copy in the documentation, and a check fails if it ever differs from the server's text.

<!-- agent-guide:start -->
Vrum is a project graph. Epics hold stories, stories hold tasks, and requirements, decisions and facts are linked to them. An epic, story or task is named by its kind and its number within that kind: `epic:3`, `story:12`, `task:40` (each kind is numbered from 1 on its own, so epic:3, story:3 and task:3 are three items). Every tool that names one takes and returns that reference, never a bare number. A decision is what a named person decided; a fact is a statement with its source. Each is either unconfirmed or confirmed by a named person, and either in force or superseded or withdrawn: read both before you rely on it. Every write needs an actor: your own name.

The working loop:
1. `next_work` takes the most urgent free story or task and returns it with its requirements, decisions (project-wide ones included), facts, earlier handoff notes and similar items, all in one call. Keep the claim with `renew_claim` while you work.
2. Before you decide something that matters, `recall` what is known about the item. If you do not know where to look, use `search`.
3. Record what is decided as it happens with `record_decision` (name the person who decided in decided_by), and what you learn with `retain_fact`. What you record starts unconfirmed: only a named person's review makes it confirmed (`set_confirmation`).
4. End with `finish_work`: say whether it is done, blocked, dropped or released, and leave a handoff note for whoever comes next. A blank note is refused.

Also: `create_plan` creates a whole plan at once or nothing (its items are named by keys that are not references, and it refers to existing items by reference), `update_nodes` changes many nodes, `changes_since` shows only what is new since an event, and `ready_work` lists what is free. `list_nodes` takes a filter text as its query argument, such as `kind:task status:ready -claimed OR is:overdue` or `epic:3` for epic 3 and everything under it. `[[story:12]]` in any text refers to an item (also `[[epic:3]]`, `[[task:40]]`, `[[rule:3]]`, `[[decision:7]]`, `[[fact:5]]`); `find_mentions` lists who refers to one. Lists leave out bodies unless you ask for detail. Refusals say what is wrong and what to do next. Anything holding a well-known secret format is refused in every field and never stored: leave secrets out of what you write. Vrum stores, retrieves and coordinates; it does not generate answers: you do the reasoning and record what you find.
<!-- agent-guide:end -->
