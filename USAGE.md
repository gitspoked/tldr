# Using TLDREADME

## Choose the lightest path

For a quick repository or file overview:

```bash
tldr peek /path/to/project
tldr peek /path/to/file.py
tldr peek /path/to/project --markdown
```

`peek` reads the filesystem, project manifests, context documents, and any
existing local index. It does not require a model or database.

For semantic search, graph traversal, and generated summaries:

```bash
tldr init /path/to/project
tldr watch /path/to/project
```

Run `tldr setup` and `tldr doctor` before the first indexed workflow.

## Default MCP workflow

The default router profile keeps the client-facing surface small:

1. `repo_next_action` when resuming work or when repository state is unclear.
2. `repo_lookup` to understand a repository, file, symbol, history, or likely impact.
3. `change_plan` before a non-trivial edit.
4. `verify_change` before reporting completion.

Every router tool returns:

- `summary`
- `confidence`
- `evidence`
- `recommended_next_action`
- `verification_commands`
- `fallback_used`

The router tools use deterministic filesystem, Git, ripgrep, parser, and
workboard fallbacks when optional services are unavailable.

## Specialist tools

Use the full profile when direct access to history, search, graph,
language-server, workboard, planning, or security tools is useful:

```bash
tldr setup --provider ollama --tool-profile full
# or for one process
tldr serve --tool-profile full
```

Restart the plugin host after changing the saved profile. Read
`repo://tooling` to see which tools are exposed, deferred, or suppressed.
[docs/TOOLS.md](docs/TOOLS.md) lists every tool and hard requirement.

Common direct lookups:

```text
know(name="Widget", root=".")
impact(name="Widget", root=".")
read_grep(pattern="timeout", paths=["."])
read_recent(scope=".", days=14)
history_search(query="em dash in commit messages", root=".", all_refs=true)
edit_context(path="src/widget.py", line=42, root=".")
test_map(path="src/widget.py", root=".")
```

`read_recent` is the lightweight current-change view. `history_search` searches
bounded commit subjects and bodies, including older commits, optional date
ranges, path scope, all refs, Unicode character names, and `U+xxxx` code
points. The default router can reach the same behavior through `repo_lookup`
when the query asks about commits or when `source_types=["history"]`.

Indexed graph and semantic tools appear only when Qdrant, FalkorDB, and the
required model are ready.

## Planning and continuity

Capture rough planning input:

```bash
tldr plans-capture .
```

Then derive the next question and refresh the durable roadmap:

```bash
tldr whats-next .
tldr current-roadmap .
```

Both commands keep candidate work inside the selected repository. To compare
shared code while forming ideas, opt in:

```bash
tldr whats-next . --cross-repository-ideas
```

External matches are returned as evidence. They do not become local tasks.

The full MCP profile also exposes file-backed plan, task, and session tools.
Their state lives under `.tldr/work/`, so interrupted work can be resumed
without relying on chat history.

## Security checks

Preview scanner selection before running anything:

```bash
tldr audit all --dry-run
```

Run focused checks as needed:

```bash
tldr audit deps .
tldr audit code .
tldr audit secrets .
tldr audit profiles
```

Missing scanners produce install guidance. Use `--save-report` to preserve a
report under `.tldr/security/reports/`.

## Provider errors

TLDREADME never initiates an Ollama pull. If a model is missing:

```bash
ollama list
ollama pull MODEL_NAME
```

If a provider is loading or downloading a model elsewhere, the tool returns a
structured `model_unavailable` response after
`TLDREADME_MODEL_TIMEOUT_SECONDS`. Use the returned fallback tool or retry after
the provider is ready.

## CLI reference

```bash
tldr setup
tldr setup --check
tldr peek PATH
tldr init PATH
tldr watch PATH
tldr ask "question"
tldr ask "question" --cross-repository
tldr serve
tldr doctor
tldr summary
tldr plans-capture PATH
tldr whats-next PATH
tldr whats-next PATH --cross-repository-ideas
tldr current-roadmap PATH
tldr audit all --dry-run
```
