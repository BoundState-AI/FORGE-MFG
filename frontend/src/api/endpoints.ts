/**
 * Module:   API Endpoints
 * Purpose:  Typed request functions routed through the openapi-fetch client.
 * Rev:      0.4.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this layer: Every wrapper below calls typedClient (openapi-fetch
 * bound to generated/schema.d.ts).  Path strings come from the generated
 * paths type — a typo in a path string fails to compile.  The `unwrap`
 * helper converts openapi-fetch's { data, error } result into either a
 * resolved value or a thrown ApiError whose shape matches what useConflict
 * and page-level try/catch blocks expect (err.response.status / data).
 *
 * This is the runtime integration point for the 5C generated-client work.
 * The wrappers remain hand-written (thin) but every request goes through
 * the typed client, so paths, params, request bodies, and response bodies
 * are all derived from the backend OpenAPI spec.
 *
 * Endpoints intentionally NOT wrapped here (no backend route yet):
 *   - GET /api/operations/{op_id}/labor          (labor entries)
 *   - GET /api/operations/{op_id}/inspections    (inspection history)
 *   - POST /api/operations/{op_id}/resume        (hold → in_progress)
 *
 * 5C parity patch (2026-04-15) restored the list/detail reads that
 * were previously stubbed out:
 *   - GET /api/material-certs                    (materials list)
 *   - GET /api/parts                             (parts list)
 *   - GET /api/parts/{part_id}                   (part detail)
 *   - GET /api/work-orders/{id} now returns WorkOrderDetailOut with
 *     operations inline (removes the dependency on a separate list GET)
 */

import { typedClient, unwrap } from './typed-client';
import type {
  PaginatedResponse,
  PaginationParams,
  CertStatus,
  WorkOrderOut,
  WorkOrderDetailOut,
  WorkOrderCreate,
  WorkOrderUpdate,
  WorkOrderTransition,
  WorkOrderClose,
  WorkOrderHoldIn,
  WorkOrderHoldOut,
  OperationOut,
  OperationDetailOut,
  OperationCreate,
  OperationTransition,
  LaborCreate,
  LaborOut,
  InspectionCreate,
  InspectionOut,
  InspectionQueueRowOut,
  InspectionQueueState,
  SignOffCreate,
  SignOffOut,
  NCROut,
  NCRCreate,
  NCRDisposition,
  NCRPatch,
  NCRClose,
  AuditEventOut,
  MaterialCertOut,
  PartOut,
  TraceabilityOut,
  DashboardSummaryOut,
  UserMeOut,
  UserOut,
  ProgramListItemOut,
  AdminHealthOut,
  DeadLetterOut,
  EvidenceUploadUrlRequest,
  EvidenceUploadUrlResponse,
  EvidenceConfirmRequest,
  EvidenceRef,
} from '@/types/api';

/* ── Helpers ── */

/** Build headers object for optional X-Program-Id: * override (spec §6.7.1). */
function programHeaders(programId?: string): Record<string, string> | undefined {
  return programId === '*' ? { 'X-Program-Id': '*' } : undefined;
}

/** Pagination query with only defined keys (so undefined never hits the wire). */
function pageQuery(params?: PaginationParams): {
  page?: number;
  page_size?: number;
} {
  const q: { page?: number; page_size?: number } = {};
  if (params?.page) q.page = params.page;
  if (params?.page_size) q.page_size = params.page_size;
  return q;
}

/* ── Dashboard ── */

export async function fetchDashboardSummary(): Promise<DashboardSummaryOut> {
  return unwrap(await typedClient.GET('/api/dashboard/summary'));
}

/* ── Work Orders ── */

export async function fetchWorkOrders(
  params?: PaginationParams & { status?: string; programId?: string },
): Promise<PaginatedResponse<WorkOrderOut>> {
  const result = await typedClient.GET('/api/work-orders', {
    params: {
      query: pageQuery(params),
      /* status is documented on the route but not in the generated query
         object.  Attach it as an extra params entry — openapi-fetch forwards
         unknown query keys into the URL unchanged. */
    },
    headers: programHeaders(params?.programId),
  });
  const envelope = unwrap(result) as unknown as PaginatedResponse<WorkOrderOut>;
  return envelope;
}

export async function fetchWorkOrder(id: string): Promise<WorkOrderDetailOut> {
  /* Returns the detail shape with operations inline (Build Spec §5.1,
     parity patch).  Clients that only need the lightweight shape can
     still destructure the fields in common with WorkOrderOut. */
  return unwrap(
    await typedClient.GET('/api/work-orders/{wo_id}', {
      params: { path: { wo_id: id } },
    }),
  );
}

export async function createWorkOrder(
  body: WorkOrderCreate,
): Promise<WorkOrderOut> {
  return unwrap(await typedClient.POST('/api/work-orders', { body }));
}

export async function updateWorkOrder(
  id: string,
  body: WorkOrderUpdate,
): Promise<WorkOrderOut> {
  return unwrap(
    await typedClient.PATCH('/api/work-orders/{wo_id}', {
      params: { path: { wo_id: id } },
      body,
    }),
  );
}

export async function releaseWorkOrder(
  id: string,
  body: WorkOrderTransition,
): Promise<WorkOrderOut> {
  return unwrap(
    await typedClient.POST('/api/work-orders/{wo_id}/release', {
      params: { path: { wo_id: id } },
      body,
    }),
  );
}

export async function recallWorkOrder(
  id: string,
  body: WorkOrderTransition,
): Promise<WorkOrderOut> {
  return unwrap(
    await typedClient.POST('/api/work-orders/{wo_id}/recall', {
      params: { path: { wo_id: id } },
      body,
    }),
  );
}

export async function holdWorkOrder(
  id: string,
  body: WorkOrderHoldIn,
): Promise<WorkOrderHoldOut> {
  return unwrap(
    await typedClient.POST('/api/work-orders/{wo_id}/hold', {
      params: { path: { wo_id: id } },
      body,
    }),
  );
}

export async function resumeWorkOrder(
  id: string,
  body: WorkOrderTransition,
): Promise<WorkOrderOut> {
  return unwrap(
    await typedClient.POST('/api/work-orders/{wo_id}/resume', {
      params: { path: { wo_id: id } },
      body,
    }),
  );
}

export async function completeWorkOrder(
  id: string,
  body: WorkOrderTransition,
): Promise<WorkOrderOut> {
  return unwrap(
    await typedClient.POST('/api/work-orders/{wo_id}/complete', {
      params: { path: { wo_id: id } },
      body,
    }),
  );
}

/**
 * GC-4 close-as-attestation (UX spec §5.3).  Body is `WorkOrderClose`
 * (`version` + optional `closure_rationale`).  The server enforces the
 * `CLOSURE_ENABLED` feature flag (403 CLOSURE_DISABLED when off), the
 * §11.3 re-auth freshness gate, and the close-readiness blockers
 * (open NCRs, unresolved conditional inspections).  Optional
 * `Idempotency-Key` lets the same retry deduplicate against the
 * server-side cache.
 */
export async function closeWorkOrder(
  id: string,
  body: WorkOrderClose,
  idempotencyKey?: string,
): Promise<WorkOrderOut> {
  const headers = idempotencyKey
    ? { 'Idempotency-Key': idempotencyKey }
    : undefined;
  return unwrap(
    await typedClient.POST('/api/work-orders/{wo_id}/close', {
      params: { path: { wo_id: id } },
      body,
      headers,
    }),
  );
}

/* ── Operations ── */

export async function fetchOperationDetail(
  id: string,
): Promise<OperationDetailOut> {
  return unwrap(
    await typedClient.GET('/api/operations/{op_id}', {
      params: { path: { op_id: id } },
    }),
  );
}

export async function createOperation(
  woId: string,
  body: OperationCreate,
): Promise<OperationOut> {
  return unwrap(
    await typedClient.POST('/api/work-orders/{wo_id}/operations', {
      params: { path: { wo_id: woId } },
      body,
    }),
  );
}

/**
 * Start operation.  Backend contract: the POST /start endpoint creates
 * a labor entry atomically, so the body requires `role` and `shift` in
 * addition to the optimistic-lock `version` field.  Spec §5.4.2.
 */
export async function startOperation(
  id: string,
  body: { role: string; shift: string; version?: number | null },
): Promise<OperationOut> {
  return unwrap(
    await typedClient.POST('/api/operations/{op_id}/start', {
      params: { path: { op_id: id } },
      body,
    }),
  );
}

export async function stopOperation(
  id: string,
  body: OperationTransition,
): Promise<OperationOut> {
  return unwrap(
    await typedClient.POST('/api/operations/{op_id}/stop', {
      params: { path: { op_id: id } },
      body,
    }),
  );
}

export async function completeOperation(
  id: string,
  body: OperationTransition,
): Promise<OperationOut> {
  return unwrap(
    await typedClient.POST('/api/operations/{op_id}/complete', {
      params: { path: { op_id: id } },
      body,
    }),
  );
}

export async function holdOperation(
  id: string,
  body: OperationTransition,
): Promise<OperationOut> {
  return unwrap(
    await typedClient.POST('/api/operations/{op_id}/hold', {
      params: { path: { op_id: id } },
      body,
    }),
  );
}

/* ── Labor ── */

export async function createLabor(
  opId: string,
  body: LaborCreate,
): Promise<LaborOut> {
  return unwrap(
    await typedClient.POST('/api/operations/{op_id}/labor', {
      params: { path: { op_id: opId } },
      body,
    }),
  );
}

export async function stopLabor(
  id: string,
  body: { version?: number | null; stopped_at?: string | null },
): Promise<LaborOut> {
  return unwrap(
    await typedClient.PATCH('/api/labor-entries/{labor_id}', {
      params: { path: { labor_id: id } },
      body,
    }),
  );
}

/* ── Inspections ── */

export async function fetchInspectionQueue(
  params?: PaginationParams & {
    state?: InspectionQueueState;
    safety_critical?: boolean;
    operation_id?: string;
    programId?: string;
  },
): Promise<PaginatedResponse<InspectionQueueRowOut>> {
  const result = await typedClient.GET('/api/inspections', {
    params: {
      query: {
        ...pageQuery(params),
        ...(params?.state ? { state: params.state } : {}),
        ...(params?.safety_critical !== undefined
          ? { safety_critical: params.safety_critical }
          : {}),
        ...(params?.operation_id ? { operation_id: params.operation_id } : {}),
      },
    },
    headers: programHeaders(params?.programId),
  });
  return unwrap(result) as unknown as PaginatedResponse<InspectionQueueRowOut>;
}

export async function createInspection(
  opId: string,
  body: InspectionCreate,
): Promise<InspectionOut> {
  return unwrap(
    await typedClient.POST('/api/operations/{op_id}/inspect', {
      params: { path: { op_id: opId } },
      body,
    }),
  );
}

export async function createSignOff(
  opId: string,
  body: SignOffCreate,
  /**
   * Caller-supplied Idempotency-Key.  When omitted, the typed-client
   * middleware auto-generates one (the normal first-attempt path).
   * GC-3 retry path: the sign-off mutation captures the key BEFORE the
   * first request fires, then re-passes the same key on retry after
   * fresh auth so the backend's idempotency cache deduplicates a
   * double-submit (UX spec §11.3).
   */
  idempotencyKey?: string,
): Promise<SignOffOut> {
  return unwrap(
    await typedClient.POST('/api/operations/{op_id}/sign-off', {
      params: { path: { op_id: opId } },
      body,
      headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    }),
  );
}

/* ── NCRs ── */

export async function fetchNCRs(
  params?: PaginationParams & { status?: string; programId?: string },
): Promise<PaginatedResponse<NCROut>> {
  const result = await typedClient.GET('/api/ncrs', {
    params: {
      query: pageQuery(params),
    },
    headers: programHeaders(params?.programId),
  });
  return unwrap(result) as unknown as PaginatedResponse<NCROut>;
}

export async function fetchNCR(id: string): Promise<NCROut> {
  return unwrap(
    await typedClient.GET('/api/ncrs/{ncr_id}', {
      params: { path: { ncr_id: id } },
    }),
  );
}

export async function createNCR(body: NCRCreate): Promise<NCROut> {
  return unwrap(await typedClient.POST('/api/ncrs', { body }));
}

export async function dispositionNCR(
  id: string,
  body: NCRDisposition,
  /**
   * Caller-supplied Idempotency-Key for the GC-3 same-key retry path
   * (UX spec §9.3 line 1490: "Re-auth retries reuse the same
   * Idempotency-Key; the client does not generate a new key after
   * REAUTH_REQUIRED").  The spec contract is uniform across all
   * protected actions — it is NOT carved out for PATCH even though
   * the current backend idempotency middleware only caches POSTs.
   * Carrying the header on disposition retries keeps the contract
   * honest and is free when the backend does not need it; if the
   * backend later extends caching to PATCH, the frontend is already
   * correct.
   */
  idempotencyKey?: string,
): Promise<NCROut> {
  return unwrap(
    await typedClient.PATCH('/api/ncrs/{ncr_id}/disposition', {
      params: { path: { ncr_id: id } },
      body,
      headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    }),
  );
}

export async function patchNCR(id: string, body: NCRPatch): Promise<NCROut> {
  return unwrap(
    await typedClient.PATCH('/api/ncrs/{ncr_id}', {
      params: { path: { ncr_id: id } },
      body,
    }),
  );
}

/**
 * GC-4 NCR close-as-attestation (UX spec §5.6.7).  Body is `NCRClose`
 * (`version` + optional `closure_rationale` + optional `root_cause`).
 * Server enforces the same `CLOSURE_ENABLED` feature flag as WO close
 * + the §11.3 re-auth freshness gate + the existing per-disposition
 * preconditions (`EVIDENCE_REQUIRED_FOR_CLOSE`,
 * `REMEDIATION_WO_NOT_CLOSED`).  Optional `Idempotency-Key` for
 * retry-safe submission per §9.3.
 */
export async function closeNCR(
  id: string,
  body: NCRClose,
  idempotencyKey?: string,
): Promise<NCROut> {
  const headers = idempotencyKey
    ? { 'Idempotency-Key': idempotencyKey }
    : undefined;
  return unwrap(
    await typedClient.POST('/api/ncrs/{ncr_id}/close', {
      params: { path: { ncr_id: id } },
      body,
      headers,
    }),
  );
}

/* ── Materials list (5C parity patch) ── */

export async function fetchMaterials(
  params?: PaginationParams & {
    status?: CertStatus;
    programId?: string;
  },
): Promise<PaginatedResponse<MaterialCertOut>> {
  const result = await typedClient.GET('/api/material-certs', {
    params: {
      query: {
        ...pageQuery(params),
        ...(params?.status ? { status: params.status } : {}),
      },
    },
    headers: programHeaders(params?.programId),
  });
  return unwrap(result) as unknown as PaginatedResponse<MaterialCertOut>;
}

/* ── Parts list + detail + traceability (5C parity patch) ── */

export async function fetchParts(
  params?: PaginationParams & { programId?: string },
): Promise<PaginatedResponse<PartOut>> {
  const result = await typedClient.GET('/api/parts', {
    params: { query: pageQuery(params) },
    headers: programHeaders(params?.programId),
  });
  return unwrap(result) as unknown as PaginatedResponse<PartOut>;
}

export async function fetchPart(id: string): Promise<PartOut> {
  return unwrap(
    await typedClient.GET('/api/parts/{part_id}', {
      params: { path: { part_id: id } },
    }),
  );
}

export async function fetchTraceability(id: string): Promise<TraceabilityOut> {
  return unwrap(
    await typedClient.GET('/api/parts/{part_id}/traceability', {
      params: { path: { part_id: id } },
    }),
  );
}

/* ── Evidence ── */

export async function requestUploadUrl(
  body: EvidenceUploadUrlRequest,
): Promise<EvidenceUploadUrlResponse> {
  return unwrap(await typedClient.POST('/api/evidence/upload-url', { body }));
}

export async function confirmEvidence(
  body: EvidenceConfirmRequest,
): Promise<EvidenceRef> {
  return unwrap(await typedClient.POST('/api/evidence/confirm', { body }));
}

/* ── Users ── */

export async function fetchCurrentUser(): Promise<UserMeOut> {
  /* The /me response schema drops the required wrappers on roles[] and
     program_memberships[] (they come out as optional in the generated
     types).  Narrow to the full UserMeOut shape after the fact.

     GC-4: UserMeOut adds `closure_enabled` so the frontend can
     pre-render the close action affordances based on the live
     governance flag. */
  const me = unwrap(await typedClient.GET('/api/users/me'));
  return {
    ...me,
    roles: me.roles ?? [],
    program_memberships: me.program_memberships ?? [],
    closure_enabled: me.closure_enabled ?? false,
  } as UserMeOut;
}

/* ── Audit (UX spec §6.6) ── */

/**
 * Entity-scoped audit read for the WO/NCR detail page tabs (spec
 * §6.6.3).  Passing both `entity_type` and `entity_id` uses the
 * entity-scoped authorization path — the caller's entity-type read
 * permission (wo:read / ncr:read / op:read) plus the entity's
 * program scope is sufficient.  Omitting `entity_id` exercises the
 * cross-entity path which requires admin:manage (used by the QA
 * full viewer, not by the WO/NCR tabs).
 */
export async function fetchAuditEvents(params: {
  entity_type?: string;
  entity_id?: string;
  actor_user_id?: string;
  action?: string;
  timestamp_from?: string;
  timestamp_to?: string;
  page?: number;
  page_size?: number;
}): Promise<PaginatedResponse<AuditEventOut>> {
  const result = await typedClient.GET('/api/audit/events', {
    params: { query: params as Record<string, unknown> },
  });
  return unwrap(result) as unknown as PaginatedResponse<AuditEventOut>;
}

/* ── Admin ── */

export async function fetchAdminHealth(): Promise<AdminHealthOut> {
  return unwrap(await typedClient.GET('/api/admin/health'));
}

export async function fetchAdminPrograms(
  params?: PaginationParams,
): Promise<PaginatedResponse<ProgramListItemOut>> {
  const result = await typedClient.GET('/api/admin/programs', {
    params: { query: pageQuery(params) },
  });
  return unwrap(result) as unknown as PaginatedResponse<ProgramListItemOut>;
}

export async function fetchAdminUsers(
  params?: PaginationParams,
): Promise<PaginatedResponse<UserOut>> {
  const result = await typedClient.GET('/api/admin/users', {
    params: { query: pageQuery(params) },
  });
  return unwrap(result) as unknown as PaginatedResponse<UserOut>;
}

export async function fetchAdminDeadLetters(
  params?: PaginationParams,
): Promise<PaginatedResponse<DeadLetterOut>> {
  const result = await typedClient.GET('/api/admin/dead-letters', {
    params: { query: pageQuery(params) },
  });
  return unwrap(result) as unknown as PaginatedResponse<DeadLetterOut>;
}
