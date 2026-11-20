"""Email with no containers: the settings, how the mailer talks to a mail server (with a stand-in
for smtplib, since the check stack's mail server has no TLS), what every message says, the shapes
of the new tokens, and the limits on how much Vrum sends. What happens against a real stack is in
test_email_integration.py."""

import asyncio
import logging
import re
import smtplib
import ssl
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest

from vrum.accounts import (
    INVITE_NO_MAIL,
    RESET_NO_MAIL,
    AccessRefusal,
    Accounts,
    ProjectInfo,
    mask_email,
)
from vrum.config import ConfigError, Settings
from vrum.db import Database
from vrum.mail import (
    Mailer,
    MailRefusal,
    invitation_message,
    is_plain_address,
    reset_message,
    verification_message,
)
from vrum.ratelimit import AttemptLimiter
from vrum.secret_formats import find_secret, redact_secrets

ROOT = Path(__file__).resolve().parent.parent
BASE = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "s3cret-value"}
MAIL = {**BASE, "VRUM_SMTP_HOST": "smtp.example.com", "VRUM_SMTP_FROM": "Vrum <vrum@example.com>"}
TOKEN = "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fG3hJ5vW7"
MAIL_NAMES = (
    "VRUM_PUBLIC_URL",
    "VRUM_SMTP_HOST",
    "VRUM_SMTP_PORT",
    "VRUM_SMTP_SECURITY",
    "VRUM_SMTP_USER",
    "VRUM_SMTP_PASSWORD",
    "VRUM_SMTP_FROM",
)


# -- settings ----------------------------------------------------------------------------------


def test_there_is_no_email_unless_a_mail_server_is_set_up() -> None:
    settings = Settings.from_env(BASE)
    assert settings.mail is None
    assert settings.public_url == "http://localhost:8000"
    # Empty settings, which is how compose.yaml passes the ones nobody set, are not settings.
    empty = {name: "" for name in MAIL_NAMES}
    assert Settings.from_env({**BASE, **empty}).mail is None


def test_the_compose_file_passes_every_mail_setting_with_an_empty_default() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    for name in MAIL_NAMES:
        assert f"{name}: ${{{name}:-}}" in compose
    assert not re.search(r"\$\{[A-Z_]+:?\?", compose)


def test_the_public_address_defaults_to_this_machine_and_loses_a_trailing_slash() -> None:
    assert Settings.from_env({**BASE, "VRUM_PORT": "9000"}).public_url == "http://localhost:9000"
    given = {**BASE, "VRUM_PUBLIC_URL": " https://vrum.example.com/ "}
    assert Settings.from_env(given).public_url == "https://vrum.example.com"


@pytest.mark.parametrize(
    "bad",
    [
        "vrum.example.com",
        "ftp://vrum.example.com",
        "https://",
        "https://vrum.example.com/?a=b",
        "https://vrum.example.com/#top",
        "https://user:pass@vrum.example.com",
        "https://vrum.example.com/two words",
    ],
)
def test_a_public_address_that_cannot_be_a_board_address_is_refused(bad: str) -> None:
    with pytest.raises(ConfigError, match="VRUM_PUBLIC_URL must be the address people open"):
        Settings.from_env({**BASE, "VRUM_PUBLIC_URL": bad})


def test_mail_settings_have_defaults_and_can_be_changed() -> None:
    mail = Settings.from_env(MAIL).mail
    assert mail is not None
    assert (mail.host, mail.port, mail.security) == ("smtp.example.com", 587, "starttls")
    assert (mail.user, mail.password) == (None, None)
    assert (mail.sender_name, mail.sender_address) == ("Vrum", "vrum@example.com")
    assert mail.sender == "Vrum <vrum@example.com>"

    secure = Settings.from_env({**MAIL, "VRUM_SMTP_SECURITY": "SSL"}).mail
    assert secure is not None and (secure.security, secure.port) == ("ssl", 465)
    chosen = Settings.from_env({**MAIL, "VRUM_SMTP_SECURITY": "none", "VRUM_SMTP_PORT": "1025"})
    assert chosen.mail is not None and (chosen.mail.security, chosen.mail.port) == ("none", 1025)
    bare = Settings.from_env({**MAIL, "VRUM_SMTP_FROM": "vrum@example.com"}).mail
    assert bare is not None and bare.sender_name == "" and bare.sender == "vrum@example.com"


def test_the_mail_password_is_a_secret_like_the_others() -> None:
    signed_in = {**MAIL, "VRUM_SMTP_USER": "mailer", "VRUM_SMTP_PASSWORD": "mail-pass-value"}
    settings = Settings.from_env(signed_in)
    assert settings.mail is not None and settings.mail.user == "mailer"
    assert set(settings.secrets()) == {"s3cret-value", "mail-pass-value"}
    assert "mail-pass-value" not in repr(settings)


def test_the_mail_password_can_come_from_a_file(tmp_path: Path) -> None:
    password_file = tmp_path / "mail-password"
    password_file.write_text("from-the-file\n", encoding="utf-8")
    given = {**MAIL, "VRUM_SMTP_USER": "mailer", "VRUM_SMTP_PASSWORD_FILE": str(password_file)}
    mail = Settings.from_env(given).mail
    assert mail is not None and mail.password == "from-the-file"
    with pytest.raises(ConfigError, match="only one of VRUM_SMTP_PASSWORD and"):
        Settings.from_env({**given, "VRUM_SMTP_PASSWORD": "x"})


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"VRUM_SMTP_HOST": ""}, "VRUM_SMTP_HOST is not set"),
        ({"VRUM_SMTP_HOST": "two words"}, "VRUM_SMTP_HOST must be a host name"),
        ({"VRUM_SMTP_FROM": ""}, "VRUM_SMTP_FROM is not set"),
        ({"VRUM_SMTP_FROM": "not an address"}, "VRUM_SMTP_FROM must be the address"),
        ({"VRUM_SMTP_FROM": "Vrum <vrum@localhost>"}, "VRUM_SMTP_FROM must be the address"),
        ({"VRUM_SMTP_FROM": "Vrum <a@b.c>, <d@e.f>"}, "VRUM_SMTP_FROM must be the address"),
        ({"VRUM_SMTP_FROM": "a@b.c\r\nBcc: x@y.z"}, "VRUM_SMTP_FROM must be the address"),
        ({"VRUM_SMTP_SECURITY": "tls"}, "VRUM_SMTP_SECURITY must be starttls, ssl or none"),
        ({"VRUM_SMTP_PORT": "smtp"}, "VRUM_SMTP_PORT must be a whole number from 1 to 65535"),
        ({"VRUM_SMTP_USER": "mailer"}, "VRUM_SMTP_PASSWORD is not set"),
        ({"VRUM_SMTP_PASSWORD": "pw"}, "VRUM_SMTP_USER is not set"),
        (
            {"VRUM_SMTP_SECURITY": "none", "VRUM_SMTP_USER": "u", "VRUM_SMTP_PASSWORD": "p"},
            "sends everything, the password included, as plain text",
        ),
    ],
)
def test_mail_settings_that_make_no_sense_stop_the_server_in_plain_words(
    changes: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env({**MAIL, **changes})


def test_mail_settings_without_a_host_are_refused_instead_of_quietly_ignored() -> None:
    with pytest.raises(ConfigError, match="VRUM_SMTP_HOST is not set. The VRUM_SMTP_ settings"):
        Settings.from_env({**BASE, "VRUM_SMTP_FROM": "vrum@example.com"})


# -- the addresses mail may be sent to ---------------------------------------------------------


@pytest.mark.parametrize("good", ["a@b.co", "first.last+tag@mail.example.org", "o'brien@x.io"])
def test_an_address_that_is_one_mailbox_may_be_sent_to(good: str) -> None:
    assert is_plain_address(good)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "a@b",
        "a b@c.d",
        "a@b.c\r\nBcc: x@y.z",
        "a@b.c\n",
        "a,b@c.d",
        "a@b.c, d@e.f",
        "a@b.c;d@e.f",
        "<a@b.c>",
        '"a"@b.c',
        "a@b@c.d",
        "a@b.c\x00",
        "x" * 250 + "@b.cd",
    ],
)
def test_anything_that_could_be_more_than_one_mailbox_or_add_a_header_is_not_sent_to(
    bad: str,
) -> None:
    assert not is_plain_address(bad)


def test_an_address_is_masked_by_everything_but_its_first_letter() -> None:
    assert mask_email("priya@example.com") == "p***@example.com"
    assert mask_email("x@example.com") == "x***@example.com"
    assert mask_email("nonsense") == "***"


# -- what a message says -----------------------------------------------------------------------

LINK = f"https://vrum.example.com/board/invite/vri_{TOKEN}"
DASHES = ("\u2014", "\u2013")


def messages() -> list[tuple[str, Any]]:
    return [
        (
            "invitation",
            invitation_message(
                inviter="Marie", project="Riverside", role="editor", link=LINK, expires_in_days=7
            ),
        ),
        (
            "viewer",
            invitation_message(
                inviter="Marie", project="Riverside", role="viewer", link=LINK, expires_in_days=7
            ),
        ),
        ("verification", verification_message(link=LINK, expires_in_hours=24)),
        ("reset", reset_message(link=LINK, expires_in_hours=1)),
    ]


def test_every_message_is_plain_text_with_one_link_the_expiry_and_how_to_ignore_it() -> None:
    for kind, message in messages():
        assert message.text.count("://") == 1, kind
        assert message.text.count(LINK) == 1, kind
        assert "<" not in message.text and ">" not in message.text, kind
        assert not any(dash in message.subject + message.text for dash in DASHES), kind
        assert "expires in" in message.text, kind
        assert "If you did not expect this, ignore this message." in message.text, kind
        assert "Nothing happens until the link is used." in message.text, kind
        assert "\n" not in message.subject, kind
        assert "Vrum" in message.text, kind


def test_the_subjects_and_the_invitation_say_who_what_and_which_role() -> None:
    invitation, viewer, verification, reset = (message for _, message in messages())
    assert invitation.subject == "You are invited to Riverside on Vrum"
    assert 'Marie invited you to the project "Riverside" on Vrum, as an editor' in invitation.text
    assert "a viewer, who can look at the project and not change it" in viewer.text
    assert "expires in 7 days" in invitation.text
    assert verification.subject == "Confirm your email address for Vrum"
    assert "expires in 24 hours" in verification.text
    assert reset.subject == "Reset your Vrum password"
    assert "expires in 1 hour." in reset.text
    assert "every session of the account is signed out" in reset.text


def test_a_name_chosen_by_a_person_cannot_add_a_link_a_line_or_a_header_to_a_message() -> None:
    sneaky = invitation_message(
        inviter="Eve\r\nBcc: x@y.z",
        project="Pay now at https://evil.example/pay or www.evil.example\nBcc: x@y.z",
        role="editor",
        link=LINK,
        expires_in_days=7,
    )
    assert sneaky.text.count("://") == 1 and "evil" not in sneaky.text
    assert "\n" not in sneaky.subject and "\r" not in sneaky.subject
    assert "Bcc: x@y.z" in sneaky.text.replace("\n", " ")  # as words in the text, never a header
    long = invitation_message(
        inviter="n" * 500, project="p" * 500, role="viewer", link=LINK, expires_in_days=7
    )
    assert len(long.subject) < 120 and "n" * 100 not in long.text


# -- the mailer, against a stand-in for smtplib ------------------------------------------------


class FakeSMTP:
    """Stands in for smtplib.SMTP: it keeps what was done to it, and fails where it is told to."""

    made: ClassVar[list["FakeSMTP"]] = []
    fails: ClassVar[dict[str, Exception]] = {}

    def __init__(self, host: str, port: int, **options: Any) -> None:
        self.host, self.port, self.options = host, port, options
        self.did: list[tuple[Any, ...]] = []
        self.made.append(self)
        self._maybe_fail("connect")

    def _maybe_fail(self, step: str) -> None:
        if step in self.fails:
            raise self.fails[step]

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *_: object) -> None:
        self.did.append(("quit",))

    def ehlo(self) -> None:
        self.did.append(("ehlo",))

    def starttls(self, context: ssl.SSLContext | None = None) -> None:
        self.did.append(("starttls", context))
        self._maybe_fail("starttls")

    def login(self, user: str, password: str) -> None:
        self.did.append(("login", user, password))
        self._maybe_fail("login")

    def send_message(self, message: Any, from_addr: str, to_addrs: list[str]) -> None:
        self.did.append(("send", message, from_addr, to_addrs))
        self._maybe_fail("send")


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.made, FakeSMTP.fails = [], {}
    monkeypatch.setattr("vrum.mail.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("vrum.mail.smtplib.SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP


def mailer_for(**changes: str) -> Mailer:
    mail = Settings.from_env({**MAIL, **changes}).mail
    assert mail is not None
    return Mailer(mail, hello="vrum.example.com")


def send(mailer: Mailer, to: str = "priya@example.com", kind: str = "invitation") -> Any:
    return asyncio.run(mailer.send(to, "A subject", "The text.\n", kind=kind))


def steps(connection: FakeSMTP) -> list[str]:
    return [str(done[0]) for done in connection.did]


def test_starttls_makes_the_connection_secure_before_anything_else_is_sent(
    smtp: type[FakeSMTP],
) -> None:
    sent = send(mailer_for(VRUM_SMTP_USER="mailer", VRUM_SMTP_PASSWORD="mail-pass-value"))
    (connection,) = smtp.made
    assert (connection.host, connection.port) == ("smtp.example.com", 587)
    assert connection.options["timeout"] == 10.0
    assert connection.options["local_hostname"] == "vrum.example.com"
    assert steps(connection) == ["ehlo", "starttls", "ehlo", "login", "send", "quit"]
    assert isinstance(connection.did[1][1], ssl.SSLContext)
    assert connection.did[3] == ("login", "mailer", "mail-pass-value")
    assert sent.message_id.startswith("<") and sent.message_id.endswith("@example.com>")


def test_ssl_is_secure_from_the_first_byte_and_does_not_start_tls_again(
    smtp: type[FakeSMTP],
) -> None:
    send(mailer_for(VRUM_SMTP_SECURITY="ssl"))
    (connection,) = smtp.made
    assert isinstance(connection, FakeSMTPSSL) and connection.port == 465
    assert isinstance(connection.options["context"], ssl.SSLContext)
    assert steps(connection) == ["ehlo", "send", "quit"]


def test_none_makes_no_secure_connection_and_signs_in_to_nothing(smtp: type[FakeSMTP]) -> None:
    send(mailer_for(VRUM_SMTP_SECURITY="none", VRUM_SMTP_PORT="1025"))
    (connection,) = smtp.made
    assert not isinstance(connection, FakeSMTPSSL) and connection.port == 1025
    assert steps(connection) == ["ehlo", "send", "quit"]


def test_a_message_has_one_recipient_a_sender_and_headers_that_cannot_be_injected(
    smtp: type[FakeSMTP],
) -> None:
    asyncio.run(
        mailer_for().send(
            "priya@example.com",
            "Hello\r\nBcc: someone@else.example",
            "The text.\r\nBcc: someone@else.example\n",
        )
    )
    ((_, message, sender, recipients),) = (done for done in smtp.made[0].did if done[0] == "send")
    assert (sender, recipients) == ("vrum@example.com", ["priya@example.com"])
    assert message["From"] == "Vrum <vrum@example.com>" and message["To"] == "priya@example.com"
    assert message["Subject"] == "Hello Bcc: someone@else.example"
    assert message["Bcc"] is None and message["Date"] and message["Message-ID"]
    assert message.get_content_type() == "text/plain"
    assert "The text." in message.get_content()


def test_an_address_that_could_add_a_recipient_or_a_header_is_refused_before_connecting(
    smtp: type[FakeSMTP],
) -> None:
    for bad in ("a@b.c\r\nBcc: x@y.z", "a@b.c, x@y.z", "not an address"):
        with pytest.raises(MailRefusal, match="does not look like an email address"):
            send(mailer_for(), bad)
    assert smtp.made == []


FAILURES: list[tuple[str, Exception, str]] = [
    (
        "connect",
        ConnectionRefusedError("refused"),
        "Could not reach the mail server at smtp.example.com:587.",
    ),
    ("connect", TimeoutError("timed out"), "Could not reach the mail server at"),
    ("connect", smtplib.SMTPConnectError(421, b"busy"), "Could not reach the mail server at"),
    ("connect", smtplib.SMTPServerDisconnected("gone"), "Could not reach the mail server at"),
    (
        "starttls",
        ssl.SSLError("handshake failed for mailer-user mail-pass-value"),
        "Could not make a secure connection to the mail server at smtp.example.com:587",
    ),
    (
        "starttls",
        smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server."),
        "does not offer a secure connection",
    ),
    (
        "login",
        smtplib.SMTPAuthenticationError(535, b"5.7.8 bad credentials mailer-user mail-pass-value"),
        "The mail server refused the sign-in",
    ),
    (
        "send",
        smtplib.SMTPRecipientsRefused(
            {"priya@example.com": (550, b"5.1.1 <Priya@example.com>: Recipient address rejected")}
        ),
        "The mail server did not accept the message: 550 5.1.1 <the address>: Recipient address "
        "rejected.",
    ),
    (
        "send",
        smtplib.SMTPDataError(554, b"5.7.1 Message looks like spam\r\nand more"),
        "The mail server did not accept the message: 554 5.7.1 Message looks like spam and more.",
    ),
    ("send", RuntimeError("boom mailer-user"), "problem inside the server"),
]


@pytest.mark.parametrize(("step", "error", "said"), FAILURES)
def test_every_way_the_mail_server_can_say_no_becomes_plain_words_that_hold_no_secret(
    smtp: type[FakeSMTP], step: str, error: Exception, said: str
) -> None:
    smtp.fails = {step: error}
    signed_in = {"VRUM_SMTP_USER": "mailer-user", "VRUM_SMTP_PASSWORD": "mail-pass-value"}
    with pytest.raises(MailRefusal) as refused:
        send(mailer_for(**signed_in))
    assert said in str(refused.value), str(refused.value)
    for private in ("mailer-user", "mail-pass-value", "priya@example.com", "Priya@example.com"):
        assert private not in str(refused.value)


def test_the_log_says_what_kind_of_message_it_was_and_how_it_ended_and_nothing_more(
    smtp: type[FakeSMTP], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="vrum.mail")
    send(mailer_for(), "priya@example.com", "invitation")
    smtp.fails = {"send": smtplib.SMTPRecipientsRefused({"x@y.zz": (550, b"no <x@y.zz>")})}
    with pytest.raises(MailRefusal):
        send(mailer_for(), "x@y.zz", "password_reset")
    lines = [
        (record.getMessage(), record.fields)  # type: ignore[attr-defined]
        for record in caplog.records
        if record.name == "vrum.mail"
    ]
    assert lines == [
        ("mail_sent", {"kind": "invitation"}),
        ("mail_failed", {"kind": "password_reset", "reason": "not_accepted"}),
    ]
    everything = repr(lines)
    assert "priya" not in everything and "x@y.zz" not in everything and "://" not in everything


# -- the new tokens ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prefix", "kind"),
    [
        ("vrv_", "a Vrum email confirmation token"),
        ("vrr_", "a Vrum password reset token"),
        ("vri_", "a Vrum invitation"),
    ],
)
def test_a_confirmation_or_reset_token_is_a_secret_in_a_known_format(
    prefix: str, kind: str
) -> None:
    assert find_secret(f"see {prefix}{TOKEN} here") == kind
    assert find_secret(f"{prefix}short") is None
    assert prefix + TOKEN not in redact_secrets(f"https://x.example/board/reset/{prefix}{TOKEN}")


# -- the limits on how much is sent, and the answers without a mail server ---------------------


def a_mailer() -> Mailer:
    return mailer_for()


def accounts_with(mailer: Mailer | None) -> Accounts:
    return Accounts(cast(Database, None), mailer=mailer, public_url="https://vrum.example.com")


def test_a_few_an_hour_means_a_window_that_slides_and_is_not_extended_by_asking() -> None:
    now = [0.0]
    limiter = AttemptLimiter(3, 3600, clock=lambda: now[0])
    for minute in (0, 10, 20):
        now[0] = minute * 60.0
        assert limiter.seconds_to_wait("a") == 0
        limiter.failed("a")
    now[0] = 30 * 60.0
    assert limiter.seconds_to_wait("a") == 30 * 60 + 1
    assert limiter.seconds_to_wait("someone-else") == 0
    now[0] = 61 * 60.0
    assert limiter.seconds_to_wait("a") == 0


def test_without_a_mail_server_there_is_no_reset_no_confirmation_and_no_emailed_invitation() -> (
    None
):
    accounts = accounts_with(None)
    assert accounts.mail_enabled is False
    for call in (
        accounts.request_reset("a@b.co", "client"),
        accounts.check_reset_link("vrr_x", "client"),
        accounts.reset_password("vrr_x", "a new password", "client"),
    ):
        with pytest.raises(AccessRefusal) as refused:
            asyncio.run(call)
        assert (str(refused.value), refused.value.status) == (RESET_NO_MAIL, 404)
    assert RESET_NO_MAIL == (
        "This server cannot send email, so passwords cannot be reset by email. Ask whoever runs "
        "this server."
    )


def test_an_invitation_with_an_address_is_refused_in_words_when_there_is_no_mail_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = accounts_with(None)

    async def owner(*_: Any, **__: Any) -> ProjectInfo:
        return ProjectInfo.model_validate(
            {"id": 1, "name": "P", "created_at": "2026-11-18T10:00:00Z", "role": "owner"}
        )

    monkeypatch.setattr(accounts, "owned_project", owner)
    with pytest.raises(AccessRefusal) as refused:
        asyncio.run(accounts.create_invite(1, 1, "editor", "priya@example.com"))
    assert str(refused.value) == INVITE_NO_MAIL
    assert INVITE_NO_MAIL == "This server cannot send email, so share the link yourself."


def test_a_reset_is_asked_for_five_times_an_hour_per_address_and_twenty_per_network_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = accounts_with(a_mailer())
    asked: list[str] = []

    async def sending(address: str) -> None:
        asked.append(address)

    monkeypatch.setattr(accounts, "_mail_reset", sending)

    async def go() -> list[str | int]:
        answers: list[str | int] = []
        for _ in range(7):
            # The sixth and seventh are quietly not sent, and answer exactly as the first did.
            await accounts.request_reset(" Pat@Example.com ", "10.0.0.1")
            answers.append("ok")
        for number in range(30):
            try:
                await accounts.request_reset(f"person{number}@example.com", "10.0.0.1")
            except AccessRefusal as refused:
                answers.append(refused.status)
        await asyncio.gather(*accounts._background)
        return answers

    answers = asyncio.run(go())
    assert answers[:7] == ["ok"] * 7
    assert asked.count("pat@example.com") == 5
    # Twenty requests came from one network address: the 21st and the rest are refused in words.
    assert answers[7:] == [429] * (30 - (20 - 7)) and len(asked) == 5 + (20 - 7)
    with pytest.raises(AccessRefusal, match="Too many password reset requests were asked for"):
        asyncio.run(accounts.request_reset("late@example.com", "10.0.0.1"))


def test_a_reset_for_an_address_that_is_not_an_address_is_refused_before_anything_counts() -> None:
    accounts = accounts_with(a_mailer())
    with pytest.raises(AccessRefusal, match="does not look like an email address"):
        asyncio.run(accounts.request_reset("not an address", "10.0.0.2"))
