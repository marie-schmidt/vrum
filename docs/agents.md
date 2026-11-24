# Connecting an agent

Any MCP client that speaks streamable HTTP can use Vrum. MCP (the Model Context Protocol) is the
standard way for AI coding tools to call a service. A client needs two things: the address of the
server and the key of one project.

- The address is `http://127.0.0.1:8000/mcp`. It is on the same port as the board. Change the port
  with `VRUM_PORT` in `.env` (see [configuration](configuration.md)).
- The key belongs to one project. It reaches that project and no other.

## Make a key

1. Open the project's board and choose **Settings and tools**, then **Users**, in the sidebar. The
   keys are in the **Agents** section.
2. Name the key for the agent that will use it, such as `api-agent`, and choose what it may do
   (it starts as everything; see [What a key may do](#what-a-key-may-do)). Then choose **Make an
   agent key**.
3. **Copy the key now.** It is shown once. Vrum keeps only a hash of it, so a lost key cannot be
   shown again: revoke it and make another.

## Who the agent is

A key is an agent's identity. Vrum records everything that agent does (claims, changes, notes,
handoff notes, history) under the name of its key, and shows it on the board as an agent, next to
the people. The agent never says who it is: the tools take no name, and a name sent anyway is
ignored. So make one key for each agent: two agents that share a key are one agent in the history,
and no two keys of a project that are in force can have the same name. Revoking a key ends that
agent's access on its next request, even on a connection that is already open, and what it did
stays in the history under its name.

People are the other kind of identity: whoever is signed in on the board, shown by the name on
their account. Only a person can confirm or un-confirm a note: an agent's call to
`set_confirmation` is refused, so an agent that wants a note confirmed asks a person to do it on
the board. A note that names a person other than the one making the call (who decided it, who
changed their mind, who reviewed it) still takes that person's typed name, because they
may have no account, such as a client or an officer.

Keep keys out of files you commit. Every example below reads the key from the `VRUM_AGENT_KEY`
environment variable.

## What a key may do

Vrum has more tools than an agent needs, and an MCP client allows only so many (Cursor, for one,
caps its tools at 40 across all the servers it is connected to). So an agent key is offered a
chosen set of at most 25 tools unless its owner chose otherwise: the work loop (`next_work`,
`claim_node`, `renew_claim`, `set_check`, `finish_work`, `changes_since`), reading and changing
items and plans,
links, acceptance criteria, notes, `recall` and `search`. Everything else is done by a person on the board:
histories, moving an item, confirming a note, answering an agent's question, approving a drafted
plan, lifting a gate, accepting a suggested move, what is waiting for a person, who is working on
what, the coverage report, setting
the project's definition of done, and moving data (export, import and rebuilding the index). The board's **Tools** page marks
which tools an agent is offered.

On the Users page the owner can narrow a key further when making it, and change it later: an agent
that only reviews can be given the tools that read, and one that does day-to-day work the tools for
work, claims, acceptance criteria and notes. The owner can also name tools outside the standard set for a
key, for example `export_graph` and `import_graph` for the key a backup script uses, or choose
**Everything, including administration**.

- The agent sees only what its key may use: its tool list holds just those tools, so it does not
  even know about the others. A call to a tool outside the list is refused in plain words, such as
  "This agent key (review-agent) is not allowed to use create_node. Ask the project's owner to
  change what the key may do, on the Users page. Nothing was changed.", and nothing is written. A
  standard key that asks for a tool outside the set is told that it is done on the board, by a
  person.
- A key with a list of its own may use exactly that list. A tool added to Vrum later is not in it,
  so such a key never gains a power by an upgrade. A key made without a choice may use the
  standard set, which can grow with new agent tools, but never beyond 25.
- It is checked on every request, as the key itself is: when the owner changes what a key may do,
  it applies from that agent's next request, even on a connection that is already open.
- A tool that only a person can use (`set_confirmation`, `set_definition_of_done`, `answer_ask`,
  `approve_plan`, `request_plan_changes`, `remove_gate` and `decide_placement`) cannot be given to
  any key.
- Only the project's owner changes it. The board's own calls are a person's and are limited by
  their role in the project, never by a key.

## Calling by running code

A key can call Vrum in one of two ways, and the owner chooses when making it (the choice is on the
Users page, under **How it calls Vrum**, and it can be changed later with **Change what it may
do**).

- **One tool at a time** is what is described above: the client lists the key's tools and the
  agent calls them, one call for each step.
- **By running code** gives the agent two tools, `explore` and `run`, and nothing else. The agent
  writes a short script in TypeScript, and every tool the key may use is a function of an object
  called `vrum` inside it: `vrum.list_nodes({ query: "status:ready" })` is the very call the tool
  `list_nodes` makes, with the same arguments and the same result. A script can loop, filter,
  combine several calls and keep only what it needs, so the agent spends fewer calls and fewer
  words on a day's work. Which way suits which model is for the owner to find out, which is why it
  is a choice for each key and not for the whole install.

`explore` says what a key can do. Called with nothing it lists the key's functions, one line each;
with `names` it gives the TypeScript types of those functions, with each result's exact shape;
with `query` it gives the types of the functions that match the words. `run` takes the script as
`code` (the body of an async function; `vrum` and `console` are in scope; `return` the answer as
JSON) and an optional time limit in seconds. It answers with `result`, the lines the script logged
and the list of calls it made. A client that cuts long tool descriptions still works: the
description of `run` says to call `explore` first.

```ts
const taken = await vrum.next_work({});
if (!taken.found || !taken.work) return taken.message;
const ref = taken.work.ref;
await vrum.add_note({
  text: "The slot picker needs the shop's opening hours",
  why: "Found while reading the task: the picker cannot show closed days without them",
  nodes: [ref],
});
await vrum.finish_work({ node: ref, outcome: "blocked", note: "Waiting for the opening hours." });
return { took: ref };
```

What a script is, and is not:

- **Every call is a normal call.** It goes through the same checks as a call from a client: the
  key, what it may use, a secret in any argument is refused, the project, who is doing it (the
  key's name, in the history), version checks, claims and the definition of done. A function the
  key may not use is not on `vrum`, and a call that Vrum refuses throws an error whose message is
  the tool's own sentence. A script may catch it.
- **A script is not one transaction.** Each call is its own. If a script stops half way, what it
  already did stays done. The answer says which calls ran, which one was refused and whether
  anything changed, so look at what is there before running it again. `create_plan`,
  `update_nodes` and `finish_work` are the calls that are atomic on their own.
- **It is checked first.** The script is checked against the types before it runs, so a
  misspelt function or a wrong argument comes back at once, with the line and what was meant
  ("Did you mean `list_nodes`"), and nothing has run.
- **It has limits.** A script runs for 60 seconds unless it asks for more, and at most 120. It may
  make 200 calls, of which 100 may change something. Its answer may be 256 KB and what it logs
  16 KB, and at most four scripts run at once on a server (a fifth is refused at once, in words).
  A script that goes over is stopped with a plain message.
- **It runs in a box.** The script is written by a model, so it is never trusted. It runs in the
  separate `sandbox` service of `compose.yaml`: a container with no network, no files of value,
  none of Vrum's secrets or database, a read-only disk and limits on memory and processes. Inside
  it the code has no way to read a file, open a connection, read the environment or start a
  program, and it cannot import anything. Vrum looks for secrets in the script and in everything
  the script sends, but that is a best effort, because code can build a string in pieces; the real
  protection is that the script has nowhere to send anything. If the sandbox is not running,
  `run` says so in plain words and the one tool at a time way keeps working.
- **Revoking applies at once.** Every call a script makes looks the key up again, so a key that is revoked, or switched to one
  tool at a time, stops a script that is running at its next call, and a function taken out of the key's allowance is
  refused from then on, as it would be for any other call.
- **Every call is in the log.** The server's log has a line for each call a script makes, with the
  run's number, the order, how long it took and whether it was refused, and never what the
  script said, sent or got back.

A key that runs code cannot call a tool by its name any more (it is told to call it as
`vrum.<name>(...)` inside `run`), and a key that works one tool at a time cannot call `explore` or
`run`. Neither is a loss of power: the allowance of the key decides what exists in both ways, so a
key that may only read can only read in a script too.

## Sizes and checks

**Size.** Every story and task can have a size, `xs`, `s`, `m`, `l` or `xl`: how much effort it takes
and how much context an agent needs for it, together (1, 2, 3, 5 and 8 points). An agent that plans
gives each one a size (`create_plan` and `create_node` take a size, `update_node` changes it or
clears it) and splits anything bigger than `xl`. An epic has no size: it adds up what
is in it. The filter language has `size:m,l` and `no:size`.

**Checks.** The list lines of the project's definition of done (each starting with a dash) are its
checks, which a person writes on the board under Settings. Each story or task has each check ticked
or not. Before an agent finishes a task as done it does what each check says, such as running the
tests, and ticks it with `set_check`, saying in the evidence how it checked. `next_work` hands over
the checks of the item with its briefing and `get_node` lists them with who
ticked each, when and how. An agent that tries to set an item to done (with `finish_work` or
`update_node`) while a check is unticked is refused, and the refusal names the checks; it can still
release or block the work. A person is never refused: they decide, and they tick on the board.

A check that only a person can judge starts its words with `[person]`, as in
`- [person] Looks right on a phone`. An agent can neither tick nor untick it: `next_work` marks it
`person_only`, and the agent finishes the item as `blocked` with `finish_work`, saying in the
handoff note what the person should look at. The person ticks it on the board and marks the item
done. `update_node` takes `why` with a change of status to `blocked` or `dropped`, and keeps it as a
handoff note on the item.

**Verifiers.** The owner can mark an agent's key as a *verifier* on the Users page (**Verifies other
agents' work**). A check whose words start with `[verifier]` (`- [verifier] Reviewed by someone
else`) can then be ticked or unticked only by a person or by a verifier that did not do the work:
an agent that ever claimed the item or handed it off cannot check it, and an agent whose key is not a
verifier's cannot either. `next_work` marks such a check `verifier_only`. The agent that did the work
finishes the item as `blocked`, saying in the handoff note that it is ready for a verifier; the
verifier, who does not claim the item, finds it with the filter `is:unverified` (blocked work nobody
holds that has such a check unticked), ticks its checks with `set_check`, saying how it checked, and
sets the item to done with `update_node`. A check that starts with `[github]` is the same, and the
verifier's evidence must start with the address of the GitHub pull request
(`https://github.com/owner/repo/pull/12`), then say what it saw there. **Vrum never calls GitHub and
holds no GitHub login:** the verifier looks with the GitHub command line (`gh`) on its own machine,
as Beads' gates do, and Vrum records what it reported, by its name, with the address, so that anyone
can look again. `scripts/verify_pr.py` does this: with `VRUM_AGENT_KEY` set to the verifier's key,
`uv run python scripts/verify_pr.py http://127.0.0.1:8000/mcp task:40
https://github.com/owner/repo/pull/12` runs `gh pr view`, and ticks the item's `[github]` checks
only when the pull request is merged and every check passed, saying why not otherwise (and
`--allow-no-checks` accepts a merged pull request that has no checks). The address is the last
argument and is optional: left out, the script uses the pull request the worker said the item is
in (see [Where the work is](#where-the-work-is)), and when the item says none it exits and asks
for an address. A person can tick any of these on the board.

## How big a briefing can be

Nothing an agent is handed grows without bound, and each answer says what it left out.

- **`next_work`** returns the item itself, its own title and details, which are never cut because
  they are the work, and a `context` around it: the acceptance criteria that apply, the definition of
  done, notes, handoff notes, questions, links, `discovered_from` and look-alikes. The context is cut
  to `context_chars` characters, 10,000 by default and from 1,000 to 1,000,000, plus a few hundred
  characters of envelope. What did not fit is counted in `left_out` and said in `cut_note`, which also
  says how to ask for more. The cut is made by whole entries, a section at a time, so a line is never
  half given.
- **`recall`** returns at most `max_chars` characters of entries, 40,000 by default and from 1,000 to
  1,000,000, plus the first entry of each section, so that reading on always moves forward even when
  one note is longer than the limit. A section that was cut says so in `left_out` and `cut_note` and
  gives a `next_after_id`: read on with `show` set to that section and `after_id` set to it, and every
  entry arrives exactly once.
- **Measured.** On the biggest item the checks can make (a task with long details, 70 long acceptance
  criteria, 80 long notes, 25 `discovered_from` links and 20 handoffs that each asked a question) the
  whole answer of `next_work` with the defaults was about 20,000 characters, of which about 9,500 were
  the item's own text, and a `recall` of it was about 530,000 characters before the limit and is under
  50,000 now. `tests/test_briefing_ceiling_integration.py` keeps those numbers honest.

## Where the work is

Vrum has no idea of a checkout or a worktree and never reads one: it runs in a container, and the
agent's files are on the agent's machine. But the agent can say where its work is, in plain
optional words, and Vrum keeps them with the claim and shows them to the people and agents who
look at the item next. `claim_node` and `next_work` take a repository, a branch, a commit and a
pull request, and a run label and a client name; `renew_claim`, `release_claim` and `finish_work`
take the first four.

```ts
await vrum.claim_node({
  node: "task:40",
  repository: "https://github.com/owner/repo",
  branch: "feature/slot-picker",
  commit: "0123456789abcdef0123456789abcdef01234567",
  run_label: "run-2026-11-22-a",
  client_name: "claude-code",
});
// Later, when the work is in a pull request and ready for a verifier:
await vrum.finish_work({
  node: "task:40",
  outcome: "blocked",
  note: "Everything is built. Ready for a verifier.",
  pull_request: "https://github.com/owner/repo/pull/12",
});
```

- **It is the agent's word, and it is not verified.** Vrum never asks GitHub and never looks at a
  branch or a commit, so what is shown is what the holder said. All of it is optional, and an item
  whose holder said nothing shows nothing.
- **It is checked for its shape, so it is safe to show.** A repository is an `https` address with
  no password, query or fragment. A branch is made of letters, digits and `. _ / + = , # @ -`,
  starts with a letter or digit and holds no `..`, so it can never look like an option to a
  command. A commit is 7 to 64 hexadecimal digits. A pull request is exactly
  `https://HOST/OWNER/REPO/pull/12`. A value that is not what it should be, and a secret in any of
  them, is refused in plain words that never repeat the value, and nothing is changed.
- **Renewing says where the work has got to.** What a renewal leaves out stays as it was. When the
  claim ends, the handoff note keeps what the claim said, with what was said last on top, so the
  next holder is told where the earlier work was, but a new claim starts with only what its own
  holder says.
- **Finish with the pull request.** An agent that hands work over as `blocked` for a verifier
  gives the pull request to `finish_work`, so a verifier starts from it: `get_node` shows it as
  `where`, and `scripts/verify_pr.py` uses it when it is not given an address.
- **A run label is a label, not an identity.** The run label and client name tell two instances
  that hold work for one key apart (`run-2026-11-22-a`, `claude-code`). Who did something is
  still the key's name, in the history, in the notes and on every tick; a label can never stand
  in for it, and it opens nothing: only the key that holds a claim can renew or release it.

## Asking a person

Sometimes work cannot go on until a person decides something: which of two designs to build, whether
a risky change is allowed. An agent that needs a person says so with an *ask*, an optional argument
of two tools it already has, instead of burying the question in a handoff note that nobody collects.
`finish_work` takes it with the outcome `blocked` or `released`, and `update_node` takes it with the
status `blocked`.

```ts
await vrum.finish_work({
  node: "task:40",
  outcome: "blocked",
  note: "The form is built. I need a decision before I can wire it up.",
  ask: {
    question: "Which payment provider should the checkout use?",
    choices: ["Stripe", "Adyen", "Neither"],
  },
});
// A yes or no approval, with the work left free for someone else to take:
await vrum.finish_work({
  node: "task:41",
  outcome: "released",
  note: "Parked until a person says it is fine.",
  ask: { question: "May we drop the old export format?", approval: true },
});
```

- **A question has a few choices or none.** Give two to eight `choices` for the person to pick one
  of, or leave them out for a free answer in words. An approval (`approval: true`) is answered yes
  or no and takes no choices. A question is at most 1,000 characters, and a secret in it is refused.
- **It blocks or it parks.** With `blocked` the work waits for the answer. With `released` the work
  stays free for any agent to take, and the question waits beside it.
- **Each ask has a number**, shown on the board as Q-12, and it is a record that is never edited:
  the question stays exactly as it was asked, and so does the answer.
- **Only a person answers**, on the board, under **Needs a person**. An agent's call to
  `answer_ask` is refused, and the tool is not in any key's list. The person gives one of the
  choices, may add a comment, and is named in the record. Answering work that was blocked by its
  ask sets it back to not started, so it shows as ready again (unless another open ask still
  blocks it).
- **The answer comes back to you.** `next_work` carries the item's questions and answers as
  `asks`, so does `get_node`, and the answer is an event in `changes_since`. An answer is final:
  the way to change one is to ask again.
- **Following the feed.** `changes_since` gives a `cursor`: pass it back unchanged next time to read
  only what is new. While `truncated` is true there is more to read, so ask again at once; when it
  is false, wait `poll_after_seconds` before asking again.

A person sees every open ask, with the other things only a person can settle (checks marked
`[person]`, notes waiting for confirmation on work in progress, and claims that ran out twice), in
one list on the board. Anything that is only information stays in Activity.

**What each row says about itself.** Every row of `needs_a_person` carries three fields that the
server works out (a client never has to guess them), so the same rows can be put together in the
way a person finds easiest:

- `level` says how strategic the row is: `plan` for a drafted plan, `note` for notes waiting for
  confirmation, and otherwise the kind of work the row is about, `story` or `task`. A suggested move
  is `story` when it moves any story and `task` when it moves only tasks.
- `decision` says what a person is asked to do: `approve` (a plan, a suggested move, notes, or a
  check only a person can tick), `answer` (an ask) or `look_into` (a refused approval gate, or a
  claim that ran out twice).
- `topic` says what the row is about, so that rows about the same thing can be settled together:
  `{ id, label, basis }`, or `null` for a row about nothing the project can place under an epic.
  Rows with the same `id` are about the same thing. `basis` is `meaning` when the rows were put
  together because the work they are about is alike, even under different epics, and `epic` when
  they only share the epic they are under. `id` is the epic's reference (`epic:3`) when an epic
  names the topic and `topic:7` when a piece of work does, and `label` is what to call it. A
  group put together by meaning that is mostly under one epic takes that epic's `id`, so rows of
  both bases can carry the same `id`. Meaning
  is read from the vectors the index already holds, with no language model and no call to the
  embedding service, so with search by meaning not set up, or before the index has reached the
  work, every row takes its epic.

The answer's `groups` are still by story, with plans and suggested moves in groups of their own.
The board's **Group by** choice (level, topic or decision) regroups the flat rows by these three
fields and by nothing else.

```ts
// What waits for a person, by topic: the rows about the same thing together.
const needs = await vrum.needs_a_person();
const topics = new Map<string, { label: string; basis: string; rows: string[] }>();
for (const group of needs.groups) {
  for (const row of group.items) {
    const topic = row.topic ?? { id: "other", label: "Other", basis: "epic" };
    const found = topics.get(topic.id) ?? { label: topic.label, basis: topic.basis, rows: [] };
    found.rows.push(`${row.level} to ${row.decision}: ${row.node}`);
    topics.set(topic.id, found);
  }
}
return [...topics].map(([id, found]) => ({ id, ...found }));
```

### Showing that list in a chat that draws views (MCP Apps)

Some hosts (Claude, VS Code and others that speak MCP Apps) can draw a small page beside the chat
when a tool names one. `needs_a_person` does: allow a key that tool (Users, then Agents, the key's
list) and the host shows the list by story, each row with how long it has waited, and a button that
opens the board at the right page. The view only reads. A person still answers on the board, under
their own name, because the chat holds an agent's key and an agent cannot answer for a person. A
host that does not draw views gets the same list as plain words. The view is one page that loads
nothing from anywhere and holds no address; the board's address in it is the server's own
`VRUM_PUBLIC_URL`. A host that runs in the cloud cannot reach a server on this machine, so for those
the server needs an address they can reach.

## Work you find along the way

An agent that meets something else while it works (a bug, a missing piece) creates it as its own
item and links it with `discovered_from`: the new item is the `source` and the work it was found
while doing is the `target`. It is a note about where work came from and nothing more: it never
makes anything wait (an item with only a `discovered_from` link is still ready to take), and it is
one way, so the other direction is a link of its own. The briefing `next_work` gives for the new
item lists what it was found from, under `discovered_from`, so whoever picks it up knows. On the
board it is shown as "discovered from" at the new item and "led to" at the other.

```ts
const bug = await vrum.create_node({
  kind: "task",
  title: "Rounding is wrong for 3-day rentals",
  parent: "story:12",
});
await vrum.add_link({ source: bug.ref, target: "task:40", link_type: "discovered_from" });
```

## Gates, draft plans and templates

Three things hold work or shape a plan when something outside the graph has to happen first. A
gate holds one story or task, a draft plan holds a whole plan until a person decides, and a template
is a plan kept as data so that one call makes it. None of the tools that add them is in the
standard set of 25: the owner allows them one by one on the Users page. The ones that decide for a
person (`approve_plan`, `request_plan_changes` and `remove_gate`) are done only by a person, on the
board.

**A gate.** `add_gate` holds a story or task until a time comes or a person says yes.

```ts
// Hold a task until a day (a UTC day, or a time such as "2026-12-03T09:00"):
await vrum.add_gate({ node: "task:40", kind: "timer", not_before: "2026-12-03" });
// Hold a task until a person approves:
await vrum.add_gate({
  node: "task:41",
  kind: "approval",
  question: "May we change the billing schema before the audit?",
});
```

- **A timer** takes `not_before`, a day (the start of that UTC day) or a time, in the future and at
  most three years ahead; a time that has passed, or that Vrum cannot read, is refused. The server
  fires nothing: when the time comes the hold simply stops counting and the item is ready again.
- **An approval** takes a `question` and raises it as a question for a person (Q-12 on the board's
  **Needs a person** page). A yes opens the gate. A no leaves it refused: the work stays held, and
  the refusal is a row of its own on that page until a person lifts the gate. Only a person
  answers, and an agent never opens an approval gate.
- **Held work is not offered and cannot be claimed.** `ready_work` and `next_work` leave it out
  (`next_work` says it is held by a gate), `claim_node` refuses it in words, and the filter
  `is:held` finds it. `get_node` lists what holds an item in `held_by` (`gate:5`, or `plan:3` for a
  draft plan) and each gate in `gates`, with a sentence of its own that says what it waits for or
  how it ended.
- **Only a person lifts a gate**, on the item's page (`remove_gate`, which no key can have). Lifting
  an approval gate that nobody answered also closes its question, answered "Gate lifted". A gate
  can be lifted once.

**A draft plan.** `create_plan` with `draft: true` makes the whole plan and holds every item of it:
no agent is given any of it, none of it is ready or can be claimed, and it counts as not started.
Give it a `title` (the first item's title is used when you do not). The plan is **one row** on the
**Needs a person** page, with its title, how many epics, stories and tasks it has and who proposed
it, and a person decides it once: approving frees every item at once, and rejecting needs a reason
and drops every item, each with a handoff note, "The plan was rejected: <the reason>". Approving
and rejecting are final, and `list_plans` reads the plans with how they ended. A person may also
ask for changes instead, which keeps the plan held (see "Revising a plan after feedback" below). A
plan made without `draft` is as it always was.

**Reading a plan whole.** A list of plans gives each plan's counts and the references of what it
made (at most 50). `list_plans` with `plan`, a plan's number, reads that one plan whole instead:
`tree` holds every item it made, with no cap, parents first, each with its details (`body`),
priority, size, dates, the links that start at it and its acceptance criteria that are not
dropped; `context` holds the items that already exist and that the tree hangs under or links to,
each with why it is there (`parent` or `linked`), epics first. This is the read the board's
**Needs a person** page draws, as a diff of added lines, for the person who decides. It is what a
harness or a script should read, and check, before it asks a person to approve: an item with no
size, a story with no criterion, a link to something that is not there. It reads the same after the
plan was approved or rejected, and a plan that does not exist is refused in words. A plan that was
revised also carries its revisions and the feedback on them (see "Revising a plan after feedback"
below): `tree` is then the current revision, and `revision` reads an earlier one as it was
submitted. Like any other tool, it must be in the key's allowance.

**The checks of a plan.** For a plan that still waits for a person, the same read also carries
`checks`: always three, in this order, worked out from the graph when you ask and never stored. Each
has a `kind`, a `state`, one plain sentence in `words` and `findings`, each finding naming the items
of the plan it is about (by reference) and, for a possible duplicate, the existing work it looks
like. A check never stops or decides anything: Approve and Reject work as they always did whatever
the checks say, and a warning is for the person to weigh.

- `circle`: whether the plan's `needs_first` links make a circle of waiting. The state is `ok` or
  `failed`. The database refuses to make a circle (`add_link` and `create_plan` say so), so `failed`
  is practically unreachable from outside; the check reads the same rule again and names the items.
- `unsized`: the stories and tasks with no size, one finding for each. It is the filter `no:size`
  and nothing more, and it never guesses a size. The state is `ok` or `warning`.
- `duplicate`: the epics and stories of the plan that look like work that already exists, by
  meaning, from the same index and the same threshold (0.55 unless the owner changed it) that
  `find_overlaps` and the check at `create_node` use. Each finding gives up to three existing items
  nearest first, each with its `status` and its `similarity` from 0 to 1. The state is `ok`,
  `warning` or `unavailable`.

**`unavailable` is not "no duplicates".** When meaning search cannot be used (no embedding service,
an index that is not built, a service that cannot be reached) the duplicate check says so in
`words` and has no findings, and its state is `unavailable`. Treat it as "not checked", as loudly as
a warning, and never as a clean result. `checks` is `null` in a list of plans, for a plan that was
already approved or rejected, for a plan that waits for its agent to revise it, and for an earlier
revision: it is only worked out for the current revision of a plan that waits for a person. A
harness or a script should read the checks before it asks a person, and fix what it can (give the
tasks a size, reword or drop a story that repeats work) or say what it left and why.

```ts
// Before asking a person to approve the drafted plan 1402, say what the checks found.
const { items } = await vrum.list_plans({ plan: 1402 });
const plan = items[0];
if (!plan) return "There is no such plan.";
// checks is null once the plan is decided, so read it as empty then.
const checks = plan.checks ?? [];
return checks
  .filter((check) => check.state !== "ok")
  .map((check) => ({
    check: check.kind,
    state: check.state,
    words: check.words,
    items: check.findings.flatMap((finding) => finding.items),
    like: check.findings.flatMap((finding) =>
      finding.related.map((one) => `${one.ref} ${one.title} (${one.status})`),
    ),
  }));
```

```ts
// Read the drafted plan 1402 whole, and say what a person should be told before deciding.
const { items } = await vrum.list_plans({ plan: 1402 });
const plan = items[0];
if (!plan) return "There is no such plan.";
// tree and context are empty in a list and full when one plan is asked for.
const { tree, context } = plan;
return {
  title: plan.title,
  state: plan.state,
  items: tree.length,
  hangsUnder: context.filter((one) => one.why === "parent").map((one) => one.ref),
  unsized: tree.filter((one) => one.kind !== "epic" && one.size === null).map((one) => one.ref),
  storiesWithNoCriterion: tree
    .filter((one) => one.kind === "story" && one.criteria.length === 0)
    .map((one) => one.ref),
};
```

**Revising a plan after feedback.** A person does not have to approve or reject a drafted plan: on
the board they can **Request changes**, with a summary and, if they like, comments on the lines of
the plan. A comment is about one item, or one part of it: its title, details, size, priority, start
or due day, where it hangs, its links or its acceptance criteria. Asking for changes is not
rejecting. The plan keeps its number and all of its items stay held (nothing is dropped, nothing is
freed), and it moves from `proposed` (it waits for a person) to `changes_requested` (it waits for
you). Rejecting still ends a plan, with its reason, in either state.

- **How you learn of it.** The change feed carries a `plan_changes_requested` event (who asked, the
  summary and the comments), a webhook of the kind `plan.decided` is sent, and `list_plans` with
  `status: "changes_requested"` lists the plans that wait for a revision. `get_node` says an item
  is held by the plan, in `held_by`.
- **What the plan says.** `list_plans` with `plan` reads the plan whole and now also gives
  `revision` (the first draft is revision 1) and `revisions` (one entry for each: who made it and
  when, the `note` its author gave, how many items it held, the items it took out, and the number
  of the feedback a person gave on it); `feedback` (each request, oldest first: who asked, when, on
  which revision, the summary and the comments); and `changes`, how the revision shown differs from
  the one before it (items `added`, `removed` or `changed`, with the old values of what changed in
  `before`). Each comment names its `item` and its `field` (empty when it is about the item as a
  whole) and carries `quoted`, the words it was about as they were in that revision, so that a
  later change can never attach a comment to other words. Give `revision` to read an earlier
  revision exactly as it was submitted: its `tree`, its `changes` and the feedback on it do not
  change when the plan is revised. A list of plans gives each plan's `revision` and when it was
  revised and none of the rest.
- **Sending a revision.** `revise_plan` takes the plan's number, the whole plan as it should be now
  in the words `create_plan` uses, and a `note` that says what changed. An item that continues one
  the plan has gives its `ref`; an item without a `ref` is new; an item the plan has that no entry
  continues is **removed**: it is dropped, with a handoff note, "Removed in revision 2 of Plan
  1402: <your note>", and no approval can free it later. A reviewer never removes an item: they
  ask, and you remove it. Acceptance criteria continue by `id` in `rules`; one that no entry
  continues is dropped, and a criterion stays on the item it was on. `links` and `rules` left out
  keep what the kept items have; given, they are the whole set. Every other part is as you send it,
  and a part you leave out takes its default (a size left out clears the size), because a revision
  says what the plan is and not what changed: send every part of every item.
- **A revision is checked as a whole, and a refused one changes nothing.** It meets the checks a
  new plan meets (hierarchy, sizes, dates, links, circles of waiting) and the rules of revising:
  what you remove cannot still be referred to, cannot be waited for by work outside the plan and
  cannot have live work outside the plan under it; an item cannot change kind; a criterion cannot
  move to another item. Every problem is listed at once, with "Nothing was changed". An accepted
  revision is made in one step, keeps the plan's number, and brings it back to `proposed`: a person
  reads what changed since the revision before and approves it, asks for changes again or rejects
  it. Anyone who may call `revise_plan` may revise any waiting plan, not only the one that drafted
  it, and the history says who made each revision.
- **A person never edits a plan.** They ask for the change and you make it. A decision applies only
  to the revision the person reviewed: the board sends the revision with Approve and with Request
  changes, and a plan that has a newer one is refused in words, with nothing changed. Approving
  frees only the current revision's items, so what a revision removed stays dropped, and a plan that
  waits for its agent cannot be approved ("there is nothing to approve"). If an item of a held plan
  is changed by another tool after the revision was submitted (its title, details, priority, size,
  dates, parent, a link that makes work wait, an acceptance criterion, or dropping it), `list_plans`
  names it in `drift`, with what changed, and the server refuses to approve the plan until a
  revision says what the plan is now (rejecting is never refused).
- **What a key needs.** `list_plans` and `revise_plan` are not in the standard 25 tools: the owner
  allows them for the harness that drafts and revises plans, on the Users page. Asking for changes
  and approving are a person's, and no key can have them.

```ts
// Plan 1402 waits for its agent. Read what the person asked, then send the whole plan again with
// the title they commented on reworded and every other item continued as it is.
const { items } = await vrum.list_plans({ plan: 1402 });
const plan = items[0];
if (!plan) return "There is no such plan.";
const asked = (plan.feedback ?? []).at(-1);
if (plan.state !== "changes_requested" || !asked) return "Nothing to revise.";
const comment = asked.comments.find((one) => one.field === "title");
if (!comment) return `No comment on a title. They asked: ${asked.summary}`;
// A key is only a name inside this call, and it must not look like a reference.
const key = (ref: string) => ref.replace(":", "-");
const inPlan = new Set(plan.tree.map((one) => one.ref));
// The new words are the agent's to decide: here they are written out.
const newTitle = `${comment.quoted}, with the stations named`;
const revised = await vrum.revise_plan({
  plan: 1402,
  note: `Reworded the title of ${comment.item}: ${asked.summary}`,
  items: plan.tree.map((one) => ({
    key: key(one.ref),
    ref: one.ref,
    kind: one.kind,
    title: one.ref === comment.item ? newTitle : one.title,
    body: one.body,
    priority: one.priority,
    size: one.size,
    start_on: one.start_on,
    target_on: one.target_on,
    parent: one.parent && inPlan.has(one.parent) ? key(one.parent) : one.parent,
  })),
});
return { revision: revised.revision, removed: revised.removed, counts: revised.counts };
```

**A template.** A template is data and never code: a named plan with variables and `{{name}}`
placeholders in titles, details, parents and the ends of links. A variable is a line of text,
optionally limited to a list of `choices`, a length or a pattern, or the reference of an epic or
story that exists (`type: "ref"`). A pattern is deliberately small, one character class and one
repeat such as `^[0-9.]+$`, so that no template can make the server do unbounded work.
`save_plan_template` checks the whole template and lists every problem at once, and saving the same
name again makes a new version (every version stays in the history). `list_plan_templates` lists
them (give a `name` to read one whole) and `delete_plan_template` deletes one. `create_plan` with a
`template` and `values` fills it in, checks the result exactly as it checks a plan written out by
hand, lists every problem at once if there are any, and makes the plan, as a draft when asked.

```ts
await vrum.save_plan_template({
  name: "release-checklist",
  template: {
    description: "What a release needs",
    variables: { version: { description: "The version, such as 2.4", pattern: "^[0-9.]+$" } },
    items: [
      { key: "rel", kind: "epic", title: "Release {{version}}" },
      { key: "tag", kind: "story", title: "Tag {{version}}", parent: "rel", size: "xs" },
    ],
  },
});
// Later, a plan from it that waits for a person to approve:
await vrum.create_plan({
  template: "release-checklist",
  values: { version: "2.4" },
  draft: true,
  title: "Release 2.4",
});
```

A person can do the same on the board: the **Plan templates** page (under **Settings and tools**)
lists the templates and makes a plan from one, and a drafted plan waits on **Needs a person**.
Everything here travels in an export and an import (see [backup and export](backup-and-export.md)).

## Being woken by a webhook

If you run a dispatcher or an agent harness that should start work when something happens, you do
not have to poll `changes_since` every few seconds. The project's owner can add a **webhook** on the
**Users** page: an address that Vrum sends a small signed message to, for the kinds of news the
owner chooses. A key cannot add one: it is the owner's setup, done on the board. The owner is shown
the signing secret (`whsec_` and a long random text) once, when the webhook is made, and gives it to
whatever receives the messages. The server can also mail a person the same kind of news (the
**Account** page); see [configuration](configuration.md#webhooks-and-email-nudges) for both.

**It is only a nudge, and the change feed stays the source of truth.** A message says that
something happened, not what it was. Read what you need from Vrum with your key: call
`changes_since` with the cursor you saved, and for `work.available` call `ready_work`. A receiver
that missed a message, or was down, loses nothing by reading the feed when it starts.

The kinds of news, which the owner chooses one by one:

- `ask.raised`: an agent put a question or an approval to a person.
- `ask.answered`: a person answered one.
- `plan.proposed` and `plan.decided`: a drafted plan, or a new revision of one, waits for a person,
  or a person approved or rejected it or asked for changes to it (a harness that waits for feedback
  wakes on `plan.decided`).
- `placement.proposed` and `placement.decided`: the same for a proposal to move work.
- `work.blocked` and `work.done`: a story or task was blocked, or finished.
- `work.available`: work that may be ready to take changed. It is not a list of what is ready, and
  it comes at most once every ten seconds however much changed, so call `ready_work` to see.
- `claim.lapsed`: a claim ran out with nobody renewing or releasing it. It is a time passing and
  not an event, so its message has no event number.

A message is a small JSON text and holds only numbers and references, never a title, a question or a
note, so a receiver that leaks or is tricked tells no one what the work is:

```text
{
  "type": "ask.raised",
  "timestamp": "2026-11-23T09:14:02Z",
  "data": { "project": 12, "event_id": 4081, "node": "task:40", "object": "ask:4081" }
}
```

`timestamp` is when it happened, `project` is the project's number, `event_id` the number of the event in the project's history (the
same number `changes_since` gives, or null for a lapsed claim), `node` the story or task it is about
(null for a plan or a proposal), and `object` the question, plan or proposal by number (`ask:12`,
`plan:3` or `placement:7`, or null).

**Checking a signature.** Messages are signed exactly as the
[Standard Webhooks](https://www.standardwebhooks.com/) specification has it, so any library for it
checks them, given the raw body of the request and its headers: `webhook-id` (the same through every
retry of one message, so it is what to deduplicate on), `webhook-timestamp` (seconds, the time of the
attempt) and `webhook-signature` (`v1,` and the base64 of an HMAC-SHA256 over
`<webhook-id>.<webhook-timestamp>.<body>`, keyed with the secret after `whsec_` decoded from base64;
there are two signatures separated by a space while an owner is replacing the secret, and the
message is good if either fits the secret you hold). Refuse a message whose timestamp is more than
five minutes from your clock.

```ts
import { Webhook } from "standardwebhooks";

const verifier = new Webhook(process.env.VRUM_WEBHOOK_SECRET!); // the whsec_ secret

export async function receive(request: Request): Promise<Response> {
  const body = await request.text(); // the raw text, not parsed and printed again
  let news: { type: string; data: { project: number; node: string | null } };
  try {
    news = verifier.verify(body, Object.fromEntries(request.headers)) as typeof news;
  } catch {
    return new Response("That is not signed with the secret.", { status: 400 });
  }
  // Answer quickly, then do the work: a receiver that takes longer than ten seconds has failed.
  queueMicrotask(async () => {
    if (news.type === "work.available") {
      // Read ready_work with your agent key, and start an agent for what is free.
    } else {
      // Read changes_since from the cursor you saved, and look at what is new.
    }
  });
  return new Response("ok");
}
```

- **Answer with a 2xx within ten seconds.** Any other answer, a timeout and a redirect (which is
  never followed) count as a failure. The message is sent again after 10 seconds, 1 minute,
  5 minutes, 30 minutes and 2 hours and then given up on, six attempts in all, always with the same
  `webhook-id`. The owner's Users page shows the webhook as failing, with what went wrong in words,
  until a message gets through, and what each message did.
- **The address has to be `https` and public.** Vrum refuses an address that is this machine, on a
  private network, or a metadata address, also when a name resolves to one. On your own network the
  person who runs the server can open the names you list (see the configuration page).
- **Replace the secret without losing a message.** Replacing it on the Users page makes a new one
  and keeps the old one signing too for a day, so a receiver can change over.
- **A webhook gets news only from when it was added** (and not what happened while it was turned
  off), so read `changes_since` once when you start.

## Sorting unsorted work

Work that arrives with no epic (an import, a quick note) waits in an epic called **Inbox**. The
convention is only that: an epic with that name, and nothing in the schema knows it. Two things
help put it where it belongs.

`suggest_parent` is a read. Give it a saved story or task (`node`), or the `title` and `kind` of
work that is not saved yet, and it ranks the epics (for a story) or stories (for a task) that the
work most resembles, nearest first, each with a `score` and the two items under it that the work is
most like, which is the reason. `sure` is true when the best is clearly ahead of the second (by
0.05). Every answer is a suggestion: on the demo project, with the built-in model, the right epic
was first about three times in four and in the first three about nine in ten, and when it was sure
it was right about nine times in ten. The Inbox, and the stories in it, are never offered. It needs
search by meaning. To measure it on your own project, export it and run
`uv run python scripts/parent_accuracy.py --export vrum-export.json` (it uses the embedding
service the server uses, or `--base-url` and `--model` for another).

`propose_placement` is how an agent asks for a move without making it: 1 to 20 stories or tasks
(all of one kind), a `reason`, and either a `parent` that exists or a `new_epic` (title and body) to
group stories under. Nothing moves. The proposal is a row on the board's **Needs a person** page,
under **Suggested moves**, and a person accepts it, which makes the moves as that person and makes
the new epic only then, or rejects it with a reason. An item that has moved or finished since is
left out and the person is told. You may have 25 proposals waiting, and an item can be in only one.
`list_placements` shows them with how they ended.

**What a proposal says about itself.** Read with `status: "proposed"`, `list_placements` also works
out, for each proposal that still waits, how well each item fits the parent it was proposed for:
`scores`, one for each item, in the order of `items`, and `routine`. A score's `similarity` (0 to 1)
is exactly the `score` that `suggest_parent` gives that item for that parent, so it is the same
ranking and the same model, and `rank` says where that parent comes in the list (1 is the best),
`best` which parent comes first and `sure` whether the best is clearly ahead of the second. A
`warning` is something to weigh (the item is held by an agent, is being worked on, has moved since
or is finished) and a `note` says in words why an item has no score. `scores_note` says why there
are no scores at all: meaning search is not possible now, or the proposal makes a new epic, which
has no parent to score against. A score is evidence for a person and never approval: nothing moves
and nothing is decided because of it.

`routine` is true only when all of it agrees: every item has a score, none has a warning, and the
proposed parent ranks first for every one of them, clearly ahead of the next. `routine_why` says
why or why not in one sentence ("E-3 is the clearly best parent for all 3 items." or "E-3 ranks 2nd
for S-9."). A proposal that makes a new epic is never routine. The board draws a routine proposal
in a collapsed group with its count, and every one in it is still a full card a person can decide;
grouping never hides a warning. The scores are worked out when `list_placements` is read, so they
are `null` (and `routine` too) for a proposal that was decided, and in the `placement` of
`needs_a_person`: read `list_placements` for them, once for all the proposals that wait.

```ts
// The proposals a person would have to look at twice: not routine, or with something to weigh.
const { items } = await vrum.list_placements({ status: "proposed" });
return items
  .filter((one) => one.routine === false)
  .map((one) => {
    const scores = one.scores ?? [];
    const known = scores.flatMap((score) => (score.similarity === null ? [] : [score.similarity]));
    return {
      proposal: one.id,
      why: one.routine_why,
      lowest: known.length ? Math.min(...known) : null,
      weigh: scores.flatMap((score) => (score.warning ? [score.warning] : [])),
    };
  });
```

**A steward** is an agent whose whole job is this. `scripts/steward.py` is the plain one, with no
language model: it reads the stories in the Inbox, asks `suggest_parent` for each and proposes (one
proposal for each epic they go to) only where the model is sure and the score is at least
`--min-score`; the rest it leaves for a person.

```
VRUM_AGENT_KEY=<the steward's key> uv run python scripts/steward.py http://127.0.0.1:8000/mcp --dry-run
```

Make its key an ordinary agent key named `steward`, allowed `list_nodes`, `suggest_parent`,
`list_placements` and `propose_placement` and nothing else, so it cannot move, confirm or decide
anything. Vrum has no language model, so naming a new epic for a group of stories is for an agent
that can read them (give it those four tools, `list_nodes` and `find_overlaps`) or a person.

## When a key runs out

The owner of a project can give an agent key a last day, when making it or later (Users, then
Agents, on the project's board), in UTC. Until the end of that day the key works as any other.
After it, the key is refused in plain words on its next request, even on a connection that is
already open, and the refusal says when it ended and that the owner can extend it or make a new
key. Changing the day to one that has not passed makes the key work again at once, and clearing
it makes it a key that never runs out. A key with no day is untouched: nothing is set by default,
and nothing about it is shown. An expired key stays on the Users page, marked as expired, and what
it did stays in the history under its name.

## Configure a client

**Claude Code** keeps the key in its own settings, so do this where only you can read them:

```
export VRUM_AGENT_KEY=<the key from the Users page>
claude mcp add --transport http vrum http://127.0.0.1:8000/mcp --header "Authorization: Bearer $VRUM_AGENT_KEY"
```

**A project file** that any of the clients below can read, as `.mcp.json` at the root of the
project. It holds no key: each client reads `VRUM_AGENT_KEY` from the environment.

```json
{
  "mcpServers": {
    "vrum": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer ${VRUM_AGENT_KEY}" }
    }
  }
}
```

**GitHub Copilot** (the CLI and VS Code) reads the same `mcpServers` shape from
`~/.copilot/mcp-config.json` (for all your projects) or from `.mcp.json` in the workspace, with an
added list of the tools to allow:

```json
{
  "mcpServers": {
    "vrum": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer ${VRUM_AGENT_KEY}" },
      "tools": ["*"]
    }
  }
}
```

**From Python**, with the official MCP SDK (this is what the scripts in `scripts/` and the checks
use; see `scripts/vrum_client.py`):

```python
import asyncio, os
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client


async def main() -> None:
    headers = {"Authorization": f"Bearer {os.environ['VRUM_AGENT_KEY']}"}
    async with (
        create_mcp_http_client(headers=headers) as http,
        Client(streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http)) as client,
    ):
        print(await client.call_tool("list_nodes", {"limit": 1}))


asyncio.run(main())
```

The Python example is run against a real server by `./acceptance.sh`, so it works. The other three
follow each client's own documentation for remote HTTP servers (Claude Code: "Connect Claude Code to
tools via MCP"; Copilot and VS Code: "MCP configuration reference"); the checks do not run them. If
a client does not fill in `${VRUM_AGENT_KEY}` for you, put the key in that client's own user-level
settings rather than in a file you commit.

If a client says it cannot connect, the server's answer says why: a missing, wrong or revoked key
is refused in plain words. Passwords and keys travel as plain text over HTTP, so use this on one
machine, or put Vrum behind something that adds TLS (see the limitations in the
[README](../README.md#limitations)).

## Try it

Ask the agent to call `list_nodes`, then to take the next piece of work with `next_work`. The
task it takes shows as held on the Board, and the claim and the handoff note it leaves when it
finishes appear on the **Activity** page. Every tool describes itself: its description says what
it does, what it needs and what it refuses, and the client lists them all. The board's **Tools**
page (also under **Settings and tools**) shows the same list, read from the server.

## The agent guide

This is the text the server hands to every agent when it connects (its MCP instructions). It is
the only copy in the documentation, and a check fails if it ever differs from the server's text.

<!-- agent-guide:start -->
Vrum is a project graph. Epics hold stories and stories hold tasks; acceptance criteria and notes belong to or concern them. An epic, story or task is named by its kind and number: `epic:3`, `story:12`, `task:40`. Tools take that reference, never a bare number. A note records what is known or decided, with why; only unconfirmed and confirmed (a person reviewed it) ones are in force: check its status before relying on it. Your agent key names you: never send a name.

The working loop:
1. `next_work` takes the most urgent free story or task and returns it with its acceptance criteria, the definition of done and its checks, notes and handoffs. Keep the claim with `renew_claim`; say where the work is (branch, pull_request) when you finish.
2. Before deciding something that matters, `recall` what is known, or `search`.
3. Record what is decided or learned with `add_note`: say why, and name the person in decided_by when someone decided it. Only a person confirms it.
4. Before finishing, meet the acceptance criteria, do what each check says (run the tests) and `set_check` it, saying how you checked: done is refused while one is unticked. Leave a check marked person_only for a person: finish as blocked, saying what to look at. End with `finish_work`: done, blocked, dropped or released, and a handoff note.

Also: `create_plan` makes a whole plan at once or nothing; give every story and task a size, xs to xl (split anything bigger). `changes_since` shows only what is new, and `list_nodes` takes a filter as its query, such as `kind:task status:ready -claimed OR is:overdue`. `[[story:12]]` in any text refers to an item (also `[[epic:3]]`, `[[task:40]]`, `[[rule:3]]`, `[[note:7]]`). A person does the rest on the board. With only `explore` and `run`, call each tool as `vrum.<name>(...)` inside `run`.
<!-- agent-guide:end -->
