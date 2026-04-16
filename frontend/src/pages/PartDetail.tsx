/**
 * Module:   PartDetail
 * Purpose:  Read-only part detail with full traceability chain.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Spec SS5.8: Displays serial number, part number, status, location,
 * and the full traceability chain including material certification
 * linkage, assembly chain (parent assemblies), and child parts.
 * This is a read-only page -- no mutations.
 */

import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchPart, fetchTraceability } from '@/api/endpoints';
import { StatusBadge } from '@/components/StatusBadge';
import { ErrorBanner } from '@/components/ErrorBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { PageHeader } from '@/components/PageHeader';
import type { PartOut } from '@/types/api';

/* ── Styles ── */

const sectionStyle: React.CSSProperties = {
  marginBottom: 'var(--space-lg)',
  padding: 'var(--space-md)',
  backgroundColor: 'var(--bg-surface)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)',
};

const fieldRowStyle: React.CSSProperties = {
  display: 'flex',
  gap: 'var(--space-lg)',
  marginBottom: 'var(--space-sm)',
  flexWrap: 'wrap',
};

const fieldLabelStyle: React.CSSProperties = {
  fontSize: 'var(--font-size-label)',
  fontWeight: 600,
  color: 'var(--text-secondary)',
  marginBottom: '2px',
};

const fieldValueStyle: React.CSSProperties = {
  fontSize: 'var(--font-size-body)',
  color: 'var(--text-primary)',
};

/* ── Part row for traceability lists ── */

function PartRow({ part, indent = 0 }: { part: PartOut; indent?: number }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-sm)',
        padding: 'var(--space-xs) var(--space-sm)',
        paddingLeft: `calc(var(--space-sm) + ${indent * 16}px)`,
        borderBottom: '1px solid var(--border)',
        fontSize: 'var(--font-size-body)',
      }}
    >
      <span style={{ fontWeight: 600, color: 'var(--text-primary)', minWidth: '120px' }}>
        {part.serial_number}
      </span>
      <span style={{ color: 'var(--text-secondary)', minWidth: '100px' }}>{part.part_number}</span>
      <span style={{ flex: 1, color: 'var(--text-primary)' }}>{part.part_description}</span>
      <StatusBadge status={part.status} />
    </div>
  );
}

/* ── Component ── */

export default function PartDetail() {
  const { id } = useParams<{ id: string }>();

  const {
    data: part,
    isLoading: partLoading,
    isError: partError,
    error: partErr,
  } = useQuery({
    queryKey: ['part', id],
    queryFn: () => fetchPart(id!),
    enabled: !!id,
  });

  const {
    data: traceability,
    isLoading: traceLoading,
    isError: traceError,
  } = useQuery({
    queryKey: ['traceability', id],
    queryFn: () => fetchTraceability(id!),
    enabled: !!id,
  });

  const isLoading = partLoading || traceLoading;

  if (isLoading) {
    return <div style={{ padding: 'var(--space-lg)' }}><LoadingSkeleton rows={10} /></div>;
  }

  if (partError) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <ErrorBanner message={partErr instanceof Error ? partErr.message : 'Failed to load part'} />
      </div>
    );
  }

  if (!part) return null;

  return (
    <div style={{ padding: 'var(--space-lg)', maxWidth: '900px' }}>
      <PageHeader
        title={part.serial_number}
        subtitle={`${part.part_number} -- ${part.part_description}`}
        actions={<StatusBadge status={part.status} />}
      />

      {/* Part details */}
      <div style={sectionStyle}>
        <h3 style={{ margin: 0, marginBottom: 'var(--space-sm)', color: 'var(--text-primary)' }}>Part Information</h3>
        <div style={fieldRowStyle}>
          <div>
            <div style={fieldLabelStyle}>Serial Number</div>
            <div style={fieldValueStyle}>{part.serial_number}</div>
          </div>
          <div>
            <div style={fieldLabelStyle}>Part Number</div>
            <div style={fieldValueStyle}>{part.part_number}</div>
          </div>
          <div>
            <div style={fieldLabelStyle}>Status</div>
            <div style={fieldValueStyle}><StatusBadge status={part.status} /></div>
          </div>
        </div>
        <div style={fieldRowStyle}>
          <div>
            <div style={fieldLabelStyle}>Location</div>
            <div style={fieldValueStyle}>{part.current_location ?? 'Not specified'}</div>
          </div>
          <div>
            <div style={fieldLabelStyle}>Created</div>
            <div style={fieldValueStyle}>{new Date(part.created_at).toLocaleString()}</div>
          </div>
        </div>
      </div>

      {/* Traceability section */}
      {traceError && (
        <ErrorBanner message="Failed to load traceability data" />
      )}

      {traceability && (
        <>
          {/* Material certification link */}
          {traceability.material_certification_id && (
            <div style={sectionStyle}>
              <h3 style={{ margin: 0, marginBottom: 'var(--space-sm)', color: 'var(--text-primary)' }}>
                Material Certification
              </h3>
              <div style={fieldValueStyle}>
                Linked to certification: {traceability.material_certification_id}
              </div>
            </div>
          )}

          {/* Assembly chain (parent assemblies) */}
          {traceability.assembly_chain.length > 0 && (
            <div style={sectionStyle}>
              <h3 style={{ margin: 0, marginBottom: 'var(--space-sm)', color: 'var(--text-primary)' }}>
                Assembly Chain
              </h3>
              <p style={{ margin: 0, marginBottom: 'var(--space-sm)', fontSize: 'var(--font-size-body)', color: 'var(--text-secondary)' }}>
                Parent assemblies from top-level down to this part.
              </p>
              {traceability.assembly_chain.map((p, i) => (
                <PartRow key={p.id} part={p} indent={i} />
              ))}
            </div>
          )}

          {/* Child parts */}
          {traceability.child_parts.length > 0 && (
            <div style={sectionStyle}>
              <h3 style={{ margin: 0, marginBottom: 'var(--space-sm)', color: 'var(--text-primary)' }}>
                Child Parts
              </h3>
              <p style={{ margin: 0, marginBottom: 'var(--space-sm)', fontSize: 'var(--font-size-body)', color: 'var(--text-secondary)' }}>
                Parts assembled into this component.
              </p>
              {traceability.child_parts.map((p) => (
                <PartRow key={p.id} part={p} indent={1} />
              ))}
            </div>
          )}

          {/* Traceability links */}
          {traceability.links.length > 0 && (
            <div style={sectionStyle}>
              <h3 style={{ margin: 0, marginBottom: 'var(--space-sm)', color: 'var(--text-primary)' }}>
                Traceability Links
              </h3>
              {traceability.links.map((link) => (
                <div
                  key={link.id}
                  style={{
                    display: 'flex',
                    gap: 'var(--space-sm)',
                    padding: 'var(--space-xs) 0',
                    fontSize: 'var(--font-size-body)',
                    color: 'var(--text-primary)',
                    borderBottom: '1px solid var(--border)',
                  }}
                >
                  <span style={{ fontWeight: 600 }}>{link.link_type}</span>
                  <span style={{ color: 'var(--text-secondary)' }}>
                    {link.source_type}:{link.source_id.slice(0, 8)} → {link.target_type}:{link.target_id.slice(0, 8)}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Empty traceability */}
          {!traceability.material_certification_id &&
            traceability.assembly_chain.length === 0 &&
            traceability.child_parts.length === 0 &&
            traceability.links.length === 0 && (
            <div style={sectionStyle}>
              <p style={{ margin: 0, fontSize: 'var(--font-size-body)', color: 'var(--text-secondary)' }}>
                No traceability data available for this part.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
