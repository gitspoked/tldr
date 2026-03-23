# Repository Guidelines

## Project Structure & Module Organization
`tldreadme/` is the Python package. Keep CLI wiring in `tldreadme/cli.py` and place core logic in focused modules such as `asts.py`, `deps.py`, `context_docs.py`, `embedder.py`, `grapher.py`, `pipeline.py`, `rag.py`, and `mcp_server.py`. `parser.py` is a compatibility facade during the split. Put tests in `tests/` using matching names like `test_cli.py` and `test_parser.py`. Root-level docs live in `README.md`, `SETUP.md`, `USAGE.md`, and `CLAUDE.md`. Docker and model configuration live in `docker-compose*.yml` and `litellm-config.yaml`. Local runtime state belongs in `.tldr/`; generated Claude context files are written under `.claude/`.

## Build, Test, and Development Commands
Create an environment with `python3.12 -m venv .venv` and install locally with `.venv/bin/pip install -e '.[dev]'`. Start the default local stack with `docker compose up -d` or use `docker compose -f docker-compose.llm.yml up -d` when testing the LiteLLM path. Common development commands:

- `python -m pytest -q` runs the full test suite.
- `python -m pytest tests/test_cli.py -q` runs a focused test file.
- `.venv/bin/python -m tldreadme --help` checks the module entry point.
- `.venv/bin/tldr init /path/to/project` rebuilds the index and `.claude/TLDR*.md`.
- `.venv/bin/tldr serve` starts the stdio MCP server; `.venv/bin/tldr serve --transport sse -p 8900` starts the SSE transport.
- `.venv/bin/tldr watch /path/to/project` enables incremental updates.

## Coding Style & Naming Conventions
Follow existing Python style: 4-space indentation, snake_case for modules/functions/tests, PascalCase for dataclasses, and short, direct docstrings. Prefer type hints on public functions and keep modules single-purpose. When adding behavior, extend the existing module instead of creating parallel abstractions.

Agent-facing bedrock: keep the router-default MCP surface limited to `repo_next_action`, `repo_lookup`, `change_plan`, and `verify_change`. Add new read/planning/verification behavior behind one of those tools or keep it in the `full` specialist profile. Preserve the normalized router payload keys (`summary`, `confidence`, `evidence`, `recommended_next_action`, `verification_commands`, `fallback_used`) and keep `parser.py` as the compatibility facade over `asts.py`, `deps.py`, and `context_docs.py`.

## Testing Guidelines
Use `pytest` and keep test names in the `test_<behavior>` form. Mirror current patterns: `CliRunner` for CLI coverage and `tempfile`/`Path` helpers for parser and filesystem cases. Add or update tests with any change to parsing, search, indexing, CLI behavior, or generated context output. Use `.venv/bin/python -m pytest -m bedrock -q` for the contract gate that protects the router surface and state-file compatibility.

## Commit & Pull Request Guidelines
Recent commits use short, direct subjects such as `construction cleanup` and `Add context doc scanner + symlink control`. Keep commits narrowly scoped, use imperative wording when possible, and avoid mixing refactors with behavior changes. PRs should summarize user-visible impact, list any required config or Docker changes, link issues when applicable, and note whether `.claude/TLDR*.md` was intentionally regenerated.

## Security & Configuration Tips
Do not commit `.env`, `.tldr/`, or API keys. The default workflow is local-first with Ollama; cloud providers are opt-in through `litellm-config.yaml`.
