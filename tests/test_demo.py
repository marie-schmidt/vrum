"""The demo (./demo.sh): that its script keeps to its own Compose project and refuses what it
should, and that the sample project it fills holds what it is documented to hold. No containers:
what the demo makes in a real stack is looked at by hand, as ./demo.sh's own run (see
tests/demo_world.py) checks the amounts against server_status."""

import asyncio
import datetime
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests import demo_data as data
from tests import demo_details
from tests import scripted_github as github
from tests import scripted_oidc as oidc
from tests.demo_flow import (
    Flow,
    comments_on,
    in_order,
    later_counts,
    spring_plan,
    winter_plan,
    winter_revision,
)
from tests.demo_world import (
    Names,
    agents_of_description,
    allowances,
    ask_arguments,
    asks_of_description,
    check_words,
    confirmer_of,
    derived_criteria,
    expected_counts,
    expected_needs,
    expected_note_counts,
    expected_totals,
    give_a_key_a_last_day,
    kept_of_description,
    place,
    plan_arguments,
    pull_request_number,
    recorder_of,
    verified_words,
    whereabouts,
)
from vrum.asks import AskInput, clean_ask
from vrum.checks import pull_request_address
from vrum.plan_revise import RevisionItem
from vrum.plan_revisions import (
    FIELDS,
    MOST_COMMENT_CHARS,
    MOST_NOTE_CHARS,
    MOST_SUMMARY_CHARS,
    PlanCommentInput,
)
from vrum.plan_templates import expand, parse_template
from vrum.records import PEOPLE_ONLY_TOOLS
from vrum.secret_formats import find_secret
from vrum.whereabouts import clean_whereabouts
from vrum.workflow import PlanItem, PlanLink, PlanRule

SCRIPT = Path(__file__).resolve().parent.parent / "demo.sh"
REFERENCE = re.compile(r"\[\[([a-z0-9][a-z0-9-]*)\]\]")
OPEN = ("new", "in_progress", "blocked")
TODAY = datetime.date(2026, 11, 22)


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
    found += [loose.title for loose in data.LOOSE] + [loose.body for loose in data.LOOSE]
    found += [data.INBOX_BODY, data.GROUPED.epic_title, data.GROUPED.epic_body, data.GROUPED.reason]
    found += [data.SUGGESTION_REJECTED, data.CHECKED_REASON, data.PARTIAL_REASON]
    for gate in data.GATES:
        found += [gate.question or "", gate.comment or ""]
    found += [item.get("title", "") + item.get("body", "") for item in data.WINTER_ITEMS]
    found += [title for _, title in data.WINTER_CRITERIA]
    found += [data.WINTER_FEEDBACK_SUMMARY, data.WINTER_REVISION_NOTE, data.SPRING_TITLE]
    found += [text for _, _, text in (*data.WINTER_FEEDBACK, *data.SPRING_FEEDBACK)]
    found += [data.SPRING_FEEDBACK_SUMMARY]
    found += [text for parts in data.WINTER_REVISED.values() for text in parts.values()]
    found += [item.get("title", "") + item.get("body", "") for item in data.SPRING_ITEMS]
    found += [json.dumps(template) for template in data.TEMPLATES.values()]
    found += [rule.title for rule in data.RULES] + [rule.body for rule in data.RULES]
    found += list(demo_details.TASK_DETAILS.values())
    found += [title for titles in demo_details.ACCEPTS.values() for title in titles]
    found.append(data.DEFINITION_OF_DONE)
    found += [text for wordings in data.CHECK_EVIDENCE for text in wordings]
    found += [*data.VERIFIED_EVIDENCE, data.PULL_REQUEST_SEEN, data.PULL_REQUEST_ADDRESS]
    found += [*data.PERSON_TRIED, data.UNTICK.first, data.UNTICK.reason, data.UNTICK.again]
    for note in data.NOTES:
        found += [note.text, note.why]
    for step in data.HANDOFFS:
        found.append(step.note)
    for _, ask in asks_of_description():
        found += [ask.question, *ask.choices, ask.answer or "", ask.comment or ""]
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
        tags_removed = text.replace("[person] ", "").replace("[verifier] ", "")
        tags_removed = tags_removed.replace("[github] ", "")
        assert not re.search(r"[0-9\[\]]|[\u2010-\u2015\u2212]", tags_removed), text
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
    assert 5 <= len(lines) <= 9 and all(line.startswith("- ") for line in lines)
    text = data.DEFINITION_OF_DONE.lower()
    for word in ("tests", "real phone", "changelog", "half done"):
        assert word in text
    # Exactly one line is for a person alone, and it is the last; one is for a verifier and one is
    # for a verifier with a pull request, just before it, and all of the others are ordinary.
    assert [line for line in lines if "[person]" in line] == [lines[-1]]
    assert [line for line in lines if "[verifier]" in line] == [lines[-3]]
    assert [line for line in lines if "[github]" in line] == [lines[-2]]
    assert lines[-3] == "- [verifier] Reviewed by an agent that did not write it."
    assert lines[-2] == "- [github] The pull request is merged and its checks passed."
    assert not any(line.startswith("- [") for line in lines[:-3])


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
    tags = ("[person]", "[verifier]", "[github]")
    checks = [line for line in lines if not line.startswith(tags)]
    assert len(data.CHECK_EVIDENCE) == len(checks) == len(lines) - 3 == 6
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
        assert link.kind in (
            "needs_first",
            "relates_to",
            "duplicates",
            "supersedes",
            "discovered_from",
        )
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
    assert len(data.HELD) >= 4 and len(data.LAPSED) == 2
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
        {
            "name": "set_check",
            "area": "Definition of done",
            "changes_something": True,
            "people_only": False,
            "offered_to_agents": True,
        },
        {
            "name": "update_node",
            "area": "Work",
            "changes_something": True,
            "people_only": False,
            "offered_to_agents": True,
        },
    ]
    chosen = allowances(tools)
    # A key that is not narrowed is the standard one (None); the narrowed ones are made only from
    # the tools agents are offered, so they never reach administration tools. The verifier may
    # also tick a check and set work done, which is what a verifier does.
    assert chosen["api-agent"] is None
    assert chosen["review-agent"] == ["list_nodes", "set_check", "update_node"]
    assert chosen["docs-agent"] == ["add_note", "create_node", "list_nodes", "update_node"]


def test_the_verifier_is_an_agent_with_a_kept_key_that_never_works_on_what_it_checks() -> None:
    assert data.VERIFIER.endswith(data.AGENT_SUFFIX) and data.VERIFIER in data.KEPT_KEYS
    assert data.VERIFIER in agents_of_description()
    # It is nobody's worker, holds nothing, hands nothing over and edits nothing, so it never did
    # the work on an item whose checks it ticks, which the server would refuse.
    worked = {step.actor for step in data.HANDOFFS} | {held.actor for held in data.HELD}
    worked |= {held.actor for held in data.LAPSED} | {edit.actor for edit in data.EDITS}
    worked |= {actor for _, _, actor in (*data.RULE_PLACES, *data.RULE_MOVES)}
    worked |= {edit.actor for edit in data.RULE_EDITS} | set(data.WORKERS.values())
    assert data.VERIFIER not in worked


def test_work_waits_for_a_verifier_that_is_blocked_handed_over_and_held_by_nobody() -> None:
    names = Names.of_description()
    assert len(data.WAITING_FOR_VERIFIER) >= 3
    held = {held.key for held in (*data.HELD, *data.LAPSED)}
    for key in data.WAITING_FOR_VERIFIER:
        placed = names.node(key)
        assert placed.item.kind == "task" and placed.item.status == "blocked", key
        assert key not in held
        (step,) = [step for step in data.HANDOFFS if step.key == key]
        assert step.outcome == "blocked" and step.actor == data.WORKERS[placed.epic]
        assert "ready for a verifier" in names.text(step.note).lower(), key
        # The note says which pull request to look at, in the form the server reads.
        found = re.search(r"https://\S+/pull/\d+", step.note)
        assert found and pull_request_address(found.group(0)) == found.group(0)
    # No other blocked work is said to wait for a verifier.
    assert {step.key for step in data.HANDOFFS if "verifier" in step.note.lower()} == set(
        data.WAITING_FOR_VERIFIER
    )


def test_what_the_verifier_says_it_saw_is_in_the_form_the_server_reads_and_always_the_same() -> (
    None
):
    today = datetime.date(2026, 11, 22)
    seen = re.compile(
        r"https://github\.com/example-org/bike-share/pull/\d+ merged "
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:00Z, merge commit [0-9a-f]{10}, checks passed (\d+) of \1"
    )
    for placed in place(data.EPICS):
        github_words = verified_words(True, placed, today)
        assert seen.fullmatch(github_words), github_words
        assert pull_request_address(github_words) is not None
        assert github_words == verified_words(True, placed, today)
        assert not find_secret(github_words)
        said = verified_words(False, placed, today)
        assert said.strip() and "{" not in said and not re.search(r"[\u2010-\u2015\u2212]", said)
        assert pull_request_address(said) is None
    # The merge is never in the future, and the address is a made-up organisation's.
    merged = [
        datetime.datetime.fromisoformat(
            verified_words(True, placed, today).split(" merged ")[1].split(",")[0]
        ).date()
        for placed in place(data.EPICS)
    ]
    assert max(merged) < today
    assert "example-org" in data.PULL_REQUEST_ADDRESS


def work_said_about() -> list[data.Handoff | data.Held]:
    """Every handoff note and claim of the description, which each say where the work is or not."""
    return [*data.HANDOFFS, *data.HELD, *data.LAPSED]


def said_in_the_demo() -> list[tuple[str, str, dict[str, str | None]]]:
    """Every handoff note and claim of the description, with what it says about where the work is
    (as the tools are given it) when the demo is made on the day below."""
    names = Names.of_description()
    return [
        (
            step.key,
            step.actor,
            whereabouts(step.where, step.actor, names.node(step.key).item.title, TODAY),
        )
        for step in work_said_about()
    ]


def test_what_agents_say_about_where_their_work_is_is_what_the_server_accepts() -> None:
    said = said_in_the_demo()
    assert sum(1 for _, _, words in said if words) >= 12
    for key, actor, words in said:
        given = {field: value for field, value in words.items() if value is not None}
        assert clean_whereabouts(**given) == given, key
        assert not find_secret(" ".join(given.values())), key
        if given:
            assert given["repository"] == data.REPOSITORY_ADDRESS
        if "pull_request" in given:
            assert given["pull_request"].startswith(data.REPOSITORY_ADDRESS + "/pull/"), key
        # A run label is said with the client it ran in, and never for a person.
        assert ("run_label" in given) == ("client_name" in given), key
        if "client_name" in given:
            assert actor.endswith(data.AGENT_SUFFIX) and given["client_name"] == data.CLIENTS[actor]
    assert "example-org" in data.REPOSITORY_ADDRESS


def test_the_claims_held_now_say_where_the_work_is_in_different_ways() -> None:
    said = {key: words for key, _, words in said_in_the_demo()}
    assert sum(1 for held in data.HELD if held.where) >= 3
    # One says nothing, which is what a claim that says nothing looks like.
    assert any(held.where is None for held in data.HELD)
    assert sum(1 for held in data.HELD if held.where and held.where.pull_request) >= 2
    assert all(held.where for held in data.LAPSED)
    labels: dict[str, set[str | None]] = {}
    for held in data.HELD:
        if held.where and held.where.run:
            labels.setdefault(held.actor, set()).add(said[held.key]["run_label"])
    # Two instances of one key, told apart by their run labels.
    assert any(len(found) >= 2 for found in labels.values())
    assert all(
        held.where is None or held.where.run is None
        for held in data.HELD
        if not held.actor.endswith(data.AGENT_SUFFIX)
    )


def test_the_handoff_of_verified_work_names_the_pull_request_its_github_tick_names() -> None:
    names = Names.of_description()
    for step in data.HANDOFFS:
        if step.where is None:
            continue
        placed = names.node(step.key)
        if step.where.merged:
            # Done work is the work the verifier ticked the [github] line of, with this address.
            assert placed.item.status == "done" and step.outcome == "done", step.key
            said = whereabouts(step.where, step.actor, placed.item.title, TODAY)
            assert said["pull_request"] == pull_request_address(
                verified_words(True, placed, TODAY)
            ), step.key
        elif step.where.pull_request is not None:
            # Work waiting for a verifier says, in its note and in its words, the same address.
            assert step.key in data.WAITING_FOR_VERIFIER, step.key
            found = re.search(r"https://\S+/pull/(\d+)", step.note)
            assert found and int(found.group(1)) == step.where.pull_request, step.key
    for key in data.WAITING_FOR_VERIFIER:
        (step,) = [step for step in data.HANDOFFS if step.key == key]
        assert step.where and step.where.pull_request is not None and not step.where.merged
    # A number that is said outright is never another item's made-up number.
    worked_out = {pull_request_number(item.title) for item in all_items()}
    said_outright = {
        step.where.pull_request
        for step in work_said_about()
        if step.where and step.where.pull_request is not None
    }
    assert not worked_out & said_outright


def test_one_kept_key_runs_out_ninety_days_after_the_day_the_demo_is_made() -> None:
    assert data.EXPIRING_KEY in data.KEPT_KEYS and data.EXPIRING_KEY != data.VERIFIER
    assert data.KEY_EXPIRES_AFTER_DAYS == 90
    sent: list[tuple[str, str, Any]] = []

    class Owner:
        def call(self, method: str, path: str, body: Any) -> SimpleNamespace:
            sent.append((method, path, body))
            return SimpleNamespace(
                status=200, body={"key": {"expires_on": body["expires_on"], "expired": False}}
            )

    day = asyncio.run(give_a_key_a_last_day(cast(Any, Owner()), "/keys", {data.EXPIRING_KEY: 7}))
    assert sent == [("PATCH", "/keys/7", {"expires_on": day})]
    today = datetime.datetime.now(datetime.UTC).date()
    assert (datetime.date.fromisoformat(day) - today).days == 90


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


def how_each_ask_is_put() -> list[tuple[str, data.Ask, str, str]]:
    """Every ask of the description with the key of the work, the agent that puts it and what it
    does to the work: blocks (finish_work blocked, or update_node blocked) or parks (finish_work
    released)."""
    names = Names.of_description()
    put = [
        (asked.key, asked.ask, data.WORKERS[names.node(asked.key).epic], "blocks")
        for asked in data.ASKED
    ]
    for step in data.HANDOFFS:
        if step.ask is not None:
            assert step.outcome in ("blocked", "released"), step.key
            put.append(
                (step.key, step.ask, step.actor, "blocks" if step.outcome == "blocked" else "parks")
            )
    return put


def test_six_asks_three_wait_for_a_person_and_three_were_answered_by_people_with_accounts() -> None:
    put = how_each_ask_is_put()
    assert len(put) == 6 and [(k, a) for k, a, _, _ in put] == asks_of_description()
    waiting = [(key, ask, effect) for key, ask, _, effect in put if ask.answer is None]
    answered = [ask for _, ask, _, _ in put if ask.answer is not None]
    # Two questions with choices that block work and an approval that parks it, waiting.
    assert sorted((bool(ask.choices), ask.approval, effect) for _, ask, effect in waiting) == [
        (False, True, "parks"),
        (True, False, "blocks"),
        (True, False, "blocks"),
    ]
    # A question, an approval and a question with a comment, answered.
    assert any(ask.approval for ask in answered)
    assert any(not ask.approval and ask.comment for ask in answered)
    assert any(not ask.approval and not ask.comment for ask in answered)
    for ask in answered:
        assert ask.by in (data.OWNER, data.PLANNER), ask.question
        options = ["yes", "no"] if ask.approval else list(ask.choices)
        assert ask.answer in options, ask.question
    for _, ask, _, _ in put:
        assert (ask.by is not None) == (ask.answer is not None), ask.question
        assert ask.comment is None or ask.answer is not None, ask.question
    assert len({ask.question for _, ask, _, _ in put}) == 6


def test_every_ask_is_one_the_server_accepts_about_work_that_exists_by_an_agent() -> None:
    names = Names.of_description()
    for key, ask, actor, effect in how_each_ask_is_put():
        arguments = ask_arguments(ask)
        cleaned = clean_ask(AskInput(**arguments))
        assert cleaned["question"] == ask.question and cleaned["kind"] == (
            "approval" if ask.approval else "question"
        )
        assert not (ask.approval and ask.choices), key
        placed = names.node(key)
        assert placed.item.kind in ("story", "task") and actor.endswith(data.AGENT_SUFFIX), key
        assert actor != data.VERIFIER, key
        status = placed.item.status
        if effect == "blocks" and ask.answer is None:
            # Still blocked, and nobody holds it, as the description says.
            assert status == "blocked" and key not in {held.key for held in data.HELD}, key
        elif effect == "blocks":
            # Answered, so free again, and the work went on.
            assert status in ("in_progress", "done"), key
        else:
            assert status != "blocked", key


def test_what_needs_a_person_has_all_four_kinds_in_a_few_rows_under_five_or_six_stories() -> None:
    rows = expected_needs()
    kinds = {kind: sum(1 for found, _, _ in rows if found == kind) for kind, _, _ in rows}
    assert set(kinds) == {"ask", "check", "note", "lapse"}
    assert kinds["ask"] == 3 and kinds["check"] == 1 and kinds["lapse"] == 1 and kinds["note"] >= 1
    assert 8 <= len(rows) <= 12 and len(set(rows)) == len(rows)
    assert 5 <= len({story for _, _, story in rows}) <= 6


def test_work_that_waits_for_a_person_is_blocked_handed_over_and_held_by_nobody() -> None:
    names = Names.of_description()
    held = {held.key for held in (*data.HELD, *data.LAPSED)}
    for key in data.WAITING_FOR_A_PERSON:
        placed = names.node(key)
        assert placed.item.kind == "task" and placed.item.status == "blocked", key
        assert key not in held and key not in data.WAITING_FOR_VERIFIER
        (step,) = [step for step in data.HANDOFFS if step.key == key]
        assert step.outcome == "blocked" and step.ask is None
        assert step.actor == data.WORKERS[placed.epic] or step.actor.endswith(data.AGENT_SUFFIX)
        assert "verifier" not in step.note.lower() and "phone" in step.note.lower()
    # The checks of the definition of done end with the one only a person ticks.
    assert data.DEFINITION_OF_DONE.strip().splitlines()[-1].startswith("- [person]")


def test_one_piece_of_work_has_a_claim_that_ran_out_twice_under_two_different_agents() -> None:
    twice = [held for held in data.LAPSED if held.after]
    assert len(twice) == 1 and len(data.LAPSED) == 2
    (held,) = twice
    assert held.after != held.actor and held.where is not None
    assert {held.actor, held.after} <= set(agents_of_description())
    assert data.VERIFIER not in {held.actor, held.after}
    assert held.key not in {other.key for other in (*data.HELD, *data.LAPSED) if other is not held}
    assert Names.of_description().node(held.key).item.status == "in_progress"


# -- the later part: an Inbox, a steward, gates, drafted plans and templates (M42 to M47) ----------


def test_seven_loose_stories_wait_in_the_inbox_and_two_of_them_group_under_a_new_epic() -> None:
    titles = [loose.title for loose in data.LOOSE]
    assert len(titles) == 7 and len(set(titles)) == 7
    assert not set(titles) & {item.title for item in all_items()}
    # They are written loosely, as said on a call: one or two sentences each, no markdown.
    for loose in data.LOOSE:
        assert 1 <= sentences(loose.body) <= 2 and "#" not in loose.body, loose.title
    assert set(data.GROUPED.stories) <= set(titles) and len(data.GROUPED.stories) == 2
    assert data.GROUPED.epic_title not in {epic.title for epic in data.EPICS}
    assert data.INBOX == "Inbox"
    # The description never says where a loose story belongs: only the server does.
    assert not hasattr(data.LOOSE[0], "epic") and not hasattr(data.LOOSE[0], "parent")


def test_the_steward_proposes_two_loose_stories_by_hand_for_a_person_to_look_at_twice() -> None:
    titles = [loose.title for loose in data.LOOSE]
    assert len(data.CHECKED_STORIES) == 2 and set(data.CHECKED_STORIES) <= set(titles)
    # Not the stories that group under a new epic, and the script's first stories keep their order
    # (the first it proposes is accepted and the second rejected), so the first loose story, the
    # one the loader asks the server about, is the one the steward leaves to be checked.
    assert not set(data.CHECKED_STORIES) & set(data.GROUPED.stories)
    assert data.CHECKED_STORIES[0] == titles[0]
    assert data.CHECKED_REASON and "\u2014" not in data.CHECKED_REASON


def test_the_steward_proposes_two_tasks_for_a_person_to_accept_in_part() -> None:
    names = Names.of_description()
    assert len(data.PARTIAL_ITEMS) == 2 and data.PARTIAL_LEFT_IN in data.PARTIAL_ITEMS
    parts = [names.node(key) for key in data.PARTIAL_ITEMS]
    target = names.node(data.PARTIAL_PARENT)
    # Two tasks of one story that nobody works on, holds or waits for, and another story to move
    # them under, so that each move is real and the one left out is only left out by the person.
    assert {one.item.kind for one in parts} == {"task"} and target.item.kind == "story"
    assert {one.item.status for one in parts} == {"new"}
    assert len({one.parent for one in parts}) == 1 and parts[0].parent != target.key
    busy = {step.key for step in data.HANDOFFS} | {held.key for held in data.HELD}
    busy |= {held.key for held in data.LAPSED} | {gate.key for gate in data.GATES}
    busy |= {asked.key for asked in data.ASKED}
    assert not set(data.PARTIAL_ITEMS) & busy
    assert not [link for link in data.LINKS if set(data.PARTIAL_ITEMS) & {link.source, link.target}]
    assert data.PARTIAL_REASON and "\u2014" not in data.PARTIAL_REASON
    # The steward is allowed to propose it, and it is not one of the proposals about the Inbox.
    assert "propose_placement" in data.STEWARD_TOOLS
    assert not set(data.PARTIAL_ITEMS) & {loose.title for loose in data.LOOSE}


def test_one_story_of_the_winter_plan_repeats_work_that_exists_in_other_words() -> None:
    repeated = [item for item in data.WINTER_ITEMS if item["title"] == data.WINTER_REPEATS]
    assert len(repeated) == 1 and repeated[0]["kind"] == "story"
    # It is not the same title as anything that exists: it is found by meaning, not by words.
    assert data.WINTER_REPEATS not in {item.title for item in all_items()}
    existing = [item for item in all_items() if item.title == "Battery swap schedule"]
    assert len(existing) == 1 and existing[0].key == "battery" and existing[0].status == "new"


def test_the_steward_may_use_four_tools_and_none_that_a_person_decides_with() -> None:
    assert set(data.STEWARD_TOOLS) == {
        "list_nodes",
        "suggest_parent",
        "list_placements",
        "propose_placement",
    }
    assert not set(data.STEWARD_TOOLS) & PEOPLE_ONLY_TOOLS
    assert data.STEWARD not in agents_of_description()
    assert data.INTAKE in agents_of_description() and data.INTAKE.endswith(data.AGENT_SUFFIX)
    assert data.STEWARD not in data.KEPT_KEYS


def test_gates_hold_work_nobody_has_taken_and_show_every_state() -> None:
    names = Names.of_description()
    needs = {(link.source, link.target) for link in data.LINKS if link.kind == "needs_first"}
    status = {key: names.node(key).item.status for key in names.nodes}
    busy = {step.key for step in data.HANDOFFS} | {held.key for held in data.HELD}
    busy |= {held.key for held in data.LAPSED} | {asked.key for asked in data.ASKED}
    busy |= set(data.WAITING_FOR_VERIFIER) | set(data.WAITING_FOR_A_PERSON)
    for gate in data.GATES:
        placed = names.node(gate.key)
        assert placed.item.kind == "task" and status[gate.key] == "new", gate.key
        assert gate.key not in busy and gate.actor.endswith(data.AGENT_SUFFIX), gate.key
        assert gate.actor == data.WORKERS[placed.epic], gate.key
        # Free to take until it is gated: whatever it needs first is finished.
        assert all(status[target] == "done" for source, target in needs if source == gate.key)
    assert len({gate.key for gate in data.GATES}) == len(data.GATES)
    timers = [gate for gate in data.GATES if gate.kind == "timer"]
    approvals = [gate for gate in data.GATES if gate.kind == "approval"]
    assert len(timers) == 1 and timers[0].days is not None and 7 <= timers[0].days <= 30
    assert len(approvals) == 3 and all(gate.question for gate in approvals)
    for gate in approvals:
        assert gate.days is None
        cleaned = clean_ask(AskInput(question=gate.question or "", approval=True))
        assert cleaned["kind"] == "approval"
        assert (gate.answer is None) == (gate.by is None), gate.key
        assert gate.comment is None or gate.answer is not None, gate.key
    # One is waiting for a person, one was refused (the work stays held), one approved.
    assert sorted(str(gate.answer) for gate in approvals) == ["None", "no", "yes"]
    for gate in approvals:
        assert gate.by in (None, data.OWNER, data.PLANNER)


def test_the_drafted_winter_plan_hangs_under_an_existing_epic_and_is_worth_reviewing() -> None:
    names = Names.of_description()
    items = data.WINTER_ITEMS
    keys = [str(item["key"]) for item in items]
    assert len(set(keys)) == len(keys) and not set(keys) & set(names.nodes)
    kinds = [item["kind"] for item in items]
    # No epic of its own: the stories hang under one that exists, so the review has context.
    assert (kinds.count("epic"), kinds.count("story"), kinds.count("task")) == (0, 3, 5)
    assert names.node(data.WINTER_UNDER).item.kind == "epic"
    assert data.WINTER_TITLE not in {epic.title for epic in data.EPICS}
    by_key = {str(item["key"]): item for item in items}
    for item in items:
        parent = str(item["parent"])
        if item["kind"] == "story":
            assert parent == data.WINTER_UNDER, item["key"]
        else:
            assert by_key[parent]["kind"] == "story", item["key"]
        assert item.get("size", "m") in ("xs", "s", "m", "l", "xl")
    # What makes it worth reviewing: one item left unsized on purpose, one high priority, details
    # of two or three lines on each story and on three tasks (one of them is taken out by the
    # second revision), dates on one story and a criterion on each story and on one task.
    assert [key for key, item in by_key.items() if "size" not in item] == ["fit-covers"]
    assert [key for key, item in by_key.items() if item.get("priority") == "high"] == ["studded"]
    for key, item in by_key.items():
        lines = len(item.get("body", "").splitlines())
        if item["kind"] == "story":
            assert 2 <= lines <= 3, key
    assert sorted(
        key for key, item in by_key.items() if item["kind"] == "task" and item.get("body")
    ) == [
        "chains",
        "fit-covers",
        "fit-tyres",
    ]
    assert list(data.WINTER_DAYS) == ["studded"] and by_key["studded"]["kind"] == "story"
    start, target = data.WINTER_DAYS["studded"]
    assert 0 < start < target
    on = [node for node, _ in data.WINTER_CRITERIA]
    assert sorted(on) == ["covers", "fit-tyres", "studded", "winter-battery"]
    for source, target_key, kind in data.WINTER_LINKS:
        assert kind in ("needs_first", "relates_to") and source in by_key and source != target_key
        if kind == "needs_first":
            assert by_key[source]["kind"] == by_key[target_key]["kind"] == "task"
    relates = [link for link in data.WINTER_LINKS if link[2] == "relates_to"]
    assert len(relates) == 1 and relates[0][1] not in by_key
    assert names.node(relates[0][1]).item.kind == "story"
    assert sum(link[2] == "needs_first" for link in data.WINTER_LINKS) >= 1


def test_the_winter_plan_is_written_as_the_references_the_server_gave_and_create_plan_takes() -> (
    None
):
    names = Names.of_description()
    arguments = winter_plan(TODAY, lambda key: names.node(key).ref)
    assert arguments == winter_plan(TODAY, lambda key: names.node(key).ref)
    items = [PlanItem(**item) for item in arguments["items"]]
    links = [PlanLink(**link) for link in arguments["links"]]
    rules = [PlanRule(**rule) for rule in arguments["rules"]]
    under = names.node(data.WINTER_UNDER).ref
    assert [str(item.parent) for item in items if item.kind == "story"] == [under] * 3
    assert {str(item.parent) for item in items if item.kind == "task"} == {"studded", "covers"}
    assert [(item.start_on, item.target_on) for item in items if item.start_on] == [
        ("2026-12-02", "2027-01-06")
    ]
    reached = names.node(next(t for _, t, kind in data.WINTER_LINKS if kind == "relates_to")).ref
    assert [
        (str(link.source), str(link.target)) for link in links if link.link_type == "relates_to"
    ] == [("studded", reached)]
    assert [str(rule.node) for rule in rules] == [
        "studded",
        "covers",
        "winter-battery",
        "fit-tyres",
    ]


def test_the_winter_plan_has_a_conversation_whose_revision_changes_only_what_was_asked() -> None:
    names = Names.of_description()
    by_key = {str(item["key"]): item for item in data.WINTER_ITEMS}
    # The owner commented on the title of a story, on the details of a task and on a whole task
    # that she wants dropped.
    assert [(key, part) for key, part, _ in data.WINTER_FEEDBACK] == [
        ("studded", "title"),
        ("fit-tyres", "body"),
        ("chains", None),
    ]
    assert by_key["studded"]["kind"] == "story" and by_key["fit-tyres"]["kind"] == "task"
    for key, part, text in data.WINTER_FEEDBACK:
        assert key in by_key and (part is None or part in FIELDS)
        assert PlanCommentInput(item="story:1", field=part, text=text).text == text
        assert 0 < len(text) <= MOST_COMMENT_CHARS and "\u2014" not in text
    assert 0 < len(data.WINTER_FEEDBACK_SUMMARY) <= MOST_SUMMARY_CHARS
    assert 0 < len(data.WINTER_REVISION_NOTE) <= MOST_NOTE_CHARS
    # She approved four items as they were; none of them is commented on, none is taken out and the
    # story the check for look-alikes names is not among them.
    commented = {key for key, _, _ in data.WINTER_FEEDBACK}
    assert len(data.WINTER_APPROVED) == len(set(data.WINTER_APPROVED)) == 4
    assert set(data.WINTER_APPROVED) <= set(by_key) - commented - set(data.WINTER_REMOVED)
    assert "winter-battery" not in data.WINTER_APPROVED
    # The revision changes exactly the two parts commented on, to other words, takes out the task
    # she asked to drop (a task without tasks of its own, with nothing waiting for it) and changes
    # nothing else.
    assert {key: set(parts) for key, parts in data.WINTER_REVISED.items()} == {
        "studded": {"title"},
        "fit-tyres": {"body"},
    }
    for key, parts in data.WINTER_REVISED.items():
        for part, words in parts.items():
            assert words.strip() and words != by_key[key][part], (key, part)
    assert data.WINTER_REMOVED == ("chains",)
    assert by_key["chains"]["kind"] == "task" and "chains" not in data.WINTER_APPROVED
    assert not [item for item in data.WINTER_ITEMS if item.get("parent") == "chains"]
    assert not [link for link in data.WINTER_LINKS if "chains" in link[:2]]
    assert not [node for node, _ in data.WINTER_CRITERIA if node == "chains"]
    now = {str(item["key"]): item for item in data.WINTER_NOW}
    assert list(now) == [key for key in by_key if key != "chains"]
    assert {key for key in now if now[key] != by_key[key]} == {"studded", "fit-tyres"}
    # The story that repeats existing work and the one left unsized are as they were drafted, so
    # the plan's checks do not change with the revision.
    assert now["winter-battery"] == by_key["winter-battery"]
    assert now["fit-covers"] == by_key["fit-covers"] and "size" not in now["fit-covers"]
    # What revise_plan is given: the whole plan again less the task taken out, every item
    # continuing its own, links and criteria left out (so they are kept), and nothing different
    # from the draft but those parts.
    refs = {key: f"{by_key[key]['kind']}:{number}" for number, key in enumerate(by_key, 1)}
    under = names.node(data.WINTER_UNDER).ref
    drafted = [
        item
        for item in winter_plan(TODAY, lambda key: names.node(key).ref)["items"]
        if item["key"] not in data.WINTER_REMOVED
    ]
    revision = winter_revision(TODAY, lambda key: names.node(key).ref, refs)
    parsed = [RevisionItem(**item) for item in revision]
    assert [str(item.ref) for item in parsed] == [
        ref for key, ref in refs.items() if key not in data.WINTER_REMOVED
    ]
    assert [str(item.parent) for item in parsed if item.kind == "story"] == [under] * 3
    for before, after in zip(drafted, revision, strict=True):
        key = str(before["key"])
        assert {**after, "ref": None} == {**before, **data.WINTER_REVISED.get(key, {}), "ref": None}
        assert after["ref"] == refs[key]
    assert [item.start_on for item in parsed if item.start_on] == ["2026-12-02"]
    # The comments are written as request_plan_changes takes them, each on its item's reference,
    # and a comment on a whole item names no part.
    comments = comments_on(refs, data.WINTER_FEEDBACK)
    assert [(c["item"], c.get("field")) for c in comments] == [
        (refs["studded"], "title"),
        (refs["fit-tyres"], "body"),
        (refs["chains"], None),
    ]
    assert [str(ref) for ref in in_order(["task:9", "story:2", "task:3", "epic:7"])] == [
        "epic:7",
        "story:2",
        "task:3",
        "task:9",
    ]


def test_the_spring_plan_is_small_and_sized_and_its_request_waits_for_its_agent() -> None:
    names = Names.of_description()
    items = data.SPRING_ITEMS
    keys = [str(item["key"]) for item in items]
    assert [item["kind"] for item in items] == ["story", "task", "task"]
    assert len(set(keys)) == 3 and not set(keys) & (
        set(names.nodes) | {str(item["key"]) for item in data.WINTER_ITEMS}
    )
    assert all(item.get("size") in ("xs", "s", "m", "l", "xl") for item in items)
    assert data.SPRING_TITLE not in {epic.title for epic in data.EPICS}
    assert data.SPRING_TITLE != data.WINTER_TITLE
    # The operations agent works on the epic it hangs under, as it does for every fleet item.
    assert data.WORKERS[data.WINTER_UNDER] == data.SPRING_BY
    assert data.SPRING_BY.endswith(data.AGENT_SUFFIX) and data.SPRING_BY != data.RECORDER
    arguments = spring_plan(lambda key: names.node(key).ref)
    assert arguments == spring_plan(lambda key: names.node(key).ref)
    parsed = [PlanItem(**item) for item in arguments["items"]]
    assert str(parsed[0].parent) == names.node(data.WINTER_UNDER).ref
    assert {str(item.parent) for item in parsed[1:]} == {"spring-check"}
    # The owner asked about the story's details and about a size, and said why in a summary.
    assert [(key, part) for key, part, _ in data.SPRING_FEEDBACK] == [
        ("spring-check", "body"),
        ("spring-replace", "size"),
    ]
    for key, part, text in data.SPRING_FEEDBACK:
        assert key in keys and part in FIELDS and 0 < len(text) <= MOST_COMMENT_CHARS
    assert 0 < len(data.SPRING_FEEDBACK_SUMMARY) <= MOST_SUMMARY_CHARS
    # Nothing is revised: there is no revision of it in the description.
    assert not hasattr(data, "SPRING_REVISED")


def test_both_templates_are_ones_the_server_accepts_and_make_the_plans_described() -> None:
    assert sorted(data.TEMPLATES) == ["release-checklist", "station-launch-checklist"]
    for name, template in data.TEMPLATES.items():
        parsed = parse_template(template)
        assert parsed.description and name == name.lower()
    station = parse_template(data.TEMPLATES[data.STATION_LAUNCH])
    values = {**data.LAUNCH_VALUES, "epic": "epic:5"}
    items, links, rules = expand(station, values)
    assert data.LAUNCH_VALUES["neighbourhood"] in data.NEIGHBOURHOODS
    assert [i["kind"] for i in items] == ["story", "task", "task", "task", "task"]
    assert items[0]["parent"] == "epic:5" and "Millbank Quay" in items[0]["title"]
    assert len(links) == 3 and len(rules) == 1
    release = parse_template(data.TEMPLATES[data.RELEASE_CHECKLIST])
    made, _, _ = expand(release, {"version": "2.4"})
    assert made[0]["title"] == "Release 2.4" and made[-1]["title"] == "Tag 2.4"
    assert data.LAUNCH_EPIC in Names.of_description().nodes
    assert data.LAUNCH_BY.endswith(data.AGENT_SUFFIX) and data.LAUNCH_TITLE.startswith("Launch")


def test_one_answer_is_kept_as_a_decision_by_the_person_who_gave_it() -> None:
    (kept,) = kept_of_description()
    key, ask = kept
    # Put when work is handed over, so that the numbers of the notes of the description hold, and
    # on work that nobody is working on, so that the unconfirmed decision adds no row to what needs
    # a person.
    assert key in {step.key for step in data.HANDOFFS if step.ask is not None}
    assert ask.answer is not None and ask.by in (data.OWNER, data.PLANNER) and ask.comment
    assert Names.of_description().node(key).item.status not in ("in_progress", "blocked", "done")
    assert key not in {held.key for held in data.HELD} | {step.key for step in data.LAPSED}
    assert expected_totals()["notes"] == expected_counts()["notes"] + 1


NODES: dict[str, dict[str, Any]] = {
    "epic:1": {"kind": "epic", "ref": "epic:1", "title": "Billing", "parent": None},
    "story:1": {"kind": "story", "ref": "story:1", "title": "Pay", "parent": "epic:1"},
    "task:1": {"kind": "task", "ref": "task:1", "title": "Charge", "parent": "story:1"},
    "epic:2": {"kind": "epic", "ref": "epic:2", "title": "Appearance", "parent": None},
    "story:2": {"kind": "story", "ref": "story:2", "title": "Theme", "parent": "epic:2"},
    "task:2": {"kind": "task", "ref": "task:2", "title": "Night", "parent": "story:2"},
}


class TheServer:
    """Answers the reads that the check of the standard review makes, from NODES."""

    async def call(self, tool: str, actor: str | None = None, **arguments: Any) -> dict[str, Any]:
        assert tool == "get_node"
        return NODES[arguments["node"]]


def a_row(
    kind: str,
    node: str,
    level: str,
    decision: str,
    topic: dict[str, str] | None,
    plan: dict[str, Any] | None = None,
    placement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "node": node,
        "level": level,
        "decision": decision,
        "topic": topic,
        "plan": plan,
        "placement": placement,
    }


def the_rows_of_a_list() -> list[dict[str, Any]]:
    billing = {"id": "epic:1", "label": "Billing", "basis": "epic"}
    theme = {"id": "epic:2", "label": "Appearance", "basis": "epic"}
    together = {"id": "topic:3", "label": "Charge", "basis": "meaning"}
    return [
        a_row("ask", "task:1", "task", "answer", billing),
        a_row("note", "task:1", "note", "approve", billing),
        a_row("plan", "epic:2", "plan", "approve", theme, plan={"items": ["epic:2", "story:2"]}),
        a_row(
            "placement",
            "task:2",
            "task",
            "approve",
            theme,
            placement={"parent": "story:2", "items": [{"ref": "task:2"}]},
        ),
        a_row("gate", "task:2", "task", "look_into", theme),
        a_row("lapse", "task:1", "task", "look_into", together),
    ]


def read_the_standard_review(rows: list[dict[str, Any]]) -> str:
    flow = Flow(cast(Any, TheServer()))
    needs = {"groups": [{"story": None, "title": None, "items": rows}]}
    return asyncio.run(flow.read_the_standard_review(needs))


def test_the_loader_reads_the_level_decision_and_topic_of_every_row_by_relations_only() -> None:
    said = read_the_standard_review(the_rows_of_a_list())
    assert "6 rows" in said and "1 put together by meaning" in said and "3 topics" in said


def test_an_epic_can_name_a_cluster_of_meaning_and_a_row_that_is_only_under_it() -> None:
    rows = the_rows_of_a_list()
    rows[1]["topic"] = {"id": "epic:1", "label": "Billing", "basis": "meaning"}
    said = read_the_standard_review(rows)
    assert "3 topics" in said and "2 put together by meaning" in said


def a_second_name(rows: list[dict[str, Any]]) -> None:
    rows[1]["topic"] = {**rows[1]["topic"], "label": "Billing and more"}


WRONG_ROWS = [
    (lambda rows: rows[0].update(level="story"), "says story and answer, not task and answer"),
    (lambda rows: rows[1].update(level="task"), "says task and approve, not note and approve"),
    (lambda rows: rows[3].update(level="story"), "says story and approve, not task and approve"),
    (lambda rows: rows[4].update(decision="approve"), "not task and look_into"),
    (lambda rows: rows[0].update(topic=None), "has no topic"),
    (a_second_name, "called two things"),
    (lambda rows: rows[2]["topic"].update(label="Theme"), "is called Theme, not Appearance"),
    (
        lambda rows: rows[5].update(topic={"id": "elsewhere:9", "label": "x", "basis": "meaning"}),
        "not an epic and not one of meaning",
    ),
    (lambda rows: rows[0].update(topic=dict(rows[4]["topic"])), "under another epic"),
]


@pytest.mark.parametrize(("change", "wanted"), WRONG_ROWS)
def test_the_loader_stops_with_a_plain_message_when_a_row_says_the_wrong_thing(
    change: Any, wanted: str
) -> None:
    rows = the_rows_of_a_list()
    change(rows)
    with pytest.raises(SystemExit, match=wanted):
        read_the_standard_review(rows)


def test_what_the_later_part_adds_is_counted_and_the_totals_are_sensible() -> None:
    added = later_counts()
    assert added == {"epics": 1, "stories": 12, "tasks": 11, "links": 6, "rules": 5}
    totals = expected_totals()
    base = expected_counts()
    kept = len(kept_of_description())
    assert all(
        totals[kind] == base[kind] + added.get(kind, 0) + (kept if kind == "notes" else 0)
        for kind in base
    )
    assert (totals["epics"], totals["stories"], totals["tasks"]) == (13, 65, 191)
    assert totals["links"] == len(data.LINKS) + 6
    assert totals["notes"] == 40


def test_four_tasks_say_where_they_were_found_and_never_wait_for_it() -> None:
    found = [link for link in data.LINKS if link.kind == data.DISCOVERED]
    assert len(found) == 4 and len({link.source for link in found}) == 4
    names = Names.of_description()
    other = {(link.source, link.target) for link in data.LINKS if link.kind != data.DISCOVERED}
    for link in found:
        for key in (link.source, link.target):
            assert names.node(key).item.kind == "task", key
        # The pair is not linked another way too, so each link says one thing.
        assert (link.source, link.target) not in other
        assert (link.target, link.source) not in other


def the_blame_of_the_winter_plan() -> tuple[Flow, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The winter plan as list_plans says it at revision 2 and at revision 1 (only `blame`, and
    the number and comments of the owner's feedback), as the description says it should be."""
    flow = Flow(cast(Any, SimpleNamespace()))
    flow.winter_refs = {
        str(item["key"]): f"{item['kind']}:{number}"
        for number, item in enumerate(data.WINTER_ITEMS, start=1)
    }
    refs = flow.winter_refs
    given = {"id": 7, "comments": [{"item": refs[key]} for key, _, _ in data.WINTER_FEEDBACK]}

    def line(key: str, now: bool) -> dict[str, Any]:
        removed, changed = key in data.WINTER_REMOVED, key in data.WINTER_REVISED
        asked = key in {k for k, _, _ in data.WINTER_FEEDBACK}
        approved = key in data.WINTER_APPROVED
        if now and (removed or changed):
            how, revision, note = (
                ("removed" if removed else "changed"),
                2,
                data.WINTER_REVISION_NOTE,
            )
        else:
            how, revision, note = "added", 1, None
        word = "approved" if approved else ("changed" if now else "asked") if asked else "none"
        if now and removed:
            word = "none"
        said = word != "none"
        return {
            "ref": refs[key],
            "change": how,
            "revision": revision,
            "by": data.RECORDER,
            "by_kind": "agent",
            "note": note,
            "review": {
                "state": word,
                "revision": 1 if said else None,
                "by": data.OWNER if said else None,
                "feedback": 7 if said else None,
            },
        }

    winter = {"blame": [line(str(item["key"]), True) for item in data.WINTER_ITEMS]}
    first = {"blame": [line(str(item["key"]), False) for item in data.WINTER_ITEMS]}
    return flow, winter, first, given


def test_the_loader_reads_who_changed_each_line_of_the_winter_plan_and_what_the_owner_said() -> (
    None
):
    flow, winter, first, given = the_blame_of_the_winter_plan()
    said = flow.check_the_blame(winter, first, given)
    assert said == "4 approved and unchanged, 2 changed since she looked, 1 removed"


BLAME_WRONG = [
    (lambda w, f: w["blame"].pop(), "does not name each line"),
    (
        lambda w, f: w["blame"][0]["review"].update(state="none"),
        "at revision 2 is not as described",
    ),
    (lambda w, f: w["blame"][2]["review"].update(state="approved"), "at revision 2 is not"),
    (lambda w, f: w["blame"][5]["review"].update(by="Someone"), "at revision 2 is not"),
    (lambda w, f: w["blame"][0].update(note="x"), "carry the note"),
    (lambda w, f: f["blame"][4]["review"].update(state="approved"), "at revision 1 is not"),
    (lambda w, f: f["blame"][5].update(revision=2), "at revision 1 is not"),
]


@pytest.mark.parametrize(("change", "wanted"), BLAME_WRONG)
def test_the_loader_stops_when_the_blame_is_not_what_the_description_says(
    change: Any, wanted: str
) -> None:
    flow, winter, first, given = the_blame_of_the_winter_plan()
    change(winter, first)
    with pytest.raises(SystemExit, match=wanted):
        flow.check_the_blame(winter, first, given)
