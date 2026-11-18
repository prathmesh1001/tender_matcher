"""DISCOVERY_ENABLED=false (the default): the run page gets no button and starting is refused."""

import uuid

from fastapi.testclient import TestClient

RUN = uuid.UUID(int=1)


def test_overview_says_off_without_touching_the_run(client: TestClient) -> None:
    response = client.get(f"/api/v1/discovery/runs/{RUN}")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "uncovered": []}


def test_starting_a_check_is_refused_when_off(client: TestClient) -> None:
    response = client.post(f"/api/v1/discovery/runs/{RUN}/gem", json={"product_type": "x"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "discovery_disabled"


def test_product_type_must_be_an_ontology_name(client: TestClient) -> None:
    response = client.post(
        f"/api/v1/discovery/runs/{RUN}/gem", json={"product_type": "Control Valve"}
    )
    assert response.status_code == 422


def test_saved_web_pages_are_served_sandboxed() -> None:
    from app.services.document_service import StoredFile

    assert StoredFile("a.html", "text/html", b"<script>").sandboxed
    assert not StoredFile("a.pdf", "application/pdf", b"%PDF").sandboxed
