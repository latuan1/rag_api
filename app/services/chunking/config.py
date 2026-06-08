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
