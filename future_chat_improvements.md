# Future Chat Improvements

Review of the Slice 5A ORACLE Chat surface against the BoundState sibling
repos (`boundstate_oracle`, `boundstate_mira`) to identify alignment,
intentional divergence, and gaps to close before Bedrock integration.

Dated 2026-04-14.

---

## Summary

We match the patterns where it matters. Our pipeline maps cleanly onto
Oracle's implicit stages (`_gatekeep_query → _retrieve_chat_context →
intent classify → _maybe_run_llm_reasoning → _maybe_run_llm_summary`),
and our SSE transport uses the same `done`/`error` frames Mira does.

| Concern | Oracle / Mira | forge-mfg 5A | Same? |
|---|---|---|---|
| Advisory-only contract | `OracleChatEnvelope.advisory_only=True` + grader regex for approve/certify/waive/sign-off | `guards._ACTION_VERBS` + `ADVISORY_ONLY` refusal code | ✅ |
| Pack checksums | Oracle `sha256:{hexdigest}` at `cli.py#L313` | manifest entry `sha256:` over file bytes | ✅ |
| Deterministic fallback | Oracle `_render_grounded_pack_answer`; Mira streaming fallback | default reasoner + summarizer are deterministic; Bedrock is a seam | ✅ |
| SSE frames | Mira: `text` + `done` + `error` | `chunk` + `citation` + `done` + `error` (per spec §5.15) | ✅ (superset) |

---

## Intentional divergences (spec-driven)

- **QT-001..QT-008 named templates.** Oracle uses keyword → render
  dispatch with no stable IDs. We have numbered templates because spec
  §6.2 enumerates them for the "no dynamic SQL" guarantee (§14).
  Correct to diverge.
- **ChatQueried event.** Neither sibling emits a domain event for
  queries; we do via the outbox (spec §4.5). Correct.
- **Structured citation dicts.** Oracle ships citation as a string
  (`"{std} section {x} (p. {y})"`); we ship
  `{rule_id, title, pack_id, section}`. Better for the React frontend;
  worth keeping.

---

## Gaps worth closing

### 1. Pack promotion lifecycle (spec §15 compliance)

Oracle's `pack_validator.py` at line 50 has
`status ∈ {draft, promoted}` plus `promoted_at`, `promoted_by`, and
`promotion_notes`. Spec §15.1 requires the same lifecycle:

> 3. **Approval:** QA signs off; recorded in manifest.json.

Our [packs/manifest.json](packs/manifest.json) entries carry only
`id`, `file`, `version`, `description`, `sha256`. The manifest and
loader must add promotion metadata before we can claim §15 coverage.

**Proposed manifest entry shape:**

```json
{
  "id": "NPR-8735.2C",
  "file": "npr-8735-2c.json",
  "version": "C",
  "sha256": "...",
  "status": "promoted",
  "promoted_at": "2026-04-14T12:00:00Z",
  "promoted_by": "qa.rep@msfc.nasa.gov",
  "promotion_notes": "Reviewed against NPR 8735.2C printed copy."
}
```

**Loader behavior:**

- Parse `status` (default `"draft"` for back-compat).
- Surface the status on the `Pack` dataclass so
  `adapter._execute_intent` can refuse queries that try to draw on a
  `draft` pack in production (`settings.env == "production"`).
- Keep `draft` usable in local + test so QA reviewers can preview a
  pack before promotion.

**Effort:** ~20 min (manifest edit, two fields on Pack, one loader
branch, one test that a `draft` pack is refused in production mode).

**Touches:**
- [forge/chat/packs.py](forge/chat/packs.py)
- [packs/manifest.json](packs/manifest.json)
- [tests/unit/test_chat_packs.py](tests/unit/test_chat_packs.py)

### 2. Grader dataclass for post-generation safety

Oracle's `OracleChatGrade(passed, failures[])` at `chat_grader.py#L40`
runs *after* generation with failure codes like
`prohibited_decision_language`, `review_only_not_labeled`,
`missing_inputs_not_surfaced`. Our guards are pre-generation only.

When we wire Bedrock behind the reasoner/summarizer seam (today both
are deterministic), a hallucinated "approved" could slip past guards
that only see the user query. A post-generation grader catches this.

**Proposed shape** (lives alongside guards, or as its own
`forge/chat/grader.py`):

```python
@dataclass(frozen=True, slots=True)
class ChatGrade:
    passed: bool
    failures: tuple[ChatGradeFailure, ...]

@dataclass(frozen=True, slots=True)
class ChatGradeFailure:
    code: str          # e.g. "prohibited_decision_language"
    matched: str       # the exact phrase that triggered
    span: tuple[int, int]
```

**Invocation:** `adapter.answer` runs `grade(result.advisory_answer)`
after the reasoner/summarizer finish; a non-passing grade downgrades
the response to a refusal with code `POST_GENERATION_BLOCKED` so the
HTTP layer still returns 200 with a structured refusal, not a 500.

**Effort today:** ~30 min to ship a stub whose rules match the current
`guards._ACTION_VERBS` set (so the deterministic path still passes).
The seam exists, future Bedrock wiring is just "call the seam."

**Touches:**
- `forge/chat/grader.py` (new)
- [forge/chat/adapter.py](forge/chat/adapter.py) — wire the post-grade
- [tests/unit/test_chat_grader.py](tests/unit/test_chat_grader.py) (new)

### 3. Reasoning output validation (Bedrock-time only)

Oracle's `chat_engine.py#L2202` validates that LLM JSON cites only
rules present in the retrieved set — a hallucination guardrail. Our
deterministic summarizer can't cite anything that wasn't ranked, so
this is a non-issue today. The day the reasoner becomes a Bedrock
call, a validator must run before `build_reasoning` accepts the
model's output.

**Proposed check:**

```
for citation in model_output.citations:
    if citation.rule_id not in {r.rule.rule_id for r in ranked}:
        raise ReasoningValidationError(...)
```

**Effort:** bundle with the Bedrock wiring work; no action today.

---

## Not gaps (on purpose)

- **Multi-turn session state (Mira).** Spec §5.12 doesn't call for it;
  stateless keeps idempotency and audit simpler. Revisit only if
  operators ask for "refine that" follow-ups.
- **Adaptive thinking blocks (Mira `budget_tokens=1024`).** We have no
  Bedrock call yet; N/A until we wire one.
- **Secondary rationale corpus (Oracle's
  `nasa_std_6016c_rationale.json`).** Our pack rules already carry a
  `text` field; we can extend the rule schema later without breaking
  the loader (unknown keys are ignored).
- **"Authority basis vs. citations" distinction
  (`OracleChatEnvelope.authority_basis`).** Our citation shape already
  pairs `rule_id` with `pack_id`; the distinction is representable.
  Add an explicit field only if the frontend asks for one.

---

## Recommended sequencing

Before Bedrock wiring (no hard deadline yet):

1. **Pack promotion metadata** — closes a real spec §15 gap, 20 min.
2. **`ChatGrade` dataclass stub** — establishes the seam without
   adding runtime dependencies, 30 min.

After Bedrock reasoner/summarizer land:

3. **Reasoning output validator** — runs before `build_reasoning`
   accepts model output.
4. **Wire `ChatGrade` stub to Bedrock-aware rules** (inject-attempt
   detection in generated text, not just user input).

All four changes are structural, not speculative; they map 1:1 onto
patterns already proven in the sibling repos.
