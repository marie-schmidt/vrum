"""What a reference is, without any containers: the pattern the server reads, and that the board's
own copy of it (src/vrum/board/static/references.js) is the same text, so that what the board shows
as a link is exactly what find_mentions reports."""

import re
from pathlib import Path

import pytest

from vrum.references import REFERENCE_KINDS, REFERENCE_PATTERN

SCRIPT = Path(__file__).resolve().parent.parent / "src/vrum/board/static/references.js"


@pytest.mark.parametrize(
    "text,kind,ident",
    [
        ("[[epic:3]]", "epic", "3"),
        ("[[story:12]]", "story", "12"),
        ("see [[task:40]], then", "task", "40"),
        ("see [[rule:3]], then", "rule", "3"),
        ("a [[note:7]] b", "note", "7"),
        ("[[story:123456789012345678]]", "story", "123456789012345678"),
    ],
)
def test_the_five_kinds_are_references(text: str, kind: str, ident: str) -> None:
    found = re.search(REFERENCE_PATTERN, text)
    assert found and found.groups() == (kind, ident)
    assert kind in REFERENCE_KINDS


def test_the_kinds_are_the_five_things_a_reference_can_name() -> None:
    assert REFERENCE_KINDS == ("epic", "story", "task", "rule", "note")


@pytest.mark.parametrize(
    "text",
    [
        "[[node:12]]",
        "[[decision:7]]",
        "[[fact:5]]",
        "[[note:0]]",
        "[[story:0]]",
        "[[story:05]]",
        "[[story: 5]]",
        "[[ story:5]]",
        "[[story:5 ]]",
        "[[stories:5]]",
        "[[Story:5]]",
        "[[sprint:5]]",
        "[[principle:5]]",
        "[[belief:5]]",
        "[[memory:5]]",
        "[story:5]",
        "[[story:5]",
        "[[story:]]",
        "[[story:1234567890123456789]]",
    ],
)
def test_anything_else_is_only_text(text: str) -> None:
    assert re.search(REFERENCE_PATTERN, text) is None


def test_the_board_reads_the_same_pattern() -> None:
    found = re.search(r"export const REFERENCE_PATTERN = String\.raw`([^`]*)`", SCRIPT.read_text())
    assert found, "references.js does not say its pattern as REFERENCE_PATTERN"
    assert found.group(1) == REFERENCE_PATTERN
