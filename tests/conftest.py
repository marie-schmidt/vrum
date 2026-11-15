import os
from collections.abc import AsyncIterator

import pytest
from mcp import Client

from tests.helpers import ScriptedModel, connect, the_token


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def model() -> ScriptedModel:
    """The scripted language model of the check stack."""
    return ScriptedModel()


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


@pytest.fixture
async def no_model_client() -> AsyncIterator[Client]:
    """A client of the second server, the one with no language model set up."""
    url = os.environ.get("VRUM_NO_MODEL_TEST_URL")
    if not url:
        pytest.fail(
            "VRUM_NO_MODEL_TEST_URL is not set. Run the integration tests through ./check.sh."
        )
    async with connect(url, the_token()) as connected:
        yield connected
