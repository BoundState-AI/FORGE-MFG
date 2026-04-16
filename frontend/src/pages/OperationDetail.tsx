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

import { useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  fetchOperationDetail,
  holdOperation,
} from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { SafetyCriticalBanner } from '@/components/SafetyCriticalBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ErrorBanner } from '@/components/ErrorBanner';
import { ConfirmationDialog } from '@/components/ConfirmationDialog';
import { ConflictModal } from '@/components/ConflictModal';
import type {
  OperationDetailOut,
  VersionConflictError,
  PredecessorSummary,
} from '@/types/api';

/* ── Constants ── */

const REFETCH_INTERVAL_MS = 30_000;

/* ── Helpers ── */

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

/* LaborTable and InspectionHistory sub-components removed from this slice:
   the backend does not yet expose GET endpoints for per-operation labor
   or inspection history.  They will be re-added when those endpoints
   ship — see endpoints.ts header for the full deferred list. */

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

  const op = opQuery.data;

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

  /* Resume from hold is not exposed as a backend route — see
     endpoints.ts notes.  The UI simply omits the Resume button until
     the backend adds POST /api/operations/:id/resume. */

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
          Op {op.sequence_number}: {op.description}
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
                message: `Place Op ${op.sequence_number} on hold?`,
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

        {/* Resume button deferred until backend exposes the resume route. */}
      </div>

      {/* Labor entries, inspection history, and material consumption
         tables are deferred to a future slice — the backend does not
         yet expose the supporting GET endpoints.  See endpoints.ts
         header comments for the full deferred list. */}
      <div style={cardStyle}>
        <div
          style={{
            color: 'var(--text-secondary)',
            fontSize: 'var(--font-size-body)',
            padding: 'var(--space-md)',
            textAlign: 'center',
          }}
        >
          Labor entries, inspection history, and material consumption
          tables will be wired up when the matching backend GET endpoints
          ship.  Core execution actions (hold / operator flows) are live.
        </div>
      </div>

      {/* Confirmation dialog */}
      <ConfirmationDialog
        open={pendingAction !== null}
        title={pendingAction?.title ?? ''}
        message={pendingAction?.message ?? ''}
        confirmLabel={pendingAction?.title ?? 'Confirm'}
        destructive={pendingAction?.destructive ?? false}
        loading={holdMutation.isPending}
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
