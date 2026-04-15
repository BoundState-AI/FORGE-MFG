#!/usr/bin/env python3
"""
Module:   Test Evidence Registry (Decision 1)
Purpose:  Integration tests for the build spec v1.4.2 Decision 1
          evidence contract — upload-url scope auth, confirm +
          registry write, ref hydration on inspection and NCR
          writes, PATCH /api/ncrs/{id}, and the NCR close
          evidence gate for rework / repair.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why these tests:
  The evidence registry is the single source of truth for uploaded
  blobs; every parent-entity write that carries evidence depends on
  this layer holding.  Each test exercises one explicit invariant
  so a regression surfaces as a targeted failure rather than a
  generic 500 — the distinct 422 codes (EVIDENCE_FORMAT_INVALID,
  EVIDENCE_NOT_CONFIRMED, EVIDENCE_SCOPE_MISMATCH,
  EVIDENCE_PROGRAM_MISMATCH) map to UI-facing error branches.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text

_SYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
).replace("+asyncpg", "")

_ASYNC_URL = os.getenv(
    "FORGE_DATABASE_URL",
    "postgresql+asyncpg://forge:forge@localhost:5432/forge",
)

SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
INSPECTOR_USER_ID = "00000000-0000-0000-0000-0000000000d1"
ENGINEER_USER_ID = "00000000-0000-0000-0000-0000000000d2"


def _db_available() -> bool:
    try:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


def _ensure_user(uid: str, email: str, name: str) -> None:
    engine = create_engine(_SYNC_URL)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM users WHERE id = CAST(:id AS uuid)"
        ).bindparams(id=uid)).fetchone()
        if not row:
            conn.execute(text("""
                INSERT INTO users (id, cognito_sub, email, display_name,
                    is_system_actor, active, created_by, updated_by)
                VALUES (CAST(:id AS uuid), :sub, :email, :name,
                    false, true,
                    CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=uid, sub=f"test-{uid[-4:]}", email=email, name=name,
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()


def _ensure_program(code: str) -> str:
    engine = create_engine(_SYNC_URL)
    prog_id = str(uuid.uuid4())
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM programs WHERE code = :code"
        ).bindparams(code=code)).fetchone()
        if row:
            prog_id = str(row[0])
        else:
            conn.execute(text("""
                INSERT INTO programs (id, code, name, active,
                    created_by, updated_by)
                VALUES (CAST(:id AS uuid), :code, :name,
                    true, CAST(:sys AS uuid), CAST(:sys AS uuid))
            """).bindparams(
                id=prog_id, code=code, name=f"Test {code}",
                sys=SYSTEM_USER_ID,
            ))
            conn.commit()
    engine.dispose()
    return prog_id


@pytest.mark.skipif(not _db_available(), reason="Postgres not available")
class TestEvidenceRegistry:
    """End-to-end coverage for Decision 1 evidence contract."""

    @pytest.fixture(autouse=True)
    def _setup(self) -> Any:
        from forge.api.app import create_app
        from forge.config import Settings
        from forge.db import init_engine

        self._clean_test_data()
        self.prog_id = _ensure_program("TST-EV-A")
        self.prog_b_id = _ensure_program("TST-EV-B")
        _ensure_user(INSPECTOR_USER_ID, "insp-ev@test.com", "Inspector EV")
        _ensure_user(ENGINEER_USER_ID, "eng-ev@test.com", "Engineer EV")

        settings = Settings(
            env="test", debug=False, auth_mode="local",
            database_url=_ASYNC_URL,
        )
        init_engine(settings.database_url)
        self.app = create_app(settings=settings)
        yield
        self._clean_test_data()

    def _clean_test_data(self) -> None:
        engine = create_engine(_SYNC_URL)
        with engine.connect() as conn:
            try:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "DISABLE TRIGGER trg_protect_audit_records"
                ))
                conn.execute(text(
                    "DELETE FROM audit_records "
                    "WHERE action_type LIKE 'work_order.%' "
                    "   OR action_type LIKE 'operation.%' "
                    "   OR action_type LIKE 'inspection.%' "
                    "   OR action_type LIKE 'ncr.%' "
                    "   OR action_type LIKE 'labor.%' "
                    "   OR action_type LIKE 'evidence.%'"
                ))
                conn.execute(text("DELETE FROM sign_off_events"))
                conn.execute(text("UPDATE part_instances SET reason_ncr_id = NULL"))
                conn.execute(text("DELETE FROM nonconformance_reports"))
                conn.execute(text("DELETE FROM inspection_events"))
                conn.execute(text("DELETE FROM labor_entries"))
                conn.execute(text("DELETE FROM operations"))
                conn.execute(text("DELETE FROM work_orders"))
                conn.execute(text("DELETE FROM evidence_confirmations"))
                conn.execute(text("DELETE FROM idempotency_records"))
                conn.commit()
            finally:
                conn.execute(text(
                    "ALTER TABLE audit_records "
                    "ENABLE TRIGGER trg_protect_audit_records"
                ))
                conn.commit()
        engine.dispose()

    def _override_as(
        self, user_id: str, roles: list[str],
        programs: list[str] | None = None,
    ) -> None:
        """Install an auth override scoped to the declared roles' perms."""
        from forge.api.auth import ROLE_PERMISSIONS, AuthContext

        role_perms: set[str] = set()
        for role in roles:
            role_perms |= ROLE_PERMISSIONS.get(role, frozenset())
        self.app.state.auth_override = AuthContext(
            sub=user_id, username=f"override-{user_id[-4:]}",
            roles=roles, programs=programs or ["*"],
            permissions=frozenset(role_perms),
        )

    def _clear_override(self) -> None:
        self.app.state.auth_override = None

    async def _client(self) -> Any:
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=self.app)
        return AsyncClient(transport=transport, base_url="http://test")

    def _post_headers(self, program_id: str | None) -> dict[str, str]:
        """Default POST headers: Idempotency-Key + optional X-Program-Id.

        Every mutating POST in this app requires Idempotency-Key per
        spec §5.15; the evidence routes additionally need
        X-Program-Id.  Bundling them in one helper keeps each test
        body focused on the assertion under test.
        """
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        if program_id is not None:
            headers["X-Program-Id"] = program_id
        return headers

    def _program_headers(self, program_id: str) -> dict[str, str]:
        return {"X-Program-Id": program_id}

    # ------------------------------------------------------------------
    # Upload-url
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_upload_url_rejects_inspection_scope_without_inspect_create(
        self,
    ) -> None:
        """Engineer role lacks inspect:create; inspection-scope upload 403s."""
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                "/api/evidence/upload-url",
                headers=self._post_headers(self.prog_id),
                json={
                    "scope": "inspection_evidence",
                    "filename": "photo.jpg",
                    "content_type": "image/jpeg",
                    "size_bytes": 10_000,
                },
            )
        self._clear_override()
        assert r.status_code == 403
        assert "inspection_evidence" in r.text

    @pytest.mark.asyncio
    async def test_upload_url_inspection_happy_path(self) -> None:
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                "/api/evidence/upload-url",
                headers=self._post_headers(self.prog_id),
                json={
                    "scope": "inspection_evidence",
                    "filename": "photo.jpg",
                    "content_type": "image/jpeg",
                    "size_bytes": 10_000,
                },
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["s3_key"].startswith("evidence/inspection_evidence/")
        assert "x-amz-checksum-algorithm" in body["upload_headers"]
        # Spec §5.8: x-amz-checksum-sha256 is the authoritative header.
        assert "x-amz-checksum-sha256" in body["upload_headers"]
        assert body["upload_headers"]["Content-Type"] == "image/jpeg"
        assert body["max_bytes"] > 0

    @pytest.mark.asyncio
    async def test_upload_url_ncr_scope_for_qa_manager(self) -> None:
        self._override_as(ENGINEER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                "/api/evidence/upload-url",
                headers=self._post_headers(self.prog_id),
                json={
                    "scope": "ncr_evidence",
                    "filename": "rework.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 50_000,
                },
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        assert r.json()["s3_key"].startswith("evidence/ncr_evidence/")

    # ------------------------------------------------------------------
    # Confirm
    # ------------------------------------------------------------------

    async def _upload_and_confirm(
        self, *, scope: str, program_id: str, user_id: str,
        role: str, filename: str = "file.bin",
        content_hash: str | None = None,
    ) -> dict[str, Any]:
        """Helper: issue upload-url then confirm.

        Frozen spec confirm body is {s3_key, content_hash} only —
        all other metadata is already on the pending registry row.
        """
        self._override_as(user_id, roles=[role])
        hash_value = content_hash or ("a" * 64)
        async with await self._client() as client:
            r = await client.post(
                "/api/evidence/upload-url",
                headers=self._post_headers(program_id),
                json={
                    "scope": scope,
                    "filename": filename,
                    "content_type": "application/octet-stream",
                    "size_bytes": 1024,
                },
            )
            assert r.status_code == 200, r.text
            up = r.json()

            r = await client.post(
                "/api/evidence/confirm",
                headers=self._post_headers(program_id),
                json={
                    "s3_key": up["s3_key"],
                    "content_hash": hash_value,
                },
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        return r.json()

    @pytest.mark.asyncio
    async def test_confirm_happy_path_returns_structured_ref(self) -> None:
        ref = await self._upload_and_confirm(
            scope="inspection_evidence", program_id=self.prog_id,
            user_id=INSPECTOR_USER_ID, role="inspector",
        )
        assert ref["filename"] == "file.bin"
        assert ref["content_hash"] == "a" * 64
        assert ref["uploaded_by"] == INSPECTOR_USER_ID
        assert ref["uploaded_at"]  # ISO timestamp present

    @pytest.mark.asyncio
    async def test_confirm_hash_mismatch_on_re_confirm(self) -> None:
        """Re-confirming the same key with a different hash 422s."""
        ref = await self._upload_and_confirm(
            scope="inspection_evidence", program_id=self.prog_id,
            user_id=INSPECTOR_USER_ID, role="inspector",
        )
        s3_key = ref["s3_key"]

        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                "/api/evidence/confirm",
                headers=self._post_headers(self.prog_id),
                json={
                    "s3_key": s3_key,
                    "content_hash": "b" * 64,  # different hash
                },
            )
        self._clear_override()
        assert r.status_code == 422
        assert "EVIDENCE_HASH_MISMATCH" in r.text

    @pytest.mark.asyncio
    async def test_confirm_unknown_s3_key_rejected(self) -> None:
        """Confirm with an s3_key that has no pending row 422s."""
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                "/api/evidence/confirm",
                headers=self._post_headers(self.prog_id),
                json={
                    "s3_key": "evidence/inspection_evidence/never-presigned",
                    "content_hash": "c" * 64,
                },
            )
        self._clear_override()
        assert r.status_code == 422
        assert "EVIDENCE_UPLOAD_UNKNOWN" in r.text

    @pytest.mark.asyncio
    async def test_confirm_program_mismatch_rejected(self) -> None:
        """Confirm under a different program than the pending row 422s."""
        # Pending row created under prog A.
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                "/api/evidence/upload-url",
                headers=self._post_headers(self.prog_id),
                json={
                    "scope": "inspection_evidence",
                    "filename": "x.bin",
                    "content_type": "application/octet-stream",
                    "size_bytes": 1024,
                },
            )
            assert r.status_code == 200
            up = r.json()

            # Confirm attempted under prog B.
            r = await client.post(
                "/api/evidence/confirm",
                headers=self._post_headers(self.prog_b_id),
                json={
                    "s3_key": up["s3_key"],
                    "content_hash": "d" * 64,
                },
            )
        self._clear_override()
        assert r.status_code == 422
        assert "EVIDENCE_PROGRAM_MISMATCH" in r.text

    # ------------------------------------------------------------------
    # Inspection evidence hydration
    # ------------------------------------------------------------------

    async def _make_started_held_op(self, program_id: str) -> dict[str, Any]:
        """Create a WO + held op, release, start — returns started op dict."""
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/work-orders",
                headers={
                    "X-Program-Id": program_id,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "program_id": program_id,
                    "engine_serial": f"E-EV-{uuid.uuid4().hex[:6]}",
                    "module": "Powerhead",
                    "build_phase": "fabrication",
                },
            )
            assert r.status_code == 201, r.text
            wo = r.json()
            r = await client.post(
                f"/api/work-orders/{wo['id']}/operations",
                headers={
                    "X-Program-Id": program_id,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "description": "Op 1", "sequence": 1,
                    "inspection_hold": True, "safety_critical": False,
                    "sign_off_roles_required": [],
                },
            )
            assert r.status_code == 201, r.text
            op = r.json()

        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/work-orders/{wo['id']}/release",
                headers={
                    "X-Program-Id": program_id,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={"version": wo["version"]},
            )
            assert r.status_code == 200, r.text

        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/start",
                headers={
                    "X-Program-Id": program_id,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "version": op["version"],
                    "role": "operator", "shift": "day",
                },
            )
            assert r.status_code == 200, r.text
            return r.json()  # type: ignore[no-any-return]

    @pytest.mark.asyncio
    async def test_inspection_rejects_bare_string_evidence_ref(self) -> None:
        op = await self._make_started_held_op(self.prog_id)
        ref = await self._upload_and_confirm(
            scope="inspection_evidence", program_id=self.prog_id,
            user_id=INSPECTOR_USER_ID, role="inspector",
        )

        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                headers={
                    "X-Program-Id": self.prog_id,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "criteria": "x",
                    "result": "accept",
                    # Bare string — Decision 1 rejects this at the
                    # service layer even though the confirmed key
                    # exists.
                    "evidence_refs": [ref["s3_key"]],
                },
            )
        self._clear_override()
        assert r.status_code == 422
        # The schema accepts dict-or-string so the service layer
        # emits the structured EVIDENCE_FORMAT_INVALID code rather
        # than a generic Pydantic dict_type error.
        assert "EVIDENCE_FORMAT_INVALID" in r.text

    @pytest.mark.asyncio
    async def test_inspection_hydrates_from_registry(self) -> None:
        op = await self._make_started_held_op(self.prog_id)
        ref = await self._upload_and_confirm(
            scope="inspection_evidence", program_id=self.prog_id,
            user_id=INSPECTOR_USER_ID, role="inspector",
        )
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                headers={
                    "X-Program-Id": self.prog_id,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "criteria": "x",
                    "result": "accept",
                    # Client forges metadata.  Server ignores the
                    # forged fields and hydrates from registry.
                    "evidence_refs": [{
                        "s3_key": ref["s3_key"],
                        "filename": "HACKED",
                        "content_hash": "ffff",
                    }],
                },
            )
        self._clear_override()
        assert r.status_code == 201, r.text
        body = r.json()
        assert len(body["evidence_refs"]) == 1
        persisted = body["evidence_refs"][0]
        # Filename came from the registry, not the forged body.
        assert persisted["filename"] == "file.bin"
        assert persisted["content_hash"] == "a" * 64

    @pytest.mark.asyncio
    async def test_inspection_rejects_scope_mismatch(self) -> None:
        """NCR-scoped evidence cannot be attached to an inspection."""
        op = await self._make_started_held_op(self.prog_id)
        ref = await self._upload_and_confirm(
            scope="ncr_evidence", program_id=self.prog_id,
            user_id=ENGINEER_USER_ID, role="qa_manager",
        )
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                headers={
                    "X-Program-Id": self.prog_id,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "criteria": "x",
                    "result": "accept",
                    "evidence_refs": [{"s3_key": ref["s3_key"]}],
                },
            )
        self._clear_override()
        assert r.status_code == 422
        assert "EVIDENCE_SCOPE_MISMATCH" in r.text

    @pytest.mark.asyncio
    async def test_inspection_rejects_program_mismatch(self) -> None:
        op = await self._make_started_held_op(self.prog_id)
        # Evidence uploaded in program B.
        ref = await self._upload_and_confirm(
            scope="inspection_evidence", program_id=self.prog_b_id,
            user_id=INSPECTOR_USER_ID, role="inspector",
        )
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                headers={
                    "X-Program-Id": self.prog_id,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "criteria": "x",
                    "result": "accept",
                    "evidence_refs": [{"s3_key": ref["s3_key"]}],
                },
            )
        self._clear_override()
        assert r.status_code == 422
        assert "EVIDENCE_PROGRAM_MISMATCH" in r.text

    @pytest.mark.asyncio
    async def test_inspection_rejects_unconfirmed_ref(self) -> None:
        op = await self._make_started_held_op(self.prog_id)
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                headers={
                    "X-Program-Id": self.prog_id,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "criteria": "x",
                    "result": "accept",
                    "evidence_refs": [{"s3_key": "evidence/fake/never-uploaded"}],
                },
            )
        self._clear_override()
        assert r.status_code == 422
        assert "EVIDENCE_NOT_CONFIRMED" in r.text

    # ------------------------------------------------------------------
    # NCR PATCH + close gate
    # ------------------------------------------------------------------

    async def _create_ncr(self, program_id: str, wo_id: str) -> dict[str, Any]:
        self._clear_override()
        async with await self._client() as client:
            r = await client.post(
                "/api/ncrs",
                headers={
                    "X-Program-Id": program_id,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "work_order_id": wo_id,
                    "description": "test ncr",
                },
            )
        assert r.status_code == 201, r.text
        return r.json()  # type: ignore[no-any-return]

    async def _disposition_ncr(
        self, program_id: str, ncr: dict[str, Any], disposition: str,
    ) -> dict[str, Any]:
        self._override_as(ENGINEER_USER_ID, roles=["engineer"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}/disposition",
                headers={"X-Program-Id": program_id},
                json={
                    "version": ncr["version"],
                    "disposition": disposition,
                    "disposition_rationale": "test",
                },
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    @pytest.mark.asyncio
    async def test_ncr_patch_replaces_evidence_refs(self) -> None:
        op = await self._make_started_held_op(self.prog_id)
        # Fetch WO id via op detail (op payload doesn't carry it).
        async with await self._client() as client:
            detail = await client.get(
                f"/api/operations/{op['id']}",
                headers={"X-Program-Id": self.prog_id},
            )
        wo_id = detail.json()["work_order_id"]
        ncr = await self._create_ncr(self.prog_id, wo_id)

        ref_a = await self._upload_and_confirm(
            scope="ncr_evidence", program_id=self.prog_id,
            user_id=ENGINEER_USER_ID, role="qa_manager",
        )
        ref_b = await self._upload_and_confirm(
            scope="ncr_evidence", program_id=self.prog_id,
            user_id=ENGINEER_USER_ID, role="qa_manager",
        )

        # First patch attaches ref_a.
        self._override_as(ENGINEER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}",
                headers={"X-Program-Id": self.prog_id},
                json={
                    "version": ncr["version"],
                    "evidence_refs": [{"s3_key": ref_a["s3_key"]}],
                },
            )
            assert r.status_code == 200, r.text
            ncr1 = r.json()
            assert len(ncr1["evidence_refs"]) == 1

            # Second patch REPLACES with ref_b only — not append.
            r = await client.patch(
                f"/api/ncrs/{ncr1['id']}",
                headers={"X-Program-Id": self.prog_id},
                json={
                    "version": ncr1["version"],
                    "evidence_refs": [{"s3_key": ref_b["s3_key"]}],
                },
            )
            assert r.status_code == 200, r.text
            ncr2 = r.json()
            assert len(ncr2["evidence_refs"]) == 1
            assert ncr2["evidence_refs"][0]["s3_key"] == ref_b["s3_key"]
        self._clear_override()

    @pytest.mark.asyncio
    async def test_ncr_close_blocked_without_evidence_for_rework(self) -> None:
        op = await self._make_started_held_op(self.prog_id)
        async with await self._client() as client:
            detail = await client.get(
                f"/api/operations/{op['id']}",
                headers={"X-Program-Id": self.prog_id},
            )
        wo_id = detail.json()["work_order_id"]
        ncr = await self._create_ncr(self.prog_id, wo_id)
        ncr = await self._disposition_ncr(self.prog_id, ncr, "rework")

        self._override_as(ENGINEER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/ncrs/{ncr['id']}/close",
                headers=self._post_headers(self.prog_id),
                json={"version": ncr["version"]},
            )
        self._clear_override()
        assert r.status_code == 422
        assert "EVIDENCE_REQUIRED_FOR_CLOSE" in r.text

    @pytest.mark.asyncio
    async def test_ncr_close_allowed_with_evidence_for_rework(self) -> None:
        op = await self._make_started_held_op(self.prog_id)
        async with await self._client() as client:
            detail = await client.get(
                f"/api/operations/{op['id']}",
                headers={"X-Program-Id": self.prog_id},
            )
        wo_id = detail.json()["work_order_id"]
        ncr = await self._create_ncr(self.prog_id, wo_id)
        ncr = await self._disposition_ncr(self.prog_id, ncr, "rework")

        ref = await self._upload_and_confirm(
            scope="ncr_evidence", program_id=self.prog_id,
            user_id=ENGINEER_USER_ID, role="qa_manager",
        )

        self._override_as(ENGINEER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.patch(
                f"/api/ncrs/{ncr['id']}",
                headers={"X-Program-Id": self.prog_id},
                json={
                    "version": ncr["version"],
                    "evidence_refs": [{"s3_key": ref["s3_key"]}],
                },
            )
            assert r.status_code == 200, r.text
            ncr_patched = r.json()

            r = await client.post(
                f"/api/ncrs/{ncr_patched['id']}/close",
                headers=self._post_headers(self.prog_id),
                json={"version": ncr_patched["version"]},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "closed"

    @pytest.mark.asyncio
    async def test_ncr_close_allowed_without_evidence_for_use_as_is(
        self,
    ) -> None:
        op = await self._make_started_held_op(self.prog_id)
        async with await self._client() as client:
            detail = await client.get(
                f"/api/operations/{op['id']}",
                headers={"X-Program-Id": self.prog_id},
            )
        wo_id = detail.json()["work_order_id"]
        ncr = await self._create_ncr(self.prog_id, wo_id)
        ncr = await self._disposition_ncr(self.prog_id, ncr, "use_as_is")

        self._override_as(ENGINEER_USER_ID, roles=["qa_manager"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/ncrs/{ncr['id']}/close",
                headers=self._post_headers(self.prog_id),
                json={"version": ncr["version"]},
            )
        self._clear_override()
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "closed"

    # ------------------------------------------------------------------
    # Cleanup eligibility
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pending_upload_ageout_returns_unconfirmed_rows(self) -> None:
        """list_stale_pending_uploads surfaces rows that never confirmed."""
        from datetime import UTC, datetime, timedelta

        from forge.db import get_session
        from forge.services.evidence_service import (
            list_stale_pending_uploads,
        )

        # Issue an upload URL (writes a pending row) but do NOT
        # confirm it.  After the ageout window, the pending row
        # must show up in the cleanup query.
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                "/api/evidence/upload-url",
                headers=self._post_headers(self.prog_id),
                json={
                    "scope": "inspection_evidence",
                    "filename": "stale.bin",
                    "content_type": "application/octet-stream",
                    "size_bytes": 2048,
                },
            )
            assert r.status_code == 200, r.text
            pending_key = r.json()["s3_key"]

            # Separately, also confirm a different upload — the
            # confirmed row must NOT show up in the pending-cleanup
            # query, so we have a negative control in the same test.
            ref_confirmed = await self._upload_and_confirm(
                scope="inspection_evidence", program_id=self.prog_id,
                user_id=INSPECTOR_USER_ID, role="inspector",
            )
        self._clear_override()

        async for s in get_session():
            future = datetime.now(UTC) + timedelta(days=30)
            rows = await list_stale_pending_uploads(
                session=s,
                older_than=timedelta(minutes=-1),  # effectively "all"
                now=future,
            )
            keys = {row.s3_key for row in rows}
            assert pending_key in keys
            assert ref_confirmed["s3_key"] not in keys
            break

    @pytest.mark.asyncio
    async def test_cleanup_excludes_referenced_evidence(self) -> None:
        from datetime import UTC, datetime, timedelta

        from forge.db import get_session
        from forge.services.evidence_service import (
            list_unreferenced_confirmed_evidence,
        )

        # Confirm two refs.  Attach one to an inspection.  Cleanup
        # at now+delta should include the UNATTACHED ref only.
        op = await self._make_started_held_op(self.prog_id)
        ref_referenced = await self._upload_and_confirm(
            scope="inspection_evidence", program_id=self.prog_id,
            user_id=INSPECTOR_USER_ID, role="inspector",
        )
        ref_orphan = await self._upload_and_confirm(
            scope="inspection_evidence", program_id=self.prog_id,
            user_id=INSPECTOR_USER_ID, role="inspector",
        )
        self._override_as(INSPECTOR_USER_ID, roles=["inspector"])
        async with await self._client() as client:
            r = await client.post(
                f"/api/operations/{op['id']}/inspect",
                headers={
                    "X-Program-Id": self.prog_id,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
                json={
                    "criteria": "x", "result": "accept",
                    "evidence_refs": [{"s3_key": ref_referenced["s3_key"]}],
                },
            )
            assert r.status_code == 201, r.text
        self._clear_override()

        async for s in get_session():
            future = datetime.now(UTC) + timedelta(days=30)
            rows = await list_unreferenced_confirmed_evidence(
                session=s,
                older_than=timedelta(minutes=-1),  # effectively "all"
                now=future,
            )
            keys = {row.s3_key for row in rows}
            assert ref_orphan["s3_key"] in keys
            assert ref_referenced["s3_key"] not in keys
            break
