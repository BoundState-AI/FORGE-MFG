---
title: "BSVoice-CAD SPEC-02 — SolidWorks Adapter"
subtitle: "UI-thread command dispatch, ISolidWorks* service contracts, COM-to-ToolError mapping"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-02 — SolidWorks Adapter

**Version:** 1.2.0
**Status:** DRAFT — revised after Oracle Round 2
**Depends on:** SPEC-00 (module boundaries), SPEC-01 (wire types)
**Consumed by:** SPEC-03 (tool-call lifecycle dispatches through this adapter)
**SolidWorks target:** 2025 SP3+ (COM API backward-compat holds to 2024 SP5 for most methods; Day-0 validation catches any regressions)

## 0. Scope

This spec defines how the add-in process (`BSVoiceCAD.Addin`, .NET Framework 4.8) talks to SolidWorks. Every SolidWorks COM call in the codebase flows through the abstractions defined here. Tool handlers consume `ISolidWorks*Service` interfaces; they never reference `SolidWorks.Interop.*` types directly.

**In scope:** UI-thread command dispatch, six service contracts, the NetFx polymorphic deserializer, COM exception mapping, WebSocket bridge binding and framing, tool-call deduplication registry, CallId format validation, session identity, state versioning, the shared screenshot directory convention, disconnect recovery behavior.

**Out of scope:** tool-call lifecycle state machine (SPEC-03), DHF writes (SPEC-04), agent loop (SPEC-05), SolidWorks types or macro concepts not used by the mandatory or stretch tools.

## 1. Project Shape

```
BSVoiceCAD.Addin/                                # .NET Framework 4.8, COM-registered
├── BSVoiceAddin.cs                              # ISwAddin entry point
├── Abstractions/
│   ├── ISolidWorksGateway.cs                    # Application-level
│   ├── ISolidWorksDocumentService.cs
│   ├── ISolidWorksSketchService.cs
│   ├── ISolidWorksFeatureService.cs
│   ├── ISolidWorksSelectionService.cs
│   └── ISolidWorksUndoService.cs
├── SolidWorksImpl/                              # Real COM implementations
│   ├── SolidWorksGateway.cs
│   ├── SolidWorksDocumentService.cs
│   ├── SolidWorksSketchService.cs
│   ├── SolidWorksFeatureService.cs
│   ├── SolidWorksSelectionService.cs
│   └── SolidWorksUndoService.cs
├── Threading/
│   ├── ISwCommandQueue.cs
│   └── SwUiThreadCommandQueue.cs                # Dispatches to SW UI thread via SynchronizationContext
├── Bridge/
│   ├── WebSocketServer.cs                       # HttpListener + System.Net.WebSockets
│   ├── MessageDispatcher.cs                     # Routes + catch-all exception boundary
│   ├── ProtocolVersionNegotiator.cs
│   ├── ToolCallRegistry.cs                      # In-flight dedup + 60s result cache (session-scoped)
│   └── GeneratedMessageDeserializer.cs          # Manual polymorphic switch (NetFx-only)
├── Tools/
│   ├── IToolHandler.cs
│   ├── ToolDispatcher.cs                        # Registry<string, IToolHandler> + catch-all
│   ├── DocumentTools.cs
│   ├── SketchTools.cs
│   ├── FeatureTools.cs
│   ├── QueryTools.cs                            # screenshot + get_feature_tree
│   ├── SelectionTools.cs                        # Stretch
│   ├── HoleTools.cs                             # Stretch
│   ├── PatternTools.cs                          # Stretch
│   └── EditTools.cs                             # Stretch
├── State/
│   ├── StateSerializer.cs
│   ├── StateVersionCounter.cs                   # Scoped to AddinSessionId
│   ├── AddinSessionIdGenerator.cs
│   ├── FeatureTreeTraverser.cs
│   └── ScreenshotService.cs                     # Writes PNG + returns path + SHA-256
├── Undo/
│   └── UndoObjectScope.cs                       # IDisposable; keyed by UndoGroupId
├── Events/
│   └── EventRelay.cs                            # SW events → StateUpdateMessage
├── Lifecycle/
│   ├── DisconnectHandler.cs                     # Local rollback on WS drop
│   └── ReconnectHandshake.cs
├── Logging/
│   └── FileLogger.cs
└── Properties/AssemblyInfo.cs
```

## 2. Threading Model — Dispatching to the SolidWorks UI Thread

### 2.1 Problem

SolidWorks COM APIs are apartment-threaded and bound to the SolidWorks UI thread's STA. A call to `ModelDoc2.SketchManager.CreateLine(...)` from any other thread either triggers cross-apartment COM marshaling (adding latency and risking deadlock if the UI thread is blocked) or produces `COMException 0x8001010E` (RPC_E_WRONG_THREAD). Creating a separate STA thread does NOT solve this — the SolidWorks COM objects live in the SolidWorks UI thread's apartment, and a different STA thread is a different apartment. The WebSocket server receives messages on thread pool threads. Any path from "message arrives" to "SW API call" must marshal onto the SolidWorks UI thread itself.

### 2.2 Solution

`SwUiThreadCommandQueue` captures the SolidWorks UI thread's `SynchronizationContext` during `BSVoiceAddin.ConnectToSW` (which SolidWorks guarantees runs on its UI thread). If no `SynchronizationContext` is installed on that thread, the add-in installs a `WindowsFormsSynchronizationContext` (available in .NET Framework 4.8 via `System.Windows.Forms`) before capturing. A `TaskScheduler` is derived via `TaskScheduler.FromCurrentSynchronizationContext()`. Every SW COM call in the codebase enters through `ISwCommandQueue.ExecuteAsync(...)`, which posts work onto the SolidWorks UI thread's message queue via this scheduler.

This approach leverages SolidWorks's own Windows message pump — no additional pump or dedicated worker thread is required. Work items posted via the `SynchronizationContext` are processed in FIFO order by the existing message loop.

```csharp
public interface ISwCommandQueue
{
    /// <summary>
    /// Posts work to the SolidWorks UI thread. The TResult is materialized
    /// on the UI thread and the returned Task completes on the caller's context.
    /// If IsOnSwThread is true, the work executes inline (see Section 2.3).
    /// Throws OperationCanceledException if ct is triggered before execution begins.
    /// </summary>
    Task<TResult> ExecuteAsync<TResult>(
        Func<TResult> work,
        CancellationToken ct = default);

    /// <summary>Void-returning variant.</summary>
    Task ExecuteAsync(
        Action work,
        CancellationToken ct = default);

    /// <summary>
    /// Returns true when the current thread is the SolidWorks UI thread.
    /// Used internally for reentrancy detection and in assertions.
    /// </summary>
    bool IsOnSwThread { get; }
}
```

**Startup capture (in ConnectToSW on the SW UI thread):**

```csharp
_swThreadId = Thread.CurrentThread.ManagedThreadId;
if (SynchronizationContext.Current == null)
    SynchronizationContext.SetSynchronizationContext(
        new WindowsFormsSynchronizationContext());
_swSyncContext = SynchronizationContext.Current;
_swTaskScheduler = TaskScheduler.FromCurrentSynchronizationContext();
```

**Dispatch path:** `ExecuteAsync<TResult>` calls `Task.Factory.StartNew(work, ct, TaskCreationOptions.None, _swTaskScheduler)`. The scheduler posts the delegate to the SolidWorks UI thread's message queue. The returned `Task<TResult>` completes when the delegate finishes on the UI thread.

**Startup probe (mandatory gate):** After capturing the `SynchronizationContext` and deriving the `TaskScheduler`, the add-in performs a round-trip probe: it posts a trivial delegate (setting a flag) via the scheduler and awaits completion with a 5-second timeout. If the probe does not complete within the timeout, the `SynchronizationContext` is incompatible with SolidWorks's message pump. The add-in logs a fatal error via `FileLogger`, does NOT start the WebSocket bridge, and sets a `StartupFailed` flag observable via `ISolidWorksGateway`. This is a non-recoverable state for the session; the user must restart SolidWorks and investigate the environment.

**Day-0 note on SynchronizationContext:** SolidWorks 2025 SP3 likely already installs a `SynchronizationContext` (or a compatible WinForms one) on its UI thread since it hosts WinForms-based dialogs internally. Day-0 validation (Section 11 step 2) must document the actual type returned by `SynchronizationContext.Current`. If SolidWorks provides its own context, the install-if-null path is dead code — retain it as a defensive fallback but document the finding.

### 2.3 Reentrancy

If `ExecuteAsync` is called from code already running on the SolidWorks UI thread (i.e., `IsOnSwThread` is true), posting to the message queue and then synchronously awaiting the result would deadlock — the UI thread cannot process the posted message while blocked. Therefore:

**Rule:** When `IsOnSwThread` is true, `ExecuteAsync` checks the `CancellationToken`, then executes the work delegate **inline** and returns a completed `Task<TResult>`. This prevents deadlock from nested dispatch.

**Corollary:** `IsOnSwThread` is used internally by `SwUiThreadCommandQueue` for this reentrancy detection. External callers (tool handlers, services) must not branch on `IsOnSwThread` — the queue handles marshaling transparently.

**Event reentrancy guard:** SolidWorks COM events fire on the UI thread and may fire re-entrantly during a tool's COM calls. `EventRelay` checks a `ToolInProgress` flag managed by `ToolDispatcher`. `ToolDispatcher` sets `ToolInProgress = true` before invoking the tool handler and resets it to `false` in a **`finally` block**, ensuring the flag is cleared regardless of whether the handler succeeds or throws an exception. While `ToolInProgress` is true, incoming SW events are noted but state capture is suppressed — the tool result's `StateAfter` snapshot will already reflect the changes. Events that fire outside tool execution trigger state capture normally.

### 2.4 Cancellation Semantics

The queue supports **two cancellation states** distinguished by where the command is in its lifecycle at the moment of cancel:

- **Queued-cancel (cheap):** The Task was posted to the message queue but the UI thread has not yet picked it up. `CancellationToken` causes the `TaskScheduler` to transition the task to `Canceled` without executing the delegate. The caller observes `OperationCanceledException`.
- **Executing-cancel (cooperative only):** The delegate is already running on the UI thread. COM calls cannot be interrupted. The work runs to completion, and the caller's `Task` resolves normally with the result. The caller's observation is that cancellation was ignored; the lifecycle layer (SPEC-03) treats this case by quarantining the eventual result.

This is the only correct model for SolidWorks. Any spec claim of "preempt COM mid-call" is false. SPEC-03's cancel protocol is built around this reality.

### 2.5 Startup and Shutdown

`SwUiThreadCommandQueue.Start()` is called from `BSVoiceAddin.ConnectToSW` after the `SynchronizationContext` capture and startup probe. `Stop()` is called from `DisconnectFromSW`. On Stop:

1. A `_stopped` flag is set atomically. Subsequent `ExecuteAsync` calls throw `ObjectDisposedException`.
2. Any task currently executing on the UI thread runs to completion (non-preemptible). Its result is stored in the `ToolCallRegistry` even though no client may receive it.
3. No dedicated thread join is required — the queue does not own a thread.

Because the queue does not own a thread, Stop is non-blocking. The only concern is in-flight work, which completes naturally on the SolidWorks UI thread as part of SolidWorks's own shutdown sequence.

### 2.6 Required Test — `StaCommandQueueTests`

Build-breaking. Lives in `BSVoiceCAD.Addin.Tests`. Tests create a thread with `WindowsFormsSynchronizationContext` installed and a running message loop (`Application.Run`) to simulate the SW UI thread.

- Asserts that work dispatched from a thread pool thread executes on the simulated UI thread
- Asserts FIFO ordering under load (1000 enqueues, results in order)
- Asserts queued-cancel behavior (cancel before dequeue → `OperationCanceledException`)
- Asserts executing-cancel behavior (cancel after start → work completes, result returned)
- Asserts reentrancy: nested `ExecuteAsync` from within a running command completes without deadlock
- Asserts `ObjectDisposedException` after Stop
- Asserts startup probe: a non-pumping thread causes the probe to time out and `StartupFailed` to be set

## 3. Service Contracts

All six services are synchronous. Every method runs on the SolidWorks UI thread because all callers dispatch through `ISwCommandQueue.ExecuteAsync`. Errors surface as thrown exceptions; `ToolDispatcher` converts them to `ToolError`.

### 3.1 ISolidWorksGateway

Application-level. Checks SolidWorks is loaded and readable.

```csharp
public interface ISolidWorksGateway
{
    bool IsApplicationOpen { get; }
    string GetApplicationVersion();                        // e.g., "SOLIDWORKS 2025 SP3.0"
    string GetApplicationBuild();                          // e.g., "35.2.0.0139"
    string GetActiveDocumentId();                          // Returns "" if no active doc
}
```

### 3.2 ISolidWorksDocumentService

```csharp
public interface ISolidWorksDocumentService
{
    /// <summary>
    /// Creates a new part using the named template.
    /// Supported templates: "default_mm", "default_inch".
    ///
    /// Template resolution:
    ///   1. Read the default part template path from SolidWorks user preferences
    ///      (ISldWorks.GetUserPreferenceStringValue(swDefaultTemplatePart)).
    ///   2. If the file does not exist on disk, throw TemplateNotFoundException.
    ///   3. Create the document via ISldWorks.NewDocument with the resolved path.
    ///   4. After creation, set the document unit system:
    ///      "default_mm" → MMGS, "default_inch" → IPS.
    ///
    /// Day-0 validation confirms template resolution succeeds for both symbolic names.
    /// </summary>
    DocumentRef NewPart(string template);

    /// <summary>
    /// Returns the active document, or null if none is active.
    /// </summary>
    DocumentRef? GetActiveDocument();

    /// <summary>
    /// Closes the named document without saving. The implementation marks the
    /// document as not requiring save before closing (suppressing any save-changes
    /// modal dialog that would block the UI thread).
    /// Throws ComException with HRESULT 0x80004005 if document not found.
    /// </summary>
    void CloseDocument(string documentId);

    /// <summary>
    /// Builds a full StateSnapshot from the currently active document.
    /// Callers assign StateVersion from their counter; this method builds
    /// only the content of the snapshot.
    /// </summary>
    StateSnapshot CaptureState(long stateVersion, string addinSessionId);
}
```

### 3.3 ISolidWorksSketchService

```csharp
public interface ISolidWorksSketchService
{
    /// <summary>
    /// Inserts a new sketch on the named plane. Transitions the model into
    /// sketch-active state. Returns the newly-allocated SketchId.
    /// </summary>
    string StartSketch(PlaneRef plane);

    /// <summary>
    /// Draws a rectangle from corner (x1, y1) to corner (x2, y2) in sketch
    /// coordinates (meters, SW's internal unit). CenteredOnOrigin places the
    /// rectangle centered on the sketch origin ignoring the provided corners'
    /// offset. Returns SketchEntityIds for the 4 edges and 4 vertices.
    /// </summary>
    (IReadOnlyList<SketchEntityId> Edges, IReadOnlyList<SketchEntityId> Vertices)
        DrawRectangle(double x1, double y1, double x2, double y2, bool centeredOnOrigin);

    /// <summary>
    /// Draws a circle at (cx, cy) with the given radius in sketch coordinates.
    /// Returns the circle edge's SketchEntityId and the center vertex's.
    /// </summary>
    (SketchEntityId Edge, SketchEntityId CenterVertex)
        DrawCircle(double cx, double cy, double radius, bool centeredOnOrigin);

    /// <summary>
    /// Adds a driving dimension. The validator in SPEC-01 ensures Mode and
    /// Target kind agree; this service trusts the validator's determination
    /// of sketch vs persistent context.
    /// </summary>
    string AddDimension(
        DimensionMode mode,
        DimensionTarget target,
        double value,
        DimensionType type);

    /// <summary>
    /// Exits sketch editing and returns the ID of the sketch that was
    /// being edited. The sketch is selected after this call so subsequent
    /// feature tools (extrude, cut_extrude) can consume it directly.
    /// Throws InvalidSketchStateException if no sketch is active.
    /// </summary>
    string ExitSketch();

    /// <summary>
    /// Returns the active sketch ID, or null if no sketch is active.
    /// </summary>
    string? GetActiveSketchId();
}
```

### 3.4 ISolidWorksFeatureService

```csharp
public interface ISolidWorksFeatureService
{
    /// <summary>
    /// Creates a boss extrude feature from the named sketch.
    /// Returns the new feature's EntityRef (Kind = Feature).
    /// </summary>
    EntityRef Extrude(
        string sketchId,
        double depth,
        ExtrudeDirection direction,
        bool mergeResult);

    // Stretch tools below

    EntityRef CutExtrude(string sketchId, double? depth, ExtrudeDirection direction);

    EntityRef Fillet(IReadOnlyList<string> edgeRefs, double radius, bool propagate);

    EntityRef Chamfer(IReadOnlyList<string> edgeRefs, double distance, double angleDeg);

    /// <summary>
    /// Walks the feature tree.
    ///
    /// When compressed is true, the result is filtered:
    ///   - Excluded feature types (by IFeature.GetTypeName2): "OriginProfileFeature",
    ///     "RefPlane", "RefAxis", "RefPoint", "MateGroup", "MaterialFolder",
    ///     "HistoryFolder", "SensorFolder", "DocsFolder".
    ///   - For each included feature, only parameters of type swParamTypeDouble
    ///     are returned. This retains numeric dimensions (e.g., "D1@Sketch1",
    ///     "Depth@Boss-Extrude1") that the agent can pass to ModifyParameter.
    ///
    /// When compressed is false, all features and all parameters are included.
    /// When includeSuppressed is false, suppressed features are omitted.
    /// </summary>
    IReadOnlyList<FeatureNode> GetFeatureTree(bool includeSuppressed, bool compressed);

    /// <summary>
    /// Sets the named parameter's value. ParameterName MUST be the concrete
    /// SolidWorks parameter identifier (e.g., "D1@Sketch1", "Depth@Boss-Extrude1"),
    /// NOT a semantic label. Resolution of semantic labels to concrete identifiers
    /// is the responsibility of the agent (SPEC-05) using GetFeatureTree output.
    ///
    /// The featureId parameter scopes the lookup: the implementation locates
    /// the IFeature by featureId, retrieves its dimension parameters, and
    /// resolves parameterName among them. If parameterName does not belong
    /// to the feature identified by featureId, ArgumentException is thrown
    /// (mapped to InternalError via Section 4). This provides defense-in-depth
    /// against the agent targeting a parameter on the wrong feature.
    ///
    /// Triggers a rebuild and returns after the rebuild completes.
    /// </summary>
    void ModifyParameter(string featureId, string parameterName, double newValue);
}
```

### 3.5 ISolidWorksSelectionService

```csharp
public interface ISolidWorksSelectionService
{
    /// <summary>
    /// Returns the current SW selection as typed EntityRefs.
    /// Empty list if nothing is selected.
    /// </summary>
    IReadOnlyList<EntityRef> GetCurrentSelection();

    /// <summary>
    /// Ray-casts from the viewport center through the model. Returns the
    /// nearest surface hit as an EntityRef, or null if the ray misses.
    /// Stretch-only; requires SelectByRay COM API.
    /// </summary>
    EntityRef? RaycastViewportCenter();

    /// <summary>
    /// Captures the active document's viewport at the requested pixel dimensions.
    /// Writes the PNG to {screenshotDir}/{addinSessionId}/{utcTicks}_{callId}.png.
    /// Returns (absolutePath, sha256Hex).
    /// Screenshot bytes are hashed in-memory before being written so the returned
    /// hash is always consistent with the file content at write time.
    /// </summary>
    (string Path, string Sha256) CaptureScreenshot(
        int widthPx,
        int heightPx,
        string addinSessionId,
        string callId,
        string screenshotDir);
}
```

### 3.6 ISolidWorksUndoService

```csharp
public interface ISolidWorksUndoService
{
    /// <summary>
    /// Opens a SolidWorks undo object group with the given label.
    /// Only one undo group may be active at a time. Calling StartUndoGroup
    /// while a group is already active throws InvalidUndoStateException
    /// (mapped to ToolErrorCode.InvalidUndoState).
    /// </summary>
    void StartUndoGroup(string undoGroupId, string label);

    /// <summary>
    /// Closes the named undo group, making all operations since StartUndoGroup
    /// a single undoable unit in SolidWorks' undo stack.
    /// </summary>
    void CommitUndoGroup(string undoGroupId);

    /// <summary>
    /// Issues SW's undo primitive to revert all operations since StartUndoGroup
    /// for the named group. Best-effort if the group was partially applied.
    /// </summary>
    void RollbackUndoGroup(string undoGroupId);

    /// <summary>
    /// Returns the currently-open undo group ID, or null if none is open.
    /// </summary>
    string? GetActiveUndoGroupId();
}
```

## 4. COM Exception to ToolError Mapping

`ToolDispatcher` wraps every tool handler invocation in a catch-all boundary. The mapping from exception type to `ToolErrorCode`:

| Exception Type | HRESULT | ToolErrorCode | Notes |
|----------------|---------|---------------|-------|
| `COMException` | `0x8001010E` RPC_E_WRONG_THREAD | `InternalError` | Architectural bug; should never fire if `ISwCommandQueue` is used correctly. Tests assert zero occurrences. |
| `COMException` | `0x80004005` E_FAIL | `ComException` | Generic SolidWorks failure. `SwHresult` populated. |
| `COMException` | `0x800A0005` (SW-specific: no active document) | `NoActiveDocument` | Preconditional. |
| `COMException` | other HRESULT | `ComException` | `SwHresult` = hex string. |
| `InvalidSketchStateException` | — | `InvalidSketchState` | From ExitSketch when no sketch active, etc. |
| `InvalidUndoStateException` | — | `InvalidUndoState` | From StartUndoGroup when a group is already active. |
| `TemplateNotFoundException` | — | `TemplateNotFound` | Template resolution failed in NewPart (Section 3.2). |
| `ArgumentException` / `ArgumentNullException` | — | `InternalError` | Handler or service bug. Validation failures are caught upstream by the SPEC-01 validator and never reach this boundary. Full stack trace logged via `FileLogger`. |
| `OperationCanceledException` | — | `Cancelled` | Queued-cancel path. |
| `TimeoutException` | — | `TimedOut` | Thrown by the lifecycle layer, not this adapter; listed for completeness of the vocabulary. |
| `NullReferenceException`, `KeyNotFoundException`, serialization exceptions | — | `InternalError` | Always with full stack trace logged via `FileLogger`. |
| Any other `Exception` | — | `InternalError` | Caught by the catch-all in `ToolDispatcher`. |

The `ToolError.Message` field is the exception's `Message` property, truncated to 512 characters. Full stack traces go to `FileLogger`, never to the wire.

**Note on InvalidOperationException routing:** `ToolDispatcher` catches `InvalidSketchStateException` and `InvalidUndoStateException` (both derive from `InvalidOperationException`) before the general `Exception` handler. An untyped `InvalidOperationException` that does not match either subclass maps to `InternalError`.

## 5. NetFx Polymorphic Deserializer

### 5.1 Problem

`[JsonPolymorphic]` and `[JsonDerivedType]` require `System.Text.Json` 7+, which is not available on .NET Framework 4.8. The add-in cannot use the built-in polymorphic machinery that the orchestrator uses. However, the wire format must be identical for both runtimes.

### 5.2 Solution

`GeneratedMessageDeserializer.cs` in the `Bridge/` folder is a hand-written class that reads the discriminator property from an incoming JSON message and dispatches to the correct concrete type via a switch statement. There are four switches (one per polymorphic family: `IClientMessage`, `IAddinMessage`, `IToolInput`, `IToolResult`) plus two for the nested `PlaneRef` and `DimensionTarget` unions.

The file is checked in to source control. It is NOT generated at build time via T4 or source generators — that path was attempted in earlier spec revisions and proved unreliable on clean CI runners and generated hard-to-diagnose build failures. Instead, the file is hand-maintained and kept in sync with the Protocol assembly via build-breaking tests.

Rationale for check-in over codegen: protocol changes are rare during the hackathon (the types freeze on Day 1). Asking Codex or a human to add one switch case per new type in exchange for eliminating an entire class of build-time infrastructure failures is the right trade.

**Post-hackathon note:** This hand-maintained approach is a time-bounded decision. Before the protocol stabilizes for production, this should be replaced by a source generator or a shared serializer assembly. Evaluate `System.Text.Json` 8+ via NuGet shim for .NET Framework as a potential path.

### 5.3 Sync and Round-Trip Tests

Build-breaking. Live in `BSVoiceCAD.Addin.Tests`.

**`ProtocolDeserializerSyncTests`:** Reflects all concrete implementations of each polymorphic interface (or abstract record) in the Protocol assembly. For each implementation, asserts that the corresponding switch case exists in `GeneratedMessageDeserializer.cs`. Test failure means a new type was added and the deserializer is out of sync.

**`ProtocolDeserializerRoundTripTests`:** For every concrete polymorphic type, constructs a representative instance, serializes it to JSON, and deserializes it through `GeneratedMessageDeserializer`. Asserts:

- The output is the expected concrete type.
- Key field values survive the round trip.
- Nested polymorphic unions (e.g., `PlaneRef`, `DimensionTarget`) route to the correct subtype.

The round-trip tests are the primary correctness signal. The sync tests are a secondary guard that catches missing switch cases without requiring test-data authoring for new types.

### 5.4 Shape of the Switch

```csharp
public static class GeneratedMessageDeserializer
{
    public static IClientMessage DeserializeClientMessage(JsonElement root)
    {
        var type = root.GetProperty("type").GetString();
        return type switch
        {
            "tool_call"              => root.Deserialize<ToolCallMessage>(JsonOptions.Default)!,
            "cancel_tool_call"       => root.Deserialize<CancelToolCallMessage>(JsonOptions.Default)!,
            "start_undo_group"       => root.Deserialize<StartUndoGroupMessage>(JsonOptions.Default)!,
            "commit_undo_group"      => root.Deserialize<CommitUndoGroupMessage>(JsonOptions.Default)!,
            "rollback_undo_group"    => root.Deserialize<RollbackUndoGroupMessage>(JsonOptions.Default)!,
            "client_protocol_error"  => root.Deserialize<ClientProtocolErrorMessage>(JsonOptions.Default)!,
            _ => throw new UnknownMessageTypeException(type)
        };
    }

    // Analogous switches for IAddinMessage (rarely used by the add-in — we SEND these,
    // so the switch is mostly for test code), IToolInput, IToolResult, PlaneRef,
    // DimensionTarget, FeatureParameter.
}
```

The nested polymorphic families (`IToolInput`, `PlaneRef`, `DimensionTarget`, `FeatureParameter`) read the `"tool"` or `"kind"` discriminator before deserializing.

## 6. WebSocket Bridge

### 6.1 Binding and Lifecycle

- Bind: `ws://127.0.0.1:7878/bsvoice-cad`
- Uses `System.Net.HttpListener` + `WebSocketContext` upgrade. No third-party WebSocket library.
- **URL ACL requirement:** `HttpListener` requires HTTP.sys URL ACL registration for non-admin processes. The installer or setup script must run:

```
netsh http add urlacl url=http://127.0.0.1:7878/bsvoice-cad/ user=%USERDOMAIN%\%USERNAME%
```

- Day-0 validation confirms the bind succeeds (see Section 11).
- Single concurrent client. On new connection attempt while a client is active:
  1. The add-in initiates a Close frame to the current client.
  2. If a command is currently executing on the UI thread, the add-in holds the new connection in a pending state (TCP accepted, not yet upgraded to WebSocket) until the command completes.
  3. The executing command's result (if any) is stored in the `ToolCallRegistry` as `Completed` — the normal state transition. No attempt is made to deliver it over the closing connection to the departing client.
  4. Once the executing command completes (or if none was executing), the current client connection is torn down, and the new connection proceeds with the handshake (Section 6.3).
  5. The `ToolCallRegistry` is NOT cleared (it is session-scoped; see Section 6.5). The new client can retrieve any `Completed` result within the 60-second cache window by submitting a `ToolCallMessage` with the same `CallId`.

### 6.2 Framing

One JSON document per WebSocket message. The receive loop MUST buffer frames by calling `WebSocket.ReceiveAsync` in a loop until `WebSocketReceiveResult.EndOfMessage` is true, then deserialize the complete buffer. A maximum message size of 1 MB is enforced; if the accumulated buffer exceeds this limit before `EndOfMessage` is received, the add-in closes the WebSocket with close code `1009` (Message Too Big) and discards the buffer. The close triggers the unplanned disconnect path (Section 9.2). This is simpler and safer than attempting to drain remaining frames and resume normal reads, which risks protocol desynchronization.

### 6.3 Handshake Sequence

1. Client connects.
2. `BSVoiceAddin` generates or reuses `AddinSessionId` (see Section 8).
3. Add-in sends `AddinReadyMessage` with `AddinSessionId`, `ProtocolVersion`, SW version/build, and the resolved `screenshotDir` base path (see Section 7.4).
4. Client validates `ProtocolVersion`. If mismatch, client sends `ClientProtocolErrorMessage` with `ErrorCode = "VERSION_MISMATCH"` and closes.
5. If OK, add-in captures current SW state and sends `StateUpdateMessage(Trigger = InitialState)` with full `StateSnapshot` (or `NullDocumentState` sentinel if no document is open).
6. Add-in sets `HandshakeComplete = true` (see Section 6.4).
7. Client may now send `IClientMessage`s.

### 6.4 MessageDispatcher

Dispatches incoming messages by polymorphic type. Every dispatch path is wrapped in a catch-all that converts any exception to an outbound `AddinProtocolErrorMessage(ErrorCode = "INTERNAL_ERROR")`. Unknown `IClientMessage` subtypes produce `AddinProtocolErrorMessage(ErrorCode = "UNKNOWN_MESSAGE_TYPE")`. Stale-epoch messages (ones with an `AddinSessionId` other than the current one) produce `AddinProtocolErrorMessage(ErrorCode = "STALE_EPOCH")`.

**Handshake gate:** `MessageDispatcher` maintains a `HandshakeComplete` flag, initially false for each connection. The flag is set to true after the `AddinReadyMessage` and the initial `StateUpdateMessage` have been sent (Section 6.3 steps 3 and 5). Any `IClientMessage` received while `HandshakeComplete` is false is rejected with `AddinProtocolErrorMessage(ErrorCode = "HANDSHAKE_INCOMPLETE")` and is not dispatched. This prevents pipelined tool calls from mutating the model before the baseline `StateVersion` exists.

**CallId format validation:** Before dispatching any `ToolCallMessage`, `MessageDispatcher` validates that `CallId` conforms to the UUIDv7 format defined in SPEC-01 (lowercase hexadecimal with hyphens, matching the regex `^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`). Non-conforming `CallId` values are rejected with `AddinProtocolErrorMessage(ErrorCode = "INVALID_CALL_ID")` and are not entered into the `ToolCallRegistry`. This prevents path-traversal attacks via malformed `CallId` values used in screenshot filenames (Section 7.1).

Every tool call runs through `ToolDispatcher` → `ISwCommandQueue.ExecuteAsync` → the correct service implementation → result → `ToolResultMessage` outbound.

### 6.5 ToolCallRegistry (In-Flight Dedup)

A per-session `Dictionary<string, ToolCallTracker>` keyed by `ToolCallMessage.CallId`, scoped to the current `AddinSessionId`. The registry **survives WebSocket reconnects** within the same session and is cleared only on session reset (new `AddinSessionId`).

The registry is **in-memory only**. If the SolidWorks process crashes or is force-terminated, the registry is lost. On process restart, a new `AddinSessionId` is generated (Section 8.1), which the orchestrator detects as a new session. Crash recovery does NOT rely on registry replay — it relies solely on the new-session detection path. Implementers MUST NOT add persistence to the registry; the `AddinSessionId` boundary is the crash recovery mechanism.

Each `ToolCallTracker` owns a `CancellationTokenSource`. This token is passed into `ISwCommandQueue.ExecuteAsync` when the call is enqueued. On disconnect (Section 9.2), `DisconnectHandler` iterates all `Queued` entries and calls `Cancel()` on their `CancellationTokenSource`s. The `TaskScheduler`'s queued-cancel behavior (Section 2.4) transitions the corresponding Tasks to `Canceled` without executing the delegates.

Tracker holds one of five states: `Queued`, `Executing`, `Completed`, `Cancelled`, or `Abandoned`. Transitions:

- `Queued` → `Executing`: when the command begins running on the UI thread.
- `Executing` → `Completed`: when the command finishes. The `ToolResultMessage` is cached with a 60-second retention window.
- `Queued` → `Cancelled` (terminal): on queued-cancel or disconnect while queued. The tracker's `CancellationTokenSource` is cancelled.
- `Executing` → `Abandoned` (terminal): on disconnect while the command is executing on the UI thread (Section 9.2). The command runs to completion but the result is never delivered.

On incoming `ToolCallMessage`:

- If `CallId` is new: create a `ToolCallTracker` with a new `CancellationTokenSource`, enqueue, and track as `Queued`.
- If `CallId` is `Queued` or `Executing`: respond with `AddinProtocolErrorMessage(ErrorCode = "IN_FLIGHT_DUPLICATE")`. The caller knows to wait for the original result.
- If `CallId` is `Completed` and within the 60-second window: replay the cached `ToolResultMessage`.
- If `CallId` is `Completed` but outside the window: respond with `AddinProtocolErrorMessage(ErrorCode = "EXPIRED_CALL_ID")`.
- If `CallId` is `Cancelled` or `Abandoned`: respond with `AddinProtocolErrorMessage(ErrorCode = "TERMINATED_CALL_ID")` so the client knows to use a new `CallId`.

## 7. Screenshot Directory Convention

Screenshots are NOT transmitted over the wire. They are written by the add-in (which runs in the same local user context as the orchestrator) to a shared directory readable by both processes. The `ScreenshotResult` message carries a path and SHA-256 hex.

### 7.1 Path Format

```
{screenshotDir}\{addinSessionId}\{utcTicks}_{callId}.png
```

- `{screenshotDir}` = resolved base directory (see Section 7.4).
- `{addinSessionId}` = current epoch's 32-char hex.
- `{utcTicks}` = `DateTime.UtcNow.Ticks` at capture time, zero-padded to 20 digits for lexicographic sort.
- `{callId}` = `ToolCallMessage.CallId` (UUIDv7, validated per Section 6.4), lowercase.

Both processes compute the same path from the same inputs. No negotiation needed beyond the handshake in Section 7.4.

### 7.2 Ownership and Cleanup

- The add-in creates the directory on demand (`Directory.CreateDirectory` is idempotent).
- The add-in owns writing (screenshot images and sentinel files).
- The orchestrator process is **read-only** with respect to the screenshot directory during normal operation. It reads screenshot images and the sentinel file (Section 7.4) but never creates, modifies, or deletes files in this directory.
- Cleanup is performed by the `cleanup-screenshots.ps1` maintenance script in `tools/`, run independently of the orchestrator process. It deletes files older than 7 days. Not run during demos.

### 7.3 Hash Integrity

The add-in computes SHA-256 on the in-memory PNG bytes before writing to disk, so the hash returned in `ScreenshotResult.Sha256Hash` is authoritative for the content at write time. The orchestrator re-reads and re-hashes on access (see SPEC-05, Anthropic API image ingestion) and compares. Mismatch means the file was modified between write and read — logged as a warning, the screenshot is omitted from context, and the event is written to the DHF decoration table (see SPEC-04).

### 7.4 Path Resolution and Validation

The default `screenshotDir` is `%LOCALAPPDATA%\BSVoiceCAD\screenshots`. If the `BSVOICE_SCREENSHOT_DIR` environment variable is set, its value is used instead. This supports non-standard deployment topologies (e.g., different user contexts, shared network paths).

During `BSVoiceAddin.ConnectToSW`, the add-in creates the session subdirectory (`{screenshotDir}/{addinSessionId}/`) and writes a zero-byte sentinel file `.sentinel` to it. During the WebSocket handshake (Section 6.3), the add-in includes the resolved `screenshotDir` in `AddinReadyMessage`. The orchestrator validates readability by attempting to open the sentinel file at `{screenshotDir}/{addinSessionId}/.sentinel` in read-only mode. If the file does not exist or cannot be read, validation fails. On validation failure, the orchestrator logs a warning and does not send screenshot tool calls for the session — the add-in requires no notification since it only captures screenshots in response to tool calls.

## 8. AddinSessionId

### 8.1 Generation

Generated from inputs that include a random nonce to guarantee uniqueness:

```csharp
public static class AddinSessionIdGenerator
{
    public static string Generate()
    {
        var machineId     = ReadMachineId();
        var swProcessId   = Process.GetCurrentProcess().Id;
        var loadTimestamp  = DateTime.UtcNow.Ticks;
        var nonce         = Guid.NewGuid().ToString("N");
        var input = $"{machineId}:{swProcessId}:{loadTimestamp}:{nonce}";
        using var sha = SHA256.Create();
        var hash = sha.ComputeHash(Encoding.UTF8.GetBytes(input));
        return BitConverter.ToString(hash, 0, 16).Replace("-", "").ToLowerInvariant();
    }

    private static string ReadMachineId()
    {
        try
        {
            using var key = Registry.LocalMachine.OpenSubKey(
                @"SOFTWARE\Microsoft\Cryptography");
            var guid = key?.GetValue("MachineGuid") as string;
            if (!string.IsNullOrEmpty(guid)) return guid;
        }
        catch (SecurityException) { }
        catch (UnauthorizedAccessException) { }
        return FallbackMachineId();
    }

    /// <summary>
    /// Fallback for restricted accounts that cannot read HKLM.
    /// Persists a random GUID to %LOCALAPPDATA%\BSVoiceCAD\.machine-id.
    /// </summary>
    private static string FallbackMachineId()
    {
        var path = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "BSVoiceCAD", ".machine-id");
        if (File.Exists(path))
            return File.ReadAllText(path).Trim();
        var id = Guid.NewGuid().ToString("N");
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        File.WriteAllText(path, id);
        return id;
    }
}
```

32-char lowercase hex. The `Guid.NewGuid()` nonce guarantees uniqueness even under rapid reloads within the same tick resolution or clock rollback. The other inputs (machineId, processId, timestamp) provide traceability but are not relied upon for uniqueness. Not cryptographic; collision-resistance only.

### 8.2 Lifecycle

Generated once per call to `BSVoiceAddin.ConnectToSW`. Stable for the duration of the add-in's connection to SolidWorks. Never regenerated mid-session. A new `AddinSessionId` is generated on:

- SolidWorks process restart (new process, new `ConnectToSW` call).
- Add-in reload within the same SolidWorks process (e.g., via the SolidWorks Add-in Manager — this triggers `DisconnectFromSW` followed by `ConnectToSW`, producing a new session).

Both cases constitute a **session boundary**: the `ToolCallRegistry` is discarded, `StateVersionCounter` resets to 0, and clients that present the old `AddinSessionId` receive `STALE_EPOCH` responses.

### 8.3 StateVersion Counter

`StateVersionCounter` is an instance per `BSVoiceAddin` (reset on every `ConnectToSW`). Starts at 0. Increments monotonically on every `StateUpdateMessage` and `ToolResultMessage.StateAfter`. Never decreases, never wraps. Does not persist across add-in loads (the counter's meaning is scoped to one `AddinSessionId`).

**Ordering guarantee:** `StateVersionCounter` is accessed exclusively from the SolidWorks UI thread. Tool-result state capture runs on the UI thread via `ISwCommandQueue`. Event-triggered state capture runs on the UI thread because SolidWorks fires COM events on its UI thread, and `EventRelay` dispatches capture through `ISwCommandQueue.ExecuteAsync` (which executes inline per Section 2.3 when called from the UI thread). This single-threaded access guarantees a total order on version numbers without explicit synchronization.

**Coalescing rule:** When `ToolInProgress` is true (Section 2.3), `EventRelay` suppresses event-driven state capture. The tool result's `StateAfter` snapshot is the single source of truth for state changes caused by that tool. This prevents duplicate or interleaved version numbers from re-entrant COM events during tool execution.

## 9. Disconnect and Reconnect

### 9.1 Planned Disconnect (Orchestrator-Initiated)

Orchestrator sends WebSocket Close frame with status 1000. Add-in:

1. Waits for any currently-executing tool call to complete on the UI thread. If the call does not complete within 60 seconds, the disconnect is escalated to unplanned (Section 9.2).
2. Closes any open undo group via `ISolidWorksUndoService.RollbackUndoGroup` (best-effort; the agent is expected to have committed already in planned-disconnect cases).
3. Does NOT clear `ToolCallRegistry` (session-scoped).
4. Accepts new connections.

### 9.2 Unplanned Disconnect (Transport Loss)

WebSocket raises `WebSocketException` on read or write, or the close handshake is violated. Add-in:

1. Classifies all tracked calls by their current state:
   - `Queued` calls → marked `Cancelled`. `DisconnectHandler` calls `Cancel()` on each tracker's `CancellationTokenSource` (see Section 6.5). The `TaskScheduler`'s queued-cancel behavior (Section 2.4) transitions their Tasks to `Canceled` without executing the delegates.
   - `Executing` calls → marked `Abandoned`. The command continues running on the UI thread to completion (COM calls are non-preemptible), but its result is cached in the registry without delivery. The model may have been mutated.
   - `Completed` calls → unchanged.
2. Attempts `RollbackUndoGroup` on any open undo group, local best-effort (see disclaimer below).
3. Does NOT clear the registry.
4. Awaits a new connection.

**Disclaimer on local rollback after transport loss:** If the add-in is hung or crashed (the likely cause of unplanned disconnect), the rollback attempt may not execute. In that case the SolidWorks model may be left in a partial state. The orchestrator records the outcome as `Abandoned` on reconnect when it observes that the prior undo group has no matching `UndoGroupOutcomeMessage`. The viewer shows this as an ambiguous termination.

### 9.3 Reconnect

Every new connection begins with the handshake in Section 6.3. If the add-in has a still-open undo group from a prior connection that didn't clean up (detected via `GetActiveUndoGroupId() != null`), the add-in rolls it back before sending `AddinReadyMessage`. This is a defensive measure; normal flow never leaves undo groups open across connections.

On reconnect within the same `AddinSessionId`, the `ToolCallRegistry` is available. The orchestrator can detect `Abandoned` or `Cancelled` calls via `TERMINATED_CALL_ID` responses and issue new `CallId`s. The orchestrator can also detect abandoned mutations by comparing the pre-disconnect and post-reconnect state versions.

## 10. Required Tests (Build-Breaking)

All in `BSVoiceCAD.Addin.Tests`, NetFx 4.8. All run on Windows-only CI because of the NetFx target; that's explicitly documented in SPEC-00 SDR-TL-005.

- `StaCommandQueueTests` — see Section 2.6 (includes startup probe timeout test)
- `ReentrancyDeadlockTests` — nested `ExecuteAsync` from within a running command completes without deadlock (Section 2.3)
- `ProtocolDeserializerSyncTests` — see Section 5.3
- `ProtocolDeserializerRoundTripTests` — see Section 5.3
- `ToolDispatcherTests` — exception-to-ToolError mapping for each row of the table in Section 4, using a mock `ISolidWorksGateway`. Includes: assert `ToolInProgress` is false after a handler throws an exception (verifies try/finally cleanup per Section 2.3)
- `InFlightDeduplicationTests` — the five registry states (`Queued`, `Executing`, `Completed`, `Cancelled`, `Abandoned`) and all transitions from Section 6.5; verifies CancellationTokenSource ownership and cancel behavior
- `DisconnectRollbackTests` — simulated transport loss mid-undo-group, assert best-effort rollback executes
- `DisconnectExecutingCommandTests` — verify that a command in `Executing` state at disconnect time is marked `Abandoned`, continues to completion, and its result is cached
- `ProtocolVersionNegotiationTests` — handshake pass and fail paths
- `AddinSessionIdGenerationTests` — stability within one process, difference across process restarts, registry fallback path, and consecutive `Generate()` calls always produce different IDs (verifies Guid nonce entropy)
- `ScreenshotPathComputationTests` — same inputs produce identical paths on add-in and orchestrator sides
- `UndoNestingRejectionTests` — assert that `StartUndoGroup` while a group is active throws `InvalidUndoStateException`
- `CallIdFormatValidationTests` — assert that non-UUIDv7 CallIds (including path traversal sequences like `../../../etc/passwd`, empty strings, and strings with path separators) are rejected with `INVALID_CALL_ID` and never enter the registry or reach the filesystem
- `HandshakeGateTests` — assert that `ToolCallMessage` received before `HandshakeComplete` is rejected with `HANDSHAKE_INCOMPLETE`; assert that messages are dispatched normally after handshake completes
- `ModifyParameterScopeTests` — assert that `ModifyParameter` throws `ArgumentException` when `parameterName` does not belong to the feature identified by `featureId`

Integration tests that require SolidWorks itself (`ToolRoundtripTests` et al.) live in `BSVoiceCAD.Integration.Tests` and are tagged `[Trait("Category", "RequiresSolidWorks")]` so CI can skip them on runners without SW installed.

## 11. Day-0 Validation

Before Day 1 implementation starts:

1. Visual Studio 2022 loads a hello-world NetFx 4.8 add-in with SW 2025 SP3+.
2. Sample add-in registers via COM, loads in SW, and a menu command executes a trivial `new_part` via `ISldWorks.NewDocument`. **Verify:** inspect `SynchronizationContext.Current` on the SW UI thread inside `ConnectToSW` and **document the actual type**. If SolidWorks already provides a context (likely), record this — the install-if-null path is dead code but retained as a fallback. If null, install `WindowsFormsSynchronizationContext`. In either case, confirm the startup probe round-trip completes within 5 seconds. If the probe fails, confirm the add-in refuses to start the WebSocket bridge and logs a fatal error.
3. Sample WebSocket server binds to `127.0.0.1:7878`, accepts a test client, echoes a JSON message. **Verify:** URL ACL is registered or not needed; if bind fails, run `netsh http add urlacl url=http://127.0.0.1:7878/bsvoice-cad/ user=%USERDOMAIN%\%USERNAME%` and confirm. Document whether ACL is required for the demo machine.
4. `SHA256.Create()` / `ComputeHash` and `File.WriteAllBytes` measured under GE75's normal IO load; screenshot round-trip (capture → hash → write) completes < 500ms at 1024×768.
5. `ProtocolDeserializerSyncTests` and `ProtocolDeserializerRoundTripTests` pass on a stub Protocol project with 2 types.
6. **Template resolution:** confirm `ISldWorks.GetUserPreferenceStringValue(swDefaultTemplatePart)` returns a valid path on the demo machine. If templates are missing, install them. Verify unit-system switching for both MMGS and IPS.
7. **Machine ID:** confirm `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid` is readable from the add-in's process context; test the fallback path by temporarily restricting access.
8. **Screenshot sentinel:** confirm the add-in can create the session subdirectory and write the `.sentinel` file. Confirm the orchestrator process can open and read the sentinel file in read-only mode from its process context.

Any failure halts Day 1 start and triggers the corresponding contingency.

## 12. Out of Scope

- Tool-call timeout mechanics (per-tool timeouts, cancel protocol) → SPEC-03. This adapter just exposes queued-cancel and executing-cancel semantics; the lifecycle layer decides when to issue cancel.
- DHF row construction → SPEC-04
- Agent loop, retry correlation, user confirmation → SPEC-05
- Viewer reads → SPEC-06
- Voice capture (Whisper) → SPEC-05 (audio-to-text is an orchestrator concern; the add-in doesn't see audio)

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*