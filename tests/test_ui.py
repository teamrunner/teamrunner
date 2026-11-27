# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The shipped local server, accessed over HTTP rather than mocked routes."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from teamrunner.cli import main
from teamrunner.new import EXAMPLES, examples
from teamrunner.ui.server import Server, serve


@contextmanager
def running(root):
    with Server(root) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join(timeout=5)


def get(server, path, *, token=True, headers=None):
    supplied = {"X-TeamRunner-Token": server.token} if token else {}
    supplied.update(headers or {})
    request = Request(server.origin + path, headers=supplied)
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


@pytest.mark.parametrize("example", examples())
def test_browser_lists_every_shipped_example_without_changing_it(example):
    root = EXAMPLES / example
    before = (root / "team.yaml").read_bytes()
    with running(root) as server:
        status, _, body = get(server, "/api/info")
        assert status == 200
        shown = json.loads(body)
        shown.pop("feedback")
        assert shown == {"name": example, "folder": str(root.resolve()), "team": True,
                         "examples": examples(), "workspace": False, "experiments": False, "teams": []}
        status, _, body = get(server, "/api/files")
        assert status == 200
        entries = json.loads(body)["entries"]
        assert "team.yaml" in [entry["name"] for entry in entries]
        assert any(entry["name"].endswith(".md") for entry in entries)
        assert not any(entry["name"].startswith(".") for entry in entries)
    assert (root / "team.yaml").read_bytes() == before


def test_page_and_assets_require_token_and_ship_offline(tmp_path):
    with running(tmp_path) as server:
        assert server.server_address[0] == "127.0.0.1"
        assert len(server.token) >= 32
        for route in ["/", "/app.js", "/app.css", "/vendor/editor.js", "/vendor/editor.css", "/legal.txt", "/license.txt", "/api/info", "/api/files"]:
            assert get(server, route, token=False)[0] == 403
            assert get(server, route, headers={"X-TeamRunner-Token": "wrong"})[0] == 403
            assert get(server, route)[0] == 200
        status, headers, body = get(server, "/?" + urlencode({"token": server.token}), token=False)
        assert status == 200
        assert b"__TOKEN__" not in body
        from teamrunner import __version__
        assert f"Version {__version__}</p>".encode() in body and b"__VERSION__" not in body  # About & notices
        assert b"https://" not in body
        assert b"Serenity Partners LLC" in get(server, "/legal.txt")[2]
        assert b"cost overruns" in get(server, "/legal.txt")[2].lower()
        assert b"GNU GENERAL PUBLIC LICENSE" in get(server, "/license.txt")[2]
        assert headers["Referrer-Policy"] == "no-referrer"
        assert headers["Cache-Control"] == "no-store"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert get(server, "/api/info", headers={"Origin": "https://untrusted.example"})[0] == 403
        assert get(server, "/api/info", headers={"Host": "untrusted.example"})[0] == 403
        assert get(server, "/api/info", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
        # The first visit comes from the file:// opener page, which browsers call cross-site.
        opening = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        assert get(server, "/?" + urlencode({"token": server.token}), token=False, headers=opening)[0] == 200
        assert get(server, "/", token=False, headers=opening)[0] == 403
        assert get(server, "/api/info", headers=opening)[0] == 403
        assert get(server, "/static/../server.py")[0] == 404


def test_paths_and_secrets_are_not_exposed(tmp_path):
    root = tmp_path / "team"
    root.mkdir()
    (root / "runs" / "old").mkdir(parents=True)
    (root / ".env").write_text("SECRET=value")
    (root / ".git").mkdir()
    (root / "team.yaml").write_text("name: team")
    with running(root) as server:
        for path in ["..", "../outside", str(tmp_path), ".env", ".git", "runs/../../", "\x00"]:
            status, _, body = get(server, "/api/files?" + urlencode({"path": path}))
            assert status in (400, 403)
            assert b"SECRET" not in body
        _, _, body = get(server, "/api/files")
        assert [e["name"] for e in json.loads(body)["entries"]] == ["runs", "team.yaml"]
        assert '"old"' not in body.decode()  # no recursive scanning of run archives
        _, _, body = get(server, "/api/files?path=runs")
        assert json.loads(body)["entries"][0]["name"] == "old"
        assert get(server, "/api/files?path=missing")[0] == 404
        assert get(server, "/api/files?path=team.yaml")[0] == 404


def test_symlinks_cannot_escape_or_reveal_hidden_files(tmp_path):
    root = tmp_path / "team"
    root.mkdir()
    (root / ".env").write_text("SECRET=value")
    try:
        (root / "outside").symlink_to(tmp_path, target_is_directory=True)
        (root / "keys").symlink_to(root / ".env")
        (root / "loop").symlink_to(root / "loop")
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this system")
    with running(root) as server:
        assert json.loads(get(server, "/api/files")[2])["entries"] == []
        assert get(server, "/api/files?path=outside")[0] == 403
        assert get(server, "/api/files?path=keys")[0] == 403
        assert get(server, "/api/files?path=loop")[0] in (400, 404)


@pytest.mark.parametrize("command", ["ui", "gui"])
def test_cli_command_and_alias(command, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("teamrunner.ui.server.serve", lambda folder: opened.append(folder) or 0)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TEAMRUNNER_WORKSPACE", raising=False)
    assert main([command]) == 0  # no one to ask and nothing remembered: this folder, as before
    assert opened == [Path(".")]
    assert not (tmp_path / "home/.env").exists()
    assert main([command, str(tmp_path)]) == 0
    assert opened[-1] == tmp_path


def test_plain_ui_opens_the_remembered_teams_folder_from_anywhere(tmp_path, monkeypatch, capsys):
    from teamrunner import workspace
    from teamrunner.keys import extra_settings, read_env, save_key
    opened = []
    monkeypatch.setattr("teamrunner.ui.server.serve", lambda folder: opened.append(folder) or 0)
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TEAMRUNNER_WORKSPACE", raising=False)
    teams, other, elsewhere = tmp_path / "teams", tmp_path / "other", tmp_path / "elsewhere"
    for folder in (teams, other, elsewhere):
        folder.mkdir()
    monkeypatch.chdir(elsewhere)
    keyfile = tmp_path / "home/.env"
    save_key(keyfile, "OPENAI_API_KEY", "sk-kept")

    assert main(["ui", str(teams), "--remember"]) == 0
    assert opened[-1] == teams and read_env(keyfile) == {"OPENAI_API_KEY": "sk-kept",
                                                          "TEAMRUNNER_WORKSPACE": str(teams.resolve())}
    assert "TEAMRUNNER_WORKSPACE" not in extra_settings()  # never handed to a model provider
    if os.name != "nt":
        assert keyfile.stat().st_mode & 0o777 == 0o600
    assert main(["ui"]) == 0 and opened[-1] == teams.resolve()
    assert main(["ui", str(other)]) == 0 and opened[-1] == other  # once: nothing is remembered
    assert main(["ui"]) == 0 and opened[-1] == teams.resolve()
    assert main(["ui", str(other), "--remember"]) == 0  # the line is replaced, not repeated
    assert keyfile.read_text().count("TEAMRUNNER_WORKSPACE") == 1 and workspace.remembered()[0] == other.resolve()

    monkeypatch.setenv("TEAMRUNNER_WORKSPACE", str(teams))  # the shell wins, as with keys
    assert main(["ui"]) == 0 and opened[-1] == teams
    monkeypatch.setenv("TEAMRUNNER_WORKSPACE", str(tmp_path / "gone"))
    count = len(opened)
    assert main(["ui"]) == 1 and len(opened) == count
    assert "could not be found" in capsys.readouterr().err
    assert main(["ui", "--remember"]) == 1 and main(["ui", str(tmp_path / "gone"), "--remember"]) == 1


def test_the_first_plain_ui_asks_once_where_the_workspace_is(tmp_path, monkeypatch, capsys):
    from teamrunner import workspace
    opened, asked = [], []
    monkeypatch.setattr("teamrunner.ui.server.serve", lambda folder: opened.append(folder) or 0)
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TEAMRUNNER_WORKSPACE", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "me")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "")
    assert main(["ui"]) == 0
    assert asked == ["Where should your teamrunner workspace be? [~/teamrunner] "]
    assert opened == [tmp_path / "me/teamrunner"] and opened[0].is_dir()
    assert sorted(p.name for p in opened[0].iterdir()) == ["experiments", "teams"]  # D144
    assert workspace.remembered()[0] == (tmp_path / "me/teamrunner").resolve()
    assert "opens it from anywhere" in capsys.readouterr().out
    assert main(["ui"]) == 0 and len(asked) == 1  # never asked again

    (tmp_path / "home/.env").unlink()
    chosen = tmp_path / "my work/teams"
    monkeypatch.setattr("builtins.input", lambda prompt: f"  {chosen}  ")
    assert main(["ui"]) == 0 and opened[-1] == chosen and chosen.is_dir()
    assert workspace.remembered()[0] == chosen.resolve()


def test_launch_prints_url_opens_browser_and_closes_on_interrupt(tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr("teamrunner.ui.server.webbrowser.open", lambda url: opened.append(url) or True)
    seen = []

    def interrupt(server):
        opener = Path(opened[0].removeprefix("file://")) if os.name != "nt" else None
        if opener is not None:
            seen.append((opener.read_text(encoding="utf-8"), opener.stat().st_mode & 0o777, server.url))
        raise KeyboardInterrupt

    monkeypatch.setattr(Server, "serve_forever", interrupt)
    assert serve(tmp_path) == 0
    output = capsys.readouterr().out
    # S10 (review 2026-11-16): the browser is opened at a private page, so the token
    # never appears in the command that opens it; the page is gone once the server stops.
    assert len(opened) == 1 and opened[0].startswith("file:") and "token" not in opened[0]
    if seen:
        page, mode, url = seen[0]
        assert mode == 0o600 and url.replace("&", "&amp;") in page and "http-equiv=\"refresh\"" in page
        assert not Path(opened[0].removeprefix("file://")).exists()
    assert "http://127.0.0.1:" in output and "?token=" in output
    assert "Press Ctrl+C to stop" in output
    assert "local page stopped" in output


def test_missing_root_has_a_friendly_cli_error(tmp_path, capsys):
    assert main(["ui", str(tmp_path / "missing")]) == 1
    assert "Could not open" in capsys.readouterr().err


def test_empty_folder_is_valid(tmp_path):
    with running(tmp_path) as server:
        assert json.loads(get(server, "/api/files")[2])["entries"] == []
        assert json.loads(get(server, "/api/info")[2])["team"] is False


def post(server, route, data, *, token=True, headers=None):
    supplied = {"Content-Type": "application/json"}
    if token:
        supplied["X-TeamRunner-Token"] = server.token
    supplied.update(headers or {})
    request = Request(server.origin + route, data=json.dumps(data).encode(), headers=supplied)
    try:
        response = build_opener(ProxyHandler({})).open(request, timeout=10)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read())


@pytest.fixture
def ui_team(tmp_path):
    from teamrunner.new import copy_from
    root = tmp_path / "workspace"
    root.mkdir()
    copy_from(EXAMPLES / "round_robin", root / "debate")
    # UI key/error tests use an explicit keyed provider, independently of the
    # shipped starter's free hosted model. No live provider calls are made.
    team_file = root / "debate/team.yaml"
    team_file.write_text(team_file.read_text().replace("teamrunner/demoLLM", "openrouter/google/gemma-4-31b-it"))
    return root


def test_edit_conflict_validate_and_resolved_member(ui_team):
    from teamrunner.validate import validate
    with running(ui_team) as server:
        path = "debate/team.yaml"
        original = json.loads(get(server, "/api/file?path=" + path)[2])
        assert not original["readonly"]
        broken = original["text"] + "\nunknown_word: true\n"
        status, saved = post(server, "/api/save", {**original, "text": broken})
        assert status == 200 and saved["revision"] != original["revision"]
        status, report = post(server, "/api/validate", {"path": path, "offline": True})
        assert status == 200 and not report["ok"]
        cli_report = validate(ui_team / "debate")
        assert [p["message"] for p in report["problems"]] == [p.message for p in cli_report.problems]
        problem = next(p for p in report["problems"] if p["value"] == "unknown_word")
        assert problem["file"] == path and problem["line"] and problem["fix"]
        assert post(server, "/api/save", original)[0] == 409
        assert (ui_team / path).read_bytes().decode("utf-8") == broken  # as saved: Windows checkouts have \r\n
        assert post(server, "/api/save", {**saved, "text": original["text"]})[0] == 200
        (ui_team / path).write_text(original["text"] + "# edited outside the browser\n", encoding="utf-8")
        latest = json.loads(get(server, "/api/file?path=" + path)[2])
        assert latest["revision"] != original["revision"]
        assert post(server, "/api/save", original)[0] == 409
        status, report = post(server, "/api/validate", {"path": path, "offline": True})
        assert report["ok"] and "decider" in report["roles"].values()
        member = next(p for p in report["roles"] if p.endswith(".md"))
        status, _, body = get(server, "/api/show?" + urlencode({"path": member}))
        assert status == 200 and "model:" in json.loads(body)["text"]
        assert "Who acts when" in json.loads(get(server, "/api/explain?path=debate")[2])["text"]


def test_every_write_requires_header_token_and_stays_inside_root(ui_team, tmp_path):
    with running(ui_team) as server:
        original = json.loads(get(server, "/api/file?path=debate/team.yaml")[2])
        for route in ["/api/save", "/api/member", "/api/chair", "/api/rename", "/api/trash", "/api/new", "/api/key", "/api/validate"]:
            assert post(server, route, original, token=False)[0] == 403
            assert post(server, route + "?token=" + server.token, original, token=False)[0] == 403
            assert post(server, route, original, headers={"Origin": "https://bad.example"})[0] == 403
        for path in ["../outside.md", str(tmp_path / "outside.md"), ".env", "debate/runs/new.md"]:
            assert post(server, "/api/member", {"path": path})[0] == 403
        for path in ["..", str(tmp_path), "debate/runs"]:
            assert post(server, "/api/chair", {"path": path})[0] == 403
        runs = ui_team / "debate/runs"
        runs.mkdir()
        (runs / "output.md").write_text("keep this result")
        record = json.loads(get(server, "/api/file?path=debate/runs/output.md")[2])
        assert record["readonly"]
        assert post(server, "/api/save", {**record, "text": "changed"})[0] == 403
        assert post(server, "/api/rename", {**record, "destination": "debate/output.md"})[0] == 403
        assert post(server, "/api/trash", record)[0] == 403
        assert post(server, "/api/new", {"path": "debate/runs/fake"})[0] == 403
        assert (runs / "output.md").read_text() == "keep this result"


def test_member_rename_trash_and_new_team_use_existing_functions(ui_team, monkeypatch):
    trashed = []
    monkeypatch.setattr("teamrunner.ui.trash.move_to_trash", lambda path: trashed.append(path))
    with running(ui_team) as server:
        status, file = post(server, "/api/member", {"path": "debate/researcher.md"})
        assert status == 200
        assert post(server, "/api/member", {"path": "debate/researcher.md"})[0] == 409
        assert post(server, "/api/member", {"path": "debate/manager.md"})[0] == 400
        assert post(server, "/api/new", {"path": "bare"})[0] == 200
        status, refused = post(server, "/api/member", {"path": "bare/chair.md"})
        assert status == 400 and "+ Chair" in refused["error"] and not (ui_team / "bare/chair.md").exists()
        status, chair = post(server, "/api/chair", {"path": "bare"})
        assert status == 200 and chair["path"] == "bare/chair.md" and "->" in chair["text"]
        assert all(line.startswith("#") for line in chair["text"].split("\n") if line)
        assert post(server, "/api/chair", {"path": "bare"})[0] == 409
        assert post(server, "/api/chair", {"path": "bare/models"})[0] == 400
        status, renamed = post(server, "/api/rename", {**file, "destination": "debate/reviewer.md"})
        assert status == 200 and not (ui_team / file["path"]).exists()
        assert post(server, "/api/trash", renamed)[0] == 200
        assert trashed == [ui_team / "debate/reviewer.md"]
        assert post(server, "/api/new", {"path": "fresh"})[0] == 200
        assert (ui_team / "fresh/team.yaml").exists()
        assert post(server, "/api/new", {"path": "example", "example": "fact_checked"})[0] == 200
        assert (ui_team / "example/chair.md").exists()
        assert post(server, "/api/new", {"path": "example", "example": "fact_checked"})[0] in (400, 409)
        assert post(server, "/api/new", {"path": "untrusted", "example": "../arbitrary"})[0] == 400


def test_new_team_in_a_workspace_goes_in_teams(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TEAMRUNNER_WORKSPACE", raising=False)
    root = tmp_path / "teamrunner"
    for part in ("teams", "experiments"):
        (root / part).mkdir(parents=True)
    with running(root) as server:
        status, made = post(server, "/api/new", {"path": "fresh"})
        assert status == 200 and made["path"] == "teams/fresh/team.yaml"
        assert post(server, "/api/new", {"path": "elsewhere/kept"})[0] == 200  # a path is used as written
        assert (root / "elsewhere/kept/team.yaml").exists() and not (root / "fresh").exists()
    remembered = tmp_path / "older"  # remembered before D144, with no teams/ yet
    remembered.mkdir()
    monkeypatch.setenv("TEAMRUNNER_WORKSPACE", str(remembered))
    with running(remembered) as server:
        assert post(server, "/api/new", {"path": "fresh"})[1]["path"] == "teams/fresh/team.yaml"


def test_new_experiment_is_made_in_experiments_on_a_chosen_team_and_plans(tmp_path, monkeypatch):
    from teamrunner.experiment import read_experiment
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TEAMRUNNER_WORKSPACE", raising=False)
    root = tmp_path / "teamrunner"
    (root / "teams").mkdir(parents=True)
    with running(root) as server:
        assert post(server, "/api/new", {"path": "debate"})[0] == 200
        info = json.loads(get(server, "/api/info")[2])
        assert info["workspace"] and info["teams"] == ["teams/debate"] and not info["experiments"]
        status, made = post(server, "/api/new-experiment", {"name": "first_study", "team": "teams/debate"})
        assert status == 200 and made == {"path": "experiments/first_study/experiment.md",
                                          "folder": "experiments/first_study"}
        text = (root / made["path"]).read_text(encoding="utf-8")
        assert "control: debate" in text and "from: debate" in text  # a team named alone (D144)
        assert post(server, "/api/new-experiment", {"name": "first_study", "team": "teams/debate"})[0] == 409
        assert post(server, "/api/new-experiment", {"name": "no good", "team": "teams/debate"})[0] == 400
        assert post(server, "/api/new-experiment", {"name": "other", "team": "teams/missing"})[0] == 400
        assert post(server, "/api/new-experiment", {"name": "../out", "team": "teams/debate"})[0] == 400
    exp = read_experiment(root / "experiments/first_study")
    assert not exp.problems.errors and [c.name for c in exp.conditions] == ["control", "variant"]
    assert exp.runs == 10


def test_variant_metric_and_task_make_files_whose_lines_plan(tmp_path, monkeypatch):
    from teamrunner.experiment import build, read_experiment
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TEAMRUNNER_WORKSPACE", raising=False)
    root = tmp_path / "teamrunner"
    (root / "teams").mkdir(parents=True)
    with running(root) as server:
        assert post(server, "/api/new", {"path": "debate", "example": "multi_agent_debate"})[0] == 200
        assert post(server, "/api/new-experiment", {"name": "study", "team": "teams/debate"})[0] == 200
        exp = "experiments/study"
        found = json.loads(get(server, "/api/experiment-parts?" + urlencode({"path": exp}))[2])
        assert found["teams"] == ["debate"] and {"file": "judge.md", "key": "judge"}.items() <= next(
            f for f in found["files"] if f["file"] == "judge.md").items()
        assert "judge.output.winner" in found["measurable"] and "judge.output.reasoning" not in found["measurable"]
        assert "topic" in found["inputs"]
        status, prompt = post(server, "/api/variant", {"experiment": exp, "name": "strict", "source": "debate/judge.md#prompt"})
        assert status == 200 and prompt["line"] == "judge.prompt: file:variants/strict.md"
        original = (root / "teams/debate/judge.md").read_text(encoding="utf-8")
        assert original.endswith((root / exp / "variants/strict.md").read_text(encoding="utf-8"))  # the prompt, exactly
        status, whole = post(server, "/api/variant", {"experiment": exp, "name": "whole", "source": "debate/judge.md"})
        assert status == 200 and whole["line"] == "judge: file:variants/whole.md"
        assert (root / exp / "variants/whole.md").read_text(encoding="utf-8") == original
        assert post(server, "/api/variant", {"experiment": exp, "name": "x", "source": "debate/../../secret.md"})[0] == 400
        assert post(server, "/api/variant", {"experiment": exp, "name": "strict", "source": "debate/judge.md"})[0] == 409
        lines = {}
        for kind in ("answer_key", "evaluator", "output:judge.output.winner"):
            status, made = post(server, "/api/metric", {"experiment": exp, "name": kind.split(":")[0], "kind": kind})
            assert status == 200, made
            lines[kind] = made["line"].strip()
        assert lines["output:judge.output.winner"] == "- judge.output.winner"
        assert post(server, "/api/metric", {"experiment": exp, "name": "t", "kind": "output:judge.output.reasoning"})[0] == 400
        status, task = post(server, "/api/task", {"experiment": exp, "name": "case"})
        assert status == 200 and task["line"] == "task: tasks/case"
    # Pasting every line the page showed gives an experiment that plans.
    path = root / exp / "experiment.md"
    text = path.read_text(encoding="utf-8").replace("    change: {}\n", "    change:\n      judge.prompt: file:variants/strict.md\n", 1)
    text = text.replace("# metrics:\n#   - judge.output.winner\n", "metrics:\n" + "".join(f"  {l}\n" for l in lines.values()), 1)
    text = text.replace("# task: tasks/my_task\n", "task: tasks/case\n", 1)
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("TEAMRUNNER_WORKSPACE", str(root))
    built = build(read_experiment(root / exp))
    assert not built.problems.errors, [p.message for p in built.problems.errors]


def test_a_team_goes_to_the_trash_as_a_whole_folder_and_nothing_else_does(ui_team, monkeypatch):
    trashed = []
    monkeypatch.setattr("teamrunner.ui.trash.move_to_trash", lambda path: trashed.append(path))
    (ui_team / "debate/runs/2026-01-01_000000_abcd").mkdir(parents=True)
    (ui_team / "notes").mkdir()
    with running(ui_team) as server:
        status, _, body = get(server, "/api/team-contents?path=debate")
        holds = json.loads(body)
        assert status == 200 and holds["name"] == "debate" and holds["runs"] == 0
        assert holds["files"] == sum(1 for p in (ui_team / "debate").rglob("*")
                                     if p.is_file() and "runs" not in p.parts)
        for path in ["", ".", "notes", "debate/runs", "debate/runs/2026-01-01_000000_abcd",
                     "debate/team.yaml", "../workspace", "missing"]:
            assert post(server, "/api/trash-team", {"path": path})[0] in (400, 403, 404), path
        assert post(server, "/api/trash-team", {"path": "debate"}, token=False)[0] == 403
        assert trashed == []
        assert post(server, "/api/trash-team", {"path": "debate"})[0] == 200
        assert trashed == [ui_team / "debate"]


def test_the_opened_team_itself_is_not_trashed_from_the_page(ui_team, monkeypatch):
    trashed = []
    monkeypatch.setattr("teamrunner.ui.trash.move_to_trash", lambda path: trashed.append(path))
    with running(ui_team / "debate") as server:
        assert post(server, "/api/trash-team", {"path": ""})[0] == 400
        assert post(server, "/api/trash-team", {"path": "."})[0] == 400
    assert trashed == []


def test_feedback_names_issues_and_email_with_the_version_filled_in(tmp_path):
    import teamrunner
    with running(tmp_path) as server:
        to = json.loads(get(server, "/api/info")[2])["feedback"]
    assert to["version"] == f"teamrunner {teamrunner.__version__}"
    assert to["bug"].startswith("https://github.com/teamrunner/teamrunner/issues/new?template=bug_report.yml")
    assert "version=teamrunner%20" in to["bug"] and "system=" in to["bug"]
    assert to["idea"].startswith("https://github.com/teamrunner/teamrunner/issues/new?template=question.yml")
    assert to["email"] == "hello@teamrunner.ai" and to["mail"].startswith("mailto:hello@teamrunner.ai?")
    assert str(tmp_path) not in json.dumps(to)  # nothing about the person's folders


def test_keys_saved_at_existing_location_never_return_values(ui_team, tmp_path, monkeypatch):
    from teamrunner.keys import key_file, read_env
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "keys"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    secret = "test-secret-do-not-echo"
    with running(ui_team) as server:
        status, _, body = get(server, "/api/keys?path=debate")
        assert status == 200 and {"name": "OPENROUTER_API_KEY", "present": False} in json.loads(body)["keys"]
        status, result = post(server, "/api/key", {"name": "OPENROUTER_API_KEY", "value": secret})
        assert status == 200 and secret not in json.dumps(result)
        assert read_env(key_file())["OPENROUTER_API_KEY"] == secret
        body = get(server, "/api/keys?path=debate")[2]
        assert secret.encode() not in body
        assert {"name": "OPENROUTER_API_KEY", "present": True} in json.loads(body)["keys"]
        assert post(server, "/api/key", {"name": "OPENROUTER_API_KEY", "value": "x\nINJECT=y"})[0] == 400
        assert post(server, "/api/key", {"name": "UNEXPECTED_KEY", "value": secret})[0] == 400
        assert not (ui_team / ".env").exists()


def test_saving_reuses_a_providers_model_list_and_the_button_asks_again(monkeypatch):
    from teamrunner.ui import api
    asked = []
    def lister(key, settings):
        asked.append(key)
        return {"gpt-5"}
    monkeypatch.setattr(api, "_lists", {})
    assert api.remembering({"openai": lister}, fresh=False)["openai"]("k", {}) == {"gpt-5"}
    assert api.remembering({"openai": lister}, fresh=False)["openai"]("k", {}) == {"gpt-5"}
    assert asked == ["k"]  # the second save answered from memory
    api.remembering({"openai": lister}, fresh=False)["openai"]("other", {})
    api.remembering({"openai": lister}, fresh=True)["openai"]("k", {})
    assert asked == ["k", "other", "k"]  # another key, and the Validate button, ask the provider
    def down(key, settings):
        raise OSError("no connection")
    monkeypatch.setattr(api, "_lists", {})
    for _ in range(2):
        try:
            api.remembering({"openai": down}, fresh=False)["openai"]("k", {})
        except OSError:
            asked.append("failed")
    assert asked[-2:] == ["failed", "failed"]  # a failure is never remembered
    local = api.remembering({"ollama": lister}, fresh=False)["ollama"]
    local(None, {}), local(None, {})
    assert asked[-2:] == [None, None]  # Ollama is on this computer and is always asked


def test_validate_checks_keys_and_models_with_the_shared_engine(ui_team, monkeypatch):
    from teamrunner.checks import Check
    calls = []
    def check(report, listers=None):
        calls.append(report.package.team.folder)
        return Check(), None
    monkeypatch.setattr("teamrunner.checks.online_check", check)
    with running(ui_team) as server:
        assert post(server, "/api/validate", {"path": "debate", "offline": True})[1]["ok"]
        assert not calls  # a files-only check asks no one
        status, result = post(server, "/api/validate", {"path": "debate"})
        assert status == 200 and result["ok"] and result["online"]["event"] == "check"
        assert calls == [ui_team / "debate"]


def test_validation_cannot_follow_external_member_or_parent_package(ui_team, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("---\n---\noutside")
    try:
        (ui_team / "debate/external.md").symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with running(ui_team) as server:
        assert post(server, "/api/validate", {"path": "debate", "offline": True})[0] == 403
        assert get(server, "/api/file?path=debate/external.md")[0] == 403
    (ui_team / "team.yaml").write_text("name: parent")
    with running(ui_team / "debate") as server:
        assert post(server, "/api/validate", {"path": "", "offline": True})[0] == 403


def test_linux_trash_is_recoverable_and_preserves_original_path(tmp_path, monkeypatch):
    from teamrunner.ui.trash import move_to_trash
    import sys
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "desktop"))
    source = tmp_path / "member.md"
    source.write_text("recoverable")
    move_to_trash(source)
    trash = tmp_path / "desktop/Trash"
    assert not source.exists()
    assert next((trash / "files").iterdir()).read_text() == "recoverable"
    assert "member.md" in next((trash / "info").iterdir()).read_text()


def test_provider_errors_never_echo_key_values(ui_team):
    from teamrunner.build import load_team
    from teamrunner.checks import run_check
    from teamrunner.keys import Keys
    secret = "provider-secret-do-not-echo"
    def unavailable(key, settings):
        raise RuntimeError(f"request failed: https://provider.example?key={key}")
    team, _ = load_team(ui_team / "debate")
    result = run_check(team, Keys(values={"OPENROUTER_API_KEY": secret}),
                       {"openrouter": unavailable}, {}).event()
    assert result["problems"]
    assert secret not in json.dumps(result)
    assert "[key hidden]" in json.dumps(result)


def test_online_and_flow_refuse_external_key_and_run_links(ui_team, tmp_path):
    env = tmp_path / "private.env"
    env.write_text("OPENROUTER_API_KEY=outside")
    records = tmp_path / "records"
    records.mkdir()
    try:
        (ui_team / "debate/.env").symlink_to(env)
        (ui_team / "debate/runs").symlink_to(records, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with running(ui_team) as server:
        assert post(server, "/api/validate", {"path": "debate"})[0] == 403
        assert get(server, "/api/explain?path=debate")[0] == 403


def test_validation_assigns_ids_like_the_cli_and_updates_file_revision(ui_team):
    with running(ui_team) as server:
        status, file = post(server, "/api/member", {"path": "debate/new_member.md"})
        assert status == 200 and "id:" not in file["text"]
        status, report = post(server, "/api/validate", {"path": "debate", "offline": True})
        assert status == 200 and report["ok"]
        assert "debate/new_member.md" in report["ids_added"]
        updated = json.loads(get(server, "/api/file?path=debate/new_member.md")[2])
        assert "id:" in updated["text"] and updated["revision"] != file["revision"]
        assert post(server, "/api/save", file)[0] == 409


def test_opening_runs_as_root_does_not_allow_writes(tmp_path):
    root = tmp_path / "runs" / "old"
    root.mkdir(parents=True)
    (root / "output.md").write_text("record")
    (root / "run.json").write_text("{}")
    (root / "team.yaml").write_text("name: snapshot\n")
    with running(root) as server:
        file = json.loads(get(server, "/api/file?path=output.md")[2])
        assert file["readonly"]
        assert post(server, "/api/save", {**file, "text": "altered"})[0] == 403
        assert post(server, "/api/validate", {"path": "", "offline": True})[0] == 403
        assert post(server, "/api/new", {"path": "fresh"})[0] == 403
        assert "id:" not in (root / "team.yaml").read_text()


@pytest.fixture
def recorded_runs(tmp_path):
    import shutil
    root = tmp_path / "history"
    shutil.copytree(Path(__file__).parent / "fixtures/ui_history", root)
    return root


def test_run_list_matches_cli_filters_search_and_pagination(recorded_runs):
    from teamrunner.runs import all_runs
    records = all_runs(recorded_runs / "runs")
    with running(recorded_runs) as server:
        status, _, body = get(server, "/api/runs")
        result = json.loads(body)
        assert status == 200 and result["total"] == 3
        assert [r["path"].split("/")[-1] for r in result["runs"]] == [r.folder.name for r in records]
        assert result["runs"][0]["status"] == "failed"
        assert [r["message"] for r in result["runs"]] == [r.input for r in records]
        assert [r["result"] for r in result["runs"]] == [r.result for r in records]
        assert json.loads(get(server, "/api/runs?team=debate&status=completed")[2])["total"] == 2
        assert json.loads(get(server, "/api/runs?search=REMOTE")[2])["total"] == 1
        assert json.loads(get(server, "/api/runs?search=hybrid%20work")[2])["total"] == 3
        assert not json.loads(get(server, "/api/runs?search=not-present")[2])["runs"]
        page = json.loads(get(server, "/api/runs?limit=1&offset=1")[2])
        assert len(page["runs"]) == 1 and page["runs"][0]["path"].endswith(records[1].folder.name)


def test_run_details_keep_events_members_versions_and_artifacts(recorded_runs):
    from teamrunner.runs import all_runs
    records = all_runs(recorded_runs / "runs")
    with running(recorded_runs) as server:
        def detail(record):
            status, _, body = get(server, "/api/run?" + urlencode({"path": record.folder.relative_to(recorded_runs).as_posix()}))
            assert status == 200
            return json.loads(body)
        oldest, middle, newest = [detail(r) for r in reversed(records)]
        assert oldest["members"][0]["version"]["number"] == 1
        assert middle["members"][0]["version"]["number"] == 2
        assert newest["members"][0]["name"] == "reviewer"
        assert newest["members"][0]["version"]["number"] == 2  # identity survives rename
        assert newest["members"][0]["version"]["first_run_shown"] == "6 Nov"
        assert "hybrid work" in middle["output"]
        assert {e["event"] for e in middle["events"]} >= {"agent_message", "tool_call", "retry", "route_decision", "warning"}
        assert next(e for e in middle["events"] if e.get("from") == "judge" and e["event"] == "agent_message")["read"] == [3, 4]
        assert {a["path"] for a in middle["artifacts"]} == {"inputs/brief.txt", "work/scores.csv"}
        assert not middle["has_spec"]
        assert middle["raw_files"] == ["run.json", "events.jsonl", "output.md"]
        # Filtering the list never changes the member version derived from all history.
        get(server, "/api/runs?status=failed")
        assert detail(records[0])["members"][0]["version"]["number"] == 2


def test_corrupt_and_missing_records_are_reported_without_hiding_other_runs(recorded_runs):
    runs = recorded_runs / "runs"
    broken = runs / "2026-11-07_120000_broken"
    broken.mkdir()
    (broken / "run.json").write_text('{"unfinished":')
    newest = runs / "2026-11-07_101500_review"
    (newest / "events.jsonl").write_text((newest / "events.jsonl").read_text() + '{"partial":\n')
    (newest / "output.md").unlink()
    with running(recorded_runs) as server:
        listing = json.loads(get(server, "/api/runs")[2])
        assert listing["total"] == 3 and any("skipped" in text for text in listing["warnings"])
        detail = json.loads(get(server, "/api/run?path=runs/" + newest.name)[2])
        assert len(detail["events"]) == 9
        assert any("unfinished event" in text for text in detail["warnings"])
        assert any("no output.md" in text for text in detail["warnings"])
        assert detail["output"] == "" and "output.md" not in detail["raw_files"]
        assert get(server, "/api/run?path=runs/" + broken.name)[0] == 404


def test_run_raw_downloads_and_folder_action_require_token_and_stay_inside_run(recorded_runs, tmp_path, monkeypatch):
    from teamrunner.ui import runviews
    import sys
    folder = recorded_runs / "runs/2026-11-06_100000_debate"
    run = folder.relative_to(recorded_runs).as_posix()
    calls = []
    if sys.platform == "win32":
        monkeypatch.setattr(runviews.os, "startfile", lambda path: calls.append(path))
    else:
        monkeypatch.setattr(runviews.subprocess, "run", lambda args, **kwargs: calls.append(args))
    with running(recorded_runs) as server:
        for name in ["run.json", "events.jsonl", "output.md", "inputs/brief.txt", "work/scores.csv"]:
            route = "/api/run-file?" + urlencode({"run": run, "file": name})
            assert get(server, route, token=False)[0] == 403
            status, headers, body = get(server, route)
            assert status == 200 and body == (folder / name).read_bytes()
            assert headers["Content-Type"] == "application/octet-stream"
            assert headers["Content-Disposition"].startswith("attachment;")
            assert "sandbox" in headers["Content-Security-Policy"]
        for filename in ["../../team.yaml", str(tmp_path / "outside"), ".env"]:
            assert get(server, "/api/run-file?" + urlencode({"run": run, "file": filename}))[0] == 403
        assert get(server, "/api/run?path=../outside")[0] == 403
        assert post(server, "/api/open-run-folder", {"path":run}, token=False)[0] == 403
        assert not calls
        assert post(server, "/api/open-run-folder", {"path":run})[0] == 200
        assert calls and str(folder) in calls[0]


def test_external_run_links_are_skipped_and_artifact_links_cannot_escape(recorded_runs, tmp_path):
    folder = recorded_runs / "runs/2026-11-06_100000_debate"
    outside = tmp_path / "private.txt"
    outside.write_text("private marker")
    try:
        (folder / "work/private.txt").symlink_to(outside)
        (folder / "work/package.txt").symlink_to(recorded_runs / "team.yaml")
        (recorded_runs / "runs/external").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable")
    run = folder.relative_to(recorded_runs).as_posix()
    with running(recorded_runs) as server:
        listing = json.loads(get(server, "/api/runs")[2])
        assert listing["total"] == 3 and listing["warnings"]
        detail = json.loads(get(server, "/api/run?" + urlencode({"path":run}))[2])
        assert len(detail["artifacts"]) == 2 and len(detail["warnings"]) >= 2
        for file in ["work/private.txt", "work/package.txt"]:
            assert get(server, "/api/run-file?" + urlencode({"run":run,"file":file}))[0] == 403
        (folder / "output.md").unlink()
        (folder / "output.md").symlink_to(outside)
        assert json.loads(get(server, "/api/runs")[2])["total"] == 2
        assert get(server, "/api/run?" + urlencode({"path":run}))[0] == 403


def test_missing_event_log_and_fingerprints_are_explicit(recorded_runs):
    folder = recorded_runs / "runs/2026-11-07_101500_review"
    (folder / "events.jsonl").unlink()
    with running(recorded_runs) as server:
        detail = json.loads(get(server, "/api/run?path=" + folder.relative_to(recorded_runs).as_posix())[2])
        assert not detail["events"] and not detail["members"]
        assert any("no events.jsonl" in text for text in detail["warnings"])
        assert not detail["has_spec"]


def test_runs_can_be_browsed_even_if_current_team_is_broken(recorded_runs):
    (recorded_runs / "team.yaml").write_text("not: [valid YAML\n")
    with running(recorded_runs) as server:
        assert json.loads(get(server, "/api/runs")[2])["total"] == 3
        assert get(server, "/api/run?path=runs/2026-11-05_090000_debate")[0] == 200


def test_browser_reads_a_real_engine_rehearsal(ui_team, tmp_path, monkeypatch):
    from teamrunner.run import Options, run_team
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "isolated-home"))
    outcome = run_team(ui_team / "debate", Options(rehearse=True, quiet=True))
    assert outcome.status == "completed"
    with running(ui_team) as server:
        result = json.loads(get(server, "/api/runs?path=debate")[2])
        assert result["total"] == 1 and result["runs"][0]["rehearsal"]
        path = result["runs"][0]["path"]
        detail = json.loads(get(server, "/api/run?" + urlencode({"path":path}))[2])
        messages = [e for e in detail["events"] if e["event"] == "agent_message"]
        assert len(messages) == 3
        assert detail["summary"]["cost"] == "$0.0000"
        assert "rehearsal" in detail["output"]
        assert all(m["version"]["number"] == 1 for m in detail["members"])
        assert not detail["warnings"]


def test_run_view_handles_invalid_inventory_and_unknown_status(recorded_runs):
    folder = recorded_runs / "runs/2026-11-07_101500_review"
    record = json.loads((folder / "run.json").read_text())
    record["status"] = None
    (folder / "run.json").write_text(json.dumps(record))
    events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
    events[0]["members"] = 4  # valid JSON, but an unusable member inventory
    (folder / "events.jsonl").write_text(''.join(json.dumps(e) + '\n' for e in events))
    with running(recorded_runs) as server:
        result = json.loads(get(server, "/api/runs?status=unknown")[2])
        assert result["total"] == 1 and result["runs"][0]["status"] == "unknown"
        status, _, body = get(server, "/api/run?path=" + folder.relative_to(recorded_runs).as_posix())
        detail = json.loads(body)
        assert status == 200 and not detail["members"]
        assert any("inventory could not be read" in text for text in detail["warnings"])


def test_saved_spec_and_missing_member_fingerprint_are_not_guessed(recorded_runs):
    folder = recorded_runs / "runs/2026-11-07_101500_review"
    (folder / "resolved").mkdir()
    (folder / "resolved/team.yaml").write_text("name: saved_team\n")
    events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
    events[0]["members"][0].pop("version_hash")
    (folder / "events.jsonl").write_text(''.join(json.dumps(e) + '\n' for e in events))
    with running(recorded_runs) as server:
        detail = json.loads(get(server, "/api/run?path=" + folder.relative_to(recorded_runs).as_posix())[2])
        assert detail["has_spec"]
        assert detail["members"][0]["version"] is None
        assert "resolved/team.yaml" in [file["path"] for file in detail["artifacts"]]


def sse_frames(body):
    frames = []
    for block in body.decode().split("\n\n"):
        values = {}
        for line in block.splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                values[key] = value
        if "data" in values:
            values["data"] = json.loads(values["data"])
            frames.append(values)
    return frames


def finish_job(server, job):
    instance = server.jobs.get(job["id"])
    assert instance.done.wait(10), server.jobs.status(job["id"])
    status = server.jobs.status(job["id"])
    assert status["state"] in ("completed", "stopped", "could_not_start", "failed")
    return status


def test_start_rehearsal_streams_to_completion_and_resumes_by_event_id(ui_team):
    with running(ui_team) as server:
        setup = json.loads(get(server, "/api/run-setup?path=debate")[2])
        assert setup["default_rehearse"]
        status, job = post(server, "/api/start", {"path":"debate", "rehearse":True, "mode":"message", "message":"Space exploration"})
        assert status == 200
        status, headers, body = get(server, "/api/live-events?" + urlencode({"id":job["id"]}))
        assert status == 200 and headers["Content-Type"].startswith("text/event-stream")
        frames = sse_frames(body)
        logs = [frame["data"] for frame in frames if frame["event"] == "log"]
        assert logs[0]["event"] == "run_started" and logs[-1]["event"] == "run_finished"
        assert len([e for e in logs if e["event"] == "agent_message"]) == 3
        assert [e["seq"] for e in logs] == list(range(1, len(logs) + 1))
        assert frames[-1]["event"] == "done" and frames[-1]["data"]["done"]
        # The run timeline's rows come with the log, one per turn so far, and whole again on resume.
        rows = [frame["data"] for frame in frames if frame["event"] == "digest"]
        assert [turn["seq"] for turn in rows[-1]] == [e["seq"] for e in logs if e["event"] == "agent_message"]
        assert frames[-2]["event"] == "digest"
        final = finish_job(server, job)
        record = json.loads((ui_team / final["folder"] / "record.json").read_text())
        assert record["status"] == "completed" and record["cost_usd"] == 0
        assert record["inputs"]["topic"]["value"] == "Space exploration"
        assert not json.loads(get(server, "/api/run-setup?path=debate")[2])["default_rehearse"]
        route = "/api/live-events?" + urlencode({"id":job["id"]})
        resumed = sse_frames(get(server, route, headers={"Last-Event-ID":"3"})[2])
        assert [int(f["id"]) for f in resumed if f["event"] == "log"] == list(range(4, len(logs) + 1))
        assert [f["data"] for f in resumed if f["event"] == "digest"][-1] == rows[-1]
        assert get(server, "/api/live-events?id=missing")[0] == 404


def slow_ui_runner(server, tmp_path, monkeypatch):
    import asyncio
    from functools import partial
    from threading import Event
    from teamrunner.model import Reply
    from teamrunner.run import run_team
    home = tmp_path / "keys"
    home.mkdir()
    (home / ".env").write_text("OPENROUTER_API_KEY=fake-key-for-local-test\n")
    monkeypatch.setenv("TEAMRUNNER_HOME", str(home))
    blocked, cancelled = Event(), Event()
    class Model:
        calls = 0
        async def complete(self, model, params, messages, schema, tools=None):
            self.calls += 1
            if self.calls == 1:
                return Reply("A completed turn.", "fixture-model", {"input":10,"output":5}, 0.001, 1)
            blocked.set()
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return Reply("Late answer.", "fixture-model")
    server.jobs.runner = partial(run_team, model_factory=lambda keys, settings: Model(),
        listers={"openrouter":lambda key, settings: {"google/gemma-4-31b-it"}}, model_data=lambda model: {})
    return blocked, cancelled


def test_live_run_stops_active_turn_keeps_record_and_allows_only_one(ui_team, tmp_path, monkeypatch):
    with running(ui_team) as server:
        blocked, cancelled = slow_ui_runner(server, tmp_path, monkeypatch)
        status, job = post(server, "/api/start", {"path":"debate", "rehearse":False})
        assert status == 200 and blocked.wait(15)
        assert post(server, "/api/start", {"path":"debate", "rehearse":True})[0] == 409
        snapshot = json.loads(get(server, "/api/job?id=" + job["id"])[2])
        assert snapshot["speaking"]["member"] == "against"
        request = Request(server.origin + "/api/live-events?id=" + job["id"], headers={"X-TeamRunner-Token":server.token})
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            first = b""
            while b'"event": "agent_message"' not in first:
                first += response.readline()
            assert not server.jobs.get(job["id"]).done.is_set()  # received before completion
            # Saved team files remain editable while the runner uses its prepared IR.
            file = json.loads(get(server, "/api/file?path=debate/team.yaml")[2])
            assert post(server, "/api/save", {**file, "text":file["text"] + "\n# edited while running\n"})[0] == 200
            assert post(server, "/api/stop", {"id":job["id"]})[0] == 200
            rest = response.read()
            assert b"event: done" in rest
        final = finish_job(server, job)
        assert cancelled.is_set() and final["state"] == "stopped"
        folder = ui_team / final["folder"]
        record = json.loads((folder / "record.json").read_text())
        assert record["status"] == "stopped" and record["turns"] == 1
        assert record["cost_usd"] == 0.001 and record["tokens"] == {"input":10,"output":5}
        assert "Stopped by the user" in record["reason"]
        assert "A completed turn" in (folder / "output.md").read_text()
        events = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()]
        assert events[-1]["event"] == "run_finished" and events[-1]["status"] == "stopped"
        assert post(server, "/api/start", {"path":"debate", "rehearse":True})[0] == 200


def test_server_shutdown_stops_the_active_run_cleanly(ui_team, tmp_path, monkeypatch):
    with running(ui_team) as server:
        blocked, cancelled = slow_ui_runner(server, tmp_path, monkeypatch)
        _, job = post(server, "/api/start", {"path":"debate", "rehearse":False})
        assert blocked.wait(15)
        instance = server.jobs.get(job["id"])
    assert instance.done.is_set() and cancelled.is_set()
    assert json.loads((instance.folder / "record.json").read_text())["status"] == "stopped"


def test_run_start_refuses_validation_errors_and_missing_keys_without_a_run(ui_team, tmp_path, monkeypatch):
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "isolated-home"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    path = ui_team / "debate/team.yaml"
    original = path.read_text()
    path.write_text(original + "\nunknown_setting: true\n")
    with running(ui_team) as server:
        status, response = post(server, "/api/start", {"path":"debate", "rehearse":True})
        assert status == 422 and response["validation"]["problems"]
        assert not server.jobs.jobs and not (ui_team / "debate/runs").exists()
        path.write_text(original)
        status, job = post(server, "/api/start", {"path":"debate", "rehearse":False})
        assert status == 200
        final = finish_job(server, job)
        assert final["state"] == "could_not_start" and final["folder"] is None
        assert final["check"]["problems"] and "OPENROUTER_API_KEY" in json.dumps(final["check"])
        assert not (ui_team / "debate/runs").exists()


def test_uploads_task_files_and_rerun_use_recorded_inputs(ui_team):
    import base64
    def uploaded(name, text):
        return {"name":name,"base64":base64.b64encode(text.encode()).decode()}
    with running(ui_team) as server:
        status, task = post(server, "/api/start", {"path":"debate", "rehearse":True, "mode":"task",
                                                  "upload":uploaded("task.yaml", "inputs:\n  topic: Underwater cities\n")})
        assert status == 200
        first = finish_job(server, task)
        source = json.loads((ui_team / first["folder"] / "record.json").read_text())
        assert source["inputs"]["topic"]["value"] == "Underwater cities"
        status, repeated = post(server, "/api/start", {"path":"debate", "rehearse":True, "rerun":first["folder"]})
        assert status == 200
        again = finish_job(server, repeated)
        assert json.loads((ui_team / again["folder"] / "record.json").read_text())["inputs"] == source["inputs"]
        content = "The uploaded brief."
        status, file = post(server, "/api/start", {"path":"debate", "rehearse":True, "mode":"file", "upload":uploaded("brief.txt", content)})
        assert status == 200
        file_run = finish_job(server, file)
        copied = ui_team / file_run["folder"] / "inputs/brief.txt"
        assert copied.read_text() == content
        status, repeat_file = post(server, "/api/start", {"path":"debate", "rehearse":True, "rerun":file_run["folder"]})
        assert status == 200
        repeated = finish_job(server, repeat_file)
        assert (ui_team / repeated["folder"] / "inputs/brief.txt").read_text() == content
        copied.write_text("changed since the run")
        status, error = post(server, "/api/start", {"path":"debate", "rehearse":True, "rerun":file_run["folder"]})
        assert status == 400 and "changed" in error["error"]


def test_run_control_authentication_and_input_paths(ui_team, tmp_path):
    import base64
    with running(ui_team) as server:
        for route in ["/api/job", "/api/run-setup?path=debate", "/api/live-events?id=anything"]:
            assert get(server, route, token=False)[0] == 403
        for route in ["/api/start", "/api/stop"]:
            assert post(server, route, {"path":"debate"}, token=False)[0] == 403
            assert post(server, route, {"path":"debate"}, headers={"Origin":"https://untrusted.example"})[0] == 403
        assert post(server, "/api/stop", {"id":"missing"})[0] == 404
        assert post(server, "/api/start", {"path":"..", "rehearse":True})[0] == 403
        assert post(server, "/api/start", {"path":"debate", "mode":"file", "rehearse":True,
            "upload":{"name":"../escape.txt","base64":base64.b64encode(b"no").decode()}})[0] == 400
        assert post(server, "/api/start", {"path":"debate", "mode":"file", "rehearse":True,
            "upload":{"name":"brief.txt","base64":"not base64"}})[0] == 400
        assert post(server, "/api/start", {"path":"debate", "mode":"task", "rehearse":True,
            "upload":{"name":"task.yaml","base64":base64.b64encode(b"not a task").decode()}})[0] == 400
        assert not (tmp_path / "escape.txt").exists()
        assert not server.jobs.jobs


def test_runs_directory_cannot_redirect_a_new_run_outside_root(ui_team, tmp_path):
    try:
        (ui_team / "debate/runs").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with running(ui_team) as server:
        assert post(server, "/api/start", {"path":"debate", "rehearse":True})[0] == 403
        assert not server.jobs.jobs


def test_stop_and_status_remain_responsive_during_startup(ui_team):
    from threading import Event
    from teamrunner.run import run_team
    entered, release = Event(), Event()
    def starting(folder, options, **kwargs):
        entered.set()
        assert release.wait(5)
        return run_team(folder, options, **kwargs)
    with running(ui_team) as server:
        server.jobs.runner = starting
        _, job = post(server, "/api/start", {"path":"debate", "rehearse":True})
        assert entered.wait(3)
        try:
            assert get(server, "/")[0] == 200
            assert json.loads(get(server, "/api/job?id=" + job["id"])[2])["state"] == "starting"
            assert post(server, "/api/stop", {"id":job["id"]})[0] == 200
        finally:
            release.set()
        final = finish_job(server, job)
        assert final["state"] == "stopped"
        record = json.loads((ui_team / final["folder"] / "record.json").read_text())
        assert record["turns"] == 0 and record["status"] == "stopped"


def test_rehearsal_can_stop_between_immediate_turns(ui_team):
    from threading import Event
    from teamrunner.run import Options, run_team
    cancel = Event()
    turns = []
    def current(info):
        turns.append(info["member"])
        if len(turns) == 2:
            cancel.set()
    outcome = run_team(ui_team / "debate", Options(rehearse=True, quiet=True), cancel=cancel, on_turn=current)
    assert outcome.status == "stopped"
    record = json.loads((outcome.folder / "record.json").read_text())
    assert record["turns"] == 1 and record["cost_usd"] == 0


def test_a_mailbox_outside_the_opened_folder_is_refused(tmp_path):
    """Found in the 2026-11-16 review (S10): the page kept run_code's folders inside the
    opened folder but not the mailbox read_email reads or save_draft writes into."""
    from threading import RLock
    from teamrunner.build import load_team
    from teamrunner.ui import api, jobs
    from test_validate import debate
    outside = tmp_path / "outside"
    (outside / "mail").mkdir(parents=True)
    root = tmp_path / "root"
    clerk = ("---\nkind: toolkeeper\ntools:\n  read_email: {{mailbox: {mailbox}}}\naccess:\n  judge: standard\n---\n"
             "I hand over emails.\n")
    for mailbox, refused in [(str(outside / "mail"), True), ("../../outside/mail", True), ("mailbox", False)]:
        team_dir = debate(root / "debate", **{"clerk.md": clerk.format(mailbox=mailbox)})
        (team_dir / "mailbox").mkdir(exist_ok=True)
        team, report = load_team(team_dir)
        assert team is not None, report
        guard = jobs.Jobs(root.resolve(), RLock())
        try:
            if refused:
                with pytest.raises(api.OutsideRoot, match="clerk's mailbox points outside the opened folder"):
                    guard._guard_tools(team)
            else:
                guard._guard_tools(team)
        finally:
            guard.close()



def test_ui_with_a_name_opens_it_from_the_workspace(tmp_path, monkeypatch):
    """Found in the 2026-11-16 review (D5): `teamrunner ui debate` looked only in the current folder."""
    opened = []
    monkeypatch.setattr("teamrunner.ui.server.serve", lambda folder: opened.append(folder) or 0)
    space = tmp_path / "space"
    (space / "teams" / "debate").mkdir(parents=True)
    (space / "experiments" / "study").mkdir(parents=True)
    monkeypatch.setenv("TEAMRUNNER_WORKSPACE", str(space))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert main(["ui", "debate"]) == 0 and opened[-1] == space / "teams" / "debate"
    assert main(["ui", "study"]) == 0 and opened[-1] == space / "experiments" / "study"
    (elsewhere / "local").mkdir()
    assert main(["ui", "local"]) == 0 and opened[-1] == Path("local")  # a folder here comes first


def test_the_open_file_is_checked_by_its_fingerprint(ui_team):
    """Found in the 2026-11-16 review (U8): every 3 seconds the page downloaded the whole open
    file, up to 2 MB, to see whether it changed. It now asks for the fingerprint only."""
    with running(ui_team) as server:
        full = json.loads(get(server, "/api/file?path=debate/team.yaml")[2])
        status, _, body = get(server, "/api/file?path=debate/team.yaml&only=revision")
        seen = json.loads(body)
        assert status == 200 and seen == {"path": "debate/team.yaml", "revision": full["revision"]}
        assert get(server, "/api/file?path=../outside&only=revision")[0] == 403


def test_the_run_dialog_is_given_an_estimate(ui_team):
    """Found in the 2026-11-16 review (U9): a team's real run showed no cost before it started."""
    from teamrunner.new import copy_from
    copy_from(EXAMPLES / "round_robin", ui_team / "priced")
    team_file = ui_team / "priced/team.yaml"
    team_file.write_text(team_file.read_text().replace("teamrunner/demoLLM", "openai/gpt-5"))
    copy_from(EXAMPLES / "round_robin", ui_team / "demo_team")
    with running(ui_team) as server:
        estimate = json.loads(get(server, "/api/run-estimate?path=priced")[2])["estimate"]
        assert estimate["typical_usd"] > 0 and estimate["most_usd"] >= estimate["typical_usd"]
        assert estimate["calls"] == 3 and estimate["unpriced"] == []
        free = json.loads(get(server, "/api/run-estimate?path=demo_team")[2])["estimate"]
        assert free["typical_usd"] == 0 and free["most_usd"] == 0
        unknown = json.loads(get(server, "/api/run-estimate?path=debate")[2])["estimate"]
        assert unknown["typical_usd"] is None and unknown["unpriced"] == ["openrouter/google/gemma-4-31b-it"]


# --- review 2026-11-21: security (NS7, NS9) and the browser page (NU1 to NU12) -----------------

def test_the_estimate_is_capped_and_made_without_holding_the_file_lock(ui_team, monkeypatch):
    """NU5: the Run dialog's request made the estimate while holding the file lock, and a chair
    with thousands of steps held the whole page for minutes. The dialog no longer asks for it;
    its own request reads the team under the lock, then walks the flow with the lock let go,
    and gives up at a deadline."""
    from threading import Thread
    from teamrunner.build import load_team
    from teamrunner.ui import jobs
    team, _ = load_team(ui_team / "debate")
    late = jobs.run_estimate(team, seconds=-1)  # the deadline has passed before the first turn
    assert late["too_long"] and late["walks"] == 0 and late["typical_usd"] is None and "too many turns" in late["note"]
    whole = jobs.run_estimate(team)
    assert not whole["too_long"] and whole["walks"] == whole["of"] == 8 and whole["calls"] == 3
    free = []
    with running(ui_team) as server:
        def walking(team):
            def other():  # another request's thread: Save, say
                free.append(server.files_lock.acquire(timeout=2))
                if free[-1]:
                    server.files_lock.release()
            thread = Thread(target=other)
            thread.start()
            thread.join()
            return {"typical_usd": 1.0}
        monkeypatch.setattr(jobs, "run_estimate", walking)
        setup = json.loads(get(server, "/api/run-setup?path=debate")[2])
        assert "estimate" not in setup and free == []  # opening the dialog walks nothing
        status, _, body = get(server, "/api/run-estimate?path=debate")
        assert status == 200 and json.loads(body) == {"team": "debate", "estimate": {"typical_usd": 1.0}}
        assert free == [True]
        assert get(server, "/api/run-estimate?path=../outside")[0] == 403
        assert get(server, "/api/run-estimate?path=debate", token=False)[0] == 403


def experiment_on_debate(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAMRUNNER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TEAMRUNNER_WORKSPACE", raising=False)
    root = tmp_path / "teamrunner"
    (root / "teams").mkdir(parents=True)
    with running(root) as server:
        assert post(server, "/api/new", {"path": "debate", "example": "multi_agent_debate"})[0] == 200
        assert post(server, "/api/new-experiment", {"name": "study", "team": "teams/debate"})[0] == 200
    return root, "experiments/study"


def test_variant_metric_and_task_stay_inside_the_opened_folder(tmp_path, monkeypatch):
    """NS7: + Variant, + Metric and + Task never passed their target through the check every
    other write gets. A `variants` that is a link wrote outside the folder, a link waiting at
    the new file's name had its target created, and a source such as `.env` was copied into
    a variant the page then shows."""
    root, exp = experiment_on_debate(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / exp / "variants").symlink_to(outside, target_is_directory=True)
    (root / exp / "metrics").symlink_to(root / "teams", target_is_directory=True)  # a link, though it stays inside
    (root / exp / "tasks").mkdir()
    (root / exp / "tasks" / "case1.yaml").symlink_to(outside / "made.yaml")  # leads nowhere yet
    (root / "teams/debate/.env").write_text("OPENAI_API_KEY=sk-secret\n")
    (root / "teams/debate/linked.md").symlink_to(outside / "notes.md")
    (outside / "notes.md").write_text("---\ndescription: x\n---\nprivate notes\n")
    with running(root) as server:
        refused = [post(server, "/api/variant", {"experiment": exp, "name": "strict", "source": "debate/judge.md"}),
                   post(server, "/api/metric", {"experiment": exp, "name": "graded", "kind": "evaluator"}),
                   post(server, "/api/metric", {"experiment": exp, "name": "keyed", "kind": "answer_key"}),
                   post(server, "/api/task", {"experiment": exp, "name": "case1"})]
        assert [status for status, _ in refused] == [403] * 4, refused
        assert all("link" in answer["error"] and str(tmp_path) not in answer["error"] for _, answer in refused)
        assert sorted(p.name for p in outside.iterdir()) == ["notes.md"]
        assert not list((root / "teams").glob("graded*")) and not list((root / "teams").glob("keyed*"))
        (root / exp / "variants").unlink()
        for source in ("debate/.env", "debate/.env#prompt", "debate/linked.md", "debate/team.yaml", "debate/../debate/judge.md"):
            status, answer = post(server, "/api/variant", {"experiment": exp, "name": "copied", "source": source})
            assert status == 400 and answer["error"] == "Choose one of the team's files.", (source, answer)
        assert not (root / exp / "variants").exists()
        assert post(server, "/api/variant", {"experiment": exp, "name": "strict", "source": "debate/judge.md"})[0] == 200
        assert post(server, "/api/variant", {"experiment": exp, "name": "flow", "source": "debate/chair.md"})[0] == 200


def test_one_metric_name_is_one_file(tmp_path, monkeypatch):
    """Review 2026-11-21 (smaller): one name for an answer key and an evaluator made
    metrics/x.yaml and metrics/x.md, both used by the same line under `metrics:`."""
    root, exp = experiment_on_debate(tmp_path, monkeypatch)
    with running(root) as server:
        assert post(server, "/api/metric", {"experiment": exp, "name": "quality", "kind": "answer_key"})[0] == 200
        status, answer = post(server, "/api/metric", {"experiment": exp, "name": "quality", "kind": "evaluator"})
        assert status == 409 and answer["error"] == "metrics/quality.yaml already exists. Choose another name."
        assert sorted(p.name for p in (root / exp / "metrics").iterdir()) == ["quality.yaml"]


def test_open_run_folder_reveals_and_never_launches():
    """NS9: on macOS `open <folder>` starts a folder that is an app bundle, and a downloaded
    team can hold a run folder named `results.app`. `open -R` only selects it in Finder."""
    from teamrunner.ui.runviews import reveal_command
    folder = Path("/teams/debate/runs/2026-11-06_100000_results.app")
    assert reveal_command("darwin", folder) == ["open", "-R", str(folder)]
    assert reveal_command("linux", folder) == ["xdg-open", str(folder)]


def test_a_new_team_is_never_made_inside_a_team(ui_team, tmp_path):
    """NU9: + Team inside a team made a subteam, and the parent then failed with "teams inside
    teams comes in a later version"."""
    from teamrunner.validate import validate
    with running(ui_team) as server:
        for inner in ("debate/inner", "debate/models/inner"):
            status, answer = post(server, "/api/new", {"path": inner})
            assert status == 400 and answer["error"].startswith("debate is a team"), answer
            assert post(server, "/api/new", {"path": inner, "example": "fact_checked"})[0] == 400
        status, answer = post(server, "/api/describe", {"path": "debate/inner", "description": "A team.", "model": "x"})
        assert status == 400 and answer["error"].startswith("debate is a team")
        assert not (ui_team / "debate/inner").exists() and not (ui_team / "debate/models/inner").exists()
        assert post(server, "/api/new", {"path": "beside"})[0] == 200
    assert validate(ui_team / "debate").ok
    with running(ui_team / "debate") as server:  # the opened folder is itself a team
        status, answer = post(server, "/api/new", {"path": "inner"})
        assert status == 400 and "itself a team" in answer["error"] and str(tmp_path) not in answer["error"]
        assert not (ui_team / "debate/inner").exists()


def test_dialogs_get_plain_sentences_without_whole_paths(ui_team, tmp_path):
    """NU12: "[Errno 17] File exists: '/…/researcher.md'", "invalid literal for int()" and a
    temporary folder's path in place of the uploaded file's name reached the dialogs."""
    import base64
    def raw(answer):
        text = json.dumps(answer)
        return "Errno" in text or "invalid literal" in text or str(tmp_path) in text or "teamrunner-ui-input" in text
    with running(ui_team) as server:
        assert post(server, "/api/member", {"path": "debate/researcher.md"})[0] == 200
        status, answer = post(server, "/api/member", {"path": "debate/researcher.md"})
        assert status == 409 and answer["error"] == "researcher.md already exists in this team. Choose another name."
        for query in ("limit=many", "offset=x"):
            status, _, body = get(server, "/api/runs?path=debate&" + query)
            assert status == 400 and "whole number" in json.loads(body)["error"] and not raw(json.loads(body))
        for mode, name, content in (("task", "task.yaml", b"not a task"), ("task", "task.yaml", b"inputs: [1\n"),
                                    ("task", "task.yaml", b"inputs:\n  nobody_uses_this: 1\n")):
            status, answer = post(server, "/api/start", {"path": "debate", "mode": mode, "rehearse": True,
                                  "upload": {"name": name, "base64": base64.b64encode(content).decode()}})
            assert status == 400 and not raw(answer), answer
        status, answer = post(server, "/api/start", {"path": "debate", "mode": "task", "rehearse": True,
                              "upload": {"name": "task.yaml", "base64": base64.b64encode(b"not a task").decode()}})
        assert answer["error"].startswith("task.yaml should hold `inputs:`")
        assert post(server, "/api/new", {"path": "fresh"})[0] == 200
        status, answer = post(server, "/api/new", {"path": "fresh"})
        assert status in (400, 409) and answer["error"].startswith("fresh already exists") and not raw(answer)


def test_a_request_refused_for_its_size_is_answered_not_dropped(ui_team):
    """NU8: a 13 MB request was refused with its body unread, so the connection was reset and
    the browser reported a failed request, which the page took for the server having stopped."""
    with running(ui_team) as server:
        status, answer = post(server, "/api/start", {"path": "debate", "padding": "x" * (13 * 1024 * 1024)})
        assert status == 413 and "too large" in answer["error"]
        assert get(server, "/api/info")[0] == 200


def test_rename_keeps_the_rules_of_new_member(ui_team):
    """Review 2026-11-21 (smaller): Rename took names + Member refuses, and made the chair a member."""
    with running(ui_team) as server:
        chair = post(server, "/api/chair", {"path": "debate"})[1]
        status, answer = post(server, "/api/rename", {**chair, "destination": "debate/boss.md"})
        assert status == 400 and "Keep chair.md" in answer["error"] and (ui_team / "debate/chair.md").exists()
        status, member = post(server, "/api/member", {"path": "debate/researcher.md"})
        for name in ("chair.md", "manager.md", "readme.md", "done.md", "two words.md", "9.md"):
            assert post(server, "/api/rename", {**member, "destination": f"debate/{name}"})[0] == 400, name
        assert (ui_team / "debate/researcher.md").exists()
        assert post(server, "/api/rename", {**member, "destination": "debate/04_reviewer.md"})[0] == 200


def test_the_runs_list_names_an_input_file_and_the_folder_it_read(ui_team):
    """Review 2026-11-21 (smaller): a file run showed the start of the file's whole path, the
    same for every file run; and the footer's command was given the file chosen in the sidebar."""
    import base64
    with running(ui_team) as server:
        status, job = post(server, "/api/start", {"path": "debate", "rehearse": True, "mode": "file",
                           "upload": {"name": "brief.txt", "base64": base64.b64encode(b"The brief.").decode()}})
        assert status == 200
        finish_job(server, job)
        listed = json.loads(get(server, "/api/runs?path=debate/team.yaml")[2])
        assert listed["source"] == "debate" and [run["message"] for run in listed["runs"]] == ["brief.txt (file)"]


def test_the_digest_gives_one_row_per_turn_for_any_team():
    """The run timeline and the run wall are drawn from this, so it names no field or member:
    a tool call counts with the member's next message, or its last when none follows; a
    route decision counts with the decider's turn; a long output is never a value."""
    from teamrunner.ui.runviews import digest
    events = [
        {"event": "run_started", "seq": 1},
        {"event": "tool_call", "seq": 2, "from": "reader", "tool": "read_email", "access": "refused"},
        {"event": "agent_message", "seq": 3, "from": "reader", "content": "It needs an answer.", "read": [],
         "output": {"lines": "10, 12,  15", "Urgent": True, "why": "x" * 40}, "usage": {"input": 100, "output": 9},
         "cost_usd": 0.002, "latency_ms": 1500, "stage": "sort"},
        {"event": "route_decision", "seq": 4, "decider": "reader", "field": "lines", "option": "10, 12, 15",
         "to": "answer", "bound_applied": True},
        {"event": "agent_message", "seq": 5, "from": "clerk", "content": "", "read": [3], "block": "answer",
         "pass": {"number": 2, "of": 3}, "usage": {}},
        {"event": "tool_call", "seq": 6, "from": "clerk", "tool": "save_draft", "access": "allowed"},
        {"event": "agent_message", "seq": 7},          # an event of an unexpected shape is still a turn
        "not an event",
    ]
    reader, clerk, odd = digest(events)
    assert reader["outputs"] == {"lines": "10,12,15", "Urgent": "true"} and reader["long"] == ["why"]
    assert reader["tools"] == [{"tool": "read_email", "access": "refused"}]
    assert reader["route"] == {"field": "lines", "option": "10, 12, 15", "to": "answer", "limit": True}
    assert (reader["words"], reader["tokens_read"], reader["cost"], reader["ms"]) == (4, 100, 0.002, 1500)
    assert (reader["group"], reader["round"], reader["read"]) == ("sort", None, [])
    assert (clerk["group"], clerk["round"], clerk["words"], clerk["read"]) == ("answer", 2, 0, [3])
    assert clerk["tools"] == [{"tool": "save_draft", "access": "allowed"}] and clerk["route"] is None
    assert odd["member"] == "member" and odd["read"] is None and odd["outputs"] == {}
    assert "read" not in digest(events, reads=False)[0]


def test_a_run_and_the_wall_carry_the_digest(recorded_runs):
    with running(recorded_runs) as server:
        assert "digest" not in json.loads(get(server, "/api/runs")[2])["runs"][0]
        wall = json.loads(get(server, "/api/runs?wall=1")[2])["runs"]
        middle = next(r for r in wall if r["path"].endswith("2026-11-06_100000_debate"))
        assert [t["member"] for t in middle["digest"]] == ["for", "judge"]
        assert middle["digest"][1]["outputs"] == {"winner": "for"} and "read" not in middle["digest"][1]
        one = json.loads(get(server, "/api/run?" + urlencode({"path": middle["path"]}))[2])
        assert one["digest"][1]["read"] == [3, 4] and one["digest"][1]["route"]["field"] == "winner"
        assert [c["access"] for c in one["digest"][1]["tools"]] == ["allowed", "retry"]
        assert get(server, "/picture.js")[0] == 200
