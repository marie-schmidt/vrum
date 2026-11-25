"""Project agent key management and credential lookup for Accounts."""

import secrets
from collections.abc import Collection
from typing import Any

import psycopg

from vrum.account_base import _AccountsBase
from vrum.account_models import (
    _KEY_COLUMNS,
    _KEY_EXPIRED,
    KEY_PREFIX,
    KEY_WAS_REVOKED,
    NO_SUCH_KEY,
    AccessRefusal,
    KeyAccess,
    KeyExpired,
    KeyInfo,
    _key,
    _key_access,
    clean_agent_name,
    clean_allowance,
    clean_code_mode,
    clean_expiry,
    clean_verifier,
    digest_of,
)
from vrum.common import fetch_all, fetch_one


class _KeysMixin(_AccountsBase):
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
