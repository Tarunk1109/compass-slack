import argparse
import json
import re
import uuid
from collections import Counter, defaultdict

from src.agent.graph import _get_graph
from src.agent.graph import run as run_agent

GOLDEN_PATH = "eval/golden.jsonl"

CITATION_RE = re.compile(r"\[([a-zA-Z0-9_-]+) */")
REFUSAL_PHRASES = [
    "couldn't find",
    "could not find",
    "don't have",
    "do not have",
    "no information",
    "not documented",
    "doesn't exist",
    "does not exist",
    "no such service",
    "not aware of",
    "unable to find",
    "not able to find",
    "no such",
]


def _load_golden(path: str) -> list[dict]:
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _extract_cited_services(answer: str) -> set[str]:
    return {m.group(1).lower() for m in CITATION_RE.finditer(answer)}


def _check_turn(turn: dict, answer: str) -> list[str]:
    """Returns a list of failure reasons. Empty list means the turn passed."""
    failures = []
    answer_lower = answer.lower()
    cited = _extract_cited_services(answer)

    expected_services = turn.get("expected_services")
    if expected_services:
        if not any(svc.lower() in cited for svc in expected_services):
            failures.append(f"expected a citation from one of {expected_services}, got citations: {sorted(cited)}")

    if turn.get("expects_retrieval") is False and cited:
        failures.append(f"expected no retrieval/citations, but found citations: {sorted(cited)}")

    for keyword in turn.get("answer_should_contain", []):
        if keyword.lower() not in answer_lower:
            failures.append(f"expected answer to contain '{keyword}', it did not")

    one_of = turn.get("answer_should_contain_one_of")
    if one_of and not any(keyword.lower() in answer_lower for keyword in one_of):
        failures.append(f"expected answer to contain at least one of {one_of}, it contained none")

    for keyword in turn.get("answer_should_not_contain", []):
        if keyword.lower() in answer_lower:
            failures.append(f"expected answer to NOT contain '{keyword}', but it did")

    if turn.get("expects_refusal") and not any(phrase in answer_lower for phrase in REFUSAL_PHRASES):
        failures.append("expected a graceful refusal/uncertainty, none detected")

    return failures


def _retrieval_metrics(entry: dict, first_turn_results: list[dict]) -> tuple[bool, float] | None:
    """Hit rate and reciprocal rank for an entry's first turn, computed from
    the results the agent's own retrieve() node actually used (pulled from
    graph state), if the entry declares expected_services."""
    expected_services = entry["conversation"][0].get("expected_services")
    if not expected_services:
        return None

    expected_lower = {s.lower() for s in expected_services}
    for rank, r in enumerate(first_turn_results, start=1):
        if (r.get("service_name") or "").lower() in expected_lower:
            return True, 1.0 / rank
    return False, 0.0


def run_eval(limit: int | None = None, ids: list[str] | None = None) -> None:
    entries = _load_golden(GOLDEN_PATH)
    if ids:
        wanted = set(ids)
        entries = [e for e in entries if e["id"] in wanted]
    elif limit:
        entries = entries[:limit]

    category_stats: dict[str, Counter] = defaultdict(Counter)
    failures_log = []
    manual_review_log = []
    hits = []
    reciprocal_ranks = []

    for i, entry in enumerate(entries, start=1):
        category = entry["category"]
        print(f"[{i}/{len(entries)}] {entry['id']} ({category})...", flush=True)

        if entry["conversation"][0].get("manual_review"):
            session_id = f"eval-{entry['id']}-{uuid.uuid4().hex[:8]}"
            answer = run_agent(entry["conversation"][0]["question"], session_id=session_id)
            manual_review_log.append((entry["id"], entry["conversation"][0]["question"], answer))
            category_stats[category]["manual_review"] += 1
            continue

        session_id = f"eval-{entry['id']}-{uuid.uuid4().hex[:8]}"
        entry_failures = []
        for turn_index, turn in enumerate(entry["conversation"]):
            answer = run_agent(turn["question"], session_id=session_id)

            if turn_index == 0:
                state = _get_graph().get_state({"configurable": {"thread_id": session_id}})
                first_turn_results = state.values.get("results", [])
                retrieval_result = _retrieval_metrics(entry, first_turn_results)
                if retrieval_result is not None:
                    hit, rr = retrieval_result
                    hits.append(hit)
                    reciprocal_ranks.append(rr)

            turn_failures = _check_turn(turn, answer)
            if turn_failures:
                entry_failures.append((turn_index, turn["question"], answer, turn_failures))

        if entry_failures:
            category_stats[category]["fail"] += 1
            failures_log.append((entry["id"], category, entry_failures))
        else:
            category_stats[category]["pass"] += 1

    total_pass = sum(c["pass"] for c in category_stats.values())
    total_fail = sum(c["fail"] for c in category_stats.values())
    total_manual = sum(c["manual_review"] for c in category_stats.values())
    total_scored = total_pass + total_fail

    print(f"\n{'=' * 60}")
    print(f"EVAL RESULTS: {total_pass}/{total_scored} passed ({total_manual} flagged for manual review)")
    print(f"{'=' * 60}\n")

    print("By category:")
    for category in sorted(category_stats):
        c = category_stats[category]
        scored = c["pass"] + c["fail"]
        if scored:
            print(f"  {category:30s} {c['pass']}/{scored} passed")
        if c["manual_review"]:
            print(f"  {category:30s} {c['manual_review']} flagged for manual review")

    if hits:
        hit_rate = sum(hits) / len(hits)
        mrr = sum(reciprocal_ranks) / len(reciprocal_ranks)
        print(f"\nRetrieval metrics (from the agent's actual retrieve() results, n={len(hits)}):")
        print(f"  Hit Rate: {hit_rate:.1%}")
        print(f"  MRR: {mrr:.3f}")

    if failures_log:
        print(f"\n{'=' * 60}")
        print("FAILURES")
        print(f"{'=' * 60}")
        for entry_id, category, entry_failures in failures_log:
            print(f"\n[{entry_id}] ({category})")
            for turn_index, question, answer, reasons in entry_failures:
                print(f"  turn {turn_index}: {question}")
                for reason in reasons:
                    print(f"    - {reason}")
                print(f"    answer: {answer[:200]}")

    if manual_review_log:
        print(f"\n{'=' * 60}")
        print("MANUAL REVIEW")
        print(f"{'=' * 60}")
        for entry_id, question, answer in manual_review_log:
            print(f"\n[{entry_id}] {question}")
            print(f"  answer: {answer[:300]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="only run the first N golden entries")
    parser.add_argument("--ids", type=str, default=None, help="comma-separated list of entry ids to run")
    args = parser.parse_args()
    ids = args.ids.split(",") if args.ids else None
    run_eval(limit=args.limit, ids=ids)


if __name__ == "__main__":
    main()
