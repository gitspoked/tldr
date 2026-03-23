"""RAG engine — retrieve from Qdrant + FalkorDB, synthesize via LiteLLM."""

from pathlib import Path
import re
import subprocess
from ._shared import get_embedder, get_grapher
from .lazy import load_module

GENERIC_MAINTENANCE_PATTERNS = (
    re.compile(r"\b(docstring|docstrings|comment|comments|documentation)\b", re.IGNORECASE),
    re.compile(r"\bversion control\b", re.IGNORECASE),
    re.compile(r"\bgit\b", re.IGNORECASE),
    re.compile(r"\btype annotations?\b", re.IGNORECASE),
    re.compile(r"\bimports?\b", re.IGNORECASE),
)

GOAL_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "code",
    "repo",
    "local",
    "first",
    "command",
    "plan",
    "pipeline",
    "continue",
    "active",
    "should",
    "work",
    "next",
}


def _litellm():
    """Load litellm only when an LLM-backed step is needed."""

    return load_module("litellm")


def _embedder_settings() -> tuple[str, str]:
    """Load chat model settings lazily from the embedder module."""

    from .embedder import CHAT_MODEL, _api_base

    return CHAT_MODEL, _api_base()


def _coding_tools():
    """Load router-friendly coding tools lazily for planning helpers."""

    return load_module("tldreadme.coding_tools")


def _workboard():
    """Load the workboard lazily for plan-aware suggestion flows."""

    return load_module("tldreadme.workboard")


def _repo_root(path: str | None = None) -> Path:
    """Resolve a repository root for planning-oriented helpers."""

    return Path(path or ".").resolve()


def _work_root(path: str | None = None) -> Path:
    """Return the workboard root beneath the repository root."""

    return _repo_root(path) / ".tldr" / "work"


def _dedupe(items: list[str]) -> list[str]:
    """Deduplicate while preserving order."""

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _display_path(path: str, repo_root: Path) -> str:
    """Render a repo-relative path when possible."""

    try:
        return str(Path(path).resolve().relative_to(repo_root))
    except Exception:
        return path


def _read_repo_text(repo_root: Path, relative_path: str) -> str:
    """Read a repo-relative file when present."""

    try:
        return (repo_root / relative_path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _is_low_signal_goal(text: str) -> bool:
    """Return whether a proposed goal reads like generic maintenance fluff."""

    return any(pattern.search(text) for pattern in GENERIC_MAINTENANCE_PATTERNS)


def _goal_keywords(*parts: str) -> set[str]:
    """Return normalized meaningful tokens for comparing candidate intent."""

    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", " ".join(parts).lower())
        if len(token) >= 4 and token not in GOAL_STOPWORDS
    }
    return tokens


def _candidate_is_covered_by_active_plan(candidate: dict, active_plan: dict | None) -> bool:
    """Return whether a generic candidate is already covered by the active plan."""

    if not active_plan:
        return False

    active_keywords = _goal_keywords(active_plan.get("title", ""), active_plan.get("goal", ""))
    candidate_keywords = _goal_keywords(candidate.get("title", ""), candidate.get("goal", ""))
    shared_keywords = active_keywords & candidate_keywords
    shared_files = set(active_plan.get("files", [])) & set(candidate.get("files", []))

    if len(shared_keywords) >= 3:
        return True
    if len(shared_keywords) >= 2 and shared_files:
        return True
    return False


def _goal_candidate(
    *,
    candidate_id: str,
    title: str,
    goal: str,
    why_now: str,
    files: list[str],
    evidence: list[str],
    verification_commands: list[str],
    priority: float,
    source: str,
) -> dict:
    """Build a normalized planning candidate."""

    return {
        "id": candidate_id,
        "title": title,
        "goal": goal,
        "why_now": why_now,
        "files": _dedupe(files),
        "evidence": _dedupe(evidence),
        "verification_commands": _dedupe(verification_commands),
        "priority": round(priority, 2),
        "source": source,
    }


def _planning_snapshot(path: str | None = None) -> dict:
    """Gather low-cost grounded planning signals from repo state."""

    repo_root = _repo_root(path)
    snapshot = {
        "repo_root": str(repo_root),
        "repo_next_action": None,
        "scan_context": None,
        "current": {},
    }

    try:
        snapshot["repo_next_action"] = _coding_tools().repo_next_action(root=str(repo_root))
    except Exception:
        snapshot["repo_next_action"] = None

    try:
        snapshot["scan_context"] = _coding_tools().scan_context(root=str(repo_root), limit=5)
    except Exception:
        snapshot["scan_context"] = None

    try:
        snapshot["current"] = _workboard().current_plan(root=_work_root(str(repo_root)))
    except Exception:
        snapshot["current"] = {}

    return snapshot


def _active_plan_candidate(snapshot: dict, repo_root: Path) -> dict | None:
    """Return a candidate that resumes the active tracked plan."""

    current = snapshot.get("current") or {}
    plan = current.get("plan") or {}
    session = current.get("session") or {}
    if not plan or plan.get("status") in {"done", "archived"}:
        return None

    tasks = [
        task
        for phase in plan.get("phases", [])
        for task in phase.get("tasks", [])
        if task.get("status") != "done"
    ]
    current_task = next((task for task in tasks if task.get("id") == session.get("current_task_id")), None)
    top_task = current_task or (tasks[0] if tasks else None)

    files = list(plan.get("scope", []))
    if top_task:
        files.extend(top_task.get("files", []))
    files = [_display_path(file_path, repo_root) for file_path in files]

    verification_commands = list(session.get("verification_commands", []))
    if top_task:
        verification_commands.extend(top_task.get("verification_commands", []))

    evidence = [
        f"active plan: {plan.get('title')}",
        f"plan status: {plan.get('status')}",
    ]
    if session.get("current_phase"):
        evidence.append(f"current phase: {session.get('current_phase')}")
    if current_task:
        evidence.append(f"current task: {current_task.get('title')} [{current_task.get('status')}]")
    overlaps = current.get("overlaps") or []
    if overlaps:
        evidence.append(f"overlaps: {len(overlaps)} active")

    why_now = (
        session.get("next_action")
        or session.get("current_focus")
        or f"Resume the active plan `{plan.get('title')}` before creating unrelated work."
    )

    return _goal_candidate(
        candidate_id=f"resume-{plan.get('id')}",
        title=f"Continue active plan: {plan.get('title')}",
        goal=plan.get("goal") or plan.get("title") or "Continue the active plan",
        why_now=why_now,
        files=files,
        evidence=evidence,
        verification_commands=verification_commands,
        priority=1.0,
        source="active_plan",
    )


def _feature_gap_candidates(snapshot: dict, repo_root: Path) -> list[dict]:
    """Return grounded feature-gap candidates from repo code and docs."""

    candidates: list[dict] = []
    cli_text = _read_repo_text(repo_root, "tldreadme/cli.py")
    watcher_text = _read_repo_text(repo_root, "tldreadme/watcher.py")
    readme_text = _read_repo_text(repo_root, "README.md")
    notes_text = _read_repo_text(repo_root, "TLDREADME.md")
    audit_docs_present = any(token in (readme_text + "\n" + notes_text).lower() for token in ("audit", "semgrep", "pip-audit", "gitleaks", "garak"))

    if "def audit(" not in cli_text:
        evidence = [
            "CLI exposes doctor/summary/children but no audit command.",
            "No tracked `tldreadme/audit.py` module exists yet.",
        ]
        if audit_docs_present:
            evidence.append("Project notes already point toward a local audit workflow.")
        candidates.append(
            _goal_candidate(
                candidate_id="local-audit-pipeline",
                title="Add local tldr audit pipeline",
                goal="Add a local-first `tldr audit` command that runs dependency, code, secrets, and LLM/adversarial checks with actionable local reporting.",
                why_now="The repo already has doctor, summary, workboard, and router surfaces but still lacks a dedicated local security audit workflow.",
                files=[
                    "tldreadme/cli.py",
                    "tldreadme/runtime.py",
                    "tldreadme/audit.py",
                    "README.md",
                ],
                evidence=evidence,
                verification_commands=[
                    ".venv/bin/python -m pytest -q tests/test_cli.py tests/test_runtime.py",
                ],
                priority=0.93 if audit_docs_present else 0.87,
                source="feature_gap",
            )
        )

    if "generate_claude_md" not in watcher_text:
        candidates.append(
            _goal_candidate(
                candidate_id="watcher-context-regeneration",
                title="Regenerate TLDR context during watch mode",
                goal="Extend `tldr watch` so code changes refresh generated `.claude/TLDR.md` and `.claude/TLDR_CONTEXT.md` instead of only updating embeddings and graph state.",
                why_now="The watch path updates embeddings and graph state, but human and LLM bootstrap context can still drift until the next full init.",
                files=[
                    "tldreadme/watcher.py",
                    "tldreadme/generator.py",
                    "tests/test_generator.py",
                ],
                evidence=[
                    "watcher.py currently reparses code and updates Qdrant/FalkorDB only.",
                    "Generator output is now source-driven and deterministic, so watch-mode regeneration is feasible.",
                ],
                verification_commands=[
                    ".venv/bin/python -m pytest -q tests/test_generator.py",
                ],
                priority=0.78,
                source="feature_gap",
            )
        )

    return candidates


def _format_goal_candidates(candidates: list[dict], repo_root: Path) -> str:
    """Render ranked candidates into a concise Markdown summary."""

    if not candidates:
        return "### Next Goals for Codebase Review\n\nNo grounded feature candidates were found. Re-run `repo_lookup` and `change_plan` against a narrower scope."

    lines = ["### Next Goals for Codebase Review", ""]
    for index, candidate in enumerate(candidates[:5], start=1):
        files = ", ".join(f"`{_display_path(path, repo_root)}`" for path in candidate.get("files", [])[:4]) or "`(no file focus yet)`"
        lines.append(f"#### Goal {index}: {candidate['title']}")
        lines.append("")
        lines.append(f"**What to do**: {candidate['goal']}")
        lines.append("")
        lines.append(f"**Why now**: {candidate['why_now']}")
        lines.append("")
        lines.append(f"**Files to touch**: {files}")
        if candidate.get("evidence"):
            lines.append("")
            lines.append("**Evidence**:")
            for item in candidate["evidence"][:4]:
                lines.append(f"- {item}")
        if candidate.get("verification_commands"):
            lines.append("")
            lines.append("**Verification**:")
            for command in candidate["verification_commands"][:3]:
                lines.append(f"- `{command}`")
        lines.append("")
    return "\n".join(lines).strip()


def ask_question(question: str, scope: str | None = None) -> str:
    """Full RAG pipeline: retrieve relevant code, synthesize answer."""

    embedder = get_embedder()
    grapher = get_grapher()

    # 1. Semantic retrieval from Qdrant
    similar_chunks = embedder.search_similar(question, limit=10)

    # 2. Graph retrieval — if question mentions a symbol, get its neighborhood
    graph_context = []
    for chunk in similar_chunks[:3]:
        name = chunk.get("symbol_name", "")
        if name:
            callers = grapher.get_callers(name)
            callees = grapher.get_callees(name)
            graph_context.append({
                "symbol": name,
                "callers": callers[:5],
                "callees": callees[:5],
            })

    # 3. Build context
    context = _build_context(similar_chunks, graph_context)

    # 4. Synthesize via LLM
    return _synthesize(question, context)


def read_similar(query: str, limit: int = 5) -> list[dict]:
    """Return actual code bodies of semantically similar symbols."""
    embedder = get_embedder()
    results = embedder.search_similar(query, limit=limit)
    # Return full code content, not just metadata
    return [
        {
            "symbol": r["symbol_name"],
            "kind": r["kind"],
            "file": r["file"],
            "line": r["line"],
            "signature": r["signature"],
            "code": r["content"],  # actual body
            "score": r["score"],
        }
        for r in results
    ]


def read_symbol(name: str) -> dict | None:
    """Return everything known about a symbol: body, callers, callees, context."""
    embedder = get_embedder()
    grapher = get_grapher()

    # Find the symbol by name in Qdrant
    results = embedder.search_similar(f"function {name}", limit=5)
    match = next((r for r in results if r["symbol_name"] == name), None)
    if not match:
        return None

    callers = grapher.get_callers(name)
    callees = grapher.get_callees(name)
    dependents = grapher.get_dependents(name)

    return {
        "symbol": name,
        "kind": match["kind"],
        "file": match["file"],
        "line": match["line"],
        "code": match["content"],
        "signature": match["signature"],
        "callers": callers,
        "callees": callees,
        "dependents": dependents,
    }


def read_module(module_path: str) -> dict:
    """Return full knowledge about a module/directory."""
    grapher = get_grapher()
    symbols = grapher.get_module_symbols(module_path)
    return {
        "module": module_path,
        "symbols": symbols,
        "symbol_count": len(symbols),
    }


def read_flow(entry: str, depth: int = 5) -> list[dict]:
    """Trace execution flow from an entry point."""
    grapher = get_grapher()
    return grapher.get_flow(entry, max_depth=depth)


def tldr(path: str) -> str:
    """Generate a TL;DR summary of a module/directory via RAG."""
    grapher = get_grapher()
    symbols = grapher.get_module_symbols(path)

    context = f"Module: {path}\n"
    context += f"Symbols ({len(symbols)}):\n"
    for s in symbols:
        context += f"  {s['kind']} {s['name']} — {s['signature']}\n"

    prompt = (
        f"You are a senior developer. Give a concise TL;DR of this module.\n"
        f"What does it do? What's the architecture? What are the key entry points?\n\n"
        f"{context}"
    )

    chat_model, api_base = _embedder_settings()
    resp = _litellm().completion(
        model=chat_model,
        messages=[{"role": "user", "content": prompt}],
        api_base=api_base,
        max_tokens=1000,
    )
    return resp.choices[0].message.content


def suggest_goals(path: str) -> dict:
    """Suggest grounded next goals using active plans and concrete repo feature gaps."""

    repo_root = _repo_root(path)
    snapshot = _planning_snapshot(path)

    candidates: list[dict] = []
    active_plan = _active_plan_candidate(snapshot, repo_root)
    if active_plan:
        candidates.append(active_plan)
    candidates.extend(_feature_gap_candidates(snapshot, repo_root))

    filtered_candidates: list[dict] = []
    seen_candidates: set[str] = set()
    for candidate in candidates:
        text = f"{candidate['title']} {candidate['goal']} {candidate['why_now']}"
        if candidate.get("source") != "active_plan" and _is_low_signal_goal(text):
            continue
        if candidate.get("source") != "active_plan" and _candidate_is_covered_by_active_plan(candidate, active_plan):
            continue
        key = f"{candidate['title'].strip().lower()}::{candidate['goal'].strip().lower()}"
        if key in seen_candidates:
            continue
        seen_candidates.add(key)
        filtered_candidates.append(candidate)

    ranked_candidates = sorted(filtered_candidates, key=lambda item: item.get("priority", 0), reverse=True)
    top_goal = ranked_candidates[0]["goal"] if ranked_candidates else None

    scan_context = snapshot.get("scan_context") or {}
    current = snapshot.get("current") or {}
    current_summary = current.get("summary") or {}
    current_session = current.get("session") or {}
    current_plan = current.get("plan") or {}

    return {
        "module": path,
        "analysis": {
            "repo_root": str(repo_root),
            "code_file_count": (scan_context.get("source_counts") or {}).get("code", 0),
            "test_file_count": (scan_context.get("source_counts") or {}).get("tests", 0),
            "doc_count": (scan_context.get("source_counts") or {}).get("docs", 0),
            "plan_count": (scan_context.get("source_counts") or {}).get("workboard", 0),
            "unknown_child_count": ((scan_context.get("children") or {}).get("unknown_count") or 0),
            "candidate_count": len(ranked_candidates),
            "current_plan": current_summary,
            "current_phase": current_session.get("current_phase"),
            "current_task_id": current_session.get("current_task_id"),
            "next_action": current_session.get("next_action"),
        },
        "top_goal": top_goal,
        "candidate_goals": ranked_candidates,
        "suggested_goals": _format_goal_candidates(ranked_candidates, repo_root),
        "recommended_next_action": (
            current_session.get("next_action")
            or current_session.get("current_focus")
            or (snapshot.get("repo_next_action") or {}).get("recommended_next_action")
            or ("Start with the top ranked candidate and confirm it with repo_lookup." if ranked_candidates else "Use repo_lookup to gather more grounded context.")
        ),
        "active_plan": {
            "id": current_plan.get("id"),
            "title": current_plan.get("title"),
            "goal": current_plan.get("goal"),
        } if current_plan else None,
    }


def best_question(goal: str, path: str | None = None) -> dict:
    """Derive a concrete next question from repo lookup and plan signals."""

    repo_root = _repo_root(path)

    try:
        lookup = _coding_tools().repo_lookup(query=goal, root=str(repo_root), limit=8)
    except Exception:
        lookup = {}

    try:
        plan = _coding_tools().change_plan(goal, root=str(repo_root))
    except Exception:
        plan = {}

    candidate_files = [_display_path(file_path, repo_root) for file_path in plan.get("candidate_files", [])]
    likely_symbols = _dedupe(list(plan.get("likely_symbols", [])))
    verification_commands = _dedupe(list(plan.get("verification_commands", [])))
    risks = _dedupe(list(plan.get("risks", [])))

    ranked_hits = lookup.get("ranked_hits", []) or []
    for hit in ranked_hits:
        hit_path = hit.get("path")
        if hit_path:
            candidate_files.append(_display_path(hit_path, repo_root))
    candidate_files = _dedupe(candidate_files)

    if candidate_files and likely_symbols:
        the_question = (
            f"What is the smallest end-to-end change needed in `{candidate_files[0]}` around "
            f"`{likely_symbols[0]}` to achieve \"{goal}\", and which verification command should prove it?"
        )
    elif candidate_files:
        the_question = (
            f"What is the smallest end-to-end change needed in `{candidate_files[0]}` to achieve "
            f"\"{goal}\", and which verification command should prove it?"
        )
    elif likely_symbols:
        the_question = (
            f"Which symbol should be changed first to achieve \"{goal}\", and what verification should prove the change is correct?"
        )
    else:
        the_question = (
            f"What concrete file or symbol should be targeted first to achieve \"{goal}\" safely?"
        )

    answer_lines = [
        f"Goal: {goal}",
        "",
        "Most grounded next move:",
        f"- {lookup.get('recommended_next_action') or plan.get('recommended_next_action') or 'Narrow the first edit target before changing code.'}",
    ]
    if candidate_files:
        answer_lines.append("- Primary edit targets: " + ", ".join(f"`{path}`" for path in candidate_files[:4]))
    if likely_symbols:
        answer_lines.append("- Likely symbols: " + ", ".join(f"`{name}`" for name in likely_symbols[:4]))
    if risks:
        answer_lines.append("- Risks: " + "; ".join(risks[:3]))
    if verification_commands:
        answer_lines.append("- Verification: " + "; ".join(f"`{command}`" for command in verification_commands[:3]))
    if lookup.get("evidence"):
        answer_lines.append("")
        answer_lines.append("Grounding evidence:")
        answer_lines.extend(f"- {item}" for item in lookup.get("evidence", [])[:4])

    return {
        "goal": goal,
        "best_question": the_question,
        "answer": "\n".join(answer_lines).strip(),
        "relevant_symbols": likely_symbols[:8],
        "relevant_files": candidate_files[:8],
        "recommended_next_action": lookup.get("recommended_next_action") or plan.get("recommended_next_action"),
        "verification_commands": verification_commands[:5],
        "lookup": {
            "lookup_mode": lookup.get("lookup_mode"),
            "specialist_tool": lookup.get("specialist_tool"),
            "summary": lookup.get("summary"),
        },
        "plan": {
            "summary": plan.get("summary"),
            "candidate_files": plan.get("candidate_files", []),
            "likely_symbols": plan.get("likely_symbols", []),
            "ordered_steps": plan.get("ordered_steps", []),
        },
    }


def auto_iterate(path: str, goal: str | None = None, rounds: int = 2) -> dict:
    """Repeat the backwards-flow loop for a few rounds.

    Uses the codebase analysis to seed the first goal, then asks the model for
    the next highest-value follow-up goal after each answer.
    """

    rounds = max(1, min(rounds, 5))
    goals_result = suggest_goals(path)
    candidate_goals = list(goals_result.get("candidate_goals", []))
    current_goal = goal.strip() if goal else (goals_result.get("top_goal") or (candidate_goals[0]["goal"] if candidate_goals else goals_result["suggested_goals"][:800]))
    iterations = []
    used_goals: set[str] = set()

    for index in range(1, rounds + 1):
        step = best_question(current_goal, path=path)
        iterations.append(
            {
                "round": index,
                "goal": current_goal,
                "best_question": step["best_question"],
                "answer": step["answer"],
                "relevant_symbols": step["relevant_symbols"],
                "relevant_files": step["relevant_files"],
                "recommended_next_action": step.get("recommended_next_action"),
                "verification_commands": step.get("verification_commands", []),
            }
        )
        used_goals.add(current_goal)

        if index == rounds:
            break

        next_goal = next(
            (candidate["goal"] for candidate in candidate_goals if candidate.get("goal") and candidate["goal"] not in used_goals),
            None,
        )
        if not next_goal:
            break
        current_goal = next_goal

    return {
        "path": path,
        "initial_analysis": goals_result["analysis"],
        "top_goal": goals_result.get("top_goal"),
        "candidate_goals": candidate_goals,
        "suggested_goals": goals_result["suggested_goals"],
        "iterations": iterations,
        "rounds_completed": len(iterations),
    }


def read_recent(scope: str | None = None, days: int = 7) -> list[dict]:
    """What changed recently? Uses git log to find recently modified symbols.

    Returns recently changed files with their modified symbols cross-referenced
    against the indexed knowledge in Qdrant/FalkorDB.
    """
    cmd = [
        "git", "log",
        f"--since={days} days ago",
        "--name-only",
        "--pretty=format:%H|%an|%s|%ai",
        "--diff-filter=AMR",
    ]
    if scope:
        cmd.append("--")
        cmd.append(scope)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd=scope or ".",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    commits = []
    current_commit = None

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line and len(line.split("|")) >= 4:
            parts = line.split("|", 3)
            current_commit = {
                "hash": parts[0][:8],
                "author": parts[1],
                "message": parts[2],
                "date": parts[3],
                "files": [],
            }
            commits.append(current_commit)
        elif current_commit is not None:
            current_commit["files"].append(line)

    # Cross-reference changed files with indexed symbols
    grapher = get_grapher()
    recent = []
    seen_files = set()

    for commit in commits:
        for f in commit["files"]:
            if f in seen_files:
                continue
            seen_files.add(f)

            symbols = []
            try:
                qresult = grapher.graph.query(
                    "MATCH (f:File {path: $path})-[:DEFINES]->(s:Symbol) "
                    "RETURN s.name, s.kind, s.signature, s.line",
                    {"path": f},
                )
                symbols = [
                    {"name": r[0], "kind": r[1], "signature": r[2], "line": r[3]}
                    for r in qresult.result_set
                ]
            except Exception:
                pass

            recent.append({
                "file": f,
                "last_commit": commit["hash"],
                "author": commit["author"],
                "message": commit["message"],
                "date": commit["date"],
                "symbols_in_file": symbols,
            })

    return recent


def _build_context(chunks: list[dict], graph_ctx: list[dict]) -> str:
    """Assemble retrieved code + graph info into LLM context."""
    parts = []

    # Code chunks — show actual code
    for c in chunks[:5]:
        parts.append(
            f"### {c['kind']} `{c['symbol_name']}` ({c['file']}:{c['line']})\n"
            f"```{c['language']}\n{c['content'][:1500]}\n```\n"
        )

    # Graph context
    for g in graph_ctx:
        if g["callers"]:
            callers = ", ".join(f"{c['name']} ({c['file']})" for c in g["callers"])
            parts.append(f"**{g['symbol']}** is called by: {callers}\n")
        if g["callees"]:
            callees = ", ".join(f"{c['name']} ({c['file']})" for c in g["callees"])
            parts.append(f"**{g['symbol']}** calls: {callees}\n")

    return "\n".join(parts)


def _synthesize(question: str, context: str) -> str:
    """Ask the LLM to answer based on retrieved context."""
    prompt = (
        f"You are a senior developer who deeply knows this codebase. "
        f"Answer the following question using ONLY the code context provided. "
        f"Include specific file paths and line numbers. Be precise.\n\n"
        f"## Retrieved Code Context\n\n{context}\n\n"
        f"## Question\n\n{question}"
    )

    chat_model, api_base = _embedder_settings()
    resp = _litellm().completion(
        model=chat_model,
        messages=[{"role": "user", "content": prompt}],
        api_base=api_base,
        max_tokens=2000,
    )
    return resp.choices[0].message.content
