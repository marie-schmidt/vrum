"""Who is making a call. The one owner of the answer.

Everything Vrum records about who did something comes from here and from nowhere a caller can
reach: a call over the network is the agent whose key it carries (named by the key), and a call the
board makes is the person who is signed in. The guard (guard.py) and the board decide it before
the tool runs and set it with `working_as`; the tools and everything they call only read it, so
no argument can say who is asking.

An identity is a kind and a name. The kind is `person` or `agent`; history written before
identities were verified (an old export that is loaded) has the kind `unknown`, which no call can
have."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

Kind = Literal["person", "agent"]


@dataclass(frozen=True)
class Identity:
    kind: Kind
    name: str


class NoIdentity(RuntimeError):
    """Work was started with nobody named as its author. This is a fault in the server, never
    something a caller did: the work is refused rather than recorded under a made-up name."""


_identity: ContextVar[Identity | None] = ContextVar("vrum_identity", default=None)


def person(name: str) -> Identity:
    return Identity("person", name)


def agent(name: str) -> Identity:
    return Identity("agent", name)


@contextmanager
def working_as(identity: Identity) -> Iterator[None]:
    """Everything done inside this block is done by this identity."""
    token = _identity.set(identity)
    try:
        yield
    finally:
        _identity.reset(token)


def current_identity() -> Identity:
    found = _identity.get()
    if found is None:
        raise NoIdentity("Work on a project's graph was started with nobody named as its author.")
    return found
