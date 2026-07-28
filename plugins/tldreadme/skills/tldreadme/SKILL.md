---
name: tldreadme
description: Use TLDREADME for local-first repository orientation, evidence-backed lookup, change planning, and verification.
---

# TLDREADME

Use TLDRReadme when a task depends on understanding an unfamiliar repository,
resuming interrupted work, locating the safest edit surface, or verifying that a
change is complete.

## Default workflow

1. Start with `repo_next_action` when resuming work or when the repository state
   is unclear.
2. Use `repo_lookup` to answer repository and symbol questions with local
   evidence.
3. Use `change_plan` before a non-trivial edit.
4. Use `verify_change` before reporting completion.

Keep the default router tool profile unless the task genuinely needs the larger
specialist surface.

For historical questions, call `repo_lookup` with commit-oriented wording or
`source_types=["history"]`. It searches bounded commit subjects and bodies,
including Unicode character aliases. Use the direct `history_search` tool only
when the full profile is active and the task needs explicit date, path, or
all-ref controls.

## Setup gate

If only `configuration_setup` is available, call it without arguments to read
the status and configuration form. Do not repeatedly call other tools. Ask for
any policy choice the user has not already provided, then call
`configuration_setup` again with the selected provider, tool profile, and cloud
permissions. Tell the user to restart the plugin host after it succeeds.

Use the reported `plugin_uvx` command only when the MCP setup tool cannot be
called. Do not assume a global `tldr` command exists.

Model-backed calls have a bounded deadline and never initiate an Ollama model
download. On `model_unavailable`, report the structured reason and use one of
the returned deterministic fallback tools.

## Full profile

The complete tool catalog is documented in `docs/TOOLS.md` in the marketplace
repository. Direct specialist access requires a saved `full` profile:

```bash
uvx --python 3.12 \
  --from git+https://github.com/gitspoked/tldr.git@v0.1.4 \
  tldr setup --provider ollama --tool-profile full
```

After the host restarts, use MCP discovery or `repo://tooling` to see the exact
surface. Capability-dependent tools remain suppressed until their required
backend is ready.

`repo://tooling` also includes `configuration.inference_policy`. Treat the
selected Codex, Claude, and Gemini subscriptions as host routing preferences,
not credentials. Do not expand non-code or code-level cloud context unless the
matching permission is true; code-level expansion requires
`allow_cloud_code: true`.

## Fast reconnaissance

When no index or backend service is available, use the local CLI:

```bash
tldr peek .
tldr peek path/to/file.py
tldr peek . --markdown
```

`peek` is the safe zero-infrastructure path. It does not require Qdrant,
FalkorDB, or a model provider.

## Mutating commands

`tldr init`, `tldr watch`, and roadmap capture commands write repository-local
state. Run them only when the user asks to initialize, refresh, watch, or capture
planning state.

## Runtime requirement

The bundled MCP configuration launches TLDREADME with `uvx`. If `uvx` is not
available, explain that the user can install `uv` or install TLDREADME directly
from `https://github.com/gitspoked/tldr`.
