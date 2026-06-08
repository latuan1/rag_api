import re
from typing import Iterable, List

from langchain_core.documents import Document

from app.services.chunking.config import ChunkingConfig
from app.services.chunking.metadata import merge_chunk_metadata
from app.services.chunking.recursive import RecursiveChunkingService, generate_digest


_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")


class ParagraphChunkingService:
    def __init__(self, config: ChunkingConfig, strategy_name: str = "paragraph"):
        self.config = config
        self.strategy_name = strategy_name

    def split_documents(
        self,
        documents: Iterable[Document],
        file_id: str,
        user_id: str,
        clean_content=None,
    ) -> List[Document]:
        prepared = []
        for source in documents:
            paragraphs = self._split_paragraphs(source.page_content)
            if any(len(paragraph) > self.config.max_chunk_size for paragraph in paragraphs):
                prepared.extend(
                    RecursiveChunkingService(
                        self.config, strategy_name="recursive_fallback"
                    ).split_documents([source], file_id, user_id, clean_content)
                )
                continue

            for body in self._group_paragraphs(paragraphs):
                content = clean_content(body) if clean_content else body
                metadata = self._metadata(
                    source.metadata or {}, content, len(prepared), file_id, user_id
                )
                prepared.append(Document(page_content=content, metadata=metadata))

        return prepared

    def _split_paragraphs(self, content: str) -> List[str]:
        return [
            paragraph.strip()
            for paragraph in _PARAGRAPH_SPLIT_RE.split(content or "")
            if paragraph.strip()
        ]

    def _group_paragraphs(self, paragraphs: List[str]) -> List[str]:
        groups: List[str] = []
        current: List[str] = []

        for paragraph in paragraphs:
            candidate = "\n\n".join([*current, paragraph]) if current else paragraph
            if current and len(candidate) > self.config.chunk_size:
                groups.append("\n\n".join(current))
                current = [paragraph]
            else:
                current.append(paragraph)

        if current:
            groups.append("\n\n".join(current))

        if len(groups) > 1 and len(groups[-1]) < self.config.min_chunk_size:
            groups[-2] = f"{groups[-2]}\n\n{groups[-1]}"
            groups.pop()

        return groups

    def _metadata(
        self,
        loader_metadata,
        content: str,
        index: int,
        file_id: str,
        user_id: str,
    ):
        system_metadata = {
            "file_id": file_id,
            "user_id": user_id,
            "digest": generate_digest(content),
            "chunk_index": index,
            "chunk_strategy": self.strategy_name,
            "chunking_preset": self.config.preset,
            "chunk_size": len(content),
            "contextual_prefix_enabled": False,
        }
        return merge_chunk_metadata(loader_metadata, system_metadata, self.config)
