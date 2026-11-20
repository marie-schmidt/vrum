"""A small mail server that keeps what it is sent, for the check stack and the demo.

It lets ./check.sh prove what Vrum does with email (an invitation, a link to confirm an address, a
link to choose a new password) without a real mail account and with answers that are the same every
run, and lets the demo show the messages Vrum sends. It does not prove how a real provider behaves:
TLS and sign-in are not offered here and are tried by hand with a real provider.

Run as `python scripted_mail.py`. It listens on two ports:
  SMTP (port 2525, or SMTP_PORT): EHLO, HELO, MAIL, RCPT, DATA, RSET, NOOP and QUIT, no TLS and no
    sign-in. It takes every recipient except an address at reject.example, which it answers with
    550, so that tests can see what Vrum does when the mail server says no.
  HTTP (port 8025, or HTTP_PORT):
    GET    /messages[?to=address]  what it was sent, as JSON, newest first: id, to, from, subject,
                                   text, received_at
    DELETE /messages               forget them
    GET    /health
    GET    /                       the same messages as a plain page, with the links clickable
"""

import asyncio
import html
import json
import os
import re
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs, urlsplit

REJECTED_DOMAIN = "reject.example"
LONGEST_MESSAGE = 1024 * 1024

MESSAGES: list[dict[str, Any]] = []
_A_LINK = re.compile(r"https?://[^\s<>\"]+")


def _address(argument: str) -> str:
    """The address in `FROM:<a@b.c> SIZE=100` or `TO:<a@b.c>`."""
    found = re.search(r"<([^>]*)>", argument)
    if found:
        return found.group(1)
    words = argument.partition(":")[2].split()
    return words[0] if words else ""


def _text_of(raw: bytes) -> dict[str, str]:
    parsed = message_from_bytes(raw, policy=policy.default)
    assert isinstance(parsed, EmailMessage)
    body = parsed.get_body(preferencelist=("plain",))
    return {
        "from": str(parsed["From"] or ""),
        "subject": str(parsed["Subject"] or ""),
        "text": str(body.get_content()) if body is not None else "",
    }


async def _smtp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    async def say(line: str) -> None:
        writer.write(line.encode() + b"\r\n")
        await writer.drain()

    sender = ""
    recipients: list[str] = []
    try:
        await say("220 scripted-mail ESMTP ready")
        while line := await reader.readline():
            command, _, argument = line.decode("utf-8", "replace").strip().partition(" ")
            verb = command.upper()
            if verb == "EHLO":
                await say("250-scripted-mail")
                await say("250-8BITMIME")
                await say("250 SMTPUTF8")
            elif verb == "HELO":
                await say("250 scripted-mail")
            elif verb == "MAIL":
                sender, recipients = _address(argument), []
                await say("250 2.1.0 OK")
            elif verb == "RCPT":
                address = _address(argument)
                if address.lower().endswith("@" + REJECTED_DOMAIN):
                    await say(f"550 5.1.1 <{address}>: Recipient address rejected: no such mailbox")
                else:
                    recipients.append(address)
                    await say("250 2.1.5 OK")
            elif verb == "DATA":
                if not recipients:
                    await say("503 5.5.1 need a recipient first")
                    continue
                await say("354 End data with <CR><LF>.<CR><LF>")
                raw = bytearray()
                while True:
                    part = await reader.readline()
                    if not part or part.rstrip(b"\r\n") == b".":
                        break
                    raw += part[1:] if part.startswith(b"..") else part
                if len(raw) > LONGEST_MESSAGE:
                    await say("552 5.3.4 message too big")
                    continue
                MESSAGES.append(
                    {
                        "id": len(MESSAGES) + 1,
                        "to": ", ".join(recipients),
                        "envelope_from": sender,
                        "received_at": datetime.now(UTC).isoformat(timespec="seconds"),
                        **_text_of(bytes(raw)),
                    }
                )
                sender, recipients = "", []
                await say("250 2.0.0 OK queued")
            elif verb in ("RSET", "NOOP"):
                sender, recipients = "", []
                await say("250 2.0.0 OK")
            elif verb == "QUIT":
                await say("221 2.0.0 Bye")
                break
            else:
                await say("502 5.5.2 command not recognized")
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


def _page() -> bytes:
    cards = []
    for one in reversed(MESSAGES):
        shown = html.escape(one["text"])
        shown = _A_LINK.sub(lambda found: f'<a href="{found.group(0)}">{found.group(0)}</a>', shown)
        cards.append(
            "<article><h2>" + html.escape(one["subject"]) + "</h2>"
            f"<p>To {html.escape(one['to'])}, from {html.escape(one['from'])}, "
            f"{html.escape(one['received_at'])}</p><pre>{shown}</pre></article>"
        )
    body = "".join(cards) or "<p>Nothing has been sent yet.</p>"
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="5">'
        "<title>Sent email (scripted-mail)</title><style>"
        "body{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem}"
        "article{border:1px solid #ccc;border-radius:8px;padding:0 1rem;margin:1rem 0}"
        "h2{font-size:1.1rem}p{color:#555}pre{white-space:pre-wrap;word-break:break-word}"
        "</style></head><body><h1>Sent email</h1>"
        "<p>This page shows what Vrum sent. No real email leaves this machine.</p>"
        f"{body}</body></html>"
    ).encode()


async def _http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = (await reader.readline()).decode("latin-1").split()
        while (await reader.readline()).strip():
            pass
        method, target = (request + ["", ""])[:2]
        url = urlsplit(target)
        status, kind, body = 404, "application/json", b'{"error": "not found"}'
        if url.path == "/health" and method == "GET":
            status, body = 200, b'{"status": "ok"}'
        elif url.path == "/messages" and method == "GET":
            wanted = parse_qs(url.query).get("to")
            found = [one for one in MESSAGES if not wanted or wanted[0] in one["to"]]
            status, body = 200, json.dumps(found[::-1]).encode()
        elif url.path == "/messages" and method == "DELETE":
            MESSAGES.clear()
            status, body = 200, b'{"deleted": true}'
        elif url.path == "/" and method == "GET":
            status, kind, body = 200, "text/html; charset=utf-8", _page()
        head = (
            f"HTTP/1.1 {status} OK\r\nContent-Type: {kind}\r\nContent-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
        )
        writer.write(head.encode() + body)
        await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


async def main() -> None:
    smtp = await asyncio.start_server(_smtp, "0.0.0.0", int(os.environ.get("SMTP_PORT", "2525")))
    web = await asyncio.start_server(_http, "0.0.0.0", int(os.environ.get("HTTP_PORT", "8025")))
    async with smtp, web:
        await asyncio.gather(smtp.serve_forever(), web.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
