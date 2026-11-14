# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Experiments run from the page (D150): several at once, each experiment only once.

Each runs `run_experiment` in a thread, the CLI's own engine, and keeps the lines
the CLI would print, so the page shows the same words. Stop is Ctrl-C: the runs in
progress stop, and the next start picks up where they stopped. Only transient
control state lives here; everything a run leaves is in the experiment's runs/.

A rehearsal starts at once: it calls no model and spends nothing. A real run, or
`--evaluate`, starts only on the confirmation the page showed (D151): the server makes
the quote again and refuses when anything it depends on has changed.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread

from ..console import Console
from ..experiment import EXPERIMENT_FILE, read_experiment
from ..experiment_run import _paid, run_experiment
from . import api, experiments
from .jobs import command

KEEP = 400  # the most lines kept for the page; the run folders hold everything


class Lines:
    """A stream the Console prints into, one entry per line; no colour."""

    def __init__(self, lock: Lock):
        self.lock, self.lines, self.pending = lock, [], ""

    def write(self, text: str) -> int:
        with self.lock:
            self.pending += text
            *done, self.pending = self.pending.split("\n")
            self.lines += done
            del self.lines[:-KEEP]
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


@dataclass
class Job:
    id: str
    path: str  # the experiment folder, from the opened root
    rehearse: bool
    command: str
    evaluating: str = "both"
    change: str | None = None
    models: set = field(default_factory=set)  # what a real run calls: shared ones are warned of (D150)
    spent: float = 0.0  # what this session's runs cost, as they finish
    cancel: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)
    state: str = "starting"  # starting | running | stopping | finished | stopped | failed
    code: int | None = None
    running: dict = field(default_factory=dict)  # (condition, replicate) -> run folder, from root
    finished: int = 0
    out: Lines | None = None
    thread: Thread | None = None


class ExperimentJobs:
    def __init__(self, root: Path, files_lock, runner=None, seams=None):
        self.root, self.files_lock = root, files_lock
        self.runner = runner or run_experiment
        self.seams = seams or {}  # tests: model_factory, listers
        self.lock = Lock()
        self.jobs: dict[str, Job] = {}  # by experiment path: the latest job of each
        self.closing = False

    def _folder(self, written: str) -> tuple[Path, str]:
        folder = api.inside(self.root, written)
        if not (folder / EXPERIMENT_FILE).is_file():
            raise ValueError("Choose a folder that holds an experiment.md.")
        return folder, folder.relative_to(self.root).as_posix()

    def models_in_use(self) -> dict[str, set]:
        """Each real run in progress, and the models it calls."""
        with self.lock:
            return {p: j.models for p, j in self.jobs.items() if not j.done.is_set() and not j.rehearse}

    def quote(self, data: dict) -> dict:
        return experiments.quote(self.root, data.get("path", ""), data.get("change"),
                                 data.get("evaluating", "both"), self.models_in_use(), self.seams.get("listers"))

    def busy(self) -> bool:
        with self.lock:
            return any(not j.done.is_set() for j in self.jobs.values())

    def status(self, written: str | None = None) -> dict:
        """One experiment's job, or every experiment that has one in this server session."""
        with self.lock:
            if written is None:
                return {"jobs": {p: self._shown(j, lines=False) for p, j in self.jobs.items()}}
            path = self._folder(written)[1]
            job = self.jobs.get(path)
            return self._shown(job) if job else {"active": None, "path": path}

    def _shown(self, job: Job, lines: bool = True) -> dict:
        shown = {"id": job.id, "path": job.path, "state": job.state, "rehearse": job.rehearse,
                 "evaluating": job.evaluating, "spent_usd": job.spent,
                 "command": job.command, "done": job.done.is_set(), "code": job.code,
                 "finished": job.finished,
                 "running": [{"condition": c, "replicate": r, "folder": f} for (c, r), f in job.running.items()]}
        if lines:
            with job.out.lock:
                shown["lines"] = list(job.out.lines) + ([job.out.pending] if job.out.pending else [])
        return shown

    def start(self, data: dict) -> dict:
        folder, path = self._folder(data.get("path", ""))
        rehearse = data.get("rehearse", True)
        if not isinstance(rehearse, bool):
            raise ValueError("Choose whether this is a rehearsal.")
        with self.lock:
            old = self.jobs.get(path)
            if old and not old.done.is_set():
                raise api.Conflict("This experiment is already running. Stop it or wait for it to finish.")
        api.inside(self.root, (folder / "runs").relative_to(self.root).as_posix())
        guard = Lock()
        if rehearse:
            exp = read_experiment(folder)
            if not exp.ok:
                raise ValueError("experiment.md has problems to fix first; the Plan shows them.")
            job = Job(secrets.token_urlsafe(16), path, True, command(["teamrunner", "experiment", path, "--rehearse"]),
                      out=Lines(guard))
        else:  # only on the confirmation the page showed, made again here (D151 (2))
            q = self.quote(data)
            if q["fingerprint"] != data.get("fingerprint"):
                raise experiments.Stale(q)
            if not q["can_run"]:
                raise experiments.Refused(q)
            args = (["teamrunner", "experiment", path] + experiments.CHANGES[q["change"]]
                    + experiments.EVALUATING[q["evaluating"]])
            job = Job(secrets.token_urlsafe(16), path, False, command(args), evaluating=q["evaluating"],
                      change=q["change"], models=set(q["models"]), out=Lines(guard))
        with self.lock:
            if self.closing:
                raise api.Conflict("The local server is stopping.")
            old = self.jobs.get(path)
            if old and not old.done.is_set():
                raise api.Conflict("This experiment is already running. Stop it or wait for it to finish.")
            self.jobs[path] = job
        job.thread = Thread(target=self._work, args=(job, folder), daemon=False)
        job.thread.start()
        return self.status(path)

    def _work(self, job: Job, folder: Path) -> None:
        def slot(s, run_folder, result):
            rel = run_folder.relative_to(self.root).as_posix()
            with self.lock:
                if result is None:
                    job.running[(s.condition, s.replicate)] = rel
                    if job.state == "starting":
                        job.state = "running"
                else:
                    job.running.pop((s.condition, s.replicate), None)
                    job.finished += 1
                    if not job.rehearse and result.get("record"):
                        job.spent += _paid(result["record"])
        try:
            extra = {} if job.rehearse else {"change": job.change, "evaluating": job.evaluating}
            code = self.runner(folder, Console(False, job.out), rehearse=job.rehearse, cancel=job.cancel,
                               on_slot=slot, copy_lock=self.files_lock, **extra, **self.seams)
            with self.lock:
                job.code = code
                job.state = "stopped" if job.cancel.is_set() else "finished" if code in (0, 2) else "failed"
        except Exception as error:  # the lines so far stay; the run folders hold the rest
            job.out.write(f"\n✗ the experiment could not finish: {type(error).__name__}: {error}\n")
            with self.lock:
                job.state = "failed"
        finally:
            job.out.write("\n" if job.out.pending else "")
            with self.lock:
                job.running.clear()
                job.done.set()

    def stop(self, written: str) -> dict:
        path = self._folder(written)[1]
        with self.lock:
            job = self.jobs.get(path)
            if job is None:
                raise FileNotFoundError("This experiment is not running in this server session.")
            if not job.done.is_set():
                job.cancel.set()
                job.state = "stopping"
        return self.status(path)

    def close(self) -> None:
        with self.lock:
            self.closing = True
            jobs = list(self.jobs.values())
        for job in jobs:
            job.cancel.set()
        for job in jobs:
            if job.thread:
                job.thread.join(timeout=20)
