"""There are no migrations: the database is built from schema.sql, and one built from any other
version of it is refused in plain words (see ./check.sh)."""

import pytest

from tests.helpers import compose, run_sql

pytestmark = pytest.mark.integration


def start_another_server() -> tuple[int, str]:
    """Start a second server container against the same database and see what it says."""
    result = compose("run", "--rm", "--no-deps", "app")
    return result.returncode, result.stderr


def assert_refused_with_how_to_rebuild(code: int, said: str) -> None:
    assert code == 2, said
    assert "vrum cannot start" in said
    assert "different version of the schema" in said
    assert "docker compose down --volumes" in said


def test_a_database_built_from_a_different_schema_is_refused_with_how_to_rebuild_it() -> None:
    original = run_sql("SELECT schema_hash FROM schema_info", plain=True, project=None)
    original_hash = original.stdout.strip()
    assert len(original_hash) == 64
    try:
        change = run_sql("UPDATE schema_info SET schema_hash = 'an-older-schema'", project=None)
        assert change.returncode == 0
        assert_refused_with_how_to_rebuild(*start_another_server())
    finally:
        run_sql(f"UPDATE schema_info SET schema_hash = '{original_hash}'", project=None)


def test_a_database_with_tables_but_no_schema_marker_is_refused_too() -> None:
    try:
        rename = run_sql("ALTER TABLE schema_info RENAME TO schema_info_hidden", project=None)
        assert rename.returncode == 0
        assert_refused_with_how_to_rebuild(*start_another_server())
    finally:
        run_sql("ALTER TABLE schema_info_hidden RENAME TO schema_info", project=None)
