/**
 * Module:   Reauth Recovery
 * Purpose:  Detect REAUTH_REQUIRED, buffer the protected mutation,
 *           drive a redirect-based fresh login with Cognito PKCE, and
 *           replay with the same Idempotency-Key on return.
 * Rev:      0.2.0
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
 * PKCE: Authorization Code + PKCE is the hosted-UI recommended flow
 * for single-page apps with Cognito.  We generate a cryptographically
 * random code_verifier before the redirect, send the S256 hash as
 * code_challenge on the authorize call, and redeem the code_verifier
 * alongside the authorization code on the token-exchange call.  The
 * verifier never leaves the browser.  ``AuthCallback.tsx`` handles
 * the exchange; ``beginReauth()`` prepares and persists the verifier.
 *
 * Spec contracts pinned here:
 *   - same Idempotency-Key reused on retry (§9.3 / §11.3 line 1538)
 *   - `prompt=login` forces a fresh principal (§9.2 line 1481, §11.3)
 *   - no auto-replay (§11.3 explicit re-confirmation step)
 */

import type { ReauthRequiredError } from './reauth-types';

const STORAGE_KEY_BUFFER = 'forge_reauth_buffer';
const STORAGE_KEY_PKCE_VERIFIER = 'forge_pkce_verifier';
const STORAGE_KEY_OAUTH_STATE = 'forge_oauth_state';

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
  sessionStorage.setItem(STORAGE_KEY_BUFFER, JSON.stringify(buffer));
}

/**
 * Read and clear the buffered mutation snapshot.  Called by the
 * AuthCallback page to find out what the user was trying to do, and
 * by Cancel buttons to discard a pending intent.
 */
export function takeReauthBuffer(): ReauthBuffer | null {
  const raw = sessionStorage.getItem(STORAGE_KEY_BUFFER);
  if (!raw) return null;
  sessionStorage.removeItem(STORAGE_KEY_BUFFER);
  try {
    return JSON.parse(raw) as ReauthBuffer;
  } catch {
    return null;
  }
}

/** Peek without clearing (the modal renders before user confirmation). */
export function peekReauthBuffer(): ReauthBuffer | null {
  const raw = sessionStorage.getItem(STORAGE_KEY_BUFFER);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ReauthBuffer;
  } catch {
    return null;
  }
}

/* ── PKCE helpers ──
 *
 * RFC 7636 §4.1 + §4.2: the verifier is 43-128 chars from the
 * [A-Z a-z 0-9 -._~] set; the S256 challenge is the base64url(no-padding)
 * encoding of SHA-256(verifier).  We generate 32 random bytes and
 * base64url-encode them to get a 43-char verifier.
 */

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary)
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

function generateCodeVerifier(): string {
  const random = new Uint8Array(32);
  crypto.getRandomValues(random);
  return base64UrlEncode(random);
}

async function computeCodeChallenge(verifier: string): Promise<string> {
  const encoded = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest('SHA-256', encoded);
  return base64UrlEncode(new Uint8Array(digest));
}

/** Read + clear the PKCE verifier persisted by beginReauth(). */
export function takePkceVerifier(): string | null {
  const v = sessionStorage.getItem(STORAGE_KEY_PKCE_VERIFIER);
  if (v !== null) sessionStorage.removeItem(STORAGE_KEY_PKCE_VERIFIER);
  return v;
}

/** Read + clear the OAuth CSRF state persisted by beginReauth(). */
export function takeOauthState(): string | null {
  const s = sessionStorage.getItem(STORAGE_KEY_OAUTH_STATE);
  if (s !== null) sessionStorage.removeItem(STORAGE_KEY_OAUTH_STATE);
  return s;
}

/**
 * Compose the Cognito hosted-login URL and redirect.
 *
 * Production path: Authorization Code + PKCE flow.  We generate a
 * code_verifier + code_challenge (S256), persist the verifier to
 * sessionStorage, and add ``code_challenge`` / ``code_challenge_method``
 * to the authorize URL.  ``AuthCallback`` reads the verifier back on
 * return and redeems it alongside the authorization code.  PKCE is
 * the right posture for SPAs because there is no client secret to
 * safely hold in the browser.
 *
 * `prompt=login` forces a fresh interactive auth even when an SSO
 * session is still valid — the second-signer UX depends on this so
 * the first signer's session cannot silently satisfy the gate.
 *
 * Local-dev path: no Cognito domain configured → clear the access
 * token and bounce to ``/auth/callback``, which will fall back to the
 * placeholder-token flow.  Same observable outcome, zero
 * infrastructure.
 */
export async function beginReauth(): Promise<void> {
  const cognitoDomain = import.meta.env.VITE_COGNITO_DOMAIN as
    | string
    | undefined;
  const clientId = import.meta.env.VITE_COGNITO_APP_CLIENT_ID as
    | string
    | undefined;
  const callbackUrl = `${window.location.origin}/auth/callback`;

  if (cognitoDomain && clientId) {
    /* Production / staging path: Authorization Code + PKCE.
       prompt=login is the §9.2 line 1481 enforcement point. */
    const verifier = generateCodeVerifier();
    const challenge = await computeCodeChallenge(verifier);
    const state = generateCodeVerifier(); /* reused helper; any 43 random chars */
    sessionStorage.setItem(STORAGE_KEY_PKCE_VERIFIER, verifier);
    sessionStorage.setItem(STORAGE_KEY_OAUTH_STATE, state);

    const params = new URLSearchParams({
      client_id: clientId,
      response_type: 'code',
      scope: 'openid email profile',
      redirect_uri: callbackUrl,
      prompt: 'login',
      code_challenge: challenge,
      code_challenge_method: 'S256',
      state,
    });
    window.location.assign(
      `${cognitoDomain}/oauth2/authorize?${params.toString()}`,
    );
    return;
  }

  /* Local-dev path: clear the token and bounce to the callback.  The
     callback page will see no ?code query param and fall back to the
     placeholder-token flow. */
  sessionStorage.removeItem('forge_access_token');
  window.location.assign(callbackUrl);
}

/**
 * Exchange an OAuth authorization code for a fresh Cognito access
 * token.  Called by ``AuthCallback`` on return from the hosted UI.
 *
 * Returns the access token on success, or null if:
 *   - the exchange fails (network / 4xx / 5xx)
 *   - required env vars are missing
 *   - the PKCE verifier is missing (replay attack, stale callback,
 *     opened the URL in a different tab)
 *
 * On success, the caller writes the token to sessionStorage.  We
 * return it instead of writing directly so the caller can decide
 * whether to propagate other fields (refresh token, id token) in
 * future slices.
 */
export async function exchangeCodeForToken(
  code: string,
): Promise<string | null> {
  const cognitoDomain = import.meta.env.VITE_COGNITO_DOMAIN as
    | string
    | undefined;
  const clientId = import.meta.env.VITE_COGNITO_APP_CLIENT_ID as
    | string
    | undefined;
  if (!cognitoDomain || !clientId) return null;

  const verifier = takePkceVerifier();
  if (!verifier) return null;

  const callbackUrl = `${window.location.origin}/auth/callback`;
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: clientId,
    code,
    redirect_uri: callbackUrl,
    code_verifier: verifier,
  });

  try {
    const resp = await fetch(`${cognitoDomain}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!resp.ok) return null;
    const json = (await resp.json()) as {
      access_token?: string;
    };
    return json.access_token ?? null;
  } catch {
    return null;
  }
}
