# BUILD.md — Slack RAG Copilot

This is the build spec for a Slack-based RAG copilot that answers developer questions about microservices from their OpenAPI/Swagger specs and prose docs. Build it in the order below. Get each phase working and verified before moving to the next. Do not skip ahead.

## Ground rules for whoever builds this
- Ship the simplest thing that works on Cohere first. A model bake-off (Cohere vs Voyage vs OpenAI) is explicitly OUT OF SCOPE for now and lives in Phase 9 as optional. Do not build a multi-provider abstraction layer before Phase 9. One embedder: Cohere.
- Verify each phase with the stated check before continuing. If a check fails, stop and fix it before moving on.
- Keep secrets in `.env`, never in code. `.env` is already gitignored.
- No em dashes in generated docs or comments. Keep code comments short.
- Prefer clarity over cleverness. This is a POC meant to be read by a bank engineering team.

## Current state (already done, do not redo)
- Repo initialized at project root with a Python venv active.
- Qdrant running locally in Docker on `localhost:6333`, currently empty.
- `.env` contains working `COHERE_API_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`. (Voyage is for Phase 9 only, ignore it until then.)
- Installed packages: `slack-bolt langgraph fastmcp cohere qdrant-client anthropic langfuse prance openapi-spec-validator voyageai python-dotenv pyyaml`.
- Verified all API keys work (Cohere embed-v4.0 returns 1536 dims, Anthropic Haiku responds, Qdrant reachable).
- One fake service already exists and is validated: `data/services/payments/` containing `openapi.yaml` and `README.md`. Use it as the reference format for all other services.

## Target project structure
```
slack-rag-copilot/
  data/
    services/                 # fake microservice corpus (input data, read-only at runtime)
      payments/
        openapi.yaml
        README.md
      auth/ ...
  src/
    config.py                 # loads env, constants (model names, collection name, dims)
    ingest/
      parse_openapi.py        # resolve $refs, walk spec into endpoint records
      parse_prose.py          # chunk README/runbook/ADR markdown
      chunk.py                # build chunk objects + metadata from parsed records
      embed.py                # Cohere embed-v4 wrapper (document + query)
      load_qdrant.py          # create collection, upsert chunks
      run_ingest.py           # orchestrates the whole ingestion, CLI entrypoint
    retrieval/
      search.py               # hybrid search (dense + BM25) + Cohere rerank
    mcp/
      server.py               # FastMCP server exposing retrieval + lookup tools
    agent/
      graph.py                # LangGraph: route -> retrieve -> grade -> generate
      prompts.py              # system prompts for router + generator
    slack/
      app.py                  # Bolt app, Assistant panel, streaming responses
  eval/
    golden.jsonl              # gold questions -> expected source chunks (Phase 9)
    run_eval.py               # hit rate / MRR harness (Phase 9)
  scripts/
    make_services.py          # optional helper to scaffold new service folders
  .env                        # secrets (gitignored)
  BUILD.md                    # this file
```

## Chunking model (read this before writing any ingestion code)
This is the single most important design decision. Follow it exactly.

**One chunk per API operation.** Each endpoint (one HTTP method on one path) becomes exactly one chunk. Do NOT chunk by token count. Do NOT put multiple endpoints in one chunk.

For each operation, build the chunk text by flattening: HTTP method + path, summary, description, all parameters (name, location, type, required, description), request body schema (fully resolved, no `$ref` left dangling), all response codes with their descriptions and schemas, auth requirement, tags. Resolve all `$ref`s first so schemas are inline and readable.

**Embed a short summary, store the full detail.** For each operation, generate a one-line natural-language summary (method + path + what it does + key dependencies) and embed THAT. Store the full flattened operation text as the chunk payload that gets handed to the answer model. This keeps embeddings focused and retrieval precise. This mirrors the "discovery" pattern from the OpenAPI-RAG literature: summaries for finding, full spec for answering.

**Separate chunk types.** Besides endpoint chunks, create:
- one `overview` chunk per service (from spec `info` block + first section of README)
- one `schema` chunk for any large shared schema referenced by 3 or more endpoints
- `prose` chunks from README/runbook/ADR: split on markdown headings, target 400-800 tokens, prepend the heading path to each chunk so it keeps context.

**Metadata (Qdrant payload) on every chunk:**
```
service_name      str        e.g. "payments"
doc_type          str        endpoint | schema | overview | readme | runbook | adr
operation_id      str|null   e.g. "createPayment" (endpoint chunks only)
http_method       str|null   GET/POST/... (endpoint chunks only)
path              str|null   e.g. "/v2/payments" (endpoint chunks only)
tags              list[str]
auth_type         str|null
migration_status  str        not_started | in_progress | migrated
version           str        service version from spec info
depends_on        list[str]  services this chunk/operation depends on
source_file       str        relative path to the source file
chunk_text        str        the full text handed to the generator
```
Read `x-service-name`, `x-migration-status`, `x-team-owner`, `x-depends-on`, and per-operation `x-depends-on` from the spec extensions (the sample payments spec uses these). Fall back to sensible defaults if missing, because later fake specs will deliberately be messy (missing descriptions, ugly operationIds, no extensions) to mimic auto-generated springdoc output.

## Phase 0 — Config and skeleton
Create `src/config.py`:
- load `.env` via python-dotenv
- constants: `COLLECTION_NAME = "service_docs"`, `EMBED_MODEL = "embed-v4.0"`, `EMBED_DIM = 1536`, `RERANK_MODEL = "rerank-v3.5"`, `GEN_MODEL = "claude-sonnet-4-5"`, `ROUTER_MODEL = "claude-haiku-4-5"`, `QDRANT_HOST = "localhost"`, `QDRANT_PORT = 6333`.
- Note: confirm the exact current Cohere rerank model id and Claude model ids at build time; these are the intended ones but versions move. If a model id 404s, list available models and pick the current equivalent.

Create empty package folders with `__init__.py` under `src/` and each subpackage.

**Check:** `python -c "import src.config"` runs with no error.

## Phase 1 — Generate the rest of the fake corpus
You have `payments`. Create these additional services, each with `openapi.yaml` (OpenAPI 3.1) and `README.md`, in `data/services/<name>/`:

Clean, well-documented (like payments): `auth`, `ledger`, `accounts`, `notifications`, `cards`.
Deliberately messy (mimic springdoc auto-generation): `fraud`, `kyc`, `transfers`, `fx`, `statements`, `disputes`.

Messy means: some operationIds like `getUsingGET` or `createPayment_1`, several endpoints with NO description, generic `default` error responses instead of explicit codes, deeper `$ref` nesting, missing `x-` extensions so the parser must fall back. This is intentional so the chunker is tested against realistic ugly specs.

Plant these cross-service facts (the demo depends on them):
- `payments.createPayment` depends on `fraud` and `ledger`; `payments.refundPayment` depends on `ledger` and `notifications`. (Already in the payments spec.)
- `transfers` depends on `ledger` and `fx`. `transfers` migration_status = in_progress.
- `ledger` migration_status = migrated (it is the one already-done dependency everything relies on).
- `accounts.openAccount` depends on `kyc`.
- Put one non-obvious gotcha in a README that is NOT in the spec (like the payments idempotency 409 trap), for at least `fraud` (fail-closed behavior) and `transfers` (fx rate lock timing).

Keep amounts using a shared `Money` schema (amount + ISO-4217 currency) across services for consistency. Add 1-2 ADRs total (e.g., `ledger/adr-001-double-entry.md`) so the `adr` doc_type has data.

**Check:** every spec validates. Run:
```
python -c "import glob,yaml;from openapi_spec_validator import validate;[ (validate(yaml.safe_load(open(f))),print('ok',f)) for f in glob.glob('data/services/*/openapi.yaml')]"
```
All lines print `ok`.

## Phase 2 — OpenAPI parsing
`src/ingest/parse_openapi.py`:
- Use `prance.ResolvingParser(spec_path, strict=False)` to load and resolve `$ref`s. `strict=False` so messy specs still parse.
- Walk `paths` -> methods. For each operation, emit an `EndpointRecord` dataclass with: service_name, operation_id (fall back to `f"{method}_{path}"` if missing), http_method, path, summary, description, params (list of dicts), request_body_schema (resolved dict or None), responses (dict of code -> {description, schema}), auth_type, tags, depends_on (merge spec-level and operation-level `x-depends-on`), version, migration_status.
- Read service-level extensions with `.get(...)` and defaults so missing `x-` fields never crash.
- Provide `parse_service(service_dir) -> ServiceParse` returning the endpoint records plus the raw `info` block and detected shared schemas (schemas referenced by 3 or more operations).

**Check:** `python -m src.ingest.parse_openapi data/services/payments` prints the count of endpoints (should be 6 for payments) and one sample record as readable text.

## Phase 3 — Chunk building
`src/ingest/chunk.py`:
- `flatten_endpoint(record) -> str`: render the full human-readable endpoint text described in the chunking model above.
- `summarize_endpoint(record) -> str`: a one-line summary. Build it deterministically from fields first (method, path, summary, depends_on). Only call the LLM (Haiku) to generate a summary when `summary` and `description` are both missing (messy specs), to keep cost and time low. Cache generated summaries to a local json so re-runs are free.
- `build_chunks(service_parse) -> list[Chunk]`: produce endpoint chunks, the overview chunk, schema chunks, and hand prose files to `parse_prose`.
`src/ingest/parse_prose.py`:
- Split markdown on headings, target 400-800 tokens, prepend heading path, attach doc_type from filename (`README.md`->readme, `runbook*`->runbook, `adr*`->adr).
Each `Chunk` has: `id` (stable hash of service+doc_type+operation_id/heading), `embed_text` (summary for endpoints, first line+heading for prose), `chunk_text` (full text), and the full metadata payload.

**Check:** `python -m src.ingest.chunk data/services/payments` prints total chunk count and, for one endpoint chunk, both its `embed_text` (short) and `chunk_text` (full).

## Phase 4 — Embedding + Qdrant load
`src/ingest/embed.py`:
- `embed_documents(texts) -> list[list[float]]` using Cohere `co.embed(model="embed-v4.0", input_type="search_document", embedding_types=["float"])`. Batch in groups of 96. Return `.embeddings.float`.
- `embed_query(text) -> list[float]` same but `input_type="search_query"`.
`src/ingest/load_qdrant.py`:
- Create collection `service_docs` if absent: dense vector size 1536, distance Cosine. Also configure a sparse vector named `bm25` for hybrid (use Qdrant native BM25 / IDF; if using fastembed sparse, wire it here). If sparse setup adds friction, ship dense-only for now and leave a clearly marked TODO for BM25 in Phase 6, do not block.
- Create payload indexes on `service_name`, `doc_type`, `migration_status`, `version`.
- Upsert points: id = chunk.id hashed to uuid, vector = dense embedding (+ sparse if enabled), payload = full metadata including `chunk_text`.
`src/ingest/run_ingest.py`:
- Iterate `data/services/*`, parse, chunk, embed, upsert. Print a summary: services processed, chunks by doc_type, total points.

**Check:** `python -m src.ingest.run_ingest` completes and prints totals. Then:
```
curl http://localhost:6333/collections/service_docs | python -m json.tool
```
shows `points_count` greater than 0 matching the printed total.

## Phase 5 — Retrieval
`src/retrieval/search.py`:
- `search(query, filters=None, top_k=8) -> list[dict]`:
  1. embed the query (Cohere search_query).
  2. Qdrant search over dense (and sparse via RRF if enabled), prefetch ~25 candidates, applying any payload `filters` (e.g. `{"service_name": "payments"}` or `{"migration_status": "in_progress"}`).
  3. Rerank candidates with Cohere `co.rerank(model="rerank-v3.5", query=query, documents=[c["chunk_text"] ...], top_n=top_k)`.
  4. return reranked list of {score, payload} including chunk_text, service_name, source_file, operation_id.
- Add a `__main__` so you can run ad hoc queries from the CLI.

**Check:** `python -m src.retrieval.search "how do I create a payment and what makes it fail"` returns the `createPayment` endpoint chunk as the top hit, and a query about idempotency surfaces the README gotcha chunk.

## Phase 6 — Hybrid search hardening (only if you shipped dense-only in Phase 4)
Add BM25 sparse vectors so keyword-heavy queries (operationIds, error codes like "409", service names) rank well. Store sparse alongside dense; fuse with Qdrant's RRF in the Query API. Re-run ingest to populate sparse vectors.

**Check:** a query for a bare error code (like "409 duplicate") and a query for an exact operationId both return the right endpoint in the top 3. Compare against dense-only to confirm improvement.

## Phase 7 — MCP tools + LangGraph agent
`src/mcp/server.py` (FastMCP, Streamable HTTP transport): expose tools that wrap Phase 5 retrieval and metadata lookups:
- `search_docs(query, service=None, doc_type=None, migration_status=None)` -> reranked cited snippets.
- `get_service_info(service_name)` -> overview chunk + version + migration_status + owner + depends_on.
- `get_endpoint(service, method, path)` -> full flattened endpoint text (fetch-on-demand).
- `get_migration_status(service_name)` -> status + notes.
- `list_service_dependencies(service_name)` -> depends_on edges (powers the "what breaks if I change X" demo moment).

`src/agent/graph.py` (LangGraph):
- Node `route` (Haiku): classify the question, decide if retrieval is needed, extract filter hints (service, doc_type, migration_status).
- Node `retrieve`: call `search_docs` (via the MCP tools using `langchain-mcp-adapters`, or call `src.retrieval.search` directly if wiring MCP into the graph adds friction for the first working version, then swap to MCP). Apply routed filters.
- Node `grade` (Haiku): are the top chunks relevant enough to answer? If not, rewrite query once and re-retrieve. Cap at 1 retry.
- Node `generate` (Sonnet): answer strictly from retrieved chunks, with inline citations `[service / source_file / operationId]`. If nothing relevant retrieved, say so plainly instead of inventing.
`src/agent/prompts.py`: keep the generator prompt strict about grounding and citations.

**Check:** `python -m src.agent.graph "what breaks if I change the fraud pre-auth contract"` returns an answer naming payments as a dependent, citing the right sources. A migration question ("is transfers safe to build against yet") returns status in_progress plus the ledger-is-migrated context.

## Phase 8 — Slack app
`src/slack/app.py` using Bolt for Python `Assistant`:
- Handle `assistant_thread_started`: set 3-4 suggested prompts (an onboarding question, a dependency question, a migration-status question).
- Handle `user_message`: run the LangGraph agent, stream the answer into the thread using `say_stream` / chat streaming. Throttle appends ~300ms. Fall back to a single `chat.postMessage` if streaming errors.
- Render markdown so code blocks and tables show correctly. Add feedback buttons (thumbs up/down) that log to stdout for now.
- Run in Socket Mode for local dev (needs `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` in `.env`; the human will create the Slack app and tokens and add them).

**Check:** in a Slack workspace, opening the assistant shows suggested prompts, and asking "how do I create a payment" streams a cited answer. Test the three planted demo moments end to end.

## Phase 9 — OPTIONAL, LATER: model bake-off
Only after the above works end to end. Make the embedder swappable behind one interface (`embed_documents`/`embed_query`) with implementations for Cohere, Voyage (`voyage-3-large` and `voyage-code-3`), and OpenAI (`text-embedding-3-large`). Build a gold set in `eval/golden.jsonl` (50-100 dev questions -> expected source chunk ids) and `eval/run_eval.py` computing Hit Rate@k and MRR per model. Run each model over the same corpus and queries, output a comparison table. Do not start this until Phase 8 is demoable. Keep Cohere as the default regardless, for the RBC / North-for-Banking alignment; the bake-off just quantifies the gap.

## Observability (wire in from Phase 7 on)
Add the Langfuse callback handler to the LangGraph run so every route/retrieve/grade/generate step, token count, latency, and cost is traced. A live Langfuse trace is part of the pitch (shows auditability). Self-hostable later for the bank story.

## Definition of done for the first milestone
Phases 0-8 complete: `python -m src.ingest.run_ingest` loads the corpus, and in Slack the assistant answers the three planted demo moments (onboarding lookup, hidden dependency, migration status) with correct citations, streamed. Bake-off (Phase 9) and BM25 hardening (Phase 6, if deferred) can remain as follow-ups.
