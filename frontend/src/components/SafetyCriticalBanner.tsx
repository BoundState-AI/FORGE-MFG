/**
 * Module:   SafetyCriticalBanner
 * Purpose:  Persistent top-pinned banner for safety-critical operations.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Spec §4.1 safety-critical visual treatment: double border in
 * --safety-critical-border, background in --safety-critical-bg,
 * sticky positioning so the banner stays visible during scroll.
 */

interface SafetyCriticalBannerProps {
  /** Controls visibility. When false the banner is not rendered. */
  visible: boolean;
}

export function SafetyCriticalBanner({ visible }: SafetyCriticalBannerProps) {
  if (!visible) return null;

  return (
    <div
      role="alert"
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--space-sm)',
        padding: 'var(--space-sm) var(--space-md)',
        backgroundColor: 'var(--safety-critical-bg)',
        borderTop: '3px double var(--safety-critical-border)',
        borderBottom: '3px double var(--safety-critical-border)',
        fontWeight: 700,
        fontSize: 'var(--font-size-label)',
        letterSpacing: '0.08em',
        color: 'var(--safety-critical-border)',
        textTransform: 'uppercase',
        userSelect: 'none',
      }}
    >
      <span aria-hidden="true" style={{ fontSize: '1.2em' }}>
        ⚠
      </span>
      Safety-Critical
    </div>
  );
}
