# FORGE-MFG Code Provenance

This document records the provenance posture of the FORGE-MFG source tree.
Its purpose is to give reviewers, auditors, and downstream integrators a
clear, honest account of where the code in this repository came from,
what patterns were reused from prior BoundState work, and where
follow-up work is still owed. It is a governance artifact, not a
marketing statement: if a provenance gap exists, it is named here
rather than papered over.

## Provenance Categories

Three tags define how any given unit of code in this repository was
produced. Every file is expected to fall into exactly one of the first
two categories; the third exists specifically to flag gaps.

- `net-new`
  Code authored directly for this repository from scratch against
  `FORGE_MFG_BUILD_SPEC_v1.4.2.md` and
  `FORGE_MFG_FRONTEND_UX_SPEC_v1.5.1.md`. The spec sections drove the
  design; no prior implementation was copied. Manufacturing domain
  models, the idempotency middleware, the transactional outbox worker,
  the CPM scheduling engine, the WORM replication worker, manufacturing
  authority packs, and the daily audit integrity verification job are
  all net-new.

- `pattern-reuse`
  Code that adapts structural patterns from prior BoundState projects
  (`boundstate_oracle`, `boundstate_mira`, `boundstate_forge`,
  `forge_ic`). No direct code copy is made under this tag; only
  architectural decisions are carried forward. The specific patterns
  reused are enumerated in `CLAUDE.md` under the "Reuse Map" section
  and include the FastAPI app-factory layout, the Cognito JWKS
  validation approach, SSE transport patterns, the advisory-only chat
  contract, pack validation/promotion flow, rules-engine shape,
  evidence-provider shape, audit integrity concepts, and the Merkle
  verification helper pattern.

- `unknown`
  Any code whose provenance cannot be clearly attributed to one of the
  two categories above. This category should be empty. Its presence on
  any file is treated as a review gap and a blocker for sign-off, not
  a neutral state.

## Current Provenance Posture

The following claims describe the repository as of the date this file
was introduced. They are intended to be falsifiable: any reviewer who
finds an exception should record it and open a provenance-review item.

- All backend Python code under `forge/`, all tests under `tests/`, and
  all frontend TypeScript under `frontend/src/` and `frontend/tests/`
  is either `net-new` or `pattern-reuse`. This code was authored via
  pair-programming between Dr. Joseph Wilde (BoundState AI LLC) and
  Claude, Anthropic's large language model in the Opus family. Dr.
  Wilde is the sole author-of-record.

- Dependency choices (FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2,
  React 18, Zustand, openapi-fetch, and the rest of the application
  stack) follow the technology stack mandated by Build Spec section
  2.2. Dependency selection is not itself a source-code provenance
  question, but it is recorded here so reviewers can distinguish
  mandated stack choices from discretionary authorship decisions.

- No code has been copied from proprietary third-party sources. No
  AGPL- or GPL-licensed code is present in the repository.

- Generated artifacts (the OpenAPI-generated frontend API client,
  Alembic migration skeletons, and similar) derive deterministically
  from net-new inputs and are treated as `net-new` by the provenance
  of their generator.

## Per-file Tagging Plan

Per-file provenance tags are a follow-up task and are not applied in
this pass. When applied, the convention is a single-line comment
placed immediately after the BoundState header of each file, in one of
these two exact forms:

```
# provenance: net-new
# provenance: pattern-reuse(<source>)
```

Where `<source>` names the originating BoundState project
(for example, `pattern-reuse(boundstate_mira app-factory)` or
`pattern-reuse(boundstate_oracle chat-pipeline)`). Frontend
TypeScript files use `//` comment syntax but the same tag grammar.
The absence of either tag on a source file, once per-file tagging is
rolled out, is equivalent to an `unknown` tag and must be resolved.

## Third-Party Review

An independent third-party provenance review is open as audit item 20
and has not yet been scheduled. Until that review is completed and its
findings are closed out, the claims in the "Current Provenance
Posture" section above are self-attested by the author-of-record and
have not been independently verified. This document will be updated to
record the review's scope, reviewer, date, and findings once the
engagement is scheduled and executed.
