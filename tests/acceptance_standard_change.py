"""Browser proofs of the M52 addendum (tests/acceptance_standard.py): pressing a red or green line
of
a diff, with the mouse or with Enter or Space, shows the work as the screen a person normally uses
for it (the creation layout for what a plan adds, the edit layout for what a revision changes or
takes out, the edit layout for what a suggested move moves, the layout of a recorded note), read
only, with each new value green and each old value red and every other field plain. Each proof
compares the words in the dialog with what a real MCP client reads from the tools (`list_plans`
with a revision, `list_placements`, `get_node`, `get_note`), in a project of its own, with a
headless browser signed in as the owner or as a viewer of it."""

import re
from typing import Any

from playwright.async_api import Locator, Page, expect

from tests.acceptance_review import open_review, person, review_page
from tests.acceptance_revising import drafted, revision
from tests.acceptance_revising_page import NEW_BODY, NEW_TITLE
from tests.acceptance_scenario import clip, proof
from tests.acceptance_standard import make
from tests.acceptance_standard_page import NARROW, WIDE
from tests.board_helpers import SLOW, browser_page, go
from tests.helpers import code_of, connect, ok
from tests.world import join_project, make_project, register

KINDS = {"epic": "Epic", "story": "Story", "task": "Task"}
DAYS = ("start_on", "target_on")
# The fields of a work's layout, in the order the creation and edit forms have them.
ORDER = ("kind", "title", "body", "parent", "size", "priority", *DAYS, "links", "criteria")

# What the dialog holds: each field with its state (new, removed, changed or same) and its values
# with their sign (none for a field that is plain), as words, without what only a screen reader
# hears.
VALUES = """d => [...d.querySelectorAll('div.change-field')].map(f => [
    f.dataset.field, f.dataset.state,
    [...f.querySelectorAll('div.change-value')].map(v => {
        const body = v.querySelector('.change-body').cloneNode(true);
        body.querySelectorAll('.sr-only').forEach(e => e.remove());
        return [v.dataset.sign ?? null, body.textContent.replace(/\\s+/g, ' ').trim()];
    }),
])"""

# Every text node of six words or more in the open dialog that is not somebody's own words.
DIALOG_SENTENCES = """() => {
    const found = [];
    const walker = document.createTreeWalker(
        document.querySelector('dialog#change-dialog'), NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = node.textContent.replace(/\\s+/g, ' ').trim();
        const skip = '[data-words="theirs"], .problem, [role=alert], .hovercard, .sr-only';
        if (text && text.split(' ').length >= 6 && !node.parentElement.closest(skip)) {
            found.push(text);
        }
    }
    return found;
}"""

# The background colours of a value of each sign (its words and its gutter) and of the line of the
# diff of the same sign, behind the dialog.
COLOURS = """sign => {
    const colour = el => el ? getComputedStyle(el).backgroundColor : null;
    const dialog = document.querySelector('dialog#change-dialog');
    const page = document.querySelector('#review-page');
    return [
        colour(dialog.querySelector(`.change-value[data-sign='${sign}'] .change-body`)),
        colour(page.querySelector(`.diff-line[data-sign='${sign}'] .diff-code`)),
        colour(dialog.querySelector(`.change-value[data-sign='${sign}'] .change-sign`)),
        colour(page.querySelector(`.diff-line[data-sign='${sign}'] .diff-sign`)),
    ];
}"""

# What leaves a dialog wider than a narrow window, and whether its buttons can be reached.
FITS = """() => {
    const box = document.querySelector('dialog#change-dialog').getBoundingClientRect();
    const close = document.querySelector('#change-close').getBoundingClientRect();
    return {
        left: box.left, right: box.right, width: window.innerWidth,
        sideways: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        within: close.left >= 0 && close.right <= window.innerWidth
            && close.bottom <= window.innerHeight,
        clipped: document.querySelector('dialog#change-dialog').scrollWidth
            > document.querySelector('dialog#change-dialog').clientWidth,
    };
}"""


def squash(text: str) -> str:
    """Words with no space left in them: a Markdown paragraph break and a newline read the same."""
    return re.sub(r"\s+", "", text)


def shape_of_before(key: str, value: Any) -> Any:
    """A part of an item as a revision compared it, as `list_plans` shows it for the item itself."""
    if key == "links":
        return [{"link_type": s.split()[0], "target": s.split()[1]} for s in value]
    if key == "criteria":
        return [{"id": i, "title": t, "body": b} for i, t, b in value]
    return value


def pieces_of(key: str, value: Any, lookup: dict[str, str]) -> list[str]:
    """The words a value of a field must have in the dialog."""
    if key == "kind":
        return [KINDS[value]]
    if key == "parent":
        return [code_of(value), lookup.get(value, "")]
    if key == "size":
        return [f"Size {str(value).upper()}"]
    if key == "links":
        return [code_of(one["target"]) for one in value]
    if key == "criteria":
        return [piece for one in value for piece in (f"AC-{one['id']}", one["title"])]
    return [str(value)]


def has(key: str, text: str, pieces: list[str]) -> bool:
    if key == "body":
        return all(squash(piece) in squash(text) for piece in pieces)
    return all(piece.lower() in text.lower() for piece in pieces)


def present(item: dict[str, Any]) -> list[str]:
    """The fields of a work that have something in them: a field with nothing says nothing."""
    said = ["kind", "title"]
    if str(item["body"]).strip():
        said.append("body")
    if item["parent"]:
        said.append("parent")
    if item["size"]:
        said.append("size")
    if item["priority"] != "normal":
        said.append("priority")
    said += [day for day in DAYS if item[day]]
    if item["links"]:
        said.append("links")
    if item["criteria"]:
        said.append("criteria")
    return said


async def snapshot(page: Page) -> dict[str, Any]:
    box = page.locator("dialog#change-dialog")
    fields: list[list[Any]] = await box.evaluate(VALUES)
    link = box.locator("a#change-open")
    there = await link.count() == 1
    said: list[str] = await page.evaluate(DIALOG_SENTENCES)
    return {
        "said": said,
        "mode": await box.get_attribute("data-mode"),
        "kind": await box.get_attribute("data-kind"),
        "ref": await box.get_attribute("data-ref"),
        "fields": fields,
        "buttons": await box.locator("button").all_inner_texts(),
        "controls": await box.locator(
            "input, textarea, select, form, [contenteditable], button[type=submit]"
        ).count(),
        "open": await link.get_attribute("href") if there else None,
        "open_words": spoken_words(await link.inner_text()) if there else "",
    }


def spoken_words(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


async def open_it(page: Page, line: Locator, how: str) -> None:
    """Open a line's dialog with the mouse, or with Enter or Space when the line has the focus."""
    if how == "click":
        await line.click()
    else:
        await line.focus()
        await page.keyboard.press("Enter" if how == "enter" else "Space")
    box = page.locator("dialog#change-dialog")
    await expect(box).to_be_visible(timeout=SLOW)
    await expect(box.locator("div.change-field, p.diff-problem").first).to_be_visible(timeout=SLOW)


async def close_it(page: Page, line: Locator) -> bool:
    """Escape closes the dialog and the focus is on the line again."""
    await page.keyboard.press("Escape")
    await expect(page.locator("dialog#change-dialog")).to_have_count(0, timeout=SLOW)
    back = await line.evaluate("el => el === document.activeElement")
    return bool(back)


def line_of(scope: Locator, sign: str, ref: str) -> Locator:
    return scope.locator(
        f".diff-line.diff-openable[data-sign='{sign}'][data-ref='{ref}']:not([data-part])"
    )


def wrong_item(
    snap: dict[str, Any],
    mode: str,
    item: dict[str, Any],
    lookup: dict[str, str],
    entry: dict[str, Any] | None = None,
) -> list[str]:
    """Where a dialog differs from what the tools say of the work: its mode, which fields it has,
    the
    sign and state of each, the words of each value (the new one and, for a field a revision
    changed, the old one) and that it holds no control but Close."""
    wrong: list[str] = []
    changed = list(entry["fields"]) if entry else []
    if (snap["mode"], snap["kind"], snap["ref"]) != (mode, item["kind"], item["ref"]):
        wrong.append(f"{snap['mode']} {snap['kind']} {snap['ref']} is not {mode} {item['ref']}")
    shown = [key for key in ORDER if key in present(item) or key in changed]
    if [f[0] for f in snap["fields"]] != shown:
        wrong.append(f"fields {[f[0] for f in snap['fields']]} are not {shown}")
    for key, state, values in snap["fields"]:
        if mode in ("added", "removed"):
            sign, wanted = ("+", "new") if mode == "added" else ("-", "removed")
            if (state, [v[0] for v in values]) != (wanted, [sign]):
                wrong.append(f"{key} is {state} with {[v[0] for v in values]}")
        elif key in changed:
            if (state, [v[0] for v in values]) != ("changed", ["-", "+"]):
                wrong.append(f"{key} is {state} with {[v[0] for v in values]}")
            elif entry is not None:
                old = pieces_of(key, shape_of_before(key, entry["before"][key]), lookup)
                if not has(key, values[0][1], old):
                    wrong.append(f"the old {key} is not {old}")
        elif (state, [v[0] for v in values]) != ("same", [None]):
            wrong.append(f"{key} is {state} with {[v[0] for v in values]}")
        if item[key] not in (None, "", []) and not has(
            key, values[-1][1], pieces_of(key, item[key], lookup)
        ):
            wrong.append(f"the {'new ' if mode == 'changed' and key in changed else ''}{key}")
        if mode == "changed" and key in changed and len(values) == 2:
            # The old words are in the red value and the new words in the green one.
            new_pieces = pieces_of(key, item[key], lookup)
            if has(key, values[0][1], new_pieces) and not has(key, values[1][1], new_pieces):
                wrong.append(f"{key}: the old and the new are the wrong way round")
    if snap["buttons"] != ["Close"] or snap["controls"]:
        wrong.append(f"controls {snap['buttons']} {snap['controls']}")
    if snap["said"]:
        wrong.append(f"sentences {snap['said']}")
    if snap["ref"] and (f"item={snap['ref']}" not in str(snap["open"])):
        wrong.append(f"the link goes to {snap['open']}")
    if snap["ref"] and snap["open_words"] != f"Open {code_of(snap['ref'])}":
        wrong.append(f"the link says {snap['open_words']}")
    return wrong


async def what_there_is() -> dict[str, Any]:
    """A project with a drafted plan of rich items, a plan at revision 2 whose items changed in
    several parts, were taken out and added, suggested moves (one into an epic that exists, one
    into an epic that would be made) and two notes waiting for confirmation, and a viewer."""
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    async with connect(project.mcp_url, project.key) as agent:
        epic = await make(agent, "epic", "Winter fleet and the whole depot")
        other = await make(agent, "story", "Spring tyre check before the season", epic["ref"])
        rich = await ok(
            agent,
            "create_plan",
            draft=True,
            title="Studded tyres for the river path",
            items=[
                {
                    "key": "s",
                    "kind": "story",
                    "title": "Studded tyres on the river path and the hill route",
                    "body": "Forty bikes.\nBefore the first frost.",
                    "parent": epic["ref"],
                    "size": "l",
                    "priority": "high",
                    "start_on": "2027-01-04",
                    "target_on": "2027-02-12",
                },
                {
                    "key": "t",
                    "kind": "task",
                    "title": "Fit studded tyres on forty bikes at the depot",
                    "body": "Ten bikes a day.",
                    "parent": "s",
                    "size": "m",
                    "priority": "urgent",
                    "target_on": "2027-02-05",
                },
                {
                    "key": "u",
                    "kind": "task",
                    "title": "Choose a supplier for the studded tyres",
                    "parent": "s",
                    "size": "s",
                },
            ],
            links=[
                {"source": "t", "target": "u", "link_type": "needs_first"},
                {"source": "t", "target": other["ref"], "link_type": "relates_to"},
            ],
            rules=[
                {
                    "title": "Every bike passes the grip test on wet ground",
                    "body": "Measured on the depot's test track.",
                    "node": "t",
                }
            ],
        )
        number, refs = await drafted(agent, epic["ref"], title="Winter tyres")
        person(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Name the tyres, size them again and drop the covers task.",
            comments=[{"item": refs["tyres"], "field": "title", "text": "Say which tyres."}],
        )
        items = revision(
            epic["ref"],
            refs,
            leave_out=("order",),
            tyres={"title": NEW_TITLE, "size": "m", "priority": "high"},
            fit={"body": NEW_BODY, "target_on": "2027-03-01"},
        )
        items.append(
            {
                "key": "measure",
                "kind": "task",
                "title": "Measure the stations",
                "parent": "covers",
                "size": "s",
            }
        )
        await ok(agent, "revise_plan", plan=number, items=items, note="Sized again.")
        misc = await make(agent, "epic", "Misc")
        loose = [
            await make(agent, "story", name, misc["ref"])
            for name in (
                "Refund screen for the rider's app",
                "Refund email for the riders",
                "Refund report for the finance team",
            )
        ]
        moved = await ok(
            agent,
            "propose_placement",
            items=[loose[0]["ref"], loose[1]["ref"]],
            parent=epic["ref"],
            reason="Refunds belong with the winter fleet.",
        )
        grouped = await ok(
            agent,
            "propose_placement",
            items=[loose[2]["ref"]],
            new_epic={"title": "Riders who ask for their money back", "body": "Refunds, all."},
            reason="Refunds are a theme of their own.",
        )
        story = await make(agent, "story", "Pay for a ride with a card", epic["ref"])
        task = await make(agent, "task", "Wire the webhook for the provider", story["ref"])
        await ok(agent, "claim_node", node=task["ref"])
        note = await ok(
            agent,
            "add_note",
            text="The webhook retries for a day.\nIt gives up after that.",
            why="Read it in the provider's documentation",
            nodes=[task["ref"]],
            decided_by="Priya Nair",
            decided_on="2026-11-20",
        )
        plain_note = await ok(
            agent,
            "add_note",
            text="The sandbox has no retries",
            why="Tried it twice",
            nodes=[task["ref"]],
        )
    return {
        "owner": owner,
        "project": project,
        "viewer": viewer,
        "epic": epic,
        "rich": int(rich["plan"]),
        "revised": number,
        "refs": refs,
        "moved": int(moved["id"]),
        "grouped": int(grouped["id"]),
        "loose": loose,
        "task": task,
        "note": int(note["id"]),
        "plain_note": int(plain_note["id"]),
    }


async def plan_of(project: Any, number: int, **more: Any) -> dict[str, Any]:
    async with connect(project.mcp_url, project.key) as agent:
        (found,) = (await ok(agent, "list_plans", plan=number, **more))["items"]
    return dict(found)


def lookup_of(*plans: dict[str, Any]) -> dict[str, str]:
    """The titles of the items a plan names and of the works around it: the first plan named has
    the say (a title a revision changed is the new one)."""
    found: dict[str, str] = {}
    for plan in plans:
        for one in [*plan["tree"], *plan.get("context", [])]:
            found.setdefault(one["ref"], one["title"])
    return found


async def titles_of(project: Any, refs: list[str]) -> dict[str, str]:
    async with connect(project.mcp_url, project.key) as agent:
        return {ref: (await ok(agent, "get_node", node=ref))["title"] for ref in refs}


async def each_way(
    page: Page, line: Locator, hows: tuple[str, ...] = ("click", "enter", "space")
) -> tuple[list[dict[str, Any]], list[str]]:
    """Open a line's dialog each way, close it with Escape and see the focus come back to the line.
    Returns what the dialog held each time and what went wrong in opening and closing."""
    seen: list[dict[str, Any]] = []
    wrong: list[str] = []
    for how in hows:
        await open_it(page, line, how)
        seen.append(await snapshot(page))
        if not await close_it(page, line):
            wrong.append(f"the focus did not come back to the line after {how}")
    return seen, wrong


async def added_lines_open_the_creation_layout(there: dict[str, Any]) -> None:
    project, number = there["project"], there["rich"]
    plan = await plan_of(project, number)
    lookup = lookup_of(plan)
    wrong: list[str] = []
    opened = 0
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        for index, item in enumerate(plan["tree"]):
            line = line_of(scope, "+", item["ref"])
            await expect(line).to_have_count(1, timeout=SLOW)
            hows = ("click", "enter", "space") if index == 0 else ("click",)
            seen, trouble = await each_way(page, line, hows)
            wrong += [f"{item['ref']}: {w_}" for w_ in trouble]
            for snap in seen:
                wrong += [f"{item['ref']}: {w_}" for w_ in wrong_item(snap, "added", item, lookup)]
                opened += 1
            if index == 0:
                # Every part of the story is there: the dates, the priority, the details.
                fields = [f[0] for f in seen[0]["fields"]]
                if fields != list(ORDER[:8]):
                    wrong.append(f"the story shows {fields}")
        # The task has links and a criterion, the criterion with its words.
        task = next(one for one in plan["tree"] if one["kind"] == "task" and one["links"])
        await open_it(page, line_of(scope, "+", task["ref"]), "click")
        said = await snapshot(page)
        kinds = {f[0] for f in said["fields"]}
        if not {"links", "criteria"} <= kinds:
            wrong.append(f"the task shows {sorted(kinds)}")
        await page.keyboard.press("Escape")
        # The link in the dialog goes to the work's own page and leaves the dialog behind.
        await open_it(page, line_of(scope, "+", plan["tree"][0]["ref"]), "click")
        await page.locator("a#change-open").click()
        await expect(page.locator("dialog#change-dialog")).to_have_count(0, timeout=SLOW)
        await expect(page.locator("#item-page")).to_be_visible(timeout=SLOW)
        at = await page.evaluate("location.hash")
        opens_the_page = f"item={plan['tree'][0]['ref']}" in at.replace("%3A", ":")
    proof(
        "M52",
        "on the review page of a drafted plan, pressing the green line of each item it adds, "
        "with the mouse and (for the first one) with Enter and with Space, opens the creation "
        "layout of that work (a New story or New task dialog, read only: the kind, the title, "
        "the details, the epic or story it goes in, the size, the priority, the start and target "
        "day, what it needs first and its acceptance criteria, each only when it has one), "
        "every value new and marked with a green plus, equal to what the plan lists for it, with "
        "an Open link to the work's own page that leaves the dialog behind, and Escape closes it "
        "with the focus back on the line",
        not wrong and opens_the_page and opened == len(plan["tree"]) + 2,
        f"{opened} dialogs for {len(plan['tree'])} items"
        + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def changed_and_removed_lines_show_old_red_and_new_green(there: dict[str, Any]) -> None:
    project, number = there["project"], there["revised"]
    plan = await plan_of(project, number)
    before = await plan_of(project, number, revision=1)
    lookup = lookup_of(plan, before)
    changes = {one["ref"]: one for one in plan["changes"]}
    by_ref = {one["ref"]: one for one in plan["tree"]}
    gone = {one["ref"]: one for one in before["tree"]}
    wrong: list[str] = []
    seen_changes: dict[str, list[str]] = {}
    first_snap: dict[str, tuple[Any, Any]] = {}
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{number}")
        scope = review_page(page)
        for ref, entry in changes.items():
            if entry["change"] == "changed":
                line = line_of(scope, "changed", ref)
                await expect(line).to_have_count(1, timeout=SLOW)
                seen, trouble = await each_way(page, line)
                wrong += [f"{ref}: {w_}" for w_ in trouble]
                for snap in seen:
                    wrong += [
                        f"{ref}: {w_}"
                        for w_ in wrong_item(snap, "changed", by_ref[ref], lookup, entry)
                    ]
                seen_changes[ref] = [f[0] for f in seen[0]["fields"] if f[1] == "changed"]
                first_snap[ref] = (seen[0]["mode"], seen[0]["fields"])
                # The lines of the parts that changed, red and green, open the same dialog.
                for sign in ("-", "+"):
                    parts = scope.locator(
                        f".diff-line.diff-openable[data-sign='{sign}'][data-ref='{ref}'][data-part]"
                    )
                    if await parts.count() < 1:
                        wrong.append(f"{ref}: no {sign} line of a part opens anything")
                        continue
                    await open_it(page, parts.first, "click")
                    again = await snapshot(page)
                    await page.keyboard.press("Escape")
                    if (again["mode"], again["fields"]) != (seen[0]["mode"], seen[0]["fields"]):
                        wrong.append(f"{ref}: a {sign} line of a part opens another dialog")
            elif entry["change"] == "removed":
                line = line_of(scope, "-", ref)
                await expect(line).to_have_count(1, timeout=SLOW)
                seen, trouble = await each_way(page, line)
                wrong += [f"{ref}: {w_}" for w_ in trouble]
                for snap in seen:
                    wrong += [
                        f"{ref}: {w_}" for w_ in wrong_item(snap, "removed", gone[ref], lookup)
                    ]
                seen_changes[ref] = ["removed"]
            else:
                line = line_of(scope, "+", ref)
                await expect(line).to_have_count(1, timeout=SLOW)
                await open_it(page, line, "click")
                wrong += [
                    f"{ref}: {w_}"
                    for w_ in wrong_item(await snapshot(page), "added", by_ref[ref], lookup)
                ]
                await page.keyboard.press("Escape")
                seen_changes[ref] = ["added"]
        # The Whole plan read shows every item as new, as it is now.
        await go(page, w.board, f"#/review?change=plan:{number}&mode=whole")
        await expect(review_page(page).locator(".diff-line.diff-openable").first).to_be_visible(
            timeout=SLOW
        )
        scope = review_page(page)
        first = plan["tree"][0]
        await open_it(page, line_of(scope, "+", first["ref"]), "click")
        wrong += [f"whole: {w_}" for w_ in wrong_item(await snapshot(page), "added", first, lookup)]
        await page.keyboard.press("Escape")
        # The Split read: the lines of the parts on each side open the same dialogs.
        await go(page, w.board, f"#/review?change=plan:{number}&view=split")
        scope = review_page(page)
        for ref, expected in first_snap.items():
            for sign in ("-", "+"):
                part = scope.locator(
                    f".diff-line.diff-openable[data-sign='{sign}'][data-ref='{ref}'][data-part]"
                )
                await expect(part.first).to_be_visible(timeout=SLOW)
                await open_it(page, part.first, "click")
                there_now = await snapshot(page)
                await page.keyboard.press("Escape")
                if (there_now["mode"], there_now["fields"]) != expected:
                    wrong.append(f"{ref}: in the split read a {sign} line opens another dialog")
    kinds = sorted(e["change"] for e in changes.values())
    several = [ref for ref, fields in seen_changes.items() if len(fields) >= 3]
    proof(
        "M52",
        "on the review page of a plan at revision 2, pressing the changed line of an item opens "
        "the edit layout of that work with every part the revision changed as two values (the "
        "old one red with a minus, the new one green with a plus: here the title, the size and "
        "the priority of the story, and the details and the target day of a task) and every "
        "other part plain with no sign, equal to what list_plans says now and what it says the "
        "parts were; pressing the red line of the item it took out opens the same layout with "
        "every value red, as revision 1 had it; pressing the green line of the item it added "
        "opens the creation layout; each opens with the mouse, Enter and Space, Escape brings "
        "the focus back, the lines of the changed parts (red and green, in the unified and the "
        "split read) open the very same dialog as the changed line, and the Whole plan read "
        "opens the item as it is now",
        not wrong and kinds == ["added", "changed", "changed", "removed"] and bool(several),
        f"{kinds}, changed parts {seen_changes}" + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def a_moved_item_shows_its_old_parent_and_its_new(there: dict[str, Any]) -> None:
    project = there["project"]
    async with connect(project.mcp_url, project.key) as agent:
        proposals = (await ok(agent, "list_placements", status="proposed", limit=50))["items"]
        first = next(p for p in proposals if p["id"] == there["moved"])
        grouped = next(p for p in proposals if p["id"] == there["grouped"])
        nodes = {
            ref: await ok(agent, "get_node", node=ref)
            for ref in [i["ref"] for i in first["items"]] + [i["ref"] for i in grouped["items"]]
        }
    titles = await titles_of(project, [n["parent"] for n in nodes.values()] + [first["parent"]])
    wrong: list[str] = []
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"placement:{there['moved']}")
        scope = review_page(page)
        for index, one in enumerate(first["items"]):
            node = nodes[one["ref"]]
            old, new = node["parent"], first["parent"]
            for sign in ("+", "-"):
                line = line_of(scope, sign, one["ref"])
                if await line.count() != 1:
                    wrong.append(f"{one['ref']} has no {sign} line")
                    continue
                hows = ("click", "enter", "space") if index == 0 and sign == "+" else ("click",)
                seen, trouble = await each_way(page, line, hows)
                wrong += [f"{one['ref']}: {w_}" for w_ in trouble]
                for snap in seen:
                    fields = {f[0]: f for f in snap["fields"]}
                    parent = fields.get("parent")
                    if (snap["mode"], snap["ref"]) != ("moved", one["ref"]):
                        wrong.append(f"{snap['mode']} {snap['ref']}")
                    if parent is None or parent[1] != "changed":
                        wrong.append(f"{one['ref']}: the parent is not changed")
                        continue
                    (minus, was), (plus, now) = parent[2]
                    if (minus, plus) != ("-", "+"):
                        wrong.append(f"{one['ref']}: signs {minus} {plus}")
                    if code_of(old) not in was or titles[old].lower() not in was.lower():
                        wrong.append(f"{one['ref']}: the old parent is not {old}: {was}")
                    if code_of(new) not in now or titles[new].lower() not in now.lower():
                        wrong.append(f"{one['ref']}: the new parent is not {new}: {now}")
                    others = [f for f in snap["fields"] if f[0] != "parent"]
                    if any(f[1] != "same" or f[2][0][0] is not None for f in others):
                        wrong.append(f"{one['ref']}: another part is not plain")
                    title = fields.get("title")
                    if title is None or node["title"] not in title[2][0][1]:
                        wrong.append(f"{one['ref']}: the title is not {node['title']}")
                    if snap["buttons"] != ["Close"] or snap["controls"] or snap["said"]:
                        wrong.append(f"{one['ref']}: controls {snap['buttons']} {snap['said']}")
        # A move into an epic that does not exist yet: the epic it would make opens as a new epic.
        await open_review(w, f"placement:{there['grouped']}")
        scope = review_page(page)
        epic_line = scope.locator(".diff-line.diff-openable:has(.diff-new-epic)")
        await expect(epic_line).to_have_count(1, timeout=SLOW)
        await open_it(page, epic_line, "click")
        made = await snapshot(page)
        epic_title = next((f[2][0][1] for f in made["fields"] if f[0] == "title"), "")
        epic_fields = [f[0] for f in made["fields"]]
        if (made["mode"], made["kind"]) != ("added", "epic") or grouped["new_epic"][
            "title"
        ] != epic_title:
            wrong.append(f"the new epic opens as {made['mode']} {made['kind']} {epic_title}")
        if epic_fields[:3] != ["kind", "title", "body"] or made["ref"]:
            wrong.append(f"the new epic shows {epic_fields} {made['ref']}")
        await page.keyboard.press("Escape")
        moved = grouped["items"][0]["ref"]
        await open_it(page, line_of(scope, "+", moved), "click")
        under = {f[0]: f for f in (await snapshot(page))["fields"]}
        if "Riders who ask for their money back" not in under["parent"][2][-1][1]:
            wrong.append(f"{moved} moves under {under['parent'][2][-1][1]}")
        await page.keyboard.press("Escape")
    proof(
        "M52",
        "on the review page of a suggested move, pressing the line of a story it moves, under the "
        "new parent (green) or where it is now (red), with the mouse and with Enter and Space, "
        "opens the edit layout of that story with its parent as two values, the old one red and "
        "the one the move would leave it under green, and every other part plain, as get_node "
        "and the proposal say; the epic a move would make opens as a new epic, and a story moved "
        "under it names it as the new parent",
        not wrong and len(first["items"]) == 2,
        f"{len(first['items'])} stories moved, 1 epic made"
        + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def a_note_opens_as_a_record(there: dict[str, Any]) -> None:
    project, task = there["project"], there["task"]
    async with connect(project.mcp_url, project.key) as agent:
        notes = [
            await ok(agent, "get_note", note_id=there["note"]),
            await ok(agent, "get_note", note_id=there["plain_note"]),
        ]
    wrong: list[str] = []
    opened = 0
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await go(page, w.board, "#/needs")
        row = page.locator(f"li.needs-row[data-change='note:{task['ref']}']")
        await expect(row.locator(".preview-note-line.diff-openable")).to_have_count(2, timeout=SLOW)
        for index, note in enumerate(notes):
            line = row.locator(".preview-note-line.diff-openable").nth(index)
            hows = ("click", "enter", "space") if index == 0 else ("click",)
            seen, trouble = await each_way(page, line, hows)
            wrong += trouble
            for snap in seen:
                opened += 1
                fields = {f[0]: f for f in snap["fields"]}
                wanted = (
                    ["text", "why", "decided", "concerns"]
                    if note["decided_by"]
                    else [
                        "text",
                        "why",
                        "concerns",
                    ]
                )
                if (snap["mode"], snap["kind"], snap["ref"]) != (
                    "added",
                    "note",
                    f"note:{note['id']}",
                ):
                    wrong.append(f"{snap['mode']} {snap['kind']} {snap['ref']}")
                if [f[0] for f in snap["fields"]] != wanted:
                    wrong.append(f"fields {[f[0] for f in snap['fields']]}")
                    continue
                if any(f[1] != "new" or [v[0] for v in f[2]] != ["+"] for f in snap["fields"]):
                    wrong.append("a value of the note is not new")
                if squash(note["text"]) not in squash(fields["text"][2][0][1]):
                    wrong.append("the text")
                if note["why"] not in fields["why"][2][0][1]:
                    wrong.append("the why")
                if note["decided_by"]:
                    decided = fields["decided"][2][0][1]
                    if note["decided_by"] not in decided or note["decided_on"] not in decided:
                        wrong.append(f"decided by {decided}")
                if code_of(task["ref"]) not in fields["concerns"][2][0][1]:
                    wrong.append("what it applies to")
                if snap["buttons"] != ["Close"] or snap["controls"] or snap["said"]:
                    wrong.append(f"controls {snap['buttons']} {snap['said']}")
                if f"item=note:{note['id']}" not in str(snap["open"]):
                    wrong.append(f"the link goes to {snap['open']}")
    proof(
        "M52",
        "on the list, pressing the line of a note waiting for confirmation (with the mouse, "
        "Enter or Space) opens the layout of a recorded note, read only: what is known or "
        "decided, why, who decided it and when (only when somebody did), and what it applies "
        "to, each new and marked with a green plus, equal to what get_note says, with an Open "
        "link to the note's own page",
        not wrong and opened == 4,
        f"{opened} dialogs" + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


def hue_of(colour: str) -> str:
    """Which of red or green a background mostly is, as 'red', 'green' or 'other'."""
    numbers = [float(n) for n in re.findall(r"[\d.]+", colour)[:3]]
    if len(numbers) < 3:
        return "other"
    red, green, blue = numbers
    if red > green and red > blue:
        return "red"
    if green > red and green > blue:
        return "green"
    return "other"


async def new_is_green_and_old_is_red_in_both_themes(there: dict[str, Any]) -> None:
    project, number = there["project"], there["revised"]
    plan = await plan_of(project, number)
    changed = next(e["ref"] for e in plan["changes"] if e["change"] == "changed")
    found: dict[str, list[str]] = {}
    same: list[str] = []
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{number}")
        for theme in ("light", "dark"):
            await page.evaluate(f"localStorage.setItem('vrum.theme', '{theme}')")
            await page.reload()
            line = line_of(review_page(page), "changed", changed)
            await expect(line).to_be_visible(timeout=SLOW)
            await open_it(page, line, "click")
            plus, minus = await page.evaluate(COLOURS, "+"), await page.evaluate(COLOURS, "-")
            found[theme] = [hue_of(plus[0] or ""), hue_of(minus[0] or "")]
            # The same colours as the lines of the diff behind the dialog.
            if plus[0] != plus[1] or minus[0] != minus[1]:
                same.append(f"{theme} words")
            if plus[2] != plus[3] or minus[2] != minus[3]:
                same.append(f"{theme} gutter")
            await page.keyboard.press("Escape")
        await page.evaluate("localStorage.removeItem('vrum.theme')")
    proof(
        "M52",
        "in the dialog of a changed line a new value is green and an old value is red in the "
        "light and the dark theme, in the very colours of the green and red lines of the diff "
        "behind it, and a plus and a minus say it too, so that colour is never the only clue",
        all(v == ["green", "red"] for v in found.values()) and not same,
        f"{found}" + (f"; not the diff's colours in {same}" if same else ""),
    )


async def a_viewer_can_open_every_kind_and_nothing_can_be_written(there: dict[str, Any]) -> None:
    project, viewer = there["project"], there["viewer"]
    plan = await plan_of(project, there["revised"])
    rich = await plan_of(project, there["rich"])
    changed = next(e["ref"] for e in plan["changes"] if e["change"] == "changed")
    moved = there["loose"][0]["ref"]
    targets = [
        (f"plan:{there['rich']}", "+", rich["tree"][0]["ref"], ".diff-line.diff-openable"),
        (f"plan:{there['revised']}", "changed", changed, ".diff-line.diff-openable"),
        (f"placement:{there['moved']}", "+", moved, ".diff-line.diff-openable"),
        (f"note:{there['task']['ref']}", "", f"note:{there['note']}", ".preview-note-line"),
    ]
    by: dict[str, list[list[Any]]] = {"owner": [], "viewer": []}
    kinds: list[tuple[str, dict[str, Any]]] = [
        ("owner", {"token": project.key}),
        ("viewer", {"token": None, "person": viewer}),
    ]
    for name, kwargs in kinds:
        async with browser_page(project.address, name=None, **kwargs) as w:
            page = w.page
            for change, sign, ref, selector in targets:
                if sign:
                    await open_review(w, change)
                    line = line_of(review_page(page), sign, ref)
                else:
                    # A note is a line on the list, under its row.
                    await go(page, w.board, "#/needs")
                    row = page.locator(f"li.needs-row[data-change='{change}']")
                    line = row.locator(f"{selector}.diff-openable").first
                await expect(line).to_be_visible(timeout=SLOW)
                await open_it(page, line, "enter")
                snap = await snapshot(page)
                by[name].append([snap["mode"], snap["fields"], snap["buttons"], snap["controls"]])
                if snap["said"]:
                    by[name].append(["sentences", snap["said"]])
                await page.keyboard.press("Escape")
    proof(
        "M52",
        "a viewer opens the dialog of a line of every kind (an item a plan adds, an item a "
        "revision changes, an item a move moves, a note) with the keyboard and sees exactly what "
        "the owner sees, and the dialog has no control that writes anything for either of them "
        "(only Close, and a link to the work's page), and no text of six words or more that is "
        "not somebody's own words",
        by["owner"] == by["viewer"]
        and len(by["viewer"]) == 4
        and all(entry[2] == ["Close"] and entry[3] == 0 for entry in by["viewer"]),
        f"{len(by['viewer'])} dialogs, owner and viewer the same: {by['owner'] == by['viewer']}"
        + "".join(f"; said: {clip(str(e[1]), 200)}" for e in by["viewer"] if e[0] == "sentences"),
    )


async def the_dialog_fits_a_narrow_window(there: dict[str, Any]) -> None:
    project = there["project"]
    plan = await plan_of(project, there["revised"])
    changed = next(e["ref"] for e in plan["changes"] if e["change"] == "changed")
    fits: list[dict[str, Any]] = []
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await page.set_viewport_size(NARROW)  # type: ignore[arg-type]
        for change, sign, ref in (
            (f"plan:{there['revised']}", "changed", changed),
            (
                f"plan:{there['rich']}",
                "+",
                (await plan_of(project, there["rich"]))["tree"][0]["ref"],
            ),
        ):
            await open_review(w, change)
            line = line_of(review_page(page), sign, ref)
            await expect(line).to_be_visible(timeout=SLOW)
            await open_it(page, line, "click")
            fits.append(await page.evaluate(FITS))
            await page.locator("#change-close").click()
            await expect(page.locator("dialog#change-dialog")).to_have_count(0, timeout=SLOW)
        await page.set_viewport_size(WIDE)  # type: ignore[arg-type]
    ok_fit = all(
        f["left"] >= 0
        and f["right"] <= f["width"]
        and not f["sideways"]
        and f["within"]
        and not f["clipped"]
        for f in fits
    )
    proof(
        "M52",
        "at 390 pixels wide the dialog of a changed line and of an added line is inside the "
        "window, with no sideways scroll of the page or of the dialog, and its Close button can "
        "be pressed",
        ok_fit,
        clip(str(fits), 200),
    )


async def a_line_on_the_list_opens_what_the_same_line_on_the_page_opens(
    there: dict[str, Any],
) -> None:
    project = there["project"]
    plan = await plan_of(project, there["rich"])
    lookup = lookup_of(plan)
    async with connect(project.mcp_url, project.key) as agent:
        first = next(
            p
            for p in (await ok(agent, "list_placements", status="proposed", limit=50))["items"]
            if p["id"] == there["moved"]
        )
    wrong: list[str] = []
    listed: dict[str, list[Any]] = {}
    opened = 0
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await go(page, w.board, "#/needs")
        row = page.locator(f"li.needs-row[data-change='plan:{there['rich']}']")
        for index, item in enumerate(plan["tree"]):
            line = row.locator(
                f".change-preview .diff-line.diff-openable[data-sign='+'][data-ref='{item['ref']}']"
                ":not([data-part])"
            )
            await expect(line).to_have_count(1, timeout=SLOW)
            hows = ("click", "enter") if index == 0 else ("click",)
            seen, trouble = await each_way(page, line, hows)
            wrong += [f"{item['ref']}: {w_}" for w_ in trouble]
            for snap in seen:
                opened += 1
                wrong += [f"{item['ref']}: {w_}" for w_ in wrong_item(snap, "added", item, lookup)]
        move = page.locator(f"li.needs-row[data-change='placement:{there['moved']}']")
        for one in first["items"]:
            line = move.locator(
                f".change-preview .diff-line.diff-openable[data-sign='+'][data-ref='{one['ref']}']"
            )
            await expect(line).to_have_count(1, timeout=SLOW)
            seen, trouble = await each_way(page, line, ("click", "space"))
            wrong += trouble
            opened += len(seen)
            listed[one["ref"]] = [seen[0]["mode"], seen[0]["fields"]]
        await open_review(w, f"placement:{there['moved']}")
        for one in first["items"]:
            line = line_of(review_page(page), "+", one["ref"])
            await open_it(page, line, "click")
            snap = await snapshot(page)
            await page.keyboard.press("Escape")
            if listed[one["ref"]] != [snap["mode"], snap["fields"]]:
                wrong.append(f"{one['ref']}: the list and the page differ")
    proof(
        "M52",
        "on the list, the preview of a drafted plan's card and of a suggested move's card has "
        "green lines that open the same dialogs: each item the plan adds opens its creation "
        "layout, equal to what the plan lists for it, and each story the move moves opens its "
        "edit layout with the parent old and new, word for word what the same line on the "
        "review page opens, with the mouse, Enter or Space",
        not wrong and opened == len(plan["tree"]) + 1 + 2 * len(first["items"]),
        f"{opened} dialogs" + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def standard_changes() -> None:
    there = await what_there_is()
    await added_lines_open_the_creation_layout(there)
    await changed_and_removed_lines_show_old_red_and_new_green(there)
    await a_moved_item_shows_its_old_parent_and_its_new(there)
    await a_note_opens_as_a_record(there)
    await a_line_on_the_list_opens_what_the_same_line_on_the_page_opens(there)
    await new_is_green_and_old_is_red_in_both_themes(there)
    await a_viewer_can_open_every_kind_and_nothing_can_be_written(there)
    await the_dialog_fits_a_narrow_window(there)
