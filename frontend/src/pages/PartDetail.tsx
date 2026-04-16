/**
 * Module:   PartDetail
 * Purpose:  Part detail header + full traceability chain (UX spec §5.8).
 * Rev:      0.3.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this page: UX spec §5.8 part traceability shows the material
 * cert → operations → inspections → NCRs → parent assemblies chain.
 * The 5C parity patch split the data into two reads:
 *   - GET /api/parts/{id}              → part header fields
 *   - GET /api/parts/{id}/traceability → full assembly + child tree
 *
 * Rendering them as two parallel queries means the header paints as
 * soon as the cheap detail read returns; the heavier traceability
 * chain finishes loading underneath.
 *
 * Cross-program access: UX spec §6.7.4 dictates 404 (not 403) for
 * entity-detail reads outside the caller's programs — this page
 * surfaces that as a generic not-found message rather than leaking
 * the 403/404 distinction.
 */

import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '@/auth/store';
import { fetchPart, fetchTraceability } from '@/api/endpoints';
import { StatusBadge } from '@/components/StatusBadge';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ErrorBanner } from '@/components/ErrorBanner';
import { EmptyState } from '@/components/EmptyState';
import { PageHeader } from '@/components/PageHeader';

export default function PartDetail() {
  const { id } = useParams<{ id: string }>();
  const programId = useAuthStore((s) => s.activeProgram?.id);

  const partQuery = useQuery({
    queryKey: ['part', programId, id],
    queryFn: () => fetchPart(id!),
    enabled: !!id,
  });

  const traceQuery = useQuery({
    queryKey: ['part-trace', programId, id],
    queryFn: () => fetchTraceability(id!),
    enabled: !!id,
  });

  if (partQuery.isLoading) {
    return (
      <div className="page-content">
        <LoadingSkeleton rows={4} />
      </div>
    );
  }

  if (partQuery.error || !partQuery.data) {
    return (
      <div className="page-content">
        <ErrorBanner message="Part not found" />
      </div>
    );
  }

  const part = partQuery.data;
  const trace = traceQuery.data;

  return (
    <div className="page-content">
      <PageHeader
        title={part.serial_number}
        breadcrumbs={[
          { label: 'Parts', to: '/parts' },
          { label: part.serial_number },
        ]}
      />

      <div
        className="card"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
          gap: 'var(--space-md)',
          marginBottom: 'var(--space-lg)',
        }}
      >
        <div>
          <label>Part Number</label>
          <div>{part.part_number}</div>
        </div>
        <div>
          <label>Description</label>
          <div>{part.part_description}</div>
        </div>
        <div>
          <label>Status</label>
          <StatusBadge status={part.status} />
        </div>
        <div>
          <label>Location</label>
          <div>{part.current_location ?? '—'}</div>
        </div>
      </div>

      <h2 className="section-header">Traceability</h2>
      <div className="card">
        {traceQuery.isLoading && <LoadingSkeleton rows={3} />}
        {traceQuery.error && !traceQuery.isLoading && (
          <ErrorBanner message="Failed to load traceability chain" />
        )}
        {trace && (
          <>
            {trace.material_certification_id && (
              <div style={{ marginBottom: 'var(--space-sm)' }}>
                <label>Material Cert</label>
                <div>{trace.material_certification_id}</div>
              </div>
            )}
            {trace.assembly_chain.length > 0 && (
              <div style={{ marginBottom: 'var(--space-sm)' }}>
                <label>Assembly Chain</label>
                {trace.assembly_chain.map((a) => (
                  <div key={a.id}>
                    {a.serial_number} — {a.part_description}
                  </div>
                ))}
              </div>
            )}
            {trace.child_parts.length > 0 && (
              <div>
                <label>Child Parts</label>
                {trace.child_parts.map((c) => (
                  <div key={c.id}>
                    {c.serial_number} — {c.part_description}
                  </div>
                ))}
              </div>
            )}
            {trace.assembly_chain.length === 0 &&
              trace.child_parts.length === 0 &&
              !trace.material_certification_id && (
                <EmptyState title="No traceability links found" />
              )}
          </>
        )}
      </div>
    </div>
  );
}
