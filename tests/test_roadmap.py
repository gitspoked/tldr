"""Tests for the human-first roadmap helpers."""

from tldreadme import roadmap


def _stub_roadmap_dependencies(monkeypatch):
    monkeypatch.setattr(
        "tldreadme.rag.suggest_goals",
        lambda _path: {
            "top_goal": "Add a local-first `tldr audit` command.",
            "candidate_goals": [
                {
                    "title": "Add local tldr audit pipeline",
                    "goal": "Add a local-first `tldr audit` command.",
                    "why_now": "Security and adversarial checks are the largest current feature gap.",
                    "files": ["tldreadme/cli.py", "tldreadme/audit.py"],
                    "verification_commands": [".venv/bin/python -m pytest -q tests/test_cli.py"],
                },
                {
                    "title": "Regenerate TLDR context during watch mode",
                    "goal": "Refresh generated context during watch mode.",
                    "why_now": "Generated context still drifts after init.",
                    "files": ["tldreadme/watcher.py"],
                    "verification_commands": [".venv/bin/python -m pytest -q tests/test_generator.py"],
                },
            ],
            "suggested_goals": "### Next Goals for Codebase Review\n\n#### Goal 1: Add local tldr audit pipeline",
        },
    )
    monkeypatch.setattr(
        "tldreadme.rag.best_question",
        lambda goal, path=None: {
            "best_question": f"What is the smallest end-to-end change needed to achieve `{goal}`?",
            "answer": "Start in `tldreadme/cli.py`, then verify with pytest.",
        },
    )

    def fake_repo_lookup(**kwargs):
        if kwargs.get("query"):
            return {
                "ranked_hits": [
                    {
                        "path": str(kwargs["root"]) + "/tldreadme/cli.py",
                        "line": 10,
                        "source_type": "code",
                        "snippet": "Add the audit command entry point.",
                        "why_matched": "Exact text search hit in source code.",
                    }
                ]
            }
        return {
            "source_counts": {"code": 12, "tests": 5, "docs": 4, "workboard": 2},
            "children": {"unknown_count": 1},
        }

    monkeypatch.setattr("tldreadme.coding_tools.repo_lookup", fake_repo_lookup)
    monkeypatch.setattr(
        "tldreadme.coding_tools.repo_next_action",
        lambda **_kwargs: {
            "recommended_next_action": "Use repo_lookup on the audit surface before editing.",
            "suggested_tool": "repo_lookup",
        },
    )
    monkeypatch.setattr(
        "tldreadme.summary.build_summary",
        lambda **_kwargs: {"counts": {"commits": 1, "working_tree_changes": 0}},
    )
    monkeypatch.setattr(
        "tldreadme.children.list_children",
        lambda **_kwargs: {"children": [{"path": "vendor/redocoder", "status": "unknown"}]},
    )
    monkeypatch.setattr(
        "tldreadme.workboard.list_plans",
        lambda **_kwargs: {"plans": [{"id": "plan-1", "status": "in_progress", "title": "Audit"}]},
    )
    monkeypatch.setattr(
        "tldreadme.workboard.get_plan",
        lambda _plan_id, **_kwargs: {
            "phases": [
                {
                    "tasks": [
                        {"status": "done"},
                        {"status": "pending"},
                    ]
                }
            ]
        },
    )
    monkeypatch.setattr(
        "tldreadme.workboard.current_plan",
        lambda **_kwargs: {
            "summary": {"id": "plan-1", "title": "Audit", "status": "in_progress"},
            "plan": {
                "id": "plan-1",
                "title": "Audit",
                "status": "in_progress",
                "phases": [{"tasks": [{"status": "done"}, {"status": "pending"}]}],
            },
            "session": {"current_plan_id": "plan-1"},
        },
    )


def test_capture_plan_input_writes_timestamped_drop_and_refreshes_digest(monkeypatch, tmp_path):
    _stub_roadmap_dependencies(monkeypatch)
    monkeypatch.setattr(roadmap, "_timestamp", lambda: "20260323-120000")

    (tmp_path / "README.md").write_text("# Demo\n\nThis project indexes repositories and guides next-step planning.\n", encoding="utf-8")

    result = roadmap.capture_plan_input(
        "## Notes\n\nAdd audit links https://example.com/security and keep the CLI human-first.\n",
        root=tmp_path,
    )

    assert (tmp_path / "TLDRPLANS.20260323-120000.md").exists()
    assert (tmp_path / "TLDRPLANS.md").exists()
    assert result["captures_count"] == 1
    digest = (tmp_path / "TLDRPLANS.md").read_text(encoding="utf-8")
    assert "Grounded Next Goals" in digest
    assert "https://example.com/security" in digest


def test_build_current_vibe_roadmap_writes_markdown(monkeypatch, tmp_path):
    _stub_roadmap_dependencies(monkeypatch)

    (tmp_path / "README.md").write_text(
        "# Demo\n\nThis project turns codebase context into a tool-first operating layer for agents.\n",
        encoding="utf-8",
    )
    (tmp_path / "TLDRNOTES.md").write_text("Fallback notes.\n", encoding="utf-8")

    result = roadmap.build_current_vibe_roadmap(root=tmp_path, write=True)

    roadmap_path = tmp_path / "TLDROADMAP.md"
    assert roadmap_path.exists()
    rendered = roadmap_path.read_text(encoding="utf-8")
    assert "Strategic Question To Ask Now" in rendered
    assert "Add local tldr audit pipeline" in rendered
    assert "Completion: 50.0% (1/2 tracked tasks)" in rendered
    assert result["completion"]["percent"] == 50.0
    assert result["planning_inputs"]["captures_count"] == 0
