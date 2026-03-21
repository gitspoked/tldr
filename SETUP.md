# TLDREADME Setup Guide

**Give this file to Claude Code (or any AI assistant) and let it set you up.**

```
Hey Claude, read this file and help me set up TLDREADME for my codebase.
```

---

## What You Need

- **Python 3.12+**
- **Docker** (for Qdrant and FalkorDB)
- **Ollama** (local LLM — free, private)
- **ripgrep** (`rg`)

## Step 1: Check Prerequisites

```bash
python3 --version    # need 3.12+
docker --version     # need Docker running
ollama --version     # need Ollama
rg --version         # need ripgrep
```

### Install missing pieces

```bash
# macOS
brew install python@3.12 ripgrep ollama
brew install --cask docker

# Ubuntu/Debian
sudo apt install python3.12 python3.12-venv ripgrep docker.io docker-compose-v2
curl -fsSL https://ollama.com/install.sh | sh

# Windows (WSL2)
# Install Python 3.12, Docker Desktop, ripgrep, then:
# curl -fsSL https://ollama.com/install.sh | sh
```

## Step 2: Pull Ollama Models

TLDREADME uses two models. Pull them once, they stay cached.

```bash
ollama pull nomic-embed-text             # 274MB — code embeddings
ollama pull qwen2.5-coder:3b-instruct   # 1.9GB — code understanding

# Verify
ollama list
```

Total: ~2.2GB. Runs on any machine with 4GB+ free RAM.

## Step 3: Clone and Install

```bash
git clone https://github.com/YOUR_USER/tldreadme.git
cd tldreadme
python3.12 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

## Step 4: Start Databases

```bash
docker compose up -d
```

This starts **Qdrant** (vector search) and **FalkorDB** (graph database). That's it. Ollama runs natively — no container needed.

Verify:

```bash
docker compose ps                          # both healthy
curl -s http://localhost:16333/healthz      # Qdrant
redis-cli -p 16379 ping                    # FalkorDB → PONG
curl -s http://localhost:11434/api/tags     # Ollama → your models
```

## Step 5: Index Your Codebase

```bash
source .venv/bin/activate
tldr init /path/to/your/project
```

Output:

```
TLDREADME initializing /path/to/your/project

Parsing code with tree-sitter...
  Found 847 symbols in 42 files (12,350 lines)

Embedding into Qdrant...
  Embedded 847 code chunks

Building knowledge graph in FalkorDB...
  Graphed 2,341 call edges, 198 imports

Generating context files...
  Written: /path/to/your/project/.claude/TLDR.md

Done. Your codebase is now KNOWN.
```

## Step 6: Connect to Claude Code

### Option A: CLI

```bash
claude mcp add tldreadme -- /path/to/tldreadme/.venv/bin/python3.12 -m tldreadme.mcp_server
```

### Option B: settings.json

Add to `~/.claude/settings.json` or your project's `.claude/settings.json`:

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

### Verify

In Claude Code:

```
Use the know tool to look up "main" in my codebase.
```

If Claude calls the MCP tool and returns code — you're set.

## Step 7: Watch Mode (Optional)

Keep the index fresh as you code:

```bash
tldr watch /path/to/your/project
```

On file save: re-parses the changed file, updates embeddings and graph automatically.

---

## Alternative: Cloud LLM Instead of Ollama

If you don't want to run models locally, use `docker-compose.llm.yml` which adds **LiteLLM** as a proxy to OpenAI, Anthropic, or OpenRouter.

```bash
# 1. Set your API key
cp .env.example .env
# Edit .env — add ONE of:
#   OPENAI_API_KEY=sk-...
#   ANTHROPIC_API_KEY=sk-ant-...
#   OPENROUTER_API_KEY=sk-or-...

# 2. Edit litellm-config.yaml — uncomment your provider

# 3. Start with LiteLLM stack instead
docker compose -f docker-compose.llm.yml up -d
```

This gives you Qdrant + FalkorDB + LiteLLM (port 4000). Set `LITELLM_URL=http://localhost:4000` in your `.env` and TLDREADME routes through LiteLLM to your cloud provider.

---

## Quick Reference

| Command | What |
|---------|------|
| `docker compose up -d` | Start Qdrant + FalkorDB |
| `tldr init /path` | Index a codebase |
| `tldr serve` | Start MCP server |
| `tldr watch /path` | Auto re-index on saves |
| `tldr ask "question"` | RAG answer from CLI |

## Two Docker Compose Files

| File | Services | When to use |
|------|----------|-------------|
| `docker-compose.yml` | Qdrant + FalkorDB | **Default.** You have Ollama locally. |
| `docker-compose.llm.yml` | Qdrant + FalkorDB + LiteLLM | Cloud LLM. No local Ollama. |

## Troubleshooting

**"No module named tldreadme"** — `source .venv/bin/activate`

**"Connection refused" on Qdrant/FalkorDB** — `docker compose up -d`, wait 10 seconds

**"tree-sitter Language init error"** — `pip install 'tree-sitter==0.21.3' 'tree-sitter-languages==1.10.2'`

**Ollama not responding** — `ollama serve` (or check if it's running: `curl http://localhost:11434/api/tags`)

**Parse is slow on first run** — Normal for large codebases. Subsequent `watch` updates are incremental and fast.

**Port 16379 conflict** — FalkorDB uses Redis protocol on port 16379 (non-standard to avoid conflicts). If still colliding, change in docker-compose.yml: `"16380:6379"`
