"""Tests for grounded planning and backwards-flow helpers."""

import subprocess
from types import SimpleNamespace

from tldreadme import rag


def _planning_snapshot(*, plan: dict | None = None, session: dict | None = None) -> dict:
    summary = None
    if plan:
        summary = {
            "id": plan.get("id"),
            "title": plan.get("title"),
            "status": plan.get("status", "pending"),
            "goal": plan.get("goal"),
        }
    return {
        "repo_next_action": {
            "recommended_next_action": "Use repo_lookup to inspect the next concrete implementation target."
        },
        "scan_context": {
            "source_counts": {"code": 12, "tests": 6, "docs": 4, "workboard": 1},
            "children": {"unknown_count": 0},
        },
        "current": {
            "summary": summary,
            "session": session or {},
            "plan": plan or {},
            "overlaps": [],
        },
    }


def test_suggest_goals_prefers_active_plan_and_filters_generic_maintenance(monkeypatch, tmp_path):
    repo = tmp_path
    pkg = repo / "tldreadme"
    pkg.mkdir()
    (pkg / "cli.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (pkg / "watcher.py").write_text("def start_watcher():\n    pass\n", encoding="utf-8")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    (repo / "TLDREADME.md").write_text("# notes\n", encoding="utf-8")

    plan = {
        "id": "plan-audit",
        "title": "Add local tldr audit pipeline",
        "status": "pending",
        "goal": "Add a local-first `tldr audit` command with dependency, code, secrets, and adversarial checks.",
        "phases": [
            {
                "name": "Discovery",
                "tasks": [
                    {
                        "id": "audit-surface",
                        "title": "Define audit command surface",
                        "status": "pending",
                        "files": ["tldreadme/cli.py", "tldreadme/runtime.py"],
                        "verification_commands": [
                            ".venv/bin/python -m pytest -q tests/test_cli.py"
                        ],
                    }
                ],
            }
        ],
    }
    session = {
        "current_phase": "Discovery",
        "current_task_id": "audit-surface",
        "next_action": "Shape the audit categories and scanner capability map.",
        "verification_commands": [".venv/bin/python -m pytest -q tests/test_cli.py"],
    }
    monkeypatch.setattr(
        rag, "_planning_snapshot", lambda _path: _planning_snapshot(plan=plan, session=session)
    )

    result = rag.suggest_goals(str(repo))

    assert result["top_goal"] == plan["goal"]
    assert result["candidate_goals"][0]["source"] == "active_plan"
    assert result["candidate_goals"][0]["title"].startswith("Continue active plan:")
    assert "version control" not in result["suggested_goals"].lower()
    assert len([c for c in result["candidate_goals"] if "audit" in c["title"].lower()]) == 1


def test_suggest_goals_identifies_audit_gap_before_watcher_gap(monkeypatch, tmp_path):
    repo = tmp_path
    pkg = repo / "tldreadme"
    pkg.mkdir()
    (pkg / "cli.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (pkg / "watcher.py").write_text("def start_watcher():\n    pass\n", encoding="utf-8")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    (repo / "TLDREADME.md").write_text("# planner notes\n", encoding="utf-8")

    monkeypatch.setattr(rag, "_planning_snapshot", lambda _path: _planning_snapshot())

    result = rag.suggest_goals(str(repo))

    assert result["candidate_goals"][0]["id"] == "local-audit-pipeline"
    assert result["candidate_goals"][1]["id"] == "watcher-context-regeneration"
    assert "local-first `tldr audit` command" in result["top_goal"]


def test_suggest_goals_does_not_inject_tldreadme_tasks_into_other_repositories(
    monkeypatch, tmp_path
):
    (tmp_path / "README.md").write_text("# unrelated project\n", encoding="utf-8")
    monkeypatch.setattr(rag, "_planning_snapshot", lambda _path: _planning_snapshot())

    result = rag.suggest_goals(str(tmp_path))

    assert result["top_goal"] is None
    assert result["candidate_goals"] == []
    assert result["analysis"]["scope_mode"] == "repository"


def test_cross_repository_ideas_are_evidence_not_candidate_tasks(monkeypatch, tmp_path):
    package = tmp_path / "tldreadme"
    package.mkdir()
    (package / "cli.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (package / "watcher.py").write_text("def start_watcher():\n    pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# target project\n", encoding="utf-8")
    monkeypatch.setattr(rag, "_planning_snapshot", lambda _path: _planning_snapshot())
    monkeypatch.setattr(
        rag,
        "read_similar",
        lambda *_args, **_kwargs: [
            {
                "symbol": "shared_retry_pattern",
                "kind": "function",
                "file": "/workspace/other-repo/retry.py",
                "line": 12,
                "signature": "def shared_retry_pattern():",
                "score": 0.91,
            }
        ],
    )

    result = rag.suggest_goals(str(tmp_path), cross_repository_ideas=True)

    assert [candidate["id"] for candidate in result["candidate_goals"]] == [
        "local-audit-pipeline",
        "watcher-context-regeneration",
    ]
    assert result["shared_code_evidence"][0]["symbol"] == "shared_retry_pattern"
    assert result["scope_policy"] == {
        "task_scope": "repository",
        "evidence_scope": "cross_repository",
        "cross_repository_tasks": False,
    }


def test_best_question_uses_repo_lookup_and_change_plan(monkeypatch, tmp_path):
    repo = tmp_path
    pkg = repo / "tldreadme"
    pkg.mkdir()
    target = pkg / "cli.py"
    target.write_text("def main():\n    pass\n", encoding="utf-8")

    fake_tools = SimpleNamespace(
        repo_lookup=lambda **_kwargs: {
            "recommended_next_action": "Inspect tldreadme/cli.py before editing.",
            "evidence": ["candidate file: cli.py", "missing audit command"],
            "lookup_mode": "search",
            "specialist_tool": "search_context",
            "summary": "Repo lookup used `search_context`.",
            "ranked_hits": [{"path": str(target), "line": 1, "source_type": "code"}],
        },
        change_plan=lambda *_args, **_kwargs: {
            "candidate_files": [str(target)],
            "likely_symbols": ["audit"],
            "verification_commands": [".venv/bin/python -m pytest -q tests/test_cli.py"],
            "risks": ["No targeted tests were found automatically."],
            "recommended_next_action": "Call edit_context on tldreadme/cli.py before changing code.",
            "summary": "Prepared a change plan.",
        },
    )
    monkeypatch.setattr(rag, "_coding_tools", lambda: fake_tools)

    result = rag.best_question("Add a local audit command", path=str(repo))

    assert "cli.py" in result["best_question"]
    assert "pytest" in result["answer"]
    assert "Inspect tldreadme/cli.py before editing." in result["answer"]
    assert result["relevant_symbols"] == ["audit"]
    assert result["relevant_files"][0] == "tldreadme/cli.py"


def test_auto_iterate_uses_ranked_candidate_goals_in_order(monkeypatch):
    monkeypatch.setattr(
        rag,
        "suggest_goals",
        lambda _path: {
            "analysis": {"candidate_count": 2},
            "top_goal": "Feature one",
            "candidate_goals": [
                {"goal": "Feature one"},
                {"goal": "Feature two"},
            ],
            "suggested_goals": "Feature one\nFeature two",
        },
    )
    monkeypatch.setattr(
        rag,
        "best_question",
        lambda goal, path=None: {
            "best_question": f"Question for {goal}",
            "answer": f"Answer for {goal}",
            "relevant_symbols": [],
            "relevant_files": [],
            "recommended_next_action": "Do the thing.",
            "verification_commands": [],
        },
    )

    result = rag.auto_iterate(".", rounds=2)

    assert result["iterations"][0]["goal"] == "Feature one"
    assert result["iterations"][1]["goal"] == "Feature two"
    assert result["rounds_completed"] == 2


def test_read_recent_does_not_require_graph_backend(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def sample():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "sample.py"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "Add sample",
        ],
        cwd=tmp_path,
        check=True,
    )
    recent = rag.read_recent(scope=str(tmp_path), days=30)

    assert recent[0]["file"] == "sample.py"
    assert recent[0]["symbols_in_file"][0]["name"] == "sample"
