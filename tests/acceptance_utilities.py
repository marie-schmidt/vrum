"""Chapter 12 of the acceptance scenario: the Activity page and the Settings pages Tools and
Status (M14, M16 and M20), driven in a real headless browser against the real containers, with what
a page shows compared with what a real MCP client gets from the same tools. What agents and people
do shows on the Activity page (`#/activity`); the Tools page is the server's own list of 53 tools;
Status is `server_status` and the export, and no secret is ever shown. All three open from the
keyboard, an address reopens the state it names, a draft is not lost, and a lost connection is
told in the page the person is on."""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import Page, Route, expect

from tests.acceptance_board import same_words
from tests.acceptance_scenario import URL, Failed, chapter, clip, proof
from tests.board_helpers import (
    SLOW,
    board_call,
    board_url,
    browser_page,
    crumbs,
    go,
    item_address,
    item_loaded,
    item_page,
    made,
    no_panel_or_drawer,
    on_page,
    open_editor,
    open_item,
    sidebar_current,
    tree_expand,
    tree_item,
    view_crumb,
)
from tests.board_m14 import events_of, feed_becomes, feed_ids, feed_row, ids_of, spoken
from tests.board_search import (
    address,
    asked,
    on_view,
    open_settings,
    result_rows,
    shown_triples,
    triples,
)
from tests.helpers import (
    REPO_ROOT,
    code_of,
    confirm_on_board,
    connect,
    connect_as,
    day,
    embeddings_stopped,
    export_everything,
    make_note,
    number_of,
    ok,
    on_board,
    refused,
    reindexed,
    the_token,
    unique_word,
)
from tests.world import project_at

ADA = "Ada Lovelace"
NEWS = 15_000
COUNTS = ("epics", "stories", "tasks", "links", "rules", "notes", "events")


async def tools_listed(page: Page) -> None:
    await expect(page.locator("#tool-list article.tool").first).to_be_visible(timeout=SLOW)


def hash_of(url: str) -> str:
    """The part of a board address after the #, with the #: what `go` loads afresh."""
    return "#" + url.split("#", 1)[1]


async def try_it(page: Page, name: str, **typed: str) -> Any:
    """Open the try form of a tool, type into its fields, run it, and give what it showed."""
    entry = page.locator(f"#tool-entry-{name}")
    await entry.scroll_into_view_if_needed()
    form = entry.locator("details.try")
    if not await form.evaluate("e => e.open"):
        await entry.locator("details.try summary").click()
    for field_name, text in typed.items():
        await entry.locator(f"[name={field_name}]").fill(text)
    await entry.locator("pre.result").evaluate("e => { e.hidden = true; e.textContent = ''; }")
    await entry.locator(".try-form .problem").evaluate("e => { e.textContent = ''; }")
    await entry.locator(".try-run").click()
    result = entry.locator("pre.result")
    problem = entry.locator(".try-form .problem")
    for _ in range(100):
        if await result.is_visible() and (await result.inner_text()).strip():
            return json.loads(await result.inner_text())
        if (await problem.inner_text()).strip():
            return {"refused": (await problem.inner_text()).strip()}
        await page.wait_for_timeout(100)
    raise Failed(f"M14: Try it on {name} showed nothing")


async def utilities() -> None:
    chapter("Seeing it all: the Activity page, Tools and Status, from the keyboard")
    page_url = board_url(URL)
    try:
        async with connect(URL, the_token()) as client:
            await activity(client, page_url)
            await claims(client, page_url)
            await filters(client, page_url)
            await tools(client, page_url)
            await status(client, page_url)
            await keyboard_and_addresses(client, page_url)
            await drafts(client, page_url)
            await connection_failure(client, page_url)
    except BrowserError as error:
        raise Failed(
            f"M16: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- the Activity drawer: what agents do, in plain words -----------------------------------


async def activity(client: Client, page_url: str) -> None:
    word = unique_word()
    agent, person = f"agent-{word}", f"Ada {word}"
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Work {word}", story)
    rule = await ok(client, "create_rule", actor="a", title=f"Rule {word}", node=story)
    note = f"Built the whole thing for {word}. Nothing is left, but check the logs."
    async with (
        connect_as(agent) as bot,
        browser_page(page_url, token=the_token(), name=ADA) as w,
    ):
        page = w.page
        await w.open(f"#/activity?act_actor={agent}")
        await on_page(page, "activity")
        await no_panel_or_drawer(page)
        assert await sidebar_current(page) == ["activity"]
        await expect(page.locator("#activity-count")).to_have_text("0 changes", timeout=SLOW)
        await page.evaluate("window.__same = 'same page'")
        taken = await ok(bot, "next_work", within_node=epic)
        claim = page.locator(f"#claims-live li.claim[data-node='{task}']")
        await expect(claim).to_contain_text(f"held by {agent}", timeout=NEWS)
        agent_row = page.locator(f"#agents li.agent[data-actor='{agent}']")
        working = await agent_row.get_attribute("data-state")
        counting = await claim.locator(".countdown").inner_text()
        await ok(
            bot, "update_rule", rule_id=rule["id"], expected_version=1,
            title=f"Renamed {word}",
        )  # fmt: skip
        decided = await make_note(bot, f"Decided {word}", decided_by="Marie", nodes=[story])
        await ok(bot, "finish_work", node=task, outcome="done", note=note)
        done = feed_row(page, f"finished their work on task {code_of(task)}")
        await expect(done).to_contain_text(agent, timeout=NEWS)
        rows = {
            "took": await feed_row(page, f"took task {code_of(task)}").inner_text(),
            "rule": await feed_row(page, f"changed requirement {rule['id']}").inner_text(),
            "note": await feed_row(page, f"recorded note {decided['id']}").inner_text(),
        }
        await expect(claim).to_have_count(0, timeout=NEWS)
        await expect(agent_row).to_have_attribute("data-state", "finished", timeout=NEWS)
        shown = await feed_ids(page)
        same_news = await page.evaluate("window.__same") == "same page"
        proof(
            "M20",
            "through MCP an agent takes work with next_work, edits a requirement, records a "
            "note someone decided and finishes with a handoff note; within seconds, with no "
            "reload, the Activity page shows each in plain words with the actor, the handoff note "
            "in full, newest first and equal to the server's feed, and the claim in who is "
            "working on what (with a countdown) until it is finished",
            taken["found"]
            and all(agent in text for text in rows.values())
            and f"“Rule {word}” → “Renamed {word}”" in rows["rule"]
            and f"“Decided {word}”, decided by Marie" in rows["note"]
            and spoken(await done.locator(".handoff-note").inner_text()) == note
            and working == "working"
            and "left" in counting
            and same_news
            and shown == sorted(shown, reverse=True)
            and shown == [e["id"] for e in reversed(await events_of(client, actor=agent))],
            f'"{clip(rows["took"], 60)}"',
        )
        # A line of the feed opens the record's own page, still in Activity, and the way back is
        # the feed as it was left, with its filter.
        await done.locator("a.item-link[data-kind=task]").first.click()
        await item_loaded(page, task)
        path = await crumbs(page)
        opened = (
            path[2] == "Activity"
            and await sidebar_current(page) == ["activity"]
            and (await address(page))[1] == {"item": task, "from": "activity"}
        )
        await page.locator("nav.crumbs li.crumb-view a").click()
        await on_page(page, "activity")
        back = (await address(page))[1]
        again = await feed_becomes(page, shown)
        proof(
            "M20",
            "a line of the Activity feed opens the page of the record it is about, which says in "
            "its breadcrumb and the sidebar that the person is still in Activity, and the "
            "breadcrumb's Activity brings back the feed with the actor filter it had",
            opened and back == {"act_actor": agent} and again == shown,
            f"{code_of(task)} and back",
        )

    # -- notes, told with who did what and on whose word
    second_agent = f"agent2-{word}"
    other = await made(client, "story", f"Other {word}", epic)
    async with connect_as(second_agent) as bot:
        wide = await make_note(
            bot, f"Use email {word}", decided_by="Marie", nodes=[story], project_wide=True
        )
        plain_note = await make_note(
            bot, f"People log in on phones {word}", "The team call", nodes=[story]
        )
        for each in (wide, plain_note):
            confirm_on_board("note", each, person)
        on_board(
            "set_confirmation",
            note_id=plain_note["id"],
            confirmation="unconfirmed",
            person=person,
            quote="I am not sure of this any more.",
        )
        replaced = await ok(
            bot, "supersede_note", replaces=[wide["id"]],
            text=f"Use a code {word}\n\nLog in with a code.", why="Marie changed her mind.",
            decided_by="Marie", said_by=person, quote="Let us have both.",
        )  # fmt: skip
        replacement = replaced["replacement"]
        extra = await make_note(bot, f"Codes arrive quickly {word}", "A hunch")
        await ok(bot, "link_note", note_id=extra["id"], nodes=[other])
        confirm_on_board("note", extra, person)
        await ok(
            bot, "withdraw_note", note_ids=[extra["id"]],
            reason="It was a guess", said_by=person, quote="No, we never measured it.",
        )  # fmt: skip
    events = await events_of(client, actor=second_agent)
    async with browser_page(page_url, token=the_token()) as w:
        page = w.page
        await w.open(f"#/activity?act_actor={second_agent}")
        await on_page(page, "activity")
        await expect(page.locator("#feed li.event")).to_have_count(len(events), timeout=SLOW)
        same_feed = await feed_ids(page) == [e["id"] for e in reversed(events)]

        async def said(action: str, record: int) -> str:
            found = page.locator(f"#feed li.event[data-action={action}]").filter(
                has=page.locator(f"a.item-link[data-id='{record}']")
            )
            words = found.first.locator(".event-words")
            await expect(words).to_be_visible()
            return spoken(await words.inner_text())

        t_dec, t_note = f"“Use email {word}”", f"“People log in on phones {word}”"
        told = [
            await said("note_recorded", wide["id"])
            == f"recorded note {wide['id']} {t_dec}, decided by Marie, for the whole project.",
            await said("note_confirmed", plain_note["id"])
            == f"recorded that {person} confirmed note {plain_note['id']} {t_note}.",
            await said("note_unconfirmed", plain_note["id"])
            == f"took back the confirmation of note {plain_note['id']} {t_note}, on the word of "
            f"{person}: “I am not sure of this any more”.",
            await said("note_superseded", wide["id"])
            == f"replaced note {wide['id']} {t_dec} with note {replacement['id']} "
            f"“Use a code {word}”, on the word of {person}: “Let us have both”.",
            await said("note_withdrawn", extra["id"])
            == f"withdrew note {extra['id']} “Codes arrive quickly {word}”: It was a guess, "
            f"on the word of {person}: “No, we never measured it”.",
        ]
        await w.go(f"#/activity?act_actor={second_agent}&act_kind=notes")
        wanted = [e["id"] for e in reversed(events) if e["action"].startswith("note_")]
        await expect(page.locator("#feed li.event")).to_have_count(len(wanted), timeout=SLOW)
        kinds_hold = await feed_ids(page) == wanted
        assert w.problems == []
    proof(
        "M20",
        "the Activity page tells notes in plain words: who recorded one, who confirmed it, "
        "whose word took a confirmation back or replaced and withdrew a note, and for the "
        "whole project when it is; the kind Notes filters the feed to those events alone",
        same_feed and all(told) and kinds_hold,
        f"{len(events)} events of one agent",
    )


async def claims(client: Client, page_url: str) -> None:
    word = unique_word()
    name = f"Lee {word}"
    epic = await made(client, "epic", f"Epic {word}")
    story = await made(client, "story", f"Story {word}", epic)
    mine, short, theirs = [
        await made(client, "task", f"{n} {word}", story) for n in ("Mine", "Short", "Theirs")
    ]
    await ok(client, "claim_node", actor=f"agent-{word}", node=theirs, lease_seconds=900)
    async with browser_page(page_url, token=the_token(), name=name) as w:
        page = w.page
        await w.open("#/activity")
        await on_page(page, "activity")
        await expect(page.locator("#acting-as")).to_contain_text(f"act as {name}", timeout=SLOW)
        await page.locator("#lease").fill("60")
        await page.locator("#claim-id").fill(code_of(mine))
        await page.locator("#claim-by-id-go").click()
        held = page.locator(f"#claims-live li.claim[data-node='{mine}']")
        await expect(held).to_contain_text(f"held by {name}", timeout=NEWS)
        claimed_by = (await ok(client, "get_node", node=mine))["claimed_by"]
        before = (await ok(client, "get_node", node=mine))["claim_expires_at"]
        await held.locator("button.renew").click()
        await expect(feed_row(page, f"{name} kept task {code_of(mine)}")).to_be_visible(
            timeout=NEWS
        )
        after = (await ok(client, "get_node", node=mine))["claim_expires_at"]
        await held.locator("button.release").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#release-note").fill(f"Stopped here for {word}.")
        await dialog.get_by_role("button", name="Release it").click()
        await expect(dialog).to_have_count(0, timeout=SLOW)
        released = await ok(client, "get_node", node=mine)
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
        refusal_words = await problem.inner_text()
        said = await refused(client, "release_claim", actor=name, node=theirs, note="Not mine.")
        await dialog.get_by_role("button", name="Cancel").click()
        proof(
            "M20",
            "on the Activity page a person claims a task with a typed name, keeps it with Renew (a "
            "longer lease), and lets it go with a note, each the same operation as the agent's; "
            "another holder's claim cannot be released by that name, in the server's words",
            claimed_by == name
            and after > before
            and released["claimed_by"] is None
            and released["last_handoff"]["actor"] == name
            and released["last_handoff"]["note"] == f"Stopped here for {word}."
            and same_words(refusal_words, said)
            and (await ok(client, "get_node", node=theirs))["claimed_by"] == f"agent-{word}",
            f"{code_of(mine)} as {name}",
        )
        await page.locator("#lease").fill("3")
        # The words a person writes for the kind and the number work as well as the code.
        await page.locator("#claim-id").fill(f"task {number_of(short)}")
        await page.locator("#claim-by-id-go").click()
        live = page.locator(f"#claims-live li.claim[data-node='{short}']")
        await expect(live).to_be_visible(timeout=NEWS)
        lapsed = page.locator(f"#claims-lapsed li.claim[data-node='{short}']")
        await expect(lapsed).to_be_visible(timeout=NEWS)
        state = await page.locator(f"#agents li.agent[data-actor='{name}']").get_attribute(
            "data-state"
        )
        node = await ok(client, "get_node", node=short)
        who = {a["actor"]: a for a in (await ok(client, "who_is_working"))["agents"]}
        proof(
            "M14",
            "a claim that runs out moves by itself to lapsed, and the holder's state follows; the "
            "server says the same through get_node and who_is_working",
            state == "lapsed"
            and node["claimed_by"] is None
            and node["lapsed_claim_by"] == name
            and who[name]["state"] == "lapsed",
            f"{code_of(short)}",
        )
        assert w.problems == []


async def filters(client: Client, page_url: str) -> None:
    word = unique_word()
    ann, bob = f"ann-{word}", f"bob-{word}"
    epic = await ok(client, "create_node", actor=ann, kind="epic", title=f"Epic {word}")
    one = await ok(
        client, "create_node", actor=ann, kind="story", title=f"One {word}", parent=epic["ref"]
    )
    two = await ok(
        client, "create_node", actor=bob, kind="story", title=f"Two {word}", parent=epic["ref"]
    )
    rule = await ok(client, "create_rule", actor=ann, title=f"Rule {word}", node=one["ref"])
    await ok(
        client, "place_rule", actor=ann, rule_id=rule["id"], expected_version=1,
        node=two["ref"],
    )  # fmt: skip
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/activity")
        await on_page(page, "activity")
        held = []
        for choices, wanted in (
            (f"act_actor={ann}", await events_of(client, actor=ann)),
            (f"act_item=rule:{rule['id']}", await events_of(client, rule_id=rule["id"])),
            (f"act_item={code_of(one['ref'])}", await events_of(client, node=one["ref"])),
            (f"act_item={one['ref']}", await events_of(client, node=one["ref"])),
        ):
            await w.go(f"#/activity?{choices}")
            ids = [e["id"] for e in reversed(wanted)]
            held.append(await feed_becomes(page, ids) == ids)
        await w.go(f"#/activity?act_actor={ann}&act_kind=requirements")
        await expect(feed_row(page, f"{ann} moved requirement {rule['id']}")).to_contain_text(
            f"from story {code_of(one['ref'])} “One {word}” "
            f"to story {code_of(two['ref'])} “Two {word}”",
            timeout=SLOW,
        )
        rules_only = [
            e["id"]
            for e in reversed(await events_of(client, actor=ann))
            if e["action"].startswith("rule_")
        ]
        kind_holds = await feed_becomes(page, rules_only) == rules_only
        await w.go(f"#/activity?act_actor={ann}&act_from={day(1)}")
        await expect(page.locator("#feed .feed-empty")).to_contain_text(
            "None of the changes read so far match these filters", timeout=SLOW
        )
        today = [e["id"] for e in reversed(await events_of(client, actor=ann))]
        await w.go(f"#/activity?act_actor={ann}&act_from={day()}&act_to={day()}")
        on_the_day = await feed_becomes(page, today)
        proof(
            "M20",
            "the Activity page is filterable by actor, kind of change, item and time, each equal "
            "to changes_since with the same filter, newest first, in plain words such as 'moved "
            "requirement N from story A to story B'",
            all(held) and kind_holds and on_the_day == today,
            f"{len(rules_only)} requirement changes",
        )
        assert w.problems == []


# -- Tools: the server's own list of 53 ------------------------------------------------------


def classification(dropping: str | None) -> subprocess.CompletedProcess[str]:
    """Run the test that every tool is on the board or marked as for agents only, as it is, or with
    one tool taken out of the map."""
    env = {**os.environ, **({"VRUM_TEST_DROP_FROM_MAP": dropping} if dropping else {})}
    return subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            "tests/test_board.py",
            "-k",
            "every_tool_the_server_has_is_on_the_board",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


async def tools(client: Client, page_url: str) -> None:
    listed = (await client.list_tools()).tools
    reads = {t.name for t in listed if t.annotations and t.annotations.read_only_hint}
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/board")
        await open_settings(page, "tools")
        await tools_listed(page)
        names: list[str] = await page.locator("#tool-list article.tool").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        )
        described = 0
        for tool in listed:
            entry = page.locator(f"#tool-entry-{tool.name}")
            first = (tool.description or "").split("\n\n")[0]
            fields: list[str] = await entry.locator("tr[data-field]").evaluate_all(
                "els => els.map(e => e.dataset.field)"
            )
            if spoken(await entry.locator("p.first").inner_text()) == spoken(
                first
            ) and fields == list(tool.input_schema.get("properties", {})):
                described += 1
        placed = await page.locator(".where a, .where.agents").count()
        places: list[str] = await page.locator(".where a").evaluate_all(
            "els => els.map(e => e.getAttribute('href'))"
        )
        text = await page.locator("#page").inner_text()
        proof(
            "M20",
            "the Tools page, opened from the Settings menu with the keyboard, lists exactly the 53 "
            "tools the server lists (same count, same names), each with its description and "
            "fields and a place on the board (a page, never a panel or a drawer) or the reason it "
            "is for agents only, and none of the tools that generated text or kept beliefs",
            sorted(names) == sorted(t.name for t in listed)
            and len(names) == 53
            and described == len(listed)
            and placed == len(listed)
            and all(re.fullmatch(r"#/[a-z]+", href or "") for href in places)
            and not re.search(r"belief|principle|language model", text, re.IGNORECASE),
            f"{len(listed)} tools",
        )
        word = unique_word()
        epic = await made(client, "epic", f"Tried {word}")
        epic_n = number_of(epic)
        shown = await try_it(page, "list_nodes", kind="epic", limit="3", query=f"epic:{epic_n}")
        mcp = await ok(client, "list_nodes", kind="epic", limit=3, query=f"epic:{epic_n}")
        recalled = await try_it(page, "recall", node=epic)
        refusal = await try_it(page, "get_note", note_id="999999999")
        said = await refused(client, "get_note", note_id=999999999)
        changing = page.locator("#tool-list article.tool[data-changes='1']")
        owner = project_at(page_url).owner.sign_in_again()
        tried = owner.call(
            "POST",
            f"/board/api/projects/{project_at(page_url).id}/try/create_node",
            {"actor": "x", "kind": "epic", "title": "Must not exist"},
        )
        proof(
            "M14",
            "Try it on a tool that reads returns what MCP returns, in the same words for a "
            "refusal; no tool that changes anything has a Try it, and the server turns a try of "
            "one away",
            shown == mcp
            and recalled == await ok(client, "recall", node=epic)
            and same_words(refusal["refused"], said)
            and await changing.count() > 20
            and await changing.locator("details.try").count() == 0
            and {n for n in names if await page.locator(f"#tool-entry-{n} details.try").count()}
            == reads
            and tried.status == 403
            and "Must not exist" not in json.dumps(tried.body),
            f"{len(reads)} read, {await changing.count()} change something",
        )
        assert w.problems == []
    dropped = classification("reindex")
    restored = classification(None)
    proof(
        "M14",
        "the test that every tool is on the board or marked for agents only fails when reindex is "
        "taken out of the map, naming it, and passes again with it put back",
        dropped.returncode != 0
        and "reindex is neither on the board nor marked as for agents only"
        in dropped.stdout + dropped.stderr
        and restored.returncode == 0,
        "shown once, then restored",
    )


# -- Status: server_status, the index, the export, and no secret -----------------------------


async def status(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Status {word}")
    await reindexed(client)

    async def row(page: Page, name: str) -> str:
        return (
            await page.locator(f"#status-body tr[data-name={name}] .value").inner_text()
        ).strip()

    secrets = {
        "the agent key": the_token(),
        "the owner's password": project_at(page_url).owner.password,
        "the database password": os.environ["VRUM_DB_PASSWORD"],
    }
    async with browser_page(page_url, token=the_token(), name=ADA, keep_bodies=True) as w:
        page = w.page
        await w.open("#/graph")
        await open_settings(page, "status")
        await expect(page.locator("#versions")).to_be_visible(timeout=SLOW)
        said = await ok(client, "server_status")
        index = (await ok(client, "search", query=word, limit=1))["index"]
        equal = (
            await row(page, "vrum_version") == said["vrum_version"]
            and await row(page, "postgres_version") == said["postgres_version"]
            and await row(page, "pgvector_version") == said["pgvector_version"]
            and set(said["counts"]) == set(COUNTS)
            and all([await row(page, name) == str(said["counts"][name]) for name in COUNTS])
            and await row(page, "waiting") == str(index["waiting_to_index"])
            and await row(page, "indexed") == str(said["index"]["indexed"])
            and await row(page, "model") == said["index"]["embedding_model"]
            and await row(page, "vector_size") == str(said["index"]["vector_size"])
            and "language_model" not in said
            and await page.locator("#language-model").count() == 0
        )
        proof(
            "M14",
            "Status equals server_status (versions, the size of each kind of item, the index, its "
            "model and vector size) and the index state a search reports, and says nothing of a "
            "language model",
            equal and said["index"]["meaning"] == index["meaning"],
            f"{said['counts']['events']} changes in the history",
        )
        with embeddings_stopped():
            await made(client, "story", f"Not indexed {word}", epic)
            service = page.locator("#status-body tr[data-name=service] .value")
            await expect(service).to_have_text("Not answering", timeout=SLOW)
            seen = (await ok(client, "server_status"))["index"]
            count = str(seen["waiting_to_index"])
            # The page and the top bar each hear of it at their own next look.
            await expect(page.locator("#status-body tr[data-name=waiting] .value")).to_have_text(
                count, timeout=SLOW
            )
            await expect(page.locator("#index-state")).to_contain_text(
                f"{count} waiting", timeout=SLOW
            )
            stopped = await row(page, "service") == "Not answering"
            waiting = await row(page, "waiting") == count
            chip = await page.locator("#index-state").inner_text()
        await reindexed(client)
        await expect(service).to_have_text("Answering", timeout=SLOW)
        proof(
            "M14",
            "with the embedding service stopped Status says it is not answering and counts the "
            "item waiting, as server_status does, and says so again when it is back",
            stopped
            and waiting
            and seen["embedding_service"] == "unreachable"
            and f"{seen['waiting_to_index']} waiting" in chip,
            f"{seen['waiting_to_index']} waiting",
        )
        await page.locator("#download-export").focus()
        async with page.expect_download() as waiting_for:
            await page.keyboard.press("Enter")
        download = await waiting_for.value
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "export.json"
            await download.save_as(path)
            got: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        proof(
            "M14",
            "the export downloaded with the keyboard is identical to the MCP export, every page "
            "of it, with the notes and what a person confirmed",
            got == await export_everything(client)
            and word in json.dumps(got)
            and "notes" in got
            and "facts" not in got
            and "decisions" not in got
            and "memories" not in got,
            f"{got['counts']['nodes']} nodes, {got['counts']['events']} events",
        )
        for address_ in (
            "#/board",
            "#/graph?tab=around",
            "#/roadmap",
            "#/activity",
            f"#/search?search={word}",
            "#/coverage",
            item_address(epic, "board"),
            "#/access",
            "#/tools",
        ):
            await w.go(address_)
            await page.wait_for_load_state("networkidle")
        texts = await page.locator("body").inner_text()
        proof(
            "M14",
            "no session, agent key or database password is in any response the board's pages "
            "received (searched for in every one), no page, no address and no downloaded file",
            len(w.bodies) > 20
            and all(
                not [u for u, body in w.bodies if secret in body]
                and secret not in texts
                and not [u for u in w.requests if secret in u]
                and secret not in json.dumps(got)
                for secret in secrets.values()
            ),
            f"{len(w.bodies)} responses searched",
        )


# -- M16 proof 6: from the keyboard, an address reopens the state, a draft is kept ------------


async def claims_on_page(page: Page, section: str) -> set[tuple[str, str]]:
    rows: list[list[str]] = await page.locator(f"#{section} li.claim").evaluate_all(
        "els => els.map(e => [e.dataset.node, e.dataset.actor])"
    )
    return {(node, who) for node, who in rows}


async def keyboard_and_addresses(client: Client, page_url: str) -> None:
    word = unique_word()
    agent, finisher, ann = f"agent-{word}", f"finisher-{word}", f"Ann {word}"
    epic = await made(client, "epic", f"Everything {word}")
    story = await made(client, "story", f"Story {word}", epic)
    held, finished, loose = [
        await made(client, "task", f"{name} {word}", story)
        for name in ("Held", "Finished", "Loose")
    ]
    note = f"Done for {word}.\nNothing is left."
    await ok(client, "claim_node", actor=agent, node=held, lease_seconds=600)
    await ok(client, "claim_node", actor=finisher, node=finished)
    await ok(client, "finish_work", actor=finisher, node=finished, outcome="done", note=note)
    await ok(client, "claim_node", actor=ann, node=loose, lease_seconds=900)
    decided = await make_note(client, f"Decided {word}", decided_by="Marie", nodes=[story])
    plain = await make_note(client, f"Known {word}", "Observed", nodes=[story])
    await reindexed(client)
    async with browser_page(page_url, token=the_token()) as w:
        page = w.page
        await w.open("#/roadmap")
        # Activity: it is in the sidebar right before Settings, so Shift+Tab from the Settings
        # button, then Enter, and nothing else.
        await page.locator("#settings-menu").focus()
        await page.keyboard.press("Shift+Tab")
        focused = await page.evaluate("document.activeElement.id")
        await page.keyboard.press("Enter")
        await on_page(page, "activity")
        await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
        newest = (await ok(client, "changes_since", newest=True, limit=100))["items"]
        wanted = [e["id"] for e in reversed(newest)]
        await expect(page.locator("#feed li.event")).to_have_count(len(wanted), timeout=SLOW)
        feed_equal = await feed_ids(page) == wanted
        handoff = page.locator("#feed li.event[data-action=handoff]", has_text=finisher)
        note_in_full = spoken(await handoff.locator(".handoff-note").inner_text()) == spoken(note)
        who = (await ok(client, "who_is_working"))["agents"]
        live = {(m["node"], a["actor"]) for a in who for m in a["holding"]}
        claims_equal = {(held, agent), (loose, ann)} <= live and await claims_on_page(
            page, "claims-live"
        ) == live
        on_activity = (
            await sidebar_current(page) == ["activity"]
            and await page.locator("#activity-open").get_attribute("aria-current") == "page"
        )
        # Tools through the Settings menu, with the same 53 tools the server lists.
        await open_settings(page, "tools")
        listed = {tool.name for tool in (await client.list_tools()).tools}
        names: list[str] = await page.locator("#tool-list article.tool").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        )
        # Status: the health, the sizes and the index.
        await open_settings(page, "status")
        await expect(page.locator("#versions")).to_be_visible(timeout=SLOW)
        status = await ok(client, "server_status")
        await expect(page.locator("#versions .ok")).to_contain_text("can reach its database")
        sizes = [
            (await page.locator(f"#status-body tr[data-name={name}] .value").inner_text()).strip()
            == str(count)
            for name, count in status["counts"].items()
        ]
        assert w.problems == []
        proof(
            "M20",
            "the Activity page opens from the keyboard alone (Shift+Tab from the Settings "
            "button, then Enter; the sidebar marks it as the page the person is on) and shows the "
            "server's newest changes with a handoff note in full and who is working on what "
            "exactly as MCP lists them; the Tools and Status pages open from the Settings menu "
            "with the keys and show the 53 tools and the health, sizes and index server_status "
            "gives",
            focused == "activity-open"
            and on_activity
            and feed_equal
            and note_in_full
            and claims_equal
            and sorted(names) == sorted(listed)
            and len(names) == 53
            and all(sizes)
            and status["index"]["embedding_service"] == "answering",
            f"{len(wanted)} changes, {len(live)} claims, {len(names)} tools",
        )

    # The state is made with the board's own controls, and the same address in another browser is
    # the same state, search and records included.
    wanted_notes = await asked(client, word, "both", types=["note"])
    key = f"note:{plain['id']}"
    scope = str(number_of(epic))
    async with browser_page(page_url, token=the_token()) as first:
        page = first.page
        await first.open(f"#/graph?tab=around&focus={key}")
        await expect(page.locator("#graph-hub")).to_have_attribute("data-ref", key, timeout=SLOW)
        graph_url = page.url
        name, choices = await address(page)
        graph_exact = name == "graph" and choices == {"tab": "around", "focus": key}
        await page.locator("#global-search").fill(word)
        await page.keyboard.press("Enter")
        await on_page(page, "search")
        await result_rows(page, 7)
        await page.locator("#search-facets [data-facet=type] button[data-value=note]").click()
        await result_rows(page, 2)
        search_url = page.url
        name, choices = await address(page)
        search_exact = name == "search" and choices == {"search": word, "search_type": "note"}
        await page.locator(f"#search-results tr.result[data-ref='{key}'] a.result-title").click()
        await item_loaded(page, key)
        url = page.url
        name, choices = await address(page)
        exact = (
            graph_exact
            and search_exact
            and name == "item"
            and choices == {"item": key, "from": "search"}
        )
        await page.go_back()
        await on_page(page, "search")
        await result_rows(page, 2)
        back_at_search = page.url == search_url
        await page.go_forward()
        await item_loaded(page, key)
        history_walks = back_at_search and page.url == url
        assert first.problems == []
    async with browser_page(page_url, token=the_token()) as second:
        page = second.page
        await page.goto(graph_url)
        await expect(page.locator("#graph-hub")).to_have_attribute("data-ref", key, timeout=SLOW)
        focus_again = True
        await go(page, page_url, hash_of(search_url))
        await page.wait_for_selector("#search-box", timeout=SLOW)
        await expect(page.locator("#search-box")).to_have_value(word)
        rows = await result_rows(page, 2)
        same_results = shown_triples(rows) == triples(wanted_notes["items"])
        await go(page, page_url, hash_of(url))
        await item_loaded(page, key)
        # After a reload the way back is the page itself, with nothing of the search remembered.
        arrived = (
            (await crumbs(page))[2] == "Search"
            and await view_crumb(page).get_attribute("href") == "#/search"
            and await sidebar_current(page) == []
        )
        assert second.problems == []
    # The Board, the Roadmap, Possible duplicates and Activity hold their scope and filters in the
    # address too, and so does the page of a record: the view it was reached from.
    board_address = f"#/board?epic={scope}&q=kind:story"
    roadmap_address = f"#/roadmap?q=epic:{scope}&zoom=quarter"
    duplicates_address = f"#/search?dups=1&dup_min=0.95&dup_epics={scope}"
    activity_address = "#/activity?act_actor=agent-1&act_kind=notes"
    decision_ref = f"note:{decided['id']}"
    async with browser_page(page_url, token=the_token()) as third:
        page = third.page
        await third.open(board_address)
        await expect(page.locator("#epic-filter")).to_have_value(scope, timeout=SLOW)
        await expect(page.locator("#filter")).to_have_value("kind:story", timeout=SLOW)
        wanted_cards = await ids_of(client, f"kind:story epic:{scope}")
        await expect(page.locator(".card")).to_have_count(len(wanted_cards), timeout=SLOW)
        cards: list[str] = await page.locator(".card").evaluate_all(
            "els => els.map(e => e.dataset.ref)"
        )
        board_state = cards == wanted_cards
        await third.go(roadmap_address)
        await on_view(page, "roadmap")
        await expect(page.locator(".rm-track")).to_have_attribute(
            "data-zoom", "quarter", timeout=SLOW
        )
        await expect(page.locator("#filter")).to_have_value(f"epic:{scope}", timeout=SLOW)
        roadmap_state = True
        await third.go(duplicates_address)
        await on_page(page, "search")
        await expect(page.locator("#search-tab-dups")).to_have_attribute("aria-pressed", "true")
        await expect(page.locator("#dup-min")).to_have_value("0.95", timeout=SLOW)
        await expect(page.locator("#dup-epics")).to_have_value(scope, timeout=SLOW)
        duplicates_state = True
        await third.go(activity_address)
        await on_page(page, "activity")
        await expect(page.locator("#a-actor")).to_have_value("agent-1", timeout=SLOW)
        await expect(page.locator("#a-kind")).to_have_value("notes", timeout=SLOW)
        activity_state = True
        await third.go(item_address(decision_ref, "roadmap"))
        await item_loaded(page, decision_ref)
        record_state = (await crumbs(page))[2] == "Roadmap" and await sidebar_current(page) == [
            "roadmap"
        ]
        assert third.problems == []
    reopened = {
        "the addresses made with the controls": exact,
        "the browser's history": history_walks,
        "the Graph's focus": focus_again,
        "the search results": same_results,
        "the page of a record after a reload": arrived,
        "the Board": board_state,
        "the Roadmap": roadmap_state,
        "Possible duplicates": duplicates_state,
        "Activity": activity_state,
        "the page of a record in a view": record_state,
    }
    proof(
        "M20",
        "search and view addresses reopen the state they name: the Graph's tab and focus made "
        "with the address, the Search page's words and kind filter, "
        "and the page of a record (with the view it was reached from) are the same in a second "
        "browser, the browser's history walks back and forward through them, and the Board, the "
        "Roadmap, Possible duplicates and Activity reopen their scope, filter, zoom and filters",
        all(reopened.values()),
        ", ".join(name for name, fine in reopened.items() if not fine) or url.split("#", 1)[1][:70],
    )


async def drafts(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Draft {word}")
    story = await made(client, "story", f"Story {word}", epic)
    await made(client, "task", f"First task {word}", story)
    elsewhere = await made(client, "epic", f"Elsewhere {word}")
    draft, unasked = f"Typed on the page and not saved {word}", f"typed in the search box {word}"
    async with browser_page(page_url, token=the_token()) as w:
        page = w.page
        await open_item(w, story)
        await open_editor(page)
        await page.locator("#edit-body").fill(draft)
        await page.evaluate("window.__body = document.querySelector('#edit-body')")
        steps = 0

        async def kept(step: str) -> None:
            nonlocal steps
            assert await page.evaluate("document.querySelector('#edit-body') === window.__body"), (
                step
            )
            assert await page.locator("#edit-body").input_value() == draft, step
            steps += 1

        await page.locator("#global-search").fill(unasked)
        await kept("words typed in the search box")
        await page.keyboard.press("Control+k")
        await expect(page.locator("#switcher")).to_be_visible(timeout=SLOW)
        await page.keyboard.type(word)
        await expect(page.locator("#switcher [role=option]").first).to_be_visible(timeout=SLOW)
        await page.keyboard.press("Escape")
        await expect(page.locator("#switcher")).to_have_count(0)
        await kept("the quick switcher")
        await page.locator("#settings-menu").click()
        await expect(page.locator("#settings-list")).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(page.locator("#settings-list")).to_be_hidden()
        await kept("the Settings menu")
        await tree_expand(page, epic, expand=False)
        await kept("closing the epic in the sidebar's tree")
        await tree_expand(page, epic)
        await tree_expand(page, story)
        await kept("opening the epic and the story in the tree")
        assert await page.locator("#global-search").input_value() == unasked
        # Agents change things while the draft is open: the page follows what they did (a task under
        # the story, a note about it, another epic renamed in the tree) and leaves the draft be.
        added = await made(client, "task", f"Added meanwhile {word}", story)
        decided = await make_note(
            client, f"Decided meanwhile {word}", decided_by="Marie", nodes=[story]
        )
        await ok(
            client, "update_node", actor="agent", node=elsewhere, expected_version=1,
            title=f"Renamed {word}",
        )  # fmt: skip
        await expect(page.locator(f"#item-table tbody tr[data-ref='{added}']")).to_be_visible(
            timeout=NEWS
        )
        # The sections the person is not typing in are not drawn over while they type: the page says
        # that something changed and leaves the draft, and the note waits for them to ask.
        banner = page.locator("#item-details .notice.changed")
        await expect(banner).to_contain_text("while you were typing", timeout=NEWS)
        waits = await page.locator(
            f"#item-page tr.record-line[data-ref='note:{decided['id']}']"
        ).count()
        await expect(
            tree_item(page, elsewhere).locator(":scope > .tree-row .tree-title")
        ).to_have_text(f"Renamed {word}", timeout=NEWS)
        await kept("changes an agent made meanwhile")
        before = (await ok(client, "get_node", node=story))["body"]
        await page.get_by_role("button", name="Save changes").click()
        await expect(item_page(page).locator(".card-detail")).to_contain_text(draft, timeout=SLOW)
        saved = (await ok(client, "get_node", node=story))["body"]
        assert w.problems == []
    proof(
        "M20",
        "a draft typed in the edit form on the page of a story (and words typed in the search box "
        "and not asked yet) survives the quick switcher, the Settings menu, closing and opening "
        "branches of the sidebar's tree and changes an agent makes meanwhile (a new task and "
        "another epic renamed, shown as they happen; a note about the story announced in a "
        "notice and left for the person to ask for), in the very same element; nothing is saved "
        "until the person saves it",
        before == "" and saved == draft and waits == 0,
        f"{steps} places checked",
    )


async def connection_failure(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Down {word}")
    await made(client, "story", f"Story {word}", epic)
    poll = board_call(page_url, "GET", "/board/api/info").body["poll_seconds"]
    wait = int((3 * poll + 5) * 1000)

    async def unreachable(route: Route) -> None:
        await route.abort()

    changes = "**/board/api/projects/*/tool/changes_since"
    async with browser_page(page_url, token=the_token()) as w:
        page = w.page
        note, chip = page.locator("#connection-note"), page.locator("#connection")
        await w.open("#/board")
        await expect(page.locator(f".lane[data-ref='{epic}']")).to_be_visible(timeout=SLOW)
        quiet = await note.is_hidden() and await chip.is_hidden()
        await page.route(changes, unreachable)
        await expect(note).to_be_visible(timeout=wait)
        await expect(note).to_contain_text("cannot reach the server")
        await expect(chip).to_have_text("Cannot reach the server: trying again")
        above, main = await note.bounding_box(), await page.locator("#page").bounding_box()
        in_view = bool(above and main and above["y"] + above["height"] <= main["y"] + 1)
        lane_kept = await page.locator(f".lane[data-ref='{epic}']").is_visible()
        stays = []
        for view, marker in (("graph", "#graph-explore"), ("roadmap", ".rm-track")):
            await page.locator(f"#main-nav a[data-page={view}]").click()
            await on_view(page, view)
            await expect(page.locator(marker)).to_be_visible(timeout=SLOW)
            stays.append(await note.is_visible())
        await w.go(item_address(epic, "roadmap"))
        await item_loaded(page, epic)
        stays.append(await note.is_visible())
        await page.locator("#activity-open").click()
        await on_page(page, "activity")
        stays.append(await note.is_visible())
        await open_settings(page, "status")
        await expect(page.locator("#versions")).to_be_visible(timeout=SLOW)
        stays.append(await note.is_visible())
        await page.unroute(changes, unreachable)
        await expect(note).to_be_hidden(timeout=wait)
        await w.go("#/board")
        everything = "**/board/api/projects/*/tool/*"
        await page.route(everything, unreachable)
        await page.locator("#global-search").fill(word)
        await page.keyboard.press("Enter")
        await on_page(page, "search")
        await expect(page.locator("#search-problem")).to_contain_text(
            "The board cannot reach the server", timeout=SLOW
        )
        await w.go("#/board")
        await page.locator("#new-item").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#new-title").fill(f"Not made {word}")
        await dialog.get_by_role("button", name="Create").click()
        await expect(dialog.locator("form > .problem")).to_contain_text(
            "The board cannot reach the server", timeout=SLOW
        )
        await dialog.get_by_role("button", name="Cancel").click()
        await page.unroute(everything, unreachable)
        not_made = await ids_of(client, f"Not made {word}") == []
        assert [p for p in w.problems if "Failed to load resource" not in p] == []
    proof(
        "M20",
        "a lost connection is told in the page the person is on, above the page and as a chip, in "
        "plain words, while what was drawn stays, it stays through the other views, the page of "
        "a record, the Activity page and the settings pages and goes by itself when the server "
        "answers, and what the person does while it is gone (a search, a new item) fails in the "
        "place they did it, writing nothing",
        quiet and in_view and lane_kept and all(stays) and not_made,
        f"{len(stays)} places checked",
    )
