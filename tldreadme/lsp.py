"""Lightweight LSP helpers for semantic code intelligence."""

from dataclasses import dataclass
from pathlib import Path
from shutil import which
from urllib.parse import unquote, urlparse
import json
import os
import re
import select
import subprocess
import sys


LANGUAGE_IDS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".java": "java",
}

SERVER_CANDIDATES = {
    "python": [("basedpyright-langserver", "--stdio"), ("pyright-langserver", "--stdio"), ("pylsp",)],
    "typescript": [("typescript-language-server", "--stdio")],
    "typescriptreact": [("typescript-language-server", "--stdio")],
    "javascript": [("typescript-language-server", "--stdio")],
    "javascriptreact": [("typescript-language-server", "--stdio")],
    "rust": [("rust-analyzer",)],
    "go": [("gopls", "serve"), ("gopls",)],
    "c": [("clangd", "--background-index"), ("clangd",)],
    "cpp": [("clangd", "--background-index"), ("clangd",)],
    "java": [("jdtls",)],
}

ROOT_MARKERS = (
    ".git",
    "pyproject.toml",
    "package.json",
    "tsconfig.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
)


@dataclass(frozen=True)
class LspServer:
    """Resolved language server executable for a given file."""

    language_id: str
    command: tuple[str, ...]


class LspSession:
    """Minimal synchronous JSON-RPC client for stdio language servers."""

    def __init__(self, command: tuple[str, ...], cwd: Path):
        self.command = command
        self.cwd = cwd
        self._next_id = 0
        self.notifications: list[dict] = []
        self.process: subprocess.Popen | None = None

    def __enter__(self):
        self.process = subprocess.Popen(
            list(self.command),
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.shutdown()
        finally:
            self._terminate()

    def initialize(self, root: Path) -> dict:
        """Initialize the server for a workspace root."""

        result = self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "clientInfo": {"name": "tldreadme", "version": "0.1.2"},
                "rootUri": root.resolve().as_uri(),
                "capabilities": {
                    "textDocument": {
                        "hover": {"dynamicRegistration": False},
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "documentSymbol": {"dynamicRegistration": False},
                        "diagnostic": {"dynamicRegistration": False},
                    },
                    "workspace": {"workspaceFolders": True},
                },
                "workspaceFolders": [{"uri": root.resolve().as_uri(), "name": root.name or str(root)}],
            },
        )
        self.notify("initialized", {})
        return result

    def open_document(self, path: Path, language_id: str, text: str) -> None:
        """Send didOpen for a document."""

        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path.resolve().as_uri(),
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )

    def request(self, method: str, params: dict | None) -> object:
        """Send a JSON-RPC request and wait for the matching response."""

        request_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})

        while True:
            message = self._read_message()
            if "method" in message and "id" not in message:
                self.notifications.append(message)
                continue
            if "id" not in message:
                continue
            if message["id"] != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise RuntimeError(f"LSP request `{method}` failed: {error}")
            return message.get("result")

    def notify(self, method: str, params: dict | None) -> None:
        """Send a JSON-RPC notification."""

        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def shutdown(self) -> None:
        """Gracefully stop the language server when possible."""

        if not self.process or self.process.poll() is not None:
            return

        try:
            self.request("shutdown", None)
        except Exception:
            pass

        try:
            self.notify("exit", None)
        except Exception:
            pass

    def poll_notifications(self, timeout: float = 0.25, limit: int = 20) -> list[dict]:
        """Read any queued server notifications without blocking for long."""

        if not self.process or not self.process.stdout:
            return []

        collected: list[dict] = []
        stream = self.process.stdout
        for index in range(limit):
            wait = timeout if index == 0 else 0
            try:
                ready, _, _ = select.select([stream], [], [], wait)
            except Exception:
                break
            if not ready:
                break
            message = self._read_message()
            if "method" in message and "id" not in message:
                self.notifications.append(message)
                collected.append(message)
        return collected

    def consume_notifications(self, method: str | None = None) -> list[dict]:
        """Return and remove queued notifications, optionally filtered by method."""

        matched: list[dict] = []
        remaining: list[dict] = []
        for message in self.notifications:
            if method is None or message.get("method") == method:
                matched.append(message)
            else:
                remaining.append(message)
        self.notifications = remaining
        return matched

    def _terminate(self) -> None:
        """Terminate the subprocess if it is still running."""

        if not self.process:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)

    def _send(self, payload: dict) -> None:
        """Write a framed JSON-RPC message to the language server."""

        if not self.process or not self.process.stdin:
            raise RuntimeError("LSP process is not running.")

        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self.process.stdin.write(header + body)
        self.process.stdin.flush()

    def _read_message(self) -> dict:
        """Read a framed JSON-RPC message from the language server."""

        if not self.process or not self.process.stdout:
            raise RuntimeError("LSP process is not running.")

        headers: dict[str, str] = {}
        while True:
            raw = self.process.stdout.readline()
            if raw == b"":
                raise RuntimeError("Language server closed stdout unexpectedly.")
            line = raw.decode("utf-8").strip()
            if not line:
                break
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.lower()] = value.strip()

        content_length = int(headers.get("content-length", "0"))
        if content_length <= 0:
            raise RuntimeError("Invalid LSP message without Content-Length.")

        body = self.process.stdout.read(content_length)
        if len(body) != content_length:
            raise RuntimeError("Short read while waiting for LSP response.")
        return json.loads(body.decode("utf-8"))


def infer_workspace_root(path: str | Path) -> Path:
    """Find the nearest project root for an input file."""

    current = Path(path).resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate

    return current


def guess_language_id(path: str | Path) -> str | None:
    """Infer the LSP language id from a file extension."""

    return LANGUAGE_IDS.get(Path(path).suffix.lower())


def resolve_lsp_server(path: str | Path) -> LspServer:
    """Resolve the best available LSP command for a file."""

    language_id = guess_language_id(path)
    if not language_id:
        raise RuntimeError(f"No LSP mapping for `{path}`.")

    for candidate in SERVER_CANDIDATES.get(language_id, []):
        executable = which(candidate[0])
        if executable:
            return LspServer(language_id=language_id, command=(executable, *candidate[1:]))

    checked = ", ".join(command[0] for command in SERVER_CANDIDATES.get(language_id, []))
    raise RuntimeError(f"No language server found for {language_id}. Checked: {checked}")


def semantic_inspect(
    path: str,
    line: int,
    column: int | None = None,
    *,
    root: str | None = None,
    include_references: bool = True,
    include_document_symbols: bool = True,
) -> dict:
    """Query a language server for semantic information at a file position."""

    file_path = Path(path).resolve()
    if not file_path.exists():
        raise RuntimeError(f"No such file: {file_path}")

    server = resolve_lsp_server(file_path)
    workspace_root = Path(root).resolve() if root else infer_workspace_root(file_path)
    text = file_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if line < 1 or line > max(len(lines), 1):
        raise RuntimeError(f"Line {line} is out of range for {file_path}")

    resolved_column = column or infer_identifier_column(lines[line - 1])
    position = {"line": line - 1, "character": max(0, resolved_column - 1)}

    with LspSession(server.command, workspace_root) as session:
        session.initialize(workspace_root)
        session.open_document(file_path, server.language_id, text)
        hover = session.request("textDocument/hover", _text_document_position(file_path, position))
        definitions = session.request("textDocument/definition", _text_document_position(file_path, position))
        references = []
        if include_references:
            references = session.request(
                "textDocument/references",
                {
                    **_text_document_position(file_path, position),
                    "context": {"includeDeclaration": False},
                },
            )
        document_symbols = []
        if include_document_symbols:
            try:
                document_symbols = session.request(
                    "textDocument/documentSymbol",
                    {"textDocument": {"uri": file_path.as_uri()}},
                )
            except RuntimeError:
                document_symbols = []

    return {
        "path": str(file_path),
        "workspace_root": str(workspace_root),
        "language_id": server.language_id,
        "server_command": list(server.command),
        "line": line,
        "column": resolved_column,
        "token": token_at_position(lines[line - 1], resolved_column),
        "hover": normalize_hover(hover),
        "definitions": normalize_locations(definitions),
        "references": normalize_locations(references),
        "document_symbols": normalize_document_symbols(document_symbols),
    }


def workspace_symbols(path: str, query: str, *, root: str | None = None, limit: int = 20) -> dict:
    """Query workspace symbols through the language server for a file's language."""

    file_path = Path(path).resolve()
    server = resolve_lsp_server(file_path)
    workspace_root = Path(root).resolve() if root else infer_workspace_root(file_path)

    with LspSession(server.command, workspace_root) as session:
        session.initialize(workspace_root)
        symbols = session.request("workspace/symbol", {"query": query}) or []

    return {
        "path": str(file_path),
        "workspace_root": str(workspace_root),
        "language_id": server.language_id,
        "server_command": list(server.command),
        "query": query,
        "symbols": normalize_workspace_symbols(symbols)[:limit],
    }


def document_diagnostics(path: str, *, root: str | None = None) -> dict:
    """Query a language server for document diagnostics."""

    file_path = Path(path).resolve()
    if not file_path.exists():
        raise RuntimeError(f"No such file: {file_path}")

    server = resolve_lsp_server(file_path)
    workspace_root = Path(root).resolve() if root else infer_workspace_root(file_path)
    text = file_path.read_text(encoding="utf-8", errors="replace")

    with LspSession(server.command, workspace_root) as session:
        initialized = session.initialize(workspace_root)
        capabilities = initialized.get("capabilities", {}) if isinstance(initialized, dict) else {}
        session.open_document(file_path, server.language_id, text)

        diagnostics: list[dict] = []
        diagnostic_source = None

        if capabilities.get("diagnosticProvider"):
            try:
                diagnostic_result = session.request(
                    "textDocument/diagnostic",
                    {"textDocument": {"uri": file_path.as_uri()}},
                )
                diagnostics = normalize_document_diagnostics(diagnostic_result, uri=file_path.as_uri())
                diagnostic_source = "textDocument/diagnostic"
            except RuntimeError:
                diagnostics = []

        if not diagnostics:
            try:
                session.request("textDocument/documentSymbol", {"textDocument": {"uri": file_path.as_uri()}})
            except RuntimeError:
                pass
            session.poll_notifications()
            diagnostics = normalize_publish_diagnostics(
                session.consume_notifications("textDocument/publishDiagnostics"),
                path=file_path,
            )
            diagnostic_source = "textDocument/publishDiagnostics" if diagnostics else None

        try:
            document_symbols = session.request(
                "textDocument/documentSymbol",
                {"textDocument": {"uri": file_path.as_uri()}},
            )
        except RuntimeError:
            document_symbols = []

    return {
        "path": str(file_path),
        "workspace_root": str(workspace_root),
        "language_id": server.language_id,
        "server_command": list(server.command),
        "diagnostic_source": diagnostic_source,
        "diagnostics": diagnostics,
        "document_symbols": normalize_document_symbols(document_symbols),
    }


def semantic_inspect_symbol(name: str, path: str, line: int, *, root: str | None = None) -> dict:
    """Run an LSP query for a symbol found by text search."""

    search_line = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[line - 1]
    column = infer_identifier_column(search_line, preferred=name)
    return semantic_inspect(path, line, column, root=root)


def infer_identifier_column(text: str, preferred: str | None = None) -> int:
    """Infer a reasonable 1-based identifier column from a source line."""

    if preferred:
        index = text.find(preferred)
        if index >= 0:
            return index + 1

    match = re.search(r"[A-Za-z_][A-Za-z0-9_]*", text)
    if match:
        return match.start() + 1

    stripped = len(text) - len(text.lstrip())
    return stripped + 1


def token_at_position(text: str, column: int) -> str:
    """Return the token under a 1-based column when possible."""

    if not text:
        return ""

    index = max(0, min(len(text) - 1, column - 1))
    for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", text):
        if match.start() <= index < match.end():
            return match.group(0)
    return ""


def normalize_hover(result: object) -> str | None:
    """Convert an LSP hover result into plain text."""

    if not result:
        return None

    contents = result.get("contents") if isinstance(result, dict) else result
    parts = _flatten_hover_contents(contents)
    rendered = "\n".join(part for part in parts if part)
    return rendered or None


def _flatten_hover_contents(contents: object) -> list[str]:
    """Flatten LSP hover content variants into strings."""

    if contents is None:
        return []
    if isinstance(contents, str):
        return [contents]
    if isinstance(contents, dict):
        if "value" in contents:
            return [str(contents["value"])]
        if "language" in contents and "value" in contents:
            return [str(contents["value"])]
        return [json.dumps(contents, sort_keys=True)]
    if isinstance(contents, list):
        parts: list[str] = []
        for item in contents:
            parts.extend(_flatten_hover_contents(item))
        return parts
    return [str(contents)]


def normalize_locations(result: object) -> list[dict]:
    """Normalize Location and LocationLink responses into a stable shape."""

    if not result:
        return []
    if isinstance(result, dict):
        items = [result]
    else:
        items = list(result)

    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue

        uri = item.get("uri") or item.get("targetUri")
        range_data = item.get("range") or item.get("targetSelectionRange") or item.get("targetRange")
        if not uri or not range_data:
            continue

        start = range_data.get("start", {})
        end = range_data.get("end", start)
        normalized.append(
            {
                "uri": uri,
                "path": uri_to_path(uri),
                "line": start.get("line", 0) + 1,
                "column": start.get("character", 0) + 1,
                "end_line": end.get("line", 0) + 1,
                "end_column": end.get("character", 0) + 1,
            }
        )

    return normalized


def normalize_document_symbols(result: object) -> list[dict]:
    """Normalize document symbol responses into a flat list."""

    if not result:
        return []

    normalized: list[dict] = []

    def visit(symbols: list[dict]) -> None:
        for symbol in symbols:
            if "location" in symbol:
                start = symbol["location"]["range"]["start"]
                end = symbol["location"]["range"]["end"]
            else:
                start = symbol.get("selectionRange", symbol.get("range", {})).get("start", {})
                end = symbol.get("selectionRange", symbol.get("range", {})).get("end", start)

            normalized.append(
                {
                    "name": symbol.get("name", ""),
                    "kind": symbol.get("kind"),
                    "detail": symbol.get("detail"),
                    "line": start.get("line", 0) + 1,
                    "column": start.get("character", 0) + 1,
                    "end_line": end.get("line", 0) + 1,
                    "end_column": end.get("character", 0) + 1,
                }
            )
            children = symbol.get("children") or []
            if children:
                visit(children)

    visit(list(result))
    return normalized


def normalize_workspace_symbols(result: object) -> list[dict]:
    """Normalize workspace/symbol results into a stable shape."""

    if not result:
        return []

    normalized = []
    for symbol in list(result):
        location = symbol.get("location") if isinstance(symbol, dict) else None
        if not location:
            continue
        uri = location.get("uri")
        range_data = location.get("range", {})
        start = range_data.get("start", {})
        end = range_data.get("end", start)
        normalized.append(
            {
                "name": symbol.get("name", ""),
                "kind": symbol.get("kind"),
                "container_name": symbol.get("containerName"),
                "path": uri_to_path(uri) if uri else "",
                "uri": uri,
                "line": start.get("line", 0) + 1,
                "column": start.get("character", 0) + 1,
                "end_line": end.get("line", 0) + 1,
                "end_column": end.get("character", 0) + 1,
            }
        )

    return normalized


def normalize_document_diagnostics(result: object, *, uri: str | None = None) -> list[dict]:
    """Normalize textDocument/diagnostic results into a stable shape."""

    if not result:
        return []

    if isinstance(result, dict):
        items = result.get("items", [])
    else:
        items = list(result)

    normalized = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(_normalize_diagnostic_item(item, uri=uri))
    return normalized


def normalize_publish_diagnostics(messages: list[dict], *, path: Path | None = None) -> list[dict]:
    """Normalize textDocument/publishDiagnostics notifications."""

    expected_path = str(path.resolve()) if path else None
    normalized: list[dict] = []

    for message in messages:
        params = message.get("params", {})
        uri = params.get("uri")
        message_path = uri_to_path(uri) if uri else expected_path
        if expected_path and message_path != expected_path:
            continue
        for item in params.get("diagnostics", []):
            if isinstance(item, dict):
                normalized.append(_normalize_diagnostic_item(item, uri=uri, path=message_path))

    return normalized


def _normalize_diagnostic_item(item: dict, *, uri: str | None = None, path: str | None = None) -> dict:
    """Normalize a single LSP diagnostic object."""

    range_data = item.get("range", {})
    start = range_data.get("start", {})
    end = range_data.get("end", start)
    related = []
    for info in item.get("relatedInformation", []) or []:
        location = info.get("location", {})
        location_uri = location.get("uri")
        related_range = location.get("range", {})
        related_start = related_range.get("start", {})
        related.append(
            {
                "message": info.get("message"),
                "path": uri_to_path(location_uri) if location_uri else None,
                "uri": location_uri,
                "line": related_start.get("line", 0) + 1,
                "column": related_start.get("character", 0) + 1,
            }
        )

    return {
        "path": path or (uri_to_path(uri) if uri else None),
        "uri": uri,
        "line": start.get("line", 0) + 1,
        "column": start.get("character", 0) + 1,
        "end_line": end.get("line", 0) + 1,
        "end_column": end.get("character", 0) + 1,
        "severity": normalize_diagnostic_severity(item.get("severity")),
        "code": item.get("code"),
        "source": item.get("source"),
        "message": item.get("message", ""),
        "tags": list(item.get("tags") or []),
        "related_information": related,
    }


def normalize_diagnostic_severity(value: object) -> str:
    """Convert numeric LSP severity values into readable labels."""

    mapping = {
        1: "error",
        2: "warning",
        3: "information",
        4: "hint",
    }
    if isinstance(value, str):
        return value.lower()
    return mapping.get(value, "unknown")


def uri_to_path(uri: str) -> str:
    """Convert a file URI into a local filesystem path."""

    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return uri

    path = unquote(parsed.path)
    if sys.platform.startswith("win") and path.startswith("/"):
        path = path[1:]
    return path


def _text_document_position(path: Path, position: dict[str, int]) -> dict:
    """Build a textDocument/position payload."""

    return {
        "textDocument": {"uri": path.resolve().as_uri()},
        "position": position,
    }
