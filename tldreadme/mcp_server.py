"""MCP Server — the tools that make LLMs KNOW the code."""

import json
from mcp.server import Server
from mcp.types import Tool, TextContent
from . import rag


def start_server(port: int = 8900):
    """Start the MCP server with all read_* tools."""

    server = Server("tldreadme")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="read_symbol",
                description=(
                    "Get EVERYTHING about a symbol (function, class, struct): "
                    "its full source code, who calls it, what it calls, "
                    "what depends on it. Returns actual code, not just references."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Symbol name (function, class, struct)"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="read_similar",
                description=(
                    "Find code that does similar things. Returns the ACTUAL SOURCE CODE "
                    "of similar functions/classes so you can see the pattern. "
                    "Use this to understand how something is typically done in this codebase."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What kind of code to find (e.g. 'error handling', 'HTTP handler', 'ICE agent')"},
                        "limit": {"type": "integer", "description": "Max results", "default": 5},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="read_module",
                description=(
                    "Get the full map of a module/directory: every symbol, its kind, "
                    "its signature, organized by file. Instant understanding of a module."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Module/directory path"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="read_flow",
                description=(
                    "Trace execution flow from an entry point. Shows the chain of "
                    "function calls from start to end, with the actual code at each step."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "entry": {"type": "string", "description": "Entry point symbol name"},
                        "depth": {"type": "integer", "description": "Max call depth", "default": 5},
                    },
                    "required": ["entry"],
                },
            ),
            Tool(
                name="read_depends",
                description=(
                    "What breaks if you change this? Shows everything that depends on "
                    "a symbol — callers, importers, transitive dependents."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Symbol name to check dependents for"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="read_recent",
                description=(
                    "What changed recently in the indexed codebase. Shows recently "
                    "modified symbols with their diffs."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "description": "Scope to directory (optional)"},
                        "days": {"type": "integer", "description": "How many days back", "default": 7},
                    },
                },
            ),
            Tool(
                name="tldr",
                description=(
                    "TL;DR of a module, crate, or directory. RAG-powered natural language "
                    "summary: what it does, key entry points, architecture. "
                    "The god tool — instant understanding of any part of the codebase."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to summarize"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="suggest_goals",
                description=(
                    "BACKWARDS INFERENCE: Given the code, what should we work on next? "
                    "Analyzes orphan functions, load-bearing symbols, TODOs, incomplete "
                    "patterns, and synthesizes 3-5 prioritized goals with rationale. "
                    "The code tells YOU what it needs."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Module/directory to analyze"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="best_question",
                description=(
                    "BACKWARDS INFERENCE: Given a goal, what's the RIGHT question to ask? "
                    "Looks at the relevant code and formulates the precise question a "
                    "senior dev who already knows the codebase would ask — then answers it. "
                    "Returns: the question, the answer, and the relevant code segments."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "What you want to achieve"},
                        "path": {"type": "string", "description": "Scope to module/directory (optional)"},
                    },
                    "required": ["goal"],
                },
            ),
            Tool(
                name="goal_flow",
                description=(
                    "FULL BACKWARDS CHAIN: Code → Goals → Best Question → Answer. "
                    "Analyzes a module, suggests goals, picks the highest-impact one, "
                    "formulates the right question, answers it with code context. "
                    "One call to go from 'I don't know what to do' to 'here's exactly "
                    "what to do, why, and the code that matters.'"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Module/directory to analyze"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="read_grep",
                description=(
                    "Fast text search via ripgrep. For when you know the string — "
                    "exact identifiers, error messages, config keys, TODO/FIXME. "
                    "Returns actual code with surrounding context lines. "
                    "Complements read_similar (semantic) with exact text matching."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regex or text to search for"},
                        "paths": {"type": "array", "items": {"type": "string"}, "description": "Directories to search"},
                        "glob": {"type": "string", "description": "File glob filter (e.g. '*.rs', '*.{ts,tsx}')"},
                        "file_type": {"type": "string", "description": "rg type filter (e.g. 'rust', 'py', 'ts')"},
                        "context": {"type": "integer", "description": "Context lines before/after", "default": 3},
                        "max_results": {"type": "integer", "description": "Max results", "default": 20},
                    },
                    "required": ["pattern", "paths"],
                },
            ),
            Tool(
                name="read_grep_files",
                description=(
                    "Find which files contain a pattern. Fast file-level search via rg -l. "
                    "Use to quickly scope down before a deeper read_symbol or read_similar."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regex or text to search for"},
                        "paths": {"type": "array", "items": {"type": "string"}, "description": "Directories to search"},
                        "glob": {"type": "string", "description": "File glob filter"},
                        "file_type": {"type": "string", "description": "rg type filter"},
                    },
                    "required": ["pattern", "paths"],
                },
            ),
            # ── Chains (daisy-chained tool sequences) ──
            Tool(
                name="know",
                description=(
                    "THE 80% TOOL. Instant knowledge about any symbol. "
                    "Chain: hot_index (cached) → rg (definition + usages) → graph (callers/callees). "
                    "Stops as soon as it has enough. Returns actual code, all locations, "
                    "usage count. Start here. Always."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Symbol name to know about"},
                        "root": {"type": "string", "description": "Root directory to search"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="impact",
                description=(
                    "THE 15% TOOL. What breaks if I change this? "
                    "Chain: rg (all usages) → graph (transitive dependents) → severity assessment. "
                    "Returns: severity (high/medium/low/orphan), file list, warning. "
                    "Use BEFORE modifying anything."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Symbol to assess impact for"},
                        "root": {"type": "string", "description": "Root directory"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="discover",
                description=(
                    "THE 5% TOOL. Find relevant code when you don't know the exact name. "
                    "Chain: rg (literal) → semantic (Qdrant) → merge + deduplicate + rank. "
                    "Combines exact text matching with semantic similarity."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to discover"},
                        "root": {"type": "string", "description": "Root directory"},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="explain",
                description=(
                    "THE EVERYTHING TOOL. Full explanation of a symbol. "
                    "Chain: know → impact → discover similar → LLM synthesis. "
                    "Returns a natural language explanation: what it is, how it works, "
                    "what depends on it, what's similar, what to be careful about. "
                    "Use when you need to deeply understand something before a major change."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Symbol to explain"},
                        "root": {"type": "string", "description": "Root directory"},
                    },
                    "required": ["name"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:

        if name == "know":
            from .chains import know as chain_know
            result = chain_know(arguments["name"], root=arguments.get("root", "."))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "impact":
            from .chains import impact as chain_impact
            result = chain_impact(arguments["name"], root=arguments.get("root", "."))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "discover":
            from .chains import discover as chain_discover
            result = chain_discover(arguments["query"], root=arguments.get("root", "."))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "explain":
            from .chains import explain as chain_explain
            result = chain_explain(arguments["name"], root=arguments.get("root", "."))
            return [TextContent(type="text", text=result)]

        elif name == "read_symbol":
            result = rag.read_symbol(arguments["name"])
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "read_similar":
            result = rag.read_similar(arguments["query"], limit=arguments.get("limit", 5))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "read_module":
            result = rag.read_module(arguments["path"])
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "read_flow":
            result = rag.read_flow(arguments["entry"], depth=arguments.get("depth", 5))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "read_depends":
            from .grapher import CodeGrapher
            grapher = CodeGrapher()
            result = grapher.get_dependents(arguments["name"])
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "read_recent":
            # TODO: integrate with git log for temporal awareness
            return [TextContent(type="text", text="read_recent not yet wired to git")]

        elif name == "read_grep":
            from .search import rg_search, format_hits_for_llm
            hits = rg_search(
                pattern=arguments["pattern"],
                paths=arguments["paths"],
                context=arguments.get("context", 3),
                glob=arguments.get("glob"),
                file_type=arguments.get("file_type"),
                max_results=arguments.get("max_results", 20),
            )
            formatted = format_hits_for_llm(hits)
            return [TextContent(type="text", text=formatted or "No matches found.")]

        elif name == "read_grep_files":
            from .search import rg_files
            files = rg_files(
                pattern=arguments["pattern"],
                paths=arguments["paths"],
                glob=arguments.get("glob"),
                file_type=arguments.get("file_type"),
            )
            return [TextContent(type="text", text="\n".join(files) if files else "No matching files.")]

        elif name == "tldr":
            result = rag.tldr(arguments["path"])
            return [TextContent(type="text", text=result)]

        elif name == "suggest_goals":
            result = rag.suggest_goals(arguments["path"])
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "best_question":
            result = rag.best_question(arguments["goal"], path=arguments.get("path"))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "goal_flow":
            # Full chain: Code → Goals → Best Question → Answer
            # Step 1: Get goals
            goals_result = rag.suggest_goals(arguments["path"])
            suggested = goals_result["suggested_goals"]

            # Step 2: Extract the first/highest-impact goal and formulate question
            flow_result = rag.best_question(
                goal=f"Based on this analysis, the highest priority: {suggested[:500]}",
                path=arguments.get("path"),
            )

            result = {
                "path": arguments["path"],
                "analysis": goals_result["analysis"],
                "suggested_goals": suggested,
                "best_question": flow_result["best_question"],
                "answer": flow_result["answer"],
                "relevant_files": flow_result["relevant_files"],
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    # Run as stdio MCP server (Claude Code connects via stdin/stdout)
    import asyncio
    from mcp.server.stdio import stdio_server

    async def run():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream)

    asyncio.run(run())
