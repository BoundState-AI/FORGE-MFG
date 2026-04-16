/**
 * Module:   AuthCallback
 * Purpose:  Landing page for the Cognito redirect after fresh login.
 *           Reads the buffered protected-action snapshot and routes
 *           the user back to the originating page so they can
 *           explicitly confirm-and-submit (no auto-replay).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why no automatic replay: UX spec §11.3 lines 1537-1538 are
 * explicit — "the client does not automatically replay the original
 * request" — because non-idempotent state transitions (sign-off,
 * disposition) must always pass under fresh user intent.  This page
 * routes back to the originating screen with a one-shot URL flag so
 * the page knows to surface its confirm-and-submit modal again.
 *
 * Local-dev path: in the absence of a real Cognito redirect, we
 * write a placeholder access token (matching the inline Sign-In flow
 * in App.tsx) so the AuthGate doesn't kick the user out.  Production
 * Cognito flows will replace this with the real token-exchange step;
 * the token-handling boundary stays inside this one component.
 */

import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { peekReauthBuffer } from './reauth';

export default function AuthCallback() {
  const navigate = useNavigate();

  useEffect(() => {
    /* Local-dev: ensure we have an access token for the AuthGate.
       Production Cognito flows replace this block with the OAuth
       code-exchange step; the rest of the file is identical. */
    if (!sessionStorage.getItem('forge_access_token')) {
      sessionStorage.setItem('forge_access_token', 'local-dev-token');
    }

    /* Decide where to send the user.  If a buffered protected action
       exists, route back to its origin with a one-shot URL flag so
       the page re-opens its re-confirmation modal.  No buffer ⇒
       the user came back from a session-expiry login, not a re-auth
       round trip; just go to the dashboard. */
    const buffer = peekReauthBuffer();
    if (buffer) {
      const sep = buffer.returnTo.includes('?') ? '&' : '?';
      navigate(`${buffer.returnTo}${sep}reauth=resume`, { replace: true });
    } else {
      navigate('/', { replace: true });
    }
  }, [navigate]);

  /* The redirect is synchronous on the next tick, so this view is
     visible only briefly.  Keep it intentional — a blank flash would
     read as a broken redirect. */
  return (
    <div
      className="page-content"
      style={{ textAlign: 'center', padding: 'var(--space-2xl)' }}
    >
      <p style={{ color: 'var(--text-secondary)' }}>Returning…</p>
    </div>
  );
}
