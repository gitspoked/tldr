"""Fast text search via ripgrep — complements semantic search (Qdrant)."""

import subprocess
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SearchHit:
    """A single rg match with context."""
    file: str
    line: int
    text: str                    # the matching line
    before: list[str]            # context lines before
    after: list[str]             # context lines after


def rg_search(
    pattern: str,
    paths: list[str],
    context: int = 3,
    glob: Optional[str] = None,
    file_type: Optional[str] = None,
    case_insensitive: bool = True,
    max_results: int = 20,
    fixed_strings: bool = False,
) -> list[SearchHit]:
    """Search with ripgrep, return matches with surrounding context.

    This is the fast path — no embeddings, no LLM, just rg.
    Use for: exact strings, regex, known identifiers, error messages.

    Args:
        pattern: regex or fixed string to search for
        paths: directories/files to search
        context: lines of context before/after (default 3)
        glob: file glob filter (e.g. "*.rs", "*.{ts,tsx}")
        file_type: rg type filter (e.g. "rust", "py", "ts")
        case_insensitive: case insensitive search
        max_results: cap results
        fixed_strings: treat pattern as literal, not regex
    """
    cmd = ["rg", "--json", "-C", str(context)]

    if case_insensitive:
        cmd.append("-i")
    if fixed_strings:
        cmd.append("-F")
    if glob:
        cmd.extend(["--glob", glob])
    if file_type:
        cmd.extend(["--type", file_type])

    # Skip common noise
    cmd.extend([
        "--glob", "!node_modules",
        "--glob", "!target",
        "--glob", "!dist",
        "--glob", "!.git",
        "--glob", "!*.min.js",
        "--glob", "!*.min.css",
        "--glob", "!*.map",
        "--glob", "!__pycache__",
    ])

    cmd.append(pattern)
    cmd.extend(paths)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    hits = []
    # rg --json outputs one JSON object per line
    current_match = None
    context_before = []
    context_after = []

    for line in result.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get("type")

        if msg_type == "context":
            data = obj["data"]
            text = data.get("lines", {}).get("text", "").rstrip()
            if current_match is None:
                context_before.append(text)
            else:
                context_after.append(text)

        elif msg_type == "match":
            # Flush previous match
            if current_match is not None:
                current_match.before = context_before
                current_match.after = context_after
                hits.append(current_match)
                if len(hits) >= max_results:
                    break

            data = obj["data"]
            current_match = SearchHit(
                file=data.get("path", {}).get("text", ""),
                line=data.get("line_number", 0),
                text=data.get("lines", {}).get("text", "").rstrip(),
                before=[],
                after=[],
            )
            context_before = []
            context_after = []

        elif msg_type == "end":
            # Flush last match
            if current_match is not None:
                current_match.before = context_before
                current_match.after = context_after
                hits.append(current_match)
                current_match = None
                context_before = []
                context_after = []

    # Handle trailing match without end marker
    if current_match is not None and len(hits) < max_results:
        current_match.before = context_before
        current_match.after = context_after
        hits.append(current_match)

    return hits[:max_results]


def rg_files(
    pattern: str,
    paths: list[str],
    glob: Optional[str] = None,
    file_type: Optional[str] = None,
) -> list[str]:
    """Return just file paths that match (rg -l)."""
    cmd = ["rg", "-l", "-i"]

    if glob:
        cmd.extend(["--glob", glob])
    if file_type:
        cmd.extend(["--type", file_type])

    cmd.extend([
        "--glob", "!node_modules",
        "--glob", "!target",
        "--glob", "!dist",
        "--glob", "!.git",
    ])

    cmd.append(pattern)
    cmd.extend(paths)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    return [f for f in result.stdout.strip().splitlines() if f]


def rg_count(pattern: str, paths: list[str], file_type: Optional[str] = None) -> dict:
    """Count matches per file (rg -c)."""
    cmd = ["rg", "-c", "-i"]

    if file_type:
        cmd.extend(["--type", file_type])

    cmd.extend([
        "--glob", "!node_modules",
        "--glob", "!target",
        "--glob", "!dist",
        "--glob", "!.git",
    ])

    cmd.append(pattern)
    cmd.extend(paths)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    counts = {}
    for line in result.stdout.strip().splitlines():
        if ":" in line:
            path, count = line.rsplit(":", 1)
            try:
                counts[path] = int(count)
            except ValueError:
                pass

    return counts


def format_hits_for_llm(hits: list[SearchHit], max_chars: int = 8000) -> str:
    """Format search hits as context for an LLM — actual code, not just refs."""
    parts = []
    total = 0

    for hit in hits:
        block = f"### {hit.file}:{hit.line}\n```\n"
        for b in hit.before:
            block += f"  {b}\n"
        block += f"> {hit.text}\n"
        for a in hit.after:
            block += f"  {a}\n"
        block += "```\n"

        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)

    return "\n".join(parts)
