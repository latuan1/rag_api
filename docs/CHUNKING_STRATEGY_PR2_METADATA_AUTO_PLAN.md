# Metadata Normalization and Core Auto Chunking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add additive metadata normalization, deterministic contextual prefixes, paragraph chunking, and core `auto` fallback behavior while keeping ingestion successful.

**Architecture:** Build on PR 1's `app/services/chunking/` service boundary. Add focused metadata and prefix helpers, then introduce paragraph-aware chunking and an `auto` service that uses reliable loader metadata when available and falls back to recursive chunking with explicit metadata when structure cannot be preserved. Routes should continue to call the same chunking service interface.

**Tech Stack:** Python, FastAPI, LangChain `Document`, LangChain text splitters, pytest.

---

## Files

- Create: `app/services/chunking/metadata.py`
- Create: `app/services/chunking/paragraph.py`
- Create: `app/services/chunking/auto.py`
- Modify: `app/services/chunking/factory.py`
- Modify: `app/services/chunking/recursive.py`
- Modify: `app/services/chunking/__init__.py`
- Create: `tests/services/test_chunking_metadata.py`
- Create: `tests/services/test_chunking_paragraph.py`
- Create: `tests/services/test_chunking_auto.py`
- Modify: `tests/services/test_chunking_recursive.py`
- Modify: `tests/test_main.py`

## Task 1: Add Metadata Sanitization and Protected Merge

- [ ] **Step 1: Write failing metadata tests**

Create `tests/services/test_chunking_metadata.py`:

```python
from app.services.chunking.config import ChunkingConfig
from app.services.chunking.metadata import merge_chunk_metadata, sanitize_metadata


def _config() -> ChunkingConfig:
    return ChunkingConfig(
        preset="balanced",
        strategy="auto",
        chunk_size=1200,
        chunk_overlap=120,
        min_chunk_size=300,
        max_chunk_size=2200,
        preserve_structure=True,
        contextual_prefix_enabled=True,
        max_contextual_prefix_length=40,
        max_heading_path_depth=2,
        max_metadata_string_length=12,
    )


def test_sanitize_metadata_truncates_strings_and_heading_path():
    metadata = sanitize_metadata(
        {
            "filename": "very-long-filename.pdf",
            "heading_path": ["A", "B", "C"],
            "bad": object(),
            "nested": {"drop": "me"},
        },
        _config(),
    )

    assert metadata["filename"] == "very-long-fi"
    assert metadata["heading_path"] == ["A", "B"]
    assert "bad" not in metadata
    assert "nested" not in metadata


def test_merge_chunk_metadata_protected_system_keys_win():
    merged = merge_chunk_metadata(
        loader_metadata={"file_id": "loader", "user_id": "loader-user", "source": "a.md"},
        system_metadata={"file_id": "system", "user_id": "system-user", "digest": "abc"},
        config=_config(),
    )

    assert merged["file_id"] == "system"
    assert merged["user_id"] == "system-user"
    assert merged["digest"] == "abc"
    assert merged["source"] == "a.md"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/services/test_chunking_metadata.py -v
```

Expected: FAIL because `metadata.py` does not exist.

- [ ] **Step 3: Implement metadata helpers**

Create `app/services/chunking/metadata.py`:

```python
from typing import Any, Dict, Mapping

from app.services.chunking.config import ChunkingConfig


PROTECTED_METADATA_KEYS = {"file_id", "user_id", "digest"}
SCALAR_TYPES = (str, int, float, bool, type(None))


def _sanitize_value(key: str, value: Any, config: ChunkingConfig):
    if isinstance(value, str):
        return value[: config.max_metadata_string_length]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if key == "heading_path" and isinstance(value, list):
        sanitized = [str(item)[: config.max_metadata_string_length] for item in value]
        return sanitized[: config.max_heading_path_depth]
    return None


def sanitize_metadata(
    metadata: Mapping[str, Any], config: ChunkingConfig
) -> Dict[str, Any]:
    sanitized = {}
    for key, value in (metadata or {}).items():
        clean_key = str(key)[: config.max_metadata_string_length]
        clean_value = _sanitize_value(clean_key, value, config)
        if clean_value is not None:
            sanitized[clean_key] = clean_value
    return sanitized


def merge_chunk_metadata(
    loader_metadata: Mapping[str, Any],
    system_metadata: Mapping[str, Any],
    config: ChunkingConfig,
) -> Dict[str, Any]:
    loader = sanitize_metadata(loader_metadata or {}, config)
    for key in PROTECTED_METADATA_KEYS:
        loader.pop(key, None)
    return {**loader, **system_metadata}
```

- [ ] **Step 4: Run metadata tests**

Run:

```powershell
pytest tests/services/test_chunking_metadata.py -v
```

Expected: PASS.

## Task 2: Add Deterministic Contextual Prefixes

- [ ] **Step 1: Extend metadata tests**

Append to `tests/services/test_chunking_metadata.py`:

```python
from app.services.chunking.metadata import build_context_prefix


def test_context_prefix_uses_available_fields_deterministically():
    prefix = build_context_prefix(
        {
            "filename": "README.md",
            "heading_path": ["Installation", "Docker"],
            "page_start": 3,
            "page_end": 4,
        },
        _config(),
    )

    assert prefix == "[Context: file=README.md; section=Installation > Docker; pages=3-4]\n\n"[:42]


def test_context_prefix_is_empty_without_useful_context():
    assert build_context_prefix({"chunk_index": 1}, _config()) == ""
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/services/test_chunking_metadata.py -v
```

Expected: FAIL because `build_context_prefix` does not exist.

- [ ] **Step 3: Implement prefix helper**

Add to `app/services/chunking/metadata.py`:

```python
def build_context_prefix(metadata: Mapping[str, Any], config: ChunkingConfig) -> str:
    parts = []
    filename = metadata.get("filename") or metadata.get("source")
    if filename:
        parts.append(f"file={filename}")

    heading_path = metadata.get("heading_path")
    if heading_path:
        parts.append("section=" + " > ".join(str(item) for item in heading_path))
    elif metadata.get("section_title"):
        parts.append(f"section={metadata['section_title']}")

    if metadata.get("page_start") is not None:
        page_start = metadata["page_start"]
        page_end = metadata.get("page_end", page_start)
        parts.append(f"pages={page_start}-{page_end}" if page_start != page_end else f"page={page_start}")

    if metadata.get("slide_start") is not None:
        slide_start = metadata["slide_start"]
        slide_end = metadata.get("slide_end", slide_start)
        parts.append(
            f"slides={slide_start}-{slide_end}"
            if slide_start != slide_end
            else f"slide={slide_start}"
        )

    if metadata.get("sheet_name"):
        parts.append(f"sheet={metadata['sheet_name']}")

    if metadata.get("row_start") is not None:
        row_start = metadata["row_start"]
        row_end = metadata.get("row_end", row_start)
        parts.append(f"rows={row_start}-{row_end}" if row_start != row_end else f"row={row_start}")

    if not parts:
        return ""

    prefix = "[Context: " + "; ".join(parts) + "]\n\n"
    return prefix[: config.max_contextual_prefix_length]
```

- [ ] **Step 4: Run metadata tests**

Run:

```powershell
pytest tests/services/test_chunking_metadata.py -v
```

Expected: PASS.

## Task 3: Update Recursive Service to Use Metadata Helpers

- [ ] **Step 1: Add recursive collision and digest tests**

In `tests/services/test_chunking_recursive.py`, assert:

```python
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
```

- [ ] **Step 2: Refactor `recursive.py`**

Replace local protected-key merge logic with `merge_chunk_metadata()`. Keep digest generation based on final `page_content`. Preserve `chunk_strategy`, `chunking_preset`, `chunk_index`, `chunk_size`, and `contextual_prefix_enabled=False`.

- [ ] **Step 3: Run recursive tests**

Run:

```powershell
pytest tests/services/test_chunking_recursive.py -v
```

Expected: PASS.

## Task 4: Add Paragraph-Aware Chunking

- [ ] **Step 1: Write failing paragraph tests**

Create `tests/services/test_chunking_paragraph.py`:

```python
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
    docs = [Document(page_content="First paragraph.\n\nSecond paragraph.\n\nThird paragraph.", metadata={})]
    chunks = ParagraphChunkingService(_config()).split_documents(docs, "file-1", "user-1")

    assert len(chunks) == 2
    assert "First paragraph." in chunks[0].page_content
    assert "Second paragraph." in chunks[0].page_content
    assert "Third paragraph." in chunks[1].page_content


def test_long_paragraph_falls_back_to_recursive():
    docs = [Document(page_content="x" * 100, metadata={})]
    chunks = ParagraphChunkingService(_config()).split_documents(docs, "file-1", "user-1")

    assert len(chunks) > 1
    assert all(chunk.metadata["chunk_strategy"] == "recursive_fallback" for chunk in chunks)
```

- [ ] **Step 2: Run paragraph tests to verify failure**

Run:

```powershell
pytest tests/services/test_chunking_paragraph.py -v
```

Expected: FAIL because `paragraph.py` does not exist.

- [ ] **Step 3: Implement paragraph service**

Create `app/services/chunking/paragraph.py` with:

- Split each source document on blank-line paragraph boundaries.
- Accumulate paragraphs until adding another would exceed `chunk_size`.
- Merge a final chunk shorter than `min_chunk_size` into the previous chunk when possible.
- If a paragraph exceeds `max_chunk_size`, call `RecursiveChunkingService(config, strategy_name="recursive_fallback")` for that source document.
- Use `merge_chunk_metadata()` for final metadata and generate digest from final chunk text.

- [ ] **Step 4: Run paragraph tests**

Run:

```powershell
pytest tests/services/test_chunking_paragraph.py -v
```

Expected: PASS.

## Task 5: Add Core Auto Service with Prefixes and Fallback

- [ ] **Step 1: Write failing auto tests**

Create `tests/services/test_chunking_auto.py`:

```python
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
        max_contextual_prefix_length=50,
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

    assert chunks[0].page_content.startswith("[Context: file=README.md; section=Install > Docker]")
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
```

- [ ] **Step 2: Run auto tests to verify failure**

Run:

```powershell
pytest tests/services/test_chunking_auto.py -v
```

Expected: FAIL because `auto.py` does not exist.

- [ ] **Step 3: Implement auto service**

Create `app/services/chunking/auto.py`:

- `split_documents()` calls `_split_with_structure()`.
- `_split_with_structure()` uses paragraph grouping as the MVP core behavior.
- For each final body chunk, build sanitized metadata, build one prefix, reserve prefix length from `max_chunk_size`, and trim body only if needed.
- Add `context_prefix` metadata only when a non-empty prefix is prepended.
- If structure processing raises, log a warning and call `RecursiveChunkingService(config, strategy_name="recursive_fallback")`.
- For PDF metadata, normalize loader `page` into `page_start` and `page_end`; never merge chunks across more than two adjacent pages.

- [ ] **Step 4: Wire factory**

Modify `app/services/chunking/factory.py`:

```python
from app.services.chunking.auto import AutoChunkingService
from app.services.chunking.paragraph import ParagraphChunkingService
from app.services.chunking.recursive import RecursiveChunkingService


def get_chunking_service(config):
    if config.strategy == "auto":
        return AutoChunkingService(config)
    if config.strategy == "paragraph":
        return ParagraphChunkingService(config)
    if config.strategy == "recursive":
        return RecursiveChunkingService(config)
    logger.warning("Unknown chunking strategy '%s'; falling back to recursive", config.strategy)
    return RecursiveChunkingService(config, strategy_name="recursive")
```

- [ ] **Step 5: Run service tests**

Run:

```powershell
pytest tests/services/test_chunking_metadata.py tests/services/test_chunking_paragraph.py tests/services/test_chunking_auto.py tests/services/test_chunking_recursive.py -v
```

Expected: PASS.

## Task 6: Integration Safety

- [ ] **Step 1: Confirm `/text` remains raw extraction**

Run:

```powershell
pytest tests/test_main.py::test_extract_text_from_file -v
```

Expected: PASS. `/text` should not call the chunking service and should not add contextual prefixes.

- [ ] **Step 2: Confirm embed endpoints still work**

Run:

```powershell
pytest tests/test_main.py::test_embed_file tests/test_main.py::test_embed_local_file tests/test_main.py::test_embed_file_upload -v
```

Expected: PASS.

- [ ] **Step 3: Run focused PR 2 verification**

Run:

```powershell
pytest tests/services -v
pytest tests/test_main.py -v
pytest tests/test_upload_isolation.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

Run:

```powershell
git add app/services/chunking tests/services/test_chunking_metadata.py tests/services/test_chunking_paragraph.py tests/services/test_chunking_auto.py tests/services/test_chunking_recursive.py tests/test_main.py
git commit -m "feat: add metadata-aware auto chunking"
```

Expected: commit succeeds. Do not include Office/table refinements from PR 3 in this commit.
