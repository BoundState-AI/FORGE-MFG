/**
 * Module:   LoadingSkeleton
 * Purpose:  Animated skeleton screen placeholder for page/list loading states.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Renders a configurable number of gray pulse rectangles that mimic
 * the shape of table rows or card items.  The CSS pulse animation
 * is injected once via a <style> element to avoid external CSS files.
 */

import { useEffect, useRef } from 'react';

interface LoadingSkeletonProps {
  /** Number of skeleton rows to render. Defaults to 5. */
  rows?: number;
}

const ANIMATION_NAME = 'forge-skeleton-pulse';

/**
 * Injects the keyframe animation into the document head exactly once.
 * Multiple LoadingSkeleton instances share the same animation rule.
 */
function useSkeletonKeyframes() {
  const injected = useRef(false);

  useEffect(() => {
    if (injected.current) return;
    const id = `${ANIMATION_NAME}-style`;
    if (document.getElementById(id)) {
      injected.current = true;
      return;
    }

    const style = document.createElement('style');
    style.id = id;
    style.textContent = `
      @keyframes ${ANIMATION_NAME} {
        0%   { opacity: 0.15; }
        50%  { opacity: 0.30; }
        100% { opacity: 0.15; }
      }
    `;
    document.head.appendChild(style);
    injected.current = true;
  }, []);
}

export function LoadingSkeleton({ rows = 5 }: LoadingSkeletonProps) {
  useSkeletonKeyframes();

  return (
    <div
      role="status"
      aria-label="Loading"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-sm)',
        padding: 'var(--space-md)',
      }}
    >
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          style={{
            height: '20px',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: 'var(--border)',
            animation: `${ANIMATION_NAME} 1.5s ease-in-out infinite`,
            animationDelay: `${i * 0.1}s`,
            /* Vary widths so the skeleton looks organic */
            width: i === 0 ? '60%' : i % 3 === 0 ? '80%' : '100%',
          }}
        />
      ))}
      {/* Screen-reader-only loading text */}
      <span
        style={{
          position: 'absolute',
          width: '1px',
          height: '1px',
          padding: 0,
          margin: '-1px',
          overflow: 'hidden',
          clip: 'rect(0, 0, 0, 0)',
          whiteSpace: 'nowrap',
          border: 0,
        }}
      >
        Loading...
      </span>
    </div>
  );
}
