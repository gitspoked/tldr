# CODEX.md

This file provides guidance to Codex and GitHub-facing coding agents when working with this repository.

## What This Repo Is

TLDREADME is a local-first code intelligence layer for repositories. It parses code with tree-sitter, indexes symbols into Qdrant, builds call/import/dependency graphs in FalkorDB, and serves that context through a router-first MCP server.

The main product split is:

- human CLI in `tldreadme/cli.py`
- agent/router surface in `tldreadme/mcp_server.py`
- router-friendly coding layer in `tldreadme/coding_tools.py`

## First Commands

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/tldr doctor
.venv/bin/python -m pytest -m bedrock -q
.venv/bin/python -m pytest -q
```

If the local stack is needed:

```bash
docker compose up -d
.venv/bin/tldr init .
```

## Bedrock Contract

Treat this four-tool MCP surface as stable:

- `repo_next_action`
- `repo_lookup`
- `change_plan`
- `verify_change`

New agent-facing behavior should extend one of those tools or remain in the `full` specialist profile.

Preserve the normalized top-level payload fields:

- `summary`
- `confidence`
- `evidence`
- `recommended_next_action`
- `verification_commands`
- `fallback_used`

## Important Files

- `tldreadme/parser.py` is the compatibility facade over `asts.py`, `deps.py`, and `context_docs.py`.
- `tldreadme/roadmap.py` owns `.tldr/roadmap/TLDRPLANS.md` and `TLDROADMAP.md`.
- `tldreadme/workboard.py` owns plans, tasks, and canonical session snapshots under `.tldr/work/`.
- `tldreadme/context_docs.py` defines which root docs and planning docs become router-visible context.

## Trust Order

Use this order when the repo contains conflicting guidance:

1. Code, tests, and manifests
2. `README.md`, `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, `GEMINI.md`, `TLDROADMAP.md`
3. `.tldr/roadmap/TLDRPLANS.md`
4. `TLDRNOTES.md`
5. `.claude/TLDR.md` and `.claude/TLDR_CONTEXT.md`

## Practical Guidance

- Prefer extending existing modules over creating parallel abstractions.
- Keep router-default surface small.
- Update tests with behavior changes, especially `tests/test_mcp_server.py`, `tests/test_coding_tools.py`, and `tests/test_parser.py`.
- Do not treat `.claude/` output as source of truth; it is generated context.
