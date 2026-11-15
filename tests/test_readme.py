"""The README says what is true: its agent guide is the server's instructions word for word, the
files and settings it names exist, the tools it names are tools, and the example configurations are
valid JSON. (./acceptance.sh also runs the README's Python example against the real server.)"""

import asyncio
import json
import re
from pathlib import Path

from vrum.config import Settings
from vrum.server import INSTRUCTIONS, create_server

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
ENV = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "a-password", "VRUM_TOKEN": "t" * 24}

# Names in backticks that are not tools: the kinds of link.
NOT_TOOLS = {"needs_first", "relates_to"}


def the_agent_guide() -> str:
    found = re.search(
        r"<!-- agent-guide:start -->\n(.*?)\n<!-- agent-guide:end -->", README, re.DOTALL
    )
    assert found, "the README has no agent guide between its markers"
    return found.group(1)


def test_the_agent_guide_is_the_text_the_server_gives_every_agent() -> None:
    assert the_agent_guide() == INSTRUCTIONS.strip(), (
        "The README's agent guide differs from INSTRUCTIONS in src/vrum/server.py. Copy the "
        "server's text into the README between the agent-guide markers (or the other way round)."
    )


def test_every_file_and_script_the_readme_names_exists() -> None:
    named = set(re.findall(r"(?<![\w/.])((?:\./)?scripts/[\w.-]+|\./[\w.-]+\.sh)", README))
    named |= {name for name in re.findall(r"`([\w.-]+\.(?:md|yaml|example|sh))`", README)}
    assert len(named) >= 8
    for name in sorted(named):
        assert (ROOT / name.removeprefix("./")).exists(), (
            f"the README names {name}, which is missing"
        )


def test_every_setting_the_readme_names_is_a_real_one() -> None:
    known = (ROOT / ".env.example").read_text() + (ROOT / "compose.yaml").read_text()
    named = set(re.findall(r"`((?:VRUM|EMBEDDING|LLM)_[A-Z_]+)`", README))
    assert len(named) >= 8
    for name in sorted(named):
        assert name in known, f"the README names the setting {name}, which Vrum does not have"


def test_every_tool_the_readme_names_is_a_tool_the_server_has() -> None:
    server = create_server(Settings.from_env(ENV))
    tools = {tool.name for tool in asyncio.run(server.list_tools())}
    named = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", README)) - NOT_TOOLS
    assert len(named) >= 12
    missing = sorted(named - tools)
    assert not missing, f"the README names tools that do not exist: {missing}"


def test_every_image_and_link_to_a_file_in_the_readme_points_at_a_file_that_exists() -> None:
    targets = re.findall(r"!?\[[^\]]*\]\((?!https?:|#)([^)\s]+)\)", README)
    targets += re.findall(r'<img src="(?!https?:)([^"]+)"', README)
    assert "docs/marketing/website-header.png" in targets
    for target in targets:
        assert (ROOT / target).is_file(), f"the README points at {target}, which is missing"


def test_the_example_configurations_are_valid_json_and_point_at_the_mcp_endpoint() -> None:
    blocks = re.findall(r"```json\n(.*?)\n```", README, re.DOTALL)
    assert len(blocks) == 2
    for block in blocks:
        entry = json.loads(block)["mcpServers"]["vrum"]
        assert entry["type"] == "http" and entry["url"] == "http://127.0.0.1:8000/mcp"
        assert entry["headers"] == {"Authorization": "Bearer ${VRUM_TOKEN}"}
