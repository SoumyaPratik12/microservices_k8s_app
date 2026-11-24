from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import delete

from shared.database import SessionLocal
from services.product_service.app.models import Product
from services.product_service.app import main as products

client = TestClient(products.app)
pytestmark = [pytest.mark.unit, pytest.mark.api, pytest.mark.regression]


def setup_function():
    with SessionLocal() as db:
        db.execute(delete(Product))
        db.commit()


def auth_headers():
    token = jwt.encode(
        {
            "sub": "dev@example.com",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
        },
        products.JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def order_service_headers(audience="product-service"):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "order-service",
            "service": "order-service",
            "aud": audience,
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        products.JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def test_product_routes_require_authentication():
    response = client.get("/products")
    assert response.status_code == 401


def test_product_create_list_update_and_delete():
    headers = auth_headers()
    created = client.post(
        "/products",
        headers=headers,
        json={
            "name": "Keyboard",
            "description": "Mechanical keyboard",
            "price": 79.99,
            "stock": 10,
        },
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    listed = client.get("/products", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    updated = client.patch(
        f"/products/{product_id}",
        headers=headers,
        json={"stock": 8},
    )
    assert updated.status_code == 200
    assert updated.json()["stock"] == 8

    deleted = client.delete(f"/products/{product_id}", headers=headers)
    assert deleted.status_code == 204
    assert client.get(f"/products/{product_id}", headers=headers).status_code == 404


def test_stock_reservation_rejects_insufficient_inventory():
    created = client.post(
        "/products",
        headers=auth_headers(),
        json={"name": "Limited item", "price": 10, "stock": 1},
    )
    product_id = created.json()["id"]

    reserved = client.post(
        f"/products/{product_id}/reserve",
        headers={**order_service_headers(), "Idempotency-Key": "insufficient-stock"},
        json={"quantity": 2},
    )

    assert reserved.status_code == 409
    product = client.get(f"/products/{product_id}", headers=auth_headers()).json()
    assert product["stock"] == 1


@pytest.mark.concurrency
def test_concurrent_reservations_cannot_oversell_inventory():
    created = client.post(
        "/products",
        headers=auth_headers(),
        json={"name": "Last unit", "price": 10, "stock": 1},
    )
    product_id = created.json()["id"]
    barrier = Barrier(2)

    def reserve(key):
        with TestClient(products.app) as concurrent_client:
            barrier.wait(timeout=10)
            return concurrent_client.post(
                f"/products/{product_id}/reserve",
                headers={**order_service_headers(), "Idempotency-Key": key},
                json={"quantity": 1},
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(reserve, ("reserve-a", "reserve-b")))

    assert sorted(response.status_code for response in responses) == [204, 409]
    product = client.get(f"/products/{product_id}", headers=auth_headers()).json()
    assert product["stock"] == 0


@pytest.mark.recovery
def test_reservation_replay_and_release_are_idempotent():
    created = client.post(
        "/products",
        headers=auth_headers(),
        json={"name": "Replay item", "price": 10, "stock": 5},
    )
    product_id = created.json()["id"]
    headers = {**order_service_headers(), "Idempotency-Key": "stable-reservation"}

    first = client.post(f"/products/{product_id}/reserve", headers=headers, json={"quantity": 2})
    duplicate = client.post(f"/products/{product_id}/reserve", headers=headers, json={"quantity": 2})
    conflict = client.post(f"/products/{product_id}/reserve", headers=headers, json={"quantity": 1})

    assert first.status_code == duplicate.status_code == 204
    assert conflict.status_code == 409
    assert client.get(f"/products/{product_id}", headers=auth_headers()).json()["stock"] == 3

    released = client.post(f"/products/{product_id}/release", headers=headers, json={"quantity": 2})
    duplicate_release = client.post(f"/products/{product_id}/release", headers=headers, json={"quantity": 2})
    assert released.status_code == duplicate_release.status_code == 204
    assert client.get(f"/products/{product_id}", headers=auth_headers()).json()["stock"] == 5

    reserve_after_release = client.post(
        f"/products/{product_id}/reserve", headers=headers, json={"quantity": 2}
    )
    assert reserve_after_release.status_code == 409
    assert client.get(f"/products/{product_id}", headers=auth_headers()).json()["stock"] == 5


def test_regular_user_cannot_release_stock():
    response = client.post(
        "/products/1/release",
        headers=auth_headers(),
        json={"quantity": 1},
    )

    assert response.status_code == 403


def test_order_service_token_with_wrong_audience_is_rejected():
    response = client.post(
        "/products/1/reserve",
        headers=order_service_headers(audience="payment-service"),
        json={"quantity": 1},
    )

    assert response.status_code == 401
