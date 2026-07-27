"""Citation verify-and-repair. All LLM calls are mocked, so this stays offline."""

import pytest
from conftest import service_path  # noqa: F401  (path setup)

from src.agent import graph as graph_mod
from src.agent.graph import (
    CITATION_STATS,
    _citation_label,
    _has_citation,
    _looks_like_refusal,
    _normalize_citations,
    _repair_citations,
)

SOURCES = "[payments / openapi.yaml / createPayment]\nPOST /v2/payments creates a payment."


@pytest.fixture(autouse=True)
def reset_stats():
    for key in CITATION_STATS:
        CITATION_STATS[key] = 0
    yield


# --- citation labels --------------------------------------------------------

def test_label_uses_basename_not_the_full_path():
    """The dry run produced the same source cited as both 'README.md' and
    'data/services/payments/README.md'. The label is built here so it cannot."""
    label = _citation_label({
        "service_name": "payments",
        "source_file": "data/services/payments/README.md",
        "operation_id": None,
    })
    assert label == "payments / README.md"


def test_label_includes_operation_id_when_present():
    label = _citation_label({
        "service_name": "payments",
        "source_file": "data/services/payments/openapi.yaml",
        "operation_id": "createPayment",
    })
    assert label == "payments / openapi.yaml / createPayment"


@pytest.mark.parametrize("operation_id", [None, ""])
def test_label_omits_missing_operation_id_rather_than_writing_na(operation_id):
    """A trailing 'n/a' appeared in 8 of 10 citation forms during the dry run."""
    label = _citation_label({
        "service_name": "fraud",
        "source_file": "data/services/fraud/README.md",
        "operation_id": operation_id,
    })
    assert "n/a" not in label
    assert label == "fraud / README.md"


def test_label_survives_missing_fields():
    assert _citation_label({}) == "unknown"


def test_label_is_recognised_as_a_citation_once_bracketed():
    label = _citation_label({
        "service_name": "ledger",
        "source_file": "data/services/ledger/adr-001-double-entry.md",
        "operation_id": None,
    })
    assert _has_citation(f"Some claim [{label}].")


# --- bracket normalization --------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    # Observed verbatim in the dry run.
    ("Claim [[payments / README.md]].", "Claim [payments / README.md]."),
    ("Status [[fraud / openapi.yaml], [fraud / README.md]].",
     "Status [fraud / openapi.yaml] [fraud / README.md]."),
    ("Two [[payments / README.md]] [[cards / README.md]] here.",
     "Two [payments / README.md] [cards / README.md] here."),
])
def test_nested_brackets_are_flattened(raw, expected):
    assert _normalize_citations(raw) == expected


def test_correct_citations_are_left_alone():
    text = "Use [payments / openapi.yaml / createPayment] and [fraud / README.md]."
    assert _normalize_citations(text) == text


def test_normalization_does_not_touch_ordinary_prose():
    text = "A list item [not a citation] and some code `arr[0]` stay put."
    assert _normalize_citations(text) == text


def test_normalization_is_idempotent():
    once = _normalize_citations("Claim [[payments / README.md]].")
    assert _normalize_citations(once) == once


def test_normalization_runs_before_the_missing_citation_check(monkeypatch):
    """A doubled-bracket citation is a real citation. It must not trigger a
    repair call just because its shape was wrong."""
    monkeypatch.setattr(graph_mod, "_call_model",
                        lambda *a, **k: pytest.fail("should not call the model"))
    result = _repair_citations("Claim [[payments / README.md]].", SOURCES)
    assert result == "Claim [payments / README.md]."
    assert CITATION_STATS["missing"] == 0


# --- detection --------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Call it [payments / openapi.yaml / createPayment].",
    "See [fraud / README.md] for the fail-closed rule.",
    "Multi line\nanswer with [ledger / adr-001-double-entry.md] inside.",
])
def test_detects_present_citations(text):
    assert _has_citation(text)


@pytest.mark.parametrize("text", [
    "Just call the createPayment endpoint, it works fine.",
    "You use POST /v2/payments with an Idempotency-Key header.",
    "",
    "Brackets [but no slash] here.",
])
def test_detects_missing_citations(text):
    assert not _has_citation(text)


@pytest.mark.parametrize("text", [
    "I couldn't find anything relevant in the service docs.",
    "That is not documented anywhere in these specs.",
    "There is no information about a shipping service here.",
])
def test_detects_refusals(text):
    assert _looks_like_refusal(text)


def test_normal_answer_is_not_mistaken_for_a_refusal():
    assert not _looks_like_refusal("Call POST /v2/payments to create a payment.")


# --- repair behaviour -------------------------------------------------------

def test_cited_answer_is_passed_through_untouched(monkeypatch):
    monkeypatch.setattr(graph_mod, "_call_model",
                        lambda *a, **k: pytest.fail("should not call the model"))
    answer = "Use [payments / openapi.yaml / createPayment]."
    assert _repair_citations(answer, SOURCES) == answer
    assert CITATION_STATS["missing"] == 0


def test_refusal_is_never_sent_for_repair(monkeypatch):
    """A refusal has nothing to cite. Repairing it would waste a call and
    risk the model inventing a source to satisfy the format."""
    monkeypatch.setattr(graph_mod, "_call_model",
                        lambda *a, **k: pytest.fail("should not call the model"))
    answer = "I couldn't find anything relevant in the service docs."
    assert _repair_citations(answer, SOURCES) == answer
    assert CITATION_STATS["missing"] == 0


def test_uncited_answer_is_repaired(monkeypatch):
    monkeypatch.setattr(graph_mod, "_call_model",
                        lambda *a, **k: "Use POST /v2/payments [payments / openapi.yaml / createPayment].")
    result = _repair_citations("Use POST /v2/payments.", SOURCES)
    assert _has_citation(result)
    assert CITATION_STATS["missing"] == 1
    assert CITATION_STATS["repaired"] == 1


def test_repair_receives_both_the_answer_and_the_sources(monkeypatch):
    captured = {}

    def fake(model, system, user, max_tokens=1000):
        captured["user"] = user
        return "fixed [payments / openapi.yaml / createPayment]"

    monkeypatch.setattr(graph_mod, "_call_model", fake)
    _repair_citations("Use POST /v2/payments.", SOURCES)
    assert "Use POST /v2/payments." in captured["user"]
    assert "createPayment" in captured["user"]


def test_failed_repair_falls_back_to_the_original_answer(monkeypatch):
    """A correct answer without citations still beats no answer at all."""
    monkeypatch.setattr(graph_mod, "_call_model", lambda *a, **k: "still has no citation")
    original = "Use POST /v2/payments."
    assert _repair_citations(original, SOURCES) == original
    assert CITATION_STATS["unrepairable"] == 1


def test_model_error_during_repair_falls_back_safely(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("anthropic is down")

    monkeypatch.setattr(graph_mod, "_call_model", boom)
    original = "Use POST /v2/payments."
    assert _repair_citations(original, SOURCES) == original
    assert CITATION_STATS["unrepairable"] == 1


def test_empty_answer_is_not_sent_for_repair(monkeypatch):
    monkeypatch.setattr(graph_mod, "_call_model",
                        lambda *a, **k: pytest.fail("should not call the model"))
    assert _repair_citations("   ", SOURCES) == "   "


def test_stats_accumulate_across_calls(monkeypatch):
    monkeypatch.setattr(graph_mod, "_call_model",
                        lambda *a, **k: "fixed [payments / openapi.yaml]")
    _repair_citations("uncited one", SOURCES)
    _repair_citations("uncited two", SOURCES)
    _repair_citations("already [payments / openapi.yaml]", SOURCES)
    assert CITATION_STATS["checked"] == 3
    assert CITATION_STATS["missing"] == 2
    assert CITATION_STATS["repaired"] == 2
