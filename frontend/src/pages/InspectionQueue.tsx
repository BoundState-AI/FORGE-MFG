/**
 * Module:   InspectionQueue
 * Purpose:  Inspector queue listing pending and overdue inspection hold points.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Spec SS5.5.5: The inspection queue surfaces all operations with an
 * active inspection hold, sorted oldest-pending-first with safety-critical
 * items promoted.  qa_manager sees a read-only view; inspectors can click
 * through to record an inspection.
 */

import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { fetchInspectionQueue } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { EmptyState } from '@/components/EmptyState';
import { ErrorBanner } from '@/components/ErrorBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { PageHeader } from '@/components/PageHeader';
import type { InspectionQueueState, InspectionQueueRowOut } from '@/types/api';

/* ── Constants ── */

const STATE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'all', label: 'All States' },
  { value: 'pending', label: 'Pending' },
  { value: 'conditional', label: 'Conditional' },
  { value: 'overdue', label: 'Overdue' },
  { value: 'completed', label: 'Completed' },
];

const PAGE_SIZE = 25;

/* ── Shared inline styles ── */

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

export default function InspectionQueue() {
  const navigate = useNavigate();
  const { activeProgram, hasRole } = useAuthStore();
  const isQaManager = hasRole('qa_manager') && !hasRole('inspector');

  /* Filters */
  const [stateFilter, setStateFilter] = useState<string>('all');
  const [safetyCriticalOnly, setSafetyCriticalOnly] = useState(false);
  const [programFilter, setProgramFilter] = useState<string>(activeProgram?.id ?? 'all');
  const [page, setPage] = useState(1);

  /* Build query params */
  const queryParams = useMemo(() => {
    const params: Record<string, unknown> = {
      page,
      page_size: PAGE_SIZE,
    };
    if (stateFilter !== 'all') params.state = stateFilter;
    if (safetyCriticalOnly) params.safety_critical = true;
    return params;
  }, [stateFilter, safetyCriticalOnly, page]);

  const programId = programFilter === 'all' ? '*' : programFilter;

  const {
    data,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['inspections', programId, stateFilter, safetyCriticalOnly, page],
    queryFn: () => fetchInspectionQueue({ ...queryParams, programId } as Parameters<typeof fetchInspectionQueue>[0]),
    refetchInterval: 30_000,
  });

  /* Sort: oldest pending first, then safety_critical, then overdue_since */
  const sortedItems = useMemo(() => {
    if (!data?.items) return [];
    return [...data.items].sort((a, b) => {
      /* Pending items first */
      const stateOrder: Record<InspectionQueueState, number> = {
        pending: 0,
        overdue: 1,
        conditional: 2,
        completed: 3,
      };
      const stateA = stateOrder[a.state] ?? 4;
      const stateB = stateOrder[b.state] ?? 4;
      if (stateA !== stateB) return stateA - stateB;

      /* Safety-critical promoted */
      if (a.safety_critical !== b.safety_critical) return a.safety_critical ? -1 : 1;

      /* Oldest overdue_since first */
      if (a.overdue_since && b.overdue_since) {
        return new Date(a.overdue_since).getTime() - new Date(b.overdue_since).getTime();
      }
      if (a.overdue_since) return -1;
      if (b.overdue_since) return 1;

      return new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime();
    });
  }, [data?.items]);

  /* Row click handler */
  function handleRowClick(row: InspectionQueueRowOut) {
    if (isQaManager) return; /* Read-only for qa_manager */
    const base = `/work-orders/${row.work_order_id}/ops/${row.operation_id}/inspect`;
    const search = row.state === 'conditional' && row.conditional_inspection_id
      ? `?resolves=${row.conditional_inspection_id}`
      : '';
    navigate(`${base}${search}`);
  }

  /* Pagination helpers */
  const totalPages = data?.pages ?? 1;

  return (
    <div style={{ padding: 'var(--space-lg)' }}>
      <PageHeader
        title="Inspection Queue"
        subtitle="Pending inspection hold points across operations"
        banner={
          isQaManager ? (
            <div
              style={{
                padding: 'var(--space-sm) var(--space-md)',
                backgroundColor: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                fontSize: 'var(--font-size-body)',
                color: 'var(--text-secondary)',
              }}
            >
              Read-only view -- QA Manager role cannot record inspections
            </div>
          ) : undefined
        }
        actions={
          <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center', flexWrap: 'wrap' }}>
            {/* Program filter */}
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

            {/* State filter */}
            <select
              value={stateFilter}
              onChange={(e) => { setStateFilter(e.target.value); setPage(1); }}
              style={{
                padding: 'var(--space-xs) var(--space-sm)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border)',
                fontSize: 'var(--font-size-body)',
                backgroundColor: 'var(--bg-surface)',
                color: 'var(--text-primary)',
              }}
            >
              {STATE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>

            {/* Safety-critical toggle */}
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 'var(--space-xs)',
                fontSize: 'var(--font-size-body)',
                color: 'var(--text-primary)',
                cursor: 'pointer',
              }}
            >
              <input
                type="checkbox"
                checked={safetyCriticalOnly}
                onChange={(e) => { setSafetyCriticalOnly(e.target.checked); setPage(1); }}
              />
              Safety Critical Only
            </label>
          </div>
        }
      />

      {/* Loading state */}
      {isLoading && <LoadingSkeleton rows={8} />}

      {/* Error state */}
      {isError && (
        <ErrorBanner
          message={error instanceof Error ? error.message : 'Failed to load inspection queue'}
        />
      )}

      {/* Empty state */}
      {!isLoading && !isError && sortedItems.length === 0 && (
        <EmptyState
          title="No inspections pending"
          message="There are no inspection hold points matching your current filters."
          icon="🔍"
        />
      )}

      {/* Table */}
      {!isLoading && !isError && sortedItems.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle}>WO Number</th>
                  <th style={thStyle}>Op Seq</th>
                  <th style={thStyle}>Description</th>
                  <th style={thStyle}>Hold Status</th>
                  <th style={thStyle}>State</th>
                  <th style={thStyle}>Safety</th>
                  <th style={thStyle}>Overdue Since</th>
                </tr>
              </thead>
              <tbody>
                {sortedItems.map((row) => (
                  <tr
                    key={`${row.operation_id}-${row.state}`}
                    onClick={() => handleRowClick(row)}
                    style={{
                      cursor: isQaManager ? 'default' : 'pointer',
                      transition: 'background-color 0.15s',
                    }}
                    onMouseEnter={(e) => {
                      if (!isQaManager) e.currentTarget.style.backgroundColor = 'var(--bg-surface)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.backgroundColor = 'transparent';
                    }}
                  >
                    <td style={tdStyle}>{row.work_order_number}</td>
                    <td style={tdStyle}>{row.sequence_number}</td>
                    <td style={{ ...tdStyle, maxWidth: '300px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {row.description}
                    </td>
                    <td style={tdStyle}>
                      <StatusBadge status={row.hold_point_status} />
                    </td>
                    <td style={tdStyle}>
                      <StatusBadge status={row.state} />
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'center' }}>
                      {row.safety_critical && (
                        <span title="Safety Critical" style={{ fontSize: '1.2em' }}>⚠</span>
                      )}
                    </td>
                    <td style={tdStyle}>
                      {row.overdue_since
                        ? new Date(row.overdue_since).toLocaleDateString()
                        : '--'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
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
