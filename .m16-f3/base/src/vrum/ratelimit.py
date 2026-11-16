"""Slowing down someone who is guessing: counts failed attempts in a window of time, in memory.

It is kept in memory on purpose: it protects against guessing now, not against a patient attacker
over days, and a restart that forgets the counts costs a guesser nothing a restart did not already
give them. Each key (an email address, a network address) is blocked on its own."""

import time
from collections import deque
from collections.abc import Callable


class AttemptLimiter:
    def __init__(
        self,
        allowed: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        most_keys: int = 10_000,
    ) -> None:
        self._allowed = allowed
        self._window = window_seconds
        self._clock = clock
        # How many different keys are remembered at once. Someone who tries a new address every
        # time cannot make the server hold more than this.
        self._most_keys = most_keys
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        failures = self._failures.get(key)
        if failures is None:
            return deque()
        cutoff = self._clock() - self._window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            del self._failures[key]
        return failures

    def _sweep(self) -> None:
        """Forget every key that has nothing left in the window, and if a flood of fresh ones is
        still too many, the ones that failed longest ago."""
        cutoff = self._clock() - self._window
        for key in [key for key, held in self._failures.items() if held[-1] <= cutoff]:
            del self._failures[key]
        excess = len(self._failures) - self._most_keys
        if excess > 0:
            oldest = sorted(self._failures, key=lambda key: self._failures[key][-1])[:excess]
            for key in oldest:
                del self._failures[key]

    def seconds_to_wait(self, key: str) -> int:
        """How long until this key may try again: 0 when it may try now."""
        failures = self._recent(key)
        if len(failures) < self._allowed:
            return 0
        return max(1, int(failures[0] + self._window - self._clock()) + 1)

    def failed(self, key: str) -> None:
        self._recent(key)
        self._failures.setdefault(key, deque()).append(self._clock())
        if len(self._failures) > self._most_keys:
            self._sweep()

    def succeeded(self, key: str) -> None:
        """A good attempt forgives the failures before it."""
        self._failures.pop(key, None)
