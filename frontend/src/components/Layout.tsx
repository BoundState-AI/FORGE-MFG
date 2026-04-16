/**
 * Module:   Layout
 * Purpose:  App shell with sidebar (desktop) and bottom tab bar (floor mode).
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this structure: Spec §3.1 requires a persistent left sidebar on
 * desktop and a bottom tab bar on tablet/mobile (≤768px).  Navigation
 * items are role-filtered so operators never see admin.
 */

import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { BottomTabBar } from './BottomTabBar';
import { useAuthStore } from '@/auth/store';
import { ProgramSelector } from './ProgramSelector';

export function Layout() {
  const user = useAuthStore((s) => s.user);

  return (
    <div style={{ display: 'flex', minHeight: '100vh', width: '100%' }}>
      {/* Desktop sidebar — hidden at ≤768px */}
      <div
        style={{
          width: 'var(--sidebar-width)',
          flexShrink: 0,
          borderRight: '1px solid var(--border)',
          background: 'var(--bg-surface)',
          display: 'flex',
          flexDirection: 'column',
        }}
        className="sidebar-container"
      >
        <div
          style={{
            padding: 'var(--space-md)',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div
            style={{
              fontWeight: 700,
              fontSize: 'var(--font-size-section)',
              color: 'var(--text-primary)',
              marginBottom: 'var(--space-sm)',
            }}
          >
            FORGE-MFG
          </div>
          {user && (
            <div
              style={{
                fontSize: 'var(--font-size-caption)',
                color: 'var(--text-secondary)',
                marginBottom: 'var(--space-sm)',
              }}
            >
              {user.display_name}
            </div>
          )}
          <ProgramSelector />
        </div>
        <Sidebar />
      </div>

      {/* Main content area */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <main style={{ flex: 1 }}>
          <Outlet />
        </main>
      </div>

      {/* Floor mode bottom tab bar — visible only at ≤768px */}
      <BottomTabBar />

      <style>{`
        @media (max-width: 768px) {
          .sidebar-container { display: none !important; }
        }
      `}</style>
    </div>
  );
}
