"""The MCP server with its guard: what every tool call goes through before and after the tool.

Before the tool: the arguments are scanned for secrets in well-known formats (so one is refused
before anything is stored, sent or even checked, and cannot be echoed back by a validation
message), and a call over the network is refused if the agent's key does not allow that tool (a
key is offered the tools Vrum offers agents, or only the tools the owner chose for it, and its
tool list shows only those). After it: a call whose
arguments do not fit the tool is refused in plain words, without repeating what was sent, and
every call is one line in the log."""

import logging
import time
from collections.abc import Mapping
from typing import Any, NamedTuple

from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.shared.exceptions import MCPError
from mcp.types import ListToolsResult, PaginatedRequestParams
from pydantic import ValidationError

from vrum.access import NOT_A_BEARER, bearer_key
from vrum.accounts import (
    BAD_AGENT_KEY,
    NO_AGENT_KEY,
    Accounts,
    KeyAccess,
    key_not_allowed,
    tool_not_offered,
)
from vrum.common import Refusal, refuse_secrets
from vrum.db import Database, NoProject, current_project, working_for
from vrum.identity import Identity, agent, current_identity, working_as
from vrum.logs import event
from vrum.surface import AGENT_SURFACE

log = logging.getLogger("vrum.tools")

# How many problems with one call's arguments are listed, and how long a field name may be.
_MOST_PROBLEMS = 8
_LONGEST_FIELD = 60
# The JSON-RPC error code of a request that has no valid key (the same as access.py's).
_NO_VALID_KEY = -32001


class Caller(NamedTuple):
    """Who a call is for: the project, who is making it, and the tools it may use (None: every
    tool, for the board). `standard` is true when the key has no list of its own, so the tools are
    the ones Vrum offers agents."""

    project: int
    who: Identity
    tools: frozenset[str] | None
    standard: bool = False


def tools_of(found: KeyAccess) -> frozenset[str]:
    """The tools a key may use: its own list, or the ones Vrum offers agents."""
    return AGENT_SURFACE if found.tools is None else found.tools


def plain_validation_message(tool: str, error: ValidationError) -> str:
    """What was wrong with a call's arguments, in words, with the fields named and what was sent
    left out (the library's own text repeats it, which can be a whole request)."""
    problems = []
    for item in error.errors():
        field = ".".join(str(part) for part in item["loc"]) or "the arguments"
        problems.append(f"{field[:_LONGEST_FIELD]}: {item['msg']}")
    listed = "; ".join(problems[:_MOST_PROBLEMS])
    more = f"; and {len(problems) - _MOST_PROBLEMS} more" if len(problems) > _MOST_PROBLEMS else ""
    return (
        f"Error executing tool {tool}: the arguments are not valid ({listed}{more}). Check them "
        "against the tool's description. Nothing was changed."
    )


class GuardedServer(MCPServer[Database]):
    # Who may use which project. Set when the server is built (see create_server).
    accounts: Accounts

    async def _key_of(self, headers: Mapping[str, str] | None) -> KeyAccess:
        """What the agent key in a request's headers lets its holder do, looked up again on every
        call, so a key that has just been revoked, or whose tools have just been changed, takes
        effect on a connection that is already open."""
        key, reason = bearer_key((headers or {}).get("authorization"))
        if key is None:
            raise Refusal(NO_AGENT_KEY if reason == "no_key" else NOT_A_BEARER)
        found = await self.accounts.project_for_key(key)
        if found is None:
            raise Refusal(BAD_AGENT_KEY)
        return found

    async def _caller_of(self, context: Any) -> Caller:
        """The project this call is for, who is making it and which tools it may use, decided by
        the call itself and never by an argument. A call that came over the network is for the
        project its agent key belongs to, made by the agent the key is named for, and may use the
        tools the key allows. A call the board makes in this process is for the project the board
        has already checked that the signed-in person is in, made by that person, and is limited by
        the person's role there and not by any key."""
        if context is None:
            project = current_project()
            if project is None:
                raise NoProject("The board made a call with no project named.")
            return Caller(project, current_identity(), None)
        try:
            headers = context.headers
        except RuntimeError:
            headers = None
        found = await self._key_of(headers)
        return Caller(found.project_id, agent(found.name), tools_of(found), found.tools is None)

    def _may_use(self, tool: str, allowed: frozenset[str] | None) -> bool:
        """Whether a call to this tool is within what the key allows. A name the server does not
        have is not refused here: it gets the library's usual answer for an unknown tool."""
        return allowed is None or tool in allowed or self._tool_manager.get_tool(tool) is None

    async def _handle_list_tools(
        self, ctx: ServerRequestContext[Database], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        """The tools the caller's key may use, so that an agent does not even see the ones it
        cannot: its own list, or the set Vrum offers agents."""
        listed = await super()._handle_list_tools(ctx, params)
        try:
            found = await self._key_of(getattr(ctx.request, "headers", None))
        except Refusal as refusal:
            raise MCPError(_NO_VALID_KEY, str(refusal)) from None
        allowed = tools_of(found)
        return listed.model_copy(
            update={"tools": [tool for tool in listed.tools if tool.name in allowed]}
        )

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        started = time.monotonic()
        outcome = "ok"
        reason: str | None = None
        who: Identity | None = None
        project: int | None = None
        try:
            try:
                refuse_secrets(arguments)
            except Refusal as refusal:
                raise Refusal(f"Error executing tool {name}: {refusal}") from None
            try:
                caller = await self._caller_of(context)
            except Refusal as refusal:
                raise Refusal(f"Error executing tool {name}: {refusal}") from None
            project, who = caller.project, caller.who
            if not self._may_use(name, caller.tools):
                reason = "not_allowed"
                words = (
                    tool_not_offered(who.name, name)
                    if caller.standard
                    else key_not_allowed(who.name, name)
                )
                raise Refusal(f"Error executing tool {name}: {words}")
            with working_for(project), working_as(who):
                return await super().call_tool(name, arguments, context)
        except ToolError as error:
            if isinstance(error.__cause__, ValidationError):
                outcome = "invalid"
                raise ToolError(plain_validation_message(name, error.__cause__)) from None
            outcome = "failed" if isinstance(error, UnexpectedToolError) else "refused"
            raise
        except BaseException:
            outcome = "failed"
            raise
        finally:
            event(
                log,
                logging.INFO if outcome in ("ok", "refused", "invalid") else logging.ERROR,
                "tool_call",
                tool=name[:_LONGEST_FIELD],
                actor=who.name[:_LONGEST_FIELD] if who else None,
                actor_kind=who.kind if who else None,
                project=project,
                outcome=outcome,
                ms=round((time.monotonic() - started) * 1000),
                **({"reason": reason} if reason else {}),
            )
