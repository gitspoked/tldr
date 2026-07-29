"""The init pipeline - scan, parse, embed, graph, generate."""

from pathlib import Path

from rich.console import Console

from .embedder import CodeEmbedder, symbols_to_chunks
from .generator import generate_claude_md
from .grapher import CodeGrapher
from .hot_index import build_hot_index
from .parser import parse_directory

console = Console()


def _stage_failure(stage: str, exc: Exception) -> dict[str, str]:
    """Render one operational failure without leaking a traceback."""

    detail = " ".join(str(exc).split())[:500] or type(exc).__name__
    console.print(f"  [yellow]{stage} skipped:[/] {detail}\n")
    return {
        "stage": stage,
        "error_type": type(exc).__name__,
        "detail": detail,
    }


def run_init(directory: Path, output_dir: str = ".claude"):
    """Full pipeline: parse → embed → graph → generate TLDR.md."""

    display_directory = directory
    directory = directory.resolve()
    warnings: list[dict[str, str]] = []

    console.print(f"\n[bold green]TLDREADME[/] initializing [bold]{display_directory}[/]\n")

    # 1. Parse
    console.print("[dim]Parsing code with tree-sitter...[/]")
    results = parse_directory(directory)
    total_symbols = sum(len(r.symbols) for r in results)
    total_files = len(results)
    total_lines = sum(r.line_count for r in results)
    console.print(
        f"  Found [bold]{total_symbols}[/] symbols in [bold]{total_files}[/] files ({total_lines:,} lines)\n"
    )

    if not results:
        console.print("[yellow]No parseable code found.[/]")
        return {"status": "empty", "warnings": []}

    # 2. Embed into Qdrant
    console.print("[dim]Embedding into Qdrant...[/]")
    chunks = symbols_to_chunks(results, repo_root=directory)
    try:
        embedder = CodeEmbedder()
        embedder.index_chunks(
            chunks,
            replace_repository=True,
            repo_root=directory,
        )
        console.print(f"  Embedded [bold]{len(chunks)}[/] code chunks\n")
    except Exception as exc:
        warnings.append(_stage_failure("Qdrant embedding", exc))

    # 3. Build graph in FalkorDB
    console.print("[dim]Building knowledge graph in FalkorDB...[/]")
    try:
        grapher = CodeGrapher()
        grapher.index_results(results)
        total_calls = sum(len(r.calls) for r in results)
        total_imports = sum(len(r.imports) for r in results)
        console.print(
            f"  Graphed [bold]{total_calls}[/] call edges, [bold]{total_imports}[/] imports\n"
        )
    except Exception as exc:
        warnings.append(_stage_failure("FalkorDB graph", exc))

    # 4. Build hot index (top 100 symbols cached for instant lookup)
    console.print("[dim]Building hot index...[/]")
    hot_idx = build_hot_index(directory, results)
    hot_path = directory / ".tldr"
    hot_path.mkdir(exist_ok=True)
    hot_idx.save(hot_path / "hot_index.json")
    console.print(f"  Cached [bold]{len(hot_idx.entries)}[/] hot symbols\n")

    # 5. Generate TLDR.md
    console.print("[dim]Generating context files...[/]")
    claude_path = generate_claude_md(
        directory, output_dir=output_dir, parse_results=results, hot_index=hot_idx
    )
    console.print(f"  Written: [bold]{claude_path}[/]\n")

    # Summary
    if warnings:
        console.print(
            f"[bold yellow]Done with {len(warnings)} warning(s).[/] "
            "Local context files were generated.\n"
        )
    else:
        console.print("[bold green]Done.[/] Codebase indexed.\n")
    console.print("  MCP server:  [dim]tldr serve[/]")
    console.print(f"  Watch mode:  [dim]tldr watch {directory}[/]")
    console.print('  Ask:         [dim]tldr ask "how does X work?"[/]')
    console.print()
    return {
        "status": "degraded" if warnings else "complete",
        "warnings": warnings,
        "symbols": total_symbols,
        "files": total_files,
    }
