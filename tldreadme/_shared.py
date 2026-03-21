"""Shared singleton instances — one connection per process, not per call."""

from .embedder import CodeEmbedder
from .grapher import CodeGrapher

_embedder: CodeEmbedder | None = None
_grapher: CodeGrapher | None = None


def get_embedder() -> CodeEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = CodeEmbedder()
    return _embedder


def get_grapher() -> CodeGrapher:
    global _grapher
    if _grapher is None:
        _grapher = CodeGrapher()
    return _grapher
