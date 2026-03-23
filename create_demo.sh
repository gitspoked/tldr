#!/usr/bin/env bash
# ─── TLDREADME Demo ──────────────────────────────────────────────
# Run this to see TLDREADME index itself and answer questions.
#
# Prerequisites:
#   docker compose up -d
#   ollama pull nomic-embed-text
#   ollama pull qwen2.5-coder:3b-instruct
#   pip install -e '.[dev]'
#
# Recording:
#   brew install asciinema
#   asciinema rec demo.cast -c "./create_demo.sh"
#   # Then upload: asciinema upload demo.cast
# ──────────────────────────────────────────────────────────────────

set -e

# Activate venv
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

# Suppress noisy warnings (LiteLLM cost map fetch, tree-sitter deprecation)
export PYTHONWARNINGS="ignore::FutureWarning"
export LITELLM_LOG="ERROR"
export LITELLM_TELEMETRY="False"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

pause() {
    sleep "${1:-1.5}"
}

banner() {
    echo
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}  $1${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo
}

step() {
    echo -e "${GREEN}▸${NC} ${BOLD}$1${NC}"
    pause 0.5
}

show_cmd() {
    echo -e "${YELLOW}\$${NC} ${DIM}$1${NC}"
    pause 0.3
}

# ──────────────────────────────────────────────────────────────────

clear

banner "TLDREADME — TL;DR for any codebase"

echo -e "${BOLD}Privacy-first, local-first.${NC} Your code never leaves your machine."
echo -e "Ollama + Qdrant + FalkorDB — all local. No API keys. No uploads."
echo
echo -e "Point it at a directory. It parses every function, embeds it,"
echo -e "graphs the relationships, and serves it all via MCP."
echo -e "Your LLM gets full context ${BOLD}before${NC} you ask your first question."
echo
pause 2

# ── Step 1: Index ─────────────────────────────────────────────────

banner "Step 1: Index a codebase (we'll index ourselves)"

step "Parsing code, embedding into Qdrant, building graph in FalkorDB..."
echo
show_cmd "tldr init ."
tldr init . 2>/dev/null
pause 2

# ── Step 2: Tests ─────────────────────────────────────────────────

banner "Step 2: Full test suite, all passing"

show_cmd "python -m pytest -q"
python -m pytest -q -W ignore::FutureWarning 2>&1 | tail -15
pause 2

# ── Step 3: LiteLLM ───────────────────────────────────────────────

banner "Step 3: LiteLLM — local by default, cloud when you choose"

echo -e "  Every embedding and every synthesis routes through ${BOLD}LiteLLM${NC}."
echo -e "  Default: ${BOLD}local Ollama${NC}. Nothing leaves your machine."
echo -e "  Cloud providers are opt-in — one env var when you're ready."
echo
echo -e "  ${DIM}Current config:${NC}"
CURRENT_CHAT="${TLDREADME_CHAT_MODEL:-ollama/qwen2.5-coder:3b-instruct}"
CURRENT_EMBED="${TLDREADME_EMBED_MODEL:-ollama/nomic-embed-text}"
echo -e "    Embeddings:  ${BOLD}${CURRENT_EMBED}${NC}       ${DIM}(local, free)${NC}"
echo -e "    Synthesis:   ${BOLD}${CURRENT_CHAT}${NC}      ${DIM}(local, free)${NC}"
echo
pause 2
echo -e "  ${DIM}Switch to cloud — one line:${NC}"
echo -e "    ${YELLOW}\$${NC} ${DIM}export LITELLM_URL=http://localhost:4000${NC}"
echo -e "    ${DIM}Then uncomment your provider in litellm-config.yaml:${NC}"
echo
echo -e "    ${DIM}OpenAI:${NC}       openai/gpt-4o + text-embedding-3-small"
echo -e "    ${DIM}Anthropic:${NC}    anthropic/claude-sonnet-4 + OpenAI embeddings"
echo -e "    ${DIM}OpenRouter:${NC}   any model on any provider"
echo
echo -e "  No code changes. Same tools. Better model = better answers."
echo
pause 3

# ── Step 4: Ask ───────────────────────────────────────────────────

banner "Step 4: Ask questions — RAG-powered answers from the CLI"

step "Asking: 'How does the parser extract symbols from source code?'"
echo
show_cmd "tldr ask 'How does the parser extract symbols from source code?'"
tldr ask 'How does the parser extract symbols from source code?' 2>/dev/null
pause 3

# ── Step 5: MCP ───────────────────────────────────────────────────

banner "Step 5: MCP Server — router-first for Claude Code"

echo -e "  ${BOLD}repo_lookup${NC}      — Single read entry point: scan, search, symbol, impact, or edit context."
echo -e "  ${BOLD}repo_next_action${NC} — Resume safely from sessions, overlaps, and imported child trees."
echo -e "  ${BOLD}change_plan${NC}     — Turn a coding goal into files, risks, and verification steps."
echo -e "  ${BOLD}verify_change${NC}   — Check evidence, tests, and acceptance criteria before calling work done."
echo -e "  ${DIM}  ...and 11 more${NC}"
echo
echo -e "Connect to Claude Code:"
echo -e "  ${DIM}claude mcp add tldreadme -- .venv/bin/python3.12 -m tldreadme.mcp_server${NC}"
echo

pause 2

# ── Fin ───────────────────────────────────────────────────────────

banner "That's it. Codebase indexed."

echo -e "  ${DIM}GitHub:${NC}  github.com/ntele-dev/tldreadme"
echo -e "  ${DIM}Install:${NC} pip install -e '.[dev]' && docker compose up -d && tldr init ."
echo
