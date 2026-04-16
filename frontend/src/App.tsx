/**
 * Module:   App
 * Purpose:  Root component with route definitions aligned to UX spec §3.2.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this route structure: Matches the URL structure from spec §3.2
 * exactly.  Route guards enforce role + program membership as a UX
 * convenience; server-side enforcement is independent (spec §3.2).
 */

import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from '@/api/query-client';
import { Layout } from '@/components/Layout';
import { AdminLayout } from '@/components/AdminLayout';
import { AuthGate } from '@/auth/AuthGate';

/* Pages — lazy loaded to keep initial bundle small.
   All pages use default exports so React.lazy works directly. */
import { lazy, Suspense } from 'react';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';

const Dashboard = lazy(() => import('@/pages/Dashboard'));
const WorkOrderList = lazy(() => import('@/pages/WorkOrderList'));
const WorkOrderDetail = lazy(() => import('@/pages/WorkOrderDetail'));
const OperationPage = lazy(() =>
  import('@/pages/OperationPage').then((m) => ({ default: m.OperationPage })),
);
const InspectionQueue = lazy(() => import('@/pages/InspectionQueue'));
const InspectionForm = lazy(() => import('@/pages/InspectionForm'));
const NCRList = lazy(() => import('@/pages/NCRList'));
const NCRDetail = lazy(() => import('@/pages/NCRDetail'));
const MaterialList = lazy(() => import('@/pages/MaterialList'));
const PartList = lazy(() => import('@/pages/PartList'));
const PartDetail = lazy(() => import('@/pages/PartDetail'));
const AdminDashboard = lazy(() => import('@/pages/AdminDashboard'));
const AdminPrograms = lazy(() => import('@/pages/AdminPrograms'));
const AdminUsers = lazy(() => import('@/pages/AdminUsers'));
const NotFound = lazy(() => import('@/pages/NotFound'));
const AuthCallback = lazy(() => import('@/auth/AuthCallback'));

function SuspenseFallback() {
  return (
    <div className="page-content">
      <LoadingSkeleton rows={6} />
    </div>
  );
}

export function App() {
  /* Route layout:
   *   PUBLIC (no AuthGate, no access token required):
   *     /login         — placeholder/Cognito hosted-login entry
   *     /auth/callback — Cognito redirect lands here; exchanges the
   *                      OAuth code for a fresh access token and then
   *                      routes the user back to their buffered mutation.
   *                      MUST be public so the GC-3 re-auth flow can
   *                      land after sessionStorage was cleared.
   *   GATED (AuthGate wraps everything under this layout route):
   *     all app routes — Dashboard, work orders, operations, NCRs, admin,
   *                      etc.  AuthGate still enforces the "must have
   *                      access token + loaded user profile" contract.
   *
   * Fixing the earlier structure where the callback was inside AuthGate
   * meant the clear-token-then-redirect-to-callback step was structurally
   * unreachable — AuthGate blocked on the empty token before /auth/callback
   * could render.
   */
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Suspense fallback={<SuspenseFallback />}>
          <Routes>
            {/* Public routes — no AuthGate. */}
            <Route path="/login" element={<LoginPlaceholder />} />
            <Route path="/auth/callback" element={<AuthCallback />} />

            {/* Gated routes — AuthGate wraps via its Outlet. */}
            <Route element={<AuthGate />}>
              <Route element={<Layout />}>
                <Route index element={<Dashboard />} />
                <Route path="work-orders" element={<WorkOrderList />} />
                <Route path="work-orders/:id" element={<WorkOrderDetail />} />
                <Route
                  path="work-orders/:woId/ops/:opId"
                  element={<OperationPage />}
                />
                <Route
                  path="work-orders/:woId/ops/:opId/inspect"
                  element={<InspectionForm />}
                />
                <Route path="inspections" element={<InspectionQueue />} />
                <Route path="ncrs" element={<NCRList />} />
                <Route path="ncrs/:id" element={<NCRDetail />} />
                <Route path="materials" element={<MaterialList />} />
                <Route path="parts" element={<PartList />} />
                <Route path="parts/:id" element={<PartDetail />} />

                {/* Admin routes use a nested layout with dark header */}
                <Route path="admin" element={<AdminLayout />}>
                  <Route index element={<AdminDashboard />} />
                  <Route path="users" element={<AdminUsers />} />
                  <Route path="programs" element={<AdminPrograms />} />
                </Route>
              </Route>
            </Route>

            <Route path="*" element={<NotFound />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

/**
 * Placeholder login page — real Cognito integration is a separate concern.
 * In local dev with auth_mode=local, this provides a minimal session bootstrap.
 */
function LoginPlaceholder() {
  return (
    <div className="page-content" style={{ maxWidth: 400, margin: '80px auto' }}>
      <h1 className="page-title">FORGE-MFG</h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: 'var(--space-lg)' }}>
        Sign in to continue. In local development, the backend runs with
        auth_mode=local (no Cognito required).
      </p>
      <button
        className="btn-primary"
        style={{ width: '100%' }}
        onClick={() => {
          /* Local dev: set a placeholder token and redirect */
          sessionStorage.setItem('forge_access_token', 'local-dev-token');
          window.location.href = '/';
        }}
      >
        Sign In (Local Dev)
      </button>
    </div>
  );
}
