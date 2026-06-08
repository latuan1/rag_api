from collections.abc import Mapping

from langchain_core.documents import Document

from app.services.chunking.config import ChunkingConfig


def _render_row(row: list[str]) -> str:
    return " | ".join(str(cell).strip() for cell in row)


def _cap_text(value: str, budget: int) -> str:
    if budget <= 0:
        return ""
    if len(value) <= budget:
        return value
    if budget <= 3:
        return value[:budget]
    return value[: budget - 3].rstrip() + "..."


def _header_text(header: list[str], limit: int) -> str:
    rendered = _render_row(header)
    if len(rendered) <= limit:
        return rendered

    summary = "Columns: " + ", ".join(str(column).strip() for column in header)
    return _cap_text(summary, limit)


def _with_row_metadata(
    metadata: Mapping,
    base_row: int,
    data_start_index: int,
    data_end_index: int,
) -> dict:
    chunk_metadata = dict(metadata or {})
    chunk_metadata["row_start"] = base_row + data_start_index
    chunk_metadata["row_end"] = base_row + data_end_index
    return chunk_metadata


def split_table_rows(
    rows: list[list[str]], metadata: Mapping, config: ChunkingConfig
) -> list[Document]:
    if not rows:
        return []

    limit = max(1, min(config.chunk_size, config.max_chunk_size))
    header = _header_text(rows[0], limit)
    data_rows = rows[1:]
    if not data_rows:
        return [Document(page_content=header, metadata=dict(metadata or {}))]

    base_row = metadata.get("row_start", 1) if isinstance(metadata, Mapping) else 1
    if not isinstance(base_row, int) or isinstance(base_row, bool):
        base_row = 1

    chunks: list[Document] = []
    current_rows: list[str] = []
    current_start_index = 1

    def flush(end_index: int) -> None:
        nonlocal current_rows
        if not current_rows:
            return
        content = "\n".join([header, *current_rows])
        chunks.append(
            Document(
                page_content=content[: config.max_chunk_size],
                metadata=_with_row_metadata(
                    metadata, base_row, current_start_index, end_index
                ),
            )
        )
        current_rows = []

    for index, row in enumerate(data_rows, start=1):
        rendered = _render_row(row)
        candidate_rows = [*current_rows, rendered]
        candidate = "\n".join([header, *candidate_rows])

        if current_rows and len(candidate) > limit:
            flush(index - 1)
            current_start_index = index
            candidate_rows = [rendered]
            candidate = "\n".join([header, rendered])

        if len(candidate) > config.max_chunk_size:
            row_budget = max(config.max_chunk_size - len(header) - 1, 0)
            rendered = _cap_text(rendered, row_budget)

        current_rows.append(rendered)

    flush(len(data_rows))
    return chunks
