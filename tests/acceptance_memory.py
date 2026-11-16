"""Chapter 11 of the acceptance scenario: the Rules and Memory pages of the board (M12), driven in a
real headless browser against the real containers, with what happens read back through a real MCP
client. Each proof is shown the way a person would see it: what was done in the browser, and what
the graph then holds. The check against every belief uses the scripted language model of the
stack."""

from typing import Any

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import Locator, Page, expect

from tests.acceptance_scenario import URL, Failed, chapter, clip, proof
from tests.board_helpers import SLOW, board_url, browser_page, made
from tests.helpers import (
    ScriptedModel,
    connect,
    make_belief,
    make_decision,
    make_fact,
    ok,
    refused,
    the_token,
    unique_word,
)

ADA = "Ada Lovelace"
SECTIONS = (
    "active_decisions",
    "active_principles",
    "facts",
    "beliefs",
    "stale_beliefs",
    "superseded_decisions",
    "past_memories",
)


def same_words(board_words: str, mcp_words: str) -> bool:
    """The board said what the tool says, without the protocol's "Error executing tool"."""
    return bool(board_words.strip()) and mcp_words.endswith(board_words.strip())


def top(page: Page) -> Locator:
    return page.locator("dialog.modal").last


def refusal_in(dialog: Locator) -> Locator:
    return dialog.locator("form > .problem")


def entry(page: Page, kind: str, ident: int) -> Locator:
    return page.locator(f"li.entry[data-entry='{kind}:{ident}']")


async def link(dialog: Locator, ref: str) -> None:
    box = dialog.locator(".picker input[type=text]").first
    await box.fill(ref)
    await box.press("Enter")


async def entry_id(page: Page, kind: str, words: str) -> int:
    row = page.locator(f"li.entry.{kind}", has_text=words).first
    await expect(row).to_be_visible(timeout=SLOW)
    return int(
        await row.get_attribute("data-decision" if kind == "decision" else "data-memory") or 0
    )


async def rules_and_memory() -> None:
    chapter("Rules and memory on the board: what a person records, replaces and asks for")
    page_url = board_url(URL)
    try:
        async with connect(URL, the_token()) as client:
            await the_rules(client, page_url)
            await decisions(client, page_url)
            await beliefs(client, page_url)
            await checking(client, page_url)
            await recalling(client, page_url)
    except BrowserError as error:
        raise Failed(
            f"M12: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- 1 --------------------------------------------------------------------------------------------


async def the_rules(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Rules {word}")
    first = await made(client, "story", f"First {word}", epic)
    second = await made(client, "story", f"Second {word}", epic)
    await ok(client, "create_rule", actor="agent-1", title=f"Anchor {word}", story_id=second)
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/rules")
        before = (await ok(client, "coverage_report", show="unplaced_rules", limit=1))[
            "unplaced_rule_count"
        ]
        await expect(page.locator("#unplaced-rules h3")).to_have_text(
            f"Rules in no story ({before})", timeout=SLOW
        )
        await page.locator("#new-rule").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#rule-title").fill(f"Created {word}")
        await dialog.get_by_role("button", name="Create rule").click()
        newest = page.locator("#unplaced-group li.rule").first
        await expect(newest).to_contain_text(f"Created {word}", timeout=SLOW)
        rule = int(await newest.get_attribute("data-rule") or 0)
        order = await page.evaluate(
            "[...document.querySelectorAll('#unplaced-group, #rule-groups, #coverage-report')]"
            ".map(e => e.id)"
        )
        await expect(page.locator("#unplaced-rules h3")).to_have_text(
            f"Rules in no story ({before + 1})", timeout=SLOW
        )
        listed_before = await page.locator(f"#unplaced-rules li[data-rule='{rule}']").count()
        await newest.get_by_role("button", name="Put in a story…").click()
        dialog = page.locator("dialog.modal")
        await dialog.locator("#rule-target").select_option(str(first))
        await dialog.get_by_role("button", name="Place in story").click()
        await expect(
            page.locator(f"#rule-groups section[data-group='{first}'] li.rule[data-rule='{rule}']")
        ).to_be_visible(timeout=SLOW)
        await expect(page.locator("#unplaced-rules h3")).to_have_text(
            f"Rules in no story ({before})", timeout=SLOW
        )
        left = await page.locator(f"#unplaced-rules li[data-rule='{rule}']").count()
        said = await refused(
            client, "place_rule", actor="x", rule_id=rule, expected_version=2, story_id=second
        )
        await (
            page.locator(f"li.rule[data-rule='{rule}']")
            .get_by_role("button", name="Put in a story…")
            .click()
        )
        dialog = page.locator("dialog.modal")
        await dialog.locator("#rule-target").select_option(str(second))
        await dialog.get_by_role("button", name="Place in story").click()
        await expect(refusal_in(dialog)).to_be_visible(timeout=SLOW)
        shown = await refusal_in(dialog).inner_text()
        await dialog.get_by_role("button", name="Cancel").click()
        still = (await ok(client, "get_rule", rule_id=rule))["story_id"]
        await page.drag_and_drop(
            f"li.rule[data-rule='{rule}']",
            f"#rule-groups section[data-group='{second}'] .group-head",
        )
        await expect(
            page.locator(f"#rule-groups section[data-group='{second}'] li.rule[data-rule='{rule}']")
        ).to_be_visible(timeout=SLOW)
    events = (await ok(client, "rule_history", rule_id=rule, limit=200))["items"]
    move = events[-1]
    seen_in = [
        move["id"]
        in [e["id"] for e in (await ok(client, "node_history", node_id=s, limit=200))["items"]]
        for s in (first, second)
    ]
    proof(
        "M12",
        "on the Rules page a new rule is first and in the coverage report; placing it takes it out "
        "of both; placing it in a second story is refused with the MCP message",
        order == ["unplaced-group", "rule-groups", "coverage-report"]
        and listed_before == 1
        and left == 0
        and same_words(shown, said)
        and still == first,
        clip(shown, 90),
    )
    proof(
        "M12",
        "dragging a rule onto another story moves it in one step, which is in the history of the "
        "rule and of both stories, with the typed name",
        [(e["action"], e["actor"]) for e in events]
        == [("rule_created", ADA), ("rule_placed", ADA), ("rule_moved", ADA)]
        and move["before"]["story_id"] == first
        and move["after"]["story_id"] == second
        and seen_in == [True, True],
        f"story {first} to story {second}",
    )


# -- 2 --------------------------------------------------------------------------------------------


async def decisions(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Memory {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Task {word}", story)
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")
        await page.locator("#record-decision").click()
        dialog = top(page)
        await dialog.locator("[name=title]").fill(f"Use PDF {word}")
        await dialog.locator("[name=text]").fill("Invoices are PDF files.")
        said = await refused(
            client,
            "record_decision",
            actor="x",
            title=f"Use PDF {word}",
            text="Invoices are PDF files.",
            reason="",
            decided_by=ADA,
        )
        await dialog.get_by_role("button", name="Record the decision").click()
        await expect(refusal_in(dialog)).to_be_visible(timeout=SLOW)
        blank = await refusal_in(dialog).inner_text()
        await dialog.locator("[name=reason]").fill("Everyone can open it.")
        await link(dialog, f"#{story}")
        await link(dialog, f"#{task}")
        await dialog.get_by_role("button", name="Record the decision").click()
        decision = await entry_id(page, "decision", f"Use PDF {word}")
        row = entry(page, "decision", decision)
        await row.get_by_role("button", name="Replace with a new decision…").click()
        dialog = top(page)
        await dialog.locator("[name=title]").fill(f"Use HTML {word}")
        await dialog.locator("[name=reason]").fill("PDF was hard to search.")
        await dialog.get_by_role("button", name="Replace the decision").click()
        newer = await entry_id(page, "decision", f"Use HTML {word}")
        await expect(row).to_have_attribute("data-status", "superseded", timeout=SLOW)
        chain = await row.locator(".chain").inner_text()
        badge = await row.locator(".entry-status").inner_text()
    old = await ok(client, "get_decision", decision_id=decision)
    new = await ok(client, "get_decision", decision_id=newer)
    proof(
        "M12",
        "on the Memory page a decision is recorded with two links and then replaced: the chain "
        "shows "
        "the old one superseded, and a blank reason is refused with the MCP message",
        same_words(blank, said)
        and sorted(old["node_ids"]) == [story, task]
        and old["decided_by"] == ADA
        and (old["status"], old["superseded_by_id"]) == ("superseded", newer)
        and new["supersedes_ids"] == [decision]
        and badge == "Superseded"
        and f"#{newer}" in chain
        and "in force now" in chain,
        clip(chain, 90),
    )


# -- 3 --------------------------------------------------------------------------------------------


async def beliefs(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Beliefs {word}")
    story = await made(client, "story", f"Story {word}", epic)
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")

        async def fact(text: str, source: str) -> int:
            await page.locator("#record-fact").click()
            dialog = top(page)
            await dialog.locator("[name=text]").fill(text)
            await dialog.locator("[name=source]").fill(source)
            await link(dialog, f"#{story}")
            await dialog.get_by_role("button", name="Record the fact").click()
            return await entry_id(page, "fact", text)

        one = await fact(f"First fact {word}", "The tax guide")
        two = await fact(f"Second fact {word}", "The contract")
        await page.locator("#record-belief").click()
        dialog = top(page)
        await dialog.locator("[name=text]").fill(f"Both together {word}")
        said = await refused(client, "record_belief", actor="x", text=f"Both together {word}")
        await dialog.get_by_role("button", name="Record the belief").click()
        await expect(refusal_in(dialog)).to_be_visible(timeout=SLOW)
        no_evidence = await refusal_in(dialog).inner_text()
        for ident in (one, two):
            await dialog.locator(f"[data-evidence='fact:{ident}'] input").check()
        await link(dialog, f"#{story}")
        await dialog.get_by_role("button", name="Record the belief").click()
        belief = await entry_id(page, "belief", f"Both together {word}")
        row = entry(page, "belief", belief)
        badge = await row.locator(".chip.suggestion").inner_text()
        shown = sorted(
            await row.locator("[data-evidence]").evaluate_all(
                "els => els.map(e => e.dataset.evidence)"
            )
        )

        await entry(page, "fact", one).get_by_role("button", name="Retract…").click()
        dialog = top(page)
        await dialog.locator("[name=reason]").fill("The guide was out of date.")
        await dialog.get_by_role("button", name="Retract", exact=True).click()
        stale_row = page.locator(f"#stale-beliefs li.entry[data-entry='belief:{belief}']")
        await expect(stale_row).to_be_visible(timeout=SLOW)
        why = (await ok(client, "get_memory", memory_id=belief))["stale_because"]
        stale_text = await stale_row.inner_text()
        offer = page.locator("#change-result")
        await expect(offer).to_contain_text("is now stale", timeout=SLOW)
        offered = [
            await offer.get_by_role("button", name="Retract these beliefs…").count(),
            await offer.get_by_role("button", name="Replace them with one new belief…").count(),
        ]

        other = (
            await make_belief(client, [{"id": two}], f"Another belief {word}", node_ids=[story])
        )["id"]
        await expect(entry(page, "belief", other)).to_be_visible(timeout=SLOW)
        await stale_row.locator("input.choose").check()
        await entry(page, "belief", other).locator("input.choose").check()
        await page.get_by_role("button", name="Replace with one new item…").click()
        dialog = top(page)
        await dialog.locator("[name=text]").fill(f"The new view {word}")
        await dialog.locator("[name=reason]").fill("The first fact was wrong.")
        await dialog.get_by_role("button", name="Replace with this belief").click()
        newest = await entry_id(page, "belief", f"The new view {word}")
        for old in (belief, other):
            await expect(entry(page, "belief", old)).to_have_attribute(
                "data-status", "superseded", timeout=SLOW
            )
        chains = [
            await entry(page, "belief", old).locator(".chain").inner_text()
            for old in (belief, other)
        ]

        row = entry(page, "belief", newest)
        await row.get_by_role("button", name="Confirm…").click()
        await top(page).get_by_role("button", name="Confirm", exact=True).click()
        await expect(row.locator(".chip.confirmed")).to_be_visible(timeout=SLOW)
        await row.get_by_role("button", name="Make it a decision…").click()
        dialog = top(page)
        await dialog.locator("[name=reason]").fill("It is what the contract says.")
        await dialog.get_by_role("button", name="Make it a decision").click()
        await expect(
            page.locator("li.entry.decision", has_text=f"The new view {word}")
        ).to_be_visible(timeout=SLOW)
    proof(
        "M12",
        "two facts and a belief resting on them are recorded on the page; the belief shows its "
        "evidence and a suggestion badge, and a belief with no evidence is refused with the MCP "
        "message",
        shown == sorted([f"fact:{one}", f"fact:{two}"])
        and "Suggestion" in badge
        and same_words(no_evidence, said),
        clip(no_evidence, 90),
    )
    proof(
        "M12",
        "retracting one fact moves the belief apart as stale, says which evidence changed, and "
        "offers to retract or replace it",
        bool(why) and all(sentence in stale_text for sentence in why) and offered == [1, 1],
        clip(" ".join(why), 90),
    )
    replaced = [await ok(client, "get_memory", memory_id=m) for m in (belief, other)]
    confirmed = await ok(client, "get_memory", memory_id=newest)
    (made_decision,) = confirmed["promoted_decision_ids"]
    decision = await ok(client, "get_decision", decision_id=made_decision)
    proof(
        "M12",
        "two beliefs are replaced by one new belief, which a person confirms and promotes: "
        "get_memory shows who confirmed it and when, and the decision is by the typed name and "
        "links back to the belief",
        [m["superseded_by_id"] for m in replaced] == [newest, newest]
        and all(f"#{newest}" in chain for chain in chains)
        and confirmed["confirmed_by"] == ADA
        and bool(confirmed["confirmed_at"])
        and decision["decided_by"] == ADA
        and decision["promoted_from_id"] == newest,
        f"belief {newest} became decision {made_decision}",
    )


async def checking(client: Client, page_url: str) -> None:
    marker = unique_word()
    model = ScriptedModel()
    fact = await make_fact(client, f"{marker} the evidence")
    old = await make_belief(client, [fact], f"{marker} exports run every night")
    statement = f"{marker} exports never run"
    model.script(
        marker,
        {
            "contradictions": [
                {
                    "belief_id": old["id"],
                    "reason": "One says every night, the other never.",
                    "belief_quote": marker,
                    "statement_quote": marker,
                }
            ]
        },
    )
    active = (await ok(client, "list_memories", type="belief", status="active", limit=1))["total"]
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")
        await page.locator("#record-belief").click()
        dialog = top(page)
        await dialog.locator("[name=text]").fill(statement)
        await dialog.locator(f"[data-evidence='fact:{fact['id']}'] input").check()
        await dialog.get_by_role("button", name="Check it against every belief").click()
        status = dialog.locator(".check-progress")
        await expect(status).to_contain_text(
            f"Looked at {active} of {active} belief", timeout=60_000
        )
        progress = await status.inner_text()
        proposal = dialog.locator(f"li.proposal[data-belief='{old['id']}']")
        await expect(proposal).to_be_visible(timeout=SLOW)
        quotes = await proposal.locator(".quote").all_inner_texts()
        untouched = (await ok(client, "get_memory", memory_id=old["id"]))["status"] == "active"
        await proposal.get_by_role("button", name="Replace it…").click()
        nested = top(page)
        await nested.locator("[name=reason]").fill("It contradicts the new belief.")
        await nested.get_by_role("button", name="Replace with this belief").click()
        await expect(page.locator("dialog.modal")).to_have_count(0, timeout=SLOW)
        await expect(entry(page, "belief", old["id"])).to_have_attribute(
            "data-status", "superseded", timeout=SLOW
        )
    replaced = await ok(client, "get_memory", memory_id=old["id"])
    new = await ok(client, "get_memory", memory_id=replaced["superseded_by_id"])
    proof(
        "M12",
        "with the scripted model, a belief checked against every belief shows the progress through "
        "all of them and the proposal with its quotes; replacing from there records the new belief "
        "and marks the old one superseded",
        f"Looked at {active} of" in progress
        and len(quotes) == 2
        and all(marker in quote for quote in quotes)
        and untouched
        and new["text"] == statement
        and replaced["status"] == "superseded"
        and [e["id"] for e in new["evidence"]] == [fact["id"]],
        clip(progress, 90),
    )


# -- 4 --------------------------------------------------------------------------------------------


async def recalling(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Recall {word}")
    story = await made(client, "story", f"Story {word}", epic)
    await make_decision(client, f"Kept {word}", node_ids=[story])
    gone = await make_decision(client, f"Replaced {word}", node_ids=[story])
    await ok(
        client,
        "supersede_decision",
        actor="agent-1",
        decision_id=gone["id"],
        title=f"Newer {word}",
        text="Newer text",
        reason="Changed",
        decided_by="Marie",
    )
    fact = await make_fact(client, f"Fact {word}", node_ids=[story])
    doomed = await make_fact(client, f"Doomed {word}", node_ids=[story])
    await make_belief(client, [fact], f"Sound belief {word}", node_ids=[story])
    await make_belief(client, [doomed], f"Doomed belief {word}", node_ids=[story])
    await ok(client, "retract_memory", actor="agent-1", memory_ids=[doomed["id"]], reason="Wrong")
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/memory?recall=node:{story}")
        await expect(page.locator("#recall-result .recall-heading")).to_be_visible(timeout=SLOW)
        shown: dict[str, tuple[int, list[tuple[int, str]]]] = {}
        for name in SECTIONS:
            section = page.locator(f".recall-section[data-section='{name}']")
            rows = await section.locator("li.entry").evaluate_all(
                "els => els.map(e => [Number(e.dataset.decision || e.dataset.memory),"
                " e.querySelector('.why-text').textContent])"
            )
            shown[name] = (
                int(await section.get_attribute("data-count") or -1),
                [(int(a), str(b)) for a, b in rows],
            )
    recalled: dict[str, Any] = await ok(client, "recall", node_id=story)
    expected = {
        name: (
            recalled[name]["count"],
            [
                ((item.get("decision") or item.get("memory"))["id"], item["why"])
                for item in recalled[name]["items"]
            ],
        )
        for name in SECTIONS
    }
    proof(
        "M12",
        "the recall preview for a story equals the output of recall through MCP: the same ids, "
        "the same sections, the same reasons",
        shown == expected
        and recalled["stale_beliefs"]["count"] == 1
        and recalled["superseded_decisions"]["count"] == 1,
        ", ".join(f"{name} {count}" for name, (count, _) in shown.items() if count),
    )
