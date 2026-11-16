"""What the tests of the appearance (Light, Dark, System) and of the board's states share: a browser
whose operating-system preference the test decides, the design tokens read from tokens.css, a way
to see what was painted first, and a survey of the colours a page really uses. It goes through a
real headless browser against the real containers and never reaches into the page's own code except
through the public `window.vrumTheme` and the controls a person would use."""

import asyncio
import base64
import contextlib
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import cache
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import (
    Dialog,
    Locator,
    Page,
    Response,
    Route,
    async_playwright,
)

from tests.board_helpers import SLOW, VIEWPORT, Watched
from tests.world import SESSION_COOKIE, Project, project_at
from vrum.board import STATIC

THEMES = ("light", "dark")
CHOICES = ("light", "dark", "system")

# -- the design tokens ----------------------------------------------------------------------------

Colour = tuple[int, int, int, float]


def parse_colour(text: str) -> Colour | None:
    """A colour as the tokens write it (#rgb, #rrggbb, rgb(r g b / a)) or as a browser reports it
    (rgb(r, g, b), rgba(r, g, b, a), color(srgb r g b)), as (r, g, b, alpha); None for anything
    else, such as a shadow or a length."""
    text = text.strip().lower()
    if text == "transparent":
        return (0, 0, 0, 0.0)
    hexed = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})", text)
    if hexed:
        digits = hexed.group(1)
        if len(digits) == 3:
            digits = "".join(each * 2 for each in digits)
        red, green, blue = (int(digits[at : at + 2], 16) for at in (0, 2, 4))
        alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
        return (red, green, blue, alpha)
    functional = re.fullmatch(r"(rgba?|color)\((.*)\)", text)
    if functional is None:
        return None
    parts = [part for part in re.split(r"[\s,/]+", functional.group(2).strip()) if part]
    scale = 1.0
    if functional.group(1) == "color":
        if not parts or parts[0] != "srgb":
            return None
        parts = parts[1:]
        scale = 255.0
    if len(parts) not in (3, 4):
        return None
    try:
        channels = [round(float(part) * scale) for part in parts[:3]]
        alpha = 1.0
        if len(parts) == 4:
            alpha = float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3])
    except ValueError:
        return None
    return (channels[0], channels[1], channels[2], alpha)


@cache
def _blocks() -> dict[str, dict[str, str]]:
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "tokens.css").read_text(encoding="utf-8"), flags=re.S)
    blocks: dict[str, dict[str, str]] = {"common": {}, "light": {}, "dark": {}}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        selector = " ".join(selector.split())
        which = (
            "dark"
            if 'data-theme="dark"' in selector
            else "light"
            if 'data-theme="light"' in selector
            else "common"
        )
        declared = re.findall(r"(--[\w-]+|color-scheme)\s*:\s*([^;]+);", body)
        blocks[which].update({name: value.strip() for name, value in declared})
    return blocks


def tokens(theme: str) -> dict[str, str]:
    """Every design token of a theme as tokens.css writes it, with the ones both themes share, and
    the theme's `color-scheme` under that name."""
    blocks = _blocks()
    return {**blocks["common"], **blocks[theme]}


def colour_tokens(theme: str) -> dict[str, Colour]:
    """The tokens of a theme that are colours (not the shadows, sizes or fonts), by name."""
    found: dict[str, Colour] = {}
    for name, value in tokens(theme).items():
        colour = parse_colour(value) if name.startswith("--") else None
        if colour is not None:
            found[name] = colour
    return found


def token(theme: str, name: str) -> tuple[int, int, int]:
    """One colour token of a theme as (r, g, b); the token must be an opaque colour."""
    red, green, blue, alpha = colour_tokens(theme)[name]
    assert alpha == 1.0, f"{name} is not opaque"
    return (red, green, blue)


def palette(theme: str) -> set[tuple[int, int, int]]:
    """The (r, g, b) of every colour token of a theme: what a page painted in that theme may use."""
    return {(red, green, blue) for red, green, blue, _ in colour_tokens(theme).values()}


def _linear(channel: float) -> float:
    value = channel / 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def luminance(colour: Sequence[float]) -> float:
    """The relative luminance of a colour given as (r, g, b, ...) in 0 to 255."""
    red, green, blue = colour[:3]
    return 0.2126 * _linear(red) + 0.7152 * _linear(green) + 0.0722 * _linear(blue)


def contrast(first: Sequence[float], second: Sequence[float]) -> float:
    """The WCAG contrast ratio of two opaque colours, from 1 to 21."""
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


# -- a browser whose operating system the test decides ----------------------------------------

# What a page's document was in the first moments of its life. It runs before anything of the page
# does and records every change of `data-theme` with the time it was made, the theme and the
# colours at DOMContentLoaded and at the first animation frame, and the paints.
FIRST_PAINT = """
(() => {
  const record = { changes: [], loaded: null, frame: null, paints: [] };
  window.__first = record;
  const look = () => {
    const html = document.documentElement;
    return {
      time: performance.now(),
      theme: html.getAttribute('data-theme'),
      choice: html.getAttribute('data-theme-choice'),
      scheme: getComputedStyle(html).colorScheme,
      htmlBackground: getComputedStyle(html).backgroundColor,
      bodyBackground: document.body ? getComputedStyle(document.body).backgroundColor : null,
    };
  };
  new MutationObserver((records) => {
    const add = (el) => record.changes.push({
      time: performance.now(),
      theme: el.getAttribute('data-theme'),
    });
    for (const change of records) {
      if (change.type === 'attributes') add(change.target);
      else for (const node of change.addedNodes) if (node === document.documentElement) add(node);
    }
  }).observe(document, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['data-theme'],
  });
  document.addEventListener('DOMContentLoaded', () => { record.loaded = look(); });
  requestAnimationFrame(() => { record.frame = look(); });
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      record.paints.push({ name: entry.name, time: entry.startTime });
    }
  }).observe({ type: 'paint', buffered: true });
})();
"""

# Every violation of the content security policy the page causes, as it happens.
POLICY_WATCH = """
(() => {
  window.__violations = [];
  document.addEventListener('securitypolicyviolation', (event) => {
    window.__violations.push({
      directive: event.violatedDirective,
      blocked: event.blockedURI,
      sample: event.sample,
    });
  });
})();
"""


@asynccontextmanager
async def themed_page(
    board: str,
    *,
    signed_in: bool,
    scheme: str | None = "light",
    name: str | None = "Tester",
    keep_bodies: bool = False,
    scripts: Sequence[str] = (POLICY_WATCH,),
    viewport: dict[str, int] | None = None,
) -> AsyncIterator[Watched]:
    """A headless browser on a project's board (`board`, from board_url) whose operating system
    prefers `scheme` ('light', 'dark', or None for the browser's own default). Signed in, it is the
    project's owner with a session of its own; signed out it shows the sign-in. Every request of
    every page of the context is kept in `requests`, so that a test can see that none of them
    left the server. `name` is what the board records changes with. `scripts` run before anything
    of each page of the context does."""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(
                viewport=viewport or VIEWPORT,  # type: ignore[arg-type]
                color_scheme=scheme,  # type: ignore[arg-type]
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
            if name is not None:
                await context.add_init_script(
                    f"localStorage.setItem('vrum.actor', {json.dumps(name)});"
                )
            for script in scripts:
                await context.add_init_script(script)
            page = await context.new_page()
            watched = Watched(page=page, board=board)

            async def on_dialog(dialog: Dialog) -> None:
                watched.dialogs.append(dialog.message)
                await dialog.dismiss()

            async def on_response(response: Response) -> None:
                with contextlib.suppress(Exception):
                    watched.bodies.append((response.url, await response.text()))

            context.on("request", lambda request: watched.requests.append(request.url))
            page.on("dialog", on_dialog)
            if keep_bodies:
                page.on("response", on_response)
            page.on("pageerror", lambda error: watched.problems.append(f"page error: {error}"))
            page.on(
                "console",
                lambda message: (
                    watched.problems.append(f"console: {message.text}")
                    if message.type == "error" and "status of 400" not in message.text
                    else None
                ),
            )
            yield watched
        finally:
            await browser.close()


_LOOK = """() => {
  const html = document.documentElement;
  return {
    theme: html.getAttribute('data-theme'),
    choice: html.getAttribute('data-theme-choice'),
    api: window.vrumTheme.choice(),
    stored: window.localStorage.getItem('vrum.theme'),
    scheme: getComputedStyle(html).colorScheme,
    background: getComputedStyle(document.body).backgroundColor,
    os: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
    checked: [...document.querySelectorAll('[data-appearance]:checked')]
      .map((i) => i.dataset.appearance),
    controls: document.querySelectorAll('[role=radiogroup][aria-label=Appearance]').length,
  };
}"""


async def look(page: Page) -> dict[str, Any]:
    """What the page says its appearance is: the resolved theme, the choice, what is stored, the
    colour scheme and background that are painted, the operating system's preference and which
    choices the appearance controls show as checked."""
    found: dict[str, Any] = await page.evaluate(_LOOK)
    return found


async def settled(page: Page, theme: str, choice: str, *, stored: str | None | bool = True) -> None:
    """Wait until the page shows `theme` for `choice`, then check that everything which says so
    agrees: the attributes, the page's own answer, the stored value, the colour scheme and the
    background (the theme's tokens), and the appearance controls."""
    await page.wait_for_function(
        """([theme, choice]) => {
            const html = document.documentElement;
            return html.getAttribute('data-theme') === theme
                && html.getAttribute('data-theme-choice') === choice;
        }""",
        arg=[theme, choice],
        timeout=10_000,
    )
    state = await look(page)
    assert state["theme"] == theme and state["choice"] == choice and state["api"] == choice, state
    assert state["scheme"] == theme, state
    assert parse_colour(state["background"]) == (*token(theme, "--bg"), 1.0), state
    if choice == "system":
        assert state["stored"] in (None, "system"), state
    elif stored is True:
        assert state["stored"] == choice, state
    elif stored is not False:
        assert state["stored"] == stored, state
    assert set(state["checked"]) <= {choice}, state


async def choose(page: Page, choice: str, *, within: str = "#top, #sidebar") -> None:
    """Pick Light, Dark or System with the appearance control a person sees (in the sidebar of a
    project, at the top before one is open), the way a click on its label does."""
    group = page.locator(within).locator("[role=radiogroup][aria-label=Appearance]").first
    await group.locator(f"label:has(input[data-appearance={choice}])").click()
    await page.wait_for_function(
        "choice => document.documentElement.getAttribute('data-theme-choice') === choice",
        arg=choice,
    )


async def os_prefers(page: Page, scheme: str) -> None:
    """The operating system's preference changes now, while the page is open, and the page has
    been told (two frames later, so that a handler has had its turn)."""
    await page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    await page.wait_for_function(
        "scheme => matchMedia('(prefers-color-scheme: dark)').matches === (scheme === 'dark')",
        arg=scheme,
    )
    await page.evaluate(
        "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"
    )


async def transitions_done(page: Page) -> None:
    """Wait until every colour that was changing has arrived (buttons and fields fade between
    themes for a moment), so that what is measured next is the theme and not a blend of two."""
    await page.evaluate(
        """async () => {
            await new Promise((done) => requestAnimationFrame(done));
            const running = () => document.getAnimations().filter(
                (each) => each instanceof CSSTransition && each.playState !== 'finished');
            while (running().length) {
                await Promise.all(running().map((each) => each.finished.catch(() => null)));
            }
        }"""
    )


async def switch(page: Page, theme: str) -> None:
    """Change appearance the way the control does, and wait for the colours to arrive. Under a
    modal dialog the control cannot be reached (the page behind it is inert), so the page's own
    function, which the control calls, is used here."""
    await page.evaluate("(theme) => window.vrumTheme.set(theme)", theme)
    await settled(page, theme, theme)
    await transitions_done(page)


async def refusal_look(place: Locator) -> dict[str, Any]:
    """How a message looks where it is shown: its text colour, the first background behind it that
    is painted at all, whether it has a sign as well as a colour, and its role."""
    found: dict[str, Any] = await place.evaluate(
        """(el) => {
            let ground = el;
            let background = 'rgba(0, 0, 0, 0)';
            while (ground) {
                background = getComputedStyle(ground).backgroundColor;
                if (!/^rgba\\(0, 0, 0, 0\\)$|^transparent$/.test(background)) break;
                ground = ground.parentElement;
            }
            return {
                color: getComputedStyle(el).color,
                background,
                icon: el.querySelector('svg') !== null,
                role: el.getAttribute('role'),
                words: el.textContent.trim(),
            };
        }"""
    )
    return found


async def stored_in_browser(page: Page) -> dict[str, Any]:
    """Everything the page keeps in this browser: local and session storage, and the names of the
    cookies of the context, for a test that the preference is kept alone."""
    found: dict[str, Any] = await page.evaluate(
        """() => {
            const all = (store) => Object.fromEntries(
                Object.keys(store).map((key) => [key, store.getItem(key)]));
            return {
                local: all(localStorage),
                session: all(sessionStorage),
                visibleCookies: document.cookie,
            };
        }"""
    )
    found["cookies"] = [cookie["name"] for cookie in await page.context.cookies()]
    return found


async def policy_violations(page: Page) -> list[dict[str, str]]:
    """What the content security policy turned away in the page that is open (see POLICY_WATCH)."""
    found: list[dict[str, str]] = await page.evaluate("() => window.__violations || []")
    return found


# -- the first frame -------------------------------------------------------------------------------


async def pixels(
    page: Page, png: bytes, points: Sequence[tuple[int, int]]
) -> list[tuple[int, int, int]]:
    """The colour at each point of a screenshot, read in a page of a browser context of its own."""
    browser = page.context.browser
    assert browser is not None
    context = await browser.new_context()
    try:
        reader = await context.new_page()
        found: list[list[int]] = await reader.evaluate(
            """async ([data, points]) => {
                const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
                const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
                const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
                const context = canvas.getContext('2d');
                context.drawImage(bitmap, 0, 0);
                return points.map(([x, y]) => [...context.getImageData(x, y, 1, 1).data]);
            }""",
            [base64.b64encode(png).decode(), [list(point) for point in points]],
        )
    finally:
        await context.close()
    return [(row[0], row[1], row[2]) for row in found]


# -- a survey of the colours a page uses -----------------------------------------------------------

SURVEY = """() => {
  const found = new Map();
  const describe = (el) => el.localName + (el.id ? '#' + el.id : '')
    + [...el.classList].slice(0, 3).map((name) => '.' + name).join('');
  const note = (prop, value, el) => {
    if (!value || ['none', 'auto', 'normal', 'currentcolor'].includes(value)) return;
    const key = prop + ': ' + value;
    if (!found.has(key)) found.set(key, { prop, value, where: describe(el), count: 0 });
    found.get(key).count += 1;
  };
  const shown = (el) => {
    // Closed <details> and the like are not drawn, and the browser leaves their style as it was.
    if (el.checkVisibility && !el.checkVisibility({ visibilityProperty: true })) return false;
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return box.width > 0 && box.height > 0
      && style.visibility !== 'hidden' && style.display !== 'none';
  };
  for (const el of document.querySelectorAll('body *')) {
    const marker = el.closest('marker') !== null;
    if (!marker && !shown(el)) continue;
    const style = getComputedStyle(el);
    note('color', style.color, el);
    note('background-color', style.backgroundColor, el);
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
      const width = parseFloat(style['border' + side + 'Width']);
      if (width > 0 && style['border' + side + 'Style'] !== 'none') {
        note('border-' + side.toLowerCase() + '-color', style['border' + side + 'Color'], el);
      }
    }
    if (style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0) {
      note('outline-color', style.outlineColor, el);
    }
    if (el instanceof SVGGeometryElement || el instanceof SVGTextContentElement) {
      note('fill', style.fill, el);
      note('stroke', style.stroke, el);
    }
    if (style.accentColor !== 'auto') note('accent-color', style.accentColor, el);
    const colours = /rgba?\\([^)]*\\)/g;
    for (const colour of style.backgroundImage.match(colours) || []) {
      note('background-image', colour, el);
    }
    for (const colour of style.boxShadow.match(colours) || []) note('box-shadow', colour, el);
  }
  return [...found.values()];
}"""


async def survey(page: Page) -> list[dict[str, Any]]:
    """Every distinct colour the visible elements of the page are painted in (text, background,
    borders, outlines, SVG fill and stroke, gradients and shadows), with one element that has it."""
    found: list[dict[str, Any]] = await page.evaluate(SURVEY)
    return found


def foreign_colours(found: list[dict[str, Any]], theme: str) -> list[str]:
    """What in a survey is not painted from the tokens of `theme`: a colour whose red, green and
    blue are not those of any token (a transparent one is nothing)."""
    allowed = palette(theme)
    wrong: list[str] = []
    for each in found:
        parts = re.findall(r"rgba?\([^)]*\)|color\([^)]*\)", each["value"]) or [each["value"]]
        for text in parts:
            colour = parse_colour(text)
            if colour is None:
                wrong.append(f"{each['prop']} {each['value']!r} on {each['where']}")
            elif colour[3] > 0 and colour[:3] not in allowed:
                wrong.append(f"{each['prop']} {text} on {each['where']}")
    return wrong


_NAMED = """(selectors) => Object.fromEntries(Object.entries(selectors).map(([name, selector]) => {
  const el = document.querySelector(selector);
  if (!el) return [name, null];
  const style = getComputedStyle(el);
  const backdrop = el.localName === 'dialog' ? getComputedStyle(el, '::backdrop') : null;
  return [name, {
    color: style.color,
    background: style.backgroundColor,
    border: style.borderTopColor,
    borderWidth: style.borderTopWidth,
    fill: style.fill,
    stroke: style.stroke,
    scheme: style.colorScheme,
    scrollbar: style.scrollbarColor,
    backdrop: backdrop ? backdrop.backgroundColor : null,
  }];
}))"""


async def painted(page: Page, selectors: dict[str, str]) -> dict[str, dict[str, str] | None]:
    """The computed colours of the first element each selector finds (None if there is none)."""
    found: dict[str, dict[str, str] | None] = await page.evaluate(_NAMED, selectors)
    return found


def rgb_of(text: str) -> tuple[int, int, int]:
    """The (r, g, b) of a colour a browser reports."""
    colour = parse_colour(text)
    assert colour is not None, text
    return colour[:3]


# -- listening to the board ------------------------------------------------------------------------


def api_requests(requests: Sequence[str]) -> list[str]:
    """What the board asked its server for, as the path of each request without the project's
    number: '/board/api/projects/12/tool/list_nodes' is 'tool/list_nodes'."""
    asked: list[str] = []
    for url in requests:
        found = re.fullmatch(r"/board/api/projects/\d+/(.*)", urlparse(url).path)
        if found:
            asked.append(found.group(1))
    return asked


# -- getting to a page, and holding answers back ------------------------------------------------


async def entry(page: Page, address: str) -> None:
    """Go to an address with nobody signed in, and wait for the sign-in page."""
    await page.goto(address)
    await page.wait_for_selector(".entry", timeout=SLOW)


async def in_project(page: Page, address: str) -> None:
    """Go to an address in a project, signed in, and wait for the shell (sidebar and top bar)."""
    await page.goto(address)
    await page.wait_for_selector("#app[data-shell=project]", timeout=SLOW)


async def project_list(page: Page, address: str) -> None:
    """The person's own projects, which a signed-in person is shown at /board."""
    await page.goto(address)
    await page.wait_for_selector("#projects, #no-projects", timeout=SLOW)


async def at(page: Page, signed_in: bool, address: str) -> None:
    await (in_project(page, address) if signed_in else entry(page, address))


@dataclass
class Held:
    """Answers the board is waiting for: `urls` are the requests being held, and `release` lets them
    through (a held request goes on to the server only when it is released)."""

    release: asyncio.Event = field(default_factory=asyncio.Event)
    urls: list[str] = field(default_factory=list)

    async def arrived(self, count: int = 1) -> None:
        """Wait until `count` requests are being held."""
        for _ in range(200):
            if len(self.urls) >= count:
                return
            await asyncio.sleep(0.1)
        raise AssertionError(f"only {len(self.urls)} of {count} requests were held back")


async def hold_answers(
    page: Page,
    project: Project,
    tool: str,
    *,
    when: Callable[[str], bool] | None = None,
) -> Held:
    """From now on the board's calls of one tool of a project (`when` narrows them down by the text
    of the call) wait until the returned `Held` is released."""
    held = Held()

    async def hold(route: Route) -> None:
        text = route.request.post_data or ""
        if when is not None and not when(text):
            await route.continue_()
            return
        held.urls.append(route.request.url)
        await held.release.wait()
        with contextlib.suppress(Exception):
            await route.continue_()

    await page.route(f"**/board/api/projects/{project.id}/tool/{tool}", hold)
    return held
