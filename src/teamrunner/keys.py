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


def key_file() -> Path:
    home = os.environ.get("TEAMRUNNER_HOME")
    return (Path(home) if home else Path.home() / ".teamrunner") / ".env"


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if not path.is_file():
        return values
    for text in path.read_text(encoding="utf-8").splitlines():
        if text.strip().startswith("#"):
            continue
        m = LINE.match(text)
        if m:
            value = m[2]
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[m[1]] = value
    return values


def save_key(path: Path, name: str, value: str) -> None:
    """Add a key to the key file, readable only by its owner (D99)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + f"{name}={value}\n", encoding="utf-8")
    path.chmod(0o600)


def remove_key(path: Path, name: str) -> None:
    """Take a key out of a package `.env`; delete the file if nothing else is left."""
    kept = [line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
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
    in_package = {p: read_env(p) for p in package_envs(top)} if top else {}  # None: no package yet
    keys = Keys()
    shown = f"~/{keyfile.relative_to(Path.home()).as_posix()}" if keyfile.is_relative_to(Path.home()) else str(keyfile)

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
