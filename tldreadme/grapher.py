"""FalkorDB graph builder — call graphs, imports, data flow, patterns."""

from falkordb import FalkorDB
from .parser import ParseResult, Symbol, Import, CallSite
import os


GRAPH_NAME = "tldreadme"


class CodeGrapher:
    """Builds and queries the code knowledge graph in FalkorDB."""

    def __init__(self, url: str = None):
        url = url or os.getenv("FALKORDB_URL", "redis://localhost:16379")
        self.db = FalkorDB(url=url)
        self.graph = self.db.select_graph(GRAPH_NAME)
        self._ensure_schema()

    def _ensure_schema(self):
        """Create indexes for fast lookup."""
        try:
            self.graph.query("CREATE INDEX FOR (s:Symbol) ON (s.name)")
            self.graph.query("CREATE INDEX FOR (f:File) ON (f.path)")
            self.graph.query("CREATE INDEX FOR (m:Module) ON (m.name)")
        except Exception:
            pass  # indexes may already exist

    def index_results(self, results: list[ParseResult]):
        """Build the full graph from parse results."""
        for pr in results:
            # Create file node
            self.graph.query(
                "MERGE (f:File {path: $path}) SET f.language = $lang, f.lines = $lines",
                {"path": pr.file, "lang": pr.language, "lines": pr.line_count},
            )

            # Create module node (directory)
            parts = pr.file.rsplit("/", 1)
            module = parts[0] if len(parts) > 1 else "."
            self.graph.query(
                "MERGE (m:Module {name: $name})",
                {"name": module},
            )
            self.graph.query(
                "MATCH (f:File {path: $path}), (m:Module {name: $module}) "
                "MERGE (m)-[:CONTAINS]->(f)",
                {"path": pr.file, "module": module},
            )

            # Create symbol nodes
            for sym in pr.symbols:
                self.graph.query(
                    "MERGE (s:Symbol {name: $name, file: $file, line: $line}) "
                    "SET s.kind = $kind, s.language = $lang, s.signature = $sig, "
                    "s.end_line = $end_line, s.parent = $parent",
                    {
                        "name": sym.name, "file": sym.file, "line": sym.line,
                        "kind": sym.kind, "lang": sym.language, "sig": sym.signature,
                        "end_line": sym.end_line, "parent": sym.parent,
                    },
                )
                # Link symbol to file
                self.graph.query(
                    "MATCH (s:Symbol {name: $name, file: $file, line: $line}), "
                    "(f:File {path: $file}) "
                    "MERGE (f)-[:DEFINES]->(s)",
                    {"name": sym.name, "file": sym.file, "line": sym.line},
                )

            # Create CALLS edges
            for call in pr.calls:
                self.graph.query(
                    "MATCH (caller:Symbol {name: $caller, file: $file}) "
                    "MATCH (callee:Symbol {name: $callee}) "
                    "MERGE (caller)-[:CALLS {line: $line}]->(callee)",
                    {
                        "caller": call.caller, "callee": call.callee,
                        "file": call.file, "line": call.line,
                    },
                )

            # Create IMPORTS edges
            for imp in pr.imports:
                self.graph.query(
                    "MATCH (f:File {path: $file}) "
                    "MERGE (i:Import {source: $source}) "
                    "MERGE (f)-[:IMPORTS {line: $line}]->(i)",
                    {"file": imp.file, "source": imp.source, "line": imp.line},
                )

    # ── Query methods ──────────────────────────────────────────────

    def get_callers(self, symbol_name: str) -> list[dict]:
        """Who calls this symbol?"""
        result = self.graph.query(
            "MATCH (caller:Symbol)-[:CALLS]->(s:Symbol {name: $name}) "
            "RETURN caller.name, caller.file, caller.line, caller.kind",
            {"name": symbol_name},
        )
        return [{"name": r[0], "file": r[1], "line": r[2], "kind": r[3]} for r in result.result_set]

    def get_callees(self, symbol_name: str) -> list[dict]:
        """What does this symbol call?"""
        result = self.graph.query(
            "MATCH (s:Symbol {name: $name})-[:CALLS]->(callee:Symbol) "
            "RETURN callee.name, callee.file, callee.line, callee.kind",
            {"name": symbol_name},
        )
        return [{"name": r[0], "file": r[1], "line": r[2], "kind": r[3]} for r in result.result_set]

    def get_module_symbols(self, module_path: str) -> list[dict]:
        """All symbols in a module (directory)."""
        result = self.graph.query(
            "MATCH (m:Module {name: $path})-[:CONTAINS]->(f:File)-[:DEFINES]->(s:Symbol) "
            "RETURN s.name, s.kind, s.signature, f.path, s.line "
            "ORDER BY f.path, s.line",
            {"path": module_path},
        )
        return [{"name": r[0], "kind": r[1], "signature": r[2], "file": r[3], "line": r[4]}
                for r in result.result_set]

    def get_flow(self, entry_symbol: str, max_depth: int = 5) -> list[dict]:
        """Trace call flow from an entry point, up to max_depth."""
        result = self.graph.query(
            "MATCH path = (s:Symbol {name: $name})-[:CALLS*1.." + str(max_depth) + "]->(target:Symbol) "
            "RETURN [n IN nodes(path) | {name: n.name, file: n.file, kind: n.kind}]",
            {"name": entry_symbol},
        )
        return [r[0] for r in result.result_set]

    def get_dependents(self, symbol_name: str) -> list[dict]:
        """Everything that depends on this symbol (callers, importers)."""
        result = self.graph.query(
            "MATCH (dep)-[:CALLS|IMPORTS*1..3]->(s:Symbol {name: $name}) "
            "RETURN DISTINCT dep.name, dep.file, labels(dep)[0]",
            {"name": symbol_name},
        )
        return [{"name": r[0], "file": r[1], "type": r[2]} for r in result.result_set]
