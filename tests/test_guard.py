"""What a refused call says when its arguments do not fit the tool: plain, naming the fields, and
never repeating what was sent."""

import asyncio

import psycopg
import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import BaseModel, Field, ValidationError

from vrum.db import working_for
from vrum.guard import GuardedServer, plain_validation_message
from vrum.identity import person, working_as


class Arguments(BaseModel):
    actor: str
    limit: int = Field(ge=1, le=200)
    title: str | None = None


def problems(**sent: object) -> ValidationError:
    try:
        Arguments(**sent)  # type: ignore[arg-type]
    except ValidationError as error:
        return error
    raise AssertionError("these arguments should not have been valid")


def test_the_fields_are_named_with_what_was_wrong_and_nothing_that_was_sent_is_repeated() -> None:
    message = plain_validation_message(
        "list_nodes", problems(actor=5, limit="a secret-looking value xyz-12345")
    )

    assert message.startswith("Error executing tool list_nodes: the arguments are not valid (")
    assert "actor: Input should be a valid string" in message
    assert "limit: Input should be a valid integer" in message
    assert "Nothing was changed" in message and "against the tool's description" in message
    assert "xyz-12345" not in message and "input_value" not in message
    assert "errors.pydantic.dev" not in message


def test_a_missing_argument_does_not_echo_the_arguments_that_were_given() -> None:
    message = plain_validation_message("create_node", problems(title="x" * 5000 + " the-body"))

    assert "actor: Field required" in message and "limit: Field required" in message
    assert "the-body" not in message and len(message) < 400


def test_a_range_is_said_in_words() -> None:
    message = plain_validation_message("list_nodes", problems(actor="a", limit=500))
    assert "limit: Input should be less than or equal to 200" in message


def test_only_the_first_few_problems_are_listed_and_the_rest_are_counted() -> None:
    class Many(BaseModel):
        a: int
        b: int
        c: int
        d: int
        e: int
        f: int
        g: int
        h: int
        i: int
        j: int

    try:
        Many()  # type: ignore[call-arg]
    except ValidationError as error:
        message = plain_validation_message("many", error)

    assert "a: Field required" in message and "h: Field required" in message
    assert "i: Field required" not in message and "and 2 more" in message


def test_guarded_server_translates_database_errors_into_plain_tool_errors() -> None:
    server = GuardedServer("test")

    @server.tool()
    async def unreachable() -> str:
        raise psycopg.OperationalError("connection refused")

    @server.tool()
    async def huge_text() -> str:
        raise psycopg.errors.ProgramLimitExceeded("string is too long for tsvector")

    with working_for(1), working_as(person("Priya")):
        with pytest.raises(ToolError) as down:
            asyncio.run(server.call_tool("unreachable", {}))
        assert not isinstance(down.value, UnexpectedToolError)
        assert "cannot reach its database" in str(down.value)
        assert "connection refused" not in str(down.value)

        with pytest.raises(ToolError) as too_long:
            asyncio.run(server.call_tool("huge_text", {}))
        assert not isinstance(too_long.value, UnexpectedToolError)
        assert "cannot index more than about 1 MB" in str(too_long.value)
