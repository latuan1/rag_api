import logging
from typing import Iterable, List

from langchain_core.documents import Document

from app.services.chunking.config import ChunkingConfig
from app.services.chunking.metadata import (
    build_context_prefix,
    merge_chunk_metadata,
)
from app.services.chunking.normalizers import normalize_documents
from app.services.chunking.paragraph import ParagraphChunkingService
from app.services.chunking.recursive import RecursiveChunkingService, generate_digest
from app.services.chunking.tables import split_table_rows


logger = logging.getLogger(__name__)


class AutoChunkingService:
    def __init__(self, config: ChunkingConfig):
        self.config = config

    def split_documents(
        self,
        documents: Iterable[Document],
        file_id: str,
        user_id: str,
        clean_content=None,
    ) -> List[Document]:
        documents = list(documents)
        try:
            return self._split_with_structure(documents, file_id, user_id, clean_content)
        except Exception as exc:
            logger.warning("Auto chunking failed; falling back to recursive: %s", exc)
            return RecursiveChunkingService(
                self.config, strategy_name="recursive_fallback"
            ).split_documents(documents, file_id, user_id, clean_content)

    def _split_with_structure(
        self,
        documents: Iterable[Document],
        file_id: str,
        user_id: str,
        clean_content=None,
    ) -> List[Document]:
        prepared = []
        paragraph_service = ParagraphChunkingService(self.config, strategy_name="auto")
        documents = normalize_documents(documents)

        for source in documents:
            metadata = source.metadata or {}
            if self._has_reliable_slide(metadata):
                if len(source.page_content) <= self.config.max_chunk_size:
                    prepared.append(
                        self._finalize_chunk(
                            source.page_content,
                            metadata,
                            len(prepared),
                            file_id,
                            user_id,
                            clean_content,
                        )
                    )
                else:
                    prepared.extend(
                        RecursiveChunkingService(
                            self.config, strategy_name="recursive_fallback"
                        ).split_documents(
                            [Document(page_content=source.page_content, metadata=metadata)],
                            file_id,
                            user_id,
                            clean_content,
                        )
                    )
                continue

            if self._has_reliable_table_rows(source):
                table_chunks = split_table_rows(
                    self._parse_pipe_table(source.page_content), metadata, self.config
                )
                for table_chunk in table_chunks:
                    prepared.append(
                        self._finalize_chunk(
                            table_chunk.page_content,
                            table_chunk.metadata,
                            len(prepared),
                            file_id,
                            user_id,
                            clean_content,
                        )
                    )
                continue

            paragraphs = paragraph_service._split_paragraphs(source.page_content)
            if any(len(paragraph) > self.config.max_chunk_size for paragraph in paragraphs):
                prepared.extend(
                    RecursiveChunkingService(
                        self.config, strategy_name="recursive_fallback"
                    ).split_documents(
                        [Document(page_content=source.page_content, metadata=metadata)],
                        file_id,
                        user_id,
                        clean_content,
                    )
                )
                continue

            for body in paragraph_service._group_paragraphs(paragraphs):
                prepared.append(
                    self._finalize_chunk(
                        body,
                        metadata,
                        len(prepared),
                        file_id,
                        user_id,
                        clean_content,
                    )
                )

        return prepared

    def _finalize_chunk(
        self,
        body: str,
        metadata,
        index: int,
        file_id: str,
        user_id: str,
        clean_content=None,
    ) -> Document:
        body = clean_content(body) if clean_content else body
        content, context_prefix = self._apply_context_prefix(body, metadata)
        system_metadata = {
            "file_id": file_id,
            "user_id": user_id,
            "digest": generate_digest(content),
            "chunk_index": index,
            "chunk_strategy": "auto",
            "chunking_preset": self.config.preset,
            "chunk_size": len(content),
            "contextual_prefix_enabled": bool(context_prefix),
        }
        if context_prefix:
            system_metadata["context_prefix"] = context_prefix

        return Document(
            page_content=content,
            metadata=merge_chunk_metadata(metadata, system_metadata, self.config),
        )

    def _has_reliable_slide(self, metadata) -> bool:
        return (
            metadata.get("slide_start") is not None
            and metadata.get("slide_end") is not None
        )

    def _has_reliable_table_rows(self, source: Document) -> bool:
        metadata = source.metadata or {}
        if metadata.get("row_start") is None:
            return False
        lines = [line for line in (source.page_content or "").splitlines() if line.strip()]
        return len(lines) >= 2 and all("|" in line for line in lines)

    def _parse_pipe_table(self, content: str) -> list[list[str]]:
        return [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in (content or "").splitlines()
            if line.strip()
        ]

    def _apply_context_prefix(self, body: str, metadata):
        if not self.config.contextual_prefix_enabled:
            return body[: self.config.max_chunk_size], ""

        context_prefix = build_context_prefix(metadata, self.config)
        if not context_prefix:
            return body[: self.config.max_chunk_size], ""

        context_prefix = context_prefix[: self.config.max_chunk_size]
        body_budget = max(self.config.max_chunk_size - len(context_prefix), 0)
        return f"{context_prefix}{body[:body_budget]}", context_prefix
