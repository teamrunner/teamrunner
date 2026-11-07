import io
import json
import os
from pathlib import Path

import pytest

from teamrunner.console import Console
from teamrunner.keys import PROVIDER_KEYS, find_keys, read_env
from teamrunner.model import TransientError
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
    return home


def go(folder, answers=None, options=None, listers=OPENAI, model=None):
    fake = model or FakeModel(answers)
    stream = io.StringIO()
    outcome = run_team(folder, options or Options(), model_factory=lambda keys, settings: fake,
                       listers=listers, model_data=lambda m: {}, waits=(0, 0, 0),
                       console=None if (options and options.quiet) else Console(False, stream))
    return outcome, fake, stream.getvalue()


def read_run(folder):
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
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
    reply = asyncio.run(model.complete("openai/gpt-5", {"mock_response": text},
                                       [{"role": "user", "content": "hi"}], schema))
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


def test_validate_online_runs_the_check_without_a_run(tmp_path, monkeypatch, capsys):
    from teamrunner import checks
    from teamrunner.cli import main
    monkeypatch.setitem(checks.LISTERS, "openai", lambda key, settings: {"gpt-5"})
    monkeypatch.setattr(checks, "litellm_data", lambda m: {})
    team = debate(tmp_path, **{**TEAM, "against.md": NAMED["against.md"].replace("params:\n  temperature: 0.7\n", "")})
    assert main(["validate", str(team), "--online"]) == 0
    assert "✓ 1 model · 1 key" in capsys.readouterr().out
    assert not (team / "runs").exists()

    monkeypatch.setitem(checks.LISTERS, "openai", lambda key, settings: {"gpt-4o"})
    assert main(["validate", str(team), "--online", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "does not serve `gpt-5`" in data["online"]["problems"][0]["wrong"]


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
        asyncio.run(model.complete("openai/gpt-5", {"api_base": "http://127.0.0.1:9/v1"},
                                   [{"role": "user", "content": "hi"}], None, tools))
    assert "No module named" not in str(caught.value)

    async def broken(**kwargs):
        try:
            raise ModuleNotFoundError("No module named 'mcp'")
        except ModuleNotFoundError as e:
            raise litellm.APIConnectionError("OpenAIException - No module named 'mcp'", "openai", "gpt-5") from e
    monkeypatch.setattr(litellm, "acompletion", broken)
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
