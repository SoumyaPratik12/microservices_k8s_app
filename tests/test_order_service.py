import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier

import httpx
import pytest
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import delete, select

from shared.database import SessionLocal
from services.order_service.app.models import IdempotencyRecord, Order
from services.order_service.app import main as orders
from services.order_service.app import transactions
from services.payment_service.app import main as payments
from services.payment_service.app.models import Payment
from services.product_service.app import main as products
from services.product_service.app.models import InventoryReservation, Product

client = TestClient(orders.app)
REAL_ASYNC_CLIENT = httpx.AsyncClient
pytestmark = [pytest.mark.unit, pytest.mark.api, pytest.mark.regression]


def auth_headers(email="dev@example.com"):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": email, "iat": now, "exp": now + timedelta(minutes=10)},
        orders.JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def request_headers(key, email="dev@example.com"):
    return {**auth_headers(email), "Idempotency-Key": key}


class FakeAsyncClient:
    calls = []
    reservation_status = 204
    payment_status = 201
    payment_result = "succeeded"
    release_status = 204
    payment_outcomes = {}

    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, url, headers=None):
        self.calls.append(("GET", url))
        request = httpx.Request("GET", url)
        if "/payments/by-idempotency-key/" in url:
            key = url.rsplit("/", 1)[-1]
            if key not in self.payment_outcomes:
                return httpx.Response(404, request=request)
            return httpx.Response(200, json=self.payment_outcomes[key], request=request)
        return httpx.Response(
            200,
            json={"id": 1, "name": "Keyboard", "price": 79.99, "stock": 10},
            request=request,
        )

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url))
        request = httpx.Request("POST", url)
        if url.endswith("/reserve"):
            return httpx.Response(self.reservation_status, request=request)
        if url.endswith("/release"):
            return httpx.Response(self.release_status, request=request)
        key = headers["Idempotency-Key"]
        payment = self.payment_outcomes.get(key)
        if payment is None and self.payment_status in {201, 402}:
            payment = {
                "payment_id": "payment-123",
                "order_id": json["order_id"],
                "amount": json["amount"],
                "currency": json["currency"],
                "status": self.payment_result,
                "simulation": True,
            }
            self.payment_outcomes[key] = payment
        status = 402 if payment and payment["status"] == "declined" else self.payment_status
        return httpx.Response(status, json=payment, request=request)


class LocalServiceAsyncClient:
    calls = []

    def __init__(self, *args, **kwargs):
        self.product_client = None
        self.payment_client = None

    async def __aenter__(self):
        self.product_client = REAL_ASYNC_CLIENT(transport=httpx.ASGITransport(app=products.app))
        self.payment_client = REAL_ASYNC_CLIENT(transport=httpx.ASGITransport(app=payments.app))
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.product_client.aclose()
        await self.payment_client.aclose()
        return False

    async def get(self, url, headers=None):
        self.calls.append(("GET", url))
        if url.startswith(transactions.PAYMENT_SERVICE_URL):
            return await self.payment_client.get(url, headers=headers)
        return await self.product_client.get(url, headers=headers)

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url))
        if url.startswith(transactions.PAYMENT_SERVICE_URL):
            return await self.payment_client.post(url, json=json, headers=headers)
        return await self.product_client.post(url, json=json, headers=headers)


def setup_function():
    with SessionLocal() as db:
        db.execute(delete(InventoryReservation))
        db.execute(delete(Payment))
        db.execute(delete(IdempotencyRecord))
        db.execute(delete(Order))
        db.execute(delete(Product))
        db.commit()
    FakeAsyncClient.calls = []
    FakeAsyncClient.reservation_status = 204
    FakeAsyncClient.payment_status = 201
    FakeAsyncClient.payment_result = "succeeded"
    FakeAsyncClient.release_status = 204
    FakeAsyncClient.payment_outcomes = {}
    LocalServiceAsyncClient.calls = []


def create_test_product(stock=8):
    response = TestClient(products.app).post(
        "/products",
        headers=auth_headers(),
        json={"name": "Idempotent item", "price": 12.50, "stock": stock},
    )
    assert response.status_code == 201
    return response.json()["id"]


def expire_idempotency_lease(key, email="dev@example.com"):
    with SessionLocal() as db:
        record = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user == email,
                IdempotencyRecord.idempotency_key == key,
            )
        )
        assert record is not None
        record.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()


def test_order_creation_coordinates_product_and_payment_services(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        "/orders",
        headers=request_headers("order-first"),
        json={"product_id": 1, "quantity": 2},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["product_name"] == "Keyboard"
    assert body["quantity"] == 2
    assert body["total_amount"] == 159.98
    assert body["status"] == "confirmed"
    assert body["payment"]["status"] == "succeeded"
    assert [method for method, _ in FakeAsyncClient.calls] == ["GET", "POST", "GET", "POST"]
    assert FakeAsyncClient.calls[1][1].endswith("/products/1/reserve")
    assert FakeAsyncClient.calls[3][1].endswith("/payments")

    # The order must be retrievable from a new database session.
    listed = client.get("/orders", headers=auth_headers())
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["order_id"] == body["order_id"]


def test_insufficient_inventory_does_not_call_payment_or_save_order(monkeypatch):
    FakeAsyncClient.reservation_status = 409
    monkeypatch.setattr(transactions.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/orders",
        headers=request_headers("order-insufficient"),
        json={"product_id": 1, "quantity": 2},
    )

    assert response.status_code == 409
    assert [method for method, _ in FakeAsyncClient.calls] == ["GET", "POST"]
    assert not any(url.endswith("/payments") for _, url in FakeAsyncClient.calls)
    assert client.get("/orders", headers=auth_headers()).json() == []


def test_payment_failure_releases_stock_and_does_not_save_order(monkeypatch):
    FakeAsyncClient.payment_result = "declined"
    monkeypatch.setattr(transactions.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/orders",
        headers=request_headers("order-payment-failure"),
        json={"product_id": 1, "quantity": 2},
    )

    assert response.status_code == 502
    assert [method for method, _ in FakeAsyncClient.calls] == ["GET", "POST", "GET", "POST", "POST"]
    assert FakeAsyncClient.calls[-1][1].endswith("/products/1/release")
    assert client.get("/orders", headers=auth_headers()).json() == []


def test_declined_payment_releases_stock(monkeypatch):
    FakeAsyncClient.payment_result = "declined"
    monkeypatch.setattr(transactions.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/orders",
        headers=request_headers("order-declined"),
        json={"product_id": 1, "quantity": 2},
    )

    assert response.status_code == 502
    assert FakeAsyncClient.calls[-1][1].endswith("/products/1/release")
    assert client.get("/orders", headers=auth_headers()).json() == []


@pytest.mark.concurrency
def test_concurrent_orders_cannot_purchase_the_last_unit(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", LocalServiceAsyncClient)
    monkeypatch.setattr(payments, "SIMULATED_PAYMENT_OUTCOME", "succeeded")
    with SessionLocal() as db:
        product = Product(
            name="Last unit",
            description="",
            price=10,
            stock=1,
            created_by="dev@example.com",
        )
        db.add(product)
        db.commit()
        product_id = product.id

    barrier = Barrier(2)

    def place_order(request_number):
        with TestClient(orders.app) as concurrent_client:
            barrier.wait(timeout=10)
            return concurrent_client.post(
                "/orders",
                headers=request_headers(f"concurrent-{product_id}-{request_number}"),
                json={"product_id": product_id, "quantity": 1},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(place_order, range(2)))

    assert sorted(response.status_code for response in responses) == [201, 409]
    with SessionLocal() as db:
        assert db.get(Product, product_id).stock == 0
        assert len(db.scalars(select(Order)).all()) == 1


def test_failed_payment_restores_inventory_with_product_service(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", LocalServiceAsyncClient)
    monkeypatch.setattr(payments, "SIMULATED_PAYMENT_OUTCOME", "declined")
    with SessionLocal() as db:
        product = Product(
            name="Payment rollback item",
            description="",
            price=10,
            stock=3,
            created_by="dev@example.com",
        )
        db.add(product)
        db.commit()
        product_id = product.id

    response = client.post(
        "/orders",
        headers=request_headers("local-service-decline"),
        json={"product_id": product_id, "quantity": 2},
    )

    assert response.status_code == 502
    with SessionLocal() as db:
        assert db.get(Product, product_id).stock == 3
        assert db.scalars(select(Order)).all() == []


def test_missing_or_malformed_idempotency_key_is_rejected():
    payload = {"product_id": 1, "quantity": 1}
    assert client.post("/orders", headers=auth_headers(), json=payload).status_code == 400
    assert client.post("/orders", headers=request_headers("not valid"), json=payload).status_code == 400
    with SessionLocal() as db:
        assert db.scalars(select(IdempotencyRecord)).all() == []


def test_same_key_replays_original_order_and_does_not_repeat_side_effects(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", FakeAsyncClient)
    headers = request_headers("order-replay")
    payload = {"product_id": 1, "quantity": 2}
    first = client.post("/orders", headers=headers, json=payload)
    duplicate = client.post("/orders", headers=headers, json=payload)

    assert first.status_code == duplicate.status_code == 201
    assert first.json() == duplicate.json()
    assert sum(url.endswith("/reserve") for _, url in FakeAsyncClient.calls) == 1
    assert sum(url.endswith("/payments") for _, url in FakeAsyncClient.calls) == 1
    with SessionLocal() as db:
        assert len(db.scalars(select(Order)).all()) == 1
        assert len(db.scalars(select(IdempotencyRecord)).all()) == 1


def test_same_key_different_payload_conflicts(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", FakeAsyncClient)
    headers = request_headers("order-payload-conflict")
    first = client.post("/orders", headers=headers, json={"product_id": 1, "quantity": 1})
    conflict = client.post("/orders", headers=headers, json={"product_id": 1, "quantity": 2})
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert sum(url.endswith("/reserve") for _, url in FakeAsyncClient.calls) == 1


def test_same_key_is_scoped_to_authenticated_user(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", FakeAsyncClient)
    payload = {"product_id": 1, "quantity": 1}
    first = client.post("/orders", headers=request_headers("shared-key", "one@example.test"), json=payload)
    second = client.post("/orders", headers=request_headers("shared-key", "two@example.test"), json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["order_id"] != second.json()["order_id"]
    with SessionLocal() as db:
        assert len(db.scalars(select(Order)).all()) == 2


@pytest.mark.concurrency
def test_concurrent_identical_keys_create_one_order(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", FakeAsyncClient)
    barrier = Barrier(2)

    def submit(_):
        with TestClient(orders.app) as concurrent_client:
            barrier.wait(timeout=10)
            return concurrent_client.post(
                "/orders",
                headers=request_headers("concurrent-identical"),
                json={"product_id": 1, "quantity": 1},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, range(2)))
    assert sorted(response.status_code for response in responses) in ([201, 201], [201, 409])
    assert sum(url.endswith("/reserve") for _, url in FakeAsyncClient.calls) == 1
    with SessionLocal() as db:
        assert len(db.scalars(select(Order)).all()) == 1


@pytest.mark.concurrency
def test_concurrent_different_payloads_with_same_key_conflict(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", FakeAsyncClient)
    barrier = Barrier(2)

    def submit(quantity):
        with TestClient(orders.app) as concurrent_client:
            barrier.wait(timeout=10)
            return concurrent_client.post(
                "/orders",
                headers=request_headers("concurrent-conflict"),
                json={"product_id": 1, "quantity": quantity},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, (1, 2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    with SessionLocal() as db:
        assert len(db.scalars(select(IdempotencyRecord)).all()) == 1
        assert len(db.scalars(select(Order)).all()) == 1


@pytest.mark.recovery
def test_recovery_after_reservation_crash_does_not_reserve_twice(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", LocalServiceAsyncClient)
    product_id = create_test_product(stock=5)
    update_record = transactions._update_record

    def crash_before_recording_reservation(record_id, **changes):
        if changes.get("inventory_reserved") is True:
            raise RuntimeError("simulated crash after reservation")
        return update_record(record_id, **changes)

    monkeypatch.setattr(transactions, "_update_record", crash_before_recording_reservation)
    crash_client = TestClient(orders.app, raise_server_exceptions=False)
    headers = request_headers("crash-after-reserve")
    payload = {"product_id": product_id, "quantity": 2}
    assert crash_client.post("/orders", headers=headers, json=payload).status_code == 500
    with SessionLocal() as db:
        record = db.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "crash-after-reserve")
        )
        reservation = db.get(InventoryReservation, record.order_id)
        assert record.inventory_reserved is False
        assert reservation.status == "RESERVED"
        assert db.get(Product, product_id).stock == 3

    monkeypatch.setattr(transactions, "_update_record", update_record)
    expire_idempotency_lease("crash-after-reserve")
    recovered = client.post("/orders", headers=headers, json=payload)
    assert recovered.status_code == 201
    with SessionLocal() as db:
        assert db.get(Product, product_id).stock == 3
        assert len(db.scalars(select(InventoryReservation)).all()) == 1
        assert len(db.scalars(select(Payment)).all()) == 1
        assert len(db.scalars(select(Order)).all()) == 1


@pytest.mark.recovery
def test_recovery_after_crash_before_inventory_reservation(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", LocalServiceAsyncClient)
    product_id = create_test_product(stock=3)
    reserve_inventory = transactions._reserve_inventory

    async def crash_before_reserving(client, record):
        raise RuntimeError("simulated crash before reservation")

    monkeypatch.setattr(transactions, "_reserve_inventory", crash_before_reserving)
    crash_client = TestClient(orders.app, raise_server_exceptions=False)
    headers = request_headers("crash-before-reserve")
    payload = {"product_id": product_id, "quantity": 1}
    assert crash_client.post("/orders", headers=headers, json=payload).status_code == 500
    with SessionLocal() as db:
        record = db.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "crash-before-reserve")
        )
        assert record.product_name == "Idempotent item"
        assert record.inventory_reserved is False
        assert db.get(Product, product_id).stock == 3

    monkeypatch.setattr(transactions, "_reserve_inventory", reserve_inventory)
    expire_idempotency_lease("crash-before-reserve")
    recovered = client.post("/orders", headers=headers, json=payload)
    assert recovered.status_code == 201
    with SessionLocal() as db:
        assert db.get(Product, product_id).stock == 2
        assert len(db.scalars(select(Order)).all()) == 1


@pytest.mark.recovery
def test_reconciler_recovers_payment_committed_before_order_finalization(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", LocalServiceAsyncClient)
    monkeypatch.setattr(payments, "SIMULATED_PAYMENT_OUTCOME", "succeeded")
    product_id = create_test_product(stock=4)
    update_record = transactions._update_record
    crashed = False

    def crash_after_payment_commit(record_id, **changes):
        nonlocal crashed
        if changes.get("status") == "PAYMENT_SUCCEEDED" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash after payment commit")
        return update_record(record_id, **changes)

    monkeypatch.setattr(transactions, "_update_record", crash_after_payment_commit)
    crash_client = TestClient(orders.app, raise_server_exceptions=False)
    headers = request_headers("crash-after-payment")
    payload = {"product_id": product_id, "quantity": 1}
    assert crash_client.post("/orders", headers=headers, json=payload).status_code == 500
    with SessionLocal() as db:
        record = db.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "crash-after-payment")
        )
        assert record.payment_status == "PENDING"
        assert len(db.scalars(select(Payment)).all()) == 1

    monkeypatch.setattr(transactions, "_update_record", update_record)
    expire_idempotency_lease("crash-after-payment")
    assert asyncio.run(transactions.reconcile_stale_transactions()) == 1
    assert (
        sum(method == "POST" and url.endswith("/payments") for method, url in LocalServiceAsyncClient.calls)
        == 1
    )
    with SessionLocal() as db:
        assert db.get(Product, product_id).stock == 3
        assert len(db.scalars(select(Payment)).all()) == 1
        assert len(db.scalars(select(Order)).all()) == 1
        assert db.scalar(select(IdempotencyRecord)).status == "SUCCEEDED"


@pytest.mark.recovery
def test_reconciler_recovers_claim_before_product_snapshot(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", LocalServiceAsyncClient)
    product_id = create_test_product(stock=2)
    now = datetime.now(timezone.utc)
    record = IdempotencyRecord(
        user="dev@example.com",
        idempotency_key="crash-before-product-snapshot",
        request_hash=transactions.canonical_request_hash(product_id, 1),
        status="PROCESSING",
        product_id=product_id,
        product_name=None,
        quantity=1,
        total_amount=None,
        order_id="crash-before-snapshot-order",
        payment_idempotency_key="crash-before-snapshot-payment",
        inventory_reserved=False,
        payment_status="NOT_STARTED",
        attempt_count=1,
        lease_until=now - timedelta(seconds=1),
    )
    with SessionLocal() as db:
        db.add(record)
        db.commit()

    assert asyncio.run(transactions.reconcile_stale_transactions()) == 1
    with SessionLocal() as db:
        recovered = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.idempotency_key == "crash-before-product-snapshot"
            )
        )
        assert recovered.status == "SUCCEEDED"
        assert recovered.product_name == "Idempotent item"
        assert recovered.total_amount == Decimal("12.50")
        assert db.get(Product, product_id).stock == 1
        assert db.get(Order, recovered.order_id) is not None


@pytest.mark.recovery
def test_unavailable_payment_is_reconciled_without_releasing_inventory(monkeypatch):
    product_id = create_test_product(stock=3)

    class PaymentUnavailableClient(LocalServiceAsyncClient):
        async def get(self, url, headers=None):
            if url.startswith(transactions.PAYMENT_SERVICE_URL):
                raise httpx.ConnectError(
                    "simulated unavailable payment service", request=httpx.Request("GET", url)
                )
            return await super().get(url, headers=headers)

        async def post(self, url, json=None, headers=None):
            if url.startswith(transactions.PAYMENT_SERVICE_URL):
                raise httpx.ConnectError(
                    "simulated unavailable payment service", request=httpx.Request("POST", url)
                )
            return await super().post(url, json=json, headers=headers)

    monkeypatch.setattr(transactions.httpx, "AsyncClient", PaymentUnavailableClient)
    key = "payment-unavailable"
    payload = {"product_id": product_id, "quantity": 1}
    response = client.post("/orders", headers=request_headers(key), json=payload)
    assert response.status_code == 503
    with SessionLocal() as db:
        record = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key))
        assert record.status == "REQUIRES_RECONCILIATION"
        assert record.inventory_reserved is True
        assert db.get(Product, product_id).stock == 2
        assert db.scalars(select(Payment)).all() == []

    monkeypatch.setattr(transactions.httpx, "AsyncClient", LocalServiceAsyncClient)
    expire_idempotency_lease(key)
    assert asyncio.run(transactions.reconcile_stale_transactions()) == 1
    with SessionLocal() as db:
        assert db.get(Product, product_id).stock == 2
        assert len(db.scalars(select(Payment)).all()) == 1
        assert len(db.scalars(select(Order)).all()) == 1


@pytest.mark.recovery
def test_timeout_after_payment_commit_recovers_existing_outcome(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", LocalServiceAsyncClient)
    product_id = create_test_product(stock=3)

    class TimeoutAfterCommitClient(LocalServiceAsyncClient):
        did_timeout = False

        async def post(self, url, json=None, headers=None):
            response = await super().post(url, json=json, headers=headers)
            if url.startswith(transactions.PAYMENT_SERVICE_URL) and not self.did_timeout:
                self.did_timeout = True
                raise httpx.ReadTimeout("simulated lost payment response")
            return response

    monkeypatch.setattr(transactions.httpx, "AsyncClient", TimeoutAfterCommitClient)
    response = client.post(
        "/orders",
        headers=request_headers("payment-timeout-recovery"),
        json={"product_id": product_id, "quantity": 1},
    )
    assert response.status_code == 201
    assert (
        sum(method == "POST" and url.endswith("/payments") for method, url in TimeoutAfterCommitClient.calls)
        == 1
    )
    with SessionLocal() as db:
        assert len(db.scalars(select(Payment)).all()) == 1
        assert len(db.scalars(select(Order)).all()) == 1


@pytest.mark.recovery
def test_crash_after_order_commit_replays_saved_http_result(monkeypatch):
    monkeypatch.setattr(transactions.httpx, "AsyncClient", LocalServiceAsyncClient)
    product_id = create_test_product(stock=2)
    process_order = orders.process_transaction
    crashed = False

    async def commit_then_crash(record_id):
        nonlocal crashed
        response = await process_order(record_id)
        if not crashed:
            crashed = True
            raise RuntimeError("simulated response loss after commit")
        return response

    monkeypatch.setattr(orders, "process_transaction", commit_then_crash)
    headers = request_headers("crash-after-order-commit")
    payload = {"product_id": product_id, "quantity": 1}
    crash_client = TestClient(orders.app, raise_server_exceptions=False)
    assert crash_client.post("/orders", headers=headers, json=payload).status_code == 500
    with SessionLocal() as db:
        saved = db.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "crash-after-order-commit")
        )
        assert saved.status == "SUCCEEDED"
        original_response = saved.response_json

    replay = client.post("/orders", headers=headers, json=payload)
    assert replay.status_code == 201
    assert replay.json() == original_response
    assert (
        sum(method == "POST" and url.endswith("/payments") for method, url in LocalServiceAsyncClient.calls)
        == 1
    )
    with SessionLocal() as db:
        assert db.get(Product, product_id).stock == 1
        assert len(db.scalars(select(Payment)).all()) == 1
        assert len(db.scalars(select(Order)).all()) == 1


def test_order_routes_require_authentication():
    response = client.get("/orders")
    assert response.status_code == 401
