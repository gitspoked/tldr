"""Tree-sitter AST parser — extracts symbols, calls, imports, data flow."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import sys
import tree_sitter_languages as tsl

sys.setrecursionlimit(5000)


@dataclass
class Symbol:
    """A function, class, struct, method, or constant."""
    name: str
    kind: str                      # function, class, struct, method, trait, const, enum
    file: str
    line: int
    end_line: int
    body: str                      # actual source code
    signature: str                 # just the signature (for quick display)
    docstring: Optional[str] = None
    parent: Optional[str] = None      # enclosing class/struct/impl
    language: str = ""


@dataclass
class Import:
    """An import/use/require statement."""
    source: str                    # what's being imported
    target: str                    # local name / alias
    file: str
    line: int


@dataclass
class CallSite:
    """A function/method call."""
    caller: str                    # who's calling
    callee: str                    # what's being called
    file: str
    line: int
    arguments: list[str] = field(default_factory=list)


@dataclass
class Dependency:
    """An external dependency declared in a manifest file."""
    name: str
    version: str               # version constraint or resolved version
    source_file: str           # Cargo.toml, package.json, etc.
    kind: str                  # "runtime", "dev", "build", "optional", "peer"
    features: list[str] = field(default_factory=list)  # Cargo feature flags
    registry: str = ""         # "crates.io", "npm", "pypi", "go"


@dataclass
class ContextDoc:
    """A documentation file that provides human intent and project context.

    CLAUDE.md, README.md, CONTEXT.md, ARCHITECTURE.md, etc.
    The code says WHAT, these say WHY.
    """
    file: str
    kind: str                  # "claude", "readme", "context", "architecture", "changelog", "other"
    title: str                 # first heading or filename
    content: str               # full text
    sections: list[dict]       # [{heading, content, line}] — parsed by headings
    project_root: str          # which project this belongs to (nearest dir with manifest)


@dataclass
class ParseResult:
    """Everything extracted from a single file."""
    file: str
    language: str
    symbols: list[Symbol]
    imports: list[Import]
    calls: list[CallSite]
    raw_source: str
    line_count: int


@dataclass
class ProjectDeps:
    """All dependencies for a project, extracted from manifest files."""
    manifest_file: str         # path to Cargo.toml / package.json / etc.
    project_name: str
    project_version: str
    dependencies: list[Dependency]


# Language detection by extension
#
# Core four (primary):  typescript, javascript, python, rust
# Supported (secondary): go, c, cpp, php, java, ruby, swift, kotlin, lua, zig
#
LANG_MAP = {
    # ── Core four ──
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".py": "python", ".pyi": "python",
    ".rs": "rust",
    # ── Secondary ──
    ".go": "go",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".php": "php",
    ".java": "java",
    ".rb": "ruby",
    ".swift": "swift",
    ".kt": "kotlin",
    ".lua": "lua",
    ".zig": "zig",
    ".json": "json",
    ".md": "markdown",
}


def detect_language(path: Path) -> Optional[str]:
    return LANG_MAP.get(path.suffix.lower())


def parse_file(path: Path) -> ParseResult | None:
    """Parse a single file and extract all symbols, imports, calls."""
    lang = detect_language(path)
    if not lang:
        return None

    try:
        source = path.read_text(errors="replace")
    except (OSError, UnicodeDecodeError):
        return None

    try:
        parser = tsl.get_parser(lang)
        tree = parser.parse(source.encode())
    except Exception:
        return None

    try:
        symbols = _extract_symbols(tree.root_node, source, str(path), lang)
        imports = _extract_imports(tree.root_node, source, str(path), lang)
        calls = _extract_calls(tree.root_node, source, str(path), lang)
    except RecursionError:
        return None  # skip deeply nested generated files

    return ParseResult(
        file=str(path),
        language=lang,
        symbols=symbols,
        imports=imports,
        calls=calls,
        raw_source=source,
        line_count=source.count("\n") + 1,
    )


def parse_directory(root: Path, exclude: Optional[set] = None, follow_symlinks: bool = False) -> list[ParseResult]:
    """Recursively parse all files in a directory."""
    if exclude is None:
        exclude = {"node_modules", ".git", "__pycache__", "target", ".venv", "venv", "dist", "build"}

    results = []
    for path in root.rglob("*"):
        if not follow_symlinks and path.is_symlink():
            continue
        if path.is_file() and not any(ex in path.parts for ex in exclude):
            result = parse_file(path)
            if result and result.symbols:
                results.append(result)
    return results


def _extract_symbols(node, source: str, file: str, lang: str) -> list[Symbol]:
    """Walk AST and extract function/class/struct definitions."""
    symbols = []

    # Node types that ARE symbols (we extract them as named entities)
    #
    # Core four (primary):  typescript, javascript, python, rust
    # Secondary:            go, c, cpp, php, java, ruby, swift, kotlin, lua, zig
    #
    symbol_types = {
        # ── Core four ──
        "typescript": ["function_declaration", "class_declaration", "interface_declaration",
                       "type_alias_declaration", "enum_declaration", "method_definition",
                       "arrow_function", "lexical_declaration"],
        "javascript": ["function_declaration", "class_declaration", "method_definition",
                       "arrow_function"],
        "python": ["function_definition", "class_definition"],
        "rust": ["function_item", "struct_item", "enum_item", "trait_item", "const_item",
                 "static_item", "type_item", "macro_definition"],
        # ── Secondary ──
        "go": ["function_declaration", "method_declaration", "type_declaration"],
        "c": ["function_definition", "struct_specifier", "enum_specifier", "type_definition"],
        "cpp": ["function_definition", "class_specifier", "struct_specifier", "enum_specifier",
                "namespace_definition", "template_declaration"],
        "php": ["function_definition", "class_declaration", "method_declaration",
                "interface_declaration", "trait_declaration"],
        "java": ["method_declaration", "class_declaration", "interface_declaration",
                 "enum_declaration"],
        "ruby": ["method", "class", "module", "singleton_method"],
        "swift": ["function_declaration", "class_declaration", "struct_declaration",
                  "enum_declaration", "protocol_declaration"],
        "kotlin": ["function_declaration", "class_declaration", "object_declaration",
                   "interface_declaration"],
        "lua": ["function_declaration", "local_function_declaration_statement"],
        "zig": ["fn_decl", "var_decl"],
    }

    # Node types that are CONTAINERS — we recurse into them to find symbols,
    # and they set the parent name. Rust `impl` blocks are the key case:
    # impl Foo { fn bar() {} } → bar's parent is "Foo"
    container_types = {
        # ── Core four ──
        "rust": ["impl_item", "trait_item"],
        "python": ["class_definition"],
        "typescript": ["class_declaration"],
        "javascript": ["class_declaration"],
        # ── Secondary ──
        "go": [],
        "c": ["struct_specifier"],
        "cpp": ["class_specifier", "struct_specifier", "namespace_definition"],
        "php": ["class_declaration", "trait_declaration"],
        "java": ["class_declaration"],
        "ruby": ["class", "module"],
        "swift": ["class_declaration", "struct_declaration"],
        "kotlin": ["class_declaration", "object_declaration"],
    }

    target_symbols = set(symbol_types.get(lang, []))
    target_containers = set(container_types.get(lang, []))

    def _get_impl_name(n) -> str:
        """Extract the type name from a Rust impl block: `impl Foo` or `impl Trait for Foo`."""
        # impl_item children: optional type_params, type (the target), optional trait, body
        type_node = n.child_by_field_name("type")
        trait_node = n.child_by_field_name("trait")
        if type_node:
            type_name = type_node.text.decode()
            if trait_node:
                trait_name = trait_node.text.decode()
                return f"{trait_name} for {type_name}"
            return type_name
        return "<impl>"

    def walk(n, parent_name=None):
        if n.type in target_symbols:
            name_node = n.child_by_field_name("name")
            name = name_node.text.decode() if name_node else "<anonymous>"
            body = source[n.start_byte:n.end_byte]
            sig = body.split("\n")[0].strip()

            # For Rust methods inside impl, use "Type::method" as the name
            qualified_name = f"{parent_name}::{name}" if parent_name and lang == "rust" else name

            kind = n.type
            for suffix in ("_definition", "_declaration", "_item"):
                kind = kind.replace(suffix, "")

            symbols.append(Symbol(
                name=qualified_name,
                kind=kind,
                file=file,
                line=n.start_point[0] + 1,
                end_line=n.end_point[0] + 1,
                body=body,
                signature=sig,
                parent=parent_name,
                language=lang,
            ))
            # Recurse into the symbol (e.g., nested functions, inner classes)
            for child in n.children:
                walk(child, parent_name=qualified_name)

        elif n.type in target_containers:
            # Container: extract its name, then recurse with it as parent
            if n.type == "impl_item":
                container_name = _get_impl_name(n)
            else:
                name_node = n.child_by_field_name("name")
                container_name = name_node.text.decode() if name_node else parent_name

            # Also register the container itself as a symbol (struct via impl, class, trait)
            if n.type not in target_symbols:
                body = source[n.start_byte:n.end_byte]
                sig = body.split("\n")[0].strip()
                kind = n.type.replace("_definition", "").replace("_declaration", "").replace("_item", "")
                symbols.append(Symbol(
                    name=container_name or "<anonymous>",
                    kind=kind,
                    file=file,
                    line=n.start_point[0] + 1,
                    end_line=n.end_point[0] + 1,
                    body=body[:500],  # truncate impl blocks (can be huge)
                    signature=sig,
                    parent=parent_name,
                    language=lang,
                ))

            for child in n.children:
                walk(child, parent_name=container_name)
        else:
            for child in n.children:
                walk(child, parent_name=parent_name)

    walk(node)
    return symbols


def _extract_imports(node, source: str, file: str, lang: str) -> list[Import]:
    """Extract import statements."""
    imports = []
    import_types = {
        # ── Core four ──
        "typescript": ["import_statement"],
        "javascript": ["import_statement"],
        "python": ["import_statement", "import_from_statement"],
        "rust": ["use_declaration"],
        # ── Secondary ──
        "go": ["import_declaration"],
        "c": ["preproc_include"],
        "cpp": ["preproc_include", "using_declaration"],
        "php": ["namespace_use_declaration"],
        "java": ["import_declaration"],
        "ruby": ["call"],  # require/require_relative are calls in Ruby AST
        "swift": ["import_declaration"],
        "kotlin": ["import_header"],
    }
    target_types = import_types.get(lang, [])

    def walk(n):
        if n.type in target_types:
            text = source[n.start_byte:n.end_byte]
            imports.append(Import(
                source=text,
                target=text,  # simplified — real impl would parse the from/as
                file=file,
                line=n.start_point[0] + 1,
            ))
        for child in n.children:
            walk(child)

    walk(node)
    return imports


def _extract_calls(node, source: str, file: str, lang: str) -> list[CallSite]:
    """Extract function/method call sites."""
    calls = []

    def walk(n, enclosing_fn=None):
        if n.type == "call_expression" or n.type == "call":
            fn_node = n.child_by_field_name("function") or (n.children[0] if n.children else None)
            callee = fn_node.text.decode() if fn_node else "<unknown>"
            calls.append(CallSite(
                caller=enclosing_fn or "<module>",
                callee=callee,
                file=file,
                line=n.start_point[0] + 1,
            ))

        # Track which function we're inside
        if n.type in ("function_definition", "function_item", "function_declaration", "method_declaration"):
            name_node = n.child_by_field_name("name")
            name = name_node.text.decode() if name_node else enclosing_fn
            for child in n.children:
                walk(child, enclosing_fn=name)
        else:
            for child in n.children:
                walk(child, enclosing_fn=enclosing_fn)

    walk(node)
    return calls


# ── Dependency Extraction ──────────────────────────────────────────
#
# Strategy: scan your code, catalog your deps, fetch docs on demand.
#
# We NEVER parse node_modules/, target/, .venv/, or vendor/ source.
# Instead we extract dependency metadata from manifest files:
#   - Cargo.toml      → Rust crates (with features, optional deps)
#   - package.json    → npm packages (deps, devDeps, peerDeps)
#   - go.mod          → Go modules
#   - pyproject.toml  → Python packages
#   - requirements.txt→ Python packages (fallback)
#
# These become Dependency nodes in FalkorDB:
#   (File)-[:DEPENDS_ON]->(Crate {name, version, registry})
#
# For deep understanding of a dep, use LiteLLM + context7 docs
# lookup at query time — don't pre-index library source.


def extract_deps_from_directory(root: Path) -> list[ProjectDeps]:
    """Find all manifest files and extract dependencies."""
    all_deps = []

    for cargo in root.rglob("Cargo.toml"):
        if any(skip in cargo.parts for skip in ("target", ".git", "node_modules", "retired")):
            continue
        result = _parse_cargo_toml(cargo)
        if result:
            all_deps.append(result)

    for pkg in root.rglob("package.json"):
        if any(skip in pkg.parts for skip in ("node_modules", ".git", "target", "dist")):
            continue
        result = _parse_package_json(pkg)
        if result:
            all_deps.append(result)

    for gomod in root.rglob("go.mod"):
        if any(skip in gomod.parts for skip in (".git", "vendor")):
            continue
        result = _parse_go_mod(gomod)
        if result:
            all_deps.append(result)

    for pyproj in root.rglob("pyproject.toml"):
        if any(skip in pyproj.parts for skip in (".git", ".venv", "venv")):
            continue
        result = _parse_pyproject_toml(pyproj)
        if result:
            all_deps.append(result)

    for reqs in root.rglob("requirements.txt"):
        if any(skip in reqs.parts for skip in (".git", ".venv", "venv")):
            continue
        result = _parse_requirements_txt(reqs)
        if result:
            all_deps.append(result)

    return all_deps


def _parse_cargo_toml(path: Path) -> Optional[ProjectDeps]:
    """Extract deps from Cargo.toml."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # Python < 3.11 fallback
        except ImportError:
            return None

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return None

    pkg = data.get("package", {})
    project_name = pkg.get("name", path.parent.name)
    project_version = pkg.get("version", "0.0.0")

    deps = []

    # [dependencies]
    for name, spec in data.get("dependencies", {}).items():
        version, features, optional = _parse_cargo_dep_spec(spec)
        deps.append(Dependency(
            name=name, version=version, source_file=str(path),
            kind="optional" if optional else "runtime",
            features=features, registry="crates.io",
        ))

    # [dev-dependencies]
    for name, spec in data.get("dev-dependencies", {}).items():
        version, features, _ = _parse_cargo_dep_spec(spec)
        deps.append(Dependency(
            name=name, version=version, source_file=str(path),
            kind="dev", features=features, registry="crates.io",
        ))

    # [build-dependencies]
    for name, spec in data.get("build-dependencies", {}).items():
        version, features, _ = _parse_cargo_dep_spec(spec)
        deps.append(Dependency(
            name=name, version=version, source_file=str(path),
            kind="build", features=features, registry="crates.io",
        ))

    return ProjectDeps(
        manifest_file=str(path),
        project_name=project_name,
        project_version=project_version,
        dependencies=deps,
    )


def _parse_cargo_dep_spec(spec) -> tuple:
    """Parse a Cargo dependency spec (string or table)."""
    if isinstance(spec, str):
        return spec, [], False
    elif isinstance(spec, dict):
        version = spec.get("version", spec.get("path", "path"))
        features = spec.get("features", [])
        optional = spec.get("optional", False)
        return version, features, optional
    return "unknown", [], False


def _parse_package_json(path: Path) -> Optional[ProjectDeps]:
    """Extract deps from package.json."""
    import json
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None

    project_name = data.get("name", path.parent.name)
    project_version = data.get("version", "0.0.0")

    deps = []

    for name, version in data.get("dependencies", {}).items():
        deps.append(Dependency(
            name=name, version=version, source_file=str(path),
            kind="runtime", registry="npm",
        ))

    for name, version in data.get("devDependencies", {}).items():
        deps.append(Dependency(
            name=name, version=version, source_file=str(path),
            kind="dev", registry="npm",
        ))

    for name, version in data.get("peerDependencies", {}).items():
        deps.append(Dependency(
            name=name, version=version, source_file=str(path),
            kind="peer", registry="npm",
        ))

    return ProjectDeps(
        manifest_file=str(path),
        project_name=project_name,
        project_version=project_version,
        dependencies=deps,
    )


def _parse_go_mod(path: Path) -> Optional[ProjectDeps]:
    """Extract deps from go.mod."""
    try:
        text = path.read_text()
    except Exception:
        return None

    project_name = ""
    deps = []
    in_require = False

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("module "):
            project_name = line.split("module ", 1)[1].strip()
        elif line == "require (":
            in_require = True
        elif line == ")" and in_require:
            in_require = False
        elif in_require and line and not line.startswith("//"):
            parts = line.split()
            if len(parts) >= 2:
                name, version = parts[0], parts[1]
                indirect = "// indirect" in line
                deps.append(Dependency(
                    name=name, version=version, source_file=str(path),
                    kind="indirect" if indirect else "runtime",
                    registry="go",
                ))
        elif line.startswith("require ") and "(" not in line:
            parts = line.split()
            if len(parts) >= 3:
                deps.append(Dependency(
                    name=parts[1], version=parts[2], source_file=str(path),
                    kind="runtime", registry="go",
                ))

    return ProjectDeps(
        manifest_file=str(path),
        project_name=project_name,
        project_version="",
        dependencies=deps,
    )


def _parse_pyproject_toml(path: Path) -> Optional[ProjectDeps]:
    """Extract deps from pyproject.toml."""
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return None

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return None

    project = data.get("project", {})
    project_name = project.get("name", path.parent.name)
    project_version = project.get("version", "0.0.0")

    deps = []

    for dep_str in project.get("dependencies", []):
        name, version = _parse_pep508(dep_str)
        deps.append(Dependency(
            name=name, version=version, source_file=str(path),
            kind="runtime", registry="pypi",
        ))

    for group_name, group_deps in project.get("optional-dependencies", {}).items():
        for dep_str in group_deps:
            name, version = _parse_pep508(dep_str)
            deps.append(Dependency(
                name=name, version=version, source_file=str(path),
                kind=f"optional-{group_name}", registry="pypi",
            ))

    return ProjectDeps(
        manifest_file=str(path),
        project_name=project_name,
        project_version=project_version,
        dependencies=deps,
    )


def _parse_requirements_txt(path: Path) -> Optional[ProjectDeps]:
    """Extract deps from requirements.txt."""
    try:
        text = path.read_text()
    except Exception:
        return None

    deps = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name, version = _parse_pep508(line)
        deps.append(Dependency(
            name=name, version=version, source_file=str(path),
            kind="runtime", registry="pypi",
        ))

    return ProjectDeps(
        manifest_file=str(path),
        project_name=path.parent.name,
        project_version="",
        dependencies=deps,
    )


def _parse_pep508(dep_str: str) -> tuple:
    """Parse a PEP 508 dependency string into (name, version)."""
    import re
    match = re.match(r'^([A-Za-z0-9_.-]+)\s*(.*)', dep_str)
    if match:
        return match.group(1), match.group(2).strip() or "*"
    return dep_str, "*"


# ── Context Document Scanner ──────────────────────────────────────
#
# Scans for CLAUDE.md, README.md, CONTEXT.md, ARCHITECTURE.md, etc.
# These are the "why" files — human intent, project structure,
# build commands, design decisions. The code tells you WHAT,
# these tell you WHY.
#
# Indexes every project's docs so the LLM has the full context
# landscape without reading every file.

# Files we always want to capture, in priority order
CONTEXT_DOC_NAMES = {
    # AI assistant context
    "CLAUDE.md": "claude",
    "claude.md": "claude",
    "AGENTS.md": "agents",
    "agents.md": "agents",
    "GEMINI.md": "gemini",
    "TLDREADME.md": "tldreadme",
    "TLDR.md": "tldr",
    # Project docs
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


def scan_context_docs(root: Path, exclude: Optional[set] = None, follow_symlinks: bool = False) -> list[ContextDoc]:
    """Scan a directory tree for all context/documentation files.

    Finds CLAUDE.md, README.md, CONTEXT.md, ARCHITECTURE.md, etc.
    at every level of the tree. Each doc is parsed into sections
    by heading for structured retrieval.

    Gives you every project's intent without reading a single
    line of code.

    Args:
        follow_symlinks: If False (default), skip symlinked files and dirs
                         to avoid loops and scanning outside the tree.
    """
    if exclude is None:
        exclude = {"node_modules", ".git", "__pycache__", "target", ".venv", "venv", "dist", "build"}

    docs = []
    for path in root.rglob("*.md"):
        if not follow_symlinks and path.is_symlink():
            continue
        if any(ex in path.parts for ex in exclude):
            continue

        name = path.name
        kind = CONTEXT_DOC_NAMES.get(name)

        # Also capture any .md in a .claude/ directory
        if kind is None and ".claude" in path.parts:
            kind = "claude"

        # Skip random markdown files that aren't project docs
        if kind is None:
            continue

        try:
            content = path.read_text(errors="replace")
        except OSError:
            continue

        # Skip empty files
        if not content.strip():
            continue

        # Parse into sections by heading
        sections = _parse_markdown_sections(content)

        # Find the nearest project root (dir with Cargo.toml, package.json, etc.)
        project_root = _find_project_root(path.parent)

        # Title = first heading, or filename
        title = name
        if sections and sections[0].get("heading"):
            title = sections[0]["heading"]

        docs.append(ContextDoc(
            file=str(path),
            kind=kind,
            title=title,
            content=content,
            sections=sections,
            project_root=str(project_root),
        ))

    return docs


def _parse_markdown_sections(content: str) -> list[dict]:
    """Split markdown into sections by heading."""
    sections = []
    current_heading = None
    current_lines = []
    current_line = 0

    for i, line in enumerate(content.splitlines(), 1):
        if line.startswith("#"):
            # Flush previous section
            if current_heading is not None or current_lines:
                sections.append({
                    "heading": current_heading,
                    "content": "\n".join(current_lines).strip(),
                    "line": current_line,
                })
            current_heading = line.lstrip("#").strip()
            current_lines = []
            current_line = i
        else:
            current_lines.append(line)

    # Flush last section
    if current_heading is not None or current_lines:
        sections.append({
            "heading": current_heading,
            "content": "\n".join(current_lines).strip(),
            "line": current_line,
        })

    return sections


def _find_project_root(directory: Path) -> Path:
    """Walk up to find the nearest directory with a manifest file."""
    manifest_names = {"Cargo.toml", "package.json", "go.mod", "pyproject.toml", "setup.py"}
    current = directory
    while current != current.parent:
        if any((current / m).exists() for m in manifest_names):
            return current
        current = current.parent
    return directory  # fallback to the directory itself
