import sys

import cohere
from qdrant_client import models

from src import config
from src.ingest import embed
from src.ingest.load_qdrant import DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME, get_client

PREFETCH_LIMIT = 25

_rerank_client: cohere.ClientV2 | None = None


def _get_rerank_client() -> cohere.ClientV2:
    global _rerank_client
    if _rerank_client is None:
        _rerank_client = cohere.ClientV2()
    return _rerank_client


def _build_filter(filters: dict | None) -> models.Filter | None:
    if not filters:
        return None
    conditions = [
        models.FieldCondition(key=key, match=models.MatchValue(value=value))
        for key, value in filters.items()
    ]
    return models.Filter(must=conditions)


def search(query: str, filters: dict | None = None, top_k: int = 8) -> list[dict]:
    client = get_client()
    query_filter = _build_filter(filters)
    dense_vector = embed.embed_query(query)
    sparse_vector = embed.bm25_sparse_vector(query)

    candidates = client.query_points(
        collection_name=config.COLLECTION_NAME,
        prefetch=[
            models.Prefetch(
                query=dense_vector,
                using=DENSE_VECTOR_NAME,
                filter=query_filter,
                limit=PREFETCH_LIMIT,
            ),
            models.Prefetch(
                query=sparse_vector,
                using=SPARSE_VECTOR_NAME,
                filter=query_filter,
                limit=PREFETCH_LIMIT,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=PREFETCH_LIMIT,
        with_payload=True,
    ).points

    if not candidates:
        return []

    documents = [c.payload["chunk_text"] for c in candidates]
    rerank_response = embed.with_cohere_retry(
        lambda: _get_rerank_client().rerank(
            model=config.RERANK_MODEL,
            query=query,
            documents=documents,
            top_n=min(top_k, len(documents)),
        )
    )

    results = []
    for result in rerank_response.results:
        payload = candidates[result.index].payload
        results.append(
            {
                "score": result.relevance_score,
                "payload": payload,
                "chunk_text": payload.get("chunk_text"),
                "service_name": payload.get("service_name"),
                "source_file": payload.get("source_file"),
                "operation_id": payload.get("operation_id"),
            }
        )
    return results


def main() -> None:
    query = sys.argv[1]
    results = search(query)
    for r in results:
        print(f"[{r['score']:.3f}] {r['service_name']} / {r['source_file']} / {r['operation_id']}")
        print((r["chunk_text"] or "")[:200])
        print()


if __name__ == "__main__":
    main()
