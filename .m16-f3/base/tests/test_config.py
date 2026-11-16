from pathlib import Path

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


def test_there_is_no_embedding_service_unless_one_is_set_up() -> None:
    assert Settings.from_env(FULL_ENV).embedding is None


def test_an_embedding_service_needs_a_url_and_a_model_together() -> None:
    with pytest.raises(ConfigError, match="EMBEDDING_MODEL is not set"):
        Settings.from_env({**FULL_ENV, "EMBEDDING_BASE_URL": "http://embeddings:8080/v1"})
    with pytest.raises(ConfigError, match="EMBEDDING_BASE_URL is not set"):
        Settings.from_env({**FULL_ENV, "EMBEDDING_MODEL": "a-model"})
    with pytest.raises(ConfigError, match="must start with http"):
        Settings.from_env(
            {**FULL_ENV, "EMBEDDING_BASE_URL": "embeddings:8080", "EMBEDDING_MODEL": "m"}
        )


def test_embedding_settings_have_defaults_and_can_be_changed() -> None:
    base = {**FULL_ENV, "EMBEDDING_BASE_URL": "http://embeddings:8080/v1/", "EMBEDDING_MODEL": "m"}
    embedding = Settings.from_env(base).embedding
    assert embedding is not None
    assert embedding.base_url == "http://embeddings:8080/v1" and embedding.model == "m"
    assert embedding.api_key is None
    assert (embedding.max_chars, embedding.min_similarity) == (2000, 0.35)
    assert (embedding.overlap_threshold, embedding.reindex_seconds) == (0.55, 5.0)

    changed = Settings.from_env(
        {
            **base,
            "EMBEDDING_MAX_CHARS": "500",
            "EMBEDDING_MIN_SIMILARITY": "0.4",
            "EMBEDDING_OVERLAP_THRESHOLD": "0.9",
            "EMBEDDING_REINDEX_SECONDS": "1",
            "EMBEDDING_API_KEY": "a-key",
        }
    ).embedding
    assert changed is not None and changed.api_key == "a-key"
    assert (changed.max_chars, changed.min_similarity) == (500, 0.4)
    assert (changed.overlap_threshold, changed.reindex_seconds) == (0.9, 1.0)
    assert "a-key" not in repr(changed)


@pytest.mark.parametrize(
    ("name", "bad", "message"),
    [
        ("EMBEDDING_MAX_CHARS", "ten", "must be a whole number of 100 or more"),
        ("EMBEDDING_MAX_CHARS", "50", "must be a whole number of 100 or more"),
        ("EMBEDDING_MIN_SIMILARITY", "1.5", "must be a number from 0 to 1"),
        ("EMBEDDING_MIN_SIMILARITY", "high", "must be a number from 0 to 1"),
        ("EMBEDDING_OVERLAP_THRESHOLD", "-0.1", "must be a number from 0 to 1"),
        ("EMBEDDING_REINDEX_SECONDS", "0", "must be a number of seconds, 0.5 or more"),
    ],
)
def test_embedding_settings_that_make_no_sense_are_refused_in_plain_words(
    name: str, bad: str, message: str
) -> None:
    env = {
        **FULL_ENV,
        "EMBEDDING_BASE_URL": "http://embeddings:8080/v1",
        "EMBEDDING_MODEL": "m",
        name: bad,
    }
    with pytest.raises(ConfigError, match=f"{name} {message}"):
        Settings.from_env(env)


def test_the_embedding_key_can_come_from_a_file(tmp_path: Path) -> None:
    key_file = tmp_path / "key"
    key_file.write_text("  from-a-file\n")
    base = {**FULL_ENV, "EMBEDDING_BASE_URL": "http://e/v1", "EMBEDDING_MODEL": "m"}

    embedding = Settings.from_env({**base, "EMBEDDING_API_KEY_FILE": str(key_file)}).embedding
    assert embedding is not None and embedding.api_key == "from-a-file"
    with pytest.raises(ConfigError, match="Set only one of EMBEDDING_API_KEY and"):
        Settings.from_env(
            {**base, "EMBEDDING_API_KEY": "x", "EMBEDDING_API_KEY_FILE": str(key_file)}
        )
    with pytest.raises(ConfigError, match="cannot be read"):
        Settings.from_env({**base, "EMBEDDING_API_KEY_FILE": str(tmp_path / "missing")})
    empty = tmp_path / "empty"
    empty.write_text("\n")
    with pytest.raises(ConfigError, match="which is empty"):
        Settings.from_env({**base, "EMBEDDING_API_KEY_FILE": str(empty)})


def test_there_is_no_language_model_unless_one_is_set_up() -> None:
    assert Settings.from_env(FULL_ENV).llm is None


def test_a_language_model_needs_a_url_and_a_model_together() -> None:
    with pytest.raises(ConfigError, match="LLM_MODEL is not set"):
        Settings.from_env({**FULL_ENV, "LLM_BASE_URL": "http://model:8080/v1"})
    with pytest.raises(ConfigError, match="LLM_BASE_URL is not set"):
        Settings.from_env({**FULL_ENV, "LLM_MODEL": "a-model"})
    with pytest.raises(ConfigError, match="must start with http"):
        Settings.from_env({**FULL_ENV, "LLM_BASE_URL": "model:8080", "LLM_MODEL": "m"})


def test_language_model_settings_have_defaults_and_the_key_stays_out_of_sight(
    tmp_path: Path,
) -> None:
    base = {**FULL_ENV, "LLM_BASE_URL": "http://model:8080/v1/", "LLM_MODEL": "m"}
    llm = Settings.from_env(base).llm
    assert llm is not None and llm.base_url == "http://model:8080/v1" and llm.api_key is None
    assert (llm.timeout_seconds, llm.max_input_chars) == (120.0, 12000)

    key_file = tmp_path / "key"
    key_file.write_text("a-key-in-a-file\n")
    changed = Settings.from_env(
        {**base, "LLM_TIMEOUT_SECONDS": "5", "LLM_MAX_INPUT_CHARS": "2000", "LLM_API_KEY": "k"}
    ).llm
    assert changed is not None and changed.api_key == "k"
    assert (changed.timeout_seconds, changed.max_input_chars) == (5.0, 2000)
    assert "k'" not in repr(changed)
    from_file = Settings.from_env({**base, "LLM_API_KEY_FILE": str(key_file)}).llm
    assert from_file is not None and from_file.api_key == "a-key-in-a-file"
    assert "a-key-in-a-file" not in repr(from_file)
    with pytest.raises(ConfigError, match="LLM_MAX_INPUT_CHARS must be a whole number of 1000"):
        Settings.from_env({**base, "LLM_MAX_INPUT_CHARS": "10"})
    with pytest.raises(ConfigError, match="LLM_TIMEOUT_SECONDS must be a number of seconds"):
        Settings.from_env({**base, "LLM_TIMEOUT_SECONDS": "soon"})


def test_the_server_needs_no_token_to_start_and_a_leftover_one_changes_nothing() -> None:
    # People sign in with accounts and agents use project keys: there is nothing install-wide to
    # configure, and an old VRUM_TOKEN in the environment is simply not read.
    assert Settings.from_env({"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret-value"})
    with_leftover = Settings.from_env({**FULL_ENV, "VRUM_TOKEN": "left-over-from-before-m15"})
    assert not hasattr(with_leftover, "token")
    assert "left-over-from-before-m15" not in repr(with_leftover)
    assert "left-over-from-before-m15" not in with_leftover.secrets()


def test_the_secrets_a_server_holds_are_listed_so_that_logs_can_be_scrubbed() -> None:
    env = {
        **FULL_ENV,
        "EMBEDDING_BASE_URL": "http://e/v1",
        "EMBEDDING_MODEL": "m",
        "EMBEDDING_API_KEY": "embedding-key-value",
        "LLM_BASE_URL": "http://l/v1",
        "LLM_MODEL": "m",
        "LLM_API_KEY": "model-key-value",
    }
    assert set(Settings.from_env(env).secrets()) == {
        "s3cret-value",
        "embedding-key-value",
        "model-key-value",
    }


def test_the_request_size_limit_has_a_default_and_can_be_changed() -> None:
    assert Settings.from_env(FULL_ENV).max_request_bytes == 4 * 1024 * 1024
    assert (
        Settings.from_env({**FULL_ENV, "VRUM_MAX_REQUEST_BYTES": "5000"}).max_request_bytes == 5000
    )
    with pytest.raises(ConfigError, match="VRUM_MAX_REQUEST_BYTES must be a whole number of 1000"):
        Settings.from_env({**FULL_ENV, "VRUM_MAX_REQUEST_BYTES": "10"})
