"""Chapter of the acceptance scenario for Needs a person as a review of changes (M49).

The page that asks a person to approve a plan has to show the whole of it first, as a review of a
pull request does. The server reads one plan whole (`list_plans` with `plan`: every item it made,
with its details, size, priority, dates, links and acceptance criteria, and the existing items it
hangs under or points at as context), and the board draws that as lines of added work above the
button. The proofs here are told from outside, a real MCP client and the board's own calls as the
people of the project, and then a headless browser on the page. Each part works in a project of its
own."""

import re
from typing import Any

from mcp import Client
from playwright.async_api import Locator, Page, expect

from tests.acceptance_scenario import chapter, clip, proof
from tests.board_helpers import SLOW, Watched, go, page_drawn
from tests.helpers import REPO_ROOT, connect, make_tree, ok, refused
from tests.test_checks_integration import as_person
from tests.world import Person, Project, join_project, make_key, make_project, register

DOCS = REPO_ROOT / "docs" / "agents.md"
THE_NUMBER_IN_THE_DOCS = "plan: 1402"
# The lines of a change's diff as a person sees them: a line that is folded is in the page and
# hidden, and an item's own line is the one that is not its details or a criterion.
ITEM_LINES = ".diff-line[data-sign='+'][data-ref]:not([data-part]):visible"
CONTEXT_LINES = ".diff-line[data-sign='.'][data-ref]:visible"
REMOVED_LINES = ".diff-line[data-sign='-'][data-ref]:visible"


def words(refusal: str) -> str:
    return refusal.split(": ", 1)[1] if refusal.startswith("Error executing tool") else refusal


def person(project: Project, who: Person, tool: str, **arguments: Any) -> Any:
    """What the board does when a person presses a button: the tool, as that person."""
    return as_person(who, project, tool, **arguments)


async def a_plan(
    agent: Client, epic: dict[str, Any], story: dict[str, Any], title: str
) -> dict[str, Any]:
    """A draft plan worth reviewing: two stories under an epic that exists, one with details of
    three lines, a high priority and dates, tasks (one of them left without a size), a link that
    says one task needs another first, links to a story that exists, and acceptance criteria on a
    story and on a task (and one placed nowhere)."""
    items = [
        {
            "key": "cards",
            "kind": "story",
            "title": "Pay by card",
            "parent": epic["ref"],
            "size": "l",
            "priority": "high",
            "body": "Cards only.\nNo wallets yet.\nThe provider's sandbox is enough for now.",
            "start_on": "2031-01-10",
            "target_on": "2031-02-20",
        },
        {
            "key": "form",
            "kind": "task",
            "title": "Build the card form",
            "parent": "cards",
            "size": "s",
            "body": "One field for the number, one for the date.",
        },
        {
            "key": "call",
            "kind": "task",
            "title": "Call the provider",
            "parent": "cards",
            "priority": "urgent",
        },
        {
            "key": "wallet",
            "kind": "story",
            "title": "Wallet top-up",
            "parent": epic["ref"],
            "size": "m",
        },
    ]
    links = [
        {"source": "call", "target": "form", "link_type": "needs_first"},
        {"source": "cards", "target": story["ref"], "link_type": "relates_to"},
        {"source": "wallet", "target": "cards", "link_type": "relates_to"},
    ]
    rules = [
        {"title": "A declined card says why", "node": "form"},
        {
            "title": "Cards are never stored",
            "body": "Only the provider holds them.",
            "node": "cards",
        },
        {"title": "Placed later"},
    ]
    return await ok(
        agent, "create_plan", items=items, links=links, rules=rules, draft=True, title=title
    )


async def a_big_plan(agent: Client, epic: dict[str, Any], title: str) -> dict[str, Any]:
    """A draft plan of 45 items: three stories with fourteen tasks each, none with details."""
    items: list[dict[str, Any]] = []
    for story in range(3):
        items.append(
            {"key": f"s{story}", "kind": "story", "title": f"Part {story}", "parent": epic["ref"]}
        )
        items += [
            {
                "key": f"s{story}t{task}",
                "kind": "task",
                "title": f"Part {story} step {task}",
                "parent": f"s{story}",
                "size": "xs",
            }
            for task in range(14)
        ]
    return await ok(agent, "create_plan", items=items, draft=True, title=title)


async def the_whole_plan(agent: Client, number: int) -> dict[str, Any]:
    (plan,) = (await ok(agent, "list_plans", plan=number))["items"]
    return dict(plan)


def pairs(links: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(one["link_type"], one["target"]) for one in links]


async def a_plan_reads_whole() -> None:
    """`list_plans` with `plan` reads one plan whole, and what it says is what get_node,
    list_links and list_rules say of each item."""
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, _ = await make_tree(agent)
        made = await a_plan(agent, epic, story, "Card payments")
        by_key = {c["key"]: c["ref"] for c in made["created"]}
        plan = await the_whole_plan(agent, made["plan"])
        tree = plan["tree"]
        wrong: list[str] = []
        for item in tree:
            node = await ok(agent, "get_node", node=item["ref"])
            for field in ("kind", "parent", "title", "body", "priority", "size", "start_on"):
                if item[field] != node[field]:
                    wrong.append(f"{item['ref']} {field}")
            if item["target_on"] != node["target_on"]:
                wrong.append(f"{item['ref']} target_on")
            listed = (await ok(agent, "list_links", node=item["ref"], limit=50))["items"]
            starting = pairs([one for one in listed if one["source"] == item["ref"]])
            if pairs(item["links"]) != starting:
                wrong.append(f"{item['ref']} links")
            rules = (await ok(agent, "list_rules", node=item["ref"], detail=True))["items"]
            placed = [(r["id"], r["title"], r["body"], r["status"]) for r in rules]
            if [(c["id"], c["title"], c["body"], c["status"]) for c in item["criteria"]] != placed:
                wrong.append(f"{item['ref']} criteria")
        unplaced = (await ok(agent, "list_rules", unplaced=True))["items"]
        listed_plans = (await ok(agent, "list_plans"))["items"][0]
        proof(
            "M49",
            "list_plans with a plan's number reads that plan whole: every item it made, parents "
            "first, each with the details, priority, size, dates and parent that get_node says, "
            "the links that start at it as list_links says and its acceptance criteria as "
            "list_rules says (the one placed nowhere is in none), and as context the epic it "
            "hangs under and the story it relates to, both of which already existed",
            not wrong
            and [one["ref"] for one in tree]
            == [by_key["cards"], by_key["form"], by_key["call"], by_key["wallet"]]
            and (plan["stories"], plan["tasks"], plan["state"]) == (2, 2, "proposed")
            and [(c["ref"], c["why"]) for c in plan["context"]]
            == [(epic["ref"], "parent"), (story["ref"], "linked")]
            and [one["size"] for one in tree] == ["l", "s", None, "m"]
            and tree[0]["priority"] == "high"
            and tree[0]["body"].count("\n") == 2
            and (tree[0]["start_on"], tree[0]["target_on"]) == ("2031-01-10", "2031-02-20")
            and pairs(tree[2]["links"]) == [("needs_first", by_key["form"])]
            and sum(len(one["criteria"]) for one in tree) == 2
            and [r["title"] for r in unplaced] == ["Placed later"]
            and listed_plans["tree"] == []
            and listed_plans["context"] == [],
            f"{len(tree)} items, {len(plan['context'])} context, {clip(str(wrong))}",
        )

        said = words(await refused(agent, "list_plans", plan=999))
        by_agent = words(await refused(agent, "approve_plan", plan=made["plan"], approved=True))
        proof(
            "M49",
            "a plan that is not there is refused in words, a list of plans carries no tree and no "
            "context, and a key can read a plan whole and cannot decide it",
            "no plan 999" in said and "not allowed to use" in by_agent,
            clip(said),
        )


async def a_plan_of_sixty_items() -> None:
    """The reference list stops at fifty; the whole read does not."""
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, _ = await make_tree(agent)
        items: list[dict[str, Any]] = [
            {"key": "s", "kind": "story", "title": "Everything", "parent": epic["ref"]}
        ]
        items += [
            {"key": f"t{n}", "kind": "task", "title": f"Step {n}", "parent": "s"} for n in range(59)
        ]
        made = await ok(agent, "create_plan", items=items, draft=True)
        plan = await the_whole_plan(agent, made["plan"])
        refs = [one["ref"] for one in plan["tree"]]
        created = [c["ref"] for c in made["created"]]
        proof(
            "M49",
            "a plan of 60 items lists 50 references in items, as a list of plans always did, and "
            "reads all 60 in tree, each once, the story before its tasks",
            len(plan["items"]) == 50
            and len(refs) == 60
            and sorted(refs) == sorted(created)
            and refs[0] == created[0]
            and plan["tree"][0]["kind"] == "story"
            and all(one["parent"] == created[0] for one in plan["tree"][1:]),
            f"items {len(plan['items'])}, tree {len(refs)}",
        )


async def decided_plans_still_read() -> None:
    """A person decides a plan; what was proposed can still be read, and a viewer reads it too."""
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, _ = await make_tree(agent)
        first = await a_plan(agent, epic, story, "To approve")
        second = await a_plan(agent, epic, story, "To reject")
        before = await the_whole_plan(agent, first["plan"])
        as_viewer = person(project, viewer, "list_plans", plan=first["plan"])
        viewer_tried = person(project, viewer, "approve_plan", plan=first["plan"], approved=True)
        approved = person(project, owner, "approve_plan", plan=first["plan"], approved=True)
        rejected = person(
            project, owner, "approve_plan", plan=second["plan"], approved=False, reason="Too wide"
        )
        after = await the_whole_plan(agent, first["plan"])
        gone = await the_whole_plan(agent, second["plan"])
        refs = [one["ref"] for one in before["tree"]]
        proof(
            "M49",
            "a viewer reads a plan whole on the board and is refused deciding it; once a person "
            "approves it the plan still reads whole, the same items, with its state and who "
            "decided; a rejected plan reads whole too, with the reason, and the answer to the "
            "decision itself carries no tree",
            as_viewer.status == 200
            and [one["ref"] for one in as_viewer.body["items"][0]["tree"]] == refs
            and viewer_tried.status in (400, 403)
            and approved.status == 200
            and approved.body["tree"] == []
            and approved.body["context"] == []
            and (after["state"], after["decided_by"]) == ("approved", owner.name)
            and [one["ref"] for one in after["tree"]] == refs
            and after["context"] == before["context"]
            and rejected.status == 200
            and (gone["state"], gone["reason"]) == ("rejected", "Too wide")
            and len(gone["tree"]) == 4,
            f"{len(refs)} items, approved by {owner.name}",
        )


async def the_example_in_the_docs() -> None:
    """The TypeScript example in docs/agents.md, as written (with the plan's number put in), runs
    in code mode and says what a person should be told before deciding."""
    text = DOCS.read_text(encoding="utf-8")
    blocks = re.findall(r"```ts\n(.*?)```", text, flags=re.S)
    (script,) = [
        block
        for block in blocks
        if THE_NUMBER_IN_THE_DOCS in block and block.startswith("// Read the drafted plan")
    ]
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        epic, story, _ = await make_tree(agent)
        made = await a_plan(agent, epic, story, "Card payments")
        by_key = {c["key"]: c["ref"] for c in made["created"]}
    async with connect(project.mcp_url, make_key(project, "coder", code_mode=True)) as coder:
        ran = await ok(
            coder,
            "run",
            code=script.replace(THE_NUMBER_IN_THE_DOCS, f"plan: {made['plan']}"),
        )
    proof(
        "M49",
        "the example in docs/agents.md, run as it is written in code mode, reads a drafted plan "
        "whole and says what a person should be told before deciding: how many items, what it "
        "hangs under, which item has no size and which story has no acceptance criterion",
        ran["result"]
        == {
            "title": "Card payments",
            "state": "proposed",
            "items": 4,
            "hangsUnder": [epic["ref"]],
            "unsized": [by_key["call"]],
            "storiesWithNoCriterion": [by_key["wallet"]],
        },
        clip(str(ran["result"]), 100),
    )


# -- the page's shared parts (the proofs on the page are in tests/acceptance_review_page.py) ------


async def the_needs_page(w: Watched) -> None:
    await go(w.page, w.board, "#/needs")


def plan_card(page: Page, number: int) -> Locator:
    """The card of a plan on the list."""
    return page.locator(f"li.needs-row[data-change='plan:{number}']")


def review_page(page: Page) -> Locator:
    """The review page of the change the address names."""
    return page.locator("#review-page")


async def open_review(w: Watched, change: str, **more: str) -> None:
    """Open the review page of a change by its address (`plan:9`, `placement:7`, ...), as a link
    to it does, and wait until it is drawn."""
    query = "".join(f"&{key}={value}" for key, value in more.items())
    await go(w.page, w.board, f"#/review?change={change}{query}")
    await expect(review_page(w.page)).to_have_attribute("data-change", change, timeout=SLOW)
    # A suggested move is read once for all of them: its lines come a moment after the page.
    await w.page.wait_for_function(
        "() => { const d = document.querySelector('#review-diff');"
        " return !d || !!d.querySelector('.diff-line, .diff-hunk'); }",
        timeout=SLOW,
    )
    await page_drawn(w.page)


async def lines_of(scope: Locator, selector: str, count: int | None = None) -> list[str]:
    """The `data-ref` of each line of a change's diff that matches `selector`, in page order. The
    whole of a plan is read after the change is drawn, so with a `count` it first waits until that
    many lines are on show. `scope` is the review page (or a card's preview)."""
    lines = scope.locator(f".change-diff {selector}")
    if count is not None:
        await expect(lines).to_have_count(count, timeout=SLOW)
    refs: list[str] = await lines.evaluate_all("els => els.map(e => e.dataset.ref)")
    return refs


async def open_everything(scope: Locator) -> None:
    """Press every fold of the diff until nothing is folded: one press opens what it names."""
    folds = scope.locator(".change-diff .diff-fold")
    for _ in range(60):
        if not await folds.count():
            return
        await folds.first.click()


async def whole_plan(page: Page) -> None:
    """A plan that was revised is drawn as what changed since the revision before it. Press Whole
    plan to read all of it, as a person does."""
    switch = page.locator("#review-mode")
    await expect(switch).to_be_visible(timeout=SLOW)
    whole = switch.locator("button[data-mode='whole']")
    if await whole.get_attribute("aria-pressed") != "true":
        await whole.click()
    await expect(page.locator("#review-diff[data-mode='whole']")).to_have_count(1, timeout=SLOW)


async def the_diff_is_before_the_decision(scope: Locator) -> bool:
    """The diff, then its last line, then the bar that decides it (the page's own, kept in view)."""
    return bool(
        await scope.evaluate(
            """el => {
                const diff = el.querySelector('.change-diff');
                const bar = el.querySelector('.decision-bar');
                const last = [...diff.querySelectorAll('.diff-line')].pop();
                const FOLLOWING = Node.DOCUMENT_POSITION_FOLLOWING;
                const after = (a, b) => !!(a.compareDocumentPosition(b) & FOLLOWING);
                return after(diff, bar) && after(last, bar);
            }"""
        )
    )


async def review() -> None:
    chapter("Needs a person as a review of changes (M49)")
    await a_plan_reads_whole()
    await a_plan_of_sixty_items()
    await decided_plans_still_read()
    await the_example_in_the_docs()
    # The page (tests/acceptance_review_page.py) is imported where it is run, since it builds on
    # this file.
    from tests.acceptance_review_page import review_on_the_page  # noqa: PLC0415

    await review_on_the_page()
