// Build-time check (see the Dockerfile): the import map must stub every node: module this version of
// Deno has, so that a script cannot reach one of them. The build fails if one is missing.
import { builtinModules } from "node:module";

const map = JSON.parse(Deno.readTextFileSync(Deno.args[0]));
const missing = builtinModules
  .map((name) => (name.startsWith("node:") ? name : "node:" + name))
  .filter((name) => map.imports[name] === undefined);
if (missing.length > 0) {
  console.error("The import map does not stub these modules: " + missing.join(", "));
  Deno.exit(1);
}
