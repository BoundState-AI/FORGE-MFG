/**
 * Module:   AuditTrail
 * Purpose:  Entity-scoped audit event list for the WO Detail Audit Trail
 *           tab and the NCR Detail history section (UX spec §6.6.3).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this component is the observability surface (and not an inline
 * renderer in each page):
 *   - Spec §6.6.3 says every entity audit surface consumes the same
 *     API and shares the same render rules (newest-first, expandable
 *     before/after diff).  Centralising that contract here keeps WO
 *     and NCR audit views textually identical so a future reviewer
 *     only needs to read one implementation.
 *   - The component is a read-only observability surface.  It never
 *     mutates state — clicking an event expands the row; there is no
 *     action that the audit surface itself triggers.
 *
 * Authorization contract (backend-enforced):
 *   - When both `entityType` and `entityId` are passed, the service
 *     fires the entity-scoped path: caller needs the entity-type's
 *     own read permission (wo:read / ncr:read / op:read) plus the
 *     entity's program scope.  Cross-program resolves to 404 per the
 *     §3.2 existence-leak rule — this component treats 404 as an
 *     empty state rather than surfacing "not found" on a page the
 *     user already loaded.
 *   - Callers without the required permission see the 403 surfaced
 *     as an inline error; the rest of the page keeps rendering.
 *
 * What the expanded row shows:
 *   - The raw `action` code (spec canonical name, e.g.
 *     `work_order.released`) so power users can reconcile the UI
 *     view with the audit chain.
 *   - A structured before/after field list — NOT a raw JSON dump.
 *     We enumerate only the keys that changed, showing `old → new`
 *     per key.  Keys that are null on both sides are skipped.
 *     If the audit row has a `before_state` but no `after_state` (or
 *     vice versa), we render the single side verbatim.
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchAuditEvents } from '@/api/endpoints';
import { LoadingSkeleton } from './LoadingSkeleton';
import { ErrorBanner } from './ErrorBanner';
import type { AuditEventOut } from '@/types/api';

interface AuditTrailProps {
  entityType: 'work_order' | 'ncr' | 'operation';
  entityId: string;
  /** Page size — defaults to 50 per spec §6.6.3 client rendering. */
  pageSize?: number;
}

/**
 * Humanize a raw audit action code for UI display.  We keep the raw
 * code visible in the expanded row so power users can trace the UI
 * label back to the audit chain.
 *
 * Rule: the suffix after the last `.` is the "verb".  We capitalize
 * and strip underscores.  Verb-specific overrides map a few cases
 * where the humanized form would be awkward otherwise.  If spec
 * §6.6.1 canonical naming drifts from the actually-written action
 * names (e.g. `ncr.close` vs spec `ncr.closed`), this mapping keeps
 * the UI readable without renaming the underlying audit events —
 * per the prompt's "preserve the actual event naming" constraint.
 */
/* The `.in_progress` suffix is emitted by (a) `work_order.in_progress`
   when the first op starts, (b) `operation.in_progress` whenever an op
   enters in_progress (first start, rework start, or hold resume).  We
   intentionally DO NOT override it to "Resumed" because that would
   mislabel the first-start and rework-start paths.  The default
   humanizer produces "In Progress", which is truthful for every
   origin — the expanded row still shows the raw action code so a
   reviewer can disambiguate. */
const VERB_LABEL_OVERRIDES: Record<string, string> = {
  close: 'Closed',
  closed: 'Closed',
  create: 'Created',
  created: 'Created',
  complete: 'Completed',
  completed: 'Completed',
  disposition: 'Dispositioned',
  dispositioned: 'Dispositioned',
  hold: 'Held',
  held: 'Held',
  record: 'Recorded',
  recorded: 'Recorded',
  reactivated: 'Reactivated',
  released: 'Released',
  reopened: 'Reopened',
  status_changed: 'Status Changed',
  scrap_linkage_confirmed: 'Scrap Linkage Confirmed',
  evidence_updated: 'Evidence Updated',
};

function humanizeAction(action: string): string {
  const parts = action.split('.');
  const verb = parts[parts.length - 1];
  return VERB_LABEL_OVERRIDES[verb] ?? verb
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

/**
 * Build a structured before/after diff.  Returns the list of
 * `{key, before, after}` entries for keys that differ between the
 * two dicts (or exist in only one).  Keys that are null/absent in
 * both sides are skipped so the expanded view stays focused on what
 * actually changed.
 */
function diffStates(
  before: Record<string, unknown> | null | undefined,
  after: Record<string, unknown> | null | undefined,
): Array<{ key: string; before: unknown; after: unknown }> {
  const keys = new Set<string>([
    ...Object.keys(before ?? {}),
    ...Object.keys(after ?? {}),
  ]);
  const entries: Array<{ key: string; before: unknown; after: unknown }> = [];
  for (const key of Array.from(keys).sort()) {
    const b = before?.[key] ?? null;
    const a = after?.[key] ?? null;
    if (b === null && a === null) continue;
    if (JSON.stringify(b) === JSON.stringify(a)) continue;
    entries.push({ key, before: b, after: a });
  }
  return entries;
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
}

export function AuditTrail({
  entityType, entityId, pageSize = 50,
}: AuditTrailProps) {
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const query = useQuery({
    queryKey: ['audit-events', entityType, entityId, page, pageSize],
    queryFn: () => fetchAuditEvents({
      entity_type: entityType,
      entity_id: entityId,
      page,
      page_size: pageSize,
    }),
    enabled: !!entityId,
  });

  const toggleExpanded = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const items: AuditEventOut[] = useMemo(
    () => query.data?.items ?? [],
    [query.data],
  );
  const total = query.data?.total ?? 0;
  const pages = query.data?.pages ?? 0;

  if (query.isLoading) {
    return (
      <div style={{ padding: 'var(--space-md)' }}>
        <LoadingSkeleton rows={4} />
      </div>
    );
  }

  if (query.isError) {
    const err = query.error as {
      response?: { status?: number; data?: { detail?: string } };
    };
    const status = err?.response?.status;
    /* 404 = cross-program or not-found.  Rather than shouting, we
       show an empty-state since the caller already has the page
       they requested rendered.  403 = missing permission — surface
       as an inline error so the operator knows why they can't see
       the trail. */
    if (status === 404) {
      return (
        <div
          style={{
            padding: 'var(--space-md)',
            color: 'var(--text-secondary)',
            fontStyle: 'italic',
          }}
        >
          No audit events available for this entity.
        </div>
      );
    }
    return (
      <div style={{ padding: 'var(--space-md)' }}>
        <ErrorBanner
          message={
            status === 403
              ? 'You are not authorized to view this audit trail.'
              : 'Failed to load audit events.'
          }
        />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div
        style={{
          padding: 'var(--space-md)',
          color: 'var(--text-secondary)',
          fontStyle: 'italic',
        }}
        data-testid="audit-trail-empty"
      >
        No audit events recorded yet.
      </div>
    );
  }

  return (
    <div data-testid="audit-trail">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 'var(--space-sm)',
          color: 'var(--text-secondary)',
          fontSize: 'var(--font-size-caption)',
        }}
      >
        <span>
          {total} event{total === 1 ? '' : 's'}
        </span>
        {pages > 1 && (
          <span>
            Page {page} of {pages}
          </span>
        )}
      </div>

      <div
        style={{
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-md)',
          overflow: 'hidden',
        }}
      >
        {items.map((evt, i) => {
          const isOpen = expanded.has(evt.audit_id);
          const diff = diffStates(evt.before_state, evt.after_state);
          return (
            <div
              key={evt.audit_id}
              data-testid="audit-trail-row"
              style={{
                borderBottom: i === items.length - 1
                  ? 'none'
                  : '1px solid var(--border)',
              }}
            >
              <button
                type="button"
                onClick={() => toggleExpanded(evt.audit_id)}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr auto auto',
                  alignItems: 'center',
                  gap: 'var(--space-md)',
                  width: '100%',
                  padding: 'var(--space-sm) var(--space-md)',
                  background: 'transparent',
                  border: 'none',
                  textAlign: 'left',
                  cursor: 'pointer',
                  color: 'var(--text-primary)',
                  fontSize: 'var(--font-size-body)',
                }}
              >
                <span style={{ fontWeight: 500 }}>
                  {humanizeAction(evt.action)}
                </span>
                <span style={{ color: 'var(--text-secondary)' }}>
                  {evt.actor.display_name ?? 'SYSTEM'}
                  {evt.actor.role_at_time_of_event && (
                    <span style={{ color: 'var(--text-muted)' }}>
                      {' '}({evt.actor.role_at_time_of_event})
                    </span>
                  )}
                </span>
                <span
                  style={{
                    color: 'var(--text-muted)',
                    fontSize: 'var(--font-size-caption)',
                  }}
                >
                  {formatTimestamp(evt.timestamp)}
                </span>
                <span
                  style={{
                    color: 'var(--text-muted)',
                    fontSize: 'var(--font-size-caption)',
                  }}
                >
                  {isOpen ? '▾' : '▸'}
                </span>
              </button>

              {isOpen && (
                <div
                  data-testid="audit-trail-expanded"
                  style={{
                    padding: 'var(--space-sm) var(--space-md) var(--space-md)',
                    background: 'var(--bg-elevated)',
                    fontSize: 'var(--font-size-caption)',
                    color: 'var(--text-secondary)',
                  }}
                >
                  <div style={{ marginBottom: 'var(--space-xs)' }}>
                    <span style={{ color: 'var(--text-muted)' }}>Action:</span>{' '}
                    <code style={{ color: 'var(--text-primary)' }}>
                      {evt.action}
                    </code>
                  </div>
                  {diff.length === 0 ? (
                    <div style={{ fontStyle: 'italic' }}>
                      No field-level changes recorded.
                    </div>
                  ) : (
                    <table
                      style={{
                        width: '100%',
                        borderCollapse: 'collapse',
                      }}
                    >
                      <thead>
                        <tr>
                          <th
                            style={{
                              textAlign: 'left',
                              padding: 'var(--space-xs) var(--space-sm)',
                              color: 'var(--text-muted)',
                              fontWeight: 600,
                            }}
                          >
                            Field
                          </th>
                          <th
                            style={{
                              textAlign: 'left',
                              padding: 'var(--space-xs) var(--space-sm)',
                              color: 'var(--text-muted)',
                              fontWeight: 600,
                            }}
                          >
                            Before
                          </th>
                          <th
                            style={{
                              textAlign: 'left',
                              padding: 'var(--space-xs) var(--space-sm)',
                              color: 'var(--text-muted)',
                              fontWeight: 600,
                            }}
                          >
                            After
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {diff.map((row) => (
                          <tr key={row.key}>
                            <td
                              style={{
                                padding: 'var(--space-xs) var(--space-sm)',
                                color: 'var(--text-primary)',
                              }}
                            >
                              {row.key}
                            </td>
                            <td
                              style={{
                                padding: 'var(--space-xs) var(--space-sm)',
                                color: 'var(--text-secondary)',
                              }}
                            >
                              {formatValue(row.before)}
                            </td>
                            <td
                              style={{
                                padding: 'var(--space-xs) var(--space-sm)',
                                color: 'var(--text-primary)',
                              }}
                            >
                              {formatValue(row.after)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {pages > 1 && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 'var(--space-sm)',
            marginTop: 'var(--space-sm)',
          }}
        >
          <button
            type="button"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => setPage((p) => Math.min(pages, p + 1))}
            disabled={page >= pages}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
