# Codex — Round 1

Issue → Why it matters → Suggested fix

1. SPEC-01 and SPEC-02 conflict on `AddinSessionId` generation
Why it matters → SPEC-01 defines deterministic generation from `(machineGuid, swProcessId, loadTimestamp)` with no fallback; SPEC-02 defines generation with a GUID nonce and fallback machine ID persistence. SPEC-00 says SPEC-02 owns generation while SPEC-04 consumes only epoch semantics, but SPEC-01 currently hard-specs the algorithm. This is an architecture seam break: tests, validators, and cross-module assumptions will drift immediately.
Suggested fix → Move generation algorithm entirely out of SPEC-01. In SPEC-01 keep only wire-level constraints:
- format: `^[0-9a-f]{32}$`
- uniqueness expectation within a load epoch
- semantics: new value on add-in reload
Then make SPEC-02 the sole owner of algorithm/fallback behavior. Example edit:

```csharp
// SPEC-01: keep only this
public static class AddinSessionIdContract
{
    public const string Regex = "^[0-9a-f]{32}$";
    // No generation method here.
}
```

2. Protocol error vocabularies are inconsistent across specs
Why it matters → SPEC-01 defines `AddinProtocolErrorCode` as:
`InFlightDuplicate, UnknownCallId, UnknownMessageType, StaleEpoch, MessageTooLarge, MalformedEnvelope`
But SPEC-02 dispatch behavior references:
`INTERNAL_ERROR`, `HANDSHAKE_INCOMPLETE`, `INVALID_CALL_ID`, `EXPIRED_CALL_ID`, `TERMINATED_CALL_ID`.
Those do not exist in SPEC-01. This makes the add-in implementation impossible without out-of-band enums or stringly typed behavior.
Suggested fix → Centralize the complete protocol error set in SPEC-01 and have SPEC-02 consume it verbatim. Either add the missing values:

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

or remove those conditions from wire errors and model them differently. Right now the contract is internally inconsistent.

3. SPEC-02 handshake shape contradicts SPEC-01 `AddinReadyMessage`
Why it matters → SPEC-02 says `AddinReadyMessage` includes resolved `screenshotDir` base path. SPEC-01 `AddinReadyMessage` has no such field. This is a hard wire break.
Suggested fix → Add the field to SPEC-01 if it is truly required, or remove it from handshake and derive path out-of-band. If kept:

```csharp
public record AddinReadyMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string AddinVersion { get; init; }
    public required string SwVersion { get; init; }
    public required string SwBuild { get; init; }
    public required string ProtocolVersion { get; init; }
    public required string ScreenshotRootPath { get; init; }
}
```

Then add corresponding serialization and validation tests in Protocol.

4. SPEC-04 depends on a type not defined in SPEC-01
Why it matters → SPEC-04 says it depends on `ToolCallLifecycleState` from SPEC-01, but SPEC-01 glossary defines lifecycle states without actually declaring the enum. Later SPEC-04 uses it in `ToolCallRecord` and mapping code. That blocks compile-time ownership.
Suggested fix → Define it in SPEC-01 explicitly:

```csharp
public enum ToolCallLifecycleState
{
    Completed,
    TimedOut,
    Cancelled,
    Abandoned
}
```

Then update all specs to reference the actual enum, not just glossary text.

5. `IReadOnlyDhfRepository` / `IDhfRepository` naming drifts between SPEC-04 and SPEC-06
Why it matters → SPEC-04 defines `IDhfRepository`; SPEC-06 requires `IReadOnlyDhfRepository` and says SPEC-04 must expose it; SPEC-00 says viewer queries `DhfRepository` read-only. This is a clean-boundary issue: read-only dependency is correct, but the contract is split and late-bound.
Suggested fix → Define the read-only interface in SPEC-04 and make the concrete repository implement both interfaces.

```csharp
public interface IReadOnlyDhfRepository
{
    Task<IReadOnlyList<DhfSessionSummary>> ListSessionsAsync(int limit, int offset, CancellationToken ct);
    ...
}

public interface IDhfRepository : IReadOnlyDhfRepository
{
    // if any write methods are needed elsewhere
}
```

Then SPEC-06 should depend only on the interface already owned by SPEC-04, not redefine method shapes.

6. Viewer spec references exception contracts absent from SPEC-04
Why it matters → SPEC-06 requires `RepositoryUnavailableException` and `SessionNotFoundException`. SPEC-04 does not define them. This creates hidden integration work and non-testable endpoint behavior.
Suggested fix → Add explicit exception types to SPEC-04 and constrain repository implementations to throw them at the seam.

```csharp
public sealed class RepositoryUnavailableException : Exception { ... }
public sealed class SessionNotFoundException : Exception { ... }
```

Also specify whether low-level `PostgresException` is wrapped at repository boundary.

7. Session/open-close semantics are underspecified across SPEC-00, SPEC-02, SPEC-04
Why it matters → SPEC-00 says session closes on orchestrator graceful shutdown or heartbeat timeout. SPEC-04 explicitly says orchestrator does NOT close session on graceful shutdown. SPEC-02 reconnect logic also assumes same-session resume. This is one of the core lifecycle seams and currently disagrees.
Suggested fix → Pick one rule and make all specs align. Given SPEC-04’s resume model is cleaner, update SPEC-00 glossary and seam text to remove “orchestrator graceful shutdown” as a DHF session closure reason. Closure should be tied to add-in/epoch/heartbeat only.

8. `AddinSessionId` format conflicts with glossary wording
Why it matters → SPEC-00 glossary says AddinSessionId is UUID-generated fresh on each add-in load; SPEC-01/02 both use 32-char lowercase hex, not RFC UUID text with hyphens/version bits. This creates confusing terminology and bad validation expectations.
Suggested fix → Rename in glossary from “UUID” to “32-char lowercase hex session token” unless you actually want UUID semantics. If UUIDv7 is wanted, use UUIDv7 consistently. Don’t mix.

9. `MessageId` and `CallId` identity policy is split and partially duplicated
Why it matters → SPEC-01 says `MessageId` is UUIDv7 string. SPEC-02 validates `CallId` regex as UUIDv7 at dispatch. But there is no shared typed identifier abstraction, so every module will re-validate raw strings differently. This hurts testability and increases bug surface.
Suggested fix → Introduce small value objects in Protocol for cross-process identifiers.

```csharp
public readonly record struct MessageId(string Value);
public readonly record struct CallId(string Value);
public readonly record struct UndoGroupId(string Value);
```

If avoiding custom converters is preferred for hackathon speed, at least define shared validators in Protocol and require all consumers to use them.

10. Screenshot contract is over-coupled to local filesystem paths
Why it matters → Returning absolute paths over wire bakes host filesystem layout into the protocol, leaks implementation detail, and complicates testing. It also infects DHF and viewer contracts with path handling/security concerns.
Suggested fix → Keep v1 local-only if needed, but narrow the contract to a relative content identifier plus root negotiated once. Example:

```csharp
public record ScreenshotResult : IToolResult
{
    public required string RelativePath { get; init; } // "{addinSessionId}/{file}"
    public required string Sha256Hash { get; init; }
    public required int WidthPx { get; init; }
    public required int HeightPx { get; init; }
}
```

Then only SPEC-02/05/06 own root resolution. Easier to test and safer.

11. `ToolRegistry` reflection-based static construction is doing too much
Why it matters → Reflection over assembly plus static initialization plus cross-check against `[JsonDerivedType]`, timeout tables, and schema generation creates brittle startup behavior. It’s testable, but errors surface late and often at type-load time.
Suggested fix → Make registry construction explicit and fail-fast in a deterministic builder.

```csharp
public sealed class ToolRegistryBuilder
{
    public ToolRegistry Build(Assembly protocolAssembly) { ... }
}
```

Use a startup test to compare discovered vs registered tools, but avoid heavy logic in static ctors.

12. Stretch placeholder records in the same polymorphic hierarchy are risky
Why it matters → Empty registered tool types with `IsImplemented = false` mean deserializers and schema generators know about tools that do not exist operationally. One missed `Implemented` filter exposes a non-callable tool to the model. This is a structural footgun.
Suggested fix → Separate “registered wire types” from “advertised tools.” Better: do not register stretch tools in the wire contract until they have real shapes. If registry completeness is important, use a separate manifest for planned tools rather than empty DTOs.

13. `FeatureTree` duplicated in `StateSnapshot` and `get_feature_tree` creates size/perf tension
Why it matters → You already note 4 MiB limits and truncation. Embedding a potentially large tree in every `ToolResultMessage.StateAfter` and `StateUpdateMessage` is likely the biggest serialization cost in the system.
Suggested fix → Split state summary from full tree. Put hashes and a small summary in `StateSnapshot`; fetch full tree only via tool/query when needed.

```csharp
public record StateSnapshot
{
    public required long StateVersion { get; init; }
    ...
    public required string FeatureTreeHash { get; init; }
    public int FeatureCount { get; init; }
    public bool IsFeatureTreeAvailable { get; init; }
    public bool IsFeatureTreeTruncated { get; init; }
    public IReadOnlyList<FeatureNode>? FeatureTreePreview { get; init; }
}
```

This reduces constant wire pressure and simplifies timeout handling.

14. `CaptureState(long stateVersion, string addinSessionId)` leaks protocol concerns into adapter service
Why it matters → `addinSessionId` is not needed to inspect SolidWorks state; it belongs to message assembly, not COM adapter logic. Passing it into document service blurs module boundaries.
Suggested fix → Keep adapter state capture pure and add protocol metadata in the bridge/lifecycle layer.

```csharp
StateSnapshot CaptureState(long stateVersion);
```

If a field in `StateSnapshot` requires session context, that’s a smell—keep session at envelope level as SPEC-01 already does.

15. Tool handlers and services are synchronous, but bridge/lifecycle are async; exception ownership is muddy
Why it matters → Synchronous COM services are fine, but the spec mixes where timeouts, cancellation, and result shaping happen. `ToolDispatcher` maps exceptions to `ToolError`, while SPEC-03 also owns timeout semantics. Without a narrow boundary, you’ll get duplicate mapping or inconsistent `TimedOut` vs `Cancelled`.
Suggested fix → Make adapter contract return a discriminated execution result for adapter-owned failures only; lifecycle wraps it for timeout-owned failures.

```csharp
public sealed record AdapterExecutionResult(
    bool Success,
    IToolResult? Result,
    ToolError? Error,
    StateSnapshot StateAfter,
    TimingReport Timing);
```

Then only SPEC-03 synthesizes `TimedOut`/quarantine outcomes.

16. `ToolCallRegistry` state machine likely belongs in SPEC-03, not add-in bridge
Why it matters → Add-in-side dedup/cache is justified, but it is starting to own lifecycle concepts (`Queued`, `Executing`, `Completed`, `Cancelled`, `Abandoned`) that SPEC-03 also owns. This duplication is a drift vector.
Suggested fix → Narrow add-in registry to transport/idempotency semantics: `Pending`, `Completed`, `TerminalNoReplay`. Keep business lifecycle names in orchestrator. Or explicitly define add-in-local states as a separate enum to avoid semantic overlap.

17. `GetFeatureTree(bool includeSuppressed, bool compressed)` mixes two dimensions and encodes agent concerns into adapter
Why it matters → “compressed” is a viewer/agent optimization leaking into adapter API. It’s ambiguous and harder to test than explicit projection options.
Suggested fix → Replace boolean flag with an options type.

```csharp
public sealed record FeatureTreeQuery(
    bool IncludeSuppressed,
    FeatureTreeProjection Projection);

public enum FeatureTreeProjection
{
    Full,
    NumericParametersOnly
}
```

Clearer call sites and easier future extension.

18. `DocumentRef` ownership is unclear across specs
Why it matters → SPEC-01 project shape includes `State/DocumentRef.cs`, but its actual definition is missing here; SPEC-04 uses `DocumentRef? DocumentRef` in `DhfTurnContext`. Missing core value types make downstream schema decisions hard to review and test.
Suggested fix → Ensure every shared type used across module seams is defined in SPEC-01 or removed from seam contract. No “defined elsewhere” for protocol-adjacent DTOs.

19. `HashCanonicalizer.ComputeRecordHash(object, IReadOnlyList<string>)` is too dynamic
Why it matters → Hash coverage over named string columns is fragile under refactor and weakly typed. Renaming a property can silently break integrity until tests catch it.
Suggested fix → Prefer explicit canonical projection records per hashed artifact.

```csharp
public sealed record DhfEntryHashMaterial(
    Guid EntryId,
    Guid SessionId,
    long SequenceNumber,
    string Timestamp,
    ...
    string ToolCallsHash);
```

Then hash the projection, not an `object` plus string property list. Keep `DhfHashColumns` only for schema-sync tests.

20. Viewer endpoint DTOs are inconsistent with SPEC-04 storage strings
Why it matters → Viewer examples use `"Voice"`, `"Execution"`, `"Committed"` while SPEC-04 stores lowercase/snake_case. If repository returns storage strings directly, UI contracts drift; if it maps back, that mapping belongs somewhere explicit.
Suggested fix → Define viewer-facing DTOs separately from repository row DTOs. The repository can return domain enums or storage strings; the viewer should shape its own API model deliberately.

```csharp
public sealed record EntryListItemResponse(
    Guid EntryId,
    long SequenceNumber,
    DateTimeOffset Timestamp,
    string InputModalityDisplay,
    string TurnTypeDisplay,
    ...
);
```

Or better, pass enums through and let JSON serialize consistently.

21. Architecture-test strategy is good, but some rules are not machine-enforceable as written
Why it matters → “no SQL INSERT/UPDATE/DELETE in namespace X” and “no enum `.ToString()` for DB-bound enums” are useful intentions, but naive text scans are noisy and brittle.
Suggested fix → Encode enforceable seams:
- Viewer project references only `IReadOnlyDhfRepository`
- DB write interfaces live in separate assembly not referenced by viewer
- Enum mapping functions are the only methods exposed to SQL parameter builders
- Integration tests assert actual SQL behavior rather than source scanning where possible

22. Test project naming drifts across specs
Why it matters → SPEC-00 says `BSVoiceCAD.Tests.Integration`; SPEC-02 uses `BSVoiceCAD.Integration.Tests`; SPEC-00 uses `BSVoiceCAD.Tests.Architecture`. Naming drift creates solution clutter and broken docs/scripts.
Suggested fix → Standardize now:
- `BSVoiceCAD.Tests.Protocol`
- `BSVoiceCAD.Tests.Addin`
- `BSVoiceCAD.Tests.Orchestrator`
- `BSVoiceCAD.Tests.Integration`
- `BSVoiceCAD.Tests.Architecture`

23. `FileLogger` in add-in is a bespoke logging abstraction instead of standard logging seam
Why it matters → A custom file logger tends to spread ad hoc logging APIs and makes testing harder. .NET Framework can still use `Microsoft.Extensions.Logging`.
Suggested fix → Define an interface boundary or use `ILogger<T>` throughout, with file sink configured in composition root. Even on NetFx, this is simpler than proliferating custom logger types.

24. `HttpListener` for add-in bridge is acceptable but increases platform-specific setup complexity
Why it matters → URL ACL management and `HttpListener` quirks are operational risks in a hackathon setting. The spec already includes a setup script requirement.
Suggested fix → If staying on NetFx, fine, but isolate it behind a tiny transport interface so you can swap later without bridge rewrite.

```csharp
public interface IWebSocketHost : IAsyncDisposable
{
    Task StartAsync(CancellationToken ct);
}
```

Then tests can use an in-memory/double host rather than exercising HTTP.sys every time.

25. Some enums referenced in SPEC-02 exception mapping are absent from SPEC-01
Why it matters → `InvalidUndoState`, `TemplateNotFound` appear as `ToolErrorCode` targets in SPEC-02 but are not in SPEC-01 `ToolErrorCode`. That is another hard contract mismatch.
Suggested fix → Either add them to `ToolErrorCode` or map to existing values intentionally. I’d add them because they are distinct user-observable failures:

```csharp
public enum ToolErrorCode
{
    NoActiveDocument, WrongDocumentType, InvalidSketchState,
    EntityNotFound, UnitAmbiguous, InvalidUndoState, TemplateNotFound,
    TimedOut, Cancelled, InternalError, ComException, ValidationFailed,
    InFlightDuplicate, UnknownCallId, VersionMismatch, UnknownMessageType, StaleEpoch,
    ConfirmationDenied, RetryLimitExceeded
}
```

26. Viewer path validation spec uses `Guid.TryParse` for `addinSessionId`, but protocol format is 32-char hex
Why it matters → That endpoint will reject valid session IDs if they are not RFC UUID strings. This is a direct cross-spec bug.
Suggested fix → Validate using the protocol regex, not `Guid.TryParse`.

```csharp
private static readonly Regex AddinSessionIdRegex = new("^[0-9a-f]{32}$", RegexOptions.Compiled);
```

27. Session metadata hashing gap is acknowledged but not isolated in API design
Why it matters → SPEC-04 honestly notes session metadata is not in chain. Good. But since `SessionId` is included in entry hash, readers may overestimate what is protected.
Suggested fix → Make integrity API surface the protection boundary explicitly:

```csharp
public sealed record ChainValidationResult
{
    ...
    public bool SessionMetadataProtectedByChain { get; init; } // false in v1
}
```

This avoids accidental compliance overclaiming in downstream UI/docs.

28. `StateSnapshot` validator rules are extensive but would be easier to test if represented as constructors/factories
Why it matters → Many invariants are representable in type shape instead of validator logic. Right now `IsNullDocumentState` plus many nullable fields creates invalid combinations that only runtime validation catches.
Suggested fix → Use sum types / dedicated records if possible:

```csharp
public abstract record DocumentState;
public sealed record NullDocumentState : DocumentState;
public sealed record LiveDocumentState(...) : DocumentState;
```

If you must stay flat for wire compatibility, at least provide factory methods used by producers so invalid combinations are hard to create.

29. `GeneratedMessageDeserializer` hand-maintenance is okay, but spec should isolate ownership better
Why it matters → Right now SPEC-02 owns NetFx deserialization for all protocol families, including ones the add-in may never consume. That’s extra maintenance surface.
Suggested fix → Limit manual deserializer to inbound families only plus nested unions reachable from inbound messages. Test outbound shape with Protocol tests, not duplicate deserializers for `IAddinMessage` unless truly needed.

30. Review-order note says SPEC-04 can converge parallel with SPEC-02, but current contract leaks SPEC-02 implementation details anyway
Why it matters → Parallel convergence is only possible if SPEC-04 depends strictly on glossary epoch semantics. Current docs pull in AddinReady lifecycle nuances and screenshot/session assumptions from adapter behavior.
Suggested fix → Tighten SPEC-04 inputs to:
- session metadata
- `addinSessionId`
- append contexts
No assumptions about bridge mechanics. If SPEC-04 needs behavior from reconnect/handshake, define it in SPEC-00 seam contract, not infer it from SPEC-02.

Summary
The architecture is directionally strong: protocol leaf, adapter isolation, lifecycle/DHF split, viewer read-only boundary, and explicit architecture tests are all good decisions. The biggest defects are contract drift, not decomposition. Fix the cross-spec mismatches first:
1. `AddinSessionId` ownership
2. protocol error enums
3. `AddinReadyMessage` shape
4. session closure semantics
5. missing shared types/exceptions

Once those are unified, the module boundaries become much more testable and implementation-ready.