"""Remember which stock item each order line took its quantity from.

A rejected order gives that quantity back. Until now an order line did not record its stock
item (the order form can pick one directly, and a product's stock link can change later), so
nothing could be returned. One new optional column, empty on existing rows; old code ignores it.
Skipped where the column already exists. A 10 s lock timeout makes it give up rather than queue
behind live traffic.

Revision ID: 047_invoice_item_stock
Revises: 046_schema_drift_cleanup
Create Date: 2026-11-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "047_invoice_item_stock"
down_revision: Union[str, None] = "046_schema_drift_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_NAME = "invoice_items_stock_item_id_fkey"


def _has_column(bind) -> bool:
    return "stock_item_id" in {c["name"] for c in sa.inspect(bind).get_columns("invoice_items")}


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        return
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    with op.batch_alter_table("invoice_items") as batch:
        batch.add_column(sa.Column("stock_item_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(FK_NAME, "stock_items", ["stock_item_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    with op.batch_alter_table("invoice_items") as batch:
        batch.drop_constraint(FK_NAME, type_="foreignkey")
        batch.drop_column("stock_item_id")
