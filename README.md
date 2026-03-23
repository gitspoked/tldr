# 
# TLDREADME.md
#
**TL;DR for any codebase. Privacy-first, local-first.**

Point it at a directory. It parses every function, embeds it, graphs the relationships, and serves it all via MCP. Your LLM gets full codebase context before you ask your first question.

**Your code never leaves your machine.** Ollama for inference, Qdrant and FalkorDB in local Docker containers. No API keys required. No code uploaded anywhere. Cloud providers are opt-in via LiteLLM when you want them.

```
tldr init /path/to/your/code    # parse, embed, graph, generate TLDR.md
tldr serve                       # MCP server over stdio for Claude Code
tldr serve --transport sse -p 8900  # network-accessible SSE transport
tldr watch /path/to/your/code   # stay current on file saves
tldr ask "how does X work?"     # RAG-powered answer from CLI
tldr doctor --diagnostics path/to/file.py --line 42  # human-facing diagnostics report
tldr summary                    # what changed since the last summary checkpoint
tldr plans-capture .            # paste notes until Ctrl-D, save a TLDRPLANS.<timestamp>.md drop
tldr whats-next-vibe .          # show the next strategic question and grounded options
tldr current-vibe-roadmap .     # refresh TLDRPLANS.md and write TLDROADMAP.md
```

## What It Does

```
Your Code
  │
  ├── tree-sitter ──── AST: every function, class, struct, import, call site
  ├── Qdrant ──────── vector embeddings for semantic search
  ├── FalkorDB ────── call graph, import graph, dependency graph
  ├── LiteLLM ─────── RAG synthesis (default Ollama local, or OpenAI/Anthropic/OpenRouter)
  ├── ripgrep ─────── fast text search with context
  └── MCP Server ──── tools, resources, and prompts that make LLMs understand your code
```

## The Tools

### The 80% — Just Show Me The Code

| Tool | What |
|------|------|
| `know` | Everything about a symbol: definition, usages, callers, callees. One call. Start here. |
| `read_grep` | Fast text search via rg. Exact strings, regex, error messages. |
| `read_grep_files` | Which files contain this pattern? |
| `read_semantic` | Hover, definition, references, and document symbols from the installed language server. |
| `read_workspace_symbols` | Semantic symbol search through the installed language server. |

### Router-Preferred Default Surface

| Tool | What |
|------|------|
| `repo_next_action` | Resume work safely. Looks at sessions, overlaps, workboard state, and imported child trees, then recommends the next top-level tool. |
| `repo_lookup` | Single read entry point. Internally chooses broad scan, federated search, symbol knowledge, impact lookup, or exact edit context. |
| `change_plan` | Turns a coding goal into candidate files, risks, acceptance criteria, and ordered verification steps. |
| `verify_change` | Checks workboard evidence and inferred verification commands, then reports pass/fail status and missing proof. |

Every router-preferred tool returns the same high-signal fields: `summary`, `confidence`, `evidence`, `recommended_next_action`, `verification_commands`, and `fallback_used`.
Use `repo_next_action` when resuming interrupted work, `repo_lookup` to understand the repo or a symbol, `change_plan` before editing, and `verify_change` before calling a task done.

### Specialist Lookup Tools (`--tool-profile full`)

| Tool | What |
|------|------|
| `scan_context` | Snapshot the repo surfaces available right now: code, tests, docs, generated TLDR files, workboard state, and recent changes. |
| `search_context` | Search across those surfaces in one call and return ranked context hits with the next best follow-up tool. |
| `edit_context` | Best first call before an edit. Returns the local snippet, enclosing symbol, semantic info, similar code, matching tasks, and tests. |
| `test_map` | Finds the nearest likely tests and exact verification commands for a file or symbol. |
| `pattern_search` | Finds reusable implementations so you can copy the local pattern instead of inventing a new one. |
| `diagnostics_here` | Pulls LSP diagnostics for a file or exact position, including likely fix area and impacted symbols. |
| `know` | Fast symbol knowledge: definition, usages, callers, and callees. |
| `impact` | Severity rating + affected files + transitive dependents. Run before modifying. |

### Deeper Graph Reads

| Tool | What |
|------|------|
| `read_depends` | Full dependency chain from the graph. |
| `read_flow` | Trace execution from entry point through call chain. |

### The 5% — I Need To Think

| Tool | What |
|------|------|
| `discover` | Find code by concept, not name. rg + semantic search merged. |
| `read_similar` | Actual source code of similar implementations. See the pattern. |
| `explain` | Full LLM-powered explanation: what it does, what depends on it, what to watch out for. |
| `tldr` | RAG-powered summary of any module or directory. |

### Backwards Flow — The Code Tells You What To Do

| Tool | What |
|------|------|
| `suggest_goals` | Uses active plans, repo state, and concrete feature gaps to rank grounded next steps. |
| `best_question` | Given a goal, turns it into the next concrete engineering question with file, symbol, risk, and verification hints. |
| `goal_flow` | Cold-start planning chain: grounded goals → top goal → next best engineering question. |
| `auto_iterate` | Walks a few ranked candidate goals in sequence without inventing new ones from thin context. |

## Setup

### Prerequisites

- Python 3.11+ (3.12 recommended)
- Docker (for Qdrant, FalkorDB, Ollama, LiteLLM)
- ripgrep (`brew install ripgrep`)

### Install

```bash
git clone https://github.com/ntele-dev/tldreadme.git
cd tldreadme
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/tldr doctor
.venv/bin/tldr doctor --fix
```

`tldr doctor` checks the pinned Python/tree-sitter/ripgrep runtime, reports local service reachability, and shows which common LSP servers are available on `PATH`. Add `--fix` for an interactive checkbox prompt with install/start commands for anything missing.

For human-facing code health, add `--diagnostics path/to/file.py` to `tldr doctor` to print LSP diagnostics, likely fix area, impacted symbols, and the first verification command worth running.

`tldr summary` prints commits, working tree changes, workboard updates, and session notes since the last local summary checkpoint, then advances that checkpoint unless you pass `--no-mark-checked`.

`tldr plans-capture` reads freeform notes, links, example repos, and pasted context from stdin until Ctrl-D, stores the raw drop as `TLDRPLANS.<timestamp>.md`, and refreshes the consolidated `TLDRPLANS.md` digest. Then `tldr whats-next-vibe` turns README intent, captured notes, workboard state, and grounded planning signals into the next strategic question to ask. `tldr current-vibe-roadmap` writes `TLDROADMAP.md` as the current human-facing roadmap snapshot.

Raw `lsp` and `lsp-symbols` CLI commands still exist for internal debugging, but they are intentionally hidden from the normal human-facing command surface.

For bedrock contract checks, run `.venv/bin/python -m pytest -m bedrock -q`. The pytest summary prints a GO/NO-GO bedrock gate report with the covered use case, similar use cases, and reliance weight for each critical contract test.

### Start Infrastructure

```bash
docker compose up -d

# Ollama runs natively (not in Docker) — pull models locally:
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:3b-instruct
```

### Index a Codebase

```bash
tldr init /path/to/your/project
```

This will:

1. Parse all code via tree-sitter (TypeScript, JavaScript, Python, Rust + 8 more)
2. Extract dependencies from Cargo.toml, package.json, go.mod, pyproject.toml
3. Embed symbols into Qdrant
4. Build call/import/dependency graph in FalkorDB
5. Cache top 100 symbols in a hot index
6. Generate `.claude/TLDR.md` and `.claude/TLDR_CONTEXT.md`

### Connect to Claude Code

Add to your Claude Code MCP config:

```json
{
  "mcpServers": {
    "tldreadme": {
      "command": "/path/to/tldreadme/.venv/bin/python3.12",
      "args": ["-m", "tldreadme.mcp_server"]
    }
  }
}
```

## LLM Backend

Default: local Ollama (`qwen2.5-coder:3b-instruct` for synthesis, `nomic-embed-text` for embeddings).

We advise larger parameter models if possible for local code inference. 

Qwen Examples:
    Model    		Size(Q4)   RAM needed
   qwen2.5-coder:3b     1.9G         ~4GB     
   qwen2.5-coder:7b     4.7G         ~8GB     
   qwen2.5-coder:14b    9GB         ~12GB     
   qwen2.5-coder:32b    20GB        ~24GB     
   qwen3-coder-next     46GB        ~48GB     

32B if you can, 7b works fine for code intel. 


To use cloud providers, copy `.env.example` to `.env`, set `LITELLM_URL`, switch `QDRANT_URL` / `FALKORDB_URL` to the standard ports used by `docker-compose.llm.yml`, and uncomment your provider in `litellm-config.yaml`:

```bash
cp .env.example .env

# in .env
LITELLM_URL=http://localhost:4000
QDRANT_URL=http://localhost:6333
FALKORDB_URL=redis://localhost:6379
ANTHROPIC_API_KEY=sk-ant-...
# or OPENAI_API_KEY / OPENROUTER_API_KEY
```

Config only. No code change.

## Languages

**Primary:** TypeScript, JavaScript, Python, Rust

**Secondary:** Go, C, C++, PHP, Java, Ruby, Swift, Kotlin, Lua, Zig

**Dependencies extracted from:** Cargo.toml, package.json, go.mod, pyproject.toml, requirements.txt

**Not scanned:** node_modules, target, .venv, dist. Dependencies are cataloged from manifests, not source-parsed.

## Architecture

```
tldreadme/
├── cli.py          # init | watch | serve | ask | summary | roadmap | children
├── parser.py       # compatibility facade for ASTs, deps, and docs
├── asts.py         # tree-sitter AST extraction
├── deps.py         # manifest dependency extraction
├── context_docs.py # README/CLAUDE/AGENTS scanner
├── embedder.py     # LiteLLM → Qdrant vectors
├── grapher.py      # FalkorDB call/import/dependency graph
├── lsp.py          # lightweight LSP client + semantic query helpers
├── search.py       # ripgrep wrapper (rg_search, rg_files, rg_count)
├── hot_index.py    # pre-cached top 100 symbols for instant lookup
├── rag.py          # RAG engine + grounded planning helpers
├── roadmap.py      # TLDRPLANS capture + TLDROADMAP generation
├── chains.py       # daisy-chained tool sequences (know, impact, discover, explain)
├── generator.py    # TLDR.md generator from indexed knowledge
├── watcher.py      # fswatch incremental re-indexing
├── pipeline.py     # orchestrates: parse → embed → graph → ge
└── mcp_server.py   # MCP tools, resources, and prompts
```

## Philosophy

- **Intelligence in, intelligence out.** The quality of TLDR.md determines how smart the LLM is about your code.
- **Search before writing.** `read_similar` and `discover` exist so you don't reinvent what already exists.
- **Backwards-first.** Let current repo state, active plans, and concrete feature gaps drive planning before freeform ideation.
- **Start fast, go deeper only when needed.** `know` (instant) before `explain` (LLM). `impact` (fast) before refactoring.
- **Scan your code, catalog your deps, fetch docs on demand.** Never parse node_modules or libraries when scanning.

## MCP Context

Beyond tools, TLDREADME now exposes MCP resources and prompts for stable context reads:

- Static resources: `repo://overview`, `repo://health`, `repo://tooling`, `repo://children`
- Dynamic resources: `repo://module/{path}`, `repo://symbol/{name}`, `repo://semantic/{path}?line=...`, `repo://workspace-symbols/{query}?path=...`
- Prompts: `impact-review`, `module-brief`, `semantic-investigation`
- Router-preferred tools: `repo_next_action`, `repo_lookup`, `change_plan`, `verify_change`
- Specialist lookup tools in `full`: `scan_context`, `search_context`, `edit_context`, `test_map`, `pattern_search`, `diagnostics_here`, `know`, `impact`

`tldr serve` now defaults to `--tool-profile router`, which exposes a four-intent MCP surface for agent routers: resume, lookup, plan, and verify. Tool exposure is also capability-enforced: tools that require missing backends such as LSP, Qdrant, or FalkorDB are suppressed until those capabilities are available. Use `tldr serve --tool-profile full` when you want the complete debugging and specialist surface.

## Foundation Contract

Treat the router-default surface as bedrock:

- `repo_next_action` resumes or coordinates work
- `repo_lookup` handles repo orientation, symbol lookup, impact lookup, and edit-time context
- `change_plan` turns goals into executable edits
- `verify_change` closes work with evidence and verification

New agent-facing behavior should extend one of those four tools or stay in the `full` specialist profile. Workboard plans, sessions, and child-project registries are versioned file-backed documents under `.tldr/work/`, and `parser.py` remains the compatibility facade over the split parser modules.

Human trust hierarchy:

- bedrock context docs: `README.md`, `AGENTS.md`, `CLAUDE.md`, `TLDRNOTES.md`
- planning context: `TLDRPLANS.md`, `TLDRPLANS.*.md`, `TLDROADMAP.md`
- generated context: `.claude/TLDR.md`, `.claude/TLDR_CONTEXT.md`
- operational state: `.tldr/work/*`
- source of truth remains the code, tests, and manifests

## Workboard

TLDREADME now includes a file-backed workboard for phased execution planning:

- Canonical plan files: `.tldr/work/plans/*.yaml`
- Canonical live sessions: `.tldr/work/sessions/current.<session_id>.yaml`
- MCP tools: `plan_create`, `plan_update`, `plan_list`, `plan_current`, `plan_archive`, `task_add`, `task_update`, `task_complete`, `session_note`, `session_update`
- MCP resources: `repo://plans`, `repo://session/current`, `repo://plan/{id}`, `repo://task/{plan_id}/{task_id}`
- MCP prompts: `resume-session`, `phase-review`, `done-check`

Each task supports acceptance criteria, verification commands, blockers, evidence, and next-step notes. Sessions keep only the low-noise state needed to resume and avoid overlap: current focus, next action, claimed files/symbols, blockers, and recent steps.

## Child Projects

Imported nested subprojects are treated as part of the repo by default. TLDREADME can still surface them so humans can acknowledge intent instead of silently blending them in:

- Detection file: `.tldr/work/children.yaml`
- Human CLI: `tldr children list`, `tldr children merge path/to/child`, `tldr children ignore path/to/child`
- `tldr summary` highlights newly detected `unknown` children such as imported repos or copied-in modules

## License: MIT
