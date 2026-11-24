"""add durable idempotency and payment recovery ledgers

Revision ID: 3c90a7b41d62
Revises: 608478858234
Create Date: 2026-11-23

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3c90a7b41d62"
down_revision: Union[str, Sequence[str], None] = "608478858234"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user", sa.String(length=320), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("product_name", sa.String(length=120), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("payment_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("inventory_reserved", sa.Boolean(), nullable=False),
        sa.Column("payment_status", sa.String(length=24), nullable=False),
        sa.Column("payment_response", sa.JSON(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_idempotency_order_id"),
        sa.UniqueConstraint("payment_idempotency_key", name="uq_idempotency_payment_key"),
        sa.UniqueConstraint("user", "idempotency_key", name="uq_idempotency_user_key"),
    )
    op.create_index(
        "ix_idempotency_status_lease", "idempotency_records", ["status", "lease_until"], unique=False
    )
    op.create_table(
        "payment_ledger",
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("payment_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("simulation", sa.Boolean(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("payment_id"),
        sa.UniqueConstraint("order_id", name="uq_payment_ledger_order_id"),
        sa.UniqueConstraint("payment_idempotency_key", name="uq_payment_idempotency_key"),
    )
    op.create_index("ix_payment_ledger_order_id", "payment_ledger", ["order_id"], unique=False)
    op.create_table(
        "inventory_reservations",
        sa.Column("reservation_key", sa.String(length=128), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["catalog_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("reservation_key"),
    )
    op.create_index(
        "ix_inventory_reservations_product_id", "inventory_reservations", ["product_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_reservations_product_id", table_name="inventory_reservations")
    op.drop_table("inventory_reservations")
    op.drop_index("ix_payment_ledger_order_id", table_name="payment_ledger")
    op.drop_table("payment_ledger")
    op.drop_index("ix_idempotency_status_lease", table_name="idempotency_records")
    op.drop_table("idempotency_records")
