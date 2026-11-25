"""How the values of an export file are written, and how a problem with one is told.

A file is read strictly: every value has one written form, a day is YYYY-MM-DD, a time says its
time zone, a reference to an epic, story or task is lower case with no spaces, and nothing may be
left over that the format does not know."""

from collections.abc import Iterable
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from vrum.common import Refusal, parse_iso_day, parse_node_ref

# How many problems in a file are listed before "and N more".
MOST_PROBLEMS = 20


def _day(value: Any) -> date:
    parsed = parse_iso_day(value)
    if parsed is not None:
        return parsed
    raise ValueError("must be a real day written as YYYY-MM-DD")


def _moment(value: Any) -> datetime:
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        moment = None
    if moment is None:
        raise ValueError("must be a date and time such as 2026-11-15T20:02:24.859168Z")
    if moment.tzinfo is None:
        raise ValueError("must say its time zone (end it with Z for UTC)")
    return moment


Day = Annotated[date, BeforeValidator(_day)]
Moment = Annotated[datetime, BeforeValidator(_moment)]
Id = Annotated[int, Field(strict=True, gt=0)]


def _a_node_ref(value: Any) -> str:
    """A ref in its one written form (`story:12`: lower case, no spaces), or a plain complaint."""
    try:
        if parse_node_ref(value) == value:
            return str(value)
    except Refusal:
        pass
    raise ValueError("must name an epic, story or task by its kind and number, such as story:12")


NodeRef = Annotated[str, BeforeValidator(_a_node_ref)]

Status = Literal["unconfirmed", "confirmed", "superseded", "withdrawn"]
# Who made something, and as what. `unknown` is only for historical imports that carried no kind:
# nothing that is made now has it.
ActorKind = Literal["person", "agent", "unknown"]


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def where(location: Iterable[int | str]) -> str:
    text = ""
    for part in location:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else str(part)
    return text or "the file"


def problems_in(error: ValidationError) -> list[str]:
    """What is wrong with the shape of a file, one line per place, without the values found there
    (they could be anything, a secret included)."""
    seen: set[str] = set()
    problems: list[str] = []
    for item in error.errors():
        place = where(item["loc"])
        if place in seen:
            continue
        seen.add(place)
        message = item["msg"].removeprefix("Value error, ")
        problems.append(f"{place}: {message}")
    return problems


def refuse_file(problems: list[str]) -> Refusal:
    shown = "; ".join(problems[:MOST_PROBLEMS])
    more = f"; and {len(problems) - MOST_PROBLEMS} more" if len(problems) > MOST_PROBLEMS else ""
    return Refusal(
        f"This is not a file that import_graph can load, and nothing was imported. "
        f"What is wrong: {shown}{more}. Use a file written by export_graph."
    )
