# Roadmap: what's next beyond deployment

This is a working list of what to tackle after the core POC (Phases 0-8 of
BUILD.md) and local Docker deployment were completed. Not in strict priority
order except where noted; check off or edit as things get done.

## 1. Multi-turn memory (DONE)

Implemented in `src/agent/graph.py`: the compiled graph now uses a
`MemorySaver` checkpointer keyed by `thread_id` (the Slack channel id, same
value already used as Langfuse's `session_id`). Added a `history` field to
`AgentState` (`Annotated[list[dict], operator.add]`) that accumulates one
`{"question", "answer"}` entry per turn via `generate()`. The router now
also returns a `standalone_query` field that resolves pronouns and implicit
references ("its migration status") into a self-contained search query
using the last few turns of history. Verified with a real two-turn test:
asking about fraud's dependencies, then "what is its migration status",
correctly resolved to fraud with no history duplication.

Known limitation: `MemorySaver` is in-process only, so conversation memory
resets if the app restarts. A persistent backend (SQLite or Postgres
checkpointer) would be the natural next step if that matters, e.g. for a
production deployment that restarts periodically.

## 2. A real evaluation harness (DONE)

Built `eval/golden.jsonl` (50 questions across 10 categories: endpoint
lookups, dependency impact, migration status, planted README gotchas, messy
spec robustness, shared schemas, multi-turn follow-ups, chitchat,
adversarial/refusal, and ambiguous questions flagged for manual review) and
`eval/run_eval.py`, which runs every question through the real system (real
Qdrant, Cohere, Claude) and checks citations, keyword expectations, and
refusal behavior per question, plus computes Hit Rate and MRR from the
agent's actual retrieval results.

First full run: 43/48 auto-scored passed, 97.6% Hit Rate, 0.938 MRR. Of the
5 failures, 3 were the eval's own assertions being too strict (e.g.
expecting the literal string "in_progress" when the model correctly said
"mid-migration"), not real bugs.

Two were real findings, since fixed:
- The router was skipping retrieval for questions that read as general
  advice-seeking rather than "about a service" (e.g. "should I use floats
  for money amounts"), causing Claude to answer from general training
  knowledge instead of our actual Money schema, which happened to
  contradict our real docs. Fixed by broadening the router's
  `needs_retrieval` rule and adding a safety net to the chitchat prompt.
- A follow-up question got a factually correct, clearly grounded answer
  that nonetheless dropped the `[service / file / operation]` citation
  format in its casual conversational tone. Prompt reinforcement improved
  but did not fully fix this; noted as a real, known limitation. Prompting
  alone can't 100% guarantee format compliance from a non-deterministic
  model on every stylistic variation. A structural fix (e.g. a
  post-processing pass that verifies and repairs missing citations) would
  be the reliable way to close this gap if it matters for the audit story.

Also surfaced along the way: the Cohere API key in use is a **Trial key,
hard-capped at 10 calls/minute**. Running 50 questions back to back hit this
immediately. Fixed with retry/backoff in `src/ingest/embed.py`
(`with_cohere_retry`, applied to both embed and rerank calls) so bursts
degrade gracefully instead of crashing, but the underlying cap is still real
and would need a paid Cohere tier before any real multi-user Slack usage.

Beyond retrieval metrics, an LLM-as-judge groundedness scorer (rather than
keyword matching) would be a more rigorous fast-follow, since keyword checks
had to be hand-tuned around paraphrasing (see the false failures above).

## 3. Security and data governance

- No access control: any Slack user can currently ask about any service,
  including migration status and fraud rules. A real rollout needs
  role/team-based filtering on what's retrievable per user.
- Third-party data exposure: every question and retrieved chunk goes to
  Cohere and Anthropic's APIs. Fine for synthetic demo data; for real
  internal specs this is the first compliance question (on-prem/VPC models,
  or a signed data processing agreement).
- Prompt injection surface: retrieved content (READMEs, ADRs) becomes part
  of what the LLM reads. Worth a deliberate red-team pass, e.g. planting
  "ignore previous instructions" text in a fake service doc and observing
  behavior.
- Secrets hygiene: `.env` in plaintext is fine for a demo; production needs
  a real secrets manager (Vault, AWS Secrets Manager) and token rotation.
- Audit retention: Langfuse gives a trace-level audit trail already (good
  story), but who can access that dashboard and how long traces are
  retained (they contain full question/answer content) needs a policy.

## 4. Corpus freshness and scale

- `run_ingest.py` does a full re-embed of everything every run. Fine at 175
  chunks, wasteful at real scale (hundreds of services). Needs incremental
  ingestion that only re-embeds changed chunks.
- No deletion handling: removed endpoints leave orphaned points in Qdrant
  forever (upsert-only, nothing prunes stale points).
- `list_service_dependencies` (the reverse dependency lookup, "who depends
  on X") is implemented as a full linear scroll through every endpoint
  chunk. Fine at 62 endpoints, an unindexed O(n) scan at real scale. Needs
  a payload index on `depends_on` or a precomputed dependency graph.
- A real pipeline would trigger re-ingestion from CI when a service's
  actual spec changes, not via someone manually running a script.

## 5. Testing and CI

No unit tests exist anywhere in the codebase; everything has been verified
via ad-hoc CLI runs. Worth adding unit tests for the parsing/chunking logic
specifically (the code most likely to silently break, see: the README
chunking bug that nearly broke the idempotency demo), plus a CI pipeline
that runs them and the OpenAPI spec validation on every change.

## 6. Cost and latency

- The `generate` step took 8.7s in one traced run, noticeable for a chat
  interface. Worth profiling a smaller/faster model for simpler questions,
  or genuine token streaming once the Slack threading issue is resolved.
- No caching: identical repeated questions re-run the full pipeline (embed,
  search, rerank, 3 LLM calls) every time. A semantic cache for common
  questions would cut cost meaningfully at real usage volume.
- Set a cost alert in Langfuse now that spend per trace is visible.

## 7. Product polish

- Persist feedback (thumbs up/down) somewhere real instead of `print()`,
  ideally as a Langfuse score tied to the trace so thumbs-down questions
  can be correlated with the retrieval/generation that produced them.
- Decide if Compass should work in channels, not just DMs (current design
  is DM-only).
- The suggested-prompts/native-streaming gap is specific to the sandbox
  Slack workspace used for this POC; worth testing in a real paid
  workspace to see if it works there without code changes.

## 8. Documentation for the pitch

No top-level README exists yet, only BUILD.md (a build spec, not
user-facing docs). Worth writing: an architecture overview (route, retrieve,
grade, generate, the MCP tools, the Slack integration), a demo script
covering the three planted moments, and a short ops runbook (how to
restart things, where logs live, which dashboards to check) for use during
a live demo.

## Suggested priority if picking two

Multi-turn memory (a real, noticeable gap the moment anyone has an actual
conversation with it) and the eval harness (turns "I think it works" into
"here are the numbers") have the highest payoff relative to effort.
Security and governance matter most for the pitch conversation itself but
are more about what gets said than what gets built right now, given the
corpus is synthetic.
