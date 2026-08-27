from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_which_vector_store_is_active() -> None:
    """Without a database URL the service must fall back to the local store,
    which is what makes the repository runnable with no accounts."""
    body = client.get("/health").json()

    assert body["vector_store"] in {"pgvector", "sqlite-vec"}
    assert body["embeddings"] in {"local", "openai"}


def test_openapi_schema_is_generated() -> None:
    """The shared TypeScript types are generated from this schema, so a broken
    schema breaks the web app's build rather than only the API."""
    schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == "FieldOps Copilot API"
    assert "/health" in schema["paths"]
