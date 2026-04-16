/**
 * Module:   Typed API Client
 * Purpose:  openapi-fetch runtime bound to generated schema types.
 * Rev:      0.2.0
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
 * Middleware behavior:
 *   - Authorization: Bearer <cognito_access_token>
 *   - X-Program-Id: active program (or per-request "*" override)
 *   - Idempotency-Key: UUID v4 on every POST (spec §11.3.1)
 *
 * All HTTP wrappers in endpoints.ts call this client.  The `unwrap`
 * helper converts openapi-fetch's { data, error } tuple into either
 * a resolved value or a thrown ApiError with a shape that matches
 * what useConflict.ts and page error handlers expect.
 */

import createClient, { type Middleware } from 'openapi-fetch';
import type { paths, components } from './generated/schema';

/* Export the Schemas namespace so callers can say Schemas['WorkOrderOut']. */
export type Schemas = components['schemas'];

/**
 * API error thrown by unwrap() when the server returns a non-2xx response.
 *
 * Shape is compatible with the `err.response?.status` / `err.response?.data`
 * pattern the error helpers and page handlers already use.  Keeping the
 * shape stable let us migrate from axios to openapi-fetch without touching
 * every call site.
 */
export class ApiError extends Error {
  public readonly response: {
    status: number;
    data: unknown;
  };

  constructor(status: number, data: unknown, message?: string) {
    super(message ?? `API error ${status}`);
    this.name = 'ApiError';
    this.response = { status, data };
  }
}

/* ── Middleware ── */

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    /* Bearer token */
    const token = sessionStorage.getItem('forge_access_token');
    if (token) request.headers.set('Authorization', `Bearer ${token}`);

    /* Program scope — skip if caller already set a per-request override. */
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

/**
 * Unwrap an openapi-fetch result into either a resolved value or a thrown
 * ApiError.  This is the bridge between openapi-fetch's { data, error }
 * tuple and the page-level try/catch flow inherited from the axios era.
 */
export function unwrap<T>(result: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError(
      result.response.status,
      result.error ?? result.data,
    );
  }
  return result.data as T;
}
