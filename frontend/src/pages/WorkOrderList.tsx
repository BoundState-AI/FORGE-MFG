/**
 * Module:   WorkOrderList
 * Purpose:  Paginated work order list with status/program filters (spec section 5.2).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Cache key convention: ['work-orders', programId, status, page] per spec
 * section 6.7.3 so program scope is always part of the key.
 *
 * When "All Programs" is selected, the request sets X-Program-Id: * via
 * the axios client header override, and programId in the cache key is '*'.
 * The 60-second refetch interval keeps the list current.
 */

import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchWorkOrders } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ErrorBanner } from '@/components/ErrorBanner';
import { EmptyState } from '@/components/EmptyState';
import type { PaginatedResponse, WorkOrderOut } from '@/types/api';

/* ── Constants ── */

const REFETCH_INTERVAL_MS = 60_000;
const PAGE_SIZE = 25;

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: 'All Statuses' },
  { value: 'draft', label: 'Draft' },
  { value: 'released', label: 'Released' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'hold', label: 'Hold' },
  { value: 'complete', label: 'Complete' },
  { value: 'closed', label: 'Closed' },
];

/* ── Helpers ── */

function humanize(s: string): string {
  return s
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

/* ── Shared inline styles ── */

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  padding: 'var(--space-sm) var(--space-md)',
  borderBottom: '2px solid var(--border)',
  fontSize: 'var(--font-size-label)',
  color: 'var(--text-secondary)',
  fontWeight: 600,
  whiteSpace: 'nowrap',
};

const tdStyle: React.CSSProperties = {
  padding: 'var(--space-sm) var(--space-md)',
  borderBottom: '1px solid var(--border)',
  fontSize: 'var(--font-size-table)',
  color: 'var(--text-primary)',
};

const selectStyle: React.CSSProperties = {
  padding: 'var(--space-xs) var(--space-sm)',
  borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border)',
  backgroundColor: 'var(--bg-surface)',
  color: 'var(--text-primary)',
  fontSize: 'var(--font-size-body)',
};

const buttonStyle: React.CSSProperties = {
  padding: 'var(--space-xs) var(--space-sm)',
  borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border)',
  backgroundColor: 'var(--bg-surface)',
  color: 'var(--text-primary)',
  fontSize: 'var(--font-size-body)',
  cursor: 'pointer',
};

/* ── Main Component ── */

export default function WorkOrderList() {
  const navigate = useNavigate();
  const { activeProgram, hasRole, user } = useAuthStore();

  const [statusFilter, setStatusFilter] = useState<string>('');
  const [programFilter, setProgramFilter] = useState<string>(
    activeProgram?.id ?? '',
  );
  const [page, setPage] = useState(1);

  const isAllPrograms = programFilter === '*';
  const effectiveProgramId = isAllPrograms ? '*' : (programFilter || activeProgram?.id || 'none');

  /**
   * Fetch work orders.  When "All Programs" is selected, the fetchWorkOrders
   * wrapper emits X-Program-Id: * (spec §6.7.1); otherwise the typedClient
   * middleware injects the active single-program UUID from sessionStorage.
   */
  const fetchFn = useCallback(
    () =>
      fetchWorkOrders({
        page,
        page_size: PAGE_SIZE,
        status: statusFilter || undefined,
        programId: isAllPrograms ? '*' : undefined,
      }),
    [page, statusFilter, isAllPrograms],
  );

  const { data, isLoading, isError, error } = useQuery<
    PaginatedResponse<WorkOrderOut>
  >({
    queryKey: ['work-orders', effectiveProgramId, statusFilter, page],
    queryFn: fetchFn,
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const canCreate = hasRole('engineer');
  const programs = user?.program_memberships ?? [];
  const hasMultiplePrograms = programs.length > 1;

  /* ── Loading ── */
  if (isLoading) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <h1 style={{ margin: 0, marginBottom: 'var(--space-lg)', fontSize: 'var(--font-size-page)', color: 'var(--text-primary)' }}>
          Work Orders
        </h1>
        <LoadingSkeleton rows={8} />
      </div>
    );
  }

  /* ── Error ── */
  if (isError) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <h1 style={{ margin: 0, marginBottom: 'var(--space-lg)', fontSize: 'var(--font-size-page)', color: 'var(--text-primary)' }}>
          Work Orders
        </h1>
        <ErrorBanner
          message={
            error instanceof Error
              ? error.message
              : 'Failed to load work orders.'
          }
        />
      </div>
    );
  }

  const items = data?.items ?? [];
  const totalPages = data?.pages ?? 1;

  return (
    <div style={{ padding: 'var(--space-lg)' }}>
      {/* Header row */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 'var(--space-md)',
          flexWrap: 'wrap',
          gap: 'var(--space-sm)',
        }}
      >
        <h1
          style={{
            margin: 0,
            fontSize: 'var(--font-size-page)',
            color: 'var(--text-primary)',
          }}
        >
          Work Orders
        </h1>

        {canCreate && (
          <button
            type="button"
            onClick={() => navigate('/work-orders/new')}
            style={{
              padding: 'var(--space-sm) var(--space-md)',
              border: 'none',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--interactive)',
              color: '#ffffff',
              fontSize: 'var(--font-size-body)',
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Create Work Order
          </button>
        )}
      </div>

      {/* Filters */}
      <div
        style={{
          display: 'flex',
          gap: 'var(--space-sm)',
          marginBottom: 'var(--space-md)',
          flexWrap: 'wrap',
        }}
      >
        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setPage(1);
          }}
          style={selectStyle}
          aria-label="Filter by status"
        >
          {STATUS_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>

        {hasMultiplePrograms && (
          <select
            value={programFilter}
            onChange={(e) => {
              setProgramFilter(e.target.value);
              setPage(1);
            }}
            style={selectStyle}
            aria-label="Filter by program"
          >
            <option value="*">All (assigned programs)</option>
            {programs.map((p) => (
              <option key={p.id} value={p.id}>
                {p.code} - {p.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Empty state */}
      {items.length === 0 ? (
        <EmptyState
          title="No work orders found"
          message={
            statusFilter
              ? `No work orders with status "${humanize(statusFilter)}".`
              : 'No work orders exist for this program yet.'
          }
        />
      ) : (
        <>
          {/* Table */}
          <div style={{ overflowX: 'auto' }}>
            <table
              style={{
                width: '100%',
                borderCollapse: 'collapse',
                backgroundColor: 'var(--bg-surface)',
                borderRadius: 'var(--radius-md)',
              }}
            >
              <thead>
                <tr>
                  <th style={thStyle}>WO Number</th>
                  <th style={thStyle}>Engine Serial</th>
                  <th style={thStyle}>Module</th>
                  <th style={thStyle}>Build Phase</th>
                  <th style={thStyle}>Status</th>
                  <th style={thStyle}>Priority</th>
                  <th style={thStyle}>Created</th>
                </tr>
              </thead>
              <tbody>
                {items.map((wo) => (
                  <tr
                    key={wo.id}
                    onClick={() => navigate(`/work-orders/${wo.id}`)}
                    style={{ cursor: 'pointer' }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.backgroundColor =
                        'var(--bg-elevated)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.backgroundColor = 'transparent';
                    }}
                    role="link"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') navigate(`/work-orders/${wo.id}`);
                    }}
                    aria-label={`Work order ${wo.wo_number}`}
                  >
                    <td style={{ ...tdStyle, fontWeight: 600 }}>
                      {wo.wo_number}
                    </td>
                    <td style={tdStyle}>{wo.engine_serial}</td>
                    <td style={tdStyle}>{wo.module}</td>
                    <td style={tdStyle}>{humanize(wo.build_phase)}</td>
                    <td style={tdStyle}>
                      <StatusBadge status={wo.status} />
                    </td>
                    <td style={tdStyle}>{humanize(wo.priority)}</td>
                    <td style={tdStyle}>{formatDate(wo.created_at)}</td>
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
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                style={{
                  ...buttonStyle,
                  opacity: page <= 1 ? 0.5 : 1,
                  cursor: page <= 1 ? 'not-allowed' : 'pointer',
                }}
              >
                Previous
              </button>
              <span
                style={{
                  fontSize: 'var(--font-size-body)',
                  color: 'var(--text-secondary)',
                }}
              >
                Page {page} of {totalPages}
              </span>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                style={{
                  ...buttonStyle,
                  opacity: page >= totalPages ? 0.5 : 1,
                  cursor: page >= totalPages ? 'not-allowed' : 'pointer',
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
