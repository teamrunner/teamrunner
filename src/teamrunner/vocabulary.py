# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Every word TeamRunner's files may use, by file (D89).

A word never changes its meaning, so every incompatibility is a word this
TeamRunner does not know: one from a later version, or one retired with a
replacement. Both are errors that name the word before any tokens are spent.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass(frozen=True)
class Words:
    known: frozenset[str]
    later: frozenset[str]  # words planned for a later version
    retired: dict[str, str]  # word -> what to do instead


TEAM = Words(
    known=frozenset({"id", "parent_id", "name", "description", "default_model",
                     "message_goes_to", "default_inputs"}),
    later=frozenset({"uses", "report_from"}),
    retired={
        "spec_version": "files carry no version line; delete this line",
        "defaults": "put `model:` on each worker, or give the team `default_model:`",
        "channels": "who sees what will be `whiteboard.yaml`, in a later version; for now everyone reads everything said",
    },
)

MEMBER = Words(
    known=frozenset({"id", "parent_id", "description", "kind", "model", "params",
                     "output", "tools", "access", "others"}),
    later=frozenset({"reads", "writes", "rules", "lines"}),
    retired={
        "name": "a member's name is its filename; rename the file instead and delete this line",
        "spec_version": "files carry no version line; delete this line",
    },
)

MODEL_PROFILE = Words(
    known=frozenset({"name", "params"}),
    later=frozenset(),
    retired={"spec_version": "files carry no version line; delete this line"},
)

# The settings a file may give a model under `params:`. They shape the reply and
# nothing else. Every other argument LiteLLM takes (`api_base`, `api_key`,
# `extra_headers`, `mock_response`, `model`, …) decides where a call goes, what
# it carries or whether it is made at all, so a team file may not set it: a
# team someone else wrote must not be able to send this user's key elsewhere.
MODEL_SETTINGS = frozenset({
    "temperature", "top_p", "top_k", "max_tokens", "max_completion_tokens", "seed", "stop",
    "presence_penalty", "frequency_penalty", "reasoning_effort", "verbosity", "thinking",
})


def refused_settings(params) -> list[str]:
    """The names under a `params:` that a file may not set."""
    return sorted(str(k) for k in (params or {}) if k not in MODEL_SETTINGS)


def check_params(path, params, problems, line_of=None) -> None:
    """An error for each name under `params:` that is not a model setting."""
    if not isinstance(params, dict):
        return
    for key in refused_settings(params):
        hint = suggest(key, MODEL_SETTINGS)
        problems.error(path, f"`{key}` is not a model setting a file may give under `params:`",
                       line=line_of(key) if line_of else None, value=key,
                       fix=f"did you mean `{hint}`?" if hint else
                       "remove it; `params:` takes " + ", ".join(f"`{s}`" for s in sorted(MODEL_SETTINGS)))


KINDS = frozenset({"llm", "toolkeeper"})
KINDS_LATER = frozenset({"rules", "announcer", "human"})
# How a message says a feature is planned but not here: no version numbers, which
# would not match the releases people install (review 2026-11-16, D16).
LATER = "comes in a later version of TeamRunner"
KINDS_RETIRED = {"gate": "toolkeeper"}

# Guardrail words that were renamed (D86).
GUARDRAILS_RETIRED = {"max_calls": "calls_per_turn"}

# The body + Member writes into a new member's file. A member whose
# instructions are still this (or blank) is refused, so the starter is
# never sent to a model as if it were the instructions.
MEMBER_STARTER = "Write this member’s instructions here."

# Names a member may not have, because the flow or the access table uses them.
RESERVED_NAMES = frozenset({"done", "stop", "others", "everyone", "standard", "deny"})

# Providers `validate --offline` accepts; plain `validate` asks them (D140).
PROVIDERS = frozenset({
    "openai", "anthropic", "gemini", "vertex_ai", "ollama", "mistral", "groq",
    "azure", "bedrock", "xai", "deepseek", "openrouter", "together_ai",
    "cohere", "fireworks_ai", "teamrunner",
})


def suggest(word: str, choices) -> str | None:
    hits = difflib.get_close_matches(word, sorted(choices), n=1, cutoff=0.7)
    return hits[0] if hits else None


def check_words(doc, words: Words, problems, what: str) -> set[str]:
    """Report every top-level word the file should not have.

    Returns the words that passed, so later checks look only at those.
    """
    good = set()
    for key in doc.data:
        line = doc.line(key)
        if not isinstance(key, str):
            problems.error(doc.path, f"`{key}` is not a setting name", line=line)
        elif key in words.known:
            good.add(key)
        elif key in words.retired:
            problems.error(doc.path, f"`{key}` is no longer used in {what}",
                           line=line, value=key, fix=words.retired[key])
        elif key in words.later:
            problems.error(doc.path, f"`{key}` {LATER}",
                           line=line, value=key,
                           fix="upgrade TeamRunner when it is out, or remove this line")
        else:
            hint = suggest(key, words.known)
            problems.error(doc.path, f"`{key}` is not a word TeamRunner knows in {what}",
                           line=line, value=key,
                           fix=f"did you mean `{hint}`?" if hint else
                           "check the spelling, or update TeamRunner (`teamrunner --update`) if a newer version uses it")
    return good
