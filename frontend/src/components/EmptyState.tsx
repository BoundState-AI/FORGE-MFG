/**
 * Module:   EmptyState
 * Purpose:  Meaningful empty state placeholder for list views with zero results.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Renders a centered block with an optional icon (emoji fallback),
 * title, and descriptive message.  Prevents users from mistaking
 * an empty list for a loading or error state.
 */

interface EmptyStateProps {
  title: string;
  message?: string;
  /** Emoji or single character rendered large above the title. */
  icon?: string;
}

export function EmptyState({
  title,
  message,
  icon = '📋',
}: EmptyStateProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 'var(--space-2xl) var(--space-md)',
        textAlign: 'center',
      }}
    >
      <span
        aria-hidden="true"
        style={{ fontSize: '48px', marginBottom: 'var(--space-md)' }}
      >
        {icon}
      </span>
      <h3
        style={{
          margin: 0,
          marginBottom: 'var(--space-xs)',
          fontSize: 'var(--font-size-section)',
          color: 'var(--text-primary)',
        }}
      >
        {title}
      </h3>
      {message && (
        <p
          style={{
            margin: 0,
            fontSize: 'var(--font-size-body)',
            color: 'var(--text-secondary)',
            maxWidth: '400px',
            lineHeight: 1.5,
          }}
        >
          {message}
        </p>
      )}
    </div>
  );
}
