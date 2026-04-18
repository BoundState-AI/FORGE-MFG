/**
 * Module:   AuditTrail Component Tests
 * Purpose:  Pin the entity-scoped audit surface behavior — event
 *           rendering, action humanization, structured before/after
 *           diff, and honest handling of 403/404 from the entity-
 *           scoped backend path (UX spec §6.6.3).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 */

import {
  describe,
  it,
  expect,
  vi,
  beforeEach,
  afterEach,
  type Mock,
} from 'vitest';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from '@testing-library/react';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';

vi.mock('@/api/endpoints', () => ({
  fetchAuditEvents: vi.fn(),
}));

import { fetchAuditEvents } from '@/api/endpoints';
import { AuditTrail } from '@/components/AuditTrail';
import type { AuditEventOut } from '@/types/api';

const fetchMock = fetchAuditEvents as unknown as Mock;

function buildEvent(overrides: Partial<AuditEventOut> = {}): AuditEventOut {
  return {
    audit_id: 'evt-1',
    sequence_number: 1,
    event_id: 'event-1',
    timestamp: '2026-04-18T12:00:00Z',
    actor: {
      user_id: 'u1',
      display_name: 'J. Smith',
      role_at_time_of_event: 'engineer',
    },
    action: 'work_order.released',
    entity_type: 'work_order',
    entity_id: 'wo-1',
    before_state: { status: 'draft' },
    after_state: { status: 'released' },
    integrity_hash: 'sha256:abc',
    ...overrides,
  } as AuditEventOut;
}

function renderTrail(
  entityType: 'work_order' | 'ncr' = 'work_order',
  entityId = 'wo-1',
): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <AuditTrail entityType={entityType} entityId={entityId} />
    </QueryClientProvider>,
  );
}

describe('AuditTrail — event rendering', () => {
  beforeEach(() => {
    fetchMock.mockReset();
  });
  afterEach(() => cleanup());

  it('renders a row per event with humanized action + actor', async () => {
    fetchMock.mockResolvedValue({
      items: [
        buildEvent({
          action: 'work_order.closed',
          actor: {
            user_id: 'u1',
            display_name: 'A. QA',
            role_at_time_of_event: 'qa_manager',
          },
        }),
        buildEvent({
          audit_id: 'evt-2',
          action: 'work_order.released',
        }),
      ],
      total: 2,
      page: 1,
      page_size: 50,
      pages: 1,
    });

    renderTrail();

    await waitFor(() => {
      expect(screen.getAllByTestId('audit-trail-row')).toHaveLength(2);
    });
    /* Humanization: work_order.closed → "Closed"; work_order.released
       → "Released". */
    expect(screen.getByText('Closed')).toBeInTheDocument();
    expect(screen.getByText('Released')).toBeInTheDocument();
    /* Actor display_name visible with role in parens. */
    expect(screen.getByText(/A\. QA/)).toBeInTheDocument();
    expect(screen.getByText(/\(qa_manager\)/)).toBeInTheDocument();
  });

  it('humanizes action codes with dot-segmented verbs', async () => {
    fetchMock.mockResolvedValue({
      items: [
        buildEvent({
          audit_id: 'a', action: 'ncr.close',
          entity_type: 'ncr', entity_id: 'ncr-1',
        }),
        buildEvent({
          audit_id: 'b', action: 'ncr.disposition',
          entity_type: 'ncr', entity_id: 'ncr-1',
        }),
        buildEvent({
          audit_id: 'c', action: 'ncr.create',
          entity_type: 'ncr', entity_id: 'ncr-1',
        }),
      ],
      total: 3, page: 1, page_size: 50, pages: 1,
    });

    renderTrail('ncr', 'ncr-1');

    await waitFor(() => {
      expect(screen.getAllByTestId('audit-trail-row')).toHaveLength(3);
    });
    /* `ncr.close` is preserved as a raw action but humanized to
       "Closed" — pins the GC-4 naming preservation contract.
       `ncr.disposition` → "Dispositioned", `ncr.create` → "Created". */
    expect(screen.getByText('Closed')).toBeInTheDocument();
    expect(screen.getByText('Dispositioned')).toBeInTheDocument();
    expect(screen.getByText('Created')).toBeInTheDocument();
  });

  it('expands row to show raw action + structured before/after diff', async () => {
    fetchMock.mockResolvedValue({
      items: [
        buildEvent({
          action: 'work_order.closed',
          before_state: { status: 'complete', closure_rationale: null },
          after_state: {
            status: 'closed',
            closure_rationale: 'QA attested.',
          },
        }),
      ],
      total: 1, page: 1, page_size: 50, pages: 1,
    });

    renderTrail();

    await waitFor(() => {
      expect(screen.getByTestId('audit-trail-row')).toBeInTheDocument();
    });
    /* Row is collapsed by default. */
    expect(screen.queryByTestId('audit-trail-expanded')).toBeNull();

    /* Click to expand. */
    fireEvent.click(screen.getByRole('button', { name: /Closed/ }));

    const expanded = await screen.findByTestId('audit-trail-expanded');
    /* Raw action code visible for power users. */
    expect(expanded).toHaveTextContent('work_order.closed');
    /* Status diff rendered as a structured row. */
    expect(expanded).toHaveTextContent(/status/);
    expect(expanded).toHaveTextContent(/complete/);
    expect(expanded).toHaveTextContent(/closed/);
    /* closure_rationale: null → "QA attested." is a real change;
       it should render (null shown as "—"). */
    expect(expanded).toHaveTextContent(/closure_rationale/);
    expect(expanded).toHaveTextContent(/QA attested/);
  });

  it('skips field rows that did not change', async () => {
    fetchMock.mockResolvedValue({
      items: [
        buildEvent({
          before_state: { status: 'draft', priority: 'routine' },
          after_state: { status: 'released', priority: 'routine' },
        }),
      ],
      total: 1, page: 1, page_size: 50, pages: 1,
    });

    renderTrail();
    await waitFor(() =>
      expect(screen.getByTestId('audit-trail-row')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getAllByRole('button')[0]);
    const expanded = await screen.findByTestId('audit-trail-expanded');
    expect(expanded).toHaveTextContent(/status/);
    /* `priority` is unchanged across before/after → not rendered. */
    expect(expanded).not.toHaveTextContent(/priority/);
  });
});

describe('AuditTrail — empty + error states', () => {
  beforeEach(() => {
    fetchMock.mockReset();
  });
  afterEach(() => cleanup());

  it('renders empty state when zero events', async () => {
    fetchMock.mockResolvedValue({
      items: [], total: 0, page: 1, page_size: 50, pages: 0,
    });
    renderTrail();
    expect(
      await screen.findByTestId('audit-trail-empty'),
    ).toBeInTheDocument();
  });

  it('renders "no events available" on 404 (cross-program)', async () => {
    /* Backend's §3.2 existence-leak rule: cross-program returns 404.
       The audit trail must treat that as an empty state, not as a
       blocking error — the user already has the entity page loaded,
       so shouting 404 here would contradict the page they're on.  */
    fetchMock.mockRejectedValue({
      response: { status: 404, data: { detail: 'Work order not found' } },
    });
    renderTrail();
    await waitFor(() => {
      expect(screen.getByText(/No audit events available/i))
        .toBeInTheDocument();
    });
  });

  it('renders explicit authorization error on 403', async () => {
    fetchMock.mockRejectedValue({
      response: {
        status: 403,
        data: { detail: 'Audit read of work_order requires `wo:read`.' },
      },
    });
    renderTrail();
    await waitFor(() => {
      expect(
        screen.getByText(/not authorized/i),
      ).toBeInTheDocument();
    });
  });
});
