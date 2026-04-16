/**
 * Module:   API Types
 * Purpose:  TypeScript types mirroring backend Pydantic schemas.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why hand-typed: The backend exposes OpenAPI JSON at /openapi.json when
 * running.  A future generation step (Orval) can replace these types.
 * Until then, these match the backend Pydantic schemas field-for-field.
 */

/* ── Enums ── */

export type WOStatus =
  | 'draft'
  | 'released'
  | 'in_progress'
  | 'hold'
  | 'complete'
  | 'closed';

export type OpStatus = 'pending' | 'in_progress' | 'complete' | 'hold';

export type HoldPointStatus =
  | 'none'
  | 'pending'
  | 'satisfied'
  | 'conditionally_satisfied'
  | 'blocked';

export type InspectionResult = 'accept' | 'reject' | 'conditional';

export type InspectionQueueState =
  | 'pending'
  | 'conditional'
  | 'overdue'
  | 'completed';

export type NCRStatus = 'open' | 'dispositioned' | 'closed';

export type Disposition = 'use_as_is' | 'rework' | 'repair' | 'scrap';

export type CertStatus =
  | 'quarantine'
  | 'accepted'
  | 'rejected'
  | 'consumed'
  | 'expired'
  | 'scrapped';

export type PartStatus =
  | 'raw'
  | 'in_process'
  | 'completed'
  | 'installed'
  | 'scrapped'
  | 'quarantined';

export type BuildPhase =
  | 'fabrication'
  | 'assembly'
  | 'test'
  | 'refurbishment';

export type Priority = 'routine' | 'urgent' | 'critical';

export type DocumentType =
  | 'drawing'
  | 'specification'
  | 'work_instruction'
  | 'process_standard';

export type ApprovalState =
  | 'draft'
  | 'approved'
  | 'superseded'
  | 'obsolete';

export type UploadStatus = 'pending' | 'uploaded' | 'finalized';

export type EvidenceScope = 'inspection_evidence' | 'ncr_evidence';

export type SignOffAction = 'approve' | 'witness';

export type TileSeverity = 'info' | 'warn' | 'critical';

export type ScheduleConstraint =
  | 'none'
  | 'must_start_on'
  | 'must_finish_by'
  | 'as_late_as_possible';

export type Role =
  | 'operator'
  | 'inspector'
  | 'engineer'
  | 'qa_manager'
  | 'program_manager'
  | 'admin';

/* ── Pagination ── */

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

/* ── User / Auth ── */

export interface RoleOut {
  id: string;
  name: Role;
  permissions: string[];
}

export interface ProgramOut {
  id: string;
  code: string;
  name: string;
  active: boolean;
  schedule_enabled: boolean;
  oracle_access: boolean;
  overdue_threshold_hours: number;
  version: number;
}

export interface UserOut {
  id: string;
  cognito_sub: string;
  email: string;
  display_name: string;
  active: boolean;
  is_system_actor: boolean;
  roles: RoleOut[];
  program_memberships: ProgramOut[];
}

/* ── Work Order ── */

export interface WorkOrderOut {
  id: string;
  wo_number: string;
  program_id: string;
  engine_serial: string;
  module: string;
  build_phase: BuildPhase;
  status: WOStatus;
  priority: Priority;
  authored_by_id: string;
  bom_revision_ref: string | null;
  released_at: string | null;
  released_by: string | null;
  has_graph_issue: boolean;
  completed_at: string | null;
  completed_by: string | null;
  closure_rationale: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface WorkOrderCreate {
  program_id: string;
  engine_serial: string;
  module: string;
  build_phase: BuildPhase;
  priority?: Priority;
  bom_revision_ref?: string | null;
}

export interface WorkOrderUpdate {
  version?: number;
  engine_serial?: string;
  module?: string;
  build_phase?: BuildPhase;
  priority?: Priority;
  bom_revision_ref?: string | null;
}

export interface WorkOrderTransition {
  version?: number;
}

export interface WorkOrderHoldIn {
  version?: number;
  force_stop_labor?: boolean;
  reason?: string;
}

export interface StoppedLaborEntryOut {
  entry_id: string;
  operation_id: string;
  user_id: string;
}

export interface WorkOrderHoldOut {
  work_order: WorkOrderOut;
  force_stop_labor: boolean;
  stopped_labor_entries: StoppedLaborEntryOut[];
}

/* ── Operation ── */

export interface PredecessorSummary {
  id: string;
  sequence_number: number;
  description: string;
  status: OpStatus;
}

export interface StopRequestSummary {
  request_id: string;
  labor_entry_id: string;
  status: string;
  requested_by: { user_id: string; display_name: string };
  note: string;
  created_at: string;
}

export interface OperationOut {
  id: string;
  work_order_id: string;
  sequence: number;
  description: string;
  status: OpStatus;
  execution_cycle: number;
  inspection_hold: boolean;
  safety_critical: boolean;
  estimated_hours: number;
  started_at: string | null;
  completed_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface OperationDetailOut extends OperationOut {
  work_order_status: WOStatus;
  program_id: string;
  sequence_number: number;
  hold_point_status: HoldPointStatus;
  predecessors: string[];
  predecessor_summaries: PredecessorSummary[];
  stop_requests: StopRequestSummary[];
}

export interface OperationCreate {
  description: string;
  sequence: number;
  tooling_required?: string[];
  inspection_hold?: boolean;
  safety_critical?: boolean;
  sign_off_roles_required?: string[];
  estimated_hours?: number;
  constraint_type?: ScheduleConstraint;
  constraint_date?: string | null;
}

export interface OperationTransition {
  version?: number;
}

/* ── Labor ── */

export interface LaborVersionAssertions {
  target_operation?: number;
  active_labor_entry?: number;
  active_operation?: number;
}

export interface LaborCreate {
  role: string;
  shift: string;
  notes?: string | null;
  version_assertions?: LaborVersionAssertions;
}

export interface LaborOut {
  id: string;
  operation_id: string;
  user_id: string;
  role: string;
  execution_cycle: number;
  started_at: string;
  stopped_at: string | null;
  hours: number;
  shift: string;
  notes: string | null;
  version: number;
}

/* ── Evidence ── */

export interface EvidenceRef {
  s3_key: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  content_hash: string;
  uploaded_at: string;
  uploaded_by: string;
}

export interface EvidenceUploadUrlRequest {
  scope: EvidenceScope;
  filename: string;
  content_type: string;
  size_bytes: number;
}

export interface EvidenceUploadUrlResponse {
  s3_key: string;
  upload_url: string;
  upload_headers: Record<string, string>;
  expires_at: string;
  max_bytes: number;
}

export interface EvidenceConfirmRequest {
  s3_key: string;
  content_hash: string;
}

/* ── Inspection ── */

export interface InspectionCreate {
  criteria: string;
  result: InspectionResult;
  measurements?: Record<string, unknown>;
  evidence_refs?: Array<{ s3_key: string } | string>;
  notes?: string | null;
  supersedes_inspection_id?: string | null;
}

export interface InspectionOut {
  id: string;
  operation_id: string;
  inspector_id: string;
  execution_cycle: number;
  criteria: string;
  result: InspectionResult;
  measurements: Record<string, unknown>;
  evidence_refs: Array<EvidenceRef | string>;
  inspected_at: string;
  notes: string | null;
  supersedes_inspection_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface InspectionQueueRowOut {
  operation_id: string;
  work_order_id: string;
  program_id: string;
  work_order_number: string;
  sequence_number: number;
  description: string;
  operation_status: OpStatus;
  hold_point_status: HoldPointStatus;
  state: InspectionQueueState;
  safety_critical: boolean;
  is_overdue: boolean;
  overdue_since: string | null;
  conditional_inspection_id: string | null;
  inspected_at: string | null;
  updated_at: string;
}

export interface SignOffCreate {
  role: string;
  action: SignOffAction;
  notes?: string | null;
}

export interface SignOffOut {
  id: string;
  operation_id: string;
  user_id: string;
  role: string;
  execution_cycle: number;
  action: SignOffAction;
  signed_at: string;
  notes: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

/* ── NCR ── */

export interface NCROut {
  id: string;
  ncr_number: string;
  work_order_id: string;
  operation_id: string | null;
  part_instance_id: string | null;
  description: string;
  root_cause: string | null;
  status: NCRStatus;
  disposition: Disposition;
  disposition_rationale: string | null;
  engineering_approval_id: string | null;
  mrb_required: boolean;
  mrb_disposition: string | null;
  mrb_authority_id: string | null;
  schedule_impact_days: number | null;
  initiated_by_id: string;
  evidence_refs: Array<EvidenceRef | string>;
  remediation_work_order_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface NCRCreate {
  work_order_id: string;
  operation_id?: string | null;
  part_instance_id?: string | null;
  description: string;
  mrb_required?: boolean;
}

export interface NCRDisposition {
  version: number;
  disposition: Disposition;
  disposition_rationale: string;
  engineering_approval_id?: string | null;
  mrb_disposition?: string | null;
  mrb_authority_id?: string | null;
  schedule_impact_days?: number | null;
  remediation_work_order_id?: string | null;
  version_assertions?: {
    ncr?: number;
    operation?: number;
    work_order?: number;
  };
}

export interface NCRPatch {
  version: number;
  evidence_refs: Array<{ s3_key: string } | string>;
}

/* ── Material ── */

export interface MaterialCertOut {
  id: string;
  program_id: string;
  material_spec: string;
  lot_number: string;
  heat_number: string;
  supplier: string;
  cert_document_ref_id: string;
  received_at: string;
  received_by_id: string;
  accepted_by_id: string | null;
  accepted_at: string | null;
  shelf_life_expiry: string | null;
  status: CertStatus;
  quantity_received: number;
  quantity_remaining: number;
  unit_of_measure: string;
  version: number;
  created_at: string;
  updated_at: string;
}

/* ── Part ── */

export interface PartOut {
  id: string;
  serial_number: string;
  part_number: string;
  part_description: string;
  program_id: string;
  status: PartStatus;
  material_certification_id: string | null;
  parent_assembly_id: string | null;
  current_location: string | null;
  reason_ncr_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface TraceabilityOut {
  part: PartOut;
  material_certification_id: string | null;
  assembly_chain: PartOut[];
  child_parts: PartOut[];
  links: Array<{
    id: string;
    source_type: string;
    source_id: string;
    target_type: string;
    target_id: string;
    link_type: string;
  }>;
}

/* ── Dashboard ── */

export interface DashboardDrillThrough {
  path: string;
  query: Record<string, string>;
}

export interface DashboardTileOut {
  key: string;
  label: string;
  value: number;
  severity: TileSeverity;
  visible_to_roles: string[];
  drill_through: DashboardDrillThrough | null;
}

export interface WorkOrderStatusCounts {
  draft: number;
  released: number;
  in_progress: number;
  hold: number;
  complete: number;
  closed: number;
}

export interface DashboardSummaryOut {
  tiles: DashboardTileOut[];
  work_order_status_counts: WorkOrderStatusCounts;
  program_scope: string[];
  generated_at: string;
}

/* ── Admin ── */

export interface ProgramListItemOut extends ProgramOut {
  member_count: number;
}

export interface AdminHealthOut {
  database: { status: string; latency_ms: number };
  s3: { status: string };
  audit_chain: { status: string; last_verified_at: string | null };
  outbox: { pending_count: number; dead_letter_count: number };
  timestamp: string;
}

export interface DeadLetterOut {
  delivery_id: string;
  event_id: string;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  consumer_name: string;
  attempt_count: number;
  max_attempts: number;
  last_error: string | null;
  last_attempt_at: string | null;
}

/* ── Error shapes ── */

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

/* ── SSE event shapes (spec §6.5.2) ── */

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
