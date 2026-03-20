"""RAG engine — retrieve from Qdrant + FalkorDB, synthesize via LiteLLM."""

import litellm
import os
from .embedder import CodeEmbedder
from .grapher import CodeGrapher

LITELLM_URL = os.getenv("LITELLM_URL", "http://localhost:4000")
CHAT_MODEL = "chat"


def ask_question(question: str, scope: str | None = None) -> str:
    """Full RAG pipeline: retrieve relevant code, synthesize answer."""

    embedder = CodeEmbedder()
    grapher = CodeGrapher()

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
    embedder = CodeEmbedder()
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
    embedder = CodeEmbedder()
    grapher = CodeGrapher()

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
    grapher = CodeGrapher()
    symbols = grapher.get_module_symbols(module_path)
    return {
        "module": module_path,
        "symbols": symbols,
        "symbol_count": len(symbols),
    }


def read_flow(entry: str, depth: int = 5) -> list[dict]:
    """Trace execution flow from an entry point."""
    grapher = CodeGrapher()
    return grapher.get_flow(entry, max_depth=depth)


def tldr(path: str) -> str:
    """Generate a TL;DR summary of a module/directory via RAG."""
    grapher = CodeGrapher()
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

    resp = litellm.completion(
        model=CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        api_base=LITELLM_URL,
        max_tokens=1000,
    )
    return resp.choices[0].message.content


def suggest_goals(path: str) -> dict:
    """BACKWARDS FLOW: Given the code, what should we work on next?

    Analyzes the codebase state — incomplete patterns, TODOs, dead ends,
    missing tests, orphan symbols, unused imports, partially-implemented
    features — and suggests what goals make sense.
    """
    grapher = CodeGrapher()
    embedder = CodeEmbedder()

    # Gather broad codebase signal
    symbols = grapher.get_module_symbols(path)

    # Find orphans (defined but never called)
    orphans = []
    for s in symbols:
        callers = grapher.get_callers(s["name"])
        if not callers and s["kind"] in ("function", "method"):
            orphans.append(s)

    # Find stubs (very short functions — likely placeholders)
    stubs = [s for s in symbols if s.get("signature", "").endswith("...") or s.get("signature", "").endswith("pass")]

    # Find high-connectivity symbols (most called — the load-bearing walls)
    load_bearing = []
    for s in symbols[:50]:  # cap for performance
        callers = grapher.get_callers(s["name"])
        if len(callers) >= 3:
            load_bearing.append({"symbol": s["name"], "caller_count": len(callers)})
    load_bearing.sort(key=lambda x: x["caller_count"], reverse=True)

    # Semantic search for TODOs, FIXMEs, incomplete patterns
    todos = embedder.search_similar("TODO FIXME unimplemented incomplete stub", limit=10)

    # Build context and ask LLM to synthesize goals
    context = f"Module: {path}\n"
    context += f"Total symbols: {len(symbols)}\n\n"

    if orphans:
        context += f"Orphan functions (defined but never called — {len(orphans)}):\n"
        for o in orphans[:10]:
            context += f"  - {o['name']} ({o['file']})\n"
        context += "\n"

    if load_bearing:
        context += f"Load-bearing symbols (most depended on):\n"
        for lb in load_bearing[:10]:
            context += f"  - {lb['symbol']} ({lb['caller_count']} callers)\n"
        context += "\n"

    if todos:
        context += f"Incomplete/TODO patterns found:\n"
        for t in todos[:5]:
            context += f"  - {t['symbol_name']} ({t['file']}:{t['line']}): {t['signature'][:80]}\n"
        context += "\n"

    prompt = (
        "You are a principal engineer doing a codebase review.\n"
        "Based on the analysis below, suggest 3-5 concrete next goals.\n"
        "For each goal: what to do, why it matters, which files to touch.\n"
        "Prioritize by impact — what moves the needle most?\n\n"
        f"{context}"
    )

    resp = litellm.completion(
        model=CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        api_base=LITELLM_URL,
        max_tokens=1500,
    )

    return {
        "module": path,
        "analysis": {
            "total_symbols": len(symbols),
            "orphan_count": len(orphans),
            "load_bearing": load_bearing[:5],
            "todo_count": len(todos),
        },
        "suggested_goals": resp.choices[0].message.content,
    }


def best_question(goal: str, path: str | None = None) -> dict:
    """BACKWARDS FLOW: Given a goal, what's the RIGHT question to ask this codebase?

    Instead of the user guessing what to ask, TLDREADME looks at the code
    and formulates the precise question that will lead to the best outcome.
    Then it answers that question against the actual code.
    """
    embedder = CodeEmbedder()
    grapher = CodeGrapher()

    # Find code relevant to the goal
    relevant = embedder.search_similar(goal, limit=10)

    # Get graph context for the most relevant symbols
    graph_ctx = []
    for chunk in relevant[:5]:
        name = chunk.get("symbol_name", "")
        if name:
            callers = grapher.get_callers(name)
            callees = grapher.get_callees(name)
            graph_ctx.append({
                "symbol": name, "file": chunk["file"],
                "kind": chunk["kind"], "signature": chunk["signature"],
                "callers": [c["name"] for c in callers[:5]],
                "callees": [c["name"] for c in callees[:5]],
            })

    # Build context
    context = f"Goal: {goal}\n\n"
    context += "Relevant code found:\n"
    for g in graph_ctx:
        context += f"  - {g['kind']} {g['symbol']} ({g['file']})\n"
        context += f"    signature: {g['signature']}\n"
        if g['callers']:
            context += f"    called by: {', '.join(g['callers'])}\n"
        if g['callees']:
            context += f"    calls: {', '.join(g['callees'])}\n"
    context += "\n"

    # Step 1: Ask LLM to formulate the best question
    question_prompt = (
        "You are a senior developer who deeply knows this codebase.\n"
        "The user wants to achieve this goal:\n\n"
        f"  \"{goal}\"\n\n"
        "Based on the relevant code below, what is the PRECISE question\n"
        "they should be asking? Not the obvious question — the one that\n"
        "will actually unblock them. The question a dev who already knows\n"
        "the codebase would ask.\n\n"
        "Return ONLY the question, nothing else.\n\n"
        f"{context}"
    )

    question_resp = litellm.completion(
        model=CHAT_MODEL,
        messages=[{"role": "user", "content": question_prompt}],
        api_base=LITELLM_URL,
        max_tokens=200,
    )
    the_question = question_resp.choices[0].message.content.strip()

    # Step 2: Answer that question against the actual code
    code_context = _build_context(relevant, [])
    answer = _synthesize(the_question, code_context)

    return {
        "goal": goal,
        "best_question": the_question,
        "answer": answer,
        "relevant_symbols": [g["symbol"] for g in graph_ctx],
        "relevant_files": list(set(g["file"] for g in graph_ctx)),
    }


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

    resp = litellm.completion(
        model=CHAT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        api_base=LITELLM_URL,
        max_tokens=2000,
    )
    return resp.choices[0].message.content
