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

import base64
import hashlib
import hmac
import logging
import re
import secrets
from collections.abc import Collection
from datetime import UTC, date, datetime
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel

from vrum.common import Refusal, parse_day
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
