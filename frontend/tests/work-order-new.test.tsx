import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkOrderNew from '@/pages/WorkOrderNew';

const createWorkOrderMock = vi.fn();
const navigateMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('@/api/endpoints', () => ({
  createWorkOrder: (...args: unknown[]) => createWorkOrderMock(...args),
}));

vi.mock('@/auth/store', () => ({
  useAuthStore: vi.fn((selector) =>
    selector({
      user: {
        program_memberships: [
          {
            id: 'prog-1',
            code: 'STAGING',
            name: 'Staging',
          },
        ],
      },
      activeProgram: {
        id: 'prog-1',
        code: 'STAGING',
        name: 'Staging',
      },
      hasRole: (role: string) => role === 'engineer',
    }),
  ),
}));

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WorkOrderNew />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('WorkOrderNew', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('submits the backend WorkOrderCreate payload and navigates to detail', async () => {
    createWorkOrderMock.mockResolvedValue({ id: 'wo-123' });

    renderPage();

    fireEvent.change(screen.getByLabelText('Engine Serial'), {
      target: { value: 'ES-42' },
    });
    fireEvent.change(screen.getByLabelText('Module'), {
      target: { value: 'Powerhead' },
    });
    fireEvent.change(screen.getByLabelText('Build Phase'), {
      target: { value: 'assembly' },
    });
    fireEvent.change(screen.getByLabelText('Priority'), {
      target: { value: 'urgent' },
    });
    fireEvent.change(screen.getByLabelText('BOM Revision Ref'), {
      target: { value: 'BOM-7' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Create Work Order' }));

    await waitFor(() =>
      expect(createWorkOrderMock).toHaveBeenCalledWith({
        program_id: 'prog-1',
        engine_serial: 'ES-42',
        module: 'Powerhead',
        build_phase: 'assembly',
        priority: 'urgent',
        bom_revision_ref: 'BOM-7',
      }),
    );

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith('/work-orders/wo-123'),
    );
  });
});
