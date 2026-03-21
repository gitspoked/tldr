# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

TLDREADME parses codebases via tree-sitter, embeds symbols into Qdrant, builds call/import/dependency graphs in FalkorDB, and serves the knowledge through an MCP server with 16 tools. Default LLM backend is local Ollama; optional LiteLLM proxy for cloud providers.

## Build & Run

```bash
# Install (editable, into venv)
python3.12 -m venv .venv && source .venv/bin/activate && pip install -e .

# Start infrastructure (Qdrant on :16333, FalkorDB on :16379)
docker compose up -d

# For cloud LLM instead of Ollama:
docker compose -f docker-compose.llm.yml up -d

# CLI command: `tldr`
tldr init /path/to/code      # full pipeline: parse -> embed -> graph -> generate
tldr serve                    # MCP server (stdio, not HTTP)
tldr watch /path/to/code     # incremental re-index on file saves
tldr ask "question"          # RAG-powered CLI answer
```


## Architecture

The pipeline flows: **parse -> embed -> graph -> generate**, orchestrated by `pipeline.py:run_init()`.

### Data Flow

```
Source files
  -> parser.py (tree-sitter AST -> Symbol, Import, CallSite dataclasses)
  -> embedder.py (LiteLLM embedding -> Qdrant collection "tldreadme_code")
  -> grapher.py (FalkorDB graph "tldreadme" with Symbol/File/Module/Import nodes)
  -> hot_index.py (top 100 symbols cached -> .tldr/hot_index.json)
  -> generator.py (LLM synthesis -> .claude/TLDR.md + TLDR_CONTEXT.md)
```

### Module Roles

- **parser.py** — The largest module. Extracts `Symbol`, `Import`, `CallSite`, `Dependency`, `ContextDoc` dataclasses. Handles 14 languages via tree-sitter. Also contains dependency extraction from manifest files (Cargo.toml, package.json, go.mod, pyproject.toml, requirements.txt) and context doc scanning (CLAUDE.md, README.md, etc.).
- **embedder.py** — `CodeEmbedder` class wraps Qdrant. `embed_batch()` for bulk, `embed_text()` for single queries. Collection auto-creates on first use with dimension auto-detection.
- **grapher.py** — `CodeGrapher` class wraps FalkorDB (Redis protocol). Graph schema: `(Module)-[:CONTAINS]->(File)-[:DEFINES]->(Symbol)`, `(Symbol)-[:CALLS]->(Symbol)`, `(File)-[:IMPORTS]->(Import)`. Query methods: `get_callers`, `get_callees`, `get_module_symbols`, `get_flow`, `get_dependents`.
- **chains.py** — Composed tool sequences: `know` (80% tool: hot_index -> rg -> graph), `impact` (15%: rg counts -> graph dependents -> severity), `discover` (5%: rg + semantic merge), `explain` (everything: know -> impact -> discover -> LLM synthesis).
- **search.py** — ripgrep subprocess wrapper. `rg_search` (matches with context), `rg_files` (file list), `rg_count` (per-file counts). All skip node_modules/target/dist/.git.
- **hot_index.py** — Pre-caches top 100 symbols ranked by importance heuristic (size, kind, visibility). Persists to `.tldr/hot_index.json`.
- **rag.py** — RAG engine combining Qdrant retrieval + FalkorDB graph context + LiteLLM synthesis. Also implements backwards flow: `suggest_goals`, `best_question`.
- **mcp_server.py** — Registers 16 MCP tools, routes calls to chains.py and rag.py. Runs as stdio server (not HTTP).
- **watcher.py** — watchdog-based file observer with 2-second debounce. Re-parses changed files and updates both Qdrant and FalkorDB incrementally.

### Key Design Decisions

- **LLM routing**: `embedder.py` defines `EMBED_MODEL`, `CHAT_MODEL`, `_api_base()`. If `LITELLM_URL` env var is set, routes through LiteLLM proxy; otherwise talks directly to Ollama at `OLLAMA_URL`.
- **Ports**: `docker-compose.yml` uses non-standard ports (16333 Qdrant, 16379 FalkorDB) to avoid conflicts. Code defaults match. `docker-compose.llm.yml` uses standard ports (6333, 6379) — override via `QDRANT_URL` / `FALKORDB_URL` env vars.
- **Singleton connections**: `_shared.py` provides `get_embedder()` / `get_grapher()` — one Qdrant/FalkorDB connection per process. All rag.py and chains.py functions use these instead of instantiating per call.
- **Deterministic Qdrant IDs**: `chunk_id()` hashes `file:name:line` into a stable integer ID. Re-indexing upserts in place instead of clobbering sequential IDs.
- **MCP server is stdio-only**: `mcp_server.py` uses `mcp.server.stdio.stdio_server`, not HTTP. Claude Code connects via stdin/stdout, configured with `command` + `args` in MCP config.
- **No tests exist yet.**

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_URL` | `http://localhost:11434` | Direct Ollama endpoint |
| `LITELLM_URL` | `""` (empty = use Ollama) | LiteLLM proxy URL |
| `TLDREADME_EMBED_MODEL` | `ollama/nomic-embed-text` | Embedding model |
| `TLDREADME_CHAT_MODEL` | `ollama/qwen2.5-coder:3b-instruct` | Chat/synthesis model |
| `QDRANT_URL` | `http://localhost:16333` | Qdrant vector DB |
| `FALKORDB_URL` | `redis://localhost:16379` | FalkorDB graph DB |

## Dependencies

Python 3.11+ (3.12 recommended). Key deps: `tree-sitter` 0.21.x + `tree-sitter-languages` 1.10.x (pinned — newer versions break), `litellm`, `qdrant-client`, `falkordb`, `redis`, `watchdog`, `mcp`, `click`, `rich`. Build system: hatchling.
