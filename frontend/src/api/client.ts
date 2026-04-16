/**
 * Module:   API Client
 * Purpose:  Axios instance with auth, program-scope, and idempotency interceptors.
 * Rev:      0.2.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why axios: Consistent interceptor model for injecting Authorization,
 * X-Program-Id, and Idempotency-Key headers on every request.
 *
 * Idempotency-Key (spec §11.3.1): The backend middleware rejects every
 * POST request that lacks this header with 400 MISSING_IDEMPOTENCY_KEY.
 * The key is a UUID v4 generated per-request.  This interceptor auto-
 * generates it for all POSTs so individual endpoint wrappers never need
 * to remember it.  Callers that need to reuse a key across retries
 * (e.g., post-REAUTH_REQUIRED replay in GC-3) can pre-set the header
 * on the request config to suppress auto-generation.
 */

import axios from 'axios';

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  headers: { 'Content-Type': 'application/json' },
});

/**
 * Request interceptor: inject Authorization, X-Program-Id, and Idempotency-Key.
 */
apiClient.interceptors.request.use((config) => {
  /* ── Auth ── */
  const token = sessionStorage.getItem('forge_access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  /* ── Program scope (spec §6.7.3) ──
     Per-request override (set by list pages using "All") takes precedence. */
  if (!config.headers['X-Program-Id']) {
    const programId = sessionStorage.getItem('forge_active_program');
    if (programId) {
      config.headers['X-Program-Id'] = programId;
    }
  }

  /* ── Idempotency-Key (spec §11.3.1) ──
     Auto-generate a UUID v4 for every POST unless already set.
     The backend idempotency middleware requires this header on all
     mutating POSTs and returns 400 MISSING_IDEMPOTENCY_KEY without it. */
  if (config.method === 'post' && !config.headers['Idempotency-Key']) {
    config.headers['Idempotency-Key'] = crypto.randomUUID();
  }

  return config;
});

export default apiClient;
