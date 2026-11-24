from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import httpx
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete, select

from shared.database import SessionLocal
from services.order_service.app import main as orders
from services.order_service.app import transactions
from services.order_service.app.models import IdempotencyRecord, Order
from services.payment_service.app import main as payments
from services.payment_service.app.models import Payment
from services.product_service.app import main as products
from services.product_service.app.models import InventoryReservation, Product

pytestmark = [pytest.mark.postgresql, pytest.mark.integration]

order_client = TestClient(orders.app)
product_client = TestClient(products.app)
payment_client = TestClient(payments.app)
real_async_client = httpx.AsyncClient


class InProcessServiceClient:
    def __init__(self, *args, **kwargs):
        self.product_transport = None
        self.payment_transport = None

    async def __aenter__(self):
        self.product_transport = real_async_client(transport=httpx.ASGITransport(app=products.app))
        self.payment_transport = real_async_client(transport=httpx.ASGITransport(app=payments.app))
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.product_transport.aclose()
        await self.payment_transport.aclose()
        return False

    async def get(self, url, headers=None):
        if url.startswith(transactions.PAYMENT_SERVICE_URL):
            return await self.payment_transport.get(url, headers=headers)
        return await self.product_transport.get(url, headers=headers)

    async def post(self, url, json=None, headers=None):
        if url.startswith(transactions.PAYMENT_SERVICE_URL):
            return await self.payment_transport.post(url, json=json, headers=headers)
        return await self.product_transport.post(url, json=json, headers=headers)


@pytest.fixture(autouse=True)
def isolate_postgresql_rows(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", InProcessServiceClient)
    with SessionLocal() as db:
        db.execute(delete(InventoryReservation))
        db.execute(delete(Payment))
        db.execute(delete(IdempotencyRecord))
        db.execute(delete(Order))
        db.execute(delete(Product))
        db.commit()


def user_headers(email="postgres-test@example.com"):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": email, "iat": now, "exp": now + timedelta(minutes=10)},
        orders.JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def order_service_headers(key):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "order-service",
            "service": "order-service",
            "aud": "product-service",
            "iat": now,
            "exp": now + timedelta(minutes=1),
        },
        products.JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


def create_product(stock):
    response = product_client.post(
        "/products",
        headers=user_headers(),
        json={"name": "PostgreSQL concurrency product", "price": 2.50, "stock": stock},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_postgresql_concurrent_order_claim_and_stock_reservation():
    product_id = create_product(stock=1)
    barrier = Barrier(2)

    def submit(_):
        with TestClient(orders.app) as client:
            barrier.wait(timeout=10)
            return client.post(
                "/orders",
                headers={**user_headers(), "Idempotency-Key": "pg-concurrent-same-key"},
                json={"product_id": product_id, "quantity": 1},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))

    assert sorted(response.status_code for response in responses) in ([201, 201], [201, 409])
    successful_orders = {response.json()["order_id"] for response in responses if response.status_code == 201}
    assert len(successful_orders) == 1
    with SessionLocal() as db:
        assert db.get(Product, product_id).stock == 0
        assert len(db.scalars(select(IdempotencyRecord)).all()) == 1
        assert len(db.scalars(select(Order)).all()) == 1
        assert len(db.scalars(select(Payment)).all()) == 1
        assert len(db.scalars(select(InventoryReservation)).all()) == 1


def test_postgresql_concurrent_different_payloads_with_same_key_conflict():
    product_id = create_product(stock=5)
    barrier = Barrier(2)

    def submit(quantity):
        with TestClient(orders.app) as client:
            barrier.wait(timeout=10)
            return client.post(
                "/orders",
                headers={**user_headers(), "Idempotency-Key": "pg-concurrent-payload"},
                json={"product_id": product_id, "quantity": quantity},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, (1, 2)))

    assert sorted(response.status_code for response in responses) == [201, 409]
    with SessionLocal() as db:
        assert len(db.scalars(select(IdempotencyRecord)).all()) == 1
        assert len(db.scalars(select(Order)).all()) == 1
        assert len(db.scalars(select(Payment)).all()) == 1


def test_postgresql_payment_ledger_deduplicates_concurrent_requests():
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "order-service",
            "service": "order-service",
            "aud": "payment-service",
            "iat": now,
            "exp": now + timedelta(minutes=1),
        },
        payments.JWT_SECRET,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "pg-payment-concurrent"}
    barrier = Barrier(2)

    def submit(_):
        with TestClient(payments.app) as client:
            barrier.wait(timeout=10)
            return client.post("/payments", headers=headers, json={"order_id": "pg-order", "amount": 4.25})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))

    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    with SessionLocal() as db:
        assert len(db.scalars(select(Payment)).all()) == 1
