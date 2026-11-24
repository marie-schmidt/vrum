"""Small hand-made files for the importers (M42), written the way the three sources write them.
Shared by the unit tests and the tests against the running containers."""

BEADS = "\n".join(
    [
        '{"id":"bd-1","title":"Checkout","issue_type":"epic","status":"open","priority":1,'
        '"description":"Let people pay."}',
        '{"id":"bd-2","title":"Card payments","issue_type":"feature","status":"in_progress",'
        '"priority":1,"parent":"bd-1","labels":["payments"],'
        '"acceptance_criteria":"- A card is charged once\\n- A failed charge shows a reason"}',
        '{"id":"bd-3","title":"Build the card form","issue_type":"task","status":"closed",'
        '"priority":2,"dependencies":[{"issue_id":"bd-3","depends_on_id":"bd-2",'
        '"type":"parent-child"}]}',
        '{"id":"bd-4","title":"Call the provider","issue_type":"task","status":"open",'
        '"priority":0,"dependencies":[{"issue_id":"bd-4","depends_on_id":"bd-2",'
        '"type":"parent-child"},{"issue_id":"bd-4","depends_on_id":"bd-3","type":"blocks"}]}',
        '{"id":"bd-5","title":"Fix the rounding bug","issue_type":"bug","status":"open",'
        '"priority":3,"description":"Found while building the form.",'
        '"dependencies":[{"issue_id":"bd-5","depends_on_id":"bd-3","type":"discovered-from"},'
        '{"issue_id":"bd-5","depends_on_id":"bd-4","type":"related"}]}',
        '{"id":"bd-6","title":"Wake up","issue_type":"gate","status":"open","priority":2}',
    ]
)

GITHUB = """[
  {"number": 1, "title": "Tracking: sign in", "state": "OPEN", "stateReason": null,
   "body": "We need sign in.\\n\\n- [ ] #2\\n- [x] #3\\n", "labels": [{"name": "priority: high"}],
   "milestone": {"title": "v1"}, "url": "https://github.com/acme/app/issues/1"},
  {"number": 2, "title": "Login form", "state": "CLOSED", "stateReason": "COMPLETED",
   "body": "", "labels": [], "milestone": {"title": "v1"},
   "url": "https://github.com/acme/app/issues/2"},
  {"number": 3, "title": "Password reset", "state": "CLOSED", "stateReason": "NOT_PLANNED",
   "body": "Maybe later.", "labels": [{"name": "low"}], "milestone": null,
   "url": "https://github.com/acme/app/issues/3"},
  {"number": 4, "title": "Dark mode", "state": "OPEN", "stateReason": null, "body": "Please.",
   "labels": [{"name": "enhancement"}], "milestone": null,
   "url": "https://github.com/acme/app/issues/4"},
  {"number": 5, "title": "A pull request", "state": "OPEN", "body": "", "labels": [],
   "milestone": null, "pull_request": {"url": "https://api.github.com/x"}}
]"""

SPEC_KIT = """# Tasks: Photo albums

**Input**: Design documents from `/specs/001-photo-albums/`

<!-- the template's sample tasks are replaced:
- [ ] T999 A task in a comment is not a task -->

## Phase 1: Setup

- [x] T001 Create project structure per the plan
- [ ] T002 [P] Configure linting

## Phase 2: Foundational (Blocking Prerequisites)

- [ ] T003 Set up the database schema (depends on T001)

## Phase 3: User Story 1 - Browse albums (Priority: P1)

### Implementation for User Story 1

- [ ] T004 [P] [US1] Create the Album model in src/models/album.py
- [ ] T005 [US1] Build the album list (depends on T003, T004)

## Phase 4: User Story 2 - Share an album (Priority: P2)

- [ ] T006 [US2] Create share links

## Notes

- Commit after each task
"""
