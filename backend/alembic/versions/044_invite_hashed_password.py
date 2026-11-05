"""Add hashed_password to invites.

The Invite model stores the temporary password hash issued with an invite, but
no migration created the column, so every query on invites failed on databases
built from migrations. Databases created from the models already have it, so
the column is only added where it is missing.

Revision ID: 044_invite_hashed_password
Revises: 043_company_id_sequence
Create Date: 2026-11-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "044_invite_hashed_password"
down_revision: Union[str, None] = "043_company_id_sequence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column() -> bool:
    columns = sa.inspect(op.get_bind()).get_columns("invites")
    return any(c["name"] == "hashed_password" for c in columns)


def upgrade() -> None:
    if not _has_column():
        op.add_column("invites", sa.Column("hashed_password", sa.String(length=255), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column("invites", "hashed_password")
