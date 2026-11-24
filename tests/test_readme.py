"""The documentation says what is true: the README leads with an overview and getting started, its
two screenshots are there with captions, the agent guide is the server's instructions word for word,
every link and image points at something that exists, and the files, settings and tools that the
README and the pages in docs/ name are real. The example configurations are valid JSON. (And
./acceptance.sh runs the Python example in docs/agents.md against the real server.)"""

import asyncio
import json
import re
from pathlib import Path

import pytest

from vrum.config import Settings
from vrum.secret_formats import find_secret
from vrum.server import INSTRUCTIONS, create_server

ROOT = Path(__file__).resolve().parent.parent
README_PATH = ROOT / "README.md"
README = README_PATH.read_text(encoding="utf-8")
PAGES = [README_PATH, *sorted((ROOT / "docs").glob("*.md"))]
AGENTS = (ROOT / "docs" / "agents.md").read_text(encoding="utf-8")
ENV = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "a-password"}

# Names in backticks that look like tools but are not: the statuses and the kinds of link, a claim
# in an OpenID token, and the log line and reasons that the sign-in settings are written up with.
NOT_TOOLS = {
    "in_progress",
    "needs_first",
    "person_only",
    "verifier_only",
    "pull_request",
    "relates_to",
    "email_verified",
    "sso_login",
    "certificate_not_accepted",
    "directory_error",
    "service_account_refused",
    "poll_after_seconds",
    "new_epic",
    "after_id",
    "context_chars",
    "cut_note",
    "left_out",
    "max_chars",
    "next_after_id",
    "not_before",
    "held_by",
    "discovered_from",
    "event_id",
    "routine_why",
    "scores_note",
    "changes_requested",
    "plan_changes_requested",
    "look_into",
}
# Files the text names that are made when it is used, or that live in the reader's own project.
NOT_IN_THE_REPOSITORY = {".env", ".mcp.json", "CLAUDE.md"}


def prose(text: str) -> str:
    """The text without its fenced code blocks."""
    return re.sub(r"^```.*?^```[^\n]*$", "", text, flags=re.DOTALL | re.MULTILINE)


def headings(text: str, level: int) -> list[str]:
    return re.findall(rf"^{'#' * level} (.+)$", prose(text), flags=re.MULTILINE)


def anchor(heading: str) -> str:
    """How GitHub turns a heading into the address of its section."""
    words = re.sub(r"[^\w\- ]", "", heading.lower().replace("`", ""))
    return words.strip().replace(" ", "-")


def test_the_agent_guide_is_the_text_the_server_gives_every_agent() -> None:
    found = re.search(
        r"<!-- agent-guide:start -->\n(.*?)\n<!-- agent-guide:end -->", AGENTS, re.DOTALL
    )
    assert found, "docs/agents.md has no agent guide between its markers"
    assert found.group(1) == INSTRUCTIONS.strip(), (
        "The agent guide in docs/agents.md differs from INSTRUCTIONS in src/vrum/server.py. Copy "
        "the server's text between the agent-guide markers (or the other way round)."
    )
    assert "agent-guide:start" not in README, "the guide has one home: docs/agents.md"


def test_the_readme_begins_with_the_overview_and_then_getting_started() -> None:
    assert headings(README, 1) == ["Vrum"]
    assert headings(README, 2)[:2] == ["Overview", "Getting started"]


def test_the_readme_has_no_em_dash_no_en_dash_and_no_emoji() -> None:
    dashes = [
        f"line {n}"
        for n, line in enumerate(README.splitlines(), 1)
        if re.search("[\u2013\u2014]", line)
    ]
    assert not dashes, f"the README uses an em dash or an en dash on {dashes}; use a hyphen"
    emoji = re.findall("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f]", README)
    assert not emoji, f"the README uses emoji or pictographs: {emoji}"


def test_the_two_screenshots_are_in_place_with_alt_text_and_a_caption() -> None:
    getting_started = README.index("\n## Getting started")
    found = {
        name: (match, match.group(1))
        for name in ("board", "graph")
        if (match := re.search(rf"!\[([^\]]*)\]\(docs/screenshots/{name}\.png\)", README))
    }
    assert set(found) == {"board", "graph"}, "the README must show the Board and the Graph"
    assert found["board"][0].start() < getting_started, "the Board screenshot ends the overview"
    assert found["graph"][0].start() > getting_started, "the Graph screenshot is below the start"
    for name, (match, alt) in found.items():
        assert len(alt.split()) >= 6, f"the {name} screenshot needs meaningful alt text: {alt!r}"
        after = README[match.end() :].lstrip()
        caption = after.splitlines()[0].lstrip("*_ ")
        assert caption.startswith("Example project with fictional data"), (
            f"the {name} screenshot needs a caption that starts 'Example project with fictional "
            f"data', but the next line is {after.splitlines()[0]!r}"
        )
        image = ROOT / "docs" / "screenshots" / f"{name}.png"
        assert image.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{image.name} is not a PNG"
        assert image.stat().st_size < 400 * 1024, f"{image.name} is larger than 400 KB"


@pytest.mark.parametrize("page", PAGES, ids=lambda path: path.name)
def test_every_link_and_image_points_at_a_file_that_exists(page: Path) -> None:
    text = page.read_text(encoding="utf-8")
    targets = re.findall(r"!?\[[^\]]*\]\((?!https?:|mailto:)([^)\s]+)\)", text)
    targets += re.findall(r'<img src="(?!https?:)([^"]+)"', text)
    for target in targets:
        path, _, fragment = target.partition("#")
        linked = page if not path else (page.parent / path).resolve()
        assert linked.is_file(), f"{page.name} points at {target}, which is missing"
        if fragment and linked.suffix == ".md":
            known = {anchor(h) for level in (1, 2, 3) for h in headings(linked.read_text(), level)}
            assert fragment in known, (
                f"{page.name} points at {target}, but there is no such heading"
            )


def test_every_page_in_docs_is_linked_from_the_readme() -> None:
    for page in PAGES[1:]:
        assert f"docs/{page.name}" in README, f"the README does not link to docs/{page.name}"


@pytest.mark.parametrize("page", PAGES, ids=lambda path: path.name)
def test_every_file_and_script_the_page_names_exists(page: Path) -> None:
    text = page.read_text(encoding="utf-8")
    named = set(re.findall(r"(?<![\w/.])((?:\./)?(?:scripts|src|tests|docs)/[\w./-]*[\w-])", text))
    named |= set(re.findall(r"`([A-Z][A-Z_]*\.md|[\w.-]+\.(?:yaml|example|sh))`", text))
    named |= set(re.findall(r"(?<![\w/.])(\./[\w.-]+\.sh)", text))
    for name in sorted(named - NOT_IN_THE_REPOSITORY):
        assert (ROOT / name.removeprefix("./")).exists(), (
            f"{page.name} names {name}, which is missing"
        )


@pytest.mark.parametrize("page", PAGES, ids=lambda path: path.name)
def test_every_setting_the_page_names_is_a_real_one(page: Path) -> None:
    known = "".join(
        (ROOT / name).read_text() for name in (".env.example", "compose.yaml", "src/vrum/config.py")
    )
    named = set(re.findall(r"`((?:VRUM|EMBEDDING|LLM)_[A-Z_]+)`", page.read_text(encoding="utf-8")))
    for name in sorted(named):
        assert name in known, f"{page.name} names the setting {name}, which Vrum does not have"


@pytest.mark.parametrize("page", PAGES, ids=lambda path: path.name)
def test_every_tool_the_page_names_is_a_tool_the_server_has(page: Path) -> None:
    server = create_server(Settings.from_env(ENV))
    tools = {tool.name for tool in asyncio.run(server.list_tools())}
    named = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", page.read_text(encoding="utf-8")))
    missing = sorted(named - NOT_TOOLS - tools)
    assert not missing, f"{page.name} names tools that do not exist: {missing}"


def test_the_example_configurations_are_valid_json_and_point_at_the_mcp_endpoint() -> None:
    for block in re.findall(r"```json\n(.*?)\n```", AGENTS, re.DOTALL):
        entry = json.loads(block)["mcpServers"]["vrum"]
        assert entry["type"] == "http" and entry["url"] == "http://127.0.0.1:8000/mcp"
        assert entry["headers"] == {"Authorization": "Bearer ${VRUM_AGENT_KEY}"}


def test_the_python_example_is_the_first_python_block_of_the_agent_page() -> None:
    """./acceptance.sh runs this block against the real server."""
    found = re.search(r"```python\n(.*?)\n```", AGENTS, re.DOTALL)
    assert found, "docs/agents.md has no Python example"
    compile(found.group(1), "docs/agents.md", "exec")
    assert "VRUM_AGENT_KEY" in found.group(1) and "list_nodes" in found.group(1)


@pytest.mark.parametrize("page", PAGES, ids=lambda path: path.name)
def test_no_page_holds_a_secret_in_a_well_known_format(page: Path) -> None:
    text = page.read_text(encoding="utf-8")
    assert find_secret(text) is None, f"{page.name} holds something that looks like a secret"
    assert not re.search(r"\bvr[ks]_[\w-]{8,}", text), f"{page.name} holds what looks like a key"
