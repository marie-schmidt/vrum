"""The acceptance scenario for webhooks and email nudges (M48), told from the outside: an owner adds
webhooks through the board's own HTTP routes, a real MCP client makes the news, the check stack's
stand-in receiver (tests/scripted_webhooks.py, found with `docker compose port scripted-webhooks
8030`) keeps what it is sent, the checks verify each message with vrum.webhook_signing, and the
stand-in mail server shows what a person who asked was mailed.

Two parts: `the_news_arrives` (a chapter of `working`: the signed messages, retries, the secret
being replaced, what turning one off does, one nudge for work that may be ready, a claim that runs
out, and email) and `webhook_limits` (a chapter of `hardening`: where an address may go, a redirect,
where the secret is never seen, a slow address that must not slow a tool call, and who may have
them at all). Each part works in projects of its own."""

import json
import time
from collections.abc import Callable
from typing import Any

from tests.acceptance_scenario import Failed, chapter, clip, proof
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
from tests.test_email_integration import link_of, sent_to, token_of, verify_with
from tests.test_webhooks_integration import (
    EVERYTHING,
    an_ask,
    check,
    deliveries,
    hook,
    m_type,
    path,
    received,
    received_at,
    webhook_of,
)
from tests.world import Person, Project, api, make_project, register, register_as, server_base
from vrum import webhook_signing


def words(refusal: str) -> str:
    return refusal.split(": ", 1)[1] if refusal.startswith("Error executing tool") else refusal


def wait_for(found: Callable[[], Any], what: str, seconds: float = 40) -> Any:
    """Poll until `found` gives something, or stop the walk saying what never happened."""
    deadline = time.monotonic() + seconds
    while True:
        result = found()
        if result:
            return result
        if time.monotonic() > deadline:
            raise Failed(f"M48: waited {seconds:.0f} seconds for {what}")
        time.sleep(0.5)


def at_least(tag: str, count: int) -> Callable[[], bool]:
    """Whether the stand-in has been sent at least `count` messages at this tag's address."""
    return lambda: len(received_at(tag)) >= count


def settle(seconds: float = 6) -> None:
    """Long enough for the job that sends to look at everything (it looks every two seconds)."""
    time.sleep(seconds)


def add(owner: Person, project: Project, url: str, events: list[str]) -> dict[str, Any]:
    reply = owner.call("POST", path(project), {"url": url, "events": events})
    if reply.status != 201:
        raise Failed(f"M48: the webhook was refused: {reply.body}")
    return dict(reply.body)


async def a_project() -> tuple[Person, Project]:
    owner = register()
    return owner, make_project(owner)


def failing(owner: Person, project: Project, number: int) -> Callable[[], Any]:
    return lambda: (lambda one: one if one["failing"] else None)(webhook_of(owner, project, number))


# -- the news arrives (a chapter of `working`) ---------------------------------------------------


async def signed_messages() -> None:
    """A signed message arrives for each kind asked for and for no other, and says nothing anyone
    wrote; the secret is shown once."""
    owner, project = await a_project()
    tag = unique_word()
    written = [unique_word() for _ in range(4)]
    chosen = {"ask.raised", "work.blocked", "work.done", "plan.proposed", "placement.proposed"}
    async with connect(project.mcp_url, project.key) as agent:
        before = await ok(agent, "create_node", kind="epic", title=f"Before {written[0]}")
        made = add(owner, project, hook("ok", tag), sorted(chosen))
        secret, number = made["secret"], made["webhook"]["id"]
        epic, story, task = await make_tree(agent)
        blocked = await ok(
            agent, "create_node", kind="task", title=f"Title {written[1]}", parent=story["ref"]
        )
        await ok(agent, "claim_node", node=blocked["ref"])
        await ok(
            agent, "finish_work", node=blocked["ref"], outcome="blocked",
            note=f"Private note {written[2]}",
            ask={"question": f"Which {written[3]} should it use?", "choices": ["A", "B"]},
        )  # fmt: skip
        await ok(agent, "claim_node", node=task["ref"])
        await ok(agent, "finish_work", node=task["ref"], outcome="done", note="Finished.")
        plan = await ok(
            agent, "create_plan", draft=True, title="A plan",
            items=[{"key": "e", "kind": "epic", "title": f"Plan {written[1]}"}],
        )  # fmt: skip
        loose = await ok(agent, "create_node", kind="story", title="Loose", parent=epic["ref"])
        other = await ok(agent, "create_node", kind="epic", title="Elsewhere")
        moved = await ok(
            agent, "propose_placement", items=[loose["ref"]], parent=other["ref"],
            reason=f"Because {written[2]}",
        )  # fmt: skip
        wait_for(lambda: {m_type(one) for one in received_at(tag)} >= chosen, "the signed messages")
        settle()
        sent = received_at(tag)
        listing = owner.call("GET", path(project))
        history = deliveries(owner, project, number)
    kinds = [m_type(one) for one in sent]
    bodies = [json.loads(one["body"]) for one in sent]
    by_type = {body["type"]: body["data"] for body in bodies}
    ids = [one["headers"]["webhook-id"] for one in sent]
    leaked = [
        word for word in [*written, "Loose", "Elsewhere", "Finished", "A plan", before["ref"]]
        if any(word in one["body"] for one in sent)
    ]  # fmt: skip
    proof(
        "M48",
        "an owner adds a webhook for five kinds of news (a question raised, work blocked, work "
        "done, a plan to approve, a move proposed) and is shown its secret once, in the answer "
        "that makes it; a message arrives for each of those kinds and for no other (work made, "
        "claimed and a plan made are other news), each one once, each signed as the Standard "
        "Webhooks specification has it (checked with the secret, by the very code that signs), "
        "with its own id; a message says the kind, the project, the event and the story or "
        "task's reference or the number of the question, plan or proposal, and no title, "
        "question, note or reason anyone wrote; and the owner's list shows each as delivered at "
        "once in one attempt",
        secret.startswith("whsec_")
        and len(secret) == 50
        and "secret" not in made["webhook"]
        and set(kinds) == chosen
        and len(kinds) == len(set(kinds))
        and len(set(ids)) == len(ids)
        and all(i.startswith("msg_") for i in ids)
        and all(check(secret, one) is None for one in sent)
        and all(set(b) == {"type", "timestamp", "data"} for b in bodies)
        and all(set(b["data"]) == {"project", "event_id", "node", "object"} for b in bodies)
        and all(b["data"]["project"] == project.id for b in bodies)
        and not leaked
        and by_type["ask.raised"]["node"] == blocked["ref"]
        and by_type["ask.raised"]["object"].startswith("ask:")
        and by_type["work.done"]["node"] == task["ref"]
        and by_type["plan.proposed"]["object"] == f"plan:{plan['plan']}"
        and by_type["placement.proposed"]["object"] == f"placement:{moved['id']}"
        and {d["state"] for d in history} == {"delivered"}
        and {d["attempts"] for d in history} == {1}
        and secret not in json.dumps(listing.body)
        and secret not in json.dumps(history),
        f"{len(sent)} messages, one for each kind, none for the others",
    )
    now = int(time.time())
    stale = webhook_signing.problem(secret, sent[0]["headers"], sent[0]["body"], now=now + 3600)
    wrong = check("whsec_" + "A" * 43 + "=", sent[0])
    tampered = webhook_signing.problem(secret, sent[0]["headers"], sent[0]["body"] + " ", now=now)
    proof(
        "M48",
        "a receiver can tell a message that is not right: the same message checked an hour later "
        "is too old, with another secret it fits no signature, and with one more character in its "
        "body it fits none either",
        stale == "the timestamp is too far from now"
        and wrong == "no signature fits"
        and tampered == "no signature fits",
        f'"{stale}"',
    )


async def a_receiver_that_fails() -> None:
    """A receiver that fails is tried again, six times and no more, the message keeps its id, and
    the failure shows in words on the owner's list."""
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("fail", tag), ["ask.raised"])
        number, secret = made["webhook"]["id"], made["secret"]
        await an_ask(agent)
        wait_for(lambda: received_at(tag), "the first attempt")
        shown = wait_for(failing(owner, project, number), "the failure to show")
        # The waits between attempts grow (10 seconds, a minute, ...): bring each one forward.
        for attempt in range(2, 7):
            wait_for(at_least(tag, attempt - 1), f"attempt {attempt - 1}")
            run_sql(
                "UPDATE webhook_deliveries SET next_attempt_at = now() "
                f"WHERE webhook_id = {number} AND state = 'pending'",
                project=None,
            )
            wait_for(at_least(tag, attempt), f"attempt {attempt}")
        settle(8)
    log = received_at(tag)
    [gave_up] = deliveries(owner, project, number)
    final = webhook_of(owner, project, number)
    proof(
        "M48",
        "a receiver that answers 500 is tried again and then given up on: six attempts and no "
        "more, every one signed and carrying the same message id (a receiver can tell they are "
        "one message), the owner's list shows the webhook as failing at once, saying in words "
        "what went wrong without the address, and the message as given up on after six tries",
        len(log) == 6
        and len({one["headers"]["webhook-id"] for one in log}) == 1
        and all(check(secret, one) is None for one in log)
        and shown["last_error"] == "The receiver answered 500."
        and shown["pending"] == 1
        and tag not in json.dumps(shown["last_error"])
        and gave_up["state"] == "failed"
        and gave_up["attempts"] == 6
        and gave_up["next_attempt_at"] is None
        and final["failing"] is True
        and final["pending"] == 0,
        f'6 attempts, "{shown["last_error"]}"',
    )


async def changing_and_testing() -> None:
    """A test message says how it went and is not retried; a webhook can be changed, turned off
    and on, its secret replaced, and deleted."""
    owner, project = await a_project()
    good, bad, tag = unique_word(), unique_word(), unique_word()
    fine = add(owner, project, hook("ok", good), ["ask.raised"])
    broken = add(owner, project, hook("fail", bad), ["ask.raised"])
    sent = owner.call("POST", path(project, f"/{fine['webhook']['id']}/test"))
    failed = owner.call("POST", path(project, f"/{broken['webhook']['id']}/test"))
    settle(5)
    ping = received_at(good)
    proof(
        "M48",
        "send a test says at once how it went (delivered, with the receiver's answer, or what "
        "failed), the message is one of kind ping that is signed like the others, a failed test "
        "makes the webhook show as failing, and nothing is sent again",
        sent.status == 200
        and sent.body == {"delivered": True, "status": 200, "words": "The receiver answered 200."}
        and len(ping) == 1
        and json.loads(ping[0]["body"])["type"] == "ping"
        and check(fine["secret"], ping[0]) is None
        and failed.body["delivered"] is False
        and failed.body["status"] == 500
        and webhook_of(owner, project, broken["webhook"]["id"])["failing"] is True
        and len(received_at(bad)) == 1,
        f"{sent.body['words']} and a failure",
    )
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("ok", tag), ["ask.raised"])
        number, old = made["webhook"]["id"], made["secret"]
        changed = owner.call(
            "PATCH", path(project, f"/{number}"), {"events": ["work.done", "ask.raised"]}
        )
        off = owner.call("PATCH", path(project, f"/{number}"), {"enabled": False})
        await an_ask(agent)
        settle()
        while_off = received_at(tag)
        on = owner.call("PATCH", path(project, f"/{number}"), {"enabled": True})
        settle()
        after_on = received_at(tag)
        await an_ask(agent)
        wait_for(lambda: received_at(tag), "a message once it is on again")
        replaced = owner.call("POST", path(project, f"/{number}/secret"))
        new = replaced.body["secret"]
        await an_ask(agent)
        wait_for(lambda: len(received_at(tag)) >= 2, "a message after the secret was replaced")
        run_sql(
            f"UPDATE webhooks SET previous_until = now() - interval '1 minute' WHERE id = {number}",
            project=None,
        )
        await an_ask(agent)
        wait_for(lambda: len(received_at(tag)) >= 3, "a message after the old secret ended")
        later = received_at(tag)
        gone = owner.call("DELETE", path(project, f"/{number}"))
        count = len(received_at(tag))
        await an_ask(agent)
        settle()
    proof(
        "M48",
        "a webhook is changed (its kinds, kept in the order of the list), turned off (nothing is "
        "sent while it is off, and what happened then is not sent when it is turned on again), "
        "and deleted (nothing more is sent and its list is gone)",
        changed.body["webhook"]["events"] == ["ask.raised", "work.done"]
        and off.status == 200
        and while_off == []
        and on.status == 200
        and after_on == []
        and gone.status == 200
        and owner.call("GET", path(project, f"/{number}/deliveries")).status == 404
        and len(received_at(tag)) == count,
        "off, on, deleted",
    )
    proof(
        "M48",
        "replacing a secret shows the new one once and keeps the old one signing too for a day, so "
        "a receiver can change over: the next message carries two signatures that the old and "
        "the new secret each check, and after the day only the new one signs and the old one "
        "fits nothing",
        replaced.status == 200
        and new != old
        and replaced.body["webhook"]["replacing_secret_until"] is not None
        and len(later[1]["headers"]["webhook-signature"].split()) == 2
        and check(old, later[1]) is None
        and check(new, later[1]) is None
        and len(later[2]["headers"]["webhook-signature"].split()) == 1
        and check(new, later[2]) is None
        and check(old, later[2]) == "no signature fits",
        "two signatures, then one",
    )


async def other_news() -> None:
    """Work that may be ready is one nudge however much changed, and a claim that runs out is news
    once, a time passing and not an event."""
    owner, project = await a_project()
    ready, lapse = unique_word(), unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("ok", ready), ["work.available"])
        number = made["webhook"]["id"]
        epic = await ok(agent, "create_node", kind="epic", title="E")
        for index in range(6):
            await ok(agent, "create_node", kind="story", title=f"S{index}", parent=epic["ref"])
        wait_for(lambda: received_at(ready), "the nudge")
        settle(5)
        first = len(received_at(ready))
        kinds = [d["type"] for d in deliveries(owner, project, number)]
        time.sleep(10)
        await ok(agent, "create_node", kind="story", title="Later", parent=epic["ref"])
        wait_for(lambda: len(received_at(ready)) >= 2, "the next nudge")
        first_body = json.loads(received_at(ready)[0]["body"])
    proof(
        "M48",
        "work that may be ready is one nudge, work.available, however many things changed (six "
        "stories made at once): it is a nudge to read ready_work, not a list of what is ready, so "
        "it holds only the same few numbers as the other messages and no title, and later news "
        "is a new nudge",
        first == 1
        and kinds == ["work.available"]
        and first_body["type"] == "work.available"
        and set(first_body["data"]) == {"project", "event_id", "node", "object"}
        and check(made["secret"], received_at(ready)[0]) is None,
        "one nudge for six stories",
    )
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("ok", lapse), ["claim.lapsed"])
        _, story, task = await make_tree(agent)
        other = await ok(agent, "create_node", kind="task", title="Other", parent=story["ref"])
        await ok(agent, "claim_node", node=task["ref"], lease_seconds=2)
        await ok(agent, "claim_node", node=other["ref"], lease_seconds=2)
        await ok(agent, "finish_work", node=other["ref"], outcome="done", note="In time.")
        wait_for(lambda: received_at(lapse), "the lapse", seconds=45)
        settle(8)
    got = received_at(lapse)
    body = json.loads(got[0]["body"])
    proof(
        "M48",
        "a claim that runs out is news once (claim.lapsed, about the work it held, with no event "
        "number because a time passing is not an event) and a claim that was finished in time is "
        "not",
        len(got) == 1
        and body["type"] == "claim.lapsed"
        and body["data"]["node"] == task["ref"]
        and body["data"]["event_id"] is None
        and check(made["secret"], got[0]) is None,
        f"{task['ref']} once",
    )


def a_member(owner: Person, project: Project, address: str, role: str = "viewer") -> Person:
    made = owner.call("POST", f"/board/api/projects/{project.id}/invites", {"role": role})
    if made.status != 201:
        raise Failed(f"M48: the invitation was refused: {made.body}")
    person = register_as(address)
    joined = person.call("POST", f"/board/api/invites/{made.body['token']}/accept")
    if joined.status != 200:
        raise Failed(f"M48: the invitation was not accepted: {joined.body}")
    return person


def confirmed(owner: Person, project: Project) -> tuple[Person, str]:
    """A member whose address is confirmed, as a person confirms it: with the link in the mail."""
    address = f"{unique_word()}@example.test"
    person = a_member(owner, project, address)
    mail = wait_for(lambda: sent_to(address, "Confirm your email"), "the confirmation mail")
    if verify_with(token_of(link_of(mail[0]))).status != 200:
        raise Failed("M48: the address was not confirmed")
    return person, address


async def email_nudges() -> None:
    """An email nudge goes only to the person who asked, with a confirmed address, says what kind
    of news it is and nothing anyone wrote, and stops when they ask it to."""
    owner, project = await a_project()
    asker, address = confirmed(owner, project)
    quiet, quiet_address = confirmed(owner, project)
    unconfirmed = a_member(owner, project, f"{unique_word()}@example.test", "editor")
    written = unique_word()
    where = f"/board/api/projects/{project.id}/nudges"
    listed = asker.call("GET", "/board/api/nudges")
    refused_first = unconfirmed.call("PUT", where, {"events": ["ask.raised"]})
    unknown = asker.call("PUT", where, {"events": ["work.available"]})
    chosen = asker.call("PUT", where, {"events": ["ask.raised", "work.blocked"]})
    async with connect(project.mcp_url, project.key) as agent:
        _, story, _ = await make_tree(agent)
        task = await ok(
            agent, "create_node", kind="task", title=f"Private {written}", parent=story["ref"]
        )
        await ok(agent, "claim_node", node=task["ref"])
        await ok(
            agent, "finish_work", node=task["ref"], outcome="blocked", note=f"Note {written}",
            ask={"question": f"Question {written}?", "choices": ["Yes", "No"]},
        )  # fmt: skip
        asked = wait_for(
            lambda: sent_to(address, "A question or an approval"), "the mail about the question"
        )
        blocked = wait_for(lambda: sent_to(address, "Work was blocked"), "the mail about the work")
        settle(6)
    asked_all = sent_to(address, "A question or an approval")
    blocked_all = sent_to(address, "Work was blocked")
    elsewhere = [
        m
        for who in (quiet_address,)
        for m in sent_to(who)
        if not m["subject"].startswith("Confirm")
    ]
    off = asker.call("PUT", where, {"events": []})
    proof(
        "M48",
        "an email nudge is asked for on the Account page's route by any member, even a viewer, "
        "and only with a confirmed address (an unconfirmed one is told to confirm it first, and a "
        "kind that is not one of the five is refused in words); when an agent raises a question "
        "and blocks work, the person who asked gets one mail for each kind they chose, the "
        "message says the kind and links to the board's page for it with the story or task's "
        "reference, it holds nothing anyone wrote (no title, question or note) and no dash "
        "character, and nobody else is mailed, not even another confirmed member of the project",
        listed.status == 200
        and listed.body["mail"] is True
        and listed.body["email_confirmed"] is True
        and listed.body["nudges"] == []
        and set(listed.body["kinds"])
        == {"ask.raised", "plan.proposed", "placement.proposed", "work.blocked", "claim.lapsed"}
        and refused_first.status == 400
        and "Confirm your email address first" in refused_first.body["message"]
        and unknown.status == 400
        and "not a kind of news" in unknown.body["message"]
        and chosen.status == 200
        and chosen.body["nudge"]
        == {"project": project.id, "events": ["ask.raised", "work.blocked"]}
        and len(asked_all) == 1
        and len(blocked_all) == 1
        and all(
            written not in m["text"]
            and written not in m["subject"]
            and "\u2014" not in m["text"]
            and "\u2013" not in m["text"]
            and f"/board/p/{project.id}#/" in m["text"]
            for m in asked + blocked
        )
        and f"#/item?item={task['ref']}" in blocked[0]["text"]
        and "#/needs" in asked[0]["text"]
        and elsewhere == []
        and quiet is not None
        and off.status == 200
        and asker.call("GET", "/board/api/nudges").body["nudges"] == [],
        f"{len(asked_all) + len(blocked_all)} mails, to the one who asked",
    )


async def the_news_arrives() -> None:
    """The chapter of `working`: what is sent, and to whom."""
    chapter("The news: signed webhooks and email nudges (M48)")
    await signed_messages()
    await a_receiver_that_fails()
    await changing_and_testing()
    await other_news()
    await email_nudges()


# -- where it may go, the secret, and who may (a chapter of `hardening`) -----------------------


REFUSED_ADDRESSES = (
    ("https://127.0.0.1/hook", "loopback"),
    ("https://localhost/hook", "loopback"),
    ("https://[::1]/hook", "loopback"),
    ("https://10.0.0.5/hook", "private network"),
    ("https://192.168.1.10/hook", "private network"),
    ("https://172.20.0.1/hook", "private network"),
    ("https://169.254.169.254/latest/meta-data/", "link-local"),
    ("https://100.64.0.1/hook", "not a public address"),
    ("https://db/hook", "private network"),
    ("https://app/hook", "private network"),
    ("https://user:pass@hooks.example.com/hook", "user name or a password"),
    ("hooks.example.com/hook", "https://"),
    ("ftp://hooks.example.com/hook", "https://"),
    ("https://nowhere.invalid/hook", "does not resolve"),
)


async def where_it_may_go() -> None:
    owner, project = await a_project()
    said: list[str] = []
    wrong: list[str] = []
    for url, wanted in REFUSED_ADDRESSES:
        reply = owner.call("POST", path(project), {"url": url, "events": ["ask.raised"]})
        message = reply.body.get("message", "") if isinstance(reply.body, dict) else ""
        said.append(message)
        if not (
            reply.status == 400
            and wanted in message
            and "Nothing was saved" in message
            and (url[8:40] not in message or url.startswith("hooks"))
        ):
            wrong.append(url)
    nothing = owner.call("GET", path(project)).body["webhooks"] == []
    proof(
        "M48",
        f"an address a webhook may not go to is refused when it is saved, in words that say why "
        f'and end "Nothing was saved", without repeating the address: {len(REFUSED_ADDRESSES)} '
        "cases, each of them this machine (127.0.0.1, localhost, ::1), a private network (10, "
        "192.168, 172.16, and the names of this stack's own services, which resolve to one), the "
        "metadata address 169.254.169.254, a shared address, an address with a user name and "
        "password, one that is not https, and a name that does not resolve; and the list is "
        "still empty",
        not wrong and nothing,
        f'"{clip(said[6], 90)}"' + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )

    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("redirect", tag), ["ask.raised"])
        number = made["webhook"]["id"]
        await an_ask(agent)
        shown = wait_for(failing(owner, project, number), "the failure to show")
        settle(5)
    followed = [one for one in received() if one["mode"] == "after-redirect"]
    proof(
        "M48",
        "a receiver that answers with a redirect is a failure and the redirect is never followed: "
        "the owner's list says so in words, the receiver was asked once, and the place it pointed "
        "to was never visited",
        "redirect" in shown["last_error"]
        and "not followed" in shown["last_error"]
        and [one["mode"] for one in received_at(tag)] == ["redirect"]
        and not followed,
        f'"{shown["last_error"]}"',
    )


async def the_secret_is_seen_once() -> None:
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("ok", tag), ["ask.raised"])
        secret, number = made["secret"], made["webhook"]["id"]
        await an_ask(agent)
        wait_for(lambda: received_at(tag), "the message")
        settle(4)
        listed = json.dumps(owner.call("GET", path(project)).body)
        history = json.dumps(deliveries(owner, project, number))
        exported = json.dumps(await export_everything(agent))
        denied = run_sql(
            "SELECT count(*) FROM webhooks", plain=True, as_project=True, project=project.id
        )
        in_a_note = words(
            await refused(agent, "add_note", text=f"The secret is {secret}", why="Because.")
        )
        again = owner.call("POST", path(project, f"/{number}/secret"))
    # Two copies of the server share the database and both run the job that sends (the check
    # stack's second copy is set up like the first), so the delivery is in either log.
    logs = compose("logs", "--no-log-prefix", "--no-color", "app", "app-dex")
    log = logs.stdout + logs.stderr
    held = {
        "the list": secret not in listed and "secret" not in json.loads(listed)["webhooks"][0],
        "the messages": secret not in history,
        "the export": secret not in exported,
        "the table is out of reach": denied.returncode != 0
        and "permission denied" in denied.stderr,
        "a note is refused": "what looks like a webhook signing secret" in in_a_note,
        "a new secret": again.status == 200 and again.body["secret"] != secret,
        "the log": logs.returncode == 0
        and secret not in log
        and tag not in log
        and "scripted-webhooks" not in log
        and "webhook_delivered" in log,
    }
    proof(
        "M48",
        "the secret is shown once, in the answer that makes the webhook (or replaces its secret) "
        "and never again: it is in no list of webhooks, no list of messages, no export of the "
        "project and no line of the server's log (which names a delivery and how it ended, never "
        "the address, the tag in it or the secret), the project's own database role cannot even "
        "read the table, and an agent that tries to write a secret of that form into a note is "
        "refused",
        all(held.values()),
        "a list, a history, an export and the log, none with the secret"
        if all(held.values())
        else f"wrong: {[name for name, fine in held.items() if not fine]}",
    )


async def a_slow_address() -> None:
    owner, project = await a_project()
    tag = unique_word()
    async with connect(project.mcp_url, project.key) as agent:
        made = add(owner, project, hook("slow", tag), ["work.available", "ask.raised"])
        number = made["webhook"]["id"]
        _, story, _ = await make_tree(agent)
        started = time.monotonic()
        for index in range(8):
            await ok(agent, "create_node", kind="task", title=f"Quick {index}", parent=story["ref"])
        took = time.monotonic() - started
        shown = wait_for(
            failing(owner, project, number), "the slow address to be given up on", seconds=45
        )
    proof(
        "M48",
        "an address that takes 20 seconds to answer never slows a tool call: eight calls that "
        "make news for it take less than eight seconds in all, because sending is done by a "
        "background job and not inside the request, and the receiver that was too slow is given up "
        "on in time and shown as failing in words",
        took < 8 and shown["last_error"] == "The receiver did not answer in time.",
        f'8 calls in {took:.1f} seconds; "{shown["last_error"]}"',
    )


async def only_the_owner() -> None:
    owner, project = await a_project()
    made = add(owner, project, hook("ok", unique_word()), ["ask.raised"])
    number = made["webhook"]["id"]
    editor = a_member(owner, project, f"{unique_word()}@example.test", "editor")
    viewer = a_member(owner, project, f"{unique_word()}@example.test", "viewer")
    stranger = register()
    calls = (
        ("GET", "", None),
        ("POST", "", {"url": hook("ok", unique_word()), "events": ["ask.raised"]}),
        ("PATCH", f"/{number}", {"enabled": False}),
        ("DELETE", f"/{number}", None),
        ("POST", f"/{number}/secret", None),
        ("POST", f"/{number}/test", None),
        ("GET", f"/{number}/deliveries", None),
    )
    wrong: list[str] = []
    for who, name in ((editor, "an editor"), (viewer, "a viewer"), (stranger, "a stranger")):
        for method, tail, body in calls:
            reply = who.call(method, path(project, tail), body)
            if reply.status not in (400, 403, 404) or made["secret"] in json.dumps(reply.body):
                wrong.append(f"{name}: {method} {tail or '/'} {reply.status}")
    no_session = api(server_base(), "GET", path(project)).status
    still = webhook_of(owner, project, number)
    proof(
        "M48",
        "only the project's owner has webhooks: an editor, a viewer and a person who is not in "
        "the project are each refused every route (listing, adding, changing, replacing the "
        "secret, testing, deleting and reading the messages) and none of the answers holds the "
        "secret, a request with no session is turned away, and the webhook is as it was",
        not wrong and no_session == 401 and still["enabled"] is True,
        f"{len(calls) * 3} refused calls" + (f"; wrong: {wrong[:2]}" if wrong else ""),
    )


async def what_is_wrong_is_said() -> None:
    owner, project = await a_project()
    url = hook("ok", unique_word())
    cases: list[tuple[dict[str, Any], str]] = [
        ({"url": url}, "events is a list"),
        ({"url": url, "events": []}, "events is a list"),
        ({"url": url, "events": ["nonsense"]}, "'nonsense' is not a kind of news"),
        ({"events": ["ask.raised"]}, "url is the address"),
    ]
    wrong: list[str] = []
    for body, wanted in cases:
        reply = owner.call("POST", path(project), body)
        if reply.status != 400 or wanted not in reply.body["message"]:
            wrong.append(wanted)
    choices = owner.call("POST", path(project), {"url": url, "events": ["nonsense"]})
    empty = owner.call("GET", path(project)).body["webhooks"] == []
    add(owner, project, url, ["ask.raised"])
    twice = owner.call("POST", path(project), {"url": url, "events": ["work.done"]})
    for index in range(9):
        add(owner, project, hook("ok", f"more-{index}-{unique_word()}"), ["work.done"])
    over = owner.call(
        "POST", path(project), {"url": hook("ok", unique_word()), "events": ["work.done"]}
    )
    listing = owner.call("GET", path(project)).body
    proof(
        "M48",
        "what is wrong with a webhook is said in words and changes nothing (no kind chosen, a "
        "kind that does not exist with the choices listed, no address), the same address twice "
        "is refused, and a project may have at most 10; the server names the ten kinds of news "
        "in the owner's list so a page never has to write its own",
        not wrong
        and "Choose from: ask.raised" in choices.body["message"]
        and empty
        and twice.status == 400
        and "already has a webhook for that address" in twice.body["message"]
        and over.status == 400
        and "at most 10 webhooks" in over.body["message"]
        and len(listing["webhooks"]) == 10
        and listing["most"] == 10
        and set(listing["kinds"]) == set(EVERYTHING),
        "4 refusals, a repeat and the tenth",
    )


async def webhook_limits() -> None:
    """The chapter of `hardening`: where an address may go, a redirect, the secret, a slow address
    and who may have them."""
    chapter("Being careful with webhooks: where they may go, the secret, who may have them (M48)")
    await where_it_may_go()
    await the_secret_is_seen_once()
    await a_slow_address()
    await only_the_owner()
    await what_is_wrong_is_said()
