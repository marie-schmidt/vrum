"""M43 on the board: Where could it go? on a story's or task's page, and the proposals to move work.

Driven in a real headless browser and compared with what a real MCP client reads (see ./check.sh),
on the check stack's scripted embedding service, whose words stand for concepts (the epics of the
test carry those words, as in test_placement_integration.py). The button asks the server
`suggest_parent` and shows its answer in place, best first, each with its score, the items that are
the reason and Move here (for a person who can write); a proposal an agent made is ONE row on the
Needs a person page with Accept and Reject (a reason is needed), and accepting makes the moves as
the person, says what it left where it was, and makes a new epic only then. A viewer sees all of it
and no button. Every test makes an account and a project of its own."""

import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import ViewportSize, expect

from tests.board_helpers import SLOW, browser_page, nothing_open, on_page, open_item, part, plain
from tests.helpers import connect, embeddings_stopped, ok, refused, reindexed
from tests.test_board_needs_integration import (
    DECIDING,
    EMPTY,
    no_overflow,
    open_routine,
    row,
    sees_the_list,
)
from tests.test_checks_integration import as_person, words_of
from tests.test_placement_integration import a_sorted_project, node
from tests.world import Person, Project, join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NARROW: ViewportSize = {"width": 390, "height": 900}
LONG_REASON = "These stories are all about taking a payment from a rider. " + " ".join(
    f"Reason {n} concerns the invoice and the charge." for n in range(1, 9)
)


def person_call(project: Project, who: Person, tool: str, **arguments: Any) -> Any:
    return as_person(who, project, tool, **arguments)


def code(ref: str) -> str:
    kind, number = ref.split(":")
    return f"{kind[0].upper()}-{number}"


async def parent_of(client: Client, ref: str) -> str | None:
    found = await ok(client, "get_node", node=ref)
    return None if found["parent"] is None else str(found["parent"])


# -- Where could it go? ----------------------------------------------


async def test_the_button_asks_only_when_pressed_and_lists_the_servers_suggestions() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        page = w.page
        await open_item(w, refs["loose_pay"], "board")
        section = part(page, "suggest")
        button = section.locator("#item-suggest-parent")
        await expect(button).to_have_text("Where could it go?", timeout=SLOW)
        await expect(button).to_have_attribute("aria-expanded", "false")
        # Nothing is asked on its own, and nothing is drawn until it is.
        await expect(section.locator(".suggestion")).to_have_count(0)
        asked = [r for r in w.requests if r.endswith("/tool/suggest_parent")]
        assert asked == []
        # An epic is not placed under anything, so it has no such button.
        await open_item(w, refs["billing"], "board")
        await expect(part(page, "suggest")).to_have_count(0)
        await open_item(w, refs["loose_pay"], "board")

        await button.click()
        await expect(button).to_have_attribute("aria-expanded", "true")
        wanted = await ok(agent, "suggest_parent", node=refs["loose_pay"])
        assert wanted["sure"] is True and wanted["current_parent"] == refs["inbox"]
        suggestions = section.locator(".suggestion")
        await expect(suggestions).to_have_count(len(wanted["suggestions"]), timeout=SLOW)
        shown = await suggestions.evaluate_all(
            "els => els.map(e => [e.dataset.ref, e.dataset.score])"
        )
        assert [(ref, float(score)) for ref, score in shown] == [
            (s["ref"], s["score"]) for s in wanted["suggestions"]
        ]
        top = wanted["suggestions"][0]
        assert top["ref"] == refs["billing"]
        # Best first: the code and title as a link, the score with two decimals and a meter.
        first = suggestions.first
        await expect(first.locator(".suggestion-head a.item-link")).to_contain_text("Billing")
        await expect(first.locator(".suggestion-head .item-code")).to_have_text(code(top["ref"]))
        await expect(first.locator(".score strong")).to_have_text(f"{top['score']:.2f}")
        await expect(first.locator(".meter")).to_have_attribute(
            "aria-valuenow", re.compile(r"^0\.\d+")
        )
        # Sure is on the first only, and its hover card says it is still a guess.
        await expect(section.locator(".sure-chip")).to_have_count(1)
        await expect(first.locator(".sure-chip")).to_have_text("Sure")
        await first.locator(".sure-chip").hover()
        await expect(page.locator(".hovercard:visible")).to_contain_text("Still a guess")
        await page.keyboard.press("Escape")
        # The reason: the items it most resembles, with how alike, each a link.
        reasons = first.locator(".suggestion-because a.item-link")
        await expect(reasons).to_have_count(len(top["because"]))
        text = (await first.locator(".suggestion-because").text_content()) or ""
        for one in top["because"]:
            assert code(one["ref"]) in text and f"({one['similarity']:.2f})" in text, text
        assert top["holds"] and f"Holds {top['holds']} items already." in (
            (await first.locator(".suggestion-holds").text_content()) or ""
        )
        # Where it is now: its parent, the Inbox, which is never a suggestion.
        await expect(section.locator(".suggest-now")).to_contain_text("Inbox")
        await expect(section.locator(f".suggestion[data-ref='{refs['inbox']}']")).to_have_count(0)
        await expect(section.locator(".suggestion-move")).to_have_count(len(wanted["suggestions"]))
        asked_now = [r for r in w.requests if r.endswith("/tool/suggest_parent")]
        assert len(asked_now) == 1
        await no_overflow(page)

        # Pressing it again closes it, and leaving the page forgets it.
        await button.click()
        await expect(section.locator(".suggestion")).to_have_count(0)
        await expect(button).to_have_attribute("aria-expanded", "false")
        await button.click()
        await expect(section.locator(".suggestion").first).to_be_visible(timeout=SLOW)
        await open_item(w, refs["loose_mail"], "board")
        await expect(part(page, "suggest").locator(".suggestion")).to_have_count(0)
        await expect(part(page, "suggest").locator("#item-suggest-parent")).to_have_attribute(
            "aria-expanded", "false"
        )
        await page.set_viewport_size(NARROW)
        await part(page, "suggest").locator("#item-suggest-parent").click()
        await expect(part(page, "suggest").locator(".suggestion").first).to_be_visible(timeout=SLOW)
        await no_overflow(page)
        assert w.problems == []


async def test_move_here_moves_the_item_as_the_person_and_the_page_follows() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        page = w.page
        await open_item(w, refs["loose_mail"], "board")
        section = part(page, "suggest")
        await section.locator("#item-suggest-parent").click()
        best = section.locator(f".suggestion[data-ref='{refs['mail']}']")
        await expect(best).to_be_visible(timeout=SLOW)
        await expect(best.locator("button.suggestion-move")).to_have_text("Move here")
        await best.locator("button.suggestion-move").click()
        await expect(section.locator(".suggestion")).to_have_count(0, timeout=SLOW)
        assert await parent_of(agent, refs["loose_mail"]) == refs["mail"]
        history = await ok(agent, "node_history", node=refs["loose_mail"])
        moves = [e for e in history["items"] if e["action"] == "move"]
        assert moves[-1]["actor"] == "Priya Raman" and moves[-1]["actor_kind"] == "person"
        # The page follows: the path above it names the epic it is under now.
        await expect(page.locator("nav.crumbs")).to_contain_text("Notifications", timeout=SLOW)
        # Asking again, it is where the server says it belongs: it ranks first now.
        await section.locator("#item-suggest-parent").click()
        await expect(section.locator(".suggest-now")).to_contain_text("Notifications", timeout=SLOW)
        await expect(section.locator(".suggest-now")).to_contain_text("ranks 1st of the choices")
        assert w.problems == []


async def test_a_viewer_sees_the_suggestions_and_no_move_button_and_the_server_refuses_a_move() -> (
    None
):
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        async with browser_page(project.address, token=None, person=viewer, name=None) as w:
            page = w.page
            await open_item(w, refs["loose_pay"], "board")
            section = part(page, "suggest")
            await section.locator("#item-suggest-parent").click()
            await expect(section.locator(".suggestion").first).to_be_visible(timeout=SLOW)
            await expect(section.locator(".suggestion-move")).to_have_count(0)
            await expect(section.locator("button")).to_have_count(1)
            assert w.problems == []
        found = await ok(agent, "get_node", node=refs["loose_pay"])
        refusal = person_call(
            project,
            viewer,
            "move_node",
            node=refs["loose_pay"],
            expected_version=found["version"],
            new_parent=refs["billing"],
        )
        assert refusal.status in (400, 403), refusal.body
        assert await parent_of(agent, refs["loose_pay"]) == refs["inbox"]


async def test_a_server_that_cannot_suggest_says_why_in_its_own_words_in_the_section() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        page = w.page
        with embeddings_stopped():
            # Work that was just written is not in the index, and the service that would have
            # to be asked is not there.
            fresh = await node(agent, "story", "Pay the invoice again", refs["inbox"])
            await open_item(w, fresh, "board")
            section = part(page, "suggest")
            before = words_of(await refused(agent, "suggest_parent", node=fresh))
            await section.locator("#item-suggest-parent").click()
            problem = section.locator(".suggest-problem")
            await expect(problem).to_contain_text("could not be reached", timeout=SLOW)
            shown = (await problem.text_content()) or ""
            after = words_of(await refused(agent, "suggest_parent", node=fresh))
            # The sentence changes once the job that keeps the index has noticed the outage, so
            # the page says what the server said when it was asked, which is one of the two.
            try:
                plain(shown, before)
            except AssertionError:
                plain(shown, after)
        await expect(section.locator(".suggestion")).to_have_count(0)
        assert "Error executing tool" not in shown
        # The service is back: the next reindex clears what the stopped one left.
        await reindexed(agent)
        await section.locator("#item-suggest-parent").click()
        await section.locator("#item-suggest-parent").click()
        await expect(section.locator(".suggestion").first).to_be_visible(timeout=SLOW)
        assert w.problems == []


# -- proposals to move work ----------------------------------------------


async def propose(agent: Client, items: list[str], **where: Any) -> dict[str, Any]:
    made: dict[str, Any] = await ok(
        agent, "propose_placement", items=items, reason=where.pop("reason", LONG_REASON), **where
    )
    return made


async def test_a_proposal_is_one_row_and_accepting_it_moves_the_items_as_the_person() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        loose = [refs["loose_pay"], refs["loose_mail"], refs["loose_login"]]
        move = await propose(agent, loose, parent=refs["billing"])
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        wanted = await sees_the_list(page, agent)
        assert wanted["groups"][0]["title"] == "Suggested moves"
        await open_routine(page)
        await expect(page.locator("#sidebar-needs-count")).to_have_text("1")

        proposal = row(page, "placement", loose[0])
        await expect(proposal.locator(".change-kind-word")).to_have_count(0)
        await expect(proposal.locator(".change-kind .item-code")).to_have_text(
            f"Proposal {move['id']}"
        )
        # The title has no code of its own, and the item codes inside it are links.
        await expect(proposal.locator(".change-title")).to_have_text(
            f"Move {code(loose[0])}, {code(loose[1])} and {code(loose[2])} "
            f"under {code(refs['billing'])} Billing"
        )
        await expect(proposal.locator(".change-title a.item-link")).to_have_count(4)
        (served,) = [i for g in wanted["groups"] for i in g["items"] if i["kind"] == "placement"]
        assert served["reason"] != LONG_REASON and len(LONG_REASON) > 200
        await expect(proposal.locator("li[data-fact=by] .actor-name")).to_have_text("tests")
        # The work added under the new parent, each from where it is now, as `list_placements`
        # says.
        asked = (await ok(agent, "list_placements", status="proposed"))["items"][0]
        added = proposal.locator(".diff-line[data-sign='+'][data-ref]")
        await expect(added).to_have_count(3)
        assert await added.evaluate_all("els => els.map(e => e.dataset.ref)") == [
            one["ref"] for one in asked["items"]
        ]
        for line, ref in zip(await added.all(), loose, strict=True):
            await expect(line.locator(".item-link .item-code").first).to_have_text(code(ref))
            await expect(line.locator(".diff-from")).to_contain_text("Inbox")
        await expect(proposal.locator(DECIDING)).to_have_text(["Approve", "Reject…"])
        await no_overflow(page)
        # The proposer's reason in full, and the same work removed from the old place, are on
        # the review page.
        await proposal.locator("a.change-open").click()
        whole = page.locator("#review-page")
        assert await whole.locator(".needs-move-reason").text_content() == LONG_REASON
        removed = whole.locator(".diff-line[data-sign='-'][data-ref]")
        assert await removed.evaluate_all("els => els.map(e => e.dataset.ref)") == [
            one["ref"] for one in asked["items"]
        ]
        await page.locator("#review-bar button.verdict[data-verb=approve]").focus()
        await expect(page.locator(".hovercard:visible")).to_contain_text(
            f"Moves 3 stories under {code(refs['billing'])}"
        )
        await page.locator("#review-back").click()
        await on_page(page, "needs")
        await open_routine(page)

        # Nothing has moved. One of them moves elsewhere meanwhile, so it is left where it is.
        found = await ok(agent, "get_node", node=loose[2])
        await ok(
            agent,
            "move_node",
            node=loose[2],
            new_parent=refs["access"],
            expected_version=found["version"],
        )
        await proposal.locator("button.needs-yes").click()
        await expect(proposal).to_have_count(0, timeout=SLOW)
        decided = (await ok(agent, "list_placements", status="accepted"))["items"][0]
        assert decided["decided_by"] == "Priya Raman" and decided["applied"] == loose[:2]
        assert [s["ref"] for s in decided["skipped"]] == [loose[2]]
        # What was left is said once, in the server's words, and kept until dismissed.
        said = page.locator("#needs-said .needs-said")
        await expect(said).to_contain_text(
            f"{code(loose[2])} left where it is: it was moved to {code(refs['access'])} since"
        )
        assert decided["skipped"][0]["why"] == f"it was moved to {code(refs['access'])} since"
        for ref in loose[:2]:
            assert await parent_of(agent, ref) == refs["billing"]
            history = await ok(agent, "node_history", node=ref)
            last = [e for e in history["items"] if e["action"] == "move"][-1]
            assert (last["actor"], last["actor_kind"]) == ("Priya Raman", "person")
        assert await parent_of(agent, loose[2]) == refs["access"]
        await said.locator("button.notice-close").click()
        await expect(said).to_have_count(0)
        await expect(page.locator("#needs-body")).to_have_text(EMPTY, timeout=SLOW)
        assert w.problems == []


async def test_a_proposal_to_group_stories_under_a_new_epic_makes_it_only_when_accepted() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        items = [refs["loose_pay"], refs["loose_mail"], refs["loose_login"], refs["loose_story"]]
        await propose(
            agent,
            items,
            new_epic={"title": "Receipts", "body": "Everything about receipts."},
            reason="They are all about receipts.",
        )
        epics_before = (await ok(agent, "list_nodes", kind="epic"))["total"]
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_routine(page)
        proposal = row(page, "placement", items[0])
        await expect(proposal.locator(".change-title")).to_have_text(
            f"Move {code(items[0])}, {code(items[1])} and 2 more under a new epic, Receipts"
        )
        # A new epic is an added line, and the work under it.
        await expect(
            proposal.locator(".diff-line[data-new-epic] .diff-new-epic").first
        ).to_have_text("Receipts")
        await expect(proposal.locator(".diff-line[data-sign='+'][data-ref]")).to_have_count(4)
        # Its details are on the review page, and what approving does is the verb's hover.
        await proposal.locator("a.change-open").click()
        await expect(
            page.locator("#review-diff .diff-line[data-part='body'][data-new-epic] .diff-code")
        ).to_have_text("added: Everything about receipts.", timeout=SLOW)
        await page.locator("#review-bar button.verdict[data-verb=approve]").focus()
        await expect(page.locator(".hovercard:visible")).to_contain_text(
            "Makes a new epic, Receipts, and moves 4 stories under it"
        )
        await page.locator("#review-back").click()
        await on_page(page, "needs")
        await open_routine(page)
        # Nothing yet: no epic exists.
        assert (await ok(agent, "list_nodes", kind="epic"))["total"] == epics_before
        await proposal.locator("button.needs-yes").click()
        await expect(proposal).to_have_count(0, timeout=SLOW)
        assert (await ok(agent, "list_nodes", kind="epic"))["total"] == epics_before + 1
        made = (await ok(agent, "list_placements", status="accepted"))["items"][0]["epic"]
        assert made and await parent_of(agent, items[0]) == made
        # Nothing was left behind, so nothing is said.
        await expect(page.locator("#needs-said .needs-said")).to_have_count(0)
        assert w.problems == []


async def test_rejecting_a_proposal_needs_a_reason_and_moves_nothing() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        move = await propose(agent, [refs["loose_pay"]], parent=refs["billing"], reason="Billing.")
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await open_routine(page)
        proposal = row(page, "placement", refs["loose_pay"])
        await expect(proposal).to_be_visible(timeout=SLOW)
        await proposal.locator("button.needs-no").click()
        dialog = page.locator("dialog#reject-dialog[open]")
        await expect(dialog).to_be_visible()
        await nothing_open(page)
        await expect(dialog.locator("h2")).to_have_text(f"Reject Proposal {move['id']}")
        await expect(dialog.locator("#reject-reason")).to_have_value("")
        await expect(dialog.locator(".reject-words")).to_have_count(0)
        await dialog.locator("button[type=submit]").click()
        problem = dialog.locator(".problem")
        await expect(problem).to_contain_text("needs a reason", timeout=SLOW)
        blank = person_call(
            project, owner, "decide_placement", placement=move["id"], accepted=False
        )
        plain((await problem.text_content()) or "", blank.body["message"])
        assert (await ok(agent, "list_placements", status="proposed"))["total"] == 1
        await dialog.locator("#reject-reason").fill("Not now")
        await dialog.locator("button[type=submit]").click()
        await expect(page.locator("dialog#reject-dialog")).to_have_count(0, timeout=SLOW)
        await expect(proposal).to_have_count(0, timeout=SLOW)
        gone = (await ok(agent, "list_placements", status="rejected"))["items"][0]
        assert (gone["decided_by"], gone["decision_reason"]) == (owner.name, "Not now")
        assert await parent_of(agent, refs["loose_pay"]) == refs["inbox"]
        assert w.problems == []


async def test_a_viewer_sees_the_proposal_and_no_button_and_an_agent_cannot_decide_it() -> None:
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        move = await propose(agent, [refs["loose_pay"]], parent=refs["billing"])
        async with browser_page(project.address, token=None, person=viewer, name=None) as w:
            page = w.page
            await w.open("#/needs")
            await on_page(page, "needs")
            await sees_the_list(page, agent)
            await open_routine(page)
            await expect(row(page, "placement", refs["loose_pay"])).to_be_visible()
            await expect(page.locator(f"#needs-body li.needs-row {DECIDING}")).to_have_count(0)
            # What it would do is all there to read, for a viewer too, on its review page.
            await row(page, "placement", refs["loose_pay"]).locator("a.change-open").click()
            await expect(page.locator("#review-page .diff-line[data-sign='-']")).to_have_count(
                1, timeout=SLOW
            )
            await expect(page.locator("#review-bar, button.verdict")).to_have_count(0)
            assert w.problems == []
        refusal = person_call(
            project, viewer, "decide_placement", placement=move["id"], accepted=True
        )
        assert refusal.status in (400, 403), refusal.body
        assert (await ok(agent, "list_placements", status="proposed"))["total"] == 1
        # The proposal is in the feed and, once decided, the decision is too.
        assert (
            person_call(
                project,
                owner,
                "decide_placement",
                placement=move["id"],
                accepted=False,
                reason="No",
            ).status
            == 200
        )
        async with browser_page(project.address, token=project.key, name=None) as w:
            page = w.page
            await w.open("#/activity?act_kind=moves")
            await expect(page.locator("#feed .event").first).to_be_visible(timeout=SLOW)
            joined = "\n".join(await page.locator("#feed .event .event-words").all_inner_texts())
            assert re.search(
                rf"proposed Proposal {move['id']}: move story S-\d+ “Pay the invoice” under "
                rf"epic E-\d+ “Billing”: ",
                joined,
            ), joined
            assert f"rejected Proposal {move['id']}: No" in joined, joined
            assert w.problems == []
