"""Router-friendly coding tools built from existing repo intelligence primitives."""

from pathlib import Path
from shlex import quote
import subprocess

from .parser import LANG_MAP, parse_file, scan_context_docs
from .search import rg_files, rg_search

IGNORED_SCAN_PARTS = {"node_modules", ".git", "__pycache__", "target", ".venv", "venv", "dist", "build"}
ROUTER_CONTRACT_VERSION = 1
PREFERRED_RESULT_KEYS = (
    "tool_contract_version",
    "summary",
    "confidence",
    "evidence",
    "recommended_next_action",
    "verification_commands",
    "fallback_used",
)
ROUTER_TOP_LEVEL_SEQUENCE = ("repo_next_action", "repo_lookup", "change_plan", "verify_change")
LOOKUP_IMPACT_HINTS = (
    "impact",
    "break",
    "breakage",
    "blast radius",
    "blast",
    "dependent",
    "dependents",
    "dependency",
    "dependencies",
    "caller",
    "callers",
    "callee",
    "callees",
    "usage",
    "usages",
    "used by",
    "who uses",
    "what uses",
    "references",
    "refactor risk",
)
ROUTER_TOP_LEVEL_TOOLS = set(ROUTER_TOP_LEVEL_SEQUENCE)


def _repo_root(root: str | None = None) -> Path:
    """Resolve the repository root used for heuristics."""

    return Path(root or ".").resolve()


def _resolve_path(path: str, root: Path) -> Path:
    """Resolve a path relative to the repository root when needed."""

    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (root / candidate).resolve()


def _work_root(root: Path) -> Path:
    """Return the workboard directory under the repository root."""

    return root / ".tldr" / "work"


def _dedupe(items: list[str]) -> list[str]:
    """Deduplicate a list of strings while keeping order."""

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _preferred_result(
    *,
    summary: str,
    confidence: float,
    evidence: list[str] | None = None,
    recommended_next_action: str,
    verification_commands: list[str] | None = None,
    fallback_used: list[str] | None = None,
    **payload,
) -> dict:
    """Return a normalized router-friendly tool payload."""

    payload.update(
        {
            "tool_contract_version": ROUTER_CONTRACT_VERSION,
            "summary": summary,
            "confidence": round(max(0.0, min(confidence, 1.0)), 2),
            "evidence": _dedupe([item for item in (evidence or []) if item]),
            "recommended_next_action": recommended_next_action,
            "verification_commands": _dedupe(list(verification_commands or [])),
            "fallback_used": _dedupe(list(fallback_used or [])),
        }
    )
    return payload


def _router_next_tool(name: str | None) -> str:
    """Map specialist tools back to the smaller router-default surface."""

    if name in ROUTER_TOP_LEVEL_TOOLS:
        return str(name)
    if name in {"scan_context", "search_context", "edit_context", "know", "impact", "pattern_search", "diagnostics_here", "test_map"}:
        return "repo_lookup"
    if name in {"plan_current", "plan_list", "session_update", "session_note"}:
        return "repo_next_action"
    return "change_plan"


def _query_prefers_impact(query: str | None) -> bool:
    """Return whether the lookup query reads like impact analysis."""

    if not query:
        return False
    lowered = query.lower()
    return any(term in lowered for term in LOOKUP_IMPACT_HINTS)


def _project_type(root: Path, source_files: list[str]) -> str:
    """Infer the dominant project type for verification suggestions."""

    if (root / "pyproject.toml").exists() or (root / "setup.cfg").exists() or any(path.endswith(".py") for path in source_files):
        return "python"
    if (root / "Cargo.toml").exists() or any(path.endswith(".rs") for path in source_files):
        return "rust"
    if (root / "go.mod").exists() or any(path.endswith(".go") for path in source_files):
        return "go"
    if (root / "package.json").exists() or any(path.endswith((".ts", ".tsx", ".js", ".jsx")) for path in source_files):
        return "node"
    return "generic"


def _iter_repo_files(root: Path) -> list[Path]:
    """Return repository files while skipping common generated/vendor directories."""

    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_SCAN_PARTS for part in path.parts):
            continue
        files.append(path)
    return files


def _code_files(root: Path) -> list[Path]:
    """Return files supported by the parser language map."""

    return [path for path in _iter_repo_files(root) if path.suffix.lower() in LANG_MAP]


def _test_files(root: Path) -> list[Path]:
    """Return likely test files under the repository."""

    matches: list[Path] = []
    for path in _code_files(root):
        name = path.name.lower()
        if "tests" in path.parts or path.parts[-2:-1] == ("test",):
            matches.append(path)
            continue
        if name.startswith("test_") or "_test." in name or name.endswith(".spec.ts") or name.endswith(".spec.js"):
            matches.append(path)
    return matches


def _context_docs_for_root(root: Path) -> list:
    """Return scanned context docs for a repository root."""

    try:
        return scan_context_docs(root)
    except Exception:
        return []


def _generated_context_files(root: Path) -> list[Path]:
    """Return generated `.claude` context files when present."""

    candidates = [
        root / ".claude" / "TLDR.md",
        root / ".claude" / "TLDR_CONTEXT.md",
    ]
    return [path for path in candidates if path.exists()]


def _workboard_snapshot(root: Path) -> dict | None:
    """Return current workboard state and listing when available."""

    work_root = _work_root(root)
    if not work_root.exists():
        return None

    try:
        from .workboard import current_plan, list_plans

        return {
            "current": current_plan(root=work_root),
            "listing": list_plans(root=work_root),
        }
    except Exception:
        return None


def _children_snapshot(root: Path) -> dict | None:
    """Return detected child-project state when available."""

    try:
        from .children import list_children

        return list_children(root=root, include_ignored=True)
    except Exception:
        return None


def _repo_task_by_id(current: dict, task_id: str | None) -> dict | None:
    """Find the current task payload inside the active plan when present."""

    if not task_id:
        return None

    plan = (current or {}).get("plan") or {}
    for phase in plan.get("phases", []):
        for task in phase.get("tasks", []):
            if task.get("id") == task_id:
                payload = dict(task)
                payload["phase"] = phase.get("name")
                return payload
    return None


def _recent_summary(root: Path, limit: int = 10) -> dict | None:
    """Return recent repository context without advancing the summary checkpoint."""

    try:
        from .summary import build_summary

        return build_summary(root=root, mark_checked=False, limit=limit)
    except Exception:
        return None


def _query_terms(query: str) -> list[str]:
    """Return normalized query terms for fuzzy text matching."""

    return [term for term in query.lower().split() if term]


def _text_match_score(text: str, query: str) -> float:
    """Score a text blob against a query using exact and token matches."""

    haystack = text.lower()
    needle = query.lower().strip()
    if not needle:
        return 0.0

    score = 0.0
    if needle in haystack:
        score += 1.0
    for term in _query_terms(query):
        if term in haystack:
            score += 0.2
    return score


def _first_matching_line(text: str, query: str) -> tuple[int | None, str]:
    """Return the first matching line and compact snippet for a query."""

    lower_query = query.lower()
    for index, line in enumerate(text.splitlines(), 1):
        if lower_query in line.lower():
            return index, _clip_text(line, max_lines=1, max_chars=220)

    terms = _query_terms(query)
    for index, line in enumerate(text.splitlines(), 1):
        if any(term in line.lower() for term in terms):
            return index, _clip_text(line, max_lines=1, max_chars=220)
    return None, _clip_text(text, max_lines=3, max_chars=220)


def _scope_path(scope: str | None, root: Path) -> Path | None:
    """Resolve an optional scope path relative to the repo root."""

    return _resolve_path(scope, root) if scope else None


def _scope_includes(path: Path, scope: Path | None) -> bool:
    """Return whether a path is under the optional scope."""

    if scope is None:
        return True
    try:
        path.resolve().relative_to(scope.resolve())
        return True
    except ValueError:
        return False


def _relative_label(path: str | Path, root: Path) -> str:
    """Render a path relative to the repository root when possible."""

    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(root.resolve()))
    except Exception:
        return str(candidate)


def _context_window(path: Path, line: int, radius: int = 3) -> dict:
    """Return a small source snippet around a line."""

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return {"focus_line": "", "before": [], "after": []}

    index = max(0, min(len(lines) - 1, line - 1))
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return {
        "focus_line": lines[index],
        "before": lines[start:index],
        "after": lines[index + 1:end],
    }


def _enclosing_symbol(path: Path, line: int) -> dict | None:
    """Find the narrowest parsed symbol enclosing a line."""

    parsed = parse_file(path)
    if not parsed:
        return None

    matches = [symbol for symbol in parsed.symbols if symbol.line <= line <= symbol.end_line]
    if not matches:
        return None

    symbol = min(matches, key=lambda item: (item.end_line - item.line, item.line))
    return {
        "name": symbol.name,
        "kind": symbol.kind,
        "file": symbol.file,
        "line": symbol.line,
        "end_line": symbol.end_line,
        "signature": symbol.signature,
        "language": symbol.language,
    }


def _semantic_context(path: str, line: int, column: int | None, root: str | None = None) -> dict | None:
    """Fetch language-server semantic context when available."""

    try:
        from .lsp import semantic_inspect

        return semantic_inspect(path, line, column, root=root)
    except Exception:
        return None


def _knowledge_for_symbol(symbol: str, root: str) -> dict | None:
    """Fetch fast symbol knowledge from the existing chain."""

    try:
        from .chains import know

        return know(symbol, root=root)
    except Exception:
        return None


def _impact_for_symbol(symbol: str, root: str) -> dict | None:
    """Fetch impact analysis for a symbol when possible."""

    try:
        from .chains import impact

        return impact(symbol, root=root)
    except Exception:
        return None


def _discover_for_goal(query: str, root: str) -> dict | None:
    """Run the discovery chain for a goal-oriented query."""

    try:
        from .chains import discover

        return discover(query, root=root)
    except Exception:
        return None


def _similar_for_symbol(symbol: str) -> list[dict]:
    """Find similar implementations when the semantic/vector stack is available."""

    try:
        from .rag import read_similar

        return read_similar(symbol, limit=3)
    except Exception:
        return []


def _diagnostics_for_path(path: str, root: str | None = None) -> dict | None:
    """Fetch language-server diagnostics for a file when available."""

    try:
        from .lsp import document_diagnostics

        return document_diagnostics(path, root=root)
    except Exception:
        return None


def _current_work_context(repo_root: Path, path: str | None, symbol: str | None) -> dict | None:
    """Return the current plan and tasks most relevant to a file or symbol."""

    work_root = _work_root(repo_root)
    if not work_root.exists():
        return None

    try:
        from .workboard import current_plan

        current = current_plan(root=work_root)
    except Exception:
        return None

    plan = current.get("plan")
    if not plan:
        return current

    matches = []
    path_obj = Path(path).resolve() if path else None
    for phase in plan.get("phases", []):
        for task in phase.get("tasks", []):
            score = 0
            files = task.get("files", [])
            if path_obj:
                for file_entry in files:
                    file_path = Path(file_entry)
                    if file_entry == str(path_obj) or file_path.name == path_obj.name:
                        score += 2
                        break
            haystack = " ".join(
                [
                    task.get("title", ""),
                    task.get("next_step") or "",
                    *task.get("notes", []),
                    *task.get("acceptance_criteria", []),
                ]
            ).lower()
            if symbol and symbol.lower() in haystack:
                score += 1
            if score:
                matches.append(
                    {
                        "phase": phase.get("name"),
                        "task_id": task.get("id"),
                        "title": task.get("title"),
                        "status": task.get("status"),
                        "next_step": task.get("next_step"),
                        "verification_commands": task.get("verification_commands", []),
                        "acceptance_criteria": task.get("acceptance_criteria", []),
                        "score": score,
                    }
                )

    matches.sort(key=lambda item: (-item["score"], item["status"] == "done", item["title"]))
    current["matching_tasks"] = matches[:5]
    return current


def _candidate_test_files_for_source(source_path: Path, repo_root: Path) -> tuple[list[str], list[str]]:
    """Infer likely test files from a source file path."""

    matches: list[str] = []
    heuristics: list[str] = []
    test_dirs = [directory for directory in (repo_root / "tests", repo_root / "test") if directory.exists()]
    if not test_dirs:
        return matches, heuristics

    stem = source_path.stem
    suffixes = _dedupe([source_path.suffix, ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go"])

    try:
        relative = source_path.relative_to(repo_root)
    except ValueError:
        relative = source_path.name

    if isinstance(relative, Path):
        parent = relative.parent
    else:
        parent = Path()

    for test_dir in test_dirs:
        for suffix in suffixes:
            candidates = [
                test_dir / f"test_{stem}{suffix}",
                test_dir / f"{stem}_test{suffix}",
                test_dir / parent / f"test_{stem}{suffix}",
                test_dir / parent / f"{stem}_test{suffix}",
            ]
            for candidate in candidates:
                if candidate.exists():
                    matches.append(str(candidate.resolve()))
                    heuristics.append(f"filename match: {candidate.relative_to(repo_root)}")

        for candidate in test_dir.rglob("*"):
            if not candidate.is_file():
                continue
            candidate_stem = candidate.stem.lower()
            if stem.lower() in candidate_stem:
                matches.append(str(candidate.resolve()))
                heuristics.append(f"stem match: {candidate.relative_to(repo_root)}")

    return _dedupe(matches), _dedupe(heuristics)


def _content_test_hits(symbol: str | None, repo_root: Path) -> tuple[list[str], list[str]]:
    """Search for symbol references inside test files."""

    if not symbol:
        return [], []

    hits: list[str] = []
    heuristics: list[str] = []
    test_dirs = [directory for directory in (repo_root / "tests", repo_root / "test") if directory.exists()]
    if not test_dirs:
        return [], []

    try:
        files = rg_files(symbol, [str(directory) for directory in test_dirs])
    except Exception:
        return [], []

    for file_path in files:
        hits.append(str(Path(file_path).resolve()))
        heuristics.append(f"symbol reference: {Path(file_path).resolve().relative_to(repo_root)}")

    return _dedupe(hits), _dedupe(heuristics)


def _verification_commands(repo_root: Path, source_files: list[str], test_files: list[str]) -> list[str]:
    """Suggest verification commands based on project type and discovered tests."""

    project_type = _project_type(repo_root, source_files)
    commands: list[str] = []

    if project_type == "python":
        if test_files:
            commands.append("python -m pytest -q " + " ".join(quote(path) for path in test_files))
        else:
            commands.append("python -m pytest -q")
        if source_files:
            commands.append("PYTHONPYCACHEPREFIX=/tmp/tldr-pyc python -m compileall " + " ".join(quote(path) for path in source_files))
        return _dedupe(commands)

    if project_type == "rust":
        commands.append("cargo test")
        return commands

    if project_type == "go":
        commands.append("go test ./...")
        return commands

    if project_type == "node":
        commands.append("npm test -- --runInBand")
        return commands

    return ["rg -n \"TODO|FIXME\" ."]


def _clip_text(text: str, max_lines: int = 10, max_chars: int = 500) -> str:
    """Return a compact text excerpt."""

    excerpt = "\n".join(text.splitlines()[:max_lines]).strip()
    if len(excerpt) > max_chars:
        return excerpt[: max_chars - 3].rstrip() + "..."
    return excerpt


def _find_task_context(task_id: str, repo_root: Path, plan_id: str | None = None) -> dict | None:
    """Find a task by id from the workboard, optionally scoped to a plan."""

    work_root = _work_root(repo_root)
    if not work_root.exists():
        return None

    try:
        from .workboard import get_task, list_plans
    except Exception:
        return None

    candidate_plan_ids = [plan_id] if plan_id else [item["id"] for item in list_plans(root=work_root).get("plans", [])]
    for candidate in candidate_plan_ids:
        try:
            return get_task(candidate, task_id, root=work_root)
        except RuntimeError:
            continue
    return None


def _command_tail(text: str, max_lines: int = 20, max_chars: int = 800) -> str:
    """Return the tail of command output for summaries."""

    lines = text.strip().splitlines()
    tail = "\n".join(lines[-max_lines:])
    if len(tail) > max_chars:
        return tail[-max_chars:]
    return tail


def _run_verification_commands(
    commands: list[str],
    repo_root: Path,
    *,
    max_commands: int = 3,
    timeout_seconds: int = 120,
) -> list[dict]:
    """Execute verification commands and capture compact results."""

    results: list[dict] = []
    for command in commands[:max_commands]:
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            results.append(
                {
                    "command": command,
                    "passed": completed.returncode == 0,
                    "exit_code": completed.returncode,
                    "stdout_tail": _command_tail(completed.stdout),
                    "stderr_tail": _command_tail(completed.stderr),
                }
            )
        except subprocess.TimeoutExpired as exc:
            results.append(
                {
                    "command": command,
                    "passed": False,
                    "exit_code": None,
                    "timed_out": True,
                    "stdout_tail": _command_tail(exc.stdout or ""),
                    "stderr_tail": _command_tail(exc.stderr or ""),
                }
            )
    return results


def _evidence_status_counts(entries: list[str]) -> dict[str, int]:
    """Parse coarse pass/fail signals from evidence text."""

    passed = 0
    failed = 0
    for entry in entries:
        lower = entry.lower()
        if any(token in lower for token in ("failed", "error", "traceback", "exception", "timed out")):
            failed += 1
        elif any(token in lower for token in ("passed", "ok", "success")):
            passed += 1
    return {"passed": passed, "failed": failed}


def _diagnostics_near_position(diagnostics: list[dict], line: int | None, column: int | None) -> tuple[list[dict], bool]:
    """Return diagnostics at or near a position."""

    if line is None:
        return diagnostics[:10], False

    exact = [
        item
        for item in diagnostics
        if item.get("line", 0) <= line <= item.get("end_line", item.get("line", 0))
        and (
            column is None
            or item.get("column", 0) <= column <= item.get("end_column", item.get("column", 0))
            or item.get("line") != line
        )
    ]
    if exact:
        return exact[:10], False

    nearby = [item for item in diagnostics if abs(item.get("line", line) - line) <= 2]
    if nearby:
        nearby.sort(key=lambda item: abs(item.get("line", line) - line))
        return nearby[:10], True

    ranked = sorted(diagnostics, key=lambda item: abs(item.get("line", line) - line))
    return ranked[:10], bool(ranked)


def _impacted_symbols(diagnostics: list[dict], document_symbols: list[dict]) -> list[str]:
    """Infer which document symbols are covered by diagnostics."""

    impacted: list[str] = []
    for diagnostic in diagnostics:
        line = diagnostic.get("line", 0)
        for symbol in document_symbols:
            if symbol.get("line", 0) <= line <= symbol.get("end_line", line):
                impacted.append(symbol.get("name", ""))
    return _dedupe(impacted)


def test_map(path: str | None = None, symbol: str | None = None, root: str = ".") -> dict:
    """Map a source file or symbol to likely tests and verification commands."""

    repo_root = _repo_root(root)
    source_files: list[str] = []
    heuristics: list[str] = []

    if path:
        resolved = _resolve_path(path, repo_root)
        source_files.append(str(resolved))
        heuristics.append(f"source path: {resolved.relative_to(repo_root) if resolved.is_relative_to(repo_root) else resolved}")

    knowledge = None
    if symbol and not source_files:
        knowledge = _knowledge_for_symbol(symbol, str(repo_root))
        definition = (knowledge or {}).get("definition") if knowledge else None
        if definition:
            source_files.append(str(_resolve_path(definition["file"], repo_root)))
            heuristics.append(f"symbol definition: {definition['file']}")

    source_files = _dedupe(source_files)
    test_files: list[str] = []

    for source_file in source_files:
        file_matches, file_heuristics = _candidate_test_files_for_source(Path(source_file), repo_root)
        test_files.extend(file_matches)
        heuristics.extend(file_heuristics)

    symbol_hits, symbol_heuristics = _content_test_hits(symbol, repo_root)
    test_files.extend(symbol_hits)
    heuristics.extend(symbol_heuristics)
    test_files = _dedupe(test_files)
    heuristics = _dedupe(heuristics)

    verification_commands = _verification_commands(repo_root, source_files, test_files)
    confidence = 0.9 if test_files else 0.6 if source_files else 0.3
    fallback_used: list[str] = []
    if not test_files:
        fallback_used.append("broad_verification_only")
    if symbol and not source_files:
        fallback_used.append("symbol_definition_lookup_failed")

    summary = (
        f"Found {len(test_files)} likely test file(s) for {symbol or (Path(source_files[0]).name if source_files else 'the requested target')}."
        if test_files
        else "No likely tests found; fall back to broad verification commands."
    )

    evidence = [f"test file: {Path(file_path).name}" for file_path in test_files[:5]]
    evidence.extend(f"heuristic: {item}" for item in heuristics[:5])

    return _preferred_result(
        root=str(repo_root),
        project_type=_project_type(repo_root, source_files),
        path=source_files[0] if source_files else None,
        symbol=symbol,
        source_files=source_files,
        test_files=test_files,
        heuristics=heuristics,
        summary=summary,
        confidence=confidence,
        evidence=evidence,
        verification_commands=verification_commands,
        recommended_next_action=(
            f"Run {verification_commands[0]}" if verification_commands else "Inspect the nearest existing tests before editing."
        ),
        fallback_used=fallback_used,
        next_best_tool="edit_context" if source_files else "change_plan",
    )


def edit_context(path: str, line: int, column: int | None = None, root: str = ".") -> dict:
    """Return the code-time context a router needs before editing."""

    repo_root = _repo_root(root)
    source_path = _resolve_path(path, repo_root)
    snippet = _context_window(source_path, line)
    enclosing = _enclosing_symbol(source_path, line)
    semantic = _semantic_context(str(source_path), line, column, root=str(repo_root))
    symbol_name = (enclosing or {}).get("name") or (semantic or {}).get("token")

    knowledge = _knowledge_for_symbol(symbol_name, str(repo_root)) if symbol_name else None
    similar = _similar_for_symbol(symbol_name) if symbol_name else []
    tests = test_map(path=str(source_path), symbol=symbol_name, root=str(repo_root))
    work_context = _current_work_context(repo_root, str(source_path), symbol_name)
    fallback_used: list[str] = []
    if not semantic:
        fallback_used.append("syntax_only_context")
    if not knowledge:
        fallback_used.append("symbol_knowledge_unavailable")
    if not similar:
        fallback_used.append("pattern_matches_unavailable")
    if not work_context:
        fallback_used.append("workboard_context_unavailable")

    verification_commands = list(tests.get("verification_commands", []))
    matching_tasks = (work_context or {}).get("matching_tasks", [])
    for task in matching_tasks:
        verification_commands.extend(task.get("verification_commands", []))
    verification_commands = _dedupe(verification_commands)

    confidence = 0.35
    if enclosing:
        confidence += 0.25
    if semantic:
        confidence += 0.25
    if knowledge:
        confidence += 0.15
    confidence = min(confidence, 0.95)

    if matching_tasks and matching_tasks[0].get("next_step"):
        recommended_next_action = matching_tasks[0]["next_step"]
    elif verification_commands:
        target = symbol_name or source_path.name
        recommended_next_action = f"Edit {target}, then run {verification_commands[0]}."
    else:
        recommended_next_action = f"Inspect {source_path.name} around line {line} before editing."

    summary_target = symbol_name or source_path.name
    summary = f"Edit context ready for {summary_target} at {source_path}:{line}."
    evidence = [f"focus line: {snippet['focus_line']}"]
    if enclosing:
        evidence.append(f"enclosing symbol: {enclosing['kind']} {enclosing['name']}")
    if semantic and semantic.get("hover"):
        evidence.append(f"semantic hover: {semantic['hover']}")
    if similar:
        evidence.append(f"similar implementation: {similar[0].get('symbol', similar[0].get('file', ''))}")
    if matching_tasks:
        evidence.append(f"matching task: {matching_tasks[0]['title']}")

    return _preferred_result(
        path=str(source_path),
        line=line,
        column=column,
        snippet=snippet,
        enclosing_symbol=enclosing,
        semantic=semantic,
        knowledge=knowledge,
        similar=similar,
        tests=tests,
        work_context=work_context,
        summary=summary,
        confidence=confidence,
        evidence=evidence,
        verification_commands=verification_commands,
        recommended_next_action=recommended_next_action,
        fallback_used=fallback_used,
        next_best_tool="change_plan",
    )


def change_plan(goal: str, path: str | None = None, symbol: str | None = None, root: str = ".") -> dict:
    """Turn a coding goal into candidate files, steps, risks, and verification commands."""

    repo_root = _repo_root(root)
    primary_path = str(_resolve_path(path, repo_root)) if path else None

    knowledge = _knowledge_for_symbol(symbol, str(repo_root)) if symbol else None
    if not primary_path and knowledge and knowledge.get("definition"):
        primary_path = str(_resolve_path(knowledge["definition"]["file"], repo_root))

    impact = _impact_for_symbol(symbol, str(repo_root)) if symbol else None
    discovery_query = " ".join(part for part in [goal, symbol, Path(primary_path).stem if primary_path else ""] if part).strip()
    discovery = _discover_for_goal(discovery_query or goal, str(repo_root))

    candidate_files: list[str] = []
    if primary_path:
        candidate_files.append(primary_path)
    if knowledge and knowledge.get("definition"):
        candidate_files.append(str(_resolve_path(knowledge["definition"]["file"], repo_root)))
    for item in (discovery or {}).get("merged", []):
        if item.get("file"):
            candidate_files.append(str(_resolve_path(item["file"], repo_root)))
    candidate_files = _dedupe(candidate_files)[:8]

    likely_symbols = _dedupe(
        [symbol or ""]
        + [item.get("symbol", "") for item in (discovery or {}).get("merged", [])]
        + ([entry.get("name", "") for entry in (knowledge or {}).get("callers", [])[:2]] if knowledge else [])
    )[:8]

    tests = test_map(path=primary_path, symbol=symbol, root=str(repo_root))
    work_context = _current_work_context(repo_root, primary_path, symbol)
    fallback_used: list[str] = []
    if not impact:
        fallback_used.append("impact_graph_unavailable")
    if not discovery or not discovery.get("merged"):
        fallback_used.append("discovery_candidates_unavailable")
    if not work_context:
        fallback_used.append("workboard_context_unavailable")

    acceptance_criteria: list[str] = []
    if work_context:
        for task in work_context.get("matching_tasks", []):
            acceptance_criteria.extend(task.get("acceptance_criteria", []))
    if not acceptance_criteria:
        acceptance_criteria.append(f"The codebase satisfies the requested goal: {goal}")
    if tests.get("test_files"):
        acceptance_criteria.append("Related tests pass with the updated behavior.")
    else:
        acceptance_criteria.append("Add or confirm at least one meaningful verification path for the changed area.")
    acceptance_criteria = _dedupe(acceptance_criteria)

    risks: list[str] = []
    if impact:
        risks.append(impact.get("warning", ""))
        if impact.get("severity") in {"high", "medium"}:
            risks.append(f"Impact severity is {impact['severity']}; review dependent files before editing.")
    if not tests.get("test_files"):
        risks.append("No targeted tests were found automatically; regressions may only show up in broader validation.")
    risks = _dedupe([risk for risk in risks if risk])

    verification_commands = list(tests.get("verification_commands", []))
    if work_context:
        for task in work_context.get("matching_tasks", []):
            verification_commands.extend(task.get("verification_commands", []))
    verification_commands = _dedupe(verification_commands)

    ordered_steps = [
        f"Inspect the primary edit target: {candidate_files[0]}" if candidate_files else "Inspect the most relevant implementation before editing.",
        "Update the implementation in the smallest set of candidate files first.",
        "Update or add focused tests covering the requested behavior.",
        f"Run verification: {verification_commands[0]}" if verification_commands else "Run the strongest available verification command.",
    ]

    if work_context and work_context.get("matching_tasks"):
        next_step = work_context["matching_tasks"][0].get("next_step")
        if next_step:
            ordered_steps.insert(0, next_step)
    ordered_steps = _dedupe(ordered_steps)

    confidence = 0.4
    if candidate_files:
        confidence += 0.25
    if symbol and impact:
        confidence += 0.15
    if tests.get("test_files"):
        confidence += 0.15
    if work_context and work_context.get("matching_tasks"):
        confidence += 0.1
    confidence = min(confidence, 0.95)

    recommended_next_action = (
        f"Call edit_context on {candidate_files[0]} at the exact line you plan to change."
        if candidate_files
        else "Use discover or read_grep to narrow the primary edit target before changing code."
    )
    evidence = [f"candidate file: {Path(file_path).name}" for file_path in candidate_files[:4]]
    evidence.extend(f"likely symbol: {name}" for name in likely_symbols[:4])
    if impact and impact.get("warning"):
        evidence.append(f"impact: {impact['warning']}")

    return _preferred_result(
        goal=goal,
        path=primary_path,
        symbol=symbol,
        candidate_files=candidate_files,
        likely_symbols=likely_symbols,
        impact=impact,
        discovery=discovery,
        tests=tests,
        work_context=work_context,
        ordered_steps=ordered_steps,
        acceptance_criteria=acceptance_criteria,
        risks=risks,
        summary=f"Prepared a change plan for: {goal}",
        confidence=confidence,
        evidence=evidence,
        verification_commands=verification_commands,
        recommended_next_action=recommended_next_action,
        fallback_used=fallback_used,
        next_best_tool="edit_context" if candidate_files else "discover",
    )


def diagnostics_here(path: str, line: int | None = None, column: int | None = None, root: str = ".") -> dict:
    """Return LSP diagnostics for a file or exact position."""

    repo_root = _repo_root(root)
    source_path = _resolve_path(path, repo_root)
    tests = test_map(path=str(source_path), root=str(repo_root))
    diagnostics_payload = _diagnostics_for_path(str(source_path), root=str(repo_root))
    fallback_used: list[str] = []

    if not diagnostics_payload:
        fallback_used.append("lsp_diagnostics_unavailable")
        relevant_diagnostics: list[dict] = []
        document_symbols: list[dict] = []
        used_nearest = False
    else:
        relevant_diagnostics, used_nearest = _diagnostics_near_position(
            diagnostics_payload.get("diagnostics", []),
            line,
            column,
        )
        document_symbols = diagnostics_payload.get("document_symbols", [])
        if used_nearest:
            fallback_used.append("nearest_diagnostic_match")

    impacted_symbols = _impacted_symbols(relevant_diagnostics, document_symbols)
    likely_fix_area = None
    if relevant_diagnostics:
        top = relevant_diagnostics[0]
        likely_fix_area = {
            "path": top.get("path"),
            "line": top.get("line"),
            "column": top.get("column"),
            "severity": top.get("severity"),
            "message": top.get("message"),
            "symbol": impacted_symbols[0] if impacted_symbols else None,
        }

    evidence = [
        f"{item.get('severity', 'unknown')}: {item.get('message', '')} ({Path(item.get('path') or str(source_path)).name}:{item.get('line')})"
        for item in relevant_diagnostics[:5]
    ]
    if not evidence:
        evidence.append("No LSP diagnostics reported for the requested location.")

    verification_commands = list(tests.get("verification_commands", []))
    if likely_fix_area:
        recommended_next_action = f"Resolve the {likely_fix_area['severity']} at line {likely_fix_area['line']} before broader edits."
    elif verification_commands:
        recommended_next_action = f"Run {verification_commands[0]} to confirm the file is clean."
    else:
        recommended_next_action = "Inspect the file manually; no diagnostics or verification commands were available."

    confidence = 0.85 if relevant_diagnostics and not used_nearest else 0.65 if relevant_diagnostics else 0.25

    return _preferred_result(
        path=str(source_path),
        line=line,
        column=column,
        diagnostics=relevant_diagnostics,
        all_diagnostics=(diagnostics_payload or {}).get("diagnostics", []),
        impacted_symbols=impacted_symbols,
        likely_fix_area=likely_fix_area,
        diagnostic_source=(diagnostics_payload or {}).get("diagnostic_source"),
        summary=(
            f"Found {len(relevant_diagnostics)} relevant diagnostic(s) in {source_path.name}."
            if relevant_diagnostics
            else f"No diagnostics reported for {source_path.name}."
        ),
        confidence=confidence,
        evidence=evidence,
        verification_commands=verification_commands,
        recommended_next_action=recommended_next_action,
        fallback_used=fallback_used,
        next_best_tool="edit_context",
    )


def pattern_search(
    query: str | None = None,
    *,
    path: str | None = None,
    symbol: str | None = None,
    root: str = ".",
    limit: int = 5,
) -> dict:
    """Find existing implementation patterns before writing new code."""

    repo_root = _repo_root(root)
    search_query = query or symbol or (Path(path).stem if path else "")
    fallback_used: list[str] = []

    semantic = _similar_for_symbol(search_query) if search_query else []
    if not semantic:
        fallback_used.append("semantic_pattern_matches_unavailable")

    discovery = _discover_for_goal(search_query, str(repo_root)) if search_query else None
    if not discovery or not discovery.get("merged"):
        fallback_used.append("discovery_candidates_unavailable")

    try:
        rg_hits = rg_search(search_query, [str(repo_root)], context=2, max_results=limit) if search_query else []
    except Exception:
        rg_hits = []
    if not rg_hits:
        fallback_used.append("text_pattern_matches_unavailable")

    patterns: list[dict] = []
    seen: set[str] = set()

    for item in semantic[:limit]:
        key = f"semantic:{item.get('file')}:{item.get('line')}:{item.get('symbol')}"
        if key in seen:
            continue
        seen.add(key)
        patterns.append(
            {
                "source": "semantic",
                "score": item.get("score", 0.0),
                "symbol": item.get("symbol"),
                "kind": item.get("kind"),
                "file": item.get("file"),
                "line": item.get("line"),
                "snippet": _clip_text(item.get("code", "")),
                "reuse_reason": "Closest semantic implementation match in the indexed codebase.",
            }
        )

    for item in (discovery or {}).get("merged", [])[: limit * 2]:
        key = f"discover:{item.get('file')}:{item.get('line')}:{item.get('symbol', item.get('text'))}"
        if key in seen:
            continue
        seen.add(key)
        patterns.append(
            {
                "source": item.get("source", "discover"),
                "score": item.get("score", 0.0),
                "symbol": item.get("symbol"),
                "file": item.get("file"),
                "line": item.get("line"),
                "snippet": _clip_text(item.get("code", item.get("text", ""))),
                "reuse_reason": "Discovery hit that appears related to the requested change.",
            }
        )

    for hit in rg_hits[:limit]:
        key = f"rg:{hit.file}:{hit.line}:{hit.text}"
        if key in seen:
            continue
        seen.add(key)
        patterns.append(
            {
                "source": "rg",
                "score": 0.5,
                "symbol": None,
                "file": hit.file,
                "line": hit.line,
                "snippet": _clip_text("\n".join([*hit.before, hit.text, *hit.after])),
                "reuse_reason": "Exact text match found via ripgrep.",
            }
        )

    patterns.sort(key=lambda item: item.get("score", 0), reverse=True)
    patterns = patterns[:limit]

    top = patterns[0] if patterns else None
    tests = test_map(path=top.get("file") if top else path, symbol=symbol, root=str(repo_root))
    evidence = [
        f"{item['source']} pattern: {Path(item['file']).name}:{item.get('line')} {item.get('symbol') or ''}".strip()
        for item in patterns[:5]
    ]
    if not evidence:
        evidence.append("No reusable patterns were found from semantic, discovery, or ripgrep search.")

    if top:
        recommended_next_action = (
            f"Reuse the implementation pattern from {Path(top['file']).name}:{top.get('line')} "
            f"instead of creating a new shape from scratch."
        )
        summary = f"Found {len(patterns)} reusable implementation pattern(s) for `{search_query}`."
        confidence = 0.85 if top["source"] == "semantic" else 0.7
    else:
        recommended_next_action = "Broaden the query or inspect nearby modules manually before writing a new implementation."
        summary = f"No reusable implementation patterns found for `{search_query}`."
        confidence = 0.25

    return _preferred_result(
        query=search_query,
        path=str(_resolve_path(path, repo_root)) if path else None,
        symbol=symbol,
        patterns=patterns,
        ranked_reusable_snippets=patterns[:3],
        use_instead=top,
        summary=summary,
        confidence=confidence,
        evidence=evidence,
        verification_commands=tests.get("verification_commands", []),
        recommended_next_action=recommended_next_action,
        fallback_used=fallback_used,
        next_best_tool="edit_context" if top else "discover",
    )


def verify_change(
    *,
    files: list[str] | None = None,
    symbol: str | None = None,
    task_id: str | None = None,
    plan_id: str | None = None,
    root: str = ".",
    run_commands: bool = False,
    max_commands: int = 3,
) -> dict:
    """Verify a change against inferred tests and workboard task criteria."""

    repo_root = _repo_root(root)
    fallback_used: list[str] = []
    task = _find_task_context(task_id, repo_root, plan_id=plan_id) if task_id else None
    if task_id and not task:
        fallback_used.append("task_lookup_failed")

    target_files: list[str] = []
    for file_path in files or []:
        target_files.append(str(_resolve_path(file_path, repo_root)))
    if task:
        target_files.extend(task.get("files", []))

    knowledge = _knowledge_for_symbol(symbol, str(repo_root)) if symbol else None
    if symbol and knowledge and knowledge.get("definition"):
        target_files.append(str(_resolve_path(knowledge["definition"]["file"], repo_root)))
    elif symbol:
        fallback_used.append("symbol_definition_lookup_failed")
    target_files = _dedupe(target_files)

    verification_commands: list[str] = []
    related_tests: list[str] = []
    evidence: list[str] = []
    for index, file_path in enumerate(target_files[:3]):
        mapped = test_map(path=file_path, symbol=symbol if index == 0 else None, root=str(repo_root))
        verification_commands.extend(mapped.get("verification_commands", []))
        related_tests.extend(mapped.get("test_files", []))
        evidence.extend(f"test map: {item}" for item in mapped.get("heuristics", [])[:2])

    if not target_files and symbol:
        mapped = test_map(symbol=symbol, root=str(repo_root))
        verification_commands.extend(mapped.get("verification_commands", []))
        related_tests.extend(mapped.get("test_files", []))
        evidence.extend(f"test map: {item}" for item in mapped.get("heuristics", [])[:2])

    if task:
        verification_commands = _dedupe(task.get("verification_commands", []) + verification_commands)
        evidence.extend(f"task evidence: {item}" for item in task.get("evidence", [])[:5])
        evidence.append(f"task status: {task.get('status')}")
    else:
        verification_commands = _dedupe(verification_commands)

    command_results = _run_verification_commands(
        verification_commands,
        repo_root,
        max_commands=max_commands,
    ) if run_commands and verification_commands else []

    if verification_commands and not run_commands:
        fallback_used.append("verification_commands_not_executed")
    if not verification_commands:
        fallback_used.append("verification_commands_unavailable")

    evidence.extend(
        f"command {'passed' if result['passed'] else 'failed'}: {result['command']}"
        for result in command_results
    )
    evidence.extend(f"related test: {Path(file_path).name}" for file_path in related_tests[:5])

    command_passed = sum(1 for result in command_results if result.get("passed"))
    command_failed = sum(1 for result in command_results if not result.get("passed"))
    evidence_signals = _evidence_status_counts(evidence)

    if command_results:
        status = "failed" if command_failed else "passed"
    elif evidence_signals["failed"]:
        status = "failed"
    elif evidence_signals["passed"]:
        status = "passed"
    elif verification_commands:
        status = "not_run"
    else:
        status = "unknown"

    acceptance_criteria = list(task.get("acceptance_criteria", [])) if task else []
    if acceptance_criteria:
        if command_failed:
            acceptance_criteria_satisfied = False
        elif task and task.get("status") == "done" and (task.get("evidence") or command_results):
            acceptance_criteria_satisfied = True
        else:
            acceptance_criteria_satisfied = False
    elif command_results:
        acceptance_criteria_satisfied = command_failed == 0
    else:
        acceptance_criteria_satisfied = None

    missing_evidence: list[str] = []
    if task and task.get("status") != "done":
        missing_evidence.append(f"Task `{task['id']}` is still marked `{task['status']}`.")
    if acceptance_criteria and not task.get("evidence") and not command_results:
        missing_evidence.append("No recorded evidence is attached to the task acceptance criteria.")
    if verification_commands and not command_results and run_commands:
        missing_evidence.append("Verification commands were requested but no command results were captured.")
    if verification_commands and not run_commands:
        missing_evidence.append("Verification commands are available but were not executed in this verification run.")
    if command_failed:
        missing_evidence.append("One or more verification commands failed.")
    if not related_tests:
        missing_evidence.append("No targeted tests were identified for the requested change.")
    missing_evidence = _dedupe(missing_evidence)

    pass_fail_summary = {
        "status": status,
        "passed_commands": command_passed,
        "failed_commands": command_failed,
        "recorded_pass_signals": evidence_signals["passed"],
        "recorded_fail_signals": evidence_signals["failed"],
    }

    if command_failed:
        first_failed = next(result for result in command_results if not result.get("passed"))
        recommended_next_action = f"Fix the failing verification command first: {first_failed['command']}."
    elif missing_evidence:
        recommended_next_action = missing_evidence[0]
    elif acceptance_criteria_satisfied:
        recommended_next_action = "Record the passing evidence on the workboard task and move to the next item."
    elif verification_commands:
        recommended_next_action = f"Run {verification_commands[0]} to collect concrete evidence."
    else:
        recommended_next_action = "Add a focused verification command or test before treating the change as complete."

    confidence = 0.85 if command_results else 0.7 if task else 0.55 if verification_commands else 0.3

    return _preferred_result(
        files=target_files,
        symbol=symbol,
        task=task,
        what_to_run=verification_commands,
        command_results=command_results,
        related_tests=_dedupe(related_tests),
        pass_fail_summary=pass_fail_summary,
        missing_evidence=missing_evidence,
        acceptance_criteria=acceptance_criteria,
        acceptance_criteria_satisfied=acceptance_criteria_satisfied,
        summary=(
            f"Verification status is `{status}` for {task_id or symbol or (Path(target_files[0]).name if target_files else 'the requested change')}."
        ),
        confidence=confidence,
        evidence=evidence,
        verification_commands=verification_commands,
        recommended_next_action=recommended_next_action,
        fallback_used=fallback_used,
        next_best_tool="diagnostics_here" if command_failed else "task_update" if task else "edit_context",
    )


def repo_next_action(root: str = ".") -> dict:
    """Recommend the best next tool and action for the current repository state."""

    repo_root = _repo_root(root)
    workboard = _workboard_snapshot(repo_root)
    children = _children_snapshot(repo_root)
    recent = _recent_summary(repo_root, limit=5)

    current = (workboard or {}).get("current") or {}
    session = current.get("session") or {}
    plan = current.get("plan") or {}
    overlaps = current.get("overlaps") or []
    unknown_children = [child for child in (children or {}).get("children", []) if child.get("status") == "unknown"]
    current_task = _repo_task_by_id(current, session.get("current_task_id"))

    verification_commands: list[str] = []
    if current_task:
        verification_commands.extend(current_task.get("verification_commands", []))
    verification_commands.extend(session.get("verification_commands", []))
    verification_commands = _dedupe(verification_commands)

    suggested_tool = "repo_lookup"
    suggested_arguments: dict[str, object] = {"root": str(repo_root)}
    reason = "No active plan or overlap signals were found; start with a broad repo scan."
    fallback_used: list[str] = []
    confidence = 0.45

    if overlaps:
        top = overlaps[0]
        suggested_tool = "repo_lookup"
        suggested_arguments = {
            "root": str(repo_root),
            "path": (top.get("shared_files") or [None])[0],
            "symbol": (top.get("shared_symbols") or [None])[0],
        }
        reason = (
            f"Active overlap with {top.get('actor_id') or top.get('session_id')}: "
            f"shared files {', '.join(top.get('shared_files', [])[:2]) or 'none'}, "
            f"shared symbols {', '.join(top.get('shared_symbols', [])[:2]) or 'none'}."
        )
        confidence = 0.95
    elif unknown_children:
        top = unknown_children[0]
        suggested_tool = "repo_lookup"
        suggested_arguments = {"root": str(repo_root), "path": top.get("path")}
        reason = f"Unknown child subtree detected at {top.get('path')}; orient to the imported surface before deeper analysis."
        confidence = 0.88
    elif session.get("current_task_id") and current_task:
        if verification_commands and current_task.get("status") in {"in_progress", "done", "blocked"}:
            suggested_tool = "verify_change"
            suggested_arguments = {
                "task_id": current_task.get("id"),
                "plan_id": plan.get("id"),
                "root": str(repo_root),
            }
            reason = f"Current task `{current_task.get('title')}` already has verification context; confirm status before more edits."
            confidence = 0.9
        else:
            suggested_tool = "change_plan"
            suggested_arguments = {
                "goal": current_task.get("title") or plan.get("goal") or session.get("goal") or "Continue the current task",
                "path": current_task.get("files", [None])[0],
                "root": str(repo_root),
            }
            reason = f"Current task `{current_task.get('title')}` needs a sharper executable plan."
            confidence = 0.82
    elif session.get("next_action") or session.get("current_focus"):
        suggested_tool = "change_plan"
        suggested_arguments = {
            "goal": session.get("next_action") or session.get("current_focus") or plan.get("goal") or "Resume the current task",
            "root": str(repo_root),
        }
        reason = session.get("next_action") or session.get("current_focus") or "Resume the active work context."
        confidence = 0.78
    elif plan:
        suggested_tool = "change_plan"
        suggested_arguments = {
            "goal": plan.get("goal") or plan.get("title") or "Continue the current plan",
            "root": str(repo_root),
        }
        reason = f"Active plan `{plan.get('title')}` has no explicit next action; derive one from the plan goal."
        confidence = 0.72
    elif recent and ((recent.get("counts") or {}).get("working_tree_changes") or (recent.get("counts") or {}).get("commits")):
        suggested_tool = "repo_lookup"
        suggested_arguments = {"root": str(repo_root)}
        reason = "Recent changes exist without active workboard context; re-orient before continuing."
        confidence = 0.6
    else:
        fallback_used.append("no_active_work_context")

    alternative_tools = _dedupe(
        [suggested_tool]
        + (["repo_lookup"] if suggested_tool != "repo_lookup" else [])
        + (["verify_change"] if verification_commands else [])
        + ["change_plan", "repo_next_action"]
    )
    evidence = []
    if plan:
        evidence.append(f"active plan: {plan.get('title')}")
    if current_task:
        evidence.append(f"current task: {current_task.get('title')} [{current_task.get('status')}]")
    if session.get("next_action"):
        evidence.append(f"session next action: {session.get('next_action')}")
    if overlaps:
        evidence.append(f"overlap count: {len(overlaps)}")
    if unknown_children:
        evidence.append(f"unknown children: {len(unknown_children)}")

    return _preferred_result(
        root=str(repo_root),
        current_session=session,
        current_plan={"id": plan.get("id"), "title": plan.get("title"), "goal": plan.get("goal")} if plan else None,
        current_task=current_task,
        overlaps=overlaps,
        unknown_children=unknown_children,
        suggested_tool=suggested_tool,
        suggested_arguments=suggested_arguments,
        alternative_tools=alternative_tools,
        summary=f"Recommended next tool is `{suggested_tool}`.",
        confidence=confidence,
        evidence=evidence or [reason],
        verification_commands=verification_commands,
        recommended_next_action=reason,
        fallback_used=fallback_used,
        next_best_tool=suggested_tool,
    )


def repo_lookup(
    *,
    query: str | None = None,
    path: str | None = None,
    line: int | None = None,
    column: int | None = None,
    symbol: str | None = None,
    root: str = ".",
    scope: str | None = None,
    source_types: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """Single router-first lookup entry point for repo overview, search, symbol, impact, and edit context."""

    repo_root = _repo_root(root)
    lookup_scope = scope or path
    fallback_used: list[str] = []

    dispatched_tool = "scan_context"
    dispatched_arguments: dict[str, object] = {"root": str(repo_root), "scope": lookup_scope, "limit": limit}
    lookup_mode = "overview"
    result: dict | None = None

    if path and line is not None:
        resolved_path = str(_resolve_path(path, repo_root))
        dispatched_tool = "edit_context"
        dispatched_arguments = {
            "path": resolved_path,
            "line": line,
            "column": column,
            "root": str(repo_root),
        }
        lookup_mode = "edit"
        result = edit_context(resolved_path, line, column=column, root=str(repo_root))
    elif symbol and _query_prefers_impact(query):
        dispatched_tool = "impact"
        dispatched_arguments = {"name": symbol, "root": str(repo_root)}
        lookup_mode = "impact"
        impact_result = _impact_for_symbol(symbol, str(repo_root))
        if impact_result:
            verification_commands = test_map(symbol=symbol, root=str(repo_root)).get("verification_commands", [])
            evidence = [
                f"severity: {impact_result.get('severity', 'unknown')}",
                impact_result.get("warning", ""),
                f"files affected: {len(impact_result.get('files_affected', []))}",
                f"transitive dependents: {len(impact_result.get('transitive_dependents', []))}",
            ]
            result = _preferred_result(
                symbol=symbol,
                impact=impact_result,
                summary=f"Impact lookup for `{symbol}` is `{impact_result.get('severity', 'unknown')}`.",
                confidence=0.88 if impact_result.get("total_references") is not None else 0.7,
                evidence=[item for item in evidence if item],
                verification_commands=verification_commands,
                recommended_next_action=(
                    "Use change_plan before editing this symbol."
                    if impact_result.get("severity") in {"high", "medium", "low"}
                    else "Use repo_lookup with a concrete path or line before changing code."
                ),
                fallback_used=[],
                next_best_tool="change_plan" if impact_result.get("severity") in {"high", "medium", "low"} else "repo_lookup",
            )
        else:
            fallback_used.append("impact_lookup_unavailable")

    if result is None and symbol:
        dispatched_tool = "know"
        dispatched_arguments = {"name": symbol, "root": str(repo_root)}
        lookup_mode = "symbol"
        knowledge = _knowledge_for_symbol(symbol, str(repo_root))
        if knowledge and (knowledge.get("found") or knowledge.get("definition") or knowledge.get("callers") or knowledge.get("usage_count") is not None):
            definition = knowledge.get("definition") or {}
            verification_commands = test_map(
                path=definition.get("file"),
                symbol=symbol,
                root=str(repo_root),
            ).get("verification_commands", [])
            evidence = []
            if definition.get("file") and definition.get("line"):
                evidence.append(f"definition: {Path(definition['file']).name}:{definition['line']}")
            if knowledge.get("usage_count") is not None:
                evidence.append(f"usage count: {knowledge['usage_count']}")
            if knowledge.get("callers"):
                evidence.append(f"callers: {len(knowledge['callers'])}")
            if knowledge.get("callees"):
                evidence.append(f"callees: {len(knowledge['callees'])}")
            if ((knowledge.get("semantic") or {}).get("hover")):
                evidence.append(f"semantic hover: {knowledge['semantic']['hover']}")

            if definition.get("file") and definition.get("line"):
                recommended_next_action = (
                    f"Call repo_lookup with `path={definition['file']}` and `line={definition['line']}` "
                    "for edit-time context before changing code."
                )
            else:
                recommended_next_action = "Use change_plan once the specific edit target is clear."

            result = _preferred_result(
                symbol=symbol,
                knowledge=knowledge,
                summary=f"Symbol lookup found `{symbol}` in the repository.",
                confidence=0.9 if definition else 0.76,
                evidence=evidence or [f"symbol: {symbol}"],
                verification_commands=verification_commands,
                recommended_next_action=recommended_next_action,
                fallback_used=[],
                next_best_tool="repo_lookup" if definition else "change_plan",
            )
        else:
            fallback_used.append("symbol_lookup_unavailable")

    if result is None and (query or symbol):
        dispatched_tool = "search_context"
        dispatched_arguments = {
            "query": query or symbol,
            "root": str(repo_root),
            "scope": lookup_scope,
            "source_types": source_types,
            "limit": limit,
        }
        lookup_mode = "search"
        result = search_context(
            query or symbol or "",
            root=str(repo_root),
            scope=lookup_scope,
            source_types=source_types,
            limit=limit,
        )

    if result is None:
        result = scan_context(root=str(repo_root), scope=lookup_scope, limit=limit)

    payload = dict(result)
    specialist_next_tool = payload.get("next_best_tool")
    payload["summary"] = f"Repo lookup used `{dispatched_tool}`. {payload.get('summary', '')}".strip()
    payload["lookup_mode"] = lookup_mode
    payload["lookup_inputs"] = {
        "query": query,
        "path": path,
        "line": line,
        "column": column,
        "symbol": symbol,
        "scope": lookup_scope,
    }
    payload["dispatched_tool"] = dispatched_tool
    payload["dispatched_arguments"] = dispatched_arguments
    payload["specialist_tool"] = dispatched_tool
    payload["specialist_next_tool"] = specialist_next_tool
    payload["why_this_tool"] = {
        "overview": "No specific query, symbol, or line was provided, so broad repo orientation is the safest first step.",
        "search": "A free-form query was provided, so federated context search is the strongest first lookup.",
        "symbol": "A symbol name was provided, so direct symbol knowledge is more precise than broad search.",
        "impact": "The query reads like change-risk analysis, so impact lookup is the right specialist path.",
        "edit": "A file position was provided, so edit-time context is the most precise lookup surface.",
    }[lookup_mode]
    payload["fallback_used"] = _dedupe(list(payload.get("fallback_used", [])) + fallback_used)
    payload["next_best_tool"] = _router_next_tool(specialist_next_tool)
    return payload


def _code_context_hits(query: str, repo_root: Path, *, scope: str | None = None, limit: int = 10) -> list[dict]:
    """Search source code for relevant query matches."""

    scope_path = _scope_path(scope, repo_root)
    search_paths = [str(scope_path or repo_root)]
    hits = rg_search(query, search_paths, context=2, max_results=max(limit * 2, 10))

    results: list[dict] = []
    for hit in hits:
        file_path = Path(hit.file)
        if file_path.suffix.lower() not in LANG_MAP:
            continue
        if not _scope_includes(file_path, scope_path):
            continue
        results.append(
            {
                "source_type": "code",
                "path": str(file_path.resolve()),
                "line": hit.line,
                "score": 1.0,
                "snippet": _clip_text("\n".join([*hit.before, hit.text, *hit.after]), max_lines=5, max_chars=280),
                "why_matched": "Exact text search hit in source code.",
                "symbol": None,
            }
        )
        if len(results) >= limit:
            break
    return results


def _doc_context_hits(query: str, repo_root: Path, *, scope: str | None = None, limit: int = 10) -> list[dict]:
    """Search docs, generated TLDR files, and markdown context."""

    scope_path = _scope_path(scope, repo_root)
    results: list[dict] = []
    for doc in _context_docs_for_root(repo_root):
        file_path = Path(doc.file)
        if not _scope_includes(file_path, scope_path):
            continue
        best_score = _text_match_score(doc.title, query) + _text_match_score(doc.content, query)
        if best_score <= 0:
            continue
        line, snippet = _first_matching_line(doc.content, query)
        results.append(
            {
                "source_type": "docs",
                "path": str(file_path.resolve()),
                "line": line,
                "score": 0.7 + min(best_score, 0.8),
                "snippet": snippet,
                "why_matched": f"Matched {doc.kind} context document `{file_path.name}`.",
                "symbol": None,
                "title": doc.title,
            }
        )

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]


def _workboard_context_hits(query: str, repo_root: Path, *, limit: int = 10) -> list[dict]:
    """Search plans, tasks, and session notes."""

    snapshot = _workboard_snapshot(repo_root)
    if not snapshot:
        return []

    results: list[dict] = []
    current_plan_id = ((snapshot.get("current") or {}).get("session") or {}).get("current_plan_id")

    for plan in snapshot.get("listing", {}).get("plans", []):
        body = "\n".join([plan.get("title", ""), plan.get("goal", ""), plan.get("status", "")])
        score = _text_match_score(body, query)
        if score > 0:
            results.append(
                {
                    "source_type": "workboard",
                    "path": f"repo://plan/{plan['id']}",
                    "line": None,
                    "score": 0.8 + score + (0.1 if plan["id"] == current_plan_id else 0.0),
                    "snippet": _clip_text(body, max_lines=3, max_chars=220),
                    "why_matched": "Matched plan title or goal in the workboard.",
                    "symbol": None,
                    "plan_id": plan["id"],
                }
            )

    current = snapshot.get("current") or {}
    plan = current.get("plan") or {}
    for phase in plan.get("phases", []):
        for task in phase.get("tasks", []):
            body = "\n".join(
                [
                    task.get("title", ""),
                    task.get("next_step") or "",
                    *task.get("notes", []),
                    *task.get("acceptance_criteria", []),
                ]
            )
            score = _text_match_score(body, query)
            if score <= 0:
                continue
            results.append(
                {
                    "source_type": "workboard",
                    "path": f"repo://task/{plan.get('id')}/{task.get('id')}",
                    "line": None,
                    "score": 0.95 + score,
                    "snippet": _clip_text(body, max_lines=4, max_chars=220),
                    "why_matched": f"Matched task content in phase `{phase.get('name')}`.",
                    "symbol": None,
                    "plan_id": plan.get("id"),
                    "task_id": task.get("id"),
                }
            )

    for note in (current.get("session") or {}).get("notes", []):
        score = _text_match_score(note.get("note", ""), query)
        if score <= 0:
            continue
        results.append(
            {
                "source_type": "workboard",
                "path": "repo://session/current",
                "line": None,
                "score": 0.65 + score,
                "snippet": note.get("note", ""),
                "why_matched": "Matched recent session note.",
                "symbol": None,
            }
        )

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]


def _recent_context_hits(query: str, repo_root: Path, *, limit: int = 10) -> list[dict]:
    """Search recent commits and working tree changes."""

    summary = _recent_summary(repo_root, limit=limit)
    if not summary:
        return []

    results: list[dict] = []
    for commit in summary.get("commits", []):
        body = f"{commit.get('subject', '')}\n{commit.get('short_commit', '')}"
        score = _text_match_score(body, query)
        if score <= 0:
            continue
        results.append(
            {
                "source_type": "recent",
                "path": commit.get("commit"),
                "line": None,
                "score": 0.6 + score,
                "snippet": commit.get("subject", ""),
                "why_matched": "Matched a recent git commit.",
                "symbol": None,
            }
        )

    for change in summary.get("working_tree", []):
        body = f"{change.get('status', '')} {change.get('path', '')}"
        score = _text_match_score(body, query)
        if score <= 0:
            continue
        results.append(
            {
                "source_type": "recent",
                "path": change.get("path"),
                "line": None,
                "score": 0.55 + score,
                "snippet": body.strip(),
                "why_matched": "Matched a changed working-tree path.",
                "symbol": None,
            }
        )

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]


def _children_context_hits(
    query: str,
    repo_root: Path,
    *,
    snapshot: dict | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search detected child-project metadata."""

    children = snapshot if snapshot is not None else _children_snapshot(repo_root)
    if not children:
        return []

    try:
        from .children import describe_child
    except Exception:
        describe_child = lambda child: child.get("path", "")

    results: list[dict] = []
    for child in children.get("children", []):
        body = "\n".join(
            [
                child.get("path", ""),
                child.get("status", ""),
                *child.get("manifests", []),
                *child.get("context_docs", []),
                child.get("note") or "",
            ]
        )
        score = _text_match_score(body, query)
        if score <= 0:
            continue
        results.append(
            {
                "source_type": "children",
                "path": child.get("path"),
                "line": None,
                "score": 0.75 + score + (0.1 if child.get("status") == "unknown" else 0.0),
                "snippet": describe_child(child),
                "why_matched": "Matched a detected child subtree or its manifest metadata.",
                "symbol": None,
                "child_status": child.get("status"),
            }
        )

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]


def scan_context(root: str = ".", scope: str | None = None, limit: int = 10) -> dict:
    """Return a broad snapshot of repo surfaces available for context lookup."""

    repo_root = _repo_root(root)
    scope_path = _scope_path(scope, repo_root)
    docs = _context_docs_for_root(repo_root)
    code_files = [path for path in _code_files(repo_root) if _scope_includes(path, scope_path)]
    test_files = [path for path in _test_files(repo_root) if _scope_includes(path, scope_path)]
    generated = [path for path in _generated_context_files(repo_root) if _scope_includes(path, scope_path)]
    workboard = _workboard_snapshot(repo_root)
    children = _children_snapshot(repo_root)
    recent = _recent_summary(repo_root, limit=limit)

    plan_count = len((workboard or {}).get("listing", {}).get("plans", []))
    task_count = sum(len(phase.get("tasks", [])) for phase in ((workboard or {}).get("current", {}).get("plan") or {}).get("phases", []))
    current_summary = ((workboard or {}).get("current") or {}).get("summary")
    child_entries = [
        child for child in (children or {}).get("children", [])
        if scope_path is None or _scope_includes(repo_root / child.get("path", ""), scope_path)
    ]
    unknown_children = [child for child in child_entries if child.get("status") == "unknown"]

    surfaces = [
        {
            "source_type": "code",
            "count": len(code_files),
            "examples": [_relative_label(path, repo_root) for path in code_files[: min(3, limit)]],
        },
        {
            "source_type": "tests",
            "count": len(test_files),
            "examples": [_relative_label(path, repo_root) for path in test_files[: min(3, limit)]],
        },
        {
            "source_type": "docs",
            "count": len(docs),
            "examples": [_relative_label(doc.file, repo_root) for doc in docs[: min(3, limit)]],
        },
        {
            "source_type": "generated_context",
            "count": len(generated),
            "examples": [_relative_label(path, repo_root) for path in generated[: min(3, limit)]],
        },
        {
            "source_type": "workboard",
            "count": plan_count + task_count,
            "examples": ([current_summary.get("title")] if current_summary else [])[:1],
        },
        {
            "source_type": "children",
            "count": len(child_entries),
            "examples": [child.get("path") for child in unknown_children[: min(3, limit)] or child_entries[: min(3, limit)]],
        },
        {
            "source_type": "recent",
            "count": (recent or {}).get("counts", {}).get("commits", 0) + (recent or {}).get("counts", {}).get("working_tree_changes", 0),
            "examples": [commit.get("subject") for commit in (recent or {}).get("commits", [])[: min(2, limit)]],
        },
    ]

    fallback_used: list[str] = []
    if not docs:
        fallback_used.append("context_docs_unavailable")
    if not generated:
        fallback_used.append("generated_tldr_unavailable")
    if not workboard:
        fallback_used.append("workboard_unavailable")
    if children is None:
        fallback_used.append("children_unavailable")
    if not recent:
        fallback_used.append("recent_context_unavailable")

    evidence = [
        f"code files: {len(code_files)}",
        f"tests: {len(test_files)}",
        f"context docs: {len(docs)}",
        f"plans/tasks: {plan_count}/{task_count}",
        f"children: {len(child_entries)} ({len(unknown_children)} unknown)",
    ]
    if current_summary:
        evidence.append(f"current plan: {current_summary.get('title')} [{current_summary.get('status')}]")

    verification_commands: list[str] = []
    current_plan = ((workboard or {}).get("current") or {}).get("plan") or {}
    for phase in current_plan.get("phases", []):
        for task in phase.get("tasks", []):
            verification_commands.extend(task.get("verification_commands", []))
    verification_commands = _dedupe(verification_commands)

    recommended_next_action = (
        "Use search_context with the specific question or bug you are investigating."
        if any(surface["count"] for surface in surfaces)
        else "Index the repository and generate TLDR context before relying on context search."
    )

    return _preferred_result(
        root=str(repo_root),
        scope=str(scope_path) if scope_path else None,
        source_counts={item["source_type"]: item["count"] for item in surfaces},
        surfaces=surfaces,
        current_plan=current_summary,
        children={
            "count": len(child_entries),
            "unknown_count": len(unknown_children),
            "examples": [child.get("path") for child in unknown_children[: min(5, limit)]],
        },
        recent_context={
            "since": (recent or {}).get("since"),
            "counts": (recent or {}).get("counts", {}),
            "commits": (recent or {}).get("commits", [])[: min(3, limit)],
            "working_tree": (recent or {}).get("working_tree", [])[: min(3, limit)],
        },
        summary=(
            f"Context scan found {len(code_files)} code files, {len(docs)} docs, "
            f"{len(child_entries)} child subtrees, and {plan_count} plans under {_relative_label(scope_path or repo_root, repo_root)}."
        ),
        confidence=0.88 if code_files else 0.55,
        evidence=evidence,
        verification_commands=verification_commands,
        recommended_next_action=recommended_next_action,
        fallback_used=fallback_used,
        next_best_tool="search_context",
    )


def search_context(
    query: str,
    *,
    root: str = ".",
    scope: str | None = None,
    source_types: list[str] | None = None,
    limit: int = 10,
) -> dict:
    """Search across code, docs, workboard, and recent change context."""

    repo_root = _repo_root(root)
    allowed = set(source_types or ["code", "docs", "workboard", "children", "recent"])

    hits: list[dict] = []
    fallback_used: list[str] = []
    child_snapshot = _children_snapshot(repo_root) if "children" in allowed else None

    if "code" in allowed:
        try:
            code_hits = _code_context_hits(query, repo_root, scope=scope, limit=limit)
        except Exception:
            code_hits = []
            fallback_used.append("code_context_hits_unavailable")
        hits.extend(code_hits)
    if "docs" in allowed:
        doc_hits = _doc_context_hits(query, repo_root, scope=scope, limit=limit)
        hits.extend(doc_hits)
        if not doc_hits:
            fallback_used.append("doc_context_hits_unavailable")
    if "workboard" in allowed:
        work_hits = _workboard_context_hits(query, repo_root, limit=limit)
        hits.extend(work_hits)
        if not work_hits:
            fallback_used.append("workboard_hits_unavailable")
    if "children" in allowed:
        child_hits = _children_context_hits(query, repo_root, snapshot=child_snapshot, limit=limit)
        hits.extend(child_hits)
        if child_snapshot is None:
            fallback_used.append("children_hits_unavailable")
    if "recent" in allowed:
        recent_hits = _recent_context_hits(query, repo_root, limit=limit)
        hits.extend(recent_hits)
        if not recent_hits:
            fallback_used.append("recent_hits_unavailable")

    if not hits:
        fallback_used.append("no_ranked_context_hits")

    hits.sort(key=lambda item: item.get("score", 0), reverse=True)

    seen: set[str] = set()
    ranked_hits: list[dict] = []
    for item in hits:
        key = f"{item.get('source_type')}:{item.get('path')}:{item.get('line')}:{item.get('snippet')}"
        if key in seen:
            continue
        seen.add(key)
        ranked_hits.append(item)
        if len(ranked_hits) >= limit:
            break

    grouped_hits: dict[str, list[dict]] = {}
    for item in ranked_hits:
        grouped_hits.setdefault(item["source_type"], []).append(item)

    top = ranked_hits[0] if ranked_hits else None
    verification_commands: list[str] = []
    if top and top["source_type"] == "code" and top.get("path"):
        verification_commands = test_map(path=top["path"], root=str(repo_root)).get("verification_commands", [])
        recommended_next_action = f"Inspect {top['path']}:{top.get('line')} with edit_context before changing code."
        next_best_tool = "edit_context"
    elif top and top["source_type"] == "workboard":
        recommended_next_action = "Use the active workboard context to decide the next executable task."
        next_best_tool = "verify_change" if top.get("task_id") else "change_plan"
    elif top and top["source_type"] == "docs":
        recommended_next_action = "Read the matching project guidance, then narrow on the relevant code path."
        next_best_tool = "change_plan"
    elif top and top["source_type"] == "children":
        recommended_next_action = (
            "Inspect the child subtree and decide whether it is intentionally merged. "
            "Humans can acknowledge it with `tldr children merge` or `tldr children ignore`."
        )
        next_best_tool = "scan_context"
    elif top and top["source_type"] == "recent":
        recommended_next_action = "Inspect the recent change first to understand what shifted since the last checkpoint."
        next_best_tool = "scan_context"
    else:
        recommended_next_action = "Broaden the query or run scan_context to see what context surfaces are available."
        next_best_tool = "scan_context"

    evidence = [
        f"{item['source_type']}: {_relative_label(item.get('path') or '', repo_root)}"
        + (f":{item.get('line')}" if item.get("line") else "")
        for item in ranked_hits[:5]
    ]

    return _preferred_result(
        query=query,
        root=str(repo_root),
        scope=str(_scope_path(scope, repo_root)) if scope else None,
        source_types=sorted(allowed),
        grouped_hits=grouped_hits,
        ranked_hits=ranked_hits,
        summary=(
            f"Found {len(ranked_hits)} ranked context hit(s) for `{query}` across "
            f"{', '.join(sorted(grouped_hits)) or 'no'} surfaces."
        ),
        confidence=0.9 if top and top["source_type"] == "code" else 0.8 if top else 0.3,
        evidence=evidence,
        verification_commands=verification_commands,
        recommended_next_action=recommended_next_action,
        fallback_used=fallback_used,
        next_best_tool=next_best_tool,
    )
