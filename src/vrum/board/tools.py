"""Where on the board a person can do what each MCP tool does, or why a tool is for agents only.

The Tools page of the board is made from the server's own live tool list, so it can never name a
tool that does not exist or leave one out. This map adds what the list cannot know: the area a tool
belongs to and where on the board the same thing is done. A test fails if a tool the server has is
neither here with a place on the board nor here marked as for agents only, and if a tool the board
calls is marked as for agents only.

A place on the board is a page: board, graph, roadmap, lists, search, activity, coverage, users,
tools, definition or status. Every record (an epic, story, task, acceptance criterion or note) has
a page of its own, and the words below say "its page" for it."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OnTheBoard:
    """A tool a person can use on the board: the page, and in words where on it."""

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
    "Status and changes",
    "Work",
    "Links",
    "Claims and the working loop",
    "Acceptance criteria",
    "Definition of done",
    "Notes",
    "Search and the index",
    "Moving data",
)

TOOL_PLACES: dict[str, ToolPlace] = {
    "server_status": _board(
        "Status and changes", "status", "The Status and export page shows everything it returns."
    ),
    "create_node": _board(
        "Work", "board", "New item on the Board, or Add story on an epic's lane, or Add task."
    ),
    "get_node": _board("Work", "board", "Open any item: its page shows all of it."),
    "list_nodes": _board(
        "Work", "board", "The Board, the Graph and the Roadmap, with the same filter language."
    ),
    "update_node": _board(
        "Work",
        "board",
        "Open an item and change its title, details, status, priority, size or dates on its "
        "page; drag a card on the Board or a bar on the Roadmap.",
    ),
    "update_nodes": _board(
        "Work", "roadmap", "Choose several bars on the Roadmap and move them together."
    ),
    "move_node": _board("Work", "board", "Move, on the page of a story or a task."),
    "node_history": _board("Work", "board", "History, on the page of an item."),
    "add_link": _board(
        "Links",
        "board",
        "Links, on an item's page; Mark as duplicates or Mark as related on a pair in Possible "
        "duplicates, on the Search page.",
    ),
    "remove_link": _board("Links", "board", "Links, on an item's page."),
    "list_links": _board("Links", "graph", "The Graph draws the links; an item's page lists them."),
    "ready_work": _board(
        "Claims and the working loop",
        "activity",
        "Free to take, on the Activity page; the Board counts the ready work nobody holds.",
    ),
    "claim_node": _board("Claims and the working loop", "activity", "Claim, on the Activity page."),
    "renew_claim": _board(
        "Claims and the working loop", "activity", "Renew, on the Activity page."
    ),
    "release_claim": _board(
        "Claims and the working loop", "activity", "Release, on the Activity page."
    ),
    "who_is_working": _board(
        "Claims and the working loop",
        "activity",
        "Who is working on what, on the Activity page.",
    ),
    "next_work": _agents(
        "Claims and the working loop",
        "It picks the most urgent free item, claims it and hands over everything an agent needs "
        "in one step, for an agent that has no work in mind. A person picks work on the Board "
        "and claims it on the Activity page.",
    ),
    "finish_work": _agents(
        "Claims and the working loop",
        "It ends an agent's claim, sets where the work stands and leaves the handoff note in one "
        "step. A person changes the status on the item's page, and releases a claim with a "
        "note on the Activity page.",
    ),
    "create_plan": _agents(
        "Work",
        "It creates a whole plan (items, links and acceptance criteria) as one all-or-nothing "
        "step from data an agent writes. A person makes items one at a time with New item.",
    ),
    "changes_since": _board(
        "Status and changes", "activity", "The Activity page is the feed; the board also polls it."
    ),
    "create_rule": _board(
        "Acceptance criteria",
        "lists",
        "New acceptance criterion, in the Acceptance criteria list or on a story's or task's page.",
    ),
    "get_rule": _board(
        "Acceptance criteria", "lists", "An acceptance criterion's details, on its page."
    ),
    "list_rules": _board(
        "Acceptance criteria",
        "lists",
        "The Acceptance criteria list shows every one, including those placed nowhere.",
    ),
    "update_rule": _board(
        "Acceptance criteria", "lists", "Edit, on an acceptance criterion's page."
    ),
    "place_rule": _board(
        "Acceptance criteria",
        "lists",
        "Put on a story or a task, or move to another, in the Acceptance criteria list or on "
        "its page.",
    ),
    "rule_history": _board(
        "Acceptance criteria", "lists", "History, on an acceptance criterion's page."
    ),
    "coverage_report": _board(
        "Acceptance criteria",
        "coverage",
        "The Coverage page; the strip above the Board's cards counts it.",
    ),
    "get_definition_of_done": _board(
        "Definition of done",
        "definition",
        "Settings, Definition of done; every story's and task's page shows its checks too.",
    ),
    "set_definition_of_done": _board(
        "Definition of done",
        "definition",
        "Settings, Definition of done: Edit and Save. Only a person can change it, so an "
        "agent's call is refused.",
    ),
    "set_check": _board(
        "Definition of done",
        "board",
        "Definition of done, on the page of a story or task: tick a check, and say how it was "
        "checked. An agent ticks the same checks itself.",
    ),
    "add_note": _board(
        "Notes",
        "lists",
        "Add a note, in the Notes list or on an item's page. If someone decided it, say who and "
        "when.",
    ),
    "get_note": _board("Notes", "lists", "A note's details, on its page."),
    "list_notes": _board("Notes", "lists", "The Notes list shows every note, with filters."),
    "link_note": _board("Notes", "lists", "What it concerns, on a note's page."),
    "unlink_note": _board("Notes", "lists", "What it concerns, on a note's page."),
    "supersede_note": _board("Notes", "lists", "Replace, on a note's page or in the Notes list."),
    "withdraw_note": _board("Notes", "lists", "Withdraw, on a note's page or in the Notes list."),
    "note_history": _board("Notes", "lists", "History, on a note's page."),
    "recall": _board(
        "Notes",
        "board",
        "The notes on an item's page, with the reason each is shown.",
    ),
    "set_confirmation": _board(
        "Notes",
        "lists",
        "Confirm a note, or take the confirmation away, from its status, on its page or in the "
        "Notes list. Only a person can confirm, so an agent's call is refused.",
    ),
    "search": _board("Search and the index", "search", "The search box, at the top of every page."),
    "similar_items": _board(
        "Search and the index",
        "board",
        "Might be the same as, on the page of an epic or a story.",
    ),
    "find_overlaps": _board(
        "Search and the index", "search", "Possible duplicates, on the Search page."
    ),
    "find_mentions": _board(
        "Search and the index",
        "board",
        "Mentioned in, on the page of an item, an acceptance criterion or a note.",
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
