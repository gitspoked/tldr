"""Directory watcher — re-indexes on file changes, keeps knowledge current."""

from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent
import time

from .parser import parse_file, detect_language
from .embedder import CodeEmbedder, symbols_to_chunks
from .grapher import CodeGrapher


class CodeChangeHandler(FileSystemEventHandler):
    """On file save: re-parse AST, update embeddings, update graph."""

    def __init__(self, embedder: CodeEmbedder, grapher: CodeGrapher):
        self.embedder = embedder
        self.grapher = grapher
        self._debounce: dict[str, float] = {}

    def on_modified(self, event):
        if event.is_directory:
            return
        self._handle(event.src_path)

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle(event.src_path)

    def _handle(self, path_str: str):
        path = Path(path_str)

        # Skip non-code files
        if not detect_language(path):
            return

        # Skip common noise
        if any(skip in path.parts for skip in (".git", "node_modules", "target", "__pycache__", ".venv")):
            return

        # Debounce — same file within 2 seconds
        now = time.time()
        if path_str in self._debounce and (now - self._debounce[path_str]) < 2.0:
            return
        self._debounce[path_str] = now

        # Re-parse
        result = parse_file(path)
        if not result:
            return

        print(f"[tldr] re-indexing: {path_str} ({len(result.symbols)} symbols)")

        # Update embeddings
        chunks = symbols_to_chunks([result])
        self.embedder.index_chunks(chunks)

        # Update graph
        self.grapher.index_results([result])


def start_watcher(directories: list[Path]):
    """Watch directories for code changes, re-index incrementally."""
    embedder = CodeEmbedder()
    grapher = CodeGrapher()
    handler = CodeChangeHandler(embedder, grapher)

    observer = Observer()
    for d in directories:
        print(f"[tldr] watching: {d}")
        observer.schedule(handler, str(d), recursive=True)

    observer.start()
    print(f"[tldr] watcher running. Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
