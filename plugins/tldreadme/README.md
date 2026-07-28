# TLDREADME plugin

TLDREADME provides local-first repository reconnaissance, evidence-backed
lookup, change planning, and verification to Codex and Claude Code.

The bundled MCP server uses the smaller router-first tool surface:

- `repo_next_action`
- `repo_lookup`
- `change_plan`
- `verify_change`

## Runtime requirement

Install [`uv`](https://docs.astral.sh/uv/) so the plugin can launch the pinned
TLDREADME release with `uvx` and its supported Python 3.12 runtime.

Run configuration before enabling the plugin:

```bash
tldr setup
tldr setup --check
```

If setup is incomplete, the server exposes only `configuration_setup` and
returns the command needed to finish configuration. Model calls never start an
Ollama download; slow providers return a bounded readiness error.

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
tldr setup --provider ollama --tool-profile full
```

Restart Codex or Claude Code after changing the profile. The MCP
`repo://tooling` resource reports which tools are exposed, deferred, or
suppressed by missing backends.
