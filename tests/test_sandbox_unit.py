"""The parts of the sandbox supervisor (sandbox/supervisor.py, M37) that need no container.

What a script can and cannot do is proved against the real container by tests/sandbox_attacks.py;
here are the pure parts: reading what Deno prints, checking a request, and the rules by which the
supervisor treats whatever the runner writes (it is not trusted).
"""

import asyncio
import importlib.util
import json
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SANDBOX = Path(__file__).resolve().parent.parent / "sandbox"


def load_supervisor() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sandbox_supervisor", SANDBOX / "supervisor.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


supervisor = load_supervisor()
NONCE = "0123456789abcdef0123456789abcdef"


class Writer:
    """Stands in for the connection to the app: keeps what is written."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def write(self, data: bytes) -> None:
        self.sent.append(json.loads(data))

    async def drain(self) -> None:
        return None


def make_run(**changes: Any) -> Any:
    request = {
        "id": "test",
        "code": "return 1;",
        "functions": ["echo", "add_note"],
        "declarations": "",
        "timeout": 5,
        "max_calls": 3,
        "max_result_bytes": 100,
        "max_log_bytes": 20,
    }
    request.update(changes)
    return supervisor.Run(request, None, Writer())


def runner_output(*lines: bytes, limit: int = 2**16) -> asyncio.StreamReader:
    stream = asyncio.StreamReader(limit=limit)
    for line in lines:
        stream.feed_data(line + b"\n")
    stream.feed_eof()
    return stream


def framed(message: dict[str, Any]) -> bytes:
    return NONCE.encode() + b" " + json.dumps(message).encode()


class FakeProcess:
    def __init__(self, stdout: asyncio.StreamReader) -> None:
        self.stdout = stdout


def read(run: Any, *lines: bytes, limit: int = 2**16) -> Any:
    async def go() -> Any:
        return await run.read_runner(FakeProcess(runner_output(*lines, limit=limit)), NONCE)

    return asyncio.run(go())


def test_the_import_map_stubs_every_node_module_and_every_file() -> None:
    imports = json.loads((SANDBOX / "import_map.json").read_text())["imports"]
    stubs = {key for key, value in imports.items() if key.startswith("node:")}
    assert {imports[key] for key in stubs} == {"data:text/javascript,"}
    # The build checks the map against Deno's own list; these are the ones a script would reach for.
    risky = ("zlib", "vm", "worker_threads", "net", "child_process", "fs", "os", "module", "http")
    assert len(stubs) > 60
    assert {f"node:{name}" for name in risky} <= stubs
    assert imports["file:///"].endswith("/") and imports["file:///"] != "file:///"
    assert imports["file:///sandbox/runner.ts"] == "file:///sandbox/runner.ts"


def test_a_failed_check_is_told_in_plain_words() -> None:
    text = (
        "\x1b[0m\x1b[1mTS2551\x1b[0m [ERROR]: Property 'list_node' does not exist on type "
        "'{ list_nodes(args?: ListNodesArgs | undefined): Promise<{ items: Node[]; next_cursor: "
        "string | null; }>; add_note(args: { text: string; why: string; nodes?: string[] }): "
        "Promise<{ id: number }>; }'. Did you mean 'list_nodes'?\n"
        "(async function(){const r = await vrum.list_node({kind: 'story'});\n"
        "                                       ~~~~~~~~~\n"
        "    at file:///tmp/run-x/main.ts:1:40\n\n"
        "    'list_nodes' is declared here.\n"
        "      list_nodes(args?: ListNodesArgs): Promise<Page>;\n"
        "        at file:///tmp/run-x/decl.d.ts:7:3\n\n"
        "TS2345 [ERROR]: Argument of type '{ text: string; }' is not assignable to parameter.\n"
        "  Property 'why' is missing in type '{ text: string; }' but required in type 'Note'.\n"
        "await vrum.add_note({text: 'a'});\n"
        "                    ~~~~~~~~~~~\n"
        "    at file:///tmp/run-x/main.ts:4:21\n\n"
        "TS2304 [ERROR]: Cannot find name 'Deno'. Do you need to change your target library? Try "
        "changing the 'lib' compiler option to include 'deno.ns'.\n"
        "Deno.exit();\n"
        "~~~~\n"
        "    at file:///tmp/run-x/main.ts:6:1\n\n"
        "Found 3 errors.\n"
    )
    first, second, third = supervisor.diagnostics(text.split("\n"))
    assert first[:2] == ("file:///tmp/run-x/main.ts", 1)
    assert first[2].endswith("Did you mean 'list_nodes'?")
    assert "'...'" in first[2] and "list_nodes(args" not in first[2]
    assert second[1] == 4 and "Property 'why' is missing" in second[2]
    assert third[1] == 6
    assert third[2].startswith("Cannot find name 'Deno'.") and "deno.ns" not in third[2]
    assert "no files, network or system access" in third[2]


def test_a_node_import_in_the_types_is_the_same_plain_answer() -> None:
    assert supervisor.plain("Cannot find name 'node:fs'.") == supervisor.NOT_AVAILABLE
    stub = f"Cannot find module '{supervisor.STUB_PREFIX}etc/passwd'."
    assert supervisor.plain(stub) == "Cannot find module 'file:///etc/passwd'."
    folder = "/tmp/run-ab12"
    assert supervisor.plain(f"Cannot find module 'file://{folder}/x.ts'.", folder) == (
        "Cannot find module 'x.ts'."
    )


def test_a_syntax_error_says_where_and_in_words() -> None:
    lines = [
        "error: SyntaxError: Expression expected",
        "  |",
        "2 | const b = ;",
        "    at file:///t/main.ts:2:11",
    ]
    assert supervisor.syntax_message(lines) == ("Syntax error: Expression expected.", 2)
    module_code = "error: SyntaxError: 'import', and 'export' cannot be used outside of module code"
    message, line = supervisor.syntax_message([module_code, "    at file:///t/main.ts:3:19"])
    assert line == 3 and message.startswith("`import` and `export` are not available.")
    assert supervisor.syntax_message(["error: something else"]) is None


def test_a_request_is_checked_and_the_limits_are_filled_in() -> None:
    request: dict[str, Any] = {"id": "a", "code": "return 1;", "functions": ["echo"]}
    assert supervisor.problem_with(request) is None
    assert request["timeout"] == 60 and request["max_calls"] == 200
    assert request["max_result_bytes"] == 262144 and request["max_log_bytes"] == 16384
    assert request["declarations"] == ""


@pytest.mark.parametrize(
    "change",
    [
        {"id": 5},
        {"id": "x" * 101},
        {"code": None},
        {"functions": "echo"},
        {"functions": ["not valid"]},
        {"functions": ["a" * 101]},
        {"functions": ["a", "a"]},
        {"functions": [f"f{i}" for i in range(257)]},
        {"declarations": 5},
        {"declarations": "x" * (512 * 1024 + 1)},
        {"timeout": 0},
        {"timeout": 121},
        {"timeout": True},
        {"timeout": "60"},
        {"max_calls": 1001},
        {"max_calls": 1.5},
        {"max_result_bytes": 0},
        {"max_log_bytes": -1},
    ],
)
def test_a_request_that_breaks_the_rules_is_refused_in_words(change: dict[str, Any]) -> None:
    request: dict[str, Any] = {"id": "a", "code": "return 1;", "functions": ["echo"]}
    request.update(change)
    problem = supervisor.problem_with(request)
    assert isinstance(problem, str) and problem.endswith(".")


def test_lines_without_the_nonce_are_ignored_and_counted() -> None:
    run = make_run()
    forged = json.dumps({"op": "done", "result": "forged"}).encode()
    done = framed({"op": "done", "result": 1})
    assert read(run, forged, b"garbage", forged, done)["result"] == 1
    assert run.strays == 3
    assert run.calls == 0


def test_too_many_stray_lines_end_the_run() -> None:
    run = make_run()
    with pytest.raises(supervisor.Fail) as stopped:
        read(run, *([b"not framed"] * 25))
    assert stopped.value.stage == "sandbox"


def test_a_call_is_checked_before_it_goes_to_the_app() -> None:
    run = make_run()
    call = {"op": "call", "seq": 1, "name": "echo", "args": {"a": 1}}
    assert read(run, framed(call)) is None
    assert run.writer.sent == [call]
    for bad in (
        {"op": "call", "seq": 2, "name": "echo", "args": {}},
        {"op": "call", "seq": 1, "name": "delete_everything", "args": {}},
        {"op": "call", "seq": 1, "name": "echo", "args": [1]},
        {"op": "call", "seq": 1, "name": "echo"},
    ):
        again = make_run()
        with pytest.raises(supervisor.Fail) as stopped:
            read(again, framed(bad))
        assert stopped.value.stage == "sandbox"
        assert again.writer.sent == []


def test_more_calls_than_allowed_end_the_run() -> None:
    run = make_run(max_calls=2)
    calls = [framed({"op": "call", "seq": n, "name": "echo", "args": {}}) for n in (1, 2, 3)]
    with pytest.raises(supervisor.Fail) as stopped:
        read(run, *calls)
    assert stopped.value.stage == "limit"
    assert len(run.writer.sent) == 2


def test_a_result_over_the_limit_is_refused() -> None:
    run = make_run(max_result_bytes=50)
    big = framed({"op": "done", "result": "x" * 200})
    with pytest.raises(supervisor.Fail) as stopped:
        read(run, big)
    assert stopped.value.stage == "limit"


def test_a_line_longer_than_the_limit_ends_the_run() -> None:
    run = make_run()
    with pytest.raises(supervisor.Fail) as stopped:
        read(run, b"x" * 500, limit=100)
    assert stopped.value.stage == "limit"


def test_logs_are_kept_within_their_limit_and_say_when_they_were_cut() -> None:
    run = make_run(max_log_bytes=20)
    lines = [
        framed({"op": "log", "line": "0123456789", "cut": False}),
        framed({"op": "log", "line": "abcdefghijklmnop", "cut": False}),
        framed({"op": "log", "line": "never kept", "cut": True}),
        framed({"op": "done", "result": None}),
    ]
    assert read(run, *lines)["op"] == "done"
    assert run.logs == ["0123456789", "abcdefghij"]
    assert run.logs_cut is True
    assert sum(len(line.encode()) for line in run.logs) <= 20


def test_a_final_failure_has_a_stage_the_app_knows() -> None:
    run = make_run()
    kept = run.finish({"op": "fail", "stage": "error", "message": "boom", "line": 99})
    assert kept["stage"] == "error" and kept["line"] == run.lines
    with pytest.raises(supervisor.Fail):
        run.finish({"op": "fail", "stage": "made up", "message": "x"})
    assert run.finish({"op": "done", "result": [1]})["truncated_logs"] is False


def test_how_a_runner_that_died_is_described() -> None:
    run = make_run()
    memory = run.died(133, "Fatal JavaScript out of memory")
    assert memory.stage == "memory" and "memory" in memory.message
    assert run.died(-24, "").stage == "time"
    assert run.died(1, "").stage == "sandbox"


def lines_from(*chunks: bytes) -> list[str]:
    async def go() -> list[str]:
        stream = asyncio.StreamReader()
        for chunk in chunks:
            stream.feed_data(chunk)
        stream.feed_eof()
        result: list[str] = await supervisor.collect_lines(stream)
        return result

    return asyncio.run(go())


def test_what_deno_prints_is_cut_to_short_lines_as_it_is_read() -> None:
    quoted = b'TS2307 [ERROR]: Import "' + b" " * 3_000_000 + b'x".'
    rest = b"\n    at file:///tmp/run-x/main.ts:1:40\n\nerror: Type checking failed.\n"
    whole_text = quoted + rest
    cuts = (1_000_000, 2_500_000)
    found = lines_from(whole_text[: cuts[0]], whole_text[cuts[0] : cuts[1]], whole_text[cuts[1] :])
    assert len(found[0]) == supervisor.LINE_BYTES
    assert found[1].strip() == "at file:///tmp/run-x/main.ts:1:40"
    assert found[3] == "error: Type checking failed."
    many = lines_from(b"a line\n" * 100_000)
    assert sum(len(line) + 1 for line in many) < supervisor.ERROR_BYTES + 100


def test_hostile_text_is_read_in_a_blink() -> None:
    spaces = " " * 5_000_000
    lines = [
        f'TS2307 [ERROR]: Import "{spaces}x".',
        f"let a: typeof import({spaces!r});",
        "   " + "~" * 5_000_000,
        "    at file:///tmp/run-x/main.ts:1:40",
        f"error: SyntaxError: {spaces}",
        "'" * 5_000_000,
        "\x1b[" + "1" * 5_000_000,
        "\x1b[1" * 1_000_000,
    ]
    started = time.perf_counter()
    found = supervisor.diagnostics(lines, "/tmp/run-x")
    syntax = supervisor.syntax_message(lines)
    types_only = [line for line in lines if "SyntaxError" not in line]
    verdict = supervisor.judge_check(types_only, 1, False, 1, "/tmp/run-x")
    syntax_lines = [lines[4], lines[3]]
    syntax_verdict = supervisor.judge_check(syntax_lines, 1, False, 1, "/tmp/run-x")
    plain = supervisor.plain(spaces + "'" * 5_000_000 + "Do you need to " + spaces)
    assert time.perf_counter() - started < 0.5
    assert found[0][:2] == ("file:///tmp/run-x/main.ts", 1)
    assert len(found[0][2]) <= 400 and syntax is not None and len(syntax[0]) < 400
    assert verdict.stage == "types" and verdict.line == 1 and len(verdict.message) < 600
    assert syntax_verdict.stage == "syntax" and syntax_verdict.line == 1
    assert len(plain) <= 400


def test_the_pieces_of_a_deno_line_are_read_plainly() -> None:
    assert (
        supervisor.header_of("TS2551 [ERROR]: Property 'x' is wrong.") == "Property 'x' is wrong."
    )
    assert supervisor.header_of("TS [ERROR]: x") is None
    assert supervisor.header_of("TS12x [ERROR]: x") is None
    assert supervisor.header_of("error: SyntaxError: x") is None
    assert supervisor.location_of("    at file:///tmp/run-x/main.ts:4:21") == (
        "file:///tmp/run-x/main.ts",
        4,
    )
    assert supervisor.location_of("    at x:\u00b2:3") is None
    assert supervisor.location_of("    at x:" + "9" * 30 + ":3") is None
    assert supervisor.location_of("    at nowhere") is None
    assert supervisor.is_marker("   ~~~^^^  ") and not supervisor.is_marker("   ~~x ")
    assert not supervisor.is_marker("    ")
    quoted = "a '" + "q" * 130 + "' b 'short' c"
    assert supervisor.shorten_quoted(quoted) == "a '...' b 'short' c"
    assert supervisor.plain("x\n\n   y\t z") == "x y z"


def test_a_big_line_of_json_is_parsed_off_the_loop_with_the_same_answer() -> None:
    value = {"a": ["x" * 100] * 1000, "b": None}
    raw = json.dumps(value).encode()
    assert len(raw) > supervisor.BIG_JSON
    assert asyncio.run(supervisor.parse_json(raw)) == value
    assert asyncio.run(supervisor.parse_json(b'{"a":1}')) == {"a": 1}
    with pytest.raises((ValueError, RecursionError)):
        asyncio.run(supervisor.parse_json(b"[" * 40_000))


def test_replies_wait_in_a_bounded_queue_and_an_overflow_ends_the_run() -> None:
    run = make_run()
    big = "x" * (900 * 1024)
    for seq in range(1, 40):
        run.reply_to_runner(seq, True, {"data": big})
    assert run.abort is not None and run.abort.stage == "limit"
    assert "not reading the replies" in run.abort.message and "16 MB" in run.abort.message
    assert run.outbox_bytes == 0 and len(run.outbox) == 0
    run.reply_to_runner(99, True, 1)
    assert len(run.outbox) == 0
    small = make_run()
    for seq in range(1, 201):
        small.reply_to_runner(seq, True, {"data": "x" * 1000})
    assert small.abort is None and len(small.outbox) == 200


def test_the_app_socket_is_read_while_the_script_is_not_reading() -> None:
    class Stuck:
        def write(self, data: bytes) -> None:
            return None

        async def drain(self) -> None:
            await asyncio.Event().wait()

    class StuckProcess:
        stdin = Stuck()

    async def go() -> Any:
        run = make_run()
        run.reader = asyncio.StreamReader()
        run.runner = StuckProcess()
        run.waiting = {1, 2, 3}
        for seq in (1, 2, 3):
            reply = {"op": "reply", "seq": seq, "ok": True, "result": "x" * 1000}
            run.reader.feed_data(json.dumps(reply).encode() + b"\n")
        run.reader.feed_data(b'{"op":"cancel"}\n')
        writer = asyncio.create_task(run.write_replies(StuckProcess()))
        await asyncio.wait_for(run.take_messages(), 1)
        writer.cancel()
        return run

    run = asyncio.run(go())
    assert run.stopped.is_set()
    assert run.outbox_bytes >= 2000 and run.abort is None
