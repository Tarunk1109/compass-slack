import contextlib
import json
import operator
import os
import re
import sys
from typing import Annotated, TypedDict

import anthropic
from langfuse import get_client, observe, propagate_attributes
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src import config
from src.agent.prompts import (
    CHITCHAT_SYSTEM_PROMPT,
    GENERATOR_SYSTEM_PROMPT,
    GRADER_PROMPT,
    REWRITE_PROMPT,
    ROUTER_PROMPT,
)
from src.mcp.server import get_migration_status, list_service_dependencies, search_docs

MAX_RETRIES = 1
HISTORY_TURNS = 3

_LANGFUSE_ENABLED = bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))

_client: anthropic.Anthropic | None = None


def _observe(*args, **kwargs):
    """No-op unless Langfuse keys are configured, so a bare CLI run stays quiet."""
    if _LANGFUSE_ENABLED:
        return observe(*args, **kwargs)
    return lambda fn: fn


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _call_model(model: str, system: str, user: str, max_tokens: int = 500) -> str:
    response = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if _LANGFUSE_ENABLED:
        get_client().update_current_generation(
            model=model,
            input=user,
            output=response.content[0].text,
            usage_details={
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
            },
        )
    return response.content[0].text.strip()


def _parse_json(text: str) -> dict:
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def _format_history(history: list[dict]) -> str:
    if not history:
        return ""
    lines = ["Recent conversation:"]
    for turn in history[-HISTORY_TURNS:]:
        lines.append(f"Q: {turn['question']}")
        lines.append(f"A: {turn['answer']}")
    return "\n".join(lines)


class AgentState(TypedDict):
    question: str
    query: str
    service: str | None
    doc_type: str | None
    migration_status: str | None
    intent: str
    needs_retrieval: bool
    results: list[dict]
    extra_context: str
    relevant: bool
    retries: int
    answer: str
    # Accumulates across turns within a thread_id via the checkpointer, one
    # {"question": ..., "answer": ...} entry per turn. Only generate() adds
    # to this; every other node must leave it out of its return dict, since
    # the operator.add reducer combines whatever is returned with what's
    # already there (including it unchanged elsewhere would double it up).
    history: Annotated[list[dict], operator.add]


@_observe(as_type="generation", name="route")
def route(state: AgentState) -> AgentState:
    history_text = _format_history(state.get("history", []))
    user_content = f"{history_text}\n\nCurrent question: {state['question']}" if history_text else state["question"]

    raw = _call_model(config.ROUTER_MODEL, ROUTER_PROMPT, user_content, max_tokens=300)
    try:
        parsed = _parse_json(raw)
    except (json.JSONDecodeError, ValueError):
        parsed = {
            "needs_retrieval": True,
            "service": None,
            "doc_type": None,
            "migration_status": None,
            "intent": "general",
            "standalone_query": state["question"],
        }

    return {
        "query": parsed.get("standalone_query") or state["question"],
        "service": parsed.get("service"),
        "doc_type": parsed.get("doc_type"),
        "migration_status": parsed.get("migration_status"),
        "intent": parsed.get("intent", "general"),
        "needs_retrieval": bool(parsed.get("needs_retrieval", True)),
        "retries": 0,
        "extra_context": "",
    }


def _dependency_context(service: str) -> str:
    deps = list_service_dependencies(service)
    if deps.get("error"):
        return ""
    lines = [f"Dependency graph for '{service}':"]
    lines.append(f"- {service} depends on: {', '.join(deps['depends_on']) or 'none'}")
    lines.append(f"- services that depend on {service}: {', '.join(deps['depended_on_by']) or 'none'}")
    return "\n".join(lines)


def _migration_context(service: str) -> str:
    status = get_migration_status(service)
    if status.get("error"):
        return ""
    lines = [f"Migration status for '{service}': {status['migration_status']}", status["notes"]]

    info = list_service_dependencies(service)
    for dep in info.get("depends_on", []):
        dep_status = get_migration_status(dep)
        if not dep_status.get("error"):
            lines.append(f"Migration status for its dependency '{dep}': {dep_status['migration_status']}")

    return "\n".join(lines)


@_observe(as_type="retriever", name="retrieve")
def retrieve(state: AgentState) -> AgentState:
    results = search_docs(
        state["query"],
        doc_type=state.get("doc_type"),
        migration_status=state.get("migration_status"),
    )

    extra_context = ""
    service = state.get("service")
    if service:
        if state.get("intent") == "dependency_impact":
            extra_context = _dependency_context(service)
        elif state.get("intent") == "migration_status":
            extra_context = _migration_context(service)

    return {"results": results, "extra_context": extra_context}


@_observe(as_type="generation", name="grade")
def grade(state: AgentState) -> AgentState:
    if not state.get("results") and not state.get("extra_context"):
        return {"relevant": False}
    if state.get("extra_context"):
        return {"relevant": True}

    chunks_text = "\n\n".join(
        f"[{r['service_name']} / {r['source_file']}]: {(r['chunk_text'] or '')[:300]}"
        for r in state["results"][:5]
    )
    raw = _call_model(
        config.ROUTER_MODEL,
        GRADER_PROMPT.format(question=state["question"], chunks=chunks_text),
        "Grade relevance now.",
        max_tokens=50,
    )
    try:
        relevant = bool(_parse_json(raw).get("relevant", True))
    except (json.JSONDecodeError, ValueError):
        relevant = True

    return {"relevant": relevant}


@_observe(as_type="generation", name="rewrite_query")
def rewrite_query(state: AgentState) -> AgentState:
    new_query = _call_model(
        config.ROUTER_MODEL,
        REWRITE_PROMPT.format(question=state["question"], query=state["query"]),
        "Rewrite now.",
        max_tokens=100,
    )
    return {"query": new_query, "retries": state["retries"] + 1}


@_observe(as_type="generation", name="generate")
def generate(state: AgentState) -> AgentState:
    if not state.get("needs_retrieval", True):
        history_text = _format_history(state.get("history", []))
        chitchat_content = f"{history_text}\n\nCurrent question: {state['question']}" if history_text else state["question"]
        answer = _call_model(
            config.ROUTER_MODEL,
            CHITCHAT_SYSTEM_PROMPT,
            chitchat_content,
            max_tokens=200,
        )
        return {"answer": answer, "history": [{"question": state["question"], "answer": answer}]}

    results = state.get("results", [])
    if not results and not state.get("extra_context"):
        answer = "I couldn't find anything relevant in the service docs to answer that."
        return {"answer": answer, "history": [{"question": state["question"], "answer": answer}]}

    chunks_context = "\n\n---\n\n".join(
        f"[{r['service_name']} / {r['source_file']} / {r.get('operation_id') or 'n/a'}]\n{r['chunk_text']}"
        for r in results
    )
    history_text = _format_history(state.get("history", []))
    parts = []
    if history_text:
        parts.append(history_text)
    parts.append(f"Question: {state['question']}")
    if state.get("extra_context"):
        parts.append(f"Structured service metadata:\n{state['extra_context']}")
    if chunks_context:
        parts.append(f"Retrieved chunks:\n\n{chunks_context}")
    user_content = "\n\n".join(parts)

    answer = _call_model(config.GEN_MODEL, GENERATOR_SYSTEM_PROMPT, user_content, max_tokens=1000)
    return {"answer": answer, "history": [{"question": state["question"], "answer": answer}]}


def _needs_retrieval_edge(state: AgentState) -> str:
    return "retrieve" if state["needs_retrieval"] else "generate"


def _grade_edge(state: AgentState) -> str:
    if state["relevant"] or state["retries"] >= MAX_RETRIES:
        return "generate"
    return "rewrite_query"


_checkpointer = MemorySaver()
_compiled_graph = None


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("route", route)
    graph.add_node("retrieve", retrieve)
    graph.add_node("grade", grade)
    graph.add_node("rewrite_query", rewrite_query)
    graph.add_node("generate", generate)

    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", _needs_retrieval_edge, {"retrieve": "retrieve", "generate": "generate"})
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges("grade", _grade_edge, {"generate": "generate", "rewrite_query": "rewrite_query"})
    graph.add_edge("rewrite_query", "retrieve")
    graph.add_edge("generate", END)

    return graph.compile(checkpointer=_checkpointer)


def _get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


@_observe(name="agent_run")
def run(question: str, session_id: str | None = None, user_id: str | None = None) -> str:
    context = (
        propagate_attributes(session_id=session_id, user_id=user_id)
        if _LANGFUSE_ENABLED
        else contextlib.nullcontext()
    )
    with context:
        app = _get_graph()
        thread_id = session_id or "default"
        result = app.invoke(
            {"question": question},
            config={"configurable": {"thread_id": thread_id}},
        )
        if _LANGFUSE_ENABLED:
            get_client().set_current_trace_io(input=question, output=result["answer"])
    return result["answer"]


def main() -> None:
    question = sys.argv[1]
    print(run(question))
    if _LANGFUSE_ENABLED:
        get_client().flush()


if __name__ == "__main__":
    main()
