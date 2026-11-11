"""Experiments, step 2 (D138): read, build, plan — nothing spent."""

import io
from pathlib import Path

import pytest

from teamrunner.console import Console
from teamrunner.experiment import (CONDITIONS_DIR, MARKER, NotAnExperiment, build, plan_runs,
                                   read_experiment, round_order, run_seed, study_hash)
from teamrunner.inputs import read_task_file
from teamrunner.loader import find_top
from teamrunner.plan import plan
from teamrunner.prices import load_prices

TEAM = {
    "team.yaml": "name: review\ndefault_model: openai/gpt-5\n"
                 "default_inputs:\n  code: print(1)\n",
    "chair.md": "# two rounds\nrounds(*2) -> alice:report\n\nrounds = alice -> bob\n",
    "alice.md": "---\ndescription: lead\noutput:\n  verdict: [ok, bad]\nparams:\n  max_tokens: 800\n---\n"
                "You are Alice. Review {{code}}.\n",
    "bob.md": "---\ndescription: second\n---\nYou are Bob. Review {{code}}.\n",
}


def write(folder: Path, files: dict) -> Path:
    for name, text in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder


def experiment(tmp_path, front: str, files: dict | None = None, body: str = "") -> Path:
    write(tmp_path / "review", TEAM)
    exp = tmp_path / "exp"
    write(exp, {"experiment.md": f"---\n{front}---\n{body}", **(files or {})})
    return exp


SIMPLE = """\
name: simple
conditions:
  flat: ../review
  led:
    from: ../review
    change:
      chair: file:variants/chair.md
      alice.prompt: file:variants/alice.md
      bob.model: anthropic/claude-sonnet-4-5
replications: 3
seed: 7
limits: {max_cost_usd: 10}
"""
VARIANTS = {"variants/chair.md": "alice:kickoff -> rounds(*2) -> alice:report\n\nrounds = bob -> alice\n",
            "variants/alice.md": "You lead. Review {{code}}.\n"}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "prices.yaml").write_text(
        "models:\n  openai/gpt-5: {input: 1.0, output: 10.0}\n"
        "  anthropic/claude-sonnet-4-5: {input: 3.0, output: 15.0}\n", encoding="utf-8")
    monkeypatch.setenv("TEAMRUNNER_HOME", str(home))
    return home


def built(exp: Path):
    e = build(read_experiment(exp))
    return e, {c.name: c for c in e.conditions}


def messages(e) -> str:
    return "\n".join([f"{p.message} {p.fix or ''}" for p in e.problems.errors] + [w for _, _, w in e.later])


# --- reading -----------------------------------------------------------------------

def test_the_simple_form(tmp_path):
    e, by = built(experiment(tmp_path, SIMPLE, VARIANTS, body="## Question\nWhy?\n"))
    assert e.ok, messages(e)
    assert e.name == "simple" and e.replications == 3 and e.seed == 7 and e.body == "## Question\nWhy?"
    assert [c.name for c in e.conditions] == ["flat", "led"]
    assert by["flat"].folder == (tmp_path / "review").resolve() and not by["flat"].generated
    assert by["led"].folder == tmp_path / "exp" / CONDITIONS_DIR / "led" and by["led"].generated


def test_the_full_form_is_the_factorial(tmp_path):
    front = """\
name: full
base: ../review
factors:
  structure:
    - label: flat
      change: {}
    - label: led
      change: {chair: file:variants/chair.md}
  model:
    - label: gpt
      change: {}
    - label: claude
      change: {bob.model: anthropic/claude-sonnet-4-5}
replications: 2
seed: 1
"""
    e, by = built(experiment(tmp_path, front, VARIANTS))
    assert e.ok, messages(e)
    assert list(by) == ["flat+gpt", "flat+claude", "led+gpt", "led+claude"]
    assert by["led+claude"].levels == {"structure": "led", "model": "claude"}
    assert by["led+claude"].changes == {"chair": "file:variants/chair.md", "bob.model": "anthropic/claude-sonnet-4-5"}
    assert not by["flat+gpt"].generated  # no changes: the base, as written
    assert e.factors == {"structure": ["flat", "led"], "model": ["gpt", "claude"]}


@pytest.mark.parametrize("front, said", [
    ("name: x\nreplications: 3\nseed: 1\n", "needs `conditions:`"),
    ("name: x\nconditions: {a: ../review}\nreplications: 1\nseed: 1\n", "one run per condition"),
    ("name: x\nconditions: {a: ../review}\nreplications: 3\n", "`seed:` is missing"),
    ("name: x\nconditions: {a: ../nowhere}\nreplications: 3\nseed: 1\n", "there is no folder"),
    ("name: x\nconditions: {a: ../review}\nbase: ../review\nreplications: 3\nseed: 1\n", "use one form"),
    ("name: x\nconditions: {a: {from: ../review, patch: {}}}\nreplications: 3\nseed: 1\n", "did you mean `change`"),
    ("name: x\nconditions: {a: ../review}\nreplications: 3\nseed: 1\nmetrics: [metrics/nope]\n", "no metric file"),
    ("name: x\nconditions: {a: ../review}\nreplications: 3\nseed: 1\nlimits: {max_cost: 3}\n", "did you mean `max_cost_usd`"),
    ("name: x\nconditions: {a: ../review}\nreplications: 3\nseed: 1\nsed: 2\n", "did you mean `seed`"),
])
def test_experiment_md_problems_name_the_line(tmp_path, front, said):
    e = read_experiment(experiment(tmp_path, front))
    assert said in messages(e)
    assert all(p.file.name == "experiment.md" for p in e.problems.errors)


def test_one_run_each_when_asked(tmp_path):
    e = read_experiment(experiment(tmp_path, "name: x\nconditions: {a: ../review}\nreplications: 1\n"
                                               "allow_single_run: true\nseed: 1\n"))
    assert not e.problems.errors and e.replications == 1


def test_the_old_file_is_named(tmp_path):
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "experiment.yaml").write_text("name: x\n", encoding="utf-8")
    with pytest.raises(NotAnExperiment, match="experiment.md"):
        read_experiment(tmp_path / "old")


def test_a_task_file_may_be_labelled(tmp_path):
    path = write(tmp_path, {"t.yaml": "name: t\ndescription: a task\ninputs:\n  code: x\n"}) / "t.yaml"
    assert read_task_file(path) == {"code": "x"}


# --- building -------------------------------------------------------------------------

def test_changes_build_a_folder_and_leave_the_base_alone(tmp_path):
    exp = experiment(tmp_path, SIMPLE, VARIANTS)
    before = {p.name: p.read_bytes() for p in (tmp_path / "review").iterdir()}
    e, by = built(exp)
    led = by["led"].folder
    assert (led / MARKER).is_file()
    assert (led / "chair.md").read_text() == VARIANTS["variants/chair.md"]
    alice = (led / "alice.md").read_text()
    assert alice.startswith("---\ndescription: lead\n") and alice.endswith("You lead. Review {{code}}.\n")
    assert "model: anthropic/claude-sonnet-4-5" in (led / "bob.md").read_text()
    assert {p.name: p.read_bytes() for p in (tmp_path / "review").iterdir()} == before  # no ids written either
    assert "id:" not in (led / "bob.md").read_text()


def test_whole_members_added_and_removed_and_fields_nested(tmp_path):
    front = SIMPLE.replace("""      chair: file:variants/chair.md
      alice.prompt: file:variants/alice.md
      bob.model: anthropic/claude-sonnet-4-5""", """      bob: remove
      carol: file:variants/carol.md
      chair: remove
      alice.params.temperature: 0.2""")
    files = {"variants/carol.md": "---\ndescription: new\n---\nYou are Carol. Review {{code}}.\n"}
    e, by = built(experiment(tmp_path, front, files))
    led = by["led"].folder
    assert not (led / "bob.md").exists() and (led / "carol.md").is_file()
    assert "max_tokens: 800\n  temperature: 0.2" in (led / "alice.md").read_text()
    assert e.ok, messages(e)  # round-robin of whoever is in the folder: alice and carol


@pytest.mark.parametrize("change, said", [
    ("emmma.prompt: file:variants/alice.md", "there is no member `emmma`"),
    ("alic.prompt: file:variants/alice.md", "did you mean `alice`"),
    ("alice.modle: openai/gpt-5", "did you mean `model`"),
    ("bob.prompt: file:variants/alise.md", "did you mean `file:variants/alice.md`"),
    ("dave: remove", "no dave.md to remove"),
    ("alice.model: remove", "`remove` takes out a whole file"),
    ("chair: openai/gpt-5", "is a whole file"),
    ("alice.id: 01ABC", "says who it is"),
])
def test_a_change_to_nothing_is_an_error_with_the_closest_match(tmp_path, change, said):
    front = SIMPLE.replace("      chair: file:variants/chair.md\n", f"      {change}\n")
    e, _ = built(experiment(tmp_path, front, VARIANTS))
    assert said in messages(e)
    assert not (tmp_path / "exp" / CONDITIONS_DIR / "led").exists()  # nothing half-built is left


def test_each_condition_is_validated_as_a_team(tmp_path):
    front = SIMPLE.replace("      chair: file:variants/chair.md\n", "      manager: file:variants/chair.md\n")
    e, _ = built(experiment(tmp_path, front, VARIANTS))
    assert "both `chair.md` and `manager.md`" in messages(e)


def test_a_bad_variant_is_reported_at_its_file_and_line(tmp_path):
    files = {**VARIANTS, "variants/chair.md": "rounds(*2) -> alise\n\nrounds = alice -> bob\n"}
    e, _ = built(experiment(tmp_path, SIMPLE, files))
    bad = [p for p in e.problems.errors if "alise" in p.message]
    assert bad and bad[0].file.parts[-3:] == (CONDITIONS_DIR, "led", "chair.md") and bad[0].line == 1


def test_rebuilt_every_time_and_a_hand_made_folder_is_never_touched(tmp_path):
    exp = experiment(tmp_path, SIMPLE, VARIANTS)
    _, by = built(exp)
    (by["led"].folder / "bob.md").write_text("edited by hand", encoding="utf-8")
    _, by = built(exp)
    assert "edited by hand" not in (by["led"].folder / "bob.md").read_text()

    mine = exp / CONDITIONS_DIR / "led"
    import shutil
    shutil.rmtree(mine)
    write(mine, {"notes.txt": "mine"})
    e, _ = built(exp)
    assert "was not built by TeamRunner" in messages(e)
    assert (mine / "notes.txt").read_text() == "mine" and not (mine / MARKER).exists()


def test_large_data_files_are_hard_linked(tmp_path):
    exp = experiment(tmp_path, SIMPLE, VARIANTS)
    data = tmp_path / "review" / "data.bin"
    data.write_bytes(b"x" * (1 << 20))
    _, by = built(exp)
    assert (by["led"].folder / "data.bin").stat().st_ino == data.stat().st_ino
    assert (by["led"].folder / "bob.md").stat().st_ino != (tmp_path / "review" / "bob.md").stat().st_ino


def test_runs_and_hidden_files_stay_behind(tmp_path):
    exp = experiment(tmp_path, SIMPLE, VARIANTS)
    write(tmp_path / "review", {"runs/old/record.json": "{}", ".env": "OPENAI_API_KEY=x"})
    _, by = built(exp)
    assert not (by["led"].folder / "runs").exists() and not (by["led"].folder / ".env").exists()


# --- what 0.3 brings is said plainly ------------------------------------------------------

def test_whiteboard_changes_and_subteams_need_0_3(tmp_path):
    front = SIMPLE.replace("      chair: file:variants/chair.md\n",
                           "      whiteboard.said.bob.read_by: everyone\n")
    e, _ = built(experiment(tmp_path, front, VARIANTS))
    assert not e.ok and not e.problems.errors
    assert any("changes the whiteboard" in w and "0.3" in w for _, _, w in e.later)

    write(tmp_path / "review", {"side/team.yaml": "name: side\n", "side/pro.md": "---\n---\nPro.\n"})
    front = SIMPLE.replace("      chair: file:variants/chair.md\n", "      side/pro.model: openai/gpt-5\n")
    e, _ = built(experiment(tmp_path, front, VARIANTS))
    assert any("changes a subteam" in w for _, _, w in e.later)


# --- the study's hash ----------------------------------------------------------------------

def test_the_study_hash_ignores_what_d138_leaves_out(tmp_path):
    exp = experiment(tmp_path, SIMPLE, VARIANTS, body="## Question\nWhy?\n")
    _, by = built(exp)
    first = {n: c.study_hash for n, c in by.items()}
    assert first["flat"] != first["led"] and all(h.startswith("sha256:") for h in first.values())

    # body, name, limits, replications; comments, spacing and ids in the team
    text = (exp / "experiment.md").read_text().replace("name: simple", "name: renamed") \
        .replace("replications: 3", "replications: 5").replace("{max_cost_usd: 10}", "{max_cost_usd: 99}")
    (exp / "experiment.md").write_text(text.replace("Why?", "Why not?"), encoding="utf-8")
    team = tmp_path / "review"
    (team / "team.yaml").write_text("# a comment\nname:   review\nid: 01M71T5G0PRY9E7FHN9PSHMY6J\ndefault_model: openai/gpt-5\n"
                                    "default_inputs:\n  code: print(1)\n", encoding="utf-8")
    (team / "bob.md").write_text("---\nid: 01M71T5G0PRY9E7FHN9PSHMY6K\ndescription: second   # who\n---\n\nYou are Bob. Review {{code}}.\n\n",
                                 encoding="utf-8")
    (team / "chair.md").write_text("rounds(*2)   ->   alice:report   # end\n\n# rounds\nrounds = alice -> bob\n",
                                   encoding="utf-8")
    e, by = built(exp)
    assert e.ok, messages(e)
    assert {n: c.study_hash for n, c in by.items()} == first

    (team / "bob.md").write_text("---\ndescription: second\n---\nYou are Robert. Review {{code}}.\n", encoding="utf-8")
    _, by = built(exp)
    assert by["flat"].study_hash != first["flat"]


def test_the_study_hash_counts_inputs_and_seed(tmp_path):
    top = write(tmp_path / "review", TEAM)
    base = study_hash(find_top(top), {"code": "a"}, 7)
    assert study_hash(top, {"code": "b"}, 7) != base
    assert study_hash(top, {"code": "a"}, 8) != base
    assert study_hash(top, {"code": "a"}, 7) == base


def test_the_task_gives_the_inputs(tmp_path):
    front = SIMPLE + "task: tasks/t\n"
    exp = experiment(tmp_path, front, {**VARIANTS, "tasks/t.yaml": "name: t\ninputs:\n  code: print(2)\n"})
    e, by = built(exp)
    assert by["flat"].inputs == {"code": "print(2)"}
    bad = experiment(tmp_path, front, {**VARIANTS, "tasks/t.yaml": "inputs:\n  cod: x\n"})
    e, _ = built(bad)
    assert "the task gives `cod`" in messages(e)


# --- order and seeds: pinned, so a later version never changes a study's plan ------------

def test_seeds_and_rounds_are_pinned():
    assert run_seed(7, "visible_leader", 7) == run_seed(7, "visible_leader", 7)
    assert [run_seed(7, "flat", r) for r in (1, 2, 3)] == PINNED_SEEDS
    assert [round_order(7, ["a", "b", "c"], r) for r in (1, 2, 3)] == PINNED_ROUNDS
    assert run_seed(7, "flat", 1) != run_seed(7, "flat", 2) != run_seed(8, "flat", 2)


PINNED_SEEDS = [776214560, 3936067356, 1429623395]
PINNED_ROUNDS = [["b", "c", "a"], ["a", "c", "b"], ["c", "a", "b"]]


def test_every_round_holds_every_condition_once(tmp_path):
    e, _ = built(experiment(tmp_path, SIMPLE.replace("replications: 3", "replications: 20"), VARIANTS))
    slots = plan_runs(e)
    assert len(slots) == 40
    for r in range(1, 21):
        row = [s for s in slots if s.round == r]
        assert sorted(s.condition for s in row) == ["flat", "led"]
        assert all(s.replicate == r and s.seed == run_seed(7, s.condition, r) for s in row)
    assert len({tuple(s.condition for s in slots if s.round == r) for r in range(1, 21)}) == 2  # both orders occur


# --- --plan ---------------------------------------------------------------------------------

def show(exp: Path) -> tuple[int, str]:
    stream = io.StringIO()
    code = plan(exp, Console(False, stream), prices=load_prices(), walks=2)
    return code, stream.getvalue()


def test_the_plan(tmp_path):
    code, text = show(experiment(tmp_path, SIMPLE, VARIANTS))
    assert code == 0, text
    assert "simple · 2 conditions × 3 replications = 6 runs · seed 7" in text
    assert "chair.md" in text and "replaced by variants/chair.md (flow changed)" in text
    assert "alice.md" in text and "prompt from variants/alice.md" in text
    assert "bob.md" in text and "`model` added: anthropic/claude-sonnet-4-5" in text
    assert "round   1" in text and "round   3" in text
    assert "Estimated cost ≈ $" in text and "of max ($10.00)" in text
    assert "at most ≈ $" in text
    assert "bob (openai/gpt-5): no max_tokens, so LiteLLM's maximum is used: 128,000 tokens a reply" in text
    assert "Nothing was run and nothing was spent." in text


def test_the_plan_labels_added_and_removed(tmp_path):
    front = SIMPLE.replace("""      chair: file:variants/chair.md
      alice.prompt: file:variants/alice.md
      bob.model: anthropic/claude-sonnet-4-5""", "      chair: remove\n      bob: remove\n      carol: file:variants/carol.md")
    files = {"variants/carol.md": "---\ndescription: new\n---\nYou are Carol. Review {{code}}.\n"}
    code, text = show(experiment(tmp_path, front, files))
    assert code == 0, text
    assert "bob.md                   removed" in text
    assert "carol.md                 added    from variants/carol.md" in text


def test_the_plan_says_plainly_what_needs_0_3(tmp_path):
    front = SIMPLE.replace("      chair: file:variants/chair.md\n", "      whiteboard.said.bob.read_by: everyone\n")
    code, text = show(experiment(tmp_path, front, VARIANTS))
    assert code == 1
    assert "needs TeamRunner 0.3, so it cannot run yet" in text and "experiment.md:" in text


# --- a model profile as a change (D93, D138 (3)) -------------------------------------------

PROFILED = """\
name: profiled
conditions:
  {key}
replications: 2
seed: 7
"""


def test_a_condition_can_replace_a_model_profile(tmp_path):
    team = {**TEAM, "team.yaml": TEAM["team.yaml"].replace("default_model: openai/gpt-5", "default_model: fast.yaml"),
            "models/fast.yaml": "name: openai/gpt-5\nparams:\n  temperature: 0.9\n"}
    for written in ("models/fast", "models/fast.yaml"):
        root = tmp_path / written.replace("/", "_")
        write(root / "review", team)
        front = PROFILED.format(key=f"cheap:\n    from: ../review\n    change:\n      {written}: file:variants/fast.yaml")
        exp = write(root / "exp", {"experiment.md": f"---\n{front}---\n",
                                   "variants/fast.yaml": "name: anthropic/claude-sonnet-4-5\n"})
        e, cond = built(exp)
        assert not e.problems.errors, messages(e)
        folder = cond["cheap"].folder
        assert (folder / "models" / "fast.yaml").read_text() == "name: anthropic/claude-sonnet-4-5\n"
        assert not (folder / "models" / "fast.md").exists()  # never a stray member file


def test_a_profile_change_is_checked(tmp_path):
    team = {**TEAM, "models/fast.yaml": "name: openai/gpt-5\n"}
    write(tmp_path / "review", team)
    front = PROFILED.format(key="cheap:\n    from: ../review\n    change:\n      models/fats: file:variants/x.yaml\n"
                            "      models/fast.params: {temperature: 0}")
    exp = write(tmp_path / "exp", {"experiment.md": f"---\n{front}---\n", "variants/x.yaml": "name: openai/gpt-5\n"})
    e, _ = built(exp)
    text = messages(e)
    assert "there is no model profile `fats` in models/" in text and "did you mean `models/fast`?" in text
    assert "a model profile is a whole file" in text
