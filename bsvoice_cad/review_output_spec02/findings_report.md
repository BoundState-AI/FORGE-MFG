# ORACLE Review Report

**Document:** D:\BoundState\specs\bsvoice_cad\SPEC-02_SOLIDWORKS_ADAPTER.md
**Session:** 20260418-124630
**Rounds:** 2
**Converged:** No

## Metrics

- Accepted critical defects: 0
- Accepted high defects: 8
- Total accepted defects: 31
- Total rejected claims: 17
- Preference-only findings: 3
- Duplicate rate: 0.0%
- Rounds to stability: 2
- Converged: False

## Round 1

### [CRITICAL] STA queue dispatches to a private thread, not the SolidWorks-owned COM apartment (R1-ISSUE-001)
**Problem:** The spec correctly identifies that SolidWorks COM calls must occur on the SolidWorks UI thread/apartment, but then implements a separate queue-owned STA thread. A different STA thread is not the same COM apartment as the SolidWorks main thread. Cross-apartment COM access can still produce RPC_E_WRONG_THREAD or marshaling stalls even though the target thread is STA.
**Fix:** Dispatch onto the actual SolidWorks UI thread/apartment captured at add-in startup (e.g., via SolidWorks's own callback/timer mechanism), or explicitly document and validate with real integration tests that all SolidWorks COM object acquisition and use occurs exclusively on the queue-owned STA and that SolidWorks supports this model.
**Sources:** Fran, Codex

### [HIGH] STA worker thread has no Windows message pump, risking COM deadlocks (R1-ISSUE-002)
**Problem:** The spec describes the STA thread as pumping a BlockingCollection, which is not a COM/Win32 message pump. COM STA marshaling and event reentrancy require a GetMessage/DispatchMessage loop. Without it, COM callbacks and cross-apartment calls can deadlock.
**Fix:** Use the existing SolidWorks UI thread and its message loop, or specify and implement a real Windows message loop around queued execution and validate with COM integration tests.
**Sources:** Fran

### [HIGH] 5-second shutdown join guarantee is unenforceable given non-preemptible COM calls (R1-ISSUE-003)
**Problem:** Section 2.3 states COM calls cannot be interrupted and run to completion. Section 2.4 promises a 5-second grace timeout for Stop/join. Section 2.5 encodes this as a build-breaking test. If the executing COM call takes longer than 5 seconds or hangs, the test encodes a guarantee the implementation cannot satisfy.
**Fix:** Redefine shutdown semantics: cancel only queued (not-yet-started) work, mark an executing command as indeterminate if stop is requested, and specify the unload fallback when the active COM call does not return within the grace period. Remove the 5-second join assertion from the build-breaking test.
**Sources:** Fran

### [CRITICAL] SHA256.HashData is not available on .NET Framework 4.8 (R1-ISSUE-004)
**Problem:** SHA256.HashData is a .NET 5+ static convenience method and does not exist in .NET Framework 4.8. The spec uses it in Section 8.1 (AddinSessionIdGenerator) and references it in Section 11 Day-0 validation. The code will not compile on the stated target.
**Fix:** Replace all SHA256.HashData usages with the .NET Framework 4.8-compatible pattern: 'using var sha = SHA256.Create(); var hash = sha.ComputeHash(bytes);'
**Sources:** Fran, Codex

### [HIGH] Tool-call deduplication registry is cleared on disconnect, allowing CallId replay after reconnect (R1-ISSUE-005)
**Problem:** ToolCallRegistry is scoped per-connection and explicitly cleared on both planned and unplanned disconnect. A client that retries after transport loss sends the same CallId, which is treated as a new tool call and can re-execute mutating operations (sketch, extrude, etc.) against an already-modified model.
**Fix:** Scope deduplication to AddinSessionId rather than the socket connection, retaining InFlight/Completed records across reconnects until TTL expiry or explicit session reset. Alternatively, explicitly declare the protocol as at-least-once and require all tools to be idempotent.
**Sources:** Fran, Codex

### [CRITICAL] Forced client drop can race with non-preemptible executing COM work, producing inconsistent state (R1-ISSUE-006)
**Problem:** Section 6.1 allows dropping the current client when a new connection arrives. Section 9.1 waits up to 60 seconds then forces the drop. Section 2.3 states executing COM calls run to completion. A replacement client can connect and receive an initial state snapshot while the previous command is still mutating the model, causing the snapshot to be stale and dedup/rollback cleanup to race with ongoing mutations.
**Fix:** Do not accept a replacement client or clear session state while a command is still executing. Block reconnect until the COM call finishes, or require add-in/process restart when a command exceeds the disconnect grace period.
**Sources:** Codex

### [HIGH] InFlight calls marked Cancelled on disconnect even though executing commands may still complete and mutate the model (R1-ISSUE-007)
**Problem:** Section 2.3 states that once a command starts executing, it runs to completion and the result is returned normally. Section 9.2 marks all InFlight calls as Cancelled on unplanned disconnect. InFlight includes commands already executing, so labeling them Cancelled is incorrect — the model may have been mutated.
**Fix:** Split InFlight into at least two states: queued-not-started and executing. Mark queued-not-started as Cancelled; mark executing-on-disconnect as Abandoned or UnknownOutcome. Update reconnect and audit logic accordingly.
**Sources:** Fran

### [HIGH] Undo-group nesting is simultaneously permitted and prohibited (R1-ISSUE-008)
**Problem:** Section 3.6 states 'Multiple calls with different group IDs nest' but immediately adds 'tests restrict this to at most one active group at a time.' GetActiveUndoGroupId() returns a single string. Reconnect cleanup in Section 9.3 only checks for one active group. The spec does not define which rule governs implementation.
**Fix:** Choose one model. Either forbid nesting in the contract (reject StartUndoGroup when one is already active, enforce in tests) or define full nested-stack semantics including GetActiveUndoGroups, cleanup order, and reconnect unwinding.
**Sources:** Fran, Codex

### [HIGH] WebSocket fragmentation handling is specified incorrectly (R1-ISSUE-009)
**Problem:** Section 6.1 states 'System.Net.WebSockets reassembles fragmented frames before the application sees them.' This is false. System.Net.WebSockets does not automatically reassemble multi-frame messages; callers must loop on ReceiveAsync accumulating buffers until EndOfMessage is true.
**Fix:** Specify an explicit receive loop that buffers frames until EndOfMessage, enforces a maximum message size, and only then deserializes one JSON document.
**Sources:** Codex

### [HIGH] ArgumentException/ArgumentNullException from handler bugs mapped to ValidationFailed, misclassifying server defects as client errors (R1-ISSUE-010)
**Problem:** The COM exception mapping table maps ArgumentException/ArgumentNullException to ValidationFailed with the note 'Indicates handler bug.' This tells the client its request was invalid when the actual cause is a server-side defect, hiding bugs and potentially triggering wrong retry/recovery logic upstream.
**Fix:** Map only exceptions explicitly thrown by the validation layer to ValidationFailed. Treat unexpected ArgumentException/ArgumentNullException from handlers or services as InternalError unless the source is unambiguously user-input validation.
**Sources:** Fran

### [HIGH] ExecuteAsync reentrancy on the owned STA thread is unspecified, risking self-deadlock (R1-ISSUE-011)
**Problem:** If code already running on the STA thread calls ExecuteAsync and awaits the returned Task, the queue will deadlock (the task can never complete because the STA thread is blocked waiting for itself). The spec provides IsOnStaThread but explicitly forbids using it for flow control, leaving implementers with no safe reentrancy rule.
**Fix:** Define one explicit behavior: either execute inline when already on the owner thread (with a note about reentrancy implications), or reject nested ExecuteAsync calls with a deterministic exception and add a test for this case.
**Sources:** Codex

### [HIGH] State version ordering between SolidWorks events and tool-result snapshots is undefined (R1-ISSUE-012)
**Problem:** EventRelay.cs fires StateUpdateMessages from SolidWorks events. Tool calls produce StateAfter snapshots via the command queue. The spec never defines how these two sources of state updates are serialized relative to each other, creating the possibility of out-of-order or duplicate version numbers.
**Fix:** Route all event-originated state capture through the same command queue and a single version allocator. Define ordering and coalescing rules between event updates and ToolResultMessage.StateAfter.
**Sources:** Codex

### [HIGH] GetFeatureTree 'user-facing parameter' filtering criteria are undefined (R1-ISSUE-013)
**Problem:** The compressed=true filtering rule is described as 'named user-facing parameters only (excludes SolidWorks-internal bookkeeping parameters)' without defining what constitutes a user-facing vs. bookkeeping parameter. The agent relies on this output to resolve concrete parameter identifiers, making the contract untestable and likely to drift across SolidWorks versions.
**Fix:** Define exact inclusion/exclusion criteria (e.g., parameters whose names match a specific pattern, or a whitelist per feature type), including version-stability expectations. Alternatively, return the full parameter list and move filtering to a higher layer.
**Sources:** Fran

### [MEDIUM] Deserializer sync test checks source text presence, not runtime deserialization correctness (R1-ISSUE-014)
**Problem:** ProtocolDeserializerSyncTests asserts that a switch case exists in the source file, but does not verify discriminator values, target types, nested union routing, or actual deserialization success. The test can pass while the runtime deserializer routes a message to the wrong type or fails on real payloads.
**Fix:** Add runtime round-trip tests for every concrete polymorphic type using real serialized payloads, asserting successful deserialization to the expected concrete type. Keep the source-text check as a secondary guard, not the primary correctness signal.
**Sources:** Fran

### [HIGH] HttpListener requires HTTP.sys URL ACL registration not addressed in deployment prerequisites (R1-ISSUE-015)
**Problem:** HttpListener on Windows requires HTTP.sys URL ACL registration for non-admin processes. The spec defines no installer or provisioning step for this, meaning the add-in may silently fail to bind its WebSocket bridge on standard-user machines.
**Fix:** Add a deployment requirement that reserves the URL prefix during installation (netsh http add urlacl), or switch to a raw TcpListener-based WebSocket implementation that does not depend on HTTP.sys URL ACLs.
**Sources:** Codex

### [HIGH] NewPart template resolution contract is undefined, risking interactive dialogs or silent failures (R1-ISSUE-016)
**Problem:** The spec defines symbolic template names ('default_mm', 'default_inch') but not how they map to actual SolidWorks template file paths or user settings. On clean machines or misconfigured profiles, NewPart can fail or trigger interactive template dialogs that block the STA queue.
**Fix:** Define the exact source of the template path for each symbolic name (e.g., SolidWorks default template settings, a config file, or a bundled template), add startup validation for missing templates, and specify the ToolError returned when resolution fails.
**Sources:** Codex

### [HIGH] CloseDocument does not specify prompt suppression for dirty documents (R1-ISSUE-017)
**Problem:** CloseDocument says 'Does not save' but omits what happens when the target document is dirty and SolidWorks would normally show a save/discard prompt. A modal prompt on the SolidWorks UI blocks the STA queue and can hang the adapter indefinitely.
**Fix:** Specify silent-close behavior explicitly, including the COM flags or SolidWorks settings used to suppress prompts, and define the ToolError returned when a dirty document cannot be closed non-interactively.
**Sources:** Codex

### [HIGH] Screenshot sharing assumes co-located same-user filesystem context with no fallback (R1-ISSUE-018)
**Problem:** The screenshot mechanism assumes the add-in and orchestrator share the same %LOCALAPPDATA% path. This breaks under UAC elevation, containerized orchestrators, remote desktop sessions, or antivirus locking LocalAppData. The only failure handling is a logged warning and omission from context.
**Fix:** Require an explicit shared directory via config/environment variable with handshake confirmation. Add a memory-only or in-message transfer option for non-co-located processes.
**Sources:** Grok

### [MEDIUM] AddinSessionId generation depends on HKLM registry read with no fallback for restricted accounts (R1-ISSUE-019)
**Problem:** ReadMachineGuidFromRegistry reads HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid. This can fail under restricted user accounts, AppLocker policies, or non-Windows CI. No fallback or error behavior is specified, which would break the reconnect handshake and screenshot paths.
**Fix:** Document a fallback (e.g., random GUID persisted in a user-writable location) and test under a least-privilege account. Day-0 validation should include a restricted-account test.
**Sources:** Grok

### [MEDIUM] Hand-maintained deserializer creates ongoing sync fragility post-hackathon (R1-ISSUE-020)
**Problem:** The rationale for hand-maintaining GeneratedMessageDeserializer.cs is explicitly scoped to hackathon conditions ('protocol changes are rare during the hackathon'). The spec does not address post-hackathon evolution, where the reflection-based sync test becomes a recurring source of build breaks.
**Fix:** Accept the current approach as a time-bounded decision but add a TODO/ADR noting that a source generator or shared serializer assembly should replace this before the protocol stabilizes for production. The alternative of System.Text.Json 8+ via a NuGet shim for NetFx should be evaluated.
**Sources:** Grok

### [MEDIUM] Scope of spec exceeds stated boundaries, creating a god-spec (R1-ISSUE-021)
**Problem:** Section 0 claims narrow focus on 'STA command queue, six service contracts, COM exception mapping, screenshot directory convention, disconnect recovery.' The actual document specifies full add-in architecture including tool dispatch, state capture, event relay, session management, undo, and bridge protocol — concerns that belong to SPEC-03/04/05.
**Fix:** Either update the scope statement to accurately reflect what is specified, or split the document: keep adapter plumbing (queue, services, COM mapping) here and move tool dispatch, state, lifecycle, and bridge details to their consuming specs.
**Sources:** Grok

### Coordinator Decisions
- **R1-ISSUE-001**: accepted_defect — Factually correct. A private STA thread is a different COM apartment from the SolidWorks UI thread. Cross-apartment marshaling is unreliable and defeats the purpose of the queue. Section 2 rewritten t
- **R1-ISSUE-002**: accepted_defect — Resolved jointly with ISSUE-001. Dispatching to the actual SolidWorks UI thread leverages its existing Windows message pump. No separate pump needed. BlockingCollection eliminated.
- **R1-ISSUE-003**: accepted_defect — Valid contradiction. COM calls are non-preemptible yet the spec promised a 5-second join. Revised: the queue no longer owns a thread, so Stop is non-blocking. The 5-second join test is removed. Execut
- **R1-ISSUE-004**: accepted_defect — SHA256.HashData is .NET 5+ only. The target is .NET Framework 4.8. Replaced with SHA256.Create()/ComputeHash() in Section 8.1 and Section 11.
- **R1-ISSUE-005**: accepted_defect — Valid operational risk. Clearing dedup on disconnect allows replayed CallIds to re-execute mutations. Registry now scoped to AddinSessionId and survives reconnects. Cleared only on session reset.
- **R1-ISSUE-006**: accepted_defect — Valid race condition. Revised Section 6.1: new connections are held in pending state until any executing command completes. The add-in never accepts a replacement client while a COM call is mid-execut
- **R1-ISSUE-007**: accepted_defect — Valid contradiction between non-preemptible execution and blanket Cancelled labeling. InFlight split into Queued/Executing states. Queued→Cancelled on disconnect; Executing→Abandoned. Section 6.5 and 
- **R1-ISSUE-008**: accepted_defect — Clear contradiction. Chose single-active-group model: StartUndoGroup while a group is active throws InvalidOperationException. Simpler, testable, and sufficient for hackathon scope. Section 3.6 update
- **R1-ISSUE-009**: accepted_defect — Factually correct. System.Net.WebSockets does not auto-reassemble fragmented messages. New Section 6.2 specifies the receive loop with EndOfMessage buffering and 1MB max message size.
- **R1-ISSUE-010**: accepted_defect — Valid misclassification. ArgumentException from handler internals is a server bug, not a client validation failure. Remapped to InternalError. Added custom exception subclasses (InvalidSketchStateExce
- **R1-ISSUE-011**: accepted_defect — Valid deadlock risk. New Section 2.3 defines inline execution when IsOnSwThread is true, preventing self-deadlock. Build-breaking ReentrancyDeadlockTests added.
- **R1-ISSUE-012**: accepted_defect — Valid ordering concern. Section 8.3 now explicitly states StateVersionCounter is accessed exclusively from the SW UI thread. Added ToolInProgress flag and event coalescing rule to prevent interleaved 
- **R1-ISSUE-013**: accepted_defect — Valid ambiguity. Section 3.4 now defines concrete exclusion criteria: excluded feature types listed by GetTypeName2 value, included parameters restricted to swParamTypeDouble.
- **R1-ISSUE-014**: accepted_defect — Valid testability gap. Added ProtocolDeserializerRoundTripTests as a build-breaking companion to the existing sync tests. Round-trip tests are the primary correctness signal; sync tests remain as a se
- **R1-ISSUE-015**: accepted_defect — Valid deployment concern. HttpListener on non-admin accounts requires URL ACL registration. Added netsh command to Section 6.1 and URL ACL verification to Day-0 validation in Section 11.
- **R1-ISSUE-016**: accepted_defect — Valid. Symbolic template names had no resolution path. Section 3.2 now defines resolution via ISldWorks.GetUserPreferenceStringValue(swDefaultTemplatePart) with unit system enforcement, plus TemplateN
- **R1-ISSUE-017**: accepted_defect — Valid. A modal save prompt on the UI thread would hang the adapter. Section 3.2 now specifies that the implementation marks the document as not requiring save before closing to suppress the prompt.
- **R1-ISSUE-018**: accepted_defect — Partially valid. The co-located assumption is correct for hackathon scope but lacked validation. New Section 7.4 adds BSVOICE_SCREENSHOT_DIR env var override and handshake-time path validation. Full c
- **R1-ISSUE-019**: accepted_defect — Low risk but valid. Added fallback in Section 8.1: on SecurityException or missing key, persists a random GUID to %LOCALAPPDATA%\BSVoiceCAD\.machine-id. Day-0 validates both paths.
- **R1-ISSUE-020**: preference_only — The spec already acknowledges this is a hackathon-scoped decision. Added a post-hackathon note to Section 5.2 recommending source generator or STJ shim evaluation before production. No structural chan
- **R1-ISSUE-021**: accepted_defect — Valid. The scope statement was misleading. Updated Section 0 to accurately enumerate all topics covered including bridge, dedup, session identity, and state versioning.

## Round 2

### [LOW] STA queue dispatches to a private thread, not the SolidWorks-owned COM apartment (R1-ISSUE-001)
**Problem:** R1 raised this as critical. The spec has been substantially revised in v1.1.0 to use SynchronizationContext captured from the SW UI thread itself, not a private STA thread. The core architectural defect is resolved. Residual concerns about WindowsFormsSynchronizationContext injection are captured in R2-ISSUE-001.
**Fix:** No further action on the original issue. Monitor R2-ISSUE-001 for the residual WinForms context concern.

### [LOW] STA worker thread has no Windows message pump, risking COM deadlocks (R1-ISSUE-002)
**Problem:** R1 raised this. The spec now explicitly leverages SolidWorks's own Windows message pump via SynchronizationContext. No dedicated worker thread is owned. Issue is resolved by the v1.1.0 redesign.
**Fix:** Closed. No action needed.

### [LOW] 5-second shutdown join guarantee is unenforceable given non-preemptible COM calls (R1-ISSUE-003)
**Problem:** R1 raised this. The spec now explicitly states Stop() is non-blocking and no thread join is required. Issue is resolved.
**Fix:** Closed. No action needed.

### [LOW] SHA256.HashData is not available on .NET Framework 4.8 (R1-ISSUE-004)
**Problem:** R1 raised this. The spec now uses SHA256.Create() / ComputeHash() which is available on .NET Framework 4.8. Issue is resolved.
**Fix:** Closed. No action needed.

### [LOW] Tool-call deduplication registry is cleared on disconnect, allowing CallId replay after reconnect (R1-ISSUE-005)
**Problem:** R1 raised this. The spec now explicitly states the registry survives WebSocket reconnects within the same session and is cleared only on session reset. Issue is resolved.
**Fix:** Closed. The in-memory-only persistence concern is now tracked as a separate issue (Grok's finding, see R2-ISSUE-002).

### [HIGH] Forced client drop can race with non-preemptible executing COM work, producing inconsistent state (R1-ISSUE-006)
**Problem:** The spec partially addresses this in Section 9.2 (Abandoned state, result cached without delivery) but Section 6.1's single-concurrent-client handoff scenario still leaves a gap: the Close frame is sent to the old client before the executing command finishes, and the new client is not yet upgraded. The eventual ToolResultMessage has no eligible recipient.
**Fix:** Define one explicit rule for the handoff case: either (a) delay sending Close until the executing command result is sent to the old client, then proceed with handoff; (b) mark the call Abandoned and cache the result for replay to the new client after handshake; or (c) reject the new connection with a busy/retry response until the command completes. The chosen rule must be consistent with Section 9.2 Abandoned semantics.
**Sources:** Fran

### [LOW] InFlight calls marked Cancelled on disconnect even though executing commands may still complete and mutate the model (R1-ISSUE-007)
**Problem:** R1 raised this. The spec now correctly distinguishes Queued→Cancelled from Executing→Abandoned. Issue is resolved.
**Fix:** Closed. No action needed.

### [LOW] Undo-group nesting is simultaneously permitted and prohibited (R1-ISSUE-008)
**Problem:** R1 raised this. The spec now explicitly prohibits nesting: 'Only one undo group may be active at a time. Calling StartUndoGroup while a group is already active throws InvalidUndoStateException.' Issue is resolved.
**Fix:** Closed. No action needed.

### [HIGH] WebSocket fragmentation handling is specified incorrectly (R1-ISSUE-009)
**Problem:** Section 6.2 now correctly specifies buffering frames until EndOfMessage=true. However, the oversized-message handling still has a gap: after detecting the 1 MB limit, the spec says 'the accumulated buffer is discarded' but does not specify whether remaining frames of the oversized message must be drained before resuming normal reads. Leaving unread frames on the socket corrupts all subsequent message framing.
**Fix:** Specify one of: (a) continue reading and discarding frames until EndOfMessage=true, then resume normal reads; or (b) close the WebSocket with a policy violation close code and reset session state. Option (b) is simpler and safer.
**Sources:** Codex

### [LOW] ArgumentException/ArgumentNullException from handler bugs mapped to InternalError — resolved (R1-ISSUE-010)
**Problem:** R1 raised this as a misclassification (ValidationFailed). The spec now maps ArgumentException/ArgumentNullException to InternalError with full stack trace logged. Issue is resolved.
**Fix:** Closed. No action needed.

### [LOW] ExecuteAsync reentrancy on the owned STA thread is unspecified, risking self-deadlock (R1-ISSUE-011)
**Problem:** R1 raised this. The spec now has a full Section 2.3 on reentrancy: when IsOnSwThread is true, ExecuteAsync executes inline. Issue is resolved.
**Fix:** Closed. No action needed.

### [LOW] State version ordering between SolidWorks events and tool-result snapshots is undefined (R1-ISSUE-012)
**Problem:** R1 raised this. Section 8.3 now defines the ordering guarantee: StateVersionCounter is accessed exclusively from the SW UI thread, and the ToolInProgress coalescing rule prevents duplicate/interleaved version numbers. Issue is resolved.
**Fix:** Closed. No action needed.

### [LOW] GetFeatureTree 'user-facing parameter' filtering criteria are undefined (R1-ISSUE-013)
**Problem:** R1 raised this. Section 3.4 now defines compressed filtering precisely: excluded feature types by GetTypeName2, and only swParamTypeDouble parameters retained. Issue is resolved.
**Fix:** Closed. No action needed.

### [LOW] Deserializer sync test checks source text presence, not runtime deserialization correctness (R1-ISSUE-014)
**Problem:** R1 raised this. Section 5.3 now defines both ProtocolDeserializerSyncTests (reflection-based switch-case existence check) and ProtocolDeserializerRoundTripTests (actual serialization/deserialization correctness). Issue is resolved.
**Fix:** Closed. No action needed.

### [LOW] HttpListener requires HTTP.sys URL ACL registration not addressed in deployment prerequisites (R1-ISSUE-015)
**Problem:** R1 raised this. Section 6.1 now explicitly documents the URL ACL requirement and provides the netsh command. Day-0 validation confirms bind success. Issue is resolved.
**Fix:** Closed. No action needed.

### [HIGH] NewPart template resolution contract is undefined, risking interactive dialogs or silent failures (R1-ISSUE-016)
**Problem:** The spec now defines template resolution steps (read preference, check file exists, throw TemplateNotFoundException, create, set units). However, both 'default_mm' and 'default_inch' resolve through the same single swDefaultTemplatePart preference path. The only differentiation is a post-creation unit switch. This means the two symbolic names do not represent distinct templates — they represent the same template file with different unit settings applied after creation. If the template file has embedded unit settings or custom properties that conflict with the post-creation switch, the contract is misleading.
**Fix:** Either: (a) define separate preference keys or configured paths for mm and inch templates so they can be genuinely distinct files; or (b) rename the parameter to represent requested units (e.g., 'units' enum) and document explicitly that the same default template file is reused with a post-creation unit switch. Option (b) is more honest about the implementation.
**Sources:** Fran

### [LOW] CloseDocument does not specify prompt suppression for dirty documents (R1-ISSUE-017)
**Problem:** R1 raised this. Section 3.2 now explicitly states: 'The implementation marks the document as not requiring save before closing (suppressing any save-changes modal dialog that would block the UI thread).' Issue is resolved.
**Fix:** Closed. No action needed.

### [HIGH] Screenshot sharing assumes co-located same-user filesystem context with no fallback (R1-ISSUE-018)
**Problem:** Three distinct sub-problems remain: (1) The orchestrator is declared read-only in Section 7.2 but Section 7.4 requires it to create and delete a temporary file to validate the path — this requires write access, directly contradicting the read-only declaration. (2) When path validation fails, the orchestrator 'disables screenshot-dependent features' but no message or negotiated capability is defined to inform the add-in to stop emitting screenshot data. The add-in will continue capturing and writing screenshots that the orchestrator ignores. (3) No fallback for ACL mismatches, antivirus locking, or different elevation contexts.
**Fix:** Fix (1): Replace the temp-file probe with a read-only accessibility check (e.g., attempt to open an add-in-written sentinel file), or grant the orchestrator explicit write/delete access and update Section 7.2 accordingly. Fix (2): Add a negotiated capability in the handshake — either the orchestrator sends a message disabling screenshot emission, or the add-in checks a capability flag before capturing. Fix (3): Document the supported deployment topology constraints explicitly.
**Sources:** Codex, Fran, Fran

### [LOW] AddinSessionId generation depends on HKLM registry read with no fallback for restricted accounts (R1-ISSUE-019)
**Problem:** R1 raised this. Section 8.1 now defines a FallbackMachineId() that persists a random GUID to %LOCALAPPDATA%\BSVoiceCAD\.machine-id. Day-0 validation step 7 tests both paths. Issue is resolved.
**Fix:** Closed. No action needed.

### [LOW] Hand-maintained deserializer creates ongoing sync fragility post-hackathon (R1-ISSUE-020)
**Problem:** R1 raised this. The spec now explicitly acknowledges this as a time-bounded decision with a post-hackathon note to replace with a source generator or shared serializer assembly. The build-breaking sync tests mitigate the risk during the hackathon. Issue is adequately addressed for the current scope.
**Fix:** Closed for hackathon scope. Track as technical debt.

### [LOW] Scope of spec exceeds stated boundaries, creating a god-spec (R1-ISSUE-021)
**Problem:** R1 raised this. The spec's scope section is now more precisely bounded with explicit out-of-scope items. The breadth is intentional for the adapter layer. Issue is adequately addressed.
**Fix:** Closed. No action needed.

### [MEDIUM] WindowsFormsSynchronizationContext injection may interfere with SolidWorks's native message handling (R2-ISSUE-001)
**Problem:** The spec installs a WindowsFormsSynchronizationContext on the SW UI thread if none is present. SolidWorks may already have its own message handling expectations for its UI thread, and injecting a WinForms context could alter modal dialog handling, COM event delivery ordering, or introduce hidden message filters. More critically, the spec only defines a Day-0 validation check but no runtime failure handling or startup gate if the probe fails.
**Fix:** Add a mandatory startup gate: if the SynchronizationContext probe fails (post/dispatch round-trip does not complete within a timeout), refuse to start the WebSocket bridge and emit a deterministic error to the log. Additionally, investigate whether SolidWorks already installs a SynchronizationContext on its UI thread in practice (likely it does, given it hosts WinForms dialogs); if so, the install-if-null path may never execute and the concern is moot — document this finding from Day-0 validation.
**Sources:** Grok, Fran

### [MEDIUM] ToolCallRegistry is in-memory only; SolidWorks crash loses all state, breaking reconnect guarantees (R2-ISSUE-002)
**Problem:** The registry survives WebSocket reconnects within the same session, but only in memory. If SolidWorks crashes or is force-terminated, the registry is lost. On restart, a new AddinSessionId is generated, so the orchestrator correctly detects a new session. However, the spec's disconnect recovery narrative (Section 9.2, 9.3) implicitly assumes the add-in process survives the disconnect — the 'Abandoned' state and cached results are only meaningful if the process is still running.
**Fix:** The spec already acknowledges the crash case in the disclaimer. For the hackathon scope, this is acceptable. However, the spec should explicitly state that the registry is in-memory only and that crash recovery relies solely on the new-AddinSessionId detection path, not on registry replay. This prevents implementers from assuming persistence is needed.
**Sources:** Grok

### [HIGH] No concrete cancellation hook defined for queued tool calls at disconnect time (R2-ISSUE-003)
**Problem:** Section 9.2 requires that Queued calls be marked Cancelled and their Tasks completed with OperationCanceledException on disconnect. However, the spec does not define how DisconnectHandler or ToolCallRegistry obtains a cancellation handle for already-posted work. ISwCommandQueue.ExecuteAsync accepts a CancellationToken from the caller, but the registry does not define ownership of a per-call CancellationTokenSource that the disconnect path can trigger.
**Fix:** Define that ToolCallRegistry creates and owns a CancellationTokenSource per tracked call. This token is passed into ISwCommandQueue.ExecuteAsync when the call is enqueued. On disconnect, DisconnectHandler iterates Queued entries and calls Cancel() on their CancellationTokenSources. The TaskScheduler's queued-cancel behavior (Section 2.4) then handles the rest. Add this to the ToolCallTracker data structure definition.
**Sources:** Codex

### [HIGH] Handshake has no server-side gate preventing pre-handshake tool_call messages from executing (R2-ISSUE-004)
**Problem:** Section 6.3 step 6 says 'Client may now send IClientMessages' after the initial StateUpdateMessage, but this is a client-side convention. MessageDispatcher has no defined state that prevents it from dispatching a pipelined tool_call that arrives before the initial state snapshot is sent. A racing tool_call can mutate the model before the baseline StateVersion exists.
**Fix:** Define an explicit HandshakeComplete flag in MessageDispatcher. Before this flag is set (after AddinReadyMessage and initial StateUpdateMessage are sent), any incoming IClientMessage is rejected with AddinProtocolErrorMessage(ErrorCode = 'HANDSHAKE_INCOMPLETE'). Set the flag atomically after the initial state snapshot is sent.
**Sources:** Codex

### [MEDIUM] AddinSessionId uniqueness guarantee is overstated; rapid reloads or clock rollback can produce collisions (R2-ISSUE-005)
**Problem:** Two related problems: (1) The spec states the ID is 'guaranteed to change on add-in reload' but uses DateTime.UtcNow.Ticks which is not monotonic — rapid reloads within the same tick resolution or clock rollback can produce the same input tuple and thus the same ID. (2) Section 8.1 says 'guaranteed to change on add-in reload' while Section 8.2 says regenerated 'On SolidWorks restart' — these conflict when the add-in is unloaded and reloaded within the same SolidWorks process (e.g., via the add-in manager).
**Fix:** Replace DateTime.UtcNow.Ticks with Guid.NewGuid() as the entropy source (or append it to the hash input) to guarantee uniqueness regardless of clock state. Explicitly define add-in reload within the same SolidWorks process as a session boundary (new AddinSessionId generated), and add this case to AddinSessionIdGenerationTests.
**Sources:** Codex, Fran

### [HIGH] CallId used raw in screenshot filenames without validation, enabling path traversal (R2-ISSUE-006)
**Problem:** The screenshot filename incorporates the client-supplied CallId directly as a path component. The spec does not require the adapter to validate or sanitize CallId before using it in filesystem paths. Any local process connecting to the localhost WebSocket can supply a malformed CallId containing path separators or traversal sequences (e.g., '../../../sensitive/file'), potentially writing files outside the intended screenshot directory.
**Fix:** Require that any inbound CallId be validated as a strict UUIDv7 format (lowercase hex with hyphens, matching the pattern defined in SPEC-01) before it is accepted into the registry or used in any filesystem path. Reject non-conforming CallIds with AddinProtocolErrorMessage(ErrorCode = 'INVALID_CALL_ID'). Add this validation to ToolDispatcher or MessageDispatcher at message ingestion time.
**Sources:** Fran

### [HIGH] ToolInProgress flag has no required finally-block cleanup, leaving event capture suppressed after tool exceptions (R2-ISSUE-007)
**Problem:** Section 2.3 defines the ToolInProgress flag as set by ToolDispatcher 'before and after each tool execution' to suppress re-entrant COM event state capture. However, the spec does not require this flag to be reset in a finally block. If tool execution throws an exception, the flag remains true indefinitely, permanently suppressing all subsequent event-driven state capture for the session.
**Fix:** Require ToolDispatcher to manage ToolInProgress in a try/finally block: set to true before invoking the handler, reset to false in the finally block regardless of outcome. Add a build-breaking test to ToolDispatcherTests that verifies ToolInProgress is false after a handler throws an exception.
**Sources:** Fran

### [MEDIUM] ModifyParameter featureId and parameterName can disagree with no defined resolution behavior (R2-ISSUE-008)
**Problem:** The ModifyParameter signature takes both featureId and parameterName, but a concrete SolidWorks parameter identifier (e.g., 'Depth@Boss-Extrude1') already embeds the owning feature name. The spec does not define what happens when featureId and the feature embedded in parameterName disagree — implementations may ignore featureId, use it as a secondary lookup, or throw on mismatch.
**Fix:** Either: (a) remove featureId from the method signature since parameterName already encodes the feature identity; or (b) define exact validation: if featureId and the feature name embedded in parameterName disagree, throw ArgumentException (mapped to InternalError). Document which takes precedence if featureId is used as a lookup hint.
**Sources:** Fran

### [LOW] Five-state deduplication registry complexity may be unnecessary given SPEC-03 lifecycle ownership (R2-ISSUE-009)
**Problem:** Grok argues the five-state registry (Queued/Executing/Completed/Cancelled/Abandoned) with 60-second result cache and multiple error codes creates combinatorial test complexity that may be unnecessary if SPEC-03 enforces unique CallIds and handles timeouts at the lifecycle layer.
**Fix:** This is a design trade-off, not a clear defect. The registry provides idempotency guarantees that are valuable for reconnect scenarios. Flag as a design decision to be validated against SPEC-03 requirements. If SPEC-03 can guarantee unique CallIds and does not need replay, simplification is possible. Do not simplify without SPEC-03 author agreement.
**Sources:** Grok

### Coordinator Decisions
- **R1-ISSUE-001**: rejected_claim — Already resolved in Round 1. Section 2.2 rewritten to capture SynchronizationContext from ConnectToSW.
- **R1-ISSUE-002**: rejected_claim — Already resolved in Round 1. No separate pump or worker thread.
- **R1-ISSUE-003**: rejected_claim — Already resolved in Round 1. Stop is non-blocking.
- **R1-ISSUE-004**: rejected_claim — Already resolved in Round 1. Uses SHA256.Create()/ComputeHash().
- **R1-ISSUE-005**: rejected_claim — Already resolved in Round 1. Registry is session-scoped and survives reconnects.
- **R1-ISSUE-006**: accepted_defect — Residual gap from Round 1: during client handoff with an executing command, the result has no delivery target. The old client received a Close frame; the new client hasn't handshaked. Fix: explicitly 
- **R1-ISSUE-007**: rejected_claim — Already resolved in Round 1. Queued→Cancelled and Executing→Abandoned states defined.
- **R1-ISSUE-008**: rejected_claim — Already resolved in Round 1. Single-active-group model with InvalidUndoStateException.
- **R1-ISSUE-009**: accepted_defect — Residual gap: after detecting oversized message, remaining frames corrupt subsequent reads. Fix: close the WebSocket with close code 1009 (Message Too Big) instead of attempting to discard and resume.
- **R1-ISSUE-010**: rejected_claim — Already resolved in Round 1. ArgumentException mapped to InternalError.
- **R1-ISSUE-011**: rejected_claim — Already resolved in Round 1. Inline execution when IsOnSwThread is true.
- **R1-ISSUE-012**: rejected_claim — Already resolved in Round 1. Single-threaded access with coalescing rule.
- **R1-ISSUE-013**: rejected_claim — Already resolved in Round 1. Concrete exclusion criteria defined.
- **R1-ISSUE-014**: rejected_claim — Already resolved in Round 1. Round-trip tests added.
- **R1-ISSUE-015**: rejected_claim — Already resolved in Round 1. URL ACL documented with netsh command.
- **R1-ISSUE-016**: preference_only — The spec explicitly documents that both symbolic names resolve to the same template file with a post-creation unit switch (Section 3.2 steps 1-4). The behavior is correct, transparent, and not mislead
- **R1-ISSUE-017**: rejected_claim — Already resolved in Round 1. Save-changes prompt suppression specified.
- **R1-ISSUE-018**: accepted_defect — Residual sub-problem (1): orchestrator is declared read-only but validation requires creating/deleting a temp file, which is a write operation. Fix: replace temp-file probe with sentinel-file read. Th
- **R1-ISSUE-019**: rejected_claim — Already resolved in Round 1. FallbackMachineId() with persisted GUID.
- **R1-ISSUE-020**: rejected_claim — Already classified as preference_only in Round 1. Post-hackathon note added.
- **R1-ISSUE-021**: rejected_claim — Already resolved in Round 1. Scope section updated.
- **R2-ISSUE-001**: accepted_defect — Valid feasibility concern. The spec defines Day-0 validation but no runtime failure handling if the SynchronizationContext doesn't work. Fix: add a mandatory startup probe with 5-second timeout. If th
- **R2-ISSUE-002**: accepted_defect — Valid weak signal. The spec implicitly assumes process survival for registry replay but never explicitly states the registry is in-memory only. Fix: add explicit statement to Section 6.5 that the regi
- **R2-ISSUE-003**: accepted_defect — Valid implementation gap. Section 9.2 requires Queued calls to be cancelled on disconnect, but no CancellationTokenSource ownership is defined. Fix: each ToolCallTracker owns a CancellationTokenSource
- **R2-ISSUE-004**: accepted_defect — Valid operational risk. A pipelined tool_call arriving before the initial StateUpdateMessage can mutate the model before a baseline StateVersion exists. Fix: add HandshakeComplete flag to MessageDispa
- **R2-ISSUE-005**: accepted_defect — Valid. DateTime.UtcNow.Ticks is not monotonic; rapid reloads or clock rollback can collide. Fix: add Guid.NewGuid() nonce to hash input. Also explicitly define add-in reload within the same SW process
- **R2-ISSUE-006**: accepted_defect — Valid security defect. CallId is client-supplied and used raw in filesystem paths. Path traversal is possible via malformed CallId containing '../' sequences. Fix: validate CallId as strict UUIDv7 for
- **R2-ISSUE-007**: accepted_defect — Valid omission. If a tool handler throws, the ToolInProgress flag stays true forever, permanently suppressing event-driven state capture. Fix: require ToolDispatcher to manage the flag in a try/finall
- **R2-ISSUE-008**: accepted_defect — Valid API contract ambiguity. The parameterName already encodes the feature name, making featureId potentially redundant or conflicting. Fix: define featureId as a scoping/validation parameter — the i
- **R2-ISSUE-009**: preference_only — Design trade-off, not a defect. The five-state registry provides idempotency guarantees valuable for reconnect scenarios. Simplification requires SPEC-03 author agreement and verification that SPEC-03

## Open Questions

- Does SolidWorks 2025 SP3 install a SynchronizationContext on its UI thread during ConnectToSW, or will WindowsFormsSynchronizationContext always need to be installed?
- Can the ToolInProgress flag miss edge cases where SolidWorks fires COM events asynchronously (via a timer or deferred callback) rather than re-entrantly during a tool's COM operations?
- Is URL ACL registration actually required for http://127.0.0.1:7878/ on the target demo machine's Windows version and user configuration, or does the localhost exemption apply?
