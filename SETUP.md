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

Check the saved state without contacting any service:

```bash
tldr setup --check
tldr setup --check --json-output
```

If setup has not run, a plugin-launched MCP server remains responsive and
exposes only `configuration_setup`. The tool returns
`Must run Configuration - Setup first.` together with the setup commands.

## 3. Prepare the local provider

Ollama models must be downloaded explicitly:

```bash
ollama pull nomic-embed-text
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

## 4. Index a repository

```bash
tldr init /path/to/project
```

This parses supported source files, extracts dependencies, stores symbol
embeddings in Qdrant, builds the FalkorDB graph, refreshes the hot index, and
writes `.claude/TLDR.md` and `.claude/TLDR_CONTEXT.md`.

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
specialist tools, save the `full` profile and restart the host:

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
  --litellm-url http://localhost:4000 \
  --embed-model embed \
  --chat-model chat
tldr doctor
```

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
