/**
 * Module:   SignOffPanel
 * Purpose:  In-page sign-off control for the Operation Execution screen.
 *           Owns the full re-auth recovery dance for protected sign-off
 *           submissions (UX spec §9 / §11.3).
 * Rev:      0.2.0
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
 *   submitting ── 201 ──▶  idle (success → parent refetches → form/banner re-derive)
 *   submitting ── 401 REAUTH_REQUIRED ──▶  needs_reauth (modal up)
 *   needs_reauth ── confirm ──▶  buffer + redirect to Cognito
 *   needs_reauth ── cancel ──▶  idle (intent dropped)
 *   (return from Cognito callback with ?reauth=resume) ──▶  reconfirm
 *     ├─ sign_off_first  → confirm-and-submit (replays buffer.payload verbatim)
 *     └─ sign_off_second → role-picker dialog (resolved from FRESH principal's
 *                          eligibility) → confirm-and-submit
 *
 * GC-3 review round 3 fixes pinned here:
 *   - Findings 1 & 3: the buffer carries the FULL request body for the
 *     first-signer / NCR / same-user path (spec §11.3 — preserve the
 *     protected action across re-auth).  The second-signer path does
 *     NOT pin a payload pre-redirect because the second signer's
 *     identity (and therefore eligible role set) is unknown until the
 *     fresh Cognito principal returns.
 *   - Finding 2 (LOW) + Round 4 (Codex Medium): banner visibility is
 *     derived from `signoffsCompletedRoles` — the server-side DISTINCT
 *     covered-role list (OperationDetailOut.signoffs_completed_roles).
 *     Form/banner gate on `required \ covered`, not on a raw count.
 *     A count would re-zero across the second-signer redirect AND
 *     would falsely satisfy the gate when two different users signed
 *     the same role (the model unique constraint allows that).
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
  type SignOffSecondSignerReauthBuffer,
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
   * Roles the operation expects to see signed off.  The banner shows
   * when at least one required role has been covered AND at least one
   * remains uncovered.  Spec §9.2 line 1480 copy is shown verbatim
   * regardless of which role(s) are still missing.
   */
  signOffRolesRequired: string[];
  /**
   * Server-side DISTINCT covered-role list for this operation in its
   * current execution_cycle (OperationDetailOut.signoffs_completed_roles).
   * Source-of-truth for the banner gate so coverage survives the
   * full-page Cognito redirect of the second-signer flow.
   *
   * GC-3 review round 4 (Codex Medium): a count would falsely satisfy
   * the gate when two different users sign the SAME role — the model
   * unique constraint allows that, and the previous count contract
   * hid both the form AND the banner before required-role coverage
   * was actually complete.  The role-list contract lets the panel
   * compute  remaining = required ∖ covered  honestly.
   */
  signoffsCompletedRoles: string[];
  /** Called after a successful sign-off so the parent can refetch state. */
  onSignOffSuccess: () => void;
}

export function SignOffPanel({
  operationId,
  operationSequence,
  operationDescription,
  isSafetyCritical,
  signOffRolesRequired,
  signoffsCompletedRoles,
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
  const [reauthBufferState, setReauthBufferState] =
    useState<ReauthBuffer | null>(null);
  const [resumeOpen, setResumeOpen] = useState(false);
  const [resumeBuffer, setResumeBuffer] = useState<ReauthBuffer | null>(null);
  const [secondSignerRole, setSecondSignerRole] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  /**
   * UI mode is derived from the server-side ROLE COVERAGE list, NOT
   * from a raw count.  This keeps the banner honest across the
   * full-page Cognito redirect of the second-signer flow AND across
   * the duplicate-role case (two different users signing the same
   * role would falsely advance a count-based gate).  See spec §9.2.
   */
  const remainingRequiredRoles = useMemo(
    () =>
      signOffRolesRequired.filter(
        (r) => !signoffsCompletedRoles.includes(r),
      ),
    [signOffRolesRequired, signoffsCompletedRoles],
  );
  const formVisible = signoffsCompletedRoles.length === 0;
  const moreSignoffsNeeded =
    signoffsCompletedRoles.length >= 1 && remainingRequiredRoles.length > 0;

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

  /* On mount: if the URL carries ?reauth=resume and a sign_off-flavored
     buffer exists, surface the post-reauth re-confirmation flow.  For
     the second-signer variant, also resolve the FRESH principal's
     eligible roles so the picker can render with sensible defaults. */
  useEffect(() => {
    if (searchParams.get('reauth') !== 'resume') return;
    const buffer = peekReauthBuffer();
    if (
      buffer &&
      (buffer.action === 'sign_off_first' ||
        buffer.action === 'sign_off_second')
    ) {
      setResumeBuffer(buffer);
      setResumeOpen(true);
      if (buffer.action === 'sign_off_second') {
        /* Compute which of the operation's required roles the new
           principal can satisfy AND that have not already been
           covered this cycle.  GC-3 round 4: the picker must NOT
           offer roles already signed by another user — that would
           leave the actual remaining role uncovered while satisfying
           the picker.  Auto-select when exactly one. */
        const eligible = buffer.opMeta.signOffRolesRequired.filter(
          (r) =>
            userRoles.includes(r as never) &&
            !signoffsCompletedRoles.includes(r),
        );
        setSecondSignerRole(eligible.length === 1 ? eligible[0] : null);
      }
    }
    /* Strip the query flag so a refresh doesn't re-prompt. */
    const next = new URLSearchParams(searchParams);
    next.delete('reauth');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, userRoles, signoffsCompletedRoles]);

  const signOffMutation = useMutation({
    mutationFn: async (vars: {
      idempotencyKey: string;
      body: SignOffCreate;
    }) => createSignOff(operationId, vars.body, vars.idempotencyKey),
    onSuccess: () => {
      setErrorMsg(null);
      setActiveIdempotencyKey(null);
      onSignOffSuccess();
    },
    onError: (
      err: unknown,
      vars: { idempotencyKey: string; body: SignOffCreate },
    ) => {
      const reauth = extractReauthError(err);
      if (reauth) {
        /* GC-3 review round 3 finding 1: buffer the FULL request body,
           not just the role label.  Spec §11.3 — preserve the protected
           action across re-auth so the post-redirect resume can submit
           exactly what the user originally chose, not the latest live
           form state.  The buffered idempotency key matches the one
           the original request used so the server-side dedupe is
           honored on retry (§9.3 line 1538). */
        const buffer: ReauthBuffer = {
          createdAt: new Date().toISOString(),
          idempotencyKey: vars.idempotencyKey,
          action: 'sign_off_first',
          payload: vars.body,
          actionSummary: `Sign off Op ${operationSequence} (${operationDescription}) as ${vars.body.role}`,
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

  /** User confirmed in the ReauthModal → buffer is set; redirect now.
      beginReauth is async (PKCE challenge needs WebCrypto SHA-256),
      but we intentionally do not await the promise in event handlers —
      the final line is ``window.location.assign(...)``, which tears
      the page down either way. */
  function handleConfirmReauth(): void {
    setReauthOpen(false);
    void beginReauth();
  }

  /** User cancelled the reauth modal → discard the intent cleanly. */
  function handleCancelReauth(): void {
    setReauthOpen(false);
    setReauthBufferState(null);
    setActiveIdempotencyKey(null);
    takeReauthBuffer();
  }

  /** Post-callback re-confirmation: user explicitly resubmits.
      Branches on the buffer variant — first-signer replays the
      payload verbatim; second-signer constructs a new payload from
      the fresh principal's resolved role. */
  function handleResumeConfirm(): void {
    if (!resumeBuffer) return;
    setResumeOpen(false);
    takeReauthBuffer();

    if (resumeBuffer.action === 'sign_off_first') {
      /* GC-3 review round 3 finding 1: replay the EXACT body the user
         originally submitted (buffer.payload), NOT the current form
         state.  A user who tweaked the role select between the
         original submit and this confirm would otherwise silently
         submit a different role than they were asked to confirm. */
      setActiveIdempotencyKey(resumeBuffer.idempotencyKey);
      signOffMutation.mutate({
        idempotencyKey: resumeBuffer.idempotencyKey,
        body: resumeBuffer.payload,
      });
      return;
    }

    if (resumeBuffer.action === 'sign_off_second') {
      /* Second-signer payload is constructed AFTER the fresh principal
         is back, because the first signer cannot truthfully pin the
         second signer's role pre-redirect.  Either we auto-selected
         (single eligible role) or the picker captured the choice. */
      if (!secondSignerRole) {
        setErrorMsg('Pick a role to sign off as before submitting.');
        return;
      }
      setActiveIdempotencyKey(resumeBuffer.idempotencyKey);
      signOffMutation.mutate({
        idempotencyKey: resumeBuffer.idempotencyKey,
        body: {
          role: secondSignerRole,
          action: 'approve' satisfies SignOffAction,
        },
      });
    }
  }

  function handleResumeCancel(): void {
    setResumeOpen(false);
    setResumeBuffer(null);
    setSecondSignerRole(null);
    setActiveIdempotencyKey(null);
    takeReauthBuffer();
  }

  /** Force-fresh-principal path for the second signer (spec §9.2).
      The buffer carries OPERATION metadata, NOT a payload — the
      second signer's identity (and therefore eligible role) is
      unknown until they authenticate fresh. */
  function handleSecondSignerStart(): void {
    /* The second signer's Idempotency-Key is theirs alone — it's a
       different (user_id, role) tuple on the server side. */
    const key = crypto.randomUUID();
    setActiveIdempotencyKey(key);
    const buffer: SignOffSecondSignerReauthBuffer = {
      createdAt: new Date().toISOString(),
      idempotencyKey: key,
      action: 'sign_off_second',
      opMeta: {
        operationId,
        operationSequence,
        operationDescription,
        signOffRolesRequired,
      },
      actionSummary: `Second sign-off on Op ${operationSequence} (${operationDescription})`,
      returnTo: window.location.pathname,
      forceFreshPrincipal: true,
    };
    setReauthBuffer(buffer);
    setReauthBufferState(buffer);
    setReauthOpen(true);
  }

  if (!canSignOff) return null;

  /* Resolve the eligible-role list for the second-signer resume
     dialog.  Computed at render-time so a re-auth that returns with
     an unexpected role set surfaces immediately.

     GC-3 round 4: also subtract roles that have already been signed
     this cycle — the picker must not let the second signer pick a
     role that wouldn't actually advance the gate. */
  const isSecondSignerResume =
    resumeBuffer?.action === 'sign_off_second';
  const secondSignerEligibleRoles = isSecondSignerResume
    ? (resumeBuffer as SignOffSecondSignerReauthBuffer).opMeta
        .signOffRolesRequired.filter(
          (r) =>
            userRoles.includes(r as never) &&
            !signoffsCompletedRoles.includes(r),
        )
    : [];
  const secondSignerSubmitDisabled =
    isSecondSignerResume &&
    (secondSignerEligibleRoles.length === 0 || secondSignerRole === null);

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

      {moreSignoffsNeeded && (
        /* §9.2 line 1480 verbatim banner — shows whenever the server
           reports at least one sign-off recorded but more required
           roles remain unsigned.  Server-side gate so the count
           survives the second-signer redirect. */
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
          {remainingRequiredRoles.length > 1 && (
            <span style={{ color: 'var(--text-secondary)' }}>
              {' '}({remainingRequiredRoles.length} roles remaining)
            </span>
          )}
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

      {formVisible && (
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

      {isSafetyCritical && formVisible && (
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
          reauthBufferState?.actionSummary ??
          `Sign off Op ${operationSequence}`
        }
        authAgeSeconds={null}
        forceFreshPrincipal={reauthBufferState?.forceFreshPrincipal ?? false}
        onConfirmReauth={handleConfirmReauth}
        onCancel={handleCancelReauth}
      />

      {/* Post-callback explicit re-confirmation (no auto-replay).
          For the second-signer variant we surface a role picker before
          the confirm action because the second signer's role cannot
          be pinned pre-redirect (their identity is unknown then). */}
      {resumeOpen && isSecondSignerResume && (
        <SecondSignerResumeDialog
          actionSummary={resumeBuffer?.actionSummary ?? ''}
          eligibleRoles={secondSignerEligibleRoles}
          selectedRole={secondSignerRole}
          onRoleChange={setSecondSignerRole}
          onConfirm={handleResumeConfirm}
          onCancel={handleResumeCancel}
          loading={signOffMutation.isPending}
          submitDisabled={secondSignerSubmitDisabled}
        />
      )}

      {!isSecondSignerResume && (
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
      )}
    </div>
  );
}

/**
 * Inline dialog for the second-signer resume flow.  Distinct from the
 * generic ConfirmationDialog because the second signer must PICK a
 * role from the (post-reauth-resolved) eligible set before submitting.
 *
 * Why inline instead of a shared component: the only consumer is this
 * panel, and the picker semantics are sign-off-specific (eligible =
 * required ∩ user roles).  Pulling it into a generic component would
 * surface API choices about role/eligibility shape without a second
 * caller to validate the abstraction.
 */
function SecondSignerResumeDialog({
  actionSummary,
  eligibleRoles,
  selectedRole,
  onRoleChange,
  onConfirm,
  onCancel,
  loading,
  submitDisabled,
}: {
  actionSummary: string;
  eligibleRoles: string[];
  selectedRole: string | null;
  onRoleChange: (role: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
  loading: boolean;
  submitDisabled: boolean;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Confirm second sign-off"
      data-testid="second-signer-resume-dialog"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
      }}
    >
      <div
        style={{
          backgroundColor: 'var(--bg-surface)',
          borderRadius: 'var(--radius-lg)',
          padding: 'var(--space-lg)',
          maxWidth: 520,
          width: '90%',
        }}
      >
        <h2
          style={{
            margin: 0,
            marginBottom: 'var(--space-sm)',
            fontSize: 'var(--font-size-section)',
            color: 'var(--text-primary)',
          }}
        >
          Confirm second sign-off
        </h2>
        <p
          style={{
            margin: 0,
            marginBottom: 'var(--space-md)',
            color: 'var(--text-secondary)',
          }}
        >
          Authentication refreshed. {actionSummary}.
        </p>

        {eligibleRoles.length === 0 ? (
          <ErrorBanner
            message="You are not eligible to sign off this operation. The required roles do not match your assignments."
          />
        ) : (
          <div style={{ marginBottom: 'var(--space-md)' }}>
            <label
              htmlFor="second-signer-role"
              style={{
                display: 'block',
                color: 'var(--text-secondary)',
                marginBottom: 'var(--space-xs)',
              }}
            >
              Sign off as
            </label>
            <select
              id="second-signer-role"
              value={selectedRole ?? ''}
              onChange={(e) => onRoleChange(e.target.value)}
              style={{ width: '100%' }}
            >
              {selectedRole === null && (
                <option value="" disabled>
                  Select a role…
                </option>
              )}
              {eligibleRoles.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
        )}

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 'var(--space-sm)',
          }}
        >
          <button type="button" className="btn-secondary" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={onConfirm}
            disabled={loading || submitDisabled}
          >
            {loading ? 'Submitting…' : 'Confirm and Submit'}
          </button>
        </div>
      </div>
    </div>
  );
}
