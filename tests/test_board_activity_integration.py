"""M14's Activity page against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh): what agents do shows in plain words within seconds with
the handoff note in full, who is working on what follows claims with a countdown to their leases,
a person takes, renews and releases work under a typed name, and the feed's filters give what
`changes_since` gives. Names and titles carry a unique word, so each test looks only at what it
made."""

import datetime
from typing import Any

import anyio
import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import SLOW, board_url, browser_page, made, plain
from tests.board_m14 import events_of, feed_ids, spoken
from tests.board_m14 import feed_row as row
from tests.helpers import day, make_decision, ok, refused, the_token, unique_word

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NEWS = 15_000  # milliseconds: the board looks for news every five seconds, and the spec allows ten


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


# -- 2: an agent's work, in plain words ---------------------------------------------------------


async def test_what_an_agent_does_shows_in_plain_words_with_the_note_in_full(
    client: Client, board: str
) -> None:
    word = unique_word()
    agent = f"agent-{word}"
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Work {word}", story)
    rule = await ok(client, "create_rule", actor="a", title=f"Rule {word}", story_id=story)
    note = f"Built the whole thing for {word}. Nothing is left, but check the logs."

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/activity?actor={agent}")
        await expect(page.locator("#count")).to_have_text("Nothing has happened yet.", timeout=SLOW)
        await page.evaluate("window.__same = 'same page'")

        taken = await ok(client, "next_work", actor=agent, within_node_id=epic)
        assert taken["found"] and taken["work"]["id"] == task
        # In plain words, with the actor, and the claim in who is working on what with a countdown.
        await expect(row(page, f"took task #{task} “Work {word}”")).to_contain_text(
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
        decided = await make_decision(client, f"Decided {word}", actor=agent, node_ids=[story])
        await ok(client, "finish_work", actor=agent, node_id=task, outcome="done", note=note)

        await expect(row(page, f"changed rule {rule['id']}")).to_contain_text(
            f"title: “Rule {word}” → “Renamed {word}”", timeout=NEWS
        )
        await expect(row(page, f"recorded decision {decided['id']}")).to_contain_text(
            f"“Decided {word}”, decided by Marie", timeout=NEWS
        )
        done = row(page, f"finished their work on task #{task}")
        await expect(done).to_contain_text(
            f"{agent} finished their work on task #{task}", timeout=NEWS
        )
        await expect(done).to_contain_text("(done) and left this handoff note")
        # The note is shown in full, in the row.
        assert spoken(await done.locator(".handoff-note").inner_text()) == note
        await expect(row(page, f"let go of task #{task}")).to_be_visible()

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


# -- 2: a person takes work, keeps it, lets it go, and lets it lapse ----------------------------


async def test_a_person_claims_renews_and_releases_under_the_typed_name(
    client: Client, board: str
) -> None:
    word = unique_word()
    name = f"Ada {word}"
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    mine, theirs = [await made(client, "task", f"{n} {word}", story) for n in ("Mine", "Theirs")]
    await ok(client, "claim_node", actor=f"agent-{word}", node_id=theirs, lease_seconds=900)

    async with browser_page(board, token=the_token(), name=name) as w:
        page = w.page
        await w.open("#/activity")
        await expect(page.locator("#acting-as")).to_contain_text(f"act as {name}", timeout=SLOW)
        await page.locator("#lease").fill("60")
        await page.locator("#claim-id").fill(f"#{mine}")
        await page.locator("#claim-by-id-go").click()
        # The page says so in words and a countdown, and the server has it under the typed name.
        claim = page.locator(f"#claims-live li.claim[data-node='{mine}']")
        await expect(claim).to_contain_text(f"held by {name}", timeout=NEWS)
        node = await ok(client, "get_node", node_id=mine)
        assert node["claimed_by"] == name
        await expect(row(page, f"{name} took task #{mine}")).to_be_visible(timeout=NEWS)
        await expect(page.locator(f"#agents li.agent[data-actor='{name}']")).to_have_attribute(
            "data-state", "working"
        )
        before = (await ok(client, "get_node", node_id=mine))["claim_expires_at"]

        # Renew keeps it for the default lease, which is much longer than the 60 seconds.
        await claim.locator("button.renew").click()
        await expect(row(page, f"{name} kept task #{mine}")).to_be_visible(timeout=NEWS)
        after = (await ok(client, "get_node", node_id=mine))["claim_expires_at"]
        gained = datetime.datetime.fromisoformat(after) - datetime.datetime.fromisoformat(before)
        assert gained > datetime.timedelta(minutes=10)

        # Releasing leaves the note the person typed, and the work is free again.
        await claim.locator("button.release").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#release-note").fill(f"Stopped here for {word}.")
        await dialog.get_by_role("button", name="Release it").click()
        await expect(dialog).to_have_count(0, timeout=SLOW)
        released = await ok(client, "get_node", node_id=mine)
        assert released["claimed_by"] is None and released["status"] == "ready"
        assert released["last_handoff"]["actor"] == name
        assert released["last_handoff"]["note"] == f"Stopped here for {word}."
        done = row(page, f"{name} finished their work on task #{mine}")
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
            await refused(client, "release_claim", actor=name, node_id=theirs, note="Not mine."),
        )
        assert (await ok(client, "get_node", node_id=theirs))["claimed_by"] == f"agent-{word}"
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
        await w.open("#/activity")
        await page.locator("#lease").fill("3")
        await page.locator("#claim-id").fill(str(task))
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
        node = await ok(client, "get_node", node_id=task)
        assert node["claimed_by"] is None and node["lapsed_claim_by"] == name
        who = {a["actor"]: a for a in (await ok(client, "who_is_working"))["agents"]}
        assert who[name]["state"] == "lapsed"
        assert [h["node_id"] for h in who[name]["lapsed"]] == [task]
        # The holder can still let go of it, with a note, and then it is neither.
        await lapsed.locator("button.release").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#release-note").fill("It ran out on me.")
        await dialog.get_by_role("button", name="Release it").click()
        await expect(dialog).to_have_count(0, timeout=SLOW)
        await expect(lapsed).to_have_count(0, timeout=NEWS)
        await expect(agent_row).to_have_attribute("data-state", "finished", timeout=NEWS)
        assert w.problems == []


async def test_the_free_list_is_the_servers_ready_work_and_a_bad_id_is_refused_in_its_words(
    client: Client, board: str
) -> None:
    free = [n["id"] for n in (await ok(client, "ready_work", limit=10))["items"]]
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/activity")
        listed = page.locator("#free li.claim")
        await expect(listed).to_have_count(len(free), timeout=SLOW)
        assert [int(await e.get_attribute("data-node") or 0) for e in await listed.all()] == free
        await page.locator("#claim-id").fill("999999999")
        await page.locator("#claim-by-id-go").click()
        notice = page.locator("#notices .notice.refusal")
        await expect(notice).to_be_visible(timeout=SLOW)
        plain(
            await notice.locator(".notice-text").inner_text(),
            await refused(client, "claim_node", actor="Tester", node_id=999999999),
        )
        assert w.problems == []


# -- the feed: filters, items and older changes ---------------------------------------------------


async def test_the_feeds_filters_give_what_changes_since_gives(client: Client, board: str) -> None:
    word = unique_word()
    ann, bob = f"ann-{word}", f"bob-{word}"
    epic = await ok(client, "create_node", actor=ann, kind="epic", title=f"Epic {word}")
    story = await ok(
        client, "create_node", actor=ann, kind="story", title=f"Story {word}", parent_id=epic["id"]
    )
    other = await ok(
        client, "create_node", actor=bob, kind="story", title=f"Other {word}", parent_id=epic["id"]
    )
    rule = await ok(client, "create_rule", actor=ann, title=f"Rule {word}", story_id=story["id"])
    await ok(
        client,
        "move_rule",
        actor=ann,
        rule_id=rule["id"],
        expected_version=1,
        new_story_id=other["id"],
    )
    decision = await make_decision(client, f"Decision {word}", actor=bob, node_ids=[story["id"]])
    await ok(client, "claim_node", actor=bob, node_id=story["id"])
    both = await events_of(client, node_id=epic["id"])
    assert both

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/home")

        async def shown(address: str, events: list[dict[str, Any]]) -> None:
            """Go to an address, and wait until the feed is exactly these events, newest first."""
            wanted = [e["id"] for e in reversed(events)]
            await w.go(address)
            for _ in range(150):
                if await feed_ids(page) == wanted:
                    return
                await anyio.sleep(0.1)
            assert await feed_ids(page) == wanted, address

        # Who: the server's own filter.
        ann_events = await events_of(client, actor=ann)
        await shown(f"#/activity?actor={ann}", ann_events)
        # The kind of change: worked out here, equal to the actions of that kind.
        rules_only = [e for e in ann_events if e["action"].startswith("rule_")]
        await shown(f"#/activity?actor={ann}&kind=rules", rules_only)
        # The spec's own sentence: a rule moved from one story to another.
        await expect(row(page, f"{ann} moved rule {rule['id']}")).to_contain_text(
            f"from story #{story['id']} “Story {word}” to story #{other['id']} “Other {word}”"
        )
        # An item: a rule, a decision and a node each give exactly the server's answer.
        for item, filters in (
            (f"rule:{rule['id']}", {"rule_id": rule["id"]}),
            (f"decision {decision['id']}", {"decision_id": decision["id"]}),
            (f"#{story['id']}", {"node_id": story["id"]}),
        ):
            await shown(
                f"#/activity?item={item.replace(' ', '+')}", await events_of(client, **filters)
            )
        # The days: today keeps everything of today, and a day in the future keeps nothing.
        today = day()
        await shown(f"#/activity?actor={ann}&from={today}&to={today}", ann_events)
        await shown(f"#/activity?actor={ann}&from={day(1)}", [])
        await expect(page.locator("#count")).to_contain_text("Nothing read so far matches")
        await shown(f"#/activity?actor={ann}&to={day(-1)}", [])
        # The form does the same as the address, and Clear takes every filter off.
        await w.go("#/activity")
        await page.locator("#a-actor").fill(bob)
        await page.locator("#a-kind").select_option("claims")
        await page.locator("#apply-activity").click()
        claims = [e for e in await events_of(client, actor=bob) if e["action"].startswith("claim")]
        await expect(page.locator("#feed li.event")).to_have_count(len(claims), timeout=SLOW)
        assert "kind=claims" in await page.evaluate("location.hash")
        await page.locator("#clear-activity").click()
        await expect(page.locator("#a-actor")).to_have_value("")
        # A thing that does not exist is refused in the server's words, and nothing is listed.
        await w.go("#/activity?item=rule:999999999")
        await expect(page.locator("#activity-problem")).not_to_have_text("", timeout=SLOW)
        plain(
            await page.locator("#activity-problem").inner_text(),
            await refused(client, "changes_since", rule_id=999999999),
        )
        assert await page.locator("#feed li.event").count() == 0
        assert w.problems == []


async def test_older_changes_are_read_when_asked_for_and_the_feed_never_repeats_one(
    client: Client, board: str
) -> None:
    word = unique_word()
    actor = f"busy-{word}"
    epic = await ok(client, "create_node", actor=actor, kind="epic", title=f"Busy {word}")
    for number in range(104):
        await ok(
            client,
            "create_node",
            actor=actor,
            kind="story",
            title=f"S{number} {word}",
            parent_id=epic["id"],
        )
    everything = await events_of(client, actor=actor)
    assert len(everything) == 105

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/activity?actor={actor}")
        await expect(page.locator("#feed li.event")).to_have_count(100, timeout=SLOW)
        assert await feed_ids(page) == [e["id"] for e in reversed(everything)][:100]
        await expect(page.locator("#count")).to_contain_text("Older changes have not been read yet")
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
            parent_id=epic["id"],
        )
        await expect(page.locator("#feed li.event")).to_have_count(106, timeout=NEWS)
        ids = await feed_ids(page)
        assert len(set(ids)) == 106 and ids == sorted(ids, reverse=True)
        assert w.problems == []
