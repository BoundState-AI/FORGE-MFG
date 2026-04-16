/**
 * Module:   PartDetail
 * Purpose:  Traceability view — part record GET not yet on the backend.
 * Rev:      0.2.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why reduced surface: The generated OpenAPI schema confirms the
 * backend does not expose GET /api/parts/{part_id}.  The traceability
 * endpoint (GET /api/parts/{part_id}/traceability) still works, so
 * this page renders the full trace graph without the part header.
 * A future backend slice will add the part GET so this page can show
 * serial_number, part_number, status, etc. (spec §5.8).
 */

import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '@/auth/store';
import { fetchTraceability } from '@/api/endpoints';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ErrorBanner } from '@/components/ErrorBanner';
import { EmptyState } from '@/components/EmptyState';
import { PageHeader } from '@/components/PageHeader';

export default function PartDetail() {
  const { id } = useParams<{ id: string }>();
  const programId = useAuthStore((s) => s.activeProgram?.id);

  const traceQuery = useQuery({
    queryKey: ['part-trace', programId, id],
    queryFn: () => fetchTraceability(id!),
    enabled: !!id,
  });

  const trace = traceQuery.data;

  if (traceQuery.isLoading) {
    return (
      <div className="page-content">
        <LoadingSkeleton rows={4} />
      </div>
    );
  }

  if (traceQuery.error || !trace) {
    return (
      <div className="page-content">
        <ErrorBanner message="Failed to load part traceability" />
      </div>
    );
  }

  const part = trace.part;
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
          <div>{part.status}</div>
        </div>
        <div>
          <label>Location</label>
          <div>{part.current_location ?? '—'}</div>
        </div>
      </div>

      <h2 className="section-header">Traceability</h2>
      <div className="card">
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
      </div>
    </div>
  );
}
