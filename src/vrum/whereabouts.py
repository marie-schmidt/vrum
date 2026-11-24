"""Where a piece of work is, and which run is doing it (M41).

A claim, a handoff and `finish_work` may say, in plain optional words, where the work lives: the
repository's address, the branch, the commit and the pull request's address. A claim may also carry
a run label and a client name (`run-2026-11-22-a`, `claude-code`). All of it is what the agent says
about itself: Vrum never reads a checkout, never creates a branch or a worktree and never asks
GitHub (a container cannot reach any of them), and the run label and client name are an audit label,
never an identity or a permission: the key's name is still who did the work.

The values are shown to people and to other agents, and a client may well put a branch name into a
command, so they are held to a strict, short alphabet instead of being let through: a branch cannot
start with `-` or hold a space, a quote or a shell character, an address is `https` with no
password in it, and a secret in any of them is refused."""

import re
from typing import Any

from pydantic import BaseModel, Field

from vrum.common import Refusal, refuse_secrets

# Every field, in the order they are shown. The first four say where the work is, the last two
# which run is doing it.
PLACE_FIELDS = ("repository", "branch", "commit", "pull_request")
RUN_FIELDS = ("run_label", "client_name")
WHERE_FIELDS = PLACE_FIELDS + RUN_FIELDS

MOST_REPOSITORY_CHARS = 300
MOST_BRANCH_CHARS = 200
MOST_RUN_LABEL_CHARS = 80
MOST_CLIENT_NAME_CHARS = 60

# These patterns are repeated, byte for byte, in the claims table of schema.sql, so that the
# database refuses what this module would.
_REPOSITORY = re.compile(r"https://[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?(?:/[A-Za-z0-9._~%/-]*)?")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+=,#@-]*")
_COMMIT = re.compile(r"[0-9a-f]{7,64}")
_PULL_REQUEST_WHOLE = re.compile(
    r"https://[A-Za-z0-9][A-Za-z0-9.-]*/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*"
)
# A path segment of only dots would make an address mean something other than what it says.
_DOT_SEGMENT = re.compile(r"/\.\.?(?:/|$)")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:/+#@()=,-]*")
# A pull request address at the start of a piece of evidence, with whatever follows it left off.
_PULL_REQUEST_AT_START = re.compile(
    r"^(https://[A-Za-z0-9.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*)"
    r"(?:[/?#]\S*)?(?=\s|$)"
)


def pull_request_address(evidence: str | None) -> str | None:
    """The GitHub pull request address a piece of evidence starts with (without anything after its
    number), or None when it does not start with one."""
    match = _PULL_REQUEST_AT_START.match((evidence or "").strip())
    return None if match is None else match.group(1)


class Whereabouts(BaseModel):
    """What the holder said about where the work is and which run it is. Not checked against a
    checkout or GitHub. Empty when nobody said."""

    repository: str | None = Field(default=None, description="Repository address, as said.")
    branch: str | None = Field(default=None, description="Branch, as said.")
    commit: str | None = Field(default=None, description="Commit hash, as said.")
    pull_request: str | None = Field(default=None, description="Pull request address, as said.")
    run_label: str | None = Field(default=None, description="The run's label, as said.")
    client_name: str | None = Field(default=None, description="The client's name, as said.")


def _bad(field: str, wanted: str) -> Refusal:
    """The refusal for a value that is not what it should be. It never repeats the value: an
    address may hold a password."""
    return Refusal(f"{field} must be {wanted}. Nothing was changed.")


def _branch_is_a_name(branch: str) -> bool:
    return not (
        ".." in branch
        or "//" in branch
        or branch.endswith(("/", ".", ".lock"))
        or any(part.startswith(".") or part.endswith(".lock") for part in branch.split("/"))
    )


def clean_whereabouts(
    *,
    repository: str | None = None,
    branch: str | None = None,
    commit: str | None = None,
    pull_request: str | None = None,
    run_label: str | None = None,
    client_name: str | None = None,
) -> dict[str, str]:
    """The fields that were given, checked and tidied, by name; none that were left out or blank.
    Anything that is not what it should be, and any secret, is refused in plain words and nothing
    is stored."""
    given: dict[str, Any] = {
        "repository": repository,
        "branch": branch,
        "commit": commit,
        "pull_request": pull_request,
        "run_label": run_label,
        "client_name": client_name,
    }
    cleaned: dict[str, str] = {}
    for field, value in given.items():
        if value is None or not value.strip():
            continue
        cleaned[field] = value.strip()
    refuse_secrets(cleaned)
    if (value := cleaned.get("repository")) is not None and not (
        len(value) <= MOST_REPOSITORY_CHARS
        and _REPOSITORY.fullmatch(value)
        and not _DOT_SEGMENT.search(value)
    ):
        raise _bad(
            "repository",
            "the repository's https address with no password or query in it, such as "
            "https://github.com/owner/repo",
        )
    if (value := cleaned.get("branch")) is not None and not (
        len(value) <= MOST_BRANCH_CHARS and _BRANCH.fullmatch(value) and _branch_is_a_name(value)
    ):
        raise _bad(
            "branch",
            f"a branch name of letters, digits and . _ / + = , # @ - (at most {MOST_BRANCH_CHARS} "
            "characters, starting with a letter or digit)",
        )
    if (value := cleaned.get("commit")) is not None:
        if not _COMMIT.fullmatch(value.lower()):
            raise _bad("commit", "a commit hash of 7 to 64 hex digits")
        cleaned["commit"] = value.lower()
    if (value := cleaned.get("pull_request")) is not None and not (
        _PULL_REQUEST_WHOLE.fullmatch(value) and not _DOT_SEGMENT.search(value)
    ):
        raise _bad(
            "pull_request",
            "the pull request's https address, such as https://github.com/owner/repo/pull/12",
        )
    for field, most in (
        ("run_label", MOST_RUN_LABEL_CHARS),
        ("client_name", MOST_CLIENT_NAME_CHARS),
    ):
        if (value := cleaned.get(field)) is not None and not (
            len(value) <= most and _LABEL.fullmatch(value)
        ):
            raise _bad(
                field,
                f"a short label of letters, digits and . _ : / + # @ ( ) = , - (at most {most} "
                "characters, starting with a letter or digit)",
            )
    return cleaned


def whereabouts_in(row: dict[str, Any]) -> dict[str, str]:
    """The fields a row, or an event's data, holds a value for."""
    return {field: row[field] for field in WHERE_FIELDS if row.get(field)}
