import hashlib
import re
import time
from typing import Callable, TypeVar

import cohere
from cohere.errors.too_many_requests_error import TooManyRequestsError
from qdrant_client import models

from src import config

BATCH_SIZE = 96
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_DELAY_SECONDS = 6.0

TOKEN_RE = re.compile(r"[a-z0-9]+")
SPARSE_VOCAB_SIZE = 2**24

_client: cohere.ClientV2 | None = None

T = TypeVar("T")


def _get_client() -> cohere.ClientV2:
    global _client
    if _client is None:
        _client = cohere.ClientV2()
    return _client


def with_cohere_retry(fn: Callable[[], T]) -> T:
    """Cohere trial keys are capped at 10 calls/minute. Retry with backoff
    instead of letting a burst of calls (e.g. answering several Slack
    questions back to back) crash outright."""
    for attempt in range(RATE_LIMIT_MAX_RETRIES):
        try:
            return fn()
        except TooManyRequestsError:
            if attempt == RATE_LIMIT_MAX_RETRIES - 1:
                raise
            time.sleep(RATE_LIMIT_BASE_DELAY_SECONDS * (attempt + 1))
    raise AssertionError("unreachable")


def embed_documents(texts: list[str]) -> list[list[float]]:
    client = _get_client()
    embeddings: list[list[float]] = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        response = with_cohere_retry(
            lambda batch=batch: client.embed(
                model=config.EMBED_MODEL,
                input_type="search_document",
                embedding_types=["float"],
                texts=batch,
            )
        )
        embeddings.extend(response.embeddings.float)
    return embeddings


def embed_query(text: str) -> list[float]:
    client = _get_client()
    response = with_cohere_retry(
        lambda: client.embed(
            model=config.EMBED_MODEL,
            input_type="search_query",
            embedding_types=["float"],
            texts=[text],
        )
    )
    return response.embeddings.float[0]


def _tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _token_index(token: str) -> int:
    digest = hashlib.md5(token.encode()).hexdigest()
    return int(digest, 16) % SPARSE_VOCAB_SIZE


def bm25_sparse_vector(text: str) -> models.SparseVector:
    """Raw term-frequency sparse vector. Qdrant applies IDF weighting
    server-side (Modifier.IDF) from the indexed collection's term stats."""
    counts: dict[int, float] = {}
    for token in _tokenize(text):
        idx = _token_index(token)
        counts[idx] = counts.get(idx, 0) + 1
    return models.SparseVector(indices=list(counts.keys()), values=list(counts.values()))
