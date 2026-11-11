# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""The built-in tools a toolkeeper may hold in 0.1 (D105, D108).

Each tool declares the guardrails it understands (D77); a guardrail it
does not declare is a validate error. The tools' code comes later; this
is what `validate` needs to know about them.
"""

GENERAL_GUARDRAILS = frozenset({"calls_per_turn", "calls_per_run", "timeout"})

TOOLS: dict[str, frozenset[str]] = {
    "web_search_brave": frozenset({"max_results", "domains", "keep_results"}),
    "read_page": frozenset({"domains", "max_chars", "types"}),
    "read_file": frozenset({"max_chars", "types"}),
    "run_code": frozenset({"network", "paths"}),
    "read_email": frozenset({"mailbox", "max_chars", "only_input"}),
    "save_draft": frozenset({"mailbox", "max_chars", "reply_only"}),
}

# The kinds of file `types` may list; PDF is the only one in 0.1.
FILE_TYPES = frozenset({"pdf"})


def guardrails_of(tool: str) -> frozenset[str]:
    return GENERAL_GUARDRAILS | TOOLS[tool]
