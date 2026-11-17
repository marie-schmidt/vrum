"""Starting Vrum with nothing set up: no .env and no database password anywhere, as on a fresh
clone. It comes up, makes its own random password, keeps it and the data across a restart, lets a
person choose the password before the first start, and says in plain words what is wrong when the
password file is gone while the data is still there.

Each test starts a second, separate copy of Vrum (tests/second_stack.py) that no password was given
to, and removes it afterwards (see ./check.sh)."""

import re
import subprocess
from collections.abc import Iterator

import pytest

from tests.helpers import ok
from tests.second_stack import PROJECT, SecondStack

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SECRETS_VOLUME = f"{PROJECT}_vrum-secrets"


@pytest.fixture
def stack() -> Iterator[SecondStack]:
    second = SecondStack(nothing_set=True)
    try:
        yield second
    finally:
        second.close()


def password_of(stack: SecondStack) -> str:
    """What the stack keeps as its database password, read from the file the server reads."""
    result = stack.compose("exec", "-T", "app", "cat", "/secrets/db-password")
    assert result.returncode == 0, result.stderr
    return result.stdout


def start_again(stack: SecondStack) -> None:
    assert stack.compose("down").returncode == 0
    result = stack.compose("up", "--detach", "--wait", "--wait-timeout", "240")
    assert result.returncode == 0, result.stdout + result.stderr


async def test_it_starts_with_nothing_set_and_keeps_its_own_password_and_its_data(
    stack: SecondStack,
) -> None:
    config = stack.compose("config")
    assert config.returncode == 0, "compose.yaml does not start without a setting"
    stack.up()

    password = password_of(stack)
    assert re.fullmatch(r"[0-9a-f]{48}", password), "it is not a random password of its own"
    logs = stack.compose("logs", "--no-color").stdout
    assert password not in logs, "the database password is in a log"
    async with stack.client() as client:
        assert (await ok(client, "server_status"))["postgres_version"]
        made = await ok(client, "create_node", actor="agent-1", kind="epic", title="Kept")

    # Stopping and starting again keeps the password and everything in the database.
    start_again(stack)
    assert password_of(stack) == password
    async with stack.client() as client:
        assert (await ok(client, "get_node", node_id=made["id"]))["title"] == "Kept"

    # Deleting the data too is a new install, with a new password.
    stack.destroy()
    stack.up()
    assert password_of(stack) != password


async def test_a_password_chosen_before_the_first_start_is_used_and_a_later_one_is_ignored() -> (
    None
):
    chosen = "chosen-" + "x" * 30
    stack = SecondStack(chosen, nothing_set=True)
    stack.env["VRUM_DB_PASSWORD"] = chosen
    try:
        stack.up()
        assert password_of(stack) == chosen

        # Changing the value afterwards must not lock the server out of its own database.
        stack.env["VRUM_DB_PASSWORD"] = "something-else-entirely"
        start_again(stack)
        assert password_of(stack) == chosen
        async with stack.client() as client:
            assert (await ok(client, "server_status"))["postgres_version"]
    finally:
        stack.close()


async def test_a_database_that_lost_its_password_file_says_so_in_plain_words(
    stack: SecondStack,
) -> None:
    stack.up()
    assert stack.compose("down").returncode == 0
    removed = subprocess.run(
        ["docker", "volume", "rm", SECRETS_VOLUME], capture_output=True, text=True, check=False
    )
    assert removed.returncode == 0, removed.stderr

    result = stack.compose("up", "--detach", "--wait", "--wait-timeout", "60")
    assert result.returncode != 0, "it started with a database it could not open"
    told = stack.compose("logs", "--no-color", "db").stdout
    assert "already exists, but the file that holds its password is gone" in told
    assert "docker compose down --volumes" in told
