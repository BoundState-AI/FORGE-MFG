# Fran — Round 2

1. **[Critical] The spec suite still contains contract contradictions that will cause integration failure before first demo**
   - **What’s wrong:** Prior reviewers already flagged this, and I agree: the cross-spec contract is not converged.
     - `AddinSessionId` is described as:
       - a “UUID” in SPEC-00 glossary,
       - a 32-char lowercase hex hash in SPEC-01,
       - a different-generation algorithm with fallback and nonce in SPEC-02.
     - `AddinReadyMessage` in SPEC-02 includes `screenshotDir`; SPEC-01 does not.
     - SPEC-02 emits protocol error conditions not present in SPEC-01 enums (`HandshakeIncomplete`, `InvalidCallId`, `ExpiredCallId`, `TerminatedCallId`, `InternalError`).
     - SPEC-04 says SPEC-06 depends on read-only repository contracts, but SPEC-06 is effectively defining those contracts itself.
     - SPEC-00 says dual same-machine add-in sessions must be handled; SPEC-04 currently closes open same-workstation sessions wholesale.
   - **Why this breaks at 3 AM:** You will get a system that compiles in pieces and fails in wiring:
     - add-in sends a message the orchestrator cannot deserialize,
     - viewer asks for exceptions/interfaces that do not exist,
     - recovery/session logic closes the wrong session.
   - **Compliance angle:** This violates the spirit of SWE-013/SWE-032 traceability and interface control. You do not have a stable, singular interface definition yet.
   - **Fix:**
     1. Freeze a **Normative Interface Matrix** in SPEC-00 listing:
        - message DTO fields,
        - all shared enums,
        - repository interfaces,
        - session ID format/ownership,
        - closure semantics.
     2. Make one spec the owner for each shared artifact:
        - protocol DTOs/enums → SPEC-01
        - add-in session generation algorithm → SPEC-02
        - repository interfaces/exceptions → SPEC-04
     3. Add a build-breaking `CrossSpecContractSyncTests`.
     4. Do not begin implementation of SPEC-03/05 until this is resolved.

2. **[Critical] Same-machine multi-session semantics are still contradictory and dangerous**
   - **What’s wrong:** SPEC-00 explicitly says two concurrent add-in instances on the same machine produce distinct `AddinSessionId`s and SPEC-04 must handle dual-session scenarios. But SPEC-04 `OpenOrResumeSessionCore` closes any open sessions on the same workstation before opening a new one.
   - **Why this breaks:** Legitimate concurrent sessions on one workstation will be silently terminated. That is data loss and audit corruption, not a cosmetic issue.
   - **Why this matters operationally:** This is exactly the sort of “works fine in test, corrupts evidence in edge deployment” defect that shows up under demo pressure or lab workstations with multiple SW instances.
   - **Fix:**
     1. Decide policy:
        - either **single active DHF session per workstation** is the rule, or
        - multiple same-workstation sessions are supported.
     2. If supported, scope stale cleanup by `addin_session_id`, not workstation.
     3. If not supported, remove the contrary claim from SPEC-00 and fail hard when a second same-workstation active session appears.
     4. Add acceptance tests for the chosen rule.

3. **[Critical] Localhost is still being treated as trusted for both viewer and add-in bridge**
   - **What’s wrong:** I agree with prior concerns. Both:
     - Viewer (`127.0.0.1:7879`, no auth)
     - Add-in bridge (`127.0.0.1:7878`, no client auth)
     assume localhost is safe.
   - **Why this breaks in production:** Any local process can:
     - scrape DHF data,
     - trigger SolidWorks mutations,
     - request screenshots,
     - poison logs/audit entries.
   - **Security classification:** OWASP A01 / A05. Also IP-sensitive exposure of design state and BoundState workflow logic.
   - **Fix:**
     1. Introduce a startup-generated **shared secret** for orchestrator↔add-in IPC.
     2. Require it in handshake or WebSocket subprotocol.
     3. Protect viewer endpoints with a session token even on localhost.
     4. Add origin checks and explicit no-CORS posture.
     5. Document localhost as an **untrusted boundary**.

4. **[High] Session metadata is still not cryptographically anchored**
   - **What’s wrong:** SPEC-04 admits `user_identity`, workstation, versions, and protocol metadata are only trigger-protected, not hash-chain protected.
   - **Why this matters:** Provenance is not just the entry chain. If someone changes who performed the session or on what workstation, your chain still says “valid.”
   - **Compliance concern:** This weakens evidentiary integrity and attribution. For regulated-adjacent claims, this is a real gap.
   - **Fix:**
     1. Compute `session_metadata_hash` at session creation.
     2. Include it in first-entry hash material or all-entry hash material.
     3. Surface metadata-verification status in validator output.
     4. Add explicit tests for metadata tamper detection.

5. **[High] DHF write-path failure behavior remains underspecified**
   - **What’s wrong:** Viewer has repository-unavailable semantics. DHF writes do not have a clearly defined safe-state when Postgres is unavailable, migration failed, disk is full, or transaction retries exhaust.
   - **Why this breaks:** If the audit ledger cannot persist, do you keep letting the AI mutate CAD? If yes, your “audit trail foundation” framing becomes misleading immediately.
   - **What happens at 3 AM:** Postgres hiccups, append fails, operator keeps modeling, now you have unlogged actions and no deterministic recovery stance.
   - **Fix:**
     1. Add a top-level rule in SPEC-00 and SPEC-04:
        - **audit persistence failure locks new turns** until persistence is restored, or
        - system enters explicit degraded mode with no compliance framing.
     2. Make recovery event append failure also lock the session.
     3. Add operator-visible health status.
     4. Add tests for DB outage during append and during recovery-event append.

6. **[High] Raw-edge parsing and malformed JSON handling are still not fully specified**
   - **What’s wrong:** Envelope validation is defined after deserialization, but many failures happen before typed materialization:
     - malformed JSON,
     - invalid UTF-8,
     - missing discriminator,
     - duplicate keys,
     - deeply nested payloads.
   - **Why this breaks:** Without a raw-edge contract, actual parser behavior is implementation-dependent and likely inconsistent across .NET 8 and .NET Framework.
   - **Fix:**
     1. Specify a raw receive pipeline in SPEC-01:
        - bytes → UTF-8 validation → DOM parse → discriminator extract → typed deserialize → envelope validate.
     2. Define duplicate-key handling policy.
     3. Define maximum nesting depth.
     4. Add fuzz tests and malformed-payload tests in integration.

7. **[High] Screenshot artifacts are still overexposed and overly coupled to filesystem paths**
   - **What’s wrong:** Absolute file paths are transmitted/stored and later served. This leaks environment details and makes artifact portability, redaction, and security harder.
   - **Why this matters:** Paths reveal usernames, machine layout, and local storage conventions. They are also brittle across replay/export/restore.
   - **Fix:**
     1. Replace absolute path with artifact ID or relative path at the protocol layer.
     2. Resolve root path only in adapter/viewer.
     3. Add restrictive directory ACL guidance.
     4. Clarify retention and cleanup provenance in DHF.
     5. Ensure public/demo exports never surface raw host paths.

8. **[High] Viewer auth and screenshot-serving controls are insufficient for IP-sensitive content**
   - **What’s wrong:** Even with path traversal guards, the viewer serves screenshots and audit data with no authentication.
   - **Why this matters:** This is direct disclosure of potentially proprietary CAD geometry and customer-related context.
   - **Fix:**
     1. Gate all viewer and screenshot endpoints behind the same local auth token.
     2. Add `Cache-Control: no-store` and other defensive headers.
     3. Consider explicit “demo mode” watermark/banner when insecure mode is enabled.
     4. Redact sensitive fields in default list views.

9. **[High] Error vocabulary drift is not a minor issue; it is a contract defect**
   - **What’s wrong:** I strongly agree with Codex here. SPEC-02 references error states that SPEC-01 does not define. Same for some `ToolErrorCode` values.
   - **Why this breaks:** You will either:
     - implement undocumented ad hoc strings, or
     - collapse semantically distinct failures into wrong codes.
   - **Fix:**
     1. SPEC-01 must own the complete protocol error enum set.
     2. SPEC-01 must also own complete `ToolErrorCode` values referenced by SPEC-02.
     3. Add build-breaking enum parity tests.
     4. Remove uppercase string examples that conflict with enum serialization policy.

10. **[Medium] The viewer contract is still owned in the wrong place**
    - **What’s wrong:** SPEC-06 defines required methods/exceptions for `IReadOnlyDhfRepository`, but SPEC-04 is supposed to own that interface.
    - **Why this matters:** This is interface control drift disguised as convenience.
    - **Fix:**
      1. Move the normative interface definition to SPEC-04.
      2. In SPEC-06, reference it instead of redefining it.
      3. Keep SPEC-06 limited to consumption expectations and endpoint behavior.

11. **[Medium] Advisory lock keying via `hashtext` is acceptable for hackathon, weak for production**
    - **What’s wrong:** `hashtext` collisions are unlikely but not impossible, and you are using locks for integrity-critical sequencing.
    - **Why this matters:** A collision here becomes a subtle concurrency bug.
    - **Fix:**
      1. Use a deterministic 64-bit lock key derivation from session GUID.
      2. Separate namespaces for session-open vs append locks.
      3. Add tests for lock-key determinism and namespacing.

12. **[Medium] Eventual consistency in viewer is fine, but the current UX can mislead operators**
    - **What’s wrong:** The page uses multiple repository calls and shallow validation separately. The UI may briefly display incompatible facts.
    - **Why this matters:** For an audit viewer, “hash chain valid” next to a stale entry count is tolerable; “hash chain valid” next to an entry list that includes a not-yet-validated append is more problematic.
    - **Fix:**
      1. Add `asOfTimestamp` to responses.
      2. Consider one snapshot method for initial page load.
      3. Label integrity as “validated through entry N” if needed.

13. **[Medium] Timeout/recovery ordering needs one more layer of hardening**
    - **What’s good:** You are unusually honest that timed-out COM calls may still mutate the model. Good.
    - **Residual concern:** The system needs explicit rules for persistence failure during recovery.
    - **Fix:**
      1. Add mandatory ordering:
         - timeout fires,
         - turn closes,
         - new turns blocked,
         - recovery attempted,
         - recovery event persisted,
         - only then unlock.
      2. If recovery event persistence fails, remain locked.
      3. Add a distinct fatal state for “model reconciled but audit persistence incomplete.”

14. **[Medium] Logging remains under-specified and privacy-risky**
    - **What’s wrong:** “Full stack traces to file” is not enough of a policy.
    - **Why this matters:** Logs are an unbounded side channel for sensitive transcripts, paths, and internal state.
    - **Fix:**
      1. Define structured logging requirements.
      2. Correlate by `AddinSessionId`, `SessionId`, `CallId`, `TurnId`.
      3. Redact transcript text and file paths from non-debug logs by default.
      4. Protect log files with ACLs and retention rules.

15. **[Medium] The specs still need a top-level hazard/failure matrix**
    - **What’s wrong:** Failure handling exists, but it is fragmented across modules.
    - **Why this matters:** For SWE-013/032/033-style rigor, you want a single hazard-to-safe-state mapping.
    - **Fix:** Add a SPEC-00 appendix or SDR listing system response to:
      - DB unavailable,
      - SW hang,
      - transport loss,
      - malformed message flood,
      - disk full,
      - screenshot artifact unreadable,
      - model API timeout,
      - viewer unavailable.

16. **[Medium] `HttpListener` and WinForms `SynchronizationContext` should be explicitly labeled as implementation constraints, not architecture truths**
    - **What’s good:** You did better than most specs by noting Day-0 validation.
    - **Concern:** These are brittle environment dependencies.
    - **Fix:**
      1. Mark them as v1 implementation decisions.
      2. Add fallback/contingency notes.
      3. Track startup probe failure as a first-class health signal.

17. **[Low] ACC numbering collision should be fixed now**
    - **Agree with Beth:** ACC-08 collision is sloppy and will break traceability.
   - **Fix:** Renumber and maintain one authoritative acceptance ID table in SPEC-00 only.

18. **[Low] Some wording around BSMS and hallucination resistance is overclaimed**
   - **Agree with prior feedback:** “There is no path” and similar absolute claims are legal/compliance bait.
   - **Fix:** Rephrase to bounded engineering claims:
     - reduces risk,
     - constrains evidence sources,
     - preserves row-level provenance,
     - fails with explicit absence more often than similarity-based retrieval.

19. **[Low] Manual SQL remediation in exception text is not great operational hygiene**
   - **Agree with Beth/previous concerns:** Don’t encourage copy-paste SQL from runtime errors.
   - **Fix:** Exception should say “follow runbook” and logs may include internal remediation references. Keep raw SQL in internal docs only.

20. **[Low] Absolute-path leakage in DHF and protocol should be treated as an IP exposure defect, not just a portability concern**
   - **Fix:** Convert to relative/logical identifiers now if possible; if not, mark as temporary and prohibit exposing raw paths in viewer/export.

## Overall judgment

I agree with the strongest prior concerns. The architecture is strong in decomposition and unusually honest about failure modes, especially around COM timeout/recovery. But it is **not yet interface-stable**, and that is the immediate liability.

### Must-fix before implementation
1. Resolve all cross-spec contract contradictions.
2. Fix same-workstation dual-session semantics.
3. Add local auth/secret protection for bridge and viewer.
4. Define DHF write failure safe-state.
5. Anchor session metadata cryptographically.

### Good enough to defer
- replacing `hashtext`,
- richer observability,
- artifact encryption,
- full production-grade admin tooling.

If you want, I can do the next pass as a **spec-by-spec redline checklist** with exact section edits.