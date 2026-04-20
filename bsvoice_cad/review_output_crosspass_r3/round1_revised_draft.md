---
title: "BSVoice-CAD SPEC-00 — Orchestration Contract"
subtitle: "The six-module decomposition; interfaces only, no implementation"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-00 — Orchestration Contract

**Version:** 1.5.0
**Status:** DRAFT — R1 Oracle defect fixes
**Hackathon application:** Submitted 2026-04-17; awaiting selection notification

**Changelog v1.4.0 → v1.5.0:**
- R1 defect fix: DHF append failure session lock is now persistent via `locked_at`/`lock_reason` columns in `dhf.sessions` (SPEC-04 migration 007). Survives orchestrator restart. OpenOrResumeSessionAsync rejects locked sessions. Seam C and never-descoped list updated.
- R1 defect fix: Recovery events now stored as `turn_type = 'recovery'` entries in `dhf.entries` with `input_modality = 'system'`. `RecoveryEventContext` DTO and `AppendRecoveryEventAsync` implementation specified in SPEC-04 §5 and §7.2. Schema updated via SPEC-04 migration 008.
- R1 defect fix: `AddinTeardownMessage` added to SPEC-01 §4.2. `explicit_shutdown` DHF session closure reason now requires this message; transient transport loss does NOT close sessions. Glossary updated.
- R1 defect fix: SDR-TL-006 clarified: 'foundation' means extensible data structure, not partial implementation of any compliance requirement.
- R1 defect fix: Viewer `GET /` without sessionId now renders neutral landing page. Anonymous `?tenant=` query override removed. Session listing requires possession of a valid session UUID as capability proof. SPEC-06 v1.4.0.
- R1 defect fix: `IReadOnlyDhfRepository.ValidateChainAsync` is now strictly side-effect-free. Integrity-event decoration writes are performed only through `IDhfRepository.RecordIntegrityFindingsAsync`.
- R1 defect fix: Viewer route root confirmed as `GET /` (SPEC-06); migration doc §1 updated to remove `/viewer` reference.
- R1 defect fix: Migration doc §2 handshake failure description corrected — pre-upgrade failures use HTTP 400/401; WebSocket close code 4401 reserved for post-upgrade revocation only.
- R1 defect fix: Screenshot bytea columns added to schema (SPEC-04 migration 008). `EntryDetail` read DTO updated with `ScreenshotBeforePng`/`ScreenshotAfterPng` fields. Deep validation and viewer rendering are now implementable.

## 0. Why This Exists

BSVoice-CAD is six logical modules that interact at well-defined seams. This document defines the module boundaries, the seam contracts between them, cross-cutting architectural decisions (SDRs), shared vocabulary, scope gates, and review ordering. Implementation details live in the per-module specs (SPEC-01 through SPEC-06). Oracle reviews each module spec in isolation; this document is the integration contract that lets those reviews compose without drift.

**Non-goals for this document:** state machine details, record field definitions, SQL schemas, build sequence, timeline. Those live in per-module specs.

**Deployment boundary governance:** Every v1 (hackathon) vs v2 (Medtronic production) deployment-coupled decision lives in `DEPLOYMENT_V1_V2_MIGRATION.md` (this directory). Per-spec sections reference it by section number and MUST NOT redefine v1/v2 semantics locally. SDR-TL-011 is the SPEC-00 anchor for that document.

## 1. Module Map

| Spec | Module | Owns | Lives In | Depends On |
|------|--------|------|----------|------------|
| SPEC-01 | Wire Protocol | Cross-process types, polymorphic contracts, JSON policy, WebSocket framing contract, version handshake, TLS + API-key handshake (v1) | `BSVoiceCAD.Protocol` | (none — leaf module) |
| SPEC-02 | SolidWorks Adapter | STA command queue, `ISolidWorks*Service` contracts, COM-to-`ToolError` mapping, AddinSessionId generation algorithm, screenshot base64 producer | `BSVoiceCAD.Addin` | SPEC-01 |
| SPEC-03 | Tool-Call Lifecycle | Call state machine, timeouts, cancel protocol, quarantine rules, per-turn DHF aggregation, post-timeout recovery | `BSVoiceCAD.Orchestrator.Lifecycle` + `BSVoiceCAD.Addin.Bridge` | SPEC-01, SPEC-02, SPEC-04 |
| SPEC-04 | DHF Ledger | Schema, hash model, append logic, DHF session lifecycle, read-only repository projection, tenant-scoping | `BSVoiceCAD.Orchestrator.Dhf` + `BSVoiceCAD.Orchestrator.Database` | SPEC-01 (message shapes), SPEC-00 §6 (AddinSessionId epoch semantics) |
| SPEC-05 | Agent Loop + Input | User input, confirmation flow, Opus 4.7 tool-calling, vision context, retry correlation, system prompt | `BSVoiceCAD.Orchestrator.Agent` + `BSVoiceCAD.Orchestrator.Input` | SPEC-01, SPEC-03 |
| SPEC-06 | Viewer | Timeline rendering, DHF read API, integrity endpoints, Railway-hosted at `vcad.boundstate.ai` | `BSVoiceCAD.Orchestrator.Viewer` | SPEC-04 (read-only) |

**Dependency direction is strictly downward.** SPEC-01 depends on nothing. SPEC-06 depends on SPEC-04 only. Circular references are a defect.

**Assembly mapping note:** The "Lives In" column names the primary assembly (`.csproj`) for each module. Where two assemblies are listed (separated by `+`), the first targets the orchestrator runtime (.NET 8, Railway-hosted) and the second targets the add-in runtime (.NET Framework 4.8, local Windows workstation). The architecture-test project (`BSVoiceCAD.Tests.Architecture`) must reference all assemblies from both runtimes to enforce cross-runtime dependency rules. SPEC-03's `BSVoiceCAD.Addin.Bridge` assembly contains only the add-in-side dispatch surface; all lifecycle state and DHF aggregation logic lives in `BSVoiceCAD.Orchestrator.Lifecycle`.

**AddinSessionId epoch semantics** are defined in this document's glossary (§6) so that SPEC-04 can consume them without a dependency on SPEC-02. SPEC-02 owns the generation algorithm; SPEC-01 owns the format regex and epoch rotation contract; SPEC-04 consumes only the epoch rotation invariants defined here.

## 2. Seams (Where The Modules Touch)

Each seam enforces one-directional dependency. **Compile-time** dependency direction is verified by `NetArchTest` rules living in a dedicated architecture-test project (`BSVoiceCAD.Tests.Architecture`) that is permitted to reference all module assemblies. **Runtime** seam behavior — transport correctness, serialization round-trips, reconnect semantics, threading invariants — is verified by integration and contract tests in `BSVoiceCAD.Tests.Integration` (see §3). Per-module test projects test only their own module's internal invariants.

### Seam A: SPEC-01 → everyone

Protocol types are consumed by every other module. No module defines its own cross-process wire types. Architecture rule: no cross-process DTO types (types serialized over the WebSocket transport) exist outside `BSVoiceCAD.Protocol.*` namespaces. Internal module-boundary types (e.g., `DhfTurnContext` in SPEC-04) are permitted provided they do not cross the WebSocket boundary.

The cross-process transport between the .NET Framework 4.8 add-in (local workstation) and the .NET 8 orchestrator (Railway-hosted at `vcad.boundstate.ai`) is a **public-internet WebSocket** connection with TLS termination at the Railway edge, API-key authentication in the handshake (v1), and JSON message framing. The full transport contract — including connection lifecycle, error semantics, reconnect behavior, threading guarantees, auth handshake, and TLS — is defined in SPEC-01. For v1 → v2 transport evolution (API key → OAuth device flow), see `DEPLOYMENT_V1_V2_MIGRATION.md` §2.

SPEC-01 v1.4.0 adds `AddinTeardownMessage` to the `IAddinMessage` polymorphic family. This message enables the add-in to signal graceful shutdown, which is the sole trigger for `explicit_shutdown` DHF session closure (see §6 Glossary).

### Seam B: SPEC-03 → SPEC-02

The tool-call lifecycle dispatches `IToolInput` to the SolidWorks adapter and expects `IToolResult` or `ToolError` back. The adapter never owns lifecycle state; the lifecycle never owns COM threading. SPEC-02 provides a synchronous method surface; SPEC-03 wraps each call in its own timeout and cancel semantics.

**Cancellation boundary:** Cancellation is effective only before a COM call is dispatched to the STA thread. Once a synchronous COM call is in flight, it runs to completion; the STA thread cannot be safely interrupted. A call whose orchestrator-side timeout expires while COM execution is in progress is recorded as `TimedOut`, but the COM operation may still complete and mutate the model.

**Post-timeout recovery (mandatory — never descopable):** Before any subsequent turn may open, SPEC-03 must execute a recovery flow. SPEC-03 defines the deterministic selection rule between the two permitted strategies (state refresh via feature-tree comparison, or undo-group rollback) and the observable success criterion for each. If recovery itself fails, the session enters a locked state that prevents further turns until manual intervention. The recovery outcome — including whether a post-timeout COM completion was detected and whether the model was rolled back or reconciled — is recorded as a DHF recovery event via `DhfLedger.AppendRecoveryEventAsync` (defined in SPEC-04 §7.2) before the next turn opens. Recovery events are stored as `turn_type = 'recovery'` entries in `dhf.entries` and participate in the hash chain. This ensures the audit trail captures any model mutation that occurred after the timeout.

**Turn completion boundary for timeouts:** A timed-out turn closes at the moment the timeout fires; the turn's DHF entry records the `TimedOut` status for the affected tool call(s). Any post-timeout recovery constitutes a separate recovery event, not a continuation of the timed-out turn. The hash chain includes both the turn entry and the recovery event in sequence.

### Seam C: SPEC-03 → SPEC-04

The lifecycle layer aggregates all tool-call outcomes within a single turn and produces one DHF write per completed turn. Upon turn completion, SPEC-03 constructs a `DhfTurnContext` (defined in SPEC-04) containing all tool-call results for that turn and hands it to `DhfLedger.AppendTurnAsync`. The DHF layer never observes individual lifecycle transitions directly; it receives complete, aggregated turn records only. The hash chain covers turn-level entries and recovery events.

**Incomplete turns (crash, disconnect, kill mid-turn):** Turns that fail to complete do not produce DHF turn entries, because SPEC-03 has not yet constructed the `DhfTurnContext`. On session closure (detected via heartbeat timeout or reconnect with a new AddinSessionId), SPEC-04 records a session-closure event noting the reason. On the next session start, SPEC-03 must perform state reconciliation (feature-tree comparison against last recorded turn) before opening the first new turn. If the reconciliation detects model state that diverges from the DHF record, SPEC-03 records a reconciliation event via `DhfLedger.AppendRecoveryEventAsync`. The undo group for an incomplete execution turn was never closed; SolidWorks behavior for unclosed `UndoObjectScope` is documented in SPEC-02.

**DHF append failure locks the session persistently (never descopable):** If `DhfLedger.AppendTurnAsync` fails after all retries, the orchestrator MUST NOT silently continue. The session is locked persistently by calling `DhfLedger.LockSessionAsync` (SPEC-04 §7.1), which sets `locked_at` and `lock_reason` on the `dhf.sessions` row. No further turns may be opened on a locked session — both `AppendTurnAsync` and `OpenOrResumeSessionAsync` reject locked sessions. The lock survives orchestrator restart because it is persisted in the database. The user is notified via the agent output channel. Reopening requires manual intervention (operator closes the session via SQL, reconciles the model state, and starts a new session). This invariant is codified in SPEC-04 §11 DHF-014 and enforced at the lifecycle layer.

### Seam D: SPEC-05 → SPEC-03

The agent loop submits typed tool calls to the lifecycle layer and awaits terminal outcomes. The agent never touches the WebSocket, never touches the STA queue, never writes to the DHF. It is a pure consumer of the lifecycle surface. SPEC-05 has no compile-time dependency on SPEC-04; all DHF interaction is mediated by SPEC-03.

### Seam E: SPEC-05 → SPEC-04 (indirect, via SPEC-03)

The agent loop opens turns but does not write DHF entries directly. SPEC-03 is the sole DHF writer. This keeps turn construction in one place. SPEC-05 has no direct assembly reference to SPEC-04; the dependency is purely indirect through SPEC-03's aggregation role.

### Seam F: SPEC-06 → SPEC-04

The viewer queries `IReadOnlyDhfRepository` (defined in SPEC-04 §9.2 as a read-only projection of the full `IDhfRepository`) read-only. No write paths exist from the viewer. The viewer's isolated DI container resolves only `IReadOnlyDhfRepository`; the write-capable `IDhfRepository` is not registered. `IReadOnlyDhfRepository.ValidateChainAsync` is strictly side-effect-free — it returns validation results in-memory only and does NOT write integrity-event decorations. Decoration writes for integrity findings are performed exclusively through `IDhfRepository.RecordIntegrityFindingsAsync` by the orchestrator runtime. This architectural isolation is enforced by both a DI-container test and a NetArchTest rule (SPEC-06 SDR-06-003).

## 3. Mandatory vs Stretch (Top-Level)

Mandatory scope covers the 9-tool demo (`new_part`, `start_sketch`, `draw_rectangle`, `draw_circle`, `add_dimension`, `exit_sketch`, `extrude`, `screenshot`, `get_feature_tree`), text input via ConsoleInputService, undo groups, per-turn DHF with hash chain, minimal viewer timeline at `vcad.boundstate.ai`. Per-module mandatory/stretch lists live in each module spec.

**Descope priority (LIFO):** Installer, Demo 2, viewer polish, DHF extraction, error recovery UX (user-facing retry messaging and graceful degradation prompts), additional grounding tools beyond the mandatory two (`screenshot`, `get_feature_tree`), Sonnet 4.6 decoration extraction, patterns, voice, Clerk viewer auth (deferred to v1.5; see SDR-TL-012), all stretch. **Never descoped:** the 9 mandatory tools (including `screenshot` and `get_feature_tree`), per-turn DHF, hash chain at entry level, minimal viewer timeline, undo groups, text input, post-timeout state reconciliation (Seam B mandatory recovery), **DHF append failure locks the session persistently** (Seam C mandatory behavior — lock persisted via `locked_at`/`lock_reason` columns; survives orchestrator restart), WebSocket TLS + API-key handshake, ACC-01/02/03/04/08/10/12/20 acceptance tests.

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

(ACC-20 was renumbered from SPEC-06's original ACC-08 to resolve the cross-spec numbering collision flagged in the cross-pass Oracle review. SPEC-02's ACC-08 is the canonical holder of the number.)

### Integration and E2E Test Project

Cross-module acceptance tests (ACC-01, ACC-04, and any other tests requiring multiple module assemblies or external dependencies) are owned by `BSVoiceCAD.Tests.Integration`. This project is permitted to reference all module assemblies and requires the following runtime dependencies: a SolidWorks instance (or a documented test double for CI), a PostgreSQL database (EmbeddedPostgres permitted), and either a live Anthropic API key or a recorded-response test double. ACC tests that can be verified within a single module (ACC-02, ACC-03, ACC-08, ACC-10, ACC-12, ACC-19, ACC-20) may live in that module's own test project, but must also have an integration-level smoke test in `BSVoiceCAD.Tests.Integration` to verify cross-module wiring.

**Test project naming is canonical:** `BSVoiceCAD.Tests.Integration`, `BSVoiceCAD.Tests.Architecture`, `BSVoiceCAD.Orchestrator.Tests`, `BSVoiceCAD.Addin.Tests`, `BSVoiceCAD.Protocol.Tests`. This resolves the test-project-naming drift flagged in cross-pass Oracle P0-6.

## 4. Top-Level SDRs (Decisions That Touch Multiple Specs)

SDRs local to a single module live in that module's spec. These cross-cut.

**SDR-TL-001: All-.NET.** Add-in .NET Framework 4.8 (SW COM requirement), orchestrator .NET 8 (Railway-hosted), shared Protocol .NET Standard 2.0 with `LangVersion=latest`. Locked.

**SDR-TL-002: PostgreSQL 16 with EmbeddedPostgres local-dev fallback.** Production v1 uses Railway-managed Postgres 16. EmbeddedPostgres is a local-dev convenience only — not a deployable fallback. No SQLite. Locked.

**SDR-TL-003: Direct Anthropic API.** Opus 4.7 primary. Sonnet 4.6 for decoration extraction is stretch scope — not required for the mandatory 9-tool demo; if descoped, all tool schemas and prompts use Opus 4.7 only. No Bedrock for hackathon. Locked (Opus 4.7 mandatory; Sonnet 4.6 stretch).

**SDR-TL-004: Code license MIT; spec classification Proprietary.** Locked.

**SDR-TL-005: Hackathon selection gate.** Application submitted 2026-04-17; awaiting selection notification from Anthropic hackathon organizers. Eligibility-neutral build work (protocol types, schema, CLAUDE.md, test infrastructure) proceeds now. Submission-affecting work (video recording, public repo publication, LICENSE commit) blocks on confirmed selection. If not selected, the build still produces a working SolidWorks voice copilot that serves as the reference implementation for the Medtronic partnership pitch.

**SDR-TL-006: DHF regulatory framing.** Positioned as "design audit log foundation for future 21 CFR 820.30 / Part 11 alignment." **"Foundation" means the data structure is extensible toward compliance requirements — it does NOT mean any compliance requirement is partially or fully implemented.** **This system is NOT 21 CFR Part 11 compliant and MUST NOT be represented as such to customers, partners, or regulators.** The gap-analysis table in SPEC-04 enumerates specific Part 11 requirements not yet met (e.g., electronic signatures, validated system status, audit trail completeness under 21 CFR 11.10). Governance owner for gap-analysis maintenance and any compliance claims: Dr. Joseph Wilde (BoundState AI CEO). Any customer-facing artifact (pitch deck, demo, documentation) that references 21 CFR or Part 11 must include the non-compliance disclaimer verbatim.

**SDR-TL-007: camelCase JSON property policy; PascalCase enum string values.** All wire formats and DHF JSONB payloads use camelCase property names. All enums serialize as PascalCase strings via `JsonStringEnumConverter`. Enforcement is split by ownership: wire-format serialization rules are enforced build-breaking in SPEC-01 tests; DHF JSONB serialization rules are enforced build-breaking in SPEC-04 tests. Both test suites verify round-trip fidelity for their respective type sets.

**SDR-TL-008: Opus 4.7 plays two roles (mandatory), not three.** Vision interpreter (context-only; enriches agent prompt with structured observations from recent screenshot) and tool-calling agent. Same vendor, same model family, two uses. No multi-vendor consensus layer at runtime. The system relies on single-model inference with extended thinking enabled; the DHF provides external traceability including ambiguity events. No testable "self-verification" contract is assumed at the model level. (Sonnet 4.6 decoration extraction, if in scope, is a third optional role — see SDR-TL-003.)

**SDR-TL-009: No vector retrieval. BSMS-shaped architecture only.** BSVoice-CAD's runtime MUST NOT use FAISS, any other vector database, embedding-based similarity search, or any retrieval mechanism whose result quality is measured by cosine distance. This applies to CAD vocabulary resolution, feature-tree querying, precedent lookup, ambiguity resolution, and any other place where the temptation arises to "just embed the thing and do nearest-neighbor."

The architectural alternative — required for every current and future knowledge-retrieval need in this product — is the BoundState BSMS pattern: **B**ouncer (domain-persona input gate that classifies intent and rejects out-of-scope or malicious input), **S**onnet reasoning over structured candidates, **M**APTIS-equivalent ground-truth database query against a structured Postgres schema, **S**onnet synthesis with row-level citations. This pattern is validated in production at NASA MSFC (MIRA) and is the mandated retrieval architecture for every BoundState product.

**Rationale:**
1. Vector retrieval fails open under domain drift. When a query doesn't match stored content, FAISS returns the nearest thing in embedding space, which is confidently wrong. BSMS fails closed — the database either has the row or it doesn't, and the absence is a first-class signal.
2. Vector retrieval cannot be audited. Regulated environments (medtech, aerospace, defense) cannot accept "cosine similarity 0.87 to a document we never showed you." BSMS produces SQL-queryable audit trails where every answer points to specific rows.
3. Vector retrieval accumulates complexity without bound. The failure mode is always "one more reranker, one more chunk-size parameter, one more hybrid-search layer, one more embedding model swap." Architectures that require continuous patching to stay useful are signaling that their foundation is wrong.
4. BSMS is structurally hallucination-resistant. Sonnet reasons over concrete rows; the bouncer rejects adversarial input before it reaches reasoning; the database is the ground truth. There is no path by which the model invents a material, tool, parameter, or precedent that does not exist in the schema.

**Scope of this SDR within BSVoice-CAD (hackathon):** For the hackathon deliverable, BSVoice-CAD is small enough that most knowledge needs are handled inline by the tool-calling agent against live SolidWorks state and typed tool schemas. No FAISS is needed and none is introduced. Vision Context (SDR-TL-008) is enrichment from the current screenshot, not retrieval.

**Scope of this SDR post-hackathon (CAD-BSMS roadmap):** When BSVoice-CAD adds a vocabulary layer for medtech-specific CAD terminology ("bore," "lumen," "flange," thread callouts, parameter naming conventions), it MUST use BSMS applied to CAD — a Postgres-backed vocabulary DB with a bouncer persona ("retired medical-device M&P engineer") gating user input. This is the documented path to the v1.0 regulated release and the Medtronic partnership pitch. It is not FAISS. It will never be FAISS.

**Enforcement:** Architecture review for any runtime change in BSVoice-CAD that introduces a retrieval mechanism MUST verify the mechanism is BSMS-shaped. Reviewer checklist: (1) is there a domain-persona bouncer? (2) is the ground-truth store a structured database (Postgres, not vector)? (3) is reasoning performed over typed rows, not cosine-scored chunks? (4) do results carry row-level citations? If any answer is no, the change is rejected. This enforcement lives in the cross-spec architecture review, not in per-module test code (because the test for "did you use FAISS" is a structural review question, not a unit-test assertion).

**Locked. Applies to all descendants of this architecture (CAD-BSMS, Manufacturing-BSMS, future verticals). See `D:\\BoundState\\BOUNDSTATE_PLATFORM_PRINCIPLES.md` for the cross-product governance statement.**

**SDR-TL-010:** (Reserved — assigned in a prior draft to a placeholder that did not survive editorial review. Not reused, to preserve traceability of cross-reference numbering in the findings reports.)

**SDR-TL-011: Deployment boundary — Railway-hosted orchestrator and viewer at `vcad.boundstate.ai`; add-in runs local on Windows workstation.** The WebSocket between add-in and orchestrator crosses the public internet, with TLS termination at the Railway edge and API-key authentication in the handshake. This explicitly reverses any implicit "localhost-only" assumption carried through earlier drafts. The full v1 transport contract (TLS, auth, rate limiting, reconnect) is specified in SPEC-01 §12. v1 → v2 evolution path (API key → OAuth device flow) is specified in `DEPLOYMENT_V1_V2_MIGRATION.md` §2.

**Threat model for v1:** The WebSocket is authenticated but the viewer is not (see SDR-TL-012). Session URLs are UUIDv4 capabilities — possession of a valid session UUID is the sole access gate. The viewer does NOT auto-expose sessions without a session UUID; `GET /` without a `sessionId` parameter renders a neutral landing page. v1 is defensible for: (a) the hackathon demo, (b) the initial Medtronic "look at the product" walkthrough, (c) any session where the design content does not include PHI, PII, or trade-secret medical-device designs. v1 is NOT defensible for actual regulated customer use.

**Locked.**

**SDR-TL-012: Viewer auth deferred to v1.5 (post-hackathon).** v1 viewer has no authentication. Access control is "knowledge of the session UUID" — a UUIDv4 capability with ~122 bits of entropy. The landing page at `https://vcad.boundstate.ai/` is publicly accessible but shows no session data without a valid `sessionId` parameter. Session URLs (`https://vcad.boundstate.ai/?sessionId={sessionId}`) are reachable by anyone who has the URL; knowing one session UUID for a tenant reveals all sessions for that tenant via the sidebar (acceptable for hackathon; v1.5 Clerk resolves this). Clerk SSO integration is the v1.5 upgrade path and is estimated at 15-30 minutes of self-contained integration work (middleware on viewer routes, Clerk session cookie, no changes to DHF schema, orchestrator WebSocket, or protocol). Clerk integration is **not** a v1→v2 protocol change and does not bump `ProtocolVersion`. The v1.5 timing is "whenever a customer relationship requires it." Auth0 is the documented fallback if Clerk is unavailable (ITAR-restricted customers or air-gapped deployment). See `DEPLOYMENT_V1_V2_MIGRATION.md` §4 for the migration mechanism.

**Rationale for deferring:** The viewer is the thing Medtronic will look at first, but the first look does not include PHI. The hackathon timeline is tight, and Clerk integration competes with higher-priority work (the 9 mandatory tools, the DHF append path, the deploy-to-Railway path). Deferring is cheap because Clerk slots in as middleware without touching any architectural boundary. The risk of deferring is that someone shares a session URL; the mitigation is that the session URL is opaque (122 bits entropy) and the landing page does not expose any data without one.

**Locked.**

## 5. Review Order

Oracle reviews modules in dependency order so lower-numbered specs stabilize before higher ones consume them.

1. SPEC-00 (this document) — establishes module boundaries
2. SPEC-01 (Wire Protocol) — leaf module, gates everything
3. SPEC-02 (SolidWorks Adapter) — no circular dependency with SPEC-03
4. SPEC-04 (DHF Ledger) — depends on SPEC-01 message shapes and SPEC-00 §6 epoch semantics; can converge in parallel with SPEC-02
5. SPEC-03 (Tool-Call Lifecycle) — depends on SPEC-01, SPEC-02, SPEC-04
6. SPEC-05 (Agent Loop + Input) — depends on SPEC-01, SPEC-03
7. SPEC-06 (Viewer) — depends on SPEC-04 only; can be deferred

After all individual passes converge, one integration Oracle pass on SPEC-00 catches any remaining cross-cuts. Expected cross-cut defect count: low, because the seams above define them away.

## 6. Glossary (Terms Used Across Multiple Specs)

- **Turn:** One user utterance + the agent's response, possibly containing 0+ tool calls. Turns are the DHF-entry granularity; one DHF entry per completed turn.
- **Turn type:** One of `execution` (produces geometry and an undo group), `clarification` (agent asks; zero tool calls; no undo group), `confirmation` (agent requests user approval; zero tool calls; no undo group), `ambiguity_resolution` (clarification turn where voice and vision disagreed on target entity; DHF records both inputs and the resolution), or `recovery` (system-generated entry recording post-timeout or post-crash reconciliation outcome; see Recovery Event below).
- **Undo Group:** The SolidWorks UndoObjectScope wrapping all tool calls in one turn. One undo group per execution turn; zero for non-execution turns.
- **AddinSessionId:** 32-character lowercase hex string (format regex `^[0-9a-f]{32}$`) generated fresh on each add-in load. Derivation algorithm — including hash function, input fields, and fallback behavior — is specified in SPEC-02 §8; SPEC-01 §9 specifies only the format contract and validator. **Epoch semantics (consumed by SPEC-04, authoritative source is this glossary):** A new AddinSessionId signals a new epoch. On epoch change: (1) StateVersion resets to 0; (2) the current DHF session (if any) is closed with reason `superseded_session`; (3) a new DHF session opens on the first AddinReadyMessage carrying the new AddinSessionId. Rotation invariant: AddinSessionId changes if and only if the add-in is reloaded (SolidWorks restart, add-in disable/re-enable, or crash recovery). A given SolidWorks process runs exactly one add-in instance; cross-workstation collisions on the same AddinSessionId are handled as a hard error per SPEC-04 SDR-04-008.
- **StateVersion:** Monotonic per-AddinSessionId counter carried by state-bearing messages. Resets to 0 on new AddinSessionId epoch.
- **Tool-Call Lifecycle State:** `Sent → Completed | TimedOut | Cancelled | Abandoned`. Terminal states are final. The `ToolCallLifecycleState` enum is defined in SPEC-01 (cross-process type; consumed by SPEC-03 and SPEC-04).
- **DHF Session:** One row in `dhf.sessions`. Opens on first AddinReadyMessage for a given AddinSessionId. Closes on: (a) `explicit_shutdown` — add-in sends an `AddinTeardownMessage` (defined in SPEC-01 §4.2) before process exit, signaling intentional shutdown; (b) `superseded_session` — a new AddinSessionId arrives, retiring the prior session; (c) `heartbeat_timeout` — heartbeat absence exceeded threshold; root cause (add-in crash, SolidWorks exit, network failure, orchestrator idle-timeout disconnect) is not distinguishable from the orchestrator side and is recorded as unknown. **Transient transport loss (network drop, orchestrator idle-timeout, Wi-Fi hiccup) does NOT close the DHF session** — the session remains open pending add-in reconnect with the same AddinSessionId per SPEC-04 SDR-04-005. **DHF sessions are NOT closed on orchestrator graceful shutdown** — the add-in may still be running and will resume the session on reconnect. If finer-grained cause attribution is needed in the future, it must flow through the `AddinTeardownMessage`; `heartbeat_timeout` does not claim to know why. Reconnect and dangling-session reconciliation rules are defined in SPEC-04.
- **Recovery Event:** A DHF entry with `turn_type = 'recovery'` and `input_modality = 'system'` recording the outcome of a post-timeout or post-crash reconciliation. Stored in `dhf.entries` and participates in the hash chain like any other entry. Includes: trigger (timeout, crash, disconnect), strategy applied (state refresh or undo-group rollback), success/failure, and any detected model-state divergence. The `RecoveryEventContext` DTO is defined in SPEC-04 §5. Appended via `DhfLedger.AppendRecoveryEventAsync` (defined in SPEC-04 §7.2).
- **Quarantine:** A late message received after a terminal lifecycle state is logged and discarded; it never updates session state, never contributes to DHF turn entries, never triggers retries. **Exception:** If a quarantined message provides evidence of post-timeout model mutation (e.g., a late `Completed` result for a `TimedOut` call), the quarantine log entry is referenced by the subsequent mandatory recovery event to preserve audit trail continuity.
- **Vision Context:** Structured observations extracted by Opus 4.7 from a recent SolidWorks viewport screenshot, passed into the agent loop's prompt as enrichment. Not a decision-making component; purely enriches the agent's understanding of what the user can currently see. Defined in SPEC-05.
- **Ambiguity Event:** A clarification turn triggered because the user's utterance (voice transcript) and the current visual/geometric state imply multiple possible targets. Recorded in the DHF as `turn_type = 'ambiguity_resolution'` with both the transcript and the vision context observations preserved.
- **BSMS:** BoundState's mandated retrieval and knowledge-access pattern. **B**ouncer (domain-persona input gate), **S**onnet reasoning, **M**aterial/domain ground-truth database query, **S**onnet synthesis with row-level citations. Validated in MIRA at NASA MSFC. See SDR-TL-009.
- **Tenant:** In v1, identified implicitly by the API key used to open the WebSocket connection. The `api_keys` table (SPEC-01 §12) maps key → tenant identifier. Every DHF session row carries a `tenant_id` (SPEC-04 §3 migration 006). In v2, tenant is identified by the OAuth authentication context. See `DEPLOYMENT_V1_V2_MIGRATION.md` §5.
- **AddinTeardownMessage:** An `IAddinMessage` (defined in SPEC-01 §4.2, v1.4.0) sent by the add-in to signal graceful shutdown before process exit. Carries a `TeardownReason` enum indicating the cause (user disabled add-in, SolidWorks shutting down, or unrecoverable add-in error). Receipt of this message is the sole trigger for `explicit_shutdown` DHF session closure.

## 7. Build-Readiness

This document is short on purpose. Its correctness is a function of whether the module boundaries match the actual architecture, not whether it covers every detail. Implementation does not start here; it starts in the per-module specs.

Implementation blockers, in order:
1. SPEC-01 converges (wire protocol locked, TLS + auth handshake specified)
2. SPEC-02 + SPEC-04 converge in parallel (SPEC-04 consumes AddinSessionId epoch semantics from SPEC-00 §6, not from SPEC-02 internals)
3. SPEC-03 converges (depends on all three above)
4. SPEC-05 converges
5. SPEC-06 can start anytime after SPEC-04 converges
6. Integration Oracle pass on SPEC-00
7. **Day-0 validation:** deploy orchestrator to Railway, establish `wss://vcad.boundstate.ai/ws` connection from the add-in running locally, complete a tool round-trip end-to-end. This must succeed before any feature work begins — it de-risks every subsequent spec's assumptions about the network path.
8. SolidWorks 2025 SP3+ validation on the Windows workstation; Postgres 16 on Railway; Anthropic.SDK from the deployed orchestrator; Whisper.net CUDA validated only if voice remains in scope.

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*
---
title: "BSVoice-CAD SPEC-01 — Wire Protocol"
subtitle: "Cross-process types, polymorphic contracts, JSON policy, version handshake, TLS + auth handshake"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-01 — Wire Protocol

**Version:** 1.4.0
**Status:** DRAFT — R1 Oracle defect fixes
**Depends on:** SPEC-00 (module boundaries, deployment SDRs), `DEPLOYMENT_V1_V2_MIGRATION.md` (v1/v2 deployment boundary)
**Consumed by:** SPEC-02, SPEC-03, SPEC-04, SPEC-05

**Changelog v1.3.0 → v1.4.0:**
- R1 defect fix: Added `AddinTeardownMessage` to `IAddinMessage` polymorphic family (§4.2) with `TeardownReason` enum (§6.4). Enables `explicit_shutdown` DHF session closure per SPEC-00 §6 glossary.
- §5: `AddinTeardownMessage` record definition added.
- §16: `AddinTeardownMessageSerializationTests` added to required test list.
- §18: Updated SPEC-02 consumption to include `AddinTeardownMessage` and `TeardownReason`.

## 0. Scope

This spec defines every type that crosses a process boundary in BSVoice-CAD, plus the boundary-level behavioral rules that govern how those types are validated at the wire edge (epoch rejection, version-match handshake, per-tool timeouts, message-envelope invariants, TLS + authentication handshake).

All other specs reference types defined here and add nothing of their own to the wire format.

**In scope:** polymorphic message hierarchies, tool input/result records for all mandatory tools, shared enums and records (including the `ToolCallLifecycleState` enum consumed by SPEC-03 and SPEC-04), JSON serialization policy, protocol versioning, handshake message shapes, TLS + API-key authentication handshake, boundary validation rules.

**Out of scope:** state machines (SPEC-03), DHF schema (SPEC-04), COM service contracts (SPEC-02), agent loop logic (SPEC-05), OAuth device-flow implementation (v2 — see `DEPLOYMENT_V1_V2_MIGRATION.md` §2). Stretch tool field definitions are deferred; their records exist as empty placeholders with polymorphic registrations to maintain registry completeness (see Section 7.2).

**Deployment model — Railway-hosted orchestrator, local add-in:** The orchestrator runs on Railway at `vcad.boundstate.ai`. The add-in runs on the engineer's Windows workstation alongside SolidWorks. The WebSocket between them crosses the public internet over TLS (`wss://vcad.boundstate.ai/ws`). TLS is terminated at the Railway edge; the orchestrator process speaks plain WS internally but the contract described here is `wss://`. Authentication is API key in the WebSocket handshake (v1 only); v2 uses OAuth 2.0 device flow per `DEPLOYMENT_V1_V2_MIGRATION.md` §2.

**This explicitly reverses the earlier co-location constraint.** Prior drafts assumed orchestrator and add-in shared a local filesystem and a localhost WebSocket. That model is dead. Screenshots are transported inline as base64 (v1) or as S3 presigned URLs (v2) — never as local filesystem paths. See §7.1 for the `ScreenshotResult` contract.

## 1. Project Shape

```
BSVoiceCAD.Protocol/                        # .NET Standard 2.0, LangVersion=latest
├── Abstractions/
│   ├── IToolInput.cs                       # Marker; [JsonPolymorphic]
│   ├── IToolResult.cs                      # Marker; [JsonPolymorphic]
│   ├── IClientMessage.cs                   # Orchestrator → Add-in
│   └── IAddinMessage.cs                    # Add-in → Orchestrator
├── Versioning/
│   └── ProtocolVersion.cs                  # const string Current = "1.0.0"
├── Messages/
│   ├── ToolCallMessage.cs
│   ├── CancelToolCallMessage.cs
│   ├── StartUndoGroupMessage.cs
│   ├── CommitUndoGroupMessage.cs
│   ├── RollbackUndoGroupMessage.cs
│   ├── ClientProtocolErrorMessage.cs
│   ├── ToolResultMessage.cs
│   ├── StateUpdateMessage.cs
│   ├── AddinReadyMessage.cs
│   ├── AddinTeardownMessage.cs             # NEW v1.4.0
│   ├── UndoGroupOutcomeMessage.cs
│   └── AddinProtocolErrorMessage.cs
├── Tools/
│   ├── Inputs/                             # One record per tool
│   ├── Results/                            # One record per tool
│   ├── Common/
│   │   ├── ToolError.cs
│   │   ├── ToolErrorCode.cs                # enum
│   │   ├── ToolCallLifecycleState.cs       # enum
│   │   ├── TimingReport.cs
│   │   ├── UnitSystem.cs
│   │   ├── DocumentType.cs
│   │   ├── ExtrudeDirection.cs
│   │   ├── StandardPlane.cs
│   │   ├── DimensionType.cs
│   │   ├── DimensionMode.cs
│   │   ├── EntityKind.cs
│   │   ├── EntityRef.cs
│   │   ├── SketchEntityId.cs
│   │   ├── SelectionRef.cs                 # Discriminated union (persistent | sketch)
│   │   ├── DimensionTarget.cs              # Discriminated union
│   │   ├── PlaneRef.cs                     # Discriminated union
│   │   ├── CancelReason.cs                 # enum (message-level)
│   │   ├── RollbackReason.cs               # enum (message-level)
│   │   ├── TeardownReason.cs               # enum (message-level) — NEW v1.4.0
│   │   ├── ClientProtocolErrorCode.cs      # enum (message-level)
│   │   └── AddinProtocolErrorCode.cs       # enum (message-level)
│   ├── Registry/
│   │   ├── ToolNameAttribute.cs
│   │   ├── ToolRegistry.cs
│   │   └── ToolTimeouts.cs                 # Per-tool timeout table
│   └── Schema/
│       ├── JsonSchemaGenerator.cs
│       └── AnthropicToolSpec.cs
├── State/
│   ├── StateSnapshot.cs
│   ├── FeatureNode.cs
│   ├── FeatureParameter.cs                 # Polymorphic base
│   ├── DocumentRef.cs
│   └── NullDocumentState.cs
├── Transport/
│   ├── ApiKeyHandshake.cs                  # v1 subprotocol parser + validator
│   └── HandshakeResult.cs
├── Serialization/
│   ├── JsonOptions.cs                      # camelCase, string enums
│   └── HashCanonicalizer.cs
└── Validation/
    ├── Validators/                         # One per tool input + message envelope
    ├── AddinSessionIdFormatValidator.cs
    └── ToolInputValidator.cs
```

## 2. JSON Policy

Locked decisions. Enforced by build-breaking tests in `BSVoiceCAD.Protocol.Tests`.

### 2.1 Property Naming

`JsonOptions.Default.PropertyNamingPolicy = JsonNamingPolicy.CamelCase`.

Every wire property name is camelCase. Rejected alternatives: PascalCase (inconsistent with general JSON convention), snake_case (inconsistent with C# conventions and adds mental overhead). This applies to message fields, tool input/result fields, DHF JSONB payloads, and Anthropic tool schemas.

### 2.2 Enum Serialization

All enums serialize as **PascalCase strings** via `JsonStringEnumConverter` registered in `JsonOptions.Default`. Rationale: enum values appear in system prompt examples shown to Opus. PascalCase matches C# enum member names, which makes system prompt examples directly readable and avoids a second casing transformation for reviewers.

Example: `ExtrudeDirection.Forward` serializes to `"Forward"`, not `"forward"` or `0`. `UndoGroupOutcome.RolledBack` serializes to `"RolledBack"`.

Integer enum serialization is forbidden. `JsonEnumSerializationTests` fails the build if any enum round-trips as an integer.

### 2.3 Null Handling

`JsonIgnoreCondition.WhenWritingNull` for optional fields. Required fields that are null are validation errors, not serialization errors.

### 2.4 Polymorphic Discriminators

Every polymorphic family uses an explicit discriminator property:

- `IToolInput` and `IToolResult`: discriminator property `"tool"` with value equal to the tool name (snake_case tool names like `"new_part"`, `"start_sketch"` — this is the one place where snake_case survives, because the tool name IS the Anthropic API tool identifier)
- `IClientMessage`: discriminator property `"type"` with values like `"tool_call"`, `"cancel_tool_call"`, `"start_undo_group"`
- `IAddinMessage`: discriminator property `"type"` with values like `"tool_result"`, `"state_update"`, `"addin_ready"`, `"addin_teardown"`
- `PlaneRef`: discriminator `"kind"` with values `"standard"` or `"entity"`
- `DimensionTarget`: discriminator `"kind"` with values `"sketch"` or `"persistent"`
- `SelectionRef`: discriminator `"kind"` with values `"persistent"` or `"sketch"`
- `FeatureParameter`: discriminator `"kind"` with values `"string"`, `"number"`, `"boolean"`, `"ref"`

## 3. Wire Framing

One complete JSON document per WebSocket **message** (not per frame). The application-level receive loop MUST call `ReceiveAsync` repeatedly, accumulating returned segments into a buffer, until `WebSocketReceiveResult.EndOfMessage` is `true`. Only then is the accumulated buffer a complete message ready for deserialization. A single logical message may span multiple `ReceiveAsync` calls when the message size exceeds the receive buffer. No NDJSON.

**Maximum single-message size: 4 MiB.** This limit applies to the fully reassembled message.

**Oversize message handling:** If the accumulated buffer exceeds 4 MiB before `EndOfMessage` is received, the receiver MUST:

1. Stop buffering additional data.
2. Continue calling `ReceiveAsync` and discarding the returned bytes until `EndOfMessage` is `true` (drain-and-discard), to maintain WebSocket stream synchronization.
3. Send a protocol error message (`ClientProtocolErrorCode.MessageTooLarge` or `AddinProtocolErrorCode.MessageTooLarge`).
4. Leave the WebSocket connection open; subsequent messages proceed normally.

Messages that are fully reassembled at or under 4 MiB are deserialized normally. Messages fully reassembled above 4 MiB (possible if the receiver's buffer is ≥ 4 MiB) MUST be rejected without deserialization, with the same protocol error.

**Malformed JSON handling (messages under 4 MiB that fail to parse):** When a fully received message is under the size cap but `JsonSerializer.Deserialize` throws (or the polymorphic discriminator is unrecognized, missing, or produces an unresolvable concrete type), the receiver MUST:

1. Log the parse failure with the raw UTF-8 byte length and the first 512 bytes of the payload (for diagnostics).
2. Send a protocol error message with `ErrorCode = MalformedEnvelope`: the orchestrator sends `ClientProtocolErrorMessage`, the add-in sends `AddinProtocolErrorMessage`.
3. Set `RejectedMessageId` to `null` when the parse failure prevented extraction of the `messageId` field; otherwise echo it.
4. Leave the WebSocket connection open. Subsequent messages proceed normally. A single malformed message does NOT terminate the session.
5. Do NOT attempt partial recovery (e.g., "re-parse with a different discriminator"). Malformed is malformed; reject the whole envelope.

Repeated malformed messages from the same peer within a short window (>5 within 10 seconds) MAY trigger a session close with WebSocket close code 1002 ("protocol error"), at the receiver's discretion. This is a DOS mitigation, not a correctness rule.

No chunked-transfer or streaming protocol is defined in this version. For Demo 1 / hackathon scope, models are expected to remain well under the 4 MiB limit. If the embedded `FeatureTree` in a `StateSnapshot` would cause the enclosing message to exceed the limit, the add-in MUST truncate the tree and set `IsFeatureTreeTruncated = true` (see Section 8). The orchestrator MAY then call `get_feature_tree` as a separate tool invocation to retrieve the complete tree in a dedicated message. A full pagination mechanism for very large feature trees is deferred to a future protocol version.

Screenshots are transported inline as base64 (v1) — see §7.1.

## 4. Polymorphic Families

### 4.1 IClientMessage (Orchestrator → Add-in)

```csharp
[JsonPolymorphic(TypeDiscriminatorPropertyName = "type")]
[JsonDerivedType(typeof(ToolCallMessage),              "tool_call")]
[JsonDerivedType(typeof(CancelToolCallMessage),        "cancel_tool_call")]
[JsonDerivedType(typeof(StartUndoGroupMessage),        "start_undo_group")]
[JsonDerivedType(typeof(CommitUndoGroupMessage),       "commit_undo_group")]
[JsonDerivedType(typeof(RollbackUndoGroupMessage),     "rollback_undo_group")]
[JsonDerivedType(typeof(ClientProtocolErrorMessage),   "client_protocol_error")]
public interface IClientMessage
{
    string MessageId { get; }            // Idempotency key for this message
    string AddinSessionId { get; }       // Epoch marker; rejected if stale
}
```

### 4.2 IAddinMessage (Add-in → Orchestrator)

```csharp
[JsonPolymorphic(TypeDiscriminatorPropertyName = "type")]
[JsonDerivedType(typeof(ToolResultMessage),            "tool_result")]
[JsonDerivedType(typeof(StateUpdateMessage),           "state_update")]
[JsonDerivedType(typeof(AddinReadyMessage),            "addin_ready")]
[JsonDerivedType(typeof(AddinTeardownMessage),         "addin_teardown")]
[JsonDerivedType(typeof(UndoGroupOutcomeMessage),      "undo_group_outcome")]
[JsonDerivedType(typeof(AddinProtocolErrorMessage),    "addin_protocol_error")]
public interface IAddinMessage
{
    string MessageId { get; }
    string AddinSessionId { get; }       // Authoritative; nested duplicates forbidden
}
```

### 4.3 IToolInput and IToolResult

Both use `TypeDiscriminatorPropertyName = "tool"` with the tool's snake_case name as the discriminator value. Every mandatory and stretch tool has exactly one `XxxInput : IToolInput` and one `XxxResult : IToolResult` registered via `[JsonDerivedType]`.

Registration completeness is enforced by `JsonDerivedTypeRegistrationCompletenessTests`: reflects all concrete implementations of each polymorphic interface in the Protocol assembly and fails the build if any concrete type lacks a `[JsonDerivedType]` attribute on the base.

## 5. Message Record Definitions

All records are immutable C# 10+ records with `required` modifier on non-nullable fields. `MessageId` is a UUIDv7 string for time-sorted uniqueness. `AddinSessionId` is the current epoch (see Section 9).

```csharp
public record ToolCallMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string CallId { get; init; }            // Lifecycle identity (see SPEC-03)
    public required string UndoGroupId { get; init; }       // Correlates to undo group
    public required IToolInput Input { get; init; }
}

public record CancelToolCallMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string CallId { get; init; }            // Which ToolCallMessage to cancel
    public required string UndoGroupId { get; init; }
    public required CancelReason Reason { get; init; }
}

public record StartUndoGroupMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string UndoGroupId { get; init; }
    public required string Label { get; init; }             // Shown in SW undo stack
}

public record CommitUndoGroupMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string UndoGroupId { get; init; }
}

public record RollbackUndoGroupMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string UndoGroupId { get; init; }
    public required RollbackReason Reason { get; init; }
}

public record ClientProtocolErrorMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required ClientProtocolErrorCode ErrorCode { get; init; }
    public required string ErrorText { get; init; }
    public string? ExpectedProtocolVersion { get; init; }
    public string? ReceivedProtocolVersion { get; init; }
    public string? RejectedMessageId { get; init; }
}
```

**ClientProtocolErrorMessage AddinSessionId rule:** When the orchestrator sends `ClientProtocolErrorMessage` in response to a received add-in message (including during handshake, before an epoch is established), it MUST set `AddinSessionId` to the value from the triggering message's `AddinSessionId` field. When the triggering message is malformed and `AddinSessionId` cannot be extracted, the orchestrator uses a reserved sentinel value `00000000000000000000000000000000` (32 zero hex chars) to maintain format validity while signaling "no meaningful epoch."

```csharp
public record ToolResultMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string CallId { get; init; }            // Matches ToolCallMessage.CallId
    public required string UndoGroupId { get; init; }
    public required bool Success { get; init; }
    public IToolResult? Result { get; init; }
    public ToolError? Error { get; init; }
    public required StateSnapshot StateAfter { get; init; }
    public required TimingReport Timing { get; init; }
}
```

**ToolResultMessage envelope invariant** (enforced by `MessageEnvelopeInvariantTests` and `ToolResultMessageValidator`):

- When `Success == true`: `Result` MUST be non-null and `Error` MUST be null.
- When `Success == false`: `Result` MUST be null and `Error` MUST be non-null.
- Any other combination is a validation error. The receiver MUST reject the invalid envelope with a protocol error message carrying `MalformedEnvelope` as the error code (using the appropriate directional message type: `ClientProtocolErrorMessage` when the orchestrator rejects an add-in message, `AddinProtocolErrorMessage` when the add-in rejects an orchestrator message).

```csharp
public record StateUpdateMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required StateUpdateTrigger Trigger { get; init; }
    public required StateSnapshot State { get; init; }
    public string? CorrelatedCallId { get; init; }          // Set when Trigger = ToolCompletion
}

public record AddinReadyMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string AddinVersion { get; init; }
    public required string SwVersion { get; init; }
    public required string SwBuild { get; init; }
    public required string ProtocolVersion { get; init; }
    public required string ScreenshotRootPath { get; init; }   // Add-in's local screenshot root
    public required string WorkstationId { get; init; }        // Client-supplied tenant hint (see SPEC-04 SDR-04-008)
}
```

**`ScreenshotRootPath`:** the add-in's local filesystem path where it captures PNGs before encoding. The orchestrator does NOT read from this path — the filesystem is on the add-in's workstation, not on Railway. The field exists for two reasons: (a) audit trail completeness (DHF records the add-in's capture context), (b) v2 forward-compatibility (when screenshots move to S3 presigned URLs, the add-in will continue to stage PNGs at `ScreenshotRootPath` locally before uploading; the field reminds both sides that a local staging area exists).

**`WorkstationId`:** `Environment.MachineName` on the add-in's Windows workstation. In v1, this is a client-supplied tenant hint used by SPEC-04 for `dhf.sessions.workstation_id` and the cross-workstation conflict check (SDR-04-008). It is NOT a security trust boundary — trust flows from the API key authenticating the WebSocket, which scopes operations to a tenant. A spoofed `WorkstationId` within a tenant cannot escalate across tenants.

```csharp
public record AddinTeardownMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required TeardownReason Reason { get; init; }
}

public record UndoGroupOutcomeMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string UndoGroupId { get; init; }
    public required UndoGroupOutcome Outcome { get; init; } // Committed | RolledBack | Abandoned
    public string? FailureReason { get; init; }
}

public record AddinProtocolErrorMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required AddinProtocolErrorCode ErrorCode { get; init; }
    public required string ErrorText { get; init; }
    public string? RejectedMessageId { get; init; }
}

public enum StateUpdateTrigger
{
    InitialState,          // Post-handshake full-state resync (mandatory after AddinReady)
    ToolCompletion,        // Paired with ToolResultMessage
    SelectionChanged,
    DocumentOpened,
    DocumentClosed,
    RebuildComplete,
    ExternalChange         // User modified model outside BSVoice-CAD
}

public enum UndoGroupOutcome
{
    Committed,
    RolledBack,
    Abandoned              // Transport loss or add-in crash; rollback best-effort
}

public enum TeardownReason
{
    UserDisabledAddin,     // User disabled add-in via SW Add-in Manager
    SolidWorksShutdown,    // SolidWorks process is exiting
    AddinError             // Unrecoverable add-in error
}
```

**`AddinTeardownMessage`:** Sent by the add-in during `DisconnectFromSW` (SPEC-02 §9.1) before the WebSocket is closed. This is the sole mechanism for triggering `explicit_shutdown` DHF session closure (SPEC-00 §6 glossary). The orchestrator closes the DHF session on receipt. If the add-in crashes or the network drops before the message can be sent, the session remains open and eventually closes via `heartbeat_timeout`.

## 6. Common Types

### 6.1 ToolError

```csharp
public record ToolError
{
    public required ToolErrorCode Code { get; init; }
    public required string Message { get; init; }
    public string? SwHresult { get; init; }
    public bool Recoverable { get; init; }
    public string? SuggestedAction { get; init; }
    public IReadOnlyDictionary<string, string>? Context { get; init; }
}

public enum ToolErrorCode
{
    // Precondition
    NoActiveDocument, WrongDocumentType, InvalidSketchState,
    EntityNotFound, UnitAmbiguous, InvalidUndoState, TemplateNotFound,

    // Runtime
    TimedOut, Cancelled, InternalError, ComException, ValidationFailed,

    // Protocol
    InFlightDuplicate, UnknownCallId, VersionMismatch, UnknownMessageType, StaleEpoch,

    // Business rule
    ConfirmationDenied, RetryLimitExceeded
}
```

**`InvalidUndoState`:** produced when a tool call arrives that requires an active undo group (execution tools) but no `StartUndoGroupMessage` has been received, or arrives when an undo group is already committed/rolled-back. SPEC-02 §5 maps this case.

**`TemplateNotFound`:** produced by `new_part` when the requested template name does not match any registered SolidWorks part template on the add-in's machine. SPEC-02 §5 maps this case.

### 6.2 TimingReport

```csharp
public record TimingReport
{
    public int QueueWaitMs { get; init; }    // Time between add-in receipt and STA dequeue
    public int ExecuteMs { get; init; }      // Time between STA dequeue and SW API return
    public int? RebuildMs { get; init; }     // Rebuild time if the op triggered one
    public int TotalMs { get; init; }        // End-to-end add-in-side wall-clock
}
```

### 6.3 ToolCallLifecycleState

```csharp
public enum ToolCallLifecycleState
{
    Sent,             // Dispatched to add-in; no terminal yet
    Completed,        // Terminal: tool returned a result
    TimedOut,         // Terminal: orchestrator-side timeout fired
    Cancelled,        // Terminal: cancel message acknowledged before result
    Abandoned         // Terminal: session closed or epoch rotated before result
}
```

Consumed by SPEC-03 (state machine transitions) and SPEC-04 (`dhf.tool_calls.lifecycle_state` storage column). The `Sent` state is not persisted to DHF — only terminal states produce rows. DHF serialization uses canonical storage strings (`completed`, `timed_out`, `cancelled`, `abandoned`) via `DhfEnumMappings.ToStorage()` in SPEC-04 §4.5.

### 6.4 Shared Enums

```csharp
public enum UnitSystem { Mm, Inch }

public enum DocumentType { Part, Assembly, Drawing }

public enum ExtrudeDirection { Forward, Reverse, Both, Midplane }

public enum StandardPlane { Front, Top, Right }

public enum DimensionType { Length, Radius, Diameter, Angle }

public enum DimensionMode
{
    LinearSingleEntity,
    LinearTwoEntity,
    RadialSingleEntity,
    DiametralSingleEntity,
    AngularTwoEntity        // Stretch; not required for Demo 1
}

public enum EntityKind
{
    Face, Edge, Vertex, Feature, Sketch, Plane, SketchEntity
}

// --- Message-level enums ---

public enum CancelReason
{
    UserAbort,
    Timeout,
    AgentAbort
}

public enum RollbackReason
{
    Timeout,
    UserCancel,
    Error
}

public enum ClientProtocolErrorCode
{
    VersionMismatch,
    MessageTooLarge,
    UnknownMessageType,
    MalformedEnvelope,     // Orchestrator detected malformed add-in message
    StaleEpoch,            // Orchestrator detected stale AddinSessionId from add-in
    AuthenticationFailed   // v1: API key rejected at handshake; see §12
}

public enum AddinProtocolErrorCode
{
    InFlightDuplicate,
    UnknownCallId,
    UnknownMessageType,
    StaleEpoch,            // Add-in detected stale AddinSessionId from orchestrator
    MessageTooLarge,
    MalformedEnvelope      // Add-in detected malformed orchestrator message
}
```

### 6.5 EntityRef and SketchEntityId

```csharp
// Persistent cross-document-session reference
public record EntityRef
{
    public required EntityKind Kind { get; init; }
    public required string PersistentId { get; init; }    // SW persistent reference string
    public required string DisplayLabel { get; init; }    // e.g., "Face<1>@Boss-Extrude1"
    public required string DocumentId { get; init; }
}

// Transient, valid only within the active sketch session
public record SketchEntityId
{
    public required string LocalId { get; init; }         // Unique within the sketch
    public required string SketchId { get; init; }        // Parent sketch context
}
```

### 6.6 PlaneRef, DimensionTarget, and SelectionRef (Discriminated Unions)

```csharp
[JsonPolymorphic(TypeDiscriminatorPropertyName = "kind")]
[JsonDerivedType(typeof(StandardPlaneRef), "standard")]
[JsonDerivedType(typeof(EntityPlaneRef),   "entity")]
public abstract record PlaneRef;

public record StandardPlaneRef(StandardPlane Plane) : PlaneRef;
public record EntityPlaneRef(EntityRef Reference) : PlaneRef;

// Dimension target can be transient (active sketch) or persistent (post-exit).
[JsonPolymorphic(TypeDiscriminatorPropertyName = "kind")]
[JsonDerivedType(typeof(SketchDimensionTarget),      "sketch")]
[JsonDerivedType(typeof(PersistentDimensionTarget),  "persistent")]
public abstract record DimensionTarget;

public record SketchDimensionTarget(
    SketchEntityId Primary,
    SketchEntityId? Secondary) : DimensionTarget;

public record PersistentDimensionTarget(
    EntityRef Primary,
    EntityRef? Secondary) : DimensionTarget;

// Selection reference — supports both persistent entities and transient sketch entities.
[JsonPolymorphic(TypeDiscriminatorPropertyName = "kind")]
[JsonDerivedType(typeof(PersistentSelectionRef), "persistent")]
[JsonDerivedType(typeof(SketchSelectionRef),     "sketch")]
public abstract record SelectionRef;

public record PersistentSelectionRef : SelectionRef
{
    public required EntityRef Entity { get; init; }
}

public record SketchSelectionRef : SelectionRef
{
    public required SketchEntityId SketchEntity { get; init; }
}
```

## 7. Tool Input and Result Records

### 7.1 Mandatory Tools

```csharp
public record NewPartInput : IToolInput
{
    public string Template { get; init; } = "default_mm";
    public string? DocumentName { get; init; }
}

public record NewPartResult : IToolResult
{
    public required string DocumentId { get; init; }
    public required string DocumentName { get; init; }
    public required UnitSystem Units { get; init; }
}

public record StartSketchInput : IToolInput
{
    public required PlaneRef Plane { get; init; }
}

public record StartSketchResult : IToolResult
{
    public required string SketchId { get; init; }
    public required string PlaneUsed { get; init; }
}

public record DrawRectangleInput : IToolInput
{
    public required double X1 { get; init; }
    public required double Y1 { get; init; }
    public required double X2 { get; init; }
    public required double Y2 { get; init; }
    public bool CenteredOnOrigin { get; init; }
}

public record DrawRectangleResult : IToolResult
{
    public required IReadOnlyList<SketchEntityId> EdgeIds { get; init; }    // 4 edges
    public required IReadOnlyList<SketchEntityId> VertexIds { get; init; }  // 4 corners
    public required string SketchId { get; init; }
}

public record DrawCircleInput : IToolInput
{
    public required double CenterX { get; init; }
    public required double CenterY { get; init; }
    public required double Radius { get; init; }
    public bool CenteredOnOrigin { get; init; }
}

public record DrawCircleResult : IToolResult
{
    public required SketchEntityId EdgeId { get; init; }
    public required SketchEntityId CenterVertexId { get; init; }
    public required string SketchId { get; init; }
}

public record AddDimensionInput : IToolInput
{
    public required DimensionMode Mode { get; init; }
    public required DimensionTarget Target { get; init; }   // Polymorphic: sketch or persistent
    public required double Value { get; init; }
    public required DimensionType Type { get; init; }
}

public record AddDimensionResult : IToolResult
{
    public required string DimensionId { get; init; }
    public required double AppliedValue { get; init; }
    public bool OverDefinedSketch { get; init; }
}

public record ExitSketchInput : IToolInput;

public record ExitSketchResult : IToolResult
{
    public required string ExitedSketchId { get; init; }
}

public record ExtrudeInput : IToolInput
{
    public required string SketchId { get; init; }
    public required double Depth { get; init; }
    public ExtrudeDirection Direction { get; init; } = ExtrudeDirection.Forward;
    public bool MergeResult { get; init; } = true;
}

public record ExtrudeResult : IToolResult
{
    public required string FeatureId { get; init; }
    public required string FeatureName { get; init; }
}

public record ScreenshotInput : IToolInput
{
    public int WidthPx { get; init; } = 1024;
    public int HeightPx { get; init; } = 768;
}

public record ScreenshotResult : IToolResult
{
    // Exactly one of ScreenshotBase64 or ScreenshotUrl MUST be non-null (envelope invariant).
    // v1: ScreenshotBase64 populated, ScreenshotUrl null.
    // v2: ScreenshotUrl populated (S3 presigned GET URL, 5-minute expiry), ScreenshotBase64 null.

    public string? ScreenshotBase64 { get; init; }      // v1; base64-encoded PNG, max 2 MiB post-encode
    public string? ScreenshotUrl { get; init; }          // v2; presigned S3 GET URL
    public required string Sha256Hash { get; init; }     // Hex, lowercase; computed over raw PNG bytes
    public required int WidthPx { get; init; }
    public required int HeightPx { get; init; }
}
```

**Screenshot transport (v1):** The add-in captures the SolidWorks viewport as a PNG in memory, computes SHA-256 over the raw bytes, base64-encodes, and populates `ScreenshotBase64`. The encoded length MUST NOT exceed 2 MiB. If the captured PNG would exceed 2 MiB post-encode, the add-in MUST progressively reduce capture dimensions (e.g., halve `WidthPx` and `HeightPx` iteratively) until the encoded size fits, and return the reduced-dimension result with updated `WidthPx` / `HeightPx`. The 2 MiB screenshot cap leaves approximately 2 MiB of headroom within the 4 MiB message envelope for the rest of the `ToolResultMessage` (most importantly `StateAfter.FeatureTree`).

**Screenshot transport (v2):** `ScreenshotResult` carries `ScreenshotUrl` (S3 presigned GET URL, 5-minute expiry) instead of base64. The add-in uploads the PNG to S3 via a presigned PUT URL obtained from an orchestrator HTTPS endpoint. See `DEPLOYMENT_V1_V2_MIGRATION.md` §3 for the full transport migration.

**Envelope invariant** (enforced by `ScreenshotResultValidator`): exactly one of `ScreenshotBase64` or `ScreenshotUrl` MUST be non-null. Both null, or both non-null, is a validation error producing `MalformedEnvelope`. Consumers (SPEC-05 agent loop for Anthropic vision API, SPEC-04 DHF writer, SPEC-06 viewer) branch on which field is populated; the wire contract is stable across v1 and v2 so `ProtocolVersion` does not change.

**`Sha256Hash`:** computed over the raw PNG bytes, not over the base64 encoding and not over the URL. Identical in v1 and v2 for a given capture. Stored authoritatively in DHF (`dhf.entries.screenshot_sha256`) as the integrity anchor for audit trail replay.

```csharp
public record GetFeatureTreeInput : IToolInput
{
    public bool IncludeSuppressed { get; init; } = false;
}

public record GetFeatureTreeResult : IToolResult
{
    public required IReadOnlyList<FeatureNode> Tree { get; init; }
    public required string TreeHash { get; init; }
}
```

### 7.2 Stretch Tools

`CutExtrudeInput/Result`, `FilletInput/Result`, `ChamferInput/Result`, `HoleWizardInput/Result`, `LinearPatternInput/Result`, `CircularPatternInput/Result`, `GetSelectionInput/Result`, `RaycastViewportCenterInput/Result`, `ModifyFeatureParameterInput/Result`. Each follows the same input/result pattern.

**These are empty placeholder records** with `// TODO: STRETCH` markers. Their polymorphic registrations (`[JsonDerivedType]` on `IToolInput`/`IToolResult`) and `[ToolName]` attributes are present from day one so that `ToolRegistry` and completeness tests remain valid. **All stretch tools MUST set `IsImplemented = false` on their `[ToolName]` attribute** (see Section 11). Schema generation and tool catalog exposure MUST exclude tools where `IsImplemented == false`. Field definitions are deferred to stretch implementation. Any future field addition to a stretch tool record constitutes a wire-contract change and MUST increment `ProtocolVersion`.

## 8. State Snapshot

```csharp
public record StateSnapshot
{
    public required long StateVersion { get; init; }      // Monotonic within AddinSessionId
    public required DateTimeOffset StateTimestamp { get; init; }
    public string? DocumentId { get; init; }
    public string? DocumentName { get; init; }
    public DocumentType? DocumentType { get; init; }
    public bool IsSketchActive { get; init; }
    public string? ActiveSketchId { get; init; }
    public required IReadOnlyList<SelectionRef> ActiveSelection { get; init; }
    public required IReadOnlyList<FeatureNode> FeatureTree { get; init; }
    public required string FeatureTreeHash { get; init; }  // HashCanonicalizer output; "NO_DOCUMENT" sentinel valid
    public bool IsFeatureTreeTruncated { get; init; }      // True when tree was cut to fit message size
    public UnitSystem? Units { get; init; }                // Null when IsNullDocumentState is true
    public required bool IsNullDocumentState { get; init; }
}
```

**Null-document rules:** When `IsNullDocumentState` is `true`, ALL of the following MUST hold:

- `Units` MUST be `null`
- `DocumentId` MUST be `null`
- `DocumentName` MUST be `null`
- `DocumentType` MUST be `null`
- `FeatureTree` MUST be empty
- `FeatureTreeHash` MUST be `"NO_DOCUMENT"`
- `IsFeatureTreeTruncated` MUST be `false`
- `IsSketchActive` MUST be `false`
- `ActiveSketchId` MUST be `null`
- `ActiveSelection` MUST be empty

**Live-document rules:** When `IsNullDocumentState` is `false`, ALL of the following MUST hold:

- `Units` MUST be non-null
- `DocumentId` MUST be non-null
- `DocumentName` MUST be non-null
- `DocumentType` MUST be non-null

**Sketch-state invariant** (applies regardless of `IsNullDocumentState`):

- When `IsSketchActive` is `true`: `ActiveSketchId` MUST be non-null
- When `IsSketchActive` is `false`: `ActiveSketchId` MUST be `null`

**Feature tree truncation:** When the add-in determines that including the full `FeatureTree` would cause the enclosing message to exceed the 4 MiB wire limit (Section 3), it MUST truncate the tree (e.g., include only top-level features) and set `IsFeatureTreeTruncated = true`. The `FeatureTreeHash` MUST still reflect the hash of the **complete** (non-truncated) tree so that change detection remains accurate. When `IsFeatureTreeTruncated` is `true`, the orchestrator SHOULD call `get_feature_tree` to retrieve the complete tree in a separate message.

```csharp
public record FeatureNode
{
    public required string FeatureId { get; init; }
    public required string FeatureName { get; init; }
    public required string FeatureType { get; init; }
    public required bool Suppressed { get; init; }
    public string? ParentFeatureId { get; init; }
    public required IReadOnlyList<FeatureParameter> Parameters { get; init; }
}

[JsonPolymorphic(TypeDiscriminatorPropertyName = "kind")]
[JsonDerivedType(typeof(FeatureParameterString),  "string")]
[JsonDerivedType(typeof(FeatureParameterNumber),  "number")]
[JsonDerivedType(typeof(FeatureParameterBoolean), "boolean")]
[JsonDerivedType(typeof(FeatureParameterRef),     "ref")]
public abstract record FeatureParameter
{
    public required string Name { get; init; }
}

public record FeatureParameterString : FeatureParameter
{
    public required string Value { get; init; }
}

public record FeatureParameterNumber : FeatureParameter
{
    public required decimal Value { get; init; }
    public required string Unit { get; init; }
}

public record FeatureParameterBoolean : FeatureParameter
{
    public required bool Value { get; init; }
}

public record FeatureParameterRef : FeatureParameter
{
    public required string EntityId { get; init; }
}
```

`FeatureParameterNumber` uses `decimal` to eliminate floating-point nondeterminism in hashing. See Section 14 for the canonical decimal format.

## 9. AddinSessionId Format and Epoch Semantics

SPEC-01 is authoritative for the **format contract** and the **epoch semantics** of `AddinSessionId`. The **derivation algorithm** (UUID version, hash function, input fields, fallback behavior) is authoritative in SPEC-02 §8. This split is intentional: SPEC-04 consumes epoch semantics without taking a dependency on SPEC-02 add-in internals.

**Format:** `AddinSessionId` is exactly 32 lowercase hexadecimal characters matching the regex `^[0-9a-f]{32}$`. `AddinSessionIdFormatValidator` (see Section 15) enforces this format. Any message carrying an `AddinSessionId` that fails format validation MUST be rejected with `MalformedEnvelope`.

**Uniqueness expectation:** The format does not itself guarantee uniqueness across the population of add-in instances — that is the responsibility of the SPEC-02 generation algorithm. SPEC-01 consumers assume that any two `AddinSessionId` values observed within a single tenant's sessions are either literally equal (same add-in instance, possibly reconnecting) or refer to distinct add-in loads.

**Collision handling (same AddinSessionId, reconnect from same workstation):** If a new `AddinReadyMessage` arrives with the same `AddinSessionId` as the currently established epoch, the orchestrator MUST treat it as a reconnect within the same session: re-run version validation, accept the updated capability fields (`AddinVersion`, `SwVersion`, `SwBuild`, `ScreenshotRootPath`), but NOT reset `StateVersion` to 0. The epoch identity is unchanged.

**Cross-workstation collision:** If a new `AddinReadyMessage` arrives with an `AddinSessionId` that matches an already-open DHF session on a different `WorkstationId` (within the same tenant), this is a hard error. SPEC-04 SDR-04-008 specifies the `CrossWorkstationSessionConflictException` behavior; the orchestrator MUST NOT silently merge or reassign the session.

**Epoch rules (enforced by consumers in SPEC-03 and SPEC-04):**

- Every `IClientMessage` and `IAddinMessage` carries exactly one `AddinSessionId` at the top-level record. Nested duplicates inside `StateSnapshot` or other records are forbidden.
- **`AddinReadyMessage` and `AddinTeardownMessage` are exempt from epoch validation.** `AddinReadyMessage` is the sole mechanism for establishing or changing the current epoch. `AddinTeardownMessage` is sent during shutdown and must be processable even if the epoch is transitioning.
- **Initial connection:** Before the first `AddinReadyMessage` is received, no epoch exists. The orchestrator MUST accept the first `AddinReadyMessage` (post-TLS, post-API-key-validation per §12) and record its `AddinSessionId` as the current epoch. All non-exempt messages received before the first epoch is established MUST be rejected with `ClientProtocolErrorMessage(ErrorCode: StaleEpoch)`, with `AddinSessionId` echoed from the rejected message (see Section 5).
- **Steady state:** After an epoch is established, any non-exempt message from the add-in with an `AddinSessionId` other than the current epoch is rejected with `ClientProtocolErrorMessage(ErrorCode: StaleEpoch)`. Any `IClientMessage` received by the add-in with a stale `AddinSessionId` is rejected with `AddinProtocolErrorMessage(ErrorCode: StaleEpoch)`.
- **Epoch transition:** When a subsequent `AddinReadyMessage` arrives with a different `AddinSessionId`, the orchestrator atomically transitions to the new epoch and resets its view of `StateVersion` to 0.

## 10. Protocol Versioning

`ProtocolVersion.Current` is a const string. Initial value: `"1.0.0"`. Semantic versioning semantics but pre-1.0 is not permitted for this project.

**Handshake rule:**

- Add-in sends `AddinReadyMessage` with `ProtocolVersion = Current`
- Orchestrator compares to its own `Current`
- Exact string match required. Mismatch → orchestrator sends a version-mismatch error (see below), then closes the WebSocket.
- No negotiation, no downgrade, no "minor version compatible." This is hackathon scope; negotiation is post-hackathon.

**ProtocolVersion does NOT bump between v1 and v2 deployments.** The wire contract (message shapes, discriminators, envelope invariants) is stable across the deployment transition by design. Only changes enumerated in `DEPLOYMENT_V1_V2_MIGRATION.md` §8 "Version-Bump Triggers" require a bump.

**Stable version-mismatch wire format:** Because a version mismatch implies the two sides may use incompatible polymorphic schemas, the version-mismatch error MUST be deserializable without relying on the polymorphic `IClientMessage` pipeline. The following flat JSON schema is **frozen across all protocol versions** — both sides MUST be able to parse it by reading raw JSON properties regardless of their protocol version:

```json
{
  "type": "client_protocol_error",
  "messageId": "<UUIDv7>",
  "addinSessionId": "<echoed from AddinReadyMessage>",
  "errorCode": "VersionMismatch",
  "errorText": "<human-readable diagnostic>",
  "expectedProtocolVersion": "<orchestrator's ProtocolVersion.Current>",
  "receivedProtocolVersion": "<add-in's reported ProtocolVersion>"
}
```

Receivers that encounter an unrecognized `"type"` discriminator value SHOULD attempt raw JSON property extraction before failing deserialization. This guarantees the version-mismatch diagnostic is always readable even when all other message types have diverged.

## 11. Tool Registry and Schema Generation

```csharp
[AttributeUsage(AttributeTargets.Class)]
public sealed class ToolNameAttribute : Attribute
{
    public string Name { get; }
    public bool IsImplemented { get; set; } = true;
    public ToolNameAttribute(string name) { Name = name; }
}

public static class ToolRegistry
{
    public static IReadOnlyList<ToolMetadata> All { get; }
    public static IReadOnlyList<ToolMetadata> Implemented { get; }   // All where IsImplemented == true
    public static ToolMetadata? GetByName(string toolName);
    public static Type? InputType(string toolName);
    public static Type? ResultType(string toolName);
}

public record ToolMetadata(
    string Name,
    Type InputType,
    Type ResultType,
    TimeSpan Timeout,
    bool IsImplemented);
```

`ToolRegistry.All` reflects the Protocol assembly at static construction time, finding every type with `[ToolName]` attributes that implement `IToolInput` or `IToolResult`. `ToolRegistry.Implemented` filters to only those with `IsImplemented == true`.

**Single source of truth for tool names:** The `[ToolName]` attribute is the authoritative identity for every tool. The `[JsonDerivedType]` discriminator value on `IToolInput` and `IToolResult`, the `ToolTimeouts` table key, and the Anthropic schema name MUST all exactly match the `[ToolName]` value. `ToolNameConsistencyTests` (build-breaking) verifies this for every registered tool.

**Uniqueness invariant:** Each tool name maps to exactly one input type and exactly one result type. `ToolNameUniquenessTests` (build-breaking) fails if multiple input or result types share the same `[ToolName]` value.

**Tool exposure rule:** Schema generation (`JsonSchemaGenerator`, `AnthropicToolSpec`) and any consumer that builds a tool catalog for external exposure (e.g., the Anthropic tool list in SPEC-05) MUST use `ToolRegistry.Implemented`, not `ToolRegistry.All`. Unimplemented tools MUST NOT appear in generated schemas or be advertised as callable. `ToolExposureExclusionTests` (build-breaking) verifies that no unimplemented tool appears in generated Anthropic tool specs.

`ToolRegistryCompletenessTests` asserts every registered input has a matching result and vice versa.

## 12. Transport — TLS and API-Key Authentication Handshake (v1)

The WebSocket between add-in and orchestrator crosses the public internet. This section specifies the v1 authentication and TLS contract. For v2 evolution (OAuth 2.0 device flow), see `DEPLOYMENT_V1_V2_MIGRATION.md` §2.

### 12.1 URL and TLS

- **WebSocket URL (v1):** `wss://vcad.boundstate.ai/ws`
- **TLS termination:** at the Railway edge. Certificates are Railway-managed (Let's Encrypt). The orchestrator container sees plain WS internally; the contract described to the add-in is `wss://`.
- **Minimum TLS version:** TLS 1.2. Add-in connections over TLS 1.1 or lower are refused at the edge; no orchestrator-side handling required.
- **Certificate pinning:** not required for v1 (Railway edge certs rotate). Add-in validates the server cert via standard Windows certificate store trust chain.

### 12.2 API-Key Credential Model

The orchestrator maintains an `api_keys` table in Postgres:

```sql
CREATE TABLE auth.api_keys (
    key_id          TEXT PRIMARY KEY,           -- opaque 16-char alphanumeric
    secret_hash     TEXT NOT NULL,              -- Argon2id of raw secret
    tenant_id       TEXT NOT NULL,              -- scope for sessions and DHF rows
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at      TIMESTAMPTZ NULL,
    last_used_at    TIMESTAMPTZ NULL,
    display_name    TEXT NOT NULL,              -- "Joe's laptop", "Demo key", etc.
    comment         TEXT NULL
);

CREATE INDEX idx_api_keys_tenant ON auth.api_keys (tenant_id) WHERE revoked_at IS NULL;
```

**Key issuance (out of band):** API keys are provisioned by an operator via a CLI tool (`dhf-admin issue-key`, post-hackathon) or directly via SQL insert (hackathon scope). Secrets are never stored plaintext in the DB; Argon2id hashing parameters are `t=3, m=64MiB, p=1`.

**Key format on the wire:** `bsvc_{keyId}_{secret}` where `keyId` is 16 alphanumeric chars and `secret` is 32 alphanumeric chars (approx 190 bits entropy). Add-in stores the full key in Windows Credential Manager under the target `BoundState.BSVoiceCAD.ApiKey`. The user provisions the key into Credential Manager manually or via an installer step.

### 12.3 Handshake Sequence

The WebSocket upgrade request (HTTP/1.1 `GET /ws`) carries two subprotocols in the `Sec-WebSocket-Protocol` header:

```
Sec-WebSocket-Protocol: bsvoice-cad-v1, apikey-{keyId}.{secret}
```

The second value encodes the credential. The orchestrator's upgrade handler:

1. Parses the subprotocol list.
2. Requires the first value to be exactly `bsvoice-cad-v1` (version tag; distinct from `ProtocolVersion` which gates message shapes). If not, responds with HTTP 400 and closes.
3. Parses the second value as `apikey-{keyId}.{secret}`. If the format does not match, responds with HTTP 401 and closes.
4. Looks up `keyId` in `auth.api_keys`. If missing or `revoked_at IS NOT NULL`, responds with HTTP 401 and closes.
5. Verifies `secret` against `secret_hash` using Argon2id constant-time comparison. If mismatch, responds with HTTP 401 and closes.
6. Updates `last_used_at` (best effort; failure to update does not fail the handshake).
7. Completes the WebSocket upgrade, echoing `bsvoice-cad-v1` (only) back in `Sec-WebSocket-Protocol`. The apikey subprotocol is NEVER echoed; echoing would leak the credential into logs.

### 12.4 Handshake Failure Handling

- HTTP 400: format error. Add-in retries with backoff (5s, 30s, 5min) under the assumption that the client library is misbehaving; if repeated, the add-in surfaces a UI error "connection to BSVoice-CAD service failed; reinstall may be required."
- HTTP 401: auth failure (missing key, revoked key, wrong secret). Add-in surfaces a UI error "authentication failed; please verify your BSVoice-CAD API key in Windows Credential Manager." Does NOT retry automatically; retry requires explicit user action.
- WebSocket close code 4401 (custom, "auth failed post-upgrade"): used only if a post-upgrade condition invalidates the session (e.g., key revoked while the connection is open). Add-in treats this identically to HTTP 401.

### 12.5 Per-Connection Identity and Tenant Scoping

After successful handshake, the orchestrator binds the WebSocket connection to the `tenant_id` associated with the validated API key. All messages received on this connection are implicitly scoped to that tenant. SPEC-04 consumes the tenant_id for DHF row scoping (migration 006 adds `dhf.sessions.tenant_id`).

**Cross-tenant isolation:** A WebSocket authenticated with tenant A's API key MUST NOT be able to observe or interact with tenant B's sessions or DHF records. This is enforced at the repository layer (every read and write query filters by `tenant_id`) and verified by `TenantIsolationTests` in `BSVoiceCAD.Tests.Integration`.

### 12.6 Rate Limiting and Abuse Protection

- **Handshake attempts per IP:** Railway edge limits 60/min globally. Orchestrator-level secondary limit per failed `keyId`: 10 failed attempts per hour → temporary key lockout (15 min), logged as an `api_keys.lockout_event` row (schema deferred to post-hackathon).
- **Post-handshake message rate:** no hard rate limit for v1. A tenant that saturates the orchestrator receives `MessageTooLarge` or `MalformedEnvelope` naturally for malformed abuse; a well-formed flood would exhaust Railway service quotas, which Railway surfaces back to the operator.

### 12.7 Connection Lifecycle

- **Idle timeout:** orchestrator closes connections idle (no messages in either direction) for > 10 minutes. Add-in reconnects automatically; reconnect emits a new `AddinReadyMessage` with the same `AddinSessionId`, resuming the DHF session per SPEC-04 SDR-04-005. **Idle-timeout disconnect does NOT close the DHF session** — it remains open pending reconnect or heartbeat timeout.
- **Heartbeat:** WebSocket ping/pong every 30 seconds driven by orchestrator. Missed pong → close with 1006 ("abnormal closure") after 2 missed intervals.
- **Clean shutdown:** either side MAY send WebSocket close frame with code 1000 and a short reason. Orchestrator close does not terminate the DHF session (SPEC-04 SDR-04-005); the session remains open pending add-in reconnect or heartbeat timeout.

### 12.8 v2 Evolution

v2 replaces the API-key subprotocol with an OAuth 2.0 device-flow access token, carried in the same `Sec-WebSocket-Protocol` header position:

```
Sec-WebSocket-Protocol: bsvoice-cad-v1, bearer-{jwt_access_token}
```

Orchestrator detects which mode via env var `DEPLOYMENT_AUTH_MODE`: `api_key` (v1) or `oauth` (v2). The message-level wire contract does not change. `ProtocolVersion` does not bump. See `DEPLOYMENT_V1_V2_MIGRATION.md` §2.

## 13. Per-Tool Timeouts

`ToolTimeouts.ForTool(string toolName)` returns a `TimeSpan`. Values:

| Tool | Timeout |
|------|--------|
| default | 30 s |
| `extrude`, `cut_extrude` | 90 s |
| `fillet`, `chamfer` | 90 s |
| `hole_wizard` | 120 s |
| `linear_pattern`, `circular_pattern` | 180 s |
| `modify_feature_parameter` | 120 s |
| `screenshot` | 15 s |
| `get_feature_tree` | 15 s |

Consumers in SPEC-03 enforce these as the per-call timeout. Changes to this table require an SDR update in SPEC-00.

## 14. Hash Canonicalization

`HashCanonicalizer.ComputeFeatureTreeHash(IReadOnlyList<FeatureNode>)` and `.ComputeRecordHash(object, IReadOnlyList<string>)` produce deterministic SHA-256 hex strings.

**Rules:**

- Input is serialized with a fixed `JsonOptions` instance: camelCase properties, PascalCase enum strings, sorted object keys, null properties omitted.
- The output is a lowercase hex SHA-256 of the UTF-8 encoded canonical JSON.
- The null-document sentinel is the literal string `"NO_DOCUMENT"`, not a hash of anything.

**Decimal format:** Decimals are serialized in invariant culture as plain decimal notation. Scientific notation (the characters `E` and `e`) MUST NOT appear in the output. Trailing fractional zeros are stripped. The reference implementation is `HashCanonicalizer.FormatDecimal(decimal)`, which calls `value.ToString("G29", CultureInfo.InvariantCulture)` and post-processes any scientific-notation output into expanded decimal form. `HashDecimalFormatTests` verifies no scientific notation appears for edge-case values (10^−28, 10^28, 0, decimal.MaxValue, decimal.MinValue).

**Array ordering:** `HashCanonicalizer` normalizes array order internally before computing the hash. **Producers are NOT required to emit arrays in any particular order** — the canonicalizer sorts them deterministically. This ensures that logically equivalent data structures always produce identical hashes regardless of emission order. The sort orders applied by the canonicalizer are:

- `FeatureNode` lists (in `StateSnapshot.FeatureTree` and `GetFeatureTreeResult.Tree`): sorted by `FeatureId`, lexicographic, ordinal, case-sensitive.
- `FeatureParameter` lists (in `FeatureNode.Parameters`): sorted by `Name`, lexicographic, ordinal, case-sensitive.
- `SelectionRef` lists (in `StateSnapshot.ActiveSelection`): `PersistentSelectionRef` entries first (sorted by `Entity.PersistentId`), then `SketchSelectionRef` entries (sorted by `SketchEntity.LocalId`), all ordinal, case-sensitive.

Property-based tests (`HashCanonicalization_PropertyTests` via FsCheck) generate 1000 randomly shaped `FeatureNode` trees and assert that logical equality (including reordered-but-equivalent arrays) implies hash equality.

## 15. Validation

Every `IToolInput` has a matching `AbstractValidator<T>` in `Validation/Validators/`. FluentValidation. Rules:

- Range/Required/Enum/MinLength/MaxLength rules translate to JSON Schema constraints via `JsonSchemaGenerator`.
- Cross-field conditional rules (e.g., "Secondary required when Mode = LinearTwoEntity") are implemented in the validator but do NOT translate to JSON Schema constraints. They appear as `description` text only.

**Message-envelope validation:** In addition to tool-input validators, the following message types have envelope validators:

- `ToolResultMessageValidator`: enforces the Success/Result/Error invariant (see Section 5).
- `StateSnapshotValidator`: enforces all three invariant groups (see Section 8) — null-document rules (10 constraints when `IsNullDocumentState` is true), live-document rules (4 required-non-null fields when `IsNullDocumentState` is false), and the sketch-state invariant (`ActiveSketchId` required iff `IsSketchActive` is true).
- `ScreenshotResultValidator`: enforces the `ScreenshotBase64` / `ScreenshotUrl` envelope invariant (exactly one non-null) and the 2 MiB post-encode size cap on `ScreenshotBase64` (v1).
- `AddinSessionIdFormatValidator`: enforces the 32 lowercase hex character format (regex `^[0-9a-f]{32}$`) on `AddinSessionId` fields in both `IClientMessage` and `IAddinMessage`.

Two tests enforce the schema sync split:

- `ValidationSchemaSyncTests` (build-breaking): every mechanical validator rule has a matching JSON Schema constraint.
- `SemanticHintCoverageTests` (`[Trait("Category", "Advisory")]`, non-breaking): cross-field rules appear in schema descriptions.

Advisory tests run in a separate CI step with `continue-on-error: true` so they report without blocking.

## 16. Test Requirements (Build-Breaking)

Required tests in `BSVoiceCAD.Protocol.Tests`. Failure of any of these fails CI.

- `SerializationTests`: every record round-trips byte-identical
- `PolymorphicDeserializationTests`: every `[JsonDerivedType]` round-trips through the base interface
- `JsonDerivedTypeRegistrationCompletenessTests`: every concrete implementation of each polymorphic interface is registered
- `JsonNamingPolicyConsistencyTests`: every serialized property name is camelCase
- `JsonEnumSerializationTests`: every enum serializes as its PascalCase member name (never integer)
- `ToolRegistryCompletenessTests`: every tool has paired Input + Result + Timeout
- `ToolNameConsistencyTests`: `[ToolName]` value matches `[JsonDerivedType]` discriminator, timeout table key, and schema name for every tool
- `ToolNameUniquenessTests`: no two input or result types share the same `[ToolName]` value
- `ToolExposureExclusionTests`: no unimplemented tool (`IsImplemented == false`) appears in generated Anthropic tool specs
- `HashCanonicalizationTests`: equality-invariant + empty-input + NO_DOCUMENT sentinel
- `HashCanonicalization_PropertyTests` (FsCheck): 1000 randomized trees, including reordered-but-equivalent arrays
- `HashDecimalFormatTests`: edge-case decimal values produce no scientific notation
- `ValidationTests`: every validator accepts valid and rejects invalid inputs
- `ValidationSchemaSyncTests`: mechanical rules match schema
- `MessageEnvelopeInvariantTests`: ToolResultMessage Success/Result/Error combinations; StateSnapshot null-document invariants, live-document invariants, and sketch-state invariant; ScreenshotResult ScreenshotBase64/ScreenshotUrl envelope invariant
- `AddinSessionIdFormatTests`: validates 32 lowercase hex format acceptance and rejection of malformed values
- `ScreenshotBase64SizeTests`: v1 `ScreenshotResult` with `ScreenshotBase64 > 2 MiB` post-encode is rejected
- `MalformedJsonHandlingTests`: under-4-MiB malformed JSON produces `MalformedEnvelope`, connection stays open, subsequent messages proceed
- `ApiKeyHandshakeTests`: valid key accepted, missing key rejected with HTTP 401, revoked key rejected with HTTP 401, malformed subprotocol rejected with HTTP 400, apikey subprotocol NEVER appears in echoed `Sec-WebSocket-Protocol` response
- `AddinTeardownMessageSerializationTests`: `AddinTeardownMessage` round-trips through `IAddinMessage` polymorphic deserialization; all `TeardownReason` enum values serialize as PascalCase strings
- `ArchitectureTests`: no DTO types defined outside `BSVoiceCAD.Protocol.*`

## 17. Out of Scope

- State machine for tool-call lifecycle → SPEC-03
- DHF schema and hash chain → SPEC-04
- SolidWorks COM service contracts → SPEC-02 (including AddinSessionId generation algorithm, §8)
- System prompt, retry correlation, confirmation flow, vision-context enrichment → SPEC-05
- Build sequence, slice prompts, CLAUDE.md → deferred to assembly after module specs converge
- NetFx manual discriminator-switch deserializer: covered in SPEC-02 (because it's add-in implementation, not a wire contract; the wire is identical for both runtimes)
- Chunked-transfer / streaming protocol for messages exceeding 4 MiB → deferred to a future protocol version
- OAuth 2.0 device flow authentication → v2 (`DEPLOYMENT_V1_V2_MIGRATION.md` §2)
- S3 presigned URL screenshot transport → v2 (`DEPLOYMENT_V1_V2_MIGRATION.md` §3)
- Full pagination for very large feature trees → deferred; `IsFeatureTreeTruncated` + `get_feature_tree` is the v1 stopgap (see Section 8)

## 18. Consumption by Other Specs

SPEC-02 imports `IToolInput`, `IToolResult`, `ToolError`, `ToolErrorCode` (including `InvalidUndoState` and `TemplateNotFound`), `StateSnapshot`, `SelectionRef`, `TimingReport`, `ToolCallLifecycleState`, `ScreenshotResult`, `AddinReadyMessage` (to populate `ScreenshotRootPath` and `WorkstationId`), `AddinTeardownMessage`, `TeardownReason`, and all `ISolidWorks*Service`-facing types. SPEC-02 owns the `AddinSessionId` generation algorithm (SPEC-02 §8).

SPEC-03 imports all four polymorphic message families, `ToolRegistry`, `ToolTimeouts`, `UndoGroupOutcome`, `CancelReason`, `RollbackReason`, `ClientProtocolErrorCode`, `AddinProtocolErrorCode`, `ToolCallLifecycleState`.

SPEC-04 imports `IToolInput`, `IToolResult`, `ToolError`, `StateSnapshot`, `FeatureNode`, `HashCanonicalizer`, `UndoGroupOutcome`, `ToolCallLifecycleState`.

SPEC-05 imports all message records, `ToolRegistry` (specifically `ToolRegistry.Implemented` for tool catalog generation), `AddinProtocolErrorCode` codes, and `ScreenshotResult` (branches on `ScreenshotBase64` vs `ScreenshotUrl` for Anthropic vision API input).

SPEC-06 imports `StateSnapshot`, `SelectionRef`, `FeatureNode` (for display), and nothing else.

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*
---
title: "BSVoice-CAD SPEC-02 — SolidWorks Adapter"
subtitle: "UI-thread command dispatch, ISolidWorks* service contracts, COM-to-ToolError mapping, AddinSessionId generation, screenshot base64 producer"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-02 — SolidWorks Adapter

**Version:** 1.4.0
**Status:** DRAFT — R1 Oracle defect fixes
**Depends on:** SPEC-00 (module boundaries, deployment SDRs), SPEC-01 (wire types, AddinSessionId format contract, AddinTeardownMessage), `DEPLOYMENT_V1_V2_MIGRATION.md` (v1/v2 deployment boundary)
**Consumed by:** SPEC-03 (tool-call lifecycle dispatches through this adapter)
**SolidWorks target:** 2025 SP3+ (COM API backward-compat holds to 2024 SP5 for most methods; Day-0 validation catches any regressions)

**Changelog v1.3.0 → v1.4.0:**
- R1 defect fix: §9.1 planned disconnect now sends `AddinTeardownMessage` (SPEC-01 v1.4.0) before closing the WebSocket. This is the sole trigger for `explicit_shutdown` DHF session closure.
- §5.4: `GeneratedMessageDeserializer` gains `"addin_teardown"` switch case for `IAddinMessage` deserialization (used by test code).
- §10: Added `AddinTeardownMessageSendTests` to required test list.

## 0. Scope

This spec defines how the add-in process (`BSVoiceCAD.Addin`, .NET Framework 4.8, running local on the engineer's Windows workstation) talks to SolidWorks and to the Railway-hosted orchestrator. Every SolidWorks COM call in the codebase flows through the abstractions defined here. Tool handlers consume `ISolidWorks*Service` interfaces; they never reference `SolidWorks.Interop.*` types directly.

**In scope:** UI-thread command dispatch, six service contracts, the NetFx polymorphic deserializer, COM exception mapping, WebSocket client binding and framing (outbound to `wss://vcad.boundstate.ai/ws`), tool-call deduplication registry, CallId format validation, session identity (the authoritative `AddinSessionIdGenerator`), state versioning, the screenshot base64 producer, disconnect and network-loss recovery behavior.

**Out of scope:** tool-call lifecycle state machine (SPEC-03), DHF writes (SPEC-04), agent loop (SPEC-05), SolidWorks types or macro concepts not used by the mandatory or stretch tools, API-key handshake validation (that's SPEC-01 §12 + orchestrator code; the add-in SENDS the handshake), OAuth device flow (v2; see migration doc §2).

**Deployment model:** Add-in runs local (Windows, .NET Framework 4.8, COM-registered with SolidWorks). Orchestrator runs on Railway at `vcad.boundstate.ai`. The WebSocket between them is `wss://` (TLS terminated at Railway edge), crosses the public internet, and is authenticated via API key in the handshake subprotocol per SPEC-01 §12. **The add-in is the WebSocket client, not a server.**

## 1. Project Shape

```
BSVoiceCAD.Addin/                                # .NET Framework 4.8, COM-registered
├── BSVoiceAddin.cs                              # ISwAddin entry point
├── Abstractions/
│   ├── ISolidWorksGateway.cs                    # Application-level
│   ├── ISolidWorksDocumentService.cs
│   ├── ISolidWorksSketchService.cs
│   ├── ISolidWorksFeatureService.cs
│   ├── ISolidWorksSelectionService.cs
│   └── ISolidWorksUndoService.cs
├── SolidWorksImpl/                              # Real COM implementations
│   ├── SolidWorksGateway.cs
│   ├── SolidWorksDocumentService.cs
│   ├── SolidWorksSketchService.cs
│   ├── SolidWorksFeatureService.cs
│   ├── SolidWorksSelectionService.cs
│   └── SolidWorksUndoService.cs
├── Threading/
│   ├── ISwCommandQueue.cs
│   └── SwUiThreadCommandQueue.cs                # Dispatches to SW UI thread via SynchronizationContext
├── Bridge/
│   ├── WebSocketClient.cs                       # ClientWebSocket (NetFx 4.8) with reconnect + backoff
│   ├── MessageDispatcher.cs                     # Routes + catch-all exception boundary
│   ├── ProtocolVersionNegotiator.cs
│   ├── ApiKeyCredentialStore.cs                 # Reads API key from Windows Credential Manager
│   ├── ToolCallRegistry.cs                      # In-flight dedup + 60s result cache (session-scoped)
│   └── GeneratedMessageDeserializer.cs          # Manual polymorphic switch (NetFx-only)
├── Tools/
│   ├── IToolHandler.cs
│   ├── ToolDispatcher.cs                        # Registry<string, IToolHandler> + catch-all
│   ├── DocumentTools.cs
│   ├── SketchTools.cs
│   ├── FeatureTools.cs
│   ├── QueryTools.cs                            # screenshot + get_feature_tree
│   ├── SelectionTools.cs                        # Stretch
│   ├── HoleTools.cs                             # Stretch
│   ├── PatternTools.cs                          # Stretch
│   └── EditTools.cs                             # Stretch
├── State/
│   ├── StateSerializer.cs
│   ├── StateVersionCounter.cs                   # Scoped to AddinSessionId
│   ├── AddinSessionIdGenerator.cs               # Authoritative algorithm (see §8)
│   ├── FeatureTreeTraverser.cs
│   └── ScreenshotProducer.cs                    # Captures PNG, computes SHA-256, base64-encodes
├── Undo/
│   └── UndoObjectScope.cs                       # IDisposable; keyed by UndoGroupId
├── Events/
│   └── EventRelay.cs                            # SW events → StateUpdateMessage
├── Lifecycle/
│   ├── DisconnectHandler.cs                     # Local rollback on transport loss
│   └── ReconnectHandshake.cs                    # Exponential backoff + resume via same AddinSessionId
├── Logging/
│   └── FileLogger.cs
└── Properties/AssemblyInfo.cs
```

## 2. Threading Model — Dispatching to the SolidWorks UI Thread

### 2.1 Problem

SolidWorks COM APIs are apartment-threaded and bound to the SolidWorks UI thread's STA. A call to `ModelDoc2.SketchManager.CreateLine(...)` from any other thread either triggers cross-apartment COM marshaling (adding latency and risking deadlock if the UI thread is blocked) or produces `COMException 0x8001010E` (RPC_E_WRONG_THREAD). Creating a separate STA thread does NOT solve this — the SolidWorks COM objects live in the SolidWorks UI thread's apartment, and a different STA thread is a different apartment. The WebSocket client receives messages on thread pool threads. Any path from "message arrives" to "SW API call" must marshal onto the SolidWorks UI thread itself.

### 2.2 Solution

`SwUiThreadCommandQueue` captures the SolidWorks UI thread's `SynchronizationContext` during `BSVoiceAddin.ConnectToSW` (which SolidWorks guarantees runs on its UI thread). If no `SynchronizationContext` is installed on that thread, the add-in installs a `WindowsFormsSynchronizationContext` (available in .NET Framework 4.8 via `System.Windows.Forms`) before capturing. A `TaskScheduler` is derived via `TaskScheduler.FromCurrentSynchronizationContext()`. Every SW COM call in the codebase enters through `ISwCommandQueue.ExecuteAsync(...)`, which posts work onto the SolidWorks UI thread's message queue via this scheduler.

This approach leverages SolidWorks's own Windows message pump — no additional pump or dedicated worker thread is required. Work items posted via the `SynchronizationContext` are processed in FIFO order by the existing message loop.

```csharp
public interface ISwCommandQueue
{
    /// <summary>
    /// Posts work to the SolidWorks UI thread. The TResult is materialized
    /// on the UI thread and the returned Task completes on the caller's context.
    /// If IsOnSwThread is true, the work executes inline (see Section 2.3).
    /// Throws OperationCanceledException if ct is triggered before execution begins.
    /// </summary>
    Task<TResult> ExecuteAsync<TResult>(
        Func<TResult> work,
        CancellationToken ct = default);

    /// <summary>Void-returning variant.</summary>
    Task ExecuteAsync(
        Action work,
        CancellationToken ct = default);

    /// <summary>
    /// Returns true when the current thread is the SolidWorks UI thread.
    /// Used internally for reentrancy detection and in assertions.
    /// </summary>
    bool IsOnSwThread { get; }
}
```

**Startup capture (in ConnectToSW on the SW UI thread):**

```csharp
_swThreadId = Thread.CurrentThread.ManagedThreadId;
if (SynchronizationContext.Current == null)
    SynchronizationContext.SetSynchronizationContext(
        new WindowsFormsSynchronizationContext());
_swSyncContext = SynchronizationContext.Current;
_swTaskScheduler = TaskScheduler.FromCurrentSynchronizationContext();
```

**Dispatch path:** `ExecuteAsync<TResult>` calls `Task.Factory.StartNew(work, ct, TaskCreationOptions.None, _swTaskScheduler)`. The scheduler posts the delegate to the SolidWorks UI thread's message queue. The returned `Task<TResult>` completes when the delegate finishes on the UI thread.

**Startup probe (mandatory gate):** After capturing the `SynchronizationContext` and deriving the `TaskScheduler`, the add-in performs a round-trip probe: it posts a trivial delegate (setting a flag) via the scheduler and awaits completion with a 5-second timeout. If the probe does not complete within the timeout, the `SynchronizationContext` is incompatible with SolidWorks's message pump. The add-in logs a fatal error via `FileLogger`, does NOT start the WebSocket client, and sets a `StartupFailed` flag observable via `ISolidWorksGateway`. This is a non-recoverable state for the session; the user must restart SolidWorks and investigate the environment.

**Day-0 note on SynchronizationContext:** SolidWorks 2025 SP3 likely already installs a `SynchronizationContext` (or a compatible WinForms one) on its UI thread since it hosts WinForms-based dialogs internally. Day-0 validation (Section 11 step 2) must document the actual type returned by `SynchronizationContext.Current`. If SolidWorks provides its own context, the install-if-null path is dead code — retain it as a defensive fallback but document the finding.

### 2.3 Reentrancy

If `ExecuteAsync` is called from code already running on the SolidWorks UI thread (i.e., `IsOnSwThread` is true), posting to the message queue and then synchronously awaiting the result would deadlock — the UI thread cannot process the posted message while blocked. Therefore:

**Rule:** When `IsOnSwThread` is true, `ExecuteAsync` checks the `CancellationToken`, then executes the work delegate **inline** and returns a completed `Task<TResult>`. This prevents deadlock from nested dispatch.

**Corollary:** `IsOnSwThread` is used internally by `SwUiThreadCommandQueue` for this reentrancy detection. External callers (tool handlers, services) must not branch on `IsOnSwThread` — the queue handles marshaling transparently.

**Event reentrancy guard:** SolidWorks COM events fire on the UI thread and may fire re-entrantly during a tool's COM calls. `EventRelay` checks a `ToolInProgress` flag managed by `ToolDispatcher`. `ToolDispatcher` sets `ToolInProgress = true` before invoking the tool handler and resets it to `false` in a **`finally` block**, ensuring the flag is cleared regardless of whether the handler succeeds or throws an exception. While `ToolInProgress` is true, incoming SW events are noted but state capture is suppressed — the tool result's `StateAfter` snapshot will already reflect the changes. Events that fire outside tool execution trigger state capture normally.

### 2.4 Cancellation Semantics

The queue supports **two cancellation states** distinguished by where the command is in its lifecycle at the moment of cancel:

- **Queued-cancel (cheap):** The Task was posted to the message queue but the UI thread has not yet picked it up. `CancellationToken` causes the `TaskScheduler` to transition the task to `Canceled` without executing the delegate. The caller observes `OperationCanceledException`.
- **Executing-cancel (cooperative only):** The delegate is already running on the UI thread. COM calls cannot be interrupted. The work runs to completion, and the caller's `Task` resolves normally with the result. The caller's observation is that cancellation was ignored; the lifecycle layer (SPEC-03) treats this case by quarantining the eventual result.

This is the only correct model for SolidWorks. Any spec claim of "preempt COM mid-call" is false. SPEC-03's cancel protocol is built around this reality.

### 2.5 Startup and Shutdown

`SwUiThreadCommandQueue.Start()` is called from `BSVoiceAddin.ConnectToSW` after the `SynchronizationContext` capture and startup probe. `Stop()` is called from `DisconnectFromSW`. On Stop:

1. A `_stopped` flag is set atomically. Subsequent `ExecuteAsync` calls throw `ObjectDisposedException`.
2. Any task currently executing on the UI thread runs to completion (non-preemptible). Its result is stored in the `ToolCallRegistry` even though no client may receive it.
3. No dedicated thread join is required — the queue does not own a thread.

Because the queue does not own a thread, Stop is non-blocking. The only concern is in-flight work, which completes naturally on the SolidWorks UI thread as part of SolidWorks's own shutdown sequence.

### 2.6 Required Test — `StaCommandQueueTests`

Build-breaking. Lives in `BSVoiceCAD.Addin.Tests`. Tests create a thread with `WindowsFormsSynchronizationContext` installed and a running message loop (`Application.Run`) to simulate the SW UI thread.

- Asserts that work dispatched from a thread pool thread executes on the simulated UI thread
- Asserts FIFO ordering under load (1000 enqueues, results in order)
- Asserts queued-cancel behavior (cancel before dequeue → `OperationCanceledException`)
- Asserts executing-cancel behavior (cancel after start → work completes, result returned)
- Asserts reentrancy: nested `ExecuteAsync` from within a running command completes without deadlock
- Asserts `ObjectDisposedException` after Stop
- Asserts startup probe: a non-pumping thread causes the probe to time out and `StartupFailed` to be set

## 3. Service Contracts

All six services are synchronous. Every method runs on the SolidWorks UI thread because all callers dispatch through `ISwCommandQueue.ExecuteAsync`. Errors surface as thrown exceptions; `ToolDispatcher` converts them to `ToolError`.

### 3.1 ISolidWorksGateway

Application-level. Checks SolidWorks is loaded and readable.

```csharp
public interface ISolidWorksGateway
{
    bool IsApplicationOpen { get; }
    string GetApplicationVersion();                        // e.g., "SOLIDWORKS 2025 SP3.0"
    string GetApplicationBuild();                          // e.g., "35.2.0.0139"
    string GetActiveDocumentId();                          // Returns "" if no active doc
}
```

### 3.2 ISolidWorksDocumentService

```csharp
public interface ISolidWorksDocumentService
{
    DocumentRef NewPart(string template);
    DocumentRef? GetActiveDocument();
    void CloseDocument(string documentId);
    StateSnapshot CaptureState(long stateVersion, string addinSessionId);
}
```

### 3.3 ISolidWorksSketchService

```csharp
public interface ISolidWorksSketchService
{
    string StartSketch(PlaneRef plane);
    (IReadOnlyList<SketchEntityId> Edges, IReadOnlyList<SketchEntityId> Vertices)
        DrawRectangle(double x1, double y1, double x2, double y2, bool centeredOnOrigin);
    (SketchEntityId Edge, SketchEntityId CenterVertex)
        DrawCircle(double cx, double cy, double radius, bool centeredOnOrigin);
    string AddDimension(DimensionMode mode, DimensionTarget target, double value, DimensionType type);
    string ExitSketch();
    string? GetActiveSketchId();
}
```

### 3.4 ISolidWorksFeatureService

```csharp
public interface ISolidWorksFeatureService
{
    EntityRef Extrude(string sketchId, double depth, ExtrudeDirection direction, bool mergeResult);
    EntityRef CutExtrude(string sketchId, double? depth, ExtrudeDirection direction);
    EntityRef Fillet(IReadOnlyList<string> edgeRefs, double radius, bool propagate);
    EntityRef Chamfer(IReadOnlyList<string> edgeRefs, double distance, double angleDeg);
    IReadOnlyList<FeatureNode> GetFeatureTree(bool includeSuppressed, bool compressed);
    void ModifyParameter(string featureId, string parameterName, double newValue);
}
```

### 3.5 ISolidWorksSelectionService

```csharp
public interface ISolidWorksSelectionService
{
    IReadOnlyList<EntityRef> GetCurrentSelection();
    EntityRef? RaycastViewportCenter();
    (byte[] PngBytes, string Sha256Hex, int ActualWidthPx, int ActualHeightPx)
        CaptureScreenshot(int widthPx, int heightPx);
}
```

### 3.6 ISolidWorksUndoService

```csharp
public interface ISolidWorksUndoService
{
    void StartUndoGroup(string undoGroupId, string label);
    void CommitUndoGroup(string undoGroupId);
    void RollbackUndoGroup(string undoGroupId);
    string? GetActiveUndoGroupId();
}
```

## 4. COM Exception to ToolError Mapping

`ToolDispatcher` wraps every tool handler invocation in a catch-all boundary. The mapping from exception type to `ToolErrorCode`:

| Exception Type | HRESULT | ToolErrorCode | Notes |
|----------------|---------|---------------|-------|
| `COMException` | `0x8001010E` RPC_E_WRONG_THREAD | `InternalError` | Architectural bug; should never fire if `ISwCommandQueue` is used correctly. |
| `COMException` | `0x80004005` E_FAIL | `ComException` | Generic SolidWorks failure. `SwHresult` populated. |
| `COMException` | `0x800A0005` (SW-specific: no active document) | `NoActiveDocument` | Preconditional. |
| `COMException` | other HRESULT | `ComException` | `SwHresult` = hex string. |
| `InvalidSketchStateException` | — | `InvalidSketchState` | From ExitSketch when no sketch active, etc. |
| `InvalidUndoStateException` | — | `InvalidUndoState` | From StartUndoGroup when a group is already active. |
| `TemplateNotFoundException` | — | `TemplateNotFound` | Template resolution failed in NewPart. |
| `ArgumentException` / `ArgumentNullException` | — | `InternalError` | Handler or service bug. |
| `OperationCanceledException` | — | `Cancelled` | Queued-cancel path. |
| `TimeoutException` | — | `TimedOut` | Thrown by the lifecycle layer, not this adapter. |
| Any other `Exception` | — | `InternalError` | Caught by the catch-all in `ToolDispatcher`. |

## 5. NetFx Polymorphic Deserializer

### 5.1 Problem

`[JsonPolymorphic]` and `[JsonDerivedType]` require `System.Text.Json` 7+, which is not available on .NET Framework 4.8.

### 5.2 Solution

`GeneratedMessageDeserializer.cs` in the `Bridge/` folder is a hand-written class that reads the discriminator property from an incoming JSON message and dispatches to the correct concrete type via a switch statement.

### 5.3 Sync and Round-Trip Tests

Build-breaking. Live in `BSVoiceCAD.Addin.Tests`.

**`ProtocolDeserializerSyncTests`:** Reflects all concrete implementations of each polymorphic interface in the Protocol assembly. For each implementation, asserts that the corresponding switch case exists in `GeneratedMessageDeserializer.cs`.

**`ProtocolDeserializerRoundTripTests`:** For every concrete polymorphic type, constructs a representative instance, serializes it to JSON, and deserializes it through `GeneratedMessageDeserializer`.

### 5.4 Shape of the Switch

```csharp
public static class GeneratedMessageDeserializer
{
    public static IClientMessage DeserializeClientMessage(JsonElement root)
    {
        var type = root.GetProperty("type").GetString();
        return type switch
        {
            "tool_call"              => root.Deserialize<ToolCallMessage>(JsonOptions.Default)!,
            "cancel_tool_call"       => root.Deserialize<CancelToolCallMessage>(JsonOptions.Default)!,
            "start_undo_group"       => root.Deserialize<StartUndoGroupMessage>(JsonOptions.Default)!,
            "commit_undo_group"      => root.Deserialize<CommitUndoGroupMessage>(JsonOptions.Default)!,
            "rollback_undo_group"    => root.Deserialize<RollbackUndoGroupMessage>(JsonOptions.Default)!,
            "client_protocol_error"  => root.Deserialize<ClientProtocolErrorMessage>(JsonOptions.Default)!,
            _ => throw new UnknownMessageTypeException(type)
        };
    }

    public static IAddinMessage DeserializeAddinMessage(JsonElement root)
    {
        var type = root.GetProperty("type").GetString();
        return type switch
        {
            "tool_result"            => root.Deserialize<ToolResultMessage>(JsonOptions.Default)!,
            "state_update"           => root.Deserialize<StateUpdateMessage>(JsonOptions.Default)!,
            "addin_ready"            => root.Deserialize<AddinReadyMessage>(JsonOptions.Default)!,
            "addin_teardown"         => root.Deserialize<AddinTeardownMessage>(JsonOptions.Default)!,
            "undo_group_outcome"     => root.Deserialize<UndoGroupOutcomeMessage>(JsonOptions.Default)!,
            "addin_protocol_error"   => root.Deserialize<AddinProtocolErrorMessage>(JsonOptions.Default)!,
            _ => throw new UnknownMessageTypeException(type)
        };
    }

    // Analogous switches for IToolInput, IToolResult, PlaneRef,
    // DimensionTarget, FeatureParameter.
}
```

## 6. WebSocket Client

### 6.1 Role Inversion from v1.2.0

**The add-in is the WebSocket client, and the orchestrator (Railway-hosted at `vcad.boundstate.ai`) is the server.**

### 6.2 Binding and Lifecycle

- **Target:** `wss://vcad.boundstate.ai/ws`
- **TLS:** TLS 1.2 minimum, certificates validated via Windows certificate store trust chain.
- **Authentication:** API key in `Sec-WebSocket-Protocol` subprotocol header per SPEC-01 §12.3.

### 6.3 Framing

One JSON document per WebSocket message. Maximum message size 4 MiB (matching SPEC-01 §3).

### 6.4 Handshake Sequence

1. Add-in calls `ClientWebSocket.ConnectAsync` with subprotocols.
2. Orchestrator validates API key per SPEC-01 §12.3.
3. On success, WebSocket upgrade completes. Add-in generates or reuses `AddinSessionId` (see Section 8).
4. Add-in sends `AddinReadyMessage`.
5. Orchestrator validates `ProtocolVersion`.
6. Add-in captures current SW state and sends `StateUpdateMessage(Trigger = InitialState)`.
7. Add-in sets `HandshakeComplete = true`.
8. Orchestrator may now send `IClientMessage`s.

### 6.5 MessageDispatcher

Dispatches incoming messages by polymorphic type. Every dispatch path is wrapped in a catch-all. Unknown `IClientMessage` subtypes produce `AddinProtocolErrorMessage(ErrorCode = UnknownMessageType)`. Stale-epoch messages produce `AddinProtocolErrorMessage(ErrorCode = StaleEpoch)`.

**Handshake gate:** `MessageDispatcher` maintains a `HandshakeComplete` flag. Any `IClientMessage` received while `HandshakeComplete` is false is rejected.

**CallId format validation:** Before dispatching any `ToolCallMessage`, `MessageDispatcher` validates that `CallId` conforms to the UUIDv7 format.

### 6.6 ToolCallRegistry (In-Flight Dedup)

A per-session `Dictionary<string, ToolCallTracker>` keyed by `ToolCallMessage.CallId`, scoped to the current `AddinSessionId`. The registry **survives WebSocket reconnects** within the same session and is cleared only on session reset (new `AddinSessionId`).

The registry is **in-memory only**. Implementers MUST NOT add persistence to the registry; the `AddinSessionId` boundary is the crash recovery mechanism.

Tracker holds one of five states: `Queued`, `Executing`, `Completed`, `Cancelled`, or `Abandoned`.

## 7. Screenshot Producer

### 7.1 Produce Flow

1. Add-in invokes `ISolidWorksSelectionService.CaptureScreenshot(widthPx, heightPx)`.
2. `ScreenshotProducer` base64-encodes `pngBytes`.
3. If `encoded.Length > 2_097_152` (2 MiB), progressively reduces target dimensions.
4. Producer stages a copy of the PNG bytes to the local cache directory.
5. Producer returns `ScreenshotResult`.

### 7.2 Dimension Reduction Strategy

Iterative halving up to 4 iterations. Abandon after iteration 4.

### 7.3 Local Cache Directory (Audit / v2 Forward-Compat)

`%LOCALAPPDATA%\\BSVoiceCAD\\screenshots\\{addinSessionId}\\{utcTicks}_{callId}.png`

### 7.4 Hash Integrity

SHA-256 is computed on the raw PNG bytes (pre-encode). The orchestrator re-decodes and re-hashes on receipt.

## 8. AddinSessionId

**This spec is authoritative for the derivation algorithm.** SPEC-01 §9 owns the format contract (`^[0-9a-f]{32}$`) and epoch rotation semantics.

### 8.1 Generation Algorithm

```csharp
public static class AddinSessionIdGenerator
{
    public static string Generate()
    {
        var machineId     = ReadMachineId();
        var swProcessId   = Process.GetCurrentProcess().Id;
        var loadTimestamp  = DateTime.UtcNow.Ticks;
        var nonce         = Guid.NewGuid().ToString("N");
        var input = $"{machineId}:{swProcessId}:{loadTimestamp}:{nonce}";
        using var sha = SHA256.Create();
        var hash = sha.ComputeHash(Encoding.UTF8.GetBytes(input));
        return BitConverter.ToString(hash, 0, 16).Replace("-", "").ToLowerInvariant();
    }

    private static string ReadMachineId() { /* HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid with fallback */ }
    private static string FallbackMachineId() { /* %LOCALAPPDATA%\\BSVoiceCAD\\.machine-id */ }
}
```

### 8.2 Lifecycle

Generated once per call to `BSVoiceAddin.ConnectToSW`. Network loss does NOT trigger a new AddinSessionId.

### 8.3 StateVersion Counter

Monotonic, scoped to AddinSessionId, accessed exclusively from the SolidWorks UI thread.

## 9. Disconnect and Reconnect

### 9.1 Planned Disconnect (Orchestrator-Initiated or Add-in-Initiated)

Either side sends WebSocket Close frame with status 1000. Add-in handling:

1. Waits for any currently-executing tool call to complete on the UI thread (60-second escalation timeout).
2. Closes any open undo group via `ISolidWorksUndoService.RollbackUndoGroup` (best-effort).
3. **Sends `AddinTeardownMessage` with the appropriate `TeardownReason`** before closing the WebSocket. This is the sole trigger for `explicit_shutdown` DHF session closure. If the send fails (e.g., WebSocket already closing), the failure is logged but does not block shutdown — the session will eventually close via `heartbeat_timeout` instead.
4. Does NOT clear `ToolCallRegistry` (session-scoped).
5. If this is an orchestrator-initiated idle-timeout close (not a user-initiated shutdown), attempts to reconnect after a short delay (1 second).

### 9.2 Unplanned Disconnect (Transport Loss)

`ClientWebSocket` raises `WebSocketException`. Add-in handling:

1. Classifies all tracked calls by their current state (Queued → Cancelled, Executing → Abandoned).
2. Attempts `RollbackUndoGroup` on any open undo group, local best-effort.
3. Does NOT send `AddinTeardownMessage` (the transport is already lost). The DHF session remains open pending reconnect.
4. Does NOT clear the registry.
5. Enters reconnect loop with exponential backoff: 1s, 2s, 5s, 10s, 30s, 60s, 120s, 120s... (capped at 120s).
6. On successful reconnect, re-runs the handshake sequence (§6.4) with the SAME `AddinSessionId`.

### 9.3 Reconnect Handshake

Every new connection begins with the handshake in Section 6.4. If the add-in has a still-open undo group from a prior connection, the add-in rolls it back before sending `AddinReadyMessage`.

### 9.4 User-Visible Network Status

States: `Connecting`, `Connected`, `Reconnecting`, `AuthFailed`, `StartupFailed`.

## 10. Required Tests (Build-Breaking)

All in `BSVoiceCAD.Addin.Tests`, NetFx 4.8.

- `StaCommandQueueTests` — see Section 2.6
- `ReentrancyDeadlockTests` — nested `ExecuteAsync` completes without deadlock
- `ProtocolDeserializerSyncTests` — see Section 5.3
- `ProtocolDeserializerRoundTripTests` — see Section 5.3
- `ToolDispatcherTests` — exception-to-ToolError mapping
- `InFlightDeduplicationTests` — all registry states and transitions
- `DisconnectRollbackTests` — simulated transport loss mid-undo-group
- `DisconnectExecutingCommandTests` — command in Executing state at disconnect
- `ProtocolVersionNegotiationTests` — handshake pass and fail paths
- `AddinSessionIdGenerationTests` — stability, uniqueness, format regex
- `ScreenshotBase64ProducerTests` — capture, reduction, size cap, hash, cache
- `UndoNestingRejectionTests` — double StartUndoGroup rejection
- `CallIdFormatValidationTests` — non-UUIDv7 rejection
- `HandshakeGateTests` — pre-handshake rejection
- `ModifyParameterScopeTests` — wrong-feature parameter rejection
- `WebSocketClientReconnectTests` — backoff, AddinSessionId preservation, auth failure terminal
- `ApiKeyCredentialStoreTests` — missing credential error, present credential format
- `AddinTeardownMessageSendTests` — planned disconnect sends `AddinTeardownMessage` with correct `TeardownReason` before WebSocket close; unplanned disconnect does NOT send teardown message; send failure during planned disconnect is logged but does not block shutdown

## 11. Day-0 Validation

Before Day 1 implementation starts:

1. Visual Studio 2022 loads a hello-world NetFx 4.8 add-in with SW 2025 SP3+.
2. Inspect `SynchronizationContext.Current` on the SW UI thread and **document the actual type**.
3. `ClientWebSocket` connects to `wss://vcad.boundstate.ai/ws` with a valid test API key.
4. Screenshot round-trip measured under GE75's normal load.
5. `ProtocolDeserializerSyncTests` and `ProtocolDeserializerRoundTripTests` pass.
6. Template resolution confirmed.
7. Machine ID readable from add-in process.
8. Screenshot local cache writable.
9. Network resilience: disable Wi-Fi mid-session, confirm reconnect.
10. API key provisioning from Windows Credential Manager.

## 12. Out of Scope

- Tool-call timeout mechanics → SPEC-03
- DHF row construction → SPEC-04
- Agent loop, retry correlation, user confirmation → SPEC-05
- Viewer reads → SPEC-06
- Voice capture (Whisper) → SPEC-05
- API-key validation logic (server-side) → orchestrator code per SPEC-01 §12
- OAuth device flow (v2) → `DEPLOYMENT_V1_V2_MIGRATION.md` §2
- S3 presigned URL upload (v2) → `DEPLOYMENT_V1_V2_MIGRATION.md` §3

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*
---
title: "BSVoice-CAD SPEC-04 — DHF Ledger"
subtitle: "Schema, hash model, append logic, AddinSessionId epoch, DHF session lifecycle, read-only repository projection, tenant scoping"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-04 — DHF Ledger

**Version:** 1.5.0
**Status:** DRAFT — R1 Oracle defect fixes
**Depends on:** SPEC-00 (module boundaries), SPEC-01 (wire types — including `ToolCallLifecycleState` enum), `DEPLOYMENT_V1_V2_MIGRATION.md` (tenant and deployment boundary)
**Consumed by:** SPEC-03 (writes entries), SPEC-06 (reads entries via `IReadOnlyDhfRepository`)

**Changelog v1.4.0 → v1.5.0:**
- R1 defect fix: New migration 007 adds `locked_at TIMESTAMPTZ NULL` and `lock_reason TEXT NULL` to `dhf.sessions`. `guard_session_update` trigger updated to permit `locked_at`/`lock_reason` NULL→non-NULL transitions. DHF-014 updated: session lock is now persistent and survives orchestrator restart.
- R1 defect fix: New migration 008 adds `screenshot_before_png BYTEA NULL` and `screenshot_after_png BYTEA NULL` to `dhf.entries`. Adds `'recovery'` to `turn_type` CHECK constraint and `'system'` to `input_modality` CHECK constraint. Closes the gap where deep validation and viewer rendering had no stored bytes to operate on.
- R1 defect fix: `RecoveryEventContext` DTO defined (§5). `AppendRecoveryEventAsync` implementation specified (§7.2). `Recovery` added to `TurnType` enum; `System` added to `InputModality` enum; both mapped in `DhfEnumMappings` (§4.5).
- R1 defect fix: `IReadOnlyDhfRepository.ValidateChainAsync` is now strictly side-effect-free — returns findings in-memory only, does NOT write integrity-event decorations. New `IDhfRepository.RecordIntegrityFindingsAsync` method handles decoration persistence.
- R1 defect fix: `EntryDetail` read DTO gains `ScreenshotBeforePng` and `ScreenshotAfterPng` byte array fields for viewer screenshot rendering.
- `OpenOrResumeSessionCore` updated to reject locked sessions.
- `AppendTurnCore` updated to reject locked sessions at the start of the transaction.
- New §7.1: `LockSessionAsync` implementation.
- New §7.2: `AppendRecoveryEventAsync` implementation.
- DHF-016: Recovery events are `turn_type = 'recovery'` entries. DHF-017: Session lock is persistent.

## 0. Scope

This spec defines everything about how voice-and-text turns become durable audit records. Schema, hash chain, append semantics, session lifecycle, epoch handling, session metadata protection, decoration extraction, integrity validation, canonical enum-to-storage mappings, read-only viewer projection, tenant scoping.

**In scope:** Postgres 16 schema including tenant_id and session lock columns, hash canonicalization rules, per-turn append logic, recovery event storage, AddinSessionId epoch and DHF session split/resume, session metadata immutability, cross-workstation conflict handling, decoration rows, immutability enforcement, integrity validation, read API contracts, tenant-scoped repository implementations.

**Out of scope:** tool-call state machine (SPEC-03), agent confirmation flow (SPEC-05), viewer UI (SPEC-06), extraction LLM call shape (summarized here; full prompt in SPEC-05), API key validation (SPEC-01 §12), per-tenant schema provisioning (v2; see migration doc §5).

## 1. Project Shape

```
BSVoiceCAD.Orchestrator.Dhf/
├── DhfLedger.cs                             # AppendTurnAsync, AppendRecoveryEventAsync, LockSessionAsync
├── DhfSessionManager.cs                     # Open/resume/close dhf.sessions rows
├── DhfDecorationWriter.cs                   # Async writer for dhf.entry_decorations
├── DhfRepository.cs                         # Full read+write queries (implements IDhfRepository)
├── DhfReadOnlyRepository.cs                 # Read-only projection (implements IReadOnlyDhfRepository)
├── DhfTurnContext.cs                        # Input DTO consumed by AppendTurnAsync
├── RecoveryEventContext.cs                  # Input DTO consumed by AppendRecoveryEventAsync
├── DhfEnumMappings.cs                       # Canonical enum-to-storage string mappings
├── HashChainValidator.cs                    # Entry-chain and tool-calls-hash verification
├── CrossWorkstationSessionConflictException.cs
├── RepositoryUnavailableException.cs
├── SessionNotFoundException.cs
├── SessionLockedException.cs                # Thrown when append/open targets a locked session
├── DhfExtractor.cs                          # [STRETCH] Sonnet 4.6 decoration extraction
├── DhfHashColumns.cs                        # Canonical entry-level hash coverage (in Protocol)
├── ToolCallHashColumns.cs                   # Canonical child-row hash coverage (in Protocol)
└── Exporters/
    ├── PdfExporter.cs                       # [STRETCH]
    └── GreenlightGuruExporter.cs            # [ROADMAP] stub

BSVoiceCAD.Orchestrator.Database/
└── Migrations/
    ├── 001_initial_schema.sql
    ├── 002_indexes.sql
    ├── 003_entries_and_tool_calls.sql
    ├── 004_entry_decorations.sql
    ├── 005_immutability_triggers.sql
    ├── 006_tenant_id.sql
    ├── 007_session_lock.sql                 # v1.5.0: persistent session lock
    └── 008_recovery_and_screenshots.sql     # v1.5.0: recovery turn type + screenshot bytea
```

## 2. Design Decisions (Local SDRs)

**SDR-04-001 through SDR-04-007:** Unchanged from v1.4.0.

**SDR-04-008:** Unchanged from v1.4.0 (cross-workstation conflict is hard error).

**SDR-04-009:** Unchanged (canonical enum-to-storage mappings).

**SDR-04-010: Session metadata immutability via DB trigger.** All columns in `dhf.sessions` except `ended_at`, `locked_at`, and `lock_reason` are immutable once inserted. `guard_session_update` permits `ended_at` NULL→non-NULL and `locked_at`/`lock_reason` NULL→non-NULL transitions; rejects all other UPDATE attempts. DELETE on `dhf.sessions` is also rejected.

**SDR-04-011:** Unchanged (read-only repository projection is the viewer's sole database surface).

## 3. Schema

### 3.1 Migrations

Migrations 001 through 006 are unchanged from v1.4.0.

```sql
-- 007_session_lock.sql (NEW in v1.5.0)

ALTER TABLE dhf.sessions ADD COLUMN locked_at TIMESTAMPTZ NULL;
ALTER TABLE dhf.sessions ADD COLUMN lock_reason TEXT NULL;

-- Replace guard_session_update to handle locked_at/lock_reason transitions.
CREATE OR REPLACE FUNCTION dhf.guard_session_update() RETURNS TRIGGER AS $$
BEGIN
    -- Closed sessions cannot be modified at all
    IF OLD.ended_at IS NOT NULL THEN
        RAISE EXCEPTION
            'dhf.sessions: session % is already closed; no further updates permitted',
            OLD.session_id;
    END IF;

    -- Check immutable columns haven't changed
    IF ROW(NEW.session_id, NEW.started_at, NEW.user_identity, NEW.user_display_name,
           NEW.workstation_id, NEW.sw_version, NEW.addin_version,
           NEW.orchestrator_version, NEW.protocol_version, NEW.addin_session_id,
           NEW.tenant_id)
       IS DISTINCT FROM
       ROW(OLD.session_id, OLD.started_at, OLD.user_identity, OLD.user_display_name,
           OLD.workstation_id, OLD.sw_version, OLD.addin_version,
           OLD.orchestrator_version, OLD.protocol_version, OLD.addin_session_id,
           OLD.tenant_id)
    THEN
        RAISE EXCEPTION
            'dhf.sessions: metadata columns are immutable on session %',
            OLD.session_id;
    END IF;

    -- ended_at: only NULL -> non-NULL
    IF OLD.ended_at IS NULL AND NEW.ended_at IS NOT NULL THEN
        NULL; -- permitted
    ELSIF NEW.ended_at IS DISTINCT FROM OLD.ended_at THEN
        RAISE EXCEPTION
            'dhf.sessions: ended_at may only transition NULL to non-NULL on session %',
            OLD.session_id;
    END IF;

    -- locked_at: only NULL -> non-NULL
    IF OLD.locked_at IS NULL AND NEW.locked_at IS NOT NULL THEN
        NULL; -- permitted
    ELSIF NEW.locked_at IS DISTINCT FROM OLD.locked_at THEN
        RAISE EXCEPTION
            'dhf.sessions: locked_at may only transition NULL to non-NULL on session %',
            OLD.session_id;
    END IF;

    -- lock_reason: only NULL -> non-NULL, and only when locked_at is being set
    IF OLD.lock_reason IS NULL AND NEW.lock_reason IS NOT NULL THEN
        IF NEW.locked_at IS NULL THEN
            RAISE EXCEPTION
                'dhf.sessions: lock_reason requires locked_at on session %',
                OLD.session_id;
        END IF;
    ELSIF NEW.lock_reason IS DISTINCT FROM OLD.lock_reason THEN
        RAISE EXCEPTION
            'dhf.sessions: lock_reason may only transition NULL to non-NULL on session %',
            OLD.session_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

```sql
-- 008_recovery_and_screenshots.sql (NEW in v1.5.0)

-- Add 'recovery' to turn_type CHECK constraint
ALTER TABLE dhf.entries DROP CONSTRAINT IF EXISTS entries_turn_type_check;
ALTER TABLE dhf.entries ADD CONSTRAINT entries_turn_type_check
    CHECK (turn_type IN ('execution', 'clarification', 'confirmation',
                         'ambiguity_resolution', 'recovery'));

-- Add 'system' to input_modality CHECK constraint
ALTER TABLE dhf.entries DROP CONSTRAINT IF EXISTS entries_input_modality_check;
ALTER TABLE dhf.entries ADD CONSTRAINT entries_input_modality_check
    CHECK (input_modality IN ('voice', 'text', 'system'));

-- Screenshot bytea storage for deep validation and viewer rendering
ALTER TABLE dhf.entries ADD COLUMN screenshot_before_png BYTEA NULL;
ALTER TABLE dhf.entries ADD COLUMN screenshot_after_png BYTEA NULL;
```

### 3.2 Enforcement Gap (Honest Disclosure)

Unchanged from v1.4.0. The orchestrator has `DROP TRIGGER` privilege.

## 4. Hash Model

### 4.1–4.4 Unchanged from v1.4.0.

Note: `screenshot_before_png` and `screenshot_after_png` are NOT included in `DhfHashColumns` — their integrity is verified via the `screenshot_before_sha256` and `screenshot_after_sha256` columns which ARE in the hash input.

### 4.5 Enum-to-Storage Mappings

```csharp
public static class DhfEnumMappings
{
    public static string ToStorage(this InputModality m) => m switch
    {
        InputModality.Voice => "voice",
        InputModality.Text => "text",
        InputModality.System => "system",
        _ => throw new ArgumentOutOfRangeException(nameof(m))
    };

    public static string ToStorage(this TurnType t) => t switch
    {
        TurnType.Execution => "execution",
        TurnType.Clarification => "clarification",
        TurnType.Confirmation => "confirmation",
        TurnType.AmbiguityResolution => "ambiguity_resolution",
        TurnType.Recovery => "recovery",
        _ => throw new ArgumentOutOfRangeException(nameof(t))
    };

    // UndoGroupOutcome, ToolCallLifecycleState, DecorationType: unchanged from v1.4.0
    public static string ToStorage(this UndoGroupOutcome o) => o switch
    {
        UndoGroupOutcome.Committed => "committed",
        UndoGroupOutcome.RolledBack => "rolled_back",
        UndoGroupOutcome.Abandoned => "abandoned",
        _ => throw new ArgumentOutOfRangeException(nameof(o))
    };

    public static string ToStorage(this ToolCallLifecycleState s) => s switch
    {
        ToolCallLifecycleState.Completed => "completed",
        ToolCallLifecycleState.TimedOut => "timed_out",
        ToolCallLifecycleState.Cancelled => "cancelled",
        ToolCallLifecycleState.Abandoned => "abandoned",
        _ => throw new ArgumentOutOfRangeException(nameof(s))
    };

    public static string ToStorage(this DecorationType d) => d switch
    {
        DecorationType.RationaleExtraction => "rationale_extraction",
        DecorationType.ManualAnnotation => "manual_annotation",
        DecorationType.AmbiguityContext => "ambiguity_context",
        DecorationType.IntegrityEvent => "integrity_event",
        _ => throw new ArgumentOutOfRangeException(nameof(d))
    };
}
```

## 5. DhfTurnContext and RecoveryEventContext (Input DTOs)

`DhfTurnContext` is unchanged from v1.4.0.

```csharp
public enum InputModality { Voice, Text, System }
public enum TurnType { Execution, Clarification, Confirmation, AmbiguityResolution, Recovery }
public enum DecorationType { RationaleExtraction, ManualAnnotation, AmbiguityContext, IntegrityEvent }

public enum RecoveryTrigger { Timeout, Crash, Disconnect }
public enum RecoveryStrategy { StateRefresh, UndoGroupRollback }
```

**RecoveryEventContext (NEW in v1.5.0):**

```csharp
public record RecoveryEventContext
{
    public required Guid SessionId { get; init; }
    public required DateTimeOffset Timestamp { get; init; }
    public required RecoveryTrigger Trigger { get; init; }
    public required RecoveryStrategy Strategy { get; init; }
    public required bool Success { get; init; }
    public string? FailureReason { get; init; }
    public string? ModelStateDivergenceDescription { get; init; }
    public required string FeatureTreeBeforeHash { get; init; }
    public required string FeatureTreeAfterHash { get; init; }
    public JsonElement? FeatureTreeBefore { get; init; }
    public JsonElement? FeatureTreeAfter { get; init; }
    public string? RelatedUndoGroupId { get; init; }
    public string? ScreenshotBeforeSha256 { get; init; }
    public string? ScreenshotAfterSha256 { get; init; }
    public byte[]? ScreenshotBeforePng { get; init; }
    public byte[]? ScreenshotAfterPng { get; init; }
}
```

SPEC-03 constructs `RecoveryEventContext` from the recovery flow outcome and hands it to `DhfLedger.AppendRecoveryEventAsync` (§7.2).

## 6. DhfSessionManager

### 6.1 Open or Resume

`OpenOrResumeSessionCore` is updated to reject locked sessions:

```csharp
private async Task<Guid> OpenOrResumeSessionCore(
    string addinSessionId, string tenantId,
    SessionMetadata metadata, CancellationToken ct)
{
    // ... (advisory lock, existing lookup unchanged) ...

    if (existing is not null)
    {
        // Check if the existing session is locked
        if (existing.LockedAt is not null)
        {
            await tx.RollbackAsync(ct);
            throw new SessionLockedException(
                existing.SessionId,
                $"Session {existing.SessionId} is locked (locked at {existing.LockedAt}; "
                + $"reason: {existing.LockReason ?? "unknown"}). "
                + "Close the session via SQL and start a new one.");
        }

        if (existing.WorkstationId == metadata.WorkstationId)
        {
            // Same tenant, same AddinSessionId, same workstation → resume.
            await tx.CommitAsync(ct);
            return existing.SessionId;
        }

        // Cross-workstation conflict (unchanged from v1.4.0)
        // ... throws CrossWorkstationSessionConflictException ...
    }

    // ... (remainder unchanged from v1.4.0) ...
}
```

### 6.2 Close on Add-in Disconnect

Unchanged from v1.4.0.

### 6.3 CrossWorkstationSessionConflictException

Unchanged from v1.4.0.

## 7. DhfLedger.AppendTurnAsync

The ONLY writer of `dhf.entries` and `dhf.tool_calls` for regular turns.

`AppendTurnCore` is updated to check session lock at the start of the transaction:

```csharp
private async Task<Guid> AppendTurnCore(DhfTurnContext turn, CancellationToken ct)
{
    // ... (connection, transaction, advisory lock) ...

    // 0. Verify session is open, not locked, and fetch metadata.
    var sessionRow = await conn.QuerySingleOrDefaultAsync<SessionCheckRow>(
        @"SELECT ended_at IS NULL AS is_open, locked_at, lock_reason,
                 addin_session_id, tenant_id
          FROM dhf.sessions WHERE session_id = @SessionId",
        new { turn.SessionId }, tx)
        ?? throw new SessionNotFoundException(turn.SessionId);

    if (!sessionRow.IsOpen)
        throw new InvalidOperationException(
            $"Session {turn.SessionId} is closed; cannot append.");

    if (sessionRow.LockedAt is not null)
        throw new SessionLockedException(turn.SessionId,
            $"Session {turn.SessionId} is locked (reason: {sessionRow.LockReason ?? "unknown"}). "
            + "No further turns may be appended.");

    // ... (remainder unchanged from v1.4.0: epoch validation, tip read,
    //      tool call ordering, hash computation, INSERT) ...
}
```

### 7.1 LockSessionAsync (NEW in v1.5.0)

Called by the lifecycle layer (SPEC-03) when `AppendTurnAsync` fails after all retries. Persists the lock in the database so it survives orchestrator restart.

```csharp
public async Task LockSessionAsync(
    Guid sessionId, string reason, CancellationToken ct)
{
    using var conn = await _dataSource.OpenConnectionAsync(ct);
    using var tx = await conn.BeginTransactionAsync(IsolationLevel.Serializable, ct);

    var lockKey = $"dhf_append:{sessionId}";
    await conn.ExecuteAsync(
        "SELECT pg_advisory_xact_lock(hashtext(@k))",
        new { k = lockKey }, tx);

    var rows = await conn.ExecuteAsync(
        @"UPDATE dhf.sessions
          SET locked_at = NOW(), lock_reason = @Reason
          WHERE session_id = @SessionId
            AND ended_at IS NULL
            AND locked_at IS NULL",
        new { sessionId, reason }, tx);

    if (rows == 0)
        _logger.LogWarning(
            "LockSessionAsync: session {SessionId} was already closed or locked",
            sessionId);

    await tx.CommitAsync(ct);
}
```

The `guard_session_update` trigger (migration 007) permits this `locked_at` NULL→non-NULL transition. Idempotent: if the session is already locked or closed, the UPDATE matches zero rows.

### 7.2 AppendRecoveryEventAsync (NEW in v1.5.0)

Appends a recovery event as a `dhf.entries` row with `turn_type = 'recovery'` and `input_modality = 'system'`. Participates in the hash chain identically to regular turn entries.

```csharp
public async Task<Guid> AppendRecoveryEventAsync(
    RecoveryEventContext context, CancellationToken ct)
{
    // Build a synthetic DhfTurnContext-equivalent for the recovery entry.
    // The raw_transcript field carries a human-readable recovery description.
    var description = $"Recovery event: trigger={context.Trigger}, "
        + $"strategy={context.Strategy}, success={context.Success}";
    if (context.ModelStateDivergenceDescription is not null)
        description += $", divergence={context.ModelStateDivergenceDescription}";
    if (context.FailureReason is not null)
        description += $", failure={context.FailureReason}";

    // Use the same AppendTurnCore path with a recovery-typed entry.
    // This ensures hash-chain continuity and advisory lock coordination.
    var recoveryTurn = new DhfTurnContext
    {
        TurnId = Guid.NewGuid(),
        SessionId = context.SessionId,
        Timestamp = context.Timestamp,
        InputModality = InputModality.System,
        RawTranscript = description,
        TranscriptConfidence = null,
        TurnType = TurnType.Recovery,
        UndoGroupId = context.RelatedUndoGroupId,
        UndoOutcome = null,
        DocumentRef = null,
        DocumentRevisionRef = null,
        FeatureTreeBefore = context.FeatureTreeBefore,
        FeatureTreeAfter = context.FeatureTreeAfter,
        FeatureTreeBeforeHash = context.FeatureTreeBeforeHash,
        FeatureTreeAfterHash = context.FeatureTreeAfterHash,
        ScreenshotBeforePath = null,
        ScreenshotAfterPath = null,
        ScreenshotBeforeSha256 = context.ScreenshotBeforeSha256,
        ScreenshotAfterSha256 = context.ScreenshotAfterSha256,
        ToolCalls = [] // Recovery events have zero tool calls
    };

    return await AppendTurnAsync(recoveryTurn, ct);
}
```

Recovery events produce `tool_calls_hash = "NO_TOOL_CALLS"` since they have zero tool calls.

## 8. DhfDecorationWriter (Stretch)

Unchanged from v1.4.0.

## 9. Repository Surface

### 9.1 IDhfRepository (Full Read + Write)

```csharp
public interface IDhfRepository
{
    // Read methods
    Task<IReadOnlyList<DhfSessionSummary>> ListSessionsAsync(string tenantId, int limit, int offset, CancellationToken ct);
    Task<DhfSessionDetail?> GetSessionAsync(Guid sessionId, CancellationToken ct);
    Task<IReadOnlyList<DhfEntrySummary>> ListEntriesAsync(Guid sessionId, CancellationToken ct);
    Task<DhfEntryDetail?> GetEntryAsync(Guid entryId, CancellationToken ct);
    Task<IReadOnlyList<ToolCallRow>> GetToolCallsAsync(Guid entryId, CancellationToken ct);
    Task<IReadOnlyList<DecorationRow>> GetDecorationsAsync(Guid entryId, CancellationToken ct);
    Task<ChainValidationResult> ValidateChainAsync(Guid sessionId, ChainValidationMode mode, CancellationToken ct);

    // Write methods (NOT in IReadOnlyDhfRepository)
    Task<Guid> AppendTurnAsync(DhfTurnContext turn, CancellationToken ct);
    Task<Guid> OpenOrResumeSessionAsync(string addinSessionId, string tenantId, SessionMetadata metadata, CancellationToken ct);
    Task CloseSessionAsync(Guid sessionId, CancellationToken ct);
    Task LockSessionAsync(Guid sessionId, string reason, CancellationToken ct);
    Task<Guid> AppendRecoveryEventAsync(RecoveryEventContext context, CancellationToken ct);
    Task RecordIntegrityFindingsAsync(ChainValidationResult result, CancellationToken ct);
}
```

`RecordIntegrityFindingsAsync` writes `integrity_event` decorations for each failure in the `ChainValidationResult`. This is the sole mechanism for persisting integrity findings. It is NOT available on `IReadOnlyDhfRepository`.

### 9.2 IReadOnlyDhfRepository (Read-Only Projection)

```csharp
public interface IReadOnlyDhfRepository
{
    Task<IReadOnlyList<SessionSummary>> ListSessionsAsync(
        string tenantId, CancellationToken ct = default);

    Task<SessionSummary?> GetSessionAsync(
        Guid sessionId, CancellationToken ct = default);

    Task<IReadOnlyList<EntryListItem>> ListEntriesAsync(
        Guid sessionId, CancellationToken ct = default);

    Task<EntryDetail?> GetEntryDetailAsync(
        Guid entryId, CancellationToken ct = default);

    /// <summary>
    /// Validates the hash chain. STRICTLY SIDE-EFFECT-FREE: returns results
    /// in-memory only, does NOT write integrity-event decorations.
    /// To persist findings, the orchestrator calls
    /// IDhfRepository.RecordIntegrityFindingsAsync separately.
    /// </summary>
    Task<ChainValidationResult> ValidateChainAsync(
        Guid sessionId, ChainValidationMode mode,
        CancellationToken ct = default);
}
```

**Read-side types:**

```csharp
public enum ChainValidationMode { Shallow, Deep }

public sealed record ChainValidationResult
{
    public Guid SessionId { get; init; }
    public ChainValidationMode Mode { get; init; }
    public bool Valid { get; init; }
    public bool Truncated { get; init; }
    public int EntriesChecked { get; init; }
    public int LastSequenceChecked { get; init; }
    public IReadOnlyList<ChainValidationFinding> Failures { get; init; } = [];
    public IReadOnlyList<ChainValidationFinding> Warnings { get; init; } = [];
}

public sealed record ChainValidationFinding
{
    public Guid EntryId { get; init; }
    public int SequenceNumber { get; init; }
    public string Reason { get; init; } = "";
    public string Detail { get; init; } = ""