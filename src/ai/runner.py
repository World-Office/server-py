"""AgentRunner — a thin, model-agnostic agent loop.

The server never talks to a model vendor. An :class:`AgentRunner` wraps any
*model callable* — a function from a message transcript to a list of tool
calls — and drives it against the tool surface until the model stops or a
budget trips. Provider SDKs (Claude, GPT, local models) plug in as one-line
adapters on the caller's side; the server-side contract is::

    model(messages: list[dict]) -> list[{"name": str, "arguments": dict}]

An empty list means "done". Because every tool call lands in
:mod:`ai.tools`, the model's edits are compiled into the same observable,
attributable, revertible op stream a human editor produces — the runner adds
loop control (step/op budgets against runaway loops) and a structured report,
nothing else.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from typing import Any

from .tools import ToolContext, call_tool

#: model callable: transcript -> tool calls (empty list = done)
ModelFn = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]

STOP_DONE = "done"
STOP_MAX_STEPS = "max_steps"
STOP_MAX_OPS = "max_ops"


@dataclass
class AgentReport:
    """Structured outcome of one agent run (audit/eval-friendly)."""

    doc_id: str
    client_id: str
    task: str
    steps: int = 0
    ops_applied: int = 0
    rev: int = 0
    text: str = ""
    stopped_reason: str = STOP_DONE
    transcript: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "client_id": self.client_id,
            "task": self.task,
            "steps": self.steps,
            "ops_applied": self.ops_applied,
            "rev": self.rev,
            "text": self.text,
            "stopped_reason": self.stopped_reason,
        }


class AgentRunner:
    """Drive a model callable against the tool surface under budgets."""

    def __init__(
        self,
        model: ModelFn,
        max_steps: int = 25,
        max_ops: int = 200,
    ) -> None:
        if not callable(model):
            raise TypeError("model must be callable(messages) -> list of tool calls")
        self.model = model
        self.max_steps = max(1, int(max_steps))
        self.max_ops = max(1, int(max_ops))

    def run(
        self,
        ctx: ToolContext,
        doc_id: str,
        client_id: str,
        task: str,
        audit: Any = None,
    ) -> AgentReport:
        """Run the loop: read -> (model decides) -> tool calls -> repeat.

        Each iteration appends the tool results to the transcript so the
        model sees the effect of its previous calls. The loop stops when the
        model returns no calls (done), when ``max_steps`` model turns are
        used, or when ``max_ops`` edits have been applied — whichever comes
        first. Budgets are the runaway-loop protection: an agent can never
        spin forever or flood the hub.

        With ``audit`` (a ``DocumentStore``), the finished run leaves an
        audit row (E20) — who ran, when, with what budget and outcome.
        """
        report = AgentReport(doc_id=doc_id, client_id=client_id, task=task)
        messages: list[dict[str, Any]] = [{"role": "task", "content": task}]
        reason_set = False

        while report.steps < self.max_steps and report.ops_applied < self.max_ops:
            calls = self._safe_model(messages, report)
            report.steps += 1
            if not calls:
                report.stopped_reason = STOP_DONE
                reason_set = True
                break
            for call in calls:
                if not isinstance(call, dict):
                    continue
                result = call_tool(ctx, str(call.get("name", "")), call.get("arguments"))
                if result.get("ok") and result.get("applied_count"):
                    report.ops_applied += int(result["applied_count"])
                report.rev = int(result.get("rev", report.rev) or report.rev)
                if isinstance(result.get("text"), str):
                    report.text = result["text"]
                messages.append({"role": "tool", "name": call.get("name"), "result": result})
                report.transcript.append({"call": call, "result": result})
            if report.ops_applied >= self.max_ops:
                report.stopped_reason = STOP_MAX_OPS
                reason_set = True
        else:
            # both budgets exhausted at once: ops is the more informative
            # reason — a reason set inside the loop body wins
            if not reason_set:
                if report.ops_applied >= self.max_ops:
                    report.stopped_reason = STOP_MAX_OPS
                elif report.steps >= self.max_steps:
                    report.stopped_reason = STOP_MAX_STEPS

        if not report.text:
            from .tools import tool_read_doc

            state = tool_read_doc(ctx, doc_id)
            report.text = state.get("text", "") if state.get("ok") else ""
        if audit is not None:
            try:
                from .audit import redact_transcript

                run_id = audit.record_agent_run(
                    doc_id=doc_id,
                    client_id=client_id,
                    task=task,
                    steps=report.steps,
                    ops=report.ops_applied,
                    rev=report.rev,
                    stopped_reason=report.stopped_reason,
                )
                # E20S1: redacted transcript, retention-bounded by the store.
                audit.record_agent_trace(
                    run_id,
                    json.dumps(redact_transcript(report.transcript), ensure_ascii=False),
                )
            except Exception:  # noqa: BLE001 — auditing must never break a run
                pass
        return report

    def _safe_model(self, messages: list[dict[str, Any]], report: AgentReport) -> list:
        """Call the model; a misbehaving model callable yields zero calls
        (loop stops) instead of raising into the server."""
        try:
            calls = self.model(messages)
        except Exception:  # noqa: BLE001 — model adapters are external code
            report.stopped_reason = STOP_DONE
            return []
        return calls if isinstance(calls, list) else []


class ScriptedModel:
    """A deterministic model callable for tests and eval corpora: replays a
    fixed list of tool calls, then stops. This is also the seed of the
    agent-output corpora used by the property/fuzz suites (group 4)."""

    def __init__(self, *calls: dict[str, Any]) -> None:
        self._calls = [dict(c) for c in calls]
        self._i = 0

    def __call__(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._i >= len(self._calls):
            return []
        call = self._calls[self._i]
        self._i += 1
        return [call]


__all__ = [
    "AgentReport",
    "AgentRunner",
    "ModelFn",
    "ScriptedModel",
    "STOP_DONE",
    "STOP_MAX_OPS",
    "STOP_MAX_STEPS",
]
