from langchain_core.documents import Document

from app.services.chunking.config import ChunkingConfig
from app.services.chunking.paragraph import ParagraphChunkingService


def _config() -> ChunkingConfig:
    return ChunkingConfig(
        preset="balanced",
        strategy="paragraph",
        chunk_size=45,
        chunk_overlap=5,
        min_chunk_size=10,
        max_chunk_size=60,
        preserve_structure=True,
        contextual_prefix_enabled=False,
        max_contextual_prefix_length=240,
        max_heading_path_depth=4,
        max_metadata_string_length=512,
    )


def test_paragraphs_are_grouped_without_splitting_short_paragraphs():
    docs = [
        Document(
            page_content="First paragraph.\n\nSecond paragraph.\n\nThird paragraph.",
            metadata={},
        )
    ]
    chunks = ParagraphChunkingService(_config()).split_documents(
        docs, "file-1", "user-1"
    )

    assert len(chunks) == 2
    assert "First paragraph." in chunks[0].page_content
    assert "Second paragraph." in chunks[0].page_content
    assert "Third paragraph." in chunks[1].page_content


def test_long_paragraph_falls_back_to_recursive():
    docs = [Document(page_content="x" * 100, metadata={})]
    chunks = ParagraphChunkingService(_config()).split_documents(
        docs, "file-1", "user-1"
    )

    assert len(chunks) > 1
    assert all(chunk.metadata["chunk_strategy"] == "recursive_fallback" for chunk in chunks)
