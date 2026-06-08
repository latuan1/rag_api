import logging
from typing import Iterable, List

from langchain_core.documents import Document

from app.services.chunking.config import ChunkingConfig
from app.services.chunking.metadata import (
    build_context_prefix,
    merge_chunk_metadata,
    sanitize_metadata,
)
from app.services.chunking.paragraph import ParagraphChunkingService
from app.services.chunking.recursive import RecursiveChunkingService, generate_digest


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

        for source in documents:
            metadata = self._normalize_metadata(source.metadata or {})
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
                body = clean_content(body) if clean_content else body
                content, context_prefix = self._apply_context_prefix(body, metadata)
                system_metadata = {
                    "file_id": file_id,
                    "user_id": user_id,
                    "digest": generate_digest(content),
                    "chunk_index": len(prepared),
                    "chunk_strategy": "auto",
                    "chunking_preset": self.config.preset,
                    "chunk_size": len(content),
                    "contextual_prefix_enabled": bool(context_prefix),
                }
                if context_prefix:
                    system_metadata["context_prefix"] = context_prefix

                prepared.append(
                    Document(
                        page_content=content,
                        metadata=merge_chunk_metadata(
                            metadata, system_metadata, self.config
                        ),
                    )
                )

        return prepared

    def _normalize_metadata(self, metadata):
        normalized = dict(metadata or {})
        if "page" in normalized and "page_start" not in normalized:
            normalized["page_start"] = normalized["page"]
        if "page_start" in normalized and "page_end" not in normalized:
            normalized["page_end"] = normalized["page_start"]
        return sanitize_metadata(normalized, self.config)

    def _apply_context_prefix(self, body: str, metadata):
        if not self.config.contextual_prefix_enabled:
            return body[: self.config.max_chunk_size], ""

        context_prefix = build_context_prefix(metadata, self.config)
        if not context_prefix:
            return body[: self.config.max_chunk_size], ""

        context_prefix = context_prefix[: self.config.max_chunk_size]
        body_budget = max(self.config.max_chunk_size - len(context_prefix), 0)
        return f"{context_prefix}{body[:body_budget]}", context_prefix
