import hashlib
from pydantic import ValidationError

from app.models import DocumentModel
from app.models import KnowledgeRetrievalRequest


def test_generate_digest():
    content = "Hello, World!"
    model = DocumentModel(page_content=content)
    expected_digest = hashlib.md5(content.encode()).hexdigest()
    assert model.generate_digest() == expected_digest


def test_knowledge_retrieval_request_defaults_defer_to_service_config():
    body = KnowledgeRetrievalRequest(query="What is the grade appeal procedure?")

    assert body.query == "What is the grade appeal procedure?"
    assert body.match_count is None
    assert body.match_threshold is None
    assert body.filter_document_type is None


def test_knowledge_retrieval_request_rejects_empty_query():
    try:
        KnowledgeRetrievalRequest(query="")
    except ValidationError as exc:
        assert "query" in str(exc)
    else:
        raise AssertionError("empty query must fail validation")


def test_knowledge_retrieval_request_rejects_whitespace_query():
    try:
        KnowledgeRetrievalRequest(query="   ")
    except ValidationError as exc:
        assert "query" in str(exc)
    else:
        raise AssertionError("whitespace-only query must fail validation")


def test_knowledge_retrieval_request_rejects_out_of_range_threshold():
    try:
        KnowledgeRetrievalRequest(query="hello", match_threshold=1.5)
    except ValidationError as exc:
        assert "match_threshold" in str(exc)
    else:
        raise AssertionError("threshold outside [-1.0, 1.0] must fail")


def test_blank_filter_document_type_normalizes_to_none():
    body = KnowledgeRetrievalRequest(query="hello", filter_document_type="   ")

    assert body.filter_document_type is None
