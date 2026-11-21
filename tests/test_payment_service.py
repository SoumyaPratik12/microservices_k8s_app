from fastapi.testclient import TestClient

from services.payment_service.app.main import app

client = TestClient(app)


def test_payment_endpoint_returns_simulated_payment():
    response = client.post(
        "/payments",
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
