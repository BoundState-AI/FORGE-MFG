# ORACLE Review Report

**Document:** D:\BoundState\specs\bsvoice_cad\BUNDLE_FOR_CROSSPASS.md
**Session:** 20260419-175535
**Rounds:** 2
**Converged:** No

## Metrics

- Accepted critical defects: 4
- Accepted high defects: 6
- Total accepted defects: 10
- Total rejected claims: 0
- Preference-only findings: 4
- Duplicate rate: 0.0%
- Rounds to stability: 2
- Converged: False

## Round 1

### [CRITICAL] Vocabulary decoration write requires entry_id before the turn entry exists (R1-ISSUE-001)
**Problem:** SPEC-00 Seam G requires vocabulary resolution BEFORE tool-call construction, and SPEC-07 Stage 5 requires durable decoration persistence as part of that resolution. But the DHF entry (which provides the entry_id the decoration references) is only created at turn completion via AppendTurnAsync. No mechanism is defined for obtaining or reserving an entry_id at vocabulary-resolution time, making the decoration write contract impossible to implement as specified.
**Fix:** Choose one explicit mechanism: (a) SPEC-07 returns the decoration payload only; SPEC-05 persists it after AppendTurnAsync returns an entry_id, or (b) define a preallocated turn identifier created before vocabulary resolution and carried through the lifecycle. Update SPEC-00 Seam G, SPEC-07 §3.1/§8.2, and SPEC-05 to reflect the chosen path.
**Sources:** Fran

### [CRITICAL] SPEC-04 defines contradictory hash input sets in adjacent sections (R1-ISSUE-002)
**Problem:** SPEC-04 §4.2 DhfHashColumns includes 'entry_id', 'session_id', 'sequence_number' in the hashed column list. SPEC-04 §4.2a explicitly lists those same three columns as 'NOT included in DhfHashColumns'. The hash contract has two conflicting definitions, meaning append and validation will compute different record_hash values from the same row.
**Fix:** Make one hashed-column list authoritative and remove the contradictory statement. Add a build-breaking HashColumnSyncTests assertion that the §4.2a exclusion table and the actual DhfHashColumns set are mutually exclusive and collectively exhaustive over all dhf.entries columns.
**Sources:** Codex, Fran

### [CRITICAL] Session closure reason has no schema field or repository API despite being required by SPEC-00 (R1-ISSUE-003)
**Problem:** SPEC-00 Seam C and §6 Glossary define three closure reasons (explicit_shutdown, superseded_session, heartbeat_timeout) and state 'SPEC-04 records a session-closure event noting the reason.' However, dhf.sessions has no closure_reason column, and CloseSessionAsync only executes UPDATE dhf.sessions SET ended_at = NOW() with no reason parameter. The closure reason is unimplementable as specified.
**Fix:** Add a closure_reason TEXT column to dhf.sessions (new migration 010), add a reason parameter to CloseSessionAsync, update all call sites in SPEC-03/SPEC-04, expose it in SessionSummary/SessionDetail DTOs, and add it to the guard_session_update trigger's permitted-transition list. Alternatively, explicitly downgrade closure reason to log-only in every spec and remove the SPEC-00 claim.
**Sources:** Codex

### [HIGH] ACC identifiers 25-27 are assigned to different capabilities in SPEC-00 and SPEC-07 (R1-ISSUE-004)
**Problem:** SPEC-00 §3 assigns ACC-25 to durable session lock, ACC-26 to recovery-event persistence, ACC-27 to neutral landing page. SPEC-07 §12 assigns ACC-25 to multi-domain resolution, ACC-26 to decoration-write durability/circuit-breaker, ACC-27 to bouncer-cache raw-utterance isolation. The global acceptance-test namespace is no longer unique.
**Fix:** Renumber SPEC-07 additions to ACC-28, ACC-29, ACC-30 (or next available). Update the SPEC-00 master ACC table to include the SPEC-07 entries under the new numbers. Update all cross-references in SPEC-07 §11 test names and SPEC-00 §3 never-descopable gate list.
**Sources:** Codex, Fran

### [HIGH] Viewer entry-detail endpoint lacks session-capability access control (R1-ISSUE-005)
**Problem:** SPEC-06 §5.1 and §5.2 define GET /api/dhf/entries/{entryId} with no sessionId requirement. The spec acknowledges 'the entry ID alone does not grant access' but relies on sidebar link hiding rather than server-side authorization. Anyone who obtains an entryId can fetch full entry content including screenshots and tool-call details, bypassing the URL-as-capability boundary.
**Fix:** Require a valid sessionId capability on every entry-detail request. Verify the requested entryId belongs to the session identified by the capability before returning data. Add SessionCapabilityGateTests coverage for the entry-detail endpoint.
**Sources:** Fran

### [HIGH] Locked same-AddinSessionId sessions on other workstations are silently bypassed during session open (R1-ISSUE-006)
**Problem:** In SPEC-04 §6.1 OpenOrResumeSessionCore, the first lookup filters ended_at IS NULL AND locked_at IS NULL. The second locked-session check adds AND workstation_id = @WorkstationId. A locked open session with the same tenant+AddinSessionId on a different workstation matches neither branch, allowing a new session to be inserted with the same AddinSessionId — bypassing SDR-04-008's cross-workstation hard-error rule.
**Fix:** Before inserting, add a query for any open row (ended_at IS NULL) with the same tenant+AddinSessionId regardless of lock state. If workstation differs, throw CrossWorkstationSessionConflictException. If workstation matches and locked, throw SessionLockedException. Update SessionLockDurabilityTests and CrossWorkstationConflictTests to cover this case.
**Sources:** Fran

### [HIGH] SPEC-01 contradicts itself on whether ProtocolVersion mismatch can be detected during HTTP upgrade (R1-ISSUE-007)
**Problem:** SPEC-01 §10 states version mismatch is detected after AddinReadyMessage (post-upgrade). SPEC-01 §9.1 editorial m4 states it is 'caught by §10's version-mismatch handling (HTTP 400 during HTTP upgrade or close code 1002)'. ProtocolVersion is carried in AddinReadyMessage which is sent after upgrade, so HTTP 400 on upgrade is impossible for this failure mode.
**Fix:** Remove the HTTP-upgrade variant from §9.1 m4. Define one post-upgrade version-mismatch path: orchestrator sends ClientProtocolErrorMessage(VersionMismatch) then closes with code 1002. Update SameEpochReconnectRejectionTests to cover only the post-upgrade path.
**Sources:** Codex

### [HIGH] Locked-session recovery runbook is inconsistent across specs (R1-ISSUE-008)
**Problem:** SPEC-00 Seam C and §6 Glossary say 'operator unlocks the session via SQL' and 'manual SQL unlock is required to resume.' SPEC-04 migration 008 trigger explicitly rejects locked_at non-NULL→NULL transitions and recommends close-and-new-session as the recovery path, treating trigger bypass as last resort. These are contradictory operational procedures.
**Fix:** Pick one supported recovery model. Given the trigger design, the correct model is close-and-new-session: update SPEC-00 §6 Glossary and Seam C to say 'operator closes the locked session via SQL and the add-in reconnects with a new AddinSessionId.' Remove all 'unlock and resume' language from SPEC-00.
**Sources:** Codex

### [HIGH] Viewer mandatory entries endpoint response includes fields absent from IReadOnlyDhfRepository EntryListItem (R1-ISSUE-009)
**Problem:** SPEC-06 §4.3 GET /api/dhf/entries response example includes undoOutcome, recordHash, and previousHash. SPEC-04 §9.2 EntryListItem is defined as (EntryId, SequenceNumber, Timestamp, InputModality, TurnType, RawTranscript, ToolCallCount, HasDecorations) — none of the three fields are present. The mandatory endpoint cannot be implemented from the read-only repository without undeclared changes.
**Fix:** Either extend SPEC-04 §9.2 EntryListItem to include undoOutcome, recordHash, and previousHash, or remove those fields from the SPEC-06 §4.3 response contract. Update DhfRepositoryTests and EntriesEndpointTests accordingly.
**Sources:** Codex

### [CRITICAL] Planned disconnect escalation terminates SolidWorks process and loses unrelated unsaved work (R1-ISSUE-010)
**Problem:** SPEC-02 §9.1.1 step 7 specifies Environment.Exit(1) after a 5-second grace period when a COM call is stuck. Editorial M4 explicitly acknowledges this 'terminates the SolidWorks process' and that 'unsaved work in other open documents may be lost.' This turns an add-in fault into host-wide data loss for the user.
**Fix:** Remove Environment.Exit(1) from the in-process add-in path. Instead: mark the add-in session as permanently failed, surface a hard error to the user requiring manual SolidWorks shutdown, and log the stuck call. If forced termination is truly required, move it to an external watchdog process that is not the CAD host. Update PlannedDisconnectEscalationTests to assert no process exit occurs.
**Sources:** Fran

### [HIGH] Viewer URL-as-capability model enables tenant-wide session enumeration from a single leaked session UUID (R1-ISSUE-011)
**Problem:** SPEC-06 §4.2 and SDR-06-007 explicitly allow GET /api/dhf/sessions?sessionId={uuid} to return all sessions for the tenant that owns the presented session. A single leaked or shoulder-surfed session URL grants an adversary the complete list of all other sessions for that tenant. The spec acknowledges this as an 'accepted v1 trade-off' but does not rate-limit or scope the sidebar endpoint.
**Fix:** For the hackathon/pilot scope, add explicit documentation that the sidebar endpoint must not be used for sessions containing sensitive design IP. For any Medtronic-facing deployment, either remove the tenant-scoped sidebar list (require per-session capability for every fetch) or accelerate Clerk SSO to v1 rather than v1.5. Add a rate limit on the sessions endpoint to slow enumeration.
**Sources:** GrokContrarian

### [HIGH] v2 screenshot viewing path is not representable in the current SPEC-04 read model (R1-ISSUE-012)
**Problem:** The migration doc §3 v2 contract states 'DHF stores S3 key; viewer and Anthropic vision API fetch via the presigned GET URL.' SPEC-04 §9.2 EntryDetail read-side DTO exposes only byte[]? ScreenshotBeforePng / ScreenshotAfterPng. SPEC-06 §5.2 claims screenshotBefore.dataUrl can be 'https://... presigned (v2)' but there is no S3 key or URL field in the read model to source this from.
**Fix:** Introduce a version-agnostic screenshot reference DTO now (e.g., a discriminated union with bytes-or-url variants plus sha256 and optional S3 key) in SPEC-04 §9.2 EntryDetail and mirror it in SPEC-06 §5.2. This avoids a breaking cross-spec DTO change during v1→v2 migration.
**Sources:** Codex

### [MEDIUM] BSMS hallucination-resistance claim depends on mocked LLM tests with no live semantic enforcement (R1-ISSUE-013)
**Problem:** SPEC-07 §11 explicitly states 'Live LLM calls are NOT build-breaking... Uses recorded/mocked Sonnet responses.' The entire BSMS safety argument rests on the bouncer and synthesizer producing correct structured JSON, but only the parser is tested in CI. Model updates, temperature variance, or prompt drift can produce malformed output that triggers circuit-breaker session lock without any build-time detection.
**Fix:** Add a build-breaking prompt-checksum test (BouncerPromptSyncTests already exists — extend it to also verify the recorded mock responses were generated from the current prompt version). Add at least one build-breaking canary that calls the live model with a fixed seed utterance and asserts the rejection_code closed-enum invariant. Document the prompt version in a vocab.bouncer_prompts table.
**Sources:** GrokContrarian

### [LOW] SPEC-00 claims 'interfaces only, no implementation' but embeds SQL, C# records, test names, and migration scripts (R1-ISSUE-014)
**Problem:** SPEC-00 §0 states 'Non-goals for this document: state machine details, record field definitions, SQL schemas, build sequence.' Yet SPEC-00 contains full SQL for migrations 007-009, C# record definitions for RecoveryEventContext, test names (HeartbeatTimeoutTcpAbortTests), and implementation trade-off rationales. This creates tight coupling and makes independent per-module Oracle reviews harder.
**Fix:** This is a documentation hygiene issue rather than an implementation blocker. The implementation details in SPEC-00 are duplicated from the per-module specs and kept in sync. For future revisions, move implementation details to owning specs and keep SPEC-00 to seam contracts and cross-cutting SDRs. Low priority given the hackathon timeline.
**Sources:** GrokContrarian

### Coordinator Decisions
- **R1-ISSUE-001**: accepted_defect — Genuine critical timing defect. SPEC-07 Stage 5 requires writing a decoration with an entry_id that does not exist yet (the turn entry is created at turn completion, not at vocabulary-resolution time)
- **R1-ISSUE-002**: accepted_defect — Clear internal contradiction. §4.2 HashedColumns code array includes entry_id, session_id, sequence_number. §4.2a explicitly documents these same three columns as excluded with sound rationale (identi
- **R1-ISSUE-003**: accepted_defect — Cross-spec contradiction. SPEC-00 implies SPEC-04 persists closure reason in schema ('records a session-closure event noting the reason'). SPEC-02 editorial M2 explicitly says 'TeardownReason values a
- **R1-ISSUE-004**: accepted_defect — Duplicate ACC identifiers across specs. SPEC-00 assigns ACC-25/26/27 to DHF features; SPEC-07 assigns the same IDs to vocabulary features. Global namespace collision. Fix: renumber SPEC-07's ACC-25/26
- **R1-ISSUE-005**: accepted_defect — Real security gap. The entry-detail endpoint (stretch) takes only entryId with no sessionId capability check, allowing anyone who obtains an entryId to bypass the session-capability access model. Fix:
- **R1-ISSUE-006**: accepted_defect — Genuine query gap. The two-phase lookup in OpenOrResumeSessionCore misses locked sessions on other workstations (first query excludes locked rows, second query restricts to same workstation). A locked
- **R1-ISSUE-007**: accepted_defect — Text contradiction. §9.1 m4 references 'HTTP 400 during HTTP upgrade' for ProtocolVersion mismatch, but ProtocolVersion is carried in AddinReadyMessage which is sent after the WebSocket upgrade comple
- **R1-ISSUE-008**: accepted_defect — Contradictory recovery procedures. SPEC-00 says 'operator unlocks the session via SQL, reconciles the model state, and starts a new session' implying unlock-and-resume. SPEC-04 trigger rejects locked_
- **R1-ISSUE-009**: accepted_defect — Cross-spec DTO mismatch. SPEC-06 §4.3 response example includes undoOutcome, recordHash, previousHash which are absent from SPEC-04 §9.2 EntryListItem. The mandatory endpoint cannot be implemented fro
- **R1-ISSUE-010**: accepted_defect — Environment.Exit(1) terminates the SolidWorks host process, potentially losing unsaved work in unrelated documents. While acknowledged as intentional, a strictly better alternative exists: transition 
- **R1-ISSUE-011**: preference_only — The tenant-wide enumeration from a single session UUID is already thoroughly documented as an accepted v1 trade-off in SPEC-00 SDR-TL-012, SPEC-06 SDR-06-007, and the migration doc §4. Railway's globa
- **R1-ISSUE-012**: preference_only — Forward-compatibility concern for v2 screenshot transport. The v1 implementation is correct and complete with BYTEA columns. The v2 migration will require DTO changes regardless of what is done now. T
- **R1-ISSUE-013**: preference_only — The architecture (mocked CI + live scheduled canary) is standard practice for LLM-dependent systems. BouncerPromptSyncTests already exists. Adding a build-breaking live-model call would create flaky C
- **R1-ISSUE-014**: preference_only — Documentation hygiene issue acknowledged as low severity. The implementation details in SPEC-00 are intentional cross-references maintained in sync with per-module specs. Not a blocker for hackathon t

## Open Questions

- Should RecoveryEventContext also support PendingDecorations for future extensibility, or is the current turn-only scope sufficient?
- Should the co-transactional decoration write produce a separate decoration_id that is returned to the caller, or is the entry_id sufficient as the decoration's lookup key?
