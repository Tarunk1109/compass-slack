"""Markdown splitting. Prose chunks carry the planted gotchas that exist
nowhere in the OpenAPI specs, so breakage here silently loses demo answers."""

import os

import pytest
from conftest import service_path

from src.ingest.parse_prose import parse_prose_file


def write(tmp_path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text)
    return str(path)


# --- doc_type classification ------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("README.md", "readme"),
    ("readme.md", "readme"),
    ("runbook.md", "runbook"),
    ("runbook-oncall.md", "runbook"),
    ("adr-001-double-entry.md", "adr"),
    ("ADR-002-thing.md", "adr"),
    ("notes.md", "readme"),
])
def test_doc_type_inferred_from_filename(tmp_path, filename, expected):
    path = write(tmp_path, filename, "# Title\n\nSome body text.\n")
    assert parse_prose_file(path)[0].doc_type == expected


# --- heading splitting ------------------------------------------------------

def test_each_heading_becomes_its_own_section(tmp_path):
    path = write(tmp_path, "README.md",
                 "# Service\n\nIntro.\n\n## Setup\n\nRun it.\n\n## Gotchas\n\nBeware.\n")
    sections = parse_prose_file(path)
    assert len(sections) == 3


def test_heading_path_is_prepended_so_chunks_keep_context(tmp_path):
    """A chunk that says only 'Beware' is useless once retrieved out of context."""
    path = write(tmp_path, "README.md",
                 "# Payments\n\nIntro.\n\n## Gotchas\n\nRetries return 409.\n")
    gotcha = [s for s in parse_prose_file(path) if "409" in s.chunk_text][0]
    assert gotcha.chunk_text.startswith("Payments > Gotchas")
    assert gotcha.heading_path == ["Payments", "Gotchas"]


def test_nested_headings_build_a_full_breadcrumb(tmp_path):
    path = write(tmp_path, "README.md",
                 "# A\n\nx\n\n## B\n\ny\n\n### C\n\nz\n")
    deepest = parse_prose_file(path)[-1]
    assert deepest.heading_path == ["A", "B", "C"]


def test_sibling_heading_pops_the_stack(tmp_path):
    """A second ## must not inherit the first ##'s breadcrumb."""
    path = write(tmp_path, "README.md",
                 "# Top\n\nx\n\n## First\n\ny\n\n### Deep\n\nz\n\n## Second\n\nw\n")
    second = [s for s in parse_prose_file(path) if s.heading_path[-1] == "Second"][0]
    assert second.heading_path == ["Top", "Second"]


def test_document_with_no_headings_still_produces_a_chunk(tmp_path):
    path = write(tmp_path, "README.md", "Just a paragraph with no heading at all.\n")
    sections = parse_prose_file(path)
    assert len(sections) == 1
    assert "README.md" in sections[0].chunk_text


def test_empty_sections_are_dropped(tmp_path):
    path = write(tmp_path, "README.md", "# Title\n\n## Empty\n\n## Real\n\nContent here.\n")
    assert all(s.chunk_text.strip() for s in parse_prose_file(path))


# --- size control -----------------------------------------------------------

def test_oversized_section_is_split(tmp_path):
    body = "\n\n".join(["word " * 200] * 6)
    path = write(tmp_path, "README.md", f"# Big\n\n{body}\n")
    sections = parse_prose_file(path)
    assert len(sections) > 1
    assert all(s.heading_path == ["Big"] for s in sections)


def test_normal_section_is_not_split(tmp_path):
    path = write(tmp_path, "README.md", "# Small\n\n" + "word " * 50)
    assert len(parse_prose_file(path)) == 1


# --- embed text -------------------------------------------------------------

def test_embed_text_is_short_and_starts_with_the_breadcrumb(tmp_path):
    path = write(tmp_path, "README.md", "# Payments\n\n## Gotchas\n\nRetries return 409.\n")
    section = parse_prose_file(path)[-1]
    assert section.embed_text.startswith("Payments > Gotchas")
    assert len(section.embed_text) <= 300


def test_embed_text_skips_the_heading_line_for_its_body_snippet(tmp_path):
    path = write(tmp_path, "README.md", "# Title\n\nThe actual first sentence.\n")
    assert "The actual first sentence." in parse_prose_file(path)[0].embed_text


# --- real corpus ------------------------------------------------------------

@pytest.mark.parametrize("service,filename", [
    ("payments", "README.md"),
    ("fraud", "README.md"),
    ("fraud", "adr-001-fail-closed-policy.md"),
    ("ledger", "adr-001-double-entry.md"),
    ("transfers", "README.md"),
])
def test_real_corpus_documents_parse(service, filename):
    path = os.path.join(service_path(service), filename)
    sections = parse_prose_file(path)
    assert sections
    assert all(s.chunk_text.strip() and s.embed_text.strip() for s in sections)


@pytest.mark.parametrize("service,keyword", [
    ("payments", "idempotenc"),
    ("fraud", "fail"),
    ("transfers", "rate"),
])
def test_planted_gotchas_survive_chunking(service, keyword):
    """These answers exist only in prose, never in the specs. If chunking
    loses them, the demo answers them wrong with no visible error."""
    path = os.path.join(service_path(service), "README.md")
    text = " ".join(s.chunk_text.lower() for s in parse_prose_file(path))
    assert keyword in text
