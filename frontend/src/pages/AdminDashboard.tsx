/**
 * Module:   AdminDashboard
 * Purpose:  Admin system health overview with database, S3, audit, and outbox status.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Spec SS5.10: Dark-background admin header, system health indicators
 * (green/yellow/red), outbox pending and dead-letter counts, and links
 * to /admin/users and /admin/programs.  Audit integrity checks refresh
 * on a 5-minute interval.
 */

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { fetchAdminHealth } from '@/api/endpoints';
import { ErrorBanner } from '@/components/ErrorBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';

/* ── Status indicator colors ── */

function statusColor(status: string): string {
  switch (status) {
    case 'healthy':
    case 'ok':
    case 'verified':
      return '#22c55e'; /* green */
    case 'degraded':
    case 'warning':
    case 'unverified':
      return '#eab308'; /* yellow */
    case 'unhealthy':
    case 'error':
    case 'broken':
      return '#ef4444'; /* red */
    default:
      return 'var(--text-secondary)';
  }
}

/* ── Status dot ── */

function StatusDot({ status }: { status: string }) {
  return (
    <span
      style={{
        display: 'inline-block',
        width: '12px',
        height: '12px',
        borderRadius: '50%',
        backgroundColor: statusColor(status),
        marginRight: 'var(--space-xs)',
        verticalAlign: 'middle',
      }}
      title={status}
    />
  );
}

/* ── Health card ── */

function HealthCard({
  title,
  status,
  children,
}: {
  title: string;
  status: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      style={{
        flex: '1 1 220px',
        padding: 'var(--space-md)',
        backgroundColor: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 'var(--space-sm)' }}>
        <StatusDot status={status} />
        <span style={{ fontWeight: 600, fontSize: 'var(--font-size-body)', color: 'var(--text-primary)' }}>
          {title}
        </span>
      </div>
      <div style={{ fontSize: 'var(--font-size-body)', color: 'var(--text-secondary)' }}>
        Status: {status}
      </div>
      {children}
    </div>
  );
}

/* ── Nav link card ── */

function NavCard({ to, title, description }: { to: string; title: string; description: string }) {
  return (
    <Link
      to={to}
      style={{
        flex: '1 1 220px',
        padding: 'var(--space-md)',
        backgroundColor: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-md)',
        textDecoration: 'none',
        transition: 'border-color 0.15s',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--interactive)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)'; }}
    >
      <div style={{ fontWeight: 600, fontSize: 'var(--font-size-body)', color: 'var(--text-primary)', marginBottom: 'var(--space-xs)' }}>
        {title}
      </div>
      <div style={{ fontSize: 'var(--font-size-body)', color: 'var(--text-secondary)' }}>
        {description}
      </div>
    </Link>
  );
}

/* ── Component ── */

export default function AdminDashboard() {
  const {
    data: health,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['admin', 'health'],
    queryFn: fetchAdminHealth,
    refetchInterval: 5 * 60_000, /* 5-minute refresh for audit integrity */
  });

  return (
    <div>
      {/* Dark admin header per spec SS5.10 */}
      <div
        style={{
          backgroundColor: '#1a1a2e',
          color: '#ffffff',
          padding: 'var(--space-lg)',
          marginBottom: 'var(--space-lg)',
        }}
      >
        <h1 style={{ margin: 0, fontSize: 'var(--font-size-page)', fontWeight: 700 }}>
          Admin Dashboard
        </h1>
        <p style={{ margin: 0, marginTop: 'var(--space-xs)', color: '#9ca3af', fontSize: 'var(--font-size-body)' }}>
          System health and administration
        </p>
      </div>

      <div style={{ padding: '0 var(--space-lg) var(--space-lg)' }}>
        {isLoading && <LoadingSkeleton rows={6} />}

        {isError && (
          <ErrorBanner message={error instanceof Error ? error.message : 'Failed to load system health'} />
        )}

        {health && (
          <>
            {/* System Health section — bound to the real AdminHealthOut
                shape from the backend: service, database, audit_chain,
                worm_replication, ecs.  Each is a ComponentOut with
                status/summary/details. */}
            <h2 style={{ margin: 0, marginBottom: 'var(--space-md)', fontSize: 'var(--font-size-section)', color: 'var(--text-primary)' }}>
              System Health — Overall: {health.status}
            </h2>
            <div
              style={{
                display: 'flex',
                gap: 'var(--space-md)',
                flexWrap: 'wrap',
                marginBottom: 'var(--space-lg)',
              }}
            >
              <HealthCard title="Service" status={health.service.status}>
                <div style={{ marginTop: 'var(--space-xs)', fontSize: 'var(--font-size-badge)', color: 'var(--text-secondary)' }}>
                  {health.service.summary}
                </div>
              </HealthCard>

              <HealthCard title="Database" status={health.database.status}>
                <div style={{ marginTop: 'var(--space-xs)', fontSize: 'var(--font-size-badge)', color: 'var(--text-secondary)' }}>
                  {health.database.summary}
                </div>
              </HealthCard>

              <HealthCard title="Audit Chain" status={health.audit_chain.status}>
                <div style={{ marginTop: 'var(--space-xs)', fontSize: 'var(--font-size-badge)', color: 'var(--text-secondary)' }}>
                  {health.audit_chain.summary}
                </div>
              </HealthCard>

              <HealthCard title="WORM Replication" status={health.worm_replication.status}>
                <div style={{ marginTop: 'var(--space-xs)', fontSize: 'var(--font-size-badge)', color: 'var(--text-secondary)' }}>
                  {health.worm_replication.summary}
                </div>
              </HealthCard>

              <HealthCard title="ECS" status={health.ecs.status}>
                <div style={{ marginTop: 'var(--space-xs)', fontSize: 'var(--font-size-badge)', color: 'var(--text-secondary)' }}>
                  {health.ecs.summary}
                </div>
              </HealthCard>
            </div>

            {/* Last refreshed timestamp */}
            <p style={{ fontSize: 'var(--font-size-badge)', color: 'var(--text-secondary)', marginBottom: 'var(--space-lg)' }}>
              Last refreshed: {new Date(health.generated_at).toLocaleString()}
            </p>

            {/* Navigation links */}
            <h2 style={{ margin: 0, marginBottom: 'var(--space-md)', fontSize: 'var(--font-size-section)', color: 'var(--text-primary)' }}>
              Administration
            </h2>
            <div style={{ display: 'flex', gap: 'var(--space-md)', flexWrap: 'wrap' }}>
              <NavCard to="/admin/users" title="User Management" description="View and manage user accounts, roles, and program assignments" />
              <NavCard to="/admin/programs" title="Program Management" description="View and manage programs, scheduling, and access settings" />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
