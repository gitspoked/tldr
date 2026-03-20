"""The init pipeline — scan, parse, embed, graph, generate."""

from pathlib import Path
from rich.console import Console
from rich.progress import Progress

from .parser import parse_directory
from .embedder import CodeEmbedder, symbols_to_chunks
from .grapher import CodeGrapher
from .generator import generate_claude_md

console = Console()


def run_init(directory: Path, output_dir: str = ".claude"):
    """Full pipeline: parse → embed → graph → generate TLDR.md."""

    console.print(f"\n[bold green]TLDREADME[/] initializing [bold]{directory}[/]\n")

    # 1. Parse
    console.print("[dim]Parsing code with tree-sitter...[/]")
    results = parse_directory(directory)
    total_symbols = sum(len(r.symbols) for r in results)
    total_files = len(results)
    total_lines = sum(r.line_count for r in results)
    console.print(f"  Found [bold]{total_symbols}[/] symbols in [bold]{total_files}[/] files ({total_lines:,} lines)\n")

    if not results:
        console.print("[yellow]No parseable code found.[/]")
        return

    # 2. Embed into Qdrant
    console.print("[dim]Embedding into Qdrant...[/]")
    embedder = CodeEmbedder()
    chunks = symbols_to_chunks(results)
    embedder.index_chunks(chunks)
    console.print(f"  Embedded [bold]{len(chunks)}[/] code chunks\n")

    # 3. Build graph in FalkorDB
    console.print("[dim]Building knowledge graph in FalkorDB...[/]")
    grapher = CodeGrapher()
    grapher.index_results(results)
    total_calls = sum(len(r.calls) for r in results)
    total_imports = sum(len(r.imports) for r in results)
    console.print(f"  Graphed [bold]{total_calls}[/] call edges, [bold]{total_imports}[/] imports\n")

    # 4. Generate TLDR.md
    console.print("[dim]Generating context files...[/]")
    claude_path = generate_claude_md(directory, output_dir=output_dir)
    console.print(f"  Written: [bold]{claude_path}[/]\n")

    # Summary
    console.print("[bold green]Done.[/] Your codebase is now KNOWN.\n")
    console.print(f"  MCP server:  [dim]tldreadme serve[/]")
    console.print(f"  Watch mode:  [dim]tldreadme watch {directory}[/]")
    console.print(f"  Ask:         [dim]tldreadme ask \"how does X work?\"[/]")
    console.print()
