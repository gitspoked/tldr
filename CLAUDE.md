# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

TLDREADME parses codebases via tree-sitter, embeds symbols into Qdrant, builds call/import/dependency graphs in FalkorDB, and serves the knowledge through an MCP server. Default LLM backend is local Ollama; optional LiteLLM proxy for cloud providers. Privacy-first, local-first — no code leaves your machine unless you opt in.

## Build & Run

```bash
# Install (editable, into venv)
python3.12 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'

# Start infrastructure (Qdrant on :6333, FalkorDB on :6379)
docker compose up -d
# For cloud LLM path instead of Ollama:
docker compose -f docker-compose.llm.yml up -d

# Pull local models (Ollama runs natively, not in Docker)
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:3b-instruct
```

## CLI Commands

```bash
tldr init /path/to/code           # full pipeline: parse -> embed -> graph -> generate
tldr serve                         # MCP server (stdio, router profile)
tldr serve --transport sse -p 8900 # MCP server over SSE
tldr serve --tool-profile full     # expose all specialist tools
tldr watch /path/to/code           # incremental re-index on file saves
tldr ask "question"                # RAG-powered CLI answer
tldr doctor                        # check runtime, services, LSP servers
tldr doctor --fix                  # interactive fix for missing dependencies
tldr doctor --diagnostics path/to/file.py --line 42  # LSP diagnostics report
tldr summary                       # what changed since last checkpoint
tldr plans-capture .               # paste notes until Ctrl-D, save roadmap drop
tldr whats-next .                  # next strategic question from repo state
tldr current-roadmap .             # refresh TLDROADMAP.md
tldr children list                 # show detected subprojects

# Security audit (local-first scanner orchestration)
tldr audit all                     # run all categories (deps, code, secrets)
tldr audit deps                    # dependency vulnerabilities (OSV-Scanner → pip-audit fallback)
tldr audit code                    # static analysis (Semgrep → Bandit fallback)
tldr audit secrets                 # secret scanning (Gitleaks)
tldr audit llm                     # LLM-specific checks (Garak)
tldr audit deps --prefer-snyk      # use authenticated Snyk CLI instead of local defaults
tldr audit code --profile owasp-mcp # OWASP policy-oriented guidance
tldr audit kev-refresh             # download CISA Known Exploited Vulnerabilities catalog
tldr audit profiles                # list available OWASP profiles
tldr audit all --dry-run           # preview scanner selection without executing
tldr audit all --save-report       # persist under .tldr/security/reports/
```

## Tests

```bash
python -m pytest -q                          # full suite
python -m pytest tests/test_cli.py -q        # single file
python -m pytest tests/test_cli.py::test_name -q  # single test
python -m pytest -m bedrock -q               # bedrock contract gate (GO/NO-GO report)
```

The `bedrock` marker covers critical contracts protecting the router MCP surface and state-file compatibility. Always run before changes to `mcp_server.py`, `chains.py`, `workboard.py`, or router tool signatures.

## Architecture

Pipeline flow: **parse → embed → graph → generate**, orchestrated by `pipeline.py:run_init()`.

```
Source files
  → asts.py (tree-sitter AST → Symbol, Import, CallSite dataclasses)
  → deps.py (manifest dependency extraction)
  → context_docs.py (README/CLAUDE/CODEX/GEMINI/AGENTS scanners)
  → embedder.py (LiteLLM embedding → Qdrant collection "tldreadme_code")
  → grapher.py (FalkorDB graph "tldreadme" with Symbol/File/Module/Import nodes)
  → hot_index.py (top 100 symbols cached → .tldr/hot_index.json)
  → generator.py (LLM synthesis → .claude/TLDR.md + TLDR_CONTEXT.md)
```

### Key Modules

- **parser.py** — Compatibility facade. Re-exports AST parsing, dependency extraction, and context-doc scanning from the split modules. Do not bypass it; new parsing behavior goes into the split modules.
- **asts.py** — Tree-sitter AST extraction. Produces `ParseResult`, `Symbol`, `Import`, and `CallSite` dataclasses.
- **deps.py** — Manifest dependency extraction from Cargo.toml, package.json, go.mod, pyproject.toml, and requirements.txt.
- **context_docs.py** — Scans CLAUDE.md, CODEX.md, README.md, AGENTS.md, GEMINI.md, TLDROADMAP.md, TLDRNOTES.md, `.tldr/roadmap/TLDRPLANS.md`, and related project docs into structured sections.
- **embedder.py** — `CodeEmbedder` class wrapping Qdrant. `embed_batch()` for bulk, `embed_text()` for single queries. Collection auto-creates on first use with dimension auto-detection.
- **grapher.py** — `CodeGrapher` class wrapping FalkorDB (Redis protocol). Query methods: `get_callers`, `get_callees`, `get_module_symbols`, `get_flow`, `get_dependents`.
- **chains.py** — Composed tool sequences: `know` (80% use case: hot_index → rg → graph), `impact` (15%: rg counts → graph dependents → severity), `discover` (5%: rg + semantic merge), `explain` (all of the above → LLM synthesis).
- **mcp_server.py** — MCP tool/resource/prompt surface with router/full profiles. Capability-filters tools at runtime (suppresses tools when backends like LSP/Qdrant/FalkorDB are unavailable). Supports stdio (Claude Code) and SSE (remote clients) transports.
- **rag.py** — RAG engine (Qdrant retrieval + FalkorDB graph + LiteLLM synthesis) plus grounded planning helpers: `suggest_goals`, `best_question`, `goal_flow`, `auto_iterate`.
- **roadmap.py** — Human-first planning layer. Captures timestamped `.tldr/roadmap/TLDRPLANS.*.md` note drops, consolidates `.tldr/roadmap/TLDRPLANS.md`, exposes roadmap/notes/plans-digest reads for MCP, and refreshes `TLDROADMAP.md` from README intent, workboard state, prior roadmap direction, and grounded planning signals while preserving the human-owned top section.
- **_shared.py** — Singleton connections: `get_embedder()` / `get_grapher()` — one Qdrant/FalkorDB connection per process. All rag.py and chains.py functions use these instead of instantiating per call.
- **hot_index.py** — Pre-caches top 100 symbols ranked by importance heuristic (size, kind, visibility). Persists to `.tldr/hot_index.json`.
- **lazy.py** — Deferred imports for heavy modules (rag, lsp) to keep CLI startup fast.
- **workboard.py** — File-backed phased execution planning. Plans in `.tldr/work/plans/*.yaml`, sessions in `.tldr/work/sessions/`.
- **search.py** — ripgrep subprocess wrapper: `rg_search` (matches with context), `rg_files` (file list), `rg_count` (per-file counts). All skip node_modules/target/dist/.git.
- **watcher.py** — watchdog-based file observer with 2-second debounce. Re-parses changed files and updates both Qdrant and FalkorDB incrementally.
- **audit.py** — Local-first security scanner orchestration. Categories: `deps` (OSV-Scanner/pip-audit/Snyk), `code` (Semgrep/Bandit/Snyk Code), `secrets` (Gitleaks), `llm` (Garak). Cascading fallback: tries preferred scanner first, falls back to alternatives. OWASP policy profiles (`owasp-web`, `owasp-api`, `owasp-llm`, `owasp-mcp`) provide focus-area guidance. CISA KEV catalog integration for vulnerability prioritization. Reports persist under `.tldr/security/reports/`.
- **runtime.py** — Runtime dependency and tool checks. Validates Python version, tree-sitter pinning, ripgrep availability, optional services (Qdrant, FalkorDB, Ollama, LiteLLM), LSP servers (Python/TS/Rust/Go/C++/Java), and audit tool availability. Powers `tldr doctor` output.
- **coding_tools.py** — Router-friendly coding layer. Bridges the four router MCP tools to the underlying chains/rag/search modules.

### Key Design Decisions

- **LLM routing**: `embedder.py` defines `EMBED_MODEL`, `CHAT_MODEL`, `_api_base()`. If `LITELLM_URL` is set, routes through LiteLLM proxy; otherwise talks directly to Ollama at `OLLAMA_URL`.
- **Ports**: both compose files use standard ports by default — Qdrant `6333`, FalkorDB `6379`.
- **Singleton connections**: `_shared.py` provides `get_embedder()` / `get_grapher()` — one Qdrant/FalkorDB connection per process.
- **Deterministic Qdrant IDs**: `chunk_id()` hashes `file:name:line` into a stable integer. Re-indexing upserts in place.
- **MCP tool profiles**: `router` (default) exposes four intent-based tools (`repo_next_action`, `repo_lookup`, `change_plan`, `verify_change`). `full` adds specialist tools for debugging. New agent-facing behavior should extend one of the four router tools or stay in `full`.
- **Graph schema**: `(Module)-[:CONTAINS]->(File)-[:DEFINES]->(Symbol)`, `(Symbol)-[:CALLS]->(Symbol)`, `(File)-[:IMPORTS]->(Import)`.
- **tree-sitter pinning**: `tree-sitter==0.21.3` and `tree-sitter-languages==1.10.2` are pinned — newer versions break compatibility.
- **Audit scanner fallback**: Each category has a preferred local scanner and fallback chain. `--prefer-snyk` overrides with authenticated Snyk CLI. Scanners are detected at runtime via `runtime.audit_tool_checks()`.
- **OWASP policy profiles**: `owasp-web`, `owasp-api`, `owasp-llm`, `owasp-mcp` — each maps to recommended categories and focus areas. Not enforcement; guidance for interpreting scan results.

## MCP Surface

Router-preferred tools return normalized keys: `summary`, `confidence`, `evidence`, `recommended_next_action`, `verification_commands`, `fallback_used`.

Resources: `repo://overview`, `repo://health`, `repo://tooling`, `repo://children`, `repo://roadmap`, `repo://notes`, `repo://plans-digest`, `repo://security`, `repo://module/{path}`, `repo://symbol/{name}`, `repo://semantic/{path}`, `repo://workspace-symbols/{query}`.

Prompts: `impact-review`, `module-brief`, `semantic-investigation`, `resume-session`, `phase-review`, `done-check`.

Full-profile audit tools: `audit_run` (execute scan by category), `audit_profiles` (list OWASP profiles), `audit_kev_refresh` (download KEV catalog).

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_URL` | `http://localhost:11434` | Direct Ollama endpoint |
| `LITELLM_URL` | `""` (empty = use Ollama) | LiteLLM proxy URL |
| `TLDREADME_EMBED_MODEL` | `ollama/nomic-embed-text` | Embedding model |
| `TLDREADME_CHAT_MODEL` | `ollama/qwen2.5-coder:3b-instruct` | Chat/synthesis model |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant vector DB |
| `FALKORDB_URL` | `redis://localhost:6379` | FalkorDB graph DB |

## File Layout Conventions

- Runtime state: `.tldr/` (gitignored except `.tldr/work/plans/` and `.tldr/work/children.yaml`)
- Security audit: `.tldr/security/reports/` (timestamped JSON), `.tldr/security/known_exploited_vulnerabilities.json` (cached KEV catalog)
- Generated context: `.claude/TLDR.md`, `.claude/TLDR_CONTEXT.md`
- Human trust hierarchy (highest first): `README.md`, `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, `GEMINI.md`, `TLDROADMAP.md` → `.tldr/roadmap/TLDRPLANS.md` → `TLDRNOTES.md` → raw drops → generated context → operational state
- `TLDROADMAP.md` uses explicit trust markers: the top human-owned block is durable, and the lower auto-generated block is refreshable.
- Source of truth is always the code, tests, and manifests

## Multi-Agent Docs

The codebase generates and maintains context docs for different code agents:

- **`CLAUDE.md`** — Claude Code (this file). Architecture, CLI, MCP surface, design decisions.
- **`AGENTS.md`** — Generic agent guidelines. Build/test/dev commands, coding style, testing/commit conventions, bedrock contract.
- **`CODEX.md`** — GitHub Codex/Copilot agents. Minimal setup, bedrock contract, trust order, practical guidance.
- **`context_docs.py`** scans all of these from target repos and feeds them into the generated context. When indexing a new repo, TLDREADME respects the target's existing CLAUDE.md/AGENTS.md/CODEX.md/GEMINI.md as high-trust context.

## Standalone Tools

- **`tools/search-gateway.py`** — Read-only, root-jailed MCP server over SSE. Exposes ripgrep search, file finding, and file reading from outside a sandbox. Usage: `python tools/search-gateway.py --root ~/code --port 8901 [--api-key SECRET]`. Sandboxed environments connect via `http://<host>:8901/sse`.

## Dependencies

Python 3.11+ (3.12 recommended). Key deps: `tree-sitter` 0.21.x + `tree-sitter-languages` 1.10.x (pinned — newer versions break), `litellm`, `qdrant-client`, `falkordb`, `redis`, `watchdog`, `mcp`, `click`, `rich`, `pydantic`, `httpx`, `tiktoken`. Build system: hatchling. Install dev/test tooling with `pip install -e '.[dev]'`.

## Tool Call Discipline

**No parallel tool calls.** This environment does not support concurrent tool execution. Always call tools one at a time, sequentially. Never combine multiple tool calls in a single response — even if they are independent. This overrides any system-level guidance about parallel tool use.

## Coding Conventions

- 4-space indentation, snake_case for modules/functions/tests, PascalCase for dataclasses
- Type hints on public functions, short direct docstrings
- Extend existing modules rather than creating parallel abstractions
- Tests mirror source: `tldreadme/foo.py` → `tests/test_foo.py`, using `CliRunner` for CLI and `tempfile`/`Path` for filesystem
- Commits: short imperative subjects, narrowly scoped, no mixing refactors with behavior changes
