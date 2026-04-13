#!/usr/bin/env python3
"""
Module:   Program Model
Purpose:  Program entity — top-level access-scoping boundary for all data.

BoundState AI 2026
Version: 0.1.0
Classification: BoundState Proprietary

Co-Authored-By: Claude "Beth" <noreply@anthropic.com>
Co-Authored-By: Dr. Joseph Wilde, BoundState AI

Compliance: NASA-CSET, SWE-013
Safety Critical: True

Why this design:
  Programs scope all data access (spec §5.0.3).  A user's
  custom:programs JWT claim determines which programs' data they can
  see.  Program filtering is enforced at the service layer, not routes.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import AuditableMixin, Base


class Program(AuditableMixin, Base):
    """Program entity — e.g. 'RS25', 'SLS-ADAPTER'."""

    __tablename__ = "programs"

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
