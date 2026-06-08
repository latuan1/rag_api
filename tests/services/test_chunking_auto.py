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


def test_auto_pdf_chunks_never_span_more_than_two_pages():
    docs = [
        Document(page_content="page one " * 4, metadata={"source": "a.pdf", "page": 0}),
        Document(page_content="page two " * 4, metadata={"source": "a.pdf", "page": 1}),
        Document(page_content="page three " * 4, metadata={"source": "a.pdf", "page": 2}),
    ]

    chunks = AutoChunkingService(_config()).split_documents(docs, "file-1", "user-1")

    for chunk in chunks:
        if "page_start" in chunk.metadata and "page_end" in chunk.metadata:
            assert chunk.metadata["page_end"] - chunk.metadata["page_start"] <= 1


def test_auto_omits_unreliable_spreadsheet_metadata():
    chunks = AutoChunkingService(_config()).split_documents(
        [Document(page_content="flat spreadsheet text", metadata={"source": "report.xlsx"})],
        "file-1",
        "user-1",
    )

    assert "sheet_name" not in chunks[0].metadata
    assert "row_start" not in chunks[0].metadata


def test_auto_preserves_reliable_sheet_and_row_ranges():
    docs = [
        Document(
            page_content="Month | Region | Revenue\nJan | APAC | 100\nFeb | EMEA | 200",
            metadata={
                "source": "report.xlsx",
                "sheet_name": "Revenue",
                "row_start": 1,
                "row_end": 3,
            },
        )
    ]

    chunks = AutoChunkingService(_config()).split_documents(docs, "file-1", "user-1")

    assert chunks[0].metadata["sheet_name"] == "Revenue"
    assert chunks[0].metadata["row_start"] >= 1
    assert chunks[0].metadata["row_end"] >= chunks[0].metadata["row_start"]
    assert "sheet=Revenue" in chunks[0].page_content


def test_auto_preserves_slide_boundaries_when_reliable():
    docs = [
        Document(
            page_content="Title\nBody",
            metadata={"source": "deck.pptx", "slide_start": 1, "slide_end": 1},
        ),
        Document(
            page_content="Next\nBody",
            metadata={"source": "deck.pptx", "slide_start": 2, "slide_end": 2},
        ),
    ]

    chunks = AutoChunkingService(_config()).split_documents(docs, "file-1", "user-1")

    assert len(chunks) == 2
    assert chunks[0].metadata["slide_start"] == 1
    assert "slide=1" in chunks[0].page_content


def test_auto_preserves_heading_context_when_reliable():
    chunks = AutoChunkingService(_config()).split_documents(
        [
            Document(
                page_content="Docker setup details",
                metadata={"source": "README.md", "heading_path": ["Install", "Docker"]},
            )
        ],
        "file-1",
        "user-1",
    )

    assert chunks[0].metadata["section_title"] == "Docker"
    assert "section=Install > Docker" in chunks[0].page_content
