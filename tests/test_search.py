"""Tests for ripgrep search wrapper."""

import tempfile
from pathlib import Path

from tldreadme.search import rg_search, rg_files, rg_count, format_hits_for_llm


def _make_project(tmpdir):
    """Create a small project to search in."""
    root = Path(tmpdir)
    (root / "main.py").write_text(
        "def main():\n"
        "    print('hello world')\n"
        "    result = process_data()\n"
        "    return result\n"
    )
    (root / "lib.py").write_text(
        "def process_data():\n"
        "    # TODO: implement real processing\n"
        '    return {"status": "ok"}\n'
        "\n"
        "def helper():\n"
        "    pass\n"
    )
    (root / "config.py").write_text(
        'DATABASE_URL = "postgres://localhost/mydb"\n'
        'API_KEY = "secret"\n'
    )
    return root


def test_rg_search_finds_matches():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _make_project(tmpdir)
        hits = rg_search("process_data", [str(root)])

    assert len(hits) >= 1
    texts = [h.text for h in hits]
    assert any("process_data" in t for t in texts)


def test_rg_search_with_context():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _make_project(tmpdir)
        hits = rg_search("process_data", [str(root)], context=2)

    assert len(hits) >= 1
    # Should have context lines
    hit = hits[0]
    assert isinstance(hit.before, list)
    assert isinstance(hit.after, list)


def test_rg_search_no_matches():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _make_project(tmpdir)
        hits = rg_search("nonexistent_symbol_xyz", [str(root)])

    assert len(hits) == 0


def test_rg_search_regex():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _make_project(tmpdir)
        hits = rg_search("def (main|helper)", [str(root)])

    assert len(hits) >= 2


def test_rg_search_max_results():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _make_project(tmpdir)
        hits = rg_search("def ", [str(root)], max_results=1)

    assert len(hits) <= 1


def test_rg_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _make_project(tmpdir)
        files = rg_files("process_data", [str(root)])

    assert len(files) >= 1
    assert any("main.py" in f or "lib.py" in f for f in files)


def test_rg_files_no_matches():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _make_project(tmpdir)
        files = rg_files("zzz_not_here_zzz", [str(root)])

    assert len(files) == 0


def test_rg_count():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _make_project(tmpdir)
        counts = rg_count("def ", [str(root)])

    assert len(counts) >= 1
    total = sum(counts.values())
    assert total >= 3  # main, process_data, helper


def test_format_hits_for_llm():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _make_project(tmpdir)
        hits = rg_search("TODO", [str(root)])
        formatted = format_hits_for_llm(hits)

    assert "TODO" in formatted
    assert "```" in formatted


def test_format_hits_for_llm_empty():
    formatted = format_hits_for_llm([])
    assert formatted == ""


def test_format_hits_max_chars():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = _make_project(tmpdir)
        hits = rg_search("def ", [str(root)])
        formatted = format_hits_for_llm(hits, max_chars=50)

    # Should be truncated
    assert len(formatted) <= 200  # some slack for the last block
