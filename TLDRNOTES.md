# Notes

## What This Repo Is

TLDREADME is a local-first code intelligence tool for a repository. It parses code with tree-sitter, indexes it into Qdrant and FalkorDB, exposes that knowledge through MCP, and keeps a small local workboard under `.tldr/work/`.

In plain terms: it is trying to make repo context good enough that an LLM starts from understanding instead of guesswork.

## What To Trust

Trust order matters:

- First: source code, tests, and manifests
- Second: bedrock context docs: `README.md`, `AGENTS.md`, `CLAUDE.md`, and `TLDRNOTES.md`
- Third: local operational state under `.tldr/work/`
- Last: generated files under `.claude/`

The `.claude/TLDR.md` and `.claude/TLDR_CONTEXT.md` files are useful, but they are generated summaries. They are not the source of truth over the code.

Context classification:

- Bedrock context: `README.md`, `AGENTS.md`, `CLAUDE.md`, `TLDRNOTES.md`
- Generated context: `.claude/TLDR.md`, `.claude/TLDR_CONTEXT.md`
- Scratch / non-bedrock notes: `TLDREADME.md`

## What Is Stable Now

The main agent-facing surface is intentionally small and should stay that way:

- `repo_next_action`
- `repo_lookup`
- `change_plan`
- `verify_change`

Those four are the bedrock layer. Everything else should either support them or stay behind the fuller specialist surface.

The test suite now has a bedrock gate:

```bash
.venv/bin/python -m pytest -m bedrock -q
```

That reports a clear GO / NO-GO result for the critical contracts this repo depends on.

## What Was Just Fixed

There was a real bug where `tldr init .` could generate almost-empty `.claude/TLDR*.md` files. The cause was a relative-path mismatch between indexing and generation. That has been fixed.

So now the generated files are no longer blank. They still need quality work, but they are at least being built from the indexed repo correctly.

## What Still Needs Improvement

The generator is working, but the output is not yet impressive enough.

Current issues:

- it can rank `tests/` too high
- the overview prose is still too loose
- `TLDR_CONTEXT.md` signatures can truncate awkwardly
- the generated summary is still more "it said something" than "this is sharp and useful"

Desired direction:

- production modules first for know-how
- tests second as know-when / examples
- cleaner signatures
- tighter summaries

## About LLMVM

LLMVM could help as an optional second-pass writer, but not as the foundation.

Good use:

- refine prose in `.claude/TLDR.md`
- turn structured repo context into a cleaner narrative
- help produce tutorial-style summaries

Bad use:

- canonical indexing
- symbol extraction
- deciding what the source of truth is
- replacing deterministic parsing/ranking inside TLDREADME

So the right model is: let TLDREADME build the facts, then optionally let something like LLMVM polish the wording.

## Near-Term Next Step

If continuing from here, the best next pass is generator quality:

1. rank production modules before tests
2. move tests into a dedicated examples/validation section
3. clean up signature rendering in `TLDR_CONTEXT.md`
4. only then consider optional prose refinement

## Resume After Interruption

If this repo is reopened after a power outage or interrupted session, start here:

- the bedrock layer is already landed and tested
- `tldr init .` relative-root generation was fixed
- the current weak spot is generator quality, not indexing correctness

Recent committed milestones:

- router/state contract hardening
- bedrock contract test reporting
- relative-root fix for generated `.claude/TLDR*.md`

Likely current local noise after testing:

- `.claude/TLDR.md`
- `.claude/TLDR_CONTEXT.md`

Those generated files may be modified just because `tldr init .` was run. Treat them as outputs, not as evidence that core code changed.

Best restart command set:

```bash
git status --short
.venv/bin/python -m pytest -m bedrock -q
.venv/bin/python -m pytest -q
```

If those are green, continue with generator-quality work rather than re-debugging the indexing pipeline.
