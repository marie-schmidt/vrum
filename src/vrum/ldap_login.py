"""Sign in with a username and password that a directory (OpenLDAP, Active Directory, FreeIPA and
others) checks.

Vrum never asks the directory to compare passwords. It finds the person (with a service account,
or anonymously if the directory allows it) by searching for exactly one entry that matches the
configured filter, and then checks the password by signing in to the directory as that entry. The
typed username goes into the filter only after RFC 4515 escaping, so `*)(uid=*` is just a strange
username that matches nothing. An empty password is refused before the directory is asked at all,
because a simple bind with a name and an empty password is an anonymous bind, which many
directories answer with success. The password is used for that one bind and then forgotten: it is
never logged or kept.

The directory's own identifier for the person (entryUUID, by default) is what identifies them, not
their username, which can change. Everything here blocks, so `authenticate` runs it in a thread."""

import asyncio
import logging
import secrets
import ssl
from collections.abc import Callable
from contextlib import suppress
from typing import Any, NamedTuple

from ldap3 import (
    ANONYMOUS,
    NONE,
    SIMPLE,
    SUBTREE,
    Connection,
    Server,
    Tls,
)
from ldap3.core.exceptions import LDAPCommunicationError, LDAPException
from ldap3.utils.conv import escape_filter_chars

from vrum.accounts import ProviderPerson
from vrum.config import LdapSettings
from vrum.sso_http import ProviderProblem

log = logging.getLogger("vrum.ldap")

TIMEOUT_SECONDS = 10
MOST_USERNAME_CHARS = 256
MOST_PASSWORD_CHARS = 200
# A directory that is told the connection is not secure enough says so with these.
_NEEDS_SECURITY = ("confidentialityRequired", "strongerAuthRequired")

# How a connection to the directory is made, as (name to sign in as, password): None for both is
# an anonymous one. A test stands in for the directory with it.
Connector = Callable[[str | None, str | None], Connection]


class LdapResult(NamedTuple):
    """Who the directory says the person is, or None when the name or password is not right, with
    a short fixed reason for the log (it never holds the name or the password)."""

    person: ProviderPerson | None
    reason: str


def refused_input(username: str, password: str) -> str | None:
    """Why a username and password are not even sent to the directory, or None. An empty password
    is the one that matters: it would be an anonymous bind."""
    if not username.strip() or len(username) > MOST_USERNAME_CHARS:
        return "username_not_usable"
    if not password:
        return "empty_password"
    if len(password) > MOST_PASSWORD_CHARS:
        return "password_too_long"
    return None


def filter_for(template: str, username: str) -> str:
    """The search filter for a username: the template with {username} replaced by the name with
    every character that means something in a filter escaped."""
    return template.replace("{username}", escape_filter_chars(username))


def _values(entry: dict[str, Any], key: str, attribute: str) -> list[Any]:
    """The values an entry has for an attribute, found whatever its upper and lower case."""
    wanted = attribute.lower()
    found = entry.get(key) or {}
    for name, value in found.items():
        if str(name).lower() == wanted:
            return list(value) if isinstance(value, list | tuple) else [value]
    return []


def _text(value: Any) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, int | float) and not isinstance(value, bool):
        value = str(value)
    return value if isinstance(value, str) and value.strip() else None


def identifier_of(entry: dict[str, Any], attribute: str) -> str | None:
    """The directory's identifier for the person: exactly one value, as text when it is text (an
    entryUUID) and as hexadecimal when it is binary (an objectGUID). None when it has none, more
    than one, or one that does not fit."""
    found: str | None
    raw = _values(entry, "raw_attributes", attribute)
    if len(raw) == 1 and isinstance(raw[0], bytes) and raw[0]:
        try:
            text = raw[0].decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        found = text if text and text.isprintable() and text.strip() == text else raw[0].hex()
    else:
        values = _values(entry, "attributes", attribute)
        found = _text(values[0]) if len(values) == 1 else None
    return found if found is not None and 1 <= len(found) <= 255 else None


def person_from_entry(
    entry: dict[str, Any], settings: LdapSettings, username: str
) -> ProviderPerson | None:
    """Who a directory entry says the person is: None when it has no usable identifier. The
    address in the directory is trusted as confirmed, because the directory belongs to whoever
    runs Vrum."""
    subject = identifier_of(entry, settings.id_attribute)
    if subject is None:
        return None
    mails = _values(entry, "attributes", settings.email_attribute)
    names = _values(entry, "attributes", settings.name_attribute)
    email = _text(mails[0]) if mails else None
    return ProviderPerson(
        provider="ldap",
        subject=subject,
        email=email,
        email_confirmed=email is not None,
        name=_text(names[0]) if names else None,
        shown=username,
    )


class LdapDirectory:
    def __init__(self, settings: LdapSettings, *, connector: Connector | None = None) -> None:
        self._settings = settings
        self._connect = connector or self._real_connection
        self._server: Server | None = None

    def _real_connection(self, user: str | None, password: str | None) -> Connection:
        settings = self._settings
        if self._server is None:
            secure = settings.security in ("ldaps", "starttls")
            self._server = Server(
                settings.host,
                port=settings.port,
                use_ssl=settings.security == "ldaps",
                # A certificate that cannot be checked, or is for another name, is refused.
                tls=Tls(validate=ssl.CERT_REQUIRED) if secure else None,
                get_info=NONE,
                connect_timeout=TIMEOUT_SECONDS,
            )
        return Connection(
            self._server,
            user=user,
            password=password,
            authentication=ANONYMOUS if user is None else SIMPLE,
            receive_timeout=TIMEOUT_SECONDS,
            raise_exceptions=False,
            read_only=True,
        )

    def _bound(self, user: str | None, password: str | None) -> tuple[Connection, bool]:
        """A connection that has tried to sign in as `user`, and whether the directory accepted."""
        connection = self._connect(user, password)
        try:
            connection.open()
            if self._settings.security == "starttls" and not connection.start_tls():
                raise ProviderProblem("failed", "starttls_refused")
            accepted = bool(connection.bind())
            if not accepted and connection.result.get("description") in _NEEDS_SECURITY:
                raise ProviderProblem("failed", "directory_wants_a_secure_connection")
        except BaseException:
            with suppress(Exception):
                connection.unbind()
            raise
        return connection, accepted

    async def authenticate(self, username: str, password: str) -> LdapResult:
        """Find the person and check their password. The caller has already refused what
        `refused_input` refuses. Raises ProviderProblem when the directory cannot be reached or
        is not set up as Vrum was told."""
        try:
            return await asyncio.to_thread(self._authenticate, username.strip(), password)
        except ProviderProblem:
            raise
        except LDAPCommunicationError as error:
            # A certificate that is not trusted, or is for another name, shows as a failure to open
            # the connection: the log says which, for whoever runs the server.
            reason = "certificate_not_accepted" if "ssl wrapping" in str(error) else "unreachable"
            raise ProviderProblem("unavailable", reason) from None
        except (LDAPException, OSError):
            raise ProviderProblem("failed", "directory_error") from None

    def _authenticate(self, username: str, password: str) -> LdapResult:
        settings = self._settings
        searcher, searcher_accepted = self._bound(settings.bind_dn, settings.bind_password)
        if not searcher_accepted:
            searcher.unbind()
            raise ProviderProblem("failed", "service_account_refused")
        try:
            searcher.search(
                search_base=settings.base_dn,
                search_filter=filter_for(settings.user_filter, username),
                search_scope=SUBTREE,
                attributes=[
                    settings.id_attribute,
                    settings.email_attribute,
                    settings.name_attribute,
                ],
                size_limit=2,
                time_limit=TIMEOUT_SECONDS,
            )
            description = searcher.result.get("description")
            entries = [
                one for one in searcher.response or [] if one.get("type") == "searchResEntry"
            ]
        finally:
            searcher.unbind()
        if description not in ("success", "sizeLimitExceeded"):
            raise ProviderProblem("failed", "search_refused")
        if len(entries) != 1:
            # No such person, or more than one: the same steps as for a wrong password, so that
            # how long it takes says nothing about which usernames exist.
            decoy = f"cn={secrets.token_hex(8)},{settings.base_dn}"
            checked, _ = self._bound(decoy, password)
            checked.unbind()
            return LdapResult(None, "unknown_user" if not entries else "ambiguous_user")
        entry = entries[0]
        checked, accepted = self._bound(str(entry["dn"]), password)
        checked.unbind()
        if not accepted:
            return LdapResult(None, "wrong_password")
        person = person_from_entry(entry, settings, username)
        if person is None:
            raise ProviderProblem("failed", "no_identifier")
        return LdapResult(person, "ok")
