import hmac
import logging
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from app.config import (
    KNOWLEDGE_API_TOKEN,
    RETRIEVAL_DATABASE_COMMAND_TIMEOUT,
    RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE,
    RETRIEVAL_DATABASE_URL,
    RETRIEVAL_DATASET_NAMESPACE,
    RETRIEVAL_DEFAULT_MATCH_COUNT,
    RETRIEVAL_DEFAULT_MATCH_THRESHOLD,
    RETRIEVAL_EMBEDDING_API_KEY,
    RETRIEVAL_EMBEDDING_BASE_URL,
    RETRIEVAL_EMBEDDING_DIMENSIONS,
    RETRIEVAL_EMBEDDING_INPUT_VERSION,
    RETRIEVAL_EMBEDDING_MODEL,
    RETRIEVAL_EMBEDDING_PROFILE,
    RETRIEVAL_EMBEDDING_PROVIDER,
    RETRIEVAL_EMBEDDING_TIMEOUT_SECONDS,
)
from app.middleware import is_jwt_auth_configured
from app.models import (
    KnowledgeDocumentResponse,
    KnowledgeProfilesResponse,
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResponse,
)
from app.services.local_knowledge_retrieval import (
    DocumentOutput,
    LocalKnowledgeConfig,
    LocalKnowledgeRetrievalService,
    RetrievalConfigurationError,
    RetrievalDatabaseError,
    RetrievalEmbeddingError,
    RetrievalOutput,
)

logger = logging.getLogger(__name__)

_knowledge_service: LocalKnowledgeRetrievalService | None = None


async def require_knowledge_auth(request: Request) -> None:
    auth_configured = False

    if KNOWLEDGE_API_TOKEN:
        auth_configured = True
        provided_token = request.headers.get("X-Knowledge-API-Key", "")
        if hmac.compare_digest(provided_token, KNOWLEDGE_API_TOKEN):
            return

    if is_jwt_auth_configured():
        auth_configured = True
        user = getattr(request.state, "user", None)
        if user is not None:
            return

    if not auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local knowledge retrieval authentication is not configured",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
    )


router = APIRouter(
    prefix="/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(require_knowledge_auth)],
)


def _stable_unavailable(status_code: int = status.HTTP_503_SERVICE_UNAVAILABLE):
    return HTTPException(
        status_code=status_code,
        detail="Local knowledge retrieval is unavailable",
    )


def _object_to_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(vars(value))


def _validate_document_id(document_id: str) -> str:
    if not document_id.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="document_id must not be blank",
        )
    return document_id


def get_knowledge_service() -> LocalKnowledgeRetrievalService:
    global _knowledge_service
    if _knowledge_service is None:
        config = LocalKnowledgeConfig(
            database_url=RETRIEVAL_DATABASE_URL,
            dataset_namespace=RETRIEVAL_DATASET_NAMESPACE,
            embedding_api_key=RETRIEVAL_EMBEDDING_API_KEY,
            embedding_base_url=RETRIEVAL_EMBEDDING_BASE_URL,
            embedding_provider=RETRIEVAL_EMBEDDING_PROVIDER,
            embedding_model=RETRIEVAL_EMBEDDING_MODEL,
            embedding_dimensions=RETRIEVAL_EMBEDDING_DIMENSIONS,
            embedding_input_version=RETRIEVAL_EMBEDDING_INPUT_VERSION,
            embedding_profile=RETRIEVAL_EMBEDDING_PROFILE,
            default_match_count=RETRIEVAL_DEFAULT_MATCH_COUNT,
            default_match_threshold=RETRIEVAL_DEFAULT_MATCH_THRESHOLD,
            database_command_timeout=RETRIEVAL_DATABASE_COMMAND_TIMEOUT,
            database_statement_cache_size=RETRIEVAL_DATABASE_STATEMENT_CACHE_SIZE,
            embedding_timeout_seconds=RETRIEVAL_EMBEDDING_TIMEOUT_SECONDS,
        )
        _knowledge_service = LocalKnowledgeRetrievalService(config)
    return _knowledge_service


async def close_knowledge_service() -> None:
    global _knowledge_service
    if _knowledge_service is not None:
        await _knowledge_service.close()
        _knowledge_service = None


def _handle_retrieval_error(exc: Exception) -> None:
    if isinstance(exc, (RetrievalConfigurationError, RetrievalDatabaseError)):
        logger.exception("Local knowledge retrieval unavailable")
        raise _stable_unavailable() from exc
    if isinstance(exc, RetrievalEmbeddingError):
        logger.exception("Local knowledge embedding unavailable")
        raise _stable_unavailable(status.HTTP_502_BAD_GATEWAY) from exc
    raise exc


@router.get("/profiles", response_model=KnowledgeProfilesResponse)
async def get_profiles():
    try:
        service = get_knowledge_service()
        profiles = await service.discover_active_profiles()
        return {
            "dataset_namespace": service.dataset_namespace,
            "profiles": [_object_to_dict(profile) for profile in profiles],
        }
    except (RetrievalConfigurationError, RetrievalDatabaseError, RetrievalEmbeddingError) as exc:
        _handle_retrieval_error(exc)


@router.post("/retrieve", response_model=KnowledgeRetrievalResponse)
async def retrieve_knowledge(body: KnowledgeRetrievalRequest):
    try:
        service = get_knowledge_service()
        output: RetrievalOutput = await service.retrieve(
            query=body.query,
            match_count=body.match_count,
            match_threshold=body.match_threshold,
            filter_document_type=body.filter_document_type,
        )
        return {
            "dataset_namespace": service.dataset_namespace,
            "embedding_profile": output.profile.embedding_profile,
            "results": output.rows,
        }
    except (RetrievalConfigurationError, RetrievalDatabaseError, RetrievalEmbeddingError) as exc:
        _handle_retrieval_error(exc)


@router.get("/documents/{document_id}", response_model=KnowledgeDocumentResponse)
async def get_document(
    document_id: str = Path(..., min_length=1, max_length=255),
    limit: int = Query(500, ge=1, le=2000),
):
    document_id = _validate_document_id(document_id)
    try:
        service = get_knowledge_service()
        output: DocumentOutput = await service.fetch_document(document_id, limit)
        return {
            "dataset_namespace": service.dataset_namespace,
            "embedding_profile": output.profile.embedding_profile,
            "document_id": document_id,
            "chunks": output.rows,
        }
    except (RetrievalConfigurationError, RetrievalDatabaseError, RetrievalEmbeddingError) as exc:
        _handle_retrieval_error(exc)


@router.get(
    "/documents/{document_id}/neighbors",
    response_model=KnowledgeDocumentResponse,
)
async def get_document_neighbors(
    document_id: str = Path(..., min_length=1, max_length=255),
    ordinal: int = Query(..., ge=0),
    window: int = Query(1, ge=0, le=10),
):
    document_id = _validate_document_id(document_id)
    try:
        service = get_knowledge_service()
        output: DocumentOutput = await service.fetch_neighbors(
            document_id=document_id,
            ordinal=ordinal,
            window=window,
        )
        return {
            "dataset_namespace": service.dataset_namespace,
            "embedding_profile": output.profile.embedding_profile,
            "document_id": document_id,
            "chunks": output.rows,
        }
    except (RetrievalConfigurationError, RetrievalDatabaseError, RetrievalEmbeddingError) as exc:
        _handle_retrieval_error(exc)
