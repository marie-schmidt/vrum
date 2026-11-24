"""Accounts, sessions, projects, their people and agent keys: who may use what, and who is who.

A person registers with an email address, a name and a password (kept only as a salted, slow hash),
signs in to get a session (a random value in a cookie, of which only a hash is kept), owns any
number of private projects, and gives agents keys to reach one project each (a random value, shown
once, of which only a hash is kept). The person's name and each key's name are the identities
everything is recorded under (see identity.py): a key is named for its agent, and no two keys in
force in a project share a name. A key may call the tools Vrum offers agents (see surface.py), or
only the tools the owner chose for it (`allowed_tools`), checked on every call by the guard, and
calls them one at a time or, when the owner chose it (`code_mode`), by running code.

A project has exactly one owner, who can do everything, and any number of other people the owner
invited: an editor reads and writes the project's graph, a viewer only reads it, and neither makes
keys, invites people or changes who is in the project. An invitation is a link with a random token
(shown once, of which only a hash is kept) that one person spends within seven days. People are
told apart in the history by their names, so no two people of one project share a name. This
module is the only one that reads or writes those tables. They are not graph content: the database
role that a project's work runs as cannot reach them at all.

Every refusal says what was wrong and what to do, and none of them repeats what was sent.

Email is optional (see mail.py). When a mail server is set up, an invitation can be sent to the
address the owner typed, and only an account with that address can accept it; an address can be
confirmed by a link that reaches its mailbox; and a person who forgot their password can choose a
new one by a link sent to it. The links hold random tokens, shown once, in the email only, of which
only a hash is kept; each works once and for a short time. Whether an address has an account is
never told to anyone who has not shown they own the address.

Signing in with another provider (OpenID Connect, GitHub, LDAP: see sso.py) is optional too. A
provider only ever tells this module who someone is, as a `ProviderPerson`, and this module decides
what that means, in one place (`sign_in_with_provider`): a person who has signed in that way before
is that account; a new one is joined to the account with their address only when the address is
confirmed on both sides (the provider says it is theirs, and a link that reached its mailbox
confirmed it here), and otherwise refused; and with no account at all, one is made, with no
password. A provider is never a way to take over an account whose address was never confirmed."""

import asyncio
import base64
import hashlib
import hmac
import logging
import re
import secrets
from collections.abc import Collection, Coroutine
from datetime import UTC, date, datetime
from typing import Any, Literal, NamedTuple

import psycopg
from pydantic import BaseModel

from vrum.common import Refusal, fetch_all, fetch_one, parse_day
from vrum.db import Database
from vrum.logs import event
from vrum.mail import (
    Mailer,
    MailRefusal,
    invitation_message,
    is_plain_address,
    reset_message,
    verification_message,
)
from vrum.ratelimit import AttemptLimiter
from vrum.records import PEOPLE_ONLY_TOOLS
from vrum.secret_formats import find_secret
from vrum.surface import CODE_TOOLS

log = logging.getLogger("vrum.accounts")

SESSION_HOURS = 12
SESSION_PREFIX = "vrs_"
KEY_PREFIX = "vrk_"
INVITE_PREFIX = "vri_"
VERIFY_PREFIX = "vrv_"
RESET_PREFIX = "vrr_"
INVITE_DAYS = 7
# How long the link in a confirmation email and the link in a password reset email work.
VERIFY_HOURS = 24
RESET_HOURS = 1
MIN_PASSWORD_CHARS = 10
MAX_PASSWORD_CHARS = 200
MAX_NAME_CHARS = 100
MAX_EMAIL_CHARS = 254
# Failed sign-ins (and registrations with an address that has an account) allowed in ten minutes
# for one email address, and for one network address. The first is what stops someone guessing a
# password; the second is only there to slow down someone trying one guess on each of many
# addresses, so it is much more generous.
FAILURES_PER_EMAIL = 5
FAILURES_PER_CLIENT = 200
# Invitation links that were not valid, in the same ten minutes, for one signed-in account.
FAILURES_PER_INVITEE = 10
FAILURE_WINDOW_SECONDS = 600
# How many emails Vrum will send, so that it cannot be used to fill someone's mailbox or to spend
# a mail provider's allowance. Invitations: per owner in an hour and per project in a day.
# Confirmations the person asks for: per account in an hour. Password resets: per address and per
# network address in an hour. (The one confirmation a registration sends needs no limit of its
# own: an address can register once.)
INVITE_MAILS_PER_OWNER = 20
INVITE_MAILS_PER_PROJECT = 60
VERIFY_MAILS_PER_ACCOUNT = 3
RESETS_PER_EMAIL = 5
RESETS_PER_CLIENT = 20
HOUR_SECONDS = 3600
DAY_SECONDS = 86400
# What a person can be in a project besides its owner, which is the one role nobody is given.
ROLES_TO_GIVE = ("editor", "viewer")
# Held while a person's name is checked against the names of the people they share projects with
# and then taken, so that two people cannot take the same name in one project at the same moment.
_PEOPLE_LOCK = "vrum.people_names"

# scrypt: N=2^15, r=8, p=1 takes about 100 ms and 32 MiB, which is slow enough to make guessing
# expensive and cheap enough to sign in. They are stored with each hash, so they can be raised.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1
_SCRYPT_MEMORY = 96 * 1024 * 1024
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class AccessRefusal(Refusal):
    """A request that is refused, with the kind of refusal the web answer should carry."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


NOT_SIGNED_IN = "You are not signed in, or your session has ended. Sign in again."
NO_SUCH_PROJECT = (
    "There is no project with that address among yours. Choose one from your projects, or "
    "create a new one."
)
BAD_AGENT_KEY = (
    "The agent key is not valid, so nothing was done. It may be wrong or it may have been "
    "revoked. Ask the project's owner for a new one (Users, then Agents, on the project's board), "
    "and send it as 'Authorization: Bearer <key>'."
)
NO_AGENT_KEY = (
    "This server needs an agent key for a project, and none was sent. The project's owner can "
    "make one (Users, then Agents, on the project's board). Send it as 'Authorization: Bearer "
    "<key>'."
)
VIEWER_CANNOT_CHANGE = (
    "You can look at this project but not change it. Ask its owner to make you an editor. "
    "Nothing was changed."
)
INVITE_NOT_VALID = (
    "That invitation is not valid. It may be wrong, already used, expired or withdrawn. Ask the "
    "project's owner for a new one."
)
INVITE_NO_MAIL = "This server cannot send email, so share the link yourself."
VERIFY_NO_MAIL = (
    "This server cannot send email, so an address cannot be confirmed by email. Ask whoever runs "
    "this server."
)
RESET_NO_MAIL = (
    "This server cannot send email, so passwords cannot be reset by email. Ask whoever runs this "
    "server."
)
VERIFY_LINK_NOT_VALID = (
    "That confirmation link is not valid. It may be wrong, already used or expired. Sign in and "
    "ask for a new one on the Account page."
)
RESET_LINK_NOT_VALID = (
    "That link to choose a new password is not valid. It may be wrong, already used or expired. "
    "Ask for a new one on the sign-in page."
)
RESET_REQUESTED = (
    "If that address has an account, a link to choose a new password has been sent to it. The "
    "link works once for one hour."
)
NO_SUCH_MEMBER = "There is no such person among this project's people."
NO_SUCH_KEY = "There is no such key for this project."
KEY_WAS_REVOKED = "That key was revoked, so there is nothing to change."

# The providers a person can sign in with, besides a password, and what each is called here.
PROVIDERS = ("oidc", "github", "ldap")
# What a person is told when signing in with a provider does not work. `{name}` is what the
# provider's button says (GitHub, or what VRUM_OIDC_NAME says). The pages show exactly these words,
# and the code is what they tell the problems apart by.
SSO_WORDS = {
    "cancelled": "Signing in with {name} was cancelled. Nothing was changed.",
    "failed": (
        "Signing in with {name} did not work. Nothing was changed. Try again, and if it keeps "
        "happening ask whoever runs this server."
    ),
    "expired": "That sign-in took too long or was already used. Start again.",
    "unavailable": "Vrum could not reach {name}. Try again in a moment.",
    "no_verified_email": (
        "{name} did not give a confirmed email address, so Vrum cannot tell who you are. Confirm "
        "your email address there, or sign in another way."
    ),
    "domain_not_allowed": (
        "Accounts with that email address may not sign in this way on this server."
    ),
    "account_exists_unconfirmed": (
        "An account with your email address already exists here, but its address was never "
        "confirmed, so Vrum will not join the two. Sign in with its password, then connect {name} "
        "from the Account page."
    ),
    "signups_off": (
        "This server does not make new accounts from {name} sign-in. Sign in with an account you "
        "already have, or ask whoever runs it."
    ),
    "identity_taken": "That {name} account is already connected to another Vrum account.",
    "already_connected": "{name} is already connected to this account. Disconnect it first.",
    "link_session_lost": ("You were signed out while connecting {name}. Sign in and try again."),
    "last_way_in": (
        "That is the only way you can sign in. Set a password or connect another provider first. "
        "Nothing was changed."
    ),
    "not_connected": "{name} is not connected to this account. Nothing was changed.",
    "bad_credentials": "The username or password is not right.",
    "not_set_up": "Signing in with that provider is not set up on this server.",
}
_SSO_STATUS = {
    "cancelled": 400,
    "failed": 502,
    "expired": 400,
    "unavailable": 503,
    "no_verified_email": 403,
    "domain_not_allowed": 403,
    "account_exists_unconfirmed": 409,
    "signups_off": 403,
    "identity_taken": 409,
    "already_connected": 409,
    "link_session_lost": 409,
    "last_way_in": 409,
    "not_connected": 404,
    "bad_credentials": 401,
    "not_set_up": 404,
}


class SsoRefusal(AccessRefusal):
    """Signing in with, or connecting, a provider did not work. `code` is which problem it is (a
    key of SSO_WORDS), and the message is its words, naming the provider by its button."""

    def __init__(self, code: str, name: str = "", status: int | None = None) -> None:
        super().__init__(SSO_WORDS[code].replace("{name}", name), status or _SSO_STATUS[code])
        self.code = code


# The most tool names that are listed back in a refusal.
_MOST_NAMED = 5
_A_TOOL_NAME = re.compile(r"[A-Za-z0-9_]{1,60}")


def key_not_allowed(agent: str, tool: str) -> str:
    """What an agent is told when its key does not let it use a tool. It names the tool only when
    the server has it, so it never repeats something that was sent."""
    return (
        f"This agent key ({agent}) is not allowed to use {tool}. Ask the project's owner to change "
        "what the key may do, on the Users page. Nothing was changed."
    )


def tool_not_offered(agent: str, tool: str) -> str:
    """What an agent whose key has no list of its own is told when it asks for a tool outside the
    set Vrum offers agents (a tool a person uses on the board, or an administration tool)."""
    return (
        f"{tool} is not one of the tools Vrum offers agents, so this agent key ({agent}) cannot "
        "use it: it is done on the board, by a person. If this agent needs it, the project's owner "
        "can allow it for this key on the Users page. Nothing was changed."
    )


def code_tool_for_a_tools_key(agent: str, tool: str) -> str:
    """What an agent that calls one tool at a time is told when it asks for `explore` or `run`."""
    return (
        f"{tool} is for agent keys that call Vrum by running code, and this agent key ({agent}) "
        "calls one tool at a time. If this agent should run code, the project's owner can change "
        "how it calls Vrum on the Users page. Nothing was changed."
    )


def tool_by_name_for_a_code_key(agent: str, tool: str) -> str:
    """What an agent that runs code is told when it calls a tool by its name: it is a function of
    the script it runs."""
    return (
        f"This agent key ({agent}) calls Vrum by running code, so call it as vrum.{tool}(...) in "
        "run. Use explore to see what the functions take and return. Nothing was changed."
    )


RUN_IS_NOT_FOR_THE_BOARD = (
    "run runs a script for an agent key that calls Vrum by running code. The board does its work "
    "one page at a time, so it does not use it. Nothing was changed."
)

# What a script is told when its key is changed while it runs: a revoked key stops it at its next
# call, as a revoked key is refused at its next request anywhere else, and so does a key that has
# been changed to call one tool at a time.
SCRIPT_KEY_REVOKED = (
    "The agent key is not valid any more: it was revoked while its script was running, so the "
    "script was stopped and this call was not made. What the script had already done stays done. "
    "Ask the project's owner for a new key (Users, then Agents, on the project's board)."
)


def script_key_expired_words(day: date) -> str:
    """What a script is told when its key's last day passes while it runs: it is stopped at its
    next call, as it would be refused at its next request anywhere else."""
    return (
        f"The agent key expired after {day.isoformat()} while its script was running, so the "
        "script was stopped and this call was not made. What the script had already done stays "
        "done. The project's owner can extend the key, or make a new one (Users, then Agents, on "
        "the project's board)."
    )


SCRIPT_KEY_NOW_TOOLS = (
    "This agent key was changed to call Vrum one tool at a time while its script was running, so "
    "the script was stopped and this call was not made. What the script had already done stays "
    "done. The project's owner can change how the key calls Vrum on the Users page."
)


def no_such_function(agent: str, name: str) -> str:
    """What a script is told when it calls something that is not a function of its key. It names
    the call only when it looks like a tool name, so nothing odd that was sent is repeated."""
    called = _named([name])
    return (
        f"This agent key ({agent}) has no function called {called}. Use explore to see the "
        "functions it has. Nothing was changed."
    )


NOT_A_TRUE_OR_FALSE = (
    "How a key calls Vrum has to be true (it runs code) or false (one tool at a time). Nothing "
    "was changed."
)
VERIFIER_NOT_A_TRUE_OR_FALSE = (
    "Whether a key is a verifier's has to be true or false. Nothing was changed."
)
EXPIRY_NOT_A_DAY = (
    "A key's expiry has to be a day written as YYYY-MM-DD (the last day it works), or empty for a "
    "key that never expires. Nothing was changed."
)


def key_expired_words(day: date) -> str:
    """What an agent is told when its key ran out: when, and who can fix it."""
    return (
        f"This agent key expired after {day.isoformat()}, so nothing was done. The project's "
        "owner can extend it, or make a new key (Users, then Agents, on the project's board)."
    )


class KeyExpired(Exception):
    """A key that is real and was not revoked, but whose last day has passed. It is told apart from
    a wrong or revoked key so that the agent is told why, in words."""

    def __init__(self, day: date) -> None:
        super().__init__(key_expired_words(day))
        self.day = day


def _named(names: list[str]) -> str:
    """Tool names for a refusal, leaving out anything that does not look like a tool name, so that
    nothing odd that was sent is repeated."""
    shown = [name for name in names if _A_TOOL_NAME.fullmatch(name)][:_MOST_NAMED]
    rest = len(names) - len(shown)
    if not shown:
        return "that name" if rest == 1 else f"those {rest} names"
    return ", ".join(shown) + (f" and {rest} more" if rest else "")


def clean_allowance(tools: Any, known: Collection[str]) -> list[str] | None:
    """What a key may be allowed to do, as it is kept: None for the tools Vrum offers agents, or the
    tools it may call (any the server has, offered to agents or not), sorted and without repeats. A
    list has to name at least one tool, only tools the server has, and none that only a person can
    use, nor explore or run (a key that runs code has those two, whatever its list). For a key that
    runs code the same list is what a script may call."""
    if tools is None:
        return None
    if not isinstance(tools, list) or not all(isinstance(name, str) for name in tools):
        raise AccessRefusal(
            "What a key may do has to be a list of tool names, or left out for the standard "
            "tools. Nothing was changed."
        )
    if not tools:
        raise AccessRefusal("Tick at least one tool, or leave the key with the standard tools.")
    chosen = sorted(set(tools))
    code_tools = [name for name in chosen if name in CODE_TOOLS]
    if code_tools:
        raise AccessRefusal(
            "explore and run are what a key that calls Vrum by running code gets, so they are not "
            "ticked one by one. Untick them, or choose how the key calls Vrum instead. Nothing "
            "was changed."
        )
    people_only = [name for name in chosen if name in PEOPLE_ONLY_TOOLS]
    if people_only:
        many = len(people_only) > 1
        raise AccessRefusal(
            f"Only a person can use {_named(people_only)}, so an agent key cannot be given "
            f"{'them' if many else 'it'}. Leave {'them' if many else 'it'} unticked. Nothing was "
            "changed."
        )
    unknown = [name for name in chosen if name not in known]
    if unknown:
        raise AccessRefusal(
            f"This server has no {'tools' if len(unknown) > 1 else 'tool'} called "
            f"{_named(unknown)}. Choose from the tools on the Tools page. Nothing was changed."
        )
    return chosen


def clean_code_mode(code_mode: Any) -> bool:
    """How a key calls Vrum: true to run code, false for one tool at a time. Nothing else is
    taken for either (a text or a number is refused, not guessed at)."""
    if not isinstance(code_mode, bool):
        raise AccessRefusal(NOT_A_TRUE_OR_FALSE)
    return code_mode


def clean_verifier(verifier: Any) -> bool:
    """Whether a key is a verifier's: true or false, and nothing else is taken for either."""
    if not isinstance(verifier, bool):
        raise AccessRefusal(VERIFIER_NOT_A_TRUE_OR_FALSE)
    return verifier


def clean_expiry(expires_on: Any) -> date | None:
    """The last day a key works, as a day, or None for a key that never expires (an empty value).
    A day that has already passed is refused: the way to stop a key now is to revoke it."""
    if expires_on is None or (isinstance(expires_on, str) and not expires_on.strip()):
        return None
    if not isinstance(expires_on, str):
        raise AccessRefusal(EXPIRY_NOT_A_DAY)
    try:
        day = parse_day("A key's expiry", expires_on.strip())
    except Refusal as refusal:
        raise AccessRefusal(f"{refusal} Nothing was changed.") from None
    assert day is not None
    if day < datetime.now(UTC).date():
        raise AccessRefusal(
            f"{day.isoformat()} has already passed. Choose today or a later day, or revoke the "
            "key to stop it now. Nothing was changed."
        )
    return day


def only_the_owner(what: str, *, changes: bool = True) -> AccessRefusal:
    """The refusal for someone who is in a project but is not its owner, and asks for what only
    the owner may do."""
    ending = " Nothing was changed." if changes else ""
    return AccessRefusal(f"Only the owner of this project can {what}. Ask the owner.{ending}", 403)


def hash_password(password: str) -> str:
    """The password as it is stored: 'scrypt$N$r$p$salt$hash', with a salt of its own."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MEMORY,
        dklen=32,
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        )
    )


def password_matches(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=_SCRYPT_MEMORY,
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def digest_of(secret: str) -> str:
    """What is kept of a session or a key: a hash of it. Both are long random values, so a fast
    hash is enough, and the database never holds anything that can be sent as one."""
    return hashlib.sha256(secret.encode()).hexdigest()


class AccountInfo(BaseModel):
    id: int
    email: str
    name: str
    # Whether a link that reached this address has shown it to be the person's. Nothing is
    # withheld from an account until then.
    email_verified: bool
    # Whether the account can sign in with a password. An account that signing in with a provider
    # made has none until its person chooses one.
    has_password: bool


Role = Literal["owner", "editor", "viewer"]


class ProjectInfo(BaseModel):
    id: int
    name: str
    created_at: datetime
    # What the signed-in person is in this project: its owner, or an editor or a viewer.
    role: Role


class MemberInfo(BaseModel):
    account_id: int
    name: str
    # Only the owner is told the email addresses of the others, and a person their own.
    email: str | None
    role: Role
    joined_at: datetime


class InviteInfo(BaseModel):
    id: int
    role: Role
    created_at: datetime
    expires_at: datetime
    # The name of the person who made it, as it is now.
    created_by: str
    hint: str
    # The address the invitation is for, when the owner gave one: only an account with that
    # address can accept it. sent_at is when a mail server last took the message that holds its
    # current link, so it is None when no email was sent, or when the last one was not taken.
    sent_to: str | None
    sent_at: datetime | None


class InviteMade(NamedTuple):
    """A new invitation, with its token (shown this once), and whether it was emailed. When it was
    not, but an address was given, `email_problem` says why in words: the link still works."""

    invite: InviteInfo
    token: str
    emailed: bool
    email_problem: str | None


class InvitePreview(BaseModel):
    """What a person is shown of an invitation before they accept it: nothing of the project's
    content, and the reason they could not join, if there is one."""

    project_name: str
    owner_name: str
    role: Role
    invited_by: str
    expires_at: datetime
    problem: str | None


class KeyInfo(BaseModel):
    id: int
    name: str
    hint: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    # The tools the key may call, sorted, or None when it has no list of its own and may call the
    # tools Vrum offers agents.
    tools: list[str] | None
    # True when the key calls Vrum by running code: it is shown explore and run, and the tools
    # above are what a script may call. False: one tool at a time.
    code_mode: bool
    # True when the key belongs to an agent that checks other agents' work: it may tick the checks
    # the definition of done marks [verifier] or [github], on work it did not do itself.
    verifier: bool = False
    # The last day the key works, or None for a key that never expires; `expired` says its last day
    # has passed (the server's day, UTC), so that no screen works the day out for itself.
    expires_on: date | None = None
    expired: bool = False


class KeyAccess(NamedTuple):
    """What a key that is in force lets its holder do: reach one project, as the agent it names,
    and call the tools in `tools` (the tools Vrum offers agents when that is None), one at a time
    or, when `code_mode` is set, as the functions of a script it runs. `verifier` is the key's
    flag of that name."""

    project_id: int
    key_id: int
    name: str
    tools: frozenset[str] | None = None
    code_mode: bool = False
    verifier: bool = False


class SessionInfo(BaseModel):
    id: int
    account: AccountInfo
    csrf: str
    expires_at: datetime


class SignedIn(BaseModel):
    session: SessionInfo
    # The value to put in the cookie. It is not stored anywhere: only its hash is.
    cookie: str


class ProviderPerson(NamedTuple):
    """Who a provider says someone is, as far as Vrum has understood it. The provider told the
    subject (what it says never changes for the person) and may have told an address (and whether
    it stands behind it), a name to make an account with, and a name to show beside the
    connection. None of it has been compared with an account yet."""

    provider: str
    subject: str
    email: str | None
    email_confirmed: bool
    name: str | None
    shown: str | None


class IdentityInfo(BaseModel):
    """A provider an account is connected to."""

    provider: str
    # What the provider calls the person, to say "Connected as ...", and the address it gave.
    display_name: str | None
    email: str | None
    linked_at: datetime


class SignInMethods(BaseModel):
    """The ways an account can sign in: its password, if it has one, and its providers."""

    has_password: bool
    # The provider whose sign-in made the account, if one did.
    signed_up_with: str | None
    identities: list[IdentityInfo]


_ACCOUNT_COLUMNS = (
    "id, email, display_name, email_verified_at, (password_hash IS NOT NULL) AS has_password"
)


def _account(row: dict[str, Any]) -> AccountInfo:
    return AccountInfo(
        id=row["id"],
        email=row["email"],
        name=row["display_name"],
        email_verified=row["email_verified_at"] is not None,
        has_password=row["has_password"],
    )


def mask_email(address: str) -> str:
    """An address with most of its name left out (p***@example.com), to say which address a
    message went to without giving it to someone it was not meant for."""
    name, _, domain = address.rpartition("@")
    return f"{name[:1]}***@{domain}" if name and domain else "***"


def waiting(seconds: int) -> str:
    """How long to wait, in words."""
    return f"{seconds} seconds" if seconds < 90 else f"{-(-seconds // 60)} minutes"


def _key(row: dict[str, Any]) -> KeyInfo:
    return KeyInfo(
        id=row["id"],
        name=row["name"],
        hint=row["key_hint"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
        tools=None if row["allowed_tools"] is None else sorted(row["allowed_tools"]),
        code_mode=row["code_mode"],
        verifier=row["verifier"],
        expires_on=row["expires_on"],
        expired=row["expired"],
    )


# Whether a key's last day has passed, by the database's clock and the UTC day.
_KEY_EXPIRED = "(expires_on IS NOT NULL AND expires_on < (now() AT TIME ZONE 'UTC')::date)"
_KEY_COLUMNS = (
    "id, name, key_hint, created_at, last_used_at, revoked_at, allowed_tools, code_mode, "
    f"verifier, expires_on, {_KEY_EXPIRED} AS expired"
)


def _key_access(row: dict[str, Any]) -> KeyAccess:
    tools = row["allowed_tools"]
    return KeyAccess(
        row["project_id"],
        row["id"],
        row["name"],
        None if tools is None else frozenset(tools),
        row["code_mode"],
        row["verifier"],
    )


_PROJECT_COLUMNS = "id, name, created_at, 'owner' AS role"
# A project as a person sees it, with what that person is in it: its owner, or the role the owner
# gave them. The account is the first parameter, twice, and a project's own conditions follow.
_PROJECT_OF_A_PERSON = (
    "SELECT p.id, p.name, p.created_at, "
    "CASE WHEN p.owner_id = %(account)s THEN 'owner' ELSE m.role END AS role "
    "FROM projects p LEFT JOIN project_members m "
    "ON m.project_id = p.id AND m.account_id = %(account)s "
    "WHERE (p.owner_id = %(account)s OR m.account_id IS NOT NULL)"
)
_INVITE_COLUMNS = (
    "i.id, i.role, i.created_at, i.expires_at, i.token_hint, i.sent_to, i.sent_at, "
    "c.display_name AS created_by"
)
# An invitation that can still be accepted.
_PENDING_INVITE = "i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > now()"


def _invite(row: dict[str, Any]) -> InviteInfo:
    return InviteInfo(
        id=row["id"],
        role=row["role"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        created_by=row["created_by"],
        hint=row["token_hint"],
        sent_to=row["sent_to"],
        sent_at=row["sent_at"],
    )


def _member(row: dict[str, Any], seen_by: ProjectInfo, me: int) -> MemberInfo:
    """A person of a project as the one looking at it may see them."""
    shows_email = seen_by.role == "owner" or row["account_id"] == me
    return MemberInfo(
        account_id=row["account_id"],
        name=row["name"],
        email=row["email"] if shows_email else None,
        role=row["role"],
        joined_at=row["joined_at"],
    )


def clean_email(email: str) -> str:
    cleaned = email.strip().lower()
    if len(cleaned) > MAX_EMAIL_CHARS or not _EMAIL.fullmatch(cleaned):
        raise AccessRefusal(
            "That does not look like an email address. Write it like name@example.com."
        )
    return cleaned


def clean_display_name(name: str) -> str:
    """The name a person is shown as, which every change they make is recorded with. A name that
    holds a secret in a well-known format is refused: the history would refuse to hold it."""
    shown = name.strip()
    if not shown or len(shown) > MAX_NAME_CHARS:
        raise AccessRefusal(
            f"Say what to call you, in 1 to {MAX_NAME_CHARS} characters. Every change you "
            "make is recorded with this name."
        )
    if find_secret(shown):
        raise AccessRefusal(
            "That name looks like a secret (a key or a token), so it was not used. Say what to "
            "call you."
        )
    return shown


def clean_agent_name(name: str) -> str:
    """The name of an agent's key, which is what everything the agent does is recorded under."""
    cleaned = name.strip()
    if not cleaned or len(cleaned) > MAX_NAME_CHARS:
        raise AccessRefusal(
            f"Give the key the name of the agent that will use it, in 1 to {MAX_NAME_CHARS} "
            "characters, such as api-agent. The history says which agent did what by this name."
        )
    if find_secret(cleaned):
        raise AccessRefusal(
            "That name looks like a secret (a key or a token), so it was not used. Give the key "
            "the name of the agent that will use it, such as api-agent."
        )
    return cleaned


def clean_new_password(password: str) -> str:
    if not MIN_PASSWORD_CHARS <= len(password) <= MAX_PASSWORD_CHARS:
        raise AccessRefusal(
            f"The password must be {MIN_PASSWORD_CHARS} to {MAX_PASSWORD_CHARS} characters "
            "long. A few unrelated words make a good one."
        )
    return password


# The longest thing a provider tells about someone that is kept or shown.
_MOST_OF_WHAT_A_PROVIDER_SAYS = 200


def _plain_text(raw: str | None, most: int = _MOST_OF_WHAT_A_PROVIDER_SAYS) -> str | None:
    """What a provider said, on one line, with nothing unprintable in it, or None if nothing is
    left. It is only ever shown as text."""
    if not isinstance(raw, str):
        return None
    shown = "".join(ch for ch in " ".join(raw.split()) if ch.isprintable())
    return shown[:most].strip() or None


def usable_address(raw: str | None) -> str | None:
    """An address a provider gave, written the way accounts keep it (lower case), or None when it
    is not one an account could have."""
    if not isinstance(raw, str):
        return None
    address = raw.strip().lower()
    if len(address) > MAX_EMAIL_CHARS or not address.isprintable() or not _EMAIL.fullmatch(address):
        return None
    return address


def vouched_address(person: ProviderPerson) -> str | None:
    """The address a provider stands behind as the person's, or None when it gave none, did not
    say it was confirmed, or gave one that is not an address."""
    return usable_address(person.email) if person.email_confirmed else None


def name_for_new_account(person: ProviderPerson, address: str) -> str:
    """What to call someone whose account a provider makes: the provider's name for them, or else
    what comes before the @ of their address. Never blank, and never something that looks like a
    secret."""
    for candidate in (person.name, address.partition("@")[0]):
        shown = _plain_text(candidate, MAX_NAME_CHARS)
        if shown is not None and not find_secret(shown):
            return shown
    return "New person"


def first_sign_in_outcome(
    *, exists: bool, confirmed: bool, has_one_here: bool, create_accounts: bool
) -> str:
    """What to do when a provider identity that Vrum has not seen before signs in, with an address
    the provider vouches for (and whose domain is allowed): 'link' it to the account that has
    that address, 'create' an account, or the code of why not. `exists`: an account has the
    address; `confirmed`: a link that reached its mailbox showed it to be theirs; `has_one_here`:
    that account is already connected to another identity at this provider.

    Joining by address is the one way a provider could take over an account, so it needs the
    address to be confirmed on both sides: the provider says it is the person's, and so does a
    link here. Otherwise someone who registered another person's address with a password of their
    own, which the real person only learns of later, would be joined to the real person's
    sign-in."""
    if exists:
        if not confirmed:
            return "account_exists_unconfirmed"
        # The address may have passed to someone else: Vrum does not swap one person at a provider
        # for another by address. The person signs in the way they did before and disconnects it.
        return "already_connected" if has_one_here else "link"
    return "create" if create_accounts else "signups_off"


class Accounts:
    def __init__(
        self,
        database: Database,
        *,
        mailer: Mailer | None = None,
        public_url: str = "http://localhost:8000",
        failures_per_email: int = FAILURES_PER_EMAIL,
        failures_per_client: int = FAILURES_PER_CLIENT,
        failures_per_invitee: int = FAILURES_PER_INVITEE,
    ) -> None:
        self._db = database
        # The mail server, or None when email is off. Every link in a message starts with
        # `public_url`, which the person who runs Vrum set: it is never taken from a request.
        self._mailer = mailer
        self._public_url = public_url
        self._by_email = AttemptLimiter(failures_per_email, FAILURE_WINDOW_SECONDS)
        self._by_client = AttemptLimiter(failures_per_client, FAILURE_WINDOW_SECONDS)
        # Invitation links that were not valid: one count for the signed-in account that tried
        # them and one for the network address, kept apart from the sign-in counts.
        self._invites_by_account = AttemptLimiter(failures_per_invitee, FAILURE_WINDOW_SECONDS)
        self._invites_by_client = AttemptLimiter(failures_per_client, FAILURE_WINDOW_SECONDS)
        # Links in emails that were not valid, for one network address.
        self._links_by_client = AttemptLimiter(failures_per_client, FAILURE_WINDOW_SECONDS)
        # Emails sent (each one counts, whether or not the mail server took it).
        self._invite_mail_by_owner = AttemptLimiter(INVITE_MAILS_PER_OWNER, HOUR_SECONDS)
        self._invite_mail_by_project = AttemptLimiter(INVITE_MAILS_PER_PROJECT, DAY_SECONDS)
        self._verify_mail_by_account = AttemptLimiter(VERIFY_MAILS_PER_ACCOUNT, HOUR_SECONDS)
        self._reset_by_email = AttemptLimiter(RESETS_PER_EMAIL, HOUR_SECONDS)
        self._reset_by_client = AttemptLimiter(RESETS_PER_CLIENT, HOUR_SECONDS)
        # Mail that is sent after the answer has gone, kept so that it is not forgotten midway.
        self._background: set[asyncio.Task[None]] = set()
        # Hashing a password takes a fair amount of memory, so only a few run at a time.
        self._hashing = asyncio.Semaphore(4)

    @property
    def mail_enabled(self) -> bool:
        return self._mailer is not None

    async def _hash(self, password: str) -> str:
        async with self._hashing:
            return await asyncio.to_thread(hash_password, password)

    async def _matches(self, password: str, stored: str) -> bool:
        async with self._hashing:
            return await asyncio.to_thread(password_matches, password, stored)

    def _refuse_if_guessing(self, *keys: tuple[AttemptLimiter, str]) -> None:
        wait = max(limiter.seconds_to_wait(key) for limiter, key in keys)
        if wait:
            raise AccessRefusal(
                "There have been too many failed attempts, so nothing was tried. Wait "
                f"{wait} seconds and try again.",
                429,
            )

    # -- accounts and sessions -------------------------------------------------------------------

    async def register(self, email: str, name: str, password: str, client: str) -> SignedIn:
        """Make an account, and sign it in. An address that has an account already is refused, and
        the account it has is not touched."""
        self._refuse_if_guessing((self._by_client, client))
        address = clean_email(email)
        shown = clean_display_name(name)
        clean_new_password(password)
        hashed = await self._hash(password)
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "INSERT INTO accounts (email, display_name, password_hash) VALUES (%s, %s, %s) "
                "ON CONFLICT (email) DO NOTHING "
                f"RETURNING {_ACCOUNT_COLUMNS}",
                (address, shown, hashed),
            )
            if row is None:
                self._by_client.failed(client)
                raise AccessRefusal(
                    "An account with that email address already exists, so none was made. Sign "
                    "in with it, or register with another address.",
                    409,
                )
            signed = await self._start_session(conn, _account(row))
        if self._mailer is not None:
            self._in_background(self._mail_a_link("verify", signed.session.account.id, address))
        return signed

    async def sign_in(self, email: str, password: str, client: str) -> SignedIn:
        address = email.strip().lower()
        email_key = (self._by_email, address)
        self._refuse_if_guessing(email_key, (self._by_client, client))
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                f"SELECT {_ACCOUNT_COLUMNS}, password_hash FROM accounts WHERE email = %s",
                (address,),
            )
        # An address with no account, and an account with no password, take as long as one with a
        # wrong password, so the time says nothing about which addresses have accounts or how
        # they sign in. Neither can ever match: the decoy is only there to be compared against.
        stored = row["password_hash"] if row and row["password_hash"] else _DECOY
        matches = (
            await self._matches(password, stored) if len(password) <= MAX_PASSWORD_CHARS else False
        )
        if row is None or row["password_hash"] is None or not matches:
            self._by_email.failed(address)
            self._by_client.failed(client)
            raise AccessRefusal(
                "The email address or the password is not right, so nobody was signed in.", 401
            )
        self._by_email.succeeded(address)
        async with self._db.admin_transaction() as conn:
            return await self._start_session(conn, _account(row))

    async def _start_session(self, conn: Any, account: AccountInfo) -> SignedIn:
        cookie = SESSION_PREFIX + secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        row = await fetch_one(
            conn,
            "INSERT INTO sessions (token_hash, csrf_token, account_id, expires_at) "
            "VALUES (%s, %s, %s, now() + make_interval(hours => %s)) RETURNING id, expires_at",
            (digest_of(cookie), csrf, account.id, SESSION_HOURS),
        )
        assert row is not None
        return SignedIn(
            session=SessionInfo(
                id=row["id"], account=account, csrf=csrf, expires_at=row["expires_at"]
            ),
            cookie=cookie,
        )

    async def session(self, cookie: str | None) -> SessionInfo | None:
        """Who a session cookie belongs to, or None when it is wrong, expired or signed out."""
        if not cookie:
            return None
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "SELECT s.id, s.csrf_token, s.expires_at, a.id AS account_id, a.email, "
                "a.display_name, a.email_verified_at, "
                "(a.password_hash IS NOT NULL) AS has_password "
                "FROM sessions s "
                "JOIN accounts a ON a.id = s.account_id "
                "WHERE s.token_hash = %s AND s.ended_at IS NULL AND s.expires_at > now()",
                (digest_of(cookie),),
            )
        if row is None:
            return None
        return SessionInfo(
            id=row["id"],
            account=AccountInfo(
                id=row["account_id"],
                email=row["email"],
                name=row["display_name"],
                email_verified=row["email_verified_at"] is not None,
                has_password=row["has_password"],
            ),
            csrf=row["csrf_token"],
            expires_at=row["expires_at"],
        )

    async def sign_out(self, session_id: int) -> None:
        async with self._db.admin_transaction() as conn:
            await conn.execute(
                "UPDATE sessions SET ended_at = now() WHERE id = %s AND ended_at IS NULL",
                (session_id,),
            )

    async def rename(self, account_id: int, name: str) -> AccountInfo:
        """Change the name this person is shown as. It is what their changes are recorded with from
        now on, and it takes effect in every session of the account at once. What was recorded
        before keeps the name it was recorded with. A name that another person of one of this
        person's projects already uses is refused, since the history tells people apart by it."""
        shown = clean_display_name(name)
        async with self._db.admin_transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_PEOPLE_LOCK,))
            clash = await fetch_one(
                conn,
                "SELECT p.name FROM projects p WHERE "
                "(p.owner_id = %(me)s OR EXISTS (SELECT 1 FROM project_members m "
                "WHERE m.project_id = p.id AND m.account_id = %(me)s)) AND EXISTS ("
                "SELECT 1 FROM accounts o WHERE o.id <> %(me)s "
                "AND lower(o.display_name) = lower(%(name)s) AND (o.id = p.owner_id OR EXISTS ("
                "SELECT 1 FROM project_members m2 WHERE m2.project_id = p.id "
                "AND m2.account_id = o.id))) ORDER BY p.id LIMIT 1",
                {"me": account_id, "name": shown},
            )
            if clash is not None:
                raise AccessRefusal(
                    f"Someone in the project {clash['name']!r} already uses the name {shown}, so "
                    "your name was not changed. Choose another name: the history tells people "
                    "apart by it.",
                    409,
                )
            row = await fetch_one(
                conn,
                f"UPDATE accounts SET display_name = %s WHERE id = %s RETURNING {_ACCOUNT_COLUMNS}",
                (shown, account_id),
            )
        assert row is not None
        return _account(row)

    async def change_password(
        self, session: SessionInfo, current: str, new: str, client: str
    ) -> None:
        """Set a new password for the signed-in account, and end its other sessions, so that a
        session someone else holds does not outlive the change. A wrong current password counts
        against the same limit as a failed sign-in. An account that has no password yet (a
        provider made it) needs no current one: the person is signed in, and this gives it its
        first, which lets them sign in with their email too."""
        email = session.account.email
        email_key = (self._by_email, email)
        self._refuse_if_guessing(email_key, (self._by_client, client))
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "SELECT password_hash FROM accounts WHERE id = %s",
                (session.account.id,),
            )
        assert row is not None
        if row["password_hash"] is not None:
            matches = (
                await self._matches(current, row["password_hash"])
                if len(current) <= MAX_PASSWORD_CHARS
                else False
            )
            if not matches:
                self._by_email.failed(email)
                self._by_client.failed(client)
                raise AccessRefusal(
                    "The current password is not right, so the password was not changed."
                )
            self._by_email.succeeded(email)
        clean_new_password(new)
        hashed = await self._hash(new)
        async with self._db.admin_transaction() as conn:
            await conn.execute(
                "UPDATE accounts SET password_hash = %s WHERE id = %s",
                (hashed, session.account.id),
            )
            await conn.execute(
                "UPDATE sessions SET ended_at = now() "
                "WHERE account_id = %s AND id <> %s AND ended_at IS NULL",
                (session.account.id, session.id),
            )

    # -- other ways to sign in: OpenID Connect, GitHub, LDAP -------------------------------------
    # sso.py (and the modules it uses) find out who the provider says someone is. What that means
    # for an account is decided only here.

    async def sign_in_with_provider(
        self,
        person: ProviderPerson,
        *,
        provider_name: str,
        create_accounts: bool,
        allowed_domains: Collection[str],
    ) -> SignedIn:
        """Sign in the account this provider identity belongs to, joining or making one when it is
        new (see `first_sign_in_outcome`). Everything happens in one transaction, with a lock on
        the address, so two first sign-ins at the same moment make one account. Refused with an
        `SsoRefusal` that says why, and nothing is changed."""
        address = vouched_address(person)
        for _ in range(2):
            try:
                async with self._db.admin_transaction() as conn:
                    return await self._provider_sign_in(
                        conn, person, address, provider_name, create_accounts, allowed_domains
                    )
            except psycopg.errors.UniqueViolation:
                # Someone made this identity or this account an instant ago: look again.
                continue
            except psycopg.errors.CheckViolation:
                break
        raise SsoRefusal("failed", provider_name)

    async def _identity_account(self, conn: Any, person: ProviderPerson) -> AccountInfo | None:
        row = await fetch_one(
            conn,
            "SELECT a.id, a.email, a.display_name, a.email_verified_at, "
            "(a.password_hash IS NOT NULL) AS has_password FROM account_identities i "
            "JOIN accounts a ON a.id = i.account_id WHERE i.provider = %s AND i.subject = %s",
            (person.provider, person.subject),
        )
        return None if row is None else _account(row)

    async def _add_identity(
        self, conn: Any, account_id: int, person: ProviderPerson, address: str | None
    ) -> None:
        await conn.execute(
            "INSERT INTO account_identities (provider, subject, account_id, email, display_name) "
            "VALUES (%s, %s, %s, %s, %s)",
            (person.provider, person.subject, account_id, address, _plain_text(person.shown)),
        )

    async def _provider_sign_in(
        self,
        conn: Any,
        person: ProviderPerson,
        address: str | None,
        provider_name: str,
        create_accounts: bool,
        allowed_domains: Collection[str],
    ) -> SignedIn:
        account = await self._identity_account(conn, person)
        if account is not None:
            # Someone who has signed in this way before is that account. Their address is not
            # looked at again: people change addresses.
            shown = _plain_text(person.shown)
            if shown is not None:
                await conn.execute(
                    "UPDATE account_identities SET display_name = %s WHERE provider = %s "
                    "AND subject = %s AND display_name IS DISTINCT FROM %s",
                    (shown, person.provider, person.subject, shown),
                )
            return await self._start_session(conn, account)
        if address is None:
            raise SsoRefusal("no_verified_email", provider_name)
        if allowed_domains and address.rpartition("@")[2] not in allowed_domains:
            raise SsoRefusal("domain_not_allowed", provider_name)
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"vrum.sso.address:{address}",)
        )
        account = await self._identity_account(conn, person)
        if account is not None:
            return await self._start_session(conn, account)
        existing = await fetch_one(
            conn,
            f"SELECT {_ACCOUNT_COLUMNS}, EXISTS (SELECT 1 FROM account_identities i "
            "WHERE i.account_id = accounts.id AND i.provider = %s) AS has_one_here "
            "FROM accounts WHERE email = %s FOR UPDATE",
            (person.provider, address),
        )
        outcome = first_sign_in_outcome(
            exists=existing is not None,
            confirmed=existing is not None and existing["email_verified_at"] is not None,
            has_one_here=existing is not None and existing["has_one_here"],
            create_accounts=create_accounts,
        )
        if outcome in SSO_WORDS:
            raise SsoRefusal(outcome, provider_name)
        if existing is not None:
            account = _account(existing)
        else:
            made = await fetch_one(
                conn,
                "INSERT INTO accounts (email, display_name, email_verified_at, signed_up_with) "
                f"VALUES (%s, %s, now(), %s) RETURNING {_ACCOUNT_COLUMNS}",
                (address, name_for_new_account(person, address), person.provider),
            )
            assert made is not None
            account = _account(made)
        await self._add_identity(conn, account.id, person, address)
        return await self._start_session(conn, account)

    async def link_provider(
        self, account_id: int, session_id: int, person: ProviderPerson, *, provider_name: str
    ) -> None:
        """Connect this provider identity to the account of a person who is signed in, so that they
        can sign in with it. The session has to be the one that started it and still in force. An
        identity that belongs to another account is refused, and so is a second identity at one
        provider: the first is disconnected first."""
        try:
            async with self._db.admin_transaction() as conn:
                live = await fetch_one(
                    conn,
                    "SELECT 1 AS yes FROM sessions WHERE id = %s AND account_id = %s "
                    "AND ended_at IS NULL AND expires_at > now()",
                    (session_id, account_id),
                )
                if live is None:
                    raise SsoRefusal("link_session_lost", provider_name)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"vrum.sso.account:{account_id}",),
                )
                owner = await fetch_one(
                    conn,
                    "SELECT account_id FROM account_identities "
                    "WHERE provider = %s AND subject = %s",
                    (person.provider, person.subject),
                )
                if owner is not None:
                    if owner["account_id"] == account_id:
                        return
                    raise SsoRefusal("identity_taken", provider_name)
                mine = await fetch_one(
                    conn,
                    "SELECT 1 AS yes FROM account_identities WHERE account_id = %s "
                    "AND provider = %s",
                    (account_id, person.provider),
                )
                if mine is not None:
                    raise SsoRefusal("already_connected", provider_name)
                await self._add_identity(conn, account_id, person, usable_address(person.email))
        except psycopg.errors.UniqueViolation:
            raise SsoRefusal("identity_taken", provider_name) from None

    async def unlink_provider(
        self,
        account_id: int,
        session_id: int,
        provider: str,
        *,
        provider_name: str,
        configured: Collection[str],
    ) -> None:
        """Disconnect a provider from the account of a person who is signed in. Refused when it is
        the only way the account can sign in: with no password and no other provider that is set
        up, the person would be locked out. The account's other sessions end, since the person
        may be disconnecting it because someone else got into their account there."""
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "SELECT (password_hash IS NOT NULL) AS has_password FROM accounts WHERE id = %s "
                "FOR UPDATE",
                (account_id,),
            )
            mine = await fetch_one(
                conn,
                "SELECT 1 AS yes FROM account_identities WHERE account_id = %s AND provider = %s",
                (account_id, provider),
            )
            if row is None or mine is None:
                raise SsoRefusal("not_connected", provider_name)
            others = await fetch_one(
                conn,
                "SELECT count(*) AS n FROM account_identities WHERE account_id = %s "
                "AND provider <> %s AND provider = ANY(%s)",
                (account_id, provider, list(configured)),
            )
            assert others is not None
            if not row["has_password"] and others["n"] == 0:
                raise SsoRefusal("last_way_in", provider_name)
            await conn.execute(
                "DELETE FROM account_identities WHERE account_id = %s AND provider = %s",
                (account_id, provider),
            )
            await conn.execute(
                "UPDATE sessions SET ended_at = now() "
                "WHERE account_id = %s AND id <> %s AND ended_at IS NULL",
                (account_id, session_id),
            )

    async def sign_in_methods(self, account_id: int, configured: Collection[str]) -> SignInMethods:
        """How this account can sign in: whether it has a password, and the providers that are set
        up on this server that it is connected to."""
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "SELECT (password_hash IS NOT NULL) AS has_password, signed_up_with "
                "FROM accounts WHERE id = %s",
                (account_id,),
            )
            rows = await fetch_all(
                conn,
                "SELECT provider, display_name, email, linked_at FROM account_identities "
                "WHERE account_id = %s AND provider = ANY(%s) ORDER BY linked_at, provider",
                (account_id, list(configured)),
            )
        assert row is not None
        return SignInMethods(
            has_password=row["has_password"],
            signed_up_with=row["signed_up_with"],
            identities=[IdentityInfo(**one) for one in rows],
        )

    # -- email: links to confirm an address and to choose a new password -------------------------

    def _in_background(self, work: Coroutine[Any, Any, None]) -> None:
        """Do this after the answer has gone, so that how long it takes, and whether there was
        anything to do, cannot be seen by whoever asked."""
        task = asyncio.create_task(self._quietly(work))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _quietly(self, work: Coroutine[Any, Any, None]) -> None:
        try:
            await work
        except MailRefusal:
            pass
        except Exception as error:
            event(log, logging.ERROR, "mail_task_failed", error=type(error).__name__)

    def _refuse_if_over(self, limiter: AttemptLimiter, key: str, what: str) -> None:
        wait = limiter.seconds_to_wait(key)
        if wait:
            raise AccessRefusal(
                f"Too many {what} were asked for lately, so nothing was sent. Wait about "
                f"{waiting(wait)} and try again.",
                429,
            )

    async def _mail_a_link(self, kind: str, account_id: int, address: str) -> None:
        """Make a link of this kind ('verify' or 'reset') for the account and send it to the
        address. When the mail server does not take the message, the link is forgotten and a
        MailRefusal says why."""
        assert self._mailer is not None
        token = (VERIFY_PREFIX if kind == "verify" else RESET_PREFIX) + secrets.token_urlsafe(32)
        hours = VERIFY_HOURS if kind == "verify" else RESET_HOURS
        async with self._db.admin_transaction() as conn:
            # Used and expired tokens are of no use to anyone, so old ones go as new ones are made.
            await conn.execute(
                "DELETE FROM email_tokens WHERE expires_at < now() - interval '7 days'"
            )
            row = await fetch_one(
                conn,
                "INSERT INTO email_tokens (account_id, kind, token_hash, sent_to, expires_at) "
                "VALUES (%s, %s, %s, %s, now() + make_interval(hours => %s)) RETURNING id",
                (account_id, kind, digest_of(token), address, hours),
            )
        assert row is not None
        if kind == "verify":
            link = f"{self._public_url}/board/verify/{token}"
            message = verification_message(link=link, expires_in_hours=hours)
        else:
            link = f"{self._public_url}/board/reset/{token}"
            message = reset_message(link=link, expires_in_hours=hours)
        try:
            await self._mailer.send(
                address,
                message.subject,
                message.text,
                kind="verification" if kind == "verify" else "password_reset",
            )
        except MailRefusal:
            async with self._db.admin_transaction() as conn:
                await conn.execute("DELETE FROM email_tokens WHERE id = %s", (row["id"],))
            raise

    async def _live_token(
        self, conn: Any, kind: str, token: str, *, spend: bool
    ) -> dict[str, Any] | None:
        """The account and address a link's token was made for, if it can still be used: not
        spent, not expired, and the address is still the account's. With `spend` it is spent in
        the same step, so that it works once."""
        prefix = VERIFY_PREFIX if kind == "verify" else RESET_PREFIX
        if not token.startswith(prefix) or len(token) > 200:
            return None
        live = (
            "t.kind = %s AND t.token_hash = %s AND t.used_at IS NULL AND t.expires_at > now() "
            "AND a.id = t.account_id AND a.email = t.sent_to"
        )
        params = (kind, digest_of(token))
        if spend:
            return await fetch_one(
                conn,
                "UPDATE email_tokens t SET used_at = now() FROM accounts a "
                f"WHERE {live} RETURNING t.account_id, t.sent_to",
                params,
            )
        return await fetch_one(
            conn,
            f"SELECT t.account_id, t.sent_to FROM email_tokens t, accounts a WHERE {live}",
            params,
        )

    async def send_verification(self, account: AccountInfo) -> None:
        """Send the signed-in person a link that confirms their address. A few an hour."""
        if self._mailer is None:
            raise AccessRefusal(VERIFY_NO_MAIL, 404)
        if account.email_verified:
            raise AccessRefusal("This address is already confirmed, so nothing was sent.", 409)
        self._refuse_if_over(self._verify_mail_by_account, str(account.id), "confirmation emails")
        self._verify_mail_by_account.failed(str(account.id))
        try:
            await self._mail_a_link("verify", account.id, account.email)
        except MailRefusal as problem:
            raise AccessRefusal(f"The confirmation email was not sent. {problem}", 502) from None

    async def verify_email(self, token: str, client: str) -> str:
        """Confirm the address a confirmation link was sent to. It needs no sign-in, since it is
        opened from a mailbox. The link works once; a wrong, used or expired one is refused in one
        set of words. Returns the address."""
        self._refuse_if_guessing((self._links_by_client, client))
        async with self._db.admin_transaction() as conn:
            row = await self._live_token(conn, "verify", token, spend=True)
            if row is not None:
                await conn.execute(
                    "UPDATE accounts SET email_verified_at = coalesce(email_verified_at, now()) "
                    "WHERE id = %s",
                    (row["account_id"],),
                )
                await conn.execute(
                    "UPDATE email_tokens SET used_at = now() "
                    "WHERE account_id = %s AND kind = 'verify' AND used_at IS NULL",
                    (row["account_id"],),
                )
        if row is None:
            self._links_by_client.failed(client)
            raise AccessRefusal(VERIFY_LINK_NOT_VALID, 400)
        return str(row["sent_to"])

    async def request_reset(self, email: str, client: str) -> None:
        """Ask for a link to choose a new password, to be sent to this address. It answers the
        same, after the same work, whether or not the address has an account: the lookup and the
        sending happen afterwards, in the background. Five requests an hour for one address (the
        rest are quietly ignored, so that nobody can lock someone out of asking) and twenty for
        one network address (the rest are refused)."""
        if self._mailer is None:
            raise AccessRefusal(RESET_NO_MAIL, 404)
        self._refuse_if_over(self._reset_by_client, client, "password reset requests")
        address = clean_email(email)
        self._reset_by_client.failed(client)
        if self._reset_by_email.seconds_to_wait(address):
            return
        self._reset_by_email.failed(address)
        self._in_background(self._mail_reset(address))

    async def _mail_reset(self, address: str) -> None:
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(conn, "SELECT id FROM accounts WHERE email = %s", (address,))
        if row is not None:
            await self._mail_a_link("reset", row["id"], address)

    async def check_reset_link(self, token: str, client: str) -> None:
        """Whether a reset link can still be used, without using it (the page that asks for the
        new password asks this first). Nothing is spent by asking."""
        if self._mailer is None:
            raise AccessRefusal(RESET_NO_MAIL, 404)
        self._refuse_if_guessing((self._links_by_client, client))
        async with self._db.admin_transaction() as conn:
            row = await self._live_token(conn, "reset", token, spend=False)
        if row is None:
            self._links_by_client.failed(client)
            raise AccessRefusal(RESET_LINK_NOT_VALID, 400)

    async def reset_password(self, token: str, new: str, client: str) -> None:
        """Choose a new password with a reset link. The link works once (and so do the person's
        other reset links: they are spent too), every session of the account ends, and the address
        counts as confirmed, since the link reached its mailbox. Agent keys are not touched. A new
        password that is not allowed is refused before the link is spent."""
        if self._mailer is None:
            raise AccessRefusal(RESET_NO_MAIL, 404)
        self._refuse_if_guessing((self._links_by_client, client))
        clean_new_password(new)
        hashed = await self._hash(new)
        async with self._db.admin_transaction() as conn:
            row = await self._live_token(conn, "reset", token, spend=True)
            if row is not None:
                await conn.execute(
                    "UPDATE accounts SET password_hash = %s, "
                    "email_verified_at = coalesce(email_verified_at, now()) WHERE id = %s",
                    (hashed, row["account_id"]),
                )
                await conn.execute(
                    "UPDATE email_tokens SET used_at = now() "
                    "WHERE account_id = %s AND kind = 'reset' AND used_at IS NULL",
                    (row["account_id"],),
                )
                await conn.execute(
                    "UPDATE sessions SET ended_at = now() "
                    "WHERE account_id = %s AND ended_at IS NULL",
                    (row["account_id"],),
                )
        if row is None:
            self._links_by_client.failed(client)
            raise AccessRefusal(RESET_LINK_NOT_VALID, 400)
        self._by_email.succeeded(row["sent_to"])

    # -- projects --------------------------------------------------------------------------------

    async def projects(self, account_id: int) -> list[ProjectInfo]:
        """The projects this person owns and the ones they were invited to, in the order they were
        made, each with what the person is in it."""
        async with self._db.admin_transaction() as conn:
            rows = await fetch_all(
                conn,
                f"{_PROJECT_OF_A_PERSON} ORDER BY p.id",
                {"account": account_id},
            )
        return [ProjectInfo(**row) for row in rows]

    async def create_project(self, account_id: int, name: str) -> ProjectInfo:
        cleaned = name.strip()
        if not cleaned or len(cleaned) > MAX_NAME_CHARS:
            raise AccessRefusal(f"Give the project a name, in 1 to {MAX_NAME_CHARS} characters.")
        try:
            async with self._db.admin_transaction() as conn:
                row = await fetch_one(
                    conn,
                    "INSERT INTO projects (owner_id, name) VALUES (%s, %s) "
                    f"RETURNING {_PROJECT_COLUMNS}",
                    (account_id, cleaned),
                )
        except psycopg.errors.UniqueViolation:
            raise AccessRefusal(
                f"You already have a project called {cleaned!r}. Choose another name.", 409
            ) from None
        assert row is not None
        return ProjectInfo(**row)

    async def project_of(self, account_id: int, project_id: int) -> ProjectInfo:
        """One of the projects this account owns or was invited to, with what the account is in
        it. A project that does not exist, one that belongs to someone else and one the person is
        not in are refused in the same words, so that the refusal says nothing about other
        people's projects. A person who was removed is refused at once, on their next request."""
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                f"{_PROJECT_OF_A_PERSON} AND p.id = %(project)s",
                {"account": account_id, "project": project_id},
            )
        if row is None:
            raise AccessRefusal(NO_SUCH_PROJECT, 404)
        return ProjectInfo(**row)

    async def owned_project(
        self, account_id: int, project_id: int, *, to: str, changes: bool = True
    ) -> ProjectInfo:
        """A project of which this account is the owner. Someone who is only an editor or a viewer
        in it is refused in words that say what only the owner can do (`to`, "make agent keys")."""
        project = await self.project_of(account_id, project_id)
        if project.role != "owner":
            raise only_the_owner(to, changes=changes)
        return project

    # -- agent keys ------------------------------------------------------------------------------

    async def create_key(
        self,
        account_id: int,
        project_id: int,
        name: str,
        tools: Any = None,
        *,
        known_tools: Collection[str] = (),
        code_mode: Any = False,
        verifier: Any = False,
        expires_on: Any = None,
    ) -> tuple[KeyInfo, str]:
        """A new key for an agent to reach this project, named for the agent: that name is what
        everything the agent does is recorded under, so it is the name of no other key in force.
        `tools` is what the key may call (see `clean_allowance`, checked against `known_tools`,
        the tools the server has), or None for the tools Vrum offers agents. `code_mode` is how it
        calls them: one at a time (false) or by running code (true). `verifier` says the agent
        checks other agents' work (see `Identity.verifier`). `expires_on` is the last day it works
        (see `clean_expiry`), or None for never. The key itself is returned this once and kept
        nowhere: only a hash of it is."""
        await self.owned_project(account_id, project_id, to="make agent keys")
        cleaned = clean_agent_name(name)
        allowed = clean_allowance(tools, known_tools)
        by_code = clean_code_mode(code_mode)
        checks = clean_verifier(verifier)
        last_day = clean_expiry(expires_on)
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        try:
            async with self._db.admin_transaction() as conn:
                row = await fetch_one(
                    conn,
                    "INSERT INTO agent_keys "
                    "(project_id, name, key_hash, key_hint, allowed_tools, code_mode, verifier, "
                    "expires_on) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_KEY_COLUMNS}",
                    (
                        project_id,
                        cleaned,
                        digest_of(key),
                        key[:8],
                        allowed,
                        by_code,
                        checks,
                        last_day,
                    ),
                )
        except psycopg.errors.UniqueViolation:
            async with self._db.admin_transaction() as conn:
                existing = await fetch_one(
                    conn,
                    "SELECT name FROM agent_keys WHERE project_id = %s AND lower(name) = lower(%s) "
                    "AND revoked_at IS NULL",
                    (project_id, cleaned),
                )
            shown = existing["name"] if existing else cleaned
            raise AccessRefusal(
                f"This project already has an agent key called {shown!r}. Two agents that share "
                "a name would be one agent in the history: give each agent a key and a name of "
                "its own, or revoke that key first.",
                409,
            ) from None
        assert row is not None
        return _key(row), key

    async def keys(self, account_id: int, project_id: int) -> list[KeyInfo]:
        await self.owned_project(account_id, project_id, to="see agent keys", changes=False)
        async with self._db.admin_transaction() as conn:
            rows = await fetch_all(
                conn,
                f"SELECT {_KEY_COLUMNS} FROM agent_keys WHERE project_id = %s ORDER BY id",
                (project_id,),
            )
        return [_key(row) for row in rows]

    async def revoke_key(self, account_id: int, project_id: int, key_id: int) -> KeyInfo:
        """End a key. It takes effect on the agent's next request, even on a connection that is
        already open."""
        await self.owned_project(account_id, project_id, to="revoke agent keys")
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "UPDATE agent_keys SET revoked_at = coalesce(revoked_at, now()) "
                f"WHERE id = %s AND project_id = %s RETURNING {_KEY_COLUMNS}",
                (key_id, project_id),
            )
        if row is None:
            raise AccessRefusal(NO_SUCH_KEY, 404)
        return _key(row)

    async def set_key_tools(
        self,
        account_id: int,
        project_id: int,
        key_id: int,
        tools: Any,
        *,
        known_tools: Collection[str],
    ) -> KeyInfo:
        """Change what a key may call (None for the tools Vrum offers agents; see
        `clean_allowance`). It takes effect on the agent's next request, even on a connection that
        is already open. A key that was revoked has nothing left to change."""
        await self.owned_project(account_id, project_id, to="change what an agent key may do")
        allowed = clean_allowance(tools, known_tools)
        return await self._change_key(project_id, key_id, "allowed_tools", allowed)

    async def set_key_mode(
        self, account_id: int, project_id: int, key_id: int, code_mode: Any
    ) -> KeyInfo:
        """Change how a key calls Vrum: by running code (true) or one tool at a time (false). It
        takes effect on the agent's next request, even on a connection that is already open."""
        await self.owned_project(account_id, project_id, to="change how an agent key calls Vrum")
        return await self._change_key(project_id, key_id, "code_mode", clean_code_mode(code_mode))

    async def set_key_verifier(
        self, account_id: int, project_id: int, key_id: int, verifier: Any
    ) -> KeyInfo:
        """Mark a key as a verifier's (true) or not (false). It takes effect on the agent's next
        request, even on a connection that is already open."""
        await self.owned_project(account_id, project_id, to="change whether an agent key verifies")
        return await self._change_key(project_id, key_id, "verifier", clean_verifier(verifier))

    async def set_key_expiry(
        self, account_id: int, project_id: int, key_id: int, expires_on: Any
    ) -> KeyInfo:
        """Set the last day a key works, or clear it (an empty value) so that it never expires. A
        key whose day has passed works again from the moment it is changed to a day that has not.
        It takes effect on the agent's next request, even on a connection that is already open."""
        await self.owned_project(account_id, project_id, to="change when an agent key expires")
        return await self._change_key(project_id, key_id, "expires_on", clean_expiry(expires_on))

    async def _change_key(self, project_id: int, key_id: int, column: str, value: Any) -> KeyInfo:
        """Set one column of a key that is in force (the column is always one of this module's
        own names). A key that does not exist, or was revoked, is refused in words."""
        existing: dict[str, Any] | None = None
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                f"UPDATE agent_keys SET {column} = %s "
                "WHERE id = %s AND project_id = %s AND revoked_at IS NULL "
                f"RETURNING {_KEY_COLUMNS}",
                (value, key_id, project_id),
            )
            if row is None:
                existing = await fetch_one(
                    conn,
                    "SELECT id FROM agent_keys WHERE id = %s AND project_id = %s",
                    (key_id, project_id),
                )
        if row is None:
            raise AccessRefusal(
                NO_SUCH_KEY if existing is None else KEY_WAS_REVOKED,
                404 if existing is None else 409,
            )
        return _key(row)

    async def project_for_key(self, key: str) -> KeyAccess | None:
        """What a key lets its holder do (reach one project, as the agent the key is named for, and
        call the tools it is allowed, one at a time or by running code), or None when it is not a
        key, or was revoked. A key whose last day has passed raises `KeyExpired`, which says so."""
        digest = digest_of(key)
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "SELECT project_id, id, name, allowed_tools, code_mode, verifier, expires_on, "
                f"{_KEY_EXPIRED} AS expired FROM agent_keys "
                "WHERE key_hash = %s AND revoked_at IS NULL",
                (digest,),
            )
            if row is not None and row["expired"]:
                raise KeyExpired(row["expires_on"])
            if row is not None:
                # Noted at most once a minute, so that busy agents do not write on every call.
                await conn.execute(
                    "UPDATE agent_keys SET last_used_at = now() WHERE key_hash = %s "
                    "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')",
                    (digest,),
                )
        return None if row is None else _key_access(row)

    async def access_of_key(self, key_id: int) -> KeyAccess | None:
        """What a key that is in force lets its holder do, by the key's number, or None when it
        was revoked (`KeyExpired` when its last day has passed). A script that is running asks this
        before each of its calls (it holds the key's number, never the key), so a key that was
        revoked, expired or changed while the script ran is seen at the script's next call, as it
        is at an agent's next request."""
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "SELECT project_id, id, name, allowed_tools, code_mode, verifier, expires_on, "
                f"{_KEY_EXPIRED} AS expired FROM agent_keys "
                "WHERE id = %s AND revoked_at IS NULL",
                (key_id,),
            )
        if row is not None and row["expired"]:
            raise KeyExpired(row["expires_on"])
        return None if row is None else _key_access(row)

    # -- the people of a project -----------------------------------------------------------------

    async def members(self, account_id: int, project_id: int) -> list[MemberInfo]:
        """The owner, first, and then the people the owner invited, in the order they joined."""
        project = await self.project_of(account_id, project_id)
        async with self._db.admin_transaction() as conn:
            rows = await fetch_all(
                conn,
                "SELECT a.id AS account_id, a.display_name AS name, a.email, 'owner' AS role, "
                "p.created_at AS joined_at, 0 AS ordering FROM projects p "
                "JOIN accounts a ON a.id = p.owner_id WHERE p.id = %(project)s "
                "UNION ALL "
                "SELECT a.id, a.display_name, a.email, m.role, m.added_at, 1 "
                "FROM project_members m JOIN accounts a ON a.id = m.account_id "
                "WHERE m.project_id = %(project)s ORDER BY ordering, joined_at, account_id",
                {"project": project_id},
            )
        return [_member(row, project, account_id) for row in rows]

    async def _invited_person(
        self, conn: Any, project_id: int, person_id: int
    ) -> dict[str, Any] | None:
        return await fetch_one(
            conn,
            "SELECT a.id AS account_id, a.display_name AS name, a.email, m.role, "
            "m.added_at AS joined_at FROM project_members m "
            "JOIN accounts a ON a.id = m.account_id "
            "WHERE m.project_id = %s AND m.account_id = %s",
            (project_id, person_id),
        )

    async def change_role(
        self, account_id: int, project_id: int, person_id: int, role: str
    ) -> MemberInfo:
        """Make an invited person an editor or a viewer. Only the owner does it, and it takes
        effect on that person's next request."""
        project = await self.owned_project(
            account_id, project_id, to="change what the people of this project may do"
        )
        if role not in ROLES_TO_GIVE:
            raise AccessRefusal("Say which role the person gets: editor or viewer.")
        async with self._db.admin_transaction() as conn:
            changed = await fetch_one(
                conn,
                "UPDATE project_members SET role = %s WHERE project_id = %s AND account_id = %s "
                "RETURNING account_id",
                (role, project_id, person_id),
            )
            row = await self._invited_person(conn, project_id, person_id) if changed else None
        if row is None:
            if person_id == account_id:
                raise AccessRefusal(
                    "You own this project, and the owner's role cannot be changed.", 409
                )
            raise AccessRefusal(NO_SUCH_MEMBER, 404)
        return _member(row, project, account_id)

    async def remove_person(self, account_id: int, project_id: int, person_id: int) -> MemberInfo:
        """Take a person out of a project: the owner does it to anyone who was invited, and a
        person does it to themselves to leave. It takes effect on their next request, which is
        refused like one for a project that does not exist. What they did stays in the history
        under their name. The owner cannot leave or be removed."""
        project = await self.project_of(account_id, project_id)
        if person_id == account_id and project.role == "owner":
            raise AccessRefusal(
                "You own this project, so you cannot leave it or be removed from it.", 409
            )
        if person_id != account_id and project.role != "owner":
            raise only_the_owner("remove other people from this project")
        async with self._db.admin_transaction() as conn:
            row = await self._invited_person(conn, project_id, person_id)
            if row is not None:
                await conn.execute(
                    "DELETE FROM project_members WHERE project_id = %s AND account_id = %s",
                    (project_id, person_id),
                )
        if row is None:
            raise AccessRefusal(NO_SUCH_MEMBER, 404)
        return _member(row, project, account_id)

    # -- invitations -----------------------------------------------------------------------------

    async def create_invite(
        self, account_id: int, project_id: int, role: str, email: str | None = None
    ) -> InviteMade:
        """An invitation to join this project with a role, for the owner to hand to one person.
        Its token is returned this once and kept nowhere: only a hash of it is. With an email
        address the invitation is for that address only (an account with another cannot accept
        it) and the link is also sent to it; the invitation stands whether or not the message is
        sent, and when it is not, `email_problem` says why."""
        project = await self.owned_project(account_id, project_id, to="invite people")
        if role not in ROLES_TO_GIVE:
            raise AccessRefusal("Say which role the invited person gets: editor or viewer.")
        address = None
        if email is not None:
            if self._mailer is None:
                raise AccessRefusal(INVITE_NO_MAIL)
            address = clean_email(email)
            if not is_plain_address(address):
                raise AccessRefusal(
                    "That address cannot be sent to. Write it like name@example.com, with one "
                    "address and nothing else."
                )
        token = INVITE_PREFIX + secrets.token_urlsafe(32)
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "WITH i AS (INSERT INTO project_invites (project_id, role, token_hash, "
                "token_hint, created_by, expires_at, sent_to) VALUES (%s, %s, %s, %s, %s, "
                "now() + make_interval(days => %s), %s) RETURNING *) "
                f"SELECT {_INVITE_COLUMNS} FROM i JOIN accounts c ON c.id = i.created_by",
                (project_id, role, digest_of(token), token[:8], account_id, INVITE_DAYS, address),
            )
        assert row is not None
        invite = _invite(row)
        if address is None:
            return InviteMade(invite, token, False, None)
        wait = self._invite_mail_wait(account_id, project_id)
        if wait:
            return InviteMade(
                invite,
                token,
                False,
                "Too many invitation emails were sent lately, so this one was not sent. Wait "
                f"about {waiting(wait)} and use Resend, or copy the link and send it yourself.",
            )
        problem = await self._mail_invite(project, invite, token, address, account_id)
        if problem is None:
            invite = await self._note_sent(invite)
        return InviteMade(invite, token, problem is None, problem)

    def _invite_mail_wait(self, owner_id: int, project_id: int) -> int:
        return max(
            self._invite_mail_by_owner.seconds_to_wait(str(owner_id)),
            self._invite_mail_by_project.seconds_to_wait(str(project_id)),
        )

    async def _mail_invite(
        self, project: ProjectInfo, invite: InviteInfo, token: str, address: str, owner_id: int
    ) -> str | None:
        """Send the message that holds an invitation's link, and say in words why it was not sent,
        or None when the mail server took it. Each attempt counts against the limits."""
        assert self._mailer is not None
        self._invite_mail_by_owner.failed(str(owner_id))
        self._invite_mail_by_project.failed(str(project.id))
        message = invitation_message(
            inviter=invite.created_by,
            project=project.name,
            role=invite.role,
            link=f"{self._public_url}/board/invite/{token}",
            expires_in_days=INVITE_DAYS,
        )
        try:
            await self._mailer.send(address, message.subject, message.text, kind="invitation")
        except MailRefusal as problem:
            return str(problem)
        return None

    async def _note_sent(self, invite: InviteInfo) -> InviteInfo:
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "UPDATE project_invites SET sent_at = now() WHERE id = %s RETURNING sent_at",
                (invite.id,),
            )
        assert row is not None
        return invite.model_copy(update={"sent_at": row["sent_at"]})

    async def resend_invite(self, account_id: int, project_id: int, invite_id: int) -> InviteMade:
        """Send an invitation that has an address again, with a new link: the old link stops
        working at once, and the seven days start again. Refused, with nothing changed, when this
        owner or project has sent too many lately."""
        project = await self.owned_project(account_id, project_id, to="send invitations again")
        if self._mailer is None:
            raise AccessRefusal(INVITE_NO_MAIL)
        wait = self._invite_mail_wait(account_id, project_id)
        if wait:
            raise AccessRefusal(
                "Too many invitation emails were sent lately, so nothing was sent and the "
                f"invitation is as it was. Wait about {waiting(wait)} and try again.",
                429,
            )
        token = INVITE_PREFIX + secrets.token_urlsafe(32)
        updated = None
        async with self._db.admin_transaction() as conn:
            found = await fetch_one(
                conn,
                f"SELECT {_INVITE_COLUMNS}, i.accepted_at, i.revoked_at FROM project_invites i "
                "JOIN accounts c ON c.id = i.created_by WHERE i.id = %s AND i.project_id = %s "
                "FOR UPDATE OF i",
                (invite_id, project_id),
            )
            if (
                found is not None
                and found["accepted_at"] is None
                and found["revoked_at"] is None
                and found["sent_to"]
            ):
                await conn.execute(
                    "UPDATE project_invites SET token_hash = %s, token_hint = %s, sent_at = NULL, "
                    "expires_at = now() + make_interval(days => %s) WHERE id = %s",
                    (digest_of(token), token[:8], INVITE_DAYS, invite_id),
                )
                updated = await fetch_one(
                    conn,
                    f"SELECT {_INVITE_COLUMNS} FROM project_invites i "
                    "JOIN accounts c ON c.id = i.created_by WHERE i.id = %s",
                    (invite_id,),
                )
        if found is None or found["revoked_at"] is not None:
            raise AccessRefusal("There is no such invitation for this project.", 404)
        if found["accepted_at"] is not None:
            raise AccessRefusal(
                "That invitation was already used, so there is nothing to send. Nothing was "
                "changed.",
                409,
            )
        if updated is None:
            raise AccessRefusal(
                "That invitation has no email address, so there is nothing to send. Copy its "
                "link, or withdraw it and make a new one with an address."
            )
        invite = _invite(updated)
        problem = await self._mail_invite(project, invite, token, updated["sent_to"], account_id)
        if problem is None:
            invite = await self._note_sent(invite)
        return InviteMade(invite, token, problem is None, problem)

    async def invites(self, account_id: int, project_id: int) -> list[InviteInfo]:
        """The invitations that can still be accepted."""
        await self.owned_project(account_id, project_id, to="see the invitations", changes=False)
        async with self._db.admin_transaction() as conn:
            rows = await fetch_all(
                conn,
                f"SELECT {_INVITE_COLUMNS} FROM project_invites i "
                "JOIN accounts c ON c.id = i.created_by "
                f"WHERE i.project_id = %s AND {_PENDING_INVITE} ORDER BY i.id",
                (project_id,),
            )
        return [_invite(row) for row in rows]

    async def revoke_invite(self, account_id: int, project_id: int, invite_id: int) -> InviteInfo:
        """Withdraw an invitation that was not accepted: its link stops working at once."""
        await self.owned_project(account_id, project_id, to="withdraw invitations")
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                f"SELECT {_INVITE_COLUMNS}, i.accepted_at FROM project_invites i "
                "JOIN accounts c ON c.id = i.created_by WHERE i.id = %s AND i.project_id = %s "
                "FOR UPDATE OF i",
                (invite_id, project_id),
            )
            if row is not None and row["accepted_at"] is None:
                await conn.execute(
                    "UPDATE project_invites SET revoked_at = coalesce(revoked_at, now()) "
                    "WHERE id = %s",
                    (invite_id,),
                )
        if row is None:
            raise AccessRefusal("There is no such invitation for this project.", 404)
        if row["accepted_at"] is not None:
            raise AccessRefusal(
                "That invitation was already used, so there is nothing to withdraw. Remove the "
                "person from the project's people instead.",
                409,
            )
        return _invite(row)

    def _invite_failed(self, account_id: int, client: str) -> AccessRefusal:
        self._invites_by_account.failed(str(account_id))
        self._invites_by_client.failed(client)
        return AccessRefusal(INVITE_NOT_VALID, 404)

    def _refuse_if_guessing_invites(self, account_id: int, client: str) -> None:
        self._refuse_if_guessing(
            (self._invites_by_account, str(account_id)), (self._invites_by_client, client)
        )

    async def _pending_invite(
        self, conn: Any, token: str, *, lock: bool = False
    ) -> dict[str, Any] | None:
        """The invitation this token belongs to, if it can still be accepted."""
        if not token.startswith(INVITE_PREFIX) or len(token) > 200:
            return None
        return await fetch_one(
            conn,
            "SELECT i.id, i.project_id, i.role, i.expires_at, i.sent_to, i.sent_at, "
            "p.name AS project_name, "
            "p.owner_id, o.display_name AS owner_name, c.display_name AS invited_by, "
            "i.created_by FROM project_invites i JOIN projects p ON p.id = i.project_id "
            "JOIN accounts o ON o.id = p.owner_id JOIN accounts c ON c.id = i.created_by "
            f"WHERE i.token_hash = %s AND {_PENDING_INVITE}" + (" FOR UPDATE OF i" if lock else ""),
            (digest_of(token),),
        )

    async def _why_not(self, conn: Any, invite: dict[str, Any], account: AccountInfo) -> str | None:
        """Why this person cannot join with this invitation, in words, or None when they can."""
        if invite["owner_id"] == account.id:
            return "You own this project, so there is nothing to join. Nothing was changed."
        if invite["sent_to"] is not None and invite["sent_to"] != account.email:
            return (
                f"This invitation was sent to {mask_email(invite['sent_to'])}, so only an "
                "account with that address can accept it. Sign in with that address, or "
                "register with it. Nothing was changed."
            )
        already = await fetch_one(
            conn,
            "SELECT role FROM project_members WHERE project_id = %s AND account_id = %s",
            (invite["project_id"], account.id),
        )
        if already is not None:
            how = "an editor" if already["role"] == "editor" else "a viewer"
            return f"You are already in this project, as {how}. Nothing was changed."
        taken = await fetch_one(
            conn,
            "SELECT me.display_name FROM accounts me WHERE me.id = %(me)s AND EXISTS ("
            "SELECT 1 FROM accounts o WHERE o.id <> me.id "
            "AND lower(o.display_name) = lower(me.display_name) AND (o.id = %(owner)s OR EXISTS ("
            "SELECT 1 FROM project_members m WHERE m.project_id = %(project)s "
            "AND m.account_id = o.id)))",
            {"me": account.id, "owner": invite["owner_id"], "project": invite["project_id"]},
        )
        if taken is not None:
            return (
                f"Someone in this project already uses the name {taken['display_name']}. "
                "Change your name on the Account page first."
            )
        return None

    async def preview_invite(self, account: AccountInfo, token: str, client: str) -> InvitePreview:
        """What a signed-in person is shown of an invitation before they accept it. A token that is
        wrong, used, expired or withdrawn is refused in one set of words, and too many of those
        in a short time stop the guessing."""
        self._refuse_if_guessing_invites(account.id, client)
        async with self._db.admin_transaction() as conn:
            invite = await self._pending_invite(conn, token)
            problem = None if invite is None else await self._why_not(conn, invite, account)
        if invite is None:
            raise self._invite_failed(account.id, client)
        self._invites_by_account.succeeded(str(account.id))
        return InvitePreview(
            project_name=invite["project_name"],
            owner_name=invite["owner_name"],
            role=invite["role"],
            invited_by=invite["invited_by"],
            expires_at=invite["expires_at"],
            problem=problem,
        )

    async def accept_invite(self, account: AccountInfo, token: str, client: str) -> ProjectInfo:
        """The signed-in person joins the project with the role of the invitation, which is spent
        in the same step: it works once, for one person. Someone who owns the project or is
        already in it, or whose name another person of the project already uses, or whose address
        is not the one the invitation was sent to, is refused and the invitation stays good. An
        invitation that a mail server took, accepted by the account with that address, also shows
        the address to be theirs: the link reached its mailbox."""
        self._refuse_if_guessing_invites(account.id, client)
        async with self._db.admin_transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_PEOPLE_LOCK,))
            invite = await self._pending_invite(conn, token, lock=True)
            problem = None if invite is None else await self._why_not(conn, invite, account)
            if invite is not None and problem is None:
                await conn.execute(
                    "INSERT INTO project_members (project_id, account_id, role, added_by) "
                    "VALUES (%s, %s, %s, %s)",
                    (invite["project_id"], account.id, invite["role"], invite["created_by"]),
                )
                await conn.execute(
                    "UPDATE project_invites SET accepted_by = %s, accepted_at = now() "
                    "WHERE id = %s",
                    (account.id, invite["id"]),
                )
                if invite["sent_to"] is not None and invite["sent_at"] is not None:
                    await conn.execute(
                        "UPDATE accounts SET email_verified_at = coalesce(email_verified_at, "
                        "now()) WHERE id = %s",
                        (account.id,),
                    )
        if invite is None:
            raise self._invite_failed(account.id, client)
        if problem is not None:
            raise AccessRefusal(problem, 409)
        self._invites_by_account.succeeded(str(account.id))
        return await self.project_of(account.id, invite["project_id"])


# A hash to check a password against when the address has no account (see Accounts.sign_in).
_DECOY = hash_password("no account has this password")
