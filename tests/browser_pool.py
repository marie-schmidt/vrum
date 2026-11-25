"""Shared Chromium browser pool for Playwright tests and the acceptance walk.

Launching a fresh Playwright driver and Chromium OS process per test takes ~1-1.5 seconds each.
Inside a session-scoped `browser_pool_session()` (active for pytest via `tests/conftest.py` and
for `./acceptance.sh` via `tests/acceptance.py`), `shared_browser()` lazily launches Chromium once
and reuses that process across every test, while each caller creates and closes its own isolated
`BrowserContext` (separate cookies, storage, cache, viewport, color scheme and routes).

Outside a session (or from a different event loop), `shared_browser()` safely falls back to a
one-shot browser so standalone calls never leak processes or hit cross-loop errors."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from playwright.async_api import Browser, Playwright, async_playwright

_pool_loop: asyncio.AbstractEventLoop | None = None
_pool_lock: asyncio.Lock | None = None
_playwright: Playwright | None = None
_browser: Browser | None = None


async def enable_browser_pool() -> None:
    """Enable shared Chromium reuse on the current running event loop."""
    global _pool_loop, _pool_lock
    _pool_loop = asyncio.get_running_loop()
    _pool_lock = asyncio.Lock()


async def close_shared_browser() -> None:
    """Close the shared Chromium browser and Playwright driver on the pool's event loop."""
    global _pool_loop, _pool_lock, _playwright, _browser
    browser, playwright = _browser, _playwright
    _browser = None
    _playwright = None
    _pool_lock = None
    _pool_loop = None
    if browser is not None:
        await browser.close()
    if playwright is not None:
        await playwright.stop()


@asynccontextmanager
async def browser_pool_session() -> AsyncIterator[None]:
    """Enable shared Chromium reuse on the current event loop and close it cleanly at teardown."""
    global _pool_loop, _pool_lock
    previous_loop, previous_lock = _pool_loop, _pool_lock
    await enable_browser_pool()
    try:
        yield
    finally:
        await close_shared_browser()
        _pool_loop = previous_loop
        _pool_lock = previous_lock


@asynccontextmanager
async def shared_browser() -> AsyncIterator[Browser]:
    """Yield the shared Chromium browser when inside an active session on this event loop, or a
    fresh one-shot browser otherwise. Callers must create and close their own `BrowserContext`."""
    global _playwright, _browser
    loop = asyncio.get_running_loop()
    if _pool_loop is loop and _pool_lock is not None:
        async with _pool_lock:
            if _browser is None or not _browser.is_connected():
                if _playwright is None:
                    _playwright = await async_playwright().start()
                _browser = await _playwright.chromium.launch()
            browser = _browser
        yield browser
        return
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            yield browser
        finally:
            await browser.close()
