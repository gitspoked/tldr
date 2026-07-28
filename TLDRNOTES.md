# Maintainer notes

This is a tactical continuity file, not the product specification. Use this
trust order when repository documents disagree:

1. source code, tests, and package manifests;
2. `README.md`, `SETUP.md`, `AGENTS.md`, and the host-specific context files;
3. a human-owned `TLDROADMAP.md`, when present;
4. `.tldr/roadmap/TLDRPLANS.md` and timestamped planning captures;
5. generated `.claude/TLDR*.md` files.

## Stable contracts

- Setup is explicit and durable. Before configuration, MCP exposes only
  `configuration_setup`.
- The router profile exposes four tools: `repo_next_action`, `repo_lookup`,
  `change_plan`, and `verify_change`.
- The full profile exposes direct specialist tools only when their hard
  dependencies are ready.
- Ollama models are checked before inference and are never downloaded
  implicitly.
- Model requests have a transport timeout and an independent wall-clock
  deadline.
- Cloud inference permissions are opt-in. Subscription selections are routing
  preferences, not credentials.
- Plans, sessions, child-project decisions, and saved reports remain
  repository-local and file-backed.
- `parser.py` remains the public compatibility facade over the extraction
  modules.

## Generated state

The following paths are expected to change during normal use and should not be
treated as hand-authored source:

- `.claude/TLDR.md`
- `.claude/TLDR_CONTEXT.md`
- `.tldr/roadmap/TLDRPLANS*.md`
- `.tldr/work/sessions/`
- `.tldr/security/reports/`

`tldr current-roadmap .` creates or refreshes `TLDROADMAP.md`. It preserves the
human-owned section and replaces only the marked generated section.

## Release checks

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -m bedrock -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
uv build
python tools/mcp-smoke.py --profile router --setup-state required
python tools/mcp-smoke.py --profile router --setup-state configured
python tools/mcp-smoke.py --profile full --setup-state configured
```

Also verify a clean wheel install and validate both marketplace manifests
before moving a release tag.
