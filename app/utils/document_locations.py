import re
from bisect import bisect_right
from typing import Iterable, Optional

from langchain_core.documents import Document


TEXT_LOCATION_EXTENSIONS = {"txt", "md", "markdown", "json", "log", "csv"}
_MARKDOWN_EXTENSIONS = {"md", "markdown"}
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def is_location_supported(file_ext: str) -> bool:
    return (file_ext or "").lower().lstrip(".") in TEXT_LOCATION_EXTENSIONS


def read_source_lines(filepath: str) -> list[str]:
    from app.utils.document_loader import detect_file_encoding

    encoding = detect_file_encoding(filepath)
    with open(filepath, "r", encoding=encoding, errors="replace") as source_file:
        return source_file.readlines()


def _line_starts(lines: Iterable[str]) -> list[int]:
    starts = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    return starts


def _line_number_for_offset(starts: list[int], offset: int) -> int:
    return max(1, bisect_right(starts, offset))


def _line_range_for_span(
    starts: list[int], start_offset: int, end_offset_exclusive: int
) -> tuple[int, int]:
    start_line = _line_number_for_offset(starts, start_offset)
    end_line = _line_number_for_offset(
        starts, max(start_offset, end_offset_exclusive - 1)
    )
    return start_line, end_line


def _markdown_section_for_line(lines: list[str], line_number: int) -> Optional[str]:
    section = None
    for line in lines[:line_number]:
        match = _HEADING_RE.match(line.rstrip("\r\n"))
        if match:
            section = match.group(1).strip()
    return section


def annotate_loaded_documents(
    documents: list[Document], filepath: str, file_ext: str
) -> list[Document]:
    if not is_location_supported(file_ext):
        return documents

    try:
        source_lines = read_source_lines(filepath)
    except Exception:
        return documents

    source_text = "".join(source_lines)
    line_starts = _line_starts(source_lines)
    search_from = 0
    markdown = (file_ext or "").lower().lstrip(".") in _MARKDOWN_EXTENSIONS
    annotated = []

    for doc in documents:
        content = doc.page_content or ""
        start = source_text.find(content, search_from)
        matched_text = content
        if start < 0 and content.strip():
            start = source_text.find(content.strip(), search_from)
            matched_text = content.strip()

        if start < 0:
            annotated.append(doc)
            continue

        end = start + len(matched_text)
        start_line, end_line = _line_range_for_span(line_starts, start, end)
        metadata = dict(doc.metadata or {})
        metadata["start_line"] = start_line
        metadata["end_line"] = end_line
        if markdown:
            section = _markdown_section_for_line(source_lines, start_line)
            if section:
                metadata["section"] = section

        annotated.append(Document(page_content=doc.page_content, metadata=metadata))
        search_from = end

    return annotated


def line_range_for_text_span(
    text: str,
    base_start_line: int,
    start_offset: int,
    end_offset_exclusive: int,
) -> tuple[int, int]:
    lines = text.splitlines(keepends=True) or [text]
    starts = _line_starts(lines)
    relative_start, relative_end = _line_range_for_span(
        starts, start_offset, end_offset_exclusive
    )
    return base_start_line + relative_start - 1, base_start_line + relative_end - 1
