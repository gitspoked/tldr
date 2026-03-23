"""Tests for the local audit runner."""

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
        "osv-scanner",
        lambda root, *, dry_run, install_options: audit._base_result(
            "OSV-Scanner",
            "ok",
            f"scanned {root}",
            command=["osv-scanner", "scan"],
            findings=[],
            install_options=install_options,
        ),
    )
    monkeypatch.setitem(
        audit.RUNNERS,
        "pip-audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback runner should not execute")),
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
