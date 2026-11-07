"""`teamrunner runs` (D102): a package's runs, and any one of them replayed.

The list is newest first, one line per run. Opening a run prints exactly
what the console printed while it ran, rebuilt from its `events.jsonl` —
which is why the console reads only events (console.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .console import Console, money, seconds
from .loader import RUNS_DIR, TEAM_FILE, find_top

STAMP = "%Y-%m-%d_%H%M%S"


@dataclass
class RunRecord:
    folder: Path
    when: datetime
    run: dict

    @property
    def shown_when(self) -> str:
        return f"{self.when.day} {self.when:%b %H:%M}"

    @property
    def team(self) -> str:
        return self.run.get("team") or self.run.get("entry", "").rsplit("/", 1)[-1] or self.folder.name

    @property
    def input(self) -> str:
        inputs = self.run.get("inputs") or {}
        if not inputs:
            return ""
        item = next(iter(inputs.values()))
        value = " ".join(str(item.get("value", "")).split())
        room = 22 if item.get("source") == "default" else 32
        value = value if len(value) <= room else value[:room - 1] + "…"
        return f"{value} (default)" if item.get("source") == "default" else value

    @property
    def result(self) -> str:
        """D102's one rule: the last turn's typed outputs, else the first words of output.md;
        empty when the run failed or was stopped."""
        if self.run.get("status") != "completed":
            return ""
        last, shown = None, {}
        for event in _events(self.folder):
            if event["event"] == "run_started":
                shown = {m["name"]: {k for k, f in (m.get("output") or {}).items() if f["type"] != "text"}
                         for m in event.get("members", [])}
            elif event["event"] == "agent_message":
                last = event
        last = last or {}
        typed = {k: v for k, v in last.get("output", {}).items() if k in shown.get(last.get("from"), ())}
        if typed:
            return ", ".join(f"{k} = {v}" for k, v in typed.items())
        output = self.folder / "output.md"
        words = " ".join(output.read_text(encoding="utf-8").replace("*", "").split()) if output.exists() else ""
        return words if len(words) <= 32 else words[:31] + "…"


class NoRuns(Exception):
    pass


def runs_folder(start: Path, runs_dir: Path | None = None) -> tuple[Path, Path]:
    """The package top and its runs/ folder, found by walking up (D69)."""
    start = Path(start).resolve()
    folder = start
    while not (folder / TEAM_FILE).is_file() and folder.parent != folder:
        folder = folder.parent
    if not (folder / TEAM_FILE).is_file():
        raise NoRuns(f"{start} is not inside a team folder")
    top = find_top(folder)
    return top, Path(runs_dir) if runs_dir else top / RUNS_DIR


def all_runs(runs: Path) -> list[RunRecord]:
    found = []
    for folder in runs.iterdir() if runs.is_dir() else []:
        record = folder / "run.json"
        if not record.is_file():
            continue
        try:
            when = datetime.strptime(folder.name[:17], STAMP)
            run = json.loads(record.read_text(encoding="utf-8"))
        except ValueError:
            continue
        found.append(RunRecord(folder, when, run))
    return sorted(found, key=lambda r: (r.when, r.folder.name), reverse=True)


def pick(records: list[RunRecord], when: str) -> list[RunRecord]:
    """`latest`, a folder name, or any unique part of when it ran as the list shows it."""
    if when == "latest":
        return records[:1]
    exact = [r for r in records if r.folder.name == when]
    if exact:
        return exact
    wanted = " ".join(when.split()).lower()
    return [r for r in records if wanted in r.shown_when.lower() or wanted in r.folder.name.lower()]


def print_list(top: Path, records: list[RunRecord], shown: list[RunRecord], out: Console) -> None:
    p = out.paint
    spent = sum(r.run.get("cost_usd") or r.run.get("cost_known_usd") or 0 for r in records)
    out.say(f"{p.bold(top.name)} · {len(records)} run{'s' if len(records) != 1 else ''} · "
            f"≈ {money(spent)} spent at list prices")
    if not shown:
        out.say(p.dim("no runs match"))
        return
    for r in shown:
        status = r.run.get("status", "?")
        colour = p.green if status == "completed" else p.yellow if status == "stopped" else p.red
        rehearsal = " (rehearsal)" if r.run.get("rehearsal") else ""
        if r.run.get("backup_models"):
            rehearsal += " (backup model)"
        out.say(f"{r.shown_when:<13} {r.team:<20} {r.input:<34} {r.result:<26} "
                f"{colour(f'{status:<9}')} {r.run.get('turns', 0):>3} turns "
                f"{seconds(r.run.get('latency_ms')):>7} {money(r.run.get('cost_usd')):>9}{rehearsal}")


def _events(folder: Path):
    path = folder / "events.jsonl"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def replay(record: RunRecord, out: Console) -> None:
    """Print the run's console again, from its log (D102)."""
    run = record.run
    started = None
    for event in _events(record.folder):
        kind = event["event"]
        if kind == "run_started":
            started = event
            out.on_event(event)  # the console learns who decides and which outputs to show
            inputs = {k: SimpleNamespace(value=v.get("value"), source=v.get("source"))
                      for k, v in (event.get("inputs") or {}).items()}
            out.header(record.team, inputs)
            if event.get("rehearsal"):
                out.say(out.paint.yellow("rehearsal · no model is called; the words are placeholders "
                                         "and the answers random"))
        elif kind == "check":
            check = SimpleNamespace(
                ok=not event.get("problems"), models=event.get("models", []), keys=event.get("keys", []),
                tools=event.get("tools", []), warnings=event.get("warnings", []),
                backups=event.get("backups", []),
                problems=[SimpleNamespace(**p) for p in event.get("problems", [])])
            out.check(check, SimpleNamespace(notes=[]))
        else:
            out.on_event(event)
    if started is None:
        out.say(out.paint.red("this run's log has no start"))
    shown = run if run.get("cost_usd") is not None else {**run, "cost_usd": run.get("cost_known_usd")}
    output = record.folder / "output.md"
    out.totals(shown, record.folder, output.read_text(encoding="utf-8") if output.exists() else "")
