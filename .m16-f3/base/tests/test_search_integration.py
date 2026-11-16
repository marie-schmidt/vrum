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
    embeddings_stopped,
    make_belief,
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


def by_item(page: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(item["item_type"], item["id"]): item for item in page["items"]}


async def listing(client: Client, tool: str, **filters: Any) -> list[dict[str, Any]]:
    """Every item a list tool has, following the pages."""
    found: list[dict[str, Any]] = []
    after_id = None
    while True:
        page = await ok(client, tool, limit=200, after_id=after_id, **filters)
        found.extend(page["items"])
        after_id = page["next_after_id"]
        if after_id is None:
            return found


def text_of(item: dict[str, Any]) -> str:
    return " ".join(str(value) for value in item.values() if isinstance(value, str))


async def new_story(
    client: Client, title: str, body: str = "", parent_id: int | None = None
) -> dict[str, Any]:
    """A story with this title and body, under the given epic or under an epic of its own."""
    if parent_id is None:
        epic = await ok(client, "create_node", actor="marie", kind="epic", title="An epic")
        parent_id = epic["id"]
    return await ok(
        client,
        "create_node",
        actor="marie",
        kind="story",
        title=title,
        body=body,
        parent_id=parent_id,
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
        parent_id=epic["id"],
    )
    task = await ok(
        client,
        "create_node",
        actor="a",
        kind="task",
        title=f"Do the {word} part",
        parent_id=story["id"],
    )
    rule = await make_rule(client, "A rule", body=f"Never skip {word}.", story_id=story["id"])
    decision = await make_decision(client, "A decision", text=f"We choose {word}.")
    principle = await make_decision(client, "A principle", kind="principle", text=f"Always {word}.")
    fact = await make_fact(client, f"The {word} service is slow.")
    belief = await make_belief(client, [fact], f"Probably {word} needs a cache.")
    expected = {
        ("epic", epic["id"]),
        ("story", story["id"]),
        ("task", task["id"]),
        ("rule", rule["id"]),
        ("decision", decision["id"]),
        ("principle", principle["id"]),
        ("fact", fact["id"]),
        ("belief", belief["id"]),
    }

    page = await search(client, word, mode="words", limit=200)

    results = by_item(page)
    assert set(results) == expected and page["total"] == 8
    for item in results.values():
        assert item["matched"] == "words" and item["similarity"] is None
        assert item["why"] == f"exact words: {word}" and item["words_matched"] == [word]
        assert f"«{word}»" in item["snippet"] and item["snippet"].count("«") >= 1
    # Parent and status are read from the graph now.
    assert results[("story", story["id"])]["parent_id"] == epic["id"]
    assert results[("story", story["id"])]["parent_kind"] == "epic"
    assert results[("story", story["id"])]["parent_title"] == f"Plan {word}"
    assert results[("task", task["id"])]["parent_id"] == story["id"]
    assert results[("rule", rule["id"])]["parent_id"] == story["id"]
    assert results[("epic", epic["id"])]["parent_id"] is None
    assert {results[key]["status"] for key in expected if key[0] in ("epic", "story", "task")} == {
        "ready"
    }
    assert results[("rule", rule["id"])]["status"] == "held"
    # Each result carries its origin and a label that says what it is.
    assert results[("decision", decision["id"])]["origin"] == "decided"
    assert "Decision decided by Marie" in results[("decision", decision["id"])]["label"]
    assert "Principle decided by Marie" in results[("principle", principle["id"])]["label"]
    assert results[("fact", fact["id"])]["origin"] == "stated"
    assert "Fact (stated)" in results[("fact", fact["id"])]["label"]
    assert results[("belief", belief["id"])]["origin"] == "stated"
    assert "Suggestion" in results[("belief", belief["id"])]["label"]
    assert results[("belief", belief["id"])]["suggestion"] is True
    assert results[("decision", decision["id"])]["suggestion"] is False
    assert results[("fact", fact["id"])]["suggestion"] is False
    assert results[("fact", fact["id"])]["title"].startswith("The ")

    # The counts equal what the list tools return for the same words.
    nodes = [
        n
        for kind in ("epic", "story", "task")
        for n in await listing(client, "list_nodes", kind=kind)
    ]
    lists = {
        **{kind: [n for n in nodes if n["kind"] == kind] for kind in ("epic", "story", "task")},
        "rule": await listing(client, "list_rules"),
        "decision": await listing(client, "list_decisions", kind="decision"),
        "principle": await listing(client, "list_decisions", kind="principle"),
        "fact": await listing(client, "list_memories", type="fact"),
        "belief": await listing(client, "list_memories", type="belief"),
    }
    for item_type, items in lists.items():
        containing = [item for item in items if word in text_of(item)]
        assert page["counts_by_type"][item_type] == len(containing) == 1, item_type
    assert page["counts_by_type"] == {kind: 1 for kind in sorted(lists)}
    assert page["counts_by_status"] == {"active": 4, "held": 1, "ready": 3}

    # Filters narrow the results and leave the counts as they were, to show what filtering gives.
    narrowed = await search(client, word, mode="words", types=["story", "task"])
    assert set(by_item(narrowed)) == {("story", story["id"]), ("task", task["id"])}
    assert narrowed["total"] == 2 and narrowed["counts_by_type"] == page["counts_by_type"]
    held = await search(client, word, mode="words", statuses=["held"])
    assert set(by_item(held)) == {("rule", rule["id"])} and held["counts_by_status"]["ready"] == 3
    both = await search(client, word, mode="words", types=["story", "rule"], statuses=["held"])
    assert set(by_item(both)) == {("rule", rule["id"])}

    # Pages of three cover everything once, in the same order as one page of everything.
    seen: list[tuple[str, int]] = []
    offset: int | None = 0
    while offset is not None:
        part = await search(client, word, mode="words", limit=3, offset=offset)
        assert part["total"] == 8
        seen.extend((item["item_type"], item["id"]) for item in part["items"])
        offset = part["next_offset"]
    assert seen == [(item["item_type"], item["id"]) for item in page["items"]]
    assert len(set(seen)) == 8

    for arguments, expected_message in (
        ({"types": ["epics"]}, "types must be one of epic, story, task"),
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
        node_id=story["id"],
        expected_version=story["version"],
        body=f"We are migrating the {word} accounts.",
    )

    page = await search(client, word, mode="words")
    assert [(i["item_type"], i["id"]) for i in page["items"]] == [("story", story["id"])]
    # Words are whole words, with their endings: "migration" finds "migrating".
    stemmed = await search(client, f"migration {word}", mode="words")
    assert stemmed["total"] == 1
    # And a later edit takes the word away again.
    await ok(
        client,
        "update_node",
        actor="agent-1",
        node_id=story["id"],
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

    def ids(page: dict[str, Any]) -> set[int]:
        return {item["id"] for item in page["items"]}

    assert ids(await search(client, f"{first} {second}", mode="words")) == {both["id"]}
    assert ids(await search(client, f"{first} or {second}", mode="words")) == {
        both["id"],
        only_first["id"],
        only_second["id"],
    }
    assert ids(await search(client, f'"{first} comes before {second}"', mode="words")) == {
        both["id"]
    }
    assert ids(await search(client, f'"{second} comes before {first}"', mode="words")) == set()
    assert ids(await search(client, f"{first} -{second}", mode="words")) == {only_first["id"]}
    # The best match, with the most to say about the words, is listed first.
    ranked = await search(client, f"{first} or {second}", mode="words")
    assert ranked["items"][0]["id"] == both["id"]


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

    result = by_item(page)[("story", story["id"])]
    assert result["matched"] == "meaning" and result["suggestion"] is True
    assert result["words_matched"] == []
    assert result["similarity"] == pytest.approx(expected, abs=0.002)
    assert result["why"].startswith("similar in meaning (score ")
    assert page["index"]["meaning"] == "used"
    assert not re.search(r"«", result["snippet"])
    # The same words ask for words only: it has none of them, so it is not found.
    assert ("story", story["id"]) not in by_item(await search(client, paraphrase, mode="words"))
    # And asking for meaning only finds it too.
    assert ("story", story["id"]) in by_item(await search(client, paraphrase, mode="meaning"))
    # A floor of 1 leaves only what is identical, so it is left out.
    assert ("story", story["id"]) not in by_item(
        await search(client, paraphrase, min_similarity=1.0)
    )


async def test_a_fact_and_a_belief_are_found_by_words_and_meaning_with_their_origin(
    client: Client,
) -> None:
    word = unique_word()
    fact = await make_fact(client, f"Customers receive an invoice by email {word}")
    belief = await make_belief(client, [fact], f"Clients receive a bill by mail {word}")
    # Nothing in the product writes a model's output yet (that is M5C), so these two are as a
    # model would leave them, written straight into the database.
    extracted = run_sql(
        "INSERT INTO memories (type, kind, text, source, origin, recorded_by) VALUES "
        f"('fact', 'world', 'The customer is sent an invoice by email {word}', "
        "'a quoted passage', 'extracted', 'agent-1') RETURNING id",
        plain=True,
    )
    assert extracted.returncode == 0, extracted.stderr
    extracted_id = int(extracted.stdout.splitlines()[0])
    derived = run_sql(
        "WITH b AS (INSERT INTO memories (type, text, origin, recorded_by) VALUES "
        f"('belief', 'The client is mailed a bill {word}', 'derived', 'model') RETURNING id) "
        f"INSERT INTO belief_evidence (belief_id, fact_id) SELECT id, {fact['id']} FROM b "
        "RETURNING belief_id",
        plain=True,
    )
    assert derived.returncode == 0, derived.stderr
    derived_id = int(derived.stdout.splitlines()[0])
    await reindexed(client)

    by_words = by_item(await search(client, word, mode="words", limit=50))
    by_meaning = by_item(
        await search(client, "Clients get a bill by mail", mode="meaning", limit=50)
    )

    for key in (
        ("fact", fact["id"]),
        ("belief", belief["id"]),
        ("fact", extracted_id),
        ("belief", derived_id),
    ):
        assert key in by_words and by_words[key]["matched"] == "words", key
        assert key in by_meaning and by_meaning[key]["matched"] == "meaning", key
        assert by_meaning[key]["similarity"] is not None
    for results in (by_words, by_meaning):
        assert results[("fact", fact["id"])]["origin"] == "stated"
        assert results[("fact", fact["id"])]["suggestion"] is (results is by_meaning)
        assert results[("belief", belief["id"])]["origin"] == "stated"
        assert results[("fact", extracted_id)]["origin"] == "extracted"
        assert "proposed by a language model" in results[("fact", extracted_id)]["label"]
        assert results[("fact", extracted_id)]["suggestion"] is True
        assert results[("belief", derived_id)]["origin"] == "derived"
        assert "drawn up by a language model" in results[("belief", derived_id)]["label"]
        assert results[("belief", derived_id)]["suggestion"] is True
    # Never mistaken for a decision: none of them is one, and each says what it is.
    assert not any(item["origin"] == "decided" for item in by_words.values())
    assert not any(item["item_type"] in ("decision", "principle") for item in by_words.values())


async def test_what_matches_both_ways_is_labelled_both_and_comes_first(client: Client) -> None:
    word = unique_word()
    title = f"Invoice reminders {word}"
    body = "Send customers a reminder email when payment is late."
    story = await new_story(client, title, body)
    await reindexed(client)

    page = await search(client, f"{title} {body}")

    first = page["items"][0]
    assert (first["item_type"], first["id"]) == ("story", story["id"])
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
            client, f"Customers get an invoice by email {word}", parent_id=epic["id"]
        )

        # Found by words at once, labelled as an exact-words match, and said to be words only.
        page = await search(client, word)
        result = by_item(page)[("story", story["id"])]
        assert result["matched"] == "words" and result["similarity"] is None
        assert page["index"]["meaning"] == "service_unreachable"
        assert "cannot be reached" in page["index"]["note"]
        assert page["index"]["waiting_to_index"] == before + 1

        # The paraphrase has no words in common, so without meaning it is not found.
        assert ("story", story["id"]) not in by_item(await search(client, paraphrase))
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
        assert "items" in await ok(client, "similar_items", node_id=indexed["id"])

    # Back again: reindex picks the story up, and the paraphrase is found by meaning.
    report = await reindexed(client)
    assert report["error"] is None and report["failed"] == []
    again = await search(client, f"Customers get an invoice by email {word}")
    assert by_item(again)[("story", story["id"])]["matched"] == "both"
    assert again["index"]["meaning"] == "used" and again["index"]["waiting_to_index"] == 0
    assert by_item(await search(client, paraphrase))[("story", story["id"])]["matched"] == "meaning"


async def test_the_server_indexes_new_work_by_itself(client: Client) -> None:
    await reindexed(client)
    word = unique_word()
    story = await new_story(client, f"Customers get an invoice by email {word}")
    paraphrase = "Clients get a bill by mail"
    assert cosine(embed(paraphrase), embed(f"Customers get an invoice by email {word}")) >= 0.7

    # No reindex is asked for: the background job finds it within a few seconds.
    for _ in range(40):
        page = await search(client, paraphrase, mode="meaning")
        if ("story", story["id"]) in by_item(page):
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
            client, "create_node", actor="a", kind="task", title="A task", parent_id=story["id"]
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
            node_id=story["id"],
            expected_version=story["version"],
            body="Changed text",
        )
        await ok(
            client,
            "update_node",
            actor="a",
            node_id=task["id"],
            expected_version=task["version"],
            body="Changed task text",
        )
        assert await waiting(client) == start + 5
    await reindexed(client)
    # A change that is not to the text (here, the status) leaves it indexed.
    current = await ok(client, "get_node", node_id=story["id"])
    await ok(
        client,
        "update_node",
        actor="a",
        node_id=story["id"],
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
        parent_id=story["id"],
    )
    await reindexed(client)

    by_words = await search(client, word)
    assert by_item(by_words)[("task", task["id"])]["matched"] == "words"
    by_meaning = await search(client, "Clients receive a bill by mail", mode="meaning")
    assert ("task", task["id"]) not in by_item(by_meaning)
    count = await ok(client, "reindex")
    assert count["indexed_total"] >= 1 and count["embedded"] == 0
    # A task can still be the thing to find similar items for: its text is embedded as asked.
    similar = await ok(client, "similar_items", node_id=task["id"], min_similarity=0.0, limit=5)
    assert similar["items"] and all(i["matched"] == "meaning" for i in similar["items"])
    assert ("task", task["id"]) not in by_item(similar)


async def test_dropping_the_index_and_rebuilding_it_gives_the_same_results(
    client: Client,
) -> None:
    word = unique_word()
    epic_a = await ok(client, "create_node", actor="a", kind="epic", title="Billing")
    epic_b = await ok(client, "create_node", actor="a", kind="epic", title="Emails")
    first = await new_story(
        client, f"Customers receive an invoice by email {word}", parent_id=epic_a["id"]
    )
    second = await new_story(
        client, f"Clients receive a bill by mail {word}", parent_id=epic_b["id"]
    )
    decision = await make_decision(client, "Bills", text=f"A bill goes out by mail {word}")
    await reindexed(client)

    async def results() -> dict[str, Any]:
        meaning = await search(client, "Send the bill to a client by mail", mode="meaning")
        similar = await ok(client, "similar_items", node_id=first["id"], min_similarity=0.3)
        overlaps = await ok(client, "find_overlaps", epic_ids=[epic_a["id"]])
        return {
            "meaning": [(i["item_type"], i["id"], i["similarity"]) for i in meaning["items"]],
            "similar": [(i["item_type"], i["id"], i["similarity"]) for i in similar["items"]],
            "overlaps": [
                (p["first"]["id"], p["second"]["id"], p["similarity"]) for p in overlaps["items"]
            ],
        }

    before = await results()
    assert ("story", first["id"]) in {(t, i) for t, i, _ in before["meaning"]}
    assert (first["id"], second["id"]) in {(a, b) for a, b, _ in before["overlaps"]}
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
    assert ("story", story["id"]) in by_item(again)


async def test_an_item_the_service_refuses_is_reported_and_does_not_stop_the_others(
    client: Client,
) -> None:
    await reindexed(client)
    word = unique_word()
    refused_story = await new_story(client, f"Too long {word}", "[[reject-me]]")
    fine = await new_story(client, f"Customers receive an invoice by email {word}")

    report = await ok(client, "reindex")

    assert [(f["item_type"], f["id"]) for f in report["failed"]] == [("story", refused_story["id"])]
    assert "refused the text" in report["failed"][0]["reason"]
    assert report["error"] is None and report["waiting_after"] == 1
    found = await search(client, "Clients receive a bill by mail", mode="meaning")
    assert ("story", fine["id"]) in by_item(found)
    # It is still found by words, and stays waiting until its text changes.
    assert ("story", refused_story["id"]) in by_item(await search(client, word, mode="words"))
    assert await waiting(client) == 1
    await ok(
        client,
        "update_node",
        actor="a",
        node_id=refused_story["id"],
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
    firsts = [await new_story(client, first, parent_id=epic_a["id"]) for first, _ in _DUPLICATES]
    seconds = [await new_story(client, second, parent_id=epic_b["id"]) for _, second in _DUPLICATES]
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
        (a["id"], b["id"]): (i == j) for i, a in enumerate(firsts) for j, b in enumerate(seconds)
    }
    duplicates = {pair for pair, is_duplicate in pairs.items() if is_duplicate}
    unrelated = {pair for pair, is_duplicate in pairs.items() if not is_duplicate}
    assert len(duplicates) == 8 and len(unrelated) == 56
    # The fixture is what it says: known duplicates are alike and known unrelated pairs are not.
    scores = {
        (a["id"], b["id"]): cosine(embed(a["title"]), embed(b["title"]))
        for a in firsts
        for b in seconds
    }
    assert min(scores[pair] for pair in duplicates) >= 0.85
    assert max(scores[pair] for pair in unrelated) <= 0.4

    page = await every_overlap(client, epic_ids=[epic_a["id"], epic_b["id"]])

    found = {(p["first"]["id"], p["second"]["id"]): p for p in page["items"]}
    both_in_fixture = {
        pair
        for pair in found
        if pair[0] in {s["id"] for s in firsts + seconds}
        and pair[1] in {s["id"] for s in firsts + seconds}
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
    sample = found[(firsts[0]["id"], seconds[0]["id"])]
    assert (sample["first"]["epic_id"], sample["second"]["epic_id"]) == (epic_a["id"], epic_b["id"])
    assert sample["first"]["epic_title"] == "Overlap fixture A"
    assert sample["first"]["status"] == "ready" and sample["already_linked"] == []
    # Stories in the same epic are never a pair, however alike.
    twin = await new_story(client, _DUPLICATES[0][0], parent_id=epic_a["id"])
    await reindexed(client)
    again = await every_overlap(client, epic_ids=[epic_a["id"]])
    assert not any(
        {p["first"]["id"], p["second"]["id"]} == {twin["id"], firsts[0]["id"]}
        for p in again["items"]
    )
    assert any(
        {p["first"]["id"], p["second"]["id"]} == {twin["id"], seconds[0]["id"]}
        for p in again["items"]
    )

    # A higher bar leaves fewer, and a link that already exists is shown.
    strict = await every_overlap(client, epic_ids=[epic_a["id"]], min_similarity=0.999)
    assert strict["threshold"] == 0.999
    assert 0 < len(strict["items"]) < len(again["items"])
    await ok(
        client,
        "add_link",
        actor="a",
        source_id=firsts[1]["id"],
        target_id=seconds[1]["id"],
        link_type="duplicates",
    )
    linked = await every_overlap(client, epic_ids=[epic_a["id"]])
    pair = next(
        p
        for p in linked["items"]
        if {p["first"]["id"], p["second"]["id"]} == {firsts[1]["id"], seconds[1]["id"]}
    )
    assert pair["already_linked"] == ["duplicates"]
    # A dropped story is not compared unless asked for.
    current = await ok(client, "get_node", node_id=seconds[2]["id"])
    await ok(
        client,
        "update_node",
        actor="a",
        node_id=seconds[2]["id"],
        expected_version=current["version"],
        status="dropped",
    )
    gone = {
        (p["first"]["id"], p["second"]["id"])
        for p in (await every_overlap(client, epic_ids=[epic_a["id"]]))["items"]
    }
    assert (firsts[2]["id"], seconds[2]["id"]) not in gone
    kept = {
        (p["first"]["id"], p["second"]["id"])
        for p in (await every_overlap(client, epic_ids=[epic_a["id"]], include_dropped=True))[
            "items"
        ]
    }
    assert (firsts[2]["id"], seconds[2]["id"]) in kept


async def test_the_overlap_finder_only_reports_and_changes_nothing(client: Client) -> None:
    epic_a, epic_b, firsts, seconds = await make_overlap_fixture(client)
    nodes = [*firsts, *seconds]

    async def state() -> list[Any]:
        return [
            (
                (await ok(client, "get_node", node_id=node["id"]))["version"],
                len((await ok(client, "node_history", node_id=node["id"]))["items"]),
                len((await ok(client, "list_links", node_id=node["id"]))["items"]),
            )
            for node in nodes
        ]

    before = await state()
    page = await every_overlap(client, epic_ids=[epic_a["id"], epic_b["id"]])
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
        client, "Every customer gets an invoice by email", story_id=story["id"]
    )
    rule_two = await make_rule(client, "Each client gets a bill by mail", story_id=other["id"])
    await reindexed(client)

    epics = await every_overlap(client, item_type="epic", epic_ids=[first["id"]])
    assert {(p["first"]["id"], p["second"]["id"]) for p in epics["items"]} >= {
        (first["id"], second["id"])
    }
    assert all(p["first"]["item_type"] == "epic" for p in epics["items"])
    rules = await every_overlap(client, item_type="rule", epic_ids=[story["parent_id"]])
    assert (rule_one["id"], rule_two["id"]) in {
        (p["first"]["id"], p["second"]["id"]) for p in rules["items"]
    }

    assert "item_type must be one of story, epic, rule" in await refused(
        client, "find_overlaps", item_type="task"
    )
    assert "There is no epic with id 987654321" in await refused(
        client, "find_overlaps", epic_ids=[987654321]
    )
    assert "min_similarity must be a number from 0 to 1" in await refused(
        client, "find_overlaps", min_similarity=2
    )
    assert "offset must be 0 or more" in await refused(client, "find_overlaps", offset=-5)
    # Paging: a page of one, then the rest.
    one = await ok(client, "find_overlaps", item_type="epic", epic_ids=[first["id"]], limit=1)
    assert len(one["items"]) == 1 and one["total"] == len(epics["items"])
    if one["total"] > 1:
        assert one["next_offset"] == 1
    past = await ok(client, "find_overlaps", item_type="epic", epic_ids=[first["id"]], offset=500)
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
    belief = await make_belief(client, [fact], "Mailing a bill to a client after charging is good")
    await reindexed(client)

    page = await ok(client, "similar_items", node_id=firsts[0]["id"], limit=200)

    results = by_item(page)
    assert ("story", firsts[0]["id"]) not in results
    assert ("story", seconds[0]["id"]) in results
    assert ("fact", fact["id"]) in results and ("decision", decision["id"]) in results
    assert all(r["matched"] == "meaning" and r["suggestion"] for r in results.values())
    assert page["items"][0]["similarity"] >= page["items"][-1]["similarity"]
    assert page["counts_by_type"]["story"] >= 1 and page["index"]["meaning"] == "used"
    assert ("belief", belief["id"]) in by_item(
        await ok(client, "similar_items", memory_id=fact["id"], min_similarity=0.2, limit=50)
    )

    only = await ok(
        client, "similar_items", node_id=firsts[0]["id"], types=["decision", "fact", "belief"]
    )
    assert {i["item_type"] for i in only["items"]} <= {"decision", "fact", "belief"}
    assert ("decision", decision["id"]) in by_item(only)
    for_text = await ok(
        client, "similar_items", text="Mail every client a bill after charge", limit=50
    )
    assert ("story", seconds[0]["id"]) in by_item(for_text)
    for_decision = await ok(client, "similar_items", decision_id=decision["id"], types=["story"])
    assert ("story", firsts[0]["id"]) in by_item(for_decision)
    for_rule = await make_rule(
        client, "Send customers an invoice by email after payment", story_id=firsts[0]["id"]
    )
    await reindexed(client)
    assert ("story", firsts[0]["id"]) in by_item(
        await ok(client, "similar_items", rule_id=for_rule["id"], types=["story"])
    )
    # A paged walk gives each once.
    seen: list[tuple[str, int]] = []
    offset: int | None = 0
    while offset is not None:
        part = await ok(client, "similar_items", node_id=firsts[0]["id"], limit=2, offset=offset)
        seen.extend((i["item_type"], i["id"]) for i in part["items"])
        offset = part["next_offset"]
    assert len(seen) == len(set(seen)) == part["total"]

    for arguments, expected in (
        ({}, "Give exactly one of node_id"),
        ({"node_id": 1, "text": "x"}, "Give exactly one of node_id"),
        ({"node_id": 987654321}, "There is no node with id 987654321"),
        ({"rule_id": 987654321}, "There is no rule with id 987654321"),
        ({"decision_id": 987654321}, "There is no decision with id 987654321"),
        ({"memory_id": 987654321}, "There is no fact or belief with id 987654321"),
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
        client, "Linked", text="Customers get an invoice by email", node_ids=[story["id"]]
    )
    linked_fact = await make_fact(
        client, "Clients receive a bill by mail after payment", node_ids=[story["id"]]
    )
    alike_decision = await make_decision(
        client,
        "Billing mail",
        text="Clients receive a bill by mail after charge",
        reason="Clients pay",
    )
    alike_fact = await make_fact(client, "A customer receives an invoice by email after payment")
    alike_belief = await make_belief(
        client, [alike_fact], "Clients receive a bill by mail after charge"
    )
    unrelated = await make_fact(client, "Dark theme for the mobile app")
    principle = await make_decision(
        client, "Standing", kind="principle", text="Customers receive an invoice by email"
    )
    retracted = await make_fact(client, "A client receives a bill by mail after payment")
    await ok(client, "retract_memory", actor="a", memory_ids=[retracted["id"]], reason="Wrong")
    await reindexed(client)

    plain = await ok(client, "recall", node_id=story["id"])
    assert plain["similar"] is None

    result = await ok(client, "recall", node_id=story["id"], with_similar=True)

    similar = result["similar"]
    found = by_item({"items": similar["items"]})
    assert ("decision", alike_decision["id"]) in found
    assert ("fact", alike_fact["id"]) in found and ("belief", alike_belief["id"]) in found
    assert all(item["suggestion"] and item["matched"] == "meaning" for item in found.values())
    # Whatever is linked, a principle, something unrelated and something retracted are not here.
    for left_out in (
        ("decision", linked_decision["id"]),
        ("fact", linked_fact["id"]),
        ("principle", principle["id"]),
        ("fact", unrelated["id"]),
        ("fact", retracted["id"]),
    ):
        assert left_out not in found
    assert similar["count"] == len(similar["items"]) and similar["index"]["meaning"] == "used"
    # The exact sections are what they were without it.
    for name in ("active_decisions", "facts", "beliefs", "stale_beliefs", "past_memories"):
        assert result[name] == plain[name]

    # With the service stopped, a story that is not indexed has no vector to compare, so it gets
    # no suggestions and says why, and everything exact is still there.
    with embeddings_stopped():
        fresh = await new_story(client, "Customers receive an invoice by email after payment")
        down = await ok(client, "recall", node_id=fresh["id"], with_similar=True)
        assert down["similar"]["items"] == []
        assert down["similar"]["index"]["meaning"] == "service_unreachable"
        assert down["active_principles"]["count"] >= 1


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
    await ok(client, "similar_items", node_id=firsts[0]["id"])
    await ok(client, "similar_items", text="anything at all")
    await ok(client, "find_overlaps", epic_ids=[epic_a["id"]])
    await ok(client, "recall", node_id=firsts[0]["id"], with_similar=True)
    await ok(client, "list_nodes", limit=5)
    await ok(client, "get_node", node_id=firsts[0]["id"])
    await ok(client, "node_history", node_id=firsts[0]["id"])
    await ok(client, "server_status")
    assert snapshot() == before
