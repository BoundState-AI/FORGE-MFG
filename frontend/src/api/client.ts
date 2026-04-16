/**
 * Module:   API Client
 * Purpose:  Axios instance with auth + program-scope interceptors.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why axios: Consistent interceptor model for injecting Authorization
 * and X-Program-Id headers on every request (spec §6.7.3).  The
 * interceptor reads the active program from sessionStorage so cache
 * keys remain consistent with the wire-level contract.
 */

import axios from 'axios';

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  headers: { 'Content-Type': 'application/json' },
});

/**
 * Request interceptor: inject Authorization + X-Program-Id headers.
 *
 * Spec §6.7.3: The request interceptor reads the active program from
 * sessionStorage and injects X-Program-Id on every request.  List pages
 * that support "All" set a per-request override to "*".
 */
apiClient.interceptors.request.use((config) => {
  const token = sessionStorage.getItem('forge_access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  /* Per-request program override (set by list pages using "All") */
  if (!config.headers['X-Program-Id']) {
    const programId = sessionStorage.getItem('forge_active_program');
    if (programId) {
      config.headers['X-Program-Id'] = programId;
    }
  }

  return config;
});

export default apiClient;
