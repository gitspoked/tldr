"""Embed code chunks into Qdrant via LiteLLM."""

from dataclasses import dataclass
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
import litellm
import hashlib
import os

from .parser import Symbol, ParseResult

COLLECTION = "tldreadme_code"
EMBED_MODEL = "embed"  # routes through LiteLLM to whatever backend
LITELLM_URL = os.getenv("LITELLM_URL", "http://localhost:4000")


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
    raw = f"{file}:{name}:{line}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


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
    resp = litellm.embedding(
        model=EMBED_MODEL,
        input=[text],
        api_base=LITELLM_URL,
    )
    return resp.data[0]["embedding"]


def embed_batch(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Embed a batch of texts."""
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        resp = litellm.embedding(
            model=EMBED_MODEL,
            input=batch,
            api_base=LITELLM_URL,
        )
        all_embeddings.extend([d["embedding"] for d in resp.data])
    return all_embeddings


class CodeEmbedder:
    """Manages embedding storage in Qdrant."""

    def __init__(self, qdrant_url: str = "http://localhost:6333"):
        self.client = QdrantClient(url=qdrant_url)
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
            self.client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE),
            )
            self._collection_created = True

        # Upsert points
        points = []
        for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
            points.append(PointStruct(
                id=i,  # qdrant needs int or uuid
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
        results = self.client.search(
            collection_name=COLLECTION,
            query_vector=query_vector,
            limit=limit,
        )
        return [
            {**hit.payload, "score": hit.score}
            for hit in results
        ]
