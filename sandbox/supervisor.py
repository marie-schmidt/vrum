"""The sandbox supervisor (M37, code mode): runs the scripts agents write, one connection per run.

It listens on a Unix socket (SANDBOX_SOCKET, default /run/sandbox/run.sock, in a volume the app
shares) and speaks JSON lines, as described in docs/agents.md. For each `run` it does three things
in Deno processes of their own, each with no permission of any kind and with limits set by the
operating system:

  1. prepare.ts  takes the types out of the script (and reports a syntax error),
  2. deno check  checks the script against the declarations the app sent (type errors),
  3. runner.ts   evaluates the script from a string; every `vrum.x()` it makes comes back here as
                 a `call`, goes to the app, and the app's `reply` goes back.

Whatever happens, the process group of every child is killed and its temporary folder removed. The
app is not trusted and neither is anything the script can write: every message is checked for
shape and size.

`python supervisor.py ping` is the container's health check, and `python supervisor.py pipe`
joins its own standard input and output to the socket (for tests that cannot reach the volume).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import ctypes
import json
import os
import re
import resource
import secrets
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any

SOCKET = os.environ.get("SANDBOX_SOCKET", "/run/sandbox/run.sock")
HERE = os.path.dirname(os.path.abspath(__file__))
DENO = "/usr/local/bin/deno"
DENO_DIR = "/deno"
SCRATCH = "/tmp"

MIB = 1024 * 1024
LINE_LIMIT = 4 * MIB  # the most a line from the app may hold (the script itself is at most 1 MiB)
RUNNER_LINE_LIMIT = MIB + 256 * 1024  # one call or the result is at most 1 MiB, plus the envelope
MESSAGE_LIMIT = MIB  # one call or reply, as JSON
MOST_SCRIPT = MIB
MOST_DECLARATIONS = 512 * 1024
MOST_FUNCTIONS = 256
MOST_RUNS = 4
MOST_CHECKS = 2  # type checking is the heavy step; the others wait their turn
IDLE_SECONDS = 10  # a client that connects and sends nothing is dropped
MOST_STRAYS = 20  # lines that break the rules before a run is ended
# name: (default, lowest, highest, whole numbers only)
LIMITS = {
    "timeout": (60, 1, 120, False),
    "max_calls": (200, 0, 1000, True),
    "max_result_bytes": (262144, 1, MIB, True),
    "max_log_bytes": (16384, 0, 64 * 1024, True),
}
PREPARE_SECONDS = 3
CHECK_SECONDS = 8
# Most that is kept of what the preparing step prints on its standard output: that is its own
# answer, the script without its types, so it can be as large as the script.
OUTPUT_CAP = 2 * MIB
# What Deno prints when something is wrong quotes the script, so it is as hostile as the script.
# Every line is cut to LINE_BYTES as it is read, before anything looks at it, and only ERROR_BYTES
# of lines are kept: enough for five problems, and nothing a regular expression could chew on.
LINE_BYTES = 2048
ERROR_BYTES = 128 * 1024
MOST_PROBLEMS = 5
# Replies to calls that the app has sent and the script has not yet read, per run. The script reads
# its replies as fast as it can parse them, and it could not hold more than this of them in its
# 128 MB heap anyway, so a backlog this big means the script stopped reading (it is busy, or never
# will read): the run is ended as a limit at once, and nothing else waits for it.
REPLY_QUEUE_LIMIT = 16 * MIB
BIG_JSON = 32 * 1024  # a line of JSON bigger than this is parsed off the event loop

PREFIX = (
    "(async function(){"  # the script is wrapped as one function, on its first line, so lines match
)
SUFFIX = "\n})"
STUB_PREFIX = "file:///not-available/"  # where the import map sends every file: import
NAME = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
STAGES = {"syntax", "types", "refused", "error", "time", "memory", "limit", "sandbox"}
# Every Deno process: no permission of any kind (there is no --allow flag), nothing remote, no npm,
# no configuration found by itself, nothing downloaded.
LOCKED_DOWN = [
    "--no-prompt",
    "--no-remote",
    "--no-npm",
    "--no-config",
    "--no-lock",
    "--cached-only",
]

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")  # only ever run on a line that is already cut
SYSTEM_NAMES = {
    "Deno", "process", "fetch", "require", "Buffer", "XMLHttpRequest", "WebSocket", "Worker",
    "localStorage", "sessionStorage", "navigator", "window", "document", "global", "self",
    "crypto", "performance",
}  # fmt: skip
NOT_AVAILABLE = (
    "A script has no files, network or system access and cannot import anything: "
    "it has the language, `console` and `vrum`."
)

active_runs: set[Run] = set()
check_slots: asyncio.Semaphore
deno_version = "unknown"


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}), flush=True)


def dumps(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


class Fail(Exception):
    def __init__(self, stage: str, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.line = line


class Stopped(Exception):
    """The app cancelled the run or went away."""


PR_SET_DUMPABLE = 4
LIBC = ctypes.CDLL(None)


def limits(memory: int, cpu: int, files: int) -> Any:
    """What a child process gets before it starts: limits, and first place for the OOM killer."""

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_DATA, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
        resource.setrlimit(resource.RLIMIT_NOFILE, (files, files))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * MIB, 64 * MIB))
        # The supervisor is not dumpable (see hide_from_scripts), and a child starts as it is. Its
        # /proc files would then belong to root and the next line would be refused. The program
        # that is started next (Deno, whose file nobody can read) is not dumpable either.
        LIBC.prctl(PR_SET_DUMPABLE, 1, 0, 0, 0)
        # Out of memory in the container: the kernel kills a script's process, not the supervisor.
        with contextlib.suppress(OSError), open("/proc/self/oom_score_adj", "w") as handle:
            handle.write("1000")

    return apply


def hide_from_scripts() -> None:
    """Make this process not dumpable, so that other processes of the same user cannot read it.

    The scripts of different runs, and anything one of them might start, all run as the same user. A
    process that is not dumpable has /proc files that belong to root: its memory, its descriptors
    (the connections of the other runs) and its environment cannot be opened, and it cannot be
    traced. This needs no capability. The Deno processes get the same from the mode of the Deno
    file (execute only, see fetch_deno.py): a process started from a file nobody can read is not
    dumpable either.
    """
    LIBC.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)


async def collect(stream: asyncio.StreamReader, cap: int) -> bytes:
    """Read a stream to its end, keeping at most `cap` bytes (the rest is read and dropped)."""
    kept = bytearray()
    while chunk := await stream.read(65536):
        if len(kept) < cap:
            kept += chunk[: cap - len(kept)]
    return bytes(kept)


async def collect_lines(stream: asyncio.StreamReader) -> list[str]:
    """Read a stream to its end as lines, keeping little of it (see LINE_BYTES and ERROR_BYTES).

    The rest is read and dropped, so a script that makes Deno print a megabyte on one line costs
    a few kilobytes here, and no time.
    """
    lines: list[bytes] = []
    current = bytearray()
    kept = 0
    while chunk := await stream.read(65536):
        for number, part in enumerate(chunk.split(b"\n")):
            if number > 0:
                if kept < ERROR_BYTES:
                    lines.append(bytes(current))
                    kept += len(current) + 1
                current = bytearray()
            room = LINE_BYTES - len(current)
            if room > 0:
                current += part[:room]
    if current and kept < ERROR_BYTES:
        lines.append(bytes(current))
    return [line.decode("utf-8", errors="replace") for line in lines]


async def tail(stream: asyncio.StreamReader, cap: int) -> bytes:
    kept = b""
    while chunk := await stream.read(65536):
        kept = (kept + chunk)[-cap:]
    return kept


async def parse_json(raw: bytes) -> Any:
    """Parse a line of JSON; a big one is parsed off the event loop, so it cannot hold it up."""
    if len(raw) < BIG_JSON:
        return json.loads(raw)
    return await asyncio.to_thread(json.loads, raw)


# Everything below that reads Deno's output works on lines that are cut first, and uses plain string
# methods (find, startswith, split), each of which takes time in proportion to the line and no more.


def cut(line: str) -> str:
    """One line of Deno's output as it is looked at: cut, and without colour codes."""
    return ANSI.sub("", line[:LINE_BYTES])


def whole(text: str) -> bool:
    return text.isascii() and text.isdigit() and len(text) <= 9


def header_of(line: str) -> str | None:
    """The message of a line like `TS2551 [ERROR]: Property ...`, or None."""
    if not line.startswith("TS"):
        return None
    marker = line.find(" [ERROR]: ")
    if marker < 3 or not line[2:marker].isdigit():
        return None
    return line[marker + len(" [ERROR]: ") :]


def location_of(line: str) -> tuple[str, int] | None:
    """(file, line) of a line like `    at file:///tmp/run-x/main.ts:4:21`, or None."""
    text = line.strip()
    if not text.startswith("at "):
        return None
    parts = text[3:].rsplit(":", 2)
    if len(parts) != 3 or not whole(parts[1]) or not whole(parts[2]):
        return None
    return parts[0], int(parts[1])


def is_marker(line: str) -> bool:
    """A line of ~~~~ or ^^^^ under the part of the script an error is about."""
    text = line.strip()
    return bool(text) and not text.strip("~^")


def shorten_quoted(message: str) -> str:
    """Replace a quoted stretch of 120 characters or more with '...' (TypeScript quotes types)."""
    out: list[str] = []
    at = 0
    while True:
        start = message.find("'", at)
        end = message.find("'", start + 1) if start >= 0 else -1
        if end < 0:
            out.append(message[at:])
            return "".join(out)
        out.append(message[at:start])
        out.append("'...'" if end - start - 1 >= 120 else message[start : end + 1])
        at = end + 1


def names_system_global(message: str) -> bool:
    start = "Cannot find name '"
    if not message.startswith(start):
        return False
    end = message.find("'", len(start))
    return end > 0 and message[len(start) : end] in SYSTEM_NAMES and message[end + 1 :] in ("", ".")


def plain(message: str, scratch: str = "") -> str:
    """A TypeScript message as a short plain sentence: no paths, no huge types, no Deno advice."""
    message = " ".join(message[: 2 * LINE_BYTES].split())
    message = message.replace(STUB_PREFIX, "file:///")
    if scratch:
        message = message.replace(f"file://{scratch}/", "")
    advice = message.find("Do you need to ")
    if advice >= 0:
        message = message[:advice].rstrip()
    message = shorten_quoted(message)
    if message.startswith("Cannot find name 'node:"):
        return NOT_AVAILABLE
    if names_system_global(message):
        message += " " + NOT_AVAILABLE
    return message[:400]


def diagnostics(lines: list[str], scratch: str = "") -> list[tuple[str, int | None, str]]:
    """(file, line, message) for each problem `deno check` printed."""
    blocks: list[list[str]] = []
    for raw in lines:
        line = cut(raw)
        header = header_of(line)
        if header is not None:
            blocks.append([header])
        elif blocks:
            blocks[-1].append(line)
    problems: list[tuple[str, int | None, str]] = []
    for block in blocks:
        header, rest = block[0], block[1:]
        marker = next((i for i, line in enumerate(rest) if is_marker(line)), None)
        where = next(((i, loc) for i, line in enumerate(rest) if (loc := location_of(line))), None)
        stop = marker - 1 if marker else (where[0] if where else len(rest))
        words = [header] + [line.strip() for line in rest[: max(stop, 0)] if line.strip()]
        path, number = where[1] if where else ("", None)
        problems.append((path, number, plain(" ".join(words), scratch)))
    return problems


def syntax_message(lines: list[str]) -> tuple[str, int | None] | None:
    """The syntax error `deno check` printed, as a plain sentence and the line of the script."""
    cleaned = [cut(line) for line in lines]
    prefix = "error: SyntaxError: "
    start = next((i for i, line in enumerate(cleaned) if line.startswith(prefix)), None)
    if start is None:
        return None
    reason = " ".join(cleaned[start][len(prefix) :].split())[:300]
    number = None
    for line in cleaned[start + 1 :]:
        where = location_of(line)
        if where is not None and where[0].endswith("/main.ts"):
            number = where[1]
            break
    if "cannot be used outside of module code" in reason:
        reason = (
            "`import` and `export` are not available. The script is the body of a function, "
            "and `vrum` is already in scope."
        )
    else:
        reason = f"Syntax error: {reason}."
    return reason, number


def clamp(line: Any, script_lines: int) -> int | None:
    """A line number from Deno, kept to the lines the script has (or None)."""
    if isinstance(line, int) and not isinstance(line, bool) and line >= 1:
        return min(line, script_lines)
    return None


def judge_check(
    lines: list[str], code: int, only_locate: bool, script_lines: int, scratch: str
) -> Fail | None:
    """What a finished `deno check` says about the script: the Fail to raise, or None if it passed.

    With `only_locate` a syntax error is already known and only its place is wanted. This is a pure
    function of cut lines, run off the event loop.
    """
    if code == 0:
        return None
    located = syntax_message(lines)
    if located is not None:
        return Fail("syntax", located[0], clamp(located[1], script_lines))
    if only_locate:
        return None
    if code in (133, -5, -6) or any("out of memory" in line.lower() for line in lines):
        return Fail("memory", "Checking the script used too much memory. Make the script smaller.")
    found = diagnostics(lines, scratch)
    if not found:
        return Fail("sandbox", "The script could not be checked.")
    for path, _, message in found:
        if path.endswith("/decl.d.ts"):
            return Fail("sandbox", f"The function declarations are not valid TypeScript: {message}")
    shown = [item for item in found if item[0].endswith("/main.ts")][:MOST_PROBLEMS]
    texts = [f"Line {clamp(number, script_lines) or 1}: {message}" for _, number, message in shown]
    if len(found) > len(shown):
        texts.append(f"{len(found) - len(shown)} more problems are not shown.")
    return Fail("types", "\n".join(texts), clamp(shown[0][1], script_lines) if shown else None)


class Run:
    def __init__(
        self, request: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.request = request
        self.reader = reader
        self.writer = writer
        self.code: str = request["code"]
        self.functions: list[str] = request["functions"]
        self.timeout: float = request["timeout"]
        self.max_calls: int = request["max_calls"]
        self.max_result_bytes: int = request["max_result_bytes"]
        self.max_log_bytes: int = request["max_log_bytes"]
        self.procs: list[asyncio.subprocess.Process] = []
        self.stopped = asyncio.Event()
        self.runner: asyncio.subprocess.Process | None = None
        self.scratch = ""
        self.deno_dir = ""  # the caches of this run's Deno processes (see spawn)
        self.logs: list[str] = []
        self.log_bytes = 0
        self.logs_cut = False
        self.calls = 0
        self.waiting: set[int] = set()
        self.strays = 0
        self.lines = self.code.count("\n") + 1
        # Replies to the script's calls wait here, in order, for write_replies to write them.
        self.outbox: collections.deque[bytes] = collections.deque()
        self.outbox_bytes = 0
        self.outbox_ready = asyncio.Event()
        self.reply_writer: asyncio.Task[None] | None = None
        self.abort: Fail | None = None  # why the run was ended from outside the main flow

    async def execute(self) -> None:
        started = time.monotonic()
        listener = asyncio.create_task(self.listen())
        message: dict[str, Any] | None
        try:
            self.scratch = tempfile.mkdtemp(prefix="run-", dir=SCRATCH)
            self.deno_dir = tempfile.mkdtemp(prefix="run-", dir=DENO_DIR)
            message = await self.pipeline()
        except Fail as failure:
            message = self.failure(failure)
        except Stopped:
            message = None
        except Exception as error:  # the supervisor must outlive anything a script does
            log("run_error", id=self.request["id"], error=type(error).__name__)
            message = self.failure(
                Fail("sandbox", "The sandbox hit an unexpected problem. Try again.")
            )
        finally:
            listener.cancel()
            if self.reply_writer is not None:
                self.reply_writer.cancel()
            self.kill_all()
            for folder in (self.scratch, self.deno_dir):
                if folder:
                    shutil.rmtree(folder, ignore_errors=True)
        if message is not None and not self.stopped.is_set():
            await self.send(message)
        outcome = "stopped" if message is None else message.get("stage", "done")
        log(
            "run",
            id=self.request["id"],
            outcome=outcome,
            calls=self.calls,
            ms=round((time.monotonic() - started) * 1000),
        )

    def failure(self, failure: Fail) -> dict[str, Any]:
        return {
            "op": "fail",
            "stage": failure.stage,
            "message": failure.message,
            "line": failure.line,
            "logs": self.logs,
        }

    async def send(self, message: dict[str, Any]) -> None:
        try:
            self.writer.write(dumps(message))
            await self.writer.drain()
        except (ConnectionError, OSError):
            self.stop()

    def stop(self) -> None:
        self.stopped.set()
        self.kill_all()

    def kill_all(self) -> None:
        for proc in self.procs:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)

    def check_not_stopped(self) -> None:
        if self.stopped.is_set():
            raise Stopped

    # ---- the three steps

    async def pipeline(self) -> dict[str, Any]:
        wrapped = await asyncio.to_thread(self.wrap_script)
        problem: Fail | None = None
        script = ""
        try:
            script = await self.prepare(wrapped)
        except Fail as unlocated:
            if unlocated.stage != "syntax" or unlocated.line is not None:
                raise
            problem = unlocated
        await self.check(wrapped, problem)
        if problem is not None:
            raise problem
        return await self.run_script(script)

    def wrap_script(self) -> str:
        """The script as one function on one line, or a refusal if it is too large."""
        code_bytes = self.code.encode("utf-8", errors="replace")
        if len(code_bytes) > MOST_SCRIPT:
            raise Fail("limit", "The script is larger than 1 MB.")
        return PREFIX + code_bytes.decode("utf-8") + SUFFIX

    async def capture(
        self,
        argv: list[str],
        data: bytes | None,
        seconds: float,
        memory: int,
        cpu: int,
        what: str,
        out_cap: int,
    ) -> tuple[int, bytes, list[str]]:
        """Run a Deno process to its end: its exit status, its output, and what it said on stderr.

        Standard error is kept as short cut lines only (see collect_lines).
        """
        proc = await self.spawn(
            argv, memory=memory, cpu=cpu, files=256, feed=data is not None, cwd=self.scratch
        )

        async def feed() -> None:
            assert proc.stdin is not None and data is not None
            try:
                proc.stdin.write(data)
                await proc.stdin.drain()
                proc.stdin.close()
            except (ConnectionError, OSError):
                pass

        assert proc.stdout is not None and proc.stderr is not None
        jobs: list[Any] = [collect(proc.stdout, out_cap), collect_lines(proc.stderr)]
        if data is not None:
            jobs.append(feed())
        try:
            async with asyncio.timeout(seconds):
                results = await asyncio.gather(*jobs)
                code = await proc.wait()
        except TimeoutError:
            self.kill_all()
            raise Fail("time", f"{what} took too long, so the script was stopped.") from None
        self.check_not_stopped()
        return code, results[0], results[1]

    async def prepare(self, wrapped: str) -> str:
        argv = [
            DENO,
            "run",
            *LOCKED_DOWN,
            os.path.join(HERE, "prepare.ts"),
            str(len(PREFIX)),
            str(len(SUFFIX)),
        ]
        code, out, err = await self.capture(
            argv,
            wrapped.encode("utf-8", errors="replace"),
            PREPARE_SECONDS,
            512 * MIB,
            PREPARE_SECONDS + 5,
            "Reading the script",
            OUTPUT_CAP,
        )
        try:
            answer = await parse_json(out)
        except ValueError:
            said = "\n".join(err).lower()
            if "out of memory" in said:
                raise Fail("memory", "The script is too large to read.") from None
            if "stack" in said:
                raise Fail("syntax", "The script is nested too deeply to read.") from None
            raise Fail("sandbox", "The script could not be read.") from None
        if answer.get("ok") is True and isinstance(answer.get("js"), str):
            return answer["js"]
        message = answer.get("message")
        if not isinstance(message, str):
            message = "The code is not valid TypeScript."
        line = answer.get("line")
        raise Fail("syntax", " ".join(message[:1000].split())[:400], self.clamp_line(line))

    def clamp_line(self, line: Any) -> int | None:
        return clamp(line, self.lines)

    def write_check_files(self, wrapped: str) -> None:
        """The files `deno check` reads, in this run's own folder (done off the event loop)."""
        folder = self.scratch
        with open(os.path.join(HERE, "prelude.d.ts"), encoding="utf-8") as handle:
            prelude = handle.read()
        declarations = self.request["declarations"]
        with open(
            os.path.join(folder, "decl.d.ts"), "w", encoding="utf-8", errors="replace"
        ) as handle:
            handle.write(prelude + "\n" + declarations + "\n")
        with open(
            os.path.join(folder, "main.ts"), "w", encoding="utf-8", errors="replace", newline=""
        ) as handle:
            handle.write(wrapped)
        shutil.copy(os.path.join(HERE, "deno.json"), os.path.join(folder, "deno.json"))
        with open(os.path.join(HERE, "import_map.json"), encoding="utf-8") as handle:
            imports = json.load(handle)
        for name in ("main.ts", "decl.d.ts"):
            imports["imports"][f"file://{folder}/{name}"] = f"file://{folder}/{name}"
        with open(os.path.join(folder, "import_map.json"), "w", encoding="utf-8") as handle:
            json.dump(imports, handle)

    def clear_scratch(self) -> None:
        """Nothing of the script stays on disk once it has been checked: not the files Deno read,
        and not what the processes that handled it cached."""
        for folder in (self.scratch, self.deno_dir):
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    with contextlib.suppress(OSError):
                        os.remove(path)

    async def check(self, wrapped: str, problem: Fail | None) -> None:
        """Type check the script. With a syntax error already known, only find where it is."""
        await asyncio.to_thread(self.write_check_files, wrapped)
        folder = self.scratch
        argv = [
            DENO,
            "check",
            f"--config={folder}/deno.json",
            f"--import-map={folder}/import_map.json",
            "--no-remote",
            "--no-npm",
            "--no-lock",
            "--cached-only",
            "--v8-flags=--max-heap-size=384",
            f"{folder}/main.ts",
        ]
        try:
            async with check_slots:
                code, _, lines = await self.capture(
                    argv,
                    None,
                    CHECK_SECONDS,
                    1024 * MIB,
                    CHECK_SECONDS + 5,
                    "Checking the script",
                    4096,
                )
        finally:
            await asyncio.to_thread(self.clear_scratch)
        verdict = await asyncio.to_thread(
            judge_check, lines, code, problem is not None, self.lines, folder
        )
        if verdict is not None:
            raise verdict

    async def spawn(
        self,
        argv: list[str],
        *,
        memory: int,
        cpu: int,
        files: int,
        feed: bool,
        cwd: str,
        limit: int = 2**16,
    ) -> asyncio.subprocess.Process:
        self.check_not_stopped()
        # Every Deno process gets a cache folder of its own, thrown away with the run. A cache that
        # runs shared would let one script leave something for the processes of the next.
        home = os.path.join(self.deno_dir, str(len(self.procs)))
        os.mkdir(home, 0o700)
        env = {
            "DENO_DIR": home,
            "HOME": home,
            "NO_COLOR": "1",
            "DENO_NO_UPDATE_CHECK": "1",
            "DENO_NO_PACKAGE_JSON": "1",
        }
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if feed else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
            start_new_session=True,
            close_fds=True,
            preexec_fn=limits(memory, cpu, files),
            limit=limit,
        )
        self.procs.append(proc)
        return proc

    async def run_script(self, script: str) -> dict[str, Any]:
        nonce = secrets.token_hex(16)
        # --jitless: the script is interpreted, with no compiler to feed. Scripts are glue between
        # calls, so the slowdown does not matter, and most escapes from V8 go through its compilers.
        argv = [
            DENO,
            "run",
            *LOCKED_DOWN,
            f"--import-map={os.path.join(HERE, 'import_map.json')}",
            "--v8-flags=--max-heap-size=128,--jitless",
            os.path.join(HERE, "runner.ts"),
        ]
        proc = await self.spawn(
            argv,
            memory=384 * MIB,
            cpu=int(self.timeout) + 5,
            files=128,
            feed=True,
            cwd="/",
            limit=RUNNER_LINE_LIMIT,
        )
        self.runner = proc
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        errors = asyncio.create_task(tail(proc.stderr, 4096))
        start = {
            "nonce": nonce,
            "code": script,
            "functions": self.functions,
            "max_calls": self.max_calls,
            "max_result_bytes": self.max_result_bytes,
            "max_log_bytes": self.max_log_bytes,
        }
        try:
            async with asyncio.timeout(self.timeout):
                try:
                    proc.stdin.write(dumps(start))
                    await proc.stdin.drain()
                except (ConnectionError, OSError):
                    pass
                self.reply_writer = asyncio.create_task(self.write_replies(proc))
                final = await self.read_runner(proc, nonce)
        except TimeoutError:
            self.kill_all()
            raise Fail(
                "time", f"The script ran for more than {self.timeout:g} seconds, so it was stopped."
            ) from None
        if final is None:
            code = await proc.wait()
            self.check_not_stopped()
            if self.abort is not None:
                raise self.abort
            raise self.died(code, (await errors).decode("utf-8", errors="replace"))
        self.kill_all()
        errors.cancel()
        return self.finish(final)

    def died(self, code: int, stderr: str) -> Fail:
        if code == -signal.SIGXCPU:
            return Fail(
                "time", "The script used more processor time than it may, so it was stopped."
            )
        if "out of memory" in stderr.lower() or code in (
            133,
            -signal.SIGTRAP,
            -signal.SIGABRT,
            -signal.SIGKILL,
        ):
            return Fail("memory", "The script used more memory than it may, so it was stopped.")
        return Fail("sandbox", "The script's process ended without an answer.")

    def finish(self, final: dict[str, Any]) -> dict[str, Any]:
        if final["op"] == "fail":
            stage, message, line = final.get("stage"), final.get("message"), final.get("line")
            if stage not in STAGES or not isinstance(message, str):
                raise Fail("sandbox", "The script gave an answer that was not understood.")
            return self.failure(Fail(stage, message[:4000], self.clamp_line(line)))
        return {
            "op": "done",
            "result": final.get("result"),
            "logs": self.logs,
            "truncated_logs": self.logs_cut,
        }

    # ---- what the runner says

    def stray(self) -> None:
        self.strays += 1
        if self.strays > MOST_STRAYS:
            raise Fail(
                "sandbox", "The script's process broke the rules of the sandbox, so it was stopped."
            )

    async def read_runner(
        self, proc: asyncio.subprocess.Process, nonce: str
    ) -> dict[str, Any] | None:
        assert proc.stdout is not None
        prefix = nonce.encode() + b" "
        while True:
            try:
                raw = await proc.stdout.readline()
            except ValueError:
                raise Fail("limit", "The script sent more data at once than is allowed.") from None
            if not raw:
                return None
            if not raw.startswith(prefix):
                self.stray()
                continue
            try:
                message = await parse_json(raw[len(prefix) :])
            except (ValueError, RecursionError):
                self.stray()
                continue
            op = message.get("op") if isinstance(message, dict) else None
            if op == "log":
                self.add_log(message.get("line"), message.get("cut"))
            elif op == "call":
                await self.forward(message)
            elif op in ("done", "fail"):
                if op == "done" and len(raw) - len(prefix) > self.max_result_bytes + 64:
                    most = self.max_result_bytes // 1024
                    raise Fail(
                        "limit", f"The result is larger than the {most} KB a script may return."
                    )
                return message
            else:
                self.stray()

    def add_log(self, line: Any, cut: Any) -> None:
        """Keep a log line within the limit; `cut` says the runner's own limit was hit."""
        if not isinstance(line, str):
            return self.stray()
        if cut is True:
            self.logs_cut = True
        data = line.encode("utf-8", errors="replace")
        room = self.max_log_bytes - self.log_bytes
        if len(data) > room:
            line = data[: max(room, 0)].decode("utf-8", errors="ignore")
            data = line.encode("utf-8")
            self.logs_cut = True
        if line or cut is not True:
            self.log_bytes += len(data)
            self.logs.append(line)

    async def forward(self, message: dict[str, Any]) -> None:
        seq, name, args = message.get("seq"), message.get("name"), message.get("args")
        if (
            seq != self.calls + 1
            or not isinstance(name, str)
            or name not in self.functions
            or not isinstance(args, dict)
        ):
            raise Fail(
                "sandbox", "The script's process broke the rules of the sandbox, so it was stopped."
            )
        self.calls += 1
        if self.calls > self.max_calls:
            raise Fail("limit", f"The script made more than {self.max_calls} calls.")
        encoded = dumps({"op": "call", "seq": seq, "name": name, "args": args})
        if len(encoded) > MESSAGE_LIMIT + 1024:
            self.reply_to_runner(seq, False, "The arguments of that call are larger than 1 MB.")
            return
        self.waiting.add(seq)
        self.writer.write(encoded)
        await self.writer.drain()

    def reply_to_runner(self, seq: int, ok: bool, value: Any) -> None:
        """Queue a reply for the script. Never waits: the script may not be reading just now.

        The queue is in bytes. Past REPLY_QUEUE_LIMIT the script is not reading its replies, and the
        run is ended as a limit and its process killed, whatever the script is doing.
        """
        if self.abort is not None:
            return
        reply = {"op": "reply", "seq": seq, "ok": ok}
        reply["result" if ok else "error"] = value
        data = dumps(reply)
        if self.outbox_bytes + len(data) > REPLY_QUEUE_LIMIT:
            waiting_mb = REPLY_QUEUE_LIMIT // MIB
            self.abort = Fail(
                "limit",
                "The script is not reading the replies to its calls "
                f"(more than {waiting_mb} MB of them are waiting), so it was stopped.",
            )
            self.outbox.clear()
            self.outbox_bytes = 0
            self.kill_all()
            return
        self.outbox.append(data)
        self.outbox_bytes += len(data)
        self.outbox_ready.set()

    async def write_replies(self, proc: asyncio.subprocess.Process) -> None:
        """The one task that writes to the script's standard input, so the one that may block."""
        assert proc.stdin is not None
        try:
            while True:
                await self.outbox_ready.wait()
                while self.outbox:
                    data = self.outbox.popleft()
                    proc.stdin.write(data)
                    await proc.stdin.drain()
                    self.outbox_bytes -= len(data)
                self.outbox_ready.clear()
        except (ConnectionError, OSError):
            return  # the script's process is gone; reading its output will say so

    # ---- what the app says during a run

    async def listen(self) -> None:
        try:
            await self.take_messages()
        except Exception:  # a run that cannot hear the app is ended, never left hanging
            self.stop()

    async def take_messages(self) -> None:
        """Read the app's socket for the whole run. Nothing here waits for the script."""
        strays = 0
        while True:
            try:
                raw = await self.reader.readline()
            except (ValueError, ConnectionError, OSError):
                return self.stop()
            if not raw:
                return self.stop()
            try:
                message = await parse_json(raw)
            except (ValueError, RecursionError):
                message = None
            op = message.get("op") if isinstance(message, dict) else None
            if op == "cancel":
                return self.stop()
            seq = message.get("seq") if op == "reply" and isinstance(message, dict) else None
            if (
                op == "reply"
                and isinstance(seq, int)
                and seq in self.waiting
                and self.runner is not None
            ):
                self.waiting.discard(seq)
                if len(raw) > MESSAGE_LIMIT + 1024:
                    self.reply_to_runner(seq, False, "The result of that call is larger than 1 MB.")
                elif message.get("ok") is True:
                    self.reply_to_runner(seq, True, message.get("result"))
                else:
                    text = message.get("error")
                    self.reply_to_runner(
                        seq, False, text if isinstance(text, str) else "That call was refused."
                    )
                continue
            strays += 1
            if strays > MOST_STRAYS:
                return self.stop()


def problem_with(request: dict[str, Any]) -> str | None:
    """Why a `run` request cannot be used, in words, or None. Fills in the limits it lacks."""
    functions, declarations = request.get("functions"), request.setdefault("declarations", "")
    if not isinstance(request.get("id"), str) or len(request["id"]) > 100:
        return "The run has no valid id."
    if not isinstance(request.get("code"), str):
        return "The run has no script."
    if not isinstance(declarations, str) or len(declarations) > MOST_DECLARATIONS:
        return "The declarations are not valid."
    if not isinstance(functions, list) or len(functions) > MOST_FUNCTIONS:
        return "The list of functions is not valid."
    if not all(isinstance(n, str) and len(n) <= 100 and NAME.match(n) for n in functions):
        return "The list of functions is not valid."
    if len(set(functions)) != len(functions):
        return "The list of functions has a name twice."
    for key, (default, lowest, highest, whole) in LIMITS.items():
        value = request.setdefault(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"The limit {key} is not valid."
        if (whole and not isinstance(value, int)) or not lowest <= value <= highest:
            return f"The limit {key} is not valid."
    return None


async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await handle(reader, writer)
    except Exception as error:  # nothing a client does may stop the supervisor
        log("connection_error", error=type(error).__name__)
    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 2)
        except (OSError, TimeoutError):
            pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        raw = await asyncio.wait_for(reader.readline(), IDLE_SECONDS)
    except (TimeoutError, ValueError, ConnectionError, OSError):
        return
    if not raw:
        return
    try:
        request = await parse_json(raw)
    except (ValueError, RecursionError):
        request = None
    op = request.get("op") if isinstance(request, dict) else None
    if op == "ping":
        writer.write(dumps({"op": "pong", "deno": deno_version}))
        await writer.drain()
        return
    if op != "run":
        writer.write(dumps(fail_message("The request was not understood.")))
        await writer.drain()
        return
    assert isinstance(request, dict)
    if len(active_runs) >= MOST_RUNS:
        writer.write(dumps(fail_message("Scripts are busy. Try again in a moment.")))
        await writer.drain()
        return
    problem = problem_with(request)
    if problem is not None:
        writer.write(dumps(fail_message(problem)))
        await writer.drain()
        return
    run = Run(request, reader, writer)
    active_runs.add(run)
    try:
        await run.execute()
    finally:
        active_runs.discard(run)


def fail_message(message: str) -> dict[str, Any]:
    return {"op": "fail", "stage": "sandbox", "message": message, "line": None, "logs": []}


async def serve_forever() -> None:
    global check_slots, deno_version
    hide_from_scripts()
    check_slots = asyncio.Semaphore(MOST_CHECKS)
    try:
        shown = subprocess.run(
            [DENO, "--version"], capture_output=True, text=True, timeout=20, check=False
        ).stdout
        deno_version = shown.split()[1] if shown.startswith("deno ") else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        pass
    os.umask(0o007)
    if os.path.exists(SOCKET):
        os.remove(SOCKET)
    server = await asyncio.start_unix_server(serve, path=SOCKET, limit=LINE_LIMIT, backlog=512)
    os.chmod(SOCKET, 0o660)
    log("start", socket=SOCKET, deno=deno_version)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(name, stop.set)
    await stop.wait()
    server.close()
    for run in list(active_runs):
        run.stop()
    with contextlib.suppress(OSError):
        os.remove(SOCKET)


def ping() -> int:
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(SOCKET)
            client.sendall(b'{"op":"ping"}\n')
            answer = json.loads(client.makefile("rb").readline())
    except (OSError, ValueError):
        return 1
    return 0 if answer.get("op") == "pong" else 1


def pipe() -> int:
    """Join standard input and output to the socket, for a test that cannot reach the volume."""
    client = socket.socket(socket.AF_UNIX)
    client.connect(SOCKET)
    watching = [0, client]
    while client in watching:
        for ready in select.select(watching, [], [])[0]:
            if ready == 0:
                chunk = os.read(0, 65536)
                try:
                    if chunk:
                        client.sendall(chunk)
                    else:
                        client.shutdown(socket.SHUT_WR)
                except OSError:
                    chunk = b""  # nobody is listening any more; go on reading what is said
                if not chunk:
                    watching.remove(0)
            else:
                chunk = client.recv(65536)
                if not chunk:
                    return 0
                os.write(1, chunk)
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "ping":
        sys.exit(ping())
    if command == "pipe":
        sys.exit(pipe())
    asyncio.run(serve_forever())
