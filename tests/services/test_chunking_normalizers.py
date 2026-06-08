from langchain_core.documents import Document

from app.services.chunking.normalizers import (
    normalize_document_metadata,
    normalize_documents,
)


def test_pdf_page_metadata_maps_to_page_start_and_page_end():
    doc = Document(page_content="page text", metadata={"source": "contract.pdf", "page": 3})

    normalized = normalize_document_metadata(doc)

    assert normalized["filename"] == "contract.pdf"
    assert normalized["page_start"] == 4
    assert normalized["page_end"] == 4


def test_markdown_heading_path_is_derived_from_element_metadata_when_available():
    doc = Document(
        page_content="Install text",
        metadata={
            "source": "README.md",
            "category": "NarrativeText",
            "parent_id": "h2",
            "heading_path": ["Installation", "Docker"],
        },
    )

    normalized = normalize_document_metadata(doc)

    assert normalized["heading_path"] == ["Installation", "Docker"]
    assert normalized["section_title"] == "Docker"


def test_unreliable_fields_are_omitted():
    doc = Document(page_content="sheet text", metadata={"source": "report.xlsx"})

    normalized = normalize_document_metadata(doc)

    assert "sheet_name" not in normalized
    assert "row_start" not in normalized
    assert "row_end" not in normalized


def test_normalize_documents_preserves_content():
    docs = [Document(page_content="alpha", metadata={"source": "a.txt"})]

    normalized = normalize_documents(docs)

    assert normalized[0].page_content == "alpha"
    assert normalized[0].metadata["filename"] == "a.txt"
