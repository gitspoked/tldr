"""Tests for generated TLDR context files."""

from pathlib import Path

from tldreadme import generator, pipeline


def test_generate_claude_md_uses_resolved_root_for_relative_paths(monkeypatch, tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    class FakeGraphResult:
        def __init__(self, rows):
            self.result_set = rows

    class FakeGraph:
        def __init__(self):
            self.last_root = None

        def query(self, _query, params):
            self.last_root = params["root"]
            return FakeGraphResult(
                [
                    [str(repo / "tldreadme")],
                    [str(repo / "tests")],
                ]
            )

    class FakeGrapher:
        def __init__(self):
            self.graph = FakeGraph()

        def get_module_symbols(self, module_path):
            return [
                {
                    "name": "sample",
                    "kind": "function",
                    "signature": "sample()",
                    "file": f"{module_path}/sample.py",
                    "line": 1,
                }
            ]

    grapher = FakeGrapher()
    monkeypatch.setattr(generator, "CodeGrapher", lambda: grapher)
    monkeypatch.setattr(generator.rag, "tldr", lambda module_path: f"Summary for {Path(module_path).name}")

    output_path = generator.generate_claude_md(Path("."), output_dir=".claude")
    payload = output_path.read_text(encoding="utf-8")
    context = (repo / ".claude" / "TLDR_CONTEXT.md").read_text(encoding="utf-8")

    assert grapher.graph.last_root == str(repo.resolve())
    assert output_path == repo / ".claude" / "TLDR.md"
    assert payload.startswith("# demo")
    assert "- **`tldreadme/`**" in payload
    assert "Summary for tldreadme" in payload
    assert "## tldreadme" in context
    assert "`sample`" in context


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
        lambda root, output_dir=".claude": generate_calls.append(root) or (resolved_repo / output_dir / "TLDR.md"),
    )

    pipeline.run_init(Path("."), output_dir=".claude")

    assert parse_calls == [resolved_repo]
    assert hot_index_calls == [resolved_repo]
    assert generate_calls == [resolved_repo]
