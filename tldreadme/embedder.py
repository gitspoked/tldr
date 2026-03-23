"""Embed code chunks into Qdrant via LiteLLM."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import os

from .parser import Symbol, ParseResult
from .lazy import load_attr, load_module

COLLECTION = "tldreadme_code"

# Default: talk directly to local Ollama. If LITELLM_URL is set, route through LiteLLM proxy.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LITELLM_URL = os.getenv("LITELLM_URL", "")
EMBED_MODEL = os.getenv("TLDREADME_EMBED_MODEL", "ollama/nomic-embed-text")
CHAT_MODEL = os.getenv("TLDREADME_CHAT_MODEL", "ollama/qwen2.5-coder:3b-instruct")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

def _api_base():
    """Return API base — LiteLLM proxy if configured, otherwise direct Ollama."""
    return LITELLM_URL if LITELLM_URL else OLLAMA_URL


def _litellm():
    """Load litellm only when synthesis or embeddings are needed."""

    return load_module("litellm")


def _qdrant_client_cls():
    """Load QdrantClient lazily."""

    return load_attr("qdrant_client", "QdrantClient")


def _qdrant_models():
    """Load the Qdrant model classes lazily."""

    return {
        "Distance": load_attr("qdrant_client.models", "Distance"),
        "VectorParams": load_attr("qdrant_client.models", "VectorParams"),
        "PointStruct": load_attr("qdrant_client.models", "PointStruct"),
    }


@dataclass
class CodeChunk:
    """A chunk of code ready for embedding."""
    id: str
    file: str
    symbol_name: str
    kind: str
    language: str
    content: str          # the actual code (body)
    signature: str
    context: str          # surrounding info (parent, module, imports)
    line: int
    end_line: int


def chunk_id(file: str, name: str, line: int) -> str:
    """Deterministic ID for a code chunk — same symbol at same location = same ID."""
    raw = f"{file}:{name}:{line}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _chunk_id_to_int(hex_id: str) -> int:
    """Convert hex chunk ID to integer for Qdrant point ID."""
    return int(hex_id, 16)


def symbols_to_chunks(results: list[ParseResult]) -> list[CodeChunk]:
    """Convert parsed results into embeddable chunks."""
    chunks = []
    for pr in results:
        for sym in pr.symbols:
            chunks.append(CodeChunk(
                id=chunk_id(sym.file, sym.name, sym.line),
                file=sym.file,
                symbol_name=sym.name,
                kind=sym.kind,
                language=sym.language,
                content=sym.body,
                signature=sym.signature,
                context=f"file: {sym.file}\nparent: {sym.parent or 'top-level'}\nlang: {sym.language}",
                line=sym.line,
                end_line=sym.end_line,
            ))
    return chunks


def embed_text(text: str) -> list[float]:
    """Get embedding vector for a piece of text."""
    resp = _litellm().embedding(
        model=EMBED_MODEL,
        input=[text],
        api_base=_api_base(),
    )
    return resp.data[0]["embedding"]


def _embed_one_batch(batch: list[str]) -> list[list[float]]:
    """Embed a single batch — used as a unit of work for parallel execution."""
    resp = _litellm().embedding(
        model=EMBED_MODEL,
        input=batch,
        api_base=_api_base(),
    )
    return [d["embedding"] for d in resp.data]


def embed_batch(texts: list[str], batch_size: int = 32, max_workers: int = 4) -> list[list[float]]:
    """Embed texts in parallel batches."""
    batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]

    if len(batches) <= 1:
        # Single batch — no threading overhead
        return _embed_one_batch(batches[0]) if batches else []

    all_embeddings = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for result in executor.map(_embed_one_batch, batches):
            all_embeddings.extend(result)
    return all_embeddings


class CodeEmbedder:
    """Manages embedding storage in Qdrant."""

    def __init__(self, qdrant_url: str = None):
        self.client = _qdrant_client_cls()(url=qdrant_url or QDRANT_URL)
        self._ensure_collection()

    def _ensure_collection(self):
        collections = [c.name for c in self.client.get_collections().collections]
        if COLLECTION not in collections:
            # Dimension depends on model — nomic-embed-text = 768, OpenAI = 1536
            # We'll detect on first embed
            self._collection_created = False
        else:
            self._collection_created = True

    def index_chunks(self, chunks: list[CodeChunk]):
        """Embed and store all chunks."""
        if not chunks:
            return

        # Build the text to embed: signature + context + truncated body
        texts = []
        for c in chunks:
            embed_text = f"{c.signature}\n{c.context}\n{c.content[:2000]}"
            texts.append(embed_text)

        vectors = embed_batch(texts)

        # Create collection on first use (auto-detect dimension)
        if not self._collection_created:
            models = _qdrant_models()
            self.client.create_collection(
                collection_name=COLLECTION,
                vectors_config=models["VectorParams"](size=len(vectors[0]), distance=models["Distance"].COSINE),
            )
            self._collection_created = True

        # Upsert points — use deterministic chunk_id so re-indexing updates
        # in place instead of creating duplicates
        point_struct = _qdrant_models()["PointStruct"]
        points = []
        for chunk, vector in zip(chunks, vectors):
            points.append(point_struct(
                id=_chunk_id_to_int(chunk.id),
                vector=vector,
                payload={
                    "chunk_id": chunk.id,
                    "file": chunk.file,
                    "symbol_name": chunk.symbol_name,
                    "kind": chunk.kind,
                    "language": chunk.language,
                    "signature": chunk.signature,
                    "content": chunk.content,
                    "context": chunk.context,
                    "line": chunk.line,
                    "end_line": chunk.end_line,
                },
            ))
        self.client.upsert(collection_name=COLLECTION, points=points)

    def search_similar(self, query: str, limit: int = 10) -> list[dict]:
        """Find code chunks semantically similar to a query."""
        query_vector = embed_text(query)
        results = self.client.query_points(
            collection_name=COLLECTION,
            query=query_vector,
            limit=limit,
        )
        return [
            {**hit.payload, "score": hit.score}
            for hit in results.points
        ]
