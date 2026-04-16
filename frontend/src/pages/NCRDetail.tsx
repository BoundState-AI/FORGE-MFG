/**
 * Module:   NCRDetail
 * Purpose:  NCR detail view with disposition form, evidence management, and state actions.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Spec SS5.6: Displays full NCR detail including evidence references.
 * The disposition form is available when status === 'open' to engineers
 * who did not initiate the NCR (SOD-002 enforcement).  Rework/repair
 * dispositions that target a closed linked WO require a
 * remediation_work_order_id.
 *
 * State actions per spec SS5.6.6:
 *   open        -> [Submit Disposition] (engineer, not initiator)
 *   dispositioned -> [Close NCR] placeholder (deferred to GC-4)
 *   closed      -> read-only
 */

import { useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchNCR, dispositionNCR, patchNCR, requestUploadUrl, confirmEvidence } from '@/api/endpoints';
import { useAuthStore } from '@/auth/store';
import { StatusBadge } from '@/components/StatusBadge';
import { ErrorBanner } from '@/components/ErrorBanner';
import { LoadingSkeleton } from '@/components/LoadingSkeleton';
import { ConfirmationDialog } from '@/components/ConfirmationDialog';
import { PageHeader } from '@/components/PageHeader';
import type { Disposition, NCRDisposition, NCRPatch, EvidenceRef } from '@/types/api';

/* ── Styles ── */

const sectionStyle: React.CSSProperties = {
  marginBottom: 'var(--space-lg)',
  padding: 'var(--space-md)',
  backgroundColor: 'var(--bg-surface)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)',
};

const fieldRowStyle: React.CSSProperties = {
  display: 'flex',
  gap: 'var(--space-md)',
  marginBottom: 'var(--space-sm)',
  flexWrap: 'wrap',
};

const fieldLabelStyle: React.CSSProperties = {
  fontSize: 'var(--font-size-label)',
  fontWeight: 600,
  color: 'var(--text-secondary)',
  marginBottom: '2px',
};

const fieldValueStyle: React.CSSProperties = {
  fontSize: 'var(--font-size-body)',
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

/* ── Disposition options ── */

const DISPOSITION_OPTIONS: Array<{ value: Disposition; label: string }> = [
  { value: 'use_as_is', label: 'Use As-Is' },
  { value: 'rework', label: 'Rework' },
  { value: 'repair', label: 'Repair' },
  { value: 'scrap', label: 'Scrap' },
];

/* ── Evidence Upload for NCR ── */

interface UploadState {
  filename: string;
  status: 'uploading' | 'confirming' | 'done' | 'error';
  error?: string;
  s3_key?: string;
}

/* ── Component ── */

export default function NCRDetail() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const { user, hasRole } = useAuthStore();

  /* Fetch NCR */
  const {
    data: ncr,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['ncr', id],
    queryFn: () => fetchNCR(id!),
    enabled: !!id,
    refetchInterval: 30_000,
  });

  /* Disposition form state */
  const [disposition, setDisposition] = useState<Disposition>('use_as_is');
  const [rationale, setRationale] = useState('');
  const [mrbDisposition, setMrbDisposition] = useState('');
  const [mrbAuthorityId, setMrbAuthorityId] = useState('');
  const [remediationWoId, setRemediationWoId] = useState('');
  const [showConfirm, setShowConfirm] = useState(false);
  const [formError, setFormError] = useState<{ code?: string; message: string } | null>(null);

  /* Evidence upload state */
  const [uploads, setUploads] = useState<UploadState[]>([]);

  /* Determine if current user is the initiator (SOD-002) */
  const isInitiator = ncr && user ? ncr.initiated_by_id === user.id : false;
  const canDisposition = ncr?.status === 'open' && hasRole('engineer') && !isInitiator;
  const needsRemediationWo = (disposition === 'rework' || disposition === 'repair');

  /* open → dispositioned.  Invariant: caller is engineer and NOT the
     NCR initiator (SOD-002).  Side-effect: if disposition is rework/repair
     and linked operation is complete, server atomically reopens the
     operation (complete → in_progress) and reactivates the parent WO
     if needed.  Permission: engineer (not initiator). */
  const dispositionMutation = useMutation({
    mutationFn: (body: NCRDisposition) => dispositionNCR(id!, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ncr', id] });
      queryClient.invalidateQueries({ queryKey: ['ncrs'] });
      setShowConfirm(false);
      setFormError(null);
    },
    onError: (err: unknown) => {
      setShowConfirm(false);
      const axiosErr = err as { response?: { status?: number; data?: { detail?: string; code?: string; error?: { code?: string; message?: string } } } };
      if (axiosErr.response?.status === 409) {
        setFormError({
          code: axiosErr.response.data?.error?.code ?? 'CONFLICT',
          message: axiosErr.response.data?.error?.message ?? 'Version conflict. Refresh and retry.',
        });
      } else if (axiosErr.response?.status === 422) {
        setFormError({
          code: axiosErr.response.data?.code ?? 'VALIDATION_ERROR',
          message: axiosErr.response.data?.detail ?? 'Validation error',
        });
      } else {
        setFormError({ message: err instanceof Error ? err.message : 'Disposition failed' });
      }
    },
  });

  function handleDispositionSubmit() {
    if (!ncr) return;
    const body: NCRDisposition = {
      version: ncr.version,
      disposition,
      disposition_rationale: rationale,
      mrb_disposition: ncr.mrb_required && mrbDisposition ? mrbDisposition : undefined,
      mrb_authority_id: ncr.mrb_required && mrbAuthorityId ? mrbAuthorityId : undefined,
      remediation_work_order_id: needsRemediationWo && remediationWoId ? remediationWoId : undefined,
    };
    dispositionMutation.mutate(body);
  }

  /* Evidence upload handler */
  const handleEvidenceAdd = useCallback(async (file: File) => {
    if (!ncr) return;
    const idx = uploads.length;
    setUploads((prev) => [...prev, { filename: file.name, status: 'uploading' }]);

    try {
      const urlResp = await requestUploadUrl({
        scope: 'ncr_evidence',
        filename: file.name,
        content_type: file.type || 'application/octet-stream',
        size_bytes: file.size,
      });

      const uploadResp = await fetch(urlResp.upload_url, {
        method: 'PUT',
        headers: urlResp.upload_headers,
        body: file,
      });
      if (!uploadResp.ok) throw new Error('S3 upload failed');

      setUploads((prev) =>
        prev.map((u, i) => (i === idx ? { ...u, s3_key: urlResp.s3_key, status: 'confirming' } : u)),
      );

      const arrayBuffer = await file.arrayBuffer();
      const hashBuffer = await crypto.subtle.digest('SHA-256', arrayBuffer);
      const hashHex = Array.from(new Uint8Array(hashBuffer))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');

      await confirmEvidence({ s3_key: urlResp.s3_key, content_hash: hashHex });

      setUploads((prev) =>
        prev.map((u, i) => (i === idx ? { ...u, status: 'done' } : u)),
      );

      /* Patch NCR with updated evidence_refs.
         The generated type widens evidence_refs to (string | Record<string, never>)[]
         because Pydantic exported the union as such.  The backend actually
         accepts submission-time refs of shape { s3_key: string } and hydrates
         the canonical EvidenceRef from the registry (spec §5.5.3).  We cast
         through unknown to submit the structured shape the server expects. */
      const existingKeys = ncr.evidence_refs.map((ref) =>
        typeof ref === 'string' ? ref : (ref as unknown as EvidenceRef).s3_key,
      );
      const allKeys = [...existingKeys, urlResp.s3_key].map((k) => ({ s3_key: k }));
      await patchNCR(ncr.id, {
        version: ncr.version,
        evidence_refs: allKeys as unknown as NCRPatch['evidence_refs'],
      });
      queryClient.invalidateQueries({ queryKey: ['ncr', id] });
    } catch (err) {
      setUploads((prev) =>
        prev.map((u, i) =>
          i === idx ? { ...u, status: 'error', error: err instanceof Error ? err.message : 'Failed' } : u,
        ),
      );
    }
  }, [ncr, uploads.length, id, queryClient]);

  /* Render helpers */

  function renderEvidenceRef(ref: EvidenceRef | string, index: number) {
    if (typeof ref === 'string') {
      return (
        <div key={index} style={{ fontSize: 'var(--font-size-body)', color: 'var(--text-primary)' }}>
          {ref}
        </div>
      );
    }
    return (
      <div
        key={index}
        style={{
          display: 'flex',
          gap: 'var(--space-sm)',
          padding: 'var(--space-xs) var(--space-sm)',
          backgroundColor: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
          fontSize: 'var(--font-size-body)',
          alignItems: 'center',
        }}
      >
        <span style={{ flex: 1, color: 'var(--text-primary)' }}>{ref.filename}</span>
        <span style={{ color: 'var(--text-secondary)' }}>{ref.content_type}</span>
        <span style={{ color: 'var(--text-secondary)' }}>
          {ref.size_bytes > 1024 * 1024
            ? `${(ref.size_bytes / (1024 * 1024)).toFixed(1)} MB`
            : `${(ref.size_bytes / 1024).toFixed(1)} KB`}
        </span>
      </div>
    );
  }

  if (isLoading) return <div style={{ padding: 'var(--space-lg)' }}><LoadingSkeleton rows={10} /></div>;

  if (isError) {
    return (
      <div style={{ padding: 'var(--space-lg)' }}>
        <ErrorBanner message={error instanceof Error ? error.message : 'Failed to load NCR'} />
      </div>
    );
  }

  if (!ncr) return null;

  return (
    <div style={{ padding: 'var(--space-lg)', maxWidth: '900px' }}>
      <PageHeader
        title={ncr.ncr_number}
        actions={<StatusBadge status={ncr.status} />}
      />

      {/* Detail section */}
      <div style={sectionStyle}>
        <h3 style={{ margin: 0, marginBottom: 'var(--space-sm)', color: 'var(--text-primary)' }}>Details</h3>
        <div style={fieldRowStyle}>
          <div>
            <div style={fieldLabelStyle}>Description</div>
            <div style={fieldValueStyle}>{ncr.description}</div>
          </div>
        </div>
        <div style={fieldRowStyle}>
          <div>
            <div style={fieldLabelStyle}>Initiated By</div>
            <div style={fieldValueStyle}>{ncr.initiated_by_id}</div>
          </div>
          <div>
            <div style={fieldLabelStyle}>Work Order</div>
            <div style={fieldValueStyle}>{ncr.work_order_id}</div>
          </div>
          {ncr.operation_id && (
            <div>
              <div style={fieldLabelStyle}>Operation</div>
              <div style={fieldValueStyle}>{ncr.operation_id}</div>
            </div>
          )}
        </div>
        <div style={fieldRowStyle}>
          <div>
            <div style={fieldLabelStyle}>Created</div>
            <div style={fieldValueStyle}>{new Date(ncr.created_at).toLocaleString()}</div>
          </div>
          <div>
            <div style={fieldLabelStyle}>Updated</div>
            <div style={fieldValueStyle}>{new Date(ncr.updated_at).toLocaleString()}</div>
          </div>
          <div>
            <div style={fieldLabelStyle}>MRB Required</div>
            <div style={fieldValueStyle}>{ncr.mrb_required ? 'Yes' : 'No'}</div>
          </div>
        </div>
        {ncr.disposition_rationale && (
          <div style={fieldRowStyle}>
            <div>
              <div style={fieldLabelStyle}>Disposition</div>
              <div style={fieldValueStyle}>{ncr.disposition.replace(/_/g, ' ')}</div>
            </div>
            <div>
              <div style={fieldLabelStyle}>Rationale</div>
              <div style={fieldValueStyle}>{ncr.disposition_rationale}</div>
            </div>
          </div>
        )}
        {ncr.root_cause && (
          <div style={fieldRowStyle}>
            <div>
              <div style={fieldLabelStyle}>Root Cause</div>
              <div style={fieldValueStyle}>{ncr.root_cause}</div>
            </div>
          </div>
        )}
      </div>

      {/* Evidence section */}
      <div style={sectionStyle}>
        <h3 style={{ margin: 0, marginBottom: 'var(--space-sm)', color: 'var(--text-primary)' }}>Evidence</h3>
        {ncr.evidence_refs.length === 0 && uploads.length === 0 && (
          <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-size-body)', margin: 0 }}>
            No evidence attached.
          </p>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-xs)' }}>
          {ncr.evidence_refs.map((ref, i) =>
            /* Cast is safe: the server hydrates each ref to full EvidenceRef
               shape in responses (spec §5.5.3).  The generated type widens
               to Record<string, never> because of Pydantic's union export. */
            renderEvidenceRef(ref as unknown as EvidenceRef | string, i),
          )}
          {uploads.map((u, i) => (
            <div
              key={`upload-${i}`}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 'var(--space-sm)',
                padding: 'var(--space-xs) var(--space-sm)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                fontSize: 'var(--font-size-body)',
                backgroundColor: u.status === 'error' ? 'var(--safety-critical-bg)' : 'var(--bg-surface)',
              }}
            >
              <span style={{ flex: 1 }}>{u.filename}</span>
              {u.status === 'uploading' && <span style={{ color: 'var(--text-secondary)' }}>Uploading...</span>}
              {u.status === 'confirming' && <span style={{ color: 'var(--text-secondary)' }}>Confirming...</span>}
              {u.status === 'done' && <span style={{ color: 'var(--status-complete)' }}>Done</span>}
              {u.status === 'error' && <span style={{ color: 'var(--status-error)' }}>{u.error}</span>}
            </div>
          ))}
        </div>

        {/* Add evidence button for rework/repair dispositions */}
        {(ncr.disposition === 'rework' || ncr.disposition === 'repair') && ncr.status !== 'closed' && (
          <div style={{ marginTop: 'var(--space-sm)' }}>
            <input
              type="file"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleEvidenceAdd(file);
                e.target.value = '';
              }}
              style={{ fontSize: 'var(--font-size-body)' }}
            />
          </div>
        )}
      </div>

      {/* Disposition form (visible when open) */}
      {ncr.status === 'open' && (
        <div style={sectionStyle}>
          <h3 style={{ margin: 0, marginBottom: 'var(--space-sm)', color: 'var(--text-primary)' }}>
            Submit Disposition
          </h3>

          {/* SOD-002 warning for initiator */}
          {isInitiator && (
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <ErrorBanner
                code="SOD-002"
                message="You initiated this NCR and cannot submit the disposition. A different engineer must disposition this report."
              />
            </div>
          )}

          {!hasRole('engineer') && !isInitiator && (
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <ErrorBanner
                message="Only engineers can submit NCR dispositions."
              />
            </div>
          )}

          {formError && (
            <div style={{ marginBottom: 'var(--space-md)' }}>
              <ErrorBanner
                code={formError.code}
                message={formError.message}
                onDismiss={() => setFormError(null)}
              />
            </div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-md)', opacity: canDisposition ? 1 : 0.6 }}>
            {/* Disposition select */}
            <div>
              <label style={{ display: 'block', ...fieldLabelStyle, marginBottom: 'var(--space-xs)' }}>Disposition</label>
              <select
                value={disposition}
                onChange={(e) => setDisposition(e.target.value as Disposition)}
                disabled={!canDisposition}
                style={inputStyle}
              >
                {DISPOSITION_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              {needsRemediationWo && (
                <p style={{ margin: 'var(--space-xs) 0 0 0', fontSize: 'var(--font-size-body)', color: 'var(--text-secondary)' }}>
                  This will reopen the associated operation for {disposition}.
                </p>
              )}
            </div>

            {/* Rationale */}
            <div>
              <label style={{ display: 'block', ...fieldLabelStyle, marginBottom: 'var(--space-xs)' }}>
                Rationale <span style={{ color: 'var(--status-error)' }}>*</span>
              </label>
              <textarea
                value={rationale}
                onChange={(e) => setRationale(e.target.value)}
                disabled={!canDisposition}
                rows={4}
                style={{ ...inputStyle, resize: 'vertical' }}
                placeholder="Provide rationale for this disposition..."
                required
              />
            </div>

            {/* MRB fields when required */}
            {ncr.mrb_required && (
              <>
                <div>
                  <label style={{ display: 'block', ...fieldLabelStyle, marginBottom: 'var(--space-xs)' }}>MRB Disposition</label>
                  <textarea
                    value={mrbDisposition}
                    onChange={(e) => setMrbDisposition(e.target.value)}
                    disabled={!canDisposition}
                    rows={2}
                    style={{ ...inputStyle, resize: 'vertical' }}
                    placeholder="MRB disposition details..."
                  />
                </div>
                <div>
                  <label style={{ display: 'block', ...fieldLabelStyle, marginBottom: 'var(--space-xs)' }}>MRB Authority ID</label>
                  <input
                    type="text"
                    value={mrbAuthorityId}
                    onChange={(e) => setMrbAuthorityId(e.target.value)}
                    disabled={!canDisposition}
                    style={inputStyle}
                    placeholder="MRB authority user ID..."
                  />
                </div>
              </>
            )}

            {/* Remediation WO for rework/repair */}
            {needsRemediationWo && (
              <div>
                <label style={{ display: 'block', ...fieldLabelStyle, marginBottom: 'var(--space-xs)' }}>
                  Remediation Work Order ID
                </label>
                <input
                  type="text"
                  value={remediationWoId}
                  onChange={(e) => setRemediationWoId(e.target.value)}
                  disabled={!canDisposition}
                  style={inputStyle}
                  placeholder="Required when linked WO is closed..."
                />
              </div>
            )}

            {/* Submit */}
            {canDisposition && (
              <div>
                <button
                  type="button"
                  onClick={() => {
                    if (rationale.trim()) setShowConfirm(true);
                  }}
                  disabled={!rationale.trim() || dispositionMutation.isPending}
                  style={{
                    padding: 'var(--space-sm) var(--space-md)',
                    border: 'none',
                    borderRadius: 'var(--radius-md)',
                    backgroundColor: 'var(--interactive)',
                    color: '#ffffff',
                    fontWeight: 600,
                    cursor: !rationale.trim() || dispositionMutation.isPending ? 'not-allowed' : 'pointer',
                    opacity: !rationale.trim() || dispositionMutation.isPending ? 0.6 : 1,
                    fontSize: 'var(--font-size-body)',
                  }}
                >
                  {dispositionMutation.isPending ? 'Submitting...' : 'Submit Disposition'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Dispositioned state: close action deferred */}
      {ncr.status === 'dispositioned' && (
        <div style={sectionStyle}>
          <h3 style={{ margin: 0, marginBottom: 'var(--space-sm)', color: 'var(--text-primary)' }}>Actions</h3>
          <p style={{ margin: 0, fontSize: 'var(--font-size-body)', color: 'var(--text-secondary)' }}>
            Close NCR action is deferred to a future release (GC-4).
          </p>
        </div>
      )}

      {/* Closed state: read-only */}
      {ncr.status === 'closed' && (
        <div style={sectionStyle}>
          <p style={{ margin: 0, fontSize: 'var(--font-size-body)', color: 'var(--text-secondary)' }}>
            This NCR is closed. No further actions are available.
          </p>
        </div>
      )}

      {/* Confirmation dialog */}
      <ConfirmationDialog
        open={showConfirm}
        title="Submit Disposition"
        message={
          needsRemediationWo
            ? `This will disposition the NCR as "${disposition.replace(/_/g, ' ')}" and may reopen the associated operation.`
            : `Submit disposition as "${disposition.replace(/_/g, ' ')}"?`
        }
        confirmLabel="Submit Disposition"
        onConfirm={handleDispositionSubmit}
        onCancel={() => setShowConfirm(false)}
        loading={dispositionMutation.isPending}
      />
    </div>
  );
}
