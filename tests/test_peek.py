"""Tests for peek module."""

import json
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
