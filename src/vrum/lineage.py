"""What sits above an item, and which notes reach it from there.

An epic, story or task is covered by what is linked to it and by what is linked to the story and
epic it belongs to. `recall` and `next_work` both ask this module, so the two cannot give different
answers about the same item. Nothing here is inferred: a note reaches an item only through a link
somebody made to the item or to one of the items above it."""

from psycopg import AsyncConnection

from vrum.common import fetch_all


async def chain_of(conn: AsyncConnection, ref: str) -> list[str]:
    """The item and everything above it, nearest first: a task, its story, its epic."""
    rows = await fetch_all(
        conn,
        "WITH RECURSIVE up AS (SELECT ref, parent, 0 AS depth FROM nodes WHERE ref = %s "
        "UNION ALL SELECT n.ref, n.parent, up.depth + 1 FROM nodes n JOIN up ON n.ref = up.parent) "
        "SELECT ref FROM up ORDER BY depth",
        (ref,),
    )
    return [row["ref"] for row in rows]


def note_in_chain(id_column: str) -> str:
    """A condition: the note is linked to an item of the chain (the query's `chain` list)."""
    return f"{id_column} IN (SELECT note_id FROM note_nodes WHERE node = ANY(%(chain)s))"


def criteria_holders(chain: list[str]) -> list[str]:
    """The items whose acceptance criteria apply to the item at the start of the chain, nearest
    first: the item itself when it is a story or task, then the story it is in. An epic has none
    of its own, and nothing comes up from the stories and tasks under it."""
    return [ref for ref in chain if not ref.startswith("epic:")]


# Conditions on `rules` (with the query's `holders`, from `criteria_holders`): the acceptance
# criteria in force on any of those items, the item's own first.
CRITERIA_IN_FORCE = "node = ANY(%(holders)s) AND status <> 'dropped'"
CRITERIA_ORDER = "array_position(%(holders)s::text[], node), id"


async def nearest_links(conn: AsyncConnection, ids: list[int], chain: list[str]) -> dict[int, str]:
    """For each note, the item of the chain it is linked to that is nearest the item asked about."""
    if not ids:
        return {}
    rows = await fetch_all(
        conn,
        "SELECT note_id AS record, node FROM note_nodes "
        "WHERE note_id = ANY(%(ids)s) AND node = ANY(%(chain)s)",
        {"ids": ids, "chain": chain},
    )
    nearest: dict[int, str] = {}
    for row in rows:
        known = nearest.get(row["record"])
        if known is None or chain.index(row["node"]) < chain.index(known):
            nearest[row["record"]] = row["node"]
    return nearest
