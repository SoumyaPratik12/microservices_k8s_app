from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from shared.database import Base


class Order(Base):
    __tablename__ = "customer_orders"

    order_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_name: Mapped[str] = mapped_column(String(120), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payment: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("user", "idempotency_key", name="uq_idempotency_user_key"),
        UniqueConstraint("order_id", name="uq_idempotency_order_id"),
        UniqueConstraint("payment_idempotency_key", name="uq_idempotency_payment_key"),
        Index("ix_idempotency_status_lease", "status", "lease_until"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user: Mapped[str] = mapped_column(String(320), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    product_name: Mapped[str | None] = mapped_column(String(120))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payment_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    inventory_reserved: Mapped[bool] = mapped_column(default=False, nullable=False)
    payment_status: Mapped[str] = mapped_column(String(24), default="NOT_STARTED", nullable=False)
    payment_response: Mapped[dict | None] = mapped_column(JSON)
    response_json: Mapped[dict | None] = mapped_column(JSON)
    response_status_code: Mapped[int | None] = mapped_column(Integer)
    error_details: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
