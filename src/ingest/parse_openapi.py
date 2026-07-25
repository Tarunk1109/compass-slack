import dataclasses
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import yaml
from prance import ResolvingParser

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
SCHEMA_REF_RE = re.compile(r"#/components/schemas/(.+)$")


@dataclass
class EndpointRecord:
    service_name: str
    operation_id: str
    http_method: str
    path: str
    summary: str
    description: str
    params: list[dict]
    request_body_schema: dict | None
    responses: dict[str, dict]
    auth_type: str | None
    tags: list[str]
    depends_on: list[str]
    version: str
    migration_status: str


@dataclass
class ServiceParse:
    service_name: str
    info: dict
    service_dir: str
    endpoints: list[EndpointRecord] = field(default_factory=list)
    shared_schemas: dict[str, dict] = field(default_factory=dict)


def _first_content_schema(content: dict) -> dict | None:
    if not content:
        return None
    json_content = content.get("application/json", next(iter(content.values()), {}))
    return json_content.get("schema")


def _auth_type(operation: dict, spec: dict) -> str | None:
    security = operation.get("security", spec.get("security", []))
    if not security:
        return None
    scheme_name = next(iter(security[0]), None)
    if not scheme_name:
        return None
    scheme = spec.get("components", {}).get("securitySchemes", {}).get(scheme_name, {})
    scheme_type = scheme.get("type")
    if scheme_type == "http":
        return scheme.get("scheme", scheme_name)
    return scheme_type or scheme_name


def _collect_schema_names(node: Any, raw_schemas: dict, seen: set[str] | None = None) -> set[str]:
    if seen is None:
        seen = set()
    names: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref:
            match = SCHEMA_REF_RE.search(ref)
            if match:
                name = match.group(1)
                if name not in seen:
                    seen.add(name)
                    names.add(name)
                    names |= _collect_schema_names(raw_schemas.get(name, {}), raw_schemas, seen)
            return names
        for value in node.values():
            names |= _collect_schema_names(value, raw_schemas, seen)
    elif isinstance(node, list):
        for item in node:
            names |= _collect_schema_names(item, raw_schemas, seen)
    return names


def _detect_shared_schemas(raw_spec: dict, resolved_schemas: dict) -> dict[str, dict]:
    raw_schemas = raw_spec.get("components", {}).get("schemas", {})
    reference_counts: Counter[str] = Counter()

    for path_item in raw_spec.get("paths", {}).values():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue
            op_names: set[str] = set()
            op_names |= _collect_schema_names(operation.get("requestBody", {}), raw_schemas)
            op_names |= _collect_schema_names(operation.get("responses", {}), raw_schemas)
            reference_counts.update(op_names)

    return {
        name: resolved_schemas[name]
        for name, count in reference_counts.items()
        if count >= 3 and name in resolved_schemas
    }


def parse_service(service_dir: str) -> ServiceParse:
    spec_path = os.path.join(service_dir, "openapi.yaml")

    parser = ResolvingParser(spec_path, strict=False)
    spec = parser.specification

    with open(spec_path) as f:
        raw_spec = yaml.safe_load(f)

    info = spec.get("info", {})
    service_name = info.get("x-service-name") or os.path.basename(os.path.normpath(service_dir))
    version = info.get("version", "0.0.0")
    migration_status = info.get("x-migration-status", "not_started")
    service_depends_on = info.get("x-depends-on", [])

    endpoints: list[EndpointRecord] = []

    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue

            operation_id = operation.get("operationId") or f"{method}_{path}"
            op_depends_on = operation.get("x-depends-on", [])
            depends_on = sorted(set(service_depends_on) | set(op_depends_on))

            request_body_schema = _first_content_schema(
                operation.get("requestBody", {}).get("content", {})
            )

            responses = {}
            for code, response in operation.get("responses", {}).items():
                responses[code] = {
                    "description": response.get("description", ""),
                    "schema": _first_content_schema(response.get("content", {})),
                }

            endpoints.append(
                EndpointRecord(
                    service_name=service_name,
                    operation_id=operation_id,
                    http_method=method.upper(),
                    path=path,
                    summary=operation.get("summary", ""),
                    description=operation.get("description", ""),
                    params=operation.get("parameters", []),
                    request_body_schema=request_body_schema,
                    responses=responses,
                    auth_type=_auth_type(operation, spec),
                    tags=operation.get("tags", []),
                    depends_on=depends_on,
                    version=version,
                    migration_status=migration_status,
                )
            )

    resolved_schemas = spec.get("components", {}).get("schemas", {})
    shared_schemas = _detect_shared_schemas(raw_spec, resolved_schemas)

    return ServiceParse(
        service_name=service_name,
        info=info,
        service_dir=service_dir,
        endpoints=endpoints,
        shared_schemas=shared_schemas,
    )


def _print_record(record: EndpointRecord) -> None:
    for f in dataclasses.fields(record):
        print(f"  {f.name}: {getattr(record, f.name)}")


def main() -> None:
    service_dir = sys.argv[1]
    result = parse_service(service_dir)
    print(f"{result.service_name}: {len(result.endpoints)} endpoints")
    print(f"shared schemas (referenced 3+ times): {sorted(result.shared_schemas.keys())}")
    if result.endpoints:
        print("\nSample endpoint:")
        _print_record(result.endpoints[0])


if __name__ == "__main__":
    main()
