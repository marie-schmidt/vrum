"""Project membership, role changes, and invitation lifecycle for Accounts."""

import secrets
from typing import Any

from vrum.account_base import _AccountsBase
from vrum.account_models import (
    _INVITE_COLUMNS,
    _PENDING_INVITE,
    _PEOPLE_LOCK,
    INVITE_DAYS,
    INVITE_NO_MAIL,
    INVITE_NOT_VALID,
    INVITE_PREFIX,
    NO_SUCH_MEMBER,
    ROLES_TO_GIVE,
    AccessRefusal,
    AccountInfo,
    InviteInfo,
    InviteMade,
    InvitePreview,
    MemberInfo,
    ProjectInfo,
    _invite,
    _member,
    clean_email,
    digest_of,
    mask_email,
    only_the_owner,
    waiting,
)
from vrum.common import fetch_all, fetch_one
from vrum.mail import MailRefusal, invitation_message, is_plain_address


class _MembersMixin(_AccountsBase):
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
