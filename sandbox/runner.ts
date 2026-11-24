// The runner: the one fixed, trusted program that evaluates an agent's script (M37). It runs in its own
// Deno process with no permission of any kind, started by supervisor.py, and talks to it over its
// standard input and output. The script arrives as TEXT and is evaluated with `new AsyncFunction`,
// never loaded as a module (Deno does not check permissions on the static imports of a module).
//
// Lines the runner writes are `<nonce> <json>`. The supervisor ignores any line without the nonce.
// Lines it reads are plain JSON, except the first, which carries the nonce and the script.

const D = Deno;
// `process` is a lazy global that needs `Deno` the first time it is used, so touch it before both go.
void (globalThis as Record<string, unknown>).process;

const { stringify, parse } = JSON;
const encoder = new TextEncoder();
const decoder = new TextDecoder();
const input = D.stdin.readable.getReader();
const output = D.stdout.writable.getWriter();
// Kept before the script can change them.
const objectFreeze = Object.freeze;
const objectDefine = Object.defineProperty;
const setTimer = globalThis.setTimeout;

let buffer = "";
async function readLine(): Promise<string | null> {
  for (;;) {
    const end = buffer.indexOf("\n");
    if (end >= 0) {
      const line = buffer.slice(0, end);
      buffer = buffer.slice(end + 1);
      return line;
    }
    const { value, done } = await input.read();
    if (done) return null;
    buffer += decoder.decode(value, { stream: true });
  }
}

const first = await readLine();
if (first === null) D.exit(2);
const config = parse(first as string);
const nonce: string = config.nonce;
const functionNames: string[] = config.functions;
const maxCalls: number = config.max_calls;
const maxResultBytes: number = config.max_result_bytes;
const maxLogBytes: number = config.max_log_bytes;
const ONE_MB = 1024 * 1024;

function send(text: string): Promise<void> {
  return output.write(encoder.encode(nonce + " " + text + "\n"));
}
const sendObject = (value: object) => send(stringify(value));

class VrumError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "VrumError";
  }
}
// What a call that is over the limit rejects with: still a VrumError to the script, but the run is
// reported as stopped by a limit when nothing catches it. The script cannot make one.
class CallLimitError extends VrumError {}

// What V8 puts in front of the body of a function made with `new AsyncFunction`, in lines: found by
// asking it, so a change of engine cannot make the line numbers of an error wrong.
const AsyncFunction = (async () => {}).constructor as new (...parts: string[]) => (...args: unknown[]) => Promise<unknown>;
const USER_FRAME = /<anonymous>:(\d+):\d+\)?$/;
function userLine(error: unknown, offset: number): number | null {
  const stack = error instanceof Error ? String(error.stack) : "";
  for (const frame of stack.split("\n")) {
    if (!frame.includes("eval at")) continue;
    const found = USER_FRAME.exec(frame);
    if (found) return Math.max(1, Number(found[1]) - offset);
  }
  return null;
}
let lineOffset = 0;
try {
  void new AsyncFunction("a", "b", "c", '"use strict";throw new Error("probe")')().catch((error: unknown) => {
    const found = userLine(error, 0);
    if (found !== null) lineOffset = found - 1;
  });
} catch {
  // keep zero
}
await new Promise((resolve) => setTimer(resolve, 0));
const lineOffsetOfTheBody = lineOffset;

// Logs: every line is sent as it happens, cut to the allowed size.
let logBytes = 0;
let logsCut = false;
function format(value: unknown): string {
  if (typeof value === "string") return value;
  if (value instanceof Error) return value.name === "Error" ? value.message : value.name + ": " + value.message;
  if (typeof value === "function") return "[Function]";
  try {
    const text = stringify(value, (_key, item) => (typeof item === "bigint" ? item.toString() : item));
    return text === undefined ? String(value) : text;
  } catch {
    return String(value);
  }
}
function log(...values: unknown[]): void {
  if (logsCut) return;
  let text = values.map(format).join(" ");
  const room = maxLogBytes - logBytes;
  let size = encoder.encode(text).length;
  if (size > room) {
    logsCut = true;
    const cut = decoder.decode(encoder.encode(text).slice(0, Math.max(room, 0)));
    text = cut.endsWith("\ufffd") ? cut.slice(0, -1) : cut;
    size = encoder.encode(text).length;
  }
  logBytes += size;
  void sendObject({ op: "log", line: text, cut: logsCut });
}
const scriptConsole = Object.create(null);
for (const name of ["log", "info", "warn", "error", "debug"]) scriptConsole[name] = log;
objectFreeze(scriptConsole);

// Calls: one framed line out, one reply in. A script can start many at once.
type Waiting = { resolve: (value: unknown) => void; reject: (error: unknown) => void; error: VrumError };
const waiting = new Map<number, Waiting>();
let calls = 0;
let overLimit = 0;
let idle: (() => void) | null = null;

function fail(stage: string, message: string, line: number | null): Promise<void> {
  return sendObject({ op: "fail", stage, message, line });
}

function call(name: string, args: unknown): Promise<unknown> {
  // The error is made here, while the script's own frames are on the stack, so a refusal can say
  // which line of the script made the call.
  if (calls >= maxCalls) {
    overLimit += 1;
    if (overLimit >= 20) {
      void fail("limit", `The script went on calling after using its ${maxCalls} calls, so it was stopped.`, null).then(() => D.exit(1));
    }
    return Promise.reject(new CallLimitError(`This script has already made its ${maxCalls} calls. Do the rest in another run.`));
  }
  const error = new VrumError("");
  let text: string | undefined;
  if (args === undefined) {
    text = "{}";
  } else if (typeof args !== "object" || args === null || Array.isArray(args)) {
    error.message = `The arguments of ${name} must be an object.`;
    return Promise.reject(error);
  } else {
    try {
      text = stringify(args);
    } catch {
      error.message = `The arguments of ${name} cannot be sent: they are not plain data.`;
      return Promise.reject(error);
    }
  }
  if (text === undefined || text.length > ONE_MB || encoder.encode(text).length > ONE_MB) {
    error.message = `The arguments of ${name} are larger than 1 MB.`;
    return Promise.reject(error);
  }
  calls += 1;
  const seq = calls;
  return new Promise((resolve, reject) => {
    waiting.set(seq, { resolve, reject, error });
    void send('{"op":"call","seq":' + seq + ',"name":' + stringify(name) + ',"args":' + text + "}");
  });
}

async function pump(): Promise<void> {
  for (;;) {
    const line = await readLine();
    if (line === null) D.exit(2);
    let message;
    try {
      message = parse(line as string);
    } catch {
      continue;
    }
    if (message === null || typeof message !== "object" || message.op !== "reply") continue;
    const entry = waiting.get(message.seq);
    if (entry === undefined) continue;
    waiting.delete(message.seq);
    if (message.ok === true) {
      entry.resolve(message.result === undefined ? null : message.result);
    } else {
      entry.error.message = typeof message.error === "string" ? message.error : "That call was refused.";
      entry.reject(entry.error);
    }
    if (waiting.size === 0 && idle !== null) {
      const wake = idle;
      idle = null;
      wake();
    }
  }
}
void pump();

const vrum = Object.create(null);
for (const name of functionNames) {
  objectDefine(vrum, name, { value: (args?: unknown) => call(name, args), enumerable: true });
}
objectFreeze(vrum);

// Errors that nothing caught (a call that was refused and not awaited, a throw in a timer) fail the run.
let unhandled: unknown = undefined;
let hasUnhandled = false;
function noteUnhandled(error: unknown): void {
  if (!hasUnhandled) {
    hasUnhandled = true;
    unhandled = error;
  }
}
addEventListener("unhandledrejection", (event) => {
  event.preventDefault();
  noteUnhandled((event as PromiseRejectionEvent).reason);
});
addEventListener("error", (event) => {
  event.preventDefault();
  noteUnhandled((event as ErrorEvent).error ?? (event as ErrorEvent).message);
});

// The script gets the language and a few harmless helpers, and nothing else.
const KEEP = new Set([
  "AggregateError", "Array", "ArrayBuffer", "AsyncDisposableStack", "Atomics", "BigInt", "BigInt64Array",
  "BigUint64Array", "Boolean", "DataView", "Date", "DisposableStack", "Error", "EvalError", "FinalizationRegistry",
  "Float16Array", "Float32Array", "Float64Array", "Function", "Infinity", "Int16Array", "Int32Array", "Int8Array",
  "Intl", "Iterator", "JSON", "Map", "Math", "NaN", "Number", "Object", "Promise", "Proxy", "RangeError",
  "ReferenceError", "Reflect", "RegExp", "Set", "SharedArrayBuffer", "String", "SuppressedError", "Symbol",
  "SyntaxError", "Temporal", "TypeError", "URIError", "Uint16Array", "Uint32Array", "Uint8Array", "Uint8ClampedArray",
  "WeakMap", "WeakRef", "WeakSet", "decodeURI", "decodeURIComponent", "encodeURI", "encodeURIComponent", "escape",
  "eval", "globalThis", "isFinite", "isNaN", "parseFloat", "parseInt", "undefined", "unescape",
  "atob", "btoa", "clearTimeout", "queueMicrotask", "setTimeout", "structuredClone",
]);
const globalScope = globalThis as Record<string, unknown>;
for (const name of Object.getOwnPropertyNames(globalScope)) {
  if (KEEP.has(name)) continue;
  try {
    delete globalScope[name];
  } catch {
    // a property that cannot be deleted is left alone
  }
  if (name in globalScope) {
    try {
      objectDefine(globalScope, name, { value: undefined, configurable: true, writable: true });
    } catch {
      // nothing more can be done here
    }
  }
}

const NOT_AVAILABLE =
  "A script has no files, network or system access and cannot import anything: it has the language, `console` and `vrum`.";
function plainMessage(text: string): string {
  if (/Requires \w+ access|NotCapable|PermissionDenied/.test(text)) return NOT_AVAILABLE;
  if (/specifiers? w(as|ere) requested|Module not found|Unsupported scheme|not a dependency/.test(text)) return NOT_AVAILABLE;
  return text;
}

function describe(error: unknown): { stage: string; message: string; line: number | null } {
  try {
    return describeError(error);
  } catch {
    // an error object that cannot even be read
    return { stage: "error", message: "The script failed with an error that cannot be described.", line: null };
  }
}

function describeError(error: unknown): { stage: string; message: string; line: number | null } {
  const line = userLine(error, lineOffsetOfTheBody);
  if (error instanceof CallLimitError) return { stage: "limit", message: error.message, line };
  if (error instanceof VrumError) return { stage: "refused", message: error.message, line };
  if (error instanceof Error) {
    const text = error.name === "Error" ? error.message : error.name + ": " + error.message;
    const memory = error instanceof RangeError && /allocation failed|out of memory/i.test(error.message);
    return { stage: memory ? "memory" : "error", message: plainMessage(text).slice(0, 2000), line };
  }
  return { stage: "error", message: plainMessage(String(error)).slice(0, 2000), line: null };
}

let outcome: { ok: true; value: unknown } | { ok: false; error: unknown };
try {
  const body = new AsyncFunction("vrum", "console", "VrumError", '"use strict";' + config.code);
  outcome = { ok: true, value: await body(vrum, scriptConsole, VrumError) };
} catch (error) {
  outcome = { ok: false, error };
}

// Calls the script started and did not wait for are still waited for, so that a write that began is
// finished and listed, and nothing keeps running unseen.
for (;;) {
  while (waiting.size > 0) await new Promise<void>((resolve) => (idle = resolve));
  await new Promise((resolve) => setTimer(resolve, 0));
  if (waiting.size === 0) break;
}

if (outcome.ok && hasUnhandled) outcome = { ok: false, error: unhandled };
if (!outcome.ok) {
  const found = describe(outcome.error);
  await fail(found.stage, found.message, found.line);
} else {
  let text: string | undefined;
  try {
    text = stringify(outcome.value);
  } catch (error) {
    await fail("error", "The value the script returned cannot be sent: " + String((error as Error)?.message), null);
    D.exit(0);
  }
  const result = text === undefined ? "null" : (text as string);
  const size = encoder.encode(result).length;
  if (size > maxResultBytes) {
    await fail(
      "limit",
      `The result is ${Math.ceil(size / 1024)} KB, and a script may return at most ${Math.floor(maxResultBytes / 1024)} KB. Return only what you need.`,
      null,
    );
  } else {
    await send('{"op":"done","result":' + result + "}");
  }
}
D.exit(0);
