# MCP tool catalog

TLDREADME exposes tools according to setup state, the selected tool profile,
and live backend capabilities.

## Discovery rules

Before setup, `configuration_setup` is the only exposed tool. It returns the
saved setup status, the cloud-processing warning, and equivalent CLI commands.

After setup, the default `router` profile exposes four stable tools. The `full`
profile exposes every direct specialist tool whose hard dependencies are
available:

```bash
tldr setup --provider ollama --tool-profile full
# or for one process:
tldr serve --tool-profile full
```

MCP clients discover the active surface through `list_tools`. TLDREADME also
publishes `repo://tooling`, which includes:

- `exposed_tools`: callable in the current process
- `deferred_tools`: part of the catalog but outside the active profile
- `suppressed_tools`: selected by the profile but missing a hard dependency
- `backend_details`: readiness and remediation information
- `recommended_sequence`: preferred order for the current repository state

A Codex or Claude router can use every tool returned by `list_tools`. Tools in
the catalog are not callable while they are deferred or suppressed; select the
`full` profile and start the required backend to expose them.

## Router profile

| Tool | Description |
| --- | --- |
| `repo_next_action` | Recommend the next action from repository, workboard, session, overlap, and child-project state. |
| `repo_lookup` | Route a repository, history, symbol, impact, search, or edit-location lookup. |
| `change_plan` | Build candidate files, risks, ordered steps, acceptance criteria, and verification commands. |
| `verify_change` | Check changes against inferred tests, workboard evidence, and acceptance criteria. |

## Repository and code lookup

| Tool | Hard requirement | Description |
| --- | --- | --- |
| `scan_context` | none | Snapshot code, tests, docs, generated context, workboard state, child projects, and recent changes. |
| `search_context` | ripgrep | Search across repository context surfaces. Pass `source_types=["history"]` for bounded commit search. |
| `edit_context` | none | Collect source, symbol, semantic, test, pattern, and task context for a location. |
| `test_map` | none | Find likely tests and verification commands for a file or symbol. |
| `pattern_search` | none | Find reusable local implementation patterns. |
| `diagnostics_here` | language server | Return diagnostics and likely fix context for a file or position. |
| `know` | none | Find a symbol through the hot index and text search, with optional semantic and graph enrichment. |
| `impact` | none | Assess direct references and optional transitive graph dependents. |
| `read_grep` | ripgrep | Return text-search matches with source context. |
| `read_grep_files` | ripgrep | Return files containing a text or regular-expression match. |
| `read_recent` | Git | Return recently changed files and symbols parsed from their current contents. |
| `history_search` | Git and ripgrep | Search bounded commit subjects and bodies with date, path, all-ref, Unicode alias, and code-point filters. |
| `read_semantic` | language server | Query hover, definition, references, and document symbols at a position. |
| `read_workspace_symbols` | language server | Search workspace symbols for a language-server workspace. |
| `read_symbol` | Qdrant and FalkorDB | Return indexed source, callers, callees, and dependents for a symbol. |
| `read_similar` | Qdrant and embedding model | Return semantically similar source implementations. |
| `read_module` | FalkorDB | Return indexed symbols for a module or directory. |
| `read_flow` | FalkorDB | Trace the call graph from an entry point. |
| `read_depends` | FalkorDB | Return transitive graph dependents for a symbol. |
| `discover` | ripgrep, Qdrant, embedding model | Merge text and semantic search results. |
| `explain` | Qdrant, FalkorDB, embedding and chat models | Synthesize a symbol explanation from source, graph, and similar code. |
| `tldr` | FalkorDB and chat model | Generate a concise indexed module summary. |

The composite tools without hard requirements degrade to deterministic local
results when optional enrichment is unavailable.

`read_recent` answers what changed recently. `history_search` answers what
happened anywhere in the selected history. It asks Git for structured commit
records, uses ripgrep to reduce the candidate set, then applies Unicode-aware
exact and whole-word ranking. This avoids raw substring matches such as `dash`
inside `scanning`. Queries can use a literal character, a name such as
`em dash`, or a code point such as `U+2014`.

## Planning

| Tool | Description |
| --- | --- |
| `suggest_goals` | Rank grounded next goals from repository and plan state. |
| `best_question` | Turn a goal into the next concrete engineering question. |
| `goal_flow` | Combine grounded goal selection with the next engineering question. |
| `auto_iterate` | Walk a bounded sequence of ranked goals. |
| `capture_plans` | Save planning input and refresh the local planning digest. |
| `whats_next` | Return the next strategic question and grounded options. |
| `current_roadmap` | Build or write the current durable roadmap view. |

## Workboard

| Tool | Description |
| --- | --- |
| `plan_list` | List tracked plans. |
| `plan_current` | Read the active plan, session, and overlap state. |
| `plan_create` | Create a file-backed plan. |
| `plan_update` | Update plan metadata, phases, notes, and scope. |
| `plan_archive` | Archive a plan while preserving its record. |
| `task_add` | Add a task to a plan phase. |
| `task_update` | Update task state, evidence, and verification fields. |
| `task_complete` | Mark a task complete with evidence. |
| `session_note` | Append a coordination note to a session. |
| `session_update` | Update the resumable session snapshot. |

## Security

| Tool | Description |
| --- | --- |
| `audit_run` | Run or preview a configured repository security scan. |
| `audit_profiles` | List the available policy-oriented audit profiles. |
| `audit_kev_refresh` | Refresh the local CISA KEV catalog. |

## Timeout and readiness errors

Model-backed tools never initiate an Ollama pull. A missing model is rejected
after the `/api/tags` preflight. Slow provider calls stop at
`TLDREADME_MODEL_TIMEOUT_SECONDS` and return `error_code: model_unavailable`
with provider, model, operation, deadline, reason, fallback tools, and a
recommended next action.

Capability failures use `error_code: tool_unavailable` and identify the missing
backend. Incomplete setup uses `error_code: setup_required` and keeps the
process responsive so a host can display configuration guidance.
