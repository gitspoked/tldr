# Repository guidance

TLDREADME is a Python CLI and Model Context Protocol server for local-first
repository reconnaissance, code navigation, planning, and verification.

The indexing pipeline parses source with tree-sitter, stores symbol embeddings
in Qdrant, builds call/import/dependency relationships in FalkorDB, and writes
local context files. Direct Ollama is the default model path; an
OpenAI-compatible LiteLLM proxy is optional.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
tldr setup
tldr doctor
```

For the default local stack:

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:3b-instruct
docker compose up -d
```

TLDREADME never pulls an Ollama model during a tool call. Provider requests use
the bounded client in `tldreadme/model_client.py` and return structured
readiness errors on missing models, failed requests, or timeouts.

## Main commands

```bash
tldr peek PATH
tldr init PATH
tldr watch PATH
tldr serve
tldr serve --tool-profile full
tldr ask "question"
tldr summary
tldr audit all --dry-run
```

## Stable MCP contract

The default `router` profile exposes:

- `repo_next_action`
- `repo_lookup`
- `change_plan`
- `verify_change`

These tools preserve the normalized keys `summary`, `confidence`, `evidence`,
`recommended_next_action`, `verification_commands`, and `fallback_used`.

Direct specialist tools are available through the `full` profile and are
capability-filtered at runtime. See `docs/TOOLS.md` and `repo://tooling`.

## Development

- Keep CLI wiring in `tldreadme/cli.py`.
- Keep provider configuration in `tldreadme/config.py`.
- Keep model transport and deadlines in `tldreadme/model_client.py`.
- Preserve `tldreadme/parser.py` as the compatibility facade over parsing,
  dependency, and context-document modules.
- Extend the four router tools for common workflows; keep direct specialist
  behavior in the `full` profile.
- Add tests for behavior changes and run both gates:

```bash
python -m pytest -m bedrock -q
python -m pytest -q
```
