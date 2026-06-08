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
