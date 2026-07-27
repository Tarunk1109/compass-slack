"""Spec parsing. These run against the real corpus and make no network calls."""

import pytest
from conftest import service_path

from src.ingest.parse_openapi import parse_service

CLEAN_SERVICES = ["payments", "auth", "ledger", "accounts", "notifications", "cards"]
MESSY_SERVICES = ["fraud", "kyc", "transfers", "fx", "statements", "disputes"]
ALL_SERVICES = CLEAN_SERVICES + MESSY_SERVICES


@pytest.mark.parametrize("name", ALL_SERVICES)
def test_every_service_parses_without_crashing(name):
    """The messy specs are the point of this test. Missing descriptions,
    absent x- extensions, and deep $ref nesting must not raise."""
    parsed = parse_service(service_path(name))
    assert parsed.endpoints, f"{name} parsed to zero endpoints"


def test_payments_endpoint_count():
    assert len(parse_service(service_path("payments")).endpoints) == 5


def test_corpus_endpoint_total():
    total = sum(len(parse_service(service_path(n)).endpoints) for n in ALL_SERVICES)
    assert total == 62


@pytest.mark.parametrize("name", ALL_SERVICES)
def test_required_fields_are_never_none(name):
    """Downstream chunking assumes these are always populated, including for
    messy specs where the source fields are missing entirely."""
    for record in parse_service(service_path(name)).endpoints:
        assert record.operation_id
        assert record.http_method in ("GET", "POST", "PUT", "PATCH", "DELETE")
        assert record.path.startswith("/")
        assert record.service_name == name
        assert isinstance(record.depends_on, list)
        assert isinstance(record.tags, list)
        assert record.migration_status in ("not_started", "in_progress", "migrated")


@pytest.mark.parametrize("name", ALL_SERVICES)
def test_refs_are_fully_resolved(name):
    """A dangling $ref means the generator receives an unreadable schema."""
    for record in parse_service(service_path(name)).endpoints:
        assert "$ref" not in str(record.request_body_schema or {})
        assert "$ref" not in str(record.responses)


def test_planted_dependencies_survive_parsing():
    """These specific edges are what the dependency demo depends on."""
    payments = {e.operation_id: e for e in parse_service(service_path("payments")).endpoints}
    assert "fraud" in payments["createPayment"].depends_on
    assert "ledger" in payments["createPayment"].depends_on
    assert "ledger" in payments["refundPayment"].depends_on
    assert "notifications" in payments["refundPayment"].depends_on

    accounts = {e.operation_id: e for e in parse_service(service_path("accounts")).endpoints}
    assert "kyc" in accounts["openAccount"].depends_on


def test_planted_migration_statuses():
    assert parse_service(service_path("ledger")).endpoints[0].migration_status == "migrated"
    assert parse_service(service_path("transfers")).endpoints[0].migration_status == "in_progress"


def test_operation_level_and_service_level_dependencies_merge():
    transfers = parse_service(service_path("transfers"))
    service_deps = set(transfers.info.get("x-depends-on", []))
    assert service_deps, "transfers should declare service-level dependencies"
    for record in transfers.endpoints:
        assert service_deps.issubset(set(record.depends_on))


def test_operation_id_falls_back_when_missing(tmp_path):
    """Messy auto-generated specs sometimes omit operationId entirely."""
    spec = tmp_path / "openapi.yaml"
    spec.write_text(
        "openapi: 3.1.0\n"
        "info:\n"
        "  title: nameless\n"
        "  version: 1.0.0\n"
        "paths:\n"
        "  /widgets:\n"
        "    get:\n"
        "      summary: List widgets\n"
        "      responses:\n"
        "        '200':\n"
        "          description: ok\n"
    )
    record = parse_service(str(tmp_path)).endpoints[0]
    assert record.operation_id == "get_/widgets"


def test_missing_extensions_fall_back_to_defaults(tmp_path):
    """No x-migration-status, no x-depends-on, no x-service-name."""
    spec = tmp_path / "openapi.yaml"
    spec.write_text(
        "openapi: 3.1.0\n"
        "info:\n"
        "  title: bare\n"
        "  version: 2.1.0\n"
        "paths:\n"
        "  /things:\n"
        "    get:\n"
        "      operationId: listThings\n"
        "      responses:\n"
        "        default:\n"
        "          description: whatever\n"
    )
    parsed = parse_service(str(tmp_path))
    record = parsed.endpoints[0]
    assert parsed.service_name == tmp_path.name
    assert record.migration_status == "not_started"
    assert record.depends_on == []
    assert record.version == "2.1.0"
    assert record.auth_type is None


def test_shared_schema_detection_requires_three_references():
    """Money is shared across payments operations, so it earns its own chunk."""
    payments = parse_service(service_path("payments"))
    assert "Money" in payments.shared_schemas
    assert isinstance(payments.shared_schemas["Money"], dict)


def test_shared_schemas_are_resolved_not_refs():
    for name in ALL_SERVICES:
        for schema in parse_service(service_path(name)).shared_schemas.values():
            assert "$ref" not in str(schema)
