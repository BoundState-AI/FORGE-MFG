#!/usr/bin/env python3
"""
Module:   Audit Replay CLI
Purpose:  Operator-facing tool for windowed audit-chain verification.
          The daily integrity job (spec §7.3) reports failures by
          sequence_number; this CLI lets the operator inspect a window
          around the failure to identify exactly which fields diverge.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013, SWE-032
Safety Critical: True

Why a CLI in addition to GET /api/audit/replay:
  The audit replay surface is investigative, not user-facing.  An
  operator working from a daily-job alert wants to run a quick local
  query without spinning up an HTTP client.  The CLI talks to the
  database directly via the same verifier (forge.audit.integrity.
  verify_audit_chain_range), so there is no HTTP-vs-direct skew —
  identical inputs produce identical outputs through both surfaces.

  The CLI deliberately mirrors the route's parameter contract so an
  operator can copy a failing request from the API and reproduce it
  locally with the same flags.

Usage:
  python -m scripts.audit_replay --from-sequence 4990 --to-sequence 5010
  python -m scripts.audit_replay --from-timestamp 2026-04-15T00:00:00Z \\
                                  --to-timestamp 2026-04-15T23:59:59Z
  python -m scripts.audit_replay --from-sequence 1 --to-sequence 100 --json

Exit codes:
  0 — chain segment verified clean
  1 — chain segment has integrity failures
  2 — invalid arguments (REPLAY_*)
  3 — unexpected runtime error (DB unreachable, etc.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from forge.audit.integrity import (
    REPLAY_LIMIT_MAX,
    ReplayParamError,
    ReplayResult,
    verify_audit_chain_range,
)


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp from the CLI.

    Accepts the usual ``2026-04-15T00:00:00Z`` form (with or without
    timezone) — argparse type-converts call this with the raw string.
    Raising ValueError surfaces argparse's own message; we don't try
    to be cleverer than the stdlib here.
    """
    # ``Z`` is a common ISO 8601 timezone marker; fromisoformat in 3.11+
    # accepts it natively, but we normalize for older handlers / clarity.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit_replay",
        description=(
            "Verify a bounded segment of the audit chain.  Specify "
            "either a sequence range OR a timestamp range, not both."
        ),
    )
    parser.add_argument(
        "--from-sequence", type=int, default=None,
        help="Starting sequence_number (inclusive, 1-indexed).",
    )
    parser.add_argument(
        "--to-sequence", type=int, default=None,
        help="Ending sequence_number (inclusive).",
    )
    parser.add_argument(
        "--from-timestamp", type=_parse_timestamp, default=None,
        help="Starting timestamp (ISO 8601, e.g. 2026-04-15T00:00:00Z).",
    )
    parser.add_argument(
        "--to-timestamp", type=_parse_timestamp, default=None,
        help="Ending timestamp (ISO 8601).",
    )
    parser.add_argument(
        "--limit", type=int, default=REPLAY_LIMIT_MAX,
        help=f"Max records returned (1..{REPLAY_LIMIT_MAX}).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of the human table.",
    )
    parser.add_argument(
        "--database-url", default=None,
        help=(
            "Override the Postgres URL (otherwise reads "
            "FORGE_DATABASE_URL or falls back to the local default)."
        ),
    )
    return parser


def _format_human(result: ReplayResult) -> str:
    """Render a tabular human summary suitable for terminal viewing.

    Why a hand-rolled table instead of a third-party formatter: the CLI
    is shipped with the slice; we don't want a runtime dep just for
    pretty output.  The columns are chosen for diagnostic value at a
    glance — sequence_number, action_type, verified flag, and a hash
    snippet so the operator sees the divergence without scrolling.
    """
    header = (
        f"Replay verified: {result.records_checked} record(s)  "
        f"status={'OK' if result.valid else 'FAILING'}  "
        f"truncated={result.truncated}\n"
        f"Anchor: sequence_number={result.range_anchor_sequence} "
        f"hash={result.range_anchor_hash[:16]}...\n"
    )
    if not result.records:
        return header + "(no records in window)\n"

    rows = ["seq    | verified | action_type                | record_hash...."]
    rows.append("-------+----------+----------------------------+----------------")
    for r in result.records:
        rows.append(
            f"{r.sequence_number:6d} | "
            f"{'Y' if r.verified else 'N':^8s} | "
            f"{r.action_type[:26]:<26s} | "
            f"{r.record_hash[:16]}..."
        )
    body = "\n".join(rows) + "\n"

    if result.failures:
        body += "\nFAILURES:\n"
        for f in result.failures:
            body += (
                f"  seq={f.sequence_number} field={f.field}\n"
                f"    expected={f.expected[:32]}...\n"
                f"    actual=  {f.actual[:32]}...\n"
                f"    reason:  {f.reason}\n"
            )
    return header + body


def _format_json(result: ReplayResult) -> str:
    """Emit the full ReplayResult as JSON for automated callers.

    asdict on the dataclass would naively serialize uuid/datetime types
    via str(); we take that for the CLI surface (it matches what the
    API serializer would produce for the same field types).
    """
    payload: dict[str, Any] = asdict(result)
    return json.dumps(payload, default=str, indent=2)


async def _run(args: argparse.Namespace) -> int:
    """Open a session, call the verifier, format, return exit code."""
    db_url = (
        args.database_url
        or os.getenv("FORGE_DATABASE_URL")
        or "postgresql+asyncpg://forge:forge@localhost:5432/forge"
    )
    engine = create_async_engine(db_url)
    try:
        async with AsyncSession(engine) as session:
            try:
                result = await verify_audit_chain_range(
                    session,
                    from_sequence=args.from_sequence,
                    to_sequence=args.to_sequence,
                    from_timestamp=args.from_timestamp,
                    to_timestamp=args.to_timestamp,
                    limit=args.limit,
                )
            except ReplayParamError as exc:
                # Print the spec error code to stderr so an automation
                # caller can branch on it; argparse-style exit code 2.
                print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
                return 2
        output = _format_json(result) if args.json else _format_human(result)
        print(output)
        return 0 if result.valid else 1
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — kept synchronous so the script is `pythonic`.

    `asyncio.run` owns the loop lifecycle; we don't share it with
    anything else from the CLI.  Exit codes are returned (not raised
    via SystemExit here) so unit tests can drive `main` directly.
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except Exception as exc:  # top-level CLI guard
        print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
