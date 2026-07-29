# TLDREADME

Local-first repository reconnaissance, code navigation, planning, and verification.

TLDREADME parses source with tree-sitter, indexes symbols in Qdrant, records
relationships in FalkorDB, and exposes the resulting context through a CLI and
MCP server. The fast `peek` path works without databases or a model provider.

Local Ollama, Qdrant, and FalkorDB are the default. TLDREADME does not pull
models automatically. If a configured model is missing, loading, or
unreachable, the request stops at a bounded deadline and returns a structured
readiness error.

MCP tool results are sent to the client that called them. Connecting the server
to a hosted client can therefore send selected repository context to that
client. LiteLLM and expanded cloud inference are opt-in during setup.

## Quick start

Requirements:

- Python 3.11 or newer; Python 3.12 is recommended
- [uv](https://docs.astral.sh/uv/) for plugin installs
- Docker for Qdrant and FalkorDB
- Ollama for the default local model path
- ripgrep (`rg`) for fast text search

Install from a checkout:

```bash
git clone https://github.com/gitspoked/tldr.git
cd tldr
python3.12 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/tldr setup
```

For the default Ollama configuration, download the two models explicitly:

```bash
ollama pull mxbai-embed-large
ollama pull qwen2.5-coder:3b-instruct
docker compose up -d
.venv/bin/tldr doctor
```

TLDREADME never starts an Ollama download on behalf of a tool call.

Inspect the target, verify the runtime, then index the repository:

```bash
.venv/bin/tldr peek /path/to/project
.venv/bin/tldr setup --check
.venv/bin/tldr doctor
.venv/bin/tldr init /path/to/project
.venv/bin/tldr serve
```

For immediate reconnaissance with no model or database:

```bash
.venv/bin/tldr peek /path/to/project
.venv/bin/tldr peek /path/to/file.py
.venv/bin/tldr peek /path/to/project --markdown
```

See [SETUP.md](SETUP.md) for local, LiteLLM, and troubleshooting instructions.

## Plugin marketplaces

This repository contains marketplace manifests for both Codex and Claude Code.
The plugin launches the pinned release with `uvx` and uses the same durable
configuration as the CLI.

Codex:

```bash
codex plugin marketplace add gitspoked/tldr
codex plugin add tldreadme@gitspoked
```

Claude Code:

```text
/plugin marketplace add gitspoked/tldr
/plugin install tldreadme@gitspoked
/reload-plugins
```

Start a new Codex task or reload Claude Code after installing the plugin. If
setup is incomplete, the MCP server starts quickly with only
`configuration_setup` available. Call it to save the provider, tool profile,
and cloud-inference policy, then restart the host. A global `tldr` install is
not required.

For manual plugin-only setup:

```bash
uvx --python 3.12 \
  --from git+https://github.com/gitspoked/tldr.git@v0.1.4 \
  tldr setup --provider ollama --tool-profile router
```

## Tool profiles

The default `router` profile deliberately exposes four stable tools:

| Tool | Purpose |
| --- | --- |
| `repo_next_action` | Recommend the next action from repository and session state. |
| `repo_lookup` | Route repository, history, symbol, impact, search, and edit-context lookups. |
| `change_plan` | Turn a goal into files, risks, steps, acceptance criteria, and checks. |
| `verify_change` | Verify a change against tests, workboard evidence, and acceptance criteria. |

These tools share the normalized fields `summary`, `confidence`, `evidence`,
`recommended_next_action`, `verification_commands`, and `fallback_used`.

The `full` profile adds direct history, search, graph, language-server,
planning, workboard, and security tools. Select `tool_profile: "full"` when
calling `configuration_setup`, or use the installed CLI:

```bash
tldr setup --provider ollama --tool-profile full
# or for one server invocation:
tldr serve --tool-profile full
```

Tools with unavailable hard dependencies are omitted from MCP `list_tools`
instead of failing after selection. `repo://tooling` reports exposed, deferred,
and suppressed tools with backend details. See [docs/TOOLS.md](docs/TOOLS.md)
for the complete catalog and access rules.

## Provider behavior

TLDREADME uses direct HTTP APIs:

- Ollama: `/api/tags`, `/api/embed`, and `/api/chat`
- LiteLLM proxy: OpenAI-compatible `/v1/embeddings` and `/v1/chat/completions`

Provider requests have transport timeouts and a hard wall-clock deadline. The
default is 15 seconds and can be changed with
`TLDREADME_MODEL_TIMEOUT_SECONDS`. Direct Ollama requests check the exact model
against `/api/tags` before inference, so a missing model is reported without
triggering a pull.

Ollama embedding requests use batches of 32 inputs by default to limit peak
memory use. Set `TLDREADME_EMBED_BATCH_SIZE` to a value from 1 through 128.
Values such as 8 or 16 trade speed for lower memory use on constrained
hardware.

LiteLLM and Qdrant credentials remain environment-managed. Set
`QDRANT_API_KEY` when the configured Qdrant endpoint requires authentication.
`tldr setup` stores endpoints, model aliases, tool profile, and inference
policy, but not secrets. Codex, Claude, and Gemini consumer-subscription
selections are routing preferences for a host integration; they do not grant
provider access and are not reusable as LiteLLM credentials.

`tldr init` keeps parsing and writes local context files when Qdrant or
FalkorDB is unavailable. It reports each skipped backend as a warning. Set
`TLDREADME_DEBUG=1` when a full traceback is needed.

Embedding models use separate Qdrant collections. Changing from Nomic to MxBAI,
or selecting another embedding model, preserves the old collection and avoids
mixing incompatible vector spaces. Run `tldr init PATH` once with the new model
to build its index.

A successful full init marks the current repository's new vectors before
removing its stale vectors. If embedding stops partway through, the cleanup
does not run, so prior vectors are not purged.

## Main commands

```bash
tldr setup                         # configure provider, policy, and tool profile
tldr setup --check                 # inspect saved setup without network probes
tldr peek PATH                     # zero-infrastructure reconnaissance
tldr init PATH                     # parse, embed, graph, and generate context
tldr watch PATH                    # incrementally refresh an index
tldr ask "question"                # answer from indexed repository context
tldr serve                         # stdio MCP server, router profile by default
tldr serve --transport sse -p 8900 # SSE MCP server
tldr doctor                        # runtime and backend diagnostics
tldr doctor --fix                  # select commands and write executable start.sh
tldr summary                       # changes since the local summary checkpoint
tldr plans-capture PATH            # capture planning notes from stdin
tldr whats-next PATH               # grounded next strategic question
tldr current-roadmap PATH          # refresh the durable roadmap
tldr audit all --dry-run           # preview local scanner selection
```

## Repository scope

Indexed queries stay inside one repository unless cross-repository search is
requested explicitly. The presence of a system-wide index does not widen the
task boundary.

```bash
tldr ask -d PATH "question"
tldr ask "find a shared implementation pattern" --cross-repository
```

`tldr whats-next PATH` reads plans, tasks, source, and documentation from
`PATH`. Work from another repository is not added to its candidate list.
Cross-repository code can be used as optional idea material:

```bash
tldr whats-next PATH --cross-repository-ideas
```

That option adds external matches under `shared_code_evidence`. The task scope
remains the selected repository. External plans, tasks, roadmap items, and
next-action candidates are never imported. New indexes store a canonical
repository root with each vector. Older indexes are filtered by file path until
they are indexed again.

## Architecture

```text
source files
  -> tree-sitter parsing
  -> Qdrant symbol embeddings
  -> FalkorDB call/import/dependency graph
  -> local hot index and generated context
  -> CLI and MCP tools
```

Key modules:

- `config.py` owns durable setup and inference policy.
- `model_client.py` provides bounded Ollama and LiteLLM HTTP requests.
- `asts.py`, `deps.py`, and `context_docs.py` extract repository structure.
- `parser.py` is the compatibility facade over those extraction modules.
- `embedder.py` owns Qdrant indexing and semantic lookup.
- `grapher.py` owns FalkorDB indexing and graph queries.
- `coding_tools.py` implements the four router tools and deterministic fallbacks.
- `rag.py` implements indexed retrieval and grounded planning helpers.
- `workboard.py` stores plans and sessions under `.tldr/work/`.
- `mcp_server.py` owns MCP tools, resources, prompts, profiles, and capability filtering.

## Security audit

`tldr audit` coordinates installed scanners and reports missing tools as setup
guidance. The default preference order is:

- dependencies: OSV-Scanner, then pip-audit
- source: Semgrep, then Bandit
- secrets: Gitleaks
- model endpoints: Garak with an explicit configuration

Use `--prefer-snyk` for an authenticated Snyk second layer. `--offline`,
`--kev-catalog`, and the `owasp-web`, `owasp-api`, `owasp-llm`, and `owasp-mcp`
profiles provide local database and policy-oriented variants. Reports can be
saved under `.tldr/security/reports/`.

## Planning and workboard

Plans and resumable sessions are file-backed:

- plans: `.tldr/work/plans/*.yaml`
- sessions: `.tldr/work/sessions/current.<session_id>.yaml`
- child-project registry: `.tldr/work/children.yaml`
- captured notes: `.tldr/roadmap/TLDRPLANS.*.md`
- planning digest: `.tldr/roadmap/TLDRPLANS.md`

The `full` tool profile exposes plan, task, session, roadmap, and audit tools.
The router profile reaches the same common workflows through its four stable
entry points.

## Supported languages

Primary parsing support covers TypeScript, JavaScript, Python, and Rust.
Additional grammars cover Go, C, C++, PHP, Java, Ruby, Swift, Kotlin, Lua, and
Zig. Dependencies are extracted from Cargo, npm, Go, Python, and related
manifests without parsing vendor directories.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -m bedrock -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
uv build
```

The bedrock test marker protects the router surface and state-file
compatibility. MCP integration coverage lives in `tools/mcp-smoke.py`.

## License

MIT. Copyright 2026 Matt Klein.
