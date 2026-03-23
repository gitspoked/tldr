"""Tests for router-friendly coding tools."""

from pathlib import Path

from tldreadme import coding_tools
from tldreadme.asts import ParseResult, Symbol


def test_router_contract_surface_is_frozen():
    assert coding_tools.ROUTER_TOP_LEVEL_SEQUENCE == (
        "repo_next_action",
        "repo_lookup",
        "change_plan",
        "verify_change",
    )
    assert coding_tools.PREFERRED_RESULT_KEYS == (
        "tool_contract_version",
        "summary",
        "confidence",
        "evidence",
        "recommended_next_action",
        "verification_commands",
        "fallback_used",
    )


def test_test_map_finds_targeted_python_tests(tmp_path):
    root = tmp_path
    source = root / "src" / "service.py"
    test_file = root / "tests" / "test_service.py"

    source.parent.mkdir(parents=True)
    test_file.parent.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    source.write_text("def service():\n    return 1\n", encoding="utf-8")
    test_file.write_text(
        "from src.service import service\n\n\ndef test_service():\n    assert service() == 1\n",
        encoding="utf-8",
    )

    result = coding_tools.test_map(path="src/service.py", root=str(root))

    assert result["project_type"] == "python"
    assert result["source_files"] == [str(source.resolve())]
    assert str(test_file.resolve()) in result["test_files"]
    assert result["verification_commands"][0].startswith("python -m pytest -q ")
    assert str(test_file.resolve()) in result["verification_commands"][0]
    assert "compileall" in result["verification_commands"][1]
    assert result["evidence"]
    assert result["fallback_used"] == []
    assert result["next_best_tool"] == "edit_context"


def test_edit_context_merges_semantic_tests_and_work_items(monkeypatch, tmp_path):
    root = tmp_path
    source = root / "app.py"
    source.write_text("def sample():\n    return 1\n", encoding="utf-8")

    parsed = ParseResult(
        file=str(source),
        language="python",
        symbols=[
            Symbol(
                name="sample",
                kind="function",
                file=str(source),
                line=1,
                end_line=2,
                body="def sample():\n    return 1\n",
                signature="sample()",
                language="python",
            )
        ],
        imports=[],
        calls=[],
        raw_source=source.read_text(encoding="utf-8"),
        line_count=2,
    )

    monkeypatch.setattr(coding_tools, "parse_file", lambda _path: parsed)
    monkeypatch.setattr(
        coding_tools,
        "_semantic_context",
        lambda *_args, **_kwargs: {"token": "sample", "hover": "hover text"},
    )
    monkeypatch.setattr(
        coding_tools,
        "_knowledge_for_symbol",
        lambda *_args, **_kwargs: {"definition": {"file": str(source), "line": 1}, "callers": [{"name": "caller"}]},
    )
    monkeypatch.setattr(coding_tools, "_similar_for_symbol", lambda _symbol: [{"symbol": "sample_variant"}])
    monkeypatch.setattr(
        coding_tools,
        "test_map",
        lambda **_kwargs: {
            "test_files": [str(root / "tests" / "test_app.py")],
            "verification_commands": ["python -m pytest -q tests/test_app.py"],
            "summary": "Found tests",
        },
    )
    monkeypatch.setattr(
        coding_tools,
        "_current_work_context",
        lambda *_args, **_kwargs: {
            "matching_tasks": [
                {
                    "title": "Patch sample",
                    "next_step": "Patch the guard clause first.",
                    "verification_commands": ["python -m pytest -q tests/test_app.py"],
                    "acceptance_criteria": ["No regression in sample()."],
                }
            ]
        },
    )

    result = coding_tools.edit_context("app.py", 1, root=str(root))

    assert result["enclosing_symbol"]["name"] == "sample"
    assert result["semantic"]["hover"] == "hover text"
    assert result["snippet"]["focus_line"] == "def sample():"
    assert result["recommended_next_action"] == "Patch the guard clause first."
    assert result["verification_commands"] == ["python -m pytest -q tests/test_app.py"]
    assert result["evidence"]
    assert result["fallback_used"] == []
    assert result["next_best_tool"] == "change_plan"


def test_change_plan_returns_candidate_files_risks_and_verification(monkeypatch, tmp_path):
    root = tmp_path
    source = root / "src" / "service.py"
    helper = root / "src" / "helper.py"
    test_file = root / "tests" / "test_service.py"

    source.parent.mkdir(parents=True)
    test_file.parent.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    source.write_text("def service():\n    return 'ok'\n", encoding="utf-8")
    helper.write_text("def helper():\n    return 'ok'\n", encoding="utf-8")
    test_file.write_text("from src.service import service\n", encoding="utf-8")

    monkeypatch.setattr(
        coding_tools,
        "_knowledge_for_symbol",
        lambda *_args, **_kwargs: {
            "definition": {"file": "src/service.py", "line": 1},
            "callers": [{"name": "orchestrate"}],
        },
    )
    monkeypatch.setattr(
        coding_tools,
        "_impact_for_symbol",
        lambda *_args, **_kwargs: {"severity": "medium", "warning": "Shared entry point."},
    )
    monkeypatch.setattr(
        coding_tools,
        "_discover_for_goal",
        lambda *_args, **_kwargs: {
            "merged": [
                {"file": "src/helper.py", "symbol": "helper"},
                {"file": "src/service.py", "symbol": "service"},
            ]
        },
    )
    monkeypatch.setattr(
        coding_tools,
        "_current_work_context",
        lambda *_args, **_kwargs: {
            "matching_tasks": [
                {
                    "title": "Add fallback handling",
                    "next_step": "Update the failing branch coverage.",
                    "verification_commands": ["python -m pytest -q tests/test_service.py"],
                    "acceptance_criteria": ["Cover the negative path."],
                }
            ]
        },
    )

    result = coding_tools.change_plan(
        "Add fallback handling",
        path="src/service.py",
        symbol="service",
        root=str(root),
    )

    assert result["candidate_files"][0] == str(source.resolve())
    assert str(helper.resolve()) in result["candidate_files"]
    assert "service" in result["likely_symbols"]
    assert "helper" in result["likely_symbols"]
    assert result["risks"][0] == "Shared entry point."
    assert any("Impact severity is medium" in risk for risk in result["risks"])
    assert "Cover the negative path." in result["acceptance_criteria"]
    assert result["recommended_next_action"].startswith("Call edit_context on ")
    assert result["evidence"]
    assert result["fallback_used"] == []
    assert result["next_best_tool"] == "edit_context"


def test_repo_lookup_dispatches_to_edit_context(monkeypatch, tmp_path):
    root = tmp_path
    source = root / "app.py"
    source.write_text("def sample():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(
        coding_tools,
        "edit_context",
        lambda path, line, column=None, root=".": {
            "path": path,
            "line": line,
            "column": column,
            "summary": "Edit context ready for sample.",
            "confidence": 0.92,
            "evidence": ["focus line: def sample():"],
            "verification_commands": ["python -m pytest -q tests/test_app.py"],
            "recommended_next_action": "Patch sample, then run the targeted test.",
            "fallback_used": [],
            "next_best_tool": "change_plan",
        },
    )

    result = coding_tools.repo_lookup(path="app.py", line=1, column=3, root=str(root))

    assert result["lookup_mode"] == "edit"
    assert result["dispatched_tool"] == "edit_context"
    assert result["specialist_tool"] == "edit_context"
    assert result["summary"].startswith("Repo lookup used `edit_context`.")
    assert result["next_best_tool"] == "change_plan"


def test_repo_lookup_dispatches_to_impact_for_change_risk_queries(monkeypatch, tmp_path):
    root = tmp_path

    monkeypatch.setattr(
        coding_tools,
        "_impact_for_symbol",
        lambda *_args, **_kwargs: {
            "name": "parse_file",
            "severity": "high",
            "warning": "Load-bearing symbol — 28 references across 9 files",
            "reference_source": "lsp",
            "total_references": 28,
            "files_affected": ["tldreadme/parser.py", "tldreadme/asts.py"],
            "transitive_dependents": ["pipeline.run"],
        },
    )
    monkeypatch.setattr(
        coding_tools,
        "test_map",
        lambda **_kwargs: {"verification_commands": ["python -m pytest -q tests/test_parser.py"]},
    )

    result = coding_tools.repo_lookup(
        query="what breaks if I change parse_file?",
        symbol="parse_file",
        root=str(root),
    )

    assert result["lookup_mode"] == "impact"
    assert result["dispatched_tool"] == "impact"
    assert result["impact"]["severity"] == "high"
    assert result["verification_commands"] == ["python -m pytest -q tests/test_parser.py"]
    assert result["next_best_tool"] == "change_plan"


def test_repo_lookup_dispatches_to_search_and_maps_specialist_follow_up(monkeypatch, tmp_path):
    root = tmp_path

    monkeypatch.setattr(
        coding_tools,
        "search_context",
        lambda *_args, **_kwargs: {
            "summary": "Found 2 ranked context hit(s) for `parser guard` across code, docs surfaces.",
            "confidence": 0.84,
            "evidence": ["code: tldreadme/parser.py:12"],
            "verification_commands": ["python -m pytest -q tests/test_parser.py"],
            "recommended_next_action": "Inspect tldreadme/parser.py:12 with edit_context before changing code.",
            "fallback_used": [],
            "next_best_tool": "edit_context",
            "ranked_hits": [{"source_type": "code", "path": "tldreadme/parser.py", "line": 12}],
        },
    )

    result = coding_tools.repo_lookup(query="parser guard", root=str(root))

    assert result["lookup_mode"] == "search"
    assert result["dispatched_tool"] == "search_context"
    assert result["specialist_next_tool"] == "edit_context"
    assert result["next_best_tool"] == "repo_lookup"


def test_diagnostics_here_returns_likely_fix_area(monkeypatch, tmp_path):
    root = tmp_path
    source = root / "app.py"
    source.write_text("def sample():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(
        coding_tools,
        "_diagnostics_for_path",
        lambda *_args, **_kwargs: {
            "diagnostic_source": "textDocument/diagnostic",
            "diagnostics": [
                {
                    "path": str(source),
                    "line": 1,
                    "column": 5,
                    "end_line": 1,
                    "end_column": 11,
                    "severity": "warning",
                    "message": "possible issue",
                }
            ],
            "document_symbols": [
                {
                    "name": "sample",
                    "line": 1,
                    "column": 1,
                    "end_line": 2,
                    "end_column": 1,
                }
            ],
        },
    )
    monkeypatch.setattr(
        coding_tools,
        "test_map",
        lambda **_kwargs: {
            "verification_commands": ["python -m pytest -q tests/test_app.py"],
            "test_files": [str(root / "tests" / "test_app.py")],
        },
    )

    result = coding_tools.diagnostics_here("app.py", line=1, column=5, root=str(root))

    assert result["diagnostics"][0]["message"] == "possible issue"
    assert result["likely_fix_area"]["symbol"] == "sample"
    assert result["impacted_symbols"] == ["sample"]
    assert result["verification_commands"] == ["python -m pytest -q tests/test_app.py"]
    assert result["fallback_used"] == []


def test_pattern_search_prefers_reusable_matches(monkeypatch, tmp_path):
    root = tmp_path
    source = root / "src" / "service.py"
    source.parent.mkdir(parents=True)
    source.write_text("def service():\n    return helper()\n", encoding="utf-8")

    monkeypatch.setattr(
        coding_tools,
        "_similar_for_symbol",
        lambda _query: [
            {
                "symbol": "helper",
                "kind": "function",
                "file": str(source),
                "line": 1,
                "code": "def helper():\n    return 1\n",
                "score": 0.93,
            }
        ],
    )
    monkeypatch.setattr(
        coding_tools,
        "_discover_for_goal",
        lambda *_args, **_kwargs: {"merged": [{"source": "semantic", "file": str(source), "line": 1, "symbol": "helper", "score": 0.8}]},
    )
    monkeypatch.setattr(
        coding_tools,
        "rg_search",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        coding_tools,
        "test_map",
        lambda **_kwargs: {"verification_commands": ["python -m pytest -q tests/test_service.py"], "test_files": []},
    )

    result = coding_tools.pattern_search(query="fallback helper", root=str(root))

    assert result["patterns"][0]["symbol"] == "helper"
    assert result["ranked_reusable_snippets"][0]["source"] == "semantic"
    assert result["use_instead"]["symbol"] == "helper"
    assert result["recommended_next_action"].startswith("Reuse the implementation pattern")
    assert "text_pattern_matches_unavailable" in result["fallback_used"]


def test_verify_change_uses_task_and_command_results(monkeypatch, tmp_path):
    root = tmp_path
    source = root / "app.py"
    source.write_text("def sample():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(
        coding_tools,
        "_find_task_context",
        lambda *_args, **_kwargs: {
            "id": "task-1",
            "plan_id": "plan-1",
            "plan_title": "Example",
            "status": "done",
            "files": [str(source)],
            "verification_commands": ["python -m pytest -q tests/test_app.py"],
            "acceptance_criteria": ["sample() stays green"],
            "evidence": ["pytest passed"],
        },
    )
    monkeypatch.setattr(
        coding_tools,
        "test_map",
        lambda **_kwargs: {
            "verification_commands": ["python -m pytest -q tests/test_app.py"],
            "test_files": [str(root / "tests" / "test_app.py")],
            "heuristics": ["filename match: tests/test_app.py"],
        },
    )
    monkeypatch.setattr(
        coding_tools,
        "_run_verification_commands",
        lambda *_args, **_kwargs: [
            {
                "command": "python -m pytest -q tests/test_app.py",
                "passed": True,
                "exit_code": 0,
                "stdout_tail": "1 passed",
                "stderr_tail": "",
            }
        ],
    )

    result = coding_tools.verify_change(
        files=["app.py"],
        task_id="task-1",
        root=str(root),
        run_commands=True,
    )

    assert result["pass_fail_summary"]["status"] == "passed"
    assert result["acceptance_criteria_satisfied"] is True
    assert result["missing_evidence"] == []
    assert result["what_to_run"] == ["python -m pytest -q tests/test_app.py"]
    assert "command passed: python -m pytest -q tests/test_app.py" in result["evidence"]


def test_scan_context_reports_repo_surfaces(monkeypatch, tmp_path):
    root = tmp_path
    code_file = root / "src" / "service.py"
    test_file = root / "tests" / "test_service.py"
    generated = root / ".claude" / "TLDR.md"

    code_file.parent.mkdir(parents=True)
    test_file.parent.mkdir(parents=True)
    generated.parent.mkdir(parents=True)
    code_file.write_text("def service():\n    return 1\n", encoding="utf-8")
    test_file.write_text("def test_service():\n    assert True\n", encoding="utf-8")
    generated.write_text("# TLDR\n", encoding="utf-8")

    monkeypatch.setattr(
        coding_tools,
        "_context_docs_for_root",
        lambda _root: [type("Doc", (), {"file": str(root / "README.md")})()],
    )
    monkeypatch.setattr(coding_tools, "_code_files", lambda _root: [code_file])
    monkeypatch.setattr(coding_tools, "_test_files", lambda _root: [test_file])
    monkeypatch.setattr(coding_tools, "_generated_context_files", lambda _root: [generated])
    monkeypatch.setattr(
        coding_tools,
        "_workboard_snapshot",
        lambda _root: {
            "listing": {"plans": [{"id": "plan-1", "title": "Plan", "status": "in_progress"}]},
            "current": {
                "summary": {"title": "Plan", "status": "in_progress"},
                "plan": {"phases": [{"tasks": [{"verification_commands": ["python -m pytest -q"]}]}]},
            },
        },
    )
    monkeypatch.setattr(
        coding_tools,
        "_children_snapshot",
        lambda _root: {
            "children": [
                {
                    "path": "redocoder",
                    "status": "unknown",
                    "manifests": ["package.json"],
                    "context_docs": ["README.md"],
                    "has_git": False,
                    "code_file_count": 12,
                    "note": None,
                }
            ]
        },
    )
    monkeypatch.setattr(
        coding_tools,
        "_recent_summary",
        lambda *_args, **_kwargs: {
            "since": "2026-03-22T00:00:00+00:00",
            "counts": {"commits": 1, "working_tree_changes": 1},
            "commits": [{"subject": "Add tool"}],
            "working_tree": [{"path": "tldreadme/cli.py"}],
        },
    )

    result = coding_tools.scan_context(root=str(root))

    assert result["source_counts"]["code"] == 1
    assert result["source_counts"]["docs"] == 1
    assert result["source_counts"]["workboard"] == 2
    assert result["source_counts"]["children"] == 1
    assert result["children"]["unknown_count"] == 1
    assert result["next_best_tool"] == "search_context"
    assert result["verification_commands"] == ["python -m pytest -q"]
    assert result["fallback_used"] == []


def test_search_context_ranks_across_surfaces(monkeypatch, tmp_path):
    root = tmp_path
    source = root / "src" / "service.py"
    source.parent.mkdir(parents=True)
    source.write_text("def service():\n    return helper()\n", encoding="utf-8")

    monkeypatch.setattr(
        coding_tools,
        "_code_context_hits",
        lambda *_args, **_kwargs: [
            {
                "source_type": "code",
                "path": str(source),
                "line": 1,
                "score": 1.0,
                "snippet": "def service():",
                "why_matched": "Exact text search hit in source code.",
                "symbol": None,
            }
        ],
    )
    monkeypatch.setattr(
        coding_tools,
        "_doc_context_hits",
        lambda *_args, **_kwargs: [
            {
                "source_type": "docs",
                "path": str(root / "README.md"),
                "line": 4,
                "score": 0.9,
                "snippet": "Service overview",
                "why_matched": "Matched README",
                "symbol": None,
            }
        ],
    )
    monkeypatch.setattr(
        coding_tools,
        "_workboard_context_hits",
        lambda *_args, **_kwargs: [
            {
                "source_type": "workboard",
                "path": "repo://task/plan-1/task-1",
                "line": None,
                "score": 0.95,
                "snippet": "Patch service",
                "why_matched": "Matched task",
                "symbol": None,
                "task_id": "task-1",
            }
        ],
    )
    monkeypatch.setattr(coding_tools, "_children_snapshot", lambda *_args, **_kwargs: {"children": []})
    monkeypatch.setattr(coding_tools, "_recent_context_hits", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        coding_tools,
        "test_map",
        lambda **_kwargs: {"verification_commands": ["python -m pytest -q tests/test_service.py"]},
    )

    result = coding_tools.search_context("service helper", root=str(root))

    assert result["ranked_hits"][0]["source_type"] == "code"
    assert "code" in result["grouped_hits"]
    assert "docs" in result["grouped_hits"]
    assert "workboard" in result["grouped_hits"]
    assert result["verification_commands"] == ["python -m pytest -q tests/test_service.py"]
    assert result["next_best_tool"] == "edit_context"
    assert "recent_hits_unavailable" in result["fallback_used"]


def test_search_context_can_surface_child_projects(monkeypatch, tmp_path):
    root = tmp_path

    monkeypatch.setattr(coding_tools, "_code_context_hits", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(coding_tools, "_doc_context_hits", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(coding_tools, "_workboard_context_hits", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(coding_tools, "_recent_context_hits", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        coding_tools,
        "_children_snapshot",
        lambda *_args, **_kwargs: {
            "children": [
                {
                    "path": "redocoder",
                    "status": "unknown",
                    "manifests": ["package.json"],
                    "context_docs": ["README.md"],
                    "has_git": False,
                    "code_file_count": 12,
                    "note": "Imported subtree",
                }
            ]
        },
    )

    result = coding_tools.search_context("redocoder package", root=str(root))

    assert result["ranked_hits"][0]["source_type"] == "children"
    assert result["next_best_tool"] == "scan_context"
    assert "children" in result["grouped_hits"]


def test_repo_next_action_prioritizes_overlap(monkeypatch, tmp_path):
    root = tmp_path

    monkeypatch.setattr(
        coding_tools,
        "_workboard_snapshot",
        lambda _root: {
            "current": {
                "session": {"current_plan_id": "plan-1", "current_task_id": "task-1", "next_action": "Run verification"},
                "plan": {
                    "id": "plan-1",
                    "title": "Parser cleanup",
                    "goal": "Stabilize parser behavior",
                    "phases": [{"name": "Build", "tasks": [{"id": "task-1", "title": "Patch parser", "status": "in_progress", "verification_commands": ["python -m pytest -q"]}]}],
                },
                "overlaps": [{"session_id": "codex-2", "actor_id": "codex", "shared_files": ["tldreadme/parser.py"], "shared_symbols": ["parse_file"]}],
            }
        },
    )
    monkeypatch.setattr(coding_tools, "_children_snapshot", lambda _root: {"children": []})
    monkeypatch.setattr(coding_tools, "_recent_summary", lambda *_args, **_kwargs: None)

    result = coding_tools.repo_next_action(root=str(root))

    assert result["suggested_tool"] == "repo_lookup"
    assert result["suggested_arguments"]["path"] == "tldreadme/parser.py"
    assert result["recommended_next_action"].startswith("Active overlap")
    assert result["next_best_tool"] == "repo_lookup"


def test_repo_next_action_prioritizes_unknown_child_when_no_overlap(monkeypatch, tmp_path):
    root = tmp_path

    monkeypatch.setattr(coding_tools, "_workboard_snapshot", lambda _root: None)
    monkeypatch.setattr(
        coding_tools,
        "_children_snapshot",
        lambda _root: {
            "children": [
                {"path": "redocoder", "status": "unknown", "manifests": ["package.json"], "context_docs": ["README.md"], "note": None}
            ]
        },
    )
    monkeypatch.setattr(coding_tools, "_recent_summary", lambda *_args, **_kwargs: None)

    result = coding_tools.repo_next_action(root=str(root))

    assert result["suggested_tool"] == "repo_lookup"
    assert result["suggested_arguments"]["path"] == "redocoder"
    assert result["unknown_children"][0]["path"] == "redocoder"


def test_top_level_router_tools_return_normalized_contract(monkeypatch, tmp_path):
    root = tmp_path
    required = set(coding_tools.PREFERRED_RESULT_KEYS) | {"next_best_tool"}

    monkeypatch.setattr(coding_tools, "_workboard_snapshot", lambda _root: None)
    monkeypatch.setattr(coding_tools, "_children_snapshot", lambda _root: {"children": []})
    monkeypatch.setattr(coding_tools, "_recent_summary", lambda *_args, **_kwargs: None)
    repo_next = coding_tools.repo_next_action(root=str(root))

    monkeypatch.setattr(
        coding_tools,
        "search_context",
        lambda *_args, **_kwargs: {
            "tool_contract_version": coding_tools.ROUTER_CONTRACT_VERSION,
            "summary": "Found 1 ranked context hit.",
            "confidence": 0.8,
            "evidence": ["code: app.py:1"],
            "recommended_next_action": "Inspect the matching file.",
            "verification_commands": ["python -m pytest -q"],
            "fallback_used": [],
            "next_best_tool": "edit_context",
        },
    )
    repo_lookup = coding_tools.repo_lookup(query="app", root=str(root))

    monkeypatch.setattr(
        coding_tools,
        "_discover_for_goal",
        lambda *_args, **_kwargs: {"merged": []},
    )
    monkeypatch.setattr(coding_tools, "_impact_for_symbol", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(coding_tools, "_current_work_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        coding_tools,
        "test_map",
        lambda **_kwargs: {
            "test_files": [],
            "verification_commands": ["python -m pytest -q"],
        },
    )
    change_plan = coding_tools.change_plan("Investigate router contract", root=str(root))

    monkeypatch.setattr(
        coding_tools,
        "_find_task_context",
        lambda *_args, **_kwargs: None,
    )
    verify_change = coding_tools.verify_change(files=["app.py"], root=str(root), run_commands=False)

    for payload in [repo_next, repo_lookup, change_plan, verify_change]:
        assert required <= set(payload)
        assert payload["tool_contract_version"] == coding_tools.ROUTER_CONTRACT_VERSION
