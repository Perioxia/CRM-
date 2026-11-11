"""Bring the database in line with the models.

Production and a database built from the migrations had drifted apart in different ways
(production picked up some tables and columns from the old create-what-is-missing helper).
This brings both to the same end state, the models:
- adds the indexes the models declare;
- makes six columns NOT NULL (empty values get the model default first);
- adds missing foreign keys as NOT VALID: new and changed rows are checked, existing rows
  are left exactly as they are;
- turns two unique indexes into the unique constraints the models declare;
- stores ledger_entries.created_at with its time zone where it was stored without.
Every step is skipped where the database already has it. Small tables only; a 10 s lock
timeout makes the whole migration give up rather than queue behind live traffic.

Revision ID: 046_schema_drift_cleanup
Revises: 045_twilio_voice
Create Date: 2026-11-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "046_schema_drift_cleanup"
down_revision: Union[str, None] = "045_twilio_voice"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [  # name, table, column, unique
    ("ix_ai_conversations_id", "ai_conversations", "id", False),
    ("ix_clients_account_id", "clients", "account_id", False),
    ("ix_companies_sandbox_parent_id", "companies", "sandbox_parent_id", False),
    ("ix_companies_status", "companies", "status", False),
    ("ix_company_settings_booking_slug", "company_settings", "booking_slug", False),
    ("ix_invoices_share_token_hash", "invoices", "share_token_hash", True),
    ("ix_leads_deleted_at", "leads", "deleted_at", False),
    ("ix_notifications_id", "notifications", "id", False),
    ("ix_otp_codes_id", "otp_codes", "id", False),
    ("ix_products_kind", "products", "kind", False),
    ("ix_quotes_plan_id", "quotes", "plan_id", False),
    ("ix_tasks_deal_id", "tasks", "deal_id", False),
    ("ix_team_memberships_company_id", "team_memberships", "company_id", False),
    ("ix_team_memberships_id", "team_memberships", "id", False),
    ("ix_teams_name", "teams", "name", False),
    ("ix_users_company_id", "users", "company_id", False),
]
NOT_NULL = [  # table, column, default
    ("companies", "is_sandbox", "false"),
    ("companies", "require_2fa", "false"),
    ("documents", "file_size", "0"),
    ("products", "kind", "'goods'"),
    ("users", "token_version", "0"),
    ("users", "totp_enabled", "false"),
]
FOREIGN_KEYS = [  # name, table, column, referred table, ON DELETE
    ("fk_clients_account_id", "clients", "account_id", "accounts", "SET NULL"),
    ("fk_companies_sandbox_parent_id", "companies", "sandbox_parent_id", "companies", "SET NULL"),
    ("fk_company_settings_booking_host_user_id", "company_settings", "booking_host_user_id", "users", "SET NULL"),
    ("fk_company_settings_report_schedule_saved_report_id", "company_settings", "report_schedule_saved_report_id", "saved_reports", "SET NULL"),
    ("fk_company_settings_twilio_default_user_id", "company_settings", "twilio_default_user_id", "users", "SET NULL"),
    ("fk_invoice_items_product_id", "invoice_items", "product_id", "products", "SET NULL"),
    ("fk_ledger_entries_updated_by", "ledger_entries", "updated_by", "users", "NO ACTION"),
    ("fk_quotes_plan_id", "quotes", "plan_id", "quote_plans", "SET NULL"),
    ("fk_tasks_deal_id", "tasks", "deal_id", "deals", "SET NULL"),
]
UNIQUE = [  # table, constraint name, existing unique index to reuse, columns
    ("invites", "invites_token_key", "ix_invites_token", ["token"]),
    ("teams", "ix_teams_company_name", "ix_teams_company_name", ["company_id", "name"]),
]


def _duplicates(bind, table: str, columns: list[str]) -> int:
    cols = ", ".join(columns)
    not_null = " AND ".join(f"{c} IS NOT NULL" for c in columns)
    return bind.execute(sa.text(
        f"SELECT count(*) FROM (SELECT {cols} FROM {table} WHERE {not_null} GROUP BY {cols} HAVING count(*) > 1) d"
    )).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    inspector = sa.inspect(bind)

    checks = [("invoices", ["share_token_hash"])] + [(table, columns) for table, _, _, columns in UNIQUE]
    for table, columns in checks:
        if found := _duplicates(bind, table, columns):
            raise RuntimeError(f"{found} {table}({', '.join(columns)}) values are duplicated; resolve them before migration 046")

    for name, table, column, unique in INDEXES:
        bind.execute(sa.text(f"CREATE {'UNIQUE ' if unique else ''}INDEX IF NOT EXISTS {name} ON {table} ({column})"))

    for table, column, default in NOT_NULL:
        bind.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {default}"))
        bind.execute(sa.text(f"UPDATE {table} SET {column} = {default} WHERE {column} IS NULL"))
        bind.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL"))

    for name, table, column, referred, on_delete in FOREIGN_KEYS:
        if any(fk["constrained_columns"] == [column] and fk["referred_table"] == referred for fk in inspector.get_foreign_keys(table)):
            continue
        bind.execute(sa.text(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY ({column}) "
            f"REFERENCES {referred} (id) ON DELETE {on_delete} NOT VALID"
        ))

    for table, name, index, columns in UNIQUE:
        if any(sorted(uc["column_names"]) == sorted(columns) for uc in inspector.get_unique_constraints(table)):
            continue
        if any(ix["name"] == index and ix["unique"] for ix in inspector.get_indexes(table)):
            bind.execute(sa.text(f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE USING INDEX {index}"))
        else:
            bind.execute(sa.text(f"ALTER TABLE {table} ADD CONSTRAINT {name} UNIQUE ({', '.join(columns)})"))

    created_at = next(c for c in inspector.get_columns("ledger_entries") if c["name"] == "created_at")
    if not getattr(created_at["type"], "timezone", False):
        bind.execute(sa.text(
            "ALTER TABLE ledger_entries ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE USING created_at AT TIME ZONE 'UTC'"
        ))


def downgrade() -> None:
    """Removes the foreign keys and NOT NULL rules this migration added; indexes, constraints,
    column defaults and the created_at type stay."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for name, table, _column, _referred, _on_delete in FOREIGN_KEYS:
        bind.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"))
    for table, column, _default in NOT_NULL:
        bind.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"))
