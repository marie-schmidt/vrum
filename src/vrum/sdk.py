"""The TypeScript an agent that runs code sees: what `vrum` can do inside a script.

Every tool of the server is a function of the object `vrum`, with the same arguments and the same
result, and this module writes that object's type from the tools' own JSON Schemas (the input
schema of each tool, and the output schema the MCP library makes from its return type). An agent
that is told the exact shapes does better than one that has to guess them, so the text carries the
result of every function, not only its arguments:

- A field that is always there is not optional. The library's output schema leaves out of
  `required` every field that has a default, although the result always holds it (the result is
  dumped whole), so for results a field with a default counts as required, except one marked
  `x-left-out-when-empty` (items.py: an `id` that is empty is left out). Arguments keep their own:
  a field with a default is optional there.
- The library wraps a result that is not an object (a list, a number) as `{"result": ...}`. The
  server hands a script the bare value, and the type here says the bare value (`ToolSpec.wrapped`).
- A type that refers back to itself, directly or through others, is `unknown` where it does. A
  schema this module does not understand is `unknown` too, never an error.
- A comment never ends early: `*/` in a description is escaped. A tool whose name is not a plain
  identifier is written as a quoted member.

`Sdk` is built once from every tool the server has: that way a type has the same name whichever
functions a script's key is allowed (two different types that would share a name get a number
after it). `declarations(names, compact=...)` is the text for chosen functions: the type aliases
they use once each, then `declare const vrum: {...}`. Compact keeps the first sentence of each
description and of each argument, and no comment on the fields of a result; an argument that
several functions share and describe in the same words (a node's reference, a limit) is described
where it first appears and not again; full keeps all of them. `index(names)` is one line for each
function."""

import json
import re
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, NamedTuple

from vrum.surface import in_order

# How deep a schema is followed before the rest is `unknown`, so that a hostile or odd one cannot
# make the text endless.
_MOST_DEPTH = 16
# The longest a one line summary or a short comment may be, in characters.
_SUMMARY_CHARS = 110
_ARGUMENT_COMMENT_CHARS = 140
_WRAP_COLUMNS = 96
# The most texts `declarations` keeps for later: one for each different set of functions asked for.
_MOST_TEXTS = 256
# The most functions `Sdk.find` returns.
_MOST_FOUND = 10

_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_SENTENCE = re.compile(r"(.+?[.!?])(?=\s+[A-Z`(\[]|\s*$)", re.S)

# Names a type alias must not take: the types every script has (a name always starts with a capital
# letter here, so the lower case words of the language cannot clash).
_RESERVED_TYPES = frozenset(
    {
        *("Array", "ReadonlyArray", "Promise", "Record", "Partial", "Required", "Readonly"),
        *("Pick", "Omit", "Exclude", "Extract", "NonNullable", "Awaited", "Date", "Error"),
        *("RegExp", "Map", "Set", "WeakMap", "WeakSet", "Function", "Object", "String"),
        *("Number", "Boolean", "Symbol", "BigInt", "JSON", "Math", "Iterator", "Generator"),
        *("AsyncIterator", "Proxy", "Reflect", "Intl", "Atomics", "ArrayBuffer", "DataView"),
        *("Uint8Array", "VrumError"),
    }
)


@dataclass(frozen=True)
class ToolSpec:
    """What the text needs of one tool. `wrapped` is true when the library wraps the tool's result
    as `{"result": ...}`; `reads_only` is the tool's own read only mark."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    wrapped: bool = False
    reads_only: bool = False


class IndexEntry(NamedTuple):
    name: str
    summary: str
    reads_only: bool


@dataclass(frozen=True)
class _Detail:
    """How much comment goes with the types: for arguments and for the fields of results, none,
    the first sentence, or all of it."""

    arguments: str
    results: str


_COMPACT = _Detail("short", "none")
_FULL = _Detail("full", "full")


class _Ts(NamedTuple):
    """A piece of TypeScript and how tightly it binds, so that it is put in brackets only when
    something follows it (`(A | B)[]`). A union also keeps its members."""

    text: str
    binds: str = "atom"
    members: tuple[str, ...] = ()


_UNKNOWN = _Ts("unknown")
_NEVER = _Ts("never")


def function_name(name: str) -> str:
    """How a function is written as a member of `vrum`: itself when it is a plain identifier (a
    reserved word is fine there), a quoted name when it is not."""
    return name if _IDENTIFIER.fullmatch(name) else json.dumps(name)


def type_name(raw: str) -> str:
    """A name for a type alias made from any text: words joined with a capital each, a letter
    first, and never a word the language or every script already has."""
    parts = re.findall(r"[A-Za-z0-9]+", raw)
    joined = "".join(part[:1].upper() + part[1:] for part in parts) or "Value"
    if joined[0].isdigit():
        joined = "T" + joined
    return joined + "Type" if joined in _RESERVED_TYPES else joined


def _literal(value: Any) -> str:
    """A JSON value as a TypeScript literal type."""
    if isinstance(value, (str, bool, int, float)) or value is None:
        return json.dumps(value) if value is not None else "null"
    return "unknown"


def _plain_text(text: str) -> list[str]:
    """A description as paragraphs: lines that belong together joined into one, blank lines kept
    as the breaks, and the indentation of a docstring gone."""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in text.replace("\r", "").split("\n"):
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def first_sentence(text: str, most: int = _SUMMARY_CHARS) -> str:
    """The first sentence of a description, at most `most` characters (cut at a word, with three
    dots, when it is longer)."""
    paragraphs = _plain_text(text)
    if not paragraphs:
        return ""
    found = _SENTENCE.match(paragraphs[0])
    sentence = found.group(1) if found else paragraphs[0]
    if len(sentence) <= most:
        return sentence
    cut = sentence[: most - 3].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "..."


def _escaped(text: str) -> str:
    return text.replace("*/", "*\\/")


def _describe_arguments_once(members: list[str]) -> list[str]:
    """The functions' texts with a repeated argument comment left out: an argument (written at four
    spaces) whose comment is word for word one already written above, for an argument of the same
    name, is described where it first appears and not again."""
    seen: set[tuple[str, str]] = set()
    written = []
    for text in members:
        lines = text.split("\n")
        kept: list[str] = []
        at = 0
        while at < len(lines):
            line = lines[at]
            if not line.startswith("    /**"):
                kept.append(line)
                at += 1
                continue
            end = at
            while not lines[end].rstrip().endswith("*/"):
                end += 1
            block = lines[at : end + 1]
            after = lines[end + 1] if end + 1 < len(lines) else ""
            found = re.match(r"    (\w+)\??:", after)
            at = end + 1
            if found is not None:
                key = (found.group(1), "\n".join(block))
                if key in seen:
                    continue
                seen.add(key)
            kept.extend(block)
        written.append("\n".join(kept))
    return written


def _comment(paragraphs: list[str], indent: int) -> list[str]:
    """A doc comment as lines at this indentation: one line when it is short and has one
    paragraph, a block otherwise. Nothing when there is no text."""
    paragraphs = [_escaped(paragraph) for paragraph in paragraphs if paragraph]
    if not paragraphs:
        return []
    pad = " " * indent
    if len(paragraphs) == 1 and len(paragraphs[0]) <= _WRAP_COLUMNS - indent:
        return [f"{pad}/** {paragraphs[0]} */"]
    lines = [f"{pad}/**"]
    for number, paragraph in enumerate(paragraphs):
        if number:
            lines.append(f"{pad} *")
        lines.extend(
            f"{pad} * {line}" for line in textwrap.wrap(paragraph, _WRAP_COLUMNS - indent - 3)
        )
    lines.append(f"{pad} */")
    return lines


def _union(parts: list[_Ts]) -> _Ts:
    """A union of types, without repeats and with null last."""
    seen: list[str] = []
    for part in parts:
        for piece in part.members if part.binds == "union" else (part.text,):
            if piece not in seen:
                seen.append(piece)
    if "unknown" in seen:
        return _UNKNOWN
    if "null" in seen and seen[-1] != "null":
        seen.remove("null")
        seen.append("null")
    if len(seen) == 1:
        return _Ts(seen[0])
    return _Ts(" | ".join(seen), "union", tuple(seen))


def _bracketed(part: _Ts) -> str:
    return f"({part.text})" if part.binds != "atom" else part.text


class _Context:
    """The definitions one schema carries, and which side of a call it describes ("in" for the
    arguments, "out" for the result)."""

    def __init__(self, schema: dict[str, Any], side: str) -> None:
        defs = schema.get("$defs")
        self.defs: dict[str, Any] = defs if isinstance(defs, dict) else {}
        self.side = side
        self.named: dict[str, str] = {}
        self._reach: dict[str, set[str]] = {}

    def definition(self, ref: Any) -> str | None:
        found = re.fullmatch(r"#/\$defs/(.+)", ref) if isinstance(ref, str) else None
        return found.group(1) if found and found.group(1) in self.defs else None

    def reaches(self, start: str) -> set[str]:
        """Every definition that `start` refers to, directly or through others."""
        if start not in self._reach:
            found: set[str] = set()
            pending = [start]
            while pending:
                for name in _refs_in(self.defs.get(pending.pop()), self):
                    if name not in found:
                        found.add(name)
                        pending.append(name)
            self._reach[start] = found
        return self._reach[start]


def _refs_in(node: Any, context: _Context) -> list[str]:
    """The definitions a piece of schema refers to (not following them)."""
    if isinstance(node, dict):
        found = []
        name = context.definition(node.get("$ref"))
        if name is not None:
            found.append(name)
        for key, value in node.items():
            if key != "$ref":
                found.extend(_refs_in(value, context))
        return found
    if isinstance(node, list):
        return [name for item in node for name in _refs_in(item, context)]
    return []


class _Function(NamedTuple):
    spec: ToolSpec
    arguments: _Context
    result: _Context | None


class Sdk:
    """The declarations of a server's tools as TypeScript. Build it once from all the tools."""

    def __init__(self, specs: Iterable[ToolSpec]) -> None:
        self._functions: dict[str, _Function] = {}
        # Each type alias by the name it has: where its text comes from (the definition of its
        # tool's schema, or a result's own root) and the text it has in full.
        self._aliases: dict[str, tuple[_Context, str | None, dict[str, Any]]] = {}
        self._claimed: dict[tuple[str, str], str] = {}
        self._texts: dict[tuple[tuple[str, ...], bool], str] = {}
        for spec in sorted(specs, key=lambda one: one.name):
            result = None
            if spec.output_schema is not None:
                result = _Context(spec.output_schema, "out")
            self._functions[spec.name] = _Function(spec, _Context(spec.input_schema, "in"), result)
        for name in self._functions:
            self._member(name, _FULL)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(in_order(self._functions))

    def has(self, name: str) -> bool:
        return name in self._functions

    def reads_only(self, name: str) -> bool:
        """Whether the tool of this name only reads. A name that is not a function counts as
        changing something."""
        return name in self._functions and self._functions[name].spec.reads_only

    def index(self, names: Iterable[str]) -> list[IndexEntry]:
        """One line for each of these functions that exists: its name, its first sentence and
        whether it only reads."""
        return [
            IndexEntry(
                name,
                first_sentence(self._functions[name].spec.description),
                self._functions[name].spec.reads_only,
            )
            for name in in_order(one for one in names if one in self._functions)
        ]

    def find(self, names: Iterable[str], query: str) -> list[str]:
        """The functions among `names` that the words of `query` fit, best first (a word in a
        function's name counts more than one in its description), at most ten. A word is matched
        by its beginning, without a plural s, so that notes finds note and criteria finds
        criterion."""
        words = [
            word.rstrip("s")[:6]
            for word in re.findall(r"[a-z0-9_]+", query.lower())
            if len(word) > 1
        ]
        scored: list[tuple[int, int, str]] = []
        for order, name in enumerate(in_order(one for one in names if one in self._functions)):
            description = self._functions[name].spec.description.lower()
            score = sum(3 for word in words if word in name) + sum(
                1 for word in words if word in description
            )
            if score:
                scored.append((-score, order, name))
        return [name for _, _, name in sorted(scored)[:_MOST_FOUND]]

    def declarations(self, names: Iterable[str], *, compact: bool) -> str:
        """The TypeScript for these functions (those among them that exist): the type aliases they
        use, each once and by name, then `declare const vrum` with a member for each. Names that
        are not functions are left out. The same functions give the same text, made once."""
        chosen = in_order(one for one in names if one in self._functions)
        key = (tuple(chosen), compact)
        if key not in self._texts:
            if len(self._texts) >= _MOST_TEXTS:
                self._texts.clear()
            self._texts[key] = self._written(chosen, _COMPACT if compact else _FULL)
        return self._texts[key]

    def _written(self, chosen: list[str], detail: _Detail) -> str:
        members: list[str] = []
        needed: set[str] = set()
        for name in chosen:
            text, used = self._member(name, detail)
            members.append(text)
            needed |= used
        if detail is _COMPACT:
            members = _describe_arguments_once(members)
        aliases: dict[str, str] = {}
        pending = sorted(needed)
        while pending:
            alias = pending.pop()
            if alias in aliases:
                continue
            text, used = self._alias_text(alias, detail)
            aliases[alias] = text
            pending.extend(sorted(used - set(aliases)))
        parts = [aliases[alias] for alias in sorted(aliases)]
        parts.append("declare const vrum: {\n" + "\n".join(members) + "\n};")
        return "\n\n".join(parts) + "\n"

    # -- writing one function and one type ---------------------------------------------------

    def _member(self, name: str, detail: _Detail) -> tuple[str, set[str]]:
        found = self._functions[name]
        spec = found.spec
        writer = _Writer(self, found.arguments, detail)
        arguments = spec.input_schema
        properties = arguments.get("properties")
        has_arguments = isinstance(properties, dict) and bool(properties)
        argument_text = ""
        if has_arguments:
            optional = not _required_of(arguments, found.arguments)
            body = writer.object_type(arguments, 2, None, 0)
            argument_text = f"args{'?' if optional else ''}: {body.text}"
        used = set(writer.used)
        result = self._result_type(found, detail)
        used |= result.used
        if detail is _COMPACT:
            summary = first_sentence(spec.description, 200)
            lines = _comment([f"{summary} Only reads." if spec.reads_only else summary], 2)
        else:
            paragraphs = _plain_text(spec.description)
            lines = _comment(paragraphs + (["Only reads."] if spec.reads_only else []), 2)
        lines.append(f"  {function_name(name)}({argument_text}): Promise<{result.text}>;")
        return "\n".join(lines), used

    def _result_type(self, found: _Function, detail: _Detail) -> "_Written":
        """The type of what a call gives back: an alias named for the result when it is an object
        with fields, otherwise written in place."""
        context = found.result
        schema = found.spec.output_schema
        if context is None or schema is None:
            return _Written("unknown", set())
        root: Any = schema
        if found.spec.wrapped:
            properties = schema.get("properties")
            root = properties.get("result", True) if isinstance(properties, dict) else True
        writer = _Writer(self, context, detail)
        if isinstance(root, dict) and isinstance(root.get("properties"), dict):
            alias = self._alias_for_root(found, context, root)
            return _Written(alias, {alias})
        return _Written(writer.type_of(root, 2, None, 0).text, writer.used)

    def _alias_for_root(self, found: _Function, context: _Context, root: dict[str, Any]) -> str:
        key = f"root:{found.spec.name}"
        if key in context.named:
            return context.named[key]
        title = root.get("title")
        base = type_name(title if isinstance(title, str) and title else f"{found.spec.name} result")
        alias = self._claim(base, context, None, root)
        context.named[key] = alias
        return alias

    def alias_for(self, context: _Context, definition: str) -> str:
        """The alias of one of a schema's definitions: made, with its name, the first time it is
        asked for."""
        if definition in context.named:
            return context.named[definition]
        alias = self._claim(type_name(definition), context, definition, context.defs[definition])
        context.named[definition] = alias
        return alias

    def _claim(
        self, base: str, context: _Context, definition: str | None, schema: dict[str, Any]
    ) -> str:
        """A name for a type, the same one for the same type however many tools use it. The text
        in full decides whether two are the same, so two different types never share a name."""
        text = _Writer(self, context, _FULL).alias_body(schema, definition).text
        known = self._claimed.get((base, text))
        if known is not None:
            return known
        alias, number = base, 1
        while alias in self._aliases:
            number += 1
            alias = f"{base}{number}"
        self._claimed[(base, text)] = alias
        self._aliases[alias] = (context, definition, schema)
        return alias

    def _alias_text(self, alias: str, detail: _Detail) -> tuple[str, set[str]]:
        context, definition, schema = self._aliases[alias]
        writer = _Writer(self, context, detail)
        body = writer.alias_body(schema, definition)
        return f"type {alias} = {body.text};", writer.used


class _Written(NamedTuple):
    text: str
    used: set[str]


def _required_of(schema: dict[str, Any], context: _Context) -> set[str]:
    """The fields of an object that are always there: the ones it lists as required, and for a
    result also the ones that have a default (a result is dumped whole, defaults and all), except
    a field marked `x-left-out-when-empty`, which the model leaves out of what it gives when it
    has no value."""
    required = schema.get("required")
    found = (
        {name for name in required if isinstance(name, str)}
        if isinstance(required, list)
        else set()
    )
    properties = schema.get("properties")
    if context.side == "out" and isinstance(properties, dict):
        found |= {
            name
            for name, spec in properties.items()
            if isinstance(spec, dict)
            and "default" in spec
            and not spec.get("x-left-out-when-empty")
        }
    return found


class _Writer:
    """Writes the TypeScript of schemas of one context, and notes which aliases it used."""

    def __init__(self, sdk: Sdk, context: _Context, detail: _Detail) -> None:
        self._sdk = sdk
        self._context = context
        self._detail = detail
        self.used: set[str] = set()

    def alias_body(self, schema: dict[str, Any], definition: str | None) -> _Ts:
        return self.type_of(schema, 0, definition, 0)

    def type_of(self, schema: Any, indent: int, parent: str | None, level: int) -> _Ts:
        """The type a schema describes. `indent` is the column of the line it starts on, `parent`
        the definition being written (so that a reference back to it is unknown)."""
        if schema is True or schema == {}:
            return _UNKNOWN
        if schema is False:
            return _NEVER
        if not isinstance(schema, dict) or level > _MOST_DEPTH:
            return _UNKNOWN
        if "$ref" in schema:
            return self._reference(schema["$ref"], parent)
        if "const" in schema:
            return _Ts(_literal(schema["const"]))
        enum = schema.get("enum")
        if isinstance(enum, list):
            return _union([_Ts(_literal(value)) for value in enum]) if enum else _NEVER
        for key in ("anyOf", "oneOf"):
            options = schema.get(key)
            if isinstance(options, list) and options:
                return _union([self.type_of(one, indent, parent, level + 1) for one in options])
        parts = schema.get("allOf")
        if isinstance(parts, list) and parts:
            written = [self.type_of(one, indent, parent, level + 1) for one in parts]
            if len(written) == 1:
                return written[0]
            return _Ts(" & ".join(_bracketed(one) for one in written), "intersection")
        kind = schema.get("type")
        if isinstance(kind, list):
            return _union(
                [self.type_of({**schema, "type": one}, indent, parent, level + 1) for one in kind]
            )
        if kind == "string":
            return _Ts("string")
        if kind in ("integer", "number"):
            return _Ts("number")
        if kind == "boolean":
            return _Ts("boolean")
        if kind == "null":
            return _Ts("null")
        if kind == "array" or (kind is None and "items" in schema):
            return self._array(schema, indent, parent, level)
        if kind == "object" or (kind is None and "properties" in schema):
            return self.object_type(schema, indent, parent, level)
        return _UNKNOWN

    def _reference(self, ref: Any, parent: str | None) -> _Ts:
        definition = self._context.definition(ref)
        if definition is None:
            return _UNKNOWN
        # Types that refer to each other are cut where they close the circle: a reference to a
        # type in the same circle is `unknown` when it points to a name that is not later in the
        # alphabet, so what is kept never circles.
        if (
            parent is not None
            and parent in self._context.reaches(definition)
            and definition <= parent
        ):
            return _UNKNOWN
        alias = self._sdk.alias_for(self._context, definition)
        self.used.add(alias)
        return _Ts(alias)

    def _array(self, schema: dict[str, Any], indent: int, parent: str | None, level: int) -> _Ts:
        items = schema.get("items")
        prefix = schema.get("prefixItems")
        if isinstance(prefix, list):
            members = [self.type_of(one, indent, parent, level + 1).text for one in prefix]
            if items not in (None, False):
                rest = self.type_of(items, indent, parent, level + 1)
                members.append(f"...{_bracketed(rest)}[]")
            return _Ts("[" + ", ".join(members) + "]")
        if items is None:
            return _Ts("unknown[]")
        return _Ts(f"{_bracketed(self.type_of(items, indent, parent, level + 1))}[]")

    def object_type(
        self, schema: dict[str, Any], indent: int, parent: str | None, level: int
    ) -> _Ts:
        """An object type with a member for each property, optional when it is not required, and
        an index signature when the schema allows other properties."""
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        extra = schema.get("additionalProperties")
        required = _required_of(schema, self._context)
        pad = " " * (indent + 2)
        lines: list[str] = []
        for name, spec in properties.items():
            if not isinstance(name, str):
                continue
            lines.extend(self._property_comment(spec, indent + 2))
            marker = "" if name in required else "?"
            written = self.type_of(spec, indent + 2, parent, level + 1)
            lines.append(f"{pad}{function_name(name)}{marker}: {written.text};")
        if extra is not False and (extra is not None or not properties):
            if properties or extra in (None, True, {}):
                index_type = "unknown"
            else:
                index_type = self.type_of(extra, indent + 2, parent, level + 1).text
            lines.append(f"{pad}[key: string]: {index_type};")
        if extra is False and not properties:
            lines.append(f"{pad}[key: string]: never;")
        if not lines:
            return _Ts("{}")
        return _Ts("{\n" + "\n".join(lines) + "\n" + " " * indent + "}")

    def _property_comment(self, spec: Any, indent: int) -> list[str]:
        how = self._detail.arguments if self._context.side == "in" else self._detail.results
        description = spec.get("description") if isinstance(spec, dict) else None
        if how == "none" or not isinstance(description, str):
            return []
        if how == "short":
            return _comment([first_sentence(description, _ARGUMENT_COMMENT_CHARS)], indent)
        return _comment(_plain_text(description), indent)
