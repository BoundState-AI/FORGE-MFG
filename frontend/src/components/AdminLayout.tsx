/**
 * Module:   AdminLayout
 * Purpose:  Distinct layout for admin pages with dark header (spec §5.10).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why distinct header: Spec §5.10 requires admin be "separated visually
 * from manufacturing UI with distinct header color (--text-primary
 * background, white text) to make it unmistakably different."
 */

import { Outlet, NavLink } from 'react-router-dom';

const navLinkStyle: React.CSSProperties = {
  color: 'rgba(255,255,255,0.7)',
  textDecoration: 'none',
  padding: 'var(--space-sm) var(--space-md)',
  fontSize: 'var(--font-size-label)',
  fontWeight: 500,
  borderRadius: 'var(--radius-md)',
};

const activeNavLink: React.CSSProperties = {
  ...navLinkStyle,
  color: '#fff',
  background: 'rgba(255,255,255,0.12)',
};

export function AdminLayout() {
  return (
    <div>
      <header
        style={{
          background: 'var(--admin-header-bg)',
          color: 'var(--admin-header-text)',
          padding: 'var(--space-sm) var(--space-xl)',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-lg)',
        }}
      >
        <span style={{ fontWeight: 700, fontSize: 'var(--font-size-section)' }}>
          Admin
        </span>
        <nav style={{ display: 'flex', gap: 'var(--space-xs)' }}>
          <NavLink
            to="/admin"
            end
            style={({ isActive }) => (isActive ? activeNavLink : navLinkStyle)}
          >
            Health
          </NavLink>
          <NavLink
            to="/admin/users"
            style={({ isActive }) => (isActive ? activeNavLink : navLinkStyle)}
          >
            Users
          </NavLink>
          <NavLink
            to="/admin/programs"
            style={({ isActive }) => (isActive ? activeNavLink : navLinkStyle)}
          >
            Programs
          </NavLink>
        </nav>
      </header>
      <Outlet />
    </div>
  );
}
