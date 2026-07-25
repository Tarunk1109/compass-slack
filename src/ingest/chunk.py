import glob
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any

import anthropic
import yaml

from src import config
from src.ingest import parse_prose
from src.ingest.parse_openapi import EndpointRecord, ServiceParse, parse_service

CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".cache", "endpoint_summaries.json")

_anthropic_client: anthropic.Anthropic | None = None


@dataclass
class Chunk:
    id: str
    embed_text: str
    chunk_text: str
    payload: dict[str, Any]


def _make_id(service_name: str, doc_type: str, key: str) -> str:
    raw = f"{service_name}:{doc_type}:{key}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _build_payload(
    *,
    service_name: str,
    doc_type: str,
    migration_status: str,
    version: str,
    source_file: str,
    chunk_text: str,
    operation_id: str | None = None,
    http_method: str | None = None,
    path: str | None = None,
    tags: list[str] | None = None,
    auth_type: str | None = None,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "service_name": service_name,
        "doc_type": doc_type,
        "operation_id": operation_id,
        "http_method": http_method,
        "path": path,
        "tags": tags or [],
        "auth_type": auth_type,
        "migration_status": migration_status,
        "version": version,
        "depends_on": depends_on or [],
        "source_file": source_file,
        "chunk_text": chunk_text,
    }


def _render_schema(schema: dict, indent: int = 2) -> str:
    text = yaml.dump(schema, sort_keys=False, default_flow_style=False)
    pad = " " * indent
    return "\n".join(pad + line for line in text.splitlines())


def flatten_endpoint(record: EndpointRecord) -> str:
    lines = [f"{record.http_method} {record.path}"]

    if record.summary:
        lines.append(f"Summary: {record.summary}")
    if record.description:
        lines.append(f"Description: {record.description}")

    lines.append(f"Tags: {', '.join(record.tags) if record.tags else 'none'}")
    lines.append(f"Auth: {record.auth_type or 'none'}")
    lines.append(f"Depends on: {', '.join(record.depends_on) if record.depends_on else 'none'}")

    lines.append("\nParameters:")
    if record.params:
        for p in record.params:
            required = "required" if p.get("required") else "optional"
            desc = p.get("description", "")
            ptype = p.get("schema", {}).get("type", "any")
            lines.append(f"  - {p.get('name')} ({p.get('in')}, {ptype}, {required}): {desc}")
    else:
        lines.append("  none")

    lines.append("\nRequest body schema:")
    if record.request_body_schema:
        lines.append(_render_schema(record.request_body_schema))
    else:
        lines.append("  none")

    lines.append("\nResponses:")
    for code, resp in record.responses.items():
        lines.append(f"  {code}: {resp.get('description', '')}")
        schema = resp.get("schema")
        if schema:
            lines.append(_render_schema(schema, indent=4))

    return "\n".join(lines)


def _load_cache() -> dict[str, str]:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


def _get_client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


def _llm_summarize(record: EndpointRecord) -> str:
    prompt = (
        "Write one plain sentence summarizing this API operation: what it does "
        "and any key dependencies. No markdown, no preamble.\n\n"
        f"Service: {record.service_name}\n"
        f"Method: {record.http_method}\n"
        f"Path: {record.path}\n"
        f"Operation ID: {record.operation_id}\n"
        f"Depends on: {', '.join(record.depends_on) or 'none'}\n"
        f"Parameters: {[p.get('name') for p in record.params]}\n"
    )
    response = _get_client().messages.create(
        model=config.ROUTER_MODEL,
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def summarize_endpoint(record: EndpointRecord) -> str:
    depends = f", depends on {', '.join(record.depends_on)}" if record.depends_on else ""

    if record.summary or record.description:
        text = record.summary or record.description
        return f"{record.http_method} {record.path} - {text}{depends}"

    cache_key = f"{record.service_name}:{record.operation_id}"
    cache = _load_cache()
    if cache_key in cache:
        return cache[cache_key]

    summary = _llm_summarize(record)
    cache[cache_key] = summary
    _save_cache(cache)
    return summary


def _first_readme_section(text: str) -> str:
    marker = "\n## "
    first = text.find(marker)
    if first == -1:
        return text.strip()
    second = text.find(marker, first + 1)
    end = second if second != -1 else len(text)
    return text[:end].strip()


def build_chunks(service_parse: ServiceParse) -> list[Chunk]:
    service_name = service_parse.service_name
    service_dir = service_parse.service_dir
    info = service_parse.info
    version = info.get("version", "0.0.0")
    migration_status = info.get("x-migration-status", "not_started")
    service_depends_on = info.get("x-depends-on", [])
    spec_source_file = os.path.join(service_dir, "openapi.yaml")

    chunks: list[Chunk] = []

    for record in service_parse.endpoints:
        chunk_text = flatten_endpoint(record)
        chunks.append(
            Chunk(
                id=_make_id(service_name, "endpoint", record.operation_id),
                embed_text=summarize_endpoint(record),
                chunk_text=chunk_text,
                payload=_build_payload(
                    service_name=service_name,
                    doc_type="endpoint",
                    operation_id=record.operation_id,
                    http_method=record.http_method,
                    path=record.path,
                    tags=record.tags,
                    auth_type=record.auth_type,
                    migration_status=record.migration_status,
                    version=record.version,
                    depends_on=record.depends_on,
                    source_file=spec_source_file,
                    chunk_text=chunk_text,
                ),
            )
        )

    readme_path = os.path.join(service_dir, "README.md")
    first_section = ""
    if os.path.exists(readme_path):
        with open(readme_path) as f:
            first_section = _first_readme_section(f.read())

    overview_lines = [
        f"Service: {service_name}",
        f"Version: {version}",
        f"Migration status: {migration_status}",
        f"Team owner: {info.get('x-team-owner', 'unknown')}",
        f"Depends on: {', '.join(service_depends_on) if service_depends_on else 'none'}",
        f"Description: {info.get('description', '').strip()}",
        "",
        first_section,
    ]
    overview_text = "\n".join(overview_lines).strip()
    overview_embed = f"{service_name} service overview: {info.get('description', '').strip()}"[:300]

    chunks.append(
        Chunk(
            id=_make_id(service_name, "overview", "overview"),
            embed_text=overview_embed,
            chunk_text=overview_text,
            payload=_build_payload(
                service_name=service_name,
                doc_type="overview",
                depends_on=service_depends_on,
                migration_status=migration_status,
                version=version,
                source_file=spec_source_file,
                chunk_text=overview_text,
            ),
        )
    )

    for name, schema in service_parse.shared_schemas.items():
        chunk_text = f"Schema: {name}\n\n{_render_schema(schema, indent=0)}"
        embed_text = f"{name} schema used across {service_name} endpoints"
        chunks.append(
            Chunk(
                id=_make_id(service_name, "schema", name),
                embed_text=embed_text,
                chunk_text=chunk_text,
                payload=_build_payload(
                    service_name=service_name,
                    doc_type="schema",
                    migration_status=migration_status,
                    version=version,
                    source_file=spec_source_file,
                    chunk_text=chunk_text,
                ),
            )
        )

    for md_file in sorted(glob.glob(os.path.join(service_dir, "*.md"))):
        for i, section in enumerate(parse_prose.parse_prose_file(md_file)):
            key = f"{os.path.basename(md_file)}#{i}"
            chunks.append(
                Chunk(
                    id=_make_id(service_name, section.doc_type, key),
                    embed_text=section.embed_text,
                    chunk_text=section.chunk_text,
                    payload=_build_payload(
                        service_name=service_name,
                        doc_type=section.doc_type,
                        migration_status=migration_status,
                        version=version,
                        source_file=md_file,
                        chunk_text=section.chunk_text,
                    ),
                )
            )

    return chunks


def main() -> None:
    service_dir = sys.argv[1]
    service_parse = parse_service(service_dir)
    chunks = build_chunks(service_parse)

    print(f"total chunks: {len(chunks)}")
    by_type = Counter(c.payload["doc_type"] for c in chunks)
    for doc_type, count in sorted(by_type.items()):
        print(f"  {doc_type}: {count}")

    endpoint_chunk = next((c for c in chunks if c.payload["doc_type"] == "endpoint"), None)
    if endpoint_chunk:
        print("\nSample endpoint chunk embed_text:")
        print(endpoint_chunk.embed_text)
        print("\nSample endpoint chunk chunk_text:")
        print(endpoint_chunk.chunk_text)


if __name__ == "__main__":
    main()
