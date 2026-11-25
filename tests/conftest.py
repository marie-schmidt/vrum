import os
from collections.abc import AsyncIterator, Iterator

import pytest
from anyio.pytest_plugin import get_runner
from mcp import Client

from tests.browser_pool import close_shared_browser, enable_browser_pool
from tests.helpers import connect, the_token


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def _shared_browser_session() -> Iterator[None]:
    """Keep one session-scoped anyio loop and lazily shared Chromium process for browser tests.
    Sync so non-anyio tests (including sync Playwright and unit tests) never trip pytest's
    sync-depending-on-async-fixture guard."""
    with get_runner("asyncio", {}) as runner:
        runner.run_test(enable_browser_pool, {})
        try:
            yield
        finally:
            runner.run_test(close_shared_browser, {})


@pytest.fixture
def server_url() -> str:
    """The MCP endpoint of the running containers, set by ./check.sh."""
    url = os.environ.get("VRUM_TEST_URL")
    if not url:
        pytest.fail("VRUM_TEST_URL is not set. Run the integration tests through ./check.sh.")
    return url


@pytest.fixture
async def client(server_url: str) -> AsyncIterator[Client]:
    async with connect(server_url, the_token()) as connected:
        yield connected
