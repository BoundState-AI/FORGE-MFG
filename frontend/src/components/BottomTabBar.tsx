/**
 * Module:   BottomTabBar
 * Purpose:  Floor-mode (≤768px) bottom navigation with 4+More pattern.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why 4+More: Spec §7.2 mandates max 4 primary tabs plus a More
 * overflow.  Tab allocation is role-specific per the spec table.
 */

import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useAuthStore } from '@/auth/store';
import type { Role } from '@/types/api';

interface TabDef {
  label: string;
  to: string;
}

const TABS_BY_ROLE: Record<Role, { primary: TabDef[]; more: TabDef[] }> = {
  operator: {
    primary: [
      { label: 'Dashboard', to: '/' },
      { label: 'Work Orders', to: '/work-orders' },
      { label: 'Materials', to: '/materials' },
      { label: 'Parts', to: '/parts' },
    ],
    more: [],
  },
  inspector: {
    primary: [
      { label: 'Dashboard', to: '/' },
      { label: 'Inspections', to: '/inspections' },
      { label: 'Work Orders', to: '/work-orders' },
      { label: 'NCRs', to: '/ncrs' },
    ],
    more: [{ label: 'Materials', to: '/materials' }],
  },
  engineer: {
    primary: [
      { label: 'Dashboard', to: '/' },
      { label: 'Work Orders', to: '/work-orders' },
      { label: 'NCRs', to: '/ncrs' },
      { label: 'Inspections', to: '/inspections' },
    ],
    more: [
      { label: 'Materials', to: '/materials' },
      { label: 'Parts', to: '/parts' },
    ],
  },
  qa_manager: {
    primary: [
      { label: 'Dashboard', to: '/' },
      { label: 'Work Orders', to: '/work-orders' },
      { label: 'NCRs', to: '/ncrs' },
      { label: 'Inspections', to: '/inspections' },
    ],
    more: [
      { label: 'Materials', to: '/materials' },
      { label: 'Parts', to: '/parts' },
    ],
  },
  program_manager: {
    primary: [
      { label: 'Dashboard', to: '/' },
      { label: 'Work Orders', to: '/work-orders' },
      { label: 'NCRs', to: '/ncrs' },
      { label: 'Parts', to: '/parts' },
    ],
    more: [{ label: 'Materials', to: '/materials' }],
  },
  admin: {
    primary: [
      { label: 'Dashboard', to: '/' },
      { label: 'Admin', to: '/admin' },
      { label: 'Work Orders', to: '/work-orders' },
      { label: 'NCRs', to: '/ncrs' },
    ],
    more: [],
  },
};

export function BottomTabBar() {
  const roles = useAuthStore((s) => s.roles);
  const [moreOpen, setMoreOpen] = useState(false);

  /* Use first role for tab layout; fall back to operator */
  const primaryRole = roles[0] ?? 'operator';
  const config = TABS_BY_ROLE[primaryRole] ?? TABS_BY_ROLE.operator;

  const tabStyle: React.CSSProperties = {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 'var(--space-sm) 0',
    fontSize: 'var(--font-size-caption)',
    fontWeight: 500,
    color: 'var(--text-secondary)',
    textDecoration: 'none',
    minHeight: 56,
  };

  const activeTabStyle: React.CSSProperties = {
    ...tabStyle,
    color: 'var(--interactive)',
    borderTop: '2px solid var(--interactive)',
  };

  return (
    <>
      <nav
        className="bottom-tab-bar"
        style={{
          position: 'fixed',
          bottom: 0,
          left: 0,
          right: 0,
          background: 'var(--bg-surface)',
          borderTop: '1px solid var(--border)',
          display: 'none', /* shown via media query */
          zIndex: 200,
        }}
      >
        {config.primary.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.to === '/'}
            style={({ isActive }) => (isActive ? activeTabStyle : tabStyle)}
          >
            {tab.label}
          </NavLink>
        ))}
        {config.more.length > 0 && (
          <button
            onClick={() => setMoreOpen(!moreOpen)}
            style={{
              ...tabStyle,
              background: 'none',
              border: 'none',
              borderRadius: 0,
              minWidth: 'auto',
            }}
          >
            More
          </button>
        )}

        {moreOpen && config.more.length > 0 && (
          <div
            style={{
              position: 'absolute',
              bottom: '100%',
              right: 0,
              background: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              boxShadow: '0 -4px 12px rgba(0,0,0,0.1)',
              minWidth: 160,
            }}
          >
            {config.more.map((tab) => (
              <NavLink
                key={tab.to}
                to={tab.to}
                onClick={() => setMoreOpen(false)}
                style={{
                  display: 'block',
                  padding: 'var(--space-sm) var(--space-md)',
                  color: 'var(--text-primary)',
                  textDecoration: 'none',
                  fontSize: 'var(--font-size-label)',
                }}
              >
                {tab.label}
              </NavLink>
            ))}
          </div>
        )}
      </nav>

      <style>{`
        @media (max-width: 768px) {
          .bottom-tab-bar { display: flex !important; }
          main { padding-bottom: 64px; }
        }
      `}</style>
    </>
  );
}
