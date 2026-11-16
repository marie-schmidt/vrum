"""M16's two kinds of context, facts and decisions, against the running containers: recorded,
confirmed, replaced and withdrawn on the Graph's context list and in the shared detail panel, in a
real headless browser, and compared with what a real MCP client reads (`get_fact`, `get_decision`,
`list_*`, `recall`, the histories). Every refusal is compared word for word with what the same
attempt gets through MCP, and a refused change is checked to have written nothing. Titles carry a
unique word, and each test looks only at what it made, since all tests share one database."""

import re

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_context import (
    ADA,
    dialog,
    dot,
    edge,
    graph_open,
    history_actions,
    lifecycle_filter,
    listed,
    panel,
    recall_matches,
    refusal_in,
    show,
    tally,
)
from tests.board_helpers import SLOW, board_url, browser_page, made, plain
from tests.helpers import (
    confirm,
    connect,
    make_decision,
    make_fact,
    ok,
    refused,
    the_token,
    unique_word,
)
from tests.world import make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MARIE = "Marie Curie"


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def chosen_picker(page: Page, *refs: str) -> None:
    """Name what a record concerns in the dialog's picker: type each and press Add."""
    box = dialog(page)
    for ref in refs:
        await box.locator(".picker input[type=text]").fill(ref)
        await box.get_by_role("button", name="Add", exact=True).click()


# -- 1. a fact and a decision, recorded on the board, are the same records everywhere -------------


async def test_a_fact_and_a_decision_recorded_on_the_board_are_the_same_records_everywhere(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Context {word}")
    story = await made(client, "story", f"Concerned story {word}", epic)
    rule = (
        await ok(client, "create_rule", actor="agent-1", title=f"Concerned {word}", story_id=story)
    )["id"]

    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await graph_open(w, f"#/graph?view=context&as=list&epic={epic}")

        # A fact, with where it came from, about a story and a requirement.
        await page.locator("#record-fact").click()
        box = dialog(page)
        await box.locator("#record-fact-text").fill(f"Fact about {word}")
        await box.locator("#record-fact-source").fill(f"The call with Marie {word}")
        await chosen_picker(page, f"#{story}", f"requirement {rule}")
        await box.get_by_role("button", name="Record the fact").click()
        await expect(box).to_have_count(0, timeout=SLOW)
        # A decision, with its reason and the person who decided it, about the story.
        await page.locator("#record-decision").click()
        box = dialog(page)
        await box.locator("#record-decision-title").fill(f"Decision {word}")
        await box.locator("#record-decision-text").fill(f"What was decided {word}")
        await box.locator("#record-decision-reason").fill(f"Because of {word}")
        await box.locator("#record-decision-by").fill(MARIE)
        await chosen_picker(page, f"#{story}")
        await box.get_by_role("button", name="Record the decision").click()
        await expect(box).to_have_count(0, timeout=SLOW)

        facts = (await ok(client, "list_facts", node_id=story))["items"]
        decisions = [
            one
            for one in (await ok(client, "list_decisions", limit=200))["items"]
            if story in one["node_ids"]
        ]
        assert len(facts) == 1 and len(decisions) == 1, (facts, decisions)
        fact = await ok(client, "get_fact", fact_id=facts[0]["id"])
        decision = await ok(client, "get_decision", decision_id=decisions[0]["id"])
        assert (fact["text"], fact["source"], fact["recorded_by"]) == (
            f"Fact about {word}",
            f"The call with Marie {word}",
            ADA,
        )
        assert (fact["status"], fact["confirmation"], fact["confirmed_by"]) == (
            "active",
            "unconfirmed",
            None,
        )
        assert (fact["node_ids"], fact["rule_ids"], fact["decision_ids"]) == ([story], [rule], [])
        assert (decision["title"], decision["text"], decision["reason"]) == (
            f"Decision {word}",
            f"What was decided {word}",
            f"Because of {word}",
        )
        assert (decision["decided_by"], decision["project_wide"], decision["status"]) == (
            MARIE,
            False,
            "active",
        )
        assert (decision["confirmation"], decision["node_ids"]) == ("unconfirmed", [story])
        fact_key, decision_key = f"fact:{fact['id']}", f"decision:{decision['id']}"
        events = (await ok(client, "decision_history", decision_id=decision["id"]))["items"]
        assert [(e["action"], e["actor"]) for e in events] == [
            ("decision_recorded", ADA),
            ("decision_linked", ADA),
        ]

        # The context list: the same ids and fields, an unconfirmed record said to be unconfirmed.
        fact_row, decision_row = listed(page, fact_key), listed(page, decision_key)
        await expect(fact_row).to_be_visible(timeout=SLOW)
        await expect(decision_row).to_be_visible()
        for row, record in ((fact_row, fact), (decision_row, decision)):
            await expect(row).to_have_attribute("data-id", str(record["id"]))
            await expect(row).to_have_attribute("data-status", "active")
            await expect(row).to_have_attribute("data-confirmation", "unconfirmed")
            await expect(row.locator(".chip.confirmation")).to_have_text(re.compile("Unconfirmed"))
            await expect(row.locator(".chip.lifecycle")).to_have_text("In force")
        await expect(fact_row).to_contain_text(f"Fact about {word}")
        await expect(fact_row).to_contain_text(f"Source: The call with Marie {word}")
        await expect(fact_row).to_contain_text(f"Recorded by {ADA}")
        await expect(decision_row).to_contain_text(f"Decision {word}")
        await expect(decision_row).to_contain_text(f"Decided by {MARIE}")
        # The connections the row names are the ones the record holds.
        connections = fact_row.locator("details.gv-connections li[data-other]")
        await expect(connections).to_have_count(2)
        assert sorted(
            await connections.evaluate_all("els => els.map(e => e.dataset.other)")
        ) == sorted([f"node:{story}", f"rule:{rule}"])

        # The map: a dot for each, with the same ids and states, and a line to what it concerns.
        await show(page, presentation="map")
        for key, kind, record in ((fact_key, "fact", fact), (decision_key, "decision", decision)):
            found = dot(page, key)
            await expect(found).to_have_count(1)
            assert await found.get_attribute("data-kind") == kind
            assert await found.get_attribute("data-id") == str(record["id"])
            assert await found.get_attribute("data-status") == "active"
            assert await found.get_attribute("data-confirmation") == "unconfirmed"
        await expect(edge(page, "concerns", fact_key, f"node:{story}")).to_have_count(1)
        await expect(edge(page, "concerns", fact_key, f"rule:{rule}")).to_have_count(1)
        await expect(edge(page, "concerns", decision_key, f"node:{story}")).to_have_count(1)

        # Choosing the fact's dot opens it in the shared detail panel, with every field.
        await dot(page, fact_key).click()
        detail = panel(page).locator(f".record-detail[data-record='{fact_key}']")
        await expect(detail).to_be_visible(timeout=SLOW)
        await expect(detail).to_have_attribute("data-status", "active")
        await expect(detail).to_have_attribute("data-confirmation", "unconfirmed")
        await expect(detail).to_contain_text(f"Fact about {word}")
        await expect(detail.locator(".source")).to_contain_text(f"The call with Marie {word}")
        await expect(detail).to_contain_text(f"Recorded by {ADA}")
        await expect(detail.locator(".confirmation-select")).to_have_value("unconfirmed")
        await expect(detail.locator(".confirmation-note")).to_contain_text("Not confirmed")
        assert (await page.evaluate("location.hash")).count(f"sel={fact_key}") == 1
        await dot(page, decision_key).click()
        detail = panel(page).locator(f".record-detail[data-record='{decision_key}']")
        await expect(detail).to_be_visible(timeout=SLOW)
        await expect(detail).to_contain_text(f"What was decided {word}")
        await expect(detail.locator(".reason")).to_contain_text(f"Because of {word}")
        await expect(detail).to_contain_text(f"Decided by {MARIE}")

        # Exact recall, section by section, for the story and for the requirement.
        await w.go(f"#/graph?view=context&as=list&epic={epic}&sel=node:{story}")
        await expect(panel(page).locator(".recall-section")).not_to_have_count(0, timeout=SLOW)
        await recall_matches(client, page, node_id=story)
        recalled = await ok(client, "recall", node_id=story)
        assert [i["fact"]["id"] for i in recalled["facts"]["items"]] == [fact["id"]]
        assert [i["decision"]["id"] for i in recalled["active_decisions"]["items"]] == [
            decision["id"]
        ]
        await w.go(f"#/graph?view=context&as=list&epic={epic}&sel=rule:{rule}")
        await expect(panel(page).locator(".recall-section")).not_to_have_count(0, timeout=SLOW)
        await recall_matches(client, page, rule_id=rule)
        assert [
            i["fact"]["id"] for i in (await ok(client, "recall", rule_id=rule))["facts"]["items"]
        ] == [fact["id"]]
        assert w.problems == [], w.problems


# -- 2. confirming and un-confirming ---------------------------------------------------------------


async def test_confirming_asks_for_the_person_and_shows_who_and_when_and_unconfirming_needs_words(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Confirming {word}")
    story = await made(client, "story", f"Reviewed story {word}", epic)
    fact = await make_fact(client, f"Reviewed fact {word}", node_ids=[story])
    decision = await make_decision(client, f"Reviewed decision {word}", node_ids=[story])
    fact_key, decision_key = f"fact:{fact['id']}", f"decision:{decision['id']}"
    # An agent recording a statement is not a person's review.
    assert (fact["confirmation"], decision["confirmation"]) == ("unconfirmed", "unconfirmed")

    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await graph_open(w, f"#/graph?view=context&as=list&epic={epic}&sel={fact_key}")
        detail = panel(page).locator(f".record-detail[data-record='{fact_key}']")
        select = detail.locator(".confirmation-select")
        await expect(select).to_have_value("unconfirmed", timeout=SLOW)

        # Confirming asks who reviewed it, and the board offers the name it is signed in with.
        # Blank it: the refusal reads as it does for an agent, nothing is written, what was typed
        # stays, and the dropdown goes on saying what is true.
        said = await refused(
            client,
            "set_confirmation",
            actor="x",
            kind="fact",
            record_id=fact["id"],
            confirmation="confirmed",
            person="",
        )
        await select.select_option("confirmed")
        box = dialog(page)
        await expect(box.locator("#confirm-person")).to_have_value(ADA)
        await box.locator("#confirm-person").fill("")
        await box.locator("#confirm-quote").fill(f"I read it {word}")
        await box.get_by_role("button", name="Confirm", exact=True).click()
        await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
        plain(await refusal_in(page).inner_text(), said)
        assert await box.locator("#confirm-quote").input_value() == f"I read it {word}"
        assert (await ok(client, "get_fact", fact_id=fact["id"]))["confirmation"] == "unconfirmed"
        await box.get_by_role("button", name="Cancel").click()
        await expect(box).to_have_count(0)
        await expect(select).to_have_value("unconfirmed")

        # With the person's name it is confirmed, and who and when are shown and read back.
        await select.select_option("confirmed")
        box = dialog(page)
        await box.locator("#confirm-person").fill(MARIE)
        await box.get_by_role("button", name="Confirm", exact=True).click()
        await expect(box).to_have_count(0, timeout=SLOW)
        await expect(detail).to_have_attribute("data-confirmation", "confirmed", timeout=SLOW)
        reviewed = await ok(client, "get_fact", fact_id=fact["id"])
        assert (reviewed["confirmation"], reviewed["confirmed_by"]) == ("confirmed", MARIE)
        assert reviewed["confirmed_at"] and reviewed["recorded_by"] == "agent-1"
        assert reviewed["status"] == "active" and reviewed["text"] == fact["text"]
        await expect(select).to_have_value("confirmed")
        await expect(detail.locator(".confirmation-note")).to_contain_text(
            f"Confirmed by {MARIE} on"
        )
        await expect(detail.locator(".chip.confirmation")).to_have_text(
            re.compile(rf"Confirmed by {MARIE}, {reviewed['confirmed_at'][:10]}")
        )
        events = await history_actions(client, "fact_history", "fact_id", fact["id"])
        assert events[-1] == "fact_confirmed"
        await expect(
            detail.locator("[data-part=history] li[data-action=fact_confirmed]")
        ).to_contain_text(ADA, timeout=SLOW)
        # The list row, the dot and the recall entry say the same.
        await expect(listed(page, fact_key)).to_have_attribute("data-confirmation", "confirmed")
        await expect(listed(page, fact_key).locator(".chip.confirmation")).to_contain_text(
            f"Confirmed by {MARIE}"
        )
        await show(page, presentation="map")
        await expect(dot(page, fact_key)).to_have_attribute("data-confirmation", "confirmed")
        await expect(dot(page, fact_key)).to_have_class(re.compile(r"\bconfirmed\b"))
        await expect(dot(page, decision_key)).to_have_attribute("data-confirmation", "unconfirmed")
        await w.go(f"#/graph?view=context&as=list&epic={epic}&sel=node:{story}")
        await expect(panel(page).locator(".recall-section")).not_to_have_count(0, timeout=SLOW)
        await recall_matches(client, page, node_id=story)
        await expect(
            panel(page).locator(f".recall-section li.record[data-record='{fact_key}']")
        ).to_have_attribute("data-confirmation", "confirmed")

        # A decision is confirmed from its row of the list, by the same dropdown.
        await expect(listed(page, decision_key)).to_be_visible(timeout=SLOW)
        row_select = listed(page, decision_key).locator(".confirmation-select")
        await row_select.select_option("confirmed")
        box = dialog(page)
        await box.locator("#confirm-person").fill(MARIE)
        await box.locator("#confirm-quote").fill(f"Yes, that is what we decided {word}")
        await box.get_by_role("button", name="Confirm", exact=True).click()
        await expect(box).to_have_count(0, timeout=SLOW)
        await expect(listed(page, decision_key)).to_have_attribute(
            "data-confirmation", "confirmed", timeout=SLOW
        )
        decided = await ok(client, "get_decision", decision_id=decision["id"])
        assert (decided["confirmation"], decided["confirmed_by"]) == ("confirmed", MARIE)
        assert decided["confirmed_at"] and decided["decided_by"] == "Marie"
        assert (await history_actions(client, "decision_history", "decision_id", decision["id"]))[
            -1
        ] == "decision_confirmed"

        # Un-confirming needs the person who says so and their words: both are refused when blank,
        # each time in the words an agent gets, and nothing is changed until both are given.
        await w.go(f"#/graph?view=context&as=list&epic={epic}&sel={fact_key}")
        await expect(select).to_have_value("confirmed", timeout=SLOW)
        asks = {
            "actor": "x",
            "kind": "fact",
            "record_id": fact["id"],
            "confirmation": "unconfirmed",
        }
        nobody = await refused(client, "set_confirmation", person="", quote="", **asks)
        silent = await refused(client, "set_confirmation", person=MARIE, quote="", **asks)
        assert nobody != silent
        await select.select_option("unconfirmed")
        box = dialog(page)
        await box.get_by_role("button", name="Un-confirm", exact=True).click()
        await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
        plain(await refusal_in(page).inner_text(), nobody)
        await box.locator("#confirm-person").fill(MARIE)
        await box.get_by_role("button", name="Un-confirm", exact=True).click()
        await expect(refusal_in(page)).to_contain_text("needs what the person said")
        plain(await refusal_in(page).inner_text(), silent)
        assert await box.locator("#confirm-person").input_value() == MARIE
        assert (await ok(client, "get_fact", fact_id=fact["id"]))["confirmation"] == "confirmed"
        await box.locator("#confirm-quote").fill(f"On second thought, no {word}")
        await box.get_by_role("button", name="Un-confirm", exact=True).click()
        await expect(box).to_have_count(0, timeout=SLOW)
        await expect(detail).to_have_attribute("data-confirmation", "unconfirmed", timeout=SLOW)
        again = await ok(client, "get_fact", fact_id=fact["id"])
        assert (again["confirmation"], again["confirmed_by"], again["confirmed_at"]) == (
            "unconfirmed",
            None,
            None,
        )
        # The earlier confirmation stays in its history, with who did each.
        assert (await history_actions(client, "fact_history", "fact_id", fact["id"]))[-2:] == [
            "fact_confirmed",
            "fact_unconfirmed",
        ]
        history = detail.locator("[data-part=history] li")
        await expect(history.filter(has_text=ADA).first).to_be_visible()
        await expect(detail.locator("li[data-action=fact_confirmed]")).to_have_count(1)
        await expect(detail.locator("li[data-action=fact_unconfirmed]")).to_have_count(1)
        assert w.problems == [], w.problems


# -- 3. replacing and withdrawing: the lifecycle and the earlier confirmation, told apart ----------


async def test_replacing_and_withdrawing_a_fact_keeps_it_and_its_confirmation_and_needs_a_word(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Ending {word}")
    story = await made(client, "story", f"Ended story {word}", epic)
    old = await make_fact(client, f"Old fact {word}", node_ids=[story], source=f"A call {word}")
    await confirm(client, "fact", old, MARIE)
    wrong = await make_fact(client, f"Wrong fact {word}", node_ids=[story])
    await confirm(client, "fact", wrong, MARIE)
    slip = await make_fact(client, f"Slip {word}", node_ids=[story])
    old_key, wrong_key, slip_key = (f"fact:{f['id']}" for f in (old, wrong, slip))

    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await graph_open(w, f"#/graph?view=context&as=list&epic={epic}")
        before = await tally(client)
        history_before = await history_actions(client, "fact_history", "fact_id", old["id"])

        # Replacing a confirmed fact: the dialog says a person confirmed it and asks for their word.
        await listed(page, old_key).locator("button.supersede-record").click()
        box = dialog(page)
        await expect(box.locator("details.said-by")).to_have_attribute("open", "")
        assert await box.locator("#supersede-text").input_value() == old["text"]
        assert await box.locator("#supersede-source").input_value() == f"A call {word}"
        await box.locator("#supersede-text").fill(f"New fact {word}")
        await box.locator("#supersede-source").fill(f"A measurement {word}")
        attempt = {
            "actor": "x",
            "replaces": [old["id"]],
            "text": f"New fact {word}",
            "source": f"A measurement {word}",
            "keep_links": True,
        }
        no_reason = await refused(client, "supersede_fact", reason="", **attempt)
        no_person = await refused(client, "supersede_fact", reason="Measured again", **attempt)
        assert "needs that person's word" in no_person and no_reason != no_person
        await box.get_by_role("button", name="Replace with this fact").click()
        await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
        plain(await refusal_in(page).inner_text(), no_reason)
        await box.locator("#supersede-reason").fill("Measured again")
        await box.get_by_role("button", name="Replace with this fact").click()
        await expect(refusal_in(page)).to_contain_text("needs that person's word")
        plain(await refusal_in(page).inner_text(), no_person)
        # Nothing was written, and what was typed is still there.
        assert await tally(client) == before
        assert (await ok(client, "get_fact", fact_id=old["id"]))["status"] == "active"
        assert await history_actions(client, "fact_history", "fact_id", old["id"]) == history_before
        assert await box.locator("#supersede-text").input_value() == f"New fact {word}"
        assert await box.locator("#supersede-reason").input_value() == "Measured again"
        # With who changed their mind and what they said, it goes through.
        await box.locator("#supersede-said-by").fill(MARIE)
        await box.locator("#supersede-quote").fill(f"Let us use the measurement {word}")
        await box.get_by_role("button", name="Replace with this fact").click()
        await expect(box).to_have_count(0, timeout=SLOW)

        ended = await ok(client, "get_fact", fact_id=old["id"])
        new = await ok(client, "get_fact", fact_id=ended["superseded_by_id"])
        assert (ended["status"], ended["status_reason"]) == ("superseded", "Measured again")
        # The earlier confirmation stays on the replaced record, and the new one has none.
        assert (ended["confirmation"], ended["confirmed_by"]) == ("confirmed", MARIE)
        assert new["supersedes_ids"] == [old["id"]] and new["status"] == "active"
        assert (new["confirmation"], new["confirmed_by"]) == ("unconfirmed", None)
        assert new["node_ids"] == [story] and new["source"] == f"A measurement {word}"
        events = (await ok(client, "fact_history", fact_id=old["id"]))["items"]
        assert events[-1]["action"] == "fact_superseded" and events[-1]["actor"] == ADA
        assert "Let us use the measurement" in str(events[-1])
        new_key = f"fact:{new['id']}"

        # In force by default; Replaced when asked for. The record shows both facts at once: it was
        # confirmed, and it is no longer in force.
        await expect(listed(page, old_key)).to_have_count(0)
        await expect(listed(page, new_key)).to_have_attribute("data-confirmation", "unconfirmed")
        await lifecycle_filter(page, "facts", "superseded")
        gone = listed(page, old_key)
        await expect(gone).to_have_attribute("data-status", "superseded")
        await expect(gone).to_have_attribute("data-confirmation", "confirmed")
        await expect(gone.locator(".chip.lifecycle")).to_have_text("Replaced")
        await expect(gone.locator(".chip.confirmation")).to_contain_text(f"Confirmed by {MARIE}")
        await expect(gone).to_have_class(re.compile(r"\bended\b"))
        await expect(gone.locator("button.supersede-record, button.withdraw-record")).to_have_count(
            0
        )
        await expect(gone.locator(".confirmation-select")).to_have_count(0)
        await gone.locator(".record-title").click()
        detail = panel(page).locator(f".record-detail[data-record='{old_key}']")
        await expect(detail).to_have_attribute("data-status", "superseded", timeout=SLOW)
        await expect(detail).to_have_attribute("data-confirmation", "confirmed")
        await expect(detail.locator("[data-part=lifecycle] .chain")).to_contain_text(
            f"Replaced by #{new['id']}"
        )
        await expect(detail.locator("[data-part=lifecycle] .ended-reason")).to_contain_text(
            "Measured again"
        )
        await expect(detail.locator(".confirmation-select")).to_be_disabled()
        await expect(detail.locator(".confirmation-note")).to_contain_text(f"Confirmed by {MARIE}")
        await expect(detail.locator(".confirmation-note")).to_contain_text("keeps the confirmation")
        await expect(detail.locator("button.supersede, button.withdraw")).to_have_count(0)
        # The one that replaced it says so too, and is not confirmed.
        await w.go(f"#/graph?view=context&as=list&epic={epic}&sel={new_key}")
        replacement = panel(page).locator(f".record-detail[data-record='{new_key}']")
        await expect(replacement.locator("[data-part=lifecycle] .replaces")).to_contain_text(
            f"#{old['id']}", timeout=SLOW
        )
        await expect(replacement).to_have_attribute("data-confirmation", "unconfirmed")

        # On the map the ended record is left out until asked for, and a line says what replaced it.
        await w.go(f"#/graph?view=context&as=map&epic={epic}")
        await expect(dot(page, new_key)).to_have_count(1, timeout=SLOW)
        await expect(dot(page, old_key)).to_have_count(0)
        await w.go(f"#/graph?view=context&as=map&epic={epic}&ended=1")
        found = dot(page, old_key)
        await expect(found).to_have_count(1, timeout=SLOW)
        assert await found.get_attribute("data-status") == "superseded"
        assert await found.get_attribute("data-confirmation") == "confirmed"
        await expect(found).to_have_class(re.compile(r"\bended\b"))
        await expect(edge(page, "replaces", new_key, old_key)).to_have_count(1)

        # Recall: the replaced one is a past fact (with its confirmation), the new one is a fact.
        await w.go(f"#/graph?view=context&as=list&epic={epic}&sel=node:{story}")
        await expect(panel(page).locator(".recall-section")).not_to_have_count(0, timeout=SLOW)
        await recall_matches(client, page, node_id=story)
        past = (await ok(client, "recall", node_id=story))["past_facts"]["items"]
        assert [(i["fact"]["id"], i["fact"]["confirmation"]) for i in past] == [
            (old["id"], "confirmed")
        ]

        # Withdrawing a confirmed fact also needs the person's word; a reason is always needed.
        await w.go(f"#/graph?view=context&as=list&epic={epic}")
        await lifecycle_filter(page, "facts", "active")
        await listed(page, wrong_key).locator("button.withdraw-record").click()
        box = dialog(page)
        asks = {"actor": "x", "fact_ids": [wrong["id"]]}
        none_given = await refused(client, "withdraw_fact", reason="", **asks)
        reason_only = await refused(client, "withdraw_fact", reason="It is wrong", **asks)
        await box.get_by_role("button", name="Withdraw", exact=True).click()
        await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
        plain(await refusal_in(page).inner_text(), none_given)
        await box.locator("#withdraw-reason").fill("It is wrong")
        await box.get_by_role("button", name="Withdraw", exact=True).click()
        await expect(refusal_in(page)).to_contain_text("needs that person's word")
        plain(await refusal_in(page).inner_text(), reason_only)
        assert (await ok(client, "get_fact", fact_id=wrong["id"]))["status"] == "active"
        assert await tally(client) == {**before, "facts": before["facts"] + 1}
        await box.locator("#withdraw-said-by").fill(MARIE)
        await box.locator("#withdraw-quote").fill("No, that is wrong")
        await box.get_by_role("button", name="Withdraw", exact=True).click()
        await expect(box).to_have_count(0, timeout=SLOW)
        withdrawn = await ok(client, "get_fact", fact_id=wrong["id"])
        assert (withdrawn["status"], withdrawn["status_reason"]) == ("withdrawn", "It is wrong")
        assert (withdrawn["confirmation"], withdrawn["confirmed_by"]) == ("confirmed", MARIE)
        # An unconfirmed fact needs only the reason.
        await listed(page, slip_key).locator("button.withdraw-record").click()
        box = dialog(page)
        await expect(box.locator("details.said-by")).not_to_have_attribute("open", "")
        await box.locator("#withdraw-reason").fill("A slip of the tongue")
        await box.get_by_role("button", name="Withdraw", exact=True).click()
        await expect(box).to_have_count(0, timeout=SLOW)
        assert (await ok(client, "get_fact", fact_id=slip["id"]))["status"] == "withdrawn"

        await lifecycle_filter(page, "facts", "withdrawn")
        for key, reason, sure in (
            (wrong_key, "It is wrong", "confirmed"),
            (slip_key, "A slip", "unconfirmed"),
        ):
            row = listed(page, key)
            await expect(row).to_have_attribute("data-status", "withdrawn", timeout=SLOW)
            await expect(row).to_have_attribute("data-confirmation", sure)
            await expect(row.locator(".chip.lifecycle")).to_have_text("Withdrawn")
            await row.locator(".record-title").click()
            detail = panel(page).locator(f".record-detail[data-record='{key}']")
            await expect(detail.locator("[data-part=lifecycle] .ended-reason")).to_contain_text(
                f"Withdrawn: {reason}", timeout=SLOW
            )
            await expect(detail).to_have_attribute("data-confirmation", sure)
        # "All" lists every lifecycle together, each told apart by its own chips.
        await lifecycle_filter(page, "facts", "")
        await expect(listed(page, new_key)).to_have_attribute("data-status", "active")
        await expect(listed(page, old_key)).to_have_attribute("data-status", "superseded")
        await expect(listed(page, wrong_key)).to_have_attribute("data-status", "withdrawn")
        assert w.problems == [], w.problems


async def test_replacing_and_withdrawing_a_decision_keep_it_and_its_confirmation_and_need_words(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Deciding {word}")
    story = await made(client, "story", f"Decided story {word}", epic)
    old = await make_decision(client, f"Old decision {word}", node_ids=[story])
    await confirm(client, "decision", old, MARIE)
    drop = await make_decision(client, f"Dropped decision {word}", node_ids=[story])
    old_key, drop_key = f"decision:{old['id']}", f"decision:{drop['id']}"

    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await graph_open(w, f"#/graph?view=context&as=list&epic={epic}")
        before = await tally(client)

        # Replacing a confirmed decision needs the word of the person who confirmed it.
        await listed(page, old_key).locator("button.supersede-record").click()
        box = dialog(page)
        await expect(box.locator("details.said-by")).to_have_attribute("open", "")
        assert await box.locator("#supersede-title").input_value() == old["title"]
        assert await box.locator("#supersede-by").input_value() == ADA
        await box.locator("#supersede-title").fill(f"New decision {word}")
        await box.locator("#supersede-text").fill(f"What is decided now {word}")
        attempt = {
            "actor": "x",
            "decision_id": old["id"],
            "title": f"New decision {word}",
            "text": f"What is decided now {word}",
            "decided_by": ADA,
            "project_wide": False,
            "keep_links": True,
        }
        no_reason = await refused(client, "supersede_decision", reason="", **attempt)
        no_person = await refused(client, "supersede_decision", reason="We learned more", **attempt)
        assert "needs that person's word" in no_person and no_reason != no_person
        await box.get_by_role("button", name="Replace with this decision").click()
        await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
        plain(await refusal_in(page).inner_text(), no_reason)
        await box.locator("#supersede-reason").fill("We learned more")
        await box.get_by_role("button", name="Replace with this decision").click()
        await expect(refusal_in(page)).to_contain_text("needs that person's word")
        plain(await refusal_in(page).inner_text(), no_person)
        assert await tally(client) == before
        assert (await ok(client, "get_decision", decision_id=old["id"]))["status"] == "active"
        assert await box.locator("#supersede-title").input_value() == f"New decision {word}"
        await box.locator("#supersede-said-by").fill(MARIE)
        await box.locator("#supersede-quote").fill(f"Yes, change it {word}")
        await box.get_by_role("button", name="Replace with this decision").click()
        await expect(box).to_have_count(0, timeout=SLOW)

        ended = await ok(client, "get_decision", decision_id=old["id"])
        new = await ok(client, "get_decision", decision_id=ended["superseded_by_id"])
        # A replaced decision gives no reason of its own: the new one carries why it changed.
        assert (ended["status"], ended["status_reason"]) == ("superseded", None)
        assert (ended["confirmation"], ended["confirmed_by"]) == ("confirmed", MARIE)
        assert (new["confirmation"], new["decided_by"], new["supersedes_ids"]) == (
            "unconfirmed",
            ADA,
            [old["id"]],
        )
        assert new["node_ids"] == [story] and new["reason"] == "We learned more"
        new_key = f"decision:{new['id']}"
        events = (await ok(client, "decision_history", decision_id=old["id"]))["items"]
        assert (events[-1]["action"], events[-1]["actor"]) == ("decision_superseded", ADA)
        assert "Yes, change it" in str(events[-1])

        await expect(listed(page, old_key)).to_have_count(0)
        await expect(listed(page, new_key)).to_have_attribute("data-confirmation", "unconfirmed")
        await lifecycle_filter(page, "decisions", "superseded")
        gone = listed(page, old_key)
        await expect(gone).to_have_attribute("data-status", "superseded")
        await expect(gone).to_have_attribute("data-confirmation", "confirmed")
        await expect(gone.locator(".chip.lifecycle")).to_have_text("Replaced")
        await expect(gone.locator(".chip.confirmation")).to_contain_text(f"Confirmed by {MARIE}")
        await expect(gone.locator("button.supersede-record, button.withdraw-record")).to_have_count(
            0
        )
        await gone.locator(".record-title").click()
        detail = panel(page).locator(f".record-detail[data-record='{old_key}']")
        await expect(detail.locator("[data-part=lifecycle] .chain")).to_contain_text(
            f"Replaced by #{new['id']}", timeout=SLOW
        )
        await expect(detail.locator(".confirmation-select")).to_be_disabled()
        await expect(detail).to_have_attribute("data-confirmation", "confirmed")
        await w.go(f"#/graph?view=context&as=map&epic={epic}&ended=1")
        await expect(dot(page, old_key)).to_have_attribute(
            "data-status", "superseded", timeout=SLOW
        )
        await expect(dot(page, old_key)).to_have_attribute("data-confirmation", "confirmed")
        await expect(edge(page, "replaces", new_key, old_key)).to_have_count(1)

        # Withdrawing an unconfirmed decision needs only a reason, which is never blank.
        await w.go(f"#/graph?view=context&as=list&epic={epic}")
        await lifecycle_filter(page, "decisions", "active")
        await listed(page, drop_key).locator("button.withdraw-record").click()
        box = dialog(page)
        blank = await refused(
            client, "withdraw_decision", actor="x", decision_id=drop["id"], reason=""
        )
        await box.get_by_role("button", name="Withdraw", exact=True).click()
        await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
        plain(await refusal_in(page).inner_text(), blank)
        assert (await ok(client, "get_decision", decision_id=drop["id"]))["status"] == "active"
        await box.locator("#withdraw-reason").fill("No longer needed")
        await box.get_by_role("button", name="Withdraw", exact=True).click()
        await expect(box).to_have_count(0, timeout=SLOW)
        dropped = await ok(client, "get_decision", decision_id=drop["id"])
        assert (dropped["status"], dropped["status_reason"]) == ("withdrawn", "No longer needed")
        assert dropped["confirmation"] == "unconfirmed"
        await lifecycle_filter(page, "decisions", "withdrawn")
        row = listed(page, drop_key)
        await expect(row).to_have_attribute("data-status", "withdrawn", timeout=SLOW)
        await expect(row.locator(".chip.lifecycle")).to_have_text("Withdrawn")
        await row.locator(".record-title").click()
        await expect(
            panel(page).locator(f".record-detail[data-record='{drop_key}'] .ended-reason")
        ).to_contain_text("Withdrawn: No longer needed", timeout=SLOW)

        # Recall tells the past decisions apart from the one in force.
        await w.go(f"#/graph?view=context&as=list&epic={epic}&sel=node:{story}")
        await expect(panel(page).locator(".recall-section")).not_to_have_count(0, timeout=SLOW)
        await recall_matches(client, page, node_id=story)
        recalled = await ok(client, "recall", node_id=story)
        assert [i["decision"]["id"] for i in recalled["active_decisions"]["items"]] == [new["id"]]
        assert sorted(i["decision"]["id"] for i in recalled["past_decisions"]["items"]) == sorted(
            [old["id"], drop["id"]]
        )
        assert w.problems == [], w.problems


# -- 4. blanks and what is not a person's: refused with nothing written, and nothing typed lost ----


async def test_a_blank_source_reason_or_person_is_refused_in_the_servers_words_and_writes_nothing(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Blanks {word}")
    story = await made(client, "story", f"Blank story {word}", epic)
    fact = await make_fact(client, f"Kept fact {word}", node_ids=[story])
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await graph_open(w, f"#/graph?view=context&as=list&epic={epic}")
        before = await tally(client)

        # A fact with no source.
        said = await refused(
            client, "retain_fact", actor="x", text=f"No source {word}", source="", node_ids=[story]
        )
        await page.locator("#record-fact").click()
        box = dialog(page)
        await box.locator("#record-fact-text").fill(f"No source {word}")
        await chosen_picker(page, f"#{story}")
        await box.get_by_role("button", name="Record the fact").click()
        await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
        plain(await refusal_in(page).inner_text(), said)
        assert await box.locator("#record-fact-text").input_value() == f"No source {word}"
        await expect(box.locator(f".picker li[data-ref='node:{story}']")).to_be_visible()
        assert await tally(client) == before
        await box.get_by_role("button", name="Cancel").click()

        # A decision with no reason, and one with no person who decided it.
        await page.locator("#record-decision").click()
        box = dialog(page)
        await box.locator("#record-decision-title").fill(f"Blank {word}")
        await box.locator("#record-decision-text").fill(f"Decided {word}")
        await box.locator("#record-decision-by").fill(MARIE)
        base = {
            "actor": "x",
            "title": f"Blank {word}",
            "text": f"Decided {word}",
            "project_wide": False,
        }
        no_reason = await refused(client, "record_decision", reason="", decided_by=MARIE, **base)
        await box.get_by_role("button", name="Record the decision").click()
        await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
        plain(await refusal_in(page).inner_text(), no_reason)
        await box.locator("#record-decision-reason").fill(f"A reason {word}")
        await box.locator("#record-decision-by").fill("")
        no_person = await refused(
            client, "record_decision", reason=f"A reason {word}", decided_by="", **base
        )
        await box.get_by_role("button", name="Record the decision").click()
        await expect(refusal_in(page)).to_contain_text("decided_by", timeout=SLOW)
        plain(await refusal_in(page).inner_text(), no_person)
        assert no_reason != no_person
        for name, value in (
            ("#record-decision-title", f"Blank {word}"),
            ("#record-decision-text", f"Decided {word}"),
            ("#record-decision-reason", f"A reason {word}"),
        ):
            assert await box.locator(name).input_value() == value
        assert await tally(client) == before
        await box.get_by_role("button", name="Cancel").click()

        # Confirming with no person: the dropdown goes back to what is true, and nothing changes.
        await (
            listed(page, f"fact:{fact['id']}")
            .locator(".confirmation-select")
            .select_option("confirmed")
        )
        box = dialog(page)
        await box.locator("#confirm-person").fill("  ")
        await box.get_by_role("button", name="Confirm", exact=True).click()
        await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
        await box.get_by_role("button", name="Cancel").click()
        await expect(
            listed(page, f"fact:{fact['id']}").locator(".confirmation-select")
        ).to_have_value("unconfirmed")
        assert (await ok(client, "get_fact", fact_id=fact["id"]))["confirmation"] == "unconfirmed"
        assert await tally(client) == before
        assert w.problems == [], w.problems


# -- 5. a project-wide decision: in recall and in the briefing of its project, never another's -----


async def test_a_project_wide_decision_is_in_its_projects_recall_and_briefing_and_never_anothers(
    server_url: str,
) -> None:
    word = unique_word()
    first, second = make_project(register()), make_project(register())
    wide_a, wide_b = f"Wide A {word}", f"Wide B {word}"
    async with connect(server_url, first.key) as a, connect(server_url, second.key) as b:
        trees = {}
        for client, project in ((a, first), (b, second)):
            epic = await made(client, "epic", f"Epic {word}")
            story = await made(client, "story", f"Story {word}", epic)
            await made(client, "task", f"Task {word}", story)
            trees[project.id] = (epic, story)
        epic_a, story_a = trees[first.id]
        epic_b, story_b = trees[second.id]
        other = await make_decision(b, wide_b, project_wide=True)
        assert other["project_wide"] is True

        async with browser_page(board_url(server_url, first), token=first.key, name=ADA) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=context&as=list&epic={epic_a}")
            await expect(page.locator("#ctx-decisions-count")).to_have_attribute("data-total", "0")
            await page.locator("#record-decision").click()
            box = dialog(page)
            await box.locator("#record-decision-title").fill(wide_a)
            await box.locator("#record-decision-text").fill(f"Everything is metric {word}")
            await box.locator("#record-decision-reason").fill(f"One system only {word}")
            await box.locator("#record-decision-by").fill(MARIE)
            await box.locator("#record-decision-project-wide").check()
            await box.get_by_role("button", name="Record the decision").click()
            await expect(box).to_have_count(0, timeout=SLOW)

            mine = (await ok(a, "list_decisions", project_wide=True))["items"]
            assert [(d["title"], d["node_ids"], d["rule_ids"]) for d in mine] == [(wide_a, [], [])]
            key = f"decision:{mine[0]['id']}"
            # It is in the list although it is linked to nothing, and says it is project-wide.
            row = listed(page, key)
            await expect(row).to_have_attribute("data-project-wide", "true", timeout=SLOW)
            await expect(row.locator(".chip.project-wide")).to_contain_text("Project-wide")
            await expect(row.locator(".chip.confirmation")).to_contain_text("Unconfirmed")

            # Recall of any item of the project carries it, flagged as not confirmed; so does the
            # briefing for work, and the detail panel shows what recall says.
            recalled = await ok(a, "recall", node_id=story_a)
            wide_items = recalled["project_wide_decisions"]["items"]
            assert [i["decision"]["title"] for i in wide_items] == [wide_a]
            assert wide_items[0]["decision"]["confirmation"] == "unconfirmed"
            assert recalled["active_decisions"]["items"] == []
            brief = await ok(a, "next_work", actor="agent-a", within_node_id=epic_a, peek=True)
            wide_brief = brief["context"]["project_wide_decisions"]
            assert [(d["title"], d["confirmation"]) for d in wide_brief] == [
                (wide_a, "unconfirmed")
            ]
            await w.go(f"#/graph?view=context&as=list&epic={epic_a}&sel=node:{story_a}")
            await expect(panel(page).locator(".recall-section")).not_to_have_count(0, timeout=SLOW)
            await recall_matches(a, page, node_id=story_a)
            entry = panel(page).locator(
                f"[data-section=project_wide_decisions] li.record[data-record='{key}']"
            )
            await expect(entry).to_have_attribute("data-project-wide", "true")
            await expect(entry).to_have_attribute("data-confirmation", "unconfirmed")

            # Once a person confirms it, recall and the briefing say so, and who confirmed it.
            await w.go(f"#/graph?view=context&as=list&epic={epic_a}")
            await listed(page, key).locator(".confirmation-select").select_option("confirmed")
            box = dialog(page)
            await box.locator("#confirm-person").fill(MARIE)
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            recalled = await ok(a, "recall", node_id=story_a)
            confirmed = recalled["project_wide_decisions"]["items"][0]["decision"]
            assert (confirmed["confirmation"], confirmed["confirmed_by"]) == ("confirmed", MARIE)
            brief = await ok(a, "next_work", actor="agent-a", within_node_id=epic_a, peek=True)
            [wide_brief] = brief["context"]["project_wide_decisions"]
            assert (wide_brief["confirmation"], wide_brief["confirmed_by"]) == ("confirmed", MARIE)
            await w.go(f"#/graph?view=context&as=list&epic={epic_a}&sel=node:{story_a}")
            await expect(entry).to_have_attribute("data-confirmation", "confirmed", timeout=SLOW)
            assert w.problems == [], w.problems

        # The other project hears nothing of it: not in recall, the briefing, its list or its page.
        recalled_b = await ok(b, "recall", node_id=story_b)
        assert [i["decision"]["title"] for i in recalled_b["project_wide_decisions"]["items"]] == [
            wide_b
        ]
        brief_b = await ok(b, "next_work", actor="agent-b", within_node_id=epic_b, peek=True)
        assert [d["title"] for d in brief_b["context"]["project_wide_decisions"]] == [wide_b]
        assert [d["title"] for d in (await ok(b, "list_decisions"))["items"]] == [wide_b]
        async with browser_page(
            board_url(server_url, second), token=second.key, name=ADA, keep_bodies=True
        ) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=context&as=list&epic={epic_b}")
            await expect(page.locator("#ctx-decisions-count")).to_have_attribute(
                "data-total", "1", timeout=SLOW
            )
            await expect(page.locator("#ctx-decisions-list")).to_contain_text(wide_b)
            shown = await page.locator("body").inner_text()
            assert wide_a not in shown and f"Everything is metric {word}" not in shown
            assert all(wide_a not in body for _, body in w.bodies)
            assert w.problems == [], w.problems
