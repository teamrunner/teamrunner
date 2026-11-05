"""The real model connection, over LiteLLM (D20). Imported only for a real
run, because LiteLLM takes a while to load."""

from __future__ import annotations

import time

from .keys import PROVIDER_KEYS
from .model import Reply, ToolCall, TransientError


def _chain(error: BaseException):
    while error is not None:
        yield error
        error = error.__cause__ or error.__context__


def _root(error: BaseException) -> str:
    last = list(_chain(error))[-1]
    return f"{type(last).__name__}: {last}"


def _broken_install(error: BaseException) -> bool:
    """A missing package or a bug is not an outage: waiting will not fix it (D100)."""
    return any(isinstance(e, (ImportError, AttributeError, TypeError, NameError)) for e in _chain(error))


class LiteLLMModel:
    def __init__(self, keys: dict[str, str], settings: dict[str, str], prices=None):
        import litellm
        litellm.suppress_debug_info = True
        self.litellm = litellm
        self.keys = keys
        self.settings = settings
        self.prices = prices  # prices.Prices, or None for LiteLLM's table only
        self.transient = (litellm.RateLimitError, litellm.ServiceUnavailableError,
                          litellm.APIConnectionError, litellm.Timeout, litellm.InternalServerError,
                          litellm.BadGatewayError)

    async def complete(self, model, params, messages, schema, tools=None):
        provider, _, name = model.partition("/")
        # A member says `ollama/<name>`; LiteLLM's `ollama/` route gave gpt-oss empty replies,
        # while its chat route calls tools and keeps to the answer form (tested 2026-11-02).
        sent = f"ollama_chat/{name}" if provider == "ollama" else model
        kwargs = {"model": sent, "messages": messages, **params}
        if provider in PROVIDER_KEYS and PROVIDER_KEYS[provider] in self.keys:
            kwargs["api_key"] = self.keys[PROVIDER_KEYS[provider]]
        if provider == "ollama" and "OLLAMA_API_BASE" in self.settings:
            kwargs["api_base"] = self.settings["OLLAMA_API_BASE"]
        if schema:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "turn", "schema": schema, "strict": True}}
        if tools:
            kwargs["tools"] = tools
        started = time.monotonic()
        try:
            response = await self.litellm.acompletion(**kwargs)
        except self.transient as e:
            if _broken_install(e):  # LiteLLM reports some of its own failures as connection errors
                raise RuntimeError(f"TeamRunner's model library could not make the call: {_root(e)}") from e
            raise TransientError(str(e)) from e
        latency = int((time.monotonic() - started) * 1000)
        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        if self.prices is not None and model in self.prices.models:  # the person's own price (D120)
            per_in, per_out = self.prices.models[model]
            cost, source = (tokens_in * per_in + tokens_out * per_out) / 1e6, self.prices.shown
        elif provider == "ollama":
            cost, source = 0.0, "a local model"  # costs nothing (D100)
        else:
            from .prices import litellm_version
            source = f"LiteLLM {litellm_version()}'s price table"
            try:
                cost = self.litellm.completion_cost(completion_response=response)
            except Exception:
                cost, source = None, None  # unknown price: recorded as unknown, never guessed
        message = response.choices[0].message
        calls = [ToolCall(c.id, c.function.name, c.function.arguments or "{}")
                 for c in (getattr(message, "tool_calls", None) or [])]
        return Reply(
            text=message.content or "",
            tool_calls=calls,
            # the model as the member names it: the chat route is TeamRunner's business
            model_resolved=(getattr(response, "model", None) or model).replace("ollama_chat/", "ollama/", 1),
            usage={"input": tokens_in, "output": tokens_out},
            cost_usd=cost,
            cost_source=source,
            latency_ms=latency,
        )
