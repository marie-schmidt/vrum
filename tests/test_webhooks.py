"""M48, with no containers: signing as the Standard Webhooks specification has it, which addresses a
webhook may go to, what a call does when the receiver misbehaves, and what counts as news."""

import http.server
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest

from vrum import webhook_signing
from vrum.config import ConfigError, WebhookSettings
from vrum.notify_events import COALESCED, EVENT_TYPES, NUDGE_TYPES, object_of, types_of
from vrum.webhook_address import (
    AddressRefused,
    DeliveryFailed,
    Target,
    parse,
    post,
    public_addresses,
    why_not,
)
from vrum.webhooks import MOST_ATTEMPTS, RETRY_DELAYS, clean_events

NO_EXTRAS = WebhookSettings()
PUBLIC = "93.184.216.34"


def gives(*found: str) -> Callable[[str, int], list[str]]:
    """A name lookup that always gives these addresses."""
    return lambda host, port: list(found)


# -- signing -------------------------------------------------------------------------------------


def test_a_signature_is_the_one_the_standard_webhooks_specification_publishes() -> None:
    secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    wanted = "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="
    given = webhook_signing.signature(
        secret, "msg_p5jXN8AQM9LWM0D4loKWxJek", 1614265330, '{"test": 2432232314}'
    )
    assert given == wanted


def test_a_new_secret_has_the_shape_the_database_requires() -> None:
    import re

    secret = webhook_signing.new_secret()
    assert re.fullmatch(r"whsec_[A-Za-z0-9+/]{43}=", secret)
    assert secret != webhook_signing.new_secret()


def test_a_message_checks_out_with_any_secret_in_force_and_not_with_anything_else() -> None:
    old, new, other = (webhook_signing.new_secret() for _ in range(3))
    body = '{"type":"ask.raised"}'
    now = 1_700_000_000
    sent = webhook_signing.headers([new, old], "msg_1", now, body)
    assert set(sent) == {"webhook-id", "webhook-timestamp", "webhook-signature"}
    assert len(sent["webhook-signature"].split()) == 2
    for secret in (old, new):
        assert webhook_signing.problem(secret, sent, body, now=now) is None
    assert webhook_signing.problem(other, sent, body, now=now) == "no signature fits"
    assert webhook_signing.problem(new, sent, body + " ", now=now) == "no signature fits"
    assert "too far" in (webhook_signing.problem(new, sent, body, now=now + 301) or "")
    assert webhook_signing.problem(new, sent, body, now=now + 300) is None
    assert webhook_signing.problem(new, {}, body, now=now) == "a header is missing"
    bad = {**sent, "webhook-timestamp": "yesterday"}
    assert "not a number" in (webhook_signing.problem(new, bad, body, now=now) or "")


# -- the address ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "words"),
    [
        ("http://hooks.example.com/x", "https://"),
        ("ftp://hooks.example.com/x", "https://"),
        ("https://user:pass@hooks.example.com/x", "user name or a password"),
        ("https://hooks.example.com/x#frag", "#fragment"),
        ("https://hooks.example.com/a b", "spaces"),
        ("https:///x", "host name"),
        ("https://hooks.example.com:0/x", "port"),
        ("https://" + "a" * 600 + ".example.com/", "at most 500"),
        ("", "written out"),
        ("not a url", "spaces"),
        ("https://hooks.example.com:99999/x", "web address"),
    ],
)
def test_an_address_that_is_not_https_with_a_host_is_refused_in_words(url: str, words: str) -> None:
    with pytest.raises(AddressRefused) as refused:
        parse(url, NO_EXTRAS)
    assert words in str(refused.value)
    assert url[:30] not in str(refused.value) or not url.strip()


def test_an_address_is_split_into_what_the_call_needs() -> None:
    assert parse("https://Hooks.Example.com/a/b?x=1", NO_EXTRAS) == Target(
        tls=True, host="hooks.example.com", port=443, path="/a/b?x=1"
    )
    assert parse("https://hooks.example.com:8443", NO_EXTRAS) == Target(
        True, "hooks.example.com", 8443, "/"
    )
    plain = WebhookSettings(allow_insecure_http=True)
    assert parse("http://receiver:8030/hook/ok", plain) == Target(
        False, "receiver", 8030, "/hook/ok"
    )


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.1",
        "169.254.169.254",
        "169.254.0.1",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "::",
        "fc00::1",
        "fd00:ec2::254",
        "fe80::1",
        "::ffff:10.0.0.1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
    ],
)
def test_an_address_that_is_not_a_public_one_is_refused(address: str) -> None:
    assert why_not(address) is not None
    target = Target(True, "hooks.example.com", 443, "/")
    with pytest.raises(AddressRefused) as refused:
        public_addresses(target, NO_EXTRAS, lambda host, port: [address])
    assert "public address" in str(refused.value)
    assert address not in str(refused.value)


@pytest.mark.parametrize("address", [PUBLIC, "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_a_public_address_is_let_through(address: str) -> None:
    target = Target(True, "hooks.example.com", 443, "/")
    assert public_addresses(target, NO_EXTRAS, lambda host, port: [address]) == [address]


def test_one_bad_address_among_good_ones_refuses_the_name() -> None:
    target = Target(True, "hooks.example.com", 443, "/")
    with pytest.raises(AddressRefused):
        public_addresses(target, NO_EXTRAS, lambda host, port: [PUBLIC, "10.0.0.9"])


def test_a_name_that_resolves_to_nothing_is_refused_and_an_address_is_not_looked_up() -> None:
    target = Target(True, "nowhere.example.com", 443, "/")
    with pytest.raises(AddressRefused) as refused:
        public_addresses(target, NO_EXTRAS, lambda host, port: [])
    assert "does not resolve" in str(refused.value)
    literal = Target(True, "127.0.0.1", 443, "/")
    calls: list[str] = []

    def asked(host: str, port: int) -> list[str]:
        calls.append(host)
        return []

    with pytest.raises(AddressRefused):
        public_addresses(literal, NO_EXTRAS, asked)
    assert calls == []


def test_the_person_who_runs_the_server_can_open_a_name_an_address_or_a_network() -> None:
    internal = Target(True, "hooks.internal.example", 443, "/")
    by_name = WebhookSettings(allowed_hosts=("hooks.internal.example",))
    assert public_addresses(internal, by_name, gives("10.1.2.3")) == ["10.1.2.3"]
    by_network = WebhookSettings(allowed_hosts=("10.0.0.0/8",))
    assert public_addresses(internal, by_network, gives("10.1.2.3")) == ["10.1.2.3"]
    # Opening one network opens no other, and not loopback or the metadata address.
    for closed in ("192.168.0.1", "127.0.0.1", "169.254.169.254"):
        with pytest.raises(AddressRefused):
            public_addresses(internal, by_network, gives(closed))
    single = WebhookSettings(allowed_hosts=("192.168.1.5",))
    assert public_addresses(internal, single, gives("192.168.1.5")) == ["192.168.1.5"]


def test_the_allowed_hosts_setting_is_read_and_a_bad_entry_is_refused() -> None:
    read = WebhookSettings.from_env(
        {
            "VRUM_WEBHOOK_ALLOWED_HOSTS": " Hooks.Internal.example , 10.0.0.0/8 ,",
            "VRUM_WEBHOOK_ALLOW_INSECURE_HTTP": "true",
        }
    )
    assert read.allowed_hosts == ("hooks.internal.example", "10.0.0.0/8")
    assert read.allow_insecure_http is True
    assert WebhookSettings.from_env({}) == WebhookSettings()
    with pytest.raises(ConfigError):
        WebhookSettings.from_env({"VRUM_WEBHOOK_ALLOWED_HOSTS": "a b"})
    with pytest.raises(ConfigError):
        WebhookSettings.from_env({"VRUM_WEBHOOK_ALLOW_INSECURE_HTTP": "maybe"})


# -- a call --------------------------------------------------------------------------------------


@contextmanager
def receiver(mode: str = "ok") -> Iterator[tuple[int, list[dict[str, str]]]]:
    seen: list[dict[str, str]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            seen.append(
                {"path": self.path, "body": body, **{k.lower(): v for k, v in self.headers.items()}}
            )
            if self.path.startswith("/after"):
                self.send_response(200)
            elif mode == "fail":
                self.send_response(500)
            elif mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "/after")
            elif mode == "slow":
                time.sleep(2)
                self.send_response(200)
            else:
                self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], seen
    finally:
        server.shutdown()
        server.server_close()


LOCAL = WebhookSettings(allowed_hosts=("127.0.0.1",), allow_insecure_http=True)


def to(port: int, path: str = "/hook") -> Target:
    return Target(tls=False, host="127.0.0.1", port=port, path=path)


def test_a_message_is_posted_with_its_headers_and_a_2xx_is_a_delivery() -> None:
    secret = webhook_signing.new_secret()
    body = '{"type":"ask.raised","data":{"node":"task:4"}}'
    sent = webhook_signing.headers([secret], "msg_abc", int(time.time()), body)
    with receiver() as (port, seen):
        assert post(to(port), LOCAL, sent, body) == 200
    [got] = seen
    assert got["body"] == body and got["content-type"] == "application/json"
    assert webhook_signing.problem(secret, got, body, now=int(time.time())) is None
    assert got["webhook-id"] == "msg_abc"


def test_a_failing_receiver_and_a_redirect_are_failures_and_a_redirect_is_not_followed() -> None:
    with receiver("fail") as (port, seen):
        with pytest.raises(DeliveryFailed) as failed:
            post(to(port), LOCAL, {}, "{}")
        assert failed.value.status == 500 and "answered 500" in failed.value.words
    with receiver("redirect") as (port, seen):
        with pytest.raises(DeliveryFailed) as redirected:
            post(to(port), LOCAL, {}, "{}")
        assert redirected.value.status == 302 and "not followed" in redirected.value.words
        assert [one["path"] for one in seen] == ["/hook"], "the redirect was followed"


def test_a_slow_receiver_and_one_that_is_not_there_are_failures_in_words() -> None:
    with receiver("slow") as (port, _):
        with pytest.raises(DeliveryFailed) as slow:
            post(to(port), LOCAL, {}, "{}", timeout=0.5)
        assert "in time" in slow.value.words
    with pytest.raises(DeliveryFailed) as gone:
        post(to(1), LOCAL, {}, "{}")
    assert "could not be reached" in gone.value.words
    assert "127.0.0.1" not in gone.value.words


def test_a_call_to_an_address_that_is_not_allowed_never_connects_and_is_checked_again() -> None:
    with receiver() as (port, seen):
        with pytest.raises(DeliveryFailed) as refused:
            post(to(port), WebhookSettings(allow_insecure_http=True), {}, "{}")
        assert "public address" in refused.value.words and seen == []
    # The name is looked up once, and the call goes to the address that was checked.
    asked: list[str] = []

    def resolver(host: str, port: int) -> list[str]:
        asked.append(host)
        return ["127.0.0.1"]

    with receiver() as (port, seen):
        named = Target(tls=False, host="hooks.example.test", port=port, path="/hook")
        assert post(named, LOCAL, {}, "{}", resolver=resolver) == 200
        assert asked == ["hooks.example.test"] and seen[0]["host"].startswith("hooks.example.test")


# -- news ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "before", "after", "expected"),
    [
        ("ask_raised", None, {"question": "q"}, ["ask.raised"]),
        ("ask_answered", None, {"ask": 4}, ["ask.answered", "work.available"]),
        ("plan_proposed", None, {"title": "t"}, ["plan.proposed"]),
        ("plan_approved", None, {"plan": 3}, ["plan.decided", "work.available"]),
        ("plan_rejected", None, {"plan": 3}, ["plan.decided", "work.available"]),
        ("placement_proposed", None, {"items": []}, ["placement.proposed"]),
        ("placement_accepted", None, {"placement": 9}, ["placement.decided"]),
        ("update", {"status": "in_progress"}, {"status": "blocked"}, ["work.blocked"]),
        ("update", {"status": "in_progress"}, {"status": "done"}, ["work.done", "work.available"]),
        ("update", {"status": "blocked"}, {"status": "new"}, ["work.available"]),
        ("update", {"status": "blocked"}, {"status": "blocked"}, []),
        ("update", {"priority": "low"}, {"priority": "high"}, []),
        ("handoff", None, {"outcome": "blocked", "note": "n"}, []),
        ("handoff", None, {"outcome": "done", "note": "n"}, []),
        ("handoff", None, {"outcome": "released", "note": "n"}, ["work.available"]),
        ("create", None, {"kind": "task"}, ["work.available"]),
        ("create", None, {"kind": "epic"}, []),
        ("gate_removed", None, {"gate": 1}, ["work.available"]),
        ("note_recorded", None, {"text": "x"}, []),
        ("claimed", None, {}, []),
        ("move", None, {"parent": "epic:1"}, []),
    ],
)
def test_which_events_are_news(
    action: str, before: dict[str, object] | None, after: dict[str, object], expected: list[str]
) -> None:
    assert types_of(action, before, after) == expected


def test_every_kind_of_news_that_can_be_made_is_one_that_can_be_asked_for() -> None:
    made = {
        kind
        for action in (
            "ask_raised",
            "ask_answered",
            "plan_proposed",
            "plan_approved",
            "placement_proposed",
            "placement_rejected",
            "create",
        )
        for kind in types_of(action, None, {"kind": "story"})
    } | {"work.blocked", "work.done", "claim.lapsed"}
    assert made <= set(EVENT_TYPES) and set(NUDGE_TYPES) <= set(EVENT_TYPES)
    assert set(COALESCED) <= set(EVENT_TYPES)
    assert "work.available" not in NUDGE_TYPES


def test_a_message_names_what_it_is_about_by_number_and_never_by_words() -> None:
    assert object_of("ask_raised", 12, {"question": "Which provider?"}) == "ask:12"
    assert object_of("ask_answered", 30, {"ask": 12, "answer": "Stripe"}) == "ask:12"
    assert object_of("plan_proposed", 3, {"title": "Launch"}) == "plan:3"
    assert object_of("plan_rejected", 8, {"plan": 3}) == "plan:3"
    assert object_of("placement_accepted", 9, {"placement": 7}) == "placement:7"
    assert object_of("update", 5, {"status": "blocked"}) is None


def test_the_kinds_of_news_asked_for_are_checked_and_put_in_order() -> None:
    assert clean_events(["work.done", "ask.raised"]) == ["ask.raised", "work.done"]
    assert clean_events(["ask.raised", "ask.raised"]) == ["ask.raised"]
    for bad in ([], "ask.raised", ["nonsense"], [3], None):
        with pytest.raises(Exception) as refused:
            clean_events(bad)
        assert "Nothing was changed" in str(refused.value)
    with pytest.raises(Exception) as only_some:
        clean_events(["work.done"], NUDGE_TYPES)
    assert "not a kind of news" in str(only_some.value)


def test_the_retries_grow_and_a_message_is_given_up_on_after_the_last() -> None:
    assert list(RETRY_DELAYS) == sorted(RETRY_DELAYS) and len(set(RETRY_DELAYS)) == len(
        RETRY_DELAYS
    )
    assert RETRY_DELAYS[0] <= 10 and RETRY_DELAYS[-1] <= 2 * 3600
    assert len(RETRY_DELAYS) + 1 == MOST_ATTEMPTS
