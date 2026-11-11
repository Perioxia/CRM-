"""Bring the database in line with the models.

Adds the indexes the models declare, makes five flag/counter columns NOT NULL (empty
values get the model default first), and adds seven foreign keys with ON DELETE SET NULL
as NOT VALID: new and changed rows are checked, existing rows are left exactly as they are.
Every step is skipped where the database already has it, so databases built from the
models and from the migrations both end up the same. Small tables only; a 10 s lock
timeout makes the whole migration give up rather than queue behind live traffic.

Revision ID: 045_schema_drift_cleanup
Revises: 044_invite_hashed_password
Create Date: 2026-11-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "045_schema_drift_cleanup"
down_revision: Union[str, None] = "044_invite_hashed_password"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [  # name, table, column, unique
    ("ix_clients_account_id", "clients", "account_id", False),
    ("ix_companies_sandbox_parent_id", "companies", "sandbox_parent_id", False),
    ("ix_company_settings_booking_slug", "company_settings", "booking_slug", False),
    ("ix_invoices_share_token_hash", "invoices", "share_token_hash", True),
    ("ix_leads_deleted_at", "leads", "deleted_at", False),
    ("ix_tasks_deal_id", "tasks", "deal_id", False),
    ("ix_team_memberships_company_id", "team_memberships", "company_id", False),
    ("ix_teams_name", "teams", "name", False),
]
NOT_NULL = [  # table, column, default
    ("companies", "is_sandbox", "false"),
    ("companies", "require_2fa", "false"),
    ("documents", "file_size", "0"),
    ("users", "token_version", "0"),
    ("users", "totp_enabled", "false"),
]
FOREIGN_KEYS = [  # name, table, column, referred table
    ("fk_clients_account_id", "clients", "account_id", "accounts"),
    ("fk_companies_sandbox_parent_id", "companies", "sandbox_parent_id", "companies"),
    ("fk_company_settings_booking_host_user_id", "company_settings", "booking_host_user_id", "users"),
    ("fk_company_settings_report_schedule_saved_report_id", "company_settings", "report_schedule_saved_report_id", "saved_reports"),
    ("fk_invoice_items_product_id", "invoice_items", "product_id", "products"),
    ("fk_ledger_entries_updated_by", "ledger_entries", "updated_by", "users"),
    ("fk_tasks_deal_id", "tasks", "deal_id", "deals"),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))

    duplicates = bind.execute(sa.text(
        "SELECT count(*) FROM (SELECT share_token_hash FROM invoices "
        "WHERE share_token_hash IS NOT NULL GROUP BY 1 HAVING count(*) > 1) d"
    )).scalar()
    if duplicates:
        raise RuntimeError(f"{duplicates} invoices.share_token_hash values are duplicated; resolve them before migration 045")

    for name, table, column, unique in INDEXES:
        bind.execute(sa.text(f"CREATE {'UNIQUE ' if unique else ''}INDEX IF NOT EXISTS {name} ON {table} ({column})"))

    for table, column, default in NOT_NULL:
        bind.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {default}"))
        bind.execute(sa.text(f"UPDATE {table} SET {column} = {default} WHERE {column} IS NULL"))
        bind.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL"))

    inspector = sa.inspect(bind)
    for name, table, column, referred in FOREIGN_KEYS:
        existing = inspector.get_foreign_keys(table)
        if any(fk["constrained_columns"] == [column] and fk["referred_table"] == referred for fk in existing):
            continue
        bind.execute(sa.text(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY ({column}) "
            f"REFERENCES {referred} (id) ON DELETE SET NULL NOT VALID"
        ))


def downgrade() -> None:
    """Removes the foreign keys and NOT NULL rules this migration added; indexes and column defaults stay."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for name, table, _column, _referred in FOREIGN_KEYS:
        bind.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))
    for table, column, _default in NOT_NULL:
        bind.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"))
