/**
 * Module:   Typed API Client
 * Purpose:  openapi-fetch client bound to generated schema types.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why openapi-fetch + generated types: This is the "generated client"
 * layer for the 5C slice.  The schema types in generated/schema.d.ts
 * are produced from the backend's live OpenAPI 3.1 spec by the
 * `npm run generate-api` script.  openapi-fetch consumes those types
 * and provides:
 *
 *   - path and method autocomplete (compile-time 404 prevention)
 *   - typed params, query strings, request/response bodies
 *   - zero runtime bundle cost beyond fetch itself
 *
 * Middleware mirrors the axios interceptor in client.ts:
 *   - Authorization: Bearer <cognito_access_token>
 *   - X-Program-Id: active program (or per-request "*" override)
 *   - Idempotency-Key: UUID v4 on every POST (spec §11.3.1)
 *
 * Hand-written HTTP wrappers in endpoints.ts use this typed client
 * under the hood, so the type surface for every response is derived
 * from the backend schema — not hand-maintained.
 */

import createClient, { type Middleware } from 'openapi-fetch';
import type { paths, components } from './generated/schema';

/* Export the Schemas namespace so callers can say Schemas['WorkOrderOut'].
   This is the bridge between the generated schema file and hand-written code. */
export type Schemas = components['schemas'];

/**
 * Auth + program-scope + idempotency middleware.
 *
 * openapi-fetch middleware runs on every request; we mutate the Request
 * before it hits fetch.
 */
const authMiddleware: Middleware = {
  async onRequest({ request }) {
    /* Bearer token */
    const token = sessionStorage.getItem('forge_access_token');
    if (token) request.headers.set('Authorization', `Bearer ${token}`);

    /* Program scope — skip if caller already set a per-request override */
    if (!request.headers.has('X-Program-Id')) {
      const programId = sessionStorage.getItem('forge_active_program');
      if (programId) request.headers.set('X-Program-Id', programId);
    }

    /* Idempotency-Key for every POST (spec §11.3.1).  Backend middleware
       rejects POSTs without this header with 400 MISSING_IDEMPOTENCY_KEY. */
    if (request.method === 'POST' && !request.headers.has('Idempotency-Key')) {
      request.headers.set('Idempotency-Key', crypto.randomUUID());
    }

    return request;
  },
};

export const typedClient = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE_URL || '',
});

typedClient.use(authMiddleware);
