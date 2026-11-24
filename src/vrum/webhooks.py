"""Outgoing webhooks and email nudges (M48): a small signed message, or an email, that wakes
someone when something happens that needs them, with the change feed still the source of truth.

A project's owner adds a webhook (an address and the kinds of news it wants, see notify_events.py);
the server makes its signing secret, shows it once and keeps it only to sign with. A member who
wants email says which kinds of news to be mailed about. A background job (`Dispatcher`) reads each
project's log from where it stopped last time, and for each event that is news writes a message
for each webhook that asked for it (a row that survives a restart), sends it with growing delays
between attempts (10 seconds, 1 minute, 5 minutes, 30 minutes, 2 hours) and then gives up and
shows the failure on the Users page; and mails each person who asked. Nothing is sent from inside a
request: a tool call only writes its event, so a dead or slow address cannot slow one down.

What is sent never holds what anything is called or what anyone wrote: the kind of news, the
project's number, the event's number and the story or task's reference (see notify_events.py).
Addresses and secrets are never logged: a line says only which webhook and how it ended."""

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from vrum import webhook_signing
from vrum.accounts import AccessRefusal, Accounts
from vrum.common import fetch_all, fetch_one
from vrum.config import WebhookSettings
from vrum.db import Database
from vrum.logs import event
from vrum.mail import Mailer, MailRefusal, nudge_message
from vrum.notify_events import (
    COALESCED,
    EVENT_TYPES,
    MOST_WEBHOOKS,
    NUDGE_TYPES,
    object_of,
    types_of,
)
from vrum.webhook_address import (
    AddressRefused,
    DeliveryFailed,
    Resolver,
    lookup,
    parse,
    post,
    public_addresses,
)

log = logging.getLogger("vrum.webhooks")

# Seconds to wait before attempts 2, 3, 4, 5 and 6; after the sixth a message is given up on.
RETRY_DELAYS = (10, 60, 300, 1800, 7200)
MOST_ATTEMPTS = len(RETRY_DELAYS) + 1
TICK_SECONDS = 2.0
MOST_EVENTS_A_TICK = 200
MOST_AT_ONCE = 8
# A message that is being sent is not offered again for this long.
_LEASE_SECONDS = 60
SECRET_OVERLAP_HOURS = 24
NUDGES_PER_HOUR = 10
# The first half of the advisory lock that serialises changes to one project's webhooks.
_LOCK_SPACE = 48
MOST_MAILS_A_TICK = 20
_KEEP_DELIVERED_DAYS = 7
_KEEP_FAILED_DAYS = 30


class WebhookInfo(BaseModel):
    id: int
    url: str
    events: list[str]
    enabled: bool
    created_at: datetime
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None = Field(description="What went wrong the last time, in words.")
    failing: bool = Field(
        description="True when the last attempt failed and none has worked since."
    )
    pending: int = Field(description="How many messages wait to be sent or sent again.")
    replacing_secret_until: datetime | None = Field(
        description="While a secret is being replaced, until when the old one still signs too."
    )


class WebhookMade(BaseModel):
    webhook: WebhookInfo
    secret: str = Field(description="The signing secret. This is the only time it is shown.")


class DeliveryInfo(BaseModel):
    id: int
    type: str
    message_id: str
    state: str = Field(description="pending, delivered or failed (given up on).")
    attempts: int
    next_attempt_at: datetime | None
    created_at: datetime
    delivered_at: datetime | None
    last_status: int | None
    last_error: str | None


class NudgeInfo(BaseModel):
    project: int
    events: list[str]


class PingResult(BaseModel):
    delivered: bool
    status: int | None
    words: str


_INFO = (
    "w.id, w.url, w.events, w.enabled, w.created_at, w.last_success_at, w.last_failure_at, "
    "w.last_error, "
    "(w.last_failure_at IS NOT NULL AND (w.last_success_at IS NULL "
    "OR w.last_failure_at > w.last_success_at)) AS failing, "
    "(SELECT count(*) FROM webhook_deliveries d WHERE d.webhook_id = w.id "
    "AND d.state = 'pending') AS pending, "
    "CASE WHEN w.previous_secret IS NOT NULL AND w.previous_until > now() "
    "THEN w.previous_until END AS replacing_secret_until"
)


def _info(row: dict[str, Any]) -> WebhookInfo:
    return WebhookInfo.model_validate({**row, "pending": int(row["pending"])})


def clean_events(value: Any, allowed: tuple[str, ...] | list[str] | None = None) -> list[str]:
    """The kinds of news asked for, in the order of the list of kinds, or a refusal in words."""
    known = list(allowed) if allowed is not None else list(EVENT_TYPES)
    if not isinstance(value, list) or not value or not all(isinstance(one, str) for one in value):
        raise AccessRefusal(
            "events is a list of the kinds of news you want, such as ask.raised. Choose from: "
            + ", ".join(known)
            + ". Nothing was changed."
        )
    wrong = sorted({one for one in value if one not in known})
    if wrong:
        raise AccessRefusal(
            f"{', '.join(repr(one[:40]) for one in wrong)} is not a kind of news. Choose from: "
            + ", ".join(known)
            + ". Nothing was changed."
        )
    return [one for one in known if one in value]


class WebhookBook:
    """Webhooks and nudges as people set them: only the owner has a project's webhooks, and a
    person has their own nudges."""

    def __init__(
        self,
        database: Database,
        accounts: Accounts,
        settings: WebhookSettings,
        mailer: Mailer | None,
        *,
        resolver: Resolver = lookup,
    ) -> None:
        self._database = database
        self._accounts = accounts
        self._settings = settings
        self._mailer = mailer
        self._resolver = resolver

    # -- webhooks ----------------------------------------------------------------------------

    async def _owned(self, account_id: int, project_id: int, to: str) -> None:
        await self._accounts.owned_project(account_id, project_id, to=to)

    async def list_webhooks(self, account_id: int, project_id: int) -> list[WebhookInfo]:
        await self._owned(account_id, project_id, "see the webhooks of a project")
        async with self._database.admin_transaction() as conn:
            rows = await fetch_all(
                conn,
                f"SELECT {_INFO} FROM webhooks w WHERE w.project_id = %s ORDER BY w.id",
                (project_id,),
            )
        return [_info(row) for row in rows]

    async def create_webhook(
        self, account_id: int, project_id: int, url: Any, events: Any
    ) -> WebhookMade:
        await self._owned(account_id, project_id, "add a webhook to a project")
        chosen = clean_events(events)
        if not isinstance(url, str):
            raise AccessRefusal(
                "url is the address to send to, such as https://hooks.example.com/vrum."
            )
        try:
            target = parse(url, self._settings)
            await asyncio.to_thread(public_addresses, target, self._settings, self._resolver)
        except AddressRefused as refusal:
            raise AccessRefusal(f"{refusal} Nothing was saved.") from None
        secret = webhook_signing.new_secret()
        async with self._database.admin_transaction() as conn:
            # Two owners adding at once must not both get past the limit.
            await conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_LOCK_SPACE, project_id))
            existing = await fetch_all(
                conn, "SELECT url FROM webhooks WHERE project_id = %s", (project_id,)
            )
            if len(existing) >= MOST_WEBHOOKS:
                raise AccessRefusal(
                    f"A project may have at most {MOST_WEBHOOKS} webhooks. Delete one first."
                )
            if any(row["url"] == url.strip() for row in existing):
                raise AccessRefusal(
                    "This project already has a webhook for that address. Change that one, or "
                    "delete it first."
                )
            head = await _head(conn, project_id)
            row = await fetch_one(
                conn,
                "INSERT INTO webhooks (project_id, url, events, secret, created_by, after_xid, "
                "after_id) VALUES (%s, %s, %s, %s, %s, %s::text::xid8, %s) RETURNING id",
                (project_id, url.strip(), chosen, secret, account_id, head[0], head[1]),
            )
            assert row is not None
            await _ensure_cursor(conn, project_id)
            found = await fetch_one(
                conn, f"SELECT {_INFO} FROM webhooks w WHERE w.id = %s", (row["id"],)
            )
        assert found is not None
        return WebhookMade(webhook=_info(found), secret=secret)

    async def change_webhook(
        self,
        account_id: int,
        project_id: int,
        webhook_id: int,
        *,
        events: Any = None,
        enabled: Any = None,
    ) -> WebhookInfo:
        await self._owned(account_id, project_id, "change a webhook")
        if events is None and enabled is None:
            raise AccessRefusal(
                "Say what to change: events (the kinds of news) or enabled (true or false). "
                "Nothing was changed."
            )
        chosen = clean_events(events) if events is not None else None
        if enabled is not None and not isinstance(enabled, bool):
            raise AccessRefusal("enabled is true or false. Nothing was changed.")
        async with self._database.admin_transaction() as conn:
            await self._require(conn, project_id, webhook_id)
            if chosen is not None:
                await conn.execute(
                    "UPDATE webhooks SET events = %s WHERE id = %s", (chosen, webhook_id)
                )
            if enabled is not None:
                # Turning it on again does not send what happened while it was off.
                head = await _head(conn, project_id)
                await conn.execute(
                    "UPDATE webhooks SET enabled = %s, after_xid = CASE WHEN %s THEN "
                    "%s::text::xid8 ELSE after_xid END, after_id = CASE WHEN %s THEN %s "
                    "ELSE after_id END WHERE id = %s",
                    (enabled, enabled, head[0], enabled, head[1], webhook_id),
                )
            found = await fetch_one(
                conn, f"SELECT {_INFO} FROM webhooks w WHERE w.id = %s", (webhook_id,)
            )
        assert found is not None
        return _info(found)

    async def rotate_secret(self, account_id: int, project_id: int, webhook_id: int) -> WebhookMade:
        """Make a new secret. The old one keeps signing for a day too, so that a receiver can
        change over without missing a message."""
        await self._owned(account_id, project_id, "replace a webhook's secret")
        secret = webhook_signing.new_secret()
        async with self._database.admin_transaction() as conn:
            await self._require(conn, project_id, webhook_id)
            await conn.execute(
                "UPDATE webhooks SET previous_secret = secret, "
                "previous_until = now() + make_interval(hours => %s), secret = %s WHERE id = %s",
                (SECRET_OVERLAP_HOURS, secret, webhook_id),
            )
            found = await fetch_one(
                conn, f"SELECT {_INFO} FROM webhooks w WHERE w.id = %s", (webhook_id,)
            )
        assert found is not None
        return WebhookMade(webhook=_info(found), secret=secret)

    async def delete_webhook(self, account_id: int, project_id: int, webhook_id: int) -> None:
        await self._owned(account_id, project_id, "delete a webhook")
        async with self._database.admin_transaction() as conn:
            await self._require(conn, project_id, webhook_id)
            await conn.execute("DELETE FROM webhooks WHERE id = %s", (webhook_id,))
            await _drop_idle_cursor(conn, project_id)

    async def deliveries(
        self, account_id: int, project_id: int, webhook_id: int, limit: int = 50
    ) -> list[DeliveryInfo]:
        await self._owned(account_id, project_id, "see what a webhook sent")
        async with self._database.admin_transaction() as conn:
            await self._require(conn, project_id, webhook_id)
            rows = await fetch_all(
                conn,
                "SELECT id, news AS type, message_id, state, attempts, CASE WHEN state = 'pending' "
                "THEN next_attempt_at END AS next_attempt_at, created_at, delivered_at, "
                "last_status, last_error FROM webhook_deliveries WHERE webhook_id = %s "
                "ORDER BY id DESC LIMIT %s",
                (webhook_id, max(1, min(limit, 200))),
            )
        return [DeliveryInfo.model_validate(row) for row in rows]

    async def send_test(self, account_id: int, project_id: int, webhook_id: int) -> PingResult:
        """Send one message of the kind `ping` now, once, and say how it went. It is not retried."""
        await self._owned(account_id, project_id, "test a webhook")
        async with self._database.admin_transaction() as conn:
            found = await self._require(conn, project_id, webhook_id)
        message_id = f"msg_ping_{int(time.time() * 1000)}"
        body = json.dumps(
            {
                "type": "ping",
                "timestamp": _iso(time.time()),
                "data": {"project": project_id, "event_id": None, "node": None, "object": None},
            },
            separators=(",", ":"),
        )
        status: int | None
        try:
            status = await asyncio.to_thread(
                _send, found, message_id, body, self._settings, self._resolver
            )
            words, delivered = f"The receiver answered {status}.", True
        except DeliveryFailed as failure:
            status, words, delivered = failure.status, failure.words, False
        async with self._database.admin_transaction() as conn:
            await _record_outcome(conn, webhook_id, delivered, None if delivered else words)
        return PingResult(delivered=delivered, status=status, words=words)

    async def _require(self, conn: Any, project_id: int, webhook_id: int) -> dict[str, Any]:
        found = await fetch_one(
            conn,
            "SELECT id, url, secret, previous_secret, previous_until FROM webhooks "
            "WHERE id = %s AND project_id = %s FOR UPDATE",
            (webhook_id, project_id),
        )
        if found is None:
            raise AccessRefusal("There is no such webhook in this project.", 404)
        return found

    # -- nudges ------------------------------------------------------------------------------

    async def my_nudges(self, account_id: int) -> list[NudgeInfo]:
        async with self._database.admin_transaction() as conn:
            rows = await fetch_all(
                conn,
                "SELECT project_id AS project, events FROM nudges WHERE account_id = %s "
                "ORDER BY project_id",
                (account_id,),
            )
        return [NudgeInfo.model_validate(row) for row in rows]

    async def set_nudges(self, account_id: int, project_id: int, events: Any) -> NudgeInfo:
        """Ask to be mailed about some kinds of news in a project (an empty list stops them). It
        needs a mail server and an email address the person has confirmed, so that a message
        cannot be aimed at someone else's address."""
        await self._accounts.project_of(account_id, project_id)
        if not isinstance(events, list):
            raise AccessRefusal(
                "events is a list of the kinds of news to be mailed about. Choose from: "
                + ", ".join(NUDGE_TYPES)
                + ". An empty list stops them. Nothing was changed."
            )
        chosen = clean_events(events, NUDGE_TYPES) if events else []
        if chosen:
            if self._mailer is None:
                raise AccessRefusal(
                    "Email is not set up on this server, so there is nothing to send nudges "
                    "with. Whoever runs it can set it up. Nothing was changed."
                )
            async with self._database.admin_transaction() as conn:
                account = await fetch_one(
                    conn,
                    "SELECT email_verified_at FROM accounts WHERE id = %s",
                    (account_id,),
                )
            if account is None or account["email_verified_at"] is None:
                raise AccessRefusal(
                    "Confirm your email address first (on your Account page): a nudge is only "
                    "sent to an address you have confirmed. Nothing was changed."
                )
        async with self._database.admin_transaction() as conn:
            if chosen:
                await conn.execute(
                    "INSERT INTO nudges (project_id, account_id, events) VALUES (%s, %s, %s) "
                    "ON CONFLICT (project_id, account_id) DO UPDATE SET events = EXCLUDED.events",
                    (project_id, account_id, chosen),
                )
                await _ensure_cursor(conn, project_id)
            else:
                await conn.execute(
                    "DELETE FROM nudges WHERE project_id = %s AND account_id = %s",
                    (project_id, account_id),
                )
                await _drop_idle_cursor(conn, project_id)
        return NudgeInfo(project=project_id, events=chosen)


def _iso(moment: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment))


def _send(
    row: dict[str, Any],
    message_id: str,
    body: str,
    settings: WebhookSettings,
    resolver: Resolver,
) -> int:
    """One attempt, signed with the secret in force and, while a secret is being replaced, the old
    one as well. Runs in a thread."""
    keys = [row["secret"]]
    until = row.get("previous_until")
    if row.get("previous_secret") and until is not None and until.timestamp() > time.time():
        keys.append(row["previous_secret"])
    try:
        target = parse(row["url"], settings)
    except AddressRefused as refusal:
        raise DeliveryFailed(f"Not sent: {refusal}") from None
    headers = webhook_signing.headers(keys, message_id, int(time.time()), body)
    return post(target, settings, headers, body, resolver=resolver)


async def _head(conn: Any, project_id: int) -> tuple[str, int]:
    """Where a project's log stands now, as far as it can be read without missing a late commit."""
    row = await fetch_one(
        conn,
        "SELECT xid::text AS xid, id FROM events WHERE project_id = %s "
        "AND xid < pg_snapshot_xmin(pg_current_snapshot()) ORDER BY xid DESC, id DESC LIMIT 1",
        (project_id,),
    )
    return ("0", 0) if row is None else (str(row["xid"]), int(row["id"]))


async def _ensure_cursor(conn: Any, project_id: int) -> None:
    head = await _head(conn, project_id)
    await conn.execute(
        "INSERT INTO notify_cursor (project_id, xid, id) VALUES (%s, %s::text::xid8, %s) "
        "ON CONFLICT DO NOTHING",
        (project_id, head[0], head[1]),
    )


async def _drop_idle_cursor(conn: Any, project_id: int) -> None:
    await conn.execute(
        "DELETE FROM notify_cursor WHERE project_id = %s AND NOT EXISTS "
        "(SELECT 1 FROM webhooks WHERE project_id = %s AND enabled) AND NOT EXISTS "
        "(SELECT 1 FROM nudges WHERE project_id = %s)",
        (project_id, project_id, project_id),
    )


async def _record_outcome(conn: Any, webhook_id: int, delivered: bool, words: str | None) -> None:
    if delivered:
        await conn.execute(
            "UPDATE webhooks SET last_success_at = now(), last_error = NULL WHERE id = %s",
            (webhook_id,),
        )
    else:
        await conn.execute(
            "UPDATE webhooks SET last_failure_at = now(), last_error = %s WHERE id = %s",
            (words, webhook_id),
        )


def _position(xid: str, event_id: int) -> tuple[int, int]:
    return (int(xid), event_id)


class Dispatcher:
    """The background job: look at each project's log from where it stopped, queue a message for
    every webhook that asked for each piece of news, send what is due, and mail the people who
    asked. One server runs it; it is safe to run twice, because a message is queued once and
    leased while it is being sent."""

    def __init__(
        self,
        database: Database,
        settings: WebhookSettings,
        mailer: Mailer | None,
        public_url: str,
        *,
        resolver: Resolver = lookup,
    ) -> None:
        self._database = database
        self._settings = settings
        self._mailer = mailer
        self._public_url = public_url
        self._resolver = resolver
        self._last_prune = 0.0

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("The webhook job hit an unexpected error and will try again.")
            await asyncio.sleep(TICK_SECONDS)

    async def tick(self) -> None:
        mails = await self.scan()
        await self.deliver_due()
        await self.send_mails(mails)
        if time.monotonic() - self._last_prune > 3600:
            self._last_prune = time.monotonic()
            await self.prune()

    # -- reading the log -----------------------------------------------------------------------

    async def scan(self) -> list[tuple[int, str, str, str | None, int]]:
        """Queue messages for the news since last time. Gives back the nudge mails to send:
        (account, address, kind, node, project)."""
        mails: list[tuple[int, str, str, str | None, int]] = []
        async with self._database.admin_transaction() as conn:
            projects = await fetch_all(
                conn,
                "SELECT project_id FROM webhooks WHERE enabled UNION "
                "SELECT project_id FROM nudges ORDER BY 1",
            )
        # One short transaction for each project, and none that writes when there is no news: an
        # open transaction that has written holds back the change feed's view of later events.
        for one in projects:
            async with self._database.admin_transaction() as conn:
                mails.extend(await self._scan_project(conn, int(one["project_id"])))
        return mails

    async def _scan_project(
        self, conn: Any, project: int
    ) -> list[tuple[int, str, str, str | None, int]]:
        cursor = await fetch_one(
            conn,
            "SELECT xid::text AS xid, id, lapse_checked_at FROM notify_cursor "
            "WHERE project_id = %s",
            (project,),
        )
        if cursor is None:
            await _ensure_cursor(conn, project)
            return []
        news = await fetch_one(
            conn,
            "SELECT EXISTS (SELECT 1 FROM events e WHERE e.project_id = %(p)s "
            "AND (e.xid, e.id) > (%(xid)s::text::xid8, %(id)s) "
            "AND e.xid < pg_snapshot_xmin(pg_current_snapshot())) AS events, "
            "EXISTS (SELECT 1 FROM claims c WHERE c.project_id = %(p)s "
            "AND c.expires_at <= now() AND c.expires_at > %(since)s AND ("
            "EXISTS (SELECT 1 FROM webhooks w WHERE w.project_id = %(p)s AND w.enabled "
            "AND 'claim.lapsed' = ANY (w.events)) OR EXISTS (SELECT 1 FROM nudges n "
            "WHERE n.project_id = %(p)s AND 'claim.lapsed' = ANY (n.events)))) AS lapse",
            {
                "p": project,
                "xid": cursor["xid"],
                "id": cursor["id"],
                "since": cursor["lapse_checked_at"],
            },
        )
        assert news is not None
        if not news["events"] and not news["lapse"]:
            return []
        # There is something to do: hold the position while it is done, so that two servers
        # running this job do not both queue it.
        cursor = await fetch_one(
            conn,
            "SELECT xid::text AS xid, id, lapse_checked_at FROM notify_cursor "
            "WHERE project_id = %s FOR UPDATE",
            (project,),
        )
        assert cursor is not None
        hooks = await fetch_all(
            conn,
            "SELECT id, events, after_xid::text AS after_xid, after_id, created_at FROM webhooks "
            "WHERE project_id = %s AND enabled",
            (project,),
        )
        people = await fetch_all(
            conn,
            "SELECT n.account_id, n.events, n.created_at, a.email FROM nudges n JOIN accounts a "
            "ON a.id = n.account_id JOIN projects p ON p.id = n.project_id "
            "WHERE n.project_id = %s AND a.email_verified_at IS NOT NULL AND "
            "(p.owner_id = n.account_id OR EXISTS (SELECT 1 FROM project_members m "
            "WHERE m.project_id = n.project_id AND m.account_id = n.account_id))",
            (project,),
        )
        mails: list[tuple[int, str, str, str | None, int]] = []
        rows = await fetch_all(
            conn,
            "SELECT e.xid::text AS xid, e.id, e.action, e.node, e.before, e.after FROM events e "
            "WHERE e.project_id = %s AND (e.xid, e.id) > (%s::text::xid8, %s) "
            "AND e.xid < pg_snapshot_xmin(pg_current_snapshot()) ORDER BY e.xid, e.id LIMIT %s",
            (project, cursor["xid"], cursor["id"], MOST_EVENTS_A_TICK),
        )
        for row in rows:
            where = _position(row["xid"], row["id"])
            for kind in types_of(row["action"], row["before"], row["after"] or {}):
                for hook in hooks:
                    if kind in hook["events"] and where > _position(
                        hook["after_xid"], hook["after_id"]
                    ):
                        await self._queue(
                            conn, hook["id"], project, kind, f"event:{row['id']}", row
                        )
                for person in people:
                    if kind in person["events"] and kind in NUDGE_TYPES:
                        mails.append(
                            (person["account_id"], person["email"], kind, row["node"], project)
                        )
        if rows:
            last = rows[-1]
            await conn.execute(
                "UPDATE notify_cursor SET xid = %s::text::xid8, id = %s WHERE project_id = %s",
                (last["xid"], last["id"], project),
            )
        if news["lapse"]:
            mails.extend(
                await self._lapsed(conn, project, hooks, people, cursor["lapse_checked_at"])
            )
        return mails

    async def _lapsed(
        self,
        conn: Any,
        project: int,
        hooks: list[dict[str, Any]],
        people: list[dict[str, Any]],
        since: datetime,
    ) -> list[tuple[int, str, str, str | None, int]]:
        wanted = [h for h in hooks if "claim.lapsed" in h["events"]]
        askers = [p for p in people if "claim.lapsed" in p["events"]]
        mails: list[tuple[int, str, str, str | None, int]] = []
        if wanted or askers:
            rows = await fetch_all(
                conn,
                "SELECT node, expires_at, floor(extract(epoch FROM expires_at))::bigint AS epoch "
                "FROM claims WHERE project_id = %s AND expires_at <= now() AND expires_at > %s",
                (project, since),
            )
            for claim in rows:
                for hook in wanted:
                    # Only what ran out after the webhook was added.
                    if claim["expires_at"] <= hook["created_at"]:
                        continue
                    await self._queue(
                        conn,
                        hook["id"],
                        project,
                        "claim.lapsed",
                        f"lapse:{claim['node']}:{claim['epoch']}",
                        {"id": None, "node": claim["node"], "action": "lapse", "after": {}},
                    )
                for person in askers:
                    if claim["expires_at"] <= person["created_at"]:
                        continue
                    mails.append(
                        (
                            person["account_id"],
                            person["email"],
                            "claim.lapsed",
                            claim["node"],
                            project,
                        )
                    )
            if rows:
                # Everything that had run out is now looked at; a claim that runs out later is
                # later than the latest of these.
                await conn.execute(
                    "UPDATE notify_cursor SET lapse_checked_at = %s WHERE project_id = %s",
                    (max(claim["expires_at"] for claim in rows), project),
                )
        return mails

    async def _queue(
        self,
        conn: Any,
        webhook: int,
        project: int,
        kind: str,
        subject: str,
        row: dict[str, Any],
    ) -> None:
        window = COALESCED.get(kind)
        if window is not None:
            recent = await fetch_one(
                conn,
                "SELECT 1 FROM webhook_deliveries WHERE webhook_id = %s AND news = %s "
                "AND created_at > now() - make_interval(secs => %s)",
                (webhook, kind, window),
            )
            if recent is not None:
                return
        message_id = "msg_" + _hash(f"{webhook}:{kind}:{subject}")
        body = json.dumps(
            {
                "type": kind,
                "timestamp": _iso(time.time()),
                "data": {
                    "project": project,
                    "event_id": row["id"],
                    "node": row["node"],
                    "object": object_of(row["action"], row["id"] or 0, row["after"] or {}),
                },
            },
            separators=(",", ":"),
        )
        await conn.execute(
            "INSERT INTO webhook_deliveries (webhook_id, project_id, news, subject, message_id, "
            "body) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (webhook, project, kind, subject, message_id, body),
        )

    # -- sending -------------------------------------------------------------------------------

    async def deliver_due(self) -> None:
        async with self._database.admin_transaction() as conn:
            due = await fetch_all(
                conn,
                "SELECT d.id, d.webhook_id, d.message_id, d.body, d.attempts, w.url, w.secret, "
                "w.previous_secret, w.previous_until FROM webhook_deliveries d "
                "JOIN webhooks w ON w.id = d.webhook_id WHERE d.state = 'pending' "
                "AND d.next_attempt_at <= now() AND w.enabled ORDER BY d.next_attempt_at, d.id "
                "LIMIT 50 FOR UPDATE OF d SKIP LOCKED",
            )
            if due:
                await conn.execute(
                    "UPDATE webhook_deliveries SET next_attempt_at = now() + "
                    "make_interval(secs => %s) WHERE id = ANY(%s)",
                    (_LEASE_SECONDS, [d["id"] for d in due]),
                )
        gate = asyncio.Semaphore(MOST_AT_ONCE)

        async def one(item: dict[str, Any]) -> None:
            async with gate:
                try:
                    status = await asyncio.to_thread(
                        _send,
                        item,
                        item["message_id"],
                        item["body"],
                        self._settings,
                        self._resolver,
                    )
                except DeliveryFailed as failure:
                    await self._failed(item, failure)
                else:
                    await self._delivered(item, status)

        await asyncio.gather(*(one(item) for item in due))

    async def _delivered(self, item: dict[str, Any], status: int) -> None:
        async with self._database.admin_transaction() as conn:
            await conn.execute(
                "UPDATE webhook_deliveries SET state = 'delivered', attempts = attempts + 1, "
                "delivered_at = now(), last_status = %s, last_error = NULL WHERE id = %s",
                (status, item["id"]),
            )
            await _record_outcome(conn, item["webhook_id"], True, None)
        event(log, logging.INFO, "webhook_delivered", webhook=item["webhook_id"], status=status)

    async def _failed(self, item: dict[str, Any], failure: DeliveryFailed) -> None:
        attempts = int(item["attempts"]) + 1
        final = attempts >= MOST_ATTEMPTS
        async with self._database.admin_transaction() as conn:
            if final:
                await conn.execute(
                    "UPDATE webhook_deliveries SET state = 'failed', attempts = %s, "
                    "last_status = %s, last_error = %s WHERE id = %s",
                    (attempts, failure.status, failure.words, item["id"]),
                )
            else:
                await conn.execute(
                    "UPDATE webhook_deliveries SET attempts = %s, last_status = %s, "
                    "last_error = %s, next_attempt_at = now() + make_interval(secs => %s) "
                    "WHERE id = %s",
                    (
                        attempts,
                        failure.status,
                        failure.words,
                        RETRY_DELAYS[attempts - 1],
                        item["id"],
                    ),
                )
            await _record_outcome(conn, item["webhook_id"], False, failure.words)
        event(
            log,
            logging.WARNING,
            "webhook_failed",
            webhook=item["webhook_id"],
            attempt=attempts,
            gave_up=final,
            status=failure.status,
        )

    # -- mail ----------------------------------------------------------------------------------

    async def send_mails(self, mails: list[tuple[int, str, str, str | None, int]]) -> None:
        if self._mailer is None or not mails:
            return
        for account, address, kind, node, project in mails[:MOST_MAILS_A_TICK]:
            if not await self._may_mail(account):
                event(log, logging.INFO, "nudge_skipped", reason="rate_limit")
                continue
            page = (
                "needs" if kind in ("ask.raised", "plan.proposed", "placement.proposed") else None
            )
            where = "#/needs" if page else f"#/item?item={node}"
            link = f"{self._public_url}/board/p/{project}{where}"
            message = nudge_message(kind=kind, node=node if not page else None, link=link)
            try:
                await self._mailer.send(address, message.subject, message.text, kind="nudge")
            except MailRefusal:
                continue

    async def _may_mail(self, account: int) -> bool:
        """Counts and takes one of the person's ten an hour in the database, under a lock, so that
        every server sharing it keeps to the same limit."""
        async with self._database.admin_transaction() as conn:
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"vrum.nudge_mails:{account}",)
            )
            row = await fetch_one(
                conn,
                "SELECT count(*) AS sent FROM nudge_mails "
                "WHERE account_id = %s AND sent_at > now() - interval '1 hour'",
                (account,),
            )
            if row is not None and int(row["sent"]) >= NUDGES_PER_HOUR:
                return False
            await conn.execute("INSERT INTO nudge_mails (account_id) VALUES (%s)", (account,))
            return True

    async def prune(self) -> None:
        async with self._database.admin_transaction() as conn:
            await conn.execute("DELETE FROM nudge_mails WHERE sent_at < now() - interval '1 hour'")
            await conn.execute(
                "DELETE FROM webhook_deliveries WHERE (state = 'delivered' AND delivered_at < "
                "now() - make_interval(days => %s)) OR (state <> 'delivered' AND created_at < "
                "now() - make_interval(days => %s))",
                (_KEEP_DELIVERED_DAYS, _KEEP_FAILED_DAYS),
            )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]
