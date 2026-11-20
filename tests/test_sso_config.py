"""The settings for signing in with another provider, with no containers: off by default, each
provider turned on by its own first setting, and every setting that makes no sense refused in plain
words at start."""

from pathlib import Path

import pytest

from vrum.config import ConfigError, Settings

BASE = {"VRUM_DB_HOST": "db", "VRUM_DB_PASSWORD": "a-database-password"}
OIDC = {
    **BASE,
    "VRUM_OIDC_ISSUER": "https://login.example.com/realms/vrum",
    "VRUM_OIDC_CLIENT_ID": "vrum",
}
GITHUB = {**BASE, "VRUM_GITHUB_CLIENT_ID": "gh-id", "VRUM_GITHUB_CLIENT_SECRET": "gh-secret-value"}
LDAP = {**BASE, "VRUM_LDAP_URL": "ldaps://ldap.example.com", "VRUM_LDAP_BASE_DN": "dc=example"}


def test_nothing_is_set_up_unless_asked_for() -> None:
    sso = Settings.from_env(BASE).sso
    assert (sso.oidc, sso.github, sso.ldap) == (None, None, None)
    assert sso.providers() == () and sso.warnings() == () and sso.secrets() == ()
    assert sso.create_accounts is True and sso.allow_insecure_http is False


def test_a_blank_setting_is_the_same_as_none() -> None:
    blanks = {
        name: ""
        for name in (
            "VRUM_SSO_CREATE_ACCOUNTS",
            "VRUM_SSO_ALLOW_INSECURE_HTTP",
            "VRUM_OIDC_ISSUER",
            "VRUM_OIDC_CLIENT_ID",
            "VRUM_GITHUB_CLIENT_ID",
            "VRUM_GITHUB_URL",
            "VRUM_LDAP_URL",
            "VRUM_LDAP_USER_FILTER",
        )
    }
    assert Settings.from_env({**BASE, **blanks}).sso.providers() == ()


def test_openid_connect_has_defaults_and_keeps_the_issuer_exactly_as_written() -> None:
    oidc = Settings.from_env({**OIDC, "VRUM_OIDC_ISSUER": "https://accounts.example.com/"}).sso.oidc
    assert oidc is not None
    assert oidc.issuer == "https://accounts.example.com/"
    assert (oidc.name, oidc.client_secret, oidc.allowed_domains) == ("OpenID", None, ())
    assert oidc.scopes == ("openid", "email", "profile")


def test_openid_connect_settings_can_all_be_changed() -> None:
    env = {
        **OIDC,
        "VRUM_OIDC_CLIENT_SECRET": "an-oidc-secret",
        "VRUM_OIDC_NAME": "Company login",
        "VRUM_OIDC_SCOPES": "openid,email",
        "VRUM_OIDC_ALLOWED_DOMAINS": " Example.com, @example.org ,example.com,",
    }
    sso = Settings.from_env(env).sso
    assert sso.oidc is not None
    assert sso.oidc.name == "Company login" and sso.oidc.scopes == ("openid", "email")
    assert sso.oidc.allowed_domains == ("example.com", "example.org")
    assert sso.allowed_domains("oidc") == ("example.com", "example.org")
    assert sso.allowed_domains("github") == () and sso.allowed_domains("nope") == ()
    assert "an-oidc-secret" in sso.secrets() and "an-oidc-secret" not in repr(sso)


def test_the_openid_secret_can_come_from_a_file(tmp_path: Path) -> None:
    secret = tmp_path / "oidc-secret"
    secret.write_text("from-the-file\n", encoding="utf-8")
    found = Settings.from_env({**OIDC, "VRUM_OIDC_CLIENT_SECRET_FILE": str(secret)})
    assert found.sso.oidc is not None and found.sso.oidc.client_secret == "from-the-file"
    assert "from-the-file" in found.secrets()
    with pytest.raises(ConfigError, match="only one of VRUM_OIDC_CLIENT_SECRET and"):
        Settings.from_env(
            {**OIDC, "VRUM_OIDC_CLIENT_SECRET": "x", "VRUM_OIDC_CLIENT_SECRET_FILE": str(secret)}
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"VRUM_OIDC_CLIENT_ID": ""}, "VRUM_OIDC_CLIENT_ID is not set"),
        ({"VRUM_OIDC_ISSUER": "http://login.example.com"}, "must start with https://"),
        ({"VRUM_OIDC_ISSUER": "login.example.com"}, "must be a web address"),
        ({"VRUM_OIDC_ISSUER": "https://u:p@login.example.com"}, "must not hold a user name"),
        ({"VRUM_OIDC_ISSUER": "https://login.example.com/?x=1"}, "without a query"),
        ({"VRUM_OIDC_SCOPES": "email profile"}, "must include openid"),
        ({"VRUM_OIDC_NAME": "x" * 41}, "name on the sign-in button"),
        ({"VRUM_OIDC_ALLOWED_DOMAINS": "example"}, "list of email domains"),
        ({"VRUM_OIDC_ALLOWED_DOMAINS": "a@b.co"}, "list of email domains"),
        ({"VRUM_SSO_CREATE_ACCOUNTS": "maybe"}, "must be true or false"),
        ({"VRUM_SSO_ALLOW_INSECURE_HTTP": "2"}, "must be true or false"),
    ],
)
def test_openid_settings_that_make_no_sense_are_refused_in_plain_words(
    changes: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env({**OIDC, **changes})


def test_a_setting_of_a_provider_that_is_not_turned_on_stops_the_server() -> None:
    for name, trigger in (
        ("VRUM_OIDC_CLIENT_ID", "VRUM_OIDC_ISSUER"),
        ("VRUM_OIDC_ALLOWED_DOMAINS", "VRUM_OIDC_ISSUER"),
        ("VRUM_GITHUB_CLIENT_SECRET", "VRUM_GITHUB_CLIENT_ID"),
        ("VRUM_GITHUB_URL", "VRUM_GITHUB_CLIENT_ID"),
        ("VRUM_LDAP_BASE_DN", "VRUM_LDAP_URL"),
        ("VRUM_LDAP_BIND_PASSWORD", "VRUM_LDAP_URL"),
    ):
        value = "https://x.example.com" if "URL" in name else "x"
        with pytest.raises(ConfigError, match=f"{trigger} is not set"):
            Settings.from_env({**BASE, name: value})


def test_http_is_only_accepted_for_a_provider_when_the_person_who_runs_vrum_says_so() -> None:
    env = {
        **OIDC,
        "VRUM_OIDC_ISSUER": "http://localhost:5556/dex",
        "VRUM_SSO_ALLOW_INSECURE_HTTP": "true",
    }
    sso = Settings.from_env(env).sso
    assert sso.allow_insecure_http is True
    assert sso.oidc is not None and sso.oidc.issuer == "http://localhost:5556/dex"
    (event, words) = sso.warnings()[0]
    assert event == "sso_insecure_http" and "VRUM_SSO_ALLOW_INSECURE_HTTP" in words
    # It is said whenever it is on, provider or not, so that it is never forgotten.
    alone = Settings.from_env({**BASE, "VRUM_SSO_ALLOW_INSECURE_HTTP": "true"}).sso.warnings()
    assert [one for one, _ in alone] == ["sso_insecure_http"]


def test_github_has_defaults_and_needs_its_secret() -> None:
    github = Settings.from_env(GITHUB).sso.github
    assert github is not None
    assert github.name == "GitHub"
    assert github.url == "https://github.com"
    assert github.authorize_url == "https://github.com/login/oauth/authorize"
    assert github.api_url == "https://api.github.com"
    assert "gh-secret-value" in Settings.from_env(GITHUB).secrets()
    assert "gh-secret-value" not in repr(github)
    with pytest.raises(ConfigError, match="VRUM_GITHUB_CLIENT_SECRET is not set"):
        Settings.from_env({**BASE, "VRUM_GITHUB_CLIENT_ID": "gh-id"})


def test_a_github_enterprise_server_sets_its_own_addresses() -> None:
    env = {
        **GITHUB,
        "VRUM_GITHUB_URL": "https://github.example.com/",
        "VRUM_GITHUB_API_URL": "https://github.example.com/api/v3",
        "VRUM_GITHUB_ALLOWED_DOMAINS": "example.com",
    }
    github = Settings.from_env(env).sso.github
    assert github is not None
    assert github.url == "https://github.example.com"
    assert github.authorize_url == "https://github.example.com/login/oauth/authorize"
    assert github.api_url == "https://github.example.com/api/v3"
    assert github.allowed_domains == ("example.com",)
    with pytest.raises(ConfigError, match="must start with https://"):
        Settings.from_env({**GITHUB, "VRUM_GITHUB_API_URL": "http://api.example.com"})


def test_ldap_has_defaults_and_secure_choices() -> None:
    ldap = Settings.from_env(LDAP).sso.ldap
    assert ldap is not None
    assert (ldap.port, ldap.security, ldap.bind_dn, ldap.bind_password) == (
        636,
        "ldaps",
        None,
        None,
    )
    plain = Settings.from_env({**LDAP, "VRUM_LDAP_URL": "ldap://ldap.example.com"}).sso.ldap
    assert plain is not None and (plain.port, plain.security) == (389, "starttls")
    assert (
        Settings.from_env({**LDAP, "VRUM_LDAP_URL": "ldap://x:1389/"}).sso.providers()[0].host
        == "x:1389"
    )


def test_not_securing_the_directory_connection_is_a_choice_that_is_said_at_every_start() -> None:
    env = {**LDAP, "VRUM_LDAP_URL": "ldap://ldap.example.com", "VRUM_LDAP_SECURITY": "none"}
    sso = Settings.from_env(env).sso
    assert [event for event, _ in sso.warnings()] == ["ldap_not_secure"]
    assert Settings.from_env({**LDAP, "VRUM_LDAP_SECURITY": "none"}).sso.warnings() == ()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"VRUM_LDAP_BASE_DN": ""}, "VRUM_LDAP_BASE_DN is not set"),
        ({"VRUM_LDAP_URL": "http://ldap.example.com"}, "VRUM_LDAP_URL must be the address"),
        ({"VRUM_LDAP_URL": "ldaps://"}, "VRUM_LDAP_URL must be the address"),
        ({"VRUM_LDAP_URL": "ldaps://h:0"}, "VRUM_LDAP_URL must be the address"),
        ({"VRUM_LDAP_URL": "ldaps://h:99999"}, "VRUM_LDAP_URL must be the address"),
        ({"VRUM_LDAP_URL": "ldaps://u@h"}, "VRUM_LDAP_URL must be the address"),
        ({"VRUM_LDAP_SECURITY": "tls"}, "must be starttls or none"),
        ({"VRUM_LDAP_BIND_DN": "cn=x"}, "VRUM_LDAP_BIND_PASSWORD is not set"),
        ({"VRUM_LDAP_BIND_PASSWORD": "x"}, "VRUM_LDAP_BIND_DN is not set"),
        ({"VRUM_LDAP_ID_ATTRIBUTE": "entry UUID"}, "name of a directory attribute"),
        ({"VRUM_LDAP_EMAIL_ATTRIBUTE": "mail)(x"}, "name of a directory attribute"),
        ({"VRUM_LDAP_NAME": "a\nb"}, "name on the sign-in button"),
    ],
)
def test_ldap_settings_that_make_no_sense_are_refused_in_plain_words(
    changes: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env({**LDAP, **changes})


def test_the_bind_password_is_a_secret_and_can_come_from_a_file(tmp_path: Path) -> None:
    password = tmp_path / "bind"
    password.write_text("bind-from-file\n", encoding="utf-8")
    env = {**LDAP, "VRUM_LDAP_BIND_DN": "cn=vrum", "VRUM_LDAP_BIND_PASSWORD_FILE": str(password)}
    found = Settings.from_env(env)
    assert found.sso.ldap is not None and found.sso.ldap.bind_password == "bind-from-file"
    assert "bind-from-file" in found.secrets() and "bind-from-file" not in repr(found)


def test_all_three_providers_can_be_set_up_at_once_and_are_listed_in_order() -> None:
    env = {**OIDC, **GITHUB, **LDAP, "VRUM_OIDC_NAME": "Google"}
    sso = Settings.from_env(env).sso
    assert [(one.id, one.name, one.host) for one in sso.providers()] == [
        ("oidc", "Google", "login.example.com"),
        ("github", "GitHub", "github.com"),
        ("ldap", "LDAP", "ldap.example.com"),
    ]
