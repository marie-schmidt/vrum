"""A scripted GitHub for the check stack and the demo: the OAuth sign-in and the two API calls Vrum
makes, with fictional people. It is built from GitHub's documented responses (the OAuth "web
application flow" of docs.github.com/apps/oauth-apps and the REST endpoints GET /user and GET
/user/emails), including the ones that surprise a careless client: an error from the token endpoint
is an HTTP 200 with an `error` in the body, an answer is form encoded unless the request says
`Accept: application/json`, an API call without a User-Agent is refused, and /user/emails is "not
found" for a token that was not given the `user:email` scope. It is not GitHub: the live GitHub
needs an OAuth App that only its owner can register, and trying it is left to a person. Nothing in
the product knows this file exists. Standard library only.

Run as `python scripted_github.py`. Settings, from the environment:
  PORT           the port it listens on (default 8080)
  CLIENT_ID      the one OAuth App it knows (default vrum-github-check)
  CLIENT_SECRET  its secret (default scripted-github-secret)
  REDIRECT_URIS  the callback addresses it accepts, exactly, comma separated (empty: any)

What it serves (the API is under /api/v3, as on GitHub Enterprise Server):
  GET  /login/oauth/authorize      a page of fictional people as buttons; a button sends the browser
                                   back to the callback with a code and the state. With
                                   `person=<key>` in the query it does that at once.
  POST /login/oauth/access_token   the code exchange (client id and secret in the form, redirect
                                   address, and the PKCE verifier when a challenge was sent)
  GET  /api/v3/user                the signed-in person: login, numeric id, name, public email
  GET  /api/v3/user/emails         every address of the person, with `primary` and `verified`
  GET  /_people, POST /_people     the fictional people, and add or replace one (below)
  GET  /_log, DELETE /_log         what was asked of it (`?since=<id>` for later entries)
  GET  /_control, POST /_control   make things misbehave, or reset (below)
  GET  /health

A person is {key, id, login, name?, email?, emails: [{email, verified, primary, visibility?}]}:
`email` is the public profile address that /user shows, which can be any address and is not proof
of anything; `emails` is what /user/emails lists. The numeric `id` is what never changes, and
`login` can be renamed by posting the same key again.

POST /_control takes a JSON object with any of:
  token       what the NEXT code exchange does wrong: "error" (HTTP 200 with
              bad_verification_code), "no_access_token", "endpoint_500", "endpoint_slow",
              "endpoint_redirect" or "endpoint_huge"; or "ok"
  authorize   what the NEXT approval at the authorize page does instead: "access_denied",
              "server_error" (with an error description that holds markup) or "wrong_state"
  user        "down" (HTTP 500), "unauthorized" (401 Bad credentials) or "ok", for /user
  emails      "down", "unauthorized", "not_found" or "ok", for /user/emails
  rate_limited  true makes both answer 403 as GitHub does when the rate limit is used up
  seconds     how long "endpoint_slow" waits (default 15)
  reset       true forgets every control and the log (the people stay)
"""

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

LONGEST_BODY = 1024 * 1024
CODE_LIFETIME = 600
DOCUMENTATION = "https://docs.github.com/rest"
TOKEN_MODES = (
    "ok",
    "error",
    "no_access_token",
    "endpoint_500",
    "endpoint_slow",
    "endpoint_redirect",
    "endpoint_huge",
)
AUTHORIZE_MODES = ("ok", "access_denied", "server_error", "wrong_state")
API_MODES = ("ok", "down", "unauthorized")


def email(address: str, *, verified: bool = True, primary: bool = False) -> dict[str, Any]:
    return {"email": address, "verified": verified, "primary": primary, "visibility": None}


# People of both the checks and the demo. The last three are the demo's: an address that already
# has a confirmed account there, one whose account was never confirmed, and one who is new.
DEFAULT_PEOPLE: list[dict[str, Any]] = [
    {
        "key": "gwen",
        "id": 1001,
        "login": "gwen-archer",
        "name": "Gwen Archer",
        "email": "gwen.archer@example.com",
        "emails": [
            email("gwen.archer@example.com", primary=True),
            email("gwen@workshop.example.net"),
            email("gwen.old@example.org", verified=False),
        ],
    },
    {
        "key": "hugo",
        "id": 1002,
        "login": "hugo-h",
        "name": None,
        "email": None,
        "emails": [email("hugo.hale@example.com", primary=True)],
    },
    {
        "key": "ines",
        "id": 1003,
        "login": "ines-p",
        "name": "Ines Park (primary address not confirmed)",
        "email": "ines.park@example.com",
        "emails": [
            email("ines.park@example.com", verified=False, primary=True),
            email("ines@elsewhere.example.net"),
        ],
    },
    {
        "key": "jonas",
        "id": 1004,
        "login": "jonas-k",
        "name": "Jonas Keller (no confirmed address)",
        "email": None,
        "emails": [email("jonas.keller@example.com", verified=False, primary=True)],
    },
    {
        "key": "kira",
        "id": 1005,
        "login": "kira-v",
        "name": "Kira Vance (no address at all)",
        "email": None,
        "emails": [],
    },
    {
        "key": "mallory",
        "id": 1006,
        "login": "mallory-x",
        "name": "Mallory X (public address is someone else's)",
        "email": "ava.stone@example.com",
        "emails": [
            email("mallory@example.net", primary=True),
            email("ava.stone@example.com", verified=False),
        ],
    },
    {
        "key": "priya",
        "id": 2001,
        "login": "priya-nair",
        "name": "Priya Nair",
        "email": None,
        "emails": [email("priya@example.com", primary=True)],
    },
    {
        "key": "tom",
        "id": 2002,
        "login": "tom-berg",
        "name": "Tom Berg",
        "email": None,
        "emails": [email("tom@example.com", primary=True)],
    },
    {
        "key": "noor",
        "id": 2003,
        "login": "noor-haddad",
        "name": "Noor Haddad",
        "email": None,
        "emails": [email("noor@example.com", primary=True)],
    },
]


def user_document(person: dict[str, Any], api: str) -> dict[str, Any]:
    """GET /user as GitHub documents it (the fields Vrum could use and some it must not)."""
    login = person["login"]
    return {
        "login": login,
        "id": person["id"],
        "node_id": base64.b64encode(f"04:User{person['id']}".encode()).decode(),
        "avatar_url": f"https://avatars.example.invalid/u/{person['id']}",
        "gravatar_id": "",
        "url": f"{api}/users/{login}",
        "html_url": f"https://github.example.invalid/{login}",
        "type": "User",
        "site_admin": False,
        "name": person.get("name"),
        "company": None,
        "blog": "",
        "location": None,
        "email": person.get("email"),
        "hireable": None,
        "bio": None,
        "public_repos": 3,
        "followers": 1,
        "following": 0,
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


class Provider:
    def __init__(self, environment: dict[str, str]) -> None:
        self.port = int(environment.get("PORT", "8080"))
        self.client_id = environment.get("CLIENT_ID", "vrum-github-check")
        self.client_secret = environment.get("CLIENT_SECRET", "scripted-github-secret")
        self.redirect_uris = [
            one.strip() for one in environment.get("REDIRECT_URIS", "").split(",") if one.strip()
        ]
        self.lock = threading.Lock()
        self.people = {one["key"]: dict(one) for one in DEFAULT_PEOPLE}
        self.codes: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, dict[str, Any]] = {}
        self.log: list[dict[str, Any]] = []
        self.reset_controls()

    def reset_controls(self) -> None:
        self.next_token = "ok"
        self.next_authorize = "ok"
        self.user_state = "ok"
        self.emails_state = "ok"
        self.rate_limited = False
        self.slow_seconds = 15.0

    def record(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.log.append(
                {
                    "id": len(self.log) + 1,
                    "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                    **entry,
                }
            )

    def control(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("reset"):
            self.reset_controls()
            with self.lock:
                self.log.clear()
                self.codes.clear()
                self.tokens.clear()
        wanted = (
            ("token", "next_token", TOKEN_MODES),
            ("authorize", "next_authorize", AUTHORIZE_MODES),
            ("user", "user_state", API_MODES),
            ("emails", "emails_state", (*API_MODES, "not_found")),
        )
        for name, attribute, allowed in wanted:
            if name in body:
                if body[name] not in allowed:
                    return {"error": f"{name} must be one of {list(allowed)}"}
                setattr(self, attribute, body[name])
        if "rate_limited" in body:
            self.rate_limited = bool(body["rate_limited"])
        if "seconds" in body:
            self.slow_seconds = float(body["seconds"])
        return self.state()

    def state(self) -> dict[str, Any]:
        return {
            "token": self.next_token,
            "authorize": self.next_authorize,
            "user": self.user_state,
            "emails": self.emails_state,
            "rate_limited": self.rate_limited,
            "seconds": self.slow_seconds,
        }

    def add_person(self, body: dict[str, Any]) -> dict[str, Any] | None:
        key = body.get("key")
        if not isinstance(key, str) or not key or not isinstance(body.get("id"), int):
            return None
        person = {"login": key, "name": None, "email": None, "emails": [], **body}
        with self.lock:
            self.people[key] = person
        return person

    # -- authorize ---------------------------------------------------------------------------------

    def check_authorize(self, params: dict[str, str]) -> str | None:
        if params.get("client_id") != self.client_id:
            return "The client_id and/or client_secret passed are incorrect."
        if self.redirect_uris and params.get("redirect_uri") not in self.redirect_uris:
            return "The redirect_uri MUST match the registered callback URL for this application."
        if not params.get("redirect_uri"):
            return "There is no redirect_uri."
        return None

    @staticmethod
    def back(params: dict[str, str], **answer: str) -> str:
        sent = {name: value for name, value in answer.items() if value}
        if params.get("state") and "state" not in sent:
            sent["state"] = params["state"]
        address = params["redirect_uri"]
        return address + ("&" if "?" in address else "?") + urlencode(sent)

    def approve(self, params: dict[str, str], person_key: str) -> tuple[str, str]:
        mode, self.next_authorize = self.next_authorize, "ok"
        if mode == "access_denied":
            return self.back(
                params,
                error="access_denied",
                error_description="The user has denied your application access.",
                error_uri="https://docs.github.com/apps/oauth-apps",
            ), "denied"
        if mode == "server_error":
            noted = "<script>alert('from github')</script> something went wrong"
            return self.back(params, error="server_error", error_description=noted), "error"
        code = secrets.token_hex(10)
        with self.lock:
            self.codes[code] = {
                "person": person_key,
                "redirect_uri": params["redirect_uri"],
                "scope": params.get("scope", ""),
                "challenge": params.get("code_challenge"),
                "at": time.time(),
                "used": False,
            }
        state = "forged-state" if mode == "wrong_state" else ""
        return self.back(params, code=code, state=state), "approved"

    # -- token -------------------------------------------------------------------------------------

    def exchange(self, form: dict[str, str]) -> dict[str, Any]:
        """The answer to a code exchange: status, body, result, and the delay or redirect."""
        mode, self.next_token = self.next_token, "ok"
        wrong = {
            "error": "bad_verification_code",
            "error_description": "The code passed is incorrect or expired.",
            "error_uri": "https://docs.github.com/apps/oauth-apps/troubleshooting-oauth-app-access-token-request-errors",
        }
        secret = form.get("client_secret", "")
        if form.get("client_id") != self.client_id or not hmac.compare_digest(
            secret.encode(), self.client_secret.encode()
        ):
            body = {
                "error": "incorrect_client_credentials",
                "error_description": "The client_id and/or client_secret passed are incorrect.",
                "error_uri": "https://docs.github.com/apps/oauth-apps",
            }
            return {"status": 200, "body": body, "result": "incorrect_client_credentials"}
        with self.lock:
            issued = self.codes.get(form.get("code", ""))
            replay = bool(issued and issued["used"])
            if issued:
                issued["used"] = True
        if not issued or replay or time.time() - issued["at"] > CODE_LIFETIME:
            return {"status": 200, "body": wrong, "result": "bad_verification_code"}
        if form.get("redirect_uri") and form["redirect_uri"] != issued["redirect_uri"]:
            body = {
                "error": "redirect_uri_mismatch",
                "error_description": "The redirect_uri MUST match the registered callback URL.",
            }
            return {"status": 200, "body": body, "result": "redirect_uri_mismatch"}
        if issued["challenge"]:
            verifier = form.get("code_verifier", "")
            made = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            if not verifier or not hmac.compare_digest(
                made.rstrip(b"=").decode(), issued["challenge"]
            ):
                return {"status": 200, "body": wrong, "result": "bad_verification_code (PKCE)"}
        if mode == "error":
            return {"status": 200, "body": wrong, "result": "bad_verification_code (scripted)"}
        if mode == "endpoint_500":
            return {"status": 500, "body": {"message": "Server Error"}, "result": "500"}
        if mode == "endpoint_redirect":
            return {"status": 302, "body": {}, "redirect": "/elsewhere", "result": "redirect"}
        if mode == "no_access_token":
            return {"status": 200, "body": {"token_type": "bearer"}, "result": "no access token"}
        token = "gho_" + secrets.token_hex(18)
        scope = ",".join(issued["scope"].replace(",", " ").split())
        with self.lock:
            self.tokens[token] = {"person": issued["person"], "scope": scope}
        body = {"access_token": token, "scope": scope, "token_type": "bearer"}
        if mode == "endpoint_huge":
            body["padding"] = "x" * (2 * LONGEST_BODY)
        delay = self.slow_seconds if mode == "endpoint_slow" else 0.0
        return {"status": 200, "body": body, "delay": delay, "result": f"issued ({mode})"}

    # -- the API -----------------------------------------------------------------------------------

    def api(self, path: str, headers: Any, api_root: str) -> tuple[int, Any, dict[str, str]]:
        """The answer to GET /user or GET /user/emails: status, body and extra headers."""
        if not headers.get("User-Agent"):
            message = (
                "Request forbidden by administrative rules. Please make sure your request has a "
                "User-Agent header (https://docs.github.com/en/rest/overview/resources-in-the-rest-api#user-agent-required)."
            )
            return 403, {"message": message}, {}
        if self.rate_limited:
            body = {
                "message": "API rate limit exceeded for 127.0.0.1.",
                "documentation_url": DOCUMENTATION,
            }
            return 403, body, {"X-RateLimit-Remaining": "0"}
        authorization = headers.get("Authorization", "")
        scheme, _, given = authorization.partition(" ")
        token = self.tokens.get(given) if scheme.lower() in ("bearer", "token") else None
        if not authorization:
            return (
                401,
                {"message": "Requires authentication", "documentation_url": DOCUMENTATION},
                {},
            )
        state = self.user_state if path.endswith("/user") else self.emails_state
        if token is None or state == "unauthorized":
            return 401, {"message": "Bad credentials", "documentation_url": DOCUMENTATION}, {}
        if state == "down":
            return 500, {"message": "Server Error"}, {}
        person = self.people[token["person"]]
        scopes = {"X-OAuth-Scopes": token["scope"].replace(",", ", ")}
        if path.endswith("/user"):
            return 200, user_document(person, api_root), scopes
        if state == "not_found" or "user:email" not in token["scope"].split(","):
            body = {"message": "Not Found", "documentation_url": DOCUMENTATION}
            return 404, body, scopes
        return 200, person["emails"], scopes


# -- the web side ----------------------------------------------------------------------------------


def _page(title: str, body: str) -> bytes:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>"
        "body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:2rem auto;padding:0 1rem}"
        "button{display:block;width:100%;text-align:left;font:inherit;padding:.6rem .8rem;"
        "margin:.5rem 0;border:1px solid #888;border-radius:8px;background:#fff;cursor:pointer}"
        "button:hover,button:focus-visible{background:#eef}small{color:#555}"
        f"</style></head><body>{body}</body></html>"
    ).encode()


def authorize_page(provider: Provider, params: dict[str, str]) -> bytes:
    fields = "".join(
        f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(value)}">'
        for name, value in params.items()
    )
    buttons = "".join(
        f'<button type="submit" name="person" value="{html.escape(key)}" '
        f'data-person="{html.escape(key)}">{html.escape(str(one.get("name") or one["login"]))}'
        f"<br><small>{html.escape(str(one['login']))}</small></button>"
        for key, one in provider.people.items()
    )
    return _page(
        "Scripted GitHub: authorize",
        "<h1>Scripted GitHub</h1><p>Choose who you are. This is a stand-in for GitHub for tests "
        "and the demo: nothing real is contacted, and there is no password. The application "
        f"asks for: <b>{html.escape(params.get('scope') or 'nothing')}</b>.</p>"
        f'<form method="post" action="/login/oauth/authorize">{fields}{buttons}'
        '<button type="submit" name="cancel" value="1" data-person="cancel">Cancel</button></form>',
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    provider: Provider

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def _send(
        self,
        status: int,
        body: bytes,
        kind: str = "application/json; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _json(self, status: int, value: Any, headers: dict[str, str] | None = None) -> None:
        self._send(status, json.dumps(value).encode(), headers=headers)

    def _body(self) -> bytes:
        return self.rfile.read(min(int(self.headers.get("Content-Length") or 0), LONGEST_BODY))

    def _form(self) -> dict[str, str]:
        return dict(parse_qsl(self._body().decode("utf-8", "replace"), keep_blank_values=True))

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def do_DELETE(self) -> None:
        self._route("DELETE")

    def _route(self, method: str) -> None:
        provider = self.provider
        url = urlsplit(self.path)
        query = {name: values[-1] for name, values in parse_qs(url.query).items()}
        path = url.path
        if path == "/health" and method == "GET":
            self._json(200, {"status": "ok"})
        elif path.startswith("/_"):
            self._control(method, path, query)
        elif path == "/login/oauth/authorize" and method in ("GET", "POST"):
            self._authorize(method, query)
        elif path == "/login/oauth/access_token" and method == "POST":
            self._token()
        elif path in ("/api/v3/user", "/api/v3/user/emails") and method == "GET":
            self._api(path)
        elif path == "/elsewhere":
            provider.record(
                {"method": method, "path": path, "result": "somebody followed a redirect"}
            )
            self._json(200, {"followed": True})
        elif path == "/" and method == "GET":
            names = ", ".join(html.escape(str(one["login"])) for one in provider.people.values())
            self._send(
                200,
                _page(
                    "Scripted GitHub",
                    f"<h1>Scripted GitHub</h1><p>A stand-in for tests and the demo. People: "
                    f"{names}</p>",
                ),
                "text/html; charset=utf-8",
            )
        else:
            self._json(404, {"message": "Not Found", "documentation_url": DOCUMENTATION})

    def _authorize(self, method: str, query: dict[str, str]) -> None:
        provider = self.provider
        params = {**query, **(self._form() if method == "POST" else {})}
        shown = {name: value for name, value in params.items() if name not in ("person", "cancel")}
        problem = provider.check_authorize(shown)
        entry: dict[str, Any] = {"method": method, "path": "/login/oauth/authorize", "query": shown}
        if problem:
            provider.record({**entry, "result": problem})
            page = _page("Cannot sign in", f"<h1>Cannot sign in</h1><p>{html.escape(problem)}</p>")
            self._send(404, page, "text/html; charset=utf-8")
        elif params.get("cancel"):
            where = provider.back(
                shown,
                error="access_denied",
                error_description="The user has denied your application access.",
            )
            provider.record({**entry, "result": "cancelled"})
            self._send(303, b"", headers={"Location": where})
        elif params.get("person") in provider.people:
            where, result = provider.approve(shown, params["person"])
            provider.record(
                {**entry, "person": params["person"], "result": result, "redirect": where}
            )
            self._send(303, b"", headers={"Location": where})
        else:
            provider.record({**entry, "result": "page"})
            self._send(200, authorize_page(provider, shown), "text/html; charset=utf-8")

    def _token(self) -> None:
        provider = self.provider
        form = self._form()
        answer = provider.exchange(form)
        shown = {name: value for name, value in form.items() if name != "client_secret"}
        provider.record(
            {
                "method": "POST",
                "path": "/login/oauth/access_token",
                "form": shown,
                "accept": self.headers.get("Accept", ""),
                "status": answer["status"],
                "result": answer["result"],
                # What was handed out, so that a check can look for it where it must never be.
                "issued": {
                    name: answer["body"][name]
                    for name in ("access_token",)
                    if name in answer["body"]
                },
            }
        )
        if answer.get("delay"):
            time.sleep(answer["delay"])
        headers = {"Location": answer["redirect"]} if answer.get("redirect") else None
        if "json" in self.headers.get("Accept", ""):
            self._json(answer["status"], answer["body"], headers)
        else:
            plain = urlencode({name: str(value) for name, value in answer["body"].items()})
            self._send(
                answer["status"],
                plain.encode(),
                "application/x-www-form-urlencoded; charset=utf-8",
                headers,
            )

    def _api(self, path: str) -> None:
        provider = self.provider
        api_root = f"http://{self.headers.get('Host', 'localhost')}/api/v3"
        status, body, headers = provider.api(path, self.headers, api_root)
        provider.record(
            {
                "method": "GET",
                "path": path,
                "user_agent": self.headers.get("User-Agent", ""),
                "authorization": (self.headers.get("Authorization", "").split(" ")[0:1] or [""])[
                    0
                ].lower(),
                "status": status,
            }
        )
        self._json(status, body, headers)

    def _control(self, method: str, path: str, query: dict[str, str]) -> None:
        provider = self.provider
        if path == "/_log" and method == "GET":
            since = int(query.get("since", "0"))
            self._json(200, [one for one in list(provider.log) if one["id"] > since])
        elif path == "/_log" and method == "DELETE":
            with provider.lock:
                provider.log.clear()
            self._json(200, {"deleted": True})
        elif path == "/_people" and method == "GET":
            self._json(200, list(provider.people.values()))
        elif path in ("/_people", "/_control") and method == "POST":
            try:
                body = json.loads(self._body() or b"{}")
            except ValueError:
                body = None
            if not isinstance(body, dict):
                self._json(400, {"error": "send a JSON object"})
            elif path == "/_people":
                person = provider.add_person(body)
                self._json(
                    201 if person else 400,
                    person or {"error": "a person needs a key and a numeric id"},
                )
            else:
                answer = provider.control(body)
                self._json(400 if "error" in answer else 200, answer)
        elif path == "/_control" and method == "GET":
            self._json(200, provider.state())
        else:
            self._json(404, {"error": "not found"})


def serve(provider: Provider) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"provider": provider})
    server = ThreadingHTTPServer(("0.0.0.0", provider.port), handler)
    server.daemon_threads = True
    return server


if __name__ == "__main__":
    serve(Provider(dict(os.environ))).serve_forever()
