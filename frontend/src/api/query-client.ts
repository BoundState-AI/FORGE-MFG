/**
 * Module:   React Query Client
 * Purpose:  Global QueryClient with FORGE-MFG defaults.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why these defaults: Spec §6.1 mandates server-confirmed state — no
 * optimistic updates on manufacturing state changes.  We keep staleTime
 * at 30s for detail views (spec §6.2 polling interval).
 */

import { QueryClient } from '@tanstack/react-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: true,
    },
    mutations: {
      retry: 0,
    },
  },
});
