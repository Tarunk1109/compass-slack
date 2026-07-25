from fastmcp import FastMCP
from qdrant_client import models

from src import config
from src.ingest.load_qdrant import get_client
from src.retrieval.search import search

mcp = FastMCP("slack-rag-copilot")


def _scroll_one(filters: dict) -> dict | None:
    client = get_client()
    conditions = [models.FieldCondition(key=k, match=models.MatchValue(value=v)) for k, v in filters.items()]
    points, _ = client.scroll(
        collection_name=config.COLLECTION_NAME,
        scroll_filter=models.Filter(must=conditions),
        limit=1,
        with_payload=True,
    )
    return points[0].payload if points else None


def search_docs(
    query: str,
    service: str | None = None,
    doc_type: str | None = None,
    migration_status: str | None = None,
) -> list[dict]:
    """Reranked, cited snippets from the service docs corpus for a natural-language query."""
    filters = {}
    if service:
        filters["service_name"] = service
    if doc_type:
        filters["doc_type"] = doc_type
    if migration_status:
        filters["migration_status"] = migration_status

    results = search(query, filters=filters or None)
    return [
        {
            "score": r["score"],
            "service_name": r["service_name"],
            "source_file": r["source_file"],
            "operation_id": r["operation_id"],
            "chunk_text": r["chunk_text"],
        }
        for r in results
    ]


def get_service_info(service_name: str) -> dict:
    """Overview, version, migration status, owner, and dependencies for a service."""
    payload = _scroll_one({"service_name": service_name, "doc_type": "overview"})
    if not payload:
        return {"error": f"no overview found for service '{service_name}'"}
    return {
        "service_name": payload["service_name"],
        "version": payload["version"],
        "migration_status": payload["migration_status"],
        "depends_on": payload["depends_on"],
        "overview_text": payload["chunk_text"],
    }


def get_endpoint(service: str, method: str, path: str) -> dict:
    """Full flattened endpoint text for one operation, fetched on demand."""
    payload = _scroll_one(
        {
            "service_name": service,
            "doc_type": "endpoint",
            "http_method": method.upper(),
            "path": path,
        }
    )
    if not payload:
        return {"error": f"no endpoint found for {method} {path} in service '{service}'"}
    return {
        "operation_id": payload["operation_id"],
        "http_method": payload["http_method"],
        "path": payload["path"],
        "chunk_text": payload["chunk_text"],
    }


def get_migration_status(service_name: str) -> dict:
    """Migration status plus any documented migration notes for a service."""
    overview = _scroll_one({"service_name": service_name, "doc_type": "overview"})
    if not overview:
        return {"error": f"unknown service '{service_name}'"}

    client = get_client()
    readme_points, _ = client.scroll(
        collection_name=config.COLLECTION_NAME,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(key="service_name", match=models.MatchValue(value=service_name)),
                models.FieldCondition(key="doc_type", match=models.MatchValue(value="readme")),
            ]
        ),
        limit=20,
        with_payload=True,
    )
    notes = next(
        (p.payload["chunk_text"] for p in readme_points if "migration notes" in p.payload["chunk_text"].lower()),
        None,
    )

    return {
        "service_name": service_name,
        "migration_status": overview["migration_status"],
        "notes": notes or "no migration notes documented",
    }


def list_service_dependencies(service_name: str) -> dict:
    """Forward edges (what this service depends on) and reverse edges (what
    depends on this service), so 'what breaks if I change X' can be answered."""
    overview = _scroll_one({"service_name": service_name, "doc_type": "overview"})
    depends_on = overview["depends_on"] if overview else []

    client = get_client()
    dependents: set[str] = set()
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=config.COLLECTION_NAME,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="doc_type", match=models.MatchValue(value="endpoint"))]
            ),
            limit=100,
            offset=offset,
            with_payload=True,
        )
        for p in points:
            if service_name in (p.payload.get("depends_on") or []):
                dependents.add(p.payload["service_name"])
        if offset is None:
            break

    return {
        "service_name": service_name,
        "depends_on": depends_on,
        "depended_on_by": sorted(dependents),
    }


mcp.tool(search_docs)
mcp.tool(get_service_info)
mcp.tool(get_endpoint)
mcp.tool(get_migration_status)
mcp.tool(list_service_dependencies)


if __name__ == "__main__":
    mcp.run(transport="http")
