# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""run_code's sandbox (D114): Python in WebAssembly (Pyodide), run by Deno.

Deno comes with TeamRunner; Pyodide is fetched once into TeamRunner's own
Deno folder (`~/.teamrunner/deno`) and kept. Deno's permissions are the
walls: it may read only that folder, write only into it, and reach the
internet only to fetch Pyodide's packages — and, unless the toolkeeper
turns `network` on, not at all once the code starts (run.js revokes it).

The code itself never has a network (review 2026-11-16, S6). With `network`
on, its fetch() is passed out to `fetch_for_code` here, which fetches the page
with read_page's rule: never this computer, its local network (IPv6 included)
or a cloud machine's metadata service, redirects included. While Pyodide
loads, before the code starts, Deno also refuses `LOCAL_NETWORK`.
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
MAX_COPY_OUT = 100 * 1024 * 1024  # bytes of files the code may leave in /work
# This computer, private networks, shared and link-local addresses (cloud metadata
# among them), multicast and reserved: refused while Pyodide loads its packages.
# Deno takes IPv4 ranges but IPv6 only as single addresses; the code itself has
# no network at all, so this list is a second wall, not the first.
LOCAL_NETWORK = ("localhost", "[::1]", "[::]", "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
                 "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15",
                 "224.0.0.0/4", "240.0.0.0/4")


class SandboxError(Exception):
    """The sandbox itself could not run (not the code inside it)."""


MARK = b"\x1eteamrunner "  # starts each line run.js sends; see run.js
MAX_LINE = 512 * 1024 * 1024  # the answer carries the work folder's files, base64


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
               "--allow-env", "--allow-net", f"--deny-net={','.join(LOCAL_NETWORK)}", str(SCRIPT)]
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env={**_plain_env(), "DENO_DIR": str(home)}, limit=MAX_LINE)
    errors = asyncio.ensure_future(process.stderr.read())
    writing = asyncio.Lock()
    fetching: set[asyncio.Task] = set()

    async def say(message: dict) -> None:
        async with writing:
            process.stdin.write(json.dumps(message).encode() + b"\n")
            await process.stdin.drain()

    async def answer_fetch(asked: dict) -> None:
        try:
            await say({"id": asked.get("id"), **await fetch_for_code(asked)})
        except (BrokenPipeError, ConnectionResetError):
            pass  # the code finished without waiting for it

    answer = None
    try:
        await say({"code": code, "files": files, "network": network})
        while answer is None:
            line = await process.stdout.readline()
            if not line:
                break
            if not line.startswith(MARK):
                continue  # anything else Deno or Pyodide printed
            message = json.loads(line[len(MARK):])
            if "answer" in message:
                answer = message["answer"]
            elif "fetch" in message and network:
                task = asyncio.ensure_future(answer_fetch(message["fetch"]))
                fetching.add(task)
                task.add_done_callback(fetching.discard)
        await process.wait()
    except asyncio.CancelledError:  # the toolkeeper's timeout: stop the code at once
        process.kill()
        await process.wait()
        raise
    finally:
        for task in list(fetching):
            task.cancel()
        if process.returncode is None:
            process.kill()
            await process.wait()
    err = await errors
    if answer is None or process.returncode != 0:
        raise SandboxError(err.decode(errors="replace").strip()[-500:] or "the sandbox stopped")
    if sum(len(data) * 3 // 4 for data in answer["files"].values()) > MAX_COPY_OUT:
        raise SandboxError("the code left more than 100 MB in its work folder, so none of it was kept")
    written = []
    for rel, data in answer["files"].items():
        target = (work / rel).resolve()
        if target.is_relative_to(work.resolve()):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(data))
            written.append(rel)
    return Ran(answer["stdout"], answer["stderr"], _own_lines(answer["error"]), sorted(written),
               answer["python"])


async def fetch_for_code(asked: dict) -> dict:
    """The code's fetch(), made by TeamRunner with read_page's rule: http and https
    only, never this computer or its local network, checked before every request,
    redirects included. Returns the reply for run.js, or `error`."""
    import httpx
    from ..builtin_tools import MAX_DOWNLOAD, Refused, _public, _refuse_local
    url = str(asked.get("url", ""))
    host = httpx.URL(url).host if url.startswith(("http://", "https://")) else ""
    if not host:
        return {"error": f"TeamRunner fetches only http and https links, not {url[:100]}"}
    if not _public(host):
        return {"error": f"TeamRunner refused {host}: it is on this computer or its local network"}
    headers = [(k, v) for k, v in asked.get("headers") or []
               if k.lower() not in ("host", "content-length", "connection", "accept-encoding")]
    body = base64.b64decode(asked["body"]) if asked.get("body") else None
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30,
                                     event_hooks={"request": [_refuse_local]}) as client:
            async with client.stream(str(asked.get("method") or "GET"), url, headers=headers,
                                     content=body) as response:
                data = b""
                async for chunk in response.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_DOWNLOAD:
                        return {"error": "TeamRunner refused the reply: it is larger than 25 MB"}
    except Refused as e:
        return {"error": f"TeamRunner refused: {e}"}
    except httpx.HTTPError as e:
        return {"error": f"the request failed: {e}"}
    kept = [(k, v) for k, v in response.headers.multi_items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding", "connection")]
    return {"status": response.status_code, "reason": response.reason_phrase, "headers": kept,
            "body": base64.b64encode(data).decode(), "url": str(response.url),
            "redirected": bool(response.history)}


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
