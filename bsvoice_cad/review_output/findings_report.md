# ORACLE Review Report

**Document:** D:\BoundState\specs\bsvoice_cad\SPEC-00_ORCHESTRATION_CONTRACT.md
**Session:** 20260418-122821
**Rounds:** 2
**Converged:** No

## Metrics

- Accepted critical defects: 2
- Accepted high defects: 10
- Total accepted defects: 23
- Total rejected claims: 2
- Preference-only findings: 0
- Duplicate rate: 0.0%
- Rounds to stability: 2
- Converged: False

## Round 1

### [CRITICAL] DHF write granularity contradicts per-turn ledger granularity (R1-ISSUE-001)
**Problem:** Seam C states 'Every terminal lifecycle transition produces a DHF write,' but the Glossary defines the DHF-entry granularity as one per turn, and a turn may contain 0+ tool calls (each with its own terminal lifecycle transition). These two rules are mutually exclusive without an aggregation or disambiguation rule.
**Fix:** Choose one canonical persistence unit. If DHF is per turn, define how multiple tool-call outcomes are aggregated into one append and what the hash-chain covers. If DHF is per tool call, update the Glossary, viewer contract, and acceptance-test expectations accordingly. Either way, update Seam C to be unambiguous.
**Sources:** Fran, Codex

### [CRITICAL] Timeout/cancel semantics are unimplementable for synchronous STA COM calls and create CAD-state/DHF divergence (R1-ISSUE-002)
**Problem:** Once a synchronous COM call is dispatched on the STA thread, SPEC-03 cannot reliably cancel or interrupt it. A call marked TimedOut or Cancelled by the orchestrator may continue mutating the SolidWorks model. The Quarantine rule discards late completions, but the CAD state has already changed, breaking undo-group guarantees, DHF audit integrity, and downstream command assumptions.
**Fix:** Define the exact cancellation boundary: pre-dispatch only, cooperative cancellation points inside SPEC-02, or a mandatory post-timeout recovery/lockout flow before subsequent turns. For mutating calls, add a mandatory post-timeout state-refresh or rollback step, or explicitly remove timeout/cancel guarantees for in-flight synchronous COM execution and document the operational consequence.
**Sources:** Fran, Codex

### [HIGH] SPEC-03 dependency on SPEC-04 is inconsistent across the document (R1-ISSUE-003)
**Problem:** Two related but distinct inconsistencies exist. (1) The Module Map lists SPEC-03's dependency as 'SPEC-01' only, but Seam C and Section 5 both state SPEC-03 depends on SPEC-04 (it emits a DhfTurnContext defined there). (2) SPEC-05 is listed as depending on SPEC-04 in the Module Map and Section 5, yet Seam D says 'The agent never touches the DHF' and Seam E labels the relationship 'indirect via SPEC-03.' These contradictions make build ordering, architecture tests, and review sequencing unenforceable.
**Fix:** Correct the Module Map to list SPEC-04 as a dependency of SPEC-03. For SPEC-05, decide whether it has a direct compile-time reference to SPEC-04 (e.g., for read-only query types) or only an indirect runtime relationship via SPEC-03, and make Seam E, the Module Map, and Section 5 consistent with that decision.
**Sources:** Codex, Fran

### [HIGH] No-DTO-outside-Protocol rule is violated by DhfTurnContext defined in SPEC-04 (R1-ISSUE-004)
**Problem:** Seam A declares 'no DTO types exist outside BSVoiceCAD.Protocol.* namespaces,' but Seam C introduces DhfTurnContext as a cross-module data carrier defined in SPEC-04. This makes the architecture rule either overstated or immediately violated, rendering NetArchTest enforcement ambiguous.
**Fix:** Either (a) narrow the rule to cross-process wire DTOs only and explicitly permit internal module-boundary models like DhfTurnContext, or (b) move all cross-module contract types into SPEC-01/Protocol and update Seam C accordingly. Document the distinction clearly so NetArchTest rules can be written unambiguously.
**Sources:** Codex, Fran

### [HIGH] Shared BSVoiceCAD.Orchestrator.Agent namespace claimed by both SPEC-03 and SPEC-05 (R1-ISSUE-005)
**Problem:** The Module Map assigns BSVoiceCAD.Orchestrator.Agent to both SPEC-03 (Tool-Call Lifecycle) and SPEC-05 (Agent Loop + Input). Shared namespace ownership creates governance conflicts during parallel implementation and makes NetArchTest seam rules unenforceable.
**Fix:** Partition the Agent namespace into distinct sub-namespaces owned by exactly one spec (e.g., BSVoiceCAD.Orchestrator.Agent.Lifecycle for SPEC-03 and BSVoiceCAD.Orchestrator.Agent.Loop for SPEC-05). Update the Module Map and all seam descriptions to reference the partitioned namespaces.
**Sources:** Grok

### [HIGH] DHF session closure contract omits ungraceful termination paths (R1-ISSUE-006)
**Problem:** The DHF Session glossary entry defines closure only for clean replacement (new AddinSessionId) or clean orchestrator shutdown. It is silent on add-in crash, SolidWorks exit, orchestrator kill, WebSocket disconnect, or partial restart. Sessions can remain logically open indefinitely, making viewer/integrity logic and restart behavior undefined.
**Fix:** Define disconnect detection (heartbeat/timeout), who is responsible for closing a dangling session, how the closure reason is recorded in the ledger, and how reconnects with the same or a new AddinSessionId are reconciled. This contract should live in SPEC-04 with a reference here.
**Sources:** Codex, Fran

### [HIGH] Stretch feature (Whisper.net CUDA / voice) listed as mandatory day-0 validation dependency (R1-ISSUE-007)
**Problem:** Section 3 explicitly lists 'voice' as descope-eligible stretch work, but Section 7 includes 'Whisper.net CUDA' in the mandatory day-0 validation list without qualification. This creates a hidden schedule dependency on a descopable feature.
**Fix:** Remove Whisper.net CUDA from the mandatory day-0 validation list, or add an explicit conditional qualifier ('only if voice remains in scope'). Consider splitting Section 7 into mandatory and conditional validation tracks.
**Sources:** Codex, Fran

### [HIGH] NetArchTest seam rules placed in lower-numbered spec test projects require upward assembly references (R1-ISSUE-008)
**Problem:** Section 2 states seam violations are detected by NetArchTest rules 'living in the test project of the lower-numbered spec.' To verify that higher-numbered assemblies do not violate seams, those test projects must reference the higher-numbered assemblies, creating upward references that contradict the stated leaf-module model and prevent independent build/review of lower-numbered specs.
**Fix:** Move all cross-seam architecture validation rules to a dedicated integration/architecture test project that is explicitly permitted to reference all module assemblies. Lower-numbered spec test projects should only test their own module's internal rules.
**Sources:** Codex

### [HIGH] Cross-process transport mechanism between .NET 4.8 add-in and .NET 8 orchestrator is unspecified (R1-ISSUE-009)
**Problem:** The document references WebSocket and STA queue in passing but never names the actual IPC transport, serialization protocol, or error-mapping semantics between the .NET Framework 4.8 SolidWorks add-in and the .NET 8 orchestrator. Without this, the synchronous dispatch described in Seam B and the one-directional seams are unimplementable and untestable.
**Fix:** Add an explicit transport definition to SPEC-01 (e.g., named pipes, gRPC, JSON-RPC over stdio, or WebSocket with a defined framing protocol) including error semantics, threading guarantees, and reconnect behavior. Reference it from every cross-process seam.
**Sources:** Grok

### [MEDIUM] Mandatory acceptance tests ACC-01/02/03/04/08/10/12 are cited without top-level definitions or cross-references (R1-ISSUE-010)
**Problem:** Section 3 declares specific ACC IDs as never-descopable scope gates, but Section 0 explicitly excludes test names from this document and provides no cross-reference to where those IDs are defined. The top-level scope gate is unusable without knowing what pass/fail criteria the IDs represent.
**Fix:** Add a one-line canonical reference for each ACC ID (e.g., 'ACC-01: defined in SPEC-02 §X') or replace the opaque IDs with named capabilities meaningful at the orchestration level. A lookup table in an appendix would suffice.
**Sources:** Fran

### [MEDIUM] AddinSessionId UUID derivation algorithm is underspecified (R1-ISSUE-011)
**Problem:** The Glossary defines AddinSessionId as 'UUID generated fresh on each add-in load, deterministically from (machineGuid, swProcessId, loadTimestamp)' but omits the UUID version/hash algorithm, timestamp precision and timezone normalization, and fallback behavior when machineGuid is unavailable. Collisions or environment-specific differences would corrupt the DHF session epoch and StateVersion reset behavior.
**Fix:** Specify the UUID derivation algorithm (e.g., UUID v5 with a fixed namespace UUID, SHA-256 of concatenated inputs), required timestamp precision and UTC normalization, the source of machineGuid, and fallback behavior when it is unavailable. This detail belongs in SPEC-02 with a reference here.
**Sources:** Codex

### [MEDIUM] Opus 4.7 'internal self-verification at xhigh effort' claimed as consensus mechanism with no observable contract (R1-ISSUE-012)
**Problem:** SDR-TL-008 asserts that Opus 4.7's internal self-verification at xhigh effort serves as the model-level consensus mechanism, but defines no observable inputs, outputs, logs, or failure criteria. Reviewers and testers cannot verify when consensus was invoked, whether it succeeded, or how it should affect ambiguity handling and DHF records.
**Fix:** Either remove the consensus claim and treat the design as single-model inference with no special verification contract, or define an explicit validation contract with measurable outputs (e.g., required DHF fields, confidence thresholds, logging of extended-thinking token counts) that can be tested.
**Sources:** Fran

### [LOW] Document scope exceeds its stated 'names nothing else' boundary (R1-ISSUE-013)
**Problem:** Section 0 states the document 'names the modules, names the seams, and names nothing else,' but Sections 3–7 introduce mandatory scope lists, SDRs, regulatory framing, review ordering, a detailed glossary, and build blockers. While this content is useful, it contradicts the stated scope boundary.
**Fix:** Either rewrite Section 0 to accurately describe the document's actual scope (integration contract + cross-cutting decisions + glossary), or extract Sections 3–7 into a separate ADR/decision log and keep SPEC-00 to seams only. The former is lower-risk given the content is genuinely useful here.
**Sources:** Grok

### [LOW] Brittle dependency on unverified Opus 4.7 vision and self-verification capabilities (R1-ISSUE-014)
**Problem:** Core seams and DHF schema assume specific model capabilities (structured vision output, reliable self-verification at xhigh effort) that are asserted but not contractually guaranteed by the model provider. If these behaviors are inconsistent, the ambiguity_resolution turn type and vision context enrichment may not function as designed.
**Fix:** Define explicit fallback behavior when vision output is unstructured or self-verification is unavailable. Decouple the architecture from any specific model version by specifying the required output contract (schema) rather than the mechanism.
**Sources:** Grok

### Coordinator Decisions
- **R1-ISSUE-001**: accepted_defect — Seam C's 'every terminal lifecycle transition produces a DHF write' directly contradicts the glossary's 'turns are the DHF-entry granularity' when a turn contains multiple tool calls. Rewrote Seam C t
- **R1-ISSUE-002**: accepted_defect — Synchronous COM calls on the STA thread cannot be cancelled mid-execution. The original text implied SPEC-03 could wrap each call in cancel semantics, which is physically impossible once dispatched. A
- **R1-ISSUE-003**: accepted_defect — Two contradictions confirmed. (1) Module Map listed SPEC-03 depending on SPEC-01 only, but Seam C and Section 5 show it depends on SPEC-02 and SPEC-04 as well. Fixed Module Map to SPEC-01, SPEC-02, SP
- **R1-ISSUE-004**: accepted_defect — The blanket rule 'no DTO types exist outside BSVoiceCAD.Protocol.* namespaces' is violated by DhfTurnContext, which is a legitimate internal module-boundary type that never crosses the WebSocket bound
- **R1-ISSUE-005**: accepted_defect — Two specs claiming the same namespace is a governance and testability defect. Renamed SPEC-03's namespace to BSVoiceCAD.Orchestrator.Lifecycle (distinct from SPEC-05's BSVoiceCAD.Orchestrator.Agent). 
- **R1-ISSUE-006**: accepted_defect — DHF session closure was defined only for clean paths. Added heartbeat timeout as a closure trigger, noted that ungraceful termination is detected via heartbeat absence with the termination reason reco
- **R1-ISSUE-007**: accepted_defect — Clear contradiction: voice is explicitly descope-eligible stretch, but Whisper.net CUDA was in the mandatory day-0 validation list. Changed to conditional: 'Whisper.net CUDA validated only if voice re
- **R1-ISSUE-008**: accepted_defect — Placing cross-seam arch rules in lower-numbered test projects requires upward assembly references, contradicting the leaf-module build model. Moved all cross-seam NetArchTest rules to a dedicated BSVo
- **R1-ISSUE-009**: accepted_defect — The cross-process transport was mentioned in passing (WebSocket in Seam D) but never formally owned or defined. Added 'WebSocket framing contract' to SPEC-01's Owns column and added an explicit transp
- **R1-ISSUE-010**: accepted_defect — ACC IDs were used as never-descopable scope gates but had no definitions or cross-references in this document. Added a cross-reference table in Section 3 mapping each ACC ID to a capability summary an
- **R1-ISSUE-011**: accepted_defect — The glossary entry described inputs to the derivation but omitted the algorithm. At SPEC-00 level, the right fix is a forward reference rather than specifying the algorithm here. Added explicit refere
- **R1-ISSUE-012**: accepted_defect — The claim that Opus 4.7's 'internal self-verification at xhigh effort' constitutes a consensus mechanism is untestable and unverifiable. Rewrote SDR-TL-008 to describe the design as single-model infer
- **R1-ISSUE-013**: accepted_defect — Section 0 claimed 'names nothing else' but Sections 3-7 contain SDRs, scope gates, glossary, review order, and build-readiness content. Rewrote Section 0 to accurately describe the document's actual s
- **R1-ISSUE-014**: rejected_claim — The architecture already mitigates this risk at the SPEC-00 level: vision is explicitly labeled 'enrichment, not a decision-making component,' so degraded vision output does not break the control flow

## Round 2

### [CRITICAL] COM timeout creates unrecorded model mutations: quarantine rule erases evidence, recovery is mandatory but descopable, and turn completion boundary is undefined (R1-ISSUE-002)
**Problem:** Three interlocking defects compound the COM timeout hazard: (1) A timed-out COM call that later completes and mutates the model has its completion quarantined and excluded from DHF, producing an audit trail that omits a real mutation. (2) The post-timeout recovery flow is declared mandatory in Seam B but 'error recovery' appears in the LIFO descope list as stretch, allowing a team to legally remove the safety gate. (3) The contract does not define when a timed-out turn is 'complete' for DHF purposes — at timeout, after quarantine, or after recovery — leaving hash-chain ordering and replay semantics implementation-defined in the highest-risk path.
**Fix:** 1) Require that any post-timeout COM completion that may imply model mutation be captured in DHF as an out-of-band recovery event before the next turn opens. 2) Split 'error recovery' in the descope list into mandatory-safety (post-timeout state reconciliation) and optional-UX (user-facing retry messaging); mark the former never-descopable. 3) Define the exact DHF close point for timeout cases and whether recovery outcome is stored in the same turn entry, a follow-up entry, or a separate recovery event. 4) Specify the selection rule between 'state refresh' and 'undo-group rollback' and the observable success criterion for each.
**Sources:** Grok, Fran, Fran, Fran, Codex

### [HIGH] SPEC-04 has an undeclared semantic dependency on SPEC-02 (AddinSessionId), invalidating the stated parallel-convergence and dependency map (R1-ISSUE-003)
**Problem:** The Module Map declares SPEC-04 depends only on SPEC-01 message shapes, and Section 7 states SPEC-02 and SPEC-04 can converge in parallel because 'they don't touch each other.' However, SPEC-04's session epoching, StateVersion reset, and reconciliation semantics all depend on how SPEC-02 generates and rotates AddinSessionId — a derivation algorithm explicitly deferred to SPEC-02. The dependency map is incomplete and the review/build order is unsound.
**Fix:** Either (a) move the AddinSessionId semantic contract (generation algorithm, rotation rules, epoch semantics) into SPEC-00 or SPEC-01 so SPEC-04 can depend on it without depending on SPEC-02, or (b) explicitly declare SPEC-04 depends on SPEC-02 and update the dependency table, Module Map, and review order accordingly.
**Sources:** Fran

### [HIGH] DHF session termination reason attribution overclaims what heartbeat absence can observe (R1-ISSUE-006)
**Problem:** The glossary states ungraceful termination is 'detected via heartbeat absence' and that 'the resulting session closure records the termination reason,' listing four distinct causes (add-in crash, SolidWorks exit, orchestrator kill, WebSocket disconnect). Heartbeat absence cannot distinguish among these causes, so the system will record cause codes it cannot actually observe, producing false audit data. Additionally, no top-level invariants are given for concurrent reconnect races or duplicate AddinSessionId scenarios that could corrupt hash-chain integrity.
**Fix:** Reduce closure reason vocabulary to observable categories: explicit-shutdown, superseded-session, and heartbeat-timeout-unknown. If finer attribution is desired, specify the additional out-of-band signals required (e.g., OS process exit codes, explicit teardown messages) and which module is responsible for emitting them. Add at least one adversarial scenario (dual add-in instances, forced disconnect during COM call) as a required SPEC-04 invariant.
**Sources:** Fran, Grok

### [HIGH] Grounding tools (screenshot, get_feature_tree) are simultaneously mandatory and stretch (R2-ISSUE-001)
**Problem:** Section 3 mandatory scope explicitly includes 'screenshot' and 'get_feature_tree' in the 9-tool demo, and ACC-08 ('Screenshot and feature-tree grounding return valid data') is listed as never-descopable. The same section's LIFO descope list includes 'grounding tools ... all stretch.' Implementers cannot determine whether these capabilities are required for the first shippable build.
**Fix:** Remove screenshot/get_feature_tree from the stretch bucket, or redefine 'grounding tools' in the descope list to explicitly exclude the two ACC-08 capabilities and clarify what 'grounding tools' does refer to in that context.
**Sources:** Codex

### [HIGH] Cross-module acceptance tests (ACC-01/02/03/04/08/10/12) have no executable home and cannot be enforced in CI (R1-ISSUE-010)
**Problem:** The document forbids per-module test projects from spanning modules, but defines no integration/E2E test project or harness that owns the never-descopable ACC IDs. These tests require multiple modules and external dependencies (SolidWorks, WebSocket transport, DB, model client) and cannot be placed in any existing test project as described.
**Fix:** Add a dedicated integration/E2E test project (e.g., BSVoiceCAD.Tests.Integration), state which ACC IDs it owns, define its allowed assembly references, and specify its runtime dependency requirements (SolidWorks instance, Postgres, Anthropic API or test double).
**Sources:** Codex

### [HIGH] SPEC-01 tests cannot enforce DHF JSONB serialization policy without violating module boundaries (R1-ISSUE-008)
**Problem:** SDR-TL-007 assigns enforcement of both wire-format and DHF JSONB payload serialization to SPEC-01 tests. DHF JSONB payloads are owned by SPEC-04. Enforcing SPEC-04 serialization in SPEC-01 tests either requires SPEC-01 to take an undeclared dependency on SPEC-04 (violating the architecture) or leaves DHF serialization unenforced where it is produced.
**Fix:** Split enforcement by ownership: keep wire-format serialization tests in SPEC-01 and move DHF JSONB serialization tests to SPEC-04 or the shared architecture-test project with explicit cross-module scope declared.
**Sources:** Codex

### [HIGH] Module-to-assembly mapping is too vague to implement NetArchTest rules, especially for SPEC-03's split namespace (R2-ISSUE-002)
**Problem:** The Module Map uses namespace-like labels (e.g., 'BSVoiceCAD.Orchestrator.Lifecycle + BSVoiceCAD.Addin.Bridge') while Section 2 uses assembly-level enforcement language. NetArchTest rules operate on assemblies, not namespaces. SPEC-03 is split across orchestrator and add-in bridge namespaces in different runtimes (.NET 8 and .NET Framework 4.8), making it impossible to write deterministic arch rules without knowing the exact project/assembly boundaries.
**Fix:** List the exact .csproj/assembly names for each spec, or explicitly define the namespace sets and assembly boundaries that ArchTests must enforce. For SPEC-03's cross-runtime split, clarify which portion lives in which runtime and how the arch-test project references both.
**Sources:** Codex

### [HIGH] No top-level contract for turns that never reach completion (crash, disconnect, kill mid-turn) (R2-ISSUE-003)
**Problem:** The document defines DHF behavior only for 'completed turns' but does not state what happens to abandoned or in-flight turns during crash, disconnect, kill, or shutdown. SPEC-03 and SPEC-04 can implement incompatible recovery behavior, and replay/integrity checks will be ambiguous when failures occur mid-turn.
**Fix:** Define the failure-path contract for incomplete turns at the SPEC-00 seam level: whether to append an aborted-turn record, emit a separate session event, or guarantee no record — and specify how that is reconciled on restart/reconnect.
**Sources:** Codex

### [MEDIUM] NetArchTest enforcement claim overclaims: runtime seam failures (WebSocket, threading, serialization) are not covered (R2-ISSUE-004)
**Problem:** The document states seam violations are 'detected by NetArchTest rules,' but NetArchTest is a compile-time dependency checker. It cannot detect runtime WebSocket behavior, cross-runtime serialization mismatches, reconnect semantics, or threading violations — which are the riskiest integration behaviors in this architecture.
**Fix:** Limit NetArchTest claims to compile-time dependency enforcement. Add a separate requirement for contract/integration tests covering transport behavior, serialization round-trips, reconnect semantics, and threading invariants, and assign them to the integration test project.
**Sources:** Fran

### [MEDIUM] Sonnet 4.6 role is contradicted between SDR-TL-003 and SDR-TL-008; 'decoration extraction' is undefined (R1-ISSUE-012)
**Problem:** SDR-TL-003 states 'Sonnet 4.6 for decoration extraction.' SDR-TL-008 states 'Opus 4.7 plays two roles, not three' and describes only vision interpreter and tool-calling agent. It is unclear whether Sonnet 4.6 remains in scope, what 'decoration extraction' means, which module owns it, and whether it is mandatory or stretch.
**Fix:** Reconcile the two SDRs: either remove Sonnet 4.6 from SDR-TL-003 and confirm it is out of scope, or define 'decoration extraction,' assign it to a module, and classify it as mandatory or stretch.
**Sources:** Codex

### [MEDIUM] Regulatory framing creates compliance misuse risk without governance owner or binding non-compliance language (R1-ISSUE-006)
**Problem:** SDR-TL-006 positions DHF as a '21 CFR 820.30 / Part 11 alignment foundation' while disclaiming compliance, but no governance owner is assigned for the gap-analysis or legal review. The Medtronic partnership pitch context increases the risk that customers treat the audit log as Part 11-ready based on the 'foundation' language.
**Fix:** Either remove all 21 CFR / Part 11 references from top-level SDRs, or assign an explicit governance owner for the gap-analysis and add binding non-compliance language that survives per-module decomposition (e.g., a required disclaimer in any customer-facing artifact).
**Sources:** Grok

### [MEDIUM] Single-model assumption for vision and ambiguity events creates untestable dependency on unreleased model behavior (R1-ISSUE-014)
**Problem:** The architecture assumes Opus 4.7 (an unreleased 2026 model) will reliably produce structured vision observations and ambiguity_resolution turns. Removal of the consensus layer plus deferral of details to SPEC-05 creates an untestable dependency. Adversarial scenarios (model behavior shift, rate limiting, deprecation, vision misinterpretation) break the turn-type taxonomy and hash-chain integrity.
**Fix:** Define an abstract model-output contract (expected structured output schema, failure modes, fallback behavior) at the SPEC-00 or SPEC-05 level, and require synthetic test doubles that can simulate model failures for integration testing.
**Sources:** Grok

### Coordinator Decisions
- **R1-ISSUE-002**: accepted_defect — Three interlocking defects confirmed: (1) Quarantine rule discards post-timeout COM completions from DHF even if they mutated the model — audit gap. (2) 'error recovery' appears in the LIFO descope li
- **R1-ISSUE-003**: accepted_defect — This is a new finding (distinct from the Round 1 R1-ISSUE-003 about SPEC-03/SPEC-05 dependencies). SPEC-04 semantically depends on AddinSessionId epoch rotation behavior defined in SPEC-02, but the Mo
- **R1-ISSUE-006**: accepted_defect — Two aspects under this ID, both accepted. (Termination): Heartbeat absence cannot distinguish among add-in crash, SolidWorks exit, orchestrator kill, and WebSocket disconnect, so recording specific te
- **R2-ISSUE-001**: accepted_defect — Clear contradiction: screenshot and get_feature_tree are in the mandatory 9-tool demo and ACC-08 is never-descopable, but 'grounding tools' appears in the LIFO descope list. Fix: changed descope list 
- **R1-ISSUE-010**: accepted_defect — Round 1 added the ACC cross-reference table but did not address the executable home problem. Per-module test projects are forbidden from spanning modules, but ACC-01 and ACC-04 (at minimum) require mu
- **R1-ISSUE-008**: accepted_defect — Round 1 addressed a different aspect (NetArchTest project placement). SDR-TL-007 still assigns DHF JSONB enforcement to SPEC-01 tests, but DHF payloads are owned by SPEC-04. Enforcing them in SPEC-01 
- **R2-ISSUE-002**: accepted_defect — NetArchTest operates on assemblies, not namespaces. The 'Lives In' column uses namespace-like labels without clarifying whether these are project names, and SPEC-03's cross-runtime split makes enforce
- **R2-ISSUE-003**: accepted_defect — The document defines DHF behavior only for completed turns. Crash, disconnect, or kill mid-turn leaves model mutations unrecorded and the undo group unclosed, with no defined reconciliation contract. 
- **R2-ISSUE-004**: accepted_defect — NetArchTest is compile-time only; the document's claim that seam violations are 'detected by NetArchTest rules' is an overclaim for runtime behaviors (WebSocket, serialization, threading, reconnect). 
- **R1-ISSUE-012**: accepted_defect — Round 1 addressed the self-verification overclaim in SDR-TL-008 but left the SDR-TL-003/008 contradiction unresolved. SDR-TL-003 lists 'Sonnet 4.6 for decoration extraction' while SDR-TL-008 says 'two
- **R1-ISSUE-014**: rejected_claim — Rejected in Round 1; resubmission does not present new evidence at the SPEC-00 level. Vision is explicitly labeled enrichment-only (not decision-making), so model degradation does not break control fl

## Open Questions

- What is the exact post-timeout recovery strategy for in-flight COM calls? SPEC-03 must choose between state-refresh, undo-group rollback, or session lockout.
- Are the inferred ACC capability summaries correct, or do they need adjustment once per-module specs are authored?
- What heartbeat interval and timeout threshold should SPEC-04 use for detecting ungraceful add-in termination?
