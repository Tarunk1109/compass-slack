"""Chunk construction. A guard fixture makes any accidental LLM call fail loudly,
so this file is guaranteed to stay offline and free to run."""

import pytest
from conftest import service_path

from src.ingest import chunk as chunk_mod
from src.ingest.chunk import Chunk, build_chunks, flatten_endpoint, summarize_endpoint
from src.ingest.parse_openapi import EndpointRecord, parse_service

ALL_SERVICES = [
    "payments", "auth", "ledger", "accounts", "notifications", "cards",
    "fraud", "kyc", "transfers", "fx", "statements", "disputes",
]


@pytest.fixture(autouse=True)
def no_llm_calls(monkeypatch):
    def explode(record):
        raise AssertionError(f"unexpected LLM call for {record.operation_id}")

    monkeypatch.setattr(chunk_mod, "_llm_summarize", explode)


def make_record(**overrides) -> EndpointRecord:
    defaults = dict(
        service_name="payments",
        operation_id="createPayment",
        http_method="POST",
        path="/v2/payments",
        summary="Create a payment",
        description="Creates a payment and returns it.",
        params=[{"name": "Idempotency-Key", "in": "header", "required": True,
                 "description": "Dedupe key", "schema": {"type": "string"}}],
        request_body_schema={"type": "object", "properties": {"amount": {"type": "integer"}}},
        responses={"201": {"description": "Created", "schema": {"type": "object"}},
                   "409": {"description": "Duplicate idempotency key", "schema": None}},
        auth_type="bearer",
        tags=["payments"],
        depends_on=["fraud", "ledger"],
        version="2.0.0",
        migration_status="in_progress",
    )
    defaults.update(overrides)
    return EndpointRecord(**defaults)


# --- flatten_endpoint -------------------------------------------------------

def test_flattened_text_contains_everything_the_model_needs():
    text = flatten_endpoint(make_record())
    for expected in ["POST /v2/payments", "Create a payment", "Idempotency-Key",
                     "409", "Duplicate idempotency key", "bearer", "fraud", "ledger"]:
        assert expected in text, f"missing {expected!r} from flattened endpoint"


def test_flattened_text_handles_completely_bare_endpoint():
    text = flatten_endpoint(make_record(
        summary="", description="", params=[], request_body_schema=None,
        responses={}, auth_type=None, tags=[], depends_on=[],
    ))
    assert "POST /v2/payments" in text
    assert "none" in text


# --- summarize_endpoint -----------------------------------------------------

def test_summary_is_built_from_fields_when_present():
    summary = summarize_endpoint(make_record())
    assert "POST /v2/payments" in summary
    assert "Create a payment" in summary
    assert "fraud" in summary


def test_summary_falls_back_to_description_when_summary_missing():
    assert "Creates a payment" in summarize_endpoint(make_record(summary=""))


def test_llm_is_used_only_when_both_summary_and_description_are_missing(monkeypatch, tmp_path):
    """The one path the real corpus never exercises, so it needs a test."""
    calls = []

    def fake_llm(record):
        calls.append(record.operation_id)
        return "generated summary"

    monkeypatch.setattr(chunk_mod, "_llm_summarize", fake_llm)
    monkeypatch.setattr(chunk_mod, "CACHE_PATH", str(tmp_path / "cache.json"))

    assert summarize_endpoint(make_record(summary="", description="")) == "generated summary"
    assert calls == ["createPayment"]


def test_generated_summaries_are_cached_so_reruns_are_free(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(chunk_mod, "_llm_summarize",
                        lambda r: (calls.append(r.operation_id), "cached summary")[1])
    monkeypatch.setattr(chunk_mod, "CACHE_PATH", str(tmp_path / "cache.json"))

    record = make_record(summary="", description="")
    summarize_endpoint(record)
    summarize_endpoint(record)
    assert len(calls) == 1, "second call should have hit the cache"


# --- chunk ids --------------------------------------------------------------

def test_chunk_ids_are_stable_across_runs():
    """Unstable ids would duplicate every point in Qdrant on re-ingestion."""
    first = {c.id for c in build_chunks(parse_service(service_path("payments")))}
    second = {c.id for c in build_chunks(parse_service(service_path("payments")))}
    assert first == second


def test_chunk_ids_are_unique_across_the_whole_corpus():
    seen: dict[str, str] = {}
    for name in ALL_SERVICES:
        for c in build_chunks(parse_service(service_path(name))):
            assert c.id not in seen, f"id collision: {name} vs {seen[c.id]}"
            seen[c.id] = f"{name}/{c.payload['doc_type']}/{c.payload.get('operation_id')}"


def test_different_services_produce_different_ids():
    a = {c.id for c in build_chunks(parse_service(service_path("payments")))}
    b = {c.id for c in build_chunks(parse_service(service_path("ledger")))}
    assert not (a & b)


# --- build_chunks -----------------------------------------------------------

def test_every_service_gets_exactly_one_overview_chunk():
    for name in ALL_SERVICES:
        chunks = build_chunks(parse_service(service_path(name)))
        overviews = [c for c in chunks if c.payload["doc_type"] == "overview"]
        assert len(overviews) == 1, f"{name} produced {len(overviews)} overview chunks"


def test_one_chunk_per_endpoint_never_split_never_merged():
    """The single most important invariant in the whole project."""
    for name in ALL_SERVICES:
        parsed = parse_service(service_path(name))
        chunks = build_chunks(parsed)
        endpoint_chunks = [c for c in chunks if c.payload["doc_type"] == "endpoint"]
        assert len(endpoint_chunks) == len(parsed.endpoints)


def test_endpoint_chunks_embed_short_text_but_store_full_text():
    """Embedding the summary and storing the detail is the core retrieval trick."""
    chunks = build_chunks(parse_service(service_path("payments")))
    for c in (c for c in chunks if c.payload["doc_type"] == "endpoint"):
        assert len(c.embed_text) < len(c.chunk_text)
        assert c.payload["chunk_text"] == c.chunk_text


def test_every_chunk_carries_the_metadata_needed_for_citation_and_filtering():
    required = ["service_name", "doc_type", "migration_status", "version",
                "source_file", "chunk_text", "depends_on", "tags"]
    for name in ALL_SERVICES:
        for c in build_chunks(parse_service(service_path(name))):
            for field in required:
                assert field in c.payload, f"{name} chunk missing {field}"
            assert c.payload["chunk_text"].strip()
            assert c.embed_text.strip()


def test_prose_documents_become_chunks():
    """Regression guard: a chunking bug here once nearly broke the
    idempotency demo, whose answer lives only in the README."""
    chunks = build_chunks(parse_service(service_path("payments")))
    readme_chunks = [c for c in chunks if c.payload["doc_type"] == "readme"]
    assert readme_chunks, "payments README produced no chunks"
    assert any("idempotenc" in c.chunk_text.lower() for c in readme_chunks)


def test_adr_documents_are_typed_correctly():
    chunks = build_chunks(parse_service(service_path("ledger")))
    assert any(c.payload["doc_type"] == "adr" for c in chunks)


def test_shared_schemas_become_their_own_chunks():
    chunks = build_chunks(parse_service(service_path("payments")))
    schema_chunks = [c for c in chunks if c.payload["doc_type"] == "schema"]
    assert any("Money" in c.chunk_text for c in schema_chunks)


def test_corpus_produces_the_expected_chunk_total():
    total = sum(len(build_chunks(parse_service(service_path(n)))) for n in ALL_SERVICES)
    assert total == 175, f"corpus chunk count drifted to {total}"
