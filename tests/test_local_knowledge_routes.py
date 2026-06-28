from types import SimpleNamespace
import datetime
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
import pytest

from app.middleware import security_middleware
from app.services.local_knowledge_retrieval import (
    RetrievalConfigurationError,
    RetrievalDatabaseError,
    RetrievalEmbeddingError,
)


class FakeKnowledgeService:
    dataset_namespace = "vinuni-policy"

    def __init__(self):
        self.retrieve_calls = []
        self.resolve_profile_called = False
        self.closed = False

    async def discover_active_profiles(self):
        return [
            SimpleNamespace(
                embedding_profile="google-gemini:gemini-embedding-001:1536:v1",
                embedding_provider="google-gemini",
                embedding_model="gemini-embedding-001",
                embedding_dimensions=1536,
                embedding_input_version="v1",
                active_chunks=458,
            )
        ]

    async def retrieve(self, **kwargs):
        self.retrieve_calls.append(kwargs)
        profile = (await self.discover_active_profiles())[0]
        return SimpleNamespace(
            profile=profile,
            rows=[
                {
                    "chunk_id": "chunk-001",
                    "document_id": "policy-001",
                    "section_id": "article-1",
                    "ordinal": 0,
                    "content": "Policy text",
                    "embedding_content": "Embedded text",
                    "policy_title": "Academic Regulations",
                    "reference_number": "REF-001",
                    "document_type": "Policy",
                    "heading": "Article 1",
                    "heading_path": ["Academic Regulations", "Article 1"],
                    "url_source": "https://example.edu/policy",
                    "similarity": 0.82,
                }
            ],
        )

    async def fetch_document(self, document_id, limit):
        profile = (await self.discover_active_profiles())[0]
        return SimpleNamespace(
            profile=profile,
            rows=[
                {
                    "chunk_id": "chunk-001",
                    "ordinal": 0,
                    "content": "Policy text",
                    "heading": "Article 1",
                    "heading_path": ["Academic Regulations", "Article 1"],
                    "source_line_spans": [],
                    "review_required": False,
                    "review_reasons": [],
                    "data_quality_flags": [],
                    "warnings": [],
                }
            ],
        )

    async def fetch_neighbors(self, document_id, ordinal, window):
        return await self.fetch_document(document_id, limit=window + 1)

    async def resolve_profile(self):
        self.resolve_profile_called = True
        raise AssertionError("routes must not call resolve_profile directly")

    async def close(self):
        self.closed = True


@pytest.fixture
def routes_module(monkeypatch):
    import app.routes.local_knowledge_routes as module

    module._knowledge_service = None
    monkeypatch.setattr(module, "KNOWLEDGE_API_TOKEN", None)
    monkeypatch.setattr(module, "is_jwt_auth_configured", lambda: False)
    return module


@pytest.fixture
def client(routes_module):
    app = FastAPI()
    app.include_router(routes_module.router)
    return TestClient(app)


def set_fake_service(monkeypatch, routes_module, service=None):
    fake = service or FakeKnowledgeService()
    monkeypatch.setattr(routes_module, "get_knowledge_service", lambda: fake)
    return fake


def test_profiles_returns_503_when_auth_not_configured(client, monkeypatch, routes_module):
    set_fake_service(monkeypatch, routes_module)

    response = client.get("/knowledge/profiles")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Local knowledge retrieval authentication is not configured"
    )


@pytest.mark.parametrize("configured", ["token", "jwt"])
def test_profiles_returns_401_when_auth_configured_but_missing(
    client, monkeypatch, routes_module, configured
):
    set_fake_service(monkeypatch, routes_module)
    if configured == "token":
        monkeypatch.setattr(routes_module, "KNOWLEDGE_API_TOKEN", "secret-token")
    else:
        monkeypatch.setattr(routes_module, "is_jwt_auth_configured", lambda: True)

    response = client.get("/knowledge/profiles")

    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"


def test_backend_token_auth_succeeds(client, monkeypatch, routes_module):
    set_fake_service(monkeypatch, routes_module)
    monkeypatch.setattr(routes_module, "KNOWLEDGE_API_TOKEN", "secret-token")

    response = client.get(
        "/knowledge/profiles",
        headers={"X-Knowledge-API-Key": "secret-token"},
    )

    assert response.status_code == 200
    assert response.json()["dataset_namespace"] == "vinuni-policy"
    assert response.json()["profiles"][0]["active_chunks"] == 458


def test_jwt_request_state_succeeds_when_backend_token_invalid(
    monkeypatch, routes_module
):
    set_fake_service(monkeypatch, routes_module)
    monkeypatch.setattr(routes_module, "KNOWLEDGE_API_TOKEN", "secret-token")
    monkeypatch.setattr(routes_module, "is_jwt_auth_configured", lambda: True)

    app = FastAPI()

    @app.middleware("http")
    async def fake_jwt_state(request, call_next):
        request.state.user = {"id": "user-1"}
        return await call_next(request)

    app.include_router(routes_module.router)
    client = TestClient(app)

    response = client.get(
        "/knowledge/profiles",
        headers={"X-Knowledge-API-Key": "wrong-token"},
    )

    assert response.status_code == 200


def test_jwt_middleware_state_succeeds_when_backend_token_invalid(
    monkeypatch, routes_module
):
    set_fake_service(monkeypatch, routes_module)
    monkeypatch.setattr(routes_module, "KNOWLEDGE_API_TOKEN", "secret-token")
    monkeypatch.setattr(routes_module, "is_jwt_auth_configured", lambda: True)
    monkeypatch.setenv("JWT_SECRET", "testsecret")

    payload = {
        "id": "user-1",
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=1),
    }
    token = jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")

    app = FastAPI()
    app.middleware("http")(security_middleware)
    app.include_router(routes_module.router)
    client = TestClient(app)

    response = client.get(
        "/knowledge/profiles",
        headers={
            "X-Knowledge-API-Key": "wrong-token",
            "Authorization": f"Bearer {token}",
        },
    )

    assert response.status_code == 200


def test_retrieval_uses_operation_profile_and_normalizes_blank_filter(
    client, monkeypatch, routes_module
):
    service = set_fake_service(monkeypatch, routes_module)
    monkeypatch.setattr(routes_module, "KNOWLEDGE_API_TOKEN", "secret-token")

    response = client.post(
        "/knowledge/retrieve",
        headers={"X-Knowledge-API-Key": "secret-token"},
        json={
            "query": "What is the appeal procedure?",
            "filter_document_type": "   ",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["dataset_namespace"] == "vinuni-policy"
    assert (
        body["embedding_profile"]
        == "google-gemini:gemini-embedding-001:1536:v1"
    )
    assert service.resolve_profile_called is False
    assert service.retrieve_calls[0]["filter_document_type"] is None


@pytest.mark.parametrize(
    ("exc", "status_code"),
    [
        (RetrievalConfigurationError("postgresql://secret raw detail"), 503),
        (RetrievalDatabaseError("select * from secret"), 503),
        (RetrievalEmbeddingError("provider secret endpoint"), 502),
    ],
)
def test_route_errors_are_sanitized(client, monkeypatch, routes_module, exc, status_code):
    class FailingService(FakeKnowledgeService):
        async def discover_active_profiles(self):
            raise exc

    set_fake_service(monkeypatch, routes_module, FailingService())
    monkeypatch.setattr(routes_module, "KNOWLEDGE_API_TOKEN", "secret-token")

    response = client.get(
        "/knowledge/profiles",
        headers={"X-Knowledge-API-Key": "secret-token"},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": "Local knowledge retrieval is unavailable"}
    assert "secret" not in response.text
    assert "select" not in response.text


def test_service_factory_errors_are_sanitized(client, monkeypatch, routes_module):
    def fail_factory():
        raise RetrievalConfigurationError("dsn password secret")

    monkeypatch.setattr(routes_module, "get_knowledge_service", fail_factory)
    monkeypatch.setattr(routes_module, "KNOWLEDGE_API_TOKEN", "secret-token")

    response = client.get(
        "/knowledge/profiles",
        headers={"X-Knowledge-API-Key": "secret-token"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Local knowledge retrieval is unavailable"}
    assert "secret" not in response.text


def test_document_id_validation_rejects_blank(client, monkeypatch, routes_module):
    set_fake_service(monkeypatch, routes_module)
    monkeypatch.setattr(routes_module, "KNOWLEDGE_API_TOKEN", "secret-token")

    response = client.get(
        "/knowledge/documents/%20",
        headers={"X-Knowledge-API-Key": "secret-token"},
    )

    assert response.status_code == 422


def test_document_and_neighbors_return_service_envelopes(
    client, monkeypatch, routes_module
):
    set_fake_service(monkeypatch, routes_module)
    monkeypatch.setattr(routes_module, "KNOWLEDGE_API_TOKEN", "secret-token")
    headers = {"X-Knowledge-API-Key": "secret-token"}

    document = client.get("/knowledge/documents/doc-1", headers=headers)
    neighbors = client.get(
        "/knowledge/documents/doc-1/neighbors?ordinal=2&window=1",
        headers=headers,
    )

    assert document.status_code == 200
    assert document.json()["document_id"] == "doc-1"
    assert document.json()["chunks"][0]["review_required"] is False
    assert neighbors.status_code == 200
    assert neighbors.json()["document_id"] == "doc-1"


@pytest.mark.parametrize("document_id", ["x", "x" * 255])
def test_document_id_validation_accepts_bounds(
    client, monkeypatch, routes_module, document_id
):
    set_fake_service(monkeypatch, routes_module)
    monkeypatch.setattr(routes_module, "KNOWLEDGE_API_TOKEN", "secret-token")

    response = client.get(
        f"/knowledge/documents/{document_id}",
        headers={"X-Knowledge-API-Key": "secret-token"},
    )

    assert response.status_code == 200


def test_document_id_validation_rejects_too_long(client, monkeypatch, routes_module):
    set_fake_service(monkeypatch, routes_module)
    monkeypatch.setattr(routes_module, "KNOWLEDGE_API_TOKEN", "secret-token")

    response = client.get(
        f"/knowledge/documents/{'x' * 256}",
        headers={"X-Knowledge-API-Key": "secret-token"},
    )

    assert response.status_code == 422


def test_close_knowledge_service_closes_and_resets(routes_module):
    service = FakeKnowledgeService()
    routes_module._knowledge_service = service

    import asyncio

    asyncio.run(routes_module.close_knowledge_service())

    assert service.closed is True
    assert routes_module._knowledge_service is None
