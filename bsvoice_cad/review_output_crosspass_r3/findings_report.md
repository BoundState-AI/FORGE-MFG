# ORACLE Review Report

**Document:** D:\BoundState\specs\bsvoice_cad\BUNDLE_FOR_CROSSPASS.md
**Session:** 20260418-172705
**Rounds:** 2
**Converged:** No

## Metrics

- Accepted critical defects: 1
- Accepted high defects: 13
- Total accepted defects: 20
- Total rejected claims: 0
- Preference-only findings: 6
- Duplicate rate: 0.0%
- Rounds to stability: 2
- Converged: False

## Round 1

### [CRITICAL] Session lock after DHF append failure is in-memory only — not durable across orchestrator restart (R1-ISSUE-001)
**Problem:** SPEC-04 DHF-014 states 'the session is marked locked in memory,' but SPEC-04 SDR-04-005 and the glossary explicitly preserve DHF sessions across orchestrator restart. After a restart, `OpenOrResumeSessionAsync` sees `ended_at IS NULL` and resumes the session with no lock check, allowing further writes onto a compromised audit chain. This defeats a never-descopable invariant. Grok additionally notes there is no `LockSessionAsync` primitive defined, leaving the exception path ambiguous.
**Fix:** Persist lock state in `dhf.sessions` (e.g., add `locked_at TIMESTAMPTZ NULL` and `lock_reason TEXT NULL` columns in a migration 007). Make `OpenOrResumeSessionAsync` reject locked sessions and `AppendTurnAsync` check the lock before acquiring the advisory lock. Alternatively, close the session transactionally on append failure so restart cannot reopen it. Either way, define an explicit `IDhfRepository.LockSessionAsync` or `CloseSessionAsync`-on-failure path.
**Sources:** Fran, Codex, Grok

### [CRITICAL] Screenshot bytes have no defined storage column or read DTO — deep validation and viewer rendering are unimplementable (R1-ISSUE-002)
**Problem:** Migration 003 defines only `screenshot_before_path`, `screenshot_after_path`, `screenshot_before_sha256`, `screenshot_after_sha256`. SPEC-04 §10.2 claims 'screenshots are stored inline in DHF as `screenshot_png` bytea' but no such column exists in the schema. SPEC-04 `EntryDetail` read DTO contains only SHA-256 fields. SPEC-06 §5.2 expects `screenshotBefore.dataUrl` in entry-detail responses. Both deep hash validation and viewer screenshot rendering depend on data the schema does not store.
**Fix:** Add explicit screenshot storage to the schema (e.g., `screenshot_before_png BYTEA NULL`, `screenshot_after_png BYTEA NULL` in a new migration, or a separate `dhf.entry_screenshots` artifact table with S3 key for v2). Update `EntryDetail` read DTO to include the bytes or URL. Alternatively, explicitly remove screenshot-bytes-dependent deep validation and viewer rendering from v1 scope everywhere it is currently promised.
**Sources:** Fran, Codex

### [CRITICAL] Unauthenticated viewer exposes session enumeration — URL-as-capability model is broken by GET / and ?tenant= override (R1-ISSUE-003)
**Problem:** SPEC-00 SDR-TL-012 and SPEC-06 SDR-06-007 claim the v1 access model is 'knowledge of the session UUID.' However: (1) `GET /` auto-loads the most recent session for `DefaultTenantId = 'default'` without requiring any session UUID; (2) `GET /api/dhf/sessions` lists all session IDs for the resolved tenant; (3) the tenant resolver accepts an anonymous `?tenant=X` query parameter and returns that tenant's session list. Together these allow any internet user to enumerate session IDs and metadata without possessing a capability token, completely invalidating the stated security model.
**Fix:** Make `GET /` a neutral landing page that requires a `sessionId` query parameter (or authenticated session in v1.5). Remove the anonymous `?tenant=` override entirely in v1. Require possession of a valid session UUID before returning any session content or metadata. If session listing is needed, gate it behind the same API key used for the WebSocket (or defer to v1.5 Clerk).
**Sources:** Fran, Codex, Codex, Grok

### [CRITICAL] Recovery events have no defined schema, storage, or hash-chain representation — AppendRecoveryEventAsync is unimplementable (R1-ISSUE-004)
**Problem:** SPEC-00 Seam B and Seam C mandate recovery events as never-descopable. The SPEC-00 glossary defines 'Recovery Event: A DHF entry (distinct from a turn entry) ... Appended via DhfLedger.AppendRecoveryEventAsync. Participates in the hash chain.' SPEC-04 exposes `AppendRecoveryEventAsync` in `IDhfRepository`. However: `dhf.entries.turn_type` CHECK constraint only allows `('execution', 'clarification', 'confirmation', 'ambiguity_resolution')` — no recovery type. No separate recovery table is defined. No `RecoveryEventContext` DTO is defined. No hash-chain sequencing rule for recovery events is specified.
**Fix:** Define one authoritative persistence model: either (a) add `'recovery'` to the `turn_type` CHECK constraint and define the recovery-specific columns/fields, or (b) add a separate `dhf.recovery_events` table with its own hash-chain sequencing rules. Define `RecoveryEventContext` DTO. Specify how recovery events interleave with turn entries in the hash chain (e.g., `previous_hash` links).
**Sources:** Fran

### [HIGH] Handshake failure transport is contradictory between SPEC-01 and the migration document (R1-ISSUE-005)
**Problem:** SPEC-01 §12.4 defines HTTP 400 for format errors and HTTP 401 for auth failure — these are pre-upgrade HTTP responses. The migration document §2 states 'Handshake failure: orchestrator responds with WebSocket close code 4401 (custom, "auth failed").' A WebSocket close code cannot be sent before the HTTP upgrade succeeds. The add-in's reconnect and error-handling logic (SPEC-02 §6.2) depends on which failure channel is authoritative.
**Fix:** Designate SPEC-01 §12.4 as authoritative. Pre-upgrade failures use HTTP 400/401 (the WebSocket upgrade is rejected at the HTTP layer). Reserve close code 4401 exclusively for post-upgrade revocation (key revoked while connection is live, per SPEC-01 §12.4 last bullet). Update the migration document §2 to match.
**Sources:** Codex

### [HIGH] DHF session closure on disconnect conflicts with same-session reconnect semantics (R1-ISSUE-006)
**Problem:** SPEC-00 §6 Glossary defines `explicit_shutdown` as triggered when 'the orchestrator detects add-in disconnect.' SPEC-01 §12.7 states idle timeout closes the connection and 'Add-in reconnects automatically; reconnect emits a new AddinReadyMessage with the same AddinSessionId, resuming the DHF session per SPEC-04 SDR-04-005.' SPEC-04 SDR-04-005 says same-AddinSessionId reconnect resumes the prior session. The spec cannot both close the DHF session on disconnect detection and resume it on reconnect with the same ID.
**Fix:** Narrow `explicit_shutdown` to only cover cases where the add-in sends an explicit teardown message (which currently doesn't exist in the protocol — see R1-ISSUE-007). Transient transport loss (idle timeout, network drop) must NOT close the DHF session; it should remain open pending reconnect or heartbeat timeout. Update the glossary to reflect this distinction clearly.
**Sources:** Codex

### [HIGH] `explicit_shutdown` DHF closure reason is not implementable — no teardown message exists in the protocol (R1-ISSUE-007)
**Problem:** SPEC-00 §6 Glossary defines `explicit_shutdown` as triggered when 'add-in sends a teardown message.' The same paragraph notes 'SPEC-02 must emit an explicit teardown message before process exit' if finer-grained attribution is needed. However, SPEC-01 §4.2 defines `IAddinMessage` types as only `tool_result`, `state_update`, `addin_ready`, `undo_group_outcome`, `addin_protocol_error` — no teardown message type exists. A dropped WebSocket cannot distinguish graceful shutdown from crash or network loss.
**Fix:** Either (a) add an `AddinTeardownMessage` to SPEC-01 §4.2 with defined send conditions in SPEC-02, or (b) remove `explicit_shutdown` from the observable closure reasons and collapse it into `heartbeat_timeout` with an 'unknown cause' note. The current spec acknowledges this gap but leaves it unresolved.
**Sources:** Fran

### [HIGH] `IReadOnlyDhfRepository.ValidateChainAsync` writes integrity_event decorations in deep mode — violates read-only contract (R1-ISSUE-008)
**Problem:** SPEC-06 SDR-06-003 states 'The viewer never writes to any table. All paths go through IReadOnlyDhfRepository.' SPEC-04 §10.2 states 'Deep validation writes integrity_event decorations for detected failures via WriteIntegrityEventAsync.' This means a method on the read-only interface performs database writes in deep mode, breaking the core isolation guarantee and invalidating the DI-container enforcement.
**Fix:** Make `ValidateChainAsync` on `IReadOnlyDhfRepository` strictly side-effect-free — return findings in-memory only. If persistence of integrity findings is required, expose a separate write-capable method on `IDhfRepository` (not `IReadOnlyDhfRepository`) that the orchestrator (not the viewer) calls explicitly after validation.
**Sources:** Fran

### [MEDIUM] Viewer route root conflicts between SPEC-06 (`GET /`) and migration document (`/viewer`) (R1-ISSUE-009)
**Problem:** The migration document §1 deployment matrix states 'Viewer host | Same Railway service, path `/viewer`.' SPEC-06 §4.1 defines `GET /` as the viewer timeline root, and SPEC-06 §3 says the viewer registers endpoints on the orchestrator `WebApplication` with no separate listener or path prefix. These are two different canonical viewer roots.
**Fix:** Choose one canonical viewer root and update all references. SPEC-06's `GET /` is more internally consistent (all endpoint definitions use `/` prefix). Update the migration document §1 to remove `/viewer` or clarify it as a path prefix under which all viewer routes are mounted.
**Sources:** Fran

### [MEDIUM] Regulatory framing 'foundation for future alignment' creates misrepresentation risk for Medtronic pitch (R1-ISSUE-010)
**Problem:** SPEC-00 SDR-TL-006 and SPEC-04 §12 frame the system as 'design audit log foundation for future 21 CFR 820.30 / Part 11 alignment' while simultaneously disclosing that the orchestrator has DROP TRIGGER privilege, electronic signatures are absent, and the system is not Part 11 compliant. The 'foundation for' framing risks being read by a non-technical Medtronic audience as partial compliance rather than zero compliance.
**Fix:** The existing non-compliance disclaimer is strong and should be retained. Consider adding a one-sentence clarification in SDR-TL-006 that 'foundation' means 'data structure that could be extended toward compliance, not a partial implementation of any compliance requirement.' Ensure the verbatim disclaimer appears in any customer-facing artifact before the 'foundation' framing.
**Sources:** Grok

### [MEDIUM] SolidWorks STA threading model is a single point of failure with no fallback if probe fails (R1-ISSUE-011)
**Problem:** SPEC-02 §2.2 captures the SolidWorks UI thread's SynchronizationContext and performs a startup probe. If the probe fails, the add-in logs a fatal error and does not start. No alternative dispatch mechanism is specified. If SolidWorks changes its internal message pump in a future version, the entire add-in becomes non-functional with no recovery path.
**Fix:** The Day-0 validation requirement to document the actual SynchronizationContext type is appropriate. Additionally, specify what the fallback behavior is if the probe fails on a future SW version (e.g., attempt a dedicated STA thread with a manual message pump as a documented degraded mode). This is medium severity because Day-0 validation is explicitly required before implementation begins.
**Sources:** Grok

### Coordinator Decisions
- **R1-ISSUE-001**: accepted_defect — Session lock in-memory only is a clear defect against the never-descopable DHF-014 invariant. SDR-04-005 explicitly preserves sessions across orchestrator restart, so an in-memory lock vanishes on res
- **R1-ISSUE-002**: accepted_defect — The schema defines no bytea column for screenshot storage despite SPEC-04 §10.2 claiming 'stored inline in DHF as screenshot_png bytea.' Deep validation and viewer rendering are both unimplementable w
- **R1-ISSUE-003**: accepted_defect — GET / auto-loading the most recent session and ?tenant= override completely invalidate the URL-as-capability claim. Any internet user can enumerate sessions. Must gate all content behind possession of
- **R1-ISSUE-004**: accepted_defect — Recovery events are never-descopable per SPEC-00 Seam B/C, but have no schema representation. The turn_type CHECK constraint excludes 'recovery', RecoveryEventContext is undefined, and AppendRecoveryE
- **R1-ISSUE-005**: accepted_defect — Genuine contradiction: SPEC-01 §12.4 correctly specifies HTTP 400/401 for pre-upgrade failures; migration doc §2 incorrectly states WebSocket close code 4401 for the same scenario. SPEC-01 is authorit
- **R1-ISSUE-006**: accepted_defect — The glossary defining explicit_shutdown on 'orchestrator detects add-in disconnect' directly contradicts SPEC-01 §12.7 idle-timeout reconnect resuming the DHF session and SPEC-04 SDR-04-005. Transport
- **R1-ISSUE-007**: accepted_defect — explicit_shutdown references a teardown message that does not exist in the IAddinMessage polymorphic family. The gap is acknowledged in the spec but unresolved. Adding AddinTeardownMessage is the corr
- **R1-ISSUE-008**: accepted_defect — IReadOnlyDhfRepository.ValidateChainAsync writing integrity_event decorations in deep mode directly violates SDR-06-003's read-only guarantee and invalidates the DI-container enforcement. The read-onl
- **R1-ISSUE-009**: accepted_defect — Genuine contradiction between migration doc §1 ('path /viewer') and SPEC-06 §4.1 ('GET /'). SPEC-06 is more internally consistent; migration doc must be updated.
- **R1-ISSUE-010**: accepted_defect — The existing disclaimer is strong but the 'foundation for' framing in customer-facing contexts could be misread as partial compliance. A one-sentence clarification is low-cost and reduces risk.
- **R1-ISSUE-011**: preference_only — The spec already requires Day-0 validation of the SynchronizationContext type before any code ships, includes the install-if-null fallback, and specifies a fatal error if the probe fails. This is corr

## Round 2

### [HIGH] Session lock after DHF append failure — operational risk of permanent availability failure requiring manual SQL intervention (R1-ISSUE-001)
**Problem:** R1 fixed the durability gap (lock now persists via locked_at/lock_reason columns). The remaining concern is that any transient DB outage or retry exhaustion permanently locks the session with no automated unlock path, no operator dashboard, and no circuit-breaker. For a medtech production target this is a significant operational risk, though for hackathon scope it is acceptable as documented. The 'never descopable' gate is appropriate for correctness; the missing piece is an unlock/recovery operator API.
**Fix:** Add a documented operator unlock API (even a CLI command or SQL procedure) with audit trail. Clarify that the 'never descopable' gate applies to the lock behavior, not to the absence of an unlock path. For v1/hackathon, document the SQL unlock procedure explicitly.
**Sources:** Grok

### [HIGH] Recovery screenshot PNG bytes defined in RecoveryEventContext but never written to dhf.entries BYTEA columns (R1-ISSUE-002)
**Problem:** Migration 008 adds screenshot_before_png/screenshot_after_png BYTEA columns. RecoveryEventContext carries byte[]? ScreenshotBeforePng/ScreenshotAfterPng. However, AppendRecoveryEventAsync (§7.2) builds a DhfTurnContext with ScreenshotBeforePath = null and ScreenshotAfterPath = null and never passes the PNG byte arrays through to persistence. The R1 fix is incomplete: deep validation and viewer rendering for recovery events still cannot rely on screenshot bytes being present.
**Fix:** Extend DhfTurnContext to carry optional PNG byte payloads (or add a dedicated recovery insert path), and ensure AppendRecoveryEventAsync writes ScreenshotBeforePng/ScreenshotAfterPng into dhf.entries. Add a test asserting the BYTEA columns are populated for recovery events.
**Sources:** Codex, Fran

### [HIGH] Viewer UUID-as-capability access control: knowing one session UUID reveals all tenant sessions via sidebar (R1-ISSUE-003)
**Problem:** Two distinct sub-problems: (1) SDR-TL-012 explicitly acknowledges that knowing one session UUID reveals all sessions for that tenant via the sidebar — this is a security exposure for CAD design data. (2) IReadOnlyDhfRepository.ListSessionsAsync takes tenantId, not a sessionId capability, so the viewer cannot implement the capability-based access model without undocumented tenant-resolution logic. The R1 fix (neutral landing page, removed ?tenant= override) addressed the anonymous enumeration path but not the sidebar exposure or the repository API gap.
**Fix:** Add IReadOnlyDhfRepository.ListSessionsForCapabilityAsync(Guid sessionId) that resolves tenant from the session and returns authorized sessions. For the security exposure, either implement Clerk now (documented as 15-30 min) or explicitly scope the sidebar to show only the single accessed session in v1.
**Sources:** Grok, Codex

### [HIGH] RecoveryEventContext structured fields collapsed to free-text RawTranscript — structured recovery data is unqueryable (R1-ISSUE-004)
**Problem:** RecoveryEventContext defines structured fields (Trigger, Strategy, Success, FailureReason, ModelStateDivergenceDescription) but AppendRecoveryEventAsync collapses them into a free-text description string written to RawTranscript. Viewer logic, validation, reporting, and audit queries cannot reliably filter or consume recovery outcomes from an ad-hoc string. This is a new issue distinct from R1-ISSUE-004 (which was about the absence of any recovery storage — now resolved). The current gap is that storage exists but is unstructured.
**Fix:** Add a recovery_context JSONB column to dhf.entries (or a separate dhf.recovery_events table), include it in hash coverage, and expose it in EntryDetail read DTO. AppendRecoveryEventAsync should write structured fields directly.
**Sources:** Fran

### [CRITICAL] Heartbeat timeout specifies illegal WebSocket close code 1006 — RFC 6455 forbids sending it in a close frame (R1-ISSUE-005)
**Problem:** SPEC-01 §12.7 specifies 'Missed pong → close with 1006 ("abnormal closure") after 2 missed intervals.' RFC 6455 §7.4.1 explicitly reserves 1006 for local reporting of abnormal closure and prohibits it from being sent in a WebSocket Close frame. .NET's ClientWebSocket will throw or substitute a different code, breaking heartbeat and reconnect behavior across implementations.
**Fix:** Either: (a) abort the TCP connection without sending a Close frame (the standard approach for heartbeat failure, which the peer observes as 1006 locally), or (b) send a valid close code such as 1001 (Going Away) or a custom 4xxx code with documented semantics. Update tests accordingly.
**Sources:** Codex, Fran

### [HIGH] Same-epoch reconnect accepts updated capability fields that are immutable in dhf.sessions — contradiction between SPEC-01 and SPEC-04 (R1-ISSUE-006)
**Problem:** SPEC-01 §9 states that same-AddinSessionId reconnects must 'accept the updated capability fields (AddinVersion, SwVersion, SwBuild, ScreenshotRootPath).' SPEC-04 SDR-04-010 states all dhf.sessions columns except ended_at, locked_at, and lock_reason are immutable once inserted. The guard_session_update trigger will reject any UPDATE to addin_version, sw_version, etc. There is no defined mutable store for reconnect capability updates.
**Fix:** Choose one contract: either (a) reject same-epoch reconnects when capability fields differ (simplest), or (b) add a separate mutable dhf.session_reconnects table for reconnect metadata and reference it consistently in both specs.
**Sources:** Fran

### [HIGH] API secret transmitted in Sec-WebSocket-Protocol header — logged by proxies, edge platforms, and APM tooling (R1-ISSUE-007)
**Problem:** SPEC-01 §12.3 places the full API credential (keyId + secret) in the Sec-WebSocket-Protocol header. This header is commonly logged by Railway edge, reverse proxies, APM tools, and browser devtools. Suppressing the echoed response header does not prevent request-side logging. A leaked header exposes a long-lived tenant credential.
**Fix:** Move the credential to an Authorization header on the HTTP upgrade request (standard practice), or issue a short-lived one-time token over HTTPS and use that in the upgrade. Define mandatory request-header redaction rules for Railway logging configuration.
**Sources:** Fran

### [HIGH] Runtime orchestrator role has DROP TRIGGER / DDL privileges — undermines DHF immutability guarantee (R1-ISSUE-008)
**Problem:** SPEC-04 §3.2 honestly discloses 'The orchestrator has DROP TRIGGER privilege.' This means the same runtime principal that appends DHF records can remove the trigger-based immutability protections. For a system positioned as a design audit log foundation for 21 CFR 820.30 alignment, this makes the append-only guarantee dependent on application goodwill rather than database policy.
**Fix:** Run the orchestrator under a DML-only Postgres role (INSERT, SELECT, UPDATE on permitted columns only) with no DDL, DROP TRIGGER, or ALTER TABLE privileges. Reserve schema migrations for a separate admin role. Update the enforcement gap disclosure to reflect the corrected trust boundary.
**Sources:** Fran

### [HIGH] Unknown message type handling is contradictory: SPEC-01 requires MalformedEnvelope, SPEC-02 requires UnknownMessageType (R1-ISSUE-009)
**Problem:** SPEC-01 §3 states an 'unrecognized' polymorphic discriminator MUST produce MalformedEnvelope. SPEC-02 §6.5 states unknown IClientMessage subtypes produce AddinProtocolErrorMessage(ErrorCode = UnknownMessageType). These are two different wire-level outcomes for the same condition. Interop tests and retry logic cannot be implemented deterministically.
**Fix:** Define one canonical mapping: malformed/unparseable envelope → MalformedEnvelope; well-formed envelope with unsupported 'type' discriminator → UnknownMessageType. Update SPEC-01 §3 and SPEC-02 §6.5 to use this split consistently, and align required tests in both specs.
**Sources:** Fran

### [HIGH] Viewer SPEC-06 declared to depend only on SPEC-04 but imports SPEC-01 types — dependency rule is contradictory (R1-ISSUE-010)
**Problem:** SPEC-00 §1 Module Map declares SPEC-06 depends on 'SPEC-04 (read-only)' only. SPEC-01 §18 states 'SPEC-06 imports StateSnapshot, SelectionRef, FeatureNode (for display), and nothing else.' StateSnapshot, SelectionRef, and FeatureNode are defined in SPEC-01 (BSVoiceCAD.Protocol). Architecture tests and project references cannot satisfy both statements simultaneously.
**Fix:** Choose one: (a) expose viewer-needed display types entirely via SPEC-04 read model DTOs (re-projecting StateSnapshot/FeatureNode into viewer-specific records), or (b) explicitly allow SPEC-06 to depend on SPEC-01 and update the Module Map and NetArchTest rules accordingly.
**Sources:** Codex

### [HIGH] CallId format (UUIDv7) validated in SPEC-02 but never defined in SPEC-01 — wire contract gap (R1-ISSUE-011)
**Problem:** SPEC-02 §6.5 validates CallId as UUIDv7 format and SPEC-02 §10 requires CallIdFormatValidationTests. SPEC-01 §5 defines CallId only as 'Lifecycle identity (see SPEC-03)' with no format specification. Producer (orchestrator, SPEC-03) and consumer (add-in, SPEC-02) can disagree on format while both remaining spec-compliant.
**Fix:** Define the canonical CallId format (UUIDv7 string) in SPEC-01 §5 alongside MessageId. Add CallIdFormatTests to SPEC-01's required test list. Remove the format definition from SPEC-02 (it should only reference SPEC-01).
**Sources:** Codex

### [HIGH] API key lockout persistence required by v1 contract but lockout_event schema is deferred to post-hackathon (R2-ISSUE-001)
**Problem:** SPEC-01 §12.6 specifies '10 failed attempts per hour → temporary key lockout (15 min), logged as an api_keys.lockout_event row (schema deferred to post-hackathon).' The v1 auth contract requires writing lockout events but no table or migration is defined. This cannot be implemented or migration-tested as written.
**Fix:** Either define the lockout_event schema and migration now (it is a simple table), or explicitly remove the persistence requirement from v1 and implement lockout as in-memory only with a documented limitation.
**Sources:** Codex

### [HIGH] Screenshot size reduction has no defined terminal behavior after 4 iterations — oversize message has no valid wire outcome (R2-ISSUE-002)
**Problem:** SPEC-02 §7.2 limits dimension reduction to 4 iterations and says 'Abandon after iteration 4' but does not define what message is sent. Additionally, neither spec requires checking the size of the fully serialized ToolResultMessage (screenshot + StateAfter.FeatureTree) against the 4 MiB envelope limit after reduction.
**Fix:** Define the explicit terminal behavior after max reductions (e.g., return ToolError with a specific code, or send a 1x1 placeholder). Require a final full-message-size check that can trigger feature-tree truncation or a deterministic ToolError before send.
**Sources:** Fran

### [HIGH] Planned disconnect 60-second escalation timeout has no defined post-timeout action (R2-ISSUE-003)
**Problem:** SPEC-02 §9.1 states the add-in 'Waits for any currently-executing tool call to complete on the UI thread (60-second escalation timeout)' but never specifies what happens when the COM call is still running at 60 seconds. Since executing COM cannot be preempted (SPEC-00 Seam B, SPEC-02 §2.4), some implementations will hang indefinitely, others will force-close, producing divergent DHF session semantics.
**Fix:** Specify the exact post-timeout action: e.g., log the stuck call, skip AddinTeardownMessage send (session closes via heartbeat_timeout), and force-close the WebSocket. Document the resulting DHF session closure reason.
**Sources:** Fran

### [MEDIUM] ACC-20 performance gate is not reproducible — no defined hardware, browser, cache state, or measurement point (R2-ISSUE-004)
**Problem:** ACC-20 requires 'Viewer timeline renders a 20-entry session in under 1 second on the demo laptop' but does not define the benchmark hardware profile, browser/runtime, warm vs cold cache, network conditions, or whether the 1s target is server response time, network transfer, or UI render time. CI and manual acceptance cannot produce a deterministic pass/fail.
**Fix:** Define: specific machine (GE75 or named equivalent), browser (Chrome stable), cache state (warm), measurement point (Time to Interactive from navigation start), and network (localhost or Railway prod). Add a Playwright/Lighthouse fixture to BSVoiceCAD.Tests.Integration.
**Sources:** Codex

### [MEDIUM] Cross-workstation conflict detection relies on client-supplied untrusted WorkstationId — ACC-19 is not reliably enforceable (R2-ISSUE-005)
**Problem:** SPEC-01 §5 documents WorkstationId as Environment.MachineName and explicitly states 'is NOT a security trust boundary.' However, SPEC-04 SDR-04-008 and ACC-19 use WorkstationId as the basis for the hard-error cross-workstation conflict check. A spoofed or non-unique WorkstationId within a tenant can cause false positives (legitimate reconnects rejected) or false negatives (conflicts silently merged).
**Fix:** Use AddinSessionId (which incorporates MachineGuid + process ID + timestamp + nonce per SPEC-02 §8.1) as the conflict discriminator rather than WorkstationId alone. WorkstationId can remain for display/audit purposes. Update ACC-19 test definition accordingly.
**Sources:** Codex

### Coordinator Decisions
- **R1-ISSUE-001**: preference_only — The R1 fix correctly implemented persistent lock via locked_at/lock_reason columns. The R2 concern is about operational tooling (unlock API, dashboard) — not a spec defect. The manual SQL intervention
- **R1-ISSUE-002**: accepted_defect — AppendRecoveryEventAsync builds DhfTurnContext with ScreenshotBeforePng/ScreenshotAfterPng silently dropped — the R1 fix is incomplete. DhfTurnContext needs byte array fields and AppendTurnCore must w
- **R1-ISSUE-003**: preference_only — Sidebar exposure is explicitly documented and accepted in SDR-TL-012. The viewer can resolve sessionId → tenantId via GetSessionAsync then call ListSessionsAsync — the two-step pattern is implementabl
- **R1-ISSUE-004**: accepted_defect — RecoveryEventContext defines structured fields (Trigger, Strategy, Success, etc.) but AppendRecoveryEventAsync collapses them to a free-text string in RawTranscript. For an audit log foundation, struc
- **R1-ISSUE-005**: accepted_defect — RFC 6455 §7.4.1 explicitly forbids sending close code 1006 in a Close frame. .NET's ClientWebSocket will throw. The orchestrator must abort the TCP connection without sending a Close frame; the peer o
- **R1-ISSUE-006**: accepted_defect — SPEC-01 §9 says 'accept the updated capability fields' on same-epoch reconnect. SPEC-04 SDR-04-010 says those columns are immutable. The guard_session_update trigger will reject the UPDATE. Resolution
- **R1-ISSUE-007**: unresolved_question — Real security concern — Sec-WebSocket-Protocol request headers are commonly logged. However, changing the auth mechanism (to Authorization header) requires updating SPEC-01 §12.3, SPEC-02 §6, and test
- **R1-ISSUE-008**: preference_only — SPEC-04 §3.2 already honestly discloses the DROP TRIGGER privilege gap. The spec does not claim database-enforced immutability against the application principal. Role separation is a good operational 
- **R1-ISSUE-009**: accepted_defect — SPEC-01 §3 routes all unrecognized discriminators to MalformedEnvelope; SPEC-02 §6.5 routes unknown subtypes to UnknownMessageType. These are different error codes for the same condition. Must define 
- **R1-ISSUE-010**: accepted_defect — SPEC-00 §1 says SPEC-06 depends on SPEC-04 only. SPEC-01 §18 says SPEC-06 imports StateSnapshot, SelectionRef, FeatureNode — which are SPEC-01 types. The Module Map must acknowledge SPEC-06's dependen
- **R1-ISSUE-011**: accepted_defect — CallId is a wire field defined in SPEC-01 §5 with no format constraint. SPEC-02 §6.5 validates it as UUIDv7. The format must be defined authoritatively in SPEC-01 so producer (SPEC-03) and consumer (S
- **R2-ISSUE-001**: accepted_defect — SPEC-01 §12.6 requires writing to api_keys.lockout_event table but no table or migration is defined. The behavior requires persistence that doesn't exist. Fix: make v1 lockout in-memory with documente
- **R2-ISSUE-002**: accepted_defect — SPEC-02 §7.2 says 'Abandon after iteration 4' with no defined wire outcome. The add-in must produce a ToolError with ScreenshotCaptureFailed code so the lifecycle layer can handle it deterministically
- **R2-ISSUE-003**: accepted_defect — SPEC-02 §9.1 specifies a 60-second escalation timeout but no post-timeout action. Since COM calls are non-preemptible, the add-in must skip rollback, send AddinTeardownMessage best-effort, and force-c
- **R2-ISSUE-004**: preference_only — ACC-20 is a hackathon demo acceptance gate — 'it loads fast on the demo laptop' is the actual test. Formalizing with Playwright/Lighthouse fixtures is over-engineering for hackathon scope. Can be tigh
- **R2-ISSUE-005**: preference_only — WorkstationId is explicitly documented as 'NOT a security trust boundary.' Cross-workstation conflict detection is a secondary defense against statistically impossible AddinSessionId collisions (SHA-2
