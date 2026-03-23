"""Local-first audit wrappers for dependency, code, secrets, and LLM checks."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import shlex
import subprocess
import sys

from .runtime import audit_tool_checks

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info", "unknown")
CATEGORY_SCANNERS = {
    "deps": ("osv-scanner", "pip-audit"),
    "code": ("semgrep", "bandit"),
    "secrets": ("gitleaks",),
    "llm": ("garak",),
}


def _empty_summary() -> dict[str, int]:
    """Build an empty severity summary."""

    summary = {level: 0 for level in SEVERITY_ORDER}
    summary["total"] = 0
    return summary


def _normalize_severity(value: object) -> str:
    """Map tool-specific severities into the shared buckets."""

    text = str(value or "").strip().lower()
    if text in {"critical", "high", "medium", "low", "info"}:
        return text
    if text in {"warning", "warn"}:
        return "medium"
    if text in {"error", "severe"}:
        return "high"
    return "unknown"


def _summarize_findings(findings: list[dict[str, object]]) -> dict[str, int]:
    """Summarize normalized findings by severity."""

    summary = _empty_summary()
    for finding in findings:
        severity = _normalize_severity(finding.get("severity"))
        summary[severity] += 1
        summary["total"] += 1
    return summary


def _shell_join(command: list[str]) -> str:
    """Render a command list for human-facing output."""

    return " ".join(shlex.quote(part) for part in command)


def _base_result(
    name: str,
    status: str,
    details: str,
    *,
    command: list[str] | None = None,
    findings: list[dict[str, object]] | None = None,
    install_options: list[dict[str, str]] | None = None,
    artifacts: list[str] | None = None,
    raw_stderr: str | None = None,
) -> dict[str, object]:
    """Build one normalized scanner result."""

    findings = findings or []
    return {
        "name": name,
        "status": status,
        "details": details,
        "command": command or [],
        "command_display": _shell_join(command or []),
        "findings": findings,
        "summary": _summarize_findings(findings),
        "install_options": install_options or [],
        "artifacts": artifacts or [],
        "stderr": (raw_stderr or "").strip(),
    }


def _parse_json(stdout: str) -> object | None:
    """Safely parse JSON output from a scanner."""

    if not stdout.strip():
        return None

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def _run_json_command(
    name: str,
    command: list[str],
    parser,
    *,
    dry_run: bool,
    cwd: str | None = None,
    success_codes: tuple[int, ...] = (0, 1),
    install_options: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    """Run a scanner that emits JSON to stdout."""

    if dry_run:
        return _base_result(
            name,
            "skip",
            "Dry run only; command was not executed.",
            command=command,
            install_options=install_options,
        )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=cwd,
    )

    payload = _parse_json(result.stdout)
    if payload is None:
        details = result.stderr.strip() or "Scanner did not emit JSON output."
        status = "error" if result.returncode not in success_codes else "warn"
        return _base_result(
            name,
            status,
            details,
            command=command,
            install_options=install_options,
            raw_stderr=result.stderr,
        )

    findings = parser(payload)
    summary = _summarize_findings(findings)
    if result.returncode not in success_codes and not findings:
        return _base_result(
            name,
            "error",
            result.stderr.strip() or f"{name} exited with status {result.returncode}.",
            command=command,
            install_options=install_options,
            raw_stderr=result.stderr,
        )

    details = "No findings reported." if summary["total"] == 0 else f"{summary['total']} findings reported."
    return _base_result(
        name,
        "ok" if summary["total"] == 0 else "warn",
        details,
        command=command,
        findings=findings,
        install_options=install_options,
        raw_stderr=result.stderr,
    )


def _parse_osv(payload: object) -> list[dict[str, object]]:
    """Normalize OSV-Scanner JSON results."""

    findings: list[dict[str, object]] = []
    if not isinstance(payload, dict):
        return findings

    for result in payload.get("results", []):
        source = result.get("source", {}) if isinstance(result, dict) else {}
        source_path = source.get("path")
        for package_entry in result.get("packages", []):
            package = package_entry.get("package", {}) if isinstance(package_entry, dict) else {}
            package_name = package.get("name")
            package_version = package.get("version")
            for vulnerability in package_entry.get("vulnerabilities", []):
                db_specific = vulnerability.get("database_specific", {}) if isinstance(vulnerability, dict) else {}
                findings.append(
                    {
                        "id": vulnerability.get("id"),
                        "title": vulnerability.get("summary") or vulnerability.get("id") or "OSV vulnerability",
                        "severity": db_specific.get("severity") or vulnerability.get("severity") or "unknown",
                        "path": source_path,
                        "package": package_name,
                        "version": package_version,
                        "aliases": vulnerability.get("aliases", []),
                    }
                )
    return findings


def _parse_pip_audit(payload: object) -> list[dict[str, object]]:
    """Normalize pip-audit JSON results."""

    findings: list[dict[str, object]] = []
    if isinstance(payload, list):
        dependencies = payload
    elif isinstance(payload, dict):
        dependencies = payload.get("dependencies", payload.get("results", []))
    else:
        return findings

    for dependency in dependencies:
        package_name = dependency.get("name") or dependency.get("package")
        package_version = dependency.get("version")
        for vulnerability in dependency.get("vulns", dependency.get("vulnerabilities", [])):
            findings.append(
                {
                    "id": vulnerability.get("id"),
                    "title": vulnerability.get("description") or vulnerability.get("id") or "Dependency vulnerability",
                    "severity": vulnerability.get("severity") or "unknown",
                    "package": package_name,
                    "version": package_version,
                    "fix_versions": vulnerability.get("fix_versions", []),
                }
            )
    return findings


def _parse_semgrep(payload: object) -> list[dict[str, object]]:
    """Normalize Semgrep JSON results."""

    findings: list[dict[str, object]] = []
    if not isinstance(payload, dict):
        return findings

    for result in payload.get("results", []):
        extra = result.get("extra", {}) if isinstance(result, dict) else {}
        findings.append(
            {
                "id": result.get("check_id"),
                "title": extra.get("message") or result.get("check_id") or "Semgrep finding",
                "severity": extra.get("severity") or result.get("severity") or "unknown",
                "path": result.get("path"),
                "line": (result.get("start") or {}).get("line"),
            }
        )
    return findings


def _parse_bandit(payload: object) -> list[dict[str, object]]:
    """Normalize Bandit JSON results."""

    findings: list[dict[str, object]] = []
    if not isinstance(payload, dict):
        return findings

    for result in payload.get("results", payload.get("issues", [])):
        findings.append(
            {
                "id": result.get("test_id"),
                "title": result.get("issue_text") or result.get("test_name") or "Bandit finding",
                "severity": result.get("issue_severity") or "unknown",
                "path": result.get("filename"),
                "line": result.get("line_number"),
                "confidence": result.get("issue_confidence"),
            }
        )
    return findings


def _parse_gitleaks(payload: object) -> list[dict[str, object]]:
    """Normalize Gitleaks JSON results."""

    findings: list[dict[str, object]] = []
    if isinstance(payload, dict):
        entries = payload.get("findings", payload.get("results", []))
    elif isinstance(payload, list):
        entries = payload
    else:
        return findings

    for entry in entries:
        findings.append(
            {
                "id": entry.get("RuleID"),
                "title": entry.get("Description") or entry.get("RuleID") or "Secret finding",
                "severity": entry.get("Severity") or "high",
                "path": entry.get("File"),
                "line": entry.get("StartLine"),
                "match": entry.get("Match"),
            }
        )
    return findings


def _run_osv(root: Path, *, dry_run: bool, install_options: list[dict[str, str]]) -> dict[str, object]:
    return _run_json_command(
        "OSV-Scanner",
        ["osv-scanner", "scan", "--format", "json", str(root)],
        _parse_osv,
        dry_run=dry_run,
        install_options=install_options,
    )


def _run_pip_audit(root: Path, *, dry_run: bool, install_options: list[dict[str, str]]) -> dict[str, object]:
    del root
    return _run_json_command(
        "pip-audit",
        [sys.executable, "-m", "pip_audit", "--format", "json"],
        _parse_pip_audit,
        dry_run=dry_run,
        install_options=install_options,
    )


def _run_semgrep(root: Path, *, dry_run: bool, install_options: list[dict[str, str]]) -> dict[str, object]:
    return _run_json_command(
        "Semgrep",
        [sys.executable, "-m", "semgrep", "scan", "--config", "auto", "--json", str(root)],
        _parse_semgrep,
        dry_run=dry_run,
        install_options=install_options,
    )


def _run_bandit(root: Path, *, dry_run: bool, install_options: list[dict[str, str]]) -> dict[str, object]:
    return _run_json_command(
        "Bandit",
        [sys.executable, "-m", "bandit", "-r", str(root), "-f", "json"],
        _parse_bandit,
        dry_run=dry_run,
        install_options=install_options,
    )


def _run_gitleaks(root: Path, *, dry_run: bool, install_options: list[dict[str, str]]) -> dict[str, object]:
    with TemporaryDirectory() as temp_dir:
        report_path = Path(temp_dir) / "gitleaks.json"
        command = [
            "gitleaks",
            "detect",
            "--no-git",
            "--source",
            str(root),
            "--report-format",
            "json",
            "--report-path",
            str(report_path),
        ]
        if dry_run:
            return _base_result(
                "Gitleaks",
                "skip",
                "Dry run only; command was not executed.",
                command=command,
                install_options=install_options,
            )

        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        payload = _parse_json(report_path.read_text() if report_path.exists() else "")
        if payload is None:
            details = result.stderr.strip() or "Gitleaks did not produce a JSON report."
            status = "error" if result.returncode not in (0, 1) else "warn"
            return _base_result(
                "Gitleaks",
                status,
                details,
                command=command,
                install_options=install_options,
                raw_stderr=result.stderr,
            )

        findings = _parse_gitleaks(payload)
        summary = _summarize_findings(findings)
        return _base_result(
            "Gitleaks",
            "ok" if summary["total"] == 0 else "warn",
            "No findings reported." if summary["total"] == 0 else f"{summary['total']} findings reported.",
            command=command,
            findings=findings,
            install_options=install_options,
            raw_stderr=result.stderr,
        )


def _run_garak(
    root: Path,
    *,
    dry_run: bool,
    garak_config: str | None,
    install_options: list[dict[str, str]],
) -> dict[str, object]:
    if not garak_config:
        return _base_result(
            "Garak",
            "skip",
            "LLM audits require --garak-config PATH so the target model and probes are explicit.",
            install_options=install_options,
        )

    command = [sys.executable, "-m", "garak", "--config", garak_config]
    if dry_run:
        return _base_result(
            "Garak",
            "skip",
            "Dry run only; command was not executed.",
            command=command,
            install_options=install_options,
        )

    with TemporaryDirectory() as temp_dir:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=900,
            cwd=temp_dir,
        )
        artifacts = sorted(
            str(path)
            for path in Path(temp_dir).glob("garak.*")
            if path.is_file()
        )
        if result.returncode not in (0, 1):
            return _base_result(
                "Garak",
                "error",
                result.stderr.strip() or f"Garak exited with status {result.returncode}.",
                command=command,
                install_options=install_options,
                raw_stderr=result.stderr,
            )

        details = "Generated Garak reports; inspect the saved artifacts for adversarial failures."
        return _base_result(
            "Garak",
            "ok",
            details,
            command=command,
            install_options=install_options,
            artifacts=artifacts,
            raw_stderr=result.stderr,
        )


RUNNERS = {
    "osv-scanner": _run_osv,
    "pip-audit": _run_pip_audit,
    "semgrep": _run_semgrep,
    "bandit": _run_bandit,
    "gitleaks": _run_gitleaks,
    "garak": _run_garak,
}


def _missing_checks_next_action(checks: list[dict[str, object]]) -> str:
    """Build a doctor-style next action for missing scanners."""

    missing = [check for check in checks if check.get("status") != "ok"]
    if not missing:
        return "Run the audit again after fixes, or expand to `tldr audit all` for broader coverage."

    first = missing[0]
    if first.get("install_options"):
        return f"Install {first['name']} or another supported scanner, then rerun this audit."
    return f"Provide the missing configuration for {first['name']} and rerun this audit."


def _merge_summaries(results: list[dict[str, object]]) -> dict[str, int]:
    """Merge scanner summaries into one category summary."""

    summary = _empty_summary()
    for result in results:
        result_summary = result.get("summary", {})
        for key in SEVERITY_ORDER:
            summary[key] += int(result_summary.get(key, 0))
        summary["total"] += int(result_summary.get("total", 0))
    return summary


def _category_status(scanners: list[dict[str, object]]) -> str:
    """Collapse scanner results into one category status."""

    statuses = [str(scanner.get("status")) for scanner in scanners]
    if "error" in statuses:
        return "error"
    if "warn" in statuses:
        return "warn"
    if "ok" in statuses:
        return "ok"
    return "skip"


def run_audit(
    category: str,
    *,
    root: str = ".",
    dry_run: bool = False,
    garak_config: str | None = None,
) -> dict[str, object]:
    """Run one audit category or the full audit suite."""

    root_path = Path(root).resolve()
    if category == "all":
        category_results = [
            run_audit(name, root=str(root_path), dry_run=dry_run, garak_config=garak_config)
            for name in CATEGORY_SCANNERS
        ]
        summary = _merge_summaries(category_results)
        return {
            "category": "all",
            "root": str(root_path),
            "ok": all(result.get("ok", False) for result in category_results),
            "status": _category_status(
                [
                    {"status": result.get("status", "warn")}
                    for result in category_results
                ]
            ),
            "summary": summary,
            "checks": [check for result in category_results for check in result.get("checks", [])],
            "scanners": [scanner for result in category_results for scanner in result.get("scanners", [])],
            "categories": category_results,
            "recommended_next_action": next(
                (
                    result.get("recommended_next_action")
                    for result in category_results
                    if result.get("recommended_next_action")
                ),
                "Review the category results and rerun any targeted audit after fixes.",
            ),
            "verification_commands": [f"tldr audit {name}" for name in CATEGORY_SCANNERS],
        }

    if category not in CATEGORY_SCANNERS:
        raise ValueError(f"Unsupported audit category: {category}")

    checks = audit_tool_checks((category,))
    checks_by_id = {str(check.get("tool_id")): check for check in checks}
    selected_tool_id = None
    if category != "llm" or garak_config:
        for tool_id in CATEGORY_SCANNERS[category]:
            check = checks_by_id.get(tool_id)
            if check and check.get("status") == "ok":
                selected_tool_id = tool_id
                break

    scanners: list[dict[str, object]] = []
    for tool_id in CATEGORY_SCANNERS[category]:
        check = checks_by_id[tool_id]
        install_options = list(check.get("install_options", []))
        if selected_tool_id == tool_id:
            runner = RUNNERS[tool_id]
            if tool_id == "garak":
                scanners.append(
                    runner(
                        root_path,
                        dry_run=dry_run,
                        garak_config=garak_config,
                        install_options=install_options,
                    )
                )
            else:
                scanners.append(runner(root_path, dry_run=dry_run, install_options=install_options))
            continue

        if check.get("status") == "ok" and selected_tool_id:
            scanners.append(
                _base_result(
                    str(check.get("name")),
                    "skip",
                    f"{checks_by_id[selected_tool_id]['name']} selected as the preferred {category} scanner.",
                    install_options=install_options,
                )
            )
            continue

        if category == "llm" and not garak_config:
            scanners.append(
                _base_result(
                    str(check.get("name")),
                    "skip",
                    "Provide --garak-config PATH to run adversarial LLM probes.",
                    install_options=install_options,
                )
            )
            continue

        scanners.append(
            _base_result(
                str(check.get("name")),
                "warn",
                str(check.get("details")),
                install_options=install_options,
            )
        )

    summary = _merge_summaries(scanners)
    recommended_next_action = (
        "Run the same command without --dry-run to execute the selected local scanner."
        if dry_run and selected_tool_id is not None
        else _missing_checks_next_action(checks)
    )
    if summary["total"] > 0 and not dry_run:
        recommended_next_action = "Review the reported findings, fix the highest-severity issues, then rerun this audit."

    missing_required_scanner = category != "llm" and selected_tool_id is None
    status = _category_status(scanners)
    ok = (
        summary["total"] == 0
        and not missing_required_scanner
        and all(scanner.get("status") != "error" for scanner in scanners)
    )
    return {
        "category": category,
        "root": str(root_path),
        "ok": ok,
        "status": status,
        "summary": summary,
        "checks": checks,
        "scanners": scanners,
        "recommended_next_action": recommended_next_action,
        "verification_commands": [f"tldr audit {category}"],
    }


def render_audit_report(report: dict[str, object]) -> str:
    """Render a concise, human-facing audit report."""

    lines = [f"Audit: {report['category']} ({report['root']})"]

    summary = report.get("summary", {})
    lines.append(
        "Summary: "
        f"{summary.get('critical', 0)} critical, "
        f"{summary.get('high', 0)} high, "
        f"{summary.get('medium', 0)} medium, "
        f"{summary.get('low', 0)} low, "
        f"{summary.get('unknown', 0)} unknown "
        f"({summary.get('total', 0)} total)"
    )

    if report.get("category") == "all":
        for category_result in report.get("categories", []):
            category_summary = category_result.get("summary", {})
            label = {
                "ok": "OK",
                "warn": "WARN",
                "skip": "SKIP",
                "error": "ERROR",
            }.get(str(category_result.get("status", "warn")), "WARN")
            if category_summary.get("total", 0):
                detail = f"{category_summary.get('total', 0)} findings"
            else:
                detail = next(
                    (
                        str(scanner.get("details"))
                        for scanner in category_result.get("scanners", [])
                        if scanner.get("status") in {"warn", "error", "skip"}
                    ),
                    "No findings reported.",
                )
            lines.append(f"{label}: {category_result['category']} - {detail}")
    else:
        for scanner in report.get("scanners", []):
            label = {
                "ok": "OK",
                "warn": "WARN",
                "skip": "SKIP",
                "error": "ERROR",
            }.get(str(scanner.get("status")), str(scanner.get("status")).upper())
            lines.append(f"{label}: {scanner['name']} - {scanner['details']}")
            if scanner.get("command_display"):
                lines.append(f"  Command: {scanner['command_display']}")
            if scanner.get("install_options"):
                lines.append(f"  Tip: {scanner['install_options'][0]['command']}")

    if report.get("recommended_next_action"):
        lines.append(f"Next: {report['recommended_next_action']}")

    return "\n".join(lines)
