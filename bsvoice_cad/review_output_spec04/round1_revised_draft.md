---
title: "BSVoice-CAD SPEC-04 — DHF Ledger"
subtitle: "Schema, hash model, append logic, AddinSessionId epoch, DHF session lifecycle"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-04 — DHF Ledger

**Version:** 1.1.0
**Status:** DRAFT — post-R1 revision
**Depends on:** SPEC-00 (module boundaries), SPEC-01 (wire types only — message shapes, `IToolInput`, `IToolResult`, `ToolError`, `StateSnapshot`, `FeatureNode`, `HashCanonicalizer`, `UndoGroupOutcome`, `ToolCallLifecycleState`)
**Consumed by:** SPEC-03 (writes entries), SPEC-06 (reads entries)

## 0. Scope

This spec defines everything about how voice-and-text turns become durable audit records. Schema, hash chain, append semantics, session lifecycle, epoch handling, decoration extraction, integrity validation, canonical enum-to-storage mappings. Nothing about this spec depends on state machines, tool-call lifecycle, or agent logic — it receives complete `DhfTurnContext` objects from the outside and persists them.

**In scope:** Postgres 16 schema, hash canonicalization rules (including timestamp normalization and enum-to-storage mapping), per-turn append logic, AddinSessionId epoch and DHF session split/resume, decoration rows, immutability enforcement, integrity validation, read API and DTO contracts.

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

**SDR-04-005: Same-AddinSessionId reconnect resumes the prior session.** If the orchestrator reconnects to an add-in whose `AddinSessionId` matches an already-open `dhf.sessions` row, it resumes that session (appends to its chain) rather than opening a new row. Distinguishes genuine add-in reload (new ID) from transient orchestrator disconnect (same ID).

**SDR-04-006: Advisory lock per DHF session during append.** Concurrency safety via Postgres `pg_advisory_xact_lock`. Lock scope narrowed to the INSERT transaction; held during tip-read → hash-compute → entry+tool_calls INSERT, released at transaction commit. No locks held during Claude calls or hash computation outside the transaction.

**SDR-04-007: No SQLite fallback.** Docker Postgres 16 is default. EmbeddedPostgres (`MysticMind.PostgresEmbed`) is the only demo-day fallback. SQLite requires schema variants, lacks advisory locks, lacks JSONB, and cannot provide append-only trigger enforcement the same way. Locked.

**SDR-04-008: Workstation_id is a trust boundary for session isolation.** The close-other-sessions logic and advisory lock scoping rely on `workstation_id` to isolate sessions per physical machine. This assumes `workstation_id` is a stable, non-spoofed machine identifier (e.g., `Environment.MachineName`). In hackathon scope, this is acceptable because only one orchestrator instance runs per workstation. Post-hackathon: evaluate stronger machine fingerprinting if multi-tenant deployment is required. A spoofed or colliding `workstation_id` can cause one orchestrator to prematurely close another's sessions.

**SDR-04-009: Canonical enum-to-storage mappings via explicit switch expressions.** All enums persisted to Postgres (`InputModality`, `TurnType`, `UndoGroupOutcome`, `ToolCallLifecycleState`, `DecorationType`) MUST be converted through `DhfEnumMappings` — never via `.ToString()` or `.ToLowerInvariant()`. This ensures C# PascalCase enum member names produce the lowercase/snake_case literals required by schema CHECK constraints. Architecture tests verify every enum value round-trips correctly.

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
```

```sql
-- 005_immutability_triggers.sql

CREATE OR REPLACE FUNCTION dhf.reject_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'dhf.% is append-only; % is not permitted',
        TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

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

-- dhf.sessions.ended_at is UPDATE-able (that's how we close sessions), so no trigger.
-- dhf.entry_decorations: application code uses insert-with-supersede (SDR-04-002).
-- No DB-level trigger because post-hackathon operational cleanup may require
-- administrative DELETE. Application-level invariant enforced by architecture tests.
```

### 3.2 Enforcement Gap (Honest Disclosure)

DB-level triggers prevent `UPDATE` and `DELETE` by any SQL client, including the orchestrator's connection. However, the orchestrator also has `DROP TRIGGER` privilege (it's the only DB user in hackathon scope). A malicious or buggy orchestrator could drop triggers, mutate rows, and recreate triggers. This is acknowledged in SDR-TL-006 (SPEC-00) and in Section 12 of this spec's integrity-validation discussion. Post-hackathon: separate DBA and application roles.

For `dhf.entry_decorations`, no DB trigger is installed because the insert-with-supersede model (SDR-04-002) may require post-hackathon administrative cleanup. The application-level invariant (no UPDATE/DELETE in normal operation) is enforced by architecture tests that scan for prohibited SQL patterns.

For hackathon credibility, the framing is "defense-in-depth append-only enforcement" — application code never attempts UPDATE/DELETE, architecture tests forbid it in code, AND the database raises exceptions on attempts against entries and tool_calls.

## 4. Hash Model

### 4.1 HashCanonicalizer Rules (Imported from SPEC-01)

- `System.Text.Json` with fixed options: camelCase property names, PascalCase enum strings, `decimal` serialized "G29" invariant-culture, null-omitting, sorted object keys.
- SHA-256 of UTF-8 canonical JSON, output as lowercase hex.
- `"NO_DOCUMENT"` is a literal sentinel string for null feature-tree states, not a hash of anything.
- `DateTimeOffset` values MUST be normalized to UTC and truncated to microsecond precision (6 decimal places) before hashing. Canonical format: `yyyy-MM-ddTHH:mm:ss.ffffffZ` via `value.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'", CultureInfo.InvariantCulture)`. The same normalized string MUST be used for both the INSERT parameter and the hash input. Postgres `TIMESTAMPTZ` stores microsecond precision, so the round-trip is lossless when this rule is followed.
- Enum values in hash inputs MUST use the storage-form string from `DhfEnumMappings` (Section 4.5), NOT `.ToString()`.

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

The method is wrapped in a bounded retry loop to handle serialization failures (`SQLSTATE 40001`) and unique-index violations (`SQLSTATE 23505`) that can occur under concurrent startup.

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
            await Task.Delay(50 * (attempt + 1), ct);
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

### 6.2 Close on Shutdown

`OrchestratorProgram.ShutdownAsync` calls `DhfSessionManager.CloseSessionAsync(sessionId)` which sets `ended_at = NOW()`. Idempotent.

## 7. DhfLedger.AppendTurnAsync

The ONLY writer of `dhf.entries` and `dhf.tool_calls`.

```csharp
public async Task<Guid> AppendTurnAsync(DhfTurnContext turn, CancellationToken ct)
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

    // 3. Normalize timestamp to canonical form (Section 4.1) before hashing.
    var canonicalTimestamp = turn.Timestamp.ToUniversalTime()
        .ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'",
                  CultureInfo.InvariantCulture);

    // 4. Build entry record using DhfEnumMappings for all enum fields (SDR-04-009).
    var entry = new DhfEntryRecord
    {
        EntryId = turn.TurnId,
        SessionId = turn.SessionId,
        SequenceNumber = nextSeq,
        Timestamp = turn.Timestamp,
        CanonicalTimestamp = canonicalTimestamp,          // Used for hash input
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
- Timestamp is normalized to canonical UTC microsecond form before hashing (R1-ISSUE-012).
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
    CancellationToken ct)
{
    var hash = HashCanonicalizer.ComputeDecorationHash(payload);
    using var conn = await _dataSource.OpenConnectionAsync(ct);
    await conn.ExecuteAsync(
        @"INSERT INTO dhf.entry_decorations
          (decoration_id, entry_id, decoration_type, created_by, payload, decoration_hash)
          VALUES (@Id, @EntryId, @Type, @CreatedBy, @Payload, @Hash)",
        new { Id = Guid.NewGuid(), entryId, Type = type.ToStorage(),
              createdBy, Payload = payload, Hash = hash });
}
```

Decoration failures never block the core pipeline. They're logged as `integrity_event` decorations with failure reason.

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

Example payload:

```json
{
  "eventType": "screenshot_hash_mismatch",
  "detectedAt": "2026-04-28T14:33:02Z",
  "expectedSha256": "...",
  "actualSha256": "...",
  "screenshotPath": "...",
  "orchestratorAction": "omitted_from_context"
}
```

Deep validation deduplicates before writing: it checks for an existing `integrity_event` decoration of the same `eventType` on the same entry before inserting a new one. If an identical event already exists, no new row is written. Viewers surface these as warnings on the relevant entry.

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
    Guid? SupersedesDecorationId);
```

## 10. HashChainValidator

Two validation modes.

### 10.1 Shallow Validation

Recomputes `record_hash` and `tool_calls_hash` from stored column values. Does NOT recompute `feature_tree_*_hash` from JSONB or `screenshot_*_sha256` from files. Fast, runs on every viewer page load. **Side-effect-free: returns results in-memory only, does not write decorations or any other rows.** The viewer displays validation results from the API response, not from stored decorations.

### 10.2 Deep Validation

All shallow checks PLUS:
- Recomputes `feature_tree_before_hash` and `feature_tree_after_hash` from stored JSONB via `HashCanonicalizer.ComputeFeatureTreeHash`
- Recomputes `screenshot_before_sha256` and `screenshot_after_sha256` from file bytes on disk, if the files exist

Missing screenshot files produce warnings, not failures, because screenshot retention has a practical cleanup window (Section 7.3 of SPEC-02). Deep validation runs on-demand from the viewer's integrity endpoint and as part of acceptance testing. Deep validation writes `integrity_event` decorations for detected failures, with deduplication (see Section 8.2).

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

## 12. Regulatory Framing Gap Analysis

Honest mapping per SDR-TL-006. DHF positioning is "design audit log foundation for future 21 CFR 820.30 / Part 11 alignment," not current compliance.

| Requirement | BSVoice-CAD Coverage | Status |
|-------------|---------------------|--------|
| 21 CFR 820.30(a) Design plan | Out of scope — not a design-action artifact | Out-of-scope |
| 21 CFR 820.30(c) Design input tracking | Requirement linkages in decoration table (stretch) | Partial |
| 21 CFR 820.30(e) Design review records | Transcript + rationale captured | Partial (no formal review workflow) |
| 21 CFR 820.30(j) Design History File | Append-only hash-chained ledger of design actions | Foundation (not certified compliant) |
| 21 CFR Part 11 §11.10 Closed-system controls | Hash chain + DB triggers + defense-in-depth | Partial (orchestrator has DROP TRIGGER privilege; post-hackathon separation) |
| 21 CFR Part 11 §11.50 Signature manifestations | Windows user identity only, not FDA-compliant e-sig | Out-of-scope |
| 21 CFR Part 11 §11.70 Signature/record linking | User identity stored per session (joined to entries via session_id) | Partial |
| 21 CFR Part 11 §11.100–200 E-signatures | Not implemented | Out-of-scope |
| 21 CFR Part 11 §11.300 ID codes / passwords | Not implemented | Out-of-scope |

Submission language MUST say "design audit log foundation" and MUST NOT say "FDA-compliant" or "21 CFR 820.30 compliant."

## 13. Required Tests (Build-Breaking)

All in `BSVoiceCAD.Orchestrator.Tests` unless noted. All use Testcontainers Postgres 16 to exercise real DB behavior.

- `DhfSessionManagerTests` — open new session, resume same AddinSessionId, close prior workstation session on new load, reject cross-workstation mutation, retry on serialization failure / unique violation
- `DhfLedgerTests` — append turn, assert entry + tool_calls rows, hash chain valid, tool_calls_hash covers children, reject append to closed session, reject mismatched tool-call AddinSessionId
- `HashChainTests` — deliberate column mutation detected, deliberate JSONB mutation detected (deep mode), deliberate screenshot byte mutation detected (deep mode)
- `HashChainConcurrencyTests` — 1000 concurrent appends via advisory lock, no forks, all valid
- `ToolCallHashTests` — child row tamper detection, row reorder tamper detection
- `ImmutabilityTriggerTests` — attempt UPDATE/DELETE on entries and tool_calls, assert DB raises exception
- `NonExecutingTurnTests` — clarification, confirmation, ambiguity_resolution turns produce correct rows
- `DecorationWriterTests` — ambiguity_context and integrity_event decorations written correctly, deduplication of integrity_event works
- `DhfRepositoryTests` — read-only query paths, pagination, DTO field mapping
- `EnumMappingTests` — every value of `InputModality`, `TurnType`, `UndoGroupOutcome`, `ToolCallLifecycleState`, `DecorationType` round-trips through `ToStorage()` and satisfies the corresponding schema CHECK constraint
- `HashColumnSyncTests` — asserts `DhfHashColumns.HashedColumns` and `ToolCallHashColumns.HashedColumns` cover all non-excluded columns in their respective tables and that all listed columns exist in the schema and corresponding DTOs. Excluded columns (`record_hash`, `entry_id` in tool_calls, etc.) are listed explicitly in the test as an allowlist
- `TimestampCanonTests` — verifies `DateTimeOffset` round-trips through Postgres `TIMESTAMPTZ` and back produce identical canonical strings for hash computation
- `DbUpBootstrapTests` — verifies migrations execute successfully on a fresh database with no pre-existing `dhf` schema

### 13.1 Acceptance Tests (exposed to SPEC-00 ACC table)

| ID | Criterion | Method |
|----|-----------|--------|
| ACC-02 | Per-turn DHF with correct child tool_calls; hash chain valid (deep) | `HashChainValidator.ValidateDeep` returns zero failures; warnings logged but non-blocking |
| ACC-12 | Advisory lock prevents hash fork under 1000 concurrent appends | `HashChainConcurrencyTests` |
| ACC-15 | AddinSessionId change triggers DHF session split, same ID resumes | `DhfSessionManagerTests` integration test |
| ACC-16 | DB trigger rejects UPDATE/DELETE on entries and tool_calls | `ImmutabilityTriggerTests` |
| ACC-17 | tool_calls_hash detects child-row tampering | `ToolCallHashTests` |

## 14. Out of Scope

- Tool-call lifecycle state machine (states, transitions, timeouts, cancel, quarantine) → SPEC-03
- Construction of `DhfTurnContext` from lifecycle events → SPEC-03
- Agent loop, confirmation flow, vision context enrichment → SPEC-05
- Viewer UI rendering and endpoints → SPEC-06
- Voice transcription → SPEC-05

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*