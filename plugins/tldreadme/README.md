# TLDREADME plugin

TLDREADME provides local-first repository reconnaissance, evidence-backed
lookup, change planning, and verification to Codex and Claude Code.

The bundled MCP server uses the smaller router-first tool surface:

- `repo_next_action`
- `repo_lookup`
- `change_plan`
- `verify_change`

Repository lookup and planning stay inside the selected repository by default.
Cross-repository code is available only when explicitly requested for
comparison or shared-code evidence. External plans and tasks never enter the
selected repository's next-action list.

## Runtime requirement

Install [`uv`](https://docs.astral.sh/uv/) so the plugin can launch the pinned
TLDREADME release with `uvx` and its supported Python 3.12 runtime.

If setup is incomplete, the server exposes only `configuration_setup` and
returns the choices needed to finish configuration. Call that tool again with
the selected provider and inference policy, then restart the host. A global
`tldr` install is not required. Model calls never start an Ollama download;
slow providers return a bounded readiness error.

For the first full index in a repository, use `tldr peek PATH`,
`tldr setup --check`, and `tldr doctor` before `tldr init PATH`. The default
Ollama models are `mxbai-embed-large` for embeddings and
`qwen2.5-coder:3b-instruct` for model-backed synthesis. TLDREADME never
downloads either model automatically.

Manual plugin-only fallback:

```bash
uvx --python 3.12 \
  --from git+https://github.com/gitspoked/tldr.git@v0.1.4 \
  tldr setup --provider ollama --tool-profile router
```

## Codex

```bash
codex plugin marketplace add gitspoked/tldr
codex plugin add tldreadme@gitspoked
```

Start a new task after installation.

## Claude Code

```text
/plugin marketplace add gitspoked/tldr
/plugin install tldreadme@gitspoked
/reload-plugins
```

## Specialist tools

The router profile is intentionally small. The remaining history, search,
graph, language-server, planning, workboard, and security tools are documented in
[`docs/TOOLS.md`](../../docs/TOOLS.md) and become directly discoverable after:

```bash
uvx --python 3.12 \
  --from git+https://github.com/gitspoked/tldr.git@v0.1.4 \
  tldr setup --provider ollama --tool-profile full
```

Restart Codex or Claude Code after changing the profile. The MCP
`repo://tooling` resource reports which tools are exposed, deferred, or
suppressed by missing backends.
