"""Check a GitHub pull request with `gh` and, only if it is merged and every check passed, tick the
[github] checks of a story or task in a running Vrum, as the verifier whose key you hold.

    VRUM_AGENT_KEY=<the key of a verifier> uv run python scripts/verify_pr.py \\
        http://127.0.0.1:8000/mcp task:40 https://github.com/owner/repo/pull/12

Leave the address out and it uses the pull request the worker said the item is in (`pull_request`
on finish_work or claim_node, shown by get_node as `where`); with none said it asks you for one.

Vrum holds no GitHub login and never calls GitHub (the owner's decision, as in Beads, whose gates
run `gh` on the machine of whoever checks). This script is where GitHub is asked: on your own
machine, with the login `gh` already has. It runs `gh pr view`, decides whether the pull request
is merged with passing checks, and writes what it saw as the evidence of the tick, in one fixed
form that anyone can check again with `gh pr view`:

    <the pull request address> merged <time>, merge commit <sha>, checks passed <n> of <n>

The key must be a verifier's (Users page, "Verifies other agents' work") and must not belong to the
agent that did the work on the item: Vrum refuses the tick otherwise and this script says so. Pass
--allow-no-checks to accept a merged pull request that has no checks at all.

Exit status: 0 when it ticked, or when there was nothing to tick; 1 when the pull request is not
good enough or Vrum refused; 2 when `gh` could not tell (not installed, not signed in, no such pull
request)."""

import argparse
import asyncio
import json
import subprocess
import sys
from typing import Any, NoReturn

from vrum_client import connect, said

# vrum_client puts the checkout's src folder on the path, so this runs without installing anything.
from vrum.checks import pull_request_address  # noqa: E402, I001

GH_FIELDS = "url,state,isDraft,mergedAt,mergeCommit,headRefOid,statusCheckRollup"
# What a finished check may have concluded for the pull request to count as passing.
_PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_STATUS_PASSING = {"SUCCESS"}


def _check_name(entry: dict[str, Any]) -> str:
    return str(entry.get("name") or entry.get("context") or "a check")


def judge(address: str, view: dict[str, Any], *, allow_no_checks: bool = False) -> tuple[bool, str]:
    """Whether a pull request, as `gh pr view --json` describes it, is merged with every check
    passed, and the words: the evidence to record when it is, or why it is not good enough."""
    state = str(view.get("state") or "").upper()
    if state != "MERGED":
        what = "still open" if state == "OPEN" else f"{state.lower() or 'in an unknown state'}"
        return False, f"{address} is {what}, not merged."
    if view.get("isDraft"):
        return False, f"{address} is a draft."
    rollup: list[dict[str, Any]] = list(view.get("statusCheckRollup") or [])
    unfinished: list[str] = []
    failed: list[str] = []
    for entry in rollup:
        if "conclusion" in entry or entry.get("__typename") == "CheckRun":
            status = str(entry.get("status") or "").upper()
            conclusion = str(entry.get("conclusion") or "").upper()
            if status and status != "COMPLETED":
                unfinished.append(_check_name(entry))
            elif conclusion not in _PASSING:
                failed.append(_check_name(entry))
        elif str(entry.get("state") or "").upper() not in _STATUS_PASSING:
            failed.append(_check_name(entry))
    if unfinished:
        return False, f"{address} has checks that have not finished: {', '.join(unfinished)}."
    if failed:
        return False, f"{address} has checks that did not pass: {', '.join(failed)}."
    if not rollup and not allow_no_checks:
        return False, (
            f"{address} is merged but reports no checks at all; run again with --allow-no-checks "
            "to accept that."
        )
    commit = str((view.get("mergeCommit") or {}).get("oid") or view.get("headRefOid") or "")
    merged = str(view.get("mergedAt") or "an unknown time")
    checks = f"checks passed {len(rollup)} of {len(rollup)}" if rollup else "no checks reported"
    return True, f"{address} merged {merged}, merge commit {commit[:10] or 'unknown'}, {checks}"


def _cannot_tell(words: str) -> NoReturn:
    print(f"{words} Nothing was ticked.", file=sys.stderr)
    raise SystemExit(2)


def look(address: str) -> dict[str, Any]:
    """What `gh pr view` says about a pull request. Exits with a plain message when `gh` is not
    there, is not signed in, or does not know the pull request."""
    try:
        done = subprocess.run(
            ["gh", "pr", "view", address, "--json", GH_FIELDS],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError:
        _cannot_tell(
            "gh, the GitHub command line, is not installed or not on the PATH. Install it from "
            "https://cli.github.com and run `gh auth login`: Vrum holds no GitHub login, so this "
            "check is made on your machine."
        )
    except subprocess.TimeoutExpired:
        _cannot_tell("gh did not answer within a minute.")
    if done.returncode != 0:
        first = (done.stderr.strip().splitlines() or ["it gave no reason"])[0]
        _cannot_tell(f"gh could not read {address}: {first}.")
    try:
        view = json.loads(done.stdout)
    except ValueError:
        _cannot_tell("gh did not answer with JSON.")
    if not isinstance(view, dict):
        _cannot_tell("gh did not answer with a pull request.")
    return view


async def said_pull_request(url: str, node: str) -> str | None:
    """The pull request the worker said the item is in (get_node's `where`), or None."""
    async with connect(url) as client:
        found = await client.call_tool("get_node", {"node": node})
    if found.is_error or found.structured_content is None:
        raise SystemExit(f"Vrum refused to show {node}: {said(found)}")
    where = found.structured_content.get("where") or {}
    return str(where["pull_request"]) if where.get("pull_request") else None


async def tick(url: str, node: str, evidence: str) -> int:
    """Tick every unticked [github] check of the node with this evidence. Gives the exit status."""
    async with connect(url) as client:
        found = await client.call_tool("get_node", {"node": node})
        if found.is_error or found.structured_content is None:
            print(f"Vrum refused to show {node}: {said(found)}", file=sys.stderr)
            return 1
        checks: list[dict[str, Any]] = found.structured_content.get("checks") or []
        waiting = [c for c in checks if c.get("github") and not c.get("checked")]
        if not any(c.get("github") for c in checks):
            print(f"{node} has no [github] check, so there is nothing to tick.")
            return 0
        if not waiting:
            print(f"Every [github] check of {node} is already ticked.")
            return 0
        status = 0
        for check in waiting:
            result = await client.call_tool(
                "set_check",
                {"node": node, "item": check["item"], "checked": True, "evidence": evidence},
            )
            if result.is_error:
                print(f"Vrum refused to tick {check['text']!r}: {said(result)}", file=sys.stderr)
                status = 1
            else:
                print(f"Ticked {check['text']!r} on {node}.")
        return status


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_pr.py",
        description="Tick the [github] checks of a story or task if a pull request is merged and "
        "its checks passed, as seen with gh on this machine.",
    )
    parser.add_argument("url", help="the server's MCP address, ending in /mcp")
    parser.add_argument("node", help="the story or task, such as task:40")
    parser.add_argument(
        "pull_request",
        nargs="?",
        help="the pull request's address on GitHub (by default the one the item says it is in)",
    )
    parser.add_argument(
        "--allow-no-checks", action="store_true", help="accept a merged pull request with no checks"
    )
    given = parser.parse_args(argv)
    typed = given.pull_request
    if typed is None:
        typed = asyncio.run(said_pull_request(given.url, given.node))
        if typed is None:
            raise SystemExit(
                f"{given.node} does not say which pull request it is in. Give its address, such as "
                "https://github.com/owner/repo/pull/12."
            )
    address = pull_request_address(typed)
    if address is None:
        raise SystemExit(
            "Give the pull request's address, such as https://github.com/owner/repo/pull/12."
        )
    good, words = judge(address, look(address), allow_no_checks=given.allow_no_checks)
    if not good:
        print(f"Nothing was ticked: {words}", file=sys.stderr)
        return 1
    return asyncio.run(tick(given.url, given.node, words))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
