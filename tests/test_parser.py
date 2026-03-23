"""Tests for tree-sitter parser — the foundation of everything."""

import tempfile
from pathlib import Path

from tldreadme.parser import (
    parse_file, parse_directory, detect_language,
    extract_deps_from_directory, scan_context_docs,
    _parse_markdown_sections,
)


# ── Language Detection ────────────────────────────────────────────

def test_detect_language_core_four():
    assert detect_language(Path("foo.py")) == "python"
    assert detect_language(Path("bar.rs")) == "rust"
    assert detect_language(Path("baz.ts")) == "typescript"
    assert detect_language(Path("qux.js")) == "javascript"


def test_detect_language_secondary():
    assert detect_language(Path("main.go")) == "go"
    assert detect_language(Path("lib.c")) == "c"
    assert detect_language(Path("app.java")) == "java"
    assert detect_language(Path("mod.rb")) == "ruby"


def test_detect_language_tsx_jsx():
    assert detect_language(Path("Component.tsx")) == "typescript"
    assert detect_language(Path("Component.jsx")) == "javascript"


def test_detect_language_unknown():
    assert detect_language(Path("data.csv")) is None
    assert detect_language(Path("Makefile")) is None


# ── Python Parsing ────────────────────────────────────────────────

def test_parse_python_function():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write('def hello(name: str) -> str:\n    """Say hello."""\n    return f"Hello {name}"\n')
        f.flush()
        result = parse_file(Path(f.name))

    assert result is not None
    assert result.language == "python"
    assert len(result.symbols) >= 1

    func = next(s for s in result.symbols if s.name == "hello")
    assert func.kind == "function"
    assert func.line == 1
    assert "def hello" in func.signature


def test_parse_python_signature_survives_unicode_prefix():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8") as f:
        f.write(
            '"""Shared singleton instances — one connection per process."""\n\n'
            "def get_embedder():\n"
            "    return None\n"
        )
        f.flush()
        result = parse_file(Path(f.name))

    assert result is not None
    func = next(s for s in result.symbols if s.name == "get_embedder")
    assert func.signature == "def get_embedder():"
    assert func.body.startswith("def get_embedder():")


def test_parse_python_class():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(
            "class Dog:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
            "\n"
            "    def bark(self):\n"
            '        return f"{self.name} says woof"\n'
        )
        f.flush()
        result = parse_file(Path(f.name))

    assert result is not None
    names = [s.name for s in result.symbols]
    assert "Dog" in names
    # Methods should also be extracted
    assert "__init__" in names or "bark" in names


def test_parse_python_imports():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(
            "import os\n"
            "from pathlib import Path\n"
            "from typing import Optional\n"
            "\n"
            "def foo(): pass\n"
        )
        f.flush()
        result = parse_file(Path(f.name))

    assert result is not None
    assert len(result.imports) >= 2


def test_parse_python_calls():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(
            "def outer():\n"
            "    inner()\n"
            "    print('hello')\n"
            "\n"
            "def inner():\n"
            "    pass\n"
        )
        f.flush()
        result = parse_file(Path(f.name))

    assert result is not None
    assert len(result.calls) >= 1
    callee_names = [c.callee for c in result.calls]
    assert "inner" in callee_names or "print" in callee_names


# ── Rust Parsing ──────────────────────────────────────────────────

def test_parse_rust_function():
    with tempfile.NamedTemporaryFile(suffix=".rs", mode="w", delete=False) as f:
        f.write("pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n")
        f.flush()
        result = parse_file(Path(f.name))

    assert result is not None
    assert result.language == "rust"
    func = next(s for s in result.symbols if s.name == "add")
    assert func.kind == "function"


def test_parse_rust_struct_and_impl():
    with tempfile.NamedTemporaryFile(suffix=".rs", mode="w", delete=False) as f:
        f.write(
            "pub struct Point {\n"
            "    pub x: f64,\n"
            "    pub y: f64,\n"
            "}\n"
            "\n"
            "impl Point {\n"
            "    pub fn new(x: f64, y: f64) -> Self {\n"
            "        Point { x, y }\n"
            "    }\n"
            "\n"
            "    pub fn distance(&self) -> f64 {\n"
            "        (self.x * self.x + self.y * self.y).sqrt()\n"
            "    }\n"
            "}\n"
        )
        f.flush()
        result = parse_file(Path(f.name))

    assert result is not None
    names = [s.name for s in result.symbols]
    assert "Point" in names
    # Qualified method names: Type::method
    assert "Point::new" in names or "new" in names


# ── TypeScript Parsing ────────────────────────────────────────────

def test_parse_typescript():
    with tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False) as f:
        f.write(
            "interface User {\n"
            "  name: string;\n"
            "  age: number;\n"
            "}\n"
            "\n"
            "function greet(user: User): string {\n"
            '  return `Hello ${user.name}`;\n'
            "}\n"
        )
        f.flush()
        result = parse_file(Path(f.name))

    assert result is not None
    assert result.language == "typescript"
    names = [s.name for s in result.symbols]
    assert "greet" in names


# ── JavaScript Parsing ────────────────────────────────────────────

def test_parse_javascript():
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(
            "function fetchData(url) {\n"
            "  return fetch(url).then(r => r.json());\n"
            "}\n"
            "\n"
            "class ApiClient {\n"
            "  constructor(baseUrl) {\n"
            "    this.baseUrl = baseUrl;\n"
            "  }\n"
            "}\n"
        )
        f.flush()
        result = parse_file(Path(f.name))

    assert result is not None
    names = [s.name for s in result.symbols]
    assert "fetchData" in names
    assert "ApiClient" in names


# ── Directory Parsing ─────────────────────────────────────────────

def test_parse_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "main.py").write_text("def main(): pass\n")
        (root / "lib.py").write_text("def helper(): pass\n")
        (root / "data.csv").write_text("a,b,c\n")  # should be skipped
        (root / "node_modules").mkdir()
        (root / "node_modules" / "junk.js").write_text("function x(){}")  # excluded

        results = parse_directory(root)

    assert len(results) == 2  # only .py files, not .csv or node_modules
    all_symbols = [s.name for r in results for s in r.symbols]
    assert "main" in all_symbols
    assert "helper" in all_symbols


def test_parse_directory_skips_symlinks():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "real.py").write_text("def real(): pass\n")
        (root / "link.py").symlink_to(root / "real.py")

        results = parse_directory(root, follow_symlinks=False)

    # Should only get real.py, not the symlink
    files = [r.file for r in results]
    assert len(files) == 1


# ── Dependency Extraction ─────────────────────────────────────────

def test_extract_deps_pyproject():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "testpkg"\nversion = "1.0"\n'
            'dependencies = ["requests>=2.0", "click"]\n'
        )

        deps = extract_deps_from_directory(root)

    assert len(deps) == 1
    pkg = deps[0]
    assert pkg.project_name == "testpkg"
    dep_names = [d.name for d in pkg.dependencies]
    assert "requests" in dep_names
    assert "click" in dep_names


def test_extract_deps_package_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "package.json").write_text(
            '{"name": "myapp", "version": "2.0.0", '
            '"dependencies": {"express": "^4.18"}, '
            '"devDependencies": {"jest": "^29.0"}}'
        )

        deps = extract_deps_from_directory(root)

    assert len(deps) == 1
    dep_names = [d.name for d in deps[0].dependencies]
    assert "express" in dep_names
    assert "jest" in dep_names
    jest = next(d for d in deps[0].dependencies if d.name == "jest")
    assert jest.kind == "dev"


def test_extract_deps_cargo_toml():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "Cargo.toml").write_text(
            '[package]\nname = "mylib"\nversion = "0.1.0"\n\n'
            '[dependencies]\nserde = { version = "1.0", features = ["derive"] }\n'
            'tokio = "1.0"\n\n'
            '[dev-dependencies]\ncriterion = "0.5"\n'
        )

        deps = extract_deps_from_directory(root)

    assert len(deps) == 1
    dep_names = [d.name for d in deps[0].dependencies]
    assert "serde" in dep_names
    assert "tokio" in dep_names
    assert "criterion" in dep_names
    serde = next(d for d in deps[0].dependencies if d.name == "serde")
    assert "derive" in serde.features


# ── Context Doc Scanner ───────────────────────────────────────────

def test_scan_context_docs():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "README.md").write_text("# My Project\n\nThis does stuff.\n")
        (root / "CLAUDE.md").write_text("# Instructions\n\n## Build\n\n`make build`\n")
        (root / "CODEX.md").write_text("# Codex\n\nCodex-specific guidance.\n")
        (root / "GEMINI.md").write_text("# Gemini\n\nGemini-specific guidance.\n")
        (root / "TLDROADMAP.md").write_text("# TLDROADMAP\n\n## North Star\n\nRoadmap intent.\n")
        (root / "TLDRNOTES.md").write_text("# Notes\n\nTactical caveat.\n")
        (root / ".tldr" / "roadmap").mkdir(parents=True)
        (root / ".tldr" / "roadmap" / "TLDRPLANS.md").write_text("# TLDRPLANS\n\nCurrent digest.\n")
        (root / "random.md").write_text("# Random\n\nNot a context doc.\n")
        (root / "pyproject.toml").write_text('[project]\nname = "x"\n')

        docs = scan_context_docs(root)

    kinds = [d.kind for d in docs]
    assert "readme" in kinds
    assert "claude" in kinds
    assert "codex" in kinds
    assert "gemini" in kinds
    assert "roadmap" in kinds
    assert "notes" in kinds
    assert "plans" in kinds
    # random.md should NOT be included (not in CONTEXT_DOC_NAMES)
    assert len(docs) == 7


def test_parse_markdown_sections():
    content = "# Title\n\nIntro text.\n\n## Setup\n\nDo this.\n\n## Usage\n\nRun that.\n"
    sections = _parse_markdown_sections(content)

    assert len(sections) == 3
    assert sections[0]["heading"] == "Title"
    assert sections[1]["heading"] == "Setup"
    assert "Do this" in sections[1]["content"]
    assert sections[2]["heading"] == "Usage"


# ── Edge Cases ────────────────────────────────────────────────────

def test_parse_empty_file():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("")
        f.flush()
        result = parse_file(Path(f.name))

    # Empty file has no symbols — parser may return None or empty
    assert result is None or len(result.symbols) == 0


def test_parse_binary_file():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="wb", delete=False) as f:
        f.write(b"\x00\x01\x02\x03\xff\xfe")
        f.flush()
        result = parse_file(Path(f.name))

    # Should not crash — errors="replace" handles it
    assert result is not None or result is None  # just don't crash


def test_parse_nonexistent_file():
    result = parse_file(Path("/nonexistent/file.py"))
    assert result is None


def test_parse_unsupported_extension():
    with tempfile.NamedTemporaryFile(suffix=".xyz", mode="w", delete=False) as f:
        f.write("not code")
        f.flush()
        result = parse_file(Path(f.name))

    assert result is None
