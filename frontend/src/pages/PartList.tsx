/**
 * Module:   PartList
 * Purpose:  Placeholder — backend GET /api/parts not yet exposed.
 * Rev:      0.2.0
 *
 * BoundState Proprietary
 * Co-Authored-By: Dr. Joseph Wilde, BoundState AI
 * Co-Authored-By: Claude "Beth" (Opus 4.6)
 *
 * Why placeholder: The generated OpenAPI schema confirms the backend
 * exposes POST /api/parts (create) and PATCH /{part_id}, plus
 * GET /{part_id}/traceability, but not a list GET or single-part GET.
 * Rather than call a 404-bound URL we render an explicit placeholder
 * until a future backend slice adds the list endpoint (spec §5.8).
 */

import { EmptyState } from '@/components/EmptyState';
import { PageHeader } from '@/components/PageHeader';

export default function PartList() {
  return (
    <div className="page-content">
      <PageHeader title="Parts" />
      <EmptyState
        title="Part listing coming soon"
        message="The backend list endpoint (GET /api/parts) has not been implemented yet. Part traceability is available via the per-part traceability endpoint."
      />
    </div>
  );
}
