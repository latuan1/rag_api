from langchain_core.documents import Document

from app.services.chunking.auto import AutoChunkingService
from app.services.chunking.config import ChunkingConfig


def _config() -> ChunkingConfig:
    return ChunkingConfig(
        preset="balanced",
        strategy="auto",
        chunk_size=80,
        chunk_overlap=10,
        min_chunk_size=10,
        max_chunk_size=100,
        preserve_structure=True,
        contextual_prefix_enabled=True,
        max_contextual_prefix_length=80,
        max_heading_path_depth=4,
        max_metadata_string_length=512,
    )


def test_auto_adds_context_prefix_when_metadata_is_available():
    docs = [
        Document(
            page_content="Install Docker and set environment variables.",
            metadata={"filename": "README.md", "heading_path": ["Install", "Docker"]},
        )
    ]

    chunks = AutoChunkingService(_config()).split_documents(docs, "file-1", "user-1")

    assert chunks[0].page_content.startswith(
        "[Context: file=README.md; section=Install > Docker]"
    )
    assert chunks[0].metadata["context_prefix"]
    assert chunks[0].metadata["chunk_strategy"] == "auto"


def test_prefix_counts_toward_chunk_size():
    chunks = AutoChunkingService(_config()).split_documents(
        [Document(page_content="body " * 40, metadata={"filename": "a.txt"})],
        "file-1",
        "user-1",
    )

    assert all(len(chunk.page_content) <= _config().max_chunk_size for chunk in chunks)


def test_auto_fallback_marks_recursive_fallback_on_failure(monkeypatch):
    service = AutoChunkingService(_config())

    def fail(*args, **kwargs):
        raise RuntimeError("structure failed")

    monkeypatch.setattr(service, "_split_with_structure", fail)
    chunks = service.split_documents(
        [Document(page_content="alpha beta gamma", metadata={})],
        "file-1",
        "user-1",
    )

    assert chunks
    assert all(chunk.metadata["chunk_strategy"] == "recursive_fallback" for chunk in chunks)
