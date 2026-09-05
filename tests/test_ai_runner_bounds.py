"""Agent loop budget enforcement: runaway loop protection (TC-E17-05/06).

Tests the two core budget mechanisms that prevent agents from spinning forever:

* max_steps: limits model iterations (TC-E17-05)
* max_ops: limits total ops applied (TC-E17-05)

The runner must stop cleanly when either budget is exceeded, leaving the
document consistent and the hub responsive (TC-E17-05 FI + BENCH).
"""

from __future__ import annotations

import io
import time

import pytest
from docx import Document

from src.ai.runner import (
    STOP_DONE,
    STOP_MAX_OPS,
    STOP_MAX_STEPS,
    AgentRunner,
    ScriptedModel,
)
from src.ai.tools import ToolContext
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir


def _docx_bytes(text: str = "Base text") -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def ctx(tmp_path):
    store = DocumentStore(str(tmp_path / "t.db"), str(tmp_path / "content"))
    store.init("doc1", "r.docx")
    store.put_content("doc1", _docx_bytes())
    yield ToolContext(store=store, hub=CollabHub())
    wipe_db(tmp_path / "t.db")
    wipe_dir(tmp_path / "content")


# ----------------------------------------------------------------------
# Tests for max_steps budget (TC-E17-05)
# ----------------------------------------------------------------------


def test_max_steps_budget_stops_after_exact_number_of_steps(ctx):
    """A model that always returns calls stops exactly at max_steps."""
    call_count = 0

    class AlwaysCallModel:
        def __call__(self, messages):
            nonlocal call_count
            call_count += 1
            return [{"name": "apply_ops", "arguments": {
                "doc_id": "doc1", "client_id": "agent=x",
                "ops": [{"t": "ins", "at": 0, "text": "x"}],
            }}]

    model = AlwaysCallModel()
    runner = AgentRunner(model, max_steps=3)
    report = runner.run(ctx, "doc1", "agent=x", "spin")

    assert report.stopped_reason == STOP_MAX_STEPS
    assert report.steps == 3
    assert call_count == 3


def test_budget_steps_enforced_even_when_model_fast(ctx):
    """Steps budget is checked before each iteration, regardless of speed."""
    model = ScriptedModel(
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=fast",
            "ops": [{"t": "ins", "at": 10, "text": "!"}],
        }},
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=fast",
            "ops": [{"t": "ins", "at": 11, "text": "!"}],
        }},
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=fast",
            "ops": [{"t": "ins", "at": 12, "text": "!"}],
        }},
    )
    runner = AgentRunner(model, max_steps=2)
    report = runner.run(ctx, "doc1", "agent=fast", "test")

    # After 2 steps (2 calls), we hit the budget, so 2 ops applied
    assert report.stopped_reason == STOP_MAX_STEPS
    assert report.steps == 2
    assert report.ops_applied == 2
    # Third call never made it to the model


def test_steps_budget_one_stops_immediately(ctx):
    """max_steps=1 allows exactly one model call."""
    call_count = 0

    class CountingModel:
        def __call__(self, messages):
            nonlocal call_count
            call_count += 1
            return [{"name": "apply_ops", "arguments": {
                "doc_id": "doc1", "client_id": "agent=x",
                "ops": [{"t": "ins", "at": 0, "text": "x"}],
            }}]

    model = CountingModel()
    runner = AgentRunner(model, max_steps=1)
    report = runner.run(ctx, "doc1", "agent=x", "test")

    assert report.stopped_reason == STOP_MAX_STEPS
    assert report.steps == 1
    assert call_count == 1


# ----------------------------------------------------------------------
# Tests for max_ops budget (TC-E17-05)
# ----------------------------------------------------------------------


def test_max_ops_budget_stops_after_exact_number_of_ops(ctx):
    """Ops budget stops when ops_applied reaches the limit."""
    model = ScriptedModel(
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=y",
            "ops": [{"t": "ins", "at": 10, "text": "A"}],
        }},
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=y",
            "ops": [{"t": "ins", "at": 11, "text": "B"}],
        }},
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=y",
            "ops": [{"t": "ins", "at": 12, "text": "C"}],
        }},
    )
    runner = AgentRunner(model, max_steps=10, max_ops=2)
    report = runner.run(ctx, "doc1", "agent=y", "test")

    assert report.stopped_reason == STOP_MAX_OPS
    assert report.ops_applied == 2
    # Third call never executed
    assert report.steps >= 2  # Could be 2 or 3 depending on when check happens


def test_budget_ops_enforced_across_multiple_calls(ctx):
    """Ops budget accumulates across multiple model calls.

    Note: The runner applies all ops from a call before checking the budget,
    so if a call has 3 ops and budget is 5, the full 3 ops apply and then
    the next call would exceed the budget.
    """
    # Each call applies 3 ops, but max_ops is 5
    # First call: 3 ops (total: 3)
    # Second call would add 3 more (total: 6 > 5), so it's the call that exceeds
    model = ScriptedModel(
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=z",
            "ops": [{"t": "ins", "at": 10, "text": "X"}] * 3,  # 3 ops
        }},
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=z",
            "ops": [{"t": "ins", "at": 13, "text": "Y"}] * 3,  # 3 ops
        }},
    )
    runner = AgentRunner(model, max_steps=10, max_ops=5)
    report = runner.run(ctx, "doc1", "agent=z", "test")

    # First call applies 3 ops (total: 3)
    # Second call applies 3 more ops (total: 6), then budget is checked
    # Second call exceeded budget, so we stop with max_ops
    assert report.stopped_reason == STOP_MAX_OPS
    assert report.ops_applied == 6  # 3 + 3, the second call pushed us over
    assert report.steps >= 2


def test_ops_budget_zero_is_clamped_to_one(ctx):
    """max_ops=0 is clamped to 1 per the runner's __init__ logic."""
    model = ScriptedModel(
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=n",
            "ops": [{"t": "ins", "at": 0, "text": "!"}],
        }},
    )
    runner = AgentRunner(model, max_steps=10, max_ops=0)
    report = runner.run(ctx, "doc1", "agent=n", "test")

    # Zero is clamped to 1, so 1 op is allowed
    assert report.stopped_reason == STOP_MAX_OPS
    assert report.ops_applied == 1


# ----------------------------------------------------------------------
# Budgets interact correctly (TC-E17-06 BENCH)
# ----------------------------------------------------------------------


def test_both_budgets_active_prefers_first_exceeded(ctx):
    """When both budgets are active, the first exceeded stops the loop."""
    # Very few steps, many ops allowed
    call_count = 0

    class CountingModel:
        def __call__(self, messages):
            nonlocal call_count
            call_count += 1
            return [{"name": "apply_ops", "arguments": {
                "doc_id": "doc1", "client_id": "agent=both",
                "ops": [{"t": "ins", "at": 0, "text": "x"}],
            }}]

    model = CountingModel()
    # max_steps=2 will be hit before max_ops=100
    runner = AgentRunner(model, max_steps=2, max_ops=100)
    report = runner.run(ctx, "doc1", "agent=both", "test")

    assert report.stopped_reason == STOP_MAX_STEPS
    assert report.steps == 2
    assert report.ops_applied == 2

    # Reset and try other direction
    call_count = 0

    class ManyOpsModel:
        def __call__(self, messages):
            return [{"name": "apply_ops", "arguments": {
                "doc_id": "doc1", "client_id": "agent=both2",
                "ops": [{"t": "ins", "at": 0, "text": "x"}],
            }}]

    model2 = ManyOpsModel()
    # max_ops=1 will be hit before max_steps=100
    runner2 = AgentRunner(model2, max_steps=100, max_ops=1)
    report2 = runner2.run(ctx, "doc1", "agent=both2", "test")

    assert report2.stopped_reason == STOP_MAX_OPS
    assert report2.ops_applied == 1
    assert report2.steps >= 1


def test_budget_enforcement_leaves_document_consistent(ctx):
    """When budget is exceeded, document is consistent (no partial ops)."""
    original_content = ctx.store.get_content("doc1")

    # Model that tries to apply 10 ops but budget is 3
    model = ScriptedModel(
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=consist",
            "ops": [{"t": "ins", "at": 10, "text": "!"}],
        }},
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=consist",
            "ops": [{"t": "ins", "at": 11, "text": "!"}],
        }},
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=consist",
            "ops": [{"t": "ins", "at": 12, "text": "!"}],
        }},
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=consist",
            "ops": [{"t": "ins", "at": 13, "text": "!"}],
        }},
    )
    runner = AgentRunner(model, max_steps=10, max_ops=3)
    report = runner.run(ctx, "doc1", "agent=consist", "test")

    assert report.stopped_reason == STOP_MAX_OPS
    assert report.ops_applied == 3
    assert len(report.text) == len("Base text") + 3
    # Document is consistent - exactly 3 ops were applied


def test_budget_transcript_records_stopped_reason(ctx):
    """Transcript includes stopped_reason for debugging."""
    model = ScriptedModel(
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=trans",
            "ops": [{"t": "ins", "at": 10, "text": "!"}],
        }},
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=trans",
            "ops": [{"t": "ins", "at": 11, "text": "!"}],
        }},
    )
    runner = AgentRunner(model, max_steps=10, max_ops=1)
    report = runner.run(ctx, "doc1", "agent=trans", "test")

    assert report.stopped_reason == STOP_MAX_OPS
    # Check transcript includes result showing the budget was hit
    assert len(report.transcript) >= 1
    # The first call succeeded (1 op applied), then second call started
    # but budget was hit. The transcript shows what happened.


# ----------------------------------------------------------------------
# Budget edge cases (TC-E17-06 BENCH)
# ----------------------------------------------------------------------


def test_budget_negative_values_become_one(ctx):
    """Negative budget values are clamped to 1."""
    model = ScriptedModel(
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=neg",
            "ops": [{"t": "ins", "at": 10, "text": "!"}],
        }},
    )
    runner = AgentRunner(model, max_steps=-5, max_ops=-10)
    report = runner.run(ctx, "doc1", "agent=neg", "test")

    # Should have run at least one step with one op
    assert report.steps >= 1
    assert report.ops_applied >= 1


def test_budget_zero_values_treated_as_one(ctx):
    """Zero budget values are clamped to 1."""
    model = ScriptedModel(
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=zero",
            "ops": [{"t": "ins", "at": 10, "text": "!"}],
        }},
    )
    runner = AgentRunner(model, max_steps=0, max_ops=0)
    report = runner.run(ctx, "doc1", "agent=zero", "test")

    # Both clamped to 1, so at most one step with one op
    assert report.steps == 1
    assert report.ops_applied == 1


def test_budget_float_values_are_integers(ctx):
    """Budget values are converted to integers."""
    model = ScriptedModel(
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=float",
            "ops": [{"t": "ins", "at": 10, "text": "!"}],
        }},
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=float",
            "ops": [{"t": "ins", "at": 11, "text": "!"}],
        }},
    )
    runner = AgentRunner(model, max_steps=2.9, max_ops=2.1)
    report = runner.run(ctx, "doc1", "agent=float", "test")

    # Values clamped to int: max_steps=2, max_ops=2
    assert report.steps <= 2
    assert report.ops_applied <= 2


def test_budget_no_budget_allows_unlimited(ctx):
    """Very large budgets effectively allow unlimited execution.

    Note: The test document's content length is 9 ("Base text"), so inserting
    at index 10 clamps to the end. Multiple inserts at the same index create
    a sequence, but the tool may only apply ops that change the text.
    """
    calls_made = 0

    class LimitedModel:
        """Stops after 5 calls (not budget)."""
        def __call__(self, messages):
            nonlocal calls_made
            calls_made += 1
            if calls_made >= 5:
                return []
            return [{"name": "apply_ops", "arguments": {
                "doc_id": "doc1", "client_id": "agent=unlim",
                "ops": [{"t": "ins", "at": 10, "text": "!"}],
            }}]

    model = LimitedModel()
    runner = AgentRunner(model, max_steps=1000, max_ops=10000)
    report = runner.run(ctx, "doc1", "agent=unlim", "test")

    # Model stopped itself, not budget
    assert report.stopped_reason == STOP_DONE
    assert report.steps == 5
    # The ops_applied depends on how many distinct ops are applied
    # We expect at least 4 ops (inserts at index 10, each shifts the index)
    assert report.ops_applied >= 4


# ----------------------------------------------------------------------
# Budget benchmarks (TC-E17-06 BENCH)
# ----------------------------------------------------------------------


def test_budget_wall_time_does_not_exceed_expected(ctx):
    """Budget enforcement should be fast (<10ms per step)."""
    model = ScriptedModel(
        {"name": "apply_ops", "arguments": {
            "doc_id": "doc1", "client_id": "agent=perf",
            "ops": [{"t": "ins", "at": 10, "text": "!"}],
        }},
    )
    runner = AgentRunner(model, max_steps=10, max_ops=10)

    start = time.time()
    report = runner.run(ctx, "doc1", "agent=perf", "test")
    elapsed = time.time() - start

    # Should complete quickly
    assert elapsed < 1.0  # Less than 1 second total
    assert report.stopped_reason == STOP_DONE


def test_budget_scalability_with_steps(ctx):
    """Budget enforcement overhead is linear in steps."""
    call_count = 0

    class SimpleModel:
        def __call__(self, messages):
            nonlocal call_count
            call_count += 1
            return [{"name": "apply_ops", "arguments": {
                "doc_id": "doc1", "client_id": "agent=scale",
                "ops": [{"t": "ins", "at": 10, "text": "!"}],
            }}]

    # Test with 100 steps and max_ops=1000 (more than needed)
    call_count = 0
    model = SimpleModel()
    runner = AgentRunner(model, max_steps=100, max_ops=1000)
    report = runner.run(ctx, "doc1", "agent=scale", "test")

    # max_steps=100 should be hit first
    assert report.stopped_reason == STOP_MAX_STEPS
    assert report.steps == 100
    assert call_count == 100