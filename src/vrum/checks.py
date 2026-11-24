"""The checks of a story or task: the items of the project's definition of done (see
definition_of_done.py), ticked one by one by an agent or a person. This module owns ticking and
unticking, how a node's checks are read, and the rule that an agent may not finish an item as done
while one of its checks is unticked.

A check the definition of done marks `[person]` is one only a person can judge: an agent may
neither tick nor untick it, and when such a check is unticked an agent is told to hand the item
over as blocked, saying what the person should look at.

A check marked `[verifier]` or `[github]` may be ticked or unticked by an agent only if its key is
a verifier's and it did not do the work on the story or task (it never claimed it and never handed
it off); a person always may. An agent that did the work hands the item over as blocked, saying it
is ready for a verifier. A `[github]` tick by an agent starts its evidence with the address of a
GitHub pull request and then says what the verifier saw there. Vrum never asks GitHub: whoever
checks does it with `gh` on their own machine (scripts/verify_pr.py) and the tick records their
word, by name, with the address, so that anyone can check it again.

Only a tick has a row (`definition_checks`): unticking deletes it, and the event log keeps both.
A tick says who made it, when, and how it was checked (`evidence`, a short plain text such as "212
tests pass"): an agent has to say how, a person may. A tick of an item that is no longer in the
definition of done stays in its table and is neither counted nor shown. Ticking never changes the
node, so it does not change its version."""

from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import Refusal, fetch_all, fetch_one, missing_node, record_event
from vrum.db import Database
from vrum.definition_of_done import DefinitionItem, item_words
from vrum.identity import current_identity
from vrum.whereabouts import pull_request_address

TICKED = "check_ticked"
UNTICKED = "check_unticked"
MOST_EVIDENCE_CHARS = 2000
# How many unticked checks a refusal names before it says "and N more".
_MOST_NAMED = 8


class Check(BaseModel):
    item: str = Field(description="The item's key, which is what set_check takes.")
    text: str = Field(description="What the check says: a line of the definition of done.")
    person_only: bool = Field(
        description="True when the definition of done marks the line [person]: only a person can "
        "judge it, so an agent may not tick or untick it."
    )
    verifier_only: bool = Field(
        description="True when the line is marked [verifier] or [github]: an agent may tick or "
        "untick it only if its key is a verifier and it did not do the work on this item (it "
        "never claimed it or handed it off). A person always may."
    )
    github: bool = Field(
        description="True when the line is marked [github]: a verifier's evidence must begin with "
        "the address of a GitHub pull request, then say what was seen there. Vrum does not ask "
        "GitHub: the tick records what the verifier reported."
    )
    checked: bool = Field(description="True when it is ticked on this story or task.")
    checked_by: str | None = Field(description="The name of whoever ticked it, when ticked.")
    checked_by_kind: str | None = Field(
        description="person or agent: what that is. Empty when it is not ticked."
    )
    checked_at: datetime | None = Field(description="When it was ticked.")
    evidence: str | None = Field(
        description="How it was checked, in the words of whoever ticked it. A person may have "
        "left it out; an agent always gives it."
    )


class NodeChecks(BaseModel):
    node: str = Field(description="The story or task, such as task:40.")
    checks: list[Check] = Field(
        description="Its checks in the order of the definition of done, ticked or not."
    )
    checks_total: int = Field(description="How many checks the definition of done has.")
    checks_ticked: int = Field(description="How many of them are ticked on this item.")


_CHECKS = (
    "SELECT i.item_key AS item, i.text, i.person_only, i.verifier_only, i.github, "
    "(c.item_key IS NOT NULL) AS checked, "
    "c.checked_by, c.checked_by_kind, c.checked_at, c.evidence FROM definition_items i "
    "LEFT JOIN definition_checks c ON c.project_id = i.project_id AND c.node = %s "
    "AND c.item_key = i.item_key ORDER BY i.position"
)


async def did_the_work(conn: AsyncConnection, node: str, name: str) -> bool:
    """True when this agent ever claimed the story or task or handed it off: it is its own work, so
    it may not check it as a verifier."""
    row = await fetch_one(
        conn,
        "SELECT 1 AS found FROM events WHERE node = %s AND actor = %s AND actor_kind = 'agent' "
        "AND action IN ('claimed', 'handoff') LIMIT 1",
        (node, name),
    )
    return row is not None


async def checks_of(conn: AsyncConnection, node: str) -> list[Check]:
    """The checks of a story or task, ticked or not, in the order of the definition of done. An
    epic has none."""
    if node.startswith("epic:"):
        return []
    return [Check.model_validate(row) for row in await fetch_all(conn, _CHECKS, (node,))]


def _named(items: list[DefinitionItem], *, keys: bool = True) -> str:
    shown = "; ".join(
        f"'{item.text}' (item {item.item})" if keys else f"'{item.text}'"
        for item in items[:_MOST_NAMED]
    )
    more = len(items) - _MOST_NAMED
    return shown + (f"; and {more} more" if more > 0 else "")


async def refuse_while_unticked(
    conn: AsyncConnection, node: str, *, instead: str, block: str
) -> None:
    """Refuse, when the one acting is an agent, to call an item done while any of its checks is
    unticked, naming them. A person is never refused: they decide. `instead` is what to do if the
    checks cannot be ticked, for the sentence that ends the refusal; `block` is how this caller
    hands an item over as blocked, up to what it says ("finish it as blocked, saying in the
    handoff note"), for the checks that the agent cannot tick at all: those only a person can
    judge, and those only a verifier who did not do the work may tick when the agent is not one."""
    who = current_identity()
    if who.kind != "agent" or node.startswith("epic:"):
        return
    waiting = [
        DefinitionItem(
            item=row["item"],
            text=row["text"],
            person_only=row["person_only"],
            verifier_only=row["verifier_only"],
            github=row["github"],
        )
        for row in await fetch_all(
            conn,
            "SELECT i.item_key AS item, i.text, i.person_only, i.verifier_only, i.github "
            "FROM definition_items i "
            "WHERE NOT EXISTS (SELECT 1 FROM definition_checks c WHERE c.node = %s "
            "AND c.item_key = i.item_key) ORDER BY i.position",
            (node,),
        )
    ]
    if not waiting:
        return
    theirs = [item for item in waiting if item.person_only]
    needing = [item for item in waiting if item.verifier_only and not item.person_only]
    own_work = bool(needing) and who.verifier and await did_the_work(conn, node, who.name)
    verifiers = [] if who.verifier and not own_work else needing
    yours = [item for item in waiting if not item.person_only and item not in verifiers]
    said = f"{node} cannot be done yet: its definition of done has checks that are not ticked. "
    if yours:
        said += f"Tick these checks first with set_check, saying how you checked: {_named(yours)}. "
    if not theirs and not verifiers:
        raise Refusal(said + f"Or {instead}. Nothing was changed.")
    if verifiers:
        one = len(verifiers) == 1
        why = (
            "you did the work on it, and a verifier may not check its own work"
            if own_work
            else "your key is not a verifier's"
        )
        said += (
            f"Only a verifier who did not do the work on {node} can tick "
            f"{'this check' if one else 'these checks'} (the definition of done marks "
            f"{'it' if one else 'them'} [verifier] or [github]), and {why}: "
            f"{_named(verifiers, keys=False)}. "
        )
    if theirs:
        one = len(theirs) == 1
        said += (
            f"Only a person can judge {'this check' if one else 'these checks'} (the definition "
            f"of done marks {'it' if one else 'them'} [person]), so you cannot tick "
            f"{'it' if one else 'them'}: {_named(theirs, keys=False)}. "
        )
    if not verifiers:
        ending = f"{block} what the person should look at; a person then judges it on the board "
        ending += "and marks it done"
    elif not theirs:
        ending = f"{block} that it is ready for a verifier; a verifier then ticks the checks "
        ending += "and marks it done"
    else:
        ending = f"{block} what the person should look at and that it needs a verifier; the "
        ending += "verifier ticks its checks, a person judges the others and marks it done"
    said += ("Then " + ending) if yours else ending[:1].upper() + ending[1:]
    raise Refusal(said + ". Nothing was changed.")


def _evidence(evidence: str | None) -> str | None:
    if evidence is None or not evidence.strip():
        return None
    cleaned = evidence.strip()
    if len(cleaned) > MOST_EVIDENCE_CHARS:
        raise Refusal(
            f"The evidence is {len(cleaned):,} characters, and the longest allowed is "
            f"{MOST_EVIDENCE_CHARS:,}. Say how you checked in a sentence or two."
        )
    return cleaned


def _find(items: list[DefinitionItem], given: str) -> DefinitionItem:
    """The item a caller means: its key, or its exact words."""
    wanted = given.strip()
    words = item_words(wanted)
    for item in items:
        if item.item == wanted.lower() or item.text == words:
            return item
    if not items:
        raise Refusal(
            "The definition of done has no checks, so there is nothing to tick. A check is a "
            "list line (starting with a dash) in the definition of done, which a person writes "
            "under Settings, Definition of done."
        )
    named = "; ".join(f"{item.item}: {item.text!r}" for item in items[:_MOST_NAMED])
    more = f"; and {len(items) - _MOST_NAMED} more" if len(items) > _MOST_NAMED else ""
    raise Refusal(
        f"There is no check {given.strip()[:60]!r} in the definition of done. Send the item's key "
        f"(its 8 characters), as get_node shows them with the node's checks. The checks are: "
        f"{named}{more}."
    )


class Checks:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def set_check(
        self, *, actor: str, node: str, item: str, checked: bool, evidence: str | None
    ) -> NodeChecks:
        """Tick (`checked` true) or untick one check of a story or task. Ticking says how it was
        checked: an agent has to, a person may. Unticking may say why. A check marked [person] is
        only for a person: an agent is refused both ways. A check marked [verifier] or [github] is
        for a person or a verifier that did not do the work on the item: any other agent is
        refused both ways, and a verifier's tick of a [github] check starts with the address of
        the GitHub pull request."""
        kind = current_identity().kind
        said = _evidence(evidence)
        if checked and kind == "agent" and said is None:
            raise Refusal(
                "Say how you checked: give evidence, a short plain text such as '212 tests pass' "
                "or 'tried it on a phone'. An agent cannot tick a check without it. Nothing was "
                "changed."
            )
        async with self._database.transaction() as conn:
            found = await fetch_one(conn, "SELECT kind FROM nodes WHERE ref = %s", (node,))
            if found is None:
                raise missing_node(node)
            if found["kind"] == "epic":
                raise Refusal(
                    f"{node} is an epic. An epic has no checks of its own: tick them on its "
                    "stories and tasks."
                )
            items = [
                DefinitionItem(
                    item=row["item_key"],
                    text=row["text"],
                    person_only=row["person_only"],
                    verifier_only=row["verifier_only"],
                    github=row["github"],
                )
                for row in await fetch_all(
                    conn,
                    "SELECT item_key, text, person_only, verifier_only, github "
                    "FROM definition_items ORDER BY position",
                )
            ]
            chosen = _find(items, item)
            if chosen.person_only and kind == "agent":
                raise Refusal(
                    f"{chosen.text!r} is a check only a person can judge (the definition of done "
                    "marks it [person]). Leave it for a person to "
                    f"{'tick' if checked else 'untick'} on the board, and say in your handoff note "
                    "what they should look at. Nothing was changed."
                )
            if chosen.verifier_only and kind == "agent":
                await _require_a_verifier(conn, node, chosen, checked, said)
            existing = await fetch_one(
                conn,
                "SELECT checked_by, checked_by_kind, checked_at, evidence FROM definition_checks "
                "WHERE node = %s AND item_key = %s FOR UPDATE",
                (node, chosen.item),
            )
            if checked:
                await _tick(conn, actor, kind, node, chosen, said, existing)
            else:
                await _untick(conn, actor, node, chosen, said, existing)
            ticked = await checks_of(conn, node)
        return NodeChecks(
            node=node,
            checks=ticked,
            checks_total=len(ticked),
            checks_ticked=sum(1 for check in ticked if check.checked),
        )


def _said(chosen: DefinitionItem, evidence: str | None) -> dict[str, Any]:
    return {"item": chosen.item, "text": chosen.text, "evidence": evidence}


async def _require_a_verifier(
    conn: AsyncConnection, node: str, chosen: DefinitionItem, checked: bool, evidence: str | None
) -> None:
    """An agent may tick or untick a [verifier] or [github] check only if its key is a verifier's
    and it did not do the work on the item (it never claimed it or handed it off). A [github]
    tick starts its evidence with a pull request address."""
    who = current_identity()
    tag = "[github]" if chosen.github else "[verifier]"
    verb = "tick" if checked else "untick"
    if not who.verifier:
        raise Refusal(
            f"{chosen.text!r} is a check only a verifier can judge (the definition of done marks "
            f"it {tag}). A verifier is an agent whose key the project's owner marked as one on the "
            f"Users page, and it must not be the agent that did the work on {node}. Leave it for "
            "one: finish the item as blocked and say in the handoff note that it is ready for a "
            "verifier. Nothing was changed."
        )
    if await did_the_work(conn, node, who.name):
        raise Refusal(
            f"You cannot {verb} {chosen.text!r} on {node}: you claimed it or handed it off, so it "
            "is your own work, and a verifier may not check what it did itself. Another verifier "
            "or a person must. Nothing was changed."
        )
    if checked and chosen.github and pull_request_address(evidence) is None:
        raise Refusal(
            f"{chosen.text!r} is a [github] check: start the evidence with the address of the "
            "GitHub pull request, such as https://github.com/owner/repo/pull/12, and then say "
            "what you saw there (that it is merged, its checks and the commit). Look with gh, "
            "on your own machine: scripts/verify_pr.py in the Vrum checkout does it and ticks "
            "the check. Vrum does not ask GitHub itself. Nothing was changed."
        )


async def _tick(
    conn: AsyncConnection,
    actor: str,
    kind: str,
    node: str,
    chosen: DefinitionItem,
    evidence: str | None,
    existing: dict[str, Any] | None,
) -> None:
    before: dict[str, Any] | None = None
    if existing is None:
        inserted = await fetch_one(
            conn,
            "INSERT INTO definition_checks (node, item_key, item_text, checked_by, "
            "checked_by_kind, evidence) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (project_id, node, item_key) DO NOTHING RETURNING item_key",
            (node, chosen.item, chosen.text, actor, kind, evidence),
        )
        if inserted is None:
            raise Refusal(
                f"Nothing to change: {chosen.text!r} was ticked on {node} a moment ago by "
                "someone else. Nothing was changed."
            )
    else:
        if evidence is None or evidence == existing["evidence"]:
            raise Refusal(
                f"Nothing to change: {chosen.text!r} is already ticked on {node} by "
                f"{existing['checked_by']}. To change how it was checked, tick it again with "
                "the new evidence. Nothing was changed."
            )
        before = _said(chosen, existing["evidence"])
        await conn.execute(
            "UPDATE definition_checks SET item_text = %s, checked_by = %s, checked_by_kind = %s, "
            "checked_at = now(), evidence = %s WHERE node = %s AND item_key = %s",
            (chosen.text, actor, kind, evidence, node, chosen.item),
        )
    await record_event(
        conn,
        node=node,
        action=TICKED,
        actor=actor,
        before=before,
        after=_said(chosen, evidence),
    )


async def _untick(
    conn: AsyncConnection,
    actor: str,
    node: str,
    chosen: DefinitionItem,
    reason: str | None,
    existing: dict[str, Any] | None,
) -> None:
    if existing is None:
        raise Refusal(
            f"Nothing to change: {chosen.text!r} is not ticked on {node}. Nothing was changed."
        )
    await conn.execute(
        "DELETE FROM definition_checks WHERE node = %s AND item_key = %s", (node, chosen.item)
    )
    await record_event(
        conn,
        node=node,
        action=UNTICKED,
        actor=actor,
        before=_said(chosen, existing["evidence"]),
        after=_said(chosen, reason),
    )


__all__ = [
    "Check",
    "Checks",
    "NodeChecks",
    "checks_of",
    "pull_request_address",
    "refuse_while_unticked",
]
