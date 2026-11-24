// Step 1 of a run (see supervisor.py): turn the script's TypeScript into JavaScript, and report a
// syntax error. It runs in its own Deno process with no permission of any kind and only ever PARSES
// the text; nothing in it is evaluated except to compile it once as a function body.
//
// Standard input is the script wrapped as one function (the supervisor says how many characters it
// added before and after, as the two arguments). Standard output is one JSON line:
//   {"ok": true, "js": "<the body, types removed>"}  or  {"ok": false, "message": "...", "line": 7 | null}
import { stripTypeScriptTypes } from "node:module";

const prefixLength = Number(Deno.args[0]);
const suffixLength = Number(Deno.args[1]);
const wrapped = await new Response(Deno.stdin.readable).text();

function reply(value: object): never {
  Deno.stdout.writeSync(new TextEncoder().encode(JSON.stringify(value) + "\n"));
  Deno.exit(0);
}

const UNSUPPORTED: [RegExp, string][] = [
  [/enum is not supported/, "`enum` is not available here. Use a plain object with `as const` instead."],
  [/namespace/, "`namespace` is not available here. Use a plain object instead."],
  [/parameter propert/, "Class parameter properties are not available here. Declare the field in the class body."],
];

let stripped: string;
try {
  stripped = stripTypeScriptTypes(wrapped, { mode: "strip" });
} catch (error) {
  const text = String((error as Error).message);
  const where = /:(\d+):\d+$/.exec(text);
  const line = where ? Number(where[1]) : null;
  const known = UNSUPPORTED.find(([pattern]) => pattern.test(text));
  if (known) reply({ ok: false, message: known[1], line });
  if (line !== null) reply({ ok: false, message: text.replace(/ at file:\/\/\/\S+$/, ""), line });
  reply({ ok: false, message: "The code is not valid TypeScript.", line: null });
}

const body = stripped.slice(prefixLength, stripped.length - suffixLength);
const AsyncFunction = (async () => {}).constructor as new (...parts: string[]) => unknown;
try {
  new AsyncFunction("vrum", "console", "VrumError", '"use strict";' + body);
} catch (error) {
  reply({ ok: false, message: "The code is not valid here: " + (error as Error).message, line: null });
}
reply({ ok: true, js: body });
