/**
 * Module:   AuthGate
 * Purpose:  Auth-gated layout route.  Blocks rendering of its Outlet
 *           until a Cognito access token is present and the user
 *           profile has been fetched.
 * Rev:      0.2.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this is a layout route (not a children wrapper): public routes
 * — ``/login`` and ``/auth/callback`` — must be reachable without an
 * access token.  The GC-3 reauth flow clears the token, redirects to
 * ``/auth/callback``, exchanges the Cognito OAuth code for a fresh
 * token, and only THEN has an identity.  If the callback were wrapped
 * in an always-on auth gate the fresh-token handoff would be
 * structurally unreachable — the gate would block the user on the
 * empty token before the exchange step could run.  The React Router
 * v6 layout-route pattern (parent route with ``element={<AuthGate />}``
 * + child routes that render through ``<Outlet />``) lets us mount
 * AuthGate only where it's actually required.
 *
 * The component otherwise behaves identically to the original wrapper:
 *   - No access token → render the inline sign-in prompt (local-dev).
 *     Production deployments wire this to ``beginReauth()``.
 *   - Token present, /api/users/me in flight → skeleton.
 *   - /api/users/me failed → inline error banner.
 *   - Token + profile loaded → render child routes via ``<Outlet />``.
 */

import { useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from './store';
import { fetchCurrentUser } from '@/api/endpoints';
import { ApiError } from '@/api/typed-client';
import { beginReauth } from './reauth';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';

export function AuthGate() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setUser = useAuthStore((s) => s.setUser);
  const clearAuth = useAuthStore((s) => s.clearAuth);
  const user = useAuthStore((s) => s.user);
  const cognitoEnabled = Boolean(
    import.meta.env.VITE_COGNITO_DOMAIN &&
    import.meta.env.VITE_COGNITO_APP_CLIENT_ID,
  );

  const { data, isLoading, error } = useQuery({
    queryKey: ['users', 'me'],
    queryFn: fetchCurrentUser,
    enabled: isAuthenticated,
    staleTime: 5 * 60_000, /* User profile rarely changes mid-session */
    retry: 1,
  });

  useEffect(() => {
    if (data && !user) {
      setUser(data);
    }
  }, [data, user, setUser]);

  useEffect(() => {
    if (error instanceof ApiError && error.response.status === 401) {
      clearAuth();
    }
  }, [error, clearAuth]);

  /* No token — show inline sign-in prompt.  Production deployments
     replace this onClick handler with ``beginReauth()`` so Cognito
     handles the redirect; local dev sets a placeholder token so the
     rest of the app can run without Cognito configured. */
  if (!isAuthenticated) {
    return (
      <div className="page-content" style={{ maxWidth: 400, margin: '80px auto' }}>
        <h1 className="page-title">FORGE-MFG</h1>
        <p style={{ color: 'var(--text-secondary)', marginBottom: 'var(--space-lg)' }}>
          {cognitoEnabled
            ? 'Sign in with Cognito to continue.'
            : 'Sign in to continue.'}
        </p>
        <button
          className="btn-primary"
          style={{ width: '100%' }}
          onClick={() => {
            if (cognitoEnabled) {
              void beginReauth();
              return;
            }
            sessionStorage.setItem('forge_access_token', 'local-dev-token');
            window.location.reload();
          }}
        >
          {cognitoEnabled ? 'Sign In' : 'Sign In (Local Dev)'}
        </button>
      </div>
    );
  }

  /* Loading user profile */
  if (isLoading || !user) {
    return (
      <div className="page-content">
        <LoadingSkeleton rows={3} />
      </div>
    );
  }

  /* Auth failed */
  if (error) {
    return (
      <div className="page-content">
        <div
          style={{
            background: '#ffebee',
            border: '1px solid var(--status-error)',
            borderRadius: 'var(--radius-md)',
            padding: 'var(--space-md)',
            color: 'var(--status-error)',
          }}
          role="alert"
        >
          Failed to load user profile. Please refresh or sign in again.
        </div>
      </div>
    );
  }

  /* Authenticated + profile loaded → render child routes. */
  return <Outlet />;
}
