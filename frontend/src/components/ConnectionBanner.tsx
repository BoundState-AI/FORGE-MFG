/**
 * Module:   ConnectionBanner
 * Purpose:  Fixed banner shown when SSE/WebSocket connection is lost (spec §6.4).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Uses aria-live="assertive" so screen readers immediately announce
 * the connection loss — this is a critical status change that may
 * cause the user to act on stale data.
 */

interface ConnectionBannerProps {
  /** True when the real-time connection is healthy. */
  connected: boolean;
}

export function ConnectionBanner({ connected }: ConnectionBannerProps) {
  if (connected) return null;

  return (
    <div
      role="alert"
      aria-live="assertive"
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 1100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--space-sm)',
        padding: 'var(--space-sm) var(--space-md)',
        backgroundColor: 'var(--status-hold)',
        color: '#ffffff',
        fontSize: 'var(--font-size-label)',
        fontWeight: 600,
        textAlign: 'center',
      }}
    >
      <span
        aria-hidden="true"
        style={{
          display: 'inline-block',
          width: '8px',
          height: '8px',
          borderRadius: '50%',
          backgroundColor: '#ffffff',
          /* Simple blinking dot to signal reconnect attempts */
          animation: 'forge-connection-blink 1s ease-in-out infinite',
        }}
      />
      Connection lost — reconnecting...
      {/* Inject keyframes inline so no external CSS is needed */}
      <style>{`
        @keyframes forge-connection-blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}
