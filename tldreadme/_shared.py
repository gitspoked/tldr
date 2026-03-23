"""Shared singleton instances — one connection per process, not per call."""

from .lazy import load_attr

_embedder = None
_grapher = None


def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = load_attr("tldreadme.embedder", "CodeEmbedder")()
    return _embedder


def get_grapher():
    global _grapher
    if _grapher is None:
        _grapher = load_attr("tldreadme.grapher", "CodeGrapher")()
    return _grapher
