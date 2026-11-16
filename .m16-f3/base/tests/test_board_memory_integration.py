"""M12's Memory page against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh). What a person does is read back through get_decision,
get_memory and the histories, every refusal is compared word for word with what the same attempt
gets through MCP, and the recall preview is compared section by section with `recall`. Titles carry
a unique word, and each test looks only at what it made, since all tests share one database."""

import asyncio
import os
import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Locator, Page, expect

from tests.board_helpers import SLOW, board_url, browser_page, hold_back_news, made, plain
from tests.helpers import (
    ScriptedModel,
    day,
    make_belief,
    make_decision,
    make_fact,
    ok,
    refused,
    the_token,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

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


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


def entry(page: Page, kind: str, ident: int) -> Locator:
    return page.locator(f"li.entry[data-entry='{kind}:{ident}']")


def problem_of(dialog: Locator) -> Locator:
    """The refusal a dialog shows under its fields (not the small one inside a picker)."""
    return dialog.locator("form > .problem")


def dialog_on_top(page: Page) -> Locator:
    return page.locator("dialog.modal").last


async def add_link(dialog: Locator, ref: str) -> None:
    box = dialog.locator(".picker input[type=text]").first
    await box.fill(ref)
    await box.press("Enter")


async def found(page: Page, kind: str, text: str) -> int:
    """The id of the entry of this kind whose words include `text`, once it is on the page."""
    row = page.locator(f"li.entry.{kind}", has_text=text).first
    await expect(row).to_be_visible(timeout=SLOW)
    return int(
        await row.get_attribute("data-decision" if kind == "decision" else "data-memory") or 0
    )


async def record_fact(page: Page, text: str, source: str, *links: str) -> int:
    await page.locator("#record-fact").click()
    dialog = dialog_on_top(page)
    await dialog.locator("[name=text]").fill(text)
    await dialog.locator("[name=source]").fill(source)
    for link in links:
        await add_link(dialog, link)
    await dialog.get_by_role("button", name="Record the fact").click()
    return await found(page, "fact", text)


# -- 2. decisions: two links, then superseded, with the chain shown ------------------------------


async def test_a_decision_is_recorded_with_two_links_and_replaced_and_the_chain_is_shown(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Memory {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Task {word}", story)
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")
        await page.locator("#record-decision").click()
        dialog = dialog_on_top(page)
        await dialog.locator("[name=title]").fill(f"Use PDF {word}")
        await dialog.locator("[name=text]").fill("Invoices are PDF files.")
        # A blank reason is refused in the words MCP uses, and everything typed stays.
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
        await expect(problem_of(dialog)).to_be_visible(timeout=SLOW)
        plain(await problem_of(dialog).inner_text(), said)
        assert await dialog.locator("[name=title]").input_value() == f"Use PDF {word}"
        await dialog.locator("[name=reason]").fill("Everyone can open it.")
        await add_link(dialog, f"#{story}")
        await add_link(dialog, f"#{task}")
        await expect(dialog.locator(".picker .chip")).to_have_count(2)
        await dialog.get_by_role("button", name="Record the decision").click()
        decision = await found(page, "decision", f"Use PDF {word}")
        recorded = await ok(client, "get_decision", decision_id=decision)
        assert (recorded["kind"], recorded["decided_by"], recorded["status"]) == (
            "decision",
            ADA,
            "active",
        )
        assert sorted(recorded["node_ids"]) == [story, task]
        events = (await ok(client, "decision_history", decision_id=decision))["items"]
        assert events[0]["actor"] == ADA

        # Replacing it: the form starts from the old words, and records a new decision.
        row = entry(page, "decision", decision)
        await row.get_by_role("button", name="Replace with a new decision…").click()
        dialog = dialog_on_top(page)
        assert await dialog.locator("[name=title]").input_value() == f"Use PDF {word}"
        assert await dialog.locator("[name=text]").input_value() == "Invoices are PDF files."
        assert await dialog.locator("[name=reason]").input_value() == ""
        await dialog.locator("[name=title]").fill(f"Use HTML {word}")
        await dialog.locator("[name=text]").fill("Invoices are HTML pages.")
        await dialog.locator("[name=reason]").fill("PDF was hard to search.")
        await dialog.get_by_role("button", name="Replace the decision").click()
        newer = await found(page, "decision", f"Use HTML {word}")
        await expect(row).to_have_attribute("data-status", "superseded", timeout=SLOW)
        await expect(row.locator(".entry-status")).to_have_text("Superseded")
        await expect(row.locator(".chain")).to_contain_text(f"#{newer}")
        await expect(row.locator(".chain")).to_contain_text("in force now")
        await expect(
            entry(page, "decision", newer).locator(".quiet", has_text="Replaced")
        ).to_contain_text(f"#{decision}")
        assert await row.get_by_role("button").count() == 0
        assert w.problems == [], w.problems

    old = await ok(client, "get_decision", decision_id=decision)
    new = await ok(client, "get_decision", decision_id=newer)
    assert (old["status"], old["superseded_by_id"]) == ("superseded", newer)
    assert new["supersedes_ids"] == [decision] and new["decided_by"] == ADA
    assert sorted(new["node_ids"]) == [story, task]
    assert new["reason"] == "PDF was hard to search."


# -- 3. facts, a belief on them, a retracted fact, a replacement, confirming and promoting --------


async def test_facts_and_a_belief_on_them_stale_when_a_fact_is_retracted_replaced_and_promoted(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Beliefs {word}")
    story = await made(client, "story", f"Story {word}", epic)
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")
        first = await record_fact(page, f"First fact {word}", "The tax guide", f"#{story}")
        second = await record_fact(page, f"Second fact {word}", "The contract", f"#{story}")

        # A belief with no evidence is refused in the words of MCP.
        await page.locator("#record-belief").click()
        dialog = dialog_on_top(page)
        await dialog.locator("[name=text]").fill(f"Both together {word}")
        said = await refused(client, "record_belief", actor="x", text=f"Both together {word}")
        await dialog.get_by_role("button", name="Record the belief").click()
        await expect(problem_of(dialog)).to_be_visible(timeout=SLOW)
        plain(await problem_of(dialog).inner_text(), said)
        for ident in (first, second):
            await dialog.locator(f"[data-evidence='fact:{ident}'] input").check()
        await add_link(dialog, f"#{story}")
        await dialog.get_by_role("button", name="Record the belief").click()
        belief = await found(page, "belief", f"Both together {word}")
        row = entry(page, "belief", belief)
        # The belief shows its evidence and says it is only a suggestion.
        await expect(row.locator(".chip.suggestion")).to_contain_text("Suggestion")
        shown = await row.locator("[data-evidence]").evaluate_all(
            "els => els.map(e => e.dataset.evidence)"
        )
        assert sorted(shown) == sorted([f"fact:{first}", f"fact:{second}"])
        recorded = await ok(client, "get_memory", memory_id=belief)
        assert (recorded["origin"], recorded["recorded_by"], recorded["stale"]) == (
            "stated",
            ADA,
            False,
        )
        assert sorted(e["id"] for e in recorded["evidence"]) == sorted([first, second])
        assert await row.locator("button.confirm").count() == 1

        # Retract the first fact: the belief moves apart as stale and says which evidence changed.
        await entry(page, "fact", first).get_by_role("button", name="Retract…").click()
        dialog = dialog_on_top(page)
        await dialog.locator("[name=reason]").fill("The guide was out of date.")
        await dialog.get_by_role("button", name="Retract", exact=True).click()
        stale_row = page.locator(f"#stale-beliefs li.entry[data-entry='belief:{belief}']")
        await expect(stale_row).to_be_visible(timeout=SLOW)
        words = (await ok(client, "get_memory", memory_id=belief))["stale_because"]
        shown_words = await stale_row.inner_text()
        assert words and all(sentence in shown_words for sentence in words)
        await expect(stale_row.locator(".entry-head .chip.stale")).to_contain_text("Stale")
        assert await stale_row.locator("button.confirm, button.promote").count() == 0
        # The page offers to retract the stale belief or to replace it.
        offer = page.locator("#change-result")
        await expect(offer).to_contain_text("is now stale", timeout=SLOW)
        await expect(offer.get_by_role("button", name="Retract these beliefs…")).to_be_visible()
        await expect(
            offer.get_by_role("button", name="Replace them with one new belief…")
        ).to_be_visible()
        await expect(entry(page, "fact", first)).to_have_attribute("data-status", "retracted")

        # Replace it, together with a second belief, by one new belief: both are superseded by it.
        other = (
            await make_belief(client, [{"id": second}], f"Another belief {word}", node_ids=[story])
        )["id"]
        other_row = entry(page, "belief", other)
        await expect(other_row).to_be_visible(timeout=SLOW)
        await stale_row.locator("input.choose").check()
        await other_row.locator("input.choose").check()
        await expect(page.locator("#bulk-count")).to_have_text("2 items chosen")
        await page.get_by_role("button", name="Replace with one new item…").click()
        dialog = dialog_on_top(page)
        await expect(dialog.locator("[data-replacing]")).to_have_count(2)
        await dialog.locator("[name=text]").fill(f"The new view {word}")
        await expect(dialog.locator(f"[data-evidence='fact:{second}'] input")).to_be_checked()
        await dialog.locator("[name=reason]").fill("The first fact was wrong.")
        await dialog.get_by_role("button", name="Replace with this belief").click()
        newest = await found(page, "belief", f"The new view {word}")
        for old in (belief, other):
            await expect(entry(page, "belief", old)).to_have_attribute(
                "data-status", "superseded", timeout=SLOW
            )
            await expect(entry(page, "belief", old).locator(".chain")).to_contain_text(f"#{newest}")
        replaced = [await ok(client, "get_memory", memory_id=m) for m in (belief, other)]
        assert [m["superseded_by_id"] for m in replaced] == [newest, newest]
        assert {m["status_reason"] for m in replaced} == {"The first fact was wrong."}
        await expect(page.locator("#bulk")).to_be_hidden()

        # Confirm the new belief, then promote it: a decision by the typed name that links back.
        row = entry(page, "belief", newest)
        await row.get_by_role("button", name="Confirm…").click()
        dialog = dialog_on_top(page)
        assert await dialog.locator("[name=confirmed_by]").input_value() == ADA
        await dialog.get_by_role("button", name="Confirm", exact=True).click()
        await expect(row.locator(".chip.confirmed")).to_contain_text(
            f"Confirmed by {ADA}", timeout=SLOW
        )
        await row.get_by_role("button", name="Make it a decision…").click()
        dialog = dialog_on_top(page)
        await dialog.locator("[name=reason]").fill("It is what the contract says.")
        assert await dialog.locator("[name=decided_by]").input_value() == ADA
        await dialog.get_by_role("button", name="Make it a decision").click()
        promoted = page.locator("li.entry.decision", has_text=f"The new view {word}")
        await expect(promoted).to_be_visible(timeout=SLOW)
        await expect(promoted).to_contain_text(f"Promoted from belief #{newest}")
        assert w.problems == [], w.problems

    confirmed = await ok(client, "get_memory", memory_id=newest)
    assert confirmed["confirmed_by"] == ADA and confirmed["confirmed_at"]
    (decision_id,) = confirmed["promoted_decision_ids"]
    decision = await ok(client, "get_decision", decision_id=decision_id)
    assert (decision["decided_by"], decision["promoted_from_id"]) == (ADA, newest)
    assert decision["reason"] == "It is what the contract says."
    assert confirmed["status"] == "active" and confirmed["type"] == "belief"


async def test_replacing_a_belief_a_person_confirmed_needs_who_changed_their_mind_and_what_was_said(
    client: Client, board: str
) -> None:
    word = unique_word()
    fact = await make_fact(client, f"A fact {word}")
    belief = await make_belief(client, [fact], f"Confirmed belief {word}")
    await ok(
        client, "confirm_belief", actor="agent-1", memory_id=belief["id"], confirmed_by="Marie"
    )
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")
        row = entry(page, "belief", belief["id"])
        await expect(row.locator(".chip.confirmed")).to_contain_text(
            "Confirmed by Marie", timeout=SLOW
        )
        await row.get_by_role("button", name="Replace with a new belief…").click()
        dialog = dialog_on_top(page)
        assert await dialog.locator("[name=text]").input_value() == f"Confirmed belief {word}"
        await expect(dialog.locator("details.said-by")).to_have_attribute("open", "")
        await dialog.locator("[name=text]").fill(f"Changed belief {word}")
        await dialog.locator("[name=reason]").fill("Marie changed her mind.")
        said = await refused(
            client,
            "supersede_memory",
            actor="x",
            replaces=[belief["id"]],
            reason="Marie changed her mind.",
            text=f"Changed belief {word}",
            evidence_fact_ids=[fact["id"]],
        )
        await dialog.get_by_role("button", name="Replace with this belief").click()
        await expect(problem_of(dialog)).to_be_visible(timeout=SLOW)
        plain(await problem_of(dialog).inner_text(), said)
        assert (await ok(client, "get_memory", memory_id=belief["id"]))["status"] == "active"
        await dialog.locator("[name=said_by]").fill("Marie")
        await dialog.locator("[name=quote]").fill("I no longer think so (the team call)")
        await dialog.get_by_role("button", name="Replace with this belief").click()
        await expect(row).to_have_attribute("data-status", "superseded", timeout=SLOW)
        assert w.problems == [], w.problems
    events = (await ok(client, "memory_history", memory_id=belief["id"], limit=200))["items"]
    assert any(e["actor"] == ADA for e in events)
    assert "Marie" in str(events) and "no longer think so" in str(events)


# -- 3 (continued). with the scripted model, a belief is checked against every belief ----------


async def test_with_the_scripted_model_a_belief_is_checked_against_every_belief_and_replaces_one(
    client: Client, board: str, model: ScriptedModel
) -> None:
    marker = unique_word()
    fact = await make_fact(client, f"{marker} the evidence")
    old = await make_belief(client, [fact], f"{marker} exports run every night")
    odd = await make_belief(client, [fact], f"{marker} exports use two counters")
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
                },
                {
                    "belief_id": odd["id"],
                    "reason": "A single counter is not two.",
                    "belief_quote": marker,
                    "statement_quote": marker,
                },
            ]
        },
    )
    active = (await ok(client, "list_memories", type="belief", status="active", limit=1))["total"]
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")
        await page.locator("#record-belief").click()
        dialog = dialog_on_top(page)
        await dialog.locator("[name=text]").fill(statement)
        await dialog.locator(f"[data-evidence='fact:{fact['id']}'] input").check()
        await dialog.get_by_role("button", name="Check it against every belief").click()
        status = dialog.locator(".check-progress")
        await expect(status).to_contain_text(
            f"Looked at {active} of {active} belief", timeout=60_000
        )
        await expect(status).to_contain_text("2 proposals")
        # It went through every belief, a batch at a time, so it asked the model more than once.
        assert len(model.received(marker)) == -(-active // 25)
        proposal = dialog.locator(f"li.proposal[data-belief='{old['id']}']")
        await expect(proposal).to_contain_text("One says every night, the other never.")
        await expect(proposal).to_contain_text(f"“{marker}”")
        assert await proposal.locator(".quote").count() == 2
        # Nothing was changed by the check.
        assert (await ok(client, "get_memory", memory_id=old["id"]))["status"] == "active"

        # Retract one of them from the proposal, and replace the other with the new belief.
        other = dialog.locator(f"li.proposal[data-belief='{odd['id']}']")
        await other.get_by_role("button", name="Retract it…").click()
        nested = dialog_on_top(page)
        await nested.locator("[name=reason]").fill("It is a misreading.")
        await nested.get_by_role("button", name="Retract", exact=True).click()
        await expect(other.locator(".done-note")).to_have_text("Retracted.", timeout=SLOW)
        await proposal.get_by_role("button", name="Replace it…").click()
        nested = dialog_on_top(page)
        assert await nested.locator("[name=text]").input_value() == statement
        await expect(nested.locator(f"[data-evidence='fact:{fact['id']}'] input")).to_be_checked()
        await nested.locator("[name=reason]").fill("It contradicts the new belief.")
        await nested.get_by_role("button", name="Replace with this belief").click()
        await expect(page.locator("dialog.modal")).to_have_count(0, timeout=SLOW)
        await expect(entry(page, "belief", old["id"])).to_have_attribute(
            "data-status", "superseded", timeout=SLOW
        )
        await expect(entry(page, "belief", odd["id"])).to_have_attribute("data-status", "retracted")
        assert w.problems == [], w.problems

    replaced = await ok(client, "get_memory", memory_id=old["id"])
    new = await ok(client, "get_memory", memory_id=replaced["superseded_by_id"])
    assert (new["text"], new["status"], new["recorded_by"]) == (statement, "active", ADA)
    assert [e["id"] for e in new["evidence"]] == [fact["id"]]
    assert replaced["status_reason"] == "It contradicts the new belief."
    assert (await ok(client, "get_memory", memory_id=odd["id"]))["status"] == "retracted"


async def test_a_fact_checked_against_beliefs_offers_to_replace_a_belief_from_its_own_words(
    client: Client, board: str, model: ScriptedModel
) -> None:
    marker = unique_word()
    old_fact = await make_fact(client, f"{marker} the old evidence")
    old = await make_belief(client, [old_fact], f"{marker} exports run every night")
    model.script(
        marker,
        {
            "contradictions": [
                {
                    "belief_id": old["id"],
                    "reason": "The new fact says they do not.",
                    "belief_quote": marker,
                    "statement_quote": marker,
                }
            ]
        },
    )
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")
        await page.locator("#record-fact").click()
        dialog = dialog_on_top(page)
        await dialog.locator("[name=text]").fill(f"{marker} exports were switched off")
        await dialog.locator("[name=source]").fill("The ops channel")
        await dialog.get_by_role("button", name="Check it against every belief").click()
        proposal = dialog.locator(f"li.proposal[data-belief='{old['id']}']")
        await expect(proposal).to_be_visible(timeout=60_000)
        await proposal.get_by_role("button", name="Replace it…").click()
        nested = dialog_on_top(page)
        # A fact cannot replace a belief, so the form starts from the old belief, not the fact.
        assert await nested.locator("[name=text]").input_value() == old["text"]
        await expect(
            nested.locator(f"[data-evidence='fact:{old_fact['id']}'] input")
        ).to_be_checked()
        await expect(nested.locator(".check-beliefs")).to_have_count(0)
        await nested.get_by_role("button", name="Cancel").click()
        # The fact itself is still unrecorded, and nothing was changed by the check.
        await expect(dialog.locator("[name=text]")).to_have_value(
            f"{marker} exports were switched off"
        )
        assert (await ok(client, "get_memory", memory_id=old["id"]))["status"] == "active"
        assert w.problems == [], w.problems


async def test_a_server_with_no_model_does_not_offer_the_check(board: str) -> None:
    url = os.environ.get("VRUM_NO_MODEL_TEST_URL")
    assert url
    async with browser_page(board_url(url), token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")
        await page.locator("#record-belief").click()
        await expect(dialog_on_top(page).locator("[name=text]")).to_be_visible(timeout=SLOW)
        assert await page.locator(".check-beliefs").count() == 0
        assert w.problems == [], w.problems


# -- 4. the recall preview is what recall returns -----------------------------------------------


async def read_sections(page: Page) -> dict[str, tuple[int, list[tuple[int, str]]]]:
    found_: dict[str, tuple[int, list[tuple[int, str]]]] = {}
    for name in SECTIONS:
        section = page.locator(f".recall-section[data-section='{name}']")
        count = int(await section.get_attribute("data-count") or -1)
        rows = await section.locator("li.entry").evaluate_all(
            "els => els.map(e => [Number(e.dataset.decision || e.dataset.memory),"
            " e.querySelector('.why-text').textContent])"
        )
        found_[name] = (count, [(int(a), str(b)) for a, b in rows])
    return found_


def expected_sections(recalled: dict[str, Any]) -> dict[str, tuple[int, list[tuple[int, str]]]]:
    return {
        name: (
            recalled[name]["count"],
            [
                ((item.get("decision") or item.get("memory"))["id"], item["why"])
                for item in recalled[name]["items"]
            ],
        )
        for name in SECTIONS
    }


async def test_the_recall_preview_for_a_story_and_for_a_rule_is_what_recall_returns(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Recall {word}")
    story = await made(client, "story", f"Story {word}", epic)
    rule = int(
        (await ok(client, "create_rule", actor="agent-1", title=f"Rule {word}", story_id=story))[
            "id"
        ]
    )
    kept = await make_decision(client, f"Kept {word}", node_ids=[story], rule_ids=[rule])
    replaced = await make_decision(client, f"Replaced {word}", node_ids=[story])
    await ok(
        client,
        "supersede_decision",
        actor="agent-1",
        decision_id=replaced["id"],
        title=f"Newer {word}",
        text="Newer text",
        reason="Changed",
        decided_by="Marie",
    )
    await ok(
        client,
        "record_decision",
        actor="agent-1",
        kind="principle",
        title=f"Principle {word}",
        text="Applies everywhere",
        reason="Always",
        decided_by="Marie",
    )
    fact = await make_fact(client, f"Fact {word}", node_ids=[story], rule_ids=[rule])
    doomed = await make_fact(client, f"Doomed {word}", node_ids=[story])
    await make_belief(client, [fact], f"Sound belief {word}", node_ids=[story])
    await make_belief(client, [doomed], f"Doomed belief {word}", node_ids=[story])
    await ok(client, "retract_memory", actor="agent-1", memory_ids=[doomed["id"]], reason="Wrong")
    gone = await make_fact(client, f"Gone {word}", node_ids=[story])
    await ok(client, "retract_memory", actor="agent-1", memory_ids=[gone["id"]], reason="Wrong")
    assert kept["id"]
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")
        await expect(page.locator("#recall-preview")).not_to_have_attribute("open", "")
        await page.locator("#recall-preview summary").click()
        await page.locator("#recall-which").fill(f"#{story}")
        await page.locator("#recall-show").click()
        await expect(page.locator("#recall-result .recall-heading")).to_contain_text(
            f"item #{story}", timeout=SLOW
        )
        assert f"recall=node:{story}" in page.url
        recalled = await ok(client, "recall", node_id=story)
        shown = await read_sections(page)
        assert shown == expected_sections(recalled)
        assert recalled["stale_beliefs"]["count"] == 1 and recalled["past_memories"]["count"] >= 2
        assert recalled["superseded_decisions"]["count"] == 1
        assert [d[0] for d in shown["active_decisions"][1]] == [
            item["decision"]["id"] for item in recalled["active_decisions"]["items"]
        ]
        assert w.problems == [], w.problems

        # The address reproduces it for a rule, in a page that has never seen it.
        await w.open(f"#/memory?recall=rule:{rule}")
        await expect(page.locator("#recall-result .recall-heading")).to_contain_text(
            f"rule #{rule}", timeout=SLOW
        )
        for_rule = await ok(client, "recall", rule_id=rule)
        assert await read_sections(page) == expected_sections(for_rule)
        assert for_rule["active_decisions"]["count"] == 1
        assert for_rule["facts"]["count"] == 1

        # An item that does not exist is refused in the words of MCP.
        said = await refused(client, "recall", node_id=987654321)
        await w.go("#/memory?recall=node:987654321")
        problem = page.locator("#recall-problem")
        await expect(problem).to_be_visible(timeout=SLOW)
        plain(await problem.inner_text(), said)


# -- what the page shows is what the lists hold, and filters ---------------------------------------


async def ids_in(page: Page, section: str) -> list[int]:
    ids: list[int] = await page.locator(f"#{section} li.entry").evaluate_all(
        "els => els.map(e => Number(e.dataset.decision || e.dataset.memory))"
    )
    return ids


async def lists_are(page: Page, expected: dict[str, list[int]]) -> None:
    """Wait until the four lists hold exactly these ids (the page takes a moment to follow a
    change of address), then say what they hold if they never do."""
    want = {
        name: sorted(expected.get(name, []))
        for name in ("principles", "decisions", "facts", "beliefs")
    }
    held: dict[str, list[int]] = {}
    for _ in range(100):
        held = {name: sorted(await ids_in(page, name)) for name in want}
        if held == want:
            return
        await asyncio.sleep(0.1)
    assert held == want


async def test_the_lists_filter_by_kind_status_origin_who_date_and_what_they_concern(
    client: Client, board: str, model: ScriptedModel
) -> None:
    word = unique_word()
    who = f"ann-{word}"
    epic = await made(client, "epic", f"Filters {word}")
    story = await made(client, "story", f"Story {word}", epic)
    elsewhere = await made(client, "story", f"Elsewhere {word}", epic)
    decision = await make_decision(client, f"Decision {word}", node_ids=[story], decided_by=who)
    principle = await ok(
        client,
        "record_decision",
        actor=who,
        kind="principle",
        title=f"Principle {word}",
        text="Everywhere",
        reason="Always",
        decided_by=who,
    )
    one = await make_fact(client, f"One {word}", node_ids=[story], actor=who)
    two = await make_fact(client, f"Two {word}", node_ids=[story], actor=who)
    away = await make_fact(client, f"Away {word}", node_ids=[elsewhere], actor="agent-1")
    stated = await make_belief(client, [one, two], f"Stated {word}", node_ids=[story], actor=who)
    model.script(
        word,
        {"beliefs": [{"text": f"Derived {word}", "evidence_fact_ids": [one["id"], two["id"]]}]},
    )
    drawn = await ok(client, "consolidate_memory", actor=who, fact_ids=[one["id"], two["id"]])
    (derived,) = drawn["recorded"]
    await ok(
        client, "retract_memory", actor=who, memory_ids=[two["id"]], reason="Wrong"
    )  # both beliefs are stale now
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/memory?concerns=%23{story}")
        await expect(page.locator("#count")).to_contain_text("entries", timeout=SLOW)

        # What concerns the story is what list_memories gives for that item. (A belief a model
        # drew up concerns nothing until someone links it, so it is not here.)
        here = {
            "decisions": [decision["id"]],
            "facts": [one["id"], two["id"]],
            "beliefs": [stated["id"]],
        }
        await lists_are(page, here)
        listed = (await ok(client, "list_memories", node_id=story))["items"]
        assert sorted(m["id"] for m in listed) == sorted(here["facts"] + here["beliefs"])
        assert away["id"] not in await ids_in(page, "facts")
        await w.go(f"#/memory?concerns=%23{story}&kind=belief")
        await lists_are(page, {"beliefs": here["beliefs"]})
        await w.go(f"#/memory?concerns=%23{story}&kind=fact&status=retracted")
        await lists_are(page, {"facts": [two["id"]]})
        stale = (await ok(client, "list_memories", node_id=story, stale=True))["items"]
        await w.go(f"#/memory?concerns=%23{story}&status=stale")
        await lists_are(page, {"beliefs": [m["id"] for m in stale]})
        assert [m["id"] for m in stale] == [stated["id"]]

        # Who recorded or decided it (without regard to case), and what a model drew up.
        everyone = {
            "principles": [principle["id"]],
            "decisions": [decision["id"]],
            "facts": [one["id"], two["id"]],
            "beliefs": [stated["id"], derived["id"]],
        }
        await w.go(f"#/memory?by={who.upper()}")
        await lists_are(page, everyone)
        await expect(page.locator("#f-by")).to_have_value(who.upper())
        by_model = [
            m["id"]
            for m in (await ok(client, "list_memories", origin="derived", limit=200))["items"]
            if m["recorded_by"] == who
        ]
        assert by_model == [derived["id"]]
        await w.go(f"#/memory?by={who}&origin=derived")
        await lists_are(page, {"beliefs": by_model})
        await expect(entry(page, "belief", derived["id"]).locator(".chip.model")).to_contain_text(
            "Drawn up by a language model"
        )
        await w.go(f"#/memory?by={who}&origin=stated")
        await lists_are(page, {**everyone, "beliefs": [stated["id"]]})
        await expect(entry(page, "belief", stated["id"]).locator(".chip.model")).to_have_count(0)

        # The day: today has them all, and neither tomorrow nor yesterday has any.
        await w.go(f"#/memory?by={who}&from={day(0)}&to={day(0)}")
        await lists_are(page, everyone)
        await w.go(f"#/memory?by={who}&from={day(1)}")
        await lists_are(page, {})
        await expect(page.locator("#count")).to_have_text("Nothing matches these filters.")
        await w.go(f"#/memory?by={who}&to={day(-1)}")
        await lists_are(page, {})
        await w.go(f"#/memory?by={who}&kind=principle")
        await lists_are(page, {"principles": [principle["id"]]})

        # The form puts the filters in the address, and the address is all there is to it.
        await w.go("#/memory")
        await page.locator("#f-kind").select_option("fact")
        await page.locator("#f-by").fill(who)
        await page.locator("#apply-filter").click()
        await lists_are(page, {"facts": everyone["facts"]})
        assert "kind=fact" in page.url and f"by={who}" in page.url
        assert w.problems == [], w.problems

        # Principles are pinned: their list is above the decisions.
        order = await page.evaluate(
            "[...document.querySelectorAll('.memory-section')].map(e => e.id)"
        )
        assert order == ["principles", "decisions", "facts", "beliefs"]


async def test_what_an_agent_records_appears_with_no_reload_and_what_it_concerns_can_be_changed(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Live {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Task {word}", story)
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")
        await expect(page.locator("#count")).to_contain_text("entr", timeout=SLOW)
        await page.evaluate("window.__same = 'still here'")
        decision = await make_decision(client, f"By an agent {word}", node_ids=[story])
        row = entry(page, "decision", decision["id"])
        await expect(row).to_be_visible(timeout=30_000)
        assert await page.evaluate("window.__same") == "still here"
        await expect(row).to_contain_text("Decided by Marie")

        # What it affects: add the task, then take the story out.
        await row.get_by_role("button", name="What it affects…").click()
        dialog = dialog_on_top(page)
        await expect(dialog.locator(f"[data-ref='node:{story}']")).to_be_visible()
        await add_link(dialog, f"#{task}")
        await dialog.get_by_role("button", name="Add", exact=True).last.click()
        await expect(dialog.locator(f"[data-ref='node:{task}']")).to_be_visible(timeout=SLOW)
        await dialog.locator(f"[data-ref='node:{story}'] button").click()
        await expect(dialog.locator(f"[data-ref='node:{story}']")).to_have_count(0, timeout=SLOW)
        assert (await ok(client, "get_decision", decision_id=decision["id"]))["node_ids"] == [task]
        events = (await ok(client, "decision_history", decision_id=decision["id"]))["items"]
        assert [e["actor"] for e in events[-2:]] == [ADA, ADA]
        # Adding something that does not exist is refused in the words of MCP, in the dialog.
        said = await refused(
            client, "link_decision", actor="x", decision_id=decision["id"], node_ids=[987654321]
        )
        await add_link(dialog, "#987654321")
        await dialog.get_by_role("button", name="Add", exact=True).last.click()
        refusal = dialog.locator(".problem", has_text="987654321")
        await expect(refusal).to_be_visible(timeout=SLOW)
        plain(await refusal.inner_text(), said)
        assert w.problems == [], w.problems


async def test_the_memory_page_opens_from_the_keyboard_and_a_decision_can_be_found_by_its_link(
    client: Client, board: str
) -> None:
    word = unique_word()
    decision = await make_decision(client, f"Linked {word}")
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/memory?entry=decision:{decision['id']}")
        row = entry(page, "decision", decision["id"])
        await expect(row).to_be_visible(timeout=SLOW)
        await expect(row).to_have_class(re.compile("highlight"))
        button = page.locator("#record-decision")
        await button.focus()
        await page.keyboard.press("Enter")
        dialog = dialog_on_top(page)
        await expect(dialog).to_be_visible()
        assert await page.evaluate("document.activeElement.name") == "title"
        await page.keyboard.press("Escape")
        await expect(page.locator("dialog.modal")).to_have_count(0)
        await expect(button).to_be_focused()
        assert w.problems == [], w.problems


async def test_retracting_what_an_agent_already_retracted_is_refused_in_the_words_of_mcp(
    client: Client, board: str
) -> None:
    word = unique_word()
    fact = await make_fact(client, f"A fact {word}")
    belief = await make_belief(client, [fact], f"A belief {word}")
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/memory")
        row = entry(page, "belief", belief["id"])
        await expect(row).to_be_visible(timeout=SLOW)
        await hold_back_news(page)
        await ok(
            client,
            "retract_memory",
            actor="agent-1",
            memory_ids=[belief["id"]],
            reason="An agent found it wrong.",
        )
        said = await refused(
            client, "retract_memory", actor="x", memory_ids=[belief["id"]], reason="Again"
        )
        await row.get_by_role("button", name="Retract…").click()
        dialog = dialog_on_top(page)
        await dialog.locator("[name=reason]").fill("Again")
        await dialog.get_by_role("button", name="Retract", exact=True).click()
        await expect(problem_of(dialog)).to_be_visible(timeout=SLOW)
        plain(await problem_of(dialog).inner_text(), said)
        assert await dialog.locator("[name=reason]").input_value() == "Again"
        assert w.problems == [], w.problems
