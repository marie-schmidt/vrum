"""M41: where the work is (repository, branch, commit and pull request on claims and handoffs), the
run label and client name on a claim, and a key's expiry day (against the running containers,
through a real MCP client and the board's own HTTP API).

What has to hold: what a holder says is kept with the claim and the handoff note and is shown by
the tools; a handoff that says nothing is as before; a value that is not what it should be, and a
secret, is refused in plain words and nothing is stored; the run label never changes who anything
is recorded as; the words travel through an export and an import and into the Markdown copy; the
database refuses a bad value on its own; and a key whose last day has passed is refused in words, on
its next request and on a connection that is already open, until the owner extends it. Each test
works in a project of its own."""

import copy
import json
from typing import Any

import anyio
import pytest
from mcp import Client

from tests.helpers import connect, export_everything, make_tree, ok, refused, run_sql
from tests.test_accounts_integration import messages
from tests.test_checks_integration import A_TOKEN, sql_rows, words_of
from tests.world import HARNESS_AGENT, make_key, make_project, register
from vrum.markdown import render

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

REPOSITORY = "https://github.com/acme/widgets"
BRANCH = "feature/m41-where-the-work-is"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PULL_REQUEST = "https://github.com/acme/widgets/pull/12"
RUN = "run-2026-11-22-a"
CLIENT = "claude-code"
ALL = {
    "repository": REPOSITORY,
    "branch": BRANCH,
    "commit": COMMIT,
    "pull_request": PULL_REQUEST,
}
FIELDS = ("repository", "branch", "commit", "pull_request", "run_label", "client_name")


def said(row: dict[str, Any]) -> dict[str, Any]:
    """What a claim, handoff or event says about where the work is and which run it is."""
    return {field: row[field] for field in FIELDS if row.get(field)}


async def a_task(client: Client) -> dict[str, Any]:
    _, _, task = await make_tree(client)
    return task


async def test_what_a_holder_says_is_kept_with_the_claim_and_the_handoff_and_shown() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        claim = await ok(
            agent, "claim_node", node=task["ref"], run_label=RUN, client_name=CLIENT, **ALL
        )
        assert said(claim) == {**ALL, "run_label": RUN, "client_name": CLIENT}

        holding = (await ok(agent, "who_is_working"))["agents"][0]["holding"][0]
        assert holding["node"] == task["ref"]
        assert said(holding) == said(claim)
        detail = await ok(agent, "get_node", node=task["ref"])
        assert said(detail["where"]) == said(claim)

        # Renewing may say where the work has got to; what it leaves out stays.
        newer = "89abcdef0123456789abcdef0123456789abcdef"
        renewed = await ok(agent, "renew_claim", node=task["ref"], commit=newer, branch="next-one")
        assert said(renewed) == {
            **said(claim),
            "commit": newer,
            "branch": "next-one",
        }

        finished = await ok(
            agent,
            "finish_work",
            node=task["ref"],
            outcome="blocked",
            note="Built. Ready for a verifier.",
            pull_request="https://github.com/acme/widgets/pull/13",
        )
        handoff = finished["handoff"]
        # The handoff keeps what the claim said, with what was said last on top, and the run.
        assert said(handoff) == {
            **said(renewed),
            "pull_request": "https://github.com/acme/widgets/pull/13",
        }
        after = await ok(agent, "get_node", node=task["ref"])
        assert said(after["last_handoff"]) == said(handoff)
        assert said(after["where"]) == said(handoff)
        # With no claim left, nobody holds it, and the tools say so.
        assert (await ok(agent, "who_is_working"))["agents"][0]["holding"] == []
        last = (await ok(agent, "who_is_working"))["agents"][0]["last_handoff"]
        assert said(last) == said(handoff)

        events = (await ok(agent, "node_history", node=task["ref"]))["items"]
        by_action = {e["action"]: e for e in events}
        assert said(by_action["claimed"]["after"]) == said(claim)
        assert said(by_action["claim_renewed"]["after"]) == said(renewed)
        assert said(by_action["handoff"]["after"]) == said(handoff)
        # Taking it over does not start from the last holder's words.
        other = make_key(project, "second-agent")
        async with connect(project.mcp_url, other) as second:
            await ok(
                second,
                "update_node",
                node=task["ref"],
                status="new",
                expected_version=after["version"],
            )
            taken = await ok(second, "next_work", run_label="run-b", branch="other-branch")
            assert said(taken["claim"]) == {"run_label": "run-b", "branch": "other-branch"}
            # They are told where the earlier work was, in the handoffs they receive.
            assert said(taken["context"]["handoffs"][0]) == said(handoff)


async def test_a_claim_that_lapsed_and_is_taken_over_starts_with_only_the_new_holders_words() -> (
    None
):
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        await ok(
            agent,
            "claim_node",
            node=task["ref"],
            lease_seconds=1,
            branch="first-branch",
            run_label="run-one",
            pull_request=PULL_REQUEST,
        )
        await anyio.sleep(1.5)
        # Lapsed, with nobody taking it over, the old words are still on it.
        lapsed = (await ok(agent, "who_is_working"))["agents"][0]["lapsed"][0]
        assert said(lapsed) == {
            "branch": "first-branch",
            "run_label": "run-one",
            "pull_request": PULL_REQUEST,
        }
        other = make_key(project, "taking-over")
        async with connect(project.mcp_url, other) as second:
            taken = await ok(second, "claim_node", node=task["ref"], branch="second-branch")
            assert said(taken) == {"branch": "second-branch"}
            held = (await ok(second, "get_node", node=task["ref"]))["where"]
            assert said(held) == {"branch": "second-branch"}


async def test_a_handoff_that_says_nothing_is_exactly_as_before() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        claim = await ok(agent, "claim_node", node=task["ref"])
        assert said(claim) == {}
        await ok(agent, "renew_claim", node=task["ref"])
        done = await ok(agent, "release_claim", node=task["ref"], note="Not now.")
        assert said(done) == {}
        events = (await ok(agent, "node_history", node=task["ref"]))["items"]
        for event in events:
            if event["action"] in ("claimed", "claim_renewed", "handoff", "claim_released"):
                assert not any(
                    field in (event["after"] | (event["before"] or {})) for field in FIELDS
                ), event
        assert (await ok(agent, "get_node", node=task["ref"]))["where"] is None
        # Empty words are the same as nothing: they are not stored and are not refused.
        again = await ok(agent, "claim_node", node=task["ref"], branch="  ", run_label="")
        assert said(again) == {}


BAD = [
    ("repository", "http://github.com/acme/widgets"),
    ("repository", "https://user:hunter2@github.com/acme/widgets"),
    ("repository", "https://github.com/acme/widgets?tab=readme"),
    ("repository", "github.com/acme/widgets"),
    ("branch", "-rf"),
    ("branch", "feature branch"),
    ("branch", "feature/../main"),
    ("branch", "a;b"),
    ("branch", "name.lock"),
    ("commit", "not-a-hash"),
    ("commit", "abc12"),
    ("pull_request", "https://github.com/acme/widgets"),
    ("pull_request", "https://github.com/acme/widgets/pull/0"),
    ("pull_request", "https://github.com/acme/widgets/pull/12/files"),
    ("pull_request", "https://github.com/../x/pull/5"),
    ("repository", "https://github.com/acme/../widgets"),
    ("pull_request", "javascript:alert(1)"),
    ("run_label", "run\nlabel"),
    ("run_label", "x" * 81),
    ("client_name", "$(whoami)"),
]


@pytest.mark.parametrize(("field", "value"), BAD)
async def test_a_value_that_is_not_what_it_should_be_is_refused_and_nothing_is_kept(
    field: str, value: str
) -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        words = words_of(await refused(agent, "claim_node", node=task["ref"], **{field: value}))
        assert field in words and "Nothing was changed" in words, words
        assert "hunter2" not in words
        # Not claimed, so there is nothing stored and the item is free as before.
        assert (await ok(agent, "who_is_working"))["agents"] == []
        assert sql_rows(project, "SELECT 1 FROM claims") == []


async def test_a_secret_in_any_of_them_is_refused_and_never_repeated() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        for field in ("branch", "run_label", "pull_request", "repository"):
            said_back = await refused(agent, "claim_node", node=task["ref"], **{field: A_TOKEN})
            assert A_TOKEN not in said_back
        assert sql_rows(project, "SELECT 1 FROM events WHERE action = 'claimed'") == []


async def test_the_other_ways_of_leaving_work_check_them_too() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        await ok(agent, "claim_node", node=task["ref"], branch="keep-me")
        for tool, extra in (
            ("renew_claim", {}),
            ("release_claim", {"note": "n"}),
            ("finish_work", {"outcome": "released", "note": "n"}),
        ):
            words = words_of(await refused(agent, tool, node=task["ref"], commit="zzz", **extra))
            assert "commit" in words and "Nothing was changed" in words
        # Still held, and still saying what it said.
        held = (await ok(agent, "get_node", node=task["ref"]))["where"]
        assert said(held) == {"branch": "keep-me"}


async def test_a_run_label_never_changes_who_anything_is_recorded_as() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        await ok(agent, "claim_node", node=task["ref"], run_label="run-one", client_name="x-client")
        await ok(agent, "release_claim", node=task["ref"], note="Done for now.")
        # A second instance of the same key, with a label that looks like another agent.
        await ok(agent, "claim_node", node=task["ref"], run_label="marie", client_name="person")
        note = await ok(
            agent, "add_note", text="A thing we learned", why="Saw it", nodes=[task["ref"]]
        )
        assert note["recorded_by"] == HARNESS_AGENT and note["recorded_by_kind"] == "agent"
        events = (await ok(agent, "node_history", node=task["ref"]))["items"]
        assert {(e["actor"], e["actor_kind"]) for e in events if e["action"] == "claimed"} == {
            (HARNESS_AGENT, "agent")
        }
        holder = (await ok(agent, "who_is_working"))["agents"]
        assert [(a["actor"], a["kind"]) for a in holder] == [(HARNESS_AGENT, "agent")]
        # Only the holder, by identity, can release it: the label opens nothing.
        other = make_key(project, "other-agent")
        async with connect(project.mcp_url, other) as second:
            said_back = words_of(
                await refused(
                    second, "release_claim", node=task["ref"], note="Mine now", run_label="marie"
                )
            )
            assert HARNESS_AGENT in said_back


async def test_the_words_travel_through_an_export_an_import_and_the_markdown_copy() -> None:
    source = make_project(register())
    async with connect(source.mcp_url, source.key) as agent:
        task = await a_task(agent)
        await ok(agent, "claim_node", node=task["ref"], run_label=RUN, client_name=CLIENT, **ALL)
        await ok(
            agent,
            "finish_work",
            node=task["ref"],
            outcome="blocked",
            note="Ready for a verifier.",
        )
        file = await export_everything(agent)
    handoffs = [e for e in file["events"] if e["action"] == "handoff"]
    assert said(handoffs[0]["after"]) == {**ALL, "run_label": RUN, "client_name": CLIENT}

    target = make_project(register())
    async with connect(target.mcp_url, target.key) as client:
        await ok(client, "import_graph", document=file)
        again = await export_everything(client)
        assert [e["after"] for e in again["events"] if e["action"] == "handoff"] == [
            e["after"] for e in handoffs
        ]
        imported = (await ok(client, "list_nodes", kind="task"))["items"][0]
        assert said((await ok(client, "get_node", node=imported["ref"]))["last_handoff"]) == said(
            handoffs[0]["after"]
        )

    files = render(file)
    page = next(text for path, text in files.items() if path.startswith("tasks/"))
    assert "## Where the work is" in page
    for line in (
        f"- Repository: {REPOSITORY}",
        f"- Branch: {BRANCH}",
        f"- Commit: {COMMIT}",
        f"- Pull request: {PULL_REQUEST}",
        f"- Run: {RUN} ({CLIENT})",
    ):
        assert line in page, line


async def test_an_import_refuses_a_file_whose_events_say_something_else() -> None:
    source = make_project(register())
    async with connect(source.mcp_url, source.key) as agent:
        task = await a_task(agent)
        await ok(agent, "claim_node", node=task["ref"], **ALL)
        await ok(agent, "release_claim", node=task["ref"], note="Back.")
        file = await export_everything(agent)
    for field, value in (("branch", "-rf"), ("pull_request", "http://elsewhere/pull/1")):
        bad = copy.deepcopy(file)
        for event in bad["events"]:
            if event["action"] == "handoff":
                event["after"][field] = value
        target = make_project(register())
        async with connect(target.mcp_url, target.key) as client:
            words = words_of(await refused(client, "import_graph", document=bad))
            assert field in words and "events[" in words, words
            assert (await ok(client, "list_nodes"))["items"] == []


async def test_the_database_itself_refuses_a_bad_value_in_a_claim() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        task = await a_task(agent)
        await ok(agent, "claim_node", node=task["ref"])
    for column, value, constraint in (
        ("branch", "-rf", "claim_branch_is_a_name"),
        ("commit", "XYZ", "claim_commit_is_a_hash"),
        ("pull_request", "http://x/y/z/pull/1", "claim_pull_request_is_an_address"),
        ("repository", "https://u:p@h/x", "claim_repository_is_an_address"),
        ("run_label", "bad\nlabel", "claim_run_label_is_a_label"),
        ("client_name", "-no", "claim_client_name_is_a_label"),
    ):
        broken = run_sql(
            f"UPDATE claims SET {column} = $x${value}$x$ WHERE project_id = {project.id} "
            f"AND node = '{task['ref']}'",
            plain=True,
        )
        assert broken.returncode != 0 and constraint in broken.stderr, (column, broken.stderr)


# -- a key's last day ------------------------------------------------------------------------------


def keys_path(project: Any) -> str:
    return f"/board/api/projects/{project.id}/keys"


def key_row(project: Any, key_id: int) -> dict[str, Any]:
    listed = project.owner.call("GET", keys_path(project)).body["keys"]
    return next(row for row in listed if row["id"] == key_id)


def end_yesterday(key_id: int) -> None:
    """Move a key's last day into the past, as the passing of time would."""
    done = run_sql(
        f"UPDATE agent_keys SET expires_on = (now() AT TIME ZONE 'UTC')::date - 1 "
        f"WHERE id = {key_id}",
        plain=True,
        project=None,
    )
    assert done.returncode == 0, done.stderr


def tomorrow() -> str:
    return (
        run_sql("SELECT ((now() AT TIME ZONE 'UTC')::date + 1)::text", plain=True, project=None)
        .stdout.strip()
        .splitlines()[-1]
    )


async def test_a_key_with_no_expiry_is_untouched_and_one_with_a_future_day_works() -> None:
    owner = register()
    project = make_project(owner)
    base = keys_path(project)
    plain_key = owner.call("POST", base, {"name": "plain-agent"})
    assert plain_key.status == 201
    assert plain_key.body["key"]["expires_on"] is None and plain_key.body["key"]["expired"] is False
    dated = owner.call("POST", base, {"name": "dated-agent", "expires_on": tomorrow()})
    assert dated.status == 201, dated.body
    assert dated.body["key"]["expires_on"] == tomorrow() and dated.body["key"]["expired"] is False
    async with connect(project.mcp_url, dated.body["secret"]) as agent:
        await ok(agent, "list_nodes")
    async with connect(project.mcp_url, plain_key.body["secret"]) as agent:
        await ok(agent, "list_nodes")


async def test_an_expired_key_is_refused_in_words_and_the_owner_can_extend_it() -> None:
    owner = register()
    project = make_project(owner)
    base = keys_path(project)
    made = owner.call("POST", base, {"name": "short-lived", "expires_on": tomorrow()})
    secret, key_id = made.body["secret"], made.body["key"]["id"]
    async with connect(project.mcp_url, secret) as open_connection:
        await ok(open_connection, "list_nodes")
        end_yesterday(key_id)
        # The connection is open and was set up while the key was good: its next call is refused.
        with pytest.raises(BaseException) as already_open:
            await open_connection.call_tool("list_nodes", {})
        said_back = messages(already_open.value)
        assert "expired after" in said_back and "extend it" in said_back, said_back
    # A new client is refused as it connects, in the same words, not as a wrong key.
    with pytest.raises(BaseException) as new_client:
        async with connect(project.mcp_url, secret) as client:
            await client.call_tool("list_nodes", {})
    assert "expired after" in messages(new_client.value)
    assert "not valid" not in messages(new_client.value)
    shown = key_row(project, key_id)
    assert shown["expired"] is True and shown["expires_on"] is not None

    # A key of the project that has no expiry is not touched.
    async with connect(project.mcp_url, project.key) as client:
        await ok(client, "list_nodes")

    # The owner extends it, and it works again at once.
    extended = owner.call("PATCH", f"{base}/{key_id}", {"expires_on": tomorrow()})
    assert extended.status == 200, extended.body
    assert extended.body["key"]["expired"] is False
    async with connect(project.mcp_url, secret) as client:
        await ok(client, "list_nodes")
    # And clearing it makes it a key that never expires.
    end_yesterday(key_id)
    cleared = owner.call("PATCH", f"{base}/{key_id}", {"expires_on": None})
    assert cleared.status == 200 and cleared.body["key"]["expires_on"] is None
    async with connect(project.mcp_url, secret) as client:
        await ok(client, "list_nodes")


async def test_only_a_day_that_has_not_passed_is_taken_and_only_the_owner_changes_it() -> None:
    owner = register()
    project = make_project(owner)
    base = keys_path(project)
    key_id = owner.call("POST", base, {"name": "careful"}).body["key"]["id"]
    for bad, expected in (
        ("2001-01-01", "already passed"),
        ("next week", "YYYY-MM-DD"),
        ("2030-02-30", "YYYY-MM-DD"),
        (20301231, "YYYY-MM-DD"),
    ):
        reply = owner.call("PATCH", f"{base}/{key_id}", {"expires_on": bad})
        assert reply.status == 400 and expected in reply.body["message"], (bad, reply.body)
        assert "Nothing was changed" in reply.body["message"]
    assert owner.call("POST", base, {"name": "born-late", "expires_on": "2001-01-01"}).status == 400
    assert key_row(project, key_id)["expires_on"] is None

    stranger = register()
    reply = stranger.call("PATCH", f"{base}/{key_id}", {"expires_on": tomorrow()})
    assert reply.status in (403, 404)
    assert key_row(project, key_id)["expires_on"] is None


async def test_a_script_is_stopped_when_its_key_runs_out_while_it_runs() -> None:
    owner = register()
    project = make_project(owner)
    base = keys_path(project)
    made = owner.call(
        "POST",
        base,
        {"name": "scripted", "expires_on": tomorrow(), "code_mode": True, "tools": ["list_nodes"]},
    )
    assert made.status == 201, made.body
    key_id = made.body["key"]["id"]
    async with connect(project.mcp_url, made.body["secret"]) as agent:
        result = await ok(agent, "run", code="return (await vrum.list_nodes({})).items.length;")
        assert result["result"] == 0
        end_yesterday(key_id)
        # The next script is refused at the door, in the same words as any request.
        with pytest.raises(BaseException) as stopped:
            await agent.call_tool("run", {"code": "return 1;"})
        assert "expired after" in messages(stopped.value)


def test_the_keys_list_never_holds_the_key_itself() -> None:
    owner = register()
    project = make_project(owner)
    made = owner.call("POST", keys_path(project), {"name": "private", "expires_on": tomorrow()})
    assert made.body["secret"] not in json.dumps(owner.call("GET", keys_path(project)).body)
