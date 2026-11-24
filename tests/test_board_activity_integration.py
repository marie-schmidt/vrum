"""M20's Activity page against the running containers, driven in a real headless browser and
checked against a real MCP client (see ./check.sh): what agents do shows in plain words within
seconds, on the page (`#/activity`, reached from the sidebar or the switcher), with the handoff note
in full; the note actions (a note someone decided says by whom, and is named by its short code,
N-7) say who did what and on whose word; who is working on what is one list of people and agents
with each one's claims inside their entry and a countdown to their leases (M35); a person takes,
renews and releases work under their own name (the account they are signed in with); a run of
check ticks by one actor on one item is one row that opens to every check with what was said, whole
(M35); and the feed's filters (in the address, with the prefix act_, behind the Filter button) give
what `changes_since` gives. Every line of the feed opens its record's page, in the Activity. Work is
named by its reference to the tools (`task:40`) and by its code in the words (`T-40`); the page's
item filter and its claim box take either, or the words (`task 40`), and a criterion or a note by
AC-3 or N-7. Names and titles carry a unique word, so each test looks only at what it made."""

import datetime
import re
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
from tests.board_m14 import (
    events_of,
    feed_becomes,
    feed_ids,
    open_activity_filter,
    open_free,
    open_working,
    show_tab,
    spoken,
)
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
        assert (await crumbs(page)) == ["Activity"]
        await no_panel_or_drawer(page)
        # The filter is behind a button and says how many of its boxes are set once it is applied.
        toggle = page.locator("#activity-filter-toggle")
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        await expect(toggle.locator(".filter-count")).to_be_hidden()
        await open_activity_filter(page)
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        await page.locator("#a-actor").fill(agent)
        await page.locator("#apply-activity").click()
        assert (await address(page)) == ("activity", {"act_actor": agent})
        await expect(toggle.locator(".filter-count")).to_have_text("1")
        await expect(page.locator("#activity-count")).to_have_text("0 changes", timeout=SLOW)
        await page.evaluate("window.__same = 'same page'")

        taken = await ok(bot, "next_work", within_node=epic)
        assert taken["found"] and taken["work"]["ref"] == task
        # In plain words, with the actor, and the claim in who is working on what with a countdown:
        # one list of people and agents, with the claim inside the entry of whoever holds it (the
        # Working view of the toggle; the filter in the address stays while the view is changed).
        await expect(row(page, f"took task {code_of(task)} “Work {word}”")).to_contain_text(
            agent, timeout=NEWS
        )
        await open_working(page)
        assert (await address(page)) == ("activity", {"act_actor": agent, "tab": "working"})
        agent_row = page.locator(f"#agents li.agent[data-actor='{agent}']")
        claim = agent_row.locator(f"li.claim[data-node='{task}']")
        await expect(claim).to_have_attribute("data-actor", agent, timeout=NEWS)
        await expect(claim).to_contain_text(f"{code_of(task)} “Work {word}”")
        await expect(claim.locator(".countdown")).to_contain_text("left")
        await expect(agent_row).to_have_attribute("data-state", "working")
        await expect(agent_row).to_contain_text("Working")
        await show_tab(page, "changes")
        assert (await address(page)) == ("activity", {"act_actor": agent})

        await ok(
            bot,
            "update_rule",
            rule_id=rule["id"],
            expected_version=1,
            title=f"Renamed {word}",
        )
        decided = await make_note(bot, f"Decided {word}", decided_by="Marie", nodes=[story])
        await ok(bot, "finish_work", node=task, outcome="done", note=note)

        await expect(row(page, f"changed AC-{rule['id']}")).to_contain_text(
            f"title: “Rule {word}” → “Renamed {word}”", timeout=NEWS
        )
        await expect(row(page, f"recorded N-{decided['id']}")).to_contain_text(
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
        await open_working(page)
        await expect(claim).to_have_count(0, timeout=NEWS)
        await expect(agent_row).to_have_attribute("data-state", "finished", timeout=NEWS)
        await expect(agent_row).to_contain_text("Last handoff: done")
        await show_tab(page, "changes")
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
        assert (await crumbs(page))[0] == "Activity"
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
            # Every note is told by its short code, N-7, and one someone decided says by whom.
            assert await said("note_recorded", did) == (
                f"recorded N-{did} {t_dec}, decided by Marie, for the whole project."
            )
            assert await said("note_recorded", pid) == (
                f"recorded N-{pid} {t_note}, why: The team call."
            )
            # A person other than the one who did it confirmed it: the sentence says who.
            assert await said("note_confirmed", did) == (
                f"recorded that {reviewer} confirmed N-{did} {t_dec}."
            )
            assert await said("note_confirmed", pid) == (
                f"recorded that {reviewer} confirmed N-{pid} {t_note}."
            )
            assert await said("note_unconfirmed", pid) == (
                f"took back the confirmation of N-{pid} {t_note}, on the word of {reviewer}: "
                "“I am not sure of this any more”."
            )
            assert await said("note_superseded", did) == (
                f"replaced N-{did} {t_dec} with N-{rid} "
                f"“Use a code {word}”, on the word of {reviewer}: “Let us have both”."
            )
            assert await said("note_withdrawn", sid) == (
                f"withdrew N-{sid} “Codes arrive quickly {word}”: It was a guess, "
                f"on the word of {reviewer}: “No, we never measured it”."
            )
            linked = await said("note_linked", sid)
            assert linked == (
                f"linked N-{sid} “Codes arrive quickly {word}” to "
                f"story {code_of(other)} “Other {word}”."
            )
            assert (await said("note_unlinked", sid)).startswith(f"unlinked N-{sid} ")
            # No sentence of the feed still says "note 7" or "acceptance criterion 4".
            sentences = await page.locator("#feed .event-words").all_inner_texts()
            assert sentences and not [
                s for s in sentences if re.search(r"\bnote \d|acceptance criterion \d", s)
            ], sentences
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
            await w.open("#/activity?tab=free")
            await expect(page.locator("#claiming-as")).to_contain_text(name, timeout=SLOW)
            await page.locator("#lease").fill("60")
            await page.locator("#claim-id").fill(code_of(mine))
            await page.locator("#claim-by-id-go").click()
            # The notice names the work by its name in curly quotes, with no code.
            await expect(page.locator("#notices .notice-text").first).to_have_text(
                f"Took “Mine {word}”.", timeout=SLOW
            )
            # The page says so in words and a countdown, and the server has it under their name:
            # the claim is in the entry of the person who holds it, in the one list of who is
            # working (the Working view), and no word says "held by" any more.
            await open_working(page)
            agent_row = page.locator(f"#agents li.agent[data-actor='{name}']")
            claim = agent_row.locator(f"li.claim[data-node='{mine}']")
            await expect(claim).to_have_attribute("data-actor", name, timeout=NEWS)
            await expect(claim).to_have_attribute("data-actor-kind", "person")
            await expect(claim).not_to_contain_text("held by")
            await expect(page.locator("#claims-live, #claims-lapsed")).to_have_count(0)
            node = await ok(client, "get_node", node=mine)
            assert node["claimed_by"] == name and node["claimed_by_kind"] == "person"
            await expect(agent_row).to_have_attribute("data-state", "working")
            before = (await ok(client, "get_node", node=mine))["claim_expires_at"]

            # Renew keeps it for the default lease, which is much longer than the 60 seconds.
            await claim.locator("button.renew").click()
            await expect(page.locator("#notices .notice-text").first).to_have_text(
                f"Kept “Mine {word}” for longer.", timeout=SLOW
            )
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
            await expect(claim).to_have_count(0, timeout=NEWS)
            # The feed has all three, by this person, in the Changes view.
            await show_tab(page, "changes")
            await expect(row(page, f"{name} took task {code_of(mine)}")).to_be_visible(timeout=NEWS)
            await expect(row(page, f"{name} kept task {code_of(mine)}")).to_be_visible(timeout=NEWS)
            done = row(page, f"{name} handed task {code_of(mine)}")
            await expect(done.locator(".handoff-note")).to_have_text(
                f"Stopped here for {word}.", timeout=NEWS
            )
            await open_working(page)

            # Somebody else's claim has no buttons for this person: only the holder can renew or
            # release it, and the server says so to anyone else who asks.
            theirs_row = page.locator(
                f"#agents li.agent[data-actor='agent-{word}'] li.claim[data-node='{theirs}']"
            )
            await expect(theirs_row).to_have_attribute("data-actor", f"agent-{word}", timeout=NEWS)
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
            await w.open("#/activity?tab=free")
            await page.locator("#lease").fill("6")
            # The words a person writes for the kind and the number work as well as the code.
            await page.locator("#claim-id").fill(f"task {number(task)}")
            await page.locator("#claim-by-id-go").click()
            await expect(page.locator("#notices .notice-text").first).to_have_text(
                f"Took “Short lease {word}”.", timeout=SLOW
            )
            await open_working(page)
            agent_row = page.locator(f"#agents li.agent[data-actor='{name}']")
            live = agent_row.locator(f"li.claim[data-live='1'][data-node='{task}']")
            await expect(live).to_be_visible(timeout=NEWS)
            await expect(agent_row).to_have_attribute("data-state", "working", timeout=NEWS)

            # Nothing is written when a claim runs out, and the page still moves it, by itself,
            # within the same entry: from the claims in force to the one that ran out.
            lapsed = agent_row.locator(f"li.claim.lapsed[data-live='0'][data-node='{task}']")
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


async def test_who_is_working_is_one_list_of_people_and_agents_with_their_claims_inside_it(
    board: str,
) -> None:
    word = unique_word()
    holder, mixed, finisher = f"holder-{word}", f"mixed-{word}", f"finisher-{word}"
    project = make_project(register(name=f"Ada {word}"), f"Who {word}")
    async with (
        connect(project.mcp_url, project.key) as client,
        connect_as(holder, project) as by_holder,
        connect_as(mixed, project) as by_mixed,
        connect_as(finisher, project) as by_finisher,
    ):
        epic = await made(client, "epic", f"Epic {word}")
        story = await made(client, "story", f"Story {word}", epic)
        tasks = {
            name: await made(client, "task", f"{name} {word}", story)
            for name in ("One", "Two", "Three", "Short", "Done")
        }
        await ok(by_holder, "claim_node", node=tasks["One"], lease_seconds=900)
        await ok(by_holder, "claim_node", node=tasks["Two"], lease_seconds=900)
        await ok(by_mixed, "claim_node", node=tasks["Three"], lease_seconds=900)
        await ok(by_mixed, "claim_node", node=tasks["Short"], lease_seconds=1)
        await ok(by_finisher, "claim_node", node=tasks["Done"])
        await ok(by_finisher, "finish_work", node=tasks["Done"], outcome="done", note="All done.")

        async with browser_page(project.address, token=project.key) as w:
            page = w.page
            await w.open("#/activity?tab=working")
            # The short claim runs out on its own, and the page moves it to the lapsed ones.
            await expect(page.locator("#agents li.claim.lapsed")).to_have_count(1, timeout=NEWS)
            who = (await ok(client, "who_is_working"))["agents"]
            assert {a["actor"] for a in who} == {holder, mixed, finisher}
            # One list of people and agents, a claim in the entry of whoever holds it, the ones in
            # force before the one that ran out: no list of claims of its own and no "held by". The
            # Working view is only this list: the free work is the other view, with its count on the
            # toggle, and the claims have no count heading of their own.
            await expect(page.locator("#claims-live, #claims-lapsed")).to_have_count(0)
            await expect(
                page.locator("#working h3, #working summary, #working .side-count")
            ).to_have_count(0)
            await expect(page.locator("#free")).to_have_count(0)
            entries = page.locator("#agents li.agent")
            await expect(entries).to_have_count(len(who))
            for agent in who:
                entry = page.locator(f"#agents li.agent[data-actor='{agent['actor']}']")
                await expect(entry).to_have_attribute("data-state", agent["state"])
                live = [m["node"] for m in agent["holding"]]
                ran_out = [m["node"] for m in agent["lapsed"]]
                shown = await entry.locator("li.claim").evaluate_all(
                    "els => els.map(e => [e.dataset.node, e.dataset.live, e.dataset.actor])"
                )
                assert shown == [
                    *[[node, "1", agent["actor"]] for node in live],
                    *[[node, "0", agent["actor"]] for node in ran_out],
                ], agent["actor"]
                await expect(entry).not_to_contain_text("held by")
            # A claim is named by the work: its code and title, with no word for the kind.
            one = page.locator(f"#agents li.claim[data-node='{tasks['One']}'] a.item-link")
            assert spoken(await one.inner_text()) == f"{code_of(tasks['One'])} “One {word}”"
            await expect(one).to_have_attribute("data-ref", tasks["One"])
            # The count on the Working button is how many people and agents hold something now
            # (two here), and the one on Free to take is the server's count of the work nobody
            # holds; neither is drawn when it is zero.
            holding = len([a for a in who if a["holding"]])
            assert holding == 2
            working = page.locator("#activity-tab-working")
            await expect(working.locator(".tab-count")).to_have_text(str(holding))
            await expect(working).to_have_attribute(
                "aria-label", f"Who is working on what: {holding}"
            )
            free_total = (await ok(client, "ready_work", limit=1))["total"]
            free_tab = page.locator("#activity-tab-free")
            if free_total:
                await expect(free_tab.locator(".tab-count")).to_have_text(str(free_total))
                await expect(free_tab).to_have_attribute(
                    "aria-label", f"Free to take: {free_total}"
                )
            else:
                await expect(free_tab.locator(".tab-count")).to_be_hidden()
            # The last handoff of whoever left one is a line in their entry, with a link to it.
            finished = page.locator(f"#agents li.agent[data-actor='{finisher}']")
            line = finished.locator("p.agent-handoff")
            await expect(line).to_contain_text(f"Last handoff: done on {code_of(tasks['Done'])}")
            await expect(line.locator(f"a.item-link[data-ref='{tasks['Done']}']")).to_have_count(1)
            assert w.problems == []


async def test_a_run_of_check_ticks_is_one_row_that_opens_to_every_check_and_never_cuts_a_word(
    board: str,
) -> None:
    word = unique_word()
    ticker, other = f"ticker-{word}", f"other-{word}"
    project = make_project(register(name=f"Ada {word}"), f"Ticks {word}")
    checks = [f"Check {name} {word}" for name in ("one", "two", "three", "four", "five")]
    saved = project.owner.call(
        "POST",
        f"/board/api/projects/{project.id}/tool/set_definition_of_done",
        {"body": "\n".join(f"- {text}" for text in checks), "expected_version": 1},
    )
    assert saved.status == 200, saved.body
    long_evidence = (
        f"Ran the whole suite for {word}: "
        + " ".join(f"step {number} passed" for number in range(1, 60))
        + f" and then the very last words {word} are still all there."
    )
    assert len(long_evidence) > 600
    async with (
        connect(project.mcp_url, project.key) as client,
        connect_as(ticker, project) as by_ticker,
        connect_as(other, project) as by_other,
    ):
        epic = await made(client, "epic", f"Epic {word}")
        story = await made(client, "story", f"Story {word}", epic)
        task = await made(client, "task", f"Permits {word}", story)
        keys = {
            one["text"]: one["item"]
            for one in (await ok(client, "get_definition_of_done"))["items"]
        }
        for text, evidence in zip(
            checks[:3], ("Ran it once", long_evidence, "Looked at it"), strict=True
        ):
            await ok(by_ticker, "set_check", node=task, item=keys[text], evidence=evidence)

        def ticks(action: str, actor: str) -> list[dict[str, Any]]:
            return [
                e
                for e in everything
                if e["action"] == action and e["actor"] == actor and e["node"] == task
            ]

        everything = await events_of(client, node=task)

        async with browser_page(project.address, token=project.key) as w:
            page = w.page
            await w.open("#/activity?act_kind=checks")
            feed = page.locator("#feed li.event")
            await expect(feed).to_have_count(1, timeout=SLOW)
            run = page.locator("#feed li.event-run.action-check_ticked")
            await expect(run).to_have_count(1)
            first = ticks("check_ticked", ticker)
            assert len(first) == 3
            await expect(run).to_have_attribute("data-count", "3")
            await expect(run).to_have_attribute("data-event", str(first[-1]["id"]))
            await expect(run).to_have_attribute("data-actor", ticker)
            await expect(run).to_have_attribute("data-kind", "checks")
            words = run.locator(".event-words")
            assert spoken(await words.inner_text()) == (
                f"ticked 3 checks on task {code_of(task)} “Permits {word}”."
            )
            await expect(
                words.locator(f"a.item-link[data-kind=task][data-ref='{task}']")
            ).to_have_count(1)
            # Closed to begin with: the checks are one click away and nothing of them is on show.
            details = run.locator("details.checks-run")
            await expect(details.locator("summary")).to_have_text("Show the checks")
            await expect(details).not_to_have_attribute("open", "")
            await expect(run.locator(".run-check-evidence")).to_have_count(3)
            await expect(run.locator(".run-check-evidence").first).to_be_hidden()
            await details.locator("summary").click()
            shown = run.locator("li.run-check")
            await expect(shown).to_have_count(3)
            # Oldest first, each with its words in quotes and what was said, in full.
            assert [
                int(i) for i in await shown.evaluate_all("els => els.map(e => e.dataset.event)")
            ] == [e["id"] for e in first]
            await expect(shown.locator(".run-check-words")).to_contain_text(
                [f"“{text}”" for text in checks[:3]]
            )
            said_in = shown.locator(".run-check-evidence")
            assert [spoken(t) for t in await said_in.all_inner_texts()] == [
                "Ran it once",
                spoken(long_evidence),
                "Looked at it",
            ]
            assert spoken(await said_in.nth(1).inner_text()) == spoken(
                first[1]["after"]["evidence"]
            )
            assert (await said_in.nth(1).inner_text()).endswith("are still all there.")

            # More ticks by the same one join the run while the page is open, and what was opened
            # stays open. The count is still of changes, not of rows.
            await ok(by_ticker, "set_check", node=task, item=keys[checks[3]], evidence="Checked")
            await expect(run).to_have_attribute("data-count", "4", timeout=NEWS)
            await expect(page.locator("#feed li.event")).to_have_count(1)
            await expect(details).to_have_attribute("open", "")
            await expect(run.locator("li.run-check")).to_have_count(4)
            await expect(page.locator("#activity-count")).to_have_text("4 changes")
            # A tick by someone else is a row of its own, and a run of one is an ordinary row: its
            # words and what was said are in the row, with no "Show the checks".
            await ok(by_other, "set_check", node=task, item=keys[checks[4]], evidence="Done by me")
            single = page.locator(f"#feed li.event[data-actor='{other}'][data-action=check_ticked]")
            await expect(single).to_have_count(1, timeout=NEWS)
            await expect(single).not_to_have_class(re.compile(r"event-run"))
            await expect(single.locator("details.checks-run")).to_have_count(0)
            assert await page.locator("#feed li.event").count() == 2
            # Unticking is a run of its own, told the other way round, and says why in full.
            for text in checks[:2]:
                await ok(
                    by_ticker,
                    "set_check",
                    node=task,
                    item=keys[text],
                    checked=False,
                    evidence=f"Found a typo in {text}",
                )
            undone = page.locator("#feed li.event-run.action-check_unticked")
            await expect(undone).to_have_attribute("data-count", "2", timeout=NEWS)
            assert spoken(await undone.locator(".event-words").inner_text()) == (
                f"unticked 2 checks on task {code_of(task)} “Permits {word}”."
            )
            await undone.locator("details.checks-run summary").click()
            await expect(undone.locator(".run-check-words")).to_contain_text(
                [f"“{text}”" for text in checks[:2]]
            )
            await expect(undone.locator(".run-check-evidence")).to_have_text(
                [f"Why it was unticked: Found a typo in {text}" for text in checks[:2]]
            )
            # Every row is a run or one event, and between them they are every change there is,
            # newest first: the feed folds, it never drops one.
            everything = await events_of(client, node=task)
            tick_events = [e for e in everything if e["action"].startswith("check_")]
            assert len(tick_events) == 7
            await expect(page.locator("#activity-count")).to_have_text("7 changes")
            rows = page.locator("#feed li.event")
            assert [
                int(i) for i in await rows.evaluate_all("els => els.map(e => e.dataset.event)")
            ] == [
                tick_events[-1]["id"],
                tick_events[-3]["id"],
                tick_events[-4]["id"],
            ]
            assert w.problems == []


async def test_the_free_list_is_the_servers_ready_work_and_a_bad_ref_is_refused_in_its_words(
    client: Client, board: str
) -> None:
    free = [n["ref"] for n in (await ok(client, "ready_work", limit=10))["items"]]
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/activity?tab=free")
        listed = page.locator("#free li.claim")
        await expect(listed).to_have_count(len(free), timeout=SLOW)
        assert [await e.get_attribute("data-node") for e in await listed.all()] == free
        # A bare number names nothing, since each kind is numbered on its own: the box says so
        # itself, in its own words and beside the box, and nothing is sent. A task that does not
        # exist is asked of the server and refused in its words (the newest notice is the first).
        notices = page.locator("#notices .notice.refusal")
        await page.locator("#claim-id").fill("999999999")
        await page.locator("#claim-by-id-go").click()
        await expect(page.locator("#claim-problem")).to_have_text(
            "No item has that title or code. Type a word of its title, or a code such as S-12, "
            "AC-3 or N-7, and choose it from the list."
        )
        await expect(notices).to_have_count(0)
        await page.locator("#claim-id").fill("task:999999999")
        await page.locator("#claim-by-id-go").click()
        await expect(notices).to_have_count(1, timeout=SLOW)
        plain(
            await notices.first.locator(".notice-text").inner_text(),
            await refused(client, "claim_node", node="task:999999999"),
        )
        assert w.problems == []


async def test_the_claim_box_takes_a_word_of_a_title_or_a_code_and_refuses_in_words_beside_it(
    board: str,
) -> None:
    word = unique_word()
    name = f"Ada {word}"
    project = make_project(register(name=name), f"Box {word}")
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Epic {word}")
        first = await made(client, "story", f"First story {word}", epic)
        second = await made(client, "story", f"Second story {word}", epic)
        twin_a = await made(client, "task", f"Twin task {word}", first)
        twin_b = await made(client, "task", f"Twin task {word}", second)
        rule = await ok(client, "create_rule", title=f"Rule {word}", node=first)

        async with browser_page(project.address, token=project.key) as w:
            page = w.page
            await w.open("#/activity?tab=free")
            box, go, problem = (
                page.locator("#claim-id"),
                page.locator("#claim-by-id-go"),
                page.locator("#claim-problem"),
            )
            await expect(box).to_have_attribute(
                "placeholder", "A word of a title, or a code such as T-4"
            )
            list_ = page.locator("#claim-id-list")
            # Nothing is chosen and no list is open until the person types.
            await expect(box).to_have_value("")
            await expect(list_).to_be_hidden()
            await box.focus()
            await expect(list_).to_be_hidden()
            await expect(box).to_have_attribute("aria-expanded", "false")
            await page.locator("#lease").fill("120")
            # An empty box, words that name nothing, and a criterion are each refused beside the
            # box, in words, and what was typed stays; nothing is sent and no notice appears.
            await go.click()
            await expect(problem).to_have_text(
                "Choose what to claim: type a word of its title, or a code, and choose it from "
                "the list."
            )
            await box.fill("zzzz nothing like it")
            await go.click()
            await expect(problem).to_contain_text("No item has that title or code.")
            await expect(box).to_have_value("zzzz nothing like it")
            await box.fill(f"AC-{rule['id']}")
            await go.click()
            await expect(problem).to_have_text(
                "Only an epic, a story or a task can be claimed. Choose one from the list."
            )
            await expect(page.locator("#notices .notice")).to_have_count(0)
            assert (await ok(client, "who_is_working"))["agents"] == []
            await expect(page.locator("#lease")).to_have_value("120")
            # Typing again takes the message away, and the list offers what matches: its code, its
            # title and what it is under, so that two tasks of one title can be told apart.
            await box.fill(f"Twin task {word}")
            await expect(problem).to_have_text("")
            options = list_.locator("li.item-suggest-option")
            await expect(options).to_have_count(2)
            # Each one says its code, its title and the story it is under (in whatever order the
            # list has them).
            for ref, story_title in (
                (twin_a, f"First story {word}"),
                (twin_b, f"Second story {word}"),
            ):
                option = list_.locator(f"li.item-suggest-option[data-ref='{ref}']")
                await expect(option.locator(".item-suggest-code")).to_have_text(code_of(ref))
                await expect(option.locator(".item-suggest-title")).to_have_text(
                    f"Twin task {word}"
                )
                await expect(option.locator(".item-suggest-context")).to_have_text(story_title)
            await page.keyboard.press("Escape")
            await expect(list_).to_be_hidden()
            # Choosing one puts its code and title in the box and claims that one, not the other.
            await box.fill(f"Twin task {word}")
            await list_.locator(f"li.item-suggest-option[data-ref='{twin_b}']").click()
            await expect(box).to_have_value(f"{code_of(twin_b)} Twin task {word}")
            await go.click()
            await expect(page.locator("#notices .notice-text").first).to_have_text(
                f"Took “Twin task {word}”.", timeout=SLOW
            )
            assert (await ok(client, "get_node", node=twin_b))["claimed_by"] == name
            assert (await ok(client, "get_node", node=twin_a))["claimed_by"] is None
            assert w.problems == []


# -- the toggle: Changes, Working and Free to take, one at a time, across the whole page --------


async def test_the_toggle_shows_one_view_at_a_time_and_the_filters_and_what_is_typed_survive(
    board: str,
) -> None:
    word = unique_word()
    agent = f"agent-{word}"
    project = make_project(register(name=f"Ada {word}"), f"Toggle {word}")
    async with connect(project.mcp_url, project.key) as client, connect_as(agent, project) as bot:
        epic = await made(client, "epic", f"Epic {word}")
        story = await made(client, "story", f"Story {word}", epic)
        held = await made(client, "task", f"Held {word}", story)
        await made(client, "task", f"Free {word}", story)
        await ok(bot, "claim_node", node=held, lease_seconds=900)
        views = {"changes": "#feed-section", "working": "#working", "free": "#free"}

        async with browser_page(project.address, token=project.key) as w:
            page = w.page
            await w.open("#/activity")
            # The title is for a screen reader only (the top bar names the page), and the toggle is
            # a centred group of three buttons: Changes, Working and Free to take.
            h1 = page.locator("#page h1")
            await expect(h1).to_have_text("Activity")
            await expect(h1).to_have_class(re.compile(r"\bsr-only\b"))
            toggle = page.locator("#activity-tabs")
            await expect(toggle).to_have_attribute("aria-label", "What to look at")
            buttons = toggle.locator("button")
            assert [spoken(t) for t in await buttons.all_inner_texts()][0] == "Changes"
            assert [await b.get_attribute("data-tab") for b in await buttons.all()] == [
                "changes",
                "working",
                "free",
            ]
            await expect(page.locator("#activity-tab-working .tab-name")).to_have_text("Working")
            await expect(page.locator("#activity-tab-free .tab-name")).to_have_text("Free to take")
            tabs = await page.evaluate(
                """() => {
                    const b = document.querySelector('#activity-tabs').getBoundingClientRect();
                    const p = document.querySelector('#page').getBoundingClientRect();
                    return [b.left, b.right, p.left, p.right];
                }"""
            )
            # Centred in the page: the same room on both sides of the toggle.
            assert abs((tabs[0] - tabs[2]) - (tabs[3] - tabs[1])) <= 2, tabs

            async def only(tab: str) -> None:
                """Exactly this view is on the page, and its button is the pressed one."""
                for name, selector in views.items():
                    await expect(page.locator(selector)).to_have_count(1 if name == tab else 0)
                    await expect(page.locator(f"#activity-tab-{name}")).to_have_attribute(
                        "aria-pressed", "true" if name == tab else "false"
                    )
                await expect(page.locator("#activity-view > *")).to_have_count(1)

            await only("changes")
            assert (await address(page)) == ("activity", {})
            # A filter set in the changes stays in the address while another view is shown, a
            # typed word that was not applied stays in its box, and the filter is still open.
            await open_activity_filter(page)
            await page.locator("#a-actor").fill(agent)
            await page.locator("#apply-activity").click()
            # (the Filter button counts it once the address has been read: only then is a draft
            # typed in another box safe from being drawn over)
            await expect(page.locator("#activity-filter-toggle .filter-count")).to_have_text("1")
            await expect(page.locator("#activity-count")).not_to_have_text("Reading the changes…")
            await page.locator("#a-item").fill("a draft nobody applied")
            await page.locator("#activity-tab-working").click()
            await only("working")
            assert (await address(page)) == ("activity", {"act_actor": agent, "tab": "working"})
            await expect(page.locator("#agents li.agent")).to_have_count(1, timeout=SLOW)
            await page.locator("#activity-tab-free").click()
            await only("free")
            assert (await address(page)) == ("activity", {"act_actor": agent, "tab": "free"})
            ready = (await ok(client, "ready_work", limit=10))["items"]
            assert f"Free {word}" in [n["title"] for n in ready]
            await expect(page.locator("#free li.claim")).to_have_count(len(ready), timeout=SLOW)
            await expect(page.locator("#free li.claim", has_text=f"Free {word}")).to_have_count(1)
            # Each view has the whole width: no side column beside any of them, and the view is as
            # wide as the page and not narrower than the toggle's row.
            for tab in ("changes", "working", "free"):
                await show_tab(page, tab)
                await only(tab)
                await expect(page.locator(".activity-side, .activity-layout")).to_have_count(0)
                wide = await page.evaluate(
                    """() => {
                        const v = document.querySelector('#activity-view > section');
                        const p = document.querySelector('#page');
                        const s = getComputedStyle(p);
                        const pad = parseFloat(s.paddingLeft) + parseFloat(s.paddingRight);
                        const room = p.clientWidth - pad;
                        return [v.getBoundingClientRect().width, room];
                    }"""
                )
                assert wide[0] >= wide[1] - 2, (tab, wide)
            # Back in the changes: the filter is applied, open, and the box still holds the draft.
            await show_tab(page, "changes")
            await expect(page.locator("#activity-filter")).to_be_visible()
            await expect(page.locator("#a-actor")).to_have_value(agent)
            await expect(page.locator("#a-item")).to_have_value("a draft nobody applied")
            assert (await address(page)) == ("activity", {"act_actor": agent})
            # The address opens a view, any other word is the changes, and the browser's way back
            # steps through the views.
            await w.go("#/activity?tab=free")
            await only("free")
            await w.go("#/activity?tab=nonsense")
            await only("changes")
            await w.go("#/activity?tab=working")
            await only("working")
            await page.go_back()
            await only("changes")
            await page.go_forward()
            await only("working")
            # The arrow keys move between the buttons.
            await page.locator("#activity-tab-working").focus()
            await page.keyboard.press("ArrowRight")
            await expect(page.locator("#activity-tab-free")).to_be_focused()
            await page.keyboard.press("Home")
            await expect(page.locator("#activity-tab-changes")).to_be_focused()
            await page.keyboard.press("Enter")
            await only("changes")
            assert w.problems == []


async def test_a_name_in_the_feed_opens_a_card_with_who_they_are_and_what_they_hold(
    board: str,
) -> None:
    word = unique_word()
    agent, person = f"agent-{word}", f"Ada {word}"
    project = make_project(register(name=person), f"Card {word}")
    async with connect(project.mcp_url, project.key) as client, connect_as(agent, project) as bot:
        epic = await made(client, "epic", f"Epic {word}")
        story = await made(client, "story", f"Story {word}", epic)
        held = await made(client, "task", f"Held {word}", story)
        await ok(bot, "claim_node", node=held, lease_seconds=900)
        await ok(bot, "release_claim", node=held, note=f"Handing {word} back.")
        again = await made(client, "task", f"Again {word}", story)
        await ok(bot, "claim_node", node=again, lease_seconds=900)
        # The person has done something too, so that the feed has a name of theirs.
        on_board("create_node", project=project, kind="task", title=f"By hand {word}", parent=story)

        async with browser_page(project.address, token=project.key) as w:
            page = w.page
            await w.open("#/activity")
            name = page.locator(f"#feed li.event[data-actor='{agent}'] .actor.has-card").first
            await expect(name).to_be_visible(timeout=SLOW)
            # A name is a focus stop with no tooltip of its own (the card is its tooltip); the
            # pointer resting on it opens the card, which says who they are, what they hold now and
            # their last handoff, with links to the work.
            await expect(name).to_have_attribute("tabindex", "0")
            await expect(name).to_have_attribute("data-actor-name", agent)
            assert await name.get_attribute("title") is None
            await name.hover()
            card = page.locator(".hovercard:visible .actor-card")
            await expect(card).to_be_visible(timeout=SLOW)
            await expect(card).to_contain_text(agent)
            await expect(card).to_contain_text("An agent: it works through its key")
            held_now = card.locator("section.actor-card-part", has_text="Holding now (1)")
            await expect(held_now.locator("a.item-link")).to_have_attribute(
                "href", re.compile(re.escape(f"item={again}"))
            )
            await expect(held_now).to_contain_text(f"Again {word}")
            await expect(held_now).to_contain_text("left")
            last = card.locator("section.actor-card-part", has_text="Last handoff")
            await expect(last).to_contain_text(f"Handing {word} back.")
            # The owner also sees what the agent's key may do.
            await expect(card).to_contain_text("May do")
            # Escape closes it, and the name takes the card again from the keyboard.
            await page.keyboard.press("Escape")
            await expect(card).to_have_count(0)
            await name.focus()
            await expect(card).to_be_visible(timeout=SLOW)
            await page.keyboard.press("Escape")
            await expect(card).to_have_count(0)
            # A person's card says their role; the signed-in person is told it is them.
            me = page.locator(f"#feed li.event .actor.has-card[data-actor-name='{person}']").first
            await me.hover()
            await expect(card).to_be_visible(timeout=SLOW)
            await expect(card).to_contain_text("A person in this project")
            await expect(card.locator(".chip.role")).to_have_text("Owner")
            await expect(card.locator(".chip.you")).to_have_text("You")
            await page.keyboard.press("Escape")
            assert (await address(page))[0] == "activity"
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
        await expect(row(page, f"{ann} moved AC-{rule['id']}")).to_contain_text(
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
        # plus sign is a space in an address), or by its reference; a criterion and a note by
        # their short codes, AC-3 and N-7, in any case, or the older words.
        for item, filters in (
            (f"note+{decision['id']}", {"note_id": decision["id"]}),
            (f"note:{note['id']}", {"note_id": note["id"]}),
            (f"N-{note['id']}", {"note_id": note["id"]}),
            (f"n-{decision['id']}", {"note_id": decision["id"]}),
            (code_of(story["ref"]), {"node": story["ref"]}),
            (f"story+{number(story['ref'])}", {"node": story["ref"]}),
            (story["ref"], {"node": story["ref"]}),
            (f"criterion+{rule['id']}", {"rule_id": rule["id"]}),
            (f"AC-{rule['id']}", {"rule_id": rule["id"]}),
            (f"ac-{rule['id']}", {"rule_id": rule["id"]}),
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
        # A filter in the address opens the boxes and the Filter button counts the boxes that are
        # set, in the address or not: three here.
        toggle = page.locator("#activity-filter-toggle")
        await expect(page.locator("#activity-filter")).to_be_visible()
        await expect(toggle).to_have_attribute("aria-expanded", "true")
        await expect(toggle.locator(".filter-count")).to_have_text("2")
        await w.go(f"#/activity?act_actor={ann}&act_kind=notes&act_item={code_of(story['ref'])}")
        await expect(toggle.locator(".filter-count")).to_have_text("3")
        # The form does the same as the address, and Clear takes every filter off. The button
        # opens and closes the boxes without touching the filter.
        await w.go("#/activity")
        await expect(toggle.locator(".filter-count")).to_be_hidden()
        await toggle.click()
        await expect(page.locator("#activity-filter")).to_be_hidden()
        await expect(toggle).to_have_attribute("aria-expanded", "false")
        await open_activity_filter(page)
        await page.locator("#a-actor").fill(bob)
        await page.locator("#a-kind").select_option("claims")
        await page.locator("#apply-activity").click()
        claims = [e for e in bob_events if e["action"].startswith("claim")]
        await expect(page.locator("#feed li.event")).to_have_count(len(claims), timeout=SLOW)
        name, choices = await address(page)
        assert (name, choices["act_actor"], choices["act_kind"]) == ("activity", bob, "claims")
        await expect(toggle.locator(".filter-count")).to_have_text("2")
        await expect(toggle).to_have_class(re.compile(r"\bhas-filters\b"))
        await toggle.click()
        await expect(page.locator("#activity-filter")).to_be_hidden()
        assert (await address(page))[1]["act_actor"] == bob
        await toggle.click()
        await page.locator("#clear-activity").click()
        await expect(page.locator("#a-actor")).to_have_value("")
        assert {"act_actor", "act_kind"}.isdisjoint((await address(page))[1])
        await expect(toggle.locator(".filter-count")).to_be_hidden()
        await expect(page.locator("#activity-filter")).to_be_visible()
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
        # are still there when news arrives and the page draws the feed and the claims again, and
        # when another view is chosen and this one comes back.
        await view_crumb(page).click()
        await on_page(page, "activity")
        await open_activity_filter(page)
        await page.locator("#a-actor").fill("somebody")
        await show_tab(page, "free")
        await page.locator("#claim-id").fill(code_of(new))
        await page.locator("#lease").fill("120")
        async with connect_as(agent) as bot:
            await ok(bot, "claim_node", node=claimed)
        await expect(page.locator(f"#free li.claim[data-node='{claimed}']")).to_have_count(
            0, timeout=NEWS
        )
        assert await page.locator("#claim-id").input_value() == code_of(new)
        assert await page.locator("#lease").input_value() == "120"
        await open_working(page)
        await expect(
            page.locator(f"#agents li.agent[data-actor='{agent}'] li.claim[data-node='{claimed}']")
        ).to_be_visible(timeout=NEWS)
        await open_free(page)
        assert await page.locator("#claim-id").input_value() == code_of(new)
        assert await page.locator("#lease").input_value() == "120"
        await show_tab(page, "changes")
        await expect(row(page, f"{agent} took task {code_of(claimed)}")).to_be_visible(timeout=NEWS)
        assert await page.locator("#a-actor").input_value() == "somebody"
        await expect(page.locator("#activity-filter")).to_be_visible()
        assert "act_actor" not in (await address(page))[1]
        assert w.problems == []
