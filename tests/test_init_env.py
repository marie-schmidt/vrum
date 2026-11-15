"""scripts/init-env.sh: it writes a .env with a random database password and a random token, and
leaves an .env that is already set up alone."""

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


def test_it_writes_an_env_with_a_random_password_and_a_random_token(project: Path) -> None:
    result = run_init(project)

    env = project / ".env"
    assert result.returncode == 0, result.stderr
    found = values(env)
    assert re.fullmatch(r"[0-9a-f]{48}", found["VRUM_DB_PASSWORD"])
    assert re.fullmatch(r"[0-9a-f]{48}", found["VRUM_TOKEN"])
    assert found["VRUM_DB_PASSWORD"] != found["VRUM_TOKEN"]
    assert found["VRUM_PORT"] == "8000"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_two_runs_in_two_places_give_different_secrets(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other = copy_project(tmp_path_factory.mktemp("other"))

    run_init(project)
    run_init(other)

    first, second = values(project / ".env"), values(other / ".env")
    assert first["VRUM_TOKEN"] != second["VRUM_TOKEN"]
    assert first["VRUM_DB_PASSWORD"] != second["VRUM_DB_PASSWORD"]


def test_it_refuses_to_touch_an_env_that_already_has_a_token(project: Path) -> None:
    env = project / ".env"
    env.write_text("VRUM_DB_PASSWORD=keep-me\nVRUM_TOKEN=already-here-0123456789\n")

    result = run_init(project)

    assert result.returncode == 1 and "already exists and has a token" in result.stderr
    assert env.read_text() == "VRUM_DB_PASSWORD=keep-me\nVRUM_TOKEN=already-here-0123456789\n"


def test_it_adds_only_a_token_to_an_env_from_before_tokens_existed(project: Path) -> None:
    env = project / ".env"
    env.write_text("VRUM_DB_PASSWORD=keep-me\nVRUM_PORT=9000\n# a note of mine\n")

    result = run_init(project)

    assert result.returncode == 0, result.stderr
    text = env.read_text()
    assert text.startswith("VRUM_DB_PASSWORD=keep-me\nVRUM_PORT=9000\n# a note of mine\n")
    found = values(env)
    assert found["VRUM_DB_PASSWORD"] == "keep-me" and found["VRUM_PORT"] == "9000"
    assert re.fullmatch(r"[0-9a-f]{48}", found["VRUM_TOKEN"])
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_an_empty_token_line_is_replaced_not_repeated(project: Path) -> None:
    env = project / ".env"
    env.write_text("VRUM_DB_PASSWORD=keep-me\nVRUM_TOKEN=\n")

    result = run_init(project)

    assert result.returncode == 0, result.stderr
    assert env.read_text().count("VRUM_TOKEN=") == 1
    assert re.fullmatch(r"[0-9a-f]{48}", values(env)["VRUM_TOKEN"])
