from app.config import (
    RAG_HOST,
    RAG_PORT,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    PDF_EXTRACT_IMAGES,
    PDF_OCR_DPI,
    PDF_OCR_ENABLED,
    PDF_OCR_LANGS,
    PDF_OCR_MIN_TEXT_CHARS,
    VECTOR_DB_TYPE,
)

def test_config_defaults():
    assert RAG_HOST is not None
    assert isinstance(RAG_PORT, int)
    assert isinstance(CHUNK_SIZE, int)
    assert isinstance(CHUNK_OVERLAP, int)
    assert isinstance(PDF_EXTRACT_IMAGES, bool)
    assert PDF_OCR_ENABLED is True
    assert PDF_OCR_LANGS == "eng+vie"
    assert PDF_OCR_MIN_TEXT_CHARS == 20
    assert PDF_OCR_DPI == 200
    assert VECTOR_DB_TYPE is not None
