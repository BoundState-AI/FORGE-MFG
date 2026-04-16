/**
 * Module:   AuthCallback
 * Purpose:  Landing page for the Cognito redirect after fresh login.
 *           Exchanges the OAuth code for a fresh access token (PKCE),
 *           stores it, then routes the user back to the originating
 *           page so they can explicitly confirm-and-submit.
 * Rev:      0.2.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why no automatic replay: UX spec §11.3 lines 1537-1538 are
 * explicit — "the client does not automatically replay the original
 * request" — because non-idempotent state transitions (sign-off,
 * disposition) must always pass under fresh user intent.  This page
 * runs the token exchange, then routes back to the originating
 * screen with a one-shot URL flag so the page knows to surface its
 * confirm-and-submit modal again.
 *
 * Flow variants:
 *
 *   (A) Cognito path — we arrived with ?code=<auth_code>&state=<s>:
 *       redeem the code via PKCE (code_verifier stashed by
 *       beginReauth), stash the resulting access_token in
 *       sessionStorage, navigate back per the buffered action.
 *
 *   (B) Cognito error — we arrived with ?error=<reason>:
 *       clear the buffer (so a resume-redirect on the origin page
 *       doesn't prompt), surface the error briefly, then route to
 *       Dashboard so the user isn't stranded.
 *
 *   (C) Local-dev path — no ?code and no VITE_COGNITO_DOMAIN:
 *       write the placeholder access token (matching the inline
 *       /login button) and navigate back.  This preserves the
 *       pre-PKCE behavior for developers running without Cognito.
 *
 * Security: the OAuth ``state`` parameter is verified against the
 * value stashed on ``beginReauth()`` so a third-party-initiated
 * callback cannot silently take over the buffered mutation.  PKCE
 * itself guarantees that only this browser tab (holding the
 * verifier) can redeem this ``code``.
 */

import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  exchangeCodeForToken,
  peekReauthBuffer,
  takeOauthState,
  takeReauthBuffer,
} from './reauth';
import { useAuthStore } from './store';

export default function AuthCallback() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function run(): Promise<void> {
      const code = searchParams.get('code');
      const state = searchParams.get('state');
      const oauthError = searchParams.get('error');

      /* (B) Cognito returned an error — surface it, clear the buffer,
         and route home so the user isn't stranded on /auth/callback. */
      if (oauthError) {
        takeReauthBuffer();
        takeOauthState();
        setErrorMessage(
          searchParams.get('error_description') ?? oauthError,
        );
        /* Give the user a beat to see the error before redirecting. */
        window.setTimeout(() => {
          if (!cancelled) navigate('/', { replace: true });
        }, 2500);
        return;
      }

      /* (A) Cognito path — ?code present, redeem via PKCE. */
      if (code) {
        const expectedState = takeOauthState();
        if (expectedState !== null && state !== expectedState) {
          /* CSRF guard — mismatched state means this callback was
             initiated by someone else or we're looking at a stale
             URL.  Drop the buffer and bail. */
          takeReauthBuffer();
          setErrorMessage(
            'OAuth state mismatch — re-authentication was not completed.',
          );
          window.setTimeout(() => {
            if (!cancelled) navigate('/', { replace: true });
          }, 2500);
          return;
        }

        const accessToken = await exchangeCodeForToken(code);
        if (!accessToken) {
          takeReauthBuffer();
          setErrorMessage(
            'Could not complete re-authentication.  Please try again.',
          );
          window.setTimeout(() => {
            if (!cancelled) navigate('/', { replace: true });
          }, 2500);
          return;
        }
        /* GC-3 review round 3 finding 2: route the token through the
           Zustand setToken action so `isAuthenticated` flips in the
           same atomic update as the sessionStorage write.  A direct
           sessionStorage.setItem leaves the store unchanged — AuthGate
           reads `useAuthStore().isAuthenticated`, so the unauth branch
           would render and the post-redirect resume modal would never
           get the chance to fire. */
        useAuthStore.getState().setToken(accessToken);
      } else {
        /* (C) Local-dev fallback: no ?code, no Cognito domain
           configured.  Restore the placeholder token so downstream
           routes can render.  Production deployments always arrive
           with a ?code; getting here in production means someone
           navigated to /auth/callback directly.

           Same setToken posture as the PKCE happy path so AuthGate's
           Zustand-backed isAuthenticated flips in lockstep. */
        if (!useAuthStore.getState().isAuthenticated) {
          useAuthStore.getState().setToken('local-dev-token');
        }
      }

      if (cancelled) return;

      /* Route back.  If a buffer exists, drop a one-shot ``reauth=resume``
         flag on the URL so the origin page re-opens its confirm-and-submit
         modal.  No buffer ⇒ user came back from a session-expiry login,
         not a re-auth round trip; go to Dashboard. */
      const buffer = peekReauthBuffer();
      if (buffer) {
        const sep = buffer.returnTo.includes('?') ? '&' : '?';
        navigate(`${buffer.returnTo}${sep}reauth=resume`, { replace: true });
      } else {
        navigate('/', { replace: true });
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
  }, [navigate, searchParams]);

  return (
    <div
      className="page-content"
      style={{ textAlign: 'center', padding: 'var(--space-2xl)' }}
    >
      {errorMessage ? (
        <div
          role="alert"
          style={{
            maxWidth: 480,
            margin: '0 auto',
            padding: 'var(--space-md)',
            background: '#ffebee',
            border: '1px solid var(--status-error)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--status-error)',
          }}
        >
          {errorMessage}
        </div>
      ) : (
        <p style={{ color: 'var(--text-secondary)' }}>Returning…</p>
      )}
    </div>
  );
}
