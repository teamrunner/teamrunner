# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""+ Team → "Describe it" in the browser page (D162): a model writes the team.

The same `describe()` as `teamrunner new --describe`, run on a thread so the
page can show which try it is on. One at a time. Keys stay on this side: the
page sees the model names offered, never a key.
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock, Thread

from . import api
from ..new import NewError


class Describing:
    def __init__(self, root: Path, model_factory=None):
        self.root = root
        self.model_factory = model_factory  # tests give a stand-in model
        self.lock = Lock()
        self.state: dict = {"state": "idle"}
        self.thread: Thread | None = None

    def models(self) -> dict:
        from ..describe import offered_models
        return {"models": offered_models()}

    def status(self) -> dict:
        with self.lock:
            return dict(self.state)

    def start(self, data: dict) -> dict:
        from ..describe import check_description, offered_models
        from ..keys import PROVIDER_KEYS, extra_settings, find_keys
        with self.lock:
            if self.state["state"] == "writing":
                raise api.Conflict("A team is already being written. Wait for it to finish.")
        path = api.new_team_place(self.root, str(data.get("path", "")))
        if path.exists():
            raise api.Conflict(f"{path.relative_to(self.root).as_posix()} already exists; choose another name.")
        description = check_description(data.get("description", ""))
        model = str(data.get("model", ""))
        if model not in offered_models():
            raise ValueError("Choose one of the models whose key is set.")
        name = PROVIDER_KEYS.get(model.split("/", 1)[0])
        keys = find_keys([name] if name else [], None, None)
        if keys.missing:
            raise ValueError(f"{model} needs {name}; add it with Keys.")
        if self.model_factory is None:
            from ..litellm_model import LiteLLMModel
            writer = LiteLLMModel(keys.values, extra_settings())
        else:
            writer = self.model_factory(keys.values, {})
        shown = path.relative_to(self.root).as_posix()
        with self.lock:
            self.state = {"state": "writing", "path": shown, "model": model, "round": 0}
        self.thread = Thread(target=self._work, args=(path, description, model, writer,
                                                      list(keys.values.values())), daemon=True)
        self.thread.start()
        return self.status()

    def _round(self, n: int):
        from ..describe import MAX_ROUNDS
        with self.lock:
            self.state.update(round=n, of=MAX_ROUNDS)

    def _work(self, path: Path, description: str, model: str, writer, secrets: list[str]):
        from ..build import load_team
        from ..describe import describe
        from ..explain import explain
        from ..keys import hide
        done: dict
        try:
            result = describe(path, description, model, writer, on_round=self._round)
            cost = result.cost_usd
            done = {"cost": cost, "notes": result.notes, "round": result.rounds}
            if result.ok:
                team, _ = load_team(result.folder)
                done.update(state="done", file=(path / "team.yaml").relative_to(self.root).as_posix(),
                            explain=explain(team) if team else "")
            else:
                problems = [{"message": p} for p in result.problems] if result.report is None else [
                    {"file": p.file.relative_to(self.root).as_posix(), "line": p.line,
                     "message": p.message, "fix": p.fix}
                    for p in result.report.problems if p.level == "error"]
                kept = path.exists()
                done.update(state="failed", problems=problems, kept=kept,
                            file=(path / "team.yaml").relative_to(self.root).as_posix() if kept else None,
                            error=f"The model could not write a team that validates in {result.rounds} tries.")
        except NewError as e:
            done = {"state": "failed", "error": hide(str(e), secrets), "problems": [], "kept": False}
        except Exception as e:  # never leave the page waiting
            done = {"state": "failed", "error": hide(f"{type(e).__name__}: {e}", secrets),
                    "problems": [], "kept": path.exists()}
        with self.lock:
            self.state.update(done)
