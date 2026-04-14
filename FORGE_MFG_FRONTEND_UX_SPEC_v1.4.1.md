---
title: FORGE-MFG Frontend UX Specification
version: 1.4.1
author: Dr. Joseph Wilde / Beth (Claude Opus 4.6)
date: 2026-04-14
classification: BoundState Proprietary
status: DRAFT — Revision 4 (Oracle Review R1/R2/R3 + R4 findings applied); reviewer-scope annotations added for R5 pass
implements: FORGE_MFG_BUILD_SPEC v1.4.2, Section 5B Frontend
---

# 0. Scope of This Review (Reviewer Instructions)

> **To Oracle reviewers (Fran / Codex / Grok / Sonnet / Beth):** the
> following three items are **known open** and require input outside
> Oracle's reach.  Do not re-flag them as defects, do not attempt to
> author answers, and do not propose mitigations that would require
> data we do not have.  Focus your review budget on mechanical
> consistency, cross-reference integrity, the sections newly authored
> in v1.4 (5.4.11, 6.7, 9.5 additions, Reopen / Reactivate
> transitions, Recall + on-hold graph fix), and any genuinely new
> issues.

**0.1 R1-ISSUE-016 — NPR 8735.2C regulatory validation (closure-as-attestation).**  In scope for reviewers: flagging *new* sections whose preconditions depend on the regulatory gate (so we can add the cross-reference annotation if we missed any).  Out of scope: proposing a pre-closure checklist artifact, challenging the click-to-close attestation design, or declaring the flow unimplementable.  That decision lives with MSFC QA.

**0.2 R1-ISSUE-019 enriched by R4-001 — Section 9 safety-critical sign-off (floor authentication method, dual sign-off enforcement, cross-device coordination, network-failure recovery, multi-role edge).**  In scope for reviewers: structural completeness of the question list in Sections 9.1–9.5 (e.g., *"You omit this scenario"*).  Out of scope: proposing an answer to any of the open questions, recommending one authentication method over another, or declaring the placeholder itself a defect.  Answers require domain input from MSFC QA and BoundState security.

**0.3 R4-012 — Offline write-disable production risk.**  In scope for reviewers: flagging *new* write paths that would be blocked offline.  Out of scope: proposing a pending-queue design, a maximum-acceptable-offline-duration, or any specific mitigation.  That decision requires floor connectivity data we do not have.

**0.4 Explicit invitation.**  Reviewers are encouraged to flag:

- Missing cross-references between sections (especially to the R1-ISSUE-016 gate).
- Internal contradictions between state machines and UI button visibility tables.
- Enum values, error codes, or field names that appear in one section and disagree with another.
- New sections that should have been added to the v1.4 changelog in Section 12 but were not.
- SSE event names, audit event names, or error codes used in prose but not listed in their canonical registries (6.5.2, 6.6.1, inline error-code columns).
- Role permission mismatches between the matrices in Section 3.1 and the section-level actions they govern.
- Any Phase 1 behavior that has UI surface (routes, buttons, tooltips) but no corresponding API contract in the spec.

---

# 1. Design Philosophy
## 1.1 Core Principle
FORGE-MFG is a manufacturing execution system used on a rocket engine production floor.  Its primary interface is a transactional work surface where operators execute safety-critical manufacturing operations with gloved hands, inspectors approve or reject work in real time, and engineers manage the flow of production across multiple engine builds.
The system also provides role-appropriate monitoring surfaces — dashboards, schedule views, and status summaries — that serve oversight roles (QA managers, program managers) without compromising the transactional clarity of the operator and inspector interfaces.  These monitoring views are deliberate and constrained; they exist to support production decisions, not to serve as general-purpose analytics or executive reporting tools.
The UI must be calm, legible, and unambiguous.  Every interaction has consequences — a misplaced tap on "Complete" instead of "Hold" can cascade through the schedule.  The design must make the right action obvious and the wrong action difficult.
## 1.2 Design Direction
Industrial utilitarian.  Clean, high-contrast, no decoration.  Think aircraft cockpit instrument panels — every element serves a function, nothing is ornamental.  Color is used for status communication, not branding.  Typography is large and readable at arm's length.  Touch targets are generous.
This is not a consumer app.  It does not need to be delightful.  It needs to be trustworthy.
## 1.3 Design Constraints
- Must work on tablets (iPad, Surface) used on the manufacturing floor — touch-first
- Must work on desktop monitors in engineering offices — mouse and keyboard efficient
- Must be readable at arm's length (operator holding a tablet while standing at a workstation)
- Must function under fluorescent shop lighting (high contrast, no subtle gradients)
- GovCloud deployment — no CDN, no external font loading, fonts bundled in build
- React 18 + TypeScript, Zustand + React Query (Zustand for client state, React Query for server cache), Recharts
- All data fetching through the OpenAPI-generated client with proper loading / error states.  Architectural exceptions: presigned S3 uploads (Section 5.5) and Server-Sent Events for chat + operation-execution events (Sections 5.4, 5.9) operate outside the generated client; their full integration contracts are in Section 6.5.

# 2. User Personas
## 2.1 Operator — "Hands on the Hardware"
Who: Technicians, welders, brazers working directly on engine hardware.  60% of all users.
Environment: Standing at workstations on the manufacturing floor.  Wearing gloves (nitrile or leather).  Tablet mounted on a stand or held in one hand.  Fluorescent lighting.  Noisy environment — no audio cues.
Primary tasks: Start / stop operations, log labor time, record material consumption, mark operations complete, view work instructions.
What they need: Large touch targets (minimum 48 px, prefer 56 px).  Minimal typing — dropdowns and selectors over text fields.  Current operation status always visible.  One-tap access to their active work order.  Never more than 2 taps from their most common action.
What they fear: Accidentally completing an operation they're not done with.  Losing labor time entries.  Being blocked by an inspection hold they don't understand.
## 2.2 Inspector — "Quality Gate"
Who: QA inspectors who verify work, record measurements, accept or reject operations.  20% of users.
Environment: Walking between workstations with a tablet.  May need to photograph evidence.  Reviewing measurement data and comparing against criteria.
Primary tasks: View pending inspections (filtered queue at /inspections, Section 5.5.5), record inspection results with measurements, upload evidence photos, sign off on operations, initiate NCRs for rejected work, resolve outstanding conditional findings post-completion.
What they need: A filtered view of "what's waiting for me."  Measurement entry forms that support decimal precision.  Camera integration for evidence capture.  Clear indication of which operations are safety-critical (dual sign-off required — Section 9).  Ability to see the operator's labor entries to verify work was performed (but cannot be the same person — SOD-001).
What they fear: Missing a safety-critical hold point.  Signing off on work they haven't verified.  Accidentally approving when they meant to reject.
## 2.3 Engineer — "Production Manager"
Who: Manufacturing engineers who create work orders, define operations, manage the production flow.  15% of users.
Environment: Desktop monitors in an office adjacent to the floor.  Frequent context-switching between the system and engineering documents.  May walk to the floor with a laptop.
Primary tasks: Create and release work orders, define operations with work instructions, disposition NCRs, manage schedules, correct stopped labor entries, hold / resume operations, review build status across multiple engine programs.
What they need: Dense information display — they want to see 20 operations at once, not 3 cards.  Keyboard-efficient workflows (tab between fields, Enter to submit).  Drag-and-drop operation reordering with explicit preservation / rewrite semantics for custom predecessor graphs (Section 5.4.7).  Side-by-side views (work order + schedule, NCR + operation detail).  Bulk actions where appropriate.
What they fear: Releasing a work order with missing operations or wrong document references.  Schedule impact from an NCR they haven't noticed.
Phase 2: What-if schedule analysis (see Section 11).
## 2.4 QA Manager — "Final Authority"
Who: Quality assurance managers who close work orders and NCRs, review audit trails, monitor compliance.  3% of users.
Primary tasks: Close work orders (final QA closure), close NCRs, hold / resume work orders for quality concerns, review dashboard for open items, drill through to the Inspection queue for unresolved conditional findings, verify audit trail integrity.
What they need: Dashboard-first experience.  Open NCR count, overdue inspections, unresolved conditional findings, build status at a glance.  Ability to drill from dashboard to specific entity.  Audit trail viewer with filtering (Section 6.6).
## 2.5 Program Manager — "Read-Only Overview"
Who: Program managers who monitor progress but don't execute manufacturing actions.  2% of users.
Primary tasks: Monitor production progress, review schedule status, track build milestones, review work order and NCR status at a summary level.
What they need: Read-only access to production data, scoped to their assigned programs.  Dashboard and schedule are their primary workspace, but they require navigational access to work orders, NCRs, materials, and parts for context when investigating status.  No write actions.  Program selector locked to assigned programs only.
## 2.6 Admin — "System Operator"
Who: BoundState or MSFC system administrators.  <1% of users.
Primary tasks: User management, role assignment, program membership, system health monitoring, dead-letter queue management.
What they need: Admin panel separated from manufacturing UI.  User list with role / program editing.  System health indicators.  Audit integrity status.

# 3. Information Architecture
## 3.1 Navigation Model
Top-level navigation is a persistent left sidebar (desktop) or bottom tab bar (tablet / mobile — see Section 7.2 for the 4+More overflow pattern).  Navigation items are role-filtered — operators don't see admin, program managers don't see operation execution.
### Role Definitions
Canonical role identifiers are snake_case.  The full enum is:
| Role | Description|
|------|-------------|
|operator | Floor execution — start / stop operations, log labor, consume materials, create parts|
|inspector | Quality verification — record inspections, receive / accept materials, initiate NCRs, resolve conditional findings|
|engineer | Production management — create / edit work orders, manage operations and schedules, disposition NCRs, correct stopped labor entries|
|qamanager | Quality oversight — hold / resume / close work orders, close NCRs, read access to inspections and materials|
|programmanager | Program monitoring — read-only access to production data, scoped to assigned programs|
|admin | System administration — user management, program configuration, system health |
Roles are not hierarchical (an engineer does not inherit operator permissions).  Each role grants a distinct permission set.  Users may hold multiple roles where organizationally appropriate.
Multi-role permission composition.  When a user holds multiple roles, their effective permission set is the union of each role's permissions.  Separation-of-Duties (SoD) checks are enforced independently of role combination — a user who holds both operator and inspector roles is still blocked by SOD-001 from inspecting an operation on which they logged labor.  The default landing page for a multi-role user is the Dashboard (shared across all roles).  Navigation items are the union of items available to any role held.
### Navigation Structure
Dashboard                [all roles]
Work Orders              [read: all roles; create/edit: engineer; hold/resume/close: qa_manager; complete: engineer]
├── Work Order Detail
│   ├── Operations List
│   ├── Operation Detail (engineer hold/resume surface — Section 5.4.11)
│   ├── Operation Execution [operator]
│   └── Audit Trail Tab
Inspections              [inspector: queue + form; qa_manager: read queue + drill-through]
├── Inspection Queue (/inspections — Section 5.5.5)
└── Inspection Form (per operation)
NCRs                     [read: all roles; create: inspector, engineer; disposition: engineer; close: qa_manager]
└── NCR Detail
Schedule ¹               [read: all non-admin; create/edit: engineer]
Materials                [receive/accept: inspector; consume: operator; read: all others]
Parts                    [create: operator; update: operator (within assigned WO); read: all others]
Chat ²                   [query: all non-admin]
Admin                    [admin only]
├── Users
├── Programs
├── System Health
└── Dead Letters
¹ Schedule is visible when the selected program has an active schedule configuration.  If no schedule is configured for the program, the Schedule navigation item does not appear.  See Section 5.11 for the full schedule IA.
² Chat is visible when the selected program has oracle_access enabled in program configuration.  If oracle_access is not enabled, the Chat navigation item does not appear.  Chat's server-side authorization model is defined in Section 5.9.3.
### Runtime Configuration Changes
If a program configuration flag changes while a user is in an active session (e.g., oracle_access is disabled, or a schedule configuration is removed), the affected navigation item is removed on the next data polling cycle.  If the user is currently viewing the affected page:
1. If the form is clean (no dirty state), redirect immediately to the Dashboard with a toast: "This feature is no longer available for the selected program."
1. If the form is dirty, the client displays a blocking modal: "This feature has been disabled for the selected program.  Your unsaved changes cannot be submitted.  [Copy My Changes] [Discard and Continue]."  The Copy My Changes action places a serialized representation of the dirty form onto the clipboard so the user can paste it elsewhere; there is no auto-save to server or to local draft storage.  The redirect only proceeds after the user confirms discard.
This replaces the prior claim that "no unsaved data is lost" — dirty-form protection does not persist data; it only provides an explicit discard acknowledgment.
### Role-Based Navigation Visibility
| Page | operator | inspector | engineer | qamanager | programmanager | admin|
|------|----------|-----------|----------|------------|-----------------|-------|
|Dashboard | ✓ | ✓ | ✓ | ✓ | ✓ | ✓|
|Work Orders | read | read | ✓ | read + hold/resume/close ³ | read | read|
|Operation Execution | ✓ | — | — | — | — | —|
|Operation Detail (hold/resume) | — | — | ✓ | — | — | —|
|Inspections (queue + form) | — | ✓ | — | read queue ⁴ | — | —|
|NCRs | read | ✓ | ✓ | ✓ | read | read|
|Materials | consume | ✓ | read | read | read | —|
|Parts | ✓ | read | read | read | read | —|
|Schedule | read | read | ✓ | read | read | —|
|Chat | ✓ | ✓ | ✓ | ✓ | ✓ | —|
|Admin | — | — | — | — | — | ✓ |
Legend: ✓ = full access for this role's scope; read = read-only access; consume / create = specific action access; — = no access.
³ qa_manager has read access to all work orders in assigned programs plus hold / resume actions on active work orders and close action on completed work orders.  QA managers cannot create or edit work order drafts.
⁴ qa_manager has read access to the Inspection Queue so the Dashboard drill-throughs (unresolved conditional findings, overdue inspections) resolve to a usable page.  QA managers cannot record or sign off inspections.
## 3.2 URL Structure
/                                           → Dashboard
/work-orders                                → Work Order List
/work-orders/:id                            → Work Order Detail
/work-orders/:id/ops/:opId                  → Operation Execution (operator) or Operation Detail (engineer) — Section 5.4.11
/work-orders/:id/ops/:opId/inspect          → Inspection Form
/inspections                                → Inspection Queue (inspector, qa_manager)
/ncrs                                       → NCR List
/ncrs/:id                                   → NCR Detail
/materials                                  → Material Certification List
/materials/:id                              → Material Detail
/parts                                      → Part Instance List
/parts/:id                                  → Part Detail + Traceability
/schedule                                   → Program-level Schedule (engineer, qa_manager, program_manager) — see Section 5.11
/chat                                       → ORACLE Chat
/admin                                      → Admin Dashboard
/admin/users                                → User Management
/admin/users/:id                            → User Detail + Role / Program Assignment
/admin/programs                             → Program Management
/admin/health                               → System Health
### Program Context on Entity Routes
Entity-detail routes (/work-orders/:id, /ncrs/:id, /parts/:id, etc.) do not carry a program identifier in the URL.  Program membership is derived from the entity's server-side program_id field after the entity is fetched.  This avoids leaking cross-program entity existence via 403 responses.
Auto-switch behavior.  If a user deep-links, bookmarks, or opens a shared link to an entity that belongs to a program other than the user's currently-selected program, and that entity's program is among the user's assigned program_memberships, the client automatically switches the active program selector to that program after the entity loads and displays a toast: "Switched to program [Program Name] to view this record."  If the entity's program is not in the user's memberships, the API returns 404 (not 403) to avoid leaking cross-program existence, and the client renders the standard not-found empty state.
### Route Authorization
Navigation filtering (hiding menu items) is a UX convenience, not a security control.  All routes enforce authorization independently along three dimensions: role, program membership, and program feature flags (e.g., schedule_enabled, oracle_access).
Role / program-check routes (static, membership-only): Client-side route guards check the user's role and program membership before rendering the page.  Direct navigation to an unauthorized route redirects to the Dashboard with a toast: "You do not have access to this page."
Entity-detail routes (canonical pattern — fetch-then-authorize): Client-side pre-render guards cannot evaluate program membership for entity routes because the URL carries no program identifier.  The canonical pattern is:
1. Render a loading shell (skeleton screen).
1. Fetch the entity via the OpenAPI client.
1. On response, compare response.program_id against the user's program_memberships.  If the entity is in an assigned program, render.  If the entity's program differs from the current selector but is assigned, apply the auto-switch behavior above.  If the program is not assigned, render the not-found empty state.
1. On 404 from the server, render the not-found empty state without attempting a second authorization pass.
This contract replaces the prior implication that pre-render program guards applied to entity routes.
Feature-flag-gated routes (Schedule, Chat): Client route guards check the selected program's feature flags in addition to role / membership.  Direct navigation to /schedule when the selected program has no schedule configuration, or to /chat when oracle_access is not enabled, redirects to the Dashboard with a toast: "This feature is not available for the selected program."  The server independently enforces the same check — every /api/schedules/* and /api/chat/* endpoint returns 403 with error code FEATURE_DISABLED if the caller's selected program lacks the required flag.
Cross-program access (all routes): If an API request resolves an entity whose program_id is not in the caller's program_memberships, the API returns 404 (not 403).  This is intentional — returning 403 would confirm the entity's existence and leak cross-program metadata.  The frontend renders the standard not-found empty state.
Server-side enforcement is independent of the client.  Even if a client-side guard is bypassed, the API enforces role, program, and feature-flag checks on every request.  The frontend displays 403 responses as an inline error banner: "You are not authorized to perform this action."

# 4. Design System
## 4.1 Color Palette
Status communication palette — color carries meaning, not decoration.
| Token | Hex | Usage|
|-------|-----|-------|
|--bg-primary | #FAFAFA | Page background|
|--bg-surface | #FFFFFF | Cards, panels|
|--bg-elevated | #F5F5F5 | Section headers, table headers|
|--text-primary | #1A1A1A | Body text, labels|
|--text-secondary | #6B6B6B | Supplementary text, timestamps|
|--text-muted | #999999 | Placeholder text, disabled labels|
|--border | #E0E0E0 | Card borders, dividers|
|--status-draft | #9E9E9E | Gray — not started, pending|
|--status-active | #1565C0 | Blue — in progress, active|
|--status-hold | #F57C00 | Amber — on hold, attention needed|
|--status-complete | #2E7D32 | Green — complete, accepted, closed|
|--status-error | #C62828 | Red — rejected, failed, violation|
|--status-rework | #7B1FA2 | Purple — rework, requires re-inspection|
|--safety-critical-bg | #FFEBEE | Light red background tint — safety-critical operations|
|--safety-critical-border | #C62828 | Red double border — safety-critical operations|
|--interactive | #1565C0 | Buttons, links, interactive elements|
|--interactive-hover | #0D47A1 | Button hover state|
|--destructive | #C62828 | Delete, reject, void actions|
|--destructive-hover | #B71C1C | Destructive action hover |
### Safety-Critical vs. Error Differentiation
Safety-critical indicators and error states both use red but must be visually distinct without relying on color alone (see Section 8 for full accessibility requirements):
| State | Color | Icon | Text Label | Border|
|-------|-------|------|------------|--------|
|Safety-critical | --safety-critical-bg background with --safety-critical-border | ⚠ Warning triangle | "SAFETY-CRITICAL" (uppercase, bold) | Double border, 2 px|
|Error / Rejected | --status-error background | ⛔ Circle-X | Status-specific text (e.g., "Rejected", "Failed") | Single border, 1 px |
### Dark Mode
Not Phase 1.  Manufacturing floor lighting is bright fluorescent — dark mode is a Phase 2 consideration for office users (see Section 11).
## 4.2 Typography
Bundled in build — no external font loading (GovCloud constraint).
| Element | Font | Size | Weight | Line Height|
|---------|------|------|--------|-------------|
|Page title | System sans-serif | 24 px | 600 | 1.3|
|Section header | System sans-serif | 18 px | 600 | 1.3|
|Body text | System sans-serif | 16 px | 400 | 1.5|
|Table text | System sans-serif | 14 px | 400 | 1.4|
|Label | System sans-serif | 14 px | 500 | 1.3|
|Caption / timestamp | System sans-serif | 12 px | 400 | 1.3|
|Status badge | System sans-serif | 12 px | 600 | 1.0|
|Floor display (op card) | System sans-serif | 20 px | 500 | 1.4 |
System font stack: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif
## 4.3 Spacing and Layout
Base unit: 8 px.  All spacing is a multiple of 8.
| Token | Value | Usage|
|-------|-------|-------|
|--space-xs | 4 px | Inline padding, badge padding|
|--space-sm | 8 px | Between related elements|
|--space-md | 16 px | Between sections, card padding|
|--space-lg | 24 px | Between major sections|
|--space-xl | 32 px | Page padding, major gaps|
|--space-2xl | 48 px | Section separation |
### Touch Targets
- Minimum touch target: 48 × 48 px (WCAG 2.5.8 Level AAA)
- Preferred touch target for floor use: 56 × 48 px
- Spacing between touch targets: minimum 8 px
- Primary action buttons: full width on mobile / tablet, 200 px minimum on desktop
## 4.4 Component Library
### Status Badge
Pill-shaped, colored by status.  Each badge includes a text label — color is never the sole differentiator.
### Safety-Critical Banner
Persistent — pinned to the top of the view and never scrolls off screen.  --safety-critical-bg background, --safety-critical-border double border, ⚠ triangle icon, uppercase "SAFETY-CRITICAL" text.
### Action Buttons
Three tiers:
- Primary: Filled blue.  One per view maximum.
- Secondary: Outlined.  Supporting actions.
- Destructive: Filled red.  Requires confirmation dialog.
### Confirmation Dialogs
Required for all state transitions (complete, release, close, hold, rework), all destructive actions (reject, void, delete draft), and all safety-critical sign-offs (which additionally require the re-authentication step defined in Section 9).
### Empty States
Every list view has a meaningful empty state.
### Error States
API errors surface as inline banners with the rule / error code (e.g., COMP-001, SOD-001), not modals.  Banners persist until dismissed or the error condition clears.
### Loading States
Skeleton screens for initial page loads.  Inline spinners for action submissions.  Never blank screens.

# 5. Page Specifications
## 5.1 Dashboard
Purpose: At-a-glance production status.  First thing every user sees.
### Behavior
- Summary cards are clickable — drill to filtered list.
- Program selector scope: The dropdown is populated from the user's assigned programs.  Program managers see only their assigned programs.  Engineers, QA managers, and operators / inspectors see the programs they are assigned to.  Admins see all programs.  The selected program is the default scope for all data fetches; individual list pages MAY override with an explicit "All (assigned programs)" multi-program filter (see Section 5.2).  The server enforces program membership independently.
- Program selector persistence uses sessionStorage (tab-scoped) as the primary cache.  localStorage stores only the most-recently-used program as a hint for the initial selection on a newly-opened tab; it is not read again within the tab's lifetime.  Auto-switching when a user deep-links to a cross-program entity updates only the current tab's sessionStorage; it does not silently overwrite other tabs' program context.
- Build progress bars link to work order detail.
- NCRs over 30 days get an amber warning indicator.
- QA manager dashboard additions: Unresolved Conditional Findings count with drill-through to the Inspection Queue filtered on conditional.  Overdue Inspection count with drill-through to the Inspection Queue filtered on overdue.
- Data refreshes on 60-second polling interval (list views).  Operation Execution pages use SSE for sub-second updates on safety-critical state changes (Section 6.5.2).
### Overdue Inspection Threshold
An inspection is overdue when all of:
1. The operation is in the active state with an unsatisfied inspection hold point (hold_point_status in ('pending', 'conditionally_satisfied')), AND
1. The operation has been in the active state for longer than overdue_threshold_hours for the program (default 24 hours, configurable per-program via admin).  The clock starts at the server's operation.active_since timestamp and accumulates in calendar time (not business hours — manufacturing is shift-based).
The API exposes an is_overdue boolean and an overdue_since timestamp on each operation and inspection summary.  The dashboard drill-through uses the query parameter ?state=overdue; the Inspection Queue applies the same server-side filter.
## 5.2 Work Order List
Purpose: Find and manage work orders.
### Behavior
- Rows clickable to work order detail.
- Status badges use standard color coding.
- "Ops" column shows completed / total.
- Create button visible only for engineer role.
- Pagination per spec (page_size default 50, max 200).
- Program filter.  The Program filter on this page defaults to the active program selector but supports an "All (assigned programs)" value for roles with multi-program read access (engineer, qamanager, programmanager).  When All is selected, the page sends no program_id query parameter; the API returns rows across all of the caller's assigned programs.  Pages that support All are explicitly enumerated (currently: Work Order List, NCR List, Inspection Queue, Material List, Part List).  All other data-fetching requests use the active single-program selector unchanged.  The React Query cache key MUST include the program scope ({programId | "all"}) so a cached single-program result is not served when the user switches to All, and vice versa.
## 5.3 Work Order Detail
Purpose: Complete view of a single work order with all related data.
### Behavior
- Tabbed interface: Operations | Documents | Schedule | Audit Trail (Audit Trail driven by Section 6.6).
- Operation rows expand inline to show labor entries, inspection events, material consumption.
- Safety-critical operations marked with ⚠ triangle icon and "SC" label (not color alone).
- Hold point column shows inspection requirement.
- Draft work orders show edit controls and operation authoring (add, reorder, delete).
- Released work orders show execution controls only.
- Completed work orders show the close action (qa_manager only).
- Closed work orders are fully read-only — no action buttons.
- A work-order-on-hold banner appears at the top of the detail view (and propagates to the Operation Execution page) whenever the WO is in on_hold, with the reason and hold actor.
### Work Order State Machine
| Transition | From | To | Permitted Roles | Preconditions|
|-----------|------|----|-----------------|---------------|
|Release | draft | released | engineer | At least one operation defined; all operations have work instruction references; operation dependency graph passes DAG validation (no cycles, no self-references, no dangling predecessor IDs) (Section 5.4.6)|
|Recall | released | draft | engineer | WO is in released state (no operations have been started; if any had, WO would be active)|
|Start | released | active | system (automatic) | First operation started by an operator|
|Hold | active | onhold | engineer, qamanager | Optional force_stop_labor flag (see Hold Cascading below)|
|Resume | onhold | active | engineer, qamanager | —|
|Complete | active | completed | engineer | All operations completed; all inspection hold points satisfied or conditionally satisfied (conditional counts as satisfied for Complete); no NCRs linked to this work order in open state (all NCRs must be at minimum dispositioned)|
|Close | completed | closed | qa_manager | All NCRs linked to this work order in closed state; zero unresolved conditional inspection findings on any operation (every conditional must have a subsequent Accept or Reject, per Section 5.5.4); all operations completed|
|Reactivate | completed | active | system (automatic) | Triggered when a child operation is reopened for rework (see Section 5.4.1 Reopen transition) |
### Work Order Hold Cascading (Parent-Child Semantics)
A work-order Hold transition accepts an optional `force_stop_labor` boolean (default false).  Cascading behavior differs by flag:
When `force_stop_labor` is `false` (administrative hold — default):
- Operations in pending state: remain pending.  Start is blocked with error OP-START-003 (parent WO on hold) and a tooltip: "Cannot start: work order is on hold."
- Operations in active state: continue to execute physically — labor timers are not forcibly stopped.  However:
- Complete is blocked with error OP-COMP-004 (parent WO on hold).
- Hold on the child operation is allowed (and recommended).
- Put On Hold on labor (stop-timer) is always allowed.
- A banner on the Operation Execution page reads: "Parent work order is on hold.  You may stop your timer but cannot complete this operation until the work order is resumed."
- Operations in on_hold state: Resume is blocked while the parent is on hold, with error OP-RESUME-001 (parent WO on hold) and a disabled button state.  The resume button re-enables when the parent WO transitions back to active.
When `force_stop_labor` is `true` (stop-work hold):
- All effects of the administrative hold apply, plus:
- All active labor entries on child operations in active state are atomically stopped by the server at the hold timestamp.  The end_time of each force-stopped entry is the WO hold timestamp.
- Each force-stopped entry records an audit event labor.stopped_by_hold with the hold actor and WO hold reason.
- Operators see an immediate banner (via SSE push): "STOP WORK — Your timer was stopped because [actor] placed the work order on hold.  Reason: [reason]."
- This mode is intended for safety or quality holds where continued physical work is prohibited.
The Hold confirmation dialog includes a checkbox: "☐ Stop all active labor timers (stop-work hold)" with an explanation: "When checked, all running timers on this work order's operations will be stopped immediately.  Use this for safety or quality holds that require work to cease."
These rules ensure a quality or safety hold applied at the work-order level cannot be bypassed by transitions at the operation level.
### Work Order Permission Matrix
| Action | operator | inspector | engineer | qamanager | programmanager|
|--------|----------|-----------|----------|------------|------------------|
|View | ✓ | ✓ | ✓ | ✓ | ✓ (own programs)|
|Create | — | — | ✓ | — | —|
|Edit draft | — | — | ✓ | — | —|
|Release | — | — | ✓ | — | —|
|Recall | — | — | ✓ | — | —|
|Hold | — | — | ✓ | ✓ | —|
|Resume | — | — | ✓ | ✓ | —|
|Complete | — | — | ✓ | — | —|
|Close | — | — | — | ✓ | — |
### UI Button Visibility by State
| Work Order State | Visible Actions|
|-----------------|-----------------|
|draft | [Release Work Order] (engineer only)|
|released | [Recall to Draft] (engineer only)|
|active | [Hold Work Order] (engineer, qamanager).  `[Complete Work Order]` (engineer only) — disabled with tooltip if preconditions unmet.|
|onhold | [Resume Work Order] (engineer, qamanager).  `[Fix Graph]` (engineer only) — visible only when the server flags `graphissue = true (see Section 5.4.6).|
|completed | [Close Work Order]` (qa_manager only) — disabled with tooltip if preconditions unmet.|
|closed | No actions — fully read-only. |
### Closure-as-Attestation — UNRESOLVED COMPLIANCE QUESTION
> PENDING (R1-ISSUE-016): Spec §5.3 and §5.6 treat the [Close] button click (after server-computed preconditions pass) as the QA manager's non-repudiable attestation that they have reviewed the work order and its audit trail.  No separate "review checklist" artifact is required under this design. > > This design has not been validated against NPR 8735.2C quality system requirements for rocket-engine flight-hardware manufacture.  If the applicable regulatory body requires an explicit attestation artifact (e.g., a pre-closure checklist form that generates a distinct audit event), the closure UX, audit schema, and state machine will require changes. > > Resolution gate: This question MUST be resolved before the closure flow is approved for implementation.  If a pre-closure checklist is required, the form fields, stored artifact, and its distinct audit event must be defined and merged into this spec before implementation begins. > > Dependent sections: The following sections contain closure preconditions and downstream flows that are provisional pending this gate: Section 5.5.4 (inspection result effects on closure), Section 5.5.5 (post-completion resolution flow), Section 5.6.3 (NCR closure preconditions), Section 5.6.6 (NCR UI actions by state).
## 5.4 Operation Execution (Operator View)
Purpose: The operator's primary work screen.  Execute a single operation.
### 5.4.1 Operation State Machine
| Transition | From | To | Permitted Roles | Preconditions | API Error Code(s)|
|-----------|------|----|-----------------|---------------|-------------------|
|Start | pending | active | operator | Parent WO released or active; all predecessor operations in completed; parent WO not on hold | OP-START-001 (WO not released), OP-START-002 (predecessor incomplete), OP-START-003 (parent WO on hold)|
|Hold | active | onhold | operator, engineer | — | —|
|Resume | onhold | active | operator, engineer | Parent WO not on hold | OP-RESUME-001 (parent WO on hold)|
|Complete | active | completed | operator | No active labor entries by any user on this operation; hold_point_status in {satisfied, conditionally_satisfied}; no unresolved NCRs on this operation (see OP-COMP-003 below); parent WO not on hold | OP-COMP-001 (hold point unsatisfied), OP-COMP-002 (active labor entry), OP-COMP-003 (unresolved NCR), OP-COMP-004 (parent WO on hold)|
|Reopen | completed | active | system (automatic) | Triggered atomically when an NCR linked to this operation is dispositioned as rework or repair while the operation is completed.  See Section 5.6.2. | — (system-triggered; not user-initiated) |
OP-COMP-003 — Unresolved NCR Check (clarified):  Operation completion is blocked when one or more NCRs linked to this operation are in an unresolved state, defined as:
- NCR in open state (not yet dispositioned), OR
- NCR in dispositioned state with disposition rework or repair that lacks a post-disposition inspection with result = Accept on the linked operation.
NCRs dispositioned as use_as_is or scrap do not block operation completion (their closure preconditions do not require further work on the operation).
### 5.4.2 Start vs. Resume-Labor Contracts
Two distinct server actions create a labor entry; the UI renders them as a shared [Start Timer] button but the contracts differ.
Start Operation (pending → active).  Endpoint: POST /api/operations/:opId/start.  Atomic: transitions the operation from pending to active, creates a labor entry with start_time, and (if first operation started on a released WO) transitions the WO from released to active.  Returns operation, labor_entry, and optionally the work_order update.  Preconditions per the Start row in 5.4.1.
Resume Labor on Active Operation (labor resume).  Endpoint: POST /api/operations/:opId/labor.  Creates a new labor entry on an operation that is already in active state (e.g., operator stopped their timer and is resuming work).  Does not change operation state.  Subject to the one-active-timer-per-user invariant (see 5.4.4); if the user has an active labor entry on a different operation, this call atomically stops that entry and starts a new one.
This endpoint is the canonical endpoint for all labor-start actions on active operations, including cross-operation atomic handoffs.  When the client is aware that the user has an active timer on another operation, it SHOULD include version_assertions for concurrency safety:
POST /api/operations/:opId/labor
{
  "version_assertions": {
    "target_operation": 12,
    "active_labor_entry": 3,
    "active_operation": 7
  }
}
The version_assertions object is optional.  When present, the server validates each asserted version and returns 409 with a conflicts array if any are stale (see Section 6.3.3).  When absent, the server still performs the atomic handoff but without version verification — suitable for the common case where the user does not have an active timer elsewhere.
The UI distinguishes the two only in the confirmation dialog copy — Start Operation for the pending→active case, Start Labor for the restart case — but the button label remains [Start Timer] for operator familiarity.
### 5.4.3 Hold Point Status
An operation's hold point is derived from its inspection history, server-computed:
| hold_point_status | Meaning|
|---------------------|----------|
|none | Operation has no inspection hold point|
|pending | Hold point exists, no inspection recorded yet|
|satisfied | Most recent inspection on this operation has result = accept|
|conditionally_satisfied | Most recent inspection on this operation has result = conditional|
|blocked | Most recent inspection on this operation has result = reject (NCR auto-created) |
The frontend MUST NOT derive hold-point status by client-side traversal of the inspection list.  It uses the hold_point_status field on the operation response.  The field is updated atomically by the server whenever a new inspection is recorded; multiple inspections are always ordered by inspected_at, with the latest effective.
### 5.4.4 Labor Timer Model
Labor timers are server-authoritative.  The client is a display; the server is the source of truth.
- Start event.  Immediately persisted to server via POST.  Server records start_time and returns the labor entry ID.  Client computes elapsed time locally from start_time for responsive display.
- Stop event.  Immediately persisted via PATCH.  Server records end_time and computes duration.  Client updates display from server response.
- One active timer per user (atomic handoff).  The server enforces that a user can have at most one active (unstopped) labor entry at any time across all operations.  Starting a new timer on a different operation is a single atomic server operation that stops the previous timer and starts the new one; if either half fails, neither takes effect.  The server returns both the stopped and new entries in the response.  Client displays: "Your timer on Op XX was automatically stopped."  If the atomic operation fails, the client displays: "Could not start timer.  Your timer on Op XX is still running." and does not update local state.
- Network loss.  The client continues displaying elapsed time computed from the last known start_time and shows the "Connection lost" banner (Section 6.4).  On reconnection, the client refetches the active labor entry; if still open, display resumes seamlessly.
- Session expiry.  The labor entry remains open on the server.  After re-login, the client queries for the user's active labor entry and resumes display.  The operator can then stop the timer, recording actual elapsed time since the server-recorded start_time.
- Multi-tab / multi-device.  Only the most recent session's timer display is authoritative.  Other tabs poll for and reflect the current labor state on window focus.
### 5.4.5 Labor Time Correction
Engineers can correct recorded labor times.  Corrections are restricted to stopped labor entries only (entries with a non-null end_time).  Correcting an active entry is refused with error LABOR-CORR-001 — the operator must stop the timer first.  This restriction exists because correcting an active entry's end_time would produce an entry with both a corrected_end_time and a live tick, breaking the one-active-timer invariant.
- Correction action.  Endpoint: PATCH /api/labor-entries/:id with body {corrected_end_time, rationale}.  corrected_end_time must be after start_time and not in the future.  A rationale string (min 10 chars) is required.
- Audit trail.  The original end_time is preserved.  The correction records: correcting user, correction timestamp, original value, corrected value, and the rationale (see Section 6.6 audit event labor.corrected).
- Operator view.  Operators cannot directly correct labor entries.  They can flag an entry as needs correction via POST /api/labor-entries/:id/flag-correction with a note.  Flagged entries appear in the engineer's work order view as a pending item.
- Client-side resync.  When an engineer submits a correction on an entry that an operator's session is currently viewing, the operator's next poll / SSE tick surfaces the updated corrected_end_time and a toast: "An engineer adjusted your logged time on Op XX.  See audit trail for details."
### 5.4.6 Predecessor Graph
Operations may declare zero or more predecessors.  The server exposes the graph as a DAG; cycles, self-references, and dangling IDs are forbidden.
Reachability invariant.  A root operation is any operation with zero predecessors (in-degree 0).  Multiple root operations are legal — they represent independent starting points in the execution graph.  In a valid DAG, every operation is reachable from at least one root by following predecessor edges backward (this is a mathematical property of finite acyclic graphs and does not require a separate validation check beyond cycle and dangling-reference detection).
- Authoring.  Engineers edit the predecessor list per-operation in the work-order detail editor.  The editor validates locally against the current draft graph and surfaces errors inline (e.g., "Cannot add Op 7 as predecessor of Op 3 — creates a cycle.").
- Release-time validation.  The Release transition runs a server-side DAG validation.  Violations are rejected with specific error codes:
- WO-REL-101 (cycle detected, with participating op sequence numbers in the error payload).
- WO-REL-102 (self-reference).
- WO-REL-103 (dangling predecessor ID — references an operation that does not exist in this work order).
- Pre-activation graph fix (Recall).  If a released work order is found to contain a graph issue before any operation has started, the engineer uses [Recall to Draft] (Section 5.3) to return the WO to draft state, fix the graph, and re-release.
- Post-activation graph fix (on-hold edit).  If a graph issue is discovered after the WO has become active (e.g., a predecessor edge that blocks an operation incorrectly), the engineer places the WO on hold and uses the [Fix Graph] action.  This action:
- Is available only on on_hold work orders with engineer role.
- Opens a limited graph editor that allows editing predecessor edges only (add / remove predecessors).  Adding or removing operations is not permitted on an active WO.
- On save, the server runs DAG validation (WO-REL-101..103).  If validation fails, the editor remains open with inline errors.  If validation passes, the graph is updated and an audit event work_order.graph_fixed is recorded.
- The WO remains on_hold until the engineer explicitly resumes it.
- If the fix changes predecessor requirements for a pending operation that was previously blocked, the UI updates the operation's [Start Timer] tooltip on the next SSE tick or poll.
### 5.4.7 Drag-and-Drop Reorder Semantics
When an engineer reorders operations via drag-and-drop in the draft editor:
- Default-sequential operations (those with no explicit predecessors): Only sequence_number is updated.  Predecessor edges are inferred from sequence.
- Operations with explicit predecessors: The engineer is prompted with a modal: "This operation has explicit predecessor relationships.  Choose an action:
- `[Preserve Dependencies]` — only `sequence_number` changes; predecessor IDs remain.  New sequence may not match dependency order.
- `[Rewrite as Sequential]` — predecessors are cleared and the new sequence order becomes the dependency order.
- `[Cancel]` — no change."
- Any reorder that would place an operation before its predecessor without cancelling dependencies is allowed (the predecessor graph, not sequence, governs execution gating), but surfaces an inline warning: "Op X is sequenced before its predecessor Op Y.  Execution order is determined by dependencies, not sequence."
### 5.4.8 API Response Shape — Operation Detail
To render the predecessor-gating tooltip without additional fetches, the operation detail endpoint includes a predecessor_summaries array (not just raw IDs):
{
  "id": "op-uuid",
  "sequence_number": 3,
  "status": "pending",
  "hold_point_status": "pending",
  "predecessors": ["op-uuid-1", "op-uuid-2"],
  "predecessor_summaries": [
    {"id": "op-uuid-1", "sequence_number": 1, "description": "Machine housing", "status": "completed"},
    {"id": "op-uuid-2", "sequence_number": 2, "description": "Weld injector ports", "status": "active"}
  ],
  "version": 7,
  ...
}
The client uses predecessor_summaries to render the disabled [Start Timer] tooltip: "Waiting for: Op 2 — Weld injector ports (active)."
### 5.4.9 OP-COMP-002 — Operation-Wide Labor Check
The "all labor entries stopped" precondition applies to all users on the operation, not just the current user.  If another user has an active labor entry on this operation, the [Complete Operation] button is disabled with a tooltip surfacing:
- The other user's display name.
- Their elapsed time.
- A role-gated action: [Request Engineer to Stop] (operator) or [Stop Their Timer] (engineer).  The engineer action atomically stops the other user's labor entry and records an audit event (labor.stopped_by_engineer) with required rationale.
### 5.4.10 Behavior (Summary)
- Timer runs visually (updated every second) when labor entry is active.
- Material consumption shows running balance from the cert.
- [Complete Operation] disabled and grayed when hold_point_status is pending or blocked, or OP-COMP-002/003/004 conditions hold, with a tooltip explaining why.
- Safety-critical banner is persistent and prominent — never scrollable off screen (⚠ icon, double border per Section 4.1).
- Confirmation dialog on complete includes the hold / sign-off status summary.
- Work-order-on-hold banner is displayed whenever the parent WO is in on_hold.
- Operation Execution subscribes to an SSE channel (Section 6.5.2) for sub-second updates on hold-point status, parent-WO state, and labor entries from other users.  Polling is reserved for Dashboard and summary views.
- Reopened operations.  If a completed operation is reopened for rework (Section 5.4.1 Reopen transition), the Operation Execution page re-enables all execution controls (labor timer, material consumption, completion).  A banner indicates: "This operation was reopened for rework.  NCR [NCR-ID] requires corrective action."  The operator performs the rework, and the operation follows the normal completion flow (labor stop → inspection → complete).
### 5.4.11 Engineer Operation Detail
Purpose: The engineer's view of a single operation — monitoring, hold/resume control, labor correction, and graph management.
Route resolution.  The shared route /work-orders/:id/ops/:opId renders Operation Execution (Section 5.4) for users whose active role is operator, and Operation Detail for users whose active role is engineer.  Multi-role users who hold both operator and engineer see a role-switcher toggle at the top of the page: "Viewing as: [Operator ▾ | Engineer]" defaulting to operator if the user has an active labor entry on this operation, otherwise engineer.
Layout:
- Header: Operation description, sequence number, status badge, safety-critical banner (if applicable), parent WO breadcrumb.
- Status panel: Current state, hold-point status, predecessor status (using predecessor_summaries), linked NCR summary (count by state).
- Labor entries table: All labor entries for this operation across all users.  Columns: user, start time, end time (or "Running"), duration, corrected flag.  Engineer actions per entry:
- [Stop Timer] — on active entries by other users (records labor.stopped_by_engineer audit event with required rationale).
- [Correct Time] — on stopped entries (opens the correction form per Section 5.4.5).
- Flagged entries (operator-requested corrections) are highlighted with an amber indicator.
- Inspection history: Read-only list of all inspections on this operation, with result, inspector, timestamp, and linked NCR (if any).
- Material consumption: Read-only list of material consumption records.
- Predecessor graph (mini): Visual representation of this operation's immediate predecessors and successors, with status indicators.  Links to the full graph editor on the WO detail page.
Actions:
| Action | Preconditions | Error Codes|
|--------|---------------|-------------|
|[Hold Operation] | Operation is active | —|
|[Resume Operation] | Operation is on_hold; parent WO not on hold | OP-RESUME-001|
|[Stop Timer] (other user) | Target labor entry is active | LABOR-STOP-001 (entry already stopped)|
|[Correct Time] | Target labor entry is stopped | LABOR-CORR-001 (entry still active) |
The engineer Operation Detail page does not display the [Start Timer], [Complete Operation], or material-consume actions — those are operator-only.  If an engineer needs to perform operator actions, they must switch to the operator role via the role-switcher toggle (and SoD rules apply to the operator context).
## 5.5 Inspection Form (Inspector View)
Purpose: Record inspection results and evidence.
### 5.5.1 Behavior
- SoD warning is visible before the inspector starts entering data.
- If inspector has labor entries on this operation, the form is disabled with SOD-001 explanation.
- Result selection drives the downstream flow (see 5.5.4).
- Measurement fields have spec limits displayed inline.
- Evidence upload uses presigned S3 URLs (Section 6.5.1).
- Submit triggers a confirmation dialog; safety-critical operations require the re-authentication step defined in Section 9.
- When Conditional is selected, the Notes field becomes required.
### 5.5.2 Inspection Criteria Schema
Measurement fields are rendered dynamically from the inspection criteria schema defined in the API spec.  Each criterion includes:
- id — unique identifier
- label — display name
- unit — display unit
- type — one of the frozen Phase 1 enum: numeric, integer, boolean, text, enumerated (with choices array)
- precision — decimal places for numeric fields
- min / max — spec limits for inline warning
- required — whether the field must be filled
- display_order — rendering sequence
Unknown criterion types are treated as an unsupported-state error, not a text fallback.  If the API returns a criterion type the client does not recognize, the form renders a read-only error card: "This inspection requires a newer version of the client.  Contact an administrator.", and submission is blocked.  This prevents silent data quality failures as the schema evolves.
### 5.5.3 Evidence Attachment Lifecycle
1. Initiate upload.  Client requests a presigned S3 URL from the API (Section 6.5.1); returns attachment_id in pending state.
1. Upload.  Client uploads directly to S3 using the presigned URL.  On HTTP 200, client confirms via POST /api/attachments/:id/confirm, transitioning to uploaded.
1. Submit inspection.  [Submit Inspection] validates all attachments are in uploaded state.  Submission is blocked with inline error if any are still pending or uploading.
1. Committed.  On successful inspection submission, all associated attachments transition to committed and become immutable.
1. Orphan cleanup.  Attachments in pending or uploaded state not associated with a submitted inspection within 24 hours are eligible for server-side cleanup.
Constraints: JPEG / PNG / PDF / TIFF; max 50 MB per file; max 20 attachments per inspection.  Filenames sanitized server-side; original preserved as metadata.
### 5.5.4 Inspection Result Definitions
> Provisional (R1-ISSUE-016): The closure preconditions and downstream effects in this section are subject to the pending NPR 8735.2C compliance gate.  See Section 5.3 Closure-as-Attestation.
| Result | Effect on Hold Point | Downstream Action | Operation Status|
|--------|---------------------|-------------------|------------------|
|Accept | Satisfied | None — operation may proceed to completion | Unchanged (remains active)|
|Reject | Not satisfied | NCR creation is mandatory.  The [Submit Inspection] button changes to [Submit & Create NCR].  The form expands to include required NCR fields (description, linked operation pre-populated).  Submission atomically creates both the inspection record and the linked NCR.  The operation remains blocked until the NCR is dispositioned and (if rework/repair) re-inspection is recorded with Accept. | Unchanged (remains active, hold point unsatisfied)|
|Conditional | Conditionally satisfied | Operation may continue processing.  The conditional inspection is recorded; the WO cannot be closed (not blocked from Complete) until every conditional is resolved by a subsequent Accept or Reject on the same operation. | Unchanged (remains active) |
Post-completion resolution.  Conditional findings may be resolved on operations that are already completed.  See Section 5.5.5 for the follow-up inspection flow, including the operation Reopen mechanism for post-completion Reject.
### 5.5.5 Inspection Queue (/inspections)
Purpose: Inspector's "what's waiting for me" view and QA manager's drill-through surface.
> Provisional (R1-ISSUE-016): Post-completion resolution outcomes interact with the WO closure flow, which is subject to the pending NPR 8735.2C compliance gate.  See Section 5.3 Closure-as-Attestation.
Filters:
- state — one of all, pending, conditional (conditional findings awaiting resolution), overdue (per Section 5.1 threshold), completed (historical).
- program — program scope (defaults to active selector; supports All (assigned programs)).
- operation_id — drill-through from a specific operation.
- safety_critical — boolean toggle.
Behavior:
- Inspectors see pending + conditional + overdue assigned to them or unassigned within their program scope.  QA managers see read-only; they cannot claim items.
- Rows link to the Inspection Form at /work-orders/:id/ops/:opId/inspect.  Completed operations with open conditional findings open a variant form titled "Resolve Conditional Finding" which is restricted to Accept or Reject (the conditional path is unavailable for resolution).
- Sort default: oldest pending first, then by safety-critical flag, then by overdue_since.
- The Dashboard's "Unresolved Conditional Findings" and "Overdue Inspections" tiles drill through here with state=conditional or state=overdue preselected.
Post-completion conditional resolution flow:
1. An inspector (SoD-checked as always) opens the Inspection Queue and selects a row flagged state=conditional, operation_status=completed.
1. The Resolve Conditional Finding form opens, pre-populated with the original conditional inspection's criteria + findings for reference.
1. The inspector records a new inspection (Accept or Reject).  This creates a new InspectionEvent record with supersedes_inspection_id pointing to the conditional; the operation's hold_point_status updates according to the new result.
1. If Accept: The conditional finding is resolved.  The operation remains completed.  The WO closure precondition for this conditional is now satisfied.
1. If Reject: An NCR is atomically created as usual.  The operation remains completed at this point.  When the engineer subsequently dispositions the NCR as `rework` or `repair`, the operation is automatically reopened (see Section 5.4.1 Reopen transition and Section 5.6.2).  If the NCR is dispositioned as use_as_is or scrap, the operation is not reopened — those dispositions do not require further work on the operation.
Operation Reopen on post-completion Reject → Rework/Repair:
When an NCR created from a post-completion Reject is dispositioned as rework or repair:
- The linked operation transitions from completed to active (Reopen transition, Section 5.4.1).
- If the parent WO was in completed state, it transitions to active (Reactivate transition, Section 5.3).
- The Operation Execution page re-enables all execution controls.
- The operator performs the rework/repair, a new inspection with result Accept is recorded, and the operator completes the operation again.
- The NCR closure preconditions (Section 5.6.3) are now satisfiable.
This mechanism resolves the otherwise-uncloseable NCR that would result from a post-completion Reject with a rework/repair disposition.
## 5.6 NCR Detail
Purpose: View and manage a nonconformance report.
### 5.6.1 Behavior
- Disposition form disabled for initiator (SOD-002 enforced visually).
- Disposition options: use_as_is, rework, repair, scrap.
- MRB toggle shows additional authority fields when required.
- No under_review or verified states exist in UI.
### 5.6.2 NCR State Machine
| Transition | From | To | Permitted Roles | Preconditions|
|-----------|------|----|-----------------|---------------|
|Create | — | open | inspector, engineer | Linked to operation or standalone|
|Disposition | open | dispositioned | engineer (not the initiator — SOD-002) | Disposition selected, rationale provided.  Side-effect: If disposition is rework or repair AND the linked operation is in completed state, the server atomically reopens the operation (completed → active) and, if necessary, reactivates the parent WO (completed → active).  The engineer's disposition confirmation dialog includes a warning when this side-effect will occur: "This will reopen Operation [Op#] for [rework/repair] and return Work Order [WO#] to active status.  [Confirm] [Cancel]"|
|Close | dispositioned | closed | qa_manager | Per-disposition preconditions below (5.6.3) |
### 5.6.3 Closure Preconditions by Disposition
> Provisional (R1-ISSUE-016): NCR closure preconditions interact with the WO closure flow, which is subject to the pending NPR 8735.2C compliance gate.  See Section 5.3 Closure-as-Attestation.
Linked to operation:
| Disposition | Additional Preconditions|
|-------------|---------------------------|
|use_as_is | MRB concurrence recorded if mrb_required = true; closure rationale ≥ 20 chars|
|rework | Linked operation has a subsequent inspection recorded with result = accept after the NCR was dispositioned; rework instructions acknowledged|
|repair | MRB concurrence recorded; linked operation has a subsequent inspection recorded with result = accept after the NCR was dispositioned; completion-evidence attachments present (see 5.6.4)|
|scrap | Linked part instance's status transitioned to scrapped (see 5.6.5) |
Standalone (no linked operation):
| Disposition | Additional Preconditions|
|-------------|---------------------------|
|use_as_is | MRB concurrence recorded if mrb_required = true; closure rationale ≥ 20 chars|
|rework | Completion-evidence attachments present; closure rationale documents corrective action taken|
|repair | MRB concurrence recorded; completion-evidence attachments present; closure rationale documents repair performed|
|scrap | Referenced material cert (if any) has status = scrapped; part instance (if any) has status = scrapped; closure rationale documents scope of scrap |
### 5.6.4 Completion Evidence (Rework / Repair)
The NCR detail view includes an Evidence section for rework and repair dispositions.  It uses the same presigned-upload lifecycle as inspection evidence (Section 5.5.3).  Required attachments:
- For rework: at minimum one attachment demonstrating the rework performed (e.g., repaired weld photograph, re-measurement record).
- For repair: at minimum one attachment plus an engineering authority reference in engineering_approval_id.
Closure is blocked with error NCR-CLOSE-101 (missing evidence) until the required attachments are present and in committed state.
### 5.6.5 Part Scrap Transition
When an NCR is dispositioned as scrap with a linked part instance, the QA manager's closure flow includes a [Mark Part as Scrapped] inline action on the NCR detail.  This action:
- Is gated by qa_manager role (part-status transition authority for scrapped).
- Opens a confirmation dialog referencing the specific part serial number.
- On confirm, calls PATCH /api/parts/:id with {status: "scrapped", reason_ncr_id: <ncr_id>}.
- Records audit events part.status_changed and ncr.scrap_linkage_confirmed (Section 6.6).
- The NCR close tooltip updates: "Part SN-XXXX scrapped — ready to close."
If the part is already scrapped (independent prior action), the NCR closure proceeds without a second transition.
### 5.6.6 UI Actions by NCR State
| NCR State | Visible Actions|
|-----------|------------------|
|open | [Submit Disposition] — visible to engineer, disabled for initiator per SOD-002|
|dispositioned | [Close NCR] — visible only to qa_manager; disabled with tooltip if preconditions unmet (error codes NCR-CLOSE-001..199)|
|closed | No actions — fully read-only |
### 5.6.7 Closure-as-Attestation — UNRESOLVED (see Section 5.3 for the shared pending regulatory gate)
## 5.7 Material Certification List
Purpose: Track material certificates and consumption.
### Behavior
- Filter by status (quarantine, accepted, rejected, consumed, expired).
- Program filter supports All (assigned programs) per Section 5.2.
- Expired certs visually flagged.
- Quantity remaining shown as progress bar against quantity received.
- Consume action navigated from operation execution, not from this list.
## 5.8 Part Traceability
Purpose: Trace a serialized part through its complete manufacturing history.
### Behavior
- Visual tree: material cert → operations → inspections → NCRs → parent assemblies.
- Part-status transitions are read-only on this view except for the scrap transition gated through an NCR closure (5.6.5).
## 5.9 ORACLE Chat [CONDITIONAL — requires oracle_access]
Purpose: Advisory-only compliance and data queries.
### 5.9.1 Behavior
- Advisory banner is always visible, never dismissible.
- Citations are expandable cards linking to pack rules or live data.
- Streaming responses via SSE (contract in Section 6.5.2) with typing indicator.
- Chat history per session, not persisted across sessions in Phase 1.
### 5.9.2 Session Context Logging
Each chat session records session_id, active user, active program, timestamp, query, response, and citation list.  This log is retained for 90 days for quality analysis and regulatory review.  Sessions are not linked to work orders or operations in Phase 1 (deferred to Phase 2 pending operational need).
### 5.9.3 Chat Authorization Model (Server-Side)
The chat pipeline runs under the same program and role authorization as the standard API.  Chat cannot return data the caller could not fetch directly via the OpenAPI client:
- Every query template execution passes through the caller's program_memberships filter (same filter applied to corresponding REST endpoints).
- Role-gated endpoints (e.g., admin-only audit detail) are not exposed via chat regardless of oracle_access.
- Citation fetches (loading pack rules by ID) are always permitted because packs are program-agnostic.  Citation fetches that pull live data (e.g., "cite NCR-2026-0015") run through the same scope filter as the NCR detail endpoint.
- If a query would leak cross-program data, the chat pipeline refuses with code CHAT_SCOPE_VIOLATION rather than returning a partial answer.
These rules are enforced server-side; the frontend surfaces any refusal as a chat response with an advisory notice.
## 5.10 Admin Dashboard
Purpose: System administration and user management.
Separated visually from manufacturing UI with distinct header color (--text-primary background, white text) to make it unmistakably different from the production interface.
### Sections
- System Health: ECS task status, RDS connectivity, S3 replication lag, audit chain status — green / yellow / red indicators.
- Users: List with role badges, program memberships, active / inactive toggle.
- Programs: List with member count, active toggle, feature flags (schedule_enabled, oracle_access, overdue_threshold_hours).
- Dead Letters: Failed event deliveries with retry button.
- Audit Integrity: Last verification run timestamp, pass / fail, record count (driven by Section 6.6 API).
## 5.11 Schedule (Program-Level)
Purpose: Program-scoped schedule management and visualization.
Route: /schedule.  Visibility: engineer (write), qa_manager, program_manager (read), operator / inspector (read — for context).  Gated by the program's schedule_enabled feature flag.
### 5.11.1 Layout
- Summary header: program, active schedule, overall health indicator, next milestone.
- Gantt view: work orders on vertical axis, time on horizontal.  Color-coded by status.  Expandable rows reveal operation-level timing.
- Milestones panel: key dates, actor-responsibility, status.
- Links panel: inter-work-order dependencies.
### 5.11.2 Engineer Write Workflows
- Create schedule from work order baseline (POST).
- Update work order target dates (PATCH with optimistic concurrency, Section 6.3).
- Add / remove schedule links (inter-WO dependencies).
- Mark milestones as achieved.
### 5.11.3 Read-Only Consumers
QA managers and program managers see the same Gantt + milestones panels without edit controls.  Operators / inspectors see a flattened per-program summary view; they cannot drill into schedule links.
### 5.11.4 Work Order Detail Embed
The Work Order Detail Schedule tab (Section 5.3) surfaces the schedule slice for that specific WO: predecessor / successor links, target dates, forecast, baseline delta.  Read-only on that embed; full edit requires /schedule.

# 6. Interaction Patterns
## 6.1 Optimistic UI vs. Server-Confirmed
All state transitions are server-confirmed.  No optimistic updates on manufacturing state changes.  The button shows a spinner, the server responds with success or error, then the UI updates.  This is non-negotiable — optimistic UI on a manufacturing floor creates phantom states.
Form field edits (text, dropdowns) can be optimistic for responsiveness within a dirty form.  The dirty form is committed atomically on submit.
## 6.2 Stale Data Handling
- List views poll on a 60-second interval when the tab is active.
- Detail views poll on a 30-second interval.
- Operation Execution does not poll; it subscribes to an SSE channel (Section 6.5.2) for sub-second updates on hold-point status, parent-WO state, and labor entries from other users.
- Dirty form protection.  When a form has unsaved changes, full-entity polling is suspended for that view.  A sparse-fieldset GET (e.g., GET /api/work-orders/:id?fields=version,etag) continues at the normal polling interval to detect version changes without overwriting the user's edits.  The sparse endpoint is declared in the OpenAPI spec with response schema {version: int, etag: str}; it is not a HEAD request.  If the version has changed, a non-intrusive banner appears: "This record has been updated by another user.  Save or discard your changes to see the latest version."  Full polling resumes automatically on submit, explicit discard, or navigation.
Aggregate versioning for the work-order draft editor.  The work-order draft editor allows editing child entities (operations, predecessor edges) within a single editing session.  The parent work order's version field increments on any server-side mutation of the WO or any of its child operations (aggregate versioning).  This means the sparse-fieldset poll on the WO version detects both direct WO edits and concurrent operation edits by another user.  The server implements this by incrementing the parent WO version as a side-effect of any child-operation mutation.  When the engineer submits the draft, the request asserts the WO version; a 409 conflict covers all child-entity races.
- Idle timeout (30 min) clears all cached state, preceded by a 5-minute countdown warning starting at 25 min.  The warning modal offers [Stay Signed In] (resets the idle timer) and [Sign Out Now].
- Dirty-form interaction with idle timeout.  On timeout with a dirty form, the client displays: "Your session is about to expire.  Unsaved changes will be lost." with [Save Now] (submit if valid) and [Copy Changes] (serialize form to clipboard).  After timeout, the form is cleared; there is no server-side draft persistence in Phase 1.  Uploaded-but-unsubmitted attachments remain in S3 per the 24-hour orphan cleanup (Section 5.5.3).
- Version conflict on submit.  See Section 6.3.
## 6.3 Optimistic Concurrency Contract
All PATCH and state-transition endpoints require the caller to assert the version they observed when reading the entity.  The server rejects the request with 409 Conflict if the observed version no longer matches the current version.
### 6.3.1 Version Semantics
- Every entity exposes a monotonically increasing integer version field.  The version increments on every server-side mutation of the entity.
- Aggregate versioning: For parent-child relationships (work order → operations), the parent entity's version increments on mutations to either the parent or any child.  This ensures that a single version assertion on the parent catches all concurrent edits within the aggregate.  See Section 6.2 for the work-order draft editor application.
- The server exposes the version two ways:
- As the version field in every entity response body.
- As a strong ETag header on GET responses: ETag: "<version>".  We use strong ETags only.  Weak ETags (W/"...") are never emitted — the HTTP If-Match spec requires strong comparison.
- Clients MUST include the observed version on every mutation.  Two equivalent mechanisms are supported; pick one per endpoint, declared in the OpenAPI spec:
- If-Match: "<version>" header (preferred for PATCH against a single entity).
- version field in the JSON request body (preferred for composite operations where an entity is updated as part of a larger transition).
### 6.3.2 409 Response Shape
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "This record has been modified by another user.",
    "entity_type": "work_order",
    "entity_id": "...",
    "your_version": 7,
    "current_version": 9,
    "current_entity": { ...full fresh entity... }
  }
}
The response MUST include the current fresh entity so the client can render a diff without a second round-trip.
### 6.3.3 Composite-Mutation Concurrency (Multi-Entity Atomic Operations)
Some mutations atomically touch more than one entity: the atomic labor-timer handoff (touches previous labor entry + new labor entry + possibly the old and new operations + possibly the work order on first start), the inspect-reject flow (creates an inspection and an NCR atomically), and the NCR-disposition-with-reopen flow (transitions NCR + reopens operation + reactivates WO).
For composite mutations, the client submits a version_assertions object with an expected version for each affected entity.
Canonical example — labor handoff via `POST /api/operations/:opId/labor`:
POST /api/operations/op-uuid-new/labor
{
  "version_assertions": {
    "target_operation": 12,
    "active_labor_entry": 3,
    "active_operation": 7
  }
}
On conflict, the server returns 409 with a conflicts array naming every entity whose observed version was stale, plus their current versions and current entities.  The client renders a composite conflict modal enumerating each stale entity.
Endpoints that accept composite mutations MUST declare every version assertion they check in the OpenAPI spec.
### 6.3.4 Conflict-Resolution UX
On a 409 response, the client displays a blocking modal (not a dismissible banner, because proceeding with stale data would be unsafe):
╔══════════════════════════════════════════════════╗
║ This record has been updated                     ║
║                                                  ║
║ Another user changed WO-2026-0042 while you     ║
║ were editing.  Your version: 7.  Current: 9.    ║
║                                                  ║
║ Changes you made:                                ║
║   Module: MCC → Nozzle                          ║
║                                                  ║
║ Changes made by the other user:                 ║
║   Status: released → active                     ║
║                                                  ║
║ [Discard My Changes]     [Reload and Edit]       ║
╚══════════════════════════════════════════════════╝
- [Discard My Changes] — clears the dirty form, replaces the cached entity with current_entity from the 409 body, closes the modal.  The user sees the fresh state.
- [Reload and Edit] — replaces the underlying cached entity with current_entity but preserves the user's dirty form fields.  This allows re-submission after the user reconciles their changes against the newer state.  The server will reject again if the user tries to submit without updating the version assertion.
- The modal has no third "force overwrite" option — that would bypass the concurrency model.
- Cache invalidation: React Query invalidates the entity cache after either action, using the entity's cache key.  Sibling caches (e.g., list views) are not automatically refetched; they pick up the new version on the next poll / SSE tick.
## 6.4 Offline Behavior
- Banner: "Connection lost — reconnecting…" displayed at the top of the viewport.
- Read views continue to show the last-known data (with a timestamp).
- Write actions are disabled — buttons grayed with tooltip "Reconnect to resume."
- Labor timers continue to display locally (computed from the last known start_time).  No labor time is lost because start / stop are discrete server events; the timer resumes authoritatively on reconnect.
- Session-expiry during offline is handled as a normal re-auth flow on reconnect.
> UNRESOLVED QUESTION (R4-012): The write-disable policy is a > deliberate architectural decision to prevent phantom states on a > manufacturing floor.  However, transient WiFi drops are common in > metal-structure environments, and blocking all write actions during > connectivity loss — including operation completion, inspection > submission, and NCR creation — can halt production. > > This question requires domain input: > - What is the typical duration of transient WiFi drops on the >   MSFC manufacturing floor? > - Are wired backup connections (Ethernet docks) available at each >   workstation? > - What is the acceptable production-halt risk threshold for >   connectivity-gated write actions? > > Potential mitigations (Phase 2 or earlier if domain input warrants): > - Local pending queue with mandatory reconciliation on reconnect >   (introduces conflict-resolution complexity). > - Offline-mode sign-off with heightened audit requirements. > - Defined maximum acceptable offline duration before write-disable >   engages (e.g., allow writes during drops < 30 s). > > Until this is resolved, the write-disable policy stands as the > conservative default.
## 6.5 Integration Contracts (Non-OpenAPI Exceptions)
Two subsystems operate outside the generated OpenAPI client because their protocols are not RESTful.  Both contracts are documented here in full.
### 6.5.1 Presigned S3 Upload Contract (Evidence, Document Upload)
Phase A — Initiate upload.  POST /api/attachments (OpenAPI-native) with body {filename, mime_type, size_bytes, scope: "inspection_evidence" | "ncr_evidence" | "document", scope_id}.  Returns:
{
  "attachment_id": "att-uuid",
  "upload_url": "https://s3.<govcloud>/bucket/key?X-Amz-Signature=...",
  "upload_headers": { "Content-Type": "...", "x-amz-server-side-encryption": "..." },
  "expires_at": "2026-04-14T10:30:00Z",
  "max_bytes": 52428800
}
Phase B — Upload to S3.  Client issues PUT <upload_url> with the file bytes and the required upload_headers.  Authentication is via the presigned URL only — no Cognito bearer; the presigned URL carries its own short-lived signature (15-minute TTL).
- Success: HTTP 200 from S3.
- Retry policy: On 5xx or network error, retry with exponential backoff (base 2s, max 30s, max 5 attempts).  On 4xx, do not retry; surface error.
- Timeout: Client-side PUT timeout 5 minutes.  On timeout, abort and offer [Retry Upload] / [Cancel].
- Abort: AbortController signal attached to the PUT; user-initiated cancel aborts the in-flight request and marks the attachment aborted via POST /api/attachments/:id/abort.
Phase C — Confirm.  On S3 success, client calls POST /api/attachments/:id/confirm (OpenAPI-native).  Server verifies object existence in S3 and transitions attachment state to uploaded.  Returns updated attachment record.
Error mapping to UI:
| Failure | User-facing message|
|---------|----------------------|
|Presign request 400/403 | "Could not start upload — you may not have permission for this program."|
|Presigned URL expired (403 from S3) | "Upload link expired — click to retry." triggers a fresh presign.|
|S3 5xx | Automatic retry; after 5 attempts: "Upload failed after multiple retries.  [Retry] [Cancel]"|
|Size limit exceeded | "File too large.  Max 50 MB."|
|Confirm 404 | "Upload did not land in storage.  Please retry." |
### 6.5.2 Server-Sent Events (SSE) Contract
Used by:
- Chat — POST /api/chat/stream returns an SSE stream with events chunk, citation, done, error.
- Operation Execution — GET /api/operations/:opId/events subscribes the caller to hold-point status updates, parent-WO state changes, and other users' labor activity on the operation.  Events: hold_point_changed, wo_state_changed, labor_activity, operation_reopened, ping, done, error.
Auth: SSE connections include the Cognito bearer token via EventSource-compatible header shim (the client uses fetch with credentials: include + a polyfill that supports custom headers; pure EventSource is insufficient for bearer auth).  Token refresh mid-stream: on token expiry, the server terminates the stream with event: auth_expired, the client refreshes its token and reconnects.
Heartbeat: Server emits event: ping every 20 seconds.  If the client does not receive a ping for >40 seconds, it treats the connection as dead, closes, and reconnects.
Reconnect and state reconciliation.  On every reconnect (whether from heartbeat timeout, network drop, or auth refresh), the client performs a mandatory REST refetch of the full operation state via GET /api/operations/:opId (including predecessor_summaries, hold_point_status, and parent WO state).  This is the authoritative reconciliation mechanism.
The client MAY send Last-Event-ID: <id> on reconnect as a hint.  The server maintains a best-effort in-memory event buffer (not durable; lost on server restart or redeployment).  If the requested Last-Event-ID is within the buffer, the server replays missed events before emitting new ones.  If the ID is outside the buffer (or the buffer was cleared), the server sends event: buffer_reset with data: {"reason": "events_unavailable"}, signaling the client that the REST refetch is the sole source of truth for that reconnect.  The client MUST NOT rely on SSE replay for state consistency — the REST refetch is always performed regardless of whether replay succeeds.
Reconnect uses exponential backoff: base 1s, max 30s.  Each SSE event carries an id: <monotonic-int> header for ordering.
Abort: User-initiated abort (e.g., navigating away from the Operation Execution page) closes the EventSource / fetch reader cleanly.  The server does not persist stream state after disconnect.
Completion: A done event marks clean completion; the client closes the connection.
Error mapping: event: error with data: {code, message} is translated to an inline banner for chat; for Operation Execution, an error fails back to polling at the 30-second interval with a banner "Real-time updates unavailable — refreshing every 30 s."
Event schema (Operation Execution example):
event: hold_point_changed
id: 17
data: {"operation_id": "...", "hold_point_status": "satisfied", "inspection_id": "..."}

event: labor_activity
id: 18
data: {"operation_id": "...", "actor": {"id": "...", "display_name": "J. Rodriguez"}, "action": "started", "entry_id": "..."}

event: operation_reopened
id: 19
data: {"operation_id": "...", "ncr_id": "...", "disposition": "rework"}

event: buffer_reset
id: 20
data: {"reason": "events_unavailable"}

event: ping
id: 21
data: {}
## 6.6 Audit Trail API
All audit trail surfaces in the UI (Work Order Detail Audit Trail tab, NCR audit panel, QA audit viewer, Admin Audit Integrity panel) consume a single API.
### 6.6.1 Event Shape
{
  "audit_id": "evt-uuid",
  "timestamp": "2026-04-14T10:14:33Z",
  "actor": {"user_id": "...", "display_name": "J. Smith", "role_at_time_of_event": "engineer"},
  "action": "work_order.released",
  "entity_type": "work_order",
  "entity_id": "...",
  "entity_version_before": 3,
  "entity_version_after": 4,
  "before_state": { ...partial diff... },
  "after_state": { ...partial diff... },
  "metadata": { ...free-form... },
  "integrity_hash": "sha256:..."
}
Canonical actions relevant to Phase 1:
- work_order.created, work_order.released, work_order.recalled, work_order.held, work_order.resumed, work_order.completed, work_order.closed, work_order.reactivated, work_order.graph_fixed
- operation.started, operation.completed, operation.held, operation.resumed, operation.reopened
- labor.started, labor.stopped, labor.corrected, labor.stopped_by_engineer, labor.stopped_by_hold, labor.correction_requested
- inspection.recorded, inspection.superseded
- ncr.created, ncr.dispositioned, ncr.closed, ncr.scrap_linkage_confirmed
- material.received, material.accepted, material.consumed, material.rejected
- part.created, part.status_changed
- chat.queried
- admin.user_role_changed, admin.user_program_changed, admin.program_created, admin.program_updated
### 6.6.2 Endpoints
- GET /api/audit/events — paginated list with filters (entitytype, entityid, actoruserid, action, timestampfrom, timestampto, programid).  Response envelope: `{items, total, page, pagesize, pages}`.  Max page size 200.
- GET /api/audit/events/:id — single-event detail.
- GET /api/audit/integrity — integrity chain status: {last_verified_at, status: ok|failing, last_record_id, chain_length, last_hash}.  Admin-only.
### 6.6.3 Client Rendering
- Work Order / NCR tabs: pre-filter entity_id = <current_id>.  Newest-first.  Expandable rows show the before / after diff.
- QA Audit Viewer: full filter set exposed.  Pagination with default 50 per page.
- Admin Audit Integrity panel: polls /api/audit/integrity on a 5-minute interval.
### 6.6.4 Retention
Audit events are retained indefinitely in the primary store (append-only, hash-chained).  WORM replication to S3 Object Lock is per the build spec §7.2.
## 6.7 Program Context Wire-Level Contract
The active program context is communicated to the server via the `X-Program-Id` request header.  This header is required on all API requests except:
- Authentication endpoints (/api/auth/*).
- User-profile endpoints (/api/users/me).
- Endpoints that are inherently program-agnostic (e.g., pack-rule lookups used by chat citations).
### 6.7.1 Header Semantics
| Header Value | Meaning | When Used|
|---|---|---|
|X-Program-Id: <uuid> | Request is scoped to a single program. | Default for all requests. The UUID is the active program selector's value.|
|X-Program-Id: * | Request spans all of the caller's assigned programs. | List pages that support "All (assigned programs)" per Section 5.2 (Work Order List, NCR List, Inspection Queue, Material List, Part List).|
|(header omitted) | Invalid on scoped endpoints. | Server returns 400 with error code MISSING_PROGRAM_CONTEXT. Permitted only on program-agnostic endpoints listed above. |
### 6.7.2 Server-Side Enforcement
- On X-Program-Id: <uuid>: The server verifies the UUID is in the caller's program_memberships.  If not, returns 403 with code PROGRAM_NOT_ASSIGNED.
- On X-Program-Id: *: The server filters results to the caller's program_memberships.  Endpoints that do not support multi-program queries return 400 with code MULTI_PROGRAM_NOT_SUPPORTED.
- Feature-flag enforcement: For feature-gated endpoints (schedule, chat), the server checks the feature flag on the program identified by X-Program-Id.  On *, feature-gated endpoints return 400 MULTI_PROGRAM_NOT_SUPPORTED (features are per-program, not cross-program).
### 6.7.3 Client Implementation
The OpenAPI-generated client is configured with a request interceptor that reads the active program from sessionStorage (Section 5.1) and injects the X-Program-Id header on every request.  List pages that support "All" set the header to * when the user selects the multi-program filter.
The React Query cache key MUST include the X-Program-Id value (either the UUID or *) so that program-scoped and all-program results are never served interchangeably.

# 7. Responsive Behavior
## 7.1 Breakpoints
Tablet-first.  The design tolerates desktop and scales down to floor tablet.
| Breakpoint | Width | Devices | Mode|
|------------|-------|---------|------|
|Desktop | ≥ 1280 px | Engineer workstations | Full sidebar, dense tables|
|Desktop narrow | 1025 – 1279 px | Laptops | Full sidebar, tables compact|
|Tablet landscape | 769 – 1024 px | iPad landscape, Surface landscape | Sidebar collapses to icons|
|Floor (tablet portrait) | ≤ 768 px | iPad portrait (768 px), Surface portrait | Bottom tab bar, floor-optimized controls |
The boundary is inclusive at 768 px.  An iPad in portrait (exactly 768 px wide) renders in Floor Mode.  Floor-mode adaptations are in 7.2.
## 7.2 Floor Mode Adaptations
- Bottom tab bar replaces the sidebar.  Maximum 4 primary tabs plus a More overflow.  Tab allocation is role-specific:
| Role | Primary Tabs | More Contents|
|------|--------------|------------------|
|operator | Dashboard, Work Orders, Materials, Parts | Schedule (if enabled), Chat (if enabled), Profile, Sign Out|
|inspector | Dashboard, Inspections, Work Orders, NCRs | Materials, Schedule (if enabled), Chat (if enabled), Profile, Sign Out|
|engineer | Dashboard, Work Orders, NCRs, Schedule | Inspections, Materials, Parts, Chat, Profile, Sign Out|
|qamanager | Dashboard, Work Orders, NCRs, Inspections | Materials, Parts, Schedule, Chat, Profile, Sign Out|
|programmanager | Dashboard, Work Orders, NCRs, Schedule | Materials, Parts, Chat, Profile, Sign Out|
|admin | (Admin console is desktop-only; floor mode redirects to Dashboard) | — |
- Touch targets use the 56 × 48 px preferred size.
- Typography bumps body text to 18 px and op-card display to 22 px.
- Tables collapse to stacked card lists.
- Forms use full-width inputs and larger labels.
- Safety-critical banner occupies the full viewport width and sticks to the top.

# 8. Accessibility
- WCAG 2.2 Level AA baseline.  Touch targets meet Level AAA (2.5.8).
- Color is never the sole carrier of meaning.  Every status communicated by color is also labeled and / or iconified.
- Keyboard operability on desktop: all interactive elements reachable via Tab; primary action responds to Enter; destructive action requires explicit focus + Enter.
- Screen-reader labels on every icon-only button.  aria-live="polite" on the connection-loss and version-conflict banners.
- Contrast ratios meet 4.5:1 for body text, 3:1 for large text and UI components.
- Focus rings are always visible — no suppressed outlines.
- Glove-compatible touch: confirmed against 48 px minimum and 8 px spacing.

# 9. Safety-Critical Sign-Off and Re-Authentication — STRUCTURED PLACEHOLDER

> **PENDING (R1-ISSUE-019, enriched by R4-001):** This section is the
> central safety mechanism for dual sign-off, re-authentication,
> floor-compatible authentication, and cross-device coordination.
> Its contents require domain input from MSFC QA and BoundState
> security before authoring can be completed.  Proceeding to
> implementation of any safety-critical action without this section
> authored is a hard blocker.

> **Reviewer note (Section 0.2):** This section is a deliberate
> enumeration of open questions pending domain input.  Reviewers:
> evaluate the *completeness* of the question list below (i.e.,
> *"you omit this scenario"*), not the absence of answers.  Do not
> propose answers, do not recommend an authentication method, and
> do not declare the placeholder itself a defect.

**Open questions that MUST be answered before this section is finalized:**

## 9.1 Floor Authentication Method
Pick one (the downstream flows depend on this choice):
- PIN pad — 6-digit PIN entered on a numeric keypad widget.  Low friction for gloved hands, lowest security.
- Badge scan — RFID / barcode badge reader accepting an NFC tap or scan.  Medium friction, medium security.  Requires badge-reader hardware at each workstation.
- Biometric — on-device fingerprint or face on supported tablets.  Medium friction, high security.  Compatibility varies by device.
- Hybrid — primary via badge; PIN fallback.  Higher setup cost but resilient.
## 9.2 Dual Sign-Off Enforcement
- Is the second signer always required to re-authenticate, or can an already-logged-in second signer countersign via an in-session confirmation?
- SOD-003 (dual-signer-distinct): the two signers MUST be different users, enforced server-side.  Does the UI enforce this by pre-filtering the signer picker or by accepting the submission and rejecting with a server error?
- If both signers are present at the same tablet, does the second signer's session replace the first or run as a session overlay?
## 9.3 Network-Failure Recovery
- If connectivity drops after the first sign-off but before the second, does the partial sign-off persist server-side as a pending action?
- If a safety-critical action is submitted, returns success, but the client loses the server response, is a replay safe?  (This interacts with the idempotency-key flow in 11.3.)
- How long does a pending dual sign-off remain valid before the first signer must re-authenticate?  Proposed default: 15 minutes.
## 9.4 Multi-Role Edge Case
A user holding both operator and inspector roles could theoretically sign both halves.  SOD rules forbid this (SOD-001 at a minimum).  How is it surfaced in the UI — pre-emptive block, or submission rejection?
## 9.5 Cross-Device Dual Sign-Off
On the manufacturing floor, the operator and inspector typically carry separate tablets.  The dual-signer workflow must account for the realistic scenario where both signers are not on the same device.
- Pending sign-off visibility: When the first signer (e.g., operator) completes their half of the sign-off, how is the pending action surfaced to the second signer (e.g., inspector) on a different device?  Options: push notification (SSE event on the inspector's active session), entry in the Inspection Queue, or both.
- State representation: Is the partially-signed action a first-class server entity (e.g., pending_signoff record with its own state, timeout, and audit trail), or is it a transient annotation on the operation?
- Race conditions: What happens if the first signer's session expires or they cancel while the second signer is in the process of countersigning?  What if both signers attempt to be "first signer" simultaneously on different devices?
- Timeout and expiry: After the first signature, how long does the second signer have?  What is the UI treatment on the first signer's device while waiting (polling indicator? SSE-driven status update?).
- Completion acknowledgment: After the second signer completes their half, does the first signer's device receive a confirmation (via SSE) that the dual sign-off is complete?

# 10. Performance Targets
| Metric | Target|
|--------|--------|
|First Contentful Paint | < 1.5 s|
|Time to Interactive | < 3.0 s|
|Page navigation (SPA) | < 500 ms|
|Action response (server-confirmed) | < 2.0 s visible feedback|
|Dashboard data refresh | < 1.0 s|
|List page render (50 items) | < 500 ms|
|Operation Execution SSE event-to-render | < 500 ms |

# 11. Error and Edge Case Handling
## 11.1 SoD Violation Messaging
Every SoD violation surfaces the specific rule ID and a human-readable explanation.  The UI prevents the action before the user attempts it where possible (disable button + tooltip) and handles it gracefully if the server rejects it (inline error banner).
## 11.2 Session Expiry
When the access token expires and refresh fails, redirect to login with message: "Your session has expired.  Please sign in again."  Preserve the current URL for post-login redirect.  A dirty-form warning is displayed before redirect per Section 6.2.
## 11.3 Re-Authentication for Safety-Critical Actions (Idempotent, Non-Replay)
When the server returns 401 with code REAUTH_REQUIRED on a safety-critical action, the client presents an inline re-auth modal (not a full-page redirect).
Safe flow — idempotency-key + re-confirmation (replaces prior "automatic replay" design):
1. Before the user submits the safety-critical action, the client generates a per-action idempotency key (UUID v4) and attaches it to the request in the Idempotency-Key header (per build spec §5.15).
1. If the server returns 401 REAUTH_REQUIRED, the client displays an inline modal: "This action requires re-authentication.  Please confirm your identity to continue."  The modal shows the action summary (operation, what will happen) so the user can re-confirm intent.
1. The user re-authenticates (password, PIN, or the method selected in Section 9.1).
1. The client does not automatically replay the original request.  Instead, the modal displays [Confirm and Submit] + [Cancel].  The user must explicitly confirm.
1. On confirm, the client re-submits the request with the same idempotency key plus a fresh access token.  The server validates the idempotency key: if the original submission already committed, the server returns the cached response (200 or 4xx) for duplicate-safe semantics; otherwise the action proceeds.
1. If the user cancels, the action is abandoned and the idempotency key is discarded.
This replaces the prior design in which the client replayed the original request automatically.  Blind replay is unsafe for non-idempotent actions (inspection submission, operation complete, NCR closure); the explicit re-confirmation step plus idempotency-key deduplication eliminates duplicate state transitions.
### 11.3.1 Idempotency-Key Contract
Key format: UUID v4, generated client-side per action attempt.
Uniqueness scope: Each key is bound to the tuple (user_id, endpoint_path, request_body_hash).  The server rejects a key reuse where any element of the tuple differs, returning 422 with code IDEMPOTENCY_KEY_MISMATCH.
Retention: The server retains idempotency keys and their cached responses for a minimum of 24 hours from creation.  Retention storage is durable (database-backed, not in-memory).  After the retention window, the key is evicted and a resubmission with the same key is treated as a new request.
Eviction behavior: After TTL expiry, the key no longer deduplicates.  If a client replays a request with an expired key, the server processes it as new.  The 24-hour window is chosen to exceed any realistic re-authentication, session-recovery, or network-reconnect scenario.
Cross-authentication persistence: Idempotency keys survive token refresh and re-authentication events.  A key generated before REAUTHREQUIRED remains valid after the user re-authenticates, because the key is bound to `userid`, not to a specific access token.
Applicable endpoints: All state-transition endpoints on safety-critical operations, plus any endpoint marked idempotent: true in the OpenAPI spec.  Non-safety-critical PATCH endpoints (e.g., draft edits) use optimistic concurrency (Section 6.3) rather than idempotency keys.
## 11.4 Feature-Flag Revocation Mid-Session
See Section 3.1 → Runtime Configuration Changes.  The absolute "no data lost" claim from the v1.2 spec has been replaced with a defined discard-with-acknowledgment flow; data is not auto-persisted to server or local draft storage.

# 12. Appendix — Changelog
## 12.1 v1.3 Changelog (Oracle Review Session 20260414-053538, Rounds 1–3)
Every accepted defect from Oracle Review session 20260414-053538 is resolved below.  R1-ISSUE-NNN / R2-ISSUE-NNN / R3-ISSUE-NNN refer to the Round and issue number in that review's coordinator decision log.
| Finding | Severity | Resolution|
|---------|----------|------------|
|R1-ISSUE-001 | CRITICAL | Section 6.5 added with full presigned-S3 + SSE contracts (auth, retry, abort, heartbeat, error mapping).|
|R1-ISSUE-002 | CRITICAL | Section 11.3 rewritten — idempotency-key + explicit re-confirmation; automatic replay removed.|
|R1-ISSUE-003 | HIGH | Section 5.1 defines the program selector as the default scope; Section 5.2 explicitly lists pages that support an All (assigned programs) multi-program filter; cache keys include program scope.|
|R1-ISSUE-004 | HIGH | Section 3.2 defines auto-switch behavior on cross-program entity routes; 404-not-403 rule retained.|
|R1-ISSUE-005 | HIGH | Section 6.3 authors the full Optimistic Concurrency Contract (version semantics, strong ETag, 409 shape, composite mutations, conflict-resolution UX).|
|R1-ISSUE-006 | HIGH | Section 7.2 defines the 4+More overflow pattern with per-role tab allocation.|
|R1-ISSUE-007 | HIGH | Section 6.2 replaces HEAD with a sparse-fieldset GET declared in the OpenAPI spec.|
|R1-ISSUE-008 | HIGH | Section 3.1 Role-Based Navigation Visibility grants qa_manager read queue access to Inspections; role definition aligned.|
|R1-ISSUE-009 | HIGH | Section 5.4.6 defines the predecessor graph model (DAG), release-time validation, error codes, and break-link recovery.|
|R1-ISSUE-010 | HIGH | Section 5.6.3 defines standalone NCR closure preconditions per disposition type.|
|R1-ISSUE-011 | HIGH | Section 6.6 adds the Audit Trail API (event shape, endpoints, filtering, integrity status).|
|R1-ISSUE-012 | HIGH | Section 5.5.2 freezes the Phase 1 criterion type enum; unknown types render as an unsupported-state error that blocks submission.|
|R1-ISSUE-013 | MEDIUM | All role identifiers standardized to snakecase (`qamanager, programmanager`) throughout.|
|R1-ISSUE-014 | MEDIUM | All hold state identifiers standardized to `onhold (state machines + UI visibility).|
|R1-ISSUE-015 | MEDIUM | Section 7.1 redefines the floor breakpoint as ≤ 768 px inclusive; tablet landscape starts at 769 px.|
|R1-ISSUE-016 | UNRESOLVED | Section 5.3 Closure-as-Attestation callout — regulatory validation required before implementation. **Open blocker.**|
|R1-ISSUE-017 | HIGH (R3) | Section 6.2 + 5.4.10 — Operation Execution uses SSE (Section 6.5.2), not polling, for sub-second updates on safety-critical state. Polling retained for dashboard and lists.|
|R1-ISSUE-019 | CRITICAL | Section 9 inserted as a structured placeholder with open questions. **Open blocker — requires domain input.**|
|R2-ISSUE-001 | CRITICAL | Section 6.3 mandates strong ETags; If-Match and version-in-body both explicitly allowed; weak ETags forbidden.|
|R2-ISSUE-002 | CRITICAL | Section 6.3.3 defines composite-mutation concurrency (version_assertions map, per-entity conflicts).|
|R2-ISSUE-003 | CRITICAL | Section 5.3 Work Order Hold Cascading defines the parent-child hold semantics; Section 5.4.1 adds OP-RESUME-001 and OP-COMP-004.|
|R2-ISSUE-004 | HIGH | Section 5.5.5 adds the Inspection Queue (/inspections) with filters and drill-throughs.|
|R2-ISSUE-005 | HIGH | Section 5.4.6 release-time DAG validation, specific error codes (WO-REL-101..104).|
|R2-ISSUE-006 | HIGH | Engineer operation hold/resume surface added at /work-orders/:id/ops/:opId (Operation Detail) distinct from Operation Execution; Nav matrix updated.|
|R2-ISSUE-007 | HIGH | Section 5.5.5 post-completion Resolve Conditional Finding flow; Section 5.3 WO Complete accepts conditionallysatisfied`; Inspection Queue exposes `conditional` and `overdue` filters.|
|R2-ISSUE-008 | HIGH | Section 5.6.5 Part Scrap Transition defines the QA-gated `[Mark Part as Scrapped]` action, audit events, and NCR close linkage.|
|R2-ISSUE-009 | HIGH | Section 5.6.4 Completion Evidence adds required attachments for rework and repair dispositions; closure blocked on missing evidence.|
|R2-ISSUE-010 | HIGH | Section 5.4.7 Drag-and-Drop Reorder Semantics specifies the dependency-preservation modal for operations with explicit predecessors.|
|R2-ISSUE-011 | HIGH | Section 5.4.3 Hold Point Status — server-computed field; client MUST NOT traverse client-side.|
|R2-ISSUE-012 | HIGH | Section 5.11 Schedule page spec added (route, layout, write workflows, embed).|
|R2-ISSUE-013 | HIGH | Section 5.9.3 Chat Authorization Model — server-side role + program enforcement on query templates and live-data citations.|
|R2-ISSUE-014 | MEDIUM | Section 3.1 Multi-role permission composition — union rule, SoD independent.|
|R2-ISSUE-015 | MEDIUM | Section 6.2 Idle timeout — 5-minute warning modal, dirty-form interaction defined.|
|R3-ISSUE-001 | HIGH | Section 5.4.2 Start vs. Resume-Labor Contracts — two distinct endpoints, shared UI label.|
|R3-ISSUE-002 | HIGH | Section 5.4.9 OP-COMP-002 is operation-wide; blocking-user display + engineer-stop action.|
|R3-ISSUE-003 | HIGH | Section 3.2 canonical fetch-then-authorize pattern for entity routes.|
|R3-ISSUE-004 | HIGH | Section 5.4.8 operation response includes `predecessorsummaries` array.|
|R3-ISSUE-005 | MEDIUM | Section 3.1 Runtime Config Changes — absolute "no data lost" replaced with Copy My Changes / Discard pattern.|
|R3-ISSUE-006 | MEDIUM | Section 5.1 program selector persistence — sessionStorage primary, localStorage only as initial-tab hint. |
## 12.2 v1.4 Changelog (Oracle Review Round 4)
| Finding | Severity | Resolution|
|---------|----------|------------|
|R4-001 (cross-device sign-off) | CRITICAL | Section 9.5 added with cross-device dual-signer open questions (pending-signoff visibility, race conditions, timeout, completion acknowledgment).  Enriches existing R1-ISSUE-019 placeholder.|
|R4-002 (WO hold force-stop) | CRITICAL | Section 5.3 Hold Cascading updated with force_stop_labor flag.  Stop-work holds atomically stop all active labor entries.  New audit event labor.stopped_by_hold added to Section 6.6.1.  Hold confirmation dialog includes stop-work checkbox.|
|R4-003 (post-completion Reject deadlock) | CRITICAL | Section 5.4.1 adds Operation Reopen transition (completed → active) triggered by NCR rework/repair disposition.  Section 5.3 adds WO Reactivate transition (completed → active).  Section 5.5.5 updated with reopen flow.  Section 5.6.2 NCR Disposition documents the side-effect.  OP-COMP-003 rewritten with precise NCR-state checks.  New audit events operation.reopened, work_order.reactivated.|
|R4-004 (SSE reconnect contradiction) | CRITICAL | Section 6.5.2 rewritten: removed at-least-once replay durability claim; server maintains best-effort in-memory buffer; client performs mandatory REST refetch on every reconnect; buffer_reset event defined.|
|R4-005 (dual handoff endpoints) | HIGH | Section 5.4.2 designates POST /api/operations/:opId/labor as the canonical endpoint with optional version_assertions.  Section 6.3.3 example updated to use the canonical endpoint.  POST /api/labor-entries/handoff removed.|
|R4-006 (undeclared WO state) | HIGH | released_pending_graph_fix removed.  Section 5.3 adds Recall transition (released → draft).  Section 5.4.6 replaces break-link recovery with Recall (pre-activation) and on-hold graph fix (post-activation).  WO-REL-104 removed (redundant with WO-REL-103 in valid DAG).  Formal reachability invariant defined.  [Fix Graph] action and work_order.graph_fixed audit event added.|
|R4-007 (program context wire contract) | HIGH | Section 6.7 added: X-Program-Id header contract with single-program, all-program (*), and omitted semantics.  Server enforcement rules.  Client interceptor pattern.  Cache-key inclusion requirement.|
|R4-008 (engineer op detail page) | HIGH | Section 5.4.11 added: Engineer Operation Detail page with layout, data panels, role-switcher for multi-role users, and action matrix (Hold, Resume, Stop Timer, Correct Time).|
|R4-009 (idempotency key rules) | HIGH | Section 11.3.1 added: idempotency-key uniqueness scope (userid + endpoint + body hash), 24-hour durable retention, eviction behavior, cross-authentication persistence, applicable endpoints.|
|R4-010 (floor-mode Schedule) | MEDIUM | Section 7.2 floor-mode tab table updated: Schedule added to operator and inspector `More` contents (gated on `scheduleenabled`).|
|R4-011 (dirty-form nested versioning) | HIGH | Section 6.2 adds aggregate versioning definition for work-order draft editor.  Section 6.3.1 adds aggregate versioning rule.  Parent WO version increments on child-operation mutations.|
|R4-012 (offline write-disable risk) | UNRESOLVED | Section 6.4 updated with explicit unresolved-question callout.  Requires domain input on floor connectivity realities.  Open question — not a blocker but an acknowledged operational risk.|
|R4-013 (NPR 8735.2C provisional annotations) | MEDIUM | Sections 5.5.4, 5.5.5, 5.6.3 now include cross-reference annotations noting their dependence on the R1-ISSUE-016 regulatory gate.  Section 5.3 Closure-as-Attestation callout updated with explicit list of dependent sections. |
Three items remain unresolved:
- R1-ISSUE-016 — NPR 8735.2C compliance for closure-as-attestation (Sections 5.3 + 5.6).  Requires external NASA/MSFC review.  Hard blocker for closure-flow implementation.
- R1-ISSUE-019 (enriched by R4-001) — Section 9 safety-critical sign-off, now including cross-device coordination questions.  Requires domain input.  Hard blocker for safety-critical-action implementation.
- R4-012 — Offline write-disable production risk.  Requires domain input on floor connectivity.  Acknowledged risk; not a hard blocker but should be resolved before floor deployment.

This communication and its attachments are privileged and confidential.  Generated by ORACLE — BoundState AI LLC.



This communication and its attachments are privileged and confidential. Generated by ORACLE — BoundState AI LLC.
