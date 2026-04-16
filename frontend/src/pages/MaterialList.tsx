/**
 * Module:   MaterialList
 * Purpose:  Paginated material certification list with status filtering and expiry indicators.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Spec SS5.7: Lists material certifications with quantity remaining
 * shown as a progress bar.  Expired certs are flagged with an amber
 * indicator.  Program-scoped with "All" option.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchMaterials } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { EmptyState } from '@/components/EmptyState';
import { ErrorBanner } from '@/components/ErrorBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { PageHeader } from '@/components/PageHeader';
import type { CertStatus } from '@/types/api';

/* ── Constants ── */

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'all', label: 'All Statuses' },
  { value: 'quarantine', label: 'Quarantine' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'consumed', label: 'Consumed' },
  { value: 'expired', label: 'Expired' },
  { value: 'scrapped', label: 'Scrapped' },
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

/* ── Quantity progress bar ── */

function QuantityBar({ received, remaining }: { received: number; remaining: number }) {
  const pct = received > 0 ? Math.round((remaining / received) * 100) : 0;
  const barColor = pct > 50 ? 'var(--status-complete)' : pct > 20 ? 'var(--status-hold)' : 'var(--status-error)';

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-xs)', minWidth: '120px' }}>
      <div
        style={{
          flex: 1,
          height: '8px',
          backgroundColor: 'var(--border)',
          borderRadius: '4px',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: '100%',
            backgroundColor: barColor,
            borderRadius: '4px',
            transition: 'width 0.3s',
          }}
        />
      </div>
      <span style={{ fontSize: 'var(--font-size-badge)', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
        {remaining}/{received}
      </span>
    </div>
  );
}

/* ── Component ── */

export default function MaterialList() {
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
    queryKey: ['materials', programId, statusFilter, page],
    queryFn: () =>
      fetchMaterials({
        page,
        page_size: PAGE_SIZE,
        status: statusFilter !== 'all' ? (statusFilter as CertStatus) : undefined,
      }),
    refetchInterval: 60_000,
  });

  const items = data?.items ?? [];
  const totalPages = data?.pages ?? 1;

  /** Check if a shelf life expiry date is in the past. */
  function isExpired(expiry: string | null): boolean {
    if (!expiry) return false;
    return new Date(expiry) < new Date();
  }

  return (
    <div style={{ padding: 'var(--space-lg)' }}>
      <PageHeader
        title="Material Certifications"
        subtitle="Track material lots, certifications, and remaining quantities"
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
        <ErrorBanner message={error instanceof Error ? error.message : 'Failed to load materials'} />
      )}

      {!isLoading && !isError && items.length === 0 && (
        <EmptyState
          title="No material certifications found"
          message="There are no material certifications matching your current filters."
          icon="📦"
        />
      )}

      {!isLoading && !isError && items.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={thStyle}>Material Spec</th>
                  <th style={thStyle}>Lot Number</th>
                  <th style={thStyle}>Supplier</th>
                  <th style={thStyle}>Status</th>
                  <th style={thStyle}>Qty Received</th>
                  <th style={thStyle}>Qty Remaining</th>
                  <th style={thStyle}>Expiry</th>
                </tr>
              </thead>
              <tbody>
                {items.map((cert) => {
                  const expired = isExpired(cert.shelf_life_expiry);
                  return (
                    <tr key={cert.id}>
                      <td style={{ ...tdStyle, fontWeight: 600 }}>{cert.material_spec}</td>
                      <td style={tdStyle}>{cert.lot_number}</td>
                      <td style={tdStyle}>{cert.supplier}</td>
                      <td style={tdStyle}><StatusBadge status={cert.status} /></td>
                      <td style={tdStyle}>{cert.quantity_received} {cert.unit_of_measure}</td>
                      <td style={tdStyle}>
                        <QuantityBar received={cert.quantity_received} remaining={cert.quantity_remaining} />
                      </td>
                      <td style={tdStyle}>
                        <span
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 'var(--space-xs)',
                            color: expired ? 'var(--status-hold)' : 'var(--text-primary)',
                          }}
                        >
                          {cert.shelf_life_expiry
                            ? new Date(cert.shelf_life_expiry).toLocaleDateString()
                            : '--'}
                          {expired && (
                            <span
                              title="Expired"
                              style={{
                                display: 'inline-block',
                                width: '10px',
                                height: '10px',
                                borderRadius: '50%',
                                backgroundColor: 'var(--status-hold)',
                              }}
                            />
                          )}
                        </span>
                      </td>
                    </tr>
                  );
                })}
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
