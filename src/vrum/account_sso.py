"""External sign-in provider identity linking and sign-in flows for Accounts."""

from collections.abc import Collection
from typing import Any

import psycopg

from vrum.account_base import _AccountsBase
from vrum.account_models import (
    _ACCOUNT_COLUMNS,
    SSO_WORDS,
    AccountInfo,
    IdentityInfo,
    ProviderPerson,
    SignedIn,
    SignInMethods,
    SsoRefusal,
    _account,
    _plain_text,
    first_sign_in_outcome,
    name_for_new_account,
    usable_address,
    vouched_address,
)
from vrum.common import fetch_all, fetch_one


class _SsoMixin(_AccountsBase):
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
