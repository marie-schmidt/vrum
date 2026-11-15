"""Extraction and reflection (M5C): the parts of the memory that need a language model.

Nothing a model writes can be mistaken for a record here. Every proposal is checked against what
it claims to come from before it is kept or shown: a fact's passage must be in the text, a belief's
evidence must be among the facts it was given, a contradiction must quote both sides, an answer
must cite only what was retrieved. What is kept says that a model wrote it. Text with a secret in
a well-known format is never sent. Every call to the model is in the event log."""

import re
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import (
    Refusal,
    check_choice,
    clean_actor,
    fetch_all,
    fetch_one,
    page_size,
    record_event,
    require_link_targets,
    unique_ids,
)
from vrum.db import Database
from vrum.decisions import Decision, Decisions, require_decision
from vrum.graph import require_node
from vrum.llm import LanguageModel, ModelFailure
from vrum.memories import (
    FACT_KINDS,
    MEMORY_COLUMNS,
    Memories,
    Memory,
    require_memory,
    to_memory,
)
from vrum.recall import Recaller
from vrum.rules import require_rule
from vrum.search import Searcher
from vrum.secret_formats import find_secret

# The most characters of one item that are shown to the model.
_ITEM_CHARS = 4000
_SCOPES = ("related", "all")
_CITED = re.compile(r"\b(decision|principle|fact|belief):(\d+)\b")

_JSON_ONLY = "Reply with only a JSON object, and nothing else."

_EXTRACT_SYSTEM = f"""You find facts in a text for a project's memory. A fact is one plain \
statement that the text itself says is true, or says happened. For each fact give:
- "text": the fact, short and complete on its own.
- "kind": "world" if it is true about the project, its tools or its domain, or "experience" if it \
is something that was done or observed.
- "passage": the exact words of the text that the fact comes from, copied character for character.
Do not guess, infer or add anything the text does not say. If the text has no facts, return an \
empty list. {_JSON_ONLY}
Format: {{"facts": [{{"text": "...", "kind": "world", "passage": "..."}}]}}"""

_DRAW_UP_SYSTEM = f"""You draw up conclusions (beliefs) from facts, for a project's memory. You \
are given numbered facts. A belief is a conclusion that the facts support: it says something \
more than any one fact does, or joins several. Each belief lists the numbers of the facts it \
rests on in "evidence_fact_ids", and may use only the numbers given. Do not invent facts. Do not \
repeat a single fact as a belief. If the facts support no conclusion, return an empty list. \
{_JSON_ONLY}
Format: {{"beliefs": [{{"text": "...", "evidence_fact_ids": [1, 2]}}]}}"""

_REVISE_SYSTEM = f"""A belief in a project's memory has gone stale: some of the facts or \
decisions it rested on have changed. Draw up one revised belief, using only the evidence listed \
as still valid (facts in "evidence_fact_ids", decisions in "evidence_decision_ids", by their \
numbers). If that evidence supports no revised belief, return an empty list. {_JSON_ONLY}
Format: {{"beliefs": [{{"text": "...", "evidence_fact_ids": [1], \
"evidence_decision_ids": [2]}}]}}"""

_CONTRADICTIONS_SYSTEM = f"""You check a new statement against numbered beliefs from a project's \
memory, and say which of them it contradicts. Two things contradict only if they cannot both be \
true. A belief that is merely related, more specific or different does not contradict the \
statement. For each contradiction give:
- "belief_id": the number of the belief.
- "reason": one sentence saying why they cannot both be true.
- "belief_quote": the exact words of the belief that clash, copied character for character.
- "statement_quote": the exact words of the statement that clash, copied character for character.
If nothing is contradicted, return an empty list. {_JSON_ONLY}
Format: {{"contradictions": [{{"belief_id": 1, "reason": "...", "belief_quote": "...", \
"statement_quote": "..."}}]}}"""

_REFLECT_SYSTEM = f"""You answer a question about a project, using only the numbered items from \
its memory that are listed. A decision is made by a named person and is authoritative. A \
principle is a standing guideline. A fact is something known, with its source. A belief is only \
a suggestion. Do not use anything else you know. If the items do not answer the question, set \
"answer" to null. Otherwise answer briefly, and list in "cited" the keys of the items you used, \
exactly as written, for example "fact:7". {_JSON_ONLY}
Format: {{"answer": "...", "cited": ["decision:3", "fact:7"]}}"""


class Dropped(BaseModel):
    what: str = Field(description="What the model proposed, in a few words.")
    why: str = Field(description="Why the server did not keep it or show it.")


class ModelResult(BaseModel):
    model: str = Field(description="The language model that was asked.")
    label: str = Field(description="What this is, so it is never mistaken for a record.")
    message: str = Field(description="In plain words, what happened.")


class ProposedFact(BaseModel):
    text: str
    kind: str = Field(description="world or experience.")
    passage: str = Field(description="The exact words of the text it comes from.")


class ExtractResult(ModelResult):
    proposals: list[ProposedFact] = Field(
        description="The facts the model found whose passages are in the text."
    )
    dropped: list[Dropped] = Field(
        description="What the model proposed that was not kept, and why."
    )
    stored: bool = Field(description="True when you asked for the facts to be stored.")
    memories: list[Memory] = Field(
        description="The facts that were stored, with origin extracted. Empty unless stored."
    )


class ConsolidateResult(ModelResult):
    facts_found: int = Field(description="How many active facts there were to choose from.")
    facts_sent: int = Field(description="How many of them were shown to the model.")
    left_out: int = Field(
        description="How many were not shown (too many, too long, or holding a secret)."
    )
    recorded: list[Memory] = Field(
        description="The beliefs that were recorded, with origin derived. Suggestions only."
    )
    replaced_id: int | None = Field(
        description="When revising: the stale belief that the new one replaced."
    )
    dropped: list[Dropped] = Field(
        description="What the model proposed that was not recorded, and why."
    )


class ContradictionProposal(BaseModel):
    belief_id: int
    belief_text: str
    confirmed_by: str | None = Field(
        description="The person who confirmed the belief, if one did. Changing it then needs "
        "their word: said_by and quote."
    )
    stale: bool
    reason: str = Field(description="Why the model says they cannot both be true.")
    belief_quote: str = Field(description="The words of the belief that clash. Checked.")
    statement_quote: str = Field(description="The words of the statement that clash. Checked.")


class ContradictionResult(ModelResult):
    statement: str
    scope: str = Field(description="related or all.")
    proposals: list[ContradictionProposal] = Field(
        description="Beliefs the model says the statement contradicts. Proposals only: nothing "
        "was changed."
    )
    dropped: list[Dropped] = Field(description="What the model said that was not kept, and why.")
    shown: int = Field(description="How many beliefs were shown to the model in this call.")
    looked_at: int = Field(
        description="Scope all: how many active beliefs have been passed so far, counting from "
        "the first. Scope related: how many were shown."
    )
    active_beliefs: int = Field(description="How many active beliefs there are in all.")
    remaining: int = Field(
        description="Scope all: how many active beliefs are left after this batch. Scope "
        "related: how many related ones were not shown."
    )
    related_found: int | None = Field(
        description="Scope related: how many beliefs were found that may clash. Empty for all."
    )
    next_after_id: int | None = Field(
        description="Scope all: pass this as after_id for the next batch. Empty when none remain."
    )
    skipped_secret: int = Field(
        description="How many beliefs were not shown because they contain a secret."
    )


class ReliedOn(BaseModel):
    item_type: str = Field(description="decision, principle, fact or belief.")
    id: int
    title: str
    label: str
    status: str
    suggestion: bool = Field(
        description="True for a belief, and for anything a language model wrote."
    )


class ReflectResult(ModelResult):
    question: str
    answered: bool = Field(
        description="False when the memory does not answer the question. Then there is no answer."
    )
    answer: str | None = Field(
        description="The model's answer, generated and not stored. Empty when not answered."
    )
    relied_on: list[ReliedOn] = Field(description="The items the answer cites. All were retrieved.")
    retrieved: int = Field(description="How many items were shown to the model.")
    left_out: int = Field(description="How many more were found but not shown (too many or long).")
    skipped_secret: int = Field(
        description="How many items were not shown because they contain a secret."
    )
    search_note: str | None = Field(
        description="When meaning could not be used to find items, why."
    )


# -- small helpers ------------------------------------------------------------------------------


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _cut(text: str, length: int) -> str:
    text = _squash(text)
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def _quote_in(quote: object, text: str) -> bool:
    return isinstance(quote, str) and bool(_squash(quote)) and _squash(quote) in _squash(text)


def _whole_number(value: object) -> int | None:
    """A model's id, whether it wrote 12 or "12"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value.strip())
    return None


def _words_query(text: str) -> str:
    """The words of a question joined with `or`, so that one missing word does not hide an item
    that has the others."""
    words = dict.fromkeys(re.findall(r"[A-Za-z0-9]{3,}", text.lower()))
    return " or ".join(words) if words else text


def _how_many_fit(lines: list[str], budget: int) -> int:
    used = 0
    for count, line in enumerate(lines):
        used += len(line) + 1
        if used > budget and count > 0:
            return count
    return len(lines)


def _decision_line(decision: Decision) -> tuple[str, str]:
    kind = "principle" if decision.kind == "principle" else "decision"
    key = f"{kind}:{decision.id}"
    line = (
        f"[{key}] {kind.capitalize()}, decided by {decision.decided_by} on {decision.decided_on}: "
        f"{decision.title}. {decision.text} Reason: {decision.reason}"
    )
    return key, _cut(line, _ITEM_CHARS)


def _memory_line(memory: Memory) -> tuple[str, str]:
    key = f"{memory.type}:{memory.id}"
    if memory.type == "belief":
        line = f"[{key}] Belief (a suggestion, not settled): {memory.text}"
    else:
        by = "proposed by a language model" if memory.origin == "extracted" else "stated"
        line = f"[{key}] Fact ({by}), from {memory.source}: {memory.text}"
    return key, _cut(line, _ITEM_CHARS)


def _fact_line(memory: Memory) -> str:
    return f"[fact:{memory.id}] {_cut(memory.text, _ITEM_CHARS)} (source: {memory.source})"


def _has_secret(*texts: str | None) -> bool:
    return any(find_secret(text) for text in texts if text)


class Reflector:
    def __init__(
        self,
        database: Database,
        model: LanguageModel | None,
        memories: Memories,
        decisions: Decisions,
        searcher: Searcher,
        recaller: Recaller,
    ) -> None:
        self._database = database
        self._model = model
        self._memories = memories
        self._decisions = decisions
        self._searcher = searcher
        self._recaller = recaller

    # -- shared steps -------------------------------------------------------------------------

    def _require_model(self, tool: str, instead: str) -> LanguageModel:
        if self._model is None:
            raise Refusal(
                f"No language model is set up on this server, so {tool} cannot run. Set "
                "LLM_BASE_URL and LLM_MODEL (see .env.example) and restart the server to use it. "
                f"Everything else works. {instead}"
            )
        return self._model

    @staticmethod
    def _refuse_secret(what: str, text: str) -> None:
        found = find_secret(text)
        if found:
            raise Refusal(
                f"{what} contains what looks like {found}, so it was not sent to the language "
                "model and nothing was changed. Take the secret out and try again."
            )

    @staticmethod
    async def _ask(model: LanguageModel, system: str, user: str) -> dict[str, Any]:
        try:
            return await model.ask(system, user)
        except ModelFailure as failure:
            raise Refusal(f"{failure} Nothing was changed.") from failure

    @staticmethod
    async def _log(
        conn: AsyncConnection,
        *,
        actor: str,
        model: LanguageModel,
        tool: str,
        asked: str,
        used: list[tuple[str, int]],
        result: dict[str, Any],
    ) -> None:
        await record_event(
            conn,
            action="model_call",
            actor=actor,
            before=None,
            after={
                "tool": tool,
                "model": model.model,
                "asked": _cut(asked, 1000),
                "used": [{"type": item_type, "id": item_id} for item_type, item_id in used],
                "result": result,
            },
        )

    async def _log_alone(self, **details: Any) -> None:
        async with self._database.transaction() as conn:
            await self._log(conn, **details)

    async def _find(
        self, query: str, types: list[str], limit: int
    ) -> tuple[list[tuple[str, int]], str | None]:
        """Active items of these types that match a question by exact words, by meaning, or by
        the words of it joined with `or`. Returns them best first, and why meaning was not used
        when it was not."""
        first = await self._searcher.search(
            query=query,
            mode="both",
            types=types,
            statuses=["active"],
            min_similarity=None,
            limit=limit,
            offset=0,
        )
        found = [(item.item_type, item.id) for item in first.items]
        words = _words_query(query)
        if words != query:
            second = await self._searcher.search(
                query=words,
                mode="words",
                types=types,
                statuses=["active"],
                min_similarity=None,
                limit=limit,
                offset=0,
            )
            found.extend((item.item_type, item.id) for item in second.items)
        note = first.index.note if first.index.meaning != "used" else None
        return list(dict.fromkeys(found))[:limit], note

    # -- extract_facts ------------------------------------------------------------------------

    async def extract_facts(
        self,
        *,
        actor: str,
        text: str,
        store: bool,
        node_ids: list[int] | None,
        rule_ids: list[int] | None,
        decision_ids: list[int] | None,
    ) -> ExtractResult:
        actor = clean_actor(actor)
        model = self._require_model(
            "extract_facts", "You can record facts yourself with retain_fact."
        )
        text = text.replace("\x00", "")
        if not text.strip():
            raise Refusal("There is no text to read: text cannot be empty.")
        if len(text) > model.max_input_chars:
            raise Refusal(
                f"The text is {len(text):,} characters, and the most this server sends to the "
                f"language model at once is {model.max_input_chars:,} (LLM_MAX_INPUT_CHARS). "
                "Send it in parts."
            )
        self._refuse_secret("The text", text)
        nodes, rules, decisions = (
            unique_ids(node_ids),
            unique_ids(rule_ids),
            unique_ids(decision_ids),
        )
        if (nodes or rules or decisions) and not store:
            raise Refusal(
                "node_ids, rule_ids and decision_ids say what the stored facts concern, so they "
                "only work with store set to true."
            )
        if store:
            # A wrong id is found before the model is asked, so it costs no model call.
            async with self._database.transaction() as conn:
                await require_link_targets(conn, nodes, rules, "the facts")
                for decision_id in decisions:
                    await require_decision(conn, decision_id)

        answer = await self._ask(model, _EXTRACT_SYSTEM, f"Text:\n<<<\n{text}\n>>>")
        proposals, dropped = _check_facts(text, answer)

        asked = f"Facts from a text of {len(text):,} characters, beginning: {_cut(text, 300)}"
        stored: list[Memory] = []
        async with self._database.transaction() as conn:
            if store:
                for proposal in proposals:
                    stored.append(
                        await self._memories.retain_fact(
                            actor=actor,
                            text=proposal.text,
                            source=proposal.passage,
                            kind=proposal.kind,
                            happened_on=None,
                            node_ids=nodes,
                            rule_ids=rules,
                            decision_ids=decisions,
                            origin="extracted",
                            conn=conn,
                        )
                    )
            await self._log(
                conn,
                actor=actor,
                model=model,
                tool="extract_facts",
                asked=asked,
                used=[],
                result={
                    "proposed": len(proposals),
                    "dropped": len(dropped),
                    "stored_ids": [memory.id for memory in stored],
                },
            )
        found = f"The model found {len(proposals)} fact{'s' if len(proposals) != 1 else ''}"
        if dropped:
            found += (
                f" and {len(dropped)} proposal{'s were' if len(dropped) != 1 else ' was'} dropped"
            )
        return ExtractResult(
            model=model.model,
            label=f"Proposed by the language model {model.model} from the text you gave. "
            "Each fact quotes its passage, and the server checked that the passage is in the text.",
            message=f"{found}. "
            + (
                f"{len(stored)} stored as facts with origin extracted, the passage as their source."
                if store
                else "Nothing was stored: set store to true to keep them."
            ),
            proposals=proposals,
            dropped=dropped,
            stored=store,
            memories=stored,
        )

    # -- consolidate_memory -------------------------------------------------------------------

    async def consolidate_memory(
        self,
        *,
        actor: str,
        fact_ids: list[int] | None,
        node_id: int | None,
        query: str | None,
        revise_belief_id: int | None,
        max_facts: int,
        said_by: str | None,
        quote: str | None,
    ) -> ConsolidateResult:
        actor = clean_actor(actor)
        model = self._require_model(
            "consolidate_memory", "You can record a belief yourself with record_belief."
        )
        chosen = [
            name
            for name, value in (
                ("fact_ids", fact_ids),
                ("node_id", node_id),
                ("query", query),
                ("revise_belief_id", revise_belief_id),
            )
            if value is not None
        ]
        if len(chosen) != 1:
            raise Refusal(
                "Say which facts to draw conclusions from: give exactly one of fact_ids, node_id "
                "(the item and everything below it), query (a search) or revise_belief_id (a "
                f"stale belief to revise). You gave {len(chosen)}."
            )
        if (said_by or quote) and revise_belief_id is None:
            raise Refusal("said_by and quote are only for revising a belief: set revise_belief_id.")
        if not 1 <= max_facts <= 200:
            raise Refusal(f"max_facts must be a whole number from 1 to 200, but it is {max_facts}.")
        if revise_belief_id is not None:
            return await self._revise(actor, model, revise_belief_id, said_by, quote)
        return await self._draw_up(actor, model, fact_ids, node_id, query, max_facts)

    async def _draw_up(
        self,
        actor: str,
        model: LanguageModel,
        fact_ids: list[int] | None,
        node_id: int | None,
        query: str | None,
        max_facts: int,
    ) -> ConsolidateResult:
        note: str | None = None
        async with self._database.transaction() as conn:
            if fact_ids is not None:
                ids = unique_ids(fact_ids)
                if not ids:
                    raise Refusal("fact_ids is empty: give the ids of at least one active fact.")
            elif node_id is not None:
                await require_node(conn, node_id)
                ids = [
                    row["id"]
                    for row in await fetch_all(
                        conn,
                        "WITH RECURSIVE area(id) AS (SELECT id FROM nodes WHERE id = %(n)s "
                        "UNION ALL SELECT n.id FROM nodes n JOIN area a ON n.parent_id = a.id) "
                        "SELECT m.id FROM memory_state m WHERE m.type = 'fact' "
                        "AND m.status = 'active' AND ("
                        "EXISTS (SELECT 1 FROM memory_nodes l WHERE l.memory_id = m.id "
                        "AND l.node_id IN (SELECT id FROM area)) "
                        "OR EXISTS (SELECT 1 FROM memory_rules l JOIN rules r ON r.id = l.rule_id "
                        "WHERE l.memory_id = m.id AND r.story_id IN (SELECT id FROM area))) "
                        "ORDER BY m.id",
                        {"n": node_id},
                    )
                ]
            else:
                assert query is not None
                found, note = await self._find(query, ["fact"], 200)
                ids = [item_id for _, item_id in found]
            rows = {
                row["id"]: row
                for row in await fetch_all(
                    conn,
                    f"SELECT {MEMORY_COLUMNS} FROM memory_state WHERE id = ANY(%s::bigint[])",
                    (ids,),
                )
            }
            if fact_ids is not None:
                for fact_id in ids:
                    row = rows.get(fact_id)
                    if row is None:
                        raise Refusal(
                            f"There is no fact with id {fact_id}. Use list_memories with type "
                            "fact to find the id you want."
                        )
                    if row["type"] != "fact" or row["status"] != "active":
                        what = "a belief, not a fact" if row["type"] == "belief" else row["status"]
                        raise Refusal(
                            f"Memory {fact_id} is {what}, so conclusions cannot be drawn from it. "
                            "Only active facts can be used."
                        )
        facts = [to_memory(rows[fact_id]) for fact_id in ids if fact_id in rows]
        sendable = [fact for fact in facts if not _has_secret(fact.text, fact.source)]
        secret = len(facts) - len(sendable)
        sendable = sendable[:max_facts]
        lines = [_fact_line(fact) for fact in sendable]
        sent = _how_many_fit(lines, model.max_input_chars - len(_DRAW_UP_SYSTEM))
        sendable, lines = sendable[:sent], lines[:sent]
        left_out = len(facts) - len(sendable)
        label = (
            f"Drawn up by the language model {model.model} from the facts listed. They are "
            "suggestions, not decisions, and each rests on the evidence it names."
        )
        if not sendable:
            return ConsolidateResult(
                model=model.model,
                label=label,
                message="There were no active facts to draw conclusions from, so the language "
                "model was not asked." + (f" {note}" if note else ""),
                facts_found=len(facts),
                facts_sent=0,
                left_out=left_out,
                recorded=[],
                replaced_id=None,
                dropped=[],
            )

        answer = await self._ask(model, _DRAW_UP_SYSTEM, "Facts:\n" + "\n".join(lines))
        allowed = {fact.id for fact in sendable}
        valid, dropped = _check_beliefs(answer, allowed, set())
        recorded: list[Memory] = []
        details: dict[str, Any] = {
            "actor": actor,
            "model": model,
            "tool": "consolidate_memory",
            "asked": (
                f"Beliefs from {len(sendable)} facts: {', '.join(str(f.id) for f in sendable)}"
            ),
            "used": [("fact", fact.id) for fact in sendable],
        }
        try:
            async with self._database.transaction() as conn:
                for text, evidence_facts, _ in valid:
                    existing = await fetch_one(
                        conn,
                        "SELECT id FROM memory_state WHERE type = 'belief' AND status = 'active' "
                        "AND lower(regexp_replace(btrim(text), '\\s+', ' ', 'g')) = %s",
                        (_squash(text).lower(),),
                    )
                    if existing is not None:
                        dropped.append(
                            Dropped(
                                what=_cut(text, 80),
                                why=f"it is already recorded as belief {existing['id']}.",
                            )
                        )
                        continue
                    recorded.append(
                        await self._memories.record_belief(
                            actor=actor,
                            text=text,
                            evidence_fact_ids=evidence_facts,
                            evidence_decision_ids=None,
                            node_ids=None,
                            rule_ids=None,
                            origin="derived",
                            conn=conn,
                        )
                    )
                await self._log(
                    conn,
                    result={
                        "recorded_ids": [belief.id for belief in recorded],
                        "dropped": len(dropped),
                    },
                    **details,
                )
        except Refusal as refusal:
            # The facts changed while the model was thinking. Nothing was recorded, but the call
            # was made, so it is logged.
            await self._log_alone(result={"refused": str(refusal)}, **details)
            raise
        summary = (
            f"{len(recorded)} belief{'s' if len(recorded) != 1 else ''} recorded from "
            f"{len(sendable)} fact{'s' if len(sendable) != 1 else ''}, origin derived"
        )
        extra = f" {left_out} facts were not shown to the model." if left_out else ""
        extra += f" {secret} contain a secret and were never sent." if secret else ""
        return ConsolidateResult(
            model=model.model,
            label=label,
            message=f"{summary}.{extra}" + (f" {note}" if note else ""),
            facts_found=len(facts),
            facts_sent=len(sendable),
            left_out=left_out,
            recorded=recorded,
            replaced_id=None,
            dropped=dropped,
        )

    async def _revise(
        self,
        actor: str,
        model: LanguageModel,
        belief_id: int,
        said_by: str | None,
        quote: str | None,
    ) -> ConsolidateResult:
        async with self._database.transaction() as conn:
            row = await require_memory(conn, belief_id)
            if row["type"] != "belief":
                raise Refusal(
                    f"Memory {belief_id} is a fact, not a belief, so it cannot be revised."
                )
            if row["status"] != "active":
                raise Refusal(
                    f"Belief {belief_id} was {row['status']}"
                    + (
                        f" and replaced by belief {row['superseded_by_id']}"
                        if row["superseded_by_id"]
                        else ""
                    )
                    + ", so it is not the one to revise."
                )
            if not row["stale"]:
                raise Refusal(
                    f"Belief {belief_id} is not stale: all the evidence it rests on is still "
                    "active, so there is nothing to revise. To change it anyway, use "
                    "supersede_memory."
                )
            if row["confirmed_by"] and not (said_by and quote):
                raise Refusal(
                    f"Belief {belief_id} was confirmed by {row['confirmed_by']}, so replacing it "
                    "needs that person's word: give said_by (who changed their mind) and quote "
                    "(what they said, or where). Nothing was changed."
                )
            belief = to_memory(row)
            changed: list[str] = []
            usable_facts: dict[int, Memory] = {}
            usable_decisions: dict[int, Decision] = {}
            for item in belief.evidence:
                if item.type == "fact":
                    end = await _fact_in_force(conn, item.id)
                    if isinstance(end, Memory):
                        usable_facts[end.id] = end
                        if end.id != item.id:
                            changed.append(f"fact {item.id} was replaced by fact {end.id}")
                    else:
                        changed.append(f"fact {item.id} {end}")
                else:
                    decision = await _decision_in_force(conn, item.id)
                    if isinstance(decision, Decision):
                        usable_decisions[decision.id] = decision
                        if decision.id != item.id:
                            changed.append(
                                f"decision {item.id} was replaced by decision {decision.id}"
                            )
                    else:
                        changed.append(f"decision {item.id} {decision}")
        fact_lines = [_fact_line(fact) for fact in usable_facts.values()]
        decision_lines = [_decision_line(decision)[1] for decision in usable_decisions.values()]
        prompt = (
            f"The belief that went stale:\n{_cut(belief.text, _ITEM_CHARS)}\n\n"
            "What changed:\n"
            + "\n".join(f"- {line}" for line in changed)
            + "\n\nEvidence that is still valid:\n"
            + "\n".join([*fact_lines, *decision_lines] or ["(none)"])
        )
        self._refuse_secret(f"Belief {belief_id} or its evidence", prompt)
        label = (
            f"Revised by the language model {model.model} from the evidence that is still valid. "
            "It is a suggestion, not a decision."
        )
        details: dict[str, Any] = {
            "actor": actor,
            "model": model,
            "tool": "consolidate_memory",
            "asked": f"A revised belief to replace stale belief {belief_id}",
            "used": [("belief", belief_id)]
            + [("fact", fact_id) for fact_id in usable_facts]
            + [("decision", decision_id) for decision_id in usable_decisions],
        }
        if not usable_facts and not usable_decisions:
            return ConsolidateResult(
                model=model.model,
                label=label,
                message=f"None of the evidence of belief {belief_id} is still valid, so there is "
                "nothing to revise it from and the language model was not asked. Retract it with "
                "retract_memory if it no longer holds.",
                facts_found=0,
                facts_sent=0,
                left_out=0,
                recorded=[],
                replaced_id=None,
                dropped=[],
            )

        answer = await self._ask(model, _REVISE_SYSTEM, prompt)
        valid, dropped = _check_beliefs(answer, set(usable_facts), set(usable_decisions))
        if len(valid) > 1:
            dropped.extend(
                Dropped(what=_cut(text, 80), why="only one revised belief is taken.")
                for text, _, _ in valid[1:]
            )
        recorded: list[Memory] = []
        try:
            async with self._database.transaction() as conn:
                if valid:
                    text, evidence_facts, evidence_decisions = valid[0]
                    change = await self._memories.supersede_memory(
                        actor=actor,
                        replaces=[belief_id],
                        reason=f"Revised by the language model {model.model} because its evidence "
                        f"changed ({'; '.join(changed)}).",
                        text=text,
                        source=None,
                        kind=None,
                        happened_on=None,
                        evidence_fact_ids=evidence_facts,
                        evidence_decision_ids=evidence_decisions,
                        keep_links=True,
                        node_ids=None,
                        rule_ids=None,
                        decision_ids=None,
                        said_by=said_by,
                        quote=quote,
                        origin="derived",
                        conn=conn,
                    )
                    assert change.replacement is not None
                    recorded.append(change.replacement)
                await self._log(
                    conn,
                    result={
                        "replacement_id": recorded[0].id if recorded else None,
                        "dropped": len(dropped),
                    },
                    **details,
                )
        except Refusal as refusal:
            await self._log_alone(result={"refused": str(refusal)}, **details)
            raise
        return ConsolidateResult(
            model=model.model,
            label=label,
            message=(
                f"Belief {belief_id} was replaced by belief {recorded[0].id}, origin derived."
                if recorded
                else f"The model drew no revised belief from what is still valid, so belief "
                f"{belief_id} is unchanged and still stale."
            ),
            facts_found=len(usable_facts),
            facts_sent=len(usable_facts),
            left_out=0,
            recorded=recorded,
            replaced_id=belief_id if recorded else None,
            dropped=dropped,
        )

    # -- find_contradictions ------------------------------------------------------------------

    async def find_contradictions(
        self,
        *,
        actor: str,
        statement: str | None,
        memory_id: int | None,
        scope: str,
        batch: int,
        after_id: int | None,
    ) -> ContradictionResult:
        actor = clean_actor(actor)
        model = self._require_model(
            "find_contradictions",
            "You can walk the beliefs yourself with list_memories and judge them.",
        )
        check_choice("scope", scope, _SCOPES)
        if (statement is None) == (memory_id is None):
            raise Refusal(
                "Give exactly one of statement (the new statement, in words) or memory_id (a "
                "fact or belief that is already recorded)."
            )
        if not 1 <= batch <= 200:
            raise Refusal(f"batch must be a whole number from 1 to 200, but it is {batch}.")
        if after_id is not None and scope != "all":
            raise Refusal("after_id only works with scope all, which goes through every belief.")

        async with self._database.transaction() as conn:
            memory: dict[str, Any] | None = None
            if memory_id is not None:
                memory = await require_memory(conn, memory_id)
                if memory["status"] != "active":
                    raise Refusal(
                        f"{memory['type'].capitalize()} {memory_id} is {memory['status']}, so it "
                        "is not a new statement to check. Use one that is active."
                    )
                statement = memory["text"]
            assert statement is not None
            statement = statement.replace("\x00", "").strip()
            if not statement:
                raise Refusal("There is no statement to check: statement cannot be empty.")
        self._refuse_secret("The statement", statement)
        if len(statement) > model.max_input_chars // 2:
            raise Refusal(
                f"The statement is {len(statement):,} characters, which is too long to check "
                "against beliefs. Give the part that matters."
            )

        if scope == "related":
            candidates, related_found, note = await self._related(statement, memory, batch)
            last_id: int | None = None
        else:
            candidates, last_id = await self._sweep(after_id, batch, memory_id)
            related_found, note = None, None
        async with self._database.transaction() as conn:
            total_row = await fetch_one(
                conn,
                "SELECT count(*) AS total FROM memory_state "
                "WHERE type = 'belief' AND status = 'active'",
            )
            assert total_row is not None
            active = int(total_row["total"])

        sendable = [belief for belief in candidates if not _has_secret(belief["text"])]
        secret = len(candidates) - len(sendable)
        lines = [f"[belief:{b['id']}] {_cut(b['text'], _ITEM_CHARS)}" for b in sendable]
        budget = model.max_input_chars - len(_CONTRADICTIONS_SYSTEM) - len(statement) - 100
        fitted = _how_many_fit(lines, budget)
        if fitted < len(sendable) and scope == "all":
            # The rest go in the next batch: stop the sweep where the shown ones end.
            last_id = sendable[fitted - 1]["id"]
        sendable, lines = sendable[:fitted], lines[:fitted]

        proposals: list[ContradictionProposal] = []
        dropped: list[Dropped] = []
        if sendable:
            answer = await self._ask(
                model,
                _CONTRADICTIONS_SYSTEM,
                f"Statement:\n{_cut(statement, _ITEM_CHARS)}\n\nBeliefs:\n" + "\n".join(lines),
            )
            proposals, dropped = _check_contradictions(answer, statement, sendable)
            async with self._database.transaction() as conn:
                await self._log(
                    conn,
                    actor=actor,
                    model=model,
                    tool="find_contradictions",
                    asked=(
                        f"Does this contradict any belief (scope {scope})? {_cut(statement, 600)}"
                    ),
                    used=[("belief", belief["id"]) for belief in sendable],
                    result={
                        "scope": scope,
                        "shown": len(sendable),
                        "proposed": [proposal.belief_id for proposal in proposals],
                        "dropped": len(dropped),
                    },
                )

        async with self._database.transaction() as conn:
            if scope == "all":
                counts = await fetch_one(
                    conn,
                    "SELECT count(*) FILTER (WHERE id <= %(last)s) AS passed, "
                    "count(*) FILTER (WHERE id > %(last)s) AS left FROM memory_state "
                    "WHERE type = 'belief' AND status = 'active'",
                    {"last": last_id if last_id is not None else (after_id or 0)},
                )
                assert counts is not None
                looked_at, remaining = int(counts["passed"]), int(counts["left"])
            else:
                looked_at = len(sendable)
                remaining = max((related_found or 0) - len(sendable) - secret, 0)

        label = (
            f"Proposed by the language model {model.model}. The server checked that each quote "
            "is in the belief and in the statement. Nothing was changed."
        )
        message = _contradiction_message(
            scope, proposals, len(sendable), looked_at, active, remaining, secret, note
        )
        return ContradictionResult(
            model=model.model,
            label=label,
            message=message,
            statement=statement,
            scope=scope,
            proposals=proposals,
            dropped=dropped,
            shown=len(sendable),
            looked_at=looked_at,
            active_beliefs=active,
            remaining=remaining,
            related_found=related_found,
            next_after_id=last_id if scope == "all" and remaining > 0 else None,
            skipped_secret=secret,
        )

    async def _related(
        self, statement: str, memory: dict[str, Any] | None, batch: int
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """The active beliefs a statement may clash with: those on the same items, those sharing
        its evidence, and those found by words and by meaning. Returns the first `batch` of them,
        how many there were in all, and why meaning was not used, if it was not."""
        exact: list[int] = []
        me = memory["id"] if memory is not None else 0
        async with self._database.transaction() as conn:
            if memory is not None:
                if memory["type"] == "belief":
                    facts = [e["id"] for e in memory["evidence"] if e["type"] == "fact"]
                    decisions = [e["id"] for e in memory["evidence"] if e["type"] == "decision"]
                else:
                    facts, decisions = [memory["id"]], list(memory["decision_ids"])
                exact = [
                    row["id"]
                    for row in await fetch_all(
                        conn,
                        "SELECT b.id FROM memory_state b WHERE b.type = 'belief' "
                        "AND b.status = 'active' AND b.id <> %(me)s AND ("
                        "EXISTS (SELECT 1 FROM memory_nodes mn WHERE mn.memory_id = b.id "
                        "AND mn.node_id = ANY(%(nodes)s::bigint[])) "
                        "OR EXISTS (SELECT 1 FROM memory_rules mr WHERE mr.memory_id = b.id "
                        "AND mr.rule_id = ANY(%(rules)s::bigint[])) "
                        "OR EXISTS (SELECT 1 FROM belief_evidence be WHERE be.belief_id = b.id "
                        "AND (be.fact_id = ANY(%(facts)s::bigint[]) "
                        "OR be.decision_id = ANY(%(decisions)s::bigint[]))) "
                        "OR EXISTS (SELECT 1 FROM belief_evidence be JOIN memory_nodes fn "
                        "ON fn.memory_id = be.fact_id WHERE be.belief_id = b.id "
                        "AND fn.node_id = ANY(%(nodes)s::bigint[]))) ORDER BY b.id",
                        {
                            "me": me,
                            "nodes": list(memory["node_ids"]),
                            "rules": list(memory["rule_ids"]),
                            "facts": facts,
                            "decisions": decisions,
                        },
                    )
                ]
        found, note = await self._find(statement, ["belief"], 200)
        ordered = list(dict.fromkeys([*exact, *(item_id for _, item_id in found)]))
        ordered = [belief_id for belief_id in ordered if belief_id != me]
        async with self._database.transaction() as conn:
            rows = await fetch_all(
                conn,
                "SELECT id, text, confirmed_by, stale FROM memory_state "
                "WHERE type = 'belief' AND status = 'active' AND id = ANY(%s::bigint[])",
                (ordered,),
            )
        by_id = {row["id"]: row for row in rows}
        beliefs = [by_id[belief_id] for belief_id in ordered if belief_id in by_id]
        return beliefs[:batch], len(beliefs), note

    async def _sweep(
        self, after_id: int | None, batch: int, memory_id: int | None
    ) -> tuple[list[dict[str, Any]], int | None]:
        """The next `batch` active beliefs in id order after `after_id`. A belief that is the
        statement itself is passed over, not shown."""
        async with self._database.transaction() as conn:
            rows = await fetch_all(
                conn,
                "SELECT id, text, confirmed_by, stale FROM memory_state WHERE type = 'belief' "
                "AND status = 'active' AND id > %s ORDER BY id LIMIT %s",
                (after_id or 0, batch),
            )
        last_id = rows[-1]["id"] if rows else None
        return [row for row in rows if row["id"] != memory_id], last_id

    # -- reflect ------------------------------------------------------------------------------

    async def reflect(
        self,
        *,
        actor: str,
        question: str,
        node_id: int | None,
        rule_id: int | None,
        limit: int,
    ) -> ReflectResult:
        actor = clean_actor(actor)
        model = self._require_model(
            "reflect", "You can look things up yourself with recall and search."
        )
        question = question.replace("\x00", "").strip()
        if not question:
            raise Refusal("There is no question: question cannot be empty.")
        if node_id is not None and rule_id is not None:
            raise Refusal(
                "Give at most one of node_id and rule_id: the item the question is about."
            )
        limit = page_size(limit)
        if len(question) > model.max_input_chars // 2:
            raise Refusal(
                f"The question is {len(question):,} characters, which is too long. Ask it "
                "in fewer words."
            )
        self._refuse_secret("The question", question)
        if node_id is not None or rule_id is not None:
            async with self._database.transaction() as conn:
                if node_id is not None:
                    await require_node(conn, node_id)
                if rule_id is not None:
                    await require_rule(conn, rule_id)

        # What the memory has on the question: what is linked to the item asked about, then what
        # matches the question by words and by meaning, the same retrieval as search.
        items: dict[str, Decision | Memory] = {}
        if node_id is not None or rule_id is not None:
            recalled = await self._recaller.recall(
                node_id=node_id,
                rule_id=rule_id,
                show="all",
                limit=limit,
                after_id=None,
                with_similar=False,
            )
            for recalled_decision in recalled.active_decisions.items:
                items[_decision_line(recalled_decision.decision)[0]] = recalled_decision.decision
            for recalled_memory in [*recalled.facts.items, *recalled.beliefs.items]:
                items[_memory_line(recalled_memory.memory)[0]] = recalled_memory.memory
            for recalled_principle in recalled.active_principles.items:
                items[_decision_line(recalled_principle.decision)[0]] = recalled_principle.decision
        found, note = await self._find(question, ["decision", "principle", "fact", "belief"], limit)
        async with self._database.transaction() as conn:
            for item_type, item_id in found:
                if item_type in ("decision", "principle"):
                    decision = Decision.model_validate(await require_decision(conn, item_id))
                    items.setdefault(_decision_line(decision)[0], decision)
                else:
                    memory = to_memory(await require_memory(conn, item_id))
                    if not memory.stale:
                        items.setdefault(_memory_line(memory)[0], memory)

        usable: list[tuple[str, str, Decision | Memory]] = []
        secret = 0
        for entry in list(items.values())[:limit]:
            key, line = (
                _decision_line(entry) if isinstance(entry, Decision) else _memory_line(entry)
            )
            texts = (
                (entry.title, entry.text, entry.reason)
                if isinstance(entry, Decision)
                else (entry.text, entry.source)
            )
            if _has_secret(*texts):
                secret += 1
            else:
                usable.append((key, line, entry))
        sent = _how_many_fit(
            [line for _, line, _ in usable],
            model.max_input_chars - len(_REFLECT_SYSTEM) - len(question) - 100,
        )
        left_out = len(usable) - sent
        usable = usable[:sent]
        label = (
            f"Generated by the language model {model.model} from the items listed. It is not a "
            "record, it was not stored, and a belief it relied on is only a suggestion."
        )
        if not usable:
            return ReflectResult(
                model=model.model,
                label=label,
                message="Nothing in the memory matches the question, so the language model was "
                "not asked." + (f" {note}" if note else ""),
                question=question,
                answered=False,
                answer=None,
                relied_on=[],
                retrieved=0,
                left_out=0,
                skipped_secret=secret,
                search_note=note,
            )

        answer = await self._ask(
            model,
            _REFLECT_SYSTEM,
            f"Question: {question}\n\nItems:\n" + "\n".join(line for _, line, _ in usable),
        )
        retrieved = {key: entry for key, _, entry in usable}
        details: dict[str, Any] = {
            "actor": actor,
            "model": model,
            "tool": "reflect",
            "asked": question,
            "used": [_pair(key) for key in retrieved],
        }
        verdict = _check_answer(answer, retrieved)
        if isinstance(verdict, str):
            await self._log_alone(result={"refused": verdict}, **details)
            raise Refusal(f"{verdict} The answer was refused and nothing was stored.")
        text, cited = verdict
        relied = [_relied(key, retrieved[key]) for key in cited]
        await self._log_alone(result={"answered": text is not None, "cited": cited}, **details)
        return ReflectResult(
            model=model.model,
            label=label,
            message=(
                f"The model answered from {len(cited)} of the {len(usable)} items it was shown."
                if text is not None
                else "The model says the items found do not answer the question."
            ),
            question=question,
            answered=text is not None,
            answer=text,
            relied_on=relied,
            retrieved=len(usable),
            left_out=left_out,
            skipped_secret=secret,
            search_note=note,
        )


# -- checking what a model said -----------------------------------------------------------------


def _check_facts(text: str, answer: dict[str, Any]) -> tuple[list[ProposedFact], list[Dropped]]:
    entries = _list_in(answer, "facts")
    proposals: list[ProposedFact] = []
    dropped: list[Dropped] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            dropped.append(Dropped(what=_cut(str(entry), 80), why="it is not a fact object."))
            continue
        fact = entry.get("text")
        kind = entry.get("kind")
        passage = entry.get("passage")
        label = _cut(str(fact or passage or entry), 80)
        if not isinstance(fact, str) or not _squash(fact):
            dropped.append(Dropped(what=label, why="it has no text."))
        elif kind not in FACT_KINDS:
            dropped.append(Dropped(what=label, why="its kind is not world or experience."))
        elif not _quote_in(passage, text):
            dropped.append(
                Dropped(
                    what=label,
                    why="its passage is not in the text, so it cannot be checked.",
                )
            )
        elif _squash(fact).lower() in seen:
            dropped.append(Dropped(what=label, why="it repeats an earlier proposal."))
        else:
            seen.add(_squash(fact).lower())
            assert isinstance(passage, str) and isinstance(kind, str)
            proposals.append(ProposedFact(text=_squash(fact), kind=kind, passage=passage.strip()))
    return proposals, dropped


def _ids_in(entry: dict[str, Any], name: str) -> list[int] | None:
    """The ids a model listed under `name`: None if it listed something that is not an id."""
    value = entry.get(name, [])
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    ids = [_whole_number(item) for item in value]
    return None if any(item is None for item in ids) else [item for item in ids if item is not None]


def _check_beliefs(
    answer: dict[str, Any], allowed_facts: set[int], allowed_decisions: set[int]
) -> tuple[list[tuple[str, list[int], list[int]]], list[Dropped]]:
    """The beliefs a model proposed that rest only on the evidence it was given: each as its text
    and the fact ids and decision ids it rests on."""
    valid: list[tuple[str, list[int], list[int]]] = []
    dropped: list[Dropped] = []
    for entry in _list_in(answer, "beliefs"):
        if not isinstance(entry, dict):
            dropped.append(Dropped(what=_cut(str(entry), 80), why="it is not a belief object."))
            continue
        text = entry.get("text")
        label = _cut(str(text or entry), 80)
        facts = _ids_in(entry, "evidence_fact_ids")
        decisions = _ids_in(entry, "evidence_decision_ids")
        if not isinstance(text, str) or not _squash(text):
            dropped.append(Dropped(what=label, why="it has no text."))
        elif facts is None or decisions is None:
            dropped.append(Dropped(what=label, why="its evidence is not a list of ids."))
        elif not facts and not decisions:
            dropped.append(Dropped(what=label, why="it names no evidence."))
        elif bad := [item for item in facts if item not in allowed_facts]:
            dropped.append(
                Dropped(
                    what=label,
                    why=f"it names fact {bad[0]}, which it was not given.",
                )
            )
        elif bad := [item for item in decisions if item not in allowed_decisions]:
            dropped.append(
                Dropped(
                    what=label,
                    why=f"it names decision {bad[0]}, which it was not given.",
                )
            )
        else:
            valid.append((_squash(text), unique_ids(facts), unique_ids(decisions)))
    return valid, dropped


def _check_contradictions(
    answer: dict[str, Any], statement: str, shown: list[dict[str, Any]]
) -> tuple[list[ContradictionProposal], list[Dropped]]:
    by_id = {belief["id"]: belief for belief in shown}
    proposals: list[ContradictionProposal] = []
    dropped: list[Dropped] = []
    seen: set[int] = set()
    for entry in _list_in(answer, "contradictions"):
        if not isinstance(entry, dict):
            dropped.append(Dropped(what=_cut(str(entry), 80), why="it is not an object."))
            continue
        belief_id = _whole_number(entry.get("belief_id"))
        label = f"belief {entry.get('belief_id')}"
        belief = by_id.get(belief_id) if belief_id is not None else None
        reason = entry.get("reason")
        if belief is None:
            dropped.append(Dropped(what=label, why="it is not one of the beliefs that were shown."))
        elif belief_id in seen:
            dropped.append(Dropped(what=label, why="it was named more than once."))
        elif not isinstance(reason, str) or not _squash(reason):
            dropped.append(Dropped(what=label, why="it gives no reason."))
        elif not _quote_in(entry.get("belief_quote"), belief["text"]):
            dropped.append(Dropped(what=label, why="its quote of the belief is not in the belief."))
        elif not _quote_in(entry.get("statement_quote"), statement):
            dropped.append(
                Dropped(what=label, why="its quote of the statement is not in the statement.")
            )
        else:
            assert belief_id is not None
            seen.add(belief_id)
            proposals.append(
                ContradictionProposal(
                    belief_id=belief_id,
                    belief_text=belief["text"],
                    confirmed_by=belief["confirmed_by"],
                    stale=belief["stale"],
                    reason=_squash(reason),
                    belief_quote=_squash(str(entry["belief_quote"])),
                    statement_quote=_squash(str(entry["statement_quote"])),
                )
            )
    return proposals, dropped


def _check_answer(
    answer: dict[str, Any], retrieved: dict[str, Decision | Memory]
) -> tuple[str | None, list[str]] | str:
    """The answer and the keys it cites, or, if it cannot be accepted, why not."""
    text = answer.get("answer")
    if text is None or (isinstance(text, str) and not _squash(text)):
        return None, []
    if not isinstance(text, str):
        return "The language model's answer was not text."
    cited_raw = answer.get("cited", [])
    if not isinstance(cited_raw, list) or not all(isinstance(key, str) for key in cited_raw):
        return "The language model's list of what it cited was not a list of keys."
    cited = list(dict.fromkeys(key.strip().lower() for key in cited_raw))
    inline = {f"{kind}:{number}" for kind, number in _CITED.findall(text.lower())}
    for key in [*cited, *sorted(inline - set(cited))]:
        if key not in retrieved:
            return (
                f"The language model's answer refers to {key}, which was not one of the "
                f"{len(retrieved)} items it was given."
            )
    if not cited:
        return "The language model's answer cites nothing, so it cannot be checked."
    return _squash(text), cited


def _list_in(answer: dict[str, Any], name: str) -> list[Any]:
    value = answer.get(name, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise Refusal(
            f'The language model\'s answer has "{name}" that is not a list, so it cannot be '
            "used and nothing was changed."
        )
    return value


def _pair(key: str) -> tuple[str, int]:
    item_type, number = key.split(":")
    return item_type, int(number)


def _relied(key: str, entry: Decision | Memory) -> ReliedOn:
    item_type, number = _pair(key)
    if isinstance(entry, Decision):
        return ReliedOn(
            item_type=item_type,
            id=number,
            title=entry.title,
            label=f"{item_type.capitalize()} made by {entry.decided_by}.",
            status=entry.status,
            suggestion=False,
        )
    return ReliedOn(
        item_type=item_type,
        id=number,
        title=_cut(entry.text, 120),
        label=entry.label,
        status=entry.status,
        suggestion=entry.type == "belief" or entry.origin != "stated",
    )


def _contradiction_message(
    scope: str,
    proposals: list[ContradictionProposal],
    shown: int,
    looked_at: int,
    active: int,
    remaining: int,
    secret: int,
    note: str | None,
) -> str:
    found = (
        f"{len(proposals)} belief{'s' if len(proposals) != 1 else ''} contradicted"
        if proposals
        else "No contradiction found"
    )
    if scope == "all":
        where = (
            f"Looked at {looked_at} of {active} active beliefs so far ({shown} in this call); "
            + (
                f"{remaining} remain: call again with next_after_id."
                if remaining
                else "none remain."
            )
        )
    else:
        where = f"Looked at {shown} of {active} active beliefs, the ones that may clash."
        if remaining:
            where += (
                f" {remaining} more related ones were not shown: ask again with a larger batch, "
                "or use scope all."
            )
    extra = f" {secret} contain a secret and were never sent." if secret else ""
    advice = (
        " These are proposals: nothing was changed. To act on one, replace the belief with "
        "supersede_memory or retract it with retract_memory (a belief a person confirmed needs "
        "said_by and quote)."
        if proposals
        else ""
    )
    return f"{found}. {where}{extra}{advice}" + (f" {note}" if note else "")


async def _fact_in_force(conn: AsyncConnection, fact_id: int) -> Memory | str:
    """The fact, or the active one that replaced it, or in words why there is none."""
    for _ in range(20):
        row = await require_memory(conn, fact_id)
        if row["status"] == "active":
            return to_memory(row)
        if row["status"] == "retracted":
            return f"was retracted: {row['status_reason']}"
        fact_id = row["superseded_by_id"]
    return "was replaced again and again, with no active replacement"


async def _decision_in_force(conn: AsyncConnection, decision_id: int) -> Decision | str:
    """The decision, or the active one that replaced it, or in words why there is none."""
    for _ in range(20):
        row = await require_decision(conn, decision_id)
        if row["status"] == "active":
            return Decision.model_validate(row)
        decision_id = row["superseded_by_id"]
    return "was replaced again and again, with no active replacement"
