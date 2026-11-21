from fastapi.testclient import TestClient
from sqlalchemy import delete

from shared.database import SessionLocal
from services.auth_service.app.models import User
from services.auth_service.app import main as auth

client = TestClient(auth.app)


def setup_function():
    with SessionLocal() as db:
        db.execute(delete(User))
        db.commit()


def test_register_and_login_returns_bearer_token():
    registered = client.post(
        "/register",
        json={"email": "dev@example.com", "password": "StrongPass123"},
    )
    assert registered.status_code == 201

    login = client.post(
        "/login",
        json={"email": "dev@example.com", "password": "StrongPass123"},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert body["access_token"]


def test_duplicate_registration_is_rejected():
    payload = {"email": "dev@example.com", "password": "StrongPass123"}
    assert client.post("/register", json=payload).status_code == 201
    duplicate = client.post("/register", json=payload)
    assert duplicate.status_code == 409


def test_invalid_password_does_not_authenticate():
    client.post(
        "/register",
        json={"email": "dev@example.com", "password": "StrongPass123"},
    )
    response = client.post(
        "/login",
        json={"email": "dev@example.com", "password": "WrongPass123"},
    )
    assert response.status_code == 401
