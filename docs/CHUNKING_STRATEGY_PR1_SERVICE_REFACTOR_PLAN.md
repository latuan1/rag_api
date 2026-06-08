# Chunking Service Refactor and Balanced Preset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract current recursive chunking into a dedicated service and add rollback-friendly chunking preset resolution without changing the public RAG API contract.

**Architecture:** Keep ingestion routes responsible for loading files, authorization, and vector-store insertion, but delegate chunk preparation to `app/services/chunking/`. Configuration is resolved centrally in `app/config.py`, with `legacy` preserving current recursive `1500/100` behavior and unset env using the new balanced `auto` `1200/120` defaults. PR 1 may route `auto` through the recursive implementation until PR 2 adds structure-aware behavior.

**Tech Stack:** Python, FastAPI, LangChain `Document`, LangChain text splitters, pytest.

---

## Files

- Create: `app/services/chunking/__init__.py`
- Create: `app/services/chunking/config.py`
- Create: `app/services/chunking/recursive.py`
- Create: `app/services/chunking/factory.py`
- Modify: `app/config.py`
- Modify: `app/routes/document_routes.py`
- Create: `tests/services/test_chunking_config.py`
- Create: `tests/services/test_chunking_recursive.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_upload_isolation.py`
- Modify: `tests/test_main.py`

## Task 1: Add Chunking Config Resolution

- [ ] **Step 1: Write failing config tests**

Create `tests/services/test_chunking_config.py` with tests for unset env, `balanced`, `legacy`, explicit strategy, and explicit sizing:

```python
from app.services.chunking.config import resolve_chunking_config


def test_unset_env_uses_balanced_defaults():
    config = resolve_chunking_config({})

    assert config.preset == "balanced"
    assert config.strategy == "auto"
    assert config.chunk_size == 1200
    assert config.chunk_overlap == 120


def test_legacy_preset_uses_recursive_defaults():
    config = resolve_chunking_config({"CHUNKING_PRESET": "legacy"})

    assert config.preset == "legacy"
    assert config.strategy == "recursive"
    assert config.chunk_size == 1500
    assert config.chunk_overlap == 100
    assert config.contextual_prefix_enabled is False


def test_explicit_strategy_wins_over_preset_strategy():
    config = resolve_chunking_config(
        {"CHUNKING_PRESET": "legacy", "CHUNKING_STRATEGY": "auto"}
    )

    assert config.preset == "legacy"
    assert config.strategy == "auto"
    assert config.chunk_size == 1500
    assert config.chunk_overlap == 100


def test_explicit_size_and_overlap_win_over_preset_defaults():
    config = resolve_chunking_config(
        {
            "CHUNKING_PRESET": "balanced",
            "CHUNK_SIZE": "1800",
            "CHUNK_OVERLAP": "90",
        }
    )

    assert config.strategy == "auto"
    assert config.chunk_size == 1800
    assert config.chunk_overlap == 90
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/services/test_chunking_config.py -v
```

Expected: FAIL because `app.services.chunking.config` does not exist.

- [ ] **Step 3: Implement config module**

Create `app/services/chunking/config.py`:

```python
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class ChunkingConfig:
    preset: str
    strategy: str
    chunk_size: int
    chunk_overlap: int
    min_chunk_size: int
    max_chunk_size: int
    preserve_structure: bool
    contextual_prefix_enabled: bool
    max_contextual_prefix_length: int
    max_heading_path_depth: int
    max_metadata_string_length: int


def _env(env: Mapping[str, str], key: str) -> Optional[str]:
    value = env.get(key)
    return None if value in (None, "") else value


def _bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.lower() in {"true", "1", "yes", "on"}


def _int(value: Optional[str], default: int) -> int:
    return int(value) if value is not None else default


def resolve_chunking_config(env: Mapping[str, str]) -> ChunkingConfig:
    preset = (_env(env, "CHUNKING_PRESET") or "balanced").lower()

    if preset == "legacy":
        preset_strategy = "recursive"
        default_size = 1500
        default_overlap = 100
        default_prefix = False
    else:
        preset = "balanced"
        preset_strategy = "auto"
        default_size = 1200
        default_overlap = 120
        default_prefix = True

    strategy = (_env(env, "CHUNKING_STRATEGY") or preset_strategy).lower()

    return ChunkingConfig(
        preset=preset,
        strategy=strategy,
        chunk_size=_int(_env(env, "CHUNK_SIZE"), default_size),
        chunk_overlap=_int(_env(env, "CHUNK_OVERLAP"), default_overlap),
        min_chunk_size=_int(_env(env, "MIN_CHUNK_SIZE"), 300),
        max_chunk_size=_int(_env(env, "MAX_CHUNK_SIZE"), 2200),
        preserve_structure=_bool(_env(env, "PRESERVE_STRUCTURE"), True),
        contextual_prefix_enabled=_bool(
            _env(env, "CONTEXTUAL_PREFIX_ENABLED"), default_prefix
        ),
        max_contextual_prefix_length=_int(
            _env(env, "MAX_CONTEXTUAL_PREFIX_LENGTH"), 240
        ),
        max_heading_path_depth=_int(_env(env, "MAX_HEADING_PATH_DEPTH"), 4),
        max_metadata_string_length=_int(_env(env, "MAX_METADATA_STRING_LENGTH"), 512),
    )
```

- [ ] **Step 4: Run config tests**

Run:

```powershell
pytest tests/services/test_chunking_config.py -v
```

Expected: PASS.

- [ ] **Step 5: Wire config into `app/config.py`**

Import `resolve_chunking_config`, call it with `os.environ`, and expose:

```python
from app.services.chunking.config import resolve_chunking_config

CHUNKING_CONFIG = resolve_chunking_config(os.environ)
CHUNKING_PRESET = CHUNKING_CONFIG.preset
CHUNKING_STRATEGY = CHUNKING_CONFIG.strategy
CHUNK_SIZE = CHUNKING_CONFIG.chunk_size
CHUNK_OVERLAP = CHUNKING_CONFIG.chunk_overlap
MIN_CHUNK_SIZE = CHUNKING_CONFIG.min_chunk_size
MAX_CHUNK_SIZE = CHUNKING_CONFIG.max_chunk_size
PRESERVE_STRUCTURE = CHUNKING_CONFIG.preserve_structure
CONTEXTUAL_PREFIX_ENABLED = CHUNKING_CONFIG.contextual_prefix_enabled
MAX_CONTEXTUAL_PREFIX_LENGTH = CHUNKING_CONFIG.max_contextual_prefix_length
MAX_HEADING_PATH_DEPTH = CHUNKING_CONFIG.max_heading_path_depth
MAX_METADATA_STRING_LENGTH = CHUNKING_CONFIG.max_metadata_string_length
```

Remove the old direct assignments:

```python
CHUNK_SIZE = int(get_env_variable("CHUNK_SIZE", "1500"))
CHUNK_OVERLAP = int(get_env_variable("CHUNK_OVERLAP", "100"))
```

- [ ] **Step 6: Update config tests**

In `tests/test_config.py`, assert the new exported values are typed and the default values match balanced behavior:

```python
from app.config import (
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    CHUNKING_PRESET,
    CHUNKING_STRATEGY,
    CONTEXTUAL_PREFIX_ENABLED,
)


def test_chunking_config_exports():
    assert CHUNKING_PRESET in {"balanced", "legacy"}
    assert CHUNKING_STRATEGY in {"auto", "recursive", "paragraph"}
    assert isinstance(CHUNK_SIZE, int)
    assert isinstance(CHUNK_OVERLAP, int)
    assert isinstance(CONTEXTUAL_PREFIX_ENABLED, bool)
```

- [ ] **Step 7: Run config suite**

Run:

```powershell
pytest tests/test_config.py tests/services/test_chunking_config.py -v
```

Expected: PASS.

## Task 2: Extract Recursive Chunking Service

- [ ] **Step 1: Write failing recursive service tests**

Create `tests/services/test_chunking_recursive.py`:

```python
import hashlib

from langchain_core.documents import Document

from app.services.chunking.config import ChunkingConfig
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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/services/test_chunking_recursive.py -v
```

Expected: FAIL because `RecursiveChunkingService` does not exist.

- [ ] **Step 3: Implement recursive service**

Create `app/services/chunking/recursive.py`:

```python
import hashlib
from typing import Iterable, List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.chunking.config import ChunkingConfig


PROTECTED_METADATA_KEYS = {"file_id", "user_id", "digest"}


def generate_digest(page_content: str) -> str:
    return hashlib.md5(page_content.encode("utf-8", "ignore")).hexdigest()


class RecursiveChunkingService:
    def __init__(self, config: ChunkingConfig, strategy_name: str = "recursive"):
        self.config = config
        self.strategy_name = strategy_name

    def split_documents(
        self,
        documents: Iterable[Document],
        file_id: str,
        user_id: str,
        clean_content=None,
    ) -> List[Document]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )
        chunks = splitter.split_documents(list(documents))

        prepared = []
        for index, chunk in enumerate(chunks):
            content = clean_content(chunk.page_content) if clean_content else chunk.page_content
            loader_metadata = {
                key: value
                for key, value in (chunk.metadata or {}).items()
                if key not in PROTECTED_METADATA_KEYS
            }
            prepared.append(
                Document(
                    page_content=content,
                    metadata={
                        **loader_metadata,
                        "file_id": file_id,
                        "user_id": user_id,
                        "digest": generate_digest(content),
                        "chunk_index": index,
                        "chunk_strategy": self.strategy_name,
                        "chunking_preset": self.config.preset,
                        "chunk_size": len(content),
                        "contextual_prefix_enabled": False,
                    },
                )
            )

        return prepared
```

Create `app/services/chunking/__init__.py`:

```python
from app.services.chunking.config import ChunkingConfig, resolve_chunking_config
from app.services.chunking.recursive import RecursiveChunkingService

__all__ = ["ChunkingConfig", "RecursiveChunkingService", "resolve_chunking_config"]
```

- [ ] **Step 4: Run recursive tests**

Run:

```powershell
pytest tests/services/test_chunking_recursive.py -v
```

Expected: PASS.

## Task 3: Add Factory and Route Delegation

- [ ] **Step 1: Write factory tests**

Append to `tests/services/test_chunking_recursive.py`:

```python
from app.services.chunking.factory import get_chunking_service


def test_factory_returns_recursive_for_recursive_strategy():
    service = get_chunking_service(_config())

    assert isinstance(service, RecursiveChunkingService)


def test_factory_uses_recursive_compatibility_for_auto_in_pr1():
    config = _config()
    config = type(config)(**{**config.__dict__, "strategy": "auto", "preset": "balanced"})

    service = get_chunking_service(config)

    assert isinstance(service, RecursiveChunkingService)
    assert service.strategy_name == "auto"
```

- [ ] **Step 2: Run factory tests to verify failure**

Run:

```powershell
pytest tests/services/test_chunking_recursive.py -v
```

Expected: FAIL because `app.services.chunking.factory` does not exist.

- [ ] **Step 3: Implement factory**

Create `app/services/chunking/factory.py`:

```python
from app.config import logger
from app.services.chunking.config import ChunkingConfig
from app.services.chunking.recursive import RecursiveChunkingService


def get_chunking_service(config: ChunkingConfig):
    if config.strategy in {"recursive", "auto"}:
        return RecursiveChunkingService(config, strategy_name=config.strategy)

    logger.warning("Unknown chunking strategy '%s'; falling back to recursive", config.strategy)
    return RecursiveChunkingService(config, strategy_name="recursive")
```

- [ ] **Step 4: Refactor document preparation**

In `app/routes/document_routes.py`:

- Remove the `RecursiveCharacterTextSplitter` import.
- Import `CHUNKING_CONFIG`.
- Import `get_chunking_service`.
- Keep `generate_digest(page_content)` as a backwards-compatible route helper for existing tests.
- Change `_prepare_documents_sync()` so it delegates splitting and metadata preparation:

```python
def _prepare_documents_sync(
    data: Iterable[Document],
    file_id: str,
    user_id: str,
    clean_content: bool,
) -> List[Document]:
    clean_content_fn = clean_text if clean_content else None
    chunking_service = get_chunking_service(CHUNKING_CONFIG)
    return chunking_service.split_documents(
        data,
        file_id=file_id,
        user_id=user_id,
        clean_content=clean_content_fn,
    )
```

- [ ] **Step 5: Run route and digest tests**

Run:

```powershell
pytest tests/test_upload_isolation.py tests/test_main.py -v
```

Expected: PASS, including existing `generate_digest` assertions and `/embed`, `/local/embed`, `/embed-upload`.

## Task 4: Final PR 1 Verification

- [ ] **Step 1: Run focused tests**

Run:

```powershell
pytest tests/test_config.py tests/services/test_chunking_config.py tests/services/test_chunking_recursive.py tests/test_upload_isolation.py tests/test_main.py -v
```

Expected: PASS.

- [ ] **Step 2: Run broader non-database tests**

Run:

```powershell
pytest tests/services -v
pytest tests/utils -v
```

Expected: PASS.

