# Codex — Round 2

Issue → Cross-spec contract drift still blocks implementation  
Why it matters → The biggest prior concern remains valid: SPEC-00/01/02/04/06 do not yet form a single compile-time contract. This is not “editorial drift”; it creates guaranteed integration failures.

Concrete mismatches:
- `AddinSessionId` shape/ownership:
  - SPEC-00 glossary says “UUID generated fresh”
  - SPEC-01 defines a concrete hash algorithm and 32-char hex format
  - SPEC-02 defines a different algorithm with nonce + fallback machine ID
- `AddinReadyMessage` shape:
  - SPEC-02 handshake says it includes `screenshotDir`
  - SPEC-01 message record does not
- Protocol error enums:
  - SPEC-02 references `HANDSHAKE_INCOMPLETE`, `INVALID_CALL_ID`, `EXPIRED_CALL_ID`, `TERMINATED_CALL_ID`, `INTERNAL_ERROR`
  - SPEC-01 enum sets do not contain them
- Viewer repository seam:
  - SPEC-06 requires `IReadOnlyDhfRepository`, `RepositoryUnavailableException`, `SessionNotFoundException`, `ChainValidationResult`
  - SPEC-04 defines different names/contracts or only implies them

Suggested fix → Make SPEC-00 own a canonical seam table and strip conflicting ownership from downstream specs.

Example shape:

```csharp
// BSVoiceCAD.Protocol/Contracts/AddinSessionIdContract.cs
public static class AddinSessionIdContract
{
    public const string Pattern = "^[0-9a-f]{32}$";
    public const string Description = "32-char lowercase hex epoch token";
}
```

```csharp
// SPEC-01 owns only wire constraints, not generation algorithm
public record AddinReadyMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string AddinVersion { get; init; }
    public required string SwVersion { get; init; }
    public required string SwBuild { get; init; }
    public required string ProtocolVersion { get; init; }
    public string? ScreenshotRootPath { get; init; } // if truly required
}
```

Then add a dedicated cross-assembly test project:

```csharp
public class CrossSpecContractSyncTests
{
    [Fact]
    public void AddinReadyMessage_shape_matches_handshake_contract() { ... }

    [Fact]
    public void Addin_protocol_error_enum_contains_all_dispatch_codes() { ... }

    [Fact]
    public void Viewer_required_repository_types_exist_in_dhf_assembly() { ... }
}
```

---

Issue → SPEC-04 contradicts SPEC-00 on dual add-in sessions per workstation  
Why it matters → This is the most important unresolved architectural contradiction. SPEC-00 explicitly says:
- two concurrent add-in instances on the same machine produce distinct `AddinSessionId`s
- SPEC-04 must handle the resulting dual-session scenario

But SPEC-04 `OpenOrResumeSessionCore` does:

```sql
UPDATE dhf.sessions
SET ended_at = NOW()
WHERE ended_at IS NULL
  AND workstation_id = @WorkstationId
```

That closes all open sessions for the workstation, including legitimate concurrent sessions with different `addin_session_id`s. This is not a corner case; it is a direct violation of the glossary contract.

Suggested fix → Close only stale sessions that are proven superseded, not every open session on the workstation.

Minimum fix:

```sql
UPDATE dhf.sessions
SET ended_at = NOW()
WHERE ended_at IS NULL
  AND workstation_id = @WorkstationId
  AND addin_session_id = @SupersededAddinSessionId
```

Better: introduce an explicit close path keyed by session, not workstation sweep logic.

```csharp
// Resume same addin_session_id on same workstation
if (existing is { WorkstationId: var ws } && ws == metadata.WorkstationId)
    return existing.SessionId;

// Only open new session; do not mass-close other open sessions for same workstation
await conn.ExecuteAsync(
    @"INSERT INTO dhf.sessions (...) VALUES (...)",
    args, tx);
```

If you really need cleanup for orphan sessions, base it on heartbeat age or explicit closure reason, not workstation collision alone.

---

Issue → Session closure semantics are still inconsistent across specs  
Why it matters → Session lifecycle affects DHF continuity, reconnect behavior, and viewer interpretation. Right now:
- SPEC-00 glossary says DHF session closes on orchestrator graceful shutdown
- SPEC-04 explicitly says orchestrator does not close on graceful shutdown
- SPEC-02 reconnect behavior assumes same-session resume across orchestrator restart

These are mutually incompatible.

Suggested fix → Pick one authoritative rule and update all specs. I agree with SPEC-04’s model: add-in epoch is the session authority, not orchestrator process lifetime.

Canonical rule:

```text
DHF session closes only on:
1. explicit add-in teardown/disconnect
2. new AddinSessionId on same add-in channel
3. heartbeat timeout
4. operator/admin forced close

Orchestrator restart alone does not close the DHF session.
```

Then remove `explicit_shutdown` ambiguity from SPEC-00 unless you can distinguish orchestrator vs add-in shutdown at the protocol layer.

---

Issue → Error-code contract is underspecified and currently unimplementable  
Why it matters → SPEC-02 has concrete dispatcher behaviors that require wire-level error codes not present in SPEC-01. Without a unified enum, implementers will either:
- invent local strings, or
- overload unrelated enum values,
both of which make protocol tests meaningless.

Suggested fix → Expand `AddinProtocolErrorCode` and `ClientProtocolErrorCode` in SPEC-01 to include all actual seam states, or explicitly move some of these to local-only logs.

Likely needed:

```csharp
public enum AddinProtocolErrorCode
{
    InFlightDuplicate,
    UnknownCallId,
    UnknownMessageType,
    StaleEpoch,
    MessageTooLarge,
    MalformedEnvelope,
    InternalError,
    HandshakeIncomplete,
    InvalidCallId,
    ExpiredCallId,
    TerminatedCallId
}
```

Also fix casing drift in prose. If JSON enum policy is PascalCase strings, specs must not show `"VERSION_MISMATCH"` or `"INTERNAL_ERROR"`.

---

Issue → `ToolErrorCode` table in SPEC-02 references values absent from SPEC-01  
Why it matters → This is another compile break masked as prose. SPEC-02 maps exceptions to:
- `InvalidUndoState`
- `TemplateNotFound`

Those do not exist in SPEC-01 `ToolErrorCode`.

Suggested fix → Either add them to the shared enum or intentionally collapse them to existing values. I recommend adding them because they are distinct and testable.

```csharp
public enum ToolErrorCode
{
    NoActiveDocument,
    WrongDocumentType,
    InvalidSketchState,
    EntityNotFound,
    UnitAmbiguous,
    InvalidUndoState,
    TemplateNotFound,
    TimedOut,
    Cancelled,
    InternalError,
    ComException,
    ValidationFailed,
    InFlightDuplicate,
    UnknownCallId,
    VersionMismatch,
    UnknownMessageType,
    StaleEpoch,
    ConfirmationDenied,
    RetryLimitExceeded
}
```

Then add protocol tests asserting enum serialization and adapter mapping coverage.

---

Issue → Viewer spec validates `addinSessionId` as GUID, but protocol defines 32-char hex  
Why it matters → This is a concrete bug, not a stylistic issue. Valid screenshot URLs would be rejected by the viewer if the add-in emits non-hyphenated 32-char hex session IDs.

Suggested fix → Replace `Guid.TryParse` in SPEC-06 screenshot serving contract with the same regex validator used elsewhere.

```csharp
private static readonly Regex AddinSessionIdRegex =
    new("^[0-9a-f]{32}$", RegexOptions.Compiled);

if (!AddinSessionIdRegex.IsMatch(addinSessionId))
    return Results.BadRequest();
```

This should be shared from Protocol, not duplicated.

---

Issue → `IReadOnlyDhfRepository` ownership boundary is wrong  
Why it matters → SPEC-06 currently defines the read interface it depends on, but the dependency direction is SPEC-06 → SPEC-04. The owner of the abstraction should be SPEC-04. Otherwise the consumer is defining producer contracts, which invites drift and makes architecture tests awkward.

Suggested fix → Move the read-only interface and related DTOs/exceptions into `BSVoiceCAD.Orchestrator.Dhf` (or a `.Contracts` sub-namespace) and have Viewer consume them.

Preferred structure:

```csharp
namespace BSVoiceCAD.Orchestrator.Dhf.Contracts;

public interface IReadOnlyDhfRepository
{
    Task<IReadOnlyList<DhfSessionSummary>> ListSessionsAsync(...);
    Task<DhfSessionDetail?> GetSessionAsync(...);
    Task<IReadOnlyList<DhfEntrySummary>> ListEntriesAsync(...);
    Task<DhfEntryDetail?> GetEntryDetailAsync(...);
    Task<ChainValidationResult> ValidateChainAsync(...);
}
```

And in Viewer:

```csharp
public sealed class ViewerHost
{
    public ViewerHost(IReadOnlyDhfRepository repo, ViewerOptions options) { ... }
}
```

---

Issue → Missing exception contracts at the DHF seam reduce testability  
Why it matters → SPEC-06’s endpoint behavior depends on specific repository exceptions, but SPEC-04 does not cleanly define them. Without explicit seam exceptions, tests end up asserting on `PostgresException`, string messages, or generic 500s.

Suggested fix → Add typed repository-boundary exceptions in SPEC-04.

```csharp
public sealed class RepositoryUnavailableException : Exception
{
    public RepositoryUnavailableException(string message, Exception inner) : base(message, inner) {}
}

public sealed class SessionNotFoundException : Exception
{
    public Guid SessionId { get; }
    public SessionNotFoundException(Guid sessionId) : base($"Session {sessionId} not found.")
        => SessionId = sessionId;
}
```

Repository implementation should wrap infra errors once:

```csharp
catch (PostgresException ex) when (IsConnectivity(ex))
{
    throw new RepositoryUnavailableException("Database is temporarily unreachable.", ex);
}
```

---

Issue → `HashCanonicalizer.ComputeRecordHash(object, IReadOnlyList<string>)` is too dynamic for a regulatory-facing integrity core  
Why it matters → String-based property selection is fragile under refactor. Rename a property and you get either runtime failure or a silent integrity contract change caught only by tests. For the most sensitive part of the system, the API should be strongly typed.

Suggested fix → Keep the column list for schema-sync tests, but hash explicit projection records.

```csharp
public sealed record DhfEntryHashMaterial(
    Guid EntryId,
    Guid SessionId,
    long SequenceNumber,
    string Timestamp,
    string InputModality,
    string RawTranscript,
    float? TranscriptConfidence,
    string TurnType,
    string? UndoGroupId,
    string? UndoOutcome,
    string? DocumentRef,
    string? DocumentRevisionRef,
    string FeatureTreeBeforeHash,
    string FeatureTreeAfterHash,
    string? ScreenshotBeforePath,
    string? ScreenshotAfterPath,
    string? ScreenshotBeforeSha256,
    string? ScreenshotAfterSha256,
    string PreviousHash,
    string ToolCallsHash);
```

Then:

```csharp
entry.RecordHash = HashCanonicalizer.Compute(entry.ToHashMaterial());
```

This is easier to unit test and safer to evolve.

---

Issue → `StateSnapshot` is carrying too much data on the hot path  
Why it matters → You already documented 4 MiB limits and truncation, which is a sign the abstraction is too heavy for common messages. Embedding `FeatureTree` in `StateUpdateMessage` and `ToolResultMessage.StateAfter` will become the dominant serialization cost, create unnecessary hashing work, and complicate timeout recovery.

Suggested fix → Split “state summary” from “full tree query.” Keep hashes and small summaries in the default snapshot; fetch the full tree only through `get_feature_tree`.

Example:

```csharp
public record StateSnapshot
{
    public required long StateVersion { get; init; }
    public required DateTimeOffset StateTimestamp { get; init; }
    public string? DocumentId { get; init; }
    public string? DocumentName { get; init; }
    public DocumentType? DocumentType { get; init; }
    public bool IsSketchActive { get; init; }
    public string? ActiveSketchId { get; init; }
    public required IReadOnlyList<SelectionRef> ActiveSelection { get; init; }
    public required string FeatureTreeHash { get; init; }
    public int FeatureCount { get; init; }
    public bool IsFeatureTreeAvailable { get; init; }
    public bool IsFeatureTreeTruncated { get; init; }
    public IReadOnlyList<FeatureNode>? FeatureTreePreview { get; init; }
    public UnitSystem? Units { get; init; }
    public required bool IsNullDocumentState { get; init; }
}
```

If you keep full trees in snapshots for hackathon speed, at least declare that only `InitialState` and explicit query responses carry full trees.

---

Issue → `CaptureState(long stateVersion, string addinSessionId)` leaks protocol concerns into the adapter boundary  
Why it matters → The adapter should know how to inspect SolidWorks, not how the transport models epochs. Passing `addinSessionId` into document-state capture couples SPEC-02 internals to SPEC-01 envelope rules.

Suggested fix → Make the adapter return pure state content; add session metadata in the bridge layer.

```csharp
StateSnapshot CaptureState(long stateVersion);
```

If some downstream code wants the session ID on the wire, it already exists at the envelope level.

---

Issue → Tool registry is doing too much work at static initialization time  
Why it matters → Reflection-heavy static ctors are hard to debug and hard to test in isolation. A failure during type load can crash schema generation, protocol bootstrapping, and unit-test discovery in confusing ways.

Suggested fix → Move registry assembly scanning into an explicit builder.

```csharp
public sealed class ToolRegistryBuilder
{
    public ToolRegistry Build(Assembly assembly)
    {
        // reflect, validate, construct immutable registry
    }
}
```

Then exercise it in tests and instantiate it once at composition root. Keep `ToolRegistry.Current` only as a thin wrapper if necessary.

---

Issue → Stretch placeholder DTOs in the same wire polymorphic family are a footgun  
Why it matters → Empty but registered tool types mean the wire contract knows about tools that are intentionally unusable. One missed `Implemented` filter exposes a tool the model can call but the backend cannot execute. That’s a preventable class of integration bug.

Suggested fix → Separate “planned tool manifest” from actual wire registration. Only register concrete tool DTOs once they are callable.

If you want completeness tracking:

```csharp
public sealed record PlannedTool(string Name, bool Implemented);
```

Keep that out of `IToolInput`/`IToolResult` polymorphic registration.

---

Issue → Architecture-test strategy is good, but some proposed rules are too text-driven  
Why it matters → Rules like “no SQL INSERT/UPDATE/DELETE in namespace X” or “no enum `.ToString()` for DB-bound enums” are brittle if implemented as source scanning. They produce false positives and miss indirect violations.

Suggested fix → Prefer dependency and API enforcement over text scanning.

Examples:
- Viewer references only `IReadOnlyDhfRepository` assembly contract, not SQL/data-source packages
- Write-capable repository interface lives in a different assembly than read contract
- SQL command builders accept only storage strings, not enums
- Integration tests assert that illegal UPDATE/DELETE fail at DB level

Use architecture tests for namespace/reference boundaries, not string matching whenever possible.

---

Issue → Logging abstraction in SPEC-02 is bespoke and weakens consistency  
Why it matters → `FileLogger` as a custom type invites ad hoc logging APIs, inconsistent structured fields, and harder test injection. This project has enough cross-cutting events that standard logging abstractions will pay off immediately.

Suggested fix → Use `ILogger<T>` throughout, even on .NET Framework.

```csharp
public sealed class ToolDispatcher
{
    private readonly ILogger<ToolDispatcher> _logger;
    ...
}
```

Composition root can still configure a file sink for the add-in runtime.

---

Issue → ACC numbering is inconsistent across specs  
Why it matters → Beth’s prior note is correct. SPEC-00 says ACC-08 is screenshot/feature-tree grounding; SPEC-06 says ACC-08 is viewer render under 1 second. That makes traceability tables unreliable.

Suggested fix → Renumber now and keep a single authoritative ACC registry in SPEC-00.

Example:
- ACC-08 → grounding tools valid data
- ACC-10 → viewer timeline displays DHF
- ACC-20 → viewer timeline performance under 1 second

Then each module spec references, but does not redefine, the canonical ID.

---

Issue → Some language still overclaims guarantees relative to actual implementation  
Why it matters → Phrases like “BSMS is structurally hallucination-resistant” or “there is no path by which the model invents…” are not engineering contracts. They’re hard to test and risky in customer-facing artifacts, especially given SDR-TL-006’s careful compliance posture.

Suggested fix → Rephrase as bounded, testable claims.

Instead of:
```text
There is no path by which the model invents...
```

Use:
```text
The architecture reduces hallucination risk by constraining reasoning to typed rows
from a structured database and treating absence as a first-class outcome.
```

This aligns better with the rest of the spec’s otherwise disciplined tone.

---

Overall assessment → The decomposition is strong; the remaining defects are contract-level, not module-boundary-level  
Why it matters → I agree with prior reviewers that the six-module split is clean:
- Protocol as leaf module is correct
- Adapter/lifecycle separation is correct
- DHF as sole writer is correct
- Viewer read-only dependency is correct

The real problem is not abstraction choice; it is unresolved ownership and drift at the seams. Until those are collapsed into single-source contracts, implementation will fork.

Suggested fix → Before coding:
1. Resolve `AddinSessionId` ownership and wording everywhere
2. Resolve protocol error enums
3. Resolve `AddinReadyMessage` shape
4. Resolve session close/resume semantics
5. Move viewer read contracts into SPEC-04 ownership
6. Fix the dual-session contradiction in SPEC-04
7. Add cross-spec sync tests

If those are fixed, the architecture is implementation-ready. If not, teams will “successfully” build incompatible modules.