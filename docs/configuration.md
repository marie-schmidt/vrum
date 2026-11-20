# Configuration

Vrum starts with no configuration at all. Every setting below has a default. To change one, copy
`.env.example` to `.env` in the folder you run `docker compose` from, edit it, and restart the
stack: `./start.sh`. You can also set one for a single start on the command line, for example
`VRUM_PORT=8090 ./start.sh`. `.env` is ignored by git.

## The database password

You never have to make one. The first time Vrum starts, the database makes a random password and
keeps it in a Docker volume of its own (`vrum-secrets`) that the server reads. The database is not
published outside Docker, and no two installs share a password. The password is chosen once, when
the database is first made.

- To choose it yourself, set `VRUM_DB_PASSWORD` before the very first start. Setting or changing it
  after that changes nothing, so it can never lock the server out of its own database.
- `docker compose down` keeps the database and the password. `docker compose down --volumes`
  deletes both, accounts and projects included, and the next start makes a new password.
- If the database is still there but its password file is gone (for example, only the
  `vrum-secrets` volume was deleted), the database will not start, and `docker compose logs db`
  says why. Set `VRUM_DB_PASSWORD` to the old password, or run `docker compose down --volumes` to
  start again with an empty database.

## Settings

| Setting | What it does |
| --- | --- |
| `VRUM_DB_PASSWORD` | The database password, only read the first time Vrum starts. Optional: if it is not set, a random one is made. |
| `VRUM_PORT` | The port on this machine, for the board and the agents' endpoint. 8000 by default. |
| `VRUM_BIND_ADDRESS` | The address the port is published on. `127.0.0.1` by default: this machine only. |
| `VRUM_MAX_REQUEST_BYTES` | The most one request may hold. 4 MiB by default. It is the only limit on what one write may contain. |
| `VRUM_BOARD_POLL_SECONDS` | How often the board asks the server what changed. 5 by default, 0.5 at the least. |
| `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL` | The embedding service, for search by meaning. By default the built-in one (`http://embeddings:8080/v1` and `all-MiniLM-L6-v2`). Set both to a service of your own, or both to nothing to go without. |
| `EMBEDDING_API_KEY` | The key of the embedding service, if it wants one. |
| `EMBEDDING_MAX_CHARS` | The most text of one item sent to the model. 2000 by default. |
| `EMBEDDING_MIN_SIMILARITY` | How alike (0 to 1) a text must be to be found by meaning. 0.35 by default. |
| `EMBEDDING_OVERLAP_THRESHOLD` | How alike (0 to 1) two items must be to be reported as possible duplicates. 0.55 by default. |
| `EMBEDDING_REINDEX_SECONDS` | How often the server looks for items to index. 5 by default. |
| `VRUM_PUBLIC_URL` | Where people open the board: every link in an email starts with it. `http://localhost:8000` (your port) by default, which is right for one machine. Set it to the real address when Vrum is behind a proxy. |
| `VRUM_SMTP_HOST` | The mail server. Setting it turns email on. Optional: without it nothing is sent (see [Email](#email)). |
| `VRUM_SMTP_PORT` | The mail server's port. 587 by default, or 465 when `VRUM_SMTP_SECURITY` is `ssl`. |
| `VRUM_SMTP_SECURITY` | `starttls` (the default: an ordinary connection that is made secure), `ssl` (secure from the first byte, usually port 465) or `none` (not secure: for a catcher on this machine only). |
| `VRUM_SMTP_USER`, `VRUM_SMTP_PASSWORD` | The mail server's sign-in, if it wants one. Set both or neither. The password can instead be given as a file in `VRUM_SMTP_PASSWORD_FILE`. |
| `VRUM_SMTP_FROM` | The address messages come from, such as `Vrum <vrum@example.com>`. Required when `VRUM_SMTP_HOST` is set. |
| `VRUM_OIDC_ISSUER`, `VRUM_GITHUB_CLIENT_ID`, `VRUM_LDAP_URL` | Each turns on one way of signing in with another provider, with its own settings. Optional: without them nothing changes (see [Sign in with another provider](#sign-in-with-another-provider)). |
| `VRUM_AGENT_KEY` | Not read by the server. The agent key of one project, for the scripts in `scripts/` (see [backup and export](backup-and-export.md)). |

The server only answers requests addressed to `localhost` on its port, and compose publishes the
port on `127.0.0.1`. Passwords and keys travel as plain text over HTTP, so before you set
`VRUM_BIND_ADDRESS` to anything else, put something in front of Vrum that adds TLS, and read
`DECISIONS.md` (the M6 and M15 sections).

`docker compose logs app` shows what the server did, as one JSON line per event. The logs never
contain passwords, sessions, keys or anything you wrote.

## Email

Email is optional and off by default. With no `VRUM_SMTP_HOST`, Vrum sends nothing, and nothing
about the board changes: an invitation is a link the owner copies and shares, an email address is
only the name of an account, nothing is confirmed by email, and there is no "Forgot your password?"
link, so a forgotten password cannot be recovered.

With a mail server, Vrum sends plain text messages for three things, and nothing else:

- **An invitation** to the address the owner types in the invite form. The link in it works once
  for seven days, and only an account with that address can accept it. The owner can still copy
  the link, and can send it again (the old link then stops working).
- **A link to confirm an address**, sent when someone registers and whenever they ask for it on
  the Users page. It works once for 24 hours. Confirming gates nothing: an account whose address is
  not confirmed works fully. An invitation that was emailed to an address also confirms it when
  the account with that address accepts it.
- **A link to choose a new password**, offered on the sign-in page ("Forgot your password?"). It
  works once for one hour, ends every sign-in of the account when the new password is saved, and
  does not touch agent keys. The page that asks for it answers in the same words whether or not the
  address has an account, so it cannot be used to find out who has one.

Each message holds one link and does nothing until that link is used. A failure to send never
undoes what it belongs to: an invitation is still made (with a plain note that the email was not
sent), and its link still works. Vrum limits how many messages
it sends (invitations: 20 an hour for one owner and 60 a day for one project; confirmations: 3 an
hour for one account; password resets: 5 an hour for one address and 20 an hour for one network
address), and says so in words when a limit is reached. Nothing Vrum logs holds an address, a link
or a message: a line says only what kind of message it was and whether it was sent.

**Settings.** Set `VRUM_SMTP_HOST` and `VRUM_SMTP_FROM`, and add the rest if your mail server needs
them. A setting that makes no sense stops the server with a plain message, and the other
`VRUM_SMTP_` settings without a host do too. The password is read like the database password
and the embedding key: directly from `VRUM_SMTP_PASSWORD`, or from the file `VRUM_SMTP_PASSWORD_FILE`
names (never both), and it is kept out of the logs. A sign-in over a connection that is not secure
(`VRUM_SMTP_SECURITY=none`) is refused, since the password would travel as plain text.

**Links.** Every link in a message starts with `VRUM_PUBLIC_URL`, which you set, and never with
the address a request came with, so that nobody can make Vrum send a link to their own site. Behind
a proxy, set it to the address people use, such as `https://vrum.example.com`.

**A mail provider.** Any provider that offers SMTP works. Give its host, then use port 587 with
`starttls` (the default), or port 465 with `ssl`. Most want a user name and a password, often an
app password or an API key; `VRUM_SMTP_FROM` usually has to be an address the provider lets you send
from. If the provider refuses a message, the owner who sent the invitation is told what it said.

**A local catcher.** To try email without a real account, run a catcher on this machine, such as
MailHog or Mailpit, set `VRUM_SMTP_HOST` to its address (from inside the compose network, a service
name), `VRUM_SMTP_PORT` to its port (1025 for both of those) and `VRUM_SMTP_SECURITY=none`, and
read the messages on its web page. `./demo.sh` does this with a small stand-in that it starts for
you, so the demo shows its sent email without any setup.

## Sign in with another provider

Vrum can let people sign in with OpenID Connect (Google, Microsoft, Okta, Keycloak and many
others), with GitHub, or with a username and password that an LDAP directory (OpenLDAP, Active
Directory) checks. All three are optional and off by default. With no provider set up there is no
button, nothing in the server answers for one, and the sign-in page is exactly as it was. Each
provider turns on with one setting (`VRUM_OIDC_ISSUER`, `VRUM_GITHUB_CLIENT_ID`, `VRUM_LDAP_URL`),
you can turn on all three at once, and a setting for a provider that is not turned on stops the
server with a plain message.

A provider is another way to sign in, never a way to take over an account. The rules are these, and
the server keeps to them whatever a provider sends:

- **Someone who signed in with a provider before is the same account**, even if they changed their
  email address there. What identifies them is the number or id the provider says never changes
  (the OpenID `sub`, GitHub's numeric id, the directory's id attribute), not their address or
  their username.
- **A new person is joined to an existing account by email address only when the address is
  confirmed on both sides**: the provider says it is theirs, and the account's address was
  confirmed here (by a link that reached its mailbox, by an emailed invitation, by a password reset,
  or because a provider made the account). If the account's address was never confirmed, nobody is
  signed in and nothing is changed: whoever has that account signs in with its password and then
  connects the provider from the Users page. Without this rule, someone could register another
  person's address with a password of their own and be joined to that person's real sign-in.
  Without email set up, accounts that people made with a password are never confirmed, so they
  connect a provider from the Users page.
- **With no account at all, one is made** (when `VRUM_SSO_CREATE_ACCOUNTS` allows it): its name is
  the provider's name for the person, its address is the confirmed one the provider gave, and it has
  no password. The person can set one on the Users page, and a password reset (when email is set
  up) does the same. An account with no password cannot sign in with an email address until it has
  one.
- **A provider that does not say the address is confirmed cannot create or join anything.** OpenID
  Connect has to say `email_verified` is true, GitHub has to give a primary address that GitHub has
  verified, and a directory's `mail` attribute counts as confirmed because the directory is yours.
  An account that already has a provider connected still signs in whatever the provider says about
  its address.
- **`VRUM_OIDC_ALLOWED_DOMAINS`, `VRUM_GITHUB_ALLOWED_DOMAINS` and `VRUM_LDAP_ALLOWED_DOMAINS`**
  (comma separated, such as `example.com,example.org`; empty means any) limit which email domains a
  first sign-in with that provider may use, for making an account and for joining one. A domain
  matches exactly: `example.com` does not include `mail.example.com`.
- **The last way in cannot be taken away.** On the Users page a person can connect or disconnect a
  provider, but not the only way they can sign in. Disconnecting a provider also ends the account's
  other sessions, in case it was disconnected because someone else got in there.

Vrum never stores a provider's token: the OpenID access token and the GitHub token are used for the
sign-in and then forgotten, and a directory password is used for one check and never logged or kept.
Nothing a provider writes is shown on a page except as plain text, and a provider's own error text
is never shown at all: the board says one of a few fixed sentences. The log has one line for each
attempt (`sso_login`), saying which provider, what was being done and how it ended, and never a
code, a token, a state, an address, a name or a password.

### Settings

| Setting | What it does |
| --- | --- |
| `VRUM_SSO_CREATE_ACCOUNTS` | Whether a first sign-in with a provider may make a new account. `true` by default. With `false`, only people who already have an account can use a provider (they connect it from the Users page first, or an account is joined by a confirmed address). |
| `VRUM_SSO_ALLOW_INSECURE_HTTP` | A provider's address must start with `https://`, because codes and tokens travel to it. Set `true` only for a test provider on this machine: the server then also accepts `http://` and says so in its log at every start. `false` by default. |
| `VRUM_OIDC_ISSUER` | Turns on OpenID Connect. The provider's base address, the one that serves `/.well-known/openid-configuration`. Vrum insists that the description it finds there names this exact issuer, so write it exactly as the provider does (some end in a slash). |
| `VRUM_OIDC_CLIENT_ID` | The client id the provider gave Vrum. Required with the issuer. |
| `VRUM_OIDC_CLIENT_SECRET` | The client secret, or put the path of a file that holds it in `VRUM_OIDC_CLIENT_SECRET_FILE` (never both). Empty means a public client that relies on PKCE alone, which only some providers allow. |
| `VRUM_OIDC_NAME` | The words on the button: "Sign in with OpenID" by default. Write `Google` or `Company login`. |
| `VRUM_OIDC_SCOPES` | What Vrum asks for. `openid email profile` by default. It has to include `openid`. |
| `VRUM_OIDC_ALLOWED_DOMAINS` | Email domains that may sign in this way. Empty means any. |
| `VRUM_GITHUB_CLIENT_ID` | Turns on GitHub. The client id of a GitHub OAuth App. |
| `VRUM_GITHUB_CLIENT_SECRET` | The OAuth App's client secret (required), or the path of a file that holds it in `VRUM_GITHUB_CLIENT_SECRET_FILE`. |
| `VRUM_GITHUB_URL` | The address Vrum itself calls to swap the code for a token. `https://github.com` by default. A GitHub Enterprise Server sets its own, such as `https://github.example.com`. |
| `VRUM_GITHUB_AUTHORIZE_URL` | The address the browser is sent to. `<VRUM_GITHUB_URL>/login/oauth/authorize` by default. |
| `VRUM_GITHUB_API_URL` | The address of GitHub's API. `https://api.github.com` by default. A GitHub Enterprise Server needs its own, usually `https://github.example.com/api/v3`. |
| `VRUM_GITHUB_ALLOWED_DOMAINS` | Email domains that may sign in this way. Empty means any. The button always says GitHub. |
| `VRUM_LDAP_URL` | Turns on LDAP. The directory's address: `ldaps://host:636` (secure from the first byte) or `ldap://host:389`. |
| `VRUM_LDAP_SECURITY` | For `ldap://` only: `starttls` (the default: the connection is made secure before any password is sent, and the directory's certificate is checked) or `none` (nothing is secure: only for a directory on this machine for testing, and the server says so in its log at every start). Ignored for `ldaps://`. |
| `VRUM_LDAP_BIND_DN`, `VRUM_LDAP_BIND_PASSWORD` | The service account that searches the directory for the person who is signing in. Set both, or neither to search anonymously (some directories allow it). The password can instead be a file in `VRUM_LDAP_BIND_PASSWORD_FILE`. |
| `VRUM_LDAP_BASE_DN` | Where in the directory to look for people, such as `dc=example,dc=com`. Required. |
| `VRUM_LDAP_USER_FILTER` | The search that finds one person from the username they typed. `(uid={username})` by default. It has to hold `{username}` exactly once. Vrum escapes what was typed, so a username like `*)(uid=*` is only a strange name that matches nobody. It has to find exactly one entry: none or several is refused. |
| `VRUM_LDAP_EMAIL_ATTRIBUTE` | The attribute that holds the person's email address. `mail` by default. |
| `VRUM_LDAP_NAME_ATTRIBUTE` | The attribute that holds the person's name, used when a new account is made. `cn` by default. |
| `VRUM_LDAP_ID_ATTRIBUTE` | The attribute that holds one value that identifies the person for good, even if their username changes. `entryUUID` by default (OpenLDAP). It has to be present, single-valued and never reused. A binary value such as Active Directory's `objectGUID` is kept as hexadecimal text. `uid`, `sAMAccountName` and `mail` can change and are the wrong choice. |
| `VRUM_LDAP_NAME` | The words on the button: "Sign in with LDAP" by default. Write `Company login`. |
| `VRUM_LDAP_ALLOWED_DOMAINS` | Email domains that may sign in this way. Empty means any. |

The addresses are checked when the server starts: a setting that makes no sense, or a provider that
is half set up, stops it with a plain message, as the email settings do. The client secrets and the
directory password are read like the database password and are kept out of the logs.

### Where people are sent back to

OpenID Connect and GitHub send the browser away to sign in and back again, to an address that you
have to tell the provider beforehand. The address starts with `VRUM_PUBLIC_URL`, which you set (see
[Email](#email); it is `http://localhost:8000` by default and has to be the real address when Vrum is
behind a proxy), and is never taken from a request. Register these exactly:

- OpenID Connect: `<VRUM_PUBLIC_URL>/board/auth/oidc/callback`
- GitHub: `<VRUM_PUBLIC_URL>/board/auth/github/callback`

For example, `https://vrum.example.com/board/auth/oidc/callback`. LDAP has no redirect: the person
types the directory's username and password into Vrum, which checks them against the directory.

Open the board at the address in `VRUM_PUBLIC_URL` when you sign in with OpenID Connect or GitHub.
The cookie that ties a sign-in to the browser that started it belongs to that address, so a board
opened at `127.0.0.1` while `VRUM_PUBLIC_URL` says `localhost` cannot finish a sign-in: the person
comes back with "That sign-in took too long or was already used".

### A GitHub OAuth App

1. On GitHub, open Settings, then Developer settings, then OAuth Apps, then New OAuth App. (For an
   organization, use the organization's settings instead.)
2. Give it a name, such as Vrum. The homepage is `VRUM_PUBLIC_URL`.
3. Set the Authorization callback URL to `<VRUM_PUBLIC_URL>/board/auth/github/callback`.
4. Register it, then choose Generate a new client secret.
5. Put the client id in `VRUM_GITHUB_CLIENT_ID` and the secret in `VRUM_GITHUB_CLIENT_SECRET` (or
   a file named by `VRUM_GITHUB_CLIENT_SECRET_FILE`), and restart.

Vrum asks for the scopes `read:user` and `user:email`, which let it read who the person is and their
email addresses, and nothing else: it cannot read their code. Only the primary address, and only
when GitHub has verified it, is used. A GitHub Enterprise Server sets `VRUM_GITHUB_URL` and
`VRUM_GITHUB_API_URL`. The live GitHub path was not tried against github.com by the people who built
Vrum, since that needs an OAuth App that only you can register: the tests run against a stand-in
that answers with GitHub's documented responses. Try it once and tell us what you find.

### OpenID Connect providers

Register Vrum as a web application (a "confidential client", with the authorization code flow) with
the provider, give it the redirect address above, and put the issuer, client id and client secret in
the settings. Vrum uses PKCE, a state and a nonce, and verifies the ID token's signature, issuer,
audience, expiry and nonce. It accepts only RS256, ES256 and PS256 signatures. Examples:

- **Google**: in the Google Cloud console, Credentials, create an OAuth client id of the type Web
  application, and add the redirect address. `VRUM_OIDC_ISSUER=https://accounts.google.com`,
  `VRUM_OIDC_NAME=Google`. To keep a company's people only, set
  `VRUM_OIDC_ALLOWED_DOMAINS=example.com`.
- **Microsoft Entra ID**: register an application, add a Web redirect, make a client secret.
  `VRUM_OIDC_ISSUER=https://login.microsoftonline.com/<tenant id>/v2.0` with your own tenant's id
  (the issuer has to be exact, so the shared "common" address does not work), `VRUM_OIDC_NAME=Microsoft`.
  Entra usually does not say in its ID tokens that an email address is confirmed, so a first sign-in
  by Microsoft is refused with "did not give a confirmed email address". People there connect
  Microsoft to their existing account from the Users page, after which it works.
- **Okta**: create an OIDC Web App integration, add the redirect address.
  `VRUM_OIDC_ISSUER=https://your-company.okta.com` (or `https://your-company.okta.com/oauth2/default`
  for the default custom authorization server), `VRUM_OIDC_NAME=Okta`.
- **Keycloak**: in a realm, create a client with Client authentication on and the standard flow, and
  add the redirect address. `VRUM_OIDC_ISSUER=https://keycloak.example.com/realms/your-realm`,
  `VRUM_OIDC_NAME=Keycloak`. (Older Keycloak versions have `/auth` before `/realms`.)

Anything else that serves `/.well-known/openid-configuration` works the same way. If a provider
cannot be reached when someone signs in, they are told so in words and nothing is changed. Vrum asks
for the provider's description and keys once an hour (and again when it sees a key it does not know,
at most once a minute).

### LDAP directories

The person types their directory username and password into Vrum's sign-in page, and Vrum checks
them: it finds the person with the service account, then signs in to the directory as that person
with the password. The password reaches Vrum, so make sure people reach Vrum over https
(`VRUM_PUBLIC_URL`, a proxy in front of it) and the directory over `ldaps://` or `starttls`. The page
says so in one line under the form. An empty password is refused before the directory is asked at
all. Failures are counted like password failures: five for one username from one network address,
and two hundred for one network address, in ten minutes. A wrong password and an unknown username get
the same words and take about the same time.

**Behind a proxy.** Vrum counts attempts by the address of the connection it receives, and it does not
trust a forwarded-address header (anyone could write one). Behind a reverse proxy or a company gateway
every person therefore looks like the proxy's address, so the limits are shared by everyone: twenty
sign-in starts for any provider and two hundred failed directory sign-ins in ten minutes, and the
same for password sign-in and password reset. Plain `localhost` and a single machine are not affected.

**OpenLDAP:**

```
VRUM_LDAP_URL=ldaps://ldap.example.com:636
VRUM_LDAP_BIND_DN=cn=vrum-search,dc=example,dc=com
VRUM_LDAP_BIND_PASSWORD=the-service-account-password
VRUM_LDAP_BASE_DN=ou=people,dc=example,dc=com
VRUM_LDAP_USER_FILTER='(uid={username})'
VRUM_LDAP_ID_ATTRIBUTE=entryUUID
VRUM_LDAP_NAME=Company login
```

**Active Directory:**

```
VRUM_LDAP_URL=ldaps://dc.example.com:636
VRUM_LDAP_BIND_DN=CN=svc-vrum,OU=Service Accounts,DC=example,DC=com
VRUM_LDAP_BIND_PASSWORD=the-service-account-password
VRUM_LDAP_BASE_DN=DC=example,DC=com
VRUM_LDAP_USER_FILTER='(&(objectClass=user)(sAMAccountName={username}))'
VRUM_LDAP_ID_ATTRIBUTE=objectGUID
VRUM_LDAP_NAME_ATTRIBUTE=displayName
VRUM_LDAP_NAME=Company login
```

The service account needs only to be able to search for people and read their id, email and name
attributes. When a directory's certificate is signed by a company authority that the server does not
know, make the file that holds the authority's certificate available in the app's container and set
`SSL_CERT_FILE` to its path (it applies to every secure connection the server makes). A certificate
that cannot be checked, or that is for another name than the one in `VRUM_LDAP_URL`, is refused:
if people cannot sign in, `docker compose logs app` shows an `sso_login` line with a short reason
(`certificate_not_accepted` for a certificate that is not trusted or is for another name,
`unreachable`, `directory_error` for another problem with the connection, or
`service_account_refused` for a wrong service account password).

Everyone the filter finds can sign in, so use it to say who may: the filter
`(&(objectClass=person)(memberOf=cn=vrum-users,ou=groups,dc=example,dc=com)(uid={username}))`, for
example, lets in only the members of one group.

### What changes on the board

Each provider that is set up adds a button to the sign-in and create-account pages. On the Users
page, the Sign-in methods section shows whether the account has a password and each provider it is
connected to, with Connect and Disconnect. The Status page lists the providers that are set up (name,
kind and the host Vrum talks to, never a secret), whether a first sign-in may make accounts, and the
allowed domains.

**Restarting the server ends the sign-ins that are in progress.** Vrum keeps what it needs to finish
a sign-in (its state, nonce and PKCE secret) in memory for ten minutes, like its counts of failed
attempts, so a person who was away at the provider while the server restarted starts again. Nothing
else is lost: accounts, connections and sessions are in the database.

Changing any provider setting needs a restart (`./start.sh`). The accounts, connections and
sessions are in the database, so people who signed in with a provider whose settings you remove can
no longer use it: they sign in with a password (set one on the Users page first, or ask for a reset).

## Search by meaning

Searching by exact words needs nothing. Searching by meaning, similar items and possible duplicates
need an **embedding service**. An embedding service turns text into lists of numbers so that texts
can be compared by what they mean. Vrum never asks a language model anything: the embedding
service only turns text into numbers.

**It works with nothing to set up.** `docker compose up` (or `./start.sh`) also starts a small
embedding service of its own, the `embeddings` container, built from the `embedder/` folder. It runs
an open-source model, all-MiniLM-L6-v2 (Apache 2.0, 384 numbers per text), on the processor. The
model files are inside the image, fetched once when the image is built and checked against fixed
checksums, so the running container needs no internet and downloads nothing when it starts. No port
of it is published: only Vrum reaches it, and nothing you write leaves your machine. Text that
holds a well-known secret format (an access token, a private key) is never stored, so it is never
sent to it either.

What it costs: the first build needs internet and takes a few minutes longer, the image is about
500 MB, and the container uses about 200 MB of memory (it is limited to 1 GB). Vrum does not wait
for it: Vrum starts and answers while it is still warming up, and a search says plainly that only
exact words were used until it answers. The index is built by Vrum's own background job, a few
seconds after items are written. The model is small and made for English, and it reads about the
first thousand characters of an item (256 word pieces), so for other languages or longer texts use a
service of your own.

**To use a service of your own**, set `EMBEDDING_BASE_URL` and `EMBEDDING_MODEL` in `.env` to its
address and model (both, together), add `EMBEDDING_API_KEY` if it wants one, and restart. Any
OpenAI-compatible one works. The built-in container still starts unless you stop it from being
used: run `docker compose up -d --build --wait --scale embeddings=0` to leave it out. The index
remembers which model made it, so changing the model rebuilds the index by itself.

**To go without meaning search**, set both to nothing in `.env` (`EMBEDDING_BASE_URL=` and
`EMBEDDING_MODEL=`, with nothing after the sign) and leave the container out as above. Everything
else works, a search says that meaning is not set up, and the top bar says so too.

**Thresholds depend on the model.** `EMBEDDING_MIN_SIMILARITY` (0.35) and
`EMBEDDING_OVERLAP_THRESHOLD` (0.55) were measured on the built-in model with
`scripts/real_embeddings_check.py` against a fixture of 10 reworded duplicates, 5 related but
different pairs and 125 unrelated pairs: the finder flagged 9 of the 10 duplicates (scores 0.57 to
0.87), 1 of the 5 related pairs, and none of the unrelated ones. Four questions with no word in
common with the story they were about each found it by meaning (scores 0.66, 0.36, 0.62, 0.40).
For another model, measure and set them: run the script against a stack you do not mind adding a
few clearly named items to, with the key of one project:

```
export VRUM_AGENT_KEY=<the key of the project>
uv run python scripts/real_embeddings_check.py http://127.0.0.1:8000/mcp
```

New and changed items are indexed in the background, a few seconds after they are written. The
board's Search page and its **Status and export** page say how many are waiting, and the top bar
says so when the embedding service cannot be reached.
