"""Email (M24), through the board's own HTTP API against the real containers, with the stand-in mail
server of the check stack (tests/scripted_mail.py) read at VRUM_TEST_MAIL_URL: no browser and no MCP
client, because everything here is decided by the server.

What has to hold: an invitation can be emailed to the address the owner typed, its link works once
and only for an account with that address, and the owner is told in plain words when the message
was not sent (the invitation stands); a link to confirm an address and a link to choose a new
password are sent only by the server and work once, for a short time, and opening one in a browser
or a mail scanner never uses it; asking for a new password answers the same, and takes the same
time, whether or not the address has an account; a reset ends every session of the account and
leaves its agent keys alone; a link is built from the public address and never from the request;
tokens are kept only as hashes; and nothing the server logs holds an address, a link or a token.

The limits live in the server's memory for an hour, and this file asks for 15 resets of the 20 a
network address may ask for. Run it again on the same stack within the hour only after
`docker compose restart app`; a fresh stack (./check.sh) needs nothing."""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote, urlsplit

import pytest

from tests.helpers import compose, run_sql, unique_word
from tests.world import (
    Person,
    Project,
    Reply,
    api,
    join_project,
    make_project,
    register,
    register_as,
    server_base,
)
from vrum.accounts import (
    INVITE_NOT_VALID,
    RESET_LINK_NOT_VALID,
    RESET_REQUESTED,
    VERIFY_LINK_NOT_VALID,
)

pytestmark = pytest.mark.integration

FROM = "Vrum <vrum@example.com>"
DASHES = ("\u2014", "\u2013")


# -- the stand-in mail server and what was sent -------------------------------------------------


def mail_url() -> str:
    url = os.environ.get("VRUM_TEST_MAIL_URL")
    if not url:
        pytest.fail("VRUM_TEST_MAIL_URL is not set. Run the integration tests through ./check.sh.")
    return url


def public() -> str:
    """The public address the check stack's links are built from (compose.check.yaml)."""
    return server_base().replace("127.0.0.1", "localhost")


def sent_to(address: str, subject: str = "") -> list[dict[str, Any]]:
    """What the mail server was sent for this address, newest first, whose subject starts so."""
    with urllib.request.urlopen(f"{mail_url()}/messages?to={quote(address)}", timeout=10) as reply:
        found: list[dict[str, Any]] = json.loads(reply.read())
    return [one for one in found if one["subject"].startswith(subject)]


def wait_for_mail(address: str, subject: str = "", count: int = 1) -> list[dict[str, Any]]:
    """Wait for the messages to arrive (they are sent after the answer has gone)."""
    deadline = time.monotonic() + 20
    while True:
        found = sent_to(address, subject)
        if len(found) >= count:
            return found
        if time.monotonic() > deadline:
            pytest.fail(f"Only {len(found)} of {count} messages arrived ({subject!r}).")
        time.sleep(0.2)


def link_of(message: dict[str, Any]) -> str:
    links: list[str] = re.findall(r"https?://\S+", message["text"])
    assert len(links) == 1, f"a message holds one link, and this holds {links}"
    return links[0]


def token_of(link: str) -> str:
    return link.rsplit("/", 1)[1]


def path_of(link: str) -> str:
    return urlsplit(link).path


def fresh_link(address: str, subject: str, seen: set[str]) -> str:
    """The link of a message for this address that has not been seen yet."""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        for message in sent_to(address, subject):
            token = token_of(link_of(message))
            if token not in seen:
                seen.add(token)
                return link_of(message)
        time.sleep(0.2)
    pytest.fail(f"No new {subject!r} message arrived.")


def the_address() -> str:
    return f"{unique_word()}@example.test"


# -- the board's own requests --------------------------------------------------------------------


def invite_by_email(owner: Person, project: Project, address: str, role: str = "editor") -> Reply:
    return owner.call(
        "POST", f"/board/api/projects/{project.id}/invites", {"role": role, "email": address}
    )


def pending(owner: Person, project: Project) -> list[dict[str, Any]]:
    reply = owner.call("GET", f"/board/api/projects/{project.id}/invites")
    assert reply.status == 200, reply.body
    return list(reply.body["invites"])


def ask_for_reset(address: str, **more: Any) -> Reply:
    return api(server_base(), "POST", "/board/api/password-reset", {"email": address}, **more)


def reset_with(token: str, new: str) -> Reply:
    return api(server_base(), "POST", f"/board/api/password-reset/{token}", {"new": new})


def reset_link_works(token: str) -> Reply:
    return api(server_base(), "GET", f"/board/api/password-reset/{token}")


def verify_with(token: str, **more: Any) -> Reply:
    return api(server_base(), "POST", f"/board/api/verify/{token}", {}, **more)


def is_verified(person: Person) -> bool:
    reply = person.call("GET", "/board/api/session")
    assert reply.status == 200 and reply.body["account"] is not None, reply.body
    return bool(reply.body["account"]["email_verified"])


def get_page(path: str) -> tuple[int, str, Any]:
    try:
        with urllib.request.urlopen(server_base() + path, timeout=30) as reply:
            return reply.status, reply.read().decode(), reply.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(), error.headers


def sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sql(statement: str) -> str:
    done = run_sql(statement, plain=True, project=None)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def expire(token: str) -> None:
    sql(
        "UPDATE email_tokens SET created_at = now() - interval '3 hours', "
        f"expires_at = now() - interval '2 hours' WHERE token_hash = '{sha(token)}'"
    )


# -- whether this server sends email, and the pages the links open ------------------------------


def test_the_board_says_whether_the_server_sends_email_and_never_how_it_signs_in() -> None:
    base = server_base()
    signed_out = api(base, "GET", "/board/api/session").body
    assert (signed_out["account"], signed_out["csrf"], signed_out["mail"]) == (None, None, True)
    # What the sign-in page may offer, and nothing of how the server reaches its mail server.
    assert set(signed_out) == {"account", "csrf", "mail", "providers", "sso_create_accounts"}
    assert "scripted-mail" not in json.dumps(signed_out) and FROM not in json.dumps(signed_out)
    person = register()
    me = person.call("GET", "/board/api/session")
    assert me.body["mail"] is True
    assert me.body["account"] == {
        "id": person.id,
        "email": person.email,
        "name": person.name,
        "email_verified": False,
        "has_password": True,
    }
    signed = api(
        base, "POST", "/board/api/session", {"email": person.email, "password": person.password}
    )
    assert signed.status == 200 and signed.body["mail"] is True
    assert signed.body["account"]["email_verified"] is False
    info = person.call("GET", "/board/api/info")
    assert info.body["mail"] == {"enabled": True, "from": FROM, "host": "scripted-mail"}
    assert "password" not in json.dumps(info.body).lower()


@pytest.mark.parametrize("kind", ["invite/vri_", "reset/vrr_", "verify/vrv_"])
def test_the_address_in_a_link_opens_the_board_s_page_with_its_usual_headers(kind: str) -> None:
    status, page, headers = get_page(f"/board/{kind}{unique_word() * 4}")
    assert status == 200 and "text/html" in headers["Content-Type"]
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "no-referrer" and "no-cache" in headers["Cache-Control"]
    assert "<script" in page
    assert get_page(f"/board/{kind}a/b")[0] == 404


# -- invitations by email ------------------------------------------------------------------------


def test_an_invitation_is_emailed_and_opening_its_link_does_not_use_it() -> None:
    owner = register(name=f"Olga Owner {unique_word()}")
    project = make_project(owner, f"Riverside {unique_word()}")
    address = the_address()

    reply = invite_by_email(owner, project, address)

    assert reply.status == 201, reply.body
    assert set(reply.body) == {"invite", "token", "emailed"} and reply.body["emailed"] is True
    invite, token = reply.body["invite"], reply.body["token"]
    assert invite["sent_to"] == address and invite["sent_at"] is not None
    assert token.startswith("vri_")
    (message,) = wait_for_mail(address)
    assert message["from"] == FROM and message["to"] == address
    assert message["subject"] == f"You are invited to {project.name} on Vrum"
    text = message["text"]
    assert f'{owner.name} invited you to the project "{project.name}" on Vrum, as an editor' in text
    assert "expires in 7 days" in text and "Nothing happens until the link is used." in text
    assert link_of(message) == f"{public()}/board/invite/{token}"
    assert not any(dash in message["subject"] + text for dash in DASHES)
    # No other personal data: not the owner's address, and not even the recipient's own.
    assert owner.email not in text and address not in text

    listed = {one["id"]: one for one in pending(owner, project)}
    assert listed[invite["id"]]["sent_to"] == address and listed[invite["id"]]["sent_at"]
    assert token not in json.dumps(listed)
    # Opening the link, as a browser or a mail scanner does, uses nothing up.
    status, _, _ = get_page(path_of(link_of(message)))
    assert status == 200
    stranger = register()
    assert stranger.call("GET", f"/board/api/invites/{token}").status == 200
    assert [one["id"] for one in pending(owner, project)] == [invite["id"]]
    # Kept only as a hash.
    kept = sql(
        f"SELECT token_hash = '{sha(token)}' FROM project_invites WHERE sent_to = '{address}'"
    )
    assert kept == "t"


def test_an_invitation_with_an_address_is_for_that_address_only_and_works_once() -> None:
    owner = register()
    project = make_project(owner)
    address = the_address()
    reply = invite_by_email(owner, project, address, "viewer")
    token = reply.body["token"]
    link = f"/board/api/invites/{token}"
    masked = f"{address[0]}***@example.test"
    refusal = (
        f"This invitation was sent to {masked}, so only an account with that address can accept "
        "it. Sign in with that address, or register with it. Nothing was changed."
    )

    stranger = register()
    preview = stranger.call("GET", link)
    assert preview.status == 200 and preview.body["invite"]["problem"] == refusal
    refused = stranger.call("POST", f"{link}/accept")
    assert (refused.status, refused.body["message"]) == (409, refusal)
    assert address not in refusal
    assert [one["id"] for one in pending(owner, project)] == [reply.body["invite"]["id"]]

    # The account with that address (however it is written) joins, and the link was proof enough
    # that the address is theirs.
    guest = register_as(address.upper())
    assert is_verified(guest) is False
    assert guest.call("GET", link).body["invite"]["problem"] is None
    accepted = guest.call("POST", f"{link}/accept")
    assert accepted.status == 200 and accepted.body["project"]["role"] == "viewer"
    assert is_verified(guest) is True

    used = stranger.call("POST", f"{link}/accept")
    assert (used.status, used.body["message"]) == (404, INVITE_NOT_VALID)
    assert pending(owner, project) == []


def test_an_invitation_the_mail_server_refused_still_stands_and_says_why_in_plain_words() -> None:
    owner = register()
    project = make_project(owner)
    bad = f"{unique_word()}@reject.example"

    reply = invite_by_email(owner, project, bad)

    assert reply.status == 201 and reply.body["emailed"] is False
    problem = reply.body["email_problem"]
    assert problem.startswith("The mail server did not accept the message: 550 5.1.1")
    assert bad not in problem and "mailer" not in problem.lower()
    assert reply.body["invite"]["sent_to"] == bad and reply.body["invite"]["sent_at"] is None
    assert sent_to(bad, "You are invited") == []
    assert [one["id"] for one in pending(owner, project)] == [reply.body["invite"]["id"]]

    # Sending again, which also makes a new link, fails the same way and says so the same way.
    again = owner.call(
        "POST", f"/board/api/projects/{project.id}/invites/{reply.body['invite']['id']}/resend"
    )
    assert again.status == 200 and again.body["emailed"] is False
    assert again.body["email_problem"] == problem and again.body["token"] != reply.body["token"]

    # The link is still good for the account with that address, whose own registration worked
    # although its confirmation email was refused too, and nothing confirmed it.
    guest = register_as(bad)
    old = guest.call("POST", f"/board/api/invites/{reply.body['token']}/accept")
    assert (old.status, old.body["message"]) == (404, INVITE_NOT_VALID)
    accepted = guest.call("POST", f"/board/api/invites/{again.body['token']}/accept")
    assert accepted.status == 200
    assert is_verified(guest) is False


def test_sending_an_invitation_again_makes_a_new_link_and_the_old_one_stops() -> None:
    owner = register()
    project = make_project(owner)
    address = the_address()
    first = invite_by_email(owner, project, address)
    (original,) = wait_for_mail(address, "You are invited")

    again = owner.call(
        "POST", f"/board/api/projects/{project.id}/invites/{first.body['invite']['id']}/resend"
    )

    assert again.status == 200 and again.body["emailed"] is True
    assert set(again.body) == {"invite", "token", "emailed"}
    assert again.body["invite"]["id"] == first.body["invite"]["id"]
    assert again.body["token"] != first.body["token"] and again.body["invite"]["sent_at"]
    newest, *_ = wait_for_mail(address, "You are invited", count=2)
    assert link_of(newest) == f"{public()}/board/invite/{again.body['token']}"
    assert link_of(original) == f"{public()}/board/invite/{first.body['token']}"
    guest = register_as(address)
    dead = guest.call("GET", f"/board/api/invites/{first.body['token']}")
    assert (dead.status, dead.body["message"]) == (404, INVITE_NOT_VALID)
    assert guest.call("GET", f"/board/api/invites/{again.body['token']}").status == 200
    assert guest.call("POST", f"/board/api/invites/{again.body['token']}/accept").status == 200


def test_only_the_owner_sends_again_an_invitation_that_has_an_address_and_is_pending() -> None:
    owner = register()
    project = make_project(owner)
    plain = owner.call("POST", f"/board/api/projects/{project.id}/invites", {"role": "viewer"})
    assert plain.status == 201 and plain.body["emailed"] is False
    assert "email_problem" not in plain.body and plain.body["invite"]["sent_to"] is None
    where = f"/board/api/projects/{project.id}/invites"

    none = owner.call("POST", f"{where}/{plain.body['invite']['id']}/resend")
    assert none.status == 400 and "has no email address" in none.body["message"]
    nothing = owner.call("POST", f"{where}/999999/resend")
    assert (nothing.status, nothing.body["message"]) == (
        404,
        "There is no such invitation for this project.",
    )
    other = make_project(register())
    elsewhere = other.owner.call(
        "POST", f"/board/api/projects/{other.id}/invites/{plain.body['invite']['id']}/resend"
    )
    assert elsewhere.status == 404

    editor = join_project(project, "editor")
    refused = editor.call("POST", f"{where}/{plain.body['invite']['id']}/resend")
    assert refused.status == 403 and "Only the owner of this project" in refused.body["message"]

    address = the_address()
    made = invite_by_email(owner, project, address)
    register_as(address).call("POST", f"/board/api/invites/{made.body['token']}/accept")
    used = owner.call("POST", f"{where}/{made.body['invite']['id']}/resend")
    assert used.status == 409 and "already used" in used.body["message"]
    assert owner.call("DELETE", f"{where}/{plain.body['invite']['id']}").status == 200
    assert owner.call("POST", f"{where}/{plain.body['invite']['id']}/resend").status == 404


def test_an_address_that_is_not_one_mailbox_is_refused_and_nothing_is_made() -> None:
    owner = register()
    project = make_project(owner)
    where = f"/board/api/projects/{project.id}/invites"
    for typed, said in (
        ("not an address", "That does not look like an email address"),
        ("a@b.co, d@e.fg", "That does not look like an email address"),
        ("a,b@c.de", "That address cannot be sent to"),
        ('"a"@c.de', "That address cannot be sent to"),
    ):
        reply = owner.call("POST", where, {"role": "editor", "email": typed})
        assert reply.status == 400 and said in reply.body["message"], (typed, reply.body)
    number = owner.call("POST", where, {"role": "editor", "email": 5})
    assert number.status == 400 and "has to be text" in number.body["message"]
    assert pending(owner, project) == []

    # A blank address is no address: an invitation like any other, to share by link.
    for blank in ("", "   ", None):
        reply = owner.call("POST", where, {"role": "editor", "email": blank})
        assert reply.status == 201 and reply.body["emailed"] is False
        assert reply.body["invite"]["sent_to"] is None
    assert len(pending(owner, project)) == 3


def test_an_owner_can_email_twenty_invitations_an_hour_and_the_rest_are_still_made() -> None:
    owner = register()
    project = make_project(owner)
    first = invite_by_email(owner, project, the_address())
    assert first.body["emailed"] is True
    for _ in range(19):
        assert invite_by_email(owner, project, the_address()).body["emailed"] is True

    over = invite_by_email(owner, project, the_address())

    assert over.status == 201 and over.body["emailed"] is False
    assert (
        "Too many invitation emails were sent lately, so this one was not sent"
        in (over.body["email_problem"])
    )
    assert "copy the link" in over.body["email_problem"]
    assert len(pending(owner, project)) == 21
    again = owner.call(
        "POST", f"/board/api/projects/{project.id}/invites/{over.body['invite']['id']}/resend"
    )
    assert again.status == 429 and "Too many invitation emails" in again.body["message"]
    # Nothing changed: the link that was made is still the one that works.
    guest = register_as(over.body["invite"]["sent_to"])
    assert guest.call("GET", f"/board/api/invites/{over.body['token']}").status == 200


# -- confirming an address -----------------------------------------------------------------------


def test_registering_sends_a_link_that_confirms_the_address_once() -> None:
    address = the_address()
    person = register_as(address)
    assert is_verified(person) is False
    (message,) = wait_for_mail(address, "Confirm your email address for Vrum")
    link = link_of(message)
    token = token_of(link)
    assert message["from"] == FROM and link == f"{public()}/board/verify/{token}"
    assert token.startswith("vrv_") and "expires in 24 hours" in message["text"]
    assert not any(dash in message["text"] for dash in DASHES)
    assert (
        sql(f"SELECT token_hash = '{sha(token)}' FROM email_tokens WHERE sent_to = '{address}'")
        == "t"
    )

    # What a browser or a mail scanner does with the address uses nothing up.
    assert get_page(path_of(link))[0] == 200
    assert get_page(f"/board/api/verify/{token}")[0] in (404, 405)
    assert is_verified(person) is False
    crossed = verify_with(token, origin="https://evil.example")
    assert crossed.status == 403 and is_verified(person) is False

    done = verify_with(token)
    assert (done.status, done.body) == (200, {"ok": True, "email": address})
    assert is_verified(person) is True
    # A link works once, and a wrong, long or differently shaped one is refused in the same words.
    for wrong in (token, "vrv_" + unique_word() * 5, "vrr_" + token[4:], token + "x" * 300, "x"):
        refused = verify_with(wrong)
        assert (refused.status, refused.body["message"]) == (400, VERIFY_LINK_NOT_VALID), wrong


def test_a_confirmation_can_be_asked_for_three_times_an_hour_and_expires() -> None:
    address = the_address()
    person = register_as(address)
    seen: set[str] = set()
    registration = fresh_link(address, "Confirm your email", seen)
    where = "/board/api/account/verify-email"

    assert api(person.base, "POST", where, {}).status == 401
    no_secret = api(person.base, "POST", where, {}, cookie=person.cookie)
    assert no_secret.status == 403
    for _ in range(3):
        sent = person.call("POST", where)
        assert sent.status == 202, sent.body
        assert sent.body == {
            "message": f"A link to confirm {address} has been sent to it. It works once, for 24 "
            "hours."
        }
    over = person.call("POST", where)
    assert over.status == 429
    assert over.body["message"].startswith(
        "Too many confirmation emails were asked for lately, so nothing was sent. Wait about"
    )
    assert len(wait_for_mail(address, "Confirm your email", count=4)) == 4

    expired = fresh_link(address, "Confirm your email", seen)
    expire(token_of(expired))
    gone = verify_with(token_of(expired))
    assert (gone.status, gone.body["message"]) == (400, VERIFY_LINK_NOT_VALID)
    assert is_verified(person) is False

    # Confirming with one spends the person's other confirmation links too.
    assert verify_with(token_of(registration)).status == 200
    assert is_verified(person) is True
    for message in sent_to(address, "Confirm"):
        assert verify_with(token_of(link_of(message))).status == 400
    already = person.call("POST", where)
    assert (already.status, already.body["message"]) == (
        409,
        "This address is already confirmed, so nothing was sent.",
    )


# -- choosing a new password ---------------------------------------------------------------------


def mcp_status(key: str) -> int:
    """What the server says to a request that carries this agent key (anything but 401 is a
    server that let the key in)."""
    request = urllib.request.Request(
        f"{server_base()}/mcp",
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as reply:
            return int(reply.status)
    except urllib.error.HTTPError as error:
        return error.code


def test_a_password_is_reset_by_a_link_that_works_once_and_ends_every_session() -> None:
    base = server_base()
    person = register()
    elsewhere = person.sign_in_again()
    project = make_project(person)
    unknown = the_address()
    seen: set[str] = set()

    # The answer is the same, word for word, whether or not the address has an account.
    nobody = ask_for_reset(unknown)
    somebody = ask_for_reset(person.email)
    assert (nobody.status, nobody.body) == (202, {"message": RESET_REQUESTED})
    assert (somebody.status, somebody.body) == (nobody.status, nobody.body)
    assert RESET_REQUESTED == (
        "If that address has an account, a link to choose a new password has been sent to it. "
        "The link works once for one hour."
    )
    (message,) = wait_for_mail(person.email, "Reset your Vrum password")
    assert message["from"] == FROM and "expires in 1 hour." in message["text"]
    assert not any(dash in message["text"] for dash in DASHES)
    link = fresh_link(person.email, "Reset your Vrum password", seen)
    token = token_of(link)
    assert link == f"{public()}/board/reset/{token}" and token.startswith("vrr_")
    assert sent_to(unknown) == []

    # Looking is not using: the page, and the check the page makes first, spend nothing.
    assert get_page(path_of(link))[0] == 200
    assert reset_link_works(token).body == {"ok": True} and reset_link_works(token).status == 200
    short = reset_with(token, "short")
    assert short.status == 400 and "10 to 200 characters" in short.body["message"]
    assert reset_link_works(token).status == 200
    assert person.call("GET", "/board/api/projects").status == 200
    assert elsewhere.call("GET", "/board/api/projects").status == 200

    new = f"a brand new password {unique_word()}"
    done = reset_with(token, new)
    assert (done.status, done.body) == (200, {"ok": True}) and done.cookie is None
    # Every session of the account is over, the old password is not one any more, the new one is,
    # and the link reached the mailbox, so the address is confirmed.
    assert person.call("GET", "/board/api/projects").status == 401
    assert elsewhere.call("GET", "/board/api/projects").status == 401
    assert person.call("GET", "/board/api/session").body["account"] is None
    old = api(
        base, "POST", "/board/api/session", {"email": person.email, "password": person.password}
    )
    assert old.status == 401
    signed = api(base, "POST", "/board/api/session", {"email": person.email, "password": new})
    assert signed.status == 200 and signed.body["account"]["email_verified"] is True
    # The link worked once.
    used = reset_with(token, "yet another password 1")
    assert (used.status, used.body["message"]) == (400, RESET_LINK_NOT_VALID)
    assert reset_link_works(token).body["message"] == RESET_LINK_NOT_VALID
    # An agent key is not a session: the key is as it was and still lets its agent in.
    back = Person(base, person.email, new, person.name, signed.cookie or "", "", person.id)
    back.csrf = signed.body["csrf"]
    keys = back.call("GET", f"/board/api/projects/{project.id}/keys")
    assert keys.status == 200 and keys.body["keys"][0]["revoked_at"] is None
    assert mcp_status(project.key) != 401

    # Two links outstanding: using one spends the other.
    ask_for_reset(person.email)
    ask_for_reset(person.email)
    second = fresh_link(person.email, "Reset your Vrum password", seen)
    third = fresh_link(person.email, "Reset your Vrum password", seen)
    assert reset_with(token_of(third), f"{new} again").status == 200
    spent = reset_with(token_of(second), "a different password 1")
    assert (spent.status, spent.body["message"]) == (400, RESET_LINK_NOT_VALID)

    # An expired link, a wrong one, and one of another kind are refused in the very same words.
    ask_for_reset(person.email)
    last = fresh_link(person.email, "Reset your Vrum password", seen)
    expire(token_of(last))
    refusals = [
        reset_with(wrong, "a valid new password")
        for wrong in (token_of(last), "vrr_" + unique_word() * 5, "vri_" + token[4:], "x")
    ]
    assert [(one.status, one.body) for one in refusals] == [
        (400, {"message": RESET_LINK_NOT_VALID})
    ] * 4
    assert reset_link_works(token_of(last)).body == {"message": RESET_LINK_NOT_VALID}


def test_asking_for_a_reset_does_not_wait_for_the_mail_server_for_any_address() -> None:
    person = register()
    unknown = the_address()
    # A paused mail server takes a connection and never answers, so sending in the request would
    # take the whole ten seconds the server waits. The answer comes at once, for both.
    assert compose("pause", "scripted-mail").returncode == 0
    try:
        took = {}
        for name, address in (("known", person.email), ("unknown", unknown)):
            started = time.monotonic()
            reply = ask_for_reset(address)
            took[name] = time.monotonic() - started
            assert (reply.status, reply.body) == (202, {"message": RESET_REQUESTED})
    finally:
        assert compose("unpause", "scripted-mail").returncode == 0
    assert max(took.values()) < 3, took


def test_a_reset_is_sent_five_times_an_hour_to_one_address_and_the_sixth_looks_the_same() -> None:
    person = register()
    replies = [ask_for_reset(person.email) for _ in range(6)]
    assert [(one.status, one.body) for one in replies] == [(202, {"message": RESET_REQUESTED})] * 6
    wait_for_mail(person.email, "Reset your Vrum password", count=5)
    time.sleep(2)
    assert len(sent_to(person.email, "Reset your Vrum password")) == 5


def test_a_request_for_a_reset_has_to_come_from_the_board_as_json_and_name_an_address() -> None:
    base = server_base()
    crossed = api(
        base, "POST", "/board/api/password-reset", {"email": "a@b.co"}, origin="https://x.test"
    )
    assert crossed.status == 403
    plain = urllib.request.Request(
        f"{base}/board/api/password-reset",
        data=b"email=a@b.co",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": base},
    )
    with pytest.raises(urllib.error.HTTPError) as form:
        urllib.request.urlopen(plain, timeout=30)
    assert form.value.code == 415
    for typed in ({}, {"email": ""}, {"email": "not an address"}, {"email": 5}):
        reply = api(base, "POST", "/board/api/password-reset", typed)
        assert reply.status == 400 and "email address" in reply.body["message"], typed


# -- where links come from, what is kept and what is logged -------------------------------------


def test_a_link_is_built_from_the_public_address_and_never_from_the_request() -> None:
    forged = {
        "Host": "evil.example",
        "X-Forwarded-Host": "evil.example",
        "X-Forwarded-Proto": "https",
        "Forwarded": "host=evil.example;proto=https",
    }
    person = register()
    reply = ask_for_reset(person.email, origin=None, extra=forged)
    assert reply.status == 202, reply.body
    (reset,) = wait_for_mail(person.email, "Reset your Vrum password")
    assert link_of(reset).startswith(f"{public()}/board/reset/vrr_")

    owner = register()
    project = make_project(owner)
    address = the_address()
    made = owner.call(
        "POST",
        f"/board/api/projects/{project.id}/invites",
        {"role": "editor", "email": address},
        origin=None,
        extra=forged,
    )
    assert made.status == 201 and made.body["emailed"] is True
    (invitation,) = wait_for_mail(address, "You are invited")
    assert link_of(invitation).startswith(f"{public()}/board/invite/vri_")
    for message in (reset, invitation):
        assert "evil" not in message["text"] and "https://" not in message["text"]


def test_a_token_is_kept_only_as_a_hash_and_a_project_s_own_role_cannot_reach_the_table() -> None:
    address = the_address()
    register_as(address)
    (message,) = wait_for_mail(address, "Confirm your email")
    token = token_of(link_of(message))
    row = sql(
        "SELECT kind || ',' || (token_hash = '" + sha(token) + "') || ',' || sent_to || ',' || "
        f"(used_at IS NULL) FROM email_tokens WHERE sent_to = '{address}'"
    )
    assert row == f"verify,true,{address},true"
    assert token not in sql("SELECT string_agg(t::text, ' ') FROM email_tokens t")
    denied = run_sql("SELECT count(*) FROM email_tokens", plain=True, as_project=True)
    assert denied.returncode != 0 and "permission denied" in denied.stderr


def test_nothing_the_server_logs_holds_an_address_a_link_or_a_token() -> None:
    owner = register()
    project = make_project(owner)
    good, bad = the_address(), f"{unique_word()}@reject.example"
    invited = invite_by_email(owner, project, good)
    refused = invite_by_email(owner, project, bad)
    person = register()
    ask_for_reset(person.email)
    (reset,) = wait_for_mail(person.email, "Reset your Vrum password")
    (confirm,) = wait_for_mail(person.email, "Confirm your email")
    wait_for_mail(good, "You are invited")
    private = [
        good,
        bad,
        person.email,
        owner.email,
        invited.body["token"],
        refused.body["token"],
        token_of(link_of(reset)),
        token_of(link_of(confirm)),
        link_of(reset),
    ]

    logs = compose("logs", "--no-log-prefix", "--no-color", "app").stdout

    assert logs
    for something in private:
        assert something not in logs
    assert not re.search(r"vr[irvks]_[A-Za-z0-9_-]{30,}", logs)
    assert not re.search(r"@(example\.test|reject\.example)", logs)
    lines = [json.loads(line) for line in logs.splitlines() if line.startswith("{")]
    mail = [line for line in lines if line["event"] in ("mail_sent", "mail_failed")]
    assert {(line["event"], line["kind"]) for line in mail} >= {
        ("mail_sent", "invitation"),
        ("mail_failed", "invitation"),
        ("mail_sent", "password_reset"),
        ("mail_sent", "verification"),
    }
    for line in mail:
        assert set(line) <= {"time", "level", "event", "kind", "reason"}, line
