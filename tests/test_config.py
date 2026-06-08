from app.config import (
    RAG_HOST,
    RAG_PORT,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    CHUNKING_PRESET,
    CHUNKING_STRATEGY,
    CONTEXTUAL_PREFIX_ENABLED,
    PDF_EXTRACT_IMAGES,
    VECTOR_DB_TYPE,
)


def test_config_defaults():
    assert RAG_HOST is not None
    assert isinstance(RAG_PORT, int)
    assert isinstance(CHUNK_SIZE, int)
    assert isinstance(CHUNK_OVERLAP, int)
    assert isinstance(PDF_EXTRACT_IMAGES, bool)
    assert VECTOR_DB_TYPE is not None


def test_chunking_config_exports():
    assert CHUNKING_PRESET in {"balanced", "legacy"}
    assert CHUNKING_STRATEGY in {"auto", "recursive", "paragraph"}
    assert isinstance(CHUNK_SIZE, int)
    assert isinstance(CHUNK_OVERLAP, int)
    assert isinstance(CONTEXTUAL_PREFIX_ENABLED, bool)
