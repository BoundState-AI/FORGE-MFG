/**
 * Module:   Conflict Modal Tests
 * Purpose:  Verify 409 version conflict modal renders correctly.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConflictModal } from '@/components/ConflictModal';

describe('ConflictModal', () => {
  const baseError = {
    entity_type: 'work_order',
    entity_id: 'wo-123',
    your_version: 7,
    current_version: 9,
    message: 'This record has been modified by another user.',
  };

  it('renders nothing when error is null', () => {
    const { container } = render(
      <ConflictModal error={null} onDiscard={vi.fn()} onReload={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('shows version information when error is present', () => {
    render(
      <ConflictModal
        error={baseError}
        onDiscard={vi.fn()}
        onReload={vi.fn()}
      />,
    );
    /* Version numbers render in separate spans from labels */
    expect(screen.getByText('7')).toBeInTheDocument();
    expect(screen.getByText('9')).toBeInTheDocument();
    expect(screen.getByText(/your version/i)).toBeInTheDocument();
    expect(screen.getByText(/current version/i)).toBeInTheDocument();
  });

  it('shows entity type and id', () => {
    render(
      <ConflictModal
        error={baseError}
        onDiscard={vi.fn()}
        onReload={vi.fn()}
      />,
    );
    /* Entity info renders as "entity_type / entity_id" in one span */
    expect(screen.getByText(/work_order.*wo-123/)).toBeInTheDocument();
  });

  it('calls onDiscard when Discard button is clicked', () => {
    const onDiscard = vi.fn();
    render(
      <ConflictModal
        error={baseError}
        onDiscard={onDiscard}
        onReload={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText(/discard/i));
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it('calls onReload when Reload button is clicked', () => {
    const onReload = vi.fn();
    render(
      <ConflictModal
        error={baseError}
        onDiscard={vi.fn()}
        onReload={onReload}
      />,
    );
    fireEvent.click(screen.getByText(/reload/i));
    expect(onReload).toHaveBeenCalledTimes(1);
  });
});
