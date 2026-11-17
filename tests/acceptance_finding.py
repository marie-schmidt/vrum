"""Chapter 6 of the acceptance scenario: finding things by exact words and by meaning, and how each
result says how it matched (M5), and the check for similar items when something is created (M5B)."""

from typing import Any

from mcp import Client

from tests.acceptance_scenario import URL, chapter, clip, proof, sql_value, stories
from tests.board_search import DUPLICATES
from tests.helpers import (
    all_nodes,
    confirm,
    connect,
    embeddings_stopped,
    make_decision,
    make_fact,
    ok,
    ref_of,
    refused,
    reindexed,
    run_sql,
    the_token,
    unique_word,
)
from tests.scripted_embeddings import cosine, embed


async def search(client: Client, query: str, **arguments: Any) -> dict[str, Any]:
    return await ok(client, "search", query=query, **arguments)


def by_item(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The results of a search by reference: every kind has one (story:12, rule:3, fact:5)."""
    return {item["ref"]: item for item in page["items"]}


async def listing(client: Client, tool: str, **filters: Any) -> list[dict[str, Any]]:
    """Every rule, decision or fact a list tool has, following the pages."""
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


async def story_with(client: Client, title: str, body: str = "") -> dict[str, Any]:
    (epic,) = (await stories(client, 0))[:1]
    return await ok(
        client, "create_node", actor="marie", kind="story", title=title, body=body,
        parent=epic["ref"],
    )  # fmt: skip


async def every_overlap(client: Client, **arguments: Any) -> dict[str, Any]:
    page = await ok(client, "find_overlaps", limit=200, **arguments)
    items = list(page["items"])
    while page["next_offset"] is not None:
        page = await ok(client, "find_overlaps", limit=200, offset=page["next_offset"], **arguments)
        items.extend(page["items"])
    return {**page, "items": items}


async def finding() -> None:
    chapter("Finding things: by exact words, by meaning, and how each result says it matched")
    async with connect(URL, the_token()) as client:
        # -- exact words, at once, for every kind of item
        word = unique_word()
        epic = await ok(client, "create_node", actor="a", kind="epic", title=f"Plan {word}")
        story = await ok(
            client, "create_node", actor="a", kind="story", title="Checkout",
            body=f"We must handle {word} carefully.", parent=epic["ref"],
        )  # fmt: skip
        task = await ok(
            client, "create_node", actor="a", kind="task", title=f"Do the {word} part",
            parent=story["ref"],
        )  # fmt: skip
        rule = await ok(
            client, "create_rule", actor="a", title="A rule", body=f"Never skip {word}.",
            story=story["ref"],
        )  # fmt: skip
        decision = await make_decision(client, "A decision", text=f"We choose {word}.")
        decision = await confirm(client, "decision", decision, "Marie")
        fact = await make_fact(client, f"The {word} service is slow.")
        fact_ref, decision_ref = ref_of("fact", fact["id"]), ref_of("decision", decision["id"])
        expected = {
            epic["ref"], story["ref"], task["ref"], ref_of("rule", rule["id"]),
            decision_ref, fact_ref,
        }  # fmt: skip
        page = await search(client, word, mode="words", limit=200)
        results = by_item(page)
        proof(
            "M5",
            "every kind of item is found by its exact words the moment it is saved, and each "
            "result is labelled an exact-words match",
            set(results) == expected
            and all(r["matched"] == "words" and r["similarity"] is None for r in results.values())
            and all(r["why"] == f"exact words: {word}" for r in results.values()),
            f"{page['total']} items, no index built yet",
        )
        nodes = [
            n for kind in ("epic", "story", "task") for n in await all_nodes(client, kind=kind)
        ]
        lists = {
            **{kind: [n for n in nodes if n["kind"] == kind] for kind in ("epic", "story", "task")},
            "rule": await listing(client, "list_rules"),
            "decision": await listing(client, "list_decisions"),
            "fact": await listing(client, "list_facts"),
        }
        from_lists = {
            kind: len([item for item in items if word in text_of(item)])
            for kind, items in lists.items()
        }
        proof(
            "M5",
            "the counts by item type equal what the list tools return",
            page["counts_by_type"] == from_lists == {kind: 1 for kind in sorted(lists)},
            str(page["counts_by_type"]),
        )
        proof(
            "M5",
            "a fact and a decision are found by words, each carrying its confirmation and whether "
            "it is in force, so an unconfirmed fact is never mistaken for a confirmed decision",
            (results[fact_ref]["confirmation"], results[fact_ref]["status"])
            == ("unconfirmed", "active")
            and results[fact_ref]["label"].startswith("Fact, not confirmed by a person")
            and results[decision_ref]["confirmation"] == "confirmed"
            and results[decision_ref]["label"].startswith("Decision, confirmed by Marie on "),
            results[fact_ref]["label"][:60],
        )

        # -- meaning
        w = unique_word()
        title = f"Customers receive an invoice by email {w}"
        body = "After payment the client gets a bill by mail."
        found_story = await story_with(client, title, body)
        found_fact = await make_fact(client, title)
        found_decision = await make_decision(client, f"Clients receive a bill by mail {w}")
        await reindexed(client)
        paraphrase = "Send the bill to clients by mail once charged"
        score = cosine(embed(f"{title}\n{body}"), embed(paraphrase))
        page = await search(client, paraphrase)
        hit = by_item(page)[found_story["ref"]]
        proof(
            "M5",
            "a paraphrase that shares none of its words is found by meaning and labelled a "
            "suggestion with its score",
            hit["matched"] == "meaning"
            and hit["suggestion"] is True
            and hit["words_matched"] == []
            and abs(hit["similarity"] - score) < 0.002
            and found_story["ref"] not in by_item(await search(client, paraphrase, mode="words")),
            hit["why"],
        )
        page = await search(client, f"{title} {body}")
        first = page["items"][0]
        proof(
            "M5",
            "a result that matches both ways is labelled both and comes first",
            first["ref"] == found_story["ref"]
            and first["matched"] == "both"
            and first["suggestion"] is False
            and first["similarity"] > 0.99
            and first["why"].startswith("exact words: ")
            and "similar in meaning" in first["why"],
            first["why"],
        )
        by_words = by_item(await search(client, w, mode="words", limit=50))
        by_meaning = by_item(
            await search(client, "Clients get a bill by mail", mode="meaning", limit=50)
        )
        keys = [ref_of("fact", found_fact["id"]), ref_of("decision", found_decision["id"])]
        proof(
            "M5",
            "a fact and a decision are found by words and by meaning, each labelled with its "
            "confirmation and lifecycle",
            all(k in by_words and by_words[k]["matched"] == "words" for k in keys)
            and all(k in by_meaning and by_meaning[k]["matched"] == "meaning" for k in keys)
            and all(by_meaning[k]["confirmation"] == "unconfirmed" for k in keys)
            and all("not confirmed by a person" in by_meaning[k]["label"] for k in keys),
        )

        # -- the service goes away and comes back
        w3 = unique_word()
        epic3 = await ok(client, "create_node", actor="a", kind="epic", title="An epic")
        phrase = f"Customers get an invoice by email {w3}"
        paraphrase3 = "Clients get a bill by mail"
        with embeddings_stopped():
            added = await ok(
                client, "create_node", actor="a", kind="story", title=phrase, parent=epic3["ref"]
            )
            page = await search(client, w3)
            hit = by_item(page)[added["ref"]]
            gap = added["ref"] in by_item(await search(client, paraphrase3))
            message = await refused(client, "search", query=paraphrase3, mode="meaning")
        proof(
            "M5",
            "with the embedding service stopped, a phrase added to a story is found by words at "
            "once and labelled an exact-words match; its paraphrase is not found, and asking for "
            "meaning says plainly why not",
            hit["matched"] == "words"
            and page["index"]["meaning"] == "service_unreachable"
            and not gap
            and "Searching by meaning is not possible now" in message,
            f'"{clip(message, 80)}"',
        )
        await reindexed(client)
        back = by_item(await search(client, paraphrase3))[added["ref"]]
        proof(
            "M5",
            "once the service returns and reindex has run, the paraphrase is found by meaning",
            back["matched"] == "meaning",
            back["why"],
        )

        # -- the overlap finder, on a fixture with known answers
        epic_a = await ok(client, "create_node", actor="a", kind="epic", title="Overlap fixture A")
        epic_b = await ok(client, "create_node", actor="a", kind="epic", title="Overlap fixture B")
        firsts, seconds = [], []
        for one, other in DUPLICATES:
            firsts.append(
                await ok(
                    client,
                    "create_node",
                    actor="a",
                    kind="story",
                    title=one,
                    parent=epic_a["ref"],
                )
            )
            seconds.append(
                await ok(
                    client,
                    "create_node",
                    actor="a",
                    kind="story",
                    title=other,
                    parent=epic_b["ref"],
                )
            )
        await reindexed(client)
        pairs = {
            (a["ref"], b["ref"]): i == j
            for i, a in enumerate(firsts)
            for j, b in enumerate(seconds)
        }
        duplicates = {pair for pair, same in pairs.items() if same}
        unrelated = {pair for pair, same in pairs.items() if not same}
        scores = {
            (a["ref"], b["ref"]): cosine(embed(a["title"]), embed(b["title"]))
            for a in firsts
            for b in seconds
        }
        overlaps = await every_overlap(client, epics=[epic_a["ref"], epic_b["ref"]])
        flagged = {(p["first"]["ref"], p["second"]["ref"]) for p in overlaps["items"]}
        proof(
            "M5",
            "on a fixture of known duplicate and known unrelated pairs, the overlap finder flags "
            "the duplicates and not the unrelated pairs",
            duplicates <= flagged and not unrelated & flagged,
            f"threshold {overlaps['threshold']}: flagged {len(duplicates & flagged)} of "
            f"{len(duplicates)} duplicate pairs and {len(unrelated & flagged)} of {len(unrelated)} "
            f"unrelated pairs; duplicates scored {min(scores[p] for p in duplicates):.2f} to "
            f"{max(scores[p] for p in duplicates):.2f}, unrelated at most "
            f"{max(scores[p] for p in unrelated):.2f}",
        )
        proof(
            "M16",
            "the optional embedding service stands on its own: with no language model anywhere, "
            "meaning search and overlap detection work against it, and exact words kept working "
            "while it was stopped",
            back["matched"] == "meaning" and duplicates <= flagged and hit["matched"] == "words",
            f"{len(flagged)} overlapping pairs found by meaning",
        )

        # -- the index is derived, and only reindex writes to it
        first_story = firsts[0]

        async def snapshot_results() -> dict[str, Any]:
            meaning = await search(client, "Send the bill to a client by mail", mode="meaning")
            similar = await ok(client, "similar_items", node=first_story["ref"], min_similarity=0.3)
            over = await ok(client, "find_overlaps", epics=[epic_a["ref"]])
            return {
                "meaning": sorted(
                    (i["item_type"], i["ref"], i["similarity"]) for i in meaning["items"]
                ),
                "similar": sorted(
                    (i["item_type"], i["ref"], i["similarity"]) for i in similar["items"]
                ),
                "overlaps": sorted(
                    (p["first"]["ref"], p["second"]["ref"], p["similarity"]) for p in over["items"]
                ),
            }

        before = await snapshot_results()
        with embeddings_stopped():
            dropped = run_sql("DROP TABLE semantic_index, semantic_index_info")
            without = await search(client, word)
        report = await reindexed(client)
        proof(
            "M5",
            "dropping the index tables and running reindex gives the same results",
            dropped.returncode == 0
            and without["index"]["meaning"] == "index_not_ready"
            and report["indexed_total"] > 0
            and await snapshot_results() == before,
            f"{report['indexed_total']} items indexed again; words worked while it was gone",
        )
        snapshot_sql = (
            "SELECT md5(string_agg(item_type || ':' || item_id || ':' || content_hash || ':' || "
            "indexed_at::text || ':' || embedding::text, ',' ORDER BY item_type, item_id)) "
            "FROM semantic_index"
        )
        before_index = sql_value(snapshot_sql, as_project=True)
        await search(client, unique_word())
        await search(client, "invoice by email", mode="meaning")
        await ok(client, "similar_items", node=first_story["ref"])
        await ok(client, "similar_items", text="anything at all")
        await ok(client, "find_overlaps", epics=[epic_a["ref"]])
        await ok(client, "recall", node=first_story["ref"], with_similar=True)
        await ok(client, "list_nodes", limit=5)
        await ok(client, "get_node", node=first_story["ref"])
        proof(
            "M5",
            "no tool other than reindex writes to the index: after every reading tool it is "
            "exactly as it was",
            before_index and sql_value(snapshot_sql, as_project=True) == before_index,
            f"checksum {before_index[:12]} unchanged",
        )

        # -- M5B: a repeated title is flagged, and still created
        repeated = f"Handle {unique_word()} requests"
        (epic5, existing) = await stories(client, 1)
        existing = await ok(
            client,
            "create_node",
            actor="marie",
            kind="story",
            title=repeated,
            parent=epic5["ref"],
        )
        await reindexed(client)
        again = await ok(
            client, "create_node", actor="ann", kind="story", title=repeated, parent=epic5["ref"]
        )
        (match,) = again["similar"]
        proof(
            "M5B",
            "creating a story whose title repeats an existing one returns the existing one as "
            "similar, and still creates it",
            again["ref"] != existing["ref"]
            and again["version"] == 1
            and match["ref"] == existing["ref"]
            and match["item_type"] == "story"
            and match["suggestion"] is True
            and match["similarity"] >= 0.55,
            f"similarity {match['similarity']:.2f}",
        )
        with embeddings_stopped():
            third = await ok(
                client,
                "create_node",
                actor="ann",
                kind="story",
                title=repeated,
                parent=epic5["ref"],
            )
        proof(
            "M5B",
            "with the embedding service stopped it says it did not check, and still creates",
            third["similar"] == []
            and third["similar_checked"] is False
            and "cannot be reached" in third["similar_note"],
            f'"{clip(third["similar_note"], 80)}"',
        )
