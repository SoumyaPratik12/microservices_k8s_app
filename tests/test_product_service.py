from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete

from shared.database import SessionLocal
from services.product_service.app.models import Product
from services.product_service.app import main as products

client = TestClient(products.app)


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
