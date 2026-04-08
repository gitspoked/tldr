# Peek: Zero-Init Codebase Reconnaissance

**Date:** 2026-04-08
**Status:** Approved

## Problem

`tldr <path>` currently falls through to `init`, which requires Qdrant, FalkorDB, and Ollama. Users want a fast "WTF is in here" command that works immediately on any directory or file with zero infrastructure.

Additionally, `tldr ask -d <dir>` accepts a directory scope but ignores it in the implementation.

## Design

### New module: `peek.py`

Single entry point: `peek_target(path: str | Path) -> dict`

Handles both files and directories. Detects target type and builds a result dict through layered enrichment — each layer adds depth but none is required.

### Layered Enrichment

**Layer 0 — Filesystem scan (always runs):**
- File vs directory detection
- For directories: file count by extension, total lines, manifest detection (project type/name/version) via `deps.extract_deps_from_directory()`
- For files: language detection by extension, line count, symbol extraction (tree-sitter parse if available, regex fallback for function/class definitions)

**Layer 1 — Context docs (always runs):**
- `context_docs.scan_context_docs()` from the target or its project root
- Surfaces README.md, CLAUDE.md, AGENTS.md, CODEX.md, GEMINI.md, etc. with their sections
- For files: also shows where the file fits in the project structure relative to the project root

**Layer 2 — Indexed knowledge (if `.tldr/` exists):**
- Hot index lookup from `.tldr/hot_index.json` — is this file/directory represented in the top 100 symbols?
- Read `.claude/TLDR.md` if present — already-generated summary
- Check `.tldr/work/` for any plans

**Layer 3 — Live enrichment (if services respond):**
- Quick Qdrant ping (1s timeout) — if up, semantic search for related symbols
- Quick FalkorDB ping (1s timeout) — if up, graph neighbors (callers/callees/imports)
- Graceful skip on timeout or connection refused — no crash, just note in `fallback_used`

### Return Shape

```python
{
    "path": str,                    # absolute path to target
    "type": "file" | "directory",
    "project": {                    # from nearest manifest, or None
        "name": str,
        "version": str,
        "manifest": str,            # e.g. "pyproject.toml"
    } | None,
    "stats": {
        "files": int,               # 1 for file mode
        "lines": int,
        "extensions": dict[str, int],  # {"py": 32, "md": 6, ...}
    },
    "context_docs": [               # from scan_context_docs
        {"kind": str, "title": str, "summary": str}
    ],
    "symbols": [                    # file mode: what the file defines
        {"name": str, "kind": str, "line": int}
    ],
    "indexed": bool,                # .tldr/ exists
    "generated_summary": str | None,  # from .claude/TLDR.md if present
    "hot_symbols": [                # layer 2: from hot_index.json
        {"name": str, "kind": str, "file": str}
    ],
    "related": [                    # layer 3: from live services
        {"name": str, "relationship": str, "file": str}
    ],
    "enrichment_layers": list[str], # ["disk", "context_docs", "tldr", "qdrant", "falkordb"]
    "fallback_used": list[str],     # what was skipped and why
}
```

## CLI Integration

### Default behavior change

```
tldr <path>          -> peek (fast, no infra required)
tldr init <path>     -> full indexing pipeline (unchanged)
tldr peek <path>     -> explicit peek (same as bare path)
```

The `main()` group handler in `cli.py` (lines 20-33) changes from `ctx.invoke(init, ...)` to `ctx.invoke(peek, ...)`.

### Command definition

```python
@main.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--json-output", is_flag=True)
@click.option("--markdown", is_flag=True)
def peek(path: str, json_output: bool, markdown: bool):
    """Quick reconnaissance of a directory or file — no indexing required."""
```

**Flags:**
- Default: rich terminal output with sections and color
- `--json-output`: raw dict as JSON
- `--markdown`: markdown rendering suitable for embedding or piping

### No breaking changes

`tldr init <path>` continues working exactly as before. Only the bare-path-no-subcommand fallback changes.

## Router / MCP Integration

`repo_lookup` in `coding_tools.py` gains peek awareness. When the lookup target isn't in the indexed collection or refers to a path not yet init'd, it falls through to `peek_target()` instead of returning an error or empty result.

No new MCP tools. No new resources. `repo_lookup` gets smarter about unindexed targets.

The return dict follows the existing router contract: `summary`, `confidence`, `evidence`, `recommended_next_action`, `fallback_used` keys are present or derivable.

## Rendering

### Rich terminal (default)

```
myproject (Python 3.12 . pyproject.toml)
   v0.4.0 . 47 files . 3,812 lines
   py: 32  md: 6  yaml: 4  toml: 2  json: 3

-- What It Is -----------------------------------------------
README.md: "A local-first codebase indexer that parses
code via tree-sitter and serves knowledge over MCP."

-- Context Docs ---------------------------------------------
CLAUDE.md    architecture, CLI, MCP surface, design decisions
AGENTS.md    build/test/dev commands, coding style
README.md    project overview, installation, usage

-- Indexed? -------------------------------------------------
.tldr/ exists . hot index: 87 symbols
.claude/TLDR.md generated
Qdrant unavailable . FalkorDB unavailable
```

### Rich terminal — file mode

```
tldreadme/peek.py (Python . 142 lines)
   Part of: tldreadme v0.4.0

-- Defines --------------------------------------------------
function  peek_target        line 18
function  _scan_directory     line 45
function  _scan_file          line 78
function  _render_peek        line 110

-- Related (graph) ------------------------------------------
called by: repo_lookup (coding_tools.py)
imports:   context_docs, deps, asts
```

### JSON

Raw dict from `peek_target()`, no transformation.

### Markdown

Same sections as rich output rendered as markdown headings, tables, and bullet lists.

## Module Dependencies

`peek.py` imports only lightweight local modules at the top level:

- `context_docs.scan_context_docs` (always)
- `deps.extract_deps_from_directory` (always, for manifest detection)
- `pathlib`, `os`, `json` (stdlib)

Heavy imports are deferred behind try/except or conditional checks:

- `asts.parse_file` — only for file mode symbol extraction, with regex fallback
- `embedder.CodeEmbedder` — only in layer 3, inside try/except
- `grapher.CodeGrapher` — only in layer 3, inside try/except
- `hot_index.HotIndex` — only in layer 2, reading a JSON file

No top-level dependency on Qdrant, FalkorDB, Ollama, or LiteLLM.

## Testing

`tests/test_peek.py` covering:

- Directory mode with a temp dir containing known files, manifests, and context docs
- File mode with a single Python file containing functions/classes
- Layered degradation: no `.tldr/`, no services — verifies layers 0-1 work standalone
- Manifest detection: pyproject.toml, package.json, Cargo.toml
- Context doc scanning: README.md, CLAUDE.md present vs absent
- Return shape validation: all expected keys present, correct types

No mocking of external services. Tests run fully offline against temp directories.

## Scope Boundaries

**In scope:**
- `peek.py` module
- CLI `peek` command + default path fallback change
- `repo_lookup` peek fallback in `coding_tools.py`
- `tests/test_peek.py`
- CLAUDE.md CLI section update

**Out of scope:**
- Fixing `tldr ask -d` scope filtering (separate issue, noted)
- New MCP resources or tools
- Changes to the init pipeline
- Changes to existing rendering in other commands
