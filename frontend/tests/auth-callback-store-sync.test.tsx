/**
 * Module:   GC-3 round 3 — AuthCallback / Zustand store sync
 * Purpose:  Pin that AuthCallback flips Zustand `isAuthenticated`
 *           through `setToken(...)` on BOTH the PKCE happy path and
 *           the local-dev fallback, not via direct sessionStorage.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this test exists: Codex round-3 review finding 2 — the
 * pre-fix callback wrote to sessionStorage directly.  AuthGate reads
 * `useAuthStore().isAuthenticated`, and that flag only flips through
 * the store's `setToken` action.  Result: the post-callback page
 * thought the user was unauthenticated and never gave the resume
 * modal a chance to render.
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
import { render, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { useAuthStore } from '@/auth/store';

/* The reauth helper mocks let us drive both branches of AuthCallback
   from the test (PKCE token exchange + local-dev fallback) without
   spinning up a real Cognito surface. */
vi.mock('@/auth/reauth', async () => {
  const actual = await vi.importActual<typeof import('@/auth/reauth')>(
    '@/auth/reauth',
  );
  return {
    ...actual,
    exchangeCodeForToken: vi.fn(),
    peekReauthBuffer: vi.fn(() => null),
    takeOauthState: vi.fn(() => null),
    takeReauthBuffer: vi.fn(() => null),
  };
});

import { exchangeCodeForToken } from '@/auth/reauth';
import AuthCallback from '@/auth/AuthCallback';

const exchangeMock = exchangeCodeForToken as unknown as Mock;

function renderCallbackWith(search: string): ReturnType<typeof render> {
  return render(
    <MemoryRouter initialEntries={[`/auth/callback${search}`]}>
      <Routes>
        <Route path="/auth/callback" element={<AuthCallback />} />
        {/* Catch-all so navigate() targets render without errors. */}
        <Route path="*" element={<div data-testid="landed">landed</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('AuthCallback Zustand store sync (GC-3 round 3 finding 2)', () => {
  beforeEach(() => {
    sessionStorage.clear();
    /* Reset the store to a known unauthenticated baseline so the
       assertions below prove the store FLIPPED, not that it was
       already authenticated from a prior test. */
    useAuthStore.getState().clearAuth();
    exchangeMock.mockReset();
  });
  afterEach(() => cleanup());

  it('PKCE path: setToken updates the Zustand store with the fresh access token', async () => {
    /* Stub the env so AuthCallback takes the (A) Cognito branch. */
    (import.meta.env as Record<string, string>).VITE_COGNITO_DOMAIN =
      'https://auth.example.test';
    (import.meta.env as Record<string, string>).VITE_COGNITO_APP_CLIENT_ID =
      'client-id';
    exchangeMock.mockResolvedValue('fresh-pkce-access-token');

    expect(useAuthStore.getState().isAuthenticated).toBe(false);

    renderCallbackWith('?code=auth-code-123&state=ignored-by-stub');

    await waitFor(() => {
      expect(useAuthStore.getState().isAuthenticated).toBe(true);
    });
    expect(useAuthStore.getState().accessToken).toBe('fresh-pkce-access-token');
    /* sessionStorage is the secondary persistence layer; setToken
       writes both, so we verify the same value made it there too. */
    expect(sessionStorage.getItem('forge_access_token')).toBe(
      'fresh-pkce-access-token',
    );

    delete (import.meta.env as Record<string, string | undefined>)
      .VITE_COGNITO_DOMAIN;
    delete (import.meta.env as Record<string, string | undefined>)
      .VITE_COGNITO_APP_CLIENT_ID;
  });

  it('local-dev path: setToken installs the placeholder token', async () => {
    /* No Cognito env vars + no ?code → AuthCallback takes the (C)
       local-dev fallback branch.  Pre-fix this wrote to sessionStorage
       directly and AuthGate never noticed. */
    delete (import.meta.env as Record<string, string | undefined>)
      .VITE_COGNITO_DOMAIN;
    delete (import.meta.env as Record<string, string | undefined>)
      .VITE_COGNITO_APP_CLIENT_ID;

    expect(useAuthStore.getState().isAuthenticated).toBe(false);

    renderCallbackWith('');

    await waitFor(() => {
      expect(useAuthStore.getState().isAuthenticated).toBe(true);
    });
    expect(useAuthStore.getState().accessToken).toBe('local-dev-token');
    expect(sessionStorage.getItem('forge_access_token')).toBe(
      'local-dev-token',
    );
  });
});
