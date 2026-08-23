# Peek: layered reconnaissance

`peek` is the zero-infrastructure entry point. `peek_target(path)` inspects any
file or directory and returns a structured dict that is useful whether the target
has been indexed or not, whether Docker services are running or not, and whether a
model provider is configured or not.

It works by running four enrichment layers in sequence. Each layer adds detail
when its inputs are available and skips gracefully when they are not. The result
dict always contains every key, so callers never need defensive `.get()` calls.

```bash
tldr peek /path/to/project
tldr peek /path/to/file.py
tldr peek /path/to/project --markdown
```

## The layered model

| Layer | Runs when | Adds | Marker in `enrichment_layers` |
| --- | --- | --- | --- |
| 0 - Filesystem scan | always | file/line stats, extension histogram, symbols | `disk` |
| 1 - Context docs | always | context docs list, project name/version | `context_docs` |
| 2 - Indexed knowledge | `.tldr/` exists | top symbols, generated summary | `tldr` |
| 3 - Live services | services answer within 1s | semantic + graph neighbors | `qdrant`, `falkordb` |

Each layer is additive. A cold, unindexed repo with no services running still
returns a complete Layer 0 + Layer 1 result; the same repo once indexed and with
Qdrant/FalkorDB up returns the same shape with layers 2 and 3 filled in.

### Layer 0 - Filesystem scan (always runs)

Walks the target with hard bounds so it stays fast and never hangs:

- Max walk depth 3; symlinks are skipped.
- Noise directories are excluded: `node_modules`, `.git`, `__pycache__`, `target`,
  `.venv`, `venv`, `dist`, `build`, `.tox`.

For a directory it accumulates a file count, an extension histogram, and a total
line count. For a file it measures size, counts lines, detects the extension, and
extracts symbols. Files larger than 1 MB skip symbol extraction (and heavy line
counting) so a stray large or binary file cannot stall the scan.

Symbols come from tree-sitter when a grammar is available, with a regex fallback
that recognizes `def`, `class`, Rust `fn`/`struct`/`enum`/`trait`, Go `func`,
JS/TS `function`/`interface`, including common `pub`, `async`, and `export`
prefixes. This layer always appends `disk` to `enrichment_layers`. A path that is
neither a file nor a directory returns early with `type: "missing"`.

### Layer 1 - Context docs and manifest detection (always runs)

Finds the human-facing context around the target, resolved from the target or its
nearest project root:

- Context docs: `README.md`, `CLAUDE.md`, `AGENTS.md`, `CODEX.md`, `GEMINI.md`,
  and related files.
- Project identity: name, version, and description detected from the nearest
  manifest - `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, or `setup.py`.

Appends `context_docs` to `enrichment_layers` when any docs are found. On error it
records `context_docs_failed` in `fallback_used` and continues.

Layer 1 also derives an `about` line - a model-free "what is this repo about".
Priority is the manifest's own `description`, then the first real paragraph of a
readme-like doc. Agent-instruction files (`CLAUDE.md`, `AGENTS.md`, `CODEX.md`,
`GEMINI.md`) are skipped for this, because their opening paragraph explains how to
work in the repo, not what the repo is.

### Layer 2 - Indexed knowledge (runs if `.tldr/` exists)

If the project root has a `.tldr/` directory, the target has been indexed, so
peek reads the cheap pre-computed artifacts without touching a database:

- `.tldr/hot_index.json` -> the top 20 symbols by importance heuristic (name,
  kind, file).
- `.claude/TLDR.md` -> the pre-generated summary, returned as `generated_summary`.

Sets `indexed: true` and appends `tldr`. Read failures degrade to
`hot_index_read_failed` or `tldr_md_read_failed` in `fallback_used`.

### Layer 3 - Live services (runs if services respond within 1s)

Opportunistically enriches from the running index, but only if the services
answer immediately. It probes cheaply first and never instantiates the heavy
clients eagerly (their constructors open connections):

- Qdrant: a raw HTTP GET to `/collections`. On `200`, it uses the embedder to
  pull semantic neighbors into `related`.
- FalkorDB: a raw TCP `PING`. On `PONG`, it uses the grapher to pull call-graph
  neighbors into `related`.

Both use 1-second timeouts. Unreachable or slow services are skipped and recorded
as `qdrant_unavailable` / `falkordb_unavailable` (or `*_search_failed` /
`*_query_failed` on a query error). Successful probes append `qdrant` and/or
`falkordb` to `enrichment_layers`.

## Result contract

`peek_target()` always returns these keys, regardless of which layers fired:

| Key | Meaning |
| --- | --- |
| `path` | Absolute resolved path |
| `type` | `directory`, `file`, or `missing` |
| `project` | Name/version/description from Layer 1, or `null` |
| `about` | Model-free repo purpose (manifest description or README), or `""` |
| `stats` | Layer 0 counts (files, lines, extensions, size) |
| `context_docs` | Layer 1 docs found |
| `symbols` | Layer 0 symbols (files only) |
| `indexed` | Whether `.tldr/` was found (Layer 2) |
| `generated_summary` | `.claude/TLDR.md` contents, or `null` |
| `hot_symbols` | Top symbols from the hot index (Layer 2) |
| `related` | Semantic/graph neighbors (Layer 3) |
| `enrichment_layers` | Ordered list of layers that fired |
| `fallback_used` | Skipped steps and why |

`enrichment_layers` and `fallback_used` together are a self-describing trace:
the caller can see exactly how much context backs the result and what was
unavailable, without guessing.

## Rendering and the router contract

Three renderers consume the same dict:

- `render_peek(result)` - compact terminal output with ruled sections.
- `render_peek_markdown(result)` - Markdown with headings and tables (good for
  piping to an agent).
- `peek_to_router_result(result)` - maps peek into the MCP router contract
  (`summary`, `confidence`, `evidence`, `recommended_next_action`,
  `fallback_used`, plus the raw `peek` dict).

Confidence is a direct function of the deepest layer reached, so a caller can
weight the result by how much real context stands behind it:

| Confidence | Reached |
| --- | --- |
| 0.5 | Layers 0-1 only (filesystem + context docs) |
| 0.7 | Layer 2 present (`.tldr/` indexed knowledge) |
| 0.9 | Layer 3 present (Qdrant or FalkorDB responded) |

This is why `peek` is wired in as the `repo_lookup` fallback for unindexed repos:
it always returns something useful, and it reports honestly how confident that
something is.

## Worked example

A real `peek` on an indexed file in this repo (`tldreadme/docfetch.py`), with
FalkorDB up but Qdrant down:

```text
type              : file
enrichment_layers : ['disk', 'context_docs', 'tldr', 'falkordb']
fallback_used     : ['qdrant_unavailable', 'falkordb_query_failed']
indexed           : True
about             : Local-first repository reconnaissance, code navigation, planning, and verification through CLI and MCP interfaces.
stats             : {"files": 1, "lines": 573, "extensions": {"py": 1}, "size_bytes": 22715}
project           : {"name": "tldreadme", "version": "0.1.4", "manifest": "pyproject.toml"}
```

Read the trace back through the layers:

- Layer 0 (`disk`) produced `stats` and the symbol list.
- Layer 1 (`context_docs`) found the project and set `about` from the manifest's
  `description` - which is why it reads slightly fuller than the README tagline.
- Layer 2 (`tldr`) fired because `.tldr/` exists (`indexed: True`).
- Layer 3: FalkorDB answered its `PING`, so `falkordb` joined `enrichment_layers`,
  but Qdrant did not respond and the graph query then errored - both recorded in
  `fallback_used`. Because a Layer-3 service responded, `peek_to_router_result`
  reports confidence `0.9`.

## Design principles

- Graceful degradation. Every layer is wrapped so a failure records a reason and
  moves on; the result is never partial-shaped, only lower-confidence.
- Zero-infrastructure floor. Layers 0-1 need nothing but the filesystem, so peek
  answers on a cold checkout with no databases and no model.
- Bounded work. Depth 3, 1 MB file cap, 1-second service probes, and lazy client
  construction keep a single peek fast and non-blocking.
- Additive, not branching. The same code path runs all four layers; availability,
  not configuration, decides how rich the answer is.
