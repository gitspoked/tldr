"""Tests for the file-backed workboard."""

from pathlib import Path

from tldreadme import workboard


def test_create_plan_persists_yaml(tmp_path):
    root = tmp_path / "work"

    plan = workboard.create_plan(
        "Stabilize indexing",
        "Get the indexing pipeline stable enough for CI.",
        phases=["Plan", "Build", "Verify"],
        success_criteria=["pytest passes", "doctor is clean"],
        root=root,
    )

    plan_file = root / "plans" / f"{plan['id']}.yaml"
    assert plan_file.exists()
    assert plan["phases"][0]["name"] == "Plan"


def test_add_and_complete_task_updates_plan_state(tmp_path):
    root = tmp_path / "work"
    plan = workboard.create_plan("Workboard", "Add phased execution tracking.", root=root)

    task = workboard.add_task(
        plan["id"],
        "Implement task persistence",
        phase="Build",
        acceptance_criteria=["Tasks serialize to YAML", "Tasks reload correctly"],
        verification_commands=["pytest -q tests/test_workboard.py"],
        root=root,
    )

    updated = workboard.update_task(
        plan["id"],
        task["id"],
        status="in_progress",
        add_notes=["Started workboard schema implementation"],
        root=root,
    )
    assert updated["status"] == "in_progress"

    done = workboard.complete_task(
        plan["id"],
        task["id"],
        evidence=["pytest -q tests/test_workboard.py -> passed"],
        note="Persistence path verified",
        root=root,
    )
    assert done["status"] == "done"
    assert done["evidence"]

    plan_state = workboard.get_plan(plan["id"], root=root)
    assert plan_state["phases"][0]["tasks"] == []
    assert plan_state["phases"][1]["tasks"][0]["status"] == "done"


def test_current_plan_uses_session_pointer(tmp_path):
    root = tmp_path / "work"
    first = workboard.create_plan("First", "First goal", root=root)
    second = workboard.create_plan("Second", "Second goal", root=root)

    workboard.set_current_plan(first["id"], phase="Backlog", root=root)
    workboard.add_session_note("Continue from the schema review", root=root)

    current = workboard.current_plan(root=root)
    session_path = root / "sessions" / f"current.{current['session']['session_id']}.yaml"

    assert current["session"]["current_plan_id"] == first["id"]
    assert current["plan"]["id"] == first["id"]
    assert current["session"]["notes"][0]["note"] == "Continue from the schema review"
    assert session_path.exists()
    assert second["id"] != current["plan"]["id"]


def test_update_session_tracks_focus_and_overlap(tmp_path):
    root = tmp_path / "work"
    plan = workboard.create_plan("Overlap", "Avoid duplicated effort", root=root, set_current=False)
    task = workboard.add_task(plan["id"], "Patch parser", phase="Build", root=root, set_current=False)

    first = workboard.update_session(
        actor_id="claude-code",
        current_plan_id=plan["id"],
        current_task_id=task["id"],
        current_phase="Build",
        current_focus="Refine parser split",
        next_action="Run targeted tests",
        claimed_files=["tldreadme/parser.py"],
        claimed_symbols=["parse_file"],
        verification_commands=["pytest -q tests/test_workboard.py"],
        recent_steps=["Reviewed parser responsibilities"],
        root=root,
    )
    workboard.update_session(
        actor_id="codex",
        current_plan_id=plan["id"],
        current_task_id=task["id"],
        current_phase="Build",
        current_focus="Patch parser edge cases",
        next_action="Compare AST extraction",
        claimed_files=["tldreadme/parser.py"],
        claimed_symbols=["parse_file"],
        root=root,
    )

    current = workboard.current_plan(root=root, actor_id="claude-code")
    listing = workboard.list_sessions(root=root)

    assert first["current_focus"] == "Refine parser split"
    assert first["claimed_files"] == ["tldreadme/parser.py"]
    assert listing["count"] == 2
    assert current["active_sessions"][0]["actor_id"] == "codex"
    assert current["overlaps"][0]["same_task"] is True
    assert current["overlaps"][0]["shared_files"] == ["tldreadme/parser.py"]
    assert current["overlaps"][0]["shared_symbols"] == ["parse_file"]


def test_update_session_replaces_claims_instead_of_appending(tmp_path):
    root = tmp_path / "work"

    workboard.update_session(actor_id="claude-code", claimed_files=["one.py"], claimed_symbols=["Alpha"], root=root)
    workboard.update_session(actor_id="claude-code", claimed_files=["two.py"], claimed_symbols=[], root=root)

    current = workboard.current_plan(root=root, actor_id="claude-code")

    assert current["session"]["claimed_files"] == ["two.py"]
    assert current["session"]["claimed_symbols"] == []


def test_list_plans_returns_summaries(tmp_path):
    root = tmp_path / "work"
    plan = workboard.create_plan("Summaries", "Need list view", root=root)
    workboard.add_task(plan["id"], "Add list summaries", root=root)

    listing = workboard.list_plans(root=root)

    assert listing["count"] == 1
    assert listing["plans"][0]["id"] == plan["id"]
    assert listing["plans"][0]["task_count"] == 1


def test_get_task_includes_plan_context(tmp_path):
    root = tmp_path / "work"
    plan = workboard.create_plan("Context", "Need plan-aware tasks", root=root)
    task = workboard.add_task(plan["id"], "Inspect nested task payload", root=root)

    payload = workboard.get_task(plan["id"], task["id"], root=root)

    assert payload["plan_id"] == plan["id"]
    assert payload["plan_title"] == "Context"
    assert payload["title"] == "Inspect nested task payload"
