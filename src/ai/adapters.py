"""Vendor response translators + transport-wrapped model callables.

The server never talks to a model vendor (no SDK dependency, no egress).
A *provider adapter* is the caller-side seam that makes any vendor usable
with :class:`ai.runner.AgentRunner`:

* :func:`anthropic_calls` / :func:`openai_calls` — pure translators from a
  vendor response dict to the server-side model contract
  (``[(name, arguments), ...]`` tool calls + normalized token usage).
  They raise :class:`AdapterError` on malformed responses — never guess.
* :class:`AnthropicModel` / :class:`OpenAIModel` — :data:`~ai.runner.ModelFn`
  callables built around an injected *transport callable*
  (``request_dict -> response_dict``). Tests inject script transports; a
  deployment injects an HTTP transport (one ``httpx.post``), keeping the
  server offline by construction.

Both providers translate the *same* tool-call plan into the *same* ops —
differential-tested — so the model behind an agent is swappable without
changing document outcomes. Token usage is accumulated per model instance
(``model.usage``) for cost accounting (E19S4).
"""

from __future__ import annotations

import json
from typing import Any, Callable

#: transport callable: vendor request dict -> vendor response dict
Transport = Callable[[dict[str, Any]], dict[str, Any]]


class AdapterError(Exception):
    """A vendor response did not match the expected dialect. Raised, never
    silently swallowed: the AgentRunner treats it as 'model misbehaved'
    (zero calls, loop stops) and the document stays untouched."""


def normalize_usage(usage: Any) -> dict[str, int] | None:
    """Normalize vendor usage to ``{"input_tokens", "output_tokens"}``.

    Accepts the Anthropic shape (``input_tokens``/``output_tokens``) and the
    OpenAI shape (``prompt_tokens``/``completion_tokens``). Returns ``None``
    for absent/malformed usage instead of raising — accounting must not
    break runs.
    """
    if not isinstance(usage, dict):
        return None
    if isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
        return {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]}
    if isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int):
        return {"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"]}
    return None


def anthropic_calls(response: Any) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    """Translate an Anthropic messages response to server tool calls.

    Tool calls are the ``tool_use`` content blocks, in order; ``input`` is
    already a dict in this dialect.
    """
    if not isinstance(response, dict):
        raise AdapterError("anthropic response must be an object")
    content = response.get("content")
    if not isinstance(content, list):
        raise AdapterError("anthropic response missing content list")
    calls: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name")
            args = block.get("input")
            if not isinstance(name, str) or not name:
                raise AdapterError("tool_use block without a name")
            calls.append({"name": name, "arguments": args if isinstance(args, dict) else {}})
    return calls, normalize_usage(response.get("usage"))


def openai_calls(response: Any) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    """Translate an OpenAI chat-completions response to server tool calls.

    Tool calls are ``choices[0].message.tool_calls[*].function``, where
    ``arguments`` is a JSON *string* in this dialect and is decoded here.
    """
    if not isinstance(response, dict):
        raise AdapterError("openai response must be an object")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise AdapterError("openai response missing choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise AdapterError("openai response missing message")
    calls: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn["name"]:
            raise AdapterError("tool_call without a function name")
        raw = fn.get("arguments", "{}")
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            raise AdapterError(f"tool_call arguments are not valid JSON: {exc}") from exc
        calls.append({"name": fn["name"], "arguments": args if isinstance(args, dict) else {}})
    return calls, normalize_usage(response.get("usage"))


class _ProviderModel:
    """Shared plumbing: transcript -> vendor request -> transport -> calls."""

    #: class must override these
    _parse: Callable[[Any], tuple[list[dict[str, Any]], dict[str, int] | None]]

    def __init__(self, transport: Transport, model: str = "stub") -> None:
        if not callable(transport):
            raise TypeError("transport must be callable(request) -> response")
        self._transport = transport
        self._model = model
        self._usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

    @property
    def usage(self) -> dict[str, int]:
        """Accumulated normalized token usage across all calls (E19S4)."""
        return dict(self._usage)

    def _record(self, usage: dict[str, int] | None) -> None:
        if usage:
            self._usage["input_tokens"] += usage["input_tokens"]
            self._usage["output_tokens"] += usage["output_tokens"]

    @staticmethod
    def _transcript(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Map the server transcript to plain user/assistant turns. Tool
        results are folded into user turns as tagged JSON — both dialects
        accept it and the model-visible content is identical."""
        turns: list[dict[str, str]] = []
        for msg in messages:
            role, content = msg.get("role"), msg.get("content")
            if role == "task":
                turns.append({"role": "user", "content": f"task: {content}"})
            elif role == "tool":
                turns.append({"role": "user", "content": f"tool result: {json.dumps(content, sort_keys=True)}"})
            else:
                turns.append({"role": "user", "content": str(content)})
        return turns


class AnthropicModel(_ProviderModel):
    """ModelFn over an Anthropic-compatible transport."""

    _parse = staticmethod(anthropic_calls)

    def __call__(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        request = {
            "model": self._model,
            "messages": self._transcript(messages),
            "max_tokens": 1024,
        }
        calls, usage = self._parse(self._transport(request))
        self._record(usage)
        return calls


class OpenAIModel(_ProviderModel):
    """ModelFn over an OpenAI-compatible transport."""

    _parse = staticmethod(openai_calls)

    def __call__(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        request = {
            "model": self._model,
            "messages": self._transcript(messages),
        }
        calls, usage = self._parse(self._transport(request))
        self._record(usage)
        return calls


__all__ = [
    "AdapterError",
    "AnthropicModel",
    "OpenAIModel",
    "Transport",
    "anthropic_calls",
    "normalize_usage",
    "openai_calls",
]
