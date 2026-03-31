#!/usr/bin/env python3
"""
Search Gateway MCP Server — read-only, root-jailed search over SSE.

Runs OUTSIDE a sandbox (or on a remote server) and exposes ripgrep-based
search, file finding, and file reading locked to a single root directory.
Sandboxed environments connect via HTTP port.

Usage:
    python tools/search-gateway.py --root ~/claude --port 8901
    python tools/search-gateway.py --root /data/code --port 8901 --api-key SECRET
    python tools/search-gateway.py --root ~/claude --host 0.0.0.0  # remote access

From inside sandbox / another server, configure MCP client to connect:
    http://<host>:8901/sse
"""

import argparse
import asyncio
import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path jail
# ---------------------------------------------------------------------------

_ROOT: Path = Path.home()  # overridden by --root


def _jail(requested: str) -> Path:
    """Resolve *requested* path and verify it lives under _ROOT.

    Raises ValueError on escape attempts (symlinks, .., etc.).
    """
    # Treat relative paths as relative to root
    if not os.path.isabs(requested):
        candidate = _ROOT / requested
    else:
        candidate = Path(requested)

    resolved = candidate.resolve()

    if not (resolved == _ROOT or str(resolved).startswith(str(_ROOT) + os.sep)):
        raise ValueError(f"Path escapes root: {requested}")

    if not resolved.exists():
        raise ValueError(f"Path does not exist: {requested}")

    return resolved


def _relative(absolute: Path) -> str:
    """Return path relative to root for display."""
    try:
        return str(absolute.relative_to(_ROOT))
    except ValueError:
        return str(absolute)


# ---------------------------------------------------------------------------
# ripgrep helpers
# ---------------------------------------------------------------------------

def _find_rg() -> str:
    """Locate the rg binary."""
    for candidate in ["rg", "/usr/local/bin/rg", "/opt/homebrew/bin/rg"]:
        try:
            subprocess.run([candidate, "--version"], capture_output=True, check=True)
            return candidate
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    raise RuntimeError("ripgrep (rg) not found — install it: https://github.com/BurntSushi/ripgrep")


_RG: Optional[str] = None


def _rg() -> str:
    global _RG
    if _RG is None:
        _RG = _find_rg()
    return _RG


def _rg_search(
    pattern: str,
    path: Path,
    *,
    glob_filter: Optional[str] = None,
    file_type: Optional[str] = None,
    case_insensitive: bool = True,
    context_lines: int = 3,
    max_results: int = 30,
    fixed_strings: bool = False,
) -> list[dict]:
    """Run ripgrep and return structured results."""
    cmd = [_rg(), "--json", "-C", str(context_lines)]

    if case_insensitive:
        cmd.append("-i")
    if fixed_strings:
        cmd.append("-F")
    if glob_filter:
        cmd.extend(["--glob", glob_filter])
    if file_type:
        cmd.extend(["--type", file_type])

    # Skip noise
    for skip in ["node_modules", "target", "dist", ".git", "__pycache__",
                 "*.min.js", "*.min.css", "*.map"]:
        cmd.extend(["--glob", f"!{skip}"])

    cmd.extend([pattern, str(path)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return [{"error": "Search timed out after 30s"}]

    hits = []
    current = None
    ctx_before, ctx_after = [], []

    for line in result.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get("type")

        if msg_type == "context":
            text = obj["data"].get("lines", {}).get("text", "").rstrip()
            if current is None:
                ctx_before.append(text)
            else:
                ctx_after.append(text)

        elif msg_type == "match":
            if current is not None:
                current["before"] = ctx_before
                current["after"] = ctx_after
                hits.append(current)
                if len(hits) >= max_results:
                    break

            data = obj["data"]
            raw_path = data.get("path", {}).get("text", "")
            try:
                display_path = _relative(Path(raw_path).resolve())
            except Exception:
                display_path = raw_path

            current = {
                "file": display_path,
                "line": data.get("line_number", 0),
                "text": data.get("lines", {}).get("text", "").rstrip(),
                "before": [],
                "after": [],
            }
            ctx_before, ctx_after = [], []

        elif msg_type == "end":
            if current is not None:
                current["before"] = ctx_before
                current["after"] = ctx_after
                hits.append(current)
                current = None
                ctx_before, ctx_after = [], []

    if current is not None and len(hits) < max_results:
        current["before"] = ctx_before
        current["after"] = ctx_after
        hits.append(current)

    return hits[:max_results]


# ---------------------------------------------------------------------------
# glob/find helper
# ---------------------------------------------------------------------------

def _find_files(
    pattern: str,
    path: Path,
    max_results: int = 100,
) -> list[str]:
    """Find files matching a glob pattern under path, respecting jail."""
    results = []
    for p in sorted(path.rglob("*")):
        if not p.is_file():
            continue
        # Skip hidden dirs and common noise
        parts = p.relative_to(path).parts
        if any(part.startswith(".") or part in ("node_modules", "target", "dist", "__pycache__") for part in parts):
            continue
        if fnmatch.fnmatch(p.name, pattern) or fnmatch.fnmatch(str(p.relative_to(path)), pattern):
            results.append(_relative(p))
            if len(results) >= max_results:
                break
    return results


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

def build_server(api_key: Optional[str] = None):
    """Create the MCP server with search/find/read tools."""
    from mcp.server import Server
    from mcp.types import TextContent, Tool

    server = Server("search-gateway")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="search",
                description=(
                    "Search file contents using ripgrep. Returns matching lines "
                    "with surrounding context. Supports regex and literal patterns."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Search pattern (regex by default, or literal with fixed_strings=true)",
                        },
                        "path": {
                            "type": "string",
                            "description": "Subdirectory to search within (relative to root). Omit for entire root.",
                            "default": ".",
                        },
                        "glob": {
                            "type": "string",
                            "description": "File glob filter, e.g. '*.py', '*.{ts,tsx}'",
                        },
                        "file_type": {
                            "type": "string",
                            "description": "ripgrep type filter, e.g. 'py', 'rust', 'ts'",
                        },
                        "case_insensitive": {
                            "type": "boolean",
                            "description": "Case insensitive search (default true)",
                            "default": True,
                        },
                        "context_lines": {
                            "type": "integer",
                            "description": "Lines of context before/after matches (default 3)",
                            "default": 3,
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of matches to return (default 30)",
                            "default": 30,
                        },
                        "fixed_strings": {
                            "type": "boolean",
                            "description": "Treat pattern as literal string, not regex",
                            "default": False,
                        },
                    },
                    "required": ["pattern"],
                },
            ),
            Tool(
                name="find_files",
                description=(
                    "Find files by name/glob pattern. Returns relative paths. "
                    "Use for discovering files before reading them."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Glob pattern to match file names, e.g. '*.py', 'test_*.rs', '**/*.md'",
                        },
                        "path": {
                            "type": "string",
                            "description": "Subdirectory to search within (relative to root). Omit for entire root.",
                            "default": ".",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum files to return (default 100)",
                            "default": 100,
                        },
                    },
                    "required": ["pattern"],
                },
            ),
            Tool(
                name="read_file",
                description=(
                    "Read a file's contents. Returns numbered lines. "
                    "Path must be relative to the gateway root directory."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path relative to root",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Starting line number (1-based, default 1)",
                            "default": 1,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max lines to return (default 2000)",
                            "default": 2000,
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="list_dir",
                description=(
                    "List directory contents. Returns file and subdirectory names "
                    "with type indicators (file/dir). Path must be under root."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory path relative to root (default: root)",
                            "default": ".",
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == "search":
                search_path = _jail(arguments.get("path", "."))
                hits = _rg_search(
                    pattern=arguments["pattern"],
                    path=search_path,
                    glob_filter=arguments.get("glob"),
                    file_type=arguments.get("file_type"),
                    case_insensitive=arguments.get("case_insensitive", True),
                    context_lines=arguments.get("context_lines", 3),
                    max_results=arguments.get("max_results", 30),
                    fixed_strings=arguments.get("fixed_strings", False),
                )
                if not hits:
                    return [TextContent(type="text", text="No matches found.")]
                return [TextContent(type="text", text=json.dumps(hits, indent=2))]

            elif name == "find_files":
                search_path = _jail(arguments.get("path", "."))
                files = _find_files(
                    pattern=arguments["pattern"],
                    path=search_path,
                    max_results=arguments.get("max_results", 100),
                )
                if not files:
                    return [TextContent(type="text", text="No files found.")]
                return [TextContent(type="text", text="\n".join(files))]

            elif name == "read_file":
                file_path = _jail(arguments["path"])
                if not file_path.is_file():
                    return [TextContent(type="text", text=f"Error: {arguments['path']} is not a file")]

                offset = max(1, arguments.get("offset", 1))
                limit = min(5000, max(1, arguments.get("limit", 2000)))

                lines = file_path.read_text(errors="replace").splitlines()
                selected = lines[offset - 1 : offset - 1 + limit]

                numbered = []
                for i, line in enumerate(selected, start=offset):
                    numbered.append(f"{i:>6}\t{line}")

                if not numbered:
                    return [TextContent(type="text", text="(empty file)")]
                return [TextContent(type="text", text="\n".join(numbered))]

            elif name == "list_dir":
                dir_path = _jail(arguments.get("path", "."))
                if not dir_path.is_dir():
                    return [TextContent(type="text", text=f"Error: {arguments.get('path', '.')} is not a directory")]

                entries = []
                for child in sorted(dir_path.iterdir()):
                    if child.name.startswith("."):
                        continue
                    kind = "dir" if child.is_dir() else "file"
                    entries.append(f"[{kind}] {child.name}")

                if not entries:
                    return [TextContent(type="text", text="(empty directory)")]
                return [TextContent(type="text", text="\n".join(entries))]

            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

        except ValueError as e:
            return [TextContent(type="text", text=f"Access denied: {e}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]

    return server


# ---------------------------------------------------------------------------
# SSE transport (Starlette + uvicorn)
# ---------------------------------------------------------------------------

async def run_sse(server, host: str, port: int, api_key: Optional[str] = None):
    """Run the MCP server over SSE/HTTP."""
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response, JSONResponse
    from starlette.routing import Mount, Route

    transport = SseServerTransport("/messages/")

    # Optional API key middleware
    class AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if api_key:
                provided = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                if provided != api_key:
                    return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    async def handle_sse(request):
        async with transport.connect_sse(request.scope, request.receive, request._send) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())
        return Response()

    async def handle_health(request):
        return JSONResponse({
            "status": "ok",
            "root": str(_ROOT),
            "tools": ["search", "find_files", "read_file", "list_dir"],
        })

    app = Starlette(
        routes=[
            Route("/health", endpoint=handle_health, methods=["GET"]),
            Route("/sse", endpoint=handle_sse, methods=["GET"]),
            Mount("/messages/", app=transport.handle_post_message),
        ],
        middleware=[Middleware(AuthMiddleware)],
    )

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    print(f"\n  Search Gateway MCP Server")
    print(f"  Root:      {_ROOT}")
    print(f"  Endpoint:  http://{host}:{port}/sse")
    print(f"  Health:    http://{host}:{port}/health")
    print(f"  Auth:      {'API key required' if api_key else 'none'}")
    print()
    await uvicorn.Server(config).serve()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    global _ROOT

    parser = argparse.ArgumentParser(
        description="Search Gateway MCP — read-only, root-jailed search over SSE",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --root ~/claude --port 8901
  %(prog)s --root /data/repos --host 0.0.0.0 --port 8901 --api-key mysecret

Connect from sandbox:
  MCP endpoint: http://host.docker.internal:8901/sse
  Or for Claude Code: http://localhost:8901/sse
        """,
    )
    parser.add_argument(
        "--root", required=True,
        help="Root directory to jail all access to",
    )
    parser.add_argument(
        "--port", type=int, default=8901,
        help="Port to listen on (default: 8901)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1, use 0.0.0.0 for remote access)",
    )
    parser.add_argument(
        "--api-key",
        help="Optional API key for auth (clients send as Bearer token)",
    )

    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"Error: root directory does not exist: {root}", file=sys.stderr)
        sys.exit(1)

    _ROOT = root

    # Verify rg is available
    try:
        _rg()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    server = build_server(api_key=args.api_key)
    asyncio.run(run_sse(server, host=args.host, port=args.port, api_key=args.api_key))


if __name__ == "__main__":
    main()
