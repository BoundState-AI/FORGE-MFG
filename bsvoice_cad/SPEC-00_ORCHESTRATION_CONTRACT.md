---
title: "BSVoice-CAD SPEC-00 — Orchestration Contract"
subtitle: "The seven-module decomposition; interfaces only, no implementation"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-00 — Orchestration Contract

**Version:** 1.5.1
**Status:** DRAFT — cross-pass R3 findings applied + R4 cross-pass editorial reconciliation
**Hackathon application:** Submitted 2026-04-17; awaiting selection notification

**Changelog v1.5.0 → v1.5.1 (R4 cross-pass editorial reconciliation):**
- **C3 — Seam C closure-reason framing reconciled.** Prior text said SPEC-04 "records a session-closure event noting the reason." The DHF schema has no `closure_reason` column; SPEC-02 editorial M2 (v1.4.0+) established that closure attribution (explicit_shutdown / heartbeat_timeout / superseded_session) is observability-grade (Serilog `session_closed` event), not audit-chain-grade. Seam C text updated to match: SPEC-04 persists closure by setting `ended_at`; closure *reason* lives in the orchestrator's structured log pipeline, not in `dhf.sessions`.
- **H5 — Session Lock glossary entry + Seam C reference SPEC-04 §6.4.1.** The locked-session recovery procedure is close-and-new only in v1 (no programmatic unlock; see SPEC-04 v1.5.2 §6.4.1 for the authoritative operator procedure). Residual "unlock" language removed from both the Glossary entry and Seam C.
- **ACC master table — SPEC-07 entries renumbered.** SPEC-07 v1.1.0 introduced three ACCs under the 25–27 range that collided with SPEC-04 v1.5.0's same-numbered entries (durable session lock, recovery event, neutral landing). Master table now carries SPEC-07's entries as `ACC-28` (multi-domain resolution), `ACC-29` (decoration-payload assembly), `ACC-30` (bouncer cache isolation). SPEC-07 v1.1.2 performs the in-spec rename.
- **Depends-on updates throughout:** SPEC-04 references point to v1.5.2, SPEC-07 references point to v1.1.2.

**Changelog v1.4.0 → v1.5.0:**
- **§1 Module Map:** Added SPEC-07 (CAD Vocabulary Layer) as a seventh module. Clarified that SPEC-06 depends on SPEC-04 AND SPEC-01 (for display types `StateSnapshot`, `SelectionRef`, `FeatureNode` only; these are rendered as opaque JSON, never serialized or re-interpreted by the viewer). Fixes R3 R1-ISSUE-010.
- **§6 Glossary:** DHF Session closure reasons narrowed further. `explicit_shutdown` now means exclusively "add-in sent an `AddinTeardownMessage`"; transient transport loss (idle timeout, network drop) does NOT close the session, it remains open pending reconnect or heartbeat timeout. Fixes R3 R1-ISSUE-006.
- **§6 Glossary:** Recovery Event entry extended with persistence model: recovery events are stored as DHF entries with `turn_type = 'recovery'` and a new `recovery_context` JSONB column (SPEC-04 v1.5.0 §3 migration 009). Recovery events participate in the hash chain in normal `sequence_number` order alongside turn entries. Fixes R3 R1-ISSUE-004.
- **§4 SDR-TL-006:** Added a one-sentence clarification distinguishing "foundation" (data structure that could be extended toward compliance) from "partial compliance" (a false reading the framing could invite). Fixes R3 R1-ISSUE-010 (regulatory framing).
- **§3 Acceptance Test Cross-Reference:** Extended master ACC table with ACC-21 through ACC-24 owned by SPEC-07. Updated never-descopable gate list to include ACC-21/22 (vocabulary demo moments).
- **§4 Added SDR-TL-013:** Recovery event persistence model (chose path A: same table, `turn_type = 'recovery'`, `recovery_context` JSONB). Rationale: one chain, one tip lookup, one hash-chain rule. Alternative (separate table) doubles implementation complexity for a hackathon-scope system.
- **§4 Added SDR-TL-014:** `AddinTeardownMessage` exists in the `IAddinMessage` polymorphic family as of SPEC-01 v1.4.0. The add-in MUST send this message before process exit when graceful shutdown is possible; when graceful shutdown is not possible (crash, force-kill), the orchestrator detects the closure as `heartbeat_timeout` without distinguishing the root cause. Fixes R3 R1-ISSUE-007.

## 0. Why This Exists

BSVoice-CAD is seven logical modules that interact at well-defined seams. This document defines the module boundaries, the seam contracts between them, cross-cutting architectural decisions (SDRs), shared vocabulary, scope gates, and review ordering. Implementation details live in the per-module specs (SPEC-01 through SPEC-07). Oracle reviews each module spec in isolation; this document is the integration contract that lets those reviews compose without drift.

**Non-goals for this document:** state machine details, record field definitions, SQL schemas, build sequence, timeline. Those live in per-module specs.

**Deployment boundary governance:** Every v1 (hackathon) vs v2 (Medtronic production) deployment-coupled decision lives in `DEPLOYMENT_V1_V2_MIGRATION.md` (this directory). Per-spec sections reference it by section number and MUST NOT redefine v1/v2 semantics locally. SDR-TL-011 is the SPEC-00 anchor for that document.

## 1. Module Map

| Spec | Module | Owns | Lives In | Depends On |
|------|--------|------|----------|------------|
| SPEC-01 | Wire Protocol | Cross-process types, polymorphic contracts (including `AddinTeardownMessage`), JSON policy, WebSocket framing contract, version handshake, TLS + API-key handshake (v1), `CallId` format (UUIDv7) | `BSVoiceCAD.Protocol` | (none — leaf module) |
| SPEC-02 | SolidWorks Adapter | STA command queue, `ISolidWorks*Service` contracts, COM-to-`ToolError` mapping, AddinSessionId generation algorithm, screenshot base64 producer, teardown message send conditions | `BSVoiceCAD.Addin` | SPEC-01 |
| SPEC-03 | Tool-Call Lifecycle | Call state machine, timeouts, cancel protocol, quarantine rules, per-turn DHF aggregation, post-timeout recovery | `BSVoiceCAD.Orchestrator.Lifecycle` + `BSVoiceCAD.Addin.Bridge` | SPEC-01, SPEC-02, SPEC-04 |
| SPEC-04 | DHF Ledger | Schema, hash model, append logic, DHF session lifecycle, read-only repository projection, tenant-scoping, durable session lock, screenshot BYTEA storage, recovery-event persistence | `BSVoiceCAD.Orchestrator.Dhf` + `BSVoiceCAD.Orchestrator.Database` | SPEC-01 (message shapes), SPEC-00 §6 (AddinSessionId epoch semantics) |
| SPEC-05 | Agent Loop + Input | User input, confirmation flow, Opus 4.7 tool-calling, vision context, retry correlation, system prompt | `BSVoiceCAD.Orchestrator.Agent` + `BSVoiceCAD.Orchestrator.Input` | SPEC-01, SPEC-03, SPEC-07 (vocabulary resolution before tool-call construction) |
| SPEC-06 | Viewer | Timeline rendering, DHF read API, integrity endpoints, Railway-hosted at `vcad.boundstate.ai` | `BSVoiceCAD.Orchestrator.Viewer` | SPEC-04 (read-only), SPEC-01 (`StateSnapshot`, `SelectionRef`, `FeatureNode` — display types only, treated as opaque JSON) |
| SPEC-07 | CAD Vocabulary Layer | BSMS bouncer, query planner, `IVocabRepository`, Sonnet synthesis, citation format, DHF decoration integration | `BSVoiceCAD.Orchestrator.Vocab` + `BSVoiceCAD.Orchestrator.Database` | SPEC-04 (decoration table extension for `vocabulary_resolution` type) |

**Dependency direction is strictly downward.** SPEC-01 depends on nothing. SPEC-06 depends on SPEC-04 and SPEC-01 (display types only). SPEC-07 depends on SPEC-04. Circular references are a defect.

**SPEC-06 → SPEC-01 dependency scope:** This is a narrow dependency for display-only types (`StateSnapshot`, `SelectionRef`, `FeatureNode`). These types appear as nested JSON blobs in DHF entry details and are rendered by the viewer as opaque JSON; the viewer does not deserialize them into typed objects or extract fields programmatically. The NetArchTest rule permits `BSVoiceCAD.Orchestrator.Viewer.*` to reference `BSVoiceCAD.Protocol.*` for these display types ONLY. Cross-process DTOs (message types, tool inputs/results) are forbidden in the viewer by the same architecture test — the viewer is not a protocol consumer, only a display consumer.

**Assembly mapping note:** The "Lives In" column names the primary assembly (`.csproj`) for each module. Where two assemblies are listed (separated by `+`), the first targets the orchestrator runtime (.NET 8, Railway-hosted) and the second targets the add-in runtime (.NET Framework 4.8, local Windows workstation). The architecture-test project (`BSVoiceCAD.Tests.Architecture`) must reference all assemblies from both runtimes to enforce cross-runtime dependency rules. SPEC-03's `BSVoiceCAD.Addin.Bridge` assembly contains only the add-in-side dispatch surface; all lifecycle state and DHF aggregation logic lives in `BSVoiceCAD.Orchestrator.Lifecycle`.

**AddinSessionId epoch semantics** are defined in this document's glossary (§6) so that SPEC-04 can consume them without a dependency on SPEC-02. SPEC-02 owns the generation algorithm; SPEC-01 owns the format regex and epoch rotation contract; SPEC-04 consumes only the epoch rotation invariants defined here.

## 2. Seams (Where The Modules Touch)

Each seam enforces one-directional dependency. **Compile-time** dependency direction is verified by `NetArchTest` rules living in a dedicated architecture-test project (`BSVoiceCAD.Tests.Architecture`) that is permitted to reference all module assemblies. **Runtime** seam behavior — transport correctness, serialization round-trips, reconnect semantics, threading invariants — is verified by integration and contract tests in `BSVoiceCAD.Tests.Integration` (see §3). Per-module test projects test only their own module's internal invariants.

### Seam A: SPEC-01 → everyone

Protocol types are consumed by every other module except where explicitly narrowed (SPEC-06 imports display types only; see §1). No module defines its own cross-process wire types. Architecture rule: no cross-process DTO types (types serialized over the WebSocket transport) exist outside `BSVoiceCAD.Protocol.*` namespaces. Internal module-boundary types (e.g., `DhfTurnContext` in SPEC-04, `VocabularyResolution` in SPEC-07) are permitted provided they do not cross the WebSocket boundary.

The cross-process transport between the .NET Framework 4.8 add-in (local workstation) and the .NET 8 orchestrator (Railway-hosted at `vcad.boundstate.ai`) is a **public-internet WebSocket** connection with TLS termination at the Railway edge, API-key authentication in the handshake (v1), and JSON message framing. The full transport contract — including connection lifecycle, error semantics, reconnect behavior, threading guarantees, auth handshake, and TLS — is defined in SPEC-01. For v1 → v2 transport evolution (API key → OAuth device flow), see `DEPLOYMENT_V1_V2_MIGRATION.md` §2.

### Seam B: SPEC-03 → SPEC-02

The tool-call lifecycle dispatches `IToolInput` to the SolidWorks adapter and expects `IToolResult` or `ToolError` back. The adapter never owns lifecycle state; the lifecycle never owns COM threading. SPEC-02 provides a synchronous method surface; SPEC-03 wraps each call in its own timeout and cancel semantics.

**Cancellation boundary:** Cancellation is effective only before a COM call is dispatched to the STA thread. Once a synchronous COM call is in flight, it runs to completion; the STA thread cannot be safely interrupted. A call whose orchestrator-side timeout expires while COM execution is in progress is recorded as `TimedOut`, but the COM operation may still complete and mutate the model.

**Post-timeout recovery (mandatory — never descopable):** Before any subsequent turn may open, SPEC-03 must execute a recovery flow. SPEC-03 defines the deterministic selection rule between the two permitted strategies (state refresh via feature-tree comparison, or undo-group rollback) and the observable success criterion for each. If recovery itself fails, the session enters a locked state that prevents further turns until manual intervention. The recovery outcome — including whether a post-timeout COM completion was detected and whether the model was rolled back or reconciled — is recorded as a DHF recovery event via `DhfLedger.AppendRecoveryEventAsync` (defined in SPEC-04) before the next turn opens. Recovery events are persisted as DHF entries with `turn_type = 'recovery'` and a `recovery_context` JSONB column carrying the structured outcome (see SDR-TL-013 and SPEC-04 §5.1). The entry participates in the hash chain in normal `sequence_number` order.

**Turn completion boundary for timeouts:** A timed-out turn closes at the moment the timeout fires; the turn's DHF entry records the `TimedOut` status for the affected tool call(s). Any post-timeout recovery constitutes a separate recovery event, not a continuation of the timed-out turn. The hash chain includes both the turn entry and the recovery event in sequence.

### Seam C: SPEC-03 → SPEC-04

The lifecycle layer aggregates all tool-call outcomes within a single turn and produces one DHF write per completed turn. Upon turn completion, SPEC-03 constructs a `DhfTurnContext` (defined in SPEC-04) containing all tool-call results for that turn and hands it to `DhfLedger.AppendTurnAsync`. The DHF layer never observes individual lifecycle transitions directly; it receives complete, aggregated turn records only. The hash chain covers turn-level entries and recovery events.

**Incomplete turns (crash, disconnect, kill mid-turn):** Turns that fail to complete do not produce DHF turn entries, because SPEC-03 has not yet constructed the `DhfTurnContext`. On session closure (detected via heartbeat timeout, explicit teardown message, or reconnect with a new AddinSessionId), SPEC-04 persists session closure by setting `ended_at` on the `dhf.sessions` row. **Closure REASON (`explicit_shutdown`, `heartbeat_timeout`, `superseded_session`) is recorded in orchestrator structured logs (Serilog `session_closed` event) per SPEC-02 §9.0.4, NOT in the DHF schema** (v1.5.1 C3 reconciliation). Closure attribution is observability-grade; DHF audit integrity does not depend on closure reason. On the next session start, SPEC-03 must perform state reconciliation (feature-tree comparison against last recorded turn) before opening the first new turn. If the reconciliation detects model state that diverges from the DHF record, SPEC-03 records a reconciliation event via `DhfLedger.AppendRecoveryEventAsync`. The undo group for an incomplete execution turn was never closed; SolidWorks behavior for unclosed `UndoObjectScope` is documented in SPEC-02.

**DHF append failure locks the session (never descopable):** If `DhfLedger.AppendTurnAsync` fails after all retries, the orchestrator MUST NOT silently continue. The session is marked locked via `IDhfRepository.LockSessionAsync` which persists `locked_at` and `lock_reason` columns on the session row (SPEC-04 v1.5.2 §6.4). The lock survives orchestrator restart — `OpenOrResumeSessionAsync` rejects locked sessions per SPEC-04 §6.1. No further turns may be opened on a locked session; the user is notified via the agent output channel. **Recovery from a locked session is close-and-new only in v1** — operator closes the locked session via SQL (`UPDATE dhf.sessions SET ended_at = NOW() WHERE session_id = X`, permitted even when locked) and the add-in reconnects with a new AddinSessionId. No programmatic unlock exists in v1; see **SPEC-04 v1.5.2 §6.4.1 for the authoritative operator procedure**. This invariant is codified in SPEC-04 §11 (DHF-014) and enforced at the lifecycle layer.

### Seam D: SPEC-05 → SPEC-03

The agent loop submits typed tool calls to the lifecycle layer and awaits terminal outcomes. The agent never touches the WebSocket, never touches the STA queue, never writes to the DHF. It is a pure consumer of the lifecycle surface. SPEC-05 has no compile-time dependency on SPEC-04; all DHF interaction is mediated by SPEC-03.

### Seam E: SPEC-05 → SPEC-04 (indirect, via SPEC-03)

The agent loop opens turns but does not write DHF entries directly. SPEC-03 is the sole DHF writer. This keeps turn construction in one place. SPEC-05 has no direct assembly reference to SPEC-04; the dependency is purely indirect through SPEC-03's aggregation role.

### Seam F: SPEC-06 → SPEC-04, SPEC-01 (display types)

The viewer queries `IReadOnlyDhfRepository` (defined in SPEC-04 §9.2 as a read-only projection of the full `IDhfRepository`) read-only. No write paths exist from the viewer. The viewer's isolated DI container resolves only `IReadOnlyDhfRepository`; the write-capable `IDhfRepository` is not registered. `ValidateChainAsync` on `IReadOnlyDhfRepository` is strictly side-effect-free per SPEC-04 v1.5.0 — it never writes integrity-event decorations or any other rows. This architectural isolation is enforced by both a DI-container test and a NetArchTest rule (SPEC-06 SDR-06-003).

The viewer also imports display-only types from SPEC-01 (`StateSnapshot`, `SelectionRef`, `FeatureNode`) to render their JSON-serialized content within DHF entry details. These types are treated as opaque JSON by the viewer — never deserialized into typed objects programmatically. The architecture test limits SPEC-06 → SPEC-01 references to these display types only.

### Seam G: SPEC-05 → SPEC-07

The agent loop calls `IVocabularyResolver` (defined in SPEC-07) for every user utterance BEFORE constructing tool calls. The vocabulary layer produces `VocabularyResolution` objects (resolved canonical IDs with citations, or ambiguity escalations). The agent translates resolved terms into structured tool-call parameters; ambiguity escalations become `ambiguity_resolution` turn types per SPEC-04 glossary. The agent never queries the vocabulary database directly.

### Seam H: SPEC-07 → SPEC-04

The vocabulary layer emits decoration payloads (`VocabularyResolutionDecoration`) that SPEC-04's `DhfDecorationWriter` persists with `decoration_type = 'vocabulary_resolution'`. SPEC-07 requires SPEC-04 to include this decoration type in the CHECK constraint (migration 007 per SPEC-07 §8.1). The vocabulary layer is a producer of decorations, not a writer — all persistence goes through SPEC-04's decoration writer.

## 3. Mandatory vs Stretch (Top-Level)

Mandatory scope covers the 9-tool demo (`new_part`, `start_sketch`, `draw_rectangle`, `draw_circle`, `add_dimension`, `exit_sketch`, `extrude`, `screenshot`, `get_feature_tree`), text input via ConsoleInputService, undo groups, per-turn DHF with hash chain, minimal viewer timeline at `vcad.boundstate.ai`, two vocabulary-demo moments (SPEC-07 §10.1). Per-module mandatory/stretch lists live in each module spec.

**Descope priority (LIFO):** Installer, Demo 2, viewer polish, DHF extraction, error recovery UX (user-facing retry messaging and graceful degradation prompts), additional grounding tools beyond the mandatory two (`screenshot`, `get_feature_tree`), Sonnet 4.6 decoration extraction, patterns, voice, Clerk viewer auth (deferred to v1.5; see SDR-TL-012), all stretch. **Never descoped:** the 9 mandatory tools (including `screenshot` and `get_feature_tree`), per-turn DHF, hash chain at entry level, minimal viewer timeline, undo groups, text input, post-timeout state reconciliation (Seam B mandatory recovery), **DHF append failure locks the session durably** (Seam C mandatory behavior), WebSocket TLS + API-key handshake, neutral-landing-page access model (SPEC-06 SDR-06-007), ACC-01/02/03/04/08/10/12/19/20/21/22 acceptance tests.

### Acceptance Test Cross-Reference

The following ACC IDs are referenced as never-descopable scope gates. Each is fully defined in its owning module spec.

| ACC ID | Capability Gate | Defined In |
|--------|----------------|------------|
| ACC-01 | 9 mandatory tools execute end-to-end producing geometry | SPEC-03 |
| ACC-02 | Per-turn DHF append with hash chain integrity | SPEC-04 |
| ACC-03 | Undo group wraps each execution turn | SPEC-02 |
| ACC-04 | Text input round-trip through agent loop | SPEC-05 |
| ACC-08 | Screenshot and feature-tree grounding return valid data | SPEC-02 |
| ACC-10 | Viewer displays turn timeline from DHF | SPEC-06 |
| ACC-12 | Hash chain verification passes on replayed session | SPEC-04 |
| ACC-19 | Cross-workstation AddinSessionId conflict throws `CrossWorkstationSessionConflictException` | SPEC-04 |
| ACC-20 | Viewer timeline renders a 20-entry session in under 1 second on the demo laptop | SPEC-06 |
| ACC-21 | Voice utterance "tap a 6-32 UNC hole" resolves to `ASME_B1_1:6-32_UNC_2B` with ASME B1.1 citation in DHF | SPEC-07 |
| ACC-22 | Voice utterance "specify medical grade titanium" resolves to `ASTM_F136:Ti_6Al_4V_ELI` with ASTM F136 citation in DHF | SPEC-07 |
| ACC-23 | Bouncer rejects adversarial input ("make it look cool", injection patterns) with structured `VocabularyResolutionRejected` | SPEC-07 |
| ACC-24 | Ambiguous utterance produces `AmbiguityEscalation`; agent opens `ambiguity_resolution` turn | SPEC-07 |
| ACC-25 | DHF session lock after append failure survives orchestrator restart | SPEC-04 |
| ACC-26 | Recovery event persists structured context; `turn_type = 'recovery'` entries queryable by recovery_context fields | SPEC-04 |
| ACC-27 | Neutral landing page (`GET /`) makes no repository call and exposes no session data | SPEC-06 |
| ACC-28 | Multi-domain utterance produces `PipelineResult` with one `DomainResolution` per candidate domain; `PipelineOutcome = AllResolved` when all resolve | SPEC-07 |
| ACC-29 | `PipelineResult.DecorationPayload` is correctly assembled for all outcome variants; SPEC-07 does NOT persist — SPEC-05 does, after `AppendTurnAsync` returns entry_id | SPEC-07 |
| ACC-30 | Bouncer cache with two utterances normalizing to the same key preserves each request's own raw utterance in its DHF decoration payload | SPEC-07 |

**ACC numbering notes:**
- ACC-20 was renumbered from SPEC-06's original ACC-08 to resolve the cross-spec numbering collision flagged in cross-pass Oracle R2. SPEC-02's ACC-08 is the canonical holder of the number.
- ACC-21 through ACC-24 were added in SPEC-00 v1.5.0 to accept SPEC-07's acceptance tests into the master table.
- ACC-25 through ACC-27 were added in SPEC-00 v1.5.0 to codify R3 critical-finding fixes as never-descopable gates (durable session lock, recovery event structured storage, neutral landing page).
- **ACC-28 through ACC-30 were renumbered into the master table in v1.5.1 (R4 cross-pass H1 fix).** SPEC-07 v1.1.0 had introduced these three entries under the 25–27 range, colliding with the SPEC-04 / SPEC-06 entries above. SPEC-07 v1.1.2 performs the in-spec rename; this master table accepts them under the new numbers. ACC-29 scope was also narrowed in the C1 architectural fix: SPEC-07 assembles the decoration payload, SPEC-05 persists it.

### Integration and E2E Test Project

Cross-module acceptance tests (ACC-01, ACC-04, any test requiring multiple module assemblies or external dependencies) are owned by `BSVoiceCAD.Tests.Integration`. This project is permitted to reference all module assemblies and requires the following runtime dependencies: a SolidWorks instance (or a documented test double for CI), a PostgreSQL database (EmbeddedPostgres permitted), and either a live Anthropic API key or a recorded-response test double. ACC tests that can be verified within a single module (ACC-02, ACC-03, ACC-08, ACC-10, ACC-12, ACC-19 through ACC-27) may live in that module's own test project, but must also have an integration-level smoke test in `BSVoiceCAD.Tests.Integration` to verify cross-module wiring.

**Test project naming is canonical:** `BSVoiceCAD.Tests.Integration`, `BSVoiceCAD.Tests.Architecture`, `BSVoiceCAD.Orchestrator.Tests`, `BSVoiceCAD.Addin.Tests`, `BSVoiceCAD.Protocol.Tests`.

## 4. Top-Level SDRs (Decisions That Touch Multiple Specs)

SDRs local to a single module live in that module's spec. These cross-cut.

**SDR-TL-001: All-.NET.** Add-in .NET Framework 4.8 (SW COM requirement), orchestrator .NET 8 (Railway-hosted), shared Protocol .NET Standard 2.0 with `LangVersion=latest`. Locked.

**SDR-TL-002: PostgreSQL 16 with EmbeddedPostgres local-dev fallback.** Production v1 uses Railway-managed Postgres 16. EmbeddedPostgres is a local-dev convenience only — not a deployable fallback. No SQLite. Locked.

**SDR-TL-003: Direct Anthropic API.** Opus 4.7 primary. Sonnet 4.6 for decoration extraction and SPEC-07 bouncer + synthesis is stretch scope for extraction, mandatory for SPEC-07 (two LLM roles: bouncer persona and synthesis; both run on Sonnet 4.6 per SPEC-07 §4.2, §6.2). No Bedrock for hackathon. Locked (Opus 4.7 mandatory for agent loop; Sonnet 4.6 mandatory for SPEC-07; Sonnet 4.6 decoration-extraction still stretch).

**SDR-TL-004: Code license MIT; spec classification Proprietary.** Locked.

**SDR-TL-005: Hackathon selection gate.** Application submitted 2026-04-17; awaiting selection notification from Anthropic hackathon organizers. Eligibility-neutral build work (protocol types, schema, CLAUDE.md, test infrastructure) proceeds now. Submission-affecting work (video recording, public repo publication, LICENSE commit) blocks on confirmed selection. If not selected, the build still produces a working SolidWorks voice copilot that serves as the reference implementation for the Medtronic partnership pitch.

**SDR-TL-006: DHF regulatory framing.** Positioned as "design audit log foundation for future 21 CFR 820.30 / Part 11 alignment." **This system is NOT 21 CFR Part 11 compliant and MUST NOT be represented as such to customers, partners, or regulators.** The gap-analysis table in SPEC-04 enumerates specific Part 11 requirements not yet met (e.g., electronic signatures, validated system status, audit trail completeness under 21 CFR 11.10).

**Clarification (v1.5.0):** "Foundation" in this SDR means "a data structure that could be extended toward compliance in future work," NOT "a partial implementation of any compliance requirement." The system does not partially satisfy 21 CFR Part 11; it satisfies none of it. The foundation framing describes architectural readiness (append-only chain, immutable entries, structured audit trail), not regulatory progress. This clarification prevents a non-technical audience from reading "foundation" as "we're most of the way there." Governance owner for gap-analysis maintenance and any compliance claims: Dr. Joseph Wilde (BoundState AI CEO). Any customer-facing artifact (pitch deck, demo, documentation) that references 21 CFR or Part 11 must include the non-compliance disclaimer verbatim AND this clarification.

**SDR-TL-007: camelCase JSON property policy; PascalCase enum string values.** All wire formats and DHF JSONB payloads use camelCase property names. All enums serialize as PascalCase strings via `JsonStringEnumConverter`. Enforcement is split by ownership: wire-format serialization rules are enforced build-breaking in SPEC-01 tests; DHF JSONB serialization rules are enforced build-breaking in SPEC-04 tests. Both test suites verify round-trip fidelity for their respective type sets.

**SDR-TL-008: Opus 4.7 plays two roles (mandatory), not three.** Vision interpreter (context-only; enriches agent prompt with structured observations from recent screenshot) and tool-calling agent. Same vendor, same model family, two uses. No multi-vendor consensus layer at runtime. The system relies on single-model inference with extended thinking enabled; the DHF provides external traceability including ambiguity events. No testable "self-verification" contract is assumed at the model level. (SPEC-07 bouncer + synthesis and optional Sonnet 4.6 decoration extraction run on Sonnet 4.6 — distinct model, distinct roles; not counted in Opus 4.7's two roles.)

**SDR-TL-009: No vector retrieval. BSMS-shaped architecture only.** BSVoice-CAD's runtime MUST NOT use FAISS, any other vector database, embedding-based similarity search, or any retrieval mechanism whose result quality is measured by cosine distance. This applies to CAD vocabulary resolution, feature-tree querying, precedent lookup, ambiguity resolution, and any other place where the temptation arises to "just embed the thing and do nearest-neighbor."

The architectural alternative — required for every current and future knowledge-retrieval need in this product — is the BoundState BSMS pattern: **B**ouncer (domain-persona input gate that classifies intent and rejects out-of-scope or malicious input), **S**onnet reasoning over structured candidates, **M**APTIS-equivalent ground-truth database query against a structured Postgres schema, **S**onnet synthesis with row-level citations. This pattern is validated in production at NASA MSFC (MIRA) and is the mandated retrieval architecture for every BoundState product.

**Rationale:**
1. Vector retrieval fails open under domain drift. When a query doesn't match stored content, FAISS returns the nearest thing in embedding space, which is confidently wrong. BSMS fails closed — the database either has the row or it doesn't, and the absence is a first-class signal.
2. Vector retrieval cannot be audited. Regulated environments (medtech, aerospace, defense) cannot accept "cosine similarity 0.87 to a document we never showed you." BSMS produces SQL-queryable audit trails where every answer points to specific rows.
3. Vector retrieval accumulates complexity without bound. The failure mode is always "one more reranker, one more chunk-size parameter, one more hybrid-search layer, one more embedding model swap." Architectures that require continuous patching to stay useful are signaling that their foundation is wrong.
4. BSMS is structurally hallucination-resistant. Sonnet reasons over concrete rows; the bouncer rejects adversarial input before it reaches reasoning; the database is the ground truth. There is no path by which the model invents a material, tool, parameter, or precedent that does not exist in the schema.

**Scope of this SDR within BSVoice-CAD (hackathon):** SPEC-07 is the canonical BSMS application for CAD vocabulary. Bouncer persona, query planner, Sonnet synthesis, citation format are all specified there. Vision Context (SDR-TL-008) is enrichment from the current screenshot, not retrieval.

**Scope of this SDR post-hackathon (CAD-BSMS roadmap):** SPEC-07's corpus expansion (per `CAD_VOCAB_CORPUS.md`) proceeds in documented phases. It is BSMS at every phase. It will never be FAISS.

**Enforcement:** Architecture review for any runtime change in BSVoice-CAD that introduces a retrieval mechanism MUST verify the mechanism is BSMS-shaped. Reviewer checklist: (1) is there a domain-persona bouncer? (2) is the ground-truth store a structured database (Postgres, not vector)? (3) is reasoning performed over typed rows, not cosine-scored chunks? (4) do results carry row-level citations? If any answer is no, the change is rejected. This enforcement lives in the cross-spec architecture review, not in per-module test code (because the test for "did you use FAISS" is a structural review question, not a unit-test assertion). SPEC-07 §11.6 adds a NetArchTest rule enforcing "no embedding libraries" in the vocabulary module — the first concrete instance of this enforcement.

**Locked. Applies to all descendants of this architecture (CAD-BSMS, Manufacturing-BSMS, future verticals). See `D:\BoundState\BOUNDSTATE_PLATFORM_PRINCIPLES.md` for the cross-product governance statement.**

**SDR-TL-010:** (Reserved — assigned in a prior draft to a placeholder that did not survive editorial review. Not reused, to preserve traceability of cross-reference numbering in the findings reports.)

**SDR-TL-011: Deployment boundary — Railway-hosted orchestrator and viewer at `vcad.boundstate.ai`; add-in runs local on Windows workstation.** The WebSocket between add-in and orchestrator crosses the public internet, with TLS termination at the Railway edge and API-key authentication in the handshake. This explicitly reverses any implicit "localhost-only" assumption carried through earlier drafts. The full v1 transport contract (TLS, auth, rate limiting, reconnect) is specified in SPEC-01 §12. v1 → v2 evolution path (API key → OAuth device flow) is specified in `DEPLOYMENT_V1_V2_MIGRATION.md` §2.

**Viewer hosting:** Viewer routes mount at the service root (`/`) per SPEC-06 v1.4.0 and migration doc §1 / §4 (v1.1.0). There is no `/viewer` path prefix. WebSocket lives at `/ws`.

**Threat model for v1:** The WebSocket is authenticated but the viewer is not (see SDR-TL-012). Session URLs are UUIDv4 capabilities. `GET /` is a neutral landing page that does NOT expose session data. v1 is defensible for: (a) the hackathon demo, (b) the initial Medtronic "look at the product" walkthrough, (c) any session where the design content does not include PHI, PII, or trade-secret medical-device designs. v1 is NOT defensible for actual regulated customer use.

**Locked.**

**SDR-TL-012: Viewer auth deferred to v1.5 (post-hackathon).** v1 viewer has no authentication. Access control is "knowledge of the session UUID" — a UUIDv4 capability with ~122 bits of entropy. The landing page at `https://vcad.boundstate.ai/` is a neutral static page that does NOT list or auto-load sessions. Session URLs (`https://vcad.boundstate.ai/session/{sessionId}`) are reachable by anyone who has the URL. Clerk SSO integration is the v1.5 upgrade path and is estimated at 15-30 minutes of self-contained integration work (middleware on viewer routes, Clerk session cookie, no changes to DHF schema, orchestrator WebSocket, or protocol). Clerk integration is **not** a v1→v2 protocol change and does not bump `ProtocolVersion`. The v1.5 timing is "whenever a customer relationship requires it." Auth0 is the documented fallback if Clerk is unavailable (ITAR-restricted customers or air-gapped deployment). See `DEPLOYMENT_V1_V2_MIGRATION.md` §4 for the migration mechanism.

**Known sidebar-exposure trade-off:** Once a caller presents a valid session UUID, they can see other session UUIDs and metadata for the same tenant via the sidebar list (supports UX for switching between sessions in a tenant). This reduces the effective entropy of URL-as-capability from 122 bits per session to 122 bits for "first session access within a tenant." Accepted in v1; addressed by Clerk in v1.5.

**Rationale for deferring:** The viewer is the thing Medtronic will look at first, but the first look does not include PHI. The hackathon timeline is tight, and Clerk integration competes with higher-priority work (the 9 mandatory tools, the DHF append path, the deploy-to-Railway path). Deferring is cheap because Clerk slots in as middleware without touching any architectural boundary.

**Locked.**

**SDR-TL-013 (v1.5.0): Recovery event persistence model — same table, `turn_type = 'recovery'`, `recovery_context` JSONB column.** Recovery events (SPEC-00 Seam B, SPEC-04 glossary definition) are persisted as DHF entries in the same `dhf.entries` table as turn entries, with `turn_type = 'recovery'` added to the CHECK constraint and a new nullable `recovery_context` JSONB column carrying structured fields (`trigger`, `strategy`, `success`, `failureReason`, `modelStateDivergenceDescription`). Recovery entries participate in the hash chain in normal `sequence_number` order alongside turn entries; their `previous_hash` is the prior entry's `record_hash`, without regard to that entry's `turn_type`. The `recovery_context` JSONB is hashed into `record_hash` via `DhfHashColumns` (SPEC-04 §4.2).

**Alternative considered and rejected:** Separate `dhf.recovery_events` table with its own sequence numbering and hash-chain interleaving rules. Rejected because (a) doubles hash-chain complexity (two sequence counters, two tip lookups, cross-table interleaving rules), (b) complicates read-model queries (the viewer would need to merge and sort two tables per session), (c) provides no meaningful audit-integrity benefit over the same-table approach.

**Consequences:**
- `turn_type` CHECK constraint extended to include `'recovery'` (SPEC-04 v1.5.0 migration 009)
- `recovery_context JSONB NULL` column added to `dhf.entries` (SPEC-04 v1.5.0 migration 009)
- `DhfTurnContext` gains an optional `RecoveryContext` field used when the caller is SPEC-03's recovery flow
- The viewer renders recovery-type entries with a distinct visual treatment (SPEC-06 v1.4.0 §6 color coding)
- `ChainValidationResult` covers recovery entries identically to turn entries

**Locked.**

**SDR-TL-014 (v1.5.0): `AddinTeardownMessage` exists for explicit graceful shutdown; `explicit_shutdown` DHF closure requires receipt of this message.** The `IAddinMessage` polymorphic family (SPEC-01 v1.4.0 §4.2) includes `AddinTeardownMessage`. The add-in sends this message before process exit when graceful shutdown is possible (user clicks "Disconnect" in SolidWorks Add-in Manager, SolidWorks initiates clean shutdown). When graceful shutdown is not possible (SolidWorks crash, process force-kill, OS shutdown), the orchestrator detects closure as `heartbeat_timeout` without distinguishing the root cause. This resolves the R3 R1-ISSUE-007 gap where `explicit_shutdown` referenced a message type that did not exist in the protocol.

**Consequences for glossary (§6 DHF Session):**
- `explicit_shutdown`: triggered EXCLUSIVELY by receipt of `AddinTeardownMessage` from the add-in. Transient transport loss (idle timeout, network drop, WebSocket error) does NOT trigger `explicit_shutdown` — the session remains open pending reconnect or heartbeat timeout.
- `heartbeat_timeout`: triggered by absence of heartbeat exceeding threshold. Root cause is unknown from the orchestrator side.
- `superseded_session`: triggered by a new `AddinSessionId` arriving on a new `AddinReadyMessage` while a prior session for the same tenant+workstation is open.

**Locked.**

## 5. Review Order

Oracle reviews modules in dependency order so lower-numbered specs stabilize before higher ones consume them.

1. SPEC-00 (this document) — establishes module boundaries
2. SPEC-01 (Wire Protocol) — leaf module, gates everything
3. SPEC-02 (SolidWorks Adapter) — no circular dependency with SPEC-03
4. SPEC-04 (DHF Ledger) — depends on SPEC-01 message shapes and SPEC-00 §6 epoch semantics; can converge in parallel with SPEC-02
5. SPEC-03 (Tool-Call Lifecycle) — depends on SPEC-01, SPEC-02, SPEC-04
6. SPEC-07 (Vocabulary Layer) — depends on SPEC-04 decoration table; can converge after SPEC-04
7. SPEC-05 (Agent Loop + Input) — depends on SPEC-01, SPEC-03, SPEC-07
8. SPEC-06 (Viewer) — depends on SPEC-04 and SPEC-01 display types; can be deferred

After all individual passes converge, one integration Oracle pass on SPEC-00 catches any remaining cross-cuts. Expected cross-cut defect count: low, because the seams above define them away.

## 6. Glossary (Terms Used Across Multiple Specs)

- **Turn:** One user utterance + the agent's response, possibly containing 0+ tool calls. Turns are the DHF-entry granularity; one DHF entry per completed turn.
- **Turn type:** One of `execution` (produces geometry and an undo group), `clarification` (agent asks; zero tool calls; no undo group), `confirmation` (agent requests user approval; zero tool calls; no undo group), `ambiguity_resolution` (clarification turn where voice and vision disagreed on target entity; DHF records both inputs and the resolution), or `recovery` (a post-timeout, post-crash, or post-disconnect reconciliation event; persisted with structured `recovery_context` per SDR-TL-013). Recovery entries are NOT turn entries in the colloquial sense (no user utterance) but share the same storage model.
- **Undo Group:** The SolidWorks UndoObjectScope wrapping all tool calls in one turn. One undo group per execution turn; zero for non-execution turns.
- **AddinSessionId:** 32-character lowercase hex string (format regex `^[0-9a-f]{32}$`) generated fresh on each add-in load. Derivation algorithm — including hash function, input fields, and fallback behavior — is specified in SPEC-02 §8; SPEC-01 §9 specifies only the format contract and validator. **Epoch semantics (consumed by SPEC-04, authoritative source is this glossary):** A new AddinSessionId signals a new epoch. On epoch change: (1) StateVersion resets to 0; (2) the current DHF session (if any) is closed with reason `superseded_session`; (3) a new DHF session opens on the first AddinReadyMessage carrying the new AddinSessionId. Rotation invariant: AddinSessionId changes if and only if the add-in is reloaded (SolidWorks restart, add-in disable/re-enable, or crash recovery). A given SolidWorks process runs exactly one add-in instance; cross-workstation collisions on the same AddinSessionId are handled as a hard error per SPEC-04 SDR-04-008.
- **StateVersion:** Monotonic per-AddinSessionId counter carried by state-bearing messages. Resets to 0 on new AddinSessionId epoch.
- **Tool-Call Lifecycle State:** `Sent → Completed | TimedOut | Cancelled | Abandoned`. Terminal states are final. The `ToolCallLifecycleState` enum is defined in SPEC-01 (cross-process type; consumed by SPEC-03 and SPEC-04).
- **DHF Session:** One row in `dhf.sessions`. Opens on first AddinReadyMessage for a given AddinSessionId. Closes on:
  - `explicit_shutdown` — the add-in sent an `AddinTeardownMessage` before process exit (SDR-TL-014). This reason is distinguishable from transport loss.
  - `superseded_session` — a new AddinSessionId arrived, retiring the prior session.
  - `heartbeat_timeout` — heartbeat absence exceeded threshold. Root cause (add-in crash, SolidWorks exit, network failure, intentional shutdown without sending teardown) is NOT distinguishable from the orchestrator side and is recorded as "unknown" in the closure metadata.

  **DHF sessions are NOT closed on orchestrator graceful shutdown** — the add-in may still be running and will resume the session on reconnect per SPEC-04 SDR-04-005. **DHF sessions are NOT closed on transient transport loss** (idle timeout, network drop, WebSocket error); they remain open pending reconnect or heartbeat_timeout threshold. Reconnect and dangling-session reconciliation rules are defined in SPEC-04.

- **Recovery Event:** A DHF entry with `turn_type = 'recovery'` recording the outcome of a post-timeout or post-crash reconciliation. Structured fields captured in `recovery_context` JSONB column per SDR-TL-013: `trigger` (timeout, crash, disconnect), `strategy` applied (state refresh or undo-group rollback), `success` boolean, `failureReason` (optional string), `modelStateDivergenceDescription` (optional structured description of diverged geometry or features). Appended via `DhfLedger.AppendRecoveryEventAsync`. Participates in the hash chain in normal `sequence_number` order.
- **Quarantine:** A late message received after a terminal lifecycle state is logged and discarded; it never updates session state, never contributes to DHF turn entries, never triggers retries. **Exception:** If a quarantined message provides evidence of post-timeout model mutation (e.g., a late `Completed` result for a `TimedOut` call), the quarantine log entry is referenced by the subsequent mandatory recovery event to preserve audit trail continuity.
- **Vision Context:** Structured observations extracted by Opus 4.7 from a recent SolidWorks viewport screenshot, passed into the agent loop's prompt as enrichment. Not a decision-making component; purely enriches the agent's understanding of what the user can currently see. Defined in SPEC-05.
- **Ambiguity Event:** A clarification turn triggered because the user's utterance (voice transcript) and the current visual/geometric state imply multiple possible targets, OR because the vocabulary layer (SPEC-07) returned an `AmbiguityEscalation`. Recorded in the DHF as `turn_type = 'ambiguity_resolution'` with both the transcript and the ambiguity context preserved.
- **BSMS:** BoundState's mandated retrieval and knowledge-access pattern. **B**ouncer (domain-persona input gate), **S**onnet reasoning, **M**aterial/domain ground-truth database query, **S**onnet synthesis with row-level citations. Validated in MIRA at NASA MSFC. See SDR-TL-009. SPEC-07 is the canonical BSMS application within BSVoice-CAD.
- **Tenant:** In v1, identified implicitly by the API key used to open the WebSocket connection. The `api_keys` table (SPEC-01 §12) maps key → tenant identifier. Every DHF session row carries a `tenant_id` (SPEC-04 §3 migration 006). In v2, tenant is identified by the OAuth authentication context. See `DEPLOYMENT_V1_V2_MIGRATION.md` §5.
- **Session Lock:** A `dhf.sessions` row with `locked_at IS NOT NULL`. Set on DHF append failure after all retries (SPEC-04 §11 DHF-014, Seam C). Durable across orchestrator restart per SPEC-04 v1.5.2 §6.4. `OpenOrResumeSessionAsync` rejects locked sessions (`SessionLockedException` on same workstation; `CrossWorkstationSessionLockedException` on different workstation per v1.5.2 H3). **Recovery from a locked session is close-and-new only in v1 — no programmatic unlock exists.** See SPEC-04 §6.4.1 for the authoritative operator procedure (close the locked session via SQL, add-in reconnects with a new `AddinSessionId`).

## 7. Build-Readiness

This document is short on purpose. Its correctness is a function of whether the module boundaries match the actual architecture, not whether it covers every detail. Implementation does not start here; it starts in the per-module specs.

Implementation blockers, in order:
1. SPEC-01 converges (wire protocol locked, TLS + auth handshake specified, `AddinTeardownMessage` defined, `CallId` format locked)
2. SPEC-02 + SPEC-04 converge in parallel (SPEC-04 consumes AddinSessionId epoch semantics from SPEC-00 §6, not from SPEC-02 internals; SPEC-04 adds durable session lock, screenshot BYTEA, recovery schema)
3. SPEC-03 converges (depends on all three above)
4. SPEC-07 converges (depends on SPEC-04 for decoration table)
5. SPEC-05 converges (depends on SPEC-01, SPEC-03, SPEC-07)
6. SPEC-06 can start anytime after SPEC-04 converges
7. Integration Oracle pass on SPEC-00
8. **Day-0 validation:** deploy orchestrator to Railway, establish `wss://vcad.boundstate.ai/ws` connection from the add-in running locally, complete a tool round-trip end-to-end. This must succeed before any feature work begins — it de-risks every subsequent spec's assumptions about the network path.
9. SolidWorks 2025 SP3+ validation on the Windows workstation; Postgres 16 on Railway; Anthropic.SDK from the deployed orchestrator; Whisper.net CUDA validated only if voice remains in scope.

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*
