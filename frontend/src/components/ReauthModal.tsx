/**
 * Module:   ReauthModal
 * Purpose:  Modal that appears when a protected action returns
 *           401 REAUTH_REQUIRED.  Surfaces the action summary, lets
 *           the user explicitly Confirm-and-Re-Authenticate, and
 *           offers a Cancel that drops the buffered intent cleanly.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this modal exists separately from ConfirmationDialog: the
 * re-auth flow has three distinct user moments — (1) "I need you to
 * re-authenticate to continue", (2) the redirect itself, (3) "OK,
 * you're back, confirm you still want to do this" — and conflating
 * them into the generic confirmation dialog would muddy the spec
 * §11.3 contract that NO auto-replay happens after fresh login.
 */

import { useEffect } from 'react';
import { createPortal } from 'react-dom';

interface ReauthModalProps {
  open: boolean;
  /** Action label from the REAUTH envelope.  GC-4 extends the set. */
  action: 'sign_off' | 'ncr_disposition' | 'wo_close' | 'ncr_close';
  /** Human-readable summary of what the user was doing. */
  actionSummary: string;
  /** Optional age line: "Your sign-in is N seconds old." */
  authAgeSeconds?: number | null;
  /**
   * True when this is the second-signer flow — copy emphasises that a
   * different user must sign.  False for ordinary stale-auth recovery.
   */
  forceFreshPrincipal?: boolean;
  /** Begin the redirect to Cognito hosted login. */
  onConfirmReauth: () => void;
  /** Discard the buffered intent and close. */
  onCancel: () => void;
}

export function ReauthModal({
  open,
  action,
  actionSummary,
  authAgeSeconds,
  forceFreshPrincipal = false,
  onConfirmReauth,
  onCancel,
}: ReauthModalProps) {
  /* Lock body scroll while open — same posture as ConfirmationDialog. */
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;

  const actionLabel =
    action === 'sign_off'
      ? 'sign-off'
      : action === 'ncr_disposition'
        ? 'NCR disposition'
        : action === 'wo_close'
          ? 'work order closure'
          : 'NCR closure';
  const heading = forceFreshPrincipal
    ? 'Second sign-off requires a different user'
    : 'Re-authentication required';
  const body = forceFreshPrincipal
    ? `A different user must sign in to record the second ${actionLabel}. ` +
      `You will be redirected to the login page; the first signer's ` +
      `session will not satisfy this requirement.`
    : `This ${actionLabel} requires re-authentication. ` +
      `Sign in again to continue.`;

  return createPortal(
    <div
      role="alertdialog"
      aria-modal="true"
      aria-label={heading}
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
          boxShadow: '0 4px 24px rgba(0, 0, 0, 0.15)',
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
          {heading}
        </h2>

        <p
          style={{
            margin: 0,
            marginBottom: 'var(--space-md)',
            color: 'var(--text-secondary)',
            lineHeight: 1.5,
          }}
        >
          {body}
        </p>

        {/* Action context — what the user was about to commit to. */}
        <div
          style={{
            padding: 'var(--space-sm) var(--space-md)',
            background: 'var(--bg-elevated)',
            borderRadius: 'var(--radius-md)',
            marginBottom: 'var(--space-md)',
            fontSize: 'var(--font-size-table)',
          }}
        >
          <div style={{ color: 'var(--text-muted)' }}>You were:</div>
          <div style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
            {actionSummary}
          </div>
          {typeof authAgeSeconds === 'number' && (
            <div
              style={{
                marginTop: 'var(--space-xs)',
                color: 'var(--text-muted)',
                fontSize: 'var(--font-size-caption)',
              }}
            >
              Your sign-in is {authAgeSeconds} seconds old.
            </div>
          )}
        </div>

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 'var(--space-sm)',
          }}
        >
          <button
            type="button"
            onClick={onCancel}
            className="btn-secondary"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirmReauth}
            className="btn-primary"
          >
            Confirm and Re-Authenticate
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
