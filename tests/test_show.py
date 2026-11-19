# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
import pytest

from teamrunner.show import NotAMember, show

from test_tools import LIBRARY
from test_validate import debate


def test_show_marks_where_each_value_came_from(tmp_path):
    team = debate(tmp_path, **{
        "team.yaml": "default_model: careful.yaml\ndefault_inputs:\n  topic: Cars\n",
        "models/careful.yaml": "name: openai/gpt-5\nparams:\n  temperature: 0.2\n  max_tokens: 500\n",
        "for.md": "---\nparams:\n  temperature: 0.9\n---\nArgue for {{topic}} and {{side}}.\n",
    })
    text, _ = show(str(team / "for.md"), tmp_path)
    lines = text.splitlines()
    assert lines[0].startswith("# for — ")
    assert "kind: llm" in text and "# default" in text
    assert any(l.startswith("model: openai/gpt-5") and
               "from models/careful.yaml, named by default_model: in team.yaml" in l for l in lines)
    assert "  temperature: 0.9" in lines  # its own, so no note
    assert any(l.startswith("  max_tokens: 500") and "from models/careful.yaml" in l for l in lines)
    assert "#   topic — default from team.yaml: Cars" in lines
    assert "#   side — no default: every run must give it" in lines


def test_show_a_toolkeeper_with_its_terms_spelled_out(tmp_path):
    team = debate(tmp_path, **{"library.md": LIBRARY.replace("calls_per_turn: 2, ", "")})
    text, _ = show("library", team)
    assert '  read_page: {"calls_per_turn": 5, "timeout": 1}' in text
    assert "calls_per_turn: 5 is the default" in text
    assert '  judge: {"calls_per_turn": 3, "timeout": 1, "calls_per_run": 4}' in text
    assert "others: deny" in text


def test_show_names_the_members_when_one_is_missing(tmp_path):
    team = debate(tmp_path)
    with pytest.raises(NotAMember, match="its members are against, for, judge"):
        show("jduge", team)


def test_show_lists_the_backup_models(tmp_path):
    team = debate(tmp_path, **{
        "team.yaml": "default_model: openai/gpt-5, careful.yaml\ndefault_inputs:\n  topic: Cars\n",
        "models/careful.yaml": "name: anthropic/claude-sonnet-4-5\nparams:\n  temperature: 0.2\n",
        "for.md": "---\n---\nArgue for {{topic}}.\n",
    })
    text, _ = show(str(team / "for.md"), tmp_path)
    assert "model: openai/gpt-5" in text
    assert "# if it is not available: careful.yaml — the first one available runs; each brings its own settings" in text



def test_show_finds_a_team_in_the_workspace(tmp_path, monkeypatch, capsys):
    """C10: `show debate/judge` worked only from inside the team's parent folder."""
    from teamrunner.cli import main
    space = tmp_path / "space"
    debate(space / "teams" / "debate")
    monkeypatch.setenv("TEAMRUNNER_WORKSPACE", str(space))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert main(["show", "debate/judge"]) == 0
    assert capsys.readouterr().out.startswith("# judge — debate/judge.md")
    assert main(["show", "judge"]) == 1
    assert "write `<team>/<member>`, such as `debate/judge`" in capsys.readouterr().err
