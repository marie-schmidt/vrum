"""Recognising secrets that are written in a well-known format, so that one is never sent to a
language model (M5C) and never stored (M6 uses the same check on writes).

Only formats that are unmistakable are checked: an access token with a known prefix, a private key
block. Ordinary text that merely mentions a password or a key is not a secret in a known format,
and refusing it would make the check something people work around."""

import re

# (what it is called in a message, the pattern). Patterns are anchored on a prefix that real
# secrets of that kind always have, so that ordinary words and ids do not match.
_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a private key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("a GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("a GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("a Slack token", re.compile(r"\bxox[abeoprs]-[A-Za-z0-9-]{10,}")),
    ("a Stripe key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{16,}\b")),
    (
        "an API key of the sk- kind (OpenAI, Anthropic and others)",
        re.compile(r"\bsk-[A-Za-z0-9_-]{24,}"),
    ),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("an npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("a Vrum agent key", re.compile(r"\bvrk_[A-Za-z0-9_-]{30,}")),
    ("a Vrum session", re.compile(r"\bvrs_[A-Za-z0-9_-]{30,}")),
    ("a Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{34,}\b")),
    ("a SendGrid key", re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")),
    (
        "a JSON web token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ),
)


def find_secret(text: str) -> str | None:
    """What kind of well-known secret the text contains, in words ("a GitHub token"), or None.
    The secret itself is never part of the answer."""
    for name, pattern in _FORMATS:
        if pattern.search(text):
            return name
    return None


def find_secret_in(value: object, where: str = "") -> tuple[str, str] | None:
    """The first well-known secret in a text, or anywhere inside lists and objects of texts, as
    (the path to the field that holds it, what kind it is). The path reads like `items[2].body`.
    The secret itself is never part of the answer."""
    if isinstance(value, str):
        found = find_secret(value)
        return (where, found) if found else None
    if isinstance(value, dict):
        children = [(f"{where}.{key}" if where else str(key), item) for key, item in value.items()]
    elif isinstance(value, list | tuple):
        children = [(f"{where}[{number}]", item) for number, item in enumerate(value)]
    else:
        return None
    for path, item in children:
        found_in = find_secret_in(item, path)
        if found_in:
            return found_in
    return None


def redact_secrets(text: str) -> str:
    """The text with every well-known secret in it replaced by a note of what was removed."""
    for name, pattern in _FORMATS:
        text = pattern.sub(f"[{name} removed]", text)
    return text
