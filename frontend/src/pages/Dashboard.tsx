/**
 * Module:   Dashboard
 * Purpose:  Production dashboard with severity-colored tiles and WO status bar (spec section 5.1).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Tiles are filtered by the current user's roles so each persona sees
 * only their relevant metrics.  Tiles with a drill_through are clickable
 * and navigate via react-router.  The 60-second refetch interval keeps
 * the dashboard live without requiring SSE on this surface.
 */

import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchDashboardSummary } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ErrorBanner } from '@/components/ErrorBanner';
import { EmptyState } from '@/components/EmptyState';
import type {
  DashboardSummaryOut,
  DashboardTileOut,
  TileSeverity,
  WOStatus,
  WorkOrderStatusCounts,
} from '@/types/api';

/* ── Constants ── */

const REFETCH_INTERVAL_MS = 60_000;

/** Maps tile severity to a background color for the card. */
const SEVERITY_BG: Record<TileSeverity, string> = {
  info: 'var(--status-active)',
  warn: 'var(--status-hold)',
  critical: 'var(--status-error)',
};

/** Ordered WO statuses for the bar visualization. */
const WO_STATUS_ORDER: WOStatus[] = [
  'draft',
  'released',
  'in_progress',
  'hold',
  'complete',
  'closed',
];

/** Color for each WO status segment in the bar. */
const WO_STATUS_COLOR: Record<WOStatus, string> = {
  draft: 'var(--status-draft)',
  released: 'var(--status-active)',
  in_progress: '#2563eb',
  hold: 'var(--status-hold)',
  complete: 'var(--status-complete)',
  closed: '#6b7280',
};

/* ── Helpers ── */

function buildDrillUrl(tile: DashboardTileOut): string | null {
  if (!tile.drill_through) return null;
  const { path, query } = tile.drill_through;
  const params = new URLSearchParams(query).toString();
  return params ? `${path}?${params}` : path;
}

/* ── Sub-components ── */

function TileCard({
  tile,
  onClick,
}: {
  tile: DashboardTileOut;
  onClick: (() => void) | null;
}) {
  const bg = SEVERITY_BG[tile.severity] ?? SEVERITY_BG.info;
  const isClickable = onClick !== null;

  return (
    <div
      role={isClickable ? 'button' : undefined}
      tabIndex={isClickable ? 0 : undefined}
      onClick={isClickable ? onClick : undefined}
      onKeyDown={
        isClickable
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') onClick?.();
            }
          : undefined
      }
      style={{
        backgroundColor: bg,
        color: '#ffffff',
        borderRadius: 'var(--radius-lg)',
        padding: 'var(--space-md) var(--space-lg)',
        cursor: isClickable ? 'pointer' : 'default',
        minWidth: '180px',
        flex: '1 1 180px',
        transition: 'transform 0.1s',
      }}
      aria-label={`${tile.label}: ${tile.value}`}
    >
      <div
        style={{
          fontSize: '2rem',
          fontWeight: 700,
          lineHeight: 1.2,
        }}
      >
        {tile.value}
      </div>
      <div
        style={{
          fontSize: 'var(--font-size-body)',
          marginTop: 'var(--space-xs)',
          opacity: 0.9,
        }}
      >
        {tile.label}
      </div>
    </div>
  );
}

function StatusBar({ counts }: { counts: WorkOrderStatusCounts }) {
  const total = WO_STATUS_ORDER.reduce(
    (sum, s) => sum + (counts[s] ?? 0),
    0,
  );

  if (total === 0) return null;

  return (
    <div style={{ marginTop: 'var(--space-lg)' }}>
      <h3
        style={{
          margin: 0,
          marginBottom: 'var(--space-sm)',
          fontSize: 'var(--font-size-section)',
          color: 'var(--text-primary)',
        }}
      >
        Work Order Status Distribution
      </h3>

      {/* Bar */}
      <div
        style={{
          display: 'flex',
          height: '28px',
          borderRadius: 'var(--radius-md)',
          overflow: 'hidden',
          backgroundColor: 'var(--bg-elevated)',
        }}
        role="img"
        aria-label={`Work order distribution: ${WO_STATUS_ORDER.map(
          (s) => `${s} ${counts[s]}`,
        ).join(', ')}`}
      >
        {WO_STATUS_ORDER.map((status) => {
          const count = counts[status] ?? 0;
          if (count === 0) return null;
          const pct = (count / total) * 100;
          return (
            <div
              key={status}
              style={{
                width: `${pct}%`,
                backgroundColor: WO_STATUS_COLOR[status],
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#ffffff',
                fontSize: 'var(--font-size-badge)',
                fontWeight: 600,
                minWidth: count > 0 ? '24px' : 0,
                overflow: 'hidden',
                whiteSpace: 'nowrap',
              }}
              title={`${humanize(status)}: ${count}`}
            >
              {pct > 8 ? count : ''}
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--space-sm) var(--space-md)',
          marginTop: 'var(--space-sm)',
          fontSize: 'var(--font-size-table)',
          color: 'var(--text-secondary)',
        }}
      >
        {WO_STATUS_ORDER.map((status) => (
          <div
            key={status}
            style={{ display: 'flex', alignItems: 'center', gap: '6px' }}
          >
            <span
              style={{
                display: 'inline-block',
                width: '12px',
                height: '12px',
                borderRadius: '2px',
                backgroundColor: WO_STATUS_COLOR[status],
              }}
            />
            {humanize(status)}: {counts[status] ?? 0}
          </div>
        ))}
      </div>
    </div>
  );
}

function humanize(status: string): string {
  return status
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/* ── Main Component ── */

export default function Dashboard() {
  const navigate = useNavigate();
  const { roles, activeProgram } = useAuthStore();
  const programId = activeProgram?.id ?? 'none';

  const {
    data: summary,
    isLoading,
    isError,
    error,
  } = useQuery<DashboardSummaryOut>({
    queryKey: ['dashboard', programId],
    queryFn: fetchDashboardSummary,
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  /* Filter tiles to those visible to the current user's roles */
  const visibleTiles = (summary?.tiles ?? []).filter((tile) =>
    tile.visible_to_roles.some((r) => roles.includes(r as typeof roles[number])),
  );

  /* ── Loading state ── */
  if (isLoading) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <h1
          style={{
            margin: 0,
            marginBottom: 'var(--space-lg)',
            fontSize: 'var(--font-size-page)',
            color: 'var(--text-primary)',
          }}
        >
          Production Dashboard
        </h1>
        <LoadingSkeleton rows={6} />
      </div>
    );
  }

  /* ── Error state ── */
  if (isError) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <h1
          style={{
            margin: 0,
            marginBottom: 'var(--space-lg)',
            fontSize: 'var(--font-size-page)',
            color: 'var(--text-primary)',
          }}
        >
          Production Dashboard
        </h1>
        <ErrorBanner
          message={
            error instanceof Error
              ? error.message
              : 'Failed to load dashboard data.'
          }
        />
      </div>
    );
  }

  /* ── Empty state ── */
  if (!summary || visibleTiles.length === 0) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <h1
          style={{
            margin: 0,
            marginBottom: 'var(--space-lg)',
            fontSize: 'var(--font-size-page)',
            color: 'var(--text-primary)',
          }}
        >
          Production Dashboard
        </h1>
        <EmptyState
          title="No dashboard data available"
          message="There are no metrics to display for your current program and role."
        />
      </div>
    );
  }

  return (
    <div style={{ padding: 'var(--space-lg)' }}>
      <h1
        style={{
          margin: 0,
          marginBottom: 'var(--space-lg)',
          fontSize: 'var(--font-size-page)',
          color: 'var(--text-primary)',
        }}
      >
        Production Dashboard
      </h1>

      {/* Tiles grid */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--space-md)',
        }}
      >
        {visibleTiles.map((tile) => {
          const url = buildDrillUrl(tile);
          return (
            <TileCard
              key={tile.key}
              tile={tile}
              onClick={url ? () => navigate(url) : null}
            />
          );
        })}
      </div>

      {/* WO status bar */}
      {summary.work_order_status_counts && (
        <StatusBar counts={summary.work_order_status_counts} />
      )}

      {/* Generated-at timestamp */}
      <div
        style={{
          marginTop: 'var(--space-lg)',
          fontSize: 'var(--font-size-table)',
          color: 'var(--text-muted)',
          textAlign: 'right',
        }}
      >
        Last updated: {new Date(summary.generated_at).toLocaleString()}
      </div>
    </div>
  );
}
