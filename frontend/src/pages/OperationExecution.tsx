/**
 * Module:   OperationExecution
 * Purpose:  Operator's primary work screen with labor timers, SSE, and hold-point gating (spec section 5.4).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * This page subscribes to SSE for real-time updates (hold_point_changed,
 * wo_state_changed, labor_activity, operation_reopened).  On every SSE
 * reconnect the full operation state is refetched via REST.
 *
 * Predecessor gating: the Start button is disabled when any predecessor
 * operation has not reached 'complete'.  The tooltip lists the blocking
 * predecessors by sequence and description.
 *
 * Labor timer: elapsed time is computed client-side from started_at and
 * updated every second via setInterval.  All timer starts/stops go
 * through mutations and are confirmed by the server response.
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  fetchOperationDetail,
  startOperation,
  completeOperation,
  holdOperation,
  resumeOperation,
  createLabor,
  stopLabor,
  fetchOperationLabor,
} from '@/api/endpoints';
import { useOperationSSE } from '@/hooks/useOperationSSE';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { SafetyCriticalBanner } from '@/components/SafetyCriticalBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ErrorBanner } from '@/components/ErrorBanner';
import { ConfirmationDialog } from '@/components/ConfirmationDialog';
import { ConflictModal } from '@/components/ConflictModal';
import { ConnectionBanner } from '@/components/ConnectionBanner';
import type {
  OperationDetailOut,
  LaborOut,
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

function formatDuration(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
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

function HoldPointDisplay({ status }: { status: string }) {
  const descriptions: Record<string, string> = {
    none: 'No inspection hold point on this operation.',
    pending: 'Inspection required before this operation can be completed.',
    satisfied: 'Inspection hold point has been satisfied.',
    conditionally_satisfied: 'Inspection completed with conditions. Review required.',
    blocked: 'Inspection has blocked this operation. NCR or re-inspection required.',
  };

  const colors: Record<string, string> = {
    none: 'var(--text-muted)',
    pending: 'var(--status-hold)',
    satisfied: 'var(--status-complete)',
    conditionally_satisfied: 'var(--status-hold)',
    blocked: 'var(--status-error)',
  };

  return (
    <div
      style={{
        padding: 'var(--space-sm) var(--space-md)',
        borderLeft: `3px solid ${colors[status] ?? 'var(--border)'}`,
        backgroundColor: 'var(--bg-elevated)',
        borderRadius: '0 var(--radius-md) var(--radius-md) 0',
        fontSize: 'var(--font-size-body)',
        color: 'var(--text-primary)',
      }}
    >
      <strong>Hold Point:</strong> {humanize(status)} --{' '}
      {descriptions[status] ?? 'Unknown hold point status.'}
    </div>
  );
}

function PredecessorGate({
  predecessors,
}: {
  predecessors: PredecessorSummary[];
}) {
  const incomplete = predecessors.filter((p) => p.status !== 'complete');
  if (incomplete.length === 0) return null;

  return (
    <div
      style={{
        padding: 'var(--space-sm) var(--space-md)',
        backgroundColor: 'var(--bg-elevated)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        marginBottom: 'var(--space-md)',
        fontSize: 'var(--font-size-body)',
      }}
    >
      <strong style={{ color: 'var(--text-primary)' }}>
        Waiting for predecessor operations:
      </strong>
      <ul style={{ margin: 'var(--space-xs) 0 0 0', paddingLeft: 'var(--space-md)' }}>
        {incomplete.map((p) => (
          <li key={p.id} style={{ color: 'var(--text-secondary)', marginBottom: '2px' }}>
            Op {p.sequence_number} -- {p.description} ({humanize(p.status)})
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Displays a single active labor timer with live elapsed time. */
function ActiveTimer({
  entry,
  onStop,
  isStopping,
}: {
  entry: LaborOut;
  onStop: () => void;
  isStopping: boolean;
}) {
  const [elapsed, setElapsed] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const startTime = new Date(entry.started_at).getTime();
    const tick = () => setElapsed(Date.now() - startTime);
    tick();
    intervalRef.current = setInterval(tick, 1000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [entry.started_at]);

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: 'var(--space-sm) var(--space-md)',
        backgroundColor: 'var(--bg-elevated)',
        borderRadius: 'var(--radius-md)',
        marginBottom: 'var(--space-xs)',
      }}
    >
      <div>
        <span
          style={{
            fontFamily: 'monospace',
            fontSize: '1.4em',
            fontWeight: 700,
            color: 'var(--status-active)',
          }}
        >
          {formatDuration(elapsed)}
        </span>
        <span
          style={{
            marginLeft: 'var(--space-sm)',
            fontSize: 'var(--font-size-table)',
            color: 'var(--text-muted)',
          }}
        >
          Started {new Date(entry.started_at).toLocaleTimeString()}
        </span>
      </div>
      <button
        type="button"
        onClick={onStop}
        disabled={isStopping}
        style={{
          ...actionBtnStyle,
          backgroundColor: 'var(--destructive)',
          opacity: isStopping ? 0.6 : 1,
          cursor: isStopping ? 'not-allowed' : 'pointer',
        }}
      >
        {isStopping ? 'Stopping...' : 'Stop Timer'}
      </button>
    </div>
  );
}

/* ── Main Component ── */

export default function OperationExecution() {
  const { woId, opId } = useParams<{ woId: string; opId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { hasAnyRole, activeProgram, user } = useAuthStore();
  const programId = activeProgram?.id ?? 'none';

  const [sseConnected, setSseConnected] = useState(true);
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

  const op = opQuery.data;
  /* Memoize the list so downstream useMemo deps don't change every render. */
  const laborEntries = useMemo(() => laborQuery.data ?? [], [laborQuery.data]);

  /* Active labor entries for the current user on this operation */
  const myActiveLabor = useMemo(
    () =>
      laborEntries.filter(
        (l) => l.user_id === user?.id && l.stopped_at === null,
      ),
    [laborEntries, user?.id],
  );

  const anyActiveLabor = useMemo(
    () => laborEntries.filter((l) => l.stopped_at === null),
    [laborEntries],
  );

  /* ── Cache invalidation helper ── */

  const invalidateAll = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['operation', programId, opId] });
    queryClient.invalidateQueries({ queryKey: ['operation-labor', programId, opId] });
  }, [queryClient, programId, opId]);

  /* ── SSE ── */

  useOperationSSE(opId, {
    onReconnect: invalidateAll,
    onConnectionLost: () => setSseConnected(false),
    onConnectionRestored: () => setSseConnected(true),
    onHoldPointChanged: () => invalidateAll(),
    onWoStateChanged: () => {
      invalidateAll();
      /* Also invalidate the parent WO query if cached */
      queryClient.invalidateQueries({ queryKey: ['work-order', programId, woId] });
    },
    onLaborActivity: () => invalidateAll(),
    onOperationReopened: () => invalidateAll(),
  });

  /* ── Mutations ── */

  const handleMutationError = useCallback((err: unknown) => {
    const conflict = extractConflict(err);
    if (conflict) {
      setConflictError(conflict);
      return;
    }
    setErrorMsg(extractErrorMessage(err));
  }, []);

  /* pending → in_progress.  Invariant: parent WO released or in_progress,
     all predecessors complete, parent WO not on hold.  Server validates
     via OP-START-001..003.  Permission: operator. */
  const startMutation = useMutation({
    mutationFn: () => startOperation(opId!, { version: op?.version }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidateAll();
    },
    onError: handleMutationError,
  });

  /* in_progress → complete.  Invariant: no active labor entries,
     hold_point_status ∈ {satisfied, conditionally_satisfied},
     no unresolved NCRs, parent WO not on hold.  Server validates
     via OP-COMP-001..004.  Permission: operator. */
  const completeMutation = useMutation({
    mutationFn: () => completeOperation(opId!, { version: op?.version }),
    onSuccess: () => {
      setErrorMsg(null);
      setPendingAction(null);
      invalidateAll();
    },
    onError: (err) => {
      setPendingAction(null);
      handleMutationError(err);
    },
  });

  /* in_progress → hold.  No preconditions beyond op being in_progress.
     Permission: operator, engineer. */
  const holdMutation = useMutation({
    mutationFn: () => holdOperation(opId!, { version: op?.version }),
    onSuccess: () => {
      setErrorMsg(null);
      setPendingAction(null);
      invalidateAll();
    },
    onError: (err) => {
      setPendingAction(null);
      handleMutationError(err);
    },
  });

  /* hold → in_progress.  Invariant: parent WO not on hold.
     Server validates via OP-RESUME-001.  Permission: operator, engineer. */
  const resumeMutation = useMutation({
    mutationFn: () => resumeOperation(opId!, { version: op?.version }),
    onSuccess: () => {
      setErrorMsg(null);
      setPendingAction(null);
      invalidateAll();
    },
    onError: (err) => {
      setPendingAction(null);
      handleMutationError(err);
    },
  });

  const createLaborMutation = useMutation({
    mutationFn: () =>
      createLabor(opId!, {
        role: user?.roles?.[0]?.name ?? 'operator',
        shift: 'day',
      }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidateAll();
    },
    onError: handleMutationError,
  });

  const stopLaborMutation = useMutation({
    mutationFn: (entryId: string) => {
      const entry = laborEntries.find((l) => l.id === entryId);
      return stopLabor(entryId, { version: entry?.version });
    },
    onSuccess: () => {
      setErrorMsg(null);
      invalidateAll();
    },
    onError: handleMutationError,
  });

  /* ── Derived state ── */

  const predecessorsIncomplete = useMemo(
    () => (op?.predecessor_summaries ?? []).filter((p) => p.status !== 'complete'),
    [op],
  );

  const canStart =
    op?.status === 'pending' &&
    predecessorsIncomplete.length === 0 &&
    op?.work_order_status !== 'hold';

  const canCreateLabor =
    op?.status === 'in_progress' && op?.work_order_status !== 'hold';

  const holdPointBlocks =
    op?.hold_point_status === 'pending' || op?.hold_point_status === 'blocked';

  const canComplete =
    op?.status === 'in_progress' &&
    !holdPointBlocks &&
    anyActiveLabor.length === 0;

  const completeDisabledReason = useMemo(() => {
    if (op?.status !== 'in_progress') return null;
    if (holdPointBlocks) return 'Hold point must be satisfied before completing.';
    if (anyActiveLabor.length > 0) return 'Stop all active labor timers before completing.';
    return null;
  }, [op?.status, holdPointBlocks, anyActiveLabor.length]);

  const startDisabledReason = useMemo(() => {
    if (op?.status !== 'pending') return null;
    if (op?.work_order_status === 'hold') return 'Work order is on hold.';
    if (predecessorsIncomplete.length > 0) {
      return `Waiting for: ${predecessorsIncomplete.map((p) => `Op ${p.sequence_number} -- ${p.description} (${humanize(p.status)})`).join(', ')}`;
    }
    return null;
  }, [op, predecessorsIncomplete]);

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
      {/* SSE connection banner */}
      <ConnectionBanner connected={sseConnected} />

      {/* Safety-critical banner */}
      <SafetyCriticalBanner visible={op.safety_critical} />

      {/* Error banner */}
      {errorMsg && (
        <ErrorBanner message={errorMsg} onDismiss={() => setErrorMsg(null)} />
      )}

      {/* WO-on-hold banner */}
      {op.work_order_status === 'hold' && (
        <div
          role="alert"
          style={{
            padding: 'var(--space-sm) var(--space-md)',
            backgroundColor: 'var(--status-hold)',
            color: '#ffffff',
            borderRadius: 'var(--radius-md)',
            fontWeight: 600,
            marginBottom: 'var(--space-md)',
          }}
        >
          Work order is ON HOLD. Operations are suspended.
        </div>
      )}

      {/* Reopen banner (execution_cycle > 1 while in_progress) */}
      {op.status === 'in_progress' && op.execution_cycle > 1 && (
        <div
          role="status"
          style={{
            padding: 'var(--space-sm) var(--space-md)',
            backgroundColor: 'var(--status-active)',
            color: '#ffffff',
            borderRadius: 'var(--radius-md)',
            fontWeight: 600,
            marginBottom: 'var(--space-md)',
          }}
        >
          This operation has been reopened (execution cycle {op.execution_cycle}).
        </div>
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
          }}
        >
          Op {op.sequence_number}: {op.description}
          <StatusBadge status={op.status} />
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

      {/* Predecessor gate */}
      <PredecessorGate predecessors={op.predecessor_summaries ?? []} />

      {/* Hold point display */}
      {op.hold_point_status && op.hold_point_status !== 'none' && (
        <div style={{ marginBottom: 'var(--space-md)' }}>
          <HoldPointDisplay status={op.hold_point_status} />
        </div>
      )}

      {/* Labor timer section */}
      <div style={cardStyle}>
        <h2
          style={{
            margin: 0,
            marginBottom: 'var(--space-sm)',
            fontSize: 'var(--font-size-section)',
            color: 'var(--text-primary)',
          }}
        >
          Labor Timer
        </h2>

        {/* Active timers */}
        {myActiveLabor.length > 0 ? (
          myActiveLabor.map((entry) => (
            <ActiveTimer
              key={entry.id}
              entry={entry}
              onStop={() => stopLaborMutation.mutate(entry.id)}
              isStopping={stopLaborMutation.isPending}
            />
          ))
        ) : (
          <div
            style={{
              color: 'var(--text-secondary)',
              fontSize: 'var(--font-size-body)',
              marginBottom: 'var(--space-sm)',
            }}
          >
            No active timer.
          </div>
        )}

        {/* Start timer / Start operation button */}
        {op.status === 'pending' && (
          <div title={startDisabledReason ?? undefined}>
            <button
              type="button"
              onClick={() => startMutation.mutate()}
              disabled={!canStart || startMutation.isPending}
              style={{
                ...actionBtnStyle,
                backgroundColor: 'var(--status-complete)',
                opacity: canStart && !startMutation.isPending ? 1 : 0.5,
                cursor: canStart && !startMutation.isPending ? 'pointer' : 'not-allowed',
                marginTop: 'var(--space-sm)',
              }}
              title={startDisabledReason ?? 'Start this operation and begin labor timer'}
            >
              {startMutation.isPending ? 'Starting...' : 'Start Operation'}
            </button>
          </div>
        )}

        {op.status === 'in_progress' && myActiveLabor.length === 0 && (
          <button
            type="button"
            onClick={() => createLaborMutation.mutate()}
            disabled={!canCreateLabor || createLaborMutation.isPending}
            style={{
              ...actionBtnStyle,
              backgroundColor: 'var(--status-complete)',
              opacity: canCreateLabor && !createLaborMutation.isPending ? 1 : 0.5,
              cursor: canCreateLabor && !createLaborMutation.isPending ? 'pointer' : 'not-allowed',
              marginTop: 'var(--space-sm)',
            }}
          >
            {createLaborMutation.isPending ? 'Starting...' : 'Start Timer'}
          </button>
        )}
      </div>

      {/* Action buttons */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
        {/* Complete Operation */}
        {op.status === 'in_progress' && (
          <div title={completeDisabledReason ?? undefined}>
            <button
              type="button"
              onClick={() =>
                setPendingAction({
                  title: 'Complete Operation',
                  message: `Mark Op ${op.sequence_number} as complete? This cannot be undone unless an NCR triggers a reopen.`,
                  action: () => completeMutation.mutate(),
                })
              }
              disabled={!canComplete}
              style={{
                ...actionBtnStyle,
                backgroundColor: 'var(--interactive)',
                opacity: canComplete ? 1 : 0.5,
                cursor: canComplete ? 'pointer' : 'not-allowed',
              }}
              title={completeDisabledReason ?? 'Complete this operation'}
            >
              Complete Operation
            </button>
          </div>
        )}

        {/* Hold Operation (engineer role) */}
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

        {/* Resume Operation (engineer role) */}
        {op.status === 'hold' && hasAnyRole('engineer', 'qa_manager') && (
          <button
            type="button"
            onClick={() =>
              setPendingAction({
                title: 'Resume Operation',
                message: `Resume Op ${op.sequence_number}?`,
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

      {/* Confirmation dialog */}
      <ConfirmationDialog
        open={pendingAction !== null}
        title={pendingAction?.title ?? ''}
        message={pendingAction?.message ?? ''}
        confirmLabel={pendingAction?.title ?? 'Confirm'}
        destructive={pendingAction?.destructive ?? false}
        loading={
          completeMutation.isPending ||
          holdMutation.isPending ||
          resumeMutation.isPending
        }
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
