import hashlib

from langchain_core.documents import Document

from app.services.chunking.config import ChunkingConfig
from app.services.chunking.factory import get_chunking_service
from app.services.chunking.auto import AutoChunkingService
from app.services.chunking.recursive import RecursiveChunkingService


def _config() -> ChunkingConfig:
    return ChunkingConfig(
        preset="legacy",
        strategy="recursive",
        chunk_size=20,
        chunk_overlap=5,
        min_chunk_size=1,
        max_chunk_size=30,
        preserve_structure=False,
        contextual_prefix_enabled=False,
        max_contextual_prefix_length=240,
        max_heading_path_depth=4,
        max_metadata_string_length=512,
    )


def test_recursive_service_preserves_loader_metadata_and_system_keys_win():
    service = RecursiveChunkingService(_config())
    source = Document(
        page_content="alpha beta gamma delta epsilon zeta",
        metadata={"source": "note.txt", "file_id": "loader-file"},
    )

    chunks = service.split_documents([source], file_id="system-file", user_id="user-1")

    assert chunks
    assert all(chunk.metadata["file_id"] == "system-file" for chunk in chunks)
    assert all(chunk.metadata["user_id"] == "user-1" for chunk in chunks)
    assert all(chunk.metadata["source"] == "note.txt" for chunk in chunks)
    assert all(chunk.metadata["chunk_strategy"] == "recursive" for chunk in chunks)


def test_recursive_service_digest_uses_final_chunk_text():
    service = RecursiveChunkingService(_config())
    chunks = service.split_documents(
        [Document(page_content="alpha beta gamma", metadata={})],
        file_id="file-1",
        user_id="user-1",
    )

    for chunk in chunks:
        expected = hashlib.md5(
            chunk.page_content.encode("utf-8", "ignore")
        ).hexdigest()
        assert chunk.metadata["digest"] == expected


def test_recursive_service_uses_sanitized_loader_metadata():
    config = _config()
    service = RecursiveChunkingService(config)
    chunks = service.split_documents(
        [
            Document(
                page_content="alpha beta gamma",
                metadata={"file_id": "loader", "filename": "abc.txt", "bad": object()},
            )
        ],
        file_id="system",
        user_id="user-1",
    )

    assert chunks[0].metadata["file_id"] == "system"
    assert chunks[0].metadata["filename"] == "abc.txt"
    assert "bad" not in chunks[0].metadata


def test_factory_returns_recursive_for_recursive_strategy():
    service = get_chunking_service(_config())

    assert isinstance(service, RecursiveChunkingService)


def test_factory_returns_auto_for_auto_strategy():
    config = _config()
    config = type(config)(
        **{**config.__dict__, "strategy": "auto", "preset": "balanced"}
    )

    service = get_chunking_service(config)

    assert isinstance(service, AutoChunkingService)
