# TLDREADME — How to Actually Use This

From one coder to another. No marketing. Just what works.

---

## The Truth About How You'll Use This

You're going to call `know` 80% of the time. That's not a design flaw — that's the point. Most of coding is "where is this thing and what does it look like." TLDREADME makes that instant instead of a 3-minute grep-and-read cycle.

The other tools exist for the moments when "where is it" isn't enough. When you need to know what breaks, what's similar, or what to do next. Those moments are less frequent but they're the ones where you waste hours without good tooling.

---

## The 80% — Just Show Me The Code

### `know`

This is your default. Before you do anything else, `know` it.

```
know("DiskGraph")
```

Returns: the definition (actual code), every file that references it, caller/callee graph, usage count. One call. No round trips.

Internally it chains: hot index (cached, instant) → rg (definition + usages, milliseconds) → graph (callers/callees, if indexed). It stops as soon as it has enough. If the hot index has it, you get the answer before the rg even runs.

**When to use:** Always. This is your first move. "What is X" → `know("X")`.

### `read_grep`

When you know the exact string. Error messages, config keys, env vars, that weird constant you saw once.

```
read_grep("MEMTABLE_CAPACITY", ["/Volumes/Expansion/Qubed"])
```

Returns: every match with 3 lines of context above and below. Actual code, not file paths.

**When to use:** You have a string. You want to see everywhere it appears. Don't overthink it.

### `read_grep_files`

Even simpler — just tell me which files.

```
read_grep_files("WebRtcIceAgent", ["/Users/claude/redfire-switch"])
```

Returns: file paths. That's it. Use this to scope down before a deeper dive.

**When to use:** "Is this used in the RTP crate or just STRING?" Quick answer, move on.

---

## The 15% — What Breaks If I Touch This

### `impact`

Run this BEFORE you modify anything that other code depends on. Not after. Before.

```
impact("DiskGraph::flush_nodes")
```

Returns: severity rating (high/medium/low/orphan), total reference count, list of affected files, transitive dependents.

If it says "high — 88 references across 7 files" you know to be careful. If it says "orphan — no references found" you know nobody cares and you can refactor freely.

**When to use:** Before modifying a public function, changing a struct field, renaming anything. The 30 seconds this takes saves you the "oh shit I broke 12 things" moment.

### `read_depends`

More granular than `impact` — shows you the actual dependency chain.

```
read_depends("WebRtcSession")
```

Returns: everything that imports, calls, or transitively depends on this symbol. Graph traversal, not text search — it finds dependencies that rg might miss because they go through intermediate types.

**When to use:** When `impact` says "high" and you need to understand the actual dependency chain before deciding how to change something.

### `read_flow`

Trace execution from an entry point through the call chain.

```
read_flow("handle_offer", depth=5)
```

Returns: the chain of functions from entry to leaf. "handle_offer calls create_webrtc_session calls WebRtcIceAgent::new calls..." — with the actual code at each step.

**When to use:** Understanding a request lifecycle. "What happens when a SIP INVITE arrives?" Trace it. "How does a CQL query get from parser to disk?" Trace it.

---

## The 5% — I Need to Think

### `discover`

You don't know the exact name. You know the concept.

```
discover("error handling with retry logic")
```

Returns: merged results from rg (literal matches) and Qdrant (semantic similarity), deduplicated and ranked. Finds code you didn't know existed.

**When to use:** "How does this codebase handle X?" when you don't know where to look. Pattern exploration. Onboarding to a new module.

### `read_similar`

Show me code that does the same thing differently.

```
read_similar("bounded memtable with auto-flush")
```

Returns: actual source code of similar implementations, ranked by semantic similarity. Not file paths — the code itself, so you can see the pattern.

**When to use:** You're about to write something and you suspect it already exists in some form. Or you want to follow the existing pattern instead of inventing a new one. This is Rule Zero: search before writing.

### `explain`

The everything tool. Chains know → impact → discover → LLM synthesis.

```
explain("DiskGraph::knn")
```

Returns: a natural language explanation. What it does, how it works internally, what depends on it, what similar code exists, and what to be careful about when modifying it.

**When to use:** Before a major change to something you don't fully understand. This is expensive (hits the LLM) so don't use it for quick lookups — that's what `know` is for. Use `explain` when you need to deeply understand something before committing to an approach.

### `tldr`

Summarize an entire module or directory.

```
tldr("/Volumes/Expansion/Qubed/libqubed/qubedb3/src/graph")
```

Returns: a natural language TL;DR. Architecture, key entry points, what the module does, how the pieces fit together. RAG-powered — it retrieves the actual symbols and relationships, then synthesizes.

**When to use:** First time looking at a module. Onboarding. Writing documentation. "What does this directory even do?"

---

## Backwards Flow — The Code Tells You What To Do

This is the thing that's actually new. Everything above is "you ask, code answers." The backwards tools flip it: the code asks its own questions and answers them.

### `suggest_goals`

```
suggest_goals("/Volumes/Expansion/Qubed/libqubed/qubedb3")
```

Analyzes the codebase: finds orphan functions (defined but never called), load-bearing symbols (most depended on), TODO/FIXME patterns, incomplete implementations. Then asks the LLM to synthesize 3-5 prioritized next goals with rationale.

**When to use:** Start of a session. "What should I work on?" Let the code tell you instead of guessing.

### `best_question`

```
best_question("Wire KNN into CQL grammar")
```

You have a goal. But you're asking the wrong question. `best_question` looks at the relevant code and formulates the precise question that a senior dev who already knows the codebase would ask. Then it answers that question.

Instead of: "How do I add KNN to CQL?"
It gives you: "Should KNN be a CALL procedure or native syntax, given that DiskGraph.search_knn already returns Vec<(u64,f64)> and the executor dispatches via match on CqlExpr?"

**When to use:** You have a goal but you're not sure how to approach it. The code knows things you don't — let it formulate the question.

### `goal_flow`

```
goal_flow("/Volumes/Expansion/Qubed/libqubed/qubedb3")
```

The full backwards chain in one call: analyze code → suggest goals → pick the highest-impact one → formulate the right question → answer it. From "I don't know what to do" to "here's exactly what to do, why, and the code that matters."

**When to use:** Cold start. New session. Haven't touched this code in a week. One call to get oriented.

---

## The Init — Making It All Work

### First time setup

```bash
# Start the infrastructure
docker compose up -d

# Index a codebase
tldr init /Volumes/Expansion/Qubed/libqubed

# Start the MCP server (Claude Code connects here)
tldr serve
```

`init` does: parse all code (tree-sitter) → embed symbols (Qdrant) → build call/import/dependency graph (FalkorDB) → build hot index (top 100 symbols cached) → generate TLDR.md.

### Watch mode (keep it current)

```bash
tldr watch /Volumes/Expansion/Qubed/libqubed
```

Watches for file saves. On change: re-parse that file → update its embeddings → update graph edges → update hot index if affected. Incremental, not full rescan.

### The generated TLDR.md

After `init`, TLDREADME generates `.claude/TLDR.md` in the target directory. This is the "next-token" feature — it serializes TLDREADME's knowledge into a file that any LLM can consume on startup. Auto-updated by the watcher.

---

## What's NOT in here (and shouldn't be)

- **node_modules / target / .venv**: Never scanned. Dependencies are extracted from manifest files (Cargo.toml, package.json, go.mod, pyproject.toml). You want to know what deps are in play, not read their source.

- **Generated code**: Proto files, bundled JS, compiled output — excluded by default. If you're debugging generated code, use `read_grep` directly, it doesn't care about exclusions.

- **Git history**: `read_recent` will integrate with git log eventually, but for now temporal awareness comes from the watcher (it knows what changed since last index) and the hot index (importance ranking considers file activity).

---

## Quick Reference

| I need to... | Use this | Speed |
|---|---|---|
| See what X is | `know("X")` | Instant |
| Find exact text | `read_grep("text", [dir])` | Milliseconds |
| Check what I'll break | `impact("X")` | Fast |
| See similar code | `read_similar("description")` | Seconds |
| Understand a module | `tldr("/path/to/module")` | Seconds (LLM) |
| Deeply understand X | `explain("X")` | Slow (LLM) |
| Know what to do next | `suggest_goals("/path")` | Slow (LLM) |
| Get oriented cold | `goal_flow("/path")` | Slowest (LLM x3) |

The pattern: start fast, go deeper only when you need to. `know` before `explain`. `impact` before refactoring. `suggest_goals` before planning.

---

*Write what you'd use.*
