"""scripts/verify_pr.py without a server or GitHub: when is a pull request, as `gh pr view --json`
describes it, good enough to tick a [github] check, and what evidence does it write? Running the
whole script against a real Vrum with a stand-in `gh` is in test_verify_pr_integration.py."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from vrum.checks import pull_request_address

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
ADDRESS = "https://github.com/acme/widgets/pull/12"


def load() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location("verify_pr", SCRIPTS / "verify_pr.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


verify_pr = load()


def merged(**changes: Any) -> dict[str, Any]:
    view: dict[str, Any] = {
        "url": ADDRESS,
        "state": "MERGED",
        "isDraft": False,
        "mergedAt": "2026-11-22T17:30:00Z",
        "mergeCommit": {"oid": "0123456789abcdef0123"},
        "headRefOid": "fedcba9876543210fedc",
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "tests",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "lint",
                "status": "COMPLETED",
                "conclusion": "NEUTRAL",
            },
            {"__typename": "StatusContext", "context": "ci/build", "state": "SUCCESS"},
        ],
    }
    view.update(changes)
    return view


def test_a_merged_pull_request_with_passing_checks_is_good_and_says_what_was_seen() -> None:
    good, words = verify_pr.judge(ADDRESS, merged())
    assert good
    assert words == (
        f"{ADDRESS} merged 2026-11-22T17:30:00Z, merge commit 0123456789, checks passed 3 of 3"
    )
    # The server accepts exactly this form: it starts with the address.
    assert pull_request_address(words) == ADDRESS


def test_the_evidence_falls_back_to_the_head_commit_when_there_is_no_merge_commit() -> None:
    good, words = verify_pr.judge(ADDRESS, merged(mergeCommit=None))
    assert good and "merge commit fedcba9876," in words


@pytest.mark.parametrize(
    ("changes", "says"),
    [
        ({"state": "OPEN"}, "still open"),
        ({"state": "CLOSED"}, "closed, not merged"),
        ({"state": ""}, "in an unknown state"),
        ({"isDraft": True}, "is a draft"),
    ],
)
def test_a_pull_request_that_is_not_merged_is_never_good(
    changes: dict[str, Any], says: str
) -> None:
    good, words = verify_pr.judge(ADDRESS, merged(**changes))
    assert not good and says in words and words.startswith(ADDRESS)


def test_a_failing_a_cancelled_or_unfinished_check_is_never_good() -> None:
    for entry, says in (
        (
            {
                "__typename": "CheckRun",
                "name": "tests",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            },
            "did not pass: tests",
        ),
        (
            {
                "__typename": "CheckRun",
                "name": "tests",
                "status": "COMPLETED",
                "conclusion": "CANCELLED",
            },
            "did not pass: tests",
        ),
        (
            {
                "__typename": "CheckRun",
                "name": "tests",
                "status": "COMPLETED",
                "conclusion": "TIMED_OUT",
            },
            "did not pass: tests",
        ),
        (
            {"__typename": "CheckRun", "name": "tests", "status": "COMPLETED", "conclusion": ""},
            "did not pass: tests",
        ),
        (
            {"__typename": "CheckRun", "name": "slow", "status": "IN_PROGRESS", "conclusion": ""},
            "have not finished: slow",
        ),
        (
            {"__typename": "StatusContext", "context": "ci/build", "state": "FAILURE"},
            "did not pass: ci/build",
        ),
        (
            {"__typename": "StatusContext", "context": "ci/build", "state": "PENDING"},
            "did not pass: ci/build",
        ),
    ):
        view = merged()
        view["statusCheckRollup"] = [*view["statusCheckRollup"], entry]
        good, words = verify_pr.judge(ADDRESS, view)
        assert not good and says in words, (entry, words)


def test_a_merged_pull_request_with_no_checks_needs_the_owner_of_the_run_to_say_so() -> None:
    bare = merged(statusCheckRollup=[])
    good, words = verify_pr.judge(ADDRESS, bare)
    assert not good and "--allow-no-checks" in words
    good, words = verify_pr.judge(ADDRESS, bare, allow_no_checks=True)
    assert good and words.endswith("no checks reported")
    assert pull_request_address(words) == ADDRESS
    missing = {k: v for k, v in bare.items() if k != "statusCheckRollup"}
    assert not verify_pr.judge(ADDRESS, missing)[0]
