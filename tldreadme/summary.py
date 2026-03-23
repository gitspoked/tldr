"""Human-facing repository summaries with a local checkpoint."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

import yaml


SUMMARY_FILE = Path(".tldr/work/sessions/summary.yaml")
DEFAULT_LOOKBACK = timedelta(days=1)


def _now() -> datetime:
    """Return the current UTC time."""

    return datetime.now(timezone.utc).replace(microsecond=0)


def _repo_root(root: str | Path | None = None) -> Path:
    """Resolve the repository root."""

    return Path(root or ".").resolve()


def _summary_file(root: str | Path | None = None) -> Path:
    """Return the summary checkpoint file path."""

    return _repo_root(root) / SUMMARY_FILE


def _ensure_summary_dir(root: str | Path | None = None) -> None:
    """Ensure the summary checkpoint directory exists."""

    _summary_file(root).parent.mkdir(parents=True, exist_ok=True)


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO timestamp into UTC."""

    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_summary_checkpoint(root: str | Path | None = None) -> str | None:
    """Return the last stored summary checkpoint."""

    path = _summary_file(root)
    if not path.exists():
        return None

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return None
    value = data.get("summary_checked_at")
    return str(value) if value else None


def mark_summary_checked(root: str | Path | None = None, *, at: str | None = None) -> dict:
    """Persist a new summary checkpoint."""

    _ensure_summary_dir(root)
    payload = {"summary_checked_at": at or _now().isoformat()}
    _summary_file(root).write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
    return payload


def _git_lines(repo_root: Path, args: list[str]) -> list[str]:
    """Run a git command and return stdout lines."""

    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _git_recent_commits(repo_root: Path, since: datetime, limit: int) -> list[dict]:
    """Return commits since the summary checkpoint."""

    lines = _git_lines(
        repo_root,
        [
            "log",
            f"--since={since.isoformat()}",
            "--date=iso-strict",
            "--pretty=format:%H%x1f%h%x1f%ad%x1f%s",
            f"--max-count={limit}",
        ],
    )
    commits = []
    for line in lines:
        full, short, authored_at, subject = line.split("\x1f", 3)
        commits.append(
            {
                "commit": full,
                "short_commit": short,
                "authored_at": authored_at,
                "subject": subject,
            }
        )
    return commits


def _git_working_tree_changes(repo_root: Path, since: datetime, limit: int) -> list[dict]:
    """Return working tree paths changed since the checkpoint."""

    lines = _git_lines(repo_root, ["status", "--porcelain=v1", "--untracked-files=all"])
    changes = []
    for line in lines:
        raw_status = line[:2]
        path_value = line[3:]
        if " -> " in path_value:
            path_value = path_value.split(" -> ", 1)[1]
        candidate = repo_root / path_value
        if candidate.exists():
            modified_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
            if modified_at < since:
                continue
        changes.append({"status": raw_status, "path": path_value})
        if len(changes) >= limit:
            break
    return changes


def _workboard_updates(repo_root: Path, since: datetime, limit: int) -> dict:
    """Return plans, tasks, and notes updated since the checkpoint."""

    work_root = repo_root / ".tldr" / "work"
    if not work_root.exists():
        return {"plans": [], "tasks": [], "sessions": [], "session_overlaps": [], "session_notes": []}

    from . import workboard

    listing = workboard.list_plans(root=work_root)
    session_listing = workboard.list_sessions(root=work_root)
    plans = []
    tasks = []
    for plan_summary in listing.get("plans", []):
        updated_at = _parse_timestamp(plan_summary.get("updated_at"))
        if not updated_at or updated_at < since:
            continue
        plans.append(plan_summary)
        plan = workboard.get_plan(plan_summary["id"], root=work_root)
        for phase in plan.get("phases", []):
            for task in phase.get("tasks", []):
                task_updated = _parse_timestamp(task.get("updated_at"))
                if task_updated and task_updated >= since:
                    tasks.append(
                        {
                            "plan_id": plan_summary["id"],
                            "plan_title": plan_summary["title"],
                            "phase": phase.get("name"),
                            "id": task.get("id"),
                            "title": task.get("title"),
                            "status": task.get("status"),
                            "updated_at": task.get("updated_at"),
                        }
                    )
        if len(plans) >= limit and len(tasks) >= limit:
            break

    session_details = []
    for session_summary in session_listing.get("sessions", []):
        session = workboard.current_plan(root=work_root, session_id=session_summary["session_id"]).get("session", {})
        if session:
            session_details.append(session)

    sessions = []
    notes = []
    for session in session_details:
        session_updated = _parse_timestamp(session.get("updated_at"))
        if session_updated and session_updated >= since:
            sessions.append(
                {
                    "session_id": session.get("session_id"),
                    "actor_id": session.get("actor_id"),
                    "status": session.get("status"),
                    "current_plan_id": session.get("current_plan_id"),
                    "current_task_id": session.get("current_task_id"),
                    "current_phase": session.get("current_phase"),
                    "current_focus": session.get("current_focus"),
                    "next_action": session.get("next_action"),
                    "claimed_files": session.get("claimed_files", []),
                    "claimed_symbols": session.get("claimed_symbols", []),
                    "updated_at": session.get("updated_at"),
                }
            )

        for note in session.get("notes", []):
            noted_at = _parse_timestamp(note.get("timestamp"))
            if noted_at and noted_at >= since:
                notes.append(
                    {
                        **note,
                        "session_id": session.get("session_id"),
                        "actor_id": session.get("actor_id"),
                    }
                )

    notes.sort(key=lambda item: item.get("timestamp", ""), reverse=True)

    session_overlaps = []
    seen_pairs = set()
    for index, left in enumerate(session_details):
        left_files = set(left.get("claimed_files", []))
        left_symbols = set(left.get("claimed_symbols", []))
        left_task = left.get("current_task_id")
        left_updated = _parse_timestamp(left.get("updated_at"))
        for right in session_details[index + 1 :]:
            right_updated = _parse_timestamp(right.get("updated_at"))
            if not ((left_updated and left_updated >= since) or (right_updated and right_updated >= since)):
                continue
            pair = tuple(sorted([left.get("session_id", ""), right.get("session_id", "")]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            shared_files = sorted(left_files & set(right.get("claimed_files", [])))
            shared_symbols = sorted(left_symbols & set(right.get("claimed_symbols", [])))
            same_task = bool(left_task and left_task == right.get("current_task_id"))
            if not shared_files and not shared_symbols and not same_task:
                continue
            session_overlaps.append(
                {
                    "sessions": list(pair),
                    "actors": [left.get("actor_id"), right.get("actor_id")],
                    "shared_files": shared_files,
                    "shared_symbols": shared_symbols,
                    "same_task": same_task,
                }
            )
            if len(session_overlaps) >= limit:
                break
        if len(session_overlaps) >= limit:
            break

    return {
        "plans": plans[:limit],
        "tasks": tasks[:limit],
        "sessions": sessions[:limit],
        "session_overlaps": session_overlaps[:limit],
        "session_notes": notes[:limit],
    }


def _children_updates(repo_root: Path, since: datetime, limit: int) -> dict:
    """Return unknown child subtrees detected since the checkpoint."""

    from . import children

    listing = children.list_children(root=repo_root, include_ignored=True)
    unknown = []
    for child in listing.get("children", []):
        if child.get("status") != "unknown":
            continue
        updated_at = _parse_timestamp(child.get("updated_at"))
        if updated_at and updated_at >= since:
            unknown.append(child)

    return {
        "unknown": unknown[:limit],
        "counts": {
            "unknown": listing.get("unknown_count", 0),
            "merged": listing.get("merged_count", 0),
            "ignored": listing.get("ignored_count", 0),
        },
    }


def build_summary(
    *,
    root: str | Path = ".",
    since: str | None = None,
    mark_checked: bool = True,
    limit: int = 10,
) -> dict:
    """Build a human-facing repository summary."""

    repo_root = _repo_root(root)
    checkpoint = get_summary_checkpoint(repo_root)
    resolved_since = _parse_timestamp(since) if since else _parse_timestamp(checkpoint)
    baseline = "checkpoint" if resolved_since else "last_24h"
    if resolved_since is None:
        resolved_since = _now() - DEFAULT_LOOKBACK

    until = _now()
    commits = _git_recent_commits(repo_root, resolved_since, limit)
    working_tree = _git_working_tree_changes(repo_root, resolved_since, limit)
    workboard = _workboard_updates(repo_root, resolved_since, limit)
    children = _children_updates(repo_root, resolved_since, limit)

    updated_checkpoint = None
    if mark_checked:
        updated_checkpoint = mark_summary_checked(repo_root, at=until.isoformat())["summary_checked_at"]

    return {
        "root": str(repo_root),
        "since": resolved_since.isoformat(),
        "until": until.isoformat(),
        "baseline": baseline,
        "previous_checkpoint": checkpoint,
        "updated_checkpoint": updated_checkpoint,
        "counts": {
            "commits": len(commits),
            "working_tree_changes": len(working_tree),
            "plans": len(workboard.get("plans", [])),
            "tasks": len(workboard.get("tasks", [])),
            "sessions": len(workboard.get("sessions", [])),
            "session_overlaps": len(workboard.get("session_overlaps", [])),
            "session_notes": len(workboard.get("session_notes", [])),
            "unknown_children": len(children.get("unknown", [])),
        },
        "commits": commits,
        "working_tree": working_tree,
        "workboard": workboard,
        "children": children,
    }


def render_summary(summary: dict) -> str:
    """Render a concise human-facing summary."""

    lines = [f"Summary since {summary['since']}"]
    if summary.get("baseline") == "last_24h":
        lines.append("No previous summary checkpoint was found; using the last 24 hours.")
    lines.append(f"Checkpoint updated to {summary['updated_checkpoint'] or summary['until']}")

    counts = summary.get("counts", {})
    lines.append(
        "Counts: "
        f"{counts.get('commits', 0)} commits, "
        f"{counts.get('working_tree_changes', 0)} working tree changes, "
        f"{counts.get('tasks', 0)} task updates, "
        f"{counts.get('sessions', 0)} session updates, "
        f"{counts.get('session_notes', 0)} session notes, "
        f"{counts.get('unknown_children', 0)} unknown children"
    )

    commits = summary.get("commits", [])
    if commits:
        lines.append("")
        lines.append("Commits:")
        for commit in commits:
            lines.append(f"- {commit['short_commit']} {commit['subject']}")

    working_tree = summary.get("working_tree", [])
    if working_tree:
        lines.append("")
        lines.append("Working tree:")
        for change in working_tree:
            lines.append(f"- {change['status']} {change['path']}")

    plans = summary.get("workboard", {}).get("plans", [])
    if plans:
        lines.append("")
        lines.append("Plans:")
        for plan in plans:
            lines.append(f"- {plan['title']} [{plan['status']}]")

    tasks = summary.get("workboard", {}).get("tasks", [])
    if tasks:
        lines.append("")
        lines.append("Tasks:")
        for task in tasks:
            lines.append(f"- {task['title']} [{task['status']}] in {task['plan_title']} / {task['phase']}")

    sessions = summary.get("workboard", {}).get("sessions", [])
    if sessions:
        lines.append("")
        lines.append("Sessions:")
        for session in sessions:
            focus = session.get("current_focus") or session.get("next_action") or "No focus recorded"
            lines.append(f"- {session['actor_id']} [{session['status']}] {focus}")

    overlaps = summary.get("workboard", {}).get("session_overlaps", [])
    if overlaps:
        lines.append("")
        lines.append("Session overlaps:")
        for overlap in overlaps:
            details = []
            if overlap.get("shared_files"):
                details.append(f"files: {', '.join(overlap['shared_files'][:2])}")
            if overlap.get("shared_symbols"):
                details.append(f"symbols: {', '.join(overlap['shared_symbols'][:2])}")
            if overlap.get("same_task"):
                details.append("same task")
            lines.append(f"- {', '.join(overlap['actors'])}: {', '.join(details)}")

    unknown_children = summary.get("children", {}).get("unknown", [])
    if unknown_children:
        from .children import describe_child

        lines.append("")
        lines.append("Unknown children:")
        for child in unknown_children:
            lines.append(f"- {child['path']} [{describe_child(child)}]")

    notes = summary.get("workboard", {}).get("session_notes", [])
    if notes:
        lines.append("")
        lines.append("Session notes:")
        for note in notes:
            prefix = note.get("actor_id")
            if prefix:
                lines.append(f"- {prefix}: {note['note']}")
            else:
                lines.append(f"- {note['note']}")

    if len(lines) == 3:
        lines.append("")
        lines.append("No commits, working tree changes, or workboard updates were detected in this window.")

    return "\n".join(lines)
