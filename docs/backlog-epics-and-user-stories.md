# World-Office — Epic & User-Story Backlog

> **Canonical product:** `opencloud-docserver` (Python FastAPI WOPI docserver + vanilla-JS editor + OpenCloud/OCIS).
> **Direction:** Stoic Unix rewrite — one process, one job (edit office documents through OpenCloud).
> **Created:** 2026-08-27  ·  **Status source:** `docs/office-research/feature-graph.md` (last sweep 2026-08-26, suite 467 green).

This is the product backlog: **epics** (large capability areas) decomposed into
**user stories** (small, shippable, user-valuable increments). It is the input
for OpenSpec changes: when a story is scheduled, promote its spec fragment to
`openspec/changes/<name>/` (see “Backlog → change” at the end).

The agentic-AI domain (E13–E23) has a companion **test-case matrix** mapping every
story to concrete tests across our SOTA paradigms:
`docs/backlog-agentic-ai-test-cases.md`. Its seed OpenSpec change is
`openspec/changes/agentic-ai-document-platform/`.

## Legend

| Mark | Meaning |
|------|---------|
| 🔲 | Not started (candidate) |
| 🔶 | Started / partial (some stories done) |
| ✅ | Substantially delivered — remaining stories are polish |
| P0 / P1 / P2 | Priority: do-now / soon / sometime |
| ⚖️ | Aligned with Stoic checks (§7 of RETHINK) — review when we say yes |

---

## E1 — Table editing power
Status: 🔶 (insert/add-row/merge/header/caption done) · Priority: P1
Closes the remaining `feature-graph` table gaps: **borders, shading, width/height** (all ❌), and makes **split** a real user operation.

- **E1S1** (P1) As a **teacher**, I want to apply borders and shading to tables/cells, so handout tables look polished.
  Acceptance: border/shading controls survive DOCX & ODT round-trip (`w:tcBorders`/`w:shd` ↔ `fo:border`/`fo:background-color`).
- **E1S2** (P1) As an **author**, I want to set column width and row height, so tables fit the page.
  Acceptance: width/height round-trip both formats; nested `data-*` attributes survive sanitize.
- **E1S3** (P2) As a **user**, I want a “split cell” action split/unsplit cells, so I don’t rebuild tables.
  Acceptance: split emits `w:tcPr/w:vMerge`+`w:gridSpan` ↔ ODT `table:number-columns-spanned`, and unsplit re-joins.
- **E1S4** (P2) As a **user**, I want to paste a table from the clipboard, so I can work faster.
  Acceptance: pasted table HTML becomes a real round-tripping table (no raw HTML leakage).
- **E1S5** (P2) As a **user**, I want tables to shrink-wrap long cell text on print, so nothing overflows.
  Acceptance: long-cell docs round-trip and print-width stays inside page.

## E2 — Objects that mean something
Status: 🔶 (image insert/resize + shape/textbox/chart/equation placeholders round-trip) · Priority: P1
Today objects are purposeful *placeholders*; this epic makes them real content.

- **E2S1** (P1) As a **teacher**, I want to author equations with real math (LaTeX → MathML), not a placeholder box.
  Acceptance: `<div class="object" data-type="equation">` carries editable MathML that round-trips DOCX `m:oMath` ↔ ODT `math:math`.
- **E2S2** (P1) As an **analyst**, I want to enter chart data and labels, so the chart shows my numbers.
  Acceptance: chart data lives in a `data-*`/JSON payload that round-trips `c:chart` ↔ `chart:chart`.
- **E2S3** (P2) As a **user**, I want inline ↔ floating image wrap styles, so layouts look right.
  Acceptance: wrap choice maps to `w:drawing` inline vs anchor ↔ ODT `draw:frame` anchored form.
- **E2S4** (P2) As an **author**, I want to set alt text on images and objects, so the document is accessible.
  Acceptance: `alt`/`svg:title` already round-trips; expose it in the image dialog.
- **E2S5** (P2) As a **user**, I want to edit text inside a textbox/shape online, so placeholders become usable.
  Acceptance: `wps:txbxContent` ↔ `draw:text-box` inner text is editable and survives round-trip.

## E3 — Real-time collaboration & versions
Status: 🔶 (CRDT hub, comments, track-changes live) · Priority: P0
The flagship epic. Re-scopes two deferred items — **presence carets** (was “explicitly out of scope” in cloud-editor-complete) and **version history** (❌ REQUIRES collab) — into active work.

- **E3S1** (P0) As a **co-author**, I want to see remote carets and avatar labels, so I know where others are editing.
  Acceptance: `collab-presence` spec scenarios pass (caret appears, follows edits, disappears on leave).
- **E3S2** (P0) As an **author**, I want to browse and restore earlier versions of a document, so mistakes are reversible.
  Acceptance: version list + restore through the WOPI store; `src/lib/store.py` gains versioned content blobs.
- **E3S3** (P1) As a **co-author**, I want comments and review decisions to appear live on other screens.
  Acceptance: comment insert/accept/reject broadcast via the CRDT hub without full save.
- **E3S4** (P1) As a **team**, I want edits to merge without losing work when two people save simultaneously.
  Acceptance: concurrent-save test keeps both authors’ text (extends `tests/test_collab_sync.py`).
- **E3S5** (P2) As a **reviewer**, I want change notifications (“Alice commented”), so the loop closes.
  Acceptance: host notification via WOPI/`notifyHost` for review events.

## E4 — Offline & resilient editing
Status: 🔲 · Priority: P2 · Source: `web/sw.js` exists (service worker registered, served from disk)
- **E4S1** (P2) As a **commuter**, I want to keep editing when the network drops, so I don’t lose flow.
  Acceptance: offline queue buffers edits; WOPI PutFile replays on reconnect (idempotent by lock token).
- **E4S2** (P1) As a **user**, I want autosave, so a crash never costs me work.
  Acceptance: draft snapshot every N seconds; recovered on next open (draft ≠ saved version).
- **E4S3** (P2) As a **user**, I want the editor shell to open offline from cache, so I can start reading immediately.
  Acceptance: `sw.js` precaches editor assets; verified via Playwright offline context.

## E5 — Accessibility & internationalization
Status: 🔶 (a11y find-dialog, some labels) · Priority: P1 · Sources: `web/i18n.js`, e2e a11y tests
- **E5S1** (P0) As a **screen-reader user**, I want full keyboard/screen-reader operation, so I can author documents.
  Acceptance: axe-core mock passes on the editor; keyboard executes every toolbar action.
- **E5S2** (P1) As a **user**, I want the UI in my language, so I can work comfortably.
  Acceptance: `i18n.js` message table completes for ≥ EN + DE; UI strings externalized.
- **E5S3** (P2) As an **RTL user**, I want right-to-left document and UI flow.
  Acceptance: existing `dir=rtl` round-trip honored in toolbar/menus; layout mirrors.
- **E5S4** (P2) As a **low-vision user**, I want dark mode and zoom to persist per browser.
  Acceptance: preferences in `localStorage`/`window.__USER_*` handshake, restored on load.

## E6 — Export & print fidelity
Status: 🔶 (export-pdf/odt/html/docx, print-layout CSS) · Priority: P1
- **E6S1** (P1) As a **user**, I want PDF export that matches print pagination, so handouts look right in print.
  Acceptance: weasyprint/Chromium print pipeline honors page headers/footers/page numbers (`<header class="page-header">`, `<footer class="page-footer">`, `<span class="page-number">`).
- **E6S2** (P2) As a **user**, I want print preview to show headers/footers, so I trust the printed copy.
  Acceptance: print stylesheet renders header/footer on every printed page.
- **E6S3** (P2) As a **user**, I want export to preserve editorial markers (comments, track-changes, bookmarks, cross-refs), so nothing is silently lost.
  Acceptance: export tests reuse the conformance contracts (`test_onlyoffice_conformance.py`).

## E7 — Administration & operations
Status: 🔶 (health endpoint, structured logs, runbook) · Priority: P1 · Sources: `plan/operations-runbook.md`, `plan/specs/admin-pages.md`, `plan/specs/seaweedfs-storage.md`
- **E7S1** (P1) As an **admin**, I want a document inventory with storage/access stats, so I can see what’s hosted.
  Acceptance: read-only admin route listing store contents + sizes (Stoic: a page, not a SPA).
- **E7S2** (P1) As an **admin**, I want quotas and retention rules, so growth stays bounded.
  Acceptance: per-host quota enforced at PutFile; retention purges to trash.
- **E7S3** (P2) As an **admin**, I want backup/restore of the SQLite ledger and blobs (incl. S3/SeaweedFS), so I can rebuild after loss.
  Acceptance: `backup` + `restore` commands round-trip a fixture store.
- **E7S4** (P2) As an **operator**, I want alerts on lock-stalls and repeated convert failures, so I fix issues before users notice.
  Acceptance: structured JSON log events + configurable threshold hooks.
- **E7S5** (P2) As an **admin**, I want an audit log of document opens/saves, so I can review access.
  Acceptance: append-only audit records with host/user/id/timestamp.

## E8 — Writing assistance
Status: 🔶 (spellcheck `spellcheck` attribute, find-replace, word count) · Priority: P2 · Source: `plan/specs/spellchecker.md`, `plan/specs/ai-integration.md`
- **E8S1** (P2) As a **writer**, I want inline spelling/grammar underlines with suggestions, so my text is clean.
  Acceptance: backend suggestion API; accents/case handled (Hungarian-aware was part of the old wo-spell scope).
- **E8S2** (P2) As a **writer**, I want advanced find-replace (whole-word, match-case, regex, replace-all), so editing is fast.
  Acceptance: current find dialog gains options; state survives focus restore.
- **E8S3** (P2) As a **user**, I want live selection word/character count, so I can hit length targets.
  Acceptance: status bar updates on selection (already live for full-doc count).
- **E8S4** (P2) As a **teacher**, I want writing suggestions grounded in the document, so help is relevant.
  Acceptance: gated AI completion endpoint; nothing leaves the server without explicit consent (Stoic gating).

## E9 — Knowledge & AI integration
Status: 🔲 (T33 was deemed infeasible: repo not cloned locally) · Priority: P2 · Sources: `plan/specs/ai-integration.md`, `plan/specs/mcp-server.md`, T33 chemie-neo4j
- **E9S1** (P2) As a **researcher**, I want to push a finished document into the chemie knowledge graph, so it becomes searchable/semantic.
  Acceptance: export hook delivers document text + structure to chemie-lernen.org ingest API.
- **E9S2** (P2) As a **student**, I want to ask questions answered from my documents, so revision is targeted.
  Acceptance: Q&A endpoint retrieves passages linked to source documents.
- **E9S3** (P2) As an **agent**, I want to create/edit documents over MCP, so my other tools can use the editor.
  Acceptance: MCP server routes to docserver write endpoints with same auth.

## E10 — OpenCloud lifecycle & sharing
Status: 🔶 (WOPI host mode, save-back, lock/unlock) · Priority: P1 · Source: `plan/specs/opencloud-integration/`, `plan/specs/mobile-responsive/`
- **E10S1** (P1) As a **user**, I want view-vs-edit sharing enforced, so shared links respect permissions.
  Acceptance: read-only link opens editor in RO mode (`protect` handshake); edit link requires token.
- **E10S2** (P1) As a **user**, I want rename/move/delete in OCIS to stay in sync with the docserver ledger.
  Acceptance: WOPI rename/move/delete round-trip; stale store entries cleaned.
- **E10S3** (P1) As a **user**, I want clean WOPI locking with auto-refresh and takeover, so two editors don’t clobber each other.
  Acceptance: lock refresh loop + expired-lock takeover (extends `tests/test_wopi.py`).
- **E10S4** (P2) As a **mobile user**, I want to edit on a phone, so I can review anywhere.
  Acceptance: responsive layout at 360px; touch targets ≥ 44px; toolbar collapses.
- **E10S5** (P2) As a **user**, I want saves to land in OCIS as new versions, so the host keeps history.
  Acceptance: version bump per PutFile visible in OCIS file version list.

## E11 — Reliability & security hardening
Status: 🔶 (JWT auth, sanitizer, lock state tests) · Priority: P1 · Source: `plan/specs/cross-cutting-concerns.md`
- **E11S1** (P1) As an **operator**, I want the server to survive restarts without corrupting documents.
  Acceptance: crash-restart test leaves store consistent; half-written blobs recoverable.
- **E11S2** (P1) As a **user**, I want my document free of script injection no matter what I paste.
  Acceptance: sanitizer fuzz (`test_structured_fuzz_never_crashes_converters` style) upholds `_safe_tags` allowlist under Hypothesis.
- **E11S3** (P2) As an **admin**, I want JWT token expiry/rotation enforced, so leaked tokens fade quickly.
  Acceptance: auth tests assert 401 on expired token; rotation path covered.
- **E11S4** (P2) As an **operator**, I want the server responsive under load, so editing stays snappy.
  Acceptance: `tests/bench/` benchmarks stay within budget; no unbounded queues.

## E12 — Conformance & fidelity confidence
Status: 🔶 (30-docx corpus, 27 ONLYOFFICE-conformance cases, LibreOffice differential, Hypothesis property/fuzz) · Priority: P1
This epic carries the **SOTA testing methods** already landed and grows them.

- **E12S1** (P1) As a **developer**, I want ODT conformance quality equal to DOCX, so parity is provable.
  Acceptance: an ODT corpus alongside the docx corpus with the same round-trip + differential harness.
- **E12S2** (P1) As a **developer**, I want LibreOffice-oracle differential coverage over fixtures LibreOffice generates, so foreign-file reads are guarded.
  Acceptance: LO-produced feature fixtures (bookmarks, comments, tracked changes) feed `test_converter_differential.py`.
- **E12S3** (P2) As a **developer**, I want automated mutation testing in CI, so the suite’s teeth are verified continuously.
  Acceptance: a mutation sweep (drop author, drop bookmark name, drop w:id) is run and each mutant fails.
- **E12S4** (P2) As a **developer**, I want the full WOPI flow in CI against OCIS, so host-mode regressions are caught early.
  Acceptance: `tests/e2e/` runs on a disposable OCIS stack in CI (source: `e2e-test-infrastructure`).

---

## E13 — Agent tool surface (MCP)
Status: 🔲 · Priority: P1 · Source: `openspec/changes/agentic-ai-document-platform/`
Make document operations callable by any AI agent through a **model-agnostic MCP tool
surface** over the existing WOPI + collab APIs. No parallel AI code path.

- **E13S1** (P1) As an **agent**, I want to discover the available document tools, so I can act without hardcoded knowledge.
  Acceptance: `read_doc`, `apply_ops`, `get_versions`, `lock`, `presence` are discoverable; tool schemas are versioned and documented.
- **E13S2** (P1) As an **agent**, I want to read a document (bytes + structure), so I can reason before editing.
  Acceptance: `read_doc` returns content + op-log tail; unknown id → typed not-found result, never a 500.
- **E13S3** (P1) As an **agent**, I want to apply multi-step edits via ops, so my changes go through the same pipeline as humans.
  Acceptance: `apply_ops` reuses collab validation; malformed op → typed rejection; hub stays up.
- **E13S4** (P1) As an **agent**, I want versions/locks/presence as tools, so I coordinate instead of clobbering.
  Acceptance: lock tool returns the same `409` mismatch contract as human clients (extends `tests/test_wopi_protocol_property.py`).
- **E13S5** (P2) As an **agent developer**, I want stdio and HTTP transports, so local CLI agents and remote stacks both work.
  Acceptance: identical tool catalog over both transports; conformance test runs against each.
- **E13S6** (P2) As an **admin**, I want to enable/disable the tool surface per deployment, so agents are opt-in.
  Acceptance: disabled → tools unreachable (404/403); no agent code path active.

## E14 — Agent identity, permissions & consent
Status: 🔲 · Priority: P1
Agents are principals: attributed, scoped, consented, budgeted — least privilege by default.

- **E14S1** (P0) As a **reviewer**, I want agent edits attributable, so I can tell machine work from human work.
  Acceptance: agent ops carry an `agent=<name>` client_id; UI badge + audit rows tagged.
- **E14S2** (P1) As an **admin**, I want read-only vs edit scopes per host/deployment, so agents get least privilege.
  Acceptance: read-only agent calling `apply_ops` → `403`; scope checked server-side, not in the client.
- **E14S3** (P1) As a **user**, I want consent gating on what leaves the server, so private documents stay private.
  Acceptance: model calls fail-closed without an explicit consent flag (same Stoic gating as E8S4/E9).
- **E14S4** (P2) As an **operator**, I want per-agent op budgets and rate limits, so one agent can’t flood the hub.
  Acceptance: budget exceeded → `429` + structured log event; hub latency unaffected (benchmark).
- **E14S5** (P2) As an **admin**, I want a kill switch that revokes an agent mid-session, so incidents stop in seconds.
  Acceptance: revoke → the agent’s next tool call fails; presence shows it left; document consistent.
- **E14S6** (P2) As an **admin**, I want agent sessions to expire like tokens, so leaked agent credentials fade quickly (mirrors E11S3).
  Acceptance: expired session → `401`; refresh path covered; in-flight loop stops at the next tool call.
- **E14S7** (P1) As a **user**, I want an agent to never exceed *my own* permissions, so delegation can’t become escalation.
  Acceptance: agent effective scope = intersection(agent scope, user scope); a read-only share stays read-only for its agent (confused-deputy guard).

## E15 — Agents as collaborators
Status: 🔲 · Priority: P1
Agents join the CRDT hub as first-class clients — visible, lock-respecting, version-producing.

- **E15S1** (P1) As an **agent**, I want to run long multi-step edit loops, so complex tasks complete reliably.
  Acceptance: a 50-op loop converges to the model-reference expectation (agent-driven variant of `tests/test_collab_modelbased.py`).
- **E15S2** (P1) As a **co-author**, I want agents to appear in presence, so I know an agent is in the document.
  Acceptance: presence list includes agent entries with name/badge; leave cleans up.
- **E15S3** (P0) As a **team**, we need concurrent human+agent edits to merge without loss.
  Acceptance: interleaved human/agent op fuzz keeps both texts (property test with model-based oracle).
- **E15S4** (P1) As an **agent**, I must respect WOPI locks, so I never clobber an active editor.
  Acceptance: locked-document write without token → `409`; with token → applies.
- **E15S5** (P2) As a **user**, I want agent edits to land as versions, so history shows agent milestones.
  Acceptance: one version entry per agent batch, labelled with the agent id (extends `tests/test_persistence.py`).

## E16 — Review, transparency & control
Status: 🔲 · Priority: P1
Copilot’s lesson: **control is non-negotiable**. The op-stream diff is our review primitive —
no new data model, ops are already granular and revertible.

- **E16S1** (P0) As a **reviewer**, I want a diff of agent changes (op stream), so I see exactly what changed.
  Acceptance: pre/post-revision diff renders per-op; volatile fields normalized (reuses golden harness).
- **E16S2** (P0) As a **reviewer**, I want accept/reject per op and per batch, so I keep only good changes.
  Acceptance: reject re-emits the inverse op; document returns to its prior state; CRDT stays consistent.
- **E16S3** (P1) As a **user**, I want one-click rollback to the pre-agent revision, so mistakes are cheap.
  Acceptance: restore via the version store; concurrent editors receive the rollback (E3 interop).
- **E16S4** (P2) As a **cautious user**, I want dry-run/preview mode, so the agent proposes before it writes.
  Acceptance: preview returns proposed ops without applying; applying is a separate explicit call.
- **E16S5** (P2) As a **reviewer**, I want the agent’s stated rationale per batch, so review is informed.
  Acceptance: batch metadata carries rationale; shown in the review pane; stored in audit.

## E17 — Agent safety & anti-injection
Status: 🔲 · Priority: P1
Documents are hostile input — and now they can address the agent directly.

- **E17S1** (P0) As an **operator**, I want malformed agent ops rejected, so the hub never crashes.
  Acceptance: existing `collab.py` guards hold under agent-driver fuzz (`tests/test_resilience.py` style).
- **E17S2** (P1) As a **security engineer**, I want document-borne prompt injection contained, so document content can’t command the agent.
  Acceptance: document text is passed as data, never as instructions; an injection corpus asserts no tool calls originate from content.
- **E17S3** (P1) As an **operator**, I want runaway loops stopped, so an agent can’t spin forever.
  Acceptance: op/time budgets enforced; killing a session leaves the document consistent (fault-injection test).
- **E17S4** (P2) As a **security engineer**, I want the agent path under the sanitizer contract, so no XSS via agent-inserted HTML.
  Acceptance: `tests/test_sanitizer_adversarial.py` corpus runs against agent-written content; suppression test holds.
- **E17S5** (P2) As an **admin**, I want agent egress limited to the configured model endpoint, so documents can’t be exfiltrated.
  Acceptance: egress allowlist enforced; violation → blocked + audit event.

## E18 — Grounding & document Q&A
Status: 🔲 · Priority: P2
Our “Work IQ”: agents grounded in signals the server already stores.

- **E18S1** (P1) As an **agent**, I want a context pack (text + structure + recent versions), so edits respect document intent.
  Acceptance: pack endpoint deterministic, size-bounded, golden-tested.
- **E18S2** (P2) As an **agent**, I want to target edits by anchor/selection, so precise changes beat rewrites.
  Acceptance: anchor ops survive round-trip; invalid anchor → typed error.
- **E18S3** (P2) As a **student**, I want questions answered with citations to my passages, so I can verify.
  Acceptance: Q&A returns passage spans + document/version refs (extends E9S2).
- **E18S4** (P2) As a **user**, I want agents to see only documents I granted, so context never leaks across files.
  Acceptance: cross-document context denied without explicit grant; isolation test asserts it.

## E19 — Model pluggability & privacy
Status: 🔲 · Priority: P2
Multi-model matters; on-prem is our differentiator.

- **E19S1** (P1) As a **deployer**, I want provider-agnostic model configuration, so there is no vendor lock.
  Acceptance: `AgentRunner` adapters for ≥2 providers translate tool calls to identical ops (differential test).
- **E19S2** (P2) As a **privacy-conscious user**, I want a fully local model option, so nothing leaves the box.
  Acceptance: local-provider E2E edits a document with network egress disabled.
- **E19S3** (P2) As an **operator**, I want provider health checks + fallback, so a dead endpoint degrades gracefully.
  Acceptance: fallback order honored; provider failure → typed error, no document corruption (fault injection).
- **E19S4** (P2) As an **admin**, I want token/cost accounting per session, so spend is visible.
  Acceptance: usage rows in audit; per-agent aggregate endpoint.

## E20 — Agent observability, audit & ops
Status: 🔲 · Priority: P2
Tracing is a first-class primitive (the OpenAI/Anthropic pattern), tuned Stoic-small.

- **E20S1** (P1) As a **developer**, I want a structured trace per agent session, so failures are debuggable.
  Acceptance: trace records tool calls/ops/results; retention-bounded; content redacted by default.
- **E20S2** (P1) As an **admin**, I want agent actions in the audit log, so access is reviewable (extends E7S5).
  Acceptance: open/edit/version rows tagged with agent id + session.
- **E20S3** (P2) As an **operator**, I want an agents dashboard (active sessions, ops/s, failures), so health is visible.
  Acceptance: read-only page (Stoic: a page, not a SPA).
- **E20S4** (P2) As an **operator**, I want alerts on failure loops and budget exhaustion, so I react before users notice (extends E7S4).
  Acceptance: threshold hooks emit structured events; alert fires in a seeded scenario test.

## E21 — Workflows & multi-agent
Status: 🔲 · Priority: P2
Workflows (predefined pipelines) for predictability; free agents only where flexibility pays.

- **E21S1** (P2) As a **team**, we want predefined document workflows (draft → review → summary), so results are deterministic.
  Acceptance: workflow = ordered tool pipeline; golden-transcript tested.
- **E21S2** (P2) As an **agent**, I want handoffs to specialist agents (drafting → review), so capabilities compose.
  Acceptance: handoff preserves document state + audit chain; no privileged path is created.
- **E21S3** (P2) As a **user**, I want long jobs queued with progress, so the UI stays responsive.
  Acceptance: bounded job queue; progress via presence channel; cancel works.
- **E21S4** (P2) As an **operator**, I want scheduled jobs (e.g. nightly summarize), so routine work runs off-hours.
  Acceptance: schedule runs against granted documents only; failures alert (E20S4).
- **E21S5** (P2) As a **reviewer**, I want a workflow to pause for my approval between stages, so automated chains stay supervised.
  Acceptance: approval gate suspends the pipeline before the next stage; resume only after explicit approval; timeout → job parked with alert.

## E22 — Agent evaluation & quality gates
Status: 🔲 · Priority: P1
The SOTA harness extends to agents: **agent edits are just another untrusted input class.**

- **E22S1** (P0) As a **developer**, I want agent-output corpora in the property/fuzz suites, so integrity invariants hold for agent edits.
  Acceptance: an agent-driven Hypothesis state machine passes (convergence + never-500).
- **E22S2** (P0) As a **developer**, I want mutation coverage on the agent path, so guardrails have teeth.
  Acceptance: mutants (scope check dropped, `409` bypassed, budget removed) are all killed; score stays 100%.
- **E22S3** (P1) As a **developer**, I want golden agent transcripts, so protocol regressions are caught.
  Acceptance: a recorded session replays identically; `UPDATE_GOLDEN=1` workflow reused.
- **E22S4** (P1) As a **developer**, I want an agent regression benchmark, so quality/latency is tracked per change.
  Acceptance: benchmark task suite with time + op-count budgets in `tests/bench/`.
- **E22S5** (P2) As a **release manager**, I want E2E agent-vs-OCIS in CI, so the full flow is guarded.
  Acceptance: Playwright drives an agent editing via MCP on a disposable OCIS stack (extends E12S4).

## E23 — Agent UX in the editor
Status: 🔲 · Priority: P2
Consistent entry point, streaming progress, accessible by default.

- **E23S1** (P1) As a **user**, I want one consistent agent entry point across documents, so the interaction is learnable (Copilot’s consistency principle).
  Acceptance: same panel and commands on every document; fully keyboard reachable.
- **E23S2** (P1) As a **user**, I want live progress while the agent works, so waiting is informed.
  Acceptance: streaming status via the presence channel; cancel button stops the session.
- **E23S3** (P2) As a **user**, I want completion notifications, so I can switch tasks meanwhile.
  Acceptance: host notification on job end (extends E3S5 `notifyHost`).
- **E23S4** (P2) As a **screen-reader user**, I want agent changes announced, so I can follow along.
  Acceptance: `aria-live` region announces the batch summary; axe-core passes on the agent panel.
- **E23S5** (P2) As a **mobile user**, I want the agent panel usable at 360 px, so review works anywhere (extends E10S4).
  Acceptance: panel collapses; touch targets ≥ 44 px.

## Deliberately out of scope (Stoic rejections — for discussion before any of these become epics)

Per §7 of `RETHINK_WORLD_OFFICE.md` (apply to every merge): if it doesn’t serve
“edit office documents through OpenCloud”, reject it.

- **Desktop app / Tauri** — adds a packaging matrix with no OCIS value.
- **Plugin ecosystem** — the plugin-SDK cathedral is the anti-pattern we removed.
- **Canvas rendering** — DOCX↔HTML + contenteditable is the chosen 90% solution.
- **XLSX/PPTX editing** — out of the “one job” (documents); read-only preview if ever.
- **Multi-tenant SaaS / k8s** — single Docker image + systemd is the target shape.
- **PostgreSQL before SQLite is measured slow** — temperance: no caching/daemons before a measured need.
- **Agent marketplace / agent plugin SDK** — the plugin-SDK cathedral, again, with models.
- **Autonomous cross-app assistant** — agents act on documents through OpenCloud, nothing else.
- **Cloud-only agent runtime** — if it requires egress to a vendor cloud, it fails the consent gate (E14S3); local-model support is the Stoic answer.

> **Stoic pass note:** agentic AI (E13–E23) *serves* the one job — it is still “edit office
> documents through OpenCloud”, with an automated hand on the same op/lock/version control
> plane humans use. Anything that makes the agent a second, privileged way to edit is rejected.

---

## Backlog → change workflow

1. Pick a story; write its spec fragment (requirement + scenarios) in OpenSpec “Requirement” form.
   (Done for the agentic seed: `openspec/changes/agentic-ai-document-platform/` covers E13–E16, E22 core.)
2. Create `openspec/changes/<name>/` with `design.md`, `specs/…/spec.md`, `tasks.md`.
3. Gate: `uv run --extra dev pytest tests/ --ignore=tests/e2e` + `uv run ruff check src tests` + `node --check` on JS.
4. Ship via private branch → `merge --no-ff` → push (fleet-safe discipline).
