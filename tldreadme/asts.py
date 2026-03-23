"""Tree-sitter AST parsing for symbols, imports, and call sites."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import sys
import warnings

from .runtime import get_tree_sitter_languages

sys.setrecursionlimit(5000)

# `tree_sitter_languages==1.10.2` still triggers this upstream deprecation warning
# internally even with the pinned, supported runtime pair.
warnings.filterwarnings(
    "ignore",
    message=r"Language\(path, name\) is deprecated\. Use Language\(ptr, name\) instead\.",
    category=FutureWarning,
    module="tree_sitter",
)


@dataclass
class Symbol:
    """A function, class, struct, method, or constant."""

    name: str
    kind: str
    file: str
    line: int
    end_line: int
    body: str
    signature: str
    docstring: Optional[str] = None
    parent: Optional[str] = None
    language: str = ""


@dataclass
class Import:
    """An import/use/require statement."""

    source: str
    target: str
    file: str
    line: int


@dataclass
class CallSite:
    """A function or method call."""

    caller: str
    callee: str
    file: str
    line: int
    arguments: list[str] = field(default_factory=list)


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


LANG_MAP = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
    ".pyi": "python",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
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
    """Return the supported language for a file path."""

    return LANG_MAP.get(path.suffix.lower())


def parse_file(path: Path) -> ParseResult | None:
    """Parse a single file and extract all symbols, imports, and calls."""

    lang = detect_language(path)
    if not lang:
        return None

    try:
        source = path.read_text(errors="replace")
    except (OSError, UnicodeDecodeError):
        return None

    try:
        source_bytes = source.encode()
        parser = get_tree_sitter_languages().get_parser(lang)
        tree = parser.parse(source_bytes)
    except Exception:
        return None

    try:
        symbols = _extract_symbols(tree.root_node, source, source_bytes, str(path), lang)
        imports = _extract_imports(tree.root_node, source_bytes, str(path), lang)
        calls = _extract_calls(tree.root_node, source, str(path), lang)
    except RecursionError:
        return None

    return ParseResult(
        file=str(path),
        language=lang,
        symbols=symbols,
        imports=imports,
        calls=calls,
        raw_source=source,
        line_count=source.count("\n") + 1,
    )


def parse_directory(
    root: Path,
    exclude: Optional[set] = None,
    follow_symlinks: bool = False,
) -> list[ParseResult]:
    """Recursively parse all supported source files in a directory."""

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


def _source_slice(source_bytes: bytes, start_byte: int, end_byte: int) -> str:
    """Decode a source slice using tree-sitter byte offsets."""

    return source_bytes[start_byte:end_byte].decode(errors="replace")


def _extract_symbols(node, source: str, source_bytes: bytes, file: str, lang: str) -> list[Symbol]:
    """Walk AST and extract function/class/struct definitions."""

    symbols: list[Symbol] = []

    symbol_types = {
        "typescript": [
            "function_declaration",
            "class_declaration",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
            "method_definition",
            "arrow_function",
            "lexical_declaration",
        ],
        "javascript": [
            "function_declaration",
            "class_declaration",
            "method_definition",
            "arrow_function",
        ],
        "python": ["function_definition", "class_definition"],
        "rust": [
            "function_item",
            "struct_item",
            "enum_item",
            "trait_item",
            "const_item",
            "static_item",
            "type_item",
            "macro_definition",
        ],
        "go": ["function_declaration", "method_declaration", "type_declaration"],
        "c": ["function_definition", "struct_specifier", "enum_specifier", "type_definition"],
        "cpp": [
            "function_definition",
            "class_specifier",
            "struct_specifier",
            "enum_specifier",
            "namespace_definition",
            "template_declaration",
        ],
        "php": [
            "function_definition",
            "class_declaration",
            "method_declaration",
            "interface_declaration",
            "trait_declaration",
        ],
        "java": [
            "method_declaration",
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
        ],
        "ruby": ["method", "class", "module", "singleton_method"],
        "swift": [
            "function_declaration",
            "class_declaration",
            "struct_declaration",
            "enum_declaration",
            "protocol_declaration",
        ],
        "kotlin": [
            "function_declaration",
            "class_declaration",
            "object_declaration",
            "interface_declaration",
        ],
        "lua": ["function_declaration", "local_function_declaration_statement"],
        "zig": ["fn_decl", "var_decl"],
    }

    container_types = {
        "rust": ["impl_item", "trait_item"],
        "python": ["class_definition"],
        "typescript": ["class_declaration"],
        "javascript": ["class_declaration"],
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
            body = _source_slice(source_bytes, n.start_byte, n.end_byte)
            sig = body.split("\n")[0].strip()
            qualified_name = f"{parent_name}::{name}" if parent_name and lang == "rust" else name

            kind = n.type
            for suffix in ("_definition", "_declaration", "_item"):
                kind = kind.replace(suffix, "")

            symbols.append(
                Symbol(
                    name=qualified_name,
                    kind=kind,
                    file=file,
                    line=n.start_point[0] + 1,
                    end_line=n.end_point[0] + 1,
                    body=body,
                    signature=sig,
                    parent=parent_name,
                    language=lang,
                )
            )
            for child in n.children:
                walk(child, parent_name=qualified_name)

        elif n.type in target_containers:
            if n.type == "impl_item":
                container_name = _get_impl_name(n)
            else:
                name_node = n.child_by_field_name("name")
                container_name = name_node.text.decode() if name_node else parent_name

            if n.type not in target_symbols:
                body = _source_slice(source_bytes, n.start_byte, n.end_byte)
                sig = body.split("\n")[0].strip()
                kind = n.type.replace("_definition", "").replace("_declaration", "").replace("_item", "")
                symbols.append(
                    Symbol(
                        name=container_name or "<anonymous>",
                        kind=kind,
                        file=file,
                        line=n.start_point[0] + 1,
                        end_line=n.end_point[0] + 1,
                        body=body[:500],
                        signature=sig,
                        parent=parent_name,
                        language=lang,
                    )
                )

            for child in n.children:
                walk(child, parent_name=container_name)
        else:
            for child in n.children:
                walk(child, parent_name=parent_name)

    walk(node)
    return symbols


def _extract_imports(node, source_bytes: bytes, file: str, lang: str) -> list[Import]:
    """Extract import statements."""

    imports: list[Import] = []
    import_types = {
        "typescript": ["import_statement"],
        "javascript": ["import_statement"],
        "python": ["import_statement", "import_from_statement"],
        "rust": ["use_declaration"],
        "go": ["import_declaration"],
        "c": ["preproc_include"],
        "cpp": ["preproc_include", "using_declaration"],
        "php": ["namespace_use_declaration"],
        "java": ["import_declaration"],
        "ruby": ["call"],
        "swift": ["import_declaration"],
        "kotlin": ["import_header"],
    }
    target_types = import_types.get(lang, [])

    def walk(n):
        if n.type in target_types:
            text = _source_slice(source_bytes, n.start_byte, n.end_byte)
            imports.append(
                Import(
                    source=text,
                    target=text,
                    file=file,
                    line=n.start_point[0] + 1,
                )
            )
        for child in n.children:
            walk(child)

    walk(node)
    return imports


def _extract_calls(node, source: str, file: str, lang: str) -> list[CallSite]:
    """Extract function and method call sites."""

    calls: list[CallSite] = []

    def walk(n, enclosing_fn=None):
        if n.type == "call_expression" or n.type == "call":
            fn_node = n.child_by_field_name("function") or (n.children[0] if n.children else None)
            callee = fn_node.text.decode() if fn_node else "<unknown>"
            calls.append(
                CallSite(
                    caller=enclosing_fn or "<module>",
                    callee=callee,
                    file=file,
                    line=n.start_point[0] + 1,
                )
            )

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
