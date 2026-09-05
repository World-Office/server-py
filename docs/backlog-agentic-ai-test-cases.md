# Agentic AI — Test-Case Matrix

> Companion to `docs/backlog-epics-and-user-stories.md` (epics E13–E23).
> Seed OpenSpec change: `openspec/changes/agentic-ai-document-platform/`.
> Principle: **agent edits are just another untrusted input class** — every story is
> guarded by the SOTA paradigms already in the repo, extended where needed.
> Created: 2026-08-27 · Paradigms in use: UNIT, PROP, MB (model-based), FUZZ, MUT
> (mutation), GOLD (golden-master), DIFF (differential), FI (fault-injection),
> SEC (security/adversarial), BENCH, E2E.

## E13 — Agent tool surface (MCP)

| TC | Story | Paradigm | Test case | Invariant / expected |
|----|-------|----------|-----------|----------------------|
| TC-E13-01 | E13S1 | UNIT | Tool catalog lists all five tools | `read_doc`, `apply_ops`, `get_versions`, `lock`, `presence` discoverable |
| TC-E13-02 | E13S1 | GOLD | Tool-catalog snapshot | schema diffs are intentional only (`UPDATE_GOLDEN=1` workflow) |
| TC-E13-03 | E13S2 | UNIT | `read_doc` returns content + op-log tail | bytes equal stored content; tail = last N ops |
| TC-E13-04 | E13S2 | FUZZ | Hostile doc-ids against MCP routes (traversal/unicode/overlong) | never 500; typed not-found (extends `test_api_fuzz.py`) |
| TC-E13-05 | E13S3 | MB | Agent state machine driving `apply_ops` | text converges to reference model |
| TC-E13-06 | E13S3 | FI | Malformed op batch (`ids:"bad"`, `b:null`) | typed rejection; hub stays up (extends `test_resilience.py`) |
| TC-E13-07 | E13S4 | PROP | Lock-tool sequences (lock/refresh/unlock) | identical `409` contract as HTTP WOPI (`test_wopi_protocol_property` parity) |
| TC-E13-08 | E13S4 | UNIT | Presence join/leave via tool | list correct; leave cleans up |
| TC-E13-09 | E13S5 | DIFF | stdio vs HTTP transport conformance | identical responses to the same tool-call sequence |
| TC-E13-10 | E13S6 | SEC | Tool surface disabled | 404/403; no handler reachable |
| TC-E13-11 | E13S1 | E2E | MCP connect on OCIS stack | discovery + read end-to-end |

## E14 — Agent identity, permissions & consent

| TC | Story | Paradigm | Test case | Invariant / expected |
|----|-------|----------|-----------|----------------------|
| TC-E14-01 | E14S1 | UNIT | Agent ops carry `agent=<name>` client_id | attribution on every op |
| TC-E14-02 | E14S1 | GOLD | Audit rows / presence snapshot tagged | normalized snapshot shows agent field |
| TC-E14-03 | E14S2 | SEC | Read-only agent calls `apply_ops` | `403`; no op applied; store byte-identical |
| TC-E14-04 | E14S2 | MUT | Mutant: scope check dropped | killed (suite fails without it) |
| TC-E14-05 | E14S3 | FI | Model call without consent flag | fail-closed typed error; egress mock asserts zero requests |
| TC-E14-06 | E14S4 | BENCH | Budget/rate-limit under load | `429` at exact boundary; hub p99 unaffected |
| TC-E14-07 | E14S4 | PROP | Random op bursts vs budget | enforcement monotonic, no off-by-one |
| TC-E14-08 | E14S5 | FI | Kill switch mid-session | next tool call fails; presence cleaned; document consistent |
| TC-E14-09 | E14S1 | FUZZ | Forged `client_id` in op payloads | server-side identity wins; forgery ignored |
| TC-E14-10 | E14S6 | UNIT+FI | Expired agent session | `401`; in-flight loop stops; extends E11S3 expiry pattern |
| TC-E14-11 | E14S7 | SEC | Confused deputy: read-only user, edit-scope agent | effective scope = intersection → still `403`; no escalation path |

## E15 — Agents as collaborators

| TC | Story | Paradigm | Test case | Invariant / expected |
|----|-------|----------|-----------|----------------------|
| TC-E15-01 | E15S1 | MB | 50-op agent loop state machine | convergence vs reference model |
| TC-E15-02 | E15S2 | UNIT | Presence includes agent entries | name/badge shown; leave cleanup |
| TC-E15-03 | E15S3 | PROP | Interleaved human/agent ops (Hypothesis) | both texts preserved; no loss |
| TC-E15-04 | E15S3 | MB | Two human replicas + one agent replica | all replicas converge |
| TC-E15-05 | E15S4 | UNIT | Locked-document write without/with token | `409` / applies |
| TC-E15-06 | E15S4 | MUT | Mutant: agent path skips lock check | killed |
| TC-E15-07 | E15S5 | UNIT+FI | Agent batch → one labelled version; reopen | label persists (extends `test_persistence.py`) |
| TC-E15-08 | E15S1 | BENCH | 100-op loop on a large document | time + op-count within budget |
| TC-E15-09 | E15S3 | FUZZ | Concurrent agent+human threads | no sqlite race (`RLock` holds) |

## E16 — Review, transparency & control

| TC | Story | Paradigm | Test case | Invariant / expected |
|----|-------|----------|-----------|----------------------|
| TC-E16-01 | E16S1 | GOLD | Op-stream diff of an agent batch | normalized golden matches |
| TC-E16-02 | E16S2 | MB | Reject op → inverse op re-emitted | state equals pre-agent state |
| TC-E16-03 | E16S2 | UNIT | Accept/reject per op and per batch | state transitions correct |
| TC-E16-04 | E16S3 | E2E | Rollback click restores revision | concurrent editor receives rollback via hub |
| TC-E16-05 | E16S3 | UNIT+FI | Rollback persisted across reopen | version history intact |
| TC-E16-06 | E16S4 | UNIT | Dry-run applies nothing | store byte-identical before/after |
| TC-E16-07 | E16S4 | PROP | dry-run → apply ≡ direct apply | equality property |
| TC-E16-08 | E16S5 | UNIT | Rationale in batch metadata | stored, surfaced, audited |
| TC-E16-09 | E16S1 | UNIT | Volatile-field normalization in diff | no false diffs (timestamps/versions) |

## E17 — Agent safety & anti-injection

| TC | Story | Paradigm | Test case | Invariant / expected |
|----|-------|----------|-----------|----------------------|
| TC-E17-01 | E17S1 | FUZZ | Agent-driver malformed ops (Hypothesis) | typed rejection; hub never crashes |
| TC-E17-02 | E17S1 | MUT | Mutants: `integrate`/`op_key` validation dropped | killed |
| TC-E17-03 | E17S2 | SEC | Prompt-injection corpus in document content | no tool calls originate from content |
| TC-E17-04 | E17S2 | UNIT | Context pack marks content untrusted | delimiters/escaping present |
| TC-E17-05 | E17S3 | FI | Runaway loop: budget/time exceeded | session killed; document consistent; hub responsive |
| TC-E17-06 | E17S3 | BENCH | Loop cost bounded | ops ≤ budget; wall-time ≤ limit |
| TC-E17-07 | E17S4 | SEC | Sanitizer corpus vs agent-written HTML | `<script>` suppression test holds |
| TC-E17-08 | E17S4 | FUZZ | Agent-inserted structured HTML fuzz | converters never crash |
| TC-E17-09 | E17S5 | SEC | Egress allowlist violation | blocked + audit event; no outbound connection |
| TC-E17-10 | E17S2 | MB | Hostile document content through a full agent loop | loop completes; no side-effects from content |
| TC-E17-11 | E17S3 | UNIT | Single oversized op from an agent | typed `413`; store unchanged (reuses oversize-PutFile contract) |

## E18 — Grounding & document Q&A

| TC | Story | Paradigm | Test case | Invariant / expected |
|----|-------|----------|-----------|----------------------|
| TC-E18-01 | E18S1 | GOLD | Context-pack snapshot | deterministic, normalized |
| TC-E18-02 | E18S1 | UNIT | Pack size bound | truncation policy holds on a 5 MB doc |
| TC-E18-03 | E18S2 | PROP | Anchor ops survive round-trip | anchor resolves to same text after DOCX/ODT |
| TC-E18-04 | E18S2 | UNIT | Invalid anchor | typed error; no partial apply |
| TC-E18-05 | E18S3 | UNIT | Q&A passage spans + refs | spans match source text |
| TC-E18-06 | E18S4 | SEC | Cross-document isolation | denied without grant; no content leak |
| TC-E18-07 | E18S1 | FUZZ | Context pack over hostile/corrupt docs | tolerant readers; never 500 |

## E19 — Model pluggability & privacy

| TC | Story | Paradigm | Test case | Invariant / expected |
|----|-------|----------|-----------|----------------------|
| TC-E19-01 | E19S1 | DIFF | Provider A vs B adapters | identical op stream from identical tool calls |
| TC-E19-02 | E19S2 | E2E | Local model, egress disabled | edit completes; zero external connections |
| TC-E19-03 | E19S3 | FI | Provider 500/timeout | fallback honored; typed error if all fail; document uncorrupted |
| TC-E19-04 | E19S3 | PROP | Failures injected at random call sites | recovery property holds |
| TC-E19-05 | E19S4 | UNIT | Usage rows in audit | tokens/cost per session |
| TC-E19-06 | E19S1 | UNIT | No vendor types leak into core | architecture/import test |

## E20 — Agent observability, audit & ops

| TC | Story | Paradigm | Test case | Invariant / expected |
|----|-------|----------|-----------|----------------------|
| TC-E20-01 | E20S1 | UNIT | Trace records tool calls/ops/results | ordered, complete |
| TC-E20-02 | E20S1 | SEC | Content redaction in traces | document text absent unless debug flag |
| TC-E20-03 | E20S2 | UNIT | Audit rows tagged agent + session | extends E7S5 format |
| TC-E20-04 | E20S3 | E2E | Agents dashboard renders | read-only; sessions/ops/failures visible |
| TC-E20-05 | E20S4 | FI | Seeded failure loop | alert/threshold hook fires |
| TC-E20-06 | E20S1 | BENCH | Tracing overhead | p99 overhead within budget |

## E21 — Workflows & multi-agent

| TC | Story | Paradigm | Test case | Invariant / expected |
|----|-------|----------|-----------|----------------------|
| TC-E21-01 | E21S1 | GOLD | Workflow transcript golden | deterministic replay |
| TC-E21-02 | E21S1 | MB | Workflow = ordered tool pipeline | order enforced; failure stops pipeline |
| TC-E21-03 | E21S2 | UNIT | Handoff preserves state + audit chain | state hash equal; no privileged path |
| TC-E21-04 | E21S3 | FI | Bounded queue; cancel mid-job | no further ops; consistent document |
| TC-E21-05 | E21S3 | BENCH | Queue under load | no unbounded growth |
| TC-E21-06 | E21S4 | UNIT | Scheduled job runs for granted docs only | isolation; failures alert (E20S4) |
| TC-E21-07 | E21S5 | UNIT | Approval gate between stages | pipeline suspends; resume only on approval; timeout parks job + alert |

## E22 — Agent evaluation & quality gates

| TC | Story | Paradigm | Test case | Invariant / expected |
|----|-------|----------|-----------|----------------------|
| TC-E22-01 | E22S1 | MB | Agent-driven Hypothesis state machine (new file) | convergence + never-500 |
| TC-E22-02 | E22S1 | FUZZ | Agent corpora through the route fuzzer | extends `test_api_fuzz.py` to MCP routes |
| TC-E22-03 | E22S2 | MUT | Mutants: scope dropped / `409` bypassed / budget removed | all killed; score stays 100% (`scripts/mutation-test.py`) |
| TC-E22-04 | E22S3 | GOLD | Recorded agent session replay | normalized replay identical |
| TC-E22-05 | E22S4 | BENCH | Regression benchmark suite | time + op budgets in `tests/bench/` |
| TC-E22-06 | E22S5 | E2E | Playwright agent-vs-OCIS | full flow on disposable stack (extends E12S4) |
| TC-E22-07 | E22S1 | PROP | Document-integrity invariant for agent edits | text == f(valid ops applied); no loss |
| TC-E22-08 | E22S2 | UNIT | CI gate wiring | merge blocked when mutation score < 100% |
| TC-E22-09 | E22S1 | FI+PROP | Chaos sweep: random faults across the whole tool surface | hub never corrupts; every fault → typed error or safe stop |

## E23 — Agent UX in the editor

| TC | Story | Paradigm | Test case | Invariant / expected |
|----|-------|----------|-----------|----------------------|
| TC-E23-01 | E23S1 | E2E | Same panel across documents; keyboard reachable | axe + keyboard test pass |
| TC-E23-02 | E23S2 | E2E | Streaming progress via presence channel | status updates live; cancel works |
| TC-E23-03 | E23S3 | UNIT | `notifyHost` on completion | payload correct (extends E3S5) |
| TC-E23-04 | E23S4 | E2E | `aria-live` announcement | batch summary announced; axe passes |
| TC-E23-05 | E23S5 | E2E | 360 px viewport | touch targets ≥ 44 px; no overflow |
| TC-E23-06 | E23S1 | UNIT | Agent UI strings externalized | EN + DE tables complete (extends E5S2) |

---

## Saturation review

Method: every epic was attacked with the ten-dimensional checklist below. A cell is
**✓** (covered by the TCs above), **–** (deliberately N/A for this epic, reason given),
or points at the TCs that cover it. Saturation is reached when no cell can produce a
*new, meaningful* story — additions after this pass are bug-driven, not breadth-driven.

| Dimension | E13 | E14 | E15 | E16 | E17 | E18 | E19 | E20 | E21 | E22 | E23 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| AuthN/AuthZ & scopes | TC-13-10 | TC-14-02/03/04/06/10/11 | TC-15-05/06 | – (review implies read grant) | – | TC-18-06 | – | – | TC-21-06/07 | TC-22-03 | – (UI follows API) |
| Concurrency & races | – (stateless tools) | – | TC-15-03/04/09 | TC-16-04 | – | – | – | – | – | TC-22-01 | – |
| Failure & fault injection | TC-13-06 | TC-14-05/08/10 | – | – | TC-17-05/11 | – | TC-19-03/04 | TC-20-05 | TC-21-04/07 | TC-22-09 | – |
| Scale / performance | – | TC-14-06 | TC-15-08 | – | TC-17-06 | TC-18-02 | – | TC-20-06 | TC-21-05 | TC-22-05 | – |
| Privacy / exfiltration | – | TC-14-03/05 | – | – | TC-17-09 | TC-18-06 | TC-19-02 | TC-20-02 | – | – | – |
| Security / adversarial input | TC-13-04 | TC-14-09 | – | – | TC-17-01/03/07/08/10 | TC-18-07 | – | – | – | TC-22-02 | – |
| Persistence / lifecycle | – | – | TC-15-07 | TC-16-05 | – | – | – | TC-20-01 | – | TC-22-04 | – |
| Interop (WOPI/OCIS) | TC-13-07/11 | – | TC-15-04 | – | – | – | – | – | – | TC-22-06 | TC-23-03 |
| a11y / i18n | – | – | – | – | – | – | – | – | – | – | TC-23-01/04/05/06 |
| Protocol evolution / compat | TC-13-02 | – | – | TC-16-01 | – | TC-18-01 | TC-19-01 | – | TC-21-01 | TC-22-04 | – |

**Findings from the pass** (folded in above, listed for traceability):
1. Forged-identity fuzz (E14) was missing → TC-E14-09 added.
2. Dry-run ≡ apply equivalence property (E16) was missing → TC-E16-07 added.
3. Trace overhead budget (E20) and content redaction (E20/SEC) were missing → TC-E20-02/06.
4. Protocol-compat goldens now explicitly cover E13/E16/E18/E21/E22 (schema evolution).
5. E23 deliberately carries the a11y/i18n load; other epics are API-level (marked –).

**Round-2 additions** (authn/concurrency/failure sweep found four genuine gaps):
6. Agent-session expiry (E14S6, mirrors E11S3) → TC-E14-10.
7. Confused-deputy guard — agent ⊆ user permissions (E14S7) → TC-E14-11.
8. Human approval gate in workflows (E21S5) → TC-E21-07.
9. Oversized single op (`413` reuse) → TC-E17-11; cross-surface chaos property → TC-E22-09.

Round 3 swept the remaining cells (batch reads, trace export, mid-flight model swap,
reduced-motion UX) and produced no new *meaningful* stories — each is either Stoic-marginal
or already implied by an acceptance criterion. **Saturation reached: 11 epics, 55 stories,
92 test cases.**

**Deliberately out of scope for testing** (Stoic): agent-browser E2E beyond one OCIS
stack, multi-tenant isolation (not multi-tenant), model *quality* benchmarks (the model
is pluggable — we test the substrate, not the vendor).
