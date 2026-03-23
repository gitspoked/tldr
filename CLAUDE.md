# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

TLDREADME parses codebases via tree-sitter, embeds symbols into Qdrant, builds call/import/dependency graphs in FalkorDB, and serves the knowledge through an MCP server with 16 tools. Default LLM backend is local Ollama; optional LiteLLM proxy for cloud providers.

## Build & Run

```bash
# Install (editable, into venv)
python3.12 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'

# Start infrastructure (Qdrant on :6333, FalkorDB on :6379)
docker compose up -d

# For cloud LLM instead of Ollama:
docker compose -f docker-compose.llm.yml up -d

# CLI command: `tldr`
tldr init /path/to/code      # full pipeline: parse -> embed -> graph -> generate
tldr serve                    # MCP server (stdio)
tldr serve --transport sse -p 8900   # MCP server over SSE
tldr watch /path/to/code     # incremental re-index on file saves
tldr ask "question"          # RAG-powered CLI answer

# Tests
python3 -m pytest -q
```


## Architecture

The pipeline flows: **parse -> embed -> graph -> generate**, orchestrated by `pipeline.py:run_init()`.

### Data Flow

```
Source files
  -> asts.py (tree-sitter AST -> Symbol, Import, CallSite dataclasses)
  -> deps.py (manifest dependency extraction)
  -> context_docs.py (README/CLAUDE/AGENTS scanners)
  -> embedder.py (LiteLLM embedding -> Qdrant collection "tldreadme_code")
  -> grapher.py (FalkorDB graph "tldreadme" with Symbol/File/Module/Import nodes)
  -> hot_index.py (top 100 symbols cached -> .tldr/hot_index.json)
  -> generator.py (LLM synthesis -> .claude/TLDR.md + TLDR_CONTEXT.md)
```

### Module Roles

- **parser.py** — Compatibility facade. Re-exports AST parsing, dependency extraction, and context-doc scanning during the module split.
- **asts.py** — Tree-sitter AST extraction. Handles supported languages and produces `ParseResult`, `Symbol`, `Import`, and `CallSite`.
- **deps.py** — Manifest dependency extraction from Cargo.toml, package.json, go.mod, pyproject.toml, and requirements.txt.
- **context_docs.py** — Scans CLAUDE.md, README.md, AGENTS.md, and related project docs into structured sections.
- **embedder.py** — `CodeEmbedder` class wraps Qdrant. `embed_batch()` for bulk, `embed_text()` for single queries. Collection auto-creates on first use with dimension auto-detection.
- **grapher.py** — `CodeGrapher` class wraps FalkorDB (Redis protocol). Graph schema: `(Module)-[:CONTAINS]->(File)-[:DEFINES]->(Symbol)`, `(Symbol)-[:CALLS]->(Symbol)`, `(File)-[:IMPORTS]->(Import)`. Query methods: `get_callers`, `get_callees`, `get_module_symbols`, `get_flow`, `get_dependents`.
- **chains.py** — Composed tool sequences: `know` (80% tool: hot_index -> rg -> graph), `impact` (15%: rg counts -> graph dependents -> severity), `discover` (5%: rg + semantic merge), `explain` (everything: know -> impact -> discover -> LLM synthesis).
- **search.py** — ripgrep subprocess wrapper. `rg_search` (matches with context), `rg_files` (file list), `rg_count` (per-file counts). All skip node_modules/target/dist/.git.
- **hot_index.py** — Pre-caches top 100 symbols ranked by importance heuristic (size, kind, visibility). Persists to `.tldr/hot_index.json`.
- **rag.py** — RAG engine combining Qdrant retrieval + FalkorDB graph context + LiteLLM synthesis. Also implements grounded planning helpers: `suggest_goals`, `best_question`, `goal_flow`, and `auto_iterate`.
- **mcp_server.py** — Registers the MCP tool/resource/prompt surface, applies router/full profiles, and capability-filters tools at runtime. Supports stdio and SSE transports.
- **watcher.py** — watchdog-based file observer with 2-second debounce. Re-parses changed files and updates both Qdrant and FalkorDB incrementally.

### Key Design Decisions

- **LLM routing**: `embedder.py` defines `EMBED_MODEL`, `CHAT_MODEL`, `_api_base()`. If `LITELLM_URL` env var is set, routes through LiteLLM proxy; otherwise talks directly to Ollama at `OLLAMA_URL`.
- **Ports**: both compose files now use standard ports by default: Qdrant `6333` and FalkorDB `6379`.
- **Singleton connections**: `_shared.py` provides `get_embedder()` / `get_grapher()` — one Qdrant/FalkorDB connection per process. All rag.py and chains.py functions use these instead of instantiating per call.
- **Deterministic Qdrant IDs**: `chunk_id()` hashes `file:name:line` into a stable integer ID. Re-indexing upserts in place instead of clobbering sequential IDs.
- **MCP transports**: `mcp_server.py` supports both stdio and SSE. Claude Code uses stdio; remote clients can connect over SSE via `tldr serve --transport sse`.
- **Tests exist under `tests/`** and currently cover CLI entry points, parser behavior, search helpers, embedding chunk helpers, and the hot index.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_URL` | `http://localhost:11434` | Direct Ollama endpoint |
| `LITELLM_URL` | `""` (empty = use Ollama) | LiteLLM proxy URL |
| `TLDREADME_EMBED_MODEL` | `ollama/nomic-embed-text` | Embedding model |
| `TLDREADME_CHAT_MODEL` | `ollama/qwen2.5-coder:3b-instruct` | Chat/synthesis model |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant vector DB |
| `FALKORDB_URL` | `redis://localhost:6379` | FalkorDB graph DB |

## Dependencies

Python 3.11+ (3.12 recommended). Key deps: `tree-sitter` 0.21.x + `tree-sitter-languages` 1.10.x (pinned — newer versions break), `litellm`, `qdrant-client`, `falkordb`, `redis`, `watchdog`, `mcp`, `click`, `rich`. Build system: hatchling. Install test tooling with `pip install -e '.[dev]'`.
