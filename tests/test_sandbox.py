# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""run_code in the real sandbox (D114). Pyodide is fetched once into
.cache/deno (git-ignored) and kept, so later runs need no network."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from teamrunner.builtin_tools import Context, run_code, sandbox_place
from teamrunner.desk import Desk, TurnCard
from teamrunner.build import load_team
from teamrunner.events import Log

from test_run import home  # noqa: F401
from test_tools import go, asks, calls
from test_turns import NAMED, fits
from test_validate import debate

CACHE = Path(__file__).resolve().parents[1] / ".cache" / "deno"


@pytest.fixture(autouse=True)
def cache(monkeypatch):
    CACHE.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TEAMRUNNER_DENO_DIR", str(CACHE))


def ran(code, tmp_path, **guardrails):
    ctx = Context(tmp_path / "inputs", tmp_path / "work", {}, tmp_path)
    return asyncio.run(run_code({"code": code}, guardrails, ctx)).text


def test_python_runs_and_files_stay_in_the_work_folder(tmp_path):
    out = ran("import numpy as np\nprint(np.arange(5).sum())\nopen('sums.txt', 'w').write('10')", tmp_path)
    assert out == "Output:\n10\n\nFiles in the work folder: sums.txt"
    assert (tmp_path / "work" / "sums.txt").read_text(encoding="utf-8") == "10"
    assert ran("print(open('sums.txt').read())", tmp_path).startswith("Output:\n10")  # kept for the run


def test_errors_show_the_codes_own_lines(tmp_path):
    out = ran("x = 1\nprint(x / 0)", tmp_path)
    assert out == ('Error:\nTraceback (most recent call last):\n  File "<exec>", line 2, in <module>\n'
                   'ZeroDivisionError: division by zero')


def test_listed_folders_are_read_and_never_written(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "rates.csv").write_text("year,rate\n2025,4.1\n", encoding="utf-8")
    out = ran("print(open('/package/data/rates.csv').read().splitlines()[1])\n"
              "open('/package/data/rates.csv', 'w').write('changed')", tmp_path, paths=["data"])
    assert out.startswith("Output:\n2025,4.1")
    assert (data / "rates.csv").read_text(encoding="utf-8") == "year,rate\n2025,4.1\n"


def test_a_folder_appears_at_the_same_place_on_every_machine(tmp_path):
    package, elsewhere = tmp_path / "package", tmp_path / "elsewhere" / "survey"
    (package / "data" / "2025").mkdir(parents=True)
    elsewhere.mkdir(parents=True)
    (elsewhere / "answers.csv").write_text("a\n", encoding="utf-8")
    assert sandbox_place(package / "data" / "2025", package) == "/package/data/2025"
    assert sandbox_place(elsewhere, package) == "/outside/survey"
    ctx = Context(tmp_path / "inputs", tmp_path / "work", {}, package)
    out = asyncio.run(run_code({"code": "import os\nprint(os.listdir('/outside/survey'))"},
                               {"paths": [str(elsewhere)]}, ctx)).text
    assert out == "Output:\n['answers.csv']"


@pytest.mark.parametrize("code, refused", [
    ("import js\nprint(js.Deno.readTextFileSync('/etc/hosts'))", "Requires read access"),
    ("import js\njs.Deno.writeTextFileSync('/tmp/teamrunner-escape.txt', 'x')", "Requires write access"),
    # a program by its full path: Deno looks a bare name up first, and on Windows finds none
    (f"import js\njs.Deno.Command.new({Path(sys.executable).as_posix()!r}).outputSync()", "Requires run access"),
    ("from pyodide.http import pyfetch\nawait pyfetch('https://example.com')", "Requires net access"),
])
def test_the_walls(tmp_path, code, refused):
    assert refused in ran(code, tmp_path)


def test_keys_are_not_visible(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-seen")
    out = ran("import js\nprint(js.Deno.env.get('OPENAI_API_KEY'))", tmp_path)
    assert "sk-should-not-be-seen" not in out and "Requires env access" in out


SANDBOX = """\
---
kind: toolkeeper
tools:
  run_code: {timeout: 30}
access:
  judge: standard
---
I run Python to check the numbers in a claim.
"""


def test_a_judge_checks_a_sum(tmp_path):
    team = debate(tmp_path, **{**NAMED, "sandbox.md": SANDBOX, "chair.md": "for -> against -> judge\n"})
    outcome, model, events, printed = go(team, {"judge": [
        asks("sandbox", code="print(sum([12, 30, 58]))"),
        fits("Pro's total is right.", winner="for", reasoning="100 checks out")]})
    assert outcome.status == "completed", outcome.reason
    [call] = calls(events)
    assert call["result"] == "Output:\n100" and call["service"].startswith("pyodide 314.0.7")
    assert "└ sandbox   run “print(sum([12, 30, 58]))”   ✓ 1 of 5" in printed


def test_a_slow_program_is_stopped(tmp_path):
    team, _ = load_team(debate(tmp_path, **{**NAMED, "sandbox.md": SANDBOX.replace("30", "3")}))
    ctx = Context(tmp_path / "inputs", tmp_path / "work", {}, tmp_path)
    desk = Desk(team, Log(), ctx, lambda m: {})
    text, _ = asyncio.run(desk.ask(team.members["sandbox"], "judge", {"code": "while True: pass"},
                                   TurnCard("judge"), mandatory=False))
    assert text == "Refused: it took longer than 3 seconds."


def test_prepare_fetches_once(tmp_path, monkeypatch):
    from teamrunner import sandbox
    monkeypatch.setenv("TEAMRUNNER_DENO_DIR", str(CACHE))
    (CACHE / sandbox.READY).unlink(missing_ok=True)
    assert sandbox.prepare() is None and (CACHE / sandbox.READY).exists()
    assert sandbox.prepare() is None  # the second time it only looks for the marker


def test_deno_gets_no_keys_and_no_update_check(monkeypatch):
    """Deno sees no keys, and does not ask deno.land for a newer version of itself."""
    from teamrunner.sandbox import _plain_env
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    env = _plain_env()
    assert "OPENAI_API_KEY" not in env
    assert env["DENO_NO_UPDATE_CHECK"] == "1"



def test_code_cannot_touch_the_disk_or_the_environment(tmp_path):
    """The code reaches Deno through Pyodide's `js` module, but by then Deno has given
    up reading, writing and the environment: the cached Pyodide cannot be changed."""
    from teamrunner import sandbox
    code = ("import js\n"
            "for name, try_it in [('write', lambda: js.Deno.writeTextFileSync(js.Deno.cwd() + '/x', 'x')),\n"
            "                     ('read', lambda: js.Deno.readDirSync('/')),\n"
            "                     ('env', lambda: js.Deno.env.toObject())]:\n"
            "    try:\n"
            "        try_it(); print(name, 'allowed')\n"
            "    except Exception:\n"
            "        print(name, 'refused')\n")
    ran = asyncio.run(sandbox.run(code, tmp_path / "work", [], False))
    assert ran.stdout.split() == ["write", "refused", "read", "refused", "env", "refused"], ran
