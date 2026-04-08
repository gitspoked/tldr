"""Tests for peek module."""

import json
import json as _json
from pathlib import Path
from tldreadme.peek import peek_target


def test_peek_directory_basic(tmp_path):
    """Layer 0: filesystem scan of a directory returns stats and type."""
    (tmp_path / "main.py").write_text("def hello():\n    pass\n")
    (tmp_path / "util.py").write_text("x = 1\ny = 2\n")
    (tmp_path / "README.md").write_text("# Hello\n")

    result = peek_target(tmp_path)

    assert result["type"] == "directory"
    assert result["path"] == str(tmp_path.resolve())
    assert result["stats"]["files"] >= 3
    assert result["stats"]["lines"] >= 5
    assert "py" in result["stats"]["extensions"]
    assert result["stats"]["extensions"]["py"] == 2
    assert "md" in result["stats"]["extensions"]
    assert "enrichment_layers" in result
    assert "disk" in result["enrichment_layers"]


# ---------------------------------------------------------------------------
# Task 2: File mode + symbol extraction
# ---------------------------------------------------------------------------


def test_peek_file_basic(tmp_path):
    """Layer 0: file scan returns line count, extension, and symbols."""
    code = (
        "class MyClass:\n"
        "    def method(self):\n"
        "        pass\n"
        "\n"
        "def standalone():\n"
        "    pass\n"
        "\n"
        "async def async_handler():\n"
        "    pass\n"
    )
    py_file = tmp_path / "example.py"
    py_file.write_text(code)

    result = peek_target(py_file)

    assert result["type"] == "file"
    assert result["path"] == str(py_file.resolve())
    assert result["stats"]["lines"] == 9
    assert result["stats"]["extensions"] == {"py": 1}
    assert "disk" in result["enrichment_layers"]
    names = [s["name"] for s in result["symbols"]]
    assert "MyClass" in names
    assert "standalone" in names
    assert "async_handler" in names


def test_peek_file_large_skips_symbols(tmp_path):
    """Files over 1MB skip symbol extraction."""
    big_file = tmp_path / "big.py"
    big_file.write_text("def func():\n    pass\n" * 60000)
    assert big_file.stat().st_size > 1_000_000

    result = peek_target(big_file)

    assert result["type"] == "file"
    assert result["stats"]["lines"] > 0
    assert result["symbols"] == []


# ---------------------------------------------------------------------------
# Task 3: Context docs scanning (Layer 1)
# ---------------------------------------------------------------------------


def test_peek_directory_context_docs(tmp_path):
    (tmp_path / "README.md").write_text("# My Project\n\nA tool that does things.\n")
    (tmp_path / "CLAUDE.md").write_text("# CLAUDE.md\n\n## Architecture\n\nModular design.\n")
    (tmp_path / "main.py").write_text("print('hello')\n")

    result = peek_target(tmp_path)

    assert "context_docs" in result["enrichment_layers"]
    docs = result["context_docs"]
    kinds = [d["kind"] for d in docs]
    assert "readme" in kinds
    assert "claude" in kinds
    for doc in docs:
        assert "kind" in doc
        assert "title" in doc
        assert "summary" in doc


def test_peek_file_inherits_project_context(tmp_path):
    (tmp_path / "README.md").write_text("# Root Project\n\nThe root.\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "myproj"\nversion = "1.0.0"\n')
    src = tmp_path / "src"
    src.mkdir()
    py_file = src / "app.py"
    py_file.write_text("def main():\n    pass\n")

    result = peek_target(py_file)

    assert "context_docs" in result["enrichment_layers"]
    kinds = [d["kind"] for d in result["context_docs"]]
    assert "readme" in kinds


# ---------------------------------------------------------------------------
# Task 4: Manifest detection tests
# ---------------------------------------------------------------------------


def test_peek_directory_detects_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-tool"\nversion = "2.1.0"\n')
    (tmp_path / "src.py").write_text("x = 1\n")

    result = peek_target(tmp_path)

    assert result["project"] is not None
    assert result["project"]["name"] == "my-tool"
    assert result["project"]["version"] == "2.1.0"
    assert result["project"]["manifest"] == "pyproject.toml"


def test_peek_directory_no_manifest(tmp_path):
    (tmp_path / "script.sh").write_text("#!/bin/bash\necho hi\n")

    result = peek_target(tmp_path)

    assert result["project"] is None


# ---------------------------------------------------------------------------
# Task 5: Indexed knowledge (Layer 2)
# ---------------------------------------------------------------------------


def test_peek_directory_with_tldr(tmp_path):
    (tmp_path / "main.py").write_text("def hello(): pass\n")

    tldr_dir = tmp_path / ".tldr"
    tldr_dir.mkdir()
    hot_data = {
        "root": str(tmp_path),
        "top_files": ["main.py"],
        "entries": {
            "hello": {
                "name": "hello",
                "kind": "function",
                "importance": 0.9,
                "hit_count": 5,
                "locations": [{"file": "main.py", "line": 1, "context": "def hello(): pass"}],
            }
        },
    }
    (tldr_dir / "hot_index.json").write_text(_json.dumps(hot_data))

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "TLDR.md").write_text("# Summary\n\nThis project says hello.\n")

    result = peek_target(tmp_path)

    assert result["indexed"] is True
    assert "tldr" in result["enrichment_layers"]
    assert len(result["hot_symbols"]) > 0
    assert result["hot_symbols"][0]["name"] == "hello"
    assert result["generated_summary"] is not None
    assert "hello" in result["generated_summary"]


def test_peek_directory_without_tldr(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n")

    result = peek_target(tmp_path)

    assert result["indexed"] is False
    assert "tldr" not in result["enrichment_layers"]
    assert result["hot_symbols"] == []
    assert result["generated_summary"] is None


# ---------------------------------------------------------------------------
# Task 6: Live services (Layer 3)
# ---------------------------------------------------------------------------


def test_peek_layer3_graceful_when_services_down(tmp_path, monkeypatch):
    """Layer 3 degrades gracefully when services are unreachable."""
    (tmp_path / "main.py").write_text("def hello(): pass\n")

    # Point to non-existent ports so the connection always fails
    monkeypatch.setenv("QDRANT_URL", "http://localhost:19999")
    monkeypatch.setenv("FALKORDB_URL", "redis://localhost:19998")

    result = peek_target(tmp_path)

    assert "qdrant" not in result["enrichment_layers"]
    assert "falkordb" not in result["enrichment_layers"]
    assert any("qdrant" in f for f in result["fallback_used"]) or \
           result["fallback_used"] == []


# ---------------------------------------------------------------------------
# Task 7: Rendering functions
# ---------------------------------------------------------------------------


from tldreadme.peek import render_peek, render_peek_markdown


def test_render_peek_directory(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
    (tmp_path / "README.md").write_text("# Demo\n\nA demo project.\n")
    (tmp_path / "app.py").write_text("def main():\n    pass\n")

    result = peek_target(tmp_path)
    output = render_peek(result)

    assert "demo" in output
    assert "0.1.0" in output
    assert "pyproject.toml" in output
    assert "py" in output


def test_render_peek_markdown(tmp_path):
    (tmp_path / "main.py").write_text("def hello(): pass\n")
    (tmp_path / "README.md").write_text("# Test\n\nA test.\n")

    result = peek_target(tmp_path)
    md = render_peek_markdown(result)

    assert md.startswith("#")
    assert "main.py" in md or "Test" in md or "test" in md


# ---------------------------------------------------------------------------
# Task 8: Router contract mapping
# ---------------------------------------------------------------------------


from tldreadme.peek import peek_to_router_result


def test_peek_to_router_result(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\n\nA demo tool.\n")
    (tmp_path / "main.py").write_text("def main(): pass\n")

    peek_result = peek_target(tmp_path)
    router_result = peek_to_router_result(peek_result)

    assert "summary" in router_result
    assert "confidence" in router_result
    assert "evidence" in router_result
    assert "recommended_next_action" in router_result
    assert "fallback_used" in router_result
    assert isinstance(router_result["confidence"], float)
    assert 0.0 <= router_result["confidence"] <= 1.0
    assert "init" in router_result["recommended_next_action"].lower() or \
           "indexed" in router_result["recommended_next_action"].lower()


def test_peek_to_router_result_indexed(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n")
    tldr_dir = tmp_path / ".tldr"
    tldr_dir.mkdir()
    (tldr_dir / "hot_index.json").write_text(_json.dumps({
        "root": str(tmp_path), "top_files": [], "entries": {}
    }))

    peek_result = peek_target(tmp_path)
    router_result = peek_to_router_result(peek_result)

    assert router_result["confidence"] >= 0.7


# ---------------------------------------------------------------------------
# Task 9: CLI wiring
# ---------------------------------------------------------------------------

import json as _json_cli  # noqa: avoid conflict with earlier import

from click.testing import CliRunner
from tldreadme.cli import main as cli_main


def test_cli_peek_explicit(tmp_path):
    """tldr peek <dir> works as an explicit command."""
    (tmp_path / "main.py").write_text("x = 1\n")
    runner = CliRunner()
    result = runner.invoke(cli_main, ["peek", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "files" in result.output or "main.py" in result.output


def test_cli_peek_json(tmp_path):
    """tldr peek --json-output produces valid JSON."""
    (tmp_path / "main.py").write_text("x = 1\n")
    runner = CliRunner()
    result = runner.invoke(cli_main, ["peek", str(tmp_path), "--json-output"])
    assert result.exit_code == 0, result.output
    data = _json_cli.loads(result.output)
    assert data["type"] == "directory"


def test_cli_bare_path_invokes_peek(tmp_path):
    """tldr <dir> (no subcommand) invokes peek, not init."""
    (tmp_path / "main.py").write_text("x = 1\n")
    runner = CliRunner()
    result = runner.invoke(cli_main, [str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Indexing directory" not in result.output
    assert "files" in result.output or "Indexed?" in result.output


def test_cli_bare_file_invokes_peek(tmp_path):
    """tldr <file> (no subcommand) invokes peek for files too."""
    py_file = tmp_path / "example.py"
    py_file.write_text("def hello():\n    pass\n")
    runner = CliRunner()
    result = runner.invoke(cli_main, [str(py_file)])
    assert result.exit_code == 0, result.output
    assert "example.py" in result.output


# ---------------------------------------------------------------------------
# Task 10: repo_lookup peek fallback
# ---------------------------------------------------------------------------

from tldreadme.coding_tools import repo_lookup  # noqa: E402


def test_repo_lookup_peek_fallback(tmp_path):
    """repo_lookup falls back to peek for unindexed directories (no Qdrant/FalkorDB)."""
    (tmp_path / "main.py").write_text("def hello(): pass\n")
    (tmp_path / "README.md").write_text("# Test\n\nA test project.\n")

    result = repo_lookup(root=str(tmp_path))

    assert "summary" in result
    assert "confidence" in result
    assert isinstance(result["confidence"], float)
    assert result["confidence"] > 0


# ---------------------------------------------------------------------------
# Task 12: Full integration test
# ---------------------------------------------------------------------------

from tldreadme.peek import render_peek, render_peek_markdown, peek_to_router_result  # noqa: E402


def test_peek_full_integration(tmp_path):
    """End-to-end: directory with manifest, docs, .tldr/, rendering, and router mapping."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test-project"\nversion = "1.2.3"\n'
        'dependencies = ["click", "rich"]\n'
    )
    (tmp_path / "README.md").write_text("# Test Project\n\nA project for testing peek.\n")
    (tmp_path / "CLAUDE.md").write_text("# CLAUDE.md\n\n## Architecture\n\nSimple design.\n")

    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text(
        "class App:\n"
        "    def run(self):\n"
        "        pass\n"
        "\n"
        "def create_app():\n"
        "    return App()\n"
    )
    (src / "utils.py").write_text("def helper():\n    return 42\n")

    tldr_dir = tmp_path / ".tldr"
    tldr_dir.mkdir()
    (tldr_dir / "hot_index.json").write_text(_json.dumps({
        "root": str(tmp_path),
        "top_files": ["src/main.py"],
        "entries": {
            "App": {
                "name": "App", "kind": "class", "importance": 0.95,
                "hit_count": 10,
                "locations": [{"file": "src/main.py", "line": 1, "context": "class App:"}],
            },
        },
    }))

    result = peek_target(tmp_path)

    # Layer 0
    assert result["type"] == "directory"
    assert result["stats"]["files"] >= 4
    assert "py" in result["stats"]["extensions"]

    # Layer 1
    assert "context_docs" in result["enrichment_layers"]
    kinds = [d["kind"] for d in result["context_docs"]]
    assert "readme" in kinds
    assert "claude" in kinds
    assert result["project"]["name"] == "test-project"
    assert result["project"]["version"] == "1.2.3"

    # Layer 2
    assert result["indexed"] is True
    assert "tldr" in result["enrichment_layers"]
    assert any(s["name"] == "App" for s in result["hot_symbols"])

    # Rendering
    output = render_peek(result)
    assert "test-project" in output
    assert "1.2.3" in output

    md = render_peek_markdown(result)
    assert md.startswith("#")
    assert "test-project" in md

    # Router mapping
    router = peek_to_router_result(result)
    assert router["confidence"] >= 0.7
    assert len(router["evidence"]) > 0
