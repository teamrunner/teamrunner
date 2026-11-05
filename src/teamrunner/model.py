"""The thin interface to models (D20): one call, a request in, a reply out.

The real one, over LiteLLM, arrives with the key and model checks (D99,
D100). Everything above this line speaks only `Model` and `Reply`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    id: str
    name: str  # the toolkeeper asked
    arguments: str  # JSON text, as the model wrote it


@dataclass
class Reply:
    text: str
    model_resolved: str  # the exact model that answered (`gpt-5-2026-08-07`)
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0})
    cost_usd: float | None = None
    latency_ms: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    cost_source: str | None = None  # where the price came from (D120)


class TransientError(Exception):
    """An outage, a rate limit or a timeout: worth trying again after a wait (D100)."""


class Model(Protocol):
    async def complete(self, model: str, params: dict, messages: list[dict],
                       schema: dict | None, tools: list[dict] | None = None) -> Reply:
        """`schema`, when given, is the JSON Schema the reply's text must fit;
        `tools` are the toolkeepers the member may ask, as callable tools.
        Raises TransientError for a failure worth retrying."""
        ...
