"""Running a script for a key that runs code (code_mode.py), against a stand-in for the sandbox
service: a Unix socket server in the test that plays what the real one would say, well and badly.
No containers. The real sandbox, and the whole path from an MCP client to the database and back,
are proved in the integration tests."""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from vrum import code_mode
from vrum.accounts import (
    SCRIPT_KEY_NOW_TOOLS,
    SCRIPT_KEY_REVOKED,
    KeyAccess,
    code_tool_for_a_tools_key,
    key_not_allowed,
)
from vrum.code_mode import (
    ARGUMENTS_NOT_AN_OBJECT,
    BUSY,
    DEFAULT_TIMEOUT,
    MOST_CALLS,
    MOST_LOG_BYTES,
    MOST_WRITES,
    NOT_AVAILABLE,
    RESULT_NOT_JSON,
    RESULT_TOO_BIG,
    SCRIPT_TOO_BIG,
    TOO_MANY_CALLS,
    TOO_MANY_WRITES,
    CallRecord,
    CodeMode,
    RunOutcome,
    RunTag,
    explore,
    failure_words,
    run_description,
)
from vrum.common import Refusal
from vrum.config import ConfigError, Settings
from vrum.guard import Caller, GuardedServer
from vrum.identity import agent
from vrum.server import create_server
from vrum.surface import AGENT_SURFACE, AGENT_TOOLS

ENV = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret"}
SOCKET = "sandbox.sock"
A_LIMIT = 8 * 1024 * 1024
# A sandbox that does not read has a small buffer, so that what is written to it backs up.
A_SMALL_LIMIT = 256 * 1024

_server: GuardedServer | None = None


def the_server() -> GuardedServer:
    global _server
    if _server is None:
        _server = create_server(Settings.from_env(ENV))
    return _server


def a_caller(tools: frozenset[str] | None = AGENT_SURFACE, *, code_mode_on: bool = True) -> Caller:
    return Caller(7, agent("bot"), tools, tools is AGENT_SURFACE, code_mode_on, 3)


# -- the stand-in sandbox ---------------------------------------------------------------------


class Connection:
    """One run, as the sandbox sees it: what the app sends, and a way to answer. With `pump` off
    nothing is read from the app after the run message (`first`), as a sandbox that has stopped
    listening."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, pump: bool = True
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.received: list[dict[str, Any]] = []
        self.host_closed = asyncio.Event()
        self.host_closed_first = False
        # Set when the run is over, for a sandbox that does not read until then.
        self.release = asyncio.Event()
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.pump = asyncio.create_task(self._read_all()) if pump else None

    async def first(self) -> dict[str, Any]:
        """The run message, read straight from the socket (for a connection that does not pump)."""
        message: dict[str, Any] = json.loads(await self.reader.readline())
        return message

    async def _read_all(self) -> None:
        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    break
                message = json.loads(line)
                self.received.append(message)
                self._queue.put_nowait(message)
        except (ConnectionError, ValueError):
            pass
        finally:
            self.host_closed.set()
            self._queue.put_nowait(None)

    async def next(self, wait: float = 5.0) -> dict[str, Any] | None:
        return await asyncio.wait_for(self._queue.get(), wait)

    async def send(self, **message: Any) -> None:
        await self.send_raw(json.dumps(message).encode() + b"\n")

    async def send_raw(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    async def call(self, seq: int, name: str, args: Any = None) -> None:
        await self.send(op="call", seq=seq, name=name, args={} if args is None else args)


Script = Callable[[Connection], Awaitable[None]]


class Sandbox:
    def __init__(self) -> None:
        self.connections: list[Connection] = []
        self._server: asyncio.Server | None = None

    async def start(self, script: Script, *, pump: bool = True, limit: int = A_LIMIT) -> None:
        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            connection = Connection(reader, writer, pump=pump)
            self.connections.append(connection)
            try:
                await script(connection)
                if pump:
                    try:
                        await asyncio.wait_for(connection.host_closed.wait(), 3)
                        connection.host_closed_first = True
                    except TimeoutError:
                        pass
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                writer.close()

        self._server = await asyncio.start_unix_server(serve, path=SOCKET, limit=limit)

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await asyncio.wait_for(self._server.wait_closed(), 10)
        Path(SOCKET).unlink(missing_ok=True)


class Host:
    """The guard's end of one call, stood in for: it records each call and its timing, and
    answers as scripted."""

    def __init__(self) -> None:
        self.sdk = the_server().sdk
        self.calls: list[tuple[str, dict[str, Any], RunTag]] = []
        self.answers: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {}
        self.running_reads = 0
        self.running_writes = 0
        self.most_reads = 0
        self.most_writes = 0
        self.write_order: list[int] = []
        self.cancelled: list[str] = []

    def functions_for(self, tools: frozenset[str] | None) -> list[str]:
        return the_server().functions_for(tools)

    async def call_inner(
        self, caller: Caller, name: str, arguments: dict[str, Any], tag: RunTag
    ) -> Any:
        self.calls.append((name, arguments, tag))
        reads = self.sdk.reads_only(name)
        try:
            if reads:
                self.running_reads += 1
                self.most_reads = max(self.most_reads, self.running_reads)
            else:
                self.running_writes += 1
                self.most_writes = max(self.most_writes, self.running_writes)
                self.write_order.append(tag.seq)
            if name in self.answers:
                return await self.answers[name](arguments)
            return {"ok": True, "name": name}
        except asyncio.CancelledError:
            self.cancelled.append(name)
            raise
        finally:
            if reads:
                self.running_reads -= 1
            else:
                self.running_writes -= 1


async def play(
    script: Script,
    host: Host | None = None,
    *,
    code: str = "return 1",
    timeout: Any = DEFAULT_TIMEOUT,
    caller: Caller | None = None,
    pump: bool = True,
) -> tuple[RunOutcome, Sandbox, Host]:
    sandbox = Sandbox()
    host = host or Host()
    await sandbox.start(script, pump=pump, limit=A_LIMIT if pump else A_SMALL_LIMIT)
    try:
        outcome = await asyncio.wait_for(
            CodeMode(SOCKET, host).run(caller or a_caller(), code, timeout), 30
        )
        await asyncio.sleep(0)
        assert tasks_of_a_run() == [], "a run left tasks behind"
    finally:
        for connection in sandbox.connections:
            connection.release.set()
        await sandbox.stop()
    return outcome, sandbox, host


def tasks_of_a_run() -> list[str]:
    """What is still running of a script's run (its calls, its waits and its reads), by name."""
    left = []
    for task in asyncio.all_tasks():
        coro = task.get_coro()
        name = getattr(coro, "__qualname__", "")
        mine = name.startswith("_Run.") or name in ("Event.wait", "StreamReader.readline")
        if mine and not task.done():
            left.append(name)
    return left


def run_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: Script, **kwargs: Any
) -> tuple[RunOutcome, Sandbox, Host]:
    # A relative socket path keeps it short: a Unix socket path may hold about a hundred characters.
    monkeypatch.chdir(tmp_path)
    return asyncio.run(play(script, **kwargs))


async def finishing(connection: Connection, result: Any = None, **more: Any) -> None:
    await connection.send(op="done", result=result, logs=more.pop("logs", []), **more)


# -- a script that works ----------------------------------------------------------------------


def test_the_run_message_and_the_calls_and_the_end_of_a_script_that_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def script(connection: Connection) -> None:
        seen["run"] = await connection.next()
        await connection.call(1, "list_nodes", {"kind": "task"})
        seen["first"] = await connection.next()
        await connection.call(2, "add_note", {"text": "x"})
        seen["second"] = await connection.next()
        await finishing(connection, {"n": 2}, logs=["a", "b"])

    host = Host()

    async def listed(arguments: dict[str, Any]) -> Any:
        return {"items": [1, 2, 3], "echo": arguments}

    host.answers["list_nodes"] = listed
    outcome, sandbox, _ = run_it(tmp_path, monkeypatch, script, host=host, code="return 2;")
    run = seen["run"]
    assert run["op"] == "run" and run["code"] == "return 2;" and run["timeout"] == 60
    assert (run["max_calls"], run["max_result_bytes"], run["max_log_bytes"]) == (200, 262144, 16384)
    assert run["functions"] == the_server().functions_for(AGENT_SURFACE)
    assert run["functions"][0] == "next_work" and "list_nodes" in run["functions"]
    assert "declare const vrum: {" in run["declarations"] and "type Node = {" in run["declarations"]
    assert re.fullmatch(r"[0-9a-f]{12}", run["id"])
    assert seen["first"] == {
        "op": "reply",
        "seq": 1,
        "ok": True,
        "result": {"items": [1, 2, 3], "echo": {"kind": "task"}},
    }
    assert seen["second"] == {
        "op": "reply",
        "seq": 2,
        "ok": True,
        "result": {"ok": True, "name": "add_note"},
    }
    assert outcome.ok and outcome.result == {"n": 2} and outcome.logs == ["a", "b"]
    assert [(c.tool, c.state, c.refusal) for c in outcome.calls] == [
        ("list_nodes", "done", None),
        ("add_note", "done", None),
    ]
    assert outcome.changed_anything and outcome.changing_calls == ["add_note"]
    # The guard is told which run and which call, and a hash of the script, never its text.
    assert [tag.seq for _, _, tag in host.calls] == [1, 2]
    assert {tag.run_id for _, _, tag in host.calls} == {run["id"]}
    assert re.fullmatch(r"[0-9a-f]{12}", host.calls[0][2].script)
    assert all(connection.host_closed_first for connection in sandbox.connections)


def test_reads_run_together_and_writes_one_at_a_time_in_the_order_they_were_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def script(connection: Connection) -> None:
        await connection.next()
        for seq, name in enumerate(["list_nodes"] * 3 + ["add_note", "update_node", "add_link"], 1):
            await connection.call(seq, name)
        replies = [await connection.next() for _ in range(6)]
        assert replies[0] is not None
        assert sorted(reply["seq"] for reply in replies if reply) == [1, 2, 3, 4, 5, 6]
        await finishing(connection, None)

    host = Host()

    async def slow(arguments: dict[str, Any]) -> Any:
        await asyncio.sleep(0.05)
        return {}

    for name in ("list_nodes", "add_note", "update_node", "add_link"):
        host.answers[name] = slow
    outcome, _, _ = run_it(tmp_path, monkeypatch, script, host=host)
    assert outcome.ok and len(outcome.calls) == 6
    assert host.most_reads == 3
    assert host.most_writes == 1 and host.write_order == [4, 5, 6]


def test_a_refused_call_answers_with_the_tools_own_sentence_and_the_script_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentence = (
        "Error executing tool claim_node: story:3 is claimed by someone else. Nothing was changed."
    )
    seen: list[Any] = []

    async def script(connection: Connection) -> None:
        await connection.next()
        await connection.call(1, "claim_node", {"node": "story:3"})
        seen.append(await connection.next())
        await finishing(connection, "carried on")

    host = Host()

    async def refuse(arguments: dict[str, Any]) -> Any:
        raise Refusal(sentence)

    host.answers["claim_node"] = refuse
    outcome, _, _ = run_it(tmp_path, monkeypatch, script, host=host)
    assert seen == [{"op": "reply", "seq": 1, "ok": False, "error": sentence}]
    assert outcome.ok and outcome.result == "carried on"
    assert [(c.tool, c.state, c.refusal) for c in outcome.calls] == [
        ("claim_node", "refused", sentence)
    ]
    # A refused call changed nothing, so the script did not change anything.
    assert not outcome.changed_anything and outcome.changing_calls == []


def test_a_result_too_big_or_not_json_is_told_in_words_and_the_call_still_counts_as_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []

    async def script(connection: Connection) -> None:
        await connection.next()
        await connection.call(1, "list_nodes")
        await connection.call(2, "list_links")
        await connection.call(3, "list_rules", {"a": 1})
        await connection.call(4, "list_notes", 5)
        replies = [await connection.next() for _ in range(4)]
        seen.extend(sorted((reply for reply in replies if reply), key=lambda m: m["seq"]))
        await finishing(connection, None)

    host = Host()

    async def huge(arguments: dict[str, Any]) -> Any:
        return {"text": "x" * (code_mode.MOST_MESSAGE_BYTES + 1)}

    async def nan(arguments: dict[str, Any]) -> Any:
        return {"n": float("nan")}

    host.answers["list_nodes"] = huge
    host.answers["list_links"] = nan
    outcome, _, _ = run_it(tmp_path, monkeypatch, script, host=host)
    assert [(m["seq"], m["ok"]) for m in seen] == [(1, False), (2, False), (3, True), (4, False)]
    assert seen[0]["error"] == RESULT_TOO_BIG and seen[1]["error"] == RESULT_NOT_JSON
    assert seen[3]["error"] == ARGUMENTS_NOT_AN_OBJECT
    assert [c.state for c in outcome.calls] == ["done", "done", "done", "refused"]
    assert [name for name, _, _ in host.calls] == ["list_nodes", "list_links", "list_rules"]


def test_a_name_that_is_not_a_function_is_not_repeated_in_the_list_of_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def script(connection: Connection) -> None:
        await connection.next()
        await connection.call(1, "a name; with odd <characters>")
        await connection.next()
        await finishing(connection, None)

    host = Host()

    async def nothing(arguments: dict[str, Any]) -> Any:
        raise Refusal("This agent key (bot) has no function called that name.")

    host.answers["a name; with odd <characters>"] = nothing
    outcome, _, _ = run_it(tmp_path, monkeypatch, script, host=host)
    assert [c.tool for c in outcome.calls] == ["(not a function)"]


# -- limits the app keeps itself --------------------------------------------------------------


def test_the_two_hundred_and_first_call_is_refused_in_words_and_the_script_may_go_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies: dict[int, Any] = {}

    async def script(connection: Connection) -> None:
        await connection.next()
        for seq in range(1, MOST_CALLS + 2):
            await connection.call(seq, "list_nodes")
        for _ in range(MOST_CALLS + 1):
            message = await connection.next()
            assert message is not None
            replies[message["seq"]] = message
        await finishing(connection, "ok")

    outcome, _, host = run_it(tmp_path, monkeypatch, script)
    assert outcome.ok and len(host.calls) == MOST_CALLS and len(outcome.calls) == MOST_CALLS
    assert replies[MOST_CALLS + 1] == {
        "op": "reply",
        "seq": MOST_CALLS + 1,
        "ok": False,
        "error": TOO_MANY_CALLS,
    }
    assert all(replies[seq]["ok"] for seq in range(1, MOST_CALLS + 1))


def test_the_hundred_and_first_write_is_refused_but_reads_still_go_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies: dict[int, Any] = {}

    async def script(connection: Connection) -> None:
        await connection.next()
        for seq in range(1, MOST_WRITES + 2):
            await connection.call(seq, "add_note")
        await connection.call(MOST_WRITES + 2, "list_nodes")
        for _ in range(MOST_WRITES + 2):
            message = await connection.next()
            assert message is not None
            replies[message["seq"]] = message
        await finishing(connection, None)

    outcome, _, host = run_it(tmp_path, monkeypatch, script)
    assert outcome.ok and len(host.calls) == MOST_WRITES + 1
    assert replies[MOST_WRITES + 1]["error"] == TOO_MANY_WRITES
    assert replies[MOST_WRITES + 2]["ok"] is True
    assert len(outcome.changing_calls) == MOST_WRITES


def test_a_script_that_keeps_calling_past_the_limit_is_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def script(connection: Connection) -> None:
        await connection.next()
        for seq in range(1, MOST_CALLS + 40):
            await connection.call(seq, "list_nodes")
        while await connection.next() is not None:
            pass

    outcome, sandbox, _ = run_it(tmp_path, monkeypatch, script)
    assert not outcome.ok and outcome.stage == "limit"
    assert "kept calling after it had passed the limit" in outcome.message
    assert {"op": "cancel"} in sandbox.connections[0].received


def test_the_wall_clock_is_kept_by_the_app_and_a_hung_call_is_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(code_mode, "GRACE_SECONDS", 0.0)

    async def script(connection: Connection) -> None:
        await connection.next()
        await connection.call(1, "add_note")
        await connection.call(2, "list_nodes")
        await connection.call(3, "update_node")
        while await connection.next() is not None:
            pass

    host = Host()

    async def hang(arguments: dict[str, Any]) -> Any:
        await asyncio.sleep(60)

    async def quick(arguments: dict[str, Any]) -> Any:
        return {}

    host.answers["add_note"] = hang
    host.answers["list_nodes"] = quick
    host.answers["update_node"] = quick
    started = time.monotonic()
    outcome, sandbox, host = run_it(tmp_path, monkeypatch, script, host=host, timeout=1)
    assert time.monotonic() - started < 10
    assert not outcome.ok and outcome.stage == "time"
    assert host.cancelled == ["add_note"]
    states = {c.tool: c.state for c in outcome.calls}
    assert states == {
        "add_note": "not finished",
        "list_nodes": "done",
        "update_node": "not finished",
    }
    # The first write was sent and not answered, and the second never started: both may have changed
    # something (the second waited for the lock, so it never did, but the script is told to look).
    assert outcome.changed_anything and "add_note" in outcome.changing_calls
    assert {"op": "cancel"} in sandbox.connections[0].received
    words = failure_words(outcome)
    assert "ran out of time (1 second)" in words and "add_note (not finished)" in words
    assert "Look at what is there now before you run it again" in words


def test_a_fifth_script_at_once_is_refused_at_once_and_a_sixth_runs_when_one_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> list[RunOutcome]:
        release = asyncio.Event()

        async def hold(connection: Connection) -> None:
            await connection.next()
            await release.wait()
            await finishing(connection, None)

        sandbox = Sandbox()
        await sandbox.start(hold)
        mode = CodeMode(SOCKET, Host())
        first = [asyncio.create_task(mode.run(a_caller(), "return 1", 60)) for _ in range(4)]
        await asyncio.sleep(0.3)
        with pytest.raises(Refusal) as busy:
            await mode.run(a_caller(), "return 1", 60)
        assert str(busy.value) == BUSY
        release.set()
        done = await asyncio.gather(*first)
        done.append(await mode.run(a_caller(), "return 1", 60))
        await sandbox.stop()
        return list(done)

    monkeypatch.chdir(tmp_path)
    outcomes = asyncio.run(scenario())
    assert len(outcomes) == 5 and all(outcome.ok for outcome in outcomes)


# -- what cannot even start -------------------------------------------------------------------


def test_with_no_sandbox_the_words_say_so_and_nothing_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for path in ("not-there.sock", str(tmp_path)):
        with pytest.raises(Refusal) as refused:
            asyncio.run(CodeMode(path, Host()).run(a_caller(), "return 1", 60))
        assert str(refused.value) == NOT_AVAILABLE
    assert "Running code is not available: the sandbox service is not running." in NOT_AVAILABLE
    assert "explore still works" in NOT_AVAILABLE


@pytest.mark.parametrize("timeout", [0, 121, -1, True, "60", 1.5, None])
def test_a_bad_time_limit_is_refused_before_anything_is_sent(timeout: Any) -> None:
    with pytest.raises(Refusal, match="timeout_seconds has to be a whole number of seconds"):
        asyncio.run(CodeMode(SOCKET, Host()).run(a_caller(), "return 1", timeout))


def test_an_empty_script_a_huge_script_and_a_caller_that_does_not_run_code_are_refused() -> None:
    mode = CodeMode(SOCKET, Host())
    for empty in ("", "  \n ", None, 5):
        with pytest.raises(Refusal, match="There is no script to run"):
            asyncio.run(mode.run(a_caller(), empty, 60))
    with pytest.raises(Refusal) as big:
        asyncio.run(mode.run(a_caller(), "x" * (code_mode.MOST_SCRIPT_BYTES + 1), 60))
    assert str(big.value) == SCRIPT_TOO_BIG
    for caller in (a_caller(code_mode_on=False), a_caller(None, code_mode_on=True)):
        with pytest.raises(Refusal, match="The board does its work one page at a time"):
            asyncio.run(mode.run(caller, "return 1", 60))


def test_a_script_that_is_small_but_would_make_too_long_a_line_is_refused_and_the_connection_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def script(connection: Connection) -> None:
        await connection.next()

    with pytest.raises(Refusal) as refused:
        run_it(tmp_path, monkeypatch, script, code="\x01" * code_mode.MOST_SCRIPT_BYTES)
    assert str(refused.value) == SCRIPT_TOO_BIG


# -- a sandbox that misbehaves ----------------------------------------------------------------

Misbehaviour = Callable[[Connection], Awaitable[None]]


async def sends_garbage(connection: Connection) -> None:
    await connection.next()
    await connection.send_raw(b"this is not json\n")


async def sends_a_list(connection: Connection) -> None:
    await connection.next()
    await connection.send_raw(b'["op", "call"]\n')


async def sends_an_op_that_is_not_text(connection: Connection) -> None:
    await connection.next()
    await connection.send(op=5)


async def sends_an_unknown_op(connection: Connection) -> None:
    await connection.next()
    await connection.send(op="hello")


async def sends_a_reply(connection: Connection) -> None:
    await connection.next()
    await connection.send(op="reply", seq=1, ok=True, result=1)


async def sends_too_much(connection: Connection) -> None:
    await connection.next()
    await connection.send_raw(b'{"op":"done","result":"' + b"x" * (2 * 1024 * 1024) + b'"}\n')


async def sends_a_line_that_never_ends(connection: Connection) -> None:
    await connection.next()
    await connection.send_raw(b"x" * (2 * 1024 * 1024))


async def numbers_its_calls_wrong(connection: Connection) -> None:
    await connection.next()
    await connection.call(5, "list_nodes")


async def numbers_a_call_with_a_boolean(connection: Connection) -> None:
    await connection.next()
    await connection.send(op="call", seq=True, name="list_nodes", args={})


async def names_a_call_with_a_number(connection: Connection) -> None:
    await connection.next()
    await connection.send(op="call", seq=1, name=7, args={})


async def stops_without_finishing(connection: Connection) -> None:
    await connection.next()
    connection.writer.close()


async def stops_in_the_middle_of_a_line(connection: Connection) -> None:
    await connection.next()
    await connection.send_raw(b'{"op":"done","res')
    connection.writer.close()


async def reports_logs_that_are_not_a_list(connection: Connection) -> None:
    await connection.next()
    await connection.send(op="done", result=1, logs="a string")


async def reports_a_result_over_the_cap(connection: Connection) -> None:
    await connection.next()
    await connection.send(op="done", result="x" * (code_mode.MOST_RESULT_BYTES + 1), logs=[])


@pytest.mark.parametrize(
    ("behaviour", "stage", "words"),
    [
        (sends_garbage, "sandbox", "unexpected"),
        (sends_a_list, "sandbox", "unexpected"),
        (sends_an_op_that_is_not_text, "sandbox", "unexpected"),
        (sends_an_unknown_op, "sandbox", "unexpected"),
        (sends_a_reply, "sandbox", "unexpected"),
        (sends_too_much, "sandbox", "unexpected"),
        (sends_a_line_that_never_ends, "sandbox", "unexpected"),
        (numbers_its_calls_wrong, "sandbox", "unexpected"),
        (numbers_a_call_with_a_boolean, "sandbox", "unexpected"),
        (names_a_call_with_a_number, "sandbox", "unexpected"),
        (stops_without_finishing, "sandbox", "stopped before the script finished"),
        (stops_in_the_middle_of_a_line, "sandbox", "stopped before the script finished"),
        (reports_logs_that_are_not_a_list, "sandbox", "unexpected"),
        (reports_a_result_over_the_cap, "limit", "larger than 256 KB"),
    ],
)
def test_a_sandbox_that_misbehaves_ends_the_run_in_words_and_the_connection_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    behaviour: Misbehaviour,
    stage: str,
    words: str,
) -> None:
    outcome, sandbox, host = run_it(tmp_path, monkeypatch, behaviour)
    assert not outcome.ok and outcome.stage == stage
    assert words in outcome.message
    assert host.calls == []
    assert "nothing was changed" in failure_words(outcome).lower()
    # The app closed its end, whatever the sandbox did (one that closes first proves nothing).
    assert sandbox.connections[0].host_closed.is_set()


def test_a_fail_message_is_trusted_for_its_words_but_not_for_its_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def script(connection: Connection) -> None:
        await connection.next()
        await connection.send(
            op="fail",
            stage="a stage that is made up",
            message="  Two\n lines  ",
            line=-3,
            logs=[1, 2],
        )

    outcome, _, _ = run_it(tmp_path, monkeypatch, script)
    assert (outcome.stage, outcome.message, outcome.line, outcome.logs) == (
        "sandbox",
        "Two lines",
        None,
        [],
    )


def test_every_stage_a_sandbox_reports_is_kept_with_its_line_and_its_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for stage in ("syntax", "types", "refused", "error", "time", "memory", "limit", "sandbox"):

        async def script(connection: Connection, stage: str = stage) -> None:
            await connection.next()
            await connection.send(
                op="fail", stage=stage, message="Plain words.", line=7, logs=["hi"]
            )

        outcome, _, _ = run_it(tmp_path, monkeypatch, script)
        assert (outcome.ok, outcome.stage, outcome.line, outcome.logs) == (False, stage, 7, ["hi"])


def test_a_done_with_calls_still_in_flight_waits_for_them_and_lists_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def script(connection: Connection) -> None:
        await connection.next()
        await connection.call(1, "add_note")
        await finishing(connection, "early")

    host = Host()

    async def slow(arguments: dict[str, Any]) -> Any:
        await asyncio.sleep(0.2)
        return {"id": 1}

    host.answers["add_note"] = slow
    outcome, _, _ = run_it(tmp_path, monkeypatch, script, host=host)
    assert outcome.ok and outcome.result == "early"
    assert [(c.tool, c.state) for c in outcome.calls] == [("add_note", "done")]
    assert outcome.changed_anything


def test_a_call_after_done_is_not_read_and_the_logs_are_cut_to_sixteen_kilobytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def script(connection: Connection) -> None:
        await connection.next()
        lines = ["x" * 1000] * 40
        await connection.send(op="done", result=None, logs=lines, truncated_logs=False)
        with suppress(ConnectionError):
            await connection.call(1, "add_note")

    outcome, _, host = run_it(tmp_path, monkeypatch, script)
    assert outcome.ok and host.calls == []
    assert outcome.logs[-1].startswith("(more was written than the 16 KB")
    assert sum(len(line) + 1 for line in outcome.logs[:-1]) <= MOST_LOG_BYTES


def test_logs_the_sandbox_says_were_cut_are_marked_as_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def script(connection: Connection) -> None:
        await connection.next()
        await connection.send(op="done", result=None, logs=["a"], truncated_logs=True)

    outcome, _, _ = run_it(tmp_path, monkeypatch, script)
    assert outcome.logs[0] == "a" and "so the rest was cut" in outcome.logs[1]


# -- the log -----------------------------------------------------------------------------------


def fields_of(caplog: pytest.LogCaptureFixture, name: str) -> list[dict[str, Any]]:
    return [
        record.__dict__["fields"]
        for record in caplog.records
        if getattr(record, "fields", None) is not None and record.getMessage() == name
    ]


def test_a_run_leaves_one_line_with_its_number_and_no_text_and_repeated_refusals_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="vrum.code")

    async def script(connection: Connection) -> None:
        await connection.next()
        for seq in range(1, 4):
            await connection.call(seq, "claim_node", {"node": "story:99999"})
        for _ in range(3):
            await connection.next()
        await finishing(connection, None)

    host = Host()

    async def refuse(arguments: dict[str, Any]) -> Any:
        raise Refusal("Error executing tool claim_node: not yours.")

    host.answers["claim_node"] = refuse
    secret_looking_text = "do-not-log-this-script-text"
    outcome, _, _ = run_it(
        tmp_path, monkeypatch, script, host=host, code=f"// {secret_looking_text}\nreturn 1"
    )
    assert outcome.ok
    finished = fields_of(caplog, "run_finished")
    assert len(finished) == 1
    line = finished[0]
    assert line["outcome"] == "ok" and line["calls"] == 3 and line["refused_calls"] == 3
    assert line["actor"] == "bot" and line["project"] == 7
    assert re.fullmatch(r"[0-9a-f]{12}", line["run_id"]) and re.fullmatch(
        r"[0-9a-f]{12}", line["script"]
    )
    repeated = fields_of(caplog, "run_refused_calls")
    assert (
        len(repeated) == 1 and repeated[0]["count"] == 3 and repeated[0]["run_id"] == line["run_id"]
    )
    everything = str([record.__dict__ for record in caplog.records])
    assert secret_looking_text not in everything and "story:99999" not in everything


# -- ping --------------------------------------------------------------------------------------


def test_ping_gives_the_version_the_sandbox_says_and_none_when_it_does_not_answer_well(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> list[str | None]:
        answers: list[str | None] = []
        for reply in (b'{"op":"pong","deno":"2.9.8"}\n', b"garbage\n", b'{"op":"hello"}\n', b""):

            async def script(connection: Connection, reply: bytes = reply) -> None:
                await connection.next()
                if reply:
                    await connection.send_raw(reply)
                connection.writer.close()

            sandbox = Sandbox()
            await sandbox.start(script)
            answers.append(await CodeMode(SOCKET, Host()).ping())
            await sandbox.stop()
            Path(SOCKET).unlink(missing_ok=True)
        answers.append(await CodeMode("not-there.sock", Host()).ping())
        return answers

    monkeypatch.chdir(tmp_path)
    assert asyncio.run(scenario()) == ["2.9.8", None, None, None, None]


def test_ping_does_not_wait_longer_than_a_second_for_a_sandbox_that_says_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> tuple[str | None, float]:
        async def silent(connection: Connection) -> None:
            await connection.next()
            await asyncio.sleep(3)

        sandbox = Sandbox()
        await sandbox.start(silent)
        loop = asyncio.get_running_loop()
        started = loop.time()
        answer = await CodeMode(SOCKET, Host()).ping()
        waited = loop.time() - started
        await sandbox.stop()
        return answer, waited

    monkeypatch.chdir(tmp_path)
    answer, waited = asyncio.run(scenario())
    assert answer is None and waited < 2.5


# -- what an agent is told ---------------------------------------------------------------------


def outcome_of(stage: str | None, **more: Any) -> RunOutcome:
    return RunOutcome(ok=stage is None, stage=stage, **more)


def test_a_script_that_did_not_finish_is_described_in_plain_words_for_each_stage() -> None:
    syntax = failure_words(outcome_of("syntax", message="Unexpected token", line=3))
    assert syntax == (
        "The script was not run: it has a syntax error at line 3. Unexpected token. "
        "Nothing was changed."
    )
    types = failure_words(
        outcome_of(
            "types", message="Line 2: Property 'x' does not exist. Did you mean 'y'?", line=2
        )
    )
    assert types.startswith(
        "The script was not run: it does not match the types of the functions it calls. Line 2:"
    )
    assert "Did you mean 'y'?" in types and types.endswith("Nothing was changed.")
    refused = failure_words(
        outcome_of(
            "refused",
            message="Error executing tool claim_node: it is claimed.",
            line=9,
            calls=[CallRecord(tool="claim_node", state="refused", ms=4, refusal="it is claimed.")],
        )
    )
    assert refused.startswith(
        "A call that Vrum refused stopped the script at line 9: "
        "Error executing tool claim_node: it is claimed."
    )
    # The sentence the script stopped on is not told twice; a refusal it caught is told in full
    # sentence, cut to its first.
    assert "Calls it made, in order: claim_node (refused)." in refused
    caught = failure_words(
        outcome_of(
            "error",
            message="boom",
            calls=[
                CallRecord(
                    tool="add_note",
                    state="refused",
                    ms=1,
                    refusal="Error executing tool add_note: Not allowed. Ask the owner.",
                )
            ],
        )
    )
    assert "add_note (refused: Error executing tool add_note: Not allowed.)" in caught
    assert refused.endswith("None of the calls changed anything.")
    assert failure_words(outcome_of("error", message="x is not defined", line=4)).startswith(
        "The script stopped with an error at line 4: x is not defined. It made no calls, so"
    )
    assert "ran out of time (60 seconds)" in failure_words(outcome_of("time"))
    assert "more memory than a script may" in failure_words(outcome_of("memory"))
    assert "passed a limit: Too many." in failure_words(outcome_of("limit", message="Too many"))
    assert failure_words(
        outcome_of("sandbox", message="Scripts are busy. Try again in a moment.")
    ).startswith("Scripts are busy. Try again in a moment.")


def test_a_failed_run_says_which_calls_ran_whether_anything_changed_and_what_it_wrote() -> None:
    calls = [
        CallRecord(tool="next_work", state="done", ms=3),
        CallRecord(tool="add_note", state="done", ms=5),
        CallRecord(tool="finish_work", state="not finished", ms=0),
    ]
    words = failure_words(
        outcome_of(
            "time",
            calls=calls,
            changed_anything=True,
            changing_calls=["next_work", "add_note", "finish_work"],
            logs=[f"line {number}" for number in range(8)],
        )
    )
    assert (
        "Calls it made, in order: next_work (done), add_note (done), finish_work (not finished)."
        in words
    )
    assert (
        "Calls that change something finished, or may have: next_work, add_note, finish_work."
        in words
    )
    assert "before you run it again" in words
    counted = failure_words(
        outcome_of(
            "error",
            message="boom",
            calls=[CallRecord(tool="add_note", state="done", ms=1)] * 3
            + [CallRecord(tool="finish_work", state="done", ms=1)],
            changed_anything=True,
            changing_calls=["add_note", "add_note", "add_note", "finish_work"],
        )
    )
    assert "finished, or may have: add_note (3 times), finish_work." in counted
    assert words.endswith(
        "What it wrote before it stopped: line 3 | line 4 | line 5 | line 6 | line 7"
    )
    # Calls in a row that ended the same way are one entry, and at most thirty entries are told.
    same = failure_words(
        outcome_of(
            "error",
            message="boom",
            calls=[CallRecord(tool="list_nodes", state="done", ms=1)] * 35,
        )
    )
    assert "Calls it made, in order: list_nodes (done, 35 times)." in same
    mixed = failure_words(
        outcome_of(
            "error",
            message="boom",
            calls=[
                CallRecord(tool=name, state="done", ms=1) for name in ["get_node", "search"] * 18
            ],
        )
    )
    assert mixed.count("get_node (done)") + mixed.count("search (done)") == 30
    assert "and 6 more." in mixed


# -- the description of run, and explore -------------------------------------------------------


def test_the_sandbox_socket_has_a_default_can_be_set_and_must_be_a_full_path() -> None:
    assert Settings.from_env(ENV).sandbox_socket == "/run/sandbox/run.sock"
    assert Settings.from_env({**ENV, "VRUM_SANDBOX_SOCKET": "  "}).sandbox_socket == (
        "/run/sandbox/run.sock"
    )
    assert Settings.from_env({**ENV, "VRUM_SANDBOX_SOCKET": "/run/x/y.sock"}).sandbox_socket == (
        "/run/x/y.sock"
    )
    for wrong in ("run.sock", "./run.sock", "/run/a\nb"):
        with pytest.raises(ConfigError, match="VRUM_SANDBOX_SOCKET must be the full path"):
            Settings.from_env({**ENV, "VRUM_SANDBOX_SOCKET": wrong})


def test_the_description_of_run_holds_the_whole_contract_in_its_first_two_thousand_characters() -> (
    None
):
    sdk = the_server().sdk
    functions = the_server().functions_for(None)
    text = run_description(sdk, functions)
    zone, _, signatures = text.partition("\n\nSignatures:\n")
    assert (
        len(zone) < 1900
        and signatures.startswith("type ")
        and "declare const vrum: {" in signatures
    )
    for rule in (
        "`explore`",
        "`VrumError`",
        "nothing is rolled back",
        "60 seconds",
        "200 calls",
        "100",
    ):
        assert rule in zone
    reads = zone.split("Functions that only read: ")[1].split(".\n")[0].split(", ")
    changes = zone.split("Functions that change something: ")[1].split(".")[0].split(", ")
    assert sorted(reads + changes) == sorted(AGENT_TOOLS)
    assert all(sdk.reads_only(name) for name in reads) and not any(
        sdk.reads_only(name) for name in changes
    )
    assert len(text) < code_mode.MOST_DESCRIPTION_CHARS


def test_the_description_of_run_names_only_the_functions_the_key_has() -> None:
    sdk = the_server().sdk
    own = frozenset({"list_nodes", "add_note"})
    text = run_description(sdk, the_server().functions_for(own))
    assert "Functions that only read: list_nodes." in text
    assert "Functions that change something: add_note." in text
    declared_members = re.findall(r"^  (\w+)\(", text, re.M)
    assert declared_members == ["list_nodes", "add_note"]
    everything = {tool.name for tool in the_server()._tool_manager.list_tools()}
    others = everything - own - {"explore", "run"}
    for other in others:
        assert not re.search(
            rf"(?<![A-Za-z0-9_`.]){other}(?![A-Za-z0-9_])", text.split("Signatures:")[0]
        ), other


def test_a_key_with_more_functions_than_fit_gets_their_names_and_explore_gives_the_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = the_server().sdk
    everything = frozenset(sdk.names)
    monkeypatch.setattr(code_mode, "MOST_DESCRIPTION_CHARS", 8000)
    text = run_description(sdk, the_server().functions_for(everything))
    assert "Signatures:" not in text and "declare const vrum" not in text
    assert "`explore`" in text and "export_graph" in text and len(text) < 2400


def test_explore_lists_the_functions_describes_the_ones_asked_for_and_finds_by_words() -> None:
    sdk = the_server().sdk
    functions = the_server().functions_for(None)
    everything = explore(sdk, functions, None, None)
    assert [line.name for line in everything.functions] == functions
    assert everything.declarations == "" and everything.not_functions == []
    assert {line.only_reads for line in everything.functions} == {True, False}
    # A name that is not one of the key's functions is said so, and an odd one is not repeated.
    asked = explore(sdk, functions, ["get_node", "export_graph", "x; y", "get_node"], None)
    assert [line.name for line in asked.functions] == ["get_node"]
    assert asked.not_functions == ["export_graph", "(not a function)"]
    assert "  get_node(args: {" in asked.declarations and "export_graph" not in asked.declarations
    assert "The item's reference" in asked.declarations
    found = explore(sdk, functions, None, "acceptance criteria")
    assert {"create_rule", "list_rules"} <= {line.name for line in found.functions}
    assert len(found.functions) <= 10 and "type Rule = {" in found.declarations
    both = explore(sdk, functions, ["search"], "claim")
    assert {"search", "claim_node"} <= {line.name for line in both.functions}
    nothing = explore(sdk, functions, None, "zebra")
    assert nothing.functions == [] and nothing.declarations == ""
    assert explore(sdk, functions, [], "  ").functions != []


# -- the tool `run`, from a key's call to the sandbox and back -----------------------------------


class Keys:
    """Keys by name: what each may call, and whether it runs code. A key is taken away (revoked)
    or changed by changing `held`, as the owner does on the board while a script runs."""

    def __init__(self, **held: tuple[frozenset[str] | None, bool]) -> None:
        self.held = {
            f"vrk_{name}": KeyAccess(7, index, name, tools, by_code)
            for index, (name, (tools, by_code)) in enumerate(held.items())
        }
        self.asked: list[int] = []

    async def project_for_key(self, key: str) -> KeyAccess | None:
        return self.held.get(key)

    async def access_of_key(self, key_id: int) -> KeyAccess | None:
        self.asked.append(key_id)
        return next((one for one in self.held.values() if one.key_id == key_id), None)

    def revoke(self, name: str) -> None:
        del self.held[f"vrk_{name}"]

    def change(self, name: str, *, tools: frozenset[str] | None = None, code: bool = True) -> None:
        old = self.held[f"vrk_{name}"]
        self.held[f"vrk_{name}"] = KeyAccess(old.project_id, old.key_id, name, tools, code)


def server_that_runs_code(
    allowed: frozenset[str] = frozenset({"echo_list", "refuses"}),
) -> Any:
    """A server pointed at the stand-in sandbox, with tools of its own that need no database and
    two keys: one that runs code (and may call `allowed`) and one that does not. What `jot` was
    asked to write is in `server.jotted`, and `weighty` gives a result of the size it is told."""
    server: Any = create_server(replace(Settings.from_env(ENV), sandbox_socket=SOCKET))
    server.jotted = []

    async def echo_list(n: int) -> list[str]:
        """Give a list."""
        return ["a"] * n

    async def refuses() -> dict[str, int]:
        """Always refuses."""
        raise Refusal("Nobody may do this. Nothing was changed.")

    async def jot(text: str, wait: float = 0.0) -> dict[str, int]:
        """Write a line down, after waiting a moment."""
        await asyncio.sleep(wait)
        server.jotted.append(text)
        return {"lines": len(server.jotted)}

    async def weighty(kilobytes: int) -> dict[str, str]:
        """Give a result of this many kilobytes."""
        return {"text": "x" * (kilobytes * 1024)}

    server.add_tool(echo_list, annotations=ToolAnnotations(read_only_hint=True))
    server.add_tool(refuses)
    server.add_tool(jot)
    server.add_tool(weighty, annotations=ToolAnnotations(read_only_hint=True))
    server.accounts = Keys(coder=(allowed, True), plain=(None, False))
    return server


def sending(key: str) -> Any:
    headers = {"authorization": f"Bearer {key}"}
    return SimpleNamespace(headers=headers, request=SimpleNamespace(headers=headers))


async def through_the_tool(
    script: Script, arguments: dict[str, Any], key: str = "vrk_coder"
) -> tuple[Any, Sandbox]:
    sandbox = Sandbox()
    await sandbox.start(script)
    try:
        server = server_that_runs_code()
        return await server.call_tool("run", arguments, sending(key)), sandbox
    finally:
        await sandbox.stop()


def test_run_gives_back_what_the_script_returned_wrote_and_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def script(connection: Connection) -> None:
        seen["run"] = await connection.next()
        await connection.call(1, "echo_list", {"n": 2})
        seen["first"] = await connection.next()
        await connection.call(2, "refuses")
        seen["second"] = await connection.next()
        await finishing(connection, {"list": ["a", "a"]}, logs=["hello"])

    monkeypatch.chdir(tmp_path)
    result, sandbox = asyncio.run(
        through_the_tool(script, {"code": "return 1", "timeout_seconds": 30})
    )
    assert seen["run"]["functions"] == ["echo_list", "refuses"] and seen["run"]["timeout"] == 30
    assert seen["first"] == {"op": "reply", "seq": 1, "ok": True, "result": ["a", "a"]}
    sentence = "Error executing tool refuses: Nobody may do this. Nothing was changed."
    assert seen["second"] == {"op": "reply", "seq": 2, "ok": False, "error": sentence}
    content = result.structured_content
    assert content["result"] == {"list": ["a", "a"]} and content["logs"] == ["hello"]
    assert [(c["tool"], c["state"], c["refusal"]) for c in content["calls"]] == [
        ("echo_list", "done", None),
        ("refuses", "refused", sentence),
    ]
    assert all(isinstance(c["ms"], int) for c in content["calls"])
    assert all(connection.host_closed_first for connection in sandbox.connections)


def test_a_script_that_failed_is_a_refusal_in_plain_words_with_its_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def script(connection: Connection) -> None:
        await connection.next()
        await connection.call(1, "echo_list", {"n": 1})
        await connection.next()
        await connection.send(
            op="fail", stage="error", message="x is not defined", line=3, logs=["got here"]
        )

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ToolError) as failed:
        asyncio.run(through_the_tool(script, {"code": "return x"}))
    assert str(failed.value) == (
        "Error executing tool run: The script stopped with an error at line 3: x is not defined. "
        "Calls it made, in order: echo_list (done). None of the calls changed anything. "
        "What it wrote before it stopped: got here"
    )


def test_a_secret_in_the_script_is_refused_before_the_sandbox_hears_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def script(connection: Connection) -> None:
        await connection.next()

    sandbox_seen: list[Sandbox] = []

    async def scenario() -> None:
        sandbox = Sandbox()
        sandbox_seen.append(sandbox)
        await sandbox.start(script)
        try:
            with pytest.raises(ToolError) as refused:
                await server_that_runs_code().call_tool(
                    "run",
                    {"code": "const k = 'ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2 + "';"},
                    sending("vrk_coder"),
                )
            assert str(refused.value).startswith(
                "Error executing tool run: The code field contains"
            )
            assert "ghp_" not in str(refused.value)
        finally:
            await sandbox.stop()

    monkeypatch.chdir(tmp_path)
    asyncio.run(scenario())
    assert sandbox_seen[0].connections == []


def test_run_says_plainly_when_there_is_no_sandbox_and_a_tools_key_is_refused_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ToolError) as nothing:
        asyncio.run(
            server_that_runs_code().call_tool("run", {"code": "return 1"}, sending("vrk_coder"))
        )
    assert str(nothing.value) == "Error executing tool run: " + NOT_AVAILABLE

    async def never(connection: Connection) -> None:
        raise AssertionError("a key that does not run code must not reach the sandbox")

    with pytest.raises(ToolError) as refused:
        asyncio.run(through_the_tool(never, {"code": "return 1"}, key="vrk_plain"))
    assert str(refused.value) == "Error executing tool run: " + code_tool_for_a_tools_key(
        "plain", "run"
    )


def test_a_bad_time_limit_and_an_empty_script_are_refused_through_the_tool_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    server = server_that_runs_code()
    for arguments, words in (
        ({"code": "return 1", "timeout_seconds": 500}, "timeout_seconds has to be a whole number"),
        ({"code": "   "}, "There is no script to run"),
    ):
        with pytest.raises(ToolError) as refused:
            asyncio.run(server.call_tool("run", arguments, sending("vrk_coder")))
        assert words in str(refused.value)


# -- a key that is changed while its script runs ------------------------------------------------


async def through(server: Any, script: Script, box: Sandbox, key: str = "vrk_coder") -> Any:
    """`run` through the server's own tool, with `script` as the sandbox, and nothing left of the
    run behind when it is over."""
    await box.start(script)
    try:
        result = await server.call_tool("run", {"code": "return 1"}, sending(key))
        await asyncio.sleep(0)
        assert tasks_of_a_run() == [], "a run left tasks behind"
        return result
    finally:
        for connection in box.connections:
            connection.release.set()
        await box.stop()


async def stops(server: Any, script: Script, box: Sandbox) -> str:
    """The words of a run that must not finish, and that it did not wait for its time limit."""
    started = time.monotonic()
    try:
        await through(server, script, box)
    except ToolError as error:
        assert time.monotonic() - started < 5, "the run waited for its time limit"
        return str(error)
    raise AssertionError("the run should have been stopped")


async def replies_to(
    connection: Connection, *calls: tuple[str, dict[str, Any]], first: int = 1
) -> list[Any]:
    """Make these calls one after the other, each answered before the next (numbered from
    `first`), and give the answers."""
    answers = []
    for number, (name, arguments) in enumerate(calls, first):
        await connection.call(number, name, arguments)
        answers.append(await connection.next())
    return answers


def test_a_key_revoked_while_its_script_runs_stops_it_at_its_next_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = server_that_runs_code(frozenset({"echo_list", "jot"}))
    box = Sandbox()

    async def script(connection: Connection) -> None:
        await connection.next()
        first = await replies_to(connection, ("echo_list", {"n": 1}), ("jot", {"text": "one"}))
        assert all(reply and reply["ok"] for reply in first)
        server.accounts.revoke("coder")
        await connection.call(3, "jot", {"text": "two"})
        # The script says nothing more, as one that waits: the run still ends at once.
        while await connection.next() is not None:
            pass

    monkeypatch.chdir(tmp_path)
    words = asyncio.run(stops(server, script_box := script, box))
    assert script_box is script
    assert words.startswith(
        "Error executing tool run: A call that Vrum refused stopped the script: "
        + SCRIPT_KEY_REVOKED
    )
    assert "Calls it made, in order: echo_list (done), jot (done), jot (refused)." in words
    assert "Calls that change something finished, or may have: jot." in words
    assert server.jotted == ["one"]
    assert server.accounts.asked == [0, 0, 0]
    assert {"op": "cancel"} in box.connections[0].received


def test_a_key_changed_to_one_tool_at_a_time_while_its_script_runs_stops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = server_that_runs_code(frozenset({"echo_list", "jot"}))
    box = Sandbox()

    async def script(connection: Connection) -> None:
        await connection.next()
        await replies_to(connection, ("echo_list", {"n": 1}))
        server.accounts.change("coder", tools=frozenset({"echo_list", "jot"}), code=False)
        await connection.call(2, "jot", {"text": "late"})
        while await connection.next() is not None:
            pass

    monkeypatch.chdir(tmp_path)
    words = asyncio.run(stops(server, script, box))
    assert SCRIPT_KEY_NOW_TOOLS in words and server.jotted == []
    assert {"op": "cancel"} in box.connections[0].received


def test_a_function_taken_from_the_allowance_while_the_script_runs_is_refused_and_it_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = server_that_runs_code(frozenset({"echo_list", "jot"}))
    answers: list[Any] = []

    async def script(connection: Connection) -> None:
        await connection.next()
        answers.extend(await replies_to(connection, ("jot", {"text": "one"})))
        server.accounts.change("coder", tools=frozenset({"echo_list"}))
        answers.extend(await replies_to(connection, ("jot", {"text": "two"}), first=2))
        await connection.call(3, "echo_list", {"n": 1})
        answers.append(await connection.next())
        await finishing(connection, "carried on")

    async def scenario() -> Any:
        return await through(server, script, Sandbox())

    monkeypatch.chdir(tmp_path)
    result = asyncio.run(scenario())
    assert result.structured_content["result"] == "carried on"
    assert [answer["ok"] for answer in answers] == [True, False, True]
    assert answers[1]["error"] == "Error executing tool jot: " + key_not_allowed("coder", "jot")
    assert server.jotted == ["one"]
    assert [call["state"] for call in result.structured_content["calls"]] == [
        "done",
        "refused",
        "done",
    ]


def test_a_function_added_to_the_allowance_while_the_script_runs_is_not_one_it_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = server_that_runs_code(frozenset({"echo_list"}))
    answers: list[Any] = []

    async def script(connection: Connection) -> None:
        run = await connection.next()
        assert run is not None and run["functions"] == ["echo_list"]
        server.accounts.change("coder", tools=frozenset({"echo_list", "jot"}))
        answers.extend(await replies_to(connection, ("jot", {"text": "sneaked"})))
        await finishing(connection, None)

    async def scenario() -> Any:
        return await through(server, script, Sandbox())

    monkeypatch.chdir(tmp_path)
    asyncio.run(scenario())
    assert answers[0]["error"] == "Error executing tool jot: " + key_not_allowed("coder", "jot")
    assert server.jotted == []


def test_a_change_queued_behind_the_one_running_is_not_made_when_the_key_is_revoked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = server_that_runs_code(frozenset({"jot"}))
    box = Sandbox()

    async def script(connection: Connection) -> None:
        await connection.next()
        # Three changes at once: the first takes a moment, the other two wait for their turn.
        await connection.call(1, "jot", {"text": "first", "wait": 0.4})
        await connection.call(2, "jot", {"text": "second"})
        await connection.call(3, "jot", {"text": "third"})
        await asyncio.sleep(0.1)
        server.accounts.revoke("coder")
        while await connection.next() is not None:
            pass

    monkeypatch.chdir(tmp_path)
    words = asyncio.run(stops(server, script, box))
    assert SCRIPT_KEY_REVOKED in words
    # The first started before the key was revoked and is told as done; the others were never made.
    assert server.jotted == ["first"]
    assert "Calls that change something finished, or may have: jot." in words
    assert "(2 times)" not in words


# -- a sandbox that stops reading ---------------------------------------------------------------


def watched_runs(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every run made from now on, with the most tasks it ever held for its calls."""
    made: list[Any] = []
    real = code_mode._Run

    class Watched(real):  # type: ignore[valid-type, misc]
        def __init__(self, *arguments: Any) -> None:
            super().__init__(*arguments)
            self.most_tasks = 0
            made.append(self)

        def _spawn(self, work: Any) -> None:
            super()._spawn(work)
            self.most_tasks = max(self.most_tasks, len(self._tasks))

    monkeypatch.setattr(code_mode, "_Run", Watched)
    return made


async def sends_calls_and_never_reads(connection: Connection, calls: int = 150) -> None:
    await connection.first()
    with suppress(ConnectionError, TimeoutError):
        for seq in range(1, calls + 1):
            await asyncio.wait_for(connection.call(seq, "list_nodes", {"limit": 1}), 5)
    await connection.release.wait()


def test_a_sandbox_that_stops_reading_ends_the_run_before_much_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = watched_runs(monkeypatch)
    host = Host()

    async def heavy(arguments: dict[str, Any]) -> Any:
        return {"text": "x" * (900 * 1024)}

    host.answers["list_nodes"] = heavy
    started = time.monotonic()
    outcome, _, _ = run_it(
        tmp_path, monkeypatch, sends_calls_and_never_reads, host=host, pump=False
    )
    assert time.monotonic() - started < 10
    assert (
        not outcome.ok
        and outcome.stage == "limit"
        and outcome.message == code_mode.TOO_MUCH_WAITING
    )
    words = failure_words(outcome)
    assert "more than 8 MB were waiting, so the script was stopped" in words
    # What waited to be written never went far past the 8 MB, and not every call was made.
    run = runs[0]
    assert (
        code_mode.MOST_QUEUED_BYTES
        < run.most_queued
        <= (code_mode.MOST_QUEUED_BYTES + code_mode.MOST_MESSAGE_BYTES)
    )
    assert host.most_reads <= code_mode.MOST_PARALLEL_READS
    assert run.most_tasks <= code_mode.MOST_OUTSTANDING_CALLS
    assert len(host.calls) < 40 and run._queued == 0 and run._tasks == set()


def test_a_sandbox_that_stops_reading_small_answers_is_stopped_at_the_time_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(code_mode, "GRACE_SECONDS", 0.0)
    runs = watched_runs(monkeypatch)
    started = time.monotonic()
    outcome, _, host = run_it(
        tmp_path, monkeypatch, sends_calls_and_never_reads, pump=False, timeout=1
    )
    assert time.monotonic() - started < 6
    assert not outcome.ok and outcome.stage == "time"
    assert len(host.calls) == 150 and runs[0].most_queued < 100_000
    assert runs[0].most_tasks <= code_mode.MOST_OUTSTANDING_CALLS


def test_a_write_to_a_sandbox_that_takes_nothing_is_bounded_in_time_and_ends_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(code_mode, "SEND_SECONDS", 0.3)
    monkeypatch.setattr(code_mode, "CANCEL_SECONDS", 0.2)
    host = Host()

    async def heavy(arguments: dict[str, Any]) -> Any:
        return {"text": "x" * (900 * 1024)}

    host.answers["list_nodes"] = heavy

    async def script(connection: Connection) -> None:
        await sends_calls_and_never_reads(connection, 3)

    started = time.monotonic()
    outcome, _, _ = run_it(tmp_path, monkeypatch, script, host=host, pump=False)
    assert time.monotonic() - started < 6
    assert not outcome.ok and outcome.stage == "sandbox"
    assert outcome.message == code_mode.NOT_TAKING_ANSWERS
    assert "stopped taking the answers to its calls" in failure_words(outcome)


def test_reads_go_eight_at_a_time_a_burst_is_read_a_little_at_a_time_and_changes_keep_their_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = watched_runs(monkeypatch)
    host = Host()

    async def slow(arguments: dict[str, Any]) -> Any:
        await asyncio.sleep(0.03)
        return {}

    host.answers["list_nodes"] = slow
    host.answers["add_note"] = slow

    async def script(connection: Connection) -> None:
        await connection.next()
        for seq in range(1, 61):
            await connection.call(seq, "list_nodes")
        for seq in range(61, 91):
            await connection.call(seq, "add_note")
        for _ in range(90):
            await connection.next()
        await finishing(connection, "all answered")

    outcome, _, _ = run_it(tmp_path, monkeypatch, script, host=host)
    assert outcome.ok and outcome.result == "all answered" and len(outcome.calls) == 90
    assert host.most_reads <= code_mode.MOST_PARALLEL_READS and host.most_writes == 1
    assert host.write_order == list(range(61, 91))
    assert runs[0].most_tasks <= code_mode.MOST_OUTSTANDING_CALLS


def test_a_halted_run_makes_no_call_that_had_not_started_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = Host()
    stopped = "The agent key is not valid any more: it was revoked while its script was running."

    async def first_is_stopped(arguments: dict[str, Any]) -> Any:
        raise code_mode.KeyStopped(stopped)

    host.answers["add_note"] = first_is_stopped

    async def script(connection: Connection) -> None:
        await connection.next()
        for seq in (1, 2, 3):
            await connection.call(seq, "add_note")
        while await connection.next() is not None:
            pass

    outcome, sandbox, _ = run_it(tmp_path, monkeypatch, script, host=host)
    assert not outcome.ok and outcome.stage == "refused" and outcome.message == stopped
    # Only the first reached the guard; the others waited for their turn and were never made, so
    # nothing is said to have changed.
    assert [name for name, _, _ in host.calls] == ["add_note"]
    assert not outcome.changed_anything and outcome.changing_calls == []
    assert {"op": "cancel"} in sandbox.connections[0].received


def test_every_exit_of_a_run_leaves_no_task_and_no_connection_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `play` fails any test that leaves a task of a run running; here the ending it does not see:
    # the task that runs the script is cancelled (the agent gave up) while calls are in flight.
    async def scenario() -> None:
        sandbox = Sandbox()
        host = Host()

        async def hang(arguments: dict[str, Any]) -> Any:
            await asyncio.sleep(60)

        host.answers["add_note"] = hang

        async def script(connection: Connection) -> None:
            await connection.next()
            await connection.call(1, "add_note")
            await connection.call(2, "list_nodes")
            await connection.release.wait()

        await sandbox.start(script)
        before = {id(task) for task in asyncio.all_tasks()}
        running = asyncio.create_task(CodeMode(SOCKET, host).run(a_caller(), "return 1", 60))
        await asyncio.sleep(0.3)
        assert [name for name, _, _ in host.calls] == ["add_note", "list_nodes"]
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running
        await asyncio.sleep(0.1)
        assert tasks_of_a_run() == []
        assert host.cancelled == ["add_note"]
        assert sandbox.connections[0].host_closed.is_set()
        for connection in sandbox.connections:
            connection.release.set()
        await sandbox.stop()
        left = [task for task in asyncio.all_tasks() if id(task) not in before and not task.done()]
        assert [task for task in left if task is not asyncio.current_task()] == []

    monkeypatch.chdir(tmp_path)
    asyncio.run(scenario())
