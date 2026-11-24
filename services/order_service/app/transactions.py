import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse
import jwt
from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from typing import cast

from shared.database import SessionLocal
from services.order_service.app.models import IdempotencyRecord, Order

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me-please-use-a-strong-secret")
PRODUCT_SERVICE_URL = os.getenv("PRODUCT_SERVICE_URL", "http://127.0.0.1:8002")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://127.0.0.1:8003")
LEASE_SECONDS = 30
MAX_ATTEMPTS = 5
UNFINISHED_STATUSES = (
    "PROCESSING",
    "INVENTORY_RESERVED",
    "PAYMENT_PENDING",
    "PAYMENT_SUCCEEDED",
    "REQUIRES_RECONCILIATION",
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def canonical_request_hash(product_id: int, quantity: int) -> str:
    body = json.dumps(
        {"product_id": product_id, "quantity": quantity},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


def validate_idempotency_key(key: str | None) -> str:
    if (
        not key
        or len(key) > 128
        or not all(char.isascii() and (char.isalnum() or char in "._:-") for char in key)
    ):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must be 1-128 ASCII letters, digits, or . _ : - characters",
        )
    return key


def _snapshot(record: IdempotencyRecord) -> dict:
    return {
        "id": record.id,
        "user": record.user,
        "idempotency_key": record.idempotency_key,
        "request_hash": record.request_hash,
        "status": record.status,
        "product_id": record.product_id,
        "product_name": record.product_name,
        "quantity": record.quantity,
        "total_amount": record.total_amount,
        "order_id": record.order_id,
        "payment_idempotency_key": record.payment_idempotency_key,
        "inventory_reserved": record.inventory_reserved,
        "payment_status": record.payment_status,
        "payment_response": record.payment_response,
        "response_json": record.response_json,
        "response_status_code": record.response_status_code,
        "attempt_count": record.attempt_count,
        "lease_until": record.lease_until,
    }


def _get_snapshot(record_id: int) -> dict | None:
    with SessionLocal() as db:
        record = db.get(IdempotencyRecord, record_id)
        return _snapshot(record) if record else None


def claim_idempotency_record(
    user: str, key: str, request_hash: str, product_id: int, quantity: int
) -> tuple[dict, bool]:
    now = now_utc()
    record = IdempotencyRecord(
        user=user,
        idempotency_key=key,
        request_hash=request_hash,
        status="PROCESSING",
        product_id=product_id,
        quantity=quantity,
        order_id=str(uuid4()),
        payment_idempotency_key=str(uuid4()),
        inventory_reserved=False,
        payment_status="NOT_STARTED",
        attempt_count=1,
        lease_until=now + timedelta(seconds=LEASE_SECONDS),
    )
    with SessionLocal() as db:
        db.add(record)
        try:
            db.commit()
            return _snapshot(record), True
        except IntegrityError:
            db.rollback()

        existing_record = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user == user,
                IdempotencyRecord.idempotency_key == key,
            )
        )
        if existing_record is None:
            raise HTTPException(status_code=503, detail="Unable to claim idempotency key")
        if existing_record.request_hash != request_hash:
            raise HTTPException(
                status_code=409, detail="Idempotency-Key was already used with a different request"
            )
        if existing_record.status in {"SUCCEEDED", "FAILED"}:
            return _snapshot(existing_record), False

        acquired = cast(
            CursorResult,
            db.execute(
                update(IdempotencyRecord)
                .where(
                    IdempotencyRecord.id == existing_record.id,
                    IdempotencyRecord.status.in_(UNFINISHED_STATUSES),
                    or_(IdempotencyRecord.lease_until.is_(None), IdempotencyRecord.lease_until <= now),
                    IdempotencyRecord.attempt_count < MAX_ATTEMPTS,
                )
                .execution_options(synchronize_session=False)
                .values(
                    lease_until=now + timedelta(seconds=LEASE_SECONDS),
                    attempt_count=IdempotencyRecord.attempt_count + 1,
                    updated_at=now,
                )
            ),
        )
        db.commit()
        refreshed_record = db.get(IdempotencyRecord, existing_record.id)
        if refreshed_record is None:
            raise HTTPException(status_code=503, detail="Unable to reload idempotency record")
        return _snapshot(refreshed_record), acquired.rowcount == 1


def _update_record(record_id: int, **changes) -> dict:
    now = now_utc()
    with SessionLocal() as db:
        record = db.get(IdempotencyRecord, record_id)
        if record is None:
            raise RuntimeError("Idempotency record disappeared")
        for field, value in changes.items():
            setattr(record, field, value)
        record.updated_at = now
        if record.status in {"SUCCEEDED", "FAILED"}:
            record.lease_until = None
        else:
            record.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        db.commit()
        return _snapshot(record)


def _stored_response(record: dict) -> JSONResponse:
    return JSONResponse(
        status_code=record["response_status_code"] or 201,
        content=record["response_json"] or {},
    )


def _in_progress_response(record: dict) -> JSONResponse:
    status_code = 503 if record["status"] == "REQUIRES_RECONCILIATION" else 409
    return JSONResponse(
        status_code=status_code,
        content={"detail": "Order transaction is being processed", "status": record["status"]},
        headers={"Retry-After": "5"},
    )


def service_headers(audience: str) -> dict[str, str]:
    now = now_utc()
    token = jwt.encode(
        {
            "sub": "order-service",
            "service": "order-service",
            "aud": audience,
            "iat": now,
            "exp": now + timedelta(minutes=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _fail(record_id: int, status_code: int, detail: str, payment_status: str | None = None) -> JSONResponse:
    changes = {
        "status": "FAILED",
        "error_details": detail,
        "response_status_code": status_code,
        "response_json": {"detail": detail},
    }
    if payment_status:
        changes["payment_status"] = payment_status
    return _stored_response(_update_record(record_id, **changes))


def _requires_reconciliation(record_id: int, detail: str, payment_status: str | None = None) -> JSONResponse:
    changes = {
        "status": "REQUIRES_RECONCILIATION",
        "error_details": detail,
        "response_status_code": 503,
        "response_json": {"detail": "Order outcome is uncertain and requires reconciliation"},
    }
    if payment_status:
        changes["payment_status"] = payment_status
    record = _update_record(record_id, **changes)
    logger.warning(
        "order transaction requires reconciliation order_id=%s attempt=%s reason=%s",
        record["order_id"],
        record["attempt_count"],
        detail,
    )
    return _stored_response(record)


def _finalize_success(record_id: int, payment: dict) -> JSONResponse:
    with SessionLocal() as db:
        record = db.get(IdempotencyRecord, record_id)
        if record is None:
            raise RuntimeError("Idempotency record disappeared")
        if record.status == "SUCCEEDED" and record.response_json:
            return _stored_response(_snapshot(record))

        order = db.get(Order, record.order_id)
        if order is None:
            order = Order(
                order_id=record.order_id,
                user=record.user,
                product_id=record.product_id,
                product_name=record.product_name or "",
                quantity=record.quantity,
                total_amount=record.total_amount or Decimal("0.00"),
                status="confirmed",
                payment=payment,
            )
            db.add(order)

        response_json = {
            "order_id": record.order_id,
            "user": record.user,
            "product_id": record.product_id,
            "product_name": order.product_name,
            "quantity": record.quantity,
            "total_amount": float(record.total_amount or Decimal("0.00")),
            "status": "confirmed",
            "payment": payment,
        }
        record.status = "SUCCEEDED"
        record.payment_status = "SUCCEEDED"
        record.payment_response = payment
        record.response_json = response_json
        record.response_status_code = 201
        record.error_details = None
        record.updated_at = now_utc()
        record.lease_until = None
        db.commit()
        return JSONResponse(status_code=201, content=response_json)


async def _lookup_payment(client: httpx.AsyncClient, record: dict, headers: dict) -> dict | None:
    key = quote(record["payment_idempotency_key"], safe="")
    response = await client.get(f"{PAYMENT_SERVICE_URL}/payments/by-idempotency-key/{key}", headers=headers)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _valid_payment_response(payment: object, record: dict) -> bool:
    if not isinstance(payment, dict):
        return False
    try:
        amount = Decimal(str(payment["amount"]))
    except (KeyError, ArithmeticError, ValueError):
        return False
    return (
        payment.get("order_id") == record["order_id"]
        and amount == (record["total_amount"] or Decimal("0.00"))
        and payment.get("status") in {"succeeded", "declined"}
    )


async def _submit_or_recover_payment(client: httpx.AsyncClient, record: dict, headers: dict) -> dict | None:
    payload = {
        "order_id": record["order_id"],
        "amount": float(record["total_amount"]),
        "currency": "USD",
    }
    payment_headers = {**headers, "Idempotency-Key": record["payment_idempotency_key"]}
    try:
        existing = await _lookup_payment(client, record, headers)
    except httpx.RequestError:
        return None
    if existing is not None:
        return existing if _valid_payment_response(existing, record) else None

    try:
        submitted_response = await client.post(
            f"{PAYMENT_SERVICE_URL}/payments", json=payload, headers=payment_headers
        )
    except httpx.RequestError:
        try:
            existing = await _lookup_payment(client, record, headers)
        except httpx.RequestError:
            return None
        if existing is not None:
            return existing if _valid_payment_response(existing, record) else None
        response = None
    else:
        if submitted_response.status_code in {201, 402}:
            payment = submitted_response.json()
            return payment if _valid_payment_response(payment, record) else None
        try:
            existing = await _lookup_payment(client, record, headers)
        except httpx.RequestError:
            return None
        if existing is not None:
            return existing if _valid_payment_response(existing, record) else None

    # A missing lookup is not treated as failure. Repeating the same key is safe
    # because the Payment Service enforces its unique ledger constraint.
    try:
        response = await client.post(f"{PAYMENT_SERVICE_URL}/payments", json=payload, headers=payment_headers)
    except httpx.RequestError:
        try:
            existing = await _lookup_payment(client, record, headers)
        except httpx.RequestError:
            return None
        return existing if existing is not None and _valid_payment_response(existing, record) else None
    if response is None:
        return None
    if response.status_code in {201, 402}:
        payment = response.json()
        return payment if _valid_payment_response(payment, record) else None
    return None


async def _reserve_inventory(client: httpx.AsyncClient, record: dict) -> tuple[bool, int | None]:
    headers = {
        **service_headers("product-service"),
        "Idempotency-Key": record["order_id"],
    }
    payload = {"quantity": record["quantity"]}
    for _ in range(2):
        try:
            response = await client.post(
                f"{PRODUCT_SERVICE_URL}/products/{record['product_id']}/reserve",
                json=payload,
                headers=headers,
            )
        except httpx.RequestError:
            continue
        if response.status_code == 204:
            return True, None
        if response.status_code in {404, 409}:
            return False, response.status_code
        return False, None
    return False, None


async def _release_inventory(client: httpx.AsyncClient, record: dict) -> bool:
    headers = {
        **service_headers("product-service"),
        "Idempotency-Key": record["order_id"],
    }
    try:
        response = await client.post(
            f"{PRODUCT_SERVICE_URL}/products/{record['product_id']}/release",
            json={"quantity": record["quantity"]},
            headers=headers,
        )
    except httpx.RequestError:
        return False
    return response.status_code == 204


async def process_transaction(record_id: int) -> JSONResponse:
    record = _get_snapshot(record_id)
    if record is None:
        raise RuntimeError("Idempotency record disappeared")
    if record["status"] in {"SUCCEEDED", "FAILED"}:
        return _stored_response(record)

    async with httpx.AsyncClient(timeout=8.0) as client:
        if not record["product_name"] or record["total_amount"] is None:
            headers = service_headers("product-service")
            try:
                response = await client.get(
                    f"{PRODUCT_SERVICE_URL}/internal/products/{record['product_id']}",
                    headers=headers,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return _fail(record_id, 404, "Product not found")
                return _requires_reconciliation(record_id, "Product Service rejected the product lookup")
            except httpx.RequestError:
                return _requires_reconciliation(
                    record_id, "Product Service was unavailable before reservation"
                )

            try:
                product = response.json()
                amount = (Decimal(str(product["price"])) * record["quantity"]).quantize(Decimal("0.01"))
                product_name = str(product["name"])
            except (KeyError, TypeError, ValueError, ArithmeticError):
                return _fail(record_id, 502, "Product Service returned an invalid product")
            record = _update_record(
                record_id,
                product_name=product_name,
                total_amount=amount,
            )

        if not record["inventory_reserved"]:
            reserved, error_code = await _reserve_inventory(client, record)
            if not reserved:
                if error_code == 404:
                    return _fail(record_id, 404, "Product not found")
                if error_code == 409:
                    return _fail(record_id, 409, "Insufficient product stock")
                return _requires_reconciliation(record_id, "Inventory reservation outcome is uncertain")
            record = _update_record(
                record_id,
                inventory_reserved=True,
                status="INVENTORY_RESERVED",
            )

        if record["payment_status"] == "SUCCEEDED" and record["payment_response"]:
            return _finalize_success(record_id, record["payment_response"])

        if record["payment_status"] == "DECLINED" and record["payment_response"]:
            if not await _release_inventory(client, record):
                return _requires_reconciliation(
                    record_id,
                    "Payment was declined but inventory release is pending",
                    payment_status="DECLINED",
                )
            return _fail(record_id, 502, "Payment was declined", payment_status="DECLINED")

        record = _update_record(
            record_id,
            status="PAYMENT_PENDING",
            payment_status="PENDING",
        )
        payment = await _submit_or_recover_payment(client, record, service_headers("payment-service"))
        if payment is None:
            return _requires_reconciliation(record_id, "Payment outcome could not be determined")

        if payment["status"] == "declined":
            record = _update_record(
                record_id,
                status="PAYMENT_PENDING",
                payment_status="DECLINED",
                payment_response=payment,
            )
            if not await _release_inventory(client, record):
                return _requires_reconciliation(
                    record_id,
                    "Payment was declined but inventory release is pending",
                    payment_status="DECLINED",
                )
            return _fail(record_id, 502, "Payment was declined", payment_status="DECLINED")

        _update_record(
            record_id,
            status="PAYMENT_SUCCEEDED",
            payment_status="SUCCEEDED",
            payment_response=payment,
        )
        return _finalize_success(record_id, payment)


async def reconcile_stale_transactions(limit: int = 50) -> int:
    now = now_utc()
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(IdempotencyRecord.id)
                .where(
                    IdempotencyRecord.status.in_(UNFINISHED_STATUSES),
                    IdempotencyRecord.lease_until <= now,
                    IdempotencyRecord.attempt_count < MAX_ATTEMPTS,
                )
                .order_by(IdempotencyRecord.updated_at)
                .limit(limit)
            ).all()
        )

    reconciled = 0
    for record_id in ids:
        with SessionLocal() as db:
            acquired = cast(
                CursorResult,
                db.execute(
                    update(IdempotencyRecord)
                    .where(
                        IdempotencyRecord.id == record_id,
                        IdempotencyRecord.status.in_(UNFINISHED_STATUSES),
                        IdempotencyRecord.lease_until <= now,
                        IdempotencyRecord.attempt_count < MAX_ATTEMPTS,
                    )
                    .execution_options(synchronize_session=False)
                    .values(
                        lease_until=now + timedelta(seconds=LEASE_SECONDS),
                        attempt_count=IdempotencyRecord.attempt_count + 1,
                        updated_at=now,
                    )
                ),
            )
            db.commit()
        if acquired.rowcount == 1:
            record = _get_snapshot(record_id)
            if record is None:
                continue
            logger.info(
                "reconciling order transaction order_id=%s attempt=%s",
                record["order_id"],
                record["attempt_count"],
            )
            await process_transaction(record_id)
            reconciled += 1
    return reconciled


def reconcile_once() -> int:
    return asyncio.run(reconcile_stale_transactions())
