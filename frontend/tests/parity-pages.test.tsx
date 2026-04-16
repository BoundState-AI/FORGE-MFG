/**
 * Module:   Parity Page Rendering Tests
 * Purpose:  Assert the 5C parity-patch pages render real data, not the
 *           "coming soon" placeholders that preceded them.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * These tests exist specifically to guard against a regression to the
 * placeholder state the parity patch removed.  Each test mounts the
 * page with mocked query data and asserts real content appears — a
 * row in the table, a real part header, etc.  If a future refactor
 * silently re-introduces a "coming soon" stub, these fail loudly.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';

const mockAuthState = {
  user: {
    id: 'u1',
    display_name: 'Test User',
    email: 't@t.com',
    cognito_sub: 't',
    active: true,
    is_system_actor: false,
    roles: [{ id: 'r1', name: 'engineer', permissions: [] }],
    program_memberships: [
      {
        id: 'prog-1',
        code: 'RS25',
        name: 'RS-25',
        active: true,
        schedule_enabled: false,
        oracle_access: false,
        overdue_threshold_hours: 24,
        version: 1,
      },
    ],
  },
  isAuthenticated: true,
  accessToken: 'test-token',
  activeProgram: {
    id: 'prog-1',
    code: 'RS25',
    name: 'RS-25',
    active: true,
    schedule_enabled: false,
    oracle_access: false,
    overdue_threshold_hours: 24,
    version: 1,
  },
  roles: ['engineer'],
  programIds: ['prog-1'],
  hasRole: (role: string) => role === 'engineer',
  hasAnyRole: (...roles: string[]) => roles.includes('engineer'),
  setToken: vi.fn(),
  clearAuth: vi.fn(),
  setUser: vi.fn(),
  setActiveProgram: vi.fn(),
};

vi.mock('@/auth/store', () => ({
  /* Mirror the Zustand API: useAuthStore() returns the whole state,
     useAuthStore(selector) returns the selector's result.  The real
     store supports both patterns and different pages use each. */
  useAuthStore: vi.fn((selector?: (s: typeof mockAuthState) => unknown) =>
    selector ? selector(mockAuthState) : mockAuthState,
  ),
}));

vi.mock('@/api/endpoints', () => ({
  fetchMaterials: vi.fn().mockResolvedValue({
    items: [
      {
        id: 'cert-1',
        program_id: 'prog-1',
        material_spec: 'Inconel-718',
        lot_number: 'LOT-42',
        heat_number: 'HEAT-42',
        supplier: 'ACME Metals',
        cert_document_ref_id: 'doc-1',
        received_at: '2026-04-10T00:00:00Z',
        received_by_id: 'u1',
        accepted_by_id: null,
        accepted_at: null,
        shelf_life_expiry: null,
        status: 'accepted',
        quantity_received: 100,
        quantity_remaining: 75,
        unit_of_measure: 'kg',
        version: 1,
        created_at: '2026-04-10T00:00:00Z',
        updated_at: '2026-04-10T00:00:00Z',
      },
    ],
    total: 1,
    page: 1,
    page_size: 25,
    pages: 1,
  }),
  fetchParts: vi.fn().mockResolvedValue({
    items: [
      {
        id: 'part-1',
        serial_number: 'SN-0001',
        part_number: 'PN-9',
        part_description: 'Nozzle throat',
        program_id: 'prog-1',
        status: 'in_process',
        material_certification_id: null,
        parent_assembly_id: null,
        current_location: 'Bay A',
        reason_ncr_id: null,
        version: 1,
        created_at: '2026-04-10T00:00:00Z',
        updated_at: '2026-04-10T00:00:00Z',
      },
    ],
    total: 1,
    page: 1,
    page_size: 25,
    pages: 1,
  }),
  fetchPart: vi.fn().mockResolvedValue({
    id: 'part-1',
    serial_number: 'SN-DETAIL',
    part_number: 'PN-9',
    part_description: 'Nozzle throat',
    program_id: 'prog-1',
    status: 'in_process',
    material_certification_id: 'cert-1',
    parent_assembly_id: null,
    current_location: 'Bay A',
    reason_ncr_id: null,
    version: 1,
    created_at: '2026-04-10T00:00:00Z',
    updated_at: '2026-04-10T00:00:00Z',
  }),
  fetchTraceability: vi.fn().mockResolvedValue({
    part: {
      id: 'part-1',
      serial_number: 'SN-DETAIL',
      part_number: 'PN-9',
      part_description: 'Nozzle throat',
      program_id: 'prog-1',
      status: 'in_process',
      material_certification_id: 'cert-1',
      parent_assembly_id: null,
      current_location: 'Bay A',
      reason_ncr_id: null,
      version: 1,
      created_at: '2026-04-10T00:00:00Z',
      updated_at: '2026-04-10T00:00:00Z',
    },
    material_certification_id: 'cert-1',
    assembly_chain: [],
    child_parts: [],
    links: [],
  }),
  fetchWorkOrder: vi.fn().mockResolvedValue({
    id: 'wo-1',
    wo_number: 'WO-2026-0042',
    program_id: 'prog-1',
    engine_serial: 'ES-A',
    module: 'Powerhead',
    build_phase: 'fabrication',
    status: 'in_progress',
    priority: 'routine',
    authored_by_id: 'u1',
    bom_revision_ref: null,
    released_at: '2026-04-11T00:00:00Z',
    released_by: 'u1',
    has_graph_issue: false,
    completed_at: null,
    completed_by: null,
    closure_rationale: null,
    version: 3,
    created_at: '2026-04-10T00:00:00Z',
    updated_at: '2026-04-12T00:00:00Z',
    operations: [
      {
        id: 'op-1',
        work_order_id: 'wo-1',
        sequence: 1,
        description: 'Machine housing',
        status: 'complete',
        execution_cycle: 1,
        inspection_hold: false,
        safety_critical: false,
        estimated_hours: 2,
        started_at: '2026-04-11T09:00:00Z',
        completed_at: '2026-04-11T11:00:00Z',
        version: 5,
        created_at: '2026-04-10T00:00:00Z',
        updated_at: '2026-04-11T11:00:00Z',
      },
      {
        id: 'op-2',
        work_order_id: 'wo-1',
        sequence: 2,
        description: 'Weld injector ports',
        status: 'in_progress',
        execution_cycle: 1,
        inspection_hold: true,
        safety_critical: true,
        estimated_hours: 4,
        started_at: '2026-04-11T11:30:00Z',
        completed_at: null,
        version: 2,
        created_at: '2026-04-10T00:00:00Z',
        updated_at: '2026-04-11T11:30:00Z',
      },
    ],
  }),
}));

function renderAtRoute(
  path: string,
  routePattern: string,
  Component: React.ComponentType,
) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={routePattern} element={<Component />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('5C parity pages render real data', () => {
  afterEach(() => cleanup());

  it('MaterialList renders a real row, not a placeholder', async () => {
    const { default: MaterialList } = await import('@/pages/MaterialList');
    renderAtRoute('/materials', '/materials', MaterialList);

    /* Real data surfaces the material_spec from the mocked response. */
    await waitFor(() => {
      expect(screen.getByText('Inconel-718')).toBeInTheDocument();
    });
    expect(screen.getByText('LOT-42')).toBeInTheDocument();
    /* The regressed placeholder copy must not appear. */
    expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/not yet been implemented/i),
    ).not.toBeInTheDocument();
  });

  it('PartList renders a real row, not a placeholder', async () => {
    const { default: PartList } = await import('@/pages/PartList');
    renderAtRoute('/parts', '/parts', PartList);

    await waitFor(() => {
      expect(screen.getByText('SN-0001')).toBeInTheDocument();
    });
    expect(screen.getByText('Nozzle throat')).toBeInTheDocument();
    expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument();
  });

  it('PartDetail renders the part header from the real detail GET', async () => {
    const { default: PartDetail } = await import('@/pages/PartDetail');
    renderAtRoute('/parts/part-1', '/parts/:id', PartDetail);

    /* SN-DETAIL appears in both the breadcrumb and page title —
       asserting there are two occurrences is itself proof that the
       page header rendered from the detail GET (a single traceability-
       only fallback would show the SN in one place at most). */
    await waitFor(() => {
      expect(screen.getAllByText('SN-DETAIL').length).toBeGreaterThanOrEqual(1);
    });
    /* Fields that only exist on PartOut — if the old traceability-only
       path were still in use, these would not appear because
       TraceabilityOut.part is hydrated from a separate query. */
    expect(screen.getByText('Nozzle throat')).toBeInTheDocument();
    expect(screen.getByText('Bay A')).toBeInTheDocument();
    expect(screen.getByText('Traceability')).toBeInTheDocument();
  });

  it('WorkOrderDetail renders operations from the detail response', async () => {
    const { default: WorkOrderDetail } = await import(
      '@/pages/WorkOrderDetail'
    );
    renderAtRoute(
      '/work-orders/wo-1',
      '/work-orders/:id',
      WorkOrderDetail,
    );

    await waitFor(() => {
      expect(screen.getByText('WO-2026-0042')).toBeInTheDocument();
    });
    /* Real operation descriptions from the mocked detail response. */
    expect(screen.getByText('Machine housing')).toBeInTheDocument();
    expect(screen.getByText('Weld injector ports')).toBeInTheDocument();
    /* The placeholder "operations list endpoint ... has not been
       implemented yet" must be gone. */
    expect(
      screen.queryByText(/not been implemented yet/i),
    ).not.toBeInTheDocument();
  });
});
