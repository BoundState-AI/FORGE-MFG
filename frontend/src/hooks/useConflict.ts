/**
 * Module:   useConflict
 * Purpose:  Helpers for detecting and extracting 409 version conflicts.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why this helper: Centralizes 409 detection so every mutation handler
 * doesn't repeat the same axios response inspection.  The ConflictModal
 * component consumes the extracted shape.
 */

import { AxiosError } from 'axios';

interface ConflictInfo {
  entity_type: string;
  entity_id: string;
  your_version: number;
  current_version: number;
  message: string;
}

export function isAxiosConflict(err: unknown): boolean {
  return err instanceof AxiosError && err.response?.status === 409;
}

export function extractConflict(err: unknown): ConflictInfo | null {
  if (!isAxiosConflict(err)) return null;
  const axErr = err as AxiosError<{ error?: ConflictInfo }>;
  const body = axErr.response?.data?.error;
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
