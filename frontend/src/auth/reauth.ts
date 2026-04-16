/**
 * Module:   Reauth Recovery
 * Purpose:  Detect REAUTH_REQUIRED, buffer the protected mutation,
 *           drive a redirect-based fresh login, and replay with the
 *           same Idempotency-Key on return.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this module: UX spec §11.3 mandates an explicit re-auth flow
 * for protected actions — the client must NOT auto-replay.  When the
 * server returns 401 REAUTH_REQUIRED, the page captures the user's
 * intent (which mutation, which Idempotency-Key, what they were
 * trying to do), redirects through Cognito with `prompt=login` to
 * force a fresh principal, and on return offers the user an explicit
 * "Confirm and Submit" action that replays the original request with
 * the SAME Idempotency-Key (so the server's idempotency cache
 * deduplicates a double-submit).
 *
 * The buffer is intentionally a single slot, not a queue.  A user can
 * only re-auth for one pending action at a time; surfacing more would
 * be an invitation to lose context.  The buffer survives the round
 * trip through Cognito by living in sessionStorage.
 *
 * Spec contracts pinned here:
 *   - same Idempotency-Key reused on retry (§9.3 / §11.3 line 1538)
 *   - `prompt=login` forces a fresh principal (§9.2 line 1481, §11.3)
 *   - no auto-replay (§11.3 explicit re-confirmation step)
 */

import type { ReauthRequiredError } from './reauth-types';

const STORAGE_KEY = 'forge_reauth_buffer';

/** Pending protected mutation snapshot persisted across the redirect. */
export interface ReauthBuffer {
  /** ISO timestamp the buffer was created — used for staleness debug. */
  createdAt: string;
  /** Same Idempotency-Key the original request used (spec §9.3). */
  idempotencyKey: string;
  /** Action label from the REAUTH envelope; drives modal copy. */
  action: 'sign_off' | 'ncr_disposition';
  /**
   * Page-supplied human description of the pending action.
   * Surfaces in the post-callback re-confirmation modal so the user
   * knows exactly what they're about to commit to.
   */
  actionSummary: string;
  /** App route to return to after the Cognito round trip. */
  returnTo: string;
  /**
   * When true, the caller wants the second-signer pattern: force a
   * fresh principal so a different user can authenticate.  Maps to
   * Cognito `prompt=login` (§9.2 line 1481).  When false, we still
   * use `prompt=login` to ensure freshness, but the modal copy is
   * "re-authenticate" rather than "different user must sign".
   */
  forceFreshPrincipal: boolean;
}

/**
 * Type guard — does this thrown error look like the backend's
 * REAUTH_REQUIRED envelope?  Shape-based on purpose so it works with
 * any error class that preserves `{ response: { status, data } }`.
 */
export function isReauthRequired(err: unknown): err is ReauthRequiredError {
  if (!err || typeof err !== 'object') return false;
  const resp = (err as { response?: unknown }).response;
  if (!resp || typeof resp !== 'object') return false;
  const r = resp as { status?: unknown; data?: unknown };
  if (r.status !== 401) return false;
  const data = r.data as { detail?: { error?: { code?: unknown } } } | undefined;
  return data?.detail?.error?.code === 'REAUTH_REQUIRED';
}

/**
 * Pull the structured envelope out of a reauth error so callers can
 * render action-specific copy.  Returns null for any non-reauth error.
 */
export function extractReauthError(
  err: unknown,
): ReauthRequiredError['response']['data']['detail']['error'] | null {
  if (!isReauthRequired(err)) return null;
  return err.response.data.detail.error;
}

/**
 * Persist the pending mutation snapshot before redirecting to Cognito.
 * sessionStorage survives the redirect because it's keyed per browsing
 * tab and the OAuth callback returns to the same tab.
 */
export function setReauthBuffer(buffer: ReauthBuffer): void {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(buffer));
}

/**
 * Read and clear the buffered mutation snapshot.  Called by the
 * AuthCallback page to find out what the user was trying to do, and
 * by Cancel buttons to discard a pending intent.
 */
export function takeReauthBuffer(): ReauthBuffer | null {
  const raw = sessionStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  sessionStorage.removeItem(STORAGE_KEY);
  try {
    return JSON.parse(raw) as ReauthBuffer;
  } catch {
    return null;
  }
}

/** Peek without clearing (the modal renders before user confirmation). */
export function peekReauthBuffer(): ReauthBuffer | null {
  const raw = sessionStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ReauthBuffer;
  } catch {
    return null;
  }
}

/**
 * Compose the Cognito hosted-login URL and redirect.  In local-dev
 * mode (no Cognito configured) we route to the inline /login page,
 * which clears the access token and prompts the user to sign in
 * again — same observable effect with zero infrastructure.
 *
 * `prompt=login` (Cognito's pass-through to the underlying OAuth
 * provider) forces a fresh interactive auth even when an SSO session
 * is still valid.  This is what makes the second-signer flow honest
 * — the first signer's session cannot silently satisfy the gate.
 */
export function beginReauth(): void {
  const cognitoDomain = import.meta.env.VITE_COGNITO_DOMAIN as
    | string
    | undefined;
  const clientId = import.meta.env.VITE_COGNITO_APP_CLIENT_ID as
    | string
    | undefined;
  const callbackUrl = `${window.location.origin}/auth/callback`;

  if (cognitoDomain && clientId) {
    /* Production / staging path: redirect to Cognito hosted login.
       prompt=login is the §9.2 line 1481 enforcement point. */
    const params = new URLSearchParams({
      client_id: clientId,
      response_type: 'code',
      scope: 'openid email profile',
      redirect_uri: callbackUrl,
      prompt: 'login',
    });
    window.location.assign(
      `${cognitoDomain}/oauth2/authorize?${params.toString()}`,
    );
    return;
  }

  /* Local-dev path: clear the token and bounce to /login.  The user
     re-signs through the placeholder login page; on return the
     AuthCallback handler picks up the buffered mutation. */
  sessionStorage.removeItem('forge_access_token');
  window.location.assign(callbackUrl);
}
