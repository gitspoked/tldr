"""Tests for LSP integration."""

from pathlib import Path
import sys
import tempfile

from tldreadme import chains
from tldreadme.lsp import LspServer, document_diagnostics, resolve_lsp_server, semantic_inspect, workspace_symbols


FAKE_LSP_SERVER = r"""
import json
import sys

current_uri = None

def read_message():
    headers = {}
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            return None
        if raw in (b"\r\n", b"\n"):
            break
        key, value = raw.decode("utf-8").split(":", 1)
        headers[key.lower()] = value.strip()
    length = int(headers["content-length"])
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def send(payload):
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break

    method = message.get("method")

    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "capabilities": {
                        "diagnosticProvider": {
                            "interFileDependencies": False,
                            "workspaceDiagnostics": False,
                        }
                    }
                },
            }
        )
        continue

    if method == "initialized":
        continue

    if method == "textDocument/didOpen":
        current_uri = message["params"]["textDocument"]["uri"]
        continue

    if method == "textDocument/hover":
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"contents": {"kind": "markdown", "value": "hover: fake symbol"}},
            }
        )
        continue

    if method == "textDocument/definition":
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "uri": current_uri,
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 10},
                    },
                },
            }
        )
        continue

    if method == "textDocument/references":
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": [
                    {
                        "uri": current_uri,
                        "range": {
                            "start": {"line": 2, "character": 11},
                            "end": {"line": 2, "character": 17},
                        },
                    }
                ],
            }
        )
        continue

    if method == "textDocument/documentSymbol":
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": [
                    {
                        "name": "sample",
                        "kind": 12,
                        "detail": "function",
                        "selectionRange": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 10},
                        },
                    }
                ],
            }
        )
        continue

    if method == "workspace/symbol":
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": [
                    {
                        "name": "sample",
                        "kind": 12,
                        "containerName": "module",
                        "location": {
                            "uri": current_uri,
                            "range": {
                                "start": {"line": 0, "character": 4},
                                "end": {"line": 0, "character": 10},
                            },
                        },
                    }
                ],
            }
        )
        continue

    if method == "textDocument/diagnostic":
        send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "kind": "full",
                    "items": [
                        {
                            "range": {
                                "start": {"line": 1, "character": 4},
                                "end": {"line": 1, "character": 10},
                            },
                            "severity": 2,
                            "source": "fake-lsp",
                            "message": "possible issue",
                        }
                    ],
                },
            }
        )
        continue

    if method == "shutdown":
        send({"jsonrpc": "2.0", "id": message["id"], "result": None})
        continue

    if method == "exit":
        break
"""


def test_resolve_lsp_server_picks_available_candidate(monkeypatch):
    monkeypatch.setattr(
        "tldreadme.lsp.which",
        lambda name: {
            "pyright-langserver": "/tmp/pyright-langserver",
        }.get(name),
    )

    server = resolve_lsp_server("example.py")

    assert server.language_id == "python"
    assert server.command == ("/tmp/pyright-langserver", "--stdio")


def test_semantic_inspect_with_fake_server(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source = root / "sample.py"
        source.write_text("def sample():\n    return 1\nvalue = sample()\n")

        fake_server = root / "fake_lsp.py"
        fake_server.write_text(FAKE_LSP_SERVER)

        monkeypatch.setattr(
            "tldreadme.lsp.resolve_lsp_server",
            lambda _path: LspServer("python", (sys.executable, str(fake_server))),
        )

        result = semantic_inspect(str(source), 1, 5, root=str(root))

    assert result["hover"] == "hover: fake symbol"
    assert result["definitions"][0]["line"] == 1
    assert result["references"][0]["line"] == 3
    assert result["document_symbols"][0]["name"] == "sample"
    assert result["server_command"][0] == sys.executable


def test_workspace_symbols_with_fake_server(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source = root / "sample.py"
        source.write_text("def sample():\n    return 1\n")

        fake_server = root / "fake_lsp.py"
        fake_server.write_text(FAKE_LSP_SERVER)

        monkeypatch.setattr(
            "tldreadme.lsp.resolve_lsp_server",
            lambda _path: LspServer("python", (sys.executable, str(fake_server))),
        )

        result = workspace_symbols(str(source), "sam", root=str(root))

    assert result["symbols"][0]["name"] == "sample"
    assert result["symbols"][0]["container_name"] == "module"


def test_document_diagnostics_with_fake_server(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        source = root / "sample.py"
        source.write_text("def sample():\n    return 1\n")

        fake_server = root / "fake_lsp.py"
        fake_server.write_text(FAKE_LSP_SERVER)

        monkeypatch.setattr(
            "tldreadme.lsp.resolve_lsp_server",
            lambda _path: LspServer("python", (sys.executable, str(fake_server))),
        )

        result = document_diagnostics(str(source), root=str(root))

    assert result["diagnostic_source"] == "textDocument/diagnostic"
    assert result["diagnostics"][0]["severity"] == "warning"
    assert result["diagnostics"][0]["message"] == "possible issue"


def test_know_includes_semantic_overlay(monkeypatch):
    monkeypatch.setattr(
        chains,
        "rg_search",
        lambda *_args, **_kwargs: [
            type(
                "Hit",
                (),
                {
                    "file": "/tmp/example.py",
                    "line": 3,
                    "text": "def sample():",
                    "before": [],
                    "after": ["    return 1"],
                },
            )()
        ],
    )
    monkeypatch.setattr(chains, "rg_files", lambda *_args, **_kwargs: ["/tmp/example.py"])
    monkeypatch.setattr(
        chains.rag,
        "read_symbol",
        lambda _name: {"callers": [{"name": "caller"}], "callees": [{"name": "callee"}]},
    )
    monkeypatch.setattr(
        chains,
        "semantic_inspect_symbol",
        lambda *_args, **_kwargs: {
            "hover": "hover text",
            "definitions": [{"path": "/tmp/example.py", "line": 3, "column": 5, "end_line": 3, "end_column": 11}],
            "references": [{"path": "/tmp/example.py", "line": 8, "column": 12, "end_line": 8, "end_column": 18}],
            "document_symbols": [{"name": "sample", "line": 3, "column": 5, "end_line": 3, "end_column": 11}],
            "server_command": ["pyright-langserver", "--stdio"],
        },
    )

    result = chains.know("sample", root=".")

    assert result["semantic"]["hover"] == "hover text"
    assert result["semantic"]["references"][0]["line"] == 8


def test_impact_prefers_lsp_references(monkeypatch):
    monkeypatch.setattr(chains, "rg_count", lambda *_args, **_kwargs: {"legacy.py": 9})
    monkeypatch.setattr(
        chains,
        "rg_search",
        lambda *_args, **_kwargs: [
            type("Hit", (), {"file": "/tmp/example.py", "line": 3, "text": "def sample():", "before": [], "after": []})()
        ],
    )
    monkeypatch.setattr(
        chains,
        "semantic_inspect_symbol",
        lambda *_args, **_kwargs: {
            "references": [
                {"path": "/tmp/a.py", "line": 10, "column": 3, "end_line": 10, "end_column": 9},
                {"path": "/tmp/b.py", "line": 12, "column": 7, "end_line": 12, "end_column": 13},
            ]
        },
    )

    result = chains.impact("sample", root=".")

    assert result["reference_source"] == "lsp"
    assert result["total_references"] == 2
    assert sorted(result["files_affected"]) == ["/tmp/a.py", "/tmp/b.py"]
