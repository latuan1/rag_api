# app/models.py
import hashlib
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List


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
    k: int = Field(default=4, ge=1, le=50)
    entity_id: Optional[str] = None

    @field_validator("query")
    @classmethod
    def query_must_be_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be empty")
        return value

    @field_validator("entity_id")
    @classmethod
    def entity_id_must_be_non_empty_when_supplied(
        cls, value: Optional[str]
    ) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("entity_id must not be empty")
        return value

    @field_validator("file_ids")
    @classmethod
    def file_ids_must_be_unique_non_empty_and_bounded(
        cls, value: List[str]
    ) -> List[str]:
        normalized = []
        seen = set()
        for file_id in value:
            file_id = file_id.strip()
            if not file_id:
                raise ValueError("file_ids must not contain empty values")
            if file_id not in seen:
                normalized.append(file_id)
                seen.add(file_id)
        if not normalized:
            raise ValueError("file_ids must not be empty")
        if len(normalized) > 50:
            raise ValueError("file_ids must contain no more than 50 unique values")
        return normalized
