"""Stateful model-based testing of the TextCRDT collaboration core.

Two Hypothesis ``RuleBasedStateMachine`` suites exercise the two hardest
properties of the tombstone-RGA sequence CRDT, going beyond the hand-written
and looser seeded-random tests in ``test_collab.py``:

* **Single-replica conformance** — a reference ``list[str]`` model mirrors
  every insert/delete the machine can generate (including clamping cases);
  after *every* step the CRDT's materialized text must equal the model
  exactly, so divergence is caught the moment it appears, not only at the
  end of a run.

* **Multi-replica convergence under arbitrary delivery** — one leader
  applies edits locally while three followers receive the same op stream
  through per-follower queues the machine may drain in any order: FIFO, or
  a random out-of-order pop (reordered delivery), with arbitrary lag. The
  base-document seed op is shipped like any other op — exactly how the hub
  replays the stored document to late joiners. An invariant holds that any
  follower whose queue has been drained fully has converged to the leader;
  at teardown the remaining queues are flushed and a late-joining replica
  that replays the full op log in generation order must also converge.

This is model-based / stateful property testing — the state of the art for
CRDTs, because the machine explores interleavings no human would write by
hand (delete-before-insert, insert-before-parent, reordered delivery,
duplicate-free but arbitrarily delayed replay).
"""

from __future__ import annotations

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from src.editor.collab import TextCRDT

# Printable unicode that must survive round-trips (control/surrogate/format
# characters are not representable in the emitted document formats).
_SAFE_CHARS = st.characters(
    blacklist_categories=("Cc", "Cs", "Cf", "Zl", "Zp"), max_codepoint=0x1FFFF
)
_ST_TEXT = st.text(alphabet=_SAFE_CHARS, min_size=0, max_size=6)
# Generous upper bound — both the CRDT and the reference model clamp, and
# clamping equivalence is part of the contract under test.
_MAX_INDEX = 24

_PROP = settings(max_examples=40, stateful_step_count=100, deadline=None)


@st.composite
def _insert_step(draw) -> tuple[int, str]:
    """A (position, text) pair; the position is clamped at apply time."""
    position = draw(st.integers(-_MAX_INDEX, _MAX_INDEX))
    text = draw(_ST_TEXT)
    return position, text


@st.composite
def _delete_step(draw) -> tuple[int, int]:
    """A (start, length) pair; both ends are clamped at apply time."""
    position = draw(st.integers(-_MAX_INDEX, _MAX_INDEX))
    length = draw(st.integers(0, 6))
    return position, length


# ---------------------------------------------------------------------------
# 1. Single-replica model conformance
# ---------------------------------------------------------------------------

@_PROP
class SingleReplicaModel(RuleBasedStateMachine):
    """CRDT text must equal a reference list[str] after every single step."""

    def __init__(self) -> None:
        super().__init__()
        base = "αβ base-seed γδ"
        self.crdt = TextCRDT("site-A", initial_text=base)
        self.model = list(base)

    @rule(step=_insert_step())
    def do_insert(self, step: tuple[int, str]) -> None:
        position, text = step
        pos = max(0, min(position, len(self.model)))
        self.model[pos:pos] = list(text)
        self.crdt.local_insert(position, text)

    @rule(step=_delete_step())
    def do_delete(self, step: tuple[int, int]) -> None:
        # Mirror local_delete's exact clamp: both ends are clamped from the
        # RAW positions (NOT from the already-clamped start), so a negative
        # start with a non-positive end collapses to an empty range.
        position, length = step
        start = max(0, min(position, len(self.model)))
        end = max(start, min(position + length, len(self.model)))
        del self.model[start:end]
        self.crdt.local_delete(position, position + length)

    @invariant()
    def text_matches_reference(self) -> None:
        assert self.crdt.to_string() == "".join(self.model), (
            "CRDT diverged from reference model\n"
            f"crdt = {self.crdt.to_string()!r}\n"
            f"model= {''.join(self.model)!r}"
        )

    @invariant()
    def alive_count_matches(self) -> None:
        assert self.crdt.alive_count == len(self.model)


# ---------------------------------------------------------------------------
# 2. Multi-replica convergence under arbitrary delivery
# ---------------------------------------------------------------------------


@_PROP
class MultiReplicaConvergence(RuleBasedStateMachine):
    """Followers receiving arbitrary delivery orders converge to the leader."""

    N_FOLLOWERS = 3

    def __init__(self) -> None:
        super().__init__()
        base = "converge me"
        self.leader = TextCRDT("leader", initial_text=base)
        self.model = list(base)
        # Followers start EMPTY — the base document arrives as the seed op,
        # shipped through the same delivery queues as every other op, exactly
        # like the hub replays the stored document to late-joining editors.
        self.followers = [TextCRDT(f"F{i}") for i in range(self.N_FOLLOWERS)]
        self.queues: list[list[dict]] = [[] for _ in range(self.N_FOLLOWERS)]
        self.op_log: list[dict] = [self.leader.seed_op]
        for queue in self.queues:
            queue.append(self.leader.seed_op)

    @invariant()
    def leader_matches_reference(self) -> None:
        assert self.leader.to_string() == "".join(self.model), (
            "leader CRDT diverged from its reference model\n"
            f"leader = {self.leader.to_string()!r}\n"
            f"model  = {''.join(self.model)!r}"
        )

    @invariant()
    def fully_synced_followers_converge(self) -> None:
        for i, (follower, queue) in enumerate(zip(self.followers, self.queues)):
            if not queue:  # drained everything generated so far -> must match
                assert follower.to_string() == self.leader.to_string(), (
                    f"follower {i} fully-synced but NOT converged\n"
                    f"leader   = {self.leader.to_string()!r}\n"
                    f"follower = {follower.to_string()!r}"
                )

    @rule(position=st.integers(-_MAX_INDEX, _MAX_INDEX), text=_ST_TEXT)
    def insert(self, position: int, text: str) -> None:
        pos = max(0, min(position, len(self.model)))
        self.model[pos:pos] = list(text)
        op = self.leader.local_insert(position, text)
        self.op_log.append(op)
        for queue in self.queues:
            queue.append(op)

    @rule(position=st.integers(-_MAX_INDEX, _MAX_INDEX), length=st.integers(0, 6))
    def delete(self, position: int, length: int) -> None:
        start = max(0, min(position, len(self.model)))
        end = max(start, min(position + length, len(self.model)))
        del self.model[start:end]
        op = self.leader.local_delete(position, position + length)
        self.op_log.append(op)
        for queue in self.queues:
            queue.append(op)

    @rule(follower=st.integers(0, N_FOLLOWERS - 1))
    def deliver_oldest(self, follower: int) -> None:
        """FIFO delivery (in-order transport, variable lag)."""
        queue = self.queues[follower]
        if not queue:
            return
        self.followers[follower].integrate(queue.pop(0))

    @rule(follower=st.integers(0, N_FOLLOWERS - 1), slot=st.integers(0, 63))
    def deliver_out_of_order(self, follower: int, slot: int) -> None:
        """Out-of-order delivery — pops from an arbitrary queue position."""
        queue = self.queues[follower]
        if not queue:
            return
        index = min(slot, len(queue) - 1)
        self.followers[follower].integrate(queue.pop(index))

    def teardown(self) -> None:
        # Flush every remaining op to each follower, then require convergence.
        for follower, queue in zip(self.followers, self.queues):
            for op in queue:
                follower.integrate(op)
            queue.clear()
        leader_text = self.leader.to_string()
        for i, follower in enumerate(self.followers):
            assert follower.to_string() == leader_text, (
                f"follower {i} diverged after full delivery\n"
                f"leader   = {leader_text!r}\n"
                f"follower = {follower.to_string()!r}"
            )
        # Late joiner: replay the full op log (seed first) in generation order.
        late = TextCRDT("late")
        for op in self.op_log:
            late.integrate(op)
        assert late.to_string() == leader_text, (
            "late-join replay diverged\n"
            f"leader = {leader_text!r}\nlate   = {late.to_string()!r}"
        )


TestSingleReplicaModel = SingleReplicaModel.TestCase
TestMultiReplicaConvergence = MultiReplicaConvergence.TestCase
