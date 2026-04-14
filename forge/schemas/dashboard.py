#!/usr/bin/env python3
"""
Module:   Dashboard Schemas
Purpose:  Pydantic response models for GET /api/dashboard/summary
          (UX spec v1.5.1 §5.1, build spec v1.4.2 §7 endpoint table).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: False

Why this design:
  The dashboard is the first page every authenticated user sees (UX
  spec §5.1) so its wire shape has to be stable across frontend slices
  — changing a tile key later means a breaking change to every role's
  home view.  That steers the design toward explicit typed objects
  (not ad-hoc dicts) and an "every tile is a uniform row" pattern so
  the frontend can iterate instead of hard-coding N conditionals.

  Tiles are role-filtered server-side.  UX spec §5.1 calls out
  Unresolved Conditional Findings and Overdue Inspections as
  "QA manager dashboard additions" — the spec language is explicit
  about audience.  Serving every tile to every dash:read caller
  would mean the "QA additions" language becomes a frontend-hiding
  convention rather than a server-enforced audience boundary, and
  a future UI bug (forgetting to hide a tile) would leak
  management-only counts to operators.  Instead each tile carries
  `visible_to_roles` and the service filters `tiles[]` to rows the
  caller can see.  The filter runs AFTER aggregation so the
  backend always computes accurate numbers — the only thing gated
  is visibility.

  DashboardTileOut carries a drill_through target so the frontend can
  render clickable cards without hard-coding routing magic strings.
  Every tile that should be clickable names its destination here.

  WO-status counts get a dedicated sub-object (not one tile per
  status) so the "Build status" card can render as a stacked bar or
  a breakdown without requiring a second API call.  The keys mirror
  the WOStatus enum values verbatim so a consumer that enumerates
  status can use them directly as map keys.  Build status is general
  production info visible to every dash:read role per §5.1
  ("Build progress bars link to work order detail"), so it lives at
  the top level rather than inside the role-filtered tiles list.

  Schedule status (build spec §7 names it as a dashboard item) is
  DEFERRED — the schedule model exists but no truthful "on track /
  at risk / behind" aggregate exists yet and faking one would ship a
  misleading tile.  The response deliberately omits any
  schedule_status field so consumers cannot accidentally depend on
  a stub; a test pins this contract so the deferral is enforced, not
  just documented.  When the CPM/milestone roll-up lands, schedule
  tiles can be appended to the same DashboardSummaryOut.tiles list
  without a contract break.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TileSeverity = Literal["info", "warn", "critical"]


class DashboardDrillThrough(BaseModel):
    """Where clicking a dashboard tile should take the user.

    Paths are the canonical frontend routes from UX spec §3.2.  Query
    params mirror the server-enforceable filter vocabulary (e.g. the
    Inspection Queue accepts `state=overdue` and `state=conditional`
    per §5.5.5).  Empty query dict means "no filter, just open the
    target page."

    Keeping query as a generic dict rather than a discriminated union
    because each tile's destination has its own filter vocabulary —
    typing every variant would balloon the schema for no frontend
    benefit (the client just passes the dict through to its router).
    """

    path: str = Field(
        ...,
        description=(
            "Canonical frontend route (UX spec §3.2), e.g. '/inspections'"
        ),
    )
    query: dict[str, str] = Field(
        default_factory=dict,
        description="Filter params to pre-select on the target page",
    )


class DashboardTileOut(BaseModel):
    """A single clickable card on the dashboard (UX spec §5.1).

    Uniform row shape so the frontend iterates instead of branching.
    `key` is the stable machine identifier (used for ordering, test
    assertions, and a11y labels).  `label` is the user-facing string.
    `value` is always an int for Phase 1 — every named tile is a
    count — but declaring it as int here rather than using a union
    keeps the OpenAPI contract simple; non-count tiles would get
    their own schema variant.

    `visible_to_roles` is informational on the wire — a tile only
    appears in the response if the caller is authorized to see it
    (the service filters the list).  It is included so a frontend
    feature flag (or an admin-mode "preview as role X" tool) can
    reason about audience without duplicating the mapping.
    """

    key: str = Field(
        ...,
        description="Stable machine identifier (e.g. 'open_ncrs')",
    )
    label: str
    value: int = Field(ge=0)
    severity: TileSeverity = "info"
    visible_to_roles: list[str] = Field(
        default_factory=list,
        description=(
            "Roles for which this tile is included in the response "
            "(UX spec §5.1 audience — server-enforced)."
        ),
    )
    drill_through: DashboardDrillThrough | None = None


class WorkOrderStatusCounts(BaseModel):
    """Per-status work-order totals for the Build Status card.

    Keys mirror forge.models.enums.WOStatus exactly so consumers that
    enumerate the enum can use the values directly without a mapping
    layer.  Zero is a valid count — the tile still renders with a
    zero bar rather than hiding, giving a consistent footprint.
    """

    draft: int = Field(ge=0, default=0)
    released: int = Field(ge=0, default=0)
    in_progress: int = Field(ge=0, default=0)
    hold: int = Field(ge=0, default=0)
    complete: int = Field(ge=0, default=0)
    closed: int = Field(ge=0, default=0)


class DashboardSummaryOut(BaseModel):
    """Top-level dashboard payload (UX spec v1.5.1 §5.1).

    Structure:
      - `tiles` is the ordered list of count-cards (open NCRs,
        overdue inspections, unresolved conditional findings).  The
        ordering is the server's recommended display order; the
        frontend renders top-to-bottom as-is.
      - `work_order_status_counts` backs the "Build Status" card
        that the UX spec names explicitly but doesn't coerce into a
        single count — WO status is inherently multi-valued.
      - `program_scope` echoes the program codes the server
        aggregated over so the frontend can render
        "showing PROG-RS25" or "across 3 programs" without
        re-reading the X-Program-Id header (avoids echo drift if the
        header and the authorized scope ever disagree).
      - `generated_at` supports the 60-second polling interval
        (UX §5.1) — clients can display "updated Ns ago" without
        keeping their own wall clock.
    """

    tiles: list[DashboardTileOut]
    work_order_status_counts: WorkOrderStatusCounts
    program_scope: list[str] = Field(
        default_factory=list,
        description=(
            "Program codes aggregated over.  This endpoint is "
            "single-program only (the middleware rejects "
            "X-Program-Id: *), so the list always contains exactly "
            "one entry — the code of the program named by the "
            "active X-Program-Id header.  Carried as a list rather "
            "than a scalar so the shape does not break if a future "
            "slice opens dashboard to a bounded multi-program mode."
        ),
    )
    generated_at: datetime
