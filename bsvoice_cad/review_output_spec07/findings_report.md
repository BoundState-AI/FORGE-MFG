# ORACLE Review Report

**Document:** D:\BoundState\specs\bsvoice_cad\SPEC-07_VOCABULARY_LAYER.md
**Session:** 20260419-115520
**Rounds:** 2
**Converged:** No

## Metrics

- Accepted critical defects: 3
- Accepted high defects: 19
- Total accepted defects: 32
- Total rejected claims: 0
- Preference-only findings: 0
- Duplicate rate: 0.0%
- Rounds to stability: 2
- Converged: False

## Round 1

### [CRITICAL] Resolution cardinality contradiction: multi-domain pipeline vs. single-term output contract (R1-ISSUE-001)
**Problem:** The spec simultaneously describes a pipeline that generates multiple domain queries for a single utterance (§3.1 shows 'resolved_terms: [ResolvedTerm, ...]', §5.1 example hits threads+materials+manufacturing) and a synthesis layer that selects 'the ONE row' with a singular ResolvedTerm output contract (§6.2, §6.4). These are irreconcilable without a design decision.
**Fix:** Make an explicit architectural decision: (a) one resolved term per utterance — restrict planner to primary domain, remove plural fields from §3.1 diagram, or (b) multiple resolved terms per utterance — rewrite §6.2 synthesis prompt, define a multi-term result schema with per-term confidence/citation/ambiguity, update §6.4 DTOs, §8.2 decoration payload, and all affected tests.
**Sources:** Fran, Codex

### [CRITICAL] Audit citation persistence is mandatory by policy but implemented as fire-and-forget with no durability contract (R1-ISSUE-002)
**Problem:** SDR-07-006 states 'Every resolution records its citation in a decoration payload' and ACC-21/ACC-22 require DHF citation recording as acceptance criteria. §3.2 simultaneously designates decoration writes as 'Fire-and-forget; not in critical path' with no retry, idempotency, ordering, or failure-state definition. A tool call can execute and succeed while its citation is silently lost.
**Fix:** Either (a) await the decoration write before reporting resolution success to SPEC-05, making it part of the success path, or (b) implement a durable outbox/queue with idempotency keys, retry policy, and an explicit failure state that prevents the pipeline from claiming a resolution was recorded when it was not. Add a failure mode row to §3.3 for decoration-write failure.
**Sources:** Fran, Codex

### [HIGH] Raw utterance is absent from planner and synthesis input contracts, breaking alias fallback and exact-match rules (R1-ISSUE-003)
**Problem:** Two separate pipeline stages require the raw utterance text but neither receives it. (1) §5.2 thread alias fallback uses 'WHERE alias_text = @utterance' but the planner only receives a BouncerResult (§5.1), which has no utterance field. (2) §6.2 synthesis prompt says it receives 'a user utterance' and rule 1 checks 'exactly matches the utterance's subject/parameters', but §3.1 Stage 4 input is only 'ResultSets + BouncerDecision.intent'.
**Fix:** Add the raw (and normalized) utterance string to the planner input contract and to the Stage 4 synthesizer request schema. Update BouncerResult or define a separate PipelineContext object that carries the utterance through all stages.
**Sources:** Fran, Fran

### [HIGH] IVocabRepository cannot enforce the declared repeatable-read transaction scope across multi-query utterances (R1-ISSUE-004)
**Problem:** §3.1 Stage 3 policy states 'all queries within single read transaction; repeatable read isolation for multi-query consistency', but IVocabRepository (§9.1) exposes only independent per-method async calls with no transaction, session, or batch API. There is no mechanism for the pipeline to execute multiple planner queries within one repeatable-read snapshot without leaking transaction management outside the repository or changing the interface.
**Fix:** Add a repository batch/read-session contract, e.g. an ExecuteQueriesAsync(IReadOnlyList<VocabQuery>, CancellationToken) method or an explicit IVocabReadSession that owns the transaction scope and exposes the per-domain query methods within it.
**Sources:** Codex

### [HIGH] Manufacturing domain is in scope and bouncer closed-set but has no grammar, schema, or repository contract (R1-ISSUE-005)
**Problem:** The bouncer persona prompt (§4.2) and VocabDomains.All closed set include 'manufacturing', 'welding', 'drawing_abbrev', 'units', and 'primitives'. §5.1 example explicitly routes 'tap a 6-32 UNC hole in titanium' to the manufacturing domain. None of these domains have grammar rules (§5.2–5.7 cover only threads/materials/fasteners/gdnt/sw_api/med_anatomy), repository methods (§9.1 has no ManufacturingRow or analogous types), or defined query paths. Accepted bouncer outputs for these domains have no implementable execution path.
**Fix:** Either (a) remove unsupported domains from v1 VocabDomains.All, the bouncer prompt, and all examples, or (b) add schema, grammar rules, repository methods, synthesis behavior, and required tests for each domain before shipping. At minimum, manufacturing must be addressed given its use in the mandatory demo example.
**Sources:** Codex, Fran

### [HIGH] Material grammar lookup paths (UNS, ASTM, AMS) are not exposed by IVocabRepository (R1-ISSUE-006)
**Problem:** §5.3 material grammar specifies three lookup paths: UNS number against vocab.materials.uns_number, ASTM/AMS against vocab.canonical_terms.standard_source. IVocabRepository (§9.1) only exposes FindMaterialsByGradeNameAsync and GetMaterialAsync. The UNS and standard-source paths cannot be implemented through the repository interface without violating SDR-07-004 (no raw SQL in pipeline code).
**Fix:** Add FindMaterialsByUnsNumberAsync(string unsNumber) and FindMaterialsByStandardSourceAsync(string standardSource, string domain) to IVocabRepository, or define a typed generic material lookup API covering all selectors in §5.3.
**Sources:** Codex

### [HIGH] Mandatory demo 2 requires material grammar/search path not included in mandatory scope (R1-ISSUE-007)
**Problem:** §10.1 mandatory scope lists 'Query planner with thread grammar rules (§5.2) and fastener grammar rules (§5.4)' but not material grammar. Yet mandatory demo 2 requires resolving 'specify the plate in titanium, medical grade' to ASTM_F136:Ti_6Al_4V_ELI — which requires a material grammar/search path to convert free text to a canonical ID. GetMaterialAsync only fetches after the ID is already known.
**Fix:** Move material grammar rules (§5.3) and at minimum FindMaterialsByGradeNameAsync into mandatory scope, or demote demo 2 to stretch scope.
**Sources:** Fran

### [HIGH] Build-breaking tests depend on live LLM latency and nondeterministic model outputs (R1-ISSUE-008)
**Problem:** §11.1 BouncerLatencyTests 'fails the build if exceeded on CI' at p95 < 600ms; §11.5 PipelineLatencyTests enforces p95 < 1.2s. §11.1 BouncerAcceptRejectTests and §11.4 synthesis tests require seeded utterances to match expected LLM classifications/outputs. Neither latency nor semantic correctness of live LLM calls is controllable by the codebase, making these tests a source of environmental false failures.
**Fix:** Run CI build-breaking tests against mocked/recorded model responses and deterministic timing contracts. Move live-model latency and semantic correctness checks to a separate scheduled canary suite or non-blocking release gate that does not block merges.
**Sources:** Fran, Codex, Codex

### [HIGH] Security-event logging stores full utterance text without redaction, retention, or access controls (R1-ISSUE-009)
**Problem:** §4.5 logs 'the full utterance text' at LogLevel.Warning for security events. The system handles medical vocabulary (§5.7, §4.2 domain list includes 'medical') and engineering IP. Rejected transcripts may contain PHI, proprietary design details, or attacker-crafted payloads. No redaction, retention policy, access control, or restricted sink is specified.
**Fix:** Default to redacted or hashed utterance text in standard warning logs. If full text is needed for security analysis, route it to a restricted audit sink with explicit access controls, retention limits, and tenant-boundary enforcement. Document the policy in §4.5.
**Sources:** Fran, Codex

### [HIGH] Thread class defaults unconditionally to '2B' (internal) regardless of utterance intent (R1-ISSUE-010)
**Problem:** §5.2 grammar rule for '<number>-<number> <series> <class>?' specifies 'default class = 2B' when class is omitted. 2B is an internal thread class (nut/tapped hole). An utterance about an external thread (bolt/screw) with omitted class would be silently resolved to the wrong canonical ID (e.g., 2A should be the external default). This produces wrong tool calls without triggering ambiguity escalation.
**Fix:** Only default to 2B when parsed_intent.action or context explicitly indicates an internal thread (tap, tapped hole, nut). For external thread context, default to 2A. When context is ambiguous, omit the class and return an AmbiguityEscalation with both 2A and 2B candidates rather than silently defaulting.
**Sources:** Fran

### [HIGH] Persisted decoration payload lacks a stable versioned JSON schema for polymorphic VocabularyResolution (R1-ISSUE-011)
**Problem:** §8.2 specifies that VocabularyResolutionDecoration (containing 'public required VocabularyResolution Resolution') is serialized to the payload JSON column of dhf.entry_decorations. VocabularyResolution is an abstract record with multiple concrete subtypes (§6.4). The spec does not define a discriminator field, stable field set, or schema version. CLR polymorphic serialization defaults are fragile across serializer settings changes, subtype additions, or renames, potentially making stored rows unreadable.
**Fix:** Define a concrete versioned JSON schema for each VocabularyResolution variant with an explicit discriminator property (e.g., 'kind': 'resolved'|'ambiguous'|'empty'|'failure'|'rejected') and a schema_version field. Document this schema in §8.2 and ensure SPEC-06 rendering is bound to the schema, not CLR type names.
**Sources:** Codex

### [MEDIUM] Confidence threshold configuration is underspecified — two config values, no binding algorithm (R1-ISSUE-012)
**Problem:** §6.3 defines a four-band threshold table (≥0.85 accept, 0.70–0.85 accept+log, 0.50–0.70 ambiguity, <0.50 ambiguity). §13 exposes two config values: confidenceFloorForResolution: 0.70 and ambiguityThreshold: 0.85. The spec never defines the algorithm that maps these config values to the four-band behavior, leaving multiple incompatible implementations possible.
**Fix:** Add a pseudocode or decision-tree definition in §6.3 that explicitly maps confidenceFloorForResolution and ambiguityThreshold to each branch of the threshold logic. Ensure the two config values cover all four bands without gaps or overlaps.
**Sources:** Fran

### [MEDIUM] Process-lifetime repository cache has no invalidation path for corpus hot-fix migrations (R1-ISSUE-013)
**Problem:** §7 specifies the repository cache has 'TTL is the process lifetime' for canonical_terms rows. §9.2 acknowledges that hot-fix migrations can run mid-session. After a hot-fix, the in-process cache will continue serving stale rows until the process restarts, causing returned parameters and citations to diverge from the database.
**Fix:** Add a cache invalidation mechanism keyed to corpus/migration version (e.g., a version counter in the database that the cache checks periodically), a LISTEN/NOTIFY-based invalidation, or replace process-lifetime TTL with a bounded TTL (e.g., 15 minutes) shorter than expected hot-fix intervals.
**Sources:** Codex

### [MEDIUM] Framing claims 'deterministic pipeline' while two LLM calls are structurally central (R1-ISSUE-014)
**Problem:** §0 states 'Every term the agent resolves passes through a deterministic pipeline' and positions the layer as the antidote to probabilistic NLP approaches. The actual pipeline has two Sonnet 4.6 LLM calls (bouncer and synthesis) as structurally required stages. The determinism claim is misleading, though the design rationale (LLM as translator, database as ground truth) is sound and worth preserving.
**Fix:** Revise §0 framing to accurately describe the architecture: the query execution and canonicalization are deterministic; the classification and disambiguation use LLM judgment constrained by structured database results. The safety argument rests on the database being ground truth, not on the pipeline being fully deterministic — make that distinction explicit.
**Sources:** Grok

### [MEDIUM] No transcription normalization stage for voice-mangled technical nomenclature (R1-ISSUE-015)
**Problem:** Grammar rules in §5.2–5.7 assume clean parsed_intent containing exact strings like '6-32 UNC', 'Ti-6Al-4V', 'M6x1.0'. No normalization or phonetic-rewriting stage is specified for common voice manglings ('six thirty two', 'em six by one point zero', 'tee eye six aluminum four vanadium'). This is a realistic failure mode for voice-to-CAD that the spec does not address.
**Fix:** Add a transcription normalization step (either within the bouncer or as a pre-bouncer stage) with a phonetic-to-canonical mapping table for common voice renderings of engineering terms. At minimum, document known failure cases and their handling. This need not be exhaustive for v1 but should be acknowledged as a known gap.
**Sources:** Grok

### [MEDIUM] Prompt injection via voice channel has a larger attack surface than §4.5 acknowledges (R1-ISSUE-016)
**Problem:** §4.5 only logs security events when the bouncer explicitly rejects with 'injection_pattern' or 'role_override_attempt' in the reason. Successful injections (where the bouncer accepts a crafted utterance) produce no security telemetry. The bouncer fast-path cache could cache a jailbroken acceptance. Voice transcription introduces phonetic ambiguity that may allow injection patterns to survive Whisper without triggering keyword detection.
**Fix:** Add post-transcription substring scanning for known injection patterns before the bouncer cache lookup (so injections are never cached as accepts). Consider a secondary validation pass on bouncer accepts for high-risk patterns. Document the threat model explicitly in §4.5.
**Sources:** Grok

### Coordinator Decisions
- **R1-ISSUE-001**: accepted_defect — Genuine architectural contradiction. §3.1 shows plural resolved_terms, §6.2 says 'select the ONE row', §6.4 defines singular subtypes. Multi-domain utterances are in scope (§5.1 example, §11.2 tests).
- **R1-ISSUE-002**: accepted_defect — Fire-and-forget contradicts SDR-07-006 ('Every resolution records') and ACC-21/22. Fix: decoration write is awaited with a latency budget and explicit failure mode. Not fire-and-forget.
- **R1-ISSUE-003**: accepted_defect — BouncerResult has no utterance field, but planner alias fallback and synthesis prompt both need it. Fix: add Utterance field to BouncerResult; update stage input descriptions.
- **R1-ISSUE-004**: accepted_defect — IVocabRepository has no transaction scope for multi-query execution. Fix: add ExecuteQueriesAsync batch method that wraps queries in a single repeatable-read transaction.
- **R1-ISSUE-005**: accepted_defect — Manufacturing, welding, drawing_abbrev, units, primitives have no grammar, schema, or repository contract. Fix: restrict v1 VocabDomains.All to implemented domains (threads, materials, fasteners, gdnt
- **R1-ISSUE-006**: accepted_defect — Material grammar specifies UNS and standard-source lookups not exposed in IVocabRepository. Fix: add FindMaterialsByUnsNumberAsync and FindByStandardSourceAsync methods.
- **R1-ISSUE-007**: accepted_defect — Demo 2 requires material grammar but §10.1 only lists thread and fastener grammars as mandatory. Fix: add material grammar to mandatory scope.
- **R1-ISSUE-008**: accepted_defect — Build-breaking tests against live LLM are inherently flaky. Fix: CI tests use recorded/mocked model responses. Live-model semantic and latency validation moves to a non-blocking canary suite.
- **R1-ISSUE-009**: accepted_defect — Full utterance text in warning logs creates PHI/IP exposure risk given medical domain scope. Fix: standard logs use redacted/hashed text; full text routed to restricted audit sink with access controls
- **R1-ISSUE-010**: accepted_defect — Unconditional 2B default is wrong for external threads. Fix: context-dependent defaulting (2B for tap/hole/nut, 2A for bolt/screw) with ambiguity escalation when context is unclear.
- **R1-ISSUE-011**: accepted_defect — Polymorphic VocabularyResolution serialized to JSON without discriminator or schema version creates fragile persistence. Fix: add explicit 'kind' discriminator and 'schema_version' to decoration paylo
- **R1-ISSUE-012**: accepted_defect — Two config values with no binding algorithm to the four-band threshold table. Fix: add explicit pseudocode decision tree in §6.3.
- **R1-ISSUE-013**: accepted_defect — Process-lifetime cache stales on hot-fix migrations. Fix: replace process-lifetime TTL with 15-minute bounded TTL.
- **R1-ISSUE-014**: accepted_defect — Calling the pipeline 'deterministic' when it has two LLM calls is misleading. The safety argument is that the database is ground truth, not that the pipeline is fully deterministic. Fix: revise §0 fra
- **R1-ISSUE-015**: accepted_defect — Grammar rules assume clean transcription; no handling of voice manglings like 'six thirty two'. Fix: add transcription normalization responsibility to the bouncer prompt (it already has LLM judgment) 
- **R1-ISSUE-016**: accepted_defect — Cached accepts, no secondary validation, phonetic ambiguity creates injection surface beyond what §4.5 acknowledges. Fix: add pre-cache injection pattern scan, document threat model.

## Round 2

### [CRITICAL] Batch repository contract exposes only CanonicalTermRow, dropping domain-specific fields required for synthesis and tool-call population (R2-ISSUE-001)
**Problem:** ExecuteQueriesAsync returns IReadOnlyDictionary<VocabQuery, VocabQueryResult> where VocabQueryResult only carries IReadOnlyList<CanonicalTermRow>. The pipeline's §0 example requires thread-specific fields (major diameter, pitch, tap drill) and demo moment 1 requires HoleWizard population from resolution. Stage 4 synthesis and downstream tool-call construction have no declared path to domain-extension fields (ThreadRow, MaterialRow, etc.) from the batch API.
**Fix:** Change VocabQueryResult to carry a typed per-domain row union or discriminated payload (e.g., a base CanonicalTermRow plus an optional domain-extension object keyed by domain type). Update Stage 3/4 contracts to consume domain-specific fields without undeclared extra round-trips.
**Sources:** Fran, Codex

### [HIGH] ParsedIntent.Parameters is typed as Dictionary<string, string> but the bouncer prompt requires a boolean normalization_uncertain flag (R2-ISSUE-002)
**Problem:** §4.2 persona prompt instructs the model to add `"normalization_uncertain": true` inside the parameters object. §4.4 declares `public required IReadOnlyDictionary<string, string> Parameters { get; init; }`. A boolean value cannot be deserialized into a string-valued dictionary without lossy coercion or a contract violation. The planner cannot reliably detect uncertain normalization.
**Fix:** Either widen Parameters to IReadOnlyDictionary<string, object> (or JsonElement) with a documented schema, or add a dedicated typed field `NormalizationUncertain: bool` outside the parameter map on ParsedIntent.
**Sources:** Fran, Codex

### [HIGH] Security-event classification depends on substring matching against LLM-generated free-text reason field — not guaranteed by contract (R2-ISSUE-003)
**Problem:** §4.5 routes security events to the restricted audit sink based on whether the model-generated `reason` string contains the tokens `injection_pattern` or `role_override_attempt`. The bouncer JSON schema (§4.2) only specifies `"reason": "one-line explanation"` — no constraint on wording. Model phrasing changes silently break security routing, ACC-23, and telemetry alerts without any detection mechanism.
**Fix:** Add a machine-readable `rejection_code` field (closed enum: `out_of_scope`, `injection_pattern`, `role_override_attempt`, `no_engineering_content`, etc.) and a boolean `security_event` field to the bouncer JSON schema. Use these structured fields for logging, audit routing, and ACC-23 checks instead of substring matching.
**Sources:** Fran, Codex

### [HIGH] Bouncer cache replays stale raw Utterance from prior request when two inputs normalize to the same cache key (R2-ISSUE-004)
**Problem:** The cache key is a hash of the normalized utterance (lowercased, filler-stripped). BouncerResult.Utterance stores the raw transcribed text. Two different raw utterances that normalize identically (e.g., 'um tap a 6-32 UNC hole' and 'tap a 6-32 UNC hole') share a cache key. On a cache hit, the cached BouncerResult — including its original raw Utterance — is returned for the new request. This corrupts audit decorations and can leak prior user text into DHF records.
**Fix:** Do not include the raw Utterance in the cached payload. Cache only decision/domain/parsed-intent data and inject the current request's raw utterance and latency into the reconstructed BouncerResult at cache-hit time. Alternatively, key the cache on the exact raw utterance (not normalized form).
**Sources:** Codex

### [HIGH] Persisted StageLatencies payload includes DecorationWriteMs and TotalMs that are unknowable before the write completes — circular dependency (R2-ISSUE-005)
**Problem:** §8.2 defines StageLatencies with DecorationWriteMs and TotalMs as required fields inside VocabularyResolutionDecoration. §3.2 states these are 'recorded as decoration metadata.' The decoration write duration and final total are not known until after the INSERT completes, making a single-pass append-only write impossible. Either a second UPDATE pass is required (violating DHF immutability) or the stored values will be zero/estimated.
**Fix:** Remove DecorationWriteMs and TotalMs from the persisted decoration payload. Record them in a separate telemetry/metrics sink (Serilog structured log or OpenTelemetry span) that does not require embedding them in the immutable DHF row. Or explicitly specify a two-phase write with ownership and consistency rules.
**Sources:** Fran

### [HIGH] Shared CancellationToken across parallel per-domain synthesis tasks contradicts per-domain timeout isolation (R2-ISSUE-006)
**Problem:** §3.1 Stage 4 specifies 'parallel via Task.WhenAll with shared cancel token.' §3.3 failure modes state 'Sonnet synthesis call timeout (>3s) ... for affected domain; other domains may still resolve.' A shared token means one domain timing out cancels all sibling synthesis tasks, making partial resolution impossible and contradicting the stated failure isolation behavior.
**Fix:** Use a parent request CancellationToken linked to per-domain CancellationTokenSource instances with individual 3-second timeouts. Per-domain timeout cancels only that domain's task; the parent token cancels all (e.g., on bouncer timeout or user cancellation). Document this token hierarchy explicitly.
**Sources:** Codex

### [HIGH] FindByAliasAsync interface omits alias_type parameter required by the planner's spoken-alias fallback query (R2-ISSUE-007)
**Problem:** §5.2 defines the alias fallback as `SELECT canonical_id FROM vocab.aliases WHERE alias_text = @utterance AND alias_type = 'spoken'`. §9.1 declares `FindByAliasAsync(string aliasText, string domain, ...)` with no alias_type parameter. The public contract cannot express the filter the planner requires, forcing either over-broad matches or hidden special-case filtering outside the declared API.
**Fix:** Add an optional `aliasType` parameter (string or enum) to FindByAliasAsync, or formalize alias_type as a field in VocabQuery.Args so ExecuteQueriesAsync can apply it through the batch path.
**Sources:** Codex

### [HIGH] Per-domain DomainResolution for zero-row domains is unspecified — aggregate PipelineOutcome becomes ambiguous (R2-ISSUE-008)
**Problem:** §6.1 states synthesis is invoked 'once per domain that returned results.' §3.3 defines zero-row behavior as EmptyResolution. §6.4 requires PipelineResult.DomainResolutions to be a list. For a multi-domain utterance where one domain returns zero rows, there is no synthesis call and no specified mechanism to produce a DomainResolution for that domain. Downstream consumers cannot distinguish 'evaluated and empty' from 'never processed,' making PartiallyResolved vs Empty outcome computation undefined.
**Fix:** Require one DomainResolution per candidate domain in the planner's output. For domains whose Stage 3 result sets are empty, create an EmptyResolution deterministically before Stage 4 (no synthesis call needed). This makes the DomainResolutions list complete and PipelineOutcome computation unambiguous.
**Sources:** Fran

### [MEDIUM] Query priority tiebreaker is claimed but synthesis prompt and pipeline code have no mechanism to honor it (R2-ISSUE-009)
**Problem:** §5.8 states 'Order conveys priority: queries earlier in the list are considered more likely to be correct. Synthesis uses this ordering as a tiebreaker when multiple queries each return a single row within the same domain.' The §6.2 synthesis system prompt makes no mention of query order or priority. No pipeline code is specified to pass ordering metadata to the model or enforce the tiebreak deterministically.
**Fix:** Either (a) perform the tiebreak deterministically in pipeline code before invoking synthesis (preferred — keeps synthesis stateless), or (b) add explicit query-priority metadata to the synthesis input JSON and reference it in the prompt. Document which approach is authoritative.
**Sources:** Fran

### [HIGH] Raw utterance stored and rendered verbatim in DHF decorations without access controls, contradicting the PHI/IP redaction rationale (R2-ISSUE-010)
**Problem:** SDR-07-010 and §4.5 justify hashing utterances in standard logs because 'the system handles medical vocabulary and engineering IP; rejected transcripts may contain PHI or proprietary design details.' Yet §8.2 stores `public required string Utterance { get; init; }` in the decoration payload, and §8.3 renders 'Utterance text (verbatim)' in the viewer — with no equivalent access controls, redaction rules, or role-gating defined for the DHF path.
**Fix:** Define a DHF utterance-handling policy: either (a) store only the normalized/canonicalized intent fragments needed for audit and omit raw text, (b) gate full-text display in the viewer behind a restricted role, or (c) apply the same redaction/restricted-sink pattern used for security logs. The policy must be consistent with SDR-07-010's stated rationale.
**Sources:** Fran

### [MEDIUM] standard_source field format is inconsistent between lookup inputs and citation output, causing false misses (R2-ISSUE-011)
**Problem:** §5.3 uses FindByStandardSourceAsync with inputs like 'ASTM F136' and 'AMS 4928' (no revision year). §6.4 Citation.StandardSource comment shows 'ASME B1.1-2019' (with revision year). §9.1 FindByStandardSourceAsync comment shows 'ASTM F136' (without). The spec does not define whether standard_source includes revision/year or just the base identifier, creating divergence between corpus storage, lookup matching, and citation rendering.
**Fix:** Define one canonical storage format for standard_source (recommend: base identifier without year, e.g., 'ASTM F136'). Store revision year separately in a revision_year column. Citation rendering combines them. Document normalization rules for lookup inputs that may include year suffixes.
**Sources:** Codex

### [MEDIUM] SchemaVersion first-property ordering requirement is unenforceable with default System.Text.Json behavior (R2-ISSUE-012)
**Problem:** §8.2 states 'The SchemaVersion field is always the first property in the serialized JSON.' System.Text.Json does not guarantee property ordering without explicit JsonPropertyOrder attributes or a custom writer. This requirement is untestable as written and can silently drift across runtime or compiler changes.
**Fix:** Apply [JsonPropertyOrder(0)] to SchemaVersion on VocabularyResolutionDecoration, or implement a custom JsonConverter that writes SchemaVersion first. Add a DecorationSchemaVersionTests assertion that verifies the serialized byte sequence starts with the SchemaVersion property.
**Sources:** Codex

### [HIGH] Medical domain is in v1 mandatory scope but bouncer persona, grammar rules, and corpus are aerospace/toolmaker-centric with no medical-specific content (R2-ISSUE-013)
**Problem:** §1 lists medical as a v1 domain. §10.1 mandatory scope includes medical grammar (§5.7). The bouncer persona (§4.2) is an 'aerospace toolmaker and QC inspector' with no medical examples, rejection criteria, or anatomical vocabulary. §5.7 medical grammar is minimal alias-only with no examples. §1 Out of scope explicitly defers 'medical-specific NLP tuning beyond the generic bouncer persona.' This creates a correctness gap for one of six v1 domains at hackathon demo time.
**Fix:** Either (a) remove medical from v1 mandatory scope and move it to stretch (consistent with the deferred tuning statement), or (b) add medical-specific bouncer instructions (anatomical term examples, rejection criteria for non-medical anatomical misuse) and expand §5.7 grammar before hackathon. Option (a) is lower risk for the demo deadline.
**Sources:** Grok

### [MEDIUM] Pre-cache injection scan forwards known-malicious patterns to the LLM rather than hard-rejecting them (R2-ISSUE-014)
**Problem:** §4.3 states the pre-cache scan 'prevents injection-accepting results from being cached' but still forwards matched patterns to the full bouncer LLM call. The defense-in-depth claim relies on the LLM correctly rejecting patterns it was explicitly told to reject — a single model miss converts a known-bad input into an accepted request. Additionally, phonetic obfuscation of injection patterns (listed in §4.5 threat model) bypasses the deterministic substring/regex scan entirely, and the v1.5 deferral of phonetic normalization leaves this gap open.
**Fix:** For high-confidence injection pattern matches (not borderline), implement a deterministic hard-reject path that returns VocabularyResolutionRejected without an LLM call. Reserve the LLM path for borderline/ambiguous matches where false positives are a real concern. Document the confidence threshold for hard-reject vs. LLM-evaluate. For phonetic evasion, acknowledge the gap explicitly in §4.5 and add it to the v1.5 backlog with a specific ticket reference.
**Sources:** Fran, Grok

### [MEDIUM] Thread class defaulting relies on LLM-derived parsed_intent.action accuracy with no deterministic fallback when action is absent or ambiguous (R2-ISSUE-015)
**Problem:** §5.2 context-dependent class defaulting requires parsed_intent.action to reliably signal internal ('tap', 'tapped hole', 'nut', 'internal') vs. external ('bolt', 'screw', 'stud', 'external') context. This signal comes from the bouncer LLM, which may fail to tag action on voice-mangled input. The spec's fallback (return queries for both 2A and 2B) is correct but will trigger frequent ambiguity escalations for common utterances like 'add a 6-32 thread' where action is absent. No keyword set or deterministic antecedent resolution is specified for the planner to use independently of parsed_intent.
**Fix:** Define an explicit keyword set in the planner (not LLM-dependent) for internal/external classification as a secondary signal. Document the expected ambiguity escalation rate for common utterances and confirm it is acceptable for the demo. If the rate is high, consider defaulting to 2B (most common internal use case) with a low-confidence flag rather than always escalating.
**Sources:** Grok

### [HIGH] Awaited decoration write on critical path couples voice CAD availability to audit system with no circuit-breaker (R2-ISSUE-016)
**Problem:** R1-ISSUE-002 was addressed by making the decoration write awaited (SDR-07-006). This correctly enforces citation durability but now blocks the real-time voice pipeline on every resolution pending a DHF INSERT. A single Postgres contention spike, SPEC-04 migration regression, or network hiccup fails the entire vocabulary resolution with DecorationWriteFailed, requiring the user to retry. No circuit-breaker, retry-with-backoff, or async-with-guarantee pattern is specified. The 40ms p95 budget for decoration write is tight for a blocking call under load.
**Fix:** Retain the awaited write as the primary path (correct for audit integrity) but add: (a) a configurable retry with exponential backoff (1-2 retries within the latency budget) before returning DecorationWriteFailed, and (b) a circuit-breaker that opens after N consecutive write failures and routes to a degraded mode with explicit user notification. Document the degraded mode behavior in §3.3.
**Sources:** Grok

### Coordinator Decisions
- **R2-ISSUE-001**: accepted_defect — Critical contract gap. VocabQueryResult carries only CanonicalTermRow but §0 example, demo moment 1, and synthesis all require domain-specific fields (major diameter, pitch, tap drill for threads; UNS
- **R2-ISSUE-002**: accepted_defect — Type mismatch between bouncer prompt and C# contract. Prompt instructs `normalization_uncertain: true` (boolean) inside parameters; ParsedIntent.Parameters is IReadOnlyDictionary<string, string>. Fix:
- **R2-ISSUE-003**: accepted_defect — Security-critical routing depends on unstable free-text matching. Model phrasing changes silently break security-event routing, ACC-23, and telemetry. Fix: add structured rejection_code enum and secur
- **R2-ISSUE-004**: accepted_defect — Audit corruption risk. Two utterances normalizing to the same cache key replay the first request's raw Utterance into the second request's DHF decoration. Fix: cache stores only decision/domains/parse
- **R2-ISSUE-005**: accepted_defect — Circular dependency. DecorationWriteMs and TotalMs cannot be known before the INSERT that stores them completes. Single-pass immutable write is impossible. Fix: split latency types — decoration payloa
- **R2-ISSUE-006**: accepted_defect — Contradictory cancellation semantics. Shared CancellationToken means one domain timeout cancels all siblings, making the stated per-domain failure isolation impossible. Fix: use per-domain linked Canc
- **R2-ISSUE-007**: accepted_defect — Interface cannot express required filter. §5.2 alias fallback requires alias_type='spoken' but FindByAliasAsync has no alias_type parameter. Fix: add optional aliasType parameter to FindByAliasAsync.
- **R2-ISSUE-008**: accepted_defect — Undefined pipeline behavior for zero-row domains. Synthesis is invoked only for domains with results, but PipelineResult.DomainResolutions must cover all candidate domains for outcome computation. Fix
- **R2-ISSUE-009**: accepted_defect — Claimed tiebreaker has no mechanism. §5.8 says synthesis uses query ordering, but §6.2 prompt has no reference to it. Fix: perform tiebreaker deterministically in pipeline code — query priority orders
- **R2-ISSUE-010**: accepted_defect — Inconsistent privacy policy. SDR-07-010 justifies hashing utterances in logs for PHI/IP, but §8.2 stores raw utterance verbatim in DHF and §8.3 renders it with no access controls. Fix: add SDR-07-011 
- **R2-ISSUE-011**: accepted_defect — Format inconsistency causes false misses. standard_source appears as 'ASTM F136' (no year) in lookups and 'ASME B1.1-2019' (with year) in citation comments. Fix: define canonical storage format as bas
- **R2-ISSUE-012**: accepted_defect — Unenforceable requirement. System.Text.Json does not guarantee property order without explicit annotation. Fix: specify [JsonPropertyOrder(0)] on SchemaVersion. The custom JsonConverter for Vocabulary
- **R2-ISSUE-013**: accepted_defect — Scope mismatch. Medical is listed as v1 mandatory but bouncer persona has no medical expertise, grammar is minimal alias-only, and medical-specific NLP is explicitly deferred. v1 demo deadline risk. F
- **R2-ISSUE-014**: accepted_defect — Defense gap. Known-malicious patterns pass deterministic scan but are still forwarded to the LLM, relying on a single model decision for security. Fix: add hard-reject path for high-confidence injecti
- **R2-ISSUE-015**: accepted_defect — Thread class defaulting depends solely on LLM-derived parsed_intent.action with no deterministic fallback. Common utterances like 'add a 6-32 thread' trigger unnecessary ambiguity escalations. Fix: ad
- **R2-ISSUE-016**: accepted_defect — No resilience on critical-path write. Single Postgres hiccup fails the entire resolution. SDR-07-006 audit-integrity requirement is correct but needs operational resilience. Fix: add 1 retry with 100m

## Open Questions

- Should per-domain synthesis calls be parallelized via Task.WhenAll or sequential? Parallel saves latency but complicates error handling and cancellation. Current draft specifies parallel.
- What is the retention period for the restricted security audit sink (§4.5)? Spec says 'defined by deployment policy' but does not specify a default.
- Should the 15-minute repository cache TTL be configurable via appsettings.json? Current draft makes it configurable.
- The phonetic normalization table (v1.5 item) needs a corpus of common voice manglings. Who produces and validates this corpus?
