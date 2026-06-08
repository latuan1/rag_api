from app.services.chunking.config import ChunkingConfig
from app.services.chunking.metadata import (
    build_context_prefix,
    merge_chunk_metadata,
    sanitize_metadata,
)


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

    assert (
        prefix
        == "[Context: file=README.md; section=Installation > Docker; pages=3-4]\n\n"[
            : _config().max_contextual_prefix_length
        ]
    )


def test_context_prefix_is_empty_without_useful_context():
    assert build_context_prefix({"chunk_index": 1}, _config()) == ""
