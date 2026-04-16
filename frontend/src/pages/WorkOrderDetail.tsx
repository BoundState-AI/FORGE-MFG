/**
 * Module:   WorkOrderDetail
 * Purpose:  Work order detail view with operation list, state transitions, and audit tab (spec section 5.3).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * State transitions are gated by role and current WO status per spec
 * section 5.3 UI Button Visibility.  All mutations go through
 * ConfirmationDialog.  The hold dialog includes a force_stop_labor
 * checkbox.  409 conflicts surface ConflictModal; 422 errors surface
 * ErrorBanner.  30-second refetch interval keeps data current.
 */

import { useState, useMemo, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  fetchWorkOrder,
  fetchWorkOrderOperations,
  releaseWorkOrder,
  recallWorkOrder,
  holdWorkOrder,
  resumeWorkOrder,
  completeWorkOrder,
} from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ErrorBanner } from '@/components/ErrorBanner';
import { EmptyState } from '@/components/EmptyState';
import { ConfirmationDialog } from '@/components/ConfirmationDialog';
import { ConflictModal } from '@/components/ConflictModal';
import type {
  WorkOrderOut,
  OperationOut,
  WOStatus,
  VersionConflictError,
} from '@/types/api';

/* ── Constants ── */

const REFETCH_INTERVAL_MS = 30_000;

type TransitionAction =
  | 'release'
  | 'recall'
  | 'hold'
  | 'resume'
  | 'complete';

interface TransitionConfig {
  label: string;
  action: TransitionAction;
  confirmTitle: string;
  confirmMessage: string;
  destructive?: boolean;
}

/* ── Helpers ── */

function humanize(s: string): string {
  return s
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function formatDate(iso: string | null): string {
  if (!iso) return '--';
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Extracts a version conflict body from an axios error response. */
function extractConflict(err: unknown): VersionConflictError | null {
  const resp = (err as { response?: { status?: number; data?: { error?: VersionConflictError } } })?.response;
  if (resp?.status === 409 && resp.data?.error?.code === 'VERSION_CONFLICT') {
    return resp.data.error as VersionConflictError;
  }
  return null;
}

/** Extracts a 422 detail string from an axios error response. */
function extract422(err: unknown): string | null {
  const resp = (err as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
  if (resp?.status === 422) {
    const detail = resp.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail.map((d: { detail?: string; msg?: string }) => d.detail ?? d.msg ?? '').join('; ');
    }
  }
  return null;
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

const actionBtnStyle: React.CSSProperties = {
  padding: 'var(--space-sm) var(--space-md)',
  border: 'none',
  borderRadius: 'var(--radius-md)',
  backgroundColor: 'var(--interactive)',
  color: '#ffffff',
  fontSize: 'var(--font-size-body)',
  fontWeight: 600,
  cursor: 'pointer',
};

const tabStyle = (active: boolean): React.CSSProperties => ({
  padding: 'var(--space-sm) var(--space-md)',
  border: 'none',
  borderBottom: active ? '2px solid var(--interactive)' : '2px solid transparent',
  backgroundColor: 'transparent',
  color: active ? 'var(--interactive)' : 'var(--text-secondary)',
  fontSize: 'var(--font-size-body)',
  fontWeight: active ? 600 : 400,
  cursor: 'pointer',
});

/* ── Sub-components ── */

function HoldBanner() {
  return (
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
      This work order is ON HOLD. Active operations are suspended.
    </div>
  );
}

function HeaderField({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ minWidth: '120px' }}>
      <div
        style={{
          fontSize: 'var(--font-size-label)',
          color: 'var(--text-secondary)',
          marginBottom: '2px',
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 'var(--font-size-body)',
          color: 'var(--text-primary)',
          fontWeight: 500,
        }}
      >
        {value}
      </div>
    </div>
  );
}

/* ── Main Component ── */

export default function WorkOrderDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { hasRole, hasAnyRole, activeProgram } = useAuthStore();
  const programId = activeProgram?.id ?? 'none';

  const [activeTab, setActiveTab] = useState<'operations' | 'audit'>('operations');
  const [pendingTransition, setPendingTransition] = useState<TransitionConfig | null>(null);
  const [forceStopLabor, setForceStopLabor] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [conflictError, setConflictError] = useState<VersionConflictError | null>(null);

  /* ── Queries ── */

  const woQuery = useQuery<WorkOrderOut>({
    queryKey: ['work-order', programId, id],
    queryFn: () => fetchWorkOrder(id!),
    enabled: !!id,
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const opsQuery = useQuery<OperationOut[]>({
    queryKey: ['work-order-operations', programId, id],
    queryFn: () => fetchWorkOrderOperations(id!),
    enabled: !!id,
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const wo = woQuery.data;
  const ops = opsQuery.data ?? [];

  /* ── Mutations ── */

  const invalidateAll = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['work-order', programId, id] });
    queryClient.invalidateQueries({ queryKey: ['work-order-operations', programId, id] });
  }, [queryClient, programId, id]);

  const transitionMutation = useMutation({
    mutationFn: async (action: TransitionAction) => {
      if (!wo) throw new Error('Work order not loaded');
      const version = wo.version;
      switch (action) {
        case 'release':
          /* draft → released.  Invariant: all ops defined, DAG valid.
             Server validates via WO-REL-101..103.  Permission: engineer. */
          return releaseWorkOrder(wo.id, { version });
        case 'recall':
          /* released → draft.  Invariant: no operations started.
             Server rejects if any op left pending state.  Permission: engineer. */
          return recallWorkOrder(wo.id, { version });
        case 'hold':
          /* in_progress → hold.  Optional force_stop_labor stops all
             active labor timers atomically (spec §5.3 Hold Cascading).
             Permission: engineer, qa_manager. */
          return holdWorkOrder(wo.id, { version, force_stop_labor: forceStopLabor });
        case 'resume':
          /* hold → in_progress.  Invariant: none (the hold is unconditional).
             Permission: engineer, qa_manager. */
          return resumeWorkOrder(wo.id, { version });
        case 'complete':
          /* in_progress → complete.  Invariant: all ops completed,
             all hold points satisfied or conditionally satisfied,
             no open NCRs.  Server checks all preconditions.
             Permission: engineer. */
          return completeWorkOrder(wo.id, { version });
      }
    },
    onSuccess: () => {
      setPendingTransition(null);
      setForceStopLabor(false);
      setErrorMsg(null);
      invalidateAll();
    },
    onError: (err: unknown) => {
      setPendingTransition(null);
      setForceStopLabor(false);

      const conflict = extractConflict(err);
      if (conflict) {
        setConflictError(conflict);
        return;
      }

      const detail = extract422(err);
      if (detail) {
        setErrorMsg(detail);
        return;
      }

      setErrorMsg(
        err instanceof Error ? err.message : 'Transition failed.',
      );
    },
  });

  /* ── Button visibility per spec section 5.3 ── */

  const actionButtons = useMemo((): TransitionConfig[] => {
    if (!wo) return [];
    const status: WOStatus = wo.status;
    const btns: TransitionConfig[] = [];

    if (status === 'draft' && hasRole('engineer')) {
      btns.push({
        label: 'Release Work Order',
        action: 'release',
        confirmTitle: 'Release Work Order',
        confirmMessage: `Release ${wo.wo_number} to production? Operations will become executable.`,
      });
    }

    if (status === 'released' && hasRole('engineer')) {
      btns.push({
        label: 'Recall to Draft',
        action: 'recall',
        confirmTitle: 'Recall to Draft',
        confirmMessage: `Recall ${wo.wo_number} back to draft status?`,
        destructive: true,
      });
    }

    if (status === 'in_progress') {
      if (hasAnyRole('engineer', 'qa_manager')) {
        btns.push({
          label: 'Hold Work Order',
          action: 'hold',
          confirmTitle: 'Hold Work Order',
          confirmMessage: `Place ${wo.wo_number} on hold? This will suspend active operations.`,
          destructive: true,
        });
      }
      if (hasRole('engineer')) {
        btns.push({
          label: 'Complete Work Order',
          action: 'complete',
          confirmTitle: 'Complete Work Order',
          confirmMessage: `Mark ${wo.wo_number} as complete? All operations must be complete first.`,
        });
      }
    }

    if (status === 'hold' && hasAnyRole('engineer', 'qa_manager')) {
      btns.push({
        label: 'Resume Work Order',
        action: 'resume',
        confirmTitle: 'Resume Work Order',
        confirmMessage: `Resume ${wo.wo_number}? Operations will become executable again.`,
      });
    }

    /* complete and closed: no action buttons */

    return btns;
  }, [wo, hasRole, hasAnyRole]);

  /* Check if complete is disabled (operations not all complete) */
  const allOpsComplete = ops.every((op) => op.status === 'complete');

  /* ── Loading ── */
  if (woQuery.isLoading) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <LoadingSkeleton rows={8} />
      </div>
    );
  }

  /* ── Error ── */
  if (woQuery.isError || !wo) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <ErrorBanner
          message={
            woQuery.error instanceof Error
              ? woQuery.error.message
              : 'Failed to load work order.'
          }
        />
      </div>
    );
  }

  return (
    <div style={{ padding: 'var(--space-lg)' }}>
      {/* Error banner */}
      {errorMsg && (
        <ErrorBanner message={errorMsg} onDismiss={() => setErrorMsg(null)} />
      )}

      {/* Hold banner */}
      {wo.status === 'hold' && <HoldBanner />}

      {/* Header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          flexWrap: 'wrap',
          gap: 'var(--space-md)',
          marginBottom: 'var(--space-lg)',
        }}
      >
        <div>
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
            {wo.wo_number}
            <StatusBadge status={wo.status} />
          </h1>
        </div>

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
          {actionButtons.map((btn) => {
            const isComplete = btn.action === 'complete';
            const disabled = isComplete && !allOpsComplete;

            return (
              <div key={btn.action} style={{ position: 'relative' }}>
                <button
                  type="button"
                  onClick={() => setPendingTransition(btn)}
                  disabled={disabled}
                  title={
                    disabled
                      ? 'All operations must be complete before completing the work order.'
                      : undefined
                  }
                  style={{
                    ...actionBtnStyle,
                    backgroundColor: btn.destructive
                      ? 'var(--destructive)'
                      : 'var(--interactive)',
                    opacity: disabled ? 0.5 : 1,
                    cursor: disabled ? 'not-allowed' : 'pointer',
                  }}
                >
                  {btn.label}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* Detail fields */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--space-md) var(--space-xl)',
          padding: 'var(--space-md)',
          backgroundColor: 'var(--bg-surface)',
          borderRadius: 'var(--radius-md)',
          marginBottom: 'var(--space-lg)',
        }}
      >
        <HeaderField label="Priority" value={humanize(wo.priority)} />
        <HeaderField label="Engine Serial" value={wo.engine_serial} />
        <HeaderField label="Module" value={wo.module} />
        <HeaderField label="Build Phase" value={humanize(wo.build_phase)} />
        <HeaderField label="Released" value={formatDate(wo.released_at)} />
        <HeaderField label="Created" value={formatDate(wo.created_at)} />
      </div>

      {/* Tabs */}
      <div
        style={{
          display: 'flex',
          gap: 0,
          borderBottom: '1px solid var(--border)',
          marginBottom: 'var(--space-md)',
        }}
      >
        <button
          type="button"
          onClick={() => setActiveTab('operations')}
          style={tabStyle(activeTab === 'operations')}
        >
          Operations
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('audit')}
          style={tabStyle(activeTab === 'audit')}
        >
          Audit Trail
        </button>
      </div>

      {/* Tab content */}
      {activeTab === 'operations' && (
        <OperationsTab
          ops={ops}
          isLoading={opsQuery.isLoading}
          woId={wo.id}
          navigate={navigate}
        />
      )}
      {activeTab === 'audit' && (
        <div
          style={{
            padding: 'var(--space-lg)',
            color: 'var(--text-secondary)',
            textAlign: 'center',
          }}
        >
          Audit trail integration will be available in a future release.
        </div>
      )}

      {/* Confirmation dialog for transitions */}
      <ConfirmationDialog
        open={pendingTransition !== null}
        title={pendingTransition?.confirmTitle ?? ''}
        message={pendingTransition?.confirmMessage ?? ''}
        confirmLabel={pendingTransition?.label ?? 'Confirm'}
        destructive={pendingTransition?.destructive ?? false}
        loading={transitionMutation.isPending}
        onConfirm={() => {
          if (pendingTransition) {
            transitionMutation.mutate(pendingTransition.action);
          }
        }}
        onCancel={() => {
          setPendingTransition(null);
          setForceStopLabor(false);
        }}
      >
        {/* Hold-specific checkbox for force_stop_labor */}
        {pendingTransition?.action === 'hold' && (
          <label
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-sm)',
              fontSize: 'var(--font-size-body)',
              color: 'var(--text-primary)',
              cursor: 'pointer',
            }}
          >
            <input
              type="checkbox"
              checked={forceStopLabor}
              onChange={(e) => setForceStopLabor(e.target.checked)}
            />
            Force stop all active labor timers
          </label>
        )}
      </ConfirmationDialog>

      {/* Conflict modal for 409 errors */}
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

/* ── Operations Tab ── */

function OperationsTab({
  ops,
  isLoading,
  woId,
  navigate,
}: {
  ops: OperationOut[];
  isLoading: boolean;
  woId: string;
  navigate: (path: string) => void;
}) {
  if (isLoading) return <LoadingSkeleton rows={5} />;

  if (ops.length === 0) {
    return (
      <EmptyState
        title="No operations"
        message="This work order has no operations defined yet."
      />
    );
  }

  /* Sort by sequence number for display */
  const sorted = [...ops].sort((a, b) => a.sequence - b.sequence);

  return (
    <div style={{ overflowX: 'auto' }}>
      <table
        style={{
          width: '100%',
          borderCollapse: 'collapse',
          backgroundColor: 'var(--bg-surface)',
          borderRadius: 'var(--radius-md)',
        }}
      >
        <thead>
          <tr>
            <th style={thStyle}>Seq</th>
            <th style={thStyle}>Description</th>
            <th style={thStyle}>Status</th>
            <th style={{ ...thStyle, textAlign: 'center' }}>Safety Critical</th>
            <th style={thStyle}>Est. Hours</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((op) => (
            <tr
              key={op.id}
              onClick={() => navigate(`/work-orders/${woId}/ops/${op.id}`)}
              style={{ cursor: 'pointer' }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = 'var(--bg-elevated)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent';
              }}
              role="link"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter')
                  navigate(`/work-orders/${woId}/ops/${op.id}`);
              }}
              aria-label={`Operation ${op.sequence}: ${op.description}`}
            >
              <td style={{ ...tdStyle, fontWeight: 600, width: '60px' }}>
                {op.sequence}
              </td>
              <td style={tdStyle}>{op.description}</td>
              <td style={tdStyle}>
                <StatusBadge status={op.status} />
              </td>
              <td style={{ ...tdStyle, textAlign: 'center' }}>
                {op.safety_critical ? (
                  <span
                    style={{
                      color: 'var(--safety-critical-border)',
                      fontWeight: 700,
                    }}
                  >
                    YES
                  </span>
                ) : (
                  <span style={{ color: 'var(--text-muted)' }}>--</span>
                )}
              </td>
              <td style={tdStyle}>{op.estimated_hours}h</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
