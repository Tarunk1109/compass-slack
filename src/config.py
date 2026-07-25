import os

from dotenv import load_dotenv

load_dotenv()

COLLECTION_NAME = "service_docs"
EMBED_MODEL = "embed-v4.0"
EMBED_DIM = 1536
RERANK_MODEL = "rerank-v3.5"
GEN_MODEL = "claude-sonnet-4-5"
ROUTER_MODEL = "claude-haiku-4-5"
QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
