"""The board's server side: the page files, accounts and projects, and the one way the pages reach
a project's graph.

The page files (src/vrum/board/static) are plain HTML, CSS and JavaScript that hold no data, so
they are open and a browser can load them before anyone has signed in. Everything else needs a
person's session (a cookie), and everything about a project needs the signed-in person to be in it
(its owner, or an editor or a viewer the owner invited), which is checked here on every request,
never in the browser, and so is what the person is in it: a viewer's call to anything that changes
something is refused, and only the owner makes agent keys, invites people and changes who is in the
project. What the pages show or change goes through `POST /board/api/projects/<id>/tool/<name>`,
which calls the MCP tool of that name on the same server for that project, as the person who is
signed in (their account's name, as a person), so the board is subject to exactly the same checks
as an agent: the same refusals in the same words, the same versions, claims and event log, the same
scan for secrets. The one difference is who is asking: only a person can confirm, and the board is
a person. There is nothing else the board can do with a graph: the tools it may call are listed
here."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from vrum.access import MCP_PATH
from vrum.accounts import (
    NO_SUCH_MEMBER,
    RESET_REQUESTED,
    SESSION_HOURS,
    VERIFY_HOURS,
    VIEWER_CANNOT_CHANGE,
    AccessRefusal,
    Accounts,
    InviteMade,
    ProjectInfo,
    SessionInfo,
    SignInMethods,
    SsoRefusal,
)
from vrum.board.session import (
    CROSS_SITE,
    client_of,
    cross_site_problem,
    end_cookie,
    end_sso_cookie,
    refuse_unless_same_site_json,
    signed_in,
    start_cookie,
    start_sso_cookie,
)
from vrum.board.tools import AREAS, TOOL_PLACES, AgentsOnly, OnTheBoard
from vrum.config import Settings
from vrum.db import NoProject, working_for
from vrum.guard import GuardedServer
from vrum.identity import NoIdentity, person, working_as
from vrum.records import PEOPLE_ONLY_TOOLS
from vrum.search import Searcher
from vrum.sso import COOKIE_NAME as SSO_COOKIE
from vrum.sso import REDIRECT_PROVIDERS, Sso
from vrum.surface import AGENT_SURFACE, MOST_AGENT_TOOLS

STATIC = Path(__file__).parent / "static"

# The tools the pages may call. A test checks that each is a tool the server has. Each later
# board milestone adds the tools its pages need.
READ_TOOLS = (
    "list_nodes",
    "get_node",
    "node_history",
    "list_links",
    "list_rules",
    "get_rule",
    "rule_history",
    "coverage_report",
    "get_definition_of_done",
    "list_notes",
    "get_note",
    "note_history",
    "recall",
    "search",
    "similar_items",
    "find_overlaps",
    "find_mentions",
    "export_graph",
    "changes_since",
    "ready_work",
    "who_is_working",
    "server_status",
)
WRITE_TOOLS = (
    "create_node",
    "update_node",
    "update_nodes",
    "move_node",
    "add_link",
    "remove_link",
    "claim_node",
    "renew_claim",
    "release_claim",
    "create_rule",
    "update_rule",
    "place_rule",
    "set_definition_of_done",
    "add_note",
    "supersede_note",
    "withdraw_note",
    "link_note",
    "unlink_note",
    "set_confirmation",
    "set_check",
)
BOARD_TOOLS = frozenset(READ_TOOLS + WRITE_TOOLS)

# Where each kind of file is served from. Nothing else is, so a file dropped into the static
# folder is not served until it has a type here.
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}

# What a page may load and do: its own files and its own server, and nothing from anywhere else.
# No inline script or style, no frames, no forms that leave the page.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _page_files() -> dict[str, tuple[bytes, str]]:
    """Every page file as it is served: its address, its bytes and its type. The home of the board
    is also served at /board and /board/."""
    files: dict[str, tuple[bytes, str]] = {}
    for path in sorted(STATIC.rglob("*")):
        content_type = _CONTENT_TYPES.get(path.suffix)
        if path.is_file() and content_type is not None:
            address = "/board/" + path.relative_to(STATIC).as_posix()
            files[address] = (path.read_bytes(), content_type)
    index = files.get("/board/index.html")
    if index is not None:
        files["/board"] = files["/board/"] = index
    return files


_FILES = _page_files()

# The addresses a link in an email opens, followed by its token. Each serves the board's one page.
_LINK_PAGES = ("/board/invite/", "/board/reset/", "/board/verify/")


def open_paths() -> Mapping[str, str]:
    """The addresses that need no sign-in (each answers GET only): the page files, which hold no
    data. The data calls under /board/api are not among them."""
    return dict.fromkeys(_FILES, "GET")


def _json(body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(
        body, status_code=status, headers={**SECURITY_HEADERS, "Cache-Control": "no-store"}
    )


def _plain(tool: str, error: ToolError) -> str:
    """A refusal as the board shows it: what an agent is told, without the "Error executing tool"
    the protocol puts in front."""
    prefix = f"Error executing tool {tool}: "
    message = str(error)
    return message.removeprefix(prefix)


def _refused(error: AccessRefusal) -> JSONResponse:
    body = {"message": str(error)}
    if isinstance(error, SsoRefusal):
        # Which sign-in problem it is, for the page to tell apart. The words are in the message.
        body["code"] = error.code
    return _json(body, error.status)


def _session_body(session: SessionInfo, mail: bool, sso: Sso) -> dict[str, Any]:
    """The person signed in, whether this server can send email, and which other ways of signing
    in it has (so that the pages offer what is set up, and nothing else)."""
    return {
        "account": session.account.model_dump(),
        "csrf": session.csrf,
        "expires_at": session.expires_at.isoformat(),
        "mail": mail,
        **_sign_in_options(sso),
    }


def _sign_in_options(sso: Sso) -> dict[str, Any]:
    return {"providers": sso.for_the_pages(), "sso_create_accounts": sso.create_accounts}


def _methods_body(methods: SignInMethods, sso: Sso) -> dict[str, Any]:
    """How the signed-in person can sign in, as the Users page shows it."""
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


def _made_body(made: InviteMade) -> dict[str, Any]:
    """An invitation as it is answered when it is made or sent again: the invitation, its token
    (the only time it is sent), whether it was emailed, and why not when it was not."""
    body: dict[str, Any] = {
        "invite": made.invite.model_dump(mode="json"),
        "token": made.token,
        "emailed": made.emailed,
    }
    if made.email_problem is not None:
        body["email_problem"] = made.email_problem
    return body


def _number(raw: Any) -> int:
    """A number in an address. Anything that is not a number is, like a number that names nothing
    of the person's, no such thing."""
    return int(raw) if str(raw).isdecimal() and len(str(raw)) <= 18 else 0


def _project_number(request: Request) -> int:
    """The project number in the address. Anything that is not a number is, like a number that
    names no project of the person's, a project that is not theirs."""
    return _number(request.path_params["project_id"])


def add_board(
    mcp: GuardedServer,
    *,
    settings: Settings,
    searcher: Searcher,
    accounts: Accounts,
    sso: Sso,
) -> None:
    """Add the board's routes to the server."""

    async def page(request: Request) -> Response:
        path = request.url.path
        # A project's address, an invitation's, a link in an email (to choose a new password or to
        # confirm an address) all serve the same page as the board's own: the page asks the server
        # whether the person is in the project, or what the link is, and says so when not. Opening
        # one of these addresses never uses the link: only the page's own request does.
        is_a_project = path.startswith("/board/p/") and path[len("/board/p/") :].isdecimal()
        is_a_link = any(
            path.startswith(prefix) and path[len(prefix) :] and "/" not in path[len(prefix) :]
            for prefix in _LINK_PAGES
        )
        if is_a_project or is_a_link:
            path = "/board"
        found = _FILES.get(path)
        if found is None:
            return Response("There is no such page file.", status_code=404, media_type="text/plain")
        body, content_type = found
        return Response(
            body,
            media_type=content_type,
            headers={**SECURITY_HEADERS, "Cache-Control": "no-cache"},
        )

    # -- accounts and sessions -------------------------------------------------------------------

    async def fields_of(request: Request, *names: str) -> dict[str, str]:
        """The named text fields of a JSON body. A missing or mistyped one is an empty text, which
        the account checks then refuse in their own words."""
        refuse_unless_same_site_json(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise AccessRefusal("The request has to be a JSON object.")
        return {name: body[name] if isinstance(body.get(name), str) else "" for name in names}

    def with_cookie(session: Any, request: Request, signed: Any, status: int = 200) -> Response:
        response = _json(_session_body(session, accounts.mail_enabled, sso), status)
        start_cookie(response, request, signed)
        return response

    async def register(request: Request) -> Response:
        try:
            given = await fields_of(request, "email", "name", "password")
            signed = await accounts.register(
                given["email"], given["name"], given["password"], client_of(request)
            )
        except AccessRefusal as error:
            return _refused(error)
        return with_cookie(signed.session, request, signed, 201)

    async def sign_in(request: Request) -> Response:
        try:
            given = await fields_of(request, "email", "password")
            signed = await accounts.sign_in(given["email"], given["password"], client_of(request))
        except AccessRefusal as error:
            return _refused(error)
        return with_cookie(signed.session, request, signed)

    async def whoami(request: Request) -> Response:
        """Who this browser is signed in as. Nobody is a normal answer, not an error, so that the
        page can ask without the browser reporting a failed request."""
        try:
            session = await signed_in(accounts, request, changing=False)
        except AccessRefusal as error:
            if error.status == 401:
                return _json(
                    {
                        "account": None,
                        "csrf": None,
                        "mail": accounts.mail_enabled,
                        **_sign_in_options(sso),
                    }
                )
            return _refused(error)
        return _json(_session_body(session, accounts.mail_enabled, sso))

    async def sign_out(request: Request) -> Response:
        try:
            session = await signed_in(accounts, request, changing=True)
        except AccessRefusal as error:
            return _refused(error)
        await accounts.sign_out(session.id)
        response = _json({"signed_out": True})
        end_cookie(response)
        return response

    async def rename_account(request: Request) -> Response:
        """Change the name the signed-in person is shown as, and recorded with."""
        try:
            session = await signed_in(accounts, request, changing=True)
            given = await fields_of(request, "name")
            account = await accounts.rename(session.account.id, given["name"])
        except AccessRefusal as error:
            return _refused(error)
        return _json(account.model_dump())

    async def change_password(request: Request) -> Response:
        """Change the signed-in person's password. Their other sessions end."""
        try:
            session = await signed_in(accounts, request, changing=True)
            given = await fields_of(request, "current", "new")
            await accounts.change_password(
                session, given["current"], given["new"], client_of(request)
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json({"ok": True})

    # -- sign in with another provider: OpenID Connect, GitHub, LDAP -----------------------------
    # With no provider set up, none of these does anything: each answers 404 in words.

    def not_set_up() -> JSONResponse:
        return _refused(SsoRefusal("not_set_up"))

    def secure_cookie(request: Request) -> bool:
        return request.url.scheme == "https" or settings.public_url.startswith("https://")

    async def start_provider_sign_in(request: Request) -> Response:
        """Begin signing in with, or connecting, OpenID Connect or GitHub: answers the address to
        send the browser to, and puts the sign-in's name in a cookie that only comes back to the
        callback below."""
        provider = request.path_params["provider"]
        if provider not in REDIRECT_PROVIDERS or not sso.name_of(provider):
            return not_set_up()
        try:
            refuse_unless_same_site_json(request)
            try:
                body = await request.json()
            except ValueError:
                body = None
            if not isinstance(body, dict):
                raise AccessRefusal("The request has to be a JSON object.")
            given = body.get("intent")
            intent = given if isinstance(given, str) else ""
            # Connecting needs the person to be signed in; signing in needs nothing yet.
            session = (
                await signed_in(accounts, request, changing=True) if intent == "link" else None
            )
            started = await sso.start(
                provider, intent, body.get("return_to"), session, client_of(request)
            )
        except AccessRefusal as error:
            return _refused(error)
        response = _json({"url": started.url})
        start_sso_cookie(response, started.cookie, secure=secure_cookie(request))
        return response

    async def provider_callback(request: Request) -> Response:
        """The provider sent the browser back. It always ends in a redirect: to the board, signed
        in (or with the account connected), or with one problem named in the address."""
        provider = request.path_params["provider"]
        if request.method != "GET":
            # A HEAD (which Starlette also routes here) must not use up a sign-in.
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
        try:
            given = await fields_of(request, "username", "password")
            signed = await sso.ldap_sign_in(
                given["username"], given["password"], client_of(request)
            )
        except AccessRefusal as error:
            return _refused(error)
        return with_cookie(signed.session, request, signed)

    async def list_sign_in_methods(request: Request) -> Response:
        try:
            session = await signed_in(accounts, request, changing=False)
        except AccessRefusal as error:
            return _refused(error)
        methods = await accounts.sign_in_methods(session.account.id, sso.configured())
        return _json(_methods_body(methods, sso))

    async def connect_ldap(request: Request) -> Response:
        if not sso.name_of("ldap"):
            return not_set_up()
        try:
            session = await signed_in(accounts, request, changing=True)
            given = await fields_of(request, "username", "password")
            await sso.ldap_link(session, given["username"], given["password"], client_of(request))
        except AccessRefusal as error:
            return _refused(error)
        methods = await accounts.sign_in_methods(session.account.id, sso.configured())
        return _json(_methods_body(methods, sso))

    async def disconnect_provider(request: Request) -> Response:
        provider = request.path_params["provider"]
        name = sso.name_of(provider)
        if not name:
            return not_set_up()
        try:
            session = await signed_in(accounts, request, changing=True)
            await accounts.unlink_provider(
                session.account.id,
                session.id,
                provider,
                provider_name=name,
                configured=sso.configured(),
            )
        except AccessRefusal as error:
            return _refused(error)
        methods = await accounts.sign_in_methods(session.account.id, sso.configured())
        return _json(_methods_body(methods, sso))

    # -- email: confirming an address and choosing a new password --------------------------------
    # With no mail server set up, the reset routes answer 404 in words and nothing else changes.

    async def send_verification(request: Request) -> Response:
        """Send the signed-in person a link that confirms their address."""
        try:
            session = await signed_in(accounts, request, changing=True)
            await accounts.send_verification(session.account)
        except AccessRefusal as error:
            return _refused(error)
        return _json(
            {
                "message": f"A link to confirm {session.account.email} has been sent to it. It "
                f"works once, for {VERIFY_HOURS} hours."
            },
            202,
        )

    async def verify_email(request: Request) -> Response:
        """Confirm an address with the link that was sent to it. The page the link opens asks this:
        opening the address itself, which a mail program may do on its own, never confirms
        anything."""
        try:
            if cross_site_problem(request) is not None:
                raise AccessRefusal(CROSS_SITE, 403)
            address = await accounts.verify_email(request.path_params["token"], client_of(request))
        except AccessRefusal as error:
            return _refused(error)
        return _json({"ok": True, "email": address})

    async def request_password_reset(request: Request) -> Response:
        """Ask for a link to choose a new password. It answers the same for every address that is
        written like one, whether or not it has an account."""
        try:
            given = await fields_of(request, "email")
            await accounts.request_reset(given["email"], client_of(request))
        except AccessRefusal as error:
            return _refused(error)
        return _json({"message": RESET_REQUESTED}, 202)

    async def check_reset_link(request: Request) -> Response:
        try:
            await accounts.check_reset_link(request.path_params["token"], client_of(request))
        except AccessRefusal as error:
            return _refused(error)
        return _json({"ok": True})

    async def reset_password(request: Request) -> Response:
        try:
            given = await fields_of(request, "new")
            await accounts.reset_password(
                request.path_params["token"], given["new"], client_of(request)
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json({"ok": True})

    # -- projects --------------------------------------------------------------------------------

    async def in_project(request: Request, *, changing: bool) -> tuple[SessionInfo, ProjectInfo]:
        """The session, and the project in the address once the person is shown to be in it, with
        what they are in it (`project.role`). It is asked of the database on every request, so a
        person who was just removed is refused at once."""
        session = await signed_in(accounts, request, changing=changing)
        return session, await accounts.project_of(session.account.id, _project_number(request))

    async def list_projects(request: Request) -> Response:
        try:
            session = await signed_in(accounts, request, changing=False)
        except AccessRefusal as error:
            return _refused(error)
        found = await accounts.projects(session.account.id)
        return _json({"projects": [project.model_dump(mode="json") for project in found]})

    async def create_project(request: Request) -> Response:
        try:
            session = await signed_in(accounts, request, changing=True)
            try:
                body = await request.json()
            except ValueError:
                body = None
            name = body.get("name") if isinstance(body, dict) else None
            project = await accounts.create_project(
                session.account.id, name if isinstance(name, str) else ""
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json(project.model_dump(mode="json"), 201)

    async def show_project(request: Request) -> Response:
        try:
            _, project = await in_project(request, changing=False)
        except AccessRefusal as error:
            return _refused(error)
        return _json(project.model_dump(mode="json"))

    # -- agent keys ------------------------------------------------------------------------------
    # Only the project's owner sees or makes agent keys: Accounts refuses anyone else in words.

    async def list_keys(request: Request) -> Response:
        try:
            session, project = await in_project(request, changing=False)
            found = await accounts.keys(session.account.id, project.id)
        except AccessRefusal as error:
            return _refused(error)
        return _json({"keys": [key.model_dump(mode="json") for key in found]})

    async def create_key(request: Request) -> Response:
        try:
            session, project = await in_project(request, changing=True)
            try:
                body = await request.json()
            except ValueError:
                body = None
            name = body.get("name") if isinstance(body, dict) else None
            key, secret = await accounts.create_key(
                session.account.id,
                project.id,
                name if isinstance(name, str) else "",
                body.get("tools") if isinstance(body, dict) else None,
                known_tools=await tool_names(),
            )
        except AccessRefusal as error:
            return _refused(error)
        # The only time the key itself is ever sent: only a hash of it is kept.
        return _json({"key": key.model_dump(mode="json"), "secret": secret}, 201)

    async def change_key(request: Request) -> Response:
        """Change what a key may do: `{"tools": [names]}` for those tools, `{"tools": null}` for
        every tool. It applies from the agent's next request."""
        try:
            session, project = await in_project(request, changing=True)
            try:
                body = await request.json()
            except ValueError:
                body = None
            if not isinstance(body, dict) or "tools" not in body:
                raise AccessRefusal(
                    "Say what the key may do: tools is a list of tool names, or null for every "
                    "tool. Nothing was changed."
                )
            key = await accounts.set_key_tools(
                session.account.id,
                project.id,
                _number(request.path_params["key_id"]),
                body["tools"],
                known_tools=await tool_names(),
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json({"key": key.model_dump(mode="json")})

    async def revoke_key(request: Request) -> Response:
        try:
            session, project = await in_project(request, changing=True)
            key = await accounts.revoke_key(
                session.account.id, project.id, _number(request.path_params["key_id"])
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json({"key": key.model_dump(mode="json")})

    # -- the people of a project and their invitations -------------------------------------------
    # Anyone in a project sees who else is in it (the owner also sees their email addresses) and
    # can leave it. Everything else here is the owner's, which Accounts enforces in words.

    async def list_members(request: Request) -> Response:
        try:
            session, project = await in_project(request, changing=False)
            found = await accounts.members(session.account.id, project.id)
        except AccessRefusal as error:
            return _refused(error)
        return _json({"members": [one.model_dump(mode="json") for one in found]})

    def person_in_address(request: Request, session: SessionInfo) -> int:
        """The person a members address names: a number, or `me` for the signed-in person."""
        raw = request.path_params["person"]
        if raw == "me":
            return session.account.id
        found = _number(raw)
        if not found:
            raise AccessRefusal(NO_SUCH_MEMBER, 404)
        return found

    async def change_member(request: Request) -> Response:
        try:
            session, project = await in_project(request, changing=True)
            given = await fields_of(request, "role")
            changed = await accounts.change_role(
                session.account.id, project.id, person_in_address(request, session), given["role"]
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json({"member": changed.model_dump(mode="json")})

    async def remove_member(request: Request) -> Response:
        try:
            session, project = await in_project(request, changing=True)
            gone = await accounts.remove_person(
                session.account.id, project.id, person_in_address(request, session)
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json({"member": gone.model_dump(mode="json")})

    async def list_invites(request: Request) -> Response:
        try:
            session, project = await in_project(request, changing=False)
            found = await accounts.invites(session.account.id, project.id)
        except AccessRefusal as error:
            return _refused(error)
        return _json({"invites": [one.model_dump(mode="json") for one in found]})

    async def optional_email(request: Request) -> str | None:
        """The email address in a request's body, or None when it has none (left out or blank).
        The body has already been read and checked by `fields_of`."""
        body = await request.json()
        raw = body.get("email") if isinstance(body, dict) else None
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise AccessRefusal("The email address has to be text, like name@example.com.")
        return raw.strip() or None

    async def create_invite(request: Request) -> Response:
        try:
            session, project = await in_project(request, changing=True)
            given = await fields_of(request, "role")
            made = await accounts.create_invite(
                session.account.id, project.id, given["role"], await optional_email(request)
            )
        except AccessRefusal as error:
            return _refused(error)
        # The only time the invitation's token is ever sent: only a hash of it is kept.
        return _json(_made_body(made), 201)

    async def resend_invite(request: Request) -> Response:
        """Send an invitation that has an address again, with a new link: the old one stops."""
        try:
            session, project = await in_project(request, changing=True)
            made = await accounts.resend_invite(
                session.account.id, project.id, _number(request.path_params["invite_id"])
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json(_made_body(made))

    async def revoke_invite(request: Request) -> Response:
        try:
            session, project = await in_project(request, changing=True)
            invite = await accounts.revoke_invite(
                session.account.id, project.id, _number(request.path_params["invite_id"])
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json({"invite": invite.model_dump(mode="json")})

    async def show_invitation(request: Request) -> Response:
        """What the signed-in person would join, for the page that opens an invitation's link."""
        try:
            session = await signed_in(accounts, request, changing=False)
            preview = await accounts.preview_invite(
                session.account, request.path_params["token"], client_of(request)
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json({"invite": preview.model_dump(mode="json")})

    async def accept_invitation(request: Request) -> Response:
        try:
            session = await signed_in(accounts, request, changing=True)
            joined = await accounts.accept_invite(
                session.account, request.path_params["token"], client_of(request)
            )
        except AccessRefusal as error:
            return _refused(error)
        return _json({"project": joined.model_dump(mode="json")})

    # -- a project's graph -----------------------------------------------------------------------

    # Whether the server itself marks each tool as only reading, from its own list, asked once. It
    # is the one thing that decides what a read is: for a viewer, and for the Tools page's "Try it".
    read_only: dict[str, bool] = {}

    async def only_reads(name: str) -> bool | None:
        """True when the server marks this tool as only reading, False when it may change
        something, and None when there is no such tool."""
        if not read_only:
            for tool in await mcp.list_tools():
                read_only[tool.name] = bool(tool.annotations and tool.annotations.read_only_hint)
        return read_only.get(name)

    async def tool_names() -> frozenset[str]:
        """The names of every tool the server has, which is what a key may be allowed."""
        await only_reads("")
        return frozenset(read_only)

    async def run(project: ProjectInfo, who: str, name: str, arguments: Any) -> Response:
        """Call a tool as the person signed in, who is named `who` in everything it records. A
        viewer, who may look and not change, is refused any tool that is not a read."""
        if project.role == "viewer" and await only_reads(name) is not True:
            return _json({"message": VIEWER_CANNOT_CHANGE}, 403)
        if not isinstance(arguments, dict):
            return _json({"message": "The request has to be a JSON object of arguments."}, 400)
        try:
            with working_for(project.id), working_as(person(who)):
                result = await mcp.call_tool(name, arguments)
        except (UnexpectedToolError, NoProject, NoIdentity):
            return _json(
                {
                    "message": "Something went wrong inside the server, and the change may not "
                    "have been made. Look at the server's log (docker compose logs app)."
                },
                500,
            )
        except ToolError as error:
            return _json({"message": _plain(name, error)}, 400)
        return _json(result.structured_content or {})

    async def arguments_of(request: Request) -> Any:
        try:
            return await request.json()
        except ValueError:
            return None

    async def call_tool(request: Request) -> Response:
        name = request.path_params["name"]
        try:
            session, project = await in_project(request, changing=True)
        except AccessRefusal as error:
            return _refused(error)
        if name not in BOARD_TOOLS:
            return _json({"message": f"The board does not use a tool called {name}."}, 404)
        return await run(project, session.account.name, name, await arguments_of(request))

    async def try_tool(request: Request) -> Response:
        """The Tools page's "try it": any tool the server itself says only reads, and nothing else.
        The server's own list decides, so a tool that changes something can never be tried here."""
        name = request.path_params["name"]
        try:
            session, project = await in_project(request, changing=True)
        except AccessRefusal as error:
            return _refused(error)
        reads = await only_reads(name)
        if reads is None:
            return _json({"message": f"The server has no tool called {name}."}, 404)
        if not reads:
            return _json(
                {
                    "message": f"{name} changes something, so the board does not let you try it "
                    "here. Use the page the tool names, or an agent."
                },
                403,
            )
        return await run(project, session.account.name, name, await arguments_of(request))

    async def index(request: Request) -> Response:
        try:
            _, project = await in_project(request, changing=False)
        except AccessRefusal as error:
            return _refused(error)
        with working_for(project.id):
            note = await searcher.index_state()
        return _json(note.model_dump())

    async def tools(request: Request) -> Response:
        """The server's live tool list, with where on the board each is used."""
        try:
            await signed_in(accounts, request, changing=False)
        except AccessRefusal as error:
            return _refused(error)
        found = []
        for tool in await mcp.list_tools():
            place = TOOL_PLACES.get(tool.name)
            read_only = bool(tool.annotations and tool.annotations.read_only_hint)
            found.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.input_schema,
                    "changes_something": not read_only,
                    "area": place.area if place else None,
                    "page": place.place.page
                    if place and isinstance(place.place, OnTheBoard)
                    else None,
                    "where": place.place.where
                    if place and isinstance(place.place, OnTheBoard)
                    else None,
                    "agents_only": place.place.why
                    if place and isinstance(place.place, AgentsOnly)
                    else None,
                    "board_calls": tool.name in BOARD_TOOLS,
                    "people_only": tool.name in PEOPLE_ONLY_TOOLS,
                    "offered_to_agents": tool.name in AGENT_SURFACE,
                }
            )
        return _json(
            {
                "mcp_path": MCP_PATH,
                "areas": list(AREAS),
                "most_agent_tools": MOST_AGENT_TOOLS,
                "tools": found,
            }
        )

    async def info(request: Request) -> Response:
        try:
            await signed_in(accounts, request, changing=False)
        except AccessRefusal as error:
            return _refused(error)
        return _json(
            {
                "poll_seconds": settings.board_poll_seconds,
                "session_hours": SESSION_HOURS,
                # Whether and how this server sends email: never the user name or password.
                "mail": {
                    "enabled": settings.mail is not None,
                    "from": settings.mail.sender if settings.mail else None,
                    "host": settings.mail.host if settings.mail else None,
                },
                # The other ways of signing in: which are set up and where they lead (the host
                # only), whether a first sign-in may make an account, and which email domains are
                # allowed. Never a secret.
                "sign_in": sso.about(),
            }
        )

    # The specific routes come first: the first that matches is the one that answers.
    api = "/board/api"
    mcp.custom_route(f"{api}/accounts", methods=["POST"])(register)
    mcp.custom_route(f"{api}/session", methods=["POST"])(sign_in)
    mcp.custom_route(f"{api}/session", methods=["GET"])(whoami)
    mcp.custom_route(f"{api}/session", methods=["DELETE"])(sign_out)
    mcp.custom_route(f"{api}/account", methods=["PATCH"])(rename_account)
    mcp.custom_route(f"{api}/account/password", methods=["POST"])(change_password)
    mcp.custom_route(f"{api}/account/verify-email", methods=["POST"])(send_verification)
    mcp.custom_route(f"{api}/account/identities", methods=["GET"])(list_sign_in_methods)
    mcp.custom_route(f"{api}/account/identities/ldap", methods=["POST"])(connect_ldap)
    mcp.custom_route(f"{api}/account/identities/{{provider}}", methods=["DELETE"])(
        disconnect_provider
    )
    mcp.custom_route(f"{api}/auth/ldap", methods=["POST"])(ldap_sign_in)
    mcp.custom_route(f"{api}/auth/{{provider}}/start", methods=["POST"])(start_provider_sign_in)
    mcp.custom_route("/board/auth/{provider}/callback", methods=["GET"])(provider_callback)
    mcp.custom_route(f"{api}/verify/{{token}}", methods=["POST"])(verify_email)
    mcp.custom_route(f"{api}/password-reset", methods=["POST"])(request_password_reset)
    mcp.custom_route(f"{api}/password-reset/{{token}}", methods=["GET"])(check_reset_link)
    mcp.custom_route(f"{api}/password-reset/{{token}}", methods=["POST"])(reset_password)
    mcp.custom_route(f"{api}/projects", methods=["GET"])(list_projects)
    mcp.custom_route(f"{api}/projects", methods=["POST"])(create_project)
    mcp.custom_route(f"{api}/projects/{{project_id}}", methods=["GET"])(show_project)
    mcp.custom_route(f"{api}/projects/{{project_id}}/keys", methods=["GET"])(list_keys)
    mcp.custom_route(f"{api}/projects/{{project_id}}/keys", methods=["POST"])(create_key)
    mcp.custom_route(f"{api}/projects/{{project_id}}/keys/{{key_id}}", methods=["DELETE"])(
        revoke_key
    )
    mcp.custom_route(f"{api}/projects/{{project_id}}/keys/{{key_id}}", methods=["PATCH"])(
        change_key
    )
    mcp.custom_route(f"{api}/projects/{{project_id}}/members", methods=["GET"])(list_members)
    mcp.custom_route(f"{api}/projects/{{project_id}}/members/{{person}}", methods=["PATCH"])(
        change_member
    )
    mcp.custom_route(f"{api}/projects/{{project_id}}/members/{{person}}", methods=["DELETE"])(
        remove_member
    )
    mcp.custom_route(f"{api}/projects/{{project_id}}/invites", methods=["GET"])(list_invites)
    mcp.custom_route(f"{api}/projects/{{project_id}}/invites", methods=["POST"])(create_invite)
    mcp.custom_route(
        f"{api}/projects/{{project_id}}/invites/{{invite_id}}/resend", methods=["POST"]
    )(resend_invite)
    mcp.custom_route(f"{api}/projects/{{project_id}}/invites/{{invite_id}}", methods=["DELETE"])(
        revoke_invite
    )
    mcp.custom_route(f"{api}/invites/{{token}}", methods=["GET"])(show_invitation)
    mcp.custom_route(f"{api}/invites/{{token}}/accept", methods=["POST"])(accept_invitation)
    mcp.custom_route(f"{api}/projects/{{project_id}}/tool/{{name}}", methods=["POST"])(call_tool)
    mcp.custom_route(f"{api}/projects/{{project_id}}/try/{{name}}", methods=["POST"])(try_tool)
    mcp.custom_route(f"{api}/projects/{{project_id}}/index", methods=["GET"])(index)
    mcp.custom_route(f"{api}/tools", methods=["GET"])(tools)
    mcp.custom_route(f"{api}/info", methods=["GET"])(info)
    for path in ("/board", "/board/"):
        mcp.custom_route(path, methods=["GET"])(page)
    mcp.custom_route("/board/p/{project_id}", methods=["GET"])(page)
    mcp.custom_route("/board/invite/{token}", methods=["GET"])(page)
    mcp.custom_route("/board/reset/{token}", methods=["GET"])(page)
    mcp.custom_route("/board/verify/{token}", methods=["GET"])(page)
    mcp.custom_route("/board/{name:path}", methods=["GET"])(page)
