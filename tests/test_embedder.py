"""Tests for embedder — chunk creation, IDs, and data structures.

These tests cover everything that doesn't need a running Qdrant/Ollama.
"""

import tempfile
from pathlib import Path

from tldreadme.embedder import (
    chunk_id, _chunk_id_to_int, symbols_to_chunks, CodeChunk,
)
from tldreadme.parser import parse_file, ParseResult


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
        f.write(
            "def alpha():\n    pass\n\n"
            "def beta():\n    pass\n\n"
            "class Gamma:\n    pass\n"
        )
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
        f.write(
            "def one(): pass\n"
            "def two(): pass\n"
            "def three(): pass\n"
        )
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
