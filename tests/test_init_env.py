"""scripts/init-env.sh: it writes a .env with a random database password and nothing else to make up
(there is no token: people sign in with accounts and agents use project keys), and leaves an .env
that already exists alone."""

import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def copy_project(path: Path) -> Path:
    (path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "init-env.sh", path / "scripts" / "init-env.sh")
    shutil.copy(ROOT / ".env.example", path / ".env.example")
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A copy of the script and the example file, so that nothing touches the real .env."""
    return copy_project(tmp_path)


def run_init(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(project / "scripts" / "init-env.sh")],
        capture_output=True,
        text=True,
        check=False,
    )


def values(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if re.match(r"^[A-Z_]+=", line):
            name, _, value = line.partition("=")
            found[name] = value
    return found


def test_it_writes_an_env_with_a_random_password_and_no_token(project: Path) -> None:
    result = run_init(project)

    env = project / ".env"
    assert result.returncode == 0, result.stderr
    found = values(env)
    assert re.fullmatch(r"[0-9a-f]{48}", found["VRUM_DB_PASSWORD"])
    assert "VRUM_TOKEN" not in found and "VRUM_TOKEN" not in env.read_text().replace(
        "There is no token", ""
    )
    assert found["VRUM_PORT"] == "8000"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_two_runs_in_two_places_give_different_passwords(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other = copy_project(tmp_path_factory.mktemp("other"))

    run_init(project)
    run_init(other)

    first, second = values(project / ".env"), values(other / ".env")
    assert first["VRUM_DB_PASSWORD"] != second["VRUM_DB_PASSWORD"]


def test_it_refuses_to_touch_an_env_that_already_exists(project: Path) -> None:
    env = project / ".env"
    env.write_text("VRUM_DB_PASSWORD=keep-me\nVRUM_PORT=9000\n# a note of mine\n")

    result = run_init(project)

    assert result.returncode == 1 and "already exists" in result.stderr
    assert env.read_text() == "VRUM_DB_PASSWORD=keep-me\nVRUM_PORT=9000\n# a note of mine\n"
