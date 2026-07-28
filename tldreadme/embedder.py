"""Embed code chunks and store them in Qdrant."""

import hashlib
from dataclasses import dataclass

from .config import get_setting
from .lazy import load_attr
from .model_client import ModelClient
from .parser import ParseResult

COLLECTION = "tldreadme_code"


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


def model_status(model: str) -> dict[str, object]:
    """Return current provider readiness without triggering a model pull."""

    return ModelClient().model_status(model)


@dataclass
class CodeChunk:
    """A chunk of code ready for embedding."""

    id: str
    file: str
    symbol_name: str
    kind: str
    language: str
    content: str  # the actual code (body)
    signature: str
    context: str  # surrounding info (parent, module, imports)
    line: int
    end_line: int


def chunk_id(file: str, name: str, line: int) -> str:
    """Deterministic ID for a code chunk - same symbol at same location = same ID."""
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
            chunks.append(
                CodeChunk(
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
                )
            )
    return chunks


def embed_text(text: str) -> list[float]:
    """Get embedding vector for a piece of text."""

    return ModelClient().embed([text])[0]


def complete_chat(messages: list[dict[str, str]], *, max_tokens: int) -> str:
    """Run a bounded chat completion after checking local model readiness."""

    return ModelClient().complete(messages, max_tokens=max_tokens)


def embed_batch(texts: list[str], **_kwargs) -> list[list[float]]:
    """Embed a batch through the configured provider."""

    return ModelClient().embed(texts)


class CodeEmbedder:
    """Manages embedding storage in Qdrant."""

    def __init__(self, qdrant_url: str = None):
        self.client = _qdrant_client_cls()(
            url=qdrant_url or get_setting("QDRANT_URL"),
            check_compatibility=False,
        )
        self._ensure_collection()

    def _ensure_collection(self):
        collections = [c.name for c in self.client.get_collections().collections]
        if COLLECTION not in collections:
            # Dimension depends on model - nomic-embed-text = 768, OpenAI = 1536
            # We'll detect on first embed
            self._collection_created = False
        else:
            self._collection_created = True

    def index_chunks(self, chunks: list[CodeChunk], slice_size: int = 500):
        """Embed and store chunks in memory-safe slices.

        Processing 190K+ symbols in one shot exhausts RAM.  This streams
        slices of ``slice_size`` chunks: embed → upsert → free → next.
        """
        if not chunks:
            return

        import sys

        point_struct = _qdrant_models()["PointStruct"]
        total = len(chunks)

        for start in range(0, total, slice_size):
            end = min(start + slice_size, total)
            batch_chunks = chunks[start:end]

            texts = [f"{c.signature}\n{c.context}\n{c.content[:2000]}" for c in batch_chunks]

            vectors = embed_batch(texts, max_workers=1)

            # Create collection on first use (auto-detect dimension)
            if not self._collection_created:
                models = _qdrant_models()
                self.client.create_collection(
                    collection_name=COLLECTION,
                    vectors_config=models["VectorParams"](
                        size=len(vectors[0]), distance=models["Distance"].COSINE
                    ),
                )
                self._collection_created = True

            points = [
                point_struct(
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
                )
                for chunk, vector in zip(batch_chunks, vectors)
            ]
            self.client.upsert(collection_name=COLLECTION, points=points)

            if end % 2000 == 0 or end == total:
                print(
                    f"  embedded {end}/{total} chunks",
                    file=sys.stderr,
                )

    def search_similar(self, query: str, limit: int = 10) -> list[dict]:
        """Find code chunks semantically similar to a query."""
        query_vector = embed_text(query)
        results = self.client.query_points(
            collection_name=COLLECTION,
            query=query_vector,
            limit=limit,
        )
        return [{**hit.payload, "score": hit.score} for hit in results.points]
