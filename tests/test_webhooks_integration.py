"""M48: outgoing webhooks and email nudges, on the running containers with the check stack's
stand-in receiver (tests/scripted_webhooks.py) and stand-in mail server, through the board's HTTP
API (only a project's owner has webhooks, and who may ask is decided by the server) and a real MCP
client (what makes the news).

What has to hold: a signed message arrives for each kind of news that was asked for and for no
other, signed as the Standard Webhooks specification has it, with the kind, the project, the event
and the item's reference and never anything anyone wrote; a receiver that fails is tried again and
then given up on, and the failure shows in words; a redirect is not followed; an address that is
loopback, private, link-local, or a name that resolves to one, is refused when it is saved; the
secret is shown once and is never in the graph, an export, a list or a log; replacing it signs with
both for a while; a dead or slow address does not slow a tool call; and an email nudge goes only to
the person who asked, with a confirmed address, a few an hour, and says nothing anyone wrote."""

import json
import os
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest

from tests.helpers import (
    compose,
    connect,
    export_everything,
    make_tree,
    ok,
    refused,
    run_sql,
    unique_word,
)
from tests.test_checks_integration import words_of
from tests.test_email_integration import link_of, sent_to, token_of, verify_with, wait_for_mail
from tests.world import Person, Project, api, make_project, register, register_as, server_base
from vrum import webhook_signing

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# The stand-in is reached by the server as scripted-webhooks:8030 (compose.check.yaml).
INSIDE = "http://scripted-webhooks:8030"
EVERYTHING = [
    "ask.raised",
    "ask.answered",
    "plan.proposed",
    "plan.decided",
    "placement.proposed",
    "placement.decided",
    "work.blocked",
    "work.done",
    "work.available",
    "claim.lapsed",
]


# -- the stand-in receiver -----------------------------------------------------------------------


def receiver_url() -> str:
    given = os.environ.get("VRUM_TEST_WEBHOOKS_URL")
    if given:
        return given
    found = compose("port", "scripted-webhooks", "8030")
    assert found.returncode == 0 and found.stdout.strip(), found.stderr
    return "http://" + found.stdout.strip().replace("0.0.0.0", "127.0.0.1")


def received() -> list[dict[str, Any]]:
    with urllib.request.urlopen(f"{receiver_url()}/_log", timeout=10) as reply:
        return list(json.loads(reply.read()))


def received_at(tag: str) -> list[dict[str, Any]]:
    """What the stand-in was sent at an address that holds this tag."""
    return [one for one in received() if tag in one["path"]]


def hook(mode: str, tag: str) -> str:
    """An address of the stand-in as the server reaches it, with a tag that is this test's own."""
    return f"{INSIDE}/hook/{mode}?t={tag}"


def wait_for(found: Callable[[], Any], what: str, seconds: float = 40) -> Any:
    deadline = time.monotonic() + seconds
    while True:
        result = found()
        if result:
            return result
        if time.monotonic() > deadline:
            pytest.fail(f"Waited {seconds} seconds for {what}.")
        time.sleep(0.5)


def at_least(tag: str, count: int) -> Callable[[], bool]:
    return lambda: len(received_at(tag)) >= count


def settle(seconds: float = 6) -> None:
    """Long enough for the job to look at everything that happened (it looks every two seconds)."""
    time.sleep(seconds)


# -- the board's own requests --------------------------------------------------------------------


def path(project: Project, tail: str = "") -> str:
    return f"/board/api/projects/{project.id}/webhooks{tail}"


def add(owner: Person, project: Project, url: str, events: list[str]) -> dict[str, Any]:
    reply = owner.call("POST", path(project), {"url": url, "events": events})
    assert reply.status == 201, reply.body
    return dict(reply.body)


def webhook_of(owner: Person, project: Project, number: int) -> dict[str, Any]:
    reply = owner.call("GET", path(project))
    assert reply.status == 200, reply.body
    return next(one for one in reply.body["webhooks"] if one["id"] == number)


def deliveries(owner: Person, project: Project, number: int) -> list[dict[str, Any]]:
    reply = owner.call("GET", path(project, f"/{number}/deliveries"))
    assert reply.status == 200, reply.body
    return list(reply.body["deliveries"])


def check(secret: str, message: dict[str, Any]) -> str | None:
    return webhook_signing.problem(
        secret, message["headers"], message["body"], now=int(time.time())
    )


async def a_project() -> tuple[Person, Project]:
    owner = register()
    return owner, make_project(owner)


# -- what is sent --------------------------------------------------------------------------------


async def test_a_signed_message_arrives_for_each_kind_asked_for_and_names_no_one() -> None:
    owner, project = await a_project()
    tag = unique_word()
    secret_words = [unique_word() for _ in range(4)]
    async with connect(project.mcp_url, project.key) as agent:
        # Work made before the webhook exists is never sent.
        before = await ok(
            agent, "create_node", actor="x", kind="epic", title=f"Before {secret_words[0]}"
        )
        made = add(
            owner,
            project,
            hook("ok", tag),
            ["ask.raised", "work.blocked", "work.done", "plan.proposed", "placement.proposed"],
        )
        secret = made["secret"]
        assert secret.startswith("whsec_") and len(secret) == 50
        number = made["webhook"]["id"]
        assert "secret" not in made["webhook"] and secret not in json.dumps(made["webhook"])

        epic, story, task = await make_tree(agent)
        title = f"Secret title {secret_words[1]}"
        blocked = await ok(
            agent, "create_node", actor="x", kind="task", title=title, parent=story["ref"]
        )
        await ok(agent, "claim_node", node=blocked["ref"])
        await ok(
            agent,
            "finish_work",
            node=blocked["ref"],
            outcome="blocked",
            note=f"Private note {secret_words[2]}",
            ask={"question": f"Which {secret_words[3]} should it use?", "choices": ["A", "B"]},
        )
        await ok(agent, "claim_node", node=task["ref"])
        await ok(agent, "finish_work", node=task["ref"], outcome="done", note="Finished.")
        plan = await ok(
            agent,
            "create_plan",
            items=[{"key": "e", "kind": "epic", "title": f"Plan {secret_words[1]}"}],
            draft=True,
            title="A plan",
        )
        loose = await ok(
            agent, "create_node", actor="x", kind="story", title="Loose", parent=epic["ref"]
        )
        other = await ok(agent, "create_node", actor="x", kind="epic", title="Elsewhere")
        proposed = await ok(
            agent,
            "propose_placement",
            items=[loose["ref"]],
            parent=other["ref"],
            reason=f"Because {secret_words[2]}",
        )

        wanted = {"ask.raised", "work.blocked", "work.done", "plan.proposed", "placement.proposed"}
        got = wait_for(
            lambda: (lambda found: found if {m_type(x) for x in found} >= wanted else None)(
                received_at(tag)
            ),
            "the signed messages",
        )
        settle()
        got = received_at(tag)
    kinds = [m_type(one) for one in got]
    assert set(kinds) == wanted and len(kinds) == len(set(kinds)), kinds
    ids = [one["headers"]["webhook-id"] for one in got]
    assert len(set(ids)) == len(ids) and all(i.startswith("msg_") for i in ids)
    for one in got:
        assert check(secret, one) is None, one["headers"]
        body = json.loads(one["body"])
        assert set(body) == {"type", "timestamp", "data"}
        assert body["data"]["project"] == project.id
        assert set(body["data"]) == {"project", "event_id", "node", "object"}
        # Nothing anyone wrote, and nothing it is called.
        for word in secret_words + ["Loose", "Elsewhere", "Finished", "A plan"]:
            assert word not in one["body"], word
        assert before["ref"] not in one["body"]
    by_type = {m_type(one): json.loads(one["body"])["data"] for one in got}
    assert by_type["ask.raised"]["node"] == blocked["ref"] and by_type["ask.raised"][
        "object"
    ].startswith("ask:")
    assert by_type["work.blocked"]["node"] == blocked["ref"]
    assert by_type["work.done"]["node"] == task["ref"]
    assert by_type["plan.proposed"]["object"] == f"plan:{plan['plan']}"
    assert by_type["placement.proposed"]["object"] == f"placement:{proposed['id']}"
    assert by_type["placement.proposed"]["node"] is None
    # What the owner sees: each one delivered at once, in one attempt.
    sent = deliveries(owner, project, number)
    assert len(sent) == len(wanted) and {d["state"] for d in sent} == {"delivered"}
    assert {d["attempts"] for d in sent} == {1} and {d["last_status"] for d in sent} == {200}
    found = webhook_of(owner, project, number)
    assert found["failing"] is False and found["pending"] == 0 and found["last_error"] is None
    assert found["last_success_at"] is not None and found["last_failure_at"] is None


def m_type(message: dict[str, Any]) -> str:
    return str(json.loads(message["body"])["type"])


# -- when it does not work -----------------------------------------------------------------------


async def an_ask(agent: Any) -> dict[str, Any]:
    _, _, task = await make_tree(agent)
    await ok(agent, "claim_node", node=task["ref"])
    await ok(
        agent,
        "finish_work",
        node=task["ref"],
        outcome="blocked",
        note="Needs a decision.",
        ask={"question": "Which one?", "choices": ["A", "B"]},
    )
    return dict(task)


async def test_a_receiver_that_fails_is_tried_again_then_given_up_on_and_the_failure_shows() -> (
    None
):
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("fail", tag), ["ask.raised"])
        number, secret = made["webhook"]["id"], made["secret"]
        await an_ask(agent)
        wait_for(lambda: received_at(tag), "the first attempt")
        shown = wait_for(
            lambda: (lambda w: w if w["failing"] else None)(webhook_of(owner, project, number)),
            "the failure to show",
        )
        assert shown["last_error"] == "The receiver answered 500."
        assert shown["pending"] == 1 and shown["last_failure_at"] is not None
        assert tag not in json.dumps(shown["last_error"])
        # The wait between attempts grows (10 seconds, then 1 minute, ...): bring each forward.
        for attempt in range(2, 7):
            wait_for(at_least(tag, attempt - 1), f"attempt {attempt - 1} to reach the receiver")
            run_sql(
                "UPDATE webhook_deliveries SET next_attempt_at = now() "
                f"WHERE webhook_id = {number} AND state = 'pending'",
                project=None,
            )
            wait_for(at_least(tag, attempt), f"attempt {attempt}")
        settle(8)
    log = received_at(tag)
    assert len(log) == 6, "tried six times and then no more"
    assert len({one["headers"]["webhook-id"] for one in log}) == 1, "the id is the same each time"
    assert len({one["headers"]["webhook-timestamp"] for one in log}) >= 2
    assert all(check(secret, one) is None for one in log)
    [gave_up] = deliveries(owner, project, number)
    assert gave_up["state"] == "failed" and gave_up["attempts"] == 6
    assert (
        gave_up["last_error"] == "The receiver answered 500." and gave_up["next_attempt_at"] is None
    )
    final = webhook_of(owner, project, number)
    assert final["failing"] is True and final["pending"] == 0


async def test_a_redirect_is_a_failure_and_is_not_followed() -> None:
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("redirect", tag), ["ask.raised"])
        number = made["webhook"]["id"]
        await an_ask(agent)
        shown = wait_for(
            lambda: (lambda w: w if w["failing"] else None)(webhook_of(owner, project, number)),
            "the failure to show",
        )
        settle(5)
    assert "redirect" in shown["last_error"] and "not followed" in shown["last_error"]
    assert [one["mode"] for one in received_at(tag)] == ["redirect"]
    assert not [one for one in received() if one["mode"] == "after-redirect"], "it was followed"


async def test_a_receiver_that_says_it_failed_in_words_but_answers_200_has_been_sent_to() -> None:
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("lie", tag), ["ask.raised"])
        await an_ask(agent)
        wait_for(lambda: received_at(tag), "the message")
        settle(4)
    [sent] = deliveries(owner, project, made["webhook"]["id"])
    assert sent["state"] == "delivered" and sent["last_status"] == 200
    assert len(received_at(tag)) == 1


async def test_a_receiver_that_refuses_the_message_is_a_failure_in_words() -> None:
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("refuse", tag), ["ask.raised"])
        number = made["webhook"]["id"]
        await an_ask(agent)
        shown = wait_for(
            lambda: (lambda w: w if w["failing"] else None)(webhook_of(owner, project, number)),
            "the failure to show",
        )
    assert shown["last_error"] == "The receiver answered 401."


async def test_a_slow_address_is_given_up_on_in_time_and_never_slows_a_tool_call() -> None:
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("slow", tag), ["work.available", "ask.raised"])
        number = made["webhook"]["id"]
        _, story, _ = await make_tree(agent)
        started = time.monotonic()
        for index in range(8):
            await ok(
                agent,
                "create_node",
                actor="x",
                kind="task",
                title=f"Quick {index}",
                parent=story["ref"],
            )
        assert time.monotonic() - started < 8, "a tool call waited for the address"
        shown = wait_for(
            lambda: (lambda w: w if w["failing"] else None)(webhook_of(owner, project, number)),
            "the slow address to be given up on",
            seconds=45,
        )
    assert shown["last_error"] == "The receiver did not answer in time."


# -- where it may go -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "words"),
    [
        ("https://127.0.0.1/hook", "loopback"),
        ("https://localhost/hook", "loopback"),
        ("https://[::1]/hook", "loopback"),
        ("https://10.0.0.5/hook", "private network"),
        ("https://192.168.1.10/hook", "private network"),
        ("https://172.20.0.1/hook", "private network"),
        ("https://169.254.169.254/latest/meta-data/", "link-local"),
        ("https://100.64.0.1/hook", "not a public address"),
        ("https://0.0.0.0/hook", "private network"),
        ("https://db/hook", "private network"),
        ("https://app/hook", "private network"),
        ("https://user:pass@hooks.example.com/hook", "user name or a password"),
        ("https://hooks.example.com/hook#x", "#fragment"),
        ("https://nowhere.invalid/hook", "does not resolve"),
        ("hooks.example.com/hook", "https://"),
        ("ftp://hooks.example.com/hook", "https://"),
        ("https://hooks.example.com/" + "a" * 600, "at most 500"),
    ],
)
async def test_an_address_that_may_not_be_reached_is_refused_when_it_is_saved(
    url: str, words: str
) -> None:
    owner, project = await a_project()
    reply = owner.call("POST", path(project), {"url": url, "events": ["ask.raised"]})
    assert reply.status == 400, reply.body
    message = reply.body["message"]
    assert words in message and "Nothing was saved" in message
    assert url[8:40] not in message or url.startswith("hooks")
    assert owner.call("GET", path(project)).body["webhooks"] == []


async def test_what_is_wrong_with_a_webhook_is_said_in_words_and_changes_nothing() -> None:
    owner, project = await a_project()
    url = hook("ok", unique_word())
    cases: list[tuple[dict[str, Any], str]] = [
        ({"url": url}, "events is a list"),
        ({"url": url, "events": []}, "events is a list"),
        ({"url": url, "events": "ask.raised"}, "events is a list"),
        ({"url": url, "events": ["nonsense"]}, "'nonsense' is not a kind of news"),
        ({"url": url, "events": [3]}, "events is a list"),
        ({"events": ["ask.raised"]}, "url is the address"),
        ({"url": 5, "events": ["ask.raised"]}, "url is the address"),
    ]
    for body, wanted in cases:
        reply = owner.call("POST", path(project), body)
        assert reply.status == 400 and wanted in reply.body["message"], (body, reply.body)
    assert (
        "Choose from: ask.raised"
        in owner.call("POST", path(project), {"url": url, "events": ["nonsense"]}).body["message"]
    )
    assert owner.call("GET", path(project)).body["webhooks"] == []
    made = add(owner, project, url, ["ask.raised"])
    again = owner.call("POST", path(project), {"url": url, "events": ["work.done"]})
    assert again.status == 400 and "already has a webhook for that address" in again.body["message"]
    for index in range(9):
        add(owner, project, hook("ok", f"more-{index}-{unique_word()}"), ["work.done"])
    over = owner.call(
        "POST", path(project), {"url": hook("ok", unique_word()), "events": ["work.done"]}
    )
    assert over.status == 400 and "at most 10 webhooks" in over.body["message"]
    listed = owner.call("GET", path(project)).body
    assert (
        len(listed["webhooks"]) == 10
        and listed["most"] == 10
        and set(listed["kinds"]) == set(EVERYTHING)
    )
    assert made["webhook"]["id"] == listed["webhooks"][0]["id"]


async def test_only_the_owner_has_webhooks_and_other_people_are_told_so() -> None:
    owner, project = await a_project()
    made = add(owner, project, hook("ok", unique_word()), ["ask.raised"])
    number = made["webhook"]["id"]
    editor = _joined(owner, project, "editor")
    stranger = register()
    for person in (editor, stranger):
        for method, tail, body in (
            ("GET", "", None),
            ("POST", "", {"url": hook("ok", unique_word()), "events": ["ask.raised"]}),
            ("PATCH", f"/{number}", {"enabled": False}),
            ("DELETE", f"/{number}", None),
            ("POST", f"/{number}/secret", None),
            ("POST", f"/{number}/test", None),
            ("GET", f"/{number}/deliveries", None),
        ):
            reply = person.call(method, path(project, tail), body)
            assert reply.status in (400, 403, 404), (method, tail, reply.status, reply.body)
            assert made["secret"] not in json.dumps(reply.body)
    assert webhook_of(owner, project, number)["enabled"] is True
    # Nobody without a session can reach them either.
    assert api(server_base(), "GET", path(project)).status == 401


def _joined(owner: Person, project: Project, role: str, address: str | None = None) -> Person:
    made = owner.call("POST", f"/board/api/projects/{project.id}/invites", {"role": role})
    assert made.status == 201, made.body
    person = register_as(address) if address else register(project.base)
    accepted = person.call("POST", f"/board/api/invites/{made.body['token']}/accept")
    assert accepted.status == 200, accepted.body
    return person


# -- the secret ----------------------------------------------------------------------------------


async def test_the_secret_is_shown_once_and_is_in_no_list_graph_export_or_log() -> None:
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("ok", tag), ["ask.raised"])
        secret, number = made["secret"], made["webhook"]["id"]
        await an_ask(agent)
        wait_for(lambda: received_at(tag), "the message")
        settle(4)
        listed = owner.call("GET", path(project))
        assert secret not in json.dumps(listed.body)
        assert secret not in json.dumps(deliveries(owner, project, number))
        assert secret not in json.dumps(await export_everything(agent))
        # The project's own work cannot even read the table, and an agent may not write a secret.
        denied = run_sql(
            "SELECT count(*) FROM webhooks", plain=True, as_project=True, project=project.id
        )
        assert denied.returncode != 0 and "permission denied" in denied.stderr
        said = await refused(
            agent,
            "add_note",
            text=f"The secret is {secret}",
            why="Because.",
        )
        assert "what looks like a webhook signing secret" in said
    # app-dex is a second copy on the same database, so either may have made the call.
    logs = compose("logs", "--no-log-prefix", "--no-color", "app", "app-dex")
    assert logs.returncode == 0
    everything = logs.stdout + logs.stderr
    assert (
        secret not in everything and tag not in everything and "scripted-webhooks" not in everything
    )
    assert "webhook_delivered" in everything
    stored = run_sql(
        f"SELECT count(*) FROM webhooks WHERE id = {number} AND secret = '{secret}'",
        plain=True,
        project=None,
    )
    assert stored.stdout.strip() == "1", "the server keeps it to sign with"


async def test_replacing_the_secret_signs_with_both_for_a_while_and_then_only_the_new() -> None:
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("ok", tag), ["ask.raised"])
        old, number = made["secret"], made["webhook"]["id"]
        replaced = owner.call("POST", path(project, f"/{number}/secret"))
        assert replaced.status == 200, replaced.body
        new = replaced.body["secret"]
        assert new != old and replaced.body["webhook"]["replacing_secret_until"] is not None
        await an_ask(agent)
        [first] = wait_for(lambda: received_at(tag), "the message")
        assert len(first["headers"]["webhook-signature"].split()) == 2
        assert check(old, first) is None and check(new, first) is None
        # A day later only the new one signs.
        run_sql(
            f"UPDATE webhooks SET previous_until = now() - interval '1 minute' WHERE id = {number}",
            project=None,
        )
        await an_ask(agent)
        second = wait_for(lambda: received_at(tag)[1:], "the second message")[0]
    assert len(second["headers"]["webhook-signature"].split()) == 1
    assert check(new, second) is None and check(old, second) == "no signature fits"
    assert webhook_of(owner, project, number)["replacing_secret_until"] is None


# -- changing and testing ------------------------------------------------------------------------


async def test_a_webhook_can_be_changed_turned_off_and_on_and_deleted() -> None:
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("ok", tag), ["ask.raised"])
        number = made["webhook"]["id"]
        for bad, wanted in (
            ({}, "Say what to change"),
            ({"events": ["nonsense"]}, "not a kind of news"),
            ({"enabled": "yes"}, "enabled is true or false"),
        ):
            reply = owner.call("PATCH", path(project, f"/{number}"), bad)
            assert reply.status == 400 and wanted in reply.body["message"], reply.body
        assert owner.call("PATCH", path(project, "/99999"), {"enabled": False}).status == 404
        changed = owner.call(
            "PATCH", path(project, f"/{number}"), {"events": ["work.done", "ask.raised"]}
        )
        assert changed.body["webhook"]["events"] == ["ask.raised", "work.done"]
        assert owner.call("PATCH", path(project, f"/{number}"), {"enabled": False}).status == 200
        await an_ask(agent)
        settle()
        assert received_at(tag) == [], "it was off"
        assert owner.call("PATCH", path(project, f"/{number}"), {"enabled": True}).status == 200
        settle()
        assert received_at(tag) == [], "what happened while it was off is not sent when it is on"
        await an_ask(agent)
        wait_for(lambda: received_at(tag), "a message once it is on again")
        gone = owner.call("DELETE", path(project, f"/{number}"))
        assert gone.status == 200
        assert owner.call("GET", path(project)).body["webhooks"] == []
        assert owner.call("GET", path(project, f"/{number}/deliveries")).status == 404
        count = len(received_at(tag))
        await an_ask(agent)
        settle()
        assert len(received_at(tag)) == count


async def test_a_test_message_says_how_it_went_and_is_not_retried() -> None:
    owner, project = await a_project()
    good, bad = unique_word(), unique_word()
    fine = add(owner, project, hook("ok", good), ["ask.raised"])
    broken = add(owner, project, hook("fail", bad), ["ask.raised"])
    sent = owner.call("POST", path(project, f"/{fine['webhook']['id']}/test"))
    assert sent.status == 200 and sent.body == {
        "delivered": True,
        "status": 200,
        "words": "The receiver answered 200.",
    }
    [ping] = received_at(good)
    assert json.loads(ping["body"])["type"] == "ping" and check(fine["secret"], ping) is None
    failed = owner.call("POST", path(project, f"/{broken['webhook']['id']}/test"))
    assert failed.body["delivered"] is False and failed.body["status"] == 500
    assert webhook_of(owner, project, broken["webhook"]["id"])["failing"] is True
    settle(5)
    assert len(received_at(bad)) == 1


# -- other kinds of news -------------------------------------------------------------------------


async def test_a_claim_that_runs_out_is_news_once_and_one_that_is_finished_is_not() -> None:
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("ok", tag), ["claim.lapsed"])
        secret = made["secret"]
        _, story, task = await make_tree(agent)
        other = await ok(
            agent, "create_node", actor="x", kind="task", title="Other", parent=story["ref"]
        )
        await ok(agent, "claim_node", node=task["ref"], lease_seconds=2)
        await ok(agent, "claim_node", node=other["ref"], lease_seconds=2)
        await ok(agent, "finish_work", node=other["ref"], outcome="done", note="Done in time.")
        [got] = wait_for(lambda: received_at(tag), "the lapse", seconds=40)
        settle(8)
    sent = received_at(tag)
    assert len(sent) == 1, "once"
    body = json.loads(sent[0]["body"])
    assert body["type"] == "claim.lapsed" and body["data"]["node"] == task["ref"]
    assert body["data"]["event_id"] is None and check(secret, got) is None


async def test_work_that_may_be_ready_is_one_nudge_however_many_things_changed() -> None:
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("ok", tag), ["work.available"])
        number = made["webhook"]["id"]
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="E")
        for index in range(6):
            await ok(
                agent, "create_node", actor="x", kind="story", title=f"S{index}", parent=epic["ref"]
            )
        wait_for(lambda: received_at(tag), "the nudge")
        settle(5)
        assert len(received_at(tag)) == 1
        assert [d["type"] for d in deliveries(owner, project, number)] == ["work.available"]
        # Later news is a new nudge.
        time.sleep(10)
        await ok(agent, "create_node", actor="x", kind="story", title="Later", parent=epic["ref"])
        wait_for(lambda: len(received_at(tag)) >= 2, "the next nudge")


# -- email nudges --------------------------------------------------------------------------------


def a_person_with_a_confirmed_address(owner: Person, project: Project) -> tuple[Person, str]:
    address = f"{unique_word()}@example.test"
    person = _joined(owner, project, "viewer", address)
    link = link_of(wait_for_mail(address, "Confirm your email")[0])
    assert verify_with(token_of(link)).status == 200
    return person, address


async def test_a_nudge_is_mailed_only_to_the_person_who_asked_and_says_nothing_written() -> None:
    owner, project = await a_project()
    asker, address = a_person_with_a_confirmed_address(owner, project)
    quiet, other_address = a_person_with_a_confirmed_address(owner, project)
    words = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        listed = asker.call("GET", "/board/api/nudges")
        assert listed.status == 200 and listed.body["nudges"] == [] and listed.body["mail"] is True
        assert listed.body["email_confirmed"] is True
        assert set(listed.body["kinds"]) == {
            "ask.raised",
            "plan.proposed",
            "placement.proposed",
            "work.blocked",
            "claim.lapsed",
        }
        set_it = asker.call(
            "PUT",
            f"/board/api/projects/{project.id}/nudges",
            {"events": ["ask.raised", "work.blocked"]},
        )
        assert set_it.status == 200 and set_it.body["nudge"] == {
            "project": project.id,
            "events": ["ask.raised", "work.blocked"],
        }
        _, story, _ = await make_tree(agent)
        task = await ok(
            agent,
            "create_node",
            actor="x",
            kind="task",
            title=f"Private {words}",
            parent=story["ref"],
        )
        await ok(agent, "claim_node", node=task["ref"])
        await ok(
            agent,
            "finish_work",
            node=task["ref"],
            outcome="blocked",
            note=f"Note {words}",
            ask={"question": f"Question {words}?", "choices": ["Yes", "No"]},
        )
        mails = wait_for(lambda: wait_for_mail(address, "A question or an approval"), "the mail")
        blocked = wait_for_mail(address, "Work was blocked")
        settle(6)
    assert len(sent_to(address, "A question or an approval")) == 1
    assert len(blocked) == 1
    for one in mails + blocked:
        assert words not in one["text"] and words not in one["subject"]
        assert "\u2014" not in one["text"] and "\u2013" not in one["text"]
        assert f"/board/p/{project.id}#/" in one["text"]
    assert task["ref"] in blocked[0]["text"] and "#/item?item=" + task["ref"] in blocked[0]["text"]
    assert "#/needs" in mails[0]["text"]
    assert sent_to(other_address) == [
        m for m in sent_to(other_address) if m["subject"].startswith("Confirm")
    ]
    assert sent_to(f"{unique_word()}@example.test") == []
    assert [m for m in sent_to(other_address) if "waiting" in m["subject"]] == []
    # Turning them off stops them.
    off = asker.call("PUT", f"/board/api/projects/{project.id}/nudges", {"events": []})
    assert off.status == 200 and off.body["nudge"]["events"] == []
    assert asker.call("GET", "/board/api/nudges").body["nudges"] == []
    assert quiet is not None


async def test_a_nudge_needs_a_confirmed_address_and_a_known_kind_of_news() -> None:
    owner, project = await a_project()
    unconfirmed = _joined(owner, project, "editor", f"{unique_word()}@example.test")
    where = f"/board/api/projects/{project.id}/nudges"
    reply = unconfirmed.call("PUT", where, {"events": ["ask.raised"]})
    assert reply.status == 400 and "Confirm your email address first" in reply.body["message"]
    assert unconfirmed.call("GET", "/board/api/nudges").body["nudges"] == []
    confirmed, _ = a_person_with_a_confirmed_address(owner, project)
    for bad, wanted in (
        ({"events": ["work.available"]}, "not a kind of news"),
        ({"events": ["nonsense"]}, "not a kind of news"),
        ({"events": "ask.raised"}, "events is a list"),
        ({}, "events is a list"),
    ):
        said = confirmed.call("PUT", where, bad)
        assert said.status == 400 and wanted in said.body["message"], said.body
    stranger = register()
    assert stranger.call("PUT", where, {"events": ["ask.raised"]}).status in (400, 403, 404)
    assert api(server_base(), "PUT", where, {"events": []}).status == 401


async def test_a_nudge_stops_when_the_person_leaves_and_is_at_most_ten_an_hour() -> None:
    owner, project = await a_project()
    person, address = a_person_with_a_confirmed_address(owner, project)
    leaver, leaver_address = a_person_with_a_confirmed_address(owner, project)
    where = f"/board/api/projects/{project.id}/nudges"
    assert person.call("PUT", where, {"events": ["ask.raised"]}).status == 200
    assert leaver.call("PUT", where, {"events": ["ask.raised"]}).status == 200
    removed = owner.call("DELETE", f"/board/api/projects/{project.id}/members/{leaver.id}")
    assert removed.status == 200, removed.body
    async with connect(project.mcp_url, project.key) as agent:
        _, story, _ = await make_tree(agent)
        for index in range(13):
            task = await ok(
                agent, "create_node", actor="x", kind="task", title=f"T{index}", parent=story["ref"]
            )
            await ok(agent, "claim_node", node=task["ref"])
            await ok(
                agent,
                "finish_work",
                node=task["ref"],
                outcome="blocked",
                note="Stuck.",
                ask={"question": "Which?", "choices": ["A", "B"]},
            )
        wait_for(lambda: len(sent_to(address, "A question or an approval")) >= 10, "ten mails")
        settle(10)
    assert len(sent_to(address, "A question or an approval")) == 10
    assert sent_to(leaver_address, "A question or an approval") == []


async def test_the_nudge_and_webhook_tables_are_out_of_reach_of_a_project_role() -> None:
    project = make_project(register())
    for table in ("webhooks", "webhook_deliveries", "nudges", "notify_cursor"):
        result = run_sql(
            f"SELECT count(*) FROM {table}", plain=True, as_project=True, project=project.id
        )
        assert result.returncode != 0 and "permission denied" in result.stderr, table
    insert = (
        "INSERT INTO webhooks (project_id, url, events, secret, created_by, after_xid, after_id) "
        "VALUES (%d, '%s', '%s', '%s', 1, '0', 0)"
    )
    good_secret = "whsec_" + "A" * 43 + "="
    constraint_cases = (
        insert % (project.id, "https://a.example/x", "{}", good_secret),
        insert % (project.id, "ftp://a.example/x", "{ask.raised}", good_secret),
        insert % (project.id, "https://a.example/x", "{ask.raised}", "not-a-secret"),
    )
    for sql in constraint_cases:
        result = run_sql(sql, plain=True, project=None)
        assert result.returncode != 0 and "violates check constraint" in result.stderr, sql
    assert words_of("Error executing tool x: y") == "y"
