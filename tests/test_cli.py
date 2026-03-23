"""Tests for CLI entry points."""

from click.testing import CliRunner
from tldreadme.cli import main
from tldreadme import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "TLDREADME" in result.output
    assert "audit" in result.output
    assert "children" in result.output
    assert "plans-capture" in result.output
    assert "whats-next" in result.output
    assert "current-roadmap" in result.output
    assert "whats-next-vibe" not in result.output
    assert "current-vibe-roadmap" not in result.output
    assert "lsp" not in result.output
    assert "lsp-symbols" not in result.output


def test_cli_init_help():
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--help"])
    assert result.exit_code == 0
    assert "DIRECTORY" in result.output


def test_cli_serve_help():
    runner = CliRunner()
    result = runner.invoke(main, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--transport" in result.output
    assert "--port" in result.output
    assert "--tool-profile" in result.output
    assert "SSE" in result.output


def test_cli_watch_help():
    runner = CliRunner()
    result = runner.invoke(main, ["watch", "--help"])
    assert result.exit_code == 0


def test_cli_ask_help():
    runner = CliRunner()
    result = runner.invoke(main, ["ask", "--help"])
    assert result.exit_code == 0
    assert "QUESTION" in result.output


def test_cli_audit_help():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "--help"])
    assert result.exit_code == 0
    assert "deps" in result.output
    assert "code" in result.output
    assert "secrets" in result.output
    assert "llm" in result.output
    assert "all" in result.output
    assert "profiles" in result.output
    assert "kev-refresh" in result.output


def test_cli_audit_deps_help():
    runner = CliRunner()
    result = runner.invoke(main, ["audit", "deps", "--help"])
    assert result.exit_code == 0
    assert "--offline" in result.output
    assert "--download-offline-db" in result.output
    assert "--kev-catalog" in result.output
    assert "--profile" in result.output
    assert "--prefer-snyk" in result.output
    assert "--save-report" in result.output


def test_cli_lsp_help():
    runner = CliRunner()
    result = runner.invoke(main, ["lsp", "--help"])
    assert result.exit_code == 0
    assert "LINE" in result.output
    assert "--column" in result.output


def test_cli_lsp_symbols_help():
    runner = CliRunner()
    result = runner.invoke(main, ["lsp-symbols", "--help"])
    assert result.exit_code == 0
    assert "QUERY" in result.output
    assert "--limit" in result.output


def test_cli_summary_help():
    runner = CliRunner()
    result = runner.invoke(main, ["summary", "--help"])
    assert result.exit_code == 0
    assert "--since" in result.output
    assert "--no-mark-checked" in result.output


def test_cli_plans_capture_help():
    runner = CliRunner()
    result = runner.invoke(main, ["plans-capture", "--help"])
    assert result.exit_code == 0
    assert ".tldr/roadmap/TLDRPLANS.<timestamp>.md" in result.output


def test_cli_whats_next_help():
    runner = CliRunner()
    result = runner.invoke(main, ["whats-next", "--help"])
    assert result.exit_code == 0
    assert "strategic question" in result.output.lower()


def test_cli_current_roadmap_help():
    runner = CliRunner()
    result = runner.invoke(main, ["current-roadmap", "--help"])
    assert result.exit_code == 0
    assert "--no-write" in result.output


def test_cli_children_help():
    runner = CliRunner()
    result = runner.invoke(main, ["children", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "merge" in result.output
    assert "ignore" in result.output


def test_cli_audit_json_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tldreadme.audit.run_audit",
        lambda category, **_kwargs: {
            "category": category,
            "root": str(tmp_path),
            "ok": True,
            "status": "ok",
            "summary": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0, "total": 0},
            "checks": [],
            "scanners": [],
            "policy_profile": None,
            "recommended_next_action": "Run the next audit.",
            "verification_commands": [f"tldr audit {category}"],
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["audit", "deps", str(tmp_path), "--json-output"])

    assert result.exit_code == 0
    assert '"category": "deps"' in result.output


def test_cli_audit_save_report_in_json_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tldreadme.audit.run_audit",
        lambda category, **_kwargs: {
            "category": category,
            "root": str(tmp_path),
            "ok": True,
            "status": "ok",
            "summary": {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0, "total": 0},
            "checks": [],
            "scanners": [],
            "policy_profile": None,
            "recommended_next_action": "Run the next audit.",
            "verification_commands": [f"tldr audit {category}"],
        },
    )
    monkeypatch.setattr(
        "tldreadme.audit.save_audit_report",
        lambda report, **_kwargs: {
            "path": str(tmp_path / "reports" / f"{report['category']}.json"),
            "latest_path": str(tmp_path / "latest-audit.json"),
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["audit", "deps", str(tmp_path), "--json-output", "--save-report"])

    assert result.exit_code == 0
    assert '"saved_report"' in result.output
    assert '"latest_path"' in result.output


def test_cli_audit_profiles(monkeypatch):
    monkeypatch.setattr(
        "tldreadme.audit.list_policy_profiles",
        lambda: [{"id": "owasp-mcp", "description": "profile", "recommended_categories": ["code"], "focus_areas": ["tools"]}],
    )
    monkeypatch.setattr(
        "tldreadme.audit.render_policy_profiles",
        lambda: "Audit Profiles:\nowasp-mcp: profile",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["audit", "profiles"])

    assert result.exit_code == 0
    assert "owasp-mcp" in result.output


def test_cli_audit_kev_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tldreadme.audit.refresh_kev_catalog",
        lambda **_kwargs: {
            "path": str(tmp_path / "kev.json"),
            "count": 42,
            "url": "https://example.com/kev.json",
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["audit", "kev-refresh", "--output", str(tmp_path / "kev.json")])

    assert result.exit_code == 0
    assert "Wrote KEV catalog:" in result.output
    assert "Entries: 42" in result.output


def test_cli_doctor_runs():
    runner = CliRunner()
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "tree-sitter" in result.output
    assert "ripgrep" in result.output
    assert "doctor --fix" in result.output


def test_cli_doctor_diagnostics(monkeypatch, tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def sample():\n    return 1\n")

    monkeypatch.setattr(
        "tldreadme.runtime.runtime_report",
        lambda: {
            "ok": True,
            "checks": [
                {
                    "name": "python",
                    "status": "ok",
                    "ok": True,
                    "details": "3.12.12",
                    "category": "runtime",
                    "required": True,
                    "install_options": [],
                }
            ],
        },
    )
    monkeypatch.setattr(
        "tldreadme.coding_tools.diagnostics_here",
        lambda *_args, **_kwargs: {
            "path": str(source),
            "diagnostics": [{"path": str(source), "line": 1, "severity": "warning", "message": "possible issue"}],
            "likely_fix_area": {"path": str(source), "line": 1, "severity": "warning"},
            "impacted_symbols": ["sample"],
            "verification_commands": ["python -m pytest -q tests/test_sample.py"],
            "fallback_used": [],
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["doctor", "--diagnostics", str(source), "--line", "1"])

    assert result.exit_code == 0
    assert "Diagnostics:" in result.output
    assert "WARNING:" in result.output
    assert "Likely fix area:" in result.output
    assert "Impacted symbols: sample" in result.output


def test_cli_doctor_fix_prints_install_options(monkeypatch):
    runner = CliRunner()

    monkeypatch.setattr(
        "tldreadme.runtime.runtime_report",
        lambda: {
            "ok": True,
            "checks": [
                {
                    "name": "Python LSP",
                    "status": "warn",
                    "ok": False,
                    "details": "missing",
                    "category": "lsp",
                    "required": False,
                    "install_options": [
                        {"label": "Install basedpyright via npm", "command": "npm install -g basedpyright"},
                    ],
                }
            ],
        },
    )

    result = runner.invoke(main, ["doctor", "--fix"])
    assert result.exit_code == 0
    assert "[ ] 1. Python LSP [lsp]" in result.output
    assert "npm install -g basedpyright" in result.output
    assert "non-interactive" in result.output


def test_select_doctor_fix_items_uses_questionary(monkeypatch):
    class FakePrompt:
        def __init__(self, values):
            self.values = values

        def ask(self):
            return self.values

    class FakeQuestionary:
        @staticmethod
        def Choice(title, value):
            return {"title": title, "value": value}

        @staticmethod
        def checkbox(_message, choices, **_kwargs):
            return FakePrompt([choices[0]["value"]])

    checks = [
        {
            "name": "Python LSP",
            "category": "lsp",
            "install_options": [
                {"label": "Install basedpyright via npm", "command": "npm install -g basedpyright"},
            ],
        }
    ]

    monkeypatch.setattr(cli, "_load_questionary", lambda: FakeQuestionary())

    selected = cli._select_doctor_fix_items(checks)

    assert selected == checks


def test_cli_lsp_invokes_semantic_query(monkeypatch, tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def sample():\n    return 1\n")

    monkeypatch.setattr(
        "tldreadme.lsp.semantic_inspect",
        lambda *args, **kwargs: {
            "path": args[0],
            "line": args[1],
            "column": args[2],
            "hover": "hover text",
            "definitions": [],
            "references": [],
            "document_symbols": [],
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["lsp", str(source), "1", "--column", "5"])

    assert result.exit_code == 0
    assert "hover text" in result.output


def test_cli_lsp_symbols_invokes_workspace_query(monkeypatch, tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def sample():\n    return 1\n")

    monkeypatch.setattr(
        "tldreadme.lsp.workspace_symbols",
        lambda *args, **kwargs: {
            "path": args[0],
            "query": args[1],
            "symbols": [{"name": "sample"}],
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["lsp-symbols", str(source), "sample"])

    assert result.exit_code == 0
    assert "\"name\": \"sample\"" in result.output


def test_cli_summary_renders_report(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tldreadme.summary.build_summary",
        lambda **_kwargs: {
            "since": "2026-03-22T00:00:00+00:00",
            "updated_checkpoint": "2026-03-22T01:00:00+00:00",
            "counts": {"commits": 1, "working_tree_changes": 1, "tasks": 1, "session_notes": 1},
            "commits": [{"short_commit": "abc123", "subject": "Add summary command"}],
            "working_tree": [{"status": " M", "path": "tldreadme/cli.py"}],
            "workboard": {"plans": [], "tasks": [], "session_notes": []},
        },
    )
    monkeypatch.setattr(
        "tldreadme.summary.render_summary",
        lambda payload: f"Summary since {payload['since']}\nCounts: 1 commit",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["summary", str(tmp_path)])

    assert result.exit_code == 0
    assert "Summary since 2026-03-22T00:00:00+00:00" in result.output


def test_cli_plans_capture_reads_stdin(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tldreadme.roadmap.capture_plan_input",
        lambda _text, root: {
            "capture_path": str(tmp_path / ".tldr/roadmap/TLDRPLANS.20260323-120000.md"),
            "plans_path": str(tmp_path / ".tldr/roadmap/TLDRPLANS.md"),
            "captures_count": 3,
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["plans-capture", str(tmp_path)], input="notes\nlinks\n")

    assert result.exit_code == 0
    assert "Saved capture: TLDRPLANS.20260323-120000.md" in result.output
    assert "Updated plans digest: TLDRPLANS.md" in result.output


def test_cli_whats_next_renders_report(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tldreadme.roadmap.whats_next_vibe",
        lambda root: {
            "project": "demo",
            "project_intent": "Turn context into action.",
            "completion": {"percent": 50.0, "tasks_done": 1, "tasks_total": 2, "plan_count": 1},
            "current_plan": {"title": "Audit", "status": "in_progress"},
            "current_code_status": {"source_counts": {"code": 10, "tests": 4, "docs": 2, "workboard": 1}},
            "strategic_question": "What is the most strategic next question?",
            "top_goal": "Add audit",
            "next_options": [],
            "recommended_next_action": "Use repo_lookup first.",
            "next_tool": "repo_lookup",
            "direction_signals": [],
        },
    )
    monkeypatch.setattr(
        "tldreadme.roadmap.render_whats_next_vibe",
        lambda payload: f"What's next for {payload['project']}\n{payload['strategic_question']}",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["whats-next", str(tmp_path)])

    assert result.exit_code == 0
    assert "What's next for demo" in result.output


def test_cli_current_roadmap_writes_report(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tldreadme.roadmap.build_current_vibe_roadmap",
        lambda root, write: {
            "path": str(tmp_path / "TLDROADMAP.md"),
            "project": "demo",
            "strategic_question": "What is the most strategic next question?",
            "project_intent": "Turn context into action.",
            "completion": {"percent": 50.0, "tasks_done": 1, "tasks_total": 2, "plan_count": 1},
            "current_plan": {"title": "Audit", "status": "in_progress"},
            "current_code_status": {"source_counts": {"code": 10, "tests": 4, "docs": 2, "workboard": 1}},
            "top_goal": "Add audit",
            "next_options": [],
            "recommended_next_action": "Use repo_lookup first.",
            "next_tool": "repo_lookup",
            "direction_signals": [],
        },
    )
    monkeypatch.setattr(
        "tldreadme.roadmap.render_whats_next_vibe",
        lambda payload: f"What's next for {payload['project']}\n{payload['strategic_question']}",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["current-roadmap", str(tmp_path)])

    assert result.exit_code == 0
    assert "Wrote TLDROADMAP.md" in result.output
    assert "What's next for demo" in result.output


def test_cli_legacy_roadmap_aliases_still_work(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tldreadme.roadmap.whats_next_vibe",
        lambda root: {
            "project": "demo",
            "project_intent": "Turn context into action.",
            "completion": {"percent": 50.0, "tasks_done": 1, "tasks_total": 2, "plan_count": 1},
            "current_plan": {"title": "Audit", "status": "in_progress"},
            "current_code_status": {"source_counts": {"code": 10, "tests": 4, "docs": 2, "workboard": 1}},
            "strategic_question": "What is the most strategic next question?",
            "top_goal": "Add audit",
            "next_options": [],
            "recommended_next_action": "Use repo_lookup first.",
            "next_tool": "repo_lookup",
            "direction_signals": [],
        },
    )
    monkeypatch.setattr(
        "tldreadme.roadmap.render_whats_next_vibe",
        lambda payload: f"What's next for {payload['project']}\n{payload['strategic_question']}",
    )
    monkeypatch.setattr(
        "tldreadme.roadmap.build_current_vibe_roadmap",
        lambda root, write: {
            "path": str(tmp_path / "TLDROADMAP.md"),
            "project": "demo",
            "strategic_question": "What is the most strategic next question?",
            "project_intent": "Turn context into action.",
            "completion": {"percent": 50.0, "tasks_done": 1, "tasks_total": 2, "plan_count": 1},
            "current_plan": {"title": "Audit", "status": "in_progress"},
            "current_code_status": {"source_counts": {"code": 10, "tests": 4, "docs": 2, "workboard": 1}},
            "top_goal": "Add audit",
            "next_options": [],
            "recommended_next_action": "Use repo_lookup first.",
            "next_tool": "repo_lookup",
            "direction_signals": [],
        },
    )

    runner = CliRunner()
    whats_next = runner.invoke(main, ["whats-next-vibe", str(tmp_path)])
    current = runner.invoke(main, ["current-vibe-roadmap", str(tmp_path), "--no-write"])

    assert whats_next.exit_code == 0
    assert current.exit_code == 0


def test_cli_children_list_renders_report(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tldreadme.children.list_children",
        lambda **_kwargs: {
            "count": 1,
            "unknown_count": 1,
            "merged_count": 0,
            "ignored_count": 0,
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
            ],
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["children", "list", str(tmp_path)])

    assert result.exit_code == 0
    assert "UNKNOWN: redocoder" in result.output
    assert "package.json" in result.output


def test_cli_children_merge_marks_child(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "tldreadme.children.merge_child",
        lambda *_args, **_kwargs: {
            "path": "redocoder",
            "status": "merged",
            "manifests": ["package.json"],
            "context_docs": ["README.md"],
            "has_git": False,
            "code_file_count": 12,
            "note": "Imported intentionally",
        },
    )
    monkeypatch.setattr(
        "tldreadme.children.describe_child",
        lambda payload: f"manifests: {payload['manifests'][0]}; note: {payload['note']}",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["children", "merge", "redocoder", "--root", str(tmp_path), "--note", "Imported intentionally"])

    assert result.exit_code == 0
    assert "MERGED: redocoder" in result.output
    assert "Imported intentionally" in result.output


def test_cli_unknown_command():
    runner = CliRunner()
    result = runner.invoke(main, ["nonexistent"])
    assert result.exit_code != 0
