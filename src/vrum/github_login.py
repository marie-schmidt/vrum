"""Sign in with GitHub: the OAuth code flow (with PKCE), for a GitHub OAuth App on github.com or on
a GitHub Enterprise Server.

Vrum sends the person to GitHub, gets a code back, swaps it at GitHub for an access token, asks
GitHub who the token belongs to, and forgets the token at once: it is used for two calls and never
kept, logged or shown. Who the person is comes from the user's numeric id (the one thing GitHub
never changes: a login can be renamed and reused by someone else) and the user's primary email
address, and that only when GitHub says it is verified."""

import logging
from collections.abc import Mapping
from typing import Any

from vrum.accounts import ProviderPerson
from vrum.config import GithubSettings
from vrum.sso_http import (
    Call,
    Fetch,
    ProviderProblem,
    ProviderUnreachable,
    with_query,
)
from vrum.sso_http import fetch as real_fetch

log = logging.getLogger("vrum.github")

SCOPE = "read:user user:email"
_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "Vrum",
    "X-GitHub-Api-Version": "2022-11-28",
}


def primary_verified_email(emails: Any) -> str | None:
    """The address GitHub marks as the person's primary one and verified, or None. An address that
    is only one of several, or that GitHub has not verified, is never used: anyone can add an
    address to their GitHub account, and only a verified one has been shown to be theirs."""
    if not isinstance(emails, list):
        return None
    for one in emails:
        if (
            isinstance(one, dict)
            and one.get("primary") is True
            and one.get("verified") is True
            and isinstance(one.get("email"), str)
            and one["email"]
        ):
            return str(one["email"])
    return None


def person_from_user(user: Any, email: str | None) -> ProviderPerson:
    """Who GitHub says the person is. The subject is the numeric id, never the login."""
    identifier = user.get("id") if isinstance(user, dict) else None
    if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1:
        raise ProviderProblem("failed", "subject_not_usable")
    login = user.get("login") if isinstance(user.get("login"), str) else None
    name = user.get("name") if isinstance(user.get("name"), str) else None
    return ProviderPerson(
        provider="github",
        subject=str(identifier),
        email=email,
        email_confirmed=email is not None,
        name=name or login,
        shown=login or name,
    )


class GithubClient:
    def __init__(
        self, settings: GithubSettings, *, redirect_uri: str, fetch: Fetch = real_fetch
    ) -> None:
        self._settings = settings
        self._redirect_uri = redirect_uri
        self._fetch = fetch

    def authorization_url(self, *, state: str, challenge: str) -> str:
        return with_query(
            self._settings.authorize_url,
            {
                "client_id": self._settings.client_id,
                "redirect_uri": self._redirect_uri,
                "scope": SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )

    async def _token(self, code: str, verifier: str) -> str:
        form = {
            "client_id": self._settings.client_id,
            "client_secret": self._settings.client_secret,
            "code": code,
            "redirect_uri": self._redirect_uri,
            "code_verifier": verifier,
        }
        try:
            reply = await self._fetch(
                Call(
                    f"{self._settings.url}/login/oauth/access_token",
                    {"Accept": "application/json", "User-Agent": "Vrum"},
                    form,
                )
            )
            body = reply.json()
        except ProviderUnreachable:
            raise ProviderProblem("unavailable", "unreachable") from None
        # GitHub answers 200 with an `error` when the code is wrong or has been used.
        token = body.get("access_token") if isinstance(body, dict) else None
        if reply.status != 200 or not isinstance(token, str) or not token or "error" in body:
            raise ProviderProblem("failed", "token_request_refused")
        return token

    async def _get(self, path: str, token: str) -> tuple[int, Any]:
        headers: Mapping[str, str] = {**_API_HEADERS, "Authorization": f"Bearer {token}"}
        try:
            reply = await self._fetch(Call(f"{self._settings.api_url}{path}", headers))
            return reply.status, reply.json() if reply.status == 200 else None
        except ProviderUnreachable:
            raise ProviderProblem("unavailable", "unreachable") from None

    async def person(self, code: str, verifier: str, *, need_email: bool) -> ProviderPerson:
        """Everything after the person came back: swap the code, ask who the token is for, and
        forget the token. When connecting an existing account (`need_email` false) a failure to
        learn the address does not matter."""
        token = await self._token(code, verifier)
        status, user = await self._get("/user", token)
        if status != 200:
            raise ProviderProblem("failed", "user_request_refused")
        email: str | None = None
        try:
            status, emails = await self._get("/user/emails", token)
            email = primary_verified_email(emails) if status == 200 else None
        except ProviderProblem:
            if need_email:
                raise
        return person_from_user(user, email)
