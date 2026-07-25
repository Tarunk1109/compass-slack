import uuid

from qdrant_client import QdrantClient, models

from src import config
from src.ingest import embed
from src.ingest.chunk import Chunk

# Fixed namespace so chunk_id_to_uuid is deterministic across runs.
_NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")

PAYLOAD_INDEX_FIELDS = ("service_name", "doc_type", "migration_status", "version")

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "bm25"
BM25_KEYWORD_FIELDS = ("operation_id", "http_method", "path", "service_name")


def get_client() -> QdrantClient:
    return QdrantClient(host=config.QDRANT_HOST, port=config.QDRANT_PORT)


def chunk_id_to_uuid(chunk_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, chunk_id))


def ensure_collection(client: QdrantClient) -> None:
    if client.collection_exists(config.COLLECTION_NAME):
        info = client.get_collection(config.COLLECTION_NAME)
        sparse_vectors = info.config.params.sparse_vectors or {}
        if SPARSE_VECTOR_NAME in sparse_vectors:
            return
        # Predates hybrid search (dense-only schema from Phase 4). Rebuild.
        client.delete_collection(config.COLLECTION_NAME)

    client.create_collection(
        collection_name=config.COLLECTION_NAME,
        vectors_config={
            DENSE_VECTOR_NAME: models.VectorParams(size=config.EMBED_DIM, distance=models.Distance.COSINE),
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: models.SparseVectorParams(modifier=models.Modifier.IDF),
        },
    )

    for field_name in PAYLOAD_INDEX_FIELDS:
        client.create_payload_index(
            collection_name=config.COLLECTION_NAME,
            field_name=field_name,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


def _bm25_source_text(chunk: Chunk) -> str:
    keyword_bits = " ".join(str(chunk.payload.get(f) or "") for f in BM25_KEYWORD_FIELDS)
    return f"{chunk.chunk_text}\n{keyword_bits}"


def upsert_chunks(client: QdrantClient, chunks: list[Chunk], vectors: list[list[float]]) -> None:
    points = [
        models.PointStruct(
            id=chunk_id_to_uuid(chunk.id),
            vector={
                DENSE_VECTOR_NAME: vector,
                SPARSE_VECTOR_NAME: embed.bm25_sparse_vector(_bm25_source_text(chunk)),
            },
            payload=chunk.payload,
        )
        for chunk, vector in zip(chunks, vectors)
    ]
    client.upsert(collection_name=config.COLLECTION_NAME, points=points)
