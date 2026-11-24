"""M40: scripts/verify_pr.py, run as a person would, against the running containers, with a stand-in
`gh` on the PATH that says what the test tells it to (a real GitHub is not tested, as with sign-in).

The script asks `gh pr view`, decides whether the pull request is merged with every check passed,
and only then ticks the [github] checks of a story or task as the verifier whose key it holds,
with the evidence in one fixed form that starts with the pull request's address. What has to hold:
it ticks in the one case that is good and never otherwise, it never touches a check it may not
(a check that is not [github], or one it is refused), it says why in plain words, it exits with
the status the script documents, and Vrum itself is never asked to reach GitHub."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tests.helpers import REPO_ROOT, connect, make_tree, ok
from tests.test_checks_integration import DOCS, TESTS, define
from tests.world import Project, make_key, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ADDRESS = "https://github.com/acme/widgets/pull/12"
REVIEWED = "Reviewed by someone else"
MERGED = "The pull request is merged and its checks passed"
BODY = f"- {TESTS}\n- [verifier] {REVIEWED}\n- [github] {MERGED}\n"
GOOD = {
    "url": ADDRESS,
    "state": "MERGED",
    "isDraft": False,
    "mergedAt": "2026-11-22T17:30:00Z",
    "mergeCommit": {"oid": "0123456789abcdef0123"},
    "headRefOid": "fedcba9876543210fedc",
    "statusCheckRollup": [
        {"__typename": "CheckRun", "name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "SKIPPED"},
    ],
}
SEEN = f"{ADDRESS} merged 2026-11-22T17:30:00Z, merge commit 0123456789, checks passed 2 of 2"

# What `gh` is in these tests: it logs how it was asked and prints the pull request the test chose,
# or fails with the words the test chose.
STAND_IN_GH = f"""#!{sys.executable}
import os, sys
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
if os.environ.get("FAKE_GH_FAIL"):
    print(os.environ["FAKE_GH_FAIL"], file=sys.stderr)
    sys.exit(1)
print(open(os.environ["FAKE_GH_VIEW"]).read())
"""


class Gh:
    """A folder with the stand-in `gh` in it, and the pull request it will describe."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.bin = folder / "bin"
        self.bin.mkdir()
        tool = self.bin / "gh"
        tool.write_text(STAND_IN_GH, encoding="utf-8")
        tool.chmod(0o755)
        self.view = folder / "view.json"
        self.log = folder / "gh.log"
        self.say(GOOD)

    def say(self, view: dict[str, Any]) -> None:
        self.view.write_text(json.dumps(view), encoding="utf-8")

    def asked(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []


def run_verify(
    gh: Gh,
    key: str,
    node: str,
    address: str | None = ADDRESS,
    *more: str,
    fail: str | None = None,
    bare_path: bool = False,
) -> subprocess.CompletedProcess[str]:
    """The script, with the stand-in gh first on the PATH, or (`bare_path`) with a PATH that holds
    only an empty folder, so that no gh is there at all."""
    environment = {k: v for k, v in os.environ.items() if k not in ("VRUM_AGENT_KEY", "PATH")}
    path = str(gh.folder / "empty") if bare_path else f"{gh.bin}{os.pathsep}{os.environ['PATH']}"
    environment.update(
        VRUM_AGENT_KEY=key,
        PATH=path,
        FAKE_GH_VIEW=str(gh.view),
        FAKE_GH_LOG=str(gh.log),
    )
    if fail:
        environment["FAKE_GH_FAIL"] = fail
    return subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "verify_pr.py"),
            os.environ["VRUM_TEST_URL"],
            node,
            *([address] if address else []),
            *more,
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


async def ready_task(project: Project, worker: Client) -> dict[str, Any]:
    """A task the worker did and handed over as blocked, with the checks of BODY."""
    _, _, task = await make_tree(worker)
    await ok(worker, "claim_node", node=task["ref"])
    await ok(worker, "set_check", node=task["ref"], item=TESTS, evidence="Ran them")
    await ok(worker, "finish_work", node=task["ref"], outcome="blocked", note="Ready to verify.")
    return task


async def ticks(client: Client, ref: str) -> dict[str, dict[str, Any]]:
    return {c["text"]: c for c in (await ok(client, "get_node", node=ref))["checks"]}


async def test_a_merged_pull_request_with_passing_checks_is_ticked_by_the_verifier(
    tmp_path: Path,
) -> None:
    project = make_project(register())
    define(project.owner, project, BODY)
    reviewer = make_key(project, "reviewer-agent", verifier=True)
    gh = Gh(tmp_path)
    async with connect(project.mcp_url, project.key) as worker:
        task = await ready_task(project, worker)
        done = run_verify(gh, reviewer, task["ref"])
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == f"Ticked {MERGED!r} on {task['ref']}."
        # gh was asked about exactly this pull request, once, and for what the script reads.
        (asked,) = gh.asked()
        assert asked.startswith(f"pr view {ADDRESS} --json ") and "statusCheckRollup" in asked

        checks = await ticks(worker, task["ref"])
        mine = checks[MERGED]
        assert (mine["checked"], mine["checked_by"], mine["checked_by_kind"]) == (
            True,
            "reviewer-agent",
            "agent",
        )
        assert mine["evidence"] == SEEN
        # Nothing else was touched: not the worker's check and not the other verifier check.
        assert checks[TESTS]["checked_by"] != "reviewer-agent" and checks[TESTS]["checked"]
        assert checks[REVIEWED]["checked"] is False
        # Run again, there is nothing left to tick and it says so.
        again = run_verify(gh, reviewer, task["ref"])
        assert again.returncode == 0
        assert again.stdout.strip() == f"Every [github] check of {task['ref']} is already ticked."


async def test_with_no_address_it_uses_the_pull_request_the_worker_said_the_item_is_in(
    tmp_path: Path,
) -> None:
    project = make_project(register())
    define(project.owner, project, BODY)
    reviewer = make_key(project, "reviewer-agent", verifier=True)
    gh = Gh(tmp_path)
    async with connect(project.mcp_url, project.key) as worker:
        # Nobody said where the work is: the script asks for the address and asks gh nothing.
        silent_task = await ready_task(project, worker)
        silent = run_verify(gh, reviewer, silent_task["ref"], None)
        assert silent.returncode != 0 and "does not say which pull request" in silent.stderr
        assert gh.asked() == []
        # The worker said it when it finished, and the script finds it on the item.
        _, _, task = await make_tree(worker)
        await ok(worker, "claim_node", node=task["ref"], pull_request=ADDRESS)
        await ok(worker, "set_check", node=task["ref"], item=TESTS, evidence="Ran them")
        await ok(worker, "finish_work", node=task["ref"], outcome="blocked", note="Ready.")
        found = run_verify(gh, reviewer, task["ref"], None)
        assert found.returncode == 0, found.stderr
        (asked,) = gh.asked()
        assert asked.startswith(f"pr view {ADDRESS} --json ")
        assert (await ticks(worker, task["ref"]))[MERGED]["evidence"] == SEEN


async def test_a_pull_request_that_is_not_good_ticks_nothing_and_says_why(tmp_path: Path) -> None:
    project = make_project(register())
    define(project.owner, project, BODY)
    reviewer = make_key(project, "reviewer-agent", verifier=True)
    gh = Gh(tmp_path)
    async with connect(project.mcp_url, project.key) as worker:
        task = await ready_task(project, worker)
        failing = {
            **GOOD,
            "statusCheckRollup": [
                {
                    "name": "tests",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                    "__typename": "CheckRun",
                }
            ],
        }
        for view, says in (
            ({**GOOD, "state": "OPEN", "mergedAt": None}, "is still open, not merged."),
            ({**GOOD, "state": "CLOSED", "mergedAt": None}, "is closed, not merged."),
            (failing, "has checks that did not pass: tests."),
            ({**GOOD, "statusCheckRollup": []}, "reports no checks at all"),
        ):
            gh.say(view)
            result = run_verify(gh, reviewer, task["ref"])
            assert result.returncode == 1, (view, result.stdout, result.stderr)
            assert result.stdout == ""
            assert result.stderr.startswith("Nothing was ticked: ") and says in result.stderr
            assert (await ticks(worker, task["ref"]))[MERGED]["checked"] is False
        # The owner of the run may accept a merged pull request that has no checks.
        gh.say({**GOOD, "statusCheckRollup": []})
        accepted = run_verify(gh, reviewer, task["ref"], ADDRESS, "--allow-no-checks")
        assert accepted.returncode == 0, accepted.stderr
        evidence = (await ticks(worker, task["ref"]))[MERGED]["evidence"]
        assert (
            evidence
            == f"{ADDRESS} merged 2026-11-22T17:30:00Z, merge commit 0123456789, no checks reported"
        )


async def test_gh_missing_or_unable_to_read_the_pull_request_ticks_nothing(tmp_path: Path) -> None:
    project = make_project(register())
    define(project.owner, project, BODY)
    reviewer = make_key(project, "reviewer-agent", verifier=True)
    gh = Gh(tmp_path)
    async with connect(project.mcp_url, project.key) as worker:
        task = await ready_task(project, worker)
        refused = run_verify(
            gh,
            reviewer,
            task["ref"],
            fail="GraphQL: Could not resolve to a PullRequest (not found)",
        )
        assert refused.returncode == 2
        said = (
            f"gh could not read {ADDRESS}: GraphQL: Could not resolve to a PullRequest (not found)"
        )
        assert f"{said}. Nothing was ticked." in refused.stderr
        # With no gh on the PATH at all, it says how to get one and that Vrum holds no login.
        (tmp_path / "empty").mkdir()
        missing = run_verify(gh, reviewer, task["ref"], bare_path=True)
        assert missing.returncode == 2
        assert "Install it from https://cli.github.com" in missing.stderr
        assert "Vrum holds no GitHub login" in missing.stderr
        assert missing.stderr.rstrip().endswith("Nothing was ticked.")
        assert (await ticks(worker, task["ref"]))[MERGED]["checked"] is False


async def test_a_key_that_may_not_verify_is_refused_and_the_script_says_so(tmp_path: Path) -> None:
    project = make_project(register())
    define(project.owner, project, BODY)
    plain = make_key(project, "another-agent")
    gh = Gh(tmp_path)
    async with connect(project.mcp_url, project.key) as worker:
        task = await ready_task(project, worker)
        # A key that is not a verifier's.
        notverifier = run_verify(gh, plain, task["ref"])
        assert notverifier.returncode == 1
        assert f"Vrum refused to tick {MERGED!r}: " in notverifier.stderr
        assert "only a verifier can judge" in notverifier.stderr
        # The worker's own key, even if it were a verifier, did the work.
        assert (await ticks(worker, task["ref"]))[MERGED]["checked"] is False
    own = make_key(project, "did-it-and-verifies", verifier=True)
    async with connect(project.mcp_url, own) as doer:
        _, _, mine = await make_tree(doer)
        await ok(doer, "claim_node", node=mine["ref"])
        refused = run_verify(gh, own, mine["ref"])
        assert refused.returncode == 1 and "your own work" in refused.stderr
        assert (await ticks(doer, mine["ref"]))[MERGED]["checked"] is False


async def test_it_ticks_nothing_when_there_is_no_github_check_and_refuses_a_bad_address(
    tmp_path: Path,
) -> None:
    project = make_project(register())
    define(project.owner, project, f"- {TESTS}\n- [verifier] {REVIEWED}\n- {DOCS}")
    reviewer = make_key(project, "reviewer-agent", verifier=True)
    gh = Gh(tmp_path)
    async with connect(project.mcp_url, project.key) as worker:
        task = await ready_task(project, worker)
        result = run_verify(gh, reviewer, task["ref"])
        assert result.returncode == 0
        assert (
            result.stdout.strip()
            == f"{task['ref']} has no [github] check, so there is nothing to tick."
        )
        assert (await ticks(worker, task["ref"]))[REVIEWED]["checked"] is False
        bad = run_verify(gh, reviewer, task["ref"], "https://github.com/acme/widgets/issues/12")
        assert bad.returncode != 0
        assert (
            "Give the pull request's address, such as https://github.com/owner/repo/pull/12."
            in (bad.stderr)
        )
        assert gh.asked() == [a for a in gh.asked() if a.startswith("pr view ")]
        assert len(gh.asked()) == 1  # only the first run asked gh at all
