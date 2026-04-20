# ORACLE Review Report

**Document:** D:\BoundState\specs\bsvoice_cad\SPEC-06_VIEWER.md
**Session:** 20260418-124659
**Rounds:** 2
**Converged:** No

## Metrics

- Accepted critical defects: 0
- Accepted high defects: 10
- Total accepted defects: 21
- Total rejected claims: 4
- Preference-only findings: 3
- Duplicate rate: 0.0%
- Rounds to stability: 2
- Converged: False

## Round 1

### [CRITICAL] Path traversal protection ignores the `addinSessionId` path segment (R1-ISSUE-001)
**Problem:** The screenshot endpoint `GET /screenshots/{addinSessionId}/{filename}` applies path-traversal guards only to `filename`. The `addinSessionId` segment is inserted into the filesystem path without format validation or canonical-path verification, enabling escape from `ScreenshotRootPath` or cross-session file access.
**Fix:** Constrain `addinSessionId` to a strict UUID format (regex or `Guid.TryParse`), build the full resolved path canonically (`Path.GetFullPath`), and reject any resolved path that does not start with the canonical `ScreenshotRootPath`.
**Sources:** Fran, Codex

### [HIGH] GET / does not define how the 'selected session' is chosen (R1-ISSUE-002)
**Problem:** The mandatory timeline page populates `TimelineViewModel` via `ListEntriesAsync` for 'the selected session', but the spec never defines how a session is selected, what the default is when multiple sessions exist, how a user switches sessions, or what the empty-state behavior is.
**Fix:** Define the selection contract explicitly: e.g. `GET /?sessionId={uuid}` with a documented default rule (most recent session) when `sessionId` is absent, plus defined behavior for missing/invalid IDs and the no-sessions empty state.
**Sources:** Fran, Codex

### [HIGH] Integrity endpoint timeout/truncation behavior is absent from the API contract (R1-ISSUE-003)
**Problem:** Section 13 states the endpoint returns partial results with a 'truncation flag' on timeout, but the Section 5.3 response schema contains no such field. No concrete timeout value, cancellation-token propagation, or max-duration guard is specified. Clients cannot distinguish a complete validation from a timed-out partial result.
**Fix:** Add explicit fields to the integrity response schema: `truncated` (bool), `timeoutMs` (int), `lastSequenceChecked` (int). Define semantics for `valid` when `truncated=true`. Specify a hard timeout (e.g. 8 seconds) with `CancellationToken` propagation, or make deep validation an async background job returning a `jobId`.
**Sources:** Fran, Codex, Grok

### [HIGH] Repository failure handling contradicts Postgres-unreachable handling (R1-ISSUE-004)
**Problem:** Section 13 assigns HTTP 500 to `IDhfRepository` throws and HTTP 503 to Postgres-unreachable, but a Postgres outage surfaces as an `IDhfRepository` exception. The spec provides no exception taxonomy or result contract to distinguish the two cases, leading to inconsistent status codes and undefined banner-trigger logic for the HTML route.
**Fix:** Define an exception taxonomy: e.g. `RepositoryUnavailableException` (connection/timeout) → 503 + degraded-page banner; all other repository exceptions → 500. Specify how Razor page routes catch and render the banner vs. error state.
**Sources:** Fran, Codex

### [HIGH] ACC-08 performance acceptance criterion is not reproducibly testable (R1-ISSUE-005)
**Problem:** The 1-second render threshold is tied to a specific machine (GE75) with unspecified cache state, dataset characteristics, and measurement boundaries. The automated test is otherwise described as portable and in-process, making CI results hardware-dependent and non-deterministic.
**Fix:** Either move ACC-08 to a manual demo-validation procedure with the GE75 threshold, or define a reproducible benchmark harness with fixed dataset, explicit measurement start/stop points, warmup passes, and a hardware-agnostic target (e.g. p95 latency under controlled load).
**Sources:** Fran, Codex

### [HIGH] Read-only guarantee is not enforced at the dependency boundary (R1-ISSUE-006)
**Problem:** The architecture test only scans Viewer-namespace source for SQL INSERT/UPDATE/DELETE strings. This does not prevent the viewer from invoking write-capable repository methods or receiving a write-capable `IDhfRepository` implementation. The stated strong non-write guarantee lacks an enforcement mechanism.
**Fix:** Introduce a `IReadOnlyDhfRepository` interface exposing only read methods, inject only that interface into the viewer, and add a test that fails if any write-capable type is resolvable from the viewer's DI container.
**Sources:** Fran

### [HIGH] Entry detail JSON schema is unspecified (R1-ISSUE-007)
**Problem:** Section 5.2 references `ToolCallRow[]` and `DecorationRow[]` in the response but defines no fields, types, nullability, ordering guarantees, or nested object shapes. The entry-detail page and its tests cannot be built without inventing a private schema.
**Fix:** Add a complete JSON contract for `/api/dhf/entries/{entryId}` covering every field name, type, enum domain, nullability, ordering, and screenshot URL representation.
**Sources:** Codex

### [HIGH] Read consistency is undefined during concurrent orchestrator writes (R1-ISSUE-008)
**Problem:** The viewer runs in-process with the orchestrator. `GET /` issues separate `ListSessionsAsync` and `ListEntriesAsync` calls; `app.js` polls every 3 seconds; integrity validation reads live session data. No snapshot isolation, transaction boundaries, or concurrent-write behavior is defined, risking internally inconsistent counts, timelines, or false tamper reports.
**Fix:** Define repository read-isolation requirements for viewer endpoints (e.g. repeatable-read snapshot), or explicitly restrict integrity validation and timeline counts to closed/stable sessions.
**Sources:** Codex

### [MEDIUM] `screenshotRootPath` startup validation is internally inconsistent (R1-ISSUE-009)
**Problem:** `ViewerOptions.ScreenshotRootPath` is modeled as a non-nullable required property (`default!`), but the startup-failure rule is conditional on stretch endpoints being enabled. The spec does not define what happens when stretch endpoints are disabled and the path is absent or invalid.
**Fix:** Make `ScreenshotRootPath` nullable or optional; validate it only when `EnableStretchEndpoints=true`; document both branches explicitly.
**Sources:** Fran

### [MEDIUM] Integrity validity semantics are undefined when screenshot files are missing (R1-ISSUE-010)
**Problem:** The sample integrity response shows `"valid": true` alongside a `screenshot_file_missing` warning. The spec never defines whether missing screenshots constitute a failure (changing `valid` to false), a warning only, or are conditional on ledger metadata.
**Fix:** Define an explicit taxonomy of conditions that are `failures` (set `valid=false`) vs. `warnings` (leave `valid=true`), and document the rule for screenshot-missing specifically.
**Sources:** Fran

### [MEDIUM] Mandatory/stretch split is undermined by the project scaffold and test inventory (R1-ISSUE-011)
**Problem:** The project layout, class names, and test list already fully scaffold stretch features (EntryDetailEndpoints.cs, IntegrityEndpoints.cs, ExportEndpoints.cs, EntryDetail.cshtml, PdfExportTests, ScreenshotPathTraversalTests), defeating the stated baseline-only intent of SDR-06-004.
**Fix:** Provide a minimal mandatory project shape separately, and move stretch scaffolding and tests into a clearly gated appendix or follow-on spec section.
**Sources:** Grok

### [MEDIUM] Localhost-only threat model lacks pluggable auth and boundary enumeration (R1-ISSUE-012)
**Problem:** The spec relies on process isolation with no auth, no rate limiting, and no logging, while exposing full design history and a filesystem-derived screenshot endpoint. Common vectors (ngrok, Docker port mapping, reverse proxies, shared dev machines) are not addressed, and retrofitting auth later cascades into every endpoint.
**Fix:** Design endpoints with a pluggable auth middleware stub (even if disabled by flag for hackathon). Explicitly enumerate and document the boundary conditions under which the 7879 surface is considered safe.
**Sources:** Grok

### [LOW] 3-second polling chosen over lighter real-time alternatives without adequate justification (R1-ISSUE-013)
**Problem:** The orchestrator already runs a real-time bridge on :7878. The viewer deliberately uses 3-second polling, guaranteeing up-to-3-second UI lag during active modeling sessions. The stated rationale ('no second WS endpoint') does not consider SSE, which is lighter and avoids a second WebSocket.
**Fix:** Consider SSE as a lighter alternative to polling. If polling is retained, document the latency trade-off explicitly and confirm it is acceptable for the demo use case.
**Sources:** Grok

### Coordinator Decisions
- **R1-ISSUE-001**: accepted_defect — Genuine security gap. The addinSessionId segment is interpolated into a filesystem path with no validation. Both reviewers independently identified this. Fix: add Guid.TryParse for addinSessionId, can
- **R1-ISSUE-002**: accepted_defect — The mandatory timeline page references 'the selected session' without defining selection mechanics, default behavior, query-string contract, or empty-state rendering. This blocks implementation.
- **R1-ISSUE-003**: accepted_defect — Section 13 promises truncation behavior that Section 5.3's schema cannot represent. Real contract inconsistency. Fix: add truncated, timeoutMs, lastSequenceChecked to the schema; specify a hard timeou
- **R1-ISSUE-004**: accepted_defect — Postgres-unreachable is a subset of IDhfRepository-throws, so the two failure-mode bullets are contradictory. Fix: define an exception taxonomy that distinguishes connectivity failures from data error
- **R1-ISSUE-005**: accepted_defect — Hardware-specific threshold in an automated test is non-deterministic in CI. Fix: split into a reproducible CI regression guard (generous threshold) and a manual demo-validation procedure for the GE75
- **R1-ISSUE-006**: accepted_defect — String scanning for SQL keywords does not prevent invoking write methods on an injected write-capable repository. The enforcement mechanism is architecturally inadequate. Fix: require a read-only inte
- **R1-ISSUE-007**: accepted_defect — Stretch endpoint schema references ToolCallRow[] and DecorationRow[] without any field definitions. Cannot implement or test against this contract. Fix: add the full JSON shape.
- **R1-ISSUE-008**: accepted_defect — In-process viewer making multiple sequential repository calls while the orchestrator writes is a real consistency risk, especially for integrity validation. Fix: document isolation requirements and ac
- **R1-ISSUE-009**: accepted_defect — Non-nullable property with conditional validation is a real contradiction. Fix: make ScreenshotRootPath nullable and validate only when stretch endpoints are enabled.
- **R1-ISSUE-010**: accepted_defect — The example response shows valid=true with a screenshot_file_missing warning but never defines the rule. Implementers and consumers cannot distinguish pass from fail. Fix: add explicit failure-vs-warn
- **R1-ISSUE-011**: accepted_defect — Project shape and test list intermingle mandatory and stretch items without annotation, undermining the stated SDR-06-004 split. Fix: annotate each item clearly.
- **R1-ISSUE-012**: rejected_claim — SDR-06-002 already documents the hackathon trade-off and defers to SPEC-00 SDR-TL-005 for the threat model. Adding a middleware stub is scope creep for a hackathon spec. Boundary enumeration belongs i
- **R1-ISSUE-013**: preference_only — 3-second polling for a read-only audit log in a hackathon is a reasonable trade-off. SSE would be marginally better but adds implementation surface for minimal demo benefit. The rationale is already d

## Round 2

### [HIGH] Path traversal protection has string-prefix bypass vulnerability and missing encoded/stream attack coverage (R1-ISSUE-001)
**Problem:** The canonical-path guard uses a plain string StartsWith check, which is bypassable by sibling paths sharing a prefix (e.g. C:\shots2 starts with C:\shots). Additionally, URL-encoded traversal (%2e%2e), NTFS alternate data streams, and symlink following are not addressed. Case sensitivity differs across platforms.
**Fix:** Append a trailing directory separator to the root before comparison, use OS-appropriate ordinal comparison, or use Path.GetRelativePath and reject results that are rooted or begin with '..'. Expand ScreenshotPathTraversalTests to cover encoded variants and sibling-prefix cases.
**Sources:** Grok, Codex, Fran

### [HIGH] IReadOnlyDhfRepository cannot distinguish 'session not found' from 'session with zero entries' (R1-ISSUE-002)
**Problem:** ListEntriesAsync returning an empty list is indistinguishable from a valid session with zero entries. The mandatory GET / and GET /api/dhf/entries endpoints require 404 on unknown sessionId, but the interface contract provides no mechanism to detect non-existence. Additionally, the timeline page displays 'hash chain: valid (shallow)' but no shallow-validation method is defined on the interface.
**Fix:** Add SessionExistsAsync or GetSessionSummaryAsync to IReadOnlyDhfRepository, or define typed not-found semantics (e.g. SessionNotFoundException) for ListEntriesAsync. Also add a shallow-validation status method or clarify how the timeline obtains chain validity without a separate call.
**Sources:** Codex, Grok

### [HIGH] ValidateChainAsync contract is underspecified: no signature, no partial-result semantics on timeout, no isolation guarantee (R1-ISSUE-003)
**Problem:** The spec requires ValidateChainAsync to support shallow/deep modes, an 8-second CancellationToken hard timeout with partial results (entriesChecked, lastSequenceChecked, truncated=true), and repeatable-read snapshot isolation — but no method signature, DTO contract, or cancellation-to-partial-result mechanism is defined. A CancellationToken normally throws OperationCanceledException rather than returning partial state.
**Fix:** Publish a concrete ValidateChainAsync signature including session id, mode enum, CancellationToken, and a result type that carries partial-result fields. Define explicitly how the validator accumulates and returns progress on cancellation (e.g. progress callback, incremental result object). Move isolation-level requirements into SPEC-04's interface definition.
**Sources:** Codex, Fran, Grok

### [HIGH] 503 vs 500 error mapping depends on RepositoryUnavailableException which may not exist in SPEC-04 (R1-ISSUE-004)
**Problem:** The viewer's failure-mode table requires distinguishing RepositoryUnavailableException (→503) from all other exceptions (→500), but the spec hedges that SPEC-04 may not yet define this type and offers 'wraps NpgsqlException with IsTransient=true' as a fallback — which is not a stable, testable contract surface.
**Fix:** Make RepositoryUnavailableException mandatory in SPEC-04 immediately. Remove the fallback wording. The viewer should catch only the concrete typed exception for 503 mapping.
**Sources:** Codex, Fran

### [HIGH] ACC-08 performance criterion uses manual demo validation on a specific laptop, not reproducibly testable in CI (R1-ISSUE-005)
**Problem:** ACC-08 requires < 1 second on the GE75 demo laptop measured manually. The CI regression guard uses a 3-second ceiling on unspecified hardware. The two thresholds are inconsistent and the primary criterion is not automatable.
**Fix:** Define a reproducible automated test with a fixed dataset and a single threshold. If hardware variance is a concern, document a normalization approach or use a relative baseline rather than an absolute wall-clock number.

### [HIGH] Read-only DI enforcement is unverifiable without a defined isolated viewer composition root (R1-ISSUE-006)
**Problem:** The spec claims DI-based read-only enforcement via ViewerReadOnlyDiTests, but ViewerHost receives a repo instance directly from the orchestrator's DI setup. No separate viewer service provider is defined. In a shared in-process host, write-capable services registered in the orchestrator container remain accessible, and the test has no unambiguous isolated container to verify.
**Fix:** Define an isolated viewer WebApplication with its own IServiceProvider that registers only IReadOnlyDhfRepository. Specify the adapter pattern explicitly. ViewerReadOnlyDiTests should target this isolated provider.
**Sources:** Fran

### [MEDIUM] Feature tree field representation is contradictory between JSON example and field table (R1-ISSUE-007)
**Problem:** The Section 5.2 JSON example shows featureTreeBefore as { "json": "..." } (a wrapper with a string), while the field table says the type is 'object' with notes 'SPEC-01 FeatureNode JSON'. These are two incompatible representations.
**Fix:** Choose one canonical representation — either inline FeatureNode JSON object or a named string field — and align the example, field table, repository DTO, and tests to that shape.
**Sources:** Codex, Fran

### [HIGH] Entry detail read consistency is falsely assumed atomic; multi-table reads require explicit transaction scope (R1-ISSUE-008)
**Problem:** Section 10 states 'Entry detail: Single-entry reads are atomic by nature,' but the entry-detail payload in Section 5.2 spans the entry row, tool calls, decorations, feature trees, and screenshot URLs — multiple tables. Concurrent writes can produce mixed-version payloads unless GetEntryDetailAsync explicitly uses a single consistent transaction.
**Fix:** Require GetEntryDetailAsync to materialize all entry-detail data within a single read transaction/snapshot. Remove the 'atomic by nature' claim and replace with an explicit consistency guarantee.
**Sources:** Fran

### [HIGH] screenshotRootPath with %LOCALAPPDATA% token will fail startup without explicit environment variable expansion (R1-ISSUE-009)
**Problem:** The documented appsettings.json example uses %LOCALAPPDATA%\BSVoiceCAD\screenshots. ASP.NET Core configuration binding does not expand Windows environment variable tokens. A literal Directory.Exists check on the unexpanded string will always fail, causing InvalidOperationException at startup when stretch endpoints are enabled.
**Fix:** Specify that ScreenshotRootPath is passed through Environment.ExpandEnvironmentVariables before validation, or derive the default from Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData) and require overrides to be absolute expanded paths. Update the config example accordingly.
**Sources:** Codex, Fran

### [HIGH] Viewer startup exception on invalid stretch config blocks entire orchestrator launch (R2-ISSUE-001)
**Problem:** ViewerHost.Start is called from OrchestratorProgram.Main. If EnableStretchEndpoints=true and ScreenshotRootPath is missing, the viewer throws InvalidOperationException, halting the entire orchestrator including the agent loop and modeling session. This contradicts the spec's framing of the viewer as a leaf module with no impact on core functionality.
**Fix:** Make stretch-config validation non-fatal: log a warning, disable stretch endpoints internally, and allow the orchestrator to start. Only throw on mandatory configuration failures (e.g. invalid port).
**Sources:** Grok

### [HIGH] Integrity validation ownership is contradictory: viewer vs. repository layer (R2-ISSUE-002)
**Problem:** Section 5.3 says the endpoint 'Runs HashChainValidator.ValidateShallow or ValidateDeep' (viewer-layer logic). SDR-06-003 says 'All paths go through IReadOnlyDhfRepository.' Section 10 says ValidateChainAsync must be on IReadOnlyDhfRepository. These three statements are mutually inconsistent about where validation logic lives.
**Fix:** Assign validation ownership to one layer. If the repository owns it, the viewer calls only IReadOnlyDhfRepository.ValidateChainAsync and HashChainValidator is an internal implementation detail of SPEC-04. Update all three sections to be consistent.
**Sources:** Codex

### [LOW] Integrity validity semantics are defined for missing screenshots (warning, not failure) (R1-ISSUE-010)
**Problem:** Round 1 flagged this as undefined. Section 5.3 now defines the taxonomy explicitly: screenshot file missing = warning (valid unchanged), screenshot content mismatch = failure (valid=false). This issue appears resolved in v1.1.0.
**Fix:** No action needed — resolved in current draft.

### [MEDIUM] Mandatory/stretch split is undermined by project scaffold and test inventory (R1-ISSUE-011)
**Problem:** The project scaffold lists stretch files (EntryDetailEndpoints.cs, IntegrityEndpoints.cs, ExportEndpoints.cs) alongside mandatory files without a clear build-time gate. The test inventory similarly mixes mandatory and stretch tests. A developer following the scaffold may implement stretch items believing them mandatory.
**Fix:** Either separate stretch files into a distinct folder/project gated by EnableStretchEndpoints, or add a build-time conditional (e.g. #if STRETCH or a separate .csproj) so the mandatory baseline is unambiguously isolated.

### [LOW] Localhost-only threat model lacks pluggable auth boundary for post-hackathon deployment (R1-ISSUE-012)
**Problem:** SDR-06-002 documents localhost-only with no auth and defers multi-user auth to post-hackathon. No extension point or interface is defined for adding authentication, making the post-hackathon path a structural refactor rather than a configuration change.
**Fix:** Add a note or stub (e.g. IViewerAuthPolicy) that can be a no-op for hackathon but provides a defined extension point for post-hackathon auth. Low priority for current scope.

### [LOW] 3-second polling interval chosen without documented rationale or comparison to SSE (R1-ISSUE-013)
**Problem:** Section 7 specifies 3-second polling and explicitly rejects WebSocket but does not consider Server-Sent Events (SSE), which would provide lower latency without the complexity of a second WS endpoint.
**Fix:** Low priority. If latency becomes a concern, SSE is a natural upgrade path. Document this as a known trade-off in SDR-06 or Section 15.

### Coordinator Decisions
- **R1-ISSUE-001**: accepted_defect — Residual from Round 1. Section 9 guard 3 still uses plain StartsWith without a trailing directory separator, allowing sibling-directory bypass (e.g. C:\shots2 matches C:\shots). Also missing explicit 
- **R1-ISSUE-002**: accepted_defect — Residual from Round 1. SDR-06-003 still lists only four methods; no GetSessionAsync or equivalent to distinguish 'session not found' from 'empty session'. The mandatory timeline and /api/dhf/entries r
- **R1-ISSUE-003**: accepted_defect — Residual from Round 1. No concrete method signature, result DTO, or cancellation-to-partial-result mechanism is defined for ValidateChainAsync. CancellationToken normally throws OperationCanceledExcep
- **R1-ISSUE-004**: accepted_defect — Residual from Round 1. Section 14 still contains the hedge 'If SPEC-04 does not yet define this type, it wraps NpgsqlException with IsTransient=true or TimeoutException.' This is not a stable testable
- **R1-ISSUE-005**: rejected_claim — Already resolved in v1.1.0. The dual-track approach (1s manual on GE75 for acceptance, 3s in CI for regression guard) is standard practice for hardware-specific performance criteria. The two threshold
- **R1-ISSUE-006**: accepted_defect — Residual from Round 1. The current draft shows ViewerHost receiving a repo instance from the orchestrator but does not specify an isolated IServiceProvider for the viewer's WebApplication. In a shared
- **R1-ISSUE-007**: accepted_defect — Residual from Round 1. Section 5.2 JSON example still shows featureTreeBefore as { "json": "..." } while the field table says type 'object' with 'SPEC-01 FeatureNode JSON'. Fix: align the example to s
- **R1-ISSUE-008**: accepted_defect — Residual from Round 1. Section 10 still says 'Entry detail: Single-entry reads are atomic by nature.' The entry-detail payload spans entries, tool calls, decorations, feature trees, and screenshots — 
- **R1-ISSUE-009**: accepted_defect — Residual from Round 1. Section 13 config example still uses %LOCALAPPDATA% with no expansion specification. ASP.NET Core config binding does not expand Windows env var tokens. Fix: specify Environment
- **R2-ISSUE-001**: accepted_defect — New finding. ViewerHost throwing InvalidOperationException on invalid stretch config blocks the entire orchestrator including the agent loop. This contradicts the viewer's role as a non-critical leaf 
- **R2-ISSUE-002**: accepted_defect — New finding. Genuine contradiction: Section 5.3 references HashChainValidator (viewer-layer logic), SDR-06-003 says all paths go through IReadOnlyDhfRepository, Section 10 says ValidateChainAsync is o
- **R1-ISSUE-010**: rejected_claim — Already resolved in v1.1.0. The referee confirms: 'No action needed — resolved in current draft.' Section 5.3 now defines the failure/warning taxonomy explicitly.
- **R1-ISSUE-011**: preference_only — Already addressed in v1.1.0 with [Mandatory]/[Stretch] annotations throughout and separate test sections. Build-time conditional compilation or separate .csproj is an implementation preference that ad
- **R1-ISSUE-012**: rejected_claim — Already rejected in Round 1. SDR-06-002 documents the trade-off and defers to SPEC-00 SDR-TL-005. Adding an auth stub interface is scope creep.
- **R1-ISSUE-013**: preference_only — Already classified in Round 1. 3-second polling for a hackathon read-only viewer is adequate. SSE is a valid future enhancement but not a defect.

## Open Questions

- SPEC-04 must ratify the IReadOnlyDhfRepository interface split — does it already expose a read-only surface, or must it be added?
- Should deep integrity validation be converted to an async background job (returning a jobId) rather than a synchronous endpoint with a timeout?
