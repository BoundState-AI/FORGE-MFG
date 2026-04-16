/**
 * Module:   SignOffPanel
 * Purpose:  In-page sign-off control for the Operation Execution screen.
 *           Owns the full re-auth recovery dance for protected sign-off
 *           submissions (UX spec §9 / §11.3).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why a dedicated panel: the sign-off flow has more moving parts than
 * a plain button — role selection, the same-device second-signer
 * banner, the buffered Idempotency-Key retry path, and the explicit
 * post-reauth re-confirmation step (no auto-replay).  Encapsulating
 * the whole dance here keeps OperationExecution focused on labor +
 * operation-state transitions.
 *
 * State machine, in plain English:
 *
 *   idle  ── user picks role + clicks Sign Off ──▶  submitting
 *   submitting ── 201 ──▶  idle (success toast)
 *   submitting ── 401 REAUTH_REQUIRED ──▶  needs_reauth (modal up)
 *   needs_reauth ── confirm ──▶  buffer + redirect to Cognito
 *   needs_reauth ── cancel ──▶  idle (intent dropped)
 *   (return from Cognito callback with ?reauth=resume) ──▶  reconfirm
 *   reconfirm ── confirm-and-submit ──▶  submitting (same key)
 *   reconfirm ── cancel ──▶  idle
 *
 * Spec invariants pinned here:
 *   - same Idempotency-Key reused on retry (§9.3 / §11.3 line 1538)
 *   - explicit user re-confirmation, no auto-replay (§11.3)
 *   - second-signer flow forces fresh principal via prompt=login
 *     (§9.2 line 1481)
 */

import { useEffect, useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { createSignOff } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import {
  beginReauth,
  extractReauthError,
  peekReauthBuffer,
  setReauthBuffer,
  takeReauthBuffer,
  type ReauthBuffer,
} from '@/auth/reauth';
import { ReauthModal } from './ReauthModal';
import { ConfirmationDialog } from './ConfirmationDialog';
import { ErrorBanner } from './ErrorBanner';
import type { SignOffAction, SignOffCreate } from '@/types/api';

interface SignOffPanelProps {
  /** Operation being signed off — used in the action summary copy. */
  operationId: string;
  operationSequence: number;
  operationDescription: string;
  /** True when the operation requires the same-device dual-signoff dance. */
  isSafetyCritical: boolean;
  /**
   * Roles the operation expects to see signed off.  When non-empty AND
   * the first sign-off lands but more required roles remain unsigned,
   * the second-signer banner surfaces.  In Phase 1 we display the
   * banner generically — the spec §9.2 line 1480 copy does not
   * itemize the missing role.
   */
  signOffRolesRequired: string[];
  /** Called after a successful sign-off so the parent can refetch state. */
  onSignOffSuccess: () => void;
}

export function SignOffPanel({
  operationId,
  operationSequence,
  operationDescription,
  isSafetyCritical,
  signOffRolesRequired,
  onSignOffSuccess,
}: SignOffPanelProps) {
  const { roles: userRoles, hasRole } = useAuthStore();
  const [searchParams, setSearchParams] = useSearchParams();

  /* The role this user is signing as.  Defaulted to the first role
     they hold that's also in the operation's required list. */
  const eligibleRoles = useMemo(
    () =>
      signOffRolesRequired.filter((r) => userRoles.includes(r as never)),
    [signOffRolesRequired, userRoles],
  );
  const [selectedRole, setSelectedRole] = useState<string>(
    eligibleRoles[0] ?? userRoles[0] ?? 'inspector',
  );
  const [pendingConfirm, setPendingConfirm] = useState(false);
  const [reauthOpen, setReauthOpen] = useState(false);
  const [reauthBuffer, setReauthBufferState] = useState<ReauthBuffer | null>(
    null,
  );
  const [resumeOpen, setResumeOpen] = useState(false);
  const [resumeBuffer, setResumeBuffer] = useState<ReauthBuffer | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [signOffComplete, setSignOffComplete] = useState(false);

  /** True when the operator can sign off (has at least one eligible role). */
  const canSignOff =
    eligibleRoles.length > 0 || hasRole('inspector') || hasRole('engineer');

  /**
   * The Idempotency-Key for the in-flight or pending sign-off.  Stable
   * across retries — only changes when a new sign-off is initiated.
   */
  const [activeIdempotencyKey, setActiveIdempotencyKey] = useState<
    string | null
  >(null);

  /* On mount: if the URL carries ?reauth=resume and a buffer exists
     for THIS operation, surface the post-reauth re-confirmation modal
     so the user can explicitly confirm the replay (no auto-replay). */
  useEffect(() => {
    if (searchParams.get('reauth') !== 'resume') return;
    const buffer = peekReauthBuffer();
    if (buffer && buffer.action === 'sign_off') {
      setResumeBuffer(buffer);
      setResumeOpen(true);
    }
    /* Strip the query flag so a refresh doesn't re-prompt. */
    const next = new URLSearchParams(searchParams);
    next.delete('reauth');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const signOffMutation = useMutation({
    mutationFn: async (vars: {
      idempotencyKey: string;
      body: SignOffCreate;
    }) => createSignOff(operationId, vars.body, vars.idempotencyKey),
    onSuccess: () => {
      setErrorMsg(null);
      setSignOffComplete(true);
      setActiveIdempotencyKey(null);
      onSignOffSuccess();
    },
    onError: (err: unknown) => {
      const reauth = extractReauthError(err);
      if (reauth) {
        /* Buffer the exact intent — same idempotency key is what makes
           the retry safe (§11.3).  The redirect target embeds the
           operation URL so AuthCallback returns the user here. */
        const buffer: ReauthBuffer = {
          createdAt: new Date().toISOString(),
          idempotencyKey: activeIdempotencyKey ?? crypto.randomUUID(),
          action: 'sign_off',
          actionSummary: `Sign off Op ${operationSequence} (${operationDescription}) as ${selectedRole}`,
          returnTo: window.location.pathname,
          forceFreshPrincipal: false,
        };
        setReauthBuffer(buffer);
        setReauthBufferState(buffer);
        setReauthOpen(true);
        return;
      }
      const msg =
        (err as { response?: { data?: { detail?: unknown } } })?.response?.data
          ?.detail;
      setErrorMsg(typeof msg === 'string' ? msg : 'Sign-off failed');
    },
  });

  /** First-time submit: generate the key, open the confirmation dialog. */
  function handleSubmitClick(): void {
    setActiveIdempotencyKey(crypto.randomUUID());
    setPendingConfirm(true);
  }

  /** User confirmed in the standard ConfirmationDialog → fire mutation. */
  function handleConfirmSubmit(): void {
    setPendingConfirm(false);
    if (!activeIdempotencyKey) return;
    signOffMutation.mutate({
      idempotencyKey: activeIdempotencyKey,
      body: { role: selectedRole, action: 'approve' satisfies SignOffAction },
    });
  }

  /** User confirmed in the ReauthModal → buffer is set; redirect now. */
  function handleConfirmReauth(): void {
    setReauthOpen(false);
    beginReauth();
  }

  /** User cancelled the reauth modal → discard the intent cleanly. */
  function handleCancelReauth(): void {
    setReauthOpen(false);
    setReauthBufferState(null);
    setActiveIdempotencyKey(null);
    takeReauthBuffer();
  }

  /** Post-callback re-confirmation: user explicitly resubmits. */
  function handleResumeConfirm(): void {
    if (!resumeBuffer) return;
    setResumeOpen(false);
    /* Use the buffered key — same key = idempotent retry. */
    setActiveIdempotencyKey(resumeBuffer.idempotencyKey);
    /* Pull the buffer (and clear it) so a refresh after this point
       doesn't re-prompt. */
    takeReauthBuffer();
    signOffMutation.mutate({
      idempotencyKey: resumeBuffer.idempotencyKey,
      body: { role: selectedRole, action: 'approve' satisfies SignOffAction },
    });
  }

  function handleResumeCancel(): void {
    setResumeOpen(false);
    setResumeBuffer(null);
    setActiveIdempotencyKey(null);
    takeReauthBuffer();
  }

  /** Force-fresh-principal path for the second signer (spec §9.2). */
  function handleSecondSignerStart(): void {
    /* The second signer's Idempotency-Key is theirs alone — it's a
       different (user_id, role) tuple on the server side. */
    const key = crypto.randomUUID();
    setActiveIdempotencyKey(key);
    setReauthBuffer({
      createdAt: new Date().toISOString(),
      idempotencyKey: key,
      action: 'sign_off',
      actionSummary: `Second sign-off on Op ${operationSequence} (${operationDescription})`,
      returnTo: window.location.pathname,
      forceFreshPrincipal: true,
    });
    setReauthBufferState({
      createdAt: new Date().toISOString(),
      idempotencyKey: key,
      action: 'sign_off',
      actionSummary: `Second sign-off on Op ${operationSequence} (${operationDescription})`,
      returnTo: window.location.pathname,
      forceFreshPrincipal: true,
    });
    setReauthOpen(true);
  }

  if (!canSignOff) return null;

  return (
    <div
      className="card"
      style={{ marginBottom: 'var(--space-md)' }}
      data-testid="signoff-panel"
    >
      <div
        style={{
          fontWeight: 600,
          marginBottom: 'var(--space-sm)',
          color: 'var(--text-primary)',
        }}
      >
        Sign Off
      </div>

      {errorMsg && (
        <ErrorBanner message={errorMsg} onDismiss={() => setErrorMsg(null)} />
      )}

      {signOffComplete && signOffRolesRequired.length > 1 && (
        /* §9.2 line 1480 verbatim banner — fires only after a
           successful first sign-off, when more required roles remain. */
        <div
          role="status"
          aria-live="polite"
          style={{
            padding: 'var(--space-sm) var(--space-md)',
            background: 'var(--bg-elevated)',
            borderLeft: '3px solid var(--status-active)',
            borderRadius: '0 var(--radius-md) var(--radius-md) 0',
            marginBottom: 'var(--space-sm)',
            color: 'var(--text-primary)',
          }}
          data-testid="second-signer-banner"
        >
          <strong>First sign-off recorded.</strong>{' '}
          Second sign-off required from a different user.
          <div style={{ marginTop: 'var(--space-sm)' }}>
            <button
              type="button"
              className="btn-primary"
              onClick={handleSecondSignerStart}
            >
              Sign Off as Second Signer
            </button>
          </div>
        </div>
      )}

      {!signOffComplete && (
        <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'flex-end' }}>
          <div>
            <label htmlFor="signoff-role">Role</label>
            <select
              id="signoff-role"
              value={selectedRole}
              onChange={(e) => setSelectedRole(e.target.value)}
            >
              {(eligibleRoles.length > 0 ? eligibleRoles : userRoles).map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className="btn-primary"
            onClick={handleSubmitClick}
            disabled={signOffMutation.isPending}
          >
            {signOffMutation.isPending ? 'Submitting…' : 'Sign Off'}
          </button>
        </div>
      )}

      {isSafetyCritical && !signOffComplete && (
        <div
          style={{
            marginTop: 'var(--space-sm)',
            color: 'var(--text-secondary)',
            fontSize: 'var(--font-size-caption)',
          }}
        >
          Safety-critical: re-authentication is required if your sign-in
          is older than 5 minutes.
        </div>
      )}

      {/* First-attempt confirmation — generic state-transition dialog. */}
      <ConfirmationDialog
        open={pendingConfirm}
        title="Confirm Sign-Off"
        message={`Sign off Op ${operationSequence} as ${selectedRole}? This action cannot be undone.`}
        confirmLabel="Sign Off"
        onConfirm={handleConfirmSubmit}
        onCancel={() => setPendingConfirm(false)}
        loading={signOffMutation.isPending}
      />

      {/* Stale-auth recovery modal. */}
      <ReauthModal
        open={reauthOpen}
        action="sign_off"
        actionSummary={
          reauthBuffer?.actionSummary ??
          `Sign off Op ${operationSequence}`
        }
        authAgeSeconds={null}
        forceFreshPrincipal={reauthBuffer?.forceFreshPrincipal ?? false}
        onConfirmReauth={handleConfirmReauth}
        onCancel={handleCancelReauth}
      />

      {/* Post-callback explicit re-confirmation (no auto-replay). */}
      <ConfirmationDialog
        open={resumeOpen}
        title="Confirm and Submit"
        message={
          resumeBuffer
            ? `Authentication refreshed. Submit ${resumeBuffer.actionSummary}?`
            : ''
        }
        confirmLabel="Confirm and Submit"
        onConfirm={handleResumeConfirm}
        onCancel={handleResumeCancel}
        loading={signOffMutation.isPending}
      />
    </div>
  );
}
