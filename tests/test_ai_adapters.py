"""Provider adapters (E19S1): vendor-agnostic tool-call translation.

The same edit plan expressed in the Anthropic and OpenAI dialects must land
as the *same* ops on the document (differential test). Translators are pure
and offline (the transport is injected); a misbehaving provider is a typed
AdapterError that the AgentRunner absorbs with the document untouched
(E19S3), and token usage accumulates per model instance (E19S4).
"""

from __future__ import annotations

import io
import json

import pytest
from docx import Document

from src.ai.adapters import (
    AdapterError,
    AnthropicModel,
    OpenAIModel,
    anthropic_calls,
    normalize_usage,
    openai_calls,
)
from src.ai.runner import AgentRunner
from src.ai.tools import ToolContext
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir


def _docx_bytes(text: str = "grounding base") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def ctx(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "plan.docx")
    store.put_content("doc1", _docx_bytes())
    yield ToolContext(store=store, hub=CollabHub())
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


# ----------------------------------------------------------------------
# The same plan in two vendor dialects (raw response fixtures)

def _anthropic_step(tool_name: str, tool_input: dict, call_id: str = "tu_1") -> dict:
    return {
        "content": [{"type": "tool_use", "id": call_id, "name": tool_name, "input": tool_input}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _openai_step(tool_name: str, tool_input: dict, call_id: str = "call_1") -> dict:
    return {
        "choices": [{"message": {"tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": tool_name, "arguments": json.dumps(tool_input)}},
        ]}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }


_EDIT = {"doc_id": "doc1", "client_id": "agent=alfie",
         "ops": [{"t": "ins", "at": 14, "text": " extended"}]}


def _anthropic_transport(responses):
    it = iter(responses)
    return lambda request: next(it)


def _openai_transport(responses):
    it = iter(responses)
    return lambda request: next(it)


# ----------------------------------------------------------------------
# Differential test (E19S1): identical outcome from both dialects

def test_both_producers_translate_same_plan_to_same_ops(ctx, tmp_path):
    """Anthropic and OpenAI dialects of ONE edit plan -> identical document
    state, identical reports, byte-identical hub snapshots."""
    # per-provider isolated worlds (distinct db/content paths)
    def world(tag):
        store = DocumentStore(str(tmp_path / f"w{tag}.db"), str(tmp_path / f"w{tag}content"))
        store.init("doc1", "plan.docx")
        store.put_content("doc1", _docx_bytes())
        return ToolContext(store=store, hub=CollabHub())

    ctx_a = world("a")
    ctx_o = world("o")

    model_a = AnthropicModel(_anthropic_transport([
        _anthropic_step("read_doc", {"doc_id": "doc1"}),
        _anthropic_step("apply_ops", _EDIT),
    ]))
    model_o = OpenAIModel(_openai_transport([
        _openai_step("read_doc", {"doc_id": "doc1"}),
        _openai_step("apply_ops", _EDIT),
    ]))

    report_a = AgentRunner(model_a).run(ctx_a, "doc1", "agent=alfie", "extend the text")
    report_o = AgentRunner(model_o).run(ctx_o, "doc1", "agent=alfie", "extend the text")

    assert report_a.text == report_o.text == "grounding base extended"
    assert report_a.ops_applied == report_o.ops_applied == 1
    assert report_a.rev == report_o.rev
    assert report_a.stopped_reason == report_o.stopped_reason == "done"
    assert ctx_a.hub.ensure("doc1", "").snapshot()["text"] == \
        ctx_o.hub.ensure("doc1", "").snapshot()["text"] == "grounding base extended"


def test_translators_agree_on_the_same_calls():
    calls_a, _ = anthropic_calls(_anthropic_step("lock", {"doc_id": "d", "action": "lock", "token": "t"}))
    calls_o, _ = openai_calls(_openai_step("lock", {"doc_id": "d", "action": "lock", "token": "t"}))
    assert calls_a == calls_o == [{"name": "lock",
                                   "arguments": {"doc_id": "d", "action": "lock", "token": "t"}}]


# ----------------------------------------------------------------------
# Usage accounting (E19S4)

def test_usage_normalization_and_accumulation():
    assert normalize_usage({"input_tokens": 3, "output_tokens": 4}) == {"input_tokens": 3, "output_tokens": 4}
    assert normalize_usage({"prompt_tokens": 5, "completion_tokens": 6}) == {"input_tokens": 5, "output_tokens": 6}
    assert normalize_usage(None) is None
    assert normalize_usage({"weird": 1}) is None

    model = AnthropicModel(_anthropic_transport([
        _anthropic_step("read_doc", {"doc_id": "d"}),
        _anthropic_step("get_versions", {"doc_id": "d"}),
    ]))
    model([])  # first call
    model([])  # second call
    assert model.usage == {"input_tokens": 20, "output_tokens": 10}


def test_openai_arguments_must_decode():
    bad = {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "read_doc", "arguments": "{not json"}},
    ]}}]}
    with pytest.raises(AdapterError):
        openai_calls(bad)


# ----------------------------------------------------------------------
# Typed failures, no document corruption (E19S3)

def test_provider_fault_leaves_document_untouched(ctx):
    """Transport raises -> runner stops cleanly; the document is unchanged."""

    def exploding_transport(request):
        raise ConnectionError("provider down")

    model = AnthropicModel(exploding_transport)
    report = AgentRunner(model).run(ctx, "doc1", "agent=alfie", "edit me")
    assert report.stopped_reason == "done"
    assert report.ops_applied == 0
    assert ctx.hub.ensure("doc1", "").snapshot()["text"] == "grounding base"


def test_malformed_vendor_response_is_typed_adapter_error(ctx):
    with pytest.raises(AdapterError):
        anthropic_calls({"choices": "wrong-dialect"})
    with pytest.raises(AdapterError):
        openai_calls({"content": []})
    # and through a transport: absorbed by the runner, doc intact
    model = OpenAIModel(lambda request: {"total_tokens": 1})
    report = AgentRunner(model).run(ctx, "doc1", "agent=alfie", "edit me")
    assert report.ops_applied == 0
    assert ctx.hub.ensure("doc1", "").snapshot()["text"] == "grounding base"


def test_transports_receive_canonical_request_shapes(ctx):
    seen = []

    def anthro(req):
        seen.append(("anthropic", req))
        return {"content": [], "usage": {"input_tokens": 1, "output_tokens": 1}}

    def openai(req):
        seen.append(("openai", req))
        return {"choices": [{"message": {}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    AnthropicModel(anthro)([{"role": "task", "content": "t"}])
    OpenAIModel(openai)([{"role": "task", "content": "t"}])
    kinds = dict(seen)
    assert kinds["anthropic"]["messages"][0]["role"] == "user"
    assert kinds["anthropic"]["messages"][0]["content"].startswith("task:")
    assert kinds["openai"]["messages"] == kinds["anthropic"]["messages"]
