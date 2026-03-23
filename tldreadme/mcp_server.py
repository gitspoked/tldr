"""MCP Server with stdio and SSE transports for codebase-aware queries."""

import asyncio
import json
import time
from urllib.parse import parse_qs, quote, unquote, urlparse

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server import Server
from mcp.types import EmbeddedResource, GetPromptResult, Prompt, PromptArgument, PromptMessage, Resource, ResourceTemplate, TextContent, TextResourceContents, Tool

from .lazy import load_module

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8900
DEFAULT_SSE_PATH = "/sse"
DEFAULT_MESSAGES_PATH = "/messages/"
DEFAULT_TOOL_PROFILE = "router"
CAPABILITY_CACHE_TTL_SECONDS = 5.0


def _rag():
    """Load the RAG module only when needed by a request."""

    return load_module("tldreadme.rag")


def _lsp():
    """Load the LSP module only when needed by a request."""

    return load_module("tldreadme.lsp")


def _workboard():
    """Load the workboard module only when needed by a request."""

    return load_module("tldreadme.workboard")


def _runtime():
    """Load the runtime diagnostics module only when needed by a request."""

    return load_module("tldreadme.runtime")


def _coding_tools():
    """Load the router-friendly coding tools only when needed."""

    return load_module("tldreadme.coding_tools")


def _children():
    """Load child-project detection only when needed."""

    return load_module("tldreadme.children")


def _tool_meta(
    *,
    category: str,
    priority: str,
    read_only: bool,
    latency: str,
    backends: list[str],
    profiles: list[str],
    fallback_to: list[str] | None = None,
) -> dict:
    """Return explicit router-friendly metadata for a tool."""

    return {
        "category": category,
        "priority": priority,
        "read_only": read_only,
        "latency": latency,
        "backends": backends,
        "profiles": profiles,
        "fallback_to": fallback_to or [],
    }


ROUTER_TOOL_NAMES = [
    "repo_next_action",
    "repo_lookup",
    "change_plan",
    "verify_change",
]

TOOL_METADATA = {
    "repo_next_action": _tool_meta(category="coordination", priority="preferred", read_only=True, latency="fast", backends=["workboard", "children", "summary"], profiles=["router", "full"], fallback_to=["repo_lookup", "change_plan"]),
    "repo_lookup": _tool_meta(category="lookup", priority="preferred", read_only=True, latency="fast", backends=["filesystem", "rg", "asts", "hot_index", "graph", "lsp", "workboard", "children", "summary"], profiles=["router", "full"], fallback_to=["repo_next_action"]),
    "read_symbol": _tool_meta(category="read", priority="advanced", read_only=True, latency="fast", backends=["graph", "hot_index"], profiles=["full"], fallback_to=["know"]),
    "read_similar": _tool_meta(category="read", priority="advanced", read_only=True, latency="medium", backends=["vector"], profiles=["full"], fallback_to=["pattern_search"]),
    "read_module": _tool_meta(category="read", priority="advanced", read_only=True, latency="fast", backends=["graph"], profiles=["full"], fallback_to=["scan_context"]),
    "read_flow": _tool_meta(category="read", priority="advanced", read_only=True, latency="medium", backends=["graph"], profiles=["full"], fallback_to=["impact"]),
    "read_depends": _tool_meta(category="read", priority="advanced", read_only=True, latency="fast", backends=["graph"], profiles=["full"], fallback_to=["impact"]),
    "read_recent": _tool_meta(category="read", priority="advanced", read_only=True, latency="fast", backends=["git", "summary"], profiles=["full"], fallback_to=["scan_context"]),
    "read_grep": _tool_meta(category="search", priority="advanced", read_only=True, latency="fast", backends=["rg"], profiles=["full"], fallback_to=["search_context"]),
    "read_grep_files": _tool_meta(category="search", priority="advanced", read_only=True, latency="fast", backends=["rg"], profiles=["full"], fallback_to=["search_context"]),
    "read_semantic": _tool_meta(category="semantic", priority="advanced", read_only=True, latency="medium", backends=["lsp"], profiles=["full"], fallback_to=["edit_context"]),
    "read_workspace_symbols": _tool_meta(category="semantic", priority="advanced", read_only=True, latency="medium", backends=["lsp"], profiles=["full"], fallback_to=["search_context"]),
    "scan_context": _tool_meta(category="orientation", priority="specialist", read_only=True, latency="fast", backends=["filesystem", "workboard", "summary", "children"], profiles=["full"]),
    "search_context": _tool_meta(category="orientation", priority="specialist", read_only=True, latency="fast", backends=["rg", "docs", "workboard", "children", "summary"], profiles=["full"], fallback_to=["scan_context"]),
    "edit_context": _tool_meta(category="coding", priority="specialist", read_only=True, latency="medium", backends=["asts", "lsp", "workboard"], profiles=["full"], fallback_to=["know", "search_context"]),
    "change_plan": _tool_meta(category="planning", priority="preferred", read_only=True, latency="medium", backends=["rg", "workboard", "tests"], profiles=["router", "full"], fallback_to=["repo_lookup"]),
    "test_map": _tool_meta(category="verification", priority="specialist", read_only=True, latency="fast", backends=["filesystem", "rg"], profiles=["full"], fallback_to=["change_plan"]),
    "verify_change": _tool_meta(category="verification", priority="preferred", read_only=False, latency="medium", backends=["workboard", "tests", "subprocess"], profiles=["router", "full"], fallback_to=["repo_lookup", "change_plan"]),
    "pattern_search": _tool_meta(category="coding", priority="specialist", read_only=True, latency="medium", backends=["rg", "vector", "semantic"], profiles=["full"], fallback_to=["search_context"]),
    "diagnostics_here": _tool_meta(category="semantic", priority="specialist", read_only=True, latency="medium", backends=["lsp"], profiles=["full"], fallback_to=["edit_context"]),
    "plan_list": _tool_meta(category="workboard", priority="advanced", read_only=True, latency="fast", backends=["workboard"], profiles=["full"], fallback_to=["plan_current"]),
    "plan_current": _tool_meta(category="workboard", priority="specialist", read_only=True, latency="fast", backends=["workboard"], profiles=["full"], fallback_to=["repo_next_action"]),
    "plan_create": _tool_meta(category="workboard", priority="advanced", read_only=False, latency="fast", backends=["workboard"], profiles=["full"], fallback_to=[]),
    "plan_update": _tool_meta(category="workboard", priority="advanced", read_only=False, latency="fast", backends=["workboard"], profiles=["full"], fallback_to=[]),
    "plan_archive": _tool_meta(category="workboard", priority="advanced", read_only=False, latency="fast", backends=["workboard"], profiles=["full"], fallback_to=[]),
    "task_add": _tool_meta(category="workboard", priority="advanced", read_only=False, latency="fast", backends=["workboard"], profiles=["full"], fallback_to=[]),
    "task_update": _tool_meta(category="workboard", priority="advanced", read_only=False, latency="fast", backends=["workboard"], profiles=["full"], fallback_to=[]),
    "task_complete": _tool_meta(category="workboard", priority="advanced", read_only=False, latency="fast", backends=["workboard"], profiles=["full"], fallback_to=[]),
    "session_note": _tool_meta(category="workboard", priority="advanced", read_only=False, latency="fast", backends=["workboard"], profiles=["full"], fallback_to=["session_update"]),
    "session_update": _tool_meta(category="workboard", priority="specialist", read_only=False, latency="fast", backends=["workboard"], profiles=["full"], fallback_to=[]),
    "know": _tool_meta(category="symbol", priority="specialist", read_only=True, latency="fast", backends=["hot_index", "rg", "graph"], profiles=["full"], fallback_to=["search_context"]),
    "impact": _tool_meta(category="symbol", priority="specialist", read_only=True, latency="fast", backends=["rg", "graph"], profiles=["full"], fallback_to=["know"]),
    "discover": _tool_meta(category="discovery", priority="advanced", read_only=True, latency="medium", backends=["rg", "vector"], profiles=["full"], fallback_to=["search_context"]),
    "explain": _tool_meta(category="synthesis", priority="advanced", read_only=True, latency="slow", backends=["llm", "graph", "vector"], profiles=["full"], fallback_to=["know", "impact"]),
    "tldr": _tool_meta(category="generation", priority="advanced", read_only=True, latency="slow", backends=["llm", "graph", "vector"], profiles=["full"], fallback_to=[]),
    "suggest_goals": _tool_meta(category="generation", priority="advanced", read_only=True, latency="slow", backends=["llm", "graph"], profiles=["full"], fallback_to=[]),
    "best_question": _tool_meta(category="generation", priority="advanced", read_only=True, latency="slow", backends=["llm", "graph"], profiles=["full"], fallback_to=[]),
    "goal_flow": _tool_meta(category="generation", priority="advanced", read_only=True, latency="slow", backends=["llm", "graph"], profiles=["full"], fallback_to=[]),
    "auto_iterate": _tool_meta(category="generation", priority="advanced", read_only=True, latency="slow", backends=["llm", "graph"], profiles=["full"], fallback_to=[]),
}

TOOL_REQUIRED_BACKENDS = {
    "read_symbol": ["vector", "graph"],
    "read_similar": ["vector"],
    "read_module": ["graph"],
    "read_flow": ["graph"],
    "read_depends": ["graph"],
    "read_recent": ["git"],
    "read_grep": ["rg"],
    "read_grep_files": ["rg"],
    "read_semantic": ["lsp"],
    "read_workspace_symbols": ["lsp"],
    "search_context": ["rg"],
    "diagnostics_here": ["lsp"],
    "tldr": ["graph", "llm"],
    "suggest_goals": ["graph", "vector", "llm"],
    "best_question": ["graph", "vector", "llm"],
    "goal_flow": ["graph", "vector", "llm"],
    "auto_iterate": ["graph", "vector", "llm"],
}

_CAPABILITY_CACHE: dict[str, object] = {"expires_at": 0.0, "value": None}


def _runtime_capabilities(force_refresh: bool = False) -> dict[str, object]:
    """Return a short-lived cached capability snapshot."""

    now = time.monotonic()
    if not force_refresh and _CAPABILITY_CACHE["value"] and now < float(_CAPABILITY_CACHE["expires_at"]):
        return dict(_CAPABILITY_CACHE["value"])

    runtime_module = _runtime()
    if hasattr(runtime_module, "capability_report"):
        capabilities = runtime_module.capability_report()
    else:
        capabilities = {
            "report_ok": bool(runtime_module.runtime_report().get("ok")),
            "backends": {},
        }
    _CAPABILITY_CACHE["value"] = capabilities
    _CAPABILITY_CACHE["expires_at"] = now + CAPABILITY_CACHE_TTL_SECONDS
    return dict(capabilities)


def _missing_backends_for_tool(name: str, capabilities: dict[str, object]) -> list[str]:
    """Return any hard-required backends missing for a tool."""

    backend_state = capabilities.get("backends", {})
    required = TOOL_REQUIRED_BACKENDS.get(name, [])
    return [backend for backend in required if not backend_state.get(backend, False)]


def _tool_names_for_profile(tool_profile: str = DEFAULT_TOOL_PROFILE, capabilities: dict[str, object] | None = None) -> list[str]:
    """Return exposed tool names for the requested profile."""

    capabilities = capabilities or _runtime_capabilities()
    if tool_profile == "full":
        names = list(TOOL_METADATA)
    else:
        names = [name for name in ROUTER_TOOL_NAMES if name in TOOL_METADATA]
    return [name for name in names if not _missing_backends_for_tool(name, capabilities)]


def _routing_signals() -> dict[str, object]:
    """Return low-cost repository state signals used for tool ordering."""

    signals = {
        "has_current_plan": False,
        "has_current_task": False,
        "has_next_action": False,
        "has_overlaps": False,
        "unknown_children": 0,
    }

    try:
        current = _workboard().current_plan()
    except Exception:
        current = {}

    session = current.get("session") or {}
    signals["has_current_plan"] = bool(current.get("plan"))
    signals["has_current_task"] = bool(session.get("current_task_id"))
    signals["has_next_action"] = bool(session.get("next_action") or session.get("current_focus"))
    signals["has_overlaps"] = bool(current.get("overlaps"))

    try:
        children = _children().list_children(include_ignored=True)
    except Exception:
        children = {}
    signals["unknown_children"] = sum(1 for child in children.get("children", []) if child.get("status") == "unknown")

    return signals


def _recommended_sequence(
    tool_profile: str = DEFAULT_TOOL_PROFILE,
    capabilities: dict[str, object] | None = None,
    routing_signals: dict[str, object] | None = None,
) -> list[str]:
    """Return the preferred tool order for the current repo state."""

    capabilities = capabilities or _runtime_capabilities()
    routing_signals = routing_signals or _routing_signals()
    exposed = _tool_names_for_profile(tool_profile, capabilities=capabilities)

    if routing_signals.get("has_overlaps"):
        preferred = ["repo_next_action", "repo_lookup", "verify_change", "change_plan"]
    elif int(routing_signals.get("unknown_children", 0)) > 0:
        preferred = ["repo_next_action", "repo_lookup", "change_plan", "verify_change"]
    elif routing_signals.get("has_current_task"):
        preferred = ["repo_next_action", "verify_change", "change_plan", "repo_lookup"]
    elif routing_signals.get("has_next_action"):
        preferred = ["repo_next_action", "change_plan", "repo_lookup", "verify_change"]
    elif routing_signals.get("has_current_plan"):
        preferred = ["repo_next_action", "change_plan", "repo_lookup", "verify_change"]
    else:
        preferred = ["repo_lookup", "repo_next_action", "change_plan", "verify_change"]

    ordered: list[str] = []
    for name in preferred + exposed:
        if name in exposed and name not in ordered:
            ordered.append(name)
    return ordered


def _ordered_tool_names_for_profile(
    tool_profile: str = DEFAULT_TOOL_PROFILE,
    capabilities: dict[str, object] | None = None,
    routing_signals: dict[str, object] | None = None,
) -> list[str]:
    """Return exposed tool names in recommendation order."""

    return _recommended_sequence(tool_profile, capabilities=capabilities, routing_signals=routing_signals)


def _tool_is_exposed(name: str, tool_profile: str = DEFAULT_TOOL_PROFILE, capabilities: dict[str, object] | None = None) -> bool:
    """Return whether a tool is exposed for the current profile."""

    return name in set(_tool_names_for_profile(tool_profile, capabilities=capabilities))


def _filter_tools_for_profile(
    tools: list[Tool],
    tool_profile: str = DEFAULT_TOOL_PROFILE,
    capabilities: dict[str, object] | None = None,
    routing_signals: dict[str, object] | None = None,
) -> list[Tool]:
    """Filter MCP tools down to the requested exposure profile."""

    capabilities = capabilities or _runtime_capabilities()
    ordered_names = _ordered_tool_names_for_profile(tool_profile, capabilities=capabilities, routing_signals=routing_signals)
    allowed = set(ordered_names)
    ordered = [tool for tool in tools if tool.name in allowed]
    ordered.sort(key=lambda tool: ordered_names.index(tool.name))
    return ordered


def _tooling_payload(tool_profile: str = DEFAULT_TOOL_PROFILE, capabilities: dict[str, object] | None = None) -> dict:
    """Return explicit routing metadata and the current exposure split."""

    capabilities = capabilities or _runtime_capabilities()
    routing_signals = _routing_signals()
    exposed_names = _ordered_tool_names_for_profile(tool_profile, capabilities=capabilities, routing_signals=routing_signals)
    suppressed_names = [name for name in TOOL_METADATA if _missing_backends_for_tool(name, capabilities)]
    deferred_names = [name for name in TOOL_METADATA if name not in exposed_names and name not in suppressed_names]
    return {
        "active_profile": tool_profile,
        "capabilities": capabilities,
        "routing_signals": routing_signals,
        "recommended_sequence": _recommended_sequence(tool_profile, capabilities=capabilities, routing_signals=routing_signals),
        "exposed_tools": [
            {**TOOL_METADATA[name], "name": name, "required_backends": TOOL_REQUIRED_BACKENDS.get(name, [])}
            for name in exposed_names
        ],
        "deferred_tools": [
            {**TOOL_METADATA[name], "name": name, "required_backends": TOOL_REQUIRED_BACKENDS.get(name, [])}
            for name in deferred_names
        ],
        "suppressed_tools": [
            {
                **TOOL_METADATA[name],
                "name": name,
                "required_backends": TOOL_REQUIRED_BACKENDS.get(name, []),
                "missing_backends": _missing_backends_for_tool(name, capabilities),
            }
            for name in suppressed_names
        ],
        "profiles": {
            "router": {
                "description": "Smaller default surface for agent routers. Four intent-native tools: resume, lookup, plan, and verify.",
                "tool_count": len(_tool_names_for_profile("router", capabilities=capabilities)),
            },
            "full": {
                "description": "Full debugging and specialist surface, including lower-level lookups, workboard mutations, and generation helpers.",
                "tool_count": len(_tool_names_for_profile("full", capabilities=capabilities)),
            },
        },
    }


def _list_static_resources(tool_profile: str = DEFAULT_TOOL_PROFILE) -> list[Resource]:
    """Return static MCP resources exposed by the server."""

    return [
        Resource(
            name="overview",
            uri="repo://overview",
            description="Overview of the repository intelligence surface exposed by TLDREADME.",
            mimeType="application/json",
        ),
        Resource(
            name="health",
            uri="repo://health",
            description="Runtime dependency and service health report for the current TLDREADME environment.",
            mimeType="application/json",
        ),
        Resource(
            name="tooling",
            uri="repo://tooling",
            description=f"Explicit router metadata and the currently exposed MCP tool profile (`{tool_profile}`).",
            mimeType="application/json",
        ),
        Resource(
            name="children",
            uri="repo://children",
            description="Detected nested child subtrees and whether they are unknown, merged, or ignored.",
            mimeType="application/json",
        ),
        Resource(
            name="plans",
            uri="repo://plans",
            description="Tracked repository plans and phased task summaries.",
            mimeType="application/json",
        ),
        Resource(
            name="session-current",
            uri="repo://session/current",
            description="Canonical current session snapshot with active-plan, overlap, and note context.",
            mimeType="application/json",
        ),
    ]


def _list_resource_templates() -> list[ResourceTemplate]:
    """Return dynamic resource templates for symbol, module, and LSP lookups."""

    return [
        ResourceTemplate(
            name="module",
            uriTemplate="repo://module/{path}",
            description="Structured module or directory summary from the graph index.",
            mimeType="application/json",
        ),
        ResourceTemplate(
            name="symbol",
            uriTemplate="repo://symbol/{name}",
            description="Structured symbol summary including code, callers, callees, and dependents.",
            mimeType="application/json",
        ),
        ResourceTemplate(
            name="semantic",
            uriTemplate="repo://semantic/{path}?line={line}&column={column}&root={root}",
            description="Exact semantic information from an installed language server at a file position.",
            mimeType="application/json",
        ),
        ResourceTemplate(
            name="workspace-symbols",
            uriTemplate="repo://workspace-symbols/{query}?path={path}&root={root}&limit={limit}",
            description="Workspace symbol search through the language server for the file's language.",
            mimeType="application/json",
        ),
        ResourceTemplate(
            name="plan",
            uriTemplate="repo://plan/{id}",
            description="Full workboard plan with phases, tasks, and success criteria.",
            mimeType="application/json",
        ),
        ResourceTemplate(
            name="task",
            uriTemplate="repo://task/{plan_id}/{task_id}",
            description="Single workboard task with acceptance criteria and verification commands.",
            mimeType="application/json",
        ),
    ]


def _list_prompt_definitions() -> list[Prompt]:
    """Return reusable MCP prompts backed by repository resources."""

    return [
        Prompt(
            name="impact-review",
            description="Review the change impact for a symbol using indexed and semantic repo context.",
            arguments=[
                PromptArgument(name="symbol", description="Symbol name to review", required=True),
                PromptArgument(name="root", description="Optional repository root", required=False),
            ],
        ),
        Prompt(
            name="module-brief",
            description="Get a concise brief of a module or directory using the graph-backed module resource.",
            arguments=[
                PromptArgument(name="path", description="Module or directory path", required=True),
            ],
        ),
        Prompt(
            name="semantic-investigation",
            description="Investigate a specific file position with language-server semantics.",
            arguments=[
                PromptArgument(name="path", description="Source file path", required=True),
                PromptArgument(name="line", description="1-based line number", required=True),
                PromptArgument(name="column", description="1-based column number", required=False),
                PromptArgument(name="root", description="Optional workspace root", required=False),
            ],
        ),
        Prompt(
            name="resume-session",
            description="Resume the current repository plan using the local workboard session state.",
            arguments=[
                PromptArgument(name="plan_id", description="Optional explicit plan id", required=False),
            ],
        ),
        Prompt(
            name="phase-review",
            description="Review the next actions, risks, and success criteria for a specific phase.",
            arguments=[
                PromptArgument(name="plan_id", description="Plan id", required=True),
                PromptArgument(name="phase", description="Phase name", required=True),
            ],
        ),
        Prompt(
            name="done-check",
            description="Verify whether a specific task is actually done according to its acceptance criteria.",
            arguments=[
                PromptArgument(name="plan_id", description="Plan id", required=True),
                PromptArgument(name="task_id", description="Task id", required=True),
            ],
        ),
    ]


def _read_resource_text(uri: str, tool_profile: str = DEFAULT_TOOL_PROFILE, capabilities: dict[str, object] | None = None) -> str:
    """Resolve a repo:// resource URI into JSON text."""

    capabilities = capabilities or _runtime_capabilities()
    parsed = urlparse(uri)
    if parsed.scheme != "repo":
        raise ValueError(f"Unsupported resource URI: {uri}")

    target = parsed.netloc
    path_value = unquote(parsed.path.lstrip("/"))
    query = parse_qs(parsed.query)

    if target == "overview":
        payload = {
            "server": "tldreadme",
            "active_tool_profile": tool_profile,
            "capabilities": capabilities,
            "resources": [resource.uri for resource in _list_static_resources(tool_profile)],
            "resource_templates": [template.uriTemplate for template in _list_resource_templates()],
            "prompts": [prompt.name for prompt in _list_prompt_definitions()],
            "exposed_tools": _tool_names_for_profile(tool_profile, capabilities=capabilities),
            "notes": "Use `repo_next_action` to resume, `repo_lookup` to orient or investigate, `change_plan` to prepare an edit, and `verify_change` to close it out. `repo://tooling` explains the live router profile.",
        }
        return json.dumps(payload, indent=2)

    if target == "health":
        return json.dumps(_runtime().runtime_report(), indent=2)

    if target == "tooling":
        return json.dumps(_tooling_payload(tool_profile, capabilities=capabilities), indent=2)

    if target == "children":
        return json.dumps(_children().list_children(include_ignored=True), indent=2)

    if target == "plans":
        return json.dumps(_workboard().list_plans(), indent=2)

    if target == "session" and path_value == "current":
        return json.dumps(_workboard().current_plan(), indent=2)

    if target == "module":
        return json.dumps(_rag().read_module(path_value), indent=2, default=str)

    if target == "symbol":
        return json.dumps(_rag().read_symbol(path_value), indent=2, default=str)

    if target == "semantic":
        line = int(query["line"][0])
        column = int(query["column"][0]) if query.get("column", [""])[0] else None
        root = query.get("root", [None])[0]
        include_references = query.get("include_references", ["true"])[0].lower() != "false"
        return json.dumps(
            _lsp().semantic_inspect(path_value, line, column, root=root, include_references=include_references),
            indent=2,
            default=str,
        )

    if target == "workspace-symbols":
        if not query.get("path"):
            raise ValueError("workspace-symbols resources require a `path` query parameter.")
        root = query.get("root", [None])[0]
        limit = int(query.get("limit", ["20"])[0])
        return json.dumps(
            _lsp().workspace_symbols(query["path"][0], path_value, root=root, limit=limit),
            indent=2,
            default=str,
        )

    if target == "plan":
        return json.dumps(_workboard().get_plan(path_value), indent=2, default=str)

    if target == "task":
        parts = [part for part in path_value.split("/") if part]
        if len(parts) != 2:
            raise ValueError("task resources require `repo://task/{plan_id}/{task_id}`.")
        return json.dumps(_workboard().get_task(parts[0], parts[1]), indent=2, default=str)

    raise ValueError(f"Unknown repo resource target: {target}")


def _embedded_resource_message(uri: str, text: str) -> PromptMessage:
    """Wrap resource text as an embedded prompt message."""

    return PromptMessage(
        role="user",
        content=EmbeddedResource(
            type="resource",
            resource=TextResourceContents(uri=uri, mimeType="application/json", text=text),
        ),
    )


def _build_prompt(name: str, arguments: dict[str, str] | None, tool_profile: str = DEFAULT_TOOL_PROFILE) -> GetPromptResult:
    """Build a prompt result backed by repository resources."""

    arguments = arguments or {}

    if name == "impact-review":
        symbol = arguments["symbol"]
        resource_uri = f"repo://symbol/{quote(symbol, safe='')}"
        resource_text = _read_resource_text(resource_uri, tool_profile=tool_profile)
        return GetPromptResult(
            description=f"Impact review for symbol `{symbol}`.",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            "Review the impact of changing this symbol. Focus on direct callers, "
                            "transitive dependents, likely breakage radius, and safe rollout strategy."
                        ),
                    ),
                ),
                _embedded_resource_message(resource_uri, resource_text),
            ],
        )

    if name == "module-brief":
        path_value = arguments["path"]
        resource_uri = f"repo://module/{quote(path_value, safe='/')}"
        resource_text = _read_resource_text(resource_uri, tool_profile=tool_profile)
        return GetPromptResult(
            description=f"Module brief for `{path_value}`.",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text="Summarize this module: architecture, key symbols, entry points, and likely change hotspots.",
                    ),
                ),
                _embedded_resource_message(resource_uri, resource_text),
            ],
        )

    if name == "semantic-investigation":
        path_value = arguments["path"]
        line = arguments["line"]
        column = arguments.get("column")
        root = arguments.get("root")
        resource_uri = f"repo://semantic/{quote(path_value, safe='/')}?line={line}"
        if column:
            resource_uri += f"&column={column}"
        if root:
            resource_uri += f"&root={quote(root, safe='/')}"
        resource_text = _read_resource_text(resource_uri, tool_profile=tool_profile)
        return GetPromptResult(
            description=f"Semantic investigation for `{path_value}:{line}`.",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            "Use the attached semantic context to explain the symbol at this position, "
                            "its resolved definition, references, and how it fits into the surrounding file."
                        ),
                    ),
                ),
                _embedded_resource_message(resource_uri, resource_text),
            ],
        )

    if name == "resume-session":
        current = _workboard().current_plan()
        plan_id = arguments.get("plan_id") or current["session"].get("current_plan_id")
        messages = [
            PromptMessage(
                role="user",
                content=TextContent(
                    type="text",
                    text=(
                        "Resume this repository session. Identify the active phase, unfinished tasks, "
                        "blockers, verification steps, and the single best next action."
                    ),
                ),
            ),
            _embedded_resource_message("repo://session/current", json.dumps(current, indent=2)),
        ]
        if plan_id:
            plan_uri = f"repo://plan/{quote(plan_id, safe='')}"
            messages.append(_embedded_resource_message(plan_uri, _read_resource_text(plan_uri, tool_profile=tool_profile)))
        return GetPromptResult(
            description="Resume the current workboard session.",
            messages=messages,
        )

    if name == "phase-review":
        plan_id = arguments["plan_id"]
        phase = arguments["phase"]
        resource_uri = f"repo://plan/{quote(plan_id, safe='')}"
        return GetPromptResult(
            description=f"Phase review for `{plan_id}` / `{phase}`.",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            f"Review the `{phase}` phase only. Summarize unfinished tasks, blockers, "
                            "acceptance criteria, and the recommended execution order."
                        ),
                    ),
                ),
                _embedded_resource_message(resource_uri, _read_resource_text(resource_uri, tool_profile=tool_profile)),
            ],
        )

    if name == "done-check":
        plan_id = arguments["plan_id"]
        task_id = arguments["task_id"]
        task_uri = f"repo://task/{quote(plan_id, safe='')}/{quote(task_id, safe='')}"
        return GetPromptResult(
            description=f"Done-check for task `{task_id}`.",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(
                        type="text",
                        text=(
                            "Assess whether this task is actually complete. Compare the evidence against the "
                            "acceptance criteria and verification commands, then call out any gaps."
                        ),
                    ),
                ),
                _embedded_resource_message(task_uri, _read_resource_text(task_uri, tool_profile=tool_profile)),
            ],
        )

    raise ValueError(f"Unknown prompt: {name}")


def _build_server(tool_profile: str = DEFAULT_TOOL_PROFILE) -> Server:
    """Create the MCP server and register all tools."""

    server = Server("tldreadme")

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return _list_static_resources(tool_profile)

    @server.list_resource_templates()
    async def list_resource_templates() -> list[ResourceTemplate]:
        return _list_resource_templates()

    @server.read_resource()
    async def read_resource(uri) -> list[ReadResourceContents]:
        capabilities = _runtime_capabilities()
        return [ReadResourceContents(_read_resource_text(str(uri), tool_profile=tool_profile, capabilities=capabilities), mime_type="application/json")]

    @server.list_prompts()
    async def list_prompts() -> list[Prompt]:
        return _list_prompt_definitions()

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        return _build_prompt(name, arguments, tool_profile=tool_profile)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        capabilities = _runtime_capabilities()
        tools = [
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
                        "query": {
                            "type": "string",
                            "description": "What kind of code to find (e.g. 'error handling', 'HTTP handler', 'ICE agent')",
                        },
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
                name="auto_iterate",
                description=(
                    "Multi-step backwards flow. Repeats code analysis → best question → "
                    "answer for a few rounds to surface follow-up work."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Module/directory to analyze"},
                        "goal": {"type": "string", "description": "Optional starting goal"},
                        "rounds": {"type": "integer", "description": "Iteration count", "default": 2},
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
            Tool(
                name="read_semantic",
                description=(
                    "Query the installed language server at an exact file position. "
                    "Returns hover text, resolved definitions, semantic references, and document symbols. "
                    "Use this when syntax-level parsing is not enough and you need real symbol resolution."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Source file path"},
                        "line": {"type": "integer", "description": "1-based line number"},
                        "column": {"type": "integer", "description": "1-based column number"},
                        "root": {"type": "string", "description": "Workspace root (optional)"},
                        "include_references": {
                            "type": "boolean",
                            "description": "Whether to include semantic references",
                            "default": True,
                        },
                    },
                    "required": ["path", "line"],
                },
            ),
            Tool(
                name="read_workspace_symbols",
                description=(
                    "Search workspace symbols through the installed language server for a file's language. "
                    "Useful for semantic symbol discovery beyond text search."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Representative source file path for the language server"},
                        "query": {"type": "string", "description": "Workspace symbol query"},
                        "root": {"type": "string", "description": "Workspace root (optional)"},
                        "limit": {"type": "integer", "description": "Max symbols to return", "default": 20},
                    },
                    "required": ["path", "query"],
                },
            ),
            Tool(
                name="repo_next_action",
                description=(
                    "ROUTER-PREFERRED. Recommend the best next top-level tool from current repo state, active sessions, overlaps, and imported child subtrees."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "root": {"type": "string", "description": "Repository root (optional)"},
                    },
                },
            ),
            Tool(
                name="repo_lookup",
                description=(
                    "ROUTER-PREFERRED. Single read/lookup entry point. Internally dispatches to overview, federated search, symbol knowledge, impact, or edit-time context based on the inputs you provide."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Optional free-form question or lookup query"},
                        "path": {"type": "string", "description": "Optional file or directory path"},
                        "line": {"type": "integer", "description": "Optional 1-based line number for exact edit context"},
                        "column": {"type": "integer", "description": "Optional 1-based column number"},
                        "symbol": {"type": "string", "description": "Optional symbol name"},
                        "root": {"type": "string", "description": "Repository root (optional)"},
                        "scope": {"type": "string", "description": "Optional lookup scope"},
                        "source_types": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["code", "docs", "workboard", "children", "recent"]},
                            "description": "Optional source filters when the lookup dispatches to federated search",
                        },
                        "limit": {"type": "integer", "description": "Max ranked hits or examples", "default": 10},
                    },
                },
            ),
            Tool(
                name="scan_context",
                description=(
                    "SPECIALIST. Snapshot the available repo context surfaces: code, tests, docs, generated TLDR files, workboard, child subtrees, and recent changes."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "root": {"type": "string", "description": "Repository root (optional)"},
                        "scope": {"type": "string", "description": "Optional file or directory scope"},
                        "limit": {"type": "integer", "description": "Max examples per surface", "default": 10},
                    },
                },
            ),
            Tool(
                name="search_context",
                description=(
                    "SPECIALIST. Federated context search across code, docs, workboard, child subtrees, generated TLDR files, and recent changes."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What context to find"},
                        "root": {"type": "string", "description": "Repository root (optional)"},
                        "scope": {"type": "string", "description": "Optional file or directory scope"},
                        "source_types": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["code", "docs", "workboard", "children", "recent"]},
                            "description": "Optional source filters",
                        },
                        "limit": {"type": "integer", "description": "Max ranked hits", "default": 10},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="edit_context",
                description=(
                    "SPECIALIST. Code-time context for the exact place you want to edit. "
                    "Combines snippet, enclosing symbol, semantic info, similar code, tests, and matching workboard tasks."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Source file path"},
                        "line": {"type": "integer", "description": "1-based line number"},
                        "column": {"type": "integer", "description": "1-based column number"},
                        "root": {"type": "string", "description": "Repository root (optional)"},
                    },
                    "required": ["path", "line"],
                },
            ),
            Tool(
                name="change_plan",
                description=(
                    "ROUTER-PREFERRED. Turn a coding goal into candidate files, risks, steps, acceptance criteria, and verification commands."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string", "description": "What you want to change or achieve"},
                        "path": {"type": "string", "description": "Optional source file path"},
                        "symbol": {"type": "string", "description": "Optional symbol name"},
                        "root": {"type": "string", "description": "Repository root (optional)"},
                    },
                    "required": ["goal"],
                },
            ),
            Tool(
                name="test_map",
                description=(
                    "SPECIALIST. Map a source file or symbol to likely tests and exact verification commands."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Optional source file path"},
                        "symbol": {"type": "string", "description": "Optional symbol name"},
                        "root": {"type": "string", "description": "Repository root (optional)"},
                    },
                },
            ),
            Tool(
                name="verify_change",
                description=(
                    "ROUTER-PREFERRED. Verify a change against tests, workboard evidence, and acceptance criteria."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "files": {"type": "array", "items": {"type": "string"}, "description": "Changed file paths"},
                        "symbol": {"type": "string", "description": "Optional symbol name"},
                        "task_id": {"type": "string", "description": "Optional workboard task id"},
                        "plan_id": {"type": "string", "description": "Optional workboard plan id when task ids are ambiguous"},
                        "root": {"type": "string", "description": "Repository root (optional)"},
                        "run_commands": {
                            "type": "boolean",
                            "description": "Whether to execute the inferred verification commands",
                            "default": False,
                        },
                        "max_commands": {"type": "integer", "description": "Cap executed commands", "default": 3},
                    },
                },
            ),
            Tool(
                name="pattern_search",
                description=(
                    "SPECIALIST. Search for reusable implementation patterns before writing new code."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Pattern or behavior to find"},
                        "path": {"type": "string", "description": "Optional source file path"},
                        "symbol": {"type": "string", "description": "Optional symbol name"},
                        "root": {"type": "string", "description": "Repository root (optional)"},
                        "limit": {"type": "integer", "description": "Max reusable snippets", "default": 5},
                    },
                },
            ),
            Tool(
                name="diagnostics_here",
                description=(
                    "SPECIALIST. Return LSP diagnostics for a file or exact position, including likely fix area and impacted symbols."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Source file path"},
                        "line": {"type": "integer", "description": "Optional 1-based line number"},
                        "column": {"type": "integer", "description": "Optional 1-based column number"},
                        "root": {"type": "string", "description": "Repository root (optional)"},
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="plan_list",
                description="List tracked repository plans from the workboard.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "blocked", "done", "archived"],
                            "description": "Optional status filter",
                        },
                    },
                },
            ),
            Tool(
                name="plan_current",
                description="Get the current session snapshot, current plan, and any active overlap warnings.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Optional explicit session id"},
                        "actor_id": {"type": "string", "description": "Optional actor selector"},
                    },
                },
            ),
            Tool(
                name="plan_create",
                description=(
                    "Create a file-backed repository plan with phases, success criteria, and risks. "
                    "Stored under .tldr/work/plans/."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Plan title"},
                        "goal": {"type": "string", "description": "Desired outcome"},
                        "scope": {"type": "array", "items": {"type": "string"}},
                        "owner": {"type": "string", "description": "Optional owner"},
                        "phases": {"type": "array", "items": {"type": "string"}},
                        "success_criteria": {"type": "array", "items": {"type": "string"}},
                        "risks": {"type": "array", "items": {"type": "string"}},
                        "notes": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "goal"],
                },
            ),
            Tool(
                name="plan_update",
                description="Update top-level plan metadata such as status, owner, notes, and success criteria.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string"},
                        "title": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "blocked", "done", "archived"]},
                        "owner": {"type": "string"},
                        "goal": {"type": "string"},
                        "add_scope": {"type": "array", "items": {"type": "string"}},
                        "add_success_criteria": {"type": "array", "items": {"type": "string"}},
                        "add_risks": {"type": "array", "items": {"type": "string"}},
                        "add_notes": {"type": "array", "items": {"type": "string"}},
                        "current_phase": {"type": "string"},
                    },
                    "required": ["plan_id"],
                },
            ),
            Tool(
                name="plan_archive",
                description="Archive a plan without deleting its history.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string"},
                    },
                    "required": ["plan_id"],
                },
            ),
            Tool(
                name="task_add",
                description="Add a task to a plan phase with acceptance criteria and verification commands.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string"},
                        "title": {"type": "string"},
                        "phase": {"type": "string", "default": "Backlog"},
                        "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"], "default": "medium"},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "files": {"type": "array", "items": {"type": "string"}},
                        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                        "verification_commands": {"type": "array", "items": {"type": "string"}},
                        "notes": {"type": "array", "items": {"type": "string"}},
                        "next_step": {"type": "string"},
                    },
                    "required": ["plan_id", "title"],
                },
            ),
            Tool(
                name="task_update",
                description="Update task execution state, evidence, blockers, or verification details.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string"},
                        "task_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "blocked", "done"]},
                        "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "next_step": {"type": "string"},
                        "add_blockers": {"type": "array", "items": {"type": "string"}},
                        "add_evidence": {"type": "array", "items": {"type": "string"}},
                        "add_notes": {"type": "array", "items": {"type": "string"}},
                        "add_files": {"type": "array", "items": {"type": "string"}},
                        "add_acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                        "add_verification_commands": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["plan_id", "task_id"],
                },
            ),
            Tool(
                name="task_complete",
                description="Mark a task done and attach verification evidence.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string"},
                        "task_id": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "note": {"type": "string"},
                    },
                    "required": ["plan_id", "task_id"],
                },
            ),
            Tool(
                name="session_note",
                description="Append a short-term coordination note to the local current session.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "note": {"type": "string"},
                        "plan_id": {"type": "string"},
                        "phase": {"type": "string"},
                        "session_id": {"type": "string"},
                        "actor_id": {"type": "string"},
                    },
                    "required": ["note"],
                },
            ),
            Tool(
                name="session_update",
                description="Update the canonical resumable session snapshot for focus, claims, blockers, and next action.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "actor_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["active", "paused", "blocked", "done", "archived"]},
                        "goal": {"type": "string"},
                        "current_plan_id": {"type": "string"},
                        "current_task_id": {"type": "string"},
                        "current_phase": {"type": "string"},
                        "current_focus": {"type": "string"},
                        "next_action": {"type": "string"},
                        "claimed_files": {"type": "array", "items": {"type": "string"}},
                        "claimed_symbols": {"type": "array", "items": {"type": "string"}},
                        "verification_commands": {"type": "array", "items": {"type": "string"}},
                        "blockers": {"type": "array", "items": {"type": "string"}},
                        "recent_steps": {"type": "array", "items": {"type": "string"}},
                        "forked_from": {"type": "string"},
                    },
                },
            ),
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
        return _filter_tools_for_profile(tools, tool_profile, capabilities=capabilities)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
        arguments = arguments or {}
        capabilities = _runtime_capabilities()

        if not _tool_is_exposed(name, tool_profile, capabilities=capabilities):
            details = _tooling_payload(tool_profile, capabilities=capabilities)
            missing_backends = _missing_backends_for_tool(name, capabilities)
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "error": (
                                f"Tool `{name}` is not exposed in the `{tool_profile}` profile."
                                if not missing_backends
                                else f"Tool `{name}` is unavailable because these backends are missing: {', '.join(missing_backends)}."
                            ),
                            "active_profile": tool_profile,
                            "exposed_tools": [tool["name"] for tool in details["exposed_tools"]],
                            "deferred_tools": [tool["name"] for tool in details["deferred_tools"][:10]],
                            "suppressed_tools": [tool["name"] for tool in details.get("suppressed_tools", [])[:10]],
                            "hint": (
                                "Install or start the missing backend, or restart the server with `--tool-profile full` for the full specialist surface."
                                if missing_backends
                                else "Restart the server with `--tool-profile full` for the full specialist surface."
                            ),
                        },
                        indent=2,
                    ),
                )
            ]

        if name == "know":
            from .chains import know as chain_know

            result = chain_know(arguments["name"], root=arguments.get("root", "."))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "impact":
            from .chains import impact as chain_impact

            result = chain_impact(arguments["name"], root=arguments.get("root", "."))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "discover":
            from .chains import discover as chain_discover

            result = chain_discover(arguments["query"], root=arguments.get("root", "."))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "explain":
            from .chains import explain as chain_explain

            result = chain_explain(arguments["name"], root=arguments.get("root", "."))
            return [TextContent(type="text", text=result)]

        if name == "read_symbol":
            result = _rag().read_symbol(arguments["name"])
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "read_similar":
            result = _rag().read_similar(arguments["query"], limit=arguments.get("limit", 5))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "read_module":
            result = _rag().read_module(arguments["path"])
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "read_flow":
            result = _rag().read_flow(arguments["entry"], depth=arguments.get("depth", 5))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "read_depends":
            from ._shared import get_grapher

            result = get_grapher().get_dependents(arguments["name"])
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "read_recent":
            result = _rag().read_recent(scope=arguments.get("scope"), days=arguments.get("days", 7))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "read_grep":
            from .search import format_hits_for_llm, rg_search

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

        if name == "read_grep_files":
            from .search import rg_files

            files = rg_files(
                pattern=arguments["pattern"],
                paths=arguments["paths"],
                glob=arguments.get("glob"),
                file_type=arguments.get("file_type"),
            )
            return [TextContent(type="text", text="\n".join(files) if files else "No matching files.")]

        if name == "read_semantic":
            result = _lsp().semantic_inspect(
                arguments["path"],
                arguments["line"],
                arguments.get("column"),
                root=arguments.get("root"),
                include_references=arguments.get("include_references", True),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "read_workspace_symbols":
            result = _lsp().workspace_symbols(
                arguments["path"],
                arguments["query"],
                root=arguments.get("root"),
                limit=arguments.get("limit", 20),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "scan_context":
            result = _coding_tools().scan_context(
                root=arguments.get("root", "."),
                scope=arguments.get("scope"),
                limit=arguments.get("limit", 10),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "repo_next_action":
            result = _coding_tools().repo_next_action(root=arguments.get("root", "."))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "repo_lookup":
            result = _coding_tools().repo_lookup(
                query=arguments.get("query"),
                path=arguments.get("path"),
                line=arguments.get("line"),
                column=arguments.get("column"),
                symbol=arguments.get("symbol"),
                root=arguments.get("root", "."),
                scope=arguments.get("scope"),
                source_types=arguments.get("source_types"),
                limit=arguments.get("limit", 10),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "search_context":
            result = _coding_tools().search_context(
                arguments["query"],
                root=arguments.get("root", "."),
                scope=arguments.get("scope"),
                source_types=arguments.get("source_types"),
                limit=arguments.get("limit", 10),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "plan_list":
            result = _workboard().list_plans(status=arguments.get("status"))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "plan_current":
            result = _workboard().current_plan(
                session_id=arguments.get("session_id"),
                actor_id=arguments.get("actor_id"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "plan_create":
            result = _workboard().create_plan(
                arguments["title"],
                arguments["goal"],
                scope=arguments.get("scope"),
                owner=arguments.get("owner"),
                phases=arguments.get("phases"),
                success_criteria=arguments.get("success_criteria"),
                risks=arguments.get("risks"),
                notes=arguments.get("notes"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "plan_update":
            result = _workboard().update_plan(
                arguments["plan_id"],
                title=arguments.get("title"),
                status=arguments.get("status"),
                owner=arguments.get("owner"),
                goal=arguments.get("goal"),
                add_scope=arguments.get("add_scope"),
                add_success_criteria=arguments.get("add_success_criteria"),
                add_risks=arguments.get("add_risks"),
                add_notes=arguments.get("add_notes"),
                current_phase=arguments.get("current_phase"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "plan_archive":
            result = _workboard().archive_plan(arguments["plan_id"])
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "task_add":
            result = _workboard().add_task(
                arguments["plan_id"],
                arguments["title"],
                phase=arguments.get("phase", "Backlog"),
                priority=arguments.get("priority", "medium"),
                depends_on=arguments.get("depends_on"),
                files=arguments.get("files"),
                acceptance_criteria=arguments.get("acceptance_criteria"),
                verification_commands=arguments.get("verification_commands"),
                notes=arguments.get("notes"),
                next_step=arguments.get("next_step"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "task_update":
            result = _workboard().update_task(
                arguments["plan_id"],
                arguments["task_id"],
                status=arguments.get("status"),
                priority=arguments.get("priority"),
                next_step=arguments.get("next_step"),
                add_blockers=arguments.get("add_blockers"),
                add_evidence=arguments.get("add_evidence"),
                add_notes=arguments.get("add_notes"),
                add_files=arguments.get("add_files"),
                add_acceptance_criteria=arguments.get("add_acceptance_criteria"),
                add_verification_commands=arguments.get("add_verification_commands"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "task_complete":
            result = _workboard().complete_task(
                arguments["plan_id"],
                arguments["task_id"],
                evidence=arguments.get("evidence"),
                note=arguments.get("note"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "session_note":
            result = _workboard().add_session_note(
                arguments["note"],
                plan_id=arguments.get("plan_id"),
                phase=arguments.get("phase"),
                session_id=arguments.get("session_id"),
                actor_id=arguments.get("actor_id"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "session_update":
            result = _workboard().update_session(
                session_id=arguments.get("session_id"),
                actor_id=arguments.get("actor_id"),
                status=arguments.get("status"),
                goal=arguments.get("goal"),
                current_plan_id=arguments.get("current_plan_id"),
                current_task_id=arguments.get("current_task_id"),
                current_phase=arguments.get("current_phase"),
                current_focus=arguments.get("current_focus"),
                next_action=arguments.get("next_action"),
                claimed_files=arguments.get("claimed_files"),
                claimed_symbols=arguments.get("claimed_symbols"),
                verification_commands=arguments.get("verification_commands"),
                blockers=arguments.get("blockers"),
                recent_steps=arguments.get("recent_steps"),
                forked_from=arguments.get("forked_from"),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "edit_context":
            result = _coding_tools().edit_context(
                arguments["path"],
                arguments["line"],
                column=arguments.get("column"),
                root=arguments.get("root", "."),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "change_plan":
            result = _coding_tools().change_plan(
                arguments["goal"],
                path=arguments.get("path"),
                symbol=arguments.get("symbol"),
                root=arguments.get("root", "."),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "test_map":
            result = _coding_tools().test_map(
                path=arguments.get("path"),
                symbol=arguments.get("symbol"),
                root=arguments.get("root", "."),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "verify_change":
            result = _coding_tools().verify_change(
                files=arguments.get("files"),
                symbol=arguments.get("symbol"),
                task_id=arguments.get("task_id"),
                plan_id=arguments.get("plan_id"),
                root=arguments.get("root", "."),
                run_commands=arguments.get("run_commands", False),
                max_commands=arguments.get("max_commands", 3),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "pattern_search":
            result = _coding_tools().pattern_search(
                query=arguments.get("query"),
                path=arguments.get("path"),
                symbol=arguments.get("symbol"),
                root=arguments.get("root", "."),
                limit=arguments.get("limit", 5),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "diagnostics_here":
            result = _coding_tools().diagnostics_here(
                arguments["path"],
                line=arguments.get("line"),
                column=arguments.get("column"),
                root=arguments.get("root", "."),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "tldr":
            result = _rag().tldr(arguments["path"])
            return [TextContent(type="text", text=result)]

        if name == "suggest_goals":
            result = _rag().suggest_goals(arguments["path"])
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "best_question":
            result = _rag().best_question(arguments["goal"], path=arguments.get("path"))
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "goal_flow":
            goals_result = _rag().suggest_goals(arguments["path"])
            suggested = goals_result["suggested_goals"]
            flow_result = _rag().best_question(
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

        if name == "auto_iterate":
            result = _rag().auto_iterate(
                path=arguments["path"],
                goal=arguments.get("goal"),
                rounds=arguments.get("rounds", 2),
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def _run_stdio(server: Server) -> None:
    """Run the server using stdio transport."""

    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def _run_sse(
    server: Server,
    host: str,
    port: int,
    sse_path: str,
    message_path: str,
) -> None:
    """Run the server using SSE transport."""

    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.routing import Mount, Route

    sse_path = _normalize_http_path(sse_path)
    message_path = _normalize_http_path(message_path, trailing_slash=True)
    transport = SseServerTransport(message_path)

    async def handle_sse(request):
        async with transport.connect_sse(request.scope, request.receive, request._send) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())
        return Response()

    app = Starlette(
        routes=[
            Route(sse_path, endpoint=handle_sse, methods=["GET"]),
            Mount(message_path, app=transport.handle_post_message),
        ]
    )

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()


def _normalize_http_path(path: str, trailing_slash: bool = False) -> str:
    """Normalize Starlette route paths."""

    normalized = path if path.startswith("/") else f"/{path}"
    if trailing_slash:
        return normalized.rstrip("/") + "/"
    if normalized != "/" and normalized.endswith("/"):
        return normalized.rstrip("/")
    return normalized


def start_server(
    transport: str = "stdio",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    sse_path: str = DEFAULT_SSE_PATH,
    message_path: str = DEFAULT_MESSAGES_PATH,
    tool_profile: str = DEFAULT_TOOL_PROFILE,
) -> None:
    """Start the MCP server using stdio or SSE."""

    server = _build_server(tool_profile=tool_profile)

    if transport == "stdio":
        asyncio.run(_run_stdio(server))
        return
    if transport == "sse":
        asyncio.run(_run_sse(server, host=host, port=port, sse_path=sse_path, message_path=message_path))
        return

    raise ValueError(f"Unsupported MCP transport: {transport}")
