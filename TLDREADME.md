# TLDREADME maintainer notes

This file is a short continuity map. The canonical user documentation is
[README.md](README.md), installation is covered by [SETUP.md](SETUP.md), and
the complete MCP catalog is in [docs/TOOLS.md](docs/TOOLS.md).

## Product contract

- `tldr peek` must work without Qdrant, FalkorDB, or a model provider.
- `tldr setup` is required before plugin tools are enabled.
- An unconfigured MCP server exposes only `configuration_setup`.
- The default MCP profile exposes exactly `repo_next_action`, `repo_lookup`,
  `change_plan`, and `verify_change`.
- The `full` profile exposes specialist tools only when their hard backends are
  ready.
- Direct Ollama calls check the exact model before inference and never initiate
  a model download.
- Provider calls return within the configured wall-clock deadline.
- Plans, sessions, child-project decisions, and reports remain file-backed.
- `parser.py` remains the compatibility facade over the split extraction
  modules.

## Local development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
tldr setup
python -m pytest -q
ruff check .
ruff format --check .
```

Use `python -m pytest -m bedrock -q` after changing router contracts,
capability filtering, workboard schemas, or compatibility loaders.

## Release gate

```bash
uv build
python tools/mcp-smoke.py --profile router --setup-state required
python tools/mcp-smoke.py --profile router --setup-state configured
python tools/mcp-smoke.py --profile full --setup-state configured
```

Also validate:

- a clean wheel install,
- Codex and Claude marketplace manifests,
- the pinned plugin source and version,
- `git diff --check`,
- Ruff check and format,
- the full pytest suite.
