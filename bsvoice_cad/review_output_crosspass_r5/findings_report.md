# ORACLE Review Report

**Document:** D:\BoundState\specs\bsvoice_cad\BUNDLE_FOR_CROSSPASS.md
**Session:** 20260419-184840
**Rounds:** 2
**Converged:** No

## Metrics

- Accepted critical defects: 6
- Accepted high defects: 4
- Total accepted defects: 24
- Total rejected claims: 0
- Preference-only findings: 4
- Duplicate rate: 4.9%
- Rounds to stability: 2
- Converged: False

## Round 1

### [CRITICAL] PipelineResult ↔ VocabularyResolutionDecoration recursive self-reference makes successful payloads unserializable (R1-ISSUE-001)
**Problem:** PipelineResult contains VocabularyResolutionDecoration? DecorationPayload, and VocabularyResolutionDecoration contains VocabularyPipelineReturn PipelineReturn which is polymorphic over PipelineResult. For any successful resolution, the decoration payload contains a PipelineResult that contains the decoration payload again — an infinite cycle. No truncation or projection is defined to break it.
**Fix:** Replace VocabularyResolutionDecoration.PipelineReturn with a non-recursive snapshot type (e.g., PipelineResultSnapshot) that explicitly excludes DecorationPayload, or remove DecorationPayload from PipelineResult and return the decoration as a separate out-of-band value from IVocabularyResolver.
**Sources:** Fran

### [CRITICAL] SPEC-05 → SPEC-04 direct write path contradicts SPEC-00's declared module boundary (SPEC-03 is sole DHF writer; SPEC-05 has no direct SPEC-04 reference) (R1-ISSUE-002)
**Problem:** SPEC-00 §1 states SPEC-03 is the sole DHF writer and SPEC-05 has no direct assembly reference to SPEC-04. SPEC-07 v1.1.2 SDR-07-012 now requires SPEC-05 to directly call DhfDecorationWriter and LockSessionAsync after AppendTurnAsync. These are irreconcilable. Additionally, the DhfDecorationPersister contract SPEC-05 must implement is entirely undefined (SPEC-05 §X TBD).
**Fix:** Either (A) update SPEC-00 module map, Seam E, and dependency rules to explicitly permit and govern a SPEC-05 → SPEC-04 write path for decorations, and define the DhfDecorationPersister contract now; or (B) route decoration persistence through SPEC-03 to preserve the existing boundary. Either way, the DhfDecorationPersister interface must be fully specified before implementation.
**Sources:** Fran, Codex

### [CRITICAL] Post-append decoration persistence breaks the 'every resolution records its citation durably' guarantee — committed turn can exist without citation (R1-ISSUE-003)
**Problem:** SPEC-07 §0 and SDR-07-006 claim the pipeline returns success only after the citation is durably written. After v1.1.2, AppendTurnAsync commits the turn entry first, then SPEC-05 attempts the decoration insert. If the decoration insert fails after retry exhaustion, the DHF contains a committed turn with no vocabulary citation. Locking the session afterward does not repair the missing citation for that already-committed turn.
**Fix:** Either (A) make turn append and vocabulary-decoration insert atomic in one DB transaction using a preallocated entry_id passed into AppendTurnAsync; or (B) explicitly downgrade the guarantee in SDR-07-006 and §0, define a compensating marker for uncited turns, and specify a recovery/audit flow for that state.
**Sources:** Fran

### [HIGH] StageLatencies in PipelineResult still requires DecorationWriteMs and TotalMs after decoration writes moved to SPEC-05 (R1-ISSUE-004)
**Problem:** SPEC-07 §3.2 states 'DecorationWriteMs now lives in SPEC-05's Serilog envelope' and Stage 5 is in-memory only with no I/O. Yet SPEC-07 §6.4 StageLatencies still declares required fields DecorationWriteMs and TotalMs on PipelineResult. SPEC-07 cannot populate these fields for work it no longer performs.
**Fix:** Remove DecorationWriteMs and TotalMs from SPEC-07's StageLatencies record. These fields belong in SPEC-05's own telemetry envelope. Update PersistedStageLatencies in §8.2 to confirm it already correctly excludes them, and ensure StageLatencies matches.
**Sources:** Codex, Fran

### [HIGH] Pre-upgrade HTTP 400/401/429 status codes and Retry-After header are not reliably observable from .NET Framework 4.8 ClientWebSocket (R1-ISSUE-005)
**Problem:** SPEC-01 §12.4 and SPEC-02 §6.2 require the add-in to branch on HTTP 400/401/429 and read the Retry-After response header from failed WebSocket upgrade attempts. The add-in targets .NET Framework 4.8 ClientWebSocket, which does not reliably expose pre-upgrade HTTP response status codes or headers from a failed ConnectAsync call.
**Fix:** Either (A) add a separate HTTPS preflight endpoint (e.g., GET /health or POST /api/auth/probe) that returns machine-readable status and headers the add-in can read before opening the WebSocket; or (B) replace the HTTP-status-based branching with a post-upgrade error protocol the add-in can observe on the established WebSocket connection.
**Sources:** Codex

### [HIGH] GetFeatureTree wire contract missing 'compressed' parameter present in adapter interface (R1-ISSUE-006)
**Problem:** SPEC-02 §3.4 ISolidWorksFeatureService.GetFeatureTree takes (bool includeSuppressed, bool compressed). SPEC-01 §7.1 GetFeatureTreeInput only has IncludeSuppressed. The compressed parameter has no wire representation and no normative default, leaving implementers to guess the behavior.
**Fix:** Either add a Compressed field to GetFeatureTreeInput with a documented default and semantics, or remove the compressed parameter from the adapter interface and hard-code one documented behavior (e.g., always compressed for tool calls, always full for direct service calls).
**Sources:** Codex

### [HIGH] Oversize screenshot fallback emits FeatureTreeHash='OVERSIZE' sentinel, contradicting SPEC-01's invariant that hash must reflect the complete tree (R1-ISSUE-007)
**Problem:** SPEC-02 §7.5 instructs the add-in to emit FeatureTreeHash='OVERSIZE' in the error path. SPEC-01 §8 states 'The FeatureTreeHash MUST still reflect the hash of the complete (non-truncated) tree so that change detection remains accurate.' These are mutually exclusive for the same message.
**Fix:** Either keep the real full-tree hash in the oversize error path (computing it before truncation), or formally extend SPEC-01 with an explicit oversize sentinel contract, update validators and hash-chain logic to handle it, and document the audit-trail implications.
**Sources:** Codex

### [HIGH] SPEC-02 Day-0 validation step 11 checks DHF session row for closure_reason field that SPEC-00 explicitly removed from the schema (R1-ISSUE-008)
**Problem:** SPEC-02 §11 item 11 says to 'confirm the orchestrator's DHF session row shows closure reason explicit_shutdown.' SPEC-00 v1.5.1 C3 reconciliation explicitly states closure reason lives in Serilog structured logs, NOT in dhf.sessions. The DHF schema has no closure_reason column.
**Fix:** Update SPEC-02 §11 item 11 to validate the Serilog session_closed structured log event instead of a DHF row field. Specify the exact log fields to check (e.g., closure_reason='explicit_shutdown', session_id).
**Sources:** Fran

### [HIGH] AddinTeardownMessage send path is contradictory: §9.0.2 uses direct socket I/O while §9.0.3 and §9.1.1 claim it goes through SwUiThreadCommandQueue (R1-ISSUE-009)
**Problem:** SPEC-02 §9.0.2 TeardownMessageSender pseudocode calls _webSocket.SendAsync directly (no UI thread queue). SPEC-02 §9.0.3 states teardown 'will block behind the COM call on the shared message queue (both go through SwUiThreadCommandQueue).' §9.1.1 step 3 repeats 'The send path goes through SwUiThreadCommandQueue.' These are mutually exclusive execution models for the same operation.
**Fix:** Declare one authoritative execution model for teardown sends. If direct socket I/O (§9.0.2), update §9.0.3 and §9.1.1 to remove the SwUiThreadCommandQueue claim and re-examine whether the 60-second drain rationale still holds. If UI-thread-queued, update §9.0.2 pseudocode accordingly.
**Sources:** Fran

### [HIGH] ViewerOptions.VocabRawInputAllowed security gate is defined in prose but absent from the ViewerOptions class and appsettings configuration (R1-ISSUE-010)
**Problem:** SPEC-06 SDR-06-008 references ViewerOptions.VocabRawInputAllowed as the security gate controlling raw utterance and ParsedIntent exposure. SPEC-06 §3 ViewerOptions class only contains EnableStretchEndpoints. SPEC-06 §13 configuration only shows enableStretchEndpoints. The security-sensitive switch exists only in prose.
**Fix:** Add VocabRawInputAllowed to the ViewerOptions class, add its config key to §13 appsettings, specify the default value (false), and document the per-tenant override mechanism and who can change it.
**Sources:** Codex

### [MEDIUM] Security-event DHF decoration destination is contradictory within SPEC-07 (§3.3 says write if security_event=true; §14 and changelog say never write for rejections) (R1-ISSUE-011)
**Problem:** SPEC-07 §3.3 failure-mode table states 'Decoration Written? Only if security_event=true' for bouncer rejections. SPEC-07 §14 states 'No (security events logged, not DHF'd)'. The v1.1.1 changelog M3/M4 states 'no DHF decoration is written for rejections — security events route to the restricted audit sink.' Three different answers to the same question.
**Fix:** Adopt the §14/changelog position (no DHF decoration for any rejection, security events go to restricted audit sink only) as the single authoritative answer. Update §3.3 to match. Remove all remaining references to DHF decoration writes for rejected security events.
**Sources:** Fran

### [MEDIUM] NetFx add-in pseudocode uses Guid.CreateVersion7() which requires .NET 9+ or an unspecified polyfill (R1-ISSUE-012)
**Problem:** SPEC-02 §9.0.2 TeardownMessageSender pseudocode uses Guid.CreateVersion7().ToString() for MessageId generation. The add-in targets .NET Framework 4.8. SPEC-01 §5 notes Guid.CreateVersion7() is .NET 9+ only and requires 'backport or NuGet implementation on earlier runtimes' — but no specific library is named.
**Fix:** Name the specific UUIDv7 NuGet package or shared utility used by the NetFx add-in for all MessageId generation. Add it to the project shape in SPEC-02 §1 and reference it consistently in all add-in pseudocode.
**Sources:** Fran

### [MEDIUM] Same-workstation stale-session cleanup silently closes sessions from other SolidWorks processes on the same workstation (R1-ISSUE-013)
**Problem:** SPEC-04 §6.1 OpenOrResumeSessionCore closes all open unlocked sessions for the same tenant+workstation before opening a new one. The spec never declares that a workstation can have only one active SolidWorks process. If two legitimate SolidWorks instances run on one workstation, opening the second session silently force-closes the first.
**Fix:** Either (A) declare and enforce a hard single-SolidWorks-process-per-workstation invariant in SPEC-00 §6 and SPEC-02, or (B) scope stale-session cleanup to a stronger process identity (e.g., include SW process ID in the session row and cleanup predicate).
**Sources:** Fran

### [MEDIUM] LLM-based bouncer as primary security boundary is architecturally weak; phonetic evasion acknowledged but mitigation is insufficient for a security gate (R1-ISSUE-014)
**Problem:** SPEC-07 §4.5 acknowledges phonetic evasion bypasses the deterministic scan and relies on the bouncer LLM persona as the fallback. The spec's own threat model admits this gap. Using an LLM as a security boundary is architecturally fragile — the LLM is the weakest link, not the strongest.
**Fix:** Promote the deterministic grammar/alias match path as the primary security gate for known injection patterns. Reserve LLM invocation for genuinely ambiguous cases. Add rate limiting on bouncer invocations per session. Document the residual risk explicitly in the threat model rather than treating the LLM persona as a reliable security control.
**Sources:** Grok

### [LOW] SPEC-00 scope creep: integration contract has absorbed detailed behavioral specifications that belong in per-module specs (R1-ISSUE-015)
**Problem:** SPEC-00 subtitle claims 'interfaces only, no implementation' but the document defines cancellation boundaries, post-timeout recovery strategies, lock recovery procedures, exact closure reason mapping, recovery_context JSONB shape, and 30+ ACC gates. This creates a brittle single point of truth for runtime behavior across all modules.
**Fix:** Progressively migrate behavioral contracts back to owning module specs. SPEC-00 should contain module map, seam directionality, mandatory/stretch list, and high-level ACC cross-reference. Per-seam detailed state transitions and recovery procedures should live in the specs that own those behaviors.
**Sources:** Grok

### Coordinator Decisions
- **R1-ISSUE-001**: accepted_defect — PipelineResult.DecorationPayload contains VocabularyResolutionDecoration, which contains VocabularyPipelineReturn polymorphic over PipelineResult — creating an infinite serialization cycle. System.Tex
- **R1-ISSUE-002**: accepted_defect — SPEC-00 Seam E declares SPEC-05 has no direct SPEC-04 assembly reference, but SPEC-07 SDR-07-012 requires SPEC-05 to call DhfDecorationWriter and LockSessionAsync. Fixed by adding Seam E₂ and updating
- **R1-ISSUE-003**: accepted_defect — SPEC-07 §0 claims pipeline success ONLY after durable write, but after v1.1.2 the turn commits first and decoration write is separate. A committed turn can exist without its citation if the decoration
- **R1-ISSUE-004**: accepted_defect — StageLatencies in §6.4 declares required DecorationWriteMs and TotalMs fields, but SPEC-07 no longer performs decoration writes (moved to SPEC-05 in v1.1.2). Fixed by removing these fields and adding 
- **R1-ISSUE-005**: accepted_defect — .NET Framework 4.8 ClientWebSocket does not reliably expose HTTP status codes from failed WebSocket upgrades. The branching on HTTP 400/401/429 in SPEC-02 §6.2 may not be implementable as specified. F
- **R1-ISSUE-006**: accepted_defect — SPEC-02 ISolidWorksFeatureService.GetFeatureTree takes (bool includeSuppressed, bool compressed) but SPEC-01 GetFeatureTreeInput only has IncludeSuppressed. The compressed parameter has no wire repres
- **R1-ISSUE-007**: accepted_defect — SPEC-02 §7.5 emits FeatureTreeHash='OVERSIZE' in the error path, contradicting SPEC-01 §8 invariant that FeatureTreeHash must reflect the complete tree. Fixed by requiring the real hash to be computed
- **R1-ISSUE-008**: accepted_defect — SPEC-02 §11 item 11 validates 'the orchestrator's DHF session row shows closure reason explicit_shutdown' but SPEC-00 v1.5.1 C3 explicitly removed closure_reason from DHF schema (lives in Serilog). Fi
- **R1-ISSUE-009**: accepted_defect — §9.0.2 pseudocode uses direct _webSocket.SendAsync (no UI thread queue) but §9.0.3 and §9.1.1 claim teardown sends go through SwUiThreadCommandQueue. Mutually exclusive execution models. Fixed by decl
- **R1-ISSUE-010**: accepted_defect — SDR-06-008 references ViewerOptions.VocabRawInputAllowed as the security gate but the property is absent from the ViewerOptions class and appsettings config. Fixed by adding the property and config en
- **R1-ISSUE-011**: accepted_defect — §3.3 says 'Decoration Written? Only if security_event=true' for bouncer rejections; §14 says 'No (security events logged, not DHF'd)'; v1.1.1 M3 says 'no DHF decoration for rejections.' Three contradi
- **R1-ISSUE-012**: accepted_defect — SPEC-02 §9.0.2 pseudocode uses Guid.CreateVersion7() which requires .NET 9+; add-in targets .NET Framework 4.8 per SDR-TL-001. Fixed by adding a UUIDv7 polyfill note naming the required NuGet package 
- **R1-ISSUE-013**: accepted_defect — SPEC-04 §6.1 stale-session cleanup closes all open sessions for the same tenant+workstation, but the spec never declares that only one SolidWorks process may be active per workstation. Two legitimate 
- **R1-ISSUE-014**: preference_only — The spec already uses the deterministic regex scan as the primary security gate (§4.5 pre-cache injection scan runs before cache lookup and LLM invocation). The LLM bouncer is a secondary gate. The ph
- **R1-ISSUE-015**: preference_only — SPEC-00's behavioral detail at seam boundaries is an intentional design choice. Seam contracts necessarily include behavioral specifications for what happens at module boundaries. The current centrali

## Round 2

### [CRITICAL] SPEC-00 still forbids direct SPEC-05→SPEC-04 dependency that decoration persistence now requires (R1-ISSUE-002)
**Problem:** SPEC-00 Module Map lists SPEC-05 Depends On as 'SPEC-01, SPEC-03, SPEC-07' and Seam E explicitly states 'SPEC-05 has no direct assembly reference to SPEC-04; the dependency is purely indirect through SPEC-03's aggregation role.' However, SPEC-07 SDR-07-012 (v1.1.2 C1 fix) now requires SPEC-05 to call SPEC-04's DhfDecorationWriter directly after AppendTurnAsync returns entry_id. The top-level orchestration contract forbids the dependency the new architecture requires.
**Fix:** Update SPEC-00 Module Map to add SPEC-04 to SPEC-05's Depends On list (narrowly scoped to decoration write surface). Add an explicit Seam I or extend Seam E/H to document the SPEC-05→SPEC-04 decoration-persistence path, naming the exact interface (DhfDecorationWriter). Remove stale Seam H language implying SPEC-07 persists directly. Update NetArchTest rules accordingly.
**Sources:** Fran, Codex

### [CRITICAL] SPEC-07 §0 and SDR-07-006 still claim pipeline success waits for durable citation persistence, contradicting the v1.1.2 C1 ownership shift (R1-ISSUE-003)
**Problem:** SPEC-07 §0 says 'The pipeline returns success ONLY after the citation is durably written' and SDR-07-006 says 'The write is awaited on the critical path... pipeline success is reported only after durable persistence.' These normative statements directly contradict Stage 5 which now says 'NOT persisted here — returned to SPEC-05' and SDR-07-012 which moved persistence to SPEC-05. Callers cannot determine whether a PipelineResult means 'payload assembled' or 'citation durably stored.'
**Fix:** Rewrite SPEC-07 §0 and SDR-07-006 so SPEC-07 guarantees payload assembly only. Move the durable-write success condition entirely into SPEC-05 contract text. Update ACC-29 description to reflect 'payload correctly assembled' rather than 'durably persisted.'
**Sources:** Fran, Codex

### [CRITICAL] SPEC-07 StageLatencies still requires DecorationWriteMs and TotalMs that SPEC-07 cannot compute before returning (R1-ISSUE-004)
**Problem:** The StageLatencies record in SPEC-07 §6.4 has required fields DecorationWriteMs and TotalMs. But Stage 5 now only assembles a payload and returns to SPEC-05 before any write occurs. SPEC-07 cannot populate these fields at the point it returns PipelineResult. The DTO is internally impossible as specified.
**Fix:** Remove DecorationWriteMs and TotalMs from SPEC-07's StageLatencies record. Define a separate SPEC-05-owned latency envelope that augments the pipeline result after persistence completes. Update §3.2 latency table to reflect SPEC-07-only timing.
**Sources:** Fran, Codex

### [CRITICAL] GetFeatureTreeInput missing 'Compressed' parameter present in SPEC-02 adapter interface (R1-ISSUE-006)
**Problem:** SPEC-01 §7.1 defines GetFeatureTreeInput with only IncludeSuppressed. SPEC-02 §3.4 defines ISolidWorksFeatureService.GetFeatureTree(bool includeSuppressed, bool compressed). The orchestrator cannot request compressed vs full feature trees over the wire, so producer and consumer can both follow their local specs and disagree at runtime.
**Fix:** Either add 'public bool Compressed { get; init; } = true;' to SPEC-01 GetFeatureTreeInput with documented semantics and update validators/schema/tests, or remove the compressed parameter from SPEC-02 and define one fixed behavior (e.g., always compressed) documented in both specs.
**Sources:** Fran, Codex

### [CRITICAL] SPEC-02 oversize screenshot fallback emits FeatureTreeHash='OVERSIZE' sentinel, violating SPEC-01's hash invariant (R1-ISSUE-007)
**Problem:** SPEC-02 §7.5 specifies that the oversize fallback uses a placeholder StateAfter with FeatureTreeHash='OVERSIZE'. SPEC-01 §8 requires 'The FeatureTreeHash MUST still reflect the hash of the complete (non-truncated) tree so that change detection remains accurate.' The sentinel is not defined in SPEC-01 and directly violates the hash invariant.
**Fix:** Use the real SHA-256 hash of the complete untruncated feature tree even in the oversize placeholder state (the hash can be computed before truncation). Alternatively, formally add 'OVERSIZE' as a supported sentinel to SPEC-01 §8 and update HashCanonicalizer, validators, and all consumers to handle it.
**Sources:** Fran, Codex

### [HIGH] AddinTeardownMessage send path is contradictory: §9.0.2 uses direct socket I/O while §9.0.3 and §9.1.1 claim it goes through SwUiThreadCommandQueue (R1-ISSUE-009)
**Problem:** SPEC-02 §9.0.2 TeardownMessageSender pseudocode shows direct WebSocket I/O via '_webSocket.SendAsync(...)'. But §9.0.3 says the 60-second drain is essential because 'if the add-in sends teardown while a COM call is still executing on the UI thread, the teardown send will block behind the COM call on the shared message queue (both go through SwUiThreadCommandQueue).' And §9.1.1 step 3 says 'Do NOT attempt to send AddinTeardownMessage. The send path goes through SwUiThreadCommandQueue.' These are mutually exclusive threading models.
**Fix:** Clarify that TeardownMessageSender uses direct WebSocket I/O on a non-UI thread (not through SwUiThreadCommandQueue). Update §9.0.3 and §9.1.1 to remove the false claim that teardown send is UI-thread-queued. The 60-second drain is still needed to avoid sending teardown while a COM call is in-flight (to ensure clean state), but the reason is not queue blocking.
**Sources:** Fran, Codex

### [HIGH] SPEC-02 Day-0 validation step 11 checks DHF session row for closure_reason that SPEC-00 explicitly removed from the schema (R1-ISSUE-008)
**Problem:** SPEC-02 §11 Day-0 Validation item 11 says 'confirm the orchestrator's DHF session row shows closure reason explicit_shutdown.' SPEC-00 changelog C3 explicitly states closure reason is 'recorded in orchestrator structured logs (Serilog session_closed event) per SPEC-02 §9.0.4, NOT in the DHF schema.' The validation procedure points to a persistence location the architecture removed.
**Fix:** Change SPEC-02 §11 item 11 to: (a) verify dhf.sessions.ended_at is set, and (b) verify the Serilog structured log contains a session_closed event with reason=explicit_shutdown. Remove any reference to a DHF row closure_reason field.
**Sources:** Fran, Codex

### [HIGH] ViewerOptions.VocabRawInputAllowed security gate is defined in prose but absent from ViewerOptions class and appsettings configuration (R1-ISSUE-010)
**Problem:** SPEC-06 SDR-06-008 references ViewerOptions.VocabRawInputAllowed and describes per-tenant gating, but the ViewerOptions class in §3 only defines EnableStretchEndpoints, and the §13 configuration example shows no vocabRawInputAllowed key. Additionally, a single global boolean cannot implement per-tenant behavior as described.
**Fix:** Either: (a) add VocabRawInputAllowed to ViewerOptions as a global flag and explicitly downgrade the per-tenant claim to all-tenants-or-none, or (b) define a tenant-keyed policy source (e.g., Dictionary<string,bool> keyed by tenantId) with a concrete config schema and resolver interface. Update §13 configuration example to include the new key.
**Sources:** Fran, Codex

### [HIGH] Security-event DHF decoration destination is contradictory within SPEC-07 (R1-ISSUE-011)
**Problem:** SPEC-07 §3.3 failure modes table has a row 'Bouncer rejects utterance' with 'Decoration Written? Only if security_event=true.' But the note below says 'no DHF decoration is written for rejections — security events route to the restricted audit sink.' And §14 failure taxonomy says security-event rejection => 'No (security events logged, not DHF'd).' Two incompatible rules exist for the same path.
**Fix:** Choose one rule and apply it consistently. The §14 and §3.3 note position (audit-sink-only, no DHF decoration for any rejection) is architecturally cleaner and avoids writing attacker-controlled content into the append-only audit chain. Update §3.3 table to 'No' for all rejection rows.
**Sources:** Fran, Codex

### [MEDIUM] Pre-upgrade HTTP status codes and Retry-After header may not be reliably observable from .NET Framework 4.8 ClientWebSocket (R1-ISSUE-005)
**Problem:** SPEC-02 §6.2 specifies add-in behavior on HTTP 401, HTTP 400, and HTTP 429 with Retry-After header parsing. The add-in targets .NET Framework 4.8 using ClientWebSocket. The spec does not define the mechanism by which the add-in reads upgrade-response status codes and headers from ClientWebSocket.ConnectAsync, which may throw WebSocketException without exposing the HTTP status code cleanly.
**Fix:** Specify the exact NetFx API or exception inspection path used to extract HTTP status codes and headers from failed ConnectAsync calls (e.g., WebSocketException.InnerException as HttpRequestException, or a preflight HTTPS probe). Alternatively, add a fallback classification path when status details are unavailable.
**Sources:** Fran, Codex

### [MEDIUM] LLM-based bouncer as primary security boundary is architecturally weak for a regulated engineering context (R2-ISSUE-001)
**Problem:** The bouncer is an LLM call with a structured JSON output schema. All bouncer tests use recorded/mocked responses; live-model semantic checks are non-blocking canaries. LLMs are stochastic and subject to prompt drift or model updates. The 'deterministic after bouncer' framing is partially false since the bouncer itself is nondeterministic. For a system targeting regulated medical device design, this is a meaningful risk.
**Fix:** Add runtime JSON schema validation with hard rejection on malformed bouncer output (already partially specified via MalformedBouncerOutput path). Consider making at least a subset of bouncer semantic tests build-blocking (e.g., the injection-pattern rejection cases). Document the accepted risk explicitly in SDR-07-003.
**Sources:** Grok

### [MEDIUM] Materials corpus licensing is unscoped and may block v1.0 Medtronic delivery (R2-ISSUE-002)
**Problem:** CAD_VOCAB_CORPUS.md §2.5 lists 19,295 materials rows as Tier 2 (required for v1.0 Medtronic) from licensed sources (ASTM Compass, SAE). No licensing review, budget, or timeline appears in SPEC-00 §3 Mandatory vs Stretch or SPEC-07 §10. The demo slice is only 22 rows. Phase 3 ETL is estimated at 4 developer-weeks plus licensed data access.
**Fix:** Move full materials catalog to stretch scope or add an explicit licensing review gate to SPEC-00 §5 Review Order. Clarify in SPEC-07 §10 that 'mandatory' means the 22-row demo slice only, not the full 19k-row catalog.
**Sources:** Grok

### [MEDIUM] NetFx add-in pseudocode uses Guid.CreateVersion7() which requires .NET 9+ or an unspecified polyfill (R1-ISSUE-012)
**Problem:** SPEC-02 §9.0.2 TeardownMessageSender pseudocode uses 'Guid.CreateVersion7().ToString()' for MessageId generation. The add-in targets .NET Framework 4.8. Guid.CreateVersion7() is a .NET 9+ API and is not available on .NET Framework 4.8 without a NuGet polyfill. SPEC-01 §18 mentions 'Guid.CreateVersion7() (or equivalent polyfill on target runtimes)' for SPEC-03 but SPEC-02 has no such caveat.
**Fix:** Add a note in SPEC-02 §9.0.2 specifying the UUIDv7 polyfill NuGet package to use on .NET Framework 4.8 (e.g., UUIDNext or similar), consistent with the polyfill approach mentioned in SPEC-01 §5.
**Sources:** Codex

### Coordinator Decisions
- **R1-ISSUE-002**: accepted_defect — Previously accepted in Round 1 but text revision was not applied (revised_draft was null). The contradiction between SPEC-00 Seam E ('SPEC-05 has no direct assembly reference to SPEC-04') and SPEC-07 
- **R1-ISSUE-003**: accepted_defect — Previously accepted in Round 1 but text revision was not applied. SPEC-07 §0 and SDR-07-006 still claim pipeline success waits for durable write, contradicting the v1.1.2 C1 ownership shift. Applying 
- **R1-ISSUE-004**: accepted_defect — Previously accepted in Round 1 but text revision was not applied. StageLatencies still declares required DecorationWriteMs and TotalMs that SPEC-07 cannot populate. Applying fix now: remove those fiel
- **R1-ISSUE-006**: accepted_defect — Previously accepted in Round 1 but text revision was not applied. GetFeatureTreeInput still lacks the Compressed parameter that SPEC-02's adapter interface requires. Applying fix now: add Compressed f
- **R1-ISSUE-007**: accepted_defect — Previously accepted in Round 1 but text revision was not applied. SPEC-02 §7.5 still emits FeatureTreeHash='OVERSIZE' which violates SPEC-01 §8 hash invariant. Applying fix now: require real hash comp
- **R1-ISSUE-009**: accepted_defect — Previously accepted in Round 1 but text revision was not applied. §9.0.2 direct socket I/O contradicts §9.0.3 and §9.1.1 claims about SwUiThreadCommandQueue. Applying fix now: declare §9.0.2 authorita
- **R1-ISSUE-008**: accepted_defect — Previously accepted in Round 1 but text revision was not applied. Day-0 validation step 11 still checks DHF session row for closure_reason that SPEC-00 v1.5.1 C3 removed from the schema. Applying fix 
- **R1-ISSUE-010**: accepted_defect — Previously accepted in Round 1 but text revision was not applied. SDR-06-008 references ViewerOptions.VocabRawInputAllowed but the property is absent from ViewerOptions class and appsettings. Applying
- **R1-ISSUE-011**: accepted_defect — Previously accepted in Round 1 but text revision was not applied. §3.3 table still says 'Only if security_event=true' for bouncer rejection decorations, contradicting §14 which says 'No (security even
- **R1-ISSUE-005**: accepted_defect — Previously accepted in Round 1 but text revision was not applied. SPEC-02 §6.2 still specifies HTTP 401/400/429 branching without addressing .NET Framework 4.8 ClientWebSocket observability limitation
- **R2-ISSUE-001**: preference_only — The spec already positions the deterministic regex scan (§4.5 pre-cache injection scan) as the primary security gate, running BEFORE both cache lookup and the LLM bouncer. The LLM bouncer is a seconda
- **R2-ISSUE-002**: preference_only — SPEC-07 §10.1 mandatory scope explicitly specifies '300-row demo seed corpus (from CAD_VOCAB_CORPUS.md §4)' — the 22-row materials demo slice, not the full 19k catalog. The full catalog is Phase 3 (CA
- **R1-ISSUE-012**: accepted_defect — Previously accepted in Round 1 but text revision was not applied. SPEC-02 §9.0.2 pseudocode uses Guid.CreateVersion7() which requires .NET 9+; add-in targets .NET Framework 4.8. Applying fix now: add 

## Open Questions

- Should SPEC-05's DhfDecorationPersister contract be authored before implementation begins, or can it be deferred until the SPEC-05 authoring pass? The Seam E₂ addition documents the boundary but the implementation contract is still TBD.
- Should the non-atomic citation persistence be elevated to a formal acceptance criterion (e.g., ACC-31: 'A turn committed without its vocabulary decoration triggers session lock within one retry cycle'), or is the SDR-07-012 compensating-control description sufficient?
- The NetFx preflight probe to GET /health is recommended but optional. Should it be mandatory for v1 to provide reliable auth-failure differentiation?
