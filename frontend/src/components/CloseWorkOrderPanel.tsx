/**
 * Module:   CloseWorkOrderPanel
 * Purpose:  GC-4 closure-as-attestation UI for the WO detail page.
 *           Renders the QA-manager close action, server-derived
 *           blocker tooltip, the governance-disabled banner, AND
 *           the full §11.3 re-auth recovery dance on 401
 *           REAUTH_REQUIRED with same-key idempotency retry.
 * Rev:      0.2.0
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
 *
 * Render paths (only when `wo.status === 'complete'`):
 *
 *   A. closure_enabled === false (any reader)
 *      Governance-disabled banner.  No close button.
 *
 *   B1. closure_enabled === true AND qa_manager AND closure_ready
 *       "Close Work Order" button → confirmation dialog with
 *       optional rationale textarea → closeWorkOrder mutate.
 *
 *   B2. closure_enabled === true AND qa_manager AND !closure_ready
 *       Disabled button with tooltip + visible blocker list.
 *
 *   C. closure_enabled === true AND !qa_manager
 *       Nothing — the close action belongs to QA.
 *
 * GC-4 round-2 additions (Codex review):
 *   - §11.3 REAUTH_REQUIRED path: same machinery as NCRDetail
 *     disposition and SignOffPanel — buffer the full WorkOrderClose
 *     payload (including rationale), redirect via PKCE, post-
 *     callback Confirm-and-Submit replays verbatim with the same
 *     Idempotency-Key.
 *   - Idempotency key generated at first-submit time and reused on
 *     every retry for this attempt, so the server-side dedupe sees
 *     the same key through fresh-auth recovery.
 */

import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { closeWorkOrder } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import {
  beginReauth,
  extractReauthError,
  peekReauthBuffer,
  setReauthBuffer,
  takeReauthBuffer,
  type WorkOrderCloseReauthBuffer,
} from '@/auth/reauth';
import { ConfirmationDialog } from './ConfirmationDialog';
import { ErrorBanner } from './ErrorBanner';
import { ReauthModal } from './ReauthModal';
import type {
  ClosureBlocker,
  WorkOrderClose,
  WorkOrderDetailOut,
} from '@/types/api';

interface CloseWorkOrderPanelProps {
  wo: WorkOrderDetailOut;
  onCloseSuccess: () => void;
}

export function CloseWorkOrderPanel({
  wo,
  onCloseSuccess,
}: CloseWorkOrderPanelProps) {
  const { hasRole, closureEnabled } = useAuthStore();
  const [searchParams, setSearchParams] = useSearchParams();

  const [pendingConfirm, setPendingConfirm] = useState(false);
  const [rationale, setRationale] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  /**
   * Idempotency-Key for the in-flight close attempt.  Generated on
   * first-submit click; reused on every retry including the post-
   * reauth replay so the server-side dedupe sees a single key
   * across fresh-auth recovery (spec §9.3 / §11.3 line 1538).
   */
  const [activeIdempotencyKey, setActiveIdempotencyKey] = useState<
    string | null
  >(null);
  const [reauthOpen, setReauthOpen] = useState(false);
  const [reauthBufferState, setReauthBufferState] =
    useState<WorkOrderCloseReauthBuffer | null>(null);
  const [resumeOpen, setResumeOpen] = useState(false);
  const [resumeBuffer, setResumeBuffer] =
    useState<WorkOrderCloseReauthBuffer | null>(null);

  const isQaManager = hasRole('qa_manager');
  const blockers: ClosureBlocker[] = wo.closure_blockers ?? [];
  const ready = wo.closure_ready ?? false;

  const closeMutation = useMutation({
    mutationFn: async (vars: {
      body: WorkOrderClose;
      idempotencyKey: string;
    }) =>
      closeWorkOrder(wo.id, vars.body, vars.idempotencyKey),
    onSuccess: () => {
      setPendingConfirm(false);
      setRationale('');
      setErrorMsg(null);
      setActiveIdempotencyKey(null);
      onCloseSuccess();
    },
    onError: (
      err: unknown,
      vars: { body: WorkOrderClose; idempotencyKey: string },
    ) => {
      setPendingConfirm(false);
      /* GC-4 round-2: 401 REAUTH_REQUIRED lands in the re-auth
         recovery flow, not a generic banner.  Buffer the FULL
         WorkOrderClose body so the post-redirect resume replays
         the exact rationale the user typed, not the current
         dialog state (GC-3 round-3 payload-preservation rule). */
      const reauth = extractReauthError(err);
      if (reauth) {
        const buffer: WorkOrderCloseReauthBuffer = {
          createdAt: new Date().toISOString(),
          idempotencyKey: vars.idempotencyKey,
          action: 'wo_close',
          payload: vars.body,
          woNumber: wo.wo_number,
          actionSummary: `Close work order ${wo.wo_number}`,
          returnTo: window.location.pathname,
          forceFreshPrincipal: false,
        };
        setReauthBuffer(buffer);
        setReauthBufferState(buffer);
        setReauthOpen(true);
        return;
      }
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

  /* On mount / remount with ?reauth=resume: surface the confirm-and-
     submit dialog for the buffered WO close.  Spec §11.3: no
     auto-replay; user must explicitly re-confirm. */
  useEffect(() => {
    if (searchParams.get('reauth') !== 'resume') return;
    const buffer = peekReauthBuffer();
    if (buffer && buffer.action === 'wo_close') {
      setResumeBuffer(buffer);
      setResumeOpen(true);
    }
    const next = new URLSearchParams(searchParams);
    next.delete('reauth');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  /* The close path is gated on `complete`.  In any other WO state
     the panel is silent — other transition affordances live on the
     header action-button row in WorkOrderDetail. */
  if (wo.status !== 'complete') return null;

  /* Whether to render the panel at all. */
  const renderPanel = !closureEnabled || isQaManager;
  if (!renderPanel) return null;

  /* First-click submit: generate the key, open the confirm dialog. */
  function handleCloseClick(): void {
    setActiveIdempotencyKey(crypto.randomUUID());
    setPendingConfirm(true);
  }

  function handleConfirmSubmit(): void {
    if (!activeIdempotencyKey) return;
    setPendingConfirm(false);
    closeMutation.mutate({
      body: {
        version: wo.version,
        closure_rationale: rationale.trim() ? rationale.trim() : null,
      },
      idempotencyKey: activeIdempotencyKey,
    });
  }

  function handleConfirmReauth(): void {
    setReauthOpen(false);
    /* beginReauth is async (PKCE challenge needs WebCrypto SHA-256)
       but we intentionally do not await in event handlers — the
       final line tears the page down with window.location.assign. */
    void beginReauth();
  }

  function handleCancelReauth(): void {
    setReauthOpen(false);
    setReauthBufferState(null);
    setActiveIdempotencyKey(null);
    takeReauthBuffer();
  }

  function handleResumeConfirm(): void {
    if (!resumeBuffer) return;
    setResumeOpen(false);
    takeReauthBuffer();
    setActiveIdempotencyKey(resumeBuffer.idempotencyKey);
    /* Replay the EXACT buffered payload — not current dialog state. */
    closeMutation.mutate({
      body: resumeBuffer.payload,
      idempotencyKey: resumeBuffer.idempotencyKey,
    });
  }

  function handleResumeCancel(): void {
    setResumeOpen(false);
    setResumeBuffer(null);
    setActiveIdempotencyKey(null);
    takeReauthBuffer();
  }

  /* Path A: governance-disabled banner. */
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
        onClick={handleCloseClick}
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

      {/* First-attempt confirmation with rationale textarea. */}
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
        onConfirm={handleConfirmSubmit}
        onCancel={() => {
          setPendingConfirm(false);
          setRationale('');
          setActiveIdempotencyKey(null);
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

      {/* Stale-auth recovery modal (§11.3). */}
      <ReauthModal
        open={reauthOpen}
        action="wo_close"
        actionSummary={
          reauthBufferState?.actionSummary ??
          `Close work order ${wo.wo_number}`
        }
        authAgeSeconds={null}
        forceFreshPrincipal={false}
        onConfirmReauth={handleConfirmReauth}
        onCancel={handleCancelReauth}
      />

      {/* Post-callback explicit re-confirmation (no auto-replay). */}
      <ConfirmationDialog
        open={resumeOpen}
        title="Confirm and Submit"
        message={
          resumeBuffer
            ? `Authentication refreshed.  Close ${resumeBuffer.woNumber}?`
            : ''
        }
        confirmLabel={closeMutation.isPending ? 'Closing…' : 'Confirm and Close'}
        loading={closeMutation.isPending}
        onConfirm={handleResumeConfirm}
        onCancel={handleResumeCancel}
      />
    </div>
  );
}
