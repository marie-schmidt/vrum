"""Email confirmation and password reset token flows for Accounts."""

import secrets
from typing import Any

from vrum.account_base import _AccountsBase
from vrum.account_models import (
    RESET_HOURS,
    RESET_LINK_NOT_VALID,
    RESET_NO_MAIL,
    RESET_PREFIX,
    VERIFY_HOURS,
    VERIFY_LINK_NOT_VALID,
    VERIFY_NO_MAIL,
    VERIFY_PREFIX,
    AccessRefusal,
    AccountInfo,
    clean_email,
    clean_new_password,
    digest_of,
)
from vrum.common import fetch_one
from vrum.mail import MailRefusal, reset_message, verification_message


class _EmailMixin(_AccountsBase):
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
