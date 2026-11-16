"""Where on the board a person can do what each MCP tool does, or why a tool is for agents only.

The Tools page of the board is made from the server's own live tool list, so it can never name a
tool that does not exist or leave one out. This map adds what the list cannot know: the area a tool
belongs to and where on the board the same thing is done. A test fails if a tool the server has is
neither here with a place on the board nor here marked as for agents only, and if a tool the board
calls is marked as for agents only.

A place on the board is a page (board, graph, roadmap, access, tools or status) or one of the two
drawers that open over any page (search or activity)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OnTheBoard:
    """A tool a person can use on the board: the page or drawer, and in words where on it."""

    page: str
    where: str


@dataclass(frozen=True)
class AgentsOnly:
    """A tool that only agents use, and why."""

    why: str


@dataclass(frozen=True)
class ToolPlace:
    area: str
    place: OnTheBoard | AgentsOnly


def _board(area: str, page: str, where: str) -> ToolPlace:
    return ToolPlace(area, OnTheBoard(page, where))


def _agents(area: str, why: str) -> ToolPlace:
    return ToolPlace(area, AgentsOnly(why))


AREAS = (
    "Server",
    "Work",
    "Links",
    "Claims and the working loop",
    "Requirements",
    "Decisions",
    "Facts",
    "Search and the index",
    "Moving data",
)

TOOL_PLACES: dict[str, ToolPlace] = {
    "server_status": _board(
        "Server", "status", "The Status and export page shows everything it returns."
    ),
    "create_node": _board(
        "Work", "board", "New item on the Board, or Add story on an epic's lane, or Add task."
    ),
    "get_node": _board("Work", "board", "Select any item: its detail panel shows all of it."),
    "list_nodes": _board(
        "Work", "board", "The Board, the Graph and the Roadmap, with the same filter language."
    ),
    "update_node": _board(
        "Work",
        "board",
        "Select a card and change its title, details, status, priority or dates in the detail "
        "panel; drag a card on the Board or a bar on the Roadmap.",
    ),
    "update_nodes": _board(
        "Work", "roadmap", "Choose several bars on the Roadmap and move them together."
    ),
    "move_node": _board("Work", "board", "Move, in the detail panel of a story or a task."),
    "node_history": _board("Work", "board", "History, in the detail panel of an item."),
    "add_link": _board(
        "Links",
        "board",
        "Links, in an item's detail panel; Mark as duplicates or Mark as related on a pair in "
        "the search drawer's Possible duplicates.",
    ),
    "remove_link": _board("Links", "board", "Links, in an item's detail panel."),
    "list_links": _board(
        "Links", "graph", "The Graph draws the links; an item's detail panel lists them."
    ),
    "ready_work": _board(
        "Claims and the working loop",
        "activity",
        "Free to take, in the Activity drawer; the Board counts the ready work nobody holds.",
    ),
    "claim_node": _board(
        "Claims and the working loop", "activity", "Claim, in the Activity drawer."
    ),
    "renew_claim": _board(
        "Claims and the working loop", "activity", "Renew, in the Activity drawer."
    ),
    "release_claim": _board(
        "Claims and the working loop", "activity", "Release, in the Activity drawer."
    ),
    "who_is_working": _board(
        "Claims and the working loop",
        "activity",
        "Who is working on what, at the top of the Activity drawer.",
    ),
    "next_work": _agents(
        "Claims and the working loop",
        "It picks the most urgent free item, claims it and hands over everything an agent needs "
        "in one step, for an agent that has no work in mind. A person picks work on the Board "
        "and claims it in the Activity drawer.",
    ),
    "finish_work": _agents(
        "Claims and the working loop",
        "It ends an agent's claim, sets where the work stands and leaves the handoff note in one "
        "step. A person changes the status in the detail panel, and releases a claim with a "
        "note in the Activity drawer.",
    ),
    "create_plan": _agents(
        "Work",
        "It creates a whole plan (items, links and requirements) as one all-or-nothing step from "
        "data an agent writes. A person makes items one at a time with New item.",
    ),
    "changes_since": _board(
        "Server", "activity", "The Activity drawer is the feed; the board also polls it."
    ),
    "create_rule": _board(
        "Requirements", "graph", "New requirement, in the Graph's context list or on a story."
    ),
    "get_rule": _board("Requirements", "graph", "A requirement's details, in the detail panel."),
    "list_rules": _board(
        "Requirements",
        "graph",
        "The Graph's context list shows every requirement, including those in no story.",
    ),
    "update_rule": _board("Requirements", "graph", "Edit, in a requirement's detail panel."),
    "place_rule": _board(
        "Requirements", "graph", "Put in a story, in the Graph's context list or the detail panel."
    ),
    "move_rule": _board(
        "Requirements", "graph", "Move to another story, in the Graph's context list."
    ),
    "rule_history": _board("Requirements", "graph", "History, in a requirement's detail panel."),
    "coverage_report": _board(
        "Requirements", "board", "Coverage on the Board; the strip above the cards counts it."
    ),
    "record_decision": _board(
        "Decisions", "graph", "Record a decision, in the Graph's context list or an item's panel."
    ),
    "get_decision": _board("Decisions", "graph", "A decision's details, in the detail panel."),
    "list_decisions": _board(
        "Decisions", "graph", "The Graph's context list shows every decision."
    ),
    "link_decision": _board(
        "Decisions", "graph", "What it concerns, in a decision's detail panel."
    ),
    "unlink_decision": _board(
        "Decisions", "graph", "What it concerns, in a decision's detail panel."
    ),
    "supersede_decision": _board(
        "Decisions", "graph", "Replace, in a decision's detail panel or the context list."
    ),
    "withdraw_decision": _board(
        "Decisions", "graph", "Withdraw, in a decision's detail panel or the context list."
    ),
    "decision_history": _board("Decisions", "graph", "History, in a decision's detail panel."),
    "recall": _board(
        "Decisions",
        "board",
        "The decisions and facts in an item's detail panel, with the reason each is shown.",
    ),
    "retain_fact": _board(
        "Facts", "graph", "Record a fact, in the Graph's context list or an item's panel."
    ),
    "get_fact": _board("Facts", "graph", "A fact's details, in the detail panel."),
    "list_facts": _board("Facts", "graph", "The Graph's context list shows every fact."),
    "link_fact": _board("Facts", "graph", "What it concerns, in a fact's detail panel."),
    "unlink_fact": _board("Facts", "graph", "What it concerns, in a fact's detail panel."),
    "supersede_fact": _board(
        "Facts", "graph", "Replace, in a fact's detail panel or the context list."
    ),
    "withdraw_fact": _board(
        "Facts", "graph", "Withdraw, in a fact's detail panel or the context list."
    ),
    "fact_history": _board("Facts", "graph", "History, in a fact's detail panel."),
    "set_confirmation": _board(
        "Facts",
        "graph",
        "The Unconfirmed / Confirmed choice on a fact or a decision, in its detail panel or the "
        "context list.",
    ),
    "search": _board("Search and the index", "search", "The search box, at the top of every view."),
    "similar_items": _board(
        "Search and the index",
        "board",
        "Might be the same as, in the detail panel of an epic or a story.",
    ),
    "find_overlaps": _board(
        "Search and the index", "search", "Possible duplicates, in the search drawer."
    ),
    "find_mentions": _board(
        "Search and the index",
        "board",
        "Mentioned in, in the detail panel of an item, a requirement, a decision or a fact.",
    ),
    "reindex": _agents(
        "Search and the index",
        "It rebuilds the index of meanings from the embedding service. The server already does "
        "that by itself within seconds, and the Status and export page shows how it went, so "
        "running it by hand is a job for an agent or the command line.",
    ),
    "export_graph": _board(
        "Moving data", "status", "Download the full export, on the Status and export page."
    ),
    "import_graph": _agents(
        "Moving data",
        "It loads a whole export into an empty project, which is done once to set one up, so it "
        "stays with the command line (scripts/import_graph.py) and docs/backup-and-export.md.",
    ),
}
