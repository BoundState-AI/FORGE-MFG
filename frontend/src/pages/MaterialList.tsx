/**
 * Module:   MaterialList
 * Purpose:  Material certification list (UX spec §5.7).
 * Rev:      0.3.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this page: UX spec §5.7 mandates a Material Certification List
 * with status filtering, program filtering (including the "All
 * (assigned programs)" option from §6.7.1), expired-cert flagging,
 * and a qty-remaining progress bar.  The list GET was added in the
 * 5C parity patch (`GET /api/material-certs` with `mat:read`).
 *
 * The page fetches through the typed client via ``fetchMaterials``,
 * which routes ``programId: '*'`` into an ``X-Program-Id: *`` header
 * override (spec §6.7.1).
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchMaterials } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ErrorBanner } from '@/components/ErrorBanner';
import { EmptyState } from '@/components/EmptyState';
import { PageHeader } from '@/components/PageHeader';
import type { CertStatus } from '@/types/api';

const PAGE_SIZE = 25;
const REFETCH_INTERVAL_MS = 60_000;

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'all', label: 'All Statuses' },
  { value: 'quarantine', label: 'Quarantine' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'consumed', label: 'Consumed' },
  { value: 'expired', label: 'Expired' },
  { value: 'scrapped', label: 'Scrapped' },
];

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

/** Returns true when the cert's shelf life has already elapsed. */
function isExpired(shelfLife: string | null): boolean {
  if (!shelfLife) return false;
  return new Date(shelfLife).getTime() < Date.now();
}

export default function MaterialList() {
  const user = useAuthStore((s) => s.user);
  const activeProgram = useAuthStore((s) => s.activeProgram);

  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [programFilter, setProgramFilter] = useState<string>(
    activeProgram?.id ?? '',
  );
  const [page, setPage] = useState(1);

  const isAllPrograms = programFilter === '*';
  const effectiveProgramId = isAllPrograms
    ? '*'
    : (programFilter || activeProgram?.id || 'none');

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['materials', effectiveProgramId, statusFilter, page],
    queryFn: () =>
      fetchMaterials({
        page,
        page_size: PAGE_SIZE,
        status:
          statusFilter !== 'all' ? (statusFilter as CertStatus) : undefined,
        programId: isAllPrograms ? '*' : undefined,
      }),
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  const programs = useMemo(
    () => user?.program_memberships ?? [],
    [user?.program_memberships],
  );
  const hasMultiplePrograms = programs.length > 1;

  return (
    <div className="page-content">
      <PageHeader title="Materials" />

      {/* Filters */}
      <div
        style={{
          display: 'flex',
          gap: 'var(--space-md)',
          flexWrap: 'wrap',
          marginBottom: 'var(--space-md)',
        }}
      >
        <div>
          <label htmlFor="mat-status">Status</label>
          <select
            id="mat-status"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(1);
            }}
            style={{ minWidth: 180 }}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        {hasMultiplePrograms && (
          <div>
            <label htmlFor="mat-program">Program</label>
            <select
              id="mat-program"
              value={programFilter}
              onChange={(e) => {
                setProgramFilter(e.target.value);
                setPage(1);
              }}
              style={{ minWidth: 220 }}
            >
              <option value="*">All (assigned programs)</option>
              {programs.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.code} — {p.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {isLoading && <LoadingSkeleton rows={5} />}
      {isError && (
        <ErrorBanner
          message={
            error instanceof Error
              ? error.message
              : 'Failed to load materials'
          }
        />
      )}

      {data && data.items.length === 0 && (
        <EmptyState
          title="No material certifications found"
          message="Try adjusting the status or program filter."
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <table>
            <thead>
              <tr>
                <th>Material Spec</th>
                <th>Lot</th>
                <th>Supplier</th>
                <th>Status</th>
                <th>Remaining / Received</th>
                <th>Expires</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((cert) => {
                const expired = isExpired(cert.shelf_life_expiry);
                const pct =
                  cert.quantity_received > 0
                    ? Math.round(
                        (cert.quantity_remaining / cert.quantity_received) *
                          100,
                      )
                    : 0;
                return (
                  <tr key={cert.id}>
                    <td style={{ fontWeight: 500 }}>{cert.material_spec}</td>
                    <td>{cert.lot_number}</td>
                    <td>{cert.supplier}</td>
                    <td>
                      <StatusBadge status={cert.status} />
                    </td>
                    <td style={{ minWidth: 180 }}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 'var(--space-sm)',
                        }}
                      >
                        <div
                          style={{
                            flex: 1,
                            height: 8,
                            background: 'var(--bg-elevated)',
                            borderRadius: 4,
                            overflow: 'hidden',
                          }}
                        >
                          <div
                            style={{
                              width: `${pct}%`,
                              height: '100%',
                              background:
                                pct < 20
                                  ? 'var(--status-hold)'
                                  : 'var(--status-active)',
                            }}
                          />
                        </div>
                        <span
                          style={{
                            fontSize: 'var(--font-size-caption)',
                            color: 'var(--text-secondary)',
                            fontVariantNumeric: 'tabular-nums',
                            minWidth: 90,
                            textAlign: 'right',
                          }}
                        >
                          {cert.quantity_remaining} / {cert.quantity_received}{' '}
                          {cert.unit_of_measure}
                        </span>
                      </div>
                    </td>
                    <td>
                      {expired ? (
                        <span
                          style={{
                            color: 'var(--status-hold)',
                            fontWeight: 600,
                          }}
                          title="Shelf life has expired"
                        >
                          ⚠ {formatDate(cert.shelf_life_expiry)}
                        </span>
                      ) : (
                        formatDate(cert.shelf_life_expiry)
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {data.pages > 1 && (
            <div
              style={{
                display: 'flex',
                gap: 'var(--space-sm)',
                marginTop: 'var(--space-md)',
                justifyContent: 'center',
                alignItems: 'center',
              }}
            >
              <button
                type="button"
                className="btn-secondary"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
              >
                Previous
              </button>
              <span style={{ color: 'var(--text-secondary)' }}>
                Page {data.page} of {data.pages}
              </span>
              <button
                type="button"
                className="btn-secondary"
                disabled={page >= data.pages}
                onClick={() => setPage(page + 1)}
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
