"""Tests for the ``SessionRegistry`` lifecycle.

The registry is a purely in-memory, **ephemeral** store of active editor
sessions: sessions exist only for the lifetime of the process and are
removed explicitly via ``drop()`` when they expire (client disconnect,
failed save, editor closed by the user). There is deliberately no TTL or
persistence — a restart loses all sessions, and ``drop()`` is the expiry
mechanism.

Paradigms:

* **Lifecycle / expiry** — a session is found after ``register`` and gone
  (by id and by doc) after ``drop``; dropping a doc expires *every*
  concurrent session for that file, not just one.

* **Concurrent sessions** — two launches of the SAME file (two users, or
  two tabs of one user) coexist in one registry: the registry keys by the
  unique per-launch ``session_id``, so they never clobber each other, and
  the doc-level ``get()`` shortcut still resolves the *most recent*
  session deterministically by ``created_at``.

* **Isolation** — sessions of different documents are fully independent:
  registering or dropping one document never leaks into another's
  sessions, and ``all()`` reflects exactly the live set, ordered by
  creation time.

* **Model-based** — a Hypothesis state machine mirrors an arbitrary
  register/drop sequence in a reference model; after every step the real
  registry must match the model exactly. A complementary ``@given``
  property test checks the same invariants over random op lists.

All timestamps are supplied explicitly — no time-of-day dependence, no
sleeps, no network.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from src.editor.session import EditorSession, SessionRegistry

# Deterministic creation times, distinct per session, so "most recent" is
# always well-defined and stable across runs.
_T0 = 1000.0


def _session(doc_id: str, *, created_at: float | None = None, user_id: str = "alice") -> EditorSession:
    """A fully-specified, deterministic session for the given document."""
    return EditorSession(
        doc_id=doc_id,
        name=f"{doc_id}.docx",
        size=10,
        version="1",
        last_modified=0,
        user_id=user_id,
        owner_id=user_id,
        created_at=_T0 if created_at is None else created_at,
    )


# ---------------------------------------------------------------------------
# Lifecycle / expiry
# ---------------------------------------------------------------------------

def test_registry_lifecycle_register_get_drop():
    """A session is absent before registration, findable by id and by doc
    after registration, and expires completely once dropped."""
    reg = SessionRegistry()
    s = _session("doc1")
    assert reg.get("doc1") is None
    assert reg.get_by_id(s.session_id) is None
    assert reg.all() == []

    reg.register(s)
    assert reg.get("doc1") is s
    assert reg.get_by_id(s.session_id) is s
    assert reg.all() == [s]

    reg.drop("doc1")
    # expiry is complete: neither the doc-level shortcut nor the id lookup
    # may resurrect the dropped session.
    assert reg.get("doc1") is None
    assert reg.get_by_id(s.session_id) is None
    assert reg.all() == []


def test_drop_expires_every_concurrent_session_for_the_doc():
    """Expiry via ``drop()`` removes ALL live sessions of a document (they
    share the same doc id), not merely the most recent one."""
    reg = SessionRegistry()
    s1 = _session("doc1", created_at=_T0)
    s2 = _session("doc1", created_at=_T0 + 1.0)
    reg.register(s1)
    reg.register(s2)
    assert len(reg.all()) == 2

    reg.drop("doc1")
    assert reg.get("doc1") is None
    assert reg.get_by_id(s1.session_id) is None
    assert reg.get_by_id(s2.session_id) is None
    assert reg.all() == []


def test_expired_sessions_do_not_survive_after_second_register():
    """Re-registering a session id replaces the old entry (the registry is
    keyed by session id), so a restart/relaunch of the same editor yields
    exactly one live session, not a stale duplicate."""
    reg = SessionRegistry()
    s = _session("doc1", created_at=_T0)
    reg.register(s)
    reg.register(s)
    assert reg.all() == [s]
    assert len(reg.all()) == 1


# ---------------------------------------------------------------------------
# Concurrent sessions for the SAME document
# ---------------------------------------------------------------------------

def test_concurrent_sessions_for_same_doc_coexist_without_clobbering():
    """Two launches of the same file (two users, or two tabs) must coexist:
    each keyed by its unique per-launch session id, neither overwriting the
    other, both reachable through the id-exact lookup."""
    reg = SessionRegistry()
    alice = _session("doc1", user_id="alice", created_at=_T0)
    bob = _session("doc1", user_id="bob", created_at=_T0 + 1.0)
    assert alice.session_id != bob.session_id, "sessions must be uniquely keyed"

    reg.register(alice)
    reg.register(bob)
    assert len(reg.all()) == 2
    # id-exact resolution: never the other user's session
    assert reg.get_by_id(alice.session_id) is alice
    assert reg.get_by_id(bob.session_id) is bob
    # the backward-compatible doc shortcut resolves the MOST RECENT launch
    assert reg.get("doc1") is bob


def test_get_resolves_most_recent_launch_by_created_at_not_order():
    """``get(doc_id)`` means "the most recently launched session for this
    file". Creation time decides — even when sessions register out of
    chronological order."""
    reg = SessionRegistry()
    early = _session("doc1", created_at=_T0)
    late = _session("doc1", created_at=_T0 + 1.0)
    # register newest first, oldest second: registration order must NOT win
    reg.register(late)
    reg.register(early)
    assert reg.get("doc1") is late
    # both remain individually addressable regardless of the shortcut
    assert reg.get_by_id(early.session_id) is early
    assert reg.get_by_id(late.session_id) is late


# ---------------------------------------------------------------------------
# Isolation between documents
# ---------------------------------------------------------------------------

def test_documents_are_isolated_in_the_registry():
    """Registering/dropping one document never leaks into another's
    sessions: lookups are scoped to the doc id, sessions carry their own
    state, and dropping doc1 leaves doc2 untouched."""
    reg = SessionRegistry()
    d1 = _session("doc1", created_at=_T0)
    d2 = _session("doc2", created_at=_T0)

    reg.register(d1)
    # before doc2 exists, asking for it yields nothing (no cross-doc leak)
    assert reg.get("doc2") is None
    assert reg.get_by_id(d2.session_id) is None

    reg.register(d2)
    assert reg.get("doc2") is d2

    # dropping doc1 must not disturb doc2
    reg.drop("doc1")
    assert reg.get("doc1") is None
    assert reg.get_by_id(d1.session_id) is None
    assert reg.get("doc2") is d2
    assert reg.get_by_id(d2.session_id) is d2
    assert reg.all() == [d2]


def test_all_is_sorted_by_created_at_across_documents():
    """``all()`` is the deterministic, creation-ordered view of the live
    session set, regardless of registration order."""
    reg = SessionRegistry()
    oldest = _session("doc1", created_at=_T0)
    middle = _session("doc2", created_at=_T0 + 1.0)
    newest = _session("doc3", created_at=_T0 + 2.0)
    # deliberately scrambled registration order
    reg.register(newest)
    reg.register(oldest)
    reg.register(middle)
    assert reg.all() == [oldest, middle, newest]
    # dropping the newest leaves the remainder in order
    reg.drop("doc3")
    assert reg.all() == [oldest, middle]


def test_dropping_unknown_document_is_a_noop():
    """Expiring a doc that has no live sessions is a safe no-op — the
    registry never raises and keeps its surviving sessions intact."""
    reg = SessionRegistry()
    s = _session("doc1", created_at=_T0)
    reg.register(s)
    reg.drop("doc-unknown")
    assert reg.all() == [s]
    reg.drop("doc1")
    assert reg.all() == []


# ---------------------------------------------------------------------------
# Property test: invariants under arbitrary register/drop sequences
# ---------------------------------------------------------------------------

_SESSIONS = [
    _session(f"doc{i % 3}", created_at=_T0 + float(i), user_id=f"u{i}") for i in range(6)
]
_OP_REG = st.sampled_from(["register-0", "register-1", "register-2", "register-3", "register-4", "register-5"])
_OP_DROP = st.sampled_from(["drop-doc0", "drop-doc1", "drop-doc2", "drop-other"])


@given(
    st.lists(
        st.one_of(_OP_REG, _OP_DROP),
        min_size=0,
        max_size=40,
    )
)
@settings(deadline=None, max_examples=100)
def test_registry_invariants_under_random_sequences(ops: list[str]) -> None:
    """For ANY sequence of register/drop operations on a fresh registry the
    invariants hold after each step:

    * a session is findable by id IFF it is currently registered (no ghost
      lookups, no drops failing to take effect);
    * ``all()`` contains exactly the live sessions, unique, sorted by
      creation time;
    * the doc-level shortcut returns the live session with the greatest
      ``created_at`` (or nothing when the doc has no live sessions).
    """
    reg = SessionRegistry()
    live: set[str] = set()
    for op in ops:
        if op.startswith("register-"):
            s = _SESSIONS[int(op.rsplit("-", 1)[1])]
            reg.register(s)
            live.add(s.session_id)
        else:
            doc = {"drop-doc0": "doc0", "drop-doc1": "doc1", "drop-doc2": "doc2"}.get(op)
            reg.drop(doc)  # "drop-other" never matches a live doc
            if doc:
                for s in _SESSIONS:
                    if s.doc_id == doc:
                        live.discard(s.session_id)

        # invariant 1: findability by id mirrors the model exactly
        for s in _SESSIONS:
            assert (s.session_id in live) == (reg.get_by_id(s.session_id) is not None), (
                f"after {op!r}: id lookup out of sync for {s.session_id}"
            )
        # invariant 2: all() is exactly the live set, unique and ordered
        all_sessions = reg.all()
        expected = sorted(
            (s for s in _SESSIONS if s.session_id in live), key=lambda s: s.created_at
        )
        assert [s.session_id for s in all_sessions] == [s.session_id for s in expected]
        assert len({s.session_id for s in all_sessions}) == len(all_sessions)
        assert [s.created_at for s in all_sessions] == sorted(s.created_at for s in all_sessions)
        # invariant 3: doc shortcut returns latest live session by created_at
        for doc in ("doc0", "doc1", "doc2"):
            candidates = [s for s in _SESSIONS if s.doc_id == doc and s.session_id in live]
            expected = max(candidates, key=lambda s: s.created_at) if candidates else None
            assert reg.get(doc) is expected, f"after {op!r}: doc shortcut wrong for {doc}"


# ---------------------------------------------------------------------------
# Model-based state machine
# ---------------------------------------------------------------------------

_PROP = settings(max_examples=25, stateful_step_count=60, deadline=None)


@_PROP
class SessionRegistryModel(RuleBasedStateMachine):
    """Concurrent + expiry model conformance: a reference set of live
    session ids mirrors every register/drop step, and after every step the
    real registry must match the model exactly — findability, ordering,
    and doc-level resolution of the most recent session."""

    def __init__(self) -> None:
        super().__init__()
        self.registry = SessionRegistry()
        self.sessions = _SESSIONS
        self.live: set[str] = set()

    @rule(i=st.integers(min_value=0, max_value=len(_SESSIONS) - 1))
    def register_session(self, i: int) -> None:
        s = self.sessions[i]
        self.registry.register(s)
        self.live.add(s.session_id)

    @rule(doc=st.sampled_from(["doc0", "doc1", "doc2", "nope"]))
    def drop_document(self, doc: str) -> None:
        self.registry.drop(doc)  # unknown docs are a no-op
        for s in self.sessions:
            if s.doc_id == doc:
                self.live.discard(s.session_id)

    @rule(doc=st.sampled_from(["doc0", "doc1", "doc2", "nope"]))
    def check_doc_shortcut(self, doc: str) -> None:
        candidates = [s for s in self.sessions if s.doc_id == doc and s.session_id in self.live]
        expected = max(candidates, key=lambda s: s.created_at) if candidates else None
        assert self.registry.get(doc) is expected, (
            f"get({doc!r}) must resolve the most recent live session"
        )

    @invariant()
    def findability_matches_model(self) -> None:
        for s in self.sessions:
            present = s.session_id in self.live
            found = self.registry.get_by_id(s.session_id) is not None
            assert present == found, (
                f"session {s.session_id} findability={found} but model says live={present}"
            )

    @invariant()
    def all_matches_model_ordered(self) -> None:
        all_sessions = self.registry.all()
        expected = sorted(
            (s for s in self.sessions if s.session_id in self.live),
            key=lambda s: s.created_at,
        )
        assert [s.session_id for s in all_sessions] == [s.session_id for s in expected]
        assert {s.session_id for s in all_sessions} == self.live

    @invariant()
    def concurrent_sessions_keep_distinct_ids(self) -> None:
        ids = [s.session_id for s in self.registry.all()]
        assert len(ids) == len(set(ids)), "sessions must never clobber one another's ids"
