# ORACLE Review Report

**Document:** D:\BoundState\specs\bsvoice_cad\SPEC-01_WIRE_PROTOCOL.md
**Session:** 20260418-124628
**Rounds:** 2
**Converged:** No

## Metrics

- Accepted critical defects: 2
- Accepted high defects: 9
- Total accepted defects: 24
- Total rejected claims: 0
- Preference-only findings: 3
- Duplicate rate: 0.0%
- Rounds to stability: 2
- Converged: False

## Round 1

### [HIGH] WebSocket framing contract conflates frames with messages (R1-ISSUE-001)
**Problem:** Section 3 states 'WebSocket frame boundaries define message boundaries' then immediately contradicts itself by noting that System.Net.WebSockets reassembles fragmented messages. These are mutually exclusive: if frames define boundaries, fragmentation cannot exist; if fragmentation is reassembled, frame boundaries are irrelevant to the application layer. Implementers following the first sentence will deserialize partial payloads.
**Fix:** State unambiguously that one JSON document maps to one WebSocket message (not frame), that receivers must accumulate frames until EndOfMessage=true before deserializing, and that the 4 MiB limit applies to the fully reassembled message.
**Sources:** Fran, Codex

### [CRITICAL] Oversize-message (>4 MiB) streaming/chunking protocol is undefined (R1-ISSUE-002)
**Problem:** The spec mandates chunking for payloads exceeding 4 MiB but defines no chunk message types, correlation fields, sequence numbers, completion markers, reassembly rules, integrity checks, or error handling. Feature trees and StateSnapshots can easily exceed this limit under realistic CAD models. This directly contradicts the spec's claim to define all cross-process wire types.
**Fix:** Either (a) define explicit chunked-transfer wire types with correlation, sequencing, and completion semantics in this spec, or (b) remove streaming from v1 scope, enforce hard rejection of messages exceeding 4 MiB with a defined error code, and address large payloads via pagination or compression in the result types themselves.
**Sources:** Fran, Codex, Grok

### [HIGH] Stretch tool wire types registered but field definitions deferred (R1-ISSUE-003)
**Problem:** Section 0 claims this spec defines every type that crosses a process boundary, but Section 7.2 explicitly defers field definitions for all stretch tools to future implementation while keeping their polymorphic registrations active. Consumers cannot generate schemas, validators, or serializers for undefined types, and any later field addition constitutes an unstated wire-contract change.
**Fix:** Either fully specify every registered stretch tool record now, or remove stretch tool registrations from the protocol until their fields are defined. The claim of completeness must match reality.
**Sources:** Fran, Codex

### [HIGH] Epoch stale-rejection rule blocks the AddinReadyMessage that establishes a new epoch (R1-ISSUE-004)
**Problem:** Section 9 states that any message with an AddinSessionId other than the current epoch is rejected with STALE_EPOCH, and that the epoch changes only when AddinReadyMessage arrives with a new AddinSessionId. These rules are mutually exclusive: the AddinReadyMessage carrying the new epoch would itself be rejected before the epoch can be updated. Reconnects and add-in restarts are logically impossible without an unstated special case.
**Fix:** Explicitly define AddinReadyMessage as the sole message type exempt from epoch validation, specify how the current epoch is initialized on first connection (e.g., no epoch exists yet), and define the atomic transition from old epoch to new epoch upon receipt of a valid AddinReadyMessage.
**Sources:** Fran

### [HIGH] StateSnapshot requires Units when no document is active (R1-ISSUE-005)
**Problem:** StateSnapshot.Units is required (non-nullable) but DocumentId, DocumentType, and other document-dependent fields are nullable, and IsNullDocumentState explicitly signals the no-document case. There is no defined unit value when no document exists, forcing implementations to either invent a fake value or violate the contract.
**Fix:** Make Units nullable (UnitSystem?) in StateSnapshot, or add a sentinel enum member (e.g., UnitSystem.Unknown) and specify it must be used when IsNullDocumentState is true.
**Sources:** Fran

### [HIGH] ToolResultMessage success/result/error invariants are comment-only and untested (R1-ISSUE-006)
**Problem:** The mutually exclusive relationship between Success, Result, and Error in ToolResultMessage is documented only in comments. The validation section covers IToolInput only. Invalid states (Success=true with Error populated, both null, etc.) are wire-valid and leave receiver behavior undefined.
**Fix:** Add explicit validators and build-breaking tests for ToolResultMessage enforcing: (Success && Result != null && Error == null) || (!Success && Result == null && Error != null). Extend Section 14 and Section 15 to cover message-envelope invariants, not just tool inputs.
**Sources:** Fran

### [HIGH] Tool name has two unsynchronized sources of truth (discriminator vs. ToolNameAttribute) (R1-ISSUE-007)
**Problem:** The wire discriminator value for IToolInput/IToolResult is the tool's snake_case name (from [JsonDerivedType] attributes on the polymorphic base), while the registry uses [ToolName] attributes on the concrete types. The spec defines no single authoritative source and no test that these two names match. A mismatch creates tools that serialize under one name but register/schema-generate under another.
**Fix:** Define one authoritative source for tool names (e.g., [ToolName] attribute, with [JsonDerivedType] value derived from it via source generator or test). Add a build-breaking test asserting that discriminator value, ToolNameAttribute value, timeout table key, and schema name are identical for every tool.
**Sources:** Codex

### [HIGH] StateSnapshot.ActiveSelection cannot represent transient sketch entity selections (R1-ISSUE-008)
**Problem:** SketchEntityId is defined as transient (valid only within an active sketch session), but StateSnapshot.ActiveSelection is typed as IReadOnlyList<EntityRef> (persistent references only). During an active sketch, selected entities are SketchEntityIds, making faithful snapshot transmission impossible without lossy conversion or hidden out-of-band state.
**Fix:** Change ActiveSelection to a discriminated union type (e.g., SelectionRef with 'kind': 'persistent'|'sketch') that can carry both EntityRef and SketchEntityId, and register/test that union.
**Sources:** Codex

### [HIGH] GetFeatureTreeInput.Compressed flag has no defined wire effect (R1-ISSUE-009)
**Problem:** GetFeatureTreeInput exposes a Compressed flag (defaulting to true), but GetFeatureTreeResult only contains IReadOnlyList<FeatureNode> Tree and string TreeHash — no compressed payload field, encoding indicator, or alternate response shape. Callers cannot know what behavior to expect; implementers will either ignore the flag or invent incompatible behavior.
**Fix:** Remove Compressed from v1 scope, or define an explicit compressed response contract specifying the encoding algorithm, content field, and how the receiver signals support.
**Sources:** Codex

### [HIGH] Closed-vocabulary message fields modeled as unconstrained strings (R1-ISSUE-010)
**Problem:** Several message fields have finite allowed token sets documented only in comments (e.g., CancelToolCallMessage.Reason: "user_abort"|"timeout"|"agent_abort"; RollbackUndoGroupMessage.Reason: "timeout"|"user_cancel"|"error"; ClientProtocolErrorMessage.ErrorCode: "VERSION_MISMATCH"|...). The actual contract type is string with no schema constraint or validator, making typos and casing drift undetectable.
**Fix:** Replace these fields with enums (consistent with the existing enum serialization policy) or add explicit message-envelope validators and schema constraints for each allowed token set, covered by build-breaking tests.
**Sources:** Codex

### [HIGH] Hash canonicalization does not define array ordering, making hashes non-deterministic (R1-ISSUE-011)
**Problem:** Section 13 specifies sorted object keys for canonicalization but says nothing about ordering of arrays (FeatureTree, Parameters, ActiveSelection, etc.). Logically equivalent trees emitted in different sibling or parameter orders will produce different hashes, undermining state-change detection and any downstream hash-chain logic.
**Fix:** Define canonical ordering for every hashed list (e.g., FeatureNodes ordered by FeatureId lexicographically, Parameters ordered by Name). Add property-based tests covering reordered-but-logically-equivalent inputs asserting hash equality.
**Sources:** Codex

### [MEDIUM] G29 decimal format can emit scientific notation, contradicting the no-scientific-notation rule (R1-ISSUE-012)
**Problem:** Section 13 specifies G29 format for decimal serialization in hash canonicalization and simultaneously states 'no scientific notation.' G29 is not fixed-precision and can emit exponent notation for very large or very small values, making these rules mutually contradictory. Hash outputs can diverge across implementations or edge cases.
**Fix:** Replace G29 with an explicit fixed-point format that never emits scientific notation (e.g., always emit full integer and fractional digits with invariant culture), and add tests for extreme-scale decimal values to verify no exponent notation appears.
**Sources:** Fran

### [MEDIUM] ToolRegistry does not define or enforce tool-name uniqueness (R1-ISSUE-013)
**Problem:** The spec defines input/result pairing completeness but never states that each tool name must map to exactly one input type and exactly one result type. Duplicate [ToolName] annotations can create ambiguous lookups, nondeterministic schema generation, or wrong type resolution at runtime.
**Fix:** Define one-to-one uniqueness for tool names as a protocol invariant and add a build-breaking test that fails if multiple input or result types share the same tool name.
**Sources:** Fran

### [HIGH] ScreenshotResult assumes shared filesystem, breaking remote deployment (R1-ISSUE-014)
**Problem:** The wire protocol is defined over WebSocket (which supports remote endpoints), but ScreenshotResult transmits only a FilePath and Sha256Hash, implicitly requiring a shared filesystem between add-in and orchestrator. This breaks if the orchestrator runs on a different machine, in a container, or as a cloud function, and imposes an unstated deployment constraint that contradicts the process-boundary premise.
**Fix:** Either (a) explicitly declare co-located deployment as a hard architectural invariant in Section 0 and document the shared-directory contract here rather than deferring to SPEC-02, or (b) transmit image bytes (base64 or binary WebSocket message) within the wire contract to support remote deployments.
**Sources:** Grok

### [MEDIUM] Spec claims 'pure data definitions' but embeds behavioral rules and enforcement logic (R1-ISSUE-015)
**Problem:** Section 0 states 'This spec has zero state transitions — it is pure data definitions,' but the document embeds epoch rejection rules (Section 9), handshake sequencing with WebSocket close behavior (Section 10), per-tool timeout values (Section 12), and validation policy splits (Section 14) — all of which dictate consumer state machine behavior. This creates overlap with SPEC-03/SPEC-04.
**Fix:** Either revise the scope claim to accurately reflect that behavioral rules are included, or extract all enforcement/rejection/timeout logic to SPEC-03 and restrict this spec to type shapes, JSON policy, and discriminator values only.
**Sources:** Grok

### [LOW] Reflection-based completeness tests create fragile CI dependency (R1-ISSUE-016)
**Problem:** Protocol correctness depends on runtime assembly scanning and attribute presence. Any rename, split, or addition of a tool outside the exact reflection contract breaks CI. This is a valid engineering concern but is a design preference rather than a correctness defect.
**Fix:** Consider replacing with a Roslyn source generator or explicit static registration list that produces derived-type attributes and registry from a single source of truth, reducing fragility.
**Sources:** Grok

### Coordinator Decisions
- **R1-ISSUE-001**: accepted_defect — Clear contradiction in Section 3. 'Frame boundaries define message boundaries' is incorrect WebSocket terminology and contradicts the immediately following sentence about reassembly. Implementers woul
- **R1-ISSUE-002**: accepted_defect — The spec promises chunking behavior but defines zero wire types, sequencing, or reassembly rules for it. This directly violates the spec's own completeness claim. For hackathon scope, the correct fix 
- **R1-ISSUE-003**: accepted_defect — Section 0 claims completeness over all cross-process types while Section 7.2 explicitly defers field definitions. The scope claim must be scoped to mandatory tools, and the placeholder nature of stret
- **R1-ISSUE-004**: accepted_defect — Logical impossibility: the AddinReadyMessage that establishes a new epoch would be rejected by the rule requiring epoch match. Must define an explicit exemption and initial-connection semantics.
- **R1-ISSUE-005**: accepted_defect — StateSnapshot.Units is required but meaningless when IsNullDocumentState is true. Making it nullable resolves the contradiction cleanly.
- **R1-ISSUE-006**: accepted_defect — The Success/Result/Error invariant is documented only in comments with no validation or test coverage. Invalid wire states are silently accepted. Adding message-envelope validators and tests is necess
- **R1-ISSUE-007**: accepted_defect — Two independent attribute-based naming systems ([JsonDerivedType] discriminator and [ToolName]) with no synchronization mechanism or test. A single authoritative source must be defined with a build-br
- **R1-ISSUE-008**: accepted_defect — ActiveSelection typed as IReadOnlyList<EntityRef> cannot represent transient SketchEntityId selections during active sketch sessions. A discriminated union SelectionRef is necessary for faithful snaps
- **R1-ISSUE-009**: accepted_defect — GetFeatureTreeInput.Compressed defaults to true but the result type has no compressed payload field, encoding indicator, or alternate shape. Remove from v1 rather than invent an undefined contract.
- **R1-ISSUE-010**: accepted_defect — Closed vocabularies documented in comments on string fields violate the spec's own enum serialization policy (Section 2.2). Replacing with proper enums provides compile-time safety, schema generation,
- **R1-ISSUE-011**: accepted_defect — Hash canonicalization specifies sorted object keys but no array ordering rule. Logically equivalent trees emitted in different sibling orders produce different hashes, breaking state-change detection.
- **R1-ISSUE-012**: accepted_defect — G29 format can emit scientific notation for extreme decimal values (e.g., 1E-28), directly contradicting the stated 'no scientific notation' rule. The format spec must define an expansion step.
- **R1-ISSUE-013**: accepted_defect — Tool name uniqueness is a basic invariant that pairing tests alone do not enforce. Duplicate [ToolName] attributes would cause ambiguous lookups and nondeterministic schema generation.
- **R1-ISSUE-014**: accepted_defect — ScreenshotResult's FilePath contract requires shared filesystem, but the spec does not declare co-location as an architectural constraint. For hackathon scope, declaring co-location explicitly in Sect
- **R1-ISSUE-015**: accepted_defect — The scope claim 'pure data definitions' is factually incorrect — the spec embeds epoch rejection rules, handshake sequencing, timeout values, and validation policy splits. The claim must be revised to
- **R1-ISSUE-016**: preference_only — Reflection-based completeness tests are a valid engineering pattern that catches exactly the registration omissions they're designed to catch. The suggestion to use source generators is a reasonable a

## Round 2

### [MEDIUM] WebSocket framing contract conflates frames with messages (R1-ISSUE-001)
**Problem:** Section 3 simultaneously states 'Receivers MUST accumulate frames until EndOfMessage = true before deserializing' and 'System.Net.WebSockets handles frame reassembly transparently; application code never sees partial frames.' These are contradictory. Additionally, the spec does not define what a receiver must do when the 4 MiB limit is crossed mid-stream before EndOfMessage — whether to drain-and-discard remaining frames or abort the connection immediately.
**Fix:** Remove the 'handles frame reassembly transparently' claim. Replace with an explicit receive-loop contract: partial segments must be accumulated until EndOfMessage. Add a required behavior for limit breach: either drain-and-discard remaining frames without buffering, or close/abort the WebSocket immediately.
**Sources:** Codex, Fran

### [CRITICAL] 4 MiB message cap conflicts with mandatory full StateSnapshot/FeatureTree payloads and no truncation contract exists (R1-ISSUE-002)
**Problem:** ToolResultMessage requires StateAfter (a full StateSnapshot), StateUpdateMessage requires a full StateSnapshot, and the post-handshake InitialState trigger mandates a full-state resync — all of which include a required IReadOnlyList<FeatureNode> FeatureTree. The spec provides no truncation flags, pagination fields, or depth-limit fields on StateSnapshot. The cited 'GetFeatureTreeInput with depth limits' mitigation does not exist in the contract. Large models make the mandatory initial snapshot impossible to send without violating either completeness or the 4 MiB limit.
**Fix:** Define an explicit large-state contract: add truncation/pagination metadata to StateSnapshot (e.g., IsFeatureTreeTruncated, a follow-up fetch API), or make FeatureTree optional/on-demand in mandatory envelopes with a separate GetFeatureTree call. Remove the nonexistent 'depth limits' example from Section 3 unless the fields are added.
**Sources:** Codex, Fran

### [HIGH] Stretch tool wire types registered as callable tools with no availability gate (R1-ISSUE-003)
**Problem:** Empty placeholder stretch tool records carry [ToolName] attributes and [JsonDerivedType] registrations, causing ToolRegistry.All to include them as callable tools. Consumers building tool catalogs from ToolRegistry or generated schemas will advertise or invoke tools with no defined fields or implementation, causing guaranteed runtime failures. No ToolMetadata.IsImplemented flag or equivalent exclusion mechanism exists.
**Fix:** Add an explicit IsImplemented (or ImplementationStatus) flag to ToolMetadata. Require schema generation and tool exposure (e.g., Anthropic tool catalog) to exclude tools where IsImplemented == false. Alternatively, remove placeholders from ToolRegistry entirely until their wire contracts are defined.
**Sources:** Codex, Fran, Grok

### [CRITICAL] Epoch stale-rejection rule and pre-epoch error responses have no valid AddinSessionId (R1-ISSUE-004)
**Problem:** Two related but distinct problems: (1) IClientMessage requires a non-nullable AddinSessionId, but the orchestrator must send ClientProtocolErrorMessage (a version-mismatch error) before any epoch is established — no valid AddinSessionId value exists at that point. (2) The spec mandates rejection of invalid ToolResultMessage envelopes with AddinProtocolErrorCode.MalformedEnvelope and stale-epoch add-in messages with AddinProtocolErrorCode.StaleEpoch, but the receiver of those messages is the orchestrator, which can only send ClientProtocolErrorMessage — whose enum has no MalformedEnvelope or StaleEpoch values.
**Fix:** For pre-epoch errors: define a pre-epoch exception — either make AddinSessionId nullable/optional on pre-handshake protocol errors, or specify that the orchestrator echoes the AddinReadyMessage.AddinSessionId even when the epoch is not accepted. For directional error codes: add MalformedEnvelope and StaleEpoch to ClientProtocolErrorCode (orchestrator-emittable), or define a single bidirectional protocol-error envelope with a unified code set.
**Sources:** Fran, Codex

### [HIGH] StateSnapshot null-document and live-document invariants are incomplete (R1-ISSUE-005)
**Problem:** Two gaps: (1) When IsNullDocumentState=true, the rules only constrain Units, DocumentId, FeatureTree, FeatureTreeHash, and IsSketchActive — but DocumentName, DocumentType, ActiveSketchId, and ActiveSelection are unconstrained, allowing stale metadata to leak. (2) When IsNullDocumentState=false, only Units is required to be non-null; DocumentId, DocumentName, DocumentType are still nullable with no stated requirement, and the ActiveSketchId/IsSketchActive relationship is undefined (e.g., IsSketchActive=false with non-null ActiveSketchId is permitted).
**Fix:** Extend null-document rules to cover all document-scoped fields (DocumentName=null, DocumentType=null, ActiveSketchId=null, ActiveSelection=empty). Add live-document invariants (DocumentId, DocumentName, DocumentType required when IsNullDocumentState=false). Add sketch-state invariant (ActiveSketchId required iff IsSketchActive=true, forbidden otherwise). Make all of these build-breaking validator/test requirements.
**Sources:** Fran, Fran, Codex

### [HIGH] Hash canonicalization contradicts itself on whether array ordering is producer responsibility or canonicalizer responsibility (R1-ISSUE-011)
**Problem:** Section 13 states 'Arrays are hashed in their serialized order. Producers MUST emit arrays in canonical order' — placing ordering responsibility on producers. The same section then requires property tests to 'assert that logical equality (including reordered-but-equivalent arrays) implies hash equality' — which is only possible if HashCanonicalizer sorts arrays internally. These two rules are mutually exclusive unless the canonicalizer sorts, which the first rule denies.
**Fix:** Choose one model: (A) HashCanonicalizer sorts all hashed arrays itself before hashing — remove the 'Producers MUST emit arrays in canonical order' requirement, keep the reorder-invariance property test; or (B) Producers must pre-sort, reordered arrays produce different hashes — remove the reorder-invariance claim from the property test. Option A is preferred as it eliminates distributed ordering logic.
**Sources:** Codex, Fran, Grok

### [HIGH] ScreenshotResult file-path design creates permanent architectural lock-in (R1-ISSUE-014)
**Problem:** ScreenshotResult transmits a file path rather than image bytes or a content-addressable reference, hard-coding a Windows co-location assumption into the wire contract itself. This cannot be relaxed without a breaking wire-contract change, contradicting the spec's claim that deployment constraints are out of scope rather than load-bearing on the protocol.
**Fix:** Acknowledge explicitly in the spec that this is a v1-only design decision with a known v2 migration path. Consider adding a content-addressable reference field (e.g., a URI scheme that works for both local paths and remote URLs) to ease future migration, or at minimum document the exact breaking change required for remote deployment.
**Sources:** Grok

### [HIGH] Version-mismatch error message may be undeserializable by the mismatched peer (R1-ISSUE-006)
**Problem:** When protocol versions diverge, the orchestrator sends ClientProtocolErrorMessage serialized under its own version. If the divergence includes changes to polymorphic registrations, record layouts, or enum values, the add-in may be unable to deserialize the error message — defeating the purpose of the handshake diagnostic.
**Fix:** Define a minimal, stable plain-JSON error format outside the polymorphic families that both sides can always parse regardless of version divergence (e.g., a fixed flat JSON object with type, errorCode, expectedVersion, receivedVersion), or send the error as a WebSocket text frame with a documented fixed schema before closing.
**Sources:** Grok

### [MEDIUM] G29 decimal format can emit scientific notation, contradicting the no-scientific-notation rule (R1-ISSUE-012)
**Problem:** Section 13 states HashCanonicalizer.FormatDecimal calls value.ToString("G29", CultureInfo.InvariantCulture) and 'post-processes any scientific-notation output into expanded decimal form.' The spec acknowledges G29 can produce scientific notation but relies on post-processing. This is a known fragility that the spec itself flags via HashDecimalFormatTests — the fix is in the implementation, not the spec text, but the spec should be clearer that G29 alone is insufficient.
**Fix:** This issue was partially addressed in R1 by adding the post-processing note and tests. Confirm the post-processing algorithm is specified (not just referenced) so implementers can reproduce it without reading source code. Consider specifying the exact expansion algorithm inline.

### [MEDIUM] AddinSessionId generation underspecified: GetMachineGuid behavior, collision risk, and format validation absent (R2-ISSUE-001)
**Problem:** The GenerateAddinSessionId implementation references GetMachineGuid() without defining its behavior, entropy source, or fallback. No collision analysis is provided for rapid restarts or VM snapshot restore scenarios. No format/length validator is specified in Validation/Validators for the 32-character lowercase hex format. The epoch mechanism becomes a single point of failure if two sessions produce the same ID.
**Fix:** Specify GetMachineGuid() behavior (e.g., reads HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid on Windows). Add a strict regex/length validator (32 lowercase hex chars) to Validation/Validators. Document collision probability and define recovery behavior (e.g., if a new AddinReadyMessage arrives with the same AddinSessionId as the current epoch, treat it as a reconnect).
**Sources:** Grok

### [LOW] Over-specified CI test suite creates fragile build dependency (R1-ISSUE-016)
**Problem:** Mandating 1000 FsCheck randomized cases as build-breaking creates non-deterministic CI failures dependent on FsCheck seed and machine performance. The combination of reflection-based completeness tests, schema sync tests, and architecture tests in every PR creates high-friction protocol changes.
**Fix:** Reduce property test iterations to 100 for CI (keep 1000 for nightly). This is a low-priority quality-of-life issue that does not affect correctness.
**Sources:** Grok

### Coordinator Decisions
- **R1-ISSUE-001**: accepted_defect — The contradiction persists in the R1 revision: Section 3 simultaneously tells receivers to accumulate frames and claims frame reassembly is transparent. Additionally, no behavior is defined for when t
- **R1-ISSUE-002**: accepted_defect — Section 3 still references nonexistent 'GetFeatureTreeInput with depth limits' as a mitigation. StateSnapshot.FeatureTree is required in every ToolResultMessage and StateUpdateMessage with no truncati
- **R1-ISSUE-003**: accepted_defect — ToolMetadata still lacks an IsImplemented flag. ToolRegistry.All exposes empty placeholder stretch tools to schema generators and tool catalogs, causing guaranteed runtime failures if invoked. Adding 
- **R1-ISSUE-004**: accepted_defect — Two gaps remain: (1) ClientProtocolErrorMessage requires AddinSessionId but the orchestrator must send version-mismatch errors before establishing an epoch — no valid value is defined. Fix: orchestrat
- **R1-ISSUE-005**: accepted_defect — R1 made Units nullable and added 5 null-document constraints, but left DocumentName, DocumentType, ActiveSketchId, and ActiveSelection unconstrained when IsNullDocumentState=true. Live-document invari
- **R1-ISSUE-011**: accepted_defect — The contradiction persists: 'Producers MUST emit arrays in canonical order' vs. property tests asserting 'reordered-but-equivalent arrays implies hash equality.' These are mutually exclusive. Resolvin
- **R1-ISSUE-014**: accepted_defect — The co-location constraint was declared in R1, but no v2 migration path is documented. Adding a brief note about the known breaking change required for remote deployment improves architectural transpa
- **R1-ISSUE-006**: accepted_defect — When protocol versions diverge, the ClientProtocolErrorMessage may use polymorphic schemas the mismatched peer cannot parse, defeating the diagnostic purpose. Defining a frozen, version-independent wi
- **R1-ISSUE-012**: preference_only — The spec already documents G29 + mandatory post-processing + HashDecimalFormatTests coverage. The request to inline the full expansion algorithm is implementation detail — the behavior is fully constr
- **R2-ISSUE-001**: accepted_defect — GetMachineGuid() is referenced without defining its behavior, source, or fallback. No format validator exists for the 32-character lowercase hex format. For a wire protocol spec, the format of a load-
- **R1-ISSUE-016**: preference_only — This was preference_only in R1 and remains so. The 1000-iteration FsCheck count is a QoL concern, not a correctness defect. The weak_signal flag from the referee confirms low confidence. No draft chan

## Open Questions

- Should ToolErrorCode's protocol-level members (InFlightDuplicate, UnknownCallId, VersionMismatch, etc.) be removed now that dedicated message-level error enums exist, or kept for backward compatibility and cases where tool results report protocol-adjacent failures?
- What pagination strategy should be defined for feature trees that approach the 4 MiB hard limit? Depth-limited tree + continuation token? Page-by-subtree?
