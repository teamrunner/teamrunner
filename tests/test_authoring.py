# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
import re
from pathlib import Path

from teamrunner.validate import validate

AGENTS = Path(__file__).resolve().parents[1] / "src" / "teamrunner" / "authoring" / "AGENTS.md"


def blocks(text):
    return re.findall(r"```(\w*)\n(.*?)```", text, re.DOTALL)


def test_the_samples_in_agents_md_make_a_valid_team(tmp_path):
    """The instructions teach by example; every sample in them must validate."""
    found = blocks(AGENTS.read_text(encoding="utf-8"))
    yaml = [b for kind, b in found if kind == "yaml"]
    md = [b for kind, b in found if kind == "markdown"]
    flow = [b for kind, b in found if kind == "" and "->" in b]
    team = tmp_path / "debate"
    team.mkdir()
    (team / "team.yaml").write_text(yaml[0], encoding="utf-8")
    (team / "fact_checker.md").write_text(md[0], encoding="utf-8")
    (team / "web.md").write_text(md[1], encoding="utf-8")
    (team / "chair.md").write_text(flow[0], encoding="utf-8")
    (team / "models").mkdir()
    (team / "models" / "careful.yaml").write_text(yaml[1], encoding="utf-8")
    for name in ("for", "against"):
        (team / f"{name}.md").write_text(f"---\nmodel: careful.yaml\n---\nYou argue about {{{{topic}}}}.\n", encoding="utf-8")
    (team / "judge.md").write_text("---\nmodel: openai/gpt-5\noutput:\n  winner: [for, against]\n---\n"
                                   "You judge the debate on {{topic}}.\n", encoding="utf-8")
    report = validate(team)
    assert report.problems.errors == [], [(p.file.name, p.line, p.message) for p in report.problems.errors]


def test_agents_md_names_only_tools_and_limits_that_exist():
    from teamrunner.tool_registry import GENERAL_GUARDRAILS, TOOLS
    text = AGENTS.read_text(encoding="utf-8")
    table = text[text.index("| Tool | Does |"):text.index("Every tool also understands")]
    for tool, limits in TOOLS.items():
        row = next(line for line in table.splitlines() if line.startswith(f"| `{tool}`"))
        for limit in limits:
            assert f"`{limit}" in row, (tool, limit)
    for limit in GENERAL_GUARDRAILS:
        assert f"`{limit}`" in text


# --- teamrunner new --describe ------------------------------------------------------

import json  # noqa: E402

import pytest  # noqa: E402

from teamrunner.authoring import skill  # noqa: E402
from teamrunner.describe import describe  # noqa: E402
from teamrunner.model import Reply  # noqa: E402
from teamrunner.new import NewError  # noqa: E402

TEAM = {"team.yaml": "name: debate\ndefault_inputs:\n  topic: \"Zoos\"\n",
        "for.md": "---\nmodel: openai/gpt-5\n---\nYou argue for {{topic}}.\n",
        "judge.md": "---\nmodel: openai/gpt-5\noutput:\n  winner: [for, judge]\n---\nYou judge {{topic}}.\n"}


class Writer:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    async def complete(self, model, params, messages, schema, tools=None):
        self.seen.append(list(messages))
        return Reply(self.replies.pop(0), model, cost_usd=0.01)


def answer(files, notes="one judge"):
    return json.dumps({"files": [{"path": p, "content": c} for p, c in files.items()], "notes": notes})


def test_describe_writes_validates_and_repairs(tmp_path):
    broken = {**TEAM, "for.md": TEAM["for.md"].replace("model:", "modle:")}
    writer = Writer(answer(broken), answer(TEAM))
    result = describe(tmp_path / "debate", "for argues, a judge decides", "openai/gpt-5", writer)
    assert result.ok and result.rounds == 2 and result.cost_usd == 0.02 and result.notes == "one judge"
    assert writer.seen[0][0]["content"].startswith("# Writing a TeamRunner team")
    assert "`debate`" in writer.seen[0][1]["content"] and "for argues" in writer.seen[0][1]["content"]
    told = writer.seen[1][-1]["content"]
    assert told.startswith("That did not work:\nvalidate:") and "modle" in told
    assert (tmp_path / "debate" / ".gitignore").read_text(encoding="utf-8").endswith("runs/\n")
    assert (tmp_path / "debate" / "for.md").read_text(encoding="utf-8").startswith("---\nid: ")  # ids once it validates


def test_describe_refuses_paths_outside_the_team_and_never_writes_over(tmp_path):
    writer = Writer(*[answer({**TEAM, "../evil.md": "x"})] * 3)
    result = describe(tmp_path / "debate", "x", "openai/gpt-5", writer)
    assert not result.ok and "`../evil.md` is not a file a team may hold" in result.problems
    assert not (tmp_path / "evil.md").exists() and not (tmp_path / "debate").exists()
    (tmp_path / "taken").mkdir()
    with pytest.raises(NewError, match="already exists"):
        describe(tmp_path / "taken", "x", "openai/gpt-5", Writer())


def test_the_skill_is_the_same_instructions():
    text = skill()
    assert text.startswith("---\nname: teamrunner-authoring\ndescription: Write a TeamRunner team")
    assert text.endswith(AGENTS.read_text(encoding="utf-8"))


def test_teamrunner_skill_installs_for_both_assistants(tmp_path, monkeypatch, capsys):
    from teamrunner.authoring import install_skill, write_agents_md
    from teamrunner.cli import main
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # the home folder on Windows
    assert main(["skill"]) == 0
    claude = tmp_path / ".claude/skills/teamrunner-authoring/SKILL.md"
    codex = tmp_path / ".agents/skills/teamrunner-authoring/SKILL.md"
    assert claude.read_text(encoding="utf-8") == codex.read_text(encoding="utf-8")
    assert claude.read_text(encoding="utf-8").startswith("---\nname: teamrunner-authoring\ndescription: ")
    assert "✓ Claude Code: installed · ~/.claude/skills/teamrunner-authoring/SKILL.md" in capsys.readouterr().out
    assert [what for _, _, what in install_skill(tmp_path)] == ["already up to date"] * 2

    claude.write_text(claude.read_text(encoding="utf-8").replace("Writing", "Old writing"), encoding="utf-8")
    assert install_skill(tmp_path)[0][2] == "updated"

    mine = tmp_path / ".agents/skills/teamrunner-authoring/SKILL.md"
    mine.write_text("---\nname: teamrunner-authoring\ndescription: my own\n---\n", encoding="utf-8")
    assert install_skill(tmp_path)[1][2].startswith("left alone:")
    assert mine.read_text(encoding="utf-8").endswith("my own\n---\n")  # never written over

    project = tmp_path / "project"
    project.mkdir()
    path, what = write_agents_md(project)
    assert what == "installed" and path.read_text(encoding="utf-8").endswith(AGENTS.read_text(encoding="utf-8"))
    (project / "AGENTS.md").write_text("# The project's own notes\n", encoding="utf-8")
    assert main(["skill", "--agents-md", str(project)]) == 1
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == "# The project's own notes\n"
