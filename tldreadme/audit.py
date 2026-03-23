"""Local-first audit wrappers for dependency, code, secrets, and LLM checks."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
import shlex
import subprocess
import sys
import time

from .runtime import audit_tool_checks

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info", "unknown")
CATEGORY_SCANNERS = {
    "deps": ("osv-scanner", "pip-audit", "snyk-oss"),
    "code": ("semgrep", "bandit", "snyk-code"),
    "secrets": ("gitleaks",),
    "llm": ("garak",),
}
DEFAULT_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
DEFAULT_KEV_PATH = ".tldr/security/known_exploited_vulnerabilities.json"
SECURITY_ROOT = ".tldr/security"
REPORTS_DIR = "reports"
POLICY_PROFILES = {
    "owasp-web": {
        "name": "OWASP Top 10",
        "description": "General web application security focus.",
        "recommended_categories": ("deps", "code", "secrets"),
        "focus_areas": (
            "injection and unsafe deserialization",
            "broken access control and authz gaps",
            "security logging, SSRF, and insecure defaults",
        ),
    },
    "owasp-api": {
        "name": "OWASP API Security Top 10",
        "description": "API-specific trust boundary and authorization focus.",
        "recommended_categories": ("deps", "code", "secrets"),
        "focus_areas": (
            "broken object and function level authorization",
            "mass assignment and excessive data exposure",
            "rate limiting, auth, and inventory gaps",
        ),
    },
    "owasp-llm": {
        "name": "OWASP LLM Top 10",
        "description": "Prompt injection and model misuse focus.",
        "recommended_categories": ("code", "llm", "secrets"),
        "focus_areas": (
            "prompt injection and indirect prompt control",
            "sensitive information disclosure",
            "unsafe tool use, output handling, and model abuse",
        ),
    },
    "owasp-mcp": {
        "name": "OWASP MCP Top 10",
        "description": "MCP server and agent tool-surface focus.",
        "recommended_categories": ("code", "secrets", "llm"),
        "focus_areas": (
            "tool abuse and overbroad capability exposure",
            "context poisoning and unsafe prompt/tool chaining",
            "credential leakage and server boundary mistakes",
        ),
    },
}
TOOL_DISPLAY_NAMES = {
    "osv-scanner": "OSV-Scanner",
    "pip-audit": "pip-audit",
    "semgrep": "Semgrep",
    "bandit": "Bandit",
    "gitleaks": "Gitleaks",
    "snyk-oss": "Snyk Open Source",
    "snyk-code": "Snyk Code",
    "garak": "Garak",
}


def _empty_summary() -> dict[str, int]:
    """Build an empty severity summary."""

    summary = {level: 0 for level in SEVERITY_ORDER}
    summary["kev"] = 0
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
        if finding.get("known_exploited"):
            summary["kev"] += 1
        summary["total"] += 1
    return summary


def _load_kev_catalog(path: str | None) -> dict[str, dict[str, object]]:
    """Load a local CISA KEV catalog and index it by CVE identifier."""

    if not path:
        return {}

    payload = _parse_json(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}

    indexed: dict[str, dict[str, object]] = {}
    for item in payload.get("vulnerabilities", []):
        cve = str(item.get("cveID") or "").strip().upper()
        if cve:
            indexed[cve] = item
    return indexed


def _apply_kev(findings: list[dict[str, object]], kev_catalog: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    """Annotate findings with KEV metadata when a CVE is known exploited."""

    if not kev_catalog:
        return findings

    annotated: list[dict[str, object]] = []
    for finding in findings:
        aliases = [str(alias).upper() for alias in finding.get("aliases", [])]
        candidates = [str(finding.get("id") or "").upper(), *aliases]
        match = next((kev_catalog[candidate] for candidate in candidates if candidate in kev_catalog), None)
        if not match:
            annotated.append(finding)
            continue

        enriched = dict(finding)
        enriched["known_exploited"] = True
        enriched["kev_due_date"] = match.get("dueDate")
        enriched["kev_vendor_project"] = match.get("vendorProject")
        enriched["kev_product"] = match.get("product")
        annotated.append(enriched)

    return annotated


def _profile_metadata(profile: str | None) -> dict[str, object] | None:
    """Return the normalized policy profile metadata."""

    if not profile:
        return None

    data = POLICY_PROFILES.get(profile)
    if not data:
        raise ValueError(f"Unsupported audit policy profile: {profile}")

    return {
        "id": profile,
        "name": data["name"],
        "description": data["description"],
        "recommended_categories": list(data["recommended_categories"]),
        "focus_areas": list(data["focus_areas"]),
    }


def _check_payload(tool_id: str, checks_by_id: dict[str, dict[str, object]]) -> dict[str, object]:
    """Return an existing audit check or a safe placeholder."""

    check = checks_by_id.get(tool_id)
    if check is not None:
        return check

    return {
        "name": TOOL_DISPLAY_NAMES.get(tool_id, tool_id),
        "tool_id": tool_id,
        "status": "warn",
        "details": f"{TOOL_DISPLAY_NAMES.get(tool_id, tool_id)} availability was not reported.",
        "install_options": [],
    }


def _category_order(category: str, *, prefer_snyk: bool) -> tuple[str, ...]:
    """Return the scanner preference order for a category."""

    scanners = list(CATEGORY_SCANNERS[category])
    if prefer_snyk:
        snyk_tool = "snyk-oss" if category == "deps" else "snyk-code" if category == "code" else None
        if snyk_tool and snyk_tool in scanners:
            scanners.remove(snyk_tool)
            scanners.insert(0, snyk_tool)
    return tuple(scanners)


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
    return _run_osv_with_options(root, dry_run=dry_run, install_options=install_options)


def _run_osv_with_options(
    root: Path,
    *,
    dry_run: bool,
    install_options: list[dict[str, str]],
    offline: bool = False,
    download_offline_db: bool = False,
) -> dict[str, object]:
    command = ["osv-scanner", "scan", "--format", "json"]
    if offline:
        command.append("--offline")
    if download_offline_db:
        command.append("--download-offline-databases")
    command.append(str(root))
    return _run_json_command(
        "OSV-Scanner",
        command,
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


def _parse_snyk_oss(payload: object) -> list[dict[str, object]]:
    """Normalize Snyk Open Source JSON results."""

    findings: list[dict[str, object]] = []
    if not isinstance(payload, dict):
        return findings

    for vulnerability in payload.get("vulnerabilities", []):
        findings.append(
            {
                "id": vulnerability.get("id") or vulnerability.get("identifiers", {}).get("CVE", [None])[0],
                "title": vulnerability.get("title") or vulnerability.get("id") or "Snyk vulnerability",
                "severity": vulnerability.get("severity") or "unknown",
                "package": vulnerability.get("packageName"),
                "version": vulnerability.get("version"),
                "path": " > ".join(vulnerability.get("from", []) or []),
                "aliases": vulnerability.get("identifiers", {}).get("CVE", []),
            }
        )
    return findings


def _parse_snyk_code(payload: object) -> list[dict[str, object]]:
    """Normalize Snyk Code JSON results."""

    findings: list[dict[str, object]] = []
    if not isinstance(payload, dict):
        return findings

    for run in payload.get("runs", []):
        results = run.get("results", [])
        rules = {rule.get("id"): rule for rule in run.get("tool", {}).get("driver", {}).get("rules", [])}
        for result in results:
            rule = rules.get(result.get("ruleId"), {})
            locations = result.get("locations", [])
            path = None
            line = None
            if locations:
                physical = locations[0].get("physicalLocation", {})
                artifact = physical.get("artifactLocation", {})
                region = physical.get("region", {})
                path = artifact.get("uri")
                line = region.get("startLine")
            findings.append(
                {
                    "id": result.get("ruleId"),
                    "title": rule.get("name") or result.get("message", {}).get("text") or result.get("ruleId") or "Snyk Code finding",
                    "severity": (rule.get("properties", {}) or {}).get("severity") or "unknown",
                    "path": path,
                    "line": line,
                }
            )
    return findings


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


def _run_snyk_oss(root: Path, *, dry_run: bool, install_options: list[dict[str, str]]) -> dict[str, object]:
    return _run_json_command(
        "Snyk Open Source",
        ["snyk", "test", "--json", str(root)],
        _parse_snyk_oss,
        dry_run=dry_run,
        install_options=install_options,
    )


def _run_snyk_code(root: Path, *, dry_run: bool, install_options: list[dict[str, str]]) -> dict[str, object]:
    return _run_json_command(
        "Snyk Code",
        ["snyk", "code", "test", "--json", str(root)],
        _parse_snyk_code,
        dry_run=dry_run,
        install_options=install_options,
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
    "snyk-oss": _run_snyk_oss,
    "snyk-code": _run_snyk_code,
    "garak": _run_garak,
}


def list_policy_profiles() -> list[dict[str, object]]:
    """Return all supported audit policy profiles."""

    return [_profile_metadata(profile_id) for profile_id in POLICY_PROFILES]


def render_policy_profiles() -> str:
    """Render the supported audit policy profiles."""

    lines = ["Audit Profiles:"]
    for profile in list_policy_profiles():
        lines.append(f"{profile['id']}: {profile['description']}")
        lines.append(f"  Categories: {', '.join(profile['recommended_categories'])}")
        lines.append(f"  Focus: {', '.join(profile['focus_areas'])}")
    return "\n".join(lines)


def refresh_kev_catalog(
    *,
    output_path: str = DEFAULT_KEV_PATH,
    url: str = DEFAULT_KEV_URL,
) -> dict[str, object]:
    """Download and cache the CISA Known Exploited Vulnerabilities catalog."""

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=30) as response:  # nosec B310 - explicit official feed URL/config
        payload = response.read().decode("utf-8")

    parsed = _parse_json(payload)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("vulnerabilities"), list):
        raise RuntimeError(f"Downloaded KEV payload from {url} was not valid JSON.")

    target.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    return {
        "url": url,
        "path": str(target),
        "count": len(parsed.get("vulnerabilities", [])),
    }


def _security_root(root: str | Path = ".") -> Path:
    """Return the repository-local security workspace."""

    return Path(root).resolve() / SECURITY_ROOT


def _reports_dir(root: str | Path = ".") -> Path:
    """Return the report storage directory."""

    return _security_root(root) / REPORTS_DIR


def save_audit_report(report: dict[str, object], *, root: str = ".", label: str | None = None) -> dict[str, object]:
    """Persist an audit report under .tldr/security/reports and update the latest snapshot."""

    reports_dir = _reports_dir(root)
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    category = str(report.get("category", "audit"))
    slug = f"-{label}" if label else ""
    report_path = reports_dir / f"{category}{slug}-{timestamp}.json"
    latest_path = _security_root(root) / "latest-audit.json"
    payload = dict(report)
    payload["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {
        "path": str(report_path),
        "latest_path": str(latest_path),
    }


def read_security_state(*, root: str = ".") -> dict[str, object]:
    """Return the local security workspace state and latest saved audit."""

    security_root = _security_root(root)
    latest_path = security_root / "latest-audit.json"
    kev_path = security_root / Path(DEFAULT_KEV_PATH).name
    reports = sorted(_reports_dir(root).glob("*.json"), reverse=True) if _reports_dir(root).exists() else []
    latest_report = None
    if latest_path.exists():
        latest_report = _parse_json(latest_path.read_text(encoding="utf-8"))

    return {
        "security_root": str(security_root),
        "kev_catalog_path": str(kev_path) if kev_path.exists() else None,
        "latest_audit_path": str(latest_path) if latest_path.exists() else None,
        "latest_report": latest_report,
        "reports": [str(path) for path in reports[:10]],
        "profiles": list_policy_profiles(),
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
        summary["kev"] += int(result_summary.get("kev", 0))
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
    offline: bool = False,
    download_offline_db: bool = False,
    kev_catalog_path: str | None = None,
    profile: str | None = None,
    prefer_snyk: bool = False,
) -> dict[str, object]:
    """Run one audit category or the full audit suite."""

    root_path = Path(root).resolve()
    kev_catalog = _load_kev_catalog(kev_catalog_path)
    policy_profile = _profile_metadata(profile)
    if category == "all":
        category_results = [
            run_audit(
                name,
                root=str(root_path),
                dry_run=dry_run,
                garak_config=garak_config,
                offline=offline,
                download_offline_db=download_offline_db,
                kev_catalog_path=kev_catalog_path,
                profile=profile,
                prefer_snyk=prefer_snyk,
            )
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
            "policy_profile": policy_profile,
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
    scanner_order = _category_order(category, prefer_snyk=prefer_snyk)
    if category != "llm" or garak_config:
        for tool_id in scanner_order:
            check = checks_by_id.get(tool_id)
            if check and check.get("status") == "ok":
                selected_tool_id = tool_id
                break

    scanners: list[dict[str, object]] = []
    for tool_id in scanner_order:
        check = _check_payload(tool_id, checks_by_id)
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
            elif tool_id == "osv-scanner":
                scanners.append(
                    _run_osv_with_options(
                        root_path,
                        dry_run=dry_run,
                        install_options=install_options,
                        offline=offline,
                        download_offline_db=download_offline_db,
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

    for scanner in scanners:
        scanner["findings"] = _apply_kev(list(scanner.get("findings", [])), kev_catalog)
        scanner["summary"] = _summarize_findings(scanner["findings"])
        if scanner["summary"]["kev"]:
            scanner["details"] = (
                f"{scanner['summary']['total']} findings reported "
                f"({scanner['summary']['kev']} known exploited)."
            )

    summary = _merge_summaries(scanners)
    recommended_next_action = (
        "Run the same command without --dry-run to execute the selected local scanner."
        if dry_run and selected_tool_id is not None
        else _missing_checks_next_action(checks)
    )
    if summary["total"] > 0 and not dry_run:
        recommended_next_action = "Review the reported findings, fix the highest-severity issues, then rerun this audit."
    if summary["kev"] > 0:
        recommended_next_action = "Prioritize the known exploited findings first, then rerun this audit after remediation."
    if policy_profile and summary["total"] == 0 and category not in policy_profile["recommended_categories"]:
        recommended_next_action = (
            f"{policy_profile['name']} emphasizes {', '.join(policy_profile['recommended_categories'])}; "
            f"consider auditing one of those categories next."
        )
    if offline and selected_tool_id != "osv-scanner" and category == "deps":
        recommended_next_action = "Offline dependency mode requires OSV-Scanner; install it and rerun `tldr audit deps --offline`."
    if prefer_snyk and selected_tool_id not in {"snyk-oss", "snyk-code"} and category in {"deps", "code"}:
        recommended_next_action = "Snyk preference is enabled, but the Snyk CLI is unavailable; install and authenticate `snyk`, or rerun without `--prefer-snyk`."

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
        "policy_profile": policy_profile,
        "selected_scanner": selected_tool_id,
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
        f"{summary.get('kev', 0)} known exploited, "
        f"{summary.get('unknown', 0)} unknown "
        f"({summary.get('total', 0)} total)"
    )

    policy_profile = report.get("policy_profile")
    if policy_profile:
        lines.append(f"Profile: {policy_profile['id']} - {policy_profile['description']}")

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
