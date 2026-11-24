"""The MCP server with its guard: what every tool call goes through before and after the tool.

Before the tool: the arguments are scanned for secrets in well-known formats (so one is refused
before anything is stored, sent or even checked, and cannot be echoed back by a validation
message), and a call over the network is refused if the agent's key does not allow that tool (a
key is offered the tools Vrum offers agents, or only the tools the owner chose for it, and its
tool list shows only those). After it: a call whose
arguments do not fit the tool is refused in plain words, without repeating what was sent, and
every call is one line in the log.

A key can call Vrum one tool at a time, or by running code (`code_mode`): its tool list is then
exactly `explore` and `run`, and the tools its allowance names are the functions of the script it
runs. Every call a script makes comes back through `call_inner`, which is the same guard (the
secret scan, the allowance, the project, the identity, the log line) without the protocol around
it, so a function does what the tool of that name does, for the same key, in the same project."""

import logging
import re
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cached_property
from typing import Any, NamedTuple

from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, ListToolsResult, PaginatedRequestParams, TextContent
from pydantic import ValidationError

from vrum.access import NOT_A_BEARER, bearer_key
from vrum.accounts import (
    BAD_AGENT_KEY,
    NO_AGENT_KEY,
    RUN_IS_NOT_FOR_THE_BOARD,
    SCRIPT_KEY_NOW_TOOLS,
    SCRIPT_KEY_REVOKED,
    Accounts,
    KeyAccess,
    KeyExpired,
    code_tool_for_a_tools_key,
    key_not_allowed,
    no_such_function,
    script_key_expired_words,
    tool_by_name_for_a_code_key,
    tool_not_offered,
)
from vrum.code_mode import KeyStopped, RunTag, run_description
from vrum.common import Refusal, refuse_secrets
from vrum.db import Database, NoProject, current_project, working_for
from vrum.identity import Identity, agent, current_identity, working_as
from vrum.logs import event
from vrum.records import PEOPLE_ONLY_TOOLS
from vrum.sdk import Sdk, ToolSpec
from vrum.surface import AGENT_SURFACE, CODE_TOOLS, in_order

log = logging.getLogger("vrum.tools")

# How many problems with one call's arguments are listed, and how long a field name may be.
_MOST_PROBLEMS = 8
_LONGEST_FIELD = 60
# What a tool's name looks like. A name that does not is never repeated back to a caller.
_A_TOOL_NAME = re.compile(r"[A-Za-z0-9_]{1,60}")
# The JSON-RPC error code of a request that has no valid key (the same as access.py's).
_NO_VALID_KEY = -32001


class Caller(NamedTuple):
    """Who a call is for: the project, who is making it, and the tools it may use (None: every
    tool, for the board). `standard` is true when the key has no list of its own, so the tools are
    the ones Vrum offers agents. `code_mode` is true for a key that calls Vrum by running code:
    the tools are then the functions of its scripts. `key_id` is the number of the key a call came
    over (the board has none): a script holds it, never the key, to ask about the key again."""

    project: int
    who: Identity
    tools: frozenset[str] | None
    standard: bool = False
    code_mode: bool = False
    key_id: int | None = None


_caller: ContextVar[Caller | None] = ContextVar("vrum_caller", default=None)


@contextmanager
def calling_as(caller: Caller) -> Iterator[None]:
    """Everything done inside this block is done for this caller: what `current_caller` says."""
    token = _caller.set(caller)
    try:
        yield
    finally:
        _caller.reset(token)


def current_caller() -> Caller:
    """The caller of the tool that is running: set by the guard before the tool starts, so a tool
    that needs to know the key's allowance reads it here and never from an argument."""
    found = _caller.get()
    if found is None:
        raise NoProject("A tool that needs to know who is calling was run outside the guard.")
    return found


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
        try:
            found = await self.accounts.project_for_key(key)
        except KeyExpired as expired:
            raise Refusal(str(expired)) from None
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
        return Caller(
            found.project_id,
            agent(found.name, verifier=found.verifier),
            tools_of(found),
            found.tools is None,
            found.code_mode,
            found.key_id,
        )

    @cached_property
    def sdk(self) -> Sdk:
        """The TypeScript of every tool the server has (all but the two a script cannot call),
        made once, the first time it is needed, when every tool is registered."""
        return Sdk(
            ToolSpec(
                tool.name,
                tool.description,
                tool.parameters,
                tool.output_schema,
                tool.fn_metadata.wrap_output,
                bool(tool.annotations and tool.annotations.read_only_hint),
            )
            for tool in self._tool_manager.list_tools()
            if tool.name not in CODE_TOOLS
        )

    def functions_for(self, tools: frozenset[str] | None) -> list[str]:
        """The functions a script has when its key may call these tools (None: the tools Vrum
        offers agents): the tools the server has, in the order an agent reads them, and never one
        that only a person can use."""
        allowed = AGENT_SURFACE if tools is None else tools
        return in_order(
            name for name in allowed if self.sdk.has(name) and name not in PEOPLE_ONLY_TOOLS
        )

    def _refusal_for(self, name: str, caller: Caller) -> str | None:
        """Why a call over the network, or from the board, may not use this tool, in words, or
        None when it may. A name the server does not have is not refused here: it gets the
        library's usual answer for an unknown tool."""
        agent_name = caller.who.name
        if name in CODE_TOOLS:
            if caller.code_mode or (caller.tools is None and name == "explore"):
                return None
            if caller.tools is None:
                return RUN_IS_NOT_FOR_THE_BOARD
            return code_tool_for_a_tools_key(agent_name, name)
        if caller.tools is None or self._tool_manager.get_tool(name) is None:
            return None
        if name not in caller.tools:
            if caller.standard:
                return tool_not_offered(agent_name, name)
            return key_not_allowed(agent_name, name)
        if caller.code_mode:
            return tool_by_name_for_a_code_key(agent_name, name)
        return None

    def _inner_refusal(self, caller: Caller, name: str) -> str | None:
        """Why a script may not call this function, in words, or None when it may. Only a key that
        runs code has functions, and they are the tools of its allowance that a person is not the
        only one to use: anything else, including the two tools of code mode, is not one. A caller
        with no key number (the board) has none."""
        agent_name = caller.who.name
        if (
            not caller.code_mode
            or caller.tools is None
            or caller.key_id is None
            or name in CODE_TOOLS
            or self._tool_manager.get_tool(name) is None
        ):
            return no_such_function(agent_name, name)
        if name not in caller.tools or name in PEOPLE_ONLY_TOOLS:
            if caller.standard:
                return tool_not_offered(agent_name, name)
            return key_not_allowed(agent_name, name)
        return None

    async def _handle_list_tools(
        self, ctx: ServerRequestContext[Database], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        """The tools the caller's key may use, so that an agent does not even see the ones it
        cannot: its own list, or the set Vrum offers agents. A key that runs code is shown
        `explore` and `run` and nothing else, with `run` described for the functions it has."""
        listed = await super()._handle_list_tools(ctx, params)
        try:
            found = await self._key_of(getattr(ctx.request, "headers", None))
        except Refusal as refusal:
            raise MCPError(_NO_VALID_KEY, str(refusal)) from None
        allowed = tools_of(found)
        if found.code_mode:
            functions = self.functions_for(allowed)
            by_name = {tool.name: tool for tool in listed.tools}
            shown = []
            for name in CODE_TOOLS:
                tool = by_name[name]
                if name == "run":
                    description = run_description(self.sdk, functions)
                    tool = tool.model_copy(update={"description": description})
                shown.append(tool)
            return listed.model_copy(update={"tools": shown})
        return listed.model_copy(
            update={
                "tools": [
                    tool
                    for tool in listed.tools
                    if tool.name in allowed and tool.name not in CODE_TOOLS
                ]
            }
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
            words = self._refusal_for(name, caller)
            if words is not None:
                reason = "not_allowed"
                raise Refusal(f"Error executing tool {name}: {words}")
            with working_for(project), working_as(who), calling_as(caller):
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
            _log_call(name, outcome, reason, who, project, started)

    async def _key_now(self, caller: Caller) -> Caller:
        """The caller as its key is now: the same caller with the key's allowance as it stands this
        moment. A script holds the caller of the call that started it, so before each of its calls
        the key is read again, as `call_tool` reads it again on every request; a key that was
        revoked, or changed to call one tool at a time, stops the script (`KeyStopped`). A caller
        that is not a script's is left as it is: `_inner_refusal` says what it is."""
        if caller.key_id is None or not caller.code_mode or caller.tools is None:
            return caller
        try:
            found = await self.accounts.access_of_key(caller.key_id)
        except KeyExpired as expired:
            raise KeyStopped(script_key_expired_words(expired.day)) from None
        if found is None or found.project_id != caller.project:
            raise KeyStopped(SCRIPT_KEY_REVOKED)
        if not found.code_mode:
            raise KeyStopped(SCRIPT_KEY_NOW_TOOLS)
        return caller._replace(
            tools=tools_of(found),
            standard=found.tools is None,
            who=agent(caller.who.name, verifier=found.verifier),
        )

    async def call_inner(
        self, caller: Caller, name: str, arguments: dict[str, Any], tag: RunTag
    ) -> Any:
        """One call a script makes, answered as the tool of that name would answer it: the key
        read again (see `_key_now`), the secret scan, the key's allowance, the key's project and
        identity, the tool's own checks (one transaction), and one line in the log that says which
        run and which call it was (never what was sent or what came back). It gives back the tool's
        result as plain data (a result the library wrapped as {"result": ...} is unwrapped), or
        raises what the tool raises, a refusal in the tool's own sentence. A function has to be in
        the allowance the script started with and in the key's allowance now: one taken away while
        the script runs is refused, and one added does not exist in the script."""
        started = time.monotonic()
        outcome = "ok"
        reason: str | None = None
        shown = name if _A_TOOL_NAME.fullmatch(name) else "that call"
        try:
            now = await self._key_now(caller)
            try:
                refuse_secrets(arguments)
            except Refusal as refusal:
                raise Refusal(f"Error executing tool {shown}: {refusal}") from None
            words = self._inner_refusal(caller, name) or self._inner_refusal(now, name)
            if words is not None:
                reason = "not_allowed"
                raise Refusal(f"Error executing tool {shown}: {words}")
            with working_for(caller.project), working_as(now.who), calling_as(now):
                result = await super().call_tool(name, arguments, None)
            return self._value_of(name, result)
        except ToolError as error:
            if isinstance(error, KeyStopped):
                reason = "key_ended"
            if isinstance(error.__cause__, ValidationError):
                outcome = "invalid"
                raise ToolError(plain_validation_message(shown, error.__cause__)) from None
            outcome = "failed" if isinstance(error, UnexpectedToolError) else "refused"
            raise
        except BaseException:
            outcome = "failed"
            raise
        finally:
            _log_call(
                name,
                outcome,
                reason,
                caller.who,
                caller.project,
                started,
                via="run",
                run_id=tag.run_id,
                seq=tag.seq,
                script=tag.script,
            )

    def _value_of(self, name: str, result: Any) -> Any:
        """What a tool gave back, as plain data: its structured result with the library's wrapper
        taken off, or its text when it has no structured result."""
        if not isinstance(result, CallToolResult):
            raise ToolError(f"Error executing tool {name}: the tool needs more input.")
        structured = result.structured_content
        if structured is None:
            texts = [block.text for block in result.content if isinstance(block, TextContent)]
            return "\n".join(texts) if texts else None
        tool = self._tool_manager.get_tool(name)
        if tool is not None and tool.fn_metadata.wrap_output:
            return structured.get("result")
        return structured


def _log_call(
    name: str,
    outcome: str,
    reason: str | None,
    who: Identity | None,
    project: int | None,
    started: float,
    **extra: Any,
) -> None:
    """The one line every call leaves in the log: which tool, for whom, how it went and how long
    it took, never what was sent or what came back."""
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
        **extra,
    )
