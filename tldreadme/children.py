"""Detection and acknowledgment for nested child projects."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import os

from pydantic import BaseModel, Field
import yaml

WORK_ROOT = Path(".tldr/work")
CHILDREN_FILE = Path(".tldr/work/children.yaml")
SCHEMA_VERSION = 1
CHILDREN_DOCUMENT_TYPE = "tldreadme/children_registry"

ChildStatus = Literal["unknown", "merged", "ignored"]

IGNORED_CHILD_PARTS = {"node_modules", ".git", "__pycache__", "target", ".venv", "venv", "dist", "build", ".tldr"}
MANIFEST_FILES = {
    "Cargo.toml",
    "go.mod",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
}
CONTEXT_DOC_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "TLDREADME.md",
    "TLDR.md",
    "agents.md",
    "claude.md",
    "readme.md",
}
CODE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cjs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".lua",
    ".mjs",
    ".php",
    ".py",
    ".pyi",
    ".rb",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
    ".zig",
}


class ChildRecord(BaseModel):
    """A tracked nested child subtree."""

    schema_version: int = SCHEMA_VERSION
    path: str
    status: ChildStatus = "unknown"
    detected_at: str
    updated_at: str
    manifests: list[str] = Field(default_factory=list)
    context_docs: list[str] = Field(default_factory=list)
    has_git: bool = False
    code_file_count: int = 0
    note: str | None = None


class ChildRegistry(BaseModel):
    """Canonical child acknowledgment document."""

    schema_version: int = SCHEMA_VERSION
    document_type: str = CHILDREN_DOCUMENT_TYPE
    children: list[ChildRecord] = Field(default_factory=list)


def _now() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _repo_root(root: str | Path | None = None) -> Path:
    """Resolve the repository root used for child detection."""

    return Path(root or ".").resolve()


def _work_root(root: str | Path | None = None) -> Path:
    """Return the local work directory."""

    return _repo_root(root) / WORK_ROOT


def _children_file(root: str | Path | None = None) -> Path:
    """Return the children registry path."""

    return _repo_root(root) / CHILDREN_FILE


def _ensure_dirs(root: str | Path | None = None) -> None:
    """Create the work directory when missing."""

    _work_root(root).mkdir(parents=True, exist_ok=True)


def _dump_yaml(path: Path, payload: dict) -> None:
    """Write a YAML document with stable formatting."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")


def _load_registry(root: str | Path | None = None) -> ChildRegistry:
    """Load the children registry or return an empty one."""

    path = _children_file(root)
    if not path.exists():
        return ChildRegistry()

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return ChildRegistry()
    changed = False
    if data.get("schema_version") is None:
        data["schema_version"] = SCHEMA_VERSION
        changed = True
    if not data.get("document_type"):
        data["document_type"] = CHILDREN_DOCUMENT_TYPE
        changed = True
    registry = ChildRegistry.model_validate(data)
    if changed:
        _save_registry(registry, root)
    return registry


def _save_registry(registry: ChildRegistry, root: str | Path | None = None) -> None:
    """Persist the child registry."""

    _ensure_dirs(root)
    _dump_yaml(_children_file(root), registry.model_dump(mode="json"))


def _relative_child_path(path: Path, repo_root: Path) -> str:
    """Return a repository-relative path for a child directory."""

    resolved = path.resolve()
    relative = resolved.relative_to(repo_root)
    if not relative.parts:
        raise RuntimeError("The repository root cannot be tracked as a child.")
    return relative.as_posix()


def _resolve_child_dir(path: str | Path, repo_root: Path) -> Path:
    """Resolve and validate a child directory path."""

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (repo_root / candidate).resolve()
    else:
        candidate = candidate.resolve()

    if not candidate.exists():
        raise RuntimeError(f"Unknown child path `{path}`.")
    if not candidate.is_dir():
        raise RuntimeError(f"Child path `{path}` must be a directory.")
    try:
        candidate.relative_to(repo_root)
    except ValueError as exc:
        raise RuntimeError(f"Child path `{path}` is outside the repository root.") from exc
    if candidate == repo_root:
        raise RuntimeError("The repository root cannot be tracked as a child.")
    return candidate


def _count_code_files(directory: Path, *, limit: int = 500) -> int:
    """Count parseable code files under a subtree, capped for speed."""

    count = 0
    for current_root, dirnames, filenames in os.walk(directory):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_CHILD_PARTS]
        for name in filenames:
            if Path(name).suffix.lower() in CODE_SUFFIXES:
                count += 1
                if count >= limit:
                    return count
    return count


def _inspect_directory(directory: Path, repo_root: Path) -> dict | None:
    """Inspect a nested directory for child-project signals."""

    try:
        entries = list(directory.iterdir())
    except OSError:
        return None

    filenames = {entry.name for entry in entries if entry.is_file()}
    dirnames = {entry.name for entry in entries if entry.is_dir()}
    manifests = sorted(name for name in filenames if name in MANIFEST_FILES)
    context_docs = sorted(name for name in filenames if name in CONTEXT_DOC_FILES)
    has_git = ".git" in dirnames
    code_file_count = _count_code_files(directory)

    if not manifests and not has_git and not (context_docs and code_file_count >= 3):
        return None

    return {
        "path": _relative_child_path(directory, repo_root),
        "manifests": manifests,
        "context_docs": context_docs,
        "has_git": has_git,
        "code_file_count": code_file_count,
    }


def _detect_children(root: str | Path | None = None) -> list[dict]:
    """Detect shallow nested child projects under the repository root."""

    repo_root = _repo_root(root)
    candidates: list[tuple[Path, dict]] = []

    for current_root, dirnames, _filenames in os.walk(repo_root):
        current = Path(current_root).resolve()
        dirnames[:] = [name for name in dirnames if name not in IGNORED_CHILD_PARTS]
        if current == repo_root:
            continue
        inspected = _inspect_directory(current, repo_root)
        if inspected:
            candidates.append((current, inspected))

    selected: list[tuple[Path, dict]] = []
    for current, payload in sorted(candidates, key=lambda item: (len(item[0].relative_to(repo_root).parts), str(item[0]))):
        if any(parent == existing for parent in current.parents for existing, _meta in selected if parent != repo_root):
            continue
        selected.append((current, payload))

    return [payload for _, payload in selected]


def _manual_payload(directory: Path, repo_root: Path) -> dict:
    """Build a minimal manual child payload for an acknowledged subtree."""

    return {
        "path": _relative_child_path(directory, repo_root),
        "manifests": [],
        "context_docs": [],
        "has_git": (directory / ".git").exists(),
        "code_file_count": _count_code_files(directory),
    }


def _status_sort_key(status: str) -> int:
    """Return a stable sort order for child statuses."""

    order = {"unknown": 0, "merged": 1, "ignored": 2}
    return order.get(status, 99)


def _sync_registry(root: str | Path | None = None) -> ChildRegistry:
    """Refresh the child registry from the current filesystem state."""

    repo_root = _repo_root(root)
    registry = _load_registry(repo_root)
    stored = {child.path: child for child in registry.children}
    refreshed: list[ChildRecord] = []
    changed = False
    timestamp = _now()

    for payload in _detect_children(repo_root):
        existing = stored.pop(payload["path"], None)
        if existing is None:
            refreshed.append(
                ChildRecord(
                    path=payload["path"],
                    status="unknown",
                    detected_at=timestamp,
                    updated_at=timestamp,
                    manifests=payload["manifests"],
                    context_docs=payload["context_docs"],
                    has_git=payload["has_git"],
                    code_file_count=payload["code_file_count"],
                )
            )
            changed = True
            continue

        metadata_changed = any(
            [
                existing.manifests != payload["manifests"],
                existing.context_docs != payload["context_docs"],
                existing.has_git != payload["has_git"],
                existing.code_file_count != payload["code_file_count"],
            ]
        )
        existing.manifests = payload["manifests"]
        existing.context_docs = payload["context_docs"]
        existing.has_git = payload["has_git"]
        existing.code_file_count = payload["code_file_count"]
        if metadata_changed:
            existing.updated_at = timestamp
            changed = True
        refreshed.append(existing)

    for child in stored.values():
        child_path = repo_root / child.path
        if child_path.exists():
            refreshed.append(child)
        else:
            changed = True

    refreshed.sort(key=lambda item: (_status_sort_key(item.status), item.path))
    registry.children = refreshed
    if changed or (registry.children and not _children_file(repo_root).exists()):
        _save_registry(registry, repo_root)
    return registry


def _child_payload(child: ChildRecord) -> dict:
    """Return a JSON-ready child payload."""

    return child.model_dump(mode="json")


def describe_child(child: dict) -> str:
    """Return a concise human description for a child subtree."""

    parts: list[str] = []
    if child.get("manifests"):
        parts.append(f"manifests: {', '.join(child['manifests'][:3])}")
    if child.get("has_git"):
        parts.append("nested git repo")
    if child.get("context_docs"):
        parts.append(f"docs: {', '.join(child['context_docs'][:3])}")
    if child.get("code_file_count"):
        parts.append(f"{child['code_file_count']} code files")
    if child.get("note"):
        parts.append(f"note: {child['note']}")
    return "; ".join(parts) or "No child signals recorded."


def list_children(
    *,
    root: str | Path | None = None,
    status: ChildStatus | None = None,
    include_ignored: bool = False,
    refresh: bool = True,
) -> dict:
    """List detected child subtrees and their acknowledgment state."""

    repo_root = _repo_root(root)
    registry = _sync_registry(repo_root) if refresh else _load_registry(repo_root)
    children = list(registry.children)
    if status:
        children = [child for child in children if child.status == status]
    elif not include_ignored:
        children = [child for child in children if child.status != "ignored"]

    return {
        "root": str(repo_root),
        "count": len(children),
        "unknown_count": sum(1 for child in registry.children if child.status == "unknown"),
        "merged_count": sum(1 for child in registry.children if child.status == "merged"),
        "ignored_count": sum(1 for child in registry.children if child.status == "ignored"),
        "children": [_child_payload(child) for child in children],
    }


def _set_child_status(
    path: str | Path,
    *,
    status: ChildStatus,
    root: str | Path | None = None,
    note: str | None = None,
) -> dict:
    """Persist an acknowledgment status for a child subtree."""

    repo_root = _repo_root(root)
    target = _resolve_child_dir(path, repo_root)
    relative = _relative_child_path(target, repo_root)
    registry = _sync_registry(repo_root)
    children = {child.path: child for child in registry.children}
    child = children.get(relative)

    if child is None:
        payload = _inspect_directory(target, repo_root) or _manual_payload(target, repo_root)
        timestamp = _now()
        child = ChildRecord(
            path=payload["path"],
            status=status,
            detected_at=timestamp,
            updated_at=timestamp,
            manifests=payload["manifests"],
            context_docs=payload["context_docs"],
            has_git=payload["has_git"],
            code_file_count=payload["code_file_count"],
            note=note,
        )
        registry.children.append(child)
    else:
        child.status = status
        child.updated_at = _now()
        if note is not None:
            child.note = note

    registry.children.sort(key=lambda item: (_status_sort_key(item.status), item.path))
    _save_registry(registry, repo_root)
    return _child_payload(child)


def merge_child(path: str | Path, *, root: str | Path | None = None, note: str | None = None) -> dict:
    """Mark a child subtree as intentionally merged into the repository."""

    return _set_child_status(path, status="merged", root=root, note=note)


def ignore_child(path: str | Path, *, root: str | Path | None = None, note: str | None = None) -> dict:
    """Mark a child subtree as intentionally ignored."""

    return _set_child_status(path, status="ignored", root=root, note=note)
