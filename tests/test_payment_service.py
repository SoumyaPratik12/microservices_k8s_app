from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete, select

from shared.database import SessionLocal
from services.payment_service.app import main as payments
from services.payment_service.app.models import Payment

client = TestClient(payments.app)
pytestmark = [pytest.mark.unit, pytest.mark.api, pytest.mark.regression]


def setup_function():
    with SessionLocal() as db:
        db.execute(delete(Payment))
        db.commit()


def order_service_headers(key="payment-key"):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "order-service",
            "service": "order-service",
            "aud": "payment-service",
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        payments.JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


def test_payment_endpoint_returns_simulated_payment():
    response = client.post(
        "/payments",
        headers=order_service_headers(),
        json={"order_id": "order-123", "amount": 159.98, "currency": "usd"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["order_id"] == "order-123"
    assert body["amount"] == 159.98
    assert body["currency"] == "USD"
    assert body["status"] == "succeeded"
    assert body["simulation"] is True
    assert body["payment_id"]
    with SessionLocal() as db:
        assert len(db.scalars(select(Payment)).all()) == 1


def test_duplicate_payment_returns_persisted_result():
    headers = order_service_headers("payment-replay")
    payload = {"order_id": "order-456", "amount": 10.25, "currency": "usd"}
    first = client.post("/payments", headers=headers, json=payload)
    duplicate = client.post("/payments", headers=headers, json=payload)

    assert first.status_code == duplicate.status_code == 201
    assert first.json() == duplicate.json()
    with SessionLocal() as db:
        assert len(db.scalars(select(Payment)).all()) == 1


@pytest.mark.concurrency
def test_concurrent_duplicate_payment_keys_create_one_ledger_row():
    headers = order_service_headers("payment-concurrent")
    payload = {"order_id": "order-concurrent", "amount": 10.25, "currency": "usd"}
    barrier = Barrier(2)

    def submit(_):
        with TestClient(payments.app) as concurrent_client:
            barrier.wait(timeout=10)
            return concurrent_client.post("/payments", headers=headers, json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, range(2)))
    assert sorted(response.status_code for response in responses) == [201, 201]
    assert responses[0].json() == responses[1].json()
    with SessionLocal() as db:
        assert len(db.scalars(select(Payment)).all()) == 1


def test_payment_key_with_different_payload_conflicts():
    headers = order_service_headers("payment-payload-conflict")
    assert (
        client.post("/payments", headers=headers, json={"order_id": "order-1", "amount": 10}).status_code
        == 201
    )
    conflict = client.post("/payments", headers=headers, json={"order_id": "order-1", "amount": 11})
    assert conflict.status_code == 409


def test_order_cannot_receive_a_second_payment_key():
    first = client.post(
        "/payments",
        headers=order_service_headers("payment-first-key"),
        json={"order_id": "single-payment-order", "amount": 10},
    )
    second = client.post(
        "/payments",
        headers=order_service_headers("payment-second-key"),
        json={"order_id": "single-payment-order", "amount": 10},
    )
    assert first.status_code == 201
    assert second.status_code == 409


def test_decline_is_persisted_and_replayed(monkeypatch):
    monkeypatch.setattr(payments, "SIMULATED_PAYMENT_OUTCOME", "declined")
    headers = order_service_headers("payment-decline")
    payload = {"order_id": "order-declined", "amount": 5}
    first = client.post("/payments", headers=headers, json=payload)
    duplicate = client.post("/payments", headers=headers, json=payload)

    assert first.status_code == duplicate.status_code == 402
    assert first.json() == duplicate.json()
    assert first.json()["status"] == "declined"


def test_payment_outcome_lookup_requires_service_auth():
    created = client.post(
        "/payments",
        headers=order_service_headers("payment-lookup"),
        json={"order_id": "order-lookup", "amount": 2},
    )
    assert created.status_code == 201
    lookup = client.get(
        "/payments/by-idempotency-key/payment-lookup",
        headers={"Authorization": order_service_headers()["Authorization"]},
    )
    assert lookup.status_code == 200
    assert lookup.json() == created.json()
    assert client.get("/payments/by-idempotency-key/payment-lookup").status_code == 401
