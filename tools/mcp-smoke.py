#!/usr/bin/env python3
"""Exercise the TLDRReadme MCP server through its real stdio transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from shutil import which
from tempfile import mkdtemp

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROUTER_TOOLS = {
    "repo_next_action",
    "repo_lookup",
    "change_plan",
    "verify_change",
}
SETUP_TOOL = "configuration_setup"


def _text(result) -> str:
    return "\n".join(item.text for item in result.content if getattr(item, "type", None) == "text")


async def _call(session: ClientSession, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments)
    if result.isError:
        raise RuntimeError(f"{name} returned an MCP error: {_text(result)}")
    text = _text(result)
    if not text.strip():
        raise RuntimeError(f"{name} returned no text content")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = text
    return {
        "name": name,
        "bytes": len(text.encode("utf-8")),
        "_payload": payload,
    }


def _record(call: dict) -> dict:
    """Return the report-safe portion of a call result."""

    return {key: value for key, value in call.items() if key != "_payload"}


def _first_python_file(root: Path) -> Path:
    """Choose a stable Python file for semantic and source-oriented tools."""

    candidates = (
        root / "tldreadme" / "mcp_server.py",
        root / "src" / "sample.py",
        root / "sample.py",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for candidate in root.rglob("*.py"):
        if not any(part in {".git", ".venv", "venv", "__pycache__"} for part in candidate.parts):
            return candidate
    raise RuntimeError(f"no Python source file found under {root}")


def _lsp_fixture() -> tuple[Path, Path]:
    """Create a small source file for an installed language server."""

    scratch = Path(mkdtemp(prefix="tldreadme-lsp-smoke-"))
    candidates = (
        (
            ("basedpyright-langserver", "pyright-langserver", "pylsp"),
            "sample.py",
            "def sample() -> int:\n    return 1\n",
        ),
        (("rust-analyzer",), "sample.rs", "fn sample() -> i32 {\n    1\n}\n"),
        (("clangd",), "sample.c", "int sample(void) {\n    return 1;\n}\n"),
        (("gopls",), "sample.go", "package sample\n\nfunc Sample() int {\n\treturn 1\n}\n"),
        (
            ("typescript-language-server",),
            "sample.ts",
            "export function sample(): number {\n  return 1;\n}\n",
        ),
        (("jdtls",), "Sample.java", "class Sample {\n  int sample() { return 1; }\n}\n"),
    )
    for commands, filename, source in candidates:
        if any(which(command) for command in commands):
            path = scratch / filename
            path.write_text(source, encoding="utf-8")
            return path, scratch
    raise RuntimeError("full profile advertised LSP tools without an installed language server")


async def _exercise_full_surface(
    session: ClientSession,
    names: set[str],
    root: Path,
) -> list[dict]:
    """Call every tool advertised by the configured full profile."""

    source = _first_python_file(root)
    relative_source = str(source.relative_to(root))
    symbol = "_build_server" if source.name == "mcp_server.py" else source.stem
    calls: list[dict] = []
    lsp_source, lsp_root = _lsp_fixture()

    async def invoke(name: str, arguments: dict) -> dict | None:
        if name not in names:
            return None
        call = await _call(session, name, arguments)
        calls.append(_record(call))
        return call

    await invoke("repo_next_action", {"root": str(root)})
    await invoke(
        "repo_lookup",
        {
            "query": "MCP router and full tool profiles",
            "root": str(root),
            "limit": 5,
        },
    )
    await invoke(
        "change_plan",
        {
            "goal": "Document router and specialist MCP access",
            "root": str(root),
        },
    )
    await invoke(
        "verify_change",
        {
            "files": [relative_source],
            "root": str(root),
            "run_commands": False,
        },
    )

    static_calls = {
        "read_recent": {"scope": str(root), "days": 30},
        "history_search": {
            "query": "MCP smoke plan",
            "root": str(root),
            "limit": 3,
        },
        "read_symbol": {"name": symbol},
        "read_similar": {"query": "MCP tool routing", "limit": 2},
        "read_module": {"path": str(source.parent)},
        "read_flow": {"entry": symbol, "depth": 2},
        "read_depends": {"name": symbol},
        "read_grep": {
            "pattern": "tool_profile",
            "paths": [str(root)],
            "max_results": 5,
        },
        "read_grep_files": {
            "pattern": "tool_profile",
            "paths": [str(root)],
        },
        "read_semantic": {
            "path": str(lsp_source),
            "line": 1,
            "root": str(lsp_root),
            "include_references": False,
        },
        "read_workspace_symbols": {
            "path": str(lsp_source),
            "query": "sample",
            "root": str(lsp_root),
            "limit": 5,
        },
        "scan_context": {"root": str(root), "limit": 3},
        "search_context": {
            "query": "tool profile",
            "root": str(root),
            "limit": 3,
        },
        "edit_context": {
            "path": str(source),
            "line": 1,
            "root": str(root),
        },
        "test_map": {"path": relative_source, "root": str(root)},
        "pattern_search": {
            "query": "MCP tool routing",
            "path": relative_source,
            "root": str(root),
            "limit": 2,
        },
        "diagnostics_here": {
            "path": str(lsp_source),
            "line": 1,
            "root": str(lsp_root),
        },
        "know": {"name": symbol, "root": str(root)},
        "impact": {"name": symbol, "root": str(root)},
        "discover": {"query": "MCP tool routing", "root": str(root)},
        "explain": {"name": symbol, "root": str(root)},
        "tldr": {"path": str(source.parent)},
        "suggest_goals": {"path": str(root)},
        "best_question": {
            "goal": "Document the MCP tool surface",
            "path": str(root),
        },
        "goal_flow": {"path": str(root)},
        "auto_iterate": {
            "path": str(root),
            "goal": "Document the MCP tool surface",
            "rounds": 1,
        },
        "capture_plans": {
            "text": "Document the router and specialist tool surfaces.",
            "root": str(root),
        },
        "whats_next": {"root": str(root)},
        "current_roadmap": {"root": str(root), "write": False},
        "audit_run": {
            "category": "all",
            "root": str(root),
            "dry_run": True,
        },
        "audit_profiles": {},
    }
    for name, arguments in static_calls.items():
        await invoke(name, arguments)

    plan = await invoke(
        "plan_create",
        {
            "title": "MCP smoke plan",
            "goal": "Exercise the mutable workboard tools",
            "phases": ["Verification"],
        },
    )
    plan_id = plan["_payload"]["id"] if plan else None
    if plan_id:
        await invoke("plan_list", {})
        await invoke("plan_current", {})
        await invoke(
            "plan_update",
            {
                "plan_id": plan_id,
                "add_notes": ["Full-profile integration smoke."],
                "current_phase": "Verification",
            },
        )
        task = await invoke(
            "task_add",
            {
                "plan_id": plan_id,
                "title": "Exercise workboard tools",
                "phase": "Verification",
                "files": [relative_source],
                "acceptance_criteria": ["Every advertised tool returns."],
            },
        )
        task_id = task["_payload"]["id"] if task else None
        if task_id:
            await invoke(
                "task_update",
                {
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "status": "in_progress",
                    "add_evidence": ["Integration smoke started."],
                },
            )
            await invoke(
                "session_update",
                {
                    "current_plan_id": plan_id,
                    "current_task_id": task_id,
                    "current_phase": "Verification",
                    "current_focus": "MCP integration smoke",
                },
            )
            await invoke(
                "session_note",
                {
                    "note": "Full-profile integration smoke.",
                    "plan_id": plan_id,
                    "phase": "Verification",
                },
            )
            await invoke(
                "task_complete",
                {
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "evidence": ["Every reachable tool returned."],
                },
            )
        await invoke("plan_archive", {"plan_id": plan_id})

    scratch = Path(mkdtemp(prefix="tldreadme-mcp-smoke-"))
    kev_source = scratch / "kev-source.json"
    kev_source.write_text(
        json.dumps({"vulnerabilities": []}),
        encoding="utf-8",
    )
    await invoke(
        "audit_kev_refresh",
        {
            "url": kev_source.as_uri(),
            "output_path": str(scratch / "kev.json"),
        },
    )

    called = {call["name"] for call in calls}
    missing = sorted(names - called)
    if missing:
        raise RuntimeError("full-profile smoke has no successful call for: " + ", ".join(missing))
    return calls


async def smoke(
    profile: str,
    root: Path,
    setup_state: str,
    *,
    server_python: str,
    use_checkout: bool,
) -> dict:
    env = dict(os.environ)
    if use_checkout:
        source_root = str(Path(__file__).resolve().parents[1])
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            source_root
            if not existing_pythonpath
            else os.pathsep.join([source_root, existing_pythonpath])
        )
    else:
        env.pop("PYTHONPATH", None)
    if setup_state == "configured":
        env["TLDREADME_SETUP_COMPLETE"] = "1"
        env["TLDREADME_CONFIG_PATH"] = str(
            root / ".tldr" / f"isolated-smoke-config-{os.getpid()}.json"
        )
        env["LITELLM_URL"] = ""
        env["OLLAMA_URL"] = "http://127.0.0.1:9"
        env["QDRANT_URL"] = "http://127.0.0.1:9"
        env["FALKORDB_URL"] = "redis://127.0.0.1:9"
        env["TLDREADME_EMBED_MODEL"] = "ollama/nomic-embed-text"
        env["TLDREADME_CHAT_MODEL"] = "ollama/qwen2.5-coder:3b-instruct"
        env["TLDREADME_MODEL_TIMEOUT_SECONDS"] = "0.5"
    else:
        for name in (
            "TLDREADME_SETUP_COMPLETE",
            "LITELLM_URL",
            "OLLAMA_URL",
            "QDRANT_URL",
            "FALKORDB_URL",
            "TLDREADME_CHAT_MODEL",
            "TLDREADME_EMBED_MODEL",
        ):
            env.pop(name, None)
        env["TLDREADME_MODEL_TIMEOUT_SECONDS"] = "0.5"
        env["TLDREADME_CONFIG_PATH"] = str(
            root / ".tldr" / f"missing-smoke-config-{os.getpid()}.json"
        )

    server_cwd = Path(mkdtemp(prefix="tldreadme-mcp-server-"))
    parameters = StdioServerParameters(
        command=server_python,
        args=[
            "-m",
            "tldreadme",
            "serve",
            "--tool-profile",
            profile,
        ],
        cwd=str(server_cwd),
        env=env,
    )

    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}

            if setup_state == "required":
                if names != {SETUP_TOOL}:
                    raise RuntimeError(
                        "unconfigured server must expose only configuration_setup: "
                        + ", ".join(sorted(names))
                    )
                setup_result = await _call(session, SETUP_TOOL, {})
                setup_payload = setup_result["_payload"]
                if not isinstance(setup_payload, dict):
                    raise RuntimeError("configuration_setup did not return a JSON object")
                if setup_payload.get("status") != "setup_required":
                    raise RuntimeError("configuration_setup did not report setup_required")
                if "configuration_form" not in setup_payload:
                    raise RuntimeError("configuration_setup did not return host form metadata")
                commands = setup_payload.get("commands", {})
                if commands.get("ollama") != "tldr setup --provider ollama":
                    raise RuntimeError(
                        "configuration_setup did not preserve the installed CLI setup command"
                    )
                plugin_uvx = commands.get("plugin_uvx", {})
                if "uvx --python 3.12" not in plugin_uvx.get("ollama", ""):
                    raise RuntimeError(
                        "configuration_setup did not return a plugin-only uvx setup command"
                    )
                configured_result = await _call(
                    session,
                    SETUP_TOOL,
                    {
                        "provider": "ollama",
                        "ollama_url": "http://127.0.0.1:9",
                        "qdrant_url": "http://127.0.0.1:9",
                        "falkordb_url": "redis://127.0.0.1:9",
                        "tool_profile": "router",
                    },
                )
                configured_payload = configured_result["_payload"]
                if not isinstance(configured_payload, dict) or not configured_payload.get(
                    "configured"
                ):
                    raise RuntimeError("configuration_setup did not persist host setup")
                refreshed = await session.list_tools()
                refreshed_names = {tool.name for tool in refreshed.tools}
                if refreshed_names != ROUTER_TOOLS:
                    raise RuntimeError(
                        "host setup did not unlock the router surface: "
                        + ", ".join(sorted(refreshed_names))
                    )
                return {
                    "profile": profile,
                    "setup_state": setup_state,
                    "tool_count": len(names),
                    "tools": sorted(names),
                    "post_setup_tools": sorted(refreshed_names),
                    "calls": [
                        _record(setup_result),
                        _record(configured_result),
                    ],
                }

            tools_by_name = {tool.name: tool for tool in listed.tools}
            lookup_schema = tools_by_name["repo_lookup"].inputSchema
            lookup_properties = lookup_schema.get("properties", {})
            history_sources = (
                lookup_properties.get("source_types", {}).get("items", {}).get("enum", [])
            )
            if "history" not in history_sources or "history_all_refs" not in lookup_properties:
                raise RuntimeError("repo_lookup does not advertise historical lookup controls")
            if "history_search" in names:
                history_schema = tools_by_name["history_search"].inputSchema
                history_properties = history_schema.get("properties", {})
                required_history_fields = {
                    "query",
                    "scope",
                    "all_refs",
                    "since",
                    "until",
                    "limit",
                    "max_commits",
                    "timeout_seconds",
                }
                missing_history_fields = sorted(required_history_fields - set(history_properties))
                if missing_history_fields:
                    raise RuntimeError(
                        "history_search schema is missing: " + ", ".join(missing_history_fields)
                    )

            required = ROUTER_TOOLS
            missing = sorted(required - names)
            if missing:
                raise RuntimeError(
                    f"{profile} profile is missing required tools: {', '.join(missing)}"
                )
            if profile == "router" and names != ROUTER_TOOLS:
                raise RuntimeError(
                    "router profile drifted from its four-tool contract: "
                    + ", ".join(sorted(names))
                )

            if profile == "full":
                calls = await _exercise_full_surface(session, names, root)
            else:
                calls = [
                    _record(
                        await _call(
                            session,
                            "repo_next_action",
                            {"root": str(root)},
                        )
                    ),
                    _record(
                        await _call(
                            session,
                            "repo_lookup",
                            {
                                "query": "MCP router and full tool profiles",
                                "root": str(root),
                                "limit": 5,
                            },
                        )
                    ),
                    _record(
                        await _call(
                            session,
                            "change_plan",
                            {
                                "goal": "Document router and specialist MCP access",
                                "root": str(root),
                            },
                        )
                    ),
                    _record(
                        await _call(
                            session,
                            "verify_change",
                            {
                                "files": [str(_first_python_file(root))],
                                "root": str(root),
                                "run_commands": False,
                            },
                        )
                    ),
                ]

            return {
                "profile": profile,
                "setup_state": setup_state,
                "tool_count": len(names),
                "tools": sorted(names),
                "calls": calls,
            }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["router", "full"], required=True)
    parser.add_argument(
        "--setup-state",
        choices=["configured", "required"],
        default="configured",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--server-python",
        default=sys.executable,
        help="Python interpreter used to launch the MCP server.",
    )
    parser.add_argument(
        "--installed-server",
        action="store_true",
        help="Launch the installed package without adding the source checkout to PYTHONPATH.",
    )
    args = parser.parse_args()
    report = asyncio.run(
        smoke(
            args.profile,
            args.root.resolve(),
            args.setup_state,
            server_python=args.server_python,
            use_checkout=not args.installed_server,
        )
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
