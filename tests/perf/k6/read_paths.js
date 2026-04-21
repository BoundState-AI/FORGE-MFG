// Module:   k6 Read-Path Load Pack (6A-3C2)
// Purpose:  Baseline read-path load against deployed staging.
//           Four endpoints, ~2-minute ramp, slice-owned latency
//           guardrails as thresholds.  The perf-verification
//           workflow (.github/workflows/perf-verification.yml)
//           runs this script; k6 exit 99 on threshold breach
//           propagates to the workflow's threshold gate.
// Rev:      0.1.0
//
// BoundState Proprietary
// Co-Authored-By: Dr. Joseph Wilde, BoundState AI
// Co-Authored-By: Claude "Beth" (Opus 4.7)
//
// What this verifies (spec §11 Month 6 perf testing):
//   - /api/health:              ALB + API liveness baseline
//   - /api/work-orders (list):  paginated list read with program scope
//   - /api/work-orders/{id}:    detail read with program scope
//   - /api/dashboard/summary:   dashboard read with SINGLE program id
//                                (/ dashboard rejects X-Program-Id: * )
//
// Program-scope contract (forge/api/routes/dashboard.py:51):
//   Every authenticated read uses the same single SEED_PROGRAM_ID.
//   We do NOT use `*` anywhere — it would fail on dashboard and it
//   is not representative of a real operator's load shape.
//
// Load profile — baseline only:
//   Ramp 0→5 VUs over 30s, sustain 10 VUs for 60s, ramp 10→0 VUs
//   over 30s.  ~2-minute wall clock.  Staging cluster is 2×2-vCPU
//   API tasks (6A-1 scaffold); 10 VUs sits well below saturation.
//   This run measures latency floor + variance, not the knee.
//
// Thresholds — slice-owned guardrails:
//   These are NOT spec-mandated latency SLOs.  Spec §11 names
//   performance testing but does not prescribe numbers for Phase 1.
//   If a later spec rev or the ACC-09 AO package defines real SLOs,
//   the numbers below move to match.  See
//   docs/ops/6A_3C2_perf_and_operator_timing.md §5.

import http from 'k6/http';
import { check } from 'k6';

// ---- Env resolution (hard-fail on missing) ----

function requireEnv(name) {
  const v = __ENV[name];
  if (!v || v.length === 0) {
    throw new Error(
      `required env var ${name} is empty — the perf workflow must ` +
      `export BASE_URL, TOKEN, SEED_WO_ID, SEED_PROGRAM_ID before ` +
      `invoking k6 run (see .github/workflows/perf-verification.yml)`,
    );
  }
  return v;
}

const BASE_URL = requireEnv('BASE_URL');
const TOKEN = requireEnv('TOKEN');
const SEED_WO_ID = requireEnv('SEED_WO_ID');
const SEED_PROGRAM_ID = requireEnv('SEED_PROGRAM_ID');

const AUTH_HEADERS = {
  Authorization: `Bearer ${TOKEN}`,
  'X-Program-Id': SEED_PROGRAM_ID,
};

// ---- Options ----

export const options = {
  // Baseline ramp — the only profile this slice ships.  If soak
  // or spike profiles land later they get their own script file
  // and their own workflow input.
  stages: [
    { duration: '30s', target: 5 },
    { duration: '60s', target: 10 },
    { duration: '30s', target: 0 },
  ],

  thresholds: {
    // Error-rate guardrail — real operator traffic shouldn't see
    // >1% failures under this load.
    http_req_failed: ['rate<0.01'],

    // Per-endpoint p95 latency guardrails.  Tagged via
    // `tags: { endpoint: '<name>' }` on each request so the same
    // metric name can express per-endpoint thresholds.
    'http_req_duration{endpoint:health}':     ['p(95)<500'],
    'http_req_duration{endpoint:wo_list}':    ['p(95)<1500'],
    'http_req_duration{endpoint:wo_detail}':  ['p(95)<1000'],
    'http_req_duration{endpoint:dashboard}':  ['p(95)<2000'],
  },
};

// ---- Probe functions ----

function probeHealth() {
  // No auth headers on health — matches the staging smoke harness.
  const r = http.get(`${BASE_URL}/api/health`, {
    tags: { endpoint: 'health' },
  });
  check(r, {
    'health 200': (x) => x.status === 200,
  });
}

function probeWorkOrderList() {
  const r = http.get(
    `${BASE_URL}/api/work-orders?page=1&page_size=50`,
    {
      headers: AUTH_HEADERS,
      tags: { endpoint: 'wo_list' },
    },
  );
  check(r, {
    'wo_list 200': (x) => x.status === 200,
  });
}

function probeWorkOrderDetail() {
  const r = http.get(
    `${BASE_URL}/api/work-orders/${SEED_WO_ID}`,
    {
      headers: AUTH_HEADERS,
      tags: { endpoint: 'wo_detail' },
    },
  );
  check(r, {
    'wo_detail 200': (x) => x.status === 200,
  });
}

function probeDashboard() {
  // Single-program X-Program-Id is required here — dashboard route
  // is not on the multi-program allowlist.  Using `*` would 400.
  const r = http.get(`${BASE_URL}/api/dashboard/summary`, {
    headers: AUTH_HEADERS,
    tags: { endpoint: 'dashboard' },
  });
  check(r, {
    'dashboard 200': (x) => x.status === 200,
  });
}

// ---- Iteration ----

export default function () {
  // Call order matches what an operator doing a WO review would
  // actually trigger: health (implicit page nav), dashboard,
  // WO list, then drill into one WO detail.  Each iteration hits
  // all four endpoints once so per-endpoint counts stay balanced.
  probeHealth();
  probeDashboard();
  probeWorkOrderList();
  probeWorkOrderDetail();
}
