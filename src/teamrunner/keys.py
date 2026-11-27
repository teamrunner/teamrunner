# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""API keys (D99): they live in `~/.teamrunner/.env`, under the provider's
own names. A key found only in the shell, or in a `.env` inside the
package, is offered a move there; it is never copied without consent.

Nothing here prints or logs a key — only where it came from.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .loader import RUNS_DIR

PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "together_ai": "TOGETHERAI_API_KEY",
    "cohere": "COHERE_API_KEY",
    "fireworks_ai": "FIREWORKS_AI_API_KEY",
}
NO_KEY = frozenset({"ollama"})  # local; OLLAMA_API_BASE may set the address (D100)
# Providers with several settings of their own, set up as LiteLLM documents them.
SET_UP_ELSEWHERE = frozenset({"azure", "bedrock", "vertex_ai"})

LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")

Ask = Callable[[str, list[str]], str]  # a question and its answers -> the answer chosen


HIDDEN = "[key hidden]"


def hide(value, secrets):
    """`value` with every key in `secrets` replaced by `[key hidden]`, inside strings,
    lists and dicts at any depth: what a provider's error echoes never reaches a run
    folder people share (review 2026-11-16, S8). Values under 8 characters are not
    keys and are left alone, so a stray short value cannot blank ordinary words."""
    secrets = [s for s in secrets if s and len(s) >= 8]
    if not secrets:
        return value
    return _hide(value, sorted(set(secrets), key=len, reverse=True))


def _hide(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            if secret in value:
                value = value.replace(secret, HIDDEN)
        return value
    if isinstance(value, dict):
        return {k: _hide(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_hide(v, secrets) for v in value]
    return value


def key_file() -> Path:
    home = os.environ.get("TEAMRUNNER_HOME")
    return (Path(home) if home else Path.home() / ".teamrunner") / ".env"


class KeyFileError(ValueError):
    """A key file that cannot be read as text. Its message is for the person, whole."""


def env_text(path: Path) -> str:
    """A key file's text, however a Windows tool saved it (review 2026-11-21, NC2): Notepad's
    "UTF-8" starts the file with a mark that hid the first key, and PowerShell 5's
    `echo KEY=… > .env` writes UTF-16, which brought every command down."""
    raw = path.read_bytes()
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16")
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise KeyFileError(f"{path} is not plain text that TeamRunner can read; save it as UTF-8 "
                           "(in Notepad: Save As, Encoding: UTF-8), one NAME=value on each line") from None


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if not path.is_file():
        return values
    for text in env_text(path).splitlines():
        if text.strip().startswith("#"):
            continue
        m = LINE.match(text)
        if m:
            value = m[2]
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[m[1]] = value
    return values


def switch(name: str) -> str:
    """A TeamRunner setting such as `TEAMRUNNER_NO_CRASH_REPORTS`: from the shell, or, for
    someone who has never set a shell variable, a `NAME=value` line in the key file
    (review 2026-11-21, ND10). Lower case, without spaces; empty when it is not set."""
    value = os.environ.get(name, "")
    if not value.strip():
        try:
            value = read_env(key_file()).get(name, "")
        except (OSError, ValueError):
            value = ""
    return value.strip().lower()


def stored_secrets() -> list[str]:
    """The key file's values to hide in anything shown or kept. TeamRunner's own settings
    there (`TEAMRUNNER_WORKSPACE`, a folder) are not secrets: hiding them turned every path
    in a crash's message into `[key hidden]`. Never raises."""
    try:
        return [value for name, value in read_env(key_file()).items()
                if value and not name.startswith("TEAMRUNNER_")]
    except (OSError, ValueError):
        return []


def _make_private(path: Path) -> bool:
    """A key file made by hand is often readable by everyone on the computer; make it
    the owner's alone (review 2026-11-16, D28). True if it had to be changed. Not on
    Windows, where the profile folder's permissions do this."""
    if os.name == "nt" or not path.is_file():
        return False
    try:
        if path.stat().st_mode & 0o077:
            path.chmod(0o600)
            return True
    except OSError:
        pass
    return False


def save_key(path: Path, name: str, value: str) -> None:
    """Put a key in the key file, readable only by its owner (D99). A key already
    there under that name is replaced, so the old value does not stay in the file;
    the file is never readable by others, not even for a moment (review 2026-11-16, S10)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = env_text(path) if path.exists() else ""
    kept = [line for line in existing.splitlines(keepends=True)
            if not ((m := LINE.match(line)) and m[1] == name)]
    if kept and not kept[-1].endswith("\n"):
        kept[-1] += "\n"
    text = "".join(kept) + f"{name}={value}\n"
    temporary = path.with_name(path.name + ".new")
    temporary.unlink(missing_ok=True)
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    os.replace(temporary, path)
    path.chmod(0o600)


def remove_key(path: Path, name: str) -> None:
    """Take a key out of a package `.env`; delete the file if nothing else is left."""
    kept = [line for line in env_text(path).splitlines(keepends=True)
            if not ((m := LINE.match(line)) and m[1] == name)]
    if any(line.strip() and not line.strip().startswith("#") for line in kept):
        path.write_text("".join(kept), encoding="utf-8")
    else:
        path.unlink()


def package_envs(top: Path) -> list[Path]:
    return [p for p in sorted(top.rglob(".env"))
            if p.is_file() and p.relative_to(top).parts[0] != RUNS_DIR]


@dataclass
class Keys:
    values: dict[str, str] = field(default_factory=dict)  # name -> key, for the model calls
    sources: dict[str, str] = field(default_factory=dict)  # name -> key_file | shell_once | package
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # what the console says about where keys came from
    warnings: list[str] = field(default_factory=list)


def find_keys(names: list[str], top: Path | None, ask: Ask | None, env: dict | None = None,
              keyfile: Path | None = None) -> Keys:
    """Look up each key: the key file first, then the shell, then the package.
    `ask` is None when no one is at the keyboard (D99, D101's --quiet)."""
    env = os.environ if env is None else env
    keyfile = keyfile or key_file()
    stored = read_env(keyfile)
    keys_made_private = _make_private(keyfile)
    in_package = {p: read_env(p) for p in package_envs(top)} if top else {}  # None: no package yet
    keys = Keys()
    shown = f"~/{keyfile.relative_to(Path.home()).as_posix()}" if keyfile.is_relative_to(Path.home()) else str(keyfile)

    if keys_made_private:
        keys.notes.append(f"{shown} could be read by other people on this computer; it is now yours alone")
    for name in names:
        held = [p for p, values in in_package.items() if name in values]
        if name in stored:
            keys.values[name], keys.sources[name] = stored[name], "key_file"
            for p in held:
                where = p.relative_to(top.parent)
                if in_package[p][name] != stored[name]:
                    keys.warnings.append(f"{where} holds a different {name}; the one in {shown} is used")
                if ask and ask(f"{where} also holds {name}. Remove it from the package?",
                               ["Yes", "No"]) == "Yes":
                    remove_key(p, name)
        elif env.get(name):
            keys.values[name] = env[name]
            if ask and ask(f"{name} isn't in {shown} but is set in your shell. Save it there?",
                           ["Yes", "Use it just this once"]) == "Yes":
                save_key(keyfile, name, env[name])
                keys.sources[name] = "key_file"
            else:
                keys.sources[name] = "shell_once"
                keys.notes.append(f"{name} is used from your shell for this run only")
        elif held:
            p = held[0]
            where = p.relative_to(top.parent)
            keys.values[name], keys.sources[name] = in_package[p][name], "package"
            if ask and ask(f"{where} holds {name}. Anyone you send this folder to gets your key. "
                           f"Move it to {shown} and remove it here?", ["Yes", "No"]) == "Yes":
                save_key(keyfile, name, in_package[p][name])
                remove_key(p, name)
                keys.sources[name] = "key_file"
            else:
                keys.warnings.append(f"{where} holds {name}; anyone you send this folder to gets your key")
        else:
            keys.missing.append(name)
    return keys


def extra_settings(keyfile: Path | None = None) -> dict[str, str]:
    """Machine settings from the key file, such as OLLAMA_API_BASE (D100)."""
    values = read_env(keyfile or key_file())
    demo_base = os.environ.get("TEAMRUNNER_DEMO_API_BASE") or values.get("TEAMRUNNER_DEMO_API_BASE")
    settings = {k: v for k, v in values.items()
            if not k.endswith("_KEY") and not k.startswith("TEAMRUNNER_")}  # ours are not the provider's
    if demo_base:
        settings["TEAMRUNNER_DEMO_API_BASE"] = demo_base
    return settings
