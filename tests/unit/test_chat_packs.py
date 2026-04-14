#!/usr/bin/env python3
"""
Module:   Test Chat Packs
Purpose:  Unit tests for pack manifest + checksum governance
          (spec §15).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013

Why these tests:
  Pack governance is the integrity boundary between authority
  documents and the advisory chat surface.  A pack whose bytes no
  longer match its manifest digest has been mutated outside the
  governance workflow and MUST be refused.  These tests prove:
    * The shipped manifest + packs load cleanly.
    * A flipped byte in a pack file raises PackIntegrityError.
    * A manifest entry pointing at a missing file raises.
    * Duplicate rule ids across packs are refused (citation
      ambiguity is a spec §15 failure).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from forge.chat.packs import (
    PackIntegrityError,
    get_default_registry,
    load_registry,
    reset_default_registry,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture()
def workspace_tmp() -> Iterator[Path]:
    """Workspace-local scratch dir.

    pytest's built-in tmp_path fixture resolves through a user-scoped
    location (AppData on Windows) which this environment cannot always
    access.  Rooting scratch space under .pytest-scratch/ in the repo
    keeps permissions predictable.
    """
    root = _repo_root() / ".pytest-scratch" / f"packs-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_default_registry_loads() -> None:
    """The shipped manifest + packs must load without integrity errors."""
    reset_default_registry()
    reg = get_default_registry()
    assert len(reg.packs) >= 4
    ids = {p.id for p in reg.packs}
    assert "NPR-8735.2C" in ids
    assert "MSFC-STD-3012A" in ids


def test_rule_index_is_populated() -> None:
    """Every pack rule must be reachable via the by-id index."""
    reset_default_registry()
    reg = get_default_registry()
    assert reg.all_rules()
    for rule in reg.all_rules():
        assert reg.get_rule(rule.rule_id) is rule


def test_checksum_mismatch_is_refused(workspace_tmp: Path) -> None:
    """Flipping a byte in a pack file triggers PackIntegrityError."""
    src_root = _repo_root() / "packs"
    # Copy manifest verbatim, pack file with a byte change.
    mcopy = workspace_tmp / "manifest.json"
    mcopy.write_bytes((src_root / "manifest.json").read_bytes())
    for pfile in src_root.glob("*.json"):
        if pfile.name == "manifest.json":
            continue
        raw = pfile.read_bytes()
        if pfile.name == "npr-8735-2c.json":
            # Append a byte so digest no longer matches manifest.
            (workspace_tmp / pfile.name).write_bytes(raw + b" ")
        else:
            (workspace_tmp / pfile.name).write_bytes(raw)

    with pytest.raises(PackIntegrityError):
        load_registry(mcopy)


def test_missing_pack_file_is_refused(workspace_tmp: Path) -> None:
    """Manifest entry referencing a non-existent file must raise."""
    manifest = {
        "packs": [
            {
                "id": "FOO", "file": "missing.json", "version": "A",
                "sha256": "00" * 32,
            }
        ]
    }
    mpath = workspace_tmp / "manifest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackIntegrityError):
        load_registry(mpath)


def test_duplicate_rule_id_is_refused(workspace_tmp: Path) -> None:
    """Two packs sharing a rule id breaks citation uniqueness."""
    pack_a = {
        "id": "A", "name": "A", "version": "1", "source": "A",
        "rules": [{"id": "X-1", "title": "t", "text": "", "keywords": []}],
    }
    pack_b = {
        "id": "B", "name": "B", "version": "1", "source": "B",
        "rules": [{"id": "X-1", "title": "t2", "text": "", "keywords": []}],
    }
    a_bytes = (
        json.dumps(pack_a, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    b_bytes = (
        json.dumps(pack_b, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    (workspace_tmp / "a.json").write_bytes(a_bytes)
    (workspace_tmp / "b.json").write_bytes(b_bytes)
    manifest = {
        "packs": [
            {
                "id": "A", "file": "a.json", "version": "1",
                "sha256": hashlib.sha256(a_bytes).hexdigest(),
            },
            {
                "id": "B", "file": "b.json", "version": "1",
                "sha256": hashlib.sha256(b_bytes).hexdigest(),
            },
        ]
    }
    mpath = workspace_tmp / "manifest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackIntegrityError):
        load_registry(mpath)
