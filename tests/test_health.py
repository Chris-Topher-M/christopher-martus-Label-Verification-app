from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_health_returns_healthy_status() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "ttb-label-verification",
    }


def test_root_serves_frontend() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "TTB Label Verification" in response.text
