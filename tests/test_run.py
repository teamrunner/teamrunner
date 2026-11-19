# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
import io
import json
import os
from pathlib import Path

import pytest

from teamrunner.console import Console
from teamrunner.keys import PROVIDER_KEYS, find_keys, read_env
from teamrunner.model import Reply, TransientError
from teamrunner.run import Options, run_team

from test_engine import FACT_CHECKED
from test_turns import NAMED, FakeModel, fits
from test_validate import debate

OPENAI = {"openai": lambda key, settings: {"gpt-5", "gpt-5-mini"}}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A key file of the test's own, and no keys in the shell."""
    home = tmp_path / "home"
    monkeypatch.setenv("TEAMRUNNER_HOME", str(home))
    for name in PROVIDER_KEYS.values():  # never a real key from the shell running the tests
        monkeypatch.delenv(name, raising=False)
    home.mkdir()
    (home / ".env").write_text("OPENAI_API_KEY=sk-test-from-key-file\n", encoding="utf-8")
    (home / ".env").chmod(0o600)  # as TeamRunner makes it; a hand-made one is made private (D28)
    return home


def go(folder, answers=None, options=None, listers=OPENAI, model=None):
    fake = model or FakeModel(answers)
    stream = io.StringIO()
    outcome = run_team(folder, options or Options(), model_factory=lambda keys, settings: fake,
                       listers=listers, model_data=lambda m: {}, waits=(0, 0, 0),
                       console=None if (options and options.quiet) else Console(False, stream))
    return outcome, fake, stream.getvalue()


def read_run(folder):
    run = json.loads((folder / "record.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return run, events, (folder / "output.md").read_text(encoding="utf-8")


TEAM = {**NAMED, "chair.md": "rounds(*2) -> judge\nrounds = for -> against\n"}
VERDICT = {"judge": [fits("Con argued better.", winner="against", reasoning="clearer")]}


def test_a_run_end_to_end(tmp_path):
    team = debate(tmp_path / "debate", **TEAM)
    outcome, model, printed = go(team, VERDICT)
    assert (outcome.status, outcome.exit_code) == ("completed", 0)
    assert outcome.folder.parent == team / "runs"
    assert outcome.folder.name.endswith("_debate")

    run, events, output = read_run(outcome.folder)
    kinds = [e["event"] for e in events if e["event"] != "warning"]  # NAMED's unused fact_checker warns
    assert kinds == ["run_started", "check"] + ["agent_message"] * 5 + ["run_finished"]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert len({e["run_id"] for e in events}) == 1 and run["run_id"] == events[0]["run_id"]
    started = events[0]
    assert started["entry"] == "debate"
    assert started["inputs"] == {"topic": {"value": "Cars downtown", "source": "default"}}
    judge = next(m for m in started["members"] if m["name"] == "judge")
    assert judge["prompt"] == "judge decides on Cars downtown."
    assert judge["model_from"] == "own" and judge["version_hash"].startswith("sha256:")
    assert judge["component_id"]  # validate wrote the id in first (D64)
    assert run["status"] == "completed" and run["turns"] == 5
    assert run["keys"] == {"OPENAI_API_KEY": "key_file"}
    assert run["cost_usd"] == pytest.approx(0.005)
    assert "sk-test" not in (outcome.folder / "events.jsonl").read_text(encoding="utf-8") + json.dumps(run)
    assert output.startswith("**judge**\n\nCon argued better.")
    assert "- **winner:** against" in output

    assert "run · debate · default input topic: \"Cars downtown\"" in printed
    assert "✓ 1 model · 1 key" in printed
    assert "rounds 1 of 2" in printed and "rounds 2 of 2" in printed
    assert "winner = against" in printed
    assert printed.rstrip().endswith("- **winner:** against\n- **reasoning:** clearer")


def test_a_message_fills_its_input(tmp_path):
    outcome, model, _ = go(debate(tmp_path, **TEAM), VERDICT, Options(message="Zoos"))
    run, _, _ = read_run(outcome.folder)
    assert run["inputs"]["topic"] == {"value": "Zoos", "source": "given"}
    assert model.calls[0][3][0]["content"] == "for argues for Zoos."


def test_a_task_file_and_a_file(tmp_path):
    team = debate(tmp_path / "t", **{**TEAM, "team.yaml": "message_goes_to: topic\n"})
    task = tmp_path / "task.yaml"
    task.write_text("inputs:\n  topic: From a task file\n", encoding="utf-8")
    outcome, _, _ = go(team, VERDICT, Options(task_file=task))
    assert read_run(outcome.folder)[0]["inputs"]["topic"]["source"] == "given"

    paper = tmp_path / "paper.txt"
    paper.write_text("a paper", encoding="utf-8")
    outcome, model, _ = go(team, VERDICT, Options(file=paper))
    copy = outcome.folder / "inputs" / "paper.txt"
    assert copy.read_text(encoding="utf-8") == "a paper"
    run = read_run(outcome.folder)[0]
    assert run["inputs"]["topic"]["value"] == str(copy)
    assert run["inputs"]["topic"]["file"]["original"] == str(paper.resolve())


@pytest.mark.parametrize("options, says", [
    (Options(), "needs `topic`, which has no default"),
    (Options(task_file=Path("/nowhere.yaml")), "there is no task file"),
])
def test_inputs_that_stop_the_run_before_it_starts(tmp_path, options, says):
    team = debate(tmp_path, **{**TEAM, "team.yaml": "name: debate\n"})
    outcome, model, printed = go(team, options=options)
    assert (outcome.status, outcome.exit_code) == ("could_not_start", 1)
    assert says in outcome.reason and model.calls == []
    assert not (team / "runs").exists()


def test_problems_with_keys_and_models_are_listed_together(tmp_path, home):
    (home / ".env").write_text("", encoding="utf-8")
    team = debate(tmp_path, **{**TEAM, "against.md": NAMED["against.md"].replace("openai/gpt-5", "anthropic/claude-x")})
    listers = {**OPENAI, "anthropic": lambda k, s: {"claude-sonnet-4-5-20250929"}}
    outcome, model, printed = go(team, listers=listers)
    assert outcome.exit_code == 1 and model.calls == []
    assert "OPENAI_API_KEY" in printed and "ANTHROPIC_API_KEY" in printed
    assert "for and judge use openai/gpt-5, which needs OPENAI_API_KEY" in printed


def test_a_model_the_provider_does_not_serve(tmp_path):
    team = debate(tmp_path, **{**TEAM, "for.md": NAMED["for.md"].replace("gpt-5", "gpt-5-minii")})
    outcome, _, printed = go(team)
    assert outcome.exit_code == 1
    assert "openai does not serve `gpt-5-minii`" in printed
    assert "did you mean `openai/gpt-5-mini`?" in printed


def test_dated_and_pulled_models_count_as_served():
    from teamrunner.checks import served
    assert served("claude-sonnet-4-5", {"claude-sonnet-4-5-20250929"})
    assert not served("gpt-4", {"gpt-4-turbo"})
    assert served("llama3", {"llama3:latest"})


def test_a_flow_stop_ends_the_run_stopped(tmp_path):
    team = debate(tmp_path, **{**NAMED, "chair.md": "fact_checker -> for -> against -> judge\n"
                               "fact_checker --pass--> for\nfact_checker --fail--> stop\n"})
    outcome, _, _ = go(team, {"fact_checker": [fits("Not a debate topic.", verdict="fail", reasons="")]})
    run, events, output = read_run(outcome.folder)
    assert (outcome.status, outcome.exit_code) == ("stopped", 130)
    assert "fact_checker answered `fail`" in run["reason"]
    assert "Not a debate topic." in output


def test_a_failed_turn_keeps_the_log(tmp_path):
    outcome, _, printed = go(debate(tmp_path, **TEAM), {"judge": ["not json"] * 3})
    run, events, output = read_run(outcome.folder)
    assert (outcome.status, outcome.exit_code) == ("failed", 2)
    assert run["turns"] == 4 and events[-1]["event"] == "run_finished"
    assert len([e for e in events if e["event"] == "retry"]) == 3
    assert output.startswith("**The run failed.** judge's reply did not fit after 3 tries")


class Flaky(FakeModel):
    def __init__(self, failures, answers=None):
        super().__init__(answers)
        self.failures = failures

    async def complete(self, *args):
        if self.failures:
            self.failures -= 1
            raise TransientError("rate limited")
        return await super().complete(*args)


def test_provider_failures_are_retried_then_fail_the_run(tmp_path):
    outcome, _, _ = go(debate(tmp_path / "a", **TEAM), VERDICT, model=Flaky(2, VERDICT))
    _, events, _ = read_run(outcome.folder)
    assert outcome.status == "completed"
    assert [(e["kind"], e["attempt"]) for e in events if e["event"] == "retry"] == [("provider", 1), ("provider", 2)]

    outcome, _, _ = go(debate(tmp_path / "b", **TEAM), model=Flaky(99))
    assert outcome.status == "failed" and "still failed after 4 tries" in outcome.reason


def test_quiet_prints_nothing(tmp_path, capsys):
    outcome, _, printed = go(debate(tmp_path, **TEAM), VERDICT, Options(quiet=True))
    assert outcome.exit_code == 0 and capsys.readouterr().out == ""


def test_validate_errors_stop_the_run(tmp_path):
    outcome, model, _ = go(debate(tmp_path, **{**TEAM, "for.md": "---\nmodle: x\n---\nhi\n"}))
    assert outcome.exit_code == 1 and model.calls == [] and not outcome.report.ok


# --- keys (D99) ----------------------------------------------------------------

def test_a_shell_key_is_offered_a_save(tmp_path, home):
    (home / ".env").write_text("", encoding="utf-8")
    asked = []
    keys = find_keys(["OPENAI_API_KEY"], tmp_path, lambda q, a: asked.append(q) or "Yes",
                     env={"OPENAI_API_KEY": "sk-shell"})
    assert keys.sources == {"OPENAI_API_KEY": "key_file"}
    assert read_env(home / ".env") == {"OPENAI_API_KEY": "sk-shell"}
    if os.name != "nt":  # Windows keeps a file private by its folder, not its mode
        assert oct((home / ".env").stat().st_mode & 0o777) == "0o600"
    assert "isn't in" in asked[0]


def test_no_one_at_the_keyboard_uses_a_shell_key_once(tmp_path, home):
    (home / ".env").write_text("", encoding="utf-8")
    keys = find_keys(["OPENAI_API_KEY"], tmp_path, None, env={"OPENAI_API_KEY": "sk-shell"})
    assert keys.sources == {"OPENAI_API_KEY": "shell_once"} and read_env(home / ".env") == {}


def test_a_package_key_is_offered_a_move(tmp_path, home):
    (home / ".env").write_text("", encoding="utf-8")
    team = debate(tmp_path / "debate")
    (team / ".env").write_text("OPENAI_API_KEY=sk-pkg\n", encoding="utf-8")
    keys = find_keys(["OPENAI_API_KEY"], team, lambda q, a: "Yes", env={})
    assert keys.sources == {"OPENAI_API_KEY": "key_file"}
    assert not (team / ".env").exists() and read_env(home / ".env") == {"OPENAI_API_KEY": "sk-pkg"}

    (team / ".env").write_text("OPENAI_API_KEY=sk-other\nOTHER=1\n", encoding="utf-8")
    keys = find_keys(["OPENAI_API_KEY"], team, None, env={})
    assert keys.values["OPENAI_API_KEY"] == "sk-pkg"  # the key file wins
    assert "different OPENAI_API_KEY" in keys.warnings[0]


def test_the_litellm_wrapper_offline():
    """LiteLLM's own mock_response: the real call path, with no network."""
    import asyncio
    from teamrunner.litellm_model import LiteLLMModel
    from teamrunner.turns import output_schema
    from teamrunner.ir import OutputField
    model = LiteLLMModel({"OPENAI_API_KEY": "sk-test"}, {})
    schema = output_schema({"winner": OutputField("winner", "choice", ("for", "against"))})
    text = '{"said": "Con.", "output": {"winner": "against"}}'
    model.extra = {"mock_response": text}
    reply = asyncio.run(model.complete("openai/gpt-5", {}, [{"role": "user", "content": "hi"}], schema))
    assert reply.text == text and reply.usage["output"] > 0


@pytest.mark.parametrize("name, with_tools, native_schema", [
    ("openrouter/google/gemma-4-31b-it", True, False),
    ("openrouter/google/gemma-4-31b-it", False, True),
    ("openai/gpt-5", True, True),
])
def test_tool_use_keeps_gemma_final_outputs_validated(monkeypatch, name, with_tools, native_schema):
    import asyncio
    import litellm
    from teamrunner.ir import OutputField
    from teamrunner.litellm_model import LiteLLMModel
    from teamrunner.turns import output_schema, read_reply

    fields = {"paper": OutputField("paper", "choice", ("full", "not_full"))}
    schema = output_schema(fields)
    messages = [{"role": "user", "content": "Read the paper with the library."}]
    tools = [{"type": "function", "function": {"name": "library", "parameters": {"type": "object"}}}]
    sent = {}

    async def complete(**kwargs):
        sent.update(kwargs)
        return litellm.ModelResponse(model=name, choices=[{"message": {
            "role": "assistant", "content": fits("Read the PDF.", paper="full")}}])

    monkeypatch.setattr(litellm, "acompletion", complete)
    reply = asyncio.run(LiteLLMModel({}, {}).complete(name, {}, messages, schema,
                                                     tools if with_tools else None))
    assert read_reply(reply.text, fields) == ("Read the PDF.", {"paper": "full"}, [])
    assert ("response_format" in sent) == native_schema
    if with_tools:
        assert sent["tools"] == tools
    if not native_schema:
        assert json.dumps(schema) in sent["messages"][-1]["content"]
    assert messages == [{"role": "user", "content": "Read the paper with the library."}]


def test_run_warns_about_links_outside_every_time(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "against.md").write_text(NAMED["against.md"], encoding="utf-8")
    team = debate(tmp_path / "debate", **{**TEAM, "against.md": None})
    (team / "against.md").symlink_to(shared / "against.md")
    outcome, _, printed = go(team, VERDICT)
    assert outcome.status == "completed"
    assert "! against.md  this is a link to" in printed and "outside the package" in printed
    _, events, _ = read_run(outcome.folder)
    assert any(e["event"] == "warning" and "outside the package" in e["message"] for e in events)


def test_validate_checks_keys_and_models_without_a_run(tmp_path, monkeypatch, capsys):
    from teamrunner import checks
    from teamrunner.cli import main
    monkeypatch.setitem(checks.LISTERS, "openai", lambda key, settings: {"gpt-5"})
    monkeypatch.setattr(checks, "litellm_data", lambda m: {})
    team = debate(tmp_path, **{**TEAM, "against.md": NAMED["against.md"].replace("params:\n  temperature: 0.7\n", "")})
    assert main(["validate", str(team)]) == 0  # D140: no switch needed
    assert "✓ 1 model · 1 key" in capsys.readouterr().out
    assert not (team / "runs").exists()

    monkeypatch.setitem(checks.LISTERS, "openai", lambda key, settings: {"gpt-4o"})
    assert main(["validate", str(team), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "does not serve `gpt-5`" in data["online"]["problems"][0]["wrong"]
    assert main(["validate", str(team)]) == 1
    assert "files OK, 1 warning, 1 problem with keys or models" in capsys.readouterr().out
    assert main(["validate", str(team), "--online"]) == 1  # the old switch is still accepted

    # --offline checks the files only and asks no one
    monkeypatch.setitem(checks.LISTERS, "openai", lambda key, settings: 1 / 0)
    assert main(["validate", str(team), "--offline"]) == 0
    assert "keys and models not checked (--offline)" in capsys.readouterr().out

    # no connection: the files are still valid, and the models are reported as not checked
    import urllib.error
    def unplugged(key, settings):
        raise urllib.error.URLError("nodename nor servname provided")
    monkeypatch.setitem(checks.LISTERS, "openai", unplugged)
    assert main(["validate", str(team)]) == 0
    assert "could not reach openai" in capsys.readouterr().out


def test_settings_a_model_refuses_stop_the_run_before_it_starts(tmp_path):
    from teamrunner.checks import litellm_params
    team = debate(tmp_path, **TEAM)  # NAMED's against has temperature 0.7 on openai/gpt-5
    from teamrunner.checks import run_check
    from teamrunner.build import load_team
    from teamrunner.keys import Keys
    check = run_check(load_team(team)[0], Keys(values={"OPENAI_API_KEY": "x"}, sources={}), OPENAI, {},
                      lambda m: {}, litellm_params)
    [problem] = check.problems
    assert problem.who == ["against"] and "doesn't support temperature=0.7" in problem.wrong
    assert "drop_params" not in problem.wrong


def test_tool_calls_reach_the_provider_and_broken_installs_are_not_retried(monkeypatch):
    """LiteLLM 1.106 loads its MCP code for any call that offers tools (needing the `mcp`
    package), and reported the missing package as a connection error, which was retried."""
    import asyncio
    import litellm
    from teamrunner.litellm_model import LiteLLMModel
    from teamrunner.model import TransientError
    tools = [{"type": "function", "function": {"name": "web", "description": "search", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
        "additionalProperties": False}}}]
    model = LiteLLMModel({"OPENAI_API_KEY": "sk-test"}, {})
    with pytest.raises(TransientError) as caught:  # nothing listens there: a real connection failure
        model.extra = {"api_base": "http://127.0.0.1:9/v1"}
        asyncio.run(model.complete("openai/gpt-5", {}, [{"role": "user", "content": "hi"}], None, tools))
    assert "No module named" not in str(caught.value)

    async def broken(**kwargs):
        try:
            raise ModuleNotFoundError("No module named 'mcp'")
        except ModuleNotFoundError as e:
            raise litellm.APIConnectionError("OpenAIException - No module named 'mcp'", "openai", "gpt-5") from e
    monkeypatch.setattr(litellm, "acompletion", broken)
    model.extra = {}
    with pytest.raises(RuntimeError, match="could not make the call: ModuleNotFoundError"):
        asyncio.run(model.complete("openai/gpt-5", {}, [{"role": "user", "content": "hi"}], None, tools))


def test_a_local_models_exact_build_is_recorded(tmp_path):
    """Ollama lists each pulled model with its digest; the check records it, so a study
    on a local model can be rerun on the same build."""
    team = debate(tmp_path, **{**TEAM, **{f"{n}.md": NAMED[f"{n}.md"].replace("openai/gpt-5", "ollama/qwen3:8b")
                                          .replace("params:\n  temperature: 0.7\n", "")
                                          for n in ("for", "against", "judge", "fact_checker")}})
    ollama = {"ollama": lambda key, settings: {"qwen3:8b": "sha256:abc123", "llama3.2:latest": "sha256:def"}}
    outcome, _, printed = go(team, VERDICT, listers=ollama)
    assert outcome.status == "completed", outcome.reason
    _, events, _ = read_run(outcome.folder)
    [model] = next(e for e in events if e["event"] == "check")["models"]
    assert (model["model"], model["available"], model["digest"]) == ("ollama/qwen3:8b", True, "sha256:abc123")
    assert read_run(outcome.folder)[0]["keys"] == {}  # a local model needs no key


def test_ollama_members_go_through_the_chat_route(monkeypatch):
    import asyncio
    import litellm
    from teamrunner.litellm_model import LiteLLMModel
    sent = {}

    async def fake(**kwargs):
        sent.update(kwargs)
        raise litellm.BadRequestError("stop here", "ollama_chat", kwargs["model"])
    monkeypatch.setattr(litellm, "acompletion", fake)
    model = LiteLLMModel({}, {"OLLAMA_API_BASE": "http://gpu-box:11434"})
    with pytest.raises(litellm.BadRequestError):
        asyncio.run(model.complete("ollama/gpt-oss:20b", {}, [{"role": "user", "content": "hi"}], None))
    assert sent["model"] == "ollama_chat/gpt-oss:20b" and sent["api_base"] == "http://gpu-box:11434"
    with pytest.raises(litellm.BadRequestError):
        asyncio.run(model.complete("openai/gpt-5", {}, [{"role": "user", "content": "hi"}], None))
    assert sent["model"] == "openai/gpt-5"


def test_run_json_records_the_environment(tmp_path):
    """D14c: run.json records Python, LiteLLM and Deno beside TeamRunner's version."""
    import platform
    from importlib.metadata import version
    team = debate(tmp_path / "debate", **TEAM)
    outcome, _, _ = go(team, VERDICT)
    run, _, _ = read_run(outcome.folder)
    assert run["environment"] == {"python": platform.python_version(),
                                  "litellm": version("litellm"), "deno": version("deno")}


# --- backup models (D135) -------------------------------------------------------

def _listed(team_files, old, new):
    return {k: v.replace(old, new) if k.endswith(".md") else v
            for k, v in team_files.items()}


def test_a_member_runs_on_its_first_available_choice_and_says_so(tmp_path):
    from teamrunner.runs import all_runs, print_list
    team = debate(tmp_path / "debate", **{**TEAM, "for.md": NAMED["for.md"].replace(
        "openai/gpt-5", "openai/gpt-9, openai/gpt-5-mini, openai/gpt-5")})
    outcome, model, printed = go(team, VERDICT)
    assert outcome.status == "completed"
    assert "↪ for runs on openai/gpt-5-mini, its 2nd choice — openai/gpt-9: openai does not serve it" in printed
    assert {m for name, m, *_ in model.calls if name == "for"} == {"openai/gpt-5-mini"}
    assert {m for name, m, *_ in model.calls if name == "judge"} == {"openai/gpt-5"}

    run, events, _ = read_run(outcome.folder)
    assert run["backup_models"] == {"for": "openai/gpt-5-mini"}
    member = next(m for m in events[0]["members"] if m["name"] == "for")
    assert member["model"] == "openai/gpt-5-mini" and member["model_choice"] == 2
    assert member["model_choices"] == ["openai/gpt-9", "openai/gpt-5-mini", "openai/gpt-5"]
    assert member["models_skipped"] == [{"model": "openai/gpt-9", "written": "openai/gpt-9",
                                         "reason": "openai does not serve it"}]
    check = next(e for e in events if e["event"] == "check")
    assert check["backups"][0]["member"] == "for"
    assert "openai/gpt-5-mini" in run["prices"]["models"]  # priced as it ran

    stream = io.StringIO()
    records = all_runs(team / "runs")
    print_list(team, records, records, Console(False, stream))
    assert "(backup model)" in stream.getvalue()


def test_a_missing_key_moves_every_member_to_a_local_model(tmp_path, home):
    (home / ".env").write_text("", encoding="utf-8")
    team = debate(tmp_path, **_listed(TEAM, "openai/gpt-5", "openai/gpt-5, ollama/llama3"))
    listers = {**OPENAI, "ollama": lambda k, s: {"llama3:latest": "sha256:abc"}}
    outcome, model, printed = go(team, VERDICT, listers=listers)
    assert outcome.status == "completed"
    assert "run on ollama/llama3, their 2nd choice — openai/gpt-5: no OPENAI_API_KEY was found" in printed
    assert {m for _, m, *_ in model.calls} == {"ollama/llama3"}
    assert "OPENAI_API_KEY" not in read_run(outcome.folder)[0]["keys"]


def test_a_first_choice_that_is_available_is_used_quietly(tmp_path):
    team = debate(tmp_path, **_listed(TEAM, "openai/gpt-5", "openai/gpt-5, anthropic/claude-x"))
    outcome, model, printed = go(team, VERDICT)  # no Anthropic key, and none needed
    assert outcome.status == "completed" and "↪" not in printed
    run, events, _ = read_run(outcome.folder)
    assert run["backup_models"] == {}
    assert next(m for m in events[0]["members"] if m["name"] == "for")["model_choice"] == 1


def test_when_no_choice_is_available_every_one_is_listed(tmp_path):
    team = debate(tmp_path, **{**TEAM, "for.md": NAMED["for.md"].replace(
        "openai/gpt-5", "openai/gpt-9, ollama/llama3")})
    listers = {**OPENAI, "ollama": lambda k, s: {"mistral:latest": "sha256:abc"}}
    outcome, model, printed = go(team, listers=listers)
    assert outcome.exit_code == 1 and model.calls == []
    assert "openai does not serve `gpt-9`" in printed
    assert "pull it with `ollama pull llama3`" in printed
    assert "↪" not in printed


def test_a_profile_in_the_list_brings_its_own_settings(tmp_path):
    team = debate(tmp_path, **{
        **TEAM, "team.yaml": 'name: debate\ndefault_model: openai/gpt-9, careful.yaml\n'
                             'default_inputs:\n  topic: "Cars downtown"\n',
        "models/careful.yaml": "name: openai/gpt-5-mini\nparams:\n  max_tokens: 300\n",
        "for.md": "---\n---\nfor argues for {{topic}}.\n"})
    outcome, model, _ = go(team, VERDICT)
    assert outcome.status == "completed"
    call = next(c for c in model.calls if c[0] == "for")
    assert call[1] == "openai/gpt-5-mini" and call[2] == {"max_tokens": 300}


def test_runs_started_in_the_same_second_get_their_own_folders(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime
    from teamrunner.runfolder import RunFolder
    moment = datetime(2026, 11, 9, 8, 30, 0)
    with ThreadPoolExecutor(8) as pool:
        folders = list(pool.map(lambda _: RunFolder.create(tmp_path, "team", now=moment).path, range(8)))
    assert len(set(folders)) == 8


@pytest.mark.parametrize("message, permanent", [
    ("You have no credits remaining. Add credits to continue.", True),
    ("You exceeded your current quota, please check your plan and billing details.", True),
    ("Error code: 429 - {'error': {'code': 'insufficient_quota'}}", True),
    ("Rate limit reached for requests per min. Please try again in 1s.", False),
])
def test_out_of_credit_fails_at_once_but_a_rate_limit_is_retried(monkeypatch, message, permanent):
    """Found in the 0.0.6 end-to-end test: an empty OpenAI account was retried for a minute."""
    import asyncio
    import litellm
    from teamrunner.litellm_model import LiteLLMModel
    from teamrunner.model import TransientError

    async def refuse(**kwargs):
        raise litellm.RateLimitError(message, llm_provider="openai", model="gpt-5")
    monkeypatch.setattr(litellm, "acompletion", refuse)
    expected = RuntimeError if permanent else TransientError
    with pytest.raises(expected) as caught:
        asyncio.run(LiteLLMModel({"OPENAI_API_KEY": "sk-test"}, {}).complete(
            "openai/gpt-5", {}, [{"role": "user", "content": "hi"}], None))
    assert isinstance(caught.value, TransientError) is not permanent
    if permanent:
        assert "out of credit" in str(caught.value)


@pytest.mark.parametrize("model", ["teamrunner/demoLLM", "ollama/llama3.2"])
def test_a_free_model_fits_any_ceiling_without_a_known_longest_reply(model):
    """Found in the 0.0.6 end-to-end test: `--max-cost` refused the free demo model."""
    from teamrunner.ceiling import worst_case
    from teamrunner.prices import load_prices
    assert worst_case(model, {}, [{"role": "user", "content": "hi"}], None, None, load_prices()) == 0.0


# --- a team's own git settings run nothing ------------------------------------------------------

def _repo(folder, *config):
    import shutil
    import subprocess
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "team.yaml").write_text("name: t\n", encoding="utf-8")

    def git(*args):
        subprocess.run(["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@example.com",
                        "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)
    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "one")
    for key, value in config:
        git("config", key, value)
    return folder


def test_the_fingerprint_reads_git_and_says_when_files_changed(tmp_path):
    from teamrunner.runfolder import fingerprint
    team = _repo(tmp_path / "team")
    first = fingerprint(team)
    assert len(first["git_commit"]) == 40 and first["git_dirty"] is False
    (team / "team.yaml").write_text("name: changed\n", encoding="utf-8")
    assert fingerprint(team)["git_dirty"] is True


def test_a_team_s_git_settings_run_no_command(tmp_path):
    """A team sent as an archive can carry .git/config; nothing it names is run (rehearsals included)."""
    import sys
    from teamrunner.runfolder import fingerprint
    proof = tmp_path / "ran"
    hook = tmp_path / "hook.py"
    hook.write_text(f"import pathlib, sys\npathlib.Path({str(proof)!r}).write_text('ran')\n"
                    "sys.stdout.write(sys.stdin.read())\n", encoding="utf-8")
    command = f'"{Path(sys.executable).as_posix()}" "{hook.as_posix()}"'

    monitored = _repo(tmp_path / "monitored", ("core.fsmonitor", command))
    assert len(fingerprint(monitored)["git_commit"]) == 40

    filtered = _repo(tmp_path / "filtered", ("filter.x.clean", command), ("filter.x.process", command))
    (filtered / ".gitattributes").write_text("* filter=x\n", encoding="utf-8")
    (filtered / "team.yaml").write_text("name: changed, so git compares it\n", encoding="utf-8")
    found = fingerprint(filtered)
    assert len(found["git_commit"]) == 40 and "git_dirty" not in found  # not asked, not guessed

    included = _repo(tmp_path / "included", ("include.path", "../elsewhere"))
    assert "git_dirty" not in fingerprint(included)
    assert not proof.exists()


class Echoing(FakeModel):
    """A provider whose errors repeat the key, as some do in a URL or a header dump."""

    def __init__(self, error):
        super().__init__()
        self.error = error

    async def complete(self, *args):
        raise self.error("401 for https://api.example/v1?key=sk-test-from-key-file: "
                         "Authorization: Bearer sk-test-from-key-file")


@pytest.mark.parametrize("error", [TransientError, ValueError])
def test_a_key_in_a_provider_s_error_never_reaches_the_run_folder(tmp_path, error):
    """Found in the 2026-11-16 review (S8): a model's error went into events.jsonl,
    record.json and output.md as it was."""
    outcome, _, printed = go(debate(tmp_path / "debate", **TEAM), model=Echoing(error))
    assert outcome.status == "failed"
    written = [(outcome.folder / name).read_text(encoding="utf-8")
               for name in ("events.jsonl", "record.json", "output.md")]
    for text in [*written, printed, outcome.reason]:
        assert "sk-test-from-key-file" not in text
    assert all("[key hidden]" in text for text in written)


def test_hide_reaches_every_string_and_leaves_short_values_alone():
    from teamrunner.keys import hide
    secret = "sk-abcdefgh12345"
    event = {"a": f"x {secret} y", "b": [{"c": secret}, 3, None], "d": ("t", secret)}
    assert hide(event, [secret, "", "ab"]) == {"a": "x [key hidden] y", "b": [{"c": "[key hidden]"}, 3, None],
                                               "d": ["t", "[key hidden]"]}
    assert hide("ab is a word", ["ab"]) == "ab is a word"


@pytest.mark.skipif(os.name == "nt", reason="file modes are POSIX")
def test_the_key_file_is_private_from_the_start_and_keeps_one_value_per_key(tmp_path, monkeypatch):
    """Found in the 2026-11-16 review (S10): the file was written, then made private; and saving
    a key again left the old value in the file."""
    from teamrunner.keys import read_env, save_key
    made = []
    real_open = os.open

    def watching(path, flags, mode=0o777, *args, **kw):
        made.append(mode)
        return real_open(path, flags, mode, *args, **kw)

    monkeypatch.setattr(os, "open", watching)
    keyfile = tmp_path / "home2" / ".env"
    save_key(keyfile, "OPENAI_API_KEY", "sk-old-value-1234")
    save_key(keyfile, "BRAVE_API_KEY", "brave-value-5678")
    save_key(keyfile, "OPENAI_API_KEY", "sk-new-value-1234")
    assert made and all(mode == 0o600 for mode in made)
    assert keyfile.stat().st_mode & 0o777 == 0o600
    text = keyfile.read_text(encoding="utf-8")
    assert "sk-old-value-1234" not in text and text.count("OPENAI_API_KEY") == 1
    assert read_env(keyfile) == {"BRAVE_API_KEY": "brave-value-5678", "OPENAI_API_KEY": "sk-new-value-1234"}


def test_a_team_s_name_reaches_the_run_folder_as_a_plain_word(tmp_path):
    """Found in the 2026-11-16 review (S10): only `/` was replaced, so on Windows `..\\x`
    could land outside runs/, and `:` `*` `?` stopped the run with a traceback."""
    from datetime import datetime
    from teamrunner.runfolder import RunFolder, folder_word
    assert folder_word("..\\..\\x") == "x" and folder_word("a: b*?/c") == "a_b_c" and folder_word("???") == "team"
    assert len(folder_word("x" * 300)) == 60
    run = RunFolder.create(tmp_path, "..\\..\\escape: now?", now=datetime(2026, 11, 16, 12, 0, 0))
    run.close()
    assert run.path.parent == tmp_path / "runs" and run.path.name == "2026-11-16_120000_escape_now"



@pytest.mark.skipif(os.name == "nt", reason="file modes are POSIX")
def test_a_key_file_made_by_hand_is_made_private(tmp_path, home):
    """Found in the 2026-11-16 review (D28): a key file made by hand stayed readable by everyone."""
    from teamrunner.keys import find_keys
    (home / ".env").chmod(0o644)
    keys = find_keys(["OPENAI_API_KEY"], None, None)
    assert (home / ".env").stat().st_mode & 0o777 == 0o600
    assert any("it is now yours alone" in n for n in keys.notes)
    assert not any("yours alone" in n for n in find_keys(["OPENAI_API_KEY"], None, None).notes)  # once



def test_a_provider_failure_does_not_make_the_cost_unknown(tmp_path):
    """Found in the 2026-11-16 review (C4): one retried provider failure made the run's cost `$?`."""
    outcome, _, _ = go(debate(tmp_path / "a", **TEAM), VERDICT, model=Flaky(1, VERDICT))
    run, events, _ = read_run(outcome.folder)
    assert outcome.status == "completed" and any(e["event"] == "retry" for e in events)
    assert run["cost_usd"] is not None and run["cost_usd"] == run["cost_known_usd"]
    assert run["provider_failures"] == 1


def test_an_unexpected_error_is_one_plain_line(tmp_path, monkeypatch, capsys, home):
    """C5's last resort: a fault TeamRunner did not foresee prints one line, never a traceback,
    and keeps the details, with any key hidden, for a bug report."""
    from teamrunner import cli

    def broken(args):
        raise RuntimeError("something odd with sk-test-from-key-file in it")

    monkeypatch.setattr(cli, "cmd_validate", broken)
    team = debate(tmp_path / "debate", **TEAM)
    assert cli.main(["validate", str(team)]) == 3
    err = capsys.readouterr().err
    assert "TeamRunner hit a problem it did not expect: something odd with [key hidden]" in err
    assert "Traceback" not in err and "sk-test-from-key-file" not in err
    kept = (home / "last-error.txt").read_text(encoding="utf-8")
    assert "Traceback" in kept and "sk-test-from-key-file" not in kept


def test_runs_lists_a_run_whose_record_has_no_turn_count(tmp_path, capsys):
    """C10: `"turns": null` in a record made `teamrunner runs` crash."""
    from teamrunner.cli import main
    team = debate(tmp_path / "debate", **TEAM)
    outcome, _, _ = go(team, VERDICT)
    record = outcome.folder / "record.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data["turns"] = None
    record.write_text(json.dumps(data), encoding="utf-8")
    assert main(["runs", str(team)]) == 0
    assert "  ? turns" in capsys.readouterr().out


def test_max_cost_must_be_an_ordinary_amount(capsys):
    """C10: `--max-cost inf` switched the ceiling off."""
    from teamrunner.cli import main
    for given in ("inf", "nan", "Infinity"):
        with pytest.raises(SystemExit):
            main(["run", ".", "--max-cost", given])
        assert "give an amount in US dollars above 0" in capsys.readouterr().err


def test_a_run_s_ceiling_counts_its_tool_calls(tmp_path):
    """Found in the 2026-11-16 review (C9): a single run's --max-cost did not count web searches."""
    import asyncio as aio
    from teamrunner import builtin_tools
    from teamrunner.build import load_team
    from teamrunner.builtin_tools import Answer, Context, Tool
    from teamrunner.ceiling import Ceiling
    from teamrunner.desk import Desk, TurnCard
    from teamrunner.events import Log
    web = ("---\nkind: toolkeeper\ntools:\n  web_search_brave: {}\naccess:\n  judge: standard\n---\nI search.\n")
    team, report = load_team(debate(tmp_path / "debate", **{**TEAM, "web.md": web}))
    assert team is not None, report

    async def search(args, guardrails, ctx):
        return Answer("results", service="brave", cost_usd=builtin_tools.BRAVE_PRICE_USD)

    original = builtin_tools.TOOLS["web_search_brave"]
    builtin_tools.TOOLS["web_search_brave"] = Tool("web_search_brave", "Searches.", original.arguments, search)
    try:
        ceiling = Ceiling(0.012)  # room for two searches at half a cent, not three
        ctx = Context(tmp_path / "inputs", tmp_path / "work", {}, tmp_path, ceiling=ceiling)
        desk, card = Desk(team, Log(), ctx, lambda m: {}), TurnCard("judge")
        said = [aio.run(desk.ask(team.members["web"], "judge", {"query": "q"}, card, mandatory=False))[0]
                for _ in range(3)]
    finally:
        builtin_tools.TOOLS["web_search_brave"] = original
    assert said[:2] == ["results", "results"] and said[2] == "Refused: the run's cost ceiling is reached."
    assert abs(ceiling.spent - 0.01) < 1e-9 and ceiling.reserved == 0
    assert card.by_toolkeeper["web"].used == 2  # the refused call does not use up the allowance


def test_an_unknown_price_says_how_to_add_it(tmp_path, capsys):
    """A model with no list price: before the run, and under `runs` where its cost shows `$?`,
    the person is told the cost is unknown and how to add the price to prices.yaml."""
    from teamrunner.cli import main
    team = debate(tmp_path / "debate", **TEAM)
    for path in [*team.glob("*.md"), team / "team.yaml"]:
        path.write_text(path.read_text(encoding="utf-8").replace("openai/gpt-5", "openai/gpt-zz"), encoding="utf-8")
    fake = FakeModel(VERDICT)

    async def unpriced(*args):
        reply = await FakeModel.complete(fake, *args)
        return Reply(reply.text, reply.model_resolved, reply.usage, None, reply.latency_ms)

    fake.complete = unpriced
    outcome, _, printed = go(team, VERDICT, model=fake, listers={"openai": lambda key, settings: {"gpt-zz"}})
    assert outcome.status == "completed"
    said = "openai/gpt-zz has no list price, so its cost shows as unknown ($?)"
    assert printed.count(said) == 2 and "prices.yaml" in printed  # before the run, and at its end
    assert "`models: {openai/gpt-zz: {input: …, output: …}}`" in printed
    capsys.readouterr()
    assert main(["runs", str(team)]) == 0
    listed = capsys.readouterr().out
    assert "$?" in listed and "! $?: " + said in listed


def test_retry_note_is_shown_on_a_terminal_then_wiped_and_never_kept_elsewhere():
    import io
    from teamrunner.console import Console

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    retry = {"event": "retry", "kind": "provider", "instance_path": "debate/for", "attempt": 1,
             "reason": "TeamRunner demo: the demo model is temporarily busy", "wait_ms": 8000}
    screen = Terminal()
    console = Console(stream=screen)
    console.on_event(retry)
    assert "for: TeamRunner demo: the demo model is temporarily busy · try 2, in 8s" in screen.getvalue()
    assert not screen.getvalue().endswith("\n")
    console.say("for   A short argument.")
    shown = screen.getvalue()
    assert shown.endswith("\r\033[Kfor   A short argument.\n")  # the note is wiped, then the turn

    piped = io.StringIO()
    Console(stream=piped).on_event(retry)
    assert piped.getvalue() == ""
    kept = io.StringIO()
    Console(verbose=True, stream=kept).on_event(retry)
    assert "trying again in 8s" in kept.getvalue() and kept.getvalue().endswith("\n")


@pytest.mark.parametrize("body", ["Write this member’s instructions here.\n", "\n"])
def test_unwritten_instructions_stop_a_real_run_but_not_a_rehearsal(tmp_path, body):
    starter = '---\ndescription: ""\nmodel: openai/gpt-5\n---\n' + body
    team = debate(tmp_path, **{**TEAM, "for.md": starter})
    outcome, model, printed = go(team, answers=VERDICT)
    assert outcome.exit_code == 1 and model.calls == []
    assert "for has no instructions yet" in printed
    rehearsal = run_team(team, Options(rehearse=True, quiet=True))
    assert rehearsal.exit_code == 0
