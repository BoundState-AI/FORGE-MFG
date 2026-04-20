---
title: "BSVoice-CAD SPEC-00 — Orchestration Contract"
subtitle: "The six-module decomposition; interfaces only, no implementation"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-00 — Orchestration Contract

**Version:** 1.2.0
**Status:** DRAFT — post-Round-2 Oracle revision
**Hackathon application:** Submitted 2026-04-17; awaiting selection notification

## 0. Why This Exists

BSVoice-CAD is six logical modules that interact at well-defined seams. This document defines the module boundaries, the seam contracts between them, cross-cutting architectural decisions (SDRs), shared vocabulary, scope gates, and review ordering. Implementation details live in the per-module specs (SPEC-01 through SPEC-06). Oracle reviews each module spec in isolation; this document is the integration contract that lets those reviews compose without drift.

**Non-goals for this document:** state machine details, record field definitions, SQL schemas, build sequence, timeline. Those live in per-module specs.

## 1. Module Map

| Spec | Module | Owns | Lives In | Depends On |
|------|--------|------|----------|------------|
| SPEC-01 | Wire Protocol | Cross-process types, polymorphic contracts, JSON policy, WebSocket framing contract, version handshake | `BSVoiceCAD.Protocol` | (none — leaf module) |
| SPEC-02 | SolidWorks Adapter | STA command queue, `ISolidWorks*Service` contracts, COM-to-`ToolError` mapping, AddinSessionId generation | `BSVoiceCAD.Addin` | SPEC-01 |
| SPEC-03 | Tool-Call Lifecycle | Call state machine, timeouts, cancel protocol, quarantine rules, per-turn DHF aggregation, post-timeout recovery | `BSVoiceCAD.Orchestrator.Lifecycle` + `BSVoiceCAD.Addin.Bridge` | SPEC-01, SPEC-02, SPEC-04 |
| SPEC-04 | DHF Ledger | Schema, hash model, append logic, DHF session lifecycle | `BSVoiceCAD.Orchestrator.Dhf` + `BSVoiceCAD.Orchestrator.Database` | SPEC-01 (message shapes), SPEC-00 §6 (AddinSessionId epoch semantics) |
| SPEC-05 | Agent Loop + Input | User input, confirmation flow, Opus 4.7 tool-calling, vision context, retry correlation, system prompt | `BSVoiceCAD.Orchestrator.Agent` + `BSVoiceCAD.Orchestrator.Input` | SPEC-01, SPEC-03 |
| SPEC-06 | Viewer | Timeline rendering, DHF read API, integrity endpoints | `BSVoiceCAD.Orchestrator.Viewer` | SPEC-04 (read-only) |

**Dependency direction is strictly downward.** SPEC-01 depends on nothing. SPEC-06 depends on SPEC-04 only. Circular references are a defect.

**Assembly mapping note:** The "Lives In" column names the primary assembly (`.csproj`) for each module. Where two assemblies are listed (separated by `+`), the first targets the orchestrator runtime (.NET 8) and the second targets the add-in runtime (.NET Framework 4.8). The architecture-test project (`BSVoiceCAD.Tests.Architecture`) must reference all assemblies from both runtimes to enforce cross-runtime dependency rules. SPEC-03's `BSVoiceCAD.Addin.Bridge` assembly contains only the add-in-side dispatch surface; all lifecycle state and DHF aggregation logic lives in `BSVoiceCAD.Orchestrator.Lifecycle`.

**AddinSessionId epoch semantics** are defined in this document's glossary (§6) so that SPEC-04 can consume them without a dependency on SPEC-02. SPEC-02 owns the generation algorithm; SPEC-04 consumes only the epoch rotation invariants defined here.

## 2. Seams (Where The Modules Touch)

Each seam enforces one-directional dependency. **Compile-time** dependency direction is verified by `NetArchTest` rules living in a dedicated architecture-test project (`BSVoiceCAD.Tests.Architecture`) that is permitted to reference all module assemblies. **Runtime** seam behavior — transport correctness, serialization round-trips, reconnect semantics, threading invariants — is verified by integration and contract tests in `BSVoiceCAD.Tests.Integration` (see §3). Per-module test projects test only their own module's internal invariants.

### Seam A: SPEC-01 → everyone

Protocol types are consumed by every other module. No module defines its own cross-process wire types. Architecture rule: no cross-process DTO types (types serialized over the WebSocket transport) exist outside `BSVoiceCAD.Protocol.*` namespaces. Internal module-boundary types (e.g., `DhfTurnContext` in SPEC-04) are permitted provided they do not cross the WebSocket boundary.

The cross-process transport between the .NET Framework 4.8 add-in and the .NET 8 orchestrator is a WebSocket connection with JSON message framing. The full transport contract — including connection lifecycle, error semantics, reconnect behavior, and threading guarantees — is defined in SPEC-01.

### Seam B: SPEC-03 → SPEC-02

The tool-call lifecycle dispatches `IToolInput` to the SolidWorks adapter and expects `IToolResult` or `ToolError` back. The adapter never owns lifecycle state; the lifecycle never owns COM threading. SPEC-02 provides a synchronous method surface; SPEC-03 wraps each call in its own timeout and cancel semantics.

**Cancellation boundary:** Cancellation is effective only before a COM call is dispatched to the STA thread. Once a synchronous COM call is in flight, it runs to completion; the STA thread cannot be safely interrupted. A call whose orchestrator-side timeout expires while COM execution is in progress is recorded as `TimedOut`, but the COM operation may still complete and mutate the model.

**Post-timeout recovery (mandatory — never descopable):** Before any subsequent turn may open, SPEC-03 must execute a recovery flow. SPEC-03 defines the deterministic selection rule between the two permitted strategies (state refresh via feature-tree comparison, or undo-group rollback) and the observable success criterion for each. If recovery itself fails, the session enters a locked state that prevents further turns until manual intervention. The recovery outcome — including whether a post-timeout COM completion was detected and whether the model was rolled back or reconciled — is recorded as a DHF recovery event via `DhfLedger.AppendRecoveryEventAsync` (defined in SPEC-04) before the next turn opens. This ensures the audit trail captures any model mutation that occurred after the timeout.

**Turn completion boundary for timeouts:** A timed-out turn closes at the moment the timeout fires; the turn's DHF entry records the `TimedOut` status for the affected tool call(s). Any post-timeout recovery constitutes a separate recovery event, not a continuation of the timed-out turn. The hash chain includes both the turn entry and the recovery event in sequence.

### Seam C: SPEC-03 → SPEC-04

The lifecycle layer aggregates all tool-call outcomes within a single turn and produces one DHF write per completed turn. Upon turn completion, SPEC-03 constructs a `DhfTurnContext` (defined in SPEC-04) containing all tool-call results for that turn and hands it to `DhfLedger.AppendTurnAsync`. The DHF layer never observes individual lifecycle transitions directly; it receives complete, aggregated turn records only. The hash chain covers turn-level entries and recovery events.

**Incomplete turns (crash, disconnect, kill mid-turn):** Turns that fail to complete do not produce DHF turn entries, because SPEC-03 has not yet constructed the `DhfTurnContext`. On session closure (detected via heartbeat timeout or reconnect with a new AddinSessionId), SPEC-04 records a session-closure event noting the reason. On the next session start, SPEC-03 must perform state reconciliation (feature-tree comparison against last recorded turn) before opening the first new turn. If the reconciliation detects model state that diverges from the DHF record, SPEC-03 records a reconciliation event via `DhfLedger.AppendRecoveryEventAsync`. The undo group for an incomplete execution turn was never closed; SolidWorks behavior for unclosed `UndoObjectScope` is documented in SPEC-02.

### Seam D: SPEC-05 → SPEC-03

The agent loop submits typed tool calls to the lifecycle layer and awaits terminal outcomes. The agent never touches the WebSocket, never touches the STA queue, never writes to the DHF. It is a pure consumer of the lifecycle surface. SPEC-05 has no compile-time dependency on SPEC-04; all DHF interaction is mediated by SPEC-03.

### Seam E: SPEC-05 → SPEC-04 (indirect, via SPEC-03)

The agent loop opens turns but does not write DHF entries directly. SPEC-03 is the sole DHF writer. This keeps turn construction in one place. SPEC-05 has no direct assembly reference to SPEC-04; the dependency is purely indirect through SPEC-03's aggregation role.

### Seam F: SPEC-06 → SPEC-04

The viewer queries `DhfRepository` (defined in SPEC-04) read-only. No write paths exist from the viewer.

## 3. Mandatory vs Stretch (Top-Level)

Mandatory scope covers the 9-tool demo (`new_part`, `start_sketch`, `draw_rectangle`, `draw_circle`, `add_dimension`, `exit_sketch`, `extrude`, `screenshot`, `get_feature_tree`), text input via ConsoleInputService, undo groups, per-turn DHF with hash chain, minimal viewer timeline. Per-module mandatory/stretch lists live in each module spec.

**Descope priority (LIFO):** Installer, Demo 2, viewer polish, DHF extraction, error recovery UX (user-facing retry messaging and graceful degradation prompts), additional grounding tools beyond the mandatory two (`screenshot`, `get_feature_tree`), Sonnet 4.6 decoration extraction, patterns, voice, Docker Postgres (→ EmbeddedPostgres), all stretch. **Never descoped:** the 9 mandatory tools (including `screenshot` and `get_feature_tree`), per-turn DHF, hash chain at entry level, minimal viewer timeline, undo groups, text input, post-timeout state reconciliation (Seam B mandatory recovery), ACC-01/02/03/04/08/10/12 acceptance tests.

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

### Integration and E2E Test Project

Cross-module acceptance tests (ACC-01, ACC-04, and any other tests requiring multiple module assemblies or external dependencies) are owned by `BSVoiceCAD.Tests.Integration`. This project is permitted to reference all module assemblies and requires the following runtime dependencies: a SolidWorks instance (or a documented test double for CI), a PostgreSQL database (EmbeddedPostgres permitted), and either a live Anthropic API key or a recorded-response test double. ACC tests that can be verified within a single module (ACC-02, ACC-03, ACC-08, ACC-10, ACC-12) may live in that module's own test project, but must also have an integration-level smoke test in `BSVoiceCAD.Tests.Integration` to verify cross-module wiring.

## 4. Top-Level SDRs (Decisions That Touch Multiple Specs)

SDRs local to a single module live in that module's spec. These cross-cut.

**SDR-TL-001: All-.NET.** Add-in .NET Framework 4.8 (SW COM requirement), orchestrator .NET 8, shared Protocol .NET Standard 2.0 with `LangVersion=latest`. Locked.

**SDR-TL-002: PostgreSQL 16 with EmbeddedPostgres fallback.** No SQLite. Locked.

**SDR-TL-003: Direct Anthropic API.** Opus 4.7 primary. Sonnet 4.6 for decoration extraction is stretch scope — not required for the mandatory 9-tool demo; if descoped, all tool schemas and prompts use Opus 4.7 only. No Bedrock for hackathon. Locked (Opus 4.7 mandatory; Sonnet 4.6 stretch).

**SDR-TL-004: Code license MIT; spec classification Proprietary.** Locked.

**SDR-TL-005: Hackathon selection gate.** Application submitted 2026-04-17; awaiting selection notification from Anthropic hackathon organizers. Eligibility-neutral build work (protocol types, schema, CLAUDE.md, test infrastructure) proceeds now. Submission-affecting work (video recording, public repo publication, LICENSE commit) blocks on confirmed selection. If not selected, the build still produces a working SolidWorks voice copilot that serves as the reference implementation for the Medtronic partnership pitch.

**SDR-TL-006: DHF regulatory framing.** Positioned as "design audit log foundation for future 21 CFR 820.30 / Part 11 alignment." **This system is NOT 21 CFR Part 11 compliant and MUST NOT be represented as such to customers, partners, or regulators.** The gap-analysis table in SPEC-04 enumerates specific Part 11 requirements not yet met (e.g., electronic signatures, validated system status, audit trail completeness under 21 CFR 11.10). Governance owner for gap-analysis maintenance and any compliance claims: Dr. Joseph Wilde (BoundState AI CEO). Any customer-facing artifact (pitch deck, demo, documentation) that references 21 CFR or Part 11 must include the non-compliance disclaimer verbatim.

**SDR-TL-007: camelCase JSON property policy; PascalCase enum string values.** All wire formats and DHF JSONB payloads use camelCase property names. All enums serialize as PascalCase strings via `JsonStringEnumConverter`. Enforcement is split by ownership: wire-format serialization rules are enforced build-breaking in SPEC-01 tests; DHF JSONB serialization rules are enforced build-breaking in SPEC-04 tests. Both test suites verify round-trip fidelity for their respective type sets.

**SDR-TL-008: Opus 4.7 plays two roles (mandatory), not three.** Vision interpreter (context-only; enriches agent prompt with structured observations from recent screenshot) and tool-calling agent. Same vendor, same model family, two uses. No multi-vendor consensus layer at runtime. The system relies on single-model inference with extended thinking enabled; the DHF provides external traceability including ambiguity events. No testable "self-verification" contract is assumed at the model level. (Sonnet 4.6 decoration extraction, if in scope, is a third optional role — see SDR-TL-003.)

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
- **Turn type:** One of `execution` (produces geometry and an undo group), `clarification` (agent asks; zero tool calls; no undo group), `confirmation` (agent requests user approval; zero tool calls; no undo group), or `ambiguity_resolution` (clarification turn where voice and vision disagreed on target entity; DHF records both inputs and the resolution).
- **Undo Group:** The SolidWorks UndoObjectScope wrapping all tool calls in one turn. One undo group per execution turn; zero for non-execution turns.
- **AddinSessionId:** UUID generated fresh on each add-in load. Derivation algorithm (UUID version, hash function, input fields, fallback behavior) is specified in SPEC-02. **Epoch semantics (consumed by SPEC-04, authoritative source is this glossary):** A new AddinSessionId signals a new epoch. On epoch change: (1) StateVersion resets to 0; (2) the current DHF session (if any) is closed with reason `superseded_session`; (3) a new DHF session opens on the first AddinReadyMessage carrying the new AddinSessionId. Rotation invariant: AddinSessionId changes if and only if the add-in is reloaded (SolidWorks restart, add-in disable/re-enable, or crash recovery). Two concurrent add-in instances on the same machine produce distinct AddinSessionIds; SPEC-04 must handle the resulting dual-session scenario.
- **StateVersion:** Monotonic per-AddinSessionId counter carried by state-bearing messages. Resets to 0 on new AddinSessionId epoch.
- **Tool-Call Lifecycle State:** Sent → Completed | TimedOut | Cancelled | Abandoned. Terminal states are final.
- **DHF Session:** One row in `dhf.sessions`. Opens on first AddinReadyMessage for a given AddinSessionId. Closes on epoch change (new AddinSessionId arrives), on orchestrator graceful shutdown, or on heartbeat timeout. **Closure reason vocabulary:** `explicit_shutdown` (graceful orchestrator or add-in teardown with a close message), `superseded_session` (new AddinSessionId arrived), `heartbeat_timeout` (heartbeat absence exceeded threshold; root cause — add-in crash, SolidWorks exit, network failure, orchestrator kill — is not distinguishable and is recorded as unknown). If finer-grained cause attribution is needed in the future, SPEC-02 must emit an explicit teardown message before process exit; until then, `heartbeat_timeout` does not claim to know why. Reconnect and dangling-session reconciliation rules are defined in SPEC-04.
- **Recovery Event:** A DHF entry (distinct from a turn entry) recording the outcome of a post-timeout or post-crash reconciliation. Includes: trigger (timeout, crash, disconnect), strategy applied (state refresh or undo-group rollback), success/failure, and any detected model-state divergence. Appended via `DhfLedger.AppendRecoveryEventAsync`. Participates in the hash chain.
- **Quarantine:** A late message received after a terminal lifecycle state is logged and discarded; it never updates session state, never contributes to DHF turn entries, never triggers retries. **Exception:** If a quarantined message provides evidence of post-timeout model mutation (e.g., a late `Completed` result for a `TimedOut` call), the quarantine log entry is referenced by the subsequent mandatory recovery event to preserve audit trail continuity.
- **Vision Context:** Structured observations extracted by Opus 4.7 from a recent SolidWorks viewport screenshot, passed into the agent loop's prompt as enrichment. Not a decision-making component; purely enriches the agent's understanding of what the user can currently see. Defined in SPEC-05.
- **Ambiguity Event:** A clarification turn triggered because the user's utterance (voice transcript) and the current visual/geometric state imply multiple possible targets. Recorded in the DHF as `turn_type = 'ambiguity_resolution'` with both the transcript and the vision context observations preserved.

## 7. Build-Readiness

This document is short on purpose. Its correctness is a function of whether the module boundaries match the actual architecture, not whether it covers every detail. Implementation does not start here; it starts in the per-module specs.

Implementation blockers, in order:
1. SPEC-01 converges (wire protocol locked)
2. SPEC-02 + SPEC-04 converge in parallel (SPEC-04 consumes AddinSessionId epoch semantics from SPEC-00 §6, not from SPEC-02 internals)
3. SPEC-03 converges (depends on all three above)
4. SPEC-05 converges
5. SPEC-06 can start anytime after SPEC-04 converges
6. Integration Oracle pass on SPEC-00
7. Day-0 validation on SolidWorks 2025 SP3+, Anthropic.SDK, Postgres 16; Whisper.net CUDA validated only if voice remains in scope

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*