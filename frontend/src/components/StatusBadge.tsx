/**
 * Module:   StatusBadge
 * Purpose:  Pill-shaped badge colored by entity status with mandatory text label.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Accessibility: Color is never the sole differentiator (spec §4.1).
 * Every badge renders a visible text label alongside its background color.
 */

import type { CertStatus, NCRStatus, OpStatus, WOStatus } from '@/types/api';

type MappedStatus = WOStatus | OpStatus | NCRStatus | CertStatus | string;

interface StatusBadgeProps {
  /** Raw status value from the API. */
  status: MappedStatus;
  /** Override the display text. Falls back to a human-readable form of `status`. */
  label?: string;
}

/**
 * Maps every known status string to the CSS custom property that provides
 * its color.  Unknown statuses fall back to --status-draft (neutral gray).
 */
const STATUS_COLOR_MAP: Record<string, string> = {
  /* WOStatus */
  draft: 'var(--status-draft)',
  released: 'var(--status-active)',
  in_progress: 'var(--status-active)',
  hold: 'var(--status-hold)',
  complete: 'var(--status-complete)',
  closed: 'var(--status-draft)',

  /* OpStatus — overlaps with WO but included for clarity */
  pending: 'var(--status-draft)',

  /* NCRStatus */
  open: 'var(--status-error)',
  dispositioned: 'var(--status-hold)',
  /* closed already mapped above */

  /* CertStatus */
  quarantine: 'var(--status-hold)',
  accepted: 'var(--status-complete)',
  rejected: 'var(--status-error)',
  consumed: 'var(--status-draft)',
  expired: 'var(--status-error)',
  scrapped: 'var(--status-error)',
};

/** Turns "in_progress" into "In Progress" for display. */
function humanize(status: string): string {
  return status
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

export function StatusBadge({ status, label }: StatusBadgeProps) {
  const bgColor = STATUS_COLOR_MAP[status] ?? 'var(--status-draft)';
  const displayText = label ?? humanize(status);

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '2px var(--space-sm)',
        borderRadius: '9999px',
        backgroundColor: bgColor,
        color: '#ffffff',
        fontSize: 'var(--font-size-badge)',
        fontWeight: 600,
        lineHeight: 1.5,
        letterSpacing: '0.02em',
        whiteSpace: 'nowrap',
        userSelect: 'none',
      }}
      aria-label={`Status: ${displayText}`}
    >
      {displayText}
    </span>
  );
}
