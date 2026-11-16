"""Accounts, sessions, projects and agent keys: who may use what.

A person registers with an email address and a password (kept only as a salted, slow hash), signs
in to get a session (a random value in a cookie, of which only a hash is kept), owns any number of
private projects, and gives agents keys to reach one project each (a random value, shown once, of
which only a hash is kept). This module is the only one that reads or writes those tables. They
are not graph content: the database role that a project's work runs as cannot reach them at all.

Every refusal says what was wrong and what to do, and none of them repeats what was sent."""

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime
from typing import Any

import psycopg
from pydantic import BaseModel

from vrum.common import Refusal, fetch_all, fetch_one
from vrum.db import Database
from vrum.ratelimit import AttemptLimiter

SESSION_HOURS = 12
SESSION_PREFIX = "vrs_"
KEY_PREFIX = "vrk_"
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
FAILURE_WINDOW_SECONDS = 600

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
    "revoked. Ask the project's owner for a new one (Agent access on the project's board), and "
    "send it as 'Authorization: Bearer <key>'."
)
NO_AGENT_KEY = (
    "This server needs an agent key for a project, and none was sent. The project's owner can "
    "make one (Agent access on the project's board). Send it as 'Authorization: Bearer <key>'."
)


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


class ProjectInfo(BaseModel):
    id: int
    name: str
    created_at: datetime


class KeyInfo(BaseModel):
    id: int
    label: str
    hint: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class SessionInfo(BaseModel):
    id: int
    account: AccountInfo
    csrf: str
    expires_at: datetime


class SignedIn(BaseModel):
    session: SessionInfo
    # The value to put in the cookie. It is not stored anywhere: only its hash is.
    cookie: str


def _account(row: dict[str, Any]) -> AccountInfo:
    return AccountInfo(id=row["id"], email=row["email"], name=row["display_name"])


def _key(row: dict[str, Any]) -> KeyInfo:
    return KeyInfo(
        id=row["id"],
        label=row["label"],
        hint=row["key_hint"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
    )


_KEY_COLUMNS = "id, label, key_hint, created_at, last_used_at, revoked_at"


def clean_email(email: str) -> str:
    cleaned = email.strip().lower()
    if len(cleaned) > MAX_EMAIL_CHARS or not _EMAIL.fullmatch(cleaned):
        raise AccessRefusal(
            "That does not look like an email address. Write it like name@example.com."
        )
    return cleaned


class Accounts:
    def __init__(
        self,
        database: Database,
        *,
        failures_per_email: int = FAILURES_PER_EMAIL,
        failures_per_client: int = FAILURES_PER_CLIENT,
    ) -> None:
        self._db = database
        self._by_email = AttemptLimiter(failures_per_email, FAILURE_WINDOW_SECONDS)
        self._by_client = AttemptLimiter(failures_per_client, FAILURE_WINDOW_SECONDS)
        # Hashing a password takes a fair amount of memory, so only a few run at a time.
        self._hashing = asyncio.Semaphore(4)

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
        shown = name.strip()
        if not shown or len(shown) > MAX_NAME_CHARS:
            raise AccessRefusal(
                f"Say what to call you, in 1 to {MAX_NAME_CHARS} characters. Every change you "
                "make is recorded with this name."
            )
        if not MIN_PASSWORD_CHARS <= len(password) <= MAX_PASSWORD_CHARS:
            raise AccessRefusal(
                f"The password must be {MIN_PASSWORD_CHARS} to {MAX_PASSWORD_CHARS} characters "
                "long. A few unrelated words make a good one."
            )
        hashed = await self._hash(password)
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "INSERT INTO accounts (email, display_name, password_hash) VALUES (%s, %s, %s) "
                "ON CONFLICT (email) DO NOTHING RETURNING id, email, display_name",
                (address, shown, hashed),
            )
            if row is None:
                self._by_client.failed(client)
                raise AccessRefusal(
                    "An account with that email address already exists, so none was made. Sign "
                    "in with it, or register with another address.",
                    409,
                )
            return await self._start_session(conn, _account(row))

    async def sign_in(self, email: str, password: str, client: str) -> SignedIn:
        address = email.strip().lower()
        email_key = (self._by_email, address)
        self._refuse_if_guessing(email_key, (self._by_client, client))
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "SELECT id, email, display_name, password_hash FROM accounts WHERE email = %s",
                (address,),
            )
        # An address with no account takes as long as one with a wrong password, so the time says
        # nothing about which addresses have accounts.
        stored = row["password_hash"] if row else _DECOY
        matches = (
            await self._matches(password, stored) if len(password) <= MAX_PASSWORD_CHARS else False
        )
        if row is None or not matches:
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
                "a.display_name FROM sessions s JOIN accounts a ON a.id = s.account_id "
                "WHERE s.token_hash = %s AND s.ended_at IS NULL AND s.expires_at > now()",
                (digest_of(cookie),),
            )
        if row is None:
            return None
        return SessionInfo(
            id=row["id"],
            account=AccountInfo(id=row["account_id"], email=row["email"], name=row["display_name"]),
            csrf=row["csrf_token"],
            expires_at=row["expires_at"],
        )

    async def sign_out(self, session_id: int) -> None:
        async with self._db.admin_transaction() as conn:
            await conn.execute(
                "UPDATE sessions SET ended_at = now() WHERE id = %s AND ended_at IS NULL",
                (session_id,),
            )

    # -- projects --------------------------------------------------------------------------------

    async def projects(self, account_id: int) -> list[ProjectInfo]:
        async with self._db.admin_transaction() as conn:
            rows = await fetch_all(
                conn,
                "SELECT id, name, created_at FROM projects WHERE owner_id = %s ORDER BY id",
                (account_id,),
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
                    "RETURNING id, name, created_at",
                    (account_id, cleaned),
                )
        except psycopg.errors.UniqueViolation:
            raise AccessRefusal(
                f"You already have a project called {cleaned!r}. Choose another name.", 409
            ) from None
        assert row is not None
        return ProjectInfo(**row)

    async def project_of(self, account_id: int, project_id: int) -> ProjectInfo:
        """One of this account's projects. A project that does not exist and one that belongs to
        someone else are refused in the same words, so that the refusal says nothing about the
        other person's projects."""
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "SELECT id, name, created_at FROM projects WHERE id = %s AND owner_id = %s",
                (project_id, account_id),
            )
        if row is None:
            raise AccessRefusal(NO_SUCH_PROJECT, 404)
        return ProjectInfo(**row)

    # -- agent keys ------------------------------------------------------------------------------

    async def create_key(self, account_id: int, project_id: int, label: str) -> tuple[KeyInfo, str]:
        """A new key for an agent to reach this project. The key itself is returned this once and
        kept nowhere: only a hash of it is."""
        await self.project_of(account_id, project_id)
        cleaned = label.strip()
        if not cleaned or len(cleaned) > MAX_NAME_CHARS:
            raise AccessRefusal(
                f"Give the key a label, in 1 to {MAX_NAME_CHARS} characters, such as the name of "
                "the agent that will use it."
            )
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "INSERT INTO agent_keys (project_id, label, key_hash, key_hint) "
                f"VALUES (%s, %s, %s, %s) RETURNING {_KEY_COLUMNS}",
                (project_id, cleaned, digest_of(key), key[:8]),
            )
        assert row is not None
        return _key(row), key

    async def keys(self, account_id: int, project_id: int) -> list[KeyInfo]:
        await self.project_of(account_id, project_id)
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
        await self.project_of(account_id, project_id)
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "UPDATE agent_keys SET revoked_at = coalesce(revoked_at, now()) "
                f"WHERE id = %s AND project_id = %s RETURNING {_KEY_COLUMNS}",
                (key_id, project_id),
            )
        if row is None:
            raise AccessRefusal("There is no such key for this project.", 404)
        return _key(row)

    async def project_for_key(self, key: str) -> int | None:
        """The project a key lets its holder reach, or None when it is not a key, or was revoked."""
        digest = digest_of(key)
        async with self._db.admin_transaction() as conn:
            row = await fetch_one(
                conn,
                "SELECT project_id FROM agent_keys WHERE key_hash = %s AND revoked_at IS NULL",
                (digest,),
            )
            if row is not None:
                # Noted at most once a minute, so that busy agents do not write on every call.
                await conn.execute(
                    "UPDATE agent_keys SET last_used_at = now() WHERE key_hash = %s "
                    "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')",
                    (digest,),
                )
        return None if row is None else int(row["project_id"])


# A hash to check a password against when the address has no account (see Accounts.sign_in).
_DECOY = hash_password("no account has this password")
