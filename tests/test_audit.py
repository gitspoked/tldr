"""Tests for the local audit runner."""

import json
from pathlib import Path
from types import SimpleNamespace

from tldreadme import audit


def _audit_check(
    name: str,
    tool_id: str,
    status: str = "ok",
    details: str = "ready",
    install_options: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "tool_id": tool_id,
        "status": status,
        "details": details,
        "install_options": install_options or [],
    }


def test_run_audit_prefers_first_available_scanner(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audit,
        "audit_tool_checks",
        lambda _categories=None: [
            _audit_check("OSV-Scanner", "osv-scanner"),
            _audit_check("pip-audit", "pip-audit"),
        ],
    )

    monkeypatch.setitem(
        audit.RUNNERS,
        "pip-audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback runner should not execute")),
    )
    monkeypatch.setattr(
        audit,
        "_run_osv_with_options",
        lambda root, *, dry_run, install_options, offline=False, download_offline_db=False: audit._base_result(
            "OSV-Scanner",
            "ok",
            f"scanned {root}",
            command=["osv-scanner", "scan"],
            findings=[],
            install_options=install_options,
        ),
    )

    report = audit.run_audit("deps", root=str(tmp_path))

    assert report["ok"] is True
    assert report["scanners"][0]["name"] == "OSV-Scanner"
    assert report["scanners"][0]["status"] == "ok"
    assert report["scanners"][1]["status"] == "skip"


def test_run_audit_marks_missing_required_scanner_not_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audit,
        "audit_tool_checks",
        lambda _categories=None: [
            _audit_check(
                "OSV-Scanner",
                "osv-scanner",
                status="warn",
                details="missing",
                install_options=[{"label": "Install OSV-Scanner", "command": "brew install osv-scanner"}],
            ),
            _audit_check("pip-audit", "pip-audit", status="warn", details="missing"),
        ],
    )

    report = audit.run_audit("deps", root=str(tmp_path))

    assert report["ok"] is False
    assert "Install OSV-Scanner" in report["recommended_next_action"]


def test_run_audit_llm_requires_config(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audit,
        "audit_tool_checks",
        lambda _categories=None: [
            _audit_check("Garak", "garak"),
        ],
    )

    report = audit.run_audit("llm", root=str(tmp_path))

    assert report["ok"] is True
    assert report["status"] == "skip"
    assert report["scanners"][0]["status"] == "skip"
    assert "--garak-config" in report["scanners"][0]["details"]


def test_run_osv_offline_flags_show_up_in_dry_run(tmp_path):
    result = audit._run_osv_with_options(
        tmp_path,
        dry_run=True,
        install_options=[],
        offline=True,
        download_offline_db=True,
    )

    assert "--offline" in result["command"]
    assert "--download-offline-databases" in result["command"]


def test_run_audit_annotates_known_exploited_findings(monkeypatch, tmp_path):
    kev_path = tmp_path / "kev.json"
    kev_path.write_text(
        json.dumps(
            {
                "vulnerabilities": [
                    {
                        "cveID": "CVE-2024-9999",
                        "vendorProject": "Example",
                        "product": "Demo",
                        "dueDate": "2026-04-01",
                    }
                ]
            }
        )
    )

    monkeypatch.setattr(
        audit,
        "audit_tool_checks",
        lambda _categories=None: [_audit_check("OSV-Scanner", "osv-scanner")],
    )
    monkeypatch.setitem(
        audit.RUNNERS,
        "pip-audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback runner should not execute")),
    )
    monkeypatch.setattr(
        audit,
        "_run_osv_with_options",
        lambda root, *, dry_run, install_options, offline=False, download_offline_db=False: audit._base_result(
            "OSV-Scanner",
            "warn",
            f"scanned {root}",
            command=["osv-scanner", "scan"],
            findings=[
                {
                    "id": "CVE-2024-9999",
                    "title": "Known issue",
                    "severity": "medium",
                    "aliases": [],
                }
            ],
            install_options=install_options,
        ),
    )

    report = audit.run_audit("deps", root=str(tmp_path), kev_catalog_path=str(kev_path))

    assert report["summary"]["kev"] == 1
    assert report["scanners"][0]["findings"][0]["known_exploited"] is True
    assert "known exploited" in report["recommended_next_action"].lower()


def test_run_audit_attaches_policy_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audit,
        "audit_tool_checks",
        lambda _categories=None: [_audit_check("Semgrep", "semgrep")],
    )
    monkeypatch.setitem(
        audit.RUNNERS,
        "semgrep",
        lambda root, *, dry_run, install_options: audit._base_result(
            "Semgrep",
            "ok",
            f"scanned {root}",
            command=["python", "-m", "semgrep"],
            findings=[],
            install_options=install_options,
        ),
    )

    report = audit.run_audit("code", root=str(tmp_path), profile="owasp-mcp")

    assert report["policy_profile"]["id"] == "owasp-mcp"
    assert report["policy_profile"]["recommended_categories"] == ["code", "secrets", "llm"]


def test_run_audit_can_prefer_snyk(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audit,
        "audit_tool_checks",
        lambda _categories=None: [
            _audit_check("OSV-Scanner", "osv-scanner"),
            _audit_check("pip-audit", "pip-audit"),
            _audit_check("Snyk Open Source", "snyk-oss"),
        ],
    )
    monkeypatch.setattr(
        audit,
        "_run_osv_with_options",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local scanner should not execute when Snyk is preferred")),
    )
    monkeypatch.setitem(
        audit.RUNNERS,
        "snyk-oss",
        lambda root, *, dry_run, install_options: audit._base_result(
            "Snyk Open Source",
            "ok",
            f"scanned {root}",
            command=["snyk", "test"],
            findings=[],
            install_options=install_options,
        ),
    )

    report = audit.run_audit("deps", root=str(tmp_path), prefer_snyk=True)

    assert report["selected_scanner"] == "snyk-oss"
    assert report["scanners"][0]["name"] == "Snyk Open Source"


def test_refresh_kev_catalog_writes_json(monkeypatch, tmp_path):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"vulnerabilities":[{"cveID":"CVE-2026-0001"}]}'

    monkeypatch.setattr(audit, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    result = audit.refresh_kev_catalog(output_path=str(tmp_path / "kev.json"), url="https://example.com/kev.json")

    assert result["count"] == 1
    assert Path(result["path"]).exists()


def test_list_policy_profiles_includes_owasp_mcp():
    profiles = audit.list_policy_profiles()

    assert any(profile["id"] == "owasp-mcp" for profile in profiles)


def test_save_and_read_security_state(tmp_path):
    report = {
        "category": "deps",
        "root": str(tmp_path),
        "summary": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0, "unknown": 0, "kev": 0, "total": 0},
        "status": "ok",
        "ok": True,
    }

    saved = audit.save_audit_report(report, root=str(tmp_path))
    state = audit.read_security_state(root=str(tmp_path))

    assert Path(saved["path"]).exists()
    assert state["latest_audit_path"] == saved["latest_path"]
    assert state["latest_report"]["category"] == "deps"


def test_run_semgrep_parses_json_findings(monkeypatch, tmp_path):
    monkeypatch.setattr(
        audit.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"results":[{"check_id":"python.lang.security","path":"app.py","start":{"line":7},"extra":{"message":"unsafe call","severity":"ERROR"}}]}',
            stderr="",
        ),
    )

    result = audit._run_semgrep(tmp_path, dry_run=False, install_options=[])

    assert result["status"] == "warn"
    assert result["summary"]["high"] == 1
    assert result["findings"][0]["path"] == "app.py"


def test_render_audit_report_lists_next_action():
    output = audit.render_audit_report(
        {
            "category": "deps",
            "root": "/repo",
            "summary": {"critical": 0, "high": 1, "medium": 0, "low": 0, "unknown": 0, "total": 1},
            "scanners": [
                {
                    "name": "OSV-Scanner",
                    "status": "warn",
                    "details": "1 findings reported.",
                    "command_display": "osv-scanner scan --format json /repo",
                    "install_options": [],
                }
            ],
            "recommended_next_action": "Review the reported findings, fix the highest-severity issues, then rerun this audit.",
        }
    )

    assert "Audit: deps (/repo)" in output
    assert "WARN: OSV-Scanner - 1 findings reported." in output
    assert "Next: Review the reported findings" in output
