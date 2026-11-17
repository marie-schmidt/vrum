"""M16's Activity drawer against the running containers, driven in a real headless browser and
checked against a real MCP client (see ./check.sh): what agents do shows in plain words within
seconds, in the drawer over any view, with the handoff note in full; the new fact and decision
actions say who did what and on whose word; who is working on what follows claims with a countdown
to their leases; a person takes, renews and releases work under a typed name; and the feed's
filters (in the address, with the prefix act_) give what `changes_since` gives. Work is named by
its reference to the tools (`task:40`) and by its code in the words (`T-40`); the drawer's item
filter and its claim box take either, or the words (`task 40`). Names and titles carry a unique
word, so each test looks only at what it made."""

import datetime
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import SLOW, board_url, browser_page, made, open_editor, plain
from tests.board_m14 import events_of, feed_becomes, feed_ids, spoken
from tests.board_m14 import feed_row as row
from tests.board_search import VIEWS, address, drawer, keyboard_to_activity, on_view
from tests.helpers import (
    code_of,
    confirm,
    day,
    make_decision,
    make_fact,
    ok,
    refused,
    the_token,
    unique_word,
)
from tests.helpers import number_of as number

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NEWS = 15_000  # milliseconds: the board looks for news every five seconds, and the spec allows ten


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


# -- an agent's work, in plain words ---------------------------------------------------------------


async def test_what_an_agent_does_shows_in_plain_words_with_the_note_in_full(
    client: Client, board: str
) -> None:
    word = unique_word()
    agent = f"agent-{word}"
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Work {word}", story)
    rule = await ok(client, "create_rule", actor="a", title=f"Rule {word}", story=story)
    note = f"Built the whole thing for {word}. Nothing is left, but check the logs."

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        # The drawer opens over the Roadmap, beside it, and the Roadmap stays.
        await w.open(f"#/roadmap?drawer=activity&act_actor={agent}")
        await expect(drawer(page, "activity")).to_be_visible(timeout=SLOW)
        await on_view(page, "roadmap")
        await expect(page.locator("#activity-count")).to_have_text(
            "Nothing has happened yet.", timeout=SLOW
        )
        await page.evaluate("window.__same = 'same page'")

        taken = await ok(client, "next_work", actor=agent, within_node=epic)
        assert taken["found"] and taken["work"]["ref"] == task
        # In plain words, with the actor, and the claim in who is working on what with a countdown.
        await expect(row(page, f"took task {code_of(task)} “Work {word}”")).to_contain_text(
            agent, timeout=NEWS
        )
        claim = page.locator(f"#claims-live li.claim[data-node='{task}']")
        await expect(claim).to_contain_text(f"held by {agent}", timeout=NEWS)
        await expect(claim.locator(".countdown")).to_contain_text("left")
        agent_row = page.locator(f"#agents li.agent[data-actor='{agent}']")
        await expect(agent_row).to_have_attribute("data-state", "working")
        await expect(agent_row).to_contain_text("Working")

        await ok(
            client,
            "update_rule",
            actor=agent,
            rule_id=rule["id"],
            expected_version=1,
            title=f"Renamed {word}",
        )
        decided = await make_decision(client, f"Decided {word}", actor=agent, nodes=[story])
        await ok(client, "finish_work", actor=agent, node=task, outcome="done", note=note)

        await expect(row(page, f"changed requirement {rule['id']}")).to_contain_text(
            f"title: “Rule {word}” → “Renamed {word}”", timeout=NEWS
        )
        await expect(row(page, f"recorded decision {decided['id']}")).to_contain_text(
            f"“Decided {word}”, decided by Marie", timeout=NEWS
        )
        finished = f"finished their work on task {code_of(task)}"
        done = row(page, finished)
        await expect(done).to_contain_text(f"{agent} {finished}", timeout=NEWS)
        await expect(done).to_contain_text("(done) and left this handoff note")
        # The note is shown in full, in the row.
        assert spoken(await done.locator(".handoff-note").inner_text()) == note
        await expect(row(page, f"let go of task {code_of(task)}")).to_be_visible()

        # The claim is gone, and the agent now says finished with a handoff note.
        await expect(claim).to_have_count(0, timeout=NEWS)
        await expect(agent_row).to_have_attribute("data-state", "finished", timeout=NEWS)
        await expect(agent_row).to_contain_text("Last handoff: done")
        # It was all done with no reload, and the feed is newest first and is the server's.
        assert await page.evaluate("window.__same") == "same page"
        shown = await feed_ids(page)
        assert shown == sorted(shown, reverse=True)
        assert shown == [e["id"] for e in reversed(await events_of(client, actor=agent))]
        assert w.problems == []


async def test_facts_and_decisions_are_told_with_who_did_what_and_on_whose_word(
    client: Client, board: str
) -> None:
    word = unique_word()
    agent, person = f"agent-{word}", f"Ada {word}"
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    other = await made(client, "story", f"Other {word}", epic)
    decision = await make_decision(
        client, f"Use email {word}", actor=agent, nodes=[story], project_wide=True
    )
    fact = await make_fact(client, f"People log in on phones {word}", actor=agent, nodes=[story])
    await confirm(client, "decision", decision, person, actor=agent)
    await confirm(client, "fact", fact, person, actor=agent)
    await ok(
        client,
        "set_confirmation",
        actor=agent,
        kind="fact",
        record_id=fact["id"],
        confirmation="unconfirmed",
        person=person,
        quote="I am not sure of this any more.",
    )
    replacement = await ok(
        client,
        "supersede_decision",
        actor=agent,
        decision_id=decision["id"],
        title=f"Use a code {word}",
        text="Log in with a code.",
        reason="Marie changed her mind.",
        decided_by="Marie",
        said_by=person,
        quote="Let us have both.",
    )
    second = await make_fact(client, f"Codes arrive quickly {word}", actor=agent)
    await ok(client, "link_fact", actor=agent, fact_id=second["id"], nodes=[other])
    await ok(client, "unlink_fact", actor=agent, fact_id=second["id"], nodes=[other])
    await confirm(client, "fact", second, person, actor=agent)
    await ok(
        client,
        "withdraw_fact",
        actor=agent,
        fact_ids=[second["id"]],
        reason="It was a guess",
        said_by=person,
        quote="No, we never measured it.",
    )
    events = await events_of(client, actor=agent)
    actions = [e["action"] for e in events]
    for action in (
        "fact_recorded",
        "fact_linked",
        "fact_unlinked",
        "fact_confirmed",
        "fact_unconfirmed",
        "fact_withdrawn",
        "decision_recorded",
        "decision_linked",
        "decision_confirmed",
        "decision_superseded",
    ):
        assert action in actions, action

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/graph?drawer=activity&act_actor={agent}")
        await expect(page.locator("#feed li.event")).to_have_count(len(events), timeout=SLOW)
        assert await feed_ids(page) == [e["id"] for e in reversed(events)]

        async def said(action: str, record: int | None = None) -> str:
            """What the feed says of the first event of this action (about this record)."""
            found = page.locator(f"#feed li.event[data-action={action}]")
            if record is not None:
                found = found.filter(has=page.locator(f"a.item-link[data-id='{record}']"))
            words = found.first.locator(".event-words")
            await expect(words).to_be_visible()
            return spoken(await words.inner_text())

        fid, did, sid = fact["id"], decision["id"], second["id"]
        t_fact, t_dec = f"“People log in on phones {word}”", f"“Use email {word}”"
        assert await said("decision_recorded", did) == (
            f"recorded decision {did} {t_dec}, decided by Marie, for the whole project."
        )
        assert await said("fact_recorded", fid) == (
            f"recorded fact {fid} {t_fact}, source: The team call."
        )
        # A person other than the actor confirmed it: the sentence says who.
        assert await said("decision_confirmed", did) == (
            f"recorded that {person} confirmed decision {did} {t_dec}."
        )
        assert await said("fact_confirmed", fid) == (
            f"recorded that {person} confirmed fact {fid} {t_fact}."
        )
        assert await said("fact_unconfirmed", fid) == (
            f"took back the confirmation of fact {fid} {t_fact}, on the word of {person}: "
            "“I am not sure of this any more.”."
        )
        assert await said("decision_superseded", did) == (
            f"replaced decision {did} {t_dec} with decision {replacement['id']} "
            f"“Use a code {word}”, on the word of {person}: “Let us have both.”."
        )
        assert await said("fact_withdrawn", sid) == (
            f"withdrew fact {sid} “Codes arrive quickly {word}”: It was a guess, "
            f"on the word of {person}: “No, we never measured it.”."
        )
        linked = await said("fact_linked", sid)
        assert linked == (
            f"linked fact {sid} “Codes arrive quickly {word}” to "
            f"story {code_of(other)} “Other {word}”."
        )
        assert (await said("fact_unlinked", sid)).startswith(f"unlinked fact {sid} ")
        assert w.problems == []

        # Every fact and decision event is of its own kind, and the kinds filter the feed.
        for kind, prefix in (("facts", "fact_"), ("decisions", "decision_")):
            await w.go(f"#/graph?drawer=activity&act_actor={agent}&act_kind={kind}")
            wanted = [e["id"] for e in reversed(events) if e["action"].startswith(prefix)]
            await expect(page.locator("#feed li.event")).to_have_count(len(wanted), timeout=SLOW)
            assert await feed_ids(page) == wanted
            kinds = await page.locator("#feed li.event").evaluate_all(
                "els => [...new Set(els.map(e => e.dataset.kind))]"
            )
            assert kinds == [kind]
        # Following one record gives the server's own answer for it, a fact and a decision each.
        for item, filters in (
            (f"fact+{fid}", {"fact_id": fid}),
            (f"decision:{did}", {"decision_id": did}),
        ):
            await w.go(f"#/graph?drawer=activity&act_item={item}")
            wanted = [e["id"] for e in reversed(await events_of(client, **filters))]
            await expect(page.locator("#feed li.event")).to_have_count(len(wanted), timeout=SLOW)
            assert await feed_ids(page) == wanted
        assert w.problems == []


# -- a person takes work, keeps it, lets it go, and lets it lapse -----------------------------


async def test_a_person_claims_renews_and_releases_under_the_typed_name(
    client: Client, board: str
) -> None:
    word = unique_word()
    name = f"Ada {word}"
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    mine, theirs = [await made(client, "task", f"{n} {word}", story) for n in ("Mine", "Theirs")]
    await ok(client, "claim_node", actor=f"agent-{word}", node=theirs, lease_seconds=900)

    async with browser_page(board, token=the_token(), name=name) as w:
        page = w.page
        await w.open("#/board?drawer=activity")
        await expect(page.locator("#acting-as")).to_contain_text(f"act as {name}", timeout=SLOW)
        await page.locator("#lease").fill("60")
        await page.locator("#claim-id").fill(code_of(mine))
        await page.locator("#claim-by-id-go").click()
        # The drawer says so in words and a countdown, and the server has it under the typed name.
        claim = page.locator(f"#claims-live li.claim[data-node='{mine}']")
        await expect(claim).to_contain_text(f"held by {name}", timeout=NEWS)
        node = await ok(client, "get_node", node=mine)
        assert node["claimed_by"] == name
        await expect(row(page, f"{name} took task {code_of(mine)}")).to_be_visible(timeout=NEWS)
        await expect(page.locator(f"#agents li.agent[data-actor='{name}']")).to_have_attribute(
            "data-state", "working"
        )
        before = (await ok(client, "get_node", node=mine))["claim_expires_at"]

        # Renew keeps it for the default lease, which is much longer than the 60 seconds.
        await claim.locator("button.renew").click()
        await expect(row(page, f"{name} kept task {code_of(mine)}")).to_be_visible(timeout=NEWS)
        after = (await ok(client, "get_node", node=mine))["claim_expires_at"]
        gained = datetime.datetime.fromisoformat(after) - datetime.datetime.fromisoformat(before)
        assert gained > datetime.timedelta(minutes=10)

        # Releasing leaves the note the person typed, and the work is free again.
        await claim.locator("button.release").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#release-note").fill(f"Stopped here for {word}.")
        await dialog.get_by_role("button", name="Release it").click()
        await expect(dialog).to_have_count(0, timeout=SLOW)
        released = await ok(client, "get_node", node=mine)
        assert released["claimed_by"] is None and released["status"] == "ready"
        assert released["last_handoff"]["actor"] == name
        assert released["last_handoff"]["note"] == f"Stopped here for {word}."
        done = row(page, f"{name} finished their work on task {code_of(mine)}")
        await expect(done.locator(".handoff-note")).to_have_text(
            f"Stopped here for {word}.", timeout=NEWS
        )
        await expect(claim).to_have_count(0, timeout=NEWS)

        # Somebody else's claim cannot be released by this name: the dialog says what the server
        # says.
        theirs_row = page.locator(f"#claims-live li.claim[data-node='{theirs}']")
        await expect(theirs_row).to_be_visible(timeout=NEWS)
        await theirs_row.locator("button.release").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#release-note").fill("Not mine to release.")
        await dialog.get_by_role("button", name="Release it").click()
        problem = dialog.locator("form > .problem")
        await expect(problem).not_to_have_text("", timeout=SLOW)
        plain(
            await problem.inner_text(),
            await refused(client, "release_claim", actor=name, node=theirs, note="Not mine."),
        )
        assert (await ok(client, "get_node", node=theirs))["claimed_by"] == f"agent-{word}"
        await dialog.get_by_role("button", name="Cancel").click()
        assert w.problems == []


async def test_a_claim_that_runs_out_moves_to_lapsed_and_the_state_follows(
    client: Client, board: str
) -> None:
    word = unique_word()
    name = f"Lee {word}"
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Short lease {word}", story)

    async with browser_page(board, token=the_token(), name=name) as w:
        page = w.page
        await w.open("#/graph?drawer=activity")
        await page.locator("#lease").fill("3")
        # The words a person writes for the kind and the number work as well as the code.
        await page.locator("#claim-id").fill(f"task {number(task)}")
        await page.locator("#claim-by-id-go").click()
        live = page.locator(f"#claims-live li.claim[data-node='{task}']")
        await expect(live).to_be_visible(timeout=NEWS)
        agent_row = page.locator(f"#agents li.agent[data-actor='{name}']")
        await expect(agent_row).to_have_attribute("data-state", "working", timeout=NEWS)

        # Nothing is written when a claim runs out, and the drawer still moves it, by itself.
        lapsed = page.locator(f"#claims-lapsed li.claim[data-node='{task}']")
        await expect(lapsed).to_be_visible(timeout=NEWS)
        await expect(lapsed.locator(".countdown")).to_contain_text("ran out")
        await expect(live).to_have_count(0)
        await expect(agent_row).to_have_attribute("data-state", "lapsed")
        node = await ok(client, "get_node", node=task)
        assert node["claimed_by"] is None and node["lapsed_claim_by"] == name
        who = {a["actor"]: a for a in (await ok(client, "who_is_working"))["agents"]}
        assert who[name]["state"] == "lapsed"
        assert [h["node"] for h in who[name]["lapsed"]] == [task]
        # The holder can still let go of it, with a note, and then it is neither.
        await lapsed.locator("button.release").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#release-note").fill("It ran out on me.")
        await dialog.get_by_role("button", name="Release it").click()
        await expect(dialog).to_have_count(0, timeout=SLOW)
        await expect(lapsed).to_have_count(0, timeout=NEWS)
        await expect(agent_row).to_have_attribute("data-state", "finished", timeout=NEWS)
        assert w.problems == []


async def test_the_free_list_is_the_servers_ready_work_and_a_bad_ref_is_refused_in_its_words(
    client: Client, board: str
) -> None:
    free = [n["ref"] for n in (await ok(client, "ready_work", limit=10))["items"]]
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board?drawer=activity")
        listed = page.locator("#free li.claim")
        await expect(listed).to_have_count(len(free), timeout=SLOW)
        assert [await e.get_attribute("data-node") for e in await listed.all()] == free
        # A bare number names nothing, since each kind is numbered on its own; and a task that
        # does not exist is refused too. Each time the page says what the server says (the newest
        # notice is the first one).
        notices = page.locator("#notices .notice.refusal")
        for count, bad in enumerate(("999999999", "task:999999999"), start=1):
            await page.locator("#claim-id").fill(bad)
            await page.locator("#claim-by-id-go").click()
            await expect(notices).to_have_count(count, timeout=SLOW)
            plain(
                await notices.first.locator(".notice-text").inner_text(),
                await refused(client, "claim_node", actor="Tester", node=bad),
            )
        assert w.problems == []


# -- the feed: filters in the address, items and older changes -------------------------------------


async def test_the_feeds_filters_in_the_address_give_what_changes_since_gives(
    client: Client, board: str
) -> None:
    word = unique_word()
    ann, bob = f"ann-{word}", f"bob-{word}"
    epic = await ok(client, "create_node", actor=ann, kind="epic", title=f"Epic {word}")
    story = await ok(
        client, "create_node", actor=ann, kind="story", title=f"Story {word}", parent=epic["ref"]
    )
    other = await ok(
        client, "create_node", actor=bob, kind="story", title=f"Other {word}", parent=epic["ref"]
    )
    rule = await ok(client, "create_rule", actor=ann, title=f"Rule {word}", story=story["ref"])
    await ok(
        client,
        "move_rule",
        actor=ann,
        rule_id=rule["id"],
        expected_version=1,
        new_story=other["ref"],
    )
    decision = await make_decision(client, f"Decision {word}", actor=bob, nodes=[story["ref"]])
    fact = await make_fact(client, f"A fact {word}", actor=bob, nodes=[story["ref"]])
    await ok(client, "claim_node", actor=bob, node=story["ref"])
    assert await events_of(client, node=epic["ref"])

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board?drawer=activity")

        async def shown(choices: str, events: list[dict[str, Any]]) -> None:
            """Go to an address, and wait until the feed is exactly these events, newest first."""
            wanted = [e["id"] for e in reversed(events)]
            await w.go(f"#/board?drawer=activity&{choices}")
            assert await feed_becomes(page, wanted) == wanted, choices

        # Who: the server's own filter.
        ann_events = await events_of(client, actor=ann)
        await shown(f"act_actor={ann}", ann_events)
        # The kind of change: worked out here, equal to the actions of that kind.
        rules_only = [e for e in ann_events if e["action"].startswith("rule_")]
        await shown(f"act_actor={ann}&act_kind=requirements", rules_only)
        # The spec's own sentence: a requirement moved from one story to another.
        await expect(row(page, f"{ann} moved requirement {rule['id']}")).to_contain_text(
            f"from story {code_of(story['ref'])} “Story {word}” "
            f"to story {code_of(other['ref'])} “Other {word}”"
        )
        bob_events = await events_of(client, actor=bob)
        for kind, prefix in (("decisions", "decision_"), ("facts", "fact_")):
            await shown(
                f"act_actor={bob}&act_kind={kind}",
                [e for e in bob_events if e["action"].startswith(prefix)],
            )
        # An item: a requirement, a decision, a fact and a piece of work each give exactly the
        # server's answer. A piece of work is named by its code, or by its kind and number (a
        # plus sign is a space in an address), or by its reference.
        for item, filters in (
            (f"rule:{rule['id']}", {"rule_id": rule["id"]}),
            (f"decision+{decision['id']}", {"decision_id": decision["id"]}),
            (f"fact:{fact['id']}", {"fact_id": fact["id"]}),
            (code_of(story["ref"]), {"node": story["ref"]}),
            (f"story+{number(story['ref'])}", {"node": story["ref"]}),
            (story["ref"], {"node": story["ref"]}),
        ):
            await shown(f"act_item={item}", await events_of(client, **filters))
        # The days: today keeps everything of today, and a day in the future keeps nothing.
        today = day()
        await shown(f"act_actor={ann}&act_from={today}&act_to={today}", ann_events)
        await shown(f"act_actor={ann}&act_from={day(1)}", [])
        await expect(page.locator("#activity-count")).to_contain_text("Nothing read so far matches")
        await shown(f"act_actor={ann}&act_to={day(-1)}", [])
        # The form does the same as the address, and Clear takes every filter off.
        await w.go("#/board?drawer=activity")
        await page.locator("#a-actor").fill(bob)
        await page.locator("#a-kind").select_option("claims")
        await page.locator("#apply-activity").click()
        claims = [e for e in bob_events if e["action"].startswith("claim")]
        await expect(page.locator("#feed li.event")).to_have_count(len(claims), timeout=SLOW)
        choices = (await address(page))[1]
        assert (choices["act_actor"], choices["act_kind"], choices["drawer"]) == (
            bob,
            "claims",
            "activity",
        )
        await page.locator("#clear-activity").click()
        await expect(page.locator("#a-actor")).to_have_value("")
        assert {"act_actor", "act_kind"}.isdisjoint((await address(page))[1])
        # A thing that does not exist is refused in the server's words, and nothing is listed.
        await w.go("#/board?drawer=activity&act_item=rule:999999999")
        await expect(page.locator("#activity-problem")).not_to_have_text("", timeout=SLOW)
        plain(
            await page.locator("#activity-problem").inner_text(),
            await refused(client, "changes_since", rule_id=999999999),
        )
        assert await page.locator("#feed li.event").count() == 0
        await w.go("#/board?drawer=activity&act_item=task:999999999")
        await expect(page.locator("#activity-problem")).not_to_have_text("", timeout=SLOW)
        plain(
            await page.locator("#activity-problem").inner_text(),
            await refused(client, "changes_since", node="task:999999999"),
        )
        assert await page.locator("#feed li.event").count() == 0
        assert w.problems == []


async def test_older_changes_are_read_when_asked_for_and_the_feed_never_repeats_one(
    client: Client, board: str
) -> None:
    word = unique_word()
    actor = f"busy-{word}"
    epic = await ok(client, "create_node", actor=actor, kind="epic", title=f"Busy {word}")
    for count in range(104):
        await ok(
            client,
            "create_node",
            actor=actor,
            kind="story",
            title=f"S{count} {word}",
            parent=epic["ref"],
        )
    everything = await events_of(client, actor=actor)
    assert len(everything) == 105

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/board?drawer=activity&act_actor={actor}")
        await expect(page.locator("#feed li.event")).to_have_count(100, timeout=SLOW)
        assert await feed_ids(page) == [e["id"] for e in reversed(everything)][:100]
        await expect(page.locator("#activity-count")).to_contain_text(
            "Older changes have not been read yet"
        )
        await page.locator("#show-older").click()
        await expect(page.locator("#feed li.event")).to_have_count(105, timeout=SLOW)
        assert await feed_ids(page) == [e["id"] for e in reversed(everything)]
        await expect(page.locator("#show-older")).to_have_count(0)
        # A new change joins the top at once, once, and the person's place is kept.
        await ok(
            client,
            "create_node",
            actor=actor,
            kind="story",
            title=f"New {word}",
            parent=epic["ref"],
        )
        await expect(page.locator("#feed li.event")).to_have_count(106, timeout=NEWS)
        ids = await feed_ids(page)
        assert len(set(ids)) == 106 and ids == sorted(ids, reverse=True)
        assert w.problems == []


# -- the drawer: from the keyboard, on every view, beside the detail panel -----------------------


async def test_activity_opens_by_keyboard_from_every_view_and_leaves_a_draft_in_the_panel_alone(
    client: Client, board: str
) -> None:
    word = unique_word()
    agent = f"agent-{word}"
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    draft = f"A draft that nobody has saved yet {word}"

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        for view in VIEWS:
            await w.open(f"#/{view}")
            await on_view(page, view)
            # Activity is in the sidebar, right before Settings: Shift+Tab from Settings reaches
            # it, and Enter opens the drawer.
            await keyboard_to_activity(page)
            await expect(page.locator("#activity-open")).to_have_attribute("aria-expanded", "true")
            name, choices = await address(page)
            assert (name, choices["drawer"]) == (view, "activity")
            await expect(page.locator("#drawer-title")).to_be_focused()
            await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
            # Escape closes it and gives the focus back to the button that opened it.
            await page.keyboard.press("Escape")
            await expect(page.locator("#drawer")).to_be_hidden()
            await expect(page.locator("#activity-open")).to_be_focused()
            assert "drawer" not in (await address(page))[1]
            # Ctrl+K and a word of its name open it too.
            await page.keyboard.press("Control+k")
            await page.locator("#switcher-input").fill("Activity")
            await page.keyboard.press("Enter")
            await expect(drawer(page, "activity")).to_be_visible(timeout=SLOW)
            await on_view(page, view)
            await page.locator("#drawer-close").click()
            await expect(page.locator("#drawer")).to_be_hidden()

        # With a record selected, the drawer and the detail panel are open together, the drawer on
        # the left and the panel on the right.
        await w.open(f"#/graph?sel={story}&drawer=activity&act_actor={agent}")
        await expect(page.locator("#panel #card-title")).to_contain_text(
            f"{code_of(story)} ", timeout=SLOW
        )
        await expect(drawer(page, "activity")).to_be_visible()
        boxes = [await page.locator(s).bounding_box() for s in ("#drawer", "#page", "#panel")]
        assert all(boxes)
        left, middle, right = (box["x"] for box in boxes if box)
        assert left < middle < right

        # A draft typed in the panel is still there, in the same field, when the drawer shows news
        # and when it is closed and opened again.
        await open_editor(page)
        await page.locator("#edit-body").fill(draft)
        await page.evaluate("window.__body = document.querySelector('#edit-body')")
        await ok(
            client,
            "create_node",
            actor=agent,
            kind="task",
            title=f"News {word}",
            parent=story,
        )
        await expect(row(page, f"{agent} created task")).to_be_visible(timeout=NEWS)
        for step in ("news", "closed", "opened"):
            if step == "closed":
                await page.locator("#drawer-close").click()
                await expect(page.locator("#drawer")).to_be_hidden()
            if step == "opened":
                await page.locator("#activity-open").click()
                await expect(drawer(page, "activity")).to_be_visible()
            assert await page.evaluate("document.querySelector('#edit-body') === window.__body")
            assert await page.locator("#edit-body").input_value() == draft, step
        await page.get_by_role("button", name="Save changes").click()
        await expect(page.locator("#panel .card-detail")).to_contain_text(draft, timeout=SLOW)
        assert (await ok(client, "get_node", node=story))["body"] == draft
        assert w.problems == []
