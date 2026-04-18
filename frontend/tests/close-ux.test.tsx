/**
 * Module:   GC-4 Close UX Tests
 * Purpose:  Pin the CloseWorkOrderPanel + NCR close section behavior
 *           across the three governance states:
 *             A. closure_enabled=false → disabled banner (all readers)
 *             B. closure_enabled=true + qa_manager + blockers → disabled
 *                button with blocker list
 *             C. closure_enabled=true + qa_manager + ready → enabled
 *                button → confirmation dialog → mutate
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * These tests pin the frontend's end of the GC-4 closure-as-attestation
 * contract.  Backend-side rules (CLOSURE_DISABLED 403, blocker 422,
 * rationale persistence) are covered in
 * tests/integration/test_closure_gate.py.
 */

import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';

/* ── Auth store mock ──
   The store fields the panels read are all on this object; individual
   tests mutate `roles`, `closureEnabled`, and `hasRole` to exercise
   the three render paths. */
const mockAuthState = {
  user: null,
  isAuthenticated: true,
  accessToken: 'test-token',
  activeProgram: null,
  roles: ['qa_manager'] as string[],
  programIds: [],
  closureEnabled: true,
  hasRole: (role: string) => mockAuthState.roles.includes(role),
  hasAnyRole: (...roles: string[]) =>
    roles.some((r) => mockAuthState.roles.includes(r)),
  setToken: vi.fn(),
  clearAuth: vi.fn(),
  setUser: vi.fn(),
  setActiveProgram: vi.fn(),
};

vi.mock('@/auth/store', () => ({
  useAuthStore: vi.fn(
    (selector?: (s: typeof mockAuthState) => unknown) =>
      selector ? selector(mockAuthState) : mockAuthState,
  ),
}));

vi.mock('@/api/endpoints', () => ({
  closeWorkOrder: vi.fn(),
  closeNCR: vi.fn(),
}));

import { closeWorkOrder, closeNCR } from '@/api/endpoints';
import { CloseWorkOrderPanel } from '@/components/CloseWorkOrderPanel';
import { NCRCloseSection } from '@/pages/NCRDetail';
import type { WorkOrderDetailOut } from '@/types/api';

const closeWOMock = closeWorkOrder as unknown as Mock;
const closeNCRMock = closeNCR as unknown as Mock;

/* ── Helpers ── */

function buildWO(overrides: Partial<WorkOrderDetailOut> = {}): WorkOrderDetailOut {
  return {
    id: 'wo-1',
    wo_number: 'WO-2026-0001',
    program_id: 'prog-1',
    engine_serial: 'E-UNIT-001',
    module: 'Powerhead',
    build_phase: 'fabrication',
    status: 'complete',
    priority: 'routine',
    authored_by_id: 'u-author',
    bom_revision_ref: null,
    released_at: '2026-04-01T00:00:00Z',
    released_by: 'u-releaser',
    has_graph_issue: false,
    closure_rationale: null,
    version: 3,
    created_at: '2026-03-30T00:00:00Z',
    updated_at: '2026-04-14T00:00:00Z',
    operations: [],
    closure_ready: true,
    closure_blockers: [],
    ...overrides,
  } as WorkOrderDetailOut;
}

function renderPanel(
  wo: WorkOrderDetailOut,
  onCloseSuccess: () => void = vi.fn(),
): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <CloseWorkOrderPanel wo={wo} onCloseSuccess={onCloseSuccess} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('CloseWorkOrderPanel — governance-disabled banner (path A)', () => {
  beforeEach(() => {
    mockAuthState.closureEnabled = false;
    mockAuthState.roles = ['qa_manager'];
  });
  afterEach(() => cleanup());

  it('renders the banner for a complete WO when closure_enabled=false', () => {
    renderPanel(buildWO());
    const banner = screen.getByTestId('closure-disabled-banner');
    expect(banner).toHaveTextContent(/not currently enabled/i);
    expect(banner).toHaveTextContent(/MSFC QA approval/i);
    /* Effective terminal state explicit — no close button, no tooltip
       dead-end for the operator. */
    expect(
      screen.queryByTestId('close-wo-button'),
    ).toBeNull();
  });

  it('banner also renders for non-qa_manager readers', () => {
    /* Spec §5.3: "complete is the effective terminal state" must be
       explicit to every reader, not just the QA manager.  Engineers
       and operators on this page also need to understand that the
       `closed` state is not yet reachable. */
    mockAuthState.roles = ['engineer'];
    renderPanel(buildWO());
    expect(
      screen.getByTestId('closure-disabled-banner'),
    ).toBeInTheDocument();
  });

  it('renders nothing for a non-complete WO even when disabled', () => {
    /* The panel is scoped to `status === 'complete'` — other states
       have their transition affordances in WorkOrderDetail's header
       button row, not here. */
    const { container } = renderPanel(
      buildWO({ status: 'in_progress' }),
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe('CloseWorkOrderPanel — blocker tooltip (path B2)', () => {
  beforeEach(() => {
    mockAuthState.closureEnabled = true;
    mockAuthState.roles = ['qa_manager'];
    closeWOMock.mockReset();
  });
  afterEach(() => cleanup());

  it('renders blocker list when closure_ready=false with open NCRs', () => {
    const wo = buildWO({
      closure_ready: false,
      closure_blockers: [
        {
          code: 'WO_CLOSE_NCRS_NOT_CLOSED',
          message: 'NCR NCR-2026-0007 is in `open` — must be closed before WO close.',
          entity_id: 'ncr-7',
        },
        {
          code: 'WO_CLOSE_UNRESOLVED_CONDITIONALS',
          message: 'Op 3 (Weld injector ports) has an unresolved conditional inspection.',
          entity_id: 'op-3',
        },
      ],
    });
    renderPanel(wo);

    const blockerPanel = screen.getByTestId('close-wo-blockers');
    expect(blockerPanel).toHaveTextContent(
      /2 preconditions not yet met/i,
    );
    expect(blockerPanel).toHaveTextContent(/NCR-2026-0007/);
    expect(blockerPanel).toHaveTextContent(/Weld injector ports/);

    /* The close button is visible but disabled — operator sees
       precisely why they can't click it. */
    const btn = screen.getByTestId('close-wo-button');
    expect(btn).toBeDisabled();
  });

  it('button stays disabled with generic tooltip when ready=false but blockers empty', () => {
    renderPanel(
      buildWO({ closure_ready: false, closure_blockers: [] }),
    );
    const btn = screen.getByTestId('close-wo-button');
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute(
      'title',
      expect.stringContaining('not yet satisfied'),
    );
  });
});

describe('CloseWorkOrderPanel — enabled close (path B1)', () => {
  beforeEach(() => {
    mockAuthState.closureEnabled = true;
    mockAuthState.roles = ['qa_manager'];
    closeWOMock.mockReset();
  });
  afterEach(() => cleanup());

  it('renders enabled button → confirm dialog → mutate with rationale', async () => {
    closeWOMock.mockResolvedValueOnce({
      id: 'wo-1', status: 'closed', version: 4,
    });

    const onSuccess = vi.fn();
    renderPanel(buildWO(), onSuccess);

    const btn = screen.getByTestId('close-wo-button');
    expect(btn).not.toBeDisabled();

    fireEvent.click(btn);

    /* Confirmation dialog opens; rationale textarea is present. */
    const textarea = await screen.findByTestId(
      'close-wo-rationale',
    );
    fireEvent.change(textarea, {
      target: { value: 'All ops complete; QA attest.' },
    });

    /* The dialog's confirm button carries "Confirm Close". */
    fireEvent.click(
      screen.getByRole('button', { name: /^Confirm Close$/i }),
    );

    await waitFor(() => expect(closeWOMock).toHaveBeenCalledTimes(1));

    const [woId, body] = closeWOMock.mock.calls[0];
    expect(woId).toBe('wo-1');
    expect(body).toEqual({
      version: 3,
      closure_rationale: 'All ops complete; QA attest.',
    });
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
  });

  it('rationale is nulled when textarea left blank', async () => {
    closeWOMock.mockResolvedValueOnce({
      id: 'wo-1', status: 'closed', version: 4,
    });

    renderPanel(buildWO());
    fireEvent.click(screen.getByTestId('close-wo-button'));
    await screen.findByTestId('close-wo-rationale');
    fireEvent.click(
      screen.getByRole('button', { name: /^Confirm Close$/i }),
    );

    await waitFor(() => expect(closeWOMock).toHaveBeenCalled());
    const [, body] = closeWOMock.mock.calls[0];
    /* Empty rationale carried as null, not "" — the server's optional
       field matches NULL on the persisted row. */
    expect(body.closure_rationale).toBeNull();
  });
});

describe('CloseWorkOrderPanel — closure_rationale display (path B1)', () => {
  beforeEach(() => {
    mockAuthState.closureEnabled = true;
    mockAuthState.roles = ['qa_manager'];
    closeWOMock.mockReset();
  });
  afterEach(() => cleanup());

  it('the closed WO response carries closure_rationale through onSuccess callback', async () => {
    /* Spec §5.3: closure_rationale is persisted on the closed entity.
       The panel itself doesn't render the rationale (that's the
       parent WorkOrderDetail's job) — but we pin that the success
       path fires onCloseSuccess so the parent can refetch and
       render the persisted value.  Rationale rendering in
       WorkOrderDetail header is covered by visual / manual test
       (no isolated component to mount). */
    closeWOMock.mockResolvedValueOnce({
      id: 'wo-1',
      status: 'closed',
      closure_rationale: 'Per QA attestation 2026-04-18.',
      version: 4,
    });

    const onSuccess = vi.fn();
    renderPanel(buildWO(), onSuccess);
    fireEvent.click(screen.getByTestId('close-wo-button'));
    const textarea = await screen.findByTestId('close-wo-rationale');
    fireEvent.change(textarea, {
      target: { value: 'Per QA attestation 2026-04-18.' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /^Confirm Close$/i }),
    );

    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    /* Confirm the rationale reached the server exactly as typed
       (pre-refetch the parent would then render it). */
    const [, body] = closeWOMock.mock.calls[0];
    expect(body.closure_rationale).toBe(
      'Per QA attestation 2026-04-18.',
    );
  });
});

describe('CloseWorkOrderPanel — non-qa_manager (path C)', () => {
  beforeEach(() => {
    mockAuthState.closureEnabled = true;
    mockAuthState.roles = ['engineer'];
  });
  afterEach(() => cleanup());

  it('renders nothing for a non-QA reader when closure_enabled=true', () => {
    /* When the flag is on, the close action belongs to the QA
       manager — the page shows the StatusBadge for other readers
       but not the close panel. */
    const { container } = renderPanel(buildWO());
    expect(container).toBeEmptyDOMElement();
  });
});

/* ── NCRCloseSection ── */

function renderNCRSection(
  onCloseSuccess: () => void = vi.fn(),
): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <NCRCloseSection
          ncr={{ id: 'ncr-1', ncr_number: 'NCR-2026-0001', version: 2 }}
          onCloseSuccess={onCloseSuccess}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('NCRCloseSection — governance-disabled banner (path A)', () => {
  beforeEach(() => {
    mockAuthState.closureEnabled = false;
    mockAuthState.roles = ['qa_manager'];
  });
  afterEach(() => cleanup());

  it('renders the banner when closure_enabled=false', () => {
    renderNCRSection();
    const banner = screen.getByTestId('ncr-closure-disabled-banner');
    expect(banner).toHaveTextContent(/not currently enabled/i);
    expect(banner).toHaveTextContent(/dispositioned/i);
    expect(screen.queryByTestId('close-ncr-button')).toBeNull();
  });

  it('banner visible to non-QA readers as well', () => {
    mockAuthState.roles = ['engineer'];
    renderNCRSection();
    expect(
      screen.getByTestId('ncr-closure-disabled-banner'),
    ).toBeInTheDocument();
  });
});

describe('NCRCloseSection — enabled close (path B)', () => {
  beforeEach(() => {
    mockAuthState.closureEnabled = true;
    mockAuthState.roles = ['qa_manager'];
    closeNCRMock.mockReset();
  });
  afterEach(() => cleanup());

  it('qa_manager sees the close button → confirm dialog → mutate with rationale', async () => {
    closeNCRMock.mockResolvedValueOnce({
      id: 'ncr-1', status: 'closed', version: 3,
    });

    const onSuccess = vi.fn();
    renderNCRSection(onSuccess);

    fireEvent.click(screen.getByTestId('close-ncr-button'));

    const textarea = await screen.findByTestId('close-ncr-rationale');
    fireEvent.change(textarea, {
      target: { value: 'MRB confirmed.' },
    });

    fireEvent.click(
      screen.getByRole('button', { name: /^Confirm Close$/i }),
    );

    await waitFor(() => expect(closeNCRMock).toHaveBeenCalledTimes(1));
    const [ncrId, body] = closeNCRMock.mock.calls[0];
    expect(ncrId).toBe('ncr-1');
    expect(body).toEqual({
      version: 2,
      closure_rationale: 'MRB confirmed.',
    });
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
  });
});

describe('NCRCloseSection — non-qa_manager (path C)', () => {
  beforeEach(() => {
    mockAuthState.closureEnabled = true;
    mockAuthState.roles = ['engineer'];
  });
  afterEach(() => cleanup());

  it('renders nothing for a non-QA reader when closure_enabled=true', () => {
    const { container } = renderNCRSection();
    expect(container).toBeEmptyDOMElement();
  });
});

/* ── GC-4 round-2: reauth-on-close + idempotency-key contract ──
   Spec §11.3 names WO close + NCR close as safety-critical protected
   actions.  These tests pin that:
     - a caller-supplied idempotency key is forwarded on every close
       attempt (GC-3 round-3 payload-preservation + same-key retry
       contract),
     - a 401 REAUTH_REQUIRED surfaces the ReauthModal (not a generic
       error banner), and
     - the buffer carries the FULL close payload, not just a summary. */

import { peekReauthBuffer } from '@/auth/reauth';

function buildReauthError(
  action: 'wo_close' | 'ncr_close' = 'wo_close',
): unknown {
  return {
    response: {
      status: 401,
      data: {
        detail: {
          error: {
            code: 'REAUTH_REQUIRED',
            action,
            auth_age_seconds: 400,
            max_auth_age_seconds: 330,
            message: 'This action requires re-authentication.',
          },
        },
      },
    },
  };
}

describe('CloseWorkOrderPanel — reauth + idempotency (GC-4 round 2)', () => {
  beforeEach(() => {
    mockAuthState.closureEnabled = true;
    mockAuthState.roles = ['qa_manager'];
    closeWOMock.mockReset();
    sessionStorage.clear();
  });
  afterEach(() => cleanup());

  it('forwards an Idempotency-Key on the close request', async () => {
    closeWOMock.mockResolvedValueOnce({
      id: 'wo-1', status: 'closed', version: 4,
    });

    renderPanel(buildWO());
    fireEvent.click(screen.getByTestId('close-wo-button'));
    await screen.findByTestId('close-wo-rationale');
    fireEvent.click(
      screen.getByRole('button', { name: /^Confirm Close$/i }),
    );

    await waitFor(() => expect(closeWOMock).toHaveBeenCalled());
    /* closeWorkOrder(id, body, idempotencyKey) — third arg is the
       key.  The panel generates it at first-submit click and reuses
       it on retry, so it must be a non-empty string every call. */
    const [, , key] = closeWOMock.mock.calls[0];
    expect(typeof key).toBe('string');
    expect(key).toBeTruthy();
  });

  it('opens ReauthModal + buffers the full payload on 401 REAUTH_REQUIRED', async () => {
    closeWOMock.mockRejectedValueOnce(buildReauthError('wo_close'));

    renderPanel(buildWO());
    fireEvent.click(screen.getByTestId('close-wo-button'));
    const textarea = await screen.findByTestId('close-wo-rationale');
    fireEvent.change(textarea, {
      target: { value: 'Full QA attestation note.' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /^Confirm Close$/i }),
    );

    /* After the mutation rejects, the reauth modal heading surfaces. */
    await waitFor(() => {
      expect(
        screen.getByText(/re-authentication required/i),
      ).toBeInTheDocument();
    });

    const buffer = peekReauthBuffer();
    expect(buffer).not.toBeNull();
    expect(buffer?.action).toBe('wo_close');
    if (buffer && buffer.action === 'wo_close') {
      /* Spec §11.3 payload preservation: the buffer MUST carry the
         exact rationale the user typed, not a summary string. */
      expect(buffer.payload.closure_rationale).toBe(
        'Full QA attestation note.',
      );
      expect(buffer.payload.version).toBe(3);
      /* Same-key retry contract: the buffer key equals the key the
         original request used. */
      const [, , originalKey] = closeWOMock.mock.calls[0];
      expect(buffer.idempotencyKey).toBe(originalKey);
    }
  });
});

describe('NCRCloseSection — reauth + idempotency (GC-4 round 2)', () => {
  beforeEach(() => {
    mockAuthState.closureEnabled = true;
    mockAuthState.roles = ['qa_manager'];
    closeNCRMock.mockReset();
    sessionStorage.clear();
  });
  afterEach(() => cleanup());

  it('forwards an Idempotency-Key on the close request', async () => {
    closeNCRMock.mockResolvedValueOnce({
      id: 'ncr-1', status: 'closed', version: 3,
    });

    renderNCRSection();
    fireEvent.click(screen.getByTestId('close-ncr-button'));
    await screen.findByTestId('close-ncr-rationale');
    fireEvent.click(
      screen.getByRole('button', { name: /^Confirm Close$/i }),
    );

    await waitFor(() => expect(closeNCRMock).toHaveBeenCalled());
    const [, , key] = closeNCRMock.mock.calls[0];
    expect(typeof key).toBe('string');
    expect(key).toBeTruthy();
  });

  it('opens ReauthModal + buffers the full payload on 401 REAUTH_REQUIRED', async () => {
    closeNCRMock.mockRejectedValueOnce(buildReauthError('ncr_close'));

    renderNCRSection();
    fireEvent.click(screen.getByTestId('close-ncr-button'));
    const textarea = await screen.findByTestId('close-ncr-rationale');
    fireEvent.change(textarea, {
      target: { value: 'MRB concurred; closing.' },
    });
    fireEvent.click(
      screen.getByRole('button', { name: /^Confirm Close$/i }),
    );

    await waitFor(() => {
      expect(
        screen.getByText(/re-authentication required/i),
      ).toBeInTheDocument();
    });

    const buffer = peekReauthBuffer();
    expect(buffer).not.toBeNull();
    expect(buffer?.action).toBe('ncr_close');
    if (buffer && buffer.action === 'ncr_close') {
      expect(buffer.payload.closure_rationale).toBe(
        'MRB concurred; closing.',
      );
      expect(buffer.payload.version).toBe(2);
      const [, , originalKey] = closeNCRMock.mock.calls[0];
      expect(buffer.idempotencyKey).toBe(originalKey);
    }
  });
});
