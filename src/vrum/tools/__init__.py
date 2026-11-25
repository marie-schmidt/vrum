"""Shared MCP tool annotations, parameter types, and caller identity helper."""

from datetime import date
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from vrum.asks import AskInput
from vrum.common import Ref
from vrum.identity import current_identity

_Detail = Annotated[
    bool,
    Field(
        description="True: include whole bodies. By default a list leaves them out and gives each "
        "item's length and the first characters instead, to keep the answer small."
    ),
]

_Version = Annotated[
    int, Field(description="The version you last read (from get_node or the last change).")
]
_Lease = Annotated[
    int,
    Field(
        description="How long the claim lasts before it lapses on its own, in seconds, from 1 to "
        "86400 (24 hours). Renew it to keep it longer."
    ),
]
_Repository = Annotated[
    str | None, Field(description="Optional: the repository's https address, not checked.")
]
_Branch = Annotated[str | None, Field(description="Optional: the branch the work is on.")]
_Commit = Annotated[str | None, Field(description="Optional: the commit reached, by its hash.")]
_PullRequest = Annotated[
    str | None, Field(description="Optional: the pull request's https address.")
]
_RunLabel = Annotated[
    str | None,
    Field(description="Optional: a label for this run. A label only, never who you are."),
]
_ClientName = Annotated[
    str | None, Field(description="Optional: what you run in, such as claude-code. A label only.")
]
_Ask = Annotated[
    AskInput | None,
    Field(description="Optional: a question for a person. With blocked, it blocks until answered."),
]
_Node = Annotated[
    Ref,
    Field(
        description="The epic, story or task, by its reference: its kind and number, such as "
        "story:12."
    ),
]
_NodeRefs = Annotated[
    list[Ref] | None,
    Field(
        description="References of the epics, stories or tasks it concerns, such as story:12 or "
        "task:40."
    ),
]
_RuleIds = Annotated[
    list[int] | None, Field(description="Ids of the acceptance criteria (rules) it concerns.")
]
_NoteIds = Annotated[list[int] | None, Field(description="Ids of the notes it concerns.")]
_SaidBy = Annotated[
    str | None,
    Field(
        description="The person who changed their mind. Needed, with quote, to change a record a "
        "person has confirmed. If you are passing on what a person told you, give their name here: "
        "you are recorded separately, by your key."
    ),
]
_Quote = Annotated[
    str | None,
    Field(description="What that person said, or where they said it. Needed with said_by."),
]
_DecidedOn = Annotated[
    date | None,
    Field(
        description="The day it was decided, as YYYY-MM-DD. Only with decided_by; leave empty "
        "for today."
    ),
]
_Size = Annotated[
    str | None,
    Field(
        description="xs, s, m, l or xl: how big a story or task is, effort and context together "
        "(1, 2, 3, 5 and 8 points). An epic has no size of its own: it adds up. If something is "
        "bigger than xl, split it."
    ),
]
_StartOn = Annotated[
    str | None,
    Field(description="The day work is planned to start, as YYYY-MM-DD. Never after target_on."),
]
_TargetOn = Annotated[
    str | None,
    Field(description="The day it is planned to be done by, as YYYY-MM-DD."),
]
_Limit = Annotated[int, Field(description="How many items to return, from 1 to 200.")]
_After = Annotated[
    int | None,
    Field(description="Leave empty for the first page; pass next_after_id for the next."),
]
_AfterNode = Annotated[
    Ref | None,
    Field(
        description="Leave empty for the first page; pass next_after (the reference of the last "
        "node of the page) for the next."
    ),
]

# Tools that only read: they change nothing. The Tools page of the board says so from this, and
# lets a person try exactly these.
_READS = ToolAnnotations(read_only_hint=True)


def _me() -> str:
    """The name this call's changes are recorded under: the name of the call's identity (the agent
    its key is named for, or the person signed in on the board). The guard decides it before the
    tool runs, and it is never an argument."""
    return current_identity().name
