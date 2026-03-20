# TLDREADME Setup Guide

**Give this file to Claude Code (or any AI assistant) and let it set you up.**

```
Hey Claude, read this file and help me set up TLDREADME for my codebase.
```

---

## What You Need

- **Python 3.12+**
- **Docker** (for Qdrant and FalkorDB)
- **ripgrep** (`rg`)
- **An LLM** — pick one:
  - Local: Ollama (free, private, no API key)
  - Cloud: OpenAI, Anthropic, or OpenRouter (faster, costs money)

## Step 1: Check Prerequisites

Run these. If any fail, install what's missing.

```bash
python3 --version    # need 3.12+
docker --version     # need Docker running
rg --version         # need ripgrep
```

### Install missing pieces

```bash
# macOS
brew install python@3.12 ripgrep
brew install --cask docker

# Ubuntu/Debian
sudo apt install python3.12 python3.12-venv ripgrep docker.io docker-compose-v2

# Windows (WSL2 recommended)
# Install Python 3.12, Docker Desktop, then: cargo install ripgrep
```

## Step 2: Clone and Install

```bash
git clone https://github.com/YOUR_USER/tldreadme.git
cd tldreadme
python3.12 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

## Step 3: Pick Your LLM Backend

### Option A: Local Ollama (recommended to start)

Free. Private. Runs on your machine. Needs ~4GB RAM for the models.

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh    # Linux
# macOS: brew install ollama
# Windows: download from ollama.com

# Pull the models TLDREADME uses
ollama pull nomic-embed-text           # 274MB — embeddings
ollama pull qwen2.5-coder:3b-instruct # 1.9GB — code understanding

# Verify they're running
ollama list
```

Then use the **lite** Docker Compose (just databases, no LLM containers):

```bash
cp docker-compose.lite.yml docker-compose.override.yml
docker compose up -d
```

### Option B: Cloud Provider (OpenAI, Anthropic, or OpenRouter)

Faster. No local GPU needed. Costs money per request.

```bash
cp .env.example .env
```

Edit `.env` and add ONE of these:

```bash
# Pick one:
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
```

Then edit `litellm-config.yaml` — uncomment the provider you chose and comment out the Ollama lines.

Start the full stack:

```bash
docker compose up -d
```

### Option C: Mix (local embeddings, cloud synthesis)

Best of both worlds. Embeddings stay local and free. Only the LLM synthesis (tldr, suggest_goals, explain) hits the cloud.

```bash
# Pull just the embedding model locally
ollama pull nomic-embed-text

# Set a cloud key for chat
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
```

Edit `litellm-config.yaml`:
- Keep the Ollama `embed` model
- Uncomment a cloud `chat` model

## Step 4: Start the Databases

```bash
docker compose up -d

# Verify everything is healthy
docker compose ps
curl -s http://localhost:6333/healthz    # Qdrant
redis-cli -p 6379 ping                   # FalkorDB (should say PONG)
```

## Step 5: Index Your First Codebase

```bash
# Activate venv if not already
source .venv/bin/activate

# Index a project
tldreadme init /path/to/your/project

# Example:
tldreadme init ~/my-app
```

You'll see output like:

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

  MCP server:  tldreadme serve
  Watch mode:  tldreadme watch /path/to/your/project
  Ask:         tldreadme ask "how does X work?"
```

## Step 6: Connect to Claude Code

Add TLDREADME as an MCP server so Claude can use the `know`, `impact`, `discover`, and other tools.

### Option A: Via claude settings

```bash
claude mcp add tldreadme -- /path/to/tldreadme/.venv/bin/python3.12 -m tldreadme.mcp_server
```

### Option B: Via settings.json

Add to your Claude Code settings (`~/.claude/settings.json` or project `.claude/settings.json`):

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

### Verify it works

In Claude Code:

```
Hey Claude, use the know tool to look up "main" in my codebase.
```

If Claude calls the `know` MCP tool and returns code — you're set.

## Step 7: Watch Mode (Optional)

Keep the index current as you code:

```bash
tldreadme watch /path/to/your/project
```

Runs in the background. On file save: re-parses the changed file, updates embeddings and graph. Your `know` and `impact` results are always fresh.

## Step 8: Read USAGE.md

`USAGE.md` explains all 16 tools and when to use each one. The short version:

- **`know`** — 80% of the time. "What is X?" Start here.
- **`impact`** — Before changing anything. "What breaks?"
- **`explain`** — Deep understanding before major changes.
- **`suggest_goals`** — "What should I work on next?"

---

## Quick Reference

| Command | What |
|---------|------|
| `tldreadme init /path` | Index a codebase |
| `tldreadme serve` | Start MCP server |
| `tldreadme watch /path` | Auto re-index on file saves |
| `tldreadme ask "question"` | RAG-powered answer from CLI |
| `docker compose up -d` | Start Qdrant + FalkorDB (+ optional Ollama/LiteLLM) |
| `docker compose down` | Stop infrastructure |

## Troubleshooting

**"No module named tldreadme"** — Activate the venv: `source .venv/bin/activate`

**"Connection refused" on Qdrant/FalkorDB** — Run `docker compose up -d` and wait 10 seconds.

**"tree-sitter Language init error"** — Version mismatch. Pin: `pip install 'tree-sitter==0.21.3' 'tree-sitter-languages==1.10.2'`

**Ollama models not found** — Run `ollama pull nomic-embed-text && ollama pull qwen2.5-coder:3b-instruct`

**Parse is slow** — Exclude large vendored dirs. The parser skips node_modules/target/.venv by default, but massive repos with generated code will be slower on first index.

**MCP server not connecting** — Check the path in settings.json points to the venv Python, not system Python.
