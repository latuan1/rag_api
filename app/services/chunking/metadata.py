from typing import Any, Dict, Mapping

from app.services.chunking.config import ChunkingConfig


PROTECTED_METADATA_KEYS = {"file_id", "user_id", "digest"}


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
        parts.append(
            f"pages={page_start}-{page_end}"
            if page_start != page_end
            else f"page={page_start}"
        )

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
        parts.append(
            f"rows={row_start}-{row_end}" if row_start != row_end else f"row={row_start}"
        )

    if not parts:
        return ""

    prefix = "[Context: " + "; ".join(parts) + "]\n\n"
    return prefix[: config.max_contextual_prefix_length]
