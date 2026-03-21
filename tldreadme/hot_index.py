"""Hot index — on init, pre-scan top symbols/files so reads are instant lookups."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .search import rg_files, rg_count
from .parser import ParseResult


@dataclass
class HotEntry:
    """A pre-indexed symbol or file with its locations cached."""
    name: str
    kind: str                          # "function", "struct", "file", "module"
    locations: list[dict]              # [{file, line, context}]
    importance: float                  # higher = more important
    hit_count: int = 0                 # how many references across codebase


@dataclass
class HotIndex:
    """Top N most important symbols/files, pre-scanned for instant lookup."""
    root: str
    entries: dict[str, HotEntry] = field(default_factory=dict)  # name -> HotEntry
    top_files: list[str] = field(default_factory=list)           # most important files

    def lookup(self, name: str) -> Optional[HotEntry]:
        """Instant lookup — no rg, no search, just return what we know."""
        return self.entries.get(name)

    def save(self, path: Path):
        """Persist hot index to disk."""
        data = {
            "root": self.root,
            "top_files": self.top_files,
            "entries": {
                name: {
                    "name": e.name, "kind": e.kind, "importance": e.importance,
                    "hit_count": e.hit_count, "locations": e.locations,
                }
                for name, e in self.entries.items()
            },
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> Optional["HotIndex"]:
        """Load hot index from disk."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            idx = cls(root=data["root"])
            idx.top_files = data.get("top_files", [])
            for name, e in data.get("entries", {}).items():
                idx.entries[name] = HotEntry(**e)
            return idx
        except Exception:
            return None


def build_hot_index(root: Path, parse_results: list[ParseResult], top_n: int = 100) -> HotIndex:
    """Build the hot index from parse results.

    Strategy:
    1. Rank symbols by importance (callers, size, centrality)
    2. Take top N
    3. rg-scan each one to cache every location
    4. Persist to .tldr/hot_index.json
    """
    index = HotIndex(root=str(root))

    # Score every symbol
    scored = []
    for pr in parse_results:
        for sym in pr.symbols:
            # Importance heuristic:
            #   - public functions > private
            #   - larger functions = more complex = more important
            #   - structs/classes > functions (they're the nouns)
            #   - test functions score lower
            size = sym.end_line - sym.line
            is_test = sym.name.startswith("test_")
            is_public = not sym.name.startswith("_")

            score = size * 0.3  # bigger = more important
            if sym.kind in ("struct", "class", "trait", "interface", "enum"):
                score *= 3.0   # nouns matter more
            if sym.kind == "impl":
                score *= 2.0   # impl blocks define behavior
            if is_public:
                score *= 1.5
            if is_test:
                score *= 0.2   # tests are less important for hot index

            scored.append((sym, score, pr.file))

    # Sort by score, take top N
    scored.sort(key=lambda x: x[1], reverse=True)
    top_symbols = scored[:top_n]

    # rg-scan each top symbol to find all references
    root_str = str(root)
    for sym, score, origin_file in top_symbols:
        # Strip qualified prefix for search (DiskGraph::knn -> knn also finds it)
        search_name = sym.name.split("::")[-1] if "::" in sym.name else sym.name

        # Count references across codebase
        counts = rg_count(search_name, [root_str])
        total_hits = sum(counts.values())

        # Get file list
        files = list(counts.keys())[:10]  # cap at 10 locations

        index.entries[sym.name] = HotEntry(
            name=sym.name,
            kind=sym.kind,
            locations=[
                {"file": origin_file, "line": sym.line, "definition": True},
            ] + [
                {"file": f, "hits": counts[f], "definition": False}
                for f in files if f != origin_file
            ],
            importance=score,
            hit_count=total_hits,
        )

    # Top files by total symbol weight
    file_scores: dict[str, float] = {}
    for sym, score, origin_file in scored:
        file_scores[origin_file] = file_scores.get(origin_file, 0) + score
    index.top_files = [
        f for f, _ in sorted(file_scores.items(), key=lambda x: -x[1])[:50]
    ]

    return index
