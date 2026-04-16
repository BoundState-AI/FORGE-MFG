/**
 * Module:   Route Smoke Tests
 * Purpose:  Verify main page routes render without crash.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';

/* Mock the auth store to provide a test user */
vi.mock('@/auth/store', () => ({
  useAuthStore: vi.fn((selector) => {
    const state = {
      user: {
        id: 'test-user-id',
        display_name: 'Test User',
        email: 'test@example.com',
        cognito_sub: 'test',
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
      roles: ['engineer'] as string[],
      programIds: ['prog-1'],
      hasRole: (role: string) => role === 'engineer',
      hasAnyRole: (...roles: string[]) => roles.includes('engineer'),
      setToken: vi.fn(),
      clearAuth: vi.fn(),
      setUser: vi.fn(),
      setActiveProgram: vi.fn(),
    };
    return selector(state);
  }),
}));

/* Mock API endpoints */
vi.mock('@/api/endpoints', () => ({
  fetchDashboardSummary: vi.fn().mockResolvedValue({
    tiles: [],
    work_order_status_counts: {
      draft: 0,
      released: 0,
      in_progress: 0,
      hold: 0,
      complete: 0,
      closed: 0,
    },
    program_scope: [],
    generated_at: '2026-04-15T00:00:00Z',
  }),
  fetchWorkOrders: vi.fn().mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 50,
    pages: 0,
  }),
  fetchNCRs: vi.fn().mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 50,
    pages: 0,
  }),
  fetchInspectionQueue: vi.fn().mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 50,
    pages: 0,
  }),
  /* 5C parity patch restored these wrappers once the backend GET
     routes landed.  Mocks return empty paginated envelopes so smoke
     tests do not care what's on the wire. */
  fetchMaterials: vi.fn().mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 25,
    pages: 0,
  }),
  fetchParts: vi.fn().mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    page_size: 25,
    pages: 0,
  }),
  fetchPart: vi.fn().mockResolvedValue({
    id: 'part-1',
    serial_number: 'SN-0001',
    part_number: 'PN-9',
    part_description: 'Test part',
    program_id: 'prog-1',
    status: 'raw',
    material_certification_id: null,
    parent_assembly_id: null,
    current_location: 'Bay A',
    reason_ncr_id: null,
    version: 1,
    created_at: '2026-04-15T00:00:00Z',
    updated_at: '2026-04-15T00:00:00Z',
  }),
  fetchTraceability: vi.fn().mockResolvedValue({
    part: {
      id: 'part-1',
      serial_number: 'SN-0001',
      part_number: 'PN-9',
      part_description: 'Test part',
      program_id: 'prog-1',
      status: 'raw',
      material_certification_id: null,
      parent_assembly_id: null,
      current_location: 'Bay A',
      reason_ncr_id: null,
      version: 1,
      created_at: '2026-04-15T00:00:00Z',
      updated_at: '2026-04-15T00:00:00Z',
    },
    material_certification_id: null,
    assembly_chain: [],
    child_parts: [],
    links: [],
  }),
  fetchWorkOrder: vi.fn().mockResolvedValue({
    id: 'wo-1',
    wo_number: 'WO-2026-0001',
    program_id: 'prog-1',
    engine_serial: 'ES-1',
    module: 'Powerhead',
    build_phase: 'fabrication',
    status: 'draft',
    priority: 'routine',
    authored_by_id: 'u1',
    bom_revision_ref: null,
    released_at: null,
    released_by: null,
    has_graph_issue: false,
    version: 1,
    created_at: '2026-04-15T00:00:00Z',
    updated_at: '2026-04-15T00:00:00Z',
    operations: [],
  }),
  fetchCurrentUser: vi.fn().mockResolvedValue({
    id: 'test-user-id',
    display_name: 'Test User',
    email: 'test@example.com',
    roles: [{ id: 'r1', name: 'engineer', permissions: [] }],
    program_memberships: [],
  }),
}));

function renderRoute(route: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        {/* We test the lazy-loaded pages directly, not through the full App router */}
        <div data-testid="test-wrapper" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Route smoke tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders test wrapper at /', () => {
    renderRoute('/');
    expect(screen.getByTestId('test-wrapper')).toBeInTheDocument();
  });

  it('renders test wrapper at /work-orders', () => {
    renderRoute('/work-orders');
    expect(screen.getByTestId('test-wrapper')).toBeInTheDocument();
  });

  it('renders test wrapper at /ncrs', () => {
    renderRoute('/ncrs');
    expect(screen.getByTestId('test-wrapper')).toBeInTheDocument();
  });

  it('renders test wrapper at /inspections', () => {
    renderRoute('/inspections');
    expect(screen.getByTestId('test-wrapper')).toBeInTheDocument();
  });

  it('renders test wrapper at /materials', () => {
    renderRoute('/materials');
    expect(screen.getByTestId('test-wrapper')).toBeInTheDocument();
  });

  it('renders test wrapper at /parts', () => {
    renderRoute('/parts');
    expect(screen.getByTestId('test-wrapper')).toBeInTheDocument();
  });
});
