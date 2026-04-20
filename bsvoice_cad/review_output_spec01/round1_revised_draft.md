---
title: "BSVoice-CAD SPEC-01 — Wire Protocol"
subtitle: "Cross-process types, polymorphic contracts, JSON policy, version handshake"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-01 — Wire Protocol

**Version:** 1.0.1
**Status:** DRAFT — post-Oracle-R1 revision
**Depends on:** SPEC-00 (module boundaries)
**Consumed by:** SPEC-02, SPEC-03, SPEC-04, SPEC-05

## 0. Scope

This spec defines every type that crosses a process boundary in BSVoice-CAD, plus the boundary-level behavioral rules that govern how those types are validated at the wire edge (epoch rejection, version-match handshake, per-tool timeouts, message-envelope invariants).

All other specs reference types defined here and add nothing of their own to the wire format.

**In scope:** polymorphic message hierarchies, tool input/result records for all mandatory tools, shared enums and records, JSON serialization policy, protocol versioning, handshake message shapes, boundary validation rules.

**Out of scope:** state machines (SPEC-03), DHF schema (SPEC-04), COM service contracts (SPEC-02), agent loop logic (SPEC-05). Stretch tool field definitions are deferred; their records exist as empty placeholders with polymorphic registrations to maintain registry completeness (see Section 7.2).

**Deployment constraint — co-location:** The orchestrator and add-in are co-located on the same Windows machine. They share a local filesystem and communicate over a localhost WebSocket. Remote deployment is out of scope for this version. This constraint is load-bearing: `ScreenshotResult` (Section 7.1) transmits file paths rather than image bytes.

## 1. Project Shape

```
BSVoiceCAD.Protocol/                        # .NET Standard 2.0, LangVersion=latest
├── Abstractions/
│   ├── IToolInput.cs                       # Marker; [JsonPolymorphic]
│   ├── IToolResult.cs                      # Marker; [JsonPolymorphic]
│   ├── IClientMessage.cs                   # Orchestrator → Add-in
│   └── IAddinMessage.cs                    # Add-in → Orchestrator
├── Versioning/
│   └── ProtocolVersion.cs                  # const string Current = "1.0.0"
├── Messages/
│   ├── ToolCallMessage.cs
│   ├── CancelToolCallMessage.cs
│   ├── StartUndoGroupMessage.cs
│   ├── CommitUndoGroupMessage.cs
│   ├── RollbackUndoGroupMessage.cs
│   ├── ClientProtocolErrorMessage.cs
│   ├── ToolResultMessage.cs
│   ├── StateUpdateMessage.cs
│   ├── AddinReadyMessage.cs
│   ├── UndoGroupOutcomeMessage.cs
│   └── AddinProtocolErrorMessage.cs
├── Tools/
│   ├── Inputs/                             # One record per tool
│   ├── Results/                            # One record per tool
│   ├── Common/
│   │   ├── ToolError.cs
│   │   ├── ToolErrorCode.cs                # enum
│   │   ├── TimingReport.cs
│   │   ├── UnitSystem.cs
│   │   ├── DocumentType.cs
│   │   ├── ExtrudeDirection.cs
│   │   ├── StandardPlane.cs
│   │   ├── DimensionType.cs
│   │   ├── DimensionMode.cs
│   │   ├── EntityKind.cs
│   │   ├── EntityRef.cs
│   │   ├── SketchEntityId.cs
│   │   ├── SelectionRef.cs                 # Discriminated union (persistent | sketch)
│   │   ├── DimensionTarget.cs              # Discriminated union
│   │   ├── PlaneRef.cs                     # Discriminated union
│   │   ├── CancelReason.cs                 # enum (message-level)
│   │   ├── RollbackReason.cs               # enum (message-level)
│   │   ├── ClientProtocolErrorCode.cs      # enum (message-level)
│   │   └── AddinProtocolErrorCode.cs       # enum (message-level)
│   ├── Registry/
│   │   ├── ToolNameAttribute.cs
│   │   ├── ToolRegistry.cs
│   │   └── ToolTimeouts.cs                 # Per-tool timeout table
│   └── Schema/
│       ├── JsonSchemaGenerator.cs
│       └── AnthropicToolSpec.cs
├── State/
│   ├── StateSnapshot.cs
│   ├── FeatureNode.cs
│   ├── FeatureParameter.cs                 # Polymorphic base
│   ├── DocumentRef.cs
│   └── NullDocumentState.cs
├── Serialization/
│   ├── JsonOptions.cs                      # camelCase, string enums
│   └── HashCanonicalizer.cs
└── Validation/
    ├── Validators/                         # One per tool input + message envelope
    └── ToolInputValidator.cs
```

## 2. JSON Policy

Locked decisions. Enforced by build-breaking tests in `BSVoiceCAD.Protocol.Tests`.

### 2.1 Property Naming

`JsonOptions.Default.PropertyNamingPolicy = JsonNamingPolicy.CamelCase`.

Every wire property name is camelCase. Rejected alternatives: PascalCase (inconsistent with general JSON convention), snake_case (inconsistent with C# conventions and adds mental overhead). This applies to message fields, tool input/result fields, DHF JSONB payloads, and Anthropic tool schemas.

### 2.2 Enum Serialization

All enums serialize as **PascalCase strings** via `JsonStringEnumConverter` registered in `JsonOptions.Default`. Rationale: enum values appear in system prompt examples shown to Opus. PascalCase matches C# enum member names, which makes system prompt examples directly readable and avoids a second casing transformation for reviewers.

Example: `ExtrudeDirection.Forward` serializes to `"Forward"`, not `"forward"` or `0`. `UndoGroupOutcome.RolledBack` serializes to `"RolledBack"`.

Integer enum serialization is forbidden. `JsonEnumSerializationTests` fails the build if any enum round-trips as an integer.

### 2.3 Null Handling

`JsonIgnoreCondition.WhenWritingNull` for optional fields. Required fields that are null are validation errors, not serialization errors.

### 2.4 Polymorphic Discriminators

Every polymorphic family uses an explicit discriminator property:

- `IToolInput` and `IToolResult`: discriminator property `"tool"` with value equal to the tool name (snake_case tool names like `"new_part"`, `"start_sketch"` — this is the one place where snake_case survives, because the tool name IS the Anthropic API tool identifier)
- `IClientMessage`: discriminator property `"type"` with values like `"tool_call"`, `"cancel_tool_call"`, `"start_undo_group"`
- `IAddinMessage`: discriminator property `"type"` with values like `"tool_result"`, `"state_update"`, `"addin_ready"`
- `PlaneRef`: discriminator `"kind"` with values `"standard"` or `"entity"`
- `DimensionTarget`: discriminator `"kind"` with values `"sketch"` or `"persistent"`
- `SelectionRef`: discriminator `"kind"` with values `"persistent"` or `"sketch"`
- `FeatureParameter`: discriminator `"kind"` with values `"string"`, `"number"`, `"boolean"`, `"ref"`

## 3. Wire Framing

One complete JSON document per WebSocket **message** (not per frame). Receivers MUST accumulate frames until `EndOfMessage = true` before deserializing. `System.Net.WebSockets` handles frame reassembly transparently; application code never sees partial frames. No NDJSON.

**Maximum single-message size: 4 MiB.** This limit applies to the fully reassembled message. Messages exceeding this limit MUST be rejected by the receiver without attempting deserialization. The receiver sends a protocol error message (`ClientProtocolErrorCode.MessageTooLarge` or `AddinProtocolErrorCode.MessageTooLarge`) and discards the payload.

No chunked-transfer or streaming protocol is defined in this version. If a payload (e.g., a large feature tree) approaches 4 MiB, producers MUST reduce it at the application layer (e.g., `GetFeatureTreeInput` with depth limits, or omitting suppressed features). A pagination mechanism for large feature trees is deferred to a future protocol version.

Screenshots are not transmitted over the wire — see Section 7.1.

## 4. Polymorphic Families

### 4.1 IClientMessage (Orchestrator → Add-in)

```csharp
[JsonPolymorphic(TypeDiscriminatorPropertyName = "type")]
[JsonDerivedType(typeof(ToolCallMessage),              "tool_call")]
[JsonDerivedType(typeof(CancelToolCallMessage),        "cancel_tool_call")]
[JsonDerivedType(typeof(StartUndoGroupMessage),        "start_undo_group")]
[JsonDerivedType(typeof(CommitUndoGroupMessage),       "commit_undo_group")]
[JsonDerivedType(typeof(RollbackUndoGroupMessage),     "rollback_undo_group")]
[JsonDerivedType(typeof(ClientProtocolErrorMessage),   "client_protocol_error")]
public interface IClientMessage
{
    string MessageId { get; }            // Idempotency key for this message
    string AddinSessionId { get; }       // Epoch marker; rejected if stale
}
```

### 4.2 IAddinMessage (Add-in → Orchestrator)

```csharp
[JsonPolymorphic(TypeDiscriminatorPropertyName = "type")]
[JsonDerivedType(typeof(ToolResultMessage),            "tool_result")]
[JsonDerivedType(typeof(StateUpdateMessage),           "state_update")]
[JsonDerivedType(typeof(AddinReadyMessage),            "addin_ready")]
[JsonDerivedType(typeof(UndoGroupOutcomeMessage),      "undo_group_outcome")]
[JsonDerivedType(typeof(AddinProtocolErrorMessage),    "addin_protocol_error")]
public interface IAddinMessage
{
    string MessageId { get; }
    string AddinSessionId { get; }       // Authoritative; nested duplicates forbidden
}
```

### 4.3 IToolInput and IToolResult

Both use `TypeDiscriminatorPropertyName = "tool"` with the tool's snake_case name as the discriminator value. Every mandatory and stretch tool has exactly one `XxxInput : IToolInput` and one `XxxResult : IToolResult` registered via `[JsonDerivedType]`.

Registration completeness is enforced by `JsonDerivedTypeRegistrationCompletenessTests`: reflects all concrete implementations of each polymorphic interface in the Protocol assembly and fails the build if any concrete type lacks a `[JsonDerivedType]` attribute on the base.

## 5. Message Record Definitions

All records are immutable C# 10+ records with `required` modifier on non-nullable fields. `MessageId` is a UUIDv7 string for time-sorted uniqueness. `AddinSessionId` is the current epoch (see Section 9).

```csharp
public record ToolCallMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string CallId { get; init; }            // Lifecycle identity (see SPEC-03)
    public required string UndoGroupId { get; init; }       // Correlates to undo group
    public required IToolInput Input { get; init; }
}

public record CancelToolCallMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string CallId { get; init; }            // Which ToolCallMessage to cancel
    public required string UndoGroupId { get; init; }
    public required CancelReason Reason { get; init; }
}

public record StartUndoGroupMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string UndoGroupId { get; init; }
    public required string Label { get; init; }             // Shown in SW undo stack
}

public record CommitUndoGroupMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string UndoGroupId { get; init; }
}

public record RollbackUndoGroupMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string UndoGroupId { get; init; }
    public required RollbackReason Reason { get; init; }
}

public record ClientProtocolErrorMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required ClientProtocolErrorCode ErrorCode { get; init; }
    public required string ErrorText { get; init; }
    public string? ExpectedProtocolVersion { get; init; }
    public string? ReceivedProtocolVersion { get; init; }
}

public record ToolResultMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string CallId { get; init; }            // Matches ToolCallMessage.CallId
    public required string UndoGroupId { get; init; }
    public required bool Success { get; init; }
    public IToolResult? Result { get; init; }
    public ToolError? Error { get; init; }
    public required StateSnapshot StateAfter { get; init; }
    public required TimingReport Timing { get; init; }
}
```

**ToolResultMessage envelope invariant** (enforced by `MessageEnvelopeInvariantTests` and `ToolResultMessageValidator`):

- When `Success == true`: `Result` MUST be non-null and `Error` MUST be null.
- When `Success == false`: `Result` MUST be null and `Error` MUST be non-null.
- Any other combination is a validation error. Receivers MUST reject invalid envelopes with `AddinProtocolErrorCode.MalformedEnvelope`.

```csharp
public record StateUpdateMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required StateUpdateTrigger Trigger { get; init; }
    public required StateSnapshot State { get; init; }
    public string? CorrelatedCallId { get; init; }          // Set when Trigger = ToolCompletion
}

public record AddinReadyMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string AddinVersion { get; init; }
    public required string SwVersion { get; init; }
    public required string SwBuild { get; init; }
    public required string ProtocolVersion { get; init; }
}

public record UndoGroupOutcomeMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string UndoGroupId { get; init; }
    public required UndoGroupOutcome Outcome { get; init; } // Committed | RolledBack | Abandoned
    public string? FailureReason { get; init; }
}

public record AddinProtocolErrorMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required AddinProtocolErrorCode ErrorCode { get; init; }
    public required string ErrorText { get; init; }
    public string? RejectedMessageId { get; init; }
}

public enum StateUpdateTrigger
{
    InitialState,          // Post-handshake full-state resync (mandatory after AddinReady)
    ToolCompletion,        // Paired with ToolResultMessage
    SelectionChanged,
    DocumentOpened,
    DocumentClosed,
    RebuildComplete,
    ExternalChange         // User modified model outside BSVoice-CAD
}

public enum UndoGroupOutcome
{
    Committed,
    RolledBack,
    Abandoned              // Transport loss or add-in crash; rollback best-effort
}
```

## 6. Common Types

### 6.1 ToolError

```csharp
public record ToolError
{
    public required ToolErrorCode Code { get; init; }
    public required string Message { get; init; }
    public string? SwHresult { get; init; }
    public bool Recoverable { get; init; }
    public string? SuggestedAction { get; init; }
    public IReadOnlyDictionary<string, string>? Context { get; init; }
}

public enum ToolErrorCode
{
    // Precondition
    NoActiveDocument, WrongDocumentType, InvalidSketchState,
    EntityNotFound, UnitAmbiguous,

    // Runtime
    TimedOut, Cancelled, InternalError, ComException, ValidationFailed,

    // Protocol
    InFlightDuplicate, UnknownCallId, VersionMismatch, UnknownMessageType, StaleEpoch,

    // Business rule
    ConfirmationDenied, RetryLimitExceeded
}
```

### 6.2 TimingReport

```csharp
public record TimingReport
{
    public int QueueWaitMs { get; init; }    // Time between add-in receipt and STA dequeue
    public int ExecuteMs { get; init; }      // Time between STA dequeue and SW API return
    public int? RebuildMs { get; init; }     // Rebuild time if the op triggered one
    public int TotalMs { get; init; }        // End-to-end add-in-side wall-clock
}
```

### 6.3 Shared Enums

```csharp
public enum UnitSystem { Mm, Inch }

public enum DocumentType { Part, Assembly, Drawing }

public enum ExtrudeDirection { Forward, Reverse, Both, Midplane }

public enum StandardPlane { Front, Top, Right }

public enum DimensionType { Length, Radius, Diameter, Angle }

public enum DimensionMode
{
    LinearSingleEntity,
    LinearTwoEntity,
    RadialSingleEntity,
    DiametralSingleEntity,
    AngularTwoEntity        // Stretch; not required for Demo 1
}

public enum EntityKind
{
    Face, Edge, Vertex, Feature, Sketch, Plane, SketchEntity
}

// --- Message-level enums ---

public enum CancelReason
{
    UserAbort,
    Timeout,
    AgentAbort
}

public enum RollbackReason
{
    Timeout,
    UserCancel,
    Error
}

public enum ClientProtocolErrorCode
{
    VersionMismatch,
    MessageTooLarge,
    UnknownMessageType
}

public enum AddinProtocolErrorCode
{
    InFlightDuplicate,
    UnknownCallId,
    UnknownMessageType,
    StaleEpoch,
    MessageTooLarge,
    MalformedEnvelope
}
```

### 6.4 EntityRef and SketchEntityId

```csharp
// Persistent cross-document-session reference
public record EntityRef
{
    public required EntityKind Kind { get; init; }
    public required string PersistentId { get; init; }    // SW persistent reference string
    public required string DisplayLabel { get; init; }    // e.g., "Face<1>@Boss-Extrude1"
    public required string DocumentId { get; init; }
}

// Transient, valid only within the active sketch session
public record SketchEntityId
{
    public required string LocalId { get; init; }         // Unique within the sketch
    public required string SketchId { get; init; }        // Parent sketch context
}
```

### 6.5 PlaneRef, DimensionTarget, and SelectionRef (Discriminated Unions)

```csharp
[JsonPolymorphic(TypeDiscriminatorPropertyName = "kind")]
[JsonDerivedType(typeof(StandardPlaneRef), "standard")]
[JsonDerivedType(typeof(EntityPlaneRef),   "entity")]
public abstract record PlaneRef;

public record StandardPlaneRef(StandardPlane Plane) : PlaneRef;
public record EntityPlaneRef(EntityRef Reference) : PlaneRef;

// Dimension target can be transient (active sketch) or persistent (post-exit).
[JsonPolymorphic(TypeDiscriminatorPropertyName = "kind")]
[JsonDerivedType(typeof(SketchDimensionTarget),      "sketch")]
[JsonDerivedType(typeof(PersistentDimensionTarget),  "persistent")]
public abstract record DimensionTarget;

public record SketchDimensionTarget(
    SketchEntityId Primary,
    SketchEntityId? Secondary) : DimensionTarget;

public record PersistentDimensionTarget(
    EntityRef Primary,
    EntityRef? Secondary) : DimensionTarget;

// Selection reference — supports both persistent entities and transient sketch entities.
[JsonPolymorphic(TypeDiscriminatorPropertyName = "kind")]
[JsonDerivedType(typeof(PersistentSelectionRef), "persistent")]
[JsonDerivedType(typeof(SketchSelectionRef),     "sketch")]
public abstract record SelectionRef;

public record PersistentSelectionRef : SelectionRef
{
    public required EntityRef Entity { get; init; }
}

public record SketchSelectionRef : SelectionRef
{
    public required SketchEntityId SketchEntity { get; init; }
}
```

## 7. Tool Input and Result Records

### 7.1 Mandatory Tools

```csharp
public record NewPartInput : IToolInput
{
    public string Template { get; init; } = "default_mm";
    public string? DocumentName { get; init; }
}

public record NewPartResult : IToolResult
{
    public required string DocumentId { get; init; }
    public required string DocumentName { get; init; }
    public required UnitSystem Units { get; init; }
}

public record StartSketchInput : IToolInput
{
    public required PlaneRef Plane { get; init; }
}

public record StartSketchResult : IToolResult
{
    public required string SketchId { get; init; }
    public required string PlaneUsed { get; init; }
}

public record DrawRectangleInput : IToolInput
{
    public required double X1 { get; init; }
    public required double Y1 { get; init; }
    public required double X2 { get; init; }
    public required double Y2 { get; init; }
    public bool CenteredOnOrigin { get; init; }
}

public record DrawRectangleResult : IToolResult
{
    public required IReadOnlyList<SketchEntityId> EdgeIds { get; init; }    // 4 edges
    public required IReadOnlyList<SketchEntityId> VertexIds { get; init; }  // 4 corners
    public required string SketchId { get; init; }
}

public record DrawCircleInput : IToolInput
{
    public required double CenterX { get; init; }
    public required double CenterY { get; init; }
    public required double Radius { get; init; }
    public bool CenteredOnOrigin { get; init; }
}

public record DrawCircleResult : IToolResult
{
    public required SketchEntityId EdgeId { get; init; }
    public required SketchEntityId CenterVertexId { get; init; }
    public required string SketchId { get; init; }
}

public record AddDimensionInput : IToolInput
{
    public required DimensionMode Mode { get; init; }
    public required DimensionTarget Target { get; init; }   // Polymorphic: sketch or persistent
    public required double Value { get; init; }
    public required DimensionType Type { get; init; }
}

public record AddDimensionResult : IToolResult
{
    public required string DimensionId { get; init; }
    public required double AppliedValue { get; init; }
    public bool OverDefinedSketch { get; init; }
}

public record ExitSketchInput : IToolInput;

public record ExitSketchResult : IToolResult
{
    public required string ExitedSketchId { get; init; }
}

public record ExtrudeInput : IToolInput
{
    public required string SketchId { get; init; }
    public required double Depth { get; init; }
    public ExtrudeDirection Direction { get; init; } = ExtrudeDirection.Forward;
    public bool MergeResult { get; init; } = true;
}

public record ExtrudeResult : IToolResult
{
    public required string FeatureId { get; init; }
    public required string FeatureName { get; init; }
}

public record ScreenshotInput : IToolInput
{
    public int WidthPx { get; init; } = 1024;
    public int HeightPx { get; init; } = 768;
}
```

Screenshot bytes do NOT travel over the wire. The add-in writes the PNG to a shared directory on the local machine (see Section 0, deployment constraint) and returns a path + hash. See SPEC-02 for the shared-directory convention and SPEC-05 for Anthropic image ingestion.

```csharp
public record ScreenshotResult : IToolResult
{
    public required string FilePath { get; init; }        // Absolute path in shared screenshot dir
    public required string Sha256Hash { get; init; }      // Hex, lowercase
    public required int WidthPx { get; init; }
    public required int HeightPx { get; init; }
}

public record GetFeatureTreeInput : IToolInput
{
    public bool IncludeSuppressed { get; init; } = false;
}

public record GetFeatureTreeResult : IToolResult
{
    public required IReadOnlyList<FeatureNode> Tree { get; init; }
    public required string TreeHash { get; init; }
}
```

### 7.2 Stretch Tools

`CutExtrudeInput/Result`, `FilletInput/Result`, `ChamferInput/Result`, `HoleWizardInput/Result`, `LinearPatternInput/Result`, `CircularPatternInput/Result`, `GetSelectionInput/Result`, `RaycastViewportCenterInput/Result`, `ModifyFeatureParameterInput/Result`. Each follows the same input/result pattern.

**These are empty placeholder records** with `// TODO: STRETCH` markers. Their polymorphic registrations (`[JsonDerivedType]` on `IToolInput`/`IToolResult`) and `[ToolName]` attributes are present from day one so that `ToolRegistry` and completeness tests remain valid. Field definitions are deferred to stretch implementation. Any future field addition to a stretch tool record constitutes a wire-contract change and MUST increment `ProtocolVersion`.

## 8. State Snapshot

```csharp
public record StateSnapshot
{
    public required long StateVersion { get; init; }      // Monotonic within AddinSessionId
    public required DateTimeOffset StateTimestamp { get; init; }
    public string? DocumentId { get; init; }
    public string? DocumentName { get; init; }
    public DocumentType? DocumentType { get; init; }
    public bool IsSketchActive { get; init; }
    public string? ActiveSketchId { get; init; }
    public required IReadOnlyList<SelectionRef> ActiveSelection { get; init; }
    public required IReadOnlyList<FeatureNode> FeatureTree { get; init; }
    public required string FeatureTreeHash { get; init; }  // HashCanonicalizer output; "NO_DOCUMENT" sentinel valid
    public UnitSystem? Units { get; init; }                // Null when IsNullDocumentState is true
    public required bool IsNullDocumentState { get; init; }
}
```

**Null-document rules:** When `IsNullDocumentState` is `true`, `Units` MUST be `null`, `DocumentId` MUST be `null`, `FeatureTree` MUST be empty, `FeatureTreeHash` MUST be `"NO_DOCUMENT"`, and `IsSketchActive` MUST be `false`. When `IsNullDocumentState` is `false`, `Units` MUST be non-null.

```csharp
public record FeatureNode
{
    public required string FeatureId { get; init; }
    public required string FeatureName { get; init; }
    public required string FeatureType { get; init; }
    public required bool Suppressed { get; init; }
    public string? ParentFeatureId { get; init; }
    public required IReadOnlyList<FeatureParameter> Parameters { get; init; }
}

[JsonPolymorphic(TypeDiscriminatorPropertyName = "kind")]
[JsonDerivedType(typeof(FeatureParameterString),  "string")]
[JsonDerivedType(typeof(FeatureParameterNumber),  "number")]
[JsonDerivedType(typeof(FeatureParameterBoolean), "boolean")]
[JsonDerivedType(typeof(FeatureParameterRef),     "ref")]
public abstract record FeatureParameter
{
    public required string Name { get; init; }
}

public record FeatureParameterString : FeatureParameter
{
    public required string Value { get; init; }
}

public record FeatureParameterNumber : FeatureParameter
{
    public required decimal Value { get; init; }
    public required string Unit { get; init; }
}

public record FeatureParameterBoolean : FeatureParameter
{
    public required bool Value { get; init; }
}

public record FeatureParameterRef : FeatureParameter
{
    public required string EntityId { get; init; }
}
```

`FeatureParameterNumber` uses `decimal` to eliminate floating-point nondeterminism in hashing. See Section 13 for the canonical decimal format.

## 9. AddinSessionId and Epoch

`AddinSessionId` is generated by the add-in deterministically from `(machineGuid, swProcessId, loadTimestamp)`:

```csharp
public static string GenerateAddinSessionId()
{
    var machineGuid = GetMachineGuid();
    var swProcessId = Process.GetCurrentProcess().Id;
    var loadTimestamp = DateTime.UtcNow.Ticks;
    var input = $"{machineGuid}:{swProcessId}:{loadTimestamp}";
    var hash = SHA256.HashData(Encoding.UTF8.GetBytes(input));
    return Convert.ToHexString(hash)[..32].ToLowerInvariant();
}
```

**Epoch rules (enforced by consumers in SPEC-03 and SPEC-04):**

- Every `IClientMessage` and `IAddinMessage` carries exactly one `AddinSessionId` at the top-level record. Nested duplicates inside `StateSnapshot` or other records are forbidden.
- **`AddinReadyMessage` is exempt from epoch validation.** It is the sole mechanism for establishing or changing the current epoch.
- **Initial connection:** Before the first `AddinReadyMessage` is received, no epoch exists. The orchestrator MUST accept the first `AddinReadyMessage` and record its `AddinSessionId` as the current epoch. All non-`AddinReadyMessage` messages received before the first epoch is established MUST be rejected with `AddinProtocolErrorCode.StaleEpoch`.
- **Steady state:** After an epoch is established, any non-`AddinReadyMessage` with an `AddinSessionId` other than the current epoch is rejected with `AddinProtocolErrorMessage(ErrorCode: StaleEpoch)`.
- **Epoch transition:** When a subsequent `AddinReadyMessage` arrives with a different `AddinSessionId`, the orchestrator atomically transitions to the new epoch and resets its view of `StateVersion` to 0.

## 10. Protocol Versioning

`ProtocolVersion.Current` is a const string. Initial value: `"1.0.0"`. Semantic versioning semantics but pre-1.0 is not permitted for this project.

**Handshake rule:**

- Add-in sends `AddinReadyMessage` with `ProtocolVersion = Current`
- Orchestrator compares to its own `Current`
- Exact string match required. Mismatch → orchestrator sends `ClientProtocolErrorMessage(ErrorCode: VersionMismatch)` with both versions populated, then closes the WebSocket.
- No negotiation, no downgrade, no "minor version compatible." This is hackathon scope; negotiation is post-hackathon.

## 11. Tool Registry and Schema Generation

```csharp
[AttributeUsage(AttributeTargets.Class)]
public sealed class ToolNameAttribute : Attribute
{
    public string Name { get; }
    public ToolNameAttribute(string name) { Name = name; }
}

public static class ToolRegistry
{
    public static IReadOnlyList<ToolMetadata> All { get; }
    public static ToolMetadata? GetByName(string toolName);
    public static Type? InputType(string toolName);
    public static Type? ResultType(string toolName);
}

public record ToolMetadata(
    string Name,
    Type InputType,
    Type ResultType,
    TimeSpan Timeout);
```

`ToolRegistry.All` reflects the Protocol assembly at static construction time, finding every type with `[ToolName]` attributes that implement `IToolInput` or `IToolResult`.

**Single source of truth for tool names:** The `[ToolName]` attribute is the authoritative identity for every tool. The `[JsonDerivedType]` discriminator value on `IToolInput` and `IToolResult`, the `ToolTimeouts` table key, and the Anthropic schema name MUST all exactly match the `[ToolName]` value. `ToolNameConsistencyTests` (build-breaking) verifies this for every registered tool.

**Uniqueness invariant:** Each tool name maps to exactly one input type and exactly one result type. `ToolNameUniquenessTests` (build-breaking) fails if multiple input or result types share the same `[ToolName]` value.

`ToolRegistryCompletenessTests` asserts every registered input has a matching result and vice versa.

## 12. Per-Tool Timeouts

`ToolTimeouts.ForTool(string toolName)` returns a `TimeSpan`. Values:

| Tool | Timeout |
|------|--------|
| default | 30 s |
| `extrude`, `cut_extrude` | 90 s |
| `fillet`, `chamfer` | 90 s |
| `hole_wizard` | 120 s |
| `linear_pattern`, `circular_pattern` | 180 s |
| `modify_feature_parameter` | 120 s |
| `screenshot` | 15 s |
| `get_feature_tree` | 15 s |

Consumers in SPEC-03 enforce these as the per-call timeout. Changes to this table require an SDR update in SPEC-00.

## 13. Hash Canonicalization

`HashCanonicalizer.ComputeFeatureTreeHash(IReadOnlyList<FeatureNode>)` and `.ComputeRecordHash(object, IReadOnlyList<string>)` produce deterministic SHA-256 hex strings.

**Rules:**

- Input is serialized with a fixed `JsonOptions` instance: camelCase properties, PascalCase enum strings, sorted object keys, null properties omitted.
- The output is a lowercase hex SHA-256 of the UTF-8 encoded canonical JSON.
- The null-document sentinel is the literal string `"NO_DOCUMENT"`, not a hash of anything.

**Decimal format:** Decimals are serialized in invariant culture as plain decimal notation. Scientific notation (the characters `E` and `e`) MUST NOT appear in the output. Trailing fractional zeros are stripped. The reference implementation is `HashCanonicalizer.FormatDecimal(decimal)`, which calls `value.ToString("G29", CultureInfo.InvariantCulture)` and post-processes any scientific-notation output into expanded decimal form. `HashDecimalFormatTests` verifies no scientific notation appears for edge-case values (10^−28, 10^28, 0, decimal.MaxValue, decimal.MinValue).

**Array ordering rules for hashed types:** Arrays are hashed in their serialized order. Producers MUST emit arrays in canonical order for any type that participates in hashing:

- `FeatureNode` lists (in `StateSnapshot.FeatureTree` and `GetFeatureTreeResult.Tree`): ordered by `FeatureId`, lexicographic, ordinal, case-sensitive.
- `FeatureParameter` lists (in `FeatureNode.Parameters`): ordered by `Name`, lexicographic, ordinal, case-sensitive.
- `SelectionRef` lists (in `StateSnapshot.ActiveSelection`): `PersistentSelectionRef` entries first (ordered by `Entity.PersistentId`), then `SketchSelectionRef` entries (ordered by `SketchEntity.LocalId`), all ordinal, case-sensitive.

Property-based tests (`HashCanonicalization_PropertyTests` via FsCheck) generate 1000 randomly shaped `FeatureNode` trees and assert that logical equality (including reordered-but-equivalent arrays) implies hash equality.

## 14. Validation

Every `IToolInput` has a matching `AbstractValidator<T>` in `Validation/Validators/`. FluentValidation. Rules:

- Range/Required/Enum/MinLength/MaxLength rules translate to JSON Schema constraints via `JsonSchemaGenerator`.
- Cross-field conditional rules (e.g., "Secondary required when Mode = LinearTwoEntity") are implemented in the validator but do NOT translate to JSON Schema constraints. They appear as `description` text only.

**Message-envelope validation:** In addition to tool-input validators, the following message types have envelope validators:

- `ToolResultMessageValidator`: enforces the Success/Result/Error invariant (see Section 5).
- `StateSnapshotValidator`: enforces null-document rules (see Section 8) — when `IsNullDocumentState` is true, `Units` must be null, `DocumentId` must be null, `FeatureTree` must be empty, `FeatureTreeHash` must be `"NO_DOCUMENT"`, and `IsSketchActive` must be false.

Two tests enforce the schema sync split:

- `ValidationSchemaSyncTests` (build-breaking): every mechanical validator rule has a matching JSON Schema constraint.
- `SemanticHintCoverageTests` (`[Trait("Category", "Advisory")]`, non-breaking): cross-field rules appear in schema descriptions.

Advisory tests run in a separate CI step with `continue-on-error: true` so they report without blocking.

## 15. Test Requirements (Build-Breaking)

Required tests in `BSVoiceCAD.Protocol.Tests`. Failure of any of these fails CI.

- `SerializationTests`: every record round-trips byte-identical
- `PolymorphicDeserializationTests`: every `[JsonDerivedType]` round-trips through the base interface
- `JsonDerivedTypeRegistrationCompletenessTests`: every concrete implementation of each polymorphic interface is registered
- `JsonNamingPolicyConsistencyTests`: every serialized property name is camelCase
- `JsonEnumSerializationTests`: every enum serializes as its PascalCase member name (never integer)
- `ToolRegistryCompletenessTests`: every tool has paired Input + Result + Timeout
- `ToolNameConsistencyTests`: `[ToolName]` value matches `[JsonDerivedType]` discriminator, timeout table key, and schema name for every tool
- `ToolNameUniquenessTests`: no two input or result types share the same `[ToolName]` value
- `HashCanonicalizationTests`: equality-invariant + empty-input + NO_DOCUMENT sentinel
- `HashCanonicalization_PropertyTests` (FsCheck): 1000 randomized trees, including reordered-but-equivalent arrays
- `HashDecimalFormatTests`: edge-case decimal values produce no scientific notation
- `ValidationTests`: every validator accepts valid and rejects invalid inputs
- `ValidationSchemaSyncTests`: mechanical rules match schema
- `MessageEnvelopeInvariantTests`: ToolResultMessage Success/Result/Error combinations; StateSnapshot null-document invariants
- `ArchitectureTests`: no DTO types defined outside `BSVoiceCAD.Protocol.*`

## 16. Out of Scope

- State machine for tool-call lifecycle → SPEC-03
- DHF schema and hash chain → SPEC-04
- SolidWorks COM service contracts → SPEC-02
- System prompt, retry correlation, confirmation flow, vision-context enrichment → SPEC-05
- Build sequence, slice prompts, CLAUDE.md → deferred to assembly after module specs converge
- NetFx manual discriminator-switch deserializer: covered in SPEC-02 (because it's add-in implementation, not a wire contract; the wire is identical for both runtimes)
- Chunked-transfer / streaming protocol for messages exceeding 4 MiB → deferred to a future protocol version
- Remote (non-localhost) deployment support → deferred; see Section 0 deployment constraint

## 17. Consumption by Other Specs

SPEC-02 imports `IToolInput`, `IToolResult`, `ToolError`, `ToolErrorCode`, `StateSnapshot`, `SelectionRef`, `TimingReport`, and all `ISolidWorks*Service`-facing types.

SPEC-03 imports all four polymorphic message families, `ToolRegistry`, `ToolTimeouts`, `UndoGroupOutcome`, `CancelReason`, `RollbackReason`, `ClientProtocolErrorCode`, `AddinProtocolErrorCode`.

SPEC-04 imports `IToolInput`, `IToolResult`, `ToolError`, `StateSnapshot`, `FeatureNode`, `HashCanonicalizer`, `UndoGroupOutcome`.

SPEC-05 imports all message records, `ToolRegistry`, and `AddinProtocolErrorCode` codes.

SPEC-06 imports `StateSnapshot`, `SelectionRef`, `FeatureNode` (for display), and nothing else.

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*