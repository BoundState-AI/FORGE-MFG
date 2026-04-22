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
  createOperation,
  fetchWorkOrder,
  releaseWorkOrder,
  recallWorkOrder,
  holdWorkOrder,
  resumeWorkOrder,
  completeWorkOrder,
} from '@/api/endpoints';
import { extract422Message } from '@/api/error-detail';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ErrorBanner } from '@/components/ErrorBanner';
import { ConfirmationDialog } from '@/components/ConfirmationDialog';
import { ConflictModal } from '@/components/ConflictModal';
import { CloseWorkOrderPanel } from '@/components/CloseWorkOrderPanel';
import { AuditTrail } from '@/components/AuditTrail';
import type {
  OperationCreate,
  WorkOrderDetailOut,
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

const formFieldStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 'var(--space-xs)',
};

const inputStyle: React.CSSProperties = {
  padding: 'var(--space-sm)',
  borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border)',
  backgroundColor: 'var(--bg-surface)',
  color: 'var(--text-primary)',
  fontSize: 'var(--font-size-body)',
};

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
  const [opDescription, setOpDescription] = useState('');
  const [opSequence, setOpSequence] = useState('');
  const [opEstimatedHours, setOpEstimatedHours] = useState('0');
  const [opInspectionHold, setOpInspectionHold] = useState(false);
  const [opSafetyCritical, setOpSafetyCritical] = useState(false);
  const [opErrorMsg, setOpErrorMsg] = useState<string | null>(null);

  /* ── Queries ── */

  const woQuery = useQuery<WorkOrderDetailOut>({
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

      const detail = extract422Message(err);
      if (detail) {
        setErrorMsg(detail);
        return;
      }

      setErrorMsg(
        err instanceof Error ? err.message : 'Transition failed.',
      );
    },
  });

  const createOperationMutation = useMutation({
    mutationFn: async (body: OperationCreate) => {
      if (!wo) throw new Error('Work order not loaded');
      return createOperation(wo.id, body);
    },
    onSuccess: (_op, body) => {
      setOpErrorMsg(null);
      setOpDescription('');
      setOpSequence(String(body.sequence + 1));
      setOpEstimatedHours('0');
      setOpInspectionHold(false);
      setOpSafetyCritical(false);
      invalidateAll();
    },
    onError: (err: unknown) => {
      const detail = extract422Message(err);
      setOpErrorMsg(
        detail ?? (err instanceof Error ? err.message : 'Failed to add operation.'),
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

  /* Operations are embedded on the WO detail response now that the
     5C parity patch extended it to WorkOrderDetailOut (Build Spec §5.1).
     Sort ascending by sequence for the Operations tab display. */
  const ops = useMemo(
    () => [...(wo?.operations ?? [])].sort((a, b) => a.sequence - b.sequence),
    [wo?.operations],
  );
  const nextSequence = ops.length === 0 ? 1 : Math.max(...ops.map((op) => op.sequence)) + 1;
  const allOpsComplete =
    ops.length > 0 && ops.every((op) => op.status === 'complete');

  function handleCreateOperation(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();

    const description = opDescription.trim();
    if (!description) {
      setOpErrorMsg('Operation description is required.');
      return;
    }

    const sequence =
      opSequence.trim().length > 0 ? Number.parseInt(opSequence, 10) : nextSequence;
    if (!Number.isInteger(sequence) || sequence < 1) {
      setOpErrorMsg('Sequence must be a whole number greater than zero.');
      return;
    }

    const estimatedHours =
      opEstimatedHours.trim().length > 0 ? Number.parseFloat(opEstimatedHours) : 0;
    if (!Number.isFinite(estimatedHours) || estimatedHours < 0) {
      setOpErrorMsg('Estimated hours must be zero or greater.');
      return;
    }

    setOpErrorMsg(null);
    createOperationMutation.mutate({
      description,
      sequence,
      tooling_required: [],
      inspection_hold: opInspectionHold,
      safety_critical: opSafetyCritical,
      sign_off_roles_required: [],
      estimated_hours: estimatedHours,
      constraint_type: 'none',
      constraint_date: null,
    });
  }

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
            /* Client-side hint that Complete is premature until every
               operation is complete.  The server remains authoritative —
               OP-COMP-* codes still fire on submit — but surfacing the
               tooltip early avoids wasted round trips. */
            const disabled = btn.action === 'complete' && !allOpsComplete;

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

      {/* GC-4 close-as-attestation panel.  Renders only on `complete`
          status: governance-disabled banner when the feature flag is
          off (visible to all readers so the operational reality is
          explicit), QA-manager close action when on. */}
      <CloseWorkOrderPanel wo={wo} onCloseSuccess={invalidateAll} />

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

      {/* GC-4: surface the QA closer's rationale on the closed WO so
          reviewers don't have to dig into the audit trail (spec §5.3
          attestation: "optional closure_rationale persisted on the
          closed entity").  Renders whenever the field is populated —
          typically after status === 'closed', but we gate on the
          value itself so the rationale survives follow-on reads. */}
      {wo.closure_rationale && (
        <div
          data-testid="wo-closure-rationale"
          style={{
            padding: 'var(--space-md)',
            backgroundColor: 'var(--bg-surface)',
            borderLeft: '3px solid var(--status-complete)',
            borderRadius: '0 var(--radius-md) var(--radius-md) 0',
            marginBottom: 'var(--space-lg)',
          }}
        >
          <div
            style={{
              fontSize: 'var(--font-size-label)',
              fontWeight: 600,
              color: 'var(--text-secondary)',
              marginBottom: 'var(--space-xs)',
            }}
          >
            Closure Rationale
          </div>
          <div style={{ color: 'var(--text-primary)' }}>
            {wo.closure_rationale}
          </div>
        </div>
      )}

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

      {/* Tab content.  5C parity patch: operations are embedded on the
         WO detail response, so this renders a real table now (UX spec
         §5.3 Operations tab).  Rows click through to the operation
         page at /work-orders/:id/ops/:opId. */}
      {activeTab === 'operations' && (
        <div style={{ padding: 'var(--space-md) 0' }}>
          {wo.status === 'draft' && hasRole('engineer') && (
            <div
              style={{
                marginBottom: 'var(--space-lg)',
                padding: 'var(--space-md)',
                backgroundColor: 'var(--bg-surface)',
                borderRadius: 'var(--radius-md)',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'baseline',
                  gap: 'var(--space-sm)',
                  flexWrap: 'wrap',
                  marginBottom: 'var(--space-md)',
                }}
              >
                <div>
                  <h2
                    style={{
                      margin: 0,
                      marginBottom: 'var(--space-xs)',
                      fontSize: 'var(--font-size-section)',
                    }}
                  >
                    Add Operation
                  </h2>
                  <p style={{ margin: 0, color: 'var(--text-secondary)' }}>
                    Define the routing steps here before releasing the work order.
                  </p>
                </div>
                <div style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-label)' }}>
                  Suggested next sequence: {nextSequence}
                </div>
              </div>

              {opErrorMsg && (
                <div style={{ marginBottom: 'var(--space-md)' }}>
                  <ErrorBanner message={opErrorMsg} onDismiss={() => setOpErrorMsg(null)} />
                </div>
              )}

              <form
                onSubmit={handleCreateOperation}
                style={{
                  display: 'grid',
                  gap: 'var(--space-md)',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                }}
              >
                <label style={{ ...formFieldStyle, gridColumn: '1 / -1' }}>
                  <span>Description</span>
                  <input
                    value={opDescription}
                    onChange={(e) => setOpDescription(e.target.value)}
                    style={inputStyle}
                    maxLength={1000}
                    required
                  />
                </label>

                <label style={formFieldStyle}>
                  <span>Sequence</span>
                  <input
                    value={opSequence}
                    onChange={(e) => setOpSequence(e.target.value)}
                    style={inputStyle}
                    inputMode="numeric"
                    placeholder={String(nextSequence)}
                  />
                </label>

                <label style={formFieldStyle}>
                  <span>Estimated Hours</span>
                  <input
                    value={opEstimatedHours}
                    onChange={(e) => setOpEstimatedHours(e.target.value)}
                    style={inputStyle}
                    inputMode="decimal"
                  />
                </label>

                <label
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 'var(--space-sm)',
                    color: 'var(--text-primary)',
                    marginTop: 'var(--space-lg)',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={opInspectionHold}
                    onChange={(e) => setOpInspectionHold(e.target.checked)}
                  />
                  Inspection hold required
                </label>

                <label
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 'var(--space-sm)',
                    color: 'var(--text-primary)',
                    marginTop: 'var(--space-lg)',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={opSafetyCritical}
                    onChange={(e) => setOpSafetyCritical(e.target.checked)}
                  />
                  Safety critical
                </label>

                <div style={{ gridColumn: '1 / -1', display: 'flex', justifyContent: 'flex-start' }}>
                  <button
                    type="submit"
                    disabled={createOperationMutation.isPending}
                    style={{
                      ...actionBtnStyle,
                      opacity: createOperationMutation.isPending ? 0.7 : 1,
                      cursor: createOperationMutation.isPending ? 'wait' : 'pointer',
                    }}
                  >
                    {createOperationMutation.isPending ? 'Adding Operation...' : 'Add Operation'}
                  </button>
                </div>
              </form>
            </div>
          )}

          {ops.length === 0 ? (
            <div
              style={{
                padding: 'var(--space-lg)',
                color: 'var(--text-secondary)',
                textAlign: 'center',
              }}
            >
              This work order has no operations defined yet.
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{ width: 64 }}>Seq</th>
                  <th>Description</th>
                  <th>Status</th>
                  <th style={{ textAlign: 'center' }}>Safety Critical</th>
                  <th>Est. Hours</th>
                </tr>
              </thead>
              <tbody>
                {ops.map((op) => (
                  <tr
                    key={op.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() =>
                      navigate(`/work-orders/${wo.id}/ops/${op.id}`)
                    }
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        navigate(`/work-orders/${wo.id}/ops/${op.id}`);
                      }
                    }}
                    aria-label={`Operation ${op.sequence}: ${op.description}`}
                  >
                    <td style={{ fontWeight: 600 }}>{op.sequence}</td>
                    <td>{op.description}</td>
                    <td>
                      <StatusBadge status={op.status} />
                    </td>
                    <td style={{ textAlign: 'center' }}>
                      {op.safety_critical ? (
                        <span
                          style={{
                            color: 'var(--safety-critical-border)',
                            fontWeight: 700,
                          }}
                          title="Safety-critical operation"
                        >
                          ⚠ SC
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-muted)' }}>—</span>
                      )}
                    </td>
                    <td>{op.estimated_hours}h</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
      {activeTab === 'audit' && (
        <AuditTrail entityType="work_order" entityId={wo.id} />
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
