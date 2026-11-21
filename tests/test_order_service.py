from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete

from shared.database import SessionLocal
from services.order_service.app.models import Order
from services.order_service.app import main as orders

client = TestClient(orders.app)


def auth_headers():
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": "dev@example.com", "iat": now, "exp": now + timedelta(minutes=10)},
        orders.JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, url, headers=None):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={"id": 1, "name": "Keyboard", "price": 79.99},
            request=request,
        )

    async def post(self, url, json=None):
        request = httpx.Request("POST", url)
        return httpx.Response(
            201,
            json={
                "payment_id": "payment-123",
                "order_id": json["order_id"],
                "amount": json["amount"],
                "currency": json["currency"],
                "status": "succeeded",
                "simulation": True,
            },
            request=request,
        )


def setup_function():
    with SessionLocal() as db:
        db.execute(delete(Order))
        db.commit()


def test_order_creation_coordinates_product_and_payment_services(monkeypatch):
    monkeypatch.setattr(orders.httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        "/orders",
        headers=auth_headers(),
        json={"product_id": 1, "quantity": 2},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["product_name"] == "Keyboard"
    assert body["quantity"] == 2
    assert body["total_amount"] == 159.98
    assert body["status"] == "confirmed"
    assert body["payment"]["status"] == "succeeded"

    # The order must be retrievable from a new database session.
    listed = client.get("/orders", headers=auth_headers())
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["order_id"] == body["order_id"]


def test_order_routes_require_authentication():
    response = client.get("/orders")
    assert response.status_code == 401
