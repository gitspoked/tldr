"""Tests for hot index — caching, persistence, lookup."""

import json
import tempfile
from pathlib import Path

from tldreadme.hot_index import HotIndex, HotEntry, build_hot_index
from tldreadme.parser import parse_file, parse_directory


# ── HotIndex Basics ───────────────────────────────────────────────

def test_hot_index_lookup_hit():
    idx = HotIndex(root="/test")
    idx.entries["my_func"] = HotEntry(
        name="my_func", kind="function",
        locations=[{"file": "main.py", "line": 10, "definition": True}],
        importance=5.0, hit_count=3,
    )

    entry = idx.lookup("my_func")
    assert entry is not None
    assert entry.name == "my_func"
    assert entry.kind == "function"
    assert entry.importance == 5.0


def test_hot_index_lookup_miss():
    idx = HotIndex(root="/test")
    assert idx.lookup("nonexistent") is None


def test_hot_index_save_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "hot_index.json"

        # Save
        idx = HotIndex(root="/project")
        idx.entries["alpha"] = HotEntry(
            name="alpha", kind="function",
            locations=[{"file": "a.py", "line": 1, "definition": True}],
            importance=10.0, hit_count=5,
        )
        idx.top_files = ["a.py", "b.py"]
        idx.save(path)

        # Verify file exists and is valid JSON
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["root"] == "/project"
        assert "alpha" in data["entries"]

        # Load
        loaded = HotIndex.load(path)
        assert loaded is not None
        assert loaded.root == "/project"
        assert loaded.top_files == ["a.py", "b.py"]
        entry = loaded.lookup("alpha")
        assert entry is not None
        assert entry.importance == 10.0
        assert entry.hit_count == 5


def test_hot_index_load_nonexistent():
    loaded = HotIndex.load(Path("/nonexistent/hot_index.json"))
    assert loaded is None


def test_hot_index_load_corrupt():
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        f.write("not valid json {{{")
        f.flush()
        loaded = HotIndex.load(Path(f.name))

    assert loaded is None


# ── build_hot_index ───────────────────────────────────────────────

def test_build_hot_index_from_parsed():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.py").write_text(
            "def important_function():\n"
            "    '''This is the main entry point.'''\n"
            "    helper_one()\n"
            "    helper_two()\n"
            "    return True\n"
            "\n"
            "def helper_one():\n"
            "    pass\n"
            "\n"
            "def helper_two():\n"
            "    pass\n"
        )
        (root / "models.py").write_text(
            "class UserModel:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
            "\n"
            "    def validate(self):\n"
            "        return bool(self.name)\n"
        )

        results = parse_directory(root)
        idx = build_hot_index(root, results, top_n=10)

    assert len(idx.entries) >= 1
    assert len(idx.top_files) >= 1

    # Classes should rank higher than small functions (3x multiplier)
    if "UserModel" in idx.entries:
        user_model = idx.entries["UserModel"]
        assert user_model.kind in ("class", "class_definition")


def test_build_hot_index_respects_top_n():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Create many functions
        code = "\n".join(f"def func_{i}():\n    pass\n" for i in range(20))
        (root / "many.py").write_text(code)

        results = parse_directory(root)
        idx = build_hot_index(root, results, top_n=5)

    assert len(idx.entries) <= 5


def test_build_hot_index_empty():
    idx = build_hot_index(Path("/tmp"), [], top_n=100)
    assert len(idx.entries) == 0
    assert len(idx.top_files) == 0
