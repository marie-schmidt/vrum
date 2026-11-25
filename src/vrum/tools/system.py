"""MCP tools for search, overlaps, mentions, indexing, export/import, and code mode."""

import logging
from typing import Annotated, Any

from pydantic import Field

from vrum.code_mode import (
    DEFAULT_TIMEOUT,
    MOST_TIMEOUT,
    CodeMode,
    Explored,
    RunResult,
    failure_words,
)
from vrum.code_mode import explore as describe_functions
from vrum.common import Ref, Refusal, parse_node_ref, parse_node_refs
from vrum.db import current_project
from vrum.embeddings import EmbeddingUnavailable
from vrum.exchange import Exchange, ExportPage, ImportResult
from vrum.guard import GuardedServer, current_caller
from vrum.indexing import Indexer, IndexReport
from vrum.logs import event
from vrum.references import MentionPage, References
from vrum.search import OverlapPage, Searcher, SearchPage
from vrum.tools import _READS, _Limit, _me

log = logging.getLogger("vrum.server")


def register_system_tools(
    mcp: GuardedServer,
    *,
    searcher: Searcher,
    references: References,
    indexer: Indexer,
    exchange: Exchange,
    code_mode: CodeMode,
) -> None:
    @mcp.tool(annotations=_READS)
    async def search(
        query: Annotated[
            str,
            Field(
                description="What to look for. Words are matched as whole words, so every word "
                "must be in the text (plurals and endings are matched). Put a phrase in double "
                "quotes, write or between alternatives, and put - before a word to leave it out. "
                "Any characters are allowed."
            ),
        ],
        mode: Annotated[
            str,
            Field(
                description="both (the default: exact words and meaning, fused by rank), words "
                "(exact words only) or meaning (similar meaning only)."
            ),
        ] = "both",
        types: Annotated[
            list[str] | None,
            Field(
                description="Only these types: epic, story, task, rule (acceptance criteria), note."
            ),
        ] = None,
        statuses: Annotated[
            list[str] | None,
            Field(
                description="Only these statuses, such as ready, done, open, met, confirmed or "
                "withdrawn. An acceptance criterion is open, met or dropped, and a note "
                "unconfirmed, confirmed, superseded or withdrawn."
            ),
        ] = None,
        min_similarity: Annotated[
            float | None,
            Field(
                description="How alike in meaning, from 0 to 1, an item must be to match by "
                "meaning. Leave empty for the server's setting."
            ),
        ] = None,
        limit: Annotated[int, Field(description="How many results to return, from 1 to 200.")] = 20,
        offset: Annotated[
            int, Field(description="How many results to skip. Pass next_offset for the next page.")
        ] = 0,
    ) -> SearchPage:
        """Search epics, stories, tasks, acceptance criteria and notes.

        Each result says how it matched (words, meaning or both), why, and shows a snippet with
        the matched words between « and ». Matches by meaning alone are suggestions. A note's
        status says whether it is unconfirmed or confirmed (in force) or superseded or withdrawn
        (not). The answer counts the matches by type and by status. Exact words are found the
        moment something is saved, and keep working when the
        embedding service is down; the answer says when meaning could not be used.
        """
        return await searcher.search(
            query=query,
            mode=mode,
            types=types,
            statuses=statuses,
            min_similarity=min_similarity,
            limit=limit,
            offset=offset,
        )

    @mcp.tool(annotations=_READS)
    async def similar_items(
        node: Annotated[
            Ref | None,
            Field(description="An epic, story or task (its reference, such as story:12)."),
        ] = None,
        rule_id: Annotated[
            int | None, Field(description="Or an acceptance criterion (rule).")
        ] = None,
        note_id: Annotated[int | None, Field(description="Or a note.")] = None,
        text: Annotated[str | None, Field(description="Or a piece of text.")] = None,
        types: Annotated[
            list[str] | None,
            Field(description="Only these types, such as note or story."),
        ] = None,
        min_similarity: Annotated[
            float | None,
            Field(description="How alike, from 0 to 1. Leave empty for the server's setting."),
        ] = None,
        limit: Annotated[int, Field(description="How many results to return, from 1 to 200.")] = 20,
        offset: Annotated[
            int, Field(description="How many results to skip. Pass next_offset for the next page.")
        ] = 0,
    ) -> SearchPage:
        """Find items that are alike in meaning to one item or one piece of text, nearest first.

        Give exactly one of node, rule_id, note_id or text. The item itself is
        left out. Every result is a suggestion. It needs the embedding service and the index; if
        either is not ready it says so and changes nothing.
        """
        return await searcher.similar_items(
            node=None if node is None else parse_node_ref(node, "node"),
            rule_id=rule_id,
            note_id=note_id,
            text=text,
            types=types,
            min_similarity=min_similarity,
            limit=limit,
            offset=offset,
        )

    @mcp.tool(annotations=_READS)
    async def find_overlaps(
        item_type: Annotated[
            str,
            Field(
                description="story (the default), epic, task or rule (acceptance criteria): what "
                "to compare."
            ),
        ] = "story",
        epics: Annotated[
            list[Ref] | None,
            Field(
                description="Only pairs where at least one of the two is in one of these epics "
                "(their references, such as epic:3). Leave empty to look at everything."
            ),
        ] = None,
        min_similarity: Annotated[
            float | None,
            Field(
                description="How alike, from 0 to 1, a pair must be to be reported. Leave empty "
                "for the server's setting."
            ),
        ] = None,
        include_dropped: Annotated[
            bool,
            Field(
                description="True: also compare dropped stories, epics, tasks and acceptance "
                "criteria."
            ),
        ] = False,
        limit: Annotated[int, Field(description="How many pairs to return, from 1 to 200.")] = 20,
        offset: Annotated[
            int, Field(description="How many pairs to skip. Pass next_offset for the next page.")
        ] = 0,
    ) -> OverlapPage:
        """Find pairs of stories, epics, tasks or acceptance criteria in different epics that look
        like the same thing.

        Pairs are listed most alike first, with their scores, and say which links already exist
        between them. This only reports: it never merges, links or changes anything. A person or
        an agent decides, for example by linking two stories as duplicates. It uses the index, so
        items that are new or changed and not yet indexed are not compared yet; the answer counts
        them.
        """
        return await searcher.find_overlaps(
            item_type=item_type,
            epics=parse_node_refs(epics, "epics"),
            min_similarity=min_similarity,
            include_dropped=include_dropped,
            limit=limit,
            offset=offset,
        )

    @mcp.tool(annotations=_READS)
    async def find_mentions(
        node: Annotated[
            Ref | None,
            Field(
                description="An epic, story or task (its reference, such as story:12): who "
                "refers to it."
            ),
        ] = None,
        rule_id: Annotated[
            int | None, Field(description="Or an acceptance criterion (rule).")
        ] = None,
        note_id: Annotated[int | None, Field(description="Or a note.")] = None,
        limit: _Limit = 50,
        offset: Annotated[
            int, Field(description="How many to skip. Pass next_offset for the next page.")
        ] = 0,
    ) -> MentionPage:
        """List the items whose text refers to one item, or, with no item given, every reference
        written anywhere in the graph.

        A reference is [[epic:3]], [[story:12]], [[task:40]], [[rule:3]] or [[note:7]] in the
        details of an epic, story, task or acceptance criterion, or the text or why of a note.
        Titles are not read. It is only
        text: it creates no link, changes no readiness and no order, and nothing is stored for it.
        This reads the text of everything when asked. Each mention says which item holds it, where
        and a snippet around it, and, for every reference, whether the item it names exists: one
        that does not is plainly missing. An item that refers to itself is not listed. For a
        note, an acceptance criterion or any item, use this to see what leans on it before
        changing it.
        """
        return await references.find_mentions(
            node=None if node is None else parse_node_ref(node, "node"),
            rule_id=rule_id,
            note_id=note_id,
            limit=limit,
            offset=offset,
        )

    @mcp.tool()
    async def reindex() -> IndexReport:
        """Bring the semantic index up to date now: build it if it does not exist, then embed
        every epic, story, acceptance criterion and note that is new or changed.

        The server also does this by itself within seconds, so you rarely need it. This is the only
        thing that writes to the index, which holds nothing that is not in the graph and can be
        dropped and rebuilt at any time. If the embedding service cannot be reached, nothing is
        changed and exact-words search still works.
        """
        try:
            return await indexer.reindex(current_project())
        except EmbeddingUnavailable as error:
            raise Refusal(
                f"{error} Nothing was indexed. Exact-words search still works; try again "
                "when the service is back."
            ) from error

    @mcp.tool(annotations=_READS)
    async def export_graph(
        after: Annotated[
            str | None,
            Field(description="Leave empty for the first page; pass next_after for the next."),
        ] = None,
        limit: _Limit = 200,
    ) -> ExportPage:
        """Read the whole graph as data, one page at a time: every epic, story and task (with
        dates and size), link, rule (acceptance criterion), note (with its why, who decided it, its
        status and who confirmed it, and what it concerns), the definition of done, the ticks of
        its checks, and the event log.

        Each page holds items of one list (nodes, links, rules, notes, definition_of_done, checks
        or events), in a fixed order (nodes as epics, then stories, then tasks, each by number;
        the checks by node and item; the rest by id; the definition of done is at most one item).
        Keep calling with next_after until it is empty; the first page also says how
        many items each list holds in all, to check you got them all. Dates and times are text.
        Not included: claims (leases that run out) and what is worked out from the rest (what is
        ready, the search and semantic indexes).

        The file this makes can be loaded into an empty server with import_graph, and turned into
        a Markdown copy to read in Obsidian or keep in git. scripts/export_graph.py and
        scripts/export_markdown.py do the paging for you.
        """
        return await exchange.export_page(after=after, limit=limit)

    @mcp.tool()
    async def import_graph(
        document: Annotated[
            dict[str, Any],
            Field(
                description="The whole export, as one object with format, version, counts and the "
                "lists nodes, links, rules, notes, definition_of_done, checks and events, as "
                "export_graph pages give them."
            ),
        ],
    ) -> ImportResult:
        """Load a whole export into this server, which must be empty. Every number, id, date,
        status, confirmation and event is kept, so what is exported afterwards is identical to the
        file, and the next new epic, story or task is numbered after the highest of its own kind.
        A file made by an older version is loaded too: one of version 5 has no sizes and no checks;
        one of version 4 has the story of each
        acceptance criterion read as the node it is on, and no definition of done; one of version
        3 has its active notes become confirmed or unconfirmed (as a person had confirmed them or
        not) and its held and settled acceptance criteria become open and met; one of version 1 or
        2 has its decisions and then its facts become notes, numbered from 1 in that order, and
        their links, confirmations and history go with them. A file made before names were
        verified (version 1) says as what its notes and events were made as unknown.

        It is all or nothing: the file is checked first and every problem in it is listed at once,
        and the database checks everything again as it is loaded. A server that already holds
        anything is refused, because the numbers in the file would clash with what is there: load
        it into a fresh one, or restore a backup. A file can be as big as one request may be (see
        VRUM_MAX_REQUEST_BYTES). scripts/import_graph.py sends a file for you. If an embedding
        service is set up, the server indexes the imported items by itself afterwards.
        """
        result = await exchange.import_document(document)
        event(log, logging.INFO, "import_finished", actor=_me()[:100], counts=result.counts)
        return result

    @mcp.tool(annotations=_READS)
    async def explore(
        names: Annotated[
            list[str] | None,
            Field(
                description="The functions to describe, such as the ones you are about to use. "
                "Leave empty to list every function."
            ),
        ] = None,
        query: Annotated[
            str | None,
            Field(
                description="Words to look for in the functions' names and descriptions, such as "
                "note or claim. It gives the ten that fit best."
            ),
        ] = None,
    ) -> Explored:
        """See what `vrum` can do inside `run`, for a key that calls Vrum by running code.

        With nothing asked it lists the functions this key's scripts have, with a line on each and
        whether it only reads. With names, or with words to look for, it also gives the TypeScript
        of those functions in full: their descriptions and the exact types of their arguments and
        results, which is what a script is checked against before it runs. It changes nothing.
        """
        caller = current_caller()
        return describe_functions(mcp.sdk, mcp.functions_for(caller.tools), names, query)

    @mcp.tool()
    async def run(
        code: Annotated[
            str,
            Field(
                description="The body of an async function, written in TypeScript: `vrum` and "
                "`console` are in scope and `await` works. Return a JSON value to get it back."
            ),
        ],
        timeout_seconds: Annotated[
            int,
            Field(
                description=f"How long the script may run, in seconds, from 1 to {MOST_TIMEOUT}."
            ),
        ] = DEFAULT_TIMEOUT,
    ) -> RunResult:
        """Run a TypeScript script that calls Vrum's functions, for a key that calls Vrum by
        running code.

        Each function of `vrum` is the tool of that name: the same arguments, results and checks,
        recorded under this key's name, so one script can do what many calls would. The script is
        type checked first, then run in a sandbox with no network, files or imports. A call Vrum
        refuses throws a VrumError with the tool's own sentence. Every call is its own step and
        nothing is rolled back, so a script that stops half way says which calls ran and whether
        anything changed. It gives back what the script returned, what it wrote with console.log
        and the calls it made. This key's own functions are listed where this tool is described to
        it, and `explore` gives their types.
        """
        outcome = await code_mode.run(current_caller(), code, timeout_seconds)
        if not outcome.ok:
            raise Refusal(failure_words(outcome))
        return RunResult(result=outcome.result, logs=outcome.logs, calls=outcome.calls)
