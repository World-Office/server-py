"""Model-based testing of the agent state machine driving apply_ops (TC-E13-05).

Three Hypothesis ``RuleBasedStateMachine`` suites plus deterministic unit tests
exercise the agent tool surface's most load-bearing property: that a *stateful
agent loop* driving ``tool_apply_ops`` converges to a plain reference model of
the same edit sequence — the same guarantee human editor snapshots get, since
agent edits flow through the identical CRDT op pipeline.

* **AgentSingleEditStateMachine** — one agent, one edit at a time. A reference
  ``list[str]`` mirrors every insert/delete the machine can generate (including
  clamping into range); after *every* step the document text returned by
  ``apply_ops`` AND the hub's live state must equal the reference exactly, and
  every applied op must carry the agent's ``client_id`` so the edit is
  attributable in the op stream.

* **AgentBatchStateMachine** — one agent, multi-edit ``apply_ops`` calls. Each
  edit is compiled against the live CRDT (so each sees the text after the
  previous edit in the call); the reference chains the same clamped edits over
  the previous model state. Verifies that batch application is exactly equal to
  the sequential string simulation, nothing lost.

* **MultiAgentStateMachine** — several agents editing one shared document.
  Every edit from every agent converges to the single shared reference model —
  agents are collaboration clients, not isolated writers.

This is stateful model-based testing: the Hypothesis machine explores orderings
(delete-before-insert, out-of-range indices, unicode payloads) no hand-written
test would enumerate, and the invariant fires after every step so divergence is
caught the moment it appears.

Test file for: TC-E13-05 (E13S3, MB) — "Agent state machine driving
``apply_ops`` → text converges to reference model".
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile

from docx import Document
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from src.ai.tools import ToolContext, tool_apply_ops
from src.editor.collab import CollabHub
from src.lib.store import DocumentStore, wipe_db, wipe_dir

# Printable unicode that must survive round-trips (control/surrogate/format
# characters are not representable in the emitted document formats).
_SAFE_CHARS = st.characters(
    blacklist_categories=("Cc", "Cs", "Cf", "Zl", "Zp"), max_codepoint=0x1FFFF
)
_ST_TEXT = st.text(alphabet=_SAFE_CHARS, min_size=0, max_size=5)
# Generous bound — both the tool and the reference clamp, and clamping
# equivalence is part of the contract under test.
_MAX_INDEX = 25

# The docx round-trip per apply_ops call is cheap for a short base, but the
# suites share one store per machine instance, so keep example/step counts
# modest to bound total runtime.
_PROP = settings(
    max_examples=20,
    stateful_step_count=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

DOC_ID = "doc1"


# ----------------------------------------------------------------------
# Helpers: fresh context, reference oracle
# ----------------------------------------------------------------------


def _docx_bytes(text: str) -> bytes:
    """A minimal .docx whose collaborative baseline becomes exactly *text*."""
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_context(
    base_text: str, with_docx: bool = True
) -> tuple[ToolContext, str, str]:
    """Fresh store+hub pair with ``doc1`` registered; returns (ctx, tmpdir, db).

    When *with_docx* the store carries a real docx whose plain-text baseline is
    *base_text* (the realistic "agent edits an existing document" path — the
    tool derives its seed from the stored bytes exactly like the editor does).
    Otherwise the document has no stored content and the hub is seeded directly
    (the "agent builds text from scratch" path), which keeps the machine fast.
    """
    d = tempfile.mkdtemp(prefix="wo-mb-")
    db = os.path.join(d, "t.db")
    store = DocumentStore(db, os.path.join(d, "content"))
    store.init(DOC_ID, "p.docx")
    if with_docx:
        store.put_content(DOC_ID, _docx_bytes(base_text))
    ctx = ToolContext(store=store, hub=CollabHub())
    if not with_docx:
        ctx.hub.ensure(DOC_ID, base_text)
    return ctx, d, db


def _teardown_context(d: str, db: str) -> None:
    wipe_db(db)
    wipe_dir(os.path.join(d, "content"))
    shutil.rmtree(d, ignore_errors=True)


def _reference_edit(text: str, edit: dict) -> str:
    """Plain-string simulation of one clamped agent edit (the oracle).

    Mirrors ``compile_text_edit``'s exact clamping:
    * insert: ``at`` clamps to ``[0, len]`` and text is placed between char
      ``at-1`` and char ``at`` (i.e. at visible index ``at``);
    * delete: both ends clamp against the current alive text, and the end is
      derived from the **raw** start when omitted (``end = raw_at + 1``), NOT
      from the clamped start — a negative start with a non-positive end
      collapses to an empty range and is a no-op.
    """
    if edit["t"] == "ins":
        at = max(0, min(edit["at"], len(text)))
        return text[:at] + edit["text"] + text[at:]
    raw_at = edit.get("at", 0)
    end = edit["end"] if isinstance(edit.get("end"), int) else raw_at + 1
    start = max(0, min(raw_at, len(text)))
    end = max(start, min(end, len(text)))
    return text[:start] + text[end:]


def _reference_batch(text: str, edits: list[dict]) -> str:
    """Chain the oracle over a batch: each edit sees the text after the previous."""
    for edit in edits:
        text = _reference_edit(text, edit)
    return text


# ----------------------------------------------------------------------
# 1. Single agent, single edits per apply_ops call
# ----------------------------------------------------------------------


@_PROP
class AgentSingleEditStateMachine(RuleBasedStateMachine):
    """After every single agent edit, apply_ops text == the reference model.

    The machine holds one persistent store+hub (a document seeded from a real
    docx) and fires one edit per ``apply_ops`` call. The invariant runs after
    every step, so any divergence between the tool's reply, the hub's live
    state, and the reference model is caught immediately — including clamping
    and unicode payload cases.
    """

    def __init__(self) -> None:
        super().__init__()
        self.base = "αβγ base δ"
        self.ctx, self._dir, self._db = _make_context(self.base)
        # Touch the hub so the seed text is exactly self.base (the tool derives
        # the same text from the stored docx — assert they agree).
        assert self.ctx.hub.ensure(DOC_ID, self.base).crdt.to_string() == self.base
        self.model = list(self.base)

    @rule(at=st.integers(-_MAX_INDEX, _MAX_INDEX), text=_ST_TEXT)
    def insert(self, at: int, text: str) -> None:
        """Agent inserts *text* at (possibly out-of-range) position *at*."""
        result = tool_apply_ops(
            self.ctx, DOC_ID, "agent=alpha", [{"t": "ins", "at": at, "text": text}]
        )
        assert result["ok"] is True
        expected = _reference_edit("".join(self.model), {"t": "ins", "at": at, "text": text})
        assert result["text"] == expected, (
            "tool reply diverged from reference after insert\n"
            f"expected = {expected!r}\ngot      = {result['text']!r}"
        )
        self.model = list(expected)

    @rule(
        at=st.integers(-_MAX_INDEX, _MAX_INDEX),
        end=st.one_of(st.none(), st.integers(-_MAX_INDEX, _MAX_INDEX + 3)),
    )
    def delete(self, at: int, end: int | None) -> None:
        """Agent deletes alive chars in ``[at, end)`` (end omitted → single char)."""
        result = tool_apply_ops(
            self.ctx, DOC_ID, "agent=alpha", [{"t": "del", "at": at, "end": end}]
        )
        assert result["ok"] is True
        expected = _reference_edit("".join(self.model), {"t": "del", "at": at, "end": end})
        assert result["text"] == expected, (
            "tool reply diverged from reference after delete\n"
            f"expected = {expected!r}\ngot      = {result['text']!r}"
        )
        self.model = list(expected)

    @invariant()
    def text_converges_to_reference(self) -> None:
        """The live hub text equals the reference model after every step."""
        assert self.ctx.hub.state(DOC_ID)["text"] == "".join(self.model), (
            "hub state diverged from reference model\n"
            f"hub   = {self.ctx.hub.state(DOC_ID)['text']!r}\n"
            f"model = {''.join(self.model)!r}"
        )

    def teardown(self) -> None:
        _teardown_context(self._dir, self._db)


# ----------------------------------------------------------------------
# 2. Single agent, multi-edit batches in one apply_ops call
# ----------------------------------------------------------------------


@_PROP
class AgentBatchStateMachine(RuleBasedStateMachine):
    """A batch of edits in one apply_ops call equals the chained reference.

    Each edit in a batch compiles against the live CRDT — index *i* of edit n
    refers to the text after edits 0..n-1. The oracle chains the same clamped
    edits over the previous model state, so any deviation in batch semantics
    (stale indices, re-clamping, dropped edits) shows up as a mismatch.
    """

    def __init__(self) -> None:
        super().__init__()
        self.base = "truth and beauty"
        self.ctx, self._dir, self._db = _make_context(self.base)
        self.ctx.hub.ensure(DOC_ID, self.base)
        self.model = self.base

    @rule(
        edits=st.lists(
            st.one_of(
                st.fixed_dictionaries(
                    {
                        "t": st.just("ins"),
                        "at": st.integers(-_MAX_INDEX, _MAX_INDEX),
                        "text": _ST_TEXT,
                    }
                ),
                st.fixed_dictionaries(
                    {
                        "t": st.just("del"),
                        "at": st.integers(-_MAX_INDEX, _MAX_INDEX),
                        "end": st.one_of(
                            st.none(), st.integers(-_MAX_INDEX, _MAX_INDEX + 3)
                        ),
                    }
                ),
            ),
            min_size=1,
            max_size=6,
        )
    )
    def batch(self, edits: list[dict]) -> None:
        """One multi-edit apply_ops call; text must equal the chained reference."""
        result = tool_apply_ops(self.ctx, DOC_ID, "agent=batch", edits)
        assert result["ok"] is True
        expected = _reference_batch(self.model, edits)
        assert result["text"] == expected, (
            "batch diverged from chained reference\n"
            f"expected = {expected!r}\ngot      = {result['text']!r}"
        )
        assert result["applied_count"] == len(result["applied"])
        # applied ops are one-per-live-edit and attributable to the agent
        for op in result["applied"]:
            assert op.get("s") == "agent=batch"
        self.model = expected

    @invariant()
    def text_converges_to_reference(self) -> None:
        assert self.ctx.hub.state(DOC_ID)["text"] == self.model, (
            f"hub = {self.ctx.hub.state(DOC_ID)['text']!r}, model = {self.model!r}"
        )

    def teardown(self) -> None:
        _teardown_context(self._dir, self._db)


# ----------------------------------------------------------------------
# 3. Multiple agents editing one shared document
# ----------------------------------------------------------------------


@_PROP
class MultiAgentStateMachine(RuleBasedStateMachine):
    """Agents are collaboration clients: N agents, one converged document.

    Several agent identities interleave edits through the same hub. Every
    agent's edit lands in the single shared text, each op stays attributable
    to its own ``agent=<name>`` site, and the document always equals the shared
    reference model — no isolated writer gets a private view.
    """

    AGENTS = ("agent=one", "agent=two", "agent=three")

    def __init__(self) -> None:
        super().__init__()
        self.base = "shared workspace"
        self.ctx, self._dir, self._db = _make_context(self.base)
        self.ctx.hub.ensure(DOC_ID, self.base)
        self.model = self.base

    @rule(
        agent=st.sampled_from(AGENTS),
        at=st.integers(-_MAX_INDEX, _MAX_INDEX),
        text=_ST_TEXT,
    )
    def insert(self, agent: str, at: int, text: str) -> None:
        """One agent inserts text; everyone shares the same resulting text."""
        result = tool_apply_ops(
            self.ctx, DOC_ID, agent, [{"t": "ins", "at": at, "text": text}]
        )
        assert result["ok"] is True
        expected = _reference_edit(self.model, {"t": "ins", "at": at, "text": text})
        assert result["text"] == expected
        for op in result["applied"]:
            assert op.get("s") == agent, "op attributed to the wrong agent"
        self.model = expected

    @rule(
        agent=st.sampled_from(AGENTS),
        at=st.integers(-_MAX_INDEX, _MAX_INDEX),
        end=st.one_of(st.none(), st.integers(-_MAX_INDEX, _MAX_INDEX + 3)),
    )
    def delete(self, agent: str, at: int, end: int | None) -> None:
        result = tool_apply_ops(
            self.ctx, DOC_ID, agent, [{"t": "del", "at": at, "end": end}]
        )
        assert result["ok"] is True
        expected = _reference_edit(self.model, {"t": "del", "at": at, "end": end})
        assert result["text"] == expected
        for op in result["applied"]:
            assert op.get("s") == agent
        self.model = expected

    @invariant()
    def shared_text_converges(self) -> None:
        assert self.ctx.hub.state(DOC_ID)["text"] == self.model, (
            "multi-agent document diverged\n"
            f"hub   = {self.ctx.hub.state(DOC_ID)['text']!r}\n"
            f"model = {self.model!r}"
        )

    def teardown(self) -> None:
        _teardown_context(self._dir, self._db)


# ----------------------------------------------------------------------
# Deterministic unit tests (gate: file must reference apply_ops and be runnable
# standalone; these pin concrete clamping/batch behaviour the machines explore
# under Hypothesis).
# ----------------------------------------------------------------------


def test_apply_ops_insert_converges_to_reference(tmp_path):
    """A single insert through apply_ops equals the plain-string oracle."""
    ctx, d, db = _make_context("agent base text")
    try:
        # "agent base text" has 15 visible chars; index 15 appends.
        result = tool_apply_ops(
            ctx, DOC_ID, "agent=unit", [{"t": "ins", "at": 15, "text": " ok"}]
        )
        expected = _reference_batch("agent base text", [{"t": "ins", "at": 15, "text": " ok"}])
        assert result["ok"] is True
        assert result["text"] == expected == "agent base text ok"
        assert ctx.hub.state(DOC_ID)["text"] == expected
    finally:
        _teardown_context(d, db)


def test_apply_ops_delete_converges_to_reference(tmp_path):
    """A single delete through apply_ops equals the plain-string oracle."""
    ctx, d, db = _make_context("delete this")
    try:
        result = tool_apply_ops(
            ctx, DOC_ID, "agent=unit",
            [{"t": "del", "at": 0, "end": 7}, {"t": "del", "at": 0, "end": 1}],
        )
        expected = _reference_batch("delete this", [{"t": "del", "at": 0, "end": 7},
                                                     {"t": "del", "at": 0, "end": 1}])
        assert result["ok"] is True
        assert result["text"] == expected == "his"
    finally:
        _teardown_context(d, db)


def test_apply_ops_clamping_matches_reference(tmp_path):
    """Out-of-range indices clamp identically in tool and oracle."""
    ctx, d, db = _make_context("clamp")
    try:
        edits = [
            {"t": "ins", "at": 1000, "text": "Z"},   # beyond end → append
            {"t": "ins", "at": -50, "text": "A"},    # before start → prepend
            {"t": "del", "at": 99, "end": 150},      # beyond end → no-op member
            {"t": "del", "at": 0, "end": -7},        # collapse to empty range
        ]
        result = tool_apply_ops(ctx, DOC_ID, "agent=unit", edits)
        assert result["ok"] is True
        expected = _reference_batch("clamp", edits)
        assert result["text"] == expected == "AclampZ"
    finally:
        _teardown_context(d, db)


def test_apply_ops_long_agent_loop_converges(tmp_path):
    """A 30-edit sequential agent loop: every step matches the chained oracle."""
    ctx, d, db = _make_context(".", with_docx=False)
    try:
        model = "."
        for i in range(30):
            if i % 3 == 0:
                edit = {"t": "ins", "at": i % 7, "text": f"n{i}"}
            elif i % 3 == 1:
                edit = {"t": "ins", "at": len(model), "text": "!"}
            else:
                edit = {"t": "del", "at": max(0, len(model) - 2), "end": None}
            result = tool_apply_ops(ctx, DOC_ID, "agent=loop", [edit])
            assert result["ok"] is True
            model = _reference_edit(model, edit)
            assert result["text"] == model, (
                f"step {i} diverged\n expected = {model!r}\n got      = {result['text']!r}"
            )
        assert ctx.hub.state(DOC_ID)["text"] == model
    finally:
        _teardown_context(d, db)


TestSingle = AgentSingleEditStateMachine.TestCase
TestBatch = AgentBatchStateMachine.TestCase
TestMultiAgent = MultiAgentStateMachine.TestCase
