/**
 * Module:   API Endpoints
 * Purpose:  Typed fetch functions for all Month 5 backend surfaces.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this layer: Isolates HTTP calls from React hooks so the client
 * can be replaced with generated code (Orval) without changing hooks.
 * Each function matches one backend route exactly.
 */

import apiClient from './client';
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
  SignOffCreate,
  SignOffOut,
  NCROut,
  NCRCreate,
  NCRDisposition,
  NCRPatch,
  MaterialCertOut,
  PartOut,
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

function paginationQuery(params?: PaginationParams): Record<string, number> {
  const q: Record<string, number> = {};
  if (params?.page) q.page = params.page;
  if (params?.page_size) q.page_size = params.page_size;
  return q;
}

/* ── Dashboard ── */

export async function fetchDashboardSummary(): Promise<DashboardSummaryOut> {
  const { data } = await apiClient.get<DashboardSummaryOut>(
    '/api/dashboard/summary',
  );
  return data;
}

/* ── Work Orders ── */

export async function fetchWorkOrders(
  params?: PaginationParams & { status?: string },
): Promise<PaginatedResponse<WorkOrderOut>> {
  const { data } = await apiClient.get<PaginatedResponse<WorkOrderOut>>(
    '/api/work-orders',
    { params: { ...paginationQuery(params), status: params?.status } },
  );
  return data;
}

export async function fetchWorkOrder(id: string): Promise<WorkOrderOut> {
  const { data } = await apiClient.get<WorkOrderOut>(`/api/work-orders/${id}`);
  return data;
}

export async function createWorkOrder(
  body: WorkOrderCreate,
): Promise<WorkOrderOut> {
  const { data } = await apiClient.post<WorkOrderOut>('/api/work-orders', body);
  return data;
}

export async function updateWorkOrder(
  id: string,
  body: WorkOrderUpdate,
): Promise<WorkOrderOut> {
  const { data } = await apiClient.patch<WorkOrderOut>(
    `/api/work-orders/${id}`,
    body,
  );
  return data;
}

export async function releaseWorkOrder(
  id: string,
  body: WorkOrderTransition,
): Promise<WorkOrderOut> {
  const { data } = await apiClient.post<WorkOrderOut>(
    `/api/work-orders/${id}/release`,
    body,
  );
  return data;
}

export async function recallWorkOrder(
  id: string,
  body: WorkOrderTransition,
): Promise<WorkOrderOut> {
  const { data } = await apiClient.post<WorkOrderOut>(
    `/api/work-orders/${id}/recall`,
    body,
  );
  return data;
}

export async function holdWorkOrder(
  id: string,
  body: WorkOrderHoldIn,
): Promise<WorkOrderHoldOut> {
  const { data } = await apiClient.post<WorkOrderHoldOut>(
    `/api/work-orders/${id}/hold`,
    body,
  );
  return data;
}

export async function resumeWorkOrder(
  id: string,
  body: WorkOrderTransition,
): Promise<WorkOrderOut> {
  const { data } = await apiClient.post<WorkOrderOut>(
    `/api/work-orders/${id}/resume`,
    body,
  );
  return data;
}

export async function completeWorkOrder(
  id: string,
  body: WorkOrderTransition,
): Promise<WorkOrderOut> {
  const { data } = await apiClient.post<WorkOrderOut>(
    `/api/work-orders/${id}/complete`,
    body,
  );
  return data;
}

/* ── Operations ── */

export async function fetchOperationDetail(
  id: string,
): Promise<OperationDetailOut> {
  const { data } = await apiClient.get<OperationDetailOut>(
    `/api/operations/${id}`,
  );
  return data;
}

export async function fetchWorkOrderOperations(
  woId: string,
): Promise<OperationOut[]> {
  const { data } = await apiClient.get<PaginatedResponse<OperationOut>>(
    `/api/work-orders/${woId}/operations`,
    { params: { page_size: 200 } },
  );
  return data.items ?? data as unknown as OperationOut[];
}

export async function createOperation(
  woId: string,
  body: OperationCreate,
): Promise<OperationOut> {
  const { data } = await apiClient.post<OperationOut>(
    `/api/work-orders/${woId}/operations`,
    body,
  );
  return data;
}

export async function startOperation(
  id: string,
  body: OperationTransition,
): Promise<OperationOut> {
  const { data } = await apiClient.post<OperationOut>(
    `/api/operations/${id}/start`,
    body,
  );
  return data;
}

export async function completeOperation(
  id: string,
  body: OperationTransition,
): Promise<OperationOut> {
  const { data } = await apiClient.post<OperationOut>(
    `/api/operations/${id}/complete`,
    body,
  );
  return data;
}

export async function holdOperation(
  id: string,
  body: OperationTransition,
): Promise<OperationOut> {
  const { data } = await apiClient.post<OperationOut>(
    `/api/operations/${id}/hold`,
    body,
  );
  return data;
}

export async function resumeOperation(
  id: string,
  body: OperationTransition,
): Promise<OperationOut> {
  const { data } = await apiClient.post<OperationOut>(
    `/api/operations/${id}/resume`,
    body,
  );
  return data;
}

/* ── Labor ── */

export async function createLabor(
  opId: string,
  body: LaborCreate,
): Promise<LaborOut> {
  const { data } = await apiClient.post<LaborOut>(
    `/api/operations/${opId}/labor`,
    body,
  );
  return data;
}

export async function stopLabor(
  id: string,
  body: { version?: number; stopped_at?: string },
): Promise<LaborOut> {
  const { data } = await apiClient.patch<LaborOut>(
    `/api/labor-entries/${id}`,
    body,
  );
  return data;
}

/* ── Inspections ── */

export async function fetchInspectionQueue(
  params?: PaginationParams & {
    state?: string;
    safety_critical?: boolean;
    operation_id?: string;
    programId?: string;
  },
): Promise<PaginatedResponse<InspectionQueueRowOut>> {
  const { programId, ...rest } = params ?? {};
  const headers: Record<string, string> = {};
  if (programId === '*') headers['X-Program-Id'] = '*';
  const { data } = await apiClient.get<
    PaginatedResponse<InspectionQueueRowOut>
  >('/api/inspections', { params: { ...paginationQuery(rest), ...rest }, headers });
  return data;
}

export async function createInspection(
  opId: string,
  body: InspectionCreate,
): Promise<InspectionOut> {
  const { data } = await apiClient.post<InspectionOut>(
    `/api/operations/${opId}/inspect`,
    body,
  );
  return data;
}

export async function createSignOff(
  opId: string,
  body: SignOffCreate,
): Promise<SignOffOut> {
  const { data } = await apiClient.post<SignOffOut>(
    `/api/operations/${opId}/sign-off`,
    body,
  );
  return data;
}

/* ── NCRs ── */

export async function fetchNCRs(
  params?: PaginationParams & { status?: string; programId?: string },
): Promise<PaginatedResponse<NCROut>> {
  const headers: Record<string, string> = {};
  if (params?.programId === '*') headers['X-Program-Id'] = '*';
  const { data } = await apiClient.get<PaginatedResponse<NCROut>>(
    '/api/ncrs',
    { params: { ...paginationQuery(params), status: params?.status }, headers },
  );
  return data;
}

export async function fetchNCR(id: string): Promise<NCROut> {
  const { data } = await apiClient.get<NCROut>(`/api/ncrs/${id}`);
  return data;
}

export async function createNCR(body: NCRCreate): Promise<NCROut> {
  const { data } = await apiClient.post<NCROut>('/api/ncrs', body);
  return data;
}

export async function dispositionNCR(
  id: string,
  body: NCRDisposition,
): Promise<NCROut> {
  const { data } = await apiClient.patch<NCROut>(
    `/api/ncrs/${id}/disposition`,
    body,
  );
  return data;
}

export async function patchNCR(id: string, body: NCRPatch): Promise<NCROut> {
  const { data } = await apiClient.patch<NCROut>(`/api/ncrs/${id}`, body);
  return data;
}

/* ── Materials ── */

export async function fetchMaterials(
  params?: PaginationParams & { status?: string; programId?: string },
): Promise<PaginatedResponse<MaterialCertOut>> {
  const headers: Record<string, string> = {};
  if (params?.programId === '*') headers['X-Program-Id'] = '*';
  const { data } = await apiClient.get<PaginatedResponse<MaterialCertOut>>(
    '/api/material-certs',
    { params: { ...paginationQuery(params), status: params?.status }, headers },
  );
  return data;
}

/* ── Parts ── */

export async function fetchParts(
  params?: PaginationParams,
): Promise<PaginatedResponse<PartOut>> {
  const { data } = await apiClient.get<PaginatedResponse<PartOut>>(
    '/api/parts',
    { params: paginationQuery(params) },
  );
  return data;
}

export async function fetchPart(id: string): Promise<PartOut> {
  const { data } = await apiClient.get<PartOut>(`/api/parts/${id}`);
  return data;
}

export async function fetchTraceability(id: string): Promise<TraceabilityOut> {
  const { data } = await apiClient.get<TraceabilityOut>(
    `/api/parts/${id}/traceability`,
  );
  return data;
}

/* ── Labor (read) ── */

export async function fetchOperationLabor(
  opId: string,
): Promise<LaborOut[]> {
  const { data } = await apiClient.get<PaginatedResponse<LaborOut>>(
    `/api/operations/${opId}/labor`,
    { params: { page_size: 200 } },
  );
  return data.items ?? (data as unknown as LaborOut[]);
}

/* ── Inspections (read) ── */

export async function fetchOperationInspections(
  opId: string,
): Promise<InspectionOut[]> {
  const { data } = await apiClient.get<PaginatedResponse<InspectionOut>>(
    `/api/operations/${opId}/inspections`,
    { params: { page_size: 200 } },
  );
  return data.items ?? (data as unknown as InspectionOut[]);
}

/* ── Evidence ── */

export async function requestUploadUrl(
  body: EvidenceUploadUrlRequest,
): Promise<EvidenceUploadUrlResponse> {
  const { data } = await apiClient.post<EvidenceUploadUrlResponse>(
    '/api/evidence/upload-url',
    body,
  );
  return data;
}

export async function confirmEvidence(
  body: EvidenceConfirmRequest,
): Promise<EvidenceRef> {
  const { data } = await apiClient.post<EvidenceRef>(
    '/api/evidence/confirm',
    body,
  );
  return data;
}

/* ── Users ── */

export async function fetchCurrentUser(): Promise<UserOut> {
  const { data } = await apiClient.get<UserOut>('/api/users/me');
  return data;
}

/* ── Admin ── */

export async function fetchAdminHealth(): Promise<AdminHealthOut> {
  const { data } = await apiClient.get<AdminHealthOut>('/api/admin/health');
  return data;
}

export async function fetchAdminPrograms(
  params?: PaginationParams,
): Promise<PaginatedResponse<ProgramListItemOut>> {
  const { data } = await apiClient.get<PaginatedResponse<ProgramListItemOut>>(
    '/api/admin/programs',
    { params: paginationQuery(params) },
  );
  return data;
}

export async function fetchAdminUsers(
  params?: PaginationParams,
): Promise<PaginatedResponse<UserOut>> {
  const { data } = await apiClient.get<PaginatedResponse<UserOut>>(
    '/api/admin/users',
    { params: paginationQuery(params) },
  );
  return data;
}

export async function fetchAdminDeadLetters(
  params?: PaginationParams,
): Promise<PaginatedResponse<DeadLetterOut>> {
  const { data } = await apiClient.get<PaginatedResponse<DeadLetterOut>>(
    '/api/admin/dead-letters',
    { params: paginationQuery(params) },
  );
  return data;
}
