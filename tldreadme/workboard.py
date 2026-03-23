"""File-backed plan and task management for repository work."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4
import hashlib
import os
import re
import subprocess

from pydantic import BaseModel, Field
import yaml

WORK_ROOT = Path(".tldr/work")
PLANS_DIR = "plans"
SESSIONS_DIR = "sessions"
LEGACY_SESSION = "current.yaml"
CURRENT_SESSION_PREFIX = "current."
CURRENT_SESSION_SUFFIX = ".yaml"
SCHEMA_VERSION = 1

PlanStatus = Literal["pending", "in_progress", "blocked", "done", "archived"]
TaskStatus = Literal["pending", "in_progress", "blocked", "done"]
Priority = Literal["low", "medium", "high", "critical"]
SessionStatus = Literal["active", "paused", "blocked", "done", "archived"]


class SessionNote(BaseModel):
    """A timestamped session note."""

    timestamp: str
    note: str
    plan_id: str | None = None
    phase: str | None = None


class TaskRecord(BaseModel):
    """A single executable work item."""

    id: str
    title: str
    phase: str
    status: TaskStatus = "pending"
    priority: Priority = "medium"
    depends_on: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    next_step: str | None = None
    created_at: str
    updated_at: str


class PhaseRecord(BaseModel):
    """A named phase of a plan."""

    name: str
    status: TaskStatus = "pending"
    tasks: list[TaskRecord] = Field(default_factory=list)


class PlanRecord(BaseModel):
    """A durable plan containing phased tasks."""

    schema_version: int = SCHEMA_VERSION
    id: str
    title: str
    status: PlanStatus = "pending"
    goal: str
    scope: list[str] = Field(default_factory=list)
    owner: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    phases: list[PhaseRecord] = Field(default_factory=list)
    created_at: str
    updated_at: str
    archived_at: str | None = None


class SessionState(BaseModel):
    """Local session scratch state for resuming work."""

    schema_version: int = SCHEMA_VERSION
    session_id: str
    actor_id: str
    workspace_id: str
    workspace_root: str
    repo_id: str
    repo_root: str
    family_id: str
    family_root: str
    status: SessionStatus = "active"
    goal: str | None = None
    current_plan_id: str | None = None
    current_task_id: str | None = None
    current_phase: str | None = None
    current_focus: str | None = None
    next_action: str | None = None
    claimed_files: list[str] = Field(default_factory=list)
    claimed_symbols: list[str] = Field(default_factory=list)
    verification_commands: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    recent_steps: list[str] = Field(default_factory=list)
    notes: list[SessionNote] = Field(default_factory=list)
    forked_from: str | None = None
    started_at: str | None = None
    updated_at: str


def _now() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(value: str) -> str:
    """Create a filename-safe slug."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "plan"


def _unique(existing: list[str], additions: list[str] | None) -> list[str]:
    """Append unique string values while keeping order."""

    items = list(existing)
    for item in additions or []:
        if item not in items:
            items.append(item)
    return items


def _root(root: str | Path | None = None) -> Path:
    """Resolve the workboard root directory."""

    return Path(root) if root is not None else WORK_ROOT


def _workspace_root(root: str | Path | None = None) -> Path:
    """Resolve the workspace root from the workboard root path."""

    work_root = _root(root).resolve()
    if work_root.name == "work" and work_root.parent.name == ".tldr":
        return work_root.parent.parent
    return work_root.parent


def _plans_dir(root: str | Path | None = None) -> Path:
    return _root(root) / PLANS_DIR


def _sessions_dir(root: str | Path | None = None) -> Path:
    return _root(root) / SESSIONS_DIR


def _ensure_dirs(root: str | Path | None = None) -> None:
    """Create the workboard directories when missing."""

    _plans_dir(root).mkdir(parents=True, exist_ok=True)
    _sessions_dir(root).mkdir(parents=True, exist_ok=True)


def _plan_file(plan_id: str, root: str | Path | None = None) -> Path:
    return _plans_dir(root) / f"{plan_id}.yaml"


def _session_file(session_id: str, root: str | Path | None = None) -> Path:
    return _sessions_dir(root) / f"{CURRENT_SESSION_PREFIX}{session_id}{CURRENT_SESSION_SUFFIX}"


def _legacy_session_file(root: str | Path | None = None) -> Path:
    return _sessions_dir(root) / LEGACY_SESSION


def _load_yaml(path: Path) -> dict:
    """Load a YAML file into a dictionary."""

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid workboard document in {path}")
    return data


def _dump_yaml(path: Path, payload: dict) -> None:
    """Write a YAML file with stable formatting."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")


def _load_plan(plan_id: str, root: str | Path | None = None) -> PlanRecord:
    path = _plan_file(plan_id, root)
    if not path.exists():
        raise RuntimeError(f"Unknown plan `{plan_id}`.")
    return PlanRecord.model_validate(_load_yaml(path))


def _save_plan(plan: PlanRecord, root: str | Path | None = None) -> None:
    _dump_yaml(_plan_file(plan.id, root), plan.model_dump(mode="json"))


def _hash_id(*parts: str, length: int = 12) -> str:
    """Create a short stable identifier from one or more strings."""

    digest = hashlib.sha1("::".join(parts).encode("utf-8")).hexdigest()
    return digest[:length]


def _git_root(path: Path) -> Path:
    """Return the git root when available, otherwise the workspace root."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return path

    if completed.returncode != 0:
        return path

    stdout = completed.stdout.strip()
    return Path(stdout).resolve() if stdout else path


def _family_root(repo_root: Path) -> Path:
    """Return the family root used to group nearby repositories."""

    configured = os.getenv("TLDREADME_FAMILY_ROOT")
    if configured:
        return Path(configured).resolve()
    return repo_root.parent.resolve()


def _default_actor_id() -> str:
    """Return the default actor id for session ownership."""

    return os.getenv("TLDREADME_ACTOR_ID", "cli")


def _session_identity(root: str | Path | None = None, *, actor_id: str | None = None) -> dict[str, str]:
    """Build workspace, repo, and family identifiers for a session."""

    workspace_root = _workspace_root(root)
    repo_root = _git_root(workspace_root)
    family_root = _family_root(repo_root)
    actor = actor_id or _default_actor_id()
    return {
        "actor_id": actor,
        "workspace_root": str(workspace_root),
        "workspace_id": _hash_id(str(workspace_root)),
        "repo_root": str(repo_root),
        "repo_id": _hash_id(str(repo_root)),
        "family_root": str(family_root),
        "family_id": _hash_id(str(family_root)),
    }


def _default_session_id(root: str | Path | None = None, *, actor_id: str | None = None) -> str:
    """Return the default canonical session id for an actor/workspace pair."""

    explicit = os.getenv("TLDREADME_SESSION_ID")
    if explicit:
        return explicit
    identity = _session_identity(root, actor_id=actor_id)
    return f"{_slug(identity['actor_id'])}-{identity['workspace_id']}"


def _session_summary(session: SessionState, current_session_id: str | None = None) -> dict:
    """Return a compact session summary."""

    return {
        "session_id": session.session_id,
        "actor_id": session.actor_id,
        "status": session.status,
        "goal": session.goal,
        "current_plan_id": session.current_plan_id,
        "current_task_id": session.current_task_id,
        "current_phase": session.current_phase,
        "current_focus": session.current_focus,
        "next_action": session.next_action,
        "workspace_id": session.workspace_id,
        "repo_id": session.repo_id,
        "family_id": session.family_id,
        "claimed_files": session.claimed_files,
        "claimed_symbols": session.claimed_symbols,
        "updated_at": session.updated_at,
        "is_current": session.session_id == current_session_id,
    }


def _session_relation(session: SessionState, other: SessionState) -> str:
    """Describe how two sessions are related."""

    if session.workspace_id == other.workspace_id:
        return "same_workspace"
    if session.repo_id == other.repo_id:
        return "same_repo"
    if session.family_id == other.family_id:
        return "same_family"
    return "unrelated"


def _session_overlap(session: SessionState, other: SessionState) -> dict | None:
    """Return overlap details between two sessions when relevant."""

    shared_files = sorted(set(session.claimed_files) & set(other.claimed_files))
    shared_symbols = sorted(set(session.claimed_symbols) & set(other.claimed_symbols))
    same_task = session.current_task_id and session.current_task_id == other.current_task_id
    if not shared_files and not shared_symbols and not same_task:
        return None
    return {
        "session_id": other.session_id,
        "actor_id": other.actor_id,
        "relation": _session_relation(session, other),
        "shared_files": shared_files,
        "shared_symbols": shared_symbols,
        "same_task": same_task,
        "other_next_action": other.next_action,
        "other_current_focus": other.current_focus,
    }


def _new_session(root: str | Path | None = None, *, session_id: str | None = None, actor_id: str | None = None) -> SessionState:
    """Create an in-memory session state with canonical identity fields."""

    identity = _session_identity(root, actor_id=actor_id)
    timestamp = _now()
    return SessionState(
        session_id=session_id or _default_session_id(root, actor_id=identity["actor_id"]),
        started_at=timestamp,
        updated_at=timestamp,
        **identity,
    )


def _session_paths(root: str | Path | None = None) -> list[Path]:
    """Return canonical live session files."""

    paths = sorted(_sessions_dir(root).glob(f"{CURRENT_SESSION_PREFIX}*{CURRENT_SESSION_SUFFIX}"))
    legacy = _legacy_session_file(root)
    if legacy.exists():
        paths.append(legacy)
    return paths


def _load_session_path(path: Path, root: str | Path | None = None, *, actor_id: str | None = None) -> SessionState:
    """Load a session file and normalize legacy fields."""

    data = _load_yaml(path)
    if path.name == LEGACY_SESSION:
        session = _new_session(root, actor_id=actor_id)
        session.current_plan_id = data.get("current_plan_id")
        session.current_phase = data.get("current_phase")
        session.notes = [SessionNote.model_validate(note) for note in data.get("notes", [])]
        session.updated_at = data.get("updated_at", session.updated_at)
        session.started_at = data.get("updated_at", session.started_at)
        _save_session(session, root)
        return session

    session = SessionState.model_validate(data)
    if not session.started_at:
        session.started_at = session.updated_at
    return session


def _list_sessions(root: str | Path | None = None) -> list[SessionState]:
    """Return all live sessions sorted by recency."""

    _ensure_dirs(root)
    deduped: dict[str, SessionState] = {}
    for path in _session_paths(root):
        session = _load_session_path(path, root)
        existing = deduped.get(session.session_id)
        if existing is None or session.updated_at >= existing.updated_at:
            deduped[session.session_id] = session
    sessions = list(deduped.values())
    sessions.sort(key=lambda item: item.updated_at, reverse=True)
    return sessions


def _load_session(
    root: str | Path | None = None,
    *,
    session_id: str | None = None,
    actor_id: str | None = None,
    create: bool = True,
) -> SessionState | None:
    """Load the active session for the given selector or create a new one."""

    resolved_actor = actor_id or _default_actor_id()
    resolved_id = session_id or os.getenv("TLDREADME_SESSION_ID")
    sessions = _list_sessions(root)

    if resolved_id:
        for session in sessions:
            if session.session_id == resolved_id:
                return session
        return _new_session(root, session_id=resolved_id, actor_id=resolved_actor) if create else None

    workspace_root = str(_workspace_root(root))
    for session in sessions:
        if session.actor_id == resolved_actor and session.workspace_root == workspace_root:
            return session

    default_session = _default_session_id(root, actor_id=resolved_actor)
    for session in sessions:
        if session.session_id == default_session:
            return session

    return _new_session(root, session_id=default_session, actor_id=resolved_actor) if create else None


def _save_session(session: SessionState, root: str | Path | None = None) -> None:
    """Persist a canonical live session file."""

    if not session.started_at:
        session.started_at = session.updated_at or _now()
    _dump_yaml(_session_file(session.session_id, root), session.model_dump(mode="json"))


def _get_phase(plan: PlanRecord, phase_name: str) -> PhaseRecord:
    for phase in plan.phases:
        if phase.name == phase_name:
            return phase
    phase = PhaseRecord(name=phase_name)
    plan.phases.append(phase)
    return phase


def _find_task(plan: PlanRecord, task_id: str) -> tuple[PhaseRecord, TaskRecord]:
    for phase in plan.phases:
        for task in phase.tasks:
            if task.id == task_id:
                return phase, task
    raise RuntimeError(f"Unknown task `{task_id}` in plan `{plan.id}`.")


def _refresh_phase_status(phase: PhaseRecord) -> None:
    """Derive phase status from the status of its tasks."""

    if not phase.tasks:
        phase.status = "pending"
        return

    statuses = {task.status for task in phase.tasks}
    if statuses == {"done"}:
        phase.status = "done"
    elif "blocked" in statuses:
        phase.status = "blocked"
    elif "in_progress" in statuses:
        phase.status = "in_progress"
    else:
        phase.status = "pending"


def _refresh_plan_status(plan: PlanRecord) -> None:
    """Derive plan status from its phases unless archived."""

    if plan.status == "archived":
        return

    for phase in plan.phases:
        _refresh_phase_status(phase)

    statuses = {phase.status for phase in plan.phases}
    if statuses and statuses == {"done"}:
        plan.status = "done"
    elif "blocked" in statuses:
        plan.status = "blocked"
    elif "in_progress" in statuses:
        plan.status = "in_progress"
    else:
        plan.status = "pending"


def _plan_summary(plan: PlanRecord) -> dict:
    """Return a compact summary for plan listings."""

    total_tasks = sum(len(phase.tasks) for phase in plan.phases)
    done_tasks = sum(1 for phase in plan.phases for task in phase.tasks if task.status == "done")
    return {
        "id": plan.id,
        "title": plan.title,
        "status": plan.status,
        "goal": plan.goal,
        "owner": plan.owner,
        "phase_count": len(plan.phases),
        "task_count": total_tasks,
        "completed_task_count": done_tasks,
        "updated_at": plan.updated_at,
    }


def create_plan(
    title: str,
    goal: str,
    *,
    scope: list[str] | None = None,
    owner: str | None = None,
    phases: list[str] | None = None,
    success_criteria: list[str] | None = None,
    risks: list[str] | None = None,
    notes: list[str] | None = None,
    root: str | Path | None = None,
    set_current: bool = True,
) -> dict:
    """Create and persist a new plan."""

    _ensure_dirs(root)
    created_at = _now()
    plan_id = f"{_slug(title)}-{uuid4().hex[:8]}"
    plan = PlanRecord(
        id=plan_id,
        title=title,
        goal=goal,
        scope=list(scope or []),
        owner=owner,
        success_criteria=list(success_criteria or []),
        risks=list(risks or []),
        notes=list(notes or []),
        phases=[PhaseRecord(name=name) for name in (phases or ["Backlog"])],
        created_at=created_at,
        updated_at=created_at,
    )
    _save_plan(plan, root)

    if set_current:
        set_current_plan(plan.id, phase=plan.phases[0].name if plan.phases else None, root=root)

    return plan.model_dump(mode="json")


def list_plans(*, status: PlanStatus | None = None, root: str | Path | None = None) -> dict:
    """List plan summaries."""

    _ensure_dirs(root)
    plans = [_load_plan(path.stem, root) for path in _plans_dir(root).glob("*.yaml")]
    if status:
        plans = [plan for plan in plans if plan.status == status]
    plans.sort(key=lambda plan: plan.updated_at, reverse=True)

    session = _load_session(root, create=False)
    return {
        "count": len(plans),
        "current_plan_id": session.current_plan_id if session else None,
        "plans": [_plan_summary(plan) for plan in plans],
    }


def get_plan(plan_id: str, *, root: str | Path | None = None) -> dict:
    """Return a full plan document."""

    return _load_plan(plan_id, root).model_dump(mode="json")


def update_plan(
    plan_id: str,
    *,
    title: str | None = None,
    status: PlanStatus | None = None,
    owner: str | None = None,
    goal: str | None = None,
    add_scope: list[str] | None = None,
    add_success_criteria: list[str] | None = None,
    add_risks: list[str] | None = None,
    add_notes: list[str] | None = None,
    current_phase: str | None = None,
    root: str | Path | None = None,
) -> dict:
    """Update top-level plan fields."""

    plan = _load_plan(plan_id, root)
    if title:
        plan.title = title
    if status:
        plan.status = status
        if status == "archived" and not plan.archived_at:
            plan.archived_at = _now()
    if owner is not None:
        plan.owner = owner
    if goal:
        plan.goal = goal
    plan.scope = _unique(plan.scope, add_scope)
    plan.success_criteria = _unique(plan.success_criteria, add_success_criteria)
    plan.risks = _unique(plan.risks, add_risks)
    plan.notes = _unique(plan.notes, add_notes)
    if current_phase:
        _get_phase(plan, current_phase)
    if status is None and plan.status != "archived":
        _refresh_plan_status(plan)
    plan.updated_at = _now()
    _save_plan(plan, root)

    session = _load_session(root, create=False)
    if current_phase or (session and session.current_plan_id == plan.id):
        set_current_plan(plan.id, phase=current_phase or session.current_phase, root=root)

    return plan.model_dump(mode="json")


def add_task(
    plan_id: str,
    title: str,
    *,
    phase: str = "Backlog",
    priority: Priority = "medium",
    depends_on: list[str] | None = None,
    files: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    verification_commands: list[str] | None = None,
    notes: list[str] | None = None,
    next_step: str | None = None,
    root: str | Path | None = None,
    set_current: bool = True,
) -> dict:
    """Add a task to a phase in a plan."""

    plan = _load_plan(plan_id, root)
    phase_record = _get_phase(plan, phase)
    created_at = _now()
    task = TaskRecord(
        id=f"{_slug(title)}-{uuid4().hex[:8]}",
        title=title,
        phase=phase_record.name,
        priority=priority,
        depends_on=list(depends_on or []),
        files=list(files or []),
        acceptance_criteria=list(acceptance_criteria or []),
        verification_commands=list(verification_commands or []),
        notes=list(notes or []),
        next_step=next_step,
        created_at=created_at,
        updated_at=created_at,
    )
    phase_record.tasks.append(task)
    _refresh_plan_status(plan)
    plan.updated_at = _now()
    _save_plan(plan, root)

    if set_current:
        set_current_plan(plan.id, phase=phase_record.name, root=root)

    return task.model_dump(mode="json")


def update_task(
    plan_id: str,
    task_id: str,
    *,
    status: TaskStatus | None = None,
    priority: Priority | None = None,
    next_step: str | None = None,
    add_blockers: list[str] | None = None,
    add_evidence: list[str] | None = None,
    add_notes: list[str] | None = None,
    add_files: list[str] | None = None,
    add_acceptance_criteria: list[str] | None = None,
    add_verification_commands: list[str] | None = None,
    root: str | Path | None = None,
) -> dict:
    """Update a task within a plan."""

    plan = _load_plan(plan_id, root)
    phase, task = _find_task(plan, task_id)

    if status:
        task.status = status
    if priority:
        task.priority = priority
    if next_step is not None:
        task.next_step = next_step
    task.blockers = _unique(task.blockers, add_blockers)
    task.evidence = _unique(task.evidence, add_evidence)
    task.notes = _unique(task.notes, add_notes)
    task.files = _unique(task.files, add_files)
    task.acceptance_criteria = _unique(task.acceptance_criteria, add_acceptance_criteria)
    task.verification_commands = _unique(task.verification_commands, add_verification_commands)
    task.updated_at = _now()
    _refresh_phase_status(phase)
    _refresh_plan_status(plan)
    plan.updated_at = _now()
    _save_plan(plan, root)

    return task.model_dump(mode="json")


def complete_task(
    plan_id: str,
    task_id: str,
    *,
    evidence: list[str] | None = None,
    note: str | None = None,
    root: str | Path | None = None,
) -> dict:
    """Mark a task done and attach evidence."""

    notes = [note] if note else None
    return update_task(
        plan_id,
        task_id,
        status="done",
        add_evidence=evidence,
        add_notes=notes,
        root=root,
    )


def archive_plan(plan_id: str, *, root: str | Path | None = None) -> dict:
    """Archive a plan without deleting it."""

    plan = _load_plan(plan_id, root)
    plan.status = "archived"
    plan.archived_at = _now()
    plan.updated_at = plan.archived_at
    _save_plan(plan, root)
    return plan.model_dump(mode="json")


def get_task(plan_id: str, task_id: str, *, root: str | Path | None = None) -> dict:
    """Return a single task plus plan context."""

    plan = _load_plan(plan_id, root)
    phase, task = _find_task(plan, task_id)
    payload = task.model_dump(mode="json")
    payload["plan_id"] = plan.id
    payload["plan_title"] = plan.title
    payload["phase"] = phase.name
    return payload


def set_current_plan(
    plan_id: str,
    *,
    phase: str | None = None,
    current_task_id: str | None = None,
    current_focus: str | None = None,
    next_action: str | None = None,
    session_id: str | None = None,
    actor_id: str | None = None,
    root: str | Path | None = None,
) -> dict:
    """Set the local current plan pointer."""

    _ensure_dirs(root)
    session = _load_session(root, session_id=session_id, actor_id=actor_id)
    if session.current_plan_id != plan_id:
        session.current_task_id = None
        session.current_focus = None
        session.next_action = None
    session.current_plan_id = plan_id
    session.current_phase = phase
    if current_task_id is not None:
        session.current_task_id = current_task_id
    if current_focus is not None:
        session.current_focus = current_focus
    if next_action is not None:
        session.next_action = next_action
    session.updated_at = _now()
    _save_session(session, root)
    return session.model_dump(mode="json")


def add_session_note(
    note: str,
    *,
    plan_id: str | None = None,
    phase: str | None = None,
    session_id: str | None = None,
    actor_id: str | None = None,
    root: str | Path | None = None,
) -> dict:
    """Append a session note for short-term coordination."""

    _ensure_dirs(root)
    session = _load_session(root, session_id=session_id, actor_id=actor_id)
    session.notes.append(SessionNote(timestamp=_now(), note=note, plan_id=plan_id or session.current_plan_id, phase=phase or session.current_phase))
    session.recent_steps = _unique(session.recent_steps, [note])[-5:]
    if plan_id is not None:
        session.current_plan_id = plan_id
    if phase is not None:
        session.current_phase = phase
    session.updated_at = _now()
    _save_session(session, root)
    return session.model_dump(mode="json")


def update_session(
    *,
    session_id: str | None = None,
    actor_id: str | None = None,
    status: SessionStatus | None = None,
    goal: str | None = None,
    current_plan_id: str | None = None,
    current_task_id: str | None = None,
    current_phase: str | None = None,
    current_focus: str | None = None,
    next_action: str | None = None,
    claimed_files: list[str] | None = None,
    claimed_symbols: list[str] | None = None,
    verification_commands: list[str] | None = None,
    blockers: list[str] | None = None,
    recent_steps: list[str] | None = None,
    forked_from: str | None = None,
    root: str | Path | None = None,
) -> dict:
    """Update the canonical resumable session snapshot."""

    _ensure_dirs(root)
    session = _load_session(root, session_id=session_id, actor_id=actor_id)
    if status is not None:
        session.status = status
    if goal is not None:
        session.goal = goal
    if current_plan_id is not None:
        session.current_plan_id = current_plan_id
    if current_task_id is not None:
        session.current_task_id = current_task_id
    if current_phase is not None:
        session.current_phase = current_phase
    if current_focus is not None:
        session.current_focus = current_focus
    if next_action is not None:
        session.next_action = next_action
    if claimed_files is not None:
        session.claimed_files = _unique([], claimed_files)
    if claimed_symbols is not None:
        session.claimed_symbols = _unique([], claimed_symbols)
    if verification_commands is not None:
        session.verification_commands = _unique([], verification_commands)
    if blockers is not None:
        session.blockers = _unique([], blockers)
    if recent_steps is not None:
        session.recent_steps = _unique([], recent_steps)[-5:]
    if forked_from is not None:
        session.forked_from = forked_from
    session.updated_at = _now()
    _save_session(session, root)
    return session.model_dump(mode="json")


def list_sessions(*, root: str | Path | None = None) -> dict:
    """List live canonical sessions for the current workboard."""

    sessions = _list_sessions(root)
    return {
        "count": len(sessions),
        "sessions": [_session_summary(session) for session in sessions],
    }


def current_plan(
    *,
    root: str | Path | None = None,
    session_id: str | None = None,
    actor_id: str | None = None,
) -> dict:
    """Return the current session pointer plus the active plan, if any."""

    _ensure_dirs(root)
    session = _load_session(root, session_id=session_id, actor_id=actor_id)
    plan = None
    if session.current_plan_id:
        try:
            plan = _load_plan(session.current_plan_id, root)
        except RuntimeError:
            session.current_plan_id = None
            session.current_task_id = None
            session.current_phase = None

    if plan is None:
        listing = list_plans(root=root)
        candidate = next((item["id"] for item in listing["plans"] if item["status"] != "archived"), None)
        if candidate:
            plan = _load_plan(candidate, root)
            session.current_plan_id = candidate
            session.updated_at = _now()
            _save_session(session, root)

    active_sessions = []
    overlaps = []
    for other in _list_sessions(root):
        if other.session_id == session.session_id:
            continue
        relation = _session_relation(session, other)
        if relation == "unrelated":
            continue
        active_sessions.append(_session_summary(other, current_session_id=session.session_id) | {"relation": relation})
        overlap = _session_overlap(session, other)
        if overlap:
            overlaps.append(overlap)

    return {
        "session": session.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json") if plan else None,
        "summary": _plan_summary(plan) if plan else None,
        "active_sessions": active_sessions,
        "overlaps": overlaps,
    }
