"""Smoke tests for GET /api/health (liveness + provider status)."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(settings: Settings | None = None) -> TestClient:
    return TestClient(create_app(settings))


def test_health_reports_liveness_and_provider_summary() -> None:
    client = make_client(Settings(_env_file=None))

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["provider"]["name"] == "glm"
    assert body["provider"]["model"] == "glm-5.3-flash"
