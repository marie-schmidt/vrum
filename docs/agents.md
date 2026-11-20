# Connecting an agent

Any MCP client that speaks streamable HTTP can use Vrum. MCP (the Model Context Protocol) is the
standard way for AI coding tools to call a service. A client needs two things: the address of the
server and the key of one project.

- The address is `http://127.0.0.1:8000/mcp`. It is on the same port as the board. Change the port
  with `VRUM_PORT` in `.env` (see [configuration](configuration.md)).
- The key belongs to one project. It reaches that project and no other.

## Make a key

1. Open the project's board and choose **Settings and tools**, then **Users**, in the sidebar. The
   keys are in the **Agents** section.
2. Name the key for the agent that will use it, such as `api-agent`, and choose what it may do
   (it starts as everything; see [What a key may do](#what-a-key-may-do)). Then choose **Make an
   agent key**.
3. **Copy the key now.** It is shown once. Vrum keeps only a hash of it, so a lost key cannot be
   shown again: revoke it and make another.

## Who the agent is

A key is an agent's identity. Vrum records everything that agent does (claims, changes, notes,
handoff notes, history) under the name of its key, and shows it on the board as an agent, next to
the people. The agent never says who it is: the tools take no name, and a name sent anyway is
ignored. So make one key for each agent: two agents that share a key are one agent in the history,
and no two keys of a project that are in force can have the same name. Revoking a key ends that
agent's access on its next request, even on a connection that is already open, and what it did
stays in the history under its name.

People are the other kind of identity: whoever is signed in on the board, shown by the name on
their account. Only a person can confirm or un-confirm a note: an agent's call to
`set_confirmation` is refused, so an agent that wants a note confirmed asks a person to do it on
the board. A note that names a person other than the one making the call (who decided it, who
changed their mind, who reviewed it) still takes that person's typed name, because they
may have no account, such as a client or an officer.

Keep keys out of files you commit. Every example below reads the key from the `VRUM_AGENT_KEY`
environment variable.

## What a key may do

Vrum has more tools than an agent needs, and an MCP client allows only so many (Cursor, for one,
caps its tools at 40 across all the servers it is connected to). So an agent key is offered a
chosen set of at most 25 tools unless its owner chose otherwise: the work loop (`next_work`,
`claim_node`, `renew_claim`, `set_check`, `finish_work`, `changes_since`), reading and changing
items and plans,
links, acceptance criteria, notes, `recall` and `search`. Everything else is done by a person on the board:
histories, moving an item, confirming a note, who is working on what, the coverage report, setting
the project's definition of done, and moving data (export, import and rebuilding the index). The board's **Tools** page marks
which tools an agent is offered.

On the Users page the owner can narrow a key further when making it, and change it later: an agent
that only reviews can be given the tools that read, and one that does day-to-day work the tools for
work, claims, acceptance criteria and notes. The owner can also name tools outside the standard set for a
key, for example `export_graph` and `import_graph` for the key a backup script uses, or choose
**Everything, including administration**.

- The agent sees only what its key may use: its tool list holds just those tools, so it does not
  even know about the others. A call to a tool outside the list is refused in plain words, such as
  "This agent key (review-agent) is not allowed to use create_node. Ask the project's owner to
  change what the key may do, on the Users page. Nothing was changed.", and nothing is written. A
  standard key that asks for a tool outside the set is told that it is done on the board, by a
  person.
- A key with a list of its own may use exactly that list. A tool added to Vrum later is not in it,
  so such a key never gains a power by an upgrade. A key made without a choice may use the
  standard set, which can grow with new agent tools, but never beyond 25.
- It is checked on every request, as the key itself is: when the owner changes what a key may do,
  it applies from that agent's next request, even on a connection that is already open.
- A tool that only a person can use, `set_confirmation`, cannot be given to any key.
- Only the project's owner changes it. The board's own calls are a person's and are limited by
  their role in the project, never by a key.

## Sizes and checks

**Size.** Every story and task can have a size, `xs`, `s`, `m`, `l` or `xl`: how much effort it takes
and how much context an agent needs for it, together (1, 2, 3, 5 and 8 points). An agent that plans
gives each one a size (`create_plan` and `create_node` take a size, `update_node` changes it or
clears it) and splits anything bigger than `xl`. An epic has no size: it adds up what
is in it. The filter language has `size:m,l` and `no:size`.

**Checks.** The list lines of the project's definition of done (each starting with a dash) are its
checks, which a person writes on the board under Settings. Each story or task has each check ticked
or not. Before an agent finishes a task as done it does what each check says, such as running the
tests, and ticks it with `set_check`, saying in the evidence how it checked. `next_work` hands over
the checks of the item with its briefing and `get_node` lists them with who
ticked each, when and how. An agent that tries to set an item to done (with `finish_work` or
`update_node`) while a check is unticked is refused, and the refusal names the checks; it can still
release or block the work. A person is never refused: they decide, and they tick on the board.

A check that only a person can judge starts its words with `[person]`, as in
`- [person] Looks right on a phone`. An agent can neither tick nor untick it: `next_work` marks it
`person_only`, and the agent finishes the item as `blocked` with `finish_work`, saying in the
handoff note what the person should look at. The person ticks it on the board and marks the item
done. `update_node` takes `why` with a change of status to `blocked` or `dropped`, and keeps it as a
handoff note on the item.

## Configure a client

**Claude Code** keeps the key in its own settings, so do this where only you can read them:

```
export VRUM_AGENT_KEY=<the key from the Users page>
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
        print(await client.call_tool("list_nodes", {"limit": 1}))


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

Ask the agent to call `list_nodes`, then to take the next piece of work with `next_work`. The
task it takes shows as held on the Board, and the claim and the handoff note it leaves when it
finishes appear on the **Activity** page. Every tool describes itself: its description says what
it does, what it needs and what it refuses, and the client lists them all. The board's **Tools**
page (also under **Settings and tools**) shows the same list, read from the server.

## The agent guide

This is the text the server hands to every agent when it connects (its MCP instructions). It is
the only copy in the documentation, and a check fails if it ever differs from the server's text.

<!-- agent-guide:start -->
Vrum is a project graph. Epics hold stories and stories hold tasks; acceptance criteria and notes belong to or concern them. An epic, story or task is named by its kind and number: `epic:3`, `story:12`, `task:40`. Tools take that reference, never a bare number. A note records what is known or decided, with why. Unconfirmed and confirmed (a person reviewed it) notes are in force; superseded and withdrawn ones are not: check its status before relying on it. Your agent key names you: never send a name.

The working loop:
1. `next_work` takes the most urgent free story or task and returns it with its acceptance criteria, the definition of done and its checks, notes and handoffs. Keep the claim with `renew_claim`.
2. Before deciding something that matters, `recall` what is known, or `search`.
3. Record what is decided or learned with `add_note`: say why, and name the person in decided_by when someone decided it. It starts unconfirmed; only a person confirms it.
4. Before finishing, meet the acceptance criteria, do what each check says (run the tests) and `set_check` it, saying how you checked: done is refused while one is unticked. Leave a check marked person_only for a person: finish as blocked, saying what to look at. End with `finish_work`: done, blocked, dropped or released, and a handoff note.

Also: `create_plan` makes a whole plan at once or nothing; give every story and task a size, xs to xl (the effort and context it will take; split anything bigger). `changes_since` shows only what is new, and `list_nodes` takes a filter as its query, such as `kind:task status:ready -claimed OR is:overdue`. `[[story:12]]` in any text refers to an item (also `[[epic:3]]`, `[[task:40]]`, `[[rule:3]]`, `[[note:7]]`). A person does what is not offered to you on the board.
<!-- agent-guide:end -->
