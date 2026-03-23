"""Tests for runtime dependency checks."""

import pytest

from tldreadme import runtime


def test_ensure_rg_runtime_missing(monkeypatch):
    monkeypatch.setattr(runtime, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="ripgrep"):
        runtime.ensure_rg_runtime()


def test_optional_lsp_checks_warn_when_missing(monkeypatch):
    monkeypatch.setattr(runtime, "which", lambda _name: None)

    checks = runtime.optional_lsp_checks()

    assert checks
    assert all(check["status"] == "warn" for check in checks)
    assert any(check["name"] == "Python LSP" for check in checks)


def test_optional_service_checks_skip_unconfigured_litellm(monkeypatch):
    monkeypatch.setenv("LITELLM_URL", "")
    checks = runtime.optional_service_checks()

    litellm = next(check for check in checks if check["name"] == "LiteLLM")
    assert litellm["status"] == "skip"


def test_optional_service_checks_skip_when_connectivity_probe_blocked(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setattr(runtime, "_check_socket", lambda _url: (_ for _ in ()).throw(PermissionError("blocked")))

    checks = runtime.optional_service_checks()

    qdrant = next(check for check in checks if check["name"] == "Qdrant")
    assert qdrant["status"] == "skip"


def test_runtime_report_keeps_optional_warnings_non_fatal(monkeypatch):
    monkeypatch.setattr(runtime, "ensure_tree_sitter_runtime", lambda: {"tree-sitter": "0.21.3", "tree-sitter-languages": "1.10.2"})
    monkeypatch.setattr(runtime, "get_rg_version", lambda: "ripgrep 14.1.1")
    monkeypatch.setattr(runtime, "optional_service_checks", lambda: [runtime._check("Qdrant", "warn", "offline", category="service")])
    monkeypatch.setattr(runtime, "optional_lsp_checks", lambda: [runtime._check("Python LSP", "warn", "missing", category="lsp")])

    report = runtime.runtime_report()

    assert report["ok"] is True
    assert any(check["status"] == "warn" for check in report["checks"])
    assert any(check["install_options"] for check in report["checks"] if check["status"] != "ok")


def test_install_options_for_python_lsp_include_npm_when_available(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "which",
        lambda name: {
            "npm": "/opt/homebrew/bin/npm",
        }.get(name),
    )

    options = runtime.install_options_for_check(runtime._check("Python LSP", "warn", "missing", category="lsp"))

    assert any(option["command"] == "npm install -g basedpyright" for option in options)


def test_capability_report_derives_backend_availability(monkeypatch):
    monkeypatch.setattr(runtime, "which", lambda name: "/usr/bin/git" if name == "git" else None)

    report = {
        "ok": True,
        "checks": [
            runtime._check("tree-sitter", "ok", "ready", category="runtime"),
            runtime._check("ripgrep", "ok", "ready", category="runtime"),
            runtime._check("Qdrant", "warn", "offline", category="service"),
            runtime._check("FalkorDB", "ok", "ready", category="service"),
            runtime._check("Ollama", "ok", "ready", category="service"),
            runtime._check("Python LSP", "warn", "missing", category="lsp"),
            runtime._check("TypeScript/JavaScript LSP", "ok", "ready", category="lsp"),
        ],
    }

    capabilities = runtime.capability_report(report)

    assert capabilities["backends"]["asts"] is True
    assert capabilities["backends"]["rg"] is True
    assert capabilities["backends"]["vector"] is False
    assert capabilities["backends"]["graph"] is True
    assert capabilities["backends"]["llm"] is True
    assert capabilities["backends"]["lsp"] is True
    assert capabilities["backends"]["git"] is True


def test_audit_tool_checks_report_binary_and_python_scanners(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "which",
        lambda name: {
            "osv-scanner": "/opt/homebrew/bin/osv-scanner",
            "gitleaks": "/opt/homebrew/bin/gitleaks",
        }.get(name),
    )
    monkeypatch.setattr(
        runtime,
        "find_spec",
        lambda name: object() if name in {"pip_audit", "semgrep"} else None,
    )
    monkeypatch.setattr(runtime, "version", lambda _name: "1.2.3")

    checks = runtime.audit_tool_checks(("deps", "code", "secrets"))

    assert any(check["name"] == "OSV-Scanner" and check["status"] == "ok" for check in checks)
    assert any(check["name"] == "pip-audit" and check["status"] == "ok" for check in checks)
    assert any(check["name"] == "Semgrep" and check["status"] == "ok" for check in checks)
    assert any(check["name"] == "Bandit" and check["status"] == "warn" for check in checks)
    assert any(check["name"] == "Gitleaks" and check["status"] == "ok" for check in checks)


def test_install_options_for_semgrep_include_python_install():
    options = runtime.install_options_for_check(runtime._check("Semgrep", "warn", "missing", category="audit"))

    assert any(option["command"] == "python3.12 -m pip install semgrep" for option in options)


def test_install_options_for_snyk_include_auth(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "which",
        lambda name: {
            "npm": "/opt/homebrew/bin/npm",
            "brew": "/opt/homebrew/bin/brew",
        }.get(name),
    )

    options = runtime.install_options_for_check(runtime._check("Snyk Open Source", "warn", "missing", category="audit"))

    assert any(option["command"] == "snyk auth" for option in options)
