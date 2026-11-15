import pytest

from vrum.config import ConfigError, Settings

FULL_ENV = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret-value"}


def test_defaults_apply_when_only_the_required_values_are_set() -> None:
    settings = Settings.from_env(FULL_ENV)
    assert (settings.host, settings.port) == ("127.0.0.1", 8000)
    assert (settings.db_port, settings.db_name, settings.db_user) == (5432, "vrum", "vrum")


def test_there_is_no_default_database_password() -> None:
    with pytest.raises(ConfigError, match="VRUM_DB_PASSWORD is not set"):
        Settings.from_env({"VRUM_DB_HOST": "db"})


def test_a_blank_database_host_is_refused() -> None:
    with pytest.raises(ConfigError, match="VRUM_DB_HOST is not set"):
        Settings.from_env({**FULL_ENV, "VRUM_DB_HOST": "  "})


@pytest.mark.parametrize("bad", ["abc", "0", "70000", "-1", "80.5"])
def test_a_port_that_is_not_a_valid_number_is_refused_in_plain_words(bad: str) -> None:
    with pytest.raises(ConfigError, match="VRUM_PORT must be a whole number from 1 to 65535"):
        Settings.from_env({**FULL_ENV, "VRUM_PORT": bad})


def test_the_password_never_appears_when_settings_are_printed() -> None:
    assert "s3cret-value" not in repr(Settings.from_env(FULL_ENV))
