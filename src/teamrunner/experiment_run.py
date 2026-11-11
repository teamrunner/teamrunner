# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""`teamrunner experiment <folder>`: run every (condition, replicate) slot (D138 (4)–(6)).

    plan → the estimate (nothing starts if it is over the ceiling) → round 1 →
    the estimate again from real costs (the rest does not start if it is now
    over) → the other rounds, up to `max_concurrent` at once → a line for each
    run and the totals

Nothing asks: the estimate is shown, and the ceiling is hard. `--max-cost`
replaces `max_cost_usd` for this session, and the record says so.

Each run executes from its own `resolved/` copy of its condition, in the
experiment's `runs/`, through `run_team` — the same engine as a single run.
Every model call goes through the ceiling (`ceiling.Guarded`).

Resume is what the command does (D138 (7)–(9), amended). A slot is filled by
a completed run, or one its flow ended at `stop`, of the study as it now
stands. A run that failed, or was cut off by Ctrl-C or the ceiling, is redone
in its slot with the same seed: the new run reuses the old one's turns from
its `events.jsonl` and calls models only from where it stopped; the old record
is marked `replaced_by`, the new one `continued_from`. When the study has
changed since its runs were made, nothing runs until `--restart` or
`--keep-unchanged` says what to do.

Rehearsal runs are kept in `runs/` and marked, and never fill a slot. A
rehearsal checks the wiring, not the budget: it ignores the ceiling and shows
the estimate at the end, saying so only when it is over.
"""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

from .build import _team
from .ceiling import Ceiling, Guarded, max_reply
from .experiment import (Condition, Experiment, Slot, _what_changed, canonical_files, copy_package,
                         experiment_hash, plan_runs, read_experiment, write_ids)
from .loader import RUNS_DIR, find_top
from .plan import RAISE, _money, ceiling_of, litellm_max_lines, per_run, report_problems, say_estimate
from .prices import Prices, load_prices, model_price
from .evaluate import estimate_one, pending, run_jobs, shuffle, spent_on
from .metrics import Evaluator
from .run import Options, run_team
from .runfolder import record_file
from .schema_check import plain

# --- what is already in runs/ ----------------------------------------------------------

def experiment_records(exp: Experiment) -> list[tuple[Path, dict]]:
    """Every run this experiment has made, newest last."""
    found = []
    runs = exp.folder / RUNS_DIR
    for folder in sorted(runs.iterdir()) if runs.is_dir() else []:
        path = record_file(folder)
        if path is None:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(record, dict) and isinstance(record.get("experiment"), dict):
            found.append((folder, record))
    return found


def _cost(record: dict) -> float:
    cost = record.get("cost_usd")
    return float(cost if cost is not None else record.get("cost_known_usd") or 0.0)


def _paid(record: dict) -> float:
    """What a run itself spent: its cost less the turns it reused (D138 (8), amended)."""
    return _cost(record) - float(record.get("cost_reused_usd") or 0.0)


def fills(record: dict) -> bool:
    """A completed run, or one its own flow ended at `stop` (D138 (7), amended)."""
    return record.get("status") == "completed" or (
        record.get("status") == "stopped" and record.get("stopped_by") == "flow")


class Study:
    """Which of the experiment's real runs belong to the study as it now stands."""

    def __init__(self, exp: Experiment, records, change: str | None):
        self.exp = exp
        self.hash = experiment_hash(exp)
        self.hashes = {c.name: c.study_hash for c in exp.conditions}
        self.real = [(f, r) for f, r in records if not r.get("rehearsal")]
        mine = [r for _, r in self.real if r["experiment"].get("experiment_hash") == self.hash]
        # Changed: there are runs, none of them of this study, and no decision yet.
        self.changed = bool(self.real) and not mine
        kept = any(r["experiment"].get("study_change") == "keep_unchanged" for r in mine)
        self.change = change if self.changed else None
        self.by_condition = kept or self.change == "keep_unchanged"

    def belongs(self, record: dict) -> bool:
        block = record["experiment"]
        if self.hashes.get(block.get("condition")) != block.get("study_hash"):
            return False
        return self.by_condition or block.get("experiment_hash") == self.hash

    def slots(self) -> tuple[dict, dict]:
        """(filled, to redo): each slot's filling run, and each slot's latest run that
        failed or was cut off, not yet replaced."""
        filled, redo = {}, {}
        for folder, record in self.real:
            if not self.belongs(record):
                continue
            block = record["experiment"]
            slot = (block["condition"], block.get("replicate"))
            if fills(record):
                filled.setdefault(slot, (folder, record))
            elif block.get("replaced_by") is None:
                redo[slot] = (folder, record)  # newest last: the latest wins
        return filled, {k: v for k, v in redo.items() if k not in filled}

    def what_changed(self) -> tuple[list[str], list[str]]:
        """(each changed condition, with what changed in it; the unchanged conditions)."""
        latest = {}
        for folder, record in self.real:
            latest[record["experiment"].get("condition")] = (folder, record)
        changed, same = [], []
        for c in self.exp.conditions:
            if c.name not in latest:
                changed.append(f"{c.name}: new")
                continue
            folder, record = latest[c.name]
            if record["experiment"].get("study_hash") == c.study_hash:
                same.append(c.name)
                continue
            parts = []
            if record["experiment"].get("seeds", {}).get("master") != self.exp.seed:
                parts.append("the seed")
            if {k: v.get("value") for k, v in (record.get("inputs") or {}).items()} != c.inputs:
                parts.append("the task inputs")
            if (folder / "resolved").is_dir():
                old, new = canonical_files(folder / "resolved"), canonical_files(find_top(c.folder))
                for name in sorted(set(old) | set(new)):
                    if name not in new:
                        parts.append(f"{name} removed")
                    elif name not in old:
                        parts.append(f"{name} added")
                    elif old[name] != new[name]:
                        what = _what_changed(old[name], new[name])
                        parts.append(f"{name}" + (f" ({what})" if what else ""))
            changed.append(f"{c.name}: {', '.join(parts) or 'its files'}")
        gone = sorted({r["experiment"].get("condition") for _, r in self.real} - set(self.hashes))
        changed += [f"{name}: no longer a condition" for name in gone]
        return changed, same


def counted(exp: Experiment, records) -> dict[tuple[str, int], tuple[Path, dict]]:
    """The real runs that fill a slot of the study as it now stands."""
    return Study(exp, records, None).slots()[0]


# --- one run ------------------------------------------------------------------------------

def resolve(cond: Condition, dest: Path) -> Path:
    """`resolved/` (D60, amended): an exact copy of the condition's package as it
    ran, nothing written in; defaults are read from its own files, and what ran
    from `record.json`. Returns the team folder inside it."""
    top = find_top(cond.folder)
    copy_package(top, dest)
    return dest / cond.folder.relative_to(top)


def _folder_for(exp: Experiment, slot: Slot, now: datetime) -> Path:
    runs = exp.folder / RUNS_DIR
    runs.mkdir(exist_ok=True)
    name = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{slot.condition}_r{slot.replicate:02d}"
    path, n = runs / name, 2
    while True:
        try:
            path.mkdir()
            return path
        except FileExistsError:
            path, n = runs / f"{name}_{n}", n + 1


def block(exp: Experiment, cond: Condition, slot: Slot, *, continued_from: str | None = None,
          study_change: str | None = None) -> dict:
    """The `experiment` block of a run's record (SPEC §6.2)."""
    return {"name": exp.name, "condition": cond.name, "levels": dict(cond.levels),
            "changes": {str(k): v for k, v in plain(cond.changes).items()},
            "replicate": slot.replicate, "round": slot.round,
            "seeds": {"master": exp.seed, "run": slot.seed},
            "study_hash": cond.study_hash, "experiment_hash": experiment_hash(exp),
            "study_change": study_change,  # restart | keep_unchanged, in the session that chose it
            "replaced_by": None, "continued_from": continued_from,
            "body": exp.body}


# --- the whole experiment -------------------------------------------------------------------

class Runner:
    def __init__(self, exp: Experiment, out, *, rehearse: bool, max_cost: float | None,
                 prices: Prices, change: str | None = None, evaluating: str = "both", model_factory=None,
                 listers=None, model_data=None, cancel: threading.Event | None = None, waits=None):
        self.exp, self.out, self.rehearse, self.change = exp, out, rehearse, change
        self.evaluating = evaluating  # both | only (--evaluate) | none (--no-evaluate)
        self.evaluators = [m for m in exp.measures if isinstance(m, Evaluator)]
        self._walks = None
        self.redo: dict[tuple[str, int], tuple[Path, dict]] = {}
        self.limit, self.limit_from = ceiling_of(exp, max_cost)
        self.prices = prices
        self.model_factory, self.listers, self.model_data, self.waits = model_factory, listers, model_data, waits
        self.cancel = cancel or threading.Event()
        self.by_name = {c.name: c for c in exp.conditions}
        self.lock = threading.Lock()
        self.results: list[dict] = []
        self.could_not_start = None
        self.spent_before = 0.0
        self.over_after_first = False

    def one(self, slot: Slot, ceiling: Ceiling) -> dict:
        cond = self.by_name[slot.condition]
        old_folder, old = self.redo.get((slot.condition, slot.replicate), (None, None))
        folder = _folder_for(self.exp, slot, datetime.now())
        team = resolve(cond, folder / "resolved")
        options = Options(task_file=self.exp.task, quiet=True, rehearse=self.rehearse, seed=slot.seed,
                          run_folder=folder,
                          experiment=block(self.exp, cond, slot, study_change=self.study_change,
                                           continued_from=old.get("run_id") if old else None),
                          max_cost=self.limit, max_cost_from=self.limit_from, continue_from=old_folder)
        kwargs = {}
        if self.waits is not None:
            kwargs["waits"] = self.waits
        outcome = run_team(team, options, model_factory=self.model_factory, listers=self.listers,
                           model_data=self.model_data, cancel=self.cancel,
                           wrap=None if self.rehearse else (lambda model: Guarded(model, ceiling, self.prices)),
                           **kwargs)
        path = record_file(folder)
        record = json.loads(path.read_text(encoding="utf-8")) if path else {}
        if old is not None and record:  # the old record says which run redid its slot
            old["experiment"]["replaced_by"] = record.get("run_id")
            record_file(old_folder).write_text(json.dumps(old, indent=2, ensure_ascii=False, default=str) + "\n",
                                               encoding="utf-8")
        ceiling.settle(0.0, record.get("cost_tools_usd") or 0.0)  # tools are counted after the run
        result = {"slot": slot, "status": outcome.status, "folder": folder, "record": record,
                  "outcome": outcome}
        with self.lock:
            self.results.append(result)
        self.line(result)
        return result

    def line(self, result: dict) -> None:
        slot, record, paint = result["slot"], result["record"], self.out.paint
        mark = {"completed": paint.green("✓"), "stopped": paint.yellow("■"),
                "failed": paint.red("✗")}.get(result["status"], paint.red("✗"))
        cost = _money(_cost(record)) if record else ""
        took = f"{(record.get('latency_ms') or 0) / 1000:.1f}s" if record else ""
        where = os.path.relpath(result["folder"], self.exp.folder)
        text = (f"  {mark} round {slot.round:>2} · {slot.condition:<{self.width}}  {result['status']:<9} "
                f"{record.get('turns', 0):>3} turns  {took:>6}  {cost:>8}  {paint.dim(where)}")
        if record.get("experiment", {}).get("continued_from"):
            text += "  " + paint.dim("continued")
        if result["status"] != "completed" and (record.get("reason") or result["outcome"].reason):
            text += "\n      " + paint.dim(record.get("reason") or result["outcome"].reason)
        with self.lock:
            self.out.say(text)

    def run(self) -> int:
        exp, out, say = self.exp, self.out, self.out.say
        self.width = max(len(c.name) for c in exp.conditions)
        records = experiment_records(exp)
        real = [r for _, r in records if not r.get("rehearsal")]
        spent = sum(_paid(r) for r in real) + spent_on([(f, r) for f, r in records if not r.get("rehearsal")],
                                                     self.evaluators)
        study = Study(exp, records, self.change)
        real_done, real_redo = study.slots()
        done = {} if self.rehearse else real_done
        self.redo = {} if self.rehearse else real_redo
        self.study_change = None if self.rehearse else study.change
        limit = self.limit
        todo = [s for s in plan_runs(exp) if (s.condition, s.replicate) not in done]
        # what a real run would still have to do, for the estimate a rehearsal ends with
        self.real_todo = [s for s in plan_runs(exp) if (s.condition, s.replicate) not in real_done]
        self.real_spent = spent

        title = exp.name or exp.folder.name
        mode = " · rehearsal: no model is called, nothing is spent, nothing counts" if self.rehearse else ""
        say(f"experiment · {title} · {exp.runs} runs ({len(exp.conditions)} conditions × {exp.replications}){mode}")
        if not self.rehearse and study.changed and study.change is None:
            return self._changed(study)
        if self.change and not self.rehearse and not study.changed:
            say(out.paint.dim(f"--{self.change.replace('_', '-')} does nothing: the study has not changed"))
        if self.study_change == "restart":
            say(out.paint.dim("--restart: the changed study starts from round 1; earlier runs are kept, "
                              "and do not count"))
        elif self.study_change == "keep_unchanged":
            say(out.paint.dim("--keep-unchanged: the runs of conditions that did not change still count"))
        if done:
            say(out.paint.dim(f"{len(done)} of {exp.runs} slots already have a completed run; "
                              f"{len(todo)} to run"))
        if self.redo:
            say(out.paint.dim(f"{len(self.redo)} of them redo a run that failed or was cut off, "
                              "reusing its turns"))
        if self.evaluating == "only":
            return self._after(Ceiling(None if self.rehearse else limit, spent), 0)
        if not todo:
            say(out.paint.green("✓ every slot has a completed run; nothing to do"))
            return self._after(Ceiling(None if self.rehearse else limit, spent), 0)

        if limit is not None and not self.rehearse:
            missing = self._unpriceable()
            if missing:
                say(out.paint.red("✗ the ceiling cannot be kept, so nothing was run:"))
                for line in missing:
                    say(f"  {line}")
                return 1

        if self.limit_from == "--max-cost" and not self.rehearse:
            said = exp.limits.get("max_cost_usd")
            say(out.paint.dim(f"max {_money(limit)} from --max-cost"
                              + (f", in place of {_money(said)} in experiment.md" if said is not None else "")))
        if not self.rehearse and say_estimate(out, self._estimate(todo, spent), limit, spent):
            say(out.paint.red(f"  ✗ over the ceiling, so nothing was run. {RAISE}"))
            return 1
        if limit is not None and not self.rehearse:  # what the ceiling sets aside for them
            for line in litellm_max_lines(exp):
                say(f"  {line}")

        ceiling = Ceiling(None if self.rehearse else limit, spent)
        self.spent_before = spent
        first_round = [s for s in todo if s.round == todo[0].round]
        rest = [s for s in todo if s.round != todo[0].round]
        try:
            self._batch(first_round, ceiling)
            if rest and not self._stopping(ceiling):
                again = None if self.rehearse else self._estimate(rest, ceiling.spent, real_costs=True)
                over = again is not None and say_estimate(
                    out, again, limit, lead=f"After round {todo[0].round}, from real costs: estimated cost")
                if over:
                    say(out.paint.red(f"  ✗ over the ceiling, so the other rounds were not started. {RAISE}, "
                                      "then run again to continue"))
                    self.over_after_first = True
                else:
                    self._batch(rest, ceiling)
        except KeyboardInterrupt:
            self.cancel.set()
            say(out.paint.yellow("■ Ctrl-C: stopping every run in progress…"))
        code = self._totals(ceiling, len(todo))
        if self.cancel.is_set():
            return code
        return max(code, self._after(ceiling, code))

    # --- evaluating and the summary (D138 (11), (13), amended) ----------------------------

    def _after(self, ceiling: Ceiling, code: int) -> int:
        """Evaluate what the session should, then print the summary. Returns an exit code."""
        exp, out = self.exp, self.out
        records = experiment_records(exp)
        if self.rehearse:
            mine = {r["folder"] for r in self.results}
            runs = [(f, r) for f, r in records if f in mine and fills(r)]
            left = {}
        else:
            study = Study(exp, records, self.change)
            filled = study.slots()[0]
            runs = sorted(filled.values(), key=lambda fr: (fr[1]["experiment"].get("round") or 0, fr[0].name))
            left = self._left_out(study, filled)
        result = 0
        if self.evaluators and self.evaluating != "none":
            result = self._evaluate(runs, ceiling)
        elif self.evaluators and not self.rehearse:
            out.say(out.paint.dim("--no-evaluate: no evaluator was called"))
        from collections import Counter
        from .runs import read_events
        from .summary import summary
        out.say("")
        note = " · rehearsal: placeholder values, not results" if self.rehearse else ""
        evaluating = spent_on(runs, self.evaluators) if not self.rehearse else None
        runs_cost = sum(_paid(r) for _, r in runs) if not self.rehearse else None
        summary(exp, runs, Counter(left), exp.measures, out, lambda f: list(read_events(f)),
                spent_runs=runs_cost, spent_evaluating=evaluating, note=note)
        if self.rehearse and self.real_todo:
            out.say("")
            self._rehearsal_estimate()  # last: what the real experiment would cost
        return result

    def _left_out(self, study: Study, filled: dict) -> dict[str, int]:
        why = {}
        for folder, record in study.real:
            if not study.belongs(record) or (folder, record) in filled.values():
                continue
            if record["experiment"].get("replaced_by"):
                continue
            label = "failed" if record.get("status") == "failed" else (
                f"stopped by {'the ceiling' if record.get('stopped_by') == 'ceiling' else 'the user'}")
            why[label] = why.get(label, 0) + 1
        missing = self.exp.runs - len(filled)
        if missing - sum(why.values()) > 0:
            why["not run yet"] = missing - sum(why.values())
        return why

    def _evaluate(self, runs, ceiling: Ceiling) -> int:
        out, say, paint = self.out, self.out.say, self.out.paint
        again = self.evaluating == "only"
        jobs, stale = pending(runs, self.evaluators, again)
        by_name = {e.name: e for e in self.evaluators}
        for name, n in sorted(stale.items()):
            cost = self._eval_cost(by_name[name], runs[:n]) if not self.rehearse else None
            say(paint.yellow(f"{by_name[name].path.name} changed since {n} run{'s' if n != 1 else ''} "
                             f"{'were' if n != 1 else 'was'} evaluated. To evaluate "
                             f"{'them' if n != 1 else 'it'} again"
                             + (f" (≈ {_money(cost)})" if cost else "") + ": --evaluate"))
        if not jobs:
            if again:
                say(paint.green("✓ every run that counts is evaluated by each evaluator's current version"))
            return 0
        if not self.rehearse and ceiling.limit is not None:
            missing = self._unpriceable(evaluators_only=True)
            if missing:
                say(paint.red("✗ the ceiling cannot be kept, so nothing was evaluated:"))
                for line in missing:
                    say(f"  {line}")
                return 1
        if not self.rehearse and self.evaluating == "only":  # the estimate, as before any spending
            walks = self._walk()
            costs = [estimate_one(j.evaluator, walks[j.record["experiment"]["condition"]].tokens_out, self.prices)
                     for j in jobs]
            est = None if any(c is None for c in costs) else ceiling.spent + sum(costs)
            if say_estimate(out, est, ceiling.limit, ceiling.spent):
                say(paint.red(f"  ✗ over the ceiling, so nothing was evaluated. {RAISE}"))
                return 1
        names = sorted({j.evaluator.name for j in jobs})
        say(f"evaluating · {len(jobs)} evaluation{'s' if len(jobs) != 1 else ''} "
            f"({', '.join(names)}), in an order drawn from seed {self.exp.seed}")
        model = self._evaluator_model()
        if model is None:
            return 1
        if not self.rehearse:
            model = Guarded(model, ceiling, self.prices)
        done = run_jobs(shuffle(jobs, self.exp.seed), model, self.exp.limits.get("max_concurrent", 1),
                        waits=self.waits if self.waits is not None else (2, 8, 30), cancel=self.cancel)
        parts = [f"{done['made']} made"]
        if done["failed"]:
            parts.append(f"{done['failed']} failed")
        if done["refused"]:
            parts.append(f"{done['refused']} not made: the ceiling ({_money(ceiling.limit)}) was reached; "
                         "they are made next time")
        cost = "" if self.rehearse else f" · {_money(done['cost'])}"
        say(f"evaluated · {', '.join(parts)}{cost}")
        for error in done.get("errors", [])[:3]:
            say(paint.dim(f"  {error}"))
        return 0 if not (done["failed"] or done["refused"]) else 2

    def _evaluator_model(self):
        """The model the evaluators call: the run's seams, or LiteLLM with the keys they need."""
        if self.rehearse:
            from .rehearsal import RehearsalModel
            return RehearsalModel(self.exp.seed or 0)
        from .keys import PROVIDER_KEYS, extra_settings, find_keys
        names = sorted({PROVIDER_KEYS[e.model.split("/", 1)[0]] for e in self.evaluators
                        if e.model.split("/", 1)[0] in PROVIDER_KEYS})
        keys = find_keys(names, None, None)
        settings = extra_settings()
        if self.model_factory is not None:
            return self.model_factory(keys.values, settings)
        missing = [n for n in names if not keys.values.get(n)]
        if missing:
            self.out.say(self.out.paint.red(f"✗ nothing was evaluated: no {', '.join(missing)} was found; "
                                            "add it to ~/.teamrunner/.env"))
            return None
        from .litellm_model import LiteLLMModel
        return LiteLLMModel(keys.values, settings, self.prices)

    def _eval_cost(self, ev: Evaluator, runs) -> float | None:
        """What evaluating these runs with `ev` would cost, `evaluations:` times each."""
        walks = self._walk()
        total = 0.0
        for _, record in runs:
            e = walks.get(record["experiment"]["condition"])
            one = estimate_one(ev, e.tokens_out if e else 0, self.prices)
            if one is None:
                return None
            total += one * ev.evaluations
        return total

    def _walk(self):
        if self._walks is None:
            self._walks = per_run(self.exp, self.prices)
        return self._walks

    def _changed(self, study: Study) -> int:
        """The study changed since its runs were made: say what, and run nothing."""
        say, paint = self.out.say, self.out.paint
        changed, same = study.what_changed()
        n = sum(1 for _, r in study.real if fills(r))
        say(paint.yellow(f"The study has changed since {n} of its run{'s' if n != 1 else ''} "
                         f"{'were' if n != 1 else 'was'} made ({'; '.join(changed)})."))
        say("  Those runs are kept in runs/ but no longer count. Nothing was run.")
        say("  To start the changed study from round 1: --restart")
        if same:
            say(f"  To keep the runs of conditions that did not change ({', '.join(same)}): --keep-unchanged")
        return 1

    def _rehearsal_estimate(self) -> None:
        """A rehearsal ends with what the real experiment would cost, and says so
        only when that is over the ceiling."""
        est = self._estimate(self.real_todo, self.real_spent)
        if say_estimate(self.out, est, self.limit, self.real_spent, brief=True):
            self.out.say(self.out.paint.yellow(f"  ! over the ceiling: a real run would not start. {RAISE}"))
        if est is None:
            for line in self._unpriceable():
                self.out.say(self.out.paint.yellow(f"  ! {line}"))

    def _stopping(self, ceiling: Ceiling) -> bool:
        return self.cancel.is_set() or ceiling.refused > 0 or self.could_not_start is not None

    def _batch(self, slots: list[Slot], ceiling: Ceiling) -> None:
        """Start the slots in plan order, up to max_concurrent at once."""
        workers = self.exp.limits.get("max_concurrent", 1)
        pending = list(slots)
        running = set()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            try:
                while pending or running:
                    while pending and len(running) < workers and not self._stopping(ceiling):
                        running.add(pool.submit(self.one, pending.pop(0), ceiling))
                    if not running:
                        break
                    finished, running = wait(running, timeout=0.2, return_when=FIRST_COMPLETED)
                    for future in finished:
                        result = future.result()
                        if result["status"] == "could_not_start" and self.could_not_start is None:
                            self.could_not_start = result
            except KeyboardInterrupt:
                self.cancel.set()
                wait(running)
                raise

    def _unpriceable(self, evaluators_only: bool = False) -> list[str]:
        """Every model whose worst case cannot be priced: a hard ceiling needs them all."""
        uses = []  # (who, model, params)
        if not evaluators_only:
            for cond in self.exp.conditions:
                for m in _team(cond.report).members.values():
                    if m.kind != "toolkeeper":
                        uses += [(m.name, c.model.value, {k: s.value for k, s in c.params.items()})
                                 for c in m.choices]
        if self.evaluating != "none":
            uses += [(e.name, e.model, e.params) for e in self.evaluators]
        found = []
        for name, model, params in uses:
            if model_price(model, self.prices)["input_per_million"] is None:
                line = f"{model} has no list price; add it to {self.prices.shown} (D120)"
            elif max_reply(model, params) is None:
                line = f"{name} on {model}: no known longest reply; give it `params: {{max_tokens: …}}`"
            else:
                continue
            if line not in found:
                found.append(line)
        return found

    def _estimate(self, slots: list[Slot], spent: float, real_costs: bool = False) -> float | None:
        """What the slots will cost, plus what is already spent: from rehearsal
        walks, or after round 1 from the real runs' costs per condition."""
        per = {}
        if real_costs:
            for result in self.results:
                if result["status"] == "completed" and result["record"]:
                    per.setdefault(result["slot"].condition, []).append(_cost(result["record"]))
            per = {k: sum(v) / len(v) for k, v in per.items()}
        if not real_costs or len(per) < len(self.exp.conditions):
            walks = self._walk()
            for name, e in walks.items():
                if name not in per:
                    if e.per_run_usd is None:
                        return None
                    per[name] = e.per_run_usd
        evaluating = 0.0
        if self.evaluating != "none":
            walks = self._walk()
            for s in slots:
                for ev in self.evaluators:
                    one = estimate_one(ev, walks[s.condition].tokens_out, self.prices)
                    if one is None:
                        return None
                    evaluating += one * ev.evaluations
        return spent + sum(per[s.condition] for s in slots) + evaluating

    def _totals(self, ceiling: Ceiling, planned: int) -> int:
        say, paint = self.out.say, self.out.paint
        if self.could_not_start is not None:
            outcome = self.could_not_start["outcome"]
            say(paint.red(f"✗ a run could not start, so the experiment stopped: {outcome.reason}"))
            if outcome.check is not None:
                for problem in outcome.check.problems:
                    say(f"  {problem}")
        by = {}
        for r in self.results:
            by[r["status"]] = by.get(r["status"], 0) + 1
        if ceiling.refused:
            say(paint.yellow(f"■ the ceiling ({_money(ceiling.limit)}) was reached: {ceiling.refused} call"
                             f"{'s' if ceiling.refused != 1 else ''} not sent. Stopped runs are kept; "
                             "to go further: --max-cost <usd>, then run again"))
        ended = sum(1 for r in self.results if r["record"].get("stopped_by") == "flow")
        if ended:  # the team's own result, not a run cut off
            by["stopped"] -= ended
            by = {k: v for k, v in by.items() if v}
            by["ended at the flow's stop"] = ended
        parts = [f"{n} {status}" for status, n in sorted(by.items())]
        complete = sum(1 for r in self.results if r["record"] and fills(r["record"])) == planned
        tail = ("complete" if complete else "incomplete") if not self.rehearse else \
            ("rehearsal complete" if complete else "rehearsal incomplete") + ", nothing counted"
        spent = ("nothing spent" if self.rehearse
                 else f"spent {_money(ceiling.spent - self.spent_before)}"
                 + (f" ({_money(ceiling.spent)} in all)" if self.spent_before else ""))
        records = [r["record"] for r in self.results if r["record"]]
        charges = [r.get("cost_charged_usd") for r in records]
        if (not self.rehearse and records and all(c is not None for c in charges)
                and not any(r.get("cost_reused_usd") for r in records)):
            spent += f", the provider says {_money(sum(charges))}"  # the bill, beside the list price (D120)
        say(f"{self.exp.name} · {', '.join(parts) or 'nothing ran'} of {planned} · {spent} · {tail} · "
            f"runs in {RUNS_DIR}/")
        if self.cancel.is_set():
            return 130
        return 0 if complete else 2


def run_experiment(folder: Path, out, *, rehearse: bool = False, max_cost: float | None = None,
                   change: str | None = None, evaluating: str = "both", prices: Prices | None = None,
                   **seams) -> int:
    """`change`: "restart" or "keep_unchanged", what to do when the study has changed.
    `evaluating`: "both" (run, then evaluate), "only" (--evaluate) or "none" (--no-evaluate)."""
    exp = read_experiment(folder)
    write_ids(exp)  # D64: into the files themselves, once, so every run's copy carries them
    if not report_problems(exp, out):
        return 1
    runner = Runner(exp, out, rehearse=rehearse, max_cost=max_cost, change=change, evaluating=evaluating,
                    prices=prices if prices is not None else load_prices(), **seams)
    return runner.run()


def print_summary(folder: Path, out) -> int:
    """`--summary`: the summary of the runs that count, from the run folders; nothing is called."""
    from collections import Counter
    from .experiment import build
    from .runs import read_events
    from .summary import summary
    exp = read_experiment(folder)
    build(exp)
    if not report_problems(exp, out):
        return 1
    records = experiment_records(exp)
    study = Study(exp, records, None)
    if study.changed:
        out.say(out.paint.yellow("The study has changed since its runs were made, so no run counts yet; "
                                 "`teamrunner experiment` says what changed."))
        return 1
    filled = study.slots()[0]
    runs = sorted(filled.values(), key=lambda fr: (fr[1]["experiment"].get("round") or 0, fr[0].name))
    runner = Runner(exp, out, rehearse=False, max_cost=None, prices=load_prices())
    evaluators = runner.evaluators
    summary(exp, runs, Counter(runner._left_out(study, filled)), exp.measures, out,
            lambda f: list(read_events(f)), spent_runs=sum(_paid(r) for _, r in runs),
            spent_evaluating=spent_on(runs, evaluators))
    return 0


def export_experiment(folder: Path, dest: Path, out, completed: bool = False) -> int:
    """`--export <dir>`: the five CSV tables (SPEC §5.5), rebuilt from the run folders;
    nothing is called. `completed`: only the runs that count."""
    from .experiment import build
    from .export import export
    exp = read_experiment(folder)
    build(exp)
    if not report_problems(exp, out):
        return 1
    records = experiment_records(exp)
    study = Study(exp, records, None)
    counting = set() if study.changed else {r.get("run_id") for _, r in study.slots()[0].values()}
    runs = [(f, r) for f, r in records if not r.get("rehearsal")]
    if completed:
        runs = [(f, r) for f, r in runs if r.get("run_id") in counting]
    runs.sort(key=lambda fr: ((fr[1]["experiment"].get("round") or 0), fr[0].name))
    rows = export(exp, runs, counting, dest)
    shown = os.path.relpath(dest) if Path(dest).resolve().is_relative_to(Path.cwd()) else str(dest)
    which = "the runs that count" if completed else "every run, with `counts` saying which count"
    out.say(f"exported {len(runs)} run{'s' if len(runs) != 1 else ''} ({which}) to {shown}/")
    for name, n in rows.items():
        out.say(f"  {name + '.csv':<17} {n:>6} row{'s' if n != 1 else ''}")
    if study.changed:
        out.say(out.paint.yellow("! the study has changed since its runs were made, so none counts yet; "
                                 "`teamrunner experiment` says what changed"))
    return 0
