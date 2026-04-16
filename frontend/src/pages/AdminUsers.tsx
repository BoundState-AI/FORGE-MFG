/**
 * Module:   AdminUsers
 * Purpose:  Read-only user management list for admin users.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Spec SS5.10: Lists all users with display name, email, active status,
 * roles (as badge list), and program memberships (as badge list).
 * This is a read-only view in this slice -- write actions are deferred.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchAdminUsers } from '@/api/endpoints';
import { StatusBadge } from '@/components/StatusBadge';
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

/* ── Active indicator ── */

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

/* ── Badge list for roles/programs ── */

function BadgeList({ items }: { items: string[] }) {
  if (items.length === 0) return <span style={{ color: 'var(--text-secondary)' }}>--</span>;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
      {items.map((item) => (
        <StatusBadge key={item} status={item} label={item} />
      ))}
    </div>
  );
}

/* ── Component ── */

export default function AdminUsers() {
  const [page, setPage] = useState(1);

  const {
    data,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['admin', 'users', page],
    queryFn: () => fetchAdminUsers({ page, page_size: PAGE_SIZE }),
  });

  const items = data?.items ?? [];
  const totalPages = data?.pages ?? 1;

  return (
    <div style={{ padding: 'var(--space-lg)' }}>
      <PageHeader
        title="User Management"
        subtitle="View user accounts, roles, and program assignments (read-only)"
      />

      {isLoading && <LoadingSkeleton rows={8} />}

      {isError && (
        <ErrorBanner message={error instanceof Error ? error.message : 'Failed to load users'} />
      )}

      {!isLoading && !isError && items.length === 0 && (
        <EmptyState
          title="No users found"
          message="No user accounts are configured in the system."
          icon="👤"
        />
      )}

      {!isLoading && !isError && items.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle}>Display Name</th>
                  <th style={thStyle}>Email</th>
                  <th style={thStyle}>Active</th>
                  <th style={thStyle}>Roles</th>
                  <th style={thStyle}>Programs</th>
                </tr>
              </thead>
              <tbody>
                {items.map((user) => (
                  <tr key={user.id}>
                    <td style={{ ...tdStyle, fontWeight: 600 }}>{user.display_name}</td>
                    <td style={tdStyle}>{user.email}</td>
                    <td style={tdStyle}>
                      <ActiveIndicator active={user.active} />
                      {user.active ? 'Active' : 'Inactive'}
                    </td>
                    <td style={tdStyle}>
                      <BadgeList items={user.roles.map((r) => r.name)} />
                    </td>
                    <td style={tdStyle}>
                      <BadgeList items={user.program_memberships.map((p) => p.code)} />
                    </td>
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
