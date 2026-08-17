"""Tests for tldreadme.docfetch - the privacy-gated dependency-doc fetcher.

These never touch the network: the remote backend is dependency-injected, so the
egress path is exercised with a fake resolver and asserted to carry ONLY the
allowlisted payload.
"""

import json

import pytest

from tldreadme import docfetch


# --------------------------------------------------------------------------- #
# Config / mode gate
# --------------------------------------------------------------------------- #

def test_default_mode_is_off(monkeypatch):
    monkeypatch.delenv("TLDR_DEP_DOCS", raising=False)
    assert docfetch.mode() == "off"


def test_unknown_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("TLDR_DEP_DOCS", "yolo")
    assert docfetch.mode() == "off"


def test_modes_normalize(monkeypatch):
    monkeypatch.setenv("TLDR_DEP_DOCS", "  CONTEXT7 ")
    assert docfetch.mode() == "context7"


# --------------------------------------------------------------------------- #
# Egress choke point
# --------------------------------------------------------------------------- #

def test_egress_payload_only_allowlisted_keys():
    payload = docfetch.build_egress_payload("fastapi", "0.110.0", "pypi", "routing")
    assert set(payload.keys()) == set(docfetch.EGRESS_ALLOWED_KEYS)
    assert payload["library"] == "fastapi"
    assert payload["version"] == "0.110.0"
    assert payload["ecosystem"] == "pypi"
    assert payload["topic"] == "routing"


def test_egress_payload_revalidates_topic():
    with pytest.raises(docfetch.PrivacyError):
        docfetch.build_egress_payload("fastapi", "1.0", "pypi", "../etc/passwd")


# --------------------------------------------------------------------------- #
# Topic privacy validation
# --------------------------------------------------------------------------- #

def test_topic_default_overview():
    assert docfetch.assert_generic_topic("") == "overview"
    assert docfetch.assert_generic_topic(None) == "overview"


def test_topic_trims():
    assert docfetch.assert_generic_topic("  authentication setup ") == "authentication setup"


def test_topic_rejects_paths():
    for leaky in ("../secrets/key", "/Users/mfk/proj/main.py", "./local/thing", "~/proj"):
        with pytest.raises(docfetch.PrivacyError):
            docfetch.assert_generic_topic(leaky)


def test_topic_rejects_code():
    for leaky in ("def handler(): pass", "config = {a: 1}", "a; b", "use `secret_key` here"):
        with pytest.raises(docfetch.PrivacyError):
            docfetch.assert_generic_topic(leaky)


def test_topic_rejects_project_identity():
    with pytest.raises(docfetch.PrivacyError):
        docfetch.assert_generic_topic("wiring for portland daemon", project_name="portland")
    with pytest.raises(docfetch.PrivacyError):
        docfetch.assert_generic_topic("how myrepo uses this", project_root="/x/y/myrepo")


def test_topic_rejects_multiline_and_overlong():
    with pytest.raises(docfetch.PrivacyError):
        docfetch.assert_generic_topic("line one\nline two")
    with pytest.raises(docfetch.PrivacyError):
        docfetch.assert_generic_topic("x" * (docfetch.MAX_TOPIC_LEN + 1))


def test_topic_allows_ordinary_questions():
    # These contain words like import/return but are legitimate doc questions.
    for ok in (
        "how to import data",
        "return value semantics",
        "authentication with JWT",
        "dependency injection overview",
        "TLS/SSL configuration",
    ):
        assert docfetch.assert_generic_topic(ok) == ok


# --------------------------------------------------------------------------- #
# dep_docs end to end (no network)
# --------------------------------------------------------------------------- #

def _no_call_resolver(payload):
    raise AssertionError(f"resolver must not be called; would have sent {payload}")


def test_no_library_lists_manifest_deps(tmp_path, monkeypatch):
    monkeypatch.setenv("TLDR_DEP_DOCS", "off")
    (tmp_path / "requirements.txt").write_text("requests==2.32.0\nhttpx>=0.27\n")
    result = docfetch.dep_docs(root=str(tmp_path))
    assert result["status"] == "need-library"
    assert "requests" in result["text"]
    assert result["egress_payload"] is None


def test_off_mode_disabled_no_egress(monkeypatch):
    monkeypatch.setenv("TLDR_DEP_DOCS", "off")
    result = docfetch.dep_docs(library="fastapi", topic="routing", resolver=_no_call_resolver)
    assert result["status"] == "disabled"
    assert result["egress_payload"] is None


def test_leaky_topic_returns_rejected_not_raise(monkeypatch):
    monkeypatch.setenv("TLDR_DEP_DOCS", "context7")
    result = docfetch.dep_docs(
        library="fastapi", topic="/Users/mfk/secret.py", resolver=_no_call_resolver
    )
    assert result["status"] == "rejected"
    assert result["egress_payload"] is None


def test_local_mode_never_calls_remote(monkeypatch):
    monkeypatch.setenv("TLDR_DEP_DOCS", "local")
    result = docfetch.dep_docs(
        library="pkg-not-installed-xyz", topic="overview", resolver=_no_call_resolver
    )
    assert result["source"] == "local"
    assert result["egress_payload"] is None
    assert result["status"] in ("ok", "unavailable")


def test_context7_egress_is_allowlisted_only(monkeypatch, tmp_path):
    monkeypatch.setenv("TLDR_DEP_DOCS", "context7")
    captured = {}

    def fake(payload):
        captured.update(payload)
        return "DOC BODY", "fake"

    result = docfetch.dep_docs(
        library="fastapi", topic="routing", version="0.110.0",
        root=str(tmp_path), resolver=fake,
    )
    assert result["status"] == "ok"
    assert result["source"] == "context7"
    # Only allowlisted keys egressed; nothing project-identifying rode along.
    assert set(captured.keys()) <= set(docfetch.EGRESS_ALLOWED_KEYS)
    assert captured["library"] == "fastapi"
    assert captured["version"] == "0.110.0"
    assert str(tmp_path) not in json.dumps(captured)
    assert result["egress_payload"] == captured


def test_context7_uses_manifest_pinned_version(monkeypatch, tmp_path):
    monkeypatch.setenv("TLDR_DEP_DOCS", "context7")
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "version": "1.0.0", "dependencies": {"react": "18.3.1"}})
    )
    captured = {}

    def fake(payload):
        captured.update(payload)
        return "docs", "fake"

    docfetch.dep_docs(library="react", topic="hooks", root=str(tmp_path), resolver=fake)
    assert captured["version"] == "18.3.1"
    assert captured["ecosystem"] == "npm"


def test_cache_hit_avoids_second_egress(monkeypatch, tmp_path):
    monkeypatch.setenv("TLDR_DEP_DOCS", "context7")
    calls = {"n": 0}

    def once(payload):
        calls["n"] += 1
        return "CACHED BODY", "fake"

    first = docfetch.dep_docs(
        library="fastapi", topic="routing", version="0.110.0",
        root=str(tmp_path), resolver=once,
    )
    assert first["source"] == "context7"
    assert calls["n"] == 1

    second = docfetch.dep_docs(
        library="fastapi", topic="routing", version="0.110.0",
        root=str(tmp_path), resolver=_no_call_resolver,
    )
    assert second["source"] == "cache"
    assert second["text"] == "CACHED BODY"


def test_remote_failure_degrades_gracefully(monkeypatch, tmp_path):
    monkeypatch.setenv("TLDR_DEP_DOCS", "context7")

    def failing(payload):
        return None, "context7 request failed: URLError"

    result = docfetch.dep_docs(
        library="pkg-not-installed-xyz", topic="overview",
        root=str(tmp_path), resolver=failing,
    )
    assert result["status"] == "unavailable"
    assert "local" in result["fallback_used"]


# --------------------------------------------------------------------------- #
# Manifest-grounded version resolution (local, no egress)
# --------------------------------------------------------------------------- #

def test_resolve_version_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.0.0"\n'
        'dependencies = ["httpx>=0.27.0"]\n'
    )
    version, ecosystem = docfetch.resolve_from_manifest("httpx", str(tmp_path))
    assert ecosystem == "pypi"
    assert "0.27.0" in version


def test_available_dep_names(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.32.0\nrich>=13\n")
    names = docfetch.available_dep_names(str(tmp_path))
    assert "requests" in names
    assert "rich" in names


# --------------------------------------------------------------------------- #
# Local pypi backend (reads installed metadata only)
# --------------------------------------------------------------------------- #

def test_local_pypi_reads_installed_metadata():
    from importlib import metadata

    installed = [
        d.metadata["Name"]
        for d in metadata.distributions()
        if d.metadata and d.metadata.get("Name")
    ]
    assert installed, "expected at least one installed distribution"
    name = "pytest" if "pytest" in installed else installed[0]
    text, version, provenance = docfetch._local_pypi_docs(name)
    assert version, "installed version should resolve from importlib metadata"
    assert provenance
