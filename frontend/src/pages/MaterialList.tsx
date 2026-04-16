/**
 * Module:   MaterialList
 * Purpose:  Placeholder — backend GET /api/material-certs not yet exposed.
 * Rev:      0.2.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why placeholder: The generated OpenAPI schema confirms the backend
 * exposes POST /api/material-certs (receive), PATCH /{id}/accept, and
 * POST /{id}/consume — but no list GET.  Rather than call a 404-bound
 * URL we render an explicit placeholder until a future backend slice
 * adds the list endpoint (spec §5.7).
 */

import { EmptyState } from '@/components/EmptyState';
import { PageHeader } from '@/components/PageHeader';

export default function MaterialList() {
  return (
    <div className="page-content">
      <PageHeader title="Materials" />
      <EmptyState
        title="Material listing coming soon"
        message="The backend list endpoint (GET /api/material-certs) has not been implemented yet. Material cert receive, accept, and consume actions are available via their direct endpoints."
      />
    </div>
  );
}
