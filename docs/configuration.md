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
| `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL` | An embedding service, for search by meaning. Set both or neither. |
| `EMBEDDING_API_KEY` | The key of the embedding service, if it wants one. |
| `EMBEDDING_MAX_CHARS` | The most text of one item sent to the model. 2000 by default. |
| `EMBEDDING_MIN_SIMILARITY` | How alike (0 to 1) a text must be to be found by meaning. 0.35 by default. |
| `EMBEDDING_OVERLAP_THRESHOLD` | How alike (0 to 1) two items must be to be reported as possible duplicates. 0.55 by default. |
| `EMBEDDING_REINDEX_SECONDS` | How often the server looks for items to index. 5 by default. |
| `VRUM_AGENT_KEY` | Not read by the server. The agent key of one project, for the scripts in `scripts/` (see [backup and export](backup-and-export.md)). |

The server only answers requests addressed to `localhost` on its port, and compose publishes the
port on `127.0.0.1`. Passwords and keys travel as plain text over HTTP, so before you set
`VRUM_BIND_ADDRESS` to anything else, put something in front of Vrum that adds TLS, and read
`DECISIONS.md` (the M6 and M15 sections).

`docker compose logs app` shows what the server did, as one JSON line per event. The logs never
contain passwords, sessions, keys or anything you wrote.

## Search by meaning

Searching by exact words needs nothing. Searching by meaning, similar items and possible duplicates
need an **embedding service**. An embedding service turns text into lists of numbers so that texts
can be compared by what they mean. Any OpenAI-compatible one works. Vrum never asks a language
model anything: the embedding service only turns text into numbers.

Without one, everything else works, and a search says plainly that meaning is not set up. Nothing is
sent anywhere unless you set `EMBEDDING_BASE_URL`, and text that holds a well-known secret format (an
access token, a private key) is never stored, so it is never sent either.

**To use a service of your own**, set `EMBEDDING_BASE_URL` and `EMBEDDING_MODEL` in `.env` to its
address and model, add `EMBEDDING_API_KEY` if it wants one, and restart.

**To run a model on this machine**, add these two lines to `.env`:

```
EMBEDDING_BASE_URL=http://ollama:11434/v1
EMBEDDING_MODEL=all-minilm
```

and start with the local profile:

```
docker compose --profile local-embeddings up -d --build --wait
```

The first start downloads the model, about 50 MB, from the internet. After that it runs offline.

**Thresholds depend on the model.** `EMBEDDING_MIN_SIMILARITY` and `EMBEDDING_OVERLAP_THRESHOLD`
default to values measured for `all-minilm` on a small sample. For another model, measure and set
them. To see what a real model does with realistic requirements, run
`scripts/real_embeddings_check.py` against a stack you do not mind adding a few clearly named items
to, with the key of one project:

```
export VRUM_AGENT_KEY=<the key of the project>
uv run python scripts/real_embeddings_check.py http://127.0.0.1:8000/mcp
```

New and changed items are indexed in the background, a few seconds after they are written. The
board's search drawer and its **Status and export** page say how many are waiting.
