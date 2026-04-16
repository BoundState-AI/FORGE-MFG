/**
 * Module:   GC-3 round 3 — NCR disposition payload preservation
 * Purpose:  Pin that the post-reauth resume submits the ORIGINAL
 *           disposition body, not whatever the form currently shows.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this test exists: Codex round-3 review finding 1 — without the
 * payload in the buffer, NCRDetail rebuilt the request body from
 * current React state on resume.  A user who tweaked any field
 * between the original submit and the post-redirect Confirm-and-Submit
 * would silently submit different data than they were asked to
 * confirm.  Spec §11.3 mandates the protected action be preserved
 * across the round trip — which means the buffer carries the body,
 * and the resume replays it verbatim.
 *
 * Why a separate file from reauth.test.tsx: the main reauth file
 * installs a global ``vi.mock('@/api/endpoints', ...)`` that stubs
 * createSignOff but breaks the dispositionNCR + fetchNCR round trip
 * we need here.  Same posture as reauth-ncr.test.ts.
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
  act,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';
import {
  setReauthBuffer,
  type NCRDispositionReauthBuffer,
} from '@/auth/reauth';

/* Stub the auth store so NCRDetail's `user` + `hasRole('engineer')`
   give the engineer affordance.  Initiator id MUST differ from the
   stubbed user so SOD-002 doesn't disable the form. */
const mockAuthState = {
  user: {
    id: 'user-engineer',
    display_name: 'Test Engineer',
    email: 't@t.com',
    cognito_sub: 't',
    active: true,
    is_system_actor: false,
    roles: [{ id: 'r1', name: 'engineer', permissions: [] }],
    program_memberships: [],
  },
  isAuthenticated: true,
  accessToken: 'test-token',
  activeProgram: null,
  roles: ['engineer'],
  programIds: [],
  hasRole: (role: string) => role === 'engineer',
  hasAnyRole: (...roles: string[]) => roles.includes('engineer'),
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
  fetchNCR: vi.fn(),
  dispositionNCR: vi.fn(),
  patchNCR: vi.fn(),
  requestUploadUrl: vi.fn(),
  confirmEvidence: vi.fn(),
}));

import { fetchNCR, dispositionNCR } from '@/api/endpoints';
import NCRDetail from '@/pages/NCRDetail';

const fetchNCRMock = fetchNCR as unknown as Mock;
const dispositionNCRMock = dispositionNCR as unknown as Mock;

const NCR_FIXTURE = {
  id: 'ncr-1',
  ncr_number: 'NCR-2026-0001',
  work_order_id: 'wo-1',
  operation_id: null,
  part_instance_id: null,
  description: 'Bad weld',
  root_cause: null,
  status: 'open',
  disposition: 'pending',
  disposition_rationale: null,
  engineering_approval_id: null,
  mrb_required: false,
  mrb_disposition: null,
  mrb_authority_id: null,
  schedule_impact_days: null,
  /* Initiator MUST differ from mockAuthState.user.id so SOD-002 does
     NOT block the disposition affordance for our stubbed engineer. */
  initiated_by_id: 'someone-else',
  evidence_refs: [],
  remediation_work_order_id: null,
  version: 1,
  created_at: '2026-04-16T00:00:00Z',
  updated_at: '2026-04-16T00:00:00Z',
};

function renderNCRDetailAtResume(): ReturnType<typeof render> {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  /* The route carries `?reauth=resume` so NCRDetail's resume useEffect
     fires on mount.  We supply the path param via the Routes match so
     `useParams()` returns ncr-1 — same shape as the real /ncrs/:id. */
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/ncrs/ncr-1?reauth=resume']}>
        <Routes>
          <Route path="/ncrs/:id" element={<NCRDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('NCRDetail post-reauth replay (GC-3 round 3 finding 1)', () => {
  beforeEach(() => {
    sessionStorage.clear();
    fetchNCRMock.mockReset();
    dispositionNCRMock.mockReset();
  });
  afterEach(() => cleanup());

  it('submits the buffered payload, not the latest live form state', async () => {
    /* Seed the sessionStorage buffer with the ORIGINAL disposition the
       user chose (use_as_is, "original rationale").  When NCRDetail
       mounts with ?reauth=resume, the useEffect reads the buffer and
       opens the post-callback Confirm-and-Submit dialog. */
    const originalBuffer: NCRDispositionReauthBuffer = {
      createdAt: new Date().toISOString(),
      idempotencyKey: 'fixed-key-original',
      action: 'ncr_disposition',
      payload: {
        version: 1,
        disposition: 'use_as_is',
        disposition_rationale: 'Original rationale captured at submit',
      },
      actionSummary: 'Disposition NCR NCR-2026-0001 as use_as_is',
      returnTo: '/ncrs/ncr-1',
      forceFreshPrincipal: false,
    };
    setReauthBuffer(originalBuffer);

    fetchNCRMock.mockResolvedValue(NCR_FIXTURE);
    /* The replay must succeed — return a dispositioned NCR. */
    dispositionNCRMock.mockResolvedValue({
      ...NCR_FIXTURE,
      status: 'dispositioned',
      disposition: 'use_as_is',
      disposition_rationale: 'Original rationale captured at submit',
      version: 2,
    });

    renderNCRDetailAtResume();

    /* Wait for the NCR to load + the resume dialog to open.
       Dialog title + confirm button both say "Confirm and Submit",
       so anchor on the heading role to disambiguate. */
    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /Confirm and Submit/i }),
      ).toBeInTheDocument();
    });

    /* Now SIMULATE THE BUG SETUP — the user changes the form before
       clicking Confirm.  We change disposition select + rationale to
       prove the resume DOES NOT use these new values.
       NCRDetail's <label>s aren't linked via htmlFor, so we anchor
       on the role/placeholder instead. */
    const select = screen.getByRole('combobox') as HTMLSelectElement;
    const textarea = screen.getByPlaceholderText(
      /Provide rationale for this disposition/i,
    ) as HTMLTextAreaElement;

    act(() => {
      fireEvent.change(select, { target: { value: 'rework' } });
      fireEvent.change(textarea, {
        target: { value: 'Tampered rationale after re-auth' },
      });
    });

    /* The form now shows rework + tampered rationale.  Click the
       Confirm and Submit button in the resume dialog. */
    fireEvent.click(
      screen.getByRole('button', { name: /Confirm and Submit/i }),
    );

    await waitFor(() => expect(dispositionNCRMock).toHaveBeenCalled());

    /* Spec §11.3: the replayed call MUST carry the ORIGINAL payload,
       not the post-mount form state.  This is the regression. */
    const [ncrId, body, idempotencyKey] = dispositionNCRMock.mock.calls[0];
    expect(ncrId).toBe('ncr-1');
    expect(body).toEqual(originalBuffer.payload);
    expect(idempotencyKey).toBe(originalBuffer.idempotencyKey);
  });
});
