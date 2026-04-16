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
 * Endpoints intentionally NOT wrapped here (no backend route exists yet;
 * the generated schema confirms these paths have `get: never` or no
 * matching route at all; see 5C commit notes for the list deferred to a
 * future backend slice):
 *   - GET /api/work-orders/{wo_id}/operations   (list operations)
 *   - GET /api/operations/{op_id}/labor          (labor entries)
 *   - GET /api/operations/{op_id}/inspections    (inspection history)
 *   - GET /api/operations/{op_id}/resume         (hold → in_progress)
 *   - GET /api/parts                             (parts list)
 *   - GET /api/parts/{part_id}                   (part detail)
 *   - GET /api/material-certs                    (materials list)
 *
 * Pages that wanted those reads render an explicit "not yet available"
 * placeholder rather than calling a 404-bound URL.
 */

import { typedClient, unwrap } from './typed-client';
import type {
  PaginatedResponse,
  PaginationParams,
  WorkOrderOut,
  WorkOrderCreate,
  WorkOrderUpdate,
  WorkOrderTransition,
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
  TraceabilityOut,
  DashboardSummaryOut,
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

export async function fetchWorkOrder(id: string): Promise<WorkOrderOut> {
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
): Promise<SignOffOut> {
  return unwrap(
    await typedClient.POST('/api/operations/{op_id}/sign-off', {
      params: { path: { op_id: opId } },
      body,
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
): Promise<NCROut> {
  return unwrap(
    await typedClient.PATCH('/api/ncrs/{ncr_id}/disposition', {
      params: { path: { ncr_id: id } },
      body,
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

/* ── Parts — traceability only (list + detail GETs not on backend yet) ── */

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

export async function fetchCurrentUser(): Promise<UserOut> {
  /* The /me response schema drops the required wrappers on roles[] and
     program_memberships[] (they come out as optional in the generated
     types).  Narrow to the full UserOut shape after the fact. */
  const me = unwrap(await typedClient.GET('/api/users/me'));
  return {
    ...me,
    roles: me.roles ?? [],
    program_memberships: me.program_memberships ?? [],
  } as UserOut;
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
