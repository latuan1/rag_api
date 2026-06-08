# Office and Structured File Chunking Refinements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine `auto` chunking for Office and structured files with reliable best-effort heading, row, table, slide, and block metadata.

**Architecture:** Extend the PR 2 chunking service with small normalizer modules that derive structure only when loader output makes it reliable. Table and row-aware chunking should operate on normalized row blocks, while slide, heading, and generic block behavior should preserve natural boundaries and omit uncertain metadata. Recursive fallback remains the final safety path.

**Tech Stack:** Python, FastAPI, LangChain `Document`, LangChain text splitters, pytest.

---

## Files

- Create: `app/services/chunking/normalizers.py`
- Create: `app/services/chunking/tables.py`
- Modify: `app/services/chunking/auto.py`
- Modify: `app/services/chunking/metadata.py`
- Modify: `app/services/chunking/__init__.py`
- Create: `tests/services/test_chunking_normalizers.py`
- Create: `tests/services/test_chunking_tables.py`
- Modify: `tests/services/test_chunking_auto.py`
- Modify: `tests/utils/test_lazy_load.py`
- Modify: `tests/test_main.py`

## Task 1: Add Reliable Metadata Normalizers

- [ ] **Step 1: Write failing normalizer tests**

Create `tests/services/test_chunking_normalizers.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/services/test_chunking_normalizers.py -v
```

Expected: FAIL because `normalizers.py` does not exist.

- [ ] **Step 3: Implement normalizers**

Create `app/services/chunking/normalizers.py`:

- `normalize_document_metadata(doc)` copies metadata and adds `filename` from `source` basename when available.
- Map zero-based loader `page` to one-based `page_start` and `page_end`.
- Preserve existing `heading_path` only if it is a non-empty list of strings.
- Set `section_title` to the final heading path item when heading path is reliable.
- Preserve `slide_start`, `slide_end`, `sheet_name`, `row_start`, and `row_end` only when loader metadata already provides them as stable scalar values.
- `normalize_documents(docs)` returns new `Document` objects and does not mutate original documents.

- [ ] **Step 4: Run normalizer tests**

Run:

```powershell
pytest tests/services/test_chunking_normalizers.py -v
```

Expected: PASS.

## Task 2: Add Row-Aware Table Chunking

- [ ] **Step 1: Write failing table tests**

Create `tests/services/test_chunking_tables.py`:

```python
from app.services.chunking.config import ChunkingConfig
from app.services.chunking.tables import split_table_rows


def _config() -> ChunkingConfig:
    return ChunkingConfig(
        preset="balanced",
        strategy="auto",
        chunk_size=80,
        chunk_overlap=0,
        min_chunk_size=10,
        max_chunk_size=120,
        preserve_structure=True,
        contextual_prefix_enabled=True,
        max_contextual_prefix_length=40,
        max_heading_path_depth=4,
        max_metadata_string_length=32,
    )


def test_large_table_splits_by_row_ranges_and_repeats_header():
    rows = [
        ["Month", "Region", "Revenue"],
        ["Jan", "APAC", "100"],
        ["Feb", "EMEA", "200"],
        ["Mar", "NA", "300"],
        ["Apr", "LATAM", "400"],
    ]

    chunks = split_table_rows(
        rows,
        metadata={"filename": "report.xlsx", "sheet_name": "Revenue", "row_start": 1},
        config=_config(),
    )

    assert len(chunks) > 1
    assert all("Month | Region | Revenue" in chunk.page_content for chunk in chunks)
    assert chunks[0].metadata["row_start"] == 2
    assert chunks[-1].metadata["row_end"] == 5


def test_oversized_header_is_capped():
    rows = [["very-long-column-name-" * 20], ["value-1"], ["value-2"]]

    chunks = split_table_rows(rows, {"filename": "wide.csv", "row_start": 1}, _config())

    assert chunks
    assert all(len(chunk.page_content) <= _config().max_chunk_size for chunk in chunks)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/services/test_chunking_tables.py -v
```

Expected: FAIL because `tables.py` does not exist.

- [ ] **Step 3: Implement table helpers**

Create `app/services/chunking/tables.py`:

- Accept `rows: list[list[str]]`, `metadata`, and `config`.
- Render rows as simple pipe-delimited text.
- Treat first row as header.
- Repeat sanitized header in each chunk.
- Track data row ranges in one-based metadata using `row_start` and `row_end`.
- Keep each chunk within `max_chunk_size` when possible.
- If the sanitized header alone is too large, replace it with a compact column summary like `Columns: col1, col2, ...` capped by available budget.
- Return `Document` objects with row metadata; final digest and system metadata are added later by `auto`.

- [ ] **Step 4: Run table tests**

Run:

```powershell
pytest tests/services/test_chunking_tables.py -v
```

Expected: PASS.

## Task 3: Integrate Normalizers into Auto Chunking

- [ ] **Step 1: Extend auto tests for normalized metadata**

Append to `tests/services/test_chunking_auto.py`:

```python
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
```

- [ ] **Step 2: Run auto tests to verify failure or missing behavior**

Run:

```powershell
pytest tests/services/test_chunking_auto.py -v
```

Expected: FAIL if `auto.py` does not normalize page fields or incorrectly guesses spreadsheet metadata.

- [ ] **Step 3: Update `auto.py`**

Modify `AutoChunkingService` to:

- Call `normalize_documents()` before structure processing.
- Group PDF chunks by adjacent page metadata and never allow a final chunk to span more than two pages.
- Preserve heading, slide, sheet, row, and filename metadata only when normalizer provides it.
- Keep paragraph-aware fallback for flat text.
- Keep recursive fallback on exceptions with warning and `chunk_strategy="recursive_fallback"`.

- [ ] **Step 4: Run auto and normalizer tests**

Run:

```powershell
pytest tests/services/test_chunking_auto.py tests/services/test_chunking_normalizers.py -v
```

Expected: PASS.

## Task 4: Add CSV/XLS/XLSX Row-Aware Path

- [ ] **Step 1: Add row-aware auto test**

Append to `tests/services/test_chunking_auto.py`:

```python
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
```

- [ ] **Step 2: Update `auto.py` row-aware handling**

When a normalized document has `row_start` and table-like pipe-delimited content:

- Parse rows by newline and pipe delimiters.
- Call `split_table_rows()`.
- Add contextual prefixes and final system metadata through the same finalization path used by paragraph chunks.
- Use recursive fallback only when row parsing fails.

- [ ] **Step 3: Run table and auto tests**

Run:

```powershell
pytest tests/services/test_chunking_tables.py tests/services/test_chunking_auto.py -v
```

Expected: PASS.

## Task 5: Add Slide and Heading-Aware Best Effort

- [ ] **Step 1: Add slide and heading tests**

Append to `tests/services/test_chunking_auto.py`:

```python
def test_auto_preserves_slide_boundaries_when_reliable():
    docs = [
        Document(page_content="Title\nBody", metadata={"source": "deck.pptx", "slide_start": 1, "slide_end": 1}),
        Document(page_content="Next\nBody", metadata={"source": "deck.pptx", "slide_start": 2, "slide_end": 2}),
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
```

- [ ] **Step 2: Update `auto.py`**

Implement:

- One final chunk per slide document when `slide_start` and `slide_end` are reliable and body fits within `max_chunk_size`.
- Recursive fallback within a slide only when slide body exceeds `max_chunk_size`.
- Heading-aware paragraph chunks preserve heading metadata and prefix context.

- [ ] **Step 3: Run auto tests**

Run:

```powershell
pytest tests/services/test_chunking_auto.py -v
```

Expected: PASS.

## Task 6: Loader and Endpoint Regression Coverage

- [ ] **Step 1: Extend loader metadata inventory tests**

In `tests/utils/test_lazy_load.py`, add assertions that supported loaders still return `Document` objects and that any structure metadata checks are conditional. Do not require unreliable metadata from DOCX, XLSX, PPTX, XML, RST, or EPUB loaders.

- [ ] **Step 2: Confirm raw `/text` behavior**

Run:

```powershell
pytest tests/test_main.py::test_extract_text_from_file -v
```

Expected: PASS. The response text has no `[Context:` prefix.

- [ ] **Step 3: Run full focused verification**

Run:

```powershell
pytest tests/test_config.py -v
pytest tests/services -v
pytest tests/utils -v
pytest tests/test_main.py -v
pytest tests/test_upload_isolation.py -v
```

Expected: PASS.

- [ ] **Step 4: Run integration tests when database services are available**

Run:

```powershell
pytest tests/integration -v
```

Expected: PASS when the configured integration database is running. If skipped or failed because services are unavailable, record the exact reason in the PR notes.

