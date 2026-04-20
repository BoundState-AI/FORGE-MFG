# Beth — Round 1



# Beth's Architecture Review — SPEC-00 through SPEC-06 (excluding SPEC-03, SPEC-05)

## Strongest Concern: This Spec Suite Is Ready to Build

I'll say it plainly — this is the most complete pre-implementation architecture I've reviewed at BoundState. The module boundaries are clean, the seam contracts are testable, and the dependency direction is enforced at multiple levels. My concerns below are real, but they're refinements, not structural defects.

---

## Strategic Assessment

**Does this serve the business vision?** Yes. The DHF-as-audit-trail positions us for Medtronic without claiming compliance we don't have. SDR-TL-006's non-compliance disclaimer is the right call — I've seen companies lose credibility by over-claiming Part 11. The gap analysis table is honest.

**Does it scale to new domains?** The BSMS mandate (SDR-TL-009) is the right long-term bet. The 9-tool surface is deliberately small, and the extension points (stretch tool placeholders, `IsImplemented` flag, decoration types) are clean. New verticals can add tools without touching the hash chain or session lifecycle.

**Is the abstraction level right?** Almost. A few places are over-specified for hackathon scope, and one place is under-specified. Details below.

---

## SPEC-00 — Orchestration Contract

- **Clean.** Module map, seam definitions, dependency direction, review order — all structurally sound.
- **SDR-TL-009 is the right length.** I know it reads long, but this is the most important architectural decision in the company. Every word earns its place. The enforcement checklist (4 questions) is practical.
- **Minor: ACC table gap.** ACC-08 in SPEC-00 §3 says "Screenshot and feature-tree grounding return valid data" but ACC-08 in SPEC-06 §12 says "Viewer timeline renders a 20-entry session in under 1 second." These are different capabilities with the same ID. **Fix: renumber one of them.** The SPEC-06 ACC-08 should probably be ACC-10 (which is currently "Viewer displays turn timeline from DHF" — related but not identical to the performance criterion).

## SPEC-01 — Wire Protocol

- **The strongest spec in the set.** Polymorphic families are well-defined, hash canonicalization is deterministic, and the test requirements are comprehensive.
- **`decimal` for `FeatureParameterNumber` is the right call.** Floating-point nondeterminism in hash chains is a class of bug that's nearly impossible to debug in production.
- **Concern: `SelectionRef` sort order in `HashCanonicalizer`.** The spec says `PersistentSelectionRef` first, then `SketchSelectionRef`, each sorted by their respective ID fields. But what happens when the same `PersistentId` appears twice with different `DisplayLabel` values? The sort is by `PersistentId` only — if two refs share a `PersistentId` (possible if the same entity is selected twice via different paths), the sort is nondeterministic. **Recommendation:** Add a tiebreaker on `DisplayLabel` (or `DocumentId`) to the sort spec, or assert uniqueness of `PersistentId` within a single `ActiveSelection` list.
- **4 MiB limit is fine for hackathon.** The truncation + `get_feature_tree` fallback is the right 80% solution. Don't build pagination until you have a model that actually exceeds it.
- **`ScreenshotResult.FilePath` co-location constraint is well-documented.** The v2 migration note is exactly the right level of forward-looking — acknowledges the debt without building the bridge.

## SPEC-02 — SolidWorks Adapter

- **Threading model is correct and well-defended.** The `SynchronizationContext` capture, startup probe, reentrancy guard, and two-state cancellation model are all architecturally sound. This is the hardest part of the system to get right, and it reads like someone who's been burned by COM threading before.
- **`ToolCallRegistry` is session-scoped, in-memory only — correct.** The explicit "MUST NOT add persistence" note prevents the most common mistake in this pattern.
- **Concern: `ToolInProgress` flag is a boolean, not a counter.** If a tool handler calls another service method that also dispatches through `ISwCommandQueue` (reentrancy per §2.3), the flag is set/cleared by the outermost `ToolDispatcher` call. But if `EventRelay` fires during a nested reentrant call, `ToolInProgress` is still `true` — correct behavior. However, if someone adds a second `ToolDispatcher`-like entry point in the future, the flag semantics break. **Recommendation:** Add a comment on `ToolInProgress` that it is owned exclusively by `ToolDispatcher` and must not be set/cleared by any other component. Or make it a `private` field with a `ToolDispatcher`-scoped accessor.
- **`AddinSessionIdGenerator` fallback path is well-designed.** The `%LOCALAPPDATA%\.machine-id` fallback for restricted accounts is pragmatic. The `Guid.NewGuid()` nonce guarantees uniqueness regardless of the machine-id source.
- **Minor: `DisconnectHandler` iterating `Queued` entries on disconnect.** The spec says it calls `Cancel()` on each tracker's `CancellationTokenSource`. But the `TaskScheduler`'s queued-cancel behavior is cooperative — if the delegate is already being dequeued by the UI thread at the exact moment of cancel, there's a race. The spec handles this correctly (the task transitions to `Canceled` only if it hasn't started), but the `Abandoned` state for `Executing` calls should also cover the "cancel arrived during dequeue but before execution started" window. **Verify:** the `TaskScheduler.FromCurrentSynchronizationContext()` implementation on .NET Framework 4.8 treats cancel-during-dequeue as `Canceled`, not `RanToCompletion`. If not, this is a Day-0 validation item.

## SPEC-04 — DHF Ledger

- **The hash model is the crown jewel.** Two-level hash coverage (entry + tool_calls_hash), explicit column lists, canonical enum mappings, timestamp normalization — this is the right architecture for a regulated audit trail.
- **SDR-04-008 (cross-workstation hard error) is the correct call.** Silent auto-close was a data-integrity disaster waiting to happen. The structured exception with actionable remediation text is exactly right.
- **Concern: `session_metadata_hash` is deferred.** The spec acknowledges (§3.2) that session metadata is not covered by the entry hash chain, and that a trigger bypass could alter it without invalidating entries. The trigger is defense-in-depth, but the gap is real. **Recommendation for post-hackathon:** Compute `session_metadata_hash` at INSERT time and include it in the first entry's `previous_hash` computation (or a dedicated field). This closes the gap without changing the entry hash model for subsequent entries. For hackathon: the trigger is sufficient. Document the gap in the Medtronic pitch as "known, planned closure."
- **Concern: `DhfEnumMappings` exhaustiveness.** The switch expressions throw `ArgumentOutOfRangeException` for unknown enum values, which is correct. But if someone adds a new `TurnType` variant and forgets to update the mapping, the architecture test catches it only if the test enumerates all enum values. **Verify:** `EnumMappingTests` uses reflection to enumerate all values of each mapped enum and asserts each one round-trips. If it does, this is covered. If it uses hardcoded test cases, it's fragile.
- **`CloseSessionAsync` acquires the per-session advisory lock.** This is the right fix for the close-during-append race. The lock key format (`dhf_append:{sessionId}`) matches between `AppendTurnCore` and `CloseSessionAsync`, which is load-bearing. **Recommendation:** Add a constant or helper for the lock key format to prevent copy-paste divergence.
- **Minor: `OpenOrResumeSessionCore` closes stale sessions on THIS workstation.** The `UPDATE dhf.sessions SET ended_at = NOW() WHERE ended_at IS NULL AND workstation_id = @WorkstationId` closes ALL open sessions on the workstation, not just the one with the matching `addin_session_id`. This is correct for the "lazy cleanup after orchestrator restart" scenario, but it means if two SolidWorks instances are running on the same machine with different `AddinSessionId`s, opening a session for one will close the other's. **Question:** Is this the intended behavior? SPEC-00 §6 says "Two concurrent add-in instances on the same machine produce distinct AddinSessionIds; SPEC-04 must handle the resulting dual-session scenario." The current implementation does NOT handle it — it closes the other session. **This is a spec contradiction.** Either the close-stale logic needs to be scoped to `AND addin_session_id != @NewAddinSessionId`, or SPEC-00 needs to drop the dual-session claim.

## SPEC-06 — Viewer

- **Correctly minimal.** Read-only, isolated DI, no write paths, polling instead of WebSocket push. This is the right 80% viewer.
- **SDR-06-003 enforcement is multi-layered.** Isolated DI container + DI test + architecture test + interface constraint. Four independent mechanisms for one invariant is appropriate for a regulated-adjacent system.
- **SDR-06-006 (non-fatal stretch failures) is the right call.** A misconfigured screenshot path should never prevent the core modeling session from running.
- **Path traversal guards are thorough.** The sibling-directory bypass prevention (trailing `Path.DirectorySeparatorChar` on the normalized root) is a detail most specs miss. Good.
- **Concern: `IReadOnlyDhfRepository` is defined in this spec but owned by SPEC-04.** The interface contract in §3a is detailed enough to implement against, but there's a risk of drift if SPEC-04 changes the return types. **Recommendation:** The interface definition should live in SPEC-04 with a cross-reference from SPEC-06. Currently the spec reads as if SPEC-06 is defining SPEC-04's contract, which inverts the dependency direction. The dependency arrow is SPEC-06 → SPEC-04, so SPEC-04 should own the interface shape.
- **`ValidateChainAsync` cancellation contract is well-specified.** The requirement that the method returns `Truncated=true` instead of throwing `OperationCanceledException` is a good API design decision — it prevents the viewer from needing to distinguish "validation failed" from "validation timed out."

---

## Cross-Cutting Concerns

### What's Missing

1. **SPEC-03 and SPEC-05 are not in this review.** The seams that touch them (B, C, D, E) are well-defined in SPEC-00, but I can't verify that the lifecycle layer and agent loop honor those contracts without seeing their specs. **This is the highest-risk gap.** SPEC-03 is the most complex module (state machine + timeout + recovery + DHF aggregation), and it's the one most likely to violate seam contracts under pressure.

2. **No observability spec.** Serilog is mentioned in SPEC-06 and `FileLogger` in SPEC-02, but there's no cross-cutting decision about structured logging, correlation IDs, or metrics. For hackathon this is fine. For Medtronic, you need a logging SDR that ensures every DHF-relevant event is traceable from user utterance to DB row. **Recommendation:** Add SDR-TL-010 (Structured Logging) as a post-hackathon item in SPEC-00.

3. **No graceful degradation spec for Anthropic API failures.** SDR-TL-003 locks Opus 4.7 as the primary model, but there's no cross-cutting decision about what happens when the API is unreachable, rate-limited, or returns malformed responses. This probably lives in SPEC-05, but it affects SPEC-03 (lifecycle state for a turn that never got a model response) and SPEC-04 (what DHF entry is written for a failed turn). **Flag for SPEC-05 review.**

### What's Over-Engineered (for hackathon)

1. **`CrossWorkstationSessionConflictException` with `dhf-admin close-session` remediation text.** The exception itself is the right call. The CLI reference in the error message is aspirational — that CLI doesn't exist and won't exist during the hackathon. **Recommendation:** Keep the exception, simplify the message to reference the manual SQL path only. Add the CLI reference when the CLI exists.

2. **Deep validation with integrity-event decorations and atomic deduplication.** This is stretch scope and the spec treats it as such, but the implementation detail (partial unique index on `payload->>'dedupKey'`) is production-grade infrastructure for a feature that may never ship in the hackathon window. **Not a problem** — it's well-scoped as stretch — but don't let it creep into mandatory scope.

### What's Under-Specified

1. **The dual-session scenario (SPEC-00 §6 vs SPEC-04 §6.1).** As noted above, this is a contradiction. Resolve it before implementation starts.

---

## Verdict

**Ship it.** Fix the dual-session contradiction (SPEC-00 §6 vs SPEC-04 §6.1) and the ACC-08 numbering collision before handing to implementation. Everything else is refinement that can happen during build.

The architecture serves the business vision, maintains the competitive moat (GPTL, evidence-first, negative selection via BSMS), and will still make sense in 6 months. The seam contracts are clean enough that Codex can implement individual modules without understanding the whole system. The 80/20 philosophy is respected — the mandatory scope is achievable, and the stretch scope is well-bounded.