"""Bounded Git history search with ripgrep candidate filtering."""

from __future__ import annotations

import re
import subprocess
import time
import unicodedata
from pathlib import Path

RECORD_SEPARATOR = b"\0"
FIELD_SEPARATOR = "\x1f"
DEFAULT_MAX_COMMITS = 10_000
DEFAULT_TIMEOUT_SECONDS = 5.0

UNICODE_ALIASES = {
    "em dash": ("\u2014",),
    "en dash": ("\u2013",),
    "smart quote": ("\u2018", "\u2019", "\u201c", "\u201d"),
    "curly quote": ("\u2018", "\u2019", "\u201c", "\u201d"),
    "non-breaking space": ("\u00a0",),
    "nonbreaking space": ("\u00a0",),
    "ellipsis character": ("\u2026",),
}

QUERY_STOPWORDS = {
    "a",
    "all",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "commit",
    "commits",
    "did",
    "do",
    "does",
    "find",
    "for",
    "from",
    "git",
    "history",
    "in",
    "into",
    "is",
    "it",
    "message",
    "messages",
    "of",
    "on",
    "or",
    "repo",
    "repository",
    "search",
    "show",
    "the",
    "to",
    "was",
    "were",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}

HISTORY_INTENT_PHRASES = (
    "all refs",
    "authored by",
    "commit body",
    "commit bodies",
    "commit history",
    "commit message",
    "commit messages",
    "git history",
    "introduced in",
    "removed in",
    "when was",
)

HISTORY_INTENT_WORDS = {
    "author",
    "authored",
    "blame",
    "commit",
    "commits",
    "history",
    "introduced",
    "reflog",
}


def _normalize(text: str) -> str:
    """Normalize text for case-insensitive comparisons without discarding punctuation."""

    return unicodedata.normalize("NFKC", text).casefold()


def _words(text: str) -> list[str]:
    """Return Unicode-aware word tokens."""

    return re.findall(r"[^\W_]+", _normalize(text), flags=re.UNICODE)


def query_requests_history(query: str | None, source_types: list[str] | None = None) -> bool:
    """Return whether a router query explicitly asks for Git history."""

    if source_types and "history" in source_types:
        return True
    if not query:
        return False

    normalized = _normalize(query)
    if any(phrase in normalized for phrase in HISTORY_INTENT_PHRASES):
        return True
    return bool(set(_words(normalized)) & HISTORY_INTENT_WORDS)


def _query_plan(query: str) -> dict[str, object]:
    """Build exact literals and whole-word terms for candidate selection."""

    raw = query.strip()
    normalized = _normalize(raw)
    aliases: list[str] = []
    for phrase, literals in UNICODE_ALIASES.items():
        if phrase in normalized:
            aliases.extend(literals)
        aliases.extend(literal for literal in literals if literal in raw)

    codepoint_matches = re.findall(r"\b(?:u\+|\\u)([0-9a-f]{4,6})\b", normalized)
    for value in codepoint_matches:
        try:
            aliases.append(chr(int(value, 16)))
        except (ValueError, OverflowError):
            continue

    terms = [word for word in _words(normalized) if word not in QUERY_STOPWORDS and len(word) > 1]
    exact_phrases = [raw] if raw else []
    patterns = list(dict.fromkeys([*aliases, *exact_phrases, *terms]))
    browse = not aliases and not terms
    return {
        "aliases": list(dict.fromkeys(aliases)),
        "terms": list(dict.fromkeys(terms)),
        "exact_phrases": exact_phrases,
        "patterns": patterns,
        "browse": browse,
    }


def _run(
    args: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess:
    """Run a bounded subprocess in byte mode."""

    return subprocess.run(
        args,
        cwd=str(cwd),
        input=input_bytes,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def _git_root(root: Path, timeout_seconds: float) -> Path | None:
    """Resolve the containing Git worktree."""

    try:
        result = _run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            timeout_seconds=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.decode("utf-8", errors="replace").strip()).resolve()


def _history_blob(
    repo_root: Path,
    *,
    all_refs: bool,
    max_commits: int,
    timeout_seconds: float,
    since: str | None,
    until: str | None,
    scope: str | None,
) -> tuple[bytes, str | None]:
    """Return NUL-delimited commit records from Git."""

    pretty = "%H%x1f%h%x1f%aI%x1f%aN%x1f%s%x1f%b%x00"
    args = [
        "git",
        "log",
        "--no-color",
        f"--format={pretty}",
        f"--max-count={max_commits}",
    ]
    if all_refs:
        args.append("--all")
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")
    if scope:
        args.extend(["--", scope])

    try:
        result = _run(args, cwd=repo_root, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired:
        return b"", "git_timeout"
    except OSError:
        return b"", "git_unavailable"
    if result.returncode != 0:
        return b"", "git_log_failed"
    return result.stdout, None


def _candidate_blob(
    history_blob: bytes,
    patterns: list[str],
    *,
    repo_root: Path,
    timeout_seconds: float,
) -> tuple[bytes, str | None]:
    """Use ripgrep to select commit records that contain a query candidate."""

    args = [
        "rg",
        "--null-data",
        "--fixed-strings",
        "--ignore-case",
        "--no-filename",
        "--color",
        "never",
    ]
    for pattern in patterns:
        args.extend(["-e", pattern])

    try:
        result = _run(
            args,
            cwd=repo_root,
            timeout_seconds=timeout_seconds,
            input_bytes=history_blob,
        )
    except subprocess.TimeoutExpired:
        return b"", "rg_timeout"
    except OSError:
        return b"", "rg_unavailable"
    if result.returncode == 1:
        return b"", None
    if result.returncode != 0:
        return b"", "rg_failed"
    return result.stdout, None


def _parse_record(raw: bytes) -> dict[str, str] | None:
    """Parse one Git record."""

    text = raw.decode("utf-8", errors="replace").strip("\n")
    if not text:
        return None
    fields = text.split(FIELD_SEPARATOR, 5)
    if len(fields) != 6:
        return None
    commit, short_commit, authored_at, author, subject, body = fields
    return {
        "commit": commit,
        "short_commit": short_commit,
        "authored_at": authored_at,
        "author": author,
        "subject": subject,
        "body": body.strip(),
    }


def _matching_line(text: str, literals: list[str], terms: list[str]) -> tuple[str, str]:
    """Return the first matching line and its match kind."""

    for line in text.splitlines() or [text]:
        normalized = _normalize(line)
        for literal in literals:
            if _normalize(literal) in normalized:
                return line.strip(), "exact"
        line_words = set(_words(normalized))
        if line_words & set(terms):
            return line.strip(), "word"
    return text.splitlines()[0].strip() if text.splitlines() else "", "browse"


def _rank_record(record: dict[str, str], plan: dict[str, object]) -> dict | None:
    """Rank one candidate using exact literals and whole-word matches."""

    subject = record["subject"]
    body = record["body"]
    normalized_subject = _normalize(subject)
    normalized_body = _normalize(body)
    aliases = list(plan["aliases"])
    exact_phrases = list(plan["exact_phrases"])
    terms = list(plan["terms"])
    browse = bool(plan["browse"])

    score = 0.0
    matched_field = "subject"
    matched_text = ""
    match_kind = "browse"

    for literal in [*aliases, *exact_phrases]:
        normalized_literal = _normalize(literal)
        if not normalized_literal:
            continue
        if normalized_literal in normalized_subject:
            score = max(score, 8.0 if literal in aliases else 6.0)
            matched_field = "subject"
            matched_text = literal
            match_kind = "exact_unicode" if literal in aliases else "exact_phrase"
        elif normalized_literal in normalized_body:
            score = max(score, 7.0 if literal in aliases else 5.0)
            matched_field = "body"
            matched_text = literal
            match_kind = "exact_unicode" if literal in aliases else "exact_phrase"

    subject_words = set(_words(normalized_subject))
    body_words = set(_words(normalized_body))
    subject_matches = [term for term in terms if term in subject_words]
    body_matches = [term for term in terms if term in body_words]
    if subject_matches:
        score += 1.2 * len(subject_matches)
        if not matched_text:
            matched_field = "subject"
            matched_text = subject_matches[0]
            match_kind = "whole_word"
    if body_matches:
        score += 0.8 * len(body_matches)
        if not matched_text:
            matched_field = "body"
            matched_text = body_matches[0]
            match_kind = "whole_word"

    if score <= 0 and not browse:
        return None
    if browse:
        score = max(score, 0.1)

    source_text = subject if matched_field == "subject" else body
    snippet, line_match_kind = _matching_line(
        source_text,
        [*aliases, *exact_phrases],
        terms,
    )
    if match_kind == "browse":
        match_kind = line_match_kind

    return {
        **{key: value for key, value in record.items() if key != "body"},
        "score": round(score, 2),
        "matched_field": matched_field,
        "matched_text": matched_text,
        "match_kind": match_kind,
        "snippet": snippet,
    }


def search_history(
    query: str,
    *,
    root: str = ".",
    scope: str | None = None,
    all_refs: bool = False,
    since: str | None = None,
    until: str | None = None,
    limit: int = 10,
    max_commits: int = DEFAULT_MAX_COMMITS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Search commit subjects and bodies through Git and ripgrep."""

    started = time.monotonic()
    result_limit = max(1, min(int(limit), 100))
    commit_limit = max(1, min(int(max_commits), 100_000))
    deadline = max(0.1, min(float(timeout_seconds), 30.0))
    requested_root = Path(root).resolve()
    repo_root = _git_root(requested_root, deadline)
    if repo_root is None:
        return {
            "query": query,
            "root": str(requested_root),
            "results": [],
            "error_code": "history_unavailable",
            "reason": "The requested path is not inside a readable Git worktree.",
            "fallback_used": ["git_worktree_unavailable"],
        }

    plan = _query_plan(query)
    blob, git_error = _history_blob(
        repo_root,
        all_refs=all_refs,
        max_commits=commit_limit,
        timeout_seconds=deadline,
        since=since,
        until=until,
        scope=scope,
    )
    if git_error:
        return {
            "query": query,
            "root": str(repo_root),
            "results": [],
            "error_code": "history_unavailable",
            "reason": git_error,
            "fallback_used": [git_error],
        }

    records = [
        record for raw in blob.split(RECORD_SEPARATOR) if (record := _parse_record(raw)) is not None
    ]
    candidate_records = records
    rg_error = None
    if not plan["browse"] and plan["patterns"]:
        candidates, rg_error = _candidate_blob(
            blob,
            list(plan["patterns"]),
            repo_root=repo_root,
            timeout_seconds=deadline,
        )
        candidate_records = [
            record
            for raw in candidates.split(RECORD_SEPARATOR)
            if (record := _parse_record(raw)) is not None
        ]
        if rg_error:
            candidate_records = records

    ranked = [
        ranked_record
        for record in candidate_records
        if (ranked_record := _rank_record(record, plan)) is not None
    ]
    ranked.sort(
        key=lambda item: (float(item["score"]), str(item["authored_at"])),
        reverse=True,
    )
    fallback_used = [rg_error] if rg_error else []
    return {
        "query": query,
        "root": str(repo_root),
        "scope": scope,
        "all_refs": all_refs,
        "since": since,
        "until": until,
        "search_backend": "git+rg",
        "examined_commits": len(records),
        "candidate_commits": len(candidate_records),
        "history_truncated": len(records) >= commit_limit,
        "limit": result_limit,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
        "query_aliases": list(plan["aliases"]),
        "query_terms": list(plan["terms"]),
        "results": ranked[:result_limit],
        "fallback_used": fallback_used,
    }
