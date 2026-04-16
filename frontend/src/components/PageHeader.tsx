/**
 * Module:   PageHeader
 * Purpose:  Reusable page header with title, breadcrumbs, and actions slot.
 * Rev:      0.2.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Renders a consistent page title bar across all views.  Breadcrumbs
 * use plain <a> tags — the consuming app wraps them with its router's
 * Link component if needed (keeps this file router-agnostic).
 *
 * Backward-compatible: subtitle, banner props still work alongside
 * the new breadcrumbs prop.
 */

import type { ReactNode } from 'react';

interface Breadcrumb {
  label: string;
  /** When omitted the breadcrumb renders as plain text (current page). */
  to?: string;
}

interface PageHeaderProps {
  title: string;
  breadcrumbs?: Breadcrumb[];
  subtitle?: string;
  /** Right-aligned action slot for buttons, filters, etc. */
  actions?: ReactNode;
  /** Optional banner rendered below the header (e.g. read-only notice). */
  banner?: ReactNode;
}

export function PageHeader({
  title,
  breadcrumbs,
  subtitle,
  actions,
  banner,
}: PageHeaderProps) {
  return (
    <header
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-xs)',
        marginBottom: 'var(--space-lg)',
      }}
    >
      {/* Breadcrumb trail */}
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav aria-label="Breadcrumb">
          <ol
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-xs)',
              listStyle: 'none',
              margin: 0,
              padding: 0,
              fontSize: 'var(--font-size-caption)',
              color: 'var(--text-muted)',
            }}
          >
            {breadcrumbs.map((crumb, idx) => {
              const isLast = idx === breadcrumbs.length - 1;
              return (
                <li
                  key={crumb.label}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 'var(--space-xs)',
                  }}
                >
                  {idx > 0 && (
                    <span aria-hidden="true" style={{ color: 'var(--text-muted)' }}>
                      /
                    </span>
                  )}
                  {crumb.to && !isLast ? (
                    <a
                      href={crumb.to}
                      style={{
                        color: 'var(--interactive)',
                        textDecoration: 'none',
                      }}
                    >
                      {crumb.label}
                    </a>
                  ) : (
                    <span
                      aria-current={isLast ? 'page' : undefined}
                      style={{ color: 'var(--text-secondary)' }}
                    >
                      {crumb.label}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        </nav>
      )}

      {/* Title row with actions */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 'var(--space-md)',
          flexWrap: 'wrap',
        }}
      >
        <div>
          <h1
            style={{
              margin: 0,
              fontSize: 'var(--font-size-title)',
              color: 'var(--text-primary)',
              fontWeight: 700,
            }}
          >
            {title}
          </h1>
          {subtitle && (
            <p
              style={{
                margin: 0,
                marginTop: 'var(--space-xs)',
                fontSize: 'var(--font-size-body)',
                color: 'var(--text-secondary)',
                lineHeight: 1.5,
              }}
            >
              {subtitle}
            </p>
          )}
        </div>
        {actions && (
          <div
            style={{
              display: 'flex',
              gap: 'var(--space-sm)',
              alignItems: 'center',
            }}
          >
            {actions}
          </div>
        )}
      </div>

      {/* Optional banner below the header */}
      {banner && <div style={{ marginTop: 'var(--space-sm)' }}>{banner}</div>}
    </header>
  );
}
