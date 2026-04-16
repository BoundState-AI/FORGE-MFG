/**
 * Module:   PartList
 * Purpose:  Serialized part instance list (UX spec §5.8 + §6.7.1).
 * Rev:      0.3.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this page: UX spec §3.2 routes ``/parts`` to a part instance
 * list and §6.7.1 includes Part List in the "All (assigned programs)"
 * multi-program read set.  The list GET was added in the 5C parity
 * patch (``GET /api/parts`` with ``part:read``).  Clickable rows
 * navigate to the part detail page, which also lives on the new
 * backend detail read.
 */

import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchParts } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ErrorBanner } from '@/components/ErrorBanner';
import { EmptyState } from '@/components/EmptyState';
import { PageHeader } from '@/components/PageHeader';

const PAGE_SIZE = 25;
const REFETCH_INTERVAL_MS = 60_000;

export default function PartList() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const activeProgram = useAuthStore((s) => s.activeProgram);

  const [programFilter, setProgramFilter] = useState<string>(
    activeProgram?.id ?? '',
  );
  const [page, setPage] = useState(1);

  const isAllPrograms = programFilter === '*';
  const effectiveProgramId = isAllPrograms
    ? '*'
    : (programFilter || activeProgram?.id || 'none');

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['parts', effectiveProgramId, page],
    queryFn: () =>
      fetchParts({
        page,
        page_size: PAGE_SIZE,
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
      <PageHeader title="Parts" />

      {hasMultiplePrograms && (
        <div style={{ marginBottom: 'var(--space-md)' }}>
          <label htmlFor="part-program">Program</label>
          <select
            id="part-program"
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

      {isLoading && <LoadingSkeleton rows={5} />}
      {isError && (
        <ErrorBanner
          message={
            error instanceof Error ? error.message : 'Failed to load parts'
          }
        />
      )}

      {data && data.items.length === 0 && (
        <EmptyState
          title="No parts found"
          message="Create a part from an operation or adjust the program filter."
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <table>
            <thead>
              <tr>
                <th>Serial Number</th>
                <th>Part Number</th>
                <th>Description</th>
                <th>Status</th>
                <th>Location</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((p) => (
                <tr
                  key={p.id}
                  style={{ cursor: 'pointer' }}
                  onClick={() => navigate(`/parts/${p.id}`)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      navigate(`/parts/${p.id}`);
                    }
                  }}
                >
                  <td style={{ fontWeight: 500 }}>{p.serial_number}</td>
                  <td>{p.part_number}</td>
                  <td>{p.part_description}</td>
                  <td>
                    <StatusBadge status={p.status} />
                  </td>
                  <td>{p.current_location ?? '—'}</td>
                </tr>
              ))}
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
