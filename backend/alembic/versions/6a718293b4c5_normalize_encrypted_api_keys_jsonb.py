"""Normalize encrypted user API keys to PostgreSQL JSONB.

Revision ID: 6a718293b4c5
Revises: 5f60718293a4
Create Date: 2026-07-11

Some legacy databases already have ``users.api_keys`` as JSONB, while a later
model revision represented the encrypted value as TEXT.  The mismatch makes
asyncpg bind ciphertext as VARCHAR and breaks user registration/settings on
the JSONB installations.  Preserve encrypted tokens and plaintext legacy JSON
as JSONB scalar strings so the application can read and re-encrypt both.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "6a718293b4c5"
down_revision: Union[str, None] = "5f60718293a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _api_keys_type() -> sa.types.TypeEngine | None:
    inspector = sa.inspect(op.get_bind())
    if "users" not in inspector.get_table_names():
        return None
    for column in inspector.get_columns("users"):
        if column["name"] == "api_keys":
            return column["type"]
    return None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    column_type = _api_keys_type()
    if column_type is None or isinstance(column_type, postgresql.JSONB):
        return
    if not isinstance(column_type, sa.String):
        raise RuntimeError(
            "Refusing to convert users.api_keys from unsupported type "
            f"{column_type!r}"
        )

    op.execute(
        sa.text(
            """
            ALTER TABLE users
            ALTER COLUMN api_keys TYPE JSONB
            USING CASE
                WHEN api_keys IS NULL THEN NULL
                ELSE to_jsonb(api_keys)
            END
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    column_type = _api_keys_type()
    if column_type is None or isinstance(column_type, sa.String):
        return
    if not isinstance(column_type, postgresql.JSONB):
        raise RuntimeError(
            "Refusing to convert users.api_keys from unsupported type "
            f"{column_type!r}"
        )

    op.execute(
        sa.text(
            """
            ALTER TABLE users
            ALTER COLUMN api_keys TYPE TEXT
            USING CASE
                WHEN api_keys IS NULL THEN NULL
                WHEN jsonb_typeof(api_keys) = 'string' THEN api_keys #>> '{}'
                ELSE api_keys::text
            END
            """
        )
    )
