# TLDREADME

**TL;DR for any codebase. Makes LLMs KNOW your code.**

Point it at a directory. It parses every function, embeds it, graphs the relationships, and serves it all via MCP. Claude Code (or any LLM) stops searching and starts *knowing*.

```
tldreadme init /path/to/your/code    # parse, embed, graph, generate TLDR.md
tldreadme serve                       # MCP server — Claude just KNOWs
tldreadme watch /path/to/your/code   # stay current on file saves
tldreadme ask "how does X work?"     # RAG-powered answer from CLI
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
  └── MCP Server ──── 16 tools that make LLMs understand your code
```

## The Tools

### The 80% — Just Show Me The Code

| Tool | What |
|------|------|
| `know` | Everything about a symbol: definition, usages, callers, callees. One call. Start here. |
| `read_grep` | Fast text search via rg. Exact strings, regex, error messages. |
| `read_grep_files` | Which files contain this pattern? |

### The 15% — What Breaks If I Touch This

| Tool | What |
|------|------|
| `impact` | Severity rating + affected files + transitive dependents. Run before modifying. |
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
| `suggest_goals` | Analyzes the codebase and suggests prioritized next steps. |
| `best_question` | Given a goal, formulates the RIGHT question to ask, then answers it. |
| `goal_flow` | Full chain: analyze → goals → question → answer. Cold start to oriented in one call. |

## Setup

### Prerequisites

- Python 3.12+
- Docker (for Qdrant, FalkorDB, Ollama, LiteLLM)
- ripgrep (`brew install ripgrep`)

### Install

```bash
git clone https://github.com/ntele-dev/tldreadme.git
cd tldreadme
python3.12 -m venv .venv
.venv/bin/pip install -e .
```

### Start Infrastructure

```bash
docker compose up -d
# Wait for Ollama, then pull models:
docker exec -it tldreadme-ollama-1 ollama pull nomic-embed-text
docker exec -it tldreadme-ollama-1 ollama pull qwen2.5-coder:3b-instruct
```

### Index a Codebase

```bash
tldreadme init /path/to/your/project
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

To use cloud providers, set env vars and uncomment in `litellm-config.yaml`:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...
export OPENROUTER_API_KEY=sk-or-...
```

One env var change. No code change.

## Languages

**Primary:** TypeScript, JavaScript, Python, Rust

**Secondary:** Go, C, C++, PHP, Java, Ruby, Swift, Kotlin, Lua, Zig

**Dependencies extracted from:** Cargo.toml, package.json, go.mod, pyproject.toml, requirements.txt

**Not scanned:** node_modules, target, .venv, dist. Dependencies are cataloged from manifests, not source-parsed.

## Architecture

```
tldreadme/
├── cli.py          # init | watch | serve | ask
├── parser.py       # tree-sitter AST extraction (12 languages)
├── embedder.py     # LiteLLM → Qdrant vectors
├── grapher.py      # FalkorDB call/import/dependency graph
├── search.py       # ripgrep wrapper (rg_search, rg_files, rg_count)
├── hot_index.py    # pre-cached top 100 symbols for instant lookup
├── rag.py          # RAG engine + backwards flow (suggest_goals, best_question)
├── chains.py       # daisy-chained tool sequences (know, impact, discover, explain)
├── generator.py    # TLDR.md generator from indexed knowledge
├── watcher.py      # fswatch incremental re-indexing
├── pipeline.py     # orchestrates: parse → embed → graph → generate
└── mcp_server.py   # 16 MCP tools
```

## Philosophy

- **Intelligence in, intelligence out.** The quality of TLDR.md determines how smart the LLM is about your code.
- **Search before writing.** `read_similar` and `discover` exist so you don't reinvent what already exists.
- **Backwards-first.** The code knows what it needs. `suggest_goals` and `best_question` extract that knowledge.
- **Start fast, go deeper only when needed.** `know` (instant) before `explain` (LLM). `impact` (fast) before refactoring.
- **Scan your code, catalog your deps, fetch docs on demand.** Never parse node_modules.

## License

MIT
