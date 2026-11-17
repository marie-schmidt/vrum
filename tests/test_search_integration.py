"""M5 against the running containers: exact-words search, search by meaning, the two fused, the
reindex job, similar items and the overlap finder, through a real MCP client (see ./check.sh).

The embedding service in the check stack is a scripted stand-in (tests/scripted_embeddings.py), so
these tests prove what Vrum does with an embedding service: not how good a real model is."""

import re
from typing import Any

import anyio
import pytest
from mcp import Client

from tests.helpers import (
    confirm,
    embeddings_stopped,
    make_decision,
    make_fact,
    make_rule,
    make_stories,
    ok,
    refused,
    reindexed,
    run_sql,
    unique_word,
)
from tests.scripted_embeddings import DIMENSIONS, cosine, embed

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def search(client: Client, query: str, **arguments: Any) -> dict[str, Any]:
    return await ok(client, "search", query=query, **arguments)


def key_of(item: dict[str, Any]) -> tuple[str, Any]:
    """What names a result: its ref for an epic, story or task (which has no id), and its id
    for a requirement, decision or fact (whose ref says the same)."""
    kind = item["item_type"]
    if kind in ("epic", "story", "task"):
        assert "id" not in item and item["ref"].startswith(f"{kind}:"), item
        return kind, item["ref"]
    assert item["ref"] == f"{kind}:{item['id']}", item
    return kind, item["id"]


def by_item(page: dict[str, Any]) -> dict[tuple[str, Any], dict[str, Any]]:
    return {key_of(item): item for item in page["items"]}


async def listing(client: Client, tool: str, **filters: Any) -> list[dict[str, Any]]:
    """Every item a list tool has, following the pages (nodes by `after`, others by `after_id`)."""
    cursor, onward = (
        ("after", "next_after") if tool == "list_nodes" else ("after_id", "next_after_id")
    )
    found: list[dict[str, Any]] = []
    after = None
    while True:
        page = await ok(client, tool, limit=200, **{cursor: after}, **filters)
        found.extend(page["items"])
        after = page[onward]
        if after is None:
            return found


def text_of(item: dict[str, Any]) -> str:
    return " ".join(str(value) for value in item.values() if isinstance(value, str))


async def new_story(
    client: Client, title: str, body: str = "", parent: str | None = None
) -> dict[str, Any]:
    """A story with this title and body, under the given epic or under an epic of its own."""
    if parent is None:
        epic = await ok(client, "create_node", actor="marie", kind="epic", title="An epic")
        parent = epic["ref"]
    return await ok(
        client,
        "create_node",
        actor="marie",
        kind="story",
        title=title,
        body=body,
        parent=parent,
    )


async def waiting(client: Client) -> int:
    page = await search(client, unique_word(), mode="words")
    return int(page["index"]["waiting_to_index"])


async def test_exact_words_find_every_kind_of_item_and_the_counts_match_the_lists(
    client: Client,
) -> None:
    word = unique_word()
    epic = await ok(client, "create_node", actor="a", kind="epic", title=f"Plan {word}")
    story = await ok(
        client,
        "create_node",
        actor="a",
        kind="story",
        title="Checkout",
        body=f"We must handle {word} carefully.",
        parent=epic["ref"],
    )
    task = await ok(
        client,
        "create_node",
        actor="a",
        kind="task",
        title=f"Do the {word} part",
        parent=story["ref"],
    )
    rule = await make_rule(client, "A rule", body=f"Never skip {word}.", story=story["ref"])
    decision = await make_decision(client, "A decision", text=f"We choose {word}.")
    wide = await make_decision(client, "A wide one", project_wide=True, text=f"Always {word}.")
    await confirm(client, "decision", wide)
    fact = await make_fact(client, f"The {word} service is slow.")
    confirmed_fact = await make_fact(client, f"The {word} queue is short.")
    await confirm(client, "fact", confirmed_fact)
    # A confirmed fact that was replaced, and one that was withdrawn: both are still found, as
    # what they now are, and the replaced one keeps the confirmation it had.
    old_fact = await make_fact(client, f"The {word} cache is cold.")
    await confirm(client, "fact", old_fact)
    new_fact = (
        await ok(
            client,
            "supersede_fact",
            actor="a",
            replaces=[old_fact["id"]],
            reason="Measured again.",
            text=f"The {word} cache is warm.",
            source="A measurement",
            said_by="Marie",
            quote="It has warmed up.",
        )
    )["replacement"]
    wrong_fact = await make_fact(client, f"The {word} office has a dog.")
    await ok(client, "withdraw_fact", actor="a", fact_ids=[wrong_fact["id"]], reason="A cat.")
    expected = {
        ("epic", epic["ref"]),
        ("story", story["ref"]),
        ("task", task["ref"]),
        ("rule", rule["id"]),
        ("decision", decision["id"]),
        ("decision", wide["id"]),
        ("fact", fact["id"]),
        ("fact", confirmed_fact["id"]),
        ("fact", old_fact["id"]),
        ("fact", new_fact["id"]),
        ("fact", wrong_fact["id"]),
    }

    page = await search(client, word, mode="words", limit=200)

    results = by_item(page)
    assert set(results) == expected and page["total"] == 11
    for item in results.values():
        assert item["matched"] == "words" and item["similarity"] is None
        assert item["why"] == f"exact words: {word}" and item["words_matched"] == [word]
        assert f"«{word}»" in item["snippet"] and item["snippet"].count("«") >= 1
    # Parent and status are read from the graph now.
    assert results[("story", story["ref"])]["parent"] == epic["ref"]
    assert results[("story", story["ref"])]["parent_kind"] == "epic"
    assert results[("story", story["ref"])]["parent_title"] == f"Plan {word}"
    assert results[("task", task["ref"])]["parent"] == story["ref"]
    assert results[("rule", rule["id"])]["parent"] == story["ref"]
    assert results[("epic", epic["ref"])]["parent"] is None
    assert {results[key]["status"] for key in expected if key[0] in ("epic", "story", "task")} == {
        "ready"
    }
    assert results[("rule", rule["id"])]["status"] == "held"
    # Work and requirements are labelled as what they are and have no confirmation.
    for key, label in (
        (("epic", epic["ref"]), "Epic"),
        (("story", story["ref"]), "Story"),
        (("task", task["ref"]), "Task"),
        (("rule", rule["id"]), "Requirement"),
    ):
        assert results[key]["label"] == label and results[key]["confirmation"] is None, key
    # A decision or fact says what it is, whether it still counts and whether a person looked at
    # it, in its label and in its confirmation field.
    plain = results[("decision", decision["id"])]
    assert plain["label"] == "Decision, not confirmed by a person"
    assert (plain["status"], plain["confirmation"]) == ("active", "unconfirmed")
    wide_result = results[("decision", wide["id"])]
    assert wide_result["label"].startswith("Project-wide decision, confirmed by Marie on ")
    assert (wide_result["status"], wide_result["confirmation"]) == ("active", "confirmed")
    unconfirmed = results[("fact", fact["id"])]
    assert unconfirmed["label"] == "Fact, not confirmed by a person. Source: The team call"
    assert (unconfirmed["status"], unconfirmed["confirmation"]) == ("active", "unconfirmed")
    assert unconfirmed["title"].startswith("The ")
    assert results[("fact", confirmed_fact["id"])]["label"].startswith(
        "Fact, confirmed by Marie on "
    )
    assert results[("fact", confirmed_fact["id"])]["confirmation"] == "confirmed"
    replaced = results[("fact", old_fact["id"])]
    assert replaced["label"].startswith("Superseded fact, confirmed by Marie on ")
    assert (replaced["status"], replaced["confirmation"]) == ("superseded", "confirmed")
    replacement = results[("fact", new_fact["id"])]
    assert replacement["label"].startswith("Fact, not confirmed by a person")
    assert (replacement["status"], replacement["confirmation"]) == ("active", "unconfirmed")
    withdrawn = results[("fact", wrong_fact["id"])]
    assert withdrawn["label"].startswith("Withdrawn fact, not confirmed by a person")
    assert (withdrawn["status"], withdrawn["confirmation"]) == ("withdrawn", "unconfirmed")
    assert not any(item["suggestion"] for item in results.values())

    # The counts equal what the list tools return for the same words.
    nodes = [
        n
        for kind in ("epic", "story", "task")
        for n in await listing(client, "list_nodes", kind=kind)
    ]
    lists = {
        **{kind: [n for n in nodes if n["kind"] == kind] for kind in ("epic", "story", "task")},
        "rule": await listing(client, "list_rules"),
        "decision": await listing(client, "list_decisions"),
        "fact": await listing(client, "list_facts"),
    }
    expected_counts = {"epic": 1, "story": 1, "task": 1, "rule": 1, "decision": 2, "fact": 5}
    for item_type, items in lists.items():
        containing = [item for item in items if word in text_of(item)]
        assert page["counts_by_type"][item_type] == len(containing), item_type
    assert page["counts_by_type"] == dict(sorted(expected_counts.items()))
    assert page["counts_by_status"] == {
        "active": 5,
        "held": 1,
        "ready": 3,
        "superseded": 1,
        "withdrawn": 1,
    }

    # Filters narrow the results and leave the counts as they were, to show what filtering gives.
    narrowed = await search(client, word, mode="words", types=["story", "task"])
    assert set(by_item(narrowed)) == {("story", story["ref"]), ("task", task["ref"])}
    assert narrowed["total"] == 2 and narrowed["counts_by_type"] == page["counts_by_type"]
    held = await search(client, word, mode="words", statuses=["held"])
    assert set(by_item(held)) == {("rule", rule["id"])} and held["counts_by_status"]["ready"] == 3
    both = await search(client, word, mode="words", types=["story", "rule"], statuses=["held"])
    assert set(by_item(both)) == {("rule", rule["id"])}
    # Context records can be told apart by what they are and where they stand.
    records = await search(client, word, mode="words", types=["decision", "fact"])
    assert records["total"] == 7 and records["counts_by_type"] == page["counts_by_type"]
    ended = await search(client, word, mode="words", statuses=["superseded", "withdrawn"])
    assert set(by_item(ended)) == {("fact", old_fact["id"]), ("fact", wrong_fact["id"])}
    in_force = await search(client, word, mode="words", types=["fact"], statuses=["active"])
    assert set(by_item(in_force)) == {
        ("fact", fact["id"]),
        ("fact", confirmed_fact["id"]),
        ("fact", new_fact["id"]),
    }
    assert in_force["counts_by_status"] == page["counts_by_status"]

    # Pages of three cover everything once, in the same order as one page of everything.
    seen: list[tuple[str, int]] = []
    offset: int | None = 0
    while offset is not None:
        part = await search(client, word, mode="words", limit=3, offset=offset)
        assert part["total"] == 11
        seen.extend(key_of(item) for item in part["items"])
        offset = part["next_offset"]
    assert seen == [key_of(item) for item in page["items"]]
    assert len(set(seen)) == 11

    for arguments, expected_message in (
        ({"types": ["epics"]}, "types must be one of epic, story, task, rule, decision, fact"),
        ({"types": ["belief"]}, "types must be one of epic, story, task, rule, decision, fact"),
        ({"statuses": ["open"]}, "statuses must be one of new, ready"),
        ({"mode": "fuzzy"}, "mode must be one of both, words, meaning"),
        ({"limit": 0}, "limit must be a whole number from 1 to 200"),
        ({"offset": -1}, "offset must be 0 or more"),
        ({"min_similarity": 1.5}, "min_similarity must be a number from 0 to 1"),
    ):
        assert expected_message in await refused(client, "search", query=word, **arguments)


async def test_a_change_is_found_by_words_the_moment_it_is_saved(client: Client) -> None:
    story = await new_story(client, "A story about nothing in particular")
    word = unique_word()
    assert (await search(client, word, mode="words"))["total"] == 0

    updated = await ok(
        client,
        "update_node",
        actor="agent-1",
        node=story["ref"],
        expected_version=story["version"],
        body=f"We are migrating the {word} accounts.",
    )

    page = await search(client, word, mode="words")
    assert [key_of(i) for i in page["items"]] == [("story", story["ref"])]
    # Words are whole words, with their endings: "migration" finds "migrating".
    stemmed = await search(client, f"migration {word}", mode="words")
    assert stemmed["total"] == 1
    # And a later edit takes the word away again.
    await ok(
        client,
        "update_node",
        actor="agent-1",
        node=story["ref"],
        expected_version=updated["version"],
        body="Nothing here now.",
    )
    assert (await search(client, word, mode="words"))["total"] == 0


async def test_the_query_can_ask_for_all_words_a_phrase_an_alternative_or_to_leave_one_out(
    client: Client,
) -> None:
    first, second = unique_word(), unique_word()
    both = await new_story(client, f"{first} comes before {second} here")
    only_first = await new_story(client, f"{first} stands alone")
    only_second = await new_story(client, f"{second} stands alone")

    def refs(page: dict[str, Any]) -> set[str]:
        return {item["ref"] for item in page["items"]}

    assert refs(await search(client, f"{first} {second}", mode="words")) == {both["ref"]}
    assert refs(await search(client, f"{first} or {second}", mode="words")) == {
        both["ref"],
        only_first["ref"],
        only_second["ref"],
    }
    assert refs(await search(client, f'"{first} comes before {second}"', mode="words")) == {
        both["ref"]
    }
    assert refs(await search(client, f'"{second} comes before {first}"', mode="words")) == set()
    assert refs(await search(client, f"{first} -{second}", mode="words")) == {only_first["ref"]}
    # The best match, with the most to say about the words, is listed first.
    ranked = await search(client, f"{first} or {second}", mode="words")
    assert ranked["items"][0]["ref"] == both["ref"]


@pytest.mark.parametrize("mode", ["both", "words", "meaning"])
async def test_a_query_never_fails_on_odd_characters(client: Client, mode: str) -> None:
    await new_story(client, "A story that exists so there is something to search")
    odd = [
        '"an unbalanced quote',
        "a & b | !c",
        "foo:* bar:A",
        "((( )))",
        "back\\slash and 'single' quotes",
        "emoji 🚀🔥 and 日本語 and عربى",
        "x" * 5000,
        "line one\nline two\ttabbed",
        "null\x00byte",
        "«highlight markers»",
        "-",
        "or or or",
        "<-> <2> <script>alert(1)</script>",
        "100% done; DROP TABLE nodes; --",
        "%s %(name)s {0} {}",
    ]
    for query in odd:
        page = await search(client, query, mode=mode)
        assert isinstance(page["items"], list) and page["total"] >= 0, query
    for blank in ("", "   ", "\x00"):
        assert "cannot be empty" in await refused(client, "search", query=blank, mode=mode)


async def test_a_query_of_only_common_words_says_why_it_found_nothing_by_words(
    client: Client,
) -> None:
    page = await search(client, "the and of", mode="words")
    assert page["total"] == 0
    assert "too common" in page["index"]["note"]


async def test_a_paraphrase_is_found_by_meaning_and_labelled_a_suggestion(client: Client) -> None:
    word = unique_word()
    story = await new_story(
        client,
        f"Customers receive an invoice by email {word}",
        "After payment the client gets a bill by mail.",
    )
    await reindexed(client)
    content = (
        f"Customers receive an invoice by email {word}\n"
        "After payment the client gets a bill by mail."
    )
    paraphrase = "Send the bill to clients by mail once charged"
    expected = cosine(embed(content), embed(paraphrase))
    assert expected >= 0.7, "the fixture must be alike enough to be found"

    page = await search(client, paraphrase)

    result = by_item(page)[("story", story["ref"])]
    assert result["matched"] == "meaning" and result["suggestion"] is True
    assert result["words_matched"] == []
    assert result["similarity"] == pytest.approx(expected, abs=0.002)
    assert result["why"].startswith("similar in meaning (score ")
    assert page["index"]["meaning"] == "used"
    assert not re.search(r"«", result["snippet"])
    # The same words ask for words only: it has none of them, so it is not found.
    assert ("story", story["ref"]) not in by_item(await search(client, paraphrase, mode="words"))
    # And asking for meaning only finds it too.
    assert ("story", story["ref"]) in by_item(await search(client, paraphrase, mode="meaning"))
    # A floor of 1 leaves only what is identical, so it is left out.
    assert ("story", story["ref"]) not in by_item(
        await search(client, paraphrase, min_similarity=1.0)
    )


async def test_facts_and_decisions_are_found_by_words_and_meaning_with_their_confirmation(
    client: Client,
) -> None:
    word = unique_word()
    fact = await make_fact(client, f"Customers receive an invoice by email {word}")
    confirmed = await make_fact(client, f"The customer is sent an invoice by email {word}")
    await confirm(client, "fact", confirmed)
    decision = await make_decision(
        client, f"Mail the bill {word}", text="Clients receive a bill by mail", reason="Clients pay"
    )
    wide = await make_decision(
        client,
        f"Always mail the bill {word}",
        text="The client is mailed a bill",
        reason="Clients pay",
        project_wide=True,
    )
    await confirm(client, "decision", wide)
    gone = await make_fact(client, f"The client is mailed a bill {word}")
    await ok(client, "withdraw_fact", actor="a", fact_ids=[gone["id"]], reason="Not so.")
    await reindexed(client)
    found = {
        ("fact", fact["id"]): ("active", "unconfirmed"),
        ("fact", confirmed["id"]): ("active", "confirmed"),
        ("decision", decision["id"]): ("active", "unconfirmed"),
        ("decision", wide["id"]): ("active", "confirmed"),
        ("fact", gone["id"]): ("withdrawn", "unconfirmed"),
    }

    by_words = by_item(await search(client, word, mode="words", limit=50))
    by_meaning = by_item(
        await search(client, "Clients get a bill by mail", mode="meaning", limit=50)
    )

    for key, (status, confirmation) in found.items():
        assert key in by_words and by_words[key]["matched"] == "words", key
        assert key in by_meaning and by_meaning[key]["matched"] == "meaning", key
        assert by_meaning[key]["similarity"] is not None
        for results in (by_words, by_meaning):
            assert (results[key]["status"], results[key]["confirmation"]) == (status, confirmation)
            assert results[key]["suggestion"] is (results is by_meaning)
    # Each says what it is in its label, whichever way it was found.
    for results in (by_words, by_meaning):
        assert results[("fact", fact["id"])]["label"].startswith("Fact, not confirmed by a person")
        assert results[("fact", confirmed["id"])]["label"].startswith(
            "Fact, confirmed by Marie on "
        )
        assert (
            results[("decision", decision["id"])]["label"] == "Decision, not confirmed by a person"
        )
        assert results[("decision", wide["id"])]["label"].startswith(
            "Project-wide decision, confirmed by Marie on "
        )
        assert results[("fact", gone["id"])]["label"].startswith("Withdrawn fact")


async def test_what_matches_both_ways_is_labelled_both_and_comes_first(client: Client) -> None:
    word = unique_word()
    title = f"Invoice reminders {word}"
    body = "Send customers a reminder email when payment is late."
    story = await new_story(client, title, body)
    await reindexed(client)

    page = await search(client, f"{title} {body}")

    first = page["items"][0]
    assert key_of(first) == ("story", story["ref"])
    assert first["matched"] == "both" and first["similarity"] == pytest.approx(1.0, abs=0.001)
    assert word in first["words_matched"] and "reminder" in first["words_matched"]
    assert (
        first["why"].startswith("exact words: ")
        and "; similar in meaning (score 1.00)" in first["why"]
    )
    assert first["suggestion"] is False
    assert page["counts_by_type"]["story"] >= 1


async def test_with_the_embedding_service_stopped_words_still_work_and_meaning_says_so(
    client: Client,
) -> None:
    word = unique_word()
    indexed = await new_story(client, "An indexed story about backups and snapshots")
    epic = await ok(client, "create_node", actor="a", kind="epic", title="An epic")
    await reindexed(client)
    paraphrase = "Clients get a bill by mail"
    assert cosine(embed(paraphrase), embed(f"Customers get an invoice by email {word}")) >= 0.7
    with embeddings_stopped():
        before = await waiting(client)
        story = await new_story(
            client, f"Customers get an invoice by email {word}", parent=epic["ref"]
        )

        # Found by words at once, labelled as an exact-words match, and said to be words only.
        page = await search(client, word)
        result = by_item(page)[("story", story["ref"])]
        assert result["matched"] == "words" and result["similarity"] is None
        assert page["index"]["meaning"] == "service_unreachable"
        assert "cannot be reached" in page["index"]["note"]
        assert page["index"]["waiting_to_index"] == before + 1

        # The paraphrase has no words in common, so without meaning it is not found.
        assert ("story", story["ref"]) not in by_item(await search(client, paraphrase))
        message = await refused(client, "search", query=paraphrase, mode="meaning")
        assert "Searching by meaning is not possible now" in message
        assert "cannot be reached" in message
        message = await refused(client, "reindex")
        assert "cannot be reached" in message and "Nothing was indexed" in message
        assert "Finding similar items needs meaning" in await refused(
            client, "similar_items", text="anything"
        )
        # What is already in the index still works without the service: overlaps, and similar
        # items for an item that is indexed.
        assert "items" in await ok(client, "find_overlaps")
        assert "items" in await ok(client, "similar_items", node=indexed["ref"])

    # Back again: reindex picks the story up, and the paraphrase is found by meaning.
    report = await reindexed(client)
    assert report["error"] is None and report["failed"] == []
    again = await search(client, f"Customers get an invoice by email {word}")
    assert by_item(again)[("story", story["ref"])]["matched"] == "both"
    assert again["index"]["meaning"] == "used" and again["index"]["waiting_to_index"] == 0
    assert (
        by_item(await search(client, paraphrase))[("story", story["ref"])]["matched"] == "meaning"
    )


async def test_the_server_indexes_new_work_by_itself(client: Client) -> None:
    await reindexed(client)
    word = unique_word()
    story = await new_story(client, f"Customers get an invoice by email {word}")
    paraphrase = "Clients get a bill by mail"
    assert cosine(embed(paraphrase), embed(f"Customers get an invoice by email {word}")) >= 0.7

    # No reindex is asked for: the background job finds it within a few seconds.
    for _ in range(40):
        page = await search(client, paraphrase, mode="meaning")
        if ("story", story["ref"]) in by_item(page):
            break
        await anyio.sleep(0.5)
    else:
        pytest.fail("The background job did not index the new story within 20 seconds.")
    assert (await search(client, word))["index"]["waiting_to_index"] == 0


async def test_new_and_changed_items_are_counted_as_waiting_and_tasks_are_not(
    client: Client,
) -> None:
    await reindexed(client)
    with embeddings_stopped():
        start = await waiting(client)
        (story,) = await make_stories(client, 1)
        # An epic and a story are new, so they wait.
        assert await waiting(client) == start + 2
        task = await ok(
            client, "create_node", actor="a", kind="task", title="A task", parent=story["ref"]
        )
        assert await waiting(client) == start + 2
        await make_fact(client, "A new fact")
        await make_decision(client, "A new decision")
        await make_rule(client, "A new rule")
        assert await waiting(client) == start + 5
        # Changing the text of one that is waiting does not count it twice, and changing a task
        # does nothing.
        await ok(
            client,
            "update_node",
            actor="a",
            node=story["ref"],
            expected_version=story["version"],
            body="Changed text",
        )
        await ok(
            client,
            "update_node",
            actor="a",
            node=task["ref"],
            expected_version=task["version"],
            body="Changed task text",
        )
        assert await waiting(client) == start + 5
    await reindexed(client)
    # A change that is not to the text (here, the status) leaves it indexed.
    current = await ok(client, "get_node", node=story["ref"])
    await ok(
        client,
        "update_node",
        actor="a",
        node=story["ref"],
        expected_version=current["version"],
        status="in_progress",
    )
    assert await waiting(client) == 0


async def test_tasks_are_found_by_words_only(client: Client) -> None:
    word = unique_word()
    (story,) = await make_stories(client, 1)
    task = await ok(
        client,
        "create_node",
        actor="a",
        kind="task",
        title=f"Customers get an invoice by email {word}",
        parent=story["ref"],
    )
    await reindexed(client)

    by_words = await search(client, word)
    assert by_item(by_words)[("task", task["ref"])]["matched"] == "words"
    by_meaning = await search(client, "Clients receive a bill by mail", mode="meaning")
    assert ("task", task["ref"]) not in by_item(by_meaning)
    count = await ok(client, "reindex")
    assert count["indexed_total"] >= 1 and count["embedded"] == 0
    # A task can still be the thing to find similar items for: its text is embedded as asked.
    similar = await ok(client, "similar_items", node=task["ref"], min_similarity=0.0, limit=5)
    assert similar["items"] and all(i["matched"] == "meaning" for i in similar["items"])
    assert ("task", task["ref"]) not in by_item(similar)


async def test_dropping_the_index_and_rebuilding_it_gives_the_same_results(
    client: Client,
) -> None:
    word = unique_word()
    epic_a = await ok(client, "create_node", actor="a", kind="epic", title="Billing")
    epic_b = await ok(client, "create_node", actor="a", kind="epic", title="Emails")
    first = await new_story(
        client, f"Customers receive an invoice by email {word}", parent=epic_a["ref"]
    )
    second = await new_story(client, f"Clients receive a bill by mail {word}", parent=epic_b["ref"])
    decision = await make_decision(client, "Bills", text=f"A bill goes out by mail {word}")
    await reindexed(client)

    async def results() -> dict[str, Any]:
        meaning = await search(client, "Send the bill to a client by mail", mode="meaning")
        similar = await ok(client, "similar_items", node=first["ref"], min_similarity=0.3)
        overlaps = await ok(client, "find_overlaps", epics=[epic_a["ref"]])
        return {
            "meaning": [(*key_of(i), i["similarity"]) for i in meaning["items"]],
            "similar": [(*key_of(i), i["similarity"]) for i in similar["items"]],
            "overlaps": [
                (p["first"]["ref"], p["second"]["ref"], p["similarity"]) for p in overlaps["items"]
            ],
        }

    before = await results()
    assert ("story", first["ref"]) in {(t, i) for t, i, _ in before["meaning"]}
    assert (first["ref"], second["ref"]) in {(a, b) for a, b, _ in before["overlaps"]}
    assert ("decision", decision["id"]) in {(t, i) for t, i, _ in before["similar"]}

    with embeddings_stopped():
        dropped = run_sql("DROP TABLE semantic_index, semantic_index_info")
        assert dropped.returncode == 0, dropped.stderr
        # Without the index, exact words work and meaning says the index is not ready.
        page = await search(client, word)
        assert page["total"] >= 3
        assert all(item["matched"] == "words" for item in page["items"])
        assert page["index"]["meaning"] == "index_not_ready"
        assert page["index"]["waiting_to_index"] >= 3
        assert "Finding overlaps needs the index" in await refused(client, "find_overlaps")

    report = await reindexed(client)
    assert report["indexed_total"] >= 3
    assert await results() == before


async def test_the_vector_size_is_read_from_the_model_and_the_index_follows_a_new_model(
    client: Client,
) -> None:
    report = await reindexed(client)
    assert report["dimensions"] == DIMENSIONS and report["model"] == "scripted-embedding"
    size = run_sql("SELECT DISTINCT vector_dims(embedding) FROM semantic_index", plain=True)
    assert size.stdout.strip() == str(DIMENSIONS), size.stderr
    column = run_sql(
        "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'semantic_index'::regclass "
        "AND attname = 'embedding'",
        plain=True,
    )
    assert column.stdout.strip() == str(DIMENSIONS), column.stderr

    story = await new_story(client, "Customers receive an invoice by email")
    with embeddings_stopped():
        # An index built with another model is no use for this one: everything waits.
        changed = run_sql("UPDATE semantic_index_info SET model = 'another-model'")
        assert changed.returncode == 0, changed.stderr
        page = await search(client, "invoice")
        assert page["index"]["meaning"] == "index_not_ready"
        assert page["index"]["waiting_to_index"] >= 2
        assert "not ready" in await refused(client, "find_overlaps")
    await reindexed(client)
    model = run_sql("SELECT model FROM semantic_index_info", plain=True)
    assert model.stdout.strip() == "scripted-embedding", model.stderr
    again = await search(client, "Clients receive a bill by mail", mode="meaning")
    assert ("story", story["ref"]) in by_item(again)


async def test_an_item_the_service_refuses_is_reported_and_does_not_stop_the_others(
    client: Client,
) -> None:
    await reindexed(client)
    word = unique_word()
    refused_story = await new_story(client, f"Too long {word}", "[[reject-me]]")
    fine = await new_story(client, f"Customers receive an invoice by email {word}")

    report = await ok(client, "reindex")

    assert [key_of(f) for f in report["failed"]] == [("story", refused_story["ref"])]
    assert "refused the text" in report["failed"][0]["reason"]
    assert report["error"] is None and report["waiting_after"] == 1
    found = await search(client, "Clients receive a bill by mail", mode="meaning")
    assert ("story", fine["ref"]) in by_item(found)
    # It is still found by words, and stays waiting until its text changes.
    assert ("story", refused_story["ref"]) in by_item(await search(client, word, mode="words"))
    assert await waiting(client) == 1
    await ok(
        client,
        "update_node",
        actor="a",
        node=refused_story["ref"],
        expected_version=refused_story["version"],
        body="Short now.",
    )
    assert (await reindexed(client))["failed"] == []
    assert await waiting(client) == 0


# -- overlaps ---------------------------------------------------------------------------------

# Each story and its twin say the same thing in other words. The first four twins use exactly the
# same ideas; the last four add a word of their own, so they are alike but not identical.
_DUPLICATES = [
    (
        "Customers receive an invoice by email after payment",
        "Clients receive a bill by mail after charge",
    ),
    ("Export the monthly report as a CSV file", "Download the monthly summary as a CSV file"),
    (
        "Users sign in with a password and keep their role",
        "Customers authenticate with credentials and keep their privilege",
    ),
    (
        "Translate every image caption into the customer language",
        "Localisation of every photo caption into the client locale",
    ),
    (
        "Search results appear slow for large projects",
        "Lookup results appear sluggish for large projects today",
    ),
    (
        "Create a backup every night and restore it on demand",
        "Make a snapshot every night and recover it on demand tonight",
    ),
    ("Dark theme for the mobile app", "Night theme for the smartphone app now"),
    (
        "Cancel a subscription and keep an audit trail",
        "Terminate a membership and keep a log history for good",
    ),
]


async def make_overlap_fixture(
    client: Client,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Two epics holding the eight pairs of a story and its reworded twin."""
    epic_a = await ok(client, "create_node", actor="a", kind="epic", title="Overlap fixture A")
    epic_b = await ok(client, "create_node", actor="a", kind="epic", title="Overlap fixture B")
    firsts = [await new_story(client, first, parent=epic_a["ref"]) for first, _ in _DUPLICATES]
    seconds = [await new_story(client, second, parent=epic_b["ref"]) for _, second in _DUPLICATES]
    await reindexed(client)
    return epic_a, epic_b, firsts, seconds


async def every_overlap(client: Client, **arguments: Any) -> dict[str, Any]:
    page = await ok(client, "find_overlaps", limit=200, **arguments)
    items = list(page["items"])
    while page["next_offset"] is not None:
        page = await ok(client, "find_overlaps", limit=200, offset=page["next_offset"], **arguments)
        items.extend(page["items"])
    return {**page, "items": items}


async def test_the_overlap_finder_flags_the_duplicates_and_not_the_unrelated_pairs(
    client: Client,
) -> None:
    epic_a, epic_b, firsts, seconds = await make_overlap_fixture(client)
    pairs = {
        (a["ref"], b["ref"]): (i == j) for i, a in enumerate(firsts) for j, b in enumerate(seconds)
    }
    duplicates = {pair for pair, is_duplicate in pairs.items() if is_duplicate}
    unrelated = {pair for pair, is_duplicate in pairs.items() if not is_duplicate}
    assert len(duplicates) == 8 and len(unrelated) == 56
    # The fixture is what it says: known duplicates are alike and known unrelated pairs are not.
    scores = {
        (a["ref"], b["ref"]): cosine(embed(a["title"]), embed(b["title"]))
        for a in firsts
        for b in seconds
    }
    assert min(scores[pair] for pair in duplicates) >= 0.85
    assert max(scores[pair] for pair in unrelated) <= 0.4

    page = await every_overlap(client, epics=[epic_a["ref"], epic_b["ref"]])

    found = {(p["first"]["ref"], p["second"]["ref"]): p for p in page["items"]}
    both_in_fixture = {
        pair
        for pair in found
        if pair[0] in {s["ref"] for s in firsts + seconds}
        and pair[1] in {s["ref"] for s in firsts + seconds}
    }
    flagged_duplicates = duplicates & set(found)
    flagged_unrelated = unrelated & set(found)
    print(
        f"\noverlap finder, threshold {page['threshold']}: flagged {len(flagged_duplicates)} of "
        f"{len(duplicates)} known duplicate pairs and {len(flagged_unrelated)} of "
        f"{len(unrelated)} known unrelated pairs; the duplicates scored "
        f"{min(scores[p] for p in duplicates):.2f} to {max(scores[p] for p in duplicates):.2f} "
        f"and the unrelated pairs at most {max(scores[p] for p in unrelated):.2f}"
    )
    assert flagged_duplicates == duplicates
    assert flagged_unrelated == set()
    assert both_in_fixture == duplicates
    assert page["threshold"] == 0.55 and page["total"] == len(page["items"]) >= 8
    scored = {pair: found[pair]["similarity"] for pair in duplicates}
    for pair, similarity in scored.items():
        assert similarity == pytest.approx(scores[pair], abs=0.002)
    # Listed most alike first, and each pair says where its two sides are.
    similarities = [p["similarity"] for p in page["items"]]
    assert similarities == sorted(similarities, reverse=True)
    sample = found[(firsts[0]["ref"], seconds[0]["ref"])]
    assert (sample["first"]["epic"], sample["second"]["epic"]) == (epic_a["ref"], epic_b["ref"])
    assert sample["first"]["epic_title"] == "Overlap fixture A"
    assert sample["first"]["status"] == "ready" and sample["already_linked"] == []
    # Stories in the same epic are never a pair, however alike.
    twin = await new_story(client, _DUPLICATES[0][0], parent=epic_a["ref"])
    await reindexed(client)
    again = await every_overlap(client, epics=[epic_a["ref"]])
    assert not any(
        {p["first"]["ref"], p["second"]["ref"]} == {twin["ref"], firsts[0]["ref"]}
        for p in again["items"]
    )
    assert any(
        {p["first"]["ref"], p["second"]["ref"]} == {twin["ref"], seconds[0]["ref"]}
        for p in again["items"]
    )

    # A higher bar leaves fewer, and a link that already exists is shown.
    strict = await every_overlap(client, epics=[epic_a["ref"]], min_similarity=0.999)
    assert strict["threshold"] == 0.999
    assert 0 < len(strict["items"]) < len(again["items"])
    await ok(
        client,
        "add_link",
        actor="a",
        source=firsts[1]["ref"],
        target=seconds[1]["ref"],
        link_type="duplicates",
    )
    linked = await every_overlap(client, epics=[epic_a["ref"]])
    pair = next(
        p
        for p in linked["items"]
        if {p["first"]["ref"], p["second"]["ref"]} == {firsts[1]["ref"], seconds[1]["ref"]}
    )
    assert pair["already_linked"] == ["duplicates"]
    # A dropped story is not compared unless asked for.
    current = await ok(client, "get_node", node=seconds[2]["ref"])
    await ok(
        client,
        "update_node",
        actor="a",
        node=seconds[2]["ref"],
        expected_version=current["version"],
        status="dropped",
    )
    gone = {
        (p["first"]["ref"], p["second"]["ref"])
        for p in (await every_overlap(client, epics=[epic_a["ref"]]))["items"]
    }
    assert (firsts[2]["ref"], seconds[2]["ref"]) not in gone
    kept = {
        (p["first"]["ref"], p["second"]["ref"])
        for p in (await every_overlap(client, epics=[epic_a["ref"]], include_dropped=True))["items"]
    }
    assert (firsts[2]["ref"], seconds[2]["ref"]) in kept


async def test_the_overlap_finder_only_reports_and_changes_nothing(client: Client) -> None:
    epic_a, epic_b, firsts, seconds = await make_overlap_fixture(client)
    nodes = [*firsts, *seconds]

    async def state() -> list[Any]:
        return [
            (
                (await ok(client, "get_node", node=node["ref"]))["version"],
                len((await ok(client, "node_history", node=node["ref"]))["items"]),
                len((await ok(client, "list_links", node=node["ref"]))["items"]),
            )
            for node in nodes
        ]

    before = await state()
    page = await every_overlap(client, epics=[epic_a["ref"], epic_b["ref"]])
    assert len(page["items"]) >= 8
    assert await state() == before


async def test_overlaps_can_compare_epics_and_rules_and_refuse_bad_asks(client: Client) -> None:
    word = unique_word()
    first = await ok(
        client, "create_node", actor="a", kind="epic", title=f"Invoice for customers {word}"
    )
    second = await ok(
        client, "create_node", actor="a", kind="epic", title=f"Billing for clients {word}"
    )
    (story,) = await make_stories(client, 1)
    other = await new_story(client, "Another story")
    rule_one = await make_rule(
        client, "Every customer gets an invoice by email", story=story["ref"]
    )
    rule_two = await make_rule(client, "Each client gets a bill by mail", story=other["ref"])
    await reindexed(client)

    epics = await every_overlap(client, item_type="epic", epics=[first["ref"]])
    assert {(p["first"]["ref"], p["second"]["ref"]) for p in epics["items"]} >= {
        (first["ref"], second["ref"])
    }
    assert all(p["first"]["item_type"] == "epic" for p in epics["items"])
    rules = await every_overlap(client, item_type="rule", epics=[story["parent"]])
    assert (rule_one["id"], rule_two["id"]) in {
        (p["first"]["id"], p["second"]["id"]) for p in rules["items"]
    }

    assert "item_type must be one of story, epic, rule" in await refused(
        client, "find_overlaps", item_type="task"
    )
    assert "There is no epic 987654321 in this project" in await refused(
        client, "find_overlaps", epics=["epic:987654321"]
    )
    assert "epics must name epics, such as epic:3, but story:1 is not an epic" in await refused(
        client, "find_overlaps", epics=["story:1"]
    )
    assert "min_similarity must be a number from 0 to 1" in await refused(
        client, "find_overlaps", min_similarity=2
    )
    assert "offset must be 0 or more" in await refused(client, "find_overlaps", offset=-5)
    # Paging: a page of one, then the rest.
    one = await ok(client, "find_overlaps", item_type="epic", epics=[first["ref"]], limit=1)
    assert len(one["items"]) == 1 and one["total"] == len(epics["items"])
    if one["total"] > 1:
        assert one["next_offset"] == 1
    past = await ok(client, "find_overlaps", item_type="epic", epics=[first["ref"]], offset=500)
    assert past["items"] == [] and past["total"] == epics["total"]


# -- similar items ----------------------------------------------------------------------------


async def test_similar_items_for_an_item_or_a_text_exclude_the_item_and_are_suggestions(
    client: Client,
) -> None:
    epic_a, epic_b, firsts, seconds = await make_overlap_fixture(client)
    fact = await make_fact(client, "Clients receive a bill by mail after charge")
    decision = await make_decision(
        client, "Bills", text="Customers receive an invoice by email after payment"
    )
    other_fact = await make_fact(client, "Mailing a bill to a client after charging is good")
    await reindexed(client)

    page = await ok(client, "similar_items", node=firsts[0]["ref"], limit=200)

    results = by_item(page)
    assert ("story", firsts[0]["ref"]) not in results
    assert ("story", seconds[0]["ref"]) in results
    assert ("fact", fact["id"]) in results and ("decision", decision["id"]) in results
    assert all(r["matched"] == "meaning" and r["suggestion"] for r in results.values())
    assert results[("fact", fact["id"])]["confirmation"] == "unconfirmed"
    assert results[("story", seconds[0]["ref"])]["confirmation"] is None
    assert page["items"][0]["similarity"] >= page["items"][-1]["similarity"]
    assert page["counts_by_type"]["story"] >= 1 and page["index"]["meaning"] == "used"
    for_fact = await ok(client, "similar_items", fact_id=fact["id"], min_similarity=0.2, limit=50)
    assert ("fact", other_fact["id"]) in by_item(for_fact)
    assert ("fact", fact["id"]) not in by_item(for_fact)

    only = await ok(client, "similar_items", node=firsts[0]["ref"], types=["decision", "fact"])
    assert {i["item_type"] for i in only["items"]} <= {"decision", "fact"}
    assert ("decision", decision["id"]) in by_item(only)
    for_text = await ok(
        client, "similar_items", text="Mail every client a bill after charge", limit=50
    )
    assert ("story", seconds[0]["ref"]) in by_item(for_text)
    for_decision = await ok(client, "similar_items", decision_id=decision["id"], types=["story"])
    assert ("story", firsts[0]["ref"]) in by_item(for_decision)
    for_rule = await make_rule(
        client, "Send customers an invoice by email after payment", story=firsts[0]["ref"]
    )
    await reindexed(client)
    assert ("story", firsts[0]["ref"]) in by_item(
        await ok(client, "similar_items", rule_id=for_rule["id"], types=["story"])
    )
    # A paged walk gives each once.
    seen: list[tuple[str, int]] = []
    offset: int | None = 0
    while offset is not None:
        part = await ok(client, "similar_items", node=firsts[0]["ref"], limit=2, offset=offset)
        seen.extend(key_of(i) for i in part["items"])
        offset = part["next_offset"]
    assert len(seen) == len(set(seen)) == part["total"]

    for arguments, expected in (
        ({}, "Give exactly one of node, rule_id"),
        ({"node": "story:1", "text": "x"}, "Give exactly one of node, rule_id"),
        ({"fact_id": 1, "decision_id": 1}, "Give exactly one of node, rule_id"),
        ({"node": "story:987654321"}, "There is no story 987654321 in this project"),
        ({"rule_id": 987654321}, "There is no requirement with id 987654321"),
        ({"decision_id": 987654321}, "There is no decision with id 987654321"),
        ({"fact_id": 987654321}, "There is no fact with id 987654321"),
        ({"text": "  "}, "text cannot be empty"),
        ({"text": "x", "types": ["story", "idea"]}, "types must be one of"),
        ({"text": "x", "min_similarity": -0.1}, "min_similarity must be a number from 0 to 1"),
    ):
        assert expected in await refused(client, "similar_items", **arguments)


async def test_recall_can_add_what_is_alike_but_not_linked_and_says_it_is_a_suggestion(
    client: Client,
) -> None:
    story = await new_story(client, "Customers receive an invoice by email after payment")
    linked_decision = await make_decision(
        client, "Linked", text="Customers get an invoice by email", nodes=[story["ref"]]
    )
    linked_fact = await make_fact(
        client, "Clients receive a bill by mail after payment", nodes=[story["ref"]]
    )
    alike_decision = await make_decision(
        client,
        "Billing mail",
        text="Clients receive a bill by mail after charge",
        reason="Clients pay",
    )
    alike_fact = await make_fact(client, "A customer receives an invoice by email after payment")
    confirmed_alike = await make_fact(client, "Clients receive a bill by mail after charge")
    await confirm(client, "fact", confirmed_alike)
    unrelated = await make_fact(client, "Dark theme for the mobile app")
    wide = await make_decision(
        client,
        "Standing",
        text="Customers receive an invoice by email",
        project_wide=True,
    )
    withdrawn = await make_fact(client, "A client receives a bill by mail after payment")
    await ok(client, "withdraw_fact", actor="a", fact_ids=[withdrawn["id"]], reason="Wrong")
    replaced = await make_decision(
        client, "Old billing mail", text="Customers get a bill by mail after payment"
    )
    await ok(
        client,
        "supersede_decision",
        actor="a",
        decision_id=replaced["id"],
        title="Dark theme",
        text="A dark theme for the mobile app",
        reason="Changed",
        decided_by="Marie",
    )
    await reindexed(client)

    plain = await ok(client, "recall", node=story["ref"])
    assert plain["similar"] is None

    result = await ok(client, "recall", node=story["ref"], with_similar=True)

    similar = result["similar"]
    found = by_item({"items": similar["items"]})
    assert ("decision", alike_decision["id"]) in found
    assert ("fact", alike_fact["id"]) in found and ("fact", confirmed_alike["id"]) in found
    assert all(item["suggestion"] and item["matched"] == "meaning" for item in found.values())
    # A suggestion says whether a person confirmed it, like everything else a record shows.
    assert found[("fact", alike_fact["id"])]["confirmation"] == "unconfirmed"
    assert found[("fact", confirmed_alike["id"])]["confirmation"] == "confirmed"
    # What is linked, a project-wide decision (it has its own section), something unrelated,
    # something withdrawn and something replaced are not here.
    for left_out in (
        ("decision", linked_decision["id"]),
        ("fact", linked_fact["id"]),
        ("decision", wide["id"]),
        ("fact", unrelated["id"]),
        ("fact", withdrawn["id"]),
        ("decision", replaced["id"]),
    ):
        assert left_out not in found
    assert similar["count"] == len(similar["items"]) and similar["index"]["meaning"] == "used"
    assert wide["id"] in {
        item["decision"]["id"] for item in result["project_wide_decisions"]["items"]
    }
    # The exact sections are what they were without it.
    for name in (
        "active_decisions",
        "project_wide_decisions",
        "facts",
        "past_decisions",
        "past_facts",
    ):
        assert result[name] == plain[name]

    # With the service stopped, a story that is not indexed has no vector to compare, so it gets
    # no suggestions and says why, and everything exact is still there.
    with embeddings_stopped():
        fresh = await new_story(client, "Customers receive an invoice by email after payment")
        down = await ok(client, "recall", node=fresh["ref"], with_similar=True)
        assert down["similar"]["items"] == []
        assert down["similar"]["index"]["meaning"] == "service_unreachable"
        assert down["project_wide_decisions"]["count"] >= 1


# -- nothing but the reindex job writes to the index ------------------------------------------


async def test_reading_tools_leave_the_index_exactly_as_it_was(client: Client) -> None:
    epic_a, _, firsts, _ = await make_overlap_fixture(client)
    snapshot_sql = (
        "SELECT md5(string_agg(item_type || ':' || item_id || ':' || content_hash || ':' || "
        "indexed_at::text || ':' || embedding::text, ',' ORDER BY item_type, item_id)) "
        "FROM semantic_index"
    )

    def snapshot() -> str:
        # As the shared project's own work sees it: the background indexer keeps indexing other
        # projects' items, which is not what this test is about.
        result = run_sql(snapshot_sql, plain=True, as_project=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    before = snapshot()
    assert before
    word = unique_word()
    await search(client, word)
    await search(client, "invoice by email", mode="meaning")
    await ok(client, "similar_items", node=firsts[0]["ref"])
    await ok(client, "similar_items", text="anything at all")
    await ok(client, "find_overlaps", epics=[epic_a["ref"]])
    await ok(client, "recall", node=firsts[0]["ref"], with_similar=True)
    await ok(client, "list_nodes", limit=5)
    await ok(client, "get_node", node=firsts[0]["ref"])
    await ok(client, "node_history", node=firsts[0]["ref"])
    await ok(client, "server_status")
    assert snapshot() == before
