import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { extract422Message } from '@/api/error-detail';
import { createWorkOrder } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { ErrorBanner } from '@/components/ErrorBanner';
import type { BuildPhase, Priority, WorkOrderCreate } from '@/types/api';

const BUILD_PHASE_OPTIONS: Array<{ value: BuildPhase; label: string }> = [
  { value: 'fabrication', label: 'Fabrication' },
  { value: 'assembly', label: 'Assembly' },
  { value: 'test', label: 'Test' },
  { value: 'refurbishment', label: 'Refurbishment' },
];

const PRIORITY_OPTIONS: Array<{ value: Priority; label: string }> = [
  { value: 'routine', label: 'Routine' },
  { value: 'urgent', label: 'Urgent' },
  { value: 'critical', label: 'Critical' },
];

const fieldStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 'var(--space-xs)',
};

const inputStyle: React.CSSProperties = {
  padding: 'var(--space-sm)',
  borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border)',
  backgroundColor: 'var(--bg-surface)',
  color: 'var(--text-primary)',
  fontSize: 'var(--font-size-body)',
};

export default function WorkOrderNew() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user, activeProgram, hasRole } = useAuthStore();
  const programs = user?.program_memberships ?? [];

  const [programId, setProgramId] = useState(
    activeProgram?.id ?? programs[0]?.id ?? '',
  );
  const [engineSerial, setEngineSerial] = useState('');
  const [module, setModule] = useState('');
  const [buildPhase, setBuildPhase] = useState<BuildPhase>('fabrication');
  const [priority, setPriority] = useState<Priority>('routine');
  const [bomRevisionRef, setBomRevisionRef] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: async (body: WorkOrderCreate) => createWorkOrder(body),
    onSuccess: (wo) => {
      setErrorMsg(null);
      queryClient.invalidateQueries({ queryKey: ['work-orders'] });
      navigate(`/work-orders/${wo.id}`);
    },
    onError: (err: unknown) => {
      const detail = extract422Message(err);
      setErrorMsg(
        detail ??
          (err instanceof Error ? err.message : 'Failed to create work order.'),
      );
    },
  });

  if (!hasRole('engineer')) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <h1 style={{ margin: 0, marginBottom: 'var(--space-md)' }}>
          Create Work Order
        </h1>
        <ErrorBanner message="Engineer role required to create work orders." />
      </div>
    );
  }

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!programId) {
      setErrorMsg('Select a program before creating the work order.');
      return;
    }

    createMutation.mutate({
      program_id: programId,
      engine_serial: engineSerial.trim(),
      module: module.trim(),
      build_phase: buildPhase,
      priority,
      bom_revision_ref: bomRevisionRef.trim() || null,
    });
  }

  return (
    <div style={{ padding: 'var(--space-lg)', maxWidth: 760 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 'var(--space-sm)',
          marginBottom: 'var(--space-lg)',
          flexWrap: 'wrap',
        }}
      >
        <div>
          <h1 style={{ margin: 0, marginBottom: 'var(--space-xs)' }}>
            Create Work Order
          </h1>
          <p style={{ margin: 0, color: 'var(--text-secondary)' }}>
            Create a draft work order in the selected program.
          </p>
        </div>
        <button
          type="button"
          onClick={() => navigate('/work-orders')}
          style={{
            padding: 'var(--space-sm) var(--space-md)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border)',
            backgroundColor: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            cursor: 'pointer',
          }}
        >
          Cancel
        </button>
      </div>

      {errorMsg && (
        <div style={{ marginBottom: 'var(--space-md)' }}>
          <ErrorBanner message={errorMsg} />
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        style={{
          display: 'grid',
          gap: 'var(--space-md)',
          gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
        }}
      >
        <label style={fieldStyle}>
          <span>Program</span>
          <select
            value={programId}
            onChange={(e) => setProgramId(e.target.value)}
            style={inputStyle}
            required
          >
            {programs.map((program) => (
              <option key={program.id} value={program.id}>
                {program.code} - {program.name}
              </option>
            ))}
          </select>
        </label>

        <label style={fieldStyle}>
          <span>Engine Serial</span>
          <input
            value={engineSerial}
            onChange={(e) => setEngineSerial(e.target.value)}
            style={inputStyle}
            maxLength={100}
            required
          />
        </label>

        <label style={fieldStyle}>
          <span>Module</span>
          <input
            value={module}
            onChange={(e) => setModule(e.target.value)}
            style={inputStyle}
            maxLength={100}
            required
          />
        </label>

        <label style={fieldStyle}>
          <span>Build Phase</span>
          <select
            value={buildPhase}
            onChange={(e) => setBuildPhase(e.target.value as BuildPhase)}
            style={inputStyle}
            required
          >
            {BUILD_PHASE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label style={fieldStyle}>
          <span>Priority</span>
          <select
            value={priority}
            onChange={(e) => setPriority(e.target.value as Priority)}
            style={inputStyle}
            required
          >
            {PRIORITY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label style={fieldStyle}>
          <span>BOM Revision Ref</span>
          <input
            value={bomRevisionRef}
            onChange={(e) => setBomRevisionRef(e.target.value)}
            style={inputStyle}
            maxLength={255}
          />
        </label>

        <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 'var(--space-sm)' }}>
          <button
            type="submit"
            disabled={createMutation.isPending}
            style={{
              padding: 'var(--space-sm) var(--space-md)',
              border: 'none',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--interactive)',
              color: '#ffffff',
              fontWeight: 600,
              cursor: createMutation.isPending ? 'wait' : 'pointer',
              opacity: createMutation.isPending ? 0.8 : 1,
            }}
          >
            {createMutation.isPending ? 'Creating...' : 'Create Work Order'}
          </button>
        </div>
      </form>
    </div>
  );
}
