# Beth — Round 2



# Beth's Round 2 Review — Responding to Fran, Ben, and Codex

## Top-Line Assessment

The prior round feedback is substantive. Codex found real contract drift. Fran found real security gaps. Ben's strategic critique has a kernel of truth wrapped in overreaction. Here's where I land.

---

## Agreements — Fix These Before Build

**1. Cross-spec contract drift is the #1 blocker. (Codex #1-3, #5-6, #8, #25-26; Fran #1)**

Everyone caught this independently. That's a signal. The specific conflicts:

- `AddinSessionId` generation: SPEC-01 specifies an algorithm it doesn't own. **Fix: Strip algorithm from SPEC-01, keep only format regex + epoch semantics. SPEC-02 is sole algorithm owner.** Codex's suggestion is exactly right.
- `AddinReadyMessage` missing `ScreenshotRootPath`: SPEC-02 says it's there, SPEC-01 doesn't define it. **Fix: Add the field to SPEC-01.**
- Error code vocabulary: SPEC-02 invents 5 codes that don't exist in SPEC-01 enums. **Fix: Add them to the SPEC-01 enums.** Codex's expanded enum is the right shape.
- `ToolErrorCode` missing `InvalidUndoState`, `TemplateNotFound`: Same problem. **Fix: Add them.**
- `ToolCallLifecycleState` referenced but never declared in SPEC-01: **Fix: Add the enum.**
- Viewer `Guid.TryParse` vs 32-char hex: Direct bug. **Fix: Use protocol regex.**
- `IReadOnlyDhfRepository` / exception types: SPEC-06 defines contracts SPEC-04 must provide but SPEC-04 doesn't declare them. **Fix: SPEC-04 owns the interface and exception types. SPEC-06 references them.**
- Session closure semantics: SPEC-00 lists "orchestrator graceful shutdown" as a closure reason; SPEC-04 explicitly says it doesn't close on shutdown. **Fix: Align SPEC-00 to SPEC-04's model.**
- Test project naming: `BSVoiceCAD.Tests.Integration` vs `BSVoiceCAD.Integration.Tests`. **Fix: Standardize per Codex's naming scheme.**

I'd add one Codex missed: SPEC-00 glossary says "UUID generated fresh" for AddinSessionId while the actual format is 32-char hex hash. **Fix: Change glossary to "32-character lowercase hex session token."**

None of these are architectural — they're editorial. But they're build-blocking editorial. **Joe: a single reconciliation pass through SPEC-00 and SPEC-01 fixes 80% of these.**

**2. Dual-session on same workstation is a spec contradiction. (Beth R1, Codex #7 partial)**

SPEC-00 §6 says "Two concurrent add-in instances on the same machine produce distinct AddinSessionIds; SPEC-04 must handle the resulting dual-session scenario." SPEC-04's `OpenOrResumeSessionCore` closes ALL open sessions on the workstation before opening a new one. These can't both be true.

- **My recommendation: Drop the dual-session claim from SPEC-00.** Running two SolidWorks instances with two add-ins on one machine is not a hackathon scenario, not a demo scenario, and not a Medtronic pilot scenario. Supporting it adds complexity to session management for zero near-term value. If it's needed later, scope it then.
- If Joe insists on keeping it, SPEC-04's close-stale query needs `AND addin_session_id != @NewAddinSessionId` and the advisory lock scope needs rethinking.

**3. ACC-08 numbering collision. (Beth R1)**

SPEC-00 §3 ACC-08 = "Screenshot and feature-tree grounding return valid data." SPEC-06 §12 ACC-08 = "Viewer timeline renders in under 1 second." **Fix: Renumber SPEC-06's to ACC-10 (which is currently the viewer timeline ACC) or assign a new ID.** This is trivial but will cause test-matrix confusion if left.

---

## Disagreements — Don't Fix These Now

**4. Ben's "radical simplification" is wrong for this product.**

Ben's right that this is a lot of spec for a hackathon. He's wrong about the remedy.

- The DHF hash chain is not "theater." It's the product. Without it, BSVoice-CAD is "another AI CAD demo" — a category with zero defensibility. With it, it's "the only AI CAD tool that produces forensic-grade audit trails." That's the Medtronic pitch. That's the moat. Cutting it to "simple append-only log" kills the business case.
- "Ship the naive version first" ignores that the naive version has no competitive differentiation. There are already YouTube videos of people voice-controlling SolidWorks with GPT-4. The audit trail IS the product.
- "Kill the dual-runtime WebSocket bridge" — can't. SolidWorks COM is .NET Framework 4.8. The orchestrator needs .NET 8 for modern Anthropic SDK, Postgres drivers, and ASP.NET Core. The bridge is load-bearing, not optional.
- **What I'd concede to Ben:** The decoration extraction (Sonnet 4.6) and deep validation are correctly scoped as stretch. The `CrossWorkstationSessionConflictException` remediation text referencing a nonexistent CLI is aspirational — simplify the message to reference manual SQL only. And the descope priority list in SPEC-00 §3 is already the right instrument for time pressure.

**5. Fran's security concerns are real but mostly post-hackathon. (Fran #2, #3, #8)**

- Localhost viewer auth: Correct concern for production. Wrong priority for hackathon. The threat model is "single user on their own workstation running a demo." Adding per-process auth tokens to a localhost viewer adds integration complexity for zero demo-day value. **Post-hackathon: yes. Now: no.**
- WebSocket bridge auth: Same reasoning. A mutual session secret is the right eventual fix. For hackathon, the add-in and orchestrator are the only two processes that know the port, and they're both launched by the same user. **Document the gap, don't fix it now.**
- Replay/forgery via CallId: Valid in a multi-user or hostile-network scenario. Not valid for localhost single-user. **Post-hackathon.**
- **What I'd concede to Fran:** Add a single SDR (SDR-TL-011) in SPEC-00 that says "localhost-only deployment is a hackathon security boundary, not a production security control" and lists the specific gaps (no viewer auth, no bridge auth, no replay protection) with post-hackathon remediation owners. This costs nothing and prevents anyone from accidentally shipping the hackathon security posture to Medtronic.

**6. Fran's "session metadata hash" concern is acknowledged but correctly deferred. (Fran #6)**

SPEC-04 §3.2 already discloses this gap honestly. The trigger is defense-in-depth for hackathon. Computing `session_metadata_hash` and anchoring it in the first entry's hash is the right post-hackathon fix. Beth R1 already flagged this. **No spec change needed — the gap is documented and the fix is scoped.**

**7. Codex's type-safety improvements are nice-to-haves, not blockers. (Codex #9, #13, #14, #17, #19, #28)**

- Value objects for `MessageId`/`CallId`/`UndoGroupId` (#9): Good idea, adds ceremony. Raw strings with shared validators are fine for hackathon.
- `StateSnapshot` with optional `FeatureTreePreview` (#13): Architecturally cleaner but changes the wire contract shape. The current truncation + `get_feature_tree` fallback works. Don't redesign the snapshot for a problem that hasn't manifested.
- `CaptureState` removing `addinSessionId` param (#14): Fair point about leaking protocol into adapter. But the field is on `StateSnapshot` (which is a wire type), so someone has to set it. Moving it to the bridge layer is a one-line change during implementation, not a spec revision.
- `FeatureTreeQuery` options type (#17): Over-engineering a boolean. Two booleans are fine for 9 tools.
- Explicit hash material records (#19): Better type safety, more code. The column-list approach is tested by `HashColumnSyncTests`. Either works. Don't change during spec phase.
- Sum types for `DocumentState` (#28): Would be cleaner. Wire compatibility constraint makes this impractical without a mapping layer. Keep the flat record with validators.

**8. Codex's `ToolCallRegistry` lifecycle-state overlap concern is valid but manageable. (Codex #16)**

The add-in's registry states (`Queued`, `Executing`, `Completed`, `Cancelled`, `Abandoned`) do overlap with SPEC-03's lifecycle states. But they serve different purposes: registry states are transport-level idempotency; lifecycle states are business-level audit. The names overlap because the concepts overlap. **Fix: Add a comment in SPEC-02 that registry states are transport-scoped and do not imply lifecycle-state transitions. Use a separate enum name if confusion persists during implementation.**

---

## New Concerns

**9. SPEC-03 and SPEC-05 are still unreviewed.**

This is the biggest remaining risk. SPEC-03 (lifecycle) is the most complex module — it owns the state machine, timeout/recovery, DHF aggregation, and sits at the intersection of three seams. SPEC-05 (agent loop) owns the Anthropic API integration, system prompt, confirmation flow, and vision context. Both are load-bearing for the demo. **These specs need Oracle review before implementation starts on any module that depends on them.**

**10. No DB-write failure safe-state is defined. (Fran #18, partially)**

Fran is right that the spec doesn't say what happens when `AppendTurnAsync` fails durably. If the audit trail can't be written, can modeling continue? This is a product-level decision, not a module-level one.

- **My recommendation: Hard stop.** If DHF append fails after retry exhaustion, the orchestrator enters a locked state. No further turns. The audit trail IS the product — operating without it is operating without the value proposition. Log the failure, surface it to the user, and require restart.
- This needs a one-line addition to SPEC-00 §3 never-descoped list: "DHF append failure locks the session."

**11. Fran's malformed JSON handling concern is partially valid. (Fran #7)**

The specs define behavior for known message types with invalid fields but not for completely unparseable input. What happens when the WebSocket receives `{{{garbage`?

- SPEC-01 §3 defines the framing (one JSON doc per message) but not parse-failure behavior.
- **Fix: Add to SPEC-01 §3:** "If the reassembled message buffer is not valid UTF-8 or does not parse as a JSON object, the receiver sends a protocol error with `MalformedEnvelope` and the connection remains open. The raw bytes are not logged (may contain binary/sensitive data); only the byte count and a sanitized error summary are logged."
- Fuzz tests are stretch. The explicit behavior definition is mandatory.

**12. Advisory lock `hashtext` collision risk is real but low. (Fran #12)**

`hashtext` returns a 32-bit integer. With a small number of concurrent sessions (hackathon: 1), collision probability is negligible. At scale (hundreds of concurrent sessions), it becomes a concern. **For hackathon: document the limitation. Post-hackathon: switch to two-int advisory lock form.** Don't change the spec now.

---

## Summary: Required Changes Before Build

| Priority | Change | Owner |
|----------|--------|-------|
| **P0** | Reconcile `AddinSessionId` — strip algorithm from SPEC-01, keep format+semantics only | SPEC-01 |
| **P0** | Add missing fields to SPEC-01: `AddinReadyMessage.ScreenshotRootPath`, `ToolCallLifecycleState` enum, expanded error code enums, expanded `ToolErrorCode` | SPEC-01 |
| **P0** | Add `IReadOnlyDhfRepository`, `RepositoryUnavailableException`, `SessionNotFoundException` to SPEC-04 | SPEC-04 |
| **P0** | Fix viewer `addinSessionId` validation to use protocol regex, not `Guid.TryParse` | SPEC-06 |
| **P0** | Align session closure semantics — remove "orchestrator graceful shutdown" from SPEC-00 glossary | SPEC-00 |
| **P0** | Standardize test project naming across all specs | SPEC-00 |
| **P1** | Drop dual-session claim from SPEC-00 §6 or fix SPEC-04 close-stale query | SPEC-00/04 |
| **P1** | Fix ACC-08 numbering collision | SPEC-00/06 |
| **P1** | Add SDR-TL-011 (localhost security boundary) to SPEC-00 | SPEC-00 |
| **P1** | Add DHF-append-failure-locks-session to never-descoped list | SPEC-00 |
| **P1** | Add malformed JSON parse-failure behavior to SPEC-01 §3 | SPEC-01 |
| **P1** | Simplify `CrossWorkstationSessionConflictException` message — SQL path only, no CLI reference | SPEC-04 |
| **P2** | Add SDR-TL-010 (structured logging) as post-hackathon item | SPEC-00 |
| **P2** | Document `hashtext` collision limitation | SPEC-04 |
| **P2** | Review SPEC-03 and SPEC-05 | Pending |

**Verdict: The architecture holds. The contract drift is the only build-blocker, and it's editorial, not structural. Fix the P0s, then build.**