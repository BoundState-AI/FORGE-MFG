---
title: "BSVoice-CAD — Deployment v1 → v2 Migration"
subtitle: "Single source of truth for every v1/v2 deployment boundary"
author: "Dr. Joseph Wilde / Beth (Claude Opus 4.7)"
date: "2026-04-18"
version: "1.1.0"
status: "Locked (governs all SPEC-NN deployment-coupled sections)"
---

# BSVoice-CAD — Deployment v1 → v2 Migration

**Changelog v1.0.0 → v1.1.0:**
- §1 deployment matrix: viewer host path corrected from `/viewer` to `/` to match SPEC-06 §4.1 canonical routing (fixes R3 R1-ISSUE-009).
- §2 transport: handshake-failure transport clarified. Pre-upgrade failures (auth failure, format error) use HTTP 400/401 on the WebSocket upgrade response per SPEC-01 §12.4 — NOT a WebSocket close code, because a Close frame cannot be sent before the upgrade completes. WebSocket close code 4401 is reserved exclusively for post-upgrade credential revocation (fixes R3 R1-ISSUE-005).
- §4 viewer hosting: access-control posture tightened. `GET /` is a neutral landing page; it does NOT auto-load the most recent session. Session enumeration endpoints (`/api/dhf/sessions`) require a valid `sessionId` capability to resolve tenant scope. Anonymous `?tenant=X` query override is removed. This aligns the document with SPEC-06 v1.4.0 fixes for R3 R1-ISSUE-003.
- No other substantive changes. SPEC authors must continue to reference this document by section rather than redefining v1/v2 locally.

## 0. Why This Document Exists

BSVoice-CAD is intentionally built to run in two deployment modes:

- **v1 (hackathon + initial Medtronic look):** Railway-hosted orchestrator, minimal auth surface, base64 screenshots, public viewer URL.
- **v2 (Medtronic production / regulated customer rollout):** Tenant-isolated architecture with OAuth device flow, S3 presigned URLs for binary artifacts, Clerk-backed viewer auth, per-tenant Postgres schemas.

Every spec that touches a deployment-coupled decision (transport, auth, storage, hosting) carries a short "Deployment: v1/v2 boundary" subsection that points here. This document is the single source of truth for what changes, what stays, and what a ProtocolVersion bump triggers. **Specs must not redefine v1/v2 semantics locally.** If a spec needs a v2 decision this document doesn't cover, update this document first, then reference it from the spec.

The document's existence is itself a non-negotiable design constraint: **no rewrites between v1 and v2.** Every v1 component has a documented evolution path to v2 that does not require throwing away code. Where a change requires a breaking wire-contract change, the `ProtocolVersion` bump is called out explicitly.

## 1. Deployment Matrix

| Dimension | v1 (Hackathon / Initial Medtronic) | v2 (Medtronic Production) |
|-----------|-----------------------------------|---------------------------|
| Orchestrator host | Railway (`vcad.boundstate.ai`) | Railway or AWS (TBD — Medtronic infra policy) |
| Viewer host | Same Railway service, co-hosted at root path `/` | Same service; may split if Medtronic requires air-gapped viewer |
| Add-in host | Engineer's Windows workstation (local SolidWorks) | Unchanged — add-in is always local |
| WebSocket URL | `wss://vcad.boundstate.ai/ws` | `wss://{tenant}.vcad.boundstate.ai/ws` or `wss://vcad-{customer}.boundstate.ai/ws` |
| Viewer URL | `https://vcad.boundstate.ai/` | Same shape; possibly behind customer VPN |
| WebSocket auth | API key in `Sec-WebSocket-Protocol` subprotocol header | OAuth 2.0 device flow with refresh tokens |
| Viewer auth | **None** — URL-as-capability (session UUIDv4); neutral landing at `/` | Clerk SSO, tenant-scoped |
| Postgres | Single Railway-managed Postgres 16 instance | Per-tenant isolation: separate schemas or separate databases per customer |
| Screenshots | Base64-inline in `ScreenshotResult`, 2 MiB cap post-encode | S3 presigned URL (PUT/GET), SHA-256 verified |
| Feature tree transport | `IsFeatureTreeTruncated` + on-demand `get_feature_tree` pagination | Unchanged |
| Secrets | Railway env vars | Railway env vars + AWS Secrets Manager for per-tenant config |
| DNS | `vcad.boundstate.ai` (CNAME → Railway) | `{tenant}.vcad.boundstate.ai` wildcard, per-tenant certs |
| TLS | Railway edge termination | Unchanged (Railway edge) or customer-managed cert if air-gapped |

**Note on viewer path:** The viewer is co-hosted in the same ASP.NET Core process as the orchestrator's WebSocket endpoint and HTTP API, per SPEC-06 SDR-06-002. Viewer endpoints mount at the service root (`GET /` for the landing page, `GET /session/{sessionId}` for session detail, `GET /api/dhf/*` for JSON endpoints). There is no `/viewer` path prefix. The WebSocket endpoint lives at `/ws` and is disjoint from viewer routes.

## 2. Transport — WebSocket

### v1 Contract

- URL: `wss://vcad.boundstate.ai/ws`
- TLS: terminated at Railway edge; orchestrator speaks plain WS internally
- Framing: unchanged from SPEC-01 §3 (one JSON doc per reassembled WebSocket message, 4 MiB cap, drain-and-discard on oversize)
- Auth: API key passed as a subprotocol in the WebSocket handshake — `Sec-WebSocket-Protocol: bsvoice-cad-v1, apikey-{keyId}.{secret}`. The orchestrator parses the second subprotocol value, looks up `keyId` in the `api_keys` table, and validates `secret` via constant-time comparison.
- **Handshake failure (pre-upgrade):** Failures detected during the HTTP upgrade request — missing or malformed subprotocol, unknown `keyId`, failed secret verification, rate-limit exceeded — are returned as HTTP responses on the upgrade request itself. The WebSocket upgrade is rejected at the HTTP layer; no WebSocket connection is established; no Close frame is sent (because none can be sent before upgrade completes). Per SPEC-01 §12.4:
  - `400 Bad Request` — malformed subprotocol format
  - `401 Unauthorized` — unknown `keyId`, failed secret verification, or API key revoked
  - `429 Too Many Requests` — rate limit exceeded
  SPEC-01 §12.4 is authoritative for this mapping.
- **Post-upgrade credential revocation:** The orchestrator MAY close an established WebSocket with custom close code `4401` if an API key is revoked while a connection is live (e.g., admin operator disables the key via the `api_keys` table). Close code 4401 is reserved exclusively for this post-upgrade revocation scenario. The add-in treats 4401 as terminal — it transitions to `AuthFailed` state and does NOT retry.

### v2 Contract

- URL: `wss://{tenant}.vcad.boundstate.ai/ws` or `wss://vcad-{customer}.boundstate.ai/ws` (decision deferred to Medtronic procurement)
- TLS: unchanged
- Framing: unchanged
- Auth: OAuth 2.0 device authorization grant (RFC 8628).
  - Add-in first-run opens a browser tab to `https://vcad.boundstate.ai/device`, user signs in via Clerk, authorizes the add-in instance.
  - Orchestrator issues an access token + refresh token to the add-in, stored in Windows Credential Manager on the user's workstation.
  - WebSocket handshake carries the access token in `Sec-WebSocket-Protocol: bsvoice-cad-v1, bearer-{token}`.
  - Refresh handled transparently before token expiry.
- Handshake failure: same HTTP 400/401/429 mapping on the upgrade response. Close code 4401 still reserved exclusively for post-upgrade revocation. The add-in triggers re-authentication flow on any auth-category failure.

### What Does Not Change

- SPEC-01 polymorphic message types — every message record unchanged
- `ProtocolVersion.Current` — **does not bump** between v1 and v2. The wire contract is identical; only the handshake credential differs.
- Rate limiting, message size limits, epoch semantics, reconnect behavior — all unchanged

### Migration Mechanism

- v1 → v2 is controlled by orchestrator config, not protocol change
- Orchestrator reads `DEPLOYMENT_AUTH_MODE` env var: `api_key` (v1) or `oauth` (v2)
- The `api_keys` table stays in v2 for service-to-service calls (e.g., internal tools, CI smoke tests) — but add-in connections must use OAuth
- Add-in detects which mode the orchestrator is running by inspecting the WWW-Authenticate header on the initial HTTPS probe to `/health`

## 3. Screenshot Transport

### v1 Contract

- `ScreenshotResult` carries:
  - `screenshotBase64`: base64-encoded PNG, **max 2 MiB after encoding**
  - `sha256Hash`: hex SHA-256 of the decoded PNG bytes
  - `widthPx`, `heightPx`
- Add-in captures PNG, checks encoded size; if > 2 MiB, progressively reduces dimensions until it fits
- No local filesystem path, no shared directory, no URL
- Orchestrator decodes to memory, passes to Anthropic vision API as image input, discards after turn
- DHF stores: `screenshot_before_sha256` / `screenshot_after_sha256` (always) and `screenshot_before_png` / `screenshot_after_png` BYTEA columns (SPEC-04 §3 migration 008 adds these) for audit replay and deep validation. Post-hackathon may move screenshot bytes to S3 to reduce DB size, but the SHA-256 columns remain canonical identifiers.

### v2 Contract

- `ScreenshotResult` carries:
  - `screenshotUrl`: S3 presigned GET URL (expires 5 min)
  - `sha256Hash`: hex SHA-256
  - `widthPx`, `heightPx`
  - `screenshotBase64`: **absent** (null or omitted entirely)
- Add-in POSTs PNG to S3 via presigned PUT URL obtained from orchestrator HTTPS endpoint (`POST /api/presigned-upload`), receives key
- Orchestrator issues presigned GET URL to include in the message
- DHF stores S3 key; viewer and Anthropic vision API fetch via the presigned GET URL

### Migration Mechanism

- This **is a breaking wire-contract change** — `ScreenshotResult` record shape differs between v1 and v2
- **Mitigation without rewrite:** `ScreenshotResult` gains `screenshotBase64` (nullable) and `screenshotUrl` (nullable) in v1, with an envelope invariant: exactly one must be non-null
- v1 orchestrator always sends base64, never URL
- v2 orchestrator always sends URL, never base64
- Consumers (agent loop, viewer, DHF writer) branch on which field is populated
- This allows a single codebase to target both deployment modes by configuration, with no code rewrite
- **Does not bump ProtocolVersion** — the record shape is forward-compatible

### What Does Not Change

- The 4 MiB message size cap on all other messages — screenshot base64 is special-cased to 2 MiB to leave headroom for `StateSnapshot`
- `StateSnapshot` truncation rules for feature tree — unchanged
- SHA-256 hash verification — identical in both modes
- DHF `screenshot_*_sha256` columns — authoritative in both modes

## 4. Viewer Hosting and Auth

### v1 Contract

- URL: `https://vcad.boundstate.ai/` (served by same Railway service as orchestrator)
- Auth: **none.** Viewer is publicly accessible at the landing page.
- **Landing page behavior (`GET /`):** Neutral landing page only. Does NOT auto-load any session. Does NOT list sessions. Does NOT accept anonymous tenant parameters. Shows a brief product description and a "Sign in to see your sessions" placeholder that currently just explains the URL-as-capability model.
- **Session access:** `GET /session/{sessionId}` or `GET /?sessionId={sessionId}` is the only path to content. Knowledge of a session UUIDv4 is the capability to view that session. UUIDv4 provides ~122 bits of entropy; URL guessing is not a practical attack.
- **Session list:** `GET /api/dhf/sessions` requires a `sessionId` query parameter whose session the caller already knows. The orchestrator resolves the tenant from that session row and returns other sessions scoped to the same tenant. This supports the sidebar UX (clicking around between sessions within a tenant) without enabling anonymous enumeration.
- **No anonymous tenant override:** The `?tenant=X` query parameter from earlier drafts is removed. Tenant resolution in v1 is exclusively via the `sessionId` → `tenant_id` lookup path. A request with no `sessionId` cannot resolve a tenant and cannot list sessions.
- TLS: Railway edge
- Rate limit: global IP-based rate limiter (Railway default) to prevent enumeration brute-forcing

### v2 Contract

- URL: unchanged shape; may be per-tenant subdomain
- Auth: **Clerk** (or Auth0) SSO, tenant-scoped
  - Landing page requires login
  - Session list filtered to sessions owned by authenticated tenant
  - Session URLs still use UUIDs, but accessing a session URL requires tenant membership
- Rate limit: unchanged + per-user rate limits

### Threat Model Boundary

**v1 is explicitly NOT suitable for PHI or PII.** The URL-as-capability model is defensible for:

- Hackathon demo with synthetic design data
- Medtronic's initial "look at the product" walkthrough with non-sensitive sample models
- Any session where disclosure of the full session content to someone who has been sent the URL would be embarrassing at worst

v1 is explicitly NOT defensible for:

- Patient data
- Medical device designs under trade-secret protection
- Anything covered by 21 CFR Part 11 in production use (SDR-TL-006 already establishes this)

This boundary is codified in SPEC-00 SDR-TL-011 and SDR-TL-012.

**Known sidebar-exposure trade-off (documented, not fixed in v1):** Knowledge of one session UUID within a tenant allows the holder to see other session UUIDs and metadata for the same tenant via the sidebar list. This is an intentional v1 trade-off for UX (switching between sessions within a tenant) and is acceptable under the threat model above. v1.5 Clerk integration replaces this with tenant-role-based access control.

### Migration Mechanism

- Clerk integration is a self-contained addition: middleware on viewer routes, Clerk-provided session cookie, no changes to DHF schema or orchestrator WebSocket handling
- Estimated integration: 15-30 min of code + Clerk dashboard configuration
- **Does not require orchestrator changes** — the viewer is the only component that gains auth
- **Does not bump ProtocolVersion** — no wire format changes
- v1.5 adds an auth middleware ahead of all viewer routes; the session-resolution logic remains the same but runs inside an authenticated context

## 5. Database and Tenant Isolation

### v1 Contract

- Single Railway-managed Postgres 16 instance
- Single `dhf` schema per SPEC-04
- `workstation_id` column on `dhf.sessions` — now a **client-supplied tenant hint**, not a trust boundary (see SDR-04-008 rewritten for v1)
- `api_keys` table holds credential → tenant mapping
- Every session row is scoped to the tenant that owned the API key used to open it (implicit column: `tenant_id` added in migration 006)

### v2 Contract

- **Per-tenant Postgres schema** — one `dhf_{tenant_slug}` schema per customer
- Schema creation automated on first successful OAuth tenant provisioning
- Cross-tenant queries forbidden at the application layer (repository enforces `tenant_id` filter on every query; arch test verifies)
- Backup and restore per-tenant, independent

### Migration Mechanism

- Migration 006 (v1) adds `tenant_id` column to `dhf.sessions` (and by extension, joined via session_id to entries / tool_calls / decorations)
- v1 repository queries always include `WHERE tenant_id = @TenantId` in reads and INSERTs
- Per-tenant schema migration (v2) introduces schema-per-tenant; a migration script copies rows from `dhf.sessions WHERE tenant_id = X` into `dhf_X.sessions` for each tenant
- v2 repository selects schema based on authenticated tenant context
- **No application code rewrite required** — the repository gains a tenant-resolution step in its constructor, but the SQL shape is identical
- **Does not bump ProtocolVersion** — database internals invisible to the wire contract

## 6. AddinSessionId and Workstation Trust

### v1 Contract

- `AddinSessionId`: unchanged from SPEC-01 §9 format (32 lowercase hex, derived deterministically from `machineGuid`, `swProcessId`, `loadTimestampUtcTicks`, and a random nonce per SPEC-02 §8.1)
- Algorithm authoritative in SPEC-02 §8 (SPEC-01 specifies format regex + epoch semantics only)
- `workstation_id`: **client-supplied string, not a trust boundary.** The add-in reports `Environment.MachineName` as workstation_id; the orchestrator accepts it at face value within the authenticated API key's tenant scope
- Cross-workstation conflict (same AddinSessionId, different workstation_id, same tenant) remains a hard error per SDR-04-008 — the value of this check is detecting VM cloning, snapshot rollbacks, and accidental ID collisions within one tenant's fleet

### v2 Contract

- `AddinSessionId`: unchanged format
- `workstation_id`: scoped to OAuth-authenticated tenant context; enforcement is identity-bound, not machine-bound
- Post-hackathon improvement: replace `Environment.MachineName` with a cryptographically-generated persistent workstation token, stored in Windows Credential Manager alongside OAuth tokens — eliminates the "machine name collision" category of incidents

### What Does Not Change

- Cross-workstation hard-error semantics (SDR-04-008)
- Epoch rotation rules (SPEC-01 §9, SPEC-00 §6 glossary)
- `CrossWorkstationSessionConflictException` structured metadata and remediation message

## 7. Observability and Logging

### v1 Contract

- Structured logging via Serilog to Railway stdout (captured by Railway's log aggregation)
- Log fields: timestamp, level, message, exception (with stack), `session_id`, `entry_id`, `api_key_id` (redacted to first 8 chars)
- No PHI expected in logs by construction (DHF payloads never logged; only IDs and counts)
- Retention: Railway default (7 days)
- **Header redaction:** Railway request logging MUST redact the `Sec-WebSocket-Protocol` header on the WebSocket upgrade request, because the v1 API key lives in that header value. Redaction is configured in the Railway logging sink configuration, not in application code. See SPEC-01 §12.3 for the known-risk disclosure regarding this header.

### v2 Contract

- Additional sinks: per-tenant audit log export to customer-designated S3 bucket or SIEM
- Extended retention: 7 years minimum (21 CFR 820 retention requirement)
- PHI/PII redaction review per tenant
- OAuth bearer tokens MUST be redacted from request logs (same mechanism as API key redaction in v1)

### Migration Mechanism

- Serilog sink configuration change only
- No code changes to log call sites
- Retention is storage-layer policy, not application code

## 8. Version-Bump Triggers

The following changes require `ProtocolVersion.Current` to bump. None are required for v1→v2 as specified above.

- Any `IClientMessage` or `IAddinMessage` record gains or loses a required field
- Any polymorphic discriminator value changes
- Any enum adds or removes a value that may appear in persisted hash chains
- `HashCanonicalizer` rules change in a way that breaks historical hash verification
- `AddinSessionId` format changes (currently `^[0-9a-f]{32}$`)

The following v1→v2 changes do **not** require a ProtocolVersion bump (and are explicitly designed not to):

- Auth mode (API key ↔ OAuth)
- Screenshot transport (base64 ↔ S3 URL), because `ScreenshotResult` already carries both nullable fields with an envelope invariant
- Viewer auth (none ↔ Clerk)
- Postgres schema layout (shared ↔ per-tenant)
- `workstation_id` enforcement semantics

## 9. Locked Decisions

**DM-1:** v1 WebSocket auth is API key in `Sec-WebSocket-Protocol` subprotocol; v2 is OAuth device flow. No other auth schemes considered. Known risk: the subprotocol header may be logged by intermediaries; Railway logging configuration MUST redact this header per §7.

**DM-2:** v1 screenshot transport is base64-inline capped at 2 MiB post-encode; v2 is S3 presigned URL. The `ScreenshotResult` record carries both `screenshotBase64` (v1, nullable) and `screenshotUrl` (v2, nullable) with an envelope invariant that exactly one is set.

**DM-3:** v1 viewer has no auth and relies on UUIDv4 session URLs as capabilities. `GET /` is a neutral landing page that does not list or resolve sessions. Session content requires possession of a session UUID. v2 adds Clerk. v1 MUST NOT be used for PHI, PII, or regulated design data in production customer contexts. SDR-TL-011 and SDR-TL-012 codify this boundary.

**DM-4:** v1 database is a single Railway-managed Postgres instance with a `tenant_id` column enforced in every query. v2 is per-tenant schema isolation. Migration between them is an SQL script; no application code rewrite.

**DM-5:** ProtocolVersion does not bump between v1 and v2. The wire contract is stable across the deployment transition by design.

**DM-6:** `vcad.boundstate.ai` is the canonical v1 domain. Per-tenant subdomains (`{tenant}.vcad.boundstate.ai`) are reserved for v2 multi-tenancy.

**DM-7:** Clerk is the selected v2 viewer auth provider. If Clerk is unavailable (ITAR-restricted customers, air-gapped deployment), Auth0 is the fallback. Both use OAuth 2.0 with PKCE; the orchestrator's tenant model doesn't care which provider.

**DM-8 (v1.1.0):** Pre-upgrade handshake failures are HTTP 400/401/429; WebSocket close code 4401 is reserved exclusively for post-upgrade credential revocation. SPEC-01 §12.4 is authoritative. Sending close code 1006 in a Close frame is forbidden per RFC 6455 §7.4.1; heartbeat-timeout behavior is specified in SPEC-01 §12.7.

**DM-9 (v1.1.0):** The viewer is co-hosted at the service root (`/`); there is no `/viewer` path prefix. SPEC-06 §4.1 is authoritative for viewer routing.

---

*This document is locked at v1.1.0. Changes require an update to this document FIRST, then downstream spec patches. Each spec's "Deployment: v1/v2 boundary" subsection must reference this document by section number, not by repeating its contents.*

*Generated by Beth (Claude Opus 4.7) and Dr. Joseph Wilde — BoundState AI LLC.*
