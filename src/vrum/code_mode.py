"""Running code for an agent key that calls Vrum by running code: the host side.

The script runs in the sandbox service (a container of its own, with no network and no secrets,
reached over a Unix socket on a volume the two share: see sandbox/). This module is the app's end
of that socket. It sends the script, and each time the script calls a function of `vrum` the
sandbox sends the call back, the app answers it through the guard (`GuardedServer.call_inner`: the
same secret scan, allowance, project, identity and log line as a tool call, one transaction for
each call) and the sandbox hands the answer to the script. The messages are JSON lines, one run
for each connection:

    app to sandbox   ping | run | reply | cancel
    sandbox to app   pong | call | done | fail

The app does not trust the sandbox. It validates every message's shape and size, counts calls and
writes and watches the wall clock itself, whatever the sandbox says it enforces, and a sandbox that
sends garbage, too much, something it should not or nothing at all ends the run with the stage
`sandbox`. The connection is always closed at the end; the sandbox then kills what it ran.

Calls that only read may run together. Calls that change something run one at a time, in the order
the script made them. Nothing is rolled back and nothing is replayed: each call is its own
transaction, and a run that stops half way says which calls finished, which were refused and which
were left unanswered, and whether anything may have changed.

This module also writes everything an agent that runs code is told: the description of `run` for
the key (the guide, the names of its functions and, when they fit, their signatures), what
`explore` gives back, and the plain words for a script that did not finish."""

import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from vrum.accounts import RUN_IS_NOT_FOR_THE_BOARD
from vrum.common import Refusal
from vrum.logs import event
from vrum.sdk import Sdk, first_sentence

if TYPE_CHECKING:
    from vrum.guard import Caller

log = logging.getLogger("vrum.code")

# The limits of a script. They are fixed, not settings, and the sandbox enforces its own copy.
DEFAULT_TIMEOUT = 60
MOST_TIMEOUT = 120
MOST_CALLS = 200
MOST_WRITES = 100
MOST_RUNS = 4
MOST_SCRIPT_BYTES = 1024 * 1024
MOST_RESULT_BYTES = 256 * 1024
MOST_LOG_BYTES = 16 * 1024
# One call, or one reply, may hold this much JSON.
MOST_MESSAGE_BYTES = 1024 * 1024
# A line the app sends may hold this much (the script, the declarations and the limits); a line it
# accepts is at most one message and a little more for what goes round it.
MOST_LINE_BYTES = 4 * 1024 * 1024
_ACCEPTED_LINE_BYTES = MOST_MESSAGE_BYTES + 64 * 1024
# How long past its own time limit the sandbox has to report, how long the app waits to reach it
# and to see the connection close, and how many calls over the limits are answered before the run
# is stopped. The sandbox's clock starts when the script does, after it has read and type checked
# it (up to about 11 s on a busy machine), so the app allows that much more.
GRACE_SECONDS = 15
CONNECT_SECONDS = 2
CLOSE_SECONDS = 2
MOST_OVER_LIMIT = 20
# How many of a script's calls the app makes at once (reads; changes always go one at a time), how
# many it holds for one script, started or waiting, before it stops reading the sandbox's next
# call, and how many bytes of answers may wait to be written to a sandbox that is not reading them.
MOST_PARALLEL_READS = 8
MOST_OUTSTANDING_CALLS = 16
MOST_QUEUED_BYTES = 8 * 1024 * 1024
# The longest one write to the sandbox may take, and the longest the app waits for its own
# cancel to be taken.
SEND_SECONDS = 10
CANCEL_SECONDS = 1
# Refusals in one run that are noted in the log as a sign of something wrong.
REPEATED_REFUSALS = 3
# The most characters the description of `run` may have when it holds the signatures too. Past it,
# the description has the function names only and `explore` gives the types.
MOST_DESCRIPTION_CHARS = 32_000

_STAGES = ("syntax", "types", "refused", "error", "time", "memory", "limit", "sandbox")
_A_FUNCTION_NAME = re.compile(r"[A-Za-z0-9_]{1,60}")
_MOST_MESSAGE_CHARS = 2000
_MOST_LISTED_CALLS = 30
_MOST_LISTED_LOGS = 5
_LISTED_LOG_CHARS = 200
_LISTED_REFUSAL_CHARS = 160

NOT_AVAILABLE = (
    "Running code is not available: the sandbox service is not running. Nothing was run and "
    "nothing was changed. Ask whoever runs this server to start it. explore still works."
)
BUSY = (
    "Scripts are busy: this server is already running as many as it allows. Try again in a "
    "moment. Nothing was run."
)
EMPTY_SCRIPT = (
    "There is no script to run. Send the body of an async function as code. Nothing was run."
)
SCRIPT_TOO_BIG = "The script is larger than 1 MB. Split it into smaller scripts. Nothing was run."
BAD_TIMEOUT = (
    f"timeout_seconds has to be a whole number of seconds from 1 to {MOST_TIMEOUT}. "
    "Nothing was run."
)
UNEXPECTED = (
    "The sandbox sent something unexpected, so the script was stopped. What it had already done "
    "stays done."
)
SANDBOX_ENDED = (
    "The sandbox stopped before the script finished, so the script was stopped. What it had "
    "already done stays done."
)
TOO_MANY_CALLS = (
    f"This script has made {MOST_CALLS} calls, the most one script may make. This call was not "
    "made. Nothing was changed by it."
)
TOO_MANY_WRITES = (
    f"This script has made {MOST_WRITES} calls that change something, the most one script may "
    "make. This call was not made. Nothing was changed by it."
)
RESULT_TOO_BIG = (
    "This call ran, but its result is larger than 1 MB, which is more than a script can be "
    "given. Ask for less: a smaller limit, or fewer details."
)
RESULT_NOT_JSON = "This call ran, but its result could not be given to the script."
ARGUMENTS_NOT_AN_OBJECT = "The arguments of a call have to be an object. Nothing was changed."
CALL_FAILED = (
    "Something went wrong inside the server while this call ran, so it may not have finished. "
    "Look at the server's log (docker compose logs app)."
)
NOT_TAKING_ANSWERS = (
    "The sandbox stopped taking the answers to its calls, so the script was stopped. What it had "
    "already done stays done."
)
TOO_MUCH_WAITING = (
    "The sandbox is not taking the answers to its calls as fast as they come: more than 8 MB were "
    "waiting, so the script was stopped. What it had already done stays done."
)


class KeyStopped(ToolError):
    """The key a script runs for was revoked, or was changed to call one tool at a time, while the
    script ran. The call that found it is not made and the whole script is stopped."""


class RunTag(NamedTuple):
    """Which script a call came from, for the log: the run's number, the call's place in it and a
    short hash of the script (never its text)."""

    run_id: str
    seq: int
    script: str


class CallRecord(BaseModel):
    tool: str = Field(description="The function that was called.")
    state: str = Field(
        description="done; refused (the refusal says why); or not finished (the call was sent and "
        "the script stopped before it was answered: if it changes something, it may have been "
        "made)."
    )
    ms: int = Field(description="How long it took, in milliseconds.")
    refusal: str | None = Field(
        default=None, description="The tool's own sentence, when it refused."
    )


class RunResult(BaseModel):
    result: Any = Field(
        description="What the script returned, as JSON (null when it returned nothing)."
    )
    logs: list[str] = Field(description="What the script wrote with console.log, one entry a line.")
    calls: list[CallRecord] = Field(
        description="The calls the script made, in order: which function, how it ended and how "
        "long it took. Never what was sent or what came back."
    )


class FunctionLine(BaseModel):
    name: str
    summary: str = Field(description="What the function does, in a line.")
    only_reads: bool = Field(description="True when the function changes nothing.")


class Explored(BaseModel):
    functions: list[FunctionLine] = Field(
        description="The functions this key's scripts have: all of them, or the ones asked for "
        "(by names or by words)."
    )
    declarations: str = Field(
        description="The TypeScript of those functions, with the types of their arguments and "
        "results, when names or a query were given; empty otherwise."
    )
    not_functions: list[str] = Field(
        description="Names that were asked for and are not functions of this key."
    )


@dataclass
class _Call:
    seq: int
    tool: str
    writes: bool
    state: str = "not finished"
    ms: int = 0
    refusal: str | None = None
    # Whether the call went to the guard. One that was still waiting for its turn when the run
    # ended never did, so it cannot have changed anything.
    began: bool = False


@dataclass
class RunOutcome:
    """How a script ended. `calls` is what it asked for and how each ended; `changed_anything`
    says whether a call that changes something finished or may have (it was sent and not
    answered)."""

    ok: bool
    result: Any = None
    logs: list[str] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    stage: str | None = None
    message: str = ""
    line: int | None = None
    changed_anything: bool = False
    changing_calls: list[str] = field(default_factory=list)
    seconds: int = DEFAULT_TIMEOUT


class Host(Protocol):
    """What running a script needs of the server: the TypeScript of its tools, which tools are a
    key's functions, and the guard's way of answering one call."""

    @property
    def sdk(self) -> Sdk: ...

    def functions_for(self, tools: frozenset[str] | None) -> list[str]: ...

    async def call_inner(
        self, caller: "Caller", name: str, arguments: dict[str, Any], tag: RunTag
    ) -> Any: ...


class _Stop(Exception):
    """The run is over because of something on the app's side, not the script's: the time ran out,
    a limit was passed or the sandbox misbehaved."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message


class _NotGiven(Exception):
    """A call's result cannot be handed to the script."""

    def __init__(self, words: str) -> None:
        super().__init__(words)
        self.words = words


@dataclass
class _Ending:
    """How the sandbox's side of a run ended: its own `done` or `fail`, or a stop of ours."""

    ok: bool
    result: Any = None
    logs: list[str] = field(default_factory=list)
    stage: str | None = None
    message: str = ""
    line: int | None = None
    ours: bool = False


def _line_of(message: dict[str, Any]) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("ascii")


def _json_text(value: Any) -> str:
    """A result as JSON text, one line, or why it cannot be handed over."""
    try:
        text = json.dumps(value, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise _NotGiven(RESULT_NOT_JSON) from None
    if len(text) > MOST_MESSAGE_BYTES:
        raise _NotGiven(RESULT_TOO_BIG)
    return text


def _shown_name(name: str) -> str:
    return name if _A_FUNCTION_NAME.fullmatch(name) else "(not a function)"


class _Run:
    """One script, from the connection to the end."""

    def __init__(self, mode: "CodeMode", caller: "Caller", code: str, seconds: int) -> None:
        self._mode = mode
        self._host = mode.host
        self._caller = caller
        self._code = code
        self._seconds = seconds
        self.run_id = secrets.token_hex(6)
        self._script = hashlib.sha256(code.encode("utf-8", "surrogatepass")).hexdigest()[:12]
        self._functions = self._host.functions_for(caller.tools)
        self._calls: list[_Call] = []
        self._called = 0
        self._writes = 0
        self._over_limit = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self._writing = asyncio.Lock()
        self._reading = asyncio.Semaphore(MOST_PARALLEL_READS)
        self._sending = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._deadline = 0.0
        # Bytes of answers handed to the writer and not yet taken by the sandbox, and the most
        # there ever were.
        self._queued = 0
        self.most_queued = 0
        # Set when the run has to end because of something found away from the main loop: a key
        # that was revoked, a sandbox that does not take answers. The main loop ends the run.
        self._halted: _Stop | None = None
        self._halt_event = asyncio.Event()
        # True once the sandbox is found not to be reading: nothing more is written to it, and the
        # connection is dropped at the end instead of waited for.
        self._not_reading = False

    async def go(self) -> RunOutcome:
        started = time.monotonic()
        self._deadline = started + self._seconds + GRACE_SECONDS
        await self._connect()
        ending: _Ending | None = None
        try:
            await self._send_line(self._start_line())
            ending = await self._converse()
        except _Stop as stop:
            ending = _Ending(False, stage=stop.stage, message=stop.message, ours=True)
        finally:
            await self._finish(ending)
        outcome = self._outcome(ending)
        self._note(outcome, started)
        return outcome

    async def _connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self._mode.path, limit=_ACCEPTED_LINE_BYTES),
                CONNECT_SECONDS,
            )
        except (OSError, TimeoutError):
            raise Refusal(NOT_AVAILABLE) from None

    def _start_line(self) -> bytes:
        declarations = self._host.sdk.declarations(self._functions, compact=True)
        line = _line_of(
            {
                "op": "run",
                "id": self.run_id,
                "code": self._code,
                "functions": self._functions,
                "declarations": declarations,
                "timeout": self._seconds,
                "max_calls": MOST_CALLS,
                "max_result_bytes": MOST_RESULT_BYTES,
                "max_log_bytes": MOST_LOG_BYTES,
            }
        )
        if len(line) > MOST_LINE_BYTES:
            raise Refusal(SCRIPT_TOO_BIG)
        return line

    def _halt(self, stop: _Stop) -> None:
        """End the run: the first reason found is the one told. Everything waiting on a call or on
        the sandbox wakes, and no call that has not started is made."""
        if self._halted is None:
            self._halted = stop
            self._halt_event.set()

    def _raise_if_halted(self) -> None:
        if self._halted is not None:
            raise _Stop(self._halted.stage, self._halted.message)

    async def _send_line(self, line: bytes, wait: float | None = None) -> None:
        """Write one line to the sandbox, one at a time. A sandbox that takes nothing for `wait`
        seconds (`SEND_SECONDS` unless told, or until the run's own time is up) is not reading:
        the run is ended, since the answer would only wait in memory."""
        writer = self._writer
        if writer is None:
            return
        wait = SEND_SECONDS if wait is None else wait
        async with self._sending:
            try:
                writer.write(line)
                remaining = max(0.1, self._deadline - time.monotonic())
                await asyncio.wait_for(writer.drain(), min(wait, remaining))
            except TimeoutError:
                self._not_reading = True
                self._halt(_Stop("sandbox", NOT_TAKING_ANSWERS))
            except OSError:
                # The sandbox is gone. The next read says so.
                pass

    async def _send_reply(self, reply: bytes) -> None:
        """Hand an answer to the sandbox. What is waiting to be written is counted, and a sandbox
        that lets more than 8 MB wait ends the run (the run holds nothing it cannot let go of)."""
        if self._halted is not None:
            return
        self._queued += len(reply)
        self.most_queued = max(self.most_queued, self._queued)
        try:
            if self._queued > MOST_QUEUED_BYTES:
                self._not_reading = True
                self._halt(_Stop("limit", TOO_MUCH_WAITING))
                return
            await self._send_line(reply)
        finally:
            self._queued -= len(reply)

    # -- what the sandbox says ---------------------------------------------------------------

    async def _converse(self) -> _Ending:
        reader = self._reader
        assert reader is not None
        while True:
            self._raise_if_halted()
            await self._wait_for_room()
            message = self._parse(await self._read(reader))
            op = message["op"]
            if op == "call":
                self._start_call(message)
            elif op == "done":
                return self._done(message)
            elif op == "fail":
                return self._fail(message)
            else:
                raise _Stop("sandbox", UNEXPECTED)

    async def _until(self, waiting_for: Sequence[asyncio.Future[Any]]) -> None:
        """Wait until something in `waiting_for` is done, the run is halted or the run's own time
        is up, whichever is first; the task made here is cancelled and awaited, never left."""
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise _Stop("time", "")
        halted = asyncio.ensure_future(self._halt_event.wait())
        try:
            await asyncio.wait(
                {halted, *waiting_for}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            halted.cancel()
            await asyncio.gather(halted, return_exceptions=True)

    async def _wait_for_room(self) -> None:
        """Do not read the sandbox's next call while this many are being answered: the sandbox
        waits (its writes block) and nothing piles up here."""
        while len(self._tasks) >= MOST_OUTSTANDING_CALLS:
            await self._until(list(self._tasks))
            self._raise_if_halted()

    async def _read(self, reader: asyncio.StreamReader) -> bytes:
        reading = asyncio.ensure_future(reader.readline())
        try:
            await self._until([reading])
        finally:
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)
        self._raise_if_halted()
        if reading.cancelled():
            raise _Stop("time", "")
        try:
            line: bytes = reading.result()
        except (ValueError, asyncio.LimitOverrunError):
            raise _Stop("sandbox", UNEXPECTED) from None
        except OSError:
            raise _Stop("sandbox", SANDBOX_ENDED) from None
        if not line.endswith(b"\n"):
            raise _Stop("sandbox", SANDBOX_ENDED)
        return line

    @staticmethod
    def _parse(line: bytes) -> dict[str, Any]:
        try:
            message = json.loads(line)
        except ValueError:
            raise _Stop("sandbox", UNEXPECTED) from None
        if not isinstance(message, dict) or not isinstance(message.get("op"), str):
            raise _Stop("sandbox", UNEXPECTED)
        return message

    def _start_call(self, message: dict[str, Any]) -> None:
        seq, name, arguments = message.get("seq"), message.get("name"), message.get("args", {})
        self._called += 1
        if type(seq) is not int or seq != self._called or not isinstance(name, str):
            raise _Stop("sandbox", UNEXPECTED)
        writes = not self._host.sdk.reads_only(name)
        words = None
        if self._called > MOST_CALLS:
            words = TOO_MANY_CALLS
        elif writes and self._writes >= MOST_WRITES:
            words = TOO_MANY_WRITES
        if words is not None:
            self._over_limit += 1
            if self._over_limit > MOST_OVER_LIMIT:
                raise _Stop(
                    "limit",
                    "It kept calling after it had passed the limit on calls, so it was stopped.",
                )
            self._spawn(self._reply_error(seq, words))
            return
        if writes:
            self._writes += 1
        record = _Call(seq, _shown_name(name), writes)
        self._calls.append(record)
        self._spawn(self._answer(record, name, arguments))

    def _spawn(self, work: Any) -> None:
        task: asyncio.Task[None] = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _make_call(
        self, record: _Call, name: str, arguments: dict[str, Any], tag: RunTag
    ) -> Any:
        """One call through the guard, unless the run has already ended: a call that had not
        started when the run was halted (a key revoked, a sandbox that does not listen) is not
        made. The guard asks about the key again before it runs the call."""
        if self._halted is not None:
            raise Refusal(self._halted.message or NOT_TAKING_ANSWERS)
        record.began = True
        return await self._host.call_inner(self._caller, name, arguments, tag)

    async def _answer(self, record: _Call, name: str, arguments: Any) -> None:
        """Make one call through the guard and tell the sandbox how it went. Changes go one at a
        time, in the order made, and a change that waited for its turn is checked again once it
        has it (the run may have been halted meanwhile); reads go eight at a time."""
        started = time.monotonic()
        reply: bytes | None = None
        try:
            if not isinstance(arguments, dict):
                raise Refusal(ARGUMENTS_NOT_AN_OBJECT)
            tag = RunTag(self.run_id, record.seq, self._script)
            if record.writes:
                async with self._writing:
                    value = await self._make_call(record, name, arguments, tag)
            else:
                async with self._reading:
                    value = await self._make_call(record, name, arguments, tag)
            text = _json_text(value)
            del value
            reply = f'{{"op":"reply","seq":{record.seq},"ok":true,"result":{text}}}\n'.encode(
                "ascii"
            )
            record.state = "done"
        except KeyStopped as stopped:
            record.state, record.refusal = "refused", str(stopped)
            self._halt(_Stop("refused", str(stopped)))
        except ToolError as error:
            record.state, record.refusal = "refused", str(error)
            reply = _line_of({"op": "reply", "seq": record.seq, "ok": False, "error": str(error)})
        except _NotGiven as problem:
            record.state = "done"
            reply = _line_of(
                {"op": "reply", "seq": record.seq, "ok": False, "error": problem.words}
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("A call from a script failed outside the tool")
            reply = _line_of({"op": "reply", "seq": record.seq, "ok": False, "error": CALL_FAILED})
        finally:
            record.ms = round((time.monotonic() - started) * 1000)
        if reply is not None:
            await self._send_reply(reply)

    async def _reply_error(self, seq: int, words: str) -> None:
        await self._send_reply(_line_of({"op": "reply", "seq": seq, "ok": False, "error": words}))

    def _done(self, message: dict[str, Any]) -> _Ending:
        result = message.get("result")
        try:
            size = len(json.dumps(result, separators=(",", ":"), allow_nan=False))
        except (TypeError, ValueError):
            raise _Stop("sandbox", UNEXPECTED) from None
        if size > MOST_RESULT_BYTES:
            raise _Stop(
                "limit",
                "The script's result is larger than 256 KB, so it was not returned. Return less: "
                "the fields you need, or a count.",
            )
        logs = _logs_of(message.get("logs"), bool(message.get("truncated_logs")), strict=True)
        return _Ending(True, result=result, logs=logs)

    def _fail(self, message: dict[str, Any]) -> _Ending:
        stage = message.get("stage")
        stage = stage if stage in _STAGES else "sandbox"
        words = message.get("message")
        words = " ".join(words.split())[:_MOST_MESSAGE_CHARS] if isinstance(words, str) else ""
        line = message.get("line")
        line = line if type(line) is int and line > 0 else None
        logs = _logs_of(message.get("logs"), False, strict=False)
        return _Ending(False, logs=logs, stage=stage, message=words, line=line)

    # -- the end -----------------------------------------------------------------------------

    async def _finish(self, ending: _Ending | None) -> None:
        """Settle the calls the script left behind, and close the connection. A sandbox that ended
        the run itself has had every call answered, so the calls it still has are waited for (as
        long as the time limit allows). When the run ends for another reason (our own stop, or
        nothing at all because this task was cancelled) the calls are cancelled first, before
        anything is awaited, then the sandbox is told to cancel (it may not be listening, so only
        for a moment), and the connection is dropped hard if it will not close: nothing of the run
        is left running or held. Every task of the run is awaited."""
        stopped = ending is None or ending.ours
        if stopped:
            for task in list(self._tasks):
                task.cancel()
            if not self._not_reading:
                await self._send_line(_line_of({"op": "cancel"}), CANCEL_SECONDS)
        else:
            pending = [task for task in self._tasks if not task.done()]
            if pending:
                await asyncio.wait(pending, timeout=max(0.0, self._deadline - time.monotonic()))
        for task in [task for task in self._tasks if not task.done()]:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        writer = self._writer
        if writer is not None:
            if self._not_reading:
                writer.transport.abort()
                return
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), CLOSE_SECONDS)
            except (OSError, TimeoutError):
                writer.transport.abort()

    def _outcome(self, ending: _Ending | None) -> RunOutcome:
        assert ending is not None
        changing = [
            call.tool
            for call in self._calls
            if call.writes
            and (call.state == "done" or (call.state == "not finished" and call.began))
        ]
        return RunOutcome(
            ending.ok,
            ending.result,
            ending.logs,
            [
                CallRecord(tool=call.tool, state=call.state, ms=call.ms, refusal=call.refusal)
                for call in self._calls
            ],
            None if ending.ok else ending.stage,
            ending.message,
            ending.line,
            bool(changing),
            changing,
            self._seconds,
        )

    def _note(self, outcome: RunOutcome, started: float) -> None:
        refused = sum(1 for call in outcome.calls if call.state == "refused")
        event(
            log,
            logging.INFO,
            "run_finished",
            run_id=self.run_id,
            script=self._script,
            actor=self._caller.who.name[:60],
            project=self._caller.project,
            outcome="ok" if outcome.ok else outcome.stage,
            calls=len(outcome.calls),
            refused_calls=refused,
            ms=round((time.monotonic() - started) * 1000),
        )
        if refused >= REPEATED_REFUSALS:
            event(
                log,
                logging.WARNING,
                "run_refused_calls",
                run_id=self.run_id,
                script=self._script,
                actor=self._caller.who.name[:60],
                project=self._caller.project,
                count=refused,
            )


def _logs_of(value: Any, truncated: bool, *, strict: bool) -> list[str]:
    """What a script wrote, at most 16 KB of it (the app cuts it again whatever the sandbox
    did). A sandbox that sends logs that are not a list of text is misbehaving when it reports a
    result; when it reports a failure the logs are only a help, so they are left out."""
    if not isinstance(value, list) or not all(isinstance(one, str) for one in value):
        if strict and value is not None:
            raise _Stop("sandbox", UNEXPECTED)
        return []
    kept: list[str] = []
    used = 0
    for one in value:
        used += len(one.encode("utf-8", "replace")) + 1
        if used > MOST_LOG_BYTES:
            truncated = True
            break
        kept.append(one)
    if truncated:
        kept.append("(more was written than the 16 KB that are kept, so the rest was cut)")
    return kept


class CodeMode:
    """The app's end of the sandbox socket. One for the server: at most four scripts run at once
    across all keys and projects, and the fifth is refused in words at once."""

    def __init__(self, socket_path: str, host: Host) -> None:
        self.path = socket_path
        self.host = host
        self._running = 0

    async def ping(self) -> str | None:
        """The version of Deno the sandbox says it runs, or None when it does not answer within a
        second. For the status page: it never waits long and never raises."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.path, limit=_ACCEPTED_LINE_BYTES), 1
            )
        except (OSError, TimeoutError):
            return None
        try:
            writer.write(_line_of({"op": "ping"}))
            await asyncio.wait_for(writer.drain(), 1)
            answer = json.loads(await asyncio.wait_for(reader.readline(), 1))
        except (OSError, TimeoutError, ValueError):
            return None
        finally:
            writer.close()
        if isinstance(answer, dict) and answer.get("op") == "pong":
            return str(answer.get("deno", ""))[:40]
        return None

    async def run(self, caller: "Caller", code: Any, timeout: Any = DEFAULT_TIMEOUT) -> RunOutcome:
        """Run a script for a key that runs code. It raises a refusal in words when the script
        cannot even be started (nothing sandboxed to run it, too many running, an empty or oversize
        script, a bad time limit); every other ending is a `RunOutcome`."""
        if not caller.code_mode or caller.tools is None or caller.key_id is None:
            raise Refusal(RUN_IS_NOT_FOR_THE_BOARD)
        if type(timeout) is not int or not 1 <= timeout <= MOST_TIMEOUT:
            raise Refusal(BAD_TIMEOUT)
        if not isinstance(code, str) or not code.strip():
            raise Refusal(EMPTY_SCRIPT)
        if len(code.encode("utf-8", "surrogatepass")) > MOST_SCRIPT_BYTES:
            raise Refusal(SCRIPT_TOO_BIG)
        if self._running >= MOST_RUNS:
            raise Refusal(BUSY)
        self._running += 1
        try:
            return await _Run(self, caller, code, timeout).go()
        finally:
            self._running -= 1


# -- what an agent is told ---------------------------------------------------------------------


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?" else text + "."


def failure_words(outcome: RunOutcome) -> str:
    """A script that did not finish, in plain words: what stopped it, which calls it made and how
    each ended, whether anything changed, and what it had written."""
    at = f" at line {outcome.line}" if outcome.line else ""
    message = _sentence(outcome.message)
    stage = outcome.stage
    if stage == "syntax":
        head = f"The script was not run: it has a syntax error{at}. {message}"
    elif stage == "types":
        head = (
            "The script was not run: it does not match the types of the functions it calls. "
            f"{message}"
        )
    elif stage == "refused":
        head = f"A call that Vrum refused stopped the script{at}: {message}"
    elif stage == "error":
        head = f"The script stopped with an error{at}: {message}"
    elif stage == "time":
        unit = "second" if outcome.seconds == 1 else "seconds"
        head = f"The script ran out of time ({outcome.seconds} {unit}) and was stopped."
    elif stage == "memory":
        head = "The script used more memory than a script may, so it was stopped."
    elif stage == "limit":
        head = f"The script was stopped because it passed a limit: {message}"
    else:
        head = message or UNEXPECTED
    parts = [" ".join(head.split())]
    if stage in ("syntax", "types"):
        parts.append("Nothing was changed.")
    elif not outcome.calls:
        parts.append("It made no calls, so nothing was changed.")
    else:
        parts.append(_calls_words(outcome))
        if outcome.changed_anything:
            parts.append(
                "Calls that change something finished, or may have: "
                + _listed(outcome.changing_calls)
                + ". Look at what is there now before you run it again, so that nothing is done "
                "twice."
            )
        else:
            parts.append("None of the calls changed anything.")
    if outcome.logs:
        shown = [line[:_LISTED_LOG_CHARS] for line in outcome.logs[-_MOST_LISTED_LOGS:]]
        parts.append("What it wrote before it stopped: " + " | ".join(shown))
    return " ".join(parts)


def _listed(names: Sequence[str]) -> str:
    """The functions that were called, each once, with how many times when more than one."""
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    parts = [f"{name} ({count} times)" if count > 1 else name for name, count in counts.items()]
    shown = ", ".join(parts[:_MOST_LISTED_CALLS])
    return shown + (
        f" and {len(parts) - _MOST_LISTED_CALLS} more" if len(parts) > _MOST_LISTED_CALLS else ""
    )


def _calls_words(outcome: RunOutcome) -> str:
    """The calls a script made, in order, each with how it ended; calls in a row that ended the
    same way are one entry with how many times. A refusal is told in the tool's own first
    sentence, unless it is the very sentence the script stopped on."""
    groups: list[tuple[tuple[str, str], int]] = []
    for call in outcome.calls:
        state = call.state
        if call.refusal and call.refusal not in outcome.message:
            state += f": {first_sentence(call.refusal, _LISTED_REFUSAL_CHARS)}"
        if groups and groups[-1][0] == (call.tool, state):
            groups[-1] = (groups[-1][0], groups[-1][1] + 1)
        else:
            groups.append(((call.tool, state), 1))
    shown = groups[:_MOST_LISTED_CALLS]
    parts = [
        f"{tool} ({state}{f', {count} times' if count > 1 else ''})"
        for (tool, state), count in shown
    ]
    more = len(outcome.calls) - sum(count for _, count in shown)
    return (
        "Calls it made, in order: " + ", ".join(parts) + (f" and {more} more" if more else "") + "."
    )


_GUIDE = f"""\
Run a TypeScript script against Vrum. `code` is the body of an async function: `vrum` and \
`console` are in scope, `await` works, and what you `return` (any JSON value) comes back as \
`result`. Call a function as `await vrum.<name>({{ ...arguments }})`: it is the tool of that name, \
with the same arguments, results and checks, recorded under your key's name. Before you write \
code, call `explore` with the names you will use: it gives their exact argument and result types, \
and the script is type checked before it runs.
A call Vrum refuses throws a `VrumError` whose message is the tool's own sentence; catch it to \
carry on. Every call is its own step and nothing is rolled back: if a script stops half way, what \
finished stays done, and the answer lists the calls and says whether anything changed, so do not \
simply run it again. Calls that only read may run together (`Promise.all`); calls that change \
something run one at a time, in order.
Limits: {DEFAULT_TIMEOUT} seconds (`timeout_seconds`, at most {MOST_TIMEOUT}), {MOST_CALLS} calls \
of which {MOST_WRITES} may change something, 256 KB of result, 16 KB of `console` output. No \
network, files or imports."""


def run_description(sdk: Sdk, functions: Sequence[str]) -> str:
    """What the tool `run` says about itself to a key whose functions are these. The first two
    thousand characters or so are all some clients show a model, so they hold the whole contract:
    the guide, and the names of the functions. The signatures, which give every argument and result
    exactly, follow when they fit (the standard set does); `explore` gives them in any case."""
    reads = [name for name in functions if sdk.reads_only(name)]
    changes = [name for name in functions if not sdk.reads_only(name)]
    lines = [_GUIDE]
    if reads:
        lines.append("Functions that only read: " + ", ".join(reads) + ".")
    if changes:
        lines.append("Functions that change something: " + ", ".join(changes) + ".")
    text = "\n".join(lines)
    signatures = sdk.declarations(functions, compact=True)
    if functions and len(text) + len(signatures) + 40 <= MOST_DESCRIPTION_CHARS:
        text += "\n\nSignatures:\n" + signatures
    return text


_MOST_NAMES = 50


def explore(
    sdk: Sdk, functions: Sequence[str], names: Sequence[str] | None, query: str | None
) -> Explored:
    """What `explore` answers: with nothing asked, a line for each function of the key; with names
    or words, the lines of those and their TypeScript in full."""
    own = set(functions)
    asked = list(names or [])[:_MOST_NAMES]
    wanted = bool(asked) or bool(query and query.strip())
    if not wanted:
        chosen = list(functions)
        not_functions: list[str] = []
    else:
        found = {name for name in asked if name in own}
        not_functions = [_shown_name(name) for name in asked if name not in own]
        if query and query.strip():
            found.update(sdk.find(functions, query))
        chosen = [name for name in functions if name in found]
    lines = [
        FunctionLine(name=entry.name, summary=entry.summary, only_reads=entry.reads_only)
        for entry in sdk.index(chosen)
    ]
    declarations = sdk.declarations(chosen, compact=False) if wanted and chosen else ""
    return Explored(functions=lines, declarations=declarations, not_functions=not_functions)
