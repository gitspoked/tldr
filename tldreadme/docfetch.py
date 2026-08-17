"""External dependency documentation fetch - privacy-gated.

Fetch CURRENT docs for a project's declared dependencies WITHOUT leaking the
project under development. This realizes the "fetch docs on demand" half of
TLDR's philosophy for *external* deps, complementing ``context_docs`` which
scans only local project docs.

Privacy contract (enforced in code, not just prose)
---------------------------------------------------
Only a dependency's PUBLIC identity - ``library`` name, pinned ``version``,
``ecosystem`` - plus a short generic ``topic`` may ever leave the machine, and
only when the user explicitly opts in. Nothing about the project under
development (its name, paths, source, symbols, or local notes) is ever part of
an outbound request. Two mechanisms guarantee this:

1. Structural allowlist: :func:`build_egress_payload` is the single choke point
   for anything outbound and emits ONLY the keys in :data:`EGRESS_ALLOWED_KEYS`.
   :func:`dep_docs` has no parameter that carries project context into it.
2. Topic validation: the only free-text field, ``topic``, is length/format
   validated by :func:`assert_generic_topic`, which rejects anything that looks
   like a filesystem path, source code, or the project's own name.

Config (environment)
--------------------
``TLDR_DEP_DOCS``  = ``off`` (default) | ``local`` | ``context7``
    off      no fetch, no egress; :func:`dep_docs` reports it is disabled.
    local    only no-egress backends (installed package metadata already on
             disk). Nothing leaves the machine.
    context7 ALSO allowed to query the remote Context7 docs service, sending
             ONLY the allowlisted payload.
``CONTEXT7_API_KEY``  optional bearer passed verbatim to Context7.
``CONTEXT7_MCP_URL``  override, default ``https://mcp.context7.com/mcp``.

Even the ``local`` -> ``context7`` step is opt-in because a dependency list can
fingerprint a project and reveal its security posture; that judgement belongs to
the user, so the default is the conservative ``off``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

# The ONLY keys permitted to leave the machine. Widening this set is a privacy
# decision - do not add project-scoped fields here.
EGRESS_ALLOWED_KEYS = ("library", "version", "ecosystem", "topic")

MODE_OFF = "off"
MODE_LOCAL = "local"
MODE_CONTEXT7 = "context7"
_VALID_MODES = (MODE_OFF, MODE_LOCAL, MODE_CONTEXT7)

DEFAULT_CONTEXT7_URL = "https://mcp.context7.com/mcp"
MAX_TOPIC_LEN = 160

# Substrings/patterns that mark a topic as leaking project context rather than
# asking a generic question about a dependency's own docs.
# Match actual code STRUCTURE (statement punctuation or a function/class
# signature), not English that merely contains words like "import" or "return".
_CODE_MARKERS = re.compile(r"[;{}]|\b(?:def|class|func|function)\s+\w+\s*\(", re.IGNORECASE)
_PATHY = re.compile(r"(?:\.\.[\\/])|(?:\./)|(?:^[\\/])|(?:^~)|(?:[A-Za-z]:\\)|(?:/[\w.-]+/[\w.-]+/)")


class PrivacyError(ValueError):
    """Raised when a request would carry project-identifying data off-machine."""


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

def mode() -> str:
    """Return the configured egress mode, defaulting to the conservative ``off``.

    Any unrecognized value collapses to ``off`` - fail closed, never open.
    """

    value = (os.environ.get("TLDR_DEP_DOCS") or MODE_OFF).strip().lower()
    return value if value in _VALID_MODES else MODE_OFF


def _context7_url() -> str:
    return (os.environ.get("CONTEXT7_MCP_URL") or DEFAULT_CONTEXT7_URL).strip()


def _context7_api_key() -> str:
    return (os.environ.get("CONTEXT7_API_KEY") or "").strip()


# --------------------------------------------------------------------------- #
# Privacy enforcement
# --------------------------------------------------------------------------- #

def assert_generic_topic(
    topic: str | None,
    *,
    project_name: str | None = None,
    project_root: str | None = None,
) -> str:
    """Validate ``topic`` as a generic, dependency-scoped question.

    Returns the cleaned topic (empty -> ``"overview"``). Raises
    :class:`PrivacyError` if it looks like a path, source code, or names the
    project under development.
    """

    cleaned = (topic or "").strip()
    if not cleaned:
        return "overview"
    if "\n" in cleaned or "\r" in cleaned:
        raise PrivacyError("topic must be a single short line, not pasted context")
    if len(cleaned) > MAX_TOPIC_LEN:
        raise PrivacyError(f"topic too long (>{MAX_TOPIC_LEN} chars); keep it a generic question")
    if "`" in cleaned:
        raise PrivacyError("topic must not contain code (backticks)")
    if _CODE_MARKERS.search(cleaned):
        raise PrivacyError("topic looks like source code, not a documentation question")
    if _PATHY.search(cleaned):
        raise PrivacyError("topic looks like a filesystem path")
    # Do not let the project's own identity ride along in the free-text field.
    for needle in (project_name, project_root and Path(project_root).name):
        if needle and len(needle) >= 3 and needle.lower() in cleaned.lower():
            raise PrivacyError("topic must not reference the project under development")
    return cleaned


def build_egress_payload(library: str, version: str, ecosystem: str, topic: str) -> dict[str, str]:
    """Build the ONLY dict that may leave the machine.

    Emits exactly :data:`EGRESS_ALLOWED_KEYS` and nothing else. The topic is
    re-validated here so this choke point is safe even if a caller skipped the
    earlier check.
    """

    payload = {
        "library": (library or "").strip(),
        "version": (version or "").strip(),
        "ecosystem": (ecosystem or "").strip(),
        "topic": assert_generic_topic(topic),
    }
    # Defensive: never emit a key outside the allowlist.
    return {k: payload[k] for k in EGRESS_ALLOWED_KEYS}


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class DepDocResult:
    """Outcome of a dependency-doc lookup, safe to serialize to the caller."""

    library: str
    topic: str
    ecosystem: str = ""
    version_requested: str = ""
    installed_version: str = ""
    version_docs: str = ""
    drift: str = ""                       # "", "match", "installed<pinned", etc.
    status: str = "unknown"               # disabled|rejected|ok|unavailable|error
    source: str = ""                      # local|context7|cache|none
    summary: str = ""
    text: str = ""
    provenance: str = ""
    egress_payload: dict[str, str] | None = None  # exactly what left (or would leave)
    confidence: float = 0.0
    recommended_next_action: str = ""
    verification_commands: list[str] = field(default_factory=list)
    fallback_used: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Manifest-grounded version + ecosystem resolution (local only, no egress)
# --------------------------------------------------------------------------- #

_REGISTRY_TO_ECOSYSTEM = {
    "pypi": "pypi",
    "npm": "npm",
    "crates.io": "crates",
    "go": "go",
}


def resolve_from_manifest(library: str, root: str | None) -> tuple[str, str]:
    """Look up ``library``'s pinned version + ecosystem from the project's
    manifests. Local read only; nothing leaves the machine. Returns
    ``(version, ecosystem)`` with empty strings when not found.
    """

    if not root or not library:
        return "", ""
    try:
        from .deps import extract_deps_from_directory
    except Exception:
        return "", ""
    try:
        projects = extract_deps_from_directory(Path(root))
    except Exception:
        return "", ""
    want = _norm_name(library)
    for project in projects:
        for dep in project.dependencies:
            if _norm_name(dep.name) == want:
                return dep.version or "", _REGISTRY_TO_ECOSYSTEM.get(dep.registry, dep.registry or "")
    return "", ""


def available_dep_names(root: str | None) -> list[str]:
    """List dependency names declared in the project's manifests (local only)."""

    if not root:
        return []
    try:
        from .deps import extract_deps_from_directory
    except Exception:
        return []
    try:
        projects = extract_deps_from_directory(Path(root))
    except Exception:
        return []
    names: list[str] = []
    for project in projects:
        for dep in project.dependencies:
            if dep.name not in names:
                names.append(dep.name)
    return names


def _norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", (name or "").strip().lower())


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #

def _local_pypi_docs(library: str) -> tuple[str | None, str, str]:
    """Return (text, installed_version, provenance) from installed pypi metadata.

    Uses importlib.metadata - reads only what is already on disk, no egress.
    """

    try:
        from importlib import metadata as importlib_metadata
    except Exception:
        return None, "", ""
    for candidate in (library, _norm_name(library), (library or "").replace("-", "_")):
        try:
            meta = importlib_metadata.metadata(candidate)
        except Exception:
            continue
        installed = ""
        try:
            installed = importlib_metadata.version(candidate)
        except Exception:
            installed = meta.get("Version", "") if meta else ""
        summary = (meta.get("Summary", "") if meta else "") or ""
        description = (meta.get("Description", "") if meta else "") or ""
        text = "\n\n".join(part for part in (summary, description) if part).strip()
        if text:
            return text, installed, f"installed pypi metadata for {candidate}"
        if installed:
            return summary or "(no long description in installed metadata)", installed, f"installed pypi metadata for {candidate}"
    return None, "", ""


def _local_npm_docs(library: str, root: str | None) -> tuple[str | None, str, str]:
    """Best-effort read of an installed node_modules package (local only)."""

    if not root:
        return None, "", ""
    pkg_dir = Path(root) / "node_modules" / library
    pkg_json = pkg_dir / "package.json"
    if not pkg_json.is_file():
        return None, "", ""
    try:
        data = json.loads(pkg_json.read_text())
    except Exception:
        return None, "", ""
    installed = str(data.get("version", "") or "")
    parts = [str(data.get("description", "") or "")]
    for readme in ("README.md", "readme.md", "Readme.md"):
        rp = pkg_dir / readme
        if rp.is_file():
            try:
                parts.append(rp.read_text()[:20000])
            except Exception:
                pass
            break
    text = "\n\n".join(p for p in parts if p).strip()
    if text:
        return text, installed, f"installed node_modules/{library}"
    return None, installed, ""


def _local_fetch(library: str, ecosystem: str, root: str | None) -> tuple[str | None, str, str]:
    """Dispatch to a no-egress local backend by ecosystem."""

    if ecosystem in ("pypi", ""):
        text, installed, prov = _local_pypi_docs(library)
        if text is not None or installed:
            return text, installed, prov
    if ecosystem == "npm":
        return _local_npm_docs(library, root)
    return None, "", ""


def default_context7_resolver(payload: dict[str, str]) -> tuple[str | None, str]:
    """Query the remote Context7 docs MCP with the allowlisted payload.

    EXPERIMENTAL transport: performs a JSON-RPC ``tools/call`` over the Context7
    streamable-HTTP endpoint using only the standard library. It sends ONLY the
    keys already present in ``payload`` (the egress choke point guarantees those
    are allowlisted). Any failure returns ``(None, reason)`` so the caller can
    degrade gracefully; it never raises into the tool path.

    NOTE: the live handshake against mcp.context7.com has not been validated in
    this environment (opt-in, network-gated). Treat functional success as
    unverified until a real opt-in smoke run confirms it. The PRIVACY behavior,
    by contrast, is guaranteed regardless of transport: nothing beyond ``payload``
    is ever serialized.
    """

    import urllib.request
    import urllib.error

    library = payload.get("library", "")
    topic = payload.get("topic", "overview")
    version = payload.get("version", "")
    lib_id = f"{library}" + (f"@{version}" if version else "")
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "query-docs",
            "arguments": {"libraryId": lib_id, "query": topic},
        },
    }
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    api_key = _context7_api_key()
    if api_key:
        headers["Authorization"] = api_key if api_key.lower().startswith("bearer") else f"Bearer {api_key}"
    request = urllib.request.Request(_context7_url(), data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, f"context7 request failed: {exc.__class__.__name__}"
    text = _extract_text_from_mcp_response(raw)
    if text:
        return text, "context7 query-docs"
    return None, "context7 returned no usable text"


def _extract_text_from_mcp_response(raw: str) -> str:
    """Pull human-readable doc text out of a JSON or SSE MCP response body."""

    chunks: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[len("data:"):].strip()
        if not line or not (line.startswith("{") or line.startswith("[")):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        result = obj.get("result") if isinstance(obj, dict) else None
        content = (result or {}).get("content") if isinstance(result, dict) else None
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                    chunks.append(str(item["text"]))
    return "\n".join(chunks).strip()


# --------------------------------------------------------------------------- #
# Cache (local, under .tldr/deps - gitignored)
# --------------------------------------------------------------------------- #

def _cache_path(root: str | None, ecosystem: str, library: str, version: str, topic: str) -> Path | None:
    if not root:
        return None
    safe = lambda s: re.sub(r"[^A-Za-z0-9_.@-]+", "-", s or "_")
    return (
        Path(root)
        / ".tldr"
        / "deps"
        / safe(ecosystem or "unknown")
        / f"{safe(library)}@{safe(version or 'unpinned')}"
        / f"{safe(topic)}.md"
    )


def _read_cache(path: Path | None) -> str | None:
    if path and path.is_file():
        try:
            return path.read_text()
        except Exception:
            return None
    return None


def _write_cache(path: Path | None, text: str) -> None:
    if not path or not text:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Public entry point (wired into the MCP `dep_docs` tool)
# --------------------------------------------------------------------------- #

def dep_docs(
    library: str | None = None,
    topic: str = "overview",
    version: str | None = None,
    root: str | None = None,
    *,
    resolver: Callable[[dict[str, str]], tuple[str | None, str]] | None = None,
) -> dict[str, object]:
    """Fetch documentation for a single external dependency, privacy-gated.

    ``library``  dependency/package name (e.g. ``"fastapi"``). If omitted, returns
                 the list of dependencies declared in the project's manifests.
    ``topic``    short generic question about that dependency's docs.
    ``version``  pinned version; if omitted it is resolved from the project's
                 manifests (so docs match the version actually in use).
    ``root``     project root, used ONLY locally to read manifests + write cache.
    ``resolver`` remote fetch callable (dependency-injected for testing); defaults
                 to :func:`default_context7_resolver`.
    """

    active = mode()
    current_mode = active

    # No library -> local discovery listing, zero egress.
    if not library:
        names = available_dep_names(root)
        return DepDocResult(
            library="",
            topic="",
            status="need-library",
            source="local",
            summary=f"{len(names)} dependencies declared; pass `library` to fetch docs for one.",
            text="\n".join(names),
            recommended_next_action="Call dep_docs again with `library` set to one of the listed names.",
            confidence=1.0,
        ).as_dict()

    # Resolve ecosystem + pinned version from manifests (local only).
    resolved_version, ecosystem = resolve_from_manifest(library, root)
    version_requested = (version or resolved_version or "").strip()

    # Validate the topic BEFORE any mode branching (fail closed on leaks).
    try:
        clean_topic = assert_generic_topic(topic, project_root=root)
    except PrivacyError as exc:
        return DepDocResult(
            library=library, topic=str(topic or ""), ecosystem=ecosystem,
            version_requested=version_requested, status="rejected", source="none",
            summary=f"Refused to send a leaky query off-machine: {exc}",
            recommended_next_action="Rephrase `topic` as a short generic question about the library's own docs.",
        ).as_dict()

    result = DepDocResult(
        library=library, topic=clean_topic, ecosystem=ecosystem,
        version_requested=version_requested,
    )

    # Mode: off -> disabled, no backend touched, no egress.
    if current_mode == MODE_OFF:
        result.status = "disabled"
        result.source = "none"
        result.summary = "Dependency-doc fetch is off. Set TLDR_DEP_DOCS=local (no egress) or =context7 (opt-in) to enable."
        result.recommended_next_action = "export TLDR_DEP_DOCS=local for offline metadata, or =context7 to allow remote doc fetch."
        return result.as_dict()

    # Cache hit short-circuits any egress.
    cache_file = _cache_path(root, ecosystem, library, version_requested, clean_topic)
    cached = _read_cache(cache_file)
    if cached:
        result.status = "ok"
        result.source = "cache"
        result.text = cached
        result.version_docs = version_requested
        result.summary = f"Cached docs for {library} ({version_requested or 'unpinned'})."
        result.confidence = 0.7
        return result.as_dict()

    # Always try the local, no-egress backend first.
    local_text, installed_version, local_prov = _local_fetch(library, ecosystem, root)
    result.installed_version = installed_version
    if installed_version and version_requested:
        result.drift = _drift(installed_version, version_requested)

    if current_mode == MODE_LOCAL:
        if local_text:
            result.status = "ok"
            result.source = "local"
            result.text = local_text
            result.provenance = local_prov
            result.version_docs = installed_version or version_requested
            result.summary = f"Local docs for {library} (installed {installed_version or 'unknown'}); no data left the machine."
            result.confidence = 0.5
        else:
            result.status = "unavailable"
            result.source = "local"
            result.summary = f"No local docs for {library}; enable TLDR_DEP_DOCS=context7 to fetch remotely (opt-in)."
            result.recommended_next_action = "export TLDR_DEP_DOCS=context7 to allow a name+version-only remote lookup."
        return result.as_dict()

    # context7 mode: opt-in remote fetch of the allowlisted payload only.
    payload = build_egress_payload(library, version_requested, ecosystem, clean_topic)
    result.egress_payload = payload  # transparency: exactly what leaves
    fetch = resolver or default_context7_resolver
    remote_text, remote_prov = fetch(payload)
    if remote_text:
        result.status = "ok"
        result.source = "context7"
        result.text = remote_text
        result.provenance = remote_prov
        result.version_docs = version_requested
        result.summary = f"Remote docs for {library} ({version_requested or 'unpinned'}) via Context7."
        result.confidence = 0.6
        _write_cache(cache_file, remote_text)
        return result.as_dict()

    # Remote failed -> degrade to whatever local had.
    result.fallback_used = ["local"]
    if local_text:
        result.status = "ok"
        result.source = "local"
        result.text = local_text
        result.provenance = local_prov
        result.version_docs = installed_version or version_requested
        result.summary = f"Remote fetch failed ({remote_prov}); served local docs for {library} instead."
        result.confidence = 0.4
    else:
        result.status = "unavailable"
        result.source = "none"
        result.summary = f"Remote fetch failed ({remote_prov}) and no local docs for {library}."
        result.recommended_next_action = "Check network/opt-in config, or query the library's docs manually."
    return result.as_dict()


def _drift(installed: str, pinned: str) -> str:
    """Cheap human-readable drift label between installed and pinned versions."""

    inst = (installed or "").strip()
    pin = re.sub(r"^[~^>=<!\s]+", "", (pinned or "").strip())
    if not inst or not pin or pin == "*":
        return ""
    if inst == pin:
        return "match"
    return f"installed {inst} != pinned {pinned}"
