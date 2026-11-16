"""Where on the board a person can do what each MCP tool does, or why a tool is for agents only.

The Tools page of the board is made from the server's own live tool list, so it can never name a
tool that does not exist or leave one out. This map adds what the list cannot know: the area a tool
belongs to and where on the board the same thing is done. A test fails if a tool the server has is
neither here with a place on the board nor here marked as for agents only, and if a tool the board
calls is marked as for agents only."""

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
    "Server",
    "Work",
    "Links",
    "Claims and the working loop",
    "Rules",
    "Decisions",
    "Facts and beliefs",
    "Search and the index",
    "Language model",
    "Moving data",
)

TOOL_PLACES: dict[str, ToolPlace] = {
    "server_status": _board("Server", "status", "The Status page shows everything it returns."),
    "create_node": _board(
        "Work", "board", "New item on the Board page, or Add a story or Add a task on a card."
    ),
    "get_node": _board("Work", "board", "Open any item: its card shows all of it."),
    "list_nodes": _board("Work", "table", "The Table page, with the same filter language."),
    "update_node": _board(
        "Work", "board", "Open a card and change its title, details, status, priority or dates."
    ),
    "update_nodes": _board(
        "Work", "table", "Tick rows on the Table page and change them together."
    ),
    "move_node": _board(
        "Work", "outline", "Drag an item on the Outline page, or choose Move on a card."
    ),
    "node_history": _board("Work", "board", "History at the bottom of a card."),
    "add_link": _board(
        "Links", "board", "A card's Links section, or Mark as… on the Overlaps page."
    ),
    "remove_link": _board("Links", "board", "A card's Links section."),
    "list_links": _board("Links", "graph", "A card's Links section; the Graph page draws them."),
    "ready_work": _board(
        "Claims and the working loop",
        "home",
        "Home counts the ready work nobody holds, and Activity lists it with a Claim button.",
    ),
    "claim_node": _board(
        "Claims and the working loop", "activity", "Claim, under Who is working on what."
    ),
    "renew_claim": _board(
        "Claims and the working loop", "activity", "Renew, under Who is working on what."
    ),
    "release_claim": _board(
        "Claims and the working loop", "activity", "Release, under Who is working on what."
    ),
    "who_is_working": _board(
        "Claims and the working loop",
        "activity",
        "Who is working on what, beside the feed on the Activity page.",
    ),
    "next_work": _agents(
        "Claims and the working loop",
        "It picks the most urgent free item, claims it and hands over everything an agent needs "
        "in one step, for an agent that has no work in mind. A person picks work on the Board "
        "and claims it on Activity.",
    ),
    "finish_work": _agents(
        "Claims and the working loop",
        "It ends an agent's claim, sets where the work stands and leaves the handoff note in one "
        "step. A person changes the status on a card, and releases a claim with a note on "
        "Activity.",
    ),
    "create_plan": _agents(
        "Work",
        "It creates a whole plan (items, links and rules) as one all-or-nothing step from data "
        "an agent writes. A person makes items one at a time with New item.",
    ),
    "changes_since": _board(
        "Server", "activity", "The Activity page is the feed; the board also polls it."
    ),
    "create_rule": _board("Rules", "rules", "New rule on the Rules page."),
    "get_rule": _board("Rules", "rules", "A rule's details on the Rules page."),
    "list_rules": _board("Rules", "rules", "The Rules page lists every rule."),
    "update_rule": _board("Rules", "rules", "Edit, on a rule on the Rules page."),
    "place_rule": _board("Rules", "rules", "Put in a story, on the Rules page."),
    "move_rule": _board("Rules", "rules", "Drag a rule onto another story, or Put in a story."),
    "rule_history": _board("Rules", "activity", "The Activity page, with the filter on the rule."),
    "coverage_report": _board(
        "Rules", "rules", "The coverage report on the Rules page; Coverage on the Board page."
    ),
    "record_decision": _board("Decisions", "memory", "Record a decision on the Memory page."),
    "get_decision": _board("Decisions", "memory", "A decision's entry on the Memory page."),
    "list_decisions": _board("Decisions", "memory", "The Memory page lists decisions."),
    "link_decision": _board("Decisions", "memory", "What it concerns, on a decision."),
    "unlink_decision": _board("Decisions", "memory", "What it concerns, on a decision."),
    "supersede_decision": _board("Decisions", "memory", "Replace, on a decision."),
    "decision_history": _board(
        "Decisions", "activity", "The Activity page, with the filter on the decision."
    ),
    "recall": _board(
        "Decisions",
        "memory",
        "The recall preview on the Memory page, and the sections of a card.",
    ),
    "retain_fact": _board("Facts and beliefs", "memory", "Record a fact on the Memory page."),
    "record_belief": _board("Facts and beliefs", "memory", "Record a belief on the Memory page."),
    "supersede_memory": _board("Facts and beliefs", "memory", "Replace, on a fact or belief."),
    "retract_memory": _board("Facts and beliefs", "memory", "Retract, on a fact or belief."),
    "confirm_belief": _board("Facts and beliefs", "memory", "Confirm, on a belief."),
    "promote_belief": _board("Facts and beliefs", "memory", "Make it a decision, on a belief."),
    "link_memory": _board("Facts and beliefs", "memory", "What it concerns, on a fact or belief."),
    "unlink_memory": _board(
        "Facts and beliefs", "memory", "What it concerns, on a fact or belief."
    ),
    "get_memory": _board("Facts and beliefs", "memory", "A fact's or belief's entry on Memory."),
    "list_memories": _board("Facts and beliefs", "memory", "The Memory page lists them."),
    "memory_history": _board(
        "Facts and beliefs", "activity", "The Activity page, with the filter on the item."
    ),
    "search": _board("Search and the index", "search", "The Search page."),
    "similar_items": _board(
        "Search and the index", "board", "Might be the same as, on the card of an epic or story."
    ),
    "find_overlaps": _board("Search and the index", "overlaps", "The Overlaps page."),
    "find_mentions": _board(
        "Search and the index",
        "board",
        "Mentioned in, on a card, a rule or a memory entry; the Graph's Mentions layer.",
    ),
    "reindex": _agents(
        "Search and the index",
        "It rebuilds the index of meanings from the embedding service. The server already does "
        "that by itself within seconds, and the Status page shows how it went, so running it by "
        "hand is a job for an agent or the command line.",
    ),
    "extract_facts": _agents(
        "Language model",
        "It reads a passage an agent hands it and proposes facts to record, each with the words "
        "it came from. On the board a person writes the facts themselves, on the Memory page.",
    ),
    "consolidate_memory": _agents(
        "Language model",
        "It asks the language model to draw beliefs from facts, for an agent to record. On the "
        "board a person records beliefs themselves, on the Memory page.",
    ),
    "find_contradictions": _board(
        "Language model", "memory", "Check against every belief, when writing a fact or belief."
    ),
    "reflect": _board("Language model", "search", "Ask, at the bottom of the Search page."),
    "export_graph": _board("Moving data", "status", "Download the full export, on Status."),
    "import_graph": _agents(
        "Moving data",
        "It loads a whole export into an empty server, which is done once to set one up, so it "
        "stays with the command line (scripts/import_graph.py) and the README.",
    ),
}
