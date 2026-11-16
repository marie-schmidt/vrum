"""Settings read from the environment. Nothing here has a default secret."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from psycopg.conninfo import make_conninfo

# How alike (0 to 1, cosine) two texts must be to be found by meaning, and to be reported as
# overlapping. Scores depend on the model, so these are measured for the local model that
# compose.yaml offers (all-minilm), on reworded requirements: duplicates scored 0.54 to 0.87,
# related but different ones 0.30 to 0.49, and unrelated ones 0.35 at most; short questions scored
# 0.36 to 0.66 against the story they were about. They are a starting point, small samples, and
# differ by model: for another model, measure and set EMBEDDING_MIN_SIMILARITY and
# EMBEDDING_OVERLAP_THRESHOLD.
DEFAULT_MIN_SIMILARITY = 0.35
DEFAULT_OVERLAP_THRESHOLD = 0.55
# The most bytes one request may hold (4 MiB, a few thousand plan items or a few hundred pages of
# text). It is the only limit on what one write may contain.
DEFAULT_MAX_REQUEST_BYTES = 4 * 1024 * 1024
# How often the board asks for what changed. An agent's work shows on the board within this long.
DEFAULT_BOARD_POLL_SECONDS = 5.0


class ConfigError(Exception):
    """The environment is missing something or holds a value that cannot be used."""


def _required(env: Mapping[str, str], name: str, hint: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set. {hint}")
    return value


def _port(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdecimal() or not 1 <= int(raw) <= 65535:
        raise ConfigError(f"{name} must be a whole number from 1 to 65535, but it is {raw!r}.")
    return int(raw)


def _whole_number(env: Mapping[str, str], name: str, default: int, smallest: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdecimal() or int(raw) < smallest:
        raise ConfigError(
            f"{name} must be a whole number of {smallest} or more, but it is {raw!r}."
        )
    return int(raw)


def _fraction(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if not 0.0 <= value <= 1.0:
        raise ConfigError(f"{name} must be a number from 0 to 1, but it is {raw!r}.")
    return value


def _seconds(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if value < 0.5:
        raise ConfigError(f"{name} must be a number of seconds, 0.5 or more, but it is {raw!r}.")
    return value


def _secret(env: Mapping[str, str], name: str) -> str | None:
    """A secret given directly in NAME, or in the file that NAME_FILE names. Never both."""
    direct = env.get(name, "").strip()
    path = env.get(f"{name}_FILE", "").strip()
    if direct and path:
        raise ConfigError(f"Set only one of {name} and {name}_FILE, not both.")
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError as error:
            raise ConfigError(
                f"{name}_FILE is {path!r}, which cannot be read ({error})."
            ) from error
        if not value:
            raise ConfigError(f"{name}_FILE is {path!r}, which is empty.")
        return value
    return direct or None


@dataclass(frozen=True)
class EmbeddingSettings:
    """The embedding service: any OpenAI-compatible one, local or not. Without it, exact-words
    search works and everything that needs meaning says so."""

    base_url: str
    model: str
    api_key: str | None = field(repr=False)
    # The longest text sent to the model for one item. Longer text is cut, the title first.
    max_chars: int
    # How alike two texts must be (0 to 1, cosine) to be found by meaning.
    min_similarity: float
    # How alike two items must be to be reported as overlapping.
    overlap_threshold: float
    # How often the background job looks for items that are waiting to be indexed.
    reindex_seconds: float

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "EmbeddingSettings | None":
        base_url = env.get("EMBEDDING_BASE_URL", "").strip()
        model = env.get("EMBEDDING_MODEL", "").strip()
        if not base_url and not model:
            return None
        if not base_url or not model:
            missing = "EMBEDDING_BASE_URL" if not base_url else "EMBEDDING_MODEL"
            raise ConfigError(
                f"{missing} is not set. EMBEDDING_BASE_URL and EMBEDDING_MODEL go together: set "
                "both to use an embedding service, or neither to go without one."
            )
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError(
                f"EMBEDDING_BASE_URL must start with http:// or https://, but it is {base_url!r}."
            )
        return cls(
            base_url=base_url.rstrip("/"),
            model=model,
            api_key=_secret(env, "EMBEDDING_API_KEY"),
            max_chars=_whole_number(env, "EMBEDDING_MAX_CHARS", 2000, 100),
            min_similarity=_fraction(env, "EMBEDDING_MIN_SIMILARITY", DEFAULT_MIN_SIMILARITY),
            overlap_threshold=_fraction(
                env, "EMBEDDING_OVERLAP_THRESHOLD", DEFAULT_OVERLAP_THRESHOLD
            ),
            reindex_seconds=_seconds(env, "EMBEDDING_REINDEX_SECONDS", 5.0),
        )


@dataclass(frozen=True)
class LanguageModelSettings:
    """The language model: any OpenAI-compatible chat service, local or not. Without it, the tools
    that need a model say so, and everything else works."""

    base_url: str
    model: str
    api_key: str | None = field(repr=False)
    # How long to wait for one answer.
    timeout_seconds: float
    # The most text sent to the model in one request. More than this is not sent.
    max_input_chars: int

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "LanguageModelSettings | None":
        base_url = env.get("LLM_BASE_URL", "").strip()
        model = env.get("LLM_MODEL", "").strip()
        if not base_url and not model:
            return None
        if not base_url or not model:
            missing = "LLM_BASE_URL" if not base_url else "LLM_MODEL"
            raise ConfigError(
                f"{missing} is not set. LLM_BASE_URL and LLM_MODEL go together: set both to use "
                "a language model, or neither to go without one."
            )
        if not base_url.startswith(("http://", "https://")):
            raise ConfigError(
                f"LLM_BASE_URL must start with http:// or https://, but it is {base_url!r}."
            )
        return cls(
            base_url=base_url.rstrip("/"),
            model=model,
            api_key=_secret(env, "LLM_API_KEY"),
            timeout_seconds=_seconds(env, "LLM_TIMEOUT_SECONDS", 120.0),
            # About 3,000 tokens: it fits the context Ollama gives a model by default (4,096), with
            # room for the answer. A model with a larger context can be given a larger limit.
            max_input_chars=_whole_number(env, "LLM_MAX_INPUT_CHARS", 12000, 1000),
        )


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str = field(repr=False)
    # The most bytes one request may hold. A bigger one is refused with a plain message.
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    # How often the board asks the server for what changed, in seconds.
    board_poll_seconds: float = DEFAULT_BOARD_POLL_SECONDS
    embedding: EmbeddingSettings | None = None
    llm: LanguageModelSettings | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        return cls(
            host=env.get("VRUM_HOST", "").strip() or "127.0.0.1",
            port=_port(env, "VRUM_PORT", 8000),
            db_host=_required(
                env, "VRUM_DB_HOST", "Set it to the name or address of the PostgreSQL server."
            ),
            db_port=_port(env, "VRUM_DB_PORT", 5432),
            db_name=env.get("VRUM_DB_NAME", "").strip() or "vrum",
            db_user=env.get("VRUM_DB_USER", "").strip() or "vrum",
            db_password=_required(
                env,
                "VRUM_DB_PASSWORD",
                "There is no default password; run ./scripts/init-env.sh to create one in .env.",
            ),
            max_request_bytes=_whole_number(
                env, "VRUM_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES, 1000
            ),
            board_poll_seconds=_seconds(env, "VRUM_BOARD_POLL_SECONDS", DEFAULT_BOARD_POLL_SECONDS),
            embedding=EmbeddingSettings.from_env(env),
            llm=LanguageModelSettings.from_env(env),
        )

    def secrets(self) -> tuple[str, ...]:
        """Every secret value this server holds, so that nothing it writes can contain one."""
        found = [self.db_password]
        for service in (self.embedding, self.llm):
            if service is not None and service.api_key:
                found.append(service.api_key)
        return tuple(found)

    def database_conninfo(self) -> str:
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.db_user,
            password=self.db_password,
            connect_timeout=5,
        )
