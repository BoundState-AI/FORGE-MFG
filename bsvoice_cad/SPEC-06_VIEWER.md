---
title: "BSVoice-CAD SPEC-06 — Viewer"
subtitle: "Timeline rendering, DHF read API, integrity endpoints, Railway deployment, tenant-scoped reads via session capability"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
---

# BSVoice-CAD SPEC-06 — Viewer

**Version:** 1.4.0
**Status:** DRAFT — cross-pass R3 findings applied + SDR-06-008 m2 mirror patch + R4 editorial (H2, H6 fixes) + R5 H1-R rename completion
**Depends on:** SPEC-00 v1.5.0 (module boundaries including SPEC-06 → SPEC-01 display-type allowance, SDR-TL-011 deployment, SDR-TL-012 v1 no-auth), SPEC-01 v1.4.0 (`StateSnapshot`, `SelectionRef`, `FeatureNode` display types; `AddinSessionIdFormatValidator`; `ScreenshotResult` envelope), SPEC-04 v1.5.0 (`IReadOnlyDhfRepository` — strictly read-only including side-effect-free `ValidateChainAsync`, `SessionSummary`, `EntryListItem`, `EntryDetail`, `ChainValidationResult`, `RepositoryUnavailableException`, `SessionNotFoundException`), `DEPLOYMENT_V1_V2_MIGRATION.md` v1.1.0 (v1/v2 boundary, §4 viewer hosting and auth)
**Consumed by:** (none — leaf module in the other direction)

**Changelog v1.3.0 → v1.4.0:**
- **SDR-06-007 rewritten** to fix R3 R1-ISSUE-003 (anonymous enumeration). `GET /` is now a neutral landing page that does NOT auto-load any session and does NOT list sessions. Session content requires possession of a `sessionId` URL parameter. The `?tenant=X` anonymous override is REMOVED — tenant resolution happens exclusively via `sessionId` → session row lookup → session's `tenant_id`. A caller who does not know a valid sessionId cannot reach any session list or any session content.
- **§4.0 Tenant Resolution rewritten** — the `TenantResolver` no longer accepts anonymous tenant query parameters. Only two resolution paths exist in v1: (a) `sessionId` present → resolved from the session row, (b) no `sessionId` → no tenant → neutral landing page rendered (no sessions to list). v1.5 adds Clerk-authenticated resolution as path (c).
- **§4.1 GET / behavior tightened** — returns an explanation page only. Does not query the repository. Does not enumerate.
- **§4.2 GET /api/dhf/sessions rewritten** — now requires `sessionId` query parameter whose session the caller knows. Orchestrator resolves the tenant from that session and returns the tenant's other sessions for sidebar UX. 400 on missing `sessionId`, 404 if the session doesn't exist. No tenant can be enumerated by a caller who does not already possess a session capability.
- **Depends-On expanded** to acknowledge SPEC-01 import for display types (`StateSnapshot`, `SelectionRef`, `FeatureNode`). Matches SPEC-00 v1.5.0 module map. Fixes R3 R1-ISSUE-010.
- **§3a contract clarification** — `ValidateChainAsync` on `IReadOnlyDhfRepository` is strictly side-effect-free. Any integrity-event decoration persistence is a separate write path on `IDhfRepository` (not reachable from the viewer). Matches SPEC-04 v1.5.0 change. Fixes R3 R1-ISSUE-008.
- **§11 tests** extended with `NeutralLandingTests`, `SessionCapabilityGateTests`, `NoAnonymousTenantTests`, `ValidateChainSideEffectFreeTests`.
- Known sidebar-exposure trade-off (knowing one session UUID within a tenant allows viewing the tenant's other session IDs via `GET /api/dhf/sessions`) is explicitly documented as accepted v1 trade-off per SDR-TL-012 and migration doc §4.
- §13 configuration: `defaultTenantId` option removed (no longer used — anonymous tenant resolution is forbidden).
- §14 failure modes: added `MissingSessionCapability` response kind.
- **SDR-06-008 added** (editorial patch, consumer of SPEC-07 SDR-07-011) — role-token gating for raw user-input display in `VocabularyResolution` decoration payloads. Scope expanded in the v1.4.0+ mirror patch (see "Editorial v1.4.0+" block below) to cover the full `ParsedIntent` alongside the raw utterance, per SPEC-07 v1.1.1 Option A. Standard viewers see canonicalized resolution output only; elevated tenants gated by `ViewerOptions.VocabRawInputAllowed` see the full raw-input surface. Surfacing policy is documented in §2 SDR-06-008 and referenced in the §5.2 decoration contract table. No test additions required at this stage — gating behavior will be covered by `VocabDecorationRoleGateTests` when the viewer renders vocabulary decorations in v1 (currently decoration rendering is stretch per SDR-06-004).

**Editorial v1.4.0+ (SDR-06-008 scope expanded for Option A; R4 cross-pass H2 / H6 fixes; no version bump):**
- **m2 mirror** — SDR-06-008 now gates the full raw-input surface (utterance AND `ParsedIntent` fields `Action`, `Subject`, `Parameters`, `NormalizationUncertain`), matching SPEC-07 v1.1.1 SDR-07-011 Option A per Dr. Wilde's decision. Canonical resolution output (`canonical_id`, `standard_source`, `revision_year`, `citation`, `confidence`, and the human-readable canonicalized resolution summary) remains unconditionally visible to all viewer roles.
- Role token renamed to `vocab_view_raw_input` (was an utterance-only token prior to v1.4.0+ mirror). The config option was renamed to `ViewerOptions.VocabRawInputAllowed` (appsettings key: `vocabRawInputAllowed`). Tenant-level configuration migration is mechanical (rename key and value).
- §5.2 decoration contract table updated to reflect expanded gating surface.
- **R4 H2 — Entry-detail endpoint now requires sessionId capability gate.** `GET /api/dhf/entries/{entryId}` without sessionId returns 400; with sessionId that doesn't own the entry returns 404 (does not leak cross-session entry existence); with sessionId that owns the entry returns entry detail. Mirrors the §4.2 sessionId-capability gate on the sessions-list endpoint (SDR-06-007 anonymous-enumeration prevention).
- **R4 H6 — Entries endpoint response contract aligned with SPEC-04 v1.5.2 `EntryListItem`.** SPEC-04 extended the DTO with `undoOutcome`, `recordHash`, `previousHash` (all three are existing `dhf.entries` columns). The `GET /api/dhf/entries` response contract in §4.3 now matches the extended projection. Any fields in the prior contract not present in `EntryListItem` are removed.
- **R5 H1-R — `ViewerOptions.VocabRawInputAllowed` property and appsettings entry added.** The R4 rename updated SDR-06-008 prose and the §5.2 decoration-table annotation but never landed the actual class property or the `appsettings.json` config key. R5 Oracle flagged the security gate as existing "in prose only." v1.4.0+ adds the `VocabRawInputAllowed` property to `ViewerOptions` (default `false`) and the `vocabRawInputAllowed` key to §13's appsettings example. The per-tenant override mechanism is now documented in §13 alongside the v1.5 Clerk-SSO migration path.

## 0. Scope

This spec defines the read-only DHF viewer — the web page that shows what happened during a voice-and-text modeling session. It is hosted on Railway at `vcad.boundstate.ai` as part of the orchestrator service, co-hosted at the service root (`/`), not under a `/viewer` path prefix. It shares the orchestrator's ASP.NET Core process, its HTTP listener, and its database connection pool. It has no write paths. It never calls SolidWorks. It never touches the agent loop. It reads `IReadOnlyDhfRepository` (read-only projection defined in SPEC-04 §9.2) and renders HTML or JSON.

**In scope:** Minimal API endpoints and Razor Pages for the timeline (co-hosted with the orchestrator API), vanilla-JS incremental updates, JSON endpoints for DHF reads and integrity checks, stretch-only PDF export and entry-detail pages, tenant-scoped reads via session capability, v1 URL-as-capability access model with neutral landing page.

**Out of scope:** DHF schema (SPEC-04), write paths (there are none), Clerk SSO / OAuth (deferred to v1.5 per migration doc §4), session management (SPEC-04 owns it), voice or vision (SPEC-05), screenshot filesystem serving (removed in v1.3.0 — screenshots flow through entry-detail JSON), tenant resolution from anonymous input (removed in v1.4.0 — see SDR-06-007).

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
│   ├── Index.cshtml                     # [Mandatory] Neutral landing page
│   ├── Timeline.cshtml                  # [Mandatory] Session timeline (requires sessionId)
│   ├── EntryDetail.cshtml               # [Stretch]   Single-entry detail
│   └── _Layout.cshtml                   # [Mandatory]
├── wwwroot/
│   ├── app.js                           # [Mandatory] Vanilla JS, ~150 LOC
│   ├── style.css                        # [Mandatory]
│   └── favicon.ico                      # [Mandatory]
├── TenantResolver.cs                    # [Mandatory] Resolves tenantId via sessionId capability only
└── ViewModels/
    ├── LandingViewModel.cs              # [Mandatory] Neutral landing state
    ├── TimelineViewModel.cs             # [Mandatory] Session timeline state
    └── EntryDetailViewModel.cs          # [Stretch]
```

For hackathon baseline, only `[Mandatory]` files need to exist. `[Stretch]` files are created only if `EnableStretchEndpoints` is being implemented.

## 2. Design Decisions (Local SDRs)

**SDR-06-001: ASP.NET Core Minimal API + Razor Pages.** No React, no Blazor, no build step. Single orchestrator process serves both JSON endpoints and HTML pages. Rejected alternative: Blazor Server (adds SignalR dependency and state management complexity for a read-only viewer).

**SDR-06-002: Co-hosted with orchestrator on Railway at `vcad.boundstate.ai`, root path `/`.** The viewer is not a separate service, not a separate process, and not a separate host. It registers its endpoints on the same `WebApplication` that hosts the orchestrator's WebSocket endpoint and API. Railway serves both at `vcad.boundstate.ai` on the single port bound by `PORT` env var. HTTPS is terminated at the Railway edge; the process sees plain HTTP traffic. The WebSocket endpoint lives at `/ws`; viewer routes live at `/`, `/session/{sessionId}`, `/entry/{entryId}`, `/api/dhf/*`. The WebSocket and viewer route namespaces are disjoint.

Threat model: the viewer is internet-reachable. Threat surface:
- **Unauthenticated reads of tenant data** — mitigated in v1 by URL-as-capability (SDR-06-007): session URLs are unguessable UUIDs that function as bearer tokens. DHF audit content is design-activity metadata, not PHI/PII, so this model is acceptable for hackathon + early pilots. NOT acceptable for Medtronic production — v1.5 introduces Clerk SSO per migration doc §4.
- **Enumeration attacks** — mitigated in v1.4.0 by requiring a valid `sessionId` capability for any session-listing or session-content response (SDR-06-007). The `GET /` landing page does not expose session data.
- **Path traversal on entry-id params** — UUIDs are type-validated at the route layer; no filesystem serving exists in v1.3.0 or later.

**SDR-06-003: Read-only, enforced at the dependency boundary.** The viewer never writes to any table. It never calls SolidWorks. All paths go through `IReadOnlyDhfRepository` — a read-only projection of the full `IDhfRepository` defined in SPEC-04 §9.2. The viewer's isolated DI scope resolves only `IReadOnlyDhfRepository`; no write-capable repository type is registered in viewer route handlers.

Enforcement mechanisms (all must pass):
1. **Isolated DI scope**: `ViewerHost` registers only the `IReadOnlyDhfRepository` instance for endpoints under the viewer's route prefix. `IDhfRepository` is registered elsewhere in the DI container for orchestrator-internal use but is not resolvable from viewer handler scope.
2. **DI-container test** (`ViewerReadOnlyDiTests`): asserts that `IDhfRepository` and any write-capable type are not resolvable from the viewer's endpoint-handler scope.
3. **Architecture test** (`ViewerReadOnlyArchitectureTests`): NetArchTest rule — no SQL INSERT/UPDATE/DELETE in `BSVoiceCAD.Orchestrator.Viewer.*`.
4. **Interface constraint**: `IReadOnlyDhfRepository` exposes no write methods by design. Per SPEC-04 v1.5.0, `ValidateChainAsync` on `IReadOnlyDhfRepository` is strictly side-effect-free (§3a clarifies); integrity-event persistence is on `IDhfRepository` only.

**SDR-06-004: Minimal viewer is Mandatory; polish is Stretch.** The neutral landing page and session timeline (when a sessionId is provided) are baseline and cover ACC-20. Entry detail pages, integrity indicator, PDF export, and industrial-utilitarian styling are stretch. Items are annotated `[Mandatory]` or `[Stretch]` throughout this spec.

**SDR-06-005: Shallow validation on timeline load, deep validation only on demand.** Shallow hash-chain validation runs when the timeline renders for a known session by calling `IReadOnlyDhfRepository.ValidateChainAsync` with `ChainValidationMode.Shallow`. Deep validation runs only when the user hits `/api/dhf/integrity?deep=true`. Both modes are delegated entirely to the repository layer and are strictly side-effect-free from the viewer's perspective — the viewer never triggers a write through its DI scope. Per SPEC-04 v1.5.0, deep validation findings are returned in-memory only; persistence of integrity-event decorations (when desired) is the orchestrator's separate responsibility via `IDhfRepository` and is not reachable from the viewer.

**SDR-06-006: Non-fatal stretch configuration failures.** The viewer is a leaf module. A misconfigured stretch option must not prevent the orchestrator from starting the agent loop and core modeling session. If stretch configuration is invalid at startup, `ViewerHost` logs a warning via Serilog, disables stretch endpoints internally, and continues with mandatory-only endpoints.

**SDR-06-007 (v1.4.0, REWRITTEN): URL-as-capability for v1; Clerk deferred to v1.5. GET / is a neutral landing page that does NOT expose session data.** In v1, a session URL (`/?sessionId={uuid}` or `/session/{uuid}`) is the access-control mechanism. The UUIDv4 sessionId is 122 bits of entropy — unguessable by brute force, but not protected against accidental sharing (an engineer emailing a link exposes the session).

**The following behaviors are required to preserve URL-as-capability as a meaningful security property:**

1. `GET /` without a `sessionId` renders a neutral landing page only. It MUST NOT call `IReadOnlyDhfRepository` for any session, list, or metadata query. It MUST NOT auto-load the most recent session for any tenant.
2. `GET /api/dhf/sessions` without a `sessionId` query parameter returns 400. There is no endpoint in v1 that lists sessions without the caller first presenting a session UUID capability.
3. `GET /api/dhf/sessions?sessionId={uuid}` resolves the session's `tenant_id` and returns sessions scoped to that tenant — this supports the sidebar UX of switching between sessions within a tenant. The caller must already possess one valid capability to reach any other.
4. `GET /?tenant={X}` — removed. Anonymous tenant query parameters are not recognized. Requests containing them have the parameter ignored.
5. Any attempt to access content without a valid `sessionId` returns `MissingSessionCapability` (HTTP 401 for API endpoints, neutral landing page for HTML endpoints).

**Known sidebar-exposure trade-off (accepted for v1):** Once a caller holds one valid session capability, they can see other session UUIDs and metadata for the same tenant via the sidebar list. This is an intentional UX decision — engineers need to switch between sessions in their tenant. It reduces the effective entropy of URL-as-capability from 122 bits per session to 122 bits for "first session access within a tenant." v1.5 Clerk integration replaces this trade-off with tenant-role-based access control where the sidebar is filtered to the authenticated user's visible sessions.

**Not acceptable for:**
- Multi-user customer deployments where tenants must be strictly isolated
- PHI/PII data (which BSVoice-CAD does NOT record by design — transcripts describe CAD geometry, not patient data)
- Any context outside the hackathon-demo / initial-Medtronic-pilot threat model documented in SDR-TL-012

The v1.5 migration path (per migration doc §4) is Clerk SSO with per-tenant role-based access control. The transition is non-breaking: the `sessionId` URL structure survives, but unauthenticated requests are rejected by a middleware in front of the viewer endpoints. The viewer's business logic (endpoint handlers, tenant resolution, DB queries) does not change substantially — the TenantResolver gains a Clerk path.

**SDR-06-008 (v1.4.0, scope expanded in v1.4.0+ mirror patch — consumer of SPEC-07 v1.1.1 SDR-07-011): Role-token gating for raw user input in `VocabularyResolution` decorations.** The `VocabularyResolutionDecoration` payload includes raw user-input fields (raw transcribed utterance AND the full `ParsedIntent` — `Action`, `Subject`, `Parameters`, `NormalizationUncertain`) that may contain PHI, proprietary design details, or attacker-crafted text (per SPEC-07 SDR-07-010/011). Canonical resolution output (`canonical_id`, `standard_source`, `revision_year`, `citation`, `confidence`) is the vocabulary layer's *output*, not user input, and is unconditionally safe to display.

The viewer's entry-detail endpoint renders this decoration with a role gate over the raw-input surface:

- **Default (standard viewer, no role token present):** The raw-input fields are elided from the serialized response. `contentJson.utterance`, `contentJson.parsedIntent.action`, `contentJson.parsedIntent.subject`, `contentJson.parsedIntent.parameters`, and `contentJson.parsedIntent.normalizationUncertain` are all omitted. A canonicalized intent fragment derived from the decoration's resolved fields is rendered in their place (e.g., `"thread callout resolved: 6-32 UNC 2B"`). Canonical resolution output remains fully visible.
- **Elevated (`vocab_view_raw_input` role token present):** Full raw-input surface is returned in `contentJson.utterance` and `contentJson.parsedIntent`. Canonical resolution output is unchanged (always visible).

In v1 (URL-as-capability), the role token is NOT bound to a user identity — there is no authenticated subject. The token is provisioned per-tenant at deployment time as a tenant-level configuration flag (`ViewerOptions.VocabRawInputAllowed`, renamed in v1.4.0+ from the v1.4.0 utterance-only config key). Default is `false`. A tenant-specific override to `true` permits raw-input display for every viewer on that tenant's sessions. In v1.5 (Clerk SSO), the token becomes a user-role claim and gating is per-user per §4.2 of the migration doc.

The viewer never stores or caches raw user input outside the response body; redaction happens at serialization time, not at persistence time (the raw text remains in the DHF for audit-sink access per SPEC-07 SDR-07-010).

**Every response emits `X-BSVoice-Auth-Model: url-capability-v1`** to signal the access model to clients and to make future audits of this trade-off self-documenting.

## 3. Hosting

`ViewerHost` registers the viewer's endpoints and Razor Pages on the orchestrator's `WebApplication`. The orchestrator calls `viewer.RegisterEndpoints(app)` during startup, after DB migrations complete but before `app.Run()`. No separate listener, no separate port.

```csharp
public sealed class ViewerHost
{
    public ViewerHost(IReadOnlyDhfRepository repo, ViewerOptions options);
    public void RegisterEndpoints(WebApplication app);
}

public sealed class ViewerOptions
{
    public bool EnableStretchEndpoints { get; init; }
    // DefaultTenantId removed in v1.4.0 — anonymous tenant resolution is forbidden per SDR-06-007.

    // v1.4.0+ R5 H1-R — security gate for SDR-06-008 role-token display
    // of raw user-input fields in VocabularyResolution decorations
    // (raw utterance AND ParsedIntent). Default false: standard viewers
    // see canonicalized resolution output only. When true for a tenant,
    // all viewers on that tenant's sessions may see the raw input
    // surface. Per-tenant override mechanism is documented in §13.
    // Renamed in v1.4.0+ from the v1.4.0 utterance-only property
    // (scope in v1.4.0 was utterance-only). v1.4.0+ expanded scope to
    // the full raw-input surface per Dr. Wilde Option A decision.
    public bool VocabRawInputAllowed { get; init; } = false;
}
```

Port binding is the orchestrator's responsibility. Railway provides the `PORT` env var (typically 3000-9000 range). `Program.Main` configures Kestrel with `WebApplication.CreateBuilder(args).WebHost.UseUrls($"http://0.0.0.0:{port}")` where port comes from `Environment.GetEnvironmentVariable("PORT")`. TLS is terminated at Railway's edge — the ASP.NET process serves plain HTTP.

Startup validation:
- If `EnableStretchEndpoints` is `true`, no filesystem-dependent configuration needs to be present (v1.3.0 removed screenshot path dependency).
- If the orchestrator cannot bind to `PORT`, startup fails. This is fatal — the entire service is non-functional. Railway will detect this and flag the deploy as failed.

**Tenant resolution** is performed by `TenantResolver` on every request that needs a tenant scope. See §4.0 for the algorithm.

## 3a. IReadOnlyDhfRepository Required Contract Surface

This section mirrors SPEC-04 §9.2 for reference and to document the viewer's consumption boundary. SPEC-04 is authoritative for implementation.

```csharp
// Defined in SPEC-04 §9.2. Consumed by SPEC-06.
public interface IReadOnlyDhfRepository
{
    // [Mandatory] Returns sessions for the given tenant, ordered by StartedAt descending.
    // Tenant scoping is mandatory — no overload exists without tenantId.
    Task<IReadOnlyList<SessionSummary>> ListSessionsAsync(
        string tenantId,
        CancellationToken ct = default);

    // [Mandatory] Returns the session summary, or null if sessionId does not exist.
    // This is the primary tenant-resolution entry point for the viewer: caller
    // presents a sessionId capability, receives the session's tenant_id, then
    // can list the tenant's other sessions.
    Task<SessionSummary?> GetSessionAsync(
        Guid sessionId,
        CancellationToken ct = default);

    // [Mandatory] Returns entries for the given session, ordered by SequenceNumber ascending.
    // Returns an empty list if the session exists but has no entries.
    // Throws SessionNotFoundException if the session does not exist.
    Task<IReadOnlyList<EntryListItem>> ListEntriesAsync(
        Guid sessionId,
        CancellationToken ct = default);

    // [Stretch] Returns full entry detail, or null if entryId does not exist.
    // EntryDetail includes tool calls and decorations inline (SPEC-04 v1.5.0).
    // Screenshot bytes are inlined per SPEC-04 v1.5.0 §10.2.
    Task<EntryDetail?> GetEntryDetailAsync(
        Guid entryId,
        CancellationToken ct = default);

    // [Mandatory for shallow, Stretch for deep]
    // STRICTLY SIDE-EFFECT-FREE. Never writes to any table, never emits
    // decoration payloads, never triggers downstream persistence.
    // Returns findings in-memory only. If operator wants integrity findings
    // persisted, that is done via IDhfRepository (not IReadOnlyDhfRepository)
    // and is not reachable from the viewer.
    Task<ChainValidationResult> ValidateChainAsync(
        Guid sessionId,
        ChainValidationMode mode,
        CancellationToken ct = default);
}
```

Supporting record types (`SessionSummary`, `EntryListItem`, `EntryDetail`, `ChainValidationMode`, `ChainValidationResult`, `ChainValidationFinding`) are defined verbatim in SPEC-04 §9.2. Viewer code imports them from `BSVoiceCAD.Orchestrator.Dhf` namespace.

Cross-spec requirements on SPEC-04 (verified against SPEC-04 v1.5.0):
- **`RepositoryUnavailableException`** — defined in SPEC-04 §9.3. ✅
- **`SessionNotFoundException`** — defined in SPEC-04 §9.3. ✅
- **Deep validation isolation** — SPEC-04 §10.2 acquires repeatable-read snapshot. ✅
- **Entry detail materialization** — SPEC-04 §9.2 `GetEntryDetailAsync` materializes all related data (tool calls, decorations, feature trees, screenshot bytes) within a single read-committed transaction. ✅
- **ValidateChainAsync side-effect-free guarantee** — SPEC-04 v1.5.0 explicitly specifies that the `IReadOnlyDhfRepository` implementation of this method never writes. ✅ (New in R3 patch; addresses R1-ISSUE-008.)

Cross-spec import from SPEC-01 (for display types only): `StateSnapshot`, `SelectionRef`, `FeatureNode`. These appear as nested JSON objects in `EntryDetail.FeatureTreeBefore` / `FeatureTreeAfter` and in the agent-state snapshot fields of some decoration payloads. The viewer does NOT serialize or deserialize these types — it treats them as opaque JSON for rendering. NetArchTest rule permits this narrow SPEC-06 → SPEC-01 dependency, scoped to display types only.

## 4. Mandatory Endpoints

### 4.0 Tenant Resolution

`TenantResolver.ResolveAsync(HttpContext)` returns the tenantId for the request OR signals that no tenant is resolvable. Algorithm:

1. **v1.5+ (Clerk path):** read tenantId from the authenticated session token. If present, return it. (Not implemented in v1.)
2. **v1 capability path:** read `sessionId` from:
   - Route parameter `{sessionId}` if present (e.g., `/session/{sessionId}`), or
   - Query parameter `?sessionId=` if present.
   Parse the sessionId as a UUID. If parse succeeds, call `IReadOnlyDhfRepository.GetSessionAsync(sessionId)`. If the session exists, return its `TenantId`.
3. **Otherwise:** return `TenantResolutionResult.None`. The caller treats this as an unauthenticated request with no tenant scope.

```csharp
public sealed record TenantResolutionResult
{
    public string? TenantId { get; init; }         // null if no tenant could be resolved
    public Guid? SourceSessionId { get; init; }    // null in Clerk path; set in capability path
    public bool IsResolved => TenantId is not null;
}

public async Task<TenantResolutionResult> ResolveAsync(HttpContext ctx)
{
    // v1 capability path
    var sessionIdStr = ctx.Request.RouteValues["sessionId"] as string
                    ?? ctx.Request.Query["sessionId"].FirstOrDefault();

    if (Guid.TryParse(sessionIdStr, out var sessionGuid))
    {
        var session = await _repo.GetSessionAsync(sessionGuid, ctx.RequestAborted);
        if (session is not null)
        {
            return new TenantResolutionResult
            {
                TenantId = session.TenantId,
                SourceSessionId = session.SessionId
            };
        }
    }

    // No capability presented, or session not found. No tenant resolved.
    return new TenantResolutionResult();
}
```

**Removed in v1.4.0:**
- The `?tenant=X` anonymous override. Tenant is never resolvable from unverified input.
- The `DefaultTenantId` fallback. No tenant is assumed when no capability is presented.

**Consequences:**
- A request with no `sessionId` is an unauthenticated request. It receives the neutral landing page or a 401-style response depending on the endpoint.
- A request with an invalid `sessionId` (malformed UUID or unknown session) is also unauthenticated. Same response.
- Enumeration attacks fail: a caller cannot brute-force session UUIDs because the UUID space is 122 bits; a caller cannot list a tenant's sessions without first knowing a valid session in that tenant.

### 4.1 GET /

Razor page. Renders the **neutral landing page**. Does NOT call `IReadOnlyDhfRepository`. Does NOT list sessions. Does NOT resolve a tenant.

Landing page content:
- Product title and brief description
- Explanation of URL-as-capability: "To view a session, navigate to the session URL shared with you (e.g., `/session/{sessionId}`)"
- Placeholder "Sign in to see your sessions" — non-functional in v1; becomes Clerk CTA in v1.5
- Link to `https://www.boundstate.ai` for prospective users to learn more

This endpoint is **safe to expose publicly**. It does not require a tenant scope, does not reveal session IDs, and does not query any tenant-scoped data.

### 4.1a GET /?sessionId={uuid} and GET /session/{uuid}

Razor page. Renders the **session timeline** for the specified session. `TenantResolver` resolves the tenant from the sessionId (§4.0). If the sessionId is missing, malformed, or does not resolve to an existing session, returns one of:

- Missing `sessionId`: redirect to `GET /` (the landing page).
- Malformed sessionId: 400 with a brief "invalid session URL" message.
- Unknown sessionId (well-formed UUID, no session row): 404 with a brief "session not found" message — this reveals no information about which tenants exist, because the response is identical for "session was deleted," "session never existed," and "session belongs to a tenant you cannot access."

If the sessionId resolves successfully, the `TimelineViewModel` is populated:
1. `IReadOnlyDhfRepository.GetSessionAsync(sessionId)` → selected session (already resolved by TenantResolver, can be reused)
2. `IReadOnlyDhfRepository.ListSessionsAsync(tenantId)` → session list sidebar (sibling sessions for UX)
3. `IReadOnlyDhfRepository.ListEntriesAsync(sessionId)` → entries for the timeline
4. `IReadOnlyDhfRepository.ValidateChainAsync(sessionId, ChainValidationMode.Shallow)` → shallow chain validity for the header display

The sidebar of sibling sessions is the documented sidebar-exposure trade-off (SDR-06-007): knowing one session UUID reveals other session UUIDs for the same tenant. Accepted in v1 under the SDR-TL-012 threat model.

Must render within 1 second for a 20-entry session on the demo laptop connecting to Railway. ACC-20 measures this (see §12).

### 4.2 GET /api/dhf/sessions?sessionId={uuid}

JSON list of session summaries for the tenant that owns the provided `sessionId`. Used by vanilla-JS `app.js` to poll for new sessions within the user's current tenant without full page reload.

**Input contract:**
- Requires `sessionId` query parameter (UUID format).
- No other tenant-resolution paths. Requests without `sessionId` return 400.
- Requests with an unknown `sessionId` return 404 (matching the timeline page behavior — reveals no tenant information).

**Tenant resolution:** via `TenantResolver.ResolveAsync` (§4.0) against the `sessionId` parameter. The resolved `tenantId` is then passed to `ListSessionsAsync`.

**Response:**

```json
[
  {
    "sessionId": "...",
    "startedAt": "2026-04-21T13:00:00Z",
    "endedAt": null,
    "userDisplayName": "Joe Wilde",
    "workstationId": "WILDE-GE75",
    "tenantId": "...",
    "addinSessionId": "...",
    "entryCount": 7
  }
]
```

**Error responses:**
- 400 if `sessionId` is missing or malformed
- 404 if `sessionId` does not resolve to a session (identical behavior regardless of whether the session was never created, was deleted, or belongs to a different tenant — the response body contains no tenant information)
- 401 if the response envelope is constructed from `TenantResolutionResult` with `IsResolved = false` (should be equivalent to 404 in practice but available for future OAuth-path differentiation)

### 4.3 GET /api/dhf/entries?sessionId={uuid}

JSON list of entries for a session, ordered by `sequence_number` ascending.

The endpoint calls `GetSessionAsync(sessionId)` first to verify the session exists. If null, returns 404. Then calls `ListEntriesAsync(sessionId)` to retrieve entries.

**AddinSessionId validation (when present in query):** If any endpoint takes an `addinSessionId` parameter, it is validated using `AddinSessionIdFormatValidator` from SPEC-01 §15 — the hex regex `^[0-9a-f]{32}$` matches the contract defined there. `Guid.TryParse` is NOT used because AddinSessionId is NOT a UUID (it's a 32-char lowercase hex string per SPEC-01 §9).

Response schema matches SPEC-04 v1.5.2 `EntryListItem` exactly (H6 fix). The projection carries every field documented below — no more, no less — so the endpoint is implementable directly from `IReadOnlyDhfRepository.ListEntriesAsync` without undeclared reads.

```json
[
  {
    "entryId": "...",
    "sequenceNumber": 1,
    "timestamp": "...",
    "inputModality": "Voice",
    "turnType": "Execution",
    "rawTranscript": "Make a 100 by 50 by 20 plate",
    "toolCallCount": 5,
    "hasDecorations": true,
    "undoOutcome": "Committed",
    "recordHash": "3f2a…",
    "previousHash": null
  }
]
```

Notes on specific fields:
- `turnType` may be `"Recovery"` (SPEC-04 v1.5.0 `TurnType.Recovery`); recovery-type entries have `toolCallCount == 0` and `rawTranscript` is a machine-generated summary (SPEC-04 DHF-002).
- `undoOutcome` may be null for non-execution turn types (clarification, confirmation, ambiguity_resolution, recovery).
- `recordHash` is lowercase hex, always populated. `previousHash` is lowercase hex for non-first entries; `null` for the first entry in the chain (SPEC-04 v1.5.2 §9.2 projection maps the stored `'GENESIS'` sentinel to null at the viewer boundary).

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

Razor page showing full detail for one entry: transcript, tool calls in order with inputs and results expanded, feature tree before/after (folded), screenshot thumbnails (rendered from inline base64 data URLs — see §5.2), decorations, hash-chain status indicator.

Tenant resolution: the endpoint internally resolves the entry's session, then the session's tenant. A caller must have arrived at this URL from a context that already holds the session's capability (the entry ID alone does not grant access; the session sidebar hides entries from other sessions).

### 5.2 GET /api/dhf/entries/{entryId}?sessionId={uuid}

Full JSON detail for a single entry. Used by the entry detail page.

**SessionId capability gate (v1.4.0+ R4 H2 fix).** This endpoint requires the `sessionId` query parameter whose session owns the requested `entryId`. This mirrors the §4.2 gate on the sessions-list endpoint and closes the enumeration gap flagged in R4 cross-pass Oracle H5.

| Request | Response |
|---|---|
| No `sessionId` query parameter | **400 BadRequest** — `{"error": "missing sessionId"}` |
| `sessionId` not a valid UUID | **400 BadRequest** — `{"error": "invalid sessionId"}` |
| `sessionId` valid but does not own `entryId` (either session doesn't exist, or entry belongs to a different session) | **404 NotFound** — `{"error": "entry not found"}`. Body is byte-identical whether the entry exists in another session or not, so cross-session entry existence cannot be inferred. |
| `sessionId` valid and owns `entryId` | **200 OK** with full entry detail JSON |

The endpoint's implementation MUST verify `entry.SessionId == sessionId` at the repository layer before returning data. Sidebar-link-based access control (prior v1.3.0 approach) is insufficient because any caller who obtains an entryId via log exfiltration or bookmark leak could bypass the sidebar.

Screenshots are inlined as data URLs (v1) or S3 presigned URLs (v2). The viewer does NOT serve a separate screenshot endpoint (v1.3.0 removed that path). The JSON response includes the full base64 screenshot bytes when present; browsers render them directly via `<img src="data:image/png;base64,..." />`.

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
  "featureTreeBefore": { "...FeatureNode JSON..." },
  "featureTreeAfter": { "...FeatureNode JSON..." },
  "screenshotBefore": {
    "sha256": "...",
    "dataUrl": "data:image/png;base64,iVBORw0KGgo..."
  },
  "screenshotAfter": {
    "sha256": "...",
    "dataUrl": "data:image/png;base64,iVBORw0KGgo..."
  },
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
| `turnType` | string | no | Enum: `"Execution"`, `"Clarification"`, `"Confirmation"`, `"AmbiguityResolution"`, `"Recovery"` (new in SPEC-04 v1.5.0) |
| `undoOutcome` | string | yes | Enum: `"Committed"`, `"RolledBack"`, `"Abandoned"`, or null if not applicable |
| `recordHash` | string | no | SHA-256 hex |
| `previousHash` | string | no | SHA-256 hex or `"GENESIS"` |
| `featureTreeBefore` | object | yes | Inline SPEC-01 FeatureNode JSON object; null if not captured |
| `featureTreeAfter` | object | yes | Inline SPEC-01 FeatureNode JSON object; null if not captured |
| `screenshotBefore` | object | yes | `{sha256, dataUrl}`; null if no screenshot |
| `screenshotBefore.sha256` | string | no (when parent present) | Hex SHA-256 of PNG bytes (pre-encode) |
| `screenshotBefore.dataUrl` | string | no (when parent present) | `data:image/png;base64,...` (v1) or `https://...` presigned (v2) |
| `screenshotAfter` | object | yes | Same shape as `screenshotBefore` |
| `recoveryContext` | object | yes | New in SPEC-04 v1.5.0. Present only when `turnType = "Recovery"`. Contains structured recovery fields (trigger, strategy, success, failureReason, modelStateDivergenceDescription) |
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
| `decorations[].decorationType` | string | no | Enum: `"Rationale"`, `"AmbiguityContext"`, `"IntegrityEvent"`, `"VocabularyResolution"` (SPEC-07) |
| `decorations[].contentJson` | object | no | Type-specific payload. For `VocabularyResolution`, both `contentJson.utterance` AND `contentJson.parsedIntent` (Action, Subject, Parameters, NormalizationUncertain) are role-gated per SDR-06-008 — elided for standard viewers, returned only when `ViewerOptions.VocabRawInputAllowed` is true for the session's tenant. Canonical resolution output (`canonical_id`, `standard_source`, `revision_year`, `citation`, `confidence`) is always visible. |
| `decorations[].createdAt` | ISO 8601 | no | UTC |

Error responses: 400 (invalid entryId format), 404 (entry not found).

**Response size note:** A single entry with two screenshots at the 2 MiB base64 cap each is ~4 MiB plus feature tree JSON and tool-call payloads. This is acceptable for the entry-detail endpoint (user explicitly requested one entry). The timeline page (`GET /session/{uuid}`) does NOT include screenshots — only entry summaries.

### 5.3 GET /api/dhf/integrity?sessionId={uuid}&deep=false

Calls `IReadOnlyDhfRepository.ValidateChainAsync` with `ChainValidationMode.Shallow` (default) or `ChainValidationMode.Deep` (when `deep=true`). The viewer contains no validation logic; it delegates entirely to the repository layer and serializes the returned `ChainValidationResult`.

**Side-effect-free contract:** Per SPEC-04 v1.5.0 and SDR-06-005, `ValidateChainAsync` on the read-only interface does not write any decorations or any other rows. The endpoint's response reflects findings in-memory only. If the operator wishes to persist integrity findings, they use a separate endpoint backed by `IDhfRepository.RecordIntegrityEventAsync` (not exposed through the viewer).

**Timeout:** The endpoint creates a `CancellationTokenSource` with an 8-second timeout and passes the token to `ValidateChainAsync`. Per the contract, the repository method returns a partial `ChainValidationResult` with `Truncated=true` on cancellation rather than throwing.

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
  "warnings": []
}
```

**Failure vs. warning taxonomy:**

| Condition | Classification | Effect on `valid` |
|-----------|---------------|--------------------|
| Hash-chain break (record hash mismatch) | `failure` | `valid = false` |
| Previous-hash link mismatch | `failure` | `valid = false` |
| Feature-tree JSONB hash mismatch (deep only) | `failure` | `valid = false` |
| Screenshot SHA-256 mismatch (deep, bytes exist but content differs) | `failure` | `valid = false` |
| Screenshot bytes missing (deep only — post-cleanup) | `warning` | `valid` unchanged |
| Decoration content hash mismatch | `failure` | `valid = false` |

**Semantics when `truncated=true`:** `valid` reflects only the entries actually checked (up to `lastSequenceChecked`). The client must treat a truncated result as inconclusive for entries beyond `lastSequenceChecked`.

### 5.4 POST /api/dhf/export/pdf?sessionId={uuid}

QuestPDF-based PDF generation. Streams the PDF as the response body. Content-Type `application/pdf`. The PDF includes: session metadata header, per-entry transcript and tool-call summary, hash chain verification status, full tool-call inputs/outputs in appendix. Screenshots embedded inline where present (decoded from the base64 payload in `EntryDetail`).

## 6. Timeline Page Layout

Single-column layout. Industrial-utilitarian aesthetic (monospace headers, grid layout, minimal chrome). Rough markup:

```
┌──────────────────────────────────────────────────────────────────┐
│ BSVoice-CAD Design Audit Log                      [●] Running    │
├──────────────────────────────────────────────────────────────────┤
│ Session: 2026-04-21 13:00 · Joe Wilde · WILDE-GE75               │
│ Tenant: (tenant name) · 7 entries · hash chain: valid (shallow)  │
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
- Recovery (new turn type per SPEC-04 v1.5.0): subtle purple

## 7. JavaScript (app.js)

Vanilla JS. No framework. When the user is on the timeline page (i.e., they already hold a sessionId capability), polls `/api/dhf/sessions?sessionId={currentSessionId}` every 3 seconds to refresh the sibling-sessions sidebar and detect new entries on the current session. When a new session appears in the tenant or the current session gains entries, inserts them into the DOM without full page reload. ~150 LOC total.

Specifically no WebSocket to the viewer — the WebSocket at `wss://vcad.boundstate.ai/ws` is the add-in bridge (SPEC-01 §12, SPEC-02 §6), not a viewer push channel. Polling is sufficient for hackathon scope and avoids introducing a second WS endpoint or multiplexing viewer traffic over the add-in bridge.

The landing page (`GET /`) does NOT include `app.js` or any JavaScript that polls endpoints — the landing page is fully static.

## 8. Static Assets

`wwwroot/style.css` applies the industrial-utilitarian look: monospace headers (`ui-monospace, SF Mono, Consolas, monospace`), body in a legible sans (`system-ui`), grid-based layout, subtle 1px borders, color-coded rows. Dark-mode variant via `@media (prefers-color-scheme: dark)` so the demo video looks good regardless of the judges' display settings.

No CDN dependencies. No Google Fonts. All assets are local, served from the orchestrator's static files middleware.

## 9. (Reserved)

Section 9 in v1.2.0 defined filesystem-based screenshot serving. Removed in v1.3.0 because screenshots are no longer stored on a local filesystem — they are inlined in DHF as base64 bytes (v1) or referenced via S3 presigned URLs (v2). The entry-detail JSON response (§5.2) carries screenshot content directly; no separate static endpoint exists.

## 10. Read Consistency

The viewer runs in-process with the orchestrator, which may be actively appending DHF entries during a modeling session. The viewer tolerates eventual consistency:

- **Timeline page and entry-list endpoints:** Each call to `IReadOnlyDhfRepository` uses the database's default read-committed isolation. The session list and entry list are fetched in separate calls; the entry count in the session list may briefly lag behind the actual entries returned. This is acceptable — the next 3-second poll corrects it.
- **Integrity validation:** Shallow validation reads the hash chain in a single query ordered by `sequence_number`. If a new entry is appended between validation start and finish, the validator operates on a consistent prefix. Deep validation must acquire a repeatable-read snapshot for the session's entries to prevent mid-validation mutations from causing false tamper reports. This is a SPEC-04 requirement (§10.2).
- **Entry detail:** `GetEntryDetailAsync` must materialize all related data (entry row, tool calls, decorations, feature trees, screenshot bytes) within a single read-committed transaction. This is a SPEC-04 requirement (§9.2), not a viewer-layer concern.

## 11. Required Tests

All in `BSVoiceCAD.Orchestrator.Tests` using `WebApplicationFactory<T>` for in-process API testing. No SolidWorks dependency; these tests can run on any OS with .NET 8.

### Mandatory Tests

- `NeutralLandingTests` (NEW v1.4.0) — GET / returns 200 with neutral landing HTML; does NOT include any `sessionId` values; does NOT call `IReadOnlyDhfRepository` (verified via mock that asserts zero calls); does NOT resolve a tenant (verified via TenantResolver logging); matches even when there are many sessions in the database
- `SessionCapabilityGateTests` (NEW v1.4.0) — GET /api/dhf/sessions without `sessionId` parameter returns 400; GET /api/dhf/sessions?sessionId=malformed returns 400; GET /api/dhf/sessions?sessionId=valid-but-unknown returns 404; GET /api/dhf/sessions?sessionId=valid-and-known returns the session's tenant's session list; response bodies for 404 and "session in a different tenant" are byte-identical (no tenant-existence inference possible)
- `NoAnonymousTenantTests` (NEW v1.4.0) — GET /?tenant=X and GET /api/dhf/sessions?tenant=X ignore the tenant query parameter; behavior is equivalent to the request without the parameter; no response body contains data scoped to the claimed tenant
- `ValidateChainSideEffectFreeTests` (NEW v1.4.0) — call GET /api/dhf/integrity?sessionId=X&deep=true against a seeded session with a deliberately tampered entry; verify a failure is reported; verify no new rows in `dhf.entry_decorations` were created as a result of the validation call; verify the repository's write-capable `IDhfRepository` methods were never invoked (via DI scope inspection)
- `TimelineRenderTests` — GET /session/{valid} renders correct session; GET /session/{malformed} returns 400; GET /session/{nonexistent-valid-uuid} returns 404; GET /session/{valid} with no entries renders empty timeline state (not neutral landing)
- `SessionsEndpointTests` — GET /api/dhf/sessions?sessionId={valid} returns well-formed JSON for the resolved tenant, correct ordering (most recent first); includes the caller's own session in the list; does not include sessions from other tenants
- `EntriesEndpointTests` — GET /api/dhf/entries returns entries in sequence_number ascending, 404 on unknown sessionId, 400 on missing sessionId
- `ViewerReadOnlyDiTests` — Resolves endpoint-handler scope; asserts `IDhfRepository` (write-capable) is not resolvable; asserts only `IReadOnlyDhfRepository` is registered in the viewer's scope
- `ViewerReadOnlyArchitectureTests` — NetArchTest rule: no SQL INSERT/UPDATE/DELETE in `BSVoiceCAD.Orchestrator.Viewer.*`
- `TimelinePerformanceTests` — CI regression guard: 20-entry seeded session renders within 3 seconds on any CI runner (generous ceiling); see ACC-20 below for demo-specific validation
- `AddinSessionIdHexRegexTests` (v1.3.0) — any endpoint that accepts an `addinSessionId` parameter validates via `AddinSessionIdFormatValidator.IsValid(id)` which enforces `^[0-9a-f]{32}$`. Asserts: uppercase hex rejected; UUID-dash format rejected; length <32 rejected; length >32 rejected; 32-char lowercase hex accepted
- `NoLocalhostReferenceTests` (v1.3.0) — architecture test: grep the viewer source for hardcoded `127.0.0.1`, `localhost:`, or `:7879`; assert zero matches
- `HeaderEmissionTests` (v1.3.0) — every response from viewer endpoints includes `X-BSVoice-Auth-Model: url-capability-v1` header

### Stretch Tests

- `EntryDetailEndpointTests` — GET /api/dhf/entries/{entryId}?sessionId={uuid} returns well-formed JSON matching schema in §5.2; 404 on unknown; 400 on invalid UUID; featureTreeBefore/After are inline FeatureNode objects (not wrapper objects); screenshot fields have `{sha256, dataUrl}` shape; recovery-type entries include `recoveryContext` object
- `EntryDetailSessionCapabilityGateTests` (NEW v1.4.0+ R4 H2) — GET /api/dhf/entries/{entryId} WITHOUT sessionId parameter returns 400; GET /api/dhf/entries/{entryId}?sessionId=malformed returns 400; GET /api/dhf/entries/{entryId}?sessionId=valid-but-unknown returns 404; GET /api/dhf/entries/{entryId}?sessionId=valid-but-different-session returns 404 with body byte-identical to the unknown-session case (no cross-session entry existence inference); GET /api/dhf/entries/{entryId}?sessionId=valid-and-owns-entry returns 200 with entry detail. Parallel to `SessionCapabilityGateTests` (§4.2) and required for the R4 enumeration-gap closure.
- `ScreenshotInlineRenderTests` (v1.3.0) — seed an entry with a screenshot; GET /api/dhf/entries/{id} returns `screenshotBefore.dataUrl` as valid `data:image/png;base64,...` prefix; decoded bytes hash to `screenshotBefore.sha256`; no separate screenshot endpoint is exposed (asserts 404 on any attempted `/screenshots/...` path)
- `IntegrityEndpointTests` — shallow and deep modes; tamper detection (seed a modified row, assert failure surfaces); truncation behavior (mock slow validation, assert `truncated=true` and `lastSequenceChecked` populated); endpoint delegates to `IReadOnlyDhfRepository.ValidateChainAsync` (no viewer-layer validation logic); verifies side-effect-free contract (no new decoration rows after validation call)
- `PdfExportTests` — QuestPDF export produces valid PDF, content-type correct, includes screenshots decoded from base64

## 12. Acceptance Tests (exposed to SPEC-00 ACC table)

| ID | Criterion | Method |
|----|-----------|--------|
| ACC-20 | Viewer timeline renders a 20-entry session in under 1 second when accessed via a valid sessionId capability | Manual demo validation on GE75 connecting to Railway (see below) |

**ACC-20 measurement methodology:**
- **Dataset:** 20 DHF entries seeded into the Railway-managed Postgres, each with 3–5 tool calls and representative transcript lengths. Session is assigned to a tenant created for testing.
- **Measurement:** Wall-clock time from HTTP request initiation (to `https://vcad.boundstate.ai/session/{sessionId}` with the full UUID) to full HTML response received, measured via browser DevTools network tab. Network latency from GE75 to Railway edge is included in the measurement — this is the real demo condition.
- **Warmup:** Two discarded requests before measurement to eliminate TLS handshake cold-start, Railway container cold-start, and EF Core / Dapper JIT warmup.
- **Demo validation:** Performed manually on the GE75 demo laptop over whatever network the demo is occurring on (expected: hotel Wi-Fi or T-Mobile 5G). Threshold: < 1 second.
- **CI regression guard:** `TimelinePerformanceTests` runs on every build with a 3-second ceiling to catch gross regressions without false-failing on slow CI hardware.

That's the only ACC this spec owns. All other DHF-related ACCs (hash chain integrity, tamper detection) live in SPEC-04.

## 13. Configuration

Viewer configuration lives in orchestrator `appsettings.json`:

```json
{
  "viewer": {
    "enableStretchEndpoints": true,
    "vocabRawInputAllowed": false
  }
}
```

`vocabRawInputAllowed` (v1.4.0+ R5 H1-R, default `false`) is the security gate for SDR-06-008 role-token display of raw user-input fields in `VocabularyResolution` decorations. Renamed from `vocabRawUtteranceAllowed` when SDR-06-008's scope was expanded from utterance-only to the full `ParsedIntent` surface (Dr. Wilde Option A). Per-tenant override mechanism: in v1, a single global boolean scopes the policy across all tenants served by this viewer host; v1.5 introduces a tenant-keyed policy source (Clerk-authenticated per §4.2 of the migration doc) where the flag resolves per-user-per-tenant from role claims rather than from a config constant.

Port binding is NOT in this configuration — it's the orchestrator-wide `PORT` env var supplied by Railway. Screenshot root path is NOT in this configuration — removed in v1.3.0. `defaultTenantId` is NOT in this configuration — removed in v1.4.0 (anonymous tenant resolution forbidden per SDR-06-007).

Environment variables:
- `PORT` — Railway-supplied, read by orchestrator startup. Viewer shares this port.

## 14. Failure Modes

The viewer distinguishes connectivity failures, missing capability, and data errors using the exception/state taxonomy below:

| Exception / State | HTTP Status | HTML Behavior | JSON Behavior |
|-------------------|-------------|---------------|---------------|
| `RepositoryUnavailableException` (connection timeout, Postgres unreachable, connection pool exhausted) | 503 | Renders page with degraded-state banner: "Design audit log temporarily unavailable. Retrying…" | Returns `{ "error": "service_unavailable", "message": "Database is temporarily unreachable" }` |
| `SessionNotFoundException` (from `ListEntriesAsync` when session does not exist) | 404 | Renders "session not found" message; landing sidebar link visible | Returns `{ "error": "session_not_found", "sessionId": "..." }` |
| Missing session capability (no `sessionId` param, or malformed, or unknown) on endpoints that require one | 400 / 401 / 404 (see §4.2) | Redirects to `/` landing for HTML; landing page if session was in another tenant for HTML | Returns `{ "error": "missing_session_capability" }` or `{ "error": "session_not_found" }` (indistinguishable between different-tenant-session and nonexistent-session) |
| All other `IReadOnlyDhfRepository` exceptions (query errors, deserialization failures, constraint violations) | 500 | Renders generic error page: "An unexpected error occurred." | Returns `{ "error": "internal_error", "message": "An unexpected error occurred" }` |

In all cases:
- Full stack traces go to the Serilog file sink. The viewer never exposes database errors or stack traces to the user.
- `RepositoryUnavailableException` and `SessionNotFoundException` are defined in SPEC-04 §9.3 and thrown by the repository implementation. This is a mandatory cross-spec requirement.
- Response bodies are carefully constructed to NOT reveal tenant existence. A 404 for "session never existed," "session was deleted," and "session belongs to tenant other than the one resolvable from your capabilities" is byte-identical.

**Deep integrity validation timeout:** Handled by the 8-second `CancellationToken` described in §5.3. The repository returns a partial `ChainValidationResult` with `Truncated=true`; the endpoint does not return 500 or 503 on timeout.

## 15. Out of Scope

- Write paths (there are none in this spec by design)
- Multi-user session filtering beyond same-tenant sibling listing (post-hackathon — v1.5 with Clerk)
- Real-time push (polling is adequate for hackathon)
- Authentication / authorization (v1 URL-as-capability per SDR-06-007; v1.5 Clerk per migration doc §4)
- Any SolidWorks interaction
- Any agent loop logic
- DHF writing (SPEC-04 owns that)
- Protocol versioning (SPEC-01 owns that)
- Hash-chain validation logic (SPEC-04 owns implementation; viewer only delegates, strictly read-only)
- Filesystem-based screenshot serving (removed in v1.3.0 — §9 deleted)
- Anonymous tenant resolution (removed in v1.4.0 — §4.0 SDR-06-007)

---

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC. Code ships under MIT.*
