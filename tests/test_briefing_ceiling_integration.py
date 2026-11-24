"""M44: a briefing cannot grow without bound. The server cuts what it adds around an item (its
acceptance criteria, definition of done, notes, handoffs, questions, links and look-alikes) to
`context_chars` (10,000 by default) and says what it left out; this makes the worst item it can
(a long text, dozens of notes, criteria, handoffs, questions and links, every one of them long) and
measures what `next_work` and `recall` give back, so that the ceiling stated in the agent guide is
a measured one."""

import json
from typing import Any

import pytest

from tests.helpers import connect, make_note, make_rule, ok, refused, reindexed, unique_word
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

LONG = "A long line that says something about the work and goes on for a while. " * 40


async def the_worst_item(agent: Any, owner: Any, project: Any) -> dict[str, str]:
    word = unique_word()
    epic = await ok(agent, "create_node", actor="x", kind="epic", title=f"Epic {word}", body=LONG)
    story = await ok(
        agent,
        "create_node",
        actor="x",
        kind="story",
        title=f"Story {word}",
        parent=epic["ref"],
        body=LONG,
    )
    task = await ok(
        agent,
        "create_node",
        actor="x",
        kind="task",
        title=f"Task {word}",
        parent=story["ref"],
        body=LONG * 3,
    )
    for index in range(40):
        await make_rule(agent, f"Criterion {index} {word}", body=LONG, node=task["ref"])
    for index in range(30):
        await make_rule(agent, f"Story criterion {index} {word}", body=LONG, node=story["ref"])
    for index in range(40):
        note = await make_note(
            agent, f"Note {index} {word}\n\n{LONG}", why=LONG, nodes=[task["ref"]]
        )
        assert note["id"]
    for index in range(40):
        await make_note(agent, f"Wide {index} {word}\n\n{LONG}", why=LONG, project_wide=True)
    for index in range(25):
        other = await ok(
            agent,
            "create_node",
            actor="x",
            kind="task",
            title=f"Found {index} {word}",
            parent=story["ref"],
            body=LONG,
        )
        await ok(
            agent, "add_link", source=other["ref"], target=task["ref"], link_type="discovered_from"
        )
    for index in range(20):
        await ok(agent, "claim_node", node=task["ref"])
        await ok(
            agent,
            "finish_work",
            node=task["ref"],
            outcome="released",
            note=f"Handoff {index}: {LONG}",
            ask={"question": f"Question {index}? {LONG[:500]}", "choices": ["Yes", "No"]},
        )
    return {"epic": epic["ref"], "story": story["ref"], "task": task["ref"]}


# What the guide says (docs/agents.md, "How big a briefing can be"): what next_work adds around an
# item is cut to context_chars, plus the few hundred characters of its envelope; one recall is cut
# to max_chars, plus the first entry of each section, so that reading on always moves forward.
ENVELOPE = 600
LARGEST_ENTRY = 9_000


async def test_the_worst_briefing_stays_within_what_the_guide_says() -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        refs = await the_worst_item(agent, owner, project)
        await reindexed(agent)
        for chars in (1000, 10_000, 100_000):
            got = await ok(
                agent, "next_work", within_node=refs["task"], peek=True, context_chars=chars
            )
            context = got["context"]
            assert context["max_characters"] == chars
            assert len(json.dumps(context)) <= chars + ENVELOPE, (chars, len(json.dumps(context)))
            # The item's own text is the work and is never cut.
            assert got["work"]["body"].startswith("A long line") and len(got["work"]["body"]) > 8000
            if chars < 100_000:
                assert sum(context["left_out"].values()) > 0 and "left out" in context["cut_note"]
        default = await ok(agent, "next_work", within_node=refs["task"], peek=True)
        assert default["context"]["max_characters"] == 10_000
        assert len(json.dumps(default)) < 25_000, len(json.dumps(default))
        # The discovered_from links of the item are there and are cut like the rest.
        assert "discovered_from" in default["context"]

        for chars in (1000, 10_000, 40_000):
            recalled = await ok(
                agent, "recall", node=refs["task"], with_similar=True, max_chars=chars
            )
            assert recalled["max_characters"] == chars
            sections = ["notes", "project_wide_notes", "acceptance_criteria", "past_notes"]
            filled = sum(1 for name in sections if recalled[name]["items"])
            size = len(json.dumps(recalled))
            assert size <= chars + filled * LARGEST_ENTRY + ENVELOPE, (chars, size)
            assert recalled["left_out"] and "left out" in recalled["cut_note"]
            for name, dropped in recalled["left_out"].items():
                assert recalled[name]["next_after_id"] is not None and dropped > 0
        # Nothing is lost by the cut: reading on with show and after_id gives every note once.
        seen: list[int] = []
        after: int | None = None
        while True:
            page = await ok(
                agent, "recall", node=refs["task"], show="notes", max_chars=20_000, after_id=after
            )
            seen += [entry["note"]["id"] for entry in page["notes"]["items"]]
            after = page["notes"]["next_after_id"]
            if after is None:
                break
        assert len(seen) == len(set(seen)) == page["notes"]["count"] == 40
        # Project-wide notes come with every recall, so the epic of this project is cut too.
        wide = await ok(agent, "recall", node=refs["epic"])
        assert "project_wide_notes" in wide["left_out"]
    # A recall that fits says nothing was cut.
    quiet = make_project(register())
    async with connect(quiet.mcp_url, quiet.key) as agent:
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Small")
        small = await ok(agent, "recall", node=epic["ref"])
        assert small["left_out"] == {} and small["cut_note"] is None
        for bad in (999, 1_000_001):
            said = await refused(agent, "recall", node=epic["ref"], max_chars=bad)
            assert "max_chars must be a whole number from 1000 to 1000000" in said
