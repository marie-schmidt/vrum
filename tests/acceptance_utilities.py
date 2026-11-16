"""Chapter 12 of the acceptance scenario: the Activity drawer and the Settings pages Tools and
Status (M14 and M16), driven in a real headless browser against the real containers, with what a
page shows compared with what a real MCP client gets from the same tools. What agents and people do
shows in the Activity drawer over any view; the Tools page is the server's own list of 53 tools;
Status is `server_status` and the export, and no secret is ever shown. All three open from the
keyboard, an address reopens the state it names, a draft is not lost, and a lost connection is
told in the view the person is on."""

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
from tests.board_helpers import SLOW, board_call, board_url, browser_page, made
from tests.board_m14 import events_of, feed_becomes, feed_ids, feed_row, ids_of, spoken
from tests.board_search import (
    address,
    asked,
    drawer,
    on_view,
    open_settings,
    result_rows,
    shown_triples,
    triples,
)
from tests.helpers import (
    REPO_ROOT,
    confirm,
    connect,
    day,
    embeddings_stopped,
    export_everything,
    make_decision,
    make_fact,
    ok,
    refused,
    reindexed,
    the_token,
    unique_word,
)
from tests.world import project_at

ADA = "Ada Lovelace"
NEWS = 15_000
COUNTS = ("epics", "stories", "tasks", "links", "rules", "decisions", "facts", "events")


async def tools_listed(page: Page) -> None:
    await expect(page.locator("#tool-list article.tool").first).to_be_visible(timeout=SLOW)


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
    chapter("Seeing it all: the Activity drawer, Tools and Status, from the keyboard")
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
    rule = await ok(client, "create_rule", actor="a", title=f"Rule {word}", story_id=story)
    note = f"Built the whole thing for {word}. Nothing is left, but check the logs."
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/roadmap?drawer=activity&act_actor={agent}")
        await expect(drawer(page, "activity")).to_be_visible(timeout=SLOW)
        await on_view(page, "roadmap")
        await expect(page.locator("#activity-count")).to_have_text(
            "Nothing has happened yet.", timeout=SLOW
        )
        await page.evaluate("window.__same = 'same page'")
        taken = await ok(client, "next_work", actor=agent, within_node_id=epic)
        claim = page.locator(f"#claims-live li.claim[data-node='{task}']")
        await expect(claim).to_contain_text(f"held by {agent}", timeout=NEWS)
        agent_row = page.locator(f"#agents li.agent[data-actor='{agent}']")
        working = await agent_row.get_attribute("data-state")
        counting = await claim.locator(".countdown").inner_text()
        await ok(
            client, "update_rule", actor=agent, rule_id=rule["id"], expected_version=1,
            title=f"Renamed {word}",
        )  # fmt: skip
        decided = await make_decision(client, f"Decided {word}", actor=agent, node_ids=[story])
        await ok(client, "finish_work", actor=agent, node_id=task, outcome="done", note=note)
        done = feed_row(page, f"finished their work on task #{task}")
        await expect(done).to_contain_text(agent, timeout=NEWS)
        rows = {
            "took": await feed_row(page, f"took task #{task}").inner_text(),
            "rule": await feed_row(page, f"changed requirement {rule['id']}").inner_text(),
            "decision": await feed_row(page, f"recorded decision {decided['id']}").inner_text(),
        }
        await expect(claim).to_have_count(0, timeout=NEWS)
        await expect(agent_row).to_have_attribute("data-state", "finished", timeout=NEWS)
        shown = await feed_ids(page)
        same_news = await page.evaluate("window.__same") == "same page"
        proof(
            "M14",
            "through MCP an agent takes work with next_work, edits a requirement, records a "
            "decision and finishes with a handoff note; within seconds, with no reload, the "
            "Activity drawer over the Roadmap shows each in plain words with the actor, the "
            "handoff note in full, newest first and equal to the server's feed, and the claim in "
            "who is working on what (with a countdown) until it is finished",
            taken["found"]
            and all(agent in text for text in rows.values())
            and f"“Rule {word}” → “Renamed {word}”" in rows["rule"]
            and f"“Decided {word}”, decided by Marie" in rows["decision"]
            and spoken(await done.locator(".handoff-note").inner_text()) == note
            and working == "working"
            and "left" in counting
            and same_news
            and shown == sorted(shown, reverse=True)
            and shown == [e["id"] for e in reversed(await events_of(client, actor=agent))],
            f'"{clip(rows["took"], 60)}"',
        )

    # -- the two records, told with who did what and on whose word
    second_agent = f"agent2-{word}"
    other = await made(client, "story", f"Other {word}", epic)
    wide = await make_decision(
        client, f"Use email {word}", actor=second_agent, node_ids=[story], project_wide=True
    )
    fact = await make_fact(
        client, f"People log in on phones {word}", actor=second_agent, node_ids=[story]
    )
    await confirm(client, "decision", wide, person, actor=second_agent)
    await confirm(client, "fact", fact, person, actor=second_agent)
    await ok(
        client, "set_confirmation", actor=second_agent, kind="fact", record_id=fact["id"],
        confirmation="unconfirmed", person=person, quote="I am not sure of this any more.",
    )  # fmt: skip
    replacement = await ok(
        client, "supersede_decision", actor=second_agent, decision_id=wide["id"],
        title=f"Use a code {word}", text="Log in with a code.", reason="Marie changed her mind.",
        decided_by="Marie", said_by=person, quote="Let us have both.",
    )  # fmt: skip
    extra = await make_fact(client, f"Codes arrive quickly {word}", actor=second_agent)
    await ok(client, "link_fact", actor=second_agent, fact_id=extra["id"], node_ids=[other])
    await ok(
        client, "withdraw_fact", actor=second_agent, fact_ids=[extra["id"]],
        reason="It was a guess", said_by=person, quote="No, we never measured it.",
    )  # fmt: skip
    events = await events_of(client, actor=second_agent)
    async with browser_page(page_url, token=the_token()) as w:
        page = w.page
        await w.open(f"#/graph?drawer=activity&act_actor={second_agent}")
        await expect(page.locator("#feed li.event")).to_have_count(len(events), timeout=SLOW)
        same_feed = await feed_ids(page) == [e["id"] for e in reversed(events)]

        async def said(action: str, record: int) -> str:
            found = page.locator(f"#feed li.event[data-action={action}]").filter(
                has=page.locator(f"a.item-link[data-id='{record}']")
            )
            words = found.first.locator(".event-words")
            await expect(words).to_be_visible()
            return spoken(await words.inner_text())

        t_dec, t_fact = f"“Use email {word}”", f"“People log in on phones {word}”"
        told = [
            await said("decision_recorded", wide["id"])
            == f"recorded decision {wide['id']} {t_dec}, decided by Marie, for the whole project.",
            await said("fact_confirmed", fact["id"])
            == f"recorded that {person} confirmed fact {fact['id']} {t_fact}.",
            await said("fact_unconfirmed", fact["id"])
            == f"took back the confirmation of fact {fact['id']} {t_fact}, on the word of "
            f"{person}: “I am not sure of this any more.”.",
            await said("decision_superseded", wide["id"])
            == f"replaced decision {wide['id']} {t_dec} with decision {replacement['id']} "
            f"“Use a code {word}”, on the word of {person}: “Let us have both.”.",
            await said("fact_withdrawn", extra["id"])
            == f"withdrew fact {extra['id']} “Codes arrive quickly {word}”: It was a guess, "
            f"on the word of {person}: “No, we never measured it.”.",
        ]
        kinds_hold = True
        for kind, prefix in (("facts", "fact_"), ("decisions", "decision_")):
            await w.go(f"#/graph?drawer=activity&act_actor={second_agent}&act_kind={kind}")
            wanted = [e["id"] for e in reversed(events) if e["action"].startswith(prefix)]
            await expect(page.locator("#feed li.event")).to_have_count(len(wanted), timeout=SLOW)
            kinds_hold = kinds_hold and await feed_ids(page) == wanted
        assert w.problems == []
    proof(
        "M16",
        "the Activity drawer tells the two records in plain words: who recorded a fact or a "
        "decision, who confirmed it, whose word took a confirmation back or replaced and "
        "withdrew a record, and for the whole project when it is; the kinds Facts and Decisions "
        "filter the feed to those events alone",
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
    await ok(client, "claim_node", actor=f"agent-{word}", node_id=theirs, lease_seconds=900)
    async with browser_page(page_url, token=the_token(), name=name) as w:
        page = w.page
        await w.open("#/board?drawer=activity")
        await expect(page.locator("#acting-as")).to_contain_text(f"act as {name}", timeout=SLOW)
        await page.locator("#lease").fill("60")
        await page.locator("#claim-id").fill(f"#{mine}")
        await page.locator("#claim-by-id-go").click()
        held = page.locator(f"#claims-live li.claim[data-node='{mine}']")
        await expect(held).to_contain_text(f"held by {name}", timeout=NEWS)
        claimed_by = (await ok(client, "get_node", node_id=mine))["claimed_by"]
        before = (await ok(client, "get_node", node_id=mine))["claim_expires_at"]
        await held.locator("button.renew").click()
        await expect(feed_row(page, f"{name} kept task #{mine}")).to_be_visible(timeout=NEWS)
        after = (await ok(client, "get_node", node_id=mine))["claim_expires_at"]
        await held.locator("button.release").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#release-note").fill(f"Stopped here for {word}.")
        await dialog.get_by_role("button", name="Release it").click()
        await expect(dialog).to_have_count(0, timeout=SLOW)
        released = await ok(client, "get_node", node_id=mine)
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
        said = await refused(client, "release_claim", actor=name, node_id=theirs, note="Not mine.")
        await dialog.get_by_role("button", name="Cancel").click()
        proof(
            "M14",
            "in the browser a person claims a task with a typed name, keeps it with Renew (a "
            "longer lease), and lets it go with a note, each the same operation as the agent's; "
            "another holder's claim cannot be released by that name, in the server's words",
            claimed_by == name
            and after > before
            and released["claimed_by"] is None
            and released["last_handoff"]["actor"] == name
            and released["last_handoff"]["note"] == f"Stopped here for {word}."
            and same_words(refusal_words, said)
            and (await ok(client, "get_node", node_id=theirs))["claimed_by"] == f"agent-{word}",
            f"#{mine} as {name}",
        )
        await page.locator("#lease").fill("3")
        await page.locator("#claim-id").fill(str(short))
        await page.locator("#claim-by-id-go").click()
        live = page.locator(f"#claims-live li.claim[data-node='{short}']")
        await expect(live).to_be_visible(timeout=NEWS)
        lapsed = page.locator(f"#claims-lapsed li.claim[data-node='{short}']")
        await expect(lapsed).to_be_visible(timeout=NEWS)
        state = await page.locator(f"#agents li.agent[data-actor='{name}']").get_attribute(
            "data-state"
        )
        node = await ok(client, "get_node", node_id=short)
        who = {a["actor"]: a for a in (await ok(client, "who_is_working"))["agents"]}
        proof(
            "M14",
            "a claim that runs out moves by itself to lapsed, and the holder's state follows; the "
            "server says the same through get_node and who_is_working",
            state == "lapsed"
            and node["claimed_by"] is None
            and node["lapsed_claim_by"] == name
            and who[name]["state"] == "lapsed",
            f"#{short}",
        )
        assert w.problems == []


async def filters(client: Client, page_url: str) -> None:
    word = unique_word()
    ann, bob = f"ann-{word}", f"bob-{word}"
    epic = await ok(client, "create_node", actor=ann, kind="epic", title=f"Epic {word}")
    one = await ok(
        client, "create_node", actor=ann, kind="story", title=f"One {word}", parent_id=epic["id"]
    )
    two = await ok(
        client, "create_node", actor=bob, kind="story", title=f"Two {word}", parent_id=epic["id"]
    )
    rule = await ok(client, "create_rule", actor=ann, title=f"Rule {word}", story_id=one["id"])
    await ok(
        client, "move_rule", actor=ann, rule_id=rule["id"], expected_version=1,
        new_story_id=two["id"],
    )  # fmt: skip
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/board?drawer=activity")
        held = []
        for choices, wanted in (
            (f"act_actor={ann}", await events_of(client, actor=ann)),
            (f"act_item=rule:{rule['id']}", await events_of(client, rule_id=rule["id"])),
            (f"act_item=%23{one['id']}", await events_of(client, node_id=one["id"])),
        ):
            await w.go(f"#/board?drawer=activity&{choices}")
            ids = [e["id"] for e in reversed(wanted)]
            held.append(await feed_becomes(page, ids) == ids)
        await w.go(f"#/board?drawer=activity&act_actor={ann}&act_kind=requirements")
        await expect(feed_row(page, f"{ann} moved requirement {rule['id']}")).to_contain_text(
            f"from story #{one['id']} “One {word}” to story #{two['id']} “Two {word}”",
            timeout=SLOW,
        )
        rules_only = [
            e["id"]
            for e in reversed(await events_of(client, actor=ann))
            if e["action"].startswith("rule_")
        ]
        kind_holds = await feed_becomes(page, rules_only) == rules_only
        await w.go(f"#/board?drawer=activity&act_actor={ann}&act_from={day(1)}")
        await expect(page.locator("#activity-count")).to_contain_text(
            "Nothing read so far matches", timeout=SLOW
        )
        today = [e["id"] for e in reversed(await events_of(client, actor=ann))]
        await w.go(f"#/board?drawer=activity&act_actor={ann}&act_from={day()}&act_to={day()}")
        on_the_day = await feed_becomes(page, today)
        proof(
            "M14",
            "the Activity drawer is filterable by actor, kind of change, item and time, each equal "
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
        text = await page.locator("#page").inner_text()
        proof(
            "M16",
            "the Tools page, opened from the Settings menu with the keyboard, lists exactly the 53 "
            "tools the server lists (same count, same names), each with its description and "
            "fields and a place on the board or the reason it is for agents only, and none of "
            "the tools that generated text or kept beliefs",
            sorted(names) == sorted(t.name for t in listed)
            and len(names) == 53
            and described == len(listed)
            and placed == len(listed)
            and not re.search(r"belief|principle|language model", text, re.IGNORECASE),
            f"{len(listed)} tools",
        )
        word = unique_word()
        epic = await made(client, "epic", f"Tried {word}")
        shown = await try_it(page, "list_nodes", kind="epic", limit="3", query=f"epic:{epic}")
        mcp = await ok(client, "list_nodes", kind="epic", limit=3, query=f"epic:{epic}")
        recalled = await try_it(page, "recall", node_id=str(epic))
        refusal = await try_it(page, "get_fact", fact_id="999999999")
        said = await refused(client, "get_fact", fact_id=999999999)
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
            and recalled == await ok(client, "recall", node_id=epic)
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
            stopped = await row(page, "service") == "Not answering"
            waiting = await row(page, "waiting") == str(seen["waiting_to_index"])
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
            "of it, with the facts and decisions and what a person confirmed",
            got == await export_everything(client)
            and word in json.dumps(got)
            and "facts" in got
            and "memories" not in got,
            f"{got['counts']['nodes']} nodes, {got['counts']['events']} events",
        )
        for address_ in (
            "#/board",
            "#/graph?view=context&as=list",
            "#/roadmap",
            "#/board?drawer=activity",
            f"#/graph?drawer=search&search={word}",
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


async def claims_on_page(page: Page, section: str) -> set[tuple[int, str]]:
    rows: list[list[str]] = await page.locator(f"#{section} li.claim").evaluate_all(
        "els => els.map(e => [e.dataset.node, e.dataset.actor])"
    )
    return {(int(node), who) for node, who in rows}


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
    await ok(client, "claim_node", actor=agent, node_id=held, lease_seconds=600)
    await ok(client, "claim_node", actor=finisher, node_id=finished)
    await ok(client, "finish_work", actor=finisher, node_id=finished, outcome="done", note=note)
    await ok(client, "claim_node", actor=ann, node_id=loose, lease_seconds=900)
    decision = await make_decision(client, f"Decided {word}", node_ids=[story])
    fact = await make_fact(client, f"Known {word}", node_ids=[story])
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
        await expect(drawer(page, "activity")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
        newest = (await ok(client, "changes_since", newest=True, limit=100))["items"]
        wanted = [e["id"] for e in reversed(newest)]
        await expect(page.locator("#feed li.event")).to_have_count(len(wanted), timeout=SLOW)
        feed_equal = await feed_ids(page) == wanted
        handoff = page.locator("#feed li.event[data-action=handoff]", has_text=finisher)
        note_in_full = spoken(await handoff.locator(".handoff-note").inner_text()) == spoken(note)
        who = (await ok(client, "who_is_working"))["agents"]
        live = {(m["node_id"], a["actor"]) for a in who for m in a["holding"]}
        claims_equal = {(held, agent), (loose, ann)} <= live and await claims_on_page(
            page, "claims-live"
        ) == live
        await page.keyboard.press("Escape")
        await expect(page.locator("#drawer")).to_be_hidden()
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
            "M16",
            "the Activity drawer opens from the keyboard alone (Shift+Tab from the Settings "
            "button, then Enter) and shows the server's newest changes with a handoff note in "
            "full and who is working on what exactly as MCP lists them; the Tools and Status "
            "pages open from the Settings menu with the keys and show the 53 tools and the "
            "health, sizes and index server_status gives",
            focused == "activity-open"
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
    wanted_facts = await asked(client, word, "both", types=["fact"])
    key = f"fact:{fact['id']}"
    async with browser_page(page_url, token=the_token()) as first:
        page = first.page
        await first.open("#/graph")
        await expect(page.locator("#graph-page")).to_be_visible(timeout=SLOW)
        # Each choice is made once the page has taken the one before it, as a person would.
        await page.locator("#graph-view-context").click()
        await expect(page.locator("#graph-view-context")).to_have_attribute(
            "aria-pressed", "true", timeout=SLOW
        )
        await page.locator("#graph-as-list").click()
        await expect(page.locator("#graph-as-list")).to_have_attribute(
            "aria-pressed", "true", timeout=SLOW
        )
        await page.locator("#graph-epic").select_option(str(epic))
        await expect(page.locator("#graph-epic")).to_have_value(str(epic))
        await page.locator("#global-search").fill(word)
        await page.keyboard.press("Enter")
        await result_rows(page, 7)
        await page.locator("#search-facets [data-facet=type] button[data-value=fact]").click()
        await result_rows(page, 1)
        await page.locator("#search-results li.result a.result-title").click()
        await expect(page.locator(f"#panel .record-detail[data-record='{key}']")).to_be_visible(
            timeout=SLOW
        )
        url = page.url
        name, choices = await address(page)
        exact = name == "graph" and choices == {
            "view": "context",
            "as": "list",
            "epic": str(epic),
            "drawer": "search",
            "search": word,
            "search_type": "fact",
            "sel": key,
        }
        await page.go_back()
        await expect(page.locator("#panel")).to_be_hidden(timeout=SLOW)
        await page.go_forward()
        await expect(page.locator(f"#panel .record-detail[data-record='{key}']")).to_be_visible(
            timeout=SLOW
        )
        history_walks = page.url == url
        assert first.problems == []
    async with browser_page(page_url, token=the_token()) as second:
        page = second.page
        await page.goto(url)
        await page.wait_for_selector("#graph-page", timeout=SLOW)
        await expect(page.locator("#graph-view-context")).to_have_attribute(
            "aria-pressed", "true", timeout=SLOW
        )
        await expect(page.locator("#graph-as-list")).to_have_attribute(
            "aria-pressed", "true", timeout=SLOW
        )
        await expect(page.locator("#graph-epic")).to_have_value(str(epic), timeout=SLOW)
        await expect(page.locator(f"#panel .record-detail[data-record='{key}']")).to_be_visible(
            timeout=SLOW
        )
        await expect(page.locator(f".gv-record[data-key='{key}']")).to_have_attribute(
            "aria-current", "true", timeout=SLOW
        )
        await expect(page.locator("#search-box")).to_have_value(word)
        rows = await result_rows(page, 1)
        same_results = shown_triples(rows) == triples(wanted_facts["items"])
        await page.locator("#graph-as-map").click()
        await expect(page.locator(f".dot[data-key='{key}']")).to_have_class(
            re.compile(r"\bchosen\b")
        )
        scope_kept = await page.locator("#graph-epic").input_value() == str(epic)
        assert second.problems == []
    # The Board and the Roadmap hold their scope, filter, selection and drawers in the address too.
    board_address = (
        f"#/board?epic={epic}&q=kind:story&sel=node:{story}&drawer=activity&act_actor=agent-1"
        "&act_kind=decisions"
    )
    roadmap_address = (
        f"#/roadmap?q=epic:{epic}&zoom=quarter&sel=decision:{decision['id']}&drawer=search"
        f"&dups=1&dup_min=0.95&dup_epics={epic}"
    )
    async with browser_page(page_url, token=the_token()) as third:
        page = third.page
        await third.open(board_address)
        await expect(page.locator("#epic-filter")).to_have_value(str(epic), timeout=SLOW)
        cards: list[int] = await page.locator(".card").evaluate_all(
            "els => els.map(e => Number(e.dataset.id))"
        )
        board_state = (
            cards == await ids_of(client, f"kind:story epic:{epic}")
            and await page.locator("#filter").input_value() == "kind:story"
            and await page.locator("#a-actor").input_value() == "agent-1"
            and await page.locator("#a-kind").input_value() == "decisions"
        )
        await expect(page.locator("#panel #card-title")).to_contain_text(f"#{story} ")
        await third.go(roadmap_address)
        await on_view(page, "roadmap")
        await expect(page.locator(".rm-track")).to_have_attribute(
            "data-zoom", "quarter", timeout=SLOW
        )
        await expect(
            page.locator(f"#panel .record-detail[data-record='decision:{decision['id']}']")
        ).to_be_visible(timeout=SLOW)
        await expect(page.locator("#search-tab-dups")).to_have_attribute("aria-pressed", "true")
        roadmap_state = (
            await page.locator("#dup-min").input_value() == "0.95"
            and await page.locator("#dup-epics").input_value() == str(epic)
            and await page.locator("#filter").input_value() == f"epic:{epic}"
        )
        assert third.problems == []
    proof(
        "M16",
        "search and view addresses reopen the state they name: the Graph's preset, presentation, "
        "scope, search, kind filter and chosen record made with the controls are the same in a "
        "second browser, the browser's history walks back and forward through them, and the "
        "Board and the Roadmap reopen their scope, filter, zoom, chosen item and drawers",
        exact and history_walks and same_results and scope_kept and board_state and roadmap_state,
        url.split("#", 1)[1][:70],
    )


async def drafts(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Draft {word}")
    story = await made(client, "story", f"Story {word}", epic)
    draft, unasked = f"Typed in the panel and not saved {word}", f"typed in the search box {word}"
    async with browser_page(page_url, token=the_token()) as w:
        page = w.page
        await w.open(f"#/graph?sel=node:{story}")
        await expect(page.locator("#panel #card-title")).to_contain_text(f"#{story} ", timeout=SLOW)
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

        await page.locator("#activity-open").click()
        await expect(drawer(page, "activity")).to_be_visible(timeout=SLOW)
        await kept("activity drawer")
        await page.locator("#global-search").fill(word)
        await page.keyboard.press("Enter")
        await expect(drawer(page, "search")).to_be_visible(timeout=SLOW)
        await result_rows(page, 2)
        await kept("search drawer")
        await page.locator("#search-tab-dups").click()
        await expect(page.locator("#drawer")).to_have_attribute("data-mode", "duplicates")
        await kept("possible duplicates")
        await page.locator("#search-tab-results").click()
        await page.locator("#search-box").fill(unasked)
        for button in (
            "#graph-view-context",
            "#graph-as-list",
            "#graph-view-everything",
            "#graph-as-map",
            "#graph-view-work",
        ):
            await page.locator(button).click()
            await expect(page.locator(button)).to_have_attribute(
                "aria-pressed", "true", timeout=SLOW
            )
            await kept(button)
            assert await page.locator("#search-box").input_value() == unasked, button
            assert (await address(page))[1]["sel"] == f"node:{story}"
        for view in ("roadmap", "board", "graph"):
            await page.locator(f"#main-nav a[data-page={view}]").click()
            await on_view(page, view)
            await kept(f"the {view} view")
            assert (await address(page))[1]["sel"] == f"node:{story}"
        await page.locator("#drawer-close").click()
        await kept("closed drawer")
        before = (await ok(client, "get_node", node_id=story))["body"]
        await page.get_by_role("button", name="Save changes").click()
        await expect(page.locator("#panel .card-detail")).to_contain_text(draft, timeout=SLOW)
        saved = (await ok(client, "get_node", node_id=story))["body"]
        assert w.problems == []
    proof(
        "M16",
        "a draft typed in the detail panel (and words typed in the search box and not asked yet) "
        "survives opening each drawer and the other tab of the search drawer, five switches of "
        "the Graph's preset and presentation and a round of the three views, in the very same "
        "element; nothing is saved until the person saves it",
        before == "" and saved == draft,
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
        await expect(page.locator(f".lane[data-epic='{epic}']")).to_be_visible(timeout=SLOW)
        quiet = await note.is_hidden() and await chip.is_hidden()
        await page.route(changes, unreachable)
        await expect(note).to_be_visible(timeout=wait)
        await expect(note).to_contain_text("cannot reach the server")
        await expect(chip).to_have_text("Cannot reach the server: trying again")
        above, main = await note.bounding_box(), await page.locator("#page").bounding_box()
        in_view = bool(above and main and above["y"] + above["height"] <= main["y"] + 1)
        lane_kept = await page.locator(f".lane[data-epic='{epic}']").is_visible()
        stays = []
        for view, marker in (("graph", "#graph-page"), ("roadmap", ".rm-track")):
            await page.locator(f"#main-nav a[data-page={view}]").click()
            await on_view(page, view)
            await expect(page.locator(marker)).to_be_visible(timeout=SLOW)
            stays.append(await note.is_visible())
        await page.locator("#activity-open").click()
        await expect(drawer(page, "activity")).to_be_visible(timeout=SLOW)
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
        await expect(page.locator("#search-problem")).to_contain_text(
            "The board cannot reach the server", timeout=SLOW
        )
        await page.locator("#drawer-close").click()
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
        "M16",
        "a lost connection is told in the view the person is on, above the page and as a chip, in "
        "plain words, while what was drawn stays, it stays through the other views, the drawer "
        "and the settings pages and goes by itself when the server answers, and what the "
        "person does while it is gone fails in the place they did it, writing nothing",
        quiet and in_view and lane_kept and all(stays) and not_made,
        f"{len(stays)} places checked",
    )
