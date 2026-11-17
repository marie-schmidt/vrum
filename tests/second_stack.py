"""A second, separate copy of Vrum for the tests that destroy or load a whole database: its own
compose project (built from compose.yaml alone, with nothing scripted), its own database volume, its
own port, its own account, project and agent key. ./check.sh gives it a port
(VRUM_RESTORE_TEST_PORT) and removes it if a test dies."""

import os
import random
import shutil
import string
import subprocess
import sys
import tempfile
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from mcp import Client

from tests.helpers import REPO_ROOT, connect
from tests.world import Project, make_project, register

# ./check.sh removes this project if a test dies. Someone running several stacks at once on one
# machine names a project of their own, so that their second copies do not remove each other.
PROJECT = os.environ.get("VRUM_RESTORE_PROJECT", "vrum-restore")


def _secret() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=40))


class SecondStack:
    """With no arguments, a copy with a random database password of its own. `password` names the
    one to start it with; `nothing_set` starts it as on a new install, with no database password
    given anywhere (and no .env read), so that Vrum makes its own."""

    def __init__(self, password: str | None = None, *, nothing_set: bool = False) -> None:
        port = os.environ.get("VRUM_RESTORE_TEST_PORT")
        if not port:
            pytest.fail(
                "VRUM_RESTORE_TEST_PORT is not set. Run the integration tests through ./check.sh."
            )
        self.base = f"http://127.0.0.1:{port}"
        self.url = f"{self.base}/mcp"
        # Made by up(), through this copy's own board API, once it is running.
        self.project: Project | None = None
        self.env = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": PROJECT,
            "COMPOSE_FILE": "compose.yaml",
            "VRUM_DB_PASSWORD": password or _secret(),
            "VRUM_PORT": port,
            "VRUM_BIND_ADDRESS": "127.0.0.1",
        }
        self.env.pop("VRUM_AGENT_KEY", None)
        self.env_file: Path | None = None
        if nothing_set:
            self.env.pop("VRUM_DB_PASSWORD")
            # An empty file stands in for the .env that a checkout may or may not have.
            self.env_file = Path(tempfile.mkdtemp(prefix="vrum-no-env-")) / "empty.env"
            self.env_file.write_text("", encoding="utf-8")

    @property
    def key(self) -> str:
        """The agent key of this copy's own project (made by up())."""
        assert self.project is not None, "up() makes the project"
        return self.project.key

    def compose(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        before = ["--env-file", str(self.env_file)] if self.env_file else []
        return subprocess.run(
            ["docker", "compose", *before, *arguments],
            cwd=REPO_ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )

    def run(self, *command: str | Path) -> subprocess.CompletedProcess[str]:
        """Run a script of this repository, aimed at this copy (it reads the agent key and the
        compose project from the environment)."""
        return subprocess.run(
            [str(part) for part in command],
            cwd=REPO_ROOT,
            env={**self.env, "VRUM_AGENT_KEY": self.key} if self.project else self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )

    def python(self, script: str, *arguments: str | Path) -> subprocess.CompletedProcess[str]:
        return self.run(sys.executable, REPO_ROOT / "scripts" / script, *arguments)

    def up(self) -> None:
        """Start it, and make a person, a project and an agent key in it, as on a new install."""
        result = self.compose("up", "--detach", "--build", "--wait", "--wait-timeout", "240")
        assert result.returncode == 0, result.stdout + result.stderr
        self.project = make_project(register(self.base, "Second stack owner"), "Second stack")

    def destroy(self) -> None:
        """Stop it and delete its database volume, as `docker compose down --volumes` does."""
        result = self.compose("down", "--volumes", "--remove-orphans")
        assert result.returncode == 0, result.stdout + result.stderr

    def close(self) -> None:
        self.compose("down", "--volumes", "--remove-orphans", "--rmi", "local")
        if self.env_file:
            shutil.rmtree(self.env_file.parent, ignore_errors=True)

    def client(self) -> AbstractAsyncContextManager[Client]:
        return connect(self.url, self.key)
