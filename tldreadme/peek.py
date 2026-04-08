"""Peek module: zero-infrastructure reconnaissance of a file or directory.

Layer 0 — filesystem scan (disk stats, extension breakdown, line counts).
Layer 1 — context docs (README, CLAUDE.md, etc.) and manifest detection.
Layer 2 — indexed knowledge (.tldr/ hot index, generated summary).
Layer 3 — live services (Qdrant semantic search, FalkorDB call graph).
"""

from __future__ import annotations

import re
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
# Symbol extraction patterns
# ---------------------------------------------------------------------------

_SYMBOL_PATTERNS = [
    re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"),
    re.compile(r"^\s*class\s+(\w+)"),
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)"),
    re.compile(r"^\s*func\s+(\w+)"),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"),
    re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)"),
    re.compile(r"^\s*(?:pub\s+)?enum\s+(\w+)"),
    re.compile(r"^\s*interface\s+(\w+)"),
    re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)"),
]

_KIND_PATTERNS = {
    "class": re.compile(r"^\s*class\s"),
    "struct": re.compile(r"^\s*(?:pub\s+)?struct\s"),
    "enum": re.compile(r"^\s*(?:pub\s+)?enum\s"),
    "interface": re.compile(r"^\s*interface\s"),
    "trait": re.compile(r"^\s*(?:pub\s+)?trait\s"),
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def peek_target(path: Path | str) -> Dict[str, Any]:
    """Inspect *path* and return a structured reconnaissance dict.

    The returned dict always contains the keys listed in the spec so callers
    can rely on them without defensive get() checks.
    """
    target = Path(path).resolve()

    enrichment_layers: list[str] = []
    fallback_used: list[str] = []

    result: Dict[str, Any] = {
        "path": str(target),
        "type": "unknown",
        "stats": {},
        "enrichment_layers": enrichment_layers,
        "fallback_used": fallback_used,
        "indexed": False,
        "context_docs": [],
        "hot_symbols": [],
        "related": [],
        "generated_summary": None,
        "symbols": [],
        "project": None,
    }

    # Layer 0: filesystem scan
    if target.is_dir():
        result["type"] = "directory"
        result["stats"] = _scan_directory(target)
        enrichment_layers.append("disk")
    elif target.is_file():
        result["type"] = "file"
        stats, symbols = _scan_file(target)
        result["stats"] = stats
        result["symbols"] = symbols
        enrichment_layers.append("disk")
    else:
        result["type"] = "missing"
        return result

    # Layer 1: context docs and manifest detection
    try:
        context_docs, project = _enrich_context_docs(target)
        result["context_docs"] = context_docs
        result["project"] = project
        if context_docs:
            enrichment_layers.append("context_docs")
    except Exception:
        fallback_used.append("context_docs_failed")

    # Layer 2: indexed knowledge
    project_root = _find_project_root(target if target.is_dir() else target.parent)
    tldr_dir = project_root / ".tldr"
    if tldr_dir.is_dir():
        result["indexed"] = True
        enrichment_layers.append("tldr")

        hot_index_path = tldr_dir / "hot_index.json"
        if hot_index_path.exists():
            try:
                from .hot_index import HotIndex
                hot_idx = HotIndex.load(hot_index_path)
                if hot_idx:
                    result["hot_symbols"] = [
                        {
                            "name": e.name,
                            "kind": e.kind,
                            "file": (e.locations[0]["file"] if e.locations else ""),
                        }
                        for e in list(hot_idx.entries.values())[:20]
                    ]
            except Exception:
                fallback_used.append("hot_index_read_failed")

        tldr_md = project_root / ".claude" / "TLDR.md"
        if tldr_md.exists():
            try:
                result["generated_summary"] = tldr_md.read_text(errors="replace")
            except OSError:
                fallback_used.append("tldr_md_read_failed")

    # Layer 3: live services
    _enrich_live_services(target, result, enrichment_layers, fallback_used)

    return result


# ---------------------------------------------------------------------------
# Layer 0 helpers
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

    Counts lines for all readable files, using a streaming approach for
    files over MAX_FILE_SIZE to avoid loading them fully into memory.
    Returns 0 for files that cannot be read.
    """
    try:
        size = file_path.stat().st_size
        if size > MAX_FILE_SIZE:
            # Stream count for large files rather than loading into memory
            count = 0
            with file_path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    count += chunk.count(b"\n")
            return count if count > 0 else 1
        text = file_path.read_bytes()
        return text.count(b"\n") + (1 if text and not text.endswith(b"\n") else 0)
    except (OSError, PermissionError):
        return 0


def _scan_directory(target: Path) -> Dict[str, Any]:
    """Walk *target* and collect file/line/extension stats."""
    file_count = 0
    line_count = 0
    extensions: Dict[str, int] = {}

    for file_path in _walk_limited(target):
        file_count += 1
        ext = file_path.suffix.lstrip(".")
        if ext:
            extensions[ext] = extensions.get(ext, 0) + 1
        line_count += _count_lines(file_path)

    return {
        "files": file_count,
        "lines": line_count,
        "extensions": extensions,
    }


def _scan_file(target: Path) -> tuple[Dict[str, Any], list[dict]]:
    """Collect basic stats and symbols for a single *target* file.

    Returns ``(stats, symbols)``.
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
    symbols = _extract_symbols(target)
    return stats, symbols


# ---------------------------------------------------------------------------
# Symbol extraction helpers
# ---------------------------------------------------------------------------


def _extract_symbols_regex(text: str) -> list[dict]:
    """Extract symbols from source text using regex patterns."""
    symbols: list[dict] = []
    for i, line in enumerate(text.splitlines(), 1):
        for pattern in _SYMBOL_PATTERNS:
            m = pattern.match(line)
            if m:
                name = m.group(1)
                kind = "function"
                for k, kp in _KIND_PATTERNS.items():
                    if kp.match(line):
                        kind = k
                        break
                symbols.append({"name": name, "kind": kind, "line": i})
                break
    return symbols


def _extract_symbols(target: Path) -> list[dict]:
    """Extract symbols from *target* file, using tree-sitter if available."""
    if target.stat().st_size > MAX_FILE_SIZE:
        return []
    try:
        from .asts import parse_file
        result = parse_file(target, isolate=False)
        if result and result.symbols:
            return [{"name": s.name, "kind": s.kind, "line": s.line} for s in result.symbols]
    except Exception:
        pass
    try:
        text = target.read_text(errors="replace")
    except OSError:
        return []
    return _extract_symbols_regex(text)


# ---------------------------------------------------------------------------
# Layer 1: context docs and manifest detection
# ---------------------------------------------------------------------------


def _find_project_root(directory: Path) -> Path:
    """Walk up the directory tree to find the project root (manifest file)."""
    manifest_names = {"Cargo.toml", "package.json", "go.mod", "pyproject.toml", "setup.py"}
    current = directory
    while current != current.parent:
        if any((current / name).exists() for name in manifest_names):
            return current
        current = current.parent
    return directory


def _enrich_context_docs(target: Path) -> tuple[list[dict], dict | None]:
    """Scan for context docs and manifest info relative to *target*."""
    from .context_docs import scan_context_docs
    from .deps import extract_deps_from_directory

    scan_root = target.parent if target.is_file() else target
    project_root = _find_project_root(scan_root)
    if project_root != scan_root:
        scan_root = project_root

    raw_docs = scan_context_docs(scan_root)
    context_docs = []
    for doc in raw_docs:
        summary = ""
        for section in doc.sections:
            content = section.get("content", "").strip()
            if content:
                first_para = content.split("\n\n")[0]
                summary = first_para[:200]
                break
        context_docs.append({"kind": doc.kind, "title": doc.title, "summary": summary})

    project = None
    deps_list = extract_deps_from_directory(scan_root)
    if deps_list:
        nearest = min(deps_list, key=lambda d: len(Path(d.manifest_file).parts))
        project = {
            "name": nearest.project_name,
            "version": nearest.project_version,
            "manifest": Path(nearest.manifest_file).name,
        }

    return context_docs, project


# ---------------------------------------------------------------------------
# Layer 3: live services
# ---------------------------------------------------------------------------


def _enrich_live_services(
    target: Path,
    result: Dict[str, Any],
    enrichment_layers: list[str],
    fallback_used: list[str],
) -> None:
    """Probe Qdrant and FalkorDB; populate related symbols if reachable."""
    import os

    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    falkordb_url = os.getenv("FALKORDB_URL", "redis://localhost:6379")

    try:
        import httpx
        resp = httpx.get(f"{qdrant_url}/collections", timeout=1.0)
        if resp.status_code == 200:
            enrichment_layers.append("qdrant")
            try:
                from ._shared import get_embedder
                embedder = get_embedder()
                query = target.stem if target.is_file() else target.name
                similar = embedder.search_similar(query, limit=5)
                for chunk in similar:
                    result["related"].append({
                        "name": chunk.get("symbol_name", chunk.get("name", "")),
                        "relationship": "semantically similar",
                        "file": chunk.get("file", ""),
                    })
            except Exception:
                fallback_used.append("qdrant_search_failed")
    except Exception:
        fallback_used.append("qdrant_unavailable")

    try:
        from urllib.parse import urlparse
        parsed = urlparse(falkordb_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect((host, port))
        sock.sendall(b"PING\r\n")
        response = sock.recv(64)
        sock.close()
        if b"PONG" in response:
            enrichment_layers.append("falkordb")
            if target.is_file():
                try:
                    from ._shared import get_grapher
                    grapher = get_grapher()
                    for sym in result.get("symbols", [])[:5]:
                        callers = grapher.get_callers(sym["name"])
                        for c in callers[:3]:
                            result["related"].append({
                                "name": c.get("name", ""),
                                "relationship": "calls this",
                                "file": c.get("file", ""),
                            })
                except Exception:
                    fallback_used.append("falkordb_query_failed")
    except Exception:
        fallback_used.append("falkordb_unavailable")


# ---------------------------------------------------------------------------
# Rendering functions
# ---------------------------------------------------------------------------


def render_peek(result: dict) -> str:
    """Render a peek result as a compact terminal-friendly string."""
    lines: list[str] = []
    path = Path(result["path"])
    project = result.get("project")
    stats = result["stats"]

    if result["type"] == "directory":
        if project:
            lines.append(f"{project['name']} ({project['manifest']})")
            version_part = (
                f"v{project['version']}"
                if project.get("version") and project["version"] != "0.0.0"
                else ""
            )
            parts = [
                p for p in [version_part, f"{stats['files']} files", f"{stats['lines']:,} lines"]
                if p
            ]
            lines.append(f"   {' . '.join(parts)}")
        else:
            lines.append(f"{path.name}")
            lines.append(f"   {stats['files']} files . {stats['lines']:,} lines")

        exts = stats.get("extensions", {})
        if exts:
            ext_parts = [f"{k}: {v}" for k, v in list(exts.items())[:8]]
            lines.append(f"   {'  '.join(ext_parts)}")
    else:
        ext = path.suffix.lstrip(".")
        lang = ext.upper() if ext else "Unknown"
        lines.append(f"{path.name} ({lang} . {stats['lines']:,} lines)")
        if project:
            lines.append(f"   Part of: {project['name']} v{project.get('version', '')}")

    context_docs = result.get("context_docs", [])
    readme_doc = next((d for d in context_docs if d["kind"] == "readme"), None)
    if readme_doc and readme_doc.get("summary"):
        lines.append("")
        lines.append("-- What It Is " + "-" * 40)
        lines.append(f"{readme_doc['summary']}")

    other_docs = [d for d in context_docs if d["kind"] != "readme"]
    if other_docs:
        lines.append("")
        lines.append("-- Context Docs " + "-" * 38)
        for doc in other_docs:
            summary_hint = doc.get("summary", "")[:60]
            lines.append(f"{doc['title']:<16} {summary_hint}")

    symbols = result.get("symbols", [])
    if symbols:
        lines.append("")
        lines.append("-- Defines " + "-" * 43)
        for sym in symbols[:15]:
            lines.append(f"{sym['kind']:<10} {sym['name']:<24} line {sym['line']}")

    lines.append("")
    lines.append("-- Indexed? " + "-" * 42)
    if result.get("indexed"):
        hot_count = len(result.get("hot_symbols", []))
        lines.append(f".tldr/ exists . hot index: {hot_count} symbols")
        if result.get("generated_summary"):
            lines.append(".claude/TLDR.md generated")
    else:
        lines.append("Not indexed — run `tldr init` for full analysis")

    layers = result.get("enrichment_layers", [])
    if "qdrant" not in layers:
        lines.append("Qdrant unavailable")
    if "falkordb" not in layers:
        lines.append("FalkorDB unavailable")

    related = result.get("related", [])
    if related:
        lines.append("")
        lines.append("-- Related " + "-" * 43)
        for r in related[:10]:
            lines.append(f"{r['relationship']}: {r['name']} ({r.get('file', '')})")

    return "\n".join(lines)


def render_peek_markdown(result: dict) -> str:
    """Render a peek result as a Markdown document."""
    lines: list[str] = []
    path = Path(result["path"])
    project = result.get("project")
    stats = result["stats"]

    name = project["name"] if project else path.name
    lines.append(f"# {name}")
    lines.append("")

    if result["type"] == "directory":
        if project:
            lines.append(f"**{project['manifest']}** v{project.get('version', '')}")
        lines.append(f"**{stats['files']}** files, **{stats['lines']:,}** lines")
        exts = stats.get("extensions", {})
        if exts:
            ext_parts = [f"`{k}`: {v}" for k, v in list(exts.items())[:8]]
            lines.append(f"Extensions: {', '.join(ext_parts)}")
    else:
        ext = path.suffix.lstrip(".")
        lines.append(f"**{ext.upper()}** file, **{stats['lines']:,}** lines")
        if project:
            lines.append(f"Part of **{project['name']}** v{project.get('version', '')}")

    context_docs = result.get("context_docs", [])
    if context_docs:
        lines.append("")
        lines.append("## Context")
        for doc in context_docs:
            summary = doc.get("summary", "")
            if summary:
                lines.append(f"- **{doc['title']}** ({doc['kind']}): {summary[:120]}")
            else:
                lines.append(f"- **{doc['title']}** ({doc['kind']})")

    symbols = result.get("symbols", [])
    if symbols:
        lines.append("")
        lines.append("## Symbols")
        lines.append("")
        lines.append("| Kind | Name | Line |")
        lines.append("|------|------|------|")
        for sym in symbols[:15]:
            lines.append(f"| {sym['kind']} | `{sym['name']}` | {sym['line']} |")

    lines.append("")
    lines.append("## Status")
    lines.append(f"- Indexed: {'yes' if result.get('indexed') else 'no'}")
    lines.append(f"- Enrichment: {', '.join(result.get('enrichment_layers', []))}")
    if result.get("fallback_used"):
        lines.append(f"- Fallbacks: {', '.join(result['fallback_used'])}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Router contract mapping
# ---------------------------------------------------------------------------


def peek_to_router_result(peek_result: dict) -> dict:
    """Map a peek result to the standard MCP router result shape."""
    layers = peek_result.get("enrichment_layers", [])
    context_docs = peek_result.get("context_docs", [])
    project = peek_result.get("project")
    stats = peek_result.get("stats", {})

    readme = next((d for d in context_docs if d["kind"] == "readme"), None)
    if readme and readme.get("summary"):
        summary = readme["summary"][:200]
    elif project:
        summary = (
            f"{project['name']}: {stats.get('files', 0)} files, "
            f"{stats.get('lines', 0):,} lines"
        )
    else:
        target_name = Path(peek_result["path"]).name
        summary = (
            f"{target_name}: {stats.get('files', 0)} files, "
            f"{stats.get('lines', 0):,} lines"
        )

    if "qdrant" in layers or "falkordb" in layers:
        confidence = 0.9
    elif "tldr" in layers:
        confidence = 0.7
    else:
        confidence = 0.5

    evidence: list[str] = []
    evidence.append(f"scanned {stats.get('files', 0)} files")
    for doc in context_docs:
        evidence.append(f"found {doc['title']}")
    hot_count = len(peek_result.get("hot_symbols", []))
    if hot_count:
        evidence.append(f"hot index: {hot_count} symbols")
    if peek_result.get("indexed"):
        evidence.append(".tldr/ present")

    if "tldr" not in layers:
        recommended = f"Run `tldr init {peek_result['path']}` for full indexing"
    elif "qdrant" not in layers or "falkordb" not in layers:
        recommended = "Start services (`docker compose up -d`) for richer analysis"
    else:
        recommended = "Codebase fully indexed — use repo_lookup for specific queries"

    return {
        "summary": summary,
        "confidence": confidence,
        "evidence": evidence,
        "recommended_next_action": recommended,
        "fallback_used": peek_result.get("fallback_used", []),
        "peek": peek_result,
    }
