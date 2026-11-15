# vrum: a self-hosted project graph for AI agents

## 1. Purpose

A small server that an AI agent (and a human through the agent) uses to plan and track work as an **exact hierarchy**: Epic → Story → Task. It adds a **derived semantic index** to find overlaps and related items, and a **memory layer** of decisions and principles that each point to the items they affect.

Three layers, with one rule between them: **memory suggests, the graph decides.**

1. **Exact graph (source of truth).** Epics, stories, tasks, rules (requirements), typed links, decisions, history. Everything that must be true is enforced by the database, not by the agent's good behaviour.
2. **Derived semantic index.** An embedding of every epic, story, rule and decision. It can be dropped and rebuilt at any time and never decides anything.
3. **Memory layer.** Decisions and principles, each linked to the items they affect. Recalling them is exact first (by link), similar second (by index).

## 2. Who uses it and how

- **AI agents** connect over **MCP** (Model Context Protocol) to the container. This is the main interface.
- **A human** works through the agent and, from M10, through a **board** in the browser: one Kanban page served by the same container. There is no other web UI. The wider human view of the work may still live elsewhere (for Deelio, a Taiga project), and this tool must be able to export to it later.
- **Self-hosting:** `docker compose up` on one machine. Data lives in a named volume. A backup and a restore are documented and tested.

## 3. Defaults the owner may veto (record each change in DECISIONS.md)

- **Language and framework:** Python 3.12 with the official MCP Python SDK (do not hand-roll the protocol; check its current documentation first), `pytest`, one lint and one type check.
- **Database:** PostgreSQL with `pgvector`, in the same compose file as the app. One database holds the graph and the index tables.
- **MCP transport:** streamable HTTP on a configurable port, bound to localhost by default, with a bearer token read from an environment variable or a secret file. No anonymous access and no default token.
- **Embeddings:** a locally hosted model reached through one small interface and an OpenAI-compatible base URL, configured by `EMBEDDING_BASE_URL` and `EMBEDDING_MODEL`. The compose file offers an optional `local-embeddings` profile that runs a local embedding service. The vector size is read from the model, never hard-coded. No data leaves the machine unless the owner points the base URL elsewhere.
- **Identity of actors:** every write names an actor (a person or an agent name) given by the caller.
- **Board (M10):** plain HTML, CSS and JavaScript served by the same server and port at `/board`, with no build step and nothing loaded from the internet. People sign in with the same bearer token. Browser tests use Playwright, as a development dependency only, never in the app image.

## 4. Non-goals (do not build)

A web UI other than the board in M10, sprints, story points, velocity and burndown charts, comments threads, notifications, multi-tenant accounts, billing, a plugin system, importers other than the one in M8, and any abstraction without a consumer in a milestone below.

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

Create, get, list, update and move nodes; add and remove links; ready work; claim, renew and release; place and move rules; coverage report (unplaced rules, stories without rules); record, supersede and recall decisions; search; find overlaps; reindex; export and import; history of one node. From M10, search and find overlaps also say how many items are waiting to be indexed. Every tool returns clear, plain-English refusals that say what was wrong and what to do next, and every list is paged.

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
- **M10. Board for humans.** A Kanban board at `/board` on the same server and port, so a person can see what the agents are doing and refine the plan alongside them. It reads and writes the same graph as the MCP tools and keeps no data of its own.
    - **What a person sees.** One column per status (new, ready, in progress, blocked, done, dropped), with one lane per epic and a filter by epic. Each card shows kind, title, priority, who holds the claim and until when, and which unfinished prerequisite blocks it. Opening a card shows its body, parent, links, linked rules, linked active decisions and principles, overlap candidates (read-only suggestions from the index) and its history. A read-only panel shows the coverage report (unplaced rules, stories without rules).
    - **What a person can do.** Create an epic, a story (choosing its epic) or a task (choosing its story). Edit title, body and priority. Change status by dragging a card, with a menu as the keyboard alternative. Move a story to another epic or a task to another story. Add and remove links. Rules and decisions stay read-only on the board in M10 and are still edited through an agent.
    - **No logic of its own.** Every board action calls the same owner-module operation as the matching MCP tool, so the same constraints, version checks, claims and event log apply, and a refusal reads the same on the board as through MCP. `ready` is computed, so nothing can be dropped into that column, and the board says so plainly.
    - **Named actors.** The person types their name on first use (kept in the browser). Every write carries it, and a blank name is refused.
    - **Conflicts.** An edit carries the version the person saw. If an agent changed the item meanwhile, the edit is refused with the plain stale-version message, the typed text stays in the form, and the card offers to load the current version.
    - **Agents show up on the board.** The board asks for new events every few seconds (default 5) and updates the affected cards without a reload. It never overwrites a form someone is typing in.
    - **The index follows the graph.** The board never writes to the semantic index. Any change to the title or body of an epic, story, rule or decision, by a person or an agent, marks that item as waiting to be indexed. The reindex job, still the only writer to the index, picks it up right after the save, and again later if the embedding service cannot be reached. A failed embedding never fails or delays the edit, because the graph is the source of truth. The board, `search` and `find overlaps` all say how many items are waiting. Moves and status changes need no new embedding, but search and overlap results must show the current parent and status from the graph, never a stale copy.
    - **Sign-in and safety.** The same bearer token as MCP (M6), typed once per browser session and sent in the Authorization header. It is never put in the address, a cookie or a log, and there are no accounts. The page files hold no data, and the data calls under `/board/api` refuse any request without a valid token. All text is shown escaped as plain text (Markdown is not rendered), the page carries a strict content security policy (own origin only), and it works with no internet.
  *Proof* (a real headless browser and a real MCP client, against the real containers):
    1. A wrong or empty token is refused with a plain message, and the data calls refuse requests without a valid token. With the right token the board loads.
    2. Through MCP, create an epic, a story, a task, a `needs_first` link and a claim. Without reloading the page, and within 10 seconds, the board shows them in the right lane and column, with the claim holder and the blocking prerequisite.
    3. In the browser, create an epic, a story and a task, edit title, body and priority, drag a task to in progress and then to done, move a task to another story, and add and remove a link. Through MCP, `get`, `list` and the history then show exactly that, with the typed actor name, the right before-and-after and the right version numbers.
    4. These are each refused on the board with the same plain message the MCP tool gives for the same attempt, leave the graph unchanged and keep the typed text: dropping a card on `ready`, a link that makes a cycle, an edit from a stale version, and a blank actor name.
    5. Edit a story on the board to contain a phrase that appears nowhere else. MCP `search` finds that story by the phrase, and the waiting count reaches zero, with no manual reindex. Stop the embedding service and edit again: the edit still saves, the board and `search` both report one item waiting, and after the service returns the count reaches zero and the item is found. The M5 proof that no tool other than reindex writes to the index still passes, and its test now also covers the board's endpoints.
    6. A title containing HTML and script markup is shown as literal text and nothing runs. Every request the browser makes goes to the server's own address, and the content security policy header is present.
    7. All earlier proofs still pass. The README has a "Board" section (the address, signing in, what a person can and cannot do), `./acceptance.sh` gains a board scenario in the real headless browser, and the layout in `AGENTS.md` lists the board module.

## 8. Quality bar

- Tests prove behaviour through MCP against real containers. Do not test your own mocks.
- Each milestone keeps all earlier proofs passing.
- Never commit a secret. Configuration comes from the environment.
- Keep to one owner module per concept; reuse it rather than copying.
- Plain English in every message a person or agent reads.

## 9. What the loop must never do

Touch any Deelio repository or the Taiga project, send data to a third party without the owner setting the base URL, invent a product answer, bulk-generate sample data that looks real, or tick a milestone whose proof has not passed.
