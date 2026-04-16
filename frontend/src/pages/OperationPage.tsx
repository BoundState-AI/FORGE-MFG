/**
 * Module:   OperationPage
 * Purpose:  Route resolver — renders OperationExecution or OperationDetail.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this resolver: Spec §5.4.11 — the shared route
 * /work-orders/:id/ops/:opId renders OperationExecution for operators
 * and OperationDetail for engineers.  Multi-role users see a toggle.
 */

import { useState } from 'react';
import { useAuthStore } from '@/auth/store';
import OperationExecution from './OperationExecution';
import OperationDetail from './OperationDetail';

export function OperationPage() {
  const hasRole = useAuthStore((s) => s.hasRole);
  const isOperator = hasRole('operator');
  const isEngineer = hasRole('engineer');
  const isBothRoles = isOperator && isEngineer;

  /* Default: operator if both roles, unless no active labor entry */
  const [viewAs, setViewAs] = useState<'operator' | 'engineer'>(
    isOperator ? 'operator' : 'engineer',
  );

  if (isBothRoles) {
    return (
      <div>
        <div
          style={{
            display: 'flex',
            gap: 'var(--space-sm)',
            padding: 'var(--space-sm) var(--space-xl)',
            background: 'var(--bg-elevated)',
            borderBottom: '1px solid var(--border)',
            fontSize: 'var(--font-size-label)',
            alignItems: 'center',
          }}
        >
          <span style={{ color: 'var(--text-secondary)' }}>Viewing as:</span>
          <button
            className={viewAs === 'operator' ? 'btn-primary' : 'btn-secondary'}
            style={{ minHeight: 36, padding: '4px 12px' }}
            onClick={() => setViewAs('operator')}
          >
            Operator
          </button>
          <button
            className={viewAs === 'engineer' ? 'btn-primary' : 'btn-secondary'}
            style={{ minHeight: 36, padding: '4px 12px' }}
            onClick={() => setViewAs('engineer')}
          >
            Engineer
          </button>
        </div>
        {viewAs === 'operator' ? <OperationExecution /> : <OperationDetail />}
      </div>
    );
  }

  if (isOperator) return <OperationExecution />;
  return <OperationDetail />;
}
