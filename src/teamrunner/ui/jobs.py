"""One in-process run at a time; only transient control state lives here."""

import base64
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import secrets
import shlex
import tempfile
from threading import Event, Lock, Thread

from ruamel.yaml import YAML

from ..build import load_team
from ..inputs import resolve
from ..run import Options, entry_path, run_team
from . import api, runviews


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
    thread: Thread | None = None


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
                    "speaking": job.speaking, "reason": job.reason, "check": job.check,
                    "done": job.done.is_set(), "command": job.command, "rehearse": job.rehearse}

    def setup(self, written):
        folder = api.team_folder(self.root, written)
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
        with task.open("w", encoding="utf-8") as stream:
            YAML().dump({"inputs": values}, stream)
        return file, task

    def _guard_tools(self, team):
        # The CLI permits explicitly configured external folders; the browser root does not.
        for member in team.members.values():
            if member.kind != "toolkeeper" or member.tool != "run_code":
                continue
            terms = [member.base, member.others, *member.access.values()]
            for term in terms:
                for written in term.guardrails.get("paths", []) if term else []:
                    path = Path(written).expanduser()
                    path = path if path.is_absolute() else team.package_top / path
                    path = path.resolve()
                    if not path.is_relative_to(self.root):
                        raise api.OutsideRoot("A run_code folder points outside the opened folder.")
                    for directory, dirs, names in os.walk(path, followlinks=False):
                        for name in dirs + names:
                            link = Path(directory) / name
                            if link.is_symlink() and not link.resolve().is_relative_to(self.root):
                                raise api.OutsideRoot("A run_code folder contains a link outside the opened folder.")

    def start(self, data):
        with self.lock:
            if self.closing:
                raise api.Conflict("The local server is stopping.")
            if self.active and not self.jobs[self.active].done.is_set():
                raise api.Conflict("A run is already in progress. Stop it or wait for it to finish.")
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
        except Exception:
            with self.lock:
                job.state, job.reason = "failed", "The run could not finish. Any events already written remain in its run folder."
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
