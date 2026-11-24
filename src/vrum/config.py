"""Settings read from the environment. Nothing here has a default secret."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.utils import getaddresses, parseaddr
from urllib.parse import urlparse

from psycopg.conninfo import make_conninfo

from vrum.mail import is_plain_address

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
# Where the sandbox service listens (a volume shared with this server, see compose.yaml).
DEFAULT_SANDBOX_SOCKET = "/run/sandbox/run.sock"


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


SMTP_SECURITIES = ("starttls", "ssl", "none")
_MAIL_SETTINGS = (
    "VRUM_SMTP_PORT",
    "VRUM_SMTP_SECURITY",
    "VRUM_SMTP_USER",
    "VRUM_SMTP_PASSWORD",
    "VRUM_SMTP_PASSWORD_FILE",
    "VRUM_SMTP_FROM",
)


def _sender(raw: str) -> tuple[str, str]:
    """The name and the address in VRUM_SMTP_FROM, written `Name <address>` or just `address`."""
    name, address = parseaddr(raw)
    name, address = name.strip(), address.strip()
    if (
        not raw.isprintable()
        or len(getaddresses([raw])) != 1
        or not is_plain_address(address)
        or any(ch in name for ch in '<>"\\')
    ):
        raise ConfigError(
            "VRUM_SMTP_FROM must be the address messages are sent from, written like "
            "vrum@example.com or Vrum <vrum@example.com>."
        )
    return name, address


@dataclass(frozen=True)
class MailSettings:
    """The mail server Vrum sends its few messages through (invitations, confirming an address,
    choosing a new password). Without it nothing is sent, and none of those exist."""

    host: str
    port: int
    # starttls (an ordinary connection that is made secure), ssl (secure from the first byte,
    # usually port 465) or none (not secure: for a catcher on this machine only).
    security: str
    user: str | None
    password: str | None = field(repr=False)
    # The name and the address messages come from. The name may be empty.
    sender_name: str
    sender_address: str

    @property
    def sender(self) -> str:
        if self.sender_name:
            return f"{self.sender_name} <{self.sender_address}>"
        return self.sender_address

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "MailSettings | None":
        host = env.get("VRUM_SMTP_HOST", "").strip()
        if not host:
            if any(env.get(name, "").strip() for name in _MAIL_SETTINGS):
                raise ConfigError(
                    "VRUM_SMTP_HOST is not set. The VRUM_SMTP_ settings go together: set the host "
                    "to turn email on, or leave all of them empty to go without email."
                )
            return None
        if any(ch.isspace() for ch in host):
            raise ConfigError(f"VRUM_SMTP_HOST must be a host name or address, not {host!r}.")
        security = env.get("VRUM_SMTP_SECURITY", "").strip().lower() or "starttls"
        if security not in SMTP_SECURITIES:
            raise ConfigError(
                f"VRUM_SMTP_SECURITY must be starttls, ssl or none, but it is {security!r}."
            )
        raw_from = env.get("VRUM_SMTP_FROM", "").strip()
        if not raw_from:
            raise ConfigError(
                "VRUM_SMTP_FROM is not set. Set it to the address messages are sent from, such as "
                "Vrum <vrum@example.com>."
            )
        sender_name, sender_address = _sender(raw_from)
        user = env.get("VRUM_SMTP_USER", "").strip() or None
        password = _secret(env, "VRUM_SMTP_PASSWORD")
        if (user is None) != (password is None):
            missing = "VRUM_SMTP_PASSWORD" if password is None else "VRUM_SMTP_USER"
            raise ConfigError(
                f"{missing} is not set. VRUM_SMTP_USER and VRUM_SMTP_PASSWORD go together: set "
                "both for a mail server that wants a sign-in, or neither."
            )
        if user is not None and security == "none":
            raise ConfigError(
                "VRUM_SMTP_SECURITY is none, which sends everything, the password included, as "
                "plain text. Use starttls or ssl with a sign-in, or leave the user and password "
                "out."
            )
        return cls(
            host=host,
            port=_port(env, "VRUM_SMTP_PORT", 465 if security == "ssl" else 587),
            security=security,
            user=user,
            password=password,
            sender_name=sender_name,
            sender_address=sender_address,
        )


MAX_PROVIDER_NAME_CHARS = 40
_OIDC_SETTINGS = (
    "VRUM_OIDC_CLIENT_ID",
    "VRUM_OIDC_CLIENT_SECRET",
    "VRUM_OIDC_CLIENT_SECRET_FILE",
    "VRUM_OIDC_NAME",
    "VRUM_OIDC_SCOPES",
    "VRUM_OIDC_ALLOWED_DOMAINS",
)
_GITHUB_SETTINGS = (
    "VRUM_GITHUB_CLIENT_SECRET",
    "VRUM_GITHUB_CLIENT_SECRET_FILE",
    "VRUM_GITHUB_URL",
    "VRUM_GITHUB_AUTHORIZE_URL",
    "VRUM_GITHUB_API_URL",
    "VRUM_GITHUB_ALLOWED_DOMAINS",
)
_LDAP_SETTINGS = (
    "VRUM_LDAP_SECURITY",
    "VRUM_LDAP_BIND_DN",
    "VRUM_LDAP_BIND_PASSWORD",
    "VRUM_LDAP_BIND_PASSWORD_FILE",
    "VRUM_LDAP_BASE_DN",
    "VRUM_LDAP_USER_FILTER",
    "VRUM_LDAP_EMAIL_ATTRIBUTE",
    "VRUM_LDAP_NAME_ATTRIBUTE",
    "VRUM_LDAP_ID_ATTRIBUTE",
    "VRUM_LDAP_NAME",
    "VRUM_LDAP_ALLOWED_DOMAINS",
)


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, but it is {raw!r}.")


def _label(env: Mapping[str, str], name: str, default: str) -> str:
    """The words on a sign-in button: one short line of printable text."""
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if len(raw) > MAX_PROVIDER_NAME_CHARS or not raw.isprintable():
        raise ConfigError(
            f"{name} is the name on the sign-in button, so it must be one short line of text "
            f"(up to {MAX_PROVIDER_NAME_CHARS} characters)."
        )
    return raw


_A_DOMAIN = re.compile(r"[^\s@,/:]+\.[^\s@,/:]+")


def _domains(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    """The email domains a provider may sign people in with, from a comma separated list. Empty
    means any. A domain matches exactly: example.com does not include mail.example.com."""
    found: list[str] = []
    for part in env.get(name, "").split(","):
        domain = part.strip().lstrip("@").lower()
        if not domain:
            continue
        if not _A_DOMAIN.fullmatch(domain) or not domain.isprintable():
            raise ConfigError(
                f"{name} is a list of email domains separated by commas, such as "
                f"example.com,example.org, but it holds {part.strip()!r}."
            )
        if domain not in found:
            found.append(domain)
    return tuple(found)


def _provider_url(
    env: Mapping[str, str],
    name: str,
    default: str | None,
    *,
    allow_insecure_http: bool,
    exactly: bool = False,
) -> str:
    """The address of a provider (or a part of it). It has to be https, because a code and a
    token travel to it; http is only accepted for a local test provider, when the person who runs
    Vrum said so. It never holds a user name or password. A trailing slash is dropped, except
    from an address that has to be kept exactly as written (an OpenID issuer)."""
    raw = env.get(name, "").strip() or default
    if raw is None:
        raise ConfigError(f"{name} is not set.")
    parsed = urlparse(raw)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or " " in raw
        or not raw.isprintable()
    ):
        raise ConfigError(
            f"{name} must be a web address starting with https://, but it is {raw!r}."
        )
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError(f"{name} must not hold a user name or a password.")
    if parsed.fragment or parsed.query:
        raise ConfigError(f"{name} must be an address without a query or a fragment.")
    if parsed.scheme == "http" and not allow_insecure_http:
        raise ConfigError(
            f"{name} must start with https://, because a sign-in code and token travel to it, but "
            f"it is {raw!r}. Only a provider on this machine for testing may use http: set "
            "VRUM_SSO_ALLOW_INSECURE_HTTP=true for that."
        )
    return raw if exactly else raw.rstrip("/")


def _host_of(url: str) -> str:
    """The host a web address names, with its port when it has one, for the Status page."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return f"{host}:{parsed.port}" if parsed.port else host


@dataclass(frozen=True)
class ProviderInfo:
    """A way of signing in, as the pages are told of it. Never holds a secret."""

    id: str
    name: str
    host: str


@dataclass(frozen=True)
class OidcSettings:
    """One OpenID Connect provider: Google, Microsoft, Okta, Keycloak, or any other that serves
    /.well-known/openid-configuration. Without a client secret Vrum is a public client and relies
    on PKCE alone."""

    issuer: str
    client_id: str
    client_secret: str | None = field(repr=False)
    name: str
    scopes: tuple[str, ...]
    allowed_domains: tuple[str, ...]

    @classmethod
    def from_env(
        cls, env: Mapping[str, str], *, allow_insecure_http: bool
    ) -> "OidcSettings | None":
        issuer = env.get("VRUM_OIDC_ISSUER", "").strip()
        if not issuer:
            if any(env.get(name, "").strip() for name in _OIDC_SETTINGS):
                raise ConfigError(
                    "VRUM_OIDC_ISSUER is not set. The VRUM_OIDC_ settings go together: set the "
                    "issuer to turn on sign-in with OpenID Connect, or leave all of them empty."
                )
            return None
        client_id = _required(
            env, "VRUM_OIDC_CLIENT_ID", "It is the client id the provider gave Vrum."
        )
        scopes = tuple(env.get("VRUM_OIDC_SCOPES", "").replace(",", " ").split()) or (
            "openid",
            "email",
            "profile",
        )
        if "openid" not in scopes or not all(scope.isprintable() for scope in scopes):
            raise ConfigError(
                "VRUM_OIDC_SCOPES must include openid, written like 'openid email profile'."
            )
        return cls(
            issuer=_provider_url(
                env,
                "VRUM_OIDC_ISSUER",
                None,
                allow_insecure_http=allow_insecure_http,
                exactly=True,
            ),
            client_id=client_id,
            client_secret=_secret(env, "VRUM_OIDC_CLIENT_SECRET"),
            name=_label(env, "VRUM_OIDC_NAME", "OpenID"),
            scopes=scopes,
            allowed_domains=_domains(env, "VRUM_OIDC_ALLOWED_DOMAINS"),
        )


@dataclass(frozen=True)
class GithubSettings:
    """Sign in with a GitHub OAuth App (or a GitHub Enterprise Server one)."""

    client_id: str
    client_secret: str = field(repr=False)
    # Where Vrum itself asks for the token, where the browser is sent to sign in, and the API.
    url: str
    authorize_url: str
    api_url: str
    allowed_domains: tuple[str, ...]
    name: str = "GitHub"

    @classmethod
    def from_env(
        cls, env: Mapping[str, str], *, allow_insecure_http: bool
    ) -> "GithubSettings | None":
        client_id = env.get("VRUM_GITHUB_CLIENT_ID", "").strip()
        if not client_id:
            if any(env.get(name, "").strip() for name in _GITHUB_SETTINGS):
                raise ConfigError(
                    "VRUM_GITHUB_CLIENT_ID is not set. The VRUM_GITHUB_ settings go together: set "
                    "the client id to turn on sign-in with GitHub, or leave all of them empty."
                )
            return None
        secret = _secret(env, "VRUM_GITHUB_CLIENT_SECRET")
        if secret is None:
            raise ConfigError(
                "VRUM_GITHUB_CLIENT_SECRET is not set. It is the client secret of the GitHub "
                "OAuth App (or put the path of a file that holds it in "
                "VRUM_GITHUB_CLIENT_SECRET_FILE)."
            )
        url = _provider_url(
            env, "VRUM_GITHUB_URL", "https://github.com", allow_insecure_http=allow_insecure_http
        )
        return cls(
            client_id=client_id,
            client_secret=secret,
            url=url,
            authorize_url=_provider_url(
                env,
                "VRUM_GITHUB_AUTHORIZE_URL",
                f"{url}/login/oauth/authorize",
                allow_insecure_http=allow_insecure_http,
            ),
            api_url=_provider_url(
                env,
                "VRUM_GITHUB_API_URL",
                "https://api.github.com",
                allow_insecure_http=allow_insecure_http,
            ),
            allowed_domains=_domains(env, "VRUM_GITHUB_ALLOWED_DOMAINS"),
        )


LDAP_SECURITIES = ("starttls", "none")
_AN_ATTRIBUTE = re.compile(r"[A-Za-z][A-Za-z0-9-]*(;[A-Za-z0-9-]+)*")
DEFAULT_LDAP_FILTER = "(uid={username})"


def _attribute(env: Mapping[str, str], name: str, default: str) -> str:
    raw = env.get(name, "").strip() or default
    if not _AN_ATTRIBUTE.fullmatch(raw):
        raise ConfigError(f"{name} must be the name of a directory attribute, but it is {raw!r}.")
    return raw


def _user_filter(env: Mapping[str, str]) -> str:
    raw = env.get("VRUM_LDAP_USER_FILTER", "").strip() or DEFAULT_LDAP_FILTER
    depth, balanced = 0, True
    for ch in raw:
        depth += (ch == "(") - (ch == ")")
        balanced = balanced and depth >= 0
    if (
        raw.count("{username}") != 1
        or not raw.startswith("(")
        or not raw.endswith(")")
        or depth != 0
        or not balanced
        or not raw.isprintable()
    ):
        raise ConfigError(
            "VRUM_LDAP_USER_FILTER must be a directory search filter in parentheses that holds "
            "{username} exactly once, such as (uid={username}) or "
            f"(&(objectClass=person)(sAMAccountName={{username}})), but it is {raw!r}."
        )
    return raw


@dataclass(frozen=True)
class LdapSettings:
    """Sign in with a username and password that a directory (OpenLDAP, Active Directory, and
    others) checks. Vrum finds the person with the service account (or anonymously), then checks
    the password by signing in to the directory as that person."""

    url: str
    host: str
    port: int
    # ldaps (secure from the first byte), starttls (an ordinary connection that is made secure)
    # or none (not secure: for a directory on this machine for testing only).
    security: str
    bind_dn: str | None
    bind_password: str | None = field(repr=False)
    base_dn: str
    user_filter: str
    email_attribute: str
    name_attribute: str
    id_attribute: str
    name: str
    allowed_domains: tuple[str, ...]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "LdapSettings | None":
        raw = env.get("VRUM_LDAP_URL", "").strip()
        if not raw:
            if any(env.get(name, "").strip() for name in _LDAP_SETTINGS):
                raise ConfigError(
                    "VRUM_LDAP_URL is not set. The VRUM_LDAP_ settings go together: set the "
                    "address of the directory to turn on sign-in with LDAP, or leave all of them "
                    "empty."
                )
            return None
        parsed = urlparse(raw)
        host = parsed.hostname
        try:
            explicit_port = parsed.port
        except ValueError:
            explicit_port = 0
        if (
            parsed.scheme not in ("ldap", "ldaps")
            or not host
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or explicit_port == 0
            or not raw.isprintable()
            or " " in raw
        ):
            raise ConfigError(
                "VRUM_LDAP_URL must be the address of the directory, such as "
                f"ldaps://ldap.example.com:636 or ldap://ldap.example.com:389, but it is {raw!r}."
            )
        chosen = env.get("VRUM_LDAP_SECURITY", "").strip().lower() or "starttls"
        if chosen not in LDAP_SECURITIES:
            raise ConfigError(
                f"VRUM_LDAP_SECURITY must be starttls or none, but it is {chosen!r}. It is "
                "ignored for an ldaps:// address, which is always secure."
            )
        bind_dn = env.get("VRUM_LDAP_BIND_DN", "").strip() or None
        bind_password = _secret(env, "VRUM_LDAP_BIND_PASSWORD")
        if (bind_dn is None) != (bind_password is None):
            missing = "VRUM_LDAP_BIND_PASSWORD" if bind_password is None else "VRUM_LDAP_BIND_DN"
            raise ConfigError(
                f"{missing} is not set. VRUM_LDAP_BIND_DN and VRUM_LDAP_BIND_PASSWORD go "
                "together: set both for the service account that searches the directory, or "
                "neither to search anonymously."
            )
        if bind_dn is not None and not bind_dn.isprintable():
            raise ConfigError("VRUM_LDAP_BIND_DN must be one line of text.")
        base_dn = _required(
            env,
            "VRUM_LDAP_BASE_DN",
            "It is where in the directory to look for people, such as dc=example,dc=com.",
        )
        if not base_dn.isprintable():
            raise ConfigError("VRUM_LDAP_BASE_DN must be one line of text.")
        secure_from_start = parsed.scheme == "ldaps"
        return cls(
            url=raw.rstrip("/"),
            host=host,
            port=explicit_port or (636 if secure_from_start else 389),
            security="ldaps" if secure_from_start else chosen,
            bind_dn=bind_dn,
            bind_password=bind_password,
            base_dn=base_dn,
            user_filter=_user_filter(env),
            email_attribute=_attribute(env, "VRUM_LDAP_EMAIL_ATTRIBUTE", "mail"),
            name_attribute=_attribute(env, "VRUM_LDAP_NAME_ATTRIBUTE", "cn"),
            id_attribute=_attribute(env, "VRUM_LDAP_ID_ATTRIBUTE", "entryUUID"),
            name=_label(env, "VRUM_LDAP_NAME", "LDAP"),
            allowed_domains=_domains(env, "VRUM_LDAP_ALLOWED_DOMAINS"),
        )


@dataclass(frozen=True)
class SsoSettings:
    """Signing in with another provider: off unless a provider is set up. A provider is never a
    way to take over an account that already exists (see accounts.py)."""

    # May a first sign-in by a provider make a new account? When not, only people who already
    # have an account can use a provider.
    create_accounts: bool = True
    # Whether a provider's address may be http. Only for a provider on this machine for testing.
    allow_insecure_http: bool = False
    oidc: OidcSettings | None = None
    github: GithubSettings | None = None
    ldap: LdapSettings | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "SsoSettings":
        insecure = _flag(env, "VRUM_SSO_ALLOW_INSECURE_HTTP", False)
        return cls(
            create_accounts=_flag(env, "VRUM_SSO_CREATE_ACCOUNTS", True),
            allow_insecure_http=insecure,
            oidc=OidcSettings.from_env(env, allow_insecure_http=insecure),
            github=GithubSettings.from_env(env, allow_insecure_http=insecure),
            ldap=LdapSettings.from_env(env),
        )

    def providers(self) -> tuple[ProviderInfo, ...]:
        """The ways of signing in that are set up, in the order the pages show them."""
        found: list[ProviderInfo] = []
        if self.oidc is not None:
            found.append(ProviderInfo("oidc", self.oidc.name, _host_of(self.oidc.issuer)))
        if self.github is not None:
            found.append(ProviderInfo("github", self.github.name, _host_of(self.github.url)))
        if self.ldap is not None:
            found.append(ProviderInfo("ldap", self.ldap.name, _host_of(self.ldap.url)))
        return tuple(found)

    def allowed_domains(self, provider: str) -> tuple[str, ...]:
        chosen = {"oidc": self.oidc, "github": self.github, "ldap": self.ldap}.get(provider)
        return chosen.allowed_domains if chosen is not None else ()

    def warnings(self) -> tuple[tuple[str, str], ...]:
        """What the person who runs Vrum should be reminded of at every start: (event, words)."""
        found: list[tuple[str, str]] = []
        if self.allow_insecure_http:
            found.append(
                (
                    "sso_insecure_http",
                    "VRUM_SSO_ALLOW_INSECURE_HTTP is on, so a provider may be reached over "
                    "plain http. Use it only for a provider on this machine for testing.",
                )
            )
        if self.ldap is not None and self.ldap.security == "none":
            found.append(
                (
                    "ldap_not_secure",
                    "VRUM_LDAP_SECURITY is none, so passwords travel to the directory as plain "
                    "text. Use it only for a directory on this machine for testing.",
                )
            )
        return tuple(found)

    def secrets(self) -> tuple[str, ...]:
        found: list[str] = []
        if self.oidc is not None and self.oidc.client_secret:
            found.append(self.oidc.client_secret)
        if self.github is not None:
            found.append(self.github.client_secret)
        if self.ldap is not None and self.ldap.bind_password:
            found.append(self.ldap.bind_password)
        return tuple(found)


@dataclass(frozen=True)
class WebhookSettings:
    """Outgoing webhooks (M48). They are always available to a project's owner, but an address
    must be a public one and `https`: the two settings below widen that for a self-hosted install,
    which is the person's own network."""

    # Names, addresses or networks (such as 10.0.0.0/8) that a webhook may go to even though they
    # are not public addresses. Empty by default: loopback, private, link-local and metadata
    # addresses are refused when a webhook is saved and again when it is called.
    allowed_hosts: tuple[str, ...] = ()
    # Whether a webhook's address may be http. Only for a receiver on this machine, for testing.
    allow_insecure_http: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "WebhookSettings":
        raw = env.get("VRUM_WEBHOOK_ALLOWED_HOSTS", "")
        hosts = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
        for host in hosts:
            if not _WEBHOOK_HOST.fullmatch(host):
                raise ConfigError(
                    f"VRUM_WEBHOOK_ALLOWED_HOSTS holds {host!r}, which is not a host name, an "
                    "address or a network such as 10.0.0.0/8. Give a comma separated list."
                )
        return cls(
            allowed_hosts=hosts,
            allow_insecure_http=_flag(env, "VRUM_WEBHOOK_ALLOW_INSECURE_HTTP", False),
        )


_WEBHOOK_HOST = re.compile(r"[a-z0-9][a-z0-9.:/-]{0,252}")


def _public_url(env: Mapping[str, str], port: int) -> str:
    """Where people reach the board, which every link in an email is built from."""
    raw = env.get("VRUM_PUBLIC_URL", "").strip()
    if not raw:
        return f"http://localhost:{port}"
    parsed = urlparse(raw)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
        or not raw.isprintable()
        or " " in raw
    ):
        raise ConfigError(
            "VRUM_PUBLIC_URL must be the address people open the board at, starting with http:// "
            f"or https://, such as https://vrum.example.com, but it is {raw!r}."
        )
    return raw.rstrip("/")


def _sandbox_socket(env: Mapping[str, str]) -> str:
    """Where the sandbox service's socket is: an absolute path."""
    raw = env.get("VRUM_SANDBOX_SOCKET", "").strip()
    if not raw:
        return DEFAULT_SANDBOX_SOCKET
    if not raw.startswith("/") or not raw.isprintable():
        raise ConfigError(
            "VRUM_SANDBOX_SOCKET must be the full path of the sandbox service's socket, such as "
            f"{DEFAULT_SANDBOX_SOCKET}, but it is {raw!r}."
        )
    return raw


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
    # Where people reach the board: every link in an email starts with it.
    public_url: str = "http://localhost:8000"
    # The mail server, or None when email is off (the default).
    mail: MailSettings | None = None
    # Signing in with another provider (OpenID Connect, GitHub, LDAP): off unless one is set up.
    sso: SsoSettings = field(default_factory=SsoSettings)
    # Outgoing webhooks and the addresses they may go to (see WebhookSettings).
    webhooks: WebhookSettings = field(default_factory=WebhookSettings)
    # The Unix socket of the sandbox service that runs the scripts of agent keys that call Vrum by
    # running code. Nothing waits for it: when it is not there, a script says so.
    sandbox_socket: str = DEFAULT_SANDBOX_SOCKET

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        db_password = _secret(env, "VRUM_DB_PASSWORD")
        if db_password is None:
            raise ConfigError(
                "VRUM_DB_PASSWORD is not set. Set it, or set VRUM_DB_PASSWORD_FILE to a file "
                "that holds it. docker compose makes the password and sets this up for you."
            )
        port = _port(env, "VRUM_PORT", 8000)
        return cls(
            host=env.get("VRUM_HOST", "").strip() or "127.0.0.1",
            port=port,
            db_host=_required(
                env, "VRUM_DB_HOST", "Set it to the name or address of the PostgreSQL server."
            ),
            db_port=_port(env, "VRUM_DB_PORT", 5432),
            db_name=env.get("VRUM_DB_NAME", "").strip() or "vrum",
            db_user=env.get("VRUM_DB_USER", "").strip() or "vrum",
            db_password=db_password,
            max_request_bytes=_whole_number(
                env, "VRUM_MAX_REQUEST_BYTES", DEFAULT_MAX_REQUEST_BYTES, 1000
            ),
            board_poll_seconds=_seconds(env, "VRUM_BOARD_POLL_SECONDS", DEFAULT_BOARD_POLL_SECONDS),
            embedding=EmbeddingSettings.from_env(env),
            public_url=_public_url(env, port),
            mail=MailSettings.from_env(env),
            sso=SsoSettings.from_env(env),
            webhooks=WebhookSettings.from_env(env),
            sandbox_socket=_sandbox_socket(env),
        )

    def secrets(self) -> tuple[str, ...]:
        """Every secret value this server holds, so that nothing it writes can contain one."""
        found = [self.db_password]
        if self.embedding is not None and self.embedding.api_key:
            found.append(self.embedding.api_key)
        if self.mail is not None and self.mail.password:
            found.append(self.mail.password)
        found.extend(self.sso.secrets())
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
