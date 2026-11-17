"""Tests for request-scoped structured logging middleware (NFR-4).

The seam is the FastAPI app itself: any HTTP request must leave behind one
machine-parseable `http_request` record with status and latency.
"""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(**settings_kwargs) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, **settings_kwargs)))


def test_request_emits_structured_access_record(json_lines) -> None:
    client = make_client()

    response = client.get("/api/health")

    assert response.status_code == 200
    records = json_lines.of_event("http_request")
    assert len(records) == 1
    record = records[0]
    assert record["level"] == "info"
    assert record["method"] == "GET"
    assert record["path"] == "/api/health"
    assert record["status_code"] == 200
    assert isinstance(record["duration_ms"], (int, float))
    assert record["duration_ms"] >= 0
    assert isinstance(record["request_id"], str)
    assert record["request_id"]


def test_each_request_gets_its_own_request_id(json_lines) -> None:
    client = make_client()

    client.get("/api/health")
    client.get("/api/health")

    records = json_lines.of_event("http_request")
    assert len(records) == 2
    assert records[0]["request_id"] != records[1]["request_id"]


def test_failed_request_still_emits_access_record(json_lines) -> None:
    from fastapi import FastAPI

    app = create_app(Settings(_env_file=None))

    @app.get("/api/_boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/_boom")

    assert response.status_code == 500
    records = json_lines.of_event("http_request")
    assert len(records) == 1
    assert records[0]["status_code"] == 500
    assert records[0]["status"] == "error"
    assert records[0]["error_type"] == "RuntimeError"
