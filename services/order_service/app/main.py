import os
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
import jwt
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from jwt.exceptions import InvalidTokenError

from shared.database import get_db, initialize_sqlite_tables
from services.order_service.app.models import IdempotencyRecord, Order
from services.order_service.app.transactions import (
    _in_progress_response,
    _stored_response,
    canonical_request_hash,
    claim_idempotency_record,
    process_transaction,
    validate_idempotency_key,
)

app = FastAPI(title="Order Service", version="0.5.0")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me-please-use-a-strong-secret")
initialize_sqlite_tables([Order.__table__, IdempotencyRecord.__table__])


class OrderCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=100)


def require_user(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        claims = jwt.decode(authorization.split(" ", 1)[1], JWT_SECRET, algorithms=["HS256"])
        return claims["sub"]
    except (InvalidTokenError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def serialize_order(order: Order) -> dict:
    return {
        "order_id": order.order_id,
        "user": order.user,
        "product_id": order.product_id,
        "product_name": order.product_name,
        "quantity": order.quantity,
        "total_amount": float(order.total_amount),
        "status": order.status,
        "payment": order.payment,
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "order-service"}


@app.post("/orders", status_code=201)
async def create_order(
    payload: OrderCreate,
    user: str = Depends(require_user),
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    key = validate_idempotency_key(idempotency_key)
    request_hash = canonical_request_hash(payload.product_id, payload.quantity)
    record, acquired = claim_idempotency_record(user, key, request_hash, payload.product_id, payload.quantity)
    if not acquired:
        if record["status"] in {"SUCCEEDED", "FAILED"}:
            return _stored_response(record)
        return _in_progress_response(record)
    return await process_transaction(record["id"])


@app.get("/orders")
def list_orders(user: str = Depends(require_user), db: Session = Depends(get_db)):
    items = db.scalars(select(Order).where(Order.user == user).order_by(Order.created_at)).all()
    return [serialize_order(item) for item in items]


@app.get("/orders/{order_id}")
def get_order(order_id: str, user: str = Depends(require_user), db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order or order.user != user:
        raise HTTPException(status_code=404, detail="Order not found")
    return serialize_order(order)
