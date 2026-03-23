"""Pytest helpers for bedrock contract reporting."""

from __future__ import annotations

from collections.abc import Iterable

from .bedrock import BedrockCase


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "bedrock: critical contract and compatibility tests that gate the bedrock layer",
    )
    config._bedrock_cases = {}


def pytest_collection_modifyitems(config, items) -> None:
    cases: dict[str, BedrockCase] = {}
    for item in items:
        case = getattr(getattr(item, "obj", None), "__bedrock_case__", None)
        if case is not None:
            item.add_marker("bedrock")
            cases[item.nodeid] = case
    config._bedrock_cases = cases


def _report_nodeids(reports: Iterable[object], known_cases: dict[str, BedrockCase]) -> set[str]:
    nodeids: set[str] = set()
    for report in reports:
        nodeid = getattr(report, "nodeid", None)
        if isinstance(nodeid, str) and nodeid in known_cases:
            nodeids.add(nodeid)
    return nodeids


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    cases: dict[str, BedrockCase] = getattr(config, "_bedrock_cases", {})
    if not cases:
        return

    stats = terminalreporter.stats
    passed = _report_nodeids(stats.get("passed", []), cases)
    failed = _report_nodeids(stats.get("failed", []), cases) | _report_nodeids(stats.get("error", []), cases)
    skipped = _report_nodeids(stats.get("skipped", []), cases) | _report_nodeids(stats.get("xfailed", []), cases)
    executed = passed | failed | skipped
    missing = set(cases) - executed

    total_reliance = sum(case.reliance_percent for case in cases.values()) or 1.0
    passed_reliance = sum(cases[nodeid].reliance_percent for nodeid in passed)
    coverage = passed_reliance / total_reliance * 100
    gate_status = "GO" if not failed and not skipped and not missing else "NO-GO"

    terminalreporter.section("Bedrock Gate", sep="=")
    terminalreporter.line(
        f"{gate_status}: {len(passed)}/{len(cases)} critical contract cases passed "
        f"({coverage:.1f}% weighted reliance coverage)."
    )

    for nodeid, case in sorted(cases.items(), key=lambda item: (-item[1].reliance_percent, item[1].case_id)):
        if nodeid in passed:
            status = "PASS"
        elif nodeid in failed:
            status = "FAIL"
        elif nodeid in skipped:
            status = "SKIP"
        else:
            status = "MISS"
        terminalreporter.line(f"{status} [{case.reliance_percent:.1f}%] {case.case_id}: {case.purpose}")
        terminalreporter.line(f"  use case: {case.use_case}")
        terminalreporter.line(f"  similar: {', '.join(case.similar_use_cases)}")
