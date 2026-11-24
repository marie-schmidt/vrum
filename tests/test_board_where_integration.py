"""M41 on the board: where the work is, which run is doing it, and a key's last day.

Driven in a real headless browser and compared with what a real MCP client reads (see ./check.sh).
A holder may say where its work is (repository, branch, commit, pull request) and which run it is
(a run label and a client name). The board shows those words in the hover card of whoever holds a
card, on the item's page (one quiet "Where" entry, and with each handoff note), in the history, on
Activity and in the Agent view, and draws nothing at all when nothing was said. An address is a link
only when it is plainly an https address (a pull request only when its path is exactly
/OWNER/REPO/pull/NUMBER); anything else is the text it is. A run label is an audit label and is
never drawn as a person. On the Users page the owner gives a key a last day when it is made, changes
and clears it later, and the chip says it, and says it in danger once the server says the key
expired. Every test makes an account and a project of its own."""

import re
import time
from datetime import date
from typing import Any

import anyio
import pytest
from playwright.async_api import Locator, Page, ViewportSize, expect

from tests.board_helpers import SLOW, browser_page, card, go, nothing_open, open_item, part
from tests.board_m14 import show_tab
from tests.helpers import connect, make_tree, number_of, ok, unique_word
from tests.test_accounts_integration import messages
from tests.test_board_code_mode_integration import key_named
from tests.test_board_verifier_integration import sideways
from tests.test_where_the_work_is_integration import end_yesterday, key_row, keys_path, tomorrow
from tests.world import join_project, make_key, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

REPOSITORY = "https://github.com/example-org/bike-share"
BRANCH = "feature/unlock-by-number"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PULL_REQUEST = "https://github.com/example-org/bike-share/pull/12"
RUN = "run-2026-11-22-a"
CLIENT = "claude-code"
NEWER = "89abcdef0123456789abcdef0123456789abcdef"
SAID = {
    "repository": REPOSITORY,
    "branch": BRANCH,
    "commit": COMMIT,
    "pull_request": PULL_REQUEST,
    "run_label": RUN,
    "client_name": CLIENT,
}
NARROW: ViewportSize = {"width": 390, "height": 900}
DANGER = re.compile(r"\bdanger\b")


async def another_task(client: Any, story: dict[str, Any], title: str) -> dict[str, Any]:
    """A task under the same story as the one make_tree made."""
    return dict(await ok(client, "create_node", kind="task", title=title, parent=story["ref"]))


def words(day: str) -> str:
    """A day as the chip writes it: 31 Dec 2026."""
    one = date.fromisoformat(day)
    return f"{one.day} {one.strftime('%b')} {one.year}"


def squeezed(text: str) -> str:
    return " ".join(text.split())


async def no_overflow(page: Page) -> None:
    found = await sideways(page)
    assert found["doc"] <= found["width"] + 1 and found["offenders"] == [], found


# -- the holder on a card ------------------------------------------------------------------------


async def test_a_held_cards_hover_card_lists_what_was_said_and_a_bare_claim_is_as_before() -> None:
    word = unique_word()
    project = make_project(register(name=f"Owner {word}"))
    worker, quiet = f"worker-{word}", f"quiet-{word}"
    async with (
        connect(project.mcp_url, make_key(project, worker)) as doer,
        connect(project.mcp_url, make_key(project, quiet)) as other,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic, story, said_task = await make_tree(doer)
        silent_task = await another_task(doer, story, f"Silent {word}")
        await ok(doer, "claim_node", node=said_task["ref"], **SAID)
        await ok(other, "claim_node", node=silent_task["ref"])
        page = w.page
        await w.open(f"#/board?epic={number_of(epic['ref'])}")

        named = card(page, said_task["ref"]).locator(".chip.held .actor.has-card")
        await expect(named).to_have_attribute("data-actor-name", worker, timeout=SLOW)
        # The chip is what it was: the holder's name, with until when. Nothing is drawn on the card.
        held = card(page, said_task["ref"]).locator(".chip.held")
        assert (await held.get_attribute("aria-label") or "").startswith(f"Held by {worker} until ")
        await expect(card(page, said_task["ref"]).locator(".where")).to_have_count(0)

        await named.hover()
        shown = page.locator(".hovercard:visible .actor-card")
        await expect(shown).to_contain_text("Holding now (1)", timeout=SLOW)
        where = shown.locator(f"li[data-node='{said_task['ref']}'] .where")
        await expect(where).to_contain_text(BRANCH)
        await expect(where.locator(".where-run")).to_have_text(f"Run: {RUN} ({CLIENT})")
        # A commit is its first seven characters, with the whole hash in its tooltip.
        short = where.locator(".where-commit .where-text")
        await expect(short).to_have_text("0123456")
        await expect(short).to_have_attribute("title", f"Commit {COMMIT}")
        pull = where.locator(".where-pull-request a")
        await expect(pull).to_have_text("Pull request 12")
        await expect(pull).to_have_attribute("href", PULL_REQUEST)
        await expect(pull).to_have_attribute("target", "_blank")
        rel = await pull.get_attribute("rel") or ""
        assert "noopener" in rel and "noreferrer" in rel, rel
        await expect(where.locator(".where-repository a")).to_have_attribute("href", REPOSITORY)
        # The holder is still the key's name: the run label is never drawn as a person or an agent.
        await expect(shown.locator(".actor-card-head")).to_contain_text(worker)
        labels = f"[data-actor-name='{RUN}'], [data-actor-name='{CLIENT}']"
        await expect(page.locator(labels)).to_have_count(0)
        await page.keyboard.press("Escape")
        await page.mouse.move(5, 5)

        # A claim that said nothing looks exactly as before: no line of any kind in the card.
        quiet_name = card(page, silent_task["ref"]).locator(".chip.held .actor.has-card")
        await expect(quiet_name).to_have_attribute("data-actor-name", quiet, timeout=SLOW)
        await quiet_name.hover()
        bare = page.locator(".hovercard:visible .actor-card")
        await expect(bare).to_contain_text("Holding now (1)", timeout=SLOW)
        await expect(bare.locator(f"li[data-node='{silent_task['ref']}']")).to_have_count(1)
        await expect(bare.locator(".where")).to_have_count(0)
        await page.keyboard.press("Escape")
        await page.mouse.move(5, 5)

        # The keyboard reaches it too: the name takes the focus and its card says the same.
        await named.focus()
        await expect(page.locator(".hovercard:visible .actor-card .where-branch")).to_contain_text(
            BRANCH, timeout=SLOW
        )
        await page.keyboard.press("Escape")

        # One read of who is working answers every card: never one call for each.
        reads = [r for r in w.requests if r.endswith("/tool/who_is_working")]
        assert 1 <= len(reads) <= 2, reads
        assert w.problems == [], w.problems


async def commit_in_card(page: Page, name: Locator) -> str:
    """Open the holder's card on the Board and read the commit it says, then close it."""
    await name.hover()
    commit = page.locator(".hovercard:visible .where-commit .where-text")
    text = await commit.inner_text(timeout=SLOW)
    await page.keyboard.press("Escape")
    await page.mouse.move(5, 5)
    return text


async def test_the_hover_card_hears_of_a_newer_commit_without_a_reload() -> None:
    word = unique_word()
    project = make_project(register(name=f"Owner {word}"))
    worker = f"worker-{word}"
    async with (
        connect(project.mcp_url, make_key(project, worker)) as doer,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic, _, task = await make_tree(doer)
        await ok(doer, "claim_node", node=task["ref"], branch=BRANCH, commit=COMMIT)
        page = w.page
        await w.open(f"#/board?epic={number_of(epic['ref'])}")
        named = card(page, task["ref"]).locator(".chip.held .actor.has-card")
        await expect(named).to_have_attribute("data-actor-name", worker, timeout=SLOW)
        assert await commit_in_card(page, named) == "0123456"

        # The board hears of the renewal at its next look at the news; the card then reads again.
        await ok(doer, "renew_claim", node=task["ref"], commit=NEWER)
        started = time.monotonic()
        seen = ""
        while seen != "89abcde" and time.monotonic() - started < 20:
            await anyio.sleep(0.5)
            seen = await commit_in_card(page, named)
        assert seen == "89abcde", seen
        assert w.problems == [], w.problems


# -- the page of an item ----------------------------------------------------------------------


async def test_the_item_page_says_where_the_work_is_and_follows_the_news() -> None:
    word = unique_word()
    project = make_project(register(name=f"Owner {word}"))
    worker, quiet = f"worker-{word}", f"quiet-{word}"
    async with (
        connect(project.mcp_url, make_key(project, worker)) as doer,
        connect(project.mcp_url, make_key(project, quiet)) as other,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, story, task = await make_tree(doer)
        bare = await another_task(doer, story, f"Bare {word}")
        await ok(
            doer,
            "claim_node",
            node=task["ref"],
            branch=BRANCH,
            commit=COMMIT,
            run_label=RUN,
            client_name=CLIENT,
        )
        await ok(other, "claim_node", node=bare["ref"])
        page = w.page
        await open_item(w, task["ref"])

        strip = page.locator("#item-page .state-strip")
        labels = await strip.locator("dt").evaluate_all("els => els.map(e => e.textContent.trim())")
        assert labels.index("Where") == labels.index("Held by") + 1, labels
        where = strip.locator("dd.where-item")
        await expect(where).to_have_count(1)
        await expect(where.locator(".where-branch")).to_contain_text(BRANCH)
        await expect(where.locator(".where-run")).to_have_text(f"Run: {RUN} ({CLIENT})")
        await expect(where.locator(".where-commit .where-text")).to_have_text("0123456")
        await expect(where.locator(".where-pull-request")).to_have_count(0)
        # The holder is still the key's name.
        await expect(strip.locator("dd.claim .actor")).to_have_attribute("data-actor-name", worker)

        # News: the agent renews with a newer commit and a pull request. The open page follows.
        await ok(doer, "renew_claim", node=task["ref"], commit=NEWER, pull_request=PULL_REQUEST)
        await expect(where.locator(".where-commit .where-text")).to_have_text(
            "89abcde", timeout=SLOW
        )
        pull = where.locator(".where-pull-request a")
        await expect(pull).to_have_attribute("href", PULL_REQUEST)
        # The keyboard reaches the link.
        await pull.focus()
        await expect(pull).to_be_focused()

        # Finishing it leaves a handoff note, which carries what was said, and the item keeps it.
        await ok(
            doer,
            "finish_work",
            node=task["ref"],
            outcome="blocked",
            note="Built. Ready for a verifier.",
            pull_request=PULL_REQUEST,
        )
        handoff = part(page, "handoff").locator("li.handoff-item")
        await expect(handoff).to_have_count(1, timeout=SLOW)
        await expect(handoff.locator(".where-pull-request a")).to_have_attribute(
            "href", PULL_REQUEST
        )
        await expect(handoff.locator(".where-run")).to_have_text(f"Run: {RUN} ({CLIENT})")
        await expect(handoff).to_contain_text("Built. Ready for a verifier.")
        # With nobody holding it any more, the entry is what the last handoff said.
        await expect(strip.locator("dd.claim")).to_have_count(0)
        await expect(strip.locator("dd.where-item .where-pull-request a")).to_have_count(1)

        # The history says it plainly, in the sentence, and the actor is the key's name.
        history = page.locator("#item-page ul.history")
        claimed = squeezed(
            await history.locator("li[data-action=claimed] .event-words").inner_text()
        )
        assert f"on branch {BRANCH}" in claimed and "at commit 0123456" in claimed, claimed
        assert f"in run {RUN} ({CLIENT})" in claimed, claimed
        renewed = squeezed(
            await history.locator("li[data-action=claim_renewed] .event-words").inner_text()
        )
        assert "at commit 89abcde" in renewed and "in pull request 12" in renewed, renewed
        ended = squeezed(await history.locator("li[data-action=handoff] .event-words").inner_text())
        assert "left this handoff note" in ended and "in pull request 12" in ended, ended
        assert f"on branch {BRANCH}" in ended, ended
        actor = history.locator("li[data-action=claimed] .history-what strong .actor")
        await expect(actor).to_have_attribute("data-actor-name", worker)
        await expect(page.locator(f"#item-page [data-actor-name='{RUN}']")).to_have_count(0)
        # The pull request in a sentence is a link.
        await expect(
            history.locator("li[data-action=claim_renewed] .event-words a.where-link")
        ).to_have_attribute("href", PULL_REQUEST)

        # What nothing was said about shows nothing: no Where in the strip, no line anywhere, and
        # the history reads as it always did.
        await open_item(w, bare["ref"])
        labels = await page.locator("#item-page .state-strip dt").evaluate_all(
            "els => els.map(e => e.textContent.trim())"
        )
        assert "Where" not in labels, labels
        await expect(page.locator("#item-page .where")).to_have_count(0)
        plain = squeezed(
            await page.locator(
                "#item-page ul.history li[data-action=claimed] .event-words"
            ).inner_text()
        )
        assert plain.startswith("took task ") and " until " in plain, plain
        for gone in ("branch", "commit", "pull request", " run "):
            assert gone not in plain, plain
        assert w.problems == [], w.problems


async def test_a_long_branch_and_label_do_not_widen_the_page_on_a_phone() -> None:
    word = unique_word()
    project = make_project(register(name=f"Owner {word}"))
    long_branch = "feature/" + "x" * 150
    long_label = "run-" + "y" * 70
    async with (
        connect(project.mcp_url, make_key(project, f"worker-{word}")) as doer,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, _, task = await make_tree(doer)
        await ok(
            doer,
            "claim_node",
            node=task["ref"],
            branch=long_branch,
            run_label=long_label,
            client_name=CLIENT,
            commit=COMMIT,
            pull_request=PULL_REQUEST,
            repository=REPOSITORY,
        )
        page = w.page
        await page.set_viewport_size(NARROW)
        await open_item(w, task["ref"])
        await expect(page.locator("#item-page dd.where-item")).to_contain_text(long_branch)
        await no_overflow(page)
        await w.open("#/activity?tab=working")
        await expect(page.locator("#agents li.claim .where")).to_contain_text(
            long_label, timeout=SLOW
        )
        await no_overflow(page)
        assert w.problems == [], w.problems


# -- an address is a link only when it plainly is one --------------------------------------------

LINKS = """async (cases) => {
    const found = await import('/board/whereabouts.js');
    return cases.map(([field, value]) => {
        const line = found.whereLine({ [field]: value });
        return {
            value,
            text: line ? line.textContent : null,
            links: line ? [...line.querySelectorAll('a')].map((a) => ({
                href: a.getAttribute('href'), target: a.target, rel: a.rel,
                text: a.textContent })) : [],
            code: line ? line.querySelectorAll('code, pre, img, script').length : 0,
        };
    });
}"""

NOTHING = """async () => {
    const found = await import('/board/whereabouts.js');
    return [found.whereLine({ branch: '  ' }), found.whereBlock({}), found.whereBlock(null),
            found.runLine({ run_label: '' }), found.whereWords(null).length,
            found.whereWords({ branch: '' }).length];
}"""


async def test_only_a_plain_https_address_is_made_a_link() -> None:
    word = unique_word()
    project = make_project(register(name=f"Owner {word}"))
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await w.open("#/board")
        not_links = [
            ("pull_request", "javascript:alert(1)"),
            ("pull_request", "data:text/html,<b>x</b>"),
            ("pull_request", "http://github.com/o/r/pull/1"),
            ("pull_request", "https://github.com/o/r/pull/1?tab=files"),
            ("pull_request", "https://github.com/o/r/pull/1#diff"),
            ("pull_request", "https://user:pw@github.com/o/r/pull/1"),
            ("pull_request", "https://github.com:8443/o/r/pull/1"),
            ("pull_request", "https://github.com/o/r/issues/1"),
            ("pull_request", "https://github.com/o/r/pull/0"),
            ("pull_request", "https://github.com/o/r/pull/1/files"),
            ("pull_request", "https://github.com/o/r/pull/1 and more"),
            ("pull_request", "https://github.com/../r/pull/1"),
            ("pull_request", "https:\\\\github.com\\o\\r\\pull\\1"),
            ("repository", "javascript:alert(1)"),
            ("repository", "http://github.com/o/r"),
            ("repository", "https://github.com/o/r?x=1"),
            ("repository", "https://github.com/o/r#readme"),
            ("repository", "https://user:pw@github.com/o/r"),
        ]
        for case in await page.evaluate(LINKS, [list(one) for one in not_links]):
            assert case["links"] == [], case
            assert case["text"] is not None and case["value"] in case["text"], case
        links = await page.evaluate(
            LINKS,
            [
                ["pull_request", "https://github.com/o/r/pull/12"],
                ["pull_request", "HTTPS://GitHub.com/o/r/pull/7"],
                ["repository", "https://github.com/o/r"],
                ["repository", "https://github.com/o/r/"],
            ],
        )
        assert [one["links"][0]["text"] for one in links] == [
            "Pull request 12",
            "Pull request 7",
            "github.com/o/r",
            "github.com/o/r",
        ]
        for one in links:
            (only,) = one["links"]
            assert only["target"] == "_blank", one
            assert "noopener" in only["rel"] and "noreferrer" in only["rel"], one
            assert only["href"].startswith("https://"), one
        assert links[0]["links"][0]["href"] == "https://github.com/o/r/pull/12"
        # A branch, a label and a commit are words and never code or markup that could be run.
        shown = await page.evaluate(
            LINKS,
            [
                ["branch", "<img src=x onerror=alert(1)>"],
                ["branch", "feature/a"],
                ["commit", "0123456789ABCDEF"],
            ],
        )
        assert all(one["code"] == 0 and one["links"] == [] for one in shown), shown
        assert shown[0]["text"] is not None and "<img src=x" in shown[0]["text"]
        # Nothing said draws nothing: not an empty line.
        assert await page.evaluate(NOTHING) == [None, None, None, None, 0, 0]
        assert w.problems == [], w.problems


# -- Activity and the Agent view -----------------------------------------------------------------


async def test_activity_shows_where_each_claim_is_and_what_a_handoff_said() -> None:
    word = unique_word()
    project = make_project(register(name=f"Owner {word}"))
    worker, quiet = f"worker-{word}", f"quiet-{word}"
    async with (
        connect(project.mcp_url, make_key(project, worker)) as doer,
        connect(project.mcp_url, make_key(project, quiet)) as other,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, story, live = await make_tree(doer)
        lapsing = await another_task(doer, story, f"Lapsing {word}")
        done = await another_task(doer, story, f"Finished {word}")
        bare = await another_task(doer, story, f"Bare {word}")
        await ok(doer, "claim_node", node=live["ref"], **SAID)
        await ok(
            doer,
            "claim_node",
            node=lapsing["ref"],
            lease_seconds=1,
            branch="lapsing-branch",
            run_label="run-lapsing",
        )
        await ok(doer, "claim_node", node=done["ref"], branch="finished-branch")
        await ok(
            doer,
            "finish_work",
            node=done["ref"],
            outcome="blocked",
            note="Ready for a verifier.",
            pull_request=PULL_REQUEST,
            commit=NEWER,
        )
        await ok(other, "claim_node", node=bare["ref"])
        page = w.page
        await w.open("#/activity?tab=working")
        agent = page.locator(f"#agents li.agent[data-actor='{worker}']")
        await expect(agent).to_have_count(1, timeout=SLOW)

        # Each claim says where its work is, next to the countdown.
        row = agent.locator(f"li.claim[data-node='{live['ref']}']")
        await expect(row.locator(".countdown")).to_have_count(1)
        await expect(row.locator(".where-branch")).to_contain_text(BRANCH)
        await expect(row.locator(".where-commit .where-text")).to_have_text("0123456")
        await expect(row.locator(".where-pull-request a")).to_have_attribute("href", PULL_REQUEST)
        await expect(row.locator(".where-run")).to_have_text(f"Run: {RUN} ({CLIENT})")
        # So does a claim that ran out.
        await expect(
            agent.locator(f"li.claim.lapsed[data-node='{lapsing['ref']}'] .where-branch")
        ).to_contain_text("lapsing-branch", timeout=SLOW)
        # And the last handoff: what the claim said, with what was said last on top.
        last = agent.locator(".agent-handoff-block")
        await expect(last).to_contain_text("Last handoff: blocked")
        await expect(last.locator(".where-pull-request a")).to_have_attribute("href", PULL_REQUEST)
        await expect(last.locator(".where-commit .where-text")).to_have_text("89abcde")
        await expect(last.locator(".where-branch")).to_contain_text("finished-branch")
        # A claim that said nothing has nothing.
        other_row = page.locator(f"#agents li.agent[data-actor='{quiet}']")
        await expect(other_row.locator("li.claim")).to_have_count(1)
        await expect(other_row.locator(".where")).to_have_count(0)
        # The holder of every row is the key's name.
        labels = f"[data-actor-name='{RUN}'], [data-actor-name='run-lapsing']"
        await expect(page.locator(labels)).to_have_count(0)

        # The feed says it in the sentence, whole.
        await show_tab(page, "changes")
        taking = page.locator("#feed li.event[data-action=claimed]", has_text="Finished")
        await expect(taking).to_contain_text("on branch finished-branch", timeout=SLOW)
        mine = page.locator(
            "#feed li.event[data-action=claimed]", has=page.locator(f"a[data-ref='{live['ref']}']")
        )
        text = squeezed(await mine.locator(".event-words").inner_text())
        assert f"in run {RUN} ({CLIENT})" in text and f"on branch {BRANCH}" in text, text
        ended = page.locator("#feed li.event[data-action=handoff]", has_text="Finished")
        await expect(ended).to_contain_text("in pull request 12")
        await expect(ended).to_contain_text("Ready for a verifier.")
        bare_row = page.locator(
            "#feed li.event[data-action=claimed]", has=page.locator(f"a[data-ref='{bare['ref']}']")
        )
        bare_text = squeezed(await bare_row.locator(".event-words").inner_text())
        for gone in ("branch", "commit", "pull request", " run "):
            assert gone not in bare_text, bare_text
        assert w.problems == [], w.problems


async def test_the_agent_view_shows_the_same_line_only_when_the_item_has_it() -> None:
    word = unique_word()
    project = make_project(register(name=f"Owner {word}"))
    async with (
        connect(project.mcp_url, make_key(project, f"worker-{word}")) as doer,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, story, task = await make_tree(doer)
        bare = await another_task(doer, story, f"Bare {word}")
        await ok(doer, "claim_node", node=task["ref"], **SAID)
        page = w.page
        await open_item(w, task["ref"])
        await page.locator("#item-agent-view").click()
        text = page.locator("dialog#agent-view #agent-view-text")
        await expect(text).to_contain_text("recall(node=", timeout=SLOW)
        line = text.locator(".ag-line", has=page.locator(".where"))
        await expect(line).to_have_count(1)
        await expect(line.locator(".ag-key")).to_have_text("where:")
        await expect(line.locator(".where-branch")).to_contain_text(BRANCH)
        await expect(line.locator(".where-run")).to_have_text(f"Run: {RUN} ({CLIENT})")
        await expect(line.locator(".where-pull-request a")).to_have_attribute("href", PULL_REQUEST)
        await expect(text).to_contain_text(f'get_node(node="{task["ref"]}")')
        await page.keyboard.press("Escape")

        # An item that nobody said anything about has the dialog it always had.
        await open_item(w, bare["ref"])
        await page.locator("#item-agent-view").click()
        text = page.locator("dialog#agent-view #agent-view-text")
        await expect(text).to_contain_text("recall(node=", timeout=SLOW)
        await expect(text.locator(".where")).to_have_count(0)
        assert "get_node(" not in await text.inner_text()
        assert w.problems == [], w.problems


# -- a key's last day ---------------------------------------------------------------------------


async def make_key_with_day(page: Page, name: str, day: str | None) -> dict[str, Any]:
    """Make a key in the New agent key dialog (one tool at a time, the standard tools), with the
    Expires field as asked. Gives what the page sent."""
    await page.locator("#key-open").click()
    dialog = page.locator("dialog#new-key-dialog")
    await expect(dialog).to_be_visible(timeout=SLOW)
    await page.locator("#key-name").fill(name)
    await page.locator("#key-call-tools").check()
    await page.locator("#key-tools-choice-standard").click()
    if day is not None:
        await page.locator("#key-expires").fill(day)
    async with page.expect_request(
        lambda r: r.method == "POST" and r.url.endswith("/keys")
    ) as sent:
        await page.locator("#create-key").click()
    body = (await sent.value).post_data_json
    await expect(page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
    await expect(dialog).to_have_count(0)
    assert isinstance(body, dict)
    return body


async def open_change(page: Page, name: str) -> None:
    await page.locator("#keys li.key", has_text=name).locator("button.change-tools").click()
    await expect(page.locator("dialog#change-tools-dialog")).to_be_visible(timeout=SLOW)


async def save_change(page: Page, key_id: int) -> Any:
    async with page.expect_request(
        lambda r: r.method == "PATCH" and r.url.endswith(f"/keys/{key_id}")
    ) as sent:
        await page.locator("dialog#change-tools-dialog button[type=submit]").click()
    return (await sent.value).post_data_json


async def test_the_new_key_dialog_takes_an_optional_day_and_the_row_says_it() -> None:
    word = unique_word()
    owner = register(name=f"Keymaker {word}")
    project = make_project(owner, f"Key days {word}")
    day = tomorrow()
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await w.open("#/users")
        await expect(page.locator("#keys li.key")).to_have_count(1, timeout=SLOW)

        # The dialog opens empty and closed: the Expires field has no day, offers no Clear (there is
        # nothing to clear), has a calendar button, and says what empty means.
        await page.locator("#key-open").click()
        dialog = page.locator("dialog#new-key-dialog")
        await expect(dialog).to_be_visible(timeout=SLOW)
        await expect(dialog.locator("input#key-expires")).to_have_value("")
        await expect(dialog.locator("label[for=key-expires]")).to_have_text("Expires")
        await expect(dialog.locator(".key-expiry .date-field button.dp-open")).to_have_count(1)
        await expect(dialog.locator(".key-expiry-clear")).to_have_count(0)
        await expect(dialog.locator("#key-expires-help")).to_contain_text("never expires")
        await expect(dialog.locator(".key-expired-note")).to_have_count(0)
        await nothing_open(page)
        await page.keyboard.press("Escape")
        await expect(dialog).to_have_count(0)

        # A key with no day sends none and says nothing about it.
        posted = await make_key_with_day(page, f"forever-{word}", None)
        assert "expires_on" not in posted, posted
        forever = page.locator("#keys li.key", has_text=f"forever-{word}")
        await expect(forever).to_have_count(1, timeout=SLOW)
        await expect(forever.locator(".chip.key-expiry")).to_have_count(0)
        await expect(forever).not_to_contain_text("xpire")
        assert key_named(owner, project.id, f"forever-{word}")["expires_on"] is None

        # A key with a day sends it, and its row has ONE quiet chip with the day.
        posted = await make_key_with_day(page, f"dated-{word}", day)
        assert posted["expires_on"] == day, posted
        dated = page.locator("#keys li.key", has_text=f"dated-{word}")
        chip = dated.locator(".chip.key-expiry")
        await expect(chip).to_have_count(1, timeout=SLOW)
        await expect(chip).to_have_text(f"Expires {words(day)}")
        await expect(chip).not_to_have_class(DANGER)
        await expect(chip).to_have_attribute("data-expired", "false")
        assert key_named(owner, project.id, f"dated-{word}")["expires_on"] == day
        await expect(page.locator("#keys .chip.key-expiry")).to_have_count(1)

        # A day that has passed is refused in the dialog, in the server's words, and what was
        # typed stays.
        await page.locator("#key-open").click()
        await expect(dialog).to_be_visible(timeout=SLOW)
        await page.locator("#key-name").fill(f"late-{word}")
        await page.locator("#key-call-tools").check()
        await page.locator("#key-tools-choice-standard").click()
        await page.locator("#key-expires").fill("2001-01-01")
        await page.locator("#create-key").click()
        await expect(dialog.locator(".problem")).to_contain_text("already passed", timeout=SLOW)
        await expect(dialog.locator(".problem")).to_contain_text("Nothing was changed")
        await expect(page.locator("#key-expires")).to_have_value("2001-01-01")
        await expect(page.locator("#key-name")).to_have_value(f"late-{word}")
        await page.keyboard.press("Escape")
        assert w.problems == [], w.problems


async def test_the_owner_changes_and_clears_a_day_and_an_expired_key_shows_one_danger_chip() -> (
    None
):
    word = unique_word()
    owner = register(name=f"Extender {word}")
    project = make_project(owner, f"Extend keys {word}")
    name = f"short-{word}"
    day = tomorrow()
    made = owner.call("POST", keys_path(project), {"name": name, "expires_on": day})
    assert made.status == 201, made.body
    secret, key_id = made.body["secret"], made.body["key"]["id"]
    async with (
        connect(project.mcp_url, secret) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        page = w.page
        await ok(agent, "list_nodes")
        await w.open("#/users")
        row = page.locator("#keys li.key", has_text=name)
        await expect(row.locator(".chip.key-expiry")).to_have_text(
            f"Expires {words(day)}", timeout=SLOW
        )

        # The name card of the agent says it too.
        await row.locator(".actor.has-card").hover()
        named = page.locator(".hovercard:visible .actor-card")
        await expect(named.locator(".chip.key-expiry")).to_have_text(
            f"Expires {words(day)}", timeout=SLOW
        )
        await page.keyboard.press("Escape")
        await page.mouse.move(5, 5)

        # The Change dialog has the day and a Clear, and no note while the key works.
        await open_change(page, name)
        dialog = page.locator("dialog#change-tools-dialog")
        field = dialog.locator("input#key-expires-change")
        await expect(field).to_have_value(day)
        await expect(dialog.locator(".key-expired-note")).to_have_count(0)
        clear = dialog.locator(".key-expiry-clear")
        await expect(clear).to_have_count(1)
        # A passed day is refused in the server's words, and nothing is lost.
        await field.fill("2001-01-01")
        async with page.expect_response(
            lambda r: r.request.method == "PATCH" and r.url.endswith(f"/keys/{key_id}")
        ) as refusal:
            await dialog.locator("button[type=submit]").click()
        assert (await refusal.value).status == 400
        await expect(dialog.locator(".problem")).to_contain_text("already passed", timeout=SLOW)
        await expect(dialog.locator(".problem")).to_contain_text("Nothing was changed")
        await expect(dialog).to_be_visible()
        assert key_row(project, key_id)["expires_on"] == day

        # Clear empties the field, and saving sends only that: a key that never expires, no chip.
        await clear.click()
        await expect(field).to_have_value("")
        assert await save_change(page, key_id) == {"expires_on": None}
        await expect(dialog).to_have_count(0, timeout=SLOW)
        await expect(row.locator(".chip.key-expiry")).to_have_count(0, timeout=SLOW)
        assert key_row(project, key_id)["expires_on"] is None

        # A day is set again, typed, and the chip is back.
        await open_change(page, name)
        await expect(field).to_have_value("")
        await expect(dialog.locator(".key-expiry-clear")).to_have_count(0)
        await field.fill(day)
        assert await save_change(page, key_id) == {"expires_on": day}
        await expect(row.locator(".chip.key-expiry")).to_have_text(
            f"Expires {words(day)}", timeout=SLOW
        )

        # The day passes. The server says the key expired: ONE danger chip with the day, in the
        # place of the quiet one (never both), and the agent is refused on its open connection.
        end_yesterday(key_id)
        gone = key_row(project, key_id)
        assert gone["expired"] is True
        await go(page, project.address, "#/users")
        row = page.locator("#keys li.key", has_text=name)
        expired = row.locator(".chip.key-expiry")
        await expect(expired).to_have_count(1, timeout=SLOW)
        await expect(expired).to_have_text(f"Expired {words(gone['expires_on'])}")
        await expect(expired).to_have_class(DANGER)
        await expect(expired).to_have_attribute("data-expired", "true")
        await expect(expired.locator("svg.icon-alert")).to_have_count(1)
        with pytest.raises(BaseException) as already_open:
            await agent.call_tool("list_nodes", {})
        said = messages(already_open.value)
        assert "expired after" in said and "extend it" in said, said

        # The Change dialog says what to do, in one quiet sentence, while the key is expired.
        await open_change(page, name)
        note = dialog.locator(".key-expired-note")
        await expect(note).to_have_count(1)
        await expect(note).to_contain_text("extend it")
        await expect(note).to_contain_text("make a new key")
        await expect(field).to_have_value(gone["expires_on"])
        await field.fill(day)
        assert await save_change(page, key_id) == {"expires_on": day}
        await expect(dialog).to_have_count(0, timeout=SLOW)
        # The chip is the quiet one again, and the open connection works at once.
        await expect(row.locator(".chip.key-expiry")).to_have_text(
            f"Expires {words(day)}", timeout=SLOW
        )
        await expect(row.locator(".chip.key-expiry")).not_to_have_class(DANGER)
        await ok(agent, "list_nodes")

        # On a phone the row and the dialog with its sentence do not widen the page.
        end_yesterday(key_id)
        await page.set_viewport_size(NARROW)
        await go(page, project.address, "#/users")
        await expect(page.locator("#keys li.key .chip.key-expiry.danger")).to_have_count(
            1, timeout=SLOW
        )
        await no_overflow(page)
        await open_change(page, name)
        await no_overflow(page)
        assert w.problems == [], w.problems


async def test_only_the_owner_sees_the_keys_so_an_editor_has_no_chip_and_no_control() -> None:
    word = unique_word()
    owner = register(name=f"Owner {word}")
    project = make_project(owner, f"Owner only {word}")
    made = owner.call(
        "POST", keys_path(project), {"name": f"dated-{word}", "expires_on": tomorrow()}
    )
    assert made.status == 201, made.body
    key_id = made.body["key"]["id"]
    editor = join_project(project, "editor", f"Editor {word}")
    async with browser_page(project.address, token=None, name=None, person=editor) as w:
        page = w.page
        await w.open("#/users")
        await expect(page.locator("#keys-owner-only")).to_be_visible(timeout=SLOW)
        assert await page.locator("#keys, #key-open, .change-tools, .chip.key-expiry").count() == 0
        # The server says the same: an editor is not given the keys and cannot change one.
        assert editor.call("GET", keys_path(project)).status in (403, 404)
        changed = editor.call("PATCH", f"{keys_path(project)}/{key_id}", {"expires_on": None})
        assert changed.status in (403, 404)
        assert key_row(project, key_id)["expires_on"] is not None
        assert w.problems == [], w.problems
