"""The TypeScript that an agent running code sees (sdk.py): made from the tools' own schemas, the
same every time, safe to put in a comment, exact about results, and matching what each tool really
gives back. No containers: whether the text is valid TypeScript for every tool is proved with
`deno check` in the integration test."""

import json
import re
from collections.abc import Iterator
from functools import cache
from typing import Any

from pydantic import TypeAdapter

from vrum.config import Settings
from vrum.guard import GuardedServer
from vrum.sdk import IndexEntry, Sdk, ToolSpec, first_sentence, function_name, type_name
from vrum.server import create_server
from vrum.surface import AGENT_TOOLS

ENV = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret"}


@cache
def the_server() -> GuardedServer:
    return create_server(Settings.from_env(ENV))


def spec(
    name: str = "do_it",
    description: str = "Do it.",
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    wrapped: bool = False,
    reads_only: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name,
        description,
        arguments if arguments is not None else {"type": "object", "properties": {}},
        result,
        wrapped,
        reads_only,
    )


def declared(*specs: ToolSpec, compact: bool = False) -> str:
    sdk = Sdk(specs)
    return sdk.declarations(sdk.names, compact=compact)


# -- every tool, the same every time ----------------------------------------------------------


def test_every_tool_the_server_has_gets_declarations_and_they_are_the_same_every_time() -> None:
    sdk = the_server().sdk
    names = set(sdk.names)
    live = {tool.name for tool in the_server()._tool_manager.list_tools()}
    assert names == live - {"explore", "run"} and len(names) >= 47
    for compact in (False, True):
        text = sdk.declarations(sdk.names, compact=compact)
        assert text.endswith("};\n") and text.count("declare const vrum: {") == 1
        assert text == sdk.declarations(reversed(sdk.names), compact=compact)
        for name in names:
            assert f"\n  {name}(" in text, name
    again = Sdk(
        ToolSpec(
            tool.name,
            tool.description,
            tool.parameters,
            tool.output_schema,
            tool.fn_metadata.wrap_output,
            bool(tool.annotations and tool.annotations.read_only_hint),
        )
        for tool in reversed(the_server()._tool_manager.list_tools())
        if tool.name not in ("explore", "run")
    )
    assert again.declarations(again.names, compact=True) == sdk.declarations(
        sdk.names, compact=True
    )


def test_a_function_is_written_in_the_work_loop_order_then_by_name() -> None:
    sdk = the_server().sdk
    text = sdk.declarations(
        ["search", "zzz_not_here", "next_work", "export_graph", "add_link"], compact=True
    )
    members = re.findall(r"^  (\w+)\(", text, re.M)
    assert members == ["next_work", "add_link", "search", "export_graph"]


def test_every_tool_name_is_a_plain_identifier_so_a_script_can_write_vrum_dot_name() -> None:
    for tool in the_server()._tool_manager.list_tools():
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tool.name), tool.name


def test_the_standard_set_is_declared_compactly_and_exactly() -> None:
    sdk = the_server().sdk
    compact = sdk.declarations(AGENT_TOOLS, compact=True)
    full = sdk.declarations(AGENT_TOOLS, compact=False)
    assert len(compact) < len(full)
    # Compact keeps the first sentence of a function's description and of an argument's, and says
    # which functions only read; it leaves the fields of a result without comments.
    assert (
        "/** Take the most urgent free story or task, and get everything you need to do it. */"
        in compact
    )
    assert "/** List notes, oldest first, a page at a time. Only reads. */" in compact
    assert "/** epic, story or task. */" in compact
    # An argument's type that has a name (PlanItem) keeps its argument comments; a result does not.
    assert "/**" not in re.search(r"type Node = \{.*?\n\};", compact, re.S).group(0)  # type: ignore[union-attr]
    assert "The item's reference: its kind" in re.search(
        r"type Node = \{.*?\n\};", full, re.S
    ).group(0)  # type: ignore[union-attr]


# -- the forms of JSON Schema -----------------------------------------------------------------


def one(arguments: dict[str, Any], result: dict[str, Any] | None = None, **more: Any) -> str:
    return declared(spec(arguments=arguments, result=result, **more))


def test_nullable_enum_const_array_and_tuple_forms() -> None:
    text = one(
        {
            "type": "object",
            "properties": {
                "size": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "kind": {"type": "string", "enum": ["epic", "story", "task"]},
                "fixed": {"const": "yes"},
                "many": {
                    "type": "array",
                    "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                },
                "pair": {
                    "type": "array",
                    "prefixItems": [{"type": "string"}, {"type": "number"}],
                    "items": False,
                },
                "rest": {
                    "type": "array",
                    "prefixItems": [{"type": "string"}],
                    "items": {"type": "boolean"},
                },
                "when": {"type": "string", "format": "date"},
                "flags": {"type": ["boolean", "null"]},
            },
            "required": ["kind", "many"],
        }
    )
    assert "size?: string | null;" in text
    assert 'kind: "epic" | "story" | "task";' in text
    assert 'fixed?: "yes";' in text
    assert "many: (string | number)[];" in text
    assert "pair?: [string, number];" in text
    assert "rest?: [string, ...boolean[]];" in text
    assert "when?: string;" in text
    assert "flags?: boolean | null;" in text
    # Nothing in the arguments is required here but two fields, so the argument is not optional.
    assert "do_it(args: {" in text


def test_no_arguments_and_all_optional_arguments() -> None:
    assert "  do_it(): Promise<unknown>;" in declared(spec())
    optional = one({"type": "object", "properties": {"limit": {"type": "integer", "default": 50}}})
    assert "do_it(args?: {" in optional and "limit?: number;" in optional


def test_objects_and_their_other_properties() -> None:
    text = one(
        {
            "type": "object",
            "properties": {
                "anything": {"type": "object", "additionalProperties": True},
                "counts": {"type": "object", "additionalProperties": {"type": "integer"}},
                "loose": {
                    "type": "object",
                    "properties": {"a": {"type": "string"}},
                    "additionalProperties": True,
                },
                "shut": {
                    "type": "object",
                    "properties": {"a": {"type": "string"}},
                    "additionalProperties": False,
                },
                "bare": {"type": "object"},
                "nothing": {"type": "object", "additionalProperties": False},
            },
        }
    )
    assert "anything?: {\n      [key: string]: unknown;\n    };" in text
    assert "counts?: {\n      [key: string]: number;\n    };" in text
    assert "loose?: {\n      a?: string;\n      [key: string]: unknown;\n    };" in text
    assert "shut?: {\n      a?: string;\n    };" in text
    assert "bare?: {\n      [key: string]: unknown;\n    };" in text
    assert "nothing?: {\n      [key: string]: never;\n    };" in text


def test_all_of_and_a_schema_that_is_not_understood() -> None:
    text = one(
        {
            "type": "object",
            "properties": {
                "both": {
                    "allOf": [
                        {"type": "string"},
                        {"type": "object", "properties": {"a": {"type": "string"}}},
                    ]
                },
                "single": {"allOf": [{"type": "string"}]},
                "any": {},
                "odd": {"not": {"type": "string"}},
                "outside": {"$ref": "https://example.com/schema.json"},
                "empty_enum": {"enum": []},
            },
        }
    )
    assert "both?: string & {" in text
    assert "single?: string;" in text
    assert "any?: unknown;" in text and "odd?: unknown;" in text and "outside?: unknown;" in text
    assert "empty_enum?: never;" in text


def test_a_schema_nested_past_the_limit_ends_in_unknown() -> None:
    schema: dict[str, Any] = {"type": "string"}
    for _ in range(40):
        schema = {"type": "array", "items": schema}
    text = one({"type": "object", "properties": {"deep": schema}})
    assert "unknown" in text and "string" not in text.split("deep")[1]


# -- results: exact, unwrapped, required ------------------------------------------------------


def test_a_result_field_with_a_default_is_required_but_an_argument_with_one_is_optional() -> None:
    both = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string", "default": "x"}},
        "required": ["a"],
    }
    text = one(both, {**both, "title": "Thing"})
    assert "args: {\n    a: string;\n    b?: string;\n  }" in text
    assert "type Thing = {\n  a: string;\n  b: string;\n};" in text


def test_a_result_the_library_wraps_is_typed_as_the_bare_value() -> None:
    wrapped = {
        "type": "object",
        "properties": {"result": {"type": "array", "items": {"type": "string"}}},
        "required": ["result"],
        "title": "do_itOutput",
    }
    assert "do_it(): Promise<string[]>;" in declared(spec(result=wrapped, wrapped=True))
    number = {
        "type": "object",
        "properties": {"result": {"type": "integer"}},
        "required": ["result"],
    }
    assert "do_it(): Promise<number>;" in declared(spec(result=number, wrapped=True))
    # A tool with no output schema gives something unknown.
    assert "do_it(): Promise<unknown>;" in declared(spec(result=None))


def test_a_result_that_is_a_dictionary_is_written_in_place() -> None:
    text = declared(
        spec(result={"type": "object", "additionalProperties": {"type": "integer"}, "title": "D"})
    )
    assert "Promise<{\n    [key: string]: number;\n  }>;" in text


# -- references and names ---------------------------------------------------------------------


def tree_schema() -> dict[str, Any]:
    return {
        "$defs": {
            "Tree": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "children": {"type": "array", "items": {"$ref": "#/$defs/Tree"}},
                    "parent": {"anyOf": [{"$ref": "#/$defs/Tree"}, {"type": "null"}]},
                },
                "required": ["name"],
            },
            "Ping": {"type": "object", "properties": {"pong": {"$ref": "#/$defs/Pong"}}},
            "Pong": {
                "type": "object",
                "properties": {"ping": {"$ref": "#/$defs/Ping"}, "n": {"type": "integer"}},
            },
        },
        "type": "object",
        "properties": {
            "tree": {"$ref": "#/$defs/Tree"},
            "ping": {"$ref": "#/$defs/Ping"},
        },
        "title": "Holder",
    }


def test_a_type_that_refers_back_to_itself_is_unknown_where_it_does() -> None:
    text = declared(spec(result=tree_schema()))
    assert (
        "type Tree = {\n  name: string;\n  children?: unknown[];\n  parent?: unknown;\n};" in text
    )
    # Two types that refer to each other are cut where the second points back at the first.
    assert "type Ping = {\n  pong?: Pong;\n};" in text
    assert "type Pong = {\n  ping?: unknown;\n  n?: number;\n};" in text
    assert "type Holder = {\n  tree?: Tree;\n  ping?: Ping;\n};" in text


def test_a_type_used_by_several_tools_has_one_name_and_different_types_never_share_one() -> None:
    thing = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    other = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}

    def result(definition: dict[str, Any]) -> dict[str, Any]:
        return {
            "$defs": {"Thing": definition},
            "type": "object",
            "properties": {"thing": {"$ref": "#/$defs/Thing"}},
            "required": ["thing"],
            "title": "Wrapper",
        }

    text = declared(
        spec("first", result=result(thing)),
        spec("second", result=result(thing)),
        spec("third", result=result(other)),
    )
    assert text.count("type Thing = ") == 1 and text.count("type Thing2 = ") == 1
    assert text.count("type Wrapper = ") == 1 and text.count("type Wrapper2 = ") == 1
    assert "first(): Promise<Wrapper>;" in text and "second(): Promise<Wrapper>;" in text
    assert "third(): Promise<Wrapper2>;" in text
    # A type has the same name whichever functions are asked for.
    sdk = Sdk(
        [
            spec("first", result=result(thing)),
            spec("second", result=result(thing)),
            spec("third", result=result(other)),
        ]
    )
    assert "third(): Promise<Wrapper2>;" in sdk.declarations(["third"], compact=False)
    assert "type Thing2 = " in sdk.declarations(["third"], compact=False)
    assert "type Thing = " not in sdk.declarations(["third"], compact=False)


def test_type_names_are_made_safe_and_function_names_are_quoted_when_needed() -> None:
    assert type_name("PlanItem") == "PlanItem"
    assert type_name("plan item") == "PlanItem"
    assert type_name("3d view") == "T3dView"
    assert type_name("") == "Value" and type_name("***") == "Value"
    assert type_name("Date") == "DateType" and type_name("Promise") == "PromiseType"
    assert type_name("VrumError") == "VrumErrorType"
    assert function_name("list_nodes") == "list_nodes"
    assert function_name("delete") == "delete"
    assert function_name("my-tool") == '"my-tool"' and function_name("a.b") == '"a.b"'
    text = declared(
        spec(
            "my-tool",
            result={
                "type": "object",
                "properties": {"my-field": {"type": "string"}},
                "title": "Date",
            },
        )
    )
    assert '  "my-tool"(): Promise<DateType>;' in text
    assert 'type DateType = {\n  "my-field": string;\n};' in text or '"my-field"?: string;' in text


def test_a_description_cannot_end_its_comment_early_or_break_the_text() -> None:
    nasty = "Does it. */ declare const hacked: 1; /* and more\n\nSecond paragraph with */ again."
    text = declared(
        spec(
            description=nasty,
            arguments={
                "type": "object",
                "properties": {"x": {"type": "string", "description": "A field */ evil"}},
            },
        ),
        compact=False,
    )
    compact = declared(spec(description=nasty), compact=True)
    for body in (text, compact):
        assert "*/ declare" not in body and "*/ evil" not in body
        # Without its comments the text is one declaration, so nothing got out of a comment.
        assert re.sub(r"/\*.*?\*/", "", body, flags=re.S).count("declare const") == 1
        assert "hacked" not in re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    assert "*\\/" in text
    assert "Second paragraph with *\\/ again." in text


def test_the_first_sentence_is_found_and_cut_to_a_length() -> None:
    assert (
        first_sentence("Create an epic, a story or a task.\n\nMore.")
        == "Create an epic, a story or a task."
    )
    assert first_sentence("Use e.g. story:12 for it. Then more.") == "Use e.g. story:12 for it."
    assert first_sentence("No full stop here") == "No full stop here"
    assert first_sentence("   ") == ""
    long = "word " * 60
    cut = first_sentence(long.strip(), 40)
    assert cut.endswith("...") and len(cut) <= 40
    # A docstring's indentation and line breaks are gone.
    assert first_sentence("Read one thing\n        in full. And more.") == "Read one thing in full."


def test_the_index_and_the_search_of_functions() -> None:
    sdk = Sdk(
        [
            spec("add_note", "Record a note: what is known.", reads_only=False),
            spec("list_notes", "List notes, a page at a time.", reads_only=True),
            spec("claim_node", "Say you are working on a story.", reads_only=False),
        ]
    )
    assert sdk.index(["list_notes", "ghost", "add_note"]) == [
        IndexEntry("add_note", "Record a note: what is known.", False),
        IndexEntry("list_notes", "List notes, a page at a time.", True),
    ]
    assert sdk.reads_only("list_notes") and not sdk.reads_only("add_note")
    assert not sdk.reads_only("ghost") and not sdk.has("ghost") and sdk.has("claim_node")
    assert sdk.find(sdk.names, "note") == ["add_note", "list_notes"]
    assert sdk.find(sdk.names, "working story") == ["claim_node"]
    assert sdk.find(sdk.names, "zebra") == [] and sdk.find(sdk.names, "") == []
    assert sdk.find(["claim_node"], "note") == []


# -- the text matches what each tool really gives back ----------------------------------------
# A small reader of the TypeScript this module writes (objects, unions, arrays, tuples, literals
# and the names of other types), and a check that a value fits a type, so that every result type
# can be held against a result a tool really gives.

_TOKENS = re.compile(
    r'\s*(?:(/\*.*?\*/)|("(?:[^"\\]|\\.)*")|(-?\d+(?:\.\d+)?)|([A-Za-z_$][A-Za-z0-9_$]*)|(\.\.\.|[{}()\[\]:;?|&,<>=]))',
    re.S,
)


def tokens_of(text: str) -> list[str]:
    found = []
    position = 0
    while position < len(text.rstrip()):
        matched = _TOKENS.match(text, position)
        assert matched, text[position : position + 40]
        position = matched.end()
        if matched.group(1) is None:
            found.append(next(group for group in matched.groups()[1:] if group is not None))
    return found


class Reader:
    def __init__(self, text: str) -> None:
        self.tokens = tokens_of(text)
        self.at = 0

    def peek(self) -> str | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self, expected: str | None = None) -> str:
        token = self.tokens[self.at]
        assert expected is None or token == expected, (
            expected,
            token,
            self.tokens[self.at - 3 : self.at + 3],
        )
        self.at += 1
        return token

    def type(self) -> tuple[Any, ...]:
        parts = [self.intersection()]
        while self.peek() == "|":
            self.take("|")
            parts.append(self.intersection())
        return parts[0] if len(parts) == 1 else ("union", parts)

    def intersection(self) -> tuple[Any, ...]:
        parts = [self.postfix()]
        while self.peek() == "&":
            self.take("&")
            parts.append(self.postfix())
        return parts[0] if len(parts) == 1 else ("all", parts)

    def postfix(self) -> tuple[Any, ...]:
        node = self.atom()
        while self.peek() == "[" and self.tokens[self.at + 1] == "]":
            self.take("[")
            self.take("]")
            node = ("array", node)
        return node

    def atom(self) -> tuple[Any, ...]:
        token = self.take()
        if token == "(":
            inner = self.type()
            self.take(")")
            return inner
        if token == "{":
            return self.members()
        if token == "[":
            items: list[Any] = []
            while self.peek() != "]":
                if self.peek() == "...":
                    self.take("...")
                    items.append(("rest", self.postfix()))
                else:
                    items.append(self.type())
                if self.peek() == ",":
                    self.take(",")
            self.take("]")
            return ("tuple", items)
        if token.startswith('"'):
            return ("literal", json.loads(token))
        if re.fullmatch(r"-?\d+(\.\d+)?", token):
            return ("literal", json.loads(token))
        return ("name", token)

    def members(self) -> tuple[Any, ...]:
        fields: dict[str, tuple[bool, Any]] = {}
        index: Any = None
        while self.peek() != "}":
            if self.peek() == "[":
                self.take("[")
                self.take("key")
                self.take(":")
                self.take("string")
                self.take("]")
                self.take(":")
                index = self.type()
            else:
                name = self.take()
                name = json.loads(name) if name.startswith('"') else name
                optional = self.peek() == "?"
                if optional:
                    self.take("?")
                self.take(":")
                fields[name] = (optional, self.type())
            self.take(";")
        self.take("}")
        return ("object", fields, index)


def parsed(declarations: str) -> tuple[dict[str, Any], dict[str, tuple[Any, Any]]]:
    """The type aliases of a declarations text by name, and for each function its argument type
    (None when it takes none, and whether the argument is optional) and result type."""
    reader = Reader(declarations)
    aliases: dict[str, Any] = {}
    functions: dict[str, tuple[Any, Any]] = {}
    while reader.peek() == "type":
        reader.take("type")
        name = reader.take()
        reader.take("=")
        aliases[name] = reader.type()
        reader.take(";")
    reader.take("declare")
    reader.take("const")
    reader.take("vrum")
    reader.take(":")
    reader.take("{")
    while reader.peek() != "}":
        name = reader.take()
        name = json.loads(name) if name.startswith('"') else name
        reader.take("(")
        argument: Any = None
        if reader.peek() != ")":
            reader.take("args")
            optional = reader.peek() == "?"
            if optional:
                reader.take("?")
            reader.take(":")
            argument = (optional, reader.type())
        reader.take(")")
        reader.take(":")
        reader.take("Promise")
        reader.take("<")
        result = reader.type()
        reader.take(">")
        reader.take(";")
        functions[name] = (argument, result)
    reader.take("}")
    return aliases, functions


def fits(value: Any, node: tuple[Any, ...], aliases: dict[str, Any]) -> bool:
    kind = node[0]
    if kind == "union":
        return any(fits(value, one, aliases) for one in node[1])
    if kind == "all":
        return all(fits(value, one, aliases) for one in node[1])
    if kind == "array":
        return isinstance(value, list) and all(fits(item, node[1], aliases) for item in value)
    if kind == "tuple":
        items = node[1]
        if not isinstance(value, list):
            return False
        rest = items[-1][1] if items and items[-1][0] == "rest" else None
        fixed = items[:-1] if rest is not None else items
        if len(value) < len(fixed) or (rest is None and len(value) != len(fixed)):
            return False
        return all(fits(v, t, aliases) for v, t in zip(value, fixed, strict=False)) and (
            rest is None or all(fits(v, rest, aliases) for v in value[len(fixed) :])
        )
    if kind == "literal":
        return bool(value == node[1] and type(value) is type(node[1]))
    if kind == "object":
        _, fields, index = node
        if not isinstance(value, dict):
            return False
        for name, (optional, field_type) in fields.items():
            if name not in value:
                if not optional:
                    return False
            elif not fits(value[name], field_type, aliases):
                return False
        extra = [name for name in value if name not in fields]
        if extra and index is None:
            return False
        return all(fits(value[name], index, aliases) for name in extra)
    name = node[1]
    if name == "string":
        return isinstance(value, str)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "unknown":
        return True
    if name == "never":
        return False
    return fits(value, aliases[name], aliases)


def example_of(schema: Any, defs: dict[str, Any], depth: int = 0) -> Any:
    """The smallest value a schema accepts: its required fields only, and one element in a list
    of objects (a model's own defaults fill in the rest)."""
    if "$ref" in schema:
        return example_of(defs[schema["$ref"].rsplit("/", 1)[1]], defs, depth)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [one for one in schema[key] if one.get("type") != "null"]
            return example_of(options[0], defs, depth) if options else None
    kind = schema.get("type")
    if kind == "string":
        return {"date": "2026-01-01", "date-time": "2026-01-01T00:00:00Z"}.get(
            schema.get("format"), "x"
        )
    if kind == "integer":
        return max(1, int(schema.get("minimum", 1)))
    if kind == "number":
        return 1.5
    if kind == "boolean":
        return True
    if kind == "array":
        items = schema.get("items", {})
        if depth < 2 and items.get("$ref"):
            return [example_of(items, defs, depth + 1)]
        return []
    if kind == "object":
        properties = schema.get("properties", {})
        return {
            name: example_of(properties[name], defs, depth + 1)
            for name in schema.get("required", [])
        }
    return None


def real_result(tool: Any) -> Any:
    """What the tool would hand a script: a smallest valid result, made by the tool's own output
    model, dumped the way the library dumps it, with the wrapper taken off."""
    schema = tool.output_schema
    defs = schema.get("$defs", {})
    example = example_of(schema, defs)
    adapter = TypeAdapter(tool.fn_metadata.output_model)
    dumped = adapter.dump_python(
        adapter.validate_python(example, by_alias=True), mode="json", by_alias=True
    )
    return dumped["result"] if tool.fn_metadata.wrap_output else dumped


def every_tool() -> Iterator[Any]:
    for tool in the_server()._tool_manager.list_tools():
        if tool.name not in ("explore", "run"):
            yield tool


def test_every_result_type_holds_a_result_the_tool_really_gives() -> None:
    sdk = the_server().sdk
    aliases, functions = parsed(sdk.declarations(sdk.names, compact=False))
    checked = 0
    for tool in every_tool():
        assert tool.output_schema is not None, tool.name
        result = real_result(tool)
        assert fits(result, functions[tool.name][1], aliases), tool.name
        checked += 1
    assert checked == len(sdk.names)


def test_a_result_type_is_not_loose_a_field_missing_or_of_the_wrong_kind_does_not_fit() -> None:
    sdk = the_server().sdk
    aliases, functions = parsed(sdk.declarations(["get_node"], compact=False))
    tool = the_server()._tool_manager.get_tool("get_node")
    assert tool is not None and tool.output_schema is not None
    good = real_result(tool)
    assert fits(good, functions["get_node"][1], aliases)
    # The library leaves fields with defaults out of `required`, and a result always has them.
    defaulted = "body_preview"
    assert defaulted in good and defaulted not in tool.output_schema["required"]
    broken = {key: value for key, value in good.items() if key != defaulted}
    assert not fits(broken, functions["get_node"][1], aliases)
    assert not fits({**good, "version": "one"}, functions["get_node"][1], aliases)
    assert not fits({**good, "surprise": 1}, functions["get_node"][1], aliases)


def test_every_argument_type_takes_the_smallest_valid_arguments_and_refuses_a_made_up_one() -> None:
    sdk = the_server().sdk
    aliases, functions = parsed(sdk.declarations(sdk.names, compact=True))
    for tool in every_tool():
        schema = tool.parameters
        arguments = example_of(schema, schema.get("$defs", {}))
        argument = functions[tool.name][0]
        if argument is None:
            assert not schema.get("properties"), tool.name
            continue
        assert fits(arguments, argument[1], aliases), tool.name
        assert argument[0] == (not schema.get("required")), tool.name
        assert not fits({**arguments, "no_such_argument": 1}, argument[1], aliases), tool.name
    # An argument that is required is not left out.
    required = [tool for tool in every_tool() if tool.parameters.get("required")]
    assert required and all(not functions[tool.name][0][0] for tool in required)


def names_in(node: Any) -> Iterator[str]:
    """Every type name a parsed type uses."""
    if isinstance(node, tuple):
        if node[0] == "name":
            yield node[1]
        for part in node[1:]:
            yield from names_in(part)
    elif isinstance(node, list):
        for part in node:
            yield from names_in(part)
    elif isinstance(node, dict):
        for _optional, member in node.values():
            yield from names_in(member)


def test_a_function_asked_for_alone_brings_every_type_it_needs_and_none_it_does_not() -> None:
    sdk = the_server().sdk
    primitives = {"string", "number", "boolean", "null", "unknown", "never"}
    for name in sdk.names:
        aliases, functions = parsed(sdk.declarations([name], compact=True))
        assert list(functions) == [name]
        used = set(names_in(list(functions[name]))) - primitives
        reached = set(used)
        pending = list(used)
        while pending:
            for more in set(names_in(aliases[pending.pop()])) - primitives - reached:
                reached.add(more)
                pending.append(more)
        assert set(aliases) == reached, name
