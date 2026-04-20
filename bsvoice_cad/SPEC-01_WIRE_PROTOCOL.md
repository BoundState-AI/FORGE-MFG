---
title: "BSVoice-CAD SPEC-01 — Wire Protocol"
subtitle: "Cross-process types, polymorphic contracts, JSON policy, version handshake, TLS + auth handshake"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-01 — Wire Protocol

**Version:** 1.4.0
**Status:** DRAFT — cross-pass R3 findings applied + peer review editorial clarifications + R4 editorial (H4 correction)
**Depends on:** SPEC-00 v1.5.0 (module boundaries, deployment SDRs, SDR-TL-013 recovery persistence, SDR-TL-014 AddinTeardownMessage declaration), `DEPLOYMENT_V1_V2_MIGRATION.md` v1.1.0 (v1/v2 deployment boundary, handshake failure transport)
**Consumed by:** SPEC-02, SPEC-03, SPEC-04, SPEC-05, SPEC-06 (display types only), SPEC-07 (via SPEC-05)

**Changelog v1.3.0 → v1.4.0:**
- **§3 malformed/unknown message split (R3 R1-ISSUE-009):** The `MalformedEnvelope` vs `UnknownMessageType` error-code semantics are now split clean. `MalformedEnvelope` applies to unparseable JSON, invalid UTF-8, missing or malformed required envelope fields, and envelope-invariant violations (e.g., `Success=true` with `Error` populated). `UnknownMessageType` applies ONLY to well-formed envelopes whose `type` discriminator deserializes successfully but names a value the receiver does not recognize (e.g., a newer protocol version adding a message type the receiver's build does not know about). Both error-code enums (`ClientProtocolErrorCode`, `AddinProtocolErrorCode`) already carry both values; this change codifies the boundary between them. SPEC-02 §6.5 aligns with this split.
- **§4.2 AddinTeardownMessage (R3 R1-ISSUE-007, SDR-TL-014):** `AddinTeardownMessage` added to the `IAddinMessage` polymorphic family with discriminator `"addin_teardown"`. Declares the add-in's intent to shut down gracefully. Allows SPEC-04 to close the DHF session with reason `explicit_shutdown` distinguishably from transport loss. Send conditions documented in SPEC-02 v1.4.0 §9.
- **§5 CallId canonical format (R3 R1-ISSUE-011):** `CallId` is now specified as a UUIDv7 string canonically in SPEC-01. Previously SPEC-02 §6.5 validated as UUIDv7 while SPEC-01 left the format open — producer and consumer could disagree while both remained spec-compliant. SPEC-01 is now the authoritative source; SPEC-02 references SPEC-01 for the format.
- **§9 reject same-epoch reconnect with different capability fields (R3 R1-ISSUE-006):** When a reconnect arrives with the same `AddinSessionId` but different `AddinVersion`, `SwVersion`, `SwBuild`, or `ScreenshotRootPath`, the orchestrator now REJECTS the reconnect with `ClientProtocolErrorMessage(ErrorCode: StaleEpoch)` rather than silently accepting the updated fields. Rationale: SPEC-04 SDR-04-010 treats those columns as immutable once written, and `guard_session_update` trigger rejects any UPDATE to them. A legitimate capability change means the add-in has restarted (e.g., after SolidWorks update), which produces a new `AddinSessionId` via the SPEC-02 §8 algorithm. Same `AddinSessionId` with different capabilities is an illegal state.
- **§12.3 API secret header redaction (R3 R1-ISSUE-007 acknowledged):** The API key travels in `Sec-WebSocket-Protocol` on the upgrade request. This header may be logged by Railway edge, reverse proxies, and APM tooling. Suppressing the echoed response header does not prevent request-side logging. Post-hackathon migration to Authorization header with short-lived bearer tokens is documented as the v1.5 path; v1 mitigation is mandatory Railway logging configuration that redacts the `Sec-WebSocket-Protocol` request header (see `DEPLOYMENT_V1_V2_MIGRATION.md` §7 v1.1.0).
- **§12.4 pre-upgrade failure transport (R3 R1-ISSUE-005):** Handshake failures detected during the HTTP upgrade request are returned as HTTP responses (400/401/429), NOT as WebSocket close codes. A Close frame cannot be sent before the WebSocket upgrade completes. Close code 4401 is reserved EXCLUSIVELY for post-upgrade credential revocation (key revoked while a connection is live). This matches migration doc v1.1.0 §2.
- **§12.6 in-memory lockout for v1 (R3 R2-ISSUE-001):** API-key failed-attempt lockout is specified as in-memory only in v1, with documented limitations (resets on orchestrator restart, does not replicate across multiple orchestrator instances). The previously-referenced `api_keys.lockout_event` table does not exist and is not introduced in v1; persistence is deferred to post-hackathon when operational tooling warrants the schema cost.
- **§12.7 heartbeat close code 1006 → TCP abort (R3 R1-ISSUE-005 CRITICAL):** RFC 6455 §7.4.1 forbids sending close code 1006 in a Close frame. The heartbeat-timeout path is now specified as a TCP-level abort (close the underlying socket without sending a Close frame), which the peer observes locally as a 1006 abnormal closure. .NET's `ClientWebSocket` / `WebSocket.Abort()` is the mechanism. All other close code references (1000 clean, 1001 Going Away, 1002 protocol error, 4401 post-upgrade auth revocation) remain valid and unchanged.
- **§16 tests added:** `HeartbeatTimeoutTcpAbortTests`, `SameEpochReconnectRejectionTests`, `UnknownMessageTypeVsMalformedEnvelopeTests`, `AddinTeardownMessageSerializationTests`, `CallIdUuidV7FormatTests`, `InMemoryLockoutTests`.
- **Depends-On updated** to reflect SPEC-00 v1.5.0 and migration doc v1.1.0 dependencies.

**Editorial v1.4.0+ (post-R3 peer review, no version bump):**
- **m1** — §5 AddinSessionId sentinel: clarified that "extracted" means parsed as a string (Interpretation A); format-invalid values are echoed as received for diagnostics rather than triggering an alternate failure path.
- **m2** — §5 AddinSessionId: cross-reference added to SPEC-02 §8's generator collision analysis. The all-zeros reserved sentinel is statistically unreachable (2^-128 per SHA-256 output) given the generator's non-constant input mixture; sentinel uniqueness is probabilistic, not deterministic.
- **m3** — §3 `MessageTooLarge` handling: added a bullet clarifying that the resulting `ClientProtocolErrorMessage` / `AddinProtocolErrorMessage` always carries `RejectedMessageId = null`, because an oversize-rejected envelope was never parsed and no `messageId` was ever extracted.
- **m4** — §9.1 Case B: `ProtocolVersion` drift on same-`AddinSessionId` reconnect is an illegal state for the same reason as capability drift; clarified which close code / HTTP status applies to which drift type.
- **m5** — §9.1 Case B: add-in's `AddinSessionId` regeneration policy is now explicit — regeneration happens ONLY when the add-in observes both `StaleEpoch` AND close code 1008 with reason prefix `capability_drift`. Other `StaleEpoch` flavors do NOT require regeneration; the add-in retries with its current `AddinSessionId` after backoff.
- **m6** — §12.4 post-upgrade credential revocation: specified that the *detection* mechanism (poll, `LISTEN/NOTIFY`, lazy-check-on-next-message) is SPEC-03 session-lifecycle territory; SPEC-01 specifies the close-code contract only.
- **R4 H4 correction (editorial)** — §9.1 Case B "ProtocolVersion drift" note no longer references "HTTP 400 during HTTP upgrade." `ProtocolVersion` travels in `AddinReadyMessage`, which is sent AFTER the WebSocket upgrade completes — so HTTP 400 is structurally impossible for this failure. Rewritten to state: mismatch is caught by §10's version-mismatch handling with close code 1002 and a `VersionMismatchError` payload. HTTP 400 remains reserved for pre-upgrade failures per §12.4.

**Changelog v1.2.0 → v1.3.0:**
- §0 scope: co-location assumption reversed. Orchestrator is Railway-hosted at `vcad.boundstate.ai`; add-in runs local on Windows workstation; WebSocket crosses public internet as `wss://` with TLS termination at Railway edge.
- New §12: TLS + API-key handshake. Spec is authoritative for v1 auth; v2 OAuth evolution documented in the migration doc.
- §9: `GenerateAddinSessionId` algorithm REMOVED. SPEC-01 owns format contract (`^[0-9a-f]{32}$`), epoch rotation semantics, and `AddinSessionIdFormatValidator`. Algorithm authoritative in SPEC-02 §8.
- §5: `AddinReadyMessage` gains required `ScreenshotRootPath` field (add-in declares its screenshot root to the orchestrator; relevant post-v1 when S3 URLs replace base64 inline — see migration doc §3).
- §6.1: `ToolErrorCode` adds `InvalidUndoState` and `TemplateNotFound` (referenced by SPEC-02 but previously not in the enum).
- §6.3 new: `ToolCallLifecycleState` enum added (`Sent | Completed | TimedOut | Cancelled | Abandoned`). SPEC-04 imports this type; previously implicit.
- §7.1 `ScreenshotResult`: complete rewrite. No filesystem path. Carries `screenshotBase64` (v1, nullable, capped at 2 MiB post-encode) and `screenshotUrl` (v2, nullable). Envelope invariant: exactly one of the two is non-null. Both carry `sha256Hash`, `widthPx`, `heightPx`. This is the v1/v2 transport boundary; the record shape is stable across both versions.
- §3: malformed JSON parse-failure behavior clarified. Under-4-MiB messages that fail JSON parse produce `MalformedEnvelope` error; connection stays open; subsequent messages proceed normally.
- Per-section numbering adjusted for the new §12. Old §12 (Per-Tool Timeouts) is now §13, old §13 is now §14, etc. Cross-references inside the doc updated.

## 0. Scope

This spec defines every type that crosses a process boundary in BSVoice-CAD, plus the boundary-level behavioral rules that govern how those types are validated at the wire edge (epoch rejection, version-match handshake, per-tool timeouts, message-envelope invariants, TLS + authentication handshake).

All other specs reference types defined here and add nothing of their own to the wire format.

**In scope:** polymorphic message hierarchies, tool input/result records for all mandatory tools, shared enums and records (including the `ToolCallLifecycleState` enum consumed by SPEC-03 and SPEC-04), JSON serialization policy, protocol versioning, handshake message shapes, TLS + API-key authentication handshake, boundary validation rules, `CallId` format (UUIDv7).

**Out of scope:** state machines (SPEC-03), DHF schema (SPEC-04), COM service contracts (SPEC-02), agent loop logic (SPEC-05), OAuth device-flow implementation (v2 — see `DEPLOYMENT_V1_V2_MIGRATION.md` §2), vocabulary resolution (SPEC-07). Stretch tool field definitions are deferred; their records exist as empty placeholders with polymorphic registrations to maintain registry completeness (see Section 7.2).

**Deployment model — Railway-hosted orchestrator, local add-in:** The orchestrator runs on Railway at `vcad.boundstate.ai`. The add-in runs on the engineer's Windows workstation alongside SolidWorks. The WebSocket between them crosses the public internet over TLS (`wss://vcad.boundstate.ai/ws`). TLS is terminated at the Railway edge; the orchestrator process speaks plain WS internally but the contract described here is `wss://`. Authentication is API key in the WebSocket handshake (v1 only); v2 uses OAuth 2.0 device flow per `DEPLOYMENT_V1_V2_MIGRATION.md` §2.

**This explicitly reverses the earlier co-location constraint.** Prior drafts assumed orchestrator and add-in shared a local filesystem and a localhost WebSocket. That model is dead. Screenshots are transported inline as base64 (v1) or as S3 presigned URLs (v2) — never as local filesystem paths. See §7.1 for the `ScreenshotResult` contract.

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
│   ├── AddinTeardownMessage.cs             # v1.4.0 — graceful shutdown signal
│   ├── UndoGroupOutcomeMessage.cs
│   └── AddinProtocolErrorMessage.cs
├── Tools/
│   ├── Inputs/                             # One record per tool
│   ├── Results/                            # One record per tool
│   ├── Common/
│   │   ├── ToolError.cs
│   │   ├── ToolErrorCode.cs                # enum
│   │   ├── ToolCallLifecycleState.cs       # enum
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
│   │   ├── TeardownReason.cs               # enum (v1.4.0)
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
├── Transport/
│   ├── ApiKeyHandshake.cs                  # v1 subprotocol parser + validator
│   ├── InMemoryKeyLockoutTracker.cs        # v1.4.0 — per-keyId failed-attempt counter
│   └── HandshakeResult.cs
├── Serialization/
│   ├── JsonOptions.cs                      # camelCase, string enums
│   └── HashCanonicalizer.cs
└── Validation/
    ├── Validators/                         # One per tool input + message envelope
    ├── AddinSessionIdFormatValidator.cs
    ├── CallIdFormatValidator.cs            # v1.4.0 — UUIDv7 validator
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
- `IAddinMessage`: discriminator property `"type"` with values like `"tool_result"`, `"state_update"`, `"addin_ready"`, `"addin_teardown"` (v1.4.0)
- `PlaneRef`: discriminator `"kind"` with values `"standard"` or `"entity"`
- `DimensionTarget`: discriminator `"kind"` with values `"sketch"` or `"persistent"`
- `SelectionRef`: discriminator `"kind"` with values `"persistent"` or `"sketch"`
- `FeatureParameter`: discriminator `"kind"` with values `"string"`, `"number"`, `"boolean"`, `"ref"`

## 3. Wire Framing

One complete JSON document per WebSocket **message** (not per frame). The application-level receive loop MUST call `ReceiveAsync` repeatedly, accumulating returned segments into a buffer, until `WebSocketReceiveResult.EndOfMessage` is `true`. Only then is the accumulated buffer a complete message ready for deserialization. A single logical message may span multiple `ReceiveAsync` calls when the message size exceeds the receive buffer. No NDJSON.

**Maximum single-message size: 4 MiB.** This limit applies to the fully reassembled message.

**Oversize message handling:** If the accumulated buffer exceeds 4 MiB before `EndOfMessage` is received, the receiver MUST:

1. Stop buffering additional data.
2. Continue calling `ReceiveAsync` and discarding the returned bytes until `EndOfMessage` is `true` (drain-and-discard), to maintain WebSocket stream synchronization.
3. Send a protocol error message (`ClientProtocolErrorCode.MessageTooLarge` or `AddinProtocolErrorCode.MessageTooLarge`).
4. Leave the WebSocket connection open; subsequent messages proceed normally.

Messages that are fully reassembled at or under 4 MiB are deserialized normally. Messages fully reassembled above 4 MiB (possible if the receiver's buffer is ≥ 4 MiB) MUST be rejected without deserialization, with the same protocol error.

**RejectedMessageId on MessageTooLarge (v1.4.0+ editorial m3):** the resulting `ClientProtocolErrorMessage` / `AddinProtocolErrorMessage` always carries `RejectedMessageId = null`. The envelope is rejected before it is parsed, so `messageId` is never extracted; there is no meaningful value to echo. Consumers of protocol-error messages MUST NOT assume `RejectedMessageId` is populated when `ErrorCode == MessageTooLarge`.

### 3.1 Error-Code Split: MalformedEnvelope vs UnknownMessageType

The protocol distinguishes two different receiver-side failure modes for incoming messages, each mapping to a distinct error code. This split is authoritative and MUST be implemented consistently by both peers (orchestrator, SPEC-02 add-in).

**`MalformedEnvelope`** is emitted when the received bytes cannot be deserialized into a valid typed message. Concrete triggers:

- Invalid UTF-8 byte sequence in the payload
- `JsonSerializer.Deserialize` throws (syntax error, unexpected token, unterminated string, etc.)
- A required envelope field is missing or has wrong type (`messageId` not a string, `addinSessionId` missing, `type` discriminator absent or not a string)
- A typed envelope invariant is violated after deserialization succeeds (e.g., `ToolResultMessage` with `Success=true` but `Result=null` and `Error` populated — see Section 5)
- `AddinSessionId` fails format validation (`^[0-9a-f]{32}$` mismatch)
- `CallId` fails format validation (not UUIDv7 — see Section 5)
- `ScreenshotResult` envelope invariant violation (neither or both of `ScreenshotBase64` / `ScreenshotUrl` populated)

The payload is rejected as unparseable/invalid. Both peers produce `MalformedEnvelope` for these conditions.

**`UnknownMessageType`** is emitted when deserialization succeeds far enough to extract the `type` discriminator cleanly, but the discriminator value is not recognized by the receiver. Concrete triggers:

- `type: "some_future_message"` where the receiver's build does not have a `[JsonDerivedType]` registration for that value
- The peer sent a message type added in a newer protocol version the receiver doesn't yet know

Semantically: the envelope is structurally well-formed, and the `type` field is a string that parses — but the value isn't in the receiver's dispatch table.

**Why the split matters:** These are genuinely different failures. `MalformedEnvelope` means the peer's message is corrupt or broken. `UnknownMessageType` means the peer is using a protocol vocabulary the receiver doesn't know — which is a version-skew signal, not a corruption signal. A reconnect loop SHOULD handle them differently: `MalformedEnvelope` from the same peer repeatedly suggests a bug, `UnknownMessageType` repeatedly suggests the peer needs to upgrade.

The receiver MUST:

1. Log the parse failure with the raw UTF-8 byte length and the first 512 bytes of the payload (for diagnostics). For `UnknownMessageType`, the log entry also records the unrecognized discriminator value.
2. Send a protocol error message with the appropriate `ErrorCode`: the orchestrator sends `ClientProtocolErrorMessage`, the add-in sends `AddinProtocolErrorMessage`.
3. Set `RejectedMessageId` to `null` when the parse failure prevented extraction of the `messageId` field; otherwise echo it. For `UnknownMessageType`, `messageId` is generally extractable (same envelope structure) and SHOULD be echoed.
4. Leave the WebSocket connection open. Subsequent messages proceed normally. A single malformed or unknown-type message does NOT terminate the session.
5. Do NOT attempt partial recovery (e.g., "re-parse with a different discriminator"). Reject the whole envelope.

Repeated malformed messages from the same peer within a short window (>5 within 10 seconds) MAY trigger a session close with WebSocket close code 1002 ("protocol error"), at the receiver's discretion. This is a DOS mitigation, not a correctness rule. Repeated `UnknownMessageType` messages are not a DOS indicator (the peer is attempting to talk to you legitimately, just in a vocabulary you don't understand); they SHOULD be logged at `LogLevel.Warning` rather than triggering closure.

No chunked-transfer or streaming protocol is defined in this version. For Demo 1 / hackathon scope, models are expected to remain well under the 4 MiB limit. If the embedded `FeatureTree` in a `StateSnapshot` would cause the enclosing message to exceed the limit, the add-in MUST truncate the tree and set `IsFeatureTreeTruncated = true` (see Section 8). The orchestrator MAY then call `get_feature_tree` as a separate tool invocation to retrieve the complete tree in a dedicated message. A full pagination mechanism for very large feature trees is deferred to a future protocol version.

Screenshots are transported inline as base64 (v1) — see §7.1.

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
[JsonDerivedType(typeof(AddinTeardownMessage),         "addin_teardown")]   // v1.4.0
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

**CallId format (v1.4.0):** `CallId` is a **UUIDv7 string** in the canonical UUID textual representation (`xxxxxxxx-xxxx-7xxx-yxxx-xxxxxxxxxxxx` where the version digit is `7` and the variant bits encode to a `y` in `{8, 9, a, b}`). The format is enforced by `CallIdFormatValidator` (see §15) which applies the regex `^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` to the lowercased value. UUIDv7 is chosen because:
- it carries a millisecond-precision Unix timestamp in the first 48 bits, enabling time-sorted indexes in SPEC-04's `dhf.tool_calls` table;
- it is collision-resistant (74 random bits after timestamp) which is sufficient for the expected per-session call volumes;
- it is a stable RFC 9562 standard format with first-class .NET support (`Guid.CreateVersion7()` on .NET 9+; backport or NuGet implementation on earlier runtimes).

SPEC-03 is authoritative for `CallId` allocation mechanics (who generates, when, and lifecycle rules). SPEC-01 is authoritative for format validation — both producer (SPEC-03 on the orchestrator) and consumer (SPEC-02 add-in) MUST validate every `CallId` they send or receive against `CallIdFormatValidator`. A message carrying a malformed `CallId` is rejected with `MalformedEnvelope`.

```csharp
public record ToolCallMessage : IClientMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string CallId { get; init; }            // UUIDv7; see Section 5 format spec
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
    public string? RejectedMessageId { get; init; }
}
```

**ClientProtocolErrorMessage AddinSessionId rule:** When the orchestrator sends `ClientProtocolErrorMessage` in response to a received add-in message (including during handshake, before an epoch is established), it MUST set `AddinSessionId` to the value from the triggering message's `AddinSessionId` field. When the triggering message is malformed and `AddinSessionId` cannot be extracted, the orchestrator uses a reserved sentinel value `00000000000000000000000000000000` (32 zero hex chars) to maintain format validity while signaling "no meaningful epoch."

**"Extracted" defined (v1.4.0+ editorial m1):** the orchestrator attempts to parse the envelope's `addinSessionId` field as a JSON string. If the field is present and a string — *regardless of whether it matches the `^[0-9a-f]{32}$` format* — the extracted string is echoed as-is in the `ClientProtocolErrorMessage`. Format-invalid values are echoed verbatim for diagnostic value (a truncated or miscased ID is still more useful to the add-in operator than the all-zeros sentinel). The all-zeros sentinel is reserved for the narrower case where the field is missing, non-string, or the envelope itself could not be parsed far enough to read it.

**Sentinel uniqueness (v1.4.0+ editorial m2):** SPEC-02 §8 owns the `AddinSessionId` generator algorithm. Its SHA-256 output over a non-constant input mix (machine ID, SolidWorks PID, load timestamp, random nonce) makes the all-zeros value statistically unreachable — the collision probability is 2^-128 per generation. Sentinel uniqueness is therefore probabilistic, not deterministic. For additional defense-in-depth, SPEC-02 §8.1 documents an optional regenerate-on-all-zeros check so no production output ever equals the reserved sentinel.

```csharp
public record ToolResultMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string CallId { get; init; }            // UUIDv7; matches ToolCallMessage.CallId
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
- Any other combination is a validation error. The receiver MUST reject the invalid envelope with a protocol error message carrying `MalformedEnvelope` as the error code (using the appropriate directional message type: `ClientProtocolErrorMessage` when the orchestrator rejects an add-in message, `AddinProtocolErrorMessage` when the add-in rejects an orchestrator message).

```csharp
public record StateUpdateMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required StateUpdateTrigger Trigger { get; init; }
    public required StateSnapshot State { get; init; }
    public string? CorrelatedCallId { get; init; }          // UUIDv7 when set; matches the triggering ToolCallMessage.CallId
}

public record AddinReadyMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required string AddinVersion { get; init; }
    public required string SwVersion { get; init; }
    public required string SwBuild { get; init; }
    public required string ProtocolVersion { get; init; }
    public required string ScreenshotRootPath { get; init; }   // Add-in's local screenshot root
    public required string WorkstationId { get; init; }        // Client-supplied tenant hint (see SPEC-04 SDR-04-008)
}
```

**`ScreenshotRootPath`:** the add-in's local filesystem path where it captures PNGs before encoding. The orchestrator does NOT read from this path — the filesystem is on the add-in's workstation, not on Railway. The field exists for two reasons: (a) audit trail completeness (DHF records the add-in's capture context), (b) v2 forward-compatibility (when screenshots move to S3 presigned URLs, the add-in will continue to stage PNGs at `ScreenshotRootPath` locally before uploading; the field reminds both sides that a local staging area exists).

**`WorkstationId`:** `Environment.MachineName` on the add-in's Windows workstation. In v1, this is a client-supplied tenant hint used by SPEC-04 for `dhf.sessions.workstation_id` and the cross-workstation conflict check (SDR-04-008). It is NOT a security trust boundary — trust flows from the API key authenticating the WebSocket, which scopes operations to a tenant. A spoofed `WorkstationId` within a tenant cannot escalate across tenants.

```csharp
public record AddinTeardownMessage : IAddinMessage
{
    public required string MessageId { get; init; }
    public required string AddinSessionId { get; init; }
    public required TeardownReason Reason { get; init; }
    public string? DiagnosticMessage { get; init; }            // Optional human-readable detail
}

public enum TeardownReason
{
    UserInitiated,             // User clicked "Disconnect" in SW Add-in Manager or equivalent UX path
    SolidWorksShutdown,        // SolidWorks signaled clean add-in unload (ISwAddin.DisconnectFromSW callback)
    AddinDisabled,             // Add-in was disabled via SW Add-in Manager while running
    HostProcessExit            // Add-in host process is terminating gracefully (ctrl-C in dev harness, service stop)
}
```

**`AddinTeardownMessage` semantics (SDR-TL-014):** Emitted by the add-in before process exit when graceful shutdown is possible. The orchestrator receives the message, marks the current DHF session as closed with reason `explicit_shutdown` (SPEC-04 §5.2), and drains any in-flight tool calls to `Cancelled` or `Abandoned` terminal states per SPEC-03. The add-in's send conditions — which code paths emit this message, and what the add-in does if the message cannot be sent due to transport loss — are specified in SPEC-02 v1.4.0 §9.

**Teardown-message handling when graceful shutdown is not possible:** If the add-in process crashes, is force-killed, or the OS shuts down without giving the add-in time to send `AddinTeardownMessage`, the orchestrator will detect the closure as `heartbeat_timeout` per SPEC-00 §6 glossary. The orchestrator CANNOT distinguish these cases from the `AddinTeardownMessage`-was-lost-in-transit case; both are recorded as `heartbeat_timeout` with cause "unknown." This is acceptable because the DHF audit trail's integrity does not depend on closure attribution — append-only semantics protect prior entries regardless of why the session ended.

**No explicit orchestrator acknowledgement required:** The orchestrator does NOT send a reply to `AddinTeardownMessage`. The add-in SHOULD send the message and then close its WebSocket (with close code 1000) within a short grace window (2 seconds recommended) without waiting for a reply. If the orchestrator's reply would have raced the add-in's socket close, the message is assumed delivered; cross-checking is the responsibility of DHF closure-reason auditing post-session, not of the teardown handshake.

```csharp
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
    EntityNotFound, UnitAmbiguous, InvalidUndoState, TemplateNotFound,

    // Runtime
    TimedOut, Cancelled, InternalError, ComException, ValidationFailed,
    ScreenshotCaptureFailed,

    // Protocol
    InFlightDuplicate, UnknownCallId, VersionMismatch, UnknownMessageType, StaleEpoch,

    // Business rule
    ConfirmationDenied, RetryLimitExceeded
}
```

**`InvalidUndoState`:** produced when a tool call arrives that requires an active undo group (execution tools) but no `StartUndoGroupMessage` has been received, or arrives when an undo group is already committed/rolled-back. SPEC-02 §5 maps this case.

**`TemplateNotFound`:** produced by `new_part` when the requested template name does not match any registered SolidWorks part template on the add-in's machine. SPEC-02 §5 maps this case.

**`ScreenshotCaptureFailed` (v1.4.0):** produced by `screenshot` when the capture pipeline exhausts its size-reduction iterations without fitting within the 2 MiB `ScreenshotBase64` cap, or when the underlying SolidWorks viewport capture API returns an error. SPEC-02 v1.4.0 §7.2 specifies the terminal behavior.

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

### 6.3 ToolCallLifecycleState

```csharp
public enum ToolCallLifecycleState
{
    Sent,             // Dispatched to add-in; no terminal yet
    Completed,        // Terminal: tool returned a result
    TimedOut,         // Terminal: orchestrator-side timeout fired
    Cancelled,        // Terminal: cancel message acknowledged before result
    Abandoned         // Terminal: session closed or epoch rotated before result
}
```

Consumed by SPEC-03 (state machine transitions) and SPEC-04 (`dhf.tool_calls.lifecycle_state` storage column). The `Sent` state is not persisted to DHF — only terminal states produce rows. DHF serialization uses canonical storage strings (`completed`, `timed_out`, `cancelled`, `abandoned`) via `DhfEnumMappings.ToStorage()` in SPEC-04 §4.5.

### 6.4 Shared Enums

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
    UnknownMessageType,    // Well-formed envelope, unknown 'type' discriminator value (§3.1)
    MalformedEnvelope,     // Unparseable envelope or envelope-invariant violation (§3.1)
    StaleEpoch,            // Orchestrator detected stale AddinSessionId from add-in
    AuthenticationFailed   // v1: API key rejected post-upgrade; see §12
}

public enum AddinProtocolErrorCode
{
    InFlightDuplicate,
    UnknownCallId,
    UnknownMessageType,    // Well-formed envelope, unknown 'type' discriminator value (§3.1)
    StaleEpoch,            // Add-in detected stale AddinSessionId from orchestrator
    MessageTooLarge,
    MalformedEnvelope      // Unparseable envelope or envelope-invariant violation (§3.1)
}
```

**Semantic boundary between `MalformedEnvelope` and `UnknownMessageType` (v1.4.0, per §3.1):**

- `MalformedEnvelope` = "I cannot deserialize this into a valid typed message." The bytes are corrupt, the envelope structure is wrong, or a typed invariant is violated.
- `UnknownMessageType` = "I successfully parsed your envelope and extracted your `type` discriminator, but the value isn't in my dispatch table." The peer is using a protocol vocabulary I don't know.

Both enums carry both codes; producers MUST choose the correct one per §3.1 triggers. Unit tests (`UnknownMessageTypeVsMalformedEnvelopeTests`) seed representative inputs for each category and verify the receiver routes them to the correct code.

### 6.5 EntityRef and SketchEntityId

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

### 6.6 PlaneRef, DimensionTarget, and SelectionRef (Discriminated Unions)

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

public record ScreenshotResult : IToolResult
{
    // Exactly one of ScreenshotBase64 or ScreenshotUrl MUST be non-null (envelope invariant).
    // v1: ScreenshotBase64 populated, ScreenshotUrl null.
    // v2: ScreenshotUrl populated (S3 presigned GET URL, 5-minute expiry), ScreenshotBase64 null.

    public string? ScreenshotBase64 { get; init; }      // v1; base64-encoded PNG, max 2 MiB post-encode
    public string? ScreenshotUrl { get; init; }          // v2; presigned S3 GET URL
    public required string Sha256Hash { get; init; }     // Hex, lowercase; computed over raw PNG bytes
    public required int WidthPx { get; init; }
    public required int HeightPx { get; init; }
}
```

**Screenshot transport (v1):** The add-in captures the SolidWorks viewport as a PNG in memory, computes SHA-256 over the raw bytes, base64-encodes, and populates `ScreenshotBase64`. The encoded length MUST NOT exceed 2 MiB. If the captured PNG would exceed 2 MiB post-encode, the add-in MUST progressively reduce capture dimensions (e.g., halve `WidthPx` and `HeightPx` iteratively) until the encoded size fits, and return the reduced-dimension result with updated `WidthPx` / `HeightPx`. The 2 MiB screenshot cap leaves approximately 2 MiB of headroom within the 4 MiB message envelope for the rest of the `ToolResultMessage` (most importantly `StateAfter.FeatureTree`). Iteration terminal behavior is specified in SPEC-02 v1.4.0 §7.2: after a bounded number of reduction iterations the add-in abandons capture and returns `ToolErrorCode.ScreenshotCaptureFailed`.

**Screenshot transport (v2):** `ScreenshotResult` carries `ScreenshotUrl` (S3 presigned GET URL, 5-minute expiry) instead of base64. The add-in uploads the PNG to S3 via a presigned PUT URL obtained from an orchestrator HTTPS endpoint. See `DEPLOYMENT_V1_V2_MIGRATION.md` §3 for the full transport migration.

**Envelope invariant** (enforced by `ScreenshotResultValidator`): exactly one of `ScreenshotBase64` or `ScreenshotUrl` MUST be non-null. Both null, or both non-null, is a validation error producing `MalformedEnvelope`. Consumers (SPEC-05 agent loop for Anthropic vision API, SPEC-04 DHF writer, SPEC-06 viewer) branch on which field is populated; the wire contract is stable across v1 and v2 so `ProtocolVersion` does not change.

**`Sha256Hash`:** computed over the raw PNG bytes, not over the base64 encoding and not over the URL. Identical in v1 and v2 for a given capture. Stored authoritatively in DHF (`dhf.entries.screenshot_before_sha256` / `screenshot_after_sha256`) as the integrity anchor for audit trail replay.

```csharp
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

**These are empty placeholder records** with `// TODO: STRETCH` markers. Their polymorphic registrations (`[JsonDerivedType]` on `IToolInput`/`IToolResult`) and `[ToolName]` attributes are present from day one so that `ToolRegistry` and completeness tests remain valid. **All stretch tools MUST set `IsImplemented = false` on their `[ToolName]` attribute** (see Section 11). Schema generation and tool catalog exposure MUST exclude tools where `IsImplemented == false`. Field definitions are deferred to stretch implementation. Any future field addition to a stretch tool record constitutes a wire-contract change and MUST increment `ProtocolVersion`.

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
    public bool IsFeatureTreeTruncated { get; init; }      // True when tree was cut to fit message size
    public UnitSystem? Units { get; init; }                // Null when IsNullDocumentState is true
    public required bool IsNullDocumentState { get; init; }
}
```

**Null-document rules:** When `IsNullDocumentState` is `true`, ALL of the following MUST hold:

- `Units` MUST be `null`
- `DocumentId` MUST be `null`
- `DocumentName` MUST be `null`
- `DocumentType` MUST be `null`
- `FeatureTree` MUST be empty
- `FeatureTreeHash` MUST be `"NO_DOCUMENT"`
- `IsFeatureTreeTruncated` MUST be `false`
- `IsSketchActive` MUST be `false`
- `ActiveSketchId` MUST be `null`
- `ActiveSelection` MUST be empty

**Live-document rules:** When `IsNullDocumentState` is `false`, ALL of the following MUST hold:

- `Units` MUST be non-null
- `DocumentId` MUST be non-null
- `DocumentName` MUST be non-null
- `DocumentType` MUST be non-null

**Sketch-state invariant** (applies regardless of `IsNullDocumentState`):

- When `IsSketchActive` is `true`: `ActiveSketchId` MUST be non-null
- When `IsSketchActive` is `false`: `ActiveSketchId` MUST be `null`

**Feature tree truncation:** When the add-in determines that including the full `FeatureTree` would cause the enclosing message to exceed the 4 MiB wire limit (Section 3), it MUST truncate the tree (e.g., include only top-level features) and set `IsFeatureTreeTruncated = true`. The `FeatureTreeHash` MUST still reflect the hash of the **complete** (non-truncated) tree so that change detection remains accurate. When `IsFeatureTreeTruncated` is `true`, the orchestrator SHOULD call `get_feature_tree` to retrieve the complete tree in a separate message.

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

`FeatureParameterNumber` uses `decimal` to eliminate floating-point nondeterminism in hashing. See Section 14 for the canonical decimal format.

## 9. AddinSessionId Format and Epoch Semantics

SPEC-01 is authoritative for the **format contract** and the **epoch semantics** of `AddinSessionId`. The **derivation algorithm** (UUID version, hash function, input fields, fallback behavior) is authoritative in SPEC-02 §8. This split is intentional: SPEC-04 consumes epoch semantics without taking a dependency on SPEC-02 add-in internals.

**Format:** `AddinSessionId` is exactly 32 lowercase hexadecimal characters matching the regex `^[0-9a-f]{32}$`. `AddinSessionIdFormatValidator` (see Section 15) enforces this format. Any message carrying an `AddinSessionId` that fails format validation MUST be rejected with `MalformedEnvelope`.

**Uniqueness expectation:** The format does not itself guarantee uniqueness across the population of add-in instances — that is the responsibility of the SPEC-02 generation algorithm. SPEC-01 consumers assume that any two `AddinSessionId` values observed within a single tenant's sessions are either literally equal (same add-in instance, possibly reconnecting) or refer to distinct add-in loads.

### 9.1 Same-Epoch Reconnect (Same AddinSessionId)

If a new `AddinReadyMessage` arrives with the same `AddinSessionId` as the currently established epoch, the orchestrator treats it as a reconnect attempt within the same logical session. Two sub-cases apply.

**Case A — Identical capability fields:** `AddinVersion`, `SwVersion`, `SwBuild`, and `ScreenshotRootPath` all match the values recorded on the open DHF session row (SPEC-04 §4.1 schema). The orchestrator:

1. Re-runs protocol-version validation (§10) against the reconnect message's `ProtocolVersion` field.
2. Re-binds the WebSocket to the existing DHF session WITHOUT issuing a new `AppendTurnAsync` or modifying any `dhf.sessions` row columns.
3. Does NOT reset `StateVersion` (the epoch identity is unchanged, so StateVersion monotonicity is unchanged).
4. Proceeds to accept subsequent `IClientMessage` / `IAddinMessage` exchanges on the rebound connection.

**Case B — Capability fields differ (v1.4.0: REJECT):** One or more of `AddinVersion`, `SwVersion`, `SwBuild`, or `ScreenshotRootPath` differs from the values recorded on the open DHF session row. The orchestrator REJECTS the reconnect with `ClientProtocolErrorMessage(ErrorCode: StaleEpoch)` and closes the WebSocket with close code 1008 ("policy violation").

Rationale: SPEC-04 SDR-04-010 makes those columns immutable once written, enforced by the `guard_session_update` trigger. Silently accepting updated capability fields would either require making the columns mutable (contradicting SDR-04-010) or would mean the DHF row's recorded capabilities diverge from the current connection's actual capabilities (audit-integrity violation). Neither is acceptable.

The add-in side interpretation: a legitimate capability change (SW was updated during the disconnect, the add-in was rebuilt with a new version, the workstation's screenshot root was reconfigured) MUST correspond to a new add-in load, which produces a fresh `AddinSessionId` via the SPEC-02 §8 algorithm (the process restarted, `loadTimestampUtcTicks` changed, and the random nonce changed). An add-in that genuinely has the same `AddinSessionId` as before and yet has different capabilities is in an illegal state — either the nonce generation is broken, the `loadTimestampUtcTicks` field was cached, or the add-in is spoofing. Rejection is the safe response.

Add-in recovery from rejection: treat close code 1008 with reason text containing `"capability_drift"` identically to an `AddinSessionId` epoch rollover — discard the current session identity, regenerate a new `AddinSessionId` via §8, and reconnect. The orchestrator will accept the new connection as a new epoch (Case §9.3 below).

**ProtocolVersion drift on same-`AddinSessionId` reconnect (v1.4.0+ editorial m4, corrected R4 H4).** A change in `ProtocolVersion` on a same-AddinSessionId reconnect is also an illegal state — same rationale as capability drift. It is caught by §10's version-mismatch handling with **close code 1002 and a `VersionMismatchError` payload** (NOT HTTP 400 — `ProtocolVersion` travels in `AddinReadyMessage`, sent AFTER the WebSocket upgrade completes, so an HTTP-upgrade-time rejection is structurally impossible for this failure). Operationally it is the same class of failure as capability drift (the add-in rebuilt/redeployed without generating a new `AddinSessionId`); the different close code reflects wire-contract severity. HTTP 400 is reserved exclusively for pre-upgrade failures (§12.4 — authentication, upgrade-header validation, rate limiting) and does not apply here — `ProtocolVersion` is wire-format content, not an upgrade-time header. The add-in's recovery path is identical to capability drift — discard the old identity, regenerate a fresh `AddinSessionId`, reconnect.

**`AddinSessionId` regeneration policy (v1.4.0+ editorial m5, corrected R4 H4).** The add-in regenerates a new `AddinSessionId` ONLY when it observes BOTH signals: (1) a `StaleEpoch` error code in `ClientProtocolErrorMessage`, AND (2) close code 1008 with reason prefix `"capability_drift"` (or a close code 1002 with a `VersionMismatchError` payload signaling protocol-version drift, per m4 corrected). Other `StaleEpoch` rejections — pre-epoch messages (no `AddinReadyMessage` has been sent yet) or steady-state stale messages (the add-in's own epoch shifted mid-flight) — do NOT require regeneration. The add-in retries with its current `AddinSessionId` after exponential backoff.

### 9.2 Cross-Workstation Collision

If a new `AddinReadyMessage` arrives with an `AddinSessionId` that matches an already-open DHF session on a different `WorkstationId` (within the same tenant), this is a hard error. SPEC-04 SDR-04-008 specifies the `CrossWorkstationSessionConflictException` behavior; the orchestrator MUST NOT silently merge or reassign the session.

### 9.3 Epoch Rules (Enforced by Consumers in SPEC-03 and SPEC-04)

- Every `IClientMessage` and `IAddinMessage` carries exactly one `AddinSessionId` at the top-level record. Nested duplicates inside `StateSnapshot` or other records are forbidden.
- **`AddinReadyMessage` is exempt from epoch validation.** It is the sole mechanism for establishing or changing the current epoch. (`AddinTeardownMessage` is NOT exempt — it MUST carry the currently-established AddinSessionId or it will be rejected as stale.)
- **Initial connection:** Before the first `AddinReadyMessage` is received, no epoch exists. The orchestrator MUST accept the first `AddinReadyMessage` (post-TLS, post-API-key-validation per §12) and record its `AddinSessionId` as the current epoch. All non-`AddinReadyMessage` messages received before the first epoch is established MUST be rejected with `ClientProtocolErrorMessage(ErrorCode: StaleEpoch)`, with `AddinSessionId` echoed from the rejected message (see Section 5).
- **Steady state:** After an epoch is established, any non-`AddinReadyMessage` from the add-in with an `AddinSessionId` other than the current epoch is rejected with `ClientProtocolErrorMessage(ErrorCode: StaleEpoch)`. Any `IClientMessage` received by the add-in with a stale `AddinSessionId` is rejected with `AddinProtocolErrorMessage(ErrorCode: StaleEpoch)`.
- **Epoch transition:** When a subsequent `AddinReadyMessage` arrives with a different `AddinSessionId`, the orchestrator atomically transitions to the new epoch and resets its view of `StateVersion` to 0.

## 10. Protocol Versioning

`ProtocolVersion.Current` is a const string. Initial value: `"1.0.0"`. Semantic versioning semantics but pre-1.0 is not permitted for this project.

**Handshake rule:**

- Add-in sends `AddinReadyMessage` with `ProtocolVersion = Current`
- Orchestrator compares to its own `Current`
- Exact string match required. Mismatch → orchestrator sends a version-mismatch error (see below), then closes the WebSocket.
- No negotiation, no downgrade, no "minor version compatible." This is hackathon scope; negotiation is post-hackathon.

**ProtocolVersion does NOT bump between v1 and v2 deployments.** The wire contract (message shapes, discriminators, envelope invariants) is stable across the deployment transition by design. Only changes enumerated in `DEPLOYMENT_V1_V2_MIGRATION.md` §8 "Version-Bump Triggers" require a bump.

**Stable version-mismatch wire format:** Because a version mismatch implies the two sides may use incompatible polymorphic schemas, the version-mismatch error MUST be deserializable without relying on the polymorphic `IClientMessage` pipeline. The following flat JSON schema is **frozen across all protocol versions** — both sides MUST be able to parse it by reading raw JSON properties regardless of their protocol version:

```json
{
  "type": "client_protocol_error",
  "messageId": "<UUIDv7>",
  "addinSessionId": "<echoed from AddinReadyMessage>",
  "errorCode": "VersionMismatch",
  "errorText": "<human-readable diagnostic>",
  "expectedProtocolVersion": "<orchestrator's ProtocolVersion.Current>",
  "receivedProtocolVersion": "<add-in's reported ProtocolVersion>"
}
```

Receivers that encounter an unrecognized `"type"` discriminator value SHOULD attempt raw JSON property extraction before failing deserialization. This guarantees the version-mismatch diagnostic is always readable even when all other message types have diverged.

## 11. Tool Registry and Schema Generation

```csharp
[AttributeUsage(AttributeTargets.Class)]
public sealed class ToolNameAttribute : Attribute
{
    public string Name { get; }
    public bool IsImplemented { get; set; } = true;
    public ToolNameAttribute(string name) { Name = name; }
}

public static class ToolRegistry
{
    public static IReadOnlyList<ToolMetadata> All { get; }
    public static IReadOnlyList<ToolMetadata> Implemented { get; }   // All where IsImplemented == true
    public static ToolMetadata? GetByName(string toolName);
    public static Type? InputType(string toolName);
    public static Type? ResultType(string toolName);
}

public record ToolMetadata(
    string Name,
    Type InputType,
    Type ResultType,
    TimeSpan Timeout,
    bool IsImplemented);
```

`ToolRegistry.All` reflects the Protocol assembly at static construction time, finding every type with `[ToolName]` attributes that implement `IToolInput` or `IToolResult`. `ToolRegistry.Implemented` filters to only those with `IsImplemented == true`.

**Single source of truth for tool names:** The `[ToolName]` attribute is the authoritative identity for every tool. The `[JsonDerivedType]` discriminator value on `IToolInput` and `IToolResult`, the `ToolTimeouts` table key, and the Anthropic schema name MUST all exactly match the `[ToolName]` value. `ToolNameConsistencyTests` (build-breaking) verifies this for every registered tool.

**Uniqueness invariant:** Each tool name maps to exactly one input type and exactly one result type. `ToolNameUniquenessTests` (build-breaking) fails if multiple input or result types share the same `[ToolName]` value.

**Tool exposure rule:** Schema generation (`JsonSchemaGenerator`, `AnthropicToolSpec`) and any consumer that builds a tool catalog for external exposure (e.g., the Anthropic tool list in SPEC-05) MUST use `ToolRegistry.Implemented`, not `ToolRegistry.All`. Unimplemented tools MUST NOT appear in generated schemas or be advertised as callable. `ToolExposureExclusionTests` (build-breaking) verifies that no unimplemented tool appears in generated Anthropic tool specs.

`ToolRegistryCompletenessTests` asserts every registered input has a matching result and vice versa.

## 12. Transport — TLS and API-Key Authentication Handshake (v1)

The WebSocket between add-in and orchestrator crosses the public internet. This section specifies the v1 authentication and TLS contract. For v2 evolution (OAuth 2.0 device flow), see `DEPLOYMENT_V1_V2_MIGRATION.md` §2.

### 12.1 URL and TLS

- **WebSocket URL (v1):** `wss://vcad.boundstate.ai/ws`
- **TLS termination:** at the Railway edge. Certificates are Railway-managed (Let's Encrypt). The orchestrator container sees plain WS internally; the contract described to the add-in is `wss://`.
- **Minimum TLS version:** TLS 1.2. Add-in connections over TLS 1.1 or lower are refused at the edge; no orchestrator-side handling required.
- **Certificate pinning:** not required for v1 (Railway edge certs rotate). Add-in validates the server cert via standard Windows certificate store trust chain.

### 12.2 API-Key Credential Model

The orchestrator maintains an `api_keys` table in Postgres:

```sql
CREATE TABLE auth.api_keys (
    key_id          TEXT PRIMARY KEY,           -- opaque 16-char alphanumeric
    secret_hash     TEXT NOT NULL,              -- Argon2id of raw secret
    tenant_id       TEXT NOT NULL,              -- scope for sessions and DHF rows
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at      TIMESTAMPTZ NULL,
    last_used_at    TIMESTAMPTZ NULL,
    display_name    TEXT NOT NULL,              -- "Joe's laptop", "Demo key", etc.
    comment         TEXT NULL
);

CREATE INDEX idx_api_keys_tenant ON auth.api_keys (tenant_id) WHERE revoked_at IS NULL;
```

**Key issuance (out of band):** API keys are provisioned by an operator via a CLI tool (`dhf-admin issue-key`, post-hackathon) or directly via SQL insert (hackathon scope). Secrets are never stored plaintext in the DB; Argon2id hashing parameters are `t=3, m=64MiB, p=1`.

**Key format on the wire:** `bsvc_{keyId}_{secret}` where `keyId` is 16 alphanumeric chars and `secret` is 32 alphanumeric chars (approx 190 bits entropy). Add-in stores the full key in Windows Credential Manager under the target `BoundState.BSVoiceCAD.ApiKey`. The user provisions the key into Credential Manager manually or via an installer step.

### 12.3 Handshake Sequence

The WebSocket upgrade request (HTTP/1.1 `GET /ws`) carries two subprotocols in the `Sec-WebSocket-Protocol` header:

```
Sec-WebSocket-Protocol: bsvoice-cad-v1, apikey-{keyId}.{secret}
```

The second value encodes the credential. The orchestrator's upgrade handler:

1. Parses the subprotocol list.
2. Requires the first value to be exactly `bsvoice-cad-v1` (version tag; distinct from `ProtocolVersion` which gates message shapes). If not, responds with HTTP 400 and closes.
3. Parses the second value as `apikey-{keyId}.{secret}`. If the format does not match, responds with HTTP 401 and closes.
4. Looks up `keyId` in `auth.api_keys`. If missing or `revoked_at IS NOT NULL`, responds with HTTP 401 and closes.
5. Verifies `secret` against `secret_hash` using Argon2id constant-time comparison. If mismatch, responds with HTTP 401 and closes.
6. Updates `last_used_at` (best effort; failure to update does not fail the handshake).
7. Completes the WebSocket upgrade, echoing `bsvoice-cad-v1` (only) back in `Sec-WebSocket-Protocol`. The apikey subprotocol is NEVER echoed; echoing would leak the credential into logs.

**Known risk — subprotocol header logging (R3 R1-ISSUE-007, v1.4.0 acknowledged, mitigated, not fully resolved):** The API credential travels in the `Sec-WebSocket-Protocol` request header. This header may be logged by:
- Railway's edge load balancer request logs
- Any reverse proxy or CDN in the path (none planned for v1, but the risk is architectural)
- APM tooling if request-header capture is enabled (none planned)
- Browser devtools if the upgrade request is observed (not applicable — no browser involved, the add-in is the WS client)

Suppressing the echoed response header does NOT prevent request-side logging. A leaked header exposes a long-lived tenant credential that is only invalidated by a manual `revoked_at` UPDATE.

**v1 mitigations (MUST be configured before production use):**
1. Railway logging configuration MUST redact the `Sec-WebSocket-Protocol` header from the request log sink. This is configured in the Railway service settings, not in application code. Migration doc v1.1.0 §7 specifies the redaction requirement.
2. Any downstream log shipper (Datadog, Grafana, etc.) MUST also be configured to drop or hash the header. Not applicable to hackathon scope (Railway logs only).
3. `ApiKeyHandshakeTests` includes a test asserting that the orchestrator NEVER writes the subprotocol header value to any log sink from application code (Serilog filter rules).

**v1.5 resolution path:** Move the credential to an `Authorization: Bearer <token>` header on the HTTP upgrade request. Issue short-lived tokens (15-minute TTL) via a separate HTTPS endpoint (`POST /api/auth/token`) that exchanges the long-lived API key for the short-lived token. Log-leak exposure window shrinks to the token's TTL instead of the key's lifetime. This is a breaking change to SPEC-01 §12.3 and SPEC-02 §6; it does NOT bump `ProtocolVersion` (the message-level wire contract is unchanged). Estimated scope: 1-2 days, deferred post-hackathon.

The acknowledgment-only treatment in v1.4.0 is explicit: the architectural risk is documented, the v1 mitigations are mandatory, the v1.5 path is specified. Risk is accepted for hackathon and initial Medtronic pilot scope (same SDR-TL-012 threat model as viewer auth).

### 12.4 Handshake Failure Handling

Per `DEPLOYMENT_V1_V2_MIGRATION.md` v1.1.0 §2 / DM-8:

**Pre-upgrade failures** (detected during the HTTP upgrade request, before WebSocket is established) are returned as HTTP responses on the upgrade request itself. No WebSocket Close frame can be sent because no WebSocket exists yet.

- **HTTP 400 Bad Request:** malformed subprotocol format (missing values, wrong prefix, malformed apikey segment). Add-in interprets this as a client-library issue. Retries with backoff (5s, 30s, 5min); if repeated, surfaces a UI error "connection to BSVoice-CAD service failed; reinstall may be required."
- **HTTP 401 Unauthorized:** auth failure (missing `keyId` in `auth.api_keys`, `revoked_at IS NOT NULL`, secret mismatch). Add-in surfaces a UI error "authentication failed; please verify your BSVoice-CAD API key in Windows Credential Manager." Does NOT retry automatically; retry requires explicit user action.
- **HTTP 429 Too Many Requests:** per-IP or per-keyId rate limit exceeded (see §12.6). Add-in backs off per the `Retry-After` response header (bounded to 15 minutes).

**Post-upgrade credential revocation** (WebSocket connection is live, API key is revoked while open — operator sets `revoked_at`): orchestrator MAY close the WebSocket with **close code 4401** (custom, "auth failed post-upgrade"). Close code 4401 is reserved EXCLUSIVELY for this post-upgrade revocation path. Add-in treats 4401 identically to HTTP 401 — surfaces the UI auth error, does NOT retry automatically.

Close code 4401 MUST NOT be used for pre-upgrade failures (see R3 R1-ISSUE-005 resolution in migration doc v1.1.0 §2). A Close frame cannot be sent before the upgrade completes.

**Detection-mechanism boundary (v1.4.0+ editorial m6).** The mechanism by which the orchestrator observes a live connection's credential revocation — poll interval over `auth.api_keys`, Postgres `LISTEN/NOTIFY` subscription, or lazy-check-on-next-message — is owned by SPEC-03 session-lifecycle logic, not by SPEC-01. SPEC-01 specifies only the close-code contract (close code 4401, reason text, post-upgrade reservation). Implementers MAY choose any detection strategy compatible with their tenant SLA and operator-ergonomics targets, provided the close-code contract is met.

### 12.5 Per-Connection Identity and Tenant Scoping

After successful handshake, the orchestrator binds the WebSocket connection to the `tenant_id` associated with the validated API key. All messages received on this connection are implicitly scoped to that tenant. SPEC-04 consumes the tenant_id for DHF row scoping (migration 006 adds `dhf.sessions.tenant_id`).

**Cross-tenant isolation:** A WebSocket authenticated with tenant A's API key MUST NOT be able to observe or interact with tenant B's sessions or DHF records. This is enforced at the repository layer (every read and write query filters by `tenant_id`) and verified by `TenantIsolationTests` in `BSVoiceCAD.Tests.Integration`.

### 12.6 Rate Limiting and Abuse Protection (v1.4.0 — In-Memory Lockout)

- **Handshake attempts per IP (edge):** Railway edge limits 60/min globally. This is not configurable from application code; it is a Railway service setting.
- **Handshake attempts per keyId (orchestrator-level, in-memory):** The orchestrator maintains an in-memory counter (`InMemoryKeyLockoutTracker`) of failed handshake attempts per `keyId` over a rolling 1-hour window. Failure triggers: unknown keyId, revoked keyId, secret mismatch, malformed apikey subprotocol where the keyId portion can still be parsed. Threshold: **10 failed attempts per keyId per hour** → the keyId is placed in a lockout set for **15 minutes**; further handshakes with that keyId return HTTP 429 regardless of secret correctness.

**In-memory-only limitations (v1.4.0, documented trade-off):**
- The lockout tracker is process-local. On orchestrator restart, all lockout state is lost. A key that was 9/10 attempts-away from lockout at the moment of restart resets to 0/10.
- The lockout tracker does not replicate across multiple orchestrator instances. If a future deployment runs >1 orchestrator container behind a load balancer (not planned for v1), an attacker can distribute failed attempts across instances to evade the per-instance threshold.
- Failed attempts are logged at `LogLevel.Warning` with structured fields `{event: "handshake_failure", keyId, reason}` so operators can correlate post-hoc via log aggregation even without persisted counters.

**What the v1.4.0 change does NOT do:** earlier drafts of SPEC-01 v1.3.0 referenced an `api_keys.lockout_event` table for lockout persistence, noted as "schema deferred to post-hackathon." That table does not exist, is not introduced in v1, and MUST NOT be referenced by implementation code. Attempting to write a `lockout_event` row would be an undefined-table error. The in-memory approach is the complete v1 story.

**v1.5 path:** Persist lockout events to a new table `auth.lockout_events` (schema: `keyId TEXT, locked_at TIMESTAMPTZ, locked_until TIMESTAMPTZ, reason TEXT, attempts_in_window INT`). Compute current lockout state on handshake by querying for any row with `locked_until > NOW()`. Estimated scope: a migration plus ~30 lines of repository code. Not needed for hackathon.

- **Post-handshake message rate:** no hard rate limit for v1. A tenant that saturates the orchestrator receives `MessageTooLarge` or `MalformedEnvelope` naturally for malformed abuse; a well-formed flood would exhaust Railway service quotas, which Railway surfaces back to the operator.

### 12.7 Connection Lifecycle

- **Idle timeout:** orchestrator closes connections idle (no messages in either direction) for > 10 minutes. Close is a clean close (WebSocket close code 1000 "normal closure"). Add-in reconnects automatically; reconnect emits a new `AddinReadyMessage` with the same `AddinSessionId`, resuming the DHF session per SPEC-04 SDR-04-005 and §9.1 Case A above (identical capability fields).
- **Heartbeat:** WebSocket ping/pong every 30 seconds driven by orchestrator. Missed pong from the add-in after 2 consecutive intervals (60s total) triggers heartbeat-timeout handling.
- **Heartbeat-timeout handling (v1.4.0 — TCP abort, per R3 R1-ISSUE-005 CRITICAL):** RFC 6455 §7.4.1 forbids sending close code 1006 in a WebSocket Close frame. Close code 1006 is a local-observation code only. `.NET`'s `ClientWebSocket.CloseAsync(WebSocketCloseStatus.Empty, ...)` and `CloseOutputAsync(WebSocketCloseStatus.Empty, ...)` paths attempt to send a Close frame; neither correctly models the "heartbeat failure" scenario.

  Correct behavior: the orchestrator **aborts the underlying TCP connection** without sending a Close frame. In .NET this is `WebSocket.Abort()`, which disposes the socket directly. The peer (add-in) observes this as an `IOException` / `WebSocketException` with `WebSocketError.ConnectionClosedPrematurely` or equivalent; the peer's local close code is conventionally reported as 1006 "abnormal closure" by the runtime, but no Close frame was sent over the wire.

  DHF session outcome: the session closes with reason `heartbeat_timeout` per SPEC-00 §6 glossary. Root cause (add-in crash, network failure, intentional shutdown without teardown) is not distinguishable and is recorded as "unknown." If the add-in later reconnects with the same `AddinSessionId`, §9.1 applies. Typical recovery is a fresh `AddinReadyMessage` with a new `AddinSessionId` (the add-in process has died and been relaunched, producing a new epoch).

  **Add-in side:** on observing a connection-closed-prematurely event without having received a Close frame, the add-in SHOULD interpret this as a heartbeat-timeout scenario and begin reconnect attempts with exponential backoff (1s, 2s, 4s, 8s, 16s, capped at 30s). Reconnect uses the same `AddinSessionId` if the add-in process is still alive.

  **Tests (`HeartbeatTimeoutTcpAbortTests`):** assert that the orchestrator's heartbeat-timeout path calls `WebSocket.Abort()` and does NOT call `CloseAsync` or `CloseOutputAsync`. Verify via a mock WebSocket wrapper that no Close frame is written to the outbound stream. Assert that the peer observes the expected `WebSocketException` without a Close frame preceding it.

- **Clean shutdown (orchestrator or add-in initiated, NOT heartbeat):** either side MAY send WebSocket close frame with code 1000 ("normal closure") and a short reason. Orchestrator close does not terminate the DHF session (SPEC-04 SDR-04-005); the session remains open pending add-in reconnect or heartbeat timeout. `AddinTeardownMessage` (§5, SDR-TL-014) is the add-in's graceful-shutdown path; the add-in SHOULD send `AddinTeardownMessage` before sending the Close frame with code 1000.

- **Policy-violation close (same-epoch reconnect with capability drift, per §9.1 Case B):** close code 1008 ("policy violation") with reason text `"capability_drift: {keyword}"`. Reason keyword identifies which field differed (e.g., `"sw_version"`, `"addin_version"`, `"screenshot_root_path"`). Used for §9.1 Case B specifically.

**Summary close-code table:**

| Code | Meaning | Triggered by |
|------|---------|-------------|
| 1000 | Normal closure | Idle timeout, clean orchestrator shutdown, add-in teardown followed by add-in close |
| 1002 | Protocol error | Repeated `MalformedEnvelope` from peer within DOS window (§3.1) |
| 1008 | Policy violation | Same-epoch reconnect with capability drift (§9.1 Case B) |
| 4401 | Auth failed post-upgrade | API key revoked while connection is live (§12.4) |
| (1006 not sent as Close frame) | Abnormal closure observed locally | Heartbeat timeout (orchestrator aborts TCP without Close frame, per above); peer observes locally |

### 12.8 v2 Evolution

v2 replaces the API-key subprotocol with an OAuth 2.0 device-flow access token, carried in the same `Sec-WebSocket-Protocol` header position:

```
Sec-WebSocket-Protocol: bsvoice-cad-v1, bearer-{jwt_access_token}
```

Orchestrator detects which mode via env var `DEPLOYMENT_AUTH_MODE`: `api_key` (v1) or `oauth` (v2). The message-level wire contract does not change. `ProtocolVersion` does not bump. See `DEPLOYMENT_V1_V2_MIGRATION.md` §2.

**v1.5 intermediate step (documented risk mitigation):** the Authorization-header migration described in §12.3's "known risk" section is a v1.5 improvement that keeps v1 API-key auth model while moving the credential off the `Sec-WebSocket-Protocol` header. This is a smaller change than full OAuth (v2) and is the recommended path when customer-facing deployments begin.

## 13. Per-Tool Timeouts

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

## 14. Hash Canonicalization

`HashCanonicalizer.ComputeFeatureTreeHash(IReadOnlyList<FeatureNode>)` and `.ComputeRecordHash(object, IReadOnlyList<string>)` produce deterministic SHA-256 hex strings.

**Rules:**

- Input is serialized with a fixed `JsonOptions` instance: camelCase properties, PascalCase enum strings, sorted object keys, null properties omitted.
- The output is a lowercase hex SHA-256 of the UTF-8 encoded canonical JSON.
- The null-document sentinel is the literal string `"NO_DOCUMENT"`, not a hash of anything.

**Decimal format:** Decimals are serialized in invariant culture as plain decimal notation. Scientific notation (the characters `E` and `e`) MUST NOT appear in the output. Trailing fractional zeros are stripped. The reference implementation is `HashCanonicalizer.FormatDecimal(decimal)`, which calls `value.ToString("G29", CultureInfo.InvariantCulture)` and post-processes any scientific-notation output into expanded decimal form. `HashDecimalFormatTests` verifies no scientific notation appears for edge-case values (10^−28, 10^28, 0, decimal.MaxValue, decimal.MinValue).

**Array ordering:** `HashCanonicalizer` normalizes array order internally before computing the hash. **Producers are NOT required to emit arrays in any particular order** — the canonicalizer sorts them deterministically. This ensures that logically equivalent data structures always produce identical hashes regardless of emission order. The sort orders applied by the canonicalizer are:

- `FeatureNode` lists (in `StateSnapshot.FeatureTree` and `GetFeatureTreeResult.Tree`): sorted by `FeatureId`, lexicographic, ordinal, case-sensitive.
- `FeatureParameter` lists (in `FeatureNode.Parameters`): sorted by `Name`, lexicographic, ordinal, case-sensitive.
- `SelectionRef` lists (in `StateSnapshot.ActiveSelection`): `PersistentSelectionRef` entries first (sorted by `Entity.PersistentId`), then `SketchSelectionRef` entries (sorted by `SketchEntity.LocalId`), all ordinal, case-sensitive.

Property-based tests (`HashCanonicalization_PropertyTests` via FsCheck) generate 1000 randomly shaped `FeatureNode` trees and assert that logical equality (including reordered-but-equivalent arrays) implies hash equality.

## 15. Validation

Every `IToolInput` has a matching `AbstractValidator<T>` in `Validation/Validators/`. FluentValidation. Rules:

- Range/Required/Enum/MinLength/MaxLength rules translate to JSON Schema constraints via `JsonSchemaGenerator`.
- Cross-field conditional rules (e.g., "Secondary required when Mode = LinearTwoEntity") are implemented in the validator but do NOT translate to JSON Schema constraints. They appear as `description` text only.

**Message-envelope validation:** In addition to tool-input validators, the following message types have envelope validators:

- `ToolResultMessageValidator`: enforces the Success/Result/Error invariant (see Section 5).
- `StateSnapshotValidator`: enforces all three invariant groups (see Section 8) — null-document rules (10 constraints when `IsNullDocumentState` is true), live-document rules (4 required-non-null fields when `IsNullDocumentState` is false), and the sketch-state invariant (`ActiveSketchId` required iff `IsSketchActive` is true).
- `ScreenshotResultValidator`: enforces the `ScreenshotBase64` / `ScreenshotUrl` envelope invariant (exactly one non-null) and the 2 MiB post-encode size cap on `ScreenshotBase64` (v1).
- `AddinSessionIdFormatValidator`: enforces the 32 lowercase hex character format (regex `^[0-9a-f]{32}$`) on `AddinSessionId` fields in both `IClientMessage` and `IAddinMessage`.
- **`CallIdFormatValidator` (v1.4.0):** enforces UUIDv7 canonical textual form on `CallId` fields in `ToolCallMessage`, `CancelToolCallMessage`, `ToolResultMessage`, and the `CorrelatedCallId` field of `StateUpdateMessage` when non-null. Regex: `^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` on the lowercased value. Must round-trip through `Guid.Parse` as well as the regex (defense in depth).
- **`AddinTeardownMessageValidator` (v1.4.0):** enforces that `AddinSessionId` matches format, `Reason` is a defined enum value, `DiagnosticMessage` (if present) is ≤ 500 characters.

Two tests enforce the schema sync split:

- `ValidationSchemaSyncTests` (build-breaking): every mechanical validator rule has a matching JSON Schema constraint.
- `SemanticHintCoverageTests` (`[Trait("Category", "Advisory")]`, non-breaking): cross-field rules appear in schema descriptions.

Advisory tests run in a separate CI step with `continue-on-error: true` so they report without blocking.

## 16. Test Requirements (Build-Breaking)

Required tests in `BSVoiceCAD.Protocol.Tests`. Failure of any of these fails CI.

- `SerializationTests`: every record round-trips byte-identical
- `PolymorphicDeserializationTests`: every `[JsonDerivedType]` round-trips through the base interface
- `JsonDerivedTypeRegistrationCompletenessTests`: every concrete implementation of each polymorphic interface is registered (includes `AddinTeardownMessage` as of v1.4.0)
- `JsonNamingPolicyConsistencyTests`: every serialized property name is camelCase
- `JsonEnumSerializationTests`: every enum serializes as its PascalCase member name (never integer)
- `ToolRegistryCompletenessTests`: every tool has paired Input + Result + Timeout
- `ToolNameConsistencyTests`: `[ToolName]` value matches `[JsonDerivedType]` discriminator, timeout table key, and schema name for every tool
- `ToolNameUniquenessTests`: no two input or result types share the same `[ToolName]` value
- `ToolExposureExclusionTests`: no unimplemented tool (`IsImplemented == false`) appears in generated Anthropic tool specs
- `HashCanonicalizationTests`: equality-invariant + empty-input + NO_DOCUMENT sentinel
- `HashCanonicalization_PropertyTests` (FsCheck): 1000 randomized trees, including reordered-but-equivalent arrays
- `HashDecimalFormatTests`: edge-case decimal values produce no scientific notation
- `ValidationTests`: every validator accepts valid and rejects invalid inputs
- `ValidationSchemaSyncTests`: mechanical rules match schema
- `MessageEnvelopeInvariantTests`: ToolResultMessage Success/Result/Error combinations; StateSnapshot null-document invariants, live-document invariants, and sketch-state invariant; ScreenshotResult ScreenshotBase64/ScreenshotUrl envelope invariant
- `AddinSessionIdFormatTests`: validates 32 lowercase hex format acceptance and rejection of malformed values
- **`CallIdUuidV7FormatTests` (v1.4.0):** valid UUIDv7 strings accepted; UUIDv4 strings rejected; non-UUID strings rejected; uppercase hex rejected; messages carrying malformed `CallId` produce `MalformedEnvelope` error
- `ScreenshotBase64SizeTests`: v1 `ScreenshotResult` with `ScreenshotBase64 > 2 MiB` post-encode is rejected
- `MalformedJsonHandlingTests`: under-4-MiB malformed JSON produces `MalformedEnvelope`, connection stays open, subsequent messages proceed
- **`UnknownMessageTypeVsMalformedEnvelopeTests` (v1.4.0):** seeded inputs for §3.1 trigger categories verify correct error-code routing — unparseable bytes → `MalformedEnvelope`; envelope-invariant violation → `MalformedEnvelope`; well-formed envelope with unknown `type` discriminator → `UnknownMessageType`; malformed `CallId` or `AddinSessionId` → `MalformedEnvelope`
- **`AddinTeardownMessageSerializationTests` (v1.4.0):** round-trips through `IAddinMessage` polymorphic deserialization; `TeardownReason` enum values all serialize as PascalCase strings; missing `Reason` field rejected as `MalformedEnvelope`
- `ApiKeyHandshakeTests`: valid key accepted, missing key rejected with HTTP 401, revoked key rejected with HTTP 401, malformed subprotocol rejected with HTTP 400, apikey subprotocol NEVER appears in echoed `Sec-WebSocket-Protocol` response, apikey subprotocol NEVER written to any application-layer Serilog sink (filter rule verification)
- **`InMemoryLockoutTests` (v1.4.0):** 10 failed handshake attempts against the same `keyId` within 1 hour trigger lockout; subsequent attempts return HTTP 429; lockout expires after 15 minutes; orchestrator restart clears lockout state (documented behavior)
- **`SameEpochReconnectRejectionTests` (v1.4.0):** §9.1 Case A (identical capability fields) accepts reconnect; §9.1 Case B (differing AddinVersion, SwVersion, SwBuild, or ScreenshotRootPath) rejects reconnect with `ClientProtocolErrorMessage(StaleEpoch)` and closes with code 1008; reason text contains `"capability_drift"` and the field keyword
- **`HeartbeatTimeoutTcpAbortTests` (v1.4.0 — CRITICAL):** heartbeat-timeout path calls `WebSocket.Abort()` and does NOT call `CloseAsync` or `CloseOutputAsync`; no Close frame appears in outbound stream; mock peer observes connection-closed-prematurely without preceding Close frame; peer's reported local close code is 1006 but no 1006 was sent over the wire
- `ArchitectureTests`: no DTO types defined outside `BSVoiceCAD.Protocol.*`

## 17. Out of Scope

- State machine for tool-call lifecycle → SPEC-03 (CallId allocation mechanics owned there; SPEC-01 owns format only per §5)
- DHF schema and hash chain → SPEC-04
- SolidWorks COM service contracts → SPEC-02 (including AddinSessionId generation algorithm, §8; AddinTeardownMessage send conditions, §9)
- System prompt, retry correlation, confirmation flow, vision-context enrichment → SPEC-05
- Build sequence, slice prompts, CLAUDE.md → deferred to assembly after module specs converge
- NetFx manual discriminator-switch deserializer: covered in SPEC-02 (because it's add-in implementation, not a wire contract; the wire is identical for both runtimes)
- Chunked-transfer / streaming protocol for messages exceeding 4 MiB → deferred to a future protocol version
- OAuth 2.0 device flow authentication → v2 (`DEPLOYMENT_V1_V2_MIGRATION.md` §2)
- S3 presigned URL screenshot transport → v2 (`DEPLOYMENT_V1_V2_MIGRATION.md` §3)
- Full pagination for very large feature trees → deferred; `IsFeatureTreeTruncated` + `get_feature_tree` is the v1 stopgap (see Section 8)
- API-key persistence beyond `auth.api_keys` table → v1.5 (lockout_events) and v2 (OAuth token tables)
- Authorization-header migration for v1.5 → documented in §12.3; not in v1 scope

## 18. Consumption by Other Specs

SPEC-02 imports `IToolInput`, `IToolResult`, `ToolError`, `ToolErrorCode` (including `InvalidUndoState`, `TemplateNotFound`, `ScreenshotCaptureFailed`), `StateSnapshot`, `SelectionRef`, `TimingReport`, `ToolCallLifecycleState`, `ScreenshotResult`, `AddinReadyMessage` (to populate `ScreenshotRootPath` and `WorkstationId`), `AddinTeardownMessage` (send conditions in SPEC-02 §9), and all `ISolidWorks*Service`-facing types. SPEC-02 owns the `AddinSessionId` generation algorithm (SPEC-02 §8) and references SPEC-01 §5 for `CallId` format validation (no local format definition).

SPEC-03 imports all four polymorphic message families (including `AddinTeardownMessage`), `ToolRegistry`, `ToolTimeouts`, `UndoGroupOutcome`, `CancelReason`, `RollbackReason`, `TeardownReason`, `ClientProtocolErrorCode`, `AddinProtocolErrorCode`, `ToolCallLifecycleState`. SPEC-03 allocates `CallId` values and MUST use `Guid.CreateVersion7()` (or equivalent polyfill on target runtimes) to produce UUIDv7 strings conforming to §5.

SPEC-04 imports `IToolInput`, `IToolResult`, `ToolError`, `StateSnapshot`, `FeatureNode`, `HashCanonicalizer`, `UndoGroupOutcome`, `ToolCallLifecycleState`. SPEC-04 closes DHF sessions on receipt of `AddinTeardownMessage` with reason `explicit_shutdown` (SPEC-00 §6 glossary, SDR-TL-014).

SPEC-05 imports all message records, `ToolRegistry` (specifically `ToolRegistry.Implemented` for tool catalog generation), `AddinProtocolErrorCode` codes, and `ScreenshotResult` (branches on `ScreenshotBase64` vs `ScreenshotUrl` for Anthropic vision API input). SPEC-05 calls SPEC-07 (vocabulary layer) before constructing `ToolCallMessage`.

SPEC-06 imports `StateSnapshot`, `SelectionRef`, `FeatureNode` (for display as opaque JSON — not deserialized into typed objects programmatically), and nothing else. NetArchTest rule limits SPEC-06 → SPEC-01 references to these display types only.

SPEC-07 does not import SPEC-01 directly; SPEC-05 mediates the agent → vocab → tool-call pipeline.

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*
