/**
 * Module:   Auth Store
 * Purpose:  Zustand store for auth state, program selection, user profile.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why Zustand for auth: Spec §1.3 mandates Zustand for client state.
 * Auth state (token, user, active program) is client-side session state,
 * not server cache — so it belongs in Zustand, not React Query.
 *
 * Program selector persistence (spec §5.1): sessionStorage is the primary
 * cache (tab-scoped).  localStorage stores only the most-recently-used
 * program as a hint for initial selection on a new tab.
 */

import { create } from 'zustand';
import type { ProgramOut, Role, UserOut } from '@/types/api';

interface AuthState {
  /* Token state */
  accessToken: string | null;
  isAuthenticated: boolean;

  /* User profile (from GET /api/users/me) */
  user: UserOut | null;

  /* Active program selector (spec §5.1) */
  activeProgram: ProgramOut | null;

  /* Derived helpers */
  roles: Role[];
  hasRole: (role: Role) => boolean;
  hasAnyRole: (...roles: Role[]) => boolean;
  programIds: string[];

  /* Actions */
  setToken: (token: string) => void;
  clearAuth: () => void;
  setUser: (user: UserOut) => void;
  setActiveProgram: (program: ProgramOut) => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: sessionStorage.getItem('forge_access_token'),
  isAuthenticated: !!sessionStorage.getItem('forge_access_token'),
  user: null,
  activeProgram: null,
  roles: [],
  programIds: [],

  hasRole: (role: Role) => get().roles.includes(role),
  hasAnyRole: (...roles: Role[]) => roles.some((r) => get().roles.includes(r)),

  setToken: (token: string) => {
    sessionStorage.setItem('forge_access_token', token);
    set({ accessToken: token, isAuthenticated: true });
  },

  clearAuth: () => {
    sessionStorage.removeItem('forge_access_token');
    sessionStorage.removeItem('forge_active_program');
    set({
      accessToken: null,
      isAuthenticated: false,
      user: null,
      activeProgram: null,
      roles: [],
      programIds: [],
    });
  },

  setUser: (user: UserOut) => {
    /* The generated schema types role names as plain strings.  Narrow to
       Role here so downstream hasRole() checks stay typed. */
    const roles = user.roles.map((r) => r.name) as Role[];
    const programIds = user.program_memberships.map((p) => p.id);
    set({ user, roles, programIds });

    /* Auto-select program if none active */
    const stored = sessionStorage.getItem('forge_active_program');
    if (stored && user.program_memberships.some((p) => p.id === stored)) {
      const match = user.program_memberships.find((p) => p.id === stored);
      if (match) set({ activeProgram: match });
    } else if (user.program_memberships.length > 0) {
      /* Fall back to localStorage hint, then first program */
      const hint = localStorage.getItem('forge_last_program');
      const hinted = user.program_memberships.find((p) => p.id === hint);
      const program = hinted ?? user.program_memberships[0];
      sessionStorage.setItem('forge_active_program', program.id);
      localStorage.setItem('forge_last_program', program.id);
      set({ activeProgram: program });
    }
  },

  setActiveProgram: (program: ProgramOut) => {
    sessionStorage.setItem('forge_active_program', program.id);
    localStorage.setItem('forge_last_program', program.id);
    set({ activeProgram: program });
  },
}));
