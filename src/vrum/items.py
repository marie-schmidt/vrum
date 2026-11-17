"""How an answer names an item that can be of any of the six kinds (epic, story, task, requirement,
decision, fact): in search results, similar items, overlaps, mentions and reindex reports.

Every such item has its `item_type` and its `ref`, the one way text and tools name it (`story:12`,
`decision:7`). A requirement, decision or fact also has its own `id`. An epic, story or task has no
`id`: the number in its ref is all there is, and it is only unique within its kind, so the answer
leaves `id` out for it."""

from typing import Any

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from vrum.common import NODE_KINDS


def named(item_type: str, number: int) -> dict[str, Any]:
    """The naming fields of an item, to build a model with. `number` is the number of an epic,
    story or task within its kind, or the id of a requirement, decision or fact."""
    return {
        "item_type": item_type,
        "ref": f"{item_type}:{number}",
        "id": None if item_type in NODE_KINDS else number,
    }


class NamedItem(BaseModel):
    item_type: str = Field(description="epic, story, task, rule, decision or fact.")
    ref: str = Field(
        description="How text and tools name it: epic:3, story:12, task:40, rule:3, decision:7 or "
        "fact:5."
    )
    id: int | None = Field(
        default=None,
        description="Only for a requirement, decision or fact: its id. An epic, story or task has "
        "none (leave this out): its ref names it.",
    )

    @model_serializer(mode="wrap")
    def _leave_out_an_empty_id(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        written: dict[str, Any] = handler(self)
        if written.get("id") is None:
            written.pop("id", None)
        return written
