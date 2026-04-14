#!/usr/bin/env python3
"""
Module:   Pack Loader
Purpose:  Load and validate Manufacturing Authority Packs
          (spec §1.2, §15).

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Spec §15 — Pack Governance Workflow — requires packs to be
  integrity-checked before their rules drive any advisory behavior.
  A pack whose file bytes no longer match its manifest SHA-256 has
  been mutated outside the governance workflow and MUST NOT be
  trusted.  We refuse to serve it by raising PackIntegrityError
  rather than silently skipping: a missing rule surfaces as a 422
  when the chat pipeline tries to cite it, which is easier to
  diagnose than a rule that quietly disappeared.

  The loader is deterministic: same files on disk → identical in-
  memory representation.  This keeps the 4-stage pipeline's retrieval
  stage reproducible (required for the "≥90% citation accuracy"
  acceptance test in spec §10.3 ACC-02).

  Pack JSON shape is intentionally flat: {id, rules[{id, title,
  text, section, keywords}]}.  Future fields can extend the schema
  without breaking loaders — unknown keys are ignored, not rejected,
  so a manifest can be updated before all consumers see it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


class PackIntegrityError(Exception):
    """Raised when a pack's on-disk bytes do not match its manifest digest.

    This is the single failure mode the rest of the pipeline needs to
    know about — other load errors (missing file, bad JSON) also raise
    this so the chat layer can translate to a 422 RULE_VIOLATION
    envelope without discriminating Python exception classes.
    """


@dataclass(frozen=True, slots=True)
class PackRule:
    """One rule extracted from an authority pack.

    The id is intentionally the canonical standard reference
    (e.g. NPR-8735.2C-3.2.1) so citations in chat responses match
    what a QA auditor would see in the source document.
    """

    pack_id: str
    rule_id: str
    section: str
    title: str
    text: str
    keywords: tuple[str, ...]

    def citation(self) -> str:
        """Return the canonical citation string for this rule."""
        return f"{self.rule_id} — {self.title}"


@dataclass(frozen=True, slots=True)
class Pack:
    """One authority pack (NPR, NASA-STD, MSFC-STD)."""

    id: str
    name: str
    version: str
    source: str
    description: str
    sha256: str
    rules: tuple[PackRule, ...]


@dataclass(frozen=True, slots=True)
class PackRegistry:
    """Loaded, integrity-checked collection of all packs."""

    packs: tuple[Pack, ...]
    manifest_path: Path
    rules_by_id: dict[str, PackRule] = field(default_factory=dict)

    def all_rules(self) -> tuple[PackRule, ...]:
        """Flatten every pack's rules into a single tuple."""
        return tuple(r for p in self.packs for r in p.rules)

    def get_rule(self, rule_id: str) -> PackRule | None:
        """Return the rule with this id, or None."""
        return self.rules_by_id.get(rule_id)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rule_from_dict(pack_id: str, raw: dict[str, object]) -> PackRule:
    """Parse a single rule dict out of a pack JSON file."""
    if not isinstance(raw, dict):
        raise PackIntegrityError(
            f"pack {pack_id}: rule entry must be an object"
        )
    if "id" not in raw:
        raise PackIntegrityError(
            f"pack {pack_id}: rule missing required id"
        )
    kws = raw.get("keywords") or []
    if not isinstance(kws, list):
        raise PackIntegrityError(
            f"pack {pack_id}: rule {raw.get('id')!r} has non-list keywords"
        )
    return PackRule(
        pack_id=pack_id,
        rule_id=str(raw["id"]),
        section=str(raw.get("section", "")),
        title=str(raw.get("title", "")),
        text=str(raw.get("text", "")),
        keywords=tuple(str(k) for k in kws),
    )


def _load_one_pack(
    pack_dir: Path, entry: dict[str, object],
) -> Pack:
    """Load + verify a single pack file against a manifest entry.

    State leaving: pack entry present in manifest, file unread.
    State entering: Pack dataclass with verified bytes.
    Invariant verified: sha256(file_bytes) == entry["sha256"].
    """
    pid = str(entry["id"])
    filename = str(entry["file"])
    expected_digest = str(entry["sha256"])
    path = pack_dir / filename
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise PackIntegrityError(
            f"pack {pid}: file {filename} not found"
        ) from exc

    actual_digest = _sha256(raw_bytes)
    if actual_digest != expected_digest:
        raise PackIntegrityError(
            f"pack {pid}: checksum mismatch — "
            f"expected {expected_digest}, got {actual_digest}"
        )

    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackIntegrityError(
            f"pack {pid}: invalid JSON in {filename}: {exc}"
        ) from exc

    rules_raw = payload.get("rules") or []
    if not isinstance(rules_raw, list):
        raise PackIntegrityError(
            f"pack {pid}: rules must be a list, got {type(rules_raw).__name__}"
        )
    rules = tuple(_rule_from_dict(pid, r) for r in rules_raw)

    return Pack(
        id=pid,
        name=str(payload.get("name", pid)),
        version=str(entry.get("version") or payload.get("version") or ""),
        source=str(payload.get("source", pid)),
        description=str(entry.get("description") or payload.get("description") or ""),
        sha256=actual_digest,
        rules=rules,
    )


def _parse_manifest(mpath: Path) -> list[dict[str, object]]:
    """Parse manifest.json into its list of pack entries.

    Extracted so load_registry stays under the 50-line Tier-1 cap and
    so every manifest-level failure (missing file, bad JSON, wrong
    shape) maps to PackIntegrityError in one place.
    """
    try:
        manifest_bytes = mpath.read_bytes()
    except FileNotFoundError as exc:
        raise PackIntegrityError(
            f"manifest not found at {mpath}"
        ) from exc
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackIntegrityError(
            f"manifest {mpath}: invalid JSON: {exc}"
        ) from exc
    entries = manifest.get("packs")
    if not isinstance(entries, list):
        raise PackIntegrityError(
            f"manifest {mpath}: packs must be a list"
        )
    return list(entries)


def _index_rules(packs: tuple[Pack, ...]) -> dict[str, PackRule]:
    """Build the flat rule_id -> PackRule map + enforce uniqueness."""
    index: dict[str, PackRule] = {}
    for pack in packs:
        for rule in pack.rules:
            if rule.rule_id in index:
                raise PackIntegrityError(
                    f"duplicate rule id {rule.rule_id!r} across packs"
                )
            index[rule.rule_id] = rule
    return index


def load_registry(manifest_path: Path | str) -> PackRegistry:
    """Load and checksum-verify every pack referenced in the manifest.

    State leaving: manifest + pack files on disk.
    State entering: in-memory PackRegistry with all rules indexed by id.
    Invariants verified:
      1. manifest.json is well-formed JSON with a `packs` list.
      2. Every pack file exists and its sha256 matches manifest entry.
      3. Rule ids are unique across the registry (duplicate rule ids
         would make citation ambiguous -- spec ACC-02 requires
         citations match a single source of truth).
    Raises PackIntegrityError on any violation; callers should map
    to a 422 RULE_VIOLATION envelope per spec §5.15.
    """
    mpath = Path(manifest_path)
    entries = _parse_manifest(mpath)
    packs = tuple(_load_one_pack(mpath.parent, e) for e in entries)
    index = _index_rules(packs)
    return PackRegistry(
        packs=packs, manifest_path=mpath, rules_by_id=index,
    )


_default_registry: PackRegistry | None = None


def get_default_registry() -> PackRegistry:
    """Return the process-wide registry loaded from packs/manifest.json.

    Cached so the checksum verification runs exactly once per process.
    Tests that want a fresh load should call load_registry directly.
    """
    global _default_registry
    if _default_registry is None:
        # Project layout: packs/ sits at the repo root, one level above
        # the forge package.  Resolve it from this file's location so
        # the CLI / worker / tests all agree on the same directory.
        root = Path(__file__).resolve().parents[2]
        _default_registry = load_registry(root / "packs" / "manifest.json")
    return _default_registry


def reset_default_registry() -> None:
    """Drop the cached registry.  Used by tests that mutate pack files."""
    global _default_registry
    _default_registry = None
