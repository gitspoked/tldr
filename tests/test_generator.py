"""Tests for generated TLDR context files."""

from pathlib import Path

from tldreadme import generator, pipeline
from tldreadme.asts import ParseResult, Symbol


def _parse_result(path: Path, *symbols: Symbol) -> ParseResult:
    source = "\n".join(symbol.signature for symbol in symbols) + "\n"
    return ParseResult(
        file=str(path),
        language="python",
        symbols=list(symbols),
        imports=[],
        calls=[],
        raw_source=source,
        line_count=max((symbol.end_line for symbol in symbols), default=1),
    )


def _symbol(
    name: str,
    *,
    kind: str,
    file: str,
    line: int,
    signature: str,
    parent: str | None = None,
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file=file,
        line=line,
        end_line=line + 1,
        body=signature,
        signature=signature,
        parent=parent,
        language="python",
    )


def test_generate_claude_md_ranks_production_before_tests_and_groups_sections(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    output_dir = ".claude"

    parse_results = [
        _parse_result(
            repo / "tests" / "test_service.py",
            _symbol(
                "test_service",
                kind="function",
                file=str(repo / "tests" / "test_service.py"),
                line=1,
                signature="def test_service():",
            ),
        ),
        _parse_result(
            repo / "demo" / "service.py",
            _symbol(
                "serve",
                kind="function",
                file=str(repo / "demo" / "service.py"),
                line=1,
                signature="def serve():",
            ),
            _symbol(
                "Service",
                kind="class",
                file=str(repo / "demo" / "service.py"),
                line=5,
                signature="class Service:",
            ),
        ),
    ]

    output_path = generator.generate_claude_md(repo, output_dir=output_dir, parse_results=parse_results)
    payload = output_path.read_text(encoding="utf-8")
    context = (repo / output_dir / "TLDR_CONTEXT.md").read_text(encoding="utf-8")

    assert output_path == repo / output_dir / "TLDR.md"
    assert payload.startswith("# demo")
    assert "Primary implementation areas: `demo/`." in payload
    assert "Validation and examples live in: `tests/`." in payload
    assert payload.index("## Know-How") < payload.index("## Know-When / Examples")
    assert payload.index("**`demo/`**") < payload.index("**`tests/`**")
    assert "Implementation area with 1 file(s) and 2 symbols" in payload
    assert "Validation and example coverage for 1 file(s) with 1 symbols" in payload
    assert "## Know-How / Production" in context
    assert "### demo/" in context
    assert "## Know-When / Examples" in context
    assert "### tests/" in context
    assert "`demo/service.py:1`" in context


def test_generate_claude_md_filters_nested_local_helpers_from_context(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()

    parse_results = [
        _parse_result(
            repo / "demo" / "service.py",
            _symbol(
                "Service",
                kind="class",
                file=str(repo / "demo" / "service.py"),
                line=1,
                signature="class Service:",
            ),
            _symbol(
                "run",
                kind="function",
                file=str(repo / "demo" / "service.py"),
                line=3,
                signature="def run(self):",
                parent="Service",
            ),
            _symbol(
                "walk",
                kind="function",
                file=str(repo / "demo" / "service.py"),
                line=7,
                signature="def walk(node):",
                parent="_extract_symbols",
            ),
        ),
    ]

    generator.generate_claude_md(repo, parse_results=parse_results)
    context = (repo / ".claude" / "TLDR_CONTEXT.md").read_text(encoding="utf-8")

    assert "`Service`" in context
    assert "`run`" in context
    assert "`walk`" not in context


def test_generate_claude_md_prioritizes_public_router_symbols_in_context(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()

    parse_results = [
        _parse_result(
            repo / "tldreadme" / "coding_tools.py",
            _symbol(
                "_repo_root",
                kind="function",
                file=str(repo / "tldreadme" / "coding_tools.py"),
                line=1,
                signature="def _repo_root(root=None):",
            ),
            _symbol(
                "repo_lookup",
                kind="function",
                file=str(repo / "tldreadme" / "coding_tools.py"),
                line=10,
                signature="def repo_lookup(query: str):",
            ),
        ),
    ]

    generator.generate_claude_md(repo, parse_results=parse_results)
    context = (repo / ".claude" / "TLDR_CONTEXT.md").read_text(encoding="utf-8")

    assert context.index("`repo_lookup`") < context.index("`_repo_root`")


def test_run_init_resolves_relative_root_before_indexing(monkeypatch, tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    resolved_repo = repo.resolve()
    parse_calls: list[Path] = []
    hot_index_calls: list[Path] = []
    generate_calls: list[Path] = []

    class FakeResult:
        def __init__(self):
            self.symbols = [object()]
            self.calls = []
            self.imports = []
            self.line_count = 1

    fake_results = [FakeResult()]

    class FakeEmbedder:
        def index_chunks(self, _chunks):
            return None

    class FakeGrapher:
        def index_results(self, _results):
            return None

    class FakeHotIndex:
        def __init__(self):
            self.entries = [object()]

        def save(self, _path):
            return None

    monkeypatch.setattr(pipeline, "parse_directory", lambda root: parse_calls.append(root) or fake_results)
    monkeypatch.setattr(pipeline, "CodeEmbedder", lambda: FakeEmbedder())
    monkeypatch.setattr(pipeline, "symbols_to_chunks", lambda results: results)
    monkeypatch.setattr(pipeline, "CodeGrapher", lambda: FakeGrapher())
    monkeypatch.setattr(
        pipeline,
        "build_hot_index",
        lambda root, results: hot_index_calls.append(root) or FakeHotIndex(),
    )
    monkeypatch.setattr(
        pipeline,
        "generate_claude_md",
        lambda root, output_dir=".claude", **kwargs: generate_calls.append(root) or (resolved_repo / output_dir / "TLDR.md"),
    )

    pipeline.run_init(Path("."), output_dir=".claude")

    assert parse_calls == [resolved_repo]
    assert hot_index_calls == [resolved_repo]
    assert generate_calls == [resolved_repo]


def test_context_signatures_are_boundary_truncated_and_markdown_safe():
    signature = "def resolve(value: str | None, fallback: str | None, context: dict[str, str | None], extra: int = 0):"

    formatted = generator._format_signature(signature, max_width=68)

    assert r"\|" in formatted
    assert len(formatted) <= 68
    assert formatted.endswith(" ...")
    assert "context:" in formatted


def test_module_summary_pluralizes_classes():
    snapshot = generator.ModuleSnapshot(
        path="demo",
        relative_path="demo",
        category="know_how",
        files=["demo/service.py"],
        symbols=[
            generator.SymbolSnapshot(
                name="Service",
                kind="class",
                signature="class Service:",
                file="demo/service.py",
                line=1,
                end_line=4,
            ),
            generator.SymbolSnapshot(
                name="Runner",
                kind="class",
                signature="class Runner:",
                file="demo/service.py",
                line=5,
                end_line=8,
            ),
        ],
        key_files=["service"],
        key_entrypoints=["Service"],
    )

    summary = generator._module_summary(snapshot)

    assert "2 classes" in summary
    assert "classs" not in summary
