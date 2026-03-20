"""Daisy chains — composed tool sequences for common workflows.

The insight: 80% of the time you need fast lookup → code → done.
15% you need impact analysis. 5% you need semantic reasoning.
Chains start fast and go deeper only when needed.
"""

from typing import Optional
from .hot_index import HotIndex
from .search import rg_search, rg_files, rg_count, format_hits_for_llm
from . import rag


def know(name: str, hot_index: Optional[HotIndex] = None, root: str = ".") -> dict:
    """The primary chain: know everything about a symbol as fast as possible.

    Chain: hot_index → rg (definition + usages) → graph (callers/callees) → done.
    Stops as soon as it has enough. No LLM unless you need synthesis.

    This is the tool Claude will call 80% of the time.
    """
    result = {"name": name, "found": False}

    # Step 1: Hot index (instant — cached top 100)
    if hot_index:
        entry = hot_index.lookup(name)
        if entry:
            result["found"] = True
            result["kind"] = entry.kind
            result["importance"] = entry.importance
            result["hit_count"] = entry.hit_count
            result["locations"] = entry.locations
            # If it's in the hot index, we already know a lot. Get the code.
            definition = next((l for l in entry.locations if l.get("definition")), None)
            if definition:
                hits = rg_search(
                    f"(fn |struct |class |def |interface |enum |trait ){name.split('::')[-1]}",
                    [definition["file"]], context=20, max_results=1,
                )
                if hits:
                    result["code"] = hits[0].text + "\n" + "\n".join(hits[0].after)
            return result

    # Step 2: rg search (fast — milliseconds)
    search_name = name.split("::")[-1] if "::" in name else name
    hits = rg_search(
        f"(fn |struct |class |def |interface |enum |trait |pub |async ){search_name}",
        [root], context=15, max_results=5,
    )

    if hits:
        result["found"] = True
        result["definition"] = {
            "file": hits[0].file,
            "line": hits[0].line,
            "code": "\n".join(hits[0].before) + "\n" + hits[0].text + "\n" + "\n".join(hits[0].after),
        }
        # Also show where it's used
        usage_files = rg_files(search_name, [root])
        result["used_in"] = usage_files[:15]
        result["usage_count"] = len(usage_files)

    # Step 3: Graph (if available — callers/callees)
    try:
        callers = rag.read_symbol(name)
        if callers:
            result["callers"] = callers.get("callers", [])
            result["callees"] = callers.get("callees", [])
    except Exception:
        pass  # graph not available, that's fine

    return result


def impact(name: str, root: str = ".") -> dict:
    """Impact chain: what breaks if I change this?

    Chain: rg (find all usages) → graph (transitive dependents) → severity assessment.
    This is the 15% tool — use before modifying anything load-bearing.
    """
    search_name = name.split("::")[-1] if "::" in name else name

    # Step 1: Direct usages via rg
    counts = rg_count(search_name, [root])
    total_references = sum(counts.values())
    files_affected = list(counts.keys())

    # Step 2: Graph-based transitive dependents
    dependents = []
    try:
        from .grapher import CodeGrapher
        grapher = CodeGrapher()
        dependents = grapher.get_dependents(name)
    except Exception:
        pass

    # Step 3: Assess severity
    if total_references > 20:
        severity = "high"
        warning = f"Load-bearing symbol — {total_references} references across {len(files_affected)} files"
    elif total_references > 5:
        severity = "medium"
        warning = f"Moderately connected — {total_references} references across {len(files_affected)} files"
    elif total_references > 0:
        severity = "low"
        warning = f"Lightly used — {total_references} references in {len(files_affected)} files"
    else:
        severity = "orphan"
        warning = "No references found — possibly unused or only used dynamically"

    return {
        "name": name,
        "severity": severity,
        "warning": warning,
        "total_references": total_references,
        "files_affected": files_affected[:20],
        "transitive_dependents": dependents[:20],
    }


def discover(query: str, root: str = ".", hot_index: Optional[HotIndex] = None) -> dict:
    """Discovery chain: find relevant code when you don't know the exact name.

    Chain: rg (literal search) → semantic (Qdrant) → merge + deduplicate → rank.
    Combines exact text matching with semantic similarity.
    The 5% tool — for exploration and pattern-finding.
    """
    # Step 1: rg for exact/regex matches
    rg_hits = rg_search(query, [root], context=5, max_results=10)
    rg_results = [
        {"source": "rg", "file": h.file, "line": h.line,
         "text": h.text, "score": 1.0}
        for h in rg_hits
    ]

    # Step 2: Semantic search via Qdrant
    semantic_results = []
    try:
        similar = rag.read_similar(query, limit=10)
        semantic_results = [
            {"source": "semantic", "file": s["file"], "line": s["line"],
             "symbol": s["symbol"], "code": s["code"][:200], "score": s["score"]}
            for s in similar
        ]
    except Exception:
        pass

    # Step 3: Merge and deduplicate (by file:line)
    seen = set()
    merged = []
    for r in rg_results + semantic_results:
        key = f"{r['file']}:{r.get('line', 0)}"
        if key not in seen:
            seen.add(key)
            merged.append(r)

    # Sort by score descending
    merged.sort(key=lambda x: x.get("score", 0), reverse=True)

    return {
        "query": query,
        "rg_hits": len(rg_results),
        "semantic_hits": len(semantic_results),
        "merged": merged[:15],
    }


def explain(name: str, root: str = ".", hot_index: Optional[HotIndex] = None) -> str:
    """Full explanation chain — the everything tool.

    Chain: know → impact → discover similar → LLM synthesis.
    Returns a natural language explanation of a symbol: what it is,
    how it works, what depends on it, what's similar, and what
    you should be careful about when modifying it.

    This is the chain that makes Claude truly KNOW a symbol.
    """
    # Gather all intelligence
    knowledge = know(name, hot_index=hot_index, root=root)
    impact_info = impact(name, root=root)

    search_name = name.split("::")[-1] if "::" in name else name
    similar = discover(f"similar to {search_name}", root=root)

    # Build context for LLM
    context = f"# Symbol: {name}\n\n"

    if knowledge.get("definition"):
        d = knowledge["definition"]
        context += f"## Definition ({d['file']}:{d['line']})\n```\n{d['code']}\n```\n\n"

    context += f"## Impact: {impact_info['severity']}\n{impact_info['warning']}\n"
    if impact_info['files_affected']:
        context += "Files: " + ", ".join(f[:60] for f in impact_info['files_affected'][:5]) + "\n"
    context += "\n"

    if similar.get("merged"):
        context += "## Similar code found:\n"
        for s in similar["merged"][:3]:
            context += f"- {s.get('symbol', s.get('text', '')[:50])} ({s['file']})\n"
        context += "\n"

    # Synthesize via LLM
    try:
        return rag._synthesize(
            f"Explain {name}: what it does, how it works, what depends on it, "
            f"and what I should be careful about when modifying it.",
            context,
        )
    except Exception:
        # LLM not available — return raw context
        return context
