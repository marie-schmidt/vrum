"""The people, projects and agent keys the integration tests work in. They are made through the real
HTTP API of the stack, exactly as a person on the board would make them, and nothing is faked: an
account is registered, signs in, creates a project and makes an agent key for it.

Every test that only needs a project to work in shares one (`the_project`), made the first time it
is asked for. A test about accounts or about the walls between projects makes its own people and
projects, so that it cannot disturb the others."""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.helpers import unique_word

SESSION_COOKIE = "vrum_session"

# The name of the agent key every project of the harness is made with (`the_token()`,
# `project.key`): who the history says made what a test does through that key.
HARNESS_AGENT = "tests"


@dataclass
class Reply:
    status: int
    body: Any
    headers: Any

    @property
    def cookie(self) -> str | None:
        """The session cookie's value, when the server set one."""
        for line in self.headers.get_all("Set-Cookie") or []:
            name, _, rest = line.partition("=")
            if name == SESSION_COOKIE:
                return str(rest.partition(";")[0])
        return None


def server_base(mcp_url: str | None = None) -> str:
    """The address of the server, from its MCP endpoint (by default the check stack's)."""
    url = mcp_url or os.environ.get("VRUM_TEST_URL")
    if not url:
        pytest.fail("VRUM_TEST_URL is not set. Run the integration tests through ./check.sh.")
    return url.removesuffix("/mcp")


def api(
    base: str,
    method: str,
    path: str,
    body: Any = None,
    *,
    cookie: str | None = None,
    csrf: str | None = None,
    origin: str | None = "same",
    extra: dict[str, str] | None = None,
) -> Reply:
    """One request to the board's API, as the board's page makes it. `origin` is what the browser
    would send: the server's own address by default, none at all with None, or another site's."""
    headers = {"Content-Type": "application/json", **(extra or {})}
    if cookie:
        headers["Cookie"] = f"{SESSION_COOKIE}={cookie}"
    if csrf:
        headers["X-Vrum-CSRF"] = csrf
    if origin:
        headers["Origin"] = base if origin == "same" else origin
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return Reply(response.status, json.loads(response.read() or b"null"), response.headers)
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            parsed = json.loads(raw or b"null")
        except ValueError:
            parsed = raw.decode(errors="replace")
        return Reply(error.code, parsed, error.headers)


@dataclass
class Person:
    """An account, signed in."""

    base: str
    email: str
    password: str
    name: str
    cookie: str
    csrf: str
    id: int = 0

    def call(self, method: str, path: str, body: Any = None, **more: Any) -> Reply:
        return api(self.base, method, path, body, cookie=self.cookie, csrf=self.csrf, **more)

    def sign_in_again(self) -> "Person":
        """A new session for the same account, so that a test that signs out cannot end the
        session another test is using."""
        reply = api(
            self.base,
            "POST",
            "/board/api/session",
            {"email": self.email, "password": self.password},
        )
        assert reply.status == 200 and reply.cookie, reply.body
        return Person(
            self.base,
            self.email,
            self.password,
            self.name,
            reply.cookie,
            reply.body["csrf"],
            self.id,
        )


@dataclass
class Project:
    id: int
    name: str
    key: str
    owner: Person
    base: str
    keys: list[str] = field(default_factory=list)

    @property
    def mcp_url(self) -> str:
        return f"{self.base}/mcp"

    @property
    def address(self) -> str:
        """The project's board, as an address."""
        return f"{self.base}/board/p/{self.id}"


_projects: dict[tuple[str, int], Project] = {}
_shared: Project | None = None


def register(base: str | None = None, name: str | None = None) -> Person:
    """A new account with an address and a password of its own, registered and signed in."""
    return register_as(f"{unique_word()}@example.test", base, name)


def register_as(email: str, base: str | None = None, name: str | None = None) -> Person:
    """A new account with this address (and a password and name of its own), registered and signed
    in, as a person registers on the board."""
    base = base or server_base()
    word = unique_word()
    shown = name or f"Tester {word}"
    password = f"correct-horse-{word}"
    reply = api(
        base, "POST", "/board/api/accounts", {"email": email, "name": shown, "password": password}
    )
    assert reply.status == 201 and reply.cookie, f"registration was refused: {reply.body}"
    return Person(
        base, email, password, shown, reply.cookie, reply.body["csrf"], reply.body["account"]["id"]
    )


_all_tools: dict[str, list[str]] = {}


def every_tool_a_key_can_have(owner: Person) -> list[str]:
    """Every tool the server has except the ones only a person can use. The keys the test harness
    makes are given all of them, as a key made for administration is, so that a test is not limited
    to the tools agents are offered; a test about what an agent is offered makes a standard key
    with `make_limited_key(project, name, None)`."""
    if owner.base not in _all_tools:
        reply = owner.call("GET", "/board/api/tools")
        assert reply.status == 200, f"the tool list was refused: {reply.body}"
        _all_tools[owner.base] = sorted(
            tool["name"] for tool in reply.body["tools"] if not tool["people_only"]
        )
    return _all_tools[owner.base]


def make_project(owner: Person, name: str | None = None) -> Project:
    """A project of this person's, with an agent key for it (one that may use every tool)."""
    reply = owner.call("POST", "/board/api/projects", {"name": name or f"Project {unique_word()}"})
    assert reply.status == 201, f"the project was refused: {reply.body}"
    made = reply.body
    key = owner.call(
        "POST",
        f"/board/api/projects/{made['id']}/keys",
        {"name": HARNESS_AGENT, "tools": every_tool_a_key_can_have(owner)},
    )
    assert key.status == 201, f"the key was refused: {key.body}"
    project = Project(
        made["id"], made["name"], key.body["secret"], owner, owner.base, [key.body["secret"]]
    )
    _projects[(owner.base, project.id)] = project
    return project


def make_key(project: Project, name: str) -> str:
    """Another agent key for the project, named for the agent that will use it (the name is who
    the agent is in the history), made the way its owner makes one, and allowed every tool.
    Returns the key itself."""
    reply = project.owner.call(
        "POST",
        f"/board/api/projects/{project.id}/keys",
        {"name": name, "tools": every_tool_a_key_can_have(project.owner)},
    )
    assert reply.status == 201, f"the key was refused: {reply.body}"
    project.keys.append(reply.body["secret"])
    return str(reply.body["secret"])


def make_limited_key(project: Project, name: str, tools: list[str] | None) -> str:
    """An agent key that may use only the tools named (the tools Vrum offers agents when `tools`
    is None), made the way its owner makes one on the Users page. Returns the key itself."""
    reply = project.owner.call(
        "POST", f"/board/api/projects/{project.id}/keys", {"name": name, "tools": tools}
    )
    assert reply.status == 201, f"the key was refused: {reply.body}"
    project.keys.append(reply.body["secret"])
    return str(reply.body["secret"])


def join_project(project: Project, role: str, name: str | None = None) -> Person:
    """A new person who joins the project as an editor or a viewer the way a person does: its owner
    makes an invitation, the person registers (with `name`, or one of their own) and accepts it."""
    made = project.owner.call("POST", f"/board/api/projects/{project.id}/invites", {"role": role})
    assert made.status == 201, f"the invitation was refused: {made.body}"
    person = register(project.base, name)
    accepted = person.call("POST", f"/board/api/invites/{made.body['token']}/accept")
    assert accepted.status == 200, f"the invitation was not accepted: {accepted.body}"
    return person


def the_project() -> Project:
    """The project almost every test works in, made once for this run, on the check stack."""
    global _shared
    if _shared is None:
        base = server_base()
        _shared = make_project(register(base, "Shared tester"), "Shared test project")
    return _shared


def project_at(board_address: str) -> Project:
    """The project whose board has this address (/board/p/12), which this run made."""
    base, _, rest = board_address.partition("/board/p/")
    number = int(rest.split("#")[0].split("/")[0])
    found = _projects.get((base, number))
    if found is None:
        pytest.fail(f"This run did not make the project at {board_address}.")
    return found
