---
title: FORGE-MFG Frontend UX Specification
version: 1.2.0
author: Dr. Joseph Wilde / Beth (Claude Opus 4.6)
date: 2026-04-14
classification: BoundState Proprietary
status: DRAFT — Merged (Oracle Review R2 sections 1–6 + original sections 7–11)
implements: FORGE_MFG_BUILD_SPEC v1.4.2, Section 5B Frontend
---

> **Merge note.** Sections 1–6 incorporate the Oracle Review Round 2
> expansions dated 2026-04-13 (API error codes, atomic labor-timer
> semantics, evidence attachment lifecycle, permission matrices,
> route authorization contract, closure-as-attestation).
> Sections 7–11 carry forward unchanged from the v1.1 original spec —
> the Oracle Review Round 2 output was truncated mid-Section 6.2 and
> never covered these sections.  They should be run through Oracle
> Review Round 3.

# 1. Design Philosophy
## 1.1 Core Principle
FORGE-MFG is a manufacturing execution system used on a rocket engine production floor. Its primary interface is a transactional work surface where operators execute safety-critical manufacturing operations with gloved hands, inspectors approve or reject work in real time, and engineers manage the flow of production across multiple engine builds.
The system also provides role-appropriate monitoring surfaces — dashboards, schedule views, and status summaries — that serve oversight roles (QA managers, program managers) without compromising the transactional clarity of the operator and inspector interfaces. These monitoring views are deliberate and constrained; they exist to support production decisions, not to serve as general-purpose analytics or executive reporting tools.
The UI must be calm, legible, and unambiguous. Every interaction has consequences — a misplaced tap on "Complete" instead of "Hold" can cascade through the schedule. The design must make the right action obvious and the wrong action difficult.
## 1.2 Design Direction
Industrial utilitarian. Clean, high-contrast, no decoration. Think aircraft cockpit instrument panels — every element serves a function, nothing is ornamental. Color is used for status communication, not branding. Typography is large and readable at arm's length. Touch targets are generous.
This is not a consumer app. It does not need to be delightful. It needs to be trustworthy.
## 1.3 Design Constraints
- Must work on tablets (iPad, Surface) used on the manufacturing floor — touch-first
- Must work on desktop monitors in engineering offices — mouse and keyboard efficient
- Must be readable at arm's length (operator holding a tablet while standing at a workstation)
- Must function under fluorescent shop lighting (high contrast, no subtle gradients)
- GovCloud deployment — no CDN, no external font loading, fonts bundled in build
- React 18 + TypeScript, Zustand or React Query, Recharts
- All data fetching through OpenAPI-generated client with proper loading/error states. Architectural exceptions: presigned S3 uploads (Section 5.5) and SSE streaming (Section 5.9) operate outside the generated client; see Section 6.5 for their integration contracts.

# 2. User Personas
## 2.1 Operator — "Hands on the Hardware"
Who: Technicians, welders, brazers working directly on engine hardware. 60% of all users.
Environment: Standing at workstations on the manufacturing floor. Wearing gloves (nitrile or leather). Tablet mounted on a stand or held in one hand. Fluorescent lighting. Noisy environment — no audio cues.
Primary tasks: Start/stop operations, log labor time, record material consumption, mark operations complete, view work instructions.
What they need: Large touch targets (minimum 48px, prefer 56px). Minimal typing — dropdowns and selectors over text fields. Current operation status always visible. One-tap access to their active work order. Never more than 2 taps from their most common action.
What they fear: Accidentally completing an operation they're not done with. Losing labor time entries. Being blocked by an inspection hold they don't understand.
## 2.2 Inspector — "Quality Gate"
Who: QA inspectors who verify work, record measurements, accept or reject operations. 20% of users.
Environment: Walking between workstations with a tablet. May need to photograph evidence. Reviewing measurement data and comparing against criteria.
Primary tasks: View pending inspections, record inspection results with measurements, upload evidence photos, sign off on operations, initiate NCRs for rejected work.
What they need: A filtered view of "what's waiting for me." Measurement entry forms that support decimal precision. Camera integration for evidence capture. Clear indication of which operations are safety-critical (dual sign-off required). Ability to see the operator's labor entries to verify work was performed (but cannot be the same person — SoD).
What they fear: Missing a safety-critical hold point. Signing off on work they haven't verified. Accidentally approving when they meant to reject.
## 2.3 Engineer — "Production Manager"
Who: Manufacturing engineers who create work orders, define operations, manage the production flow. 15% of users.
Environment: Desktop monitors in an office adjacent to the floor. Frequent context-switching between the system and engineering documents. May walk to the floor with a laptop.
Primary tasks: Create and release work orders, define operations with work instructions, disposition NCRs, manage schedules, review build status across multiple engine programs.
What they need: Dense information display — they want to see 20 operations at once, not 3 cards. Keyboard-efficient workflows (tab between fields, enter to submit). Drag-and-drop operation reordering. Side-by-side views (work order + schedule, NCR + operation detail). Bulk actions where appropriate.
What they fear: Releasing a work order with missing operations or wrong document references. Schedule impact from an NCR they haven't noticed.
Phase 2: What-if schedule analysis (see Section 10).
## 2.4 QA Manager — "Final Authority"
Who: Quality assurance managers who close work orders and NCRs, review audit trails, monitor compliance. 3% of users.
Primary tasks: Close work orders (final QA closure), close NCRs, hold/resume work orders for quality concerns, review dashboard for open items, verify audit trail integrity.
What they need: Dashboard-first experience. Open NCR count, overdue inspections, unresolved conditional findings, build status at a glance. Ability to drill from dashboard to specific entity. Audit trail viewer with filtering.
## 2.5 Program Manager — "Read-Only Overview"
Who: Program managers who monitor progress but don't execute manufacturing actions. 2% of users.
Primary tasks: Monitor production progress, review schedule status, track build milestones, review work order and NCR status at a summary level.
What they need: Read-only access to production data, scoped to their assigned programs. Dashboard and schedule are their primary workspace, but they require navigational access to work orders, NCRs, materials, and parts for context when investigating status. No write actions. Program selector locked to assigned programs only.
## 2.6 Admin — "System Operator"
Who: BoundState or MSFC system administrators. <1% of users.
Primary tasks: User management, role assignment, program membership, system health monitoring, dead-letter queue management.
What they need: Admin panel separated from manufacturing UI. User list with role/program editing. System health indicators. Audit integrity status.

# 3. Information Architecture
## 3.1 Navigation Model
Top-level navigation is a persistent left sidebar (desktop) or bottom tab bar (tablet/mobile). Navigation items are role-filtered — operators don't see admin, program managers don't see operation execution.
### Role Definitions
| Role | Description|
|------|-------------|
|operator | Floor execution — start/stop operations, log labor, consume materials, create parts|
|inspector | Quality verification — record inspections, receive/accept materials, initiate NCRs|
|engineer | Production management — create/edit work orders, manage operations and schedules, disposition NCRs|
|qa_manager | Quality oversight — hold/resume/close work orders, close NCRs, all inspector-level read access|
|program_manager | Program monitoring — read-only access to production data, scoped to assigned programs|
|admin | System administration — user management, program configuration, system health |
Roles are not hierarchical (an engineer does not inherit operator permissions). Each role grants a distinct permission set. Users may hold multiple roles where organizationally appropriate.
### Navigation Structure
Dashboard [all roles]
Work Orders [all roles — read; engineer — create/edit/manage; qa_manager — hold/resume/close]
└── Work Order Detail
    ├── Operations List
    ├── Operation Execution [operator]
    ├── Inspections [inspector]
    └── Schedule ¹
NCRs [all roles — read; inspector, engineer — create; engineer — disposition; qa_manager — close]
└── NCR Detail
Materials [inspector — receive/accept; operator — consume; all others — read]
Parts [operator — create; all — trace]
Chat ²
Admin [admin only]
├── Users
├── Programs
├── System Health
└── Dead Letters
¹ Schedule is visible when the program has an active schedule configuration. If no schedule is configured, the Schedule tab does not appear. Read access for all non-admin roles; write access for engineer.
² Chat is visible when the program has oracle_access enabled in program configuration. Available to all non-admin roles. If oracle_access is not enabled, the Chat navigation item does not appear.
### Runtime Configuration Changes
If a program configuration flag changes while a user is in an active session (e.g., oracle_access is disabled, or a schedule configuration is removed), the affected navigation item is removed on the next data polling cycle. If the user is currently viewing the affected page, they are redirected to the Dashboard with a toast notification: "This feature is no longer available for the selected program." No unsaved data is lost — dirty form protections (Section 6.2) apply before redirect.
### Role-Based Navigation Visibility
| Page | operator | inspector | engineer | qamanager | programmanager | admin|
|------|----------|-----------|----------|------------|-----------------|-------|
|Dashboard | ✓ | ✓ | ✓ | ✓ | ✓ | ✓|
|Work Orders | read | read | ✓ | read+close ³ | read | read|
|Operation Execution | ✓ | — | — | — | — | —|
|Inspections | — | ✓ | — | — | — | —|
|NCRs | read | ✓ | ✓ | ✓ | read | read|
|Materials | consume | ✓ | read | read | read | —|
|Parts | ✓ | read | read | read | read | —|
|Schedule ¹ | read | read | ✓ | read | read | —|
|Chat ² | ✓ | ✓ | ✓ | ✓ | ✓ | —|
|Admin | — | — | — | — | — | ✓ |
Legend: ✓ = full access for this role's scope; read = read-only access; consume/create = specific action access; — = no access.
³ qa_manager has read access to all work orders plus hold/resume actions on active work orders and close action on completed work orders. QA managers cannot create or edit work order drafts.
## 3.2 URL Structure
/ → Dashboard
/work-orders → Work Order List
/work-orders/:id → Work Order Detail (tabbed: operations, schedule, documents)
/work-orders/:id/ops/:opId → Operation Execution
/work-orders/:id/ops/:opId/inspect → Inspection Form
/ncrs → NCR List
/ncrs/:id → NCR Detail
/materials → Material Certification List
/materials/:id → Material Detail
/parts → Part Instance List
/parts/:id → Part Detail + Traceability
/chat → ORACLE Chat
/admin → Admin Dashboard
/admin/users → User Management
/admin/users/:id → User Detail + Role/Program Assignment
/admin/programs → Program Management
/admin/health → System Health
### Route Authorization
Navigation filtering (hiding menu items) is a UX convenience, not a security control. All routes enforce authorization independently:
- Client-side route guards check the user's role and program membership before rendering a page. If the user navigates directly to a URL they are not authorized to access (via bookmark, shared link, or URL manipulation), the client redirects to the Dashboard and displays a toast notification: "You do not have access to this page."
- Server-side authorization is enforced on every API request independently of the client. Even if a client-side guard is bypassed, the API returns 403 Forbidden for unauthorized operations. The frontend displays 403 responses as an inline error banner: "You are not authorized to perform this action."
- Cross-program access: If a user navigates to an entity belonging to a program they are not assigned to, the API returns 404 (not 403) to avoid leaking the existence of cross-program entities. The frontend displays the standard "not found" empty state.

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
|Safety-critical | --safety-critical-bg background with --safety-critical-border | ⚠ Warning triangle | "SAFETY-CRITICAL" (uppercase, bold) | Double border, 2px|
|Error / Rejected | --status-error background | ✕ Circle-X | Status-specific text (e.g., "Rejected", "Failed") | Single border, 1px |
### Dark Mode
Not Phase 1. Manufacturing floor lighting is bright fluorescent — dark mode is a Phase 2 consideration for office users (see Section 10).
## 4.2 Typography
Bundled in build — no external font loading (GovCloud constraint).
| Element | Font | Size | Weight | Line Height|
|---------|------|------|--------|-------------|
|Page title | System sans-serif | 24px | 600 | 1.3|
|Section header | System sans-serif | 18px | 600 | 1.3|
|Body text | System sans-serif | 16px | 400 | 1.5|
|Table text | System sans-serif | 14px | 400 | 1.4|
|Label | System sans-serif | 14px | 500 | 1.3|
|Caption/timestamp | System sans-serif | 12px | 400 | 1.3|
|Status badge | System sans-serif | 12px | 600 | 1.0|
|Floor display (op card) | System sans-serif | 20px | 500 | 1.4 |
System font stack: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif
This is deliberate. System fonts render fastest, require no download, and are optimized for each platform's display characteristics. On the manufacturing floor, load time and rendering reliability trump font aesthetics.
## 4.3 Spacing and Layout
Base unit: 8px. All spacing is a multiple of 8.
| Token | Value | Usage|
|-------|-------|-------|
|--space-xs | 4px | Inline padding, badge padding|
|--space-sm | 8px | Between related elements|
|--space-md | 16px | Between sections, card padding|
|--space-lg | 24px | Between major sections|
|--space-xl | 32px | Page padding, major gaps|
|--space-2xl | 48px | Section separation |
### Touch Targets
- Minimum touch target: 48px × 48px (WCAG 2.5.8 Level AAA)
- Preferred touch target for floor use: 56px × 48px
- Spacing between touch targets: minimum 8px
- Primary action buttons: full width on mobile/tablet, 200px minimum on desktop
## 4.4 Component Library
### Status Badge
Pill-shaped, colored by status. Used everywhere status is displayed. Each badge includes a text label — color is never the sole differentiator.
[● Draft] gray background, dark text
[● Active] blue background, white text
[● On Hold] amber background, dark text
[● Complete] green background, white text
[✕ Rejected] red background, white text
[● Rework] purple background, white text
### Safety-Critical Banner
Used on operations and inspections flagged as safety-critical. Distinct from error banners:
┌══════════════════════════════════════════════════════════┐
║ ⚠ SAFETY-CRITICAL — Dual sign-off required              ║
╚══════════════════════════════════════════════════════════╝
Rendered with: --safety-critical-bg background, --safety-critical-border double border, ⚠ triangle icon, uppercase "SAFETY-CRITICAL" text label. This banner is persistent — it is pinned to the top of the view and never scrolls off screen.
### Action Buttons
Three tiers:
- Primary: Filled blue. One per view maximum. The "do the thing" button (Start Operation, Submit Inspection, Release Work Order).
- Secondary: Outlined. Supporting actions (Add Labor, View Details).
- Destructive: Filled red. Requires confirmation dialog. (Reject, Void NCR).
### Confirmation Dialogs
Required for:
- All state transitions (complete, release, close, hold, rework)
- All destructive actions (reject, void, delete draft)
- Safety-critical sign-offs (additional re-authentication step — see Section 9)
Dialog structure:
┌─────────────────────────────────────┐
│ Complete Operation?                 │
│                                     │
│ You are completing Op 15: Weld      │
│ on WO-2026-0042 (RS25 Engine 4)    │
│                                     │
│ This action cannot be undone.       │
│                                     │
│ [Cancel]              [Complete]    │
└─────────────────────────────────────┘
For safety-critical operations, the confirmation dialog is followed by a re-authentication step. See Section 9 for the complete safety-critical sign-off flow including floor-compatible authentication.
### Empty States
Every list view has a meaningful empty state:
┌─────────────────────────────────────┐
│                                     │
│     No pending inspections          │
│                                     │
│     All operations in your programs │
│     are either complete or not yet  │
│     at an inspection hold point.    │
│                                     │
└─────────────────────────────────────┘
### Error States
API errors surface as inline banners, not modals. Banners persist until dismissed or the error condition clears.
┌─ ✕ ─────────────────────────────────┐
│ Could not complete operation.       │
│ Inspection hold not satisfied       │
│ (COMP-001).                         │
│                                     │
│ An accepted inspection is required  │
│ before this operation can be        │
│ completed. [✕]                      │
└──────────────────────────────────────┘
SoD violations get specific messaging:
┌─ 🚫 ────────────────────────────────┐
│ Separation of duties violation.     │
│ You cannot inspect an operation     │
│ where you have labor entries        │
│ (SOD-001). [✕]                      │
└──────────────────────────────────────┘
### Loading States
Skeleton screens for initial page loads. Inline spinners for action submissions. Never blank screens.

# 5. Page Specifications
## 5.1 Dashboard
Purpose: At-a-glance production status. First thing every user sees.
### Layout
┌─────────────────────────────────────────────────────────┐
│ FORGE-MFG              [Program: RS25 ▼] [Jane Smith ▼]│
├─────────────────────────────────────────────────────────┤
│                                                         │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐    │
│ │ Active   │ │ On Hold  │ │ Open     │ │ Behind  │    │
│ │ WOs: 12  │ │ WOs: 2   │ │ NCRs: 5  │ │ Sched: 1│    │
│ └──────────┘ └──────────┘ └──────────┘ └─────────┘    │
│                                                         │
│ Builds by Engine                                        │
│ ┌──────────────────────────────────────────────────┐   │
│ │ RS25 Engine 4  [████████░░] 78%   On Track       │   │
│ │ RS25 Engine 5  [████░░░░░░] 42%   At Risk        │   │
│ │ SLS Adapter 2  [██░░░░░░░░] 21%   On Track       │   │
│ └──────────────────────────────────────────────────┘   │
│                                                         │
│ Open NCRs by Age            Schedule Status             │
│ ┌──────────────────────┐   ┌────────────────────────┐  │
│ │ < 7 days: 3          │   │ [Gantt summary]        │  │
│ │ 7-30 days: 1         │   │                        │  │
│ │ > 30 days: 1 ⚠       │   │                        │  │
│ └──────────────────────┘   └────────────────────────┘  │
│                                                         │
│ Unresolved Conditional Findings: 2 ⚠ (qa_manager view) │
│                                                         │
└─────────────────────────────────────────────────────────┘
### Behavior
- Summary cards are clickable — drill to filtered list
- Program selector scope: The dropdown is populated from the user's assigned programs (from program_memberships). Program managers see only their assigned programs; the selector is locked to those programs. Engineers and QA managers see all programs they are assigned to. Admins see all programs. The selected program is sent as a query parameter on all data-fetching requests; the server enforces program scope independently — the selector cannot offer programs the user is not assigned to. Default selection: the user's most recently active program, persisted in localStorage.
- Build progress bars link to work order detail
- NCRs over 30 days get an amber warning indicator
- QA manager dashboard additions: Unresolved conditional findings count with drill-through to filtered inspection list. Overdue inspection count.
- Data refreshes on 60-second polling interval (not WebSocket — spec says polling is sufficient)
## 5.2 Work Order List
Purpose: Find and manage work orders.
### Layout
Table view with filters at top. Sortable columns.
┌─────────────────────────────────────────────────────────┐
│ Work Orders                              [+ New WO]     │
├─────────────────────────────────────────────────────────┤
│ Status: [All ▼]  Program: [All ▼]  Phase: [All ▼]      │
│ Search: [________________]                              │
├─────────────────────────────────────────────────────────┤
│ WO#            │ Engine   │ Module │ Status  │ Ops      │
│ WO-2026-0042   │ RS25 #4  │ MCC    │ Active  │ 12/47    │
│ WO-2026-0041   │ RS25 #4  │ Nozzle │ Hold    │ 8/32     │
│ WO-2026-0040   │ RS25 #5  │ TPA    │ Draft   │ 0/28     │
│ ...            │          │        │         │          │
├─────────────────────────────────────────────────────────┤
│ Showing 1-20 of 47                   [← 1 2 3 →]       │
└─────────────────────────────────────────────────────────┘
### Behavior
- Rows clickable to work order detail
- Status badges use standard color coding
- "Ops" column shows completed/total
- Create button visible only for engineer role
- Pagination per spec (page_size default 50, max 200)
## 5.3 Work Order Detail
Purpose: Complete view of a single work order with all related data.
### Layout
Tabbed interface: Operations | Documents | Schedule | Audit Trail
┌─────────────────────────────────────────────────────────┐
│ ← Work Orders            WO-2026-0042                   │
│ RS25 Engine 4 — MCC — Fabrication          [● Active]   │
│ Created by J. Smith │ Released 2026-03-15               │
├─────────────────────────────────────────────────────────┤
│ [Operations] [Documents] [Schedule] [Audit Trail]       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ Op  │ Description          │ Status   │ Hold │ Safety   │
│ 01  │ Machine housing      │ Complete │ —    │ —        │
│ 02  │ Weld injector ports  │ Complete │ ✓    │ —        │
│ 03  │ NDI weld inspection  │ Active   │ ✓    │ ⚠ SC     │
│ 04  │ Braze fuel manifold  │ Pending  │ ✓    │ —        │
│ 05  │ Pressure test        │ Pending  │ ✓    │ ⚠ SC     │
│ ...                                                     │
│                                                         │
│ ──── Actions ────────────────────────────               │
│ [Hold Work Order]    [Complete Work Order]               │
│ [Close Work Order] ← qa_manager only, completed WOs    │
│                                                         │
└─────────────────────────────────────────────────────────┘
### Behavior
- Operation rows expand inline to show labor entries, inspection events, material consumption
- Safety-critical operations marked with ⚠ triangle icon and "SC" label (not color alone)
- Hold point column shows inspection requirement
- Draft work orders show edit controls and operation authoring (add, reorder, delete)
- Released work orders show execution controls only
- Completed work orders show close action (qa_manager only)
- Closed work orders are fully read-only — no action buttons
- Action buttons are role-gated and state-appropriate (see state machine below)
### Work Order State Machine
| Transition | From | To | Permitted Roles | Preconditions|
|-----------|------|----|-----------------|---------------|
|Release | draft | released | engineer | At least one operation defined; all operations have work instruction references|
|Start | released | active | system (automatic) | First operation started by an operator|
|Hold | active | onhold | engineer, qamanager | —|
|Resume | onhold | active | engineer, qamanager | —|
|Complete | active | completed | engineer | All operations complete; all inspection hold points satisfied; no open NCRs linked to this work order|
|Close | completed | closed | qa_manager | All NCRs dispositioned and closed; all conditional inspection findings resolved (no unresolved conditional inspections on any operation); all operations complete |
Note on closure attestation: The Close action itself constitutes the QA manager's attestation that they have reviewed the work order and its audit trail. The system records the actor, timestamp, and entity version at closure time. No separate "review" checkbox or attestation form is required — the act of clicking Close (with confirmation dialog) after all server-computable preconditions are met IS the attestation event.
### Work Order Permission Matrix
| Action | operator | inspector | engineer | qamanager | programmanager|
|--------|----------|-----------|----------|------------|------------------|
|View | ✓ | ✓ | ✓ | ✓ | ✓ (own programs)|
|Create | — | — | ✓ | — | —|
|Edit draft | — | — | ✓ | — | —|
|Release | — | — | ✓ | — | —|
|Hold | — | — | ✓ | ✓ | —|
|Resume | — | — | ✓ | ✓ | —|
|Complete | — | — | ✓ | — | —|
|Close | — | — | — | ✓ | — |
UI button visibility by state:
| Work Order State | Visible Actions|
|-----------------|----------------|
|draft | [Release Work Order] (engineer only)|
|released | No actions — waiting for operator to start first operation|
|active | [Hold Work Order] (engineer, qamanager) [Complete Work Order] (engineer only) — Complete disabled with tooltip if preconditions unmet|
|onhold | [Resume Work Order] (engineer, qamanager)|
|completed | [Close Work Order] (qamanager only) — disabled with tooltip if preconditions unmet|
|closed | No actions — fully read-only |
## 5.4 Operation Execution (Operator View)
Purpose: The operator's primary work screen. Execute a single operation.
### Operation State Machine
| Transition | From | To | Permitted Roles | Preconditions | API Error Code|
|-----------|------|----|-----------------|---------------|----------------|
|Start | pending | active | operator | Work order is released or active; preceding sequential operations complete (if operation ordering is enforced) | OP-START-001 (WO not released), OP-START-002 (predecessor incomplete)|
|Hold | active | onhold | operator, engineer | — | —|
|Resume | onhold | active | operator, engineer | — | —|
|Complete | active | completed | operator | All labor entries stopped; if hold point exists, inspection accepted or conditionally accepted; no unresolved reject-without-rework NCRs on this operation | OP-COMP-001 (hold point unsatisfied), OP-COMP-002 (active labor entry), OP-COMP-003 (blocking NCR) |
Starting an operation: When the operator navigates to a pending operation and taps [Start Timer], the server atomically (a) transitions the operation from pending to active, (b) creates a labor entry with start_time, and (c) if this is the first operation started on a released work order, transitions the work order from released to active. The client displays the operation as Active and starts the visual timer.
Inspection-type operations: Operations that are themselves inspection activities (e.g., "NDI Weld Inspection") follow the same state machine. The operator (technician performing the NDI activity) starts and completes the operation. The inspector records the inspection result. The operation can only be completed after the inspector's hold point is satisfied. The operator completing the operation is attesting that the physical inspection activity is finished.
UI button visibility by operation state:
| Operation State | Visible Actions|
|----------------|----------------|
|pending | [Start Timer] — starts operation and labor timer atomically|
|active | [Stop Timer] / [Start Timer] (toggle based on active labor entry), [Put On Hold], [Complete Operation]|
|on_hold | [Resume Operation]|
|completed | No actions — read-only |
### Layout — Optimized for Tablet
┌─────────────────────────────────────────────────────────┐
│ Op 03: NDI Weld Inspection               [● Active]    │
│ WO-2026-0042 │ RS25 Engine 4 │ MCC                     │
╔═════════════════════════════════════════════════════════╗
║ ⚠ SAFETY-CRITICAL — Dual sign-off required              ║
╚═════════════════════════════════════════════════════════╝
├─────────────────────────────────────────────────────────┤
│                                                         │
│ ┌─── Work Instruction ──────────────────────────────┐  │
│ │ MSFC-WI-2024-0187 Rev C                           │  │
│ │ "NDI Ultrasonic Inspection of LOX Manifold Welds" │  │
│ │ [View Document]                                   │  │
│ └───────────────────────────────────────────────────┘  │
│                                                         │
│ ┌─── Tooling Required ─────────────────────────────┐  │
│ │ UT Probe (2.25 MHz) │ Couplant │ Calibration     │  │
│ └───────────────────────────────────────────────────┘  │
│                                                         │
│ ┌─── My Labor ─────────────────────────────────────┐  │
│ │ Started: 09:14 AM          │ Elapsed: 2h 34m     │  │
│ │ [Stop Timer]                                     │  │
│ └───────────────────────────────────────────────────┘  │
│                                                         │
│ ┌─── Materials Used ───────────────────────────────┐  │
│ │ AMS 5662 Lot 2024-A │ 0.5 lb consumed            │  │
│ │ [+ Add Material]                                 │  │
│ └───────────────────────────────────────────────────┘  │
│                                                         │
│ ┌─── Inspection Status ────────────────────────────┐  │
│ │ ⏳ Awaiting inspection (hold point)               │  │
│ └───────────────────────────────────────────────────┘  │
│                                                         │
│ [Put On Hold]                [Complete Operation]       │
│                                                         │
└─────────────────────────────────────────────────────────┘
When operation is in pending state, the My Labor section shows:
│ ┌─── My Labor ─────────────────────────────────────┐  │
│ │ No active labor entry                             │  │
│ │ [Start Timer]                                     │  │
│ └───────────────────────────────────────────────────┘  │
### Behavior
- Timer runs visually (updated every second) when labor entry is active
- Material consumption shows running balance from the cert
- Complete button disabled and grayed when inspection hold is unsatisfied (with tooltip explaining why)
- Safety-critical banner is persistent and prominent — never scrollable off screen (uses ⚠ icon and double border per Section 4.1)
- Confirmation dialog on complete includes the hold/sign-off status summary
### Labor Timer Model
Labor timers are server-authoritative. The client is a display; the server is the source of truth.
- Start event: Immediately persisted to server via POST. Server records start_time and returns the labor entry ID. If the operation is in pending state, the server atomically transitions it to active. Client computes elapsed time locally from start_time for responsive display.
- Stop event: Immediately persisted to server via PATCH. Server records end_time and computes duration. Client updates display from server response.
- One active timer per user (atomic handoff): The server enforces that a user can have at most one active (unstopped) labor entry at any time. Starting a new timer on a different operation is handled as a single atomic server operation that stops the previous timer and starts the new one. If either half fails, neither takes effect. The server returns both the stopped entry and the new entry in the response. The client displays a notification: "Your timer on Op XX was automatically stopped." If the atomic operation fails, the client displays: "Could not start timer. Your timer on Op XX is still running." and does not update any local state.
- Network loss: The client continues displaying elapsed time computed from the last known start_time and shows the "Connection lost" banner (Section 6.3). The timer continues visually. On reconnection, the client refetches the active labor entry from the server. If the entry is still open, display resumes seamlessly. No labor time is lost because start/stop are discrete server events, not continuous client pings.
- Session expiry: The labor entry remains open on the server. After re-login, the client queries for the user's active labor entry and resumes display. The operator can then stop the timer, which records actual elapsed time since the server-recorded start.
- Multi-tab / multi-device: Only the most recent session's timer display is authoritative. Other tabs should poll for and reflect the current labor state on window focus.
### Labor Time Correction
When an operator cannot stop a timer due to network loss, session expiry, or other disruption, the recorded duration may exceed the actual work time. To correct this:
- Correction action: An engineer can submit a labor time correction on any labor entry within their program scope. The correction specifies a revised end_time (which must be after the original start_time and not in the future).
- Audit trail: The original end_time (or lack thereof) is preserved in the audit record. The correction records: correcting user, correction timestamp, original value, corrected value, and a required rationale text field.
- UI: On the operation detail view (expanded labor entries), engineers see a [Correct Time] secondary action on each labor entry. This opens a dialog with the original start/end times, a datetime picker for the corrected end time, and a required rationale field.
- Operator-initiated request: Operators cannot directly correct labor entries. They can flag an entry as "needs correction" with a note, which appears in the engineer's work order view as a pending item.
## 5.5 Inspection Form (Inspector View)
Purpose: Record inspection results and evidence.
### Layout
┌─────────────────────────────────────────────────────────┐
│ Inspect: Op 03 — NDI Weld Inspection    [● Pending]    │
│ WO-2026-0042 │ RS25 Engine 4                            │
╔═════════════════════════════════════════════════════════╗
║ ⚠ SAFETY-CRITICAL                                       ║
╚═════════════════════════════════════════════════════════╝
├─────────────────────────────────────────────────────────┤
│                                                         │
│ Operator labor on this operation:                       │
│   J. Rodriguez — 2h 34m (welder)                       │
│   ⚠ You cannot inspect this operation if you have      │
│     labor entries on it (SOD-001)                       │
│                                                         │
│ ┌─── Inspection ────────────────────────────────────┐  │
│ │ Criteria: [Per MSFC-WI-2024-0187 Rev C, Sec 4.2] │  │
│ │                                                   │  │
│ │ Result: ○ Accept  ○ Reject  ○ Conditional         │  │
│ │                                                   │  │
│ │ Measurements:                                     │  │
│ │   Wall thickness:   [____] mm   Min: 1.2 mm      │  │
│ │   Weld penetration: [____] %    Min: 95%          │  │
│ │   Defect count:     [____]      Max: 0            │  │
│ │                                                   │  │
│ │ Evidence:                                         │  │
│ │ [📷 Capture Photo] [📎 Upload File]               │  │
│ │ ut_scan_001.png ✓  ut_scan_002.png ✓              │  │
│ │                                                   │  │
│ │ Notes:                                            │  │
│ │ [________________________________]                │  │
│ │ [________________________________]                │  │
│ └───────────────────────────────────────────────────┘  │
│                                                         │
│ [Cancel]                    [Submit Inspection]          │
│                                                         │
└─────────────────────────────────────────────────────────┘
### Behavior
- SoD warning is visible before the inspector starts entering data
- If inspector has labor entries on this operation, the form is disabled with SOD-001 explanation
- Result selection drives the downstream flow (see Inspection Result Definitions below)
- Measurement fields have spec limits displayed inline
- Evidence upload uses presigned S3 URLs (Section 5.8 of spec; see attachment lifecycle below)
- Submit triggers confirmation dialog; safety-critical operations require re-authentication (see Section 9; per FORGEMFGBUILD_SPEC Section 9.3.6)
- When Conditional is selected, the Notes field becomes required (inline validation: "Conditions must be documented in Notes when result is Conditional")
### Inspection Criteria Schema
Measurement fields are rendered dynamically from the inspection criteria schema defined in the API specification (FORGEMFGBUILD_SPEC Section 5A). The frontend receives an array of criteria objects for each inspection and renders form fields accordingly. Each criterion includes metadata that the frontend uses to generate form fields and inline validation:
- id: Unique criterion identifier
- label: Display name (e.g., "Wall thickness")
- unit: Display unit (e.g., "mm", "%")
- type: Field type (numeric, integer, boolean, text)
- precision: Decimal places for numeric fields
- min / max: Spec limit values displayed inline; used for client-side warning (not hard validation — the server validates authoritatively)
- required: Whether the field must be filled before submission
- display_order: Rendering sequence
The frontend must render unknown criteria gracefully — if the API returns a criterion type the client does not recognize, it renders a text input as a fallback and logs a warning.
### Inspection Result Definitions
| Result | Effect on Hold Point | Downstream Action | Operation Status|
|--------|---------------------|-------------------|------------------|
|Accept | Satisfied | None — operation may proceed to completion | Unchanged (remains active; operator completes)|
|Reject | Not satisfied | NCR creation is mandatory. The [Submit Inspection] button changes to [Submit & Create NCR]. The form expands to include required NCR fields (description, linked operation — pre-populated from inspection context). Submission atomically creates both the inspection record and the linked NCR. The operation remains blocked until the NCR is dispositioned and (if rework) re-inspection is recorded with Accept. | Unchanged (remains active, hold point unsatisfied)|
|Conditional | Conditionally satisfied | Operation may continue processing. The inspection is recorded with conditional status, which serves as the follow-up finding — no separate entity is created. The conditional inspection appears in the QA manager's dashboard under "Unresolved Conditional Findings." The work order cannot be closed (QA closure blocked per Section 5.3 state machine) until all conditional inspections on all operations are resolved by a subsequent Accept or Reject inspection on the same operation. Resolution occurs when a later inspection on the same operation records Accept (finding resolved) or Reject (escalated to NCR). | Unchanged (remains active) |
Conditional acceptance is used when the work meets requirements with documented limitations or required follow-up actions (e.g., "acceptable for next operation pending thermal cycle verification"). The conditional inspection record appears in the QA manager's dashboard as an open item requiring resolution.
### Evidence Attachment Lifecycle
Evidence files (photos, scans, documents) follow a managed lifecycle tied to the inspection submission:
Initiate upload: Client requests a presigned S3 URL from the API, which returns a presigned PUT URL and an attachment_id in pending state.
Upload: Client uploads directly to S3 using the presigned URL (see Section 6.5 for auth and error handling). On completion, client confirms the upload to the API via the standard OpenAPI client, transitioning the attachment to uploaded state.
Submit inspection: The [Submit Inspection] action validates that all initiated attachments are in uploaded state. If any attachment is in pending (upload not started) or uploading (in progress) state, submit is blocked with an inline error: "Please wait for all uploads to complete before submitting."
Committed: On successful inspection submission, all associated attachments transition to committed state and are immutable.
Orphan cleanup: Attachments in pending or uploaded state that are not associated with a submitted inspection within 24 hours are eligible for server-side cleanup.
Constraints:
- Allowed file types: JPEG, PNG, PDF, TIFF
- Maximum file size: 50 MB per file
- Maximum attachments per inspection: 20
- File names are sanitized server-side; original filenames preserved as metadata
## 5.6 NCR Detail
Purpose: View and manage a nonconformance report.
### Layout
┌─────────────────────────────────────────────────────────┐
│ NCR-2026-0015                            [● Open]       │
│ WO-2026-0042 │ Op 03 │ RS25 Engine 4                   │
│ Initiated by: J. Smith │ 2026-04-10                     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ Description:                                            │
│ Weld penetration at joint 3B measured at 89%,           │
│ below minimum requirement of 95% per MSFC-WI-2024-     │
│ 0187 Rev C Section 4.2.3.                               │
│                                                         │
│ ┌─── Disposition ──────────────────────────────────┐   │
│ │ Disposition: [Select ▼]                          │   │
│ │ ○ Use as is  ○ Rework  ○ Repair  ○ Scrap        │   │
│ │                                                  │   │
│ │ Rationale:                                       │   │
│ │ [________________________________]               │   │
│ │ [________________________________]               │   │
│ │                                                  │   │
│ │ MRB Required: [No ▼]                             │   │
│ │ Schedule Impact (days): [____]                   │   │
│ │                                                  │   │
│ │ [Submit Disposition]                             │   │
│ │                                                  │   │
│ │ ⚠ You initiated this NCR. Another engineer       │   │
│ │   must disposition it (SOD-002).                 │   │
│ └──────────────────────────────────────────────────┘   │
│                                                         │
│ ┌─── Audit Trail ──────────────────────────────────┐   │
│ │ 04/10 09:14  J. Smith    Created NCR             │   │
│ │ 04/10 09:15  SYSTEM      Schedule impact flagged │   │
│ └──────────────────────────────────────────────────┘   │
│                                                         │
│ ──── Closure (qa_manager, dispositioned NCRs) ──────   │
│ [Close NCR]                                             │
│                                                         │
└─────────────────────────────────────────────────────────┘
### Behavior
- Disposition form disabled for initiator (SOD-002 enforced visually)
- Disposition options match spec exactly: useasis, rework, repair, scrap
- MRB toggle shows additional authority fields when required
- No under_review or verified states exist in UI
### NCR State Machine and Role Ownership
| Transition | From | To | Permitted Roles | Preconditions|
|-----------|------|----|-----------------|---------------|
|Create | — | open | inspector, engineer | Linked to operation or standalone|
|Disposition | open | dispositioned | engineer (not the initiator — SOD-002) | Disposition type selected, rationale provided|
|Close | dispositioned | closed | qa_manager | If rework, re-inspection completed and accepted on the linked operation; if scrap, part status updated |
Note on closure attestation: As with work order closure, the Close action itself constitutes the QA manager's attestation that they have reviewed the disposition. The system records the actor, timestamp, and entity version. No separate "review and approve" step is required.
UI actions by NCR state:
| NCR State | Visible Actions|
|-----------|----------------|
|open | [Submit Disposition] — visible to engineer, disabled for initiator per SOD-002|
|dispositioned | [Close NCR] — visible only to qa_manager; disabled with tooltip if preconditions unmet|
|closed | No actions — fully read-only |
## 5.7 Material Certification List
Purpose: Track material certificates and consumption.
### Behavior
- Filter by status (quarantine, accepted, rejected, consumed, expired)
- Expired certs visually flagged
- Quantity remaining shown as progress bar against quantity received
- Consume action navigated from operation execution, not from this list
## 5.8 Part Traceability
Purpose: Trace a serialized part through its complete manufacturing history.
### Layout
Visual tree showing: material cert → operations → inspections → NCRs → parent assemblies
┌─────────────────────────────────────────────────────────┐
│ Part: SN-2026-RS25-MCC-001              [● Installed]   │
│ LOX Manifold Housing │ P/N: 1234-5678-9                 │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ Material ──→ Operations ──→ Inspections ──→ Assembly    │
│                                                         │
│ 📋 AMS 5662                                             │
│ │  Lot: 2024-A │ Heat: H-9876                           │
│ │  Supplier: Haynes │ Received: 2026-02-15              │
│ │                                                       │
│ ├── Op 01: Machine housing (Complete ✓)                 │
│ │   └── Inspection: Accept ✓ (J. Kim, 03/02)           │
│ │                                                       │
│ ├── Op 02: Weld injector ports (Complete ✓)             │
│ │   └── Inspection: Accept ✓ (J. Kim, 03/05)           │
│ │                                                       │
│ ├── Op 03: NDI weld inspection (Active)                 │
│ │   └── NCR-2026-0015 (Open)                           │
│ │                                                       │
│ └── Installed in: RS25 Engine 4 Assembly                │
│                                                         │
└─────────────────────────────────────────────────────────┘
## 5.9 ORACLE Chat [CONDITIONAL — requires oracle_access]
Purpose: Advisory-only compliance and data queries.
### Layout
Standard chat interface with citation cards. Clear advisory-only banner.
┌─────────────────────────────────────────────────────────┐
│ ORACLE Chat                                             │
│ ⓘ Advisory only — ORACLE cannot approve, certify,      │
│   waive, or sign off on any action.                     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ You: What are the hold point requirements for           │
│      welding operations on RS25?                        │
│                                                         │
│ ORACLE: Per NPR 8735.2C Section 4.3.2, all welding     │
│ operations on flight hardware require...                │
│ ┌─ Citation ─────────────────────────────────────┐     │
│ │ NPR 8735.2C § 4.3.2 — Mandatory Inspection    │     │
│ │ Points for Welding Operations                  │     │
│ └────────────────────────────────────────────────┘     │
│                                                         │
│ [Type your question...                    ] [Send]      │
└─────────────────────────────────────────────────────────┘
### Behavior
- Advisory banner is always visible, never dismissable
- Citations are expandable cards linking to pack rules or live data
- Streaming responses (SSE) with typing indicator (see Section 6.5 for SSE integration)
- Chat history per session, not persisted across sessions in Phase 1
## 5.10 Admin Dashboard
Purpose: System administration and user management.
Separated visually from manufacturing UI with distinct header color (--text-primary background, white text) to make it unmistakably different from the production interface.
### Sections
- System Health: ECS task status, RDS connectivity, S3 replication lag, audit chain status — green/yellow/red indicators
- Users: List with role badges, program memberships, active/inactive toggle
- Programs: List with member count, active toggle
- Dead Letters: Failed event deliveries with retry button
- Audit Integrity: Last verification run timestamp, pass/fail, record count

# 6. Interaction Patterns
## 6.1 Optimistic UI vs. Server-Confirmed
All state transitions are server-confirmed. No optimistic updates on manufacturing state changes. The button shows a spinner, the server responds with success or error, then the UI updates. This is non-negotiable — optimistic UI on a manufacturing floor creates phantom states.
Form field edits (text, dropdowns) can be optimistic for responsiveness.
## 6.2 Stale Data Handling
- List views poll on 60-second intervals when the tab is active
- Detail views poll on 30-second intervals
- Dirty form protection: When a form has unsaved changes (dirty state), full-entity polling is suspended for that view to prevent overwriting the user's in-progress edits. Instead, a lightweight version-check continues at the normal polling interval: the client issues a HEAD request (or minimal GET returning only the entity's version field) to detect whether the server state has changed. If the version has changed, a non-intrusive banner appears below the form header: "This record has been updated by another user. Save or discard your changes to see the latest version." The banner does not overwrite the user's in-progress edits — it only signals that a newer version exists. Full polling resumes automatically when the form is submitted, the user explicitly discards changes, or the user navigates away.
- If a user's action returns 409 (version conflict), see Optimistic Concurrency Contract below
- Idle timeout (30 min) clears all cached state



This communication and its attachments are privileged and confidential. Generated by ORACLE — BoundState AI LLC.

---

# 7. Responsive Behavior

## 7.1 Breakpoints

| Breakpoint | Width | Layout |
|-----------|-------|--------|
| Floor (tablet portrait) | < 768px | Single column, bottom nav, large touch targets |
| Tablet landscape | 768–1024px | Two column where useful, side nav |
| Desktop | > 1024px | Full layout, side nav, dense tables |

## 7.2 Floor Mode Adaptations

When viewport is < 768px:

- Navigation moves to bottom tab bar (5 tabs max)
- Tables switch to card layout
- Action buttons become full-width
- Touch targets increase to 56px minimum height
- Confirmation dialogs become full-screen sheets (bottom-up)
- Inline expand becomes navigate-to-detail

---

# 8. Accessibility

- WCAG 2.1 Level AA compliance minimum
- All interactive elements keyboard-accessible
- Screen reader labels on status badges and icons
- Color is never the sole indicator of status — always paired with text label or icon
- Focus indicators visible and high-contrast
- Form validation errors associated with fields via aria-describedby
- Tab order follows visual layout

---

# 9. Performance Targets

| Metric | Target |
|--------|--------|
| First Contentful Paint | < 1.5s |
| Time to Interactive | < 3.0s |
| Page navigation (SPA) | < 500ms |
| Action response (server-confirmed) | < 2.0s visible feedback |
| Dashboard data refresh | < 1.0s |
| List page render (50 items) | < 500ms |

---

# 10. Error and Edge Case Handling

## 10.1 SoD Violation Messaging

Every SoD violation surfaces the specific rule ID and a human-readable explanation. The UI prevents the action before the user attempts it where possible (disable button + tooltip), and handles it gracefully if the server rejects it (inline error banner).

## 10.2 Session Expiry

When access token expires and refresh fails, redirect to login with message: "Your session has expired. Please sign in again." Preserve the current URL for post-login redirect.

## 10.3 Re-Authentication for Safety-Critical Actions

When the server returns 401 with `REAUTH_REQUIRED`, show a modal login form inline (not a full page redirect). On successful re-auth, replay the original action automatically.

---

# 11. What This Document Does NOT Cover

- Visual mockups and pixel-precise designs (Phase 2 design polish)
- Animation and transition specifications (functional first)
- Branding, logo placement, marketing language
- Print layouts for reports or traceability documents
- Mobile native app (out of scope per spec)
- Dark mode (Phase 2)
- Internationalization (English only in Phase 1)

---

*This communication and its attachments are privileged and confidential. Generated by ORACLE — BoundState AI LLC.*
