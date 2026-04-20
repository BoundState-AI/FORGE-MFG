# Fran — Round 1

1. **[Critical] Cross-spec contradictions will create production defects before code is written**
   - **What’s wrong:** SPEC-00/01/02/04/06 do not agree on several contract details:
     - `AddinSessionId` generation conflicts:
       - SPEC-01 §9 says deterministic from `(machineGuid, swProcessId, loadTimestamp)` with **no fallback** and throws if MachineGuid unreadable.
       - SPEC-02 §8.1 says includes a **Guid nonce** and has a **LOCALAPPDATA fallback machine id**.
     - `AddinSessionId` semantics conflict:
       - SPEC-00 glossary says “UUID generated fresh”
       - SPEC-01 says 32-char lowercase hex hash, not UUID.
     - Handshake message shape conflict:
       - SPEC-02 §6.3 says `AddinReadyMessage` includes `screenshotDir`.
       - SPEC-01 `AddinReadyMessage` has no such field.
     - Error code vocabularies conflict:
       - SPEC-02 uses `"HANDSHAKE_INCOMPLETE"`, `"INVALID_CALL_ID"`, `"EXPIRED_CALL_ID"`, `"TERMINATED_CALL_ID"`, `"INTERNAL_ERROR"`.
       - SPEC-01 enums do not define these values.
       - SPEC-02 also shows uppercase string codes like `"VERSION_MISMATCH"` while SPEC-01 serializes enums as PascalCase.
     - Viewer depends on types not clearly defined in SPEC-04 (`IReadOnlyDhfRepository`, `RepositoryUnavailableException`, `ChainValidationResult`) while SPEC-04 exposes different names/interfaces.
   - **Why this breaks at 3 AM:** Teams will implement to different specs and integration will fail in opaque ways: handshake deserialization failures, stale-epoch false positives, inability to parse protocol errors, viewer startup/runtime failures.
   - **Fix:**
     1. Establish a **single source of truth table** in SPEC-00 for:
        - `AddinSessionId` format and generation ownership
        - handshake message fields
        - protocol error enums
        - shared repository interfaces/exceptions
     2. Update all module specs to reference that canonical table verbatim.
     3. Add a build-breaking **CrossSpecContractSyncTests** project that validates:
        - all enum names match referenced values
        - message DTO fields match spec-required fields
        - shared interface names exist in the owning assemblies
     4. Freeze one `AddinSessionId` algorithm now; do not allow both deterministic and nonce-based variants.

2. **[Critical] Viewer is unauthenticated and localhost-only is not an adequate security control**
   - **What’s wrong:** SPEC-06 explicitly binds to `127.0.0.1:7879` with **no auth** and serves sensitive DHF content including transcripts, screenshots, feature trees, user identities, workstation IDs, and audit metadata.
   - **Why this breaks in production:** “Localhost only” does not protect against:
     - malicious local processes
     - browser-based localhost abuse / CSRF-like attacks from hostile web pages
     - low-privilege malware scraping sensitive medtech/aerospace design data
     - accidental exposure if port forwarding, proxies, or remote support tools are active
   - **Security impact:** OWASP A01 Broken Access Control / A02 Cryptographic Failures / A05 Security Misconfiguration. Also IP exposure risk for proprietary CAD and BoundState process data.
   - **Fix:**
     1. Require a **per-process random auth token** for viewer endpoints, passed via header or signed cookie, even on localhost.
     2. Add `Origin`/`Host` validation and reject cross-origin browser requests by default.
     3. Set explicit anti-clickjacking and anti-sniff headers.
     4. Serve screenshots only behind the same auth gate.
     5. Add a hard “demo mode only” banner if auth remains descoped, because current design is not production-safe.

3. **[Critical] WebSocket bridge on HttpListener with URL ACL is an avoidable local attack surface**
   - **What’s wrong:** SPEC-02 binds `ws://127.0.0.1:7878/bsvoice-cad` with a user-level URL ACL and no stated client authentication.
   - **Why this breaks:** Any local process under the same user context can connect and issue tool calls to SolidWorks unless higher layers reject them. That means arbitrary model mutation, screenshot exfiltration, and audit poisoning.
   - **Fix:**
     1. Add a **mutual session secret** established out-of-band at orchestrator startup and required in the first client message or subprotocol.
     2. Prefer **named pipes** over localhost WebSockets for same-machine IPC if browser compatibility is not required. This significantly reduces accidental exposure.
     3. If WebSocket stays, require:
        - loopback bind only
        - origin/subprotocol validation
        - startup-generated secret
        - connection audit logs with PID if obtainable
     4. Add a threat model note: localhost is not trusted.

4. **[High] Screenshot path handling exposes data and trusts filesystem state too much**
   - **What’s wrong:** Screenshots are written to shared disk and referenced by absolute path. Viewer serves them later. Integrity is checked by hash, but confidentiality and access lifetime are weakly controlled.
   - **Failure modes:**
     - stale screenshots left on disk beyond intended retention
     - other local processes reading images directly from `%LOCALAPPDATA%\BSVoiceCAD\screenshots`
     - path leakage in DHF exposing machine usernames/home paths
     - screenshot file replacement or deletion causing inconsistent audit views
   - **Fix:**
     1. Do not store raw absolute paths in DHF long-term; store a **logical artifact ID** plus relative path.
     2. Apply restrictive ACLs on screenshot directories and explicitly document them.
     3. Add max file size and image validation before ingestion/serving.
     4. Consider encrypting screenshots at rest if regulatory/demo sensitivity matters.
     5. Log and surface screenshot integrity failures, but also classify whether the file became unreadable due to cleanup vs tamper.

5. **[High] Session-closing behavior in SPEC-04 can silently close legitimate sessions on same workstation**
   - **What’s wrong:** `OpenOrResumeSessionCore` closes **any stale open sessions on the workstation** before opening a new one.
   - **Why this breaks:** Same workstation can plausibly run:
     - multiple orchestrator instances accidentally
     - multiple users via remoting
     - test/dev concurrent sessions
     - race conditions during restart
     This logic can close unrelated or still-recoverable sessions without operator intent.
   - **Fix:**
     1. Scope closure more narrowly: close only sessions for the same workstation **and same user identity**, and ideally same app instance class.
     2. Record a specific closure reason, not just implicit `ended_at`.
     3. Add an operator-visible warning when stale sessions are auto-closed.
     4. Introduce a lease/heartbeat field on sessions so “stale” is evidence-based, not inferred solely by workstation collision.

6. **[High] DHF chain does not anchor session metadata cryptographically**
   - **What’s wrong:** SPEC-04 admits session metadata is only trigger-protected, not chain-hashed.
   - **Why this matters:** User identity, workstation ID, software versions, and protocol version are provenance-critical. If those fields are altered by a privileged DB user or trigger bypass, the chain still validates.
   - **Compliance concern:** This weakens audit provenance and undermines claims aligned with SWE-013/032/033-style traceability and data integrity expectations.
   - **Fix:**
     1. Add `session_metadata_hash` computed at session creation.
     2. Include that hash in the first entry or in all entry hash inputs.
     3. Add repository/reporting that exposes metadata-hash verification status.
     4. Separate DB roles post-hackathon so app cannot drop protective triggers.

7. **[High] Protocol error handling is underspecified for malformed/unparseable JSON**
   - **What’s wrong:** You define envelope validation and unknown message handling, but not the behavior for:
     - invalid JSON syntax
     - missing discriminator property
     - duplicate keys
     - huge nested structures intended to exhaust memory/CPU
   - **Why this breaks:** Real-world malformed inputs won’t deserialize into typed records. “Send protocol error message” is impossible if the parser is already in an undefined state unless raw-edge handling is specified.
   - **Fix:**
     1. Define a raw receive pipeline:
        - size limit
        - UTF-8 validation
        - parse with duplicate-key detection policy
        - discriminator extraction from raw DOM
        - only then typed deserialize
     2. Specify behavior for parse failure: log sanitized summary, send generic malformed message if connection still usable, optionally close on repeated offenses.
     3. Add fuzz/property tests for malformed payloads and JSON bombs.

8. **[High] AddinSessionId and CallId validation is inconsistent and may cause replay/forgery issues**
   - **What’s wrong:**
     - `AddinSessionId` is top-level epoch authority, but no authenticity guarantee exists.
     - `CallId` duplicate replay is cached for 60 seconds only.
     - Completed results can be replayed on reconnect by resubmitting same CallId.
   - **Why this breaks:** A malicious local client can replay prior calls/results semantics, confuse lifecycle correlation, or intentionally poison DHF with expected-yet-malicious resubmits.
   - **Fix:**
     1. Bind the connection to an authenticated client identity/secret.
     2. Add a **connection/session nonce** into message validation.
     3. Document replay policy explicitly and log every replayed `Completed` result.
     4. Consider extending cache semantics to distinguish benign reconnect replay from suspicious replay volume.

9. **[High] Deep validation can produce misleading conclusions under retention cleanup**
   - **What’s wrong:** Missing screenshot files are warnings, not failures, because cleanup is expected.
   - **Why this breaks:** For an audit log, “we can no longer verify the screenshot artifact” is not nothing. In regulated or forensic contexts, that’s degraded evidence.
   - **Fix:**
     1. Split validation outcome into:
        - `Valid`
        - `Tampered`
        - `EvidenceUnavailable`
     2. Preserve explicit retention policy metadata in the DHF/session.
     3. Viewer should not show “valid” if evidence is missing; show “chain valid, artifacts partially unverifiable.”
     4. Consider immutable artifact storage for production.

10. **[High] No explicit redaction/data minimization strategy for transcripts, screenshots, and feature trees**
    - **What’s wrong:** Raw transcript is authoritative and preserved verbatim. Screenshots and feature trees may contain proprietary geometry, filenames, customer names, part numbers, or regulated product details.
    - **IP/compliance risk:** This is a major data exposure vector, especially if logs are demoed, exported, or copied. It also risks exposing proprietary BoundState operating assumptions in customer-facing artifacts.
    - **Fix:**
      1. Classify fields by sensitivity: public/demo/internal/customer-confidential/regulatory.
      2. Add viewer/export redaction mode.
      3. Ensure logs never dump full transcript/result payloads by default.
      4. Add explicit “do not include raw screenshots in public demo exports unless approved” rule.

11. **[Medium] Session and entry reads may leak inconsistent state to viewer**
    - **What’s wrong:** SPEC-06 accepts eventual consistency, but page rendering depends on multiple separate repository calls.
    - **Why this matters:** Operators may see:
      - header says 7 entries, list shows 8
      - shallow validation for one prefix while list includes a later entry
      - “hash chain valid” while an in-flight append is mid-commit in another call
    - **Fix:**
      1. Add a repository method that returns **session summary + entries + shallow validation** in one snapshot transaction for page load.
      2. Mark UI values as “live / may update” if using polling-based eventual consistency.
      3. Include `asOfTimestamp` in API responses.

12. **[Medium] Advisory-lock design uses `hashtext`, which is collision-prone**
    - **What’s wrong:** Postgres `hashtext` is not collision-resistant enough for lock namespace design.
    - **Why this breaks:** Rare but real lock aliasing could serialize unrelated sessions or, worse, fail to isolate intended ones if lock keys collide in ways you didn’t expect.
    - **Fix:**
      1. Use the two-int advisory lock form with a stable namespace constant + parsed GUID-derived int pair.
      2. Or use a 64-bit hash with documented collision analysis.
      3. Add tests ensuring lock key derivation is deterministic and namespaced by entity type.

13. **[Medium] Tool result semantics after timeout remain operationally dangerous**
    - **What’s wrong:** You correctly admit timed-out COM calls may still mutate the model. Recovery is mandatory. Good. But the spec still leaves dangerous ambiguity:
      - what if recovery event append fails?
      - what if state refresh succeeds but undo rollback also partially applies?
      - what if user manually edits model before reconciliation on reconnect?
    - **Why this breaks:** Data integrity and operator trust collapse when the system says “timed out” but geometry changed.
    - **Fix:**
      1. Define a strict operational order:
         - detect timeout
         - hard-lock new turns
         - attempt recovery
         - append recovery event
         - if append fails, remain locked and surface fatal audit persistence error
      2. Record a model-state fingerprint before and after recovery.
      3. Require manual acknowledgment for unlock after ambiguous recovery.
      4. Add explicit acceptance tests for “timeout + late completion + append failure”.

14. **[Medium] `HttpListener` and WinForms SynchronizationContext are brittle platform choices**
    - **What’s wrong:** Both are expedient, but brittle under environment variation, corporate endpoint controls, and future supportability.
    - **Why this breaks:** You are coupling critical behavior to implementation details that may vary across SolidWorks versions and Windows configurations.
    - **Fix:**
      1. Mark both as **hackathon implementation choices**, not architectural truths.
      2. Add contingency design notes:
         - alternative IPC transport
         - alternative UI-thread marshalling mechanism if `SynchronizationContext` assumptions fail
      3. Monitor startup probe failures as a first-class operational metric.

15. **[Medium] Logging guidance is insufficiently privacy-safe and operationally structured**
    - **What’s wrong:** Several sections say “full stack traces to FileLogger/Serilog,” but there is no log schema/redaction policy.
    - **Why this breaks:** Stack traces and exception messages can contain:
      - file paths
      - usernames
      - transcript fragments
      - customer design names
      - API payload details
    - **Fix:**
      1. Define structured log fields and prohibited fields.
      2. Redact transcript/screenshot paths by default in info/warn logs.
      3. Correlate all logs with `AddinSessionId`, `SessionId`, `CallId`, `UndoGroupId`.
      4. Rotate and ACL-protect log files.
      5. Add “safe diagnostic bundle” vs “full debug mode” separation.

16. **[Medium] No explicit resource exhaustion controls for feature trees, screenshots, and validation**
    - **What’s wrong:** You have a 4 MiB message limit, but not end-to-end controls on:
      - DB JSONB size
      - screenshot dimensions/file size
      - count/depth of feature tree nodes
      - deep validation scan cost over many sessions
    - **Why this breaks:** Large models can cause memory spikes, slow page loads, DB bloat, or denial-of-service behavior on the demo laptop.
    - **Fix:**
      1. Add hard limits:
         - max feature node count/depth
         - max screenshot resolution and bytes
         - max deep-validation entries per request or time budget
      2. Return explicit truncation metadata.
      3. Add metrics on dropped/truncated artifacts.

17. **[Medium] NASA-style software assurance expectations are only partially met**
    - **What’s missing relative to your stated review lens:**
      - Clear bidirectional requirements traceability from top-level claims to tests is incomplete across specs.
      - Exception handling is described, but deterministic safe-state behavior is not always complete.
      - Some interfaces depend on prose-only invariants rather than machine-checkable contracts.
    - **Why it matters:** For SWE-013/032/033-style rigor, you want unambiguous requirements, verification linkage, and closure of failure handling.
    - **Fix:**
      1. Give every cross-spec invariant a unique requirement ID.
      2. Map each to one or more tests.
      3. Add a hazard/failure-mode appendix for:
         - transport loss
         - DB unavailable
         - SolidWorks hang
         - Anthropic unavailable
         - disk full
      4. Define safe-state behavior per hazard.

18. **[Medium] DB unavailability handling is not specified for DHF writes**
    - **What’s wrong:** Viewer has `RepositoryUnavailableException`; DHF write path does not clearly define behavior when Postgres is down, slow, or disk-full.
    - **Why this breaks:** If you cannot append the audit trail, can the system continue modeling? Today that answer is ambiguous, which is dangerous.
    - **Fix:**
      1. State explicitly: if `AppendTurnAsync` fails durably, either:
         - block further turns and enter audit-unsafe locked state, or
         - continue in degraded mode with giant warnings and no compliance framing.
      2. Add bounded retry/backoff and operator-visible health state.
      3. Surface DB health in the viewer and orchestrator console.

19. **[Low] Version mismatch handling relies on frozen raw JSON, but unknown future parser behavior is still risky**
    - **What’s good:** Stable flat mismatch payload is the right instinct.
    - **Residual issue:** If future sides change fundamental casing or envelope assumptions, raw extraction still may fail unless very conservative.
    - **Fix:** Define the version-mismatch parser as case-sensitive or case-insensitive explicitly, and test across intentionally divergent payload variants.

20. **[Low] Hardcoded ports 7878/7879 invite conflict and operator confusion**
    - **What’s wrong:** Port collisions on developer/demo machines are common.
    - **Fix:**
      1. Make ports configurable everywhere.
      2. On bind failure, log the owning process if detectable.
      3. Consider ephemeral-port negotiation for local dev, fixed ports only for demos.

21. **[Low] Spec language occasionally overclaims fail-closed/hallucination resistance**
    - **What’s wrong:** SDR-TL-009 says BSMS is “structurally hallucination-resistant” and “there is no path” for invention. That is marketing language, not engineering truth.
    - **Why this matters:** In regulated/aerospace-adjacent settings, overclaiming is liability.
    - **Fix:** Rewrite as bounded claim:
      - “reduces risk by constraining reasoning to typed rows and explicit absence states”
      - not “cannot hallucinate.”
    - Also ensure no customer artifact implies NASA endorsement from “validated at NASA MSFC.”

22. **[Low] Manual SQL remediation in exception message is operationally risky**
    - **What’s wrong:** Cross-workstation exception recommends manual SQL for hackathon scope.
    - **Why this breaks:** Operators under stress paste SQL into prod-like systems and make things worse.
    - **Fix:** Prefer “contact operator/admin runbook”; if SQL must exist, place it in internal runbook only, not exception text surfaced broadly.

23. **[Low] Absolute file paths in protocol and DHF reduce portability and leak environment details**
    - **Fix:** Move toward content-addressable artifact IDs and relative paths now, even if storage is local underneath.

24. **[Low] Missing explicit backup/restore and migration rollback plan**
    - **What’s wrong:** DbUp migration sequence exists, but no rollback/backup guidance.
    - **Why this breaks:** Migration failure during demo/prod leaves ledger unavailable.
    - **Fix:**
      1. Add pre-migration backup requirement.
      2. Make migrations idempotent and smoke-tested on downgrade scenarios where feasible.
      3. Define startup behavior if migration fails: system should not silently run partially initialized.

## Summary judgment

This is architecturally stronger than most hackathon specs. The seams are thoughtful, and the timeout/recovery honesty is better than average. But right now it has **multiple critical contract inconsistencies** plus **serious localhost trust assumptions** that would be unacceptable in production.

## Immediate must-fix before implementation
1. Reconcile all cross-spec contradictions, especially `AddinSessionId`, handshake fields, and error enums.
2. Add authentication/authorization to both viewer and add-in bridge, even if minimal local secret-based.
3. Define DB-write failure safe-state.
4. Cryptographically anchor session metadata.
5. Add raw-edge malformed JSON handling and fuzz tests.

If you want, I can turn this into a **redline change list by spec section** next.