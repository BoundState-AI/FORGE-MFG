/**
 * Module:   ConflictModal
 * Purpose:  Blocking modal for 409 version conflict errors (spec §6.3.4).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Displays the version mismatch details and offers exactly two actions:
 *   [Discard My Changes] - throws away the local edits
 *   [Reload and Edit]    - fetches the latest entity so the user can retry
 *
 * No force-overwrite option is provided.  The spec explicitly forbids
 * clients from bypassing optimistic locking (§6.3.4).
 */

import { useEffect } from 'react';
import { createPortal } from 'react-dom';

interface ConflictError {
  entity_type: string;
  entity_id: string;
  your_version: number;
  current_version: number;
  message: string;
}

interface ConflictModalProps {
  /** Pass null to hide the modal. */
  error: ConflictError | null;
  onDiscard: () => void;
  onReload: () => void;
}

export function ConflictModal({ error, onDiscard, onReload }: ConflictModalProps) {
  /* Prevent background scroll */
  useEffect(() => {
    if (!error) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [error]);

  if (!error) return null;

  return createPortal(
    <div
      role="alertdialog"
      aria-modal="true"
      aria-label="Version conflict"
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
          maxWidth: '500px',
          width: '90%',
          boxShadow: '0 4px 24px rgba(0, 0, 0, 0.15)',
        }}
      >
        {/* Header */}
        <h2
          style={{
            margin: 0,
            marginBottom: 'var(--space-sm)',
            fontSize: 'var(--font-size-section)',
            color: 'var(--status-error)',
          }}
        >
          Version Conflict
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
          {error.message}
        </p>

        {/* Version diff */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'auto 1fr',
            gap: 'var(--space-xs) var(--space-md)',
            padding: 'var(--space-sm) var(--space-md)',
            backgroundColor: 'var(--bg-elevated)',
            borderRadius: 'var(--radius-md)',
            marginBottom: 'var(--space-lg)',
            fontSize: 'var(--font-size-table)',
            fontFamily: 'monospace',
          }}
        >
          <span style={{ color: 'var(--text-muted)' }}>Entity</span>
          <span style={{ color: 'var(--text-primary)' }}>
            {error.entity_type} / {error.entity_id}
          </span>

          <span style={{ color: 'var(--text-muted)' }}>Your version</span>
          <span style={{ color: 'var(--status-error)' }}>
            {error.your_version}
          </span>

          <span style={{ color: 'var(--text-muted)' }}>Current version</span>
          <span style={{ color: 'var(--status-complete)' }}>
            {error.current_version}
          </span>
        </div>

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
            onClick={onDiscard}
            style={{
              padding: 'var(--space-sm) var(--space-md)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              fontSize: 'var(--font-size-body)',
              cursor: 'pointer',
            }}
          >
            Discard My Changes
          </button>
          <button
            type="button"
            onClick={onReload}
            style={{
              padding: 'var(--space-sm) var(--space-md)',
              border: 'none',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--interactive)',
              color: '#ffffff',
              fontSize: 'var(--font-size-body)',
              fontWeight: 600,
              cursor: 'pointer',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--interactive-hover)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--interactive)';
            }}
          >
            Reload and Edit
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
