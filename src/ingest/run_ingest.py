import glob
from collections import Counter

from src.ingest import embed, load_qdrant
from src.ingest.chunk import build_chunks
from src.ingest.parse_openapi import parse_service


def main() -> None:
    client = load_qdrant.get_client()
    load_qdrant.ensure_collection(client)

    services_processed = 0
    doc_type_counts: Counter[str] = Counter()
    total_points = 0

    for service_dir in sorted(glob.glob("data/services/*")):
        service_parse = parse_service(service_dir)
        chunks = build_chunks(service_parse)
        if not chunks:
            continue

        vectors = embed.embed_documents([c.embed_text for c in chunks])
        load_qdrant.upsert_chunks(client, chunks, vectors)

        services_processed += 1
        total_points += len(chunks)
        for c in chunks:
            doc_type_counts[c.payload["doc_type"]] += 1

    print(f"services processed: {services_processed}")
    print("chunks by doc_type:")
    for doc_type, count in sorted(doc_type_counts.items()):
        print(f"  {doc_type}: {count}")
    print(f"total points: {total_points}")


if __name__ == "__main__":
    main()
