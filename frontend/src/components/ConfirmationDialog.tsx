/**
 * Module:   ConfirmationDialog
 * Purpose:  Modal overlay for confirming state transitions and destructive actions.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Uses createPortal to render outside the component tree, preventing
 * z-index / overflow clipping issues.  The `children` slot allows
 * embedding extra form fields (e.g. the force_stop_labor checkbox
 * on the Work Order hold transition).
 */

import { useEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

interface ConfirmationDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
  /** Renders confirm button in destructive (red) style. */
  destructive?: boolean;
  /** Disables buttons and shows spinner text while an async action is in flight. */
  loading?: boolean;
  /** Extra form fields rendered between the message and the action buttons. */
  children?: ReactNode;
}

export function ConfirmationDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  onConfirm,
  onCancel,
  destructive = false,
  loading = false,
  children,
}: ConfirmationDialogProps) {
  /* Trap focus and prevent background scroll while open */
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  /* Close on Escape key */
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !loading) onCancel();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, loading, onCancel]);

  if (!open) return null;

  const confirmBg = destructive
    ? 'var(--destructive)'
    : 'var(--interactive)';
  const confirmBgHover = destructive
    ? 'var(--destructive-hover)'
    : 'var(--interactive-hover)';

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
      }}
      onClick={(e) => {
        /* Dismiss on backdrop click unless loading */
        if (e.target === e.currentTarget && !loading) onCancel();
      }}
    >
      <div
        style={{
          backgroundColor: 'var(--bg-surface)',
          borderRadius: 'var(--radius-lg)',
          padding: 'var(--space-lg)',
          maxWidth: '480px',
          width: '90%',
          boxShadow: '0 4px 24px rgba(0, 0, 0, 0.15)',
        }}
      >
        {/* Title */}
        <h2
          style={{
            margin: 0,
            marginBottom: 'var(--space-sm)',
            fontSize: 'var(--font-size-section)',
            color: 'var(--text-primary)',
          }}
        >
          {title}
        </h2>

        {/* Message */}
        <p
          style={{
            margin: 0,
            marginBottom: 'var(--space-md)',
            fontSize: 'var(--font-size-body)',
            color: 'var(--text-secondary)',
            lineHeight: 1.5,
          }}
        >
          {message}
        </p>

        {/* Optional extra form fields */}
        {children && (
          <div style={{ marginBottom: 'var(--space-md)' }}>{children}</div>
        )}

        {/* Actions */}
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
            disabled={loading}
            style={{
              padding: 'var(--space-sm) var(--space-md)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              fontSize: 'var(--font-size-body)',
              cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? 0.6 : 1,
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading}
            style={{
              padding: 'var(--space-sm) var(--space-md)',
              border: 'none',
              borderRadius: 'var(--radius-md)',
              backgroundColor: confirmBg,
              color: '#ffffff',
              fontSize: 'var(--font-size-body)',
              fontWeight: 600,
              cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? 0.7 : 1,
            }}
            onMouseEnter={(e) => {
              if (!loading) e.currentTarget.style.backgroundColor = confirmBgHover;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = confirmBg;
            }}
          >
            {loading ? 'Processing...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
