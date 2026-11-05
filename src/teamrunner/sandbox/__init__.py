"""run_code's sandbox (D114): Python in WebAssembly (Pyodide), run by Deno.

Deno comes with TeamRunner; Pyodide is fetched once into TeamRunner's own
Deno folder (`~/.teamrunner/deno`) and kept. Deno's permissions are the
walls: it may read only that folder, write only into it, and reach the
internet only to fetch Pyodide's packages — and, unless the toolkeeper
turns `network` on, not at all once the code starts (run.js revokes it).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..keys import key_file

SCRIPT = Path(__file__).with_name("run.js")
PYODIDE = "314.0.7"  # pinned in run.js; recorded with every call
MAX_COPY_IN = 100 * 1024 * 1024  # bytes copied in from `paths` and the work folder


class SandboxError(Exception):
    """The sandbox itself could not run (not the code inside it)."""


@dataclass
class Ran:
    stdout: str
    stderr: str
    error: str | None
    written: list[str]  # files the code left in the work folder
    python: str | None


def deno_dir() -> Path:
    """Where Pyodide is kept once fetched; TEAMRUNNER_DENO_DIR moves it (the tests do)."""
    moved = os.environ.get("TEAMRUNNER_DENO_DIR")
    return Path(moved) if moved else key_file().parent / "deno"


def deno_bin() -> str:
    import deno
    return deno.find_deno_bin()


def _gather(folder: Path, mount: str, files: dict, budget: list[int]) -> None:
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            budget[0] -= path.stat().st_size
            if budget[0] < 0:
                raise SandboxError("the folders to copy in hold more than 100 MB")
            rel = path.relative_to(folder).as_posix()
            files[f"{mount}/{rel}"] = base64.b64encode(path.read_bytes()).decode()


async def run(code: str, work: Path, read: list[tuple[Path, str]], network: bool) -> Ran:
    """Run `code` with `work` as its working folder and each folder in `read` copied
    in at the place paired with it. Whatever is in /work afterwards is copied back."""
    files: dict[str, str] = {}
    budget = [MAX_COPY_IN]
    for folder, place in read:
        _gather(folder, place, files, budget)
    work.mkdir(parents=True, exist_ok=True)
    _gather(work, "/work", files, budget)

    home = deno_dir()
    home.mkdir(parents=True, exist_ok=True)
    command = [deno_bin(), "run", "--quiet", "--no-prompt", "--no-config",
               f"--allow-read={home},{SCRIPT}", f"--allow-write={home}",
               "--allow-env", "--allow-net", str(SCRIPT)]
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env={**_plain_env(), "DENO_DIR": str(home)})
    try:
        out, err = await process.communicate(json.dumps(
            {"code": code, "files": files, "network": network}).encode())
    except asyncio.CancelledError:  # the toolkeeper's timeout: stop the code at once
        process.kill()
        await process.wait()
        raise
    if process.returncode != 0:
        raise SandboxError(err.decode(errors="replace").strip()[-500:] or "the sandbox stopped")
    answer = json.loads(out)
    written = []
    for rel, data in answer["files"].items():
        target = (work / rel).resolve()
        if target.is_relative_to(work.resolve()):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(data))
            written.append(rel)
    return Ran(answer["stdout"], answer["stderr"], _own_lines(answer["error"]), sorted(written),
               answer["python"])


def _own_lines(traceback: str | None) -> str | None:
    """A traceback from the code's own lines on, without Pyodide's machinery."""
    if not traceback:
        return traceback
    lines = traceback.rstrip().splitlines()
    first = next((i for i, line in enumerate(lines) if line.lstrip().startswith('File "<exec>"')), None)
    if first is None:
        return traceback.rstrip()
    return "\n".join(["Traceback (most recent call last):", *lines[first:]])


READY = "pyodide-ready"  # left in deno_dir once Pyodide has been fetched and run


def prepare() -> str | None:
    """Fetch and start Pyodide once, before a run's first turn, so the one-time
    download never counts against a toolkeeper's timeout. Returns why it failed."""
    marker = deno_dir() / READY
    if marker.exists():
        return None
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as work:
            ran = asyncio.run(asyncio.wait_for(run("print(1)", Path(work), [], False), 600))
    except (SandboxError, asyncio.TimeoutError, OSError) as e:
        return f"the code sandbox could not be prepared ({e or 'it took over ten minutes'}); " \
               "its first use needs the internet"
    if ran.stdout.strip() != "1":
        return "the code sandbox did not start properly"
    marker.write_text(PYODIDE + "\n", encoding="utf-8")
    return None


def _plain_env() -> dict:
    """Deno gets no keys from the environment: the code must not see them."""
    keep = ("PATH", "HOME", "TMPDIR", "LANG", "SYSTEMROOT", "USERPROFILE")
    # Deno would otherwise ask deno.land once a day for a newer version of itself:
    # nothing leaves the machine that TeamRunner does not say it sends.
    return {**{k: v for k, v in os.environ.items() if k in keep}, "DENO_NO_UPDATE_CHECK": "1"}
