import os
from dotenv import load_dotenv
load_dotenv()

# 1. Cohere
import cohere
co = cohere.ClientV2(os.environ["COHERE_API_KEY"])
r = co.embed(texts=["hello"], model="embed-v4.0",
             input_type="search_document", embedding_types=["float"])
print("Cohere OK, dims:", len(r.embeddings.float[0]))

# 2. Anthropic
import anthropic
msg = anthropic.Anthropic().messages.create(
    model="claude-haiku-4-5", max_tokens=10,
    messages=[{"role": "user", "content": "say ok"}])
print("Anthropic OK:", msg.content[0].text)

# 3. Voyage
import voyageai
r = voyageai.Client().embed(["hello"], model="voyage-3-large")
print("Voyage OK, dims:", len(r.embeddings[0]))

# 4. Qdrant
from qdrant_client import QdrantClient
print("Qdrant OK:", QdrantClient("localhost", port=6333).get_collections())