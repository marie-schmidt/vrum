"""Members, invitations and roles (M22 slice 2), through the board's own HTTP API against the real
containers: no browser and no MCP client, because everything here is decided by the server.

A project has one owner and any number of people the owner invited, each an editor or a viewer.
What has to hold: an invitation is shown once, kept only as a hash, spent by one person within seven
days, and refused in the same words when it is wrong, used, expired or withdrawn (and guessing is
slowed); a viewer can read and is refused everything that changes something, and only the owner
makes keys, invites people or changes who is in the project, on every request, by the server's own
read-only marks; a person who is removed or changed stops at once; what a member does is recorded
with their name as a person; no two people of one project share a name, however they got there;
no token is ever kept, logged or accepted as text; and members of one project reach no other."""

import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.helpers import compose, run_sql, unique_word
from tests.world import Person, Project, Reply, api, join_project, make_project, register
from vrum.accounts import INVITE_NOT_VALID, NO_SUCH_PROJECT, VIEWER_CANNOT_CHANGE
from vrum.board import READ_TOOLS, WRITE_TOOLS

pytestmark = pytest.mark.integration


def path(project: Project, tail: str = "") -> str:
    return f"/board/api/projects/{project.id}{tail}"


def tool(who: Person, project: Project, name: str, /, **arguments: Any) -> Reply:
    """What the board's page does when its person presses a button: the tool, as that person."""
    return who.call("POST", path(project, f"/tool/{name}"), arguments)


def done(who: Person, project: Project, name: str, /, **arguments: Any) -> dict[str, Any]:
    reply = tool(who, project, name, **arguments)
    assert reply.status == 200, f"{name} was refused: {reply.status} {reply.body}"
    return dict(reply.body)


def invite(owner: Person, project: Project, role: str) -> tuple[dict[str, Any], str]:
    reply = owner.call("POST", path(project, "/invites"), {"role": role})
    assert reply.status == 201, f"the invitation was refused: {reply.body}"
    return dict(reply.body["invite"]), str(reply.body["token"])


def epics_in(owner: Person, project: Project) -> int:
    return int(done(owner, project, "server_status")["counts"]["epics"])


def actors_of(owner: Person, project: Project, ref: str) -> list[tuple[str, str]]:
    history = done(owner, project, "node_history", node=ref)["items"]
    return [(event["actor"], event["actor_kind"]) for event in history]


def day_after(text: str, then: str) -> timedelta:
    return datetime.fromisoformat(then) - datetime.fromisoformat(text)


def a_wrong_token() -> str:
    return "vri_" + unique_word() * 3


# -- an invitation, from the owner's hand to a member --------------------------------------------


def test_an_invitation_is_shown_once_previewed_accepted_by_one_person_and_makes_a_member() -> None:
    owner = register(name=f"Olga Owner {unique_word()}")
    project = make_project(owner)
    made, token = invite(owner, project, "editor")
    assert token.startswith("vri_") and len(token) > 40
    assert set(made) == {
        "id",
        "role",
        "created_at",
        "expires_at",
        "created_by",
        "hint",
        "sent_to",
        "sent_at",
    }
    # Nobody was emailed: the owner copies the link themselves.
    assert (made["sent_to"], made["sent_at"]) == (None, None)
    assert made["role"] == "editor" and made["created_by"] == owner.name
    assert made["hint"] == token[:8]
    lasts = day_after(made["created_at"], made["expires_at"])
    assert timedelta(days=7) - timedelta(minutes=1) < lasts <= timedelta(days=7)

    pending = owner.call("GET", path(project, "/invites"))
    assert pending.status == 200 and [one["id"] for one in pending.body["invites"]] == [made["id"]]
    assert token not in json.dumps(pending.body)

    # The link's page is the board's own page, with the same security headers as any other.
    with urllib.request.urlopen(f"{owner.base}/board/invite/{token}", timeout=30) as response:
        page = response.read().decode()
        assert response.status == 200 and "text/html" in response.headers["Content-Type"]
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["Referrer-Policy"] == "no-referrer"
    assert token not in page

    guest = register(name=f"Gus Guest {unique_word()}")
    link = f"/board/api/invites/{token}"
    assert api(owner.base, "GET", link).status == 401
    preview = guest.call("GET", link)
    assert preview.status == 200 and preview.body == {
        "invite": {
            "project_name": project.name,
            "owner_name": owner.name,
            "role": "editor",
            "invited_by": owner.name,
            "expires_at": made["expires_at"],
            "problem": None,
        }
    }
    # Looking is not accepting, and a page of another site cannot accept for the person.
    assert guest.call("GET", "/board/api/projects").body == {"projects": []}
    assert guest.call("POST", f"{link}/accept", origin="https://evil.example").status == 403
    assert guest.call("GET", link).status == 200

    accepted = guest.call("POST", f"{link}/accept")
    assert accepted.status == 200
    assert set(accepted.body["project"]) == {"id", "name", "created_at", "role"}
    assert (
        accepted.body["project"]["id"] == project.id
        and accepted.body["project"]["role"] == "editor"
    )
    assert guest.call("GET", path(project)).body["role"] == "editor"
    assert owner.call("GET", path(project)).body["role"] == "owner"
    assert {
        p["id"]: p["role"] for p in guest.call("GET", "/board/api/projects").body["projects"]
    } == {project.id: "editor"}

    # It is spent: nobody, not even the same person, can use it again, and it is no longer pending.
    stranger = register()
    for who in (stranger, guest):
        for reply in (who.call("GET", link), who.call("POST", f"{link}/accept")):
            assert reply.status == 404 and reply.body == {"message": INVITE_NOT_VALID}
    assert owner.call("GET", path(project, "/invites")).body == {"invites": []}

    # The owner is listed first and sees everyone's email; the others see names and roles.
    seen = owner.call("GET", path(project, "/members")).body["members"]
    assert [(m["name"], m["role"], m["email"]) for m in seen] == [
        (owner.name, "owner", owner.email),
        (guest.name, "editor", guest.email),
    ]
    assert set(seen[1]) == {"account_id", "name", "email", "role", "joined_at"}
    assert seen[1]["account_id"] == guest.id
    heard = guest.call("GET", path(project, "/members")).body["members"]
    assert [(m["name"], m["role"], m["email"]) for m in heard] == [
        (owner.name, "owner", None),
        (guest.name, "editor", guest.email),
    ]

    # Someone who owns the project, or is already in it, is told so, and the link stays good.
    again, spare = invite(owner, project, "viewer")
    for who, words in ((owner, "You own this project"), (guest, "You are already in this project")):
        refused = who.call("POST", f"/board/api/invites/{spare}/accept")
        assert refused.status == 409 and refused.body["message"].startswith(words)
        assert who.call("GET", f"/board/api/invites/{spare}").body["invite"]["problem"]
    assert [one["id"] for one in owner.call("GET", path(project, "/invites")).body["invites"]] == [
        again["id"]
    ]
    withdrawn = owner.call("DELETE", path(project, f"/invites/{again['id']}"))
    assert withdrawn.status == 200 and withdrawn.body["invite"]["id"] == again["id"]


def test_wrong_used_expired_and_withdrawn_links_say_the_same_and_guessing_is_slowed() -> None:
    owner = register()
    project = make_project(owner)
    used_up, used = invite(owner, project, "viewer")
    join = register()
    assert join.call("POST", f"/board/api/invites/{used}/accept").status == 200
    expired_one, expired = invite(owner, project, "viewer")
    run_sql(
        "UPDATE project_invites SET created_at = now() - interval '8 days', "
        f"expires_at = now() - interval '1 day' WHERE id = {expired_one['id']}",
        project=False,
    )
    withdrawn_one, withdrawn = invite(owner, project, "viewer")
    assert owner.call("DELETE", path(project, f"/invites/{withdrawn_one['id']}")).status == 200
    _, still_good = invite(owner, project, "viewer")
    assert [
        one["role"] for one in owner.call("GET", path(project, "/invites")).body["invites"]
    ] == ["viewer"]

    tries = (a_wrong_token(), used, expired, withdrawn, "not-even-shaped-like-one", "vri_")
    for token in tries:
        who = register()
        for reply in (
            who.call("GET", f"/board/api/invites/{token}"),
            who.call("POST", f"/board/api/invites/{token}/accept"),
        ):
            assert reply.status == 404 and reply.body == {"message": INVITE_NOT_VALID}, token
        assert who.call("GET", "/board/api/projects").body == {"projects": []}

    # An invitation that was spent cannot be withdrawn, and one of another project is not found.
    assert owner.call("DELETE", path(project, f"/invites/{used_up['id']}")).status == 409
    elsewhere = make_project(register())
    assert (
        elsewhere.owner.call("DELETE", path(elsewhere, f"/invites/{expired_one['id']}")).status
        == 404
    )

    # Someone who keeps trying links that are not valid is told to wait, even with a good one.
    guesser = register()
    answers = [
        guesser.call("GET", f"/board/api/invites/{a_wrong_token()}").status for _ in range(11)
    ]
    assert answers == [404] * 10 + [429]
    slowed = guesser.call("GET", f"/board/api/invites/{still_good}")
    assert slowed.status == 429 and "too many failed attempts" in slowed.body["message"]
    assert guesser.call("POST", f"/board/api/invites/{still_good}/accept").status == 429
    assert register().call("GET", f"/board/api/invites/{still_good}").status == 200


# -- what each role may do -------------------------------------------------------------------------


def test_roles_are_applied_to_every_route_on_every_request_by_the_servers_own_read_marks() -> None:
    owner = register(name=f"Olga Owner {unique_word()}")
    project = make_project(owner)
    editor = join_project(project, "editor", f"Ed Editor {unique_word()}")
    viewer = join_project(project, "viewer", f"Vic Viewer {unique_word()}")

    # What a viewer may call is decided by the tool's own mark, so every tool the pages read must
    # carry it and no tool that writes may.
    marks = {
        t["name"]: t["changes_something"]
        for t in owner.call("GET", "/board/api/tools").body["tools"]
    }
    assert not [name for name in READ_TOOLS if marks[name]]
    assert all(marks[name] for name in WRITE_TOOLS)

    for name in READ_TOOLS:
        assert tool(viewer, project, name).status in (200, 400), name
    for name in WRITE_TOOLS:
        reply = tool(viewer, project, name)
        assert (reply.status, reply.body) == (403, {"message": VIEWER_CANNOT_CHANGE}), name
    wanted = {"kind": "epic", "title": "A viewer's epic"}
    assert tool(viewer, project, "create_node", **wanted).status == 403
    assert epics_in(owner, project) == 0
    assert viewer.call("POST", path(project, "/try/list_nodes"), {}).status == 200
    assert viewer.call("POST", path(project, "/try/create_node"), wanted).status == 403
    assert viewer.call("GET", path(project, "/index")).status == 200
    assert VIEWER_CANNOT_CHANGE == (
        "You can look at this project but not change it. Ask its owner to make you an editor. "
        "Nothing was changed."
    )

    # Keys, invitations and the people are the owner's alone: an editor and a viewer are refused
    # in plain words, and nothing is made or changed.
    owners_only = [
        ("GET", "/keys", None),
        ("POST", "/keys", {"name": "sneaky-agent"}),
        ("DELETE", "/keys/1", None),
        ("GET", "/invites", None),
        ("POST", "/invites", {"role": "editor"}),
        ("DELETE", "/invites/1", None),
        ("PATCH", f"/members/{viewer.id}", {"role": "editor"}),
        ("PATCH", f"/members/{editor.id}", {"role": "viewer"}),
        ("DELETE", f"/members/{owner.id}", None),
    ]
    for who, other in ((editor, viewer), (viewer, editor)):
        for method, tail, body in [*owners_only, ("DELETE", f"/members/{other.id}", None)]:
            reply = who.call(method, path(project, tail), body)
            assert reply.status == 403, (who.name, method, tail, reply.body)
            assert reply.body["message"].startswith("Only the owner of this project can "), tail
    assert [k["name"] for k in owner.call("GET", path(project, "/keys")).body["keys"]] == ["tests"]
    assert owner.call("GET", path(project, "/invites")).body == {"invites": []}
    assert [m["role"] for m in owner.call("GET", path(project, "/members")).body["members"]] == [
        "owner",
        "editor",
        "viewer",
    ]
    for who in (editor, viewer):
        assert who.call("GET", path(project, "/members")).status == 200

    # An editor reads and writes, and what they write is recorded with their name, as a person.
    epic = done(editor, project, "create_node", kind="epic", title="Written by the editor")
    assert actors_of(owner, project, epic["ref"]) == [(editor.name, "person")]
    note = done(editor, project, "add_note", text="The editor knows this", why="their notes")
    reviewed = done(
        editor,
        project,
        "set_confirmation",
        note_id=note["id"],
        confirmation="confirmed",
        person="Rae Reviewer",
    )
    assert reviewed["status"] == "confirmed"
    events = done(owner, project, "changes_since", actor=editor.name)["items"]
    assert {(e["actor"], e["actor_kind"]) for e in events} == {(editor.name, "person")}
    assert len(events) >= 3

    # The owner may do all of it.
    assert owner.call("POST", path(project, "/keys"), {"name": "api-agent"}).status == 201
    assert tool(owner, project, "create_node", **wanted).status == 200
    assert owner.call("POST", path(project, "/invites"), {"role": "viewer"}).status == 201


def test_changing_a_role_or_removing_a_person_takes_effect_on_their_next_request() -> None:
    owner = register()
    project = make_project(owner)
    editor = join_project(project, "editor", f"Ed Editor {unique_word()}")
    leaver = join_project(project, "viewer")
    wanted = {"kind": "epic", "title": "Work"}
    first = done(editor, project, "create_node", **wanted)

    changed = owner.call("PATCH", path(project, f"/members/{editor.id}"), {"role": "viewer"})
    assert changed.status == 200 and changed.body["member"]["role"] == "viewer"
    assert changed.body["member"]["account_id"] == editor.id and changed.body["member"]["email"]
    assert tool(editor, project, "create_node", **wanted).status == 403
    assert editor.call("GET", path(project)).body["role"] == "viewer"
    assert (
        owner.call("PATCH", path(project, f"/members/{editor.id}"), {"role": "editor"}).status
        == 200
    )
    assert tool(editor, project, "create_node", **wanted).status == 200

    for tail, body, status in (
        (f"/members/{editor.id}", {"role": "owner"}, 400),
        (f"/members/{editor.id}", {"role": ""}, 400),
        (f"/members/{owner.id}", {"role": "viewer"}, 409),
        ("/members/987654321", {"role": "viewer"}, 404),
        ("/members/me", {"role": "viewer"}, 409),
    ):
        reply = owner.call("PATCH", path(project, tail), body)
        assert reply.status == status and reply.body["message"], (tail, reply.body)

    # The owner stays: they cannot leave, be removed, or remove someone who is not there.
    for tail, status in (
        ("/members/me", 409),
        (f"/members/{owner.id}", 409),
        ("/members/987654321", 404),
        ("/members/not-a-number", 404),
    ):
        assert owner.call("DELETE", path(project, tail)).status == status, tail

    removed = owner.call("DELETE", path(project, f"/members/{editor.id}"))
    assert removed.status == 200 and removed.body["member"]["account_id"] == editor.id
    unknown = owner.call("GET", "/board/api/projects/987654321")
    assert unknown.status == 404 and unknown.body == {"message": NO_SUCH_PROJECT}
    for method, tail in (
        ("GET", ""),
        ("GET", "/members"),
        ("GET", "/index"),
        ("GET", "/keys"),
        ("POST", "/tool/list_nodes"),
        ("POST", "/tool/create_node"),
        ("POST", "/try/list_nodes"),
        ("DELETE", "/members/me"),
    ):
        reply = editor.call(method, path(project, tail), {} if method == "POST" else None)
        assert (reply.status, reply.body) == (404, unknown.body), (method, tail)
    assert editor.call("GET", "/board/api/projects").body == {"projects": []}
    assert editor.call("GET", "/board/api/session").body["account"]["id"] == editor.id
    assert actors_of(owner, project, first["ref"]) == [(editor.name, "person")]
    assert [m["role"] for m in owner.call("GET", path(project, "/members")).body["members"]] == [
        "owner",
        "viewer",
    ]

    # A person can leave on their own. A new link brings a removed person back, with its role.
    assert leaver.call("DELETE", path(project, "/members/me")).status == 200
    assert leaver.call("GET", path(project)).status == 404
    _, token = invite(owner, project, "viewer")
    back = editor.call("POST", f"/board/api/invites/{token}/accept")
    assert back.status == 200 and back.body["project"]["role"] == "viewer"
    assert tool(editor, project, "list_nodes").status == 200


# -- who is who ------------------------------------------------------------------------------------


def test_no_two_people_of_a_project_share_a_name_when_joining_or_when_renaming() -> None:
    word = unique_word()
    owner = register(name=f"Lena {word}")
    project = make_project(owner)
    _, token = invite(owner, project, "editor")
    twin = register(name=f"LENA {word}")
    taken = (
        f"Someone in this project already uses the name {twin.name}. "
        "Change your name on the Users page first."
    )
    link = f"/board/api/invites/{token}"
    assert twin.call("GET", link).body["invite"]["problem"] == taken
    refused = twin.call("POST", f"{link}/accept")
    assert (refused.status, refused.body) == (409, {"message": taken})
    assert [m["name"] for m in owner.call("GET", path(project, "/members")).body["members"]] == [
        owner.name
    ]
    assert len(owner.call("GET", path(project, "/invites")).body["invites"]) == 1

    # The link was not spent: with a name of their own the same person joins with it.
    assert twin.call("PATCH", "/board/api/account", {"name": f"Lena Two {word}"}).status == 200
    assert twin.call("GET", link).body["invite"]["problem"] is None
    assert twin.call("POST", f"{link}/accept").status == 200
    twin.name = f"Lena Two {word}"
    before = done(twin, project, "add_note", text="Before renaming", why="a test")
    assert before["recorded_by"] == f"Lena Two {word}"

    # Members are held to it too, whatever the case, and so are renames, in either direction.
    _, second = invite(owner, project, "viewer")
    third = register(name=f"lena TWO {word}")
    assert third.call("POST", f"/board/api/invites/{second}/accept").status == 409
    clash = twin.call("PATCH", "/board/api/account", {"name": f"lena {word}"})
    assert clash.status == 409
    assert clash.body["message"].startswith(f"Someone in the project {project.name!r} already uses")
    assert owner.call("PATCH", "/board/api/account", {"name": f"LENA TWO {word}"}).status == 409
    assert twin.call("GET", "/board/api/session").body["account"]["name"] == f"Lena Two {word}"
    assert owner.call("GET", "/board/api/session").body["account"]["name"] == f"Lena {word}"

    # A name only someone in no shared project uses is free, and so is one's own in another case.
    outsider = register(name=f"Outsider {word}")
    make_project(outsider)
    assert twin.call("PATCH", "/board/api/account", {"name": f"Outsider {word}"}).status == 200
    assert twin.call("PATCH", "/board/api/account", {"name": f"OUTSIDER {word}"}).status == 200

    # What was recorded keeps the name it was recorded with.
    after = done(twin, project, "add_note", text="After renaming", why="a test")
    assert before["recorded_by"] == f"Lena Two {word}"
    assert after["recorded_by"] == f"OUTSIDER {word}"
    assert (
        done(owner, project, "get_note", note_id=before["id"])["recorded_by"] == f"Lena Two {word}"
    )

    # A name is checked against every project the person is in.
    elsewhere = make_project(register(name=f"Pavel {word}"))
    _, other_token = invite(elsewhere.owner, elsewhere, "editor")
    assert twin.call("POST", f"/board/api/invites/{other_token}/accept").status == 200
    stuck = twin.call("PATCH", "/board/api/account", {"name": f"Pavel {word}"})
    assert stuck.status == 409 and repr(elsewhere.name) in stuck.body["message"]


def test_two_people_with_one_name_cannot_both_join_at_the_same_moment() -> None:
    word = unique_word()
    project = make_project(register())
    names = [f"Sam {word}", f"sam {word}"]
    pairs = []
    for name in names:
        _, token = invite(project.owner, project, "viewer")
        pairs.append((register(name=name), token))

    def accept(pair: tuple[Person, str]) -> int:
        person, token = pair
        return person.call("POST", f"/board/api/invites/{token}/accept").status

    with ThreadPoolExecutor(2) as pool:
        statuses = sorted(pool.map(accept, pairs))
    assert statuses == [200, 409]
    members = project.owner.call("GET", path(project, "/members")).body["members"]
    assert len([m for m in members if m["name"].lower() == names[0].lower()]) == 1


# -- nothing leaks, and the walls hold -------------------------------------------------------------


def test_an_invitation_token_is_never_kept_logged_or_accepted_as_text() -> None:
    owner = register()
    project = make_project(owner)
    made, token = invite(owner, project, "editor")
    guest = register()
    assert guest.call("GET", f"/board/api/invites/{token}").status == 200
    assert guest.call("POST", f"/board/api/invites/{token}/accept").status == 200

    everything = [
        owner.call("GET", path(project, "/invites")),
        owner.call("GET", path(project, "/members")),
        owner.call("GET", path(project)),
        owner.call("GET", "/board/api/projects"),
        guest.call("GET", "/board/api/projects"),
        guest.call("GET", "/board/api/session"),
    ]
    assert all(token not in json.dumps(reply.body) for reply in everything)

    stored = run_sql(
        f"SELECT token_hash || ' ' || token_hint FROM project_invites WHERE id = {made['id']}",
        plain=True,
        project=False,
    ).stdout
    assert hashlib.sha256(token.encode()).hexdigest() in stored and token not in stored

    # Written into any text, it is refused, on the board's route as on the agents' endpoint.
    reply = tool(owner, project, "add_note", text=f"The link is {token}", why="chat")
    assert reply.status == 400 and token not in json.dumps(reply.body)
    assert "invitation" in reply.body["message"]
    assert guest.call("PATCH", "/board/api/account", {"name": token}).status == 400
    assert owner.call("POST", path(project, "/keys"), {"name": token}).status == 400

    logged = compose("logs", "app", "--no-log-prefix", "--tail", "3000")
    text = logged.stdout + logged.stderr
    assert "tool_call" in text
    assert token not in text and token[4:] not in text


def test_members_of_one_project_reach_no_other_and_a_role_belongs_to_one_project() -> None:
    owner_a, owner_b = register(), register()
    project_a, project_b = make_project(owner_a), make_project(owner_b)
    member = join_project(project_a, "viewer")
    missing = owner_a.call("GET", "/board/api/projects/987654321").body

    for method, tail in (
        ("GET", ""),
        ("GET", "/members"),
        ("POST", "/tool/list_nodes"),
        ("POST", "/try/list_nodes"),
        ("GET", "/index"),
    ):
        reply = member.call(method, path(project_b, tail), {} if method == "POST" else None)
        assert (reply.status, reply.body) == (404, missing), tail
    assert owner_a.call("GET", path(project_b, "/members")).status == 404

    # The same person is a viewer in one project and an editor in another, and each is kept apart.
    _, token = invite(owner_b, project_b, "editor")
    assert member.call("POST", f"/board/api/invites/{token}/accept").status == 200
    roles = {p["id"]: p["role"] for p in member.call("GET", "/board/api/projects").body["projects"]}
    assert roles == {project_a.id: "viewer", project_b.id: "editor"}
    assert tool(member, project_a, "create_node", kind="epic", title="No").status == 403
    assert tool(member, project_b, "create_node", kind="epic", title="Yes").status == 200
    assert (epics_in(owner_a, project_a), epics_in(owner_b, project_b)) == (0, 1)

    # And is the owner of what they make themselves, however many projects they are invited to.
    own = make_project(member)
    assert own.owner.call("GET", path(own)).body["role"] == "owner"
    assert member.call("POST", path(own, "/keys"), {"name": "mine-agent"}).status == 201
    assert member.call("POST", path(project_a, "/keys"), {"name": "mine-agent"}).status == 403
