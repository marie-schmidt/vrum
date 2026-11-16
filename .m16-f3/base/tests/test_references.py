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
        ("[[node:12]]", "node", "12"),
        ("see [[rule:3]], then", "rule", "3"),
        ("a [[decision:7]] b", "decision", "7"),
        ("[[fact:5]]", "fact", "5"),
        ("[[belief:2]]", "belief", "2"),
        ("[[node:123456789012345678]]", "node", "123456789012345678"),
    ],
)
def test_the_five_kinds_are_references(text: str, kind: str, ident: str) -> None:
    found = re.search(REFERENCE_PATTERN, text)
    assert found and found.groups() == (kind, ident)
    assert kind in REFERENCE_KINDS


@pytest.mark.parametrize(
    "text",
    [
        "[[node:0]]",
        "[[node:05]]",
        "[[node: 5]]",
        "[[ node:5]]",
        "[[node:5 ]]",
        "[[nodes:5]]",
        "[[Node:5]]",
        "[[principle:5]]",
        "[node:5]",
        "[[node:5]",
        "[[node:]]",
        "[[node:1234567890123456789]]",
    ],
)
def test_anything_else_is_only_text(text: str) -> None:
    assert re.search(REFERENCE_PATTERN, text) is None


def test_the_board_reads_the_same_pattern() -> None:
    found = re.search(r"export const REFERENCE_PATTERN = String\.raw`([^`]*)`", SCRIPT.read_text())
    assert found, "references.js does not say its pattern as REFERENCE_PATTERN"
    assert found.group(1) == REFERENCE_PATTERN
