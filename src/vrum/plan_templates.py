"""Plan templates (M47): a named, parameterised plan stored as data.

A template is a plan with holes. It says which variables it takes (each with an optional
description, a default, a list of choices or a simple pattern, and whether it is required) and then
lists the epics, stories and tasks, links and acceptance criteria of the plan, with `{{name}}`
wherever a variable's value goes (in a title, a body, a parent or the end of a link). Making a plan
from it (`create_plan` with `template` and `values`) gives exactly the plan that writing the
expanded items out by hand would: the same checks, the same refusals, in the same transaction.

A template is data, never code: there is nothing to run, only text to fill in. A value is one line
of text that holds no secret; a `pattern` is one character class with one repeat (such as
`^[a-z0-9.-]+$`), so a pattern a person saves can never make the server work hard on a value. A
template is saved as an event (`plan_template_saved`) and deleted as another, so every version of
it stays in the history and it travels through an export. This module is pure: reading the shape,
checking the variables and filling in the values. Storing is workflow.py's."""

import re
import string
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vrum.common import Refusal, parse_node_ref, refuse_secrets

NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,59}")
VARIABLE = re.compile(r"[a-z][a-z0-9_]{0,29}")
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*\}\}")
# One character class (or \d or \w), one repeat, optional anchors: nothing that can backtrack badly.
_SAFE_PATTERN = re.compile(r"\^?(?:\[[^\]\\]{1,40}\]|\\d|\\w)(?:[+*]|\{\d{1,3}(?:,\d{0,3})?\})?\$?")
MOST_VARIABLES = 30
MOST_ITEMS = 200
MOST_TEMPLATE_BYTES = 100_000
MOST_VALUE_CHARS = 500
MOST_DESCRIPTION_CHARS = 500


def _usable_pattern(pattern: str) -> bool:
    """A pattern of the one safe shape that the regular expression library also accepts (a class
    such as `[b-a]` has the shape and is not one)."""
    if not _SAFE_PATTERN.fullmatch(pattern):
        return False
    try:
        re.compile(pattern.strip("^$"))
    except re.error:
        return False
    return True


class TemplateProblems(Exception):
    """A template or its values do not fit. `problems` lists every reason, one sentence each."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


class TemplateVariable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, description="What the variable is for.")
    type: Literal["text", "ref"] = Field(
        default="text",
        description="text: one line of words. ref: the reference of an item that exists, such as "
        "epic:3.",
    )
    kind: Literal["epic", "story"] | None = Field(
        default=None, description="For a ref: the kind of item it must be."
    )
    required: bool = Field(default=True, description="False: the plan can be made without it.")
    default: str | None = Field(default=None, description="Used when no value is given.")
    choices: list[str] | None = Field(default=None, description="The only values it may have.")
    pattern: str | None = Field(
        default=None, description="One character class and one repeat, such as ^[a-z0-9.-]+$."
    )
    max_length: int = Field(default=200, ge=1, le=MOST_VALUE_CHARS)


class TemplateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    kind: str
    title: str
    body: str = ""
    priority: str = "normal"
    size: str | None = None
    parent: str | None = None


class TemplateLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    link_type: str


class TemplateRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    body: str = ""
    status: str = "open"
    node: str | None = None


class Template(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", max_length=MOST_DESCRIPTION_CHARS)
    variables: dict[str, TemplateVariable] = Field(default_factory=dict)
    items: list[TemplateItem]
    links: list[TemplateLink] = Field(default_factory=list)
    rules: list[TemplateRule] = Field(default_factory=list)


def clean_name(name: str) -> str:
    cleaned = name.strip()
    if not NAME.fullmatch(cleaned):
        raise Refusal(
            "A template's name is lower case letters, digits, - and _, starting with a letter or "
            "digit, at most 60 characters, such as release-checklist. Nothing was changed."
        )
    return cleaned


def _where(problem: Mapping[str, Any]) -> str:
    place = ".".join(str(part) for part in problem["loc"])
    return f"{place}: {problem['msg']}"


def _used_names(template: Template) -> list[tuple[str, str]]:
    """Every placeholder in the template, with where it is."""
    found: list[tuple[str, str]] = []

    def scan(where: str, text: str | None) -> None:
        for name in PLACEHOLDER.findall(text or ""):
            found.append((where, name))

    for index, item in enumerate(template.items, start=1):
        for field in ("title", "body", "parent"):
            scan(f"Item {index} ({item.key}) {field}", getattr(item, field))
    for index, link in enumerate(template.links, start=1):
        scan(f"Link {index} source", link.source)
        scan(f"Link {index} target", link.target)
    for index, rule in enumerate(template.rules, start=1):
        for field in ("title", "body", "node"):
            scan(f"Acceptance criterion {index} {field}", getattr(rule, field))
    return found


def parse_template(raw: object) -> Template:
    """The template as a model, or every problem with its shape and its variables at once. The
    expanded plan is checked by the caller (it owns the plan checks); this checks the template."""
    if not isinstance(raw, dict):
        raise TemplateProblems(["The template must be an object with its variables and items."])
    problems: list[str] = []
    try:
        template = Template.model_validate(raw)
    except ValidationError as error:
        raise TemplateProblems([_where(p) for p in error.errors()]) from None
    if len(template.items) > MOST_ITEMS:
        problems.append(
            f"A template may have at most {MOST_ITEMS} items, but it has {len(template.items)}."
        )
    if len(template.variables) > MOST_VARIABLES:
        problems.append(f"A template may have at most {MOST_VARIABLES} variables.")
    for name, variable in template.variables.items():
        if not VARIABLE.fullmatch(name):
            problems.append(
                f"Variable {name!r}: a name is lower case letters, digits and _, starting with a "
                "letter, at most 30 characters."
            )
        if variable.pattern is not None and not _usable_pattern(variable.pattern):
            problems.append(
                f"Variable {name!r}: a pattern is one character class and one repeat, such as "
                "^[a-z0-9.-]+$, and nothing else."
            )
        if variable.choices is not None:
            if len(variable.choices) < 1 or any(not c.strip() for c in variable.choices):
                problems.append(f"Variable {name!r}: choices must be words, and at least one.")
            if variable.default is not None and variable.default not in variable.choices:
                problems.append(f"Variable {name!r}: the default is not one of its choices.")
        if variable.type == "ref" and (variable.choices or variable.pattern):
            problems.append(f"Variable {name!r}: a reference has no choices or pattern.")
        if variable.kind is not None and variable.type != "ref":
            problems.append(f"Variable {name!r}: kind is only for a variable of type ref.")
        if variable.default is not None:
            problems.extend(_value_problems(name, variable, variable.default, "default"))
    declared = set(template.variables)
    for where, used in _used_names(template):
        if used not in declared:
            problems.append(
                f"{where} uses {{{{{used}}}}}, which is not a variable of the template."
            )
    if problems:
        raise TemplateProblems(problems)
    return template


def _value_problems(name: str, variable: TemplateVariable, value: str, what: str) -> list[str]:
    text = value.strip()
    if "\n" in value or "\r" in value:
        return [f"Variable {name!r}: the {what} must be one line."]
    if not text:
        return [f"Variable {name!r}: the {what} cannot be blank."]
    if len(text) > variable.max_length:
        return [f"Variable {name!r}: the {what} is longer than {variable.max_length} characters."]
    if variable.type == "ref":
        try:
            ref = parse_node_ref(text, name)
        except Refusal as refusal:
            return [f"Variable {name!r}: {refusal}"]
        if variable.kind is not None and not ref.startswith(f"{variable.kind}:"):
            return [f"Variable {name!r}: the {what} must be a {variable.kind}, but it is {ref}."]
        return []
    if variable.choices is not None and text not in variable.choices:
        return [f"Variable {name!r}: the {what} must be one of {', '.join(variable.choices)}."]
    if variable.pattern is not None and not re.fullmatch(variable.pattern.strip("^$"), text):
        return [f"Variable {name!r}: the {what} does not fit its pattern {variable.pattern}."]
    return []


def _stand_in(variable: TemplateVariable) -> str:
    """A value that fits the variable's pattern, to expand a template once at save time: the first
    short run of one character that the pattern takes, else a word."""
    if variable.pattern is not None:
        pattern = re.compile(variable.pattern.strip("^$"))
        for char in "a1A_-.0" + string.printable[:94]:
            for length in (1, 2, 3, 4, 5, 8, 16, 40, 100):
                if length <= variable.max_length and pattern.fullmatch(char * length):
                    return char * length
    return "sample"


def sample_values(template: Template) -> dict[str, str]:
    """Values good enough to expand the template once, to check the plan it makes: a default, the
    first choice, or a stand-in value that fits the pattern, and for a reference a made-up one (see
    `sample_refs`)."""
    values: dict[str, str] = {}
    for name, variable in template.variables.items():
        if variable.type == "ref":
            values[name] = f"{variable.kind or 'epic'}:1"
        elif variable.default is not None:
            values[name] = variable.default
        elif variable.choices:
            values[name] = variable.choices[0]
        else:
            values[name] = _stand_in(variable)
    return values


def sample_refs(template: Template) -> set[str]:
    """The made-up references sample_values uses, which a save-time check treats as existing."""
    return {f"{v.kind or 'epic'}:1" for v in template.variables.values() if v.type == "ref"}


def expand(
    template: Template, values: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """The plan the template makes for these values: its items, links and acceptance criteria as
    the dictionaries `create_plan` takes. Every problem with the values is raised at once."""
    problems: list[str] = []
    given: dict[str, str] = {}
    for name, value in values.items():
        if name not in template.variables:
            problems.append(f"{name!r} is not a variable of this template.")
        elif not isinstance(value, str):
            problems.append(f"Variable {name!r}: give the value as text.")
        else:
            given[name] = value.strip()
    final: dict[str, str] = {}
    for name, variable in template.variables.items():
        if name in given:
            value = given[name]
        elif variable.default is not None:
            value = variable.default.strip()
        elif variable.required:
            problems.append(f"Variable {name!r} is required: give a value for it.")
            continue
        else:
            value = ""
        if value or name in given:
            found = _value_problems(name, variable, value, "value")
            if found:
                problems.extend(found)
                continue
        final[name] = value
    try:
        refuse_secrets(final)
    except Refusal as refusal:
        problems.append(str(refusal))
    if problems:
        raise TemplateProblems(problems)

    def fill(text: str | None) -> str | None:
        if text is None:
            return None
        return PLACEHOLDER.sub(lambda found: final.get(found.group(1), ""), text)

    items: list[dict[str, Any]] = []
    for item in template.items:
        made = item.model_dump(exclude_none=True)
        for field in ("title", "body", "parent"):
            if field in made:
                made[field] = fill(made[field]) or ""
        if made.get("parent") == "":
            del made["parent"]
        items.append(made)
    links = [
        {**link.model_dump(), "source": fill(link.source), "target": fill(link.target)}
        for link in template.links
    ]
    rules: list[dict[str, Any]] = []
    for rule in template.rules:
        made = rule.model_dump(exclude_none=True)
        for field in ("title", "body", "node"):
            if field in made:
                made[field] = fill(made[field]) or ""
        if made.get("node") == "":
            del made["node"]
        rules.append(made)
    return items, links, rules


class PlanTemplateInfo(BaseModel):
    name: str
    description: str
    variables: dict[str, TemplateVariable] = Field(description="What it takes, by name.")
    counts: dict[str, int] = Field(
        description="How many epics, stories, tasks, links and criteria."
    )
    version: int = Field(description="How many times it has been saved: the number of this one.")
    saved_by: str
    saved_by_kind: str
    saved_at: Any
    template: dict[str, Any] | None = Field(
        description="The whole template, when one was asked for by name or with detail."
    )


class PlanTemplatePage(BaseModel):
    items: list[PlanTemplateInfo]
    total: int = Field(description="How many templates the project has.")
