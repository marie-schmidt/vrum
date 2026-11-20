"""The tools Vrum offers an agent: a small, chosen set, and the rest.

An MCP client lists every tool a server has, and the client's own cap decides how many it can use
at all (Cursor allows 40 across all the servers it is connected to, and tool choice gets worse as
the list grows). Vrum has more operations than that, because the board does things an agent seldom
needs (histories, moving data, who is working on what) and a few that only a person may do
(confirming a note). So an agent key is offered `AGENT_TOOLS` and nothing else, unless the owner
names other tools for that key on the Users page (the tools of that list are then exactly the
ones the key may use). The board calls every tool as the signed-in person and is not limited by
this.

Every tool the server has is either in this list or is reached from the board, and a test checks
that this list names only tools that exist, holds at most `MOST_AGENT_TOOLS`, holds nothing only a
person may use, and that no tool in it tells an agent to use one that is not."""

# The work loop first, then reading and changing work, links, acceptance criteria, notes, search.
AGENT_TOOLS: tuple[str, ...] = (
    "next_work",
    "claim_node",
    "renew_claim",
    "set_check",
    "finish_work",
    "changes_since",
    "create_node",
    "create_plan",
    "get_node",
    "list_nodes",
    "update_node",
    "add_link",
    "remove_link",
    "list_links",
    "create_rule",
    "list_rules",
    "update_rule",
    "place_rule",
    "add_note",
    "list_notes",
    "link_note",
    "supersede_note",
    "withdraw_note",
    "recall",
    "search",
)

# What an MCP client can be expected to take from one server: Cursor's cap is 40 tools across all
# servers, and an agent also has its own tools and other servers, so Vrum keeps to this.
MOST_AGENT_TOOLS = 25

AGENT_SURFACE = frozenset(AGENT_TOOLS)
