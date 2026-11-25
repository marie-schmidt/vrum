"""Shared Accounts base: pool, limiters, hashing, sessions, background tasks, projects."""

import asyncio
import logging
import secrets
from collections.abc import Coroutine
from typing import Any

import psycopg

from vrum.account_models import (
    _PROJECT_COLUMNS,
    _PROJECT_OF_A_PERSON,
    DAY_SECONDS,
    FAILURE_WINDOW_SECONDS,
    FAILURES_PER_CLIENT,
    FAILURES_PER_EMAIL,
    FAILURES_PER_INVITEE,
    HOUR_SECONDS,
    INVITE_MAILS_PER_OWNER,
    INVITE_MAILS_PER_PROJECT,
    MAX_NAME_CHARS,
    NO_SUCH_PROJECT,
    RESETS_PER_CLIENT,
    RESETS_PER_EMAIL,
    SESSION_HOURS,
    SESSION_PREFIX,
    VERIFY_MAILS_PER_ACCOUNT,
    AccessRefusal,
    AccountInfo,
    ProjectInfo,
    SessionInfo,
    SignedIn,
    digest_of,
    hash_password,
    only_the_owner,
    password_matches,
    waiting,
)
from vrum.common import fetch_all, fetch_one
from vrum.db import Database
from vrum.logs import event
from vrum.mail import Mailer, MailRefusal
from vrum.ratelimit import AttemptLimiter

log = logging.getLogger("vrum.accounts")


class _AccountsBase:
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
