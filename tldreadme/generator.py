"""TLDR.md generator — deterministic context from the current source tree."""

from dataclasses import dataclass, field
from pathlib import Path
import re

from .hot_index import HotIndex
from .parser import ParseResult, parse_directory

KNOW_WHEN_PARTS = {
    "tests",
    "test",
    "__tests__",
    "spec",
    "specs",
    "examples",
    "example",
    "samples",
    "sample",
    "fixtures",
}

ENTRYPOINT_HINTS = {
    "main",
    "init",
    "serve",
    "ask",
    "watch",
    "run_init",
    "start_server",
    "repo_lookup",
    "repo_next_action",
    "change_plan",
    "verify_change",
    "scan_context",
    "search_context",
    "read_symbol",
    "read_module",
    "read_flow",
    "generate_claude_md",
}

IGNORE_ENTRYPOINT_NAMES = {
    "__init__",
    "walk",
    "decorator",
    "Choice",
    "checkbox",
    "call_tool",
    "list_tools",
    "visit",
}

CONTAINER_KINDS = {"class", "struct", "interface", "trait", "enum", "protocol", "object"}


@dataclass
class SymbolSnapshot:
    """Minimal generated context for one symbol."""

    name: str
    kind: str
    signature: str
    file: str
    line: int
    end_line: int
    parent: str | None = None


@dataclass
class ModuleSnapshot:
    """Generated context about one module directory."""

    path: str
    relative_path: str
    category: str
    files: list[str] = field(default_factory=list)
    symbols: list[SymbolSnapshot] = field(default_factory=list)
    summary: str = ""
    key_files: list[str] = field(default_factory=list)
    key_entrypoints: list[str] = field(default_factory=list)

    @property
    def symbol_count(self) -> int:
        return len(self.symbols)


def generate_claude_md(
    root: Path,
    output_dir: str = ".claude",
    *,
    parse_results: list[ParseResult] | None = None,
    hot_index: HotIndex | None = None,
) -> Path:
    """Generate TLDR.md and TLDR_CONTEXT.md for a directory from current source."""

    root = root.resolve()
    out = root / output_dir
    out.mkdir(exist_ok=True)

    if parse_results is None:
        parse_results = parse_directory(root)

    module_snapshots = _collect_module_snapshots(root, parse_results, hot_index=hot_index)

    claude_md = _build_claude_md(root, module_snapshots)
    claude_path = out / "TLDR.md"
    claude_path.write_text(claude_md, encoding="utf-8")

    context_md = _build_context_md(root, module_snapshots)
    context_path = out / "TLDR_CONTEXT.md"
    context_path.write_text(context_md, encoding="utf-8")

    return claude_path


def _collect_module_snapshots(
    root: Path,
    parse_results: list[ParseResult],
    *,
    hot_index: HotIndex | None = None,
) -> list[ModuleSnapshot]:
    """Group parse results into ranked module snapshots."""

    snapshots_by_path: dict[str, ModuleSnapshot] = {}
    for result in parse_results:
        file_path = Path(result.file)
        relative_file = _relative_path(root, file_path)
        relative_module = relative_file.parent.as_posix() if relative_file.parent.as_posix() != "." else root.name
        container_names = {
            symbol.name
            for symbol in result.symbols
            if symbol.kind in CONTAINER_KINDS
        }
        snapshot = snapshots_by_path.setdefault(
            relative_module,
            ModuleSnapshot(
                path=str(root / relative_module) if relative_module != root.name else str(root),
                relative_path=relative_module,
                category="know_when" if _is_know_when_module(relative_module) else "know_how",
            ),
        )
        snapshot.files.append(relative_file.as_posix())
        for symbol in result.symbols:
            if symbol.parent and symbol.parent not in container_names:
                continue
            snapshot.symbols.append(
                SymbolSnapshot(
                    name=symbol.name,
                    kind=symbol.kind,
                    signature=symbol.signature,
                    file=relative_file.as_posix(),
                    line=symbol.line,
                    end_line=symbol.end_line,
                    parent=symbol.parent,
                )
            )

    snapshots = list(snapshots_by_path.values())
    for snapshot in snapshots:
        snapshot.files = sorted(set(snapshot.files))
        snapshot.key_files = _select_key_files(snapshot)
        snapshot.key_entrypoints = _select_key_entrypoints(snapshot, hot_index=hot_index)
        snapshot.symbols.sort(key=_context_symbol_sort_key)
        snapshot.summary = _module_summary(snapshot)

    return sorted(snapshots, key=lambda snapshot: _module_sort_key(root, snapshot))


def _relative_path(root: Path, path: Path) -> Path:
    """Return a stable repo-relative path."""

    try:
        return path.resolve().relative_to(root)
    except Exception:
        return Path(path.name)


def _is_know_when_module(relative_path: str) -> bool:
    """Return whether a module is primarily tests/examples/fixtures."""

    path = Path(relative_path)
    parts = {part.lower() for part in path.parts}
    leaf = path.name.lower()
    return bool(parts & KNOW_WHEN_PARTS) or leaf.startswith("test")


def _module_sort_key(root: Path, snapshot: ModuleSnapshot) -> tuple[int, int, int, int, str]:
    """Rank production modules before examples/tests."""

    leaf = Path(snapshot.relative_path).name.lower()
    root_name = root.name.lower()
    if leaf == root_name:
        leaf_priority = 0
    elif leaf in {"src", "app", "lib", "pkg"}:
        leaf_priority = 1
    else:
        leaf_priority = 2

    return (
        0 if snapshot.category == "know_how" else 1,
        leaf_priority,
        -snapshot.symbol_count,
        len(Path(snapshot.relative_path).parts),
        snapshot.relative_path,
    )


def _module_label(snapshot: ModuleSnapshot, root: Path) -> str:
    """Return a human-readable module label."""

    if snapshot.relative_path == root.name:
        return f"{root.name}/"
    return f"{snapshot.relative_path}/"


def _select_key_files(snapshot: ModuleSnapshot, limit: int = 5) -> list[str]:
    """Return the most important file stems for a module snapshot."""

    scores: dict[str, int] = {}
    for file_path in snapshot.files:
        stem = Path(file_path).stem
        scores[stem] = scores.get(stem, 0) + sum(1 for symbol in snapshot.symbols if symbol.file == file_path)
    return [stem for stem, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _signal_file_priority(file_path: str) -> int:
    """Prefer product-facing surfaces before deeper helpers in context tables."""

    name = Path(file_path).name
    if name == "coding_tools.py":
        return 0
    if name in {"cli.py", "mcp_server.py", "pipeline.py", "generator.py"}:
        return 1
    if name in {"workboard.py", "summary.py", "children.py", "lsp.py"}:
        return 2
    if name in {"asts.py", "context_docs.py", "deps.py", "embedder.py", "grapher.py"}:
        return 3
    return 4


def _entrypoint_score(symbol: SymbolSnapshot) -> tuple[int, int, int, int, str]:
    """Score symbols for summary entrypoint selection."""

    base = 0
    if symbol.name in ENTRYPOINT_HINTS:
        base += 8
    if symbol.kind in {"class", "struct", "interface", "trait", "enum"}:
        base += 3
    if symbol.kind in {"function", "method"}:
        base += 6
    if symbol.parent is None:
        base += 5
    if symbol.file.endswith("cli.py") or symbol.file.endswith("mcp_server.py"):
        base += 3
    if symbol.file.endswith("coding_tools.py") or symbol.file.endswith("pipeline.py"):
        base += 4
    if symbol.name.startswith("_"):
        base -= 6
    if symbol.name in IGNORE_ENTRYPOINT_NAMES:
        base -= 10
    if symbol.name.startswith(("list_", "normalize_", "infer_", "guess_")):
        base -= 4
    return (-base, symbol.line, len(symbol.file), len(symbol.name), symbol.name)


def _context_symbol_sort_key(symbol: SymbolSnapshot) -> tuple[int, int, int, int, str, str]:
    """Sort the deep context table by usefulness instead of raw file order."""

    signal = _entrypoint_score(symbol)
    return (
        1 if symbol.name.startswith("_") else 0,
        _signal_file_priority(symbol.file),
        signal[0],
        symbol.line,
        symbol.file,
        symbol.name,
    )


def _select_key_entrypoints(snapshot: ModuleSnapshot, *, hot_index: HotIndex | None = None, limit: int = 6) -> list[str]:
    """Return key entrypoints for module summaries."""

    chosen: list[str] = []
    importance_by_name: dict[str, tuple[float, int]] = {}
    if hot_index:
        for symbol in snapshot.symbols:
            entry = hot_index.lookup(symbol.name)
            if entry:
                importance_by_name[symbol.name] = (entry.importance, entry.hit_count)

    def sort_key(symbol: SymbolSnapshot) -> tuple[float, int, int, int, int, str]:
        importance, hit_count = importance_by_name.get(symbol.name, (0.0, 0))
        score = _entrypoint_score(symbol)
        return (score[0] - (importance / 50.0), -hit_count, score[1], score[2], score[3], score[4])

    for symbol in sorted(snapshot.symbols, key=sort_key):
        if symbol.name.startswith("_") or symbol.name in IGNORE_ENTRYPOINT_NAMES:
            continue
        if symbol.name not in chosen:
            chosen.append(symbol.name)
        if len(chosen) >= limit:
            break
    return chosen


def _module_summary(snapshot: ModuleSnapshot) -> str:
    """Build a concise deterministic module summary."""

    file_count = len(snapshot.files)
    symbols = snapshot.symbols
    kind_counts: dict[str, int] = {}
    for symbol in symbols:
        kind_counts[symbol.kind] = kind_counts.get(symbol.kind, 0) + 1

    dominant = ", ".join(
        f"{count} {_pluralize_kind(kind, count)}"
        for kind, count in sorted(kind_counts.items(), key=lambda item: (-item[1], item[0]))[:3]
    )
    entrypoints = ", ".join(f"`{name}`" for name in snapshot.key_entrypoints[:5]) or "none"
    key_files = ", ".join(f"`{name}`" for name in snapshot.key_files[:4]) or "none"

    if snapshot.category == "know_when":
        return (
            f"Validation and example coverage for {file_count} file(s) with {snapshot.symbol_count} symbols. "
            f"Representative suites: {key_files}. Entry examples: {entrypoints}."
        )

    return (
        f"Implementation area with {file_count} file(s) and {snapshot.symbol_count} symbols "
        f"({dominant}). Key files: {key_files}. Key entry points: {entrypoints}."
    )


def _pluralize_kind(kind: str, count: int) -> str:
    """Return a readable kind label for summary prose."""

    if count == 1:
        return kind
    if kind == "class":
        return "classes"
    if kind.endswith("y"):
        return f"{kind[:-1]}ies"
    return f"{kind}s"


def _build_overview_lines(root: Path, know_how: list[ModuleSnapshot], know_when: list[ModuleSnapshot]) -> list[str]:
    """Build a concise overview from the ranked module groups."""

    lines: list[str] = []
    if know_how:
        primary = know_how[0]
        lines.append(primary.summary)
        lines.append("")
        lines.append(
            "Primary implementation areas: "
            + ", ".join(f"`{_module_label(snapshot, root)}`" for snapshot in know_how[:3])
            + "."
        )
    if know_when:
        lines.append(
            "Validation and examples live in: "
            + ", ".join(f"`{_module_label(snapshot, root)}`" for snapshot in know_when[:3])
            + "."
        )
    return lines


def _append_module_section(root: Path, lines: list[str], title: str, snapshots: list[ModuleSnapshot]) -> None:
    """Append a concise section of ranked modules."""

    if not snapshots:
        return

    lines.append(title)
    lines.append("")
    for snapshot in snapshots:
        lines.append(f"- **`{_module_label(snapshot, root)}`** ({snapshot.symbol_count} symbols) — {snapshot.summary}")
    lines.append("")


def _append_module_details(root: Path, lines: list[str], title: str, snapshots: list[ModuleSnapshot]) -> None:
    """Append detailed module summaries by section."""

    if not snapshots:
        return

    lines.append(f"### {title}")
    lines.append("")
    for snapshot in snapshots:
        lines.append(f"#### {_module_label(snapshot, root)}")
        lines.append("")
        lines.append(snapshot.summary)
        lines.append("")
    lines.append("")


def _format_signature(signature: str, max_width: int = 88) -> str:
    """Normalize and truncate signatures for Markdown tables."""

    normalized = " ".join(signature.replace("`", "'").split())
    if not normalized:
        return "-"

    truncated = _truncate_signature(normalized, max_width=max_width)
    escaped = truncated.replace("|", r"\|")
    if len(escaped) <= max_width:
        return escaped
    return _truncate_signature(escaped, max_width=max_width)


def _truncate_signature(signature: str, max_width: int) -> str:
    """Truncate on a readable boundary rather than mid-token where possible."""

    if len(signature) <= max_width:
        return signature

    budget = max_width - 4
    boundary = None
    for match in re.finditer(r"[\s,)\]}>]+", signature[: budget + 1]):
        boundary = match.end()
    cut = boundary or budget
    return f"{signature[:cut].rstrip()} ..."


def _symbol_location(symbol: SymbolSnapshot) -> str:
    """Return a concise location label for a symbol."""

    return f"{symbol.file}:{symbol.line}"


def _append_context_section(root: Path, lines: list[str], title: str, snapshots: list[ModuleSnapshot]) -> None:
    """Append grouped deep-context tables."""

    rendered = False
    for snapshot in snapshots:
        if not snapshot.symbols:
            continue
        if not rendered:
            lines.append(f"## {title}")
            lines.append("")
            rendered = True

        lines.append(f"### {_module_label(snapshot, root)}")
        lines.append("")
        lines.append("| Symbol | Kind | Location | Signature |")
        lines.append("|--------|------|----------|-----------|")
        for symbol in snapshot.symbols:
            lines.append(
                f"| `{symbol.name}` | {symbol.kind} | `{_symbol_location(symbol)}` | "
                f"`{_format_signature(symbol.signature)}` |"
            )
        lines.append("")


def _build_claude_md(root: Path, snapshots: list[ModuleSnapshot]) -> str:
    """Build TLDR.md — the file that makes LLMs immediately productive."""

    know_how = [snapshot for snapshot in snapshots if snapshot.category == "know_how"]
    know_when = [snapshot for snapshot in snapshots if snapshot.category == "know_when"]
    lines = [
        f"# {root.name}",
        "",
        "*Auto-generated by TLDREADME. Last indexed: now.*",
        "",
        "## Overview",
        "",
    ]

    lines.extend(_build_overview_lines(root, know_how, know_when))
    lines.append("")

    _append_module_section(root, lines, "## Know-How", know_how)
    _append_module_section(root, lines, "## Know-When / Examples", know_when)

    lines.append("## Module Details")
    lines.append("")
    _append_module_details(root, lines, "Know-How", know_how)
    _append_module_details(root, lines, "Know-When / Examples", know_when)

    return "\n".join(lines).rstrip() + "\n"


def _build_context_md(root: Path, snapshots: list[ModuleSnapshot]) -> str:
    """Build TLDR_CONTEXT.md — deeper module map with symbols and locations."""

    know_how = [snapshot for snapshot in snapshots if snapshot.category == "know_how"]
    know_when = [snapshot for snapshot in snapshots if snapshot.category == "know_when"]
    lines = [
        f"# {root.name} — Deep Context",
        "",
        "*Auto-generated by TLDREADME.*",
        "",
    ]

    _append_context_section(root, lines, "Know-How / Production", know_how)
    _append_context_section(root, lines, "Know-When / Examples", know_when)

    return "\n".join(lines).rstrip() + "\n"
