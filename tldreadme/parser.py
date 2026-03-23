"""Compatibility facade for AST parsing, dependency extraction, and doc scanning."""

from .asts import CallSite, Import, LANG_MAP, ParseResult, Symbol, detect_language, parse_directory, parse_file
from .context_docs import CONTEXT_DOC_NAMES, ContextDoc, _parse_markdown_sections, scan_context_docs
from .deps import Dependency, ProjectDeps, extract_deps_from_directory

__all__ = [
    "CallSite",
    "ContextDoc",
    "Dependency",
    "Import",
    "LANG_MAP",
    "ParseResult",
    "ProjectDeps",
    "Symbol",
    "CONTEXT_DOC_NAMES",
    "_parse_markdown_sections",
    "detect_language",
    "extract_deps_from_directory",
    "parse_directory",
    "parse_file",
    "scan_context_docs",
]
