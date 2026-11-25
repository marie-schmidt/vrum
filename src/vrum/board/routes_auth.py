"""Board HTTP routes for accounts, sessions, SSO providers, email verification and resets."""

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from vrum.accounts import (
    RESET_REQUESTED,
    VERIFY_HOURS,
    AccessRefusal,
    Accounts,
    SessionInfo,
    SignInMethods,
    SsoRefusal,
)
from vrum.board.http import (
    API,
    SECURITY_HEADERS,
    api_route,
    fields_of,
    json_object,
    json_response,
    refused_response,
)
from vrum.board.session import (
    CROSS_SITE,
    client_of,
    cross_site_problem,
    end_cookie,
    end_sso_cookie,
    signed_in,
    start_cookie,
    start_sso_cookie,
)
from vrum.config import Settings
from vrum.guard import GuardedServer
from vrum.sso import COOKIE_NAME as SSO_COOKIE
from vrum.sso import REDIRECT_PROVIDERS, Sso


def _sign_in_options(sso: Sso) -> dict[str, Any]:
    return {"providers": sso.for_the_pages(), "sso_create_accounts": sso.create_accounts}


def _session_body(session: SessionInfo, mail: bool, sso: Sso) -> dict[str, Any]:
    return {
        "account": session.account.model_dump(),
        "csrf": session.csrf,
        "expires_at": session.expires_at.isoformat(),
        "mail": mail,
        **_sign_in_options(sso),
    }


def _methods_body(methods: SignInMethods, sso: Sso) -> dict[str, Any]:
    return {
        "has_password": methods.has_password,
        "signed_up_with": methods.signed_up_with,
        "identities": [
            {
                "provider": one.provider,
                "name": sso.name_of(one.provider),
                "display_name": one.display_name,
                "email": one.email,
                "linked_at": one.linked_at.isoformat(),
            }
            for one in methods.identities
        ],
    }


def register_auth_routes(
    mcp: GuardedServer, *, settings: Settings, accounts: Accounts, sso: Sso
) -> None:
    def with_cookie(session: Any, request: Request, signed: Any, status: int = 200) -> Response:
        response = json_response(_session_body(session, accounts.mail_enabled, sso), status)
        start_cookie(response, request, signed)
        return response

    def not_set_up() -> JSONResponse:
        return refused_response(SsoRefusal("not_set_up"))

    def secure_cookie(request: Request) -> bool:
        return request.url.scheme == "https" or settings.public_url.startswith("https://")

    async def register(request: Request) -> Response:
        given = await fields_of(request, "email", "name", "password")
        signed = await accounts.register(
            given["email"], given["name"], given["password"], client_of(request)
        )
        return with_cookie(signed.session, request, signed, 201)

    async def sign_in(request: Request) -> Response:
        given = await fields_of(request, "email", "password")
        signed = await accounts.sign_in(given["email"], given["password"], client_of(request))
        return with_cookie(signed.session, request, signed)

    async def whoami(request: Request) -> Response:
        try:
            session = await signed_in(accounts, request, changing=False)
        except AccessRefusal as error:
            if error.status == 401:
                return json_response(
                    {
                        "account": None,
                        "csrf": None,
                        "mail": accounts.mail_enabled,
                        **_sign_in_options(sso),
                    }
                )
            raise
        return json_response(_session_body(session, accounts.mail_enabled, sso))

    async def sign_out(request: Request) -> Response:
        session = await signed_in(accounts, request, changing=True)
        await accounts.sign_out(session.id)
        response = json_response({"signed_out": True})
        end_cookie(response)
        return response

    async def rename_account(request: Request) -> Response:
        session = await signed_in(accounts, request, changing=True)
        given = await fields_of(request, "name")
        account = await accounts.rename(session.account.id, given["name"])
        return json_response(account.model_dump())

    async def change_password(request: Request) -> Response:
        session = await signed_in(accounts, request, changing=True)
        given = await fields_of(request, "current", "new")
        await accounts.change_password(session, given["current"], given["new"], client_of(request))
        return json_response({"ok": True})

    async def start_provider_sign_in(request: Request) -> Response:
        provider = request.path_params["provider"]
        if provider not in REDIRECT_PROVIDERS or not sso.name_of(provider):
            return not_set_up()
        body = await json_object(request)
        given = body.get("intent")
        intent = given if isinstance(given, str) else ""
        session = await signed_in(accounts, request, changing=True) if intent == "link" else None
        started = await sso.start(
            provider, intent, body.get("return_to"), session, client_of(request)
        )
        response = json_response({"url": started.url})
        start_sso_cookie(response, started.cookie, secure=secure_cookie(request))
        return response

    async def provider_callback(request: Request) -> Response:
        provider = request.path_params["provider"]
        if request.method != "GET":
            return Response(status_code=405, headers={"Allow": "GET"})
        if provider not in REDIRECT_PROVIDERS or not sso.name_of(provider):
            return not_set_up()
        landed = await sso.finish(
            provider,
            dict(request.query_params),
            request.cookies.get(SSO_COOKIE),
            client_of(request),
        )
        response = RedirectResponse(
            landed.location,
            status_code=303,
            headers={**SECURITY_HEADERS, "Cache-Control": "no-store"},
        )
        if landed.signed is not None:
            start_cookie(response, request, landed.signed)
        end_sso_cookie(response, secure=secure_cookie(request))
        return response

    async def ldap_sign_in(request: Request) -> Response:
        if not sso.name_of("ldap"):
            return not_set_up()
        given = await fields_of(request, "username", "password")
        signed = await sso.ldap_sign_in(given["username"], given["password"], client_of(request))
        return with_cookie(signed.session, request, signed)

    async def list_sign_in_methods(request: Request) -> Response:
        session = await signed_in(accounts, request, changing=False)
        methods = await accounts.sign_in_methods(session.account.id, sso.configured())
        return json_response(_methods_body(methods, sso))

    async def connect_ldap(request: Request) -> Response:
        if not sso.name_of("ldap"):
            return not_set_up()
        session = await signed_in(accounts, request, changing=True)
        given = await fields_of(request, "username", "password")
        await sso.ldap_link(session, given["username"], given["password"], client_of(request))
        methods = await accounts.sign_in_methods(session.account.id, sso.configured())
        return json_response(_methods_body(methods, sso))

    async def disconnect_provider(request: Request) -> Response:
        provider = request.path_params["provider"]
        name = sso.name_of(provider)
        if not name:
            return not_set_up()
        session = await signed_in(accounts, request, changing=True)
        await accounts.unlink_provider(
            session.account.id,
            session.id,
            provider,
            provider_name=name,
            configured=sso.configured(),
        )
        methods = await accounts.sign_in_methods(session.account.id, sso.configured())
        return json_response(_methods_body(methods, sso))

    async def send_verification(request: Request) -> Response:
        session = await signed_in(accounts, request, changing=True)
        await accounts.send_verification(session.account)
        return json_response(
            {
                "message": f"A link to confirm {session.account.email} has been sent to it. It "
                f"works once, for {VERIFY_HOURS} hours."
            },
            202,
        )

    async def verify_email(request: Request) -> Response:
        if cross_site_problem(request) is not None:
            raise AccessRefusal(CROSS_SITE, 403)
        address = await accounts.verify_email(request.path_params["token"], client_of(request))
        return json_response({"ok": True, "email": address})

    async def request_password_reset(request: Request) -> Response:
        given = await fields_of(request, "email")
        await accounts.request_reset(given["email"], client_of(request))
        return json_response({"message": RESET_REQUESTED}, 202)

    async def check_reset_link(request: Request) -> Response:
        await accounts.check_reset_link(request.path_params["token"], client_of(request))
        return json_response({"ok": True})

    async def reset_password(request: Request) -> Response:
        given = await fields_of(request, "new")
        await accounts.reset_password(
            request.path_params["token"], given["new"], client_of(request)
        )
        return json_response({"ok": True})

    api_route(mcp, f"{API}/accounts", register, "POST")
    api_route(mcp, f"{API}/session", sign_in, "POST")
    api_route(mcp, f"{API}/session", whoami, "GET")
    api_route(mcp, f"{API}/session", sign_out, "DELETE")
    api_route(mcp, f"{API}/account", rename_account, "PATCH")
    api_route(mcp, f"{API}/account/password", change_password, "POST")
    api_route(mcp, f"{API}/account/verify-email", send_verification, "POST")
    api_route(mcp, f"{API}/account/identities", list_sign_in_methods, "GET")
    api_route(mcp, f"{API}/account/identities/ldap", connect_ldap, "POST")
    api_route(mcp, f"{API}/account/identities/{{provider}}", disconnect_provider, "DELETE")
    api_route(mcp, f"{API}/auth/ldap", ldap_sign_in, "POST")
    api_route(mcp, f"{API}/auth/{{provider}}/start", start_provider_sign_in, "POST")
    mcp.custom_route("/board/auth/{provider}/callback", methods=["GET"])(provider_callback)
    api_route(mcp, f"{API}/verify/{{token}}", verify_email, "POST")
    api_route(mcp, f"{API}/password-reset", request_password_reset, "POST")
    api_route(mcp, f"{API}/password-reset/{{token}}", check_reset_link, "GET")
    api_route(mcp, f"{API}/password-reset/{{token}}", reset_password, "POST")
