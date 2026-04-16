/**
 * Module:   OperationDetail
 * Purpose:  Engineer's read-heavy view of an operation with labor table and inspection history (spec section 5.4.11).
 * Rev:      0.2.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * This view is the engineer's counterpart to OperationExecution.  It
 * shows all labor entries across users, inspection history, and allows
 * Hold/Resume but NOT Start/Complete (those are operator-only).
 *
 * Stop-request labor entries are highlighted amber so engineers can see
 * pending stop requests at a glance.
 */

import { useState, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  fetchOperationDetail,
  fetchOperationLabor,
  fetchOperationInspections,
  holdOperation,
  resumeOperation,
} from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { SafetyCriticalBanner } from '@/components/SafetyCriticalBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ErrorBanner } from '@/components/ErrorBanner';
import { EmptyState } from '@/components/EmptyState';
import { ConfirmationDialog } from '@/components/ConfirmationDialog';
import { ConflictModal } from '@/components/ConflictModal';
import type {
  OperationDetailOut,
  LaborOut,
  InspectionOut,
  VersionConflictError,
  PredecessorSummary,
} from '@/types/api';

/* ── Constants ── */

const REFETCH_INTERVAL_MS = 30_000;

/* ── Helpers ── */

function humanize(s: string): string {
  return s
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function formatDatetime(iso: string | null): string {
  if (!iso) return '--';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatHours(hours: number): string {
  return `${hours.toFixed(2)}h`;
}

function extractConflict(err: unknown): VersionConflictError | null {
  const resp = (err as { response?: { status?: number; data?: { error?: VersionConflictError } } })?.response;
  if (resp?.status === 409 && resp.data?.error?.code === 'VERSION_CONFLICT') {
    return resp.data.error as VersionConflictError;
  }
  return null;
}

function extractErrorMessage(err: unknown): string {
  const resp = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
  if (resp?.status === 422) {
    const detail = resp.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail.map((d: { detail?: string; msg?: string }) => d.detail ?? d.msg ?? '').join('; ');
    }
  }
  return err instanceof Error ? err.message : 'Operation failed.';
}

/* ── Inline styles ── */

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  padding: 'var(--space-sm) var(--space-md)',
  borderBottom: '2px solid var(--border)',
  fontSize: 'var(--font-size-label)',
  color: 'var(--text-secondary)',
  fontWeight: 600,
  whiteSpace: 'nowrap',
};

const tdStyle: React.CSSProperties = {
  padding: 'var(--space-sm) var(--space-md)',
  borderBottom: '1px solid var(--border)',
  fontSize: 'var(--font-size-table)',
  color: 'var(--text-primary)',
};

const cardStyle: React.CSSProperties = {
  backgroundColor: 'var(--bg-surface)',
  borderRadius: 'var(--radius-md)',
  padding: 'var(--space-md)',
  marginBottom: 'var(--space-md)',
};

const actionBtnStyle: React.CSSProperties = {
  padding: 'var(--space-sm) var(--space-md)',
  border: 'none',
  borderRadius: 'var(--radius-md)',
  fontSize: 'var(--font-size-body)',
  fontWeight: 600,
  cursor: 'pointer',
  color: '#ffffff',
};

/* ── Sub-components ── */

function PredecessorMiniGraph({
  predecessors,
}: {
  predecessors: PredecessorSummary[];
}) {
  if (predecessors.length === 0) return null;

  return (
    <div style={cardStyle}>
      <h3
        style={{
          margin: 0,
          marginBottom: 'var(--space-sm)',
          fontSize: 'var(--font-size-section)',
          color: 'var(--text-primary)',
        }}
      >
        Predecessors
      </h3>
      <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
        {predecessors.map((p) => (
          <div
            key={p.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-xs)',
              padding: 'var(--space-xs) var(--space-sm)',
              backgroundColor: 'var(--bg-elevated)',
              borderRadius: 'var(--radius-md)',
              fontSize: 'var(--font-size-table)',
            }}
          >
            <span style={{ fontWeight: 600 }}>Op {p.sequence_number}</span>
            <StatusBadge status={p.status} />
          </div>
        ))}
      </div>
    </div>
  );
}

function LaborTable({
  entries,
  stopRequests,
}: {
  entries: LaborOut[];
  stopRequests: Set<string>;
}) {
  if (entries.length === 0) {
    return (
      <EmptyState
        title="No labor entries"
        message="No labor has been recorded for this operation."
      />
    );
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table
        style={{
          width: '100%',
          borderCollapse: 'collapse',
          backgroundColor: 'var(--bg-surface)',
        }}
      >
        <thead>
          <tr>
            <th style={thStyle}>User</th>
            <th style={thStyle}>Start</th>
            <th style={thStyle}>End</th>
            <th style={thStyle}>Duration</th>
            <th style={thStyle}>Cycle</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const isStopRequest = stopRequests.has(entry.id);
            /* Stop-request entries highlighted amber per spec */
            const rowBg = isStopRequest ? 'rgba(245, 158, 11, 0.15)' : 'transparent';

            return (
              <tr
                key={entry.id}
                style={{ backgroundColor: rowBg }}
                title={isStopRequest ? 'Stop request pending' : undefined}
              >
                <td style={tdStyle}>{entry.user_id}</td>
                <td style={tdStyle}>{formatDatetime(entry.started_at)}</td>
                <td style={tdStyle}>
                  {entry.stopped_at ? (
                    formatDatetime(entry.stopped_at)
                  ) : (
                    <span
                      style={{
                        color: 'var(--status-active)',
                        fontWeight: 600,
                      }}
                    >
                      Running
                    </span>
                  )}
                </td>
                <td style={tdStyle}>{formatHours(entry.hours)}</td>
                <td style={tdStyle}>{entry.execution_cycle}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function InspectionHistory({ inspections }: { inspections: InspectionOut[] }) {
  if (inspections.length === 0) {
    return (
      <EmptyState
        title="No inspections"
        message="No inspections have been recorded for this operation."
      />
    );
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table
        style={{
          width: '100%',
          borderCollapse: 'collapse',
          backgroundColor: 'var(--bg-surface)',
        }}
      >
        <thead>
          <tr>
            <th style={thStyle}>Criteria</th>
            <th style={thStyle}>Result</th>
            <th style={thStyle}>Inspector</th>
            <th style={thStyle}>Date</th>
            <th style={thStyle}>Cycle</th>
            <th style={thStyle}>Notes</th>
          </tr>
        </thead>
        <tbody>
          {inspections.map((insp) => (
            <tr key={insp.id}>
              <td style={tdStyle}>{insp.criteria}</td>
              <td style={tdStyle}>
                <StatusBadge
                  status={
                    insp.result === 'accept'
                      ? 'complete'
                      : insp.result === 'reject'
                        ? 'open'
                        : 'hold'
                  }
                  label={humanize(insp.result)}
                />
              </td>
              <td style={tdStyle}>{insp.inspector_id}</td>
              <td style={tdStyle}>{formatDatetime(insp.inspected_at)}</td>
              <td style={tdStyle}>{insp.execution_cycle}</td>
              <td style={tdStyle}>{insp.notes ?? '--'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Main Component ── */

export default function OperationDetail() {
  const { woId, opId } = useParams<{ woId: string; opId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { hasAnyRole, activeProgram } = useAuthStore();
  const programId = activeProgram?.id ?? 'none';

  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [conflictError, setConflictError] = useState<VersionConflictError | null>(null);
  const [pendingAction, setPendingAction] = useState<{
    title: string;
    message: string;
    action: () => void;
    destructive?: boolean;
  } | null>(null);

  /* ── Queries ── */

  const opQuery = useQuery<OperationDetailOut>({
    queryKey: ['operation', programId, opId],
    queryFn: () => fetchOperationDetail(opId!),
    enabled: !!opId,
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const laborQuery = useQuery<LaborOut[]>({
    queryKey: ['operation-labor', programId, opId],
    queryFn: () => fetchOperationLabor(opId!),
    enabled: !!opId,
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const inspectionQuery = useQuery<InspectionOut[]>({
    queryKey: ['operation-inspections', programId, opId],
    queryFn: () => fetchOperationInspections(opId!),
    enabled: !!opId,
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const op = opQuery.data;
  const laborEntries = laborQuery.data ?? [];
  const inspections = inspectionQuery.data ?? [];

  /* Build a set of labor entry IDs that have pending stop requests */
  const stopRequestEntryIds = useMemo(
    () => new Set((op?.stop_requests ?? []).map((sr) => sr.labor_entry_id)),
    [op?.stop_requests],
  );

  /* ── Cache invalidation ── */

  const invalidateAll = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['operation', programId, opId] });
    queryClient.invalidateQueries({ queryKey: ['operation-labor', programId, opId] });
    queryClient.invalidateQueries({ queryKey: ['operation-inspections', programId, opId] });
  }, [queryClient, programId, opId]);

  /* ── Mutations (hold/resume only for engineer view) ── */

  const handleMutationError = useCallback((err: unknown) => {
    setPendingAction(null);
    const conflict = extractConflict(err);
    if (conflict) {
      setConflictError(conflict);
      return;
    }
    setErrorMsg(extractErrorMessage(err));
  }, []);

  const holdMutation = useMutation({
    mutationFn: () => holdOperation(opId!, { version: op?.version }),
    onSuccess: () => {
      setErrorMsg(null);
      setPendingAction(null);
      invalidateAll();
    },
    onError: handleMutationError,
  });

  const resumeMutation = useMutation({
    mutationFn: () => resumeOperation(opId!, { version: op?.version }),
    onSuccess: () => {
      setErrorMsg(null);
      setPendingAction(null);
      invalidateAll();
    },
    onError: handleMutationError,
  });

  /* ── Loading ── */
  if (opQuery.isLoading) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <LoadingSkeleton rows={10} />
      </div>
    );
  }

  /* ── Error ── */
  if (opQuery.isError || !op) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <ErrorBanner
          message={
            opQuery.error instanceof Error
              ? opQuery.error.message
              : 'Failed to load operation.'
          }
        />
      </div>
    );
  }

  return (
    <div style={{ padding: 'var(--space-lg)' }}>
      {/* Safety-critical banner */}
      <SafetyCriticalBanner visible={op.safety_critical} />

      {/* Error banner */}
      {errorMsg && (
        <ErrorBanner message={errorMsg} onDismiss={() => setErrorMsg(null)} />
      )}

      {/* Header */}
      <div style={{ marginBottom: 'var(--space-md)' }}>
        <button
          type="button"
          onClick={() => navigate(`/work-orders/${woId}`)}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--interactive)',
            cursor: 'pointer',
            fontSize: 'var(--font-size-body)',
            padding: 0,
            marginBottom: 'var(--space-sm)',
          }}
        >
          &larr; Back to Work Order
        </button>
        <h1
          style={{
            margin: 0,
            fontSize: 'var(--font-size-page)',
            color: 'var(--text-primary)',
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-sm)',
            flexWrap: 'wrap',
          }}
        >
          Op {op.sequence_number ?? op.sequence}: {op.description}
          <StatusBadge status={op.status} />
          <span
            style={{
              fontSize: 'var(--font-size-badge)',
              color: 'var(--text-muted)',
              fontWeight: 400,
            }}
          >
            (Engineer View)
          </span>
        </h1>
        <div
          style={{
            marginTop: 'var(--space-xs)',
            fontSize: 'var(--font-size-table)',
            color: 'var(--text-muted)',
          }}
        >
          Execution Cycle: {op.execution_cycle}
        </div>
      </div>

      {/* Predecessor mini graph */}
      <PredecessorMiniGraph predecessors={op.predecessor_summaries ?? []} />

      {/* Actions: Hold / Resume (engineer-only, not Start/Complete) */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-md)', flexWrap: 'wrap' }}>
        {op.status === 'in_progress' && hasAnyRole('engineer', 'qa_manager') && (
          <button
            type="button"
            onClick={() =>
              setPendingAction({
                title: 'Hold Operation',
                message: `Place Op ${op.sequence_number ?? op.sequence} on hold?`,
                action: () => holdMutation.mutate(),
                destructive: true,
              })
            }
            style={{
              ...actionBtnStyle,
              backgroundColor: 'var(--destructive)',
            }}
          >
            Hold Operation
          </button>
        )}

        {op.status === 'hold' && hasAnyRole('engineer', 'qa_manager') && (
          <button
            type="button"
            onClick={() =>
              setPendingAction({
                title: 'Resume Operation',
                message: `Resume Op ${op.sequence_number ?? op.sequence}?`,
                action: () => resumeMutation.mutate(),
              })
            }
            style={{
              ...actionBtnStyle,
              backgroundColor: 'var(--interactive)',
            }}
          >
            Resume Operation
          </button>
        )}
      </div>

      {/* Labor entries table */}
      <div style={cardStyle}>
        <h2
          style={{
            margin: 0,
            marginBottom: 'var(--space-sm)',
            fontSize: 'var(--font-size-section)',
            color: 'var(--text-primary)',
          }}
        >
          Labor Entries
        </h2>
        {laborQuery.isLoading ? (
          <LoadingSkeleton rows={4} />
        ) : (
          <LaborTable entries={laborEntries} stopRequests={stopRequestEntryIds} />
        )}
      </div>

      {/* Inspection history */}
      <div style={cardStyle}>
        <h2
          style={{
            margin: 0,
            marginBottom: 'var(--space-sm)',
            fontSize: 'var(--font-size-section)',
            color: 'var(--text-primary)',
          }}
        >
          Inspection History
        </h2>
        {inspectionQuery.isLoading ? (
          <LoadingSkeleton rows={3} />
        ) : (
          <InspectionHistory inspections={inspections} />
        )}
      </div>

      {/* Material consumption placeholder */}
      <div style={cardStyle}>
        <h2
          style={{
            margin: 0,
            marginBottom: 'var(--space-sm)',
            fontSize: 'var(--font-size-section)',
            color: 'var(--text-primary)',
          }}
        >
          Material Consumption
        </h2>
        <div
          style={{
            color: 'var(--text-secondary)',
            fontSize: 'var(--font-size-body)',
            padding: 'var(--space-md)',
            textAlign: 'center',
          }}
        >
          Material consumption tracking will be available in a future release.
        </div>
      </div>

      {/* Confirmation dialog */}
      <ConfirmationDialog
        open={pendingAction !== null}
        title={pendingAction?.title ?? ''}
        message={pendingAction?.message ?? ''}
        confirmLabel={pendingAction?.title ?? 'Confirm'}
        destructive={pendingAction?.destructive ?? false}
        loading={holdMutation.isPending || resumeMutation.isPending}
        onConfirm={() => pendingAction?.action()}
        onCancel={() => setPendingAction(null)}
      />

      {/* Conflict modal */}
      <ConflictModal
        error={conflictError}
        onDiscard={() => setConflictError(null)}
        onReload={() => {
          setConflictError(null);
          invalidateAll();
        }}
      />
    </div>
  );
}
