---
title: "BSVoice-CAD SPEC-00 — Orchestration Contract"
subtitle: "The seven-module decomposition; interfaces only, no implementation"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-00 — Orchestration Contract

**Version:** 1.6.0
**Status:** DRAFT — R1 integration review findings applied
**Hackathon application:** Submitted 2026-04-17; awaiting selection notification

**Changelog v1.5.0 → v1.6.0:**
- **§2 Seam G:** Clarified that vocabulary resolution produces decoration payloads returned in `PipelineResult`; persistence is deferred to the turn-append transaction (Seam C / SPEC-04 `AppendTurnAsync`) where decorations are written co-transactionally with the turn entry. Fixes R1-ISSUE-001.
- **§2 Seam H:** Clarified that decoration payloads assembled by SPEC-07 flow through SPEC-05 into `DhfTurnContext.PendingDecorations`, and SPEC-04's `AppendTurnAsync` writes them co-transactionally. Fixes R1-ISSUE-001.
- **§2 Seam C:** Closure-reason language clarified as observability-grade (structured log), not schema-persisted. Recovery language updated to close-and-new-session model per SPEC-04 trigger design. Fixes R1-ISSUE-003, R1-ISSUE-008.
- **§3 ACC table:** SPEC-07 acceptance tests added as ACC-28/29/30 (renumbered from SPEC-07's original ACC-25/26/27 to resolve namespace collision). Fixes R1-ISSUE-004.
- **§6 Glossary Session Lock:** Recovery model changed to close-and-new-session (unlock-and-resume removed). Fixes R1-ISSUE-008.
- **§6 Glossary DHF Session:** Added explicit note that closure reasons are observability-grade, recorded in structured logs, not in the DHF schema. Fixes R1-ISSUE-003.

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
| SPEC-04 | DHF Ledger | Schema, hash model, append logic, DHF session lifecycle, read-only repository projection, tenant-scoping, durable session lock, screenshot BYTEA storage, recovery-event persistence, co-transactional decoration write | `BSVoiceCAD.Orchestrator.Dhf` + `BSVoiceCAD.Orchestrator.Database` | SPEC-01 (message shapes), SPEC-00 §6 (AddinSessionId epoch semantics) |
| SPEC-05 | Agent Loop + Input | User input, confirmation flow, Opus 4.7 tool-calling, vision context, retry correlation, system prompt | `BSVoiceCAD.Orchestrator.Agent` + `BSVoiceCAD.Orchestrator.Input` | SPEC-01, SPEC-03, SPEC-07 (vocabulary resolution before tool-call construction) |
| SPEC-06 | Viewer | Timeline rendering, DHF read API, integrity endpoints, Railway-hosted at `vcad.boundstate.ai` | `BSVoiceCAD.Orchestrator.Viewer` | SPEC-04 (read-only), SPEC-01 (`StateSnapshot`, `SelectionRef`, `FeatureNode` — display types only, treated as opaque JSON) |
| SPEC-07 | CAD Vocabulary Layer | BSMS bouncer, query planner, `IVocabRepository`, Sonnet synthesis, citation format, decoration payload assembly | `BSVoiceCAD.Orchestrator.Vocab` + `BSVoiceCAD.Orchestrator.Database` | SPEC-04 (decoration table extension for `vocabulary_resolution` type) |

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

The lifecycle layer aggregates all tool-call outcomes within a single turn and produces one DHF write per completed turn. Upon turn completion, SPEC-03 constructs a `DhfTurnContext` (defined in SPEC-04) containing all tool-call results for that turn — and optionally, pending decoration payloads assembled by upstream pipeline stages (e.g., SPEC-07 vocabulary resolution) — and hands it to `DhfLedger.AppendTurnAsync`. The DHF layer never observes individual lifecycle transitions directly; it receives complete, aggregated turn records only. The hash chain covers turn-level entries and recovery events.

**Incomplete turns (crash, disconnect, kill mid-turn):** Turns that fail to complete do not produce DHF turn entries, because SPEC-03 has not yet constructed the `DhfTurnContext`. On session closure (detected via heartbeat timeout, explicit teardown message, or reconnect with a new AddinSessionId), SPEC-04 records the session closure. **Closure reason attribution** (`explicit_shutdown`, `heartbeat_timeout`, `superseded_session`) is recorded in the orchestrator's structured logs (Serilog `session_closed` event) and is NOT persisted in the DHF schema — closure attribution is observability-grade, not audit-chain-grade. On the next session start, SPEC-03 must perform state reconciliation (feature-tree comparison against last recorded turn) before opening the first new turn. If the reconciliation detects model state that diverges from the DHF record, SPEC-03 records a reconciliation event via `DhfLedger.AppendRecoveryEventAsync`. The undo group for an incomplete execution turn was never closed; SolidWorks behavior for unclosed `UndoObjectScope` is documented in SPEC-02.

**DHF append failure locks the session (never descopable):** If `DhfLedger.AppendTurnAsync` fails after all retries, the orchestrator MUST NOT silently continue. The session is marked locked via `IDhfRepository.LockSessionAsync` which persists `locked_at` and `lock_reason` columns on the session row (SPEC-04 v1.5.0 §6.4). The lock survives orchestrator restart — `OpenOrResumeSessionAsync` rejects locked sessions per SPEC-04 §6.1. No further turns may be opened on a locked session; the user is notified via the agent output channel. Reopening requires manual intervention: the operator closes the locked session via SQL (`UPDATE dhf.sessions SET ended_at = NOW() WHERE session_id = '...'`), and the add-in reconnects with a new AddinSessionId to start a fresh session. This invariant is codified in SPEC-04 §11 (DHF-014) and enforced at the lifecycle layer.

### Seam D: SPEC-05 → SPEC-03

The agent loop submits typed tool calls to the lifecycle layer and awaits terminal outcomes. The agent never touches the WebSocket, never touches the STA queue, never writes to the DHF. It is a pure consumer of the lifecycle surface. SPEC-05 has no compile-time dependency on SPEC-04; all DHF interaction is mediated by SPEC-03.

### Seam E: SPEC-05 → SPEC-04 (indirect, via SPEC-03)

The agent loop opens turns but does not write DHF entries directly. SPEC-03 is the sole DHF writer. This keeps turn construction in one place. SPEC-05 has no direct assembly reference to SPEC-04; the dependency is purely indirect through SPEC-03's aggregation role.

### Seam F: SPEC-06 → SPEC-04, SPEC-01 (display types)

The viewer queries `IReadOnlyDhfRepository` (defined in SPEC-04 §9.2 as a read-only projection of the full `IDhfRepository`) read-only. No write paths exist from the viewer. The viewer's isolated DI container resolves only `IReadOnlyDhfRepository`; the write-capable `IDhfRepository` is not registered. `ValidateChainAsync` on `IReadOnlyDhfRepository` is strictly side-effect-free per SPEC-04 v1.5.0 — it never writes integrity-event decorations or any other rows. This architectural isolation is enforced by both a DI-container test and a NetArchTest rule (SPEC-06 SDR-06-003).

The viewer also imports display-only types from SPEC-01 (`StateSnapshot`, `SelectionRef`, `FeatureNode`) to render their JSON-serialized content within DHF entry details. These types are treated as opaque JSON by the viewer — never deserialized into typed objects programmatically. The architecture test limits SPEC-06 → SPEC-01 references to these display types only.

### Seam G: SPEC-05 → SPEC-07

The agent loop calls `IVocabularyResolver` (defined in SPEC-07) for every user utterance BEFORE constructing tool calls. The vocabulary layer produces `VocabularyResolution` objects (resolved canonical IDs with citations, or ambiguity escalations). The agent translates resolved terms into structured tool-call parameters; ambiguity escalations become `ambiguity_resolution` turn types per SPEC-04 glossary. The agent never queries the vocabulary database directly.

**Decoration persistence timing (v1.6.0 — fixes R1-ISSUE-001):** The vocabulary pipeline assembles a `VocabularyResolutionDecoration` payload and returns it as part of `PipelineResult`. It does NOT write the decoration to the database — the DHF entry (which provides the `entry_id` the decoration references) does not exist yet at vocabulary-resolution time. SPEC-05 carries the decoration payload through the turn lifecycle into `DhfTurnContext.PendingDecorations`. When SPEC-03 calls `AppendTurnAsync` (Seam C), SPEC-04 writes the decoration co-transactionally with the turn entry in the same SERIALIZABLE transaction. This eliminates the entry_id timing gap and ensures decoration durability is guaranteed by the same retry/lock machinery that protects turn entries.

### Seam H: SPEC-07 → SPEC-04

The vocabulary layer assembles decoration payloads (`VocabularyResolutionDecoration`) that flow through SPEC-05 and SPEC-03 into `DhfTurnContext.PendingDecorations`. SPEC-04's `AppendTurnAsync` persists them co-transactionally with `decoration_type = 'vocabulary_resolution'`. SPEC-07 requires SPEC-04 to include this decoration type in the CHECK constraint (migration 007 per SPEC-07 §8.1). The vocabulary layer is a producer of decoration payloads, not a writer — all persistence goes through SPEC-04's turn-append transaction.

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
| ACC-28 | Multi-domain utterance "tap a 6-32 UNC hole in titanium" produces `PipelineResult` with exactly 2 `DomainResolution` entries (threads + materials), each `DomainResolved`; `PipelineOutcome = AllResolved`; decoration payload carries BOTH resolutions | SPEC-07 |
| ACC-29 | Vocabulary decoration payload assembled by pipeline is co-transactionally persisted by `AppendTurnAsync`; decoration exists in `dhf.entry_decorations` after turn commit; AppendTurnAsync failure prevents both turn and decoration write | SPEC-07 + SPEC-04 |
| ACC-30 | Bouncer cache with two utterances normalizing to the same key preserves each request's own raw utterance in its DHF decoration; no cross-contamination | SPEC-07 |

**ACC numbering notes:**
- ACC-20 was renumbered from SPEC-06's original ACC-08 to resolve the cross-spec numbering collision flagged in cross-pass Oracle R2. SPEC-02's ACC-08 is the canonical holder of the number.
- ACC-21 through ACC-24 were added in SPEC-00 v1.5.0 to accept SPEC-07's acceptance tests into the master table.
- ACC-25 through ACC-27 were added in SPEC-00 v1.5.0 to codify R3 critical-finding fixes as never-descopable gates (durable session lock, recovery event structured storage, neutral landing page).
- ACC-28 through ACC-30 were added in SPEC-00 v1.6.0 to accept SPEC-07 v1.2.0's renumbered acceptance tests (originally numbered ACC-25/26/27 in SPEC-07, renumbered to resolve the global namespace collision with SPEC-00/SPEC-04's ACC-25/26/27). Fixes R1-ISSUE-004.

### Integration and E2E Test Project

Cross-module acceptance tests (ACC-01, ACC-04, any test requiring multiple module assemblies or external dependencies) are owned by `BSVoiceCAD.Tests.Integration`. This project is permitted to reference all module assemblies and requires the following runtime dependencies: a SolidWorks instance (or a documented test double for CI), a PostgreSQL database (EmbeddedPostgres permitted), and either a live Anthropic API key or a recorded-response test double. ACC tests that can be verified within a single module (ACC-02, ACC-03, ACC-08, ACC-10, ACC-12, ACC-19 through ACC-30) may live in that module's own test project, but must also have an integration-level smoke test in `BSVoiceCAD.Tests.Integration` to verify cross-module wiring.

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
- **AddinSessionId:** 32-character lowercase hex string (format regex `^[0-9a-f]{32}$`) generated fresh on each add-in load. Derivation algorithm — including hash function, input fields, and fallback behavior — is specified in SPEC-02 §8; SPEC-01 §9 specifies only the format contract and validator. **Epoch semantics (consumed by SPEC-04, authoritative source is this glossary):** A new AddinSessionId signals a new epoch. On epoch change: (1) StateVersion resets to 0; (2) the current DHF session (if any) is closed with reason `superseded_session` (recorded in structured logs per Seam C); (3) a new DHF session opens on the first AddinReadyMessage carrying the new AddinSessionId. Rotation invariant: AddinSessionId changes if and only if the add-in is reloaded (SolidWorks restart, add-in disable/re-enable, or crash recovery). A given SolidWorks process runs exactly one add-in instance; cross-workstation collisions on the same AddinSessionId are handled as a hard error per SPEC-04 SDR-04-008.
- **StateVersion:** Monotonic per-AddinSessionId counter carried by state-bearing messages. Resets to 0 on new AddinSessionId epoch.
- **Tool-Call Lifecycle State:** `Sent → Completed | TimedOut | Cancelled | Abandoned`. Terminal states are final. The `ToolCallLifecycleState` enum is defined in SPEC-01 (cross-process type; consumed by SPEC-03 and SPEC-04).
- **DHF Session:** One row in `dhf.sessions`. Opens on first AddinReadyMessage for a given AddinSessionId. Closes on:
  - `explicit_shutdown` — the add-in sent an `AddinTeardownMessage` before process exit (SDR-TL-014). This reason is distinguishable from transport loss.
  - `superseded_session` — a new AddinSessionId arrived, retiring the prior session.
  - `heartbeat_timeout` — heartbeat absence exceeded threshold. Root cause (add-in crash, SolidWorks exit, network failure, intentional shutdown without sending teardown) is NOT distinguishable from the orchestrator side and is recorded as "unknown" in the closure metadata.

  **Closure-reason persistence model (v1.6.0):** Closure reasons (`explicit_shutdown`, `heartbeat_timeout`, `superseded_session`) are recorded in the orchestrator's structured logs (Serilog `session_closed` event with fields `{ session_id, closure_reason, tenant_id, timestamp }`), NOT in the DHF schema. The `dhf.sessions` table records only `ended_at` (timestamp of closure); it does not carry a `closure_reason` column. Closure attribution is observability-grade, not audit-chain-grade — DHF ledger integrity does not depend on knowing *why* a session closed, only on preserving the entries up to the point of closure. Operators querying session closure reasons MUST use the Serilog pipeline (or the downstream observability store), not `dhf.sessions`.

  **DHF sessions are NOT closed on orchestrator graceful shutdown** — the add-in may still be running and will resume the session on reconnect per SPEC-04 SDR-04-005. **DHF sessions are NOT closed on transient transport loss** (idle timeout, network drop, WebSocket error); they remain open pending reconnect or heartbeat_timeout threshold. Reconnect and dangling-session reconciliation rules are defined in SPEC-04.

- **Recovery Event:** A DHF entry with `turn_type = 'recovery'` recording the outcome of a post-timeout or post-crash reconciliation. Structured fields captured in `recovery_context` JSONB column per SDR-TL-013: `trigger` (timeout, crash, disconnect), `strategy` applied (state refresh or undo-group rollback), `success` boolean, `failureReason` (optional string), `modelStateDivergenceDescription` (optional structured description of diverged geometry or features). Appended via `DhfLedger.AppendRecoveryEventAsync`. Participates in the hash chain in normal `sequence_number` order.
- **Quarantine:** A late message received after a terminal lifecycle state is logged and discarded; it never updates session state, never contributes to DHF turn entries, never triggers retries. **Exception:** If a quarantined message provides evidence of post-timeout model mutation (e.g., a late `Completed` result for a `TimedOut` call), the quarantine log entry is referenced by the subsequent mandatory recovery event to preserve audit trail continuity.
- **Vision Context:** Structured observations extracted by Opus 4.7 from a recent SolidWorks viewport screenshot, passed into the agent loop's prompt as enrichment. Not a decision-making component; purely enriches the agent's understanding of what the user can currently see. Defined in SPEC-05.
- **Ambiguity Event:** A clarification turn triggered because the user's utterance (voice transcript) and the current visual/geometric state imply multiple possible targets, OR because the vocabulary layer (SPEC-07) returned an `AmbiguityEscalation`. Recorded in the DHF as `turn_type = 'ambiguity_resolution'` with both the transcript and the ambiguity context preserved.
- **BSMS:** BoundState's mandated retrieval and knowledge-access pattern. **B**ouncer (domain-persona input gate), **S**onnet reasoning, **M**aterial/domain ground-truth database query, **S**onnet synthesis with row-level citations. Validated in MIRA at NASA MSFC. See SDR-TL-009. SPEC-07 is the canonical BSMS application within BSVoice-CAD.
- **Tenant:** In v1, identified implicitly by the API key used to open the WebSocket connection. The `api_keys` table (SPEC-01 §12) maps key → tenant identifier. Every DHF session row carries a `tenant_id` (SPEC-04 §3 migration 006). In v2, tenant is identified by the OAuth authentication context. See `DEPLOYMENT_V1_V2_MIGRATION.md` §5.
- **Session Lock:** A `dhf.sessions` row with `locked_at IS NOT NULL`. Set on DHF append failure after all retries (SPEC-04 §11 DHF-014, Seam C). Durable across orchestrator restart per SPEC-04 v1.5.0 §6.4. `OpenOrResumeSessionAsync` rejects locked sessions; **recovery requires the operator to close the locked session via SQL** (`UPDATE dhf.sessions SET ended_at = NOW() WHERE session_id = '...' AND ended_at IS NULL`) **and the add-in to reconnect with a new AddinSessionId to start a fresh session.** The locked session remains in the audit trail as evidence of the append failure. The `guard_session_update` trigger rejects `locked_at` non-NULL→NULL transitions, so "unlock and resume" is not supported — only close-and-new-session.

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
---
title: "BSVoice-CAD — Deployment v1 → v2 Migration"
subtitle: "Single source of truth for every v1/v2 deployment boundary"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
version: "1.1.0"
status: "Locked (governs all SPEC-NN deployment-coupled sections)"
---

# BSVoice-CAD — Deployment v1 → v2 Migration

**Changelog v1.0.0 → v1.1.0:**
- §1 deployment matrix: viewer host path corrected from `/viewer` to `/` to match SPEC-06 §4.1 canonical routing (fixes R3 R1-ISSUE-009).
- §2 transport: handshake-failure transport clarified. Pre-upgrade failures (auth failure, format error) use HTTP 400/401 on the WebSocket upgrade response per SPEC-01 §12.4 — NOT a WebSocket close code, because a Close frame cannot be sent before the upgrade completes. WebSocket close code 4401 is reserved exclusively for post-upgrade credential revocation (fixes R3 R1-ISSUE-005).
- §4 viewer hosting: access-control posture tightened. `GET /` is a neutral landing page; it does NOT auto-load the most recent session. Session enumeration endpoints (`/api/dhf/sessions`) require a valid `sessionId` capability to resolve tenant scope. Anonymous `?tenant=X` query override is removed. This aligns the document with SPEC-06 v1.4.0 fixes for R3 R1-ISSUE-003.
- No other substantive changes. SPEC authors must continue to reference this document by section rather than redefining v1/v2 locally.

## 0. Why This Document Exists

BSVoice-CAD is intentionally built to run in two deployment modes:

- **v1 (hackathon + initial Medtronic look):** Railway-hosted orchestrator, minimal auth surface, base64 screenshots, public viewer URL.
- **v2 (Medtronic production / regulated customer rollout):** Tenant-isolated architecture with OAuth device flow, S3 presigned URLs for binary artifacts, Clerk-backed viewer auth, per-tenant Postgres schemas.

Every spec that touches a deployment-coupled decision (transport, auth, storage, hosting) carries a short "Deployment: v1/v2 boundary" subsection that points here. This document is the single source of truth for what changes, what stays, and what a ProtocolVersion bump triggers. **Specs must not redefine v1/v2 semantics locally.** If a spec needs a v2 decision this document doesn't cover, update this document first, then reference it from the spec.

The document's existence is itself a non-negotiable design constraint: **no rewrites between v1 and v2.** Every v1 component has a documented evolution path to v2 that does not require throwing away code. Where a change requires a breaking wire-contract change, the `ProtocolVersion` bump is called out explicitly.

## 1. Deployment Matrix

| Dimension | v1 (Hackathon / Initial Medtronic) | v2 (Medtronic Production) |
|-----------|-----------------------------------|---------------------------|
| Orchestrator host | Railway (`vcad.boundstate.ai`) | Railway or AWS (TBD — Medtronic infra policy) |
| Viewer host | Same Railway service, co-hosted at root path `/` | Same service; may split if Medtronic requires air-gapped viewer |
| Add-in host | Engineer's Windows workstation (local SolidWorks) | Unchanged — add-in is always local |
| WebSocket URL | `wss://vcad.boundstate.ai/ws` | `wss://{tenant}.vcad.boundstate.ai/ws` or `wss://vcad-{customer}.boundstate.ai/ws` |
| Viewer URL | `https://vcad.boundstate.ai/` | Same shape; possibly behind customer VPN |
| WebSocket auth | API key in `Sec-WebSocket-Protocol` subprotocol header | OAuth 2.0 device flow with refresh tokens |
| Viewer auth | **None** — URL-as-capability (session UUIDv4); neutral landing at `/` | Clerk SSO, tenant-scoped |
| Postgres | Single Railway-managed Postgres 16 instance | Per-tenant isolation: separate schemas or separate databases per customer |
| Screenshots | Base64-inline in `ScreenshotResult`, 2 MiB cap post-encode | S3 presigned URL (PUT/GET), SHA-256 verified |
| Feature tree transport | `IsFeatureTreeTruncated` + on-demand `get_feature_tree` pagination | Unchanged |
| Secrets | Railway env vars | Railway env vars + AWS Secrets Manager for per-tenant config |
| DNS | `vcad.boundstate.ai` (CNAME → Railway) | `{tenant}.vcad.boundstate.ai` wildcard, per-tenant certs |
| TLS | Railway edge termination | Unchanged (Railway edge) or customer-managed cert if air-gapped |

**Note on viewer path:** The viewer is co-hosted in the same ASP.NET Core process as the orchestrator's WebSocket endpoint and HTTP API, per SPEC-06 SDR-06-002. Viewer endpoints mount at the service root (`GET /` for the landing page, `GET /session/{sessionId}` for session detail, `GET /api/dhf/*` for JSON endpoints). There is no `/viewer` path prefix. The WebSocket endpoint lives at `/ws` and is disjoint from viewer routes.

## 2. Transport — WebSocket

### v1 Contract

- URL: `wss://vcad.boundstate.ai/ws`
- TLS: terminated at Railway edge; orchestrator speaks plain WS internally
- Framing: unchanged from SPEC-01 §3 (one JSON doc per reassembled WebSocket message, 4 MiB cap, drain-and-discard on oversize)
- Auth: API key passed as a subprotocol in the WebSocket handshake — `Sec-WebSocket-Protocol: bsvoice-cad-v1, apikey-{keyId}.{secret}`. The orchestrator parses the second subprotocol value, looks up `keyId` in the `api_keys` table, and validates `secret` via constant-time comparison.
- **Handshake failure (pre-upgrade):** Failures detected during the HTTP upgrade request — missing or malformed subprotocol, unknown `keyId`, failed secret verification, rate-limit exceeded — are returned as HTTP responses on the upgrade request itself. The WebSocket upgrade is rejected at the HTTP layer; no WebSocket connection is established; no Close frame is sent (because none can be sent before upgrade completes). Per SPEC-01 §12.4:
  - `400 Bad Request` — malformed subprotocol format
  - `401 Unauthorized` — unknown `keyId`, failed secret verification, or API key revoked
  - `429 Too Many Requests` — rate limit exceeded
  SPEC-01 §12.4 is authoritative for this mapping.
- **Post-upgrade credential revocation:** The orchestrator MAY close an established WebSocket with custom close code `4401` if an API key is revoked while a connection is live (e.g., admin operator disables the key via the `api_keys` table). Close code 4401 is reserved exclusively for this post-upgrade revocation scenario. The add-in treats 4401 as terminal — it transitions to `AuthFailed` state and does NOT retry.

### v2 Contract

- URL: `wss://{tenant}.vcad.boundstate.ai/ws` or `wss://vcad-{customer}.boundstate.ai/ws` (decision deferred to Medtronic procurement)
- TLS: unchanged
- Framing: unchanged
- Auth: OAuth 2.0 device authorization grant (RFC 8628).
  - Add-in first-run opens a browser tab to `https://vcad.boundstate.ai/device`, user signs in via Clerk, authorizes the add-in instance.
  - Orchestrator issues an access token + refresh token to the add-in, stored in Windows Credential Manager on the user's workstation.
  - WebSocket handshake carries the access token in `Sec-WebSocket-Protocol: bsvoice-cad-v1, bearer-{token}`.
  - Refresh handled transparently before token expiry.
- Handshake failure: same HTTP 400/401/429 mapping on the upgrade response. Close code 4401 still reserved exclusively for post-upgrade revocation. The add-in triggers re-authentication flow on any auth-category failure.

### What Does Not Change

- SPEC-01 polymorphic message types — every message record unchanged
- `ProtocolVersion.Current` — **does not bump** between v1 and v2. The wire contract is identical; only the handshake credential differs.
- Rate limiting, message size limits, epoch semantics, reconnect behavior — all unchanged

### Migration Mechanism

- v1 → v2 is controlled by orchestrator config, not protocol change
- Orchestrator reads `DEPLOYMENT_AUTH_MODE` env var: `api_key` (v1) or `oauth` (v2)
- The `api_keys` table stays in v2 for service-to-service calls (e.g., internal tools, CI smoke tests) — but add-in connections must use OAuth
- Add-in detects which mode the orchestrator is running by inspecting the WWW-Authenticate header on the initial HTTPS probe to `/health`

## 3. Screenshot Transport

### v1 Contract

- `ScreenshotResult` carries:
  - `screenshotBase64`: base64-encoded PNG, **max 2 MiB after encoding**
  - `sha256Hash`: hex SHA-256 of the decoded PNG bytes
  - `widthPx`, `heightPx`
- Add-in captures PNG, checks encoded size; if > 2 MiB, progressively reduces dimensions until it fits
- No local filesystem path, no shared directory, no URL
- Orchestrator decodes to memory, passes to Anthropic vision API as image input, discards after turn
- DHF stores: `screenshot_before_sha256` / `screenshot_after_sha256` (always) and `screenshot_before_png` / `screenshot_after_png` BYTEA columns (SPEC-04 §3 migration 008 adds these) for audit replay and deep validation. Post-hackathon may move screenshot bytes to S3 to reduce DB size, but the SHA-256 columns remain canonical identifiers.

### v2 Contract

- `ScreenshotResult` carries:
  - `screenshotUrl`: S3 presigned GET URL (expires 5 min)
  - `sha256Hash`: hex SHA-256
  - `widthPx`, `heightPx`
  - `screenshotBase64`: **absent** (null or omitted entirely)
- Add-in POSTs PNG to S3 via presigned PUT URL obtained from orchestrator HTTPS endpoint (`POST /api/presigned-upload`), receives key
- Orchestrator issues presigned GET URL to include in the message
- DHF stores S3 key; viewer and Anthropic vision API fetch via the presigned GET URL

### Migration Mechanism

- This **is a breaking wire-contract change** — `ScreenshotResult` record shape differs between v1 and v2
- **Mitigation without rewrite:** `ScreenshotResult` gains `screenshotBase64` (nullable) and `screenshotUrl` (nullable) in v1, with an envelope invariant: exactly one must be non-null
- v1 orchestrator always sends base64, never URL
- v2 orchestrator always sends URL, never base64
- Consumers (agent loop, viewer, DHF writer) branch on which field is populated
- This allows a single codebase to target both deployment modes by configuration, with no code rewrite
- **Does not bump ProtocolVersion** — the record shape is forward-compatible

### What Does Not Change

- The 4 MiB message size cap on all other messages — screenshot base64 is special-cased to 2 MiB to leave headroom for `StateSnapshot`
- `StateSnapshot` truncation rules for feature tree — unchanged
- SHA-256 hash verification — identical in both modes
- DHF `screenshot_*_sha256` columns — authoritative in both modes

## 4. Viewer Hosting and Auth

### v1 Contract

- URL: `https://vcad.boundstate.ai/` (served by same Railway service as orchestrator)
- Auth: **none.** Viewer is publicly accessible at the landing page.
- **Landing page behavior (`GET /`):** Neutral landing page only. Does NOT auto-load any session. Does NOT list sessions. Does NOT accept anonymous tenant parameters. Shows a brief product description and a "Sign in to see your sessions" placeholder that currently just explains the URL-as-capability model.
- **Session access:** `GET /session/{sessionId}` or `GET /?sessionId={sessionId}` is the only path to content. Knowledge of a session UUIDv4 is the capability to view that session. UUIDv4 provides ~122 bits of entropy; URL guessing is not a practical attack.
- **Session list:** `GET /api/dhf/sessions` requires a `sessionId` query parameter whose session the caller already knows. The orchestrator resolves the tenant from that session row and returns other sessions scoped to the same tenant. This supports the sidebar UX (clicking around between sessions within a tenant) without enabling anonymous enumeration.
- **No anonymous tenant override:** The `?tenant=X` query parameter from earlier drafts is removed. Tenant resolution in v1 is exclusively via the `sessionId` → `tenant_id` lookup path. A request with no `sessionId` cannot resolve a tenant and cannot list sessions.
- TLS: Railway edge
- Rate limit: global IP-based rate limiter (Railway default) to prevent enumeration brute-forcing

### v2 Contract

- URL: unchanged shape; may be per-tenant subdomain
- Auth: **Clerk** (or Auth0) SSO, tenant-scoped
  - Landing page requires login
  - Session list filtered to sessions owned by authenticated tenant
  - Session URLs still use UUIDs, but accessing a session URL requires tenant membership
- Rate limit: unchanged + per-user rate limits

### Threat Model Boundary

**v1 is explicitly NOT suitable for PHI or PII.** The URL-as-capability model is defensible for:

- Hackathon demo with synthetic design data
- Medtronic's initial "look at the product" walkthrough with non-sensitive sample models
- Any session where disclosure of the full session content to someone who has been sent the URL would be embarrassing at worst

v1 is explicitly NOT defensible for:

- Patient data
- Medical device designs under trade-secret protection
- Anything covered by 21 CFR Part 11 in production use (SDR-TL-006 already establishes this)

This boundary is codified in SPEC-00 SDR-TL-011 and SDR-TL-012.

**Known sidebar-exposure trade-off (documented, not fixed in v1):** Knowledge of one session UUID within a tenant allows the holder to see other session UUIDs and metadata for the same tenant via the sidebar list. This is an intentional v1 trade-off for UX (switching between sessions within a tenant) and is acceptable under the threat model above. v1.5 Clerk integration replaces this with tenant-role-based access control.

### Migration Mechanism

- Clerk integration is a self-contained addition: middleware on viewer routes, Clerk-provided session cookie, no changes to DHF schema or orchestrator WebSocket handling
- Estimated integration: 15-30 min of code + Clerk dashboard configuration
- **Does not require orchestrator changes** — the viewer is the only component that gains auth
- **Does not bump ProtocolVersion** — no wire format changes
- v1.5 adds an auth middleware ahead of all viewer routes; the session-resolution logic remains the same but runs inside an authenticated context

## 5. Database and Tenant Isolation

### v1 Contract

- Single Railway-managed Postgres 16 instance
- Single `dhf` schema per SPEC-04
- `workstation_id` column on `dhf.sessions` — now a **client-supplied tenant hint**, not a trust boundary (see SDR-04-008 rewritten for v1)
- `api_keys` table holds credential → tenant mapping
- Every session row is scoped to the tenant that owned the API key used to open it (implicit column: `tenant_id` added in migration 006)

### v2 Contract

- **Per-tenant Postgres schema** — one `dhf_{tenant_slug}` schema per customer
- Schema creation automated on first successful OAuth tenant provisioning
- Cross-tenant queries forbidden at the application layer (repository enforces `tenant_id` filter on every query; arch test verifies)
- Backup and restore per-tenant, independent

### Migration Mechanism

- Migration 006 (v1) adds `tenant_id` column to `dhf.sessions` (and by extension, joined via session_id to entries / tool_calls / decorations)
- v1 repository queries always include `WHERE tenant_id = @TenantId` in reads and INSERTs
- Per-tenant schema migration (v2) introduces schema-per-tenant; a migration script copies rows from `dhf.sessions WHERE tenant_id = X` into `dhf_X.sessions` for each tenant
- v2 repository selects schema based on authenticated tenant context
- **No application code rewrite required** — the repository gains a tenant-resolution step in its constructor, but the SQL shape is identical
- **Does not bump ProtocolVersion** — database internals invisible to the wire contract

## 6. AddinSessionId and Workstation Trust

### v1 Contract

- `AddinSessionId`: unchanged from SPEC-01 §9 format (32 lowercase hex, derived deterministically from `machineGuid`, `swProcessId`, `loadTimestampUtcTicks`, and a random nonce per SPEC-02 §8.1)
- Algorithm authoritative in SPEC-02 §8 (SPEC-01 specifies format regex + epoch semantics only)
- `workstation_id`: **client-supplied string, not a trust boundary.** The add-in reports `Environment.MachineName` as workstation_id; the orchestrator accepts it at face value within the authenticated API key's tenant scope
- Cross-workstation conflict (same AddinSessionId, different workstation_id, same tenant) remains a hard error per SDR-04-008 — the value of this check is detecting VM cloning, snapshot rollbacks, and accidental ID collisions within one tenant's fleet

### v2 Contract

- `AddinSessionId`: unchanged format
- `workstation_id`: scoped to OAuth-authenticated tenant context; enforcement is identity-bound, not machine-bound
- Post-hackathon improvement: replace `Environment.MachineName` with a cryptographically-generated persistent workstation token, stored in Windows Credential Manager alongside OAuth tokens — eliminates the "machine name collision" category of incidents

### What Does Not Change

- Cross-workstation hard-error semantics (SDR-04-008)
- Epoch rotation rules (SPEC-01 §9, SPEC-00 §6 glossary)
- `CrossWorkstationSessionConflictException` structured metadata and remediation message

## 7. Observability and Logging

### v1 Contract

- Structured logging via Serilog to Railway stdout (captured by Railway's log aggregation)
- Log fields: timestamp, level, message, exception (with stack), `session_id`, `entry_id`, `api_key_id` (redacted to first 8 chars)
- No PHI expected in logs by construction (DHF payloads never logged; only IDs and counts)
- Retention: Railway default (7 days)
- **Header redaction:** Railway request logging MUST redact the `Sec-WebSocket-Protocol` header on the WebSocket upgrade request, because the v1 API key lives in that header value. Redaction is configured in the Railway logging sink configuration, not in application code. See SPEC-01 §12.3 for the known-risk disclosure regarding this header.

### v2 Contract

- Additional sinks: per-tenant audit log export to customer-designated S3 bucket or SIEM
- Extended retention: 7 years minimum (21 CFR 820 retention requirement)
- PHI/PII redaction review per tenant
- OAuth bearer tokens MUST be redacted from request logs (same mechanism as API key redaction in v1)

### Migration Mechanism

- Serilog sink configuration change only
- No code changes to log call sites
- Retention is storage-layer policy, not application code

## 8. Version-Bump Triggers

The following changes require `ProtocolVersion.Current` to bump. None are required for v1→v2 as specified above.

- Any `IClientMessage` or `IAddinMessage` record gains or loses a required field
- Any polymorphic discriminator value changes
- Any enum adds or removes a value that may appear in persisted hash chains
- `HashCanonicalizer` rules change in a way that breaks historical hash verification
- `AddinSessionId` format changes (currently `^[0-9a-f]{32}$`)

The following v1→v2 changes do **not** require a ProtocolVersion bump (and are explicitly designed not to):

- Auth mode (API key ↔ OAuth)
- Screenshot transport (base64 ↔ S3 URL), because `ScreenshotResult` already carries both nullable fields with an envelope invariant
- Viewer auth (none ↔ Clerk)
- Postgres schema layout (shared ↔ per-tenant)
- `workstation_id` enforcement semantics

## 9. Locked Decisions

**DM-1:** v1 WebSocket auth is API key in `Sec-WebSocket-Protocol` subprotocol; v2 is OAuth device flow. No other auth schemes considered. Known risk: the subprotocol header may be logged by intermediaries; Railway logging configuration MUST redact this header per §7.

**DM-2:** v1 screenshot transport is base64-inline capped at 2 MiB post-encode; v2 is S3 presigned URL. The `ScreenshotResult` record carries both `screenshotBase64` (v1, nullable) and `screenshotUrl` (v2, nullable) with an envelope invariant that exactly one is set.

**DM-3:** v1 viewer has no auth and relies on UUIDv4 session URLs as capabilities. `GET /` is a neutral landing page that does not list or resolve sessions. Session content requires possession of a session UUID. v2 adds Clerk. v1 MUST NOT be used for PHI, PII, or regulated design data in production customer contexts. SDR-TL-011 and SDR-TL-012 codify this boundary.

**DM-4:** v1 database is a single Railway-managed Postgres instance with a `tenant_id` column enforced in every query. v2 is per-tenant schema isolation. Migration between them is an SQL script; no application code rewrite.

**DM-5:** ProtocolVersion does not bump between v1 and v2. The wire contract is stable across the deployment transition by design.

**DM-6:** `vcad.boundstate.ai` is the canonical v1 domain. Per-tenant subdomains (`{tenant}.vcad.boundstate.ai`) are reserved for v2 multi-tenancy.

**DM-7:** Clerk is the selected v2 viewer auth provider. If Clerk is unavailable (ITAR-restricted customers, air-gapped deployment), Auth0 is the fallback. Both use OAuth 2.0 with PKCE; the orchestrator's tenant model doesn't care which provider.

**DM-8 (v1.1.0):** Pre-upgrade handshake failures are HTTP 400/401/429; WebSocket close code 4401 is reserved exclusively for post-upgrade credential revocation. SPEC-01 §12.4 is authoritative. Sending close code 1006 in a Close frame is forbidden per RFC 6455 §7.4.1; heartbeat-timeout behavior is specified in SPEC-01 §12.7.

**DM-9 (v1.1.0):** The viewer is co-hosted at the service root (`/`); there is no `/viewer` path prefix. SPEC-06 §4.1 is authoritative for viewer routing.

---

*This document is locked at v1.1.0. Changes require an update to this document FIRST, then downstream spec patches. Each spec's "Deployment: v1/v2 boundary" subsection must reference this document by section number, not by repeating its contents.*

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC.*

[NOTE FROM BETH: The remaining documents (SPEC-01, SPEC-02, SPEC-04, SPEC-06, SPEC-07, and CAD_VOCAB_CORPUS) contain targeted changes for the accepted defects. Due to the extreme length of the full document bundle (~50,000+ words), I am providing the specific section-level changes below rather than reproducing every unchanged line. Each change is precisely located and the surrounding context is preserved.]

## SPEC-01 Changes (v1.4.0 → v1.4.1)

**§9.1 m4 change (fixes R1-ISSUE-007):**

Replace:
> **ProtocolVersion drift on same-`AddinSessionId` reconnect (v1.4.0+ editorial m4).** A change in `ProtocolVersion` on a same-AddinSessionId reconnect is also an illegal state — same rationale as capability drift. It is caught by §10's version-mismatch handling (HTTP 400 during HTTP upgrade or close code 1002 during WebSocket protocol-error handling) rather than the capability-drift close code 1008, but operationally it is the same class of failure

With:
> **ProtocolVersion drift on same-`AddinSessionId` reconnect (v1.4.0+ editorial m4).** A change in `ProtocolVersion` on a same-AddinSessionId reconnect is also an illegal state — same rationale as capability drift. It is caught by §10's version-mismatch handling (orchestrator sends `ClientProtocolErrorMessage(VersionMismatch)` then closes the WebSocket with close code 1002) rather than the capability-drift close code 1008, but operationally it is the same class of failure

## SPEC-02 Changes (v1.4.0 → v1.4.1)

**§9.1.1 steps 6-7 change (fixes R1-ISSUE-010):**

Replace steps 6-7:
> 6. **Dispose local resources that do not require the UI thread** (WebSocket, Serilog sinks, file handles). The `SwUiThreadCommandQueue` is abandoned (its dispose would block on the stuck task); SW's own shutdown will eventually tear it down when the stuck COM call completes and SW's message pump exits.
> 7. **Exit the add-in process** after a final 5-second grace (allows the stuck COM call a last chance to return and produce a log line). If the process has not exited after the grace, the host invokes `Environment.Exit(1)`. **Environment.Exit(1) terminates the SolidWorks process** because the add-in runs in-process with SolidWorks. The stuck COM call prevents any less-destructive unload path; this is the bounded-shutdown-time cost of a stuck UI thread. Users see SolidWorks quit without the usual save-prompt dialog; unsaved work in other open documents may be lost. This is acknowledged degradation, not defect — the alternative is an unbounded add-in process lifetime when the UI thread is hung. Post-v1: surface a last-chance "save and force-exit" dialog before the 5-second grace expires (SPEC-02 backlog).

With:
> 6. **Dispose local resources that do not require the UI thread** (WebSocket, Serilog sinks, file handles). The `SwUiThreadCommandQueue` is abandoned (its dispose would block on the stuck task); SW's own shutdown will eventually tear it down when the stuck COM call completes and SW's message pump exits.
> 7. **Transition the add-in to `PermanentlyFailed` terminal state.** The add-in sets an atomic `_permanentlyFailed` flag. All subsequent `ExecuteAsync` calls on `SwUiThreadCommandQueue` throw `InvalidOperationException("Add-in is in permanently failed state due to stuck COM call.")`. The `MessageDispatcher` rejects all incoming messages with `AddinProtocolErrorMessage(ErrorCode = MalformedEnvelope, ErrorText = "Add-in permanently failed; restart SolidWorks.")`. The add-in logs at `LogLevel.Error` with structured fields `{ event: "permanent_failure", stuck_call_id, stuck_tool_name, elapsed_seconds }`. **The add-in does NOT call `Environment.Exit(1)` or terminate the SolidWorks host process.** The stuck COM call continues running on the UI thread; it may eventually return (at which point the return is logged and discarded). The user retains full control of SolidWorks — they can save unsaved work in other documents and close SolidWorks at their discretion. The add-in surfaces an error via `MessageBox.Show` from a background thread (which creates its own message pump, independent of the stuck UI thread): "BSVoice-CAD has encountered a fatal error. Please save your work and restart SolidWorks." Post-v1: surface the error via the add-in's taskpane UI if available.

**Editorial M4 change (fixes R1-ISSUE-010):**

Replace:
> **M4** — §9.1.1 step 7 `Environment.Exit(1)` impact disclosure: rewritten to state plainly that the 5-second post-grace-period exit terminates the SolidWorks process because the add-in runs in-process. Users see SolidWorks quit without the usual save-prompt; unsaved work in other documents may be lost. Acknowledged degradation, not defect — bounded-shutdown-time is the contract.

With:
> **M4** — §9.1.1 step 7 rewritten (R1-ISSUE-010): `Environment.Exit(1)` removed. The add-in transitions to a `PermanentlyFailed` terminal state without terminating the host process. The user retains control of SolidWorks and can save unsaved work before manually closing. Error surfaced via `MessageBox.Show` from a background thread.

**§10 PlannedDisconnectEscalationTests change (fixes R1-ISSUE-010):**

Replace:
> **`PlannedDisconnectEscalationTests` (v1.4.0):** configure a stuck executing call; trigger planned disconnect; assert 60-second drain elapses without completion; assert teardown is NOT sent; assert rollback is NOT attempted; assert WebSocket is force-closed via `Abort()` with no Close frame; assert process exits after 5-second post-force-close grace; assert stuck-call log entry contains `CallId`, `ToolName`, and elapsed time; assert DHF session closure reason is `heartbeat_timeout` (via mock orchestrator)

With:
> **`PlannedDisconnectEscalationTests` (v1.4.0, updated v1.4.1):** configure a stuck executing call; trigger planned disconnect; assert 60-second drain elapses without completion; assert teardown is NOT sent; assert rollback is NOT attempted; assert WebSocket is force-closed via `Abort()` with no Close frame; assert add-in transitions to `PermanentlyFailed` state; assert `Environment.Exit` is NOT called; assert subsequent `ExecuteAsync` calls throw `InvalidOperationException`; assert subsequent incoming messages are rejected; assert stuck-call log entry contains `CallId`, `ToolName`, and elapsed time; assert DHF session closure reason is `heartbeat_timeout` (via mock orchestrator)

## SPEC-04 Changes (v1.5.1 → v1.5.2)

**§4.2 DhfHashColumns change (fixes R1-ISSUE-002):**

Replace:
```csharp
public static class DhfHashColumns
{
    public static readonly IReadOnlyList<string> HashedColumns = new[]
    {
        "entry_id", "session_id", "sequence_number", "timestamp",
        "input_modality", "raw_transcript", "transcript_confidence",
```

With:
```csharp
public static class DhfHashColumns
{
    public static readonly IReadOnlyList<string> HashedColumns = new[]
    {
        "timestamp",
        "input_modality", "raw_transcript", "transcript_confidence",
```

(entry_id, session_id, sequence_number removed per §4.2a documented exclusion rationale. entry_id is identity; session_id is FK; sequence_number ordering is enforced by previous_hash which IS hashed.)

**§5.1 DhfTurnContext addition (fixes R1-ISSUE-001):**

Add after the existing `IReadOnlyList<ToolCallRecord> ToolCalls` field:

```csharp
    /// <summary>
    /// Decoration payloads to write co-transactionally with the turn entry.
    /// Assembled by upstream pipeline stages (e.g., SPEC-07 vocabulary resolution)
    /// and carried through SPEC-05/SPEC-03 into this context. AppendTurnAsync
    /// writes them in the same SERIALIZABLE transaction as the entry and tool-call
    /// rows, eliminating the entry_id timing gap. May be null or empty.
    /// </summary>
    public IReadOnlyList<PendingDecoration>? PendingDecorations { get; init; }
```

Add new record after DhfTurnContext:

```csharp
public record PendingDecoration
{
    public required DecorationType Type { get; init; }
    public required JsonElement Payload { get; init; }
    public required string CreatedBy { get; init; }
}
```

**§6.1 OpenOrResumeSessionCore change (fixes R1-ISSUE-006):**

Insert BEFORE the existing `var existing = ...` query:

```csharp
    // Cross-workstation check: look for ANY open session (regardless of lock state)
    // with the same AddinSessionId on a DIFFERENT workstation within this tenant.
    // This catches the gap where a locked session on another workstation would be
    // invisible to both the unlocked-session and locked-same-workstation queries.
    var crossWorkstation = await conn.QuerySingleOrDefaultAsync<SessionLookupRow>(
        @"SELECT session_id, workstation_id, started_at, user_identity
          FROM dhf.sessions
          WHERE tenant_id = @TenantId
            AND addin_session_id = @AddinSessionId
            AND ended_at IS NULL
            AND workstation_id != @WorkstationId",
        new { tenantId, addinSessionId, metadata.WorkstationId },
        tx);

    if (crossWorkstation is not null)
    {
        _logger.LogError(
            "Cross-workstation session conflict within tenant {TenantId}: "
            + "addinSessionId {AddinSessionId} is open on workstation {OtherWorkstation} "
            + "(session {OtherSessionId}, started {StartedAt}, user {OtherUser}); "
            + "refusing to open or resume on workstation {ThisWorkstation}.",
            tenantId, addinSessionId, crossWorkstation.WorkstationId,
            crossWorkstation.SessionId, crossWorkstation.StartedAt,
            crossWorkstation.UserIdentity, metadata.WorkstationId);

        await tx.RollbackAsync(ct);

        throw new CrossWorkstationSessionConflictException(
            addinSessionId: addinSessionId,
            tenantId: tenantId,
            otherSessionId: crossWorkstation.SessionId,
            otherWorkstationId: crossWorkstation.WorkstationId,
            otherUserIdentity: crossWorkstation.UserIdentity,
            otherSessionStartedAt: crossWorkstation.StartedAt,
            thisWorkstationId: metadata.WorkstationId);
    }
```

**§7.1 AppendTurnCore addition (fixes R1-ISSUE-001):**

Insert after step 7 (INSERT tool call rows) and before the LogInformation call:

```csharp
    // 8. INSERT pending decorations (if any) in the same transaction.
    // Co-transactional write eliminates the entry_id timing gap — decorations
    // are guaranteed to exist iff the turn entry exists.
    if (turn.PendingDecorations is { Count: > 0 })
    {
        foreach (var deco in turn.PendingDecorations)
        {
            var decoHash = HashCanonicalizer.ComputeDecorationHash(deco.Payload);
            await conn.ExecuteAsync(
                @"INSERT INTO dhf.entry_decorations
                  (decoration_id, entry_id, decoration_type, created_by,
                   payload, decoration_hash)
                  VALUES (@Id, @EntryId, @Type, @CreatedBy,
                          @Payload, @Hash)",
                new {
                    Id = Guid.NewGuid(),
                    EntryId = entry.EntryId,
                    Type = deco.Type.ToStorage(),
                    CreatedBy = deco.CreatedBy,
                    Payload = deco.Payload,
                    Hash = decoHash
                }, tx);
        }
    }
```

## SPEC-06 Changes (v1.4.0 → v1.4.1)

**§4.3 response example change (fixes R1-ISSUE-009):**

Replace the JSON example:
```json
[
  {
    "entryId": "...",
    "sequenceNumber": 1,
    "timestamp": "...",
    "inputModality": "Voice",
    "rawTranscript": "Make a 100 by 50 by 20 plate",
    "turnType": "Execution",
    "undoOutcome": "Committed",
    "toolCallCount": 5,
    "recordHash": "...",
    "previousHash": "GENESIS"
  }
]
```

With:
```json
[
  {
    "entryId": "...",
    "sequenceNumber": 1,
    "timestamp": "...",
    "inputModality": "Voice",
    "turnType": "Execution",
    "rawTranscript": "Make a 100 by 50 by 20 plate",
    "toolCallCount": 5,
    "hasDecorations": false
  }
]
```

(Removed undoOutcome, recordHash, previousHash — not present in SPEC-04 §9.2 EntryListItem. Added hasDecorations to match the DTO.)

**§5.1 and §5.2 entry-detail change (fixes R1-ISSUE-005):**

Replace §5.1 first paragraph:
> Razor page showing full detail for one entry: transcript, tool calls in order with inputs and results expanded, feature tree before/after (folded), screenshot thumbnails (rendered from inline base64 data URLs — see §5.2), decorations, hash-chain status indicator.
>
> Tenant resolution: the endpoint internally resolves the entry's session, then the session's tenant. A caller must have arrived at this URL from a context that already holds the session's capability (the entry ID alone does not grant access; the session sidebar hides entries from other sessions).

With:
> Razor page showing full detail for one entry: transcript, tool calls in order with inputs and results expanded, feature tree before/after (folded), screenshot thumbnails (rendered from inline base64 data URLs — see §5.2), decorations, hash-chain status indicator.
>
> **Access control:** The endpoint requires a `sessionId` query parameter (or route parameter) in addition to the `entryId`. The endpoint verifies that the requested entry belongs to the specified session before returning data. If the entry does not belong to the session, the endpoint returns 404 (indistinguishable from "entry not found" to prevent information leakage). A caller without a valid session capability cannot access any entry detail. This closes the gap where knowledge of an `entryId` alone could bypass the session-capability access model.

In §5.2, add to the endpoint path:
> ### 5.2 GET /api/dhf/entries/{entryId}?sessionId={uuid}

Add to error responses:
> - 400 if `sessionId` is missing or malformed
> - 404 if entry not found OR entry does not belong to the specified session (indistinguishable responses)

## SPEC-07 Changes (v1.1.1 → v1.2.0)

**§3.1 Stage 5 change (fixes R1-ISSUE-001):**

Replace Stage 5 box:
```
┌──────────────────────────────────────────────────────┐
│ STAGE 5: Decoration Payload Assembly                  │
│   (returns payload in PipelineResult — NO DB write)   │
│   Assembles VocabularyResolutionDecoration payload    │
│   Returns it as PipelineResult.DecorationPayload     │
│   Persistence is handled co-transactionally by        │
│   SPEC-04 AppendTurnAsync (see Seam G/H in SPEC-00) │
└───────────────────┬──────────────────────────────────┘
```

**SDR-07-012 rewrite (fixes R1-ISSUE-001):**

Replace:
> **SDR-07-012 (NEW v1.1.0): Decoration write is on the critical path with bounded retry and circuit-breaker escalation.** [entire SDR]

With:
> **SDR-07-012 (v1.2.0 REWRITE — fixes R1-ISSUE-001): Decoration payload is assembled by the pipeline; persistence is co-transactional with the turn entry.** The vocabulary resolution pipeline assembles the `VocabularyResolutionDecoration` payload and returns it as part of `PipelineResult.DecorationPayload`. The pipeline does NOT write to the database — the DHF entry (which provides the `entry_id` the decoration references) does not exist at vocabulary-resolution time. SPEC-05 carries the decoration payload into `DhfTurnContext.PendingDecorations`. SPEC-04's `AppendTurnAsync` writes the decoration in the same SERIALIZABLE transaction as the turn entry and tool-call rows. This co-transactional model ensures: (a) decorations exist if and only if the turn entry exists, (b) decoration durability is guaranteed by the same retry/lock machinery that protects turn entries (DHF-014), (c) no separate circuit-breaker or retry logic is needed in SPEC-07. If `AppendTurnAsync` fails after all retries, the session locks per SPEC-04 §6.4 — both the turn and its decorations are lost together, which is correct (an incomplete turn should not leave orphan decorations).

**§3.2 latency budget change (fixes R1-ISSUE-001):**

Remove the "Decoration persistence" row from the latency table. Update total:

| Stage | p50 | p95 | Notes |
|-------|----:|----:|-------|
| Bouncer (Sonnet 4.6) | 350 ms | 600 ms | Cached results bypass this |
| Query planner (in-process) | 2 ms | 10 ms | Pure CPU, no I/O |
| Repository execution (batch) | 20 ms | 60 ms | IVocabReadSession wraps N queries in one RepeatableRead transaction |
| Per-domain synthesis (parallel) | 300 ms | 500 ms | Per-domain CancellationTokenSource, 3s individual timeout |
| Decoration payload assembly | 1 ms | 5 ms | In-memory JSON construction, no I/O |
| **Total** | **~670 ms** | **~1.18 s** | Decoration persistence cost is borne by AppendTurnAsync, not this pipeline |

**§3.3 failure modes change (fixes R1-ISSUE-001):**

Remove these rows from the failure modes table:
- "Decoration write failure (first attempt)"
- "Decoration write failure (retry exhausted)"
- "Decoration circuit breaker open"

Replace with:

| Failure | Detection | Response | Decoration Written? |
|---------|-----------|----------|---------------------|
| Decoration payload assembly fails (serialization error) | Exception in Stage 5 | Return `VocabularyResolutionFailure(reason=DecorationAssemblyFailed)` | No (payload could not be assembled) |

(Decoration persistence failures are now handled by AppendTurnAsync's existing retry/lock machinery in SPEC-04.)

**§6.4 PipelineResult addition (fixes R1-ISSUE-001):**

Add to PipelineResult:
```csharp
public sealed record PipelineResult
{
    public required IReadOnlyList<DomainResolution> DomainResolutions { get; init; }
    public required PipelineOutcome Outcome { get; init; }
    public required string RawUtterance { get; init; }
    public required ParsedIntent ParsedIntent { get; init; }
    public required StageLatencies Latencies { get; init; }
    /// <summary>
    /// Assembled VocabularyResolutionDecoration payload for co-transactional
    /// persistence via DhfTurnContext.PendingDecorations. The pipeline does NOT
    /// write this to the database; SPEC-04 AppendTurnAsync does.
    /// Null when the pipeline produces a non-success outcome (rejection, failure).
    /// </summary>
    public JsonElement? DecorationPayload { get; init; }
}
```

**§12 ACC table renumbering (fixes R1-ISSUE-004):**

Replace ACC-25, ACC-26, ACC-27 with ACC-28, ACC-29, ACC-30:

| ID | Criterion | Method |
|----|-----------|--------|
| ACC-21 | Voice utterance "tap a 6-32 UNC hole 0.5 inches deep" produces `PipelineResult` with threads `DomainResolved`; DHF records citation in co-transactionally persisted `vocabulary_resolution` decoration | `PipelineDemoMomentTests` scenario 1 |
| ACC-22 | Voice utterance "specify medical grade titanium" produces `PipelineResult` with materials `DomainResolved`; DHF records citation in co-transactionally persisted `vocabulary_resolution` decoration | `PipelineDemoMomentTests` scenario 2 |
| ACC-23 | Bouncer rejects adversarial input with structured fields | `BouncerAcceptRejectTests`, `BouncerSecurityEventLoggingTests` |
| ACC-24 | Ambiguous utterance produces `DomainAmbiguity`; agent opens `ambiguity_resolution` turn | `SynthesisAmbiguityTests` + SPEC-05 integration smoke |
| **ACC-28** | Multi-domain utterance produces `PipelineResult` with exactly 2 `DomainResolution` entries; `PipelineOutcome = AllResolved`; `DecorationPayload` carries BOTH resolutions | `PipelineMultiDomainDeterministicResolutionTests` |
| **ACC-29** | Vocabulary `DecorationPayload` in `PipelineResult` is co-transactionally persisted by `AppendTurnAsync`; decoration exists in `dhf.entry_decorations` after turn commit; `AppendTurnAsync` failure prevents both turn and decoration write | `PipelineDecorationCoTransactionalTests` |
| **ACC-30** | Bouncer cache preserves each request's own raw utterance; no cross-contamination | `BouncerCacheTests` |

Update the note at the bottom:
> ACC-28 through ACC-30 were renumbered from SPEC-07's original ACC-25/26/27 in v1.2.0 to resolve the global namespace collision with SPEC-00/SPEC-04's ACC-25/26/27 (R1-ISSUE-004).

**§11.5 pipeline test updates (fixes R1-ISSUE-001):**

Replace `PipelineDecorationDurabilityTests` and `PipelineDecorationCircuitBreakerTests` with:
> - `PipelineDecorationPayloadAssemblyTests` (v1.2.0) — successful resolution produces `PipelineResult.DecorationPayload` with valid JSON matching `VocabularyResolutionDecoration` schema; `SchemaVersion` is correct; all `DomainResolution` variants are represented; `DecorationPayload` is null when pipeline produces `VocabularyResolutionFailure` or `VocabularyResolutionRejected`.
> - `PipelineDecorationCoTransactionalTests` (v1.2.0) — integration test: SPEC-07 produces `DecorationPayload`; payload carried through `DhfTurnContext.PendingDecorations`; `AppendTurnAsync` writes decoration in same transaction; decoration row exists in `dhf.entry_decorations` with correct `entry_id` and `decoration_type = 'vocabulary_resolution'`; simulated `AppendTurnAsync` failure results in neither turn entry NOR decoration row being written.

## CAD_VOCAB_CORPUS.md

(No changes required.)