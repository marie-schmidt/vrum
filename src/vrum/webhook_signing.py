"""Signing a webhook the way the Standard Webhooks specification has it (M48), so that any receiver
with a Standard Webhooks library can check it.

A message has an id that stays the same through every retry (`webhook-id`), the time of the attempt
in whole seconds (`webhook-timestamp`) and a list of signatures (`webhook-signature`), each
`v1,` and the base64 of HMAC-SHA256 over `<id>.<timestamp>.<body>` with the secret's bytes. There
is more than one signature while a secret is being replaced: the receiver accepts the message if
any one fits the secret it holds. A secret is `whsec_` and the base64 of 32 random bytes.

This module is pure. It is also what the stand-in receiver of the checks and the tests use to
verify a message, so the sender and the checker cannot drift apart."""

import base64
import binascii
import hashlib
import hmac
import secrets
from collections.abc import Mapping, Sequence

PREFIX = "whsec_"
# How far a timestamp may be from the receiver's clock, in seconds, before it is refused.
TOLERANCE_SECONDS = 300


def new_secret() -> str:
    """A new signing secret."""
    return PREFIX + base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def _key(secret: str) -> bytes:
    return base64.b64decode(secret.removeprefix(PREFIX), validate=True)


def signature(secret: str, message_id: str, timestamp: int, body: str) -> str:
    """One signature, as `v1,<base64>`."""
    signed = f"{message_id}.{timestamp}.{body}".encode()
    digest = hmac.new(_key(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


def headers(
    secrets_in_force: Sequence[str], message_id: str, timestamp: int, body: str
) -> dict[str, str]:
    """The three headers of a message, signed with every secret in force (the new one, and the old
    one while it is being replaced)."""
    return {
        "webhook-id": message_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": " ".join(
            signature(secret, message_id, timestamp, body) for secret in secrets_in_force
        ),
    }


def problem(
    secret: str,
    received: Mapping[str, str],
    body: str,
    *,
    now: int,
    tolerance: int = TOLERANCE_SECONDS,
) -> str | None:
    """Why a message does not check out, in words, or None when it does. `received` has the
    headers with lower case names."""
    message_id = received.get("webhook-id", "")
    stamp = received.get("webhook-timestamp", "")
    listed = received.get("webhook-signature", "")
    if not message_id or not stamp or not listed:
        return "a header is missing"
    try:
        sent = int(stamp)
    except ValueError:
        return "the timestamp is not a number of seconds"
    if abs(now - sent) > tolerance:
        return "the timestamp is too far from now"
    try:
        wanted = signature(secret, message_id, sent, body)
    except (binascii.Error, ValueError):
        return "the secret is not a signing secret"
    if not any(hmac.compare_digest(wanted, one) for one in listed.split()):
        return "no signature fits"
    return None
