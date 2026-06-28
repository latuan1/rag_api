from langchain_core.documents import Document

from app.routes.document_routes import _prepare_documents_sync
from app.utils.document_locations import annotate_loaded_documents


def test_annotate_loaded_documents_adds_text_line_range(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("Line one\nLine two\nLine three\n", encoding="utf-8")
    docs = [Document(page_content="Line one\nLine two\nLine three")]

    annotated = annotate_loaded_documents(docs, str(source), "txt")

    assert annotated[0].metadata["start_line"] == 1
    assert annotated[0].metadata["end_line"] == 3


def test_prepare_documents_sync_adds_narrower_chunk_line_ranges():
    docs = [
        Document(
            page_content="Alpha\nBeta\nGamma",
            metadata={"source": "notes.txt", "start_line": 10, "end_line": 12},
        )
    ]

    prepared = _prepare_documents_sync(
        docs,
        file_id="file-a",
        user_id="user-1",
        clean_content=False,
        chunk_size=7,
        chunk_overlap=0,
    )

    line_ranges = {
        (doc.metadata["start_line"], doc.metadata["end_line"]) for doc in prepared
    }
    assert (10, 10) in line_ranges
    assert (11, 11) in line_ranges
    assert (12, 12) in line_ranges


def test_annotate_loaded_documents_adds_markdown_section(tmp_path):
    source = tmp_path / "handbook.md"
    source.write_text(
        "# Intro\n\n"
        "Welcome.\n\n"
        "## Publishing checklist\n\n"
        "Manager approval is required.\n",
        encoding="utf-8",
    )
    docs = [Document(page_content="Manager approval is required.")]

    annotated = annotate_loaded_documents(docs, str(source), "md")

    assert annotated[0].metadata["start_line"] == 7
    assert annotated[0].metadata["end_line"] == 7
    assert annotated[0].metadata["section"] == "Publishing checklist"


def test_annotate_loaded_documents_leaves_unsupported_metadata_unchanged(tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_text("not a real pdf", encoding="utf-8")
    docs = [Document(page_content="page text", metadata={"source": "scan.pdf", "page": 2})]

    annotated = annotate_loaded_documents(docs, str(source), "pdf")

    assert annotated[0].metadata == {"source": "scan.pdf", "page": 2}


def test_annotate_loaded_documents_ignores_mapping_failures(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("Original text", encoding="utf-8")
    docs = [Document(page_content="Different text", metadata={"source": "notes.txt"})]

    annotated = annotate_loaded_documents(docs, str(source), "txt")

    assert annotated[0].metadata == {"source": "notes.txt"}
