"""Create the initial Auth, Product and Order schema.

Revision ID: 608478858234
Revises:
Create Date: 2026-11-24
"""

from alembic import op
import sqlalchemy as sa


revision = "608478858234"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("salt", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("email"),
    )
    op.create_table(
        "catalog_products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "customer_orders",
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("user", sa.String(length=320), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("product_name", sa.String(length=120), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payment", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_index("ix_customer_orders_user", "customer_orders", ["user"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_customer_orders_user", table_name="customer_orders")
    op.drop_table("customer_orders")
    op.drop_table("catalog_products")
    op.drop_table("auth_users")
