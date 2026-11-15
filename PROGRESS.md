# Progress

Status: IN PROGRESS (M0 to M5B, M1A and M4A done; next is M5C)

## Inputs (from the loop file)

- PROJECT_NAME: vrum (already substituted in SPEC.md)
- TARGET_DIR: /Users/marieschmidt/Documents/Work/Start-Up/Projects/Vrum
- LEDGER_PATH: not provided, so M8 is skipped. When M8 is reached, tick it as "skipped (no ledger path)" without writing code. The `{{LEDGER_PATH}}` and `{{DECISIONS_PATH}}` placeholders in SPEC.md are left as they were.
- DECISIONS_PATH: not provided

## Milestones

Tick in order. Each tick records the date and the commit. Each milestone keeps an attempts line; after 3 failed attempts, write BLOCKED.md.

- [x] M0. Bootstrap (attempts: 1) (2026-11-14, commit a44cad8)
- [x] M1. Graph core (attempts: 1) (2026-11-15, commit ad4f5f7)
- [x] M1A. Dates (retro-fix of M1) (attempts: 1) (2026-11-15, commit d85ff0b)
- [x] M2. Links, readiness and claims (attempts: 1) (2026-11-15, commit 33e0fcf)
- [x] M3. Rules and coverage (attempts: 1) (2026-11-15, commit fc080db)
- [x] M4. Decisions and principles (attempts: 1) (2026-11-15, commit 720b08b)
- [x] M4A. Facts and beliefs (retro-fix of M4) (attempts: 1) (2026-11-15, commit 0183530)
- [x] M5. Search and the semantic index (attempts: 1) (2026-11-15, commit 0b62d76)
- [x] M5B. Agent workflow (attempts: 1) (2026-11-15, commit 366c14e)
- [ ] M5C. Extraction and reflection (needs a configured language model) (attempts: 0)
- [ ] M6. Hardening (attempts: 0)
- [ ] M7. Export, import, backup, restore (attempts: 0)
- [ ] M8. Real-data trial (attempts: 0) (skipped, no ledger path: see Inputs)
- [ ] M9. Docs and acceptance (attempts: 0)
- [ ] M10. Board for humans: Kanban and graph (attempts: 0)
- [ ] M11. More views of the work (attempts: 0)
- [ ] M12. Rules and memory on the board (attempts: 0)
- [ ] M13. Find and connect (attempts: 0)
- [ ] M14. Run and see it all (attempts: 0)

## Defaults taken (for owner's veto)

Full wording is in `DECISIONS.md`.

- M0: MCP SDK 2.x (`>=2.3,<3`), `uv` + `ruff` + `mypy --strict` + `pytest`, image `pgvector/pgvector:pg17`, `psycopg` 3 with its async pool.
- M0: no authentication until M6; the server is reachable only from this machine (compose binds `127.0.0.1`). `GET /health` is unauthenticated and says only `ok` or `unavailable`.
- M0: the database password comes from `VRUM_DB_PASSWORD` with no default (`./scripts/init-env.sh` writes a random one to the git-ignored `.env`); the database port is not published.
- M0: the server listens on `VRUM_PORT` (default 8000) and compose publishes the same port number.
- M0: the only tool is `server_status` (server, PostgreSQL and pgvector versions), so the client has something real to list; M1 adds the graph tools.

- M1: ids are whole numbers from the database; priority is `low`, `normal`, `high` or `urgent`; stored status is `new`, `in_progress`, `blocked`, `done` or `dropped` (`ready` is computed from M2 on and never stored); `update_node` accepts `parent_id` only to refuse a second parent and point to `move_node`.
- M1: history lists the changed fields before and after with versions; there is no delete (use `dropped`); every list takes `limit` (1 to 200, default 50) and `after_id`; schema changes were numbered SQL files, replaced the same day by the owner's decision: there are no migrations, see below.

- M2: a node nobody has started shows as `ready` when everything it needs first is done (so a new node that needs nothing is `ready`, and `new` means "waiting for something it needs first"); only `done` counts, so a dropped or blocked prerequisite keeps dependents waiting; `waiting_on` lists what a node waits for.
- M2: links work between any two nodes; `duplicates` and `relates_to` work both ways; `supersedes`, `duplicates` and `relates_to` are records only; `list_links` was added so links can be read.
- M2: claims are for stories and tasks that are ready or in progress with nothing open that they need first; a claim does not change status or stop others editing; leases are 1 second to 24 hours, default 15 minutes, lapse on their own, and only the holder renews or releases; `ready_work` lists free stories and tasks oldest first (priority is shown, not used for order).

- Owner's decision, 2026-11-15: no migrations. The whole database is `src/vrum/schema.sql`; a schema change means rebuilding the database from scratch. The server refuses to start on a database built from a different version of the schema, in plain words. The loop's other findings about production habits, including a recommendation about M7's backup and restore, are in `DECISIONS.md`.

- M3: rules have their own ids, a title and body, and a status of `held` (the default), `settled` or `dropped`, which can change freely. A rule is created unplaced or in a story. `place_rule` puts an unplaced rule in a story and refuses one that is already placed; `move_rule` moves a placed rule. Both, and every other change to a rule, need the version last read.
- M3: a rule event shows in the history of the rule and of each story it touches (the story it is in, and the one it left); a rule's own history is `rule_history`.
- M3: the coverage report leaves out dropped rules (they are not unplaced) and dropped stories (they need no rules), and a story whose only rules are dropped counts as having none. Each of its two lists is paged on its own with `show`.

- M4: a decision is a record and is never edited; a change of mind is `supersede_decision`, which records a new decision and marks the old one superseded in one step. `decided_by` (the person) is separate from `actor` (who recorded it); title, text, reason and `decided_by` are required.
- M4: a replacement carries over what the old decision affected unless `keep_links` is false; the old one keeps its links as a record and they cannot be changed. A decision can be superseded once, and a replacement may be of the other kind.
- M4: `recall_decisions` works by exact link only (a node or a rule): its active decisions, every active principle, and its linked decisions that were superseded; nothing is inferred from parents or children. There is no separate principles tool: `list_decisions` with `kind=principle` and `status=active`.

- M1A: dates are plain days written YYYY-MM-DD, read by the server and refused in plain words when badly written; a date is removed only by asking (`clear_start_on`, `clear_target_on`); the start may equal the target. Overdue, the worked-out span (own dates win; otherwise the span of what is under it, leaving out dropped work) and schedule conflicts (a warning, never a refusal; a done or undated prerequisite does not conflict) are computed in the `node_state` view. `list_nodes` gained inclusive date filters and `overdue`.
- M4A: facts and beliefs share one table and one id set (`type` fact or belief, `kind` world or experience for facts); the database refuses edits, deletes, a belief with no evidence and evidence that is a belief. A stale belief (active, with evidence that is no longer active) cannot be confirmed or promoted. A confirmation is once only. Replacements carry links over (`keep_links`) and a replacing fact needs a new source. `supersede_memory` and `retract_memory` answer with ids of what went stale and of decisions promoted from a replaced belief. `recall` replaces `recall_decisions` and has seven sections, each with a count, a next page and a `why` on every entry. `within_node_id` takes the item, what is under it and the rules in those stories.
- M5: exact words are PostgreSQL full-text search on generated `search` columns (English, so endings match; all words must be present, quotes for a phrase, `or`, `-`); tasks are found by words only and everything else is also embedded; fusing is by rank; the index tables are built and owned by the reindex code, with the vector size read from the model, and an item is "waiting" when no vector exists for its current text (no triggers). A background task in the server indexes what is waiting every few seconds. No approximate index, text cut at 2000 characters. Overlaps compare stories, epics or rules in different epics. **Thresholds are measured with the real local model** (`all-minilm` through Ollama): overlap 0.55, meaning floor 0.35; my first guesses of 0.8 and 0.6 would have missed most real duplicates. The check stack uses a scripted stand-in embedding service (`tests/scripted_embeddings.py`). Details and the open question about weighting titles are in `DECISIONS.md`.
- M5B: a handoff note is an event (`handoff`, then `claim_released`), and the database refuses one with a blank note or no outcome. `release_claim` needs a note and puts work that was in progress back to not started, so released work can be taken again. `finish_work` outcomes are done, blocked, dropped or released; only the holder finishes, even after the claim ran out if nobody took over.
- M5B: `next_work` hands out free stories and tasks (ready, or in progress with a lapsed claim), by priority, then earliest target date, then oldest; a story with open tasks is worked through its tasks. It takes `within_node_id` (my addition), `peek`, `lease_seconds` and `context_chars` (default 10,000; what does not fit is cut from the end and counted). Nothing free is an answer, not an error.
- M5B: `create_plan` and `update_nodes` have no size limit; a plan reports every problem at once and creates nothing; each change in `update_nodes` stands alone, and a refused change leaves no history entry. `create_node` checks epics and stories (not tasks) for similar items after saving, at the overlap threshold, waiting 5 seconds at most.
- M5B: `changes_since` shows an event only once every earlier-started change has finished, in the order changes began, so a slow writer's event is never skipped; it can lag briefly behind a long write. Lists leave `body` empty by default (with `body_chars`, a 120-character `body_preview`, and `left_out`); `detail` brings bodies back; the five-times rule holds for bodies of about a page (4.2 times at 2,200 characters).

## M0 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 3 integration tests (real containers, real MCP client) pass.
- From a clean clone: `./scripts/init-env.sh`, then `docker compose up -d`; both services reached `healthy` and a separate MCP client listed `server_status` and called it (PostgreSQL 17.12, pgvector 0.8.7).
- Also seen working: compose refuses to start without a password, a wrong `Host` header gets 421, `/health` returns 503 and `server_status` gives a plain refusal while the database is stopped, and both recover when it restarts.

## M1 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 19 integration tests pass (real containers, real MCP client). The integration tests include the M0 proof, so it still passes.
- From a clean clone with plain `docker compose up -d`: the 19 integration tests pass against that stack, and a separate script showed each refusal as an agent reads it: a story without an epic, a task under an epic, a second parent, and a stale version. A valid epic, story and task were created and the story's history was read (create by marie, update by ann, with before and after and versions).
- The database refuses bad data on its own: tests run SQL directly in the database container for a story with no parent, a task under an epic, an epic with a parent, a story under a story, an unknown kind, a blank title, the stored status `ready`, a kind change, a change without a new version, rewriting or deleting an event, and deleting a node that has history.
- Eight parallel edits from the same version gave exactly one winner. Restarting the stack kept the data and did not re-apply the migration.

## M1A proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 85 integration tests pass on a database built from scratch. Every earlier proof is still covered by them.
- From a clean clone of the commit, started as the separate compose project `vrum-proof` on its own port: the 85 integration tests pass, and a separate script showed each proof statement:
  - A start date after the target date is refused, on create and on update, with a plain message; the database also refuses it on its own (SQL test).
  - Dates set and cleared are shown by `get_node`, and the history lists exactly the dates that changed, with before and after and the actor.
  - A target date that has passed makes a node overdue until it is marked done.
  - A story and an epic with no dates report the span of what is under them, marked as worked out; given dates of their own they report those.
  - A schedule conflict shows on a node that starts before something it needs is due, and goes away when either date changes (and when the prerequisite is done).
  - A stale version is still refused for a date change.
- The test suite also covers: clearing and its refusals, loosely written dates (seven kinds, each with the plain message), same-day start and target, dropped work left out of spans, a task that never works a span out, dates that survive a move and a claim, and the date filters.
- One fix found along the way: the new `overdue` column was empty for nodes with no target date, and the model refused it; it is now false there.

## M2 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 36 integration tests pass (real containers, real MCP client). Every earlier proof is still covered by them.
- From a clean clone with plain `docker compose up -d`: the 36 integration tests pass, and a separate script showed each proof statement:
  - A cycle (A needs B needs C needs A) is refused with a message that names the whole circle.
  - B waits for A and shows `new` with `waiting_on [A]`; once A is done B shows `ready` with nothing it waits for.
  - Ten parallel claimers on one ready task gave exactly one winner (agent-0), and the other nine were told who holds it.
  - After a 3 second lease lapsed, nobody held the task and another actor claimed it; the history shows both claims.
- The database refuses bad links and claims on its own (tests run SQL directly): a link to the same node, an unknown type, a missing node, a circle, a repeated link, a two-way link repeated the other way round, a claim on an epic, a claim naming the wrong kind, a claim with no holder or no time, and a second holder of one task.
- Race check: with the lock in the cycle trigger removed, the parallel-links test fails every time, so it does guard the behaviour. Eight edits, ten claims and five pairs of opposite links added at the same moment each had exactly one winner.
- Restarting the stack kept the data and did not re-apply either migration.

## M3 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 52 integration tests pass on a database built from scratch from `schema.sql`. Every earlier proof is still covered by them.
- From a clean clone, started as the separate compose project `vrum-proof` on its own port: the 52 integration tests pass, and a separate script showed each proof statement:
  - A rule placed in one story cannot be placed in a second: the refusal says it belongs to at most one story and points to `move_rule`, and the rule is still only in the first story.
  - A move into an epic is refused and leaves the rule where it was. Two movers at once gave one winner, and the history reads created, placed, moved with each step's before and after and version; the move shows in the histories of both stories.
  - The coverage report lists the unplaced rule and the stories with no rule, and leaves out the story that holds one.
- The database refuses bad rules on its own (tests run SQL directly): a rule in an epic, a task or a story that does not exist, a blank title, an unknown status, a move into a task, a change without a new version, and events that name no rule, a rule and a node action mixed, or nothing at all. No table pairs rules with stories.
- Race check: with the row lock removed from `move_rule` and `place_rule`, both parallel tests fail on every run (six rounds each), so they guard the behaviour.
- The schema guard: a database whose stored schema marker is wrong, or missing, makes a second server refuse to start, saying to rebuild with `docker compose down --volumes`.

## M4 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 66 integration tests pass on a database built from scratch. Every earlier proof is still covered by them.
- From a clean clone of the commit, started as the separate compose project `vrum-proof` on its own port: the 66 integration tests pass, and a separate script showed each proof statement:
  - Recalling a story returned its two linked active decisions and the active principles (the linked one and one linked to nothing), and left out a decision linked to another story and a decision linked to nothing.
  - A decision replaced by a newer one was shown under superseded, with the id of what replaced it, and was not listed as active anywhere. Its title, text and reason were exactly as recorded, and its history reads recorded, linked, superseded.
  - A story with no decisions got no decisions back, only the principles: nothing is invented.
  - Superseding the same decision again is refused in plain words and points to its replacement.
- The database keeps decisions as a record on its own (tests run SQL directly): it refuses a blank title, text, reason or decider, an unknown kind or status, a decision marked superseded without a replacement (or the reverse), a replacement recorded earlier than what it replaces, an edit to what was decided or who decided it, bringing a superseded decision back, pointing it at another replacement, deleting a decision, links to nodes or rules that do not exist, a repeated link, and any change to the links of a superseded decision.
- Race check: with the row lock removed from `supersede_decision`, the test of six agents superseding one decision at once fails on every run. It first passed without the lock, because the database's own trigger stops the second one; the test now also requires the loser to get the plain message, not a database error, and that is what catches it.
- Housekeeping found along the way: tests that read `ready_work` now read every page (they broke once the shared test database held more than 200 ready items); a test that guessed at table names was replaced by the precise rule (the only table with a `story_id` is `rules`).

## M4A proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 110 integration tests pass on a database built from scratch. M4's tests now call `recall` in place of `recall_decisions` and still pass, as do every earlier milestone's.
- From a clean clone of the commit, started as the separate compose project `vrum-proof` on its own port: the 110 integration tests pass, and a separate script showed each proof statement through a real MCP client:
  - A fact retained against a story is recalled under facts with its source, origin, recorder and both dates, and never under decisions.
  - A belief with two facts as evidence reports two pieces of evidence, is under beliefs and is labelled a suggestion.
  - Retracting a fact makes its belief stale, shown apart and never as active, with the reason; superseding a fact does the same.
  - A belief with no evidence, with evidence that does not exist, or with a belief as evidence is refused in plain words; so is a blank source.
  - Confirming records who and when. Promoting records a decision by that person that links back to the belief.
  - A superseded fact shows as superseded with its replacement. The recall holds exactly what the exact links give: nothing for a story with no links, and nothing from a story for its epic.
  - One new belief replaces two of three in one `supersede_memory` call and the third is untouched. A call that names an already retracted belief is refused and changes nothing. One call replaces 30 beliefs.
  - 60 facts walked in pages of 25 come back once each, with a count that matches; `within_node_id` returns only what is in that area.
  - Retracting a fact reports the beliefs resting on it; `rests_on_memory_id` lists them; `with_dependent_beliefs` retracts them with the same reason and leaves a belief on other evidence alone.
  - A confirmed belief cannot be replaced or retracted without `said_by` and `quote`; with them it works, and the history shows both the agent and the person. An agent alone retracts a suggestion nobody confirmed.
  - Replacing a belief that was promoted reports the decision, which stays active and unchanged.
- The database refuses, on its own (tests run SQL directly): 40 kinds of bad data, among them a fact without a source or kind, a belief with no evidence (checked when the change is saved), evidence that is a belief or not active, any edit or delete of a fact, belief or evidence, a fact replaced by a belief or by an earlier item, a new link on an ended item, a belief that concerns a decision, and a decision promoted from a fact. Facts and beliefs have an append-only history too.
- Race checks: with the lock removed from `supersede_memory`, the test of two agents replacing the same fact fails (the loser gets a raw database error, not the plain message). With the second look for new dependent beliefs removed from `retract_memory`, the test of a retraction racing three new beliefs on the same fact fails on both of two runs, leaving a live belief resting on a retracted fact. Both locks were put back and the checks pass.
- Two things found while writing the tests: Postgres checks `CHECK` constraints in alphabetical order, so one constraint was renamed so that the test of an unknown origin and an unknown type each reach their own constraint; and `capitalize()` lowers the rest of a sentence, which would have turned a retraction reason into lower case in refusals, so the messages now capitalise only the first word.

## M5 proof, as run

- `./check.sh`: lint, strict types, 41 unit tests and 133 integration tests pass on a database built from scratch, with the scripted stand-in embedding service in the check stack. Every earlier milestone's tests still pass (`recall` now also has a `similar` key, empty unless asked for).
- From a clean clone of the commit, started as the separate compose project `vrum-proof` on its own port (with the stand-in service): the same 41 and 133 pass, and a separate script printed each proof statement through a real MCP client:
  - **Overlap finder, measured counts:** on a fixture of two epics with eight stories each, where each story in one has a reworded twin in the other, the finder at its default threshold flagged **8 of 8 known duplicate pairs and 0 of 56 known unrelated pairs**. The duplicates scored 0.91 to 1.00. It never reports two stories in the same epic, shows links that already exist between a pair, leaves dropped stories out unless asked, and changes nothing (versions, histories and links of every story are identical before and after).
  - **Exact words, at once, every kind:** one made-up word put in an epic, story, task, rule, decision, principle, fact and belief finds all eight, each labelled `words` with the matched word and a snippet with the word marked. The counts by type equal what the list tools return for the same word, and the counts by status are right (3 ready, 1 held, 4 active).
  - **Service stopped:** a phrase added to a story is found by words at once and labelled as an exact-words match, and the note says the service is unreachable and how many items wait. Its paraphrase (no word in common) is not found, asking for meaning only is refused in plain words, and `reindex` says the service cannot be reached and that nothing was indexed. After the service returned and `reindex` ran, the paraphrase finds the story by meaning (score 0.89), labelled a suggestion.
  - **Both ways:** a query that matches a story by words and by meaning is labelled `both`, listed first, with why: the matched words and the score.
  - **Facts and beliefs:** found by words and by meaning, each with its origin (`stated`, and, for ones written straight into the database as a model would leave them, `extracted` and `derived` with a label that says a language model wrote it), and never mistaken for a decision.
  - **Dropping the index:** with both index tables dropped, search still found everything by words and said the index was not ready; after `reindex`, the meaning search, the overlap finder and similar items gave identical results (314 rows compared).
  - **Only `reindex` writes to the index:** a digest of every row of the index was identical before and after search, similar items, overlaps, recall with similar items and the list tools; and a test reads the source and finds that only `indexing.py` has any statement that creates, changes or drops the index tables.
  - **The vector size is read from the model:** the index holds vectors of 1024 numbers, which is what the stand-in reports, and the real model gave 384.
  - **Odd characters:** an unbalanced quote, operators, brackets, emoji, a 5000 character query, a null byte and percent signs ran in all three modes without a refusal; a blank query is refused in plain words.
- **The real model, on a fresh database** (the `local-embeddings` profile, Ollama with `all-minilm`, 384 numbers per vector, from the same clean clone; `scripts/real_embeddings_check.py`): the server's own background job indexed the 26 items (2 epics and 24 stories) in 5 seconds with no `reindex` call. At the default overlap threshold of 0.55 the finder flagged **9 of 10 reworded duplicates** (scores 0.57 to 0.87, mean 0.73), **1 of 5 related-but-different pairs** (Password login and Email password reset links, 0.58, which really are related) and **0 of 125 unrelated pairs**. The duplicate it missed ("Cancel subscription" and "Let members end their plan") scored 0.54. Four questions a person might type, with no word in common with the story, each found it by meaning (scores 0.66, 0.36, 0.62 and 0.40; with a floor of 0.4 two of the four would have been missed, which is why the floor is 0.35). The same fixture with `nomic-embed-text` scored duplicates 0.69 to 0.92 but unrelated pairs up to 0.64, so it separates worse.
- **Mutation checks:** with the rule that two stories must be in different epics removed, with the fallback that finds the one item the service refuses removed, and with the check that an index built with another model is not used removed, the overlap test, the refused-item test and the model-change test each failed. The code was put back.
- **Found along the way:** the first thresholds (0.8 and 0.6, picked before any measuring) were wrong for real text, hence the measurements above. The stand-in's first vectors (256 numbers) collided often enough to make one test borderline, so they are 1024 numbers now and the paraphrase tests have a margin. The real-model script first labelled a genuinely related pair (Password login and Email password reset links) as unrelated, and read only the top five results while the stack held copies of the fixture from earlier runs; both are fixed in the script. The Hugging Face text-embeddings-inference image does not run on Apple silicon (amd64 only), so the local profile uses Ollama.
- Scratch stacks, the clone, scratch scripts and the Ollama image I pulled are removed; no containers, volumes or `vrum` images are left.

## M5B proof, as run

- `./check.sh`: lint, strict types, 41 unit tests and 163 integration tests pass on a database built from scratch (30 of them are new, in `tests/test_workflow_integration.py`). Every earlier proof is still covered by them. Two earlier tests were changed because the answers changed shape (a created node and a read node now differ by the similar-items and handoff fields), and one because release needs a note.
- From a clean clone of the commit (`366c14e`), `./check.sh` gave the same 41 and 163. Then the clone was started as the separate compose project `vrum-proof` on its own port (with the stand-in embedding service), and a separate script printed each proof through a real MCP client:
  1. **Ten agents, five ready tasks:** five agents got five different tasks (3, 4, 5, 6, 7), none twice; the other five were told "Nothing is free right now ... The open work is 1 being worked on by someone, 4 being picked up by someone else this very moment." The context held the epic and story, the one rule, the two active decisions (the old one replaced by "Invoices are HTML" appeared only under superseded decisions) and all active principles.
  2. **Handoff:** a blank note was refused ("A handoff note is needed: note cannot be empty...") and the node was identical afterwards. With a note the task was done, the dependent showed as ready, and the next agent's `next_work` showed the note and who left it. A claim left to lapse showed `claimed_by=None`, `lapsed_claim_by=cy` and no handoffs.
  3. **Plan:** 1 epic, 3 stories, 8 tasks, 5 links and 4 rules in one call. An invalid plan was refused with every problem named and nothing created (the counts of nodes, links and rules were the same before and after); so was a circle ("the key 'a' needs the key 'b', which needs the key 'a'"). A plan of **500 items and 392 links was created in one call in 0.33 seconds**.
  4. **Several changes:** five changes with one stale: 4 applied, 1 refused with the stale-version message and no history entry; each applied change is in its task's history under the actor. 120 changes reported all 120 (108 applied, 12 refused).
  5. **What changed:** at page sizes 1, 4 and 200 the feed returned exactly the 7 events the database held after the starting event, in order, with nothing before and nothing missing.
  6. **Small answers:** 50 stories with 4,950-character bodies: **32,533 characters by default, 273,905 with detail, 8.4 times smaller**; the list says what it left out.
  7. **Similar on create:** a story with the same title as an existing one was created and the answer named the existing one (similarity 1.0); with the embedding service stopped it was created and the answer said the check was skipped and why.
  8. **Instructions:** 208 words that name 11 tools, all of which exist.
- **The late event:** two writers in the real database. One begins first, writes its event late and finishes first, so it gets the higher id. The other begins second, writes its event first and finishes last, so it gets the lower id. A feed that went by id alone shows the first and never the second; this feed showed the first at once and the second as soon as it finished. A test does this on every run.
- **Mutation checks:** with the feed going by id alone, the late-event test fails (the late event is skipped); with the row lock removed from `next_work`, the ten-agent race fails (agents collide).
- **Found along the way (all fixed, each with a test):** the handoff check in the database let a note through with no outcome (a NULL slipped through the comparison); work released by `release_claim` stayed in progress and could never be taken again; the "nothing is free" message said "no open work" while other agents were mid-claim; `changes_since` read its page and its latest id in separate snapshots, so a change finishing in between could be skipped by a follower (it now reads both in one); and the context cut kept small items after a large one was cut, instead of cutting the end.
- Scratch stacks, the clone and scratch scripts are removed; no containers, volumes or `vrum` images are left.
- The implementation commit `366c14e` is titled "Save uncommitted changes": the app saved and pushed my working tree on its own before I committed. I did not rewrite pushed history.

## Notes

- A GitHub repository was created on the owner's request: https://github.com/marie-schmidt/vrum (private).
- macOS or a sync tool left stale copies named like `AGENTS 2.md`, `db 2.py` and a git ref `main 2`. They were deleted and `.gitignore` now ignores `* 2` and `* 2.*`. Existing files are now changed with edits instead of replacing them by renaming a new file over them.
- Spec change on the owner's request (2026-11-15), after research on GitHub Projects, Obsidian, Hindsight, Elasticsearch, Taiga and agent task tools: new goal statement and section 10 ("Product shape"); M5 now also does exact-words search; new M5B (agent workflow) after M5; M6 refuses pasted secrets; M7 adds a Markdown export; M9 adds an agent guide; M10 gains the graph view.
- Second spec change on the owner's request (2026-11-15): (1) the whole graph is now one of the views, not the main navigation; (2) facts and beliefs like Hindsight's are in, as the retro-fix M4A (explicit facts and beliefs with evidence, one recall) and M5C (a model, if configured, proposes facts, draws up beliefs and answers questions, all labelled and checked); (3) a roadmap like GitHub Projects' is in, which needs dates on nodes, as the retro-fix M1A. The UI milestones are now M10 (Kanban and graph), M11 (Table, Outline, Roadmap), M12 (Rules and memory), M13 (Find and connect) and M14 (Run and see it all). M1A and M4A change the already-built M1 and M4 (the database is rebuilt, with no upgrade step) and are listed where they belong so that they run first.
- One cap removed on the owner's request (2026-11-15): `create_plan` and `update_nodes` no longer have an item count limit (they had 200 and 50); only M6's cap on request size applies.
- Every choice is in `DECISIONS.md` and open to the owner's veto.
- Third spec change on the owner's question (2026-11-15): M4A and M5C now say what happens when a belief stops being true. `supersede_memory` replaces any number of beliefs at once, `list_memories` walks all of them with a count (and `within` an epic or story), `retract_memory` can retract the beliefs resting on an item, a belief a person confirmed is changed only with the person's name and words, and M5C's `find_contradictions` proposes which beliefs a new statement clashes with, either the nearest ones (saying how many it looked at out of all) or all of them in counted batches. M12's Memory page and the proofs follow.
- Iteration 0 created the repository, SPEC.md, PROGRESS.md and AGENTS.md. M0 added the code, compose stack and `./check.sh` (see AGENTS.md for how to run them). M1 added the graph core (`src/vrum/graph.py`, the first migration and six MCP tools). M2 added links, readiness and claims (`links.py`, `claims.py`, `common.py` and seven more tools). M3 added rules and coverage (`rules.py` and eight more tools) and, on the owner's instruction, replaced the migrations with the single `schema.sql`.
- M10 was added to `SPEC.md` by the owner after M0. It is the last milestone, after M9.
