"""Tests for embedder - chunk creation, IDs, and data structures.

These tests cover everything that doesn't need a running Qdrant/Ollama.
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace

from tldreadme import embedder
from tldreadme.embedder import (
    CodeChunk,
    _chunk_id_to_int,
    chunk_id,
    symbols_to_chunks,
)
from tldreadme.parser import parse_file

# ── Chunk ID ──────────────────────────────────────────────────────


def test_chunk_id_deterministic():
    """Same input = same ID, always."""
    id1 = chunk_id("src/main.rs", "process", 42)
    id2 = chunk_id("src/main.rs", "process", 42)
    assert id1 == id2


def test_chunk_id_different_inputs():
    """Different inputs = different IDs."""
    id1 = chunk_id("src/main.rs", "process", 42)
    id2 = chunk_id("src/main.rs", "process", 43)
    id3 = chunk_id("src/lib.rs", "process", 42)
    assert id1 != id2
    assert id1 != id3


def test_chunk_id_is_hex():
    cid = chunk_id("test.py", "foo", 1)
    assert len(cid) == 16
    int(cid, 16)  # should not raise


def test_chunk_id_to_int():
    cid = chunk_id("test.py", "foo", 1)
    int_id = _chunk_id_to_int(cid)
    assert isinstance(int_id, int)
    assert int_id > 0


def test_chunk_id_to_int_deterministic():
    cid = chunk_id("test.py", "bar", 10)
    assert _chunk_id_to_int(cid) == _chunk_id_to_int(cid)


def test_chunk_id_to_int_unique():
    id1 = _chunk_id_to_int(chunk_id("a.py", "x", 1))
    id2 = _chunk_id_to_int(chunk_id("a.py", "y", 1))
    assert id1 != id2


# ── Symbols to Chunks ────────────────────────────────────────────


def test_symbols_to_chunks():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def alpha():\n    pass\n\ndef beta():\n    pass\n\nclass Gamma:\n    pass\n")
        f.flush()
        result = parse_file(Path(f.name))

    chunks = symbols_to_chunks([result])
    assert len(chunks) >= 2

    # Every chunk has required fields
    for c in chunks:
        assert isinstance(c, CodeChunk)
        assert c.id  # non-empty
        assert c.file
        assert c.symbol_name
        assert c.kind
        assert c.language == "python"
        assert c.line > 0


def test_symbols_to_chunks_empty():
    chunks = symbols_to_chunks([])
    assert chunks == []


def test_symbols_to_chunks_record_repository_identity(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def sample():\n    return 1\n", encoding="utf-8")

    chunks = symbols_to_chunks([parse_file(source)], repo_root=tmp_path)

    assert chunks
    assert {chunk.repo_root for chunk in chunks} == {str(tmp_path.resolve())}


def test_symbols_to_chunks_no_symbols():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("# just a comment\nx = 1\n")
        f.flush()
        result = parse_file(Path(f.name))

    if result:
        chunks = symbols_to_chunks([result])
        # May or may not have chunks depending on whether x=1 is a symbol
        assert isinstance(chunks, list)


def test_chunks_have_unique_ids():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def one(): pass\ndef two(): pass\ndef three(): pass\n")
        f.flush()
        result = parse_file(Path(f.name))

    chunks = symbols_to_chunks([result])
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))  # all unique


def test_chunk_content_is_actual_code():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def greet(name):\n    return f'Hello {name}'\n")
        f.flush()
        result = parse_file(Path(f.name))

    chunks = symbols_to_chunks([result])
    assert len(chunks) >= 1
    greet_chunk = next(c for c in chunks if c.symbol_name == "greet")
    assert "def greet" in greet_chunk.content
    assert "Hello" in greet_chunk.content


def test_qdrant_client_skips_eager_compatibility_probe(monkeypatch):
    captured = {}

    class FakeQdrantClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def get_collections(self):
            return SimpleNamespace(collections=[])

    monkeypatch.setattr(embedder, "_qdrant_client_cls", lambda: FakeQdrantClient)
    monkeypatch.delenv("QDRANT_API_KEY", raising=False)

    client = embedder.CodeEmbedder("http://127.0.0.1:6333")

    assert client._collection_created is False
    assert captured == {
        "url": "http://127.0.0.1:6333",
        "check_compatibility": False,
    }


def test_qdrant_client_passes_api_key_from_environment(monkeypatch):
    captured = {}

    class FakeQdrantClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def get_collections(self):
            return SimpleNamespace(collections=[])

    monkeypatch.setattr(embedder, "_qdrant_client_cls", lambda: FakeQdrantClient)
    monkeypatch.setenv("QDRANT_API_KEY", "test-secret")

    client = embedder.CodeEmbedder("http://127.0.0.1:6333")

    assert client._collection_created is False
    assert captured == {
        "url": "http://127.0.0.1:6333",
        "check_compatibility": False,
        "api_key": "test-secret",
    }


def test_search_similar_is_repository_scoped_by_default(monkeypatch, tmp_path):
    captured = {}

    class FakeClient:
        def query_points(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                points=[SimpleNamespace(payload={"symbol_name": "sample"}, score=0.9)]
            )

    def model_factory(**kwargs):
        return kwargs

    monkeypatch.setattr(embedder, "embed_text", lambda _query: [0.1, 0.2])
    monkeypatch.setattr(
        embedder,
        "_qdrant_models",
        lambda: {
            "FieldCondition": model_factory,
            "Filter": model_factory,
            "MatchValue": model_factory,
        },
    )
    code_embedder = object.__new__(embedder.CodeEmbedder)
    code_embedder.client = FakeClient()

    results = code_embedder.search_similar("sample", repo_root=tmp_path)

    assert results[0]["symbol_name"] == "sample"
    assert captured["query_filter"] == {
        "must": [
            {
                "key": "repo_root",
                "match": {"value": str(tmp_path.resolve())},
            }
        ]
    }


def test_search_similar_cross_repository_requires_explicit_opt_in(monkeypatch):
    captured = {}

    class FakeClient:
        def query_points(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(points=[])

    monkeypatch.setattr(embedder, "embed_text", lambda _query: [0.1, 0.2])
    code_embedder = object.__new__(embedder.CodeEmbedder)
    code_embedder.client = FakeClient()

    code_embedder.search_similar("shared pattern", cross_repository=True)

    assert captured["query_filter"] is None


def test_search_similar_legacy_fallback_filters_out_other_repositories(monkeypatch, tmp_path):
    calls = []
    local_file = tmp_path / "local.py"
    outside_file = tmp_path.parent / "other-repo" / "outside.py"

    class FakeClient:
        def query_points(self, **kwargs):
            calls.append(kwargs)
            if kwargs["query_filter"] is not None:
                return SimpleNamespace(points=[])
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        payload={"symbol_name": "outside", "file": str(outside_file)},
                        score=0.95,
                    ),
                    SimpleNamespace(
                        payload={"symbol_name": "local", "file": str(local_file)},
                        score=0.9,
                    ),
                ]
            )

    def model_factory(**kwargs):
        return kwargs

    monkeypatch.setattr(embedder, "embed_text", lambda _query: [0.1, 0.2])
    monkeypatch.setattr(
        embedder,
        "_qdrant_models",
        lambda: {
            "FieldCondition": model_factory,
            "Filter": model_factory,
            "MatchValue": model_factory,
        },
    )
    code_embedder = object.__new__(embedder.CodeEmbedder)
    code_embedder.client = FakeClient()

    results = code_embedder.search_similar("sample", repo_root=tmp_path)

    assert len(calls) == 2
    assert [item["symbol_name"] for item in results] == ["local"]
