---
name: tldreadme
description: Use TLDREADME for local-first repository orientation, evidence-backed lookup, change planning, and verification.
---

# TLDREADME

Use TLDRReadme when a task depends on understanding an unfamiliar repository,
resuming interrupted work, locating the safest edit surface, or verifying that a
change is complete.

## Default workflow

1. Resolve the target repository root and keep it attached to every lookup,
   planning, and verification call.
2. Start with `repo_next_action` when resuming work or when repository state is
   unclear.
3. Use `repo_lookup` to answer repository and symbol questions with local
   evidence.
4. Use `change_plan` before a non-trivial edit.
5. Use `verify_change` before reporting completion.

Keep the default router tool profile unless the task genuinely needs the larger
specialist surface.

For historical questions, call `repo_lookup` with commit-oriented wording or
`source_types=["history"]`. It searches bounded commit subjects and bodies,
including Unicode character aliases. Use the direct `history_search` tool only
when the full profile is active and the task needs explicit date, path, or
all-ref controls.

## Repository boundary

Treat one repository as the default unit of work even when Qdrant contains
symbols from many repositories. Do not widen a lookup or planning request just
because system-wide context is available.

Planning, next-action, task, roadmap, history, and workboard evidence must come
from the selected repository. Cross-repository behavior requires an explicit
request from the user. When it is requested for ideation, use external code
only as `shared_code_evidence`. Never import external tasks, plans, roadmap
items, or next actions into the selected repository.

Use `cross_repository=true` for a direct shared-code comparison. Use
`cross_repository_ideas=true` for planning that may cite reusable external
code. Leave both values false in every other case.

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

## First index in a repository

Run a full initialization only when the user asks to initialize or refresh a
repository. Use this order:

```bash
tldr peek /path/to/repository
tldr setup --check
tldr doctor
tldr init /path/to/repository
tldr peek /path/to/repository
```

`peek` establishes the repository boundary before any state is written.
`setup --check` reads saved configuration without probing services. `doctor`
checks Qdrant, FalkorDB, Ollama or LiteLLM, and exact model readiness before
the longer parse begins.

For the default Ollama path, the expected models are:

```bash
ollama pull mxbai-embed-large
ollama pull qwen2.5-coder:3b-instruct
```

Never pull them automatically. Report the exact missing model and let the user
decide whether to install it. MxBAI is used for embeddings. Qwen is used for
model-backed explanations and synthesis, but is not required for parsing, the
hot index, or deterministic context generation.

The default embedding batch is 32. If the machine has limited free memory or
other Ollama models are loaded, use a lower batch for that run:

```bash
TLDREADME_EMBED_BATCH_SIZE=16 tldr init /path/to/repository
```

Keep `QDRANT_API_KEY` in the process environment when Qdrant requires
authentication. Do not place it in repository files or persisted TLDREADME
configuration.

An init result with backend warnings is still usable. Qdrant or FalkorDB
failure must not prevent the local hot index and context files from being
written. Report the degraded stage without a traceback. Use
`TLDREADME_DEBUG=1` only when the user asks for detailed debugging.

## Refreshing symbols

Run `tldr init` again after broad symbol movement or an embedding-model change.
Embedding models use separate Qdrant collections, so incompatible vector
spaces are not mixed. A successful full run removes stale vectors for the
selected repository after all current vectors have been stored. A partial run
does not perform stale-vector cleanup, so prior vectors are not purged.

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
planning state. Cross-repository ideation does not authorize writes to any
external repository.

## Runtime requirement

The bundled MCP configuration launches TLDREADME with `uvx`. If `uvx` is not
available, explain that the user can install `uv` or install TLDREADME directly
from `https://github.com/gitspoked/tldr`.
