"""Email: the one place that talks to a mail server, and the one that holds what a message says.

Mail is optional and off by default (see `MailSettings` in config.py). It is used for three things
and nothing else: an invitation sent to the address the owner typed, a link to confirm an email
address, and a link to choose a new password. Every message is plain text with one link, built with
`email.message.EmailMessage`, so a name or an address that holds a line break cannot add a header,
and every link is built from the configured public address, never from a request. A message is
sent once, at once, with no queue: when the mail server does not take it, the caller says so in
words and the action it belongs to still stands.

Nothing here ever logs an address, a link, a subject or a body: a line says only which kind of
message it was and how it ended."""

import asyncio
import logging
import re
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import TYPE_CHECKING, NamedTuple

from vrum.common import Refusal
from vrum.logs import event

if TYPE_CHECKING:
    from vrum.config import MailSettings

log = logging.getLogger("vrum.mail")

# How long one attempt may take, to connect and for each answer of the mail server.
TIMEOUT_SECONDS = 10.0
# The longest a name (a project's, a person's) is when it is written into a message.
_LONGEST_NAME = 80
_MOST_OF_A_REASON = 120

# An address as it may be sent to: what is before and after the @ holds no space, quote, bracket,
# comma, colon, semicolon or backslash and no control character, so that it is exactly one mailbox.
_NOT_IN_AN_ADDRESS = r"\s@,;:<>()\[\]\"\\\x00-\x1f\x7f"
_PART = f"[^{_NOT_IN_AN_ADDRESS}]+"
_PLAIN_ADDRESS = re.compile(f"{_PART}@{_PART}\\.{_PART}")
_A_LINK = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://\S*|\bwww\.\S+", re.IGNORECASE)


class MailRefusal(Refusal):
    """A message that could not be sent, in plain words. It never holds the mail server's user name
    or password."""


class Sent(NamedTuple):
    """A message the mail server took."""

    message_id: str


class Message(NamedTuple):
    """What a message says: its subject and its plain text body."""

    subject: str
    text: str


def is_plain_address(address: str) -> bool:
    """Whether this is exactly one mailbox, written as name@example.com, that it is safe to put in
    a message's headers."""
    return len(address) <= 254 and _PLAIN_ADDRESS.fullmatch(address) is not None


def _name(text: str) -> str:
    """A name chosen by a person, as it is written into a message: on one line, short, and with no
    link in it, so that a message never carries a link that is not ours."""
    one_line = _A_LINK.sub("[link]", " ".join(text.split()))
    if len(one_line) > _LONGEST_NAME:
        one_line = one_line[: _LONGEST_NAME - 3].rstrip() + "..."
    return "".join(ch for ch in one_line if ch.isprintable())


def _days(count: int) -> str:
    return "1 day" if count == 1 else f"{count} days"


_IGNORE = "If you did not expect this, ignore this message. Nothing happens until the link is used."
_ROLES = {
    "editor": "an editor, who can read and change the project",
    "viewer": "a viewer, who can look at the project and not change it",
}


def invitation_message(
    *, inviter: str, project: str, role: str, link: str, expires_in_days: int
) -> Message:
    shown_project = _name(project)
    return Message(
        f"You are invited to {shown_project} on Vrum",
        f'{_name(inviter)} invited you to the project "{shown_project}" on Vrum, as '
        f"{_ROLES.get(role, role)}.\n\n"
        f"Open this link to accept the invitation:\n{link}\n\n"
        f"The link works once and expires in {_days(expires_in_days)}. Only the address this "
        "message was sent to can accept it: sign in or register with that address.\n\n"
        f"{_IGNORE}\n",
    )


def verification_message(*, link: str, expires_in_hours: int) -> Message:
    return Message(
        "Confirm your email address for Vrum",
        "Someone, probably you, asked Vrum to confirm this email address.\n\n"
        f"Open this link to confirm it:\n{link}\n\n"
        f"The link works once and expires in {expires_in_hours} hours.\n\n"
        f"{_IGNORE}\n",
    )


def reset_message(*, link: str, expires_in_hours: int) -> Message:
    hours = "1 hour" if expires_in_hours == 1 else f"{expires_in_hours} hours"
    return Message(
        "Reset your Vrum password",
        "Someone, probably you, asked to choose a new password for the Vrum account that uses "
        "this email address.\n\n"
        f"Open this link to choose one:\n{link}\n\n"
        f"The link works once and expires in {hours}. When you save a new password, every "
        "session of the account is signed out.\n\n"
        f"{_IGNORE}\n",
    )


def _reason(code: int, text: bytes | str, *hide: str) -> str:
    """What the mail server said, short and on one line, without the address it was sent to."""
    said = text.decode("utf-8", "replace") if isinstance(text, bytes) else text
    for hidden in hide:
        said = re.sub(re.escape(hidden), "the address", said, flags=re.IGNORECASE)
    said = "".join(ch for ch in " ".join(said.split()) if ch.isprintable())
    return f"{code} {said[:_MOST_OF_A_REASON]}".strip()


class Mailer:
    """Sends plain text messages through one mail server, as set up by the person who runs Vrum."""

    def __init__(self, settings: "MailSettings", *, hello: str = "localhost") -> None:
        self._settings = settings
        # What this server calls itself when it says hello to the mail server.
        self._hello = hello

    @property
    def where(self) -> str:
        return f"{self._settings.host}:{self._settings.port}"

    async def send(self, to: str, subject: str, text: str, *, kind: str = "message") -> Sent:
        """Send one message to one address, or raise a MailRefusal that says in plain words what
        went wrong. One attempt, with a time limit. `kind` is what the log says it was."""
        if not is_plain_address(to):
            self._failed(kind, "address")
            raise MailRefusal(
                "That does not look like an email address that mail can be sent to. Write it "
                "like name@example.com."
            )
        message = self._build(to, subject, text)
        try:
            await asyncio.to_thread(self._deliver, message, to)
        except smtplib.SMTPAuthenticationError:
            self._failed(kind, "sign_in")
            raise MailRefusal(
                "The mail server refused the sign-in, so nothing was sent. Whoever runs this "
                "server needs to check its mail user name and password."
            ) from None
        except smtplib.SMTPNotSupportedError:
            self._failed(kind, "tls")
            raise MailRefusal(
                f"The mail server at {self.where} does not offer a secure connection, so nothing "
                "was sent. Whoever runs this server needs to check the mail security setting."
            ) from None
        except smtplib.SMTPRecipientsRefused as error:
            self._failed(kind, "not_accepted")
            code, said = next(iter(error.recipients.values()), (0, b""))
            raise MailRefusal(
                f"The mail server did not accept the message: {_reason(code, said, to)}."
            ) from None
        except smtplib.SMTPConnectError:
            self._failed(kind, "unreachable")
            raise MailRefusal(f"Could not reach the mail server at {self.where}.") from None
        except smtplib.SMTPResponseException as error:
            self._failed(kind, "not_accepted")
            raise MailRefusal(
                "The mail server did not accept the message: "
                f"{_reason(error.smtp_code, error.smtp_error, to)}."
            ) from None
        except ssl.SSLError:
            self._failed(kind, "tls")
            raise MailRefusal(
                f"Could not make a secure connection to the mail server at {self.where}, so "
                "nothing was sent. Whoever runs this server needs to check the mail security "
                "setting and port."
            ) from None
        except (smtplib.SMTPException, OSError):
            self._failed(kind, "unreachable")
            raise MailRefusal(f"Could not reach the mail server at {self.where}.") from None
        except Exception:
            self._failed(kind, "error")
            raise MailRefusal(
                "The message could not be sent because of a problem inside the server. Look at "
                "the server's log."
            ) from None
        event(log, logging.INFO, "mail_sent", kind=kind)
        return Sent(str(message["Message-ID"]))

    def _failed(self, kind: str, why: str) -> None:
        event(log, logging.WARNING, "mail_failed", kind=kind, reason=why)

    def _build(self, to: str, subject: str, text: str) -> EmailMessage:
        settings = self._settings
        message = EmailMessage()
        message["From"] = Address(settings.sender_name, addr_spec=settings.sender_address)
        message["To"] = Address(addr_spec=to)
        message["Subject"] = " ".join(subject.split())
        message["Date"] = formatdate(usegmt=True)
        message["Message-ID"] = make_msgid(domain=settings.sender_address.rpartition("@")[2])
        message.set_content(text)
        return message

    def _deliver(self, message: EmailMessage, to: str) -> None:
        """The conversation with the mail server, which blocks: it runs in a thread."""
        settings = self._settings
        if settings.security == "ssl":
            connection: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.host,
                settings.port,
                local_hostname=self._hello,
                timeout=TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            )
        else:
            connection = smtplib.SMTP(
                settings.host, settings.port, local_hostname=self._hello, timeout=TIMEOUT_SECONDS
            )
        with connection:
            connection.ehlo()
            if settings.security == "starttls":
                connection.starttls(context=ssl.create_default_context())
                connection.ehlo()
            if settings.user and settings.password:
                connection.login(settings.user, settings.password)
            connection.send_message(message, from_addr=settings.sender_address, to_addrs=[to])
