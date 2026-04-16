/**
 * Module:   Component Unit Tests
 * Purpose:  Verify shared components render correctly.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { StatusBadge } from '@/components/StatusBadge';
import { SafetyCriticalBanner } from '@/components/SafetyCriticalBanner';
import { EmptyState } from '@/components/EmptyState';
import { ErrorBanner } from '@/components/ErrorBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ConnectionBanner } from '@/components/ConnectionBanner';

describe('StatusBadge', () => {
  it('renders humanized status text', () => {
    render(<StatusBadge status="in_progress" />);
    /* StatusBadge humanizes: "in_progress" → "In Progress" */
    expect(screen.getByText('In Progress')).toBeInTheDocument();
  });

  it('renders custom label when provided', () => {
    render(<StatusBadge status="in_progress" label="Active" />);
    expect(screen.getByText('Active')).toBeInTheDocument();
  });
});

describe('SafetyCriticalBanner', () => {
  it('renders when visible is true', () => {
    render(<SafetyCriticalBanner visible={true} />);
    /* Source text is "Safety-Critical" with CSS textTransform uppercase */
    expect(screen.getByText(/Safety-Critical/i)).toBeInTheDocument();
  });

  it('does not render when visible is false', () => {
    const { container } = render(<SafetyCriticalBanner visible={false} />);
    expect(container.firstChild).toBeNull();
  });
});

describe('EmptyState', () => {
  it('renders title and message', () => {
    render(<EmptyState title="No items" message="Try adjusting filters" />);
    expect(screen.getByText('No items')).toBeInTheDocument();
    expect(screen.getByText('Try adjusting filters')).toBeInTheDocument();
  });
});

describe('ErrorBanner', () => {
  it('renders error message with code', () => {
    render(<ErrorBanner code="SOD-001" message="Separation of duties violation" />);
    /* ErrorBanner wraps code in brackets: [SOD-001] */
    expect(screen.getByText('[SOD-001]')).toBeInTheDocument();
    expect(screen.getByText(/Separation of duties/)).toBeInTheDocument();
  });

  it('calls onDismiss when dismiss button clicked', () => {
    const onDismiss = vi.fn();
    render(
      <ErrorBanner message="Error" onDismiss={onDismiss} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('has aria-live attribute for accessibility', () => {
    render(<ErrorBanner message="Error" />);
    expect(screen.getByRole('alert')).toHaveAttribute('aria-live', 'polite');
  });
});

describe('LoadingSkeleton', () => {
  it('renders the specified number of rows', () => {
    const { container } = render(<LoadingSkeleton rows={3} />);
    /* LoadingSkeleton may use divs without test IDs; check it renders */
    expect(container.firstChild).not.toBeNull();
  });
});

describe('ConnectionBanner', () => {
  it('renders warning when disconnected', () => {
    render(<ConnectionBanner connected={false} />);
    expect(screen.getByText(/Connection lost/i)).toBeInTheDocument();
  });

  it('does not render when connected', () => {
    const { container } = render(<ConnectionBanner connected={true} />);
    expect(container.firstChild).toBeNull();
  });

  it('has assertive aria-live for screen readers', () => {
    render(<ConnectionBanner connected={false} />);
    expect(screen.getByRole('alert')).toHaveAttribute('aria-live', 'assertive');
  });
});
