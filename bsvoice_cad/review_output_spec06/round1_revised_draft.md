---
title: "BSVoice-CAD SPEC-06 — Viewer"
subtitle: "Timeline rendering, DHF read API, integrity endpoints"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-06 — Viewer

**Version:** 1.1.0
**Status:** DRAFT — post-Oracle-round-1
**Depends on:** SPEC-00 (module boundaries), SPEC-01 (StateSnapshot and FeatureNode for display only), SPEC-04 (IReadOnlyDhfRepository for reads)
**Consumed by:** (none — leaf module in the other direction)

## 0. Scope

This spec defines the read-only DHF viewer — the web page that shows what happened during a voice-and-text modeling session. It lives in-process with the orchestrator as an ASP.NET Core Minimal API. It has no write paths. It never calls SolidWorks. It never touches the agent loop. It reads `IReadOnlyDhfRepository` (read-only projection defined in SPEC-04) and renders HTML or JSON.

**In scope:** Minimal API host, Razor Pages for the timeline, vanilla-JS incremental updates, JSON endpoints for DHF reads and integrity checks, stretch-only PDF export and entry-detail pages.

**Out of scope:** DHF schema (SPEC-04), write paths (there are none), authentication (localhost-only per SPEC-00 SDR), session management (SPEC-04 owns it), voice or vision (SPEC-05).

## 1. Project Shape

```
BSVoiceCAD.Orchestrator.Viewer/
├── ViewerHost.cs                        # [Mandatory] Host builder invoked from Program.cs
├── Endpoints/
│   ├── DhfEndpoints.cs                  # [Mandatory] /, /api/dhf/sessions, /api/dhf/entries
│   ├── EntryDetailEndpoints.cs          # [Stretch]   /entry/{id}, /api/dhf/entries/{id}
│   ├── IntegrityEndpoints.cs            # [Stretch]   /api/dhf/integrity
│   └── ExportEndpoints.cs              # [Stretch]   /api/dhf/export/pdf
├── Pages/
│   ├── Index.cshtml                     # [Mandatory] Session list + timeline
│   ├── EntryDetail.cshtml               # [Stretch]   Single-entry detail
│   └── _Layout.cshtml                   # [Mandatory]
├── wwwroot/
│   ├── app.js                           # [Mandatory] Vanilla JS, ~150 LOC
│   ├── style.css                        # [Mandatory]
│   └── favicon.ico                      # [Mandatory]
└── ViewModels/
    ├── TimelineViewModel.cs             # [Mandatory]
    └── EntryDetailViewModel.cs          # [Stretch]
```

For hackathon baseline, only `[Mandatory]` files need to exist. `[Stretch]` files are created only if `EnableStretchEndpoints` is being implemented.

## 2. Design Decisions (Local SDRs)

**SDR-06-001: ASP.NET Core Minimal API + Razor Pages.** No React, no Blazor, no build step. Single orchestrator process serves both JSON endpoints and HTML pages. Rejected alternative: Blazor Server (adds SignalR dependency and state management complexity for a read-only viewer).

**SDR-06-002: localhost-only.** Binds to `http://127.0.0.1:7879`. No auth. Hackathon scope. Documented in SPEC-00 SDR-TL-005; threat model is "single-user local workstation." Post-hackathon: add authentication if multi-user.

**SDR-06-003: Read-only, enforced at the dependency boundary.** The viewer never writes to any table. It never calls SolidWorks. All paths go through `IReadOnlyDhfRepository` — a read-only projection of the full `IDhfRepository` defined in SPEC-04. SPEC-04 must expose this interface containing only `ListSessionsAsync`, `ListEntriesAsync`, `GetEntryDetailAsync`, and `ValidateChainAsync`. The viewer's DI container resolves only `IReadOnlyDhfRepository`; no write-capable repository type is registered.

Enforcement mechanisms (all must pass):
1. **DI-container test** (`ViewerReadOnlyDiTests`): asserts that `IDhfRepository` and any write-capable type are not resolvable from the viewer's `IServiceProvider`.
2. **Architecture test** (`ViewerReadOnlyArchitectureTests`): NetArchTest rule — no SQL INSERT/UPDATE/DELETE in `BSVoiceCAD.Orchestrator.Viewer.*`.
3. **Interface constraint**: `IReadOnlyDhfRepository` exposes no write methods by design.

**SDR-06-004: Minimal viewer is Mandatory; polish is Stretch.** The timeline page (session list + expandable entries) is baseline and covers ACC-08. Entry detail pages, integrity indicator, PDF export, and industrial-utilitarian styling are stretch. Items are annotated `[Mandatory]` or `[Stretch]` throughout this spec.

**SDR-06-005: Shallow validation on viewer load, deep validation only on demand.** Shallow hash-chain validation runs when the timeline renders (fast, no file I/O). Deep validation (recomputes feature-tree hashes from JSONB and screenshot SHA-256s from files) runs only when the user hits `/api/dhf/integrity?deep=true`. This prevents viewer page loads from stalling on large sessions.

## 3. Hosting

`ViewerHost.Start(IReadOnlyDhfRepository repo, ViewerOptions options)` is called from `OrchestratorProgram.Main` after DI setup. It builds a `WebApplication` bound to `http://127.0.0.1:7879` (configurable via `options.Port`), maps endpoints from the `Endpoints/` folder, serves static files from `wwwroot/`, and starts listening. Runs on the orchestrator's main process — not a separate service.

```csharp
public sealed class ViewerHost : IAsyncDisposable
{
    public ViewerHost(IReadOnlyDhfRepository repo, ViewerOptions options);
    public Task StartAsync(CancellationToken ct);
    public Task StopAsync(CancellationToken ct);
    public ValueTask DisposeAsync();
}

public sealed class ViewerOptions
{
    public int Port { get; init; } = 7879;
    public bool EnableStretchEndpoints { get; init; }  // Enables entry detail, integrity, PDF
    public string? ScreenshotRootPath { get; init; }   // Required only when EnableStretchEndpoints=true
}
```

Startup validation:
- If `EnableStretchEndpoints` is `true` and `ScreenshotRootPath` is null or does not exist on disk, the viewer throws `InvalidOperationException` at startup.
- If `EnableStretchEndpoints` is `false`, `ScreenshotRootPath` is ignored regardless of its value.

## 4. Mandatory Endpoints

### 4.1 GET /

Razor page. Renders the timeline: list of DHF sessions (most recent first), expandable per-entry view showing transcript, turn_type, undo_outcome, and a count of tool calls. Entry rows are color-coded by `turn_type` and `undo_outcome`.

**Session selection contract:**

- `GET /` — renders the most recent session (by `startedAt` descending). If no sessions exist, renders an empty-state page with the message "No design sessions recorded yet."
- `GET /?sessionId={uuid}` — renders the specified session. Returns 404 if the session does not exist. Returns 400 if `sessionId` is not a valid UUID.
- The session list sidebar (or dropdown) is always visible, allowing the user to switch sessions by navigating to `/?sessionId={uuid}`.

The `TimelineViewModel` is populated at page-render time via `IReadOnlyDhfRepository.ListSessionsAsync` (for the session list) and `IReadOnlyDhfRepository.ListEntriesAsync` (for the selected session's entries).

Must render within 1 second for a 20-entry session on the demo laptop (GE75). ACC-08 measures this (see Section 11 for methodology).

### 4.2 GET /api/dhf/sessions

JSON list of session summaries. Used by vanilla-JS `app.js` to poll for new sessions without full page reload.

```json
[
  {
    "sessionId": "...",
    "startedAt": "2026-04-21T13:00:00Z",
    "endedAt": null,
    "userDisplayName": "Joe Wilde",
    "workstationId": "WILDE-GE75",
    "entryCount": 7,
    "addinSessionId": "..."
  }
]
```

### 4.3 GET /api/dhf/entries?sessionId={uuid}

JSON list of entries for a session, ordered by `sequence_number` ascending.

```json
[
  {
    "entryId": "...",
    "sequenceNumber": 1,
    "timestamp": "...",
    "inputModality": "Voice",
    "rawTranscript": "Make a 100 by 50 by 20 plate",
    "turnType": "Execution",
    "undoOutcome": "Committed",
    "toolCallCount": 5,
    "recordHash": "...",
    "previousHash": "GENESIS"
  }
]
```

Error responses:

```json
// 400
{ "error": "missing or invalid sessionId" }
// 404
{ "error": "session not found" }
```

## 5. Stretch Endpoints

Only mounted when `ViewerOptions.EnableStretchEndpoints = true`.

### 5.1 GET /entry/{entryId}

Razor page showing full detail for one entry: transcript, tool calls in order with inputs and results expanded, feature tree before/after (folded), screenshot thumbnails with click-to-expand, decorations (rationale, ambiguity context, integrity events), hash-chain status indicator (green if chain valid, red if tampered).

### 5.2 GET /api/dhf/entries/{entryId}

Full JSON detail for a single entry. Used by the entry detail page.

```json
{
  "entryId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "sessionId": "...",
  "sequenceNumber": 1,
  "timestamp": "2026-04-21T13:00:02Z",
  "inputModality": "Voice",
  "rawTranscript": "Make a 100 by 50 by 20 plate",
  "turnType": "Execution",
  "undoOutcome": "Committed",
  "recordHash": "...",
  "previousHash": "GENESIS",
  "featureTreeBefore": { "json": "..." },
  "featureTreeAfter": { "json": "..." },
  "screenshotBeforeUrl": "/screenshots/{addinSessionId}/{filename}" ,
  "screenshotAfterUrl": "/screenshots/{addinSessionId}/{filename}",
  "toolCalls": [
    {
      "toolCallId": "...",
      "sequenceWithinEntry": 1,
      "toolName": "CreateBossExtrude",
      "inputJson": { },
      "resultJson": { },
      "durationMs": 340,
      "success": true,
      "errorMessage": null
    }
  ],
  "decorations": [
    {
      "decorationId": "...",
      "decorationType": "Rationale",
      "contentJson": { },
      "createdAt": "2026-04-21T13:00:03Z"
    }
  ]
}
```

Field notes:

| Field | Type | Nullable | Notes |
|-------|------|----------|-------|
| `entryId` | UUID | no | |
| `sessionId` | UUID | no | |
| `sequenceNumber` | int | no | 1-based, ascending |
| `timestamp` | ISO 8601 | no | UTC |
| `inputModality` | string | no | Enum: `"Voice"`, `"Text"` |
| `rawTranscript` | string | no | |
| `turnType` | string | no | Enum: `"Execution"`, `"Clarification"`, `"Confirmation"`, `"AmbiguityResolution"` |
| `undoOutcome` | string | yes | Enum: `"Committed"`, `"RolledBack"`, `"Abandoned"`, or null if not applicable |
| `recordHash` | string | no | SHA-256 hex |
| `previousHash` | string | no | SHA-256 hex or `"GENESIS"` |
| `featureTreeBefore` | object | yes | SPEC-01 FeatureNode JSON; null if not captured |
| `featureTreeAfter` | object | yes | SPEC-01 FeatureNode JSON; null if not captured |
| `screenshotBeforeUrl` | string | yes | Relative URL; null if no screenshot |
| `screenshotAfterUrl` | string | yes | Relative URL; null if no screenshot |
| `toolCalls` | array | no | Ordered by `sequenceWithinEntry` ascending; empty array if none |
| `toolCalls[].toolCallId` | UUID | no | |
| `toolCalls[].sequenceWithinEntry` | int | no | 1-based |
| `toolCalls[].toolName` | string | no | |
| `toolCalls[].inputJson` | object | no | Tool-specific input payload |
| `toolCalls[].resultJson` | object | yes | null if tool did not return a result |
| `toolCalls[].durationMs` | int | no | |
| `toolCalls[].success` | bool | no | |
| `toolCalls[].errorMessage` | string | yes | null on success |
| `decorations` | array | no | Ordered by `createdAt` ascending; empty array if none |
| `decorations[].decorationId` | UUID | no | |
| `decorations[].decorationType` | string | no | Enum: `"Rationale"`, `"AmbiguityContext"`, `"IntegrityEvent"` |
| `decorations[].contentJson` | object | no | Type-specific payload |
| `decorations[].createdAt` | ISO 8601 | no | UTC |

Error responses: 400 (invalid entryId format), 404 (entry not found).

### 5.3 GET /api/dhf/integrity?sessionId={uuid}&deep=false

Runs `HashChainValidator.ValidateShallow` (default) or `ValidateDeep` (when `deep=true`) on the session's chain.

**Timeout:** Deep validation enforces an 8-second hard timeout via `CancellationToken`. If the timeout fires, the response is returned with results validated so far and `truncated=true`.

```json
{
  "sessionId": "...",
  "mode": "shallow",
  "valid": true,
  "truncated": false,
  "timeoutMs": 8000,
  "entriesChecked": 7,
  "lastSequenceChecked": 7,
  "failures": [],
  "warnings": [
    {
      "entryId": "...",
      "sequenceNumber": 4,
      "reason": "screenshot_file_missing",
      "detail": "screenshot_after_path no longer exists on disk"
    }
  ]
}
```

**Failure vs. warning taxonomy:**

| Condition | Classification | Effect on `valid` |
|-----------|---------------|--------------------|
| Hash-chain break (record hash mismatch) | `failure` | `valid = false` |
| Previous-hash link mismatch | `failure` | `valid = false` |
| Feature-tree JSONB hash mismatch (deep only) | `failure` | `valid = false` |
| Screenshot SHA-256 mismatch (deep, file exists but content differs) | `failure` | `valid = false` |
| Screenshot file missing from disk (deep only) | `warning` | `valid` unchanged |
| Decoration content hash mismatch | `failure` | `valid = false` |

**Semantics when `truncated=true`:** `valid` reflects only the entries actually checked (up to `lastSequenceChecked`). The client must treat a truncated result as inconclusive for entries beyond `lastSequenceChecked`.

### 5.4 POST /api/dhf/export/pdf?sessionId={uuid}

QuestPDF-based PDF generation. Streams the PDF as the response body. Content-Type `application/pdf`. The PDF includes: session metadata header, per-entry transcript and tool-call summary, hash chain verification status, full tool-call inputs/outputs in appendix. Screenshots embedded inline where present.

## 6. Timeline Page Layout

Single-column layout. Industrial-utilitarian aesthetic (monospace headers, grid layout, minimal chrome). Rough markup:

```
┌──────────────────────────────────────────────────────────────────┐
│ BSVoice-CAD Design Audit Log                      [●] Running    │
├──────────────────────────────────────────────────────────────────┤
│ Session: 2026-04-21 13:00 · Joe Wilde · WILDE-GE75               │
│ 7 entries · hash chain: valid (shallow)                          │
├──────────────────────────────────────────────────────────────────┤
│ #1  13:00:02  [voice]  Execution ▸ Committed                     │
│     "Make a 100 by 50 by 20 plate"                               │
│     5 tool calls · Boss-Extrude1                                 │
├──────────────────────────────────────────────────────────────────┤
│ #2  13:00:45  [voice]  Execution ▸ Committed                     │
│     "Add a 6 millimeter hole in the center"                      │
│     3 tool calls · Cut-Extrude1                                  │
├──────────────────────────────────────────────────────────────────┤
│ #3  13:01:12  [text]   Clarification                             │
│     "Fillet this edge"                                           │
│     0 tool calls · ambiguity: which edge?                        │
└──────────────────────────────────────────────────────────────────┘
```

Color coding:
- Execution + Committed: subtle green row tint
- Execution + RolledBack: subtle amber
- Execution + Abandoned: subtle red
- Clarification / Confirmation: subtle grey
- AmbiguityResolution: subtle blue

## 7. JavaScript (app.js)

Vanilla JS. No framework. Polls `/api/dhf/sessions` every 3 seconds when the user is on the timeline page. When a new session appears or the current session gains entries, inserts them into the DOM without full page reload. ~150 LOC total.

Specifically no WebSocket to the viewer — the existing WebSocket at :7878 is the add-in bridge, not a viewer push channel. Polling is sufficient for hackathon scope and avoids introducing a second WS endpoint.

## 8. Static Assets

`wwwroot/style.css` applies the industrial-utilitarian look: monospace headers (`ui-monospace, SF Mono, Consolas, monospace`), body in a legible sans (`system-ui`), grid-based layout, subtle 1px borders, color-coded rows. Dark-mode variant via `@media (prefers-color-scheme: dark)` so the demo video looks good regardless of the judges' display settings.

No CDN dependencies. No Google Fonts. All assets are local.

## 9. Screenshot Serving (Stretch)

For entry detail pages to show thumbnail previews, the viewer needs to serve screenshot files that live in `{LocalAppData}\BSVoiceCAD\screenshots\` (SPEC-02 convention). This is NOT in `wwwroot/` because screenshots live per-user per-session.

Solution: a `GET /screenshots/{addinSessionId}/{filename}` endpoint that validates both path segments and streams the file with `Content-Type: image/png`.

**Path traversal guards (all checks must pass):**

1. **`addinSessionId`:** Must pass `Guid.TryParse`. Reject with 400 if not a valid UUID.
2. **`filename`:** Must match the expected pattern `^[0-9]+_[0-9a-fA-F\-]+\.png$`. Reject with 400 if it contains `..`, `/`, `\`, null bytes, or does not match the pattern.
3. **Canonical path check:** Build the candidate path as `Path.Combine(ScreenshotRootPath, addinSessionId, filename)`, resolve it with `Path.GetFullPath`, and reject with 403 if the resolved path does not start with `Path.GetFullPath(ScreenshotRootPath)`. This is the final gate against any construction that escapes the root.
4. **File existence:** Return 404 if the resolved file does not exist.

Only mounted when `EnableStretchEndpoints = true` since this is consumed by the entry detail page.

## 10. Read Consistency

The viewer runs in-process with the orchestrator, which may be actively appending DHF entries during a modeling session. The viewer tolerates eventual consistency:

- **Timeline page and entry-list endpoints:** Each call to `IReadOnlyDhfRepository` uses the database's default read-committed isolation. The session list and entry list are fetched in separate calls; the entry count in the session list may briefly lag behind the actual entries returned. This is acceptable — the next 3-second poll corrects it.
- **Integrity validation:** Shallow validation reads the hash chain in a single query ordered by `sequence_number`. If a new entry is appended between validation start and finish, the validator operates on a consistent prefix (all entries up to the last one it fetched). Deep validation must acquire a repeatable-read snapshot for the session's entries to prevent mid-validation mutations from causing false tamper reports. SPEC-04's `IReadOnlyDhfRepository.ValidateChainAsync` must honor a `IsolationLevel` parameter or internally guarantee snapshot consistency.
- **Entry detail:** Single-entry reads are atomic by nature.

## 11. Required Tests

All in `BSVoiceCAD.Orchestrator.Tests` using `WebApplicationFactory<T>` for in-process API testing. No SolidWorks dependency; these tests can run on any OS with .NET 8.

### Mandatory Tests

- `TimelineRenderTests` — GET / returns 200, contains expected session and entry counts for seeded test data; GET /?sessionId={valid} renders correct session; GET /?sessionId={invalid} returns 400; GET / with no sessions renders empty state
- `SessionsEndpointTests` — GET /api/dhf/sessions returns well-formed JSON, correct ordering (most recent first)
- `EntriesEndpointTests` — GET /api/dhf/entries returns entries in sequence_number ascending, 404 on unknown sessionId, 400 on missing sessionId
- `ViewerReadOnlyDiTests` — Asserts `IDhfRepository` (write-capable) is not resolvable from the viewer's DI container; only `IReadOnlyDhfRepository` is registered
- `ViewerReadOnlyArchitectureTests` — NetArchTest rule: no SQL INSERT/UPDATE/DELETE in `BSVoiceCAD.Orchestrator.Viewer.*`
- `TimelinePerformanceTests` — CI regression guard: 20-entry seeded session renders within 3 seconds on any CI runner (generous ceiling); see ACC-08 below for demo-specific validation

### Stretch Tests

- `EntryDetailEndpointTests` — GET /api/dhf/entries/{entryId} returns well-formed JSON matching schema in Section 5.2; 404 on unknown; 400 on invalid UUID
- `IntegrityEndpointTests` — shallow and deep modes; tamper detection (seed a modified row, assert failure surfaces); truncation behavior (mock slow validation, assert `truncated=true` and `lastSequenceChecked` populated)
- `PdfExportTests` — QuestPDF export produces valid PDF, content-type correct
- `ScreenshotPathTraversalTests` — malicious `addinSessionId` values rejected (e.g. `../../etc`, non-UUID strings) with 400; malicious filenames rejected with 400; canonical-path escape attempts rejected with 403

## 12. Acceptance Tests (exposed to SPEC-00 ACC table)

| ID | Criterion | Method |
|----|-----------|--------|
| ACC-08 | Viewer timeline renders a 20-entry session in under 1 second | Manual demo validation on GE75 (see below) |

**ACC-08 measurement methodology:**
- **Dataset:** 20 DHF entries seeded into local Postgres, each with 3–5 tool calls and representative transcript lengths.
- **Measurement:** Wall-clock time from HTTP request initiation to full HTML response received, measured via `Stopwatch` in the test harness or browser DevTools network tab.
- **Warmup:** One discarded request before measurement to eliminate JIT and connection-pool cold-start.
- **Demo validation:** Performed manually on the GE75 demo laptop. Threshold: < 1 second.
- **CI regression guard:** `TimelinePerformanceTests` runs on every build with a 3-second ceiling to catch gross regressions without false-failing on slow CI hardware.

That's the only ACC this spec owns. All other DHF-related ACCs (hash chain integrity, tamper detection) live in SPEC-04 because they're functions of the ledger, not the viewer.

## 13. Configuration

Viewer configuration lives in orchestrator `appsettings.json`:

```json
{
  "viewer": {
    "port": 7879,
    "enableStretchEndpoints": true,
    "screenshotRootPath": "%LOCALAPPDATA%\\BSVoiceCAD\\screenshots"
  }
}
```

When `enableStretchEndpoints` is `false`, `screenshotRootPath` may be omitted or null.

## 14. Failure Modes

The viewer distinguishes connectivity failures from data errors using the exception taxonomy below:

| Exception Type | HTTP Status | HTML Behavior | JSON Behavior |
|---------------|-------------|---------------|---------------|
| `RepositoryUnavailableException` (connection timeout, Postgres unreachable, connection pool exhausted) | 503 | Renders page with degraded-state banner: "Design audit log temporarily unavailable. Retrying…" | Returns `{ "error": "service_unavailable", "message": "Database is temporarily unreachable" }` |
| All other `IDhfRepository` / `IReadOnlyDhfRepository` exceptions (query errors, deserialization failures, constraint violations) | 500 | Renders generic error page: "An unexpected error occurred." | Returns `{ "error": "internal_error", "message": "An unexpected error occurred" }` |

In all cases:
- Full stack traces go to the Serilog file sink. The viewer never exposes database errors or stack traces to the user.
- `RepositoryUnavailableException` is defined in SPEC-04 and thrown by the repository implementation when the underlying connection fails. If SPEC-04 does not yet define this type, it wraps `NpgsqlException` with `IsTransient=true` or `TimeoutException`.

**Deep integrity validation timeout:** Handled by the 8-second `CancellationToken` described in Section 5.3. The endpoint returns partial results with `truncated=true`; it does not return 500 or 503 on timeout.

## 15. Out of Scope

- Write paths (there are none in this spec by design)
- Multi-user session filtering (post-hackathon)
- Real-time push (polling is adequate for hackathon)
- Authentication / authorization (localhost-only, SPEC-00 SDR-TL-005)
- Any SolidWorks interaction
- Any agent loop logic
- DHF writing (SPEC-04 owns that)
- Protocol versioning (SPEC-01 owns that)

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*