"""Board HTTP routes for projects, agent keys, webhooks, email nudges, members, and invites."""

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from vrum.accounts import (
    NO_SUCH_MEMBER,
    AccessRefusal,
    Accounts,
    InviteMade,
    SessionInfo,
    clean_allowance,
    clean_code_mode,
    clean_expiry,
    clean_verifier,
)
from vrum.board.http import (
    API,
    api_route,
    fields_of,
    in_project,
    json_object,
    json_response,
    maybe_json_dict,
    number_param,
)
from vrum.board.session import client_of, signed_in
from vrum.config import Settings
from vrum.guard import GuardedServer
from vrum.notify_events import EVENT_TYPES, MOST_WEBHOOKS, NUDGE_TYPES
from vrum.webhooks import WebhookBook


def _made_body(made: InviteMade) -> dict[str, Any]:
    body: dict[str, Any] = {
        "invite": made.invite.model_dump(mode="json"),
        "token": made.token,
        "emailed": made.emailed,
    }
    if made.email_problem is not None:
        body["email_problem"] = made.email_problem
    return body


def _person_in_address(request: Request, session: SessionInfo) -> int:
    raw = request.path_params["person"]
    if raw == "me":
        return session.account.id
    found = number_param(raw)
    if not found:
        raise AccessRefusal(NO_SUCH_MEMBER, 404)
    return found


def _optional_email(body: dict[str, Any]) -> str | None:
    raw = body.get("email")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise AccessRefusal("The email address has to be text, like name@example.com.")
    return raw.strip() or None


def register_project_routes(
    mcp: GuardedServer,
    *,
    settings: Settings,
    accounts: Accounts,
    webhook_book: WebhookBook,
    tool_names: Callable[[], Awaitable[frozenset[str]]],
) -> None:
    async def list_projects(request: Request) -> Response:
        session = await signed_in(accounts, request, changing=False)
        found = await accounts.projects(session.account.id)
        return json_response({"projects": [project.model_dump(mode="json") for project in found]})

    async def create_project(request: Request) -> Response:
        session = await signed_in(accounts, request, changing=True)
        body = await maybe_json_dict(request)
        name = body.get("name")
        project = await accounts.create_project(
            session.account.id, name if isinstance(name, str) else ""
        )
        return json_response(project.model_dump(mode="json"), 201)

    async def show_project(request: Request) -> Response:
        _, project = await in_project(accounts, request, changing=False)
        return json_response(project.model_dump(mode="json"))

    async def list_keys(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=False)
        found = await accounts.keys(session.account.id, project.id)
        return json_response({"keys": [key.model_dump(mode="json") for key in found]})

    async def create_key(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        body = await maybe_json_dict(request)
        name = body.get("name")
        key, secret = await accounts.create_key(
            session.account.id,
            project.id,
            name if isinstance(name, str) else "",
            body.get("tools"),
            known_tools=await tool_names(),
            code_mode=body.get("code_mode", False),
            verifier=body.get("verifier", False),
            expires_on=body.get("expires_on"),
        )
        return json_response({"key": key.model_dump(mode="json"), "secret": secret}, 201)

    async def change_key(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        body = await maybe_json_dict(request)
        changes = {"tools", "code_mode", "verifier", "expires_on"}
        if not changes & body.keys():
            raise AccessRefusal(
                "Say what to change: tools is a list of tool names, or null for the standard "
                "tools, code_mode is true to run code or false for one tool at a time, "
                "verifier is true or false, and expires_on is the last day the key works, or "
                "null for a key that never expires. Nothing was changed."
            )
        await accounts.owned_project(
            session.account.id, project.id, to="change what an agent key may do"
        )
        known = await tool_names()
        if "tools" in body:
            clean_allowance(body["tools"], known)
        if "code_mode" in body:
            clean_code_mode(body["code_mode"])
        if "verifier" in body:
            clean_verifier(body["verifier"])
        if "expires_on" in body:
            clean_expiry(body["expires_on"])
        key_id = number_param(request.path_params["key_id"])
        key = None
        if "tools" in body:
            key = await accounts.set_key_tools(
                session.account.id, project.id, key_id, body["tools"], known_tools=known
            )
        if "code_mode" in body:
            key = await accounts.set_key_mode(
                session.account.id, project.id, key_id, body["code_mode"]
            )
        if "verifier" in body:
            key = await accounts.set_key_verifier(
                session.account.id, project.id, key_id, body["verifier"]
            )
        if "expires_on" in body:
            key = await accounts.set_key_expiry(
                session.account.id, project.id, key_id, body["expires_on"]
            )
        assert key is not None
        return json_response({"key": key.model_dump(mode="json")})

    async def revoke_key(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        key = await accounts.revoke_key(
            session.account.id, project.id, number_param(request.path_params["key_id"])
        )
        return json_response({"key": key.model_dump(mode="json")})

    async def list_webhooks(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=False)
        found = await webhook_book.list_webhooks(session.account.id, project.id)
        return json_response(
            {
                "webhooks": [one.model_dump(mode="json") for one in found],
                "kinds": EVENT_TYPES,
                "most": MOST_WEBHOOKS,
            }
        )

    async def create_webhook(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        body = await json_object(request)
        made = await webhook_book.create_webhook(
            session.account.id, project.id, body.get("url"), body.get("events")
        )
        return json_response(made.model_dump(mode="json"), 201)

    async def change_webhook(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        body = await json_object(request)
        changed = await webhook_book.change_webhook(
            session.account.id,
            project.id,
            number_param(request.path_params["webhook_id"]),
            events=body.get("events"),
            enabled=body.get("enabled"),
        )
        return json_response({"webhook": changed.model_dump(mode="json")})

    async def replace_webhook_secret(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        made = await webhook_book.rotate_secret(
            session.account.id, project.id, number_param(request.path_params["webhook_id"])
        )
        return json_response(made.model_dump(mode="json"))

    async def delete_webhook(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        await webhook_book.delete_webhook(
            session.account.id, project.id, number_param(request.path_params["webhook_id"])
        )
        return json_response({"deleted": True})

    async def webhook_deliveries(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=False)
        found = await webhook_book.deliveries(
            session.account.id,
            project.id,
            number_param(request.path_params["webhook_id"]),
            number_param(request.query_params.get("limit", "50")) or 50,
        )
        return json_response({"deliveries": [one.model_dump(mode="json") for one in found]})

    async def test_webhook(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        result = await webhook_book.send_test(
            session.account.id, project.id, number_param(request.path_params["webhook_id"])
        )
        return json_response(result.model_dump(mode="json"))

    async def my_nudges(request: Request) -> Response:
        session = await signed_in(accounts, request, changing=False)
        found = await webhook_book.my_nudges(session.account.id)
        return json_response(
            {
                "nudges": [one.model_dump(mode="json") for one in found],
                "kinds": {kind: EVENT_TYPES[kind] for kind in NUDGE_TYPES},
                "mail": settings.mail is not None,
                "email_confirmed": session.account.email_verified,
            }
        )

    async def set_nudges(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        body = await json_object(request)
        made = await webhook_book.set_nudges(session.account.id, project.id, body.get("events"))
        return json_response({"nudge": made.model_dump(mode="json")})

    async def list_members(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=False)
        found = await accounts.members(session.account.id, project.id)
        return json_response({"members": [one.model_dump(mode="json") for one in found]})

    async def change_member(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        given = await fields_of(request, "role")
        changed = await accounts.change_role(
            session.account.id, project.id, _person_in_address(request, session), given["role"]
        )
        return json_response({"member": changed.model_dump(mode="json")})

    async def remove_member(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        gone = await accounts.remove_person(
            session.account.id, project.id, _person_in_address(request, session)
        )
        return json_response({"member": gone.model_dump(mode="json")})

    async def list_invites(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=False)
        found = await accounts.invites(session.account.id, project.id)
        return json_response({"invites": [one.model_dump(mode="json") for one in found]})

    async def create_invite(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        body = await json_object(request)
        role = body["role"] if isinstance(body.get("role"), str) else ""
        made = await accounts.create_invite(
            session.account.id, project.id, role, _optional_email(body)
        )
        return json_response(_made_body(made), 201)

    async def resend_invite(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        made = await accounts.resend_invite(
            session.account.id, project.id, number_param(request.path_params["invite_id"])
        )
        return json_response(_made_body(made))

    async def revoke_invite(request: Request) -> Response:
        session, project = await in_project(accounts, request, changing=True)
        invite = await accounts.revoke_invite(
            session.account.id, project.id, number_param(request.path_params["invite_id"])
        )
        return json_response({"invite": invite.model_dump(mode="json")})

    async def show_invitation(request: Request) -> Response:
        session = await signed_in(accounts, request, changing=False)
        preview = await accounts.preview_invite(
            session.account, request.path_params["token"], client_of(request)
        )
        return json_response({"invite": preview.model_dump(mode="json")})

    async def accept_invitation(request: Request) -> Response:
        session = await signed_in(accounts, request, changing=True)
        joined = await accounts.accept_invite(
            session.account, request.path_params["token"], client_of(request)
        )
        return json_response({"project": joined.model_dump(mode="json")})

    api_route(mcp, f"{API}/projects", list_projects, "GET")
    api_route(mcp, f"{API}/projects", create_project, "POST")
    api_route(mcp, f"{API}/projects/{{project_id}}", show_project, "GET")
    api_route(mcp, f"{API}/projects/{{project_id}}/keys", list_keys, "GET")
    api_route(mcp, f"{API}/projects/{{project_id}}/keys", create_key, "POST")
    api_route(mcp, f"{API}/projects/{{project_id}}/keys/{{key_id}}", revoke_key, "DELETE")
    api_route(mcp, f"{API}/projects/{{project_id}}/keys/{{key_id}}", change_key, "PATCH")
    api_route(mcp, f"{API}/projects/{{project_id}}/webhooks", list_webhooks, "GET")
    api_route(mcp, f"{API}/projects/{{project_id}}/webhooks", create_webhook, "POST")
    api_route(
        mcp, f"{API}/projects/{{project_id}}/webhooks/{{webhook_id}}", change_webhook, "PATCH"
    )
    api_route(
        mcp, f"{API}/projects/{{project_id}}/webhooks/{{webhook_id}}", delete_webhook, "DELETE"
    )
    api_route(
        mcp,
        f"{API}/projects/{{project_id}}/webhooks/{{webhook_id}}/secret",
        replace_webhook_secret,
        "POST",
    )
    api_route(
        mcp,
        f"{API}/projects/{{project_id}}/webhooks/{{webhook_id}}/deliveries",
        webhook_deliveries,
        "GET",
    )
    api_route(
        mcp, f"{API}/projects/{{project_id}}/webhooks/{{webhook_id}}/test", test_webhook, "POST"
    )
    api_route(mcp, f"{API}/nudges", my_nudges, "GET")
    api_route(mcp, f"{API}/projects/{{project_id}}/nudges", set_nudges, "PUT")
    api_route(mcp, f"{API}/projects/{{project_id}}/members", list_members, "GET")
    api_route(mcp, f"{API}/projects/{{project_id}}/members/{{person}}", change_member, "PATCH")
    api_route(mcp, f"{API}/projects/{{project_id}}/members/{{person}}", remove_member, "DELETE")
    api_route(mcp, f"{API}/projects/{{project_id}}/invites", list_invites, "GET")
    api_route(mcp, f"{API}/projects/{{project_id}}/invites", create_invite, "POST")
    api_route(
        mcp, f"{API}/projects/{{project_id}}/invites/{{invite_id}}/resend", resend_invite, "POST"
    )
    api_route(mcp, f"{API}/projects/{{project_id}}/invites/{{invite_id}}", revoke_invite, "DELETE")
    api_route(mcp, f"{API}/invites/{{token}}", show_invitation, "GET")
    api_route(mcp, f"{API}/invites/{{token}}/accept", accept_invitation, "POST")
