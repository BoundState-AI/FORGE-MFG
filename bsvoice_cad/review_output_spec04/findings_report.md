# ORACLE Review Report

**Document:** D:\BoundState\specs\bsvoice_cad\SPEC-04_DHF_LEDGER.md
**Session:** 20260418-124658
**Rounds:** 2
**Converged:** No

## Metrics

- Accepted critical defects: 2
- Accepted high defects: 5
- Total accepted defects: 22
- Total rejected claims: 14
- Preference-only findings: 4
- Duplicate rate: 0.0%
- Rounds to stability: 2
- Converged: False

## Round 1

### [CRITICAL] Persisted enum values in AppendTurnAsync do not satisfy schema CHECK constraints (R1-ISSUE-001)
**Problem:** The write path calls raw .ToString() on InputModality and TurnType enums, producing PascalCase values ('Voice', 'Text', 'Execution', etc.) that do not match the lowercase/snake_case literals required by the schema CHECK constraints ('voice', 'text', 'execution', 'ambiguity_resolution'). Core entry inserts will fail at runtime.
**Fix:** Define explicit enum-to-storage mapping functions for every persisted enum (InputModality, TurnType, ToolCallLifecycleState, UndoGroupOutcome) and use them consistently in write code, validators, and hash canonicalization. Add tests for every enum value.
**Sources:** Fran, Codex

### [CRITICAL] WriteDecorationAsync enum conversion cannot produce required snake_case decoration_type literals (R1-ISSUE-002)
**Problem:** WriteDecorationAsync uses `type.ToString().ToLowerInvariant()` which produces 'rationalextraction', 'manualannotation', 'ambiguitycontext', 'integrityevent' — none of which match the schema's required snake_case literals ('rationale_extraction', 'manual_annotation', 'ambiguity_context', 'integrity_event'). All decoration inserts will fail.
**Fix:** Define a stable enum-to-storage mapping for DecorationType (e.g., attribute-based or dictionary) and reuse it in inserts, reads, and tests.
**Sources:** Codex

### [CRITICAL] AppendTurnAsync allows writes to closed sessions with no lifecycle guard (R1-ISSUE-003)
**Problem:** AppendTurnAsync reads the chain tip and inserts entries by session_id without ever checking dhf.sessions.ended_at. A stale caller holding a closed SessionId can continue appending entries after the session was closed or superseded, breaking the epoch semantics defined in SDR-04-004/005.
**Fix:** Inside the append transaction, SELECT the session row FOR UPDATE and raise an exception if ended_at IS NOT NULL. Optionally also validate that the caller's expected AddinSessionId matches the session row.
**Sources:** Fran, Codex

### [HIGH] OpenOrResumeSessionAsync advisory lock scope does not match the workstation-level mutation it performs (R1-ISSUE-004)
**Problem:** The advisory lock key includes addinSessionId (`dhf_session_open:{workstationId}:{addinSessionId}`), so concurrent open/resume calls with different addinSessionIds on the same workstation take different locks. However, the UPDATE that closes prior sessions operates on all open sessions for the workstation. This mismatch allows concurrent races that can close the wrong session or leave multiple open sessions.
**Fix:** Lock on workstation only (drop addinSessionId from the lock key) to match the scope of the UPDATE, or narrow the UPDATE to only close sessions with a different addinSessionId and document the resulting semantics.
**Sources:** Fran, Codex

### [HIGH] Documented unique-violation retry fallback for session open is not implemented (R1-ISSUE-005)
**Problem:** The spec states 'the second will fail on unique-violation and retry via the lookup path' but OpenOrResumeSessionAsync contains no catch/retry loop for SQLSTATE 23505 or serialization failures (40001). Additionally, even if retry were added, the lookup filters by workstation_id which would miss a conflict caused by an open row on a different workstation. Concurrent orchestrator startup will surface raw DB failures.
**Fix:** Add explicit bounded retry handling for SQLSTATE 23505 and 40001. Ensure the retry re-reads the open session using the same scope as the unique index (addin_session_id only, not workstation-scoped) to correctly recover from cross-workstation conflicts.
**Sources:** Fran, Codex

### [HIGH] entry_decorations mutability policy is internally contradictory (R1-ISSUE-006)
**Problem:** SDR-04-002 states decorations are 'insert-only' and 'never mutated in place.' The trigger comment in 005_immutability_triggers.sql states 'UPDATE and DELETE are permitted for operational cleanup, so no trigger.' These are incompatible policies. Consumers and implementers cannot determine whether decoration mutation is a defect or an allowed operation.
**Fix:** Choose one policy. If insert-only, add triggers and remove the cleanup allowance. If cleanup is permitted, define the allowed operations, who may perform them, and how cleanup events are themselves audited.
**Sources:** Fran, Codex

### [HIGH] Viewer page-load shallow validation writes integrity_event decorations with no deduplication (R1-ISSUE-007)
**Problem:** Section 10.1 states shallow validation runs on every viewer page load. Section 8.2 states any integrity check failure is written as an integrity_event decoration. No deduplication or idempotency rule is defined. A persistent mismatch generates a new decoration row on every page load, creating unbounded storage growth and making a nominally read-only path a write source.
**Fix:** Either make page-load validation side-effect free (no writes), or define explicit deduplication semantics (e.g., check for existing open integrity_event of same type before inserting) and move integrity-event writes off the viewer read path.
**Sources:** Fran, Codex

### [HIGH] Tool-call addinSessionId not validated against owning DHF session during append (R1-ISSUE-008)
**Problem:** ToolCallRecord carries its own AddinSessionId field. AppendTurnAsync inserts tool call rows without comparing their AddinSessionId to the owning session's addin_session_id. The schema has no constraint preventing this. Mixed-epoch tool calls can be recorded under a single entry, undermining SDR-04-004/005.
**Fix:** Either remove the redundant per-tool-call addinSessionId (deriving it from the session) or enforce equality with the owning session's addin_session_id during append and add a DB-level constraint.
**Sources:** Codex

### [HIGH] DbUp journal table bootstrap dependency: journal lives in schema created by first migration (R1-ISSUE-009)
**Problem:** DbUp must read its journal table to determine which scripts to run, but dhf.schema_versions is defined inside 001_initial_schema.sql which also creates the dhf schema. On a fresh database, DbUp cannot access the journal before running the migration that creates it.
**Fix:** Specify a bootstrap step that creates the dhf schema and journal table before DbUp migration discovery runs, or configure DbUp to use a pre-existing schema (e.g., public) for its journal.
**Sources:** Codex

### [HIGH] Regulatory table incorrectly claims user identity is stored per entry; schema stores it per session only (R1-ISSUE-010)
**Problem:** Section 12 regulatory table states 'User identity stored per entry' as partial coverage for 21 CFR Part 11 §11.70. However, user_identity exists only on dhf.sessions; dhf.entries has no user identity column. This misstates audit capability.
**Fix:** Correct the regulatory table claim to 'per session' or add immutable user identity fields to dhf.entries if per-entry attribution is actually required for the regulatory framing.
**Sources:** Codex

### [HIGH] Read API (IDhfRepository) exposes undefined DTO contracts to downstream consumers (R1-ISSUE-011)
**Problem:** IDhfRepository returns DhfSessionSummary, DhfSessionDetail, DhfEntrySummary, DhfEntryDetail, ToolCallRow, and DecorationRow, but none of these types are defined in this spec or listed as imported dependencies. Field lists, nullability, sort order, and pagination semantics are all absent.
**Fix:** Define all repository DTOs with field lists, nullability, sort order, and pagination semantics, or explicitly reference the spec where they are defined.
**Sources:** Codex

### [MEDIUM] Timestamp normalization for hashing is unspecified; TIMESTAMPTZ round-trip may produce hash mismatches (R1-ISSUE-012)
**Problem:** Section 4.1 defines HashCanonicalizer rules but does not specify how DateTimeOffset/TIMESTAMPTZ values are normalized before hashing. Postgres TIMESTAMPTZ normalizes to UTC and may truncate sub-microsecond precision. If the in-memory DateTimeOffset serializes differently than the value read back from Postgres, shallow validation will produce false failures.
**Fix:** Require UTC normalization and fixed precision (e.g., microseconds) before hashing and before INSERT, or store the canonical timestamp string that was hashed alongside the TIMESTAMPTZ column.
**Sources:** Codex

### [MEDIUM] Immutability enforcement is ineffective against the orchestrator itself given DROP TRIGGER privilege (R1-ISSUE-013)
**Problem:** The spec acknowledges the orchestrator has DROP TRIGGER privilege and can drop triggers, mutate rows, and recreate triggers. The 'defense-in-depth' framing is accurate but the triggers provide no protection against the sole writer. This weakens the regulatory foundation claim.
**Fix:** The spec already discloses this honestly and defers to post-hackathon role separation. For the current scope, ensure the regulatory framing consistently says 'foundation' not 'enforcement.' Post-hackathon: separate DBA and application roles as noted.
**Sources:** Grok

### [MEDIUM] Deep validation creates brittle dependency on filesystem state for ACC-02 acceptance criterion (R1-ISSUE-014)
**Problem:** ACC-02 requires deep validation to pass with zero warnings, but deep validation recomputes screenshot SHA-256 from files on disk. Any container restart, file move, or premature cleanup breaks ACC-02. The spec mitigates this with a manual 'do not run cleanup-screenshots.ps1' instruction, which is fragile.
**Fix:** Store screenshot SHA-256 at capture time as the authoritative value. Make deep validation treat missing files as warnings (already done) but also document that ACC-02 can pass with warnings if screenshots are absent, or pin screenshots to a test fixture path.
**Sources:** Grok

### [MEDIUM] Session isolation relies on workstation_id which may be spoofable or shared across VMs (R1-ISSUE-015)
**Problem:** The close-other-sessions logic and advisory lock scoping both rely on workstation_id as a trust boundary. If workstation_id can be spoofed, cloned, or shared (e.g., VMs, containers), one orchestrator instance can prematurely close another's sessions.
**Fix:** Document the trust assumption for workstation_id explicitly. For hackathon scope this is acceptable; post-hackathon consider a stronger machine fingerprint or accept that the close-other-sessions behavior is intentional (only one orchestrator per workstation).
**Sources:** Grok

### [MEDIUM] Hash canonicalization column lists are a brittle synchronization point with schema evolution (R1-ISSUE-016)
**Problem:** DhfHashColumns and ToolCallHashColumns are static ordered arrays. Any added column, renamed field, or DTO change not perfectly mirrored in both lists silently breaks validation or creates unverifiable historical entries. The design chose manual column lists over a single canonical serialization of the DTO.
**Fix:** Add architecture tests that assert the column lists are complete and consistent with the schema and DTOs. Consider whether a single canonical DTO serialization (excluding implementation IDs) would be simpler and less error-prone for future schema evolution.
**Sources:** Grok

### [LOW] Writer computes its own hashes with no external witness; self-attestation limits regulatory credibility (R1-ISSUE-017)
**Problem:** The orchestrator both generates events and computes/stores their hashes. A compromised orchestrator can fabricate transcripts and matching hashes; the chain validates but contains chosen lies. This is a fundamental limitation of self-attesting audit logs.
**Fix:** The spec already frames this as 'foundation' not 'compliant.' For hackathon scope this is acceptable. Post-hackathon: consider a separate privileged hash-computation service or external witness. No immediate action required beyond ensuring regulatory language stays conservative.
**Sources:** Grok

### Coordinator Decisions
- **R1-ISSUE-001**: accepted_defect — PascalCase .ToString() on C# enums produces values that violate Postgres CHECK constraints. Every INSERT into dhf.entries will fail at runtime. Definitive runtime failure with clear evidence.
- **R1-ISSUE-002**: accepted_defect — Same class as ISSUE-001. ToString().ToLowerInvariant() on PascalCase multi-word enum values cannot produce snake_case. Every decoration INSERT will fail.
- **R1-ISSUE-003**: accepted_defect — No guard against writing to a closed session breaks epoch semantics defined in SDR-04-004/005. Stale callers can corrupt the ledger.
- **R1-ISSUE-004**: accepted_defect — Lock key includes addinSessionId but UPDATE mutates all open sessions for the workstation. Different addinSessionIds take different locks, allowing concurrent races on overlapping data.
- **R1-ISSUE-005**: accepted_defect — Spec claims retry-on-race behavior that does not exist in the code. Lookup scope does not match unique index scope. Two independent defects that surface as raw DB exceptions.
- **R1-ISSUE-006**: accepted_defect — SDR-04-002 says 'insert-only, never mutated' but trigger comment says 'UPDATE and DELETE are permitted.' These are incompatible statements. Resolved by clarifying policy: application-level insert-only
- **R1-ISSUE-007**: accepted_defect — Shallow validation on every page load writing decorations creates unbounded growth and makes a nominally read-only path into a write path. Fix: shallow validation is side-effect-free; integrity_event 
- **R1-ISSUE-008**: accepted_defect — Per-tool-call AddinSessionId is never validated against the owning session. Mixed-epoch tool calls can be recorded under a single entry, violating SDR-04-004.
- **R1-ISSUE-009**: accepted_defect — DbUp cannot read its journal table if the schema containing it hasn't been created yet. Bootstrap deadlock. Fix: journal to public.dbup_journal.
- **R1-ISSUE-010**: accepted_defect — Factual error in regulatory table. user_identity exists on dhf.sessions, not dhf.entries. The claim 'per entry' is false.
- **R1-ISSUE-011**: accepted_defect — IDhfRepository exposes 6 types (DhfSessionSummary, DhfSessionDetail, etc.) with no definitions anywhere. SPEC-06 and DhfRepositoryTests cannot be implemented against undefined contracts.
- **R1-ISSUE-012**: accepted_defect — DateTimeOffset round-trips through Postgres TIMESTAMPTZ with potential sub-microsecond truncation. Hash computed pre-INSERT may not match hash recomputed from read-back value. Concrete risk of false s
- **R1-ISSUE-013**: preference_only — The spec already honestly discloses this in Section 3.2, uses 'foundation' framing throughout Section 12, and defers role separation to post-hackathon. The reviewer's own recommendation is 'no immedia
- **R1-ISSUE-014**: accepted_defect — ACC-02 requiring zero warnings from deep validation creates a brittle dependency on screenshot file retention. A container restart before ACC-02 execution fails the acceptance criterion for a non-inte
- **R1-ISSUE-015**: accepted_defect — The workstation_id trust assumption is implicit and governs session isolation. Should be documented explicitly as SDR-04-008 so implementers and reviewers understand the boundary.
- **R1-ISSUE-016**: accepted_defect — Static column lists with no automated sync check are a known class of maintenance defect. Adding architecture tests is a concrete, low-cost guard.
- **R1-ISSUE-017**: preference_only — Self-attesting audit logs are a fundamental limitation acknowledged in Section 3.2 and Section 12. The spec's regulatory language is already conservative ('foundation,' not 'compliant'). The reviewer'

## Round 2

### [LOW] Persisted enum values in AppendTurnAsync do not satisfy schema CHECK constraints (R1-ISSUE-001)
**Problem:** R1 issue addressed in post-R1 revision: DhfEnumMappings.ToStorage() is now used throughout AppendTurnAsync and WriteDecorationAsync. SDR-04-009 and DHF-010 codify the requirement. Architecture tests enforce it. No reviewer flagged a residual gap in this specific area.
**Fix:** No action required. Issue resolved in R1 revision.

### [LOW] WriteDecorationAsync enum conversion cannot produce required snake_case decoration_type literals (R1-ISSUE-002)
**Problem:** R1 issue addressed: Section 8 WriteDecorationAsync now calls type.ToStorage() and DhfEnumMappings includes DecorationType mapping. No reviewer flagged a residual gap.
**Fix:** No action required. Issue resolved in R1 revision.

### [LOW] AppendTurnAsync allows writes to closed sessions with no lifecycle guard (R1-ISSUE-003)
**Problem:** R1 issue addressed: Section 7 now includes explicit session-open guard that throws InvalidOperationException if session is closed or nonexistent. No reviewer flagged a residual gap.
**Fix:** No action required. Issue resolved in R1 revision.

### [LOW] OpenOrResumeSessionAsync advisory lock scope does not match the workstation-level mutation it performs (R1-ISSUE-004)
**Problem:** R1 issue addressed: Section 6.1 now uses workstation-scoped advisory lock key 'dhf_session_open:{metadata.WorkstationId}' matching the scope of the UPDATE that closes prior sessions.
**Fix:** No action required. Issue resolved in R1 revision.

### [LOW] Documented unique-violation retry fallback for session open is not implemented (R1-ISSUE-005)
**Problem:** R1 issue addressed: Section 6.1 now includes bounded retry loop handling both SerializationFailure (40001) and UniqueViolation (23505) with exponential backoff.
**Fix:** No action required. Issue resolved in R1 revision. Grok's finding about retry insufficiency is tracked separately as R2-ISSUE-010.

### [LOW] entry_decorations mutability policy is internally contradictory (R1-ISSUE-006)
**Problem:** R1 issue addressed: SDR-04-002 now clearly distinguishes insert-with-supersede model from entries/tool_calls immutability, explains why no DB trigger is installed on decorations, and states the application-level invariant enforced by architecture tests.
**Fix:** No action required. Issue resolved in R1 revision.

### [LOW] Viewer page-load shallow validation writes integrity_event decorations with no deduplication (R1-ISSUE-007)
**Problem:** R1 issue addressed: Section 10.1 now explicitly states shallow validation is 'Side-effect-free: returns results in-memory only, does not write decorations or any other rows.' Only deep validation writes integrity_event decorations.
**Fix:** No action required. Issue resolved in R1 revision.

### [LOW] Tool-call addinSessionId not validated against owning DHF session during append (R1-ISSUE-008)
**Problem:** R1 issue addressed: Section 7 step 0 now validates each tool call's AddinSessionId against the session's addin_session_id, throwing InvalidOperationException on mismatch.
**Fix:** No action required. Issue resolved in R1 revision.

### [LOW] DbUp journal table bootstrap dependency: journal lives in schema created by first migration (R1-ISSUE-009)
**Problem:** R1 issue addressed: Section 3.1 now explicitly states 'DbUp journals to public.dbup_journal (its default table in the public schema), avoiding a bootstrap dependency on the dhf schema that has not yet been created.'
**Fix:** No action required. Issue resolved in R1 revision.

### [LOW] Regulatory table incorrectly claims user identity is stored per entry; schema stores it per session only (R1-ISSUE-010)
**Problem:** R1 issue addressed: Section 12 regulatory table now correctly states 'User identity stored per session (joined to entries via session_id)' under §11.70.
**Fix:** No action required. Issue resolved in R1 revision.

### [LOW] Read API (IDhfRepository) exposes undefined DTO contracts to downstream consumers (R1-ISSUE-011)
**Problem:** R1 issue addressed: Section 9.1 now defines all repository DTOs in full: DhfSessionSummary, DhfSessionDetail, DhfEntrySummary, DhfEntryDetail, ToolCallRow, DecorationRow.
**Fix:** No action required. Issue resolved in R1 revision.

### [CRITICAL] Timestamp normalization for hashing is unspecified; TIMESTAMPTZ round-trip may produce hash mismatches (R1-ISSUE-012)
**Problem:** The spec now defines the canonicalization rule correctly in Section 4.1, but the AppendTurnAsync code in Section 7 contradicts it: step 3 computes canonicalTimestamp, but step 4 sets Timestamp = turn.Timestamp (raw) while CanonicalTimestamp = canonicalTimestamp (used for hash). The DB INSERT receives the raw timestamp, not the canonical one. If turn.Timestamp has a non-UTC offset or sub-microsecond precision, Postgres stores a value that differs from what was hashed, causing shallow validation to fail on legitimate rows.
**Fix:** Normalize turn.Timestamp to UTC microsecond precision once before constructing DhfEntryRecord. Use that single normalized DateTimeOffset for both the DB INSERT parameter and the hash input. Remove the separate CanonicalTimestamp field or make it the sole source for both paths. Add TimestampCanonTests covering non-UTC offsets and 7-digit fractional ticks.
**Sources:** Codex, Fran

### [HIGH] Immutability enforcement is ineffective against the orchestrator itself given DROP TRIGGER privilege (R1-ISSUE-013)
**Problem:** The orchestrator DB user has DROP TRIGGER privilege, meaning it can unilaterally bypass all append-only enforcement, mutate rows, and recreate triggers. The spec acknowledges this in Section 3.2 but defers remediation to post-hackathon. Grok escalates this to critical; the prior round scored it medium. The core fact is unchanged and acknowledged. The 'defense-in-depth' framing is accurate for hackathon scope but the regulatory framing in Section 12 must not overstate the protection.
**Fix:** For hackathon: ensure Section 12 regulatory language does not overstate protection (currently acceptable). Post-hackathon: create a separate read-write application role without DROP TRIGGER privilege; use a SECURITY DEFINER maintenance function owned by a DBA role. Do not use 'defense-in-depth' language in regulatory submissions until role separation is implemented.
**Sources:** GrokContrarian

### [LOW] Deep validation creates brittle dependency on filesystem state for ACC-02 acceptance criterion (R1-ISSUE-014)
**Problem:** R1 issue partially addressed: Section 10.2 now states 'Missing screenshot files produce warnings, not failures' and ACC-02 states 'warnings logged but non-blocking'. Section 13 ACC-02 criterion clarifies 'When screenshots are present in the test fixture, they MUST hash-verify cleanly.' This is a reasonable resolution for hackathon scope.
**Fix:** No action required. Issue adequately resolved for hackathon scope.

### [HIGH] Session isolation relies on workstation_id which may be spoofable or shared across VMs (R1-ISSUE-015)
**Problem:** SDR-04-008 acknowledges workstation_id is spoofable/collidable but defers remediation. The cross-workstation branch in Section 6.1 automatically closes an existing open session on another workstation when addin_session_id matches, even though the trust boundary is explicitly acknowledged as weak. This can silently split another workstation's ledger session. Grok and Codex both flag this; Codex specifically notes the auto-close behavior is the dangerous action, not just the identifier weakness.
**Fix:** For hackathon: acceptable given single-orchestrator-per-workstation constraint; ensure SDR-04-008 post-hackathon note is prominent. Immediate improvement: do not auto-close cross-workstation sessions silently — require explicit operator confirmation or reject as an error requiring manual resolution. Post-hackathon: replace Environment.MachineName with a cryptographically generated persistent workstation token stored in config.
**Sources:** GrokContrarian, Codex

### [MEDIUM] Hash canonicalization column lists are a brittle synchronization point with schema evolution (R1-ISSUE-016)
**Problem:** DhfHashColumns and ToolCallHashColumns are static ordered lists that must be manually kept in sync with schema migrations, DTOs, and the HashColumnSyncTests allowlist. Any added column, renamed field, or new enum value requires coordinated changes across multiple files. The HashColumnSyncTests mitigate but do not eliminate the risk. Grok proposes reflection-based derivation; this is a valid long-term improvement but not a hackathon blocker given the sync test exists.
**Fix:** For hackathon: HashColumnSyncTests provide adequate protection. Post-hackathon: consider [Hashed] attribute-driven reflection to derive column lists from DTOs, eliminating the manual sync surface.
**Sources:** GrokContrarian

### [LOW] Writer computes its own hashes with no external witness; self-attestation limits regulatory credibility (R1-ISSUE-017)
**Problem:** No reviewer raised this in R2. Retaining from R1 ledger at low severity. The hash chain is self-attested by the same process that writes it, which limits its evidentiary weight in regulatory contexts. Acknowledged in Section 12 regulatory framing.
**Fix:** Post-hackathon: consider an independent witness service or notarization step for hash chain anchoring.

### [CRITICAL] dhf.sessions metadata columns are fully mutable with no trigger protection; session attribution can be rewritten without breaking entry hash chain (R2-ISSUE-001)
**Problem:** The immutability triggers in migration 005 protect dhf.entries and dhf.tool_calls but not dhf.sessions. The sessions table allows unrestricted UPDATE to all columns including user_identity, workstation_id, sw_version, addin_version, orchestrator_version, protocol_version, addin_session_id, and started_at. None of these fields are covered by the entry hash chain (entries only store session_id as a FK). An attacker or buggy orchestrator can rewrite who performed the actions, where, and under which software version, while all entry hashes continue to validate correctly.
**Fix:** Add a DB trigger on dhf.sessions that permits only the ended_at column to transition from NULL to a non-NULL value (and rejects all other UPDATE operations). If session metadata must be auditable, compute and store a session_metadata_hash at INSERT time and include it in the first entry's previous_hash or as a separate integrity anchor. Add ImmutabilityTriggerTests coverage for sessions.
**Sources:** Fran

### [HIGH] Graceful shutdown closes session, contradicting SDR-04-005 resume semantics for transient orchestrator disconnect (R2-ISSUE-002)
**Problem:** SDR-04-005 states that same-AddinSessionId reconnect resumes the prior session, distinguishing genuine add-in reload (new ID) from transient orchestrator disconnect (same ID). However, Section 6.2 states that graceful shutdown calls CloseSessionAsync which sets ended_at = NOW(). If the orchestrator restarts cleanly while the add-in remains loaded with the same AddinSessionId, the session is closed, and the next AddinReadyMessage opens a new session rather than resuming. This makes session boundaries dependent on orchestrator process lifecycle, not add-in epoch, contradicting the stated design intent.
**Fix:** Choose one of: (A) Do not close the session on graceful orchestrator shutdown; leave it open so a restart with the same AddinSessionId resumes correctly. Close only on add-in disconnect or new AddinSessionId. (B) Explicitly redefine graceful orchestrator shutdown as a session-ending event, update SDR-04-005 to reflect this, update ACC-15 acceptance criteria, and document that orchestrator restart always creates a new session regardless of AddinSessionId continuity.
**Sources:** Fran

### [HIGH] WriteDecorationAsync has no supersede parameter; insert-with-supersede model cannot be implemented as specified (R2-ISSUE-003)
**Problem:** SDR-04-002 defines an insert-with-supersede model where new decoration rows reference supersedes_decoration_id to logically replace a prior decoration. The dhf.entry_decorations schema includes the supersedes_decoration_id FK column. However, the WriteDecorationAsync signature takes only (entryId, type, payload, createdBy, ct) and the INSERT statement omits supersedes_decoration_id entirely. There is no API path to supply the prior decoration ID, making the supersede model unimplementable as written. Additionally, there is no defined read contract for resolving which decoration is current after replacement.
**Fix:** Add an optional Guid? supersedesDecorationId parameter to WriteDecorationAsync. Include it in the INSERT when non-null. Define the read contract: GetDecorationsAsync should indicate which decorations are superseded (e.g., a boolean IsSuperseded computed from whether any other row references this decoration_id). Add DecorationWriterTests coverage for the supersede path.
**Sources:** Codex

### [HIGH] Integrity-event deduplication key is ambiguous and not enforced by DB constraint; concurrent deep validations can produce duplicates (R2-ISSUE-004)
**Problem:** Section 8.2 states deduplication checks for 'an existing integrity_event decoration of the same eventType on the same entry' but also says 'If an identical event already exists, no new row is written' — these are two different keys (eventType alone vs. full event identity). The dhf.entry_decorations table has no unique index supporting either rule. Concurrent deep validation runs can race past the check-then-insert and produce duplicate rows. Additionally, using eventType alone as the dedup key can suppress distinct failures that share a type (e.g., two different screenshot_hash_mismatch events on the same entry for before and after screenshots).
**Fix:** Define a single explicit dedup key, e.g., (entry_id, decoration_type, decoration_hash) or (entry_id, eventType, affected_artifact_path). Add a partial unique index on dhf.entry_decorations for integrity_event rows using that key. Use INSERT ... ON CONFLICT DO NOTHING to make the dedup atomic. Update Section 8.2 to use consistent language matching the enforced key.
**Sources:** Codex, Fran

### [HIGH] Session close/split operations are not coordinated with append operations; turns can be lost or committed post-close (R2-ISSUE-005)
**Problem:** OpenOrResumeSessionCore uses a workstation-scoped advisory lock ('dhf_session_open:{workstationId}'). AppendTurnAsync uses a session-scoped advisory lock ('dhf_append:{sessionId}'). CloseSessionAsync (Section 6.2) acquires no lock at all. These lock domains do not coordinate: a session can be closed or split while an append transaction is in flight. The append's session-open guard reads ended_at at the start of the transaction, but a concurrent close can set ended_at after that read and before the INSERT commits. Additionally, AppendTurnAsync has no retry path for serialization failures (40001), unlike OpenOrResumeSessionAsync.
**Fix:** Make CloseSessionAsync acquire the same per-session advisory lock ('dhf_append:{sessionId}') before setting ended_at, ensuring it cannot race with an in-flight append. Alternatively, use SELECT ... FOR UPDATE on the session row in both close and append paths. Add bounded retry handling for serialization failures in AppendTurnAsync matching the pattern in OpenOrResumeSessionAsync.
**Sources:** Codex

### [MEDIUM] Session open retry loop has no jitter and AppendTurnAsync has no equivalent retry; insufficient for sustained contention (R2-ISSUE-006)
**Problem:** The OpenOrResumeSessionAsync retry loop uses fixed linear backoff (50ms * attempt) with no jitter and only 3 attempts. Under thundering-herd reconnect scenarios (multiple orchestrators restarting simultaneously), all retriers will collide at the same intervals. AppendTurnAsync has no retry wrapper at all despite using Serializable isolation, which can produce 40001 errors under concurrent appends.
**Fix:** Add jitter to the session open retry (e.g., random 0–50ms added to each delay). Add a bounded retry loop to AppendTurnAsync for SerializationFailure (40001) with the same jitter pattern. Consider increasing maxRetries to 5 for both paths. Document maximum expected lock acquisition time.
**Sources:** GrokContrarian

### Coordinator Decisions
- **R1-ISSUE-001**: rejected_claim — Confirmed resolved in R1 revision. DhfEnumMappings.ToStorage() is used throughout. No R2 reviewer flagged a residual gap.
- **R1-ISSUE-002**: rejected_claim — Confirmed resolved in R1 revision. WriteDecorationAsync calls type.ToStorage(). No R2 reviewer flagged a residual gap.
- **R1-ISSUE-003**: rejected_claim — Confirmed resolved in R1 revision. Session-open guard present in AppendTurnAsync step 0.
- **R1-ISSUE-004**: rejected_claim — Confirmed resolved in R1 revision. Advisory lock now scoped to workstation.
- **R1-ISSUE-005**: rejected_claim — Confirmed resolved in R1 revision. Bounded retry loop with SerializationFailure and UniqueViolation handling present.
- **R1-ISSUE-006**: rejected_claim — Confirmed resolved in R1 revision. SDR-04-002 clearly distinguishes entries/tool_calls immutability from decoration insert-with-supersede model.
- **R1-ISSUE-007**: rejected_claim — Confirmed resolved in R1 revision. Shallow validation is explicitly side-effect-free.
- **R1-ISSUE-008**: rejected_claim — Confirmed resolved in R1 revision. AddinSessionId validation present in AppendTurnAsync.
- **R1-ISSUE-009**: rejected_claim — Confirmed resolved in R1 revision. DbUp journals to public.dbup_journal.
- **R1-ISSUE-010**: rejected_claim — Confirmed resolved in R1 revision. Regulatory table correctly states per-session identity.
- **R1-ISSUE-011**: rejected_claim — Confirmed resolved in R1 revision. Section 9.1 defines all repository DTOs.
- **R1-ISSUE-012**: accepted_defect — Residual defect identified by Codex and Fran in R2: AppendTurnAsync step 4 sets Timestamp = turn.Timestamp (raw) while CanonicalTimestamp = canonicalTimestamp (normalized). DB INSERT receives raw valu
- **R1-ISSUE-013**: preference_only — No new evidence in R2. Spec already honestly discloses DROP TRIGGER privilege gap in Section 3.2 and uses 'foundation' framing in Section 12. Post-hackathon remediation plan documented.
- **R1-ISSUE-014**: rejected_claim — Confirmed resolved in R1 revision. ACC-02 requires zero failures; warnings are non-blocking.
- **R1-ISSUE-015**: rejected_claim — Confirmed resolved in R1 revision via SDR-04-008.
- **R1-ISSUE-016**: rejected_claim — Confirmed resolved in R1 revision via HashColumnSyncTests.
- **R1-ISSUE-017**: preference_only — No new evidence in R2. Self-attestation limitation acknowledged in Section 3.2 and Section 12.
- **R2-ISSUE-001**: accepted_defect — Critical governance gap. dhf.sessions has no trigger protection; user_identity, workstation_id, sw_version, and all metadata columns can be rewritten without breaking the entry hash chain. An attacker
- **R2-ISSUE-002**: accepted_defect — Genuine contradiction. SDR-04-005 says same-AddinSessionId reconnect resumes, but Section 6.2 closes the session on graceful shutdown. If the orchestrator restarts while the add-in is still loaded (sa
- **R2-ISSUE-003**: accepted_defect — Clear API contract gap. SDR-04-002 defines insert-with-supersede; schema has supersedes_decoration_id column; but WriteDecorationAsync has no parameter for it and the INSERT omits it. The supersede mo
- **R2-ISSUE-004**: accepted_defect — Inconsistent language ('same eventType' vs 'identical event'), no DB enforcement, concurrent deep validations can race, and eventType alone suppresses distinct failures sharing the same type. Fix: req
- **R2-ISSUE-005**: accepted_defect — Real concurrency gap. CloseSessionAsync acquires no lock and can race with in-flight AppendTurnAsync. Fix: CloseSessionAsync acquires per-session advisory lock ('dhf_append:{sessionId}'); AppendTurnAs
- **R2-ISSUE-006**: accepted_defect — Operational correctness issue. Fixed linear backoff with no jitter causes thundering-herd collisions under concurrent startup. AppendTurnAsync has no retry at all despite Serializable isolation. Fix: 

## Open Questions

- Should decoration cleanup operations (post-hackathon) be audited in their own integrity_event decoration? Deferred.
- Should tool_call_id be added to ToolCallHashColumns to prevent silent PK replacement? Current decision is no (PK is implementation detail, not semantic content), but reviewable.
- Should a cross-workstation same-addinSessionId be treated as an error or silently close the stale session? Current decision: close stale and proceed, with logging.
