"""Only the reindex job writes to the semantic index. The rest of the code only reads it."""

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src" / "vrum"
# Anything that creates, changes or removes the index or its rows.
WRITES = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE|TRUNCATE|"
    r"DROP\s+TABLE(\s+IF\s+EXISTS)?|CREATE\s+INDEX\s+\w+\s+ON)\s+semantic_index",
    re.IGNORECASE,
)


def test_no_code_but_the_reindex_job_writes_to_the_index() -> None:
    # Everything that is served or run, the board's endpoints and pages included.
    writers = {
        path.name
        for path in SOURCE.rglob("*")
        if path.suffix in (".py", ".sql", ".js", ".html")
        and WRITES.search(path.read_text(encoding="utf-8"))
    }
    assert writers == {"indexing.py"}


def test_the_index_tables_are_not_part_of_the_graph_schema() -> None:
    schema = (SOURCE / "schema.sql").read_text(encoding="utf-8")
    assert "semantic_index" not in schema
