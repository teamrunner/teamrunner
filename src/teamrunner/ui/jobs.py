# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""One in-process run at a time; only transient control state lives here."""

import base64
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import secrets
import shlex
import tempfile
import time
from threading import Event, Lock, Thread

from ruamel.yaml import YAML

from ..build import load_team
from ..inputs import InputError, resolve
from ..run import Options, entry_path, run_team
from . import api, runviews
from .. import crash


class InvalidTeam(ValueError):
    def __init__(self, report):
        super().__init__("The team's files have errors. Fix the validation problems before running.")
        self.report = report


def command(args):
    if os.name == "nt":  # PowerShell, also used by the Windows install instructions.
        return " ".join("'" + str(arg).replace("'", "''") + "'" for arg in args)
    return shlex.join(map(str, args))


@dataclass
class Job:
    id: str
    team: str
    command: str
    rehearse: bool
    name: str = ""
    cancel: Event = field(default_factory=Event)
    done: Event = field(default_factory=Event)
    state: str = "starting"
    folder: Path | None = None
    speaking: dict | None = None
    reason: str | None = None
    check: dict | None = None
    crash: dict | None = None  # an error TeamRunner did not foresee: what the report button needs (D165)
    thread: Thread | None = None


ESTIMATE_SECONDS = 2.0  # the longest the Run dialog's estimate may work
TOO_LONG = ("this team's flow takes too many turns to walk through quickly. "
            "Rehearse it, then read the turns it took.")
BUILT_RUN = ("This folder is built from experiment.md and built again before every run, so a run "
             "made here would be deleted. Run the experiment, or the team it starts from, instead.")


class _OutOfTime(BaseException):
    """Ends a walk of the flow at the deadline. Not an Exception: a turn treats one of those
    as the model failing, and would answer it by trying again."""


def _walk(team, inputs: dict, seed: int, reply_tokens, deadline: float):
    """One walk of the flow, as estimate.walk makes it, given up at `deadline`: the time is
    looked at before every model call, so a walk ends within one turn of it."""
    import asyncio
    from ..builtin_tools import Context
    from ..engine import Engine
    from ..estimate import Counter
    from ..events import Log
    from ..turns import Members

    class Timed(Counter):
        async def complete(self, *args, **kwargs):
            if time.monotonic() > deadline:
                raise _OutOfTime()
            return await super().complete(*args, **kwargs)

    counter = Timed(seed, reply_tokens)
    context = Context(Path("inputs"), Path("work"), {}, team.package_top, rehearsal=True, inputs=inputs)
    actor = Members(team, inputs, counter, Log(), context=context, waits=())
    asyncio.run(Engine(team, actor, lambda event: None).run())
    return counter


def _priced(counters: list, prices) -> tuple[float | None, list[str], float]:
    """The mean cost and calls of the walks, as estimate.estimate works them out."""
    from ..prices import model_price
    totals: dict[str, list[int]] = {}
    for counter in counters:
        for model, used in counter.usage.items():
            held = totals.setdefault(model, [0, 0, 0])
            for at in range(3):
                held[at] += used[at]
    cost, unpriced = 0.0, []
    for model, (tokens_in, tokens_out, _) in totals.items():
        price = model_price(model, prices)
        if price["input_per_million"] is None:
            unpriced.append(model)
            continue
        cost += (tokens_in * price["input_per_million"] + tokens_out * price["output_per_million"]) / 1e6
    n = len(counters)
    return (None if unpriced else cost / n), sorted(unpriced), sum(t[2] for t in totals.values()) / n


def run_estimate(team, seconds: float = ESTIMATE_SECONDS) -> dict | None:
    """What one real run of the team would cost with its default inputs, as an experiment's
    plan estimates it: typical, and at most if every reply ran to its limit (review
    2026-11-16, U9). None when it cannot be said (the team does not load, or an input has
    no default).

    The work is capped at `seconds`: a walk takes every turn of the flow, and a chair with
    thousands of steps held the page for minutes (review 2026-11-21, NU5). One typical walk
    and one longest-reply walk come first, then more typical walks (up to estimate.WALKS)
    while there is time; `walks` says how many the figure is the mean of, out of `of`, and
    `too_long` that not even one could be finished."""
    if team is None:
        return None
    from ..estimate import REPLY_TOKENS, WALKS
    from ..prices import load_prices, unpriced_note
    try:
        values = {name: item.value for name, item in resolve(team).items()}
    except InputError:
        return None
    prices, deadline = load_prices(), time.monotonic() + seconds
    typical, most = [], []
    try:
        typical.append(_walk(team, values, 1, REPLY_TOKENS, deadline))
        most.append(_walk(team, values, 1, None, deadline))
        for seed in range(2, WALKS + 1):
            typical.append(_walk(team, values, seed, REPLY_TOKENS, deadline))
        most.append(_walk(team, values, 2, None, deadline))
    except _OutOfTime:
        pass
    if not typical:
        return {"typical_usd": None, "most_usd": None, "unpriced": [], "calls": None, "walks": 0,
                "of": WALKS, "too_long": True, "note": TOO_LONG}
    typical_usd, unpriced, calls = _priced(typical, prices)
    return {"typical_usd": typical_usd, "most_usd": _priced(most, prices)[0] if most else None,
            "unpriced": unpriced, "calls": calls, "walks": len(typical), "of": WALKS, "too_long": False,
            "note": unpriced_note(unpriced, prices) if unpriced else None}


class Jobs:
    def __init__(self, root, files_lock, runner=None):
        self.root, self.files_lock = root, files_lock
        self.runner = runner or run_team
        self.lock = Lock()
        self.jobs = {}
        self.active = None
        self.closing = False

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise FileNotFoundError("This run is not in this server session.")
            return self.jobs[job_id]

    def status(self, job_id=None):
        with self.lock:
            job = self.jobs.get(job_id or self.active)
            if job is None or (job_id is None and job.done.is_set()):
                return {"active": None}
            return {"id": job.id, "team": job.team, "state": job.state,
                    "name": job.name,
                    "folder": job.folder.relative_to(self.root).as_posix() if job.folder else None,
                    "speaking": job.speaking, "reason": job.reason, "check": job.check, "crash": job.crash,
                    "done": job.done.is_set(), "command": job.command, "rehearse": job.rehearse}

    def _not_built(self, path):
        """A run in a condition's generated folder is refused, as writes there are (D152):
        the next build of the experiment deletes the folder and the run's record with it,
        and a real run there was money spent on nothing kept (review 2026-11-21, NU1)."""
        if api.generated(self.root, path):
            raise api.OutsideRoot(BUILT_RUN)

    def estimate(self, written):
        """The cost estimate, asked for apart from the dialog and only when a real run is in
        view. The team is read under the file lock; the walks are made after it is let go,
        so Save and every other button keep working meanwhile (review 2026-11-21, NU5)."""
        with self.files_lock:
            folder = api.team_folder(self.root, written)
            self._not_built(folder)
            team, _ = load_team(folder)
        return {"team": folder.relative_to(self.root).as_posix(), "estimate": run_estimate(team)}

    def setup(self, written):
        folder = api.team_folder(self.root, written)
        self._not_built(folder)
        team, _ = load_team(folder)
        records, _ = runviews.records(self.root, folder.relative_to(self.root).as_posix())
        ran = any(r.run.get("entry") == entry_path(team) if team else r.team == folder.name for r in records)
        return {"team": folder.relative_to(self.root).as_posix(), "name": team.name if team else folder.name,
                "default_rehearse": not ran, "command_base": command(["teamrunner", "run", folder]),
                "windows": os.name == "nt"}

    def _upload(self, staging, upload):
        if not isinstance(upload, dict):
            raise ValueError("Choose an input file.")
        name = upload.get("name", "")
        if (not isinstance(name, str) or not name or name.startswith(".") or Path(name).name != name
                or any(c in name for c in '\\/:\x00\r\n')):
            raise ValueError("Choose a file with a simple filename.")
        try:
            content = base64.b64decode(upload.get("base64", ""), validate=True)
        except (ValueError, TypeError):
            raise ValueError("The uploaded file could not be read.") from None
        if len(content) > 8 * 1024 * 1024:
            raise ValueError("Choose a file no larger than 8 MB.")
        path = staging / name
        path.write_bytes(content)
        return path

    def _rerun(self, team, written, staging):
        record = runviews.selected(self.root, written)
        if record.run.get("entry") != entry_path(team):
            raise ValueError("Choose this run's original team to re-run it with the current team files.")
        values, file = {}, None
        for name, item in (record.run.get("inputs") or {}).items():
            if item.get("file"):
                if file is not None or name != team.message_goes_to:
                    raise ValueError("This run's file inputs cannot be re-run by this version.")
                original = item["file"]["original"].replace("\\", "/").rsplit("/", 1)[-1]
                source = runviews.attachment(self.root, written, "inputs/" + original)
                content = source.read_bytes()
                if hashlib.sha256(content).hexdigest() != item["file"].get("sha256"):
                    raise ValueError("The recorded input file changed; it cannot be re-run as the same input.")
                file = staging / original
                file.write_bytes(content)
            else:
                values[name] = item.get("value")
        if not values:
            return file, None
        task = staging / "recorded-inputs.yaml"
        if task == file:
            task = staging / "other-recorded-inputs.yaml"
        with task.open("w", encoding="utf-8", newline="\n") as stream:
            YAML().dump({"inputs": values}, stream)
        return file, task

    def _guard_tools(self, team):
        # The CLI permits explicitly configured external folders; the browser root does not.
        # run_code's `paths`, and the mailbox read_email reads and save_draft writes into
        # (review 2026-11-16, S10: the mailbox was not checked).
        from ..builtin_tools import DEFAULT_MAILBOX
        for member in team.members.values():
            if member.kind != "toolkeeper" or member.tool not in ("run_code", "read_email", "save_draft"):
                continue
            terms = [member.base, member.others, *member.access.values()]
            for term in terms:
                if not term:
                    continue
                if member.tool == "run_code":
                    folders, what = term.guardrails.get("paths", []), "A run_code folder"
                else:
                    folders, what = [term.guardrails.get("mailbox", DEFAULT_MAILBOX)], f"{member.name}'s mailbox"
                for written in folders:
                    path = Path(str(written)).expanduser()
                    path = path if path.is_absolute() else team.package_top / path
                    path = path.resolve()
                    if not path.is_relative_to(self.root):
                        raise api.OutsideRoot(f"{what} points outside the opened folder.")
                    if not path.exists():
                        continue
                    for directory, dirs, names in os.walk(path, followlinks=False):
                        for name in dirs + names:
                            link = Path(directory) / name
                            if link.is_symlink() and not link.resolve().is_relative_to(self.root):
                                raise api.OutsideRoot(f"{what} contains a link outside the opened folder.")

    def start(self, data):
        with self.lock:
            if self.closing:
                raise api.Conflict("The local server is stopping.")
            if self.active and not self.jobs[self.active].done.is_set():
                raise api.Conflict("A run is already in progress. Stop it or wait for it to finish.")
        self._not_built(api.inside(self.root, data.get("path", "")))  # before validating: that writes ids
        report = api.validate_team(self.root, data.get("path", ""), offline=True)
        if not report["ok"]:
            raise InvalidTeam(report)
        folder = api.inside(self.root, report["team"])
        team, _ = load_team(folder)
        if team is None:
            raise ValueError("The team's files changed. Validate them before running.")
        api.inside(self.root, (team.package_top / "runs").relative_to(self.root).as_posix())
        from ..keys import package_envs
        for env in package_envs(team.package_top):
            if not env.resolve().is_relative_to(self.root):
                raise api.OutsideRoot("A package key file points outside the opened folder.")
        self._guard_tools(team)
        temporary = tempfile.TemporaryDirectory(prefix="teamrunner-ui-input-")
        staging = Path(temporary.name)
        if not isinstance(data.get("rehearse", True), bool):
            temporary.cleanup()
            raise ValueError("Choose whether this is a rehearsal.")
        options = Options(quiet=True, rehearse=data.get("rehearse", True))
        args = ["teamrunner", "run", str(folder)]
        try:
            mode = data.get("mode", "defaults")
            if data.get("rerun"):
                options.file, options.task_file = self._rerun(team, data["rerun"], staging)
                if options.task_file:
                    args += ["--task-file", options.task_file.name]
                if options.file:
                    args += ["--file", options.file.name]
            elif mode == "message":
                message = data.get("message", "")
                if not isinstance(message, str):
                    raise ValueError("Enter a text message.")
                options.message = message
                args += ["--message", message]
            elif mode in ("file", "task"):
                path = self._upload(staging, data.get("upload"))
                if mode == "file":
                    options.file = path
                    args += ["--file", path.name]
                else:
                    options.task_file = path
                    args += ["--task-file", path.name]
            elif mode != "defaults":
                raise ValueError("Choose team defaults, a message, a file or a task file.")
            resolve(team, options.message, options.file, options.task_file)
            if options.rehearse:
                args.append("--rehearse")
            job = Job(secrets.token_urlsafe(16), report["team"], command(args), options.rehearse, name=team.name)
            with self.lock:
                if self.closing or (self.active and not self.jobs[self.active].done.is_set()):
                    raise api.Conflict("A run is already in progress, or the local server is stopping.")
                self.jobs[job.id] = job
                self.active = job.id
            job.thread = Thread(target=self._work, args=(job, folder, options, temporary), daemon=False)
            job.thread.start()
            return self.status(job.id)
        except InputError as error:
            # The input file is a copy in a temporary folder; the message names the file as
            # the person chose it, never that folder (review 2026-11-21, NU12).
            text = str(error)
            for held in {str(staging), str(staging.resolve())}:
                text = text.replace(held + os.sep, "")
            temporary.cleanup()
            raise InputError(text) from None
        except BaseException:
            temporary.cleanup()
            raise

    def _work(self, job, folder, options, temporary):
        locked = False
        def event(event):
            nonlocal locked
            if event["event"] == "run_started":
                with self.lock:
                    job.state = "stopping" if job.cancel.is_set() else "running"
                # IR, inputs and fingerprints are fixed; editing can continue now.
                if locked:
                    self.files_lock.release()
                    locked = False
        def opened(path):
            safe = api.inside(self.root, path.relative_to(self.root).as_posix())
            with self.lock:
                job.folder = safe
        def turn(info):
            with self.lock:
                job.speaking = info
        try:
            self.files_lock.acquire()
            locked = True
            api.guard_team(self.root, folder)
            current, _ = load_team(folder)
            if current:
                self._guard_tools(current)
            outcome = self.runner(folder, options, cancel=job.cancel,
                                  on_folder=opened, on_event=event, on_turn=turn)
            with self.lock:
                job.state, job.reason = outcome.status, outcome.reason
                job.check = outcome.check.event() if outcome.check else None
                if job.cancel.is_set() and outcome.folder is None:
                    job.state, job.reason = "stopped", "Stopped before the first turn; no run folder was created."
        except OSError:  # the folder or a file went away or could not be written: not a fault of TeamRunner's
            with self.lock:
                job.state, job.reason = "failed", "The run could not finish. Any events already written remain in its run folder."
        except Exception as error:  # noqa: BLE001 — a crash (D165)
            found = crash.shown(crash.record(error, "ui run"))
            with self.lock:
                job.state, job.reason = "failed", "The run could not finish. Any events already written remain in its run folder."
                job.crash = found
        finally:
            if locked:
                self.files_lock.release()
            temporary.cleanup()
            with self.lock:
                job.speaking = None
                job.done.set()

    def stop(self, job_id):
        job = self.get(job_id)
        with self.lock:
            if not job.done.is_set() and job.state in ("starting", "running", "stopping"):
                job.cancel.set()
                job.state = "stopping"
        return self.status(job.id)

    def close(self):
        with self.lock:
            self.closing = True
            active = self.jobs.get(self.active)
        if active and not active.done.is_set():
            self.stop(active.id)
            if active.thread:
                active.thread.join(timeout=20)
