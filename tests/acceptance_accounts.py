"""Chapter 14 of the acceptance scenario: getting in (M15). A person registers in a real browser,
makes a private project and works in it; a person is refused in plain words and cannot be
impersonated; two people and three projects keep their records apart, through the tools and in the
database itself; a project is switched while answers are held back; an agent key made on the board
does a day's work and is then revoked; and no password, session or key is kept or written anywhere
it should not be. Each proof compares what the browser or the API showed with what a real MCP
client or the database says."""

import asyncio
import contextlib
import json
import re

from playwright.async_api import Error as BrowserError
from playwright.async_api import expect

from tests.acceptance_scenario import Failed, chapter, clip, proof
from tests.board_helpers import SLOW, browser_page, made
from tests.helpers import compose, connect, export_everything, ok, refused, run_sql, unique_word
from tests.test_accounts_integration import hashed, held, messages, said, sign_in
from tests.test_board_accounts_integration import (
    create_in_the_browser,
    register_in_the_browser,
    wait_until_healthy,
)
from tests.test_projects_isolation_integration import Side, sql_refused, three_projects
from tests.world import api, make_project, register, server_base

PASSWORD = "a long enough password"


async def accounts() -> None:
    chapter("Getting in: accounts, private projects and agent keys")
    base = server_base()
    try:
        await registering(base)
        await refusals(base)
        await walls()
        await switching()
        await keys()
        await secrets_are_kept()
    except BrowserError as error:
        raise Failed(
            f"M15: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- 1 and 2: register, work, sign out, come back after a restart


async def registering(base: str) -> None:
    word = unique_word()
    email, name = f"{word}@example.test", f"Grace {word}"
    async with browser_page(f"{base}/board", token=None, name=None) as w:
        await w.open("")
        await expect(w.page.locator("#sign-in-form")).to_be_visible(timeout=SLOW)
        no_token_box = await w.page.locator("#token").count() == 0
        await register_in_the_browser(w, name, email, PASSWORD)
        await expect(w.page.locator("#no-projects")).to_be_visible(timeout=SLOW)
        empty_state = "no projects yet" in await w.page.locator("#no-projects").inner_text()
        await w.page.locator("#project-name").fill(f"Compiler {word}")
        await w.page.locator("#create-project").click()
        await expect(w.page.locator("#main-nav")).to_be_visible(timeout=SLOW)
        found = re.search(r"/board/p/(\d+)", w.page.url)
        assert found, w.page.url
        await w.go("#/board")
        epic = await create_in_the_browser(w, "epic", f"Portal {word}")
        story = await create_in_the_browser(w, "story", f"Sign in {word}", epic)
        task = await create_in_the_browser(w, "task", f"Login form {word}", story)
        await w.go("#/access")
        await w.page.locator("#key-label").fill("build agent")
        await w.page.locator("#create-key").click()
        await expect(w.page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
        key = (await w.page.locator("#key-secret").inner_text()).strip()
        async with connect(f"{base}/mcp", key) as agent:
            seen = {node["id"]: node["title"] for node in (await ok(agent, "list_nodes"))["items"]}
        proof(
            "M15",
            "starting with no account, a person registers in the browser (no server token is typed "
            "anywhere), sees a real empty state, creates a private project, and creates an epic, "
            "a story and a task on its board; a project-authorised MCP client sees the same three",
            no_token_box
            and empty_state
            and seen
            == {epic: f"Portal {word}", story: f"Sign in {word}", task: f"Login form {word}"},
            f"project {found.group(1)}, items {epic}, {story}, {task}",
        )
        await w.page.locator("#sign-out").click()
        await expect(w.page.locator("#sign-in-form")).to_be_visible(timeout=SLOW)
        restarted = compose("restart", "app")
        assert restarted.returncode == 0, restarted.stderr
        wait_until_healthy(base)
        await w.page.reload()
        await expect(w.page.locator("#sign-in-form")).to_be_visible(timeout=SLOW)
        await w.page.locator("#account-email").fill(email)
        await w.page.locator("#account-password").fill(PASSWORD)
        await w.page.locator("#sign-in-submit").click()
        await expect(w.page.locator("li.project")).to_have_count(1, timeout=SLOW)
        await w.page.locator("li.project a.open-project").click()
        await w.go("#/board")
        await expect(w.page.locator(f".card[data-id='{task}']")).to_be_visible(timeout=SLOW)
        async with connect(f"{base}/mcp", key) as agent:
            after = [node["id"] for node in (await ok(agent, "list_nodes"))["items"]]
        proof(
            "M15",
            "signing out and back in with the email and password, after the server has restarted, "
            "finds the same project and records, and the agent's key still works",
            sorted(after) == sorted([epic, story, task])
            and found.group(1) in w.page.url
            and await w.page.evaluate("document.cookie") == "",
            "restarted the app container",
        )


# -- 3: refusals, sign-out, expiry, rate limits, other sites


async def refusals(base: str) -> None:
    person = register()
    project = make_project(person, "Refusals")
    before = held(f"SELECT password_hash FROM accounts WHERE email = '{person.email}'")
    wrong = sign_in(base, person.email, "not the password")
    unknown = sign_in(base, f"nobody-{unique_word()}@example.test", "not the password")
    again = api(
        base,
        "POST",
        "/board/api/accounts",
        {"email": person.email, "name": "Other", "password": "another long password"},
    )
    unchanged = held(f"SELECT password_hash FROM accounts WHERE email = '{person.email}'") == before
    proof(
        "M15",
        "a wrong password, an address with no account and a second registration of an address are "
        "refused without exposing credentials, in the same words for the first two, and the "
        "existing account is not changed",
        wrong.status == unknown.status == 401
        and wrong.body == unknown.body
        and again.status == 409
        and "already exists" in said(again)
        and "not the password" not in json.dumps([wrong.body, unknown.body])
        and "another long password" not in json.dumps(again.body)
        and sign_in(base, person.email, person.password).status == 200
        and unchanged,
        f"{wrong.status} {unknown.status} {again.status}",
    )

    old_cookie, old_csrf = person.cookie, person.csrf
    person.call("DELETE", "/board/api/session")
    mine = f"/board/api/projects/{project.id}"
    replays = [
        api(base, "GET", "/board/api/projects", cookie=old_cookie),
        api(
            base,
            "POST",
            f"{mine}/tool/create_node",
            {"actor": "x", "kind": "epic", "title": "Replayed"},
            cookie=old_cookie,
            csrf=old_csrf,
        ),
        api(base, "POST", f"{mine}/keys", {"label": "replayed"}, cookie=old_cookie, csrf=old_csrf),
    ]
    fresh = person.sign_in_again()
    nothing = fresh.call("POST", f"{mine}/tool/list_nodes", {}).body["items"] == []
    expired_person = register()
    run_sql(
        "UPDATE sessions SET created_at = now() - interval '13 hours', expires_at = "
        f"now() - interval '1 hour' WHERE token_hash = {hashed(expired_person.cookie)}",
        project=None,
    )
    expired = expired_person.call("GET", "/board/api/projects")
    garbage = api(base, "GET", "/board/api/projects", cookie="vrs_not-a-session")
    proof(
        "M15",
        "an invalid or expired session is refused, and signing out ends the session on the server: "
        "replaying the old one cannot read or write project data",
        all(reply.status == 401 for reply in replays)
        and nothing
        and expired.status == 401
        and "session has ended" in said(expired)
        and garbage.status == 401,
        f"replays {[reply.status for reply in replays]}, expired {expired.status}",
    )

    target = register()
    for _ in range(5):
        sign_in(base, target.email, "wrong guess")
    blocked = sign_in(base, target.email, target.password)
    other = register().call("GET", "/board/api/projects")
    csrf_attempts = [
        api(base, "POST", "/board/api/projects", {"name": "x"}, cookie=fresh.cookie),
        api(
            base,
            "POST",
            "/board/api/projects",
            {"name": "x"},
            cookie=fresh.cookie,
            csrf=fresh.csrf,
            origin="https://evil.example",
        ),
        api(
            base,
            "POST",
            "/board/api/session",
            {"email": fresh.email, "password": fresh.password},
            origin="https://evil.example",
        ),
    ]
    proof(
        "M15",
        "repeated failed sign-ins are rate limited, and a write from another site is refused "
        "(no session secret, another origin, a sign-in from another site)",
        blocked.status == 429
        and "too many failed attempts" in said(blocked)
        and other.status == 200
        and [reply.status for reply in csrf_attempts] == [403, 403, 403]
        and [one["name"] for one in fresh.call("GET", "/board/api/projects").body["projects"]]
        == ["Refusals"],
        f"{blocked.status}; {[reply.status for reply in csrf_attempts]}",
    )


# -- 3, 4 and 7: the walls


async def walls() -> None:
    async with three_projects() as (a1, a2, b1, there):
        sides = [a1, a2, b1]
        owner_a, owner_b = a1.project.owner, b1.project.owner
        mine = [one["name"] for one in owner_a.call("GET", "/board/api/projects").body["projects"]]
        theirs = [
            one["name"] for one in owner_b.call("GET", "/board/api/projects").body["projects"]
        ]
        absent = owner_b.call("GET", f"/board/api/projects/{a1.project.id + 100_000}")
        taken = owner_b.call("GET", f"/board/api/projects/{a1.project.id}")
        forged = await a1.client.call_tool(
            "list_nodes", {"limit": 200, "project_id": b1.project.id, "owner": "the other account"}
        )
        guessed = await refused(a1.client, "get_node", node_id=there.node)
        proof(
            "M15",
            "two accounts, two projects of one and one of the other: each account's list holds "
            "only its own projects; another owner's project, a guessed record id and forged "
            "owner or project arguments are refused without revealing or changing anything",
            mine == ["One", "Two"]
            and theirs == ["Three"]
            and taken.status == absent.status == 404
            and taken.body == absent.body
            and f"no node with id {there.node}" in guessed
            and (forged.is_error or b1.word not in json.dumps(forged.structured_content)),
            f"{mine} / {theirs}",
        )

        for side in sides:
            await reindexed_side(side)
        checks = []
        for side in sides:
            others = [other for other in sides if other is not side]
            found = {
                "words": await ok(side.client, "search", query=side.word, mode="words", limit=200),
                "meaning": await ok(
                    side.client, "search", query="logging in with an email", mode="meaning"
                ),
                "similar": await ok(side.client, "similar_items", text="log in with email"),
                "overlaps": await ok(side.client, "find_overlaps", min_similarity=0.0),
                "recall": await ok(side.client, "recall", node_id=side.world["task3"]["id"]),
                "coverage": await ok(side.client, "coverage_report", limit=200),
                "claims": await ok(side.client, "who_is_working"),
                "feed": await ok(side.client, "changes_since", after_event_id=0, limit=200),
                "export": await export_everything(side.client),
            }
            for what, answer in found.items():
                text = json.dumps(answer)
                checks.append(all(other.word not in text for other in others))
                if what in {"words", "meaning", "recall", "feed", "export"}:
                    checks.append(side.word in text)
            counts = (await ok(side.client, "server_status"))["counts"]
            exported = found["export"]["counts"]
            checks.append(counts["events"] == exported["events"] == len(found["export"]["events"]))
            # A decision that applies throughout its own project: this task is in no decision's
            # links, and the project-wide one is in its recall, and no other project's is.
            checks.append(side.world["wide"]["title"] in json.dumps(found["recall"]))
            foreign = [other.world["wide"]["title"] for other in others]
            checks.append(all(title not in json.dumps(found["recall"]) for title in foreign))
        proof(
            "M15",
            "exact and semantic search, similar items, overlaps, recall, project-wide decisions, "
            "coverage, claims, change feeds, counts and exports each show only the project's own "
            "records, though the three projects share the same id numbers",
            all(checks),
            f"{len(checks)} comparisons over 3 projects",
        )

        before_a1, before_b1 = (
            await export_everything(a1.client),
            await export_everything(b1.client),
        )
        by_tool = [
            await refused(
                a1.client,
                "create_node",
                actor="x",
                kind="story",
                title="orphan",
                parent_id=there.epic,
            ),
            await refused(
                a1.client,
                "add_link",
                actor="x",
                source_id=a1.world["story1"]["id"],
                target_id=there.node,
                link_type="relates_to",
            ),
            await refused(
                a1.client,
                "retain_fact",
                actor="x",
                text="t",
                source="s",
                decision_ids=[there.decision],
            ),
            await refused(
                a1.client,
                "supersede_decision",
                actor="x",
                decision_id=there.decision,
                title="t",
                text="t",
                reason="r",
                decided_by="x",
            ),
        ]
        one = a1.project.id
        mine_ids = a1.world
        by_database = [
            sql_refused(
                "INSERT INTO nodes (project_id, id, kind, parent_id, title) "
                f"VALUES ({one}, 9001, 'story', {there.epic}, 'x')"
            ),
            sql_refused(
                "INSERT INTO links (project_id, source_id, target_id, link_type) "
                f"VALUES ({one}, {mine_ids['story1']['id']}, {there.node}, 'relates_to')",
                project=one,
            ),
            sql_refused(
                "INSERT INTO fact_decisions (project_id, fact_id, decision_id) "
                f"VALUES ({one}, {mine_ids['fact2']['id']}, {there.decision})"
            ),
            sql_refused(
                f"UPDATE facts SET status = 'superseded', superseded_by_id = {there.fact}, "
                f"status_reason = 'x' WHERE project_id = {one} AND id = {mine_ids['fact2']['id']}"
            ),
            sql_refused(
                f"UPDATE decisions SET status = 'superseded', superseded_by_id = {there.decision} "
                f"WHERE project_id = {one} AND id = {mine_ids['wide']['id']}"
            ),
        ]
        proof(
            "M15",
            "a parent, a link, a fact's decision or a replacement in another project is refused "
            "through the tools and, even for the database's owner, by direct database writes; "
            "nothing in either project changed",
            all("There is no" in text for text in by_tool)
            and all("violates" in text or "must be an active" in text for text in by_database)
            and await export_everything(a1.client) == before_a1
            and await export_everything(b1.client) == before_b1,
            f"{len(by_tool)} refused by the tools, {len(by_database)} by the database",
        )

        role_sees = run_sql(
            f"SELECT count(*) FROM nodes WHERE project_id = {b1.project.id}",
            plain=True,
            project=one,
            as_project=True,
        ).stdout.strip()
        forged_row = sql_refused(
            f"INSERT INTO nodes (project_id, kind, title) VALUES ({b1.project.id}, 'epic', 'x')",
            project=one,
            as_project=True,
        )
        accounts_read = sql_refused("SELECT count(*) FROM accounts", project=one, as_project=True)
        proof(
            "M15",
            "a project's database role sees none of another project's rows, cannot write one into "
            "it, and cannot read any account, session or key",
            role_sees == "0"
            and "row-level security" in forged_row
            and "permission denied" in accounts_read,
            "row-level security and no grants",
        )

        document = await export_everything(a1.client)
        before_a2 = await export_everything(a2.client)
        loaded_project = make_project(owner_a, "Loaded")
        async with connect(loaded_project.mcp_url, loaded_project.key) as loaded:
            await ok(loaded, "import_graph", actor="marie", document=document)
            same = await export_everything(loaded) == document
            second = await refused(loaded, "import_graph", actor="marie", document=document)
        text = json.dumps(document)
        proof(
            "M15",
            "a project's export loads into an empty project with the same ids and comes back "
            "identical, holds nothing of any account, and changes no other project",
            same
            and "already holds data" in second
            and not any(
                secret in text for secret in (owner_a.email, owner_a.password, a1.project.key)
            )
            and await export_everything(a2.client) == before_a2
            and await export_everything(b1.client) == before_b1,
            f"{document['counts']}",
        )


async def reindexed_side(side: Side) -> None:
    report = await ok(side.client, "reindex")
    assert report["error"] is None and report["waiting_after"] == 0, report


# -- 5: switching with answers held back


async def switching() -> None:
    word = unique_word()
    owner = register(name=f"Switcher {word}")
    first = make_project(owner, f"First {word}")
    second = make_project(owner, f"Second {word}")
    stranger = register(name=f"Stranger {word}")
    stranger_project = make_project(stranger, f"Stranger {word}")
    async with (
        connect(first.mcp_url, first.key) as one,
        connect(second.mcp_url, second.key) as two,
    ):
        epic_one = await made(one, "epic", f"Only in the first {word}")
        story_one = await made(one, "story", f"First story {word}", epic_one)
        await ok(one, "retain_fact", actor="a", text=f"A first fact {word}", source="The first")
        await made(two, "epic", f"Only in the second {word}")
        async with browser_page(first.address, token=first.key, name=None, keep_bodies=True) as w:
            # The first project's search drawer and detail panel are open when it is left.
            await w.open(f"#/board?drawer=search&search=first+{word}&sel=node:{story_one}")
            await expect(w.page.locator("#card-title")).to_contain_text(
                f"#{story_one} ", timeout=SLOW
            )
            await expect(w.page.locator("#search-results li.result").first).to_be_visible(
                timeout=SLOW
            )
            released = asyncio.Event()
            held_back: list[str] = []

            async def hold(route):  # type: ignore[no-untyped-def]
                held_back.append(route.request.url)
                await released.wait()
                with contextlib.suppress(Exception):
                    await route.continue_()

            await w.page.route(f"**/board/api/projects/{first.id}/tool/**", hold)
            await made(one, "epic", f"News for the first {word}")
            for _ in range(200):
                if held_back:
                    break
                await asyncio.sleep(0.1)
            before = len(w.requests)
            await w.page.locator("#project-switcher").select_option(label=f"Second {word}")
            await expect(w.page.locator("#project-switcher option:checked")).to_have_text(
                f"Second {word}", timeout=SLOW
            )
            await w.go("#/board")
            await expect(
                w.page.get_by_role("region", name=f"Epic Only in the second {word}")
            ).to_be_visible(timeout=SLOW)
            released.set()
            await asyncio.sleep(2)
            shown = await w.page.locator("body").inner_text()
            later = w.requests[before:]
            second_bodies = [text for url, text in w.bodies if f"/projects/{second.id}/" in url]
            proof(
                "M15",
                "switching projects while a response and a change-feed poll of the first are held "
                "back: releasing them never puts the first project's records on the second's board",
                bool(held_back)
                and f"Only in the first {word}" not in shown
                and f"News for the first {word}" not in shown
                and f"Only in the second {word}" in shown
                and not [url for url in later if f"/projects/{first.id}/" in url]
                and all(f"Only in the first {word}" not in text for text in second_bodies),
                f"{len(held_back)} held back, {len(later)} requests after the switch",
            )
            # M16: the drawers and the panel are part of that. Nothing of the first project shows in
            # any view, drawer or panel of the second, however late its answers were.
            leaked = []
            for address in (
                "#/graph?view=everything",
                "#/graph?view=context&as=list",
                "#/roadmap",
                "#/board?drawer=activity",
                f"#/board?drawer=search&search=first+{word}",
                f"#/board?sel=node:{story_one}",
            ):
                await w.go(address)
                await asyncio.sleep(1)
                text = await w.page.locator("body").inner_text()
                leaked += [
                    (address, gone)
                    for gone in (
                        f"Only in the first {word}",
                        f"News for the first {word}",
                        f"First story {word}",
                        f"A first fact {word}",
                    )
                    if gone in text
                ]
            after = [text for url, text in w.bodies if f"/projects/{second.id}/" in url]
            proof(
                "M16",
                "the held-back answers of a project that was left reach none of the other "
                "project's views, drawers or detail panel: the search drawer and a chosen item "
                "of the first did not come along, and what the second received holds nothing "
                "of the first",
                not leaked
                and bool(after)
                and all(f"First story {word}" not in text for text in after)
                and all(f"A first fact {word}" not in text for text in after)
                and not [url for url in w.requests[before:] if f"/projects/{first.id}/" in url],
                f"{len(after)} responses of the second project read",
            )
    async with browser_page(stranger_project.address, token=stranger_project.key, name=None) as w:
        await w.open("#/board")
        await w.page.goto(first.address)
        await expect(w.page.locator("#no-such-project")).to_be_visible(timeout=SLOW)
        body = await w.page.locator("body").inner_text()
        proof(
            "M15",
            "a project address copied to another account grants no access: it says there is no "
            "such project among theirs, and none of its records are fetched or shown",
            "no project with that address among yours" in body
            and f"Only in the first {word}" not in body
            and f"Only in the first {word}" not in json.dumps(w.requests),
            first.address.rsplit("/", 1)[-1],
        )


# -- 6: agent keys


async def keys() -> None:
    word = unique_word()
    owner = register(name=f"Keeper {word}")
    project = make_project(owner, f"Keyed {word}")
    other = make_project(owner, f"Other {word}")
    async with connect(other.mcp_url, other.key) as elsewhere:
        await made(elsewhere, "epic", f"Elsewhere {word}")
    async with browser_page(project.address, token=project.key, name=None) as w:
        await w.open("#/access")
        await w.page.locator("#key-label").fill(f"second agent {word}")
        await w.page.locator("#create-key").click()
        await expect(w.page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
        secret = (await w.page.locator("#key-secret").inner_text()).strip()
        async with connect(project.mcp_url, secret) as agent:
            epic = await made(agent, "epic", f"Work {word}")
            story = await ok(
                agent,
                "create_node",
                actor="agent",
                kind="story",
                title=f"A story {word}",
                parent_id=epic,
            )
            taken = await ok(agent, "next_work", actor="agent-keyed")
            await ok(
                agent,
                "finish_work",
                actor="agent-keyed",
                node_id=story["id"],
                outcome="done",
                note="Done with a key made on the board.",
            )
            finished = await ok(agent, "get_node", node_id=story["id"])
            seen = json.dumps(await ok(agent, "list_nodes", limit=200))
            proof(
                "M15",
                "a key made on the board connects a real MCP client that completes next_work and "
                "finish_work in its project, and cannot see the owner's other project",
                taken["work"]["id"] == story["id"]
                and finished["status"] == "done"
                and f"Work {word}" in seen
                and f"Elsewhere {word}" not in seen,
                f"key {secret[:8]}…",
            )
            row = w.page.locator("#keys li.key", has_text=f"second agent {word}")
            await row.locator("button.revoke").click()
            await w.page.locator("dialog.modal button.primary").click()
            await expect(w.page.locator("#keys li.key[data-state=revoked]")).to_have_count(
                1, timeout=SLOW
            )
            connected = ""
            try:
                await agent.call_tool("list_nodes", {})
            except BaseException as error:  # noqa: BLE001
                connected = messages(error)
        newcomer = ""
        try:
            async with connect(project.mcp_url, secret) as again:
                await again.call_tool("list_nodes", {})
        except BaseException as error:  # noqa: BLE001
            newcomer = messages(error)
        proof(
            "M15",
            "revoking the key on the board refuses the client that is already connected, and a "
            "new one, on their next requests; the project's other key still works",
            "agent key is not valid" in connected and "agent key is not valid" in newcomer,
            clip(newcomer, 60),
        )


# -- 7: secrets


async def secrets_are_kept() -> None:
    person = register()
    project = make_project(person, "Careful")
    wrong = f"a-wrong-password-{unique_word()}"
    sign_in(person.base, person.email, wrong)
    async with connect(project.mcp_url, project.key) as client:
        await ok(client, "create_node", actor="agent", kind="epic", title="Quiet")
        leak = await client.call_tool(
            "retain_fact", {"actor": "agent", "text": f"it is {project.key}", "source": "x"}
        )
        events = await ok(client, "changes_since", after_event_id=0, limit=200)
        exported = await export_everything(client)
    stored = held(f"SELECT password_hash FROM accounts WHERE email = '{person.email}'")
    dump = compose("exec", "-T", "db", "pg_dump", "-U", "vrum", "-d", "vrum").stdout
    logs = compose("logs", "app", "--no-color")
    secrets = (person.password, person.cookie, person.csrf, project.key, wrong)
    proof(
        "M15",
        "passwords are stored only as salted scrypt hashes; raw passwords, sessions and agent "
        "keys are absent from the database, the logs, the graph's events and a project's export, "
        "and a key cannot be written into a fact",
        stored.startswith("scrypt$")
        and person.password not in stored
        and all(secret not in dump for secret in (person.password, person.cookie, project.key))
        and all(secret not in logs.stdout + logs.stderr for secret in secrets)
        and all(secret not in json.dumps([events, exported]) for secret in secrets)
        and leak.is_error
        and project.key not in str(leak.content),
        f"{len(dump):,} bytes of dump and {len(logs.stdout):,} of log searched",
    )
