"""M40: a verifier, and checks that only someone other than the doer may tick (against the running
containers, through a real MCP client and the board's own HTTP API).

A line of the definition of done tagged `[verifier]` can be ticked or unticked by an agent only if
its key is marked a verifier and it did not do the work on the story or task (it never claimed it
and never handed it off); a person always may. A line tagged `[github]` is the same, and a
verifier's evidence for it starts with the address of a GitHub pull request. Vrum never calls
GitHub: it records what the verifier says it saw. What has to hold: the tags are read and kept
apart from the words, the refusals are in plain words and say what to do, the doer is sent to
finish as blocked, a verifier finds that work with `is:unverified` and sets it done, a key's
verifier flag is the owner's to change and applies at once, and the tags survive an export and an
import. Each test works in a project of its own."""

import re
from typing import Any

import pytest
from mcp import Client

from tests.helpers import connect, export_everything, make_tree, ok, refused, run_sql
from tests.test_checks_integration import (
    A_TOKEN,
    DOCS,
    TESTS,
    as_person,
    define,
    key_of,
    sql_rows,
    words_of,
)
from tests.world import HARNESS_AGENT, make_key, make_limited_key, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

REVIEWED = "Reviewed by someone else"
MERGED = "The pull request is merged and its checks passed"
PHONE = "Looks right on a phone"
PULL_REQUEST = "https://github.com/acme/widgets/pull/12"
SEEN = f"{PULL_REQUEST} merged 2026-11-22T17:30:00Z, merge commit 0123456789, checks passed 3 of 3"
BODY = f"- {TESTS}\n- [verifier] {REVIEWED}\n- [github] {MERGED}\n- [person] {PHONE}\n"


async def checks_of(client: Client, ref: str) -> dict[str, dict[str, Any]]:
    """The checks of a story or task by their words."""
    return {c["text"]: c for c in (await ok(client, "get_node", node=ref))["checks"]}


async def worked_task(project: Any, worker: Client) -> dict[str, Any]:
    """A task that the worker agent claimed and handed over as blocked, ready to be verified."""
    _, _, task = await make_tree(worker)
    await ok(worker, "claim_node", node=task["ref"])
    await ok(worker, "set_check", node=task["ref"], item=TESTS, evidence="Ran them")
    await ok(
        worker,
        "finish_work",
        node=task["ref"],
        outcome="blocked",
        note="Everything is built. Ready for a verifier.",
    )
    return task


async def test_the_tags_are_read_kept_apart_from_the_words_and_shown_to_agents() -> None:
    owner = register()
    project = make_project(owner)
    read = define(owner, project, BODY)
    assert [(i["item"], i["text"]) for i in read["items"]] == [
        (key_of(TESTS), TESTS),
        (key_of(REVIEWED), REVIEWED),
        (key_of(MERGED), MERGED),
        (key_of(PHONE), PHONE),
    ]
    assert [(i["person_only"], i["verifier_only"], i["github"]) for i in read["items"]] == [
        (False, False, False),
        (False, True, False),
        # [github] means [verifier] as well.
        (False, True, True),
        (True, False, False),
    ]
    assert sql_rows(
        project,
        "SELECT text, person_only, verifier_only, github FROM definition_items ORDER BY position",
    ) == [
        [TESTS, "f", "f", "f"],
        [REVIEWED, "f", "t", "f"],
        [MERGED, "f", "t", "t"],
        [PHONE, "t", "f", "f"],
    ]
    async with connect(project.mcp_url, project.key) as agent:
        _, _, task = await make_tree(agent)
        shown = (await ok(agent, "get_node", node=task["ref"]))["checks"]
        assert [(c["verifier_only"], c["github"]) for c in shown] == [
            (False, False),
            (True, False),
            (True, True),
            (False, False),
        ]
        context = (await ok(agent, "next_work", peek=True))["context"]
        assert [(c["verifier_only"], c["github"]) for c in context["definition_checks"]] == [
            (False, False),
            (True, False),
            (True, True),
            (False, False),
        ]
    # Adding or taking away a tag keeps the key, so every tick stays.
    as_person(owner, project, "set_check", node=task["ref"], item=REVIEWED)
    define(owner, project, BODY.replace("[verifier] ", ""))
    async with connect(project.mcp_url, project.key) as agent:
        again = await checks_of(agent, task["ref"])
        assert again[REVIEWED]["checked"] and not again[REVIEWED]["verifier_only"]


async def test_an_agent_that_is_not_a_verifier_can_neither_tick_nor_untick_such_a_check() -> None:
    owner = register()
    project = make_project(owner)
    define(owner, project, BODY)
    async with connect(project.mcp_url, project.key) as agent:
        _, _, task = await make_tree(agent)
        for text, tag in ((REVIEWED, "[verifier]"), (MERGED, "[github]")):
            for item in (key_of(text), text, f"{tag} {text}"):
                said = await refused(agent, "set_check", node=task["ref"], item=item, evidence=SEEN)
                assert words_of(said) == (
                    f"{text!r} is a check only a verifier can judge (the definition of done marks "
                    f"it {tag}). A verifier is an agent whose key the project's owner marked as "
                    f"one on the Users page, and it must not be the agent that did the work on "
                    f"{task['ref']}. Leave it for one: finish the item as blocked and say in the "
                    "handoff note that it is ready for a verifier. Nothing was changed."
                )
        assert not sql_rows(project, "SELECT 1 FROM definition_checks")
        # A person is never refused, and an agent cannot take that tick back.
        ticked = as_person(owner, project, "set_check", node=task["ref"], item=REVIEWED)
        assert ticked.status == 200, ticked.body
        said = await refused(
            agent, "set_check", node=task["ref"], item=REVIEWED, checked=False, evidence="Why"
        )
        assert "only a verifier can judge" in said
        assert (await checks_of(agent, task["ref"]))[REVIEWED]["checked"] is True
        # The ordinary check next to them is still the agent's own to tick.
        await ok(agent, "set_check", node=task["ref"], item=TESTS, evidence="Ran them")


async def test_a_verifier_who_did_not_do_the_work_ticks_and_unticks_and_one_who_did_cannot() -> (
    None
):
    owner = register()
    project = make_project(owner)
    define(owner, project, BODY)
    reviewer = make_key(project, "reviewer-agent", verifier=True)
    async with (
        connect(project.mcp_url, project.key) as worker,
        connect(project.mcp_url, reviewer) as verifier,
    ):
        task = await worked_task(project, worker)

        # The verifier ticks the [verifier] check, with its words and its own name on it.
        ticked = await ok(
            verifier, "set_check", node=task["ref"], item=REVIEWED, evidence="Read the diff"
        )
        mine = {c["text"]: c for c in ticked["checks"]}[REVIEWED]
        assert (mine["checked_by"], mine["checked_by_kind"]) == ("reviewer-agent", "agent")
        assert mine["evidence"] == "Read the diff"

        # A [github] tick starts with the address of a pull request, and says what was seen.
        for evidence in ("It is merged", f"merged {PULL_REQUEST}", "http://github.com/a/b/pull/1"):
            said = await refused(
                verifier, "set_check", node=task["ref"], item=MERGED, evidence=evidence
            )
            assert words_of(said).startswith(
                f"{MERGED!r} is a [github] check: start the evidence with the address of the "
                "GitHub pull request, such as https://github.com/owner/repo/pull/12, and then "
                "say what you saw there"
            )
            assert "scripts/verify_pr.py" in said and "Nothing was changed." in said
        done = await ok(verifier, "set_check", node=task["ref"], item=MERGED, evidence=SEEN)
        assert {c["text"]: c for c in done["checks"]}[MERGED]["evidence"] == SEEN
        # Unticking needs no address, and the verifier may take its own tick back.
        back = await ok(
            verifier, "set_check", node=task["ref"], item=MERGED, checked=False, evidence="Re-open"
        )
        assert {c["text"]: c for c in back["checks"]}[MERGED]["checked"] is False
        await ok(verifier, "set_check", node=task["ref"], item=MERGED, evidence=SEEN)

        # The person-only check stays a person's, verifier or not.
        said = await refused(verifier, "set_check", node=task["ref"], item=PHONE, evidence="Saw it")
        assert "only a person can judge" in said

        # The verifier is recorded as itself in the history.
        events = [
            e
            for e in (await ok(verifier, "node_history", node=task["ref"]))["items"]
            if e["action"].startswith("check_")
        ]
        assert [(e["actor"], e["action"]) for e in events if e["actor"] == "reviewer-agent"] == [
            ("reviewer-agent", "check_ticked"),
            ("reviewer-agent", "check_ticked"),
            ("reviewer-agent", "check_unticked"),
            ("reviewer-agent", "check_ticked"),
        ]

        # A verifier that took the work on itself has done the work: it cannot check it.
        _, _, own = await make_tree(verifier)
        await ok(verifier, "claim_node", node=own["ref"])
        for checked in (True, False):
            said = await refused(
                verifier,
                "set_check",
                node=own["ref"],
                item=REVIEWED,
                checked=checked,
                evidence="Mine",
            )
            assert words_of(said) == (
                f"You cannot {'tick' if checked else 'untick'} {REVIEWED!r} on {own['ref']}: you "
                "claimed it or handed it off, so it is your own work, and a verifier may not "
                "check what it did itself. Another verifier or a person must. Nothing was "
                "changed."
            )
        # ... even after it let the claim go.
        await ok(verifier, "release_claim", node=own["ref"], note="Not mine after all.")
        said = await refused(verifier, "set_check", node=own["ref"], item=REVIEWED, evidence="Mine")
        assert "your own work" in said


async def test_done_is_refused_to_the_doer_and_a_verifier_finds_the_work_and_finishes_it() -> None:
    owner = register()
    project = make_project(owner)
    define(owner, project, f"- {TESTS}\n- [verifier] {REVIEWED}\n- {DOCS}")
    reviewer = make_key(project, "reviewer-agent", verifier=True)
    async with (
        connect(project.mcp_url, project.key) as worker,
        connect(project.mcp_url, reviewer) as verifier,
    ):
        _, _, task = await make_tree(worker)
        await ok(worker, "claim_node", node=task["ref"])

        said = await refused(worker, "finish_work", node=task["ref"], outcome="done", note="Done.")
        assert words_of(said) == (
            f"{task['ref']} cannot be done yet: its definition of done has checks that are not "
            f"ticked. Tick these checks first with set_check, saying how you checked: "
            f"'{TESTS}' (item {key_of(TESTS)}); '{DOCS}' (item {key_of(DOCS)}). Only a verifier "
            f"who did not do the work on {task['ref']} can tick this check (the definition of "
            f"done marks it [verifier] or [github]), and your key is not a verifier's: "
            f"'{REVIEWED}'. Then finish it as blocked, saying in the handoff note that it is "
            "ready for a verifier; a verifier then ticks the checks and marks it done. Nothing "
            "was changed."
        )
        for words in (TESTS, DOCS):
            await ok(worker, "set_check", node=task["ref"], item=words, evidence="Did it")
        only = await refused(worker, "finish_work", node=task["ref"], outcome="done", note="Done.")
        assert words_of(only) == (
            f"{task['ref']} cannot be done yet: its definition of done has checks that are not "
            f"ticked. Only a verifier who did not do the work on {task['ref']} can tick this "
            "check (the definition of done marks it [verifier] or [github]), and your key is "
            f"not a verifier's: '{REVIEWED}'. Finish it as blocked, saying in the handoff note "
            "that it is ready for a verifier; a verifier then ticks the checks and marks it "
            "done. Nothing was changed."
        )
        shown = await ok(worker, "get_node", node=task["ref"])
        stuck = await refused(
            worker,
            "update_node",
            node=task["ref"],
            expected_version=shown["version"],
            status="done",
        )
        assert (
            "Set its status to blocked with update_node, giving why to say that it is ready for a "
            "verifier; a verifier then ticks the checks and marks it done. Nothing was changed."
        ) in stuck

        await ok(
            worker,
            "finish_work",
            node=task["ref"],
            outcome="blocked",
            note="Everything is built. Ready for a verifier.",
        )

        waiting = await ok(verifier, "list_nodes", query="is:unverified", limit=200)
        assert task["ref"] in [n["ref"] for n in waiting["items"]]
        # The verifier ticks what is its to tick and sets it done, with no claim.
        await ok(verifier, "set_check", node=task["ref"], item=REVIEWED, evidence="Read the diff")
        current = await ok(verifier, "get_node", node=task["ref"])
        finished = await ok(
            verifier,
            "update_node",
            node=task["ref"],
            expected_version=current["version"],
            status="done",
        )
        assert finished["status"] == "done"
        again = await ok(verifier, "list_nodes", query="is:unverified", limit=200)
        assert task["ref"] not in [n["ref"] for n in again["items"]]

        # A verifier that did the work is refused done with the reason that is true of it.
        _, _, own = await make_tree(verifier)
        await ok(verifier, "claim_node", node=own["ref"])
        for words in (TESTS, DOCS):
            await ok(verifier, "set_check", node=own["ref"], item=words, evidence="Did it")
        mine = await refused(verifier, "finish_work", node=own["ref"], outcome="done", note="Done.")
        assert (
            f"and you did the work on it, and a verifier may not check its own work: '{REVIEWED}'."
        ) in mine


async def test_the_unverified_filter_finds_blocked_work_nobody_holds_with_a_check_open() -> None:
    owner = register()
    project = make_project(owner)
    define(owner, project, f"- {TESTS}\n- [verifier] {REVIEWED}")
    reviewer = make_key(project, "reviewer-agent", verifier=True)

    async def waiting(client: Client) -> set[str]:
        found = await ok(client, "list_nodes", query="is:unverified", limit=200)
        return {n["ref"] for n in found["items"]}

    async with (
        connect(project.mcp_url, project.key) as worker,
        connect(project.mcp_url, reviewer) as verifier,
    ):
        _, story, task = await make_tree(worker)
        # New, ready and unstarted work is not waiting for a verifier.
        assert await waiting(verifier) == set()
        await ok(worker, "claim_node", node=task["ref"])
        assert await waiting(verifier) == set()  # held, in progress
        await ok(
            worker, "finish_work", node=task["ref"], outcome="blocked", note="Ready to verify."
        )
        assert task["ref"] in await waiting(verifier)
        # An epic is never found.
        assert not any(ref.startswith("epic:") for ref in await waiting(verifier))
        # Ticking the one check that needs a verifier takes it off the list; unticking restores it.
        await ok(verifier, "set_check", node=task["ref"], item=REVIEWED, evidence="Read it")
        assert task["ref"] not in await waiting(verifier)
        await ok(
            verifier, "set_check", node=task["ref"], item=REVIEWED, checked=False, evidence="No"
        )
        assert task["ref"] in await waiting(verifier)
        # A check only a person can judge is not a verifier's to wait for.
        define(owner, project, f"- {TESTS}\n- [person] {PHONE}")
        assert task["ref"] not in await waiting(verifier)
        # Dropped or done work is not waiting.
        define(owner, project, f"- {TESTS}\n- [verifier] {REVIEWED}")
        assert task["ref"] in await waiting(verifier)
        current = await ok(worker, "get_node", node=task["ref"])
        await ok(
            worker,
            "update_node",
            node=task["ref"],
            expected_version=current["version"],
            status="dropped",
            why="No longer needed.",
        )
        assert task["ref"] not in await waiting(verifier)
        assert story["ref"] not in await waiting(verifier)


async def test_a_key_is_made_a_verifier_and_cleared_by_the_owner_and_it_applies_at_once() -> None:
    owner = register()
    project = make_project(owner)
    define(owner, project, BODY)
    where = f"/board/api/projects/{project.id}/keys"
    secret = make_limited_key(project, "careful-agent", None, verifier=True)
    listed = owner.call("GET", where).body["keys"]
    mine = next(k for k in listed if k["name"] == "careful-agent")
    assert mine["verifier"] is True
    assert next(k for k in listed if k["name"] == HARNESS_AGENT)["verifier"] is False

    async with (
        connect(project.mcp_url, project.key) as worker,
        connect(project.mcp_url, secret) as agent,
    ):
        task = await worked_task(project, worker)
        await ok(agent, "set_check", node=task["ref"], item=REVIEWED, evidence="Read it")

        # Cleared by the owner: the very next call on the same open connection is refused.
        cleared = owner.call("PATCH", f"{where}/{mine['id']}", {"verifier": False})
        assert cleared.status == 200 and cleared.body["key"]["verifier"] is False
        said = await refused(
            agent, "set_check", node=task["ref"], item=REVIEWED, checked=False, evidence="Redo"
        )
        assert "only a verifier can judge" in said
        # Set again, it is accepted at once.
        set_again = owner.call("PATCH", f"{where}/{mine['id']}", {"verifier": True})
        assert set_again.status == 200 and set_again.body["key"]["verifier"] is True
        await ok(
            agent, "set_check", node=task["ref"], item=REVIEWED, checked=False, evidence="Redo"
        )

    # Nothing but true or false is taken for either, and a bad part changes nothing.
    for bad in ("yes", 1, None, [True]):
        refusal = owner.call("PATCH", f"{where}/{mine['id']}", {"verifier": bad})
        assert refusal.status == 400, bad
        assert refusal.body["message"] == (
            "Whether a key is a verifier's has to be true or false. Nothing was changed."
        )
    made = owner.call("POST", where, {"name": "odd-agent", "verifier": "yes"})
    assert made.status == 400 and made.body["message"].startswith("Whether a key is a verifier's")
    mixed = owner.call("PATCH", f"{where}/{mine['id']}", {"code_mode": True, "verifier": "no"})
    assert mixed.status == 400
    kept = next(k for k in owner.call("GET", where).body["keys"] if k["id"] == mine["id"])
    assert (kept["verifier"], kept["code_mode"]) == (True, False)
    # A key made with no mention of it is not a verifier's.
    plain = owner.call("POST", where, {"name": "plain-agent"})
    assert plain.status == 201 and plain.body["key"]["verifier"] is False


async def test_only_the_owner_changes_whether_a_key_verifies() -> None:
    from tests.world import join_project

    owner = register()
    project = make_project(owner)
    editor = join_project(project, "editor")
    viewer = join_project(project, "viewer")
    where = f"/board/api/projects/{project.id}/keys"
    key_id = owner.call("GET", where).body["keys"][0]["id"]
    for person in (editor, viewer):
        made = person.call("POST", where, {"name": "sneaky-agent", "verifier": True})
        assert made.status == 403
        changed = person.call("PATCH", f"{where}/{key_id}", {"verifier": True})
        assert changed.status == 403
        assert changed.body["message"].startswith("Only the owner of this project can ")
    assert owner.call("GET", where).body["keys"][0]["verifier"] is False


async def test_a_script_run_by_a_key_sees_its_verifier_flag_at_every_call() -> None:
    owner = register()
    project = make_project(owner)
    define(owner, project, BODY)
    where = f"/board/api/projects/{project.id}/keys"
    code_key = make_key(project, "script-verifier", code_mode=True, verifier=True)
    plain_code_key = make_key(project, "script-not-verifier", code_mode=True)
    async with connect(project.mcp_url, project.key) as worker:
        task = await worked_task(project, worker)
    script = (
        f"return await vrum.set_check({{ node: '{task['ref']}', item: '{REVIEWED}', "
        "checked: true, evidence: 'Read it in a script' });"
    )
    async with connect(project.mcp_url, plain_code_key) as agent:
        said = await refused(agent, "run", code=script)
        assert "only a verifier can judge" in said
    async with connect(project.mcp_url, code_key) as agent:
        result = await ok(agent, "run", code=script)
        assert result["result"]["checks_ticked"] >= 2
        # Taken away, the next script is refused at its first call.
        key_id = next(
            k["id"] for k in owner.call("GET", where).body["keys"] if k["name"] == "script-verifier"
        )
        assert owner.call("PATCH", f"{where}/{key_id}", {"verifier": False}).status == 200
        again = await refused(
            agent,
            "run",
            code=script.replace("checked: true", "checked: false"),
        )
        assert "only a verifier can judge" in again


async def test_the_tags_and_the_checks_travel_through_an_export_and_an_import() -> None:
    source = make_project(register())
    define(source.owner, source, BODY)
    async with connect(source.mcp_url, source.key) as client:
        file = await export_everything(client)
    assert file["definition_of_done"][0]["body"] == BODY
    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        items = (await ok(client, "get_definition_of_done"))["items"]
        assert [(i["person_only"], i["verifier_only"], i["github"]) for i in items] == [
            (False, False, False),
            (False, True, False),
            (False, True, True),
            (True, False, False),
        ]
    assert sql_rows(
        target, "SELECT verifier_only, github FROM definition_items ORDER BY position"
    ) == [["f", "f"], ["t", "f"], ["t", "t"], ["f", "f"]]


async def test_the_database_refuses_a_github_item_that_is_not_for_a_verifier() -> None:
    project = make_project(register())
    define(project.owner, project, f"- [verifier] {REVIEWED}")
    broken = run_sql(
        f"UPDATE definition_items SET github = true, verifier_only = false "
        f"WHERE project_id = {project.id}",
        plain=True,
    )
    assert broken.returncode != 0
    assert "definition_item_github_is_for_a_verifier" in broken.stderr
    assert re.search(r"violates check constraint", broken.stderr)


async def test_a_secret_is_never_taken_in_the_evidence_of_a_tick() -> None:
    owner = register()
    project = make_project(owner)
    define(owner, project, BODY)
    reviewer = make_key(project, "reviewer-agent", verifier=True)
    async with (
        connect(project.mcp_url, project.key) as worker,
        connect(project.mcp_url, reviewer) as verifier,
    ):
        task = await worked_task(project, worker)
        said = await refused(
            verifier,
            "set_check",
            node=task["ref"],
            item=MERGED,
            evidence=f"{PULL_REQUEST} merged, token {A_TOKEN}",
        )
        assert A_TOKEN not in said
        assert not sql_rows(
            project, f"SELECT 1 FROM definition_checks WHERE item_key = '{key_of(MERGED)}'"
        )
