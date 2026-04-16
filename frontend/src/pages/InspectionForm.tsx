/**
 * Module:   InspectionForm
 * Purpose:  Record an inspection result against an operation hold point.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Spec SS5.5: Inspectors record accept/reject/conditional results with
 * optional measurements and evidence uploads.  Reject auto-creates an NCR.
 * The `resolves` query param indicates a conditional-resolution variant
 * where this inspection supersedes a prior conditional result.
 *
 * Evidence upload follows spec SS6.5.1: request presigned URL, upload to S3,
 * confirm with content hash, then attach the confirmed s3_key to the
 * inspection payload.
 *
 * SOD-001: The backend enforces separation-of-duties (inspector cannot be
 * the operator who executed the operation).  A 422 with code SOD-001 is
 * rendered inline via ErrorBanner.
 */

import { useState, useCallback } from 'react';
import { useParams, useSearchParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { fetchOperationDetail, createInspection, requestUploadUrl, confirmEvidence } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { ErrorBanner } from '@/components/ErrorBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ConfirmationDialog } from '@/components/ConfirmationDialog';
import { PageHeader } from '@/components/PageHeader';
import type { InspectionResult, InspectionCreate, EvidenceRef } from '@/types/api';

/* ── Shared styles ── */

const fieldGroupStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 'var(--space-xs)',
  marginBottom: 'var(--space-md)',
};

const labelStyle: React.CSSProperties = {
  fontSize: 'var(--font-size-label)',
  fontWeight: 600,
  color: 'var(--text-primary)',
};

const inputStyle: React.CSSProperties = {
  padding: 'var(--space-sm)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)',
  fontSize: 'var(--font-size-body)',
  backgroundColor: 'var(--bg-surface)',
  color: 'var(--text-primary)',
  width: '100%',
  boxSizing: 'border-box',
};

const radioGroupStyle: React.CSSProperties = {
  display: 'flex',
  gap: 'var(--space-md)',
  alignItems: 'center',
};

const radioLabelStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 'var(--space-xs)',
  fontSize: 'var(--font-size-body)',
  color: 'var(--text-primary)',
  cursor: 'pointer',
};

/* ── Measurement key-value editor ── */

interface MeasurementEntry {
  key: string;
  value: string;
}

function MeasurementEditor({
  entries,
  onChange,
  disabled,
}: {
  entries: MeasurementEntry[];
  onChange: (entries: MeasurementEntry[]) => void;
  disabled: boolean;
}) {
  function addRow() {
    onChange([...entries, { key: '', value: '' }]);
  }

  function updateRow(index: number, field: 'key' | 'value', val: string) {
    const updated = entries.map((e, i) => (i === index ? { ...e, [field]: val } : e));
    onChange(updated);
  }

  function removeRow(index: number) {
    onChange(entries.filter((_, i) => i !== index));
  }

  return (
    <div style={fieldGroupStyle}>
      <label style={labelStyle}>Measurements</label>
      {entries.map((entry, i) => (
        <div key={i} style={{ display: 'flex', gap: 'var(--space-xs)', alignItems: 'center' }}>
          <input
            type="text"
            placeholder="Key"
            value={entry.key}
            onChange={(e) => updateRow(i, 'key', e.target.value)}
            disabled={disabled}
            style={{ ...inputStyle, flex: 1 }}
          />
          <input
            type="text"
            placeholder="Value"
            value={entry.value}
            onChange={(e) => updateRow(i, 'value', e.target.value)}
            disabled={disabled}
            style={{ ...inputStyle, flex: 1 }}
          />
          <button
            type="button"
            onClick={() => removeRow(i)}
            disabled={disabled}
            style={{
              padding: 'var(--space-xs) var(--space-sm)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--bg-surface)',
              color: 'var(--text-secondary)',
              cursor: disabled ? 'not-allowed' : 'pointer',
              fontSize: 'var(--font-size-body)',
            }}
          >
            Remove
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={addRow}
        disabled={disabled}
        style={{
          alignSelf: 'flex-start',
          padding: 'var(--space-xs) var(--space-sm)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-md)',
          backgroundColor: 'var(--bg-surface)',
          color: 'var(--text-primary)',
          cursor: disabled ? 'not-allowed' : 'pointer',
          fontSize: 'var(--font-size-body)',
        }}
      >
        + Add Measurement
      </button>
    </div>
  );
}

/* ── Evidence Upload Section ── */

interface UploadedEvidence {
  s3_key: string;
  filename: string;
  status: 'uploading' | 'confirming' | 'done' | 'error';
  error?: string;
  ref?: EvidenceRef;
}

function EvidenceUploadSection({
  evidence,
  onAdd,
  disabled,
}: {
  evidence: UploadedEvidence[];
  onAdd: (file: File) => void;
  disabled: boolean;
}) {
  return (
    <div style={fieldGroupStyle}>
      <label style={labelStyle}>Evidence Attachments</label>

      {/* Existing uploads */}
      {evidence.map((ev, i) => (
        <div
          key={i}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-sm)',
            padding: 'var(--space-xs) var(--space-sm)',
            backgroundColor: ev.status === 'error' ? 'var(--safety-critical-bg)' : 'var(--bg-surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
            fontSize: 'var(--font-size-body)',
          }}
        >
          <span style={{ flex: 1, color: 'var(--text-primary)' }}>{ev.filename}</span>
          {ev.status === 'uploading' && <span style={{ color: 'var(--text-secondary)' }}>Uploading...</span>}
          {ev.status === 'confirming' && <span style={{ color: 'var(--text-secondary)' }}>Confirming...</span>}
          {ev.status === 'done' && <span style={{ color: 'var(--status-complete)' }}>Attached</span>}
          {ev.status === 'error' && <span style={{ color: 'var(--status-error)' }}>{ev.error ?? 'Upload failed'}</span>}
        </div>
      ))}

      {/* File picker */}
      <input
        type="file"
        disabled={disabled}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onAdd(file);
          e.target.value = '';
        }}
        style={{ fontSize: 'var(--font-size-body)' }}
      />
    </div>
  );
}

/* ── Main Component ── */

export default function InspectionForm() {
  const { woId, opId } = useParams<{ woId: string; opId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { hasRole } = useAuthStore();

  const isReadOnly = hasRole('qa_manager') && !hasRole('inspector');
  const resolvesId = searchParams.get('resolves') ?? undefined;

  /* Fetch operation detail for context */
  const {
    data: operation,
    isLoading: opLoading,
    isError: opError,
  } = useQuery({
    queryKey: ['operation', opId],
    queryFn: () => fetchOperationDetail(opId!),
    enabled: !!opId,
  });

  /* Form state */
  const [criteria, setCriteria] = useState('');
  const [result, setResult] = useState<InspectionResult>('accept');
  const [measurements, setMeasurements] = useState<MeasurementEntry[]>([]);
  const [notes, setNotes] = useState('');
  const [evidence, setEvidence] = useState<UploadedEvidence[]>([]);
  const [showConfirm, setShowConfirm] = useState(false);
  const [submitError, setSubmitError] = useState<{ code?: string; message: string } | null>(null);

  /* Evidence upload handler following spec SS6.5.1 */
  const handleEvidenceAdd = useCallback(async (file: File) => {
    const idx = evidence.length;
    const newEv: UploadedEvidence = {
      s3_key: '',
      filename: file.name,
      status: 'uploading',
    };
    setEvidence((prev) => [...prev, newEv]);

    try {
      /* Step 1: Request presigned URL */
      const urlResp = await requestUploadUrl({
        scope: 'inspection_evidence',
        filename: file.name,
        content_type: file.type || 'application/octet-stream',
        size_bytes: file.size,
      });

      /* Step 2: Upload to S3 */
      const uploadResp = await fetch(urlResp.upload_url, {
        method: 'PUT',
        headers: urlResp.upload_headers,
        body: file,
      });
      if (!uploadResp.ok) throw new Error('S3 upload failed');

      setEvidence((prev) =>
        prev.map((e, i) => (i === idx ? { ...e, s3_key: urlResp.s3_key, status: 'confirming' } : e)),
      );

      /* Step 3: Compute content hash and confirm */
      const arrayBuffer = await file.arrayBuffer();
      const hashBuffer = await crypto.subtle.digest('SHA-256', arrayBuffer);
      const hashHex = Array.from(new Uint8Array(hashBuffer))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');

      const ref = await confirmEvidence({
        s3_key: urlResp.s3_key,
        content_hash: hashHex,
      });

      setEvidence((prev) =>
        prev.map((e, i) => (i === idx ? { ...e, status: 'done', ref } : e)),
      );
    } catch (err) {
      setEvidence((prev) =>
        prev.map((e, i) =>
          i === idx
            ? { ...e, status: 'error', error: err instanceof Error ? err.message : 'Upload failed' }
            : e,
        ),
      );
    }
  }, [evidence.length]);

  /* Submit mutation */
  const mutation = useMutation({
    mutationFn: (body: InspectionCreate) => createInspection(opId!, body),
    onSuccess: () => {
      navigate(`/work-orders/${woId}/ops/${opId}`);
    },
    onError: (err: unknown) => {
      /* Parse 422 SOD-001 violations */
      const axiosErr = err as { response?: { status?: number; data?: { detail?: string; code?: string } } };
      if (axiosErr.response?.status === 422) {
        setSubmitError({
          code: axiosErr.response.data?.code ?? 'VALIDATION_ERROR',
          message: axiosErr.response.data?.detail ?? 'Validation error',
        });
      } else {
        setSubmitError({
          message: err instanceof Error ? err.message : 'Failed to submit inspection',
        });
      }
    },
  });

  function handleSubmit() {
    setSubmitError(null);

    /* Build measurements object from key-value pairs */
    const measurementsObj: Record<string, unknown> = {};
    for (const entry of measurements) {
      if (entry.key.trim()) {
        measurementsObj[entry.key.trim()] = entry.value;
      }
    }

    /* Collect confirmed evidence s3_keys */
    const evidenceRefs = evidence
      .filter((e) => e.status === 'done' && e.s3_key)
      .map((e) => ({ s3_key: e.s3_key }));

    const body: InspectionCreate = {
      criteria,
      result,
      measurements: Object.keys(measurementsObj).length > 0 ? measurementsObj : undefined,
      evidence_refs: evidenceRefs.length > 0 ? evidenceRefs : undefined,
      notes: notes.trim() || undefined,
      supersedes_inspection_id: resolvesId ?? undefined,
    };

    mutation.mutate(body);
    setShowConfirm(false);
  }

  /* Validation: notes required when result is conditional */
  const notesRequired = result === 'conditional';
  const canSubmit = criteria.trim().length > 0 && (!notesRequired || notes.trim().length > 0);

  if (opLoading) return <div style={{ padding: 'var(--space-lg)' }}><LoadingSkeleton rows={6} /></div>;
  if (opError) return <div style={{ padding: 'var(--space-lg)' }}><ErrorBanner message="Failed to load operation details" /></div>;

  return (
    <div style={{ padding: 'var(--space-lg)', maxWidth: '800px' }}>
      <PageHeader
        title={resolvesId ? 'Resolve Conditional Inspection' : 'Record Inspection'}
        subtitle={operation ? `${operation.description} (Op ${operation.sequence_number})` : undefined}
        banner={
          isReadOnly ? (
            <div
              style={{
                padding: 'var(--space-sm) var(--space-md)',
                backgroundColor: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                fontSize: 'var(--font-size-body)',
                color: 'var(--text-secondary)',
              }}
            >
              Read-only view -- QA Manager role cannot record inspections
            </div>
          ) : undefined
        }
      />

      {/* Inline error from submission */}
      {submitError && (
        <div style={{ marginBottom: 'var(--space-md)' }}>
          <ErrorBanner
            code={submitError.code}
            message={submitError.message}
            onDismiss={() => setSubmitError(null)}
          />
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit && !isReadOnly) setShowConfirm(true);
        }}
      >
        {/* Criteria */}
        <div style={fieldGroupStyle}>
          <label style={labelStyle} htmlFor="criteria">Criteria</label>
          <input
            id="criteria"
            type="text"
            value={criteria}
            onChange={(e) => setCriteria(e.target.value)}
            disabled={isReadOnly}
            placeholder="Inspection criteria..."
            style={inputStyle}
            required
          />
        </div>

        {/* Result */}
        <div style={fieldGroupStyle}>
          <label style={labelStyle}>Result</label>
          <div style={radioGroupStyle}>
            {(['accept', 'reject', 'conditional'] as InspectionResult[]).map((r) => (
              <label key={r} style={radioLabelStyle}>
                <input
                  type="radio"
                  name="result"
                  value={r}
                  checked={result === r}
                  onChange={() => setResult(r)}
                  disabled={isReadOnly}
                />
                {r.charAt(0).toUpperCase() + r.slice(1)}
              </label>
            ))}
          </div>
          {result === 'reject' && (
            <p style={{ margin: 0, fontSize: 'var(--font-size-body)', color: 'var(--status-error)', marginTop: 'var(--space-xs)' }}>
              A reject result will automatically create an NCR for this operation.
            </p>
          )}
        </div>

        {/* Measurements */}
        <MeasurementEditor entries={measurements} onChange={setMeasurements} disabled={isReadOnly} />

        {/* Notes */}
        <div style={fieldGroupStyle}>
          <label style={labelStyle} htmlFor="notes">
            Notes {notesRequired && <span style={{ color: 'var(--status-error)' }}>*</span>}
          </label>
          <textarea
            id="notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={isReadOnly}
            placeholder={notesRequired ? 'Notes required for conditional results...' : 'Optional notes...'}
            rows={4}
            required={notesRequired}
            style={{ ...inputStyle, resize: 'vertical' }}
          />
        </div>

        {/* Evidence uploads */}
        <EvidenceUploadSection
          evidence={evidence}
          onAdd={handleEvidenceAdd}
          disabled={isReadOnly}
        />

        {/* Submit */}
        {!isReadOnly && (
          <div style={{ display: 'flex', gap: 'var(--space-sm)', marginTop: 'var(--space-md)' }}>
            <button
              type="button"
              onClick={() => navigate(-1)}
              style={{
                padding: 'var(--space-sm) var(--space-md)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                backgroundColor: 'var(--bg-surface)',
                color: 'var(--text-primary)',
                cursor: 'pointer',
                fontSize: 'var(--font-size-body)',
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit || mutation.isPending}
              style={{
                padding: 'var(--space-sm) var(--space-md)',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                backgroundColor: 'var(--interactive)',
                color: '#ffffff',
                fontWeight: 600,
                cursor: !canSubmit || mutation.isPending ? 'not-allowed' : 'pointer',
                opacity: !canSubmit || mutation.isPending ? 0.6 : 1,
                fontSize: 'var(--font-size-body)',
              }}
            >
              {mutation.isPending ? 'Submitting...' : 'Submit Inspection'}
            </button>
          </div>
        )}
      </form>

      {/* Confirmation dialog */}
      <ConfirmationDialog
        open={showConfirm}
        title="Submit Inspection"
        message={
          result === 'reject'
            ? 'This will record a reject result and automatically create an NCR. Continue?'
            : result === 'conditional'
              ? 'This will record a conditional result. A follow-up inspection will be required. Continue?'
              : 'Submit this inspection result?'
        }
        confirmLabel="Submit"
        onConfirm={handleSubmit}
        onCancel={() => setShowConfirm(false)}
        loading={mutation.isPending}
      />
    </div>
  );
}
