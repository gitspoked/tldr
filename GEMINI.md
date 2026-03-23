# Project Overview

This is a Python project named `tldreadme`. It's a command-line tool designed to provide a "TL;DR" for any codebase. It analyzes a directory of code, parses it using `tree-sitter`, embeds the symbols into a `Qdrant` vector database, and builds a knowledge graph of the code's relationships in `FalkorDB`.

The primary goal is to give a large language model (LLM) full codebase context. It can be used locally with `Ollama` or with cloud providers like OpenAI and Anthropic.

The project provides a CLI for humans and an MCP (Machine-Centric Protocol) server for LLMs to interact with the indexed codebase.

## Building and Running

The project uses Python 3.11+ and Docker.

### Prerequisites

- Python 3.11+ (3.12 recommended)
- Docker
- `ripgrep`

### Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/ntele-dev/tldreadme.git
    cd tldreadme
    ```
2.  Create and activate a virtual environment:
    ```bash
    python3.12 -m venv .venv
    source .venv/bin/activate
    ```
3.  Install dependencies:
    ```bash
    pip install -e '.[dev]'
    ```

### Running the Tool

**Start Infrastructure:**

The core services (Qdrant, FalkorDB, etc.) run in Docker.

```bash
docker compose up -d
```

If using local LLMs with Ollama, you'll need to pull the models:

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:3b-instruct
```

**Index a Codebase:**

To analyze a project, use the `init` command:

```bash
tldr init /path/to/your/project
```

This will parse, embed, graph, and generate summary files for the target codebase.

**Run the MCP Server:**

To expose the codebase tools to an LLM, start the MCP server:

```bash
tldr serve
```

This will start a server that an LLM like Gemini can connect to for codebase-aware tasks.

**Run Tests:**

The project uses `pytest` for testing.

```bash
pytest
```

To run the critical "bedrock" contract tests:

```bash
python -m pytest -m bedrock -q
```

## Development Conventions

-   **CLI:** The main command-line interface is built with the `click` library and is defined in `tldreadme/cli.py`.
-   **Core Pipeline:** The main indexing logic is in `tldreadme/pipeline.py`, which orchestrates parsing, embedding, and graph creation.
-   **LLM Integration:** The `tldreadme/mcp_server.py` file is crucial. It creates an MCP server that exposes a rich set of tools and resources for LLMs. It has different "tool profiles" (`router` and `full`) to expose varying levels of tool complexity.
-   **Code Parsing:** The project uses `tree-sitter` for parsing a wide variety of programming languages. The parsing logic is in `tldreadme/asts.py`.
-   **Dependencies:** Project dependencies are managed in `pyproject.toml`.
-   **Lazy Loading:** The `tldreadme.lazy` module is used to defer the import of heavy modules like `rag` and `lsp` until they are actually needed, which improves startup performance.
-   **Configuration:** The project uses `.env` files for configuration, with `.env.example` as a template. `litellm-config.yaml` is used for configuring the LLM provider.
