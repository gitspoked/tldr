"""Peek module: zero-infrastructure reconnaissance of a file or directory.

Layer 0 — filesystem scan (disk stats, extension breakdown, line counts).
Later layers add context docs (1), indexed knowledge (2), live services (3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        "__pycache__",
        "target",
        ".venv",
        "venv",
        "dist",
        "build",
        ".tox",
    }
)

MAX_SCAN_DEPTH: int = 3
MAX_FILE_SIZE: int = 1_000_000  # bytes — skip binary/huge files for line counting


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def peek_target(path: Path | str) -> Dict[str, Any]:
    """Inspect *path* and return a structured reconnaissance dict.

    The returned dict always contains the keys listed in the spec so callers
    can rely on them without defensive get() checks.
    """
    target = Path(path).resolve()

    base: Dict[str, Any] = {
        "path": str(target),
        "type": "unknown",
        "stats": {},
        "enrichment_layers": {},
        "fallback_used": False,
        "indexed": False,
        "context_docs": [],
        "hot_symbols": [],
        "related": [],
        "generated_summary": None,
        "symbols": [],
        "project": None,
    }

    if target.is_dir():
        base["type"] = "directory"
        base["stats"], base["enrichment_layers"]["disk"] = _scan_directory(target)
    elif target.is_file():
        base["type"] = "file"
        base["stats"], base["enrichment_layers"]["disk"] = _scan_file(target)
    else:
        base["type"] = "missing"

    return base


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _walk_limited(root: Path, max_depth: int = MAX_SCAN_DEPTH) -> Iterator[Path]:
    """Yield file paths under *root*, honouring *max_depth* and skipping noise.

    Symlinks and SKIP_DIRS directories are skipped entirely.
    """
    def _recurse(directory: Path, current_depth: int) -> Iterator[Path]:
        if current_depth > max_depth:
            return
        try:
            entries = sorted(directory.iterdir())
        except PermissionError:
            return
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name in SKIP_DIRS:
                    continue
                yield from _recurse(entry, current_depth + 1)
            elif entry.is_file():
                yield entry

    yield from _recurse(root, 1)


def _count_lines(file_path: Path) -> int:
    """Return the number of newline-delimited lines in *file_path*.

    Returns 0 for files that are too large or cannot be decoded as UTF-8.
    """
    try:
        if file_path.stat().st_size > MAX_FILE_SIZE:
            return 0
        text = file_path.read_bytes()
        return text.count(b"\n") + (1 if text and not text.endswith(b"\n") else 0)
    except (OSError, PermissionError):
        return 0


def _scan_directory(target: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Walk *target* and collect file/line/extension stats.

    Returns ``(stats, disk_layer)`` where *disk_layer* is the raw evidence
    stored under ``enrichment_layers["disk"]``.
    """
    file_count = 0
    line_count = 0
    extensions: Dict[str, int] = {}

    for file_path in _walk_limited(target):
        file_count += 1
        ext = file_path.suffix.lstrip(".")
        if ext:
            extensions[ext] = extensions.get(ext, 0) + 1
        line_count += _count_lines(file_path)

    stats: Dict[str, Any] = {
        "files": file_count,
        "lines": line_count,
        "extensions": extensions,
    }
    disk_layer: Dict[str, Any] = {
        "scanned": True,
        "max_depth": MAX_SCAN_DEPTH,
    }
    return stats, disk_layer


def _scan_file(target: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Collect basic stats for a single *target* file.

    Returns ``(stats, disk_layer)``.
    """
    ext = target.suffix.lstrip(".")
    lines = _count_lines(target)
    size = 0
    try:
        size = target.stat().st_size
    except OSError:
        pass

    stats: Dict[str, Any] = {
        "files": 1,
        "lines": lines,
        "extensions": {ext: 1} if ext else {},
        "size_bytes": size,
    }
    disk_layer: Dict[str, Any] = {
        "scanned": True,
        "filename": target.name,
    }
    return stats, disk_layer
