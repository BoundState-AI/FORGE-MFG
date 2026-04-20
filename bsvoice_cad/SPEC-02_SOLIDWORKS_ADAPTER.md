---
title: "BSVoice-CAD SPEC-02 — SolidWorks Adapter"
subtitle: "UI-thread command dispatch, ISolidWorks* service contracts, COM-to-ToolError mapping, AddinSessionId generation, screenshot base64 producer, teardown send conditions"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-02 — SolidWorks Adapter

**Version:** 1.4.0
**Status:** DRAFT — cross-pass R3 findings applied + peer review editorial clarifications + R4 editorial (C4 sharpened warning) + R5 H2-R cleanup
**Depends on:** SPEC-00 v1.5.0 (module boundaries, SDR-TL-014 AddinTeardownMessage declaration), SPEC-01 v1.4.0 (wire types including `AddinTeardownMessage` and `TeardownReason` enum, error-code split for `MalformedEnvelope` vs `UnknownMessageType`, `ScreenshotCaptureFailed` ToolErrorCode), `DEPLOYMENT_V1_V2_MIGRATION.md` v1.1.0 (v1/v2 deployment boundary)
**Consumed by:** SPEC-03 (tool-call lifecycle dispatches through this adapter)
**SolidWorks target:** 2025 SP3+ (COM API backward-compat holds to 2024 SP5 for most methods; Day-0 validation catches any regressions)

**Changelog v1.3.0 → v1.4.0:**
- **§4 exception mapping updated (R3 R1-ISSUE-014):** `ScreenshotTooLargeException` maps to the new `ToolErrorCode.ScreenshotCaptureFailed` (SPEC-01 v1.4.0 §6.1) instead of the generic `InternalError`. This gives the orchestrator's lifecycle layer a structured failure signal distinct from "everything else broke." `ScreenshotCaptureFailed` is also emitted when the underlying `ISolidWorksSelectionService.CaptureScreenshot` COM call fails for reasons other than `NoActiveDocument` (e.g., rendering pipeline error during viewport capture).
- **§6.5 MessageDispatcher updated (R3 R2-ISSUE-009):** The error-code split between `MalformedEnvelope` and `UnknownMessageType` is now aligned with SPEC-01 §3.1 authoritative definition. Unparseable bytes, malformed envelopes, envelope-invariant violations, and malformed `CallId` / `AddinSessionId` all map to `MalformedEnvelope`. Well-formed envelopes with an unrecognized `type` discriminator map to `UnknownMessageType`. Previously the spec routed all unknown inputs to `UnknownMessageType`.
- **§7.2 screenshot reduction terminal behavior (R3 R2-ISSUE-002):** After four reduction iterations without fitting under 2 MiB, the producer now returns a structured `ToolError` with `ScreenshotCaptureFailed` code rather than throwing an untyped exception. The failure path is fully deterministic and produces a valid `ToolResultMessage` with `Success = false`. Added an explicit full-message-size check AFTER the screenshot payload is assembled with `StateAfter.FeatureTree`, because the cumulative envelope could exceed 4 MiB even if the screenshot alone fits under 2 MiB; the final size check can trigger feature-tree truncation per SPEC-01 §8 or a deterministic `ToolError` if truncation is not sufficient.
- **§9.0 NEW — AddinTeardownMessage send conditions (R3 R1-ISSUE-007 / SDR-TL-014):** Specifies which code paths emit `AddinTeardownMessage`, which `TeardownReason` value to use for each, the best-effort delivery discipline, and what the add-in does if the message cannot be sent. Graceful shutdown always attempts the send; crash and force-kill paths have no opportunity to send, and the orchestrator detects those as `heartbeat_timeout` per SPEC-00 glossary.
- **§9.1 planned-disconnect 60s escalation (R3 R2-ISSUE-003):** Defines the exact post-timeout action when a tool call does not complete within 60 seconds of the disconnect request. The connection is force-closed (WebSocket abort, no Close frame), `AddinTeardownMessage` is NOT sent (the COM call is still running on the UI thread and blocking the message-send path), the undo group is NOT rolled back (the in-progress COM call would be interrupted, but the mutation has already happened and the model is in an unknown state), and the add-in logs the stuck call for post-mortem. The orchestrator observes this as `heartbeat_timeout` rather than `explicit_shutdown`.
- **§10 tests added:** `AddinTeardownMessageSendConditionsTests`, `ScreenshotCaptureFailedOnAbandonTests`, `PlannedDisconnectEscalationTests`, `UnknownMessageTypeVsMalformedEnvelopeReceiveTests` (SPEC-02 receive-side variant; SPEC-01 has the authoritative producer-side tests).
- **Depends-on updated** to reflect SPEC-01 v1.4.0, SPEC-00 v1.5.0, and migration doc v1.1.0 dependencies.

**Editorial v1.4.0+ (post-R3 peer review, no version bump):**
- **M1** — §8.1 machine-id portability: noted that if `%LOCALAPPDATA%` is migrated across workstations via backup/restore, the machine-id degrades from uniqueness input to correlation input. `AddinSessionId` uniqueness is preserved by the nonce and load-timestamp entropy; cross-workstation conflict detection is the responsibility of SPEC-04's `workstation_id` column (`Environment.MachineName`), not this fallback id.
- **M2** — §9.0.4 / SPEC-04 §6.2 closure-reason observability: clarified that `TeardownReason` values (`SolidWorksShutdown`, `AddinDisabled`, `ProcessExit`, `UserInitiated`) are captured in orchestrator structured logs (Serilog `session_closed` event), NOT in the DHF schema. Closure attribution is observability-grade, not audit-chain-grade; DHF integrity does not depend on closure reason.
- **M3** — §9.0.1 send-trigger matrix footnote: `TeardownReason.UserInitiated` is reserved for the post-v1 taskpane UI path. v1 emits exactly three of the four values (`SolidWorksShutdown`, `AddinDisabled`, `ProcessExit`).
- **M4** — §9.1.1 step 7 `Environment.Exit(1)` impact disclosure: rewritten to state plainly that the 5-second post-grace-period exit terminates the SolidWorks process because the add-in runs in-process. Users see SolidWorks quit without the usual save-prompt; unsaved work in other documents may be lost. Acknowledged degradation, not defect — bounded-shutdown-time is the contract.
- **M5** — §6.4 handshake race: documented that the add-in sends its initial `StateUpdateMessage` immediately after `AddinReadyMessage` without awaiting orchestrator confirmation of `ProtocolVersion`. If the orchestrator rejects the handshake with a `VersionMismatch` close, the add-in's already-sent `StateUpdateMessage` may be in a closing connection; this is tolerated by the wire protocol (the orchestrator drains the pending frame).
- **m1** — SPEC-01 v1.4.0+ m2 reciprocal: optional defensive check recommended in §8.1 — if the 16-byte SHA-256 truncation returns all-zeros (2^-128 probability), regenerate with a fresh nonce before returning. Never ship production output equal to the SPEC-01 reserved sentinel.
- **Editorial R4-C4** — sharpened `Environment.Exit(1)` user-facing warning policy in §9.1.1 step 7. The external-watchdog architecture (Oracle R4 correctly flagged as strictly better) is deferred to v1.5 backlog with explicit tracking. v1 ships with a mandatory pre-exit warning dialog and honest documentation of the host-wide data-loss risk.
- **Editorial R5-H2-R** — §11 Day-0 validation item 11 no longer checks a DHF `closure_reason` column (that column does not exist; SPEC-00 v1.5.1 C3 established Serilog-only observability for closure reason). The item now validates the Serilog `session_closed` structured-log event with explicit field list (`session_id`, `closure_reason`, `tenant_id`) and confirms `dhf.sessions.ended_at` is set.

**Changelog v1.2.0 → v1.3.0:**
- §0 scope: co-location assumption reversed. Add-in runs local on Windows workstation; orchestrator is Railway-hosted at `vcad.boundstate.ai`.
- §6: WebSocket bridge role INVERTED. The add-in is now a WebSocket CLIENT connecting out to `wss://vcad.boundstate.ai/ws`. Previously the add-in was a server binding to `ws://127.0.0.1:7878`. URL ACL requirement removed (no local server). Authentication handshake via API key per SPEC-01 §12.
- §7: screenshot transport rewritten. No shared filesystem, no `.sentinel` file, no path convention. The add-in captures PNG in memory, computes SHA-256, base64-encodes, applies the 2 MiB cap with progressive dimension reduction, and populates `ScreenshotResult.ScreenshotBase64`. Files are still staged to a local cache directory for audit and v2 forward-compat, but that path is never transmitted to the orchestrator.
- §8: `AddinSessionIdGenerator` remains authoritative here. Explicit cross-reference note that SPEC-01 §9 owns the format contract (`^[0-9a-f]{32}$`) and epoch rotation semantics.
- §9: disconnect/reconnect logic updated for network-loss case. Add-in now handles extended network outages (Wi-Fi drops, VPN reconnects) with exponential backoff, not just process-level disconnect.
- §10 tests: removed `ScreenshotSentinelTests` (no sentinel); added `ScreenshotBase64ProducerTests` and `WebSocketClientReconnectTests`.
- Test project name standardized: `BSVoiceCAD.Integration.Tests` → `BSVoiceCAD.Tests.Integration` (per SPEC-00 §3 canonical).
- §4: `InvalidUndoState` and `TemplateNotFound` exception-to-error mappings remain authoritative here (unchanged behavior; now paired with the enum values added to SPEC-01 v1.3.0 §6.1).

## 0. Scope

This spec defines how the add-in process (`BSVoiceCAD.Addin`, .NET Framework 4.8, running local on the engineer's Windows workstation) talks to SolidWorks and to the Railway-hosted orchestrator. Every SolidWorks COM call in the codebase flows through the abstractions defined here. Tool handlers consume `ISolidWorks*Service` interfaces; they never reference `SolidWorks.Interop.*` types directly.

**In scope:** UI-thread command dispatch, six service contracts, the NetFx polymorphic deserializer, COM exception mapping, WebSocket client binding and framing (outbound to `wss://vcad.boundstate.ai/ws`), tool-call deduplication registry, CallId format validation, session identity (the authoritative `AddinSessionIdGenerator`), state versioning, the screenshot base64 producer, disconnect and network-loss recovery behavior, `AddinTeardownMessage` send conditions and best-effort delivery discipline.

**Out of scope:** tool-call lifecycle state machine (SPEC-03), DHF writes (SPEC-04), agent loop (SPEC-05), SolidWorks types or macro concepts not used by the mandatory or stretch tools, API-key handshake validation (that's SPEC-01 §12 + orchestrator code; the add-in SENDS the handshake), OAuth device flow (v2; see migration doc §2).

**Deployment model:** Add-in runs local (Windows, .NET Framework 4.8, COM-registered with SolidWorks). Orchestrator runs on Railway at `vcad.boundstate.ai`. The WebSocket between them is `wss://` (TLS terminated at Railway edge), crosses the public internet, and is authenticated via API key in the handshake subprotocol per SPEC-01 §12. **The add-in is the WebSocket client, not a server.**

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
│   ├── WebSocketClient.cs                       # ClientWebSocket (NetFx 4.8) with reconnect + backoff
│   ├── MessageDispatcher.cs                     # Routes + catch-all exception boundary
│   ├── ProtocolVersionNegotiator.cs
│   ├── ApiKeyCredentialStore.cs                 # Reads API key from Windows Credential Manager
│   ├── ToolCallRegistry.cs                      # In-flight dedup + 60s result cache (session-scoped)
│   ├── TeardownMessageSender.cs                 # v1.4.0 — AddinTeardownMessage best-effort send
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
│   ├── AddinSessionIdGenerator.cs               # Authoritative algorithm (see §8)
│   ├── FeatureTreeTraverser.cs
│   └── ScreenshotProducer.cs                    # Captures PNG, computes SHA-256, base64-encodes
├── Undo/
│   └── UndoObjectScope.cs                       # IDisposable; keyed by UndoGroupId
├── Events/
│   └── EventRelay.cs                            # SW events → StateUpdateMessage
├── Lifecycle/
│   ├── DisconnectHandler.cs                     # Local rollback on transport loss
│   ├── ShutdownCoordinator.cs                   # v1.4.0 — orchestrates graceful shutdown teardown send
│   └── ReconnectHandshake.cs                    # Exponential backoff + resume via same AddinSessionId
├── Logging/
│   └── FileLogger.cs
└── Properties/AssemblyInfo.cs
```

## 2. Threading Model — Dispatching to the SolidWorks UI Thread

### 2.1 Problem

SolidWorks COM APIs are apartment-threaded and bound to the SolidWorks UI thread's STA. A call to `ModelDoc2.SketchManager.CreateLine(...)` from any other thread either triggers cross-apartment COM marshaling (adding latency and risking deadlock if the UI thread is blocked) or produces `COMException 0x8001010E` (RPC_E_WRONG_THREAD). Creating a separate STA thread does NOT solve this — the SolidWorks COM objects live in the SolidWorks UI thread's apartment, and a different STA thread is a different apartment. The WebSocket client receives messages on thread pool threads. Any path from "message arrives" to "SW API call" must marshal onto the SolidWorks UI thread itself.

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

**Startup probe (mandatory gate):** After capturing the `SynchronizationContext` and deriving the `TaskScheduler`, the add-in performs a round-trip probe: it posts a trivial delegate (setting a flag) via the scheduler and awaits completion with a 5-second timeout. If the probe does not complete within the timeout, the `SynchronizationContext` is incompatible with SolidWorks's message pump. The add-in logs a fatal error via `FileLogger`, does NOT start the WebSocket client, and sets a `StartupFailed` flag observable via `ISolidWorksGateway`. This is a non-recoverable state for the session; the user must restart SolidWorks and investigate the environment.

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
    ///   2. If the file does not exist on disk, throw TemplateNotFoundException
    ///      (mapped to ToolErrorCode.TemplateNotFound via Section 4).
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
    /// Captures the active document's viewport at the requested pixel dimensions
    /// and returns PNG bytes in memory plus the SHA-256 of those bytes. The caller
    /// (ScreenshotProducer) handles base64 encoding and size-cap enforcement.
    /// Throws ComException if viewport capture fails (mapped to
    /// ToolErrorCode.ScreenshotCaptureFailed via Section 4 per v1.4.0).
    /// </summary>
    (byte[] PngBytes, string Sha256Hex, int ActualWidthPx, int ActualHeightPx)
        CaptureScreenshot(int widthPx, int heightPx);
}
```

**Note:** The screenshot service no longer writes to disk or takes a path parameter. The add-in captures PNG bytes in memory, computes the hash, and returns both. `ScreenshotProducer` (Section 7) handles encoding, capping, and local cache staging.

### 3.6 ISolidWorksUndoService

```csharp
public interface ISolidWorksUndoService
{
    /// <summary>
    /// Opens a SolidWorks undo object group with the given label.
    /// Only one undo group may be active at a time. Calling StartUndoGroup
    /// while a group is already active throws InvalidUndoStateException
    /// (mapped to ToolErrorCode.InvalidUndoState via Section 4).
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
| `COMException` raised from `ISolidWorksSelectionService.CaptureScreenshot` (v1.4.0) | any | `ScreenshotCaptureFailed` | Viewport rendering pipeline error. `SwHresult` populated in `Context`. Distinct from `NoActiveDocument` which takes precedence when applicable. |
| `COMException` | other HRESULT | `ComException` | `SwHresult` = hex string. |
| `InvalidSketchStateException` | — | `InvalidSketchState` | From ExitSketch when no sketch active, etc. |
| `InvalidUndoStateException` | — | `InvalidUndoState` | From StartUndoGroup when a group is already active. |
| `TemplateNotFoundException` | — | `TemplateNotFound` | Template resolution failed in NewPart (Section 3.2). |
| **`ScreenshotTooLargeException` (v1.4.0)** | — | **`ScreenshotCaptureFailed`** | Thrown by `ScreenshotProducer` when 4 reduction iterations cannot fit the encoded PNG under the 2 MiB cap (Section 7.2). Replaces the v1.3.0 mapping to `InternalError`. `Context` dictionary includes `iterations_attempted` and `final_encoded_bytes` for post-mortem. |
| **`ToolResultOversizeException` (v1.4.0)** | — | **`ScreenshotCaptureFailed`** | Thrown by `ToolDispatcher` when the fully-assembled `ToolResultMessage` (screenshot + `StateAfter.FeatureTree` + other fields) exceeds the 4 MiB wire-message cap even after feature-tree truncation per SPEC-01 §8. `Context` includes `assembled_bytes` and `was_feature_tree_truncated`. |
| `ArgumentException` / `ArgumentNullException` | — | `InternalError` | Handler or service bug. Validation failures are caught upstream by the SPEC-01 validator and never reach this boundary. Full stack trace logged via `FileLogger`. |
| `OperationCanceledException` | — | `Cancelled` | Queued-cancel path. |
| `TimeoutException` | — | `TimedOut` | Thrown by the lifecycle layer, not this adapter; listed for completeness of the vocabulary. |
| `NullReferenceException`, `KeyNotFoundException`, serialization exceptions | — | `InternalError` | Always with full stack trace logged via `FileLogger`. |
| Any other `Exception` | — | `InternalError` | Caught by the catch-all in `ToolDispatcher`. |

The `ToolError.Message` field is the exception's `Message` property, truncated to 512 characters. Full stack traces go to `FileLogger`, never to the wire.

**Note on InvalidOperationException routing:** `ToolDispatcher` catches `InvalidSketchStateException` and `InvalidUndoStateException` (both derive from `InvalidOperationException`) before the general `Exception` handler. An untyped `InvalidOperationException` that does not match either subclass maps to `InternalError`.

**Note on `ScreenshotCaptureFailed` (v1.4.0):** This enum value was added to SPEC-01 §6.1 in v1.4.0. It is used for three distinct failure modes, all related to the add-in being unable to deliver a valid screenshot payload to the orchestrator:

1. Viewport capture COM call failed during `ISolidWorksSelectionService.CaptureScreenshot` (rendering pipeline error, driver issue, GPU reset).
2. Four dimension-reduction iterations failed to fit the PNG under the 2 MiB post-encode cap (pathological viewport with very high entropy — Section 7.2).
3. The fully-assembled `ToolResultMessage` exceeds the 4 MiB wire-message cap even after the screenshot fits under 2 MiB AND after `StateAfter.FeatureTree` has been truncated per SPEC-01 §8 (combined screenshot + state size is too large — Section 7.5).

All three cases produce a well-formed `ToolResultMessage` with `Success = false` and `Error.Code = ScreenshotCaptureFailed`; the orchestrator's lifecycle layer handles them deterministically (no special-case exception path).

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

**`ProtocolDeserializerSyncTests`:** Reflects all concrete implementations of each polymorphic interface (or abstract record) in the Protocol assembly. For each implementation, asserts that the corresponding switch case exists in `GeneratedMessageDeserializer.cs`. Test failure means a new type was added and the deserializer is out of sync. v1.4.0 adds `AddinTeardownMessage` to the `IAddinMessage` family — the sync test asserts this case is present.

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

    // Analogous switches for IAddinMessage (includes "addin_teardown" per v1.4.0,
    // rarely used by the add-in on the receive path — we SEND this message,
    // so the switch is mostly for test code), IToolInput, IToolResult, PlaneRef,
    // DimensionTarget, FeatureParameter.
}
```

The nested polymorphic families (`IToolInput`, `PlaneRef`, `DimensionTarget`, `FeatureParameter`) read the `"tool"` or `"kind"` discriminator before deserializing.

## 6. WebSocket Client

### 6.1 Role Inversion from v1.2.0

In prior drafts, the add-in was a WebSocket server binding to `ws://127.0.0.1:7878` and the orchestrator was the client. This is reversed in v1.3.0: **the add-in is the WebSocket client, and the orchestrator (Railway-hosted at `vcad.boundstate.ai`) is the server.** The add-in dials out to the orchestrator; the orchestrator never dials in to the add-in. This matches the deployment reality where the add-in runs behind residential or corporate NAT and cannot be reached directly from Railway.

Consequences:
- No `HttpListener`. No URL ACL registration required.
- Outbound TLS connection initiated by `ClientWebSocket` (available in .NET Framework 4.8 via `System.Net.WebSockets.ClientWebSocket`).
- Add-in MUST gracefully handle network loss (Wi-Fi drops, VPN reconnects, carrier hiccups). See §9.2.

### 6.2 Binding and Lifecycle

- **Target:** `wss://vcad.boundstate.ai/ws` (v1 URL; per-tenant subdomains deferred to v2 per migration doc §2).
- **TLS:** TLS 1.2 minimum, certificates validated via Windows certificate store trust chain.
- **Authentication:** API key in `Sec-WebSocket-Protocol` subprotocol header per SPEC-01 §12.3. The add-in reads the key from Windows Credential Manager (target `BoundState.BSVoiceCAD.ApiKey`) via `ApiKeyCredentialStore`. If the credential is missing, the add-in logs a fatal error, surfaces a UI error to the user ("BSVoice-CAD API key not found in Windows Credential Manager"), and does NOT attempt a connection.
- **Subprotocol header format:**

```csharp
_client.Options.AddSubProtocol("bsvoice-cad-v1");
_client.Options.AddSubProtocol($"apikey-{keyId}.{secret}");
```

- **Connect call:** `await _client.ConnectAsync(new Uri("wss://vcad.boundstate.ai/ws"), cancellationToken)`.
- **Handshake failure handling:** per SPEC-01 §12.4. `WebSocketException` with HTTP 401 → user-facing "auth failed" error, no auto-retry. HTTP 400 → retry with backoff (5s, 30s, 5min, then give up). HTTP 429 → back off per `Retry-After` header (bounded to 15 minutes). Other HTTP statuses → log and retry with backoff.

### 6.3 Framing

One JSON document per WebSocket message. The receive loop MUST buffer frames by calling `WebSocket.ReceiveAsync` in a loop until `WebSocketReceiveResult.EndOfMessage` is true, then deserialize the complete buffer. A maximum message size of 4 MiB is enforced (matching SPEC-01 §3); if the accumulated buffer exceeds this limit before `EndOfMessage` is received, the add-in sends an `AddinProtocolErrorMessage` with `ErrorCode = MessageTooLarge`, then drains-and-discards remaining frames to maintain WebSocket stream synchronization, then continues normal operation (connection remains open). This matches the oversize-message handling defined in SPEC-01 §3.

Malformed JSON (under 4 MiB) produces `AddinProtocolErrorMessage` with `ErrorCode = MalformedEnvelope` per SPEC-01 §3; connection remains open.

### 6.4 Handshake Sequence

1. Add-in calls `ClientWebSocket.ConnectAsync` with subprotocols (Section 6.2).
2. Orchestrator validates API key per SPEC-01 §12.3.
3. On success, WebSocket upgrade completes. Add-in generates or reuses `AddinSessionId` (see Section 8).
4. Add-in sends `AddinReadyMessage` with `AddinSessionId`, `ProtocolVersion`, SW version/build, `ScreenshotRootPath` (local cache directory, see §7.3), and `WorkstationId` (`Environment.MachineName`).
5. Orchestrator validates `ProtocolVersion`. If mismatch, orchestrator sends `ClientProtocolErrorMessage` with `ErrorCode = VersionMismatch` and closes the WebSocket.
6. If OK, add-in captures current SW state and sends `StateUpdateMessage(Trigger = InitialState)` with full `StateSnapshot` (or null-document state if no document is open).
7. Add-in sets `HandshakeComplete = true` (see Section 6.5).
8. Orchestrator may now send `IClientMessage`s.

**Handshake race note (editorial M5).** The add-in sends step 6's initial `StateUpdateMessage` immediately after `AddinReadyMessage` without waiting for orchestrator confirmation of `ProtocolVersion` validation (step 5). If the orchestrator rejects the handshake — step 5's `VersionMismatch` close, or any other pre-handshake-completion rejection — the add-in's already-sent `StateUpdateMessage` may be arriving at a closing connection. This is tolerated by the wire protocol: the orchestrator drains the pending frame before completing the close, and the add-in's next reconnect attempt starts from a clean state. The race is intentional — waiting for protocol-version confirmation between steps 5 and 6 would add a round-trip and introduce a separate "waiting for confirmation" state machine; the current design lets the WebSocket close semantics absorb the race naturally.

### 6.5 MessageDispatcher

Dispatches incoming messages by polymorphic type. Every dispatch path is wrapped in a catch-all that converts any exception to an outbound `AddinProtocolErrorMessage` (for parse failures; handler exceptions produce `ToolResultMessage` with `Success = false` and a `ToolError`).

**Error-code routing (v1.4.0, aligned with SPEC-01 §3.1):**

- **Unparseable bytes** (invalid UTF-8, `JsonSerializer.Deserialize` throws, truncated message) → `AddinProtocolErrorMessage(ErrorCode = MalformedEnvelope)`.
- **Missing or wrong-typed required envelope fields** (`messageId` not a string, `addinSessionId` missing, `type` not a string) → `AddinProtocolErrorMessage(ErrorCode = MalformedEnvelope)`.
- **Envelope invariant violations** (e.g., `ToolResultMessage` with `Success = true` and `Error` populated, `ScreenshotResult` with both or neither of `ScreenshotBase64`/`ScreenshotUrl`) → `AddinProtocolErrorMessage(ErrorCode = MalformedEnvelope)`. This case applies only to add-in-produced messages during test harnesses; the add-in generally validates its own outbound payloads before send.
- **`AddinSessionId` fails format validation** (`^[0-9a-f]{32}$` mismatch) → `AddinProtocolErrorMessage(ErrorCode = MalformedEnvelope)`.
- **`CallId` fails UUIDv7 format validation** → `AddinProtocolErrorMessage(ErrorCode = MalformedEnvelope)`.
- **Well-formed envelope with unknown `type` discriminator** (e.g., orchestrator running a newer protocol version sent a message type this add-in build doesn't know about) → `AddinProtocolErrorMessage(ErrorCode = UnknownMessageType)`. The `type` value is extracted from the envelope and included in the log entry; `RejectedMessageId` is echoed from the original.
- **Stale-epoch messages** (`AddinSessionId` differs from the current epoch) → `AddinProtocolErrorMessage(ErrorCode = StaleEpoch)`.

**Why the split matters for the add-in receive side:** `MalformedEnvelope` from the orchestrator is a bug signal — the orchestrator is sending corrupt data to this add-in. `UnknownMessageType` is a version-skew signal — the orchestrator is using protocol vocabulary this add-in doesn't understand. Repeated `MalformedEnvelope` arrivals within a short window MAY trigger add-in-initiated close with code 1002 (protocol error) as a DOS mitigation; repeated `UnknownMessageType` arrivals SHOULD be logged at `LogLevel.Warning` and tolerated — the orchestrator is attempting legitimate communication, just in a vocabulary this add-in build doesn't know. The add-in does NOT close the connection on `UnknownMessageType` alone.

**Handshake gate:** `MessageDispatcher` maintains a `HandshakeComplete` flag, initially false for each connection. The flag is set to true after the `AddinReadyMessage` and the initial `StateUpdateMessage` have been sent (Section 6.4 steps 4 and 6). Any `IClientMessage` received while `HandshakeComplete` is false is rejected with `AddinProtocolErrorMessage(ErrorCode = MalformedEnvelope)` (the envelope is valid but the timing is not — a finer-grained error code is unnecessary since the orchestrator controls the send timing and this case indicates either orchestrator bug or a timing race during reconnect). The message is not dispatched. This prevents pipelined tool calls from mutating the model before the baseline `StateVersion` exists.

**CallId format validation:** Before dispatching any `ToolCallMessage`, `MessageDispatcher` validates that `CallId` conforms to the UUIDv7 format (lowercase hexadecimal with hyphens, matching the regex `^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`). Non-conforming `CallId` values are rejected with `AddinProtocolErrorMessage(ErrorCode = MalformedEnvelope)` and are not entered into the `ToolCallRegistry`. This prevents malformed `CallId` values from polluting logs or registry keys. The format contract is owned by SPEC-01 §5 as of v1.4.0.

Every tool call runs through `ToolDispatcher` → `ISwCommandQueue.ExecuteAsync` → the correct service implementation → result → `ToolResultMessage` outbound.

### 6.6 ToolCallRegistry (In-Flight Dedup)

A per-session `Dictionary<string, ToolCallTracker>` keyed by `ToolCallMessage.CallId`, scoped to the current `AddinSessionId`. The registry **survives WebSocket reconnects** within the same session and is cleared only on session reset (new `AddinSessionId`).

The registry is **in-memory only**. If the SolidWorks process crashes or is force-terminated, the registry is lost. On process restart, a new `AddinSessionId` is generated (Section 8.2), which the orchestrator detects as a new session. Crash recovery does NOT rely on registry replay — it relies solely on the new-session detection path. Implementers MUST NOT add persistence to the registry; the `AddinSessionId` boundary is the crash recovery mechanism.

Each `ToolCallTracker` owns a `CancellationTokenSource`. This token is passed into `ISwCommandQueue.ExecuteAsync` when the call is enqueued. On transport loss (Section 9.2), `DisconnectHandler` iterates all `Queued` entries and calls `Cancel()` on their `CancellationTokenSource`s. The `TaskScheduler`'s queued-cancel behavior (Section 2.4) transitions the corresponding Tasks to `Canceled` without executing the delegates.

Tracker holds one of five states: `Queued`, `Executing`, `Completed`, `Cancelled`, or `Abandoned`. Transitions:

- `Queued` → `Executing`: when the command begins running on the UI thread.
- `Executing` → `Completed`: when the command finishes. The `ToolResultMessage` is cached with a 60-second retention window.
- `Queued` → `Cancelled` (terminal): on queued-cancel or transport loss while queued. The tracker's `CancellationTokenSource` is cancelled.
- `Executing` → `Abandoned` (terminal): on transport loss while the command is executing on the UI thread (Section 9.2). The command runs to completion but the result is never delivered.

On incoming `ToolCallMessage`:

- If `CallId` is new: create a `ToolCallTracker` with a new `CancellationTokenSource`, enqueue, and track as `Queued`.
- If `CallId` is `Queued` or `Executing`: respond with `AddinProtocolErrorMessage(ErrorCode = InFlightDuplicate)`. The caller knows to wait for the original result.
- If `CallId` is `Completed` and within the 60-second window: replay the cached `ToolResultMessage`.
- If `CallId` is `Completed` but outside the window: respond with `AddinProtocolErrorMessage(ErrorCode = UnknownCallId)`.
- If `CallId` is `Cancelled` or `Abandoned`: respond with `AddinProtocolErrorMessage(ErrorCode = UnknownCallId)` so the client knows to use a new `CallId`.

## 7. Screenshot Producer

`ScreenshotProducer` is the add-in component responsible for turning SolidWorks viewport captures into `ScreenshotResult` payloads. It replaces the filesystem-path-based approach from v1.2.0.

### 7.1 Produce Flow

1. Add-in invokes `ISolidWorksSelectionService.CaptureScreenshot(widthPx, heightPx)` which returns `(byte[] pngBytes, string sha256Hex, int actualWidth, int actualHeight)`.
2. `ScreenshotProducer` base64-encodes `pngBytes`.
3. If `encoded.Length > 2_097_152` (2 MiB), the producer progressively reduces target dimensions (halve `widthPx` and `heightPx`), re-captures, re-encodes, and repeats until fitting within 2 MiB. Maximum 4 reduction iterations; if still over 2 MiB after 4 iterations, the producer throws `ScreenshotTooLargeException` (mapped to `ToolErrorCode.ScreenshotCaptureFailed` via Section 4 per v1.4.0). This produces a structured `ToolResultMessage` with `Success = false` and `Error.Code = ScreenshotCaptureFailed`. This is a failure mode for pathologically complex viewports; the agent surfaces the error to the user deterministically.
4. Producer stages a copy of the PNG bytes to the local cache directory (§7.3) for audit purposes. **This is a write-only local cache; the path is never transmitted.**
5. Producer returns `ScreenshotResult` with:
   - `ScreenshotBase64` = the (possibly reduced) base64 string
   - `ScreenshotUrl` = `null` (v1)
   - `Sha256Hash` = hex SHA-256 of the raw PNG bytes (pre-encode)
   - `WidthPx`, `HeightPx` = actual captured dimensions (may be smaller than requested if reduction occurred)

### 7.2 Dimension Reduction Strategy and Terminal Behavior (v1.4.0)

- Input: requested `widthPx`, `heightPx` (from `ScreenshotInput`, defaults 1024×768).
- Iteration 0: requested dimensions.
- Iteration 1: `widthPx / 2`, `heightPx / 2` (e.g., 512×384).
- Iteration 2: `widthPx / 4`, `heightPx / 4` (e.g., 256×192).
- Iteration 3: `widthPx / 8`, `heightPx / 8` (e.g., 128×96).
- Iteration 4: `widthPx / 16`, `heightPx / 16` (e.g., 64×48).
- **Abandon after iteration 4.**

Each reduction halves pixel count by 4x, so most captures fit within 1-2 iterations. PNG compression makes most viewports well under 500 KiB at 1024×768; reduction only fires for unusually detailed scenes.

**Terminal behavior when all 4 iterations fail (v1.4.0):** `ScreenshotProducer` throws `ScreenshotTooLargeException` with `Context` populated:
```csharp
throw new ScreenshotTooLargeException
{
    IterationsAttempted = 4,
    FinalEncodedBytes = encoded.Length,
    FinalDimensions = ($"{currentWidth}x{currentHeight}"),
    OriginalRequestDimensions = ($"{originalWidth}x{originalHeight}")
};
```

The exception is caught by `ToolDispatcher` and mapped to `ToolErrorCode.ScreenshotCaptureFailed` per Section 4. The add-in returns a normal `ToolResultMessage`:
```json
{
  "type": "tool_result",
  "messageId": "...",
  "addinSessionId": "...",
  "callId": "...",
  "undoGroupId": "...",
  "success": false,
  "result": null,
  "error": {
    "code": "ScreenshotCaptureFailed",
    "message": "Screenshot could not be reduced below 2 MiB after 4 iterations (final: 64x48, 2100KiB).",
    "recoverable": true,
    "suggestedAction": "Simplify the current view or close unused bodies before retrying.",
    "context": {
      "iterationsAttempted": "4",
      "finalEncodedBytes": "2150231",
      "finalDimensions": "64x48",
      "originalRequestDimensions": "1024x768"
    }
  },
  "stateAfter": { "..." },
  "timing": { "..." }
}
```

The orchestrator's lifecycle layer treats this as a normal `Completed` terminal state with `Success = false` — no special exception path, no retry (the error is deterministic given the current viewport), no quarantine. The agent surfaces the failure to the user with the `suggestedAction` text.

**Recoverable flag:** `ScreenshotCaptureFailed` sets `Recoverable = true`. The failure is user-driven (the user can change the viewport or simplify the model), not system-driven. The agent MAY retry after the user acts, but MUST NOT retry automatically — the failure is deterministic across retries with the same viewport.

### 7.3 Local Cache Directory (Audit / v2 Forward-Compat)

The add-in stages screenshot PNGs to `%LOCALAPPDATA%\BSVoiceCAD\screenshots\{addinSessionId}\{utcTicks}_{callId}.png` before returning them to the orchestrator. This local cache serves two purposes:

1. **Audit trail on the add-in side:** operator can inspect what was captured and sent if a user reports an anomaly.
2. **v2 forward-compat:** when screenshot transport migrates to S3 presigned URLs (migration doc §3), the add-in will upload from this local cache rather than from memory. Retaining the local-staging discipline now means v2 doesn't require rewriting the capture flow.

The local cache directory is created with `Directory.CreateDirectory` (idempotent). Files older than 7 days are cleaned up by the `cleanup-screenshots.ps1` maintenance script in `tools/`, run independently of the orchestrator process. Not run during demos.

The orchestrator has NO VISIBILITY into this directory. It lives on the add-in's workstation and is not transmitted. The `AddinReadyMessage.ScreenshotRootPath` field (SPEC-01 §5) carries the root path for audit-trail completeness; the orchestrator records it in DHF session metadata but never attempts to access it.

### 7.4 Hash Integrity

SHA-256 is computed on the raw PNG bytes (pre-encode). The orchestrator re-decodes `ScreenshotBase64` and re-hashes the decoded bytes on receipt, comparing to `Sha256Hash`. Mismatch means the base64 payload was corrupted in transit or the hash was computed incorrectly — logged as a warning, the screenshot is omitted from context, and the event is written to the DHF decoration table as an `integrity_event` (see SPEC-04 §8.2). The mandatory 2 MiB cap ensures this decode + re-hash is cheap.

### 7.5 Full Message-Size Check (v1.4.0)

The 2 MiB screenshot cap is necessary but not sufficient. The enclosing `ToolResultMessage` carries: the `ScreenshotResult` (≤ 2 MiB base64 post-v1.4.0 cap), the `StateAfter.FeatureTree` (potentially large for complex models), the `TimingReport`, the `CallId`, `UndoGroupId`, and envelope fields. On unusual workflows (e.g., screenshot taken after a large assembly was opened), the combined payload can exceed the 4 MiB wire-message cap defined in SPEC-01 §3.

**After `ScreenshotProducer` returns a sized-down `ScreenshotResult`, the add-in assembles the full `ToolResultMessage` payload, serializes it to JSON, and measures the UTF-8 byte length.** If the length > 4 MiB, the add-in:

1. Applies `StateSnapshot` truncation per SPEC-01 §8: truncate `FeatureTree` to top-level features only and set `IsFeatureTreeTruncated = true` (`FeatureTreeHash` remains the hash of the untruncated tree). Re-serialize. Re-measure.
2. If still > 4 MiB after truncation, the add-in MUST throw `ToolResultOversizeException` which is mapped to `ToolErrorCode.ScreenshotCaptureFailed` per Section 4. The resulting `ToolResultMessage` substitutes a placeholder `StateAfter` (feature tree empty, `IsFeatureTreeTruncated = true`, `FeatureTreeHash = "OVERSIZE"`) and the error payload describes the size overflow. This preserves the wire-size invariant at the cost of the screenshot and some state detail; the orchestrator can request `get_feature_tree` separately to retrieve the truncated content.

**Rationale:** The alternative — dropping the screenshot and sending the full state — would violate the `screenshot` tool's contract (the tool is called BECAUSE a screenshot is wanted). Dropping state is worse because the next turn's agent loop relies on `StateAfter` for grounding. Both cases are bad; returning a structured failure lets the agent and the user decide how to proceed (e.g., "close some bodies and retry").

## 8. AddinSessionId

**This spec is authoritative for the derivation algorithm.** SPEC-01 §9 owns the format contract (`^[0-9a-f]{32}$`) and epoch rotation semantics.

### 8.1 Generation Algorithm

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

32-char lowercase hex. Output satisfies SPEC-01 §9 format contract. The `Guid.NewGuid()` nonce guarantees uniqueness even under rapid reloads within the same tick resolution or clock rollback. The other inputs (machineId, processId, timestamp) provide traceability but are not relied upon for uniqueness. Not cryptographic; collision-resistance only.

**Machine-id portability (editorial M1).** The `ReadMachineId` fallback persists a GUID under `%LOCALAPPDATA%`, which is user-and-workstation-scoped. If `%LOCALAPPDATA%` is migrated across workstations via backup/restore, the same machine-id will reappear on a different host — it degrades from a uniqueness input to a correlation input. `AddinSessionId` uniqueness is preserved by the nonce (`Guid.NewGuid()`) and the load timestamp, so this portability quirk does NOT produce ID collisions. SPEC-04's cross-workstation conflict detection uses `Environment.MachineName` (recorded in the `workstation_id` column), NOT this fallback id.

**Optional defensive check (editorial m1, reciprocal to SPEC-01 v1.4.0+ m2).** As belt-and-suspenders: if the 16-byte SHA-256 truncation yields all-zeros (`2^-128` probability), regenerate with a fresh nonce before returning. This guarantees production output never equals the SPEC-01 reserved sentinel (`00000000000000000000000000000000`), even accidentally. The check is optional because the collision probability is statistically negligible, but low-cost to include.

```csharp
// Optional defensive check — costs one comparison per Generate() call.
var idHex = BitConverter.ToString(hash, 0, 16).Replace("-", "").ToLowerInvariant();
if (idHex == "00000000000000000000000000000000")
{
    // Vanishingly unlikely; regenerate with fresh nonce.
    return Generate();
}
return idHex;
```

### 8.2 Lifecycle

Generated once per call to `BSVoiceAddin.ConnectToSW`. Stable for the duration of the add-in's connection to SolidWorks. Never regenerated mid-session. A new `AddinSessionId` is generated on:

- SolidWorks process restart (new process, new `ConnectToSW` call).
- Add-in reload within the same SolidWorks process (e.g., via the SolidWorks Add-in Manager — this triggers `DisconnectFromSW` followed by `ConnectToSW`, producing a new session).

Both cases constitute a **session boundary**: the `ToolCallRegistry` is discarded, `StateVersionCounter` resets to 0, and the orchestrator detects the new `AddinSessionId` on the next `AddinReadyMessage` and rotates its epoch per SPEC-01 §9.

**Network loss does NOT trigger a new AddinSessionId.** If the WebSocket drops due to network issues, the add-in reconnects with the SAME `AddinSessionId` (see §9.2), allowing the orchestrator to resume the existing DHF session per SPEC-04 SDR-04-005.

### 8.3 StateVersion Counter

`StateVersionCounter` is an instance per `BSVoiceAddin` (reset on every `ConnectToSW`). Starts at 0. Increments monotonically on every `StateUpdateMessage` and `ToolResultMessage.StateAfter`. Never decreases, never wraps. Does not persist across add-in loads (the counter's meaning is scoped to one `AddinSessionId`).

**Ordering guarantee:** `StateVersionCounter` is accessed exclusively from the SolidWorks UI thread. Tool-result state capture runs on the UI thread via `ISwCommandQueue`. Event-triggered state capture runs on the UI thread because SolidWorks fires COM events on its UI thread, and `EventRelay` dispatches capture through `ISwCommandQueue.ExecuteAsync` (which executes inline per Section 2.3 when called from the UI thread). This single-threaded access guarantees a total order on version numbers without explicit synchronization.

**Coalescing rule:** When `ToolInProgress` is true (Section 2.3), `EventRelay` suppresses event-driven state capture. The tool result's `StateAfter` snapshot is the single source of truth for state changes caused by that tool. This prevents duplicate or interleaved version numbers from re-entrant COM events during tool execution.

## 9. AddinTeardownMessage, Disconnect, and Reconnect

### 9.0 AddinTeardownMessage Send Conditions (v1.4.0 — NEW, SDR-TL-014)

`AddinTeardownMessage` is defined in SPEC-01 §4.2 / §5 as the add-in's graceful-shutdown signal to the orchestrator. Its purpose is to let the orchestrator close the DHF session with reason `explicit_shutdown` distinguishably from transport-loss paths that close with `heartbeat_timeout`. This section specifies exactly when the add-in sends this message and what the send discipline is.

#### 9.0.1 Send Trigger Matrix

| Trigger | Reason Value | Delivery Discipline | Occurs On |
|---------|-------------|---------------------|-----------|
| User clicks "Disconnect" in SolidWorks Add-in Manager | `TeardownReason.UserInitiated` | Best-effort, 2-second grace | SW UI thread (COM callback) |
| SolidWorks signals clean add-in unload via `ISwAddin.DisconnectFromSW` | `TeardownReason.SolidWorksShutdown` | Best-effort, 2-second grace | SW UI thread (COM callback) |
| Add-in is disabled while running (rare; SW Add-in Manager path) | `TeardownReason.AddinDisabled` | Best-effort, 2-second grace | SW UI thread |
| Host process graceful exit (dev harness ctrl-C, service stop) | `TeardownReason.HostProcessExit` | Best-effort, 2-second grace | Various; see §9.0.3 |
| SolidWorks crash / force-kill of SW or add-in process | — | **NOT SENT** (no opportunity) | N/A — process is dead |
| Workstation OS shutdown (rude) | — | **NOT SENT** | N/A |
| Network loss or WebSocket drop | — | **NOT SENT** (the send path is the WebSocket; if WS is broken, teardown cannot be delivered) | N/A |
| Heartbeat timeout (orchestrator-initiated) | — | **NOT SENT** (the orchestrator is the one detecting the timeout; there's nothing for the add-in to send teardown about, and if the WS is still open on the add-in side, the add-in reconnects per §9.2) | N/A |

`TeardownReason` is an enum in SPEC-01 §5. The add-in selects the reason based on which code path invoked the shutdown sequence.

**v1 emission scope (editorial M3).** `TeardownReason.UserInitiated` is reserved for the post-v1 taskpane UI path and is not emitted by any v1 code path. v1 emits three of the four enum values: `SolidWorksShutdown`, `AddinDisabled`, and `ProcessExit` (emitted in v1 as `HostProcessExit` per §9.0.3 mapping). The send-trigger matrix row labeled `UserInitiated` describes the intended future path and is retained here so the enum contract is complete.

#### 9.0.2 Send Mechanics

`TeardownMessageSender` in `Bridge/` owns the send path. Pseudocode:

```csharp
public async Task SendTeardownAsync(TeardownReason reason, string? diagnostic = null)
{
    // 1. Verify connection is still healthy for a send.
    if (_webSocket.State != WebSocketState.Open)
    {
        _log.Warning("AddinTeardownMessage not sent; WS state is {State}", _webSocket.State);
        return;   // no-op; orchestrator will detect via heartbeat_timeout
    }

    // 2. Construct the message.
    var msg = new AddinTeardownMessage
    {
        MessageId = Guid.CreateVersion7().ToString(),  // UUIDv7
        AddinSessionId = _currentEpoch,
        Reason = reason,
        DiagnosticMessage = diagnostic
    };

    // 3. Best-effort send with 2-second grace.
    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(2));
    try
    {
        var payload = JsonSerializer.SerializeToUtf8Bytes(msg, JsonOptions.Default);
        await _webSocket.SendAsync(
            new ArraySegment<byte>(payload),
            WebSocketMessageType.Text,
            endOfMessage: true,
            cts.Token);
        _log.Info("AddinTeardownMessage sent; reason={Reason}", reason);
    }
    catch (OperationCanceledException)
    {
        _log.Warning("AddinTeardownMessage send timed out after 2s; reason={Reason}", reason);
        // Orchestrator will detect via heartbeat_timeout; acceptable degradation
    }
    catch (Exception ex)
    {
        _log.Warning(ex, "AddinTeardownMessage send failed; reason={Reason}", reason);
        // Acceptable degradation — shutdown continues
    }

    // 4. Close WebSocket with code 1000 ("normal closure") immediately after send.
    //    Do NOT wait for any orchestrator reply (none is required per SPEC-01 §5).
    try
    {
        using var closeCts = new CancellationTokenSource(TimeSpan.FromSeconds(1));
        await _webSocket.CloseAsync(
            WebSocketCloseStatus.NormalClosure,
            "addin_teardown",
            closeCts.Token);
    }
    catch
    {
        // Ignore close errors. The WS may already be closed; Abort() as a final fallback.
        _webSocket.Abort();
    }
}
```

**Delivery discipline:**
- Best-effort. Failure to send is logged at `Warning` but does not block shutdown.
- 2-second grace timeout on the send itself. If the 2 seconds elapse without the frame being transmitted, the send is abandoned and the WebSocket is closed regardless.
- No retry. A single failed send is treated as terminal for this shutdown sequence.
- WebSocket close code 1000 ("normal closure") MUST follow the teardown send, regardless of whether the send succeeded. The add-in MUST NOT leave the WebSocket in `Open` state after `SendTeardownAsync` returns.
- No reply handshake. The orchestrator receives and processes `AddinTeardownMessage` without sending a reply per SPEC-01 §5; the add-in does not wait for one.

#### 9.0.3 Send Orchestration Per Trigger

**`ISwAddin.DisconnectFromSW` callback path (most common):**

```csharp
public int DisconnectFromSW()
{
    try
    {
        // Stop accepting new tool calls.
        _dispatcher.StopAcceptingMessages();

        // Drain any in-flight tool calls (or escalate after 60s per §9.1).
        _shutdownCoordinator.DrainInflight(timeoutSeconds: 60);

        // Send teardown message.
        _shutdownCoordinator.SendTeardown(TeardownReason.SolidWorksShutdown);

        // Stop queue, dispose services, etc.
        _queue.Stop();
        _disposer.DisposeAll();
    }
    catch (Exception ex)
    {
        _log.Error(ex, "DisconnectFromSW handler failed");
    }
    return 1;  // SW-required return value
}
```

The 60-second in-flight drain before teardown is essential: if the add-in sends teardown while a COM call is still executing on the UI thread, the teardown send will block behind the COM call on the shared message queue (both go through `SwUiThreadCommandQueue`), and the SW UI thread's normal shutdown sequence will be blocked. The drain gives the tool call time to complete; if it doesn't complete within 60 seconds, §9.1 escalation applies and teardown is NOT sent (the connection is force-closed instead).

**Host process exit (dev harness):** The add-in's dev harness host handles `Console.CancelKeyPress` and service-stop signals by calling `_shutdownCoordinator.Shutdown(TeardownReason.HostProcessExit)` before exiting. Same drain + send discipline.

**Add-in Manager "Disconnect" UI action:** SW invokes `DisconnectFromSW` (same path as SW shutdown). The add-in cannot distinguish user-initiated-via-Manager from SW-initiated-shutdown reliably from within the COM callback. The add-in conservatively uses `TeardownReason.SolidWorksShutdown` for both; for a user-initiated distinguishable path, the add-in's own taskpane UI (when implemented) can invoke a different entry point on `ShutdownCoordinator` that uses `TeardownReason.UserInitiated`.

**Add-in disabled while running:** SW invokes `DisconnectFromSW` with a separate SW-internal reason code that the COM API surface does not expose cleanly. Handled identically to `SolidWorksShutdown` for v1.

#### 9.0.4 Orchestrator-Side Expectations

The orchestrator receives `AddinTeardownMessage`, validates its format and epoch (per §6.5 MessageDispatcher), and passes it to SPEC-04's `DhfLedger.CloseSession` path with reason `explicit_shutdown`. The orchestrator MUST NOT require a follow-up close handshake — the add-in's close frame may race the orchestrator's response. The orchestrator considers the session cleanly closed upon receipt of the teardown message; any inbound messages arriving after teardown but before the add-in's close frame are logged at `Debug` and dropped without error.

If the WebSocket closes WITHOUT a preceding `AddinTeardownMessage`, the orchestrator records session closure as `heartbeat_timeout` with cause "unknown" per SPEC-00 §6 glossary. The 60-second heartbeat-absence threshold (SPEC-01 §12.7) applies before the orchestrator closes; receipt of teardown short-circuits the wait.

**Closure-reason observability boundary (editorial M2).** The `TeardownReason` enum values (`SolidWorksShutdown`, `AddinDisabled`, `ProcessExit`, `UserInitiated`) and the `heartbeat_timeout` / `unknown-cause` attribution are recorded in the orchestrator's structured logs (Serilog `session_closed` event) only — NOT in the DHF schema. Closure attribution is observability-grade, not audit-chain-grade; DHF ledger integrity does not depend on knowing *why* a session closed, only on preserving the entries up to the point of closure. Operators querying session closure reasons MUST use the Serilog pipeline (or the downstream observability store), not `dhf.sessions`.

### 9.1 Planned Disconnect with 60s Escalation (v1.4.0 — Specified Post-Timeout Action)

Planned disconnect means either side sends WebSocket Close frame with status 1000, or the add-in initiates teardown via §9.0.3. Add-in handling sequence:

1. Stop accepting new `IClientMessage`s (`MessageDispatcher.StopAcceptingMessages`).
2. Wait for any currently-executing tool call to complete on the UI thread (the drain). Poll `ToolCallRegistry` for `Executing` trackers; check every 500ms.
3. **Drain timeout: 60 seconds.** If the drain completes within 60 seconds, proceed to step 4. Otherwise, escalate per §9.1.1.
4. Send `AddinTeardownMessage` via `TeardownMessageSender.SendTeardownAsync` per §9.0.2.
5. Close WebSocket with code 1000.
6. Rollback any still-open undo group (defensive — should have been committed or rolled back by step 2's drain).
7. Do NOT clear `ToolCallRegistry` (session-scoped; preserved for any future reconnect).
8. If SW is still running and the trigger was transient (e.g., idle timeout from orchestrator): attempt reconnect after 1-second delay (§9.2). Otherwise, transition to `Disconnected` terminal state.

#### 9.1.1 60s Drain-Escalation Action (v1.4.0)

When the 60-second drain timeout expires with a COM call still executing on the UI thread:

1. **Log the stuck call at `LogLevel.Error`** with `CallId`, `ToolName`, time elapsed since `Executing` state transition, and the current SW message-pump thread ID. This is a post-mortem signal for diagnosing SW hangs.
2. **Mark the stuck call's tracker as `Abandoned`** in `ToolCallRegistry`. The COM call continues running on the UI thread (non-preemptible); its eventual completion will be ignored.
3. **Do NOT attempt to send `AddinTeardownMessage`.** The send path goes through `SwUiThreadCommandQueue` and would block behind the stuck COM call. Sending teardown is abandoned; the orchestrator will detect the closure as `heartbeat_timeout` rather than `explicit_shutdown`. This is the expected degraded attribution.
4. **Do NOT attempt `RollbackUndoGroup`.** Undo-group rollback also runs through `SwUiThreadCommandQueue`; it would also block. The in-progress COM call has already mutated the model; rolling back after the fact would require the UI thread to free up, which is precisely what is not happening. Document the abandoned state for the next reconnect's reconciliation flow (SPEC-00 Seam C post-timeout recovery).
5. **Force-close the WebSocket via `WebSocket.Abort()`** (TCP abort, no Close frame — matches the heartbeat-timeout escalation path in SPEC-01 §12.7). The orchestrator's WebSocket read loop observes this as a connection-closed-prematurely error, which it correlates to the absent heartbeat and closes the DHF session with reason `heartbeat_timeout` and cause "unknown."
6. **Dispose local resources that do not require the UI thread** (WebSocket, Serilog sinks, file handles). The `SwUiThreadCommandQueue` is abandoned (its dispose would block on the stuck task); SW's own shutdown will eventually tear it down when the stuck COM call completes and SW's message pump exits.
7. **Exit the add-in process** after a final 5-second grace (allows the stuck COM call a last chance to return and produce a log line). If the process has not exited after the grace, the host invokes `Environment.Exit(1)`. **This IS a last-resort termination with significant user-facing impact** (R4 Oracle C4 — sharpened disclosure):

   - `Environment.Exit(1)` terminates the SolidWorks **process**, not just the add-in, because the add-in runs in-process with SolidWorks.
   - Any unsaved work in **other** SolidWorks documents in the same process instance will be lost.
   - The user does **NOT** see SolidWorks' standard save-prompt dialog — SW is terminated before it can prompt.
   - The stuck COM call is the reason a less-destructive unload path is not available in v1.

   **USER-FACING WARNING POLICY (mandatory in v1).** Before the 5-second grace elapses, the add-in MUST display a modal dialog on a non-UI thread (WPF dispatcher on a separate `Thread` with its own message pump) if the UI thread is too stuck to render one:

   > The BSVoice-CAD add-in is not responding and must be terminated.
   > SolidWorks will quit within 5 seconds.
   > Any unsaved work in OTHER SolidWorks documents will be lost.
   > Save your other documents NOW if you can.

   The dialog uses a non-UI-thread `MessageBox` (`System.Windows.Forms.MessageBox.Show` from a worker thread with `MessageBoxOptions.ServiceNotification` on .NET Framework 4.8) so it renders even when the SW UI thread is hung. If the dialog subsystem itself is too degraded to render, the add-in logs the event at `LogLevel.Error` with the stuck-call diagnostic and proceeds with `Environment.Exit(1)` after the grace.

   **V1.5 BACKLOG ITEM: External add-in watchdog process.** R4 Oracle C4 correctly identified that a dedicated Windows service narrowing termination scope to the add-in's `AppDomain` (via SW COM automation such as `IEdmVault5` or `ISldWorks.ExitApp`) is strictly better architecture than host-process termination. It is deferred to v1.5 for the following scope reasons:

   - Separate Windows service (install/uninstall path, service registration, account permissions).
   - IPC channel from add-in health monitor to watchdog (named pipe or local socket).
   - SW COM automation flow for targeted add-in unload without host termination.
   - Installer complications (privileged service install, uninstall cleanup, service-account lifecycle).

   Tracked: **v1.5 BOARD ITEM "External add-in watchdog (C4 from R4 Oracle cross-pass)"**. When v1.5 ships, this step is replaced with "signal external watchdog; watchdog unloads add-in AppDomain via SW COM; SolidWorks process and other documents remain intact."

   This is acknowledged degradation, not defect. v1 ships with the warning policy above; v1.5 replaces this step with the watchdog architecture.

DHF session outcome: `heartbeat_timeout` with the stuck-call diagnostic in the orchestrator's logs. On the next session start (new `AddinSessionId` from a restarted SW), SPEC-03's reconciliation flow detects the model state and records a recovery event per SDR-TL-013.

**Why not send teardown first and rollback first before escalating?** Both operations require the UI thread. The UI thread is stuck. The entire point of the escalation is recognizing that the UI thread is not responding. Trying to use it one more time would just add another 60 seconds of waiting before force-close. The escalation path accepts degraded closure attribution (`heartbeat_timeout` instead of `explicit_shutdown`) as the cost of bounded shutdown time.

**Why 60 seconds and not 30 or 120?** The longest SPEC-01 §13 per-tool timeout is 180 seconds (linear/circular pattern). Granting the drain the full 180 seconds would make shutdowns hang for 3 minutes when users click Disconnect during a pattern operation. 60 seconds covers `extrude`, `fillet`, `chamfer`, `hole_wizard`, and `modify_feature_parameter` (all ≤ 120s) with headroom. Pattern tools exceeding 60s get the escalation path; this is acceptable because pattern operations are stretch tools and rarely trigger user-initiated disconnect during their execution.

### 9.2 Unplanned Disconnect (Transport Loss)

`ClientWebSocket` raises `WebSocketException` on read or write, or the close handshake is violated, or `ConnectAsync` fails. Causes include network loss (Wi-Fi drop, VPN reconnect), orchestrator restart, Railway edge outage. Add-in handling:

1. Classifies all tracked calls by their current state:
   - `Queued` calls → marked `Cancelled`. `DisconnectHandler` calls `Cancel()` on each tracker's `CancellationTokenSource` (see Section 6.6). The `TaskScheduler`'s queued-cancel behavior (Section 2.4) transitions their Tasks to `Canceled` without executing the delegates.
   - `Executing` calls → marked `Abandoned`. The command continues running on the UI thread to completion (COM calls are non-preemptible), but its result is cached in the registry without delivery. The model may have been mutated.
   - `Completed` calls → unchanged.
2. Attempts `RollbackUndoGroup` on any open undo group, local best-effort (see disclaimer below).
3. Does NOT attempt to send `AddinTeardownMessage`. The send path requires the WebSocket, which is exactly what just broke. Orchestrator detects closure as `heartbeat_timeout` per SPEC-00 glossary.
4. Does NOT clear the registry.
5. Enters reconnect loop with exponential backoff: 1s, 2s, 5s, 10s, 30s, 60s, 120s, 120s... (capped at 120s). Each attempt invokes `ReconnectHandshake.TryConnectAsync()`.
6. On successful reconnect, re-runs the handshake sequence (§6.4) with the SAME `AddinSessionId` as before the disconnect. This signals to the orchestrator that this is a resume, not a new session (SPEC-04 SDR-04-005).

**Disclaimer on local rollback after transport loss:** If the add-in is hung or crashed (the likely cause of unplanned disconnect from the add-in side), the rollback attempt may not execute. In that case the SolidWorks model may be left in a partial state. The orchestrator records the outcome as `Abandoned` on reconnect when it observes that the prior undo group has no matching `UndoGroupOutcomeMessage`. The viewer shows this as an ambiguous termination.

### 9.3 Reconnect Handshake

Every new connection begins with the handshake in Section 6.4. If the add-in has a still-open undo group from a prior connection that didn't clean up (detected via `GetActiveUndoGroupId() != null`), the add-in rolls it back before sending `AddinReadyMessage`. This is a defensive measure; normal flow never leaves undo groups open across connections.

On reconnect within the same `AddinSessionId`, the `ToolCallRegistry` is available. The orchestrator can detect `Abandoned` or `Cancelled` calls via `UnknownCallId` responses and issue new `CallId`s. The orchestrator can also detect abandoned mutations by comparing the pre-disconnect and post-reconnect state versions.

**Reconnect with differing capability fields:** If the add-in reconnects with the same `AddinSessionId` but its `AddinVersion`, `SwVersion`, `SwBuild`, or `ScreenshotRootPath` differs from what the DHF session has recorded, the orchestrator rejects the reconnect per SPEC-01 §9.1 Case B with `ClientProtocolErrorMessage(StaleEpoch)` and close code 1008 ("policy violation") with reason text containing `"capability_drift"`. The add-in treats this as a session boundary: discards the current `AddinSessionId`, generates a new one via §8, and reconnects as a fresh session.

This case should not occur in normal operation (a legitimate capability change means SW was updated, which restarts SW and thus triggers a new process and new `AddinSessionId` on the next ConnectToSW). Its handling exists as a defensive rejection of illegal states.

### 9.4 User-Visible Network Status

The add-in surfaces connection status via a SolidWorks taskpane UI element (if present) or via the add-in's logs (`FileLogger`). States:

- `Connecting` — initial connect attempt or reconnect attempt in progress.
- `Connected` — handshake complete, ready for tool calls.
- `Reconnecting` — transport loss detected, backoff retry active. Shows next retry time.
- `AuthFailed` — API key rejected (HTTP 401 at handshake, or close code 4401 post-upgrade). User must fix Credential Manager entry and restart SW.
- `StartupFailed` — SynchronizationContext probe failed (§2.2). User must restart SW.
- `Disconnected` — add-in teardown sequence completed; no reconnect will be attempted.

The backoff loop runs until either (a) reconnect succeeds, (b) user closes SolidWorks, or (c) `AuthFailed` or `StartupFailed` is reached (terminal states).

## 10. Required Tests (Build-Breaking)

All in `BSVoiceCAD.Addin.Tests`, NetFx 4.8. All run on Windows-only CI because of the NetFx target; that's explicitly documented in SPEC-00 SDR-TL-005.

- `StaCommandQueueTests` — see Section 2.6 (includes startup probe timeout test)
- `ReentrancyDeadlockTests` — nested `ExecuteAsync` from within a running command completes without deadlock (Section 2.3)
- `ProtocolDeserializerSyncTests` — see Section 5.3 (includes `AddinTeardownMessage` discriminator case as of v1.4.0)
- `ProtocolDeserializerRoundTripTests` — see Section 5.3
- `ToolDispatcherTests` — exception-to-ToolError mapping for each row of the table in Section 4, using a mock `ISolidWorksGateway`. Includes: assert `ToolInProgress` is false after a handler throws an exception (verifies try/finally cleanup per Section 2.3); assert `InvalidUndoStateException` maps to `InvalidUndoState`; assert `TemplateNotFoundException` maps to `TemplateNotFound`; **assert `ScreenshotTooLargeException` maps to `ScreenshotCaptureFailed` (v1.4.0)**; **assert `COMException` raised from `CaptureScreenshot` maps to `ScreenshotCaptureFailed` when other precondition errors do not apply (v1.4.0)**
- `InFlightDeduplicationTests` — the five registry states (`Queued`, `Executing`, `Completed`, `Cancelled`, `Abandoned`) and all transitions from Section 6.6; verifies CancellationTokenSource ownership and cancel behavior
- `DisconnectRollbackTests` — simulated transport loss mid-undo-group, assert best-effort rollback executes
- `DisconnectExecutingCommandTests` — verify that a command in `Executing` state at disconnect time is marked `Abandoned`, continues to completion, and its result is cached
- `ProtocolVersionNegotiationTests` — handshake pass and fail paths
- `AddinSessionIdGenerationTests` — stability within one process, difference across process restarts, registry fallback path, consecutive `Generate()` calls always produce different IDs (verifies Guid nonce entropy), output matches SPEC-01 §9 format regex
- `ScreenshotBase64ProducerTests` — capture produces valid base64 under 2 MiB; dimension reduction fires when requested size would exceed 2 MiB; SHA-256 is computed on raw PNG bytes not base64; local cache directory is populated but path is not transmitted; `ScreenshotResult.ScreenshotBase64` is non-null and `ScreenshotUrl` is null (v1 invariant)
- **`ScreenshotCaptureFailedOnAbandonTests` (v1.4.0):** after 4 reduction iterations with no fit, `ScreenshotTooLargeException` is thrown; it is caught by `ToolDispatcher` and produces a well-formed `ToolResultMessage` with `Success = false` and `Error.Code = ScreenshotCaptureFailed`; `Error.Context` contains `iterationsAttempted = 4` and `finalEncodedBytes` entries; `Error.Recoverable = true`; `Error.SuggestedAction` is non-empty; verify no exception propagates past `ToolDispatcher`
- **`ToolResultOversizeTests` (v1.4.0):** construct a scenario where the assembled `ToolResultMessage` exceeds 4 MiB even with a 2 MiB screenshot; assert that `FeatureTree` is truncated per SPEC-01 §8 (first attempt) and that `IsFeatureTreeTruncated = true`; if truncation is insufficient, assert `ToolResultOversizeException` fires and maps to `ScreenshotCaptureFailed`; assert the final emitted `ToolResultMessage` is ≤ 4 MiB on the wire
- `UndoNestingRejectionTests` — assert that `StartUndoGroup` while a group is active throws `InvalidUndoStateException`
- `CallIdFormatValidationTests` — assert that non-UUIDv7 CallIds are rejected with `MalformedEnvelope` and never enter the registry; references `CallIdFormatValidator` from SPEC-01 §15 (v1.4.0)
- `HandshakeGateTests` — assert that `ToolCallMessage` received before `HandshakeComplete` is rejected; assert that messages are dispatched normally after handshake completes
- `ModifyParameterScopeTests` — assert that `ModifyParameter` throws `ArgumentException` when `parameterName` does not belong to the feature identified by `featureId`
- `WebSocketClientReconnectTests` — simulated transport loss triggers exponential backoff (1s, 2s, 5s, ...); `AddinSessionId` is preserved across reconnect; `ToolCallRegistry` is preserved across reconnect; auth failure (HTTP 401) transitions to terminal `AuthFailed` state and does NOT retry; network failure (HTTP 502, connection refused, DNS failure) retries with backoff; backoff cap of 120s is respected after many failures; close code 4401 received on live connection transitions to `AuthFailed` without retry
- `ApiKeyCredentialStoreTests` — missing credential surfaces user-facing error; present credential returns correctly formatted `apikey-{keyId}.{secret}` subprotocol string
- **`AddinTeardownMessageSendConditionsTests` (v1.4.0):** for each trigger in the §9.0.1 matrix, assert the correct `TeardownReason` is emitted; assert the message includes the current `AddinSessionId`; assert `MessageId` is a well-formed UUIDv7; assert the WebSocket is closed with code 1000 after send; assert best-effort semantics — a failed send does NOT throw to the caller; assert 2-second grace timeout is honored when the orchestrator is unresponsive; assert teardown is NOT sent on transport loss, SW crash, or force-kill paths
- **`PlannedDisconnectEscalationTests` (v1.4.0):** configure a stuck executing call; trigger planned disconnect; assert 60-second drain elapses without completion; assert teardown is NOT sent; assert rollback is NOT attempted; assert WebSocket is force-closed via `Abort()` with no Close frame; assert process exits after 5-second post-force-close grace; assert stuck-call log entry contains `CallId`, `ToolName`, and elapsed time; assert DHF session closure reason is `heartbeat_timeout` (via mock orchestrator)
- **`UnknownMessageTypeVsMalformedEnvelopeReceiveTests` (v1.4.0):** seed representative incoming `IClientMessage` payloads per SPEC-01 §3.1 categories; assert unparseable bytes → `AddinProtocolErrorMessage(MalformedEnvelope)`; envelope-invariant violation → `MalformedEnvelope`; malformed `CallId` → `MalformedEnvelope`; well-formed envelope with unknown `type` → `UnknownMessageType`; `RejectedMessageId` is echoed for `UnknownMessageType` cases; repeated `UnknownMessageType` from orchestrator does NOT trigger connection close; repeated `MalformedEnvelope` (>5 in 10s) MAY trigger close with code 1002
- **`TeardownMessageSenderBestEffortTests` (v1.4.0):** mock WebSocket refuses writes; assert `SendTeardownAsync` does not throw to caller; assert the 2-second grace timeout causes log warning; assert WebSocket is still closed afterward; assert shutdown sequence continues past the failed send

Integration tests that require SolidWorks itself (`ToolRoundtripTests` et al.) live in `BSVoiceCAD.Tests.Integration` and are tagged `[Trait("Category", "RequiresSolidWorks")]` so CI can skip them on runners without SW installed.

## 11. Day-0 Validation

Before Day 1 implementation starts:

1. Visual Studio 2022 loads a hello-world NetFx 4.8 add-in with SW 2025 SP3+.
2. Sample add-in registers via COM, loads in SW, and a menu command executes a trivial `new_part` via `ISldWorks.NewDocument`. **Verify:** inspect `SynchronizationContext.Current` on the SW UI thread inside `ConnectToSW` and **document the actual type**. If SolidWorks already provides a context (likely), record this — the install-if-null path is dead code but retained as a fallback. If null, install `WindowsFormsSynchronizationContext`. In either case, confirm the startup probe round-trip completes within 5 seconds. If the probe fails, confirm the add-in refuses to start the WebSocket client and logs a fatal error.
3. `ClientWebSocket` connects to `wss://vcad.boundstate.ai/ws` with a valid test API key. **Verify:** connect succeeds; API-key subprotocol is accepted; echo round-trip works. Confirm TLS 1.2 negotiation. Test auth failure path by sending a wrong key.
4. `SHA256.Create()` / `ComputeHash` and in-memory base64 encoding measured under GE75's normal load; screenshot round-trip (capture → hash → encode → size check) completes < 500ms at 1024×768 for typical part files.
5. `ProtocolDeserializerSyncTests` and `ProtocolDeserializerRoundTripTests` pass on a stub Protocol project with 2 types.
6. **Template resolution:** confirm `ISldWorks.GetUserPreferenceStringValue(swDefaultTemplatePart)` returns a valid path on the demo machine. If templates are missing, install them. Verify unit-system switching for both MMGS and IPS.
7. **Machine ID:** confirm `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid` is readable from the add-in's process context; test the fallback path by temporarily restricting access.
8. **Screenshot local cache:** confirm the add-in can create `%LOCALAPPDATA%\BSVoiceCAD\screenshots\{addinSessionId}\` and write PNG files. Confirm the orchestrator never attempts to read from this path.
9. **Network resilience:** disable Wi-Fi mid-session, confirm add-in enters `Reconnecting` state with exponential backoff. Re-enable Wi-Fi, confirm reconnect completes with the same `AddinSessionId`, and orchestrator resumes the existing DHF session (per SPEC-04 SDR-04-005).
10. **API key provisioning:** confirm the add-in reads the key from Windows Credential Manager under target `BoundState.BSVoiceCAD.ApiKey`; confirm missing-credential path surfaces a user-facing error.
11. **Teardown message (v1.4.0, R5 H2-R updated):** trigger `DisconnectFromSW` via SW Add-in Manager; confirm `AddinTeardownMessage` reaches the orchestrator with `Reason = SolidWorksShutdown`. Validation checks: (a) `dhf.sessions.ended_at` is set on the affected session row, AND (b) the orchestrator's Serilog structured-log pipeline emits a `session_closed` event carrying fields `{session_id, closure_reason = "explicit_shutdown", tenant_id}` exactly once per closed session. **No DHF column check is performed** — closure reason is observability-grade and persisted to Serilog logs only, per SPEC-00 v1.5.1 Seam C and SPEC-02 §9.0.4. The DHF schema has never contained a `closure_reason` column. Re-run the scenario with a stuck COM call in progress; confirm the 60-second drain completes or escalates; when escalation fires, confirm the Serilog event shows `closure_reason = "heartbeat_timeout"` with `cause = "unknown"` (attribution degradation is the documented cost of a stuck UI thread — see §9.1.1 step 7).

Any failure halts Day 1 start and triggers the corresponding contingency.

## 12. Out of Scope

- Tool-call timeout mechanics (per-tool timeouts, cancel protocol) → SPEC-03. This adapter just exposes queued-cancel and executing-cancel semantics; the lifecycle layer decides when to issue cancel.
- DHF row construction → SPEC-04
- Agent loop, retry correlation, user confirmation → SPEC-05
- Viewer reads → SPEC-06
- Voice capture (Whisper) → SPEC-05 (audio-to-text is an orchestrator concern; the add-in doesn't see audio)
- API-key validation logic (server-side handshake processing) → orchestrator code per SPEC-01 §12; the add-in SENDS the handshake.
- OAuth device flow (v2 auth) → `DEPLOYMENT_V1_V2_MIGRATION.md` §2.
- S3 presigned URL upload for screenshots (v2 transport) → `DEPLOYMENT_V1_V2_MIGRATION.md` §3.
- `AddinTeardownMessage` orchestrator-side handling (DHF closure mechanics) → SPEC-04 v1.5.0. SPEC-02 only specifies the add-in's send conditions.

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*
