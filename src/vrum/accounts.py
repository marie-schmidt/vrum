"""Accounts, sessions, projects, members, invites, agent keys, email links, and SSO."""

from vrum.account_email import _EmailMixin
from vrum.account_keys import _KeysMixin
from vrum.account_members import _MembersMixin
from vrum.account_models import (
    _ACCOUNT_COLUMNS,
    _PEOPLE_LOCK,
    _account,
)
from vrum.account_models import (
    BAD_AGENT_KEY as BAD_AGENT_KEY,
)
from vrum.account_models import (
    DAY_SECONDS as DAY_SECONDS,
)
from vrum.account_models import (
    EXPIRY_NOT_A_DAY as EXPIRY_NOT_A_DAY,
)
from vrum.account_models import (
    FAILURE_WINDOW_SECONDS as FAILURE_WINDOW_SECONDS,
)
from vrum.account_models import (
    FAILURES_PER_CLIENT as FAILURES_PER_CLIENT,
)
from vrum.account_models import (
    FAILURES_PER_EMAIL as FAILURES_PER_EMAIL,
)
from vrum.account_models import (
    FAILURES_PER_INVITEE as FAILURES_PER_INVITEE,
)
from vrum.account_models import (
    HOUR_SECONDS as HOUR_SECONDS,
)
from vrum.account_models import (
    INVITE_DAYS as INVITE_DAYS,
)
from vrum.account_models import (
    INVITE_MAILS_PER_OWNER as INVITE_MAILS_PER_OWNER,
)
from vrum.account_models import (
    INVITE_MAILS_PER_PROJECT as INVITE_MAILS_PER_PROJECT,
)
from vrum.account_models import (
    INVITE_NO_MAIL as INVITE_NO_MAIL,
)
from vrum.account_models import (
    INVITE_NOT_VALID as INVITE_NOT_VALID,
)
from vrum.account_models import (
    INVITE_PREFIX as INVITE_PREFIX,
)
from vrum.account_models import (
    KEY_PREFIX as KEY_PREFIX,
)
from vrum.account_models import (
    KEY_WAS_REVOKED as KEY_WAS_REVOKED,
)
from vrum.account_models import (
    MAX_EMAIL_CHARS as MAX_EMAIL_CHARS,
)
from vrum.account_models import (
    MAX_NAME_CHARS as MAX_NAME_CHARS,
)
from vrum.account_models import (
    MAX_PASSWORD_CHARS as MAX_PASSWORD_CHARS,
)
from vrum.account_models import (
    MIN_PASSWORD_CHARS as MIN_PASSWORD_CHARS,
)
from vrum.account_models import (
    NO_AGENT_KEY as NO_AGENT_KEY,
)
from vrum.account_models import (
    NO_SUCH_KEY as NO_SUCH_KEY,
)
from vrum.account_models import (
    NO_SUCH_MEMBER as NO_SUCH_MEMBER,
)
from vrum.account_models import (
    NO_SUCH_PROJECT as NO_SUCH_PROJECT,
)
from vrum.account_models import (
    NOT_A_TRUE_OR_FALSE as NOT_A_TRUE_OR_FALSE,
)
from vrum.account_models import (
    NOT_SIGNED_IN as NOT_SIGNED_IN,
)
from vrum.account_models import (
    PROVIDERS as PROVIDERS,
)
from vrum.account_models import (
    RESET_HOURS as RESET_HOURS,
)
from vrum.account_models import (
    RESET_LINK_NOT_VALID as RESET_LINK_NOT_VALID,
)
from vrum.account_models import (
    RESET_NO_MAIL as RESET_NO_MAIL,
)
from vrum.account_models import (
    RESET_PREFIX as RESET_PREFIX,
)
from vrum.account_models import (
    RESET_REQUESTED as RESET_REQUESTED,
)
from vrum.account_models import (
    RESETS_PER_CLIENT as RESETS_PER_CLIENT,
)
from vrum.account_models import (
    RESETS_PER_EMAIL as RESETS_PER_EMAIL,
)
from vrum.account_models import (
    ROLES_TO_GIVE as ROLES_TO_GIVE,
)
from vrum.account_models import (
    RUN_IS_NOT_FOR_THE_BOARD as RUN_IS_NOT_FOR_THE_BOARD,
)
from vrum.account_models import (
    SCRIPT_KEY_NOW_TOOLS as SCRIPT_KEY_NOW_TOOLS,
)
from vrum.account_models import (
    SCRIPT_KEY_REVOKED as SCRIPT_KEY_REVOKED,
)
from vrum.account_models import (
    SESSION_HOURS as SESSION_HOURS,
)
from vrum.account_models import (
    SESSION_PREFIX as SESSION_PREFIX,
)
from vrum.account_models import (
    SSO_WORDS as SSO_WORDS,
)
from vrum.account_models import (
    VERIFIER_NOT_A_TRUE_OR_FALSE as VERIFIER_NOT_A_TRUE_OR_FALSE,
)
from vrum.account_models import (
    VERIFY_HOURS as VERIFY_HOURS,
)
from vrum.account_models import (
    VERIFY_LINK_NOT_VALID as VERIFY_LINK_NOT_VALID,
)
from vrum.account_models import (
    VERIFY_MAILS_PER_ACCOUNT as VERIFY_MAILS_PER_ACCOUNT,
)
from vrum.account_models import (
    VERIFY_NO_MAIL as VERIFY_NO_MAIL,
)
from vrum.account_models import (
    VERIFY_PREFIX as VERIFY_PREFIX,
)
from vrum.account_models import (
    VIEWER_CANNOT_CHANGE as VIEWER_CANNOT_CHANGE,
)
from vrum.account_models import (
    AccessRefusal as AccessRefusal,
)
from vrum.account_models import (
    AccountInfo as AccountInfo,
)
from vrum.account_models import (
    IdentityInfo as IdentityInfo,
)
from vrum.account_models import (
    InviteInfo as InviteInfo,
)
from vrum.account_models import (
    InviteMade as InviteMade,
)
from vrum.account_models import (
    InvitePreview as InvitePreview,
)
from vrum.account_models import (
    KeyAccess as KeyAccess,
)
from vrum.account_models import (
    KeyExpired as KeyExpired,
)
from vrum.account_models import (
    KeyInfo as KeyInfo,
)
from vrum.account_models import (
    MemberInfo as MemberInfo,
)
from vrum.account_models import (
    ProjectInfo as ProjectInfo,
)
from vrum.account_models import (
    ProviderPerson as ProviderPerson,
)
from vrum.account_models import (
    Role as Role,
)
from vrum.account_models import (
    SessionInfo as SessionInfo,
)
from vrum.account_models import (
    SignedIn as SignedIn,
)
from vrum.account_models import (
    SignInMethods as SignInMethods,
)
from vrum.account_models import (
    SsoRefusal as SsoRefusal,
)
from vrum.account_models import (
    clean_agent_name as clean_agent_name,
)
from vrum.account_models import (
    clean_allowance as clean_allowance,
)
from vrum.account_models import (
    clean_code_mode as clean_code_mode,
)
from vrum.account_models import (
    clean_display_name as clean_display_name,
)
from vrum.account_models import (
    clean_email as clean_email,
)
from vrum.account_models import (
    clean_expiry as clean_expiry,
)
from vrum.account_models import (
    clean_new_password as clean_new_password,
)
from vrum.account_models import (
    clean_verifier as clean_verifier,
)
from vrum.account_models import (
    code_tool_for_a_tools_key as code_tool_for_a_tools_key,
)
from vrum.account_models import (
    digest_of as digest_of,
)
from vrum.account_models import (
    first_sign_in_outcome as first_sign_in_outcome,
)
from vrum.account_models import (
    hash_password as hash_password,
)
from vrum.account_models import (
    key_expired_words as key_expired_words,
)
from vrum.account_models import (
    key_not_allowed as key_not_allowed,
)
from vrum.account_models import (
    log as log,
)
from vrum.account_models import (
    mask_email as mask_email,
)
from vrum.account_models import (
    name_for_new_account as name_for_new_account,
)
from vrum.account_models import (
    no_such_function as no_such_function,
)
from vrum.account_models import (
    only_the_owner as only_the_owner,
)
from vrum.account_models import (
    password_matches as password_matches,
)
from vrum.account_models import (
    script_key_expired_words as script_key_expired_words,
)
from vrum.account_models import (
    tool_by_name_for_a_code_key as tool_by_name_for_a_code_key,
)
from vrum.account_models import (
    tool_not_offered as tool_not_offered,
)
from vrum.account_models import (
    usable_address as usable_address,
)
from vrum.account_models import (
    vouched_address as vouched_address,
)
from vrum.account_models import (
    waiting as waiting,
)
from vrum.account_sso import _SsoMixin
from vrum.common import fetch_one


class Accounts(_SsoMixin, _EmailMixin, _KeysMixin, _MembersMixin):
    """The one owner of accounts, browser sessions, projects, members, invites, and agent keys."""

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


# A hash to check a password against when the address has no account (see Accounts.sign_in).
_DECOY = hash_password("no account has this password")
