"""Who did it, verified (M22 slice 1), through a real MCP client and the board's own HTTP API,
against the real containers.

The identity of a caller comes from the credential and from nothing it sends: a call over the
network is the agent its key is named for, and a call the board makes is the person signed in. What
has to hold: two keys named differently stamp their own names and kinds on claims, events and
notes, and cannot touch each other's claims; a name sent in an argument changes nothing; only a
person can confirm; key names are unique among the keys in force; a revoked key stops at once; a
person can rename their account and change their password; and an export keeps who did what, as
what (an older export loading with the kind `unknown` is proved in test_import_old_export.py)."""

import copy
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import pytest
from mcp import Client

from tests.helpers import (
    confirm_on_board,
    connect,
    connect_as,
    export_everything,
    ok,
    refused,
    run_sql,
)
from tests.world import Person, Project, Reply, api, make_key, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

A_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2


@asynccontextmanager
async def agents(project: Project, *names: str) -> AsyncIterator[list[Client]]:
    """One connected client for each name: a key of that name for the project, and its agent."""
    async with AsyncExitStack() as stack:
        yield [await stack.enter_async_context(connect_as(name, project)) for name in names]


def board(signed_in: Person, project: Project, tool: str, **arguments: Any) -> Reply:
    """What the board's page does when its person presses a button: the tool, as that person."""
    return signed_in.call("POST", f"/board/api/projects/{project.id}/tool/{tool}", arguments)


def board_ok(signed_in: Person, project: Project, tool: str, **arguments: Any) -> dict[str, Any]:
    reply = board(signed_in, project, tool, **arguments)
    assert reply.status == 200, f"{tool} was refused: {reply.body}"
    return dict(reply.body)


def keys_path(project: Project) -> str:
    return f"/board/api/projects/{project.id}/keys"


def who(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(event["actor"], event["actor_kind"]) for event in events]


def messages(error: BaseException) -> str:
    """Everything an exception, or the exceptions grouped inside it, says."""
    inner = getattr(error, "exceptions", None)
    return " ".join([str(error)] + [messages(one) for one in inner or []])


# -- the identity comes from the credential --------------------------------------------------------


async def test_two_keys_stamp_their_own_names_and_kinds_and_cannot_touch_each_others_claims() -> (
    None
):
    lena = register(name="Lena Okafor")
    project = make_project(lena)
    async with agents(project, "api-agent", "mobile-agent", "Lena Okafor") as (api_a, mobile, same):
        epic = await ok(api_a, "create_node", kind="epic", title="Who did it")
        story = await ok(api_a, "create_node", kind="story", title="Stamped", parent=epic["ref"])
        task = await ok(mobile, "create_node", kind="task", title="Other", parent=story["ref"])
        assert who((await ok(api_a, "node_history", node=epic["ref"]))["items"]) == [
            ("api-agent", "agent")
        ]
        assert who((await ok(mobile, "node_history", node=task["ref"]))["items"]) == [
            ("mobile-agent", "agent")
        ]

        # A claim is held by one identity, and only that identity may renew or release it.
        claim = await ok(api_a, "claim_node", node=story["ref"])
        assert (claim["actor"], claim["actor_kind"]) == ("api-agent", "agent")
        held = await ok(api_a, "get_node", node=story["ref"])
        assert (held["claimed_by"], held["claimed_by_kind"]) == ("api-agent", "agent")
        for tool, extra in (("renew_claim", {}), ("release_claim", {"note": "mine now"})):
            assert "claimed by api-agent, not by you" in await refused(
                mobile, tool, node=story["ref"], **extra
            )
        assert "claimed by api-agent" in await refused(mobile, "claim_node", node=story["ref"])
        assert (await ok(api_a, "renew_claim", node=story["ref"]))["actor"] == "api-agent"

        # A person is a different identity even with the same name as an agent.
        person_claim = board_ok(lena, project, "claim_node", node=task["ref"])
        assert (person_claim["actor"], person_claim["actor_kind"]) == ("Lena Okafor", "person")
        assert "claimed by Lena Okafor, not by you" in await refused(
            same, "renew_claim", node=task["ref"]
        )
        assert board_ok(lena, project, "renew_claim", node=task["ref"])["actor"] == "Lena Okafor"
        again = await ok(same, "create_node", kind="story", title="Namesake", parent=epic["ref"])
        await ok(same, "claim_node", node=again["ref"])

        working = {
            (entry["actor"], entry["kind"]): entry["state"]
            for entry in (await ok(api_a, "who_is_working"))["agents"]
        }
        assert working == {
            ("api-agent", "agent"): "working",
            ("Lena Okafor", "person"): "working",
            ("Lena Okafor", "agent"): "working",
        }

        # Taking work and finishing it leave a handoff from the agent that did it.
        spare = await ok(mobile, "create_node", kind="story", title="Spare", parent=epic["ref"])
        taken = await ok(mobile, "next_work", within_node=epic["ref"])
        assert taken["work"]["ref"] == spare["ref"]
        assert (taken["claim"]["actor"], taken["claim"]["actor_kind"]) == ("mobile-agent", "agent")
        done = await ok(
            api_a, "finish_work", node=story["ref"], outcome="done", note="All stamped."
        )
        assert (done["handoff"]["actor"], done["handoff"]["actor_kind"]) == ("api-agent", "agent")
        detail = await ok(api_a, "get_node", node=story["ref"])
        assert detail["last_handoff"]["actor_kind"] == "agent"

        # Notes carry who recorded them and as what, in every way of reading them.
        by_agent = await ok(api_a, "add_note", text="Agents are named", why="The design")
        by_person = board_ok(lena, project, "add_note", text="People sign in", why="Lena")
        assert (by_agent["recorded_by"], by_agent["recorded_by_kind"]) == ("api-agent", "agent")
        assert (by_person["recorded_by"], by_person["recorded_by_kind"]) == (
            "Lena Okafor",
            "person",
        )
        listed = {note["id"]: note for note in (await ok(mobile, "list_notes"))["items"]}
        assert listed[by_agent["id"]]["recorded_by_kind"] == "agent"
        assert listed[by_person["id"]]["recorded_by_kind"] == "person"
        recalled = await ok(mobile, "get_note", note_id=by_person["id"])
        assert recalled["recorded_by_kind"] == "person"
        assert who((await ok(mobile, "note_history", note_id=by_agent["id"]))["items"]) == [
            ("api-agent", "agent")
        ]

        # The feed can be read by name, and every event of it says what made it.
        feed = (await ok(mobile, "changes_since", actor="api-agent", limit=200))["items"]
        assert feed and {event["actor"] for event in feed} == {"api-agent"}
        assert {event["actor_kind"] for event in feed} == {"agent"}
        everyone = (await ok(mobile, "changes_since", limit=200))["items"]
        assert {("Lena Okafor", "person"), ("mobile-agent", "agent")} <= set(who(everyone))


async def test_a_name_sent_in_an_argument_changes_nothing_and_the_tools_do_not_ask_for_one() -> (
    None
):
    project = make_project(register())
    async with agents(project, "api-agent") as (agent,):
        tools = (await agent.list_tools()).tools
        asked = {t.name for t in tools if "actor" in (t.input_schema.get("properties") or {})}
        # Only the feed has one, to read the changes of one name: it is never who is writing.
        assert asked == {"changes_since"}
        assert {t.name for t in tools} >= {
            "create_node",
            "claim_node",
            "add_note",
            "import_graph",
        }

        node = await ok(
            agent, "create_node", actor="Somebody Else", kind="epic", title="Sent a name"
        )
        note = await ok(agent, "add_note", actor="Somebody Else", text="A note", why="Here")
        assert who((await ok(agent, "node_history", node=node["ref"]))["items"]) == [
            ("api-agent", "agent")
        ]
        assert (note["recorded_by"], note["recorded_by_kind"]) == ("api-agent", "agent")
        everything = await ok(agent, "changes_since", limit=200)
        assert "Somebody Else" not in {event["actor"] for event in everything["items"]}
        nothing = await ok(agent, "changes_since", actor="Somebody Else")
        assert nothing["items"] == []

        # The board's own route is the same: a page that sent a name would still be the person.
        owner = project.owner
        board_ok(owner, project, "create_node", actor="Somebody Else", kind="epic", title="Board")
        feed = (await ok(agent, "changes_since", limit=200))["items"]
        assert (owner.name, "person") in who(feed) and "Somebody Else" not in {
            e["actor"] for e in feed
        }


# -- confirming is for people ----------------------------------------------------------------------


async def test_an_agent_cannot_confirm_or_unconfirm_but_a_person_can_and_is_recorded_as_one() -> (
    None
):
    ines = register(name="Ines Duarte")
    project = make_project(ines)
    async with agents(project, "api-agent") as (agent,):
        fact = await ok(agent, "add_note", text="The fleet is 600 bikes", why="Operations plan")
        decision = await ok(
            agent,
            "add_note",
            text="Keep location short\n\nThirty days",
            why="Privacy",
            decided_by="Ines Duarte",
        )

        # An agent key can never be given this tool, so its own guard refuses it before the tool
        # can: whatever name it gives, nothing is changed. (The check inside the tool is for the
        # board's calls.)
        for record in (fact, decision):
            message = await refused(
                agent,
                "set_confirmation",
                note_id=record["id"],
                confirmation="confirmed",
                person="Ines Duarte",
                actor="Ines Duarte",
            )
            assert message.startswith(
                "Error executing tool set_confirmation: This agent key (api-agent) is not allowed"
            )
            assert "to use set_confirmation" in message and message.endswith("Nothing was changed.")
        assert (await ok(agent, "get_note", note_id=fact["id"]))["status"] == "unconfirmed"
        assert (await ok(agent, "get_note", note_id=decision["id"]))["status"] == "unconfirmed"

        # The person signed in can, on the board, and the history says a person did it.
        confirmed = confirm_on_board(
            "fact", fact, "Ines Duarte", project=project, quote="Counted at the depot."
        )
        assert confirmed["confirmed_by"] == "Ines Duarte"
        board_ok(
            ines,
            project,
            "set_confirmation",
            note_id=decision["id"],
            confirmation="confirmed",
            person="Ines Duarte",
        )
        history = (await ok(agent, "note_history", note_id=fact["id"]))["items"]
        assert who(history) == [("api-agent", "agent"), ("Ines Duarte", "person")]
        assert history[-1]["action"] == "note_confirmed"
        decided = (await ok(agent, "note_history", note_id=decision["id"]))["items"]
        assert who(decided)[-1] == ("Ines Duarte", "person")

        # Taking a confirmation away is a person's too.
        message = await refused(
            agent,
            "set_confirmation",
            note_id=fact["id"],
            confirmation="unconfirmed",
            person="Ines Duarte",
            quote="No longer true",
        )
        assert "to use set_confirmation" in message
        assert (await ok(agent, "get_note", note_id=fact["id"]))["status"] == "confirmed"
        board_ok(
            ines,
            project,
            "set_confirmation",
            note_id=fact["id"],
            confirmation="unconfirmed",
            person="Ines Duarte",
            quote="Timed again.",
        )
        last = (await ok(agent, "note_history", note_id=fact["id"]))["items"][-1]
        assert (last["action"], last["actor"], last["actor_kind"]) == (
            "note_unconfirmed",
            "Ines Duarte",
            "person",
        )

        # An agent may still pass on what a person said, and is recorded as the agent.
        board_ok(
            ines,
            project,
            "set_confirmation",
            note_id=fact["id"],
            confirmation="confirmed",
            person="Ines Duarte",
        )
        replaced = await ok(
            agent,
            "supersede_note",
            replaces=[fact["id"]],
            text="The fleet is 640 bikes",
            why="Recount: operations plan, version 4",
            said_by="Ines Duarte",
            quote="We counted again.",
        )
        assert replaced["replacement"]["recorded_by_kind"] == "agent"
        ended = (await ok(agent, "note_history", note_id=fact["id"]))["items"][-1]
        assert (ended["action"], ended["actor_kind"]) == ("note_superseded", "agent")
        assert ended["after"]["said_by"] == "Ines Duarte"


# -- key names -------------------------------------------------------------------------------------


def test_a_key_is_named_for_its_agent_and_two_keys_in_force_never_share_a_name() -> None:
    owner = register()
    project = make_project(owner)
    other = make_project(register())

    made = owner.call("POST", keys_path(project), {"name": "  api-agent  "})
    assert made.status == 201
    assert made.body["secret"].startswith("vrk_")
    assert made.body["key"]["name"] == "api-agent"
    assert set(made.body["key"]) == {
        "id",
        "name",
        "hint",
        "created_at",
        "last_used_at",
        "revoked_at",
        "tools",
        "code_mode",
        "verifier",
        "expires_on",
        "expired",
    }
    assert made.body["key"]["code_mode"] is False

    # Upper or lower case is the same name, and the refusal names the key that has it.
    for clash, existing in (("API-Agent", "api-agent"), ("Tests", "tests")):
        reply = owner.call("POST", keys_path(project), {"name": clash})
        assert reply.status == 409
        assert f"already has an agent key called '{existing}'" in reply.body["message"]
    # The same name in another project is another agent.
    assert other.owner.call("POST", keys_path(other), {"name": "api-agent"}).status == 201

    # What cannot be a name is refused in plain words, and no key is made.
    for typed, words in (
        ("", "Give the key the name of the agent"),
        ("   ", "Give the key the name of the agent"),
        ("x" * 101, "1 to 100 characters"),
        (A_TOKEN, "looks like a secret"),
    ):
        reply = owner.call("POST", keys_path(project), {"name": typed})
        assert reply.status == 400 and words in reply.body["message"], reply.body
        assert A_TOKEN not in reply.body["message"]
    assert owner.call("POST", keys_path(project), {"label": "api-agent-2"}).status == 400
    assert owner.call("POST", keys_path(project), {"name": 7}).status == 400

    # A key that was revoked frees its name.
    revoked = owner.call("DELETE", f"{keys_path(project)}/{made.body['key']['id']}")
    assert revoked.status == 200 and revoked.body["key"]["revoked_at"]
    assert owner.call("POST", keys_path(project), {"name": "API-agent"}).status == 201

    listed = owner.call("GET", keys_path(project)).body["keys"]
    assert [key["name"] for key in listed] == ["tests", "api-agent", "API-agent"]
    assert all("label" not in key for key in listed)
    columns = run_sql(
        "SELECT string_agg(column_name, ',' ORDER BY column_name) FROM information_schema.columns "
        "WHERE table_name = 'agent_keys'",
        plain=True,
        project=None,
    ).stdout.split(",")
    assert "name" in columns and "label" not in columns


async def test_a_revoked_key_stops_at_once_and_what_its_agent_did_keeps_its_name() -> None:
    owner = register()
    project = make_project(owner)
    key = make_key(project, "review-agent")
    other = make_key(project, "docs-agent")
    async with connect(project.mcp_url, key) as agent, connect(project.mcp_url, other) as docs:
        node = await ok(agent, "create_node", kind="epic", title="Before the key ended")
        listed = owner.call("GET", keys_path(project)).body["keys"]
        key_id = next(item["id"] for item in listed if item["name"] == "review-agent")
        assert owner.call("DELETE", f"{keys_path(project)}/{key_id}").status == 200

        # The connection that is already open is refused on its next call; the other key is not.
        with pytest.raises(BaseException) as already_open:
            await agent.call_tool("create_node", {"kind": "epic", "title": "After"})
        assert "agent key is not valid" in messages(already_open.value)
        history = (await ok(docs, "node_history", node=node["ref"]))["items"]
        assert who(history) == [("review-agent", "agent")]


# -- the person: name and password -----------------------------------------------------------------


def test_a_person_can_rename_their_account_and_what_they_do_next_carries_the_new_name() -> None:
    owner = register(name="Lena Okafor")
    project = make_project(owner)
    board_ok(owner, project, "create_node", kind="epic", title="Before the rename")

    renamed = owner.call("PATCH", "/board/api/account", {"name": "  Lena Okafor-Berg "})
    assert renamed.status == 200
    assert renamed.body == {
        "id": owner.id,
        "email": owner.email,
        "name": "Lena Okafor-Berg",
        "email_verified": False,
        "has_password": True,
    }
    me = api(owner.base, "GET", "/board/api/session", cookie=owner.cookie)
    assert me.body["account"]["name"] == "Lena Okafor-Berg"
    board_ok(owner, project, "create_node", kind="epic", title="After the rename")

    # What was recorded keeps the name it was recorded with.
    feed = board_ok(owner, project, "changes_since", limit=50)["items"]
    assert who(feed) == [("Lena Okafor", "person"), ("Lena Okafor-Berg", "person")]

    for typed, words in (
        ("", "Say what to call you"),
        ("n" * 101, "1 to 100 characters"),
        (A_TOKEN, "looks like a secret"),
        (5, "Say what to call you"),
    ):
        reply = owner.call("PATCH", "/board/api/account", {"name": typed})
        assert reply.status == 400 and words in reply.body["message"], reply.body
    assert (
        api(owner.base, "GET", "/board/api/session", cookie=owner.cookie).body["account"]["name"]
        == "Lena Okafor-Berg"
    )

    # A change needs the page's own secret, and the page's own address.
    path = "/board/api/account"
    assert api(owner.base, "PATCH", path, {"name": "X"}, cookie=owner.cookie).status == 403
    assert (
        owner.call("PATCH", path, {"name": "X"}, origin="https://elsewhere.example").status == 403
    )
    assert api(owner.base, "PATCH", path, {"name": "X"}).status == 401


def test_changing_the_password_needs_the_current_one_and_ends_every_other_session() -> None:
    owner = register()
    other_browser = owner.sign_in_again()
    new = "another long password 42"
    path = "/board/api/account/password"

    wrong = owner.call("POST", path, {"current": "not the password", "new": new})
    assert wrong.status == 400 and "current password is not right" in wrong.body["message"]
    short = owner.call("POST", path, {"current": owner.password, "new": "short"})
    assert short.status == 400 and "10 to 200 characters" in short.body["message"]
    assert (
        api(
            owner.base,
            "POST",
            "/board/api/session",
            {"email": owner.email, "password": owner.password},
        ).status
        == 200
    )
    # The page's own secret and address are needed here too.
    assert (
        api(
            owner.base, "POST", path, {"current": owner.password, "new": new}, cookie=owner.cookie
        ).status
        == 403
    )
    assert api(owner.base, "POST", path, {"current": owner.password, "new": new}).status == 401

    changed = owner.call("POST", path, {"current": owner.password, "new": new})
    assert changed.status == 200 and changed.body == {"ok": True}
    assert owner.call("GET", "/board/api/session").body["account"]["email"] == owner.email
    ended = api(owner.base, "GET", "/board/api/session", cookie=other_browser.cookie)
    assert ended.body["account"] is None
    assert (
        api(
            owner.base,
            "POST",
            "/board/api/session",
            {"email": owner.email, "password": owner.password},
        ).status
        == 401
    )
    assert (
        api(
            owner.base, "POST", "/board/api/session", {"email": owner.email, "password": new}
        ).status
        == 200
    )


def test_a_wrong_current_password_counts_against_the_same_limit_as_a_failed_sign_in() -> None:
    owner = register()
    path = "/board/api/account/password"
    for _ in range(5):
        assert (
            owner.call("POST", path, {"current": "guess", "new": "a long enough one"}).status == 400
        )

    blocked = owner.call("POST", path, {"current": owner.password, "new": "a long enough one"})
    assert blocked.status == 429 and "too many failed attempts" in blocked.body["message"]
    # The limit is the one sign-in has, so signing in is slowed down as well, for this address only.
    signed = api(
        owner.base, "POST", "/board/api/session", {"email": owner.email, "password": owner.password}
    )
    assert signed.status == 429
    assert register().email != owner.email


# -- moving the history ----------------------------------------------------------------------------


async def test_an_export_keeps_who_did_what_and_as_what_and_a_loaded_one_stamps_new_work() -> None:
    lena = register(name="Lena Okafor")
    source = make_project(lena)
    async with agents(source, "api-agent") as (agent,):
        epic = await ok(agent, "create_node", kind="epic", title="Moved")
        story = await ok(
            agent, "create_node", kind="story", title="With history", parent=epic["ref"]
        )
        await ok(agent, "claim_node", node=story["ref"])
        await ok(agent, "finish_work", node=story["ref"], outcome="done", note="Done and moved.")
        note = await ok(agent, "add_note", text="Agents record", why="Here")
        board_ok(lena, source, "add_note", text="People record", why="There")
        board_ok(
            lena,
            source,
            "set_confirmation",
            note_id=note["id"],
            confirmation="confirmed",
            person="Lena Okafor",
        )
        original = await export_everything(agent)

    assert original["version"] == 6
    assert {item["recorded_by_kind"] for item in original["notes"]} == {"agent", "person"}
    assert {item["actor_kind"] for item in original["events"]} == {"agent", "person"}

    # A new project takes the file and gives back exactly the same one.
    target = make_project(register())
    async with connect(target.mcp_url, target.key) as importer:
        await ok(importer, "import_graph", document=original)
        assert await export_everything(importer) == original
        shown = (await ok(importer, "node_history", node=story["ref"]))["items"]
        assert {(event["actor"], event["actor_kind"]) for event in shown} >= {
            ("api-agent", "agent")
        }
        assert (await ok(importer, "get_note", note_id=note["id"]))["recorded_by_kind"] == "agent"
        # New work in the loaded project is stamped as usual.
        made = await ok(importer, "create_node", kind="epic", title="After the import")
        newest = (await ok(importer, "node_history", node=made["ref"]))["items"]
        assert who(newest) == [("tests", "agent")]

    # A file that says it is current has to say who and as what.
    broken = copy.deepcopy(original)
    del broken["events"][0]["actor_kind"]
    empty = make_project(register())
    async with connect(empty.mcp_url, empty.key) as importer:
        message = await refused(importer, "import_graph", document=broken)
        assert "events[0].actor_kind" in message and "nothing was imported" in message
