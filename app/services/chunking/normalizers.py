from pathlib import PurePath
from typing import Iterable

from langchain_core.documents import Document


_STABLE_SCALAR_KEYS = {
    "slide_start",
    "slide_end",
    "sheet_name",
    "row_start",
    "row_end",
}


def _is_stable_scalar(value) -> bool:
    return isinstance(value, (str, int, float, bool)) and value != ""


def _reliable_heading_path(value) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, str) and item for item in value):
        return None
    return list(value)


def normalize_document_metadata(doc: Document) -> dict:
    metadata = dict(doc.metadata or {})
    normalized = dict(metadata)

    source = metadata.get("source")
    if isinstance(source, str) and source:
        normalized["filename"] = PurePath(source).name

    page = metadata.get("page")
    if isinstance(page, int) and not isinstance(page, bool):
        page_number = page + 1
        normalized["page_start"] = page_number
        normalized["page_end"] = page_number

    heading_path = _reliable_heading_path(metadata.get("heading_path"))
    if heading_path:
        normalized["heading_path"] = heading_path
        normalized["section_title"] = heading_path[-1]
    else:
        normalized.pop("heading_path", None)
        normalized.pop("section_title", None)

    for key in _STABLE_SCALAR_KEYS:
        value = metadata.get(key)
        if _is_stable_scalar(value):
            normalized[key] = value
        else:
            normalized.pop(key, None)

    return normalized


def normalize_documents(docs: Iterable[Document]) -> list[Document]:
    return [
        Document(
            page_content=doc.page_content,
            metadata=normalize_document_metadata(doc),
        )
        for doc in docs
    ]
