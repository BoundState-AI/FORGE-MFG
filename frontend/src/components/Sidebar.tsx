/**
 * Module:   Sidebar
 * Purpose:  Desktop navigation sidebar with role-filtered nav items.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why role-filtering: Spec §3.1 — navigation filtering is a UX
 * convenience, not a security control.  All routes enforce authorization
 * independently.  Operators don't see admin; program managers don't see
 * operation execution.
 */

import { NavLink } from 'react-router-dom';
import { useAuthStore } from '@/auth/store';
import type { Role } from '@/types/api';

interface NavItem {
  label: string;
  to: string;
  roles: Role[] | 'all';
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Dashboard', to: '/', roles: 'all' },
  { label: 'Work Orders', to: '/work-orders', roles: 'all' },
  {
    label: 'Inspections',
    to: '/inspections',
    roles: ['inspector', 'qa_manager'],
  },
  { label: 'NCRs', to: '/ncrs', roles: 'all' },
  {
    label: 'Materials',
    to: '/materials',
    roles: ['operator', 'inspector', 'engineer', 'qa_manager', 'program_manager'],
  },
  {
    label: 'Parts',
    to: '/parts',
    roles: ['operator', 'inspector', 'engineer', 'qa_manager', 'program_manager'],
  },
  { label: 'Admin', to: '/admin', roles: ['admin'] },
];

const linkStyle: React.CSSProperties = {
  display: 'block',
  padding: 'var(--space-sm) var(--space-md)',
  borderRadius: 'var(--radius-md)',
  color: 'var(--text-primary)',
  textDecoration: 'none',
  fontSize: 'var(--font-size-label)',
  fontWeight: 500,
  marginBottom: '2px',
};

const activeLinkStyle: React.CSSProperties = {
  ...linkStyle,
  background: 'rgba(21, 101, 192, 0.08)',
  color: 'var(--interactive)',
};

export function Sidebar() {
  const roles = useAuthStore((s) => s.roles);

  const visibleItems = NAV_ITEMS.filter((item) => {
    if (item.roles === 'all') return true;
    return item.roles.some((r) => roles.includes(r));
  });

  return (
    <nav style={{ flex: 1, padding: 'var(--space-sm)', overflowY: 'auto' }}>
      {visibleItems.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === '/'}
          style={({ isActive }) => (isActive ? activeLinkStyle : linkStyle)}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
