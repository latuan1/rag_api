import asyncio
from functools import lru_cache
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, HTTPException, Request, status
from langchain_core.documents import Document

from app.config import VECTOR_DB_TYPE, VectorDBType, logger, vector_store
from app.models import (
    KnowledgeDeleteRequest,
    KnowledgeIndexRequest,
    KnowledgeQueryRequest,
)
from app.services.vector_store.async_pg_vector import AsyncPgVector


router = APIRouter(prefix="/knowledge")


def _knowledge_chunk_id(metadata: dict[str, Any]) -> str:
    tenant_id = metadata.get("tenantId") or "_"
    return (
        f"knowledge:{metadata['ownerId']}:{tenant_id}:"
        f"{metadata['knowledgeSpaceId']}:{metadata['documentId']}:"
        f"{metadata['fileId']}:{metadata['chunkHash']}"
    )


def _query_filter(body: KnowledgeQueryRequest) -> dict[str, Any]:
    filters: dict[str, Any] = {
        "ownerId": {"$eq": body.filters.ownerId},
        "knowledgeSpaceId": {"$in": body.filters.knowledgeSpaceIds},
        "documentId": {"$in": body.filters.documentIds},
        "status": {"$in": body.filters.statuses},
    }
    if body.filters.tenantId:
        filters["tenantId"] = {"$eq": body.filters.tenantId}
    return filters


def _delete_filter(body: KnowledgeDeleteRequest) -> dict[str, Any]:
    filters: dict[str, Any] = {
        "ownerId": {"$eq": body.ownerId},
        "knowledgeSpaceId": {"$eq": body.knowledgeSpaceId},
        "documentId": {"$eq": body.documentId},
        "fileId": {"$eq": body.fileId},
    }
    if body.tenantId:
        filters["tenantId"] = {"$eq": body.tenantId}
    return filters


def _score_from_vector_score(raw_score: float) -> float:
    if VECTOR_DB_TYPE == VectorDBType.PGVECTOR:
        return max(0.0, min(1.0, 1.0 - float(raw_score)))
    return max(0.0, min(1.0, float(raw_score)))


@lru_cache(maxsize=128)
def get_cached_query_embedding(query: str):
    return vector_store.embedding_function.embed_query(query)


async def add_documents_to_vector_store(
    documents: List[Document],
    ids: List[str],
    executor=None,
) -> List[str]:
    if isinstance(vector_store, AsyncPgVector):
        return await vector_store.aadd_documents(
            documents, ids=ids, executor=executor
        )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        executor,
        lambda: vector_store.add_documents(documents, ids=ids),
    )


async def query_vector_store(
    embedding: List[float],
    k: int,
    filter: Dict[str, Any],
    executor=None,
) -> List[Tuple[Document, float]]:
    if isinstance(vector_store, AsyncPgVector):
        return await vector_store.asimilarity_search_with_score_by_vector(
            embedding,
            k=k,
            filter=filter,
            executor=executor,
        )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        executor,
        lambda: vector_store.similarity_search_with_score_by_vector(
            embedding, k=k, filter=filter
        ),
    )


async def delete_by_metadata_filter(filter: Dict[str, Any], executor=None) -> None:
    if isinstance(vector_store, AsyncPgVector):
        await vector_store.delete_by_metadata_filter(filter, executor=executor)
        return
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        executor,
        lambda: vector_store.delete_by_metadata_filter(filter),
    )


@router.post("/index")
async def index_knowledge_chunk(body: KnowledgeIndexRequest, request: Request):
    metadata = body.metadata.model_dump(exclude_none=True)
    document = Document(page_content=body.text, metadata=metadata)
    chunk_id = _knowledge_chunk_id(metadata)

    try:
        await add_documents_to_vector_store(
            [document],
            ids=[chunk_id],
            executor=request.app.state.thread_pool,
        )
    except Exception as e:
        logger.error("Failed to index knowledge chunk: %s", e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Knowledge indexing failed: {str(e)}",
        )

    return {"status": "indexed", "chunkHash": metadata["chunkHash"]}


@router.post("/query")
async def query_knowledge(body: KnowledgeQueryRequest, request: Request):
    try:
        embedding = get_cached_query_embedding(body.query)
        documents = await query_vector_store(
            embedding,
            k=body.topK,
            filter=_query_filter(body),
            executor=request.app.state.thread_pool,
        )
    except Exception as e:
        logger.error("Failed to query knowledge chunks: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Knowledge retrieval failed: {str(e)}",
        )

    results = []
    per_document_counts: dict[str, int] = {}
    for document, raw_score in documents:
        metadata = document.metadata or {}
        score = _score_from_vector_score(raw_score)
        if score < body.minRelevanceScore:
            continue

        document_id = metadata.get("documentId")
        if not document_id:
            continue

        count = per_document_counts.get(document_id, 0)
        if count >= body.maxChunksPerDocument:
            continue

        result = {
            "knowledgeSpaceId": metadata.get("knowledgeSpaceId"),
            "knowledgeSpaceName": metadata.get("knowledgeSpaceName"),
            "documentId": document_id,
            "fileId": metadata.get("fileId"),
            "filename": metadata.get("filename"),
            "chunkText": document.page_content,
            "chunkHash": metadata.get("chunkHash"),
            "page": metadata.get("page"),
            "section": metadata.get("section"),
            "score": score,
        }
        results.append({key: value for key, value in result.items() if value is not None})
        per_document_counts[document_id] = count + 1

        if len(results) >= body.maxTotalChunks:
            break

    return results


@router.post("/delete")
async def delete_knowledge(body: KnowledgeDeleteRequest, request: Request):
    try:
        await delete_by_metadata_filter(
            _delete_filter(body),
            executor=request.app.state.thread_pool,
        )
    except Exception as e:
        logger.error("Failed to delete knowledge chunks: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Knowledge deletion failed: {str(e)}",
        )

    return {
        "deleted": True,
        "documentId": body.documentId,
        "fileId": body.fileId,
    }
