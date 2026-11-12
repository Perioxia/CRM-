"""Twilio softphone settings and call timestamps.

Revision ID: 045_twilio_voice
Revises: 044_invite_hashed_password
Create Date: 2026-11-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "045_twilio_voice"
down_revision: Union[str, None] = "044_invite_hashed_password"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    settings_cols = _cols("company_settings")
    with op.batch_alter_table("company_settings") as batch:
        if "twilio_account_sid" not in settings_cols:
            batch.add_column(sa.Column("twilio_account_sid", sa.String(length=64), nullable=True))
        if "twilio_auth_token_encrypted" not in settings_cols:
            batch.add_column(sa.Column("twilio_auth_token_encrypted", sa.Text(), nullable=True))
        if "twilio_api_key_sid" not in settings_cols:
            batch.add_column(sa.Column("twilio_api_key_sid", sa.String(length=64), nullable=True))
        if "twilio_api_key_secret_encrypted" not in settings_cols:
            batch.add_column(sa.Column("twilio_api_key_secret_encrypted", sa.Text(), nullable=True))
        if "twilio_twiml_app_sid" not in settings_cols:
            batch.add_column(sa.Column("twilio_twiml_app_sid", sa.String(length=64), nullable=True))
        if "twilio_phone_number" not in settings_cols:
            batch.add_column(sa.Column("twilio_phone_number", sa.String(length=20), nullable=True))
        if "twilio_default_user_id" not in settings_cols:
            batch.add_column(sa.Column("twilio_default_user_id", sa.Integer(), nullable=True))
        if "twilio_ring_mobile_default" not in settings_cols:
            batch.add_column(sa.Column("twilio_ring_mobile_default", sa.Integer(), nullable=True, server_default="1"))

    user_cols = _cols("users")
    with op.batch_alter_table("users") as batch:
        if "twilio_phone_number" not in user_cols:
            batch.add_column(sa.Column("twilio_phone_number", sa.String(length=20), nullable=True))
        if "twilio_ring_mobile" not in user_cols:
            batch.add_column(sa.Column("twilio_ring_mobile", sa.Integer(), nullable=True))

    call_cols = _cols("call_logs")
    with op.batch_alter_table("call_logs") as batch:
        if "started_at" not in call_cols:
            batch.add_column(sa.Column("started_at", sa.DateTime(), nullable=True))
        if "ended_at" not in call_cols:
            batch.add_column(sa.Column("ended_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    settings_cols = _cols("company_settings")
    with op.batch_alter_table("company_settings") as batch:
        for name in (
            "twilio_ring_mobile_default",
            "twilio_default_user_id",
            "twilio_phone_number",
            "twilio_twiml_app_sid",
            "twilio_api_key_secret_encrypted",
            "twilio_api_key_sid",
            "twilio_auth_token_encrypted",
            "twilio_account_sid",
        ):
            if name in settings_cols:
                batch.drop_column(name)

    user_cols = _cols("users")
    with op.batch_alter_table("users") as batch:
        if "twilio_ring_mobile" in user_cols:
            batch.drop_column("twilio_ring_mobile")
        if "twilio_phone_number" in user_cols:
            batch.drop_column("twilio_phone_number")

    call_cols = _cols("call_logs")
    with op.batch_alter_table("call_logs") as batch:
        if "ended_at" in call_cols:
            batch.drop_column("ended_at")
        if "started_at" in call_cols:
            batch.drop_column("started_at")
