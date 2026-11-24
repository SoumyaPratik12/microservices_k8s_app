import hashlib
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
import jwt
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from shared.database import SessionLocal, initialize_sqlite_tables
from jwt.exceptions import InvalidTokenError
from services.payment_service.app.models import Payment

app = FastAPI(title="Payment Service", version="0.5.0")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me-please-use-a-strong-secret")
SIMULATED_PAYMENT_OUTCOME = os.getenv("PAYMENT_SIMULATED_OUTCOME", "succeeded").lower()
if SIMULATED_PAYMENT_OUTCOME not in {"succeeded", "declined"}:
    raise ValueError("PAYMENT_SIMULATED_OUTCOME must be succeeded or declined")
initialize_sqlite_tables([Payment.__table__])


class PaymentRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=36)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)


def require_order_service(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Order Service authorization required")
    try:
        claims = jwt.decode(
            authorization.split(" ", 1)[1],
            JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
        if "aud" in claims:
            audiences = claims["aud"]
            if isinstance(audiences, str):
                audiences = [audiences]
            if not isinstance(audiences, list) or "payment-service" not in audiences:
                raise InvalidTokenError("Invalid audience")
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid Order Service token") from exc
    if claims.get("service") != "order-service":
        raise HTTPException(status_code=403, detail="Order Service authorization required")


def validate_idempotency_key(key: str | None) -> str:
    if not key or len(key) > 128 or not re.fullmatch(r"[A-Za-z0-9._:-]+", key):
        raise HTTPException(status_code=400, detail="A valid Idempotency-Key is required")
    return key


def payment_request_hash(payload: PaymentRequest) -> str:
    canonical = json.dumps(
        {
            "order_id": payload.order_id,
            "amount": format(payload.amount.quantize(Decimal("0.01")), ".2f"),
            "currency": payload.currency.upper(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def serialize_payment(payment: Payment) -> dict:
    return payment.response_json


@app.get("/health")
def health():
    return {"status": "ok", "service": "payment-service"}


@app.post("/payments")
def process_payment(
    payload: PaymentRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: str | None = Header(default=None),
):
    require_order_service(authorization)
    key = validate_idempotency_key(idempotency_key)
    request_hash = payment_request_hash(payload)
    with SessionLocal() as db:
        existing = db.scalar(select(Payment).where(Payment.payment_idempotency_key == key))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status_code=409, detail="Idempotency-Key was already used for another payment"
                )
            status_code = 201 if existing.status == "succeeded" else 402
            return JSONResponse(status_code=status_code, content=serialize_payment(existing))

        now = datetime.now(timezone.utc)
        response_json = {
            "payment_id": str(uuid4()),
            "order_id": payload.order_id,
            "amount": float(payload.amount.quantize(Decimal("0.01"))),
            "currency": payload.currency.upper(),
            "status": SIMULATED_PAYMENT_OUTCOME,
            "processed_at": now.isoformat(),
            "simulation": True,
        }
        payment = Payment(
            payment_id=response_json["payment_id"],
            order_id=payload.order_id,
            payment_idempotency_key=key,
            request_hash=request_hash,
            amount=payload.amount,
            currency=payload.currency.upper(),
            status=SIMULATED_PAYMENT_OUTCOME,
            simulation=True,
            response_json=response_json,
        )
        db.add(payment)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(select(Payment).where(Payment.payment_idempotency_key == key))
            if existing is None:
                existing = db.scalar(select(Payment).where(Payment.order_id == payload.order_id))
                if existing is not None:
                    raise HTTPException(status_code=409, detail="Order already has a payment")
                raise HTTPException(status_code=503, detail="Payment outcome could not be resolved")
            if existing.request_hash != request_hash:
                raise HTTPException(
                    status_code=409, detail="Idempotency-Key was already used for another payment"
                )
            response_json = serialize_payment(existing)

    status_code = 201 if response_json["status"] == "succeeded" else 402
    return JSONResponse(status_code=status_code, content=response_json)


@app.get("/payments/by-idempotency-key/{idempotency_key}")
def get_payment_by_idempotency_key(
    idempotency_key: str,
    authorization: str | None = Header(default=None),
):
    require_order_service(authorization)
    key = validate_idempotency_key(idempotency_key)
    with SessionLocal() as db:
        payment = db.scalar(select(Payment).where(Payment.payment_idempotency_key == key))
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        return serialize_payment(payment)
