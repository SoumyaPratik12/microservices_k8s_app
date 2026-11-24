import pytest
from fastapi.testclient import TestClient

from api_gateway.app.main import app

client = TestClient(app)
pytestmark = [pytest.mark.unit, pytest.mark.api, pytest.mark.regression]


def test_gateway_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "api-gateway"


def test_gateway_rejects_unknown_service():
    response = client.get("/api/unknown")
    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown service"
