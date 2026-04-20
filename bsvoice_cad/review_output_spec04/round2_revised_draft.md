---
title: "BSVoice-CAD SPEC-04 — DHF Ledger"
subtitle: "Schema, hash model, append logic, AddinSessionId epoch, DHF session lifecycle"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-04 — DHF Ledger

**Version:** 1.2.0
**Status:** DRAFT — post-R2 revision
**Depends on:** SPEC-00 (module boundaries), SPEC-01 (wire types only — message shapes, `IToolInput`, `IToolResult`, `ToolError`, `StateSnapshot`, `FeatureNode`, `HashCanonicalizer`, `UndoGroupOutcome`, `ToolCallLifecycleState`)
**Consumed by:** SPEC-03 (writes entries), SPEC-06 (reads entries)

## 0. Scope

This spec defines everything about how voice-and-text turns become durable audit records. Schema, hash chain, append semantics, session lifecycle, epoch handling, session metadata protection, decoration extraction, integrity validation, canonical enum-to-storage mappings. Nothing about this spec depends on state machines, tool-call lifecycle, or agent logic — it receives complete `DhfTurnContext` objects from the outside and persists them.

**In scope:** Postgres 16 schema, hash canonicalization rules (including timestamp normalization and enum-to-storage mapping), per-turn append logic, AddinSessionId epoch and DHF session split/resume, session metadata immutability, decoration rows (including insert-with-supersede and integrity-event deduplication), immutability enforcement, integrity validation, read API and DTO contracts.

**Out of scope:** tool-call state machine (SPEC-03), agent confirmation flow (SPEC-05), viewer UI (SPEC-06), extraction LLM call shape (summarized here; full prompt in SPEC-05).

## 1. Project Shape

```
BSVoiceCAD.Orchestrator.Dhf/
├── DhfLedger.cs                             # AppendTurnAsync; sole writer of dhf.entries + dhf.tool_calls
├── DhfSessionManager.cs                     # Open/resume/close dhf.sessions rows
├── DhfDecorationWriter.cs                   # Async writer for dhf.entry_decorations
├── DhfRepository.cs                         # Read-only queries for viewer + integrity
├── DhfTurnContext.cs                        # Input DTO consumed by AppendTurnAsync
├── DhfEnumMappings.cs                       # Canonical enum-to-storage string mappings
├── HashChainValidator.cs                    # Entry-chain and tool-calls-hash verification
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
    └── 005_immutability_triggers.sql
```

## 2. Design Decisions (Local SDRs)

**SDR-04-001: Per-turn entry + child rows.** One row in `dhf.entries` per agent turn. Zero-or-more child rows in `dhf.tool_calls` per entry. Locked. Oracle rejected every other granularity across 10 rounds of v0.3–v0.5 review; this is the stable model.

**SDR-04-002: Immutable entries and tool calls; insert-with-supersede decorations.** `dhf.entries` and `dhf.tool_calls` are append-only, enforced at the database level by triggers (see Section 6). `dhf.entry_decorations` uses an insert-with-supersede model: new decoration rows may reference `supersedes_decoration_id` to logically replace a prior decoration, but the prior row is never mutated or deleted by application code. The DB does NOT enforce decoration immutability with triggers because post-hackathon operational cleanup (orphan removal, GDPR erasure) may require administrative DELETE. Application code MUST NOT issue UPDATE or DELETE against `dhf.entry_decorations` during normal operation; this invariant is enforced by architecture tests. This distinction exists so the LLM-extracted rationale doesn't invalidate the core integrity chain.

**SDR-04-003: Two-level hash coverage.** The entry's `record_hash` covers both the entry's own frozen columns AND a canonical `tool_calls_hash` that in turn covers all child rows for that entry. A tamper attempt on a child row changes the `tool_calls_hash`, which changes the `record_hash`, which breaks the chain. This is necessary because in v0.5 Oracle correctly identified that child rows had no cryptographic protection.

**SDR-04-004: DHF session split on AddinSessionId change.** Every new `AddinSessionId` (UUID from add-in load) opens a new `dhf.sessions` row. Prior session's `ended_at` is set at the split moment. No entry or tool call spans two sessions. Clean ordering, clean epoch semantics. Dude's call, locked.

**SDR-04-005: Same-AddinSessionId reconnect resumes the prior session.** If the orchestrator reconnects to an add-in whose `AddinSessionId` matches an already-open `dhf.sessions` row, it resumes that session (appends to its chain) rather than opening a new row. The AddinSessionId (generated by the add-in on load) is the epoch authority, not the orchestrator's process lifecycle. Consequently, the orchestrator does NOT close the DHF session on graceful shutdown — the add-in may still be running and will send the same `AddinSessionId` on reconnect. Sessions are closed by: (a) explicit add-in disconnect, (b) session split when a new `AddinSessionId` arrives, or (c) workstation-level cleanup in `OpenOrResumeSessionCore`. Sessions left open by orchestrator shutdown are cleaned up lazily on the next `OpenOrResumeSessionAsync` call for the same workstation.

**SDR-04-006: Advisory lock per DHF session during append.** Concurrency safety via Postgres `pg_advisory_xact_lock`. Lock scope narrowed to the INSERT transaction; held during tip-read → hash-compute → entry+tool_calls INSERT, released at transaction commit. No locks held during Claude calls or hash computation outside the transaction.

**SDR-04-007: No SQLite fallback.** Docker Postgres 16 is default. EmbeddedPostgres (`MysticMind.PostgresEmbed`) is the only demo-day fallback. SQLite requires schema variants, lacks advisory locks, lacks JSONB, and cannot provide append-only trigger enforcement the same way. Locked.

**SDR-04-008: Workstation_id is a trust boundary for session isolation.** The close-other-sessions logic and advisory lock scoping rely on `workstation_id` to isolate sessions per physical machine. This assumes `workstation_id` is a stable, non-spoofed machine identifier (e.g., `Environment.MachineName`). In hackathon scope, this is acceptable because only one orchestrator instance runs per workstation. Post-hackathon: evaluate stronger machine fingerprinting if multi-tenant deployment is required. A spoofed or colliding `workstation_id` can cause one orchestrator to prematurely close another's sessions.

**SDR-04-009: Canonical enum-to-storage mappings via explicit switch expressions.** All enums persisted to Postgres (`InputModality`, `TurnType`, `UndoGroupOutcome`, `ToolCallLifecycleState`, `DecorationType`) MUST be converted through `DhfEnumMappings` — never via `.ToString()` or `.ToLowerInvariant()`. This ensures C# PascalCase enum member names produce the lowercase/snake_case literals required by schema CHECK constraints. Architecture tests verify every enum value round-trips correctly.

**SDR-04-010: Session metadata immutability via DB trigger.** All columns in `dhf.sessions` except `ended_at` are immutable once inserted. A DB trigger (`guard_session_update`) permits only the `ended_at` column to transition from NULL to a non-NULL value; all other UPDATE attempts are rejected. DELETE on `dhf.sessions` is also rejected by trigger. This prevents post-hoc rewriting of user attribution, workstation identity, or software version metadata. Post-hackathon: compute a `session_metadata_hash` at INSERT time for cryptographic proof of original metadata (currently, the trigger provides DB-level protection but session metadata is not covered by the entry hash chain).

## 3. Schema

### 3.1 Migrations

Executed in strict numerical order by DbUp at orchestrator startup. DbUp journals to `public.dbup_journal` (its default table in the `public` schema), avoiding a bootstrap dependency on the `dhf` schema that has not yet been created. The `dhf` schema is created by migration 001.

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
-- addin_session_id after the prior one is closed. If you have an open
-- session with a given addin_session_id, you cannot open another.
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

    -- Turn classification
    turn_type                 TEXT NOT NULL
        CHECK (turn_type IN ('execution', 'clarification', 'confirmation', 'ambiguity_resolution')),

    -- Action
    undo_group_id             TEXT NULL,                 -- NULL for non-execution turns
    undo_outcome              TEXT NULL
        CHECK (undo_outcome IS NULL OR undo_outcome IN ('committed', 'rolled_back', 'abandoned')),

    -- State references
    document_ref              TEXT NULL,
    document_revision_ref     TEXT NULL,
    feature_tree_before       JSONB NULL,
    feature_tree_after        JSONB NULL,
    feature_tree_before_hash  TEXT NOT NULL,             -- 'NO_DOCUMENT' sentinel permitted
    feature_tree_after_hash   TEXT NOT NULL,
    screenshot_before_path    TEXT NULL,
    screenshot_after_path     TEXT NULL,
    screenshot_before_sha256  TEXT NULL,
    screenshot_after_sha256   TEXT NULL,

    -- Integrity
    previous_hash             TEXT NOT NULL,             -- 'GENESIS' for first entry in a DHF session
    tool_calls_hash           TEXT NOT NULL,             -- Canonical hash over child rows (Section 4.3)
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
    success                 BOOLEAN NULL,                -- NULL for non-completed terminals
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

-- Atomic deduplication for integrity_event decorations (R2-ISSUE-004).
-- The payload MUST contain a 'dedupKey' string that uniquely identifies the
-- failure type + affected artifact for a given entry (e.g., 'screenshot_hash_mismatch:before').
-- INSERT ... ON CONFLICT DO NOTHING uses this index to prevent duplicate events.
CREATE UNIQUE INDEX idx_integrity_event_dedup
    ON dhf.entry_decorations (entry_id, (payload->>'dedupKey'))
    WHERE decoration_type = 'integrity_event'
      AND payload->>'dedupKey' IS NOT NULL;
```

```sql
-- 005_immutability_triggers.sql

-- Generic reject function for append-only tables.
CREATE OR REPLACE FUNCTION dhf.reject_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'dhf.% is append-only; % is not permitted',
        TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

-- Session-specific guard: only ended_at may change, only NULL -> non-NULL (R2-ISSUE-001).
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

-- Sessions: metadata immutable, only ended_at close permitted, no deletes.
CREATE TRIGGER dhf_sessions_guard_update
    BEFORE UPDATE ON dhf.sessions
    FOR EACH ROW EXECUTE FUNCTION dhf.guard_session_update();

CREATE TRIGGER dhf_sessions_no_delete
    BEFORE DELETE ON dhf.sessions
    FOR EACH ROW EXECUTE FUNCTION dhf.reject_mutation();

-- Entries: fully append-only.
CREATE TRIGGER dhf_entries_no_update
    BEFORE UPDATE ON dhf.entries
    FOR EACH ROW EXECUTE FUNCTION dhf.reject_mutation();

CREATE TRIGGER dhf_entries_no_delete
    BEFORE DELETE ON dhf.entries
    FOR EACH ROW EXECUTE FUNCTION dhf.reject_mutation();

-- Tool calls: fully append-only.
CREATE TRIGGER dhf_tool_calls_no_update
    BEFORE UPDATE ON dhf.tool_calls
    FOR EACH ROW EXECUTE FUNCTION dhf.reject_mutation();

CREATE TRIGGER dhf_tool_calls_no_delete
    BEFORE DELETE ON dhf.tool_calls
    FOR EACH ROW EXECUTE FUNCTION dhf.reject_mutation();

-- dhf.entry_decorations: application code uses insert-with-supersede (SDR-04-002).
-- No DB-level trigger because post-hackathon operational cleanup may require
-- administrative DELETE. Application-level invariant enforced by architecture tests.
```

### 3.2 Enforcement Gap (Honest Disclosure)

DB-level triggers prevent unauthorized mutation across all core tables:

- **`dhf.sessions`**: `guard_session_update` permits only `ended_at` NULL→non-NULL transitions; rejects all other column changes. `reject_mutation` prevents DELETE. This protects user attribution, workstation identity, and version metadata from post-hoc rewriting.
- **`dhf.entries`** and **`dhf.tool_calls`**: `reject_mutation` prevents all UPDATE and DELETE.
- **`dhf.entry_decorations`**: No DB trigger — see SDR-04-002.

However, the orchestrator also has `DROP TRIGGER` privilege (it's the only DB user in hackathon scope). A malicious or buggy orchestrator could drop triggers, mutate rows, and recreate triggers. This is acknowledged in SDR-TL-006 (SPEC-00) and in Section 12 of this spec's integrity-validation discussion. Post-hackathon: separate DBA and application roles.

Additionally, session metadata is not currently covered by the entry hash chain — entries reference `session_id` as a FK but do not hash session-level fields. The trigger prevents mutation, but a successful trigger bypass (via DROP TRIGGER) could alter session metadata without invalidating any entry hashes. Post-hackathon: compute `session_metadata_hash` at INSERT time and anchor it in the first entry's hash computation.

For `dhf.entry_decorations`, no DB trigger is installed because the insert-with-supersede model (SDR-04-002) may require post-hackathon administrative cleanup. The application-level invariant (no UPDATE/DELETE in normal operation) is enforced by architecture tests that scan for prohibited SQL patterns.

For hackathon credibility, the framing is "defense-in-depth append-only enforcement" — application code never attempts unauthorized mutation, architecture tests forbid it in code, AND the database raises exceptions on attempts against sessions, entries, and tool_calls.

## 4. Hash Model

### 4.1 HashCanonicalizer Rules (Imported from SPEC-01)

- `System.Text.Json` with fixed options: camelCase property names, PascalCase enum strings, `decimal` serialized "G29" invariant-culture, null-omitting, sorted object keys.
- SHA-256 of UTF-8 canonical JSON, output as lowercase hex.
- `"NO_DOCUMENT"` is a literal sentinel string for null feature-tree states, not a hash of anything.
- `DateTimeOffset` values MUST be normalized to UTC and truncated to microsecond precision (6 decimal places) before hashing. Canonical format: `yyyy-MM-ddTHH:mm:ss.ffffffZ` via `value.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'", CultureInfo.InvariantCulture)`. The same normalized `DateTimeOffset` value MUST be used for both the INSERT parameter and the hash input. Postgres `TIMESTAMPTZ` stores microsecond precision, so the round-trip is lossless when this rule is followed.
- Enum values in hash inputs MUST use the storage-form string from `DhfEnumMappings` (Section 4.5), NOT `.ToString()`.

Timestamp normalization helper (used by `AppendTurnAsync` and `HashChainValidator`):

```csharp
/// <summary>
/// Normalizes a DateTimeOffset to UTC with microsecond precision.
/// Truncates sub-microsecond ticks to ensure lossless Postgres TIMESTAMPTZ round-trip.
/// </summary>
public static DateTimeOffset NormalizeTimestamp(DateTimeOffset value)
{
    var utc = value.ToUniversalTime();
    var truncatedTicks = utc.Ticks - (utc.Ticks % 10); // 1 tick = 100ns; 10 ticks = 1μs
    return new DateTimeOffset(truncatedTicks, TimeSpan.Zero);
}
```

### 4.2 DhfHashColumns (Entry-Level)

`BSVoiceCAD.Protocol/Serialization/DhfHashColumns.cs`. Ordered list. This is THE exact input to `record_hash` computation.

```csharp
public static class DhfHashColumns
{
    public static readonly IReadOnlyList<string> HashedColumns = new[]
    {
        "entry_id",
        "session_id",
        "sequence_number",
        "timestamp",
        "input_modality",
        "raw_transcript",
        "transcript_confidence",
        "turn_type",
        "undo_group_id",
        "undo_outcome",
        "document_ref",
        "document_revision_ref",
        "feature_tree_before_hash",
        "feature_tree_after_hash",
        "screenshot_before_path",
        "screenshot_after_path",
        "screenshot_before_sha256",
        "screenshot_after_sha256",
        "previous_hash",
        "tool_calls_hash"
    };
}
```

Notes:
- `feature_tree_before` and `feature_tree_after` (the raw JSONB blobs) are NOT in the hash input. Their hashes (`_hash` columns) are. This decouples JSONB round-trip behavior from chain integrity.
- `record_hash` is NOT in the hash input (it's the output).

### 4.3 ToolCallHashColumns (Child-Row Level)

`BSVoiceCAD.Protocol/Serialization/ToolCallHashColumns.cs`. Ordered list.

```csharp
public static class ToolCallHashColumns
{
    public static readonly IReadOnlyList<string> HashedColumns = new[]
    {
        "sequence_in_turn",
        "call_message_id",
        "tool_name",
        "inputs",
        "result",
        "error_code",
        "error_message",
        "success",
        "lifecycle_state",
        "is_internal",
        "retry_of_tool_call_id",
        "retry_depth",
        "execute_ms",
        "rebuild_ms",
        "total_ms",
        "state_version_before",
        "state_version_after",
        "addin_session_id"
    };
}
```

Notes:
- `tool_call_id` (UUID PK) and `entry_id` (parent FK) are NOT included — they're implementation detail of row storage, not semantic content.
- Child rows are canonicalized in `sequence_in_turn` ascending order before hashing.

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

Empty tool-call sets (e.g., `clarification` or `confirmation` turns) produce the sentinel `"NO_TOOL_CALLS"` instead of a hash of an empty array, for readability in logs.

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
        _ => throw new ArgumentOutOfRangeException(nameof(d))
    };
}
```

## 5. DhfTurnContext (Input DTO)

The DHF layer never constructs turn records itself. SPEC-03 (the tool-call lifecycle layer) assembles a complete `DhfTurnContext` and hands it in.

```csharp
public record DhfTurnContext
{
    public required Guid TurnId { get; init; }              // Used as entry_id
    public required Guid SessionId { get; init; }           // Resolved by DhfSessionManager
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
    public required string FeatureTreeBeforeHash { get; init; }  // 'NO_DOCUMENT' sentinel allowed
    public required string FeatureTreeAfterHash { get; init; }
    public string? ScreenshotBeforePath { get; init; }
    public string? ScreenshotAfterPath { get; init; }
    public string? ScreenshotBeforeSha256 { get; init; }
    public string? ScreenshotAfterSha256 { get; init; }
    public required IReadOnlyList<ToolCallRecord> ToolCalls { get; init; }
}

public record ToolCallRecord
{
    public required Guid ToolCallId { get; init; }
    public required int SequenceInTurn { get; init; }            // Will be normalized on append
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
public enum TurnType { Execution, Clarification, Confirmation, AmbiguityResolution }
public enum DecorationType { RationaleExtraction, ManualAnnotation, AmbiguityContext, IntegrityEvent }
```

`ToolCallLifecycleState` and `UndoGroupOutcome` are defined in SPEC-01 (cross-process concepts).

## 6. DhfSessionManager

### 6.1 Open or Resume

Called by the orchestrator on every `AddinReadyMessage`. Returns the `SessionId` that subsequent `AppendTurnAsync` calls should use.

The method is wrapped in a bounded retry loop with jitter to handle serialization failures (`SQLSTATE 40001`) and unique-index violations (`SQLSTATE 23505`) that can occur under concurrent startup.

```csharp
public async Task<Guid> OpenOrResumeSessionAsync(
    string addinSessionId,
    SessionMetadata metadata,     // user, workstation, versions, protocol_version
    CancellationToken ct)
{
    const int maxRetries = 3;
    for (int attempt = 0; ; attempt++)
    {
        try
        {
            return await OpenOrResumeSessionCore(addinSessionId, metadata, ct);
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
    }
}

private async Task<Guid> OpenOrResumeSessionCore(
    string addinSessionId,
    SessionMetadata metadata,
    CancellationToken ct)
{
    using var conn = await _dataSource.OpenConnectionAsync(ct);
    using var tx = await conn.BeginTransactionAsync(IsolationLevel.Serializable, ct);

    // Advisory lock scoped to workstation only. This serializes ALL session
    // open/resume/close decisions for this workstation, matching the scope
    // of the UPDATE that closes prior sessions. (R1-ISSUE-004)
    var lockKey = $"dhf_session_open:{metadata.WorkstationId}";
    await conn.ExecuteAsync(
        "SELECT pg_advisory_xact_lock(hashtext(@k))",
        new { k = lockKey },
        tx);

    // Look up any existing open session with this addin_session_id.
    // No workstation filter here: the partial unique index is on
    // (addin_session_id) WHERE ended_at IS NULL, so the lookup scope
    // must match to correctly handle unique-violation recovery. (R1-ISSUE-005)
    var existing = await conn.QuerySingleOrDefaultAsync<SessionLookupRow>(
        @"SELECT session_id, workstation_id FROM dhf.sessions
          WHERE addin_session_id = @AddinSessionId
            AND ended_at IS NULL",
        new { addinSessionId },
        tx);

    if (existing is not null)
    {
        if (existing.WorkstationId == metadata.WorkstationId)
        {
            // Same AddinSessionId, same workstation, still open → resume.
            await tx.CommitAsync(ct);
            return existing.SessionId;
        }

        // Same AddinSessionId on a DIFFERENT workstation. This is a stale
        // session or an ID collision. Close the stale session before opening
        // a new one. Log a warning — this is unexpected in normal operation.
        _logger.LogWarning(
            "Closing stale session {SessionId} on workstation {OtherWorkstation} "
            + "for addinSessionId {AddinSessionId}; new session opening on {ThisWorkstation}",
            existing.SessionId, existing.WorkstationId,
            addinSessionId, metadata.WorkstationId);

        await conn.ExecuteAsync(
            @"UPDATE dhf.sessions SET ended_at = NOW()
              WHERE session_id = @SessionId AND ended_at IS NULL",
            new { existing.SessionId },
            tx);
    }

    // Close any other open sessions on THIS workstation.
    await conn.ExecuteAsync(
        @"UPDATE dhf.sessions
          SET ended_at = NOW()
          WHERE ended_at IS NULL
            AND workstation_id = @WorkstationId",
        new { metadata.WorkstationId },
        tx);

    // Open a new session.
    var newId = Guid.NewGuid();
    await conn.ExecuteAsync(
        @"INSERT INTO dhf.sessions
          (session_id, user_identity, user_display_name, workstation_id,
           sw_version, addin_version, orchestrator_version, protocol_version,
           addin_session_id)
          VALUES (@NewId, @User, @DisplayName, @WorkstationId,
                  @SwVersion, @AddinVersion, @OrchVersion, @ProtocolVersion,
                  @AddinSessionId)",
        new
        {
            NewId = newId,
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

The advisory lock serializes all session decisions per workstation. The partial unique index (`idx_sessions_open_addin_session`) is the cross-workstation safety net: if two workstations race to open a session with the same `addin_session_id`, the loser gets a unique-violation that triggers a bounded retry. On retry, the lookup finds the winner's open session and handles it (close-stale-and-reopen or resume).

### 6.2 Close on Add-in Disconnect

`DhfSessionManager.CloseSessionAsync(sessionId)` sets `ended_at = NOW()`. Called when the add-in sends a disconnect message or when the orchestrator detects add-in connection loss. **NOT called on graceful orchestrator shutdown** — see SDR-04-005. Sessions left open by orchestrator shutdown are cleaned up lazily by `OpenOrResumeSessionCore` when the next session is opened on the same workstation.

`CloseSessionAsync` acquires the per-session advisory lock to coordinate with in-flight `AppendTurnAsync` operations (R2-ISSUE-005). This ensures a close cannot race with an append on the same session.

```csharp
public async Task CloseSessionAsync(Guid sessionId, CancellationToken ct)
{
    using var conn = await _dataSource.OpenConnectionAsync(ct);
    using var tx = await conn.BeginTransactionAsync(IsolationLevel.Serializable, ct);

    // Acquire the same per-session lock as AppendTurnAsync to prevent
    // close-during-append races. (R2-ISSUE-005)
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

Idempotent: if the session is already closed, the UPDATE matches zero rows (the `guard_session_update` trigger does not fire because no row is updated).

## 7. DhfLedger.AppendTurnAsync

The ONLY writer of `dhf.entries` and `dhf.tool_calls`.

Wrapped in a bounded retry loop with jitter for `SerializationFailure` (`SQLSTATE 40001`), which can occur when a concurrent session close or another append conflicts under Serializable isolation (R2-ISSUE-005, R2-ISSUE-006).

```csharp
public async Task<Guid> AppendTurnAsync(DhfTurnContext turn, CancellationToken ct)
{
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

    // 0. Verify session is open and fetch addin_session_id for epoch validation.
    //    (R1-ISSUE-003: guard against writes to closed sessions.
    //     R1-ISSUE-008: validate tool-call AddinSessionId against session.)
    var sessionRow = await conn.QuerySingleOrDefaultAsync<SessionCheckRow>(
        @"SELECT ended_at IS NULL AS is_open, addin_session_id
          FROM dhf.sessions WHERE session_id = @SessionId",
        new { turn.SessionId },
        tx)
        ?? throw new InvalidOperationException(
            $"Session {turn.SessionId} does not exist.");

    if (!sessionRow.IsOpen)
        throw new InvalidOperationException(
            $"Session {turn.SessionId} is closed; cannot append.");

    foreach (var tc in turn.ToolCalls)
    {
        if (tc.AddinSessionId != sessionRow.AddinSessionId)
            throw new InvalidOperationException(
                $"Tool call {tc.ToolCallId} AddinSessionId '{tc.AddinSessionId}' "
                + $"does not match session '{sessionRow.AddinSessionId}'.");
    }

    // 1. Read chain tip
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

    // 2. Normalize tool call order ONCE. Same list is used for both hash
    //    computation and INSERT; sequence_in_turn is reassigned to contiguous
    //    0-based indices derived from that single ordering.
    var ordered = turn.ToolCalls
        .OrderBy(tc => tc.SequenceInTurn)
        .Select((tc, idx) => tc with { SequenceInTurn = idx })
        .ToList();

    var toolCallsHash = HashCanonicalizer.ComputeToolCallsHash(ordered);

    // 3. Normalize timestamp to canonical UTC microsecond precision (Section 4.1).
    //    This single normalized value is used for BOTH the DB INSERT parameter and
    //    the hash input, ensuring the Postgres TIMESTAMPTZ round-trip produces the
    //    same bytes that were hashed. (R1-ISSUE-012, R2 residual fix)
    var normalizedTs = HashCanonicalizer.NormalizeTimestamp(turn.Timestamp);
    var canonicalTimestampStr = normalizedTs.ToString(
        "yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'", CultureInfo.InvariantCulture);

    // 4. Build entry record using DhfEnumMappings for all enum fields (SDR-04-009).
    var entry = new DhfEntryRecord
    {
        EntryId = turn.TurnId,
        SessionId = turn.SessionId,
        SequenceNumber = nextSeq,
        Timestamp = normalizedTs,                        // Normalized — used for DB INSERT
        CanonicalTimestamp = canonicalTimestampStr,       // Same value as string — used for hash input
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
        ScreenshotBeforePath = turn.ScreenshotBeforePath,
        ScreenshotAfterPath = turn.ScreenshotAfterPath,
        ScreenshotBeforeSha256 = turn.ScreenshotBeforeSha256,
        ScreenshotAfterSha256 = turn.ScreenshotAfterSha256,
        PreviousHash = tipHash,
        ToolCallsHash = toolCallsHash,
        RecordHash = ""  // Filled next
    };

    // 5. Compute record hash from DhfHashColumns over the entry record
    entry.RecordHash = HashCanonicalizer.ComputeRecordHash(entry, DhfHashColumns.HashedColumns);

    // 6. INSERT entry
    await conn.ExecuteAsync(InsertEntrySql, entry, tx);

    // 7. INSERT tool call rows in the same ordered sequence.
    //    MapToolCallRow uses DhfEnumMappings.ToStorage() for lifecycle_state.
    foreach (var tc in ordered)
    {
        await conn.ExecuteAsync(InsertToolCallSql, MapToolCallRow(tc, entry.EntryId), tx);
    }

    await tx.CommitAsync(ct);
    return entry.EntryId;
}
```

Key properties:
- Session-open guard prevents writes to closed or nonexistent sessions (R1-ISSUE-003).
- AddinSessionId on every tool call is validated against the owning session (R1-ISSUE-008).
- All enum values are converted via `DhfEnumMappings.ToStorage()`, producing the exact lowercase/snake_case literals required by schema CHECK constraints (R1-ISSUE-001).
- Timestamp is normalized to UTC microsecond precision once; the same normalized `DateTimeOffset` is used for both the DB INSERT parameter and the canonical hash input string (R1-ISSUE-012).
- Bounded retry with jitter handles SerializationFailure from SSI conflicts with concurrent close or append operations (R2-ISSUE-005, R2-ISSUE-006).
- Advisory lock is released on transaction commit; never held across a Claude call or any external I/O.
- `dhf.entries` is INSERT-only; DB triggers reject UPDATE/DELETE.
- The ordering list is materialized once; hash input and INSERT both use it.
- `tool_calls_hash` is computed before `record_hash` so the former can be hashed into the latter.

## 8. DhfDecorationWriter (Stretch)

`DhfExtractor` calls Sonnet 4.6 asynchronously per turn to extract rationale, requirements, alternatives, risks from the transcript. Output is written as rows in `dhf.entry_decorations`.

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

Decoration failures never block the core pipeline. They're logged as `integrity_event` decorations with failure reason.

When `supersedesDecorationId` is non-null, it references the `decoration_id` of the prior decoration being logically replaced. The prior row remains in the table for audit trail; the new row is the current version. The FK constraint ensures the referenced decoration exists.

### 8.1 Ambiguity Context Decoration

When SPEC-05 records an ambiguity resolution turn, it writes an `ambiguity_context` decoration with:

```json
{
  "voiceTranscript": "fillet this edge",
  "visionObservations": ["edge_12 at viewport center", "edge_44 under cursor"],
  "resolutionQuestion": "Which edge do you mean — the near edge or the far edge?",
  "userResponse": "near",
  "resolvedEntityRef": { "kind": "Edge", "persistentId": "...", "...": "..." }
}
```

This makes ambiguity events first-class auditable evidence that Opus 4.7 interpreted what the user meant, and what they confirmed.

### 8.2 Integrity Event Decoration

Integrity check failures detected during **deep validation** (Section 10.2) are written as `integrity_event` decorations. Shallow validation (Section 10.1) is side-effect-free and does NOT write decorations.

Every `integrity_event` payload MUST contain a `dedupKey` string field that uniquely identifies the failure type and affected artifact for a given entry. The `dedupKey` is composed of `eventType` and a discriminator that distinguishes failures that share the same type but affect different artifacts (e.g., `"screenshot_hash_mismatch:before"` vs `"screenshot_hash_mismatch:after"`).

Example payload:

```json
{
  "eventType": "screenshot_hash_mismatch",
  "dedupKey": "screenshot_hash_mismatch:before",
  "detectedAt": "2026-04-28T14:33:02Z",
  "expectedSha256": "...",
  "actualSha256": "...",
  "screenshotPath": "...",
  "orchestratorAction": "omitted_from_context"
}
```

Deep validation uses atomic deduplication via `INSERT ... ON CONFLICT DO NOTHING` against the partial unique index `idx_integrity_event_dedup` (defined in migration 004 on `(entry_id, payload->>'dedupKey')` for `integrity_event` rows). This ensures that concurrent deep validation runs cannot produce duplicate integrity events and that distinct failures on the same entry (with different `dedupKey` values) are preserved independently.

```csharp
public async Task WriteIntegrityEventAsync(
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

Viewers surface `integrity_event` decorations as warnings on the relevant entry.

## 9. DhfRepository (Read API for Viewer)

Read-only queries only. No write methods. The viewer (SPEC-06) and the HashChainValidator consume this.

```csharp
public interface IDhfRepository
{
    Task<IReadOnlyList<DhfSessionSummary>> ListSessionsAsync(int limit, int offset, CancellationToken ct);
    Task<DhfSessionDetail?> GetSessionAsync(Guid sessionId, CancellationToken ct);
    Task<IReadOnlyList<DhfEntrySummary>> ListEntriesAsync(Guid sessionId, CancellationToken ct);
    Task<DhfEntryDetail?> GetEntryAsync(Guid entryId, CancellationToken ct);
    Task<IReadOnlyList<ToolCallRow>> GetToolCallsAsync(Guid entryId, CancellationToken ct);
    Task<IReadOnlyList<DecorationRow>> GetDecorationsAsync(Guid entryId, CancellationToken ct);
}
```

Pagination: `limit` defaults to 50, max 200. `offset` is zero-based. Results are ordered by `started_at DESC` for sessions, `sequence_number ASC` for entries, `sequence_in_turn ASC` for tool calls, and `created_at ASC` for decorations.

### 9.1 Repository DTOs

```csharp
public record DhfSessionSummary(
    Guid SessionId,
    DateTimeOffset StartedAt,
    DateTimeOffset? EndedAt,
    string UserIdentity,
    string? UserDisplayName,
    string WorkstationId,
    string AddinSessionId,
    int EntryCount);                // Computed via COUNT subquery

public record DhfSessionDetail(
    Guid SessionId,
    DateTimeOffset StartedAt,
    DateTimeOffset? EndedAt,
    string UserIdentity,
    string? UserDisplayName,
    string WorkstationId,
    string SwVersion,
    string AddinVersion,
    string OrchestratorVersion,
    string ProtocolVersion,
    string AddinSessionId);

public record DhfEntrySummary(
    Guid EntryId,
    long SequenceNumber,
    DateTimeOffset Timestamp,
    string InputModality,
    string TurnType,
    string RawTranscript,            // Truncated to 200 chars in summary
    int ToolCallCount,               // Computed via COUNT subquery
    bool HasDecorations);            // Computed via EXISTS subquery

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
    string? ScreenshotBeforePath,
    string? ScreenshotAfterPath,
    string? ScreenshotBeforeSha256,
    string? ScreenshotAfterSha256,
    string PreviousHash,
    string ToolCallsHash,
    string RecordHash);

public record ToolCallRow(
    Guid ToolCallId,
    Guid EntryId,
    int SequenceInTurn,
    string CallMessageId,
    string ToolName,
    JsonElement Inputs,
    JsonElement? Result,
    string? ErrorCode,
    string? ErrorMessage,
    bool? Success,
    string LifecycleState,
    bool IsInternal,
    Guid? RetryOfToolCallId,
    int RetryDepth,
    int? ExecuteMs,
    int? RebuildMs,
    int? TotalMs,
    long StateVersionBefore,
    long? StateVersionAfter,
    string AddinSessionId);

public record DecorationRow(
    Guid DecorationId,
    Guid EntryId,
    string DecorationType,
    DateTimeOffset CreatedAt,
    string CreatedBy,
    JsonElement Payload,
    string DecorationHash,
    Guid? SupersedesDecorationId,
    bool IsSuperseded);              // true if another decoration references this decoration_id
```

`IsSuperseded` is computed via an `EXISTS` subquery checking whether any other row in `dhf.entry_decorations` has `supersedes_decoration_id` pointing to this decoration's `decoration_id`. The viewer can filter or visually de-emphasize superseded decorations while preserving them in the audit trail.

## 10. HashChainValidator

Two validation modes.

### 10.1 Shallow Validation

Recomputes `record_hash` and `tool_calls_hash` from stored column values. Does NOT recompute `feature_tree_*_hash` from JSONB or `screenshot_*_sha256` from files. Fast, runs on every viewer page load. **Side-effect-free: returns results in-memory only, does not write decorations or any other rows.** The viewer displays validation results from the API response, not from stored decorations.

Timestamp re-canonicalization during shallow validation uses the same `HashCanonicalizer.NormalizeTimestamp` helper (Section 4.1) applied to the `DateTimeOffset` read back from Postgres. Because the original INSERT used a normalized value, the round-trip is lossless and produces an identical canonical string.

### 10.2 Deep Validation

All shallow checks PLUS:
- Recomputes `feature_tree_before_hash` and `feature_tree_after_hash` from stored JSONB via `HashCanonicalizer.ComputeFeatureTreeHash`
- Recomputes `screenshot_before_sha256` and `screenshot_after_sha256` from file bytes on disk, if the files exist

Missing screenshot files produce warnings, not failures, because screenshot retention has a practical cleanup window (Section 7.3 of SPEC-02). Deep validation runs on-demand from the viewer's integrity endpoint and as part of acceptance testing. Deep validation writes `integrity_event` decorations for detected failures via `WriteIntegrityEventAsync` (Section 8.2), which provides atomic deduplication.

### 10.3 Results

```csharp
public record ValidationResult(
    bool Valid,
    IReadOnlyList<ValidationFailure> Failures,
    IReadOnlyList<ValidationWarning> Warnings);

public record ValidationFailure(Guid EntryId, long SequenceNumber, string Reason);
public record ValidationWarning(Guid EntryId, string Reason);
```

ACC-02 (mandatory acceptance test, Section 13) passes if: deep validation reports zero failures. Warnings (e.g., missing screenshot files after cleanup) are logged but do not fail the criterion. When screenshots are present in the test fixture, they MUST hash-verify cleanly (no warnings for present files).

## 11. Capture Rules (DHF-Level)

- **DHF-001 One entry per agent turn.** Turns may contain zero tool calls (clarification, confirmation, ambiguity_resolution) or N tool calls (execution).
- **DHF-002 Raw transcript is authoritative.** Never paraphrased, never cleaned. What the user said (voice) or typed (text) is preserved verbatim.
- **DHF-003 Decoration is opportunistic.** Missing rationale extraction is not a failure; the core loop always produces the entry and tool_calls rows first.
- **DHF-004 Hash chain continuous within DHF session.** First entry uses `previous_hash = 'GENESIS'`. Subsequent entries chain from the prior entry's `record_hash`. Chain resets on DHF session split (new AddinSessionId).
- **DHF-005 Failed tool calls log rows.** Error rows still get written with `success = false` or `NULL`, `lifecycle_state` reflecting terminal reason. State-before and state-after can be identical; the entry proves the system observed.
- **DHF-006 Advisory lock scope is INSERT-only.** See Section 7.
- **DHF-007 Extraction writes to decorations.** Never mutates entries or tool_calls. Architecture test enforces no UPDATE statements targeting those tables.
- **DHF-008 Non-executing turns still chain.** clarification, confirmation, ambiguity_resolution turns contribute to the hash chain even though they have zero tool calls.
- **DHF-009 Ambiguity resolution is a dedicated turn_type.** When Opus 4.7's tool-calling agent asks a clarification question specifically because voice intent and vision context disagreed (or when the geometric state presented multiple candidate targets), the turn is recorded as `ambiguity_resolution` with both the voice transcript and an `ambiguity_context` decoration preserving both inputs.
- **DHF-010 Enum values use canonical storage mappings.** All persisted enum values pass through `DhfEnumMappings.ToStorage()`. Architecture tests verify `.ToString()` is never used for DB-bound enum conversion.
- **DHF-011 Session metadata is immutable once inserted.** DB trigger (`guard_session_update`) rejects any UPDATE that modifies columns other than `ended_at`, and only permits `ended_at` to transition from NULL to non-NULL. DELETE on sessions is rejected. This protects user attribution, workstation identity, and software version metadata from post-hoc rewriting.
- **DHF-012 Integrity-event decorations use atomic deduplication.** Every `integrity_event` payload contains a `dedupKey` field. A partial unique index on `(entry_id, payload->>'dedupKey')` prevents duplicate events. `WriteIntegrityEventAsync` uses `INSERT ... ON CONFLICT DO NOTHING` for race-free dedup.

## 12. Regulatory Framing Gap Analysis

Honest mapping per SDR-TL-006. DHF positioning is "design audit log foundation for future 21 CFR 820.30 / Part 11 alignment," not current compliance.

| Requirement | BSVoice-CAD Coverage | Status |
|-------------|---------------------|--------|
| 21 CFR 820.30(a) Design plan | Out of scope — not a design-action artifact | Out-of-scope |
| 21 CFR 820.30(c) Design input tracking | Requirement linkages in decoration table (stretch) | Partial |
| 21 CFR 820.30(e) Design review records | Transcript + rationale captured | Partial (no formal review workflow) |
| 21 CFR 820.30(j) Design History File | Append-only hash-chained ledger of design actions | Foundation (not certified compliant) |
| 21 CFR Part 11 §11.10 Closed-system controls | Hash chain + DB triggers (sessions, entries, tool_calls) + defense-in-depth | Partial (orchestrator has DROP TRIGGER privilege; post-hackathon separation) |
| 21 CFR Part 11 §11.50 Signature manifestations | Windows user identity only, not FDA-compliant e-sig | Out-of-scope |
| 21 CFR Part 11 §11.70 Signature/record linking | User identity stored per session (joined to entries via session_id); session metadata protected by trigger | Partial |
| 21 CFR Part 11 §11.100–200 E-signatures | Not implemented | Out-of-scope |
| 21 CFR Part 11 §11.300 ID codes / passwords | Not implemented | Out-of-scope |

Submission language MUST say "design audit log foundation" and MUST NOT say "FDA-compliant" or "21 CFR 820.30 compliant."

## 13. Required Tests (Build-Breaking)

All in `BSVoiceCAD.Orchestrator.Tests` unless noted. All use Testcontainers Postgres 16 to exercise real DB behavior.

- `DhfSessionManagerTests` — open new session, resume same AddinSessionId, resume after orchestrator restart (session left open, not closed on shutdown), close prior workstation session on new load, reject cross-workstation mutation, retry on serialization failure / unique violation (with jitter verification)
- `DhfLedgerTests` — append turn, assert entry + tool_calls rows, hash chain valid, tool_calls_hash covers children, reject append to closed session, reject mismatched tool-call AddinSessionId, retry on serialization failure
- `HashChainTests` — deliberate column mutation detected, deliberate JSONB mutation detected (deep mode), deliberate screenshot byte mutation detected (deep mode)
- `HashChainConcurrencyTests` — 1000 concurrent appends via advisory lock, no forks, all valid
- `ToolCallHashTests` — child row tamper detection, row reorder tamper detection
- `ImmutabilityTriggerTests` — attempt UPDATE/DELETE on entries and tool_calls, assert DB raises exception; attempt UPDATE of session metadata columns (user_identity, workstation_id, sw_version, etc.), assert trigger rejects; attempt DELETE on sessions, assert trigger rejects; verify ended_at NULL→non-NULL transition is permitted; verify ended_at non-NULL→non-NULL transition is rejected
- `NonExecutingTurnTests` — clarification, confirmation, ambiguity_resolution turns produce correct rows
- `DecorationWriterTests` — ambiguity_context and integrity_event decorations written correctly; supersede path sets supersedes_decoration_id correctly; IsSuperseded computed correctly in read path; atomic integrity_event dedup via ON CONFLICT verified under concurrent writes
- `DhfRepositoryTests` — read-only query paths, pagination, DTO field mapping, IsSuperseded computation
- `EnumMappingTests` — every value of `InputModality`, `TurnType`, `UndoGroupOutcome`, `ToolCallLifecycleState`, `DecorationType` round-trips through `ToStorage()` and satisfies the corresponding schema CHECK constraint
- `HashColumnSyncTests` — asserts `DhfHashColumns.HashedColumns` and `ToolCallHashColumns.HashedColumns` cover all non-excluded columns in their respective tables and that all listed columns exist in the schema and corresponding DTOs. Excluded columns (`record_hash`, `entry_id` in tool_calls, etc.) are listed explicitly in the test as an allowlist
- `TimestampCanonTests` — verifies `DateTimeOffset` round-trips through `HashCanonicalizer.NormalizeTimestamp` → Postgres `TIMESTAMPTZ` → read-back → `NormalizeTimestamp` → canonical string and produces identical results; covers non-UTC offsets, sub-microsecond ticks, and edge-case fractional seconds
- `DbUpBootstrapTests` — verifies migrations execute successfully on a fresh database with no pre-existing `dhf` schema
- `CloseAppendCoordinationTests` — concurrent close and append on same session; verifies either append succeeds before close or append fails with session-closed error after retry; no orphan entries in closed sessions

### 13.1 Acceptance Tests (exposed to SPEC-00 ACC table)

| ID | Criterion | Method |
|----|-----------|--------|
| ACC-02 | Per-turn DHF with correct child tool_calls; hash chain valid (deep) | `HashChainValidator.ValidateDeep` returns zero failures; warnings logged but non-blocking |
| ACC-12 | Advisory lock prevents hash fork under 1000 concurrent appends | `HashChainConcurrencyTests` |
| ACC-15 | AddinSessionId change triggers DHF session split, same ID resumes (including after orchestrator restart) | `DhfSessionManagerTests` integration test |
| ACC-16 | DB trigger rejects UPDATE/DELETE on entries, tool_calls, and session metadata | `ImmutabilityTriggerTests` |
| ACC-17 | tool_calls_hash detects child-row tampering | `ToolCallHashTests` |
| ACC-18 | Session metadata immutability: trigger permits only ended_at close, rejects metadata mutation and DELETE | `ImmutabilityTriggerTests` (session-specific cases) |

## 14. Out of Scope

- Tool-call lifecycle state machine (states, transitions, timeouts, cancel, quarantine) → SPEC-03
- Construction of `DhfTurnContext` from lifecycle events → SPEC-03
- Agent loop, confirmation flow, vision context enrichment → SPEC-05
- Viewer UI rendering and endpoints → SPEC-06
- Voice transcription → SPEC-05

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*