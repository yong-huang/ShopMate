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


def test_cloud_provider_without_api_key_reports_not_configured() -> None:
    client = make_client(
        Settings(llm_provider="glm", llm_api_key=None, _env_file=None)
    )

    body = client.get("/api/health").json()

    assert body["provider"]["api_key_configured"] is False
    assert body["provider"]["status"] == "not_configured"


def test_cloud_provider_with_api_key_reports_configured() -> None:
    client = make_client(
        Settings(llm_provider="glm", llm_api_key="secret", _env_file=None)
    )

    body = client.get("/api/health").json()

    assert body["provider"]["api_key_configured"] is True
    assert body["provider"]["status"] == "configured"


def test_ollama_needs_no_key_and_reports_local_models() -> None:
    client = make_client(Settings(llm_provider="ollama", _env_file=None))

    body = client.get("/api/health").json()

    provider = body["provider"]
    assert provider["name"] == "ollama"
    assert provider["model"] == "qwen3:8b"
    assert provider["base_url"] == "http://localhost:11434"
    assert provider["api_key_configured"] is True
    assert provider["status"] == "configured"


def test_openai_provider_reports_its_key_and_model() -> None:
    client = make_client(
        Settings(
            llm_provider="openai",
            llm_model="gpt-5",
            llm_api_key="secret",
            _env_file=None,
        )
    )

    body = client.get("/api/health").json()

    provider = body["provider"]
    assert provider["name"] == "openai"
    assert provider["model"] == "gpt-5"
    assert provider["api_key_configured"] is True
    assert provider["status"] == "configured"


def test_health_allows_cross_origin_calls_from_the_frontend() -> None:
    client = make_client(Settings(frontend_port=3000, _env_file=None))

    response = client.get(
        "/api/health", headers={"Origin": "http://localhost:3000"}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_empty_base_url_is_reported_as_null() -> None:
    client = make_client(Settings(llm_base_url="", _env_file=None))

    body = client.get("/api/health").json()

    assert body["provider"]["base_url"] is None


def test_cors_origin_follows_configured_frontend_port() -> None:
    client = make_client(Settings(frontend_port=3100, _env_file=None))

    response = client.get(
        "/api/health", headers={"Origin": "http://localhost:3100"}
    )

    assert response.headers["access-control-allow-origin"] == "http://localhost:3100"
