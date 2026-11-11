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
    later: dict[str, str]  # word -> the version it arrives in
    retired: dict[str, str]  # word -> what to do instead


TEAM = Words(
    known=frozenset({"id", "parent_id", "name", "description", "default_model",
                     "message_goes_to", "default_inputs"}),
    later={"uses": "0.3", "report_from": "0.3"},
    retired={
        "spec_version": "files carry no version line; delete this line",
        "defaults": "put `model:` on each worker, or give the team `default_model:`",
        "channels": "who sees what is `whiteboard.yaml` (0.3); in 0.1 everyone reads everything said",
    },
)

MEMBER = Words(
    known=frozenset({"id", "parent_id", "description", "kind", "model", "params",
                     "output", "tools", "access", "others"}),
    later={"reads": "0.3", "writes": "0.3", "rules": "0.3", "lines": "0.3"},
    retired={
        "name": "a member's name is its filename; rename the file instead and delete this line",
        "spec_version": "files carry no version line; delete this line",
    },
)

MODEL_PROFILE = Words(
    known=frozenset({"name", "params"}),
    later={},
    retired={"spec_version": "files carry no version line; delete this line"},
)

KINDS = frozenset({"llm", "toolkeeper"})
KINDS_LATER = {"rules": "0.3", "announcer": "0.3", "human": "a later version"}
KINDS_RETIRED = {"gate": "toolkeeper"}

# Guardrail words that were renamed (D86).
GUARDRAILS_RETIRED = {"max_calls": "calls_per_turn"}

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
            problems.error(doc.path, f"`{key}` arrives in TeamRunner {words.later[key]}",
                           line=line, value=key,
                           fix="upgrade TeamRunner when it is out, or remove this line")
        else:
            hint = suggest(key, words.known)
            problems.error(doc.path, f"`{key}` is not a word TeamRunner knows in {what}",
                           line=line, value=key,
                           fix=f"did you mean `{hint}`?" if hint else
                           "check the spelling, or upgrade TeamRunner if a newer version uses it")
    return good
