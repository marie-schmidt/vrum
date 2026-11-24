"""What the Roadmap's M38 tests share: the box that fills the window, the cursor at a point, the
colour of a pixel of the page and the words of a status, read from the page's own source."""

import re
from typing import Any

from playwright.async_api import Locator, Page

from tests.board_theme import pixels
from vrum.board import STATIC


def status_words() -> dict[str, tuple[str, str]]:
    """Each status as the page words it in a hover card and in the Board's key, read from
    `STATUS` in labels.js so that the test cannot drift from the one place the words live:
    {'ready': ('Ready', 'Not started, and everything it needs first is done'), ...}."""
    source = (STATIC / "labels.js").read_text(encoding="utf-8")
    block = source.split("export const STATUS = {", 1)[1].split("};", 1)[0]
    found = re.findall(r"(\w+): \{ label: '([^']*)',.*?hint: '([^']*)'", block)
    assert len(found) == 6, found
    return {status: (label, hint) for status, label, hint in found}


BOX = """() => {
  const scroll = document.querySelector('#rm-scroll');
  const rows = document.querySelector('.rm-rows').getBoundingClientRect();
  const labels = document.querySelector('.rm-labels').getBoundingClientRect();
  const outer = scroll.getBoundingClientRect();
  const today = document.querySelector('.rm-today').getBoundingClientRect();
  const doc = document.documentElement;
  return {
    window: innerHeight, page: doc.scrollHeight, windowWide: innerWidth, pageWide: doc.scrollWidth,
    top: outer.top, bottom: outer.bottom,
    // the bottom and the right edge of what the box shows, without its border and its scroll bars
    inner: outer.top + scroll.clientTop + scroll.clientHeight,
    innerRight: outer.left + scroll.clientLeft + scroll.clientWidth,
    scrolls: scroll.scrollHeight > scroll.clientHeight + 1,
    rowsTop: rows.top, rowsBottom: rows.bottom,
    labelsBottom: labels.bottom, labelsRight: labels.right,
    todayLeft: today.left, todayWidth: today.width,
  };
}"""


async def box_of(page: Page) -> dict[str, Any]:
    """Where the timeline's box, its rows and its labels are, and how tall the window and the page
    are."""
    found: dict[str, Any] = await page.evaluate(BOX)
    return found


async def probe(page: Page, x: float, y: float) -> tuple[str, str]:
    """The element at a point of the window as `tag.class` and the cursor the pointer has over
    it."""
    found: list[str] = await page.evaluate(
        """([x, y]) => {
            const el = document.elementFromPoint(x, y);
            if (!el) return ['nothing', ''];
            const classes = [...el.classList].map((name) => '.' + name).join('');
            return [el.localName + classes, getComputedStyle(el).cursor];
        }""",
        [x, y],
    )
    return found[0], found[1]


async def middle(place: Locator) -> tuple[float, float]:
    """The middle of an element on the screen, after bringing it into view."""
    await place.scroll_into_view_if_needed()
    where = await place.bounding_box()
    assert where is not None
    return where["x"] + where["width"] / 2, where["y"] + where["height"] / 2


async def cursor_over(page: Page, place: Locator) -> tuple[str, str]:
    """What is at the middle of an element and the cursor over it."""
    x, y = await middle(place)
    return await probe(page, x, y)


async def colours_in(page: Page, points: list[tuple[int, int]]) -> list[tuple[int, int, int]]:
    """The colour of each point of the window, as the browser painted it."""
    return await pixels(page, await page.screenshot(), points)


async def show_today(page: Page) -> None:
    """Bring today's column to the middle of the timeline, sideways only."""
    await page.locator(".rm-today").evaluate(
        "e => { const box = document.querySelector('#rm-scroll');"
        " box.scrollLeft += e.getBoundingClientRect().left - (box.getBoundingClientRect().left"
        " + box.clientWidth / 2); }"
    )


def close(found: tuple[int, int, int], wanted: tuple[int, int, int]) -> bool:
    """The same colour, give or take the rounding a screenshot may bring."""
    return all(abs(a - b) <= 2 for a, b in zip(found, wanted, strict=True))
