#!/usr/bin/env bash
# Look around Vrum with sample data, without touching your own install.
#
#   ./demo.sh         starts a separate copy of Vrum with a made-up project in it (Riverside
#                     Bike Share: 13 epics, 65 stories, 191 tasks, with requirements, notes
#                     (some with the person who decided them), claims and handoff notes), then
#                     says where it is and how to sign in. Running it again starts again from a
#                     fresh database.
#   ./demo.sh stop    removes the demo: its containers, its data and its image.
#
# The demo is its own Docker Compose project (vrum-demo, or VRUM_DEMO_PROJECT) with its own
# volumes, on port 8091 (or VRUM_DEMO_PORT), reachable from this machine only. It is built from
# this folder. Nothing from your own install is read or changed: not its containers, volumes or
# data, and not the settings in .env or in your environment. Search by meaning works through the
# real embedding service that comes with Vrum (the `embeddings` container, with its model inside
# the image: see compose.demo.yaml), so what it finds is what a person would really get. Email works
# through a small stand-in mail server: the demo sends its invitations and nothing
# leaves this machine, and what was sent is shown on a page of its own at port 8092 (or
# VRUM_DEMO_MAIL_PORT). Signing in with another provider works through stand-ins too: a scripted
# OpenID provider (port 8093, or VRUM_DEMO_OIDC_PORT) and a scripted GitHub (port 8094, or
# VRUM_DEMO_GITHUB_PORT), whose pages a person's browser is sent to, and a real directory (OpenLDAP)
# with made-up people that is not published. All of them are test stand-ins: nothing real is
# contacted, and none of them has a password except the directory's (open-sesame-<name>).
#
# The sign-in password is made up each time and shown once, here. It is written nowhere else. The
# same password signs in the demo's two other people, an editor and a viewer.
set -euo pipefail
cd "$(dirname "$0")"

project="${VRUM_DEMO_PROJECT:-vrum-demo}"
port="${VRUM_DEMO_PORT:-8091}"
mail_port="${VRUM_DEMO_MAIL_PORT:-8092}"
oidc_port="${VRUM_DEMO_OIDC_PORT:-8093}"
github_port="${VRUM_DEMO_GITHUB_PORT:-8094}"

# What the demo may touch is named here and checked before anything runs: its own Compose
# project, which has to say "demo" so that it can never be your own stack (named vrum), and ports
# of its own.
if ! [[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ && "$project" == *demo* ]]; then
  echo "VRUM_DEMO_PROJECT must be a Compose project name made of lowercase letters, digits," >&2
  echo "dashes and underscores, and it must contain the word demo (it is '${project}')." >&2
  echo "This keeps the demo apart from your own Vrum, which is the project vrum." >&2
  exit 1
fi
if ! [[ "$port" =~ ^[0-9]+$ ]] || ((port < 1024 || port > 65535)); then
  echo "VRUM_DEMO_PORT must be a port number from 1024 to 65535 (it is '${port}')." >&2
  exit 1
fi
if ! [[ "$mail_port" =~ ^[0-9]+$ ]] || ((mail_port < 1024 || mail_port > 65535)); then
  echo "VRUM_DEMO_MAIL_PORT must be a port number from 1024 to 65535 (it is '${mail_port}')." >&2
  exit 1
fi
if ! [[ "$oidc_port" =~ ^[0-9]+$ ]] || ((oidc_port < 1024 || oidc_port > 65535)); then
  echo "VRUM_DEMO_OIDC_PORT must be a port number from 1024 to 65535 (it is '${oidc_port}')." >&2
  exit 1
fi
if ! [[ "$github_port" =~ ^[0-9]+$ ]] || ((github_port < 1024 || github_port > 65535)); then
  echo "VRUM_DEMO_GITHUB_PORT must be a port number from 1024 to 65535 (it is '${github_port}')." >&2
  exit 1
fi
for pair in "$port:$mail_port" "$port:$oidc_port" "$port:$github_port" "$mail_port:$oidc_port" \
  "$mail_port:$github_port" "$oidc_port:$github_port"; do
  if [ "${pair%%:*}" = "${pair##*:}" ]; then
    echo "The demo's ports must all be different ports (two of them are ${pair%%:*})." >&2
    echo "They are VRUM_DEMO_PORT, VRUM_DEMO_MAIL_PORT, VRUM_DEMO_OIDC_PORT and" >&2
    echo "VRUM_DEMO_GITHUB_PORT." >&2
    exit 1
  fi
done
case "${1:-}" in
  "" | stop) ;;
  *)
    echo "Usage: ./demo.sh         start the demo, with sample data" >&2
    echo "       ./demo.sh stop    remove the demo (its containers, data and image)" >&2
    exit 2
    ;;
esac

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "The demo needs Docker with Compose. Install Docker, then run ./demo.sh again." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but not running. Start Docker, then run ./demo.sh again." >&2
  exit 1
fi

# Whatever is set in your environment for your own Vrum must not reach the demo.
unset COMPOSE_PROFILES COMPOSE_ENV_FILES COMPOSE_PROJECT_NAME VRUM_DB_PASSWORD \
  VRUM_MAX_REQUEST_BYTES VRUM_BOARD_POLL_SECONDS EMBEDDING_BASE_URL EMBEDDING_MODEL \
  EMBEDDING_API_KEY EMBEDDING_MAX_CHARS EMBEDDING_MIN_SIMILARITY EMBEDDING_OVERLAP_THRESHOLD \
  EMBEDDING_REINDEX_SECONDS VRUM_PUBLIC_URL VRUM_SMTP_HOST VRUM_SMTP_PORT VRUM_SMTP_SECURITY \
  VRUM_SMTP_USER VRUM_SMTP_PASSWORD VRUM_SMTP_PASSWORD_FILE VRUM_SMTP_FROM \
  VRUM_SSO_CREATE_ACCOUNTS VRUM_SSO_ALLOW_INSECURE_HTTP VRUM_OIDC_ISSUER VRUM_OIDC_CLIENT_ID \
  VRUM_OIDC_CLIENT_SECRET VRUM_OIDC_CLIENT_SECRET_FILE VRUM_OIDC_NAME VRUM_OIDC_SCOPES \
  VRUM_OIDC_ALLOWED_DOMAINS VRUM_GITHUB_CLIENT_ID VRUM_GITHUB_CLIENT_SECRET \
  VRUM_GITHUB_CLIENT_SECRET_FILE VRUM_GITHUB_URL VRUM_GITHUB_AUTHORIZE_URL VRUM_GITHUB_API_URL \
  VRUM_GITHUB_ALLOWED_DOMAINS VRUM_LDAP_URL VRUM_LDAP_SECURITY VRUM_LDAP_BIND_DN \
  VRUM_LDAP_BIND_PASSWORD VRUM_LDAP_BIND_PASSWORD_FILE VRUM_LDAP_BASE_DN VRUM_LDAP_USER_FILTER \
  VRUM_LDAP_EMAIL_ATTRIBUTE VRUM_LDAP_NAME_ATTRIBUTE VRUM_LDAP_ID_ATTRIBUTE VRUM_LDAP_NAME \
  VRUM_LDAP_ALLOWED_DOMAINS VRUM_DEX_PORT VRUM_DEX_APP_PORT
export COMPOSE_FILE=compose.yaml:compose.check.yaml:compose.demo.yaml
# The real embedding service is left out of the checks' stack by a profile, and the demo wants it.
export COMPOSE_PROFILES=real-embeddings
export VRUM_PORT="$port"
export VRUM_MAIL_PORT="$mail_port"
export VRUM_OIDC_PORT="$oidc_port"
export VRUM_GITHUB_PORT="$github_port"
export VRUM_BIND_ADDRESS=127.0.0.1

# Every Docker command of the demo goes through here, so that it names the demo's own project
# and reads no .env file (an empty settings file is given instead).
demo() {
  docker compose --env-file /dev/null -p "$project" "$@"
}

if [ "${1:-}" = "stop" ]; then
  demo down --volumes --remove-orphans --rmi local
  echo
  echo "The demo is gone: its containers, its data and its image. Nothing else was touched."
  exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "The demo fills its project with a small Python program, so it needs uv." >&2
  echo "Install it (https://docs.astral.sh/uv/), then run ./demo.sh again." >&2
  exit 1
fi

# Whatever answers on a port that is not this demo is left alone, and says so.
port_is_used() {
  (exec 3<>"/dev/tcp/127.0.0.1/${1}") 2>/dev/null
}
refuse_the_port() {
  echo "Port ${1} on this machine is already used by something that is not the demo," >&2
  echo "so the demo was not started. Stop that program, or choose other ports:" >&2
  echo "  VRUM_DEMO_PORT=8191 VRUM_DEMO_MAIL_PORT=8192 VRUM_DEMO_OIDC_PORT=8193 \\" >&2
  echo "    VRUM_DEMO_GITHUB_PORT=8194 ./demo.sh" >&2
  exit 1
}
refuse_ports_in_use() {
  for used in "$port" "$mail_port" "$oidc_port" "$github_port"; do
    if port_is_used "$used"; then
      refuse_the_port "$used"
    fi
  done
}
if [ -z "$(demo ps --quiet)" ]; then
  refuse_ports_in_use
fi

# Start from a fresh database: this demo's own containers and volumes go first, and nothing else.
demo down --volumes --remove-orphans
refuse_ports_in_use

# Only what the demo shows is started: the server, its database, the embedding service (the real
# one), the sandbox that runs the scripts of a key that calls Vrum by running code, the scripted mail
# server, OpenID provider and GitHub, and the real directory. (Dex, the real
# OpenID provider the checks use, and the second server that talks to it, are left out.)
if ! demo up -d --build --wait --wait-timeout 240 app embeddings sandbox scripted-mail \
  scripted-oidc scripted-github ldap; then
  echo >&2
  echo "The demo did not start. To see why, run: docker compose -p ${project} logs" >&2
  echo "To remove what it made, run: ./demo.sh stop" >&2
  exit 1
fi

echo
echo "Making the sample project. This takes a minute."
if ! password="$(uv run python -m tests.demo_world "http://127.0.0.1:${port}/mcp")"; then
  echo >&2
  echo "The sample project could not be made, so the demo is not ready. To remove it, run:" >&2
  echo "  ./demo.sh stop" >&2
  exit 1
fi

stop="./demo.sh stop"
if [ "$project" != "vrum-demo" ]; then
  stop="VRUM_DEMO_PROJECT=${project} ${stop}"
fi
echo
echo "The demo is live."
echo "  Open the board:  http://localhost:${port}/board"
echo "  Sign in with:    demo@example.com  and password  ${password}"
echo "  Two more people have accounts with the same password: priya@example.com (an editor)"
echo "  and tom@example.com (a viewer, who can look and not change)."
echo "  It holds sample data only, so nothing here is real."
echo "  Sent email shows at http://localhost:${mail_port} (this demo sends no real email)."
echo "  Sign in with stand-in providers (OpenID at http://localhost:${oidc_port}, GitHub at http://localhost:${github_port}, and a directory, password open-sesame-<name>): priya joins her confirmed account, tom is refused (his account is not confirmed), noor is new. Nothing real is contacted."
echo "  Stop it with:    ${stop}"
