"""The demo (./demo.sh): that its script keeps to its own Compose project and refuses what it
should, and that the sample project it fills holds what it is documented to hold. No containers:
what the demo makes in a real stack is looked at by hand, as ./demo.sh's own run (see
tests/demo_world.py) checks the amounts against server_status."""

import datetime
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from tests import demo_data as data
from tests import demo_details
from tests import scripted_github as github
from tests import scripted_oidc as oidc
from tests.demo_world import (
    Names,
    agents_of_description,
    allowances,
    check_words,
    confirmer_of,
    derived_criteria,
    expected_counts,
    expected_note_counts,
    place,
    plan_arguments,
    recorder_of,
)
from vrum.secret_formats import find_secret

SCRIPT = Path(__file__).resolve().parent.parent / "demo.sh"
REFERENCE = re.compile(r"\[\[([a-z0-9][a-z0-9-]*)\]\]")
OPEN = ("new", "in_progress", "blocked")


def code_lines() -> list[str]:
    """The script's lines that run something: no comments and no messages."""
    lines = (line.strip() for line in SCRIPT.read_text().splitlines())
    return [line for line in lines if line and not line.startswith(("#", "echo"))]


def run(*arguments: str, **environment: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **environment}
    return subprocess.run(
        [str(SCRIPT), *arguments], capture_output=True, text=True, env=env, timeout=60
    )


# -- the script ------------------------------------------------------------------------------------


def test_the_script_is_an_executable_that_bash_accepts() -> None:
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_every_docker_command_names_the_demos_own_project_and_no_env_file() -> None:
    code = "\n".join(code_lines())
    commands = sorted(line for line in code_lines() if "docker compose" in line)
    assert commands == [
        'docker compose --env-file /dev/null -p "$project" "$@"',
        "if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then",
    ]
    assert "-p vrum" not in code and "COMPOSE_PROJECT_NAME=vrum" not in code
    # The only way .env could come into it is as the default file of Compose, which an empty file
    # given by --env-file replaces; the script never names, reads or writes one.
    assert not re.search(r"(?<![-\w])\.env\b", code)
    assert "--env-file /dev/null" in code


def test_stop_removes_the_demos_containers_volumes_and_image_and_nothing_else() -> None:
    stop = next(line for line in code_lines() if line.startswith("demo down") and "--rmi" in line)
    assert stop == "demo down --volumes --remove-orphans --rmi local"
    assert [
        line for line in code_lines() if re.search(r"\bdocker (rm|rmi|volume|system)", line)
    ] == []
    assert [line for line in code_lines() if "prune" in line] == []


def test_starting_again_removes_only_its_own_volumes_first() -> None:
    lines = code_lines()
    start = next(index for index, line in enumerate(lines) if line.startswith("if ! demo up -d"))
    assert lines.index("demo down --volumes --remove-orphans") < start
    assert "--build" in lines[start] and "--wait" in lines[start]


@pytest.mark.parametrize(
    ("variables", "words"),
    [
        ({"VRUM_DEMO_PROJECT": "vrum"}, "demo"),
        ({"VRUM_DEMO_PROJECT": "Vrum-Demo"}, "lowercase"),
        ({"VRUM_DEMO_PROJECT": "my_stack"}, "demo"),
        ({"VRUM_DEMO_PORT": "80"}, "1024"),
        ({"VRUM_DEMO_PORT": "eight"}, "port number"),
        ({"VRUM_DEMO_MAIL_PORT": "80"}, "VRUM_DEMO_MAIL_PORT must be a port number from 1024"),
        ({"VRUM_DEMO_MAIL_PORT": "mail"}, "VRUM_DEMO_MAIL_PORT must be a port number"),
        ({"VRUM_DEMO_OIDC_PORT": "80"}, "VRUM_DEMO_OIDC_PORT must be a port number from 1024"),
        ({"VRUM_DEMO_OIDC_PORT": "openid"}, "VRUM_DEMO_OIDC_PORT must be a port number"),
        ({"VRUM_DEMO_GITHUB_PORT": "70000"}, "VRUM_DEMO_GITHUB_PORT must be a port number from"),
        ({"VRUM_DEMO_GITHUB_PORT": "hub"}, "VRUM_DEMO_GITHUB_PORT must be a port number"),
        ({"VRUM_DEMO_PORT": "8095", "VRUM_DEMO_MAIL_PORT": "8095"}, "must all be different ports"),
        ({"VRUM_DEMO_OIDC_PORT": "8094"}, "must all be different ports"),
        ({"VRUM_DEMO_MAIL_PORT": "8093"}, "must all be different ports"),
        ({"VRUM_DEMO_PORT": "8093"}, "must all be different ports"),
    ],
)
def test_it_refuses_a_project_or_port_that_could_be_somebody_elses(
    variables: dict[str, str], words: str
) -> None:
    done = run(**variables)
    assert done.returncode == 1
    assert words in done.stderr


def test_the_demo_has_a_mail_page_of_its_own_and_none_of_your_mail_settings_reach_it() -> None:
    code = "\n".join(code_lines())
    assert 'mail_port="${VRUM_DEMO_MAIL_PORT:-8092}"' in code
    assert 'export VRUM_MAIL_PORT="$mail_port"' in code
    unset = code.partition("unset ")[2].partition("export ")[0]
    for name in ("VRUM_PUBLIC_URL", "VRUM_SMTP_HOST", "VRUM_SMTP_PASSWORD", "VRUM_SMTP_FROM"):
        assert name in unset
    # Every one of its ports is checked before anything starts, and it says where the sent email
    # shows.
    assert code.count("refuse_ports_in_use") == 3
    said = "\n".join(
        line for line in SCRIPT.read_text().splitlines() if line.strip().startswith("echo")
    )
    assert (
        "Sent email shows at http://localhost:${mail_port} (this demo sends no real email)" in said
    )


def test_the_demo_has_stand_in_sign_in_providers_and_none_of_your_settings_reach_it() -> None:
    code = "\n".join(code_lines())
    assert 'oidc_port="${VRUM_DEMO_OIDC_PORT:-8093}"' in code
    assert 'github_port="${VRUM_DEMO_GITHUB_PORT:-8094}"' in code
    assert 'export VRUM_OIDC_PORT="$oidc_port"' in code
    assert 'export VRUM_GITHUB_PORT="$github_port"' in code
    assert 'for used in "$port" "$mail_port" "$oidc_port" "$github_port"; do' in code
    unset = code.partition("unset ")[2].partition("export ")[0]
    for name in (
        "VRUM_SSO_CREATE_ACCOUNTS",
        "VRUM_SSO_ALLOW_INSECURE_HTTP",
        "VRUM_OIDC_ISSUER",
        "VRUM_OIDC_CLIENT_SECRET",
        "VRUM_GITHUB_CLIENT_SECRET",
        "VRUM_GITHUB_URL",
        "VRUM_LDAP_URL",
        "VRUM_LDAP_BIND_PASSWORD",
        "VRUM_LDAP_ALLOWED_DOMAINS",
    ):
        assert name in unset
    said = "\n".join(
        line for line in SCRIPT.read_text().splitlines() if line.strip().startswith("echo")
    )
    assert "Sign in with stand-in providers (OpenID at http://localhost:${oidc_port}" in said
    assert "GitHub at http://localhost:${github_port}" in said
    assert "Nothing real is contacted." in said


def test_the_demo_starts_the_stand_ins_and_the_directory_and_not_dex_or_the_second_server() -> None:
    lines = code_lines()
    start = next(index for index, line in enumerate(lines) if line.startswith("if ! demo up -d"))
    # The command goes on over two lines; Dex and the server that talks to it are not named, and
    # so are not started (a service is started with its dependencies only).
    command = " ".join(lines[start : start + 2])
    for service in ("scripted-oidc", "scripted-github", "ldap", "scripted-mail", "app"):
        assert re.search(rf"\b{service}\b", command)
    assert "dex" not in command


def test_the_stand_in_people_of_the_demo_are_in_every_stand_in_with_the_right_standing() -> None:
    confirmed, unconfirmed, new = (
        data.STAND_IN_CONFIRMED_EMAIL,
        data.STAND_IN_UNCONFIRMED_EMAIL,
        data.STAND_IN_NEW_EMAIL,
    )
    # The first two are the demo's own people, whose accounts the loader makes (the planner's
    # invitation is emailed, which confirms her address; the viewer's is not). Nobody has the third.
    assert (confirmed, unconfirmed) == (data.PLANNER_EMAIL, data.VIEWER_EMAIL)
    assert new not in (data.OWNER_EMAIL, data.PLANNER_EMAIL, data.VIEWER_EMAIL)
    openid = {one["email"]: one for one in oidc.DEFAULT_PEOPLE if "email" in one}
    for address in (confirmed, unconfirmed, new):
        assert openid[address]["email_verified"] is True
    named = [one["name"] for one in oidc.DEFAULT_PEOPLE if one.get("email") == new]
    assert named == [data.STAND_IN_NEW]
    for address in (confirmed, unconfirmed, new):
        github_people = [
            one
            for one in github.DEFAULT_PEOPLE
            if [
                e for e in one["emails"] if e["primary"] and e["verified"] and e["email"] == address
            ]
        ]
        assert len(github_people) == 1, address
    directory = (Path(__file__).parent / "providers" / "ldap" / "people.ldif").read_text()
    for address in (confirmed, unconfirmed, new):
        assert f"\nmail: {address}\n" in directory


def test_it_says_how_it_is_used_when_given_anything_else() -> None:
    done = run("up")
    assert done.returncode == 2
    assert "./demo.sh stop" in done.stderr


# -- the sample project ----------------------------------------------------------------------------


def all_items() -> list[data.Item]:
    return [placed.item for placed in place(data.EPICS)]


def tasks() -> list[data.Item]:
    return [item for item in all_items() if item.kind == "task"]


def texts() -> list[str]:
    found = [item.title for item in all_items()] + [item.body for item in all_items()]
    found += [rule.title for rule in data.RULES] + [rule.body for rule in data.RULES]
    found += list(demo_details.TASK_DETAILS.values())
    found += [title for titles in demo_details.ACCEPTS.values() for title in titles]
    found.append(data.DEFINITION_OF_DONE)
    found += [text for wordings in data.CHECK_EVIDENCE for text in wordings]
    found += [*data.PERSON_TRIED, data.UNTICK.first, data.UNTICK.reason, data.UNTICK.again]
    for note in data.NOTES:
        found += [note.text, note.why]
    for step in data.HANDOFFS:
        found.append(step.note)
    found += [step.quote for step in data.CONFIRMATIONS]
    found += [step.reason for step in data.WITHDRAWALS]
    found += [edit.body or "" for edit in data.EDITS] + [edit.title or "" for edit in data.EDITS]
    return found


def test_the_amounts_are_what_the_documentation_says() -> None:
    counts = expected_counts()
    assert counts["epics"] == 12
    assert 40 <= counts["stories"] <= 60
    assert 150 <= counts["tasks"] <= 200
    assert counts["notes"] >= 35 and counts["rules"] >= 150
    epics = data.EPICS
    assert sorted(len(epic.children) for epic in epics)[0] == 0
    assert sum(not epic.children for epic in epics) == 1
    assert max(len(epic.children) for epic in epics) == 9
    stories = [item for item in all_items() if item.kind == "story"]
    assert max(len(item.children) for item in stories) == 12
    assert any(not item.children for item in stories)


def test_there_is_work_in_every_status_and_priority_with_every_kind_of_date() -> None:
    assert {item.status for item in tasks()} == {"new", "in_progress", "blocked", "done", "dropped"}
    assert {item.priority for item in tasks()} == {"low", "normal", "high", "urgent"}
    overdue = [t for t in tasks() if t.due is not None and t.due < 0 and t.status in OPEN]
    assert len(overdue) >= 5
    assert any(t.due is not None and 0 <= t.due <= 7 for t in tasks())
    assert any(t.due is not None and t.due >= 60 for t in tasks())
    assert sum(t.due is None for t in tasks()) >= 5
    assert any(t.start is not None for t in tasks())


def test_titles_run_from_one_word_to_a_sentence_and_one_has_a_long_unbroken_word() -> None:
    titles = [item.title for item in all_items()]
    assert min(len(title.split()) for title in titles) == 1
    assert max(len(title) for title in titles) >= 100
    assert any(len(word) >= 40 for title in titles for word in title.split())


def test_epics_have_long_markdown_details_that_refer_to_other_items() -> None:
    for epic in data.EPICS:
        assert len(epic.body) >= 350, epic.title
        assert "## " in epic.body, epic.title
    with_references = [epic for epic in data.EPICS if REFERENCE.search(epic.body)]
    assert len(with_references) >= 10
    assert sum("```" in epic.body for epic in data.EPICS) >= 4
    assert sum("\n- " in epic.body for epic in data.EPICS) >= 4


def decided_notes() -> list[data.Note]:
    return [note for note in data.NOTES if note.by]


def plain_notes() -> list[data.Note]:
    return [note for note in data.NOTES if not note.by]


def test_there_are_nine_long_project_wide_notes_and_the_rest_of_the_notes_vary() -> None:
    wide = [note for note in data.NOTES if note.project_wide]
    assert len(wide) == 9 and all(note.by for note in wide)
    assert all(len(note.text) >= 250 and len(note.why) >= 250 for note in wide)
    assert len({note.by for note in wide}) >= 4
    assert all("platform" in note.nodes for note in wide)
    confirmed = {step.key for step in data.CONFIRMATIONS if step.confirmed}
    assert 1 <= len([note for note in wide if note.key in confirmed]) <= 4
    assert len(decided_notes()) == 20 and len(plain_notes()) == 19
    decided_keys = {note.key for note in decided_notes()}
    assert sum(1 for note in decided_notes() if note.replaces) == 1
    assert sum(1 for step in data.WITHDRAWALS if step.key in decided_keys) == 1
    assert sum(1 for note in plain_notes() if note.replaces) == 1
    assert sum(1 for step in data.WITHDRAWALS if step.key not in decided_keys) == 1
    assert all(note.why.strip() for note in data.NOTES)
    assert any(step.confirmed and step.key not in decided_keys for step in data.CONFIRMATIONS)
    assert any(not step.confirmed for step in data.CONFIRMATIONS)
    assert any(note.notes for note in plain_notes()) and any(note.rules for note in plain_notes())
    assert any(note.rules for note in decided_notes() if not note.project_wide)


def test_a_note_somebody_decided_has_its_headline_first_and_another_is_one_sentence() -> None:
    for note in decided_notes():
        headline, blank, rest = note.text.partition("\n\n")
        assert blank and rest.strip(), note.key
        assert "\n" not in headline and 0 < len(headline) <= 120, note.key
        assert note.ago is not None and note.ago > 0, note.key
    for note in plain_notes():
        assert "\n" not in note.text and note.ago is None and not note.project_wide, note.key


def test_notes_somebody_decided_are_numbered_first_and_a_note_only_names_earlier_notes() -> None:
    names = Names.of_description()
    decided = [bool(note.by) for note in data.NOTES]
    assert decided == sorted(decided, reverse=True)
    for number, note in enumerate(data.NOTES, start=1):
        assert names.notes[note.key] == number
        for key in (*note.notes, *note.replaces):
            assert names.notes[key] < number, note.key


def test_the_notes_come_to_the_amounts_the_loader_checks_the_server_against() -> None:
    assert expected_note_counts() == {
        "all": 39,
        "decided": 20,
        "project_wide": 9,
        "confirmed": 15,
        "unconfirmed": 20,
        "in_force": 35,
        "superseded": 2,
        "withdrawn": 2,
    }


def test_the_named_acceptance_criteria_are_placed_on_stories_and_some_are_not() -> None:
    placed = {rule.node for rule in data.RULES if rule.node}
    assert len(placed) >= 15
    assert sum(rule.node is None for rule in data.RULES) >= 5
    assert {rule.status for rule in data.RULES} == {"open", "met", "dropped"}
    assert sum(rule.status == "dropped" for rule in data.RULES) == 1
    assert len(data.RULE_MOVES) >= 1 and len(data.RULE_PLACES) >= 1
    names = Names.of_description()
    assert all(names.node(key).item.kind == "story" for key in placed)


def sentences(text: str) -> int:
    return len(re.findall(r"[.!?](?:\s|$)", text))


def test_every_epic_and_story_has_details_except_two_stories_left_blank_on_purpose() -> None:
    placed = place(data.EPICS)
    assert all(p.item.body.strip() for p in placed if p.item.kind == "epic")
    stories = [p.item for p in placed if p.item.kind == "story"]
    blank = [story.title for story in stories if not story.body.strip()]
    assert blank == ["Spare parts stock", "Chat with support"]
    for story in stories:
        if story.body.strip():
            assert 2 <= sentences(story.body) <= 9, story.title


def test_two_thirds_of_the_tasks_have_a_few_sentences_of_details_and_the_rest_none() -> None:
    with_details = [item for item in tasks() if item.body.strip()]
    assert 0.6 <= len(with_details) / len(tasks()) <= 0.72
    assert 50 <= len(tasks()) - len(with_details) <= 70
    for item in with_details:
        assert 1 <= sentences(item.body) <= 3, item.title


def test_details_and_criteria_are_plain_words_about_items_that_exist() -> None:
    titles = {p.item.title: p.item.kind for p in place(data.EPICS)}
    for title in demo_details.TASK_DETAILS:
        assert titles.get(title) == "task", title
    for title in demo_details.ACCEPTS:
        assert titles.get(title) in ("story", "task"), title
    # What is written here is plain: no codes, no digits or other kinds of dash.
    plain = [
        *demo_details.TASK_DETAILS.values(),
        *(t for criteria in demo_details.ACCEPTS.values() for t in criteria),
        data.DEFINITION_OF_DONE,
    ]
    for text in plain:
        assert not re.search(r"[0-9\[\]]|[\u2010-\u2015\u2212]", text.replace("[person] ", "")), (
            text
        )
        assert not any(find_secret(line) for line in text.splitlines())


def test_about_half_the_tasks_and_most_stories_have_acceptance_criteria_some_of_them_met() -> None:
    placed = place(data.EPICS)
    tasks_with = [p for p in placed if p.item.kind == "task" and p.item.accepts]
    stories = [p for p in placed if p.item.kind == "story"]
    stories_with = {p.key for p in stories if p.item.accepts}
    named_on = {names_node for names_node in {r.node for r in data.RULES} if names_node}
    covered = stories_with | {p.key for p in stories if p.item.key in named_on}
    assert 0.4 <= len(tasks_with) / len(tasks()) <= 0.6
    assert len(covered) >= 0.7 * len(stories)
    assert all(1 <= len(p.item.accepts) <= 3 for p in placed if p.item.accepts)
    derived = derived_criteria(placed)
    assert {rule["status"] for rule in derived} == {"open", "met"}
    assert all(rule["node"] and not rule["node"].startswith("e") for rule in derived)
    assert len(derived) + len(data.RULES) == expected_counts()["rules"]


def test_the_definition_of_done_is_a_short_list_in_plain_words() -> None:
    lines = data.DEFINITION_OF_DONE.splitlines()
    assert 5 <= len(lines) <= 7 and all(line.startswith("- ") for line in lines)
    text = data.DEFINITION_OF_DONE.lower()
    for word in ("tests", "real phone", "changelog", "half done"):
        assert word in text
    # Exactly one line is for a person alone, and it is the last.
    assert [line for line in lines if "[person]" in line] == [lines[-1]]


def test_most_tasks_have_a_size_and_about_an_eighth_are_left_without_one_on_purpose() -> None:
    sizes = demo_details.SIZES
    assert set(sizes.values()) <= {"xs", "s", "m", "l", "xl"}
    titles = {p.item.title: p.item for p in place(data.EPICS)}
    assert set(sizes) <= set(titles)
    sized = [item for item in tasks() if item.size]
    assert 0.85 <= len(sized) / len(tasks()) <= 0.9
    assert 15 <= len(tasks()) - len(sized) <= 25
    # Mostly s and m, some xs, a few l, and one or two xl.
    counts = {
        size: sum(item.size == size for item in sized) for size in ("xs", "s", "m", "l", "xl")
    }
    assert counts["s"] + counts["m"] > 0.7 * len(sized)
    assert 8 <= counts["xs"] <= 25 and 4 <= counts["l"] <= 12
    everything = [p.item for p in place(data.EPICS)]
    assert 1 <= sum(item.size == "xl" for item in everything) <= 2
    # A story that has tasks adds up its tasks, an epic has no size, and a story with none has one.
    for item in everything:
        if item.kind == "epic" or (item.kind == "story" and item.children):
            assert item.size is None, item.title
        if item.kind == "story" and not item.children:
            assert item.size, item.title


def test_the_plan_carries_every_size() -> None:
    arguments = plan_arguments(Names.of_description(), datetime.date(2026, 11, 19))
    sent = {item["key"]: item["size"] for item in arguments["items"]}
    wanted = {p.key: p.item.size for p in place(data.EPICS)}
    assert sent == wanted and any(sent.values())


def test_every_check_of_the_definition_of_done_has_wordings_for_how_it_was_done() -> None:
    lines = [line.removeprefix("- ") for line in data.DEFINITION_OF_DONE.splitlines()]
    checks = [line for line in lines if not line.startswith("[person]")]
    assert len(data.CHECK_EVIDENCE) == len(checks) == len(lines) - 1 == 6
    assert all(len(wordings) == 3 for wordings in data.CHECK_EVIDENCE)
    assert len({text for wordings in data.CHECK_EVIDENCE for text in wordings}) == 18
    # Every wording can be said for every piece of work, and says something about the work.
    for placed in place(data.EPICS):
        for index in range(len(checks)):
            said = check_words(index, placed)
            assert said.strip() and "{" not in said, (placed.item.title, index)
            assert not re.search(r"[\u2010-\u2015\u2212]", said)
    # The numbers are worked out from the title: the same title always says the same.
    one = place(data.EPICS)[1]
    assert check_words(1, one) == check_words(1, one)
    # A person ticks the check marked [person] on finished work, once with no words, and one tick
    # of an agent is taken back and put right: all of them about finished work.
    done = {p.item.title for p in place(data.EPICS) if p.item.status == "done"}
    assert data.PERSON_TICK_WITH_NO_WORDS in done and data.UNTICK.task in done
    assert 0 <= data.UNTICK.check < len(checks)
    assert len(data.PERSON_TRIED) == 3 and len(done) >= 12


def test_every_key_is_unique_and_every_reference_in_every_text_resolves() -> None:
    names = Names.of_description()
    for text in texts():
        written = names.text(text)
        assert not REFERENCE.search(written)
    assert names.reference("privacy") == names.node("privacy").ref


def test_nothing_in_the_description_looks_like_a_secret() -> None:
    assert [text for text in texts() if find_secret(text)] == []


def test_the_plan_is_what_create_plan_takes_and_is_always_the_same() -> None:
    names = Names.of_description()
    import datetime

    today = datetime.date(2030, 3, 15)
    arguments = plan_arguments(names, today)
    assert arguments == plan_arguments(names, today)
    items = arguments["items"]
    keys = [item["key"] for item in items]
    assert len(set(keys)) == len(keys)
    assert not any(":" in key for key in keys)
    assert {item["parent"] for item in items if item["kind"] == "story"} <= set(keys)
    assert all(item["parent"] is None for item in items if item["kind"] == "epic")
    assert len(arguments["links"]) == len(data.LINKS)
    assert len(arguments["rules"]) == expected_counts()["rules"]
    rule_nodes = {rule["node"] for rule in arguments["rules"] if rule["node"]}
    assert rule_nodes <= set(keys)
    dates = [item["target_on"] for item in items if item["target_on"]]
    assert min(dates) < today.isoformat() < max(dates)


def test_links_do_not_run_in_a_circle_and_name_items_that_exist() -> None:
    names = Names.of_description()
    waits: dict[str, set[str]] = {}
    for link in data.LINKS:
        assert link.kind in ("needs_first", "relates_to", "duplicates", "supersedes")
        assert link.source != link.target
        names.node(link.source), names.node(link.target)
        if link.kind == "needs_first":
            waits.setdefault(link.source, set()).add(link.target)

    def reaches(start: str, wanted: str, seen: frozenset[str] = frozenset()) -> bool:
        return any(
            nxt == wanted or (nxt not in seen and reaches(nxt, wanted, seen | {nxt}))
            for nxt in waits.get(start, ())
        )

    assert not [key for key in waits if reaches(key, key)]
    assert any(link.kind == "duplicates" for link in data.LINKS)
    assert any(link.kind == "supersedes" for link in data.LINKS)


def test_handoffs_and_claims_can_be_made_in_the_order_they_are_listed() -> None:
    names = Names.of_description()
    status = {key: names.node(key).item.status for key in names.nodes}
    needs = {(link.source, link.target) for link in data.LINKS if link.kind == "needs_first"}
    finished = {"done": "done", "blocked": "blocked", "dropped": "dropped"}
    done = {key for key, value in status.items() if value == "done"}
    ended = {step.key for step in data.HANDOFFS}
    # Before a handoff, an item is only waiting for what is already done.
    reached = {key for key in done if key not in ended}
    for step in data.HANDOFFS:
        assert step.outcome in ("done", "blocked", "dropped", "released")
        assert len(step.note) >= 80
        waiting = [target for source, target in needs if source == step.key]
        assert all(target in reached for target in waiting), step.key
        if step.outcome in finished:
            assert status[step.key] == finished[step.outcome], step.key
        if step.outcome == "done":
            reached.add(step.key)
    held = {held.key for held in data.HELD} | {held.key for held in data.LAPSED}
    for key in held:
        assert [target for source, target in needs if source == key and target not in reached] == []
    assert len(data.HELD) >= 4 and len(data.LAPSED) == 1
    assert len({held.actor for held in data.HELD}) >= 3


def test_edits_change_something_and_never_put_a_start_after_a_target() -> None:
    names = Names.of_description()
    for edit in data.EDITS:
        item = names.node(edit.key).item
        changes = [
            edit.title is not None and edit.title != item.title,
            edit.body is not None and edit.body != item.body,
            edit.priority is not None and edit.priority != item.priority,
            edit.due is not None and edit.due != item.due,
            edit.clear_due and item.due is not None,
        ]
        assert any(changes), edit.key
        start = edit.start if edit.start is not None else item.start
        due = edit.due if edit.due is not None else item.due
        assert start is None or due is None or start <= due, edit.key


def test_people_and_agents_are_named_the_same_way_everywhere() -> None:
    actors = {step.actor for step in data.HANDOFFS} | {held.actor for held in data.HELD}
    actors |= {edit.actor for edit in data.EDITS} | set(data.WORKERS.values())
    assert {"api-agent", "mobile-agent", "ops-agent"} <= actors
    assert {"Priya Nair", "Lena Okafor", "Tomas Berg"} <= actors
    assert all(epic.key in data.WORKERS for epic in data.EPICS)
    # finish_work, which leaves a handoff note, is for agents.
    assert all(step.actor.endswith(data.AGENT_SUFFIX) for step in data.HANDOFFS)


def test_every_agent_of_the_description_gets_a_key_and_every_other_name_is_a_person() -> None:
    agents = agents_of_description()
    assert {"planning-agent", "api-agent", "mobile-agent", "ops-agent", "review-agent"} <= set(
        agents
    )
    assert agents == sorted(set(agents)) and data.RECORDER in agents
    assert not any(name.endswith(data.AGENT_SUFFIX) for name in (data.OWNER, data.PLANNER))
    # The people of the team are named in the texts and as the actors of what they did, but they
    # are not agents and get no key.
    assert {"Priya Nair", "Lena Okafor", "Tomas Berg"}.isdisjoint(agents)


def test_the_history_shows_both_people_and_agents_recording_notes() -> None:
    # The planner records the notes she decided, as herself, and the planning agent writes down
    # the rest; the owner records the notes a person records, and confirmations are people's.
    assert {recorder_of(note) for note in decided_notes()} == {data.PLANNER, data.RECORDER}
    assert {note.key for note in data.NOTES} >= data.NOTES_BY_PERSON
    assert {recorder_of(note) for note in plain_notes()} == {data.OWNER, data.RECORDER}
    assert {confirmer_of(step) for step in data.CONFIRMATIONS} == {data.OWNER, data.PLANNER}
    decided_keys = {note.key for note in decided_notes()}
    assert any(
        step.key not in decided_keys and confirmer_of(step) == data.PLANNER
        for step in data.CONFIRMATIONS
    )


def test_the_demo_keeps_three_keys_with_different_allowances_made_from_the_servers_tool_list() -> (
    None
):
    assert set(data.KEPT_KEYS) <= set(agents_of_description())
    tools = [
        {
            "name": "list_nodes",
            "area": "Work",
            "changes_something": False,
            "people_only": False,
            "offered_to_agents": True,
        },
        {
            "name": "export_graph",
            "area": "Moving data",
            "changes_something": False,
            "people_only": False,
            "offered_to_agents": False,
        },
        {
            "name": "create_node",
            "area": "Work",
            "changes_something": True,
            "people_only": False,
            "offered_to_agents": True,
        },
        {
            "name": "add_note",
            "area": "Notes",
            "changes_something": True,
            "people_only": False,
            "offered_to_agents": True,
        },
        {
            "name": "import_graph",
            "area": "Moving data",
            "changes_something": True,
            "people_only": False,
            "offered_to_agents": False,
        },
        {
            "name": "reindex",
            "area": "Search and the index",
            "changes_something": True,
            "people_only": False,
            "offered_to_agents": False,
        },
        {
            "name": "set_confirmation",
            "area": "Notes",
            "changes_something": True,
            "people_only": True,
            "offered_to_agents": False,
        },
    ]
    chosen = allowances(tools)
    # A key that is not narrowed is the standard one (None); the narrowed ones are made only from
    # the tools agents are offered, so they never reach administration tools.
    assert chosen["api-agent"] is None
    assert chosen["review-agent"] == ["list_nodes"]
    assert chosen["docs-agent"] == ["add_note", "create_node", "list_nodes"]


def test_the_team_has_an_owner_an_editor_and_a_viewer_with_names_and_addresses_of_their_own() -> (
    None
):
    names = [data.OWNER, data.PLANNER, data.VIEWER]
    emails = [data.OWNER_EMAIL, data.PLANNER_EMAIL, data.VIEWER_EMAIL]
    assert len({name.lower() for name in names}) == 3 and len(set(emails)) == 3
    assert all(email.endswith("@example.com") for email in emails)
    assert not any(name.endswith(data.AGENT_SUFFIX) for name in names)
    # The viewer can change nothing, so they never act; the planner has things to do.
    assert data.VIEWER not in {step.person for step in data.CONFIRMATIONS}
    assert data.VIEWER not in {edit.actor for edit in data.EDITS}
    assert data.PLANNER in {edit.actor for edit in data.EDITS}
    assert data.PLANNER in data.WORKERS.values()
