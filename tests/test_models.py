import hashlib
import pytest
from pydantic import ValidationError

from app.models import DocumentModel, QueryMultipleBody


def test_generate_digest():
    content = "Hello, World!"
    model = DocumentModel(page_content=content)
    expected_digest = hashlib.md5(content.encode()).hexdigest()
    assert model.generate_digest() == expected_digest


def test_query_multiple_body_trims_and_dedupes_values():
    body = QueryMultipleBody(
        query="  What changed?  ",
        file_ids=[" file-a ", "file-b", "file-a"],
        k=10,
        entity_id=" agent-456 ",
    )

    assert body.query == "What changed?"
    assert body.file_ids == ["file-a", "file-b"]
    assert body.k == 10
    assert body.entity_id == "agent-456"


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "   ", "file_ids": ["file-a"]},
        {"query": "question", "file_ids": []},
        {"query": "question", "file_ids": ["file-a", " "]},
        {"query": "question", "file_ids": [f"file-{i}" for i in range(51)]},
        {"query": "question", "file_ids": ["file-a"], "k": 0},
        {"query": "question", "file_ids": ["file-a"], "k": 51},
        {"query": "question", "file_ids": ["file-a"], "entity_id": "   "},
    ],
)
def test_query_multiple_body_rejects_contract_violations(payload):
    with pytest.raises(ValidationError):
        QueryMultipleBody(**payload)
