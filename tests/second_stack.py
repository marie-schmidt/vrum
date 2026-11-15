"""A second, separate copy of Vrum for the tests that destroy or load a whole database: its own
compose project (built from compose.yaml alone, with nothing scripted), its own database volume, its
own port, its own token. ./check.sh gives it a port (VRUM_RESTORE_TEST_PORT) and removes it if a
test dies."""

import os
import random
import string
import subprocess
import sys
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from mcp import Client

from tests.helpers import REPO_ROOT, connect

PROJECT = "vrum-restore"


def _secret() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=40))


class SecondStack:
    def __init__(self) -> None:
        port = os.environ.get("VRUM_RESTORE_TEST_PORT")
        if not port:
            pytest.fail(
                "VRUM_RESTORE_TEST_PORT is not set. Run the integration tests through ./check.sh."
            )
        self.token = _secret()
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.env = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": PROJECT,
            "COMPOSE_FILE": "compose.yaml",
            "VRUM_DB_PASSWORD": _secret(),
            "VRUM_TOKEN": self.token,
            "VRUM_PORT": port,
            "VRUM_BIND_ADDRESS": "127.0.0.1",
        }

    def compose(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "compose", *arguments],
            cwd=REPO_ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )

    def run(self, *command: str | Path) -> subprocess.CompletedProcess[str]:
        """Run a script of this repository, aimed at this copy (it reads the token and the compose
        project from the environment)."""
        return subprocess.run(
            [str(part) for part in command],
            cwd=REPO_ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )

    def python(self, script: str, *arguments: str | Path) -> subprocess.CompletedProcess[str]:
        return self.run(sys.executable, REPO_ROOT / "scripts" / script, *arguments)

    def up(self) -> None:
        result = self.compose("up", "--detach", "--build", "--wait", "--wait-timeout", "240")
        assert result.returncode == 0, result.stdout + result.stderr

    def destroy(self) -> None:
        """Stop it and delete its database volume, as `docker compose down --volumes` does."""
        result = self.compose("down", "--volumes", "--remove-orphans")
        assert result.returncode == 0, result.stdout + result.stderr

    def close(self) -> None:
        self.compose("down", "--volumes", "--remove-orphans", "--rmi", "local")

    def client(self) -> AbstractAsyncContextManager[Client]:
        return connect(self.url, self.token)
