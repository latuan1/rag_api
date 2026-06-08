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
