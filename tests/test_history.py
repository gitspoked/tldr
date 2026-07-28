"""Tests for bounded Git history search."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tldreadme.history import query_requests_history, search_history


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(
    root: Path,
    subject: str,
    *,
    body: str | None = None,
    authored_at: str,
    filename: str = "notes.txt",
) -> str:
    path = root / filename
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{existing}{subject}\n", encoding="utf-8")
    _git(root, "add", filename)
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": authored_at,
        "GIT_COMMITTER_DATE": authored_at,
    }
    args = ["commit", "-m", subject]
    if body:
        args.extend(["-m", body])
    _git(root, *args, env=env)
    return _git(root, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "History Tester")
    _git(root, "config", "user.email", "history@example.test")
    return root


def test_history_search_finds_old_unicode_alias_in_subject(tmp_path):
    root = _repo(tmp_path)
    mark = chr(0x2014)
    commit = _commit(
        root,
        f"Initial scaffold: package {mark} concise context",
        authored_at="2026-03-12T12:00:00+00:00",
    )
    _commit(
        root,
        "Routine April cleanup",
        authored_at="2026-04-18T12:00:00+00:00",
    )

    result = search_history("em dash in commit messages", root=str(root))

    assert result["search_backend"] == "git+rg"
    assert result["results"][0]["commit"] == commit
    assert result["results"][0]["matched_field"] == "subject"
    assert result["results"][0]["match_kind"] == "exact_unicode"
    assert result["query_aliases"] == [mark]


def test_history_search_finds_commit_body_and_date_window(tmp_path):
    root = _repo(tmp_path)
    march = _commit(
        root,
        "March maintenance",
        body="No release marker here.",
        authored_at="2026-03-12T12:00:00+00:00",
    )
    april = _commit(
        root,
        "April maintenance",
        body="Migration detail: quartz-body-marker retained.",
        authored_at="2026-04-18T12:00:00+00:00",
    )

    result = search_history(
        "quartz body marker in commit body",
        root=str(root),
        since="2026-04-01",
        until="2026-04-30 23:59:59",
    )

    commits = [item["commit"] for item in result["results"]]
    assert april in commits
    assert march not in commits
    assert result["results"][0]["matched_field"] == "body"


def test_history_search_accepts_literal_unicode_and_codepoint(tmp_path):
    root = _repo(tmp_path)
    mark = chr(0x2014)
    commit = _commit(
        root,
        f"Keep punctuation {mark} intentional",
        authored_at="2026-03-12T12:00:00+00:00",
    )

    literal = search_history(mark, root=str(root))
    codepoint = search_history("U+2014 in Git history", root=str(root))

    assert literal["results"][0]["commit"] == commit
    assert literal["results"][0]["match_kind"] == "exact_unicode"
    assert codepoint["results"][0]["commit"] == commit


def test_history_search_rejects_raw_substring_false_positive(tmp_path):
    root = _repo(tmp_path)
    _commit(
        root,
        "Improve repository scanning",
        authored_at="2026-03-12T12:00:00+00:00",
    )

    result = search_history("dash in commit messages", root=str(root))

    assert result["results"] == []


def test_history_search_can_include_all_refs(tmp_path):
    root = _repo(tmp_path)
    _commit(
        root,
        "Main branch base",
        authored_at="2026-03-12T12:00:00+00:00",
    )
    _git(root, "checkout", "-b", "topic")
    topic_commit = _commit(
        root,
        "Topic-only nebula-history-token",
        authored_at="2026-04-18T12:00:00+00:00",
    )
    _git(root, "checkout", "main")

    current = search_history("nebula history token", root=str(root))
    all_refs = search_history("nebula history token", root=str(root), all_refs=True)

    assert current["results"] == []
    assert all_refs["results"][0]["commit"] == topic_commit


def test_history_search_honors_path_scope(tmp_path):
    root = _repo(tmp_path)
    in_scope = _commit(
        root,
        "Scoped cobalt token",
        authored_at="2026-03-12T12:00:00+00:00",
        filename="src/app.py",
    )
    _commit(
        root,
        "Outside cobalt token",
        authored_at="2026-04-18T12:00:00+00:00",
        filename="docs/notes.md",
    )

    result = search_history("cobalt token", root=str(root), scope="src")

    assert [item["commit"] for item in result["results"]] == [in_scope]


def test_history_search_reports_non_git_path(tmp_path):
    result = search_history("anything", root=str(tmp_path))

    assert result["results"] == []
    assert result["error_code"] == "history_unavailable"
    assert result["fallback_used"] == ["git_worktree_unavailable"]


def test_history_intent_requires_history_language_or_source_filter():
    assert query_requests_history("which commit introduced setup")
    assert query_requests_history("punctuation", ["history"])
    assert not query_requests_history("parser guard")
