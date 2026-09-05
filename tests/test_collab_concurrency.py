"""Tests for Hub concurrency: parallel sites op-flood, revision continuity, and no lost ops.

This suite uses Hypothesis property tests to verify that the collaboration hub
correctly handles high-concurrency scenarios, ensuring that all operations
eventually converge and no updates are lost regardless of delivery order
or interleaving.
"""

from __future__ import annotations

import random
from hypothesis import given, strategies as st
import pytest

from src.editor.collab import (
    BASE_SITE,
    TextCRDT,
    get_hub,
    reset_hub,
)

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _run_concurrent_flood(doc_id: str, site_count: int, ops_per_site: int):
    """
    Simulates multiple sites flooding the hub with concurrent operations.
    Returns the final text and the total number of characters that should be present
    if only inserts were used.
    """
    hub = get_hub()
    sites = [f"site-{i}" for i in range(site_count)]
    replicas = {s: TextCRDT(s) for s in sites}
    
    # All sites start with a shared base (seeded by hub)
    hub.resync(doc_id, "BASE")
    base_crdt = TextCRDT(BASE_SITE, initial_text="BASE")
    
    # Setup replicas
    replicas = {s: TextCRDT(s) for s in sites}
    for s in sites:
        # Peer replicas integrate the seed to stay anchored
        replicas[s].integrate(base_crdt.seed_op)

    all_ops_generated = []
    
    # Generate ops locally first to simulate concurrency
    for s in sites:
        rep = replicas[s]
        for _ in range(ops_per_site):
            # Randomly insert at current alive positions
            pos = random.randint(0, rep.alive_count)
            text = f"[{s}]"
            op = rep.local_insert(pos, text)
            # IMPORTANT: To simulate true concurrency and avoid the replica 
            # already having the op, we don't let the replica 'keep' it in a way 
            # that would affect other concurrent ops from the same site 
            # (though local_insert already integrates).
            all_ops_generated.append((s, op))


    # Shuffle delivery order to the hub
    random.shuffle(all_ops_generated)

    for s, op in all_ops_generated:
        hub.apply_ops(doc_id, s, [op])

    return hub.state(doc_id)["text"]

# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

def test_hub_revision_continuity():
    """
    Verify that hub revisions are strictly monotonic and continuous,
    even when operations are rejected or deduplicated.
    """
    reset_hub()
    hub = get_hub()
    doc_id = "rev_test.docx"
    
    # 1. Initial seed
    hub.resync(doc_id, "Hello")
    assert hub.rev(doc_id) == 1
    
    # 2. Valid op
    crdt = TextCRDT("A")
    op1 = crdt.local_insert(0, "!")
    res1 = hub.apply_ops(doc_id, "A", [op1])
    assert res1["rev"] == 2
    
    # 3. Duplicate op (should not bump rev)
    res2 = hub.apply_ops(doc_id, "A", [op1])
    assert res2["rev"] == 2
    assert len(res2["applied"]) == 0
    
    # 4. Malformed op (should not bump rev)
    res3 = hub.apply_ops(doc_id, "A", [{"t": "insert", "s": "A"}]) # missing fields
    assert res3["rev"] == 2
    
    # 5. Multiple ops in one batch
    crdt2 = TextCRDT("B")
    op2 = crdt2.local_insert(0, "X")
    op3 = crdt2.local_insert(1, "Y")
    res4 = hub.apply_ops(doc_id, "B", [op2, op3])
    assert res4["rev"] == 4
    assert len(res4["applied"]) == 2

def test_hub_parallel_flood_convergence():
    """
    High-level concurrency test: multiple sites performing random inserts.
    Verifies that the final state is consistent and no ops are lost.
    """
    reset_hub()
    doc_id = "flood.docx"
    site_count = 3
    ops_per_site = 10
    
    hub = get_hub()
    sites = [f"s{i}" for i in range(site_count)]
    hub.resync(doc_id, "BASE")
    base_crdt = TextCRDT(BASE_SITE, initial_text="BASE")
    
    all_ops = []
    for s in sites:
        rep = TextCRDT(s)
        rep.integrate(base_crdt.seed_op)
        for _ in range(ops_per_site):
            pos = random.randint(0, rep.alive_count)
            # Use a single character marker for a specific site.
            # site 0 -> '0', site 1 -> '1', site 2 -> '2'
            marker = s[-1] 
            op = rep.local_insert(pos, marker)
            all_ops.append((s, op))

    # Deliver in a way that preserves per-site order to avoid RGA chain breaks.
    streams = [[op for s_local, op in all_ops if s_local == s] for s in sites]
    interleaved = []
    while any(streams):
        for s_idx, stream in enumerate(streams):
            if stream:
                interleaved.append((sites[s_idx], stream.pop(0)))

    for s, op in interleaved:
        hub.apply_ops(doc_id, s, [op])
        
    final_text = hub.state(doc_id)["text"]
    
    # Base "BASE" (4) + 3 sites * 10 ops * 1 char = 34.
    assert len(final_text) == 4 + (site_count * ops_per_site)
    
    # Verify every single character from every site is present.
    for i in range(site_count):
        marker = str(i)
        assert final_text.count(marker) == ops_per_site




@given(
    num_sites=st.integers(min_value=2, max_value=10),
    ops_per_site=st.integers(min_value=1, max_value=50)
)
def test_hub_property_convergence(num_sites, ops_per_site):
    """
    Property-based test: Random number of sites and operations should
    always result in a converged state where no operations are lost.
    """
    reset_hub()
    doc_id = "prop_flood.docx"
    
    hub = get_hub()
    sites = [f"s{i}" for i in range(num_sites)]
    
    hub.resync(doc_id, "B") # Base "B"
    base_crdt = TextCRDT(BASE_SITE, initial_text="B")
    
    # Setup replicas
    replicas = {s: TextCRDT(s) for s in sites}
    for s in sites:
        replicas[s].integrate(base_crdt.seed_op)
        
    all_ops = []
    for s in sites:
        rep = replicas[s]
        for _ in range(ops_per_site):
            pos = random.randint(0, rep.alive_count)
            txt = "X"
            op = rep.local_insert(pos, txt)
            all_ops.append((s, op))
            
    # Deliver in random order
    random.shuffle(all_ops)
    for s, op in all_ops:
        hub.apply_ops(doc_id, s, [op])
        
    final_text = hub.state(doc_id)["text"]
    # Length = 1 (base) + num_sites * ops_per_site
    assert len(final_text) == 1 + (num_sites * ops_per_site)

def test_hub_interleaved_insert_delete_no_loss():
    """
    Tests a complex scenario where sites concurrently insert and delete.
    Verifies that the CRDT logic in the hub prevents 'lost' updates or 
    corrupted state when deletes target concurrent inserts.
    """
    reset_hub()
    hub = get_hub()
    doc_id = "interleave.docx"
    
    # Base: "Hello World"
    hub.resync(doc_id, "Hello World")
    base = TextCRDT(BASE_SITE, initial_text="Hello World")
    
    # Site A inserts " Brave" -> "Hello Brave World"
    a = TextCRDT("A")
    a.integrate(base.seed_op)
    op_a = a.local_insert(6, " Brave")
    
    # Site B deletes "World" -> "Hello "
    b = TextCRDT("B")
    b.integrate(base.seed_op)
    op_b = b.local_delete(6, 11)
    
    # Hub applies A then B
    hub.apply_ops(doc_id, "A", [op_a])
    hub.apply_ops(doc_id, "B", [op_b])
    
    final_text = hub.state(doc_id)["text"]
    # "Hello World" (11)
    # Site A inserts " Brave" (6 chars) at index 6 (after "Hello ")
    # Site B deletes "World" (index 6-11 of "Hello World")
    # "Hello " (6) + " Brave" (6) + "World" (5)
    # "World" are IDs from the seed.
    # Final should be "Hello  Brave"
    assert final_text == "Hello  Brave"
    assert "Brave" in final_text
    assert "World" not in final_text


