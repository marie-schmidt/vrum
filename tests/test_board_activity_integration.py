"""M20's Activity page against the running containers, driven in a real headless browser and
checked against a real MCP client (see ./check.sh): what agents do shows in plain words within
seconds, on the page (`#/activity`, reached from the sidebar or the switcher), with the handoff note
in full; the note actions (a note someone decided says by whom) say who did what and on
whose word; who is working on what follows claims with a countdown to their leases; a person takes,
renews and releases work under their own name (the account they are signed in with); and the
feed's filters (in the address, with the prefix act_) give what
`changes_since` gives. Every line of the feed opens its record's page, in the Activity. Work is
named by its reference to the tools (`task:40`) and by its code in the words (`T-40`); the page's
item filter and its claim box take either, or the words (`task 40`). Names and titles carry a
unique word, so each test looks only at what it made."""

import datetime
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import (
    SLOW,
    board_url,
    browser_page,
    crumbs,
    current_address,
    item_loaded,
    item_page,
    made,
    no_panel_or_drawer,
    on_page,
    open_editor,
    plain,
    sidebar_current,
    view_crumb,
)
from tests.board_m14 import events_of, feed_becomes, feed_ids, spoken
from tests.board_m14 import feed_row as row
from tests.board_search import VIEWS, address, keyboard_to_activity, on_view
from tests.helpers import (
    code_of,
    confirm_on_board,
    connect,
    connect_as,
    connect_each,
    day,
    make_note,
    ok,
    on_board,
    refused,
    the_token,
    unique_word,
)
from tests.helpers import number_of as number
from tests.world import make_project, register

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
    rule = await ok(client, "create_rule", title=f"Rule {word}", node=story)
    note = f"Built the whole thing for {word}. Nothing is left, but check the logs."

    async with connect_as(agent) as bot, browser_page(board, token=the_token()) as w:
        page = w.page
        # The sidebar's Activity link goes to the Activity page, from the Roadmap, and it is the
        # entry that is marked.
        await w.open("#/roadmap")
        await on_view(page, "roadmap")
        await page.locator("#activity-open").click()
        await on_page(page, "activity")
        assert await sidebar_current(page) == ["activity"]
        assert (await crumbs(page))[2:] == ["Activity"]
        await no_panel_or_drawer(page)
        await page.locator("#a-actor").fill(agent)
        await page.locator("#apply-activity").click()
        assert (await address(page)) == ("activity", {"act_actor": agent})
        await expect(page.locator("#activity-count")).to_have_text("0 changes", timeout=SLOW)
        await page.evaluate("window.__same = 'same page'")

        taken = await ok(bot, "next_work", within_node=epic)
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
            bot,
            "update_rule",
            rule_id=rule["id"],
            expected_version=1,
            title=f"Renamed {word}",
        )
        decided = await make_note(bot, f"Decided {word}", decided_by="Marie", nodes=[story])
        await ok(bot, "finish_work", node=task, outcome="done", note=note)

        await expect(row(page, f"changed acceptance criterion {rule['id']}")).to_contain_text(
            f"title: “Rule {word}” → “Renamed {word}”", timeout=NEWS
        )
        await expect(row(page, f"recorded note {decided['id']}")).to_contain_text(
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
        # A line of the feed opens the record's page, in the Activity; the way back is the
        # Activity with the filter it had.
        took = row(page, f"took task {code_of(task)}")
        await took.locator(f"a.item-link[href*='item={task}']").first.click()
        await item_loaded(page, task)
        assert (await current_address(page))[1]["from"] == "activity"
        assert (await crumbs(page))[2] == "Activity"
        assert await sidebar_current(page) == ["activity"]
        await view_crumb(page).click()
        await on_page(page, "activity")
        assert (await address(page)) == ("activity", {"act_actor": agent})
        assert await feed_becomes(page, shown) == shown
        assert w.problems == []


async def test_notes_are_told_with_who_did_what_and_on_whose_word(board: str) -> None:
    word = unique_word()
    agent, person, reviewer = f"agent-{word}", f"Ada {word}", f"Priya {word}"
    # A project of its own, so that the feed holds only what this test did, and a person to
    # confirm things there (only a person can).
    project = make_project(register(name=person), f"Told {word}")
    async with connect_as(agent, project) as bot, connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Epic {word}")
        story = await made(client, "story", f"Story {word}", epic)
        other = await made(client, "story", f"Other {word}", epic)
        decision = await make_note(
            bot, f"Use email {word}", decided_by="Marie", nodes=[story], project_wide=True
        )
        plain_note = await make_note(
            bot, f"People log in on phones {word}", "The team call", nodes=[story]
        )
        for note in (decision, plain_note):
            confirm_on_board("note", note, reviewer, project=project)
        on_board(
            "set_confirmation",
            project=project,
            note_id=plain_note["id"],
            confirmation="unconfirmed",
            person=reviewer,
            quote="I am not sure of this any more.",
        )
        replaced = await ok(
            bot,
            "supersede_note",
            replaces=[decision["id"]],
            text=f"Use a code {word}\n\nLog in with a code.",
            why="Marie changed her mind.",
            decided_by="Marie",
            said_by=reviewer,
            quote="Let us have both.",
        )
        replacement = replaced["replacement"]
        second = await make_note(bot, f"Codes arrive quickly {word}", "A hunch")
        await ok(bot, "link_note", note_id=second["id"], nodes=[other])
        await ok(bot, "unlink_note", note_id=second["id"], nodes=[other])
        confirm_on_board("note", second, reviewer, project=project)
        await ok(
            bot,
            "withdraw_note",
            note_ids=[second["id"]],
            reason="It was a guess",
            said_by=reviewer,
            quote="No, we never measured it.",
        )
        events = [e for e in await events_of(client) if e["action"].startswith("note_")]
        actions = [e["action"] for e in events]
        for action in (
            "note_recorded",
            "note_linked",
            "note_unlinked",
            "note_confirmed",
            "note_unconfirmed",
            "note_superseded",
            "note_withdrawn",
        ):
            assert action in actions, action
        everything = await events_of(client)

        async with browser_page(project.address, token=project.key) as w:
            page = w.page
            await w.open("#/activity")
            await expect(page.locator("#feed li.event")).to_have_count(
                len(everything), timeout=SLOW
            )
            assert await feed_ids(page) == [e["id"] for e in reversed(everything)]

            async def said(action: str, record: int | None = None) -> str:
                """What the feed says of the first event of this action (about this note)."""
                found = page.locator(f"#feed li.event[data-action={action}]")
                if record is not None:
                    found = found.filter(has=page.locator(f"a.item-link[data-id='{record}']"))
                words = found.first.locator(".event-words")
                await expect(words).to_be_visible()
                return spoken(await words.inner_text())

            pid, did, sid, rid = plain_note["id"], decision["id"], second["id"], replacement["id"]
            t_note, t_dec = f"“People log in on phones {word}”", f"“Use email {word}”"
            # Every note is told as a note, and one someone decided says by whom.
            assert await said("note_recorded", did) == (
                f"recorded note {did} {t_dec}, decided by Marie, for the whole project."
            )
            assert await said("note_recorded", pid) == (
                f"recorded note {pid} {t_note}, why: The team call."
            )
            # A person other than the one who did it confirmed it: the sentence says who.
            assert await said("note_confirmed", did) == (
                f"recorded that {reviewer} confirmed note {did} {t_dec}."
            )
            assert await said("note_confirmed", pid) == (
                f"recorded that {reviewer} confirmed note {pid} {t_note}."
            )
            assert await said("note_unconfirmed", pid) == (
                f"took back the confirmation of note {pid} {t_note}, on the word of {reviewer}: "
                "“I am not sure of this any more”."
            )
            assert await said("note_superseded", did) == (
                f"replaced note {did} {t_dec} with note {rid} "
                f"“Use a code {word}”, on the word of {reviewer}: “Let us have both”."
            )
            assert await said("note_withdrawn", sid) == (
                f"withdrew note {sid} “Codes arrive quickly {word}”: It was a guess, "
                f"on the word of {reviewer}: “No, we never measured it”."
            )
            linked = await said("note_linked", sid)
            assert linked == (
                f"linked note {sid} “Codes arrive quickly {word}” to "
                f"story {code_of(other)} “Other {word}”."
            )
            assert (await said("note_unlinked", sid)).startswith(f"unlinked note {sid} ")
            assert w.problems == []

            # Every note event is of one kind, and the kind filters the feed.
            await w.go("#/activity?act_kind=notes")
            await feed_becomes(page, [e["id"] for e in reversed(events)])
            assert await feed_ids(page) == [e["id"] for e in reversed(events)]
            kinds = await page.locator("#feed li.event").evaluate_all(
                "els => [...new Set(els.map(e => e.dataset.kind))]"
            )
            assert kinds == ["notes"]
            # Following one note gives the server's own answer for it, by its kind and number.
            for item, number_ in (
                (f"note+{pid}", pid),
                (f"note:{did}", did),
            ):
                await w.go(f"#/activity?act_item={item}")
                wanted = [e["id"] for e in reversed(await events_of(client, note_id=number_))]
                assert await feed_becomes(page, wanted) == wanted, item
            assert w.problems == []


# -- a person takes work, keeps it, lets it go, and lets it lapse -----------------------------


async def test_a_person_claims_renews_and_releases_under_their_own_name(board: str) -> None:
    word = unique_word()
    name = f"Ada {word}"
    # The person who is signed in is the one who acts: their account's name, not a typed one.
    project = make_project(register(name=name), f"Claims {word}")
    async with (
        connect(project.mcp_url, project.key) as client,
        connect_as(f"agent-{word}", project) as holder,
    ):
        epic = await made(client, "epic", f"Epic {word}")
        story = await made(client, "story", f"Story {word}", epic)
        mine, theirs = [
            await made(client, "task", f"{n} {word}", story) for n in ("Mine", "Theirs")
        ]
        await ok(holder, "claim_node", node=theirs, lease_seconds=900)

        async with browser_page(project.address, token=project.key) as w:
            page = w.page
            await w.open("#/activity")
            await expect(page.locator("#claiming-as")).to_contain_text(name, timeout=SLOW)
            await page.locator("#lease").fill("60")
            await page.locator("#claim-id").fill(code_of(mine))
            await page.locator("#claim-by-id-go").click()
            # The page says so in words and a countdown, and the server has it under their name.
            claim = page.locator(f"#claims-live li.claim[data-node='{mine}']")
            await expect(claim).to_contain_text(f"held by {name}", timeout=NEWS)
            node = await ok(client, "get_node", node=mine)
            assert node["claimed_by"] == name and node["claimed_by_kind"] == "person"
            await expect(row(page, f"{name} took task {code_of(mine)}")).to_be_visible(timeout=NEWS)
            await expect(page.locator(f"#agents li.agent[data-actor='{name}']")).to_have_attribute(
                "data-state", "working"
            )
            before = (await ok(client, "get_node", node=mine))["claim_expires_at"]

            # Renew keeps it for the default lease, which is much longer than the 60 seconds.
            await claim.locator("button.renew").click()
            await expect(row(page, f"{name} kept task {code_of(mine)}")).to_be_visible(timeout=NEWS)
            after = (await ok(client, "get_node", node=mine))["claim_expires_at"]
            gained = datetime.datetime.fromisoformat(after) - datetime.datetime.fromisoformat(
                before
            )
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
            done = row(page, f"{name} handed task {code_of(mine)}")
            await expect(done.locator(".handoff-note")).to_have_text(
                f"Stopped here for {word}.", timeout=NEWS
            )
            await expect(claim).to_have_count(0, timeout=NEWS)

            # Somebody else's claim has no buttons for this person: only the holder can renew or
            # release it, and the server says so to anyone else who asks.
            theirs_row = page.locator(f"#claims-live li.claim[data-node='{theirs}']")
            await expect(theirs_row).to_contain_text(f"held by agent-{word}", timeout=NEWS)
            assert await theirs_row.locator("button").count() == 0
            assert "not by you" in await refused(
                client, "release_claim", node=theirs, note="Not mine."
            )
            assert (await ok(client, "get_node", node=theirs))["claimed_by"] == f"agent-{word}"
            assert w.problems == []


async def test_a_claim_that_runs_out_moves_to_lapsed_and_the_state_follows(board: str) -> None:
    word = unique_word()
    name = f"Lee {word}"
    project = make_project(register(name=name), f"Lapse {word}")
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Epic {word}")
        story = await made(client, "story", f"Story {word}", epic)
        task = await made(client, "task", f"Short lease {word}", story)

        async with browser_page(project.address, token=project.key) as w:
            page = w.page
            await w.open("#/activity")
            await page.locator("#lease").fill("3")
            # The words a person writes for the kind and the number work as well as the code.
            await page.locator("#claim-id").fill(f"task {number(task)}")
            await page.locator("#claim-by-id-go").click()
            live = page.locator(f"#claims-live li.claim[data-node='{task}']")
            await expect(live).to_be_visible(timeout=NEWS)
            agent_row = page.locator(f"#agents li.agent[data-actor='{name}']")
            await expect(agent_row).to_have_attribute("data-state", "working", timeout=NEWS)

            # Nothing is written when a claim runs out, and the page still moves it, by itself.
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
        await w.open("#/activity")
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
                await refused(client, "claim_node", node=bad),
            )
        assert w.problems == []


# -- the feed: filters in the address, items and older changes -------------------------------------


async def test_the_feeds_filters_in_the_address_give_what_changes_since_gives(
    client: Client, board: str
) -> None:
    word = unique_word()
    ann, bob = f"ann-{word}", f"bob-{word}"
    async with connect_each(ann, bob) as (by_ann, by_bob):
        epic = await ok(by_ann, "create_node", kind="epic", title=f"Epic {word}")
        story = await ok(
            by_ann, "create_node", kind="story", title=f"Story {word}", parent=epic["ref"]
        )
        other = await ok(
            by_bob, "create_node", kind="story", title=f"Other {word}", parent=epic["ref"]
        )
        rule = await ok(by_ann, "create_rule", title=f"Rule {word}", node=story["ref"])
        await ok(by_ann, "place_rule", rule_id=rule["id"], expected_version=1, node=other["ref"])
        decision = await make_note(
            by_bob, f"Decision {word}", decided_by="Marie", nodes=[story["ref"]]
        )
        note = await make_note(by_bob, f"A note {word}", nodes=[story["ref"]])
        await ok(by_bob, "claim_node", node=story["ref"])
    assert await events_of(client, node=epic["ref"])

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/activity")

        async def shown(choices: str, events: list[dict[str, Any]]) -> None:
            """Go to an address, and wait until the feed is exactly these events, newest first."""
            wanted = [e["id"] for e in reversed(events)]
            await w.go(f"#/activity?{choices}")
            assert await feed_becomes(page, wanted) == wanted, choices

        # Who: the server's own filter.
        ann_events = await events_of(client, actor=ann)
        await shown(f"act_actor={ann}", ann_events)
        # The kind of change: worked out here, equal to the actions of that kind.
        rules_only = [e for e in ann_events if e["action"].startswith("rule_")]
        await shown(f"act_actor={ann}&act_kind=requirements", rules_only)
        # The spec's own sentence: an acceptance criterion moved from one story to another.
        await expect(row(page, f"{ann} moved acceptance criterion {rule['id']}")).to_contain_text(
            f"from story {code_of(story['ref'])} “Story {word}” "
            f"to story {code_of(other['ref'])} “Other {word}”"
        )
        bob_events = await events_of(client, actor=bob)
        await shown(
            f"act_actor={bob}&act_kind=notes",
            [e for e in bob_events if e["action"].startswith("note_")],
        )
        # An item: an acceptance criterion, a note and a piece of work each give exactly the
        # server's answer. A piece of work is named by its code, or by its kind and number (a
        # plus sign is a space in an address), or by its reference.
        for item, filters in (
            (f"note+{decision['id']}", {"note_id": decision["id"]}),
            (f"note:{note['id']}", {"note_id": note["id"]}),
            (code_of(story["ref"]), {"node": story["ref"]}),
            (f"story+{number(story['ref'])}", {"node": story["ref"]}),
            (story["ref"], {"node": story["ref"]}),
            (f"criterion+{rule['id']}", {"rule_id": rule["id"]}),
        ):
            await shown(f"act_item={item}", await events_of(client, **filters))
        # The days: today keeps everything of today, and a day in the future keeps nothing.
        today = day()
        await shown(f"act_actor={ann}&act_from={today}&act_to={today}", ann_events)
        await shown(f"act_actor={ann}&act_from={day(1)}", [])
        await expect(page.locator("#feed .feed-empty")).to_contain_text(
            "None of the changes read so far match these filters"
        )
        await shown(f"act_actor={ann}&act_to={day(-1)}", [])
        # The form does the same as the address, and Clear takes every filter off.
        await w.go("#/activity")
        await page.locator("#a-actor").fill(bob)
        await page.locator("#a-kind").select_option("claims")
        await page.locator("#apply-activity").click()
        claims = [e for e in bob_events if e["action"].startswith("claim")]
        await expect(page.locator("#feed li.event")).to_have_count(len(claims), timeout=SLOW)
        name, choices = await address(page)
        assert (name, choices["act_actor"], choices["act_kind"]) == ("activity", bob, "claims")
        await page.locator("#clear-activity").click()
        await expect(page.locator("#a-actor")).to_have_value("")
        assert {"act_actor", "act_kind"}.isdisjoint((await address(page))[1])
        # A thing that does not exist is refused in the server's words, and nothing is listed.
        await w.go("#/activity?act_item=criterion+999999999")
        await expect(page.locator("#activity-problem")).not_to_have_text("", timeout=SLOW)
        plain(
            await page.locator("#activity-problem").inner_text(),
            await refused(client, "changes_since", rule_id=999999999),
        )
        assert await page.locator("#feed li.event").count() == 0
        await w.go("#/activity?act_item=task:999999999")
        await expect(page.locator("#activity-problem")).not_to_have_text("", timeout=SLOW)
        plain(
            await page.locator("#activity-problem").inner_text(),
            await refused(client, "changes_since", node="task:999999999"),
        )
        assert await page.locator("#feed li.event").count() == 0
        assert w.problems == []


async def test_an_acceptance_criterion_named_by_rule_and_its_number_is_followed_not_a_note(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    rule = await ok(client, "create_rule", title=f"Rule {word}", node=story)
    wanted = [e["id"] for e in reversed(await events_of(client, rule_id=rule["id"]))]
    assert wanted
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/activity?act_item=rule:{rule['id']}")
        assert await feed_becomes(w.page, wanted) == wanted
        # An acceptance criterion that is not there is refused in the server's words, not as a note.
        await w.go("#/activity?act_item=rule:999999999")
        await expect(w.page.locator("#activity-problem")).not_to_have_text("", timeout=SLOW)
        plain(
            await w.page.locator("#activity-problem").inner_text(),
            await refused(client, "changes_since", rule_id=999999999),
        )
        assert w.problems == []


async def test_older_changes_are_read_when_asked_for_and_the_feed_never_repeats_one(
    client: Client, board: str
) -> None:
    word = unique_word()
    actor = f"busy-{word}"
    async with connect_as(actor) as busy:
        epic = await ok(busy, "create_node", kind="epic", title=f"Busy {word}")
        for count in range(104):
            await ok(
                busy,
                "create_node",
                kind="story",
                title=f"S{count} {word}",
                parent=epic["ref"],
            )
        everything = await events_of(client, actor=actor)
        assert len(everything) == 105

        async with browser_page(board, token=the_token()) as w:
            page = w.page
            await w.open(f"#/activity?act_actor={actor}")
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
                busy,
                "create_node",
                kind="story",
                title=f"New {word}",
                parent=epic["ref"],
            )
            await expect(page.locator("#feed li.event")).to_have_count(106, timeout=NEWS)
            ids = await feed_ids(page)
            assert len(set(ids)) == 106 and ids == sorted(ids, reverse=True)
            assert w.problems == []


# -- the page: from the keyboard, from every page, and what was typed on it -----------------------


async def test_activity_is_reached_by_keyboard_from_every_view_and_what_was_typed_is_kept(
    client: Client, board: str
) -> None:
    word = unique_word()
    agent = f"agent-{word}"
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    claimed = await made(client, "task", f"Claimed {word}", story)
    draft = f"A draft that nobody has saved yet {word}"

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        for view in VIEWS:
            await w.open(f"#/{view}")
            await on_view(page, view)
            # Activity is a link in the sidebar, right before Settings: Shift+Tab from Settings
            # reaches it, and Enter goes to the page.
            await keyboard_to_activity(page)
            await expect(page.locator("#activity-open")).to_have_attribute("aria-current", "page")
            name, choices = await address(page)
            assert name == "activity" and {"drawer", "sel", "panel"}.isdisjoint(choices), choices
            assert await sidebar_current(page) == ["activity"]
            await no_panel_or_drawer(page)
            await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
            # The browser's way back returns to the view the person came from.
            await page.go_back()
            await on_view(page, view)
            # Ctrl+K and a word of its name go to the page too, and the view's own link comes back.
            await page.keyboard.press("Control+k")
            await page.locator("#switcher-input").fill("Activity")
            await page.keyboard.press("Enter")
            await on_page(page, "activity")
            await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
            await page.locator(f"#main-nav a[data-page={view}]").click()
            await on_view(page, view)

        # A draft typed in the details of a record is still there, in the same field, when news
        # arrives: the news is shown (the new task is in the table below) and the draft is not
        # drawn over.
        await w.open(f"#/item?item={story}&from=activity")
        await item_loaded(page, story)
        await open_editor(page)
        await page.locator("#edit-body").fill(draft)
        await page.evaluate("window.__body = document.querySelector('#edit-body')")
        new = await made(client, "task", f"News {word}", story)
        await expect(item_page(page).locator(f"#item-table tr[data-ref='{new}']")).to_be_visible(
            timeout=NEWS
        )
        assert await page.evaluate("document.querySelector('#edit-body') === window.__body")
        assert await page.locator("#edit-body").input_value() == draft
        await page.get_by_role("button", name="Save changes").click()
        await expect(item_page(page).locator(".card-detail")).to_contain_text(draft, timeout=SLOW)
        assert (await ok(client, "get_node", node=story))["body"] == draft

        # Words typed on the Activity page and not applied (a filter, the claim box and the lease)
        # are still there when news arrives and the page draws the feed and the claims again.
        await view_crumb(page).click()
        await on_page(page, "activity")
        await page.locator("#a-actor").fill("somebody")
        await page.locator("#claim-id").fill(code_of(new))
        await page.locator("#lease").fill("120")
        async with connect_as(agent) as bot:
            await ok(bot, "claim_node", node=claimed)
        await expect(row(page, f"{agent} took task {code_of(claimed)}")).to_be_visible(timeout=NEWS)
        await expect(page.locator(f"#claims-live li.claim[data-node='{claimed}']")).to_be_visible()
        assert await page.locator("#a-actor").input_value() == "somebody"
        assert await page.locator("#claim-id").input_value() == code_of(new)
        assert await page.locator("#lease").input_value() == "120"
        assert "act_actor" not in (await address(page))[1]
        assert w.problems == []
