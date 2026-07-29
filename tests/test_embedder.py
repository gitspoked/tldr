"""Tests for embedder - chunk creation, IDs, and data structures.

These tests cover everything that doesn't need a running Qdrant/Ollama.
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tldreadme import embedder
from tldreadme.embedder import (
    CodeChunk,
    _chunk_id_to_int,
    chunk_id,
    collection_name_for_model,
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


def test_collection_name_separates_embedding_models():
    assert collection_name_for_model("ollama/nomic-embed-text") == "tldreadme_code"
    assert collection_name_for_model("nomic-embed-text:latest") == "tldreadme_code"
    assert collection_name_for_model("ollama/mxbai-embed-large").startswith(
        "tldreadme_code_mxbai_embed_large_"
    )
    assert collection_name_for_model("ollama/mxbai-embed-large") != collection_name_for_model(
        "ollama/qwen3-embedding"
    )


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
    code_embedder.collection_name = "tldreadme_code_test"

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
    code_embedder.collection_name = "tldreadme_code_test"

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
    code_embedder.collection_name = "tldreadme_code_test"

    results = code_embedder.search_similar("sample", repo_root=tmp_path)

    assert len(calls) == 2
    assert [item["symbol_name"] for item in results] == ["local"]


def test_full_repository_index_removes_stale_points_after_success(monkeypatch, tmp_path):
    upserts = []
    deletes = []

    class FakeClient:
        def upsert(self, **kwargs):
            upserts.append(kwargs)

        def delete(self, **kwargs):
            deletes.append(kwargs)

    def model_factory(**kwargs):
        return kwargs

    monkeypatch.setattr(
        embedder,
        "_qdrant_models",
        lambda: {
            "FieldCondition": model_factory,
            "Filter": model_factory,
            "FilterSelector": model_factory,
            "MatchValue": model_factory,
            "PointStruct": model_factory,
        },
    )
    monkeypatch.setattr(
        embedder,
        "embed_batch",
        lambda texts, **_kwargs: [[0.1, 0.2] for _text in texts],
    )
    repo_root = str(tmp_path.resolve())
    chunks = [
        CodeChunk(
            id="0000000000000001",
            file=str(tmp_path / "sample.py"),
            symbol_name="sample",
            kind="function",
            language="python",
            content="def sample(): pass",
            signature="def sample():",
            context="file: sample.py",
            line=1,
            end_line=1,
            repo_root=repo_root,
        )
    ]
    code_embedder = object.__new__(embedder.CodeEmbedder)
    code_embedder.client = FakeClient()
    code_embedder.collection_name = "tldreadme_code_test"
    code_embedder._collection_created = True

    code_embedder.index_chunks(
        chunks,
        replace_repository=True,
        repo_root=tmp_path,
    )

    assert len(upserts) == 1
    assert len(deletes) == 1
    index_run = upserts[0]["points"][0]["payload"]["index_run"]
    assert index_run
    assert deletes[0] == {
        "collection_name": "tldreadme_code_test",
        "points_selector": {
            "filter": {
                "must": [
                    {
                        "key": "repo_root",
                        "match": {"value": repo_root},
                    }
                ],
                "must_not": [
                    {
                        "key": "index_run",
                        "match": {"value": index_run},
                    }
                ],
            }
        },
        "wait": True,
    }


def test_full_repository_index_keeps_old_points_when_embedding_fails(monkeypatch, tmp_path):
    upserts = []
    deletes = []
    embed_calls = 0

    class FakeClient:
        def upsert(self, **kwargs):
            upserts.append(kwargs)

        def delete(self, **kwargs):
            deletes.append(kwargs)

    def model_factory(**kwargs):
        return kwargs

    def fake_embed(texts, **_kwargs):
        nonlocal embed_calls
        embed_calls += 1
        if embed_calls == 2:
            raise RuntimeError("provider stopped")
        return [[0.1, 0.2] for _text in texts]

    monkeypatch.setattr(
        embedder,
        "_qdrant_models",
        lambda: {
            "FieldCondition": model_factory,
            "Filter": model_factory,
            "FilterSelector": model_factory,
            "MatchValue": model_factory,
            "PointStruct": model_factory,
        },
    )
    monkeypatch.setattr(embedder, "embed_batch", fake_embed)
    repo_root = str(tmp_path.resolve())
    chunks = [
        CodeChunk(
            id=f"{index + 1:016x}",
            file=str(tmp_path / f"sample_{index}.py"),
            symbol_name=f"sample_{index}",
            kind="function",
            language="python",
            content=f"def sample_{index}(): pass",
            signature=f"def sample_{index}():",
            context=f"file: sample_{index}.py",
            line=1,
            end_line=1,
            repo_root=repo_root,
        )
        for index in range(2)
    ]
    code_embedder = object.__new__(embedder.CodeEmbedder)
    code_embedder.client = FakeClient()
    code_embedder.collection_name = "tldreadme_code_test"
    code_embedder._collection_created = True

    with pytest.raises(RuntimeError, match="provider stopped"):
        code_embedder.index_chunks(
            chunks,
            slice_size=1,
            replace_repository=True,
            repo_root=tmp_path,
        )

    assert len(upserts) == 1
    assert deletes == []
