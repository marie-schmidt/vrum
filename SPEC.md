# vrum: a self-hosted project graph for AI agents

## 1. Purpose

A small server that an AI agent (and a human through the agent) uses to plan and track work as an **exact hierarchy**: Epic → Story → Task. It adds a **derived semantic index** to find overlaps and related items, and a **memory layer** of decisions and principles that each point to the items they affect.

Three layers, with one rule between them: **memory suggests, the graph decides.**

1. **Exact graph (source of truth).** Epics, stories, tasks, rules (requirements), typed links, decisions, history. Everything that must be true is enforced by the database, not by the agent's good behaviour.
2. **Derived semantic index.** An embedding of every epic, story, rule and decision. It can be dropped and rebuilt at any time and never decides anything.
3. **Memory layer.** Decisions and principles, each linked to the items they affect. Recalling them is exact first (by link), similar second (by index).

## 2. Who uses it and how

- **AI agents** connect over **MCP** (Model Context Protocol) to the container. This is the main interface.
- **A human** works through the agent. There is **no web UI**. The human view of the work lives elsewhere (for Deelio, a Taiga project), and this tool must be able to export to it later.
- **Self-hosting:** `docker compose up` on one machine. Data lives in a named volume. A backup and a restore are documented and tested.

## 3. Defaults the owner may veto (record each change in DECISIONS.md)

- **Language and framework:** Python 3.12 with the official MCP Python SDK (do not hand-roll the protocol; check its current documentation first), `pytest`, one lint and one type check.
- **Database:** PostgreSQL with `pgvector`, in the same compose file as the app. One database holds the graph and the index tables.
- **MCP transport:** streamable HTTP on a configurable port, bound to localhost by default, with a bearer token read from an environment variable or a secret file. No anonymous access and no default token.
- **Embeddings:** a locally hosted model reached through one small interface and an OpenAI-compatible base URL, configured by `EMBEDDING_BASE_URL` and `EMBEDDING_MODEL`. The compose file offers an optional `local-embeddings` profile that runs a local embedding service. The vector size is read from the model, never hard-coded. No data leaves the machine unless the owner points the base URL elsewhere.
- **Identity of actors:** every write names an actor (a person or an agent name) given by the caller.

## 4. Non-goals (do not build)

A web UI, sprints and boards, comments threads, notifications, multi-tenant accounts, billing, a plugin system, importers other than the one in M8, and any abstraction without a consumer in a milestone below.

## 5. The data model (exact; enforced in the database)

- **Node** with a kind: `epic`, `story` or `task`.
    - An epic has no parent. A story has exactly one parent, which is an epic. A task has exactly one parent, which is a story. Any other combination is refused by the database.
    - Fields: id, kind, parent, title, body (plain text or Markdown), status, priority, version (integer for optimistic locking), created and updated times.
    - Status: `new`, `ready`, `in_progress`, `blocked`, `done`, `dropped`. `ready` is computed: a node is ready when it is `new` and every `needs_first` target is `done`.
- **Rule** (a requirement): a stated rule, with a status `held`, `settled` or `dropped`, and **at most one owning story**, enforced by a single column, never a join table. Moving a rule to another story is one atomic operation recorded in history. A rule with no story is "unplaced".
- **Link** between two nodes, with a type:
    - `needs_first` (the source cannot start before the target is done; cycles are refused),
    - `supersedes`, `duplicates`, `relates_to`.
  Links between a node and itself are refused.
- **Decision:** id, date, title, text, reason, decided_by (a named person), status `active` or `superseded`, and a `kind` of `decision` or `principle`. A decision links to zero or more nodes and rules, and may supersede another decision.
- **Claim:** at most one actor holds a claim on a task or story at a time, with a lease that expires. Claiming is atomic.
- **Event log:** every create, update, move, link, claim and decision is appended with actor, time and before-and-after. Nothing is overwritten silently. Updates that carry an old `version` are refused with a clear message.

## 6. The MCP tools (plain names, plain descriptions an agent can read)

Create, get, list, update and move nodes; add and remove links; ready work; claim, renew and release; place and move rules; coverage report (unplaced rules, stories without rules); record, supersede and recall decisions; search; find overlaps; reindex; export and import; history of one node. Every tool returns clear, plain-English refusals that say what was wrong and what to do next, and every list is paged.

## 7. Milestones

Each milestone ends in working behaviour reachable through MCP in the running containers. Tick them in order.

- **M0. Bootstrap.** Repository, SPEC.md, PROGRESS.md, AGENTS.md, DECISIONS.md, one check command (`./check.sh` or equivalent: lint, types, unit and integration tests), and a compose file where the database and the app start and a health check passes.
  *Proof:* from a clean clone, `docker compose up -d` becomes healthy, and an MCP client lists the tools.
- **M1. Graph core.** Schema, constraints, node create, get, list, update and move, optimistic versioning, event log.
  *Proof:* through MCP, a story without an epic, a task under an epic, a second parent and a stale version are each refused with a plain message; a valid epic, story and task are created and their history is readable.
- **M2. Links, readiness and claims.** Link types, cycle refusal, computed readiness, atomic claims with leases.
  *Proof:* a cycle is refused; finishing a prerequisite makes its dependent ready; ten parallel claimers on one ready task produce exactly one winner; an expired lease can be claimed by another actor.
- **M3. Rules and coverage.** Rules placed in exactly one story, atomic moves, the coverage report.
  *Proof:* a rule cannot be placed in two stories; a move is atomic and logged; the coverage report lists unplaced rules and stories with none.
- **M4. Decisions and principles.** Record, link, supersede and exact recall by node, plus the list of active principles.
  *Proof:* recalling a story returns its linked active decisions and all active principles; a superseded decision is shown as such and not as active; nothing is invented.
- **M5. Semantic index.** Embedding client, reindex job, search, overlap finder (nearest neighbours across epics above a configurable threshold, with scores, never merging or changing anything), similar-decision recall.
  *Proof:* on a fixture of known duplicate and known unrelated pairs, the overlap finder flags the duplicates and not the unrelated pairs, and the measured counts are printed; dropping the index tables and running reindex gives the same results; no tool other than reindex writes to the index.
- **M6. Hardening.** Bearer-token auth, no default token, localhost binding, paging everywhere, limits, input size caps, plain refusals, structured logs without secrets.
  *Proof:* a call without or with a wrong token is refused; the logs contain no token; an oversized request is refused with a plain message.
- **M7. Export, import, backup, restore.** Full export and import (JSON), a backup script and a restore script.
  *Proof:* backup, destroy the volume, restore into a fresh container, and the exported graph is identical to the one taken before.
- **M8. Real-data trial (only if `{{LEDGER_PATH}}` is given).** A one-off importer for the Deelio ledger (epics, rules and their story placement; decisions from `{{DECISIONS_PATH}}` if given). Read-only on the sources. Then run coverage and the overlap finder.
  *Proof:* counts after import equal counts in the source (printed); a report of the top overlap candidates is written for the owner to review; nothing is written to Taiga or to any Deelio file.
- **M9. Docs and acceptance.** README (Docker quick start, how to configure the token, example MCP client configuration, backup and restore) and `./acceptance.sh`, which starts from a clean clone and, through a real MCP client, runs one scenario covering every proof above.
  *Proof:* the acceptance script passes from a clean clone.

## 8. Quality bar

- Tests prove behaviour through MCP against real containers. Do not test your own mocks.
- Each milestone keeps all earlier proofs passing.
- Never commit a secret. Configuration comes from the environment.
- Keep to one owner module per concept; reuse it rather than copying.
- Plain English in every message a person or agent reads.

## 9. What the loop must never do

Touch any Deelio repository or the Taiga project, send data to a third party without the owner setting the base URL, invent a product answer, bulk-generate sample data that looks real, or tick a milestone whose proof has not passed.
