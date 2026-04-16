/**
 * Module:   NotFound
 * Purpose:  404 page displayed when no route matches the current URL.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 */

import { Link } from 'react-router-dom';

export default function NotFound() {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '60vh',
        padding: 'var(--space-2xl)',
        textAlign: 'center',
      }}
    >
      <h1
        style={{
          margin: 0,
          fontSize: '72px',
          fontWeight: 700,
          color: 'var(--text-secondary)',
          lineHeight: 1,
        }}
      >
        404
      </h1>
      <h2
        style={{
          margin: 0,
          marginTop: 'var(--space-sm)',
          fontSize: 'var(--font-size-page)',
          color: 'var(--text-primary)',
        }}
      >
        Page not found
      </h2>
      <p
        style={{
          margin: 0,
          marginTop: 'var(--space-sm)',
          fontSize: 'var(--font-size-body)',
          color: 'var(--text-secondary)',
          maxWidth: '400px',
          lineHeight: 1.5,
        }}
      >
        The page you are looking for does not exist or has been moved.
      </p>
      <Link
        to="/"
        style={{
          marginTop: 'var(--space-lg)',
          padding: 'var(--space-sm) var(--space-md)',
          backgroundColor: 'var(--interactive)',
          color: '#ffffff',
          borderRadius: 'var(--radius-md)',
          textDecoration: 'none',
          fontWeight: 600,
          fontSize: 'var(--font-size-body)',
        }}
      >
        Back to Dashboard
      </Link>
    </div>
  );
}
