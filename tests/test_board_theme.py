"""The appearance (Light, Dark, System) without any containers: the design tokens (both themes
complete, text and backgrounds at the contrast WCAG AA asks for), that no style sheet or script
names a colour of its own, and that the theme is decided by an external script in the head, ahead
of every style sheet, so that nothing is drawn in the wrong colours first."""

import re

import pytest

from tests.board_theme import (
    THEMES,
    colour_tokens,
    contrast,
    luminance,
    parse_colour,
    token,
    tokens,
)
from vrum.board import STATIC

STYLES = sorted(path for path in STATIC.glob("*.css") if path.name != "tokens.css")
SCRIPTS = sorted(STATIC.glob("*.js"))

TEXT = ["--text", "--text-2", "--text-3"]
SURFACES = ["--bg", "--surface", "--surface-2", "--surface-3", "--sidebar"]
TINTS = [
    "--accent-soft",
    "--ok-soft",
    "--warn-soft",
    "--info-soft",
    "--danger-soft",
    "--progress-soft",
    "--neutral-soft",
]
SIGNALS = ["--accent-text", "--ok", "--warn", "--info", "--progress", "--danger"]


def test_the_colour_parser_reads_what_the_tokens_write_and_what_a_browser_reports() -> None:
    assert parse_colour("#c8321a") == (200, 50, 26, 1.0)
    assert parse_colour("#fff") == (255, 255, 255, 1.0)
    assert parse_colour("rgb(28 27 25 / 40%)") == (28, 27, 25, 0.4)
    assert parse_colour("rgba(28, 27, 25, 0.08)") == (28, 27, 25, 0.08)
    assert parse_colour("color(srgb 1 0 0)") == (255, 0, 0, 1.0)
    assert parse_colour("0 1px 2px rgb(0 0 0 / 40%)") is None
    assert parse_colour("1rem") is None


@pytest.mark.parametrize("theme", THEMES)
def test_a_theme_says_which_colour_scheme_native_controls_and_scrollbars_should_use(
    theme: str,
) -> None:
    assert tokens(theme)["color-scheme"] == theme


def test_light_and_dark_define_the_same_colour_tokens_and_differ_in_every_surface_and_text() -> (
    None
):
    light, dark = colour_tokens("light"), colour_tokens("dark")
    assert set(light) == set(dark)
    for name in (
        *SURFACES,
        *TEXT,
        *TINTS,
        *SIGNALS,
        "--border",
        "--border-strong",
        "--accent",
        "--focus",
    ):
        assert light[name] != dark[name], name
    # Dark is not light turned upside down: its page is darker than any text, its text softer than
    # white, and its accent brighter than the light theme's, as the design asks.
    assert luminance(token("dark", "--bg")) < 0.05 < 0.8 < luminance(token("light", "--bg"))
    assert luminance(token("dark", "--text")) < luminance(token("light", "--surface"))
    assert token("dark", "--text") != (255, 255, 255)
    assert luminance(token("dark", "--accent")) > luminance(token("light", "--accent"))


def test_every_variable_a_style_sheet_or_script_uses_is_a_token() -> None:
    # A variable is a token, or one the page sets itself (a measure the script publishes, or a
    # local one such as --done of a meter).
    defined = {name for theme in THEMES for name in tokens(theme) if name.startswith("--")}
    published: set[str] = set()
    for path in SCRIPTS:
        published |= set(re.findall(r"setProperty\(\s*'(--[\w-]+)'", path.read_text("utf-8")))
    for path in [*STYLES, *SCRIPTS, STATIC / "base.css"]:
        text = path.read_text(encoding="utf-8")
        local = set(re.findall(r"(--[\w-]+)\s*:", text)) | published
        used = set(re.findall(r"var\((--[\w-]+)", text))
        assert used <= defined | local, f"{path.name}: {sorted(used - defined - local)}"


@pytest.mark.parametrize("theme", THEMES)
def test_text_and_signals_on_every_surface_reach_four_and_a_half_to_one(theme: str) -> None:
    def ratio(fore: str, back: str) -> float:
        return contrast(token(theme, fore), token(theme, back))

    low = [
        f"{fore} on {back}: {ratio(fore, back):.2f}"
        for fore in (*TEXT, *SIGNALS)
        for back in (*SURFACES, *TINTS)
        if ratio(fore, back) < 4.5
    ]
    assert low == []
    # What the primary button, the focus ring and the borders of controls are drawn with.
    assert ratio("--on-accent", "--accent") >= 4.5 and ratio("--on-accent", "--accent-hover") >= 4.5
    assert ratio("--text", "--accent-soft") >= 4.5
    for back in ("--bg", "--surface", "--surface-2", "--sidebar"):
        assert ratio("--focus", back) >= 3, back
        assert ratio("--border-strong", back) >= 3, back
        assert ratio("--accent", back) >= 3, back
    # The bar of a meter against its track, and the plain text on the two ends of the track.
    assert ratio("--ok", "--surface-3") >= 3


STATUS_COLOURS = {
    "ready": {"--info", "--info-soft"},
    "in_progress": {"--progress", "--progress-soft"},
    "blocked": {"--danger", "--danger-soft"},
    "done": {"--ok", "--ok-soft"},
}
STATUS_FAMILY = {colour for colours in STATUS_COLOURS.values() for colour in colours} | {
    "--accent",
    "--accent-text",
    "--accent-soft",
    "--warn",
    "--warn-soft",
}
STATUS_SELECTOR = re.compile(
    r"(?<![\w-])(?:status-|s-|tone-status-|graph-x-key-)(ready|in_progress|blocked|done)(?![\w-])"
)


def _comma_parts(selector: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for at, char in enumerate(selector):
        depth += char == "("
        depth -= char == ")"
        if char == "," and depth == 0:
            parts.append(selector[start:at])
            start = at + 1
    return [*parts, selector[start:]]


def test_a_status_has_the_same_colour_in_every_style_sheet() -> None:
    # Ready is blue, in progress violet, blocked red and done green, wherever a rule colours
    # something by its status: a chip, a column, a menu mark, a Roadmap bar, a mark of the Graph.
    # A rule for one status that reaches for another's colour (or for the accent, or amber) fails.
    wrong = []
    for path in STYLES:
        text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
            used = {f"--{name}" for name in re.findall(r"var\(--([\w-]+)", body)} & STATUS_FAMILY
            for part in _comma_parts(selector):
                found = set(STATUS_SELECTOR.findall(part))
                if len(found) == 1 and not used <= STATUS_COLOURS[next(iter(found))]:
                    wrong.append(f"{path.name}: {part.strip()} uses {sorted(used)}")
    assert wrong == []


def test_no_style_sheet_script_or_page_names_a_colour_of_its_own() -> None:
    # Every colour comes from tokens.css, so a theme can change all of them and nothing is left
    # in the colours of the other. The logo files in brand/ are images, not styled by the page.
    literal = re.compile(
        r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![\w-])|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color-mix)\("
    )
    named = re.compile(
        r"(?:color|background(?:-color)?|border(?:-\w+)?|outline(?:-color)?|fill|stroke|box-shadow|text-shadow)"
        r"\s*:[^;{}]*\b(?:white|black|red|green|blue|yellow|orange|purple|gray|grey|silver|navy|teal|maroon|olive|lime|aqua|fuchsia|beige|ivory)\b"
    )
    for path in [*STYLES, STATIC / "base.css", STATIC / "index.html", *SCRIPTS]:
        text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        if path.suffix == ".js":
            text = re.sub(r"(?m)^\s*//.*$", "", text)
        assert not literal.findall(text), f"{path.name}: {literal.findall(text)}"
        assert not named.findall(text), f"{path.name}: {named.findall(text)}"


def test_the_theme_comes_from_one_attribute_and_nothing_inverts_a_page_mechanically() -> None:
    # Light and Dark override the system, so no rule may ask the system itself (only theme.js
    # does, for System); and a theme is its own set of tokens, never a filter over the other.
    for path in [*STYLES, STATIC / "base.css", STATIC / "tokens.css"]:
        text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        assert "prefers-color-scheme" not in text, path.name
        assert not re.search(r"\b(?:invert|hue-rotate|sepia|saturate)\(", text), path.name
        assert not re.search(r"mix-blend-mode|backdrop-filter|(?<![\w-])filter\s*:", text), (
            path.name
        )
    assert "prefers-color-scheme" not in "".join(
        path.read_text(encoding="utf-8") for path in SCRIPTS if path.name != "theme.js"
    )


def test_the_theme_is_decided_by_an_external_script_in_the_head_ahead_of_every_style_sheet() -> (
    None
):
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    head = page.split("<head>", 1)[1].split("</head>", 1)[0]
    scripts = re.findall(r"<script\b([^>]*)>", head)
    assert scripts, "there is no script in the head"
    first = scripts[0]
    assert 'src="/board/theme.js"' in first
    # A classic script blocks until it has run: a module, `defer` or `async` would not.
    assert not re.search(r'type="module"|\bdefer\b|\basync\b', first), first
    assert head.index("/board/theme.js") < head.index('rel="stylesheet"'), "styles come first"
    assert head.index("/board/theme.js") < head.index("/board/tokens.css")
    assert re.search(r'<meta name="color-scheme" content="light dark">', head)
    # The page file itself has nothing inline: no script, no style element, no style attribute.
    assert not re.search(r"<script(?![^>]*\ssrc=)", page)
    assert "<style" not in page and " style=" not in page
    assert not re.search(r"\son\w+=", page), "an inline event handler"
    # Until the theme is decided the page says Light, never an empty or invented word.
    assert re.search(r'<html[^>]*\sdata-theme="light"', page)


def test_the_theme_script_is_a_classic_script_that_stores_one_key_and_nothing_else() -> None:
    script = (STATIC / "theme.js").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|export)\b", script, re.M), "a module would run too late"
    assert re.findall(r"var KEY = '([^']+)'", script) == ["vrum.theme"]
    assert re.findall(r"localStorage\.(\w+)\(", script) == ["getItem", "removeItem", "setItem"]
    assert "prefers-color-scheme: dark" in script
    for word in ("cookie", "sessionStorage", "fetch(", "XMLHttpRequest", "Authorization"):
        assert word not in script, word
