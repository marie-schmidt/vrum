"""Settings read from the environment. Nothing here has a default secret."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from psycopg.conninfo import make_conninfo


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


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str = field(repr=False)

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
        )

    def database_conninfo(self) -> str:
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.db_user,
            password=self.db_password,
            connect_timeout=5,
        )
