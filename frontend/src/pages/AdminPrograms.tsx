/**
 * Module:   AdminPrograms
 * Purpose:  Read-only program management list for admin users.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Spec SS5.10: Lists all programs with their activation state, member
 * count, and feature flags (schedule_enabled, oracle_access).  This is
 * a read-only view in this slice -- write actions are deferred to a
 * future admin CRUD slice.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchAdminPrograms } from '@/api/endpoints';
import { EmptyState } from '@/components/EmptyState';
import { ErrorBanner } from '@/components/ErrorBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { PageHeader } from '@/components/PageHeader';

/* ── Constants ── */

const PAGE_SIZE = 25;

const thStyle: React.CSSProperties = {
  padding: 'var(--space-sm) var(--space-md)',
  textAlign: 'left',
  fontWeight: 600,
  fontSize: 'var(--font-size-label)',
  color: 'var(--text-secondary)',
  borderBottom: '2px solid var(--border)',
  whiteSpace: 'nowrap',
};

const tdStyle: React.CSSProperties = {
  padding: 'var(--space-sm) var(--space-md)',
  fontSize: 'var(--font-size-body)',
  color: 'var(--text-primary)',
  borderBottom: '1px solid var(--border)',
};

/* ── Active/inactive indicator ── */

function ActiveIndicator({ active }: { active: boolean }) {
  return (
    <span
      style={{
        display: 'inline-block',
        width: '10px',
        height: '10px',
        borderRadius: '50%',
        backgroundColor: active ? '#22c55e' : 'var(--text-secondary)',
        marginRight: 'var(--space-xs)',
        verticalAlign: 'middle',
      }}
      title={active ? 'Active' : 'Inactive'}
    />
  );
}

/* ── Component ── */

export default function AdminPrograms() {
  const [page, setPage] = useState(1);

  const {
    data,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['admin', 'programs', page],
    queryFn: () => fetchAdminPrograms({ page, page_size: PAGE_SIZE }),
  });

  const items = data?.items ?? [];
  const totalPages = data?.pages ?? 1;

  return (
    <div style={{ padding: 'var(--space-lg)' }}>
      <PageHeader
        title="Program Management"
        subtitle="View program configurations and membership (read-only)"
      />

      {isLoading && <LoadingSkeleton rows={8} />}

      {isError && (
        <ErrorBanner message={error instanceof Error ? error.message : 'Failed to load programs'} />
      )}

      {!isLoading && !isError && items.length === 0 && (
        <EmptyState
          title="No programs found"
          message="No programs are configured in the system."
          icon="📁"
        />
      )}

      {!isLoading && !isError && items.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle}>Code</th>
                  <th style={thStyle}>Name</th>
                  <th style={thStyle}>Active</th>
                  <th style={thStyle}>Members</th>
                  <th style={thStyle}>Schedule</th>
                  <th style={thStyle}>Oracle</th>
                </tr>
              </thead>
              <tbody>
                {items.map((program) => (
                  <tr key={program.id}>
                    <td style={{ ...tdStyle, fontWeight: 600 }}>{program.code}</td>
                    <td style={tdStyle}>{program.name}</td>
                    <td style={tdStyle}>
                      <ActiveIndicator active={program.active} />
                      {program.active ? 'Active' : 'Inactive'}
                    </td>
                    <td style={tdStyle}>{program.member_count}</td>
                    <td style={tdStyle}>{program.schedule_enabled ? 'Enabled' : 'Disabled'}</td>
                    <td style={tdStyle}>{program.oracle_access ? 'Enabled' : 'Disabled'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div
              style={{
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                gap: 'var(--space-sm)',
                marginTop: 'var(--space-md)',
              }}
            >
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                style={{
                  padding: 'var(--space-xs) var(--space-sm)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-md)',
                  backgroundColor: 'var(--bg-surface)',
                  color: 'var(--text-primary)',
                  cursor: page <= 1 ? 'not-allowed' : 'pointer',
                  opacity: page <= 1 ? 0.5 : 1,
                }}
              >
                Previous
              </button>
              <span style={{ fontSize: 'var(--font-size-body)', color: 'var(--text-secondary)' }}>
                Page {page} of {totalPages}
              </span>
              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                style={{
                  padding: 'var(--space-xs) var(--space-sm)',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-md)',
                  backgroundColor: 'var(--bg-surface)',
                  color: 'var(--text-primary)',
                  cursor: page >= totalPages ? 'not-allowed' : 'pointer',
                  opacity: page >= totalPages ? 0.5 : 1,
                }}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
