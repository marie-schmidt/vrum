# ruff: noqa: E501
# The audits below are JavaScript that runs in the page; its lines are not wrapped to Python's width.
"""What M17's layout, responsiveness and visual-quality browser tests share: a real project with
content of varied lengths, a browser at each of the proof's widths and in each theme, the tour of
every screen of the board, and the in-page audits (overflow, scroll regions, targets, overlap,
names, headings, landmarks, spilled text, focus rings and contrast). The page helpers every
browser test uses are in board_helpers.py; the audits run in the page itself, because that is
where the sizes and the colours are."""

import base64
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp import Client
from playwright.async_api import BrowserContext, Page, async_playwright

from tests.board_helpers import SLOW, Watched, board_url, item_address, made
from tests.helpers import (
    confirm,
    connect,
    day,
    make_decision,
    make_fact,
    number_of,
    ok,
    ref_of,
    set_status,
)
from tests.world import SESSION_COOKIE, Project, make_project, project_at, register

THEMES = ("light", "dark")
ACTOR = "Layout Tester"
TALL = 3000  # CSS pixels: a window as tall as this shows nearly every screen without scrolling

# What a 200-character title, one with no place to break, and a long address look like.
LONG_TITLE = " ".join(["Long titles must wrap and never clip their meaning."] * 4)
UNBROKEN = "Unbroken" + "x" * 192
LONG_SOURCE = "https://example.test/archive/" + "a" * 190

# The icons of the old design, which were letters of a font. The board draws SVG now.
OLD_GLYPHS = "◆●▶✓⚑◈▤▪○◎■✕△▽▲"

# Review screenshots go here when it is set (a folder outside the repository), and nowhere else.
SHOTS = Path(os.environ["VRUM_LAYOUT_SHOTS"]) if os.environ.get("VRUM_LAYOUT_SHOTS") else None


@dataclass(frozen=True)
class Screen:
    """A browser window: its size in CSS pixels, and its pixel ratio. 200% zoom of a 1280 pixel
    window is a 640 pixel window with twice the pixels."""

    name: str
    width: int
    height: int
    scale: int = 1

    @property
    def narrow(self) -> bool:
        """The sidebar is off to the side and opened by a button (under 1100 CSS pixels)."""
        return self.width < 1100

    @property
    def phone(self) -> bool:
        """The drawer and the panel are sheets over the whole window (under 700 CSS pixels)."""
        return self.width < 700


SCREENS = [
    Screen("390", 390, 844),
    Screen("768", 768, 1024),
    Screen("1280", 1280, 900),
    Screen("1600", 1600, 1000),
    Screen("zoom200", 640, 450, 2),
]
BY_NAME = {screen.name: screen for screen in SCREENS}


# -- a project with content of every length ----------------------------------------------------


@dataclass
class World:
    """A project of its own (an account, a project and a key, made as a person would), with work
    and context of varied lengths, and the references of what was made by short name: story:12 for
    an epic, story or task, and rule:3, decision:7 and fact:5 for the records that are named in
    the board's addresses and hooks the same way."""

    project: Project
    board: str
    refs: dict[str, str] = field(default_factory=dict)

    def __getitem__(self, name: str) -> str:
        return self.refs[name]

    def number(self, name: str) -> int:
        """The number in a reference: what a rule, decision or fact is called by its own tools, and
        what an epic is called in an address (epic=3)."""
        return number_of(self.refs[name])


_worlds: dict[str, World] = {}


async def the_world(server_url: str) -> World:
    """The project these tests look at, made the first time it is asked for."""
    if server_url not in _worlds:
        project = make_project(register(name="Layout tester"), "Layout test project")
        world = World(project, board_url(server_url, project))
        async with connect(server_url, project.key) as client:
            await build(client, world)
        _worlds[server_url] = world
    return _worlds[server_url]


async def build(client: Client, world: World) -> None:
    """Work and context made through MCP: epics that are dense in one column and sparse in the
    others, one with nothing in it, one all done, one made of long words, requirements, decisions
    and facts (some with long words and a long source), links, dates, a claim."""
    refs = world.refs

    async def node(name: str, kind: str, title: str, parent: str | None, **more: Any) -> None:
        refs[name] = await made(client, kind, title, refs[parent] if parent else None, **more)

    async def status(name: str, to: str) -> None:
        await set_status(client, {"ref": refs[name]}, to)

    async def needs_first(waiting: str, first: str) -> None:
        await ok(
            client,
            "add_link",
            actor="agent-1",
            source=refs[waiting],
            target=refs[first],
            link_type="needs_first",
        )

    await node("E1", "epic", "Run and see it all", None)
    await node("E2", "epic", "Accounts and private projects", None)
    await node("E3", "epic", LONG_TITLE, None)
    await node("E4", "epic", "Nothing planned yet", None)
    await node("E5", "epic", "Finished work", None)
    # E6 has more tasks in one row than the map labels, so the map draws it as small tiles.
    await node("E6", "epic", "A wide row of tasks", None)
    await node("S7", "story", "Many small tasks", "E6")
    for number in range(1, 14):
        await node(f"W{number}", "task", f"Wide task {number:02d}", "S7")

    # E1 is dense in the Ready column and sparse in the others. Its cards say the same things in
    # the same number of words, so that their heights can be compared.
    await node("S1", "story", "Story one", "E1")
    await node("S2", "story", "A readable activity feed", "E1", priority="high")
    for number in range(1, 6):
        await node(f"A{number}", "task", f"Same card 0{number}", "S1")
    await node("A6", "task", "Same card 06", "S1")
    await status("A6", "in_progress")
    await node("A7", "task", "Same card 07", "S1")
    await status("A7", "blocked")
    await node("B1", "task", "Keep the feed current", "S2", start_on=day(2), target_on=day(9))
    await node("B2", "task", "Show expiring task claims", "S2", start_on=day(5), target_on=day(14))
    await node("B3", "task", "Needs the feed", "S2", start_on=day(10), target_on=day(18))
    await needs_first("B3", "B1")
    await node("B4", "task", "Verify the complete workflow", "S2", priority="urgent")
    await node("B5", "task", "Past its date", "S2", start_on=day(-40), target_on=day(-30))

    # E2 holds one card of the same kind in an otherwise empty lane.
    await node("S3", "story", "Story two", "E2")
    await node("C1", "task", "Same card 08", "S3")
    await node("C2", "task", "Design the sign-in flow", "S3", start_on=day(1), target_on=day(6))
    await status("C2", "in_progress")

    # E3 holds the long words.
    await node("S4", "story", UNBROKEN, "E3", priority="high")
    await node("S5", "story", LONG_TITLE, "E3")
    await node("D1", "task", LONG_TITLE, "S5", priority="urgent", target_on=day(-30))
    await node("D2", "task", UNBROKEN, "S5", start_on=day(3), target_on=day(8))
    await node("D3", "task", "Waits for the long one", "S5")
    await needs_first("D3", "D1")

    # E5 is all done, and E4 has nothing in it.
    await node("S6", "story", "Wrapped up", "E5")
    await node("F1", "task", "Done one", "S6")
    await node("F2", "task", "Done two", "S6")
    await status("F1", "done")
    await status("F2", "done")
    await ok(client, "claim_node", actor="agent-1", node=refs["B1"])

    # Context: requirements, decisions and facts, some with long words.
    async def record(name: str, kind: str, made_one: dict[str, Any]) -> None:
        refs[name] = ref_of(kind, made_one["id"])

    await record(
        "R1",
        "rule",
        await ok(
            client,
            "create_rule",
            actor="ann",
            title="Keep actor, time and handoff note",
            story=refs["S1"],
        ),
    )
    await record(
        "R2", "rule", await ok(client, "create_rule", actor="ann", title="Not in any story yet")
    )
    await record(
        "R3",
        "rule",
        await ok(client, "create_rule", actor="ann", title=LONG_TITLE, story=refs["S5"]),
    )
    claims = await make_decision(
        client,
        "Claims expire; work stays visible",
        text="A claim has a lease that expires. At most one actor holds a claim at a time.",
        reason="Coordinate work without a permanent lock when an agent stops.",
        nodes=[refs["S2"]],
        rule_ids=[world.number("R1")],
    )
    await record("X1", "decision", claims)
    long_decision = await make_decision(
        client,
        LONG_TITLE,
        text=f"{UNBROKEN} {LONG_TITLE}",
        reason=LONG_SOURCE,
        nodes=[refs["S5"]],
    )
    await record("X2", "decision", long_decision)
    await record(
        "X3",
        "decision",
        await make_decision(client, "Everything is written down", project_wide=True),
    )
    await confirm(client, "decision", claims)
    lease = await make_fact(
        client, "The team call agreed the lease is ten minutes", nodes=[refs["S2"]]
    )
    await record("Y1", "fact", lease)
    long_fact = await make_fact(
        client,
        f"{UNBROKEN} {LONG_TITLE}",
        source=LONG_SOURCE,
        nodes=[refs["D1"]],
        decision_ids=[world.number("X2")],
    )
    await record("Y2", "fact", long_fact)
    await record("Y3", "fact", await make_fact(client, "A fact about nothing in particular"))
    await confirm(client, "fact", lease)


# -- a browser at a size, in a theme ------------------------------------------------------------


@asynccontextmanager
async def layout_context(
    board: str,
    screen: Screen,
    theme: str,
    *,
    signed_in: bool = True,
    reduced_motion: bool = False,
    touch: bool = False,
    name: str = ACTOR,
) -> AsyncIterator[tuple[Watched, BrowserContext]]:
    """A headless browser of this size and theme on a project's board. The theme is the person's
    own saved choice (as the Appearance control keeps it), so the page is drawn in it from the
    first frame. Signed in, it is the project's owner with a session of its own."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(
                viewport={"width": screen.width, "height": screen.height},
                device_scale_factor=screen.scale,
                color_scheme="dark" if theme == "dark" else "light",
                reduced_motion="reduce" if reduced_motion else "no-preference",
                has_touch=touch,
                # A date field is typed in the order its language writes a date.
                locale="en-US",
            )
            if signed_in:
                person = project_at(board).owner.sign_in_again()
                await context.add_cookies(
                    [
                        {
                            "name": SESSION_COOKIE,
                            "value": person.cookie,
                            "domain": urlparse(board).hostname or "127.0.0.1",
                            "path": "/board",
                            "httpOnly": True,
                            "sameSite": "Strict",
                        }
                    ]
                )
            await context.add_init_script(
                f"localStorage.setItem('vrum.theme', {json.dumps(theme)});"
                f"localStorage.setItem('vrum.actor', {json.dumps(name)});"
            )
            page = await context.new_page()
            watched = Watched(page=page, board=board)
            page.on("request", lambda request: watched.requests.append(request.url))
            page.on("pageerror", lambda error: watched.problems.append(f"page error: {error}"))
            # A refusal the server sends is the board working; the browser logs it as a failed
            # load all the same, and so does a project that is not there.
            page.on(
                "console",
                lambda message: (
                    watched.problems.append(f"console: {message.text}")
                    if message.type == "error"
                    and "status of 400" not in message.text
                    and "status of 404" not in message.text
                    else None
                ),
            )
            yield watched, context
        finally:
            await browser.close()


@asynccontextmanager
async def layout_page(
    board: str, screen: Screen, theme: str, **options: Any
) -> AsyncIterator[Watched]:
    async with layout_context(board, screen, theme, **options) as (watched, _):
        yield watched


async def open_address(w: Watched, address: str = "") -> None:
    """Load a page of the board and wait until the shell is drawn. The menu is behind a button on
    a narrow window, so what is waited for is the shell, and not the menu being visible."""
    await w.page.goto(f"{w.board}{address}")
    await w.page.wait_for_selector(
        "#app[data-shell='project'] #main-nav, .entry", state="attached", timeout=SLOW
    )
    await w.page.wait_for_function(
        "() => ![...document.querySelectorAll('#page .loading')].some(e => e.checkVisibility())",
        timeout=SLOW,
    )


async def frames(page: Page, count: int = 2) -> None:
    """Let the browser draw a few frames, so that a change of layout has happened, and let what
    was sliding or fading finish (what never ends, a spinner, is left as it is)."""
    for _ in range(count):
        await page.evaluate("() => new Promise(done => requestAnimationFrame(() => done(0)))")
    await page.evaluate(
        "() => document.getAnimations().forEach((a) => { try { a.finish(); } catch (e) { /* endless */ } })"
    )


# -- the tour of every screen -------------------------------------------------------------------


@dataclass(frozen=True)
class Stop:
    """One screen of the board, and how to be sure that it has been drawn."""

    name: str
    address: str  # after the #, or a whole path when it starts with /
    ready: str  # a selector that is there when the screen is drawn
    group: str  # the part of the proof it belongs to


def stops(world: World) -> list[Stop]:
    """Every screen the proof names that needs a project and a person signed in: the Board, the
    Graph (map and list, Work, Context and Everything), the Roadmap, the full page of an epic, a
    story and a task, the detail panel of each kind of record (and over a page), the search and
    activity drawers, the three settings pages, and the pages that have no project open."""
    e1 = world.number("E1")
    map_ready = "svg#graph-svg[data-ready=true]"

    def page_of(name: str, item: str, ready: str) -> Stop:
        """The full page of an epic, story or task: ready once its table or its place is drawn."""
        return Stop(
            name, item_address(world[item]), f"#item-page[data-ref='{world[item]}'] {ready}", "item"
        )

    rows = "#item-table tbody tr"
    return [
        Stop("board", "#/board", ".card[data-ref]", "board"),
        Stop("board-no-match", "#/board?q=zzzznothingmatches", "#board-empty", "board"),
        Stop("graph-work-map", f"#/graph?view=work&as=map&epic={e1}", map_ready, "graph"),
        Stop("graph-context-map", f"#/graph?view=context&as=map&epic={e1}", map_ready, "graph"),
        Stop(
            "graph-everything-map", f"#/graph?view=everything&as=map&epic={e1}", map_ready, "graph"
        ),
        Stop("graph-all-map", "#/graph?view=everything&as=map", map_ready, "graph"),
        Stop("graph-work-list", f"#/graph?view=work&as=list&epic={e1}", "#work-tree li", "graph"),
        Stop("graph-context-list", "#/graph?view=context&as=list", ".gv-record", "graph"),
        Stop("graph-everything-list", "#/graph?view=everything&as=list", ".gv-record", "graph"),
        Stop("roadmap", "#/roadmap", ".rm-bar", "roadmap"),
        page_of("item-epic", "E1", rows),
        page_of("item-epic-long", "E3", rows),
        page_of("item-epic-empty", "E4", "#item-empty .empty-text"),
        page_of("item-story", "S2", rows),
        page_of("item-story-long", "S5", rows),
        page_of("item-task", "D1", "#item-part-title"),
        Stop(
            "item-quick-look",
            f"{item_address(world['S2'])}&sel={world['B1']}",
            f"#panel .card-detail[data-ref='{world['B1']}']",
            "item",
        ),
        Stop("panel-epic", f"#/board?sel={world['E3']}", "#panel .card-detail", "panel"),
        Stop("panel-story", f"#/board?sel={world['S5']}", "#panel .card-detail", "panel"),
        Stop("panel-task", f"#/board?sel={world['D1']}", "#panel .card-detail", "panel"),
        Stop("panel-requirement", f"#/board?sel={world['R3']}", "#panel .detail", "panel"),
        Stop("panel-decision", f"#/board?sel={world['X2']}", "#panel .detail", "panel"),
        Stop("panel-fact", f"#/board?sel={world['Y2']}", "#panel .detail", "panel"),
        Stop("panel-coverage", "#/board?panel=coverage", "#panel .detail", "panel"),
        Stop("search", "#/board?drawer=search&search=card", "#search-results li.result", "drawer"),
        Stop("search-empty", "#/board?drawer=search&search=zzzznothing", "#search-count", "drawer"),
        Stop("activity", "#/board?drawer=activity", "#feed .event", "drawer"),
        Stop("access", "#/access", ".settings-page", "settings"),
        Stop("tools", "#/tools", ".tool", "settings"),
        Stop("status", "#/status", "#status-body table", "settings"),
        Stop("projects", "/board", "#projects", "entry"),
        Stop("no-such-project", "/board/p/99999999", "#no-such-project", "entry"),
    ]


async def settle(page: Page) -> None:
    """Wait until the page has finished what it was asked: nothing is loading, the map's layout
    is at rest, and the network is quiet."""
    await page.wait_for_function(
        """() => {
            const waiting = [...document.querySelectorAll('.loading, [aria-busy=true]')]
                .filter(e => e.checkVisibility && e.checkVisibility());
            const map = document.querySelector('svg#graph-svg');
            const moving = map && map.offsetParent !== null && map.dataset.layout === 'compact'
                && !map.dataset.settled;
            return !waiting.length && !moving;
        }""",
        timeout=SLOW,
    )
    await page.wait_for_load_state("networkidle")
    await frames(page)


async def visit(w: Watched, stop: Stop) -> None:
    """Go to a screen of the tour (by the address, without loading the page again while it is on
    a project's board) and wait for it to be drawn."""
    page = w.page
    if stop.address.startswith("/"):
        await page.goto(f"{w.origin}{stop.address}")
    else:
        inside = await page.evaluate(
            "() => location.pathname.startsWith('/board/p/')"
            " && !!document.querySelector('#app[data-shell=project]')"
        )
        if inside:
            await page.keyboard.press("Escape")
            await page.evaluate("(hash) => { location.hash = hash; }", stop.address)
        else:
            await open_address(w, stop.address)
    await page.wait_for_selector(stop.ready, state="attached", timeout=SLOW)
    await settle(page)


async def shoot(page: Page, name: str) -> None:
    """Screenshots for people to look at, when VRUM_LAYOUT_SHOTS names a folder: what the window
    shows, and the whole page."""
    if SHOTS is not None:
        SHOTS.mkdir(parents=True, exist_ok=True)
        await page.evaluate("() => window.scrollTo(0, 0)")
        await page.screenshot(path=str(SHOTS / f"{name}.png"))
        await page.screenshot(path=str(SHOTS / f"{name}-full.png"), full_page=True)


# -- the audits, which run in the page ------------------------------------------------------------

# Everything the audits share: what is visible, what is clipped, colours, names.
LIB = r"""
const vis = (e) => e.checkVisibility({ checkVisibilityCSS: true });
const here = (e) => e.checkVisibility();
const rect = (e) => e.getBoundingClientRect();
const words = (s) => (s || '').replace(/\s+/g, ' ').trim();
const desc = (e) => {
  const id = e.id ? '#' + e.id : '';
  const raw = e.getAttribute('class');
  const cls = raw && raw.trim() ? '.' + raw.trim().split(/\s+/).slice(0, 2).join('.') : '';
  const text = words(e.getAttribute('aria-label') || e.textContent).slice(0, 36);
  return e.tagName.toLowerCase() + id + cls + (text ? ' "' + text + '"' : '');
};
const clipBox = (e, r) => {
  const box = { l: r.left, t: r.top, r: r.right, b: r.bottom };
  // The Roadmap's label column is sticky: bars and notes scroll beneath it, and are not seen there.
  const labels = e.closest('.rm-track') && document.querySelector('.rm-labels');
  if (labels) box.l = Math.max(box.l, rect(labels).right);
  for (let a = e.parentElement; a && a !== document.documentElement; a = a.parentElement) {
    const s = getComputedStyle(a);
    const p = rect(a);
    if (s.overflowX !== 'visible') { box.l = Math.max(box.l, p.left); box.r = Math.min(box.r, p.right); }
    if (s.overflowY !== 'visible') { box.t = Math.max(box.t, p.top); box.b = Math.min(box.b, p.bottom); }
  }
  return box;
};
const parse = (s) => {
  if (!s) return null;
  let m = s.match(/^rgba?\(([^)]+)\)$/);
  if (m) {
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  }
  m = s.match(/^color\(srgb ([^)]+)\)$/);
  if (m) {
    const p = m[1].split(/[ \/]+/).filter(Boolean).map(Number);
    return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1];
  }
  return null;
};
const over = (top, under) => [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3])).concat([1]);
const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
const ratio = (a, b) => {
  const x = lum(a); const y = lum(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
};
const bgOf = (e) => {
  const layers = [];
  for (let a = e; a; a = a.parentElement) {
    const c = parse(getComputedStyle(a).backgroundColor);
    if (c && c[3] > 0) layers.push(c);
    if (c && c[3] >= 1) break;
  }
  let base = [255, 255, 255, 1];
  for (const c of layers.reverse()) base = over(c, base);
  return base;
};
const textOf = (e) => {
  let t = '';
  for (const n of e.childNodes) {
    if (n.nodeType === 3) t += n.nodeValue;
    else if (n.nodeType === 1 && n.getAttribute('aria-hidden') !== 'true') t += ' ' + textOf(n);
  }
  return words(t);
};
const nameOf = (e) => {
  const ids = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
  const fromIds = words(ids.map((id) => (document.getElementById(id) || {}).textContent).join(' '));
  if (fromIds) return fromIds;
  const aria = words(e.getAttribute('aria-label'));
  if (aria) return aria;
  if (e.labels && e.labels.length) {
    const t = words([...e.labels].map((l) => l.textContent).join(' '));
    if (t) return t;
  }
  if (e.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(e.type) && e.value) return e.value;
  if (!['INPUT', 'SELECT', 'TEXTAREA'].includes(e.tagName)) {
    const own = textOf(e);
    if (own) return own;
  }
  return words(e.getAttribute('title'));
};
const CONTROLS = 'a[href], button, input:not([type=hidden]), select, textarea, summary, '
  + '[role=button], [role=menuitem], [role=treeitem], [role=option], [role=tab], [role=checkbox]';
const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled):not([type=hidden]), '
  + 'select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])';
const disabled = (e) => !!e.closest(':disabled, [aria-disabled=true]');
// Something opaque that is not part of `e` sits on top of it (a sheet, a scrim, a sticky column).
// A control on top of a control is not cover: that is an overlap, and the audits report it.
const opaque = (x) => {
  if (x.matches('#sheet-scrim, #sidebar-scrim, dialog')) return true;
  const c = parse(getComputedStyle(x).backgroundColor);
  return !!c && c[3] >= 0.9;
};
const obscured = (e, box, byControlToo) => {
  const stack = document.elementsFromPoint((box.l + box.r) / 2, (box.t + box.b) / 2);
  const related = (x) => x === e || x.contains(e) || e.contains(x);
  const first = stack.findIndex(related);
  const above = first < 0 ? stack : stack.slice(0, first);
  return above.some((x) => !related(x) && opaque(x) && (byControlToo || !x.closest(CONTROLS)));
};
"""

STRUCTURE = (
    "() => {"
    + LIB
    + r"""
const out = { theme: document.documentElement.dataset.theme };
const w = innerWidth;

// 1. Nothing sticks out of the window sideways, unless a region clips it or scrolls it.
out.overflow = { doc: document.documentElement.scrollWidth, body: document.body.scrollWidth,
  width: w, offenders: [] };
for (const e of document.body.querySelectorAll('*')) {
  if (!vis(e) || e.closest('.sr-only, .skip')) continue;
  const r = rect(e);
  if (r.width === 0 || r.height === 0) continue;
  const c = clipBox(e, r);
  if (c.r - c.l <= 0.5 || c.b - c.t <= 0.5) continue;
  if (c.r > w + 0.5 || c.l < -0.5) out.overflow.offenders.push(desc(e));
}

// 2. Whatever scrolls is a labelled region that the keyboard can reach.
out.regions = [];
for (const e of document.body.querySelectorAll('*')) {
  if (!vis(e) || e instanceof SVGElement) continue;
  const s = getComputedStyle(e);
  const x = ['auto', 'scroll'].includes(s.overflowX) && e.scrollWidth > e.clientWidth + 1;
  const y = ['auto', 'scroll'].includes(s.overflowY) && e.scrollHeight > e.clientHeight + 1;
  if (!x && !y) continue;
  out.regions.push({ desc: desc(e), id: e.id, x, y, role: e.getAttribute('role'),
    name: nameOf(e), tab: e.hasAttribute('tabindex') && e.tabIndex >= 0,
    reachable: (e.hasAttribute('tabindex') && e.tabIndex >= 0) || !!e.querySelector(FOCUSABLE) });
}

// 3. Targets of at least 24 by 24 CSS pixels, or with room around them (WCAG 2.5.8). Only what
// can be seen and reached counts: not what is clipped away, or under a sheet or a sticky column.
// The nodes of the map are looked at in their own test, at 100% zoom: the map is drawn zoomed out.
const targets = [];
for (const e of document.body.querySelectorAll(CONTROLS)) {
  if (!vis(e) || e.closest('.sr-only, .skip, svg#graph-svg')) continue;
  const c = clipBox(e, rect(e));
  const box = { l: Math.max(0, c.l), t: Math.max(0, c.t), r: Math.min(w, c.r), b: Math.min(innerHeight, c.b) };
  if (box.r - box.l <= 1 || box.b - box.t <= 1 || obscured(e, box, false)) continue;
  // A control that is mostly out of sight (scrolled away, under a sticky column) waits to be
  // scrolled to; its size is looked at when it is.
  const whole = rect(e);
  if ((box.r - box.l) * (box.b - box.t) < 0.5 * whole.width * whole.height) continue;
  // A box or a radio button is pressed through its label, so the two are one target.
  if (e.labels && e.labels.length && vis(e.labels[0])) {
    const l = clipBox(e.labels[0], rect(e.labels[0]));
    box.l = Math.min(box.l, Math.max(0, l.l)); box.t = Math.min(box.t, Math.max(0, l.t));
    box.r = Math.max(box.r, Math.min(w, l.r)); box.b = Math.max(box.b, Math.min(innerHeight, l.b));
  }
  targets.push({ e, box });
}
const inline = (e) => {
  if (!(e.tagName === 'A' || e.classList.contains('link'))) return false;
  const p = e.closest('p, li, dd, td, th, label, span.help, div');
  return !!p && words(p.textContent).length > words(e.textContent).length + 3;
};
out.small = [];
for (const t of targets) {
  // What can be seen and pressed: the part that is not clipped away.
  const wide = t.box.r - t.box.l; const high = t.box.b - t.box.t;
  if (wide >= 23.5 && high >= 23.5) continue;
  if (inline(t.e)) continue;
  const cx = (t.box.l + t.box.r) / 2; const cy = (t.box.t + t.box.b) / 2;
  const crowded = targets.some((o) => {
    if (o === t || o.e.contains(t.e) || t.e.contains(o.e)) return false;
    const nx = Math.max(o.box.l, Math.min(cx, o.box.r));
    const ny = Math.max(o.box.t, Math.min(cy, o.box.b));
    return Math.hypot(nx - cx, ny - cy) < 12;
  });
  if (crowded) {
    out.small.push({ desc: desc(t.e), w: Math.round(wide * 10) / 10, h: Math.round(high * 10) / 10 });
  }
}

// 4. Controls do not sit on each other (a link that wraps is the lines it is on, not the box
// around them).
const live = targets.filter((t) => !t.e.matches('summary'));
const lines = (t) => [...t.e.getClientRects()].filter((r) => r.width > 0 && r.height > 0).map((r) => {
  const c = clipBox(t.e, r);
  return { l: Math.max(0, c.l), t: Math.max(0, c.t), r: Math.min(w, c.r), b: Math.min(innerHeight, c.b) };
});
// A menu, a dialog or a sheet floats over the page on purpose; controls are not compared across it.
const layerOf = (e) => {
  const l = e.closest('.menu, dialog, #panel, #drawer, #sidebar');
  return l && ['fixed', 'absolute'].includes(getComputedStyle(l).position) ? l : null;
};
out.overlaps = [];
for (let i = 0; i < live.length; i++) {
  for (let j = i + 1; j < live.length; j++) {
    const a = live[i]; const b = live[j];
    if (a.e.contains(b.e) || b.e.contains(a.e) || layerOf(a.e) !== layerOf(b.e)) continue;
    if (a.e.labels && [...a.e.labels].some((l) => l.contains(b.e) || l === b.e)) continue;
    if (b.e.labels && [...b.e.labels].some((l) => l.contains(a.e) || l === a.e)) continue;
    let area = 0;
    for (const x of lines(a)) {
      for (const y of lines(b)) {
        const dx = Math.min(x.r, y.r) - Math.max(x.l, y.l);
        const dy = Math.min(x.b, y.b) - Math.max(x.t, y.t);
        if (dx > 1.5 && dy > 1.5) area += dx * dy;
      }
    }
    if (area) out.overlaps.push([desc(a.e), desc(b.e), Math.round(area)]);
  }
}

// 5. Every control has a name, and every icon is hidden from a screen reader or named.
out.unnamed = [];
for (const e of document.body.querySelectorAll(CONTROLS)) {
  if (!here(e) || e.closest('[hidden]')) continue;
  if (!nameOf(e)) out.unnamed.push(desc(e));
}
out.icons = [];
for (const e of document.body.querySelectorAll('svg')) {
  if (!here(e)) continue;
  const named = e.getAttribute('role') === 'img' && (e.getAttribute('aria-label') || e.querySelector('title'));
  const group = e.id === 'graph-svg' && e.getAttribute('aria-label');
  if (e.getAttribute('aria-hidden') === 'true' || named || group) continue;
  out.icons.push(desc(e));
}
out.glyphs = [];
const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
for (let n = walker.nextNode(); n; n = walker.nextNode()) {
  const p = n.parentElement;
  if (!p || p.closest('option, script, style, noscript') || !here(p)) continue;
  const found = [...n.nodeValue].filter((ch) => OLD.includes(ch));
  if (found.length) out.glyphs.push(desc(p) + ' has ' + found.join(''));
}

// 6. Headings: one h1, and no level skipped inside a region of the page. A heading that is only
// for a screen reader (.sr-only) is a heading to the people who move by headings, so it counts.
const levels = [];
for (const e of document.body.querySelectorAll('h1, h2, h3, h4, h5, h6, [role=heading]')) {
  if (!vis(e)) continue;
  const level = e.getAttribute('aria-level') ? Number(e.getAttribute('aria-level')) : Number(e.tagName[1]);
  const region = e.closest('main, #sidebar, #top, #drawer, #panel, dialog, nav');
  levels.push({ level, desc: desc(e), region: region ? region.id || region.tagName : 'body' });
}
out.h1 = levels.filter((l) => l.level === 1).length;
out.skips = [];
const last = {};
for (const l of levels) {
  const before = last[l.region] || 1;
  if (l.level > before + 1) out.skips.push(l.desc + ' (h' + l.level + ' after h' + before + ' in ' + l.region + ')');
  last[l.region] = l.level;
}

// 7. Landmarks: one main; navigation, asides, regions and dialogs have names.
out.mains = [...document.querySelectorAll('main, [role=main]')].filter(vis).length;
out.navs = [...document.querySelectorAll('nav')].filter(here).length;
out.unlabelled = [];
for (const e of document.querySelectorAll('nav, aside, [role=region], [role=search], dialog[open]')) {
  if (!here(e) || e.closest('[hidden]')) continue;
  if (!nameOf(e)) out.unlabelled.push(desc(e));
}

// 8. Text stays in its box: it wraps, scrolls, or is clipped with the whole of it said elsewhere.
const alt = (e) => {
  // What is spoken says an item's code in words ("Task 25: Past its date" for "T-25 Past its date"),
  // so the words after the code are what has to be there.
  const text = words(e.textContent).replace(/^[EST]-\d+\s+/, '');
  if (!text) return true;
  const probe = text.slice(0, 12);
  const holders = [e, ...e.querySelectorAll('[title], [aria-label]')];
  for (let a = e.parentElement, i = 0; a && i < 3; a = a.parentElement, i++) holders.push(a);
  return holders.some((h) => words(h.getAttribute('title')).includes(probe)
    || words(h.getAttribute('aria-label')).includes(probe));
};
out.spills = [];
out.clipped = [];
const boxes = new Map();
const boxOf = (a) => { if (!boxes.has(a)) boxes.set(a, rect(a)); return boxes.get(a); };
const texts = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
for (let n = texts.nextNode(); n; n = texts.nextNode()) {
  const p = n.parentElement;
  if (!words(n.nodeValue) || !p || p instanceof SVGElement || p.closest('svg, option, script, style')) continue;
  if (!vis(p) || p.closest('.sr-only, .skip')) continue;
  const range = document.createRange();
  range.selectNodeContents(n);
  const t = range.getBoundingClientRect();
  if (t.width === 0) continue;
  for (let a = p; a && a !== document.body; a = a.parentElement) {
    const s = getComputedStyle(a);
    if (s.display === 'inline' || s.display === 'contents') continue;
    const b = boxOf(a);
    if (b.width < 2 || b.height < 2) break;
    if (t.right <= b.right + 1 && t.left >= b.left - 1) {
      // A menu or popup is placed against the page, not inside the box of what holds it in the markup.
      if (['auto', 'scroll'].includes(s.overflowX) || s.position === 'absolute' || s.position === 'fixed') break;
      continue;
    }
    if (['auto', 'scroll'].includes(s.overflowX)) break;
    const what = desc(p) + ' pokes out of ' + desc(a) + ' by ' + Math.round(Math.max(t.right - b.right, b.left - t.left));
    if (s.overflowX === 'visible') { out.spills.push(what); break; }
    if (!alt(a) && !alt(p)) out.clipped.push(what);
    break;
  }
}
return out;
}"""
).replace("OLD.includes", f"{OLD_GLYPHS!r}.includes")


@dataclass
class Structure:
    """What the page's own audit found: every list is empty when all is well."""

    theme: str
    raw: dict[str, Any]

    def __getitem__(self, name: str) -> Any:
        return self.raw[name]


async def tall(page: Page, quick: bool = False) -> tuple[dict[str, int], int]:
    """Make the window tall, so that the whole screen can be seen at once (as a person scrolling
    would see it); gives the size to go back to, and the height of the page. A menu closes when
    the window is resized or scrolled, so a `quick` look leaves the window as it is."""
    size = page.viewport_size
    assert size is not None
    window = {"width": size["width"], "height": size["height"]}
    if quick:
        return window, window["height"]
    await page.set_viewport_size({"width": window["width"], "height": TALL})
    await frames(page, 3)
    return window, int(await page.evaluate("() => document.documentElement.scrollHeight"))


async def structure(page: Page, quick: bool = False) -> Structure:
    """Audit the screen the page shows now, as a tall window shows it: overflow, scroll regions,
    targets, overlaps, names, icons, headings, landmarks and text that does not fit."""
    size, _ = await tall(page, quick)
    try:
        raw: dict[str, Any] = await page.evaluate(STRUCTURE)
    finally:
        if not quick:
            await page.set_viewport_size({"width": size["width"], "height": size["height"]})
            await frames(page)
    return Structure(raw["theme"], raw)


# Where the keyboard is, and whether it can be seen there.
FOCUS = (
    "() => {"
    + LIB
    + r"""
const el = document.activeElement;
if (!el || el === document.body || el === document.documentElement) return { skip: true, desc: 'body' };
// The calendar button inside a date field has the browser's own focus, and the field itself does not.
if (el instanceof HTMLElement && !el.matches(':focus')) return { skip: true, desc: desc(el) };
const out = { skip: false, desc: desc(el), ok: false, why: 'no outline or ring', w: rect(el).width,
  h: rect(el).height };
const ring = (c, from) => {
  const s = getComputedStyle(c);
  if (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2) {
    const col = parse(s.outlineColor);
    if (col && col[3] > 0) {
      const inset = parseFloat(s.outlineOffset) < 0;
      const bg = bgOf(inset ? c : c.parentElement || c);
      const r = ratio(over(col, bg), bg);
      return { ok: r >= 3, why: 'outline ' + s.outlineWidth + ' ' + r.toFixed(2) + ':1 on ' + desc(c) };
    }
  }
  const m = s.boxShadow.match(/(rgba?\([^)]+\))\s+(-?[\d.]+)px\s+(-?[\d.]+)px\s+([\d.]+)px\s+([\d.]+)px/);
  if (m && parseFloat(m[5]) >= 2) {
    const col = parse(m[1]);
    const bg = bgOf(c.parentElement || c);
    const r = ratio(over(col, bg), bg);
    return { ok: r >= 3, why: 'ring ' + m[5] + 'px ' + r.toFixed(2) + ':1 on ' + desc(c) };
  }
  return null;
};
if (el.classList.contains('dot')) {
  const shape = el.querySelector('.shape');
  const s = getComputedStyle(shape);
  const fill = parse(s.fill); const stroke = parse(s.stroke);
  const bg = over(fill, bgOf(el.ownerSVGElement.parentElement));
  const r = ratio(over(stroke, bg), bg);
  return { ...out, ok: parseFloat(s.strokeWidth) >= 2 && r >= 3,
    why: 'stroke ' + s.strokeWidth + ' ' + r.toFixed(2) + ':1' };
}
let c = el;
// The ring may be on the box the element is in, or (a tree row) on the row inside the element.
const candidates = [el, ...[...el.children].slice(0, 3)];
for (let a = el.parentElement, i = 0; a && i < 4; a = a.parentElement, i++) candidates.push(a);
for (c of candidates) {
  const found = ring(c);
  if (found) return { ...out, ...found };
}
return out;
}"""
)


async def focus_ring(page: Page) -> dict[str, Any]:
    """Whether the element with the keyboard focus shows it (an outline or a ring of at least 2
    pixels, at 3:1 against what is around it, on it or on the box it sits in). What is read is
    the ring once the colours have stopped changing."""
    await page.evaluate("() => document.getAnimations().forEach((a) => a.finish())")
    await frames(page)
    result: dict[str, Any] = await page.evaluate(FOCUS)
    return result


# The words and colours of every piece of text, border and icon, to be compared with the pixels
# that are really behind them.
COLLECT = (
    "() => {"
    + LIB
    + r"""
const items = [];
const w = innerWidth; const h = innerHeight;
const sheet = [...document.querySelectorAll('dialog[open]')][0];
const fixed = ['sidebar', 'panel', 'drawer'].map((id) => document.getElementById(id))
  .find((e) => e && !e.hidden && vis(e) && getComputedStyle(e).position === 'fixed');
const root = sheet || fixed || document.body;
// The fade at the edge of the Board that has more to show is over the words under it on purpose.
const fades = [];
for (const f of document.querySelectorAll('.board-frame')) {
  const v = f.querySelector('.board-viewport');
  if (!v) continue;
  const r = rect(v);
  if (f.dataset.left === 'true') fades.push({ l: r.left, r: r.left + 32, t: r.top, b: r.bottom });
  if (f.dataset.right === 'true') fades.push({ l: r.right - 32, r: r.right, t: r.top, b: r.bottom });
}
const faded = (b) => fades.some((f) => b.l < f.r && b.r > f.l && b.t < f.b && b.b > f.t);
const seen = (e, r) => {
  const c = clipBox(e, r);
  const box = { l: Math.max(0, c.l), t: Math.max(0, c.t), r: Math.min(w, c.r), b: Math.min(h, c.b) };
  if (box.r - box.l < 2 || box.b - box.t < 2 || faded(box)) return null;
  return obscured(e, box, true) ? null : box;
};
const fade = (e) => {
  let o = 1;
  for (let a = e; a; a = a.parentElement) o *= parseFloat(getComputedStyle(a).opacity);
  return o;
};
const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
for (let n = walker.nextNode(); n; n = walker.nextNode()) {
  const text = words(n.nodeValue);
  const p = n.parentElement;
  if (!text || !p || p.closest('option, script, style, noscript, textarea, .sr-only, .skip')) continue;
  if (!vis(p) || disabled(p)) continue;
  const s = getComputedStyle(p);
  let size = parseFloat(s.fontSize);
  if (!size) continue;
  const svg = p instanceof SVGElement;
  let color = parse(svg ? s.fill : s.color);
  if (!color) continue;
  if (svg) {
    const m = p.getScreenCTM();
    size *= m ? Math.hypot(m.a, m.b) : 1;
    color[3] *= parseFloat(s.fillOpacity);
  }
  color[3] *= fade(p);
  const range = document.createRange();
  range.selectNodeContents(n);
  for (const r of range.getClientRects()) {
    const box = seen(p, r);
    // A sliver of a word that is mostly out of sight (under a sticky column, or cut by the top of
    // the window as the page is scrolled) says nothing; a line cut off at its side (an ellipsis) does.
    if (!box || box.r - box.l < 16 || box.b - box.t < 0.7 * r.height) continue;
    // The line under a link is not the page behind it.
    if (p.closest('a, .link') || s.textDecorationLine.includes('underline')) box.b = Math.max(box.t + 4, box.b - 5);
    items.push({ kind: 'text', desc: desc(p), text: text.slice(0, 40), box, color, size,
      bold: parseInt(s.fontWeight, 10) >= 700 });
  }
}
for (const e of root.querySelectorAll('input, select')) {
  if (!vis(e) || disabled(e) || ['checkbox', 'radio', 'hidden', 'range'].includes(e.type)) continue;
  const s = getComputedStyle(e);
  const r = rect(e);
  const box = seen(e, { left: r.left + parseFloat(s.paddingLeft) + 1, right: r.right - 24,
    top: r.top + 3, bottom: r.bottom - 3 });
  if (!box) continue;
  const shown = e.tagName === 'SELECT' ? e.options[e.selectedIndex] && e.options[e.selectedIndex].text : e.value;
  const color = parse(shown ? s.color : getComputedStyle(e, '::placeholder').color);
  const text = shown || e.placeholder;
  if (!text || !color) continue;
  color[3] *= fade(e);
  items.push({ kind: 'text', desc: desc(e), text: String(text).slice(0, 40), box, color,
    size: parseFloat(s.fontSize), bold: false });
}
for (const e of root.querySelectorAll('svg.icon')) {
  if (!vis(e) || disabled(e) || e.closest('.sr-only')) continue;
  const box = seen(e, rect(e));
  const color = parse(getComputedStyle(e).color);
  if (!box || !color) continue;
  color[3] *= fade(e);
  items.push({ kind: 'icon', desc: desc(e.parentElement || e), text: 'icon', box, color });
}
const edge = (e) => {
  const s = getComputedStyle(e);
  if (parseFloat(s.borderTopWidth) < 1 || s.borderTopStyle === 'none') return null;
  const c = parse(s.borderTopColor);
  return c && c[3] > 0 ? c : null;
};
const fields = 'input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=range]), '
  + 'select, textarea';
for (const e of root.querySelectorAll(fields)) {
  if (!vis(e) || disabled(e) || e.closest('.sr-only, .segmented, .appearance, .menu')) continue;
  let edgeOf = e; let color = edge(e);
  for (let a = e.parentElement, i = 0; a && i < 2 && !color; a = a.parentElement, i++) {
    color = edge(a); edgeOf = a;
  }
  const r = rect(edgeOf);
  const box = seen(edgeOf, r);
  if (!box || box.l > r.left + 1 || box.r < r.right - 1) continue;
  const edges = { l: r.left, t: r.top, r: r.right, b: r.bottom };
  items.push({ kind: 'border', desc: desc(e), text: 'border', box: color ? edges : null, color });
}
return items;
}"""
)

# Run on an empty page, with the pixels of the screen drawn without its text.
ANALYSE = r"""
async ({ png, items }) => {
  const bytes = Uint8Array.from(atob(png), (c) => c.charCodeAt(0));
  const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const g = canvas.getContext('2d', { willReadFrequently: true });
  g.drawImage(bitmap, 0, 0);
  const W = bitmap.width; const H = bitmap.height;
  const data = g.getImageData(0, 0, W, H).data;
  const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const ratio = (a, b) => {
    const x = lum(a); const y = lum(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const at = (x, y) => {
    x = Math.max(0, Math.min(W - 1, Math.round(x))); y = Math.max(0, Math.min(H - 1, Math.round(y)));
    const i = (y * W + x) * 4;
    return [data[i], data[i + 1], data[i + 2]];
  };
  const mix = (c, bg) => [0, 1, 2].map((i) => c[i] * c[3] + bg[i] * (1 - c[3]));
  const found = [];
  for (const it of items) {
    if (it.kind === 'border') {
      if (!it.box) { found.push({ ...it, ratio: 0, need: 3, why: 'a field with no border' }); continue; }
      const { l, t, r, b } = it.box;
      const cx = (l + r) / 2; const cy = (t + b) / 2;
      const sides = [at(cx, t - 3), at(cx, b + 3), at(l - 3, cy), at(r + 3, cy)];
      const rs = sides.map((s) => ratio(mix(it.color, s), s)).sort((x, y) => x - y);
      const med = (rs[1] + rs[2]) / 2;
      if (med < 3) found.push({ ...it, ratio: med, need: 3 });
      continue;
    }
    const x0 = Math.floor(it.box.l); const x1 = Math.ceil(it.box.r);
    const y0 = Math.floor(it.box.t); const y1 = Math.ceil(it.box.b);
    const px = [];
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) px.push(at(x, y));
    if (!px.length) continue;
    px.sort((a, b) => lum(a) - lum(b));
    const lo = px[Math.floor(px.length * 0.15)]; const hi = px[Math.floor(px.length * 0.85)];
    const worst = Math.min(ratio(mix(it.color, lo), lo), ratio(mix(it.color, hi), hi));
    const large = it.kind === 'text' && (it.size >= 24 || (it.size >= 18.66 && it.bold));
    const need = it.kind === 'text' && !large ? 4.5 : 3;
    if (worst < need) found.push({ ...it, ratio: worst, need });
  }
  return { checked: items.length, found };
}
"""

HIDE_TEXT = """
* { color: transparent !important; text-shadow: none !important;
    -webkit-text-fill-color: transparent !important; caret-color: transparent !important; }
svg text, svg tspan { fill: transparent !important; stroke: transparent !important; }
::placeholder { color: transparent !important; -webkit-text-fill-color: transparent !important; }
"""


async def hidden_text_screenshot(page: Page) -> bytes:
    """The screen as it is drawn now, in CSS pixels, with every word and icon made transparent,
    so that what is left is what is behind them. The board's policy turns away a style tag, but
    not a style sheet made from script."""
    await page.evaluate(
        """(css) => {
            const sheet = new CSSStyleSheet();
            sheet.replaceSync(css);
            document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
            window.__hiding = sheet;
        }""",
        HIDE_TEXT,
    )
    try:
        return await page.screenshot(type="png", scale="css", animations="disabled", caret="hide")
    finally:
        await page.evaluate(
            "() => { document.adoptedStyleSheets = document.adoptedStyleSheets"
            ".filter(s => s !== window.__hiding); }"
        )


@dataclass
class Contrast:
    checked: int = 0
    found: list[dict[str, Any]] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"{item['kind']} {item['desc']} {item['text']!r}: {item['ratio']:.2f}:1 "
            f"(needs {item['need']}){' ' + item['why'] if 'why' in item else ''}"
            for item in self.found
        ]


def where(item: dict[str, Any]) -> str:
    """An item of the contrast audit by what and where it is (to tell whether it has moved)."""
    box = item["box"] and {name: round(value) for name, value in item["box"].items()}
    return json.dumps([item["kind"], item["desc"], item["text"], box], sort_keys=True)


async def contrast(w: Watched, scratch: Page, quick: bool = False) -> Contrast:
    """The contrast of every visible word, icon and field border on the screen, against the
    pixels that are really behind it: the screen is drawn again with its text made transparent,
    and each word's colour is compared with the lightest and the darkest of what is under it
    (the 15th and 85th percentile, so that a hairline does not count). Text that is disabled is
    not held to it. Words are held to 4.5:1, or 3:1 when they are large; icons and borders to
    3:1. A `quick` look leaves the window, the scroll and the focus as they are, for a menu."""
    page = w.page
    size, height = await tall(page, quick)
    if not quick:
        # A field with the focus has a ring round it, which is not what is round its border.
        await page.evaluate("() => document.activeElement && document.activeElement.blur()")
    result = Contrast()
    try:
        for top in range(0, 1 if quick else max(height - 200, 1), TALL - 200):
            if not quick:
                await page.evaluate("(y) => window.scrollTo(0, y)", top)
                await frames(page)
            before = await page.evaluate(COLLECT)
            shot = await hidden_text_screenshot(page)
            # What moved while the screen was being drawn again says nothing about its colours.
            after = {where(item) for item in await page.evaluate(COLLECT)}
            items = [item for item in before if where(item) in after]
            checked = await scratch.evaluate(
                ANALYSE, {"png": base64.b64encode(shot).decode(), "items": items}
            )
            result.checked += int(checked["checked"])
            result.found.extend(checked["found"])
    finally:
        if not quick:
            await page.evaluate("() => window.scrollTo(0, 0)")
            await page.set_viewport_size({"width": size["width"], "height": size["height"]})
            await frames(page)
    return result


async def blank_page(context: BrowserContext) -> Page:
    """An empty page in the same browser, where pixels can be read without the board's policy."""
    page = await context.new_page()
    await page.goto("about:blank")
    return page


# -- every screen, audited, once for each size and theme -------------------------------------------


@dataclass
class Audit:
    structure: Structure
    contrast: Contrast


@dataclass
class Tour:
    """Every screen audited at one size in one theme (by name), and what the page complained of
    in the console on the way."""

    audits: dict[str, Audit] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


_tours: dict[tuple[str, str, str], Tour] = {}


async def tour(server_url: str, screen: Screen, theme: str) -> Tour:
    """Visit every screen and state of the board at this size, in this theme, signed in and out,
    and audit each. The tests of one proof read what they need from the same tour."""
    key = (server_url, screen.name, theme)
    if key in _tours:
        return _tours[key]
    world = await the_world(server_url)
    found = Tour()
    async with layout_context(world.board, screen, theme) as (w, context):
        scratch = await blank_page(context)
        await open_address(w)

        async def look(name: str, quick: bool = False) -> None:
            found.audits[name] = Audit(
                await structure(w.page, quick), await contrast(w, scratch, quick)
            )

        for stop in stops(world):
            await visit(w, stop)
            await look(stop.name)
        await visit(w, stops(world)[0])
        for name, state in states(screen, world).items():
            await state.show(w)
            await look(name, state.quick)
            await state.leave(w)
        found.problems.extend(w.problems)
    async with layout_context(world.board, screen, theme, signed_in=False) as (w, context):
        scratch = await blank_page(context)
        await w.page.goto(f"{w.origin}/board")
        await w.page.wait_for_selector("#sign-in-form")
        await settle(w.page)
        found.audits["sign-in"] = Audit(await structure(w.page), await contrast(w, scratch))
        await w.page.locator("#show-register").click()
        await w.page.wait_for_selector("#register-form")
        await settle(w.page)
        found.audits["register"] = Audit(await structure(w.page), await contrast(w, scratch))
        found.problems.extend(w.problems)
    _tours[key] = found
    return found


@dataclass(frozen=True)
class State:
    """A screen that is a state of one already visited: the sidebar opened, a menu, a dialog. A
    menu closes at the first resize, scroll or loss of focus, so it is looked at `quick`ly."""

    name: str
    open: Callable[[Watched], Awaitable[None]]
    close: Callable[[Watched], Awaitable[None]]
    quick: bool = False

    async def show(self, w: Watched) -> None:
        await self.open(w)
        await frames(w.page, 3)

    async def leave(self, w: Watched) -> None:
        await self.close(w)
        await frames(w.page)


async def press_escape(w: Watched) -> None:
    await w.page.keyboard.press("Escape")


def states(screen: Screen, world: World) -> dict[str, State]:
    """The sidebar (on a window narrow enough to hide it), the Settings menu, a card's Move menu,
    and three dialogs (the Board's New item, a fact to record, and a task to add on a story's
    page)."""

    async def sidebar(w: Watched) -> None:
        await w.page.locator("#sidebar-toggle").click()
        await w.page.wait_for_selector("#sidebar.open", state="visible")

    async def close_sidebar(w: Watched) -> None:
        await w.page.locator("#sidebar-close").click()
        await w.page.wait_for_selector("#sidebar.open", state="hidden")

    async def settings(w: Watched) -> None:
        if screen.narrow:
            await sidebar(w)
        await w.page.locator("#settings-menu").click()
        await w.page.wait_for_selector("#settings-list", state="visible")

    async def close_settings(w: Watched) -> None:
        await w.page.keyboard.press("Escape")
        if screen.narrow:
            await close_sidebar(w)

    async def move(w: Watched) -> None:
        await w.page.locator(".card .move").first.click()
        await w.page.wait_for_selector(".move-menu", state="visible")

    async def new_item(w: Watched) -> None:
        await w.page.locator("#new-item").click()
        await w.page.wait_for_selector("dialog.modal[open]", state="visible")

    async def close_dialog(w: Watched) -> None:
        await w.page.keyboard.press("Escape")
        await w.page.wait_for_selector("dialog.modal[open]", state="detached")

    async def record_fact(w: Watched) -> None:
        await w.page.evaluate("() => { location.hash = '#/graph?view=context&as=list'; }")
        await w.page.wait_for_selector("#record-fact", state="visible")
        await w.page.locator("#record-fact").click()
        await w.page.wait_for_selector("dialog.modal[open]", state="visible")

    async def new_task(w: Watched) -> None:
        await w.page.evaluate("(hash) => { location.hash = hash; }", item_address(world["S2"]))
        await w.page.wait_for_selector("#item-table tbody tr", state="attached")
        await w.page.locator("#new-child").click()
        await w.page.wait_for_selector("dialog.modal[open]", state="visible")

    async def back_to_board(w: Watched) -> None:
        await close_dialog(w)
        await w.page.evaluate("() => { location.hash = '#/board'; }")
        await w.page.wait_for_selector(".card[data-ref]", state="attached")

    found = {
        "settings-menu": State("settings-menu", settings, close_settings, quick=True),
        "move-menu": State("move-menu", move, press_escape, quick=True),
        "new-item-dialog": State("new-item-dialog", new_item, close_dialog),
        "record-fact-dialog": State("record-fact-dialog", record_fact, back_to_board),
        "new-task-dialog": State("new-task-dialog", new_task, back_to_board),
    }
    if screen.narrow:
        found = {"sidebar": State("sidebar", sidebar, close_sidebar), **found}
    return found


# -- the keyboard ---------------------------------------------------------------------------------


async def focused(page: Page) -> str:
    """What has the focus, as a short description."""
    result: str = await page.evaluate(
        "() => { const e = document.activeElement; return e ? e.tagName.toLowerCase()"
        " + (e.id ? '#' + e.id : '') + (e.getAttribute('aria-label') ? ' ' + e.getAttribute('aria-label') : '')"
        " : 'nothing'; }"
    )
    return result


async def tab_to(page: Page, selector: str, *, limit: int = 150, back: bool = False) -> int:
    """Press Tab (or Shift+Tab) until the focus is on something that matches, and say how many
    presses it took. A page that cannot be reached by the keyboard fails here."""
    seen = []
    for presses in range(1, limit + 1):
        await page.keyboard.press("Shift+Tab" if back else "Tab")
        if await page.evaluate(
            "(s) => !!document.activeElement && document.activeElement.matches(s)", selector
        ):
            # The page scrolls what has the focus into view; let that finish (a menu that is
            # opened meanwhile would be closed by it).
            await frames(page, 3)
            return presses
        seen.append(await focused(page))
    raise AssertionError(f"Tab never reached {selector}; it went through {seen[-8:]}")


async def start_of_page(page: Page) -> None:
    """Put the focus back at the start of the document, as a page that has just loaded has it."""
    await page.evaluate(
        "() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0);"
        " const s = document.createElement('span'); s.tabIndex = -1; document.body.prepend(s);"
        " s.focus(); s.remove(); }"
    )


# -- geometry the visual tests compare -------------------------------------------------------------

# Where every card of the Board is and what is in it, in CSS pixels on the page.
CARDS = """
() => [...document.querySelectorAll('.board .card')].map((card) => {
  const box = (e) => { if (!e) return null; const r = e.getBoundingClientRect();
    return { l: r.left, t: r.top, r: r.right, b: r.bottom, h: r.height, w: r.width }; };
  const own = card.getBoundingClientRect();
  const rel = (e) => { const b = box(e); return b && { ...b, top: b.t - own.top, left: b.l - own.left }; };
  const title = card.querySelector('.card-title');
  const range = document.createRange(); range.selectNodeContents(title);
  const text = range.getBoundingClientRect();
  const s = getComputedStyle(card);
  const cell = card.closest('.cell');
  return {
    ref: card.dataset.ref, kind: card.dataset.kind, status: card.dataset.status,
    epic: cell ? cell.dataset.epic : null, cell: cell ? cell.dataset.status : null,
    cellBox: cell ? box(cell) : null, box: box(card),
    siblings: card.closest('.cards') ? card.closest('.cards').children.length : 0,
    top: rel(card.querySelector('.card-top')), title: rel(title),
    text: { l: text.left, t: text.top, r: text.right, b: text.bottom },
    chips: rel(card.querySelector('.card-chips')), context: rel(card.querySelector('.card-context')),
    note: rel(card.querySelector('.card-note')),
    padding: [s.paddingTop, s.paddingRight, s.paddingBottom, s.paddingLeft].join(' '),
    radius: s.borderTopLeftRadius, minHeight: s.minHeight,
    ellipsis: getComputedStyle(title).textOverflow, titleClipped: title.scrollHeight > title.clientHeight + 1
      || title.scrollWidth > title.clientWidth + 1,
    titleText: title.textContent, label: card.getAttribute('aria-label'),
  };
})
"""

# Where the nodes and the lines of the map are, in the map's own units, and where its furniture is.
GRAPH = """
() => {
  const svg = document.querySelector('svg#graph-svg');
  const num = (e, a) => parseFloat(e.getAttribute(a));
  const screen = (e) => { const r = e.getBoundingClientRect();
    return { l: r.left, t: r.top, r: r.right, b: r.bottom }; };
  const dots = [...svg.querySelectorAll('.dot')].map((g) => {
    const shape = g.querySelector('.shape');
    const at = (g.getAttribute('transform') || '').match(/translate\\(([-\\d.]+)[ ,]+([-\\d.]+)\\)/);
    const tx = at ? parseFloat(at[1]) : 0; const ty = at ? parseFloat(at[2]) : 0;
    const local = { x: num(shape, 'x'), y: num(shape, 'y'), w: num(shape, 'width'), h: num(shape, 'height') };
    const inside = [...g.querySelectorAll('text, svg.icon')].map((e) => {
      if (e.tagName.toLowerCase() === 'svg') {
        return { what: 'icon', x: num(e, 'x'), y: num(e, 'y'), w: num(e, 'width'), h: num(e, 'height') };
      }
      const b = e.getBBox();
      return { what: e.textContent, x: b.x, y: b.y, w: b.width, h: b.height };
    });
    return { key: g.dataset.key, type: g.dataset.type, label: g.getAttribute('aria-label'),
      tile: g.classList.contains('tile'), local, inside,
      at: { x: tx + local.x, y: ty + local.y, w: local.w, h: local.h }, shape: screen(shape) };
  });
  const edges = [...svg.querySelectorAll('.edge')].map((p) => {
    const length = p.getTotalLength();
    const steps = Math.max(16, Math.ceil(length / 5));
    const points = [];
    for (let i = 0; i <= steps; i++) {
      const q = p.getPointAtLength((length * i) / steps);
      points.push([q.x, q.y]);
    }
    return { type: p.dataset.type, source: p.dataset.source, target: p.dataset.target, points };
  });
  const furniture = {};
  for (const [name, selector] of [['legend', '#graph-legend'], ['zoom', '.gm-zoom'],
    ['controls', '.gm-controls'], ['fit', '#fit'], ['canvas', '#graph-canvas'],
    ['panel', '#panel'], ['hint', '#graph-hint'], ['caption', '#graph-caption']]) {
    const e = document.querySelector(selector);
    furniture[name] = e && !e.hidden && e.checkVisibility() ? screen(e) : null;
  }
  const edgeBoxes = [...svg.querySelectorAll('.edge')].map((p) => screen(p));
  return { layout: svg.dataset.layout, dots, edges, furniture, edgeBoxes,
    zoom: document.querySelector('#zoom-level').textContent,
    transform: svg.querySelector('#viewport').getAttribute('transform') };
}
"""


async def cards(page: Page) -> dict[str, dict[str, Any]]:
    """Every card of the Board, by reference."""
    found: list[dict[str, Any]] = await page.evaluate(CARDS)
    return {card["ref"]: card for card in found}


async def graph(page: Page) -> dict[str, Any]:
    """The map as it is drawn."""
    found: dict[str, Any] = await page.evaluate(GRAPH)
    return found
