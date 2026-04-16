/**
 * Module:   NCRList
 * Purpose:  Paginated list of Non-Conformance Reports with status filtering.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Displays all NCRs scoped to the selected program (or all programs).
 * Rows are clickable through to NCRDetail.  Auto-refreshes every 60s
 * to pick up disposition changes made by other users.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { fetchNCRs } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { EmptyState } from '@/components/EmptyState';
import { ErrorBanner } from '@/components/ErrorBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { PageHeader } from '@/components/PageHeader';
import type { NCRStatus } from '@/types/api';

/* ── Constants ── */

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'all', label: 'All Statuses' },
  { value: 'open', label: 'Open' },
  { value: 'dispositioned', label: 'Dispositioned' },
  { value: 'closed', label: 'Closed' },
];

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

/* ── Component ── */

export default function NCRList() {
  const navigate = useNavigate();
  const { activeProgram } = useAuthStore();

  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [programFilter, setProgramFilter] = useState<string>(activeProgram?.id ?? 'all');
  const [page, setPage] = useState(1);

  const programId = programFilter === 'all' ? '*' : programFilter;

  const {
    data,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['ncrs', programId, statusFilter, page],
    queryFn: () =>
      fetchNCRs({
        page,
        page_size: PAGE_SIZE,
        status: statusFilter !== 'all' ? (statusFilter as NCRStatus) : undefined,
        programId,
      }),
    refetchInterval: 60_000,
  });

  const items = data?.items ?? [];
  const totalPages = data?.pages ?? 1;

  /** Truncate long descriptions for table display. */
  function truncate(text: string, max = 80): string {
    return text.length > max ? text.slice(0, max) + '...' : text;
  }

  return (
    <div style={{ padding: 'var(--space-lg)' }}>
      <PageHeader
        title="Non-Conformance Reports"
        subtitle="Track and disposition non-conformances"
        actions={
          <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center' }}>
            <select
              value={programFilter}
              onChange={(e) => { setProgramFilter(e.target.value); setPage(1); }}
              style={{
                padding: 'var(--space-xs) var(--space-sm)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border)',
                fontSize: 'var(--font-size-body)',
                backgroundColor: 'var(--bg-surface)',
                color: 'var(--text-primary)',
              }}
            >
              <option value="all">All Programs</option>
              {activeProgram && (
                <option value={activeProgram.id}>{activeProgram.code}</option>
              )}
            </select>

            <select
              value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
              style={{
                padding: 'var(--space-xs) var(--space-sm)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border)',
                fontSize: 'var(--font-size-body)',
                backgroundColor: 'var(--bg-surface)',
                color: 'var(--text-primary)',
              }}
            >
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
        }
      />

      {isLoading && <LoadingSkeleton rows={8} />}

      {isError && (
        <ErrorBanner message={error instanceof Error ? error.message : 'Failed to load NCRs'} />
      )}

      {!isLoading && !isError && items.length === 0 && (
        <EmptyState
          title="No NCRs found"
          message="There are no non-conformance reports matching your current filters."
          icon="📋"
        />
      )}

      {!isLoading && !isError && items.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle}>NCR Number</th>
                  <th style={thStyle}>WO</th>
                  <th style={thStyle}>Status</th>
                  <th style={thStyle}>Disposition</th>
                  <th style={thStyle}>Description</th>
                  <th style={thStyle}>Created</th>
                </tr>
              </thead>
              <tbody>
                {items.map((ncr) => (
                  <tr
                    key={ncr.id}
                    onClick={() => navigate(`/ncrs/${ncr.id}`)}
                    style={{ cursor: 'pointer', transition: 'background-color 0.15s' }}
                    onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--bg-surface)'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
                  >
                    <td style={{ ...tdStyle, fontWeight: 600 }}>{ncr.ncr_number}</td>
                    <td style={tdStyle}>{ncr.work_order_id.slice(0, 8)}</td>
                    <td style={tdStyle}><StatusBadge status={ncr.status} /></td>
                    <td style={tdStyle}>{ncr.disposition ? ncr.disposition.replace(/_/g, ' ') : '--'}</td>
                    <td style={{ ...tdStyle, maxWidth: '300px' }}>{truncate(ncr.description)}</td>
                    <td style={tdStyle}>{new Date(ncr.created_at).toLocaleDateString()}</td>
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
