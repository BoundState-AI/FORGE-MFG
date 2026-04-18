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
import type { ProgramOut, Role, UserMeOut } from '@/types/api';

interface AuthState {
  /* Token state */
  accessToken: string | null;
  isAuthenticated: boolean;

  /* User profile (from GET /api/users/me) */
  user: UserMeOut | null;

  /* Active program selector (spec §5.1) */
  activeProgram: ProgramOut | null;

  /* Derived helpers */
  roles: Role[];
  hasRole: (role: Role) => boolean;
  hasAnyRole: (...roles: Role[]) => boolean;
  programIds: string[];

  /* GC-4: live closure-enable governance flag.  Sourced from
     /api/users/me (server-side `Settings.closure_enabled`).  Default
     false so the UI defaults to "closed" state copy until the
     bootstrap fetch confirms otherwise. */
  closureEnabled: boolean;

  /* Actions */
  setToken: (token: string) => void;
  clearAuth: () => void;
  setUser: (user: UserMeOut) => void;
  setActiveProgram: (program: ProgramOut) => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  accessToken: sessionStorage.getItem('forge_access_token'),
  isAuthenticated: !!sessionStorage.getItem('forge_access_token'),
  user: null,
  activeProgram: null,
  roles: [],
  programIds: [],
  closureEnabled: false,

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
      closureEnabled: false,
    });
  },

  setUser: (user: UserMeOut) => {
    /* The generated schema types role names as plain strings.  Narrow to
       Role here so downstream hasRole() checks stay typed.

       UserMeOut declares roles/program_memberships as optional in the
       generated schema (Pydantic default_factory exports as optional);
       fetchCurrentUser already coerces them to [], but we keep the
       defensive narrowing here so this setter is honest if a future
       caller skips the wrapper. */
    const userRoles = user.roles ?? [];
    const userPrograms = user.program_memberships ?? [];
    const roles = userRoles.map((r) => r.name) as Role[];
    const programIds = userPrograms.map((p) => p.id);
    set({
      user,
      roles,
      programIds,
      // GC-4: mirror the server's closure-enable flag into the store
      // so subscribers can read it via a single hook call.
      closureEnabled: user.closure_enabled ?? false,
    });

    /* Auto-select program if none active */
    const stored = sessionStorage.getItem('forge_active_program');
    if (stored && userPrograms.some((p) => p.id === stored)) {
      const match = userPrograms.find((p) => p.id === stored);
      if (match) set({ activeProgram: match });
    } else if (userPrograms.length > 0) {
      /* Fall back to localStorage hint, then first program */
      const hint = localStorage.getItem('forge_last_program');
      const hinted = userPrograms.find((p) => p.id === hint);
      const program = hinted ?? userPrograms[0];
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
