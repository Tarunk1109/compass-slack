# Compass

**A Slack copilot that answers developer questions about your microservices, grounded strictly in their OpenAPI specs and docs.**

Ask it "how do I create a payment", "what breaks if I change the fraud pre-auth contract", or "is transfers safe to build against yet", and get a cited answer pulled from the actual specs instead of tribal knowledge.

```
You:      What breaks if I change the fraud pre-auth contract?

Compass:  Two services call fraud's pre-auth endpoint:

          - payments.createPayment calls it synchronously before writing
            to the ledger [payments / openapi.yaml / createPayment]
          - fraud is configured fail-closed, so a contract break rejects
            transactions rather than letting them through
            [fraud / README.md]

          fraud itself is not_started on migration, and payments depends
          on it, so a change here blocks the payments migration too
          [fraud / openapi.yaml]
```

---

## Table of contents

- [Why this exists](#why-this-exists)
- [What makes it different from a generic doc bot](#what-makes-it-different-from-a-generic-doc-bot)
- [Architecture](#architecture)
- [The chunking model](#the-chunking-model)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Usage](#usage)
- [Evaluation](#evaluation)
- [Project layout](#project-layout)
- [The demo corpus](#the-demo-corpus)
- [Observability](#observability)
- [Design decisions](#design-decisions)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)

---

## Why this exists

Engineers joining a large service estate lose days to questions that are technically answered somewhere: which endpoint do I call, what does this field mean, who breaks if I change this response shape, is that service even done migrating yet. The answers live in OpenAPI specs, READMEs, and ADRs that nobody reads end to end.

Compass makes that corpus conversational. It sits in Slack, retrieves from the real specs, and refuses to answer when the docs do not cover something rather than inventing an endpoint that does not exist.

It is currently a proof of concept running against a synthetic corpus of 12 fake payments-domain services. The retrieval, agent, evaluation, and deployment layers are real and working; the data is fake so it can be demoed publicly.

## What makes it different from a generic doc bot

Three things a naive "embed the docs, stuff them in a prompt" build gets wrong:

**1. Chunking by token count destroys API specs.** An endpoint split across two chunks means the parameters land in one and the responses in another, and neither retrieves well. Compass makes one chunk per API operation, always whole.

**2. Embedding the full spec text buries the signal.** A flattened endpoint with every schema inlined is mostly boilerplate, and the embedding drowns. Compass embeds a short natural-language summary of each operation and stores the full text as payload, so search is precise and the answer model still sees complete detail.

**3. Dependency questions are not retrieval questions.** "What breaks if I change X" needs a graph traversal, not a similarity search. Compass detects that intent, walks the dependency edges from spec metadata, and injects the result as structured context alongside the retrieved chunks.

## Architecture

```
                        Slack (Socket Mode)
                                |
                          src/slack/app.py
                       Assistant panel, streaming,
                        feedback buttons, DM fallback
                                |
                                v
        +-------------------------------------------------+
        |            LangGraph agent (graph.py)           |
        |                                                 |
        |   route ---> retrieve ---> grade ---> generate  |
        |  (Haiku)                  (Haiku)     (Sonnet)  |
        |     |                        |                  |
        |     |                        v                  |
        |     |                  rewrite_query            |
        |     |                    (max 1 retry)          |
        |     |                                           |
        |     +--> chitchat path (no retrieval needed)    |
        +-------------------------------------------------+
                                |
                        src/mcp/server.py
                      five FastMCP tools:
              search_docs, get_service_info, get_endpoint,
          get_migration_status, list_service_dependencies
                                |
                       src/retrieval/search.py
              dense + BM25 sparse, RRF fusion, Cohere rerank
                                |
                                v
                          Qdrant (service_docs)
                                ^
                                |
        +-------------------------------------------------+
        |          Ingestion pipeline (src/ingest/)       |
        |                                                 |
        |  parse_openapi --> chunk --> embed --> load     |
        |  (prance, $refs   (1 per    (Cohere   (Qdrant   |
        |   resolved)        op)       v4.0)     upsert)  |
        |         ^                                       |
        |    parse_prose (READMEs, ADRs, runbooks)        |
        +-------------------------------------------------+
                                ^
                                |
                    data/services/*/openapi.yaml + *.md
```

### Request flow, step by step

1. **Slack receives a DM.** The Bolt `Assistant` handler picks it up, sets a thinking status, and hands the text to the agent with the channel id as both the conversation thread key and the Langfuse session id.

2. **Route** (Haiku 4.5) classifies the question in one call: does it need retrieval at all, which service is it about, which doc type, which migration status, what is the intent (`dependency_impact`, `migration_status`, or `general`), and critically a `standalone_query` that resolves pronouns from conversation history so "what about its dependencies" becomes a searchable query on its own.

3. **Retrieve** runs hybrid search: the query is embedded with Cohere and simultaneously tokenized into a BM25 sparse vector, both prefetch 25 candidates from Qdrant, results fuse with Reciprocal Rank Fusion, then Cohere reranks down to the top 8. Dense retrieval alone misses bare error codes and exact operationIds; sparse alone misses paraphrased intent. For `dependency_impact` and `migration_status` intents, the graph also walks the dependency edges and injects structured metadata.

4. **Grade** (Haiku 4.5) checks whether the retrieved chunks can actually answer the question. If not, the query is rewritten once and retrieval reruns. Capped at one retry so a bad question cannot spiral.

5. **Generate** (Sonnet 4.5) answers strictly from the retrieved chunks with inline `[service / source_file / operationId]` citations, or says plainly that it could not find the answer.

6. **Stream back to Slack**, appending in 12-word slices with a 300ms throttle, then attach thumbs up/down buttons.

Every step is traced to Langfuse with token counts, latency, and cost.

## The chunking model

This is the single most important design decision in the project.

**One chunk per API operation.** Each HTTP method on each path becomes exactly one chunk, never split, never merged. The chunk text is the fully flattened operation: method and path, summary, description, every parameter with location and type and required flag, the request body schema with all `$ref`s resolved inline, every response code with its description and schema, auth requirements, tags, and dependencies.

**Embed a summary, store the full detail.** For each operation Compass builds a one-line natural-language summary (deterministically from the spec fields where possible, falling back to a Haiku call only when both summary and description are missing, cached to disk so reruns are free). That summary is what gets embedded. The full flattened text is what gets handed to the answer model. This mirrors the discovery pattern from the OpenAPI-RAG literature: short text for finding, full text for answering.

**Four other chunk types** round out the corpus: one `overview` per service from the spec `info` block plus the README's first section, one `schema` chunk for any schema shared across three or more endpoints, and `prose` chunks split on markdown headings with the heading path prepended so each fragment keeps its context (typed as `readme`, `runbook`, or `adr` by filename).

**Every chunk carries metadata** used for both filtering and citation:

| Field | Type | Purpose |
|---|---|---|
| `service_name` | str | Filter and cite |
| `doc_type` | str | `endpoint`, `schema`, `overview`, `readme`, `runbook`, `adr` |
| `operation_id` | str or null | Cite, exact lookup |
| `http_method` | str or null | Exact lookup |
| `path` | str or null | Exact lookup |
| `tags` | list[str] | Grouping |
| `auth_type` | str or null | Answer auth questions |
| `migration_status` | str | `not_started`, `in_progress`, `migrated` |
| `version` | str | Answer version questions |
| `depends_on` | list[str] | Powers the dependency graph |
| `source_file` | str | Cite |
| `chunk_text` | str | The full text handed to the generator |

Service metadata is read from OpenAPI extensions (`x-service-name`, `x-migration-status`, `x-team-owner`, `x-depends-on`) with fallbacks at every level, because half the demo corpus deliberately omits them.

## Quick start

### Prerequisites

- Python 3.14
- Docker (for Qdrant)
- API keys: [Cohere](https://dashboard.cohere.com/api-keys), [Anthropic](https://console.anthropic.com/), optionally [Langfuse](https://cloud.langfuse.com)
- A Slack app with Socket Mode enabled

### 1. Install

```bash
git clone https://github.com/Tarunk1109/compass-slack.git
cd compass-slack
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

Create a `.env` in the project root:

```bash
COHERE_API_KEY=...
ANTHROPIC_API_KEY=...

SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# Optional, enables tracing. Omitted keys silently disable Langfuse.
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
```

`.env` is gitignored. Never commit it.

### 3. Start Qdrant

```bash
docker compose up -d qdrant
curl http://localhost:6333/collections
```

### 4. Ingest the corpus

```bash
python -m src.ingest.run_ingest
```

Prints services processed, chunk counts by doc type, and total points. Expect roughly 175 chunks from 62 endpoints across 12 services. Verify:

```bash
curl http://localhost:6333/collections/service_docs | python -m json.tool
```

### 5. Ask it something

Straight from the CLI, no Slack needed:

```bash
python -m src.agent.graph "how do I create a payment and what makes it fail"
```

### 6. Run the Slack app

```bash
python -m src.slack.app
```

Or run everything containerized:

```bash
docker compose up --build
```

### Slack app setup

Create an app at [api.slack.com/apps](https://api.slack.com/apps), then:

- **Socket Mode**: enable it, generate an app-level token with `connections:write`, that is your `SLACK_APP_TOKEN`
- **Bot token scopes**: `assistant:write`, `chat:write`, `im:history`, `im:read`, `im:write`
- **Event subscriptions**: `assistant_thread_started`, `message.im`
- **Agents & AI Apps**: enable the assistant feature
- Install to your workspace, copy the bot token into `SLACK_BOT_TOKEN`

## Configuration

All model and infrastructure constants live in `src/config.py`:

| Constant | Default | Notes |
|---|---|---|
| `COLLECTION_NAME` | `service_docs` | Qdrant collection |
| `EMBED_MODEL` | `embed-v4.0` | Cohere |
| `EMBED_DIM` | `1536` | Must match the embed model |
| `RERANK_MODEL` | `rerank-v3.5` | Cohere |
| `GEN_MODEL` | `claude-sonnet-4-5` | Final answer generation |
| `ROUTER_MODEL` | `claude-haiku-4-5` | Routing, grading, rewriting, summaries |
| `QDRANT_HOST` | `localhost` | Overridden to `qdrant` in Compose |
| `QDRANT_PORT` | `6333` | |

## Usage

Every module has a CLI entrypoint for ad hoc inspection:

```bash
# Parse one service and print endpoint records
python -m src.ingest.parse_openapi data/services/payments

# Show chunk counts and a sample chunk's embed_text vs chunk_text
python -m src.ingest.chunk data/services/payments

# Full ingestion
python -m src.ingest.run_ingest

# Raw retrieval, no agent, no generation
python -m src.retrieval.search "409 duplicate idempotency"

# Full agent run
python -m src.agent.graph "is transfers safe to build against yet"

# MCP server over Streamable HTTP
python -m src.mcp.server

# Slack app
python -m src.slack.app
```

### MCP tools

The retrieval layer is exposed as five FastMCP tools, usable by any MCP client:

| Tool | Returns |
|---|---|
| `search_docs(query, service?, doc_type?, migration_status?)` | Reranked, cited snippets |
| `get_service_info(service_name)` | Overview, version, status, owner, dependencies |
| `get_endpoint(service, method, path)` | Full flattened endpoint text, fetched on demand |
| `get_migration_status(service_name)` | Status plus documented migration notes |
| `list_service_dependencies(service_name)` | Forward and reverse dependency edges |

## Evaluation

`eval/golden.jsonl` holds 50 questions across 10 categories: endpoint lookups, dependency impact, migration status, planted README gotchas, messy spec robustness, shared schemas, multi-turn follow-ups, chitchat, adversarial and refusal cases, and ambiguous questions flagged for manual review.

```bash
python -m eval.run_eval              # full run
python -m eval.run_eval --limit 5    # smoke test
python -m eval.run_eval --ids s003,m001
```

The harness runs every question through the real system (real Qdrant, real Cohere, real Claude), checks citations, keyword expectations, and refusal behavior, and computes Hit Rate and MRR from the agent's own retrieval results pulled out of graph state rather than a separate retrieval call.

**Latest full run: 43/48 auto-scored passed, 97.6% Hit Rate, 0.938 MRR.**

Three of the five failures were the eval's own assertions being too strict, for example expecting the literal string `in_progress` where the model correctly said "mid-migration". Two were real bugs, both since fixed:

- The router skipped retrieval for questions phrased as general advice-seeking ("should I use floats for money amounts"), so Claude answered from training knowledge and contradicted the actual `Money` schema. Fixed by broadening the router rule and adding a safety net to the chitchat prompt.
- A follow-up answer was correct and grounded but dropped the citation format in casual conversational tone. Prompt reinforcement improved this but did not fully close it. See [Known limitations](#known-limitations).

## Project layout

```
src/
  config.py              Model ids, collection name, dimensions
  ingest/
    parse_openapi.py     prance $ref resolution, spec walk to EndpointRecords
    parse_prose.py       Markdown heading splitter for READMEs, ADRs, runbooks
    chunk.py             Chunk construction, flattening, summarization + cache
    embed.py             Cohere embed wrapper, BM25 sparse vectors, retry logic
    load_qdrant.py       Collection schema, payload indexes, upsert
    run_ingest.py        Orchestrates the full pipeline
  retrieval/
    search.py            Hybrid dense + sparse, RRF fusion, Cohere rerank
  mcp/
    server.py            FastMCP server, five tools
  agent/
    graph.py             LangGraph: route, retrieve, grade, rewrite, generate
    prompts.py           System prompts for every node
  slack/
    app.py               Bolt Assistant, streaming, feedback, DM fallback
eval/
  golden.jsonl           50 gold questions across 10 categories
  run_eval.py            Hit Rate, MRR, assertion harness
data/services/           The synthetic corpus, 12 services
BUILD.md                 The original phased build spec
ROADMAP.md               What is next, with known gaps documented
```

## The demo corpus

12 synthetic payments-domain services, 62 endpoints, 14 prose documents. Split deliberately:

**Clean and well documented**, modeling a healthy service: `payments`, `auth`, `ledger`, `accounts`, `notifications`, `cards`.

**Deliberately messy**, mimicking auto-generated springdoc output: `fraud`, `kyc`, `transfers`, `fx`, `statements`, `disputes`. These have operationIds like `getUsingGET` and `cancelUsingPOST_1`, endpoints with no description at all, generic `default` error responses instead of explicit codes, deeper `$ref` nesting, and missing `x-` extensions so the parser has to fall back. This is the point: the chunker is tested against realistic ugly specs, not just the pretty ones.

**Planted cross-service facts** the demo depends on:

- `payments.createPayment` depends on `fraud` and `ledger`
- `payments.refundPayment` depends on `ledger` and `notifications`
- `transfers` depends on `ledger` and `fx`, and is `in_progress`
- `ledger` is `migrated`, the one finished dependency everything relies on
- `accounts.openAccount` depends on `kyc`

**Planted gotchas that exist only in prose, not in the spec**, so retrieval has to reach past the OpenAPI file to answer correctly: the payments idempotency 409 trap, fraud's fail-closed behavior, and the transfers FX rate lock timing.

## Observability

When Langfuse keys are present, every agent run is traced: each node as its own span, per-call token counts, latency, and cost, with `session_id` set to the Slack channel and `user_id` to the Slack user. Without keys, tracing degrades to a no-op decorator so CLI runs stay quiet and dependency-free.

This is deliberately part of the pitch, not an afterthought. An auditable trace of exactly which chunks produced which answer is the first thing a regulated engineering org asks for.

## Design decisions

| Decision | Reason |
|---|---|
| One chunk per API operation, never split by token count | An endpoint split across chunks retrieves badly and answers worse |
| Embed a summary, store the full text | Keeps embeddings focused; mirrors the OpenAPI-RAG discovery pattern |
| Hybrid dense + BM25 rather than dense alone | Bare error codes and exact operationIds are keyword queries, not semantic ones |
| Cohere as the default embedder | Chosen for alignment with the target org's stack, independent of benchmark results |
| No multi-provider embedder abstraction | Deliberately deferred; abstraction before a second implementation is premature |
| LLM summaries only when the spec has neither summary nor description, cached to disk | Holds ingestion cost and runtime down |
| MCP tools imported in-process rather than called over the protocol | Sanctioned shortcut for the first working version; the tool boundary is already clean enough to swap |
| Retry with a rewritten query capped at one attempt | Bounded latency; a second rewrite rarely helped in practice |
| Dependency questions get graph traversal, not just similarity search | "What breaks if I change X" is a reverse-edge lookup that embeddings answer unreliably |
| Collection auto-deleted and rebuilt when it predates the hybrid schema | Avoids silent dimension and vector-name mismatches after the Phase 6 upgrade |
| Half the demo corpus deliberately malformed | Forces the parser's fallback paths to be real, not theoretical |

## Known limitations

Honest list. Details and proposed fixes in [ROADMAP.md](ROADMAP.md).

- **Citation format is not guaranteed.** The generator occasionally drops the `[service / file / operation]` format on casual follow-ups. Prompting alone cannot fully fix this; a post-processing verify-and-repair pass is the reliable structural fix.
- **Conversation memory is in-process.** `MemorySaver` means history resets on restart. A SQLite or Postgres checkpointer is the natural next step.
- **Cohere trial keys cap at 10 calls per minute.** Retry with backoff handles bursts gracefully, but real multi-user usage needs a paid tier.
- **No access control.** Any Slack user can ask about any service, including migration status and fraud rules. Real rollout needs role-based filtering on what is retrievable.
- **Ingestion is full-rebuild only.** Every run re-embeds everything, and deleted endpoints leave orphaned points behind. Fine at 175 chunks, wasteful and wrong at real scale.
- **Reverse dependency lookup is an O(n) scan.** `list_service_dependencies` scrolls every endpoint chunk. Needs a payload index or a precomputed graph.
- **No unit tests, no CI.** Everything has been verified by CLI runs and the eval harness. The parsing and chunking logic is the most likely thing to break silently.
- **Slack native streaming is faked.** Real token streaming and suggested prompts did not fire reliably on the sandbox workspace used here, so answers are generated fully then appended in slices, with a plain-DM handler as a fallback.
- **DM only.** Channel support is not implemented.
- **Third-party data flow.** Every question and retrieved chunk goes to Cohere and Anthropic. Fine for synthetic data; the first compliance question for real internal specs.

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full list. Highest-value next steps:

1. **Persistent checkpointer** so conversation memory survives restarts
2. **LLM-as-judge groundedness scoring** to replace hand-tuned keyword assertions in the eval
3. **Citation repair pass** to close the format-compliance gap structurally
4. **Incremental ingestion** with change detection and stale-point pruning
5. **Unit tests plus CI** on the parsing and chunking layer, with spec validation on every change
6. **Embedder bake-off** (Cohere vs Voyage vs OpenAI) quantified against the existing gold set

## Further reading

- [BUILD.md](BUILD.md) is the original phased build specification, useful for understanding why the system is structured the way it is
- [ROADMAP.md](ROADMAP.md) documents what is done, what is not, and the reasoning behind each gap
