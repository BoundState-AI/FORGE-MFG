/**
 * Module:   ProgramSelector
 * Purpose:  Dropdown for selecting the active program scope.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Reads program memberships from the auth store (spec §5.1).
 * On change, calls setActiveProgram and invalidates the React Query
 * cache so all program-scoped queries refetch with the new scope.
 */

import { useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '@/auth/store';

export function ProgramSelector() {
  const user = useAuthStore((s) => s.user);
  const activeProgram = useAuthStore((s) => s.activeProgram);
  const setActiveProgram = useAuthStore((s) => s.setActiveProgram);
  const queryClient = useQueryClient();

  const programs = user?.program_memberships ?? [];

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      const selected = programs.find((p) => p.id === e.target.value);
      if (!selected) return;

      setActiveProgram(selected);

      /*
       * Invalidate all queries so program-scoped data refetches.
       * This is the simplest correct approach — individual query keys
       * already include program_id, so stale entries are replaced.
       */
      queryClient.invalidateQueries();
    },
    [programs, setActiveProgram, queryClient],
  );

  /* Nothing to render if the user has no programs */
  if (programs.length === 0) return null;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-sm)' }}>
      <label
        htmlFor="program-selector"
        style={{
          fontSize: 'var(--font-size-label)',
          color: 'var(--text-secondary)',
          fontWeight: 600,
          whiteSpace: 'nowrap',
        }}
      >
        Program:
      </label>
      <select
        id="program-selector"
        value={activeProgram?.id ?? ''}
        onChange={handleChange}
        style={{
          padding: 'var(--space-xs) var(--space-sm)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-md)',
          backgroundColor: 'var(--bg-surface)',
          color: 'var(--text-primary)',
          fontSize: 'var(--font-size-label)',
          cursor: 'pointer',
          minWidth: '180px',
        }}
      >
        {programs.map((p) => (
          <option key={p.id} value={p.id}>
            {p.code} — {p.name}
          </option>
        ))}
      </select>
    </div>
  );
}
