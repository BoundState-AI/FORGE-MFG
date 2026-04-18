/**
 * Module:   CloseWorkOrderPanel
 * Purpose:  GC-4 closure-as-attestation UI for the WO detail page.
 *           Renders the QA-manager close action, server-derived
 *           blocker tooltip, and the governance-disabled banner.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why a dedicated panel:
 *   The Phase-1 close flow has more moving parts than a generic
 *   transition button — server-derived `closure_ready` /
 *   `closure_blockers` drive the disabled-with-tooltip state, an
 *   optional `closure_rationale` textarea lives inside the
 *   confirmation dialog, and the governance-disabled banner needs
 *   to render even for non-qa_manager readers so the operational
 *   "complete is the effective terminal state" reality is explicit.
 *   Wrapping all of that in WorkOrderDetail's existing transition
 *   array would muddy that file.
 *
 * Three render paths (only when `wo.status === 'complete'`):
 *
 *   A. closure_enabled === false (any reader)
 *      Renders the governance-disabled banner explaining that
 *      `complete` is the effective terminal state pending QA
 *      approval.  No close button, no rationale textarea, no
 *      action affordance.
 *
 *   B. closure_enabled === true AND user is qa_manager
 *      Renders the close action.  Two sub-states:
 *      B1. closure_ready === true: enabled "Close Work Order"
 *          button → confirmation dialog with optional rationale
 *          textarea → closeWorkOrder mutate.
 *      B2. closure_ready === false: disabled button with tooltip
 *          listing every closure_blocker[*].message so the
 *          operator knows precisely what's missing.
 *
 *   C. closure_enabled === true AND user NOT qa_manager
 *      Renders nothing — the close action belongs to QA.  Other
 *      readers can still see the WO state via the StatusBadge
 *      above the panel.
 */

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { closeWorkOrder } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { ConfirmationDialog } from './ConfirmationDialog';
import { ErrorBanner } from './ErrorBanner';
import type { ClosureBlocker, WorkOrderDetailOut } from '@/types/api';

interface CloseWorkOrderPanelProps {
  wo: WorkOrderDetailOut;
  onCloseSuccess: () => void;
}

export function CloseWorkOrderPanel({
  wo,
  onCloseSuccess,
}: CloseWorkOrderPanelProps) {
  const { hasRole, closureEnabled } = useAuthStore();
  const [pendingConfirm, setPendingConfirm] = useState(false);
  const [rationale, setRationale] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const isQaManager = hasRole('qa_manager');
  const blockers: ClosureBlocker[] = wo.closure_blockers ?? [];
  const ready = wo.closure_ready ?? false;

  const closeMutation = useMutation({
    mutationFn: () =>
      closeWorkOrder(wo.id, {
        version: wo.version,
        closure_rationale: rationale.trim() ? rationale.trim() : null,
      }),
    onSuccess: () => {
      setPendingConfirm(false);
      setRationale('');
      setErrorMsg(null);
      onCloseSuccess();
    },
    onError: (err: unknown) => {
      setPendingConfirm(false);
      const detail = (err as {
        response?: { data?: { detail?: { code?: string; message?: string } } };
      })?.response?.data?.detail;
      if (detail && typeof detail === 'object' && detail.message) {
        setErrorMsg(detail.message);
      } else {
        setErrorMsg(
          err instanceof Error ? err.message : 'Close failed.',
        );
      }
    },
  });

  /* The close path is gated on `complete`.  In any other WO state
     the panel is silent — other transition affordances live on the
     header action-button row in WorkOrderDetail. */
  if (wo.status !== 'complete') return null;

  /* Path A: governance-disabled banner.  Visible to ALL readers (not
     just QA) so the operational reality — `complete` is terminal —
     is explicit even for engineers / operators looking at the page.
     Spec §5.3: "downstream workflows must not depend on `closed`
     state until the flag is enabled." */
  if (!closureEnabled) {
    return (
      <div
        role="status"
        data-testid="closure-disabled-banner"
        style={{
          padding: 'var(--space-md)',
          backgroundColor: 'var(--bg-elevated)',
          borderLeft: '3px solid var(--text-muted)',
          borderRadius: '0 var(--radius-md) var(--radius-md) 0',
          marginBottom: 'var(--space-lg)',
          color: 'var(--text-secondary)',
          fontSize: 'var(--font-size-body)',
        }}
      >
        <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
          Closure not currently enabled
        </div>
        <div style={{ marginTop: 'var(--space-xs)' }}>
          Phase-1 close endpoints require MSFC QA approval (or a formal QA
          waiver) before enablement.  Until then, <strong>complete</strong>{' '}
          is the effective terminal state for this work order — downstream
          workflows must not assume <code>closed</code> is reachable.
        </div>
      </div>
    );
  }

  /* Path C: closure enabled but reader isn't QA.  No affordance to
     render; the WorkOrderDetail header already shows the state. */
  if (!isQaManager) return null;

  /* Path B: QA close affordance. */
  const buttonDisabled = !ready || closeMutation.isPending;
  const tooltip = ready
    ? undefined
    : blockers.length === 0
      ? 'Close preconditions are not yet satisfied.'
      : blockers.map((b) => `• ${b.message}`).join('\n');

  return (
    <div
      data-testid="close-wo-panel"
      style={{
        padding: 'var(--space-md)',
        backgroundColor: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        marginBottom: 'var(--space-lg)',
      }}
    >
      <div
        style={{
          fontWeight: 600,
          marginBottom: 'var(--space-sm)',
          color: 'var(--text-primary)',
        }}
      >
        Close Work Order
      </div>

      {errorMsg && (
        <div style={{ marginBottom: 'var(--space-sm)' }}>
          <ErrorBanner
            message={errorMsg}
            onDismiss={() => setErrorMsg(null)}
          />
        </div>
      )}

      {!ready && blockers.length > 0 && (
        <div
          data-testid="close-wo-blockers"
          style={{
            marginBottom: 'var(--space-sm)',
            padding: 'var(--space-sm) var(--space-md)',
            backgroundColor: 'var(--bg-elevated)',
            borderLeft: '3px solid var(--status-warning, var(--text-muted))',
            borderRadius: '0 var(--radius-md) var(--radius-md) 0',
            fontSize: 'var(--font-size-body)',
            color: 'var(--text-secondary)',
          }}
        >
          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
            Cannot close — {blockers.length} precondition
            {blockers.length === 1 ? '' : 's'} not yet met:
          </div>
          <ul
            style={{
              margin: 'var(--space-xs) 0 0 var(--space-md)',
              padding: 0,
            }}
          >
            {blockers.map((b, i) => (
              <li key={`${b.code}-${i}`} style={{ marginTop: 'var(--space-xs)' }}>
                {b.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <button
        type="button"
        onClick={() => setPendingConfirm(true)}
        disabled={buttonDisabled}
        title={tooltip}
        data-testid="close-wo-button"
        style={{
          padding: 'var(--space-sm) var(--space-md)',
          border: 'none',
          borderRadius: 'var(--radius-md)',
          backgroundColor: 'var(--interactive)',
          color: '#ffffff',
          fontSize: 'var(--font-size-body)',
          fontWeight: 600,
          cursor: buttonDisabled ? 'not-allowed' : 'pointer',
          opacity: buttonDisabled ? 0.5 : 1,
        }}
      >
        Close Work Order
      </button>

      <ConfirmationDialog
        open={pendingConfirm}
        title="Close Work Order"
        message={
          `Close ${wo.wo_number}?  This is the Phase-1 attestation ` +
          `that the work order is fully complete and may not be ` +
          `reopened.  Add an optional QA rationale below.`
        }
        confirmLabel={closeMutation.isPending ? 'Closing…' : 'Confirm Close'}
        loading={closeMutation.isPending}
        onConfirm={() => closeMutation.mutate()}
        onCancel={() => {
          setPendingConfirm(false);
          setRationale('');
        }}
      >
        <div style={{ marginTop: 'var(--space-md)' }}>
          <label
            htmlFor="close-wo-rationale"
            style={{
              display: 'block',
              fontSize: 'var(--font-size-label)',
              fontWeight: 600,
              color: 'var(--text-secondary)',
              marginBottom: 'var(--space-xs)',
            }}
          >
            Closure rationale (optional)
          </label>
          <textarea
            id="close-wo-rationale"
            value={rationale}
            onChange={(e) => setRationale(e.target.value)}
            rows={3}
            maxLength={4000}
            data-testid="close-wo-rationale"
            placeholder="QA note explaining why this work order is being closed now."
            style={{
              width: '100%',
              padding: 'var(--space-sm)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              fontSize: 'var(--font-size-body)',
              resize: 'vertical',
              boxSizing: 'border-box',
            }}
          />
        </div>
      </ConfirmationDialog>
    </div>
  );
}
