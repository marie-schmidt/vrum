"""Chapter 15 of the acceptance scenario: the redesigned workbench in light and dark (M17), driven
in a real headless Chromium against the real containers, with what the page shows compared with what
a real MCP client says. Registration, sign-in and the project list are in the new design; Light,
Dark and System are chosen before and after signing in, kept across a reload and signing out,
applied before the first paint, and only System follows the operating system; switching appearance
keeps a filter, a selection and a draft; no screen scrolls sideways at 390, 768, 1280 and 1600 CSS
pixels or at 200% zoom, in either theme; cards, progress bars and icons are what the records say;
the keyboard does the work with a visible focus and reduced motion stops the transitions; and a
lost connection is told and then forgiven. Every proof reads values from the page; none is a
picture.

The chapter makes its own account, project and agent key, so it needs nothing from the chapters
before it. Set VRUM_LAYOUT_SHOTS to a folder outside the repository to also keep screenshots of the
Board and of the Graph with its detail panel in both themes, to look at."""

import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Error as BrowserError
from playwright.async_api import Page, expect

from tests.acceptance_scenario import Failed, chapter, clip, proof
from tests.board_helpers import SLOW, Watched, hold_back_news, item_address, made
from tests.board_layout import (
    LONG_SOURCE,
    LONG_TITLE,
    OLD_GLYPHS,
    SCREENS,
    UNBROKEN,
    Screen,
    Stop,
    focus_ring,
    focused,
    frames,
    layout_context,
    open_address,
    settle,
    shoot,
    start_of_page,
    tab_to,
    visit,
)
from tests.helpers import (
    REPO_ROOT,
    all_nodes,
    code_of,
    confirm,
    connect,
    day,
    make_decision,
    make_fact,
    ok,
    set_status,
    unique_word,
)
from tests.world import Project, make_project, register, server_base

# board_theme reads the board's design tokens from the source tree. A checkout whose editable
# install cannot be imported still runs, as it does under pytest (`pythonpath` in pyproject.toml).
sys.path.append(str(REPO_ROOT / "src"))

from tests.board_theme import (  # noqa: E402
    CHOICES,
    FIRST_PAINT,
    POLICY_WATCH,
    THEMES,
    api_requests,
    at,
    choose,
    contrast,
    entry,
    foreign_colours,
    in_project,
    look,
    os_prefers,
    policy_violations,
    refusal_look,
    rgb_of,
    settled,
    stored_in_browser,
    survey,
    themed_page,
    token,
    transitions_done,
)

PASSWORD = "a long enough password"
# What the board may ask the server while only the appearance changes: its own poll.
POLL = {"tool/changes_since", "index"}


async def appearance() -> None:
    chapter("Appearance: the redesigned workbench, in light and dark, at every size")
    base = server_base()
    world = await build_world()
    try:
        await accounts_in_the_new_design(base)
        await first_paint(base, world)
        await system_follows_only_when_chosen(world)
        await switching_keeps_what_was_typed(world)
        await no_screen_scrolls_sideways(world)
        await cards_progress_and_icons(world)
        await the_keyboard_and_reduced_motion(world)
        await losing_the_server(world)
    except BrowserError as error:
        raise Failed(
            f"M17: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- a project of its own, with records of every length


@dataclass
class World:
    """An account, a project and its agent key, made as a person would, and the references of the
    work made through MCP by short name (task:12), and the ids of a decision and a fact."""

    project: Project
    ids: dict[str, Any] = field(default_factory=dict)

    @property
    def board(self) -> str:
        return self.project.address

    def __getitem__(self, name: str) -> Any:
        return self.ids[name]


async def build_world() -> World:
    """Work of every length and context linked to it: an epic that is dense in one column and
    sparse in another, a small epic with two of its five tasks done, an epic of long words, dates,
    a dependency, a requirement, a confirmed decision and a fact with a long source."""
    project = make_project(register(server_base(), "Appearance tester"), "Appearance walk")
    world = World(project)
    ids = world.ids
    async with connect(project.mcp_url, project.key) as client:

        async def node(
            name: str, kind: str, title: str, parent: str | None = None, **more: Any
        ) -> None:
            ids[name] = await made(client, kind, title, ids[parent] if parent else None, **more)

        async def status(name: str, to: str) -> None:
            await set_status(client, {"ref": ids[name]}, to)

        await node("E1", "epic", "Run and see it all")
        await node("S1", "story", "Story one", "E1")
        for number in range(1, 6):
            await node(f"A{number}", "task", f"Same card 0{number}", "S1")
        await node("A6", "task", "Same card 06", "S1")
        await status("A6", "in_progress")
        await node("S2", "story", "A readable activity feed", "E1", priority="high")
        await node("B1", "task", "Keep the feed current", "S2", start_on=day(2), target_on=day(9))
        await node("B2", "task", "Show expiring claims", "S2", start_on=day(5), target_on=day(14))

        await node("E2", "epic", "A small epic")
        await node("S3", "story", "Wrap-up", "E2")
        for number in range(1, 6):
            await node(f"W{number}", "task", f"Wrap-up task {number}", "S3")
        await status("W1", "done")
        await status("W2", "done")
        await status("W3", "in_progress")
        await status("W4", "blocked")

        await node("E3", "epic", LONG_TITLE)
        await node("S5", "story", LONG_TITLE, "E3")
        await node("D1", "task", LONG_TITLE, "S5", priority="urgent", target_on=day(-30))
        await node("D2", "task", UNBROKEN, "S5", start_on=day(3), target_on=day(8))
        await node("D3", "task", "Waits for the long one", "S5")
        await ok(
            client,
            "add_link",
            actor="agent-1",
            source=ids["D3"],
            target=ids["D1"],
            link_type="needs_first",
        )

        rule = await ok(
            client, "create_rule", actor="ann", title="Keep who, when and why", story=ids["S1"]
        )
        decision = await make_decision(
            client,
            "Claims expire; work stays visible",
            text="A claim has a lease that expires. At most one actor holds a claim at a time.",
            reason="Coordinate work without a permanent lock when an agent stops.",
            nodes=[ids["S1"]],
            rule_ids=[rule["id"]],
        )
        ids["X1"] = decision["id"]
        await confirm(client, "decision", decision)
        fact = await make_fact(
            client, "The team call agreed the lease is ten minutes", nodes=[ids["S1"]]
        )
        ids["Y1"] = fact["id"]
        await make_fact(client, f"{UNBROKEN} {LONG_TITLE}", source=LONG_SOURCE, nodes=[ids["D1"]])
    return world


# -- the sign-in page, registration and the project list are in the new design


async def painted_in(page: Page, theme: str) -> tuple[int, list[str]]:
    """Choose a theme with the control a person sees, wait until the page has its colours, and read
    every colour the page is painted in: how many, and which are not a token of that theme."""
    await choose(page, theme)
    await settled(page, theme, theme)
    await transitions_done(page)
    found = await survey(page)
    return len(found), foreign_colours(found, theme)


async def secrets_held(page: Page, secrets: list[str]) -> list[str]:
    """What this browser keeps for the page besides the appearance and the name changes are
    recorded with, and whether any secret is among it."""
    kept = await stored_in_browser(page)
    odd = [name for name in kept["local"] if name not in {"vrum.theme", "vrum.actor"}]
    leaks = [secret[:4] for secret in secrets if secret and secret in json.dumps(kept)]
    return odd + leaks + (["session storage"] if kept["session"] else [])


async def accounts_in_the_new_design(base: str) -> None:
    word = unique_word()
    email, name = f"{word}@example.test", f"Ada {word}"
    looks: dict[str, tuple[int, list[str]]] = {}
    held: list[str] = []
    async with themed_page(f"{base}/board", signed_in=False, scheme="light", name=None) as w:
        page = w.page
        await entry(page, f"{base}/board")
        group = page.get_by_role("radiogroup", name="Appearance")
        before_signing_in = (
            await group.count() == 1
            and await group.locator("label").all_inner_texts() == ["Light", "Dark", "System"]
            and await group.get_by_role("radio").count() == 3
        )
        no_choice_yet = (await stored_in_browser(page))["local"] == {}
        for theme in THEMES:
            looks[f"the sign-in page in {theme}"] = await painted_in(page, theme)
        await page.locator("#show-register").click()
        await expect(page.locator("#register-form")).to_be_visible(timeout=SLOW)
        for theme in THEMES:
            looks[f"registration in {theme}"] = await painted_in(page, theme)
        await page.locator("#account-name").fill(name)
        await page.locator("#account-email").fill(email)
        await page.locator("#account-password").fill(PASSWORD)
        await page.locator("#register-submit").click()
        await expect(page.locator("#no-projects")).to_be_visible(timeout=SLOW)
        for theme in THEMES:
            looks[f"the empty project list in {theme}"] = await painted_in(page, theme)
        await page.locator("#project-name").fill(f"Compiler {word}")
        await page.locator("#create-project").click()
        await page.wait_for_selector("#app[data-shell=project]", timeout=SLOW)
        cookies = {each["name"]: each["value"] for each in await page.context.cookies()}
        session = await page.evaluate("fetch('/board/api/session').then((r) => r.json())")
        secrets = [cookies.get("vrum_session", ""), session["csrf"], PASSWORD]
        await choose(page, "dark")
        await settled(page, "dark", "dark")
        held += await secrets_held(page, secrets)

        # Signing out keeps the choice, and keeps nothing else.
        await page.locator("#sign-out").click()
        await page.wait_for_selector(".entry", timeout=SLOW)
        await settled(page, "dark", "dark")
        after_sign_out = await stored_in_browser(page)
        held += await secrets_held(page, secrets)
        await page.reload()
        await page.wait_for_selector(".entry", timeout=SLOW)
        await settled(page, "dark", "dark")
        await page.locator("#account-email").fill(email)
        await page.locator("#account-password").fill(PASSWORD)
        await page.locator("#sign-in-submit").click()
        await expect(page.locator("li.project")).to_have_count(1, timeout=SLOW)
        await settled(page, "dark", "dark")
        listed_in_dark = await page.locator("#top [data-appearance=dark]").is_checked()
        for theme in ("dark", "light"):
            looks[f"the project list in {theme}"] = await painted_in(page, theme)
        held += await secrets_held(page, secrets)
        problems = list(w.problems)
        outside = [url for url in w.requests if not url.startswith(base)]
        shown = await policy_violations(page)
    colours = sum(count for count, _ in looks.values())
    foreign = [f"{where}: {wrong[0]}" for where, (_, wrong) in looks.items() if wrong]
    proof(
        "M17",
        "the sign-in page, registration and the project list, made by really registering, are "
        "painted from the design tokens of the chosen theme, in Light and in Dark, with no colour "
        "of their own",
        not foreign and len(looks) == 8 and colours > 40,
        clip("; ".join(foreign), 160) or f"{colours} colours on {len(looks)} screens",
    )
    proof(
        "M17",
        "the appearance control (Light, Dark, System) is on the sign-in page before anyone signs "
        "in; the choice is kept across a reload, signing out and signing in again, and what the "
        "browser keeps for it is only that choice and the name changes are recorded with: no "
        "password, session or key",
        before_signing_in
        and no_choice_yet
        and after_sign_out["local"].get("vrum.theme") == "dark"
        and after_sign_out["cookies"] == []
        and listed_in_dark
        and not held
        and not problems
        and not outside
        and shown == [],
        clip("; ".join(held + problems + outside), 120) or "stored: vrum.theme = dark",
    )


# -- the first frame


def wrong_first_frame(record: dict[str, Any], resolved: str) -> str:
    """What was wrong with a page's first moments, or an empty text when nothing was: the document
    said `resolved` before anything was painted and never changed its mind, and the colours it was
    first drawn in are that theme's."""
    paints = [each["time"] for each in record["paints"]]
    changes = record["changes"][1:]  # the first is the page file itself, which says light
    if not paints or not changes:
        return "nothing was recorded"
    if [each["theme"] for each in changes] != [resolved] * len(changes):
        return f"the page said {[each['theme'] for each in changes]} on its way to {resolved}"
    if any(each["time"] >= min(paints) for each in changes):
        return "the theme was set after the first paint"
    for moment in (record["frame"], record["loaded"]):
        if moment is None or moment["theme"] != resolved or moment["scheme"] != resolved:
            return f"the first frame was {moment and moment['theme']}, not {resolved}"
        background = rgb_of(moment["bodyBackground"])
        if background != token(resolved, "--bg"):
            return f"the first frame had the background {background}, not {resolved}'s"
    return ""


async def first_paint(base: str, world: World) -> None:
    wrong: list[str] = []
    loads = 0
    violations: list[Any] = []
    for signed_in in (False, True):
        address = world.board if signed_in else f"{base}/board"
        for system, saved in (
            ("light", "dark"),
            ("dark", "light"),
            ("dark", None),
            ("light", None),
        ):
            resolved = saved or system
            async with themed_page(
                address,
                signed_in=signed_in,
                scheme=system,
                name=None,
                scripts=(FIRST_PAINT, POLICY_WATCH),
            ) as w:
                page = w.page
                await at(page, signed_in, address)
                if saved:
                    await choose(page, saved)
                    await page.reload()
                    await page.wait_for_selector("#app[data-shell=project], .entry", timeout=SLOW)
                problem = wrong_first_frame(await page.evaluate("window.__first"), resolved)
                if problem:
                    where = "signed in" if signed_in else "signed out"
                    wrong.append(f"{where}, system {system}, saved {saved}: {problem}")
                loads += 1
                violations += await policy_violations(page)
    proof(
        "M17",
        "after a reload with Dark saved, with Light saved, or with nothing saved and the system "
        "dark or light, signed out and signed in, the very first paint is already in the resolved "
        "theme: the document had it before anything was drawn, with that theme's background and "
        "no frame in the other",
        not wrong and loads == 8,
        clip("; ".join(wrong), 160) or f"{loads} loads",
    )
    with urllib.request.urlopen(f"{base}/board", timeout=30) as response:
        policy = response.headers["Content-Security-Policy"]
        page_file = response.read().decode()
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", page_file)
    proof(
        "M17",
        "the theme is applied with no content-security-policy exception: the policy allows scripts "
        "and styles only from the board itself, the page has no inline script, the theme script is "
        "a file, and no page broke the policy",
        "script-src 'self'" in policy
        and "style-src 'self'" in policy
        and "unsafe" not in policy
        and not inline
        and '<script src="/board/theme.js"></script>' in page_file
        and violations == [],
        policy.split(";")[1].strip(),
    )


# -- the operating system, and what switching keeps


def resolved_theme(choice: str, system: str) -> str:
    return system if choice == "system" else choice


async def system_follows_only_when_chosen(world: World) -> None:
    seen: list[str] = []
    wrong: list[str] = []
    async with themed_page(world.board, signed_in=True, scheme="light", name=None) as w:
        page = w.page
        await in_project(page, f"{world.board}#/board")
        for choice in CHOICES:
            await choose(page, choice)
            await settled(page, resolved_theme(choice, "light"), choice)
            for system in ("dark", "light", "dark"):
                await os_prefers(page, system)
                state = await look(page)
                seen.append(f"{choice}/{system}->{state['theme']}")
                if state["os"] != system or state["theme"] != resolved_theme(choice, system):
                    wrong.append(f"{choice} with the system {system} showed {state['theme']}")
            await os_prefers(page, "light")
    proof(
        "M17",
        "while a project's Board is open the operating system changes to dark, back to light and "
        "to dark again: Light and Dark do not move, System follows every change",
        not wrong and len(seen) == 9,
        clip("; ".join(wrong), 140) or ", ".join(seen[:3] + seen[-3:]),
    )


STATE = """() => {
  const typed = (id) => document.getElementById(id);
  const own = (id) => [typed(id).value, typed(id).__same];
  return {
    hash: location.hash, history: history.length, same: window.__same,
    filter: own('filter'), title: own('edit-title'), body: own('edit-body'),
    caret: [typed('edit-body').selectionStart, typed('edit-body').selectionEnd],
    selected: [...document.querySelectorAll('.card.selected')].map((card) => card.dataset.ref),
    cards: document.querySelectorAll('.card').length,
    panel: document.getElementById('panel').firstElementChild === window.__panel,
  };
}"""


async def switching_keeps_what_was_typed(world: World) -> None:
    task = world["A2"]
    async with connect(world.project.mcp_url, world.project.key) as client:
        version = (await ok(client, "get_node", node=task))["version"]
        async with themed_page(world.board, signed_in=True, scheme="light") as w:
            page = w.page
            await in_project(page, f"{world.board}#/board?q=kind:task&sel={task}")
            await expect(page.locator("#card-title")).to_contain_text(
                f"{code_of(task)} ", timeout=SLOW
            )
            await expect(page.locator(f".card.selected[data-ref='{task}']")).to_have_count(1)
            await hold_back_news(page)
            await page.locator("#filter").fill("kind:task status:ready")
            await page.locator("#edit-title").fill("A draft title that is not saved")
            await page.locator("#edit-body").fill("Draft line one\nDraft line two")
            await page.locator("#edit-body").focus()
            await page.evaluate(
                """() => {
                    window.__same = 'one document';
                    for (const id of ['filter', 'edit-title', 'edit-body']) {
                        document.getElementById(id).__same = id;
                    }
                    document.getElementById('edit-body').setSelectionRange(5, 9);
                    window.__panel = document.getElementById('panel').firstElementChild;
                }"""
            )
            before: dict[str, Any] = await page.evaluate(STATE)
            asked = len(w.requests)
            for choice in ("dark", "light", "system", "dark"):
                await choose(page, choice)
                await settled(page, resolved_theme(choice, "light"), choice)
            after: dict[str, Any] = await page.evaluate(STATE)
            saved_nothing = (await ok(client, "get_node", node=task))["version"] == version
            quiet = set(api_requests(w.requests[asked:])) <= POLL
    proof(
        "M17",
        "switching appearance four times while a filter is typed, a card is selected and a draft "
        "is in its text boxes leaves the filter, the selection, the draft (and the part of it that "
        "was selected) exactly as they were: the same page, the same boxes, no navigation, and "
        "nothing saved",
        after == before
        and before["filter"] == ["kind:task status:ready", "filter"]
        and before["title"] == ["A draft title that is not saved", "edit-title"]
        and before["selected"] == [task]
        and before["caret"] == [5, 9]
        and before["panel"]
        and saved_nothing
        and quiet,
        f"hash {after['hash'][:40]}",
    )


# -- every size


def views(world: World) -> list[Stop]:
    ready = "svg#graph-svg[data-ready=true]"
    rows = "#item-table tbody tr"
    return [
        Stop("the Board", "#/board", ".card[data-ref]", "board"),
        Stop("the Graph map", "#/graph?view=everything&as=map", ready, "graph"),
        Stop("the Graph list", "#/graph?view=everything&as=list", ".gv-record", "graph"),
        Stop("the Roadmap", "#/roadmap", ".rm-bar", "roadmap"),
        Stop(
            "an epic's page",
            item_address(world["E1"]),
            f"#item-page[data-ref='{world['E1']}'] {rows}",
            "item",
        ),
        Stop(
            "a story's page",
            item_address(world["S2"]),
            f"#item-page[data-ref='{world['S2']}'] {rows}",
            "item",
        ),
    ]


OVERFLOW = """() => ({
  doc: document.documentElement.scrollWidth, body: document.body.scrollWidth,
  width: window.innerWidth, scrolled: window.scrollX, ratio: window.devicePixelRatio,
  theme: document.documentElement.getAttribute('data-theme'),
})"""


async def reach_what_is_hidden(w: Watched, screen: Screen, world: World) -> list[str]:
    """What cannot be reached on this screen: the menu (behind its button on a narrow window) and
    the whole detail of a record with a very long title. The Board's columns scroll in their own
    region, which can be moved by its arrows while the page does not move."""
    page = w.page
    problems: list[str] = []
    await visit(w, views(world)[0])
    toggle, nav = page.locator("#sidebar-toggle"), page.locator("#main-nav")
    if screen.narrow:
        if await nav.is_visible() or not await toggle.is_visible():
            problems.append("the menu is not behind its button")
        await toggle.click()
        await expect(nav).to_be_visible()
        await frames(page)
        for place in ("board", "graph", "roadmap"):
            box = await nav.locator(f"a[data-page={place}]").bounding_box()
            if box is None or box["x"] < 0 or box["x"] + box["width"] > screen.width:
                problems.append(f"the {place} link is not inside the window")
        await page.keyboard.press("Escape")
        await expect(nav).to_be_hidden()
        if not (await focused(page)).startswith("button#sidebar-toggle"):
            problems.append("Escape did not give the focus back to the menu button")
        columns = await page.evaluate(
            """() => { const b = document.getElementById('board');
                return { name: b.getAttribute('aria-label'), role: b.getAttribute('role'),
                  tab: b.tabIndex, wider: b.scrollWidth > b.clientWidth + 1 }; }"""
        )
        if columns["role"] != "region" or not columns["name"] or columns["tab"] < 0:
            problems.append("the Board's columns are not a named region the keyboard can reach")
        if not columns["wider"]:
            problems.append("the columns did not need their own scroll region")
        await page.get_by_role("button", name="Scroll the columns right").click()
        await page.wait_for_function("() => document.getElementById('board').scrollLeft > 0")
        sideways = await page.evaluate(
            "() => [window.scrollX, document.documentElement.scrollWidth]"
        )
        if sideways[0] != 0 or sideways[1] > screen.width:
            problems.append(f"scrolling the columns moved the page: {sideways}")
    elif await toggle.is_visible() or not await nav.is_visible():
        problems.append("the menu should be open beside the page on a wide window")
    task = world["D1"]
    await page.locator(f".card[data-ref='{task}']").click()
    panel = page.locator("#panel")
    await expect(panel.locator(".detail")).to_be_visible(timeout=SLOW)
    await settle(page)
    box = await panel.bounding_box()
    heading = " ".join((await page.locator("#card-title").inner_text()).split())
    cut = await panel.evaluate(
        """(p) => { const t = p.querySelector('.detail-title');
            return t.scrollWidth > t.clientWidth + 1 || t.scrollHeight > t.clientHeight + 1; }"""
    )
    if box is None or box["x"] < -0.5 or box["x"] + box["width"] > screen.width + 0.5:
        problems.append(f"the detail panel is not inside the window: {box}")
    if screen.phone and box is not None and box["width"] < screen.width - 1:
        problems.append("on a phone the detail panel should fill the window")
    if LONG_TITLE not in heading or cut:
        problems.append("the long title is not shown whole in the detail panel")
    close = panel.locator(".detail-head .close")
    shut = await close.bounding_box()
    if shut is None or shut["y"] < 0 or shut["y"] + shut["height"] > screen.height:
        problems.append("the panel's close button is out of reach")
    await close.click()
    await expect(panel).to_be_hidden()
    return problems


async def no_screen_scrolls_sideways(world: World) -> None:
    visits = 0
    wide: list[str] = []
    stuck: list[str] = []
    for screen in SCREENS:
        for theme in THEMES:
            async with layout_context(world.board, screen, theme) as (w, _):
                await open_address(w, "#/board")
                for stop in views(world):
                    await visit(w, stop)
                    found: dict[str, Any] = await w.page.evaluate(OVERFLOW)
                    visits += 1
                    where = f"{stop.name} at {screen.name} in {theme}"
                    if (
                        found["theme"] != theme
                        or found["width"] != screen.width
                        or found["ratio"] != screen.scale
                    ):
                        wide.append(f"{where} was not a {screen.width} wide {theme} window")
                    if found["doc"] > found["width"] or found["scrolled"] != 0:
                        wide.append(f"{where}: the page is {found['doc']} wide in {found['width']}")
                stuck += [
                    f"{screen.name} in {theme}: {problem}"
                    for problem in await reach_what_is_hidden(w, screen, world)
                ]
                if screen.name == "1600":
                    chosen = f"#/graph?view=everything&as=map&sel=decision:{world['X1']}"
                    await visit(w, Stop("the Graph", chosen, "#panel .detail", "graph"))
                    await shoot(w.page, f"graph-panel-{theme}")
    proof(
        "M17",
        "at 390, 768, 1280 and 1600 CSS pixels wide, and at 200% zoom (640 CSS pixels at twice the "
        "pixels), the Board, the Graph's map and list, the Roadmap and the full page of an epic "
        "and of a story (with its table of what is under it) never make the page scroll "
        "sideways, in Light and in Dark",
        visits == 60 and not wide,
        clip("; ".join(wide), 160) or f"{visits} screens measured",
    )
    proof(
        "M17",
        "on a narrow window the menu is reached through its button and closes with Escape, the "
        "Board's columns scroll in a named region of their own that its arrow buttons move while "
        "the page stays put, and the detail panel of a record with a 200 character title opens "
        "inside the window with the whole title and a close button in reach (on a wide window the "
        "menu is simply there)",
        not stuck,
        clip("; ".join(stuck), 160) or "390, 768, 640 (200% zoom), 1280 and 1600 wide",
    )


# -- cards, progress and icons

CELLS = """() => [...document.querySelectorAll('.lane .cell')].map((cell) => ({
  epic: cell.dataset.epic, status: cell.dataset.status,
  height: cell.getBoundingClientRect().height, right: cell.getBoundingClientRect().right,
  cards: [...cell.querySelectorAll('.card')].map((card) => {
    const title = card.querySelector('.card-title');
    const box = card.getBoundingClientRect();
    return { ref: card.dataset.ref, height: box.height, right: box.right,
      text: title.textContent,
      clipped: title.scrollHeight > title.clientHeight + 1
        || title.scrollWidth > title.clientWidth + 1 };
  }),
}))"""

METERS = """() => [...document.querySelectorAll('.meter')]
  .filter((m) => m.checkVisibility()).map((m) => {
    const owner = m.closest('.lane, .card');
    const track = m.getBoundingClientRect();
    const fill = m.firstElementChild.getBoundingClientRect();
    const words = m.parentElement.querySelector('.progress-text')
      || m.parentElement.firstElementChild;
    return { kind: owner.classList.contains('lane') ? 'lane' : 'card',
      ref: owner.dataset.ref, text: words.textContent,
      now: Number(m.getAttribute('aria-valuenow')), max: Number(m.getAttribute('aria-valuemax')),
      role: m.getAttribute('role'), ratio: track.width ? fill.width / track.width : 0 };
  })"""

ICONS = """() => {
  const rows = (selector) => [...document.querySelectorAll(selector)]
    .filter((e) => e.checkVisibility()).map((e) => {
      const svg = e.querySelector('svg.icon');
      const box = svg ? svg.getBoundingClientRect() : { width: 0, height: 0 };
      return { name: svg ? [...svg.classList].find((c) => c.startsWith('icon-')) : null,
        shapes: svg ? svg.querySelectorAll('path, line, circle, rect').length : 0,
        w: box.width, h: box.height };
    });
  return { statuses: rows('.column-head'), kinds: rows('.card-kind'), lanes: rows('.lane-icon'),
    layers: [...document.querySelectorAll('.lane-icon svg.icon-epic')]
      .map((e) => e.querySelectorAll('path').length),
    text: document.body.innerText };
}"""


async def real_progress(client: Any) -> dict[str, tuple[int, int]]:
    """Tasks done out of the tasks there are (dropped ones left out), for every epic and story,
    counted from the tasks themselves and checked against what list_nodes says of each."""
    nodes = await all_nodes(client)
    by_ref = {node["ref"]: node for node in nodes}
    found: dict[str, tuple[int, int]] = {}
    for node in nodes:
        if node["kind"] == "task":
            continue
        tasks = [
            task
            for task in nodes
            if task["kind"] == "task"
            and task["status"] != "dropped"
            and (
                task["parent"] == node["ref"]
                if node["kind"] == "story"
                else by_ref[task["parent"]]["parent"] == node["ref"]
                and by_ref[task["parent"]]["status"] != "dropped"
            )
        ]
        done = sum(1 for task in tasks if task["status"] == "done")
        assert (node["progress_done"], node["progress_total"]) == (done, len(tasks)), node
        found[node["ref"]] = (done, len(tasks))
    return found


async def cards_progress_and_icons(world: World) -> None:
    async with connect(world.project.mcp_url, world.project.key) as client:
        real = await real_progress(client)
    stretched: list[str] = []
    wrong_meters: list[str] = []
    wrong_icons: list[str] = []
    measured = {"cards": 0, "meters": 0, "icons": 0}
    for theme in THEMES:
        for screen in (SCREENS[3], SCREENS[0]):
            where = f"{screen.name} in {theme}"
            async with layout_context(world.board, screen, theme) as (w, _):
                page = w.page
                await open_address(w, "#/board")
                await settle(page)
                if screen.name == "1600":
                    await shoot(page, f"board-{theme}")
                cells: list[dict[str, Any]] = await page.evaluate(CELLS)
                found = {card["ref"]: (cell, card) for cell in cells for card in cell["cards"]}
                same = [found[world[f"A{number}"]] for number in range(1, 6)]
                lone_cell, lone = found[world["A6"]]
                heights = [card["height"] for _, card in same]
                if max(heights) - min(heights) > 0.5:
                    stretched.append(f"{where}: five alike cards are {sorted(set(heights))} tall")
                if len(same[0][0]["cards"]) < 5 or len(lone_cell["cards"]) != 1:
                    stretched.append(f"{where}: the columns are not dense and sparse")
                if abs(lone["height"] - heights[0]) > 2:
                    stretched.append(
                        f"{where}: a lone card is {lone['height']:.0f} tall, not {heights[0]:.0f}"
                    )
                if lone_cell["height"] < lone["height"] + 20:
                    stretched.append(f"{where}: the sparse column is as short as its card")
                for name, title in (("D1", LONG_TITLE), ("D2", UNBROKEN)):
                    cell, card = found[world[name]]
                    if (
                        card["text"] != title
                        or card["clipped"]
                        or card["right"] > cell["right"] + 1
                    ):
                        stretched.append(
                            f"{where}: the long title of {code_of(world[name])} is clipped"
                        )
                measured["cards"] += len(found)

                meters: list[dict[str, Any]] = await page.evaluate(METERS)
                lanes = {meter["ref"]: meter for meter in meters if meter["kind"] == "lane"}
                if set(lanes) != {world["E1"], world["E2"], world["E3"]}:
                    wrong_meters.append(f"{where}: lanes {sorted(lanes)}")
                for meter in meters:
                    done, total = real[meter["ref"]]
                    wanted = done / total if total else 0
                    if (
                        meter["role"] != "progressbar"
                        or (meter["now"], meter["max"]) != (done, max(total, 1))
                        or not meter["text"].startswith(f"{done} of {total} task")
                        or abs(meter["ratio"] - wanted) > 0.0051
                    ):
                        wrong_meters.append(
                            f"{where}: {code_of(meter['ref'])} says {meter['text']}"
                        )
                measured["meters"] += len(meters)
                small = lanes.get(world["E2"])
                if small is None or small["text"] != "2 of 5 tasks done" or small["ratio"] > 0.41:
                    wrong_meters.append(f"{where}: the small epic does not show 2 of 5")

                icons: dict[str, Any] = await page.evaluate(ICONS)
                for group in ("statuses", "kinds", "lanes"):
                    drawn = icons[group]
                    if not drawn or any(
                        i["shapes"] == 0 or i["w"] < 8 or i["h"] < 8 for i in drawn
                    ):
                        wrong_icons.append(f"{where}: some {group} have no drawn icon")
                if len({each["name"] for each in icons["statuses"]}) != 6:
                    wrong_icons.append(f"{where}: the six statuses do not have six shapes")
                if {each["name"] for each in icons["kinds"]} != {"icon-story", "icon-task"}:
                    wrong_icons.append(f"{where}: stories and tasks do not both have their icon")
                if not icons["layers"] or set(icons["layers"]) != {3}:
                    wrong_icons.append(f"{where}: the epic is not a stack of three layers")
                used = sorted({glyph for glyph in OLD_GLYPHS if glyph in icons["text"]})
                if used:
                    wrong_icons.append(f"{where}: the page writes {used} as if they were icons")
                measured["icons"] += sum(len(icons[g]) for g in ("statuses", "kinds", "lanes"))
    proof(
        "M17",
        "on the Board a lone card in a sparse column is as tall as the same card in a full one "
        "(its column is taller than it is, so it was not stretched), five alike cards are one "
        "height, and a 200 character title and one with no place to break fit their cards whole",
        not stretched,
        clip("; ".join(stretched), 160) or f"{measured['cards']} cards read at 1600 and 390",
    )
    proof(
        "M17",
        "every progress bar is the real count: the page's numbers and the width of each bar match "
        "what list_nodes says and what counting the tasks gives, such as 2 of 5 for the small epic",
        not wrong_meters and real[world["E2"]] == (2, 5),
        clip("; ".join(wrong_meters), 160)
        or f"{measured['meters']} bars, small epic {real[world['E2']]}",
    )
    proof(
        "M17",
        "icons are drawn SVG outlines, not letters of a font: every status, kind and epic on the "
        "Board has one, the six statuses and the story and task differ in shape, the epic is a "
        "stack of three layers, and none of the old glyph characters is on the page",
        not wrong_icons,
        clip("; ".join(wrong_icons), 160) or f"{measured['icons']} icons read",
    )


# -- the keyboard, and motion

MOTION = """() => {
  const seconds = (text) => text.split(',').map(
    (part) => parseFloat(part) * (part.trim().endsWith('ms') ? 0.001 : 1));
  const controls = [...document.querySelectorAll('button, a, input, select, textarea')]
    .filter((control) => control.checkVisibility());
  const longest = controls.map(
    (control) => Math.max(...seconds(getComputedStyle(control).transitionDuration)));
  return { reduce: matchMedia('(prefers-reduced-motion: reduce)').matches,
    controls: controls.length, slowest: Math.max(...longest) };
}"""


async def the_keyboard_and_reduced_motion(world: World) -> None:
    task = world["A2"]
    card = f".card[data-ref='{task}']"
    seen: list[str] = []
    problems: list[str] = []

    async def ring(page: Page, what: str) -> None:
        found = await focus_ring(page)
        if found["skip"] or not found["ok"]:
            problems.append(f"{what} shows no focus ({found['why']})")
        else:
            seen.append(what)

    for screen in (SCREENS[2], SCREENS[1]):
        async with layout_context(world.board, screen, "light") as (w, _):
            page = w.page
            where = f"at {screen.name}"
            await open_address(w, "#/board")
            await expect(page.locator(card)).to_be_visible(timeout=SLOW)
            await settle(page)
            await start_of_page(page)
            await tab_to(page, card)
            await ring(page, f"the card {where}")
            await page.keyboard.press("Enter")
            await expect(page.locator("#card-title")).to_contain_text(
                f"{code_of(task)} ", timeout=SLOW
            )
            await frames(page, 3)
            if f"sel={task}" not in page.url:
                problems.append(f"Enter on the card did not open it {where}")
            await start_of_page(page)
            await tab_to(page, f"{card} button.move")
            await ring(page, f"the Move button {where}")
            await page.keyboard.press("Enter")
            menu = page.locator(".move-menu")
            await expect(menu).to_be_visible()
            if await page.evaluate("document.activeElement.getAttribute('role')") != "menuitem":
                problems.append(f"the Move menu did not take the focus {where}")
            await ring(page, f"a Move menu item {where}")
            await page.keyboard.press("Escape")
            await expect(menu).to_be_hidden()
            if not (await focused(page)).startswith(f"button Move {code_of(task)}"):
                problems.append(f"Escape did not give the focus back to Move {where}")
            if screen.narrow:
                # The panel is a sheet over the window here, and Escape closes it.
                await page.keyboard.press("Escape")
            else:
                await tab_to(page, "#panel .detail-head .close")
                await ring(page, f"the panel's close button {where}")
                await page.keyboard.press("Enter")
            await expect(page.locator("#panel")).to_be_hidden()
            if "sel=" in page.url:
                problems.append(f"the panel closed but the address still names it {where}")
    proof(
        "M17",
        "with the keyboard alone on the Board, a card is opened with Enter, its Move menu is "
        "opened and left with Escape, and the panel is closed (with Escape where it is a sheet "
        "over the window, with its close button beside the page); every control the keyboard "
        "stops on shows an outline of at least 2 pixels that stands out from what is around it",
        not problems and len(seen) >= 7,
        clip("; ".join(problems), 160) or f"{len(seen)} stops checked at 1280 and 768",
    )

    quick: dict[str, float] = {}
    for theme in THEMES:
        async with layout_context(world.board, SCREENS[2], theme, reduced_motion=True) as (w, _):
            await open_address(w, "#/board")
            await settle(w.page)
            still: dict[str, Any] = await w.page.evaluate(MOTION)
            quick[theme] = still["slowest"]
            if not still["reduce"] or still["controls"] < 20:
                quick[theme] = 99
    async with layout_context(world.board, SCREENS[2], "light") as (w, _):
        await open_address(w, "#/board")
        await settle(w.page)
        free: dict[str, Any] = await w.page.evaluate(MOTION)
    proof(
        "M17",
        "with reduced motion asked for, no button, link or field on the Board has a transition "
        "longer than 0.01 seconds, in Light and in Dark; the same check with motion allowed does "
        "find them (so the check can fail)",
        all(seconds <= 0.01 for seconds in quick.values())
        and not free["reduce"]
        and free["slowest"] > 0.05,
        f"slowest {max(quick.values()) * 1000:.0f} ms, with motion {free['slowest'] * 1000:.0f} ms",
    )


# -- the connection is lost, and comes back


async def losing_the_server(world: World) -> None:
    word = unique_word()
    task = world["W5"]
    draft = "Typed while the server was away"
    async with (
        connect(world.project.mcp_url, world.project.key) as client,
        themed_page(world.board, signed_in=True, scheme="dark") as w,
    ):
        page = w.page
        context = page.context
        await in_project(page, f"{world.board}#/board?sel={task}")
        await expect(page.locator("#card-title")).to_contain_text(f"{code_of(task)} ", timeout=SLOW)
        await page.locator("#edit-title").fill(draft)
        note, chip = page.locator("#connection-note"), page.locator("#connection")
        quiet_before = await note.is_hidden() and await chip.is_hidden()

        await context.set_offline(True)
        await expect(note).to_be_visible(timeout=30_000)
        await expect(chip).to_be_visible()
        words = " ".join((await note.inner_text()).split())
        told = await refusal_look(note)
        warned = (
            "cannot reach the server" in words
            and "trying again" in words
            and told["icon"]
            and told["role"] == "alert"
            and contrast(rgb_of(told["color"]), rgb_of(told["background"])) >= 4.5
            and rgb_of(told["background"]) == token("dark", "--warn-soft")
        )
        kept_in_every_view = True
        for view in ("graph", "roadmap", "board"):
            await w.go(f"#/{view}?sel={task}")
            await expect(page.locator("main#page h1").first).to_be_visible()
            kept_in_every_view = (
                kept_in_every_view
                and await note.is_visible()
                and await page.locator("#edit-title").input_value() == draft
            )
        await page.locator("#edit-title").fill(f"{draft}, and more")

        news = await made(client, "epic", f"Made while the board was cut off {word}")
        await context.set_offline(False)
        await expect(note).to_be_hidden(timeout=30_000)
        await expect(chip).to_be_hidden()
        await expect(
            page.get_by_role("region", name=f"Epic Made while the board was cut off {word}")
        ).to_be_visible(timeout=SLOW)
        recovered = await page.locator(f"#epic-list a[data-ref='{news}']").is_visible()
        draft_kept = await page.locator("#edit-title").input_value() == f"{draft}, and more"
        unsaved = await ok(client, "get_node", node=task)
    proof(
        "M17",
        "with the connection cut the board says so in the view that is open (with a sign as well "
        "as a colour, in readable contrast, on a warning surface of the theme) and keeps what was "
        "typed through every view; when the connection is back the notice goes, the epic an agent "
        "made meanwhile appears, and the draft is still in its box and was never saved",
        quiet_before
        and warned
        and kept_in_every_view
        and recovered
        and draft_kept
        and unsaved["title"] == "Wrap-up task 5",
        clip(words, 100),
    )
