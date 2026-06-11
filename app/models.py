# app/models.py
import hashlib
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Literal


class DocumentResponse(BaseModel):
    page_content: str
    metadata: dict


class DocumentModel(BaseModel):
    page_content: str
    metadata: Optional[dict] = {}

    def generate_digest(self):
        hash_obj = hashlib.md5(self.page_content.encode())
        return hash_obj.hexdigest()


class StoreDocument(BaseModel):
    filepath: str = Field(..., min_length=1)
    filename: str = Field(..., min_length=1)
    file_content_type: str = Field(..., min_length=1)
    file_id: str = Field(..., min_length=1)


class QueryRequestBody(BaseModel):
    query: str = Field(..., min_length=1)
    file_id: str = Field(..., min_length=1)
    k: int = Field(..., gt=0)
    entity_id: Optional[str] = None


class CleanupMethod(str, Enum):
    incremental = "incremental"
    full = "full"


class QueryMultipleBody(BaseModel):
    query: str = Field(..., min_length=1)
    file_ids: List[str] = Field(..., min_length=1)
    k: int = Field(4, gt=0)


class KnowledgeChunkMetadata(BaseModel):
    ownerId: str = Field(..., min_length=1)
    tenantId: Optional[str] = None
    knowledgeSpaceId: str = Field(..., min_length=1)
    knowledgeSpaceName: Optional[str] = None
    documentId: str = Field(..., min_length=1)
    fileId: str = Field(..., min_length=1)
    filename: Optional[str] = None
    chunkHash: str = Field(..., min_length=1)
    page: Optional[int] = None
    section: Optional[str] = None
    status: Optional[Literal["ready", "ready_with_warnings"]] = "ready"

    @model_validator(mode="after")
    def page_or_section_required(self):
        if self.page is None and not self.section:
            raise ValueError("At least one of page or section is required")
        return self


class KnowledgeIndexRequest(BaseModel):
    text: str = Field(..., min_length=1)
    metadata: KnowledgeChunkMetadata


class KnowledgeQueryFilters(BaseModel):
    ownerId: str = Field(..., min_length=1)
    tenantId: Optional[str] = None
    knowledgeSpaceIds: List[str] = Field(..., min_length=1)
    documentIds: List[str] = Field(..., min_length=1)
    statuses: List[Literal["ready", "ready_with_warnings"]] = Field(..., min_length=1)

    @field_validator("knowledgeSpaceIds", "documentIds", "statuses")
    @classmethod
    def values_must_not_be_empty(cls, values):
        if not values:
            raise ValueError("List must not be empty")
        return values


class KnowledgeQueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    filters: KnowledgeQueryFilters
    topK: int = Field(..., gt=0)
    minRelevanceScore: float = Field(..., ge=0, le=1)
    maxChunksPerDocument: int = Field(..., gt=0)
    maxTotalChunks: int = Field(..., gt=0)


class KnowledgeDeleteRequest(BaseModel):
    ownerId: str = Field(..., min_length=1)
    tenantId: Optional[str] = None
    knowledgeSpaceId: str = Field(..., min_length=1)
    documentId: str = Field(..., min_length=1)
    fileId: str = Field(..., min_length=1)
