/**
 * Module:   GC-3 Reauth Frontend Tests
 * Purpose:  Pin the REAUTH_REQUIRED detection, buffer round-trip, and
 *           SignOffPanel "second signer" banner behavior.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * What these tests guard against:
 *   - The REAUTH detection regressing to a generic 401-toast: the page
 *     would dead-end the user instead of routing into recovery.
 *   - The Idempotency-Key getting regenerated between attempts: the
 *     user's intent would lose its dedupe protection on retry.
 *   - The §9.2 second-signer banner copy drifting: the spec quotes
 *     the exact wording, and the frontend retry path depends on it.
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
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';
import {
  isReauthRequired,
  extractReauthError,
  setReauthBuffer,
  takeReauthBuffer,
  peekReauthBuffer,
  type ReauthBuffer,
} from '@/auth/reauth';

/* ── Auth store mock matching the parity-pages pattern ── */
const mockAuthState = {
  user: {
    id: 'u1',
    display_name: 'Test User',
    email: 't@t.com',
    cognito_sub: 't',
    active: true,
    is_system_actor: false,
    roles: [{ id: 'r1', name: 'inspector', permissions: [] }],
    program_memberships: [],
  },
  isAuthenticated: true,
  accessToken: 'test-token',
  activeProgram: null,
  roles: ['inspector'],
  programIds: [],
  hasRole: (role: string) => role === 'inspector',
  hasAnyRole: (...roles: string[]) => roles.includes('inspector'),
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
  createSignOff: vi.fn(),
}));

import { createSignOff } from '@/api/endpoints';
import { SignOffPanel } from '@/components/SignOffPanel';

const createSignOffMock = createSignOff as unknown as Mock;

/* ── Helpers ── */

function buildReauthError(overrides?: Partial<{
  action: 'sign_off' | 'ncr_disposition';
  authAge: number;
}>): unknown {
  return {
    response: {
      status: 401,
      data: {
        detail: {
          error: {
            code: 'REAUTH_REQUIRED',
            action: overrides?.action ?? 'sign_off',
            auth_age_seconds: overrides?.authAge ?? 400,
            max_auth_age_seconds: 330,
            message: 'This action requires re-authentication.',
          },
        },
      },
    },
  };
}

function renderPanel(overrides?: Partial<React.ComponentProps<typeof SignOffPanel>>) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/work-orders/wo-1/ops/op-1']}>
        <SignOffPanel
          operationId="op-1"
          operationSequence={3}
          operationDescription="Weld injector ports"
          isSafetyCritical
          signOffRolesRequired={['inspector', 'engineer']}
          onSignOffSuccess={vi.fn()}
          {...overrides}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/* ── isReauthRequired truth table ── */

describe('isReauthRequired', () => {
  it('returns true for the canonical REAUTH envelope', () => {
    expect(isReauthRequired(buildReauthError())).toBe(true);
  });

  it('returns false for plain 401 without REAUTH code', () => {
    expect(
      isReauthRequired({
        response: { status: 401, data: { detail: 'Not authenticated' } },
      }),
    ).toBe(false);
  });

  it('returns false for non-401 responses', () => {
    expect(
      isReauthRequired({
        response: {
          status: 422,
          data: { detail: { error: { code: 'REAUTH_REQUIRED' } } },
        },
      }),
    ).toBe(false);
  });

  it('returns false for null / undefined / non-object', () => {
    expect(isReauthRequired(null)).toBe(false);
    expect(isReauthRequired(undefined)).toBe(false);
    expect(isReauthRequired('REAUTH_REQUIRED')).toBe(false);
    expect(isReauthRequired(401)).toBe(false);
  });
});

describe('extractReauthError', () => {
  it('returns the structured envelope for reauth errors', () => {
    const env = extractReauthError(buildReauthError({ authAge: 405 }));
    expect(env).toEqual({
      code: 'REAUTH_REQUIRED',
      action: 'sign_off',
      auth_age_seconds: 405,
      max_auth_age_seconds: 330,
      message: 'This action requires re-authentication.',
    });
  });

  it('returns null for non-reauth errors', () => {
    expect(extractReauthError({ response: { status: 500 } })).toBeNull();
    expect(extractReauthError(null)).toBeNull();
  });
});

/* ── Buffer round-trip ── */

describe('reauth buffer', () => {
  beforeEach(() => sessionStorage.clear());

  it('preserves the Idempotency-Key across set → peek → take', () => {
    const buffer: ReauthBuffer = {
      createdAt: new Date().toISOString(),
      idempotencyKey: 'fixed-key-123',
      action: 'sign_off',
      actionSummary: 'Sign off Op 3 as inspector',
      returnTo: '/work-orders/wo-1/ops/op-1',
      forceFreshPrincipal: false,
    };
    setReauthBuffer(buffer);

    const peeked = peekReauthBuffer();
    expect(peeked?.idempotencyKey).toBe('fixed-key-123');
    /* peek does NOT clear the slot — required by the post-callback
       resume modal so a re-render doesn't lose the buffer. */
    expect(peekReauthBuffer()).not.toBeNull();

    const taken = takeReauthBuffer();
    expect(taken?.idempotencyKey).toBe('fixed-key-123');
    /* take DOES clear the slot — required so a refresh doesn't
       re-prompt the user. */
    expect(peekReauthBuffer()).toBeNull();
  });

  it('returns null cleanly when nothing is buffered', () => {
    expect(peekReauthBuffer()).toBeNull();
    expect(takeReauthBuffer()).toBeNull();
  });
});

/* ── SignOffPanel REAUTH detection + Idempotency-Key reuse ── */

describe('SignOffPanel reauth recovery', () => {
  beforeEach(() => {
    sessionStorage.clear();
    createSignOffMock.mockReset();
  });
  afterEach(() => cleanup());

  it('opens the ReauthModal on 401 REAUTH_REQUIRED and buffers the same key', async () => {
    createSignOffMock.mockRejectedValueOnce(buildReauthError());

    renderPanel();

    /* Submit → ConfirmationDialog → Confirm.  After the dialog opens
       there are two "Sign Off" buttons (panel + dialog); pick the
       second by index. */
    fireEvent.click(screen.getAllByRole('button', { name: /^Sign Off$/i })[0]);
    const buttonsAfterOpen = await screen.findAllByRole('button', {
      name: /^Sign Off$/i,
    });
    /* The dialog's confirm button is the second occurrence. */
    fireEvent.click(buttonsAfterOpen[1]);

    /* The reauth modal heading should appear once the mutation rejects. */
    await waitFor(() => {
      expect(
        screen.getByText(/re-authentication required/i),
      ).toBeInTheDocument();
    });

    /* The buffer must hold the same key the original request used. */
    const buffer = peekReauthBuffer();
    expect(buffer).not.toBeNull();
    expect(buffer?.action).toBe('sign_off');
    /* The mutation was called once with an Idempotency-Key argument
       — confirm createSignOff received the same key the buffer holds. */
    const callArgs = createSignOffMock.mock.calls[0];
    /* createSignOff(opId, body, idempotencyKey) — third arg is the key. */
    expect(callArgs?.[2]).toBe(buffer?.idempotencyKey);
  });

  it('shows the §9.2 second-signer banner after a successful first sign-off', async () => {
    createSignOffMock.mockResolvedValueOnce({
      id: 'so-1',
      operation_id: 'op-1',
      user_id: 'u1',
      role: 'inspector',
      execution_cycle: 1,
      action: 'approve',
      signed_at: '2026-04-16T00:00:00Z',
      notes: null,
      version: 1,
      created_at: '2026-04-16T00:00:00Z',
      updated_at: '2026-04-16T00:00:00Z',
    });

    const onSuccess = vi.fn();
    renderPanel({ onSignOffSuccess: onSuccess });

    /* Trigger submit → confirm.  Two buttons share the "Sign Off"
       label after the dialog opens — pick the dialog's. */
    fireEvent.click(screen.getAllByRole('button', { name: /^Sign Off$/i })[0]);
    const buttonsAfterOpen = await screen.findAllByRole('button', {
      name: /^Sign Off$/i,
    });
    fireEvent.click(buttonsAfterOpen[1]);

    await waitFor(() => expect(onSuccess).toHaveBeenCalled());

    /* Spec §9.2 line 1480 verbatim copy. */
    const banner = await screen.findByTestId('second-signer-banner');
    expect(banner).toHaveTextContent(/First sign-off recorded/i);
    expect(banner).toHaveTextContent(/Second sign-off required/i);
    expect(banner).toHaveTextContent(/different user/i);

    /* Banner must offer the second-signer entry button. */
    expect(
      screen.getByRole('button', { name: /Sign Off as Second Signer/i }),
    ).toBeInTheDocument();
  });
});

/* ── PKCE token exchange ── */

describe('exchangeCodeForToken', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    sessionStorage.clear();
    /* Stub the Cognito domain + client id so the helper's guard passes. */
    (import.meta.env as Record<string, string>).VITE_COGNITO_DOMAIN =
      'https://auth.example.test';
    (import.meta.env as Record<string, string>).VITE_COGNITO_APP_CLIENT_ID =
      'test-client-id';
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    delete (import.meta.env as Record<string, string | undefined>)
      .VITE_COGNITO_DOMAIN;
    delete (import.meta.env as Record<string, string | undefined>)
      .VITE_COGNITO_APP_CLIENT_ID;
  });

  it('posts the code + verifier to /oauth2/token and returns the access_token', async () => {
    const { exchangeCodeForToken } = await import('@/auth/reauth');
    /* Seed the verifier as if beginReauth() had run. */
    sessionStorage.setItem('forge_pkce_verifier', 'test-verifier-43-chars-min');

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ access_token: 'fresh-access-token' }),
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const token = await exchangeCodeForToken('oauth-code-abc');
    expect(token).toBe('fresh-access-token');

    /* Verify the exchange used the verifier and the Cognito endpoint. */
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://auth.example.test/oauth2/token');
    const body = (init as RequestInit).body as string;
    expect(body).toContain('grant_type=authorization_code');
    expect(body).toContain('code=oauth-code-abc');
    expect(body).toContain('code_verifier=test-verifier-43-chars-min');
    expect(body).toContain('client_id=test-client-id');

    /* Verifier is single-use: must have been cleared after redemption. */
    expect(sessionStorage.getItem('forge_pkce_verifier')).toBeNull();
  });

  it('returns null when the Cognito token endpoint rejects the code', async () => {
    const { exchangeCodeForToken } = await import('@/auth/reauth');
    sessionStorage.setItem('forge_pkce_verifier', 'test-verifier-43-chars-min');

    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error: 'invalid_grant' }),
    }) as unknown as typeof fetch;

    const token = await exchangeCodeForToken('stale-code');
    expect(token).toBeNull();
  });

  it('returns null when the PKCE verifier is missing (replay guard)', async () => {
    const { exchangeCodeForToken } = await import('@/auth/reauth');
    /* No verifier set → the helper must refuse to exchange, since
       replaying an authorization code without the verifier would
       surrender PKCE's whole point. */
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const token = await exchangeCodeForToken('whatever');
    expect(token).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

