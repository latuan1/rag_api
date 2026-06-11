from unittest.mock import MagicMock

from app.services.vector_store.atlas_mongo_vector import AtlasMongoVector


class DummyAtlasMongoVector(AtlasMongoVector):
    def __init__(self):
        self._collection = MagicMock()


def test_atlas_delete_by_metadata_filter_uses_all_scopes():
    store = DummyAtlasMongoVector()

    store.delete_by_metadata_filter(
        {
            "ownerId": {"$eq": "user_123"},
            "tenantId": {"$eq": "tenant_a"},
            "knowledgeSpaceId": {"$eq": "space_123"},
            "documentId": {"$eq": "doc_123"},
            "fileId": {"$eq": "file_123"},
        }
    )

    store._collection.delete_many.assert_called_once_with(
        {
            "ownerId": "user_123",
            "tenantId": "tenant_a",
            "knowledgeSpaceId": "space_123",
            "documentId": "doc_123",
            "fileId": "file_123",
        }
    )
