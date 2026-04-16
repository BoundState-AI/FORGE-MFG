/**
 * Module:   API Types
 * Purpose:  Re-exports from the generated OpenAPI schema + SSE additions.
 * Rev:      0.2.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this structure: The 5C slice wires a generated API client.  All
 * request/response schemas come from `generated/schema.d.ts`, which is
 * auto-generated from the backend's OpenAPI 3.1 spec by
 * `npm run generate-api`.  Hand-written types below are limited to
 * SSE event payloads — those are not documented in the OpenAPI spec
 * because SSE is a non-REST contract (spec §6.5.2).
 *
 * To refresh types after backend schema changes:
 *   cd frontend && npm run generate-api
 *
 * That regenerates both src/api/openapi.json and
 * src/api/generated/schema.d.ts in one step.
 */

import type { Schemas } from '@/api/typed-client';

/* ── Re-exports from generated schema ──
   Every type below comes from the backend OpenAPI spec verbatim.  If a
   name drifts from the backend, the generated schema will stop having
   that key and this file will fail to compile — a compile-time drift
   check that the review can rely on. */

/* Enums (discriminated unions in TypeScript) */
export type WOStatus = Schemas['WOStatus'];
export type OpStatus = Schemas['OpStatus'];
export type HoldPointStatus = Schemas['HoldPointStatus'];
export type InspectionResult = Schemas['InspectionResult'];
export type InspectionQueueState = Schemas['InspectionQueueState'];
export type NCRStatus = Schemas['NCRStatus'];
export type Disposition = Schemas['Disposition'];
export type CertStatus = Schemas['CertStatus'];
export type PartStatus = Schemas['PartStatus'];
export type BuildPhase = Schemas['BuildPhase'];
export type Priority = Schemas['Priority'];
export type DocumentType = Schemas['DocumentType'];
export type ApprovalState = Schemas['ApprovalState'];
export type UploadStatus = Schemas['UploadStatus'];
export type EvidenceScope = Schemas['EvidenceScope'];
export type SignOffAction = Schemas['SignOffAction'];
/* TileSeverity — inlined on DashboardTileOut.severity in the OpenAPI spec,
   not a standalone schema.  Extract it via indexed access. */
export type TileSeverity = Schemas['DashboardTileOut']['severity'];
export type ScheduleConstraint = Schemas['ScheduleConstraint'];

/* Role string literals — not a schema in OpenAPI (string enum inside UserOut). */
export type Role =
  | 'operator'
  | 'inspector'
  | 'engineer'
  | 'qa_manager'
  | 'program_manager'
  | 'admin';

/* Pagination envelope — the backend returns untyped dict with fixed keys */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface PaginationParams {
  page?: number;
  page_size?: number;
}

/* User / Auth */
export type RoleOut = Schemas['RoleOut'];
export type ProgramOut = Schemas['ProgramOut'];
export type UserOut = Schemas['UserOut'];

/* Work Order */
export type WorkOrderOut = Schemas['WorkOrderOut'];
export type WorkOrderDetailOut = Schemas['WorkOrderDetailOut'];
export type WorkOrderCreate = Schemas['WorkOrderCreate'];
export type WorkOrderUpdate = Schemas['WorkOrderUpdate'];
export type WorkOrderTransition = Schemas['WorkOrderTransition'];
export type WorkOrderHoldIn = Schemas['WorkOrderHoldIn'];
export type WorkOrderHoldOut = Schemas['WorkOrderHoldOut'];
export type StoppedLaborEntryOut = Schemas['StoppedLaborEntryOut'];

/* Operation */
export type OperationOut = Schemas['OperationOut'];
export type OperationDetailOut = Schemas['OperationDetailOut'];
export type OperationCreate = Schemas['OperationCreate'];
export type OperationTransition = Schemas['OperationTransition'];
export type PredecessorSummary = Schemas['PredecessorSummary'];
export type StopRequestSummary = Schemas['StopRequestSummary'];

/* Labor */
export type LaborCreate = Schemas['LaborCreate'];
export type LaborOut = Schemas['LaborOut'];
export type LaborVersionAssertions = Schemas['LaborVersionAssertions'];

/* Evidence */
export type EvidenceRef = Schemas['EvidenceRef'];
export type EvidenceUploadUrlRequest = Schemas['EvidenceUploadUrlRequest'];
export type EvidenceUploadUrlResponse = Schemas['EvidenceUploadUrlResponse'];
export type EvidenceConfirmRequest = Schemas['EvidenceConfirmRequest'];

/* Inspection */
export type InspectionCreate = Schemas['InspectionCreate'];
export type InspectionOut = Schemas['InspectionOut'];
export type InspectionQueueRowOut = Schemas['InspectionQueueRowOut'];
export type SignOffCreate = Schemas['SignOffCreate'];
export type SignOffOut = Schemas['SignOffOut'];

/* NCR */
export type NCROut = Schemas['NCROut'];
export type NCRCreate = Schemas['NCRCreate'];
export type NCRDisposition = Schemas['NCRDisposition'];
export type NCRPatch = Schemas['NCRPatch'];

/* Material */
export type MaterialCertOut = Schemas['MaterialCertOut'];

/* Part */
export type PartOut = Schemas['PartOut'];
export type TraceabilityOut = Schemas['TraceabilityOut'];

/* Dashboard */
export type DashboardSummaryOut = Schemas['DashboardSummaryOut'];
export type DashboardTileOut = Schemas['DashboardTileOut'];
export type DashboardDrillThrough = Schemas['DashboardDrillThrough'];
export type WorkOrderStatusCounts = Schemas['WorkOrderStatusCounts'];

/* Admin */
export type ProgramListItemOut = Schemas['ProgramListItemOut'];
export type AdminHealthOut = Schemas['AdminHealthOut'];
export type DeadLetterOut = Schemas['DeadLetterOut'];

/* ── Error response shapes — hand-written because FastAPI doesn't
     document these uniformly in the OpenAPI spec.  These match the
     shapes the backend actually emits (spec §6.3.2, §6.3.3). ── */

export interface VersionConflictError {
  code: 'VERSION_CONFLICT';
  message: string;
  entity_type: string;
  entity_id: string;
  your_version: number;
  current_version: number;
  current_entity: Record<string, unknown>;
}

export interface CompositeConflict {
  entity_type: string;
  entity_id: string;
  your_version: number;
  current_version: number;
  current_entity: Record<string, unknown>;
}

export interface CompositeConflictError {
  code: 'VERSION_CONFLICT';
  message: string;
  conflicts: CompositeConflict[];
}

export interface RuleViolationError {
  detail: string;
  code: string;
}

export interface ApiErrorResponse {
  error?: VersionConflictError | CompositeConflictError;
  detail?: string | RuleViolationError[];
}

/* ── SSE event shapes (spec §6.5.2) ──
     SSE is not documented in OpenAPI because FastAPI StreamingResponse
     doesn't declare event-type schemas.  These are hand-written from
     the spec's event registry. */

export interface SSEHoldPointChanged {
  operation_id: string;
  hold_point_status: HoldPointStatus;
  inspection_id: string;
}

export interface SSEWoStateChanged {
  work_order_id: string;
  state: WOStatus;
  previous_state: WOStatus;
  actor: { user_id: string; display_name: string };
  reason: string;
  force_stop_labor: boolean;
  stopped_labor_entries: Array<{
    entry_id: string;
    operation_id: string;
    user_id: string;
  }>;
}

export interface SSELaborActivity {
  operation_id: string;
  actor: { id: string; display_name: string };
  action: 'started' | 'stopped';
  entry_id: string;
}

export interface SSEOperationReopened {
  operation_id: string;
  ncr_id: string;
  disposition: string;
}
