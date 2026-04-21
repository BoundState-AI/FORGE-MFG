// Module:   k6 Smoke Script (6A-3C2)
// Purpose:  Single-iteration sanity probe against staging.  Used
//           for local debugging and as the fastest way to verify a
//           candidate token + seed ids are usable before the full
//           read_paths.js load pack runs.  NOT invoked by the
//           perf-verification workflow — the workflow only runs
//           read_paths.js, which exercises the same endpoints with
//           a real ramp.
// Rev:      0.1.0
//
// BoundState Proprietary
// Co-Authored-By: Dr. Joseph Wilde, BoundState AI
// Co-Authored-By: Claude "Beth" (Opus 4.7)

import http from 'k6/http';
import { check } from 'k6';

export const options = {
  vus: 1,
  iterations: 1,
  // No thresholds here — this is a smoke probe, not a load gate.
  // The real thresholds live on read_paths.js.
};

function requireEnv(name) {
  const v = __ENV[name];
  if (!v || v.length === 0) {
    throw new Error(`required env var ${name} is empty`);
  }
  return v;
}

export default function () {
  const baseUrl = requireEnv('BASE_URL');
  const token = requireEnv('TOKEN');
  const seedWoId = requireEnv('SEED_WO_ID');
  const seedProgramId = requireEnv('SEED_PROGRAM_ID');

  const authHeaders = {
    Authorization: `Bearer ${token}`,
    'X-Program-Id': seedProgramId,
  };

  // 1. Unauthenticated health
  const r1 = http.get(`${baseUrl}/api/health`, {
    tags: { endpoint: 'health' },
  });
  check(r1, {
    'health returns 200': (r) => r.status === 200,
  });

  // 2. Authenticated WO list
  const r2 = http.get(`${baseUrl}/api/work-orders?page=1&page_size=50`, {
    headers: authHeaders,
    tags: { endpoint: 'wo_list' },
  });
  check(r2, {
    'wo_list returns 200': (r) => r.status === 200,
    'wo_list body has items': (r) => {
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body.items);
      } catch (_) {
        return false;
      }
    },
  });

  // 3. Authenticated WO detail
  const r3 = http.get(`${baseUrl}/api/work-orders/${seedWoId}`, {
    headers: authHeaders,
    tags: { endpoint: 'wo_detail' },
  });
  check(r3, {
    'wo_detail returns 200': (r) => r.status === 200,
  });

  // 4. Dashboard — single-program X-Program-Id required; * rejected
  const r4 = http.get(`${baseUrl}/api/dashboard/summary`, {
    headers: authHeaders,
    tags: { endpoint: 'dashboard' },
  });
  check(r4, {
    'dashboard returns 200': (r) => r.status === 200,
  });
}
