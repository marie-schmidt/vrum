"""The grouping of rows into topics (M52), on a table of similarities with no database."""

from vrum.needs_topics import TOPIC_SIMILARITY, NeedsTopic, clusters, name_topics


def test_rows_that_are_alike_join_and_a_row_apart_stays_alone() -> None:
    similarity = {(0, 1): 0.9, (0, 2): 0.05, (1, 2): 0.1}
    assert clusters([0, 1, 2], similarity) == [[0, 1], [2]]


def test_the_average_decides_not_the_closest_pair() -> None:
    # 0 and 1 are alike, 1 and 2 are alike enough, but 0 and 2 are not: single-link would chain
    # all three, the average of the two groups (0.6 + 0.1) / 2 does not.
    similarity = {(0, 1): 0.9, (1, 2): 0.6, (0, 2): 0.1}
    assert clusters([0, 1, 2], similarity) == [[0, 1], [2]]
    close = {(0, 1): 0.9, (1, 2): 0.7, (0, 2): 0.6}
    assert clusters([0, 1, 2], close) == [[0, 1, 2]]


def test_nothing_alike_gives_every_row_its_own_group() -> None:
    assert clusters([3, 1, 2], {}) == [[1], [2], [3]]
    assert clusters([], {}) == []
    assert clusters([5], {}) == [[5]]


def test_the_threshold_is_a_floor_not_a_ceiling() -> None:
    assert clusters([0, 1], {(0, 1): TOPIC_SIMILARITY}) == [[0, 1]]
    assert clusters([0, 1], {(0, 1): TOPIC_SIMILARITY - 0.01}) == [[0], [1]]


def test_the_answer_does_not_depend_on_the_order_the_rows_come_in() -> None:
    similarity = {(0, 1): 0.8, (0, 2): 0.8, (1, 2): 0.8, (3, 4): 0.9}
    assert clusters([4, 3, 2, 1, 0], similarity) == clusters([0, 1, 2, 3, 4], similarity)


def test_a_group_under_one_epic_takes_the_epics_name_and_its_lone_rows_join_it() -> None:
    billing = ("epic:1", "Billing")
    epics = [billing, billing, billing, ("epic:2", "Dark mode"), None]
    titles = ["Charge a card", "Send the receipt", "Refund policy", "Night theme", "Loose"]
    found = name_topics(epics, titles, [[0, 1], [2], [3], [4]], {(0, 1): 0.9})
    assert found[0] == found[1] == NeedsTopic(id="epic:1", label="Billing", basis="meaning")
    # Row 2 was alone in its meaning, but is under the same epic, so it is found with the others.
    assert found[2] is not None and found[2].id == "epic:1" and found[2].basis == "epic"
    assert found[3] == NeedsTopic(id="epic:2", label="Dark mode", basis="epic")
    assert found[4] is None


def test_a_group_across_epics_is_named_by_the_work_closest_to_the_others() -> None:
    epics: list[tuple[str, str] | None] = [
        ("epic:1", "Billing"),
        ("epic:2", "Support"),
        ("epic:3", "Growth"),
    ]
    titles = ["Charge a card", "Refund a charge", "Email the receipt"]
    similarity = {(0, 1): 0.9, (0, 2): 0.6, (1, 2): 0.6}
    found = name_topics(epics, titles, [[0, 1, 2]], similarity)
    assert found[0] == found[1] == found[2]
    assert found[0] == NeedsTopic(id="topic:1", label="Charge a card", basis="meaning")


def test_most_of_a_group_under_one_epic_is_enough_to_name_it() -> None:
    epics: list[tuple[str, str] | None] = [
        ("epic:1", "Billing"),
        ("epic:1", "Billing"),
        ("epic:2", "Support"),
    ]
    titles = ["a", "b", "c"]
    found = name_topics(epics, titles, [[0, 1, 2]], {(0, 1): 0.9, (0, 2): 0.9, (1, 2): 0.9})
    assert {topic.label for topic in found if topic} == {"Billing"}
