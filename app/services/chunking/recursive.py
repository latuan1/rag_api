import hashlib
from typing import Iterable, List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.chunking.config import ChunkingConfig
from app.services.chunking.metadata import merge_chunk_metadata


def generate_digest(page_content: str) -> str:
    return hashlib.md5(page_content.encode("utf-8", "ignore")).hexdigest()


class RecursiveChunkingService:
    def __init__(self, config: ChunkingConfig, strategy_name: str = "recursive"):
        self.config = config
        self.strategy_name = strategy_name

    def split_documents(
        self,
        documents: Iterable[Document],
        file_id: str,
        user_id: str,
        clean_content=None,
    ) -> List[Document]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )
        chunks = splitter.split_documents(list(documents))

        prepared = []
        for index, chunk in enumerate(chunks):
            content = (
                clean_content(chunk.page_content)
                if clean_content
                else chunk.page_content
            )
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
            prepared.append(
                Document(
                    page_content=content,
                    metadata=merge_chunk_metadata(
                        chunk.metadata or {}, system_metadata, self.config
                    ),
                )
            )

        return prepared
