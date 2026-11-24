from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, JSON, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from shared.database import Base


class Payment(Base):
    __tablename__ = "payment_ledger"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_payment_ledger_order_id"),
        UniqueConstraint("payment_idempotency_key", name="uq_payment_idempotency_key"),
    )

    payment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    payment_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    simulation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
