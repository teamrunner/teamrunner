# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Kumar Muthuraman
"""Key-free client for the TeamRunner-sponsored demo gateway."""
from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx

from .model import Reply, TransientError

DEFAULT_API_BASE = "https://demollm.teamrunner.ai/v1"
PARAMS = frozenset({"temperature", "top_p", "max_tokens", "seed"})


def api_base(settings: dict) -> str:
    base = settings.get("TEAMRUNNER_DEMO_API_BASE", DEFAULT_API_BASE).rstrip("/")
    url = urlsplit(base)
    if (url.username or url.password or url.query or url.fragment or not url.hostname
            or (url.scheme != "https" and not (
                url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise ValueError("TEAMRUNNER_DEMO_API_BASE must be an HTTPS address (HTTP is allowed on localhost)")
    return base


async def complete(model, params, messages, schema, tools, settings) -> Reply:
    if model != "teamrunner/demoLLM":
        raise ValueError("the hosted demo model is teamrunner/demoLLM")
    if tools:
        raise ValueError("teamrunner/demoLLM supports teams without toolkeepers; choose your own model for tools")
    if set(params) - PARAMS:
        raise ValueError("the demo accepts only " + ", ".join(sorted(PARAMS)))
    body = {"model": "demoLLM", "messages": messages, **params}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "turn", "schema": schema, "strict": True}}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=75, follow_redirects=False) as client:
            response = await client.post(api_base(settings) + "/chat/completions", json=body)
    except httpx.TransportError as e:
        raise TransientError("could not reach the TeamRunner demo server; try again later") from e
    if response.status_code != 200:
        # Display only the gateway's deliberately public error, never an upstream response.
        try:
            reason = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            reason = "the demo server could not complete this request"
        error = TransientError if response.status_code in {429, 502, 503, 504} else RuntimeError
        raise error(f"TeamRunner demo: {str(reason)[:300]}")
    data = response.json()
    usage = data.get("usage") or {}
    return Reply(text=data["choices"][0]["message"]["content"],
                 model_resolved="openrouter/" + data["model"].removeprefix("openrouter/"),
                 usage={"input": usage.get("prompt_tokens", 0), "output": usage.get("completion_tokens", 0)},
                 cost_usd=0.0, cost_charged_usd=0.0,
                 cost_source="TeamRunner-sponsored demo (free to you)",
                 latency_ms=int((time.monotonic() - started) * 1000))
