---
title: "BSVoice-CAD SPEC-00 — Orchestration Contract"
subtitle: "The six-module decomposition; interfaces only, no implementation"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-00 — Orchestration Contract

**Version:** 1.1.0
**Status:** DRAFT — post-Round-1 Oracle revision
**Hackathon application:** Submitted 2026-04-17; awaiting selection notification

## 0. Why This Exists

BSVoice-CAD is six logical modules that interact at well-defined seams. This document defines the module boundaries, the seam contracts between them, cross-cutting architectural decisions (SDRs), shared vocabulary, scope gates, and review ordering. Implementation details live in the per-module specs (SPEC-01 through SPEC-06). Oracle reviews each module spec in isolation; this document is the integration contract that lets those reviews compose without drift.

**Non-goals for this document:** state machine details, record field definitions, SQL schemas, build sequence, timeline. Those live in per-module specs.

## 1. Module Map

| Spec | Module | Owns | Lives In | Depends On |
|------|--------|------|----------|------------|
| SPEC-01 | Wire Protocol | Cross-process types, polymorphic contracts, JSON policy, WebSocket framing contract, version handshake | `BSVoiceCAD.Protocol` | (none — leaf module) |
| SPEC-02 | SolidWorks Adapter | STA command queue, `ISolidWorks*Service` contracts, COM-to-`ToolError` mapping | `BSVoiceCAD.Addin` | SPEC-01 |
| SPEC-03 | Tool-Call Lifecycle | Call state machine, timeouts, cancel protocol, quarantine rules, per-turn DHF aggregation | `BSVoiceCAD.Orchestrator.Lifecycle` + `BSVoiceCAD.Addin.Bridge` | SPEC-01, SPEC-02, SPEC-04 |
| SPEC-04 | DHF Ledger | Schema, hash model, append logic, AddinSessionId epoch, DHF session lifecycle | `BSVoiceCAD.Orchestrator.Dhf` + `BSVoiceCAD.Orchestrator.Database` | SPEC-01 (message shapes only) |
| SPEC-05 | Agent Loop + Input | User input, confirmation flow, Opus 4.7 tool-calling, vision context, retry correlation, system prompt | `BSVoiceCAD.Orchestrator.Agent` + `BSVoiceCAD.Orchestrator.Input` | SPEC-01, SPEC-03 |
| SPEC-06 | Viewer | Timeline rendering, DHF read API, integrity endpoints | `BSVoiceCAD.Orchestrator.Viewer` | SPEC-04 (read-only) |

**Dependency direction is strictly downward.** SPEC-01 depends on nothing. SPEC-06 depends on SPEC-04 only. Circular references are a defect.

## 2. Seams (Where The Modules Touch)

Each seam is one-directional. Violations are detected by `NetArchTest` rules living in a dedicated architecture-test project (`BSVoiceCAD.Tests.Architecture`) that is permitted to reference all module assemblies. Per-module test projects test only their own module's internal invariants.

### Seam A: SPEC-01 → everyone

Protocol types are consumed by every other module. No module defines its own cross-process wire types. Architecture rule: no cross-process DTO types (types serialized over the WebSocket transport) exist outside `BSVoiceCAD.Protocol.*` namespaces. Internal module-boundary types (e.g., `DhfTurnContext` in SPEC-04) are permitted provided they do not cross the WebSocket boundary.

The cross-process transport between the .NET Framework 4.8 add-in and the .NET 8 orchestrator is a WebSocket connection with JSON message framing. The full transport contract — including connection lifecycle, error semantics, reconnect behavior, and threading guarantees — is defined in SPEC-01.

### Seam B: SPEC-03 → SPEC-02

The tool-call lifecycle dispatches `IToolInput` to the SolidWorks adapter and expects `IToolResult` or `ToolError` back. The adapter never owns lifecycle state; the lifecycle never owns COM threading. SPEC-02 provides a synchronous method surface; SPEC-03 wraps each call in its own timeout and cancel semantics.

**Cancellation boundary:** Cancellation is effective only before a COM call is dispatched to the STA thread. Once a synchronous COM call is in flight, it runs to completion; the STA thread cannot be safely interrupted. A call whose orchestrator-side timeout expires while COM execution is in progress is recorded as `TimedOut`, but the COM operation may still complete and mutate the model. SPEC-03 defines a mandatory post-timeout recovery flow (state refresh or undo-group rollback) before subsequent turns may proceed. Details in SPEC-03.

### Seam C: SPEC-03 → SPEC-04

The lifecycle layer aggregates all tool-call outcomes within a single turn and produces one DHF write per completed turn. Upon turn completion, SPEC-03 constructs a `DhfTurnContext` (defined in SPEC-04) containing all tool-call results for that turn and hands it to `DhfLedger.AppendTurnAsync`. The DHF layer never observes individual lifecycle transitions directly; it receives complete, aggregated turn records only. The hash chain covers turn-level entries.

### Seam D: SPEC-05 → SPEC-03

The agent loop submits typed tool calls to the lifecycle layer and awaits terminal outcomes. The agent never touches the WebSocket, never touches the STA queue, never writes to the DHF. It is a pure consumer of the lifecycle surface. SPEC-05 has no compile-time dependency on SPEC-04; all DHF interaction is mediated by SPEC-03.

### Seam E: SPEC-05 → SPEC-04 (indirect, via SPEC-03)

The agent loop opens turns but does not write DHF entries directly. SPEC-03 is the sole DHF writer. This keeps turn construction in one place. SPEC-05 has no direct assembly reference to SPEC-04; the dependency is purely indirect through SPEC-03's aggregation role.

### Seam F: SPEC-06 → SPEC-04

The viewer queries `DhfRepository` (defined in SPEC-04) read-only. No write paths exist from the viewer.

## 3. Mandatory vs Stretch (Top-Level)

Mandatory scope covers the 9-tool demo (`new_part`, `start_sketch`, `draw_rectangle`, `draw_circle`, `add_dimension`, `exit_sketch`, `extrude`, `screenshot`, `get_feature_tree`), text input via ConsoleInputService, undo groups, per-turn DHF with hash chain, minimal viewer timeline. Per-module mandatory/stretch lists live in each module spec.

**Descope priority (LIFO):** Installer, Demo 2, viewer polish, DHF extraction, error recovery, grounding tools, patterns, voice, Docker Postgres (→ EmbeddedPostgres), all stretch. Never descoped: the 9 mandatory tools, per-turn DHF, hash chain at entry level, minimal viewer timeline, undo groups, text input, ACC-01/02/03/04/08/10/12 acceptance tests.

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

## 4. Top-Level SDRs (Decisions That Touch Multiple Specs)

SDRs local to a single module live in that module's spec. These cross-cut.

**SDR-TL-001: All-.NET.** Add-in .NET Framework 4.8 (SW COM requirement), orchestrator .NET 8, shared Protocol .NET Standard 2.0 with `LangVersion=latest`. Locked.

**SDR-TL-002: PostgreSQL 16 with EmbeddedPostgres fallback.** No SQLite. Locked.

**SDR-TL-003: Direct Anthropic API.** Opus 4.7 primary, Sonnet 4.6 for decoration extraction. No Bedrock for hackathon. Locked.

**SDR-TL-004: Code license MIT; spec classification Proprietary.** Locked.

**SDR-TL-005: Hackathon selection gate.** Application submitted 2026-04-17; awaiting selection notification from Anthropic hackathon organizers. Eligibility-neutral build work (protocol types, schema, CLAUDE.md, test infrastructure) proceeds now. Submission-affecting work (video recording, public repo publication, LICENSE commit) blocks on confirmed selection. If not selected, the build still produces a working SolidWorks voice copilot that serves as the reference implementation for the Medtronic partnership pitch.

**SDR-TL-006: DHF regulatory framing.** Positioned as "design audit log foundation for future 21 CFR 820.30 / Part 11 alignment." NOT marketed as compliant. Gap-analysis table lives in SPEC-04.

**SDR-TL-007: camelCase JSON property policy; PascalCase enum string values.** All wire formats and DHF JSONB payloads use camelCase property names. All enums serialize as PascalCase strings via `JsonStringEnumConverter`. Both rules are enforced build-breaking in SPEC-01 tests.

**SDR-TL-008: Opus 4.7 plays two roles, not three.** Vision interpreter (context-only; enriches agent prompt with structured observations from recent screenshot) and tool-calling agent. Same vendor, same model family, two uses. No multi-vendor consensus layer at runtime. The system relies on single-model inference with extended thinking enabled; the DHF provides external traceability including ambiguity events. No testable "self-verification" contract is assumed at the model level.

## 5. Review Order

Oracle reviews modules in dependency order so lower-numbered specs stabilize before higher ones consume them.

1. SPEC-00 (this document) — establishes module boundaries
2. SPEC-01 (Wire Protocol) — leaf module, gates everything
3. SPEC-02 (SolidWorks Adapter) — no circular dependency with SPEC-03
4. SPEC-04 (DHF Ledger) — depends only on SPEC-01 message shapes
5. SPEC-03 (Tool-Call Lifecycle) — depends on SPEC-01, SPEC-02, SPEC-04
6. SPEC-05 (Agent Loop + Input) — depends on SPEC-01, SPEC-03
7. SPEC-06 (Viewer) — depends on SPEC-04 only; can be deferred

After all individual passes converge, one integration Oracle pass on SPEC-00 catches any remaining cross-cuts. Expected cross-cut defect count: low, because the seams above define them away.

## 6. Glossary (Terms Used Across Multiple Specs)

- **Turn:** One user utterance + the agent's response, possibly containing 0+ tool calls. Turns are the DHF-entry granularity; one DHF entry per completed turn.
- **Turn type:** One of `execution` (produces geometry and an undo group), `clarification` (agent asks; zero tool calls; no undo group), `confirmation` (agent requests user approval; zero tool calls; no undo group), or `ambiguity_resolution` (clarification turn where voice and vision disagreed on target entity; DHF records both inputs and the resolution).
- **Undo Group:** The SolidWorks UndoObjectScope wrapping all tool calls in one turn. One undo group per execution turn; zero for non-execution turns.
- **AddinSessionId:** UUID generated fresh on each add-in load, deterministically from (machineGuid, swProcessId, loadTimestamp). Derivation algorithm (UUID version, hash function, timestamp precision, fallback behavior) is specified in SPEC-02. Persists for the duration of one SolidWorks session. Marks the StateVersion epoch and the DHF session epoch.
- **StateVersion:** Monotonic per-AddinSessionId counter carried by state-bearing messages. Resets to 0 on new AddinSessionId.
- **Tool-Call Lifecycle State:** Sent → Completed | TimedOut | Cancelled | Abandoned. Terminal states are final.
- **DHF Session:** One row in `dhf.sessions`. Opens on first AddinReadyMessage for a given AddinSessionId. Closes when a different AddinSessionId arrives, on orchestrator graceful shutdown, or on heartbeat timeout (details in SPEC-04). Ungraceful termination (add-in crash, SolidWorks exit, orchestrator kill, WebSocket disconnect) is detected via heartbeat absence; the resulting session closure records the termination reason. Reconnect and dangling-session reconciliation rules are defined in SPEC-04.
- **Quarantine:** A late message received after a terminal lifecycle state is logged and discarded; it never updates session state, never contributes to DHF, never triggers retries.
- **Vision Context:** Structured observations extracted by Opus 4.7 from a recent SolidWorks viewport screenshot, passed into the agent loop's prompt as enrichment. Not a decision-making component; purely enriches the agent's understanding of what the user can currently see. Defined in SPEC-05.
- **Ambiguity Event:** A clarification turn triggered because the user's utterance (voice transcript) and the current visual/geometric state imply multiple possible targets. Recorded in the DHF as `turn_type = 'ambiguity_resolution'` with both the transcript and the vision context observations preserved.

## 7. Build-Readiness

This document is short on purpose. Its correctness is a function of whether the module boundaries match the actual architecture, not whether it covers every detail. Implementation does not start here; it starts in the per-module specs.

Implementation blockers, in order:
1. SPEC-01 converges (wire protocol locked)
2. SPEC-02 + SPEC-04 converge in parallel (they don't touch each other)
3. SPEC-03 converges (depends on all three above)
4. SPEC-05 converges
5. SPEC-06 can start anytime after SPEC-04 converges
6. Integration Oracle pass on SPEC-00
7. Day-0 validation on SolidWorks 2025 SP3+, Anthropic.SDK, Postgres 16; Whisper.net CUDA validated only if voice remains in scope

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*