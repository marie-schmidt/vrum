# AGENTS.md

How to work in this repository. Read `SPEC.md` first, then `PROGRESS.md`.

## Running the checks

The check command does not exist yet. Milestone M0 creates `./check.sh` (lint, types, unit and integration tests) and must replace this section with the exact commands to run the checks and to start the containers (`docker compose up -d`).

## Working rules

- One milestone per iteration: the first unchecked one in `PROGRESS.md`. Never start the next one.
- A milestone is finished only when its "Proof" in `SPEC.md` passes against the real containers and a real MCP client, with every earlier proof still passing.
- Product decisions that `SPEC.md` does not answer go in `BLOCKED.md` (question, 2 to 3 options, a recommendation, the default you would take). Do not invent answers.
- Defaults the owner may veto are recorded in `DECISIONS.md` and listed under "Defaults taken" in `PROGRESS.md`.
- Never commit a secret. Configuration comes from the environment.
- Never touch any Deelio repository or the Taiga project.
- No placeholder or shell code, and no abstraction without a consumer.
- Keep every change small and reviewable. Plain English in every message a person or agent reads.
