"""Signing in with a directory, with no containers: the search filter is built from escaped text, an
empty password never reaches the directory, and the whole check runs against ldap3's mock
directory (the same code path as a real one, with a pretend server behind it)."""

import asyncio
from typing import Any

import pytest
from ldap3 import MOCK_SYNC, Connection, Server
from ldap3.utils.conv import escape_filter_chars

from vrum.config import ConfigError, LdapSettings, Settings
from vrum.ldap_login import (
    LdapDirectory,
    LdapResult,
    filter_for,
    identifier_of,
    person_from_entry,
    refused_input,
)
from vrum.sso_http import ProviderProblem

BASE = "ou=people,dc=example,dc=com"
SERVICE = "cn=vrum-search,dc=example,dc=com"
ENV = {
    "VRUM_DB_HOST": "db",
    "VRUM_DB_PASSWORD": "a-password",
    "VRUM_LDAP_URL": "ldap://ldap.example.com",
    "VRUM_LDAP_SECURITY": "none",
    "VRUM_LDAP_BASE_DN": BASE,
    "VRUM_LDAP_BIND_DN": SERVICE,
    "VRUM_LDAP_BIND_PASSWORD": "service-password",
}


def settings(**changes: str) -> LdapSettings:
    found = Settings.from_env({**ENV, **changes}).sso.ldap
    assert found is not None
    return found


def directory(**changes: str) -> tuple[LdapDirectory, list[tuple[str | None, str | None]]]:
    """A directory with Ada, Bob and two people called Twin, whose connections are mock ones. The
    second item is every sign-in the directory was asked for."""
    server = Server("mock")
    seed = Connection(server, user=SERVICE, password="service-password", client_strategy=MOCK_SYNC)
    people: dict[str, dict[str, Any]] = {
        f"uid=ada,{BASE}": {
            "userPassword": "ada-password",
            "uid": "ada",
            "cn": "Ada Lovelace",
            "mail": "Ada@Example.com",
            "entryUUID": "11111111-aaaa",
            "objectClass": "inetOrgPerson",
        },
        f"uid=bob,{BASE}": {
            "userPassword": "bob-password",
            "uid": "bob",
            "cn": "Bob",
            "entryUUID": "22222222-bbbb",
            "objectClass": "inetOrgPerson",
        },
        f"cn=twin one,{BASE}": {
            "userPassword": "one",
            "uid": "twin",
            "entryUUID": "33333333",
            "objectClass": "inetOrgPerson",
        },
        f"cn=twin two,{BASE}": {
            "userPassword": "two",
            "uid": "twin",
            "entryUUID": "44444444",
            "objectClass": "inetOrgPerson",
        },
        f"uid=noid,{BASE}": {
            "userPassword": "noid-password",
            "uid": "noid",
            "objectClass": "inetOrgPerson",
        },
    }
    seed.strategy.add_entry(SERVICE, {"userPassword": "service-password"})
    for dn, attributes in people.items():
        seed.strategy.add_entry(dn, attributes)
    binds: list[tuple[str | None, str | None]] = []

    def connector(user: str | None, password: str | None) -> Connection:
        binds.append((user, password))
        return Connection(
            server, user=user, password=password, client_strategy=MOCK_SYNC, raise_exceptions=False
        )

    return LdapDirectory(settings(**changes), connector=connector), binds


def check(username: str, password: str, **changes: str) -> LdapResult:
    found, _ = directory(**changes)
    return asyncio.run(found.authenticate(username, password))


# -- what is never sent to the directory --------------------------------------------------------


def test_an_empty_password_is_refused_before_any_directory_is_asked() -> None:
    assert refused_input("ada", "") == "empty_password"
    assert refused_input("", "x") == "username_not_usable"
    assert refused_input("   ", "x") == "username_not_usable"
    assert refused_input("a" * 257, "x") == "username_not_usable"
    assert refused_input("ada", "p" * 201) == "password_too_long"
    assert refused_input("ada", "p" * 200) is None
    assert refused_input("ada@example.com", " ") is None


@pytest.mark.parametrize(
    "typed", ["*)(uid=*", "*", "ada)(|(uid=*", "\\", "a\x00b", "ada*", "(", ")", "ada)(mail=*"]
)
def test_a_username_goes_into_the_filter_only_after_escaping(typed: str) -> None:
    built = filter_for("(uid={username})", typed)
    assert built == "(uid=" + escape_filter_chars(typed) + ")"
    inside = built[len("(uid=") : -1]
    assert not any(ch in inside for ch in "*()\x00")
    assert built.count("(") == 1 and built.count(")") == 1


def test_a_username_that_is_an_injection_finds_nobody() -> None:
    for typed in ("*)(uid=*", "*", "ada)(|(uid=*", "a*"):
        result = check(typed, "anything")
        assert result.person is None and result.reason == "unknown_user", typed


# -- finding the person and checking the password ------------------------------------------------


def test_the_right_password_gives_the_person_the_directory_describes() -> None:
    result = check("ada", "ada-password")
    assert result.reason == "ok" and result.person is not None
    person = result.person
    assert (person.provider, person.subject) == ("ldap", "11111111-aaaa")
    assert (person.email, person.email_confirmed) == ("Ada@Example.com", True)
    assert (person.name, person.shown) == ("Ada Lovelace", "ada")


def test_a_person_with_no_address_is_still_found_but_has_nothing_confirmed() -> None:
    person = check("bob", "bob-password").person
    assert person is not None
    assert (person.email, person.email_confirmed) == (None, False)


def test_the_wrong_password_and_an_unknown_username_are_both_just_not_signed_in() -> None:
    wrong = check("ada", "not-her-password")
    unknown = check("nobody", "ada-password")
    assert wrong.person is None and unknown.person is None
    assert (wrong.reason, unknown.reason) == ("wrong_password", "unknown_user")


def test_both_failures_take_the_same_steps_with_the_directory() -> None:
    found, wrong_binds = directory()
    asyncio.run(found.authenticate("ada", "not-her-password"))
    found, unknown_binds = directory()
    asyncio.run(found.authenticate("nobody", "x"))
    assert len(wrong_binds) == len(unknown_binds) == 2
    assert wrong_binds[0] == unknown_binds[0] == (SERVICE, "service-password")
    # The bind that stands in for the missing person is for a name that exists nowhere.
    assert unknown_binds[1][0] is not None and unknown_binds[1][0].endswith("," + BASE)
    assert unknown_binds[1][0] != f"uid=nobody,{BASE}"


def test_a_username_that_matches_two_entries_signs_nobody_in() -> None:
    result = check("twin", "one")
    assert result.person is None and result.reason == "ambiguous_user"


def test_the_service_account_signs_in_first_and_the_person_with_the_password_they_typed() -> None:
    found, binds = directory()
    asyncio.run(found.authenticate("ada", "ada-password"))
    assert binds == [(SERVICE, "service-password"), (f"uid=ada,{BASE}", "ada-password")]


def test_a_wrong_service_account_password_is_a_problem_with_the_setup_not_with_the_person() -> None:
    found, _ = directory(VRUM_LDAP_BIND_PASSWORD="the-wrong-one")
    with pytest.raises(ProviderProblem) as problem:
        asyncio.run(found.authenticate("ada", "ada-password"))
    assert (problem.value.code, problem.value.reason) == ("failed", "service_account_refused")


def test_a_person_the_directory_gives_no_identifier_for_cannot_sign_in() -> None:
    found, _ = directory()
    with pytest.raises(ProviderProblem) as problem:
        asyncio.run(found.authenticate("noid", "noid-password"))
    assert problem.value.reason == "no_identifier"


def test_the_username_is_trimmed_but_not_otherwise_changed() -> None:
    assert check("  ada  ", "ada-password").reason == "ok"
    assert (
        check("ADA", "ada-password").reason == "ok"
    )  # the mock, like most directories, ignores case


# -- the identifier ------------------------------------------------------------------------------


def test_the_identifier_is_one_value_as_text_or_as_hexadecimal_for_binary() -> None:
    cfg = settings()
    text = {"raw_attributes": {"entryUUID": [b"abc-123"]}, "attributes": {"entryUUID": ["abc-123"]}}
    assert identifier_of(text, "entryUUID") == "abc-123"
    assert identifier_of(text, "ENTRYUUID") == "abc-123"
    binary = {"raw_attributes": {"objectGUID": [b"\x9f\x00\x01\xfe"]}, "attributes": {}}
    assert identifier_of(binary, "objectGUID") == "9f0001fe"
    number = {"raw_attributes": {}, "attributes": {"uidNumber": [1001]}}
    assert identifier_of(number, "uidNumber") == "1001"
    two = {"raw_attributes": {"entryUUID": [b"a", b"b"]}, "attributes": {"entryUUID": ["a", "b"]}}
    assert identifier_of(two, "entryUUID") is None
    assert identifier_of({"raw_attributes": {}, "attributes": {}}, "entryUUID") is None
    long = {"raw_attributes": {"entryUUID": [b"x" * 256]}, "attributes": {}}
    assert identifier_of(long, "entryUUID") is None
    assert person_from_entry(two, cfg, "x") is None


# -- the settings --------------------------------------------------------------------------------


def test_the_directory_settings_have_defaults_and_say_what_they_hold() -> None:
    found = settings()
    assert (found.host, found.port, found.security) == ("ldap.example.com", 389, "none")
    assert (found.user_filter, found.email_attribute) == ("(uid={username})", "mail")
    assert (found.name_attribute, found.id_attribute) == ("cn", "entryUUID")
    assert found.name == "LDAP"
    assert "service-password" not in repr(found)


def test_an_ldaps_address_is_secure_from_the_start_and_ignores_the_security_setting() -> None:
    found = settings(VRUM_LDAP_URL="ldaps://dc.example.com", VRUM_LDAP_SECURITY="none")
    assert (found.port, found.security) == (636, "ldaps")
    assert settings(VRUM_LDAP_URL="ldap://x.example.com:1389", VRUM_LDAP_SECURITY="").port == 1389


@pytest.mark.parametrize(
    "bad",
    [
        "(uid=x)",
        "(uid={username})(mail={username})",
        "uid={username}",
        "((uid={username})",
        "(uid={username}))",
        "",
    ],
)
def test_a_user_filter_that_cannot_work_is_refused(bad: str) -> None:
    if not bad:
        assert settings(VRUM_LDAP_USER_FILTER=bad).user_filter == "(uid={username})"
        return
    with pytest.raises(ConfigError, match="VRUM_LDAP_USER_FILTER must be"):
        settings(VRUM_LDAP_USER_FILTER=bad)


def test_a_user_filter_with_other_conditions_is_kept_as_written() -> None:
    wanted = "(&(objectClass=person)(sAMAccountName={username}))"
    assert settings(VRUM_LDAP_USER_FILTER=wanted).user_filter == wanted
