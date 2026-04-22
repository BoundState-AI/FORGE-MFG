import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkOrderDetail from '@/pages/WorkOrderDetail';

const fetchWorkOrderMock = vi.fn();
const createOperationMock = vi.fn();

vi.mock('@/api/endpoints', () => ({
  fetchWorkOrder: (...args: unknown[]) => fetchWorkOrderMock(...args),
  createOperation: (...args: unknown[]) => createOperationMock(...args),
  releaseWorkOrder: vi.fn(),
  recallWorkOrder: vi.fn(),
  holdWorkOrder: vi.fn(),
  resumeWorkOrder: vi.fn(),
  completeWorkOrder: vi.fn(),
}));

vi.mock('@/auth/store', () => ({
  useAuthStore: vi.fn((selector) =>
    selector({
      activeProgram: { id: 'prog-1', code: 'STAGING', name: 'Staging' },
      hasRole: (role: string) => role === 'engineer',
      hasAnyRole: (...roles: string[]) =>
        roles.includes('engineer') || roles.includes('qa_manager'),
    }),
  ),
}));

vi.mock('@/components/CloseWorkOrderPanel', () => ({
  CloseWorkOrderPanel: () => null,
}));

vi.mock('@/components/AuditTrail', () => ({
  AuditTrail: () => null,
}));

vi.mock('@/components/ConflictModal', () => ({
  ConflictModal: () => null,
}));

vi.mock('@/components/ConfirmationDialog', () => ({
  ConfirmationDialog: ({ children }: { children?: ReactNode }) => (
    <div>{children}</div>
  ),
}));

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/work-orders/wo-1']}>
        <Routes>
          <Route path="/work-orders/:id" element={<WorkOrderDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('WorkOrderDetail operations authoring', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchWorkOrderMock.mockResolvedValue({
      id: 'wo-1',
      wo_number: 'WO-1001',
      status: 'draft',
      version: 1,
      priority: 'routine',
      engine_serial: 'ES-1',
      module: 'UVA',
      build_phase: 'fabrication',
      released_at: null,
      created_at: '2026-04-22T12:00:00Z',
      closure_rationale: null,
      operations: [],
    });
    createOperationMock.mockResolvedValue({
      id: 'op-1',
      sequence: 1,
      description: 'Initial teardown',
    });
  });

  it('shows an add operation form for draft work orders and posts the payload', async () => {
    renderPage();

    await screen.findByText('WO-1001');
    expect(screen.getByRole('heading', { name: 'Add Operation' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Description'), {
      target: { value: 'Initial teardown' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add Operation' }));

    await waitFor(() =>
      expect(createOperationMock).toHaveBeenCalledWith('wo-1', {
        description: 'Initial teardown',
        sequence: 1,
        tooling_required: [],
        inspection_hold: false,
        safety_critical: false,
        sign_off_roles_required: [],
        estimated_hours: 0,
        constraint_type: 'none',
        constraint_date: null,
      }),
    );
  });
});
