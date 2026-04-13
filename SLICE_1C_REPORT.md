# Slice 1C Report

## 1. Files changed

| File | Change |
|---|---|
| `forge/audit/logger.py` | Hash-chained audit logger with advisory lock, deterministic serialization, genesis convention |
| `forge/audit/integrity.py` | Integrity verifier: sequence continuity, previous_hash linkage, record_hash correctness |
| `forge/services/versioning.py` | Optimistic locking: check_version, increment_version, VersionConflictError -> 409 |
| `forge/api/middleware/idempotency.py` | Real idempotency middleware with replay/conflict/expiry/user-scoping |
| `migrations/versions/46a86e0c1628_...py` | Forward migration: DB trigger blocking UPDATE/DELETE on audit_records |
| `tests/unit/test_audit_integrity.py` | Hash determinism, field coverage, genesis convention, chain verification, tamper detection |
| `tests/integration/test_audit_under_concurrency.py` | 10 concurrent writers produce linear chain with unique sequences |
| `tests/integration/test_audit_append_only.py` | Insert allowed, update blocked, delete blocked |
| `tests/unit/test_idempotency.py` | First execute, replay no side-effect, conflict 409, missing key 400, expiry, user scoping |
| `tests/unit/test_optimistic_locking.py` | Version match passes, mismatch raises, round-trip with real DB |

## 2. Requirement -> File -> Test mapping

| Requirement | File(s) | Test(s) |
|---|---|---|
| 1. Audit logger (hash chain) | `forge/audit/logger.py` | `test_hash_is_deterministic`, `test_changing_any_field_changes_hash`, `test_audit_chain_verifies_clean` |
| 2. Advisory-lock serialization | `forge/audit/logger.py` (pg_advisory_xact_lock) | `test_concurrent_audit_writes_do_not_fork_chain` |
| 3. Integrity verifier | `forge/audit/integrity.py` | `test_audit_chain_verifies_clean`, `test_tampered_record_detected` |
| 4. Append-only enforcement | Migration `46a86e0c1628` | `test_insert_allowed`, `test_update_blocked`, `test_delete_blocked` |
| 5. Idempotency middleware | `forge/api/middleware/idempotency.py` | `test_first_post_executes`, `test_replay_returns_cached_no_side_effect`, `test_same_key_different_body_409`, `test_missing_key_400` |
| 6. User scoping | idempotency middleware | `test_different_users_same_key_no_collision` |
| 7. Replay prevents side effects | test probe counter | `test_replay_returns_cached_no_side_effect` (counter stays at 1) |
| 8. Expiry behavior | idempotency middleware | `test_expired_record_executes_fresh` |
| 9. Optimistic locking | `forge/services/versioning.py` | `test_check_version_passes_on_match`, `test_check_version_raises_on_mismatch`, `test_optimistic_lock_round_trip` |
| 10. Error codes | middleware + versioning | `test_missing_key_400` (400), `test_same_key_different_body_409` (409), `test_conflict_response_is_409` (409) |
| 11. Forward migration | `46a86e0c1628` | `alembic upgrade head` clean |

## 3. Spec deviations

None.

## 4. Design explanations

**Audit hash-chain:** SHA-256 over canonical JSON (sorted keys, deterministic separators) of ALL stored fields. Genesis record uses `previous_hash = "0" * 64`. Each record's `record_hash` becomes the next record's `previous_hash`.

**Advisory-lock strategy:** `pg_advisory_xact_lock(ADVISORY_LOCK_ID)` acquired inside the transaction before reading the latest sequence/hash. Lock is released automatically on commit/rollback. All audit writers share one lock -- serialized globally.

**Idempotency semantics:** Middleware intercepts POST requests, computes SHA-256 of request body, checks `idempotency_records` table scoped to `(key, user_id)`. Same hash -> replay. Different hash -> 409. Expired (>24h) -> delete and re-execute. Missing key -> 400.

**Optimistic locking:** `check_version(entity, expected)` compares the caller's version against the entity's DB version. Mismatch raises `VersionConflictError` -> 409 with `current_version` in response. `increment_version()` advances the counter after a successful mutation.

## 5. Gate results

```
ruff check forge/ tests/                      -> All checks passed!
mypy forge/ tests/ --ignore-missing-imports   -> Success: no issues found in 110 source files
pytest tests/ -v                              -> 48 passed, 0 failed
alembic upgrade head (clean DB)               -> 3 revisions applied successfully
```

## 6. Review readiness

Slice 1C is review-ready.
