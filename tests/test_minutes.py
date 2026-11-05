import pytest

from teamrunner.build import load_team
from teamrunner.minutes import Entry, Minutes, MissingInput, request_for

from test_validate import debate


@pytest.fixture
def team(tmp_path):
    team, _ = load_team(debate(tmp_path, **{
        "for.md": "---\nmodel: openai/gpt-5\n---\nYou argue for: {{topic}}\n\nRounds: {{ rounds }}\n"}))
    return team


INPUTS = {"topic": "Remote work", "rounds": 3}


def test_the_first_turn(team):
    req = request_for(team.members["for"], INPUTS, Minutes(), None)
    assert req.messages == [
        {"role": "system", "content": "You argue for: Remote work\n\nRounds: 3"},
        {"role": "user", "content": "Nothing has been said yet.\n\nIt is your turn."},
    ]
    assert req.read == []


def test_minutes_label_everyone_alike(team):
    minutes = Minutes([Entry(3, "for", "Commutes waste time.\n"), Entry(4, "against", "Hallways matter.")])
    req = request_for(team.members["for"], INPUTS, minutes, "closing")
    assert req.messages[1]["content"] == (
        "for:\nCommutes waste time.\n\nagainst:\nHallways matter.\n\nIt is your turn. Stage: closing.")
    assert req.read == [3, 4]


def test_a_reply_goes_only_to_whoever_asked(team):
    minutes = Minutes([Entry(3, "judge", "Checking."), Entry(5, "web", "3 results", to="judge")])
    assert request_for(team.members["judge"], INPUTS, minutes, None).read == [3, 5]
    assert "web (to judge):\n3 results" in request_for(
        team.members["judge"], INPUTS, minutes, None).messages[1]["content"]
    assert request_for(team.members["for"], INPUTS, minutes, None).read == [3]


def test_the_hash_follows_what_was_sent(team):
    speaker = team.members["for"]
    a = request_for(speaker, INPUTS, Minutes(), None).prompt_hash
    assert a == request_for(speaker, INPUTS, Minutes(), None).prompt_hash
    assert a != request_for(speaker, {**INPUTS, "topic": "Zoos"}, Minutes(), None).prompt_hash
    assert a.startswith("sha256:")


def test_a_missing_input_is_named(team):
    with pytest.raises(MissingInput, match="`rounds`"):
        request_for(team.members["for"], {"topic": "x"}, Minutes(), None)
