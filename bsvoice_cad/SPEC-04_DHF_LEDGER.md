---
title: "BSVoice-CAD SPEC-04 — DHF Ledger"
subtitle: "Schema, hash model, append logic, AddinSessionId epoch, DHF session lifecycle, read-only repository projection, tenant scoping, durable session lock, screenshot BYTEA storage, recovery event persistence"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-04 — DHF Ledger

**Version:** 1.5.2
**Status:** DRAFT — cross-pass R3 findings applied (full standalone) + peer review v1.5.1 patches + R4 editorial fixes
**Depends on:** SPEC-00 v1.5.0 (module boundaries, SDR-TL-013 recovery persistence, SDR-TL-014 teardown message), SPEC-01 v1.4.0 (wire types including `ToolCallLifecycleState` enum, `AddinTeardownMessage`, UUIDv7 `CallId`), `DEPLOYMENT_V1_V2_MIGRATION.md` v1.1.0 (tenant and deployment boundary, handshake failure transport)
**Consumed by:** SPEC-03 (writes entries + recovery events), SPEC-06 (reads entries via `IReadOnlyDhfRepository`), SPEC-07 (extends decoration type enum via migration 007 — `vocabulary_resolution`)

**Changelog v1.5.1 → v1.5.2 (R4 cross-pass editorial fixes):**
- **C2 (CRITICAL, R4 cross-pass regression) — §4.2a excluded-columns table no longer contradicts §4.2 DhfHashColumns.** The v1.5.1 §4.2a table erroneously listed `entry_id`, `session_id`, and `sequence_number` as excluded from the hash input. Those three columns ARE in `DhfHashColumns.HashedColumns` (§4.2) — they are hashed directly as identity/ordering anchors. The §4.2a table is now restricted to JSONB/BYTEA columns that are hashed **indirectly** via companion `*_hash` columns. Preamble rewritten to clarify the "indirectly hashed" framing and to state plainly that identity columns are hashed directly.
- **H3 — cross-workstation locked-session bypass closed.** The v1.5.0/v1.5.1 `OpenOrResumeSessionCore` two-phase query could match neither branch when a locked session with the same tenant+AddinSessionId existed on a DIFFERENT workstation: phase 1 (resumable lookup) filtered out `locked_at IS NOT NULL`, and phase 2 (locked-session check) restricted to `workstation_id = @WorkstationId`. A tenant-wide cross-workstation locked-session pre-check is added; hits throw `CrossWorkstationSessionLockedException` (new in §9.3). `IDhfRepository` gains `FindLockedSessionAsync(tenantId, addinSessionId, ct)`.
- **H5 — locked-session recovery runbook reconciled to close-and-new (authoritative).** The `guard_session_update` trigger rejects any transition of `locked_at` from non-NULL to NULL; there is no programmatic unlock in v1. Operator recovery procedure is therefore close-and-new only: CLOSE the locked session (`UPDATE dhf.sessions SET ended_at = NOW() WHERE session_id = X` — permitted even when locked), then the next `AddinReadyMessage` produces a fresh `AddinSessionId`. SPEC-00 §6 Glossary and Seam C updated in v1.5.1 to remove any residual "unlock" language. Programmatic unlock is v1.5 backlog.
- **H6 — `EntryListItem` extended with `UndoOutcome` / `RecordHash` / `PreviousHash`.** SPEC-06 §4.3 response contract referenced these three fields; they were absent from the v1.5.1 read-only projection. All three are already columns on `dhf.entries`; the projection simply didn't SELECT them. Now added. Raw binary hash columns are exposed as hex-encoded strings to match the viewer's JSON rendering expectation. `PreviousHash` is nullable for the first entry in a chain.

**Changelog v1.5.0 → v1.5.1 (Beth-on-Beth peer review patches):**
- **M1 — Migration 007 ownership inverted.** Prior release stated "owned by SPEC-07 but coordinated here." Correct ownership is: **SPEC-04 §3 owns the migration** (it mutates `dhf.entry_decorations`, a SPEC-04 table), SPEC-07 §8.1 reproduces the SQL for cross-reference, and this spec is authoritative if the two texts diverge. Migration 007 header comment and the existing v1.5.0 changelog line both corrected.
- **M2 — `guard_session_update` trigger gap closed.** Prior trigger permitted `UPDATE dhf.sessions SET lock_reason = 'text'` while leaving `locked_at` NULL — the session would appear unlocked to resumption lookups but carry a spurious lock_reason. New branch rejects `lock_reason` NULL → non-NULL unless `locked_at` is set in the same UPDATE. `SessionLockTriggerEnforcementTests` extended.
- **M3 — Hash-schema v1.4.0 → v1.5.0 compatibility disclosure.** §4.2 hash input set changed between versions (removed `screenshot_before_path` / `screenshot_after_path`; added `recovery_context_hash`). Under the hackathon scope (no production v1.4.0 data exists), this is out-of-scope. Post-hackathon remediation paths documented in §4.2.
- **m1 — `"NO_RECOVERY_CONTEXT"` sentinel canonicalized as a named constant.** Added `HashCanonicalizer.NoRecoveryContextSentinel`. All call sites use the constant; `AppendTurnCore` delegates to `ComputeRecoveryContextHash(null)` rather than hardcoding the string literal.
- **m2 — `IsRecoveryEvent` derived field removed from `DhfEntrySummary` (write-side projection).** Consumers derive from `TurnType == "recovery"`. Avoids a parallel boolean that could drift from the `TurnType` enum.
- **m3 — §4.2a enumerates hash-excluded columns.** Previously scattered across §3 SQL comments; now listed in one subsection with the hash-anchor column that replaces each excluded column (or identity/FK rationale).

**Changelog v1.4.0 → v1.5.0:**
- **§3 migration 007 (NEW, owned by SPEC-04; coordinated with SPEC-07):** Extends `dhf.entry_decorations.decoration_type` CHECK constraint to include `'vocabulary_resolution'`. Migration lives in this spec's migration sequence between 006 and 008. `DhfEnumMappings.ToStorage(DecorationType)` gains the `DecorationType.VocabularyResolution => "vocabulary_resolution"` mapping.
- **§3 migration 008 (NEW, R3 C1 + C2):** Adds durable session-lock columns (`locked_at TIMESTAMPTZ NULL`, `lock_reason TEXT NULL`) to `dhf.sessions`. Adds screenshot BYTEA columns (`screenshot_before_png BYTEA NULL`, `screenshot_after_png BYTEA NULL`) to `dhf.entries`. Updates `guard_session_update` to permit `locked_at`/`lock_reason` NULL→non-NULL transitions alongside the existing `ended_at` transition. Closes the C1 critical (in-memory-only session lock vanishes on restart) and the C2 critical (screenshot bytes have no storage column).
- **§3 migration 009 (NEW, R3 C4):** Adds `'recovery'` to the `dhf.entries.turn_type` CHECK constraint. Adds `recovery_context JSONB NULL` column. Closes the C4 critical (`AppendRecoveryEventAsync` was unimplementable — no storage). Recovery entries participate in the hash chain identically to turn entries, differentiated by `turn_type`.
- **§4.2 `DhfHashColumns` extended** with `recovery_context_hash` (v1.5.0). The JSONB column itself is not hashed directly; a canonical hash of the JSONB content IS included in the entry's `record_hash` input so tampering with `recovery_context` is detectable.
- **§4.5 `DhfEnumMappings`** gains `TurnType.Recovery => "recovery"` mapping and `DecorationType.VocabularyResolution => "vocabulary_resolution"` mapping (migration 007 coordination).
- **§5 `DhfTurnContext`** gains optional `ScreenshotBeforePng`, `ScreenshotAfterPng` byte-array fields (R3 H7). These flow into the new BYTEA columns via `AppendTurnCore`. The pre-existing `ScreenshotBefore/AfterSha256` fields remain authoritative for integrity; the PNG bytes are the actual audit-trail artifact.
- **§5 `RecoveryEventContext`** DTO defined (NEW). Fields: `Trigger`, `Strategy`, `Success`, `FailureReason`, `ModelStateDivergenceDescription`, optional `ScreenshotBeforePng`, optional `ScreenshotAfterPng`, optional `ScreenshotBeforeSha256`, optional `ScreenshotAfterSha256`. Consumed by `AppendRecoveryEventAsync`.
- **§6.4 NEW — durable session lock mechanics (R3 C1):** `IDhfRepository.LockSessionAsync(Guid sessionId, string lockReason, CancellationToken ct)` persists `locked_at = NOW()` and `lock_reason` via a trigger-permitted UPDATE. `OpenOrResumeSessionCore` now checks `locked_at IS NULL` before resuming — locked sessions are rejected with `SessionLockedException` (new in §9.3). `AppendTurnCore` re-verifies lock state inside the append transaction to prevent TOCTOU races between concurrent lock and append attempts.
- **§7.2 NEW — `AppendRecoveryEventAsync` implementation (R3 C4 + H7 + H8):** Writes a DHF entry with `turn_type = 'recovery'`. Populates `recovery_context` JSONB from the structured `RecoveryEventContext` (R3 H8 — not collapsed to free-text `RawTranscript`). Writes screenshot PNG bytes to `screenshot_before_png` / `screenshot_after_png` BYTEA columns when provided (R3 H7). Hash chain continues in normal `sequence_number` order; recovery entries' `previous_hash` is the prior entry's `record_hash` regardless of that entry's `turn_type`. Zero tool-call child rows (recovery events have no tool calls), so `tool_calls_hash = "NO_TOOL_CALLS"`.
- **§9.2 `IReadOnlyDhfRepository.ValidateChainAsync` contract tightened (R3 H4):** Strictly side-effect-free on the read-only interface. The implementation MUST NOT write `integrity_event` decorations or any other rows under any mode (shallow or deep). Findings are returned in-memory only. If operators want integrity findings persisted, they invoke `IDhfRepository.RecordIntegrityEventAsync` (new) explicitly from orchestrator code; this method is NOT exposed on the read-only interface and is NOT reachable from the viewer's DI scope.
- **§9.3 NEW — `SessionLockedException`:** Thrown by `OpenOrResumeSessionCore` and `AppendTurnCore` when a session is locked. Carries `SessionId` and `LockReason`.
- **§10.2 Deep validation contract rewritten (R3 H4):** Deep validation on `IReadOnlyDhfRepository.ValidateChainAsync` returns findings in-memory only, matching shallow. Separate `IDhfRepository.RecordIntegrityEventAsync` exists for the orchestrator to optionally persist findings after calling deep validation; the two operations are decoupled. This eliminates the SDR-06-003 read-only-contract violation.
- **§11 DHF-014 rewritten:** The session lock is now durable (persisted in `dhf.sessions.locked_at`), not in-memory-only. Survives orchestrator restart. Lock condition: DHF append failure after all retries. Unlock: operator SQL only (no programmatic unlock path in v1).
- **§11 DHF-016 (NEW):** Recovery event persistence model. Per SDR-TL-013, recovery events are stored in `dhf.entries` with `turn_type = 'recovery'` and structured `recovery_context` JSONB. Hash chain includes recovery entries identically to turn entries.
- **§13.1 ACC table extended:** ACC-25 (durable session lock survives restart), ACC-26 (recovery event structured context queryable).
- **Depends-on updated** to reflect SPEC-00 v1.5.0, SPEC-01 v1.4.0, SPEC-07 dependency, migration doc v1.1.0.

**Changelog v1.3.0 → v1.4.0 (preserved from prior release):**
- SDR-04-008 rewritten: `workstation_id` is a client-supplied tenant hint, NOT a trust boundary. Trust flows from the API-key-authenticated WebSocket (SPEC-01 §12), which scopes every operation to a tenant. Cross-workstation hard-error logic retained but reframed as "catches VM cloning / ID collision within a tenant's fleet."
- New migration 006 (§3.1): adds `tenant_id` column to `dhf.sessions`. Every repository query filters by `tenant_id`. Partial unique index on `(tenant_id, addin_session_id) WHERE ended_at IS NULL`. v2 migration to per-tenant schemas documented in `DEPLOYMENT_V1_V2_MIGRATION.md` §5.
- §6.1 `OpenOrResumeSessionCore`: cross-workstation branch now throws `CrossWorkstationSessionConflictException` (§6.3) instead of silently closing the other session.
- New §6.3: `CrossWorkstationSessionConflictException` with structured metadata and simplified SQL-only remediation message.
- New §9.2: `IReadOnlyDhfRepository` read-only projection. Supporting types: `SessionSummary`, `EntryListItem`, `EntryDetail`, `ChainValidationMode`, `ChainValidationResult`, `ChainValidationFinding`.
- New §9.3: `RepositoryUnavailableException` and `SessionNotFoundException`.
- New SDR-04-011: read-only repository projection is the viewer's sole database surface.
- New DHF-014 in §11: DHF append failure locks the session.
- New ACC-19 in §13.1: cross-workstation AddinSessionId conflict.
- §12 gap-analysis table extended with `tenant_id` row.

## 0. Scope

This spec defines everything about how voice-and-text turns — and recovery events — become durable audit records. Schema, hash chain, append semantics, session lifecycle, epoch handling, session metadata protection, durable session lock on append failure, decoration extraction, integrity validation, canonical enum-to-storage mappings, read-only viewer projection, tenant scoping.

**In scope:** Postgres 16 schema including tenant_id / durable lock / screenshot bytes / recovery context, hash canonicalization rules (including timestamp normalization and enum-to-storage mapping), per-turn append logic, per-recovery-event append logic, AddinSessionId epoch and DHF session split/resume, session metadata immutability, durable session-lock lifecycle, cross-workstation conflict handling, decoration rows (including insert-with-supersede, integrity-event deduplication, SPEC-07 `vocabulary_resolution` decoration type), immutability enforcement, integrity validation split across read-only and write-capable interfaces, read API contracts (`IDhfRepository` full; `IReadOnlyDhfRepository` strictly side-effect-free), tenant-scoped repository implementations.

**Out of scope:** tool-call state machine (SPEC-03), agent confirmation flow (SPEC-05), viewer UI (SPEC-06), extraction LLM call shape (summarized here; full prompt in SPEC-05), API key validation (SPEC-01 §12), vocabulary bouncer / synthesis / repository (SPEC-07; SPEC-04 only stores the resulting decoration payloads), per-tenant schema provisioning (v2; see migration doc §5).

## 1. Project Shape

```
BSVoiceCAD.Orchestrator.Dhf/
├── DhfLedger.cs                             # AppendTurnAsync + AppendRecoveryEventAsync; sole writer of dhf.entries + dhf.tool_calls
├── DhfSessionManager.cs                     # Open/resume/close/lock dhf.sessions rows
├── DhfDecorationWriter.cs                   # Async writer for dhf.entry_decorations
├── DhfRepository.cs                         # Full read+write queries (implements IDhfRepository)
├── DhfReadOnlyRepository.cs                 # Read-only projection (implements IReadOnlyDhfRepository) — side-effect-free
├── DhfTurnContext.cs                        # Input DTO consumed by AppendTurnAsync
├── RecoveryEventContext.cs                  # Input DTO consumed by AppendRecoveryEventAsync (NEW v1.5.0)
├── DhfEnumMappings.cs                       # Canonical enum-to-storage string mappings
├── HashChainValidator.cs                    # Entry-chain and tool-calls-hash verification (pure; no writes)
├── CrossWorkstationSessionConflictException.cs  # Hard-error type (§6.3)
├── RepositoryUnavailableException.cs        # Database unreachable (§9.3)
├── SessionNotFoundException.cs              # Session does not exist (§9.3)
├── SessionLockedException.cs                # Session is locked (§9.3, NEW v1.5.0)
├── DhfExtractor.cs                          # [STRETCH] Sonnet 4.6 decoration extraction
├── DhfHashColumns.cs                        # Canonical entry-level hash coverage (in Protocol)
├── ToolCallHashColumns.cs                   # Canonical child-row hash coverage (in Protocol)
└── Exporters/
    ├── PdfExporter.cs                       # [STRETCH] QuestPDF-based PDF export
    └── GreenlightGuruExporter.cs            # [ROADMAP] stub, post-hackathon

BSVoiceCAD.Orchestrator.Database/
└── Migrations/
    ├── 001_initial_schema.sql
    ├── 002_indexes.sql
    ├── 003_entries_and_tool_calls.sql
    ├── 004_entry_decorations.sql
    ├── 005_immutability_triggers.sql
    ├── 006_tenant_id.sql
    ├── 007_vocabulary_resolution_decoration.sql   # v1.5.0: SPEC-07 decoration type extension
    ├── 008_durable_lock_and_screenshot_bytes.sql  # v1.5.0: R3 C1 + C2
    └── 009_recovery_events.sql                    # v1.5.0: R3 C4
```

## 2. Design Decisions (Local SDRs)

**SDR-04-001: Per-turn entry + child rows.** One row in `dhf.entries` per agent turn. Zero-or-more child rows in `dhf.tool_calls` per entry. Recovery events (v1.5.0) are also stored in `dhf.entries` with `turn_type = 'recovery'` and zero child rows (SDR-04-012). Locked.

**SDR-04-002: Immutable entries and tool calls; insert-with-supersede decorations.** `dhf.entries` and `dhf.tool_calls` are append-only, enforced at the database level by triggers (see §3). `dhf.entry_decorations` uses an insert-with-supersede model: new decoration rows may reference `supersedes_decoration_id` to logically replace a prior decoration, but the prior row is never mutated or deleted by application code. The DB does NOT enforce decoration immutability with triggers because post-hackathon operational cleanup (orphan removal, GDPR erasure) may require administrative DELETE. Application code MUST NOT issue UPDATE or DELETE against `dhf.entry_decorations` during normal operation; this invariant is enforced by architecture tests.

**SDR-04-003: Two-level hash coverage.** The entry's `record_hash` covers both the entry's own frozen columns AND a canonical `tool_calls_hash` that in turn covers all child rows for that entry. A tamper attempt on a child row changes the `tool_calls_hash`, which changes the `record_hash`, which breaks the chain. For recovery entries (v1.5.0), the `recovery_context` JSONB is folded into the hash computation via a canonical hash (§4.2) so tampering with recovery context is detectable.

**SDR-04-004: DHF session split on AddinSessionId change.** Every new `AddinSessionId` opens a new `dhf.sessions` row. Prior session's `ended_at` is set at the split moment. No entry or tool call spans two sessions. Locked.

**SDR-04-005: Same-AddinSessionId reconnect resumes the prior session.** If the orchestrator reconnects to an add-in whose `AddinSessionId` matches an already-open `dhf.sessions` row on the **same workstation within the same tenant AND not locked**, it resumes that session (appends to its chain) rather than opening a new row. The AddinSessionId (generated by the add-in on load) is the epoch authority, not the orchestrator's process lifecycle. Consequently, the orchestrator does NOT close the DHF session on graceful shutdown — the add-in may still be running and will send the same `AddinSessionId` on reconnect. Sessions are closed by: (a) add-in sending `AddinTeardownMessage` (SPEC-01 v1.4.0 §5), which produces `ended_at` with reason `explicit_shutdown`; (b) session split when a new `AddinSessionId` arrives; (c) heartbeat timeout per SPEC-01 §12.7; (d) workstation-level cleanup in `OpenOrResumeSessionCore` of stale open sessions for the same tenant+workstation. Locked sessions (v1.5.0, §6.4) are NEVER resumed; `OpenOrResumeSessionCore` throws `SessionLockedException`. **Cross-workstation resume is NOT permitted** — see SDR-04-008.

**SDR-04-006: Advisory lock per DHF session during append.** Concurrency safety via Postgres `pg_advisory_xact_lock`. Lock scope narrowed to the INSERT transaction; held during tip-read → hash-compute → entry+tool_calls INSERT, released at transaction commit. No locks held during Claude calls or hash computation outside the transaction. `AppendRecoveryEventAsync` (v1.5.0) uses the same advisory-lock key as `AppendTurnAsync`, so recovery events serialize with turn appends on the same session.

**SDR-04-007: No SQLite fallback.** Railway-managed Postgres 16 is default for v1 deployment. EmbeddedPostgres (`MysticMind.PostgresEmbed`) is a local-dev convenience only — not a deployable fallback per SPEC-00 SDR-TL-002. SQLite requires schema variants, lacks advisory locks, lacks JSONB, and cannot provide append-only trigger enforcement the same way. Locked.

**SDR-04-008 (v1.4.0): `workstation_id` is a client-supplied tenant hint, NOT a trust boundary. Cross-workstation collision within a tenant is a hard error.** The trust boundary is the authenticated WebSocket connection (SPEC-01 §12) — every operation received on a WebSocket is scoped to the `tenant_id` associated with the validated API key. Within that tenant scope, `workstation_id` is a client-supplied string (the add-in reports `Environment.MachineName` in `AddinReadyMessage.WorkstationId`) used for:

- DHF session metadata completeness (operator can see which workstation produced which session)
- Cross-workstation conflict detection WITHIN A TENANT'S FLEET (catches VM snapshot cloning, machine-name collision, administrative misconfiguration)
- Advisory-lock scoping for concurrent session open/close

`workstation_id` is NOT a cross-tenant isolation mechanism (`tenant_id` does that), not a cryptographic identity, and not something the orchestrator trusts for security decisions.

**Behavior:** `OpenOrResumeSessionCore` MUST throw `CrossWorkstationSessionConflictException` (§6.3) when a same-`AddinSessionId` session is open on a different `workstation_id` WITHIN THE SAME TENANT. It MUST NOT close the other session, reassign it, or open a new session. The exception carries structured metadata for operator investigation.

**SDR-04-009: Canonical enum-to-storage mappings via explicit switch expressions.** All enums persisted to Postgres (`InputModality`, `TurnType` including the new `Recovery` value, `UndoGroupOutcome`, `ToolCallLifecycleState`, `DecorationType` including the new `VocabularyResolution` value) MUST be converted through `DhfEnumMappings` — never via `.ToString()` or `.ToLowerInvariant()`. Architecture tests verify every enum value round-trips correctly.

**SDR-04-010: Session metadata immutability via DB trigger.** All columns in `dhf.sessions` except `ended_at`, `locked_at`, and `lock_reason` are immutable once inserted. The `guard_session_update` trigger (updated by migration 008) permits only `ended_at`, `locked_at`, and `lock_reason` to transition from NULL to a non-NULL value; all other UPDATE attempts are rejected. DELETE on `dhf.sessions` is also rejected by trigger. Post-hackathon: compute a `session_metadata_hash` at INSERT time for cryptographic proof of original metadata.

**SDR-04-011 (v1.4.0): Read-only repository projection is the viewer's sole database surface.** The viewer (SPEC-06) consumes only `IReadOnlyDhfRepository` (§9.2). The full `IDhfRepository` — which exposes write methods for session open/close/lock and for appending turn and recovery events — is NEVER registered in the viewer's isolated DI container. `IReadOnlyDhfRepository.ValidateChainAsync` is strictly side-effect-free as of v1.5.0 (R3 H4); any persistence of integrity findings requires the separate `IDhfRepository.RecordIntegrityEventAsync` method, which is not reachable from the viewer.

**SDR-04-012 (v1.5.0): Recovery event persistence model — same table, `turn_type = 'recovery'`, structured `recovery_context` JSONB.** Recovery events are stored in `dhf.entries` with `turn_type = 'recovery'` rather than in a separate `dhf.recovery_events` table. Rationale (per SPEC-00 SDR-TL-013): one chain, one sequence counter, one tip lookup, one hash-chain rule. Alternative (separate table) doubles hash-chain implementation complexity. Recovery entries have zero `dhf.tool_calls` child rows (`tool_calls_hash = "NO_TOOL_CALLS"`). Recovery-specific fields live in `recovery_context` JSONB; hash coverage includes a canonical hash of the JSONB content (§4.2) so tampering is detectable. Screenshot bytes for recovery events use the same `screenshot_before_png` / `screenshot_after_png` BYTEA columns as turn entries.

## 3. Schema

### 3.1 Migrations

Executed in strict numerical order by DbUp at orchestrator startup. DbUp journals to `public.dbup_journal`, avoiding a bootstrap dependency on the `dhf` schema that has not yet been created. The `dhf` schema is created by migration 001.

```sql
-- 001_initial_schema.sql

CREATE SCHEMA IF NOT EXISTS dhf;

CREATE TABLE dhf.sessions (
    session_id           UUID PRIMARY KEY,
    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at             TIMESTAMPTZ NULL,
    user_identity        TEXT NOT NULL,
    user_display_name    TEXT NULL,
    workstation_id       TEXT NOT NULL,
    sw_version           TEXT NOT NULL,
    addin_version        TEXT NOT NULL,
    orchestrator_version TEXT NOT NULL,
    protocol_version     TEXT NOT NULL,
    addin_session_id     TEXT NOT NULL
);
```

```sql
-- 002_indexes.sql

CREATE INDEX idx_sessions_started_at ON dhf.sessions (started_at DESC);

-- Partial unique index allows INSERT of a new session with the same
-- addin_session_id after the prior one is closed. Note: superseded by migration 006
-- to include tenant_id in the uniqueness key.
CREATE UNIQUE INDEX idx_sessions_open_addin_session
    ON dhf.sessions (addin_session_id)
    WHERE ended_at IS NULL;
```

```sql
-- 003_entries_and_tool_calls.sql

CREATE TABLE dhf.entries (
    entry_id                  UUID PRIMARY KEY,
    session_id                UUID NOT NULL REFERENCES dhf.sessions(session_id),
    sequence_number           BIGINT NOT NULL,
    timestamp                 TIMESTAMPTZ NOT NULL,

    -- Input
    input_modality            TEXT NOT NULL CHECK (input_modality IN ('voice', 'text')),
    raw_transcript            TEXT NOT NULL,
    transcript_confidence     REAL NULL,

    -- Turn classification (extended by migration 009 to include 'recovery')
    turn_type                 TEXT NOT NULL
        CHECK (turn_type IN ('execution', 'clarification', 'confirmation', 'ambiguity_resolution')),

    -- Action
    undo_group_id             TEXT NULL,
    undo_outcome              TEXT NULL
        CHECK (undo_outcome IS NULL OR undo_outcome IN ('committed', 'rolled_back', 'abandoned')),

    -- State references
    document_ref              TEXT NULL,
    document_revision_ref     TEXT NULL,
    feature_tree_before       JSONB NULL,
    feature_tree_after        JSONB NULL,
    feature_tree_before_hash  TEXT NOT NULL,
    feature_tree_after_hash   TEXT NOT NULL,
    screenshot_before_path    TEXT NULL,
    screenshot_after_path     TEXT NULL,
    screenshot_before_sha256  TEXT NULL,
    screenshot_after_sha256   TEXT NULL,

    -- Integrity
    previous_hash             TEXT NOT NULL,
    tool_calls_hash           TEXT NOT NULL,
    record_hash               TEXT NOT NULL,

    UNIQUE (session_id, sequence_number)
);

CREATE TABLE dhf.tool_calls (
    tool_call_id            UUID PRIMARY KEY,
    entry_id                UUID NOT NULL REFERENCES dhf.entries(entry_id),
    sequence_in_turn        INT NOT NULL,
    call_message_id         TEXT NOT NULL,
    tool_name               TEXT NOT NULL,
    inputs                  JSONB NOT NULL,
    result                  JSONB NULL,
    error_code              TEXT NULL,
    error_message           TEXT NULL,
    success                 BOOLEAN NULL,
    lifecycle_state         TEXT NOT NULL
        CHECK (lifecycle_state IN ('completed', 'timed_out', 'cancelled', 'abandoned')),
    is_internal             BOOLEAN NOT NULL DEFAULT FALSE,
    retry_of_tool_call_id   UUID NULL REFERENCES dhf.tool_calls(tool_call_id),
    retry_depth             INT NOT NULL DEFAULT 0,
    execute_ms              INT NULL,
    rebuild_ms              INT NULL,
    total_ms                INT NULL,
    state_version_before    BIGINT NOT NULL,
    state_version_after     BIGINT NULL,
    addin_session_id        TEXT NOT NULL,

    UNIQUE (entry_id, sequence_in_turn)
);

CREATE INDEX idx_entries_session_seq ON dhf.entries (session_id, sequence_number);
CREATE INDEX idx_tool_calls_entry ON dhf.tool_calls (entry_id, sequence_in_turn);
CREATE INDEX idx_tool_calls_name ON dhf.tool_calls (tool_name);
CREATE INDEX idx_tool_calls_retry_of
    ON dhf.tool_calls (retry_of_tool_call_id)
    WHERE retry_of_tool_call_id IS NOT NULL;
```

```sql
-- 004_entry_decorations.sql

CREATE TABLE dhf.entry_decorations (
    decoration_id           UUID PRIMARY KEY,
    entry_id                UUID NOT NULL REFERENCES dhf.entries(entry_id),
    decoration_type         TEXT NOT NULL
        CHECK (decoration_type IN (
            'rationale_extraction',
            'manual_annotation',
            'ambiguity_context',
            'integrity_event'
        )),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by              TEXT NOT NULL,
    payload                 JSONB NOT NULL,
    decoration_hash         TEXT NOT NULL,
    supersedes_decoration_id UUID NULL REFERENCES dhf.entry_decorations(decoration_id)
);

CREATE INDEX idx_decorations_entry ON dhf.entry_decorations (entry_id, created_at);
CREATE INDEX idx_decorations_type ON dhf.entry_decorations (entry_id, decoration_type);

-- Atomic deduplication for integrity_event decorations.
CREATE UNIQUE INDEX idx_integrity_event_dedup
    ON dhf.entry_decorations (entry_id, (payload->>'dedupKey'))
    WHERE decoration_type = 'integrity_event'
      AND payload->>'dedupKey' IS NOT NULL;
```

```sql
-- 005_immutability_triggers.sql

CREATE OR REPLACE FUNCTION dhf.reject_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'dhf.% is append-only; % is not permitted',
        TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

-- Initial guard_session_update; superseded by migration 006 (tenant_id)
-- and migration 008 (locked_at + lock_reason).
CREATE OR REPLACE FUNCTION dhf.guard_session_update() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.ended_at IS NOT NULL THEN
        RAISE EXCEPTION
            'dhf.sessions: session % is already closed; no further updates permitted',
            OLD.session_id;
    END IF;

    IF NEW.ended_at IS NULL THEN
        RAISE EXCEPTION
            'dhf.sessions: only ended_at (NULL to non-NULL) may be updated on session %',
            OLD.session_id;
    END IF;

    IF ROW(NEW.session_id, NEW.started_at, NEW.user_identity, NEW.user_display_name,
           NEW.workstation_id, NEW.sw_version, NEW.addin_version,
           NEW.orchestrator_version, NEW.protocol_version, NEW.addin_session_id)
       IS DISTINCT FROM
       ROW(OLD.session_id, OLD.started_at, OLD.user_identity, OLD.user_display_name,
           OLD.workstation_id, OLD.sw_version, OLD.addin_version,
           OLD.orchestrator_version, OLD.protocol_version, OLD.addin_session_id)
    THEN
        RAISE EXCEPTION
            'dhf.sessions: only ended_at may change; metadata columns are immutable on session %',
            OLD.session_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER dhf_sessions_guard_update
    BEFORE UPDATE ON dhf.sessions
    FOR EACH ROW EXECUTE FUNCTION dhf.guard_session_update();

CREATE TRIGGER dhf_sessions_no_delete
    BEFORE DELETE ON dhf.sessions
    FOR EACH ROW EXECUTE FUNCTION dhf.reject_mutation();

CREATE TRIGGER dhf_entries_no_update
    BEFORE UPDATE ON dhf.entries
    FOR EACH ROW EXECUTE FUNCTION dhf.reject_mutation();

CREATE TRIGGER dhf_entries_no_delete
    BEFORE DELETE ON dhf.entries
    FOR EACH ROW EXECUTE FUNCTION dhf.reject_mutation();

CREATE TRIGGER dhf_tool_calls_no_update
    BEFORE UPDATE ON dhf.tool_calls
    FOR EACH ROW EXECUTE FUNCTION dhf.reject_mutation();

CREATE TRIGGER dhf_tool_calls_no_delete
    BEFORE DELETE ON dhf.tool_calls
    FOR EACH ROW EXECUTE FUNCTION dhf.reject_mutation();
```

```sql
-- 006_tenant_id.sql

-- Add tenant_id column with a safe default for any pre-existing rows.
ALTER TABLE dhf.sessions
    ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'unassigned';

-- Index for tenant-scoped queries.
CREATE INDEX idx_sessions_tenant ON dhf.sessions (tenant_id, started_at DESC);

-- Replace the partial unique index with a tenant-scoped version.
DROP INDEX dhf.idx_sessions_open_addin_session;
CREATE UNIQUE INDEX idx_sessions_open_addin_session
    ON dhf.sessions (tenant_id, addin_session_id)
    WHERE ended_at IS NULL;

-- Replace guard_session_update to include tenant_id in the immutable-column check.
-- (Further superseded by migration 008 to permit locked_at + lock_reason transitions.)
CREATE OR REPLACE FUNCTION dhf.guard_session_update() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.ended_at IS NOT NULL THEN
        RAISE EXCEPTION
            'dhf.sessions: session % is already closed; no further updates permitted',
            OLD.session_id;
    END IF;

    IF NEW.ended_at IS NULL THEN
        RAISE EXCEPTION
            'dhf.sessions: only ended_at (NULL to non-NULL) may be updated on session %',
            OLD.session_id;
    END IF;

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
            'dhf.sessions: only ended_at may change; metadata columns are immutable on session %',
            OLD.session_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

```sql
-- 007_vocabulary_resolution_decoration.sql
-- Owned by SPEC-04 §3. Added for SPEC-07 coordination: extends the
-- decoration_type CHECK constraint to admit 'vocabulary_resolution'.
-- SPEC-07 §8.1 reproduces this SQL for cross-reference; SPEC-04 is authoritative.
-- Migration executes in the orchestrator's migration sequence between 006 and 008.

ALTER TABLE dhf.entry_decorations
    DROP CONSTRAINT entry_decorations_decoration_type_check;

ALTER TABLE dhf.entry_decorations
    ADD CONSTRAINT entry_decorations_decoration_type_check
    CHECK (decoration_type IN (
        'rationale_extraction',
        'manual_annotation',
        'ambiguity_context',
        'integrity_event',
        'vocabulary_resolution'
    ));
```

```sql
-- 008_durable_lock_and_screenshot_bytes.sql
-- v1.5.0: R3 C1 (durable session lock) + C2 (screenshot BYTEA storage)

-- C1: Durable session lock columns on dhf.sessions.
-- Lock is set via IDhfRepository.LockSessionAsync; cleared only by operator SQL.
-- Partial unique index ensures a locked open session is excluded from
-- idx_sessions_open_addin_session resumption lookups at the query level.
ALTER TABLE dhf.sessions
    ADD COLUMN locked_at    TIMESTAMPTZ NULL,
    ADD COLUMN lock_reason  TEXT NULL;

-- Rebuild the partial unique index to exclude locked sessions from the
-- resumable-session uniqueness set. A locked session remains visible (ended_at
-- still NULL) but cannot be resumed; the unique index is scoped to the
-- resumption-eligible subset.
DROP INDEX dhf.idx_sessions_open_addin_session;
CREATE UNIQUE INDEX idx_sessions_open_addin_session
    ON dhf.sessions (tenant_id, addin_session_id)
    WHERE ended_at IS NULL AND locked_at IS NULL;

-- Index for operator queries over locked sessions.
CREATE INDEX idx_sessions_locked
    ON dhf.sessions (tenant_id, locked_at)
    WHERE locked_at IS NOT NULL;

-- C2: Screenshot BYTEA storage on dhf.entries.
-- Base64 payloads from SPEC-01 ScreenshotResult are decoded into raw PNG bytes
-- by the orchestrator's DHF writer, then stored here.
ALTER TABLE dhf.entries
    ADD COLUMN screenshot_before_png  BYTEA NULL,
    ADD COLUMN screenshot_after_png   BYTEA NULL;

-- Replace guard_session_update to permit locked_at and lock_reason transitions
-- (NULL → non-NULL), alongside the existing ended_at transition. Other columns
-- remain immutable per SDR-04-010.
CREATE OR REPLACE FUNCTION dhf.guard_session_update() RETURNS TRIGGER AS $$
BEGIN
    -- Closed session is terminal: no further updates of any kind.
    IF OLD.ended_at IS NOT NULL THEN
        RAISE EXCEPTION
            'dhf.sessions: session % is already closed; no further updates permitted',
            OLD.session_id;
    END IF;

    -- Permit NULL -> non-NULL transitions on ended_at, locked_at, lock_reason.
    -- Reject any non-NULL -> non-NULL or non-NULL -> NULL change on these columns.
    IF OLD.ended_at IS NULL AND NEW.ended_at IS NOT NULL THEN
        -- close path; acceptable
        NULL;
    ELSIF OLD.ended_at IS DISTINCT FROM NEW.ended_at THEN
        RAISE EXCEPTION
            'dhf.sessions: ended_at may only transition NULL -> non-NULL on session %',
            OLD.session_id;
    END IF;

    IF OLD.locked_at IS NULL AND NEW.locked_at IS NOT NULL THEN
        -- lock path; acceptable
        IF NEW.lock_reason IS NULL THEN
            RAISE EXCEPTION
                'dhf.sessions: lock_reason is required when setting locked_at on session %',
                OLD.session_id;
        END IF;
    ELSIF OLD.locked_at IS DISTINCT FROM NEW.locked_at THEN
        RAISE EXCEPTION
            'dhf.sessions: locked_at may only transition NULL -> non-NULL on session % (unlock requires out-of-band SQL)',
            OLD.session_id;
    END IF;

    -- v1.5.1 M2: lock_reason may only be set in the same UPDATE that sets locked_at.
    -- Without this branch, `UPDATE ... SET lock_reason = 'x'` alone would pass the
    -- locked_at-immutable branch above (OLD.locked_at = NEW.locked_at = NULL) and
    -- the lock_reason-immutable branch below (OLD.lock_reason was NULL so the
    -- "once set" guard doesn't apply), leaving a spurious lock_reason on an
    -- unlocked session.
    IF NEW.lock_reason IS NOT NULL AND NEW.locked_at IS NULL THEN
        RAISE EXCEPTION
            'dhf.sessions: lock_reason may not be set without setting locked_at on session %',
            OLD.session_id;
    END IF;

    IF OLD.lock_reason IS DISTINCT FROM NEW.lock_reason AND OLD.lock_reason IS NOT NULL THEN
        RAISE EXCEPTION
            'dhf.sessions: lock_reason is immutable once set on session %',
            OLD.session_id;
    END IF;

    -- All other metadata columns remain strictly immutable.
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

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

**Unlocking a session (operator-only, v1):** Unlocking requires out-of-band SQL. The trigger above permits only NULL→non-NULL transitions on `locked_at`; reversing the lock (non-NULL→NULL) is rejected. To unlock a session, an operator MUST either:

1. Close the session (`UPDATE dhf.sessions SET ended_at = NOW() WHERE session_id = ...`), preventing further writes, then open a new session on reconnect. This is the RECOMMENDED path — the locked session remains in the audit trail as evidence of the append failure, and a fresh session carries on with a clean chain from GENESIS.
2. As a last resort, DBA-level direct trigger bypass (`ALTER TABLE dhf.sessions DISABLE TRIGGER dhf_sessions_guard_update; UPDATE ... SET locked_at = NULL ...; ENABLE TRIGGER`). This leaves forensic evidence in the audit trail that the lock was cleared, but breaks the immutability guarantee. Not recommended for regulated use.

v1.5 post-hackathon: a programmatic unlock API with operator-role RBAC and audit-trail decoration recording the unlock event.

```sql
-- 009_recovery_events.sql
-- v1.5.0: R3 C4 (recovery events have no schema representation)
-- Persistence model per SDR-TL-013 (SPEC-00) and SDR-04-012 (SPEC-04):
-- same table, turn_type = 'recovery', recovery_context JSONB.

-- Extend turn_type CHECK constraint to include 'recovery'.
ALTER TABLE dhf.entries
    DROP CONSTRAINT entries_turn_type_check;

ALTER TABLE dhf.entries
    ADD CONSTRAINT entries_turn_type_check
    CHECK (turn_type IN (
        'execution',
        'clarification',
        'confirmation',
        'ambiguity_resolution',
        'recovery'
    ));

-- Add recovery_context JSONB column. Nullable; populated only for recovery entries.
ALTER TABLE dhf.entries
    ADD COLUMN recovery_context JSONB NULL;

-- Partial index for operator queries over recovery entries.
CREATE INDEX idx_entries_recovery
    ON dhf.entries (session_id, timestamp)
    WHERE turn_type = 'recovery';

-- Constraint: recovery_context is required when turn_type = 'recovery',
-- and forbidden otherwise. Enforced at the schema level.
ALTER TABLE dhf.entries
    ADD CONSTRAINT entries_recovery_context_iff_recovery_type
    CHECK (
        (turn_type = 'recovery' AND recovery_context IS NOT NULL)
        OR
        (turn_type <> 'recovery' AND recovery_context IS NULL)
    );
```

Rows inserted before migration 006 carry `tenant_id = 'unassigned'` — a safe placeholder. Operators should reassign these rows manually if preserving pre-deployment test data is required. Rows inserted before migration 008 have `NULL` `locked_at` / `lock_reason` / `screenshot_before_png` / `screenshot_after_png` — the existing SHA-256 columns remain authoritative for integrity, and the BYTEA columns are populated only for entries written after migration 008 executes. Rows inserted before migration 009 have `NULL` `recovery_context` and no recovery-type entries (impossible because the CHECK constraint was added at that point).

### 3.2 Enforcement Gap (Honest Disclosure)

DB-level triggers prevent unauthorized mutation across all core tables:

- **`dhf.sessions`**: `guard_session_update` permits only NULL→non-NULL transitions on `ended_at`, `locked_at`, and `lock_reason` (v1.5.0). All other column changes including `tenant_id`, `user_identity`, version metadata are rejected. `reject_mutation` prevents DELETE.
- **`dhf.entries`** and **`dhf.tool_calls`**: `reject_mutation` prevents all UPDATE and DELETE.
- **`dhf.entry_decorations`**: No DB trigger — see SDR-04-002.

The orchestrator has `DROP TRIGGER` privilege in hackathon scope. A malicious or buggy orchestrator could drop triggers, mutate rows, and recreate triggers. This is acknowledged in SDR-TL-006 (SPEC-00). Post-hackathon: separate DBA and application roles.

Session metadata (including `tenant_id`) is not currently covered by the entry hash chain — entries reference `session_id` as a FK but do not hash session-level fields. The trigger prevents mutation, but a successful trigger bypass (via DROP TRIGGER) could alter session metadata without invalidating any entry hashes. Post-hackathon: compute `session_metadata_hash` at INSERT time and anchor it in the first entry's hash computation.

For `dhf.entry_decorations`, no DB trigger is installed because the insert-with-supersede model (SDR-04-002) may require post-hackathon administrative cleanup. The application-level invariant (no UPDATE/DELETE in normal operation) is enforced by architecture tests that scan for prohibited SQL patterns.

For hackathon credibility, the framing is "defense-in-depth append-only enforcement" — application code never attempts unauthorized mutation, architecture tests forbid it in code, AND the database raises exceptions on attempts against sessions, entries, and tool_calls.

## 4. Hash Model

### 4.1 HashCanonicalizer Rules (Imported from SPEC-01)

- `System.Text.Json` with fixed options: camelCase property names, PascalCase enum strings, `decimal` serialized "G29" invariant-culture, null-omitting, sorted object keys.
- SHA-256 of UTF-8 canonical JSON, output as lowercase hex.
- `"NO_DOCUMENT"` is a literal sentinel string for null feature-tree states, not a hash of anything.
- `"NO_RECOVERY_CONTEXT"` is a literal sentinel string for entries where `recovery_context IS NULL` (i.e., non-recovery turns).
- `DateTimeOffset` values MUST be normalized to UTC and truncated to microsecond precision (6 decimal places) before hashing. Canonical format: `yyyy-MM-ddTHH:mm:ss.ffffffZ` via `value.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'", CultureInfo.InvariantCulture)`. Postgres `TIMESTAMPTZ` stores microsecond precision, so the round-trip is lossless when this rule is followed.
- Enum values in hash inputs MUST use the storage-form string from `DhfEnumMappings` (§4.5), NOT `.ToString()`.

```csharp
public static DateTimeOffset NormalizeTimestamp(DateTimeOffset value)
{
    var utc = value.ToUniversalTime();
    var truncatedTicks = utc.Ticks - (utc.Ticks % 10);
    return new DateTimeOffset(truncatedTicks, TimeSpan.Zero);
}
```

### 4.2 DhfHashColumns (Entry-Level)

Ordered list in `BSVoiceCAD.Protocol/Serialization/DhfHashColumns.cs`. This is THE exact input to `record_hash` computation.

```csharp
public static class DhfHashColumns
{
    public static readonly IReadOnlyList<string> HashedColumns = new[]
    {
        "entry_id", "session_id", "sequence_number", "timestamp",
        "input_modality", "raw_transcript", "transcript_confidence",
        "turn_type", "undo_group_id", "undo_outcome",
        "document_ref", "document_revision_ref",
        "feature_tree_before_hash", "feature_tree_after_hash",
        "screenshot_before_sha256", "screenshot_after_sha256",
        "recovery_context_hash",    // v1.5.0 — canonical hash of recovery_context JSONB
        "previous_hash", "tool_calls_hash"
    };
}
```

**`recovery_context_hash` (v1.5.0):** a canonical SHA-256 hash of the `recovery_context` JSONB content, computed via `HashCanonicalizer.ComputeRecoveryContextHash`. When `recovery_context IS NULL` (non-recovery entries), the value is the sentinel `"NO_RECOVERY_CONTEXT"`. The JSONB blob itself is NOT hashed directly — the canonical hash IS included so tampering with `recovery_context` changes `record_hash` and breaks the chain.

Two additional changes in the hash input set compared to v1.4.0:
1. `screenshot_before_path` and `screenshot_after_path` are REMOVED from the hash input. These columns are legacy artifacts from the pre-deployment-shift era; new rows written by v1.5.0 code leave them NULL. The SHA-256 columns remain the authoritative integrity anchor for screenshots.
2. The BYTEA columns (`screenshot_before_png`, `screenshot_after_png`) are NOT hashed directly — the SHA-256 columns cover their integrity. Storing the bytes AND the hash provides defense-in-depth for deep validation (§10.2).

**Breaking hash-schema change (v1.4.0 → v1.5.0 / dev-data-only scope, v1.5.1 M3 disclosure).** Entries written under v1.4.0 rules have `record_hash` values that will NOT match v1.5.0's re-computation — the hash-input column set differs (removed: `screenshot_before_path`, `screenshot_after_path`; added: `recovery_context_hash`). Under the hackathon scope (no production v1.4.0 data exists — the DHF ledger was introduced in v1), this is out-of-scope. Post-hackathon: either retire all pre-v1.5.0 rows before enabling deep validation, OR introduce a `hash_algorithm_version` column on `dhf.entries` and dispatch validation per-version. This decision is deferred to the first production deployment.

The raw JSONB blobs (`feature_tree_before`, `feature_tree_after`) are NOT hashed directly; their `_hash` columns are.

`tenant_id` is NOT included in entry hash input — it's session-level metadata, not entry-level.

### 4.2a Indirectly-Hashed Entry Columns (v1.5.1 M3 enumeration, corrected v1.5.2 C2)

The `DhfHashColumns.HashedColumns` list in §4.2 is the **authoritative** set of columns that feed `record_hash`. Identity and ordering columns (`entry_id`, `session_id`, `sequence_number`) are in that set — they ARE hashed directly.

This subsection enumerates **the large-payload columns that are hashed *indirectly*** via companion `*_hash` / `*_sha256` anchor columns. The anchor columns ARE in `DhfHashColumns.HashedColumns`; the raw JSONB / BYTEA bodies are NOT — but deep validation (§10.2) re-computes each anchor from its body, so tampering with the body still invalidates `record_hash`.

| Column | Type | Anchor column (directly hashed) |
|---|---|---|
| `feature_tree_before` | JSONB | `feature_tree_before_hash` (canonical JSONB hash) |
| `feature_tree_after` | JSONB | `feature_tree_after_hash` |
| `recovery_context` | JSONB (v1.5.0) | `recovery_context_hash` (§4.4a) |
| `screenshot_before_png` | BYTEA (v1.5.0) | `screenshot_before_sha256` |
| `screenshot_after_png` | BYTEA (v1.5.0) | `screenshot_after_sha256` |

Legacy columns `screenshot_before_path` and `screenshot_after_path` (TEXT) are NOT in `DhfHashColumns` and have no anchor — they were removed from the hash input in v1.5.0 and are left NULL by new rows.

**Sanity-check invariant (enforced by `HashColumnSyncTests` per v1.5.1 E9).** Every content-bearing column of `dhf.entries` is covered either directly (the column appears in `DhfHashColumns.HashedColumns`) or indirectly (the column's anchor appears in `DhfHashColumns.HashedColumns`). No column is both. Identity columns are directly hashed. The list of indirectly-hashed columns is exactly the table above.

### 4.3 ToolCallHashColumns (Child-Row Level)

```csharp
public static class ToolCallHashColumns
{
    public static readonly IReadOnlyList<string> HashedColumns = new[]
    {
        "sequence_in_turn", "call_message_id", "tool_name",
        "inputs", "result", "error_code", "error_message", "success",
        "lifecycle_state", "is_internal",
        "retry_of_tool_call_id", "retry_depth",
        "execute_ms", "rebuild_ms", "total_ms",
        "state_version_before", "state_version_after", "addin_session_id"
    };
}
```

`tool_call_id` (PK) and `entry_id` (parent FK) are NOT included. Child rows are canonicalized in `sequence_in_turn` ascending order before hashing.

### 4.4 tool_calls_hash Computation

```csharp
public static string ComputeToolCallsHash(IReadOnlyList<ToolCallRow> orderedToolCalls)
{
    if (orderedToolCalls.Count == 0)
        return "NO_TOOL_CALLS";

    var canonicalForm = orderedToolCalls
        .Select(tc => ExtractHashedColumns(tc, ToolCallHashColumns.HashedColumns))
        .ToList();

    var json = JsonSerializer.Serialize(canonicalForm, HashCanonicalizer.JsonOptions);
    return Sha256Hex(Encoding.UTF8.GetBytes(json));
}
```

Empty tool-call sets (e.g., `clarification`, `confirmation`, or `recovery` turns) produce the sentinel `"NO_TOOL_CALLS"`.

### 4.4a recovery_context_hash Computation (v1.5.0, constant-named in v1.5.1)

```csharp
// v1.5.1 m1: sentinel is exposed as a named constant to eliminate
// string-literal drift between AppendTurnCore, HashChainValidator, and
// DhfHashColumns canonicalization.
public static class HashCanonicalizer
{
    public const string NoRecoveryContextSentinel = "NO_RECOVERY_CONTEXT";

    public static string ComputeRecoveryContextHash(JsonElement? recoveryContext)
    {
        if (recoveryContext is null or { ValueKind: JsonValueKind.Null })
            return NoRecoveryContextSentinel;

        // Canonicalize: sort keys, normalize whitespace, strip nulls.
        var canonical = CanonicalizeJson(recoveryContext.Value);
        return Sha256Hex(Encoding.UTF8.GetBytes(canonical));
    }
}
```

`CanonicalizeJson` applies the same canonicalization as top-level `HashCanonicalizer.JsonOptions`: sorted object keys, camelCase properties, PascalCase enum strings, null-omitting, invariant-culture decimal formatting.

### 4.5 Enum-to-Storage Mappings

`BSVoiceCAD.Orchestrator.Dhf/DhfEnumMappings.cs`. All enum-to-string conversions for DB persistence MUST use these methods. Raw `.ToString()` or `.ToLowerInvariant()` on enums is prohibited by architecture test.

```csharp
public static class DhfEnumMappings
{
    public static string ToStorage(this InputModality m) => m switch
    {
        InputModality.Voice => "voice",
        InputModality.Text => "text",
        _ => throw new ArgumentOutOfRangeException(nameof(m))
    };

    public static string ToStorage(this TurnType t) => t switch
    {
        TurnType.Execution => "execution",
        TurnType.Clarification => "clarification",
        TurnType.Confirmation => "confirmation",
        TurnType.AmbiguityResolution => "ambiguity_resolution",
        TurnType.Recovery => "recovery",                          // v1.5.0
        _ => throw new ArgumentOutOfRangeException(nameof(t))
    };

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
        DecorationType.VocabularyResolution => "vocabulary_resolution",  // v1.5.0 (SPEC-07)
        _ => throw new ArgumentOutOfRangeException(nameof(d))
    };
}
```

## 5. Input DTOs

The DHF layer never constructs turn records itself. SPEC-03 assembles a complete `DhfTurnContext` and hands it in. Recovery events come from SPEC-03's recovery flow as a `RecoveryEventContext`. `tenant_id` is NOT a field on either DTO — it's resolved from the `SessionId` at append time via the session lookup in §7.

### 5.1 DhfTurnContext

```csharp
public record DhfTurnContext
{
    public required Guid TurnId { get; init; }
    public required Guid SessionId { get; init; }
    public required DateTimeOffset Timestamp { get; init; }
    public required InputModality InputModality { get; init; }
    public required string RawTranscript { get; init; }
    public float? TranscriptConfidence { get; init; }
    public required TurnType TurnType { get; init; }
    public string? UndoGroupId { get; init; }
    public UndoGroupOutcome? UndoOutcome { get; init; }
    public DocumentRef? DocumentRef { get; init; }
    public string? DocumentRevisionRef { get; init; }
    public JsonElement? FeatureTreeBefore { get; init; }
    public JsonElement? FeatureTreeAfter { get; init; }
    public required string FeatureTreeBeforeHash { get; init; }
    public required string FeatureTreeAfterHash { get; init; }
    public string? ScreenshotBeforeSha256 { get; init; }
    public string? ScreenshotAfterSha256 { get; init; }
    public byte[]? ScreenshotBeforePng { get; init; }          // v1.5.0 — R3 H7
    public byte[]? ScreenshotAfterPng { get; init; }           // v1.5.0 — R3 H7
    public required IReadOnlyList<ToolCallRecord> ToolCalls { get; init; }
}

public record ToolCallRecord
{
    public required Guid ToolCallId { get; init; }
    public required int SequenceInTurn { get; init; }
    public required string CallMessageId { get; init; }
    public required string ToolName { get; init; }
    public required JsonElement Inputs { get; init; }
    public JsonElement? Result { get; init; }
    public string? ErrorCode { get; init; }
    public string? ErrorMessage { get; init; }
    public bool? Success { get; init; }
    public required ToolCallLifecycleState LifecycleState { get; init; }
    public bool IsInternal { get; init; }
    public Guid? RetryOfToolCallId { get; init; }
    public int RetryDepth { get; init; }
    public int? ExecuteMs { get; init; }
    public int? RebuildMs { get; init; }
    public int? TotalMs { get; init; }
    public required long StateVersionBefore { get; init; }
    public long? StateVersionAfter { get; init; }
    public required string AddinSessionId { get; init; }
}

public enum InputModality { Voice, Text }

public enum TurnType
{
    Execution,
    Clarification,
    Confirmation,
    AmbiguityResolution,
    Recovery                // v1.5.0 — recovery events stored in dhf.entries with turn_type='recovery'
}

public enum DecorationType
{
    RationaleExtraction,
    ManualAnnotation,
    AmbiguityContext,
    IntegrityEvent,
    VocabularyResolution    // v1.5.0 — SPEC-07 coordination
}
```

**Invariant (v1.5.0):** `DhfTurnContext.TurnType` MUST NOT be `Recovery`. `AppendTurnAsync` is the API for all non-recovery turn types; `AppendRecoveryEventAsync` (§7.2) is the API for recovery events. `AppendTurnAsync` rejects `TurnType.Recovery` with `ArgumentException("Use AppendRecoveryEventAsync for recovery events.")`. This keeps the two paths semantically distinct even though they write to the same table.

`ToolCallLifecycleState` and `UndoGroupOutcome` are defined in SPEC-01.

### 5.2 RecoveryEventContext (NEW v1.5.0)

```csharp
public record RecoveryEventContext
{
    public required Guid RecoveryEventId { get; init; }
    public required Guid SessionId { get; init; }
    public required DateTimeOffset Timestamp { get; init; }

    // Structured recovery fields (R3 H8 — must NOT be collapsed to free-text)
    public required RecoveryTrigger Trigger { get; init; }
    public required RecoveryStrategy Strategy { get; init; }
    public required bool Success { get; init; }
    public string? FailureReason { get; init; }
    public string? ModelStateDivergenceDescription { get; init; }

    // Optional state-reconciliation evidence
    public JsonElement? FeatureTreeBefore { get; init; }
    public JsonElement? FeatureTreeAfter { get; init; }
    public required string FeatureTreeBeforeHash { get; init; }
    public required string FeatureTreeAfterHash { get; init; }

    // Optional screenshot evidence (R3 H7 — must flow into BYTEA columns)
    public string? ScreenshotBeforeSha256 { get; init; }
    public string? ScreenshotAfterSha256 { get; init; }
    public byte[]? ScreenshotBeforePng { get; init; }
    public byte[]? ScreenshotAfterPng { get; init; }

    // Optional reference to the preceding turn entry (timed-out turn, if recovery
    // was triggered by timeout). Hash-chain continuity does NOT depend on this
    // field — it's informational for viewer rendering and operator audit.
    public Guid? PrecedingTurnEntryId { get; init; }

    // Optional reference to any quarantined late messages whose existence
    // motivated this recovery event (SPEC-00 §6 glossary — quarantine
    // referencing rule). Log entry IDs from the orchestrator's structured log.
    public IReadOnlyList<string>? ReferencedQuarantineLogIds { get; init; }
}

public enum RecoveryTrigger
{
    Timeout,           // Tool-call orchestrator-side timeout fired; COM may have completed
    Crash,             // Add-in process terminated between turn open and turn completion
    Disconnect,        // Transport loss during in-flight turn
    StateReconcile     // Reconnect detected model state divergence from last DHF record
}

public enum RecoveryStrategy
{
    StateRefresh,      // Fetch current feature tree + state; reconcile by comparison
    UndoGroupRollback  // Invoke SolidWorks undo to revert the in-flight undo group
}
```

**Semantics:**
- `RecoveryEventContext` is the input to `AppendRecoveryEventAsync` (§7.2). The structured fields are persisted in the `recovery_context` JSONB column; they are NOT flattened into `RawTranscript`.
- The `RawTranscript` column on the entry row is populated with a short machine-generated description (e.g., `"Recovery event: timeout trigger, state_refresh strategy, success"`) for viewer list rendering and `raw_transcript NOT NULL` compliance. The authoritative structured data is in `recovery_context`.
- Hash coverage includes `recovery_context_hash` (§4.2) which is a canonical hash of the JSONB content. Tampering with `recovery_context` invalidates the entry's `record_hash`.
- Screenshot PNG bytes (when provided) are written to `screenshot_before_png` / `screenshot_after_png` BYTEA columns, the same columns used by turn entries. SHA-256 columns remain authoritative for integrity.

## 6. DhfSessionManager

### 6.1 Open or Resume

Called by the orchestrator on every `AddinReadyMessage`, with the caller's `tenantId` resolved from the authenticated WebSocket (SPEC-01 §12.5). Returns the `SessionId` that subsequent `AppendTurnAsync` calls should use.

Every query includes `WHERE tenant_id = @TenantId` (tenant scoping). Cross-tenant `AddinSessionId` collisions are invisible to each other by query construction.

The method is wrapped in a bounded retry loop with jitter to handle serialization failures (`SQLSTATE 40001`) and unique-index violations (`SQLSTATE 23505`) under concurrent startup.

**Cross-workstation conflict is a HARD ERROR** (SDR-04-008): `OpenOrResumeSessionCore` throws `CrossWorkstationSessionConflictException` (§6.3). Not retried.

**Locked session is a HARD ERROR (v1.5.0):** If a same-AddinSessionId session within the tenant+workstation is locked (`locked_at IS NOT NULL`), `OpenOrResumeSessionCore` throws `SessionLockedException` (§9.3). Not retried. Locked sessions are excluded from resumption by the `idx_sessions_open_addin_session` partial unique index (migration 008), so the query that would normally match an open session correctly returns NULL for a locked one. The explicit check below catches the case where the lock was set between the initial lookup and the tenant-workstation cleanup step.

```csharp
public async Task<Guid> OpenOrResumeSessionAsync(
    string addinSessionId,
    string tenantId,
    SessionMetadata metadata,
    CancellationToken ct)
{
    const int maxRetries = 3;
    for (int attempt = 0; ; attempt++)
    {
        try
        {
            return await OpenOrResumeSessionCore(addinSessionId, tenantId, metadata, ct);
        }
        catch (PostgresException ex)
            when ((ex.SqlState == PostgresErrorCodes.SerializationFailure
                || ex.SqlState == PostgresErrorCodes.UniqueViolation)
                && attempt < maxRetries - 1)
        {
            _logger.LogWarning(ex,
                "Session open/resume attempt {Attempt} failed with {SqlState}; retrying",
                attempt, ex.SqlState);
            await Task.Delay(
                50 * (attempt + 1) + Random.Shared.Next(0, 50), ct);
        }
        // CrossWorkstationSessionConflictException and SessionLockedException
        // are NOT caught here; they propagate to the caller.
    }
}

private async Task<Guid> OpenOrResumeSessionCore(
    string addinSessionId,
    string tenantId,
    SessionMetadata metadata,
    CancellationToken ct)
{
    using var conn = await _dataSource.OpenConnectionAsync(ct);
    using var tx = await conn.BeginTransactionAsync(IsolationLevel.Serializable, ct);

    var lockKey = $"dhf_session_open:{tenantId}:{metadata.WorkstationId}";
    await conn.ExecuteAsync(
        "SELECT pg_advisory_xact_lock(hashtext(@k))",
        new { k = lockKey },
        tx);

    // Look up any open-and-not-locked session. Note the WHERE clause excludes
    // locked sessions; the same predicate is what idx_sessions_open_addin_session
    // (migration 008) indexes.
    var existing = await conn.QuerySingleOrDefaultAsync<SessionLookupRow>(
        @"SELECT session_id, workstation_id, started_at, user_identity
          FROM dhf.sessions
          WHERE tenant_id = @TenantId
            AND addin_session_id = @AddinSessionId
            AND ended_at IS NULL
            AND locked_at IS NULL",
        new { tenantId, addinSessionId },
        tx);

    if (existing is not null)
    {
        if (existing.WorkstationId == metadata.WorkstationId)
        {
            await tx.CommitAsync(ct);
            return existing.SessionId;  // Resume.
        }

        // Cross-workstation within tenant — hard error.
        _logger.LogError(
            "Cross-workstation session conflict within tenant {TenantId}: "
            + "addinSessionId {AddinSessionId} is open on workstation {OtherWorkstation} "
            + "(session {OtherSessionId}, started {StartedAt}, user {OtherUser}); "
            + "refusing to open or resume on workstation {ThisWorkstation}.",
            tenantId, addinSessionId, existing.WorkstationId, existing.SessionId,
            existing.StartedAt, existing.UserIdentity, metadata.WorkstationId);

        await tx.RollbackAsync(ct);

        throw new CrossWorkstationSessionConflictException(
            addinSessionId: addinSessionId,
            tenantId: tenantId,
            otherSessionId: existing.SessionId,
            otherWorkstationId: existing.WorkstationId,
            otherUserIdentity: existing.UserIdentity,
            otherSessionStartedAt: existing.StartedAt,
            thisWorkstationId: metadata.WorkstationId);
    }

    // v1.5.2 H3 FIX: Tenant-wide cross-workstation locked-session check.
    // The previous `resumable` query excluded locked rows; the PRIOR v1.5.1
    // locked-session check filtered by `workstation_id = @WorkstationId`,
    // which meant a locked open session for the same tenant+AddinSessionId
    // on a DIFFERENT workstation matched NEITHER branch — allowing a new
    // session to be inserted and bypassing SDR-04-008's cross-workstation
    // rule. The fix: scan for any locked session on this tenant+AddinSessionId
    // REGARDLESS of workstation, then branch on workstation match.
    var locked = await conn.QuerySingleOrDefaultAsync<LockedSessionRow>(
        @"SELECT session_id, workstation_id, lock_reason
          FROM dhf.sessions
          WHERE tenant_id = @TenantId
            AND addin_session_id = @AddinSessionId
            AND ended_at IS NULL
            AND locked_at IS NOT NULL",
        new { tenantId, addinSessionId },
        tx);

    if (locked is not null)
    {
        if (locked.WorkstationId == metadata.WorkstationId)
        {
            // Same-workstation lock — familiar SessionLockedException path.
            _logger.LogError(
                "Session {SessionId} is locked (reason: {LockReason}); "
                + "refusing to open or resume on same workstation.",
                locked.SessionId, locked.LockReason);

            await tx.RollbackAsync(ct);
            throw new SessionLockedException(locked.SessionId, locked.LockReason);
        }
        else
        {
            // Cross-workstation locked session — stricter hard error.
            // A lock on another workstation for this tenant+AddinSessionId
            // indicates either (a) the AddinSessionId is being reused
            // across workstations (violates SDR-04-008) or (b) an
            // operator moved a locked session between workstations and
            // neglected to close it. Either way, refuse to open here.
            _logger.LogError(
                "Cross-workstation locked session conflict within tenant {TenantId}: "
                + "addinSessionId {AddinSessionId} is LOCKED on workstation "
                + "{OtherWorkstation} (session {OtherSessionId}, lock_reason {LockReason}); "
                + "refusing to open on workstation {ThisWorkstation}.",
                tenantId, addinSessionId, locked.WorkstationId,
                locked.SessionId, locked.LockReason, metadata.WorkstationId);

            await tx.RollbackAsync(ct);
            throw new CrossWorkstationSessionLockedException(
                addinSessionId: addinSessionId,
                tenantId: tenantId,
                otherSessionId: locked.SessionId,
                otherWorkstationId: locked.WorkstationId,
                lockReason: locked.LockReason,
                thisWorkstationId: metadata.WorkstationId);
        }
    }

    // No resumable open session, no locked session blocking us. Close any stale
    // sessions on THIS workstation (left over from prior orchestrator runs —
    // SDR-04-005). Does NOT close locked sessions.
    await conn.ExecuteAsync(
        @"UPDATE dhf.sessions
          SET ended_at = NOW()
          WHERE ended_at IS NULL
            AND locked_at IS NULL
            AND tenant_id = @TenantId
            AND workstation_id = @WorkstationId",
        new { tenantId, metadata.WorkstationId },
        tx);

    // Open a new session scoped to this tenant.
    var newId = Guid.NewGuid();
    await conn.ExecuteAsync(
        @"INSERT INTO dhf.sessions
          (session_id, tenant_id, user_identity, user_display_name, workstation_id,
           sw_version, addin_version, orchestrator_version, protocol_version,
           addin_session_id)
          VALUES (@NewId, @TenantId, @User, @DisplayName, @WorkstationId,
                  @SwVersion, @AddinVersion, @OrchVersion, @ProtocolVersion,
                  @AddinSessionId)",
        new
        {
            NewId = newId,
            tenantId,
            User = metadata.UserIdentity,
            DisplayName = metadata.UserDisplayName,
            metadata.WorkstationId,
            metadata.SwVersion,
            metadata.AddinVersion,
            metadata.OrchestratorVersion,
            metadata.ProtocolVersion,
            addinSessionId
        },
        tx);

    await tx.CommitAsync(ct);
    return newId;
}
```

### 6.2 Close on Add-in Disconnect

`DhfSessionManager.CloseSessionAsync(sessionId)` sets `ended_at = NOW()`. Called when the add-in sends `AddinTeardownMessage` (SPEC-01 v1.4.0 §5, reason `explicit_shutdown`) or when the orchestrator detects add-in connection loss via heartbeat timeout (SPEC-01 §12.7, reason `heartbeat_timeout`). **NOT called on graceful orchestrator shutdown** — see SDR-04-005.

`CloseSessionAsync` acquires the per-session advisory lock to coordinate with in-flight `AppendTurnAsync` operations. Prevents close-during-append races.

```csharp
public async Task CloseSessionAsync(Guid sessionId, CancellationToken ct)
{
    using var conn = await _dataSource.OpenConnectionAsync(ct);
    using var tx = await conn.BeginTransactionAsync(IsolationLevel.Serializable, ct);

    var lockKey = $"dhf_append:{sessionId}";
    await conn.ExecuteAsync(
        "SELECT pg_advisory_xact_lock(hashtext(@k))",
        new { k = lockKey },
        tx);

    await conn.ExecuteAsync(
        @"UPDATE dhf.sessions SET ended_at = NOW()
          WHERE session_id = @SessionId AND ended_at IS NULL",
        new { sessionId },
        tx);

    await tx.CommitAsync(ct);
}
```

Idempotent: if the session is already closed (or locked and then closed), the UPDATE matches zero rows. Note: closing a locked session is permitted — the trigger allows `ended_at` NULL→non-NULL even when `locked_at IS NOT NULL` — this is the operator-recommended unlock path (see migration 008 comments).

### 6.3 CrossWorkstationSessionConflictException

Thrown by `OpenOrResumeSessionCore` when an open session with the same `AddinSessionId` exists on a different workstation WITHIN THE SAME TENANT (SDR-04-008). Carries structured metadata for operator investigation and a simplified SQL remediation snippet.

```csharp
namespace BSVoiceCAD.Orchestrator.Dhf;

public sealed class CrossWorkstationSessionConflictException : InvalidOperationException
{
    public string AddinSessionId { get; }
    public string TenantId { get; }
    public Guid OtherSessionId { get; }
    public string OtherWorkstationId { get; }
    public string OtherUserIdentity { get; }
    public DateTimeOffset OtherSessionStartedAt { get; }
    public string ThisWorkstationId { get; }

    public CrossWorkstationSessionConflictException(
        string addinSessionId,
        string tenantId,
        Guid otherSessionId,
        string otherWorkstationId,
        string otherUserIdentity,
        DateTimeOffset otherSessionStartedAt,
        string thisWorkstationId)
        : base(BuildMessage(
            addinSessionId, tenantId, otherSessionId, otherWorkstationId,
            otherUserIdentity, otherSessionStartedAt, thisWorkstationId))
    {
        AddinSessionId = addinSessionId;
        TenantId = tenantId;
        OtherSessionId = otherSessionId;
        OtherWorkstationId = otherWorkstationId;
        OtherUserIdentity = otherUserIdentity;
        OtherSessionStartedAt = otherSessionStartedAt;
        ThisWorkstationId = thisWorkstationId;
    }

    private static string BuildMessage(
        string addinSessionId,
        string tenantId,
        Guid otherSessionId,
        string otherWorkstationId,
        string otherUserIdentity,
        DateTimeOffset otherSessionStartedAt,
        string thisWorkstationId)
    {
        return
            $"Cross-workstation session conflict (tenant '{tenantId}'). " +
            $"AddinSessionId '{addinSessionId}' is currently open on a DIFFERENT " +
            $"workstation. " +
            $"This workstation: '{thisWorkstationId}'. " +
            $"Other workstation: '{otherWorkstationId}' " +
            $"(session_id={otherSessionId}, user='{otherUserIdentity}', " +
            $"started={otherSessionStartedAt:o}). " +
            $"This indicates one of: (1) AddinSessionId derivation collision within " +
            $"the tenant fleet, (2) VM snapshot cloning, (3) administrative " +
            $"misconfiguration producing identical machine identifiers. " +
            $"No session was opened or modified. " +
            $"To resolve: verify the other workstation is no longer active, then " +
            $"close the stale session via SQL: " +
            $"UPDATE dhf.sessions SET ended_at = NOW() " +
            $"WHERE session_id = '{otherSessionId}' AND ended_at IS NULL;";
    }
}
```

### 6.4 Durable Session Lock (NEW v1.5.0 — R3 C1)

`IDhfRepository.LockSessionAsync` persists the session-lock state via a trigger-permitted UPDATE on `dhf.sessions`. The lock survives orchestrator restart because it is stored in Postgres, not in-process memory.

```csharp
public async Task LockSessionAsync(
    Guid sessionId,
    string lockReason,
    CancellationToken ct)
{
    using var conn = await _dataSource.OpenConnectionAsync(ct);
    using var tx = await conn.BeginTransactionAsync(IsolationLevel.Serializable, ct);

    // Serialize with concurrent appends on the same session.
    var lockKey = $"dhf_append:{sessionId}";
    await conn.ExecuteAsync(
        "SELECT pg_advisory_xact_lock(hashtext(@k))",
        new { k = lockKey },
        tx);

    var rowsAffected = await conn.ExecuteAsync(
        @"UPDATE dhf.sessions
          SET locked_at = NOW(),
              lock_reason = @LockReason
          WHERE session_id = @SessionId
            AND ended_at IS NULL
            AND locked_at IS NULL",
        new { sessionId, lockReason },
        tx);

    if (rowsAffected == 0)
    {
        // Session is already closed or already locked. Not an error; idempotent.
        _logger.LogWarning(
            "LockSessionAsync: session {SessionId} is already closed or locked; "
            + "no update applied.",
            sessionId);
    }
    else
    {
        _logger.LogError(
            "DHF session {SessionId} LOCKED. Reason: {LockReason}. "
            + "No further appends permitted. Operator intervention required.",
            sessionId, lockReason);
    }

    await tx.CommitAsync(ct);
}
```

**Usage:** `DhfLedger.AppendTurnCore` (and `AppendRecoveryEventCore`) catches `PostgresException` after exhausting retries and calls `LockSessionAsync` before re-throwing. SPEC-03 seam C specifies this behavior — the lifecycle layer wraps the append and is responsible for invoking lock on terminal failure. SPEC-04's role is to provide the `LockSessionAsync` primitive; the policy decision (when to lock) lives in SPEC-03.

**Checking for lock in the append path (TOCTOU safety):** `AppendTurnCore` and `AppendRecoveryEventCore` re-verify `locked_at IS NULL` inside the SERIALIZABLE transaction before writing any rows. If a concurrent `LockSessionAsync` completed between the caller's decision to append and the append transaction's lock check, the append fails with `SessionLockedException` rather than silently succeeding on a locked session. See §7.

#### 6.4.1 Locked-Session Recovery — Authoritative Procedure (v1.5.2 H5)

Recovery from a locked session is **close-and-new** only in v1. The `guard_session_update` trigger (migration 008) rejects any UPDATE that transitions `locked_at` from non-NULL to NULL; there is no programmatic unlock path in v1. Prior versions of this document contained residual "unlock" language in places — that language is removed in v1.5.2 and this subsection is the single authoritative source.

**Operator recovery procedure:**

1. **CLOSE the locked session via SQL** (permitted even when `locked_at` is non-NULL):
   ```sql
   UPDATE dhf.sessions
   SET ended_at = NOW()
   WHERE session_id = '<LOCKED_SESSION_ID>'
     AND ended_at IS NULL;
   ```
   The `guard_session_update` trigger permits `ended_at` NULL → non-NULL regardless of lock state. The locked row remains in the DHF as permanent audit-trail evidence of the append failure that triggered the lock.
2. **Add-in generates a fresh AddinSessionId on next load.** The add-in does not know or care that the old session was locked; SPEC-02 §8's `AddinSessionIdGenerator` produces a new ID on each load. The next `AddinReadyMessage` arrives with the new ID, and `OpenOrResumeSessionCore` opens a new `dhf.sessions` row.
3. **SPEC-03's post-crash reconciliation flow** (seam C) handles any model-state divergence between the pre-lock session and the fresh session. The user observes a clean restart; the audit trail preserves the pre-lock entries plus a new chain starting from `previous_hash = 'GENESIS'`.

**What is NOT supported in v1:**

- Programmatic unlock (`UPDATE dhf.sessions SET locked_at = NULL`) — rejected by trigger.
- Resuming a locked session — `OpenOrResumeSessionCore` throws `SessionLockedException` or `CrossWorkstationSessionLockedException` (see §6.1 H3 fix, §9.3).
- DBA-level trigger bypass (`ALTER TABLE ... DISABLE TRIGGER`) is documented as a last resort (§3 migration 008) but leaves forensic evidence and is NOT a recommended recovery path for regulated use.

**v1.5 backlog item:** Programmatic unlock API with operator-role RBAC and audit-trail decoration recording the unlock event. Requires (a) a new API-authenticated operator endpoint, (b) an `integrity_event` decoration on the unlocked session's latest entry, and (c) a trigger update to permit non-NULL → NULL transitions only under a specific operator session identity. Not in v1 scope.

Cross-references: SPEC-00 §6 Glossary entry for "Session Lock" and Seam C both defer to this subsection for the authoritative recovery procedure.

## 7. DhfLedger

The ONLY writer of `dhf.entries` (including recovery entries) and `dhf.tool_calls`.

### 7.1 AppendTurnAsync (non-recovery turns)

Wrapped in a bounded retry loop with jitter for `SerializationFailure` (`SQLSTATE 40001`).

```csharp
public async Task<Guid> AppendTurnAsync(DhfTurnContext turn, CancellationToken ct)
{
    if (turn.TurnType == TurnType.Recovery)
        throw new ArgumentException(
            "Use AppendRecoveryEventAsync for recovery events.", nameof(turn));

    const int maxRetries = 3;
    for (int attempt = 0; ; attempt++)
    {
        try
        {
            return await AppendTurnCore(turn, ct);
        }
        catch (PostgresException ex)
            when (ex.SqlState == PostgresErrorCodes.SerializationFailure
                && attempt < maxRetries - 1)
        {
            _logger.LogWarning(ex,
                "Append attempt {Attempt} for session {SessionId} failed "
                + "with serialization failure; retrying",
                attempt, turn.SessionId);
            await Task.Delay(
                50 * (attempt + 1) + Random.Shared.Next(0, 50), ct);
        }
    }
}

private async Task<Guid> AppendTurnCore(DhfTurnContext turn, CancellationToken ct)
{
    using var conn = await _dataSource.OpenConnectionAsync(ct);
    using var tx = await conn.BeginTransactionAsync(IsolationLevel.Serializable, ct);

    // Advisory lock scoped to the DHF session for the duration of this append only.
    var lockKey = $"dhf_append:{turn.SessionId}";
    await conn.ExecuteAsync(
        "SELECT pg_advisory_xact_lock(hashtext(@k))",
        new { k = lockKey },
        tx);

    // 0. Verify session is open AND not locked. TOCTOU-safe because this runs
    //    inside the SERIALIZABLE transaction with the advisory lock held.
    var sessionRow = await conn.QuerySingleOrDefaultAsync<SessionCheckRow>(
        @"SELECT ended_at IS NULL AS is_open,
                 locked_at IS NULL AS is_unlocked,
                 lock_reason,
                 addin_session_id,
                 tenant_id
          FROM dhf.sessions
          WHERE session_id = @SessionId",
        new { turn.SessionId },
        tx)
        ?? throw new SessionNotFoundException(turn.SessionId);

    if (!sessionRow.IsOpen)
        throw new InvalidOperationException(
            $"Session {turn.SessionId} is closed; cannot append.");

    if (!sessionRow.IsUnlocked)
        throw new SessionLockedException(turn.SessionId, sessionRow.LockReason ?? "unknown");

    foreach (var tc in turn.ToolCalls)
    {
        if (tc.AddinSessionId != sessionRow.AddinSessionId)
            throw new InvalidOperationException(
                $"Tool call {tc.ToolCallId} AddinSessionId '{tc.AddinSessionId}' "
                + $"does not match session '{sessionRow.AddinSessionId}'.");
    }

    // 1. Read chain tip.
    var tipHash = await conn.QuerySingleOrDefaultAsync<string?>(
        @"SELECT record_hash FROM dhf.entries
          WHERE session_id = @SessionId
          ORDER BY sequence_number DESC
          LIMIT 1",
        new { turn.SessionId },
        tx) ?? "GENESIS";

    var nextSeq = await conn.QuerySingleAsync<long>(
        @"SELECT COALESCE(MAX(sequence_number), 0) + 1
          FROM dhf.entries WHERE session_id = @SessionId",
        new { turn.SessionId },
        tx);

    // 2. Normalize tool call order ONCE.
    var ordered = turn.ToolCalls
        .OrderBy(tc => tc.SequenceInTurn)
        .Select((tc, idx) => tc with { SequenceInTurn = idx })
        .ToList();

    var toolCallsHash = HashCanonicalizer.ComputeToolCallsHash(ordered);

    // 3. Normalize timestamp.
    var normalizedTs = HashCanonicalizer.NormalizeTimestamp(turn.Timestamp);
    var canonicalTimestampStr = normalizedTs.ToString(
        "yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'", CultureInfo.InvariantCulture);

    // 4. Build entry record. recovery_context is NULL for non-recovery turns.
    var entry = new DhfEntryRecord
    {
        EntryId = turn.TurnId,
        SessionId = turn.SessionId,
        SequenceNumber = nextSeq,
        Timestamp = normalizedTs,
        CanonicalTimestamp = canonicalTimestampStr,
        InputModality = turn.InputModality.ToStorage(),
        RawTranscript = turn.RawTranscript,
        TranscriptConfidence = turn.TranscriptConfidence,
        TurnType = turn.TurnType.ToStorage(),
        UndoGroupId = turn.UndoGroupId,
        UndoOutcome = turn.UndoOutcome?.ToStorage(),
        DocumentRef = turn.DocumentRef?.Id,
        DocumentRevisionRef = turn.DocumentRevisionRef,
        FeatureTreeBefore = turn.FeatureTreeBefore,
        FeatureTreeAfter = turn.FeatureTreeAfter,
        FeatureTreeBeforeHash = turn.FeatureTreeBeforeHash,
        FeatureTreeAfterHash = turn.FeatureTreeAfterHash,
        ScreenshotBeforeSha256 = turn.ScreenshotBeforeSha256,
        ScreenshotAfterSha256 = turn.ScreenshotAfterSha256,
        ScreenshotBeforePng = turn.ScreenshotBeforePng,      // v1.5.0 — R3 H7
        ScreenshotAfterPng = turn.ScreenshotAfterPng,        // v1.5.0 — R3 H7
        RecoveryContext = null,                                          // v1.5.0 — non-recovery turn
        RecoveryContextHash = HashCanonicalizer.ComputeRecoveryContextHash(null), // v1.5.1 m1 — delegate to canonical helper
        PreviousHash = tipHash,
        ToolCallsHash = toolCallsHash,
        RecordHash = ""
    };

    // 5. Compute record hash from DhfHashColumns (which now includes recovery_context_hash).
    entry.RecordHash = HashCanonicalizer.ComputeRecordHash(entry, DhfHashColumns.HashedColumns);

    // 6. INSERT entry.
    await conn.ExecuteAsync(InsertEntrySql, entry, tx);

    // 7. INSERT tool call rows.
    foreach (var tc in ordered)
    {
        await conn.ExecuteAsync(InsertToolCallSql, MapToolCallRow(tc, entry.EntryId), tx);
    }

    _logger.LogInformation(
        "Appended entry {EntryId} (seq {Seq}, type {TurnType}) to session {SessionId} for tenant {TenantId}",
        entry.EntryId, entry.SequenceNumber, entry.TurnType, turn.SessionId, sessionRow.TenantId);

    await tx.CommitAsync(ct);
    return entry.EntryId;
}
```

Key properties:
- Session-open-and-unlocked guard inside the SERIALIZABLE transaction prevents TOCTOU races between `LockSessionAsync` and `AppendTurnAsync`.
- `SessionNotFoundException` thrown on unknown session_id; `SessionLockedException` thrown on locked session.
- AddinSessionId on every tool call validated against owning session.
- All enum values converted via `DhfEnumMappings.ToStorage()`.
- Timestamp normalized once; same value used for INSERT parameter and canonical hash input.
- `screenshot_before_png` / `screenshot_after_png` BYTEA columns populated from `ScreenshotBeforePng` / `ScreenshotAfterPng` (v1.5.0).
- `recovery_context` is explicitly NULL; `recovery_context_hash` is the sentinel `"NO_RECOVERY_CONTEXT"`.

### 7.2 AppendRecoveryEventAsync (NEW v1.5.0 — R3 C4)

Called by SPEC-03's recovery flow after a post-timeout or post-crash reconciliation completes. Writes a DHF entry with `turn_type = 'recovery'`, structured `recovery_context` JSONB, and optional screenshot BYTEA payloads.

```csharp
public async Task<Guid> AppendRecoveryEventAsync(
    Guid sessionId,
    RecoveryEventContext context,
    CancellationToken ct)
{
    const int maxRetries = 3;
    for (int attempt = 0; ; attempt++)
    {
        try
        {
            return await AppendRecoveryEventCore(sessionId, context, ct);
        }
        catch (PostgresException ex)
            when (ex.SqlState == PostgresErrorCodes.SerializationFailure
                && attempt < maxRetries - 1)
        {
            _logger.LogWarning(ex,
                "AppendRecoveryEvent attempt {Attempt} for session {SessionId} failed; retrying",
                attempt, sessionId);
            await Task.Delay(
                50 * (attempt + 1) + Random.Shared.Next(0, 50), ct);
        }
    }
}

private async Task<Guid> AppendRecoveryEventCore(
    Guid sessionId,
    RecoveryEventContext context,
    CancellationToken ct)
{
    if (context.SessionId != sessionId)
        throw new ArgumentException(
            "RecoveryEventContext.SessionId does not match sessionId parameter.",
            nameof(context));

    using var conn = await _dataSource.OpenConnectionAsync(ct);
    using var tx = await conn.BeginTransactionAsync(IsolationLevel.Serializable, ct);

    var lockKey = $"dhf_append:{sessionId}";
    await conn.ExecuteAsync(
        "SELECT pg_advisory_xact_lock(hashtext(@k))",
        new { k = lockKey },
        tx);

    // Verify session is open AND not locked. Same rules as AppendTurnCore.
    var sessionRow = await conn.QuerySingleOrDefaultAsync<SessionCheckRow>(
        @"SELECT ended_at IS NULL AS is_open,
                 locked_at IS NULL AS is_unlocked,
                 lock_reason,
                 addin_session_id,
                 tenant_id
          FROM dhf.sessions
          WHERE session_id = @SessionId",
        new { sessionId },
        tx)
        ?? throw new SessionNotFoundException(sessionId);

    if (!sessionRow.IsOpen)
        throw new InvalidOperationException(
            $"Session {sessionId} is closed; cannot append recovery event.");

    if (!sessionRow.IsUnlocked)
        throw new SessionLockedException(sessionId, sessionRow.LockReason ?? "unknown");

    // Read chain tip (same as for turn entries — shared chain).
    var tipHash = await conn.QuerySingleOrDefaultAsync<string?>(
        @"SELECT record_hash FROM dhf.entries
          WHERE session_id = @SessionId
          ORDER BY sequence_number DESC
          LIMIT 1",
        new { sessionId },
        tx) ?? "GENESIS";

    var nextSeq = await conn.QuerySingleAsync<long>(
        @"SELECT COALESCE(MAX(sequence_number), 0) + 1
          FROM dhf.entries WHERE session_id = @SessionId",
        new { sessionId },
        tx);

    // Normalize timestamp.
    var normalizedTs = HashCanonicalizer.NormalizeTimestamp(context.Timestamp);
    var canonicalTimestampStr = normalizedTs.ToString(
        "yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'", CultureInfo.InvariantCulture);

    // Build structured recovery_context JSONB.
    var recoveryContextJson = BuildRecoveryContextJson(context);

    // Compute recovery_context_hash via canonical hash of the JSONB content.
    var recoveryContextHash = HashCanonicalizer.ComputeRecoveryContextHash(recoveryContextJson);

    // Machine-generated raw_transcript for list-view rendering. Authoritative
    // structured data is in recovery_context.
    var rawTranscript =
        $"Recovery event: trigger={context.Trigger}, strategy={context.Strategy}, "
        + $"success={context.Success}";

    // Build entry record. Recovery entries have:
    //   - turn_type = 'recovery'
    //   - input_modality = 'text' (recovery events are orchestrator-generated, not user input)
    //   - undo_group_id, undo_outcome = NULL
    //   - recovery_context = non-null JSONB
    //   - zero tool_call rows (tool_calls_hash = "NO_TOOL_CALLS")
    var entry = new DhfEntryRecord
    {
        EntryId = context.RecoveryEventId,
        SessionId = sessionId,
        SequenceNumber = nextSeq,
        Timestamp = normalizedTs,
        CanonicalTimestamp = canonicalTimestampStr,
        InputModality = "text",
        RawTranscript = rawTranscript,
        TranscriptConfidence = null,
        TurnType = "recovery",                               // v1.5.0 — new turn_type
        UndoGroupId = null,
        UndoOutcome = null,
        DocumentRef = null,
        DocumentRevisionRef = null,
        FeatureTreeBefore = context.FeatureTreeBefore,
        FeatureTreeAfter = context.FeatureTreeAfter,
        FeatureTreeBeforeHash = context.FeatureTreeBeforeHash,
        FeatureTreeAfterHash = context.FeatureTreeAfterHash,
        ScreenshotBeforeSha256 = context.ScreenshotBeforeSha256,
        ScreenshotAfterSha256 = context.ScreenshotAfterSha256,
        ScreenshotBeforePng = context.ScreenshotBeforePng,   // R3 H7 — flows through
        ScreenshotAfterPng = context.ScreenshotAfterPng,     // R3 H7 — flows through
        RecoveryContext = recoveryContextJson,               // R3 H8 — structured, not free-text
        RecoveryContextHash = recoveryContextHash,
        PreviousHash = tipHash,
        ToolCallsHash = "NO_TOOL_CALLS",                     // zero child rows
        RecordHash = ""
    };

    // Compute record hash including recovery_context_hash per DhfHashColumns.
    entry.RecordHash = HashCanonicalizer.ComputeRecordHash(entry, DhfHashColumns.HashedColumns);

    // INSERT entry; no tool_call rows.
    await conn.ExecuteAsync(InsertEntrySql, entry, tx);

    _logger.LogInformation(
        "Appended recovery entry {EntryId} (seq {Seq}) to session {SessionId} for tenant {TenantId}: "
        + "trigger={Trigger}, strategy={Strategy}, success={Success}",
        entry.EntryId, entry.SequenceNumber, sessionId, sessionRow.TenantId,
        context.Trigger, context.Strategy, context.Success);

    await tx.CommitAsync(ct);
    return entry.EntryId;
}

private static JsonElement BuildRecoveryContextJson(RecoveryEventContext context)
{
    var obj = new
    {
        trigger = context.Trigger.ToString(),       // RecoveryTrigger serializes as PascalCase
        strategy = context.Strategy.ToString(),     // RecoveryStrategy serializes as PascalCase
        success = context.Success,
        failureReason = context.FailureReason,
        modelStateDivergenceDescription = context.ModelStateDivergenceDescription,
        precedingTurnEntryId = context.PrecedingTurnEntryId,
        referencedQuarantineLogIds = context.ReferencedQuarantineLogIds
    };

    var json = JsonSerializer.Serialize(obj, HashCanonicalizer.JsonOptions);
    return JsonDocument.Parse(json).RootElement.Clone();
}
```

Key properties:
- Recovery events share the same `dhf.entries` table, same sequence counter, same hash chain as turn entries (SDR-04-012).
- `previous_hash` is the prior entry's `record_hash` regardless of that entry's `turn_type` — the chain is continuous.
- Structured fields preserved in `recovery_context` JSONB (R3 H8); queryable via PostgreSQL JSONB operators.
- Screenshot bytes flow through `RecoveryEventContext` → BYTEA columns (R3 H7) — deep validation can rehash them.
- `tool_calls_hash` sentinel is `"NO_TOOL_CALLS"`; no child rows inserted.
- `SessionLockedException` thrown if session is locked — recovery events cannot be appended to a locked session any more than turn entries can.

## 8. DhfDecorationWriter (Stretch)

`DhfExtractor` calls Sonnet 4.6 asynchronously per turn to extract rationale, requirements, alternatives, risks from the transcript. Output is written as rows in `dhf.entry_decorations`. SPEC-07 (Vocabulary Layer) also writes decorations of type `vocabulary_resolution` via this writer — see §8.3.

```csharp
public async Task WriteDecorationAsync(
    Guid entryId,
    DecorationType type,
    JsonElement payload,
    string createdBy,
    CancellationToken ct,
    Guid? supersedesDecorationId = null)
{
    var hash = HashCanonicalizer.ComputeDecorationHash(payload);
    using var conn = await _dataSource.OpenConnectionAsync(ct);
    await conn.ExecuteAsync(
        @"INSERT INTO dhf.entry_decorations
          (decoration_id, entry_id, decoration_type, created_by,
           payload, decoration_hash, supersedes_decoration_id)
          VALUES (@Id, @EntryId, @Type, @CreatedBy,
                  @Payload, @Hash, @SupersedesId)",
        new {
            Id = Guid.NewGuid(),
            entryId,
            Type = type.ToStorage(),
            createdBy,
            Payload = payload,
            Hash = hash,
            SupersedesId = supersedesDecorationId
        });
}
```

Decoration failures never block the core pipeline. They're logged as `integrity_event` decorations with failure reason. When `supersedesDecorationId` is non-null, it references the `decoration_id` of the prior decoration being logically replaced.

### 8.1 Ambiguity Context Decoration

When SPEC-05 records an ambiguity resolution turn, it writes an `ambiguity_context` decoration with voice transcript, vision observations, resolution question, user response, and resolved entity reference. First-class auditable evidence that Opus 4.7 interpreted what the user meant.

### 8.2 Integrity Event Decoration (write-capable path only)

**Contract change in v1.5.0 (R3 H4):** `integrity_event` decorations are written ONLY via `IDhfRepository.RecordIntegrityEventAsync` — NOT via the read-only chain validator. The read-only `IReadOnlyDhfRepository.ValidateChainAsync` returns findings in-memory without persisting. Orchestrator code that wants to persist findings calls `ValidateChainAsync`, inspects the result, and then separately calls `RecordIntegrityEventAsync` for each finding it chooses to record. The two operations are decoupled.

Every `integrity_event` payload MUST contain a `dedupKey` string field that uniquely identifies the failure type and affected artifact for a given entry.

```json
{
  "eventType": "screenshot_hash_mismatch",
  "dedupKey": "screenshot_hash_mismatch:before",
  "detectedAt": "2026-04-28T14:33:02Z",
  "expectedSha256": "...",
  "actualSha256": "...",
  "orchestratorAction": "omitted_from_context"
}
```

Deep validation uses atomic deduplication via `INSERT ... ON CONFLICT DO NOTHING` against the partial unique index `idx_integrity_event_dedup`:

```csharp
// On IDhfRepository only — NOT on IReadOnlyDhfRepository.
public async Task RecordIntegrityEventAsync(
    Guid entryId,
    JsonElement payload,
    string createdBy,
    CancellationToken ct)
{
    var hash = HashCanonicalizer.ComputeDecorationHash(payload);
    using var conn = await _dataSource.OpenConnectionAsync(ct);
    await conn.ExecuteAsync(
        @"INSERT INTO dhf.entry_decorations
          (decoration_id, entry_id, decoration_type, created_by,
           payload, decoration_hash)
          VALUES (@Id, @EntryId, 'integrity_event', @CreatedBy,
                  @Payload, @Hash)
          ON CONFLICT (entry_id, (payload->>'dedupKey'))
              WHERE decoration_type = 'integrity_event'
                AND payload->>'dedupKey' IS NOT NULL
          DO NOTHING",
        new {
            Id = Guid.NewGuid(),
            entryId,
            createdBy,
            Payload = payload,
            Hash = hash
        });
}
```

Viewers surface `integrity_event` decorations as warnings on the relevant entry, but the viewer does not create them — only the orchestrator does.

### 8.3 Vocabulary Resolution Decoration (NEW v1.5.0 — SPEC-07 coordination)

SPEC-07 produces `vocabulary_resolution` decoration payloads after each user utterance is resolved against the CAD vocabulary corpus. The payload shape is owned by SPEC-07 (§8.2 of SPEC-07); SPEC-04 persists it verbatim as a decoration row.

```csharp
// SPEC-07 calls this via DhfDecorationWriter.
// type = DecorationType.VocabularyResolution → "vocabulary_resolution"
await decorationWriter.WriteDecorationAsync(
    entryId: currentTurnEntryId,
    type: DecorationType.VocabularyResolution,
    payload: vocabularyResolutionDecorationJson,   // shape defined in SPEC-07 §8.2
    createdBy: "BSVoiceCAD.Vocab",
    ct);
```

Migration 007 extends the `decoration_type` CHECK constraint to include `'vocabulary_resolution'`. The `DecorationType.VocabularyResolution` enum value and `ToStorage` mapping are added to `DhfEnumMappings` in v1.5.0.

## 9. Repository Surface

### 9.1 IDhfRepository (Full Read + Write)

The write-capable repository used by the orchestrator runtime. Consumers: orchestrator runtime, test code, admin tooling. **NOT registered in the viewer DI container.**

```csharp
public interface IDhfRepository
{
    // Read methods (mirrored in IReadOnlyDhfRepository with narrower DTOs)
    Task<IReadOnlyList<DhfSessionSummary>> ListSessionsAsync(string tenantId, int limit, int offset, CancellationToken ct);
    Task<DhfSessionDetail?> GetSessionAsync(Guid sessionId, CancellationToken ct);
    Task<IReadOnlyList<DhfEntrySummary>> ListEntriesAsync(Guid sessionId, CancellationToken ct);
    Task<DhfEntryDetail?> GetEntryAsync(Guid entryId, CancellationToken ct);
    Task<IReadOnlyList<ToolCallRow>> GetToolCallsAsync(Guid entryId, CancellationToken ct);
    Task<IReadOnlyList<DecorationRow>> GetDecorationsAsync(Guid entryId, CancellationToken ct);
    Task<ChainValidationResult> ValidateChainAsync(Guid sessionId, ChainValidationMode mode, CancellationToken ct);

    // Write methods (NOT in IReadOnlyDhfRepository)
    Task<Guid> AppendTurnAsync(DhfTurnContext turn, CancellationToken ct);
    Task<Guid> AppendRecoveryEventAsync(Guid sessionId, RecoveryEventContext context, CancellationToken ct);
    Task<Guid> OpenOrResumeSessionAsync(string addinSessionId, string tenantId, SessionMetadata metadata, CancellationToken ct);
    Task CloseSessionAsync(Guid sessionId, CancellationToken ct);
    Task LockSessionAsync(Guid sessionId, string lockReason, CancellationToken ct);           // v1.5.0
    Task RecordIntegrityEventAsync(Guid entryId, JsonElement payload, string createdBy, CancellationToken ct);  // v1.5.0

    // v1.5.2 H3 — cross-workstation locked-session pre-check. Returns any open
    // locked session matching (tenantId, addinSessionId) regardless of
    // workstation_id. Consumed by OpenOrResumeSessionCore to distinguish
    // same-workstation lock (SessionLockedException) from cross-workstation
    // lock (CrossWorkstationSessionLockedException).
    Task<LockedSessionRow?> FindLockedSessionAsync(
        string tenantId, string addinSessionId, CancellationToken ct);
}
```

`ListSessionsAsync` requires an explicit `tenantId`. Pagination: `limit` defaults to 50, max 200. Zero-based `offset`.

**Write-side DTOs (used by `IDhfRepository`):**

```csharp
public record DhfSessionSummary(
    Guid SessionId,
    DateTimeOffset StartedAt,
    DateTimeOffset? EndedAt,
    DateTimeOffset? LockedAt,                    // v1.5.0
    string? LockReason,                          // v1.5.0
    string UserIdentity,
    string? UserDisplayName,
    string WorkstationId,
    string AddinSessionId,
    string TenantId,
    int EntryCount);

public record DhfSessionDetail(
    Guid SessionId,
    DateTimeOffset StartedAt,
    DateTimeOffset? EndedAt,
    DateTimeOffset? LockedAt,                    // v1.5.0
    string? LockReason,                          // v1.5.0
    string UserIdentity,
    string? UserDisplayName,
    string WorkstationId,
    string SwVersion,
    string AddinVersion,
    string OrchestratorVersion,
    string ProtocolVersion,
    string AddinSessionId,
    string TenantId);

public record DhfEntrySummary(
    Guid EntryId,
    long SequenceNumber,
    DateTimeOffset Timestamp,
    string InputModality,
    string TurnType,              // may be 'recovery' (v1.5.0); consumers derive IsRecovery from (TurnType == "recovery")
    string RawTranscript,
    int ToolCallCount,
    bool HasDecorations);
// v1.5.1 m2: IsRecoveryEvent removed. Callers compute it from TurnType to
// avoid a parallel boolean that could drift from the enum.

public record DhfEntryDetail(
    Guid EntryId,
    Guid SessionId,
    long SequenceNumber,
    DateTimeOffset Timestamp,
    string InputModality,
    string RawTranscript,
    float? TranscriptConfidence,
    string TurnType,
    string? UndoGroupId,
    string? UndoOutcome,
    string? DocumentRef,
    string? DocumentRevisionRef,
    JsonElement? FeatureTreeBefore,
    JsonElement? FeatureTreeAfter,
    string FeatureTreeBeforeHash,
    string FeatureTreeAfterHash,
    string? ScreenshotBeforeSha256,
    string? ScreenshotAfterSha256,
    byte[]? ScreenshotBeforePng,                 // v1.5.0 — R3 C2
    byte[]? ScreenshotAfterPng,                  // v1.5.0 — R3 C2
    JsonElement? RecoveryContext,                // v1.5.0 — populated iff TurnType='recovery'
    string RecoveryContextHash,                  // v1.5.0 — "NO_RECOVERY_CONTEXT" for non-recovery
    string PreviousHash,
    string ToolCallsHash,
    string RecordHash);
```

`ToolCallRow` and `DecorationRow` are unchanged from v1.4.0.

### 9.2 IReadOnlyDhfRepository (Read-Only Projection)

Strictly side-effect-free. The interface SPEC-06 consumes; exposes no write surface. Per v1.5.0 R3 H4, `ValidateChainAsync` on this interface NEVER writes — it returns findings in-memory only.

```csharp
namespace BSVoiceCAD.Orchestrator.Dhf;

public interface IReadOnlyDhfRepository
{
    Task<IReadOnlyList<SessionSummary>> ListSessionsAsync(
        string tenantId,
        CancellationToken ct = default);

    Task<SessionSummary?> GetSessionAsync(
        Guid sessionId,
        CancellationToken ct = default);

    Task<IReadOnlyList<EntryListItem>> ListEntriesAsync(
        Guid sessionId,
        CancellationToken ct = default);

    Task<EntryDetail?> GetEntryDetailAsync(
        Guid entryId,
        CancellationToken ct = default);

    /// <summary>
    /// STRICTLY SIDE-EFFECT-FREE (v1.5.0). Never writes to any table, never emits
    /// decoration payloads, never triggers downstream persistence. Returns findings
    /// in-memory only. If operators want findings persisted, they must invoke
    /// IDhfRepository.RecordIntegrityEventAsync explicitly from orchestrator code —
    /// that method is NOT reachable from the viewer's DI scope.
    /// In Deep mode, acquires a repeatable-read snapshot for the session's entries
    /// to prevent mid-validation mutations from causing false tamper reports.
    /// </summary>
    Task<ChainValidationResult> ValidateChainAsync(
        Guid sessionId,
        ChainValidationMode mode,
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
    public string Detail { get; init; } = "";
    public string DedupKey { get; init; } = "";   // For operator-triggered RecordIntegrityEventAsync
}

public sealed record SessionSummary(
    Guid SessionId,
    DateTimeOffset StartedAt,
    DateTimeOffset? EndedAt,
    DateTimeOffset? LockedAt,                    // v1.5.0 — viewer displays lock banner
    string? LockReason,                          // v1.5.0
    string UserIdentity,
    string? UserDisplayName,
    string WorkstationId,
    string AddinSessionId,
    string TenantId,
    int EntryCount);

public sealed record EntryListItem(
    Guid EntryId,
    long SequenceNumber,
    DateTimeOffset Timestamp,
    string InputModality,
    string TurnType,              // may be 'recovery' (v1.5.0)
    string RawTranscript,         // truncated to 200 chars in list view
    int ToolCallCount,
    bool HasDecorations,
    string? UndoOutcome,          // v1.5.2 H6 — maps dhf.entries.undo_outcome; nullable
    string RecordHash,            // v1.5.2 H6 — hex-encoded; always populated
    string? PreviousHash);        // v1.5.2 H6 — hex-encoded; null for first entry in chain

// v1.5.2 H6: UndoOutcome / RecordHash / PreviousHash added to support
// SPEC-06 §4.3 response contract and timeline rendering. All three are
// existing columns on dhf.entries; the projection simply SELECTs them now.
// Binary hash columns are exposed as lowercase-hex strings to match the
// viewer's JSON rendering conventions. PreviousHash is nullable because
// the first entry in a chain uses `previous_hash = 'GENESIS'` — whether
// that sentinel is exposed as the literal string "GENESIS" or as null
// is a SPEC-06 rendering choice; SPEC-04's projection passes the raw
// stored value through unchanged for non-null rows and exposes null when
// the stored value is the 'GENESIS' sentinel, matching SPEC-06 §4.3.

public sealed record EntryDetail(
    Guid EntryId,
    Guid SessionId,
    long SequenceNumber,
    DateTimeOffset Timestamp,
    string InputModality,
    string RawTranscript,
    float? TranscriptConfidence,
    string TurnType,
    string? UndoGroupId,
    string? UndoOutcome,
    string? DocumentRef,
    string? DocumentRevisionRef,
    JsonElement? FeatureTreeBefore,
    JsonElement? FeatureTreeAfter,
    string FeatureTreeBeforeHash,
    string FeatureTreeAfterHash,
    string? ScreenshotBeforeSha256,
    string? ScreenshotAfterSha256,
    byte[]? ScreenshotBeforePng,                 // v1.5.0 — viewer renders as data URL
    byte[]? ScreenshotAfterPng,                  // v1.5.0
    JsonElement? RecoveryContext,                // v1.5.0 — populated iff TurnType='recovery'
    string PreviousHash,
    string ToolCallsHash,
    string RecordHash,
    IReadOnlyList<ToolCallRow> ToolCalls,
    IReadOnlyList<DecorationRow> Decorations);
```

**DTO naming discipline:** `SessionSummary` / `EntryListItem` / `EntryDetail` are intentionally distinct names from the write-side `DhfSessionSummary` / `DhfEntrySummary` / `DhfEntryDetail`. The read-only DTOs omit `ScreenshotBeforePath` / `ScreenshotAfterPath` (legacy filesystem artifacts) and include screenshot BYTEA directly for v1.5.0 viewer rendering. `RecoveryContextHash` is not exposed on read-side `EntryDetail` because the viewer does not re-verify the hash — the chain validator does that internally if deep mode is requested.

### 9.3 Exception Types

**`RepositoryUnavailableException`** (v1.4.0): thrown by repository methods when the underlying Postgres connection fails, is refused, or times out.

```csharp
public sealed class RepositoryUnavailableException : Exception
{
    public RepositoryUnavailableException(string message, Exception? innerException = null)
        : base(message, innerException) { }
}
```

**`SessionNotFoundException`** (v1.4.0): thrown when the session_id does not resolve.

```csharp
public sealed class SessionNotFoundException : Exception
{
    public Guid SessionId { get; }
    public SessionNotFoundException(Guid sessionId)
        : base($"Session {sessionId} does not exist.")
    {
        SessionId = sessionId;
    }
}
```

**`SessionLockedException`** (NEW v1.5.0 — R3 C1): thrown by `OpenOrResumeSessionCore`, `AppendTurnCore`, and `AppendRecoveryEventCore` when the target session is locked.

```csharp
public sealed class SessionLockedException : Exception
{
    public Guid SessionId { get; }
    public string LockReason { get; }

    public SessionLockedException(Guid sessionId, string lockReason)
        : base(
            $"DHF session {sessionId} is locked. Reason: {lockReason}. "
            + $"No further writes permitted. Operator intervention required — "
            + $"close the session via SQL and start a new session: "
            + $"UPDATE dhf.sessions SET ended_at = NOW() "
            + $"WHERE session_id = '{sessionId}' AND ended_at IS NULL;")
    {
        SessionId = sessionId;
        LockReason = lockReason;
    }
}
```

The lifecycle layer (SPEC-03) translates this exception into a user-facing agent-output-channel notification. Typical flow: SPEC-03's `AppendTurnAsync` caller catches `SessionLockedException`, surfaces a message via the agent channel ("This session has been locked due to a prior audit-log write failure. Please close SolidWorks and restart to begin a new session."), and refuses further turns until the add-in reconnects with a new AddinSessionId.

**`CrossWorkstationSessionLockedException`** (v1.5.2 H3 NEW): thrown by `OpenOrResumeSessionCore` when a same-tenant+AddinSessionId session is **locked on a different workstation** than the one attempting to open. Distinct from both `CrossWorkstationSessionConflictException` (which fires on an unlocked open session) and `SessionLockedException` (which fires on a same-workstation locked session). Closes the v1.5.1 gap where locked cross-workstation sessions were silently bypassed.

```csharp
public sealed class CrossWorkstationSessionLockedException : Exception
{
    public string AddinSessionId { get; }
    public string TenantId { get; }
    public Guid OtherSessionId { get; }
    public string OtherWorkstationId { get; }
    public string LockReason { get; }
    public string ThisWorkstationId { get; }

    public CrossWorkstationSessionLockedException(
        string addinSessionId,
        string tenantId,
        Guid otherSessionId,
        string otherWorkstationId,
        string lockReason,
        string thisWorkstationId)
        : base(
            $"AddinSessionId {addinSessionId} is LOCKED on workstation "
            + $"{otherWorkstationId} (session {otherSessionId}, reason: {lockReason}). "
            + $"Cannot open on workstation {thisWorkstationId}. "
            + $"Close the locked session via SQL; the add-in will generate a new "
            + $"AddinSessionId on next load.")
    {
        AddinSessionId = addinSessionId;
        TenantId = tenantId;
        OtherSessionId = otherSessionId;
        OtherWorkstationId = otherWorkstationId;
        LockReason = lockReason;
        ThisWorkstationId = thisWorkstationId;
    }
}
```

## 10. HashChainValidator

Two validation modes, invoked via either `IDhfRepository.ValidateChainAsync` or `IReadOnlyDhfRepository.ValidateChainAsync`. Both route to the same underlying implementation.

**Critical invariant (v1.5.0):** The implementation is PURE. It reads from the database, computes hashes, and returns a `ChainValidationResult` — it never writes. `IReadOnlyDhfRepository.ValidateChainAsync` exposes the pure path; `IDhfRepository.ValidateChainAsync` exposes the same pure path. Persistence of findings requires a separate call to `IDhfRepository.RecordIntegrityEventAsync` (§8.2).

### 10.1 Shallow Validation

Recomputes `record_hash` from stored column values, using `DhfHashColumns.HashedColumns`. Recomputes `tool_calls_hash` from stored tool-call rows. Does NOT recompute `feature_tree_*_hash` from JSONB, `screenshot_*_sha256` from bytes, or `recovery_context_hash` from JSONB. Fast; runs on every viewer page load. **Side-effect-free: findings returned in-memory.**

Timestamp re-canonicalization during shallow validation uses the same `HashCanonicalizer.NormalizeTimestamp` helper applied to the `DateTimeOffset` read back from Postgres.

### 10.2 Deep Validation

All shallow checks PLUS:
- Recomputes `feature_tree_before_hash` and `feature_tree_after_hash` from stored JSONB via `HashCanonicalizer.ComputeFeatureTreeHash`.
- Recomputes `screenshot_before_sha256` and `screenshot_after_sha256` from `screenshot_before_png` / `screenshot_after_png` BYTEA bytes (v1.5.0). If the BYTEA column is NULL and the SHA-256 column is non-NULL, a warning is added to the result (pre-migration-008 row, or intentional cleanup) but validity is unchanged. In v2 deployment (post-S3 migration), screenshots are fetched from S3 via presigned URLs and re-hashed.
- Recomputes `recovery_context_hash` from stored `recovery_context` JSONB (v1.5.0). Non-recovery entries have `recovery_context IS NULL` and the hash sentinel `"NO_RECOVERY_CONTEXT"`.

Missing screenshot bytes produce warnings, not failures. **Deep validation is side-effect-free in v1.5.0 — no `integrity_event` decorations are written during validation.** Findings are returned in-memory; the orchestrator optionally persists them via `RecordIntegrityEventAsync` after reviewing the result.

In Deep mode, the implementation acquires a `REPEATABLE READ` snapshot transaction to prevent mid-validation mutations from causing false tamper reports. The snapshot is read-only by isolation level; no rows are modified.

### 10.3 Results

The `ChainValidationResult` shape defined in §9.2 is the canonical return type for both modes. `Failures` list is the build-breaking signal; `Warnings` list is advisory. Each `ChainValidationFinding` includes a `DedupKey` string that the orchestrator can pass to `RecordIntegrityEventAsync` if it chooses to persist the finding.

ACC-02 (mandatory acceptance test, §13.1) passes if deep validation reports zero failures. Warnings do not fail the criterion.

## 11. Capture Rules (DHF-Level)

- **DHF-001** One entry per agent turn OR one entry per recovery event (v1.5.0).
- **DHF-002** Raw transcript is authoritative for user-initiated turns — never paraphrased. For recovery events, `raw_transcript` is machine-generated summary; authoritative structured data is in `recovery_context`.
- **DHF-003** Decoration is opportunistic — missing rationale extraction is not a failure.
- **DHF-004** Hash chain continuous within DHF session. First entry uses `previous_hash = 'GENESIS'`. Recovery entries participate in the same chain; `previous_hash` is the prior entry's `record_hash` regardless of that entry's `turn_type`.
- **DHF-005** Failed tool calls log rows with `success = false` or `NULL`.
- **DHF-006** Advisory lock scope is INSERT-only. Same advisory-lock key used by `AppendTurnAsync`, `AppendRecoveryEventAsync`, `CloseSessionAsync`, and `LockSessionAsync` (session-scoped).
- **DHF-007** Extraction writes to decorations, never mutates entries or tool_calls.
- **DHF-008** Non-executing turns still chain.
- **DHF-009** Ambiguity resolution is a dedicated turn_type.
- **DHF-010** Enum values use canonical storage mappings via `DhfEnumMappings.ToStorage()` (including v1.5.0 additions `TurnType.Recovery` and `DecorationType.VocabularyResolution`).
- **DHF-011** Session metadata is immutable once inserted (enforced by `guard_session_update`). v1.5.0 extends the trigger to permit `locked_at` / `lock_reason` NULL→non-NULL transitions alongside the existing `ended_at` transition; all other metadata remains immutable.
- **DHF-012** Integrity-event decorations use atomic deduplication via `dedupKey`. Written ONLY via `IDhfRepository.RecordIntegrityEventAsync`, never by the read-only chain validator (v1.5.0 R3 H4).
- **DHF-013** Cross-workstation session collision is a hard error (SDR-04-008, §6.3).
- **DHF-014 (v1.5.0 REWRITTEN — R3 C1)** DHF append failure locks the session DURABLY. If `DhfLedger.AppendTurnAsync` or `AppendRecoveryEventAsync` fails after all retries (persistent serialization failures, `RepositoryUnavailableException`, hash computation bug), the orchestrator MUST call `IDhfRepository.LockSessionAsync(sessionId, reason)` which persists `locked_at` and `lock_reason` on the session row. The lock SURVIVES orchestrator restart — `OpenOrResumeSessionCore` queries `locked_at IS NULL` and rejects locked sessions. No further turns or recovery events may be written to a locked session (TOCTOU-safe via SERIALIZABLE isolation + re-check inside the append transaction). The user is notified via the agent output channel. Reopening requires operator SQL: close the locked session and start a new one on reconnect. This invariant is enforced at the lifecycle layer (SPEC-03 seam C from SPEC-00). The DHF layer exposes the failure via `SessionLockedException` and the `LockSessionAsync` primitive.
- **DHF-015** Tenant scoping on every query. Architecture tests scan for `SELECT ... FROM dhf.sessions` that lacks a `tenant_id` filter.
- **DHF-016 (NEW v1.5.0 — R3 C4 / SDR-TL-013)** Recovery event persistence model. Recovery events are stored in `dhf.entries` with `turn_type = 'recovery'`, structured `recovery_context` JSONB, zero tool-call child rows, and `tool_calls_hash = "NO_TOOL_CALLS"`. They participate in the hash chain identically to turn entries. Invoked via `IDhfRepository.AppendRecoveryEventAsync`.
- **DHF-017 (NEW v1.5.0 — R3 H4)** Read-only chain validation is strictly side-effect-free. `IReadOnlyDhfRepository.ValidateChainAsync` MUST NOT write any rows under any mode. Persistence of integrity findings is the separate responsibility of `IDhfRepository.RecordIntegrityEventAsync`, which is not reachable from the viewer's DI scope.

## 12. Regulatory Framing Gap Analysis

Honest mapping per SDR-TL-006. DHF positioning is "design audit log foundation for future 21 CFR 820.30 / Part 11 alignment," not current compliance. Per SPEC-00 v1.5.0 SDR-TL-006 clarification: "foundation" means "a data structure that could be extended toward compliance in future work," NOT "a partial implementation of any compliance requirement."

| Requirement | BSVoice-CAD Coverage | Status |
|-------------|---------------------|--------|
| 21 CFR 820.30(a) Design plan | Out of scope — not a design-action artifact | Out-of-scope |
| 21 CFR 820.30(c) Design input tracking | Requirement linkages in decoration table (stretch) | Partial |
| 21 CFR 820.30(e) Design review records | Transcript + rationale captured | Partial (no formal review workflow) |
| 21 CFR 820.30(j) Design History File | Append-only hash-chained ledger of design actions including recovery events (v1.5.0) | Foundation (not certified compliant) |
| 21 CFR Part 11 §11.10 Closed-system controls | Hash chain + DB triggers + defense-in-depth + tenant scoping + durable session lock (v1.5.0) | Partial (orchestrator has DROP TRIGGER privilege; post-hackathon separation) |
| 21 CFR Part 11 §11.50 Signature manifestations | Windows user identity only | Out-of-scope |
| 21 CFR Part 11 §11.70 Signature/record linking | User identity stored per session, tenant-scoped | Partial |
| 21 CFR Part 11 §11.100–200 E-signatures | Not implemented | Out-of-scope |
| 21 CFR Part 11 §11.300 ID codes / passwords | Not implemented | Out-of-scope |
| Tenant isolation for multi-customer deployments | `tenant_id` column; every query filters by tenant; `TenantIsolationTests` verifies no cross-tenant leakage | Partial (v2: per-tenant schemas per migration doc §5) |
| Audit trail of post-failure session state (v1.5.0) | Durable session lock with reason; `DhfLedger.LockSessionAsync`; locked sessions cannot be re-used or silently bypassed | Partial foundation |
| Recovery event auditability (v1.5.0) | Structured `recovery_context` JSONB with trigger/strategy/success fields; hash chain includes recovery entries | Partial foundation |

Submission language MUST say "design audit log foundation" and MUST NOT say "FDA-compliant" or "21 CFR 820.30 compliant."

## 13. Required Tests (Build-Breaking)

All in `BSVoiceCAD.Orchestrator.Tests` unless noted. All use Testcontainers Postgres 16 to exercise real DB behavior.

- `DhfSessionManagerTests` — open new session, resume same AddinSessionId within tenant+workstation, resume after orchestrator restart, close prior workstation session on new load, retry on serialization failure / unique violation
- `CrossWorkstationConflictTests` — seed open session for tenant A on workstation X; attempt open from tenant A on workstation Y with same AddinSessionId → `CrossWorkstationSessionConflictException` with structured fields; no new session opened; no change to original session. Separately: attempt open from tenant B on workstation Y with same AddinSessionId → succeeds (different tenants don't conflict)
- **`SessionLockDurabilityTests` (NEW v1.5.0 — R3 C1)** — lock session via `LockSessionAsync`; simulate orchestrator restart by disposing all connections and reopening; verify `OpenOrResumeSessionAsync` throws `SessionLockedException` on the same AddinSessionId+workstation+tenant; verify the lock row persists in `dhf.sessions.locked_at`
- **`SessionLockAppendRejectionTests` (NEW v1.5.0)** — lock session; attempt `AppendTurnAsync` → `SessionLockedException`; attempt `AppendRecoveryEventAsync` → `SessionLockedException`; verify no rows inserted in `dhf.entries` or `dhf.tool_calls`
- **`SessionLockTOCTOUTests` (NEW v1.5.0)** — concurrent `LockSessionAsync` and `AppendTurnAsync` on same session; SERIALIZABLE isolation + advisory lock ensures exactly one outcome: either the append completes before the lock and the lock applies afterward, or the lock applies and the append throws `SessionLockedException`; neither operation is silently dropped
- **`SessionLockTriggerEnforcementTests` (NEW v1.5.0, extended v1.5.1 M2)** — direct UPDATE of `locked_at` from NULL→non-NULL with `lock_reason` accepted; UPDATE from non-NULL→NULL rejected by trigger; UPDATE of `lock_reason` once set rejected; UPDATE of `locked_at` without `lock_reason` rejected; **v1.5.1** UPDATE of `lock_reason` from NULL to non-NULL without also setting `locked_at` rejected by trigger (closes the `guard_session_update` gap described in v1.5.1 changelog M2)
- `DhfLedgerTests` — append turn, assert entry + tool_calls rows, hash chain valid, tool_calls_hash covers children, reject append to closed session, reject mismatched tool-call AddinSessionId, retry on serialization failure, `SessionNotFoundException` thrown on unknown session_id
- **`AppendRecoveryEventTests` (NEW v1.5.0 — R3 C4)** — append recovery event; verify row written with `turn_type = 'recovery'`, structured `recovery_context` JSONB populated with all fields, zero tool_call child rows, `tool_calls_hash = "NO_TOOL_CALLS"`, hash chain continues correctly from prior entry; verify recovery entry appears in `ListEntriesAsync` in correct sequence_number order
- **`RecoveryScreenshotBytesTests` (NEW v1.5.0 — R3 H7)** — append recovery event with `ScreenshotBeforePng` / `ScreenshotAfterPng` populated; query `dhf.entries` directly and verify BYTEA columns contain the expected bytes; verify SHA-256 of stored bytes matches `screenshot_before_sha256` / `screenshot_after_sha256`
- **`RecoveryContextStructuredFieldsTests` (NEW v1.5.0 — R3 H8)** — append recovery event with `RecoveryEventContext` populated; query `dhf.entries.recovery_context` via JSONB operators (`->>'trigger'`, `->>'strategy'`, `->>'success'`) and verify values are queryable as distinct fields, NOT collapsed into a single free-text string; verify `RawTranscript` contains a machine-generated summary and not the structured fields
- **`RecoveryContextHashCoverageTests` (NEW v1.5.0)** — mutate `recovery_context` JSONB directly via `DROP TRIGGER ... UPDATE ... RECREATE TRIGGER` sequence; `HashChainValidator.ValidateDeep` detects the mutation via `recovery_context_hash` mismatch
- `HashChainTests` — deliberate column mutation detected, deliberate JSONB mutation detected (deep), deliberate screenshot byte mutation detected (deep)
- **`ScreenshotBytesDeepValidationTests` (NEW v1.5.0 — R3 C2)** — append turn with `ScreenshotBeforePng` / `ScreenshotAfterPng` populated; deep validation re-hashes the BYTEA bytes and compares to `screenshot_*_sha256`; deliberate BYTEA mutation detected; NULL BYTEA with non-NULL SHA-256 produces warning (not failure)
- `HashChainConcurrencyTests` — 1000 concurrent appends via advisory lock, no forks, all valid
- **`RecoveryEventConcurrencyTests` (NEW v1.5.0)** — interleaved `AppendTurnAsync` and `AppendRecoveryEventAsync` calls on the same session; advisory lock serializes them; resulting chain has correct sequence numbering
- `ToolCallHashTests` — child row tamper detection, row reorder tamper detection
- `ImmutabilityTriggerTests` — UPDATE/DELETE on entries/tool_calls raises; UPDATE of session metadata (user_identity, workstation_id, sw_version, tenant_id, etc.) rejected; DELETE on sessions rejected; `ended_at`, `locked_at`, `lock_reason` NULL→non-NULL permitted (v1.5.0); `ended_at`, `locked_at` non-NULL→non-NULL rejected (v1.5.0); `lock_reason` mutation once set rejected (v1.5.0)
- `NonExecutingTurnTests` — clarification, confirmation, ambiguity_resolution turns produce correct rows; recovery turns produce correct rows (v1.5.0)
- `DecorationWriterTests` — ambiguity_context and integrity_event decorations written; supersede path; `IsSuperseded` in read path; atomic integrity_event dedup under concurrent writes
- **`VocabularyResolutionDecorationTests` (NEW v1.5.0 — SPEC-07 coordination)** — after migration 007, `DhfDecorationWriter.WriteDecorationAsync(entryId, DecorationType.VocabularyResolution, payload)` succeeds; `DecorationType.VocabularyResolution.ToStorage()` returns `"vocabulary_resolution"`; CHECK constraint accepts the new value
- **`RecordIntegrityEventSeparationTests` (NEW v1.5.0 — R3 H4)** — call `IReadOnlyDhfRepository.ValidateChainAsync` on a tampered session; verify `Failures.Count > 0`; verify `dhf.entry_decorations` has ZERO new `integrity_event` rows after the call (side-effect-free); separately call `IDhfRepository.RecordIntegrityEventAsync` for each finding; verify one `integrity_event` row per finding with matching `dedupKey`
- `DhfRepositoryTests` — read-only query paths, pagination, DTO field mapping, `IsSuperseded` computation, `LockedAt`/`LockReason` exposed on `DhfSessionSummary` and `DhfSessionDetail` (v1.5.0)
- `EnumMappingTests` — every enum value round-trips through `ToStorage()` and satisfies schema CHECK constraints, including `TurnType.Recovery` (v1.5.0) and `DecorationType.VocabularyResolution` (v1.5.0)
- `HashColumnSyncTests` — `DhfHashColumns` and `ToolCallHashColumns` cover all non-excluded columns including `recovery_context_hash` (v1.5.0); allowlist excluded
- `TimestampCanonTests` — `DateTimeOffset` → `NormalizeTimestamp` → Postgres `TIMESTAMPTZ` → read-back → `NormalizeTimestamp` → canonical string produces identical results
- `DbUpBootstrapTests` — migrations 001-009 execute successfully on a fresh database (v1.5.0: added 007, 008, 009)
- `CloseAppendCoordinationTests` — concurrent close and append on same session; no orphan entries in closed sessions
- `TenantIsolationTests` (lives in `BSVoiceCAD.Tests.Integration`) — seed sessions for tenants A and B with distinct tenant_id; assert `ListSessionsAsync(tenantA)` returns only tenant A's sessions; no information leakage across tenants
- `ReadOnlyRepositoryProjectionTests` — `DhfReadOnlyRepository` implements only `IReadOnlyDhfRepository`; write-capable methods are not reachable from a viewer-side handle
- `RepositoryUnavailableExceptionTests` — disconnected Npgsql connection causes repository methods to throw `RepositoryUnavailableException` with underlying exception chained
- `SessionNotFoundExceptionTests` — `ListEntriesAsync(unknownSessionId)` throws `SessionNotFoundException` with `SessionId` populated
- **`ValidateChainSideEffectFreeTests` (NEW v1.5.0 — R3 H4)** — call `IReadOnlyDhfRepository.ValidateChainAsync` in both Shallow and Deep modes on a seeded session with deliberate tampering; verify zero new rows in `dhf.entry_decorations` as a result; verify no write-capable repository method was invoked (via DI scope inspection if using a test harness that logs resolutions)
- `TenantScopedAdvisoryLockTests` — concurrent `OpenOrResumeSessionAsync` calls with same AddinSessionId across two tenants succeed independently
- `Migration006Tests` — fresh DB, migrations 001-006 execute successfully; tenant_id column NOT NULL with default 'unassigned'; partial unique index is tenant-scoped; `guard_session_update` rejects tenant_id mutation
- **`Migration007Tests` (NEW v1.5.0)** — fresh DB, migrations 001-007 execute successfully; `decoration_type` CHECK constraint accepts `'vocabulary_resolution'`; existing decoration types still accepted; unknown type rejected
- **`Migration008Tests` (NEW v1.5.0)** — fresh DB, migrations 001-008 execute successfully; `locked_at` and `lock_reason` columns added to `dhf.sessions` with NULL default; `screenshot_before_png` and `screenshot_after_png` BYTEA columns added to `dhf.entries`; `idx_sessions_open_addin_session` partial unique index rebuilt to include `locked_at IS NULL` predicate; `idx_sessions_locked` created; updated `guard_session_update` trigger permits `locked_at` / `lock_reason` NULL→non-NULL and rejects subsequent mutations
- **`Migration009Tests` (NEW v1.5.0)** — fresh DB, migrations 001-009 execute successfully; `turn_type` CHECK constraint accepts `'recovery'`; `recovery_context` JSONB column added to `dhf.entries`; `entries_recovery_context_iff_recovery_type` constraint rejects recovery-type entries with NULL `recovery_context` and non-recovery-type entries with non-NULL `recovery_context`
- **`HashAlgorithmVersionBackwardCompatTests` (stretch / post-hackathon, v1.5.1 M3)** — validates legacy hash compatibility strategy (per-version dispatch once `hash_algorithm_version` column is introduced). No-op placeholder for the hackathon; re-enable once the first production deployment commits to either the retire-legacy-rows path or the per-version-dispatch path documented in §4.2.

### 13.1 Acceptance Tests (exposed to SPEC-00 ACC table)

| ID | Criterion | Method |
|----|-----------|--------|
| ACC-02 | Per-turn DHF with correct child tool_calls; hash chain valid (deep) | `HashChainValidator.ValidateDeep` returns zero failures |
| ACC-12 | Advisory lock prevents hash fork under 1000 concurrent appends | `HashChainConcurrencyTests` |
| ACC-15 | AddinSessionId change triggers DHF session split, same ID resumes (including after orchestrator restart) | `DhfSessionManagerTests` integration test |
| ACC-16 | DB trigger rejects UPDATE/DELETE on entries, tool_calls, and session metadata (including tenant_id); permits `ended_at` and `locked_at` NULL→non-NULL | `ImmutabilityTriggerTests` |
| ACC-17 | tool_calls_hash detects child-row tampering | `ToolCallHashTests` |
| ACC-18 | Session metadata immutability: trigger permits only the three documented NULL→non-NULL transitions, rejects metadata mutation and DELETE | `ImmutabilityTriggerTests` |
| ACC-19 | Cross-workstation AddinSessionId conflict throws `CrossWorkstationSessionConflictException` with structured metadata; no session is opened or modified | `CrossWorkstationConflictTests` |
| **ACC-25** (NEW v1.5.0) | DHF session lock survives orchestrator restart; `OpenOrResumeSessionAsync` rejects locked sessions after restart | `SessionLockDurabilityTests` |
| **ACC-26** (NEW v1.5.0) | Recovery event persists structured `recovery_context` JSONB queryable by `trigger`, `strategy`, `success` fields; NOT collapsed to free-text | `RecoveryContextStructuredFieldsTests` |

## 14. Out of Scope

- Tool-call lifecycle state machine (states, transitions, timeouts, cancel, quarantine) → SPEC-03
- Construction of `DhfTurnContext` and `RecoveryEventContext` from lifecycle events → SPEC-03 (this spec defines the shape; SPEC-03 populates it)
- Policy decision for WHEN to call `LockSessionAsync` → SPEC-03 seam C (this spec provides the primitive)
- Agent loop, confirmation flow, vision context enrichment → SPEC-05
- Viewer UI rendering and endpoints → SPEC-06
- Voice transcription → SPEC-05
- Vocabulary bouncer, planner, synthesis → SPEC-07 (this spec only persists the resulting decoration payload)
- Per-tenant schema provisioning (v2) → `DEPLOYMENT_V1_V2_MIGRATION.md` §5
- OAuth tenant resolution (v2) → `DEPLOYMENT_V1_V2_MIGRATION.md` §2
- Programmatic unlock API → v1.5 post-hackathon (see migration 008 comments)

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*
