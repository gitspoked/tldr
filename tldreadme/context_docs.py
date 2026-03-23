"""Context and documentation file scanning."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ContextDoc:
    """A documentation file that provides human intent and project context."""

    file: str
    kind: str
    title: str
    content: str
    sections: list[dict]
    project_root: str


CONTEXT_DOC_NAMES = {
    "CLAUDE.md": "claude",
    "claude.md": "claude",
    "CODEX.md": "codex",
    "codex.md": "codex",
    "AGENTS.md": "agents",
    "agents.md": "agents",
    "GEMINI.md": "gemini",
    "gemini.md": "gemini",
    "TLDROADMAP.md": "roadmap",
    "tldroadmap.md": "roadmap",
    "TLDRNOTES.md": "notes",
    "tldrnotes.md": "notes",
    "TLDRPLANS.md": "plans",
    "tldrplans.md": "plans",
    "TLDREADME.md": "tldreadme",
    "TLDR.md": "tldr",
    "README.md": "readme",
    "readme.md": "readme",
    "CONTEXT.md": "context",
    "ARCHITECTURE.md": "architecture",
    "DESIGN.md": "architecture",
    "CONTRIBUTING.md": "contributing",
    "CHANGELOG.md": "changelog",
    "DEVELOPMENT.md": "development",
    "SETUP.md": "setup",
    "USAGE.md": "usage",
    "QUICKSTART.md": "setup",
    "API.md": "api",
}


def scan_context_docs(
    root: Path,
    exclude: Optional[set] = None,
    follow_symlinks: bool = False,
) -> list[ContextDoc]:
    """Scan a directory tree for context and documentation markdown files."""

    if exclude is None:
        exclude = {"node_modules", ".git", "__pycache__", "target", ".venv", "venv", "dist", "build"}

    docs: list[ContextDoc] = []
    for path in root.rglob("*.md"):
        if not follow_symlinks and path.is_symlink():
            continue
        if any(ex in path.parts for ex in exclude):
            continue

        name = path.name
        kind = CONTEXT_DOC_NAMES.get(name)
        if kind is None and ".claude" in path.parts:
            kind = "claude"
        if kind is None:
            continue

        try:
            content = path.read_text(errors="replace")
        except OSError:
            continue

        if not content.strip():
            continue

        sections = _parse_markdown_sections(content)
        project_root = _find_project_root(path.parent)
        title = sections[0]["heading"] if sections and sections[0].get("heading") else name

        docs.append(
            ContextDoc(
                file=str(path),
                kind=kind,
                title=title,
                content=content,
                sections=sections,
                project_root=str(project_root),
            )
        )

    return docs


def _parse_markdown_sections(content: str) -> list[dict]:
    """Split markdown into sections by heading."""

    sections: list[dict] = []
    current_heading = None
    current_lines: list[str] = []
    current_line = 0

    for i, line in enumerate(content.splitlines(), 1):
        if line.startswith("#"):
            if current_heading is not None or current_lines:
                sections.append(
                    {
                        "heading": current_heading,
                        "content": "\n".join(current_lines).strip(),
                        "line": current_line,
                    }
                )
            current_heading = line.lstrip("#").strip()
            current_lines = []
            current_line = i
        else:
            current_lines.append(line)

    if current_heading is not None or current_lines:
        sections.append(
            {
                "heading": current_heading,
                "content": "\n".join(current_lines).strip(),
                "line": current_line,
            }
        )

    return sections


def _find_project_root(directory: Path) -> Path:
    """Walk up to find the nearest directory with a project manifest."""

    manifest_names = {"Cargo.toml", "package.json", "go.mod", "pyproject.toml", "setup.py"}
    current = directory
    while current != current.parent:
        if any((current / name).exists() for name in manifest_names):
            return current
        current = current.parent
    return directory
