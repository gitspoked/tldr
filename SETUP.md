# TLDREADME setup

## 1. Install

TLDREADME requires Python 3.11 or newer. Python 3.12 is recommended for the
CLI and is the pinned runtime used by the marketplace plugin.

```bash
git clone https://github.com/gitspoked/tldr.git
cd tldr
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Install the development tools only when working on TLDREADME itself:

```bash
pip install -e '.[dev]'
```

The normal local stack also needs:

- Docker
- Ollama
- ripgrep (`rg`)

On macOS:

```bash
brew install python@3.12 ripgrep ollama uv
brew install --cask docker
```

## 2. Run configuration

The interactive setup asks for the provider, MCP tool profile, and cloud
inference policy:

```bash
tldr setup
```

For a non-interactive local configuration:

```bash
tldr setup --provider ollama --tool-profile router
```

Setup writes `~/.config/tldreadme/config.json` by default. It stores endpoints,
model names, tool profile, and inference policy. It does not store API keys or
provider credentials.

If the configured Qdrant endpoint requires authentication, provide its key in
the process environment:

```bash
export QDRANT_API_KEY="..."
```

Check the saved state without contacting any service:

```bash
tldr setup --check
tldr setup --check --json-output
```

If setup has not run, a plugin-launched MCP server remains responsive and
exposes only `configuration_setup`. Call that tool with the provider and policy
choices to write the same durable configuration as the CLI. A global `tldr`
install is not required.

For a manual plugin-only fallback:

```bash
uvx --python 3.12 \
  --from git+https://github.com/gitspoked/tldr.git@v0.1.4 \
  tldr setup --provider ollama --tool-profile router
```

## 3. Prepare the local provider

Ollama models must be downloaded explicitly:

```bash
ollama pull mxbai-embed-large
ollama pull qwen2.5-coder:3b-instruct
ollama list
```

TLDREADME does not call `ollama pull` and does not use an inference endpoint
that implicitly downloads a model. Before a direct Ollama request, it checks
the exact model against `/api/tags`.

Start Qdrant and FalkorDB:

```bash
docker compose up -d
docker compose ps
```

Then verify the complete runtime:

```bash
tldr doctor
```

To turn missing-service and missing-tool guidance into a reviewable startup
script:

```bash
tldr doctor --fix
```

Select the recommended commands with Space and confirm with Enter. TLDREADME
writes an executable `start.sh` in the current directory without running it.
Review the file, then run the commands individually or run `./start.sh` for
faster start. Existing scripts are never overwritten; use `--fix-output` to
choose another path.

## 4. Index a repository

Use this sequence for the first index in each repository:

```bash
tldr peek /path/to/project
tldr setup --check
tldr doctor
tldr init /path/to/project
tldr peek /path/to/project
```

This parses supported source files, extracts dependencies, stores symbol
embeddings in Qdrant, builds the FalkorDB graph, refreshes the hot index, and
writes `.claude/TLDR.md` and `.claude/TLDR_CONTEXT.md`.

The default Ollama embedding batch is 32. For a machine under memory pressure,
start lower:

```bash
TLDREADME_EMBED_BATCH_SIZE=16 tldr init /path/to/project
```

`tldr init` does not need the Qwen chat model to parse source, build the local
hot index, or generate context files. MxBAI is required for the default
embedding stage. Qwen is used later for model-backed explanations and
synthesis.

Each embedding model has its own Qdrant collection. After a model change, run
`tldr init` again for each repository that should be searchable with that
model. A complete run removes stale vectors for that repository only. Cleanup
starts after every new vector has been stored, so a partial embedding failure
does not purge prior vectors.

Qdrant and FalkorDB are optional enrichment backends during init. If either
backend is unavailable, init reports a warning and still writes the local hot
index and context files. Use `TLDREADME_DEBUG=1` only when a traceback is
needed for development.

Use the no-infrastructure path before indexing or when services are offline:

```bash
tldr peek /path/to/project
tldr peek /path/to/file.py
```

## 5. Install the plugin

Install `uv` first so the plugin can launch the pinned package with `uvx`.

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

The default plugin surface is the four-tool `router` profile. To expose direct
specialist tools, call `configuration_setup` with `tool_profile` set to `full`,
then restart the host. Installed CLI users can save the same setting with:

```bash
tldr setup --provider ollama --tool-profile full
```

See [docs/TOOLS.md](docs/TOOLS.md) for the complete catalog and capability
requirements.

## LiteLLM proxy

Use `docker-compose.llm.yml` when inference should run through a LiteLLM proxy:

```bash
cp .env.example .env
docker compose -f docker-compose.llm.yml up -d
tldr setup \
  --provider litellm \
  --litellm-url http://localhost:6004 \
  --embed-model embed \
  --chat-model chat \
  --qdrant-url http://localhost:6033 \
  --falkordb-url redis://localhost:6079
tldr doctor
```

The alternate host ports keep this stack separate from services already using
4000, 6333, or 6379. Containers still use their standard ports internally.

Configure the upstream provider and credentials in LiteLLM or its environment.
TLDREADME can add `Authorization: Bearer` from `LITELLM_API_KEY` or
`LITELLM_MASTER_KEY`; it never persists those values.

Codex, Claude, and Gemini consumer-subscription selections in `tldr setup` are
host routing preferences. They do not grant API access and cannot be reused as
LiteLLM credentials.

Expanded non-code or code-level cloud inference requires:

1. an explicit permission flag,
2. at least one selected subscription target, and
3. acknowledgement that selected context may leave the local machine.

For example:

```bash
tldr setup \
  --provider litellm \
  --litellm-url http://localhost:4000 \
  --allow-cloud-non-code \
  --cloud-subscription codex \
  --acknowledge-cloud-warning
```

Code-level cloud expansion remains disabled unless
`--allow-cloud-code` is also supplied.

## Timeouts and model readiness

Every model request has both an HTTP transport timeout and a hard wall-clock
deadline. The default deadline is 15 seconds:

```bash
export TLDREADME_MODEL_TIMEOUT_SECONDS=15
```

When a provider is loading or downloading a model elsewhere, TLDREADME returns
a structured `model_unavailable` response after the deadline. The response
includes the provider, model, operation, deadline, reason, fallback tools, and
recommended next action.

Common Ollama fixes:

```bash
ollama list
ollama pull MODEL_NAME
ollama serve
```

## Manual MCP launch

The marketplace plugin is preferred, but the stdio server can also be
registered directly:

```bash
claude mcp add tldreadme -- /path/to/tldr/.venv/bin/tldr serve
```

For a network client:

```bash
tldr serve --transport sse --host 127.0.0.1 --port 8900
```

## Troubleshooting

`Must run Configuration - Setup first.`

```bash
tldr setup
tldr setup --check
```

`model_missing`

```bash
ollama list
ollama pull MODEL_NAME
```

`model_unavailable` with `status: timeout`

The provider did not respond before the configured deadline. Check whether it
is loading or downloading a model, verify the endpoint, and retry after the
provider is ready.

Qdrant or FalkorDB connection refused:

```bash
docker compose up -d
docker compose ps
```

Tree-sitter compatibility error:

```bash
pip install 'tree-sitter==0.21.3' 'tree-sitter-languages==1.10.2'
```

Missing language-server tools:

Install the language server for the file type, then restart the MCP server.
`tldr doctor` lists detected language servers and install suggestions.
