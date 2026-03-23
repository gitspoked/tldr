"""Tests for human-facing repository summaries."""

from datetime import timedelta

from tldreadme import summary
from tldreadme import workboard


def test_build_summary_marks_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(summary, "_git_recent_commits", lambda *_args, **_kwargs: [{"short_commit": "abc123", "subject": "Change"}])
    monkeypatch.setattr(summary, "_git_working_tree_changes", lambda *_args, **_kwargs: [{"status": " M", "path": "tldreadme/cli.py"}])
    monkeypatch.setattr(
        summary,
        "_workboard_updates",
        lambda *_args, **_kwargs: {"plans": [{"title": "Plan", "status": "in_progress"}], "tasks": [], "session_notes": []},
    )
    monkeypatch.setattr(summary, "_children_updates", lambda *_args, **_kwargs: {"unknown": [], "counts": {"unknown": 0, "merged": 0, "ignored": 0}})

    result = summary.build_summary(root=tmp_path, mark_checked=True, limit=5)

    assert result["counts"]["commits"] == 1
    assert result["counts"]["working_tree_changes"] == 1
    assert result["updated_checkpoint"]
    assert summary.get_summary_checkpoint(tmp_path) == result["updated_checkpoint"]


def test_render_summary_includes_sections():
    rendered = summary.render_summary(
        {
            "since": "2026-03-22T00:00:00+00:00",
            "updated_checkpoint": "2026-03-22T01:00:00+00:00",
            "baseline": "checkpoint",
            "counts": {"commits": 1, "working_tree_changes": 1, "tasks": 1, "sessions": 1, "session_notes": 1, "unknown_children": 1},
            "commits": [{"short_commit": "abc123", "subject": "Add summary"}],
            "working_tree": [{"status": " M", "path": "tldreadme/cli.py"}],
            "workboard": {
                "plans": [{"title": "Plan", "status": "in_progress"}],
                "tasks": [{"title": "Task", "status": "done", "plan_title": "Plan", "phase": "Build"}],
                "sessions": [{"actor_id": "claude-code", "status": "active", "current_focus": "Patch parser"}],
                "session_overlaps": [{"actors": ["claude-code", "codex"], "shared_files": ["tldreadme/parser.py"], "shared_symbols": [], "same_task": True}],
                "session_notes": [{"actor_id": "claude-code", "note": "Follow up on tests"}],
            },
            "children": {
                "unknown": [
                    {
                        "path": "redocoder",
                        "status": "unknown",
                        "manifests": ["package.json"],
                        "context_docs": ["README.md"],
                        "has_git": False,
                        "code_file_count": 12,
                    }
                ]
            },
        }
    )

    assert "Commits:" in rendered
    assert "Working tree:" in rendered
    assert "Tasks:" in rendered
    assert "Sessions:" in rendered
    assert "Session overlaps:" in rendered
    assert "Unknown children:" in rendered
    assert "Session notes:" in rendered


def test_build_summary_includes_multi_session_workboard_updates(monkeypatch, tmp_path):
    work_root = tmp_path / ".tldr" / "work"
    plan = workboard.create_plan("Session plan", "Track resumable work", root=work_root, set_current=False)
    task = workboard.add_task(plan["id"], "Split parser", phase="Build", root=work_root, set_current=False)

    workboard.update_session(
        actor_id="claude-code",
        current_plan_id=plan["id"],
        current_task_id=task["id"],
        current_phase="Build",
        current_focus="Patch parser pipeline",
        next_action="Run targeted tests",
        claimed_files=["tldreadme/parser.py"],
        root=work_root,
    )
    workboard.add_session_note("Need one more parser pass", actor_id="claude-code", root=work_root)
    workboard.update_session(
        actor_id="codex",
        current_plan_id=plan["id"],
        current_task_id=task["id"],
        current_phase="Build",
        current_focus="Compare AST output",
        next_action="Check overlap",
        claimed_files=["tldreadme/parser.py"],
        root=work_root,
    )

    child = tmp_path / "redocoder"
    child.mkdir()
    (child / "package.json").write_text('{"name":"redocoder"}\n')
    (child / "README.md").write_text("# Redocoder\n")
    (child / "src").mkdir()
    (child / "src" / "index.ts").write_text("export const value = 1;\n")

    monkeypatch.setattr(summary, "_git_recent_commits", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(summary, "_git_working_tree_changes", lambda *_args, **_kwargs: [])

    result = summary.build_summary(
        root=tmp_path,
        since=(summary._now() - timedelta(minutes=5)).isoformat(),
        mark_checked=False,
        limit=10,
    )

    assert result["counts"]["plans"] == 1
    assert result["counts"]["tasks"] == 1
    assert result["counts"]["sessions"] == 2
    assert result["counts"]["session_overlaps"] == 1
    assert result["counts"]["unknown_children"] == 1
    assert any(session["actor_id"] == "claude-code" for session in result["workboard"]["sessions"])
    assert result["workboard"]["session_notes"][0]["actor_id"] == "claude-code"
    assert result["children"]["unknown"][0]["path"] == "redocoder"
