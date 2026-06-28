# app/models.py
import hashlib
from enum import Enum
from pydantic import BaseModel, Field, StringConstraints, field_validator
from typing import Annotated, Any, Optional, List


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
    filepath: str
    filename: str
    file_content_type: str
    file_id: str


class QueryRequestBody(BaseModel):
    query: str
    file_id: str
    k: int = 4
    entity_id: Optional[str] = None


class CleanupMethod(str, Enum):
    incremental = "incremental"
    full = "full"


class QueryMultipleBody(BaseModel):
    query: str
    file_ids: List[str]
    k: int = 4


NonEmptyQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]

OptionalTrimmedString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class KnowledgeRetrievalRequest(BaseModel):
    query: NonEmptyQuery
    match_count: Optional[int] = Field(default=None, ge=1, le=50)
    match_threshold: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    filter_document_type: Optional[OptionalTrimmedString] = None

    @field_validator("filter_document_type", mode="before")
    @classmethod
    def normalize_blank_filter_document_type(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


class KnowledgeProfile(BaseModel):
    embedding_profile: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    embedding_input_version: str
    active_chunks: int


class KnowledgeProfilesResponse(BaseModel):
    dataset_namespace: str
    profiles: List[KnowledgeProfile]


class KnowledgeRetrievalResult(BaseModel):
    chunk_id: str
    document_id: str
    section_id: str
    ordinal: int
    content: str
    embedding_content: str
    policy_title: str
    reference_number: Optional[str] = None
    document_type: Optional[str] = None
    heading: str
    heading_path: List[str]
    url_source: str
    similarity: float


class KnowledgeRetrievalResponse(BaseModel):
    dataset_namespace: str
    embedding_profile: str
    results: List[KnowledgeRetrievalResult]


class KnowledgeDocumentChunk(BaseModel):
    chunk_id: str
    ordinal: int
    content: str
    heading: str
    heading_path: List[str]
    source_line_spans: List[dict[str, Any]]
    review_required: bool
    review_reasons: List[str]
    data_quality_flags: List[str]
    warnings: List[str]


class KnowledgeDocumentResponse(BaseModel):
    dataset_namespace: str
    embedding_profile: str
    document_id: str
    chunks: List[KnowledgeDocumentChunk]
