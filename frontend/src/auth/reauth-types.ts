/**
 * Module:   Reauth Type Augmentations
 * Purpose:  Discriminated-union refinement for the REAUTH_REQUIRED error.
 * Rev:      0.1.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Kept in a separate file from reauth.ts so the runtime module can
 * import the type without circular-import concerns from page-level
 * code that reaches for the same type.
 */

export interface ReauthRequiredError {
  response: {
    status: 401;
    data: {
      detail: {
        error: {
          code: 'REAUTH_REQUIRED';
          /**
           * GC-4: wo_close + ncr_close added to the backend
           * AuthFreshnessAction enum.  Spec §11.3 lists all four
           * as safety-critical protected actions.
           */
          action:
            | 'sign_off'
            | 'ncr_disposition'
            | 'wo_close'
            | 'ncr_close';
          auth_age_seconds: number | null;
          max_auth_age_seconds: number;
          message: string;
        };
      };
    };
  };
}
