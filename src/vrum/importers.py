"""Ways in (M42): turn a Beads export, a GitHub issue dump or a Spec Kit `tasks.md` into one plan
for `create_plan`, so that work that lives somewhere else can be brought into a project.

Each reader is a pure function from the file's text to an `Imported`: the epics, stories and tasks
(as `create_plan` items), the `needs_first` and other links between them, acceptance criteria, and
the status each item had. Nothing here talks to a server: the scripts in `scripts/` do that, after
a reader has accepted the whole file. A reader refuses the whole file when anything does not map
and lists every problem at once (`ImportProblems`), so that a bad file changes nothing.

How things map, the same for every source:

- Vrum has three levels. An epic is an epic; what is directly under an epic is a story; what is
  under a story is a task. Work that has no epic goes under an epic called Inbox (the convention
  for work not yet sorted), as a story. Nesting deeper than that, an epic inside anything, or a
  parent that is not in the file, is a problem and is listed, never guessed at.
- The old identifier is kept in the item's details ("Imported from Beads: bd-12"), so the old
  system can still be found from the new one. Links to the old system are text, not a connection:
  nothing is synchronised afterwards.
- Statuses carry over (open to not started, closed to done). Priorities carry over where the
  source has them.
- Nothing is invented: a size, a date or a link the source does not state is left out."""

import json
import re
from dataclasses import dataclass, field
from typing import Any

INBOX_TITLE = "Inbox"
INBOX_KEY = "inbox"
# Vrum does not read titles longer than this as a headline anywhere; a longer one is cut and the
# whole text is kept in the details.
MOST_TITLE_CHARS = 200
_STATUSES = ("new", "in_progress", "blocked", "done", "dropped")


class ImportProblems(Exception):
    """The file cannot be imported. `problems` lists every reason, one sentence each."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass
class Imported:
    """What a reader made, ready to send: `items`, `links` and `rules` are the arguments of
    `create_plan`; `statuses` is the status each plan key had in the source (only those that are
    not `new`); `left_out` says in words what was not imported and why."""

    source: str
    items: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    statuses: dict[str, str] = field(default_factory=dict)
    left_out: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        kinds = [item["kind"] for item in self.items]
        return {
            "epics": kinds.count("epic"),
            "stories": kinds.count("story"),
            "tasks": kinds.count("task"),
            "links": len(self.links),
            "acceptance criteria": len(self.rules),
        }


@dataclass
class Source:
    """One thing from the old system, before it is placed."""

    id: str
    title: str
    body: str = ""
    parent: str | None = None
    wants_epic: bool = False
    priority: str = "normal"
    status: str = "new"
    criteria: list[str] = field(default_factory=list)


def _title_and_body(title: str, body: str) -> tuple[str, str]:
    """A title that fits one line, with the whole text kept in the details when it was cut."""
    one = " ".join(title.split())
    if not one:
        return "", body
    if len(one) <= MOST_TITLE_CHARS:
        return one, body
    cut = one[: MOST_TITLE_CHARS - 1].rstrip() + "\u2026"
    return cut, (f"{one}\n\n{body}" if body else one)


def build(
    source: str, sources: list[Source], extra_problems: list[str] | None = None
) -> tuple[Imported, dict[str, str]]:
    """Place every thing at its level (see the module's note) and give it a plan key. Returns the
    plan and the plan key of each source id, so that a reader can add links between them. Raises
    `ImportProblems` listing everything that does not fit."""
    problems = list(extra_problems or [])
    by_id: dict[str, Source] = {}
    for found in sources:
        if found.id in by_id:
            problems.append(f"{found.id} appears more than once.")
        by_id[found.id] = found
    depth: dict[str, int] = {}

    def depth_of(thing: Source, trail: tuple[str, ...] = ()) -> int | None:
        if thing.id in depth:
            return depth[thing.id]
        if thing.id in trail:
            problems.append(f"{thing.id} is its own ancestor: {' under '.join(trail[-3:])}.")
            return None
        if thing.parent is None:
            depth[thing.id] = 0
            return 0
        parent = by_id.get(thing.parent)
        if parent is None:
            problems.append(f"{thing.id} is under {thing.parent}, which is not in the file.")
            return None
        above = depth_of(parent, (*trail, thing.id))
        if above is None:
            return None
        depth[thing.id] = above + 1
        return above + 1

    for found in sources:
        depth_of(found)
    kinds: dict[str, str] = {}
    needs_inbox = False
    for found in sources:
        level = depth.get(found.id)
        if level is None:
            continue
        parent = by_id[found.parent] if found.parent is not None else None
        if parent is None:
            if found.wants_epic:
                kinds[found.id] = "epic"
            else:
                kinds[found.id] = "story"
                needs_inbox = True
            continue
        above = kinds.get(parent.id)
        if above is None:
            above = _kind_above(parent, by_id, depth)
        if found.wants_epic:
            problems.append(
                f"{found.id} is an epic under {parent.id}; an epic cannot be inside anything."
            )
        elif above == "epic":
            kinds[found.id] = "story"
        elif above == "story":
            kinds[found.id] = "task"
        else:
            problems.append(
                f"{found.id} is under {parent.id}, which is itself a task. Vrum has epics, "
                "stories and tasks, so nothing can be under a task."
            )
    if problems:
        raise ImportProblems(problems)
    imported = Imported(source=source)
    keys: dict[str, str] = {}
    if needs_inbox:
        imported.items.append(
            {
                "key": INBOX_KEY,
                "kind": "epic",
                "title": INBOX_TITLE,
                "body": f"Work imported from {source} that was not under an epic. Sort it where it "
                "belongs.",
            }
        )
    for number, found in enumerate(sources, start=1):
        keys[found.id] = f"i{number}"
    for found in sources:
        kind = kinds[found.id]
        title, body = _title_and_body(found.title, found.body)
        if not title:
            problems.append(f"{found.id} has no title.")
            continue
        parent_key = keys[found.parent] if found.parent is not None else None
        if parent_key is None and kind == "story":
            parent_key = INBOX_KEY
        item: dict[str, Any] = {
            "key": keys[found.id],
            "kind": kind,
            "title": title,
            "body": body,
            "priority": found.priority,
        }
        if parent_key is not None:
            item["parent"] = parent_key
        imported.items.append(item)
        if found.status != "new":
            imported.statuses[keys[found.id]] = found.status
        if kind != "epic":
            for criterion in found.criteria:
                imported.rules.append({"title": criterion, "node": keys[found.id]})
    if problems:
        raise ImportProblems(problems)
    return imported, keys


def _kind_above(parent: Source, by_id: dict[str, Source], depth: dict[str, int]) -> str:
    """The level a parent was given, for a parent that came later in the file than its child."""
    if parent.parent is None:
        return "epic" if parent.wants_epic else "story"
    level = _kind_above(by_id[parent.parent], by_id, depth)
    return "story" if level == "epic" else "task"


def _details(
    source: str, identifier: str, address: str | None, *parts: tuple[str, str | None]
) -> str:
    """The details of an imported item: what the source said, then where it came from."""
    written = [
        f"## {heading}\n\n{text.strip()}" for heading, text in parts if text and text.strip()
    ]
    where = f"Imported from {source}: {identifier}" + (f" ({address})" if address else "")
    return "\n\n".join([*written, where])


# -- Beads -----------------------------------------------------------------------------------

_BEADS_STATUS = {
    "open": "new",
    "deferred": "new",
    "pinned": "new",
    "in_progress": "in_progress",
    "hooked": "in_progress",
    "blocked": "blocked",
    "closed": "done",
}
# Beads' priorities run from 0 (critical) to 4 (backlog).
_BEADS_PRIORITY = {0: "urgent", 1: "high", 2: "normal", 3: "low", 4: "low"}
_BEADS_EPIC_TYPES = ("epic", "milestone")
_BEADS_NOT_WORK = ("message", "event", "molecule", "gate", "agent", "rig", "role")
# What a Beads dependency of each type becomes: needs_first (the issue waits for what it depends
# on), or another link.
_BEADS_LINKS = {
    "blocks": "needs_first",
    "waits-for": "needs_first",
    "conditional-blocks": "needs_first",
    "related": "relates_to",
    "relates-to": "relates_to",
    "discovered-from": "discovered_from",
    "duplicates": "duplicates",
    "supersedes": "supersedes",
}
_BEADS_PARENT = ("parent-child", "parent")


def _criteria_of(text: str | None) -> list[str]:
    """Acceptance criteria written as a list become one criterion each; text that is not a list is
    one criterion. Blank gives none."""
    if not text or not text.strip():
        return []
    bullets = [
        re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?", "", line).strip()
        for line in text.splitlines()
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", line)
    ]
    found = [bullet for bullet in bullets if bullet] or [" ".join(text.split())]
    return [
        one if len(one) <= MOST_TITLE_CHARS else one[: MOST_TITLE_CHARS - 1] + "\u2026"
        for one in found
    ]


def from_beads(text: str, *, skip_not_work: bool = False) -> Imported:
    """A Beads export (`bd export`, one JSON issue per line; a line wrapped as `{"data": ...}` is
    unwrapped). With `skip_not_work`, issues that are messages, events, molecules or gates are left
    out and said so; otherwise they are a problem."""
    problems: list[str] = []
    issues: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            found = json.loads(line)
        except ValueError:
            problems.append(f"Line {number} is not JSON.")
            continue
        if isinstance(found, dict) and isinstance(found.get("data"), dict):
            found = found["data"]
        if not isinstance(found, dict) or not isinstance(found.get("id"), str):
            problems.append(f"Line {number} is not an issue with an id.")
            continue
        issues.append(found)
    sources: list[Source] = []
    edges: list[tuple[str, str, str, str]] = []
    left_out: list[str] = []
    skipped: set[str] = set()
    for issue in issues:
        identifier = issue["id"]
        kind = str(issue.get("issue_type") or "task")
        if kind in _BEADS_NOT_WORK:
            if skip_not_work:
                skipped.add(identifier)
                continue
            problems.append(
                f"{identifier} is a {kind}, not work. Run again with --skip-not-work to leave "
                "those out."
            )
            continue
        status = str(issue.get("status") or "open")
        if status not in _BEADS_STATUS:
            problems.append(
                f"{identifier} has the status {status!r}, which has no Vrum equivalent."
            )
            continue
        priority = issue.get("priority", 2)
        if priority not in _BEADS_PRIORITY:
            problems.append(f"{identifier} has the priority {priority!r}; Beads uses 0 to 4.")
            continue
        parent = issue.get("parent") if isinstance(issue.get("parent"), str) else None
        for dependency in issue.get("dependencies") or []:
            if not isinstance(dependency, dict):
                continue
            kind_of = str(dependency.get("type") or dependency.get("dependency_type") or "blocks")
            owner = dependency.get("issue_id", identifier)
            other = dependency.get("depends_on_id")
            if not isinstance(other, str):
                problems.append(f"{identifier} has a dependency with no depends_on_id.")
                continue
            if kind_of in _BEADS_PARENT:
                if owner == identifier:
                    parent = other
                continue
            if kind_of not in _BEADS_LINKS:
                problems.append(
                    f"{identifier} has a {kind_of!r} dependency on {other}, which has no Vrum "
                    "link type."
                )
                continue
            if owner == identifier:
                edges.append((identifier, other, _BEADS_LINKS[kind_of], kind_of))
        labels = [str(label) for label in issue.get("labels") or []]
        sources.append(
            Source(
                id=identifier,
                title=str(issue.get("title") or ""),
                body=_details(
                    "Beads",
                    identifier,
                    None,
                    ("Description", issue.get("description")),
                    ("Design", issue.get("design")),
                    ("Notes", issue.get("notes")),
                    ("Labels", ", ".join(labels) or None),
                ),
                parent=parent,
                wants_epic=kind in _BEADS_EPIC_TYPES,
                priority=_BEADS_PRIORITY[priority],
                status=_BEADS_STATUS[status],
                criteria=_criteria_of(issue.get("acceptance_criteria")),
            )
        )
    for source in sources:
        if source.parent in skipped:
            source.parent = None
    if skipped:
        left_out.append(
            f"{len(skipped)} issues that are not work (messages, events, gates) were left out."
        )
    known = {source.id for source in sources}
    for owner, other, _, kind_of in edges:
        if other not in known and other not in skipped:
            problems.append(
                f"{owner} has a {kind_of} dependency on {other}, which is not in the file."
            )
    imported, keys = build("Beads", sources, problems)
    seen: set[tuple[str, str, str]] = set()
    for owner, other, link_type, _ in edges:
        if other in skipped:
            continue
        pair = (keys[owner], keys[other], link_type)
        flipped = (keys[other], keys[owner], link_type)
        if pair in seen or (link_type in ("duplicates", "relates_to") and flipped in seen):
            continue
        seen.add(pair)
        imported.links.append({"source": pair[0], "target": pair[1], "link_type": link_type})
    imported.left_out.extend(left_out)
    return imported


# -- GitHub issues ----------------------------------------------------------------------------

_GH_PRIORITY = (
    (re.compile(r"\b(urgent|critical|p0|priority[: /-]*0)\b", re.I), "urgent"),
    (re.compile(r"\b(high|p1|priority[: /-]*1)\b", re.I), "high"),
    (re.compile(r"\b(low|p3|priority[: /-]*3)\b", re.I), "low"),
)
_TRACKED = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s+(?:[\w.-]+/[\w.-]+)?#(\d+)\b", re.M)


def _github_priority(labels: list[str]) -> str:
    for pattern, priority in _GH_PRIORITY:
        if any(pattern.search(label) for label in labels):
            return priority
    return "normal"


def from_github_issues(text: str) -> Imported:
    """A list of GitHub issues as JSON: `gh issue list --state all --limit 1000 --json
    number,title,body,state,stateReason,labels,milestone,url` or the REST API's `GET
    /repos/OWNER/REPO/issues?state=all` (pull requests in it are left out and counted). A milestone
    is an epic; an issue is a story; an issue whose body has a task list of `- [ ] #12` makes those
    issues its tasks; the rest of the body is kept as details."""
    try:
        data = json.loads(text)
    except ValueError:
        raise ImportProblems(["The file is not JSON."]) from None
    if not isinstance(data, list):
        raise ImportProblems(["The file must be a JSON list of issues."])
    problems: list[str] = []
    left_out: list[str] = []
    issues: dict[int, dict[str, Any]] = {}
    pulls = 0
    for found in data:
        if not isinstance(found, dict) or not isinstance(found.get("number"), int):
            problems.append("An entry is not an issue with a number.")
            continue
        if found.get("pull_request"):
            pulls += 1
            continue
        issues[found["number"]] = found
    if pulls:
        left_out.append(f"{pulls} pull requests were left out: they are not issues.")
    milestones: dict[str, str] = {}
    sources: list[Source] = []
    children: dict[int, int] = {}
    for number, issue in issues.items():
        for tracked in _TRACKED.findall(str(issue.get("body") or "")):
            child = int(tracked)
            if child in issues and child != number and child not in children:
                children[child] = number
    for issue in issues.values():
        milestone = issue.get("milestone")
        title = milestone.get("title") if isinstance(milestone, dict) else None
        if isinstance(title, str) and title.strip() and title not in milestones:
            milestones[title] = f"milestone:{len(milestones) + 1}"
    for title, identifier in milestones.items():
        sources.append(
            Source(
                id=identifier,
                title=title,
                body=_details("GitHub", f"milestone {title}", None),
                wants_epic=True,
            )
        )
    for number, issue in issues.items():
        labels = [
            str(label.get("name") if isinstance(label, dict) else label)
            for label in issue.get("labels") or []
        ]
        state = str(issue.get("state") or "open").lower()
        reason = str(issue.get("stateReason") or issue.get("state_reason") or "").lower()
        status = "new" if state == "open" else "dropped" if reason == "not_planned" else "done"
        milestone = issue.get("milestone")
        title = milestone.get("title") if isinstance(milestone, dict) else None
        parent = children.get(number)
        parent_id = f"#{parent}" if parent is not None else milestones.get(title or "")
        sources.append(
            Source(
                id=f"#{number}",
                title=str(issue.get("title") or ""),
                body=_details(
                    "GitHub",
                    f"issue #{number}",
                    issue.get("url") or issue.get("html_url"),
                    ("Description", str(issue.get("body") or "")),
                    ("Labels", ", ".join(labels) or None),
                ),
                parent=parent_id,
                priority=_github_priority(labels),
                status=status,
            )
        )
    imported, _ = build("GitHub", sources, problems)
    imported.left_out.extend(left_out)
    return imported


# -- Spec Kit ---------------------------------------------------------------------------------

_PHASE = re.compile(r"^##\s+(?P<name>.+?)\s*$")
_FEATURE = re.compile(r"^#\s+(?:Tasks:\s*)?(?P<name>.+?)\s*$")
_TASK = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<id>T\d+)\s+(?P<rest>.+?)\s*$")
_STORY_LABEL = re.compile(r"\[(US\d+)\]")
_PARALLEL = re.compile(r"\[P\]\s*")
_DEPENDS = re.compile(r"\(?depends on\s+(?P<ids>T\d+(?:\s*(?:,|and)\s*T\d+)*)\)?", re.I)
_PHASE_STORY = re.compile(r"user story\s+(\d+)", re.I)


def from_spec_kit(text: str) -> Imported:
    """A Spec Kit `tasks.md`: the feature (the first `# Tasks: ...` heading) is an epic; each
    `## Phase` heading is a story; each `- [ ] T012 [P] [US1] description` line is a task of the
    phase it is under, or of the phase whose heading says that user story when the line is
    labelled; a task written `(depends on T010, T011)` waits for those tasks; `[x]` means done."""
    cleaned = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    cleaned = re.sub(r"```.*?```", "", cleaned, flags=re.S)
    feature: str | None = None
    phases: list[tuple[str, str]] = []
    task_rows: list[tuple[str, str, str, str, bool, str | None]] = []
    problems: list[str] = []
    for line in cleaned.splitlines():
        matched = _TASK.match(line)
        if matched:
            rest = matched["rest"]
            label = _STORY_LABEL.search(rest)
            words = _PARALLEL.sub("", _STORY_LABEL.sub("", rest)).strip()
            depends = _DEPENDS.search(words)
            ids = re.findall(r"T\d+", depends["ids"]) if depends else []
            words = _DEPENDS.sub("", words).strip(" ,;")
            if not phases:
                phases.append(("Tasks", ""))
            task_rows.append(
                (
                    matched["id"],
                    words,
                    ",".join(ids),
                    phases[-1][0],
                    matched["mark"] != " ",
                    label[1] if label else None,
                )
            )
            continue
        phase = _PHASE.match(line)
        if phase:
            phases.append((phase["name"], line))
            continue
        top = _FEATURE.match(line)
        if top and feature is None:
            feature = top["name"]
    if not task_rows:
        raise ImportProblems(
            [
                "No task lines were found. A task is a line such as "
                "'- [ ] T012 [P] [US1] Create the model'."
            ]
        )
    story_of_label: dict[str, str] = {}
    for name, _ in phases:
        found = _PHASE_STORY.search(name)
        if found:
            story_of_label[f"US{found[1]}"] = name
    sources = [
        Source(
            id="feature",
            title=feature or "Imported from Spec Kit",
            body=_details("Spec Kit", "tasks.md", None),
            wants_epic=True,
        )
    ]
    used = [
        name
        for name, _ in phases
        if any(row[3] == name or story_of_label.get(row[5] or "") == name for row in task_rows)
    ]
    names = list(dict.fromkeys(used))
    for position, name in enumerate(names, start=1):
        sources.append(
            Source(
                id=f"phase:{position}",
                title=name,
                body=_details("Spec Kit", name, None),
                parent="feature",
            )
        )
    phase_id = {name: f"phase:{position}" for position, name in enumerate(names, start=1)}
    task_status: dict[str, str] = {}
    for identifier, words, needed, phase_name, done, story_label in task_rows:
        home = story_of_label.get(story_label or "", phase_name)
        sources.append(
            Source(
                id=identifier,
                title=words,
                body=_details("Spec Kit", identifier, None),
                parent=phase_id[home],
                status="done" if done else "new",
            )
        )
        task_status[identifier] = needed
    imported, keys = build("Spec Kit", sources, problems)
    seen: set[tuple[str, str]] = set()
    for identifier, needed_ids in task_status.items():
        for other in [d for d in needed_ids.split(",") if d]:
            if other not in keys:
                raise ImportProblems(
                    [f"{identifier} depends on {other}, which is not in the file."]
                )
            if (identifier, other) not in seen and identifier != other:
                seen.add((identifier, other))
                imported.links.append(
                    {"source": keys[identifier], "target": keys[other], "link_type": "needs_first"}
                )
    return imported
