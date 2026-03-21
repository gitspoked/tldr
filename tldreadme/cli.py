"""CLI entry point — tldr init|watch|serve|ask"""

import click
from pathlib import Path


@click.group()
def main():
    """TLDREADME — TL;DR for any codebase."""
    pass


@main.command()
@click.argument("directory", type=click.Path(exists=True))
@click.option("--output", "-o", default=".claude", help="Output dir for generated context files")
def init(directory: str, output: str):
    """Scan a directory, index everything, generate TLDR.md.

    Parses all code via tree-sitter, embeds into Qdrant, builds
    call/import/data-flow graphs in FalkorDB, then generates
    context files that make any LLM immediately understand the codebase.
    """
    from .pipeline import run_init
    run_init(Path(directory), output_dir=output)


@main.command()
@click.argument("directories", nargs=-1, type=click.Path(exists=True))
def watch(directories: tuple[str, ...]):
    """Watch directories for changes and re-index incrementally.

    On file save: re-parse changed file's AST, update embeddings
    in Qdrant, update graph edges in FalkorDB, regenerate affected
    TLDR.md sections.
    """
    from .watcher import start_watcher
    start_watcher([Path(d) for d in directories])


@main.command()
@click.option("--port", "-p", default=8900, help="MCP server port")
def serve(port: int):
    """Start the MCP server. Claude Code connects here to KNOW the code."""
    from .mcp_server import start_server
    start_server(port=port)


@main.command()
@click.argument("question")
@click.option("--directory", "-d", type=click.Path(exists=True), help="Scope to directory")
def ask(question: str, directory: str | None):
    """Ask a question about the indexed codebase. RAG-powered answer."""
    from .rag import ask_question
    answer = ask_question(question, scope=directory)
    click.echo(answer)


if __name__ == "__main__":
    main()
