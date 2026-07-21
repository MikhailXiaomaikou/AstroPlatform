"""Merge Foundry activation and materialization migration branches.

Revision ID: e293a4b5c6d7
Revises: c1708293a4b5, d18293a4b5c6
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union


revision: str = "e293a4b5c6d7"
down_revision: Union[str, tuple[str, str], None] = (
    "c1708293a4b5",
    "d18293a4b5c6",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
