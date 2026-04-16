/**
 * Module:   ErrorBanner
 * Purpose:  Inline error banner displaying an error code and message.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Uses aria-live="polite" per spec §8 so screen readers announce
 * the error without interrupting the current task.  The optional
 * error code is displayed in monospace so engineers can search logs.
 */

interface ErrorBannerProps {
  /** Machine-readable error code (e.g. "VERSION_CONFLICT", "SoD_VIOLATION"). */
  code?: string;
  /** Human-readable error description. */
  message: string;
  /** When provided, renders a dismiss button. */
  onDismiss?: () => void;
}

export function ErrorBanner({ code, message, onDismiss }: ErrorBannerProps) {
  return (
    <div
      role="alert"
      aria-live="polite"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 'var(--space-sm)',
        padding: 'var(--space-sm) var(--space-md)',
        backgroundColor: 'var(--safety-critical-bg)',
        border: '1px solid var(--status-error)',
        borderRadius: 'var(--radius-md)',
        fontSize: 'var(--font-size-body)',
        color: 'var(--status-error)',
        lineHeight: 1.5,
      }}
    >
      {/* Content */}
      <div style={{ flex: 1 }}>
        {code && (
          <span
            style={{
              fontFamily: 'monospace',
              fontWeight: 700,
              marginRight: 'var(--space-sm)',
            }}
          >
            [{code}]
          </span>
        )}
        <span>{message}</span>
      </div>

      {/* Dismiss button */}
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss error"
          style={{
            flexShrink: 0,
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--status-error)',
            fontSize: 'var(--font-size-section)',
            lineHeight: 1,
            padding: 0,
          }}
        >
          &times;
        </button>
      )}
    </div>
  );
}
