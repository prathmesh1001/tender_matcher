from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Route

from app.core.config import Environment, Settings
from app.services.health_service import HealthService


def test_health_service_reports_settings(settings: Settings) -> None:
    result = HealthService(settings).get_status()
    assert result.status == "ok"
    assert result.app == "Tender Matcher Test"
    assert result.version == "9.9.9"
    assert result.environment is Environment.TEST


def test_health_route_is_mounted_under_api_prefix(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "app": "Tender Matcher Test",
        "version": "9.9.9",
        "environment": "test",
    }


def test_health_route_absent_without_prefix(client: TestClient) -> None:
    assert client.get("/health").status_code == 404


def test_openapi_served_under_prefix_and_lists_health(client: TestClient) -> None:
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert spec["info"]["title"] == "Tender Matcher Test"
    assert "/api/v1/health" in spec["paths"]


def test_custom_prefix_is_respected(settings: Settings) -> None:
    from app.main import create_app

    custom = settings.model_copy(update={"API_PREFIX": "/api/v2"})
    with TestClient(create_app(custom)) as client:
        assert client.get("/api/v2/health").status_code == 200


def test_every_route_lives_under_api_prefix(app: FastAPI) -> None:
    paths = {r.path for r in app.routes if isinstance(r, Route | APIRoute)}
    assert paths
    assert {p for p in paths if not p.startswith("/api/v1/")} == set()
