/**
 * Module:   PartList
 * Purpose:  Paginated list of serialized part instances with status badges.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Spec SS5.8: Lists all part instances with serial number, part number,
 * status, and location.  Rows click through to PartDetail for full
 * traceability view.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { fetchParts } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
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

/* ── Component ── */

export default function PartList() {
  const navigate = useNavigate();
  const { activeProgram } = useAuthStore();

  const [page, setPage] = useState(1);

  const programId = activeProgram?.id ?? '*';

  const {
    data,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['parts', programId, page],
    queryFn: () => fetchParts({ page, page_size: PAGE_SIZE }),
  });

  const items = data?.items ?? [];
  const totalPages = data?.pages ?? 1;

  return (
    <div style={{ padding: 'var(--space-lg)' }}>
      <PageHeader
        title="Parts"
        subtitle="Serialized part instances and traceability"
      />

      {isLoading && <LoadingSkeleton rows={8} />}

      {isError && (
        <ErrorBanner message={error instanceof Error ? error.message : 'Failed to load parts'} />
      )}

      {!isLoading && !isError && items.length === 0 && (
        <EmptyState
          title="No parts found"
          message="There are no part instances in the system yet."
          icon="🔩"
        />
      )}

      {!isLoading && !isError && items.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle}>Serial Number</th>
                  <th style={thStyle}>Part Number</th>
                  <th style={thStyle}>Description</th>
                  <th style={thStyle}>Status</th>
                  <th style={thStyle}>Location</th>
                  <th style={thStyle}>Created</th>
                </tr>
              </thead>
              <tbody>
                {items.map((part) => (
                  <tr
                    key={part.id}
                    onClick={() => navigate(`/parts/${part.id}`)}
                    style={{ cursor: 'pointer', transition: 'background-color 0.15s' }}
                    onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = 'var(--bg-surface)'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = 'transparent'; }}
                  >
                    <td style={{ ...tdStyle, fontWeight: 600 }}>{part.serial_number}</td>
                    <td style={tdStyle}>{part.part_number}</td>
                    <td style={{ ...tdStyle, maxWidth: '300px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {part.part_description}
                    </td>
                    <td style={tdStyle}><StatusBadge status={part.status} /></td>
                    <td style={tdStyle}>{part.current_location ?? '--'}</td>
                    <td style={tdStyle}>{new Date(part.created_at).toLocaleDateString()}</td>
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
