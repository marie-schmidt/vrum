"""Chapter 10 of the acceptance scenario: what the project knows (M4A, M12 and M16's two records),
on the board. A fact and a decision are recorded in the Graph's context list with their source and
reason and the person who decided, linked to work, confirmed by a named person through one
dropdown, replaced and withdrawn, and a decision that applies throughout its project is in that
project's recall and briefings and no other's. Every figure is compared with what a real MCP
client reads (`get_fact`, `get_decision`, `list_*`, `recall`, the histories), and every refusal is
compared word for word with what the same attempt gets through MCP. Each part works in a project
of its own, so that nothing but what it made is on the page."""

import re

from playwright.async_api import Error as BrowserError
from playwright.async_api import expect

from tests.acceptance_board import same_words
from tests.acceptance_scenario import URL, Failed, chapter, clip, proof
from tests.board_context import (
    ADA,
    dialog,
    dot,
    edge,
    fresh_project,
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
from tests.board_helpers import SLOW, board_url, browser_page, made
from tests.helpers import confirm, connect, make_decision, make_fact, ok, refused, unique_word
from tests.test_board_context_integration import MARIE, chosen_picker
from tests.world import make_project, register


async def context() -> None:
    chapter("What the project knows: facts and decisions, recorded, confirmed, replaced, withdrawn")
    try:
        await recording()
        await confirming()
        await ending_a_fact()
        await ending_a_decision()
        await blanks()
        await project_wide()
    except BrowserError as error:
        raise Failed(
            f"M16: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- a fact and a decision, recorded on the board, are the same records everywhere ----------------


async def recording() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Context {word}")
        story = await made(client, "story", f"Concerned story {word}", epic)
        rule = (
            await ok(
                client, "create_rule", actor="agent-1", title=f"Concerned {word}", story_id=story
            )
        )["id"]
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=context&as=list&epic={epic}")
            await page.locator("#record-fact").click()
            box = dialog(page)
            await box.locator("#record-fact-text").fill(f"Fact about {word}")
            await box.locator("#record-fact-source").fill(f"The call with Marie {word}")
            await chosen_picker(page, f"#{story}", f"requirement {rule}")
            await box.get_by_role("button", name="Record the fact").click()
            await expect(box).to_have_count(0, timeout=SLOW)
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
            decisions = (await ok(client, "list_decisions"))["items"]
            assert len(facts) == 1 and len(decisions) == 1, (facts, decisions)
            fact = await ok(client, "get_fact", fact_id=facts[0]["id"])
            decision = await ok(client, "get_decision", decision_id=decisions[0]["id"])
            fact_key, decision_key = f"fact:{fact['id']}", f"decision:{decision['id']}"
            events = (await ok(client, "decision_history", decision_id=decision["id"]))["items"]
            fields_hold = (
                (fact["text"], fact["source"], fact["recorded_by"])
                == (f"Fact about {word}", f"The call with Marie {word}", ADA)
                and (fact["status"], fact["confirmation"], fact["confirmed_by"])
                == ("active", "unconfirmed", None)
                and (fact["node_ids"], fact["rule_ids"]) == ([story], [rule])
                and (decision["title"], decision["reason"], decision["decided_by"])
                == (f"Decision {word}", f"Because of {word}", MARIE)
                and (decision["project_wide"], decision["node_ids"]) == (False, [story])
                and [(e["action"], e["actor"]) for e in events]
                == [("decision_recorded", ADA), ("decision_linked", ADA)]
            )
            # The context list: the same ids and fields, an unconfirmed record said to be.
            fact_row, decision_row = listed(page, fact_key), listed(page, decision_key)
            await expect(fact_row).to_be_visible(timeout=SLOW)
            for row, record in ((fact_row, fact), (decision_row, decision)):
                await expect(row).to_have_attribute("data-id", str(record["id"]))
                await expect(row).to_have_attribute("data-confirmation", "unconfirmed")
                await expect(row.locator(".chip.confirmation")).to_have_text(
                    re.compile("Unconfirmed")
                )
            await expect(fact_row).to_contain_text(f"Source: The call with Marie {word}")
            await expect(fact_row).to_contain_text(f"Recorded by {ADA}")
            await expect(decision_row).to_contain_text(f"Decided by {MARIE}")
            # The map: a dot for each, with the same ids and states, and a line to what it concerns.
            await show(page, presentation="map")
            for key, record in ((fact_key, fact), (decision_key, decision)):
                await expect(dot(page, key)).to_have_attribute("data-id", str(record["id"]))
                await expect(dot(page, key)).to_have_attribute("data-confirmation", "unconfirmed")
            await expect(edge(page, "concerns", fact_key, f"node:{story}")).to_have_count(1)
            await expect(edge(page, "concerns", fact_key, f"rule:{rule}")).to_have_count(1)
            await expect(edge(page, "concerns", decision_key, f"node:{story}")).to_have_count(1)
            # Choosing the dot opens it in the shared detail panel, with every field.
            await dot(page, fact_key).click()
            detail = panel(page).locator(f".record-detail[data-record='{fact_key}']")
            await expect(detail).to_be_visible(timeout=SLOW)
            await expect(detail).to_contain_text(f"Source: The call with Marie {word}")
            await expect(detail.locator(".confirmation-select")).to_have_value("unconfirmed")
            await dot(page, decision_key).click()
            detail = panel(page).locator(f".record-detail[data-record='{decision_key}']")
            await expect(detail).to_contain_text(f"Why: Because of {word}", timeout=SLOW)
            # Exact recall, section by section, for the story and for the requirement.
            for target in (f"node:{story}", f"rule:{rule}"):
                await w.go(f"#/graph?view=context&as=list&epic={epic}&sel={target}")
                await expect(panel(page).locator(".recall-section")).not_to_have_count(
                    0, timeout=SLOW
                )
                kind, ident = target.split(":")
                await recall_matches(
                    client, page, **{("node_id" if kind == "node" else "rule_id"): int(ident)}
                )
            recalled = await ok(client, "recall", node_id=story)
            in_recall = (
                [i["fact"]["id"] for i in recalled["facts"]["items"]],
                [i["decision"]["id"] for i in recalled["active_decisions"]["items"]],
            )
            assert w.problems == [], w.problems
    proof(
        "M16",
        "a fact with its source and a decision with its reason and named decider, recorded on "
        "the board and linked to a story and a requirement, are the same records everywhere: "
        "the ids and fields of get and list, the rows of the context list, the dots and lines of "
        "the Map, the shared detail panel, and exact recall of the story and of the requirement",
        fields_hold and in_recall == ([fact["id"]], [decision["id"]]),
        f"fact {fact['id']} and decision {decision['id']}, recorded by {ADA}",
    )


# -- confirming: who and when, and what un-confirming needs --------------------------------


async def confirming() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Confirming {word}")
        story = await made(client, "story", f"Reviewed story {word}", epic)
        fact = await make_fact(client, f"Reviewed fact {word}", node_ids=[story])
        decision = await make_decision(client, f"Reviewed decision {word}", node_ids=[story])
        fact_key, decision_key = f"fact:{fact['id']}", f"decision:{decision['id']}"
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=context&as=list&epic={epic}&sel={fact_key}")
            detail = panel(page).locator(f".record-detail[data-record='{fact_key}']")
            select = detail.locator(".confirmation-select")
            await expect(select).to_have_value("unconfirmed", timeout=SLOW)
            said = await refused(
                client, "set_confirmation", actor="x", kind="fact", record_id=fact["id"],
                confirmation="confirmed", person="",
            )  # fmt: skip
            await select.select_option("confirmed")
            box = dialog(page)
            offered = await box.locator("#confirm-person").input_value()
            await box.locator("#confirm-person").fill("")
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            blank_words = await refusal_in(page).inner_text()
            still = (await ok(client, "get_fact", fact_id=fact["id"]))["confirmation"]
            await box.get_by_role("button", name="Cancel").click()
            await expect(select).to_have_value("unconfirmed")
            await select.select_option("confirmed")
            box = dialog(page)
            await box.locator("#confirm-person").fill(MARIE)
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(detail).to_have_attribute("data-confirmation", "confirmed", timeout=SLOW)
            reviewed = await ok(client, "get_fact", fact_id=fact["id"])
            await expect(detail.locator(".chip.confirmation")).to_have_text(
                re.compile(rf"Confirmed by {MARIE}, {reviewed['confirmed_at'][:10]}")
            )
            await expect(
                detail.locator("[data-part=history] li[data-action=fact_confirmed]")
            ).to_contain_text(ADA, timeout=SLOW)
            await expect(listed(page, fact_key)).to_have_attribute("data-confirmation", "confirmed")
            await show(page, presentation="map")
            await expect(dot(page, fact_key)).to_have_class(re.compile(r"\bconfirmed\b"))
            await expect(dot(page, decision_key)).to_have_attribute(
                "data-confirmation", "unconfirmed"
            )
            await w.go(f"#/graph?view=context&as=list&epic={epic}&sel=node:{story}")
            await expect(panel(page).locator(".recall-section")).not_to_have_count(0, timeout=SLOW)
            await recall_matches(client, page, node_id=story)
            # A decision is confirmed from its row of the list, by the same dropdown.
            await expect(listed(page, decision_key)).to_be_visible(timeout=SLOW)
            await (
                listed(page, decision_key)
                .locator(".confirmation-select")
                .select_option("confirmed")
            )
            box = dialog(page)
            await box.locator("#confirm-person").fill(MARIE)
            await box.locator("#confirm-quote").fill(f"Yes, that is what we decided {word}")
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(listed(page, decision_key)).to_have_attribute(
                "data-confirmation", "confirmed", timeout=SLOW
            )
            decided = await ok(client, "get_decision", decision_id=decision["id"])
            # Un-confirming needs the person and their words: refused when either is blank.
            await w.go(f"#/graph?view=context&as=list&epic={epic}&sel={fact_key}")
            await expect(select).to_have_value("confirmed", timeout=SLOW)
            away = {"actor": "x", "kind": "fact", "record_id": fact["id"]}
            nobody = await refused(
                client, "set_confirmation", confirmation="unconfirmed", person="", quote="", **away
            )
            silent = await refused(
                client, "set_confirmation", confirmation="unconfirmed", person=MARIE, quote="",
                **away,
            )  # fmt: skip
            await select.select_option("unconfirmed")
            box = dialog(page)
            await box.get_by_role("button", name="Un-confirm", exact=True).click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            nobody_words = await refusal_in(page).inner_text()
            await box.locator("#confirm-person").fill(MARIE)
            await box.get_by_role("button", name="Un-confirm", exact=True).click()
            await expect(refusal_in(page)).to_contain_text("needs what the person said")
            silent_words = await refusal_in(page).inner_text()
            kept_person = await box.locator("#confirm-person").input_value()
            held = (await ok(client, "get_fact", fact_id=fact["id"]))["confirmation"]
            await box.locator("#confirm-quote").fill(f"On second thought, no {word}")
            await box.get_by_role("button", name="Un-confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await expect(detail).to_have_attribute("data-confirmation", "unconfirmed", timeout=SLOW)
            again = await ok(client, "get_fact", fact_id=fact["id"])
            fact_events = await history_actions(client, "fact_history", "fact_id", fact["id"])
            decision_events = await history_actions(
                client, "decision_history", "decision_id", decision["id"]
            )
            await expect(detail.locator("li[data-action=fact_confirmed]")).to_have_count(1)
            await expect(detail.locator("li[data-action=fact_unconfirmed]")).to_have_count(1)
            assert w.problems == [], w.problems
    proof(
        "M16",
        "confirming through the dropdown asks for the person (the signed-in name is offered, a "
        "blank is refused in the tool's words and nothing changes) and then shows who and when in "
        "the panel, the row, the dot, recall and the history, attributed to the person at the "
        "keyboard; a decision is confirmed the same way",
        offered == ADA
        and same_words(blank_words, said)
        and still == "unconfirmed"
        and (reviewed["confirmation"], reviewed["confirmed_by"]) == ("confirmed", MARIE)
        and reviewed["recorded_by"] == "agent-1"
        and fact_events[-2:] == ["fact_confirmed", "fact_unconfirmed"]
        and (decided["confirmation"], decided["confirmed_by"], decided["decided_by"])
        == ("confirmed", MARIE, "Marie")
        and decision_events[-1] == "decision_confirmed",
        f"{fact_key} and {decision_key}, confirmed by {MARIE}",
    )
    proof(
        "M16",
        "un-confirming needs the person who says so and their words: each blank is refused in the "
        "words an agent gets, what was typed stays, and only then is the confirmation taken away "
        "(and the earlier one stays in the history)",
        same_words(nobody_words, nobody)
        and same_words(silent_words, silent)
        and nobody != silent
        and kept_person == MARIE
        and held == "confirmed"
        and (again["confirmation"], again["confirmed_by"], again["confirmed_at"])
        == ("unconfirmed", None, None),
        f'"{clip(silent_words, 70)}"',
    )


# -- replacing and withdrawing: the lifecycle and the earlier confirmation, told apart -------


async def ending_a_fact() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Ending {word}")
        story = await made(client, "story", f"Ended story {word}", epic)
        old = await make_fact(client, f"Old fact {word}", node_ids=[story], source=f"A call {word}")
        await confirm(client, "fact", old, MARIE)
        wrong = await make_fact(client, f"Wrong fact {word}", node_ids=[story])
        await confirm(client, "fact", wrong, MARIE)
        slip = await make_fact(client, f"Slip {word}", node_ids=[story])
        old_key, wrong_key, slip_key = (f"fact:{f['id']}" for f in (old, wrong, slip))
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=context&as=list&epic={epic}")
            before = await tally(client)
            await listed(page, old_key).locator("button.supersede-record").click()
            box = dialog(page)
            asks_for_word = await box.locator("details.said-by").get_attribute("open") is not None
            await box.locator("#supersede-text").fill(f"New fact {word}")
            await box.locator("#supersede-source").fill(f"A measurement {word}")
            attempt = {
                "actor": "x", "replaces": [old["id"]], "text": f"New fact {word}",
                "source": f"A measurement {word}", "keep_links": True,
            }  # fmt: skip
            no_person = await refused(client, "supersede_fact", reason="Measured again", **attempt)
            await box.locator("#supersede-reason").fill("Measured again")
            await box.get_by_role("button", name="Replace with this fact").click()
            await expect(refusal_in(page)).to_contain_text("needs that person's word", timeout=SLOW)
            no_person_words = await refusal_in(page).inner_text()
            nothing_written = (
                await tally(client) == before
                and (await ok(client, "get_fact", fact_id=old["id"]))["status"] == "active"
                and await box.locator("#supersede-text").input_value() == f"New fact {word}"
            )
            await box.locator("#supersede-said-by").fill(MARIE)
            await box.locator("#supersede-quote").fill(f"Let us use the measurement {word}")
            await box.get_by_role("button", name="Replace with this fact").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            ended = await ok(client, "get_fact", fact_id=old["id"])
            new = await ok(client, "get_fact", fact_id=ended["superseded_by_id"])
            new_key = f"fact:{new['id']}"
            events = (await ok(client, "fact_history", fact_id=old["id"]))["items"]
            # In force by default; Replaced when asked for, with the confirmation it had.
            await expect(listed(page, old_key)).to_have_count(0)
            await lifecycle_filter(page, "facts", "superseded")
            gone = listed(page, old_key)
            await expect(gone).to_have_attribute("data-status", "superseded")
            await expect(gone).to_have_attribute("data-confirmation", "confirmed")
            await expect(gone.locator(".chip.lifecycle")).to_have_text("Replaced")
            await expect(gone.locator(".chip.confirmation")).to_contain_text(
                f"Confirmed by {MARIE}"
            )
            await expect(
                gone.locator("button.supersede-record, button.withdraw-record")
            ).to_have_count(0)
            await gone.locator(".record-title").click()
            detail = panel(page).locator(f".record-detail[data-record='{old_key}']")
            await expect(detail.locator("[data-part=lifecycle] .chain")).to_contain_text(
                f"Replaced by #{new['id']}", timeout=SLOW
            )
            await expect(detail.locator(".confirmation-select")).to_be_disabled()
            await expect(detail.locator(".confirmation-note")).to_contain_text(
                "keeps the confirmation"
            )
            await w.go(f"#/graph?view=context&as=map&epic={epic}&ended=1")
            await expect(dot(page, old_key)).to_have_attribute(
                "data-status", "superseded", timeout=SLOW
            )
            await expect(dot(page, old_key)).to_have_attribute("data-confirmation", "confirmed")
            await expect(edge(page, "replaces", new_key, old_key)).to_have_count(1)
            await w.go(f"#/graph?view=context&as=list&epic={epic}&sel=node:{story}")
            await expect(panel(page).locator(".recall-section")).not_to_have_count(0, timeout=SLOW)
            await recall_matches(client, page, node_id=story)
            past = (await ok(client, "recall", node_id=story))["past_facts"]["items"]
            # Withdrawing a confirmed fact needs the person's word too; an unconfirmed one needs a
            # reason only.
            await w.go(f"#/graph?view=context&as=list&epic={epic}")
            await lifecycle_filter(page, "facts", "active")
            await listed(page, wrong_key).locator("button.withdraw-record").click()
            box = dialog(page)
            reason_only = await refused(
                client, "withdraw_fact", actor="x", fact_ids=[wrong["id"]], reason="It is wrong"
            )
            await box.locator("#withdraw-reason").fill("It is wrong")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(refusal_in(page)).to_contain_text("needs that person's word", timeout=SLOW)
            withdraw_words = await refusal_in(page).inner_text()
            still_active = (await ok(client, "get_fact", fact_id=wrong["id"]))["status"]
            await box.locator("#withdraw-said-by").fill(MARIE)
            await box.locator("#withdraw-quote").fill("No, that is wrong")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            await listed(page, slip_key).locator("button.withdraw-record").click()
            box = dialog(page)
            await box.locator("#withdraw-reason").fill("A slip of the tongue")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            withdrawn = await ok(client, "get_fact", fact_id=wrong["id"])
            slipped = await ok(client, "get_fact", fact_id=slip["id"])
            await lifecycle_filter(page, "facts", "withdrawn")
            row = listed(page, wrong_key)
            await expect(row).to_have_attribute("data-status", "withdrawn", timeout=SLOW)
            await expect(row).to_have_attribute("data-confirmation", "confirmed")
            await expect(row.locator(".chip.lifecycle")).to_have_text("Withdrawn")
            await lifecycle_filter(page, "facts", "")
            await expect(listed(page, new_key)).to_have_attribute("data-status", "active")
            await expect(listed(page, old_key)).to_have_attribute("data-status", "superseded")
            await expect(listed(page, wrong_key)).to_have_attribute("data-status", "withdrawn")
            assert w.problems == [], w.problems
    proof(
        "M16",
        "replacing a confirmed fact on the board asks for the person's word (the refusal is the "
        "tool's own, nothing is written and what was typed stays); with it the old fact is "
        "superseded but keeps its confirmation and the new one starts unconfirmed, and the "
        "lifecycle and the earlier confirmation are told apart in the list, the panel, the map and "
        "recall",
        asks_for_word
        and "needs that person's word" in no_person
        and same_words(no_person_words, no_person)
        and nothing_written
        and (ended["status"], ended["status_reason"]) == ("superseded", "Measured again")
        and (ended["confirmation"], ended["confirmed_by"]) == ("confirmed", MARIE)
        and (new["confirmation"], new["supersedes_ids"], new["node_ids"])
        == ("unconfirmed", [old["id"]], [story])
        and (events[-1]["action"], events[-1]["actor"]) == ("fact_superseded", ADA)
        and [(i["fact"]["id"], i["fact"]["confirmation"]) for i in past]
        == [(old["id"], "confirmed")],
        f"fact {old['id']} replaced by {new['id']}",
    )
    proof(
        "M16",
        "withdrawing a confirmed fact needs the person's word and an unconfirmed one only a "
        "reason; a withdrawn fact says why and is still shown as confirmed by whoever confirmed it",
        same_words(withdraw_words, reason_only)
        and still_active == "active"
        and (withdrawn["status"], withdrawn["status_reason"], withdrawn["confirmed_by"])
        == ("withdrawn", "It is wrong", MARIE)
        and (slipped["status"], slipped["confirmation"]) == ("withdrawn", "unconfirmed"),
        f'"{clip(withdraw_words, 70)}"',
    )


async def ending_a_decision() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Deciding {word}")
        story = await made(client, "story", f"Decided story {word}", epic)
        old = await make_decision(client, f"Old decision {word}", node_ids=[story])
        await confirm(client, "decision", old, MARIE)
        drop = await make_decision(client, f"Dropped decision {word}", node_ids=[story])
        old_key, drop_key = f"decision:{old['id']}", f"decision:{drop['id']}"
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=context&as=list&epic={epic}")
            before = await tally(client)
            await listed(page, old_key).locator("button.supersede-record").click()
            box = dialog(page)
            asks_for_word = await box.locator("details.said-by").get_attribute("open") is not None
            offered = await box.locator("#supersede-by").input_value()
            await box.locator("#supersede-title").fill(f"New decision {word}")
            await box.locator("#supersede-text").fill(f"What is decided now {word}")
            await box.locator("#supersede-reason").fill("We learned more")
            attempt = {
                "actor": "x", "decision_id": old["id"], "title": f"New decision {word}",
                "text": f"What is decided now {word}", "decided_by": ADA, "project_wide": False,
                "keep_links": True,
            }  # fmt: skip
            no_person = await refused(
                client, "supersede_decision", reason="We learned more", **attempt
            )
            await box.get_by_role("button", name="Replace with this decision").click()
            await expect(refusal_in(page)).to_contain_text("needs that person's word", timeout=SLOW)
            no_person_words = await refusal_in(page).inner_text()
            nothing_written = (
                await tally(client) == before
                and (await ok(client, "get_decision", decision_id=old["id"]))["status"] == "active"
            )
            await box.locator("#supersede-said-by").fill(MARIE)
            await box.locator("#supersede-quote").fill(f"Yes, change it {word}")
            await box.get_by_role("button", name="Replace with this decision").click()
            await expect(box).to_have_count(0, timeout=SLOW)
            ended = await ok(client, "get_decision", decision_id=old["id"])
            new = await ok(client, "get_decision", decision_id=ended["superseded_by_id"])
            new_key = f"decision:{new['id']}"
            events = (await ok(client, "decision_history", decision_id=old["id"]))["items"]
            await lifecycle_filter(page, "decisions", "superseded")
            gone = listed(page, old_key)
            await expect(gone).to_have_attribute("data-status", "superseded")
            await expect(gone).to_have_attribute("data-confirmation", "confirmed")
            await expect(gone.locator(".chip.lifecycle")).to_have_text("Replaced")
            await expect(gone.locator(".chip.confirmation")).to_contain_text(
                f"Confirmed by {MARIE}"
            )
            await gone.locator(".record-title").click()
            detail = panel(page).locator(f".record-detail[data-record='{old_key}']")
            await expect(detail.locator("[data-part=lifecycle] .chain")).to_contain_text(
                f"Replaced by #{new['id']}", timeout=SLOW
            )
            await w.go(f"#/graph?view=context&as=map&epic={epic}&ended=1")
            await expect(edge(page, "replaces", new_key, old_key)).to_have_count(1, timeout=SLOW)
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
            blank_words = await refusal_in(page).inner_text()
            await box.locator("#withdraw-reason").fill("No longer needed")
            await box.get_by_role("button", name="Withdraw", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            dropped = await ok(client, "get_decision", decision_id=drop["id"])
            await w.go(f"#/graph?view=context&as=list&epic={epic}&sel=node:{story}")
            await expect(panel(page).locator(".recall-section")).not_to_have_count(0, timeout=SLOW)
            await recall_matches(client, page, node_id=story)
            recalled = await ok(client, "recall", node_id=story)
            assert w.problems == [], w.problems
    proof(
        "M16",
        "replacing a confirmed decision asks for the person's word and offers the name signed in "
        "as who decided; once given, the old decision is superseded but still confirmed, the new "
        "one starts unconfirmed with the reason it carries, and withdrawing an unconfirmed one "
        "needs only a reason that is never blank; recall tells the past decisions apart",
        asks_for_word
        and offered == ADA
        and same_words(no_person_words, no_person)
        and nothing_written
        and (ended["status"], ended["confirmation"], ended["confirmed_by"])
        == ("superseded", "confirmed", MARIE)
        and (new["confirmation"], new["decided_by"], new["reason"])
        == ("unconfirmed", ADA, "We learned more")
        and (events[-1]["action"], events[-1]["actor"]) == ("decision_superseded", ADA)
        and same_words(blank_words, blank)
        and (dropped["status"], dropped["status_reason"]) == ("withdrawn", "No longer needed")
        and [i["decision"]["id"] for i in recalled["active_decisions"]["items"]] == [new["id"]]
        and sorted(i["decision"]["id"] for i in recalled["past_decisions"]["items"])
        == sorted([old["id"], drop["id"]]),
        f"decision {old['id']} replaced by {new['id']}, {drop['id']} withdrawn",
    )


# -- blanks, and nothing typed lost --------------------------------------------------------


async def blanks() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Blanks {word}")
        story = await made(client, "story", f"Blank story {word}", epic)
        fact = await make_fact(client, f"Kept fact {word}", node_ids=[story])
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=context&as=list&epic={epic}")
            before = await tally(client)
            no_source = await refused(
                client, "retain_fact", actor="x", text=f"No source {word}", source="",
                node_ids=[story],
            )  # fmt: skip
            await page.locator("#record-fact").click()
            box = dialog(page)
            await box.locator("#record-fact-text").fill(f"No source {word}")
            await chosen_picker(page, f"#{story}")
            await box.get_by_role("button", name="Record the fact").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            source_words = await refusal_in(page).inner_text()
            typed = await box.locator("#record-fact-text").input_value()
            await expect(box.locator(f".picker li[data-ref='node:{story}']")).to_be_visible()
            await box.get_by_role("button", name="Cancel").click()
            await page.locator("#record-decision").click()
            box = dialog(page)
            await box.locator("#record-decision-title").fill(f"Blank {word}")
            await box.locator("#record-decision-text").fill(f"Decided {word}")
            await box.locator("#record-decision-by").fill(MARIE)
            base = {
                "actor": "x", "title": f"Blank {word}", "text": f"Decided {word}",
                "project_wide": False,
            }  # fmt: skip
            no_reason = await refused(
                client, "record_decision", reason="", decided_by=MARIE, **base
            )
            await box.get_by_role("button", name="Record the decision").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            reason_words = await refusal_in(page).inner_text()
            await box.locator("#record-decision-reason").fill(f"A reason {word}")
            await box.locator("#record-decision-by").fill("")
            no_decider = await refused(
                client, "record_decision", reason=f"A reason {word}", decided_by="", **base
            )
            await box.get_by_role("button", name="Record the decision").click()
            await expect(refusal_in(page)).to_contain_text("decided_by", timeout=SLOW)
            decider_words = await refusal_in(page).inner_text()
            kept_reason = await box.locator("#record-decision-reason").input_value()
            await box.get_by_role("button", name="Cancel").click()
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
            assert w.problems == [], w.problems
        proof(
            "M16",
            "a fact with no source, a decision with no reason, one with no person who decided it "
            "and a confirmation with no person are each refused in the words an agent gets, what "
            "was typed stays, the dropdown goes back to what is true, and nothing at all is "
            "written, not even a link",
            same_words(source_words, no_source)
            and same_words(reason_words, no_reason)
            and same_words(decider_words, no_decider)
            and no_reason != no_decider
            and typed == f"No source {word}"
            and kept_reason == f"A reason {word}"
            and (await ok(client, "get_fact", fact_id=fact["id"]))["confirmation"] == "unconfirmed"
            and await tally(client) == before,
            f'"{clip(source_words, 70)}"',
        )


# -- a decision for the whole project: in its recall and briefings, and never another's ------


async def project_wide() -> None:
    word = unique_word()
    first, second = make_project(register()), make_project(register())
    wide_a, wide_b = f"Wide A {word}", f"Wide B {word}"
    async with connect(URL, first.key) as a, connect(URL, second.key) as b:
        trees = {}
        for client, project in ((a, first), (b, second)):
            epic = await made(client, "epic", f"Epic {word}")
            story = await made(client, "story", f"Story {word}", epic)
            await made(client, "task", f"Task {word}", story)
            trees[project.id] = (epic, story)
        epic_a, story_a = trees[first.id]
        epic_b, story_b = trees[second.id]
        await make_decision(b, wide_b, project_wide=True)
        async with browser_page(board_url(URL, first), token=first.key, name=ADA) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=context&as=list&epic={epic_a}")
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
            key = f"decision:{mine[0]['id']}"
            row = listed(page, key)
            await expect(row).to_have_attribute("data-project-wide", "true", timeout=SLOW)
            await expect(row.locator(".chip.project-wide")).to_contain_text("Project-wide")
            recalled = await ok(a, "recall", node_id=story_a)
            wide_items = recalled["project_wide_decisions"]["items"]
            brief = await ok(a, "next_work", actor="agent-a", within_node_id=epic_a, peek=True)
            wide_brief = brief["context"]["project_wide_decisions"]
            await w.go(f"#/graph?view=context&as=list&epic={epic_a}&sel=node:{story_a}")
            await expect(panel(page).locator(".recall-section")).not_to_have_count(0, timeout=SLOW)
            await recall_matches(a, page, node_id=story_a)
            entry = panel(page).locator(
                f"[data-section=project_wide_decisions] li.record[data-record='{key}']"
            )
            await expect(entry).to_have_attribute("data-confirmation", "unconfirmed")
            await w.go(f"#/graph?view=context&as=list&epic={epic_a}")
            await listed(page, key).locator(".confirmation-select").select_option("confirmed")
            box = dialog(page)
            await box.locator("#confirm-person").fill(MARIE)
            await box.get_by_role("button", name="Confirm", exact=True).click()
            await expect(box).to_have_count(0, timeout=SLOW)
            confirmed = (await ok(a, "recall", node_id=story_a))["project_wide_decisions"]["items"][
                0
            ]["decision"]
            brief = await ok(a, "next_work", actor="agent-a", within_node_id=epic_a, peek=True)
            [after] = brief["context"]["project_wide_decisions"]
            await w.go(f"#/graph?view=context&as=list&epic={epic_a}&sel=node:{story_a}")
            await expect(entry).to_have_attribute("data-confirmation", "confirmed", timeout=SLOW)
            assert w.problems == [], w.problems
        # The other project hears nothing of it: not in recall, the briefing, its list or its page.
        recalled_b = await ok(b, "recall", node_id=story_b)
        brief_b = await ok(b, "next_work", actor="agent-b", within_node_id=epic_b, peek=True)
        async with browser_page(
            board_url(URL, second), token=second.key, name=ADA, keep_bodies=True
        ) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=context&as=list&epic={epic_b}")
            await expect(page.locator("#ctx-decisions-count")).to_have_attribute(
                "data-total", "1", timeout=SLOW
            )
            shown = await page.locator("body").inner_text()
            clean = (
                wide_a not in shown
                and f"Everything is metric {word}" not in shown
                and all(wide_a not in body for _, body in w.bodies)
            )
            assert w.problems == [], w.problems
        proof(
            "M16",
            "a project-wide decision recorded on the board is in the recall of every item of its "
            "project and in its next_work briefing, unconfirmed until a person confirms it and "
            "then saying who; the other project's recall, briefing, list, page and responses "
            "never hold it",
            [i["decision"]["title"] for i in wide_items] == [wide_a]
            and wide_items[0]["decision"]["confirmation"] == "unconfirmed"
            and recalled["active_decisions"]["items"] == []
            and [(d["title"], d["confirmation"]) for d in wide_brief] == [(wide_a, "unconfirmed")]
            and (confirmed["confirmation"], confirmed["confirmed_by"]) == ("confirmed", MARIE)
            and (after["confirmation"], after["confirmed_by"]) == ("confirmed", MARIE)
            and [i["decision"]["title"] for i in recalled_b["project_wide_decisions"]["items"]]
            == [wide_b]
            and [d["title"] for d in brief_b["context"]["project_wide_decisions"]] == [wide_b]
            and clean,
            "its project holds it, the other holds only its own",
        )
