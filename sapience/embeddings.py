"""Embedding provider abstraction.

EMBEDDINGS_PROVIDER selects the backend (read per call, so tests and long
processes can switch without reimport):

- "openai" (default): OpenAI API, model from EMBEDDINGS_MODEL
  (default text-embedding-3-small, 1536-dim). Requires OPENAI_API_KEY.
- "local": chromadb's bundled ONNX MiniLM-L6-v2 (384-dim). No API key, no
  network after the first model download, fully local memory content.

Embedding dimensions differ between providers, so an existing database can't
mix them — switch providers with a stopped server and re-embed everything:

    python -m sapience.repair --rebuild --re-embed --server-stopped
"""

import os

from openai import OpenAI

_client = None
_local_ef = None


def provider() -> str:
    return os.environ.get("EMBEDDINGS_PROVIDER", "openai").strip().lower()


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable not set")
        _client = OpenAI(api_key=api_key)
    return _client


def _openai_model() -> str:
    return os.environ.get("EMBEDDINGS_MODEL", "text-embedding-3-small")


def _get_local_ef():
    global _local_ef
    if _local_ef is None:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        _local_ef = ONNXMiniLM_L6_V2()
    return _local_ef


def embed(text: str) -> list[float]:
    if provider() == "local":
        return [float(x) for x in _get_local_ef()([text.strip()])[0]]
    response = _get_client().embeddings.create(
        model=_openai_model(),
        input=text.strip()
    )
    return response.data[0].embedding


def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if provider() == "local":
        return [[float(x) for x in e] for e in _get_local_ef()([t.strip() for t in texts])]
    response = _get_client().embeddings.create(
        model=_openai_model(),
        input=[t.strip() for t in texts]
    )
    return [item.embedding for item in response.data]
