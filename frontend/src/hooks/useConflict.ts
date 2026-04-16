/**
 * Module:   useConflict
 * Purpose:  Helpers for detecting and extracting 409 version conflicts.
 * Rev:      0.2.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this helper: Centralizes 409 detection so every mutation handler
 * doesn't repeat the same error-shape inspection.  The ConflictModal
 * component consumes the extracted shape.
 *
 * The detection is shape-based (err.response?.status === 409) rather
 * than instance-based.  This way it works whether the underlying
 * runtime is axios, openapi-fetch via ApiError, or any other client
 * that preserves the { response: { status, data } } convention.
 */

interface ConflictInfo {
  entity_type: string;
  entity_id: string;
  your_version: number;
  current_version: number;
  message: string;
}

interface ErrorWithResponse {
  response?: {
    status?: number;
    data?: unknown;
  };
}

function asErrorWithResponse(err: unknown): ErrorWithResponse | null {
  if (err && typeof err === 'object' && 'response' in err) {
    return err as ErrorWithResponse;
  }
  return null;
}

export function isAxiosConflict(err: unknown): boolean {
  const e = asErrorWithResponse(err);
  return e?.response?.status === 409;
}

export function extractConflict(err: unknown): ConflictInfo | null {
  if (!isAxiosConflict(err)) return null;
  const e = asErrorWithResponse(err);
  const data = e?.response?.data as { error?: ConflictInfo } | undefined;
  const body = data?.error;
  if (!body) {
    return {
      entity_type: 'unknown',
      entity_id: '',
      your_version: 0,
      current_version: 0,
      message: 'Version conflict — another user modified this record.',
    };
  }
  return {
    entity_type: body.entity_type ?? 'unknown',
    entity_id: body.entity_id ?? '',
    your_version: body.your_version ?? 0,
    current_version: body.current_version ?? 0,
    message: body.message ?? 'Version conflict',
  };
}
