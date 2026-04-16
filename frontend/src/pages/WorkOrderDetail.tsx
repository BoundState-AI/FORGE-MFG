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
import { useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  fetchWorkOrder,
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
import { ConfirmationDialog } from '@/components/ConfirmationDialog';
import { ConflictModal } from '@/components/ConflictModal';
import type {
  WorkOrderOut,
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

  const wo = woQuery.data;

  /* ── Mutations ── */

  const invalidateAll = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['work-order', programId, id] });
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

  /* Operations table is deferred (backend list endpoint not yet exposed).
     The WO complete precondition will be checked by the server. */

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
            /* Client-side "all ops complete" gate removed — the operations
               list endpoint is deferred.  The server still rejects Complete
               if preconditions are unmet (OP-COMP-* codes in spec §5.4.1). */
            const disabled = false;

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

      {/* Tab content.  The operations listing depends on a backend GET
         that has not been exposed yet (see endpoints.ts header notes).
         When that endpoint ships the OperationsTab can re-attach — the
         existing implementation is preserved at the bottom of this file
         for that integration work. */}
      {activeTab === 'operations' && (
        <div
          style={{
            padding: 'var(--space-lg)',
            color: 'var(--text-secondary)',
            textAlign: 'center',
          }}
        >
          The backend list endpoint (GET /api/work-orders/:id/operations)
          has not been implemented yet. Individual operations remain
          reachable via their direct URLs.
        </div>
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


/* OperationsTab removed from this slice. Restore once the backend adds
   GET /api/work-orders/{wo_id}/operations. */
