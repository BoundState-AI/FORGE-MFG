/**
 * Module:   AuthGate
 * Purpose:  Top-level auth wrapper that loads the user profile on mount.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this gate: The app needs the user profile (roles, programs) before
 * rendering any route.  This component fetches /api/users/me on mount
 * and hydrates the auth store.  If no token is present, it redirects to
 * the login page.
 */

import { useEffect, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from './store';
import { fetchCurrentUser } from '@/api/endpoints';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';

interface AuthGateProps {
  children: ReactNode;
}

export function AuthGate({ children }: AuthGateProps) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setUser = useAuthStore((s) => s.setUser);
  const user = useAuthStore((s) => s.user);

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

  /* No token — show login */
  if (!isAuthenticated) {
    /* In a real app this would redirect to Cognito hosted login.
       For local dev, we show the inline login placeholder. */
    return (
      <div className="page-content" style={{ maxWidth: 400, margin: '80px auto' }}>
        <h1 className="page-title">FORGE-MFG</h1>
        <p style={{ color: 'var(--text-secondary)', marginBottom: 'var(--space-lg)' }}>
          Sign in to continue.
        </p>
        <button
          className="btn-primary"
          style={{ width: '100%' }}
          onClick={() => {
            sessionStorage.setItem('forge_access_token', 'local-dev-token');
            window.location.reload();
          }}
        >
          Sign In (Local Dev)
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

  return <>{children}</>;
}
