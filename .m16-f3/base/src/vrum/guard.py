"""The MCP server with its guard: what every tool call goes through before and after the tool.

Before the tool: the arguments are scanned for secrets in well-known formats (so one is refused
before anything is stored, sent or even checked, and cannot be echoed back by a validation
message). After it: a call whose arguments do not fit the tool is refused in plain words, without
repeating what was sent, and every call is one line in the log."""

import logging
import time
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import ValidationError

from vrum.access import NOT_A_BEARER, bearer_key
from vrum.accounts import BAD_AGENT_KEY, NO_AGENT_KEY, Accounts
from vrum.common import Refusal, refuse_secrets
from vrum.db import Database, NoProject, current_project, working_for
from vrum.logs import event

log = logging.getLogger("vrum.tools")

# How many problems with one call's arguments are listed, and how long a field name may be.
_MOST_PROBLEMS = 8
_LONGEST_FIELD = 60


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

    async def _project_for(self, context: Any) -> int:
        """The project this call is for, decided by the call itself and never by an argument. A
        call that came over the network is for the project its agent key belongs to, found again
        on every call, so a key that has just been revoked stops working on a connection that is
        already open. A call the board makes in this process is for the project the board has
        already checked that the signed-in person owns."""
        if context is None:
            project = current_project()
            if project is None:
                raise NoProject("The board made a call with no project named.")
            return project
        try:
            header = (context.headers or {}).get("authorization")
        except RuntimeError:
            header = None
        key, reason = bearer_key(header)
        if key is None:
            raise Refusal(NO_AGENT_KEY if reason == "no_key" else NOT_A_BEARER)
        project = await self.accounts.project_for_key(key)
        if project is None:
            raise Refusal(BAD_AGENT_KEY)
        return project

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        started = time.monotonic()
        outcome = "ok"
        actor: str | None = None
        project: int | None = None
        try:
            try:
                refuse_secrets(arguments)
            except Refusal as refusal:
                raise Refusal(f"Error executing tool {name}: {refusal}") from None
            raw_actor = arguments.get("actor")
            actor = raw_actor[:100] if isinstance(raw_actor, str) else None
            try:
                project = await self._project_for(context)
            except Refusal as refusal:
                raise Refusal(f"Error executing tool {name}: {refusal}") from None
            with working_for(project):
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
                actor=actor,
                project=project,
                outcome=outcome,
                ms=round((time.monotonic() - started) * 1000),
            )
