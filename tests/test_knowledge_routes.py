from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from langchain_core.documents import Document

from main import app
from app.routes import knowledge_routes


client = TestClient(app)


@pytest.fixture(autouse=True)
def knowledge_route_fakes(monkeypatch):
    if not hasattr(app.state, "thread_pool") or app.state.thread_pool is None:
        app.state.thread_pool = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="test-worker"
        )

    monkeypatch.setattr(
        knowledge_routes, "get_cached_query_embedding", lambda query: [0.1, 0.2, 0.3]
    )

    calls = {
        "add": AsyncMock(return_value=["knowledge:id"]),
        "query": AsyncMock(
            return_value=[
                (
                    Document(
                        page_content="Retention is seven years.",
                        metadata={
                            "ownerId": "user_123",
                            "tenantId": "tenant_a",
                            "knowledgeSpaceId": "space_123",
                            "knowledgeSpaceName": "Company Docs",
                            "documentId": "doc_123",
                            "fileId": "file_123",
                            "filename": "policy.pdf",
                            "chunkHash": "hash_123",
                            "page": 12,
                            "section": "Retention",
                            "status": "ready",
                        },
                    ),
                    0.1,
                )
            ]
        ),
        "delete": AsyncMock(return_value=None),
    }

    async def fake_add_documents(documents, ids=None, executor=None):
        return await calls["add"](documents, ids=ids, executor=executor)

    async def fake_query(embedding, k=4, filter=None, executor=None):
        return await calls["query"](embedding, k=k, filter=filter, executor=executor)

    async def fake_delete(filter, executor=None):
        return await calls["delete"](filter, executor=executor)

    monkeypatch.setattr(knowledge_routes, "add_documents_to_vector_store", fake_add_documents)
    monkeypatch.setattr(knowledge_routes, "query_vector_store", fake_query)
    monkeypatch.setattr(knowledge_routes, "delete_by_metadata_filter", fake_delete)
    return calls


def _index_payload(**metadata_overrides):
    metadata = {
        "ownerId": "user_123",
        "tenantId": "tenant_a",
        "knowledgeSpaceId": "space_123",
        "documentId": "doc_123",
        "fileId": "file_123",
        "chunkHash": "hash_123",
        "page": 12,
        "section": "Retention",
    }
    metadata.update(metadata_overrides)
    return {"text": "Retention is seven years.", "metadata": metadata}


def _query_payload(**overrides):
    payload = {
        "query": "What is retention?",
        "filters": {
            "ownerId": "user_123",
            "tenantId": "tenant_a",
            "knowledgeSpaceIds": ["space_123"],
            "documentIds": ["doc_123"],
            "statuses": ["ready", "ready_with_warnings"],
        },
        "topK": 12,
        "minRelevanceScore": 0.35,
        "maxChunksPerDocument": 4,
        "maxTotalChunks": 12,
    }
    payload.update(overrides)
    return payload


def test_knowledge_index_accepts_valid_chunk(knowledge_route_fakes):
    response = client.post("/knowledge/index", json=_index_payload())

    assert response.status_code == 200
    assert response.json() == {"status": "indexed", "chunkHash": "hash_123"}
    add_call = knowledge_route_fakes["add"].call_args
    docs = add_call.args[0]
    assert docs[0].page_content == "Retention is seven years."
    assert docs[0].metadata["ownerId"] == "user_123"
    assert add_call.kwargs["ids"] == [
        "knowledge:user_123:tenant_a:space_123:doc_123:file_123:hash_123"
    ]


@pytest.mark.parametrize(
    "field",
    ["ownerId", "knowledgeSpaceId", "documentId", "fileId", "chunkHash"],
)
def test_knowledge_index_rejects_missing_required_metadata(field):
    payload = _index_payload()
    del payload["metadata"][field]

    response = client.post("/knowledge/index", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["details"]["field"] == f"metadata.{field}"


def test_knowledge_index_requires_page_or_section():
    response = client.post(
        "/knowledge/index",
        json=_index_payload(page=None, section=None),
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"]["field"] == "metadata.page"


@pytest.mark.parametrize(
    "filters",
    [
        {"knowledgeSpaceIds": []},
        {"documentIds": []},
        {"statuses": ["draft"]},
    ],
)
def test_knowledge_query_rejects_invalid_filters(filters):
    payload = _query_payload()
    payload["filters"].update(filters)

    response = client.post("/knowledge/query", json=payload)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "field,value",
    [
        ("topK", 0),
        ("minRelevanceScore", 1.5),
        ("maxChunksPerDocument", 0),
        ("maxTotalChunks", 0),
    ],
)
def test_knowledge_query_rejects_invalid_limits(field, value):
    response = client.post("/knowledge/query", json=_query_payload(**{field: value}))

    assert response.status_code == 400
    assert response.json()["error"]["details"]["field"] == field


def test_knowledge_query_filters_and_formats_results(knowledge_route_fakes):
    response = client.post("/knowledge/query", json=_query_payload())

    assert response.status_code == 200
    assert response.json() == [
        {
            "knowledgeSpaceId": "space_123",
            "knowledgeSpaceName": "Company Docs",
            "documentId": "doc_123",
            "fileId": "file_123",
            "filename": "policy.pdf",
            "chunkText": "Retention is seven years.",
            "chunkHash": "hash_123",
            "page": 12,
            "section": "Retention",
            "score": 0.9,
        }
    ]
    query_call = knowledge_route_fakes["query"].call_args
    assert query_call.kwargs["k"] == 12
    assert query_call.kwargs["filter"] == {
        "ownerId": {"$eq": "user_123"},
        "tenantId": {"$eq": "tenant_a"},
        "knowledgeSpaceId": {"$in": ["space_123"]},
        "documentId": {"$in": ["doc_123"]},
        "status": {"$in": ["ready", "ready_with_warnings"]},
    }


def test_knowledge_delete_is_idempotent_and_scoped(knowledge_route_fakes):
    payload = {
        "ownerId": "user_123",
        "tenantId": "tenant_a",
        "knowledgeSpaceId": "space_123",
        "documentId": "doc_123",
        "fileId": "file_123",
    }

    response = client.post("/knowledge/delete", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "deleted": True,
        "documentId": "doc_123",
        "fileId": "file_123",
    }
    assert knowledge_route_fakes["delete"].call_args.args[0] == {
        "ownerId": {"$eq": "user_123"},
        "tenantId": {"$eq": "tenant_a"},
        "knowledgeSpaceId": {"$eq": "space_123"},
        "documentId": {"$eq": "doc_123"},
        "fileId": {"$eq": "file_123"},
    }
