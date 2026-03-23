"""Tests for MCP resources and prompts."""

import json

from tldreadme import mcp_server

from .bedrock import bedrock_case


def test_read_health_resource(monkeypatch):
    runtime = type("Runtime", (), {"runtime_report": staticmethod(lambda: {"ok": True, "checks": [{"name": "python", "status": "ok"}]})})()
    monkeypatch.setattr(mcp_server, "_runtime", lambda: runtime)

    payload = json.loads(mcp_server._read_resource_text("repo://health"))

    assert payload["ok"] is True
    assert payload["checks"][0]["name"] == "python"


@bedrock_case(
    "mcp.router.profile",
    purpose="Keep the default MCP exposure aligned with the four-tool router contract.",
    use_case="A router introspects repo://tooling and relies on the exposed tool list matching the bedrock surface.",
    similar_use_cases=[
        "tool listing",
        "capability-aware router boot",
        "MCP profile introspection",
    ],
    reliance_percent=99.3,
)
def test_read_tooling_resource_uses_router_profile(monkeypatch):
    capabilities = {"report_ok": True, "backends": {"rg": True, "lsp": True, "vector": False, "graph": False, "llm": False, "git": True, "filesystem": True, "docs": True, "summary": True, "workboard": True, "children": True, "tests": True, "subprocess": True, "hot_index": True, "asts": True}}
    monkeypatch.setattr(mcp_server, "_routing_signals", lambda: {"has_current_plan": False, "has_current_task": False, "has_next_action": False, "has_overlaps": False, "unknown_children": 0})
    payload = json.loads(mcp_server._read_resource_text("repo://tooling", capabilities=capabilities))

    assert payload["active_profile"] == "router"
    assert payload["router_contract_version"] == 1
    assert payload["router_contract_tools"] == ["repo_next_action", "repo_lookup", "change_plan", "verify_change"]
    assert [tool["name"] for tool in payload["exposed_tools"]] == [
        "repo_lookup",
        "repo_next_action",
        "change_plan",
        "verify_change",
    ]
    assert any(tool["name"] == "scan_context" for tool in payload["deferred_tools"])
    assert any(tool["name"] == "read_symbol" for tool in payload["suppressed_tools"])


def test_router_profile_exposes_smaller_tool_set():
    capabilities = {"report_ok": True, "backends": {"rg": True, "lsp": True, "vector": False, "graph": False, "llm": False, "git": True, "filesystem": True, "docs": True, "summary": True, "workboard": True, "children": True, "tests": True, "subprocess": True, "hot_index": True, "asts": True}}
    router_tools = mcp_server._tool_names_for_profile("router", capabilities=capabilities)
    full_tools = mcp_server._tool_names_for_profile("full", capabilities=capabilities)

    assert router_tools == ["repo_next_action", "repo_lookup", "change_plan", "verify_change"]
    assert "repo_next_action" in router_tools
    assert "repo_lookup" in router_tools
    assert "scan_context" not in router_tools
    assert "read_symbol" not in router_tools
    assert len(router_tools) < len(full_tools)


@bedrock_case(
    "mcp.capability.enforcement",
    purpose="Keep hard backend requirements from leaking unsupported tools into the exposed surface.",
    use_case="A router runs with partial capabilities and expects missing-backend tools to be suppressed rather than fail at call time.",
    similar_use_cases=[
        "degraded runtime startup",
        "LSP-missing environments",
        "capability-filtered routing",
    ],
    reliance_percent=98.9,
)
def test_capability_enforcement_suppresses_lsp_tools():
    capabilities = {"report_ok": True, "backends": {"rg": True, "lsp": False, "vector": True, "graph": True, "llm": True, "git": True, "filesystem": True, "docs": True, "summary": True, "workboard": True, "children": True, "tests": True, "subprocess": True, "hot_index": True, "asts": True}}

    router_tools = mcp_server._tool_names_for_profile("router", capabilities=capabilities)
    full_tools = mcp_server._tool_names_for_profile("full", capabilities=capabilities)

    assert router_tools == ["repo_next_action", "repo_lookup", "change_plan", "verify_change"]
    assert "read_semantic" not in full_tools
    assert "scan_context" in full_tools


def test_tooling_payload_prioritizes_repo_next_action_when_overlap_exists(monkeypatch):
    capabilities = {"report_ok": True, "backends": {"rg": True, "lsp": True, "vector": True, "graph": True, "llm": True, "git": True, "filesystem": True, "docs": True, "summary": True, "workboard": True, "children": True, "tests": True, "subprocess": True, "hot_index": True, "asts": True}}
    monkeypatch.setattr(mcp_server, "_routing_signals", lambda: {"has_current_plan": True, "has_current_task": True, "has_next_action": True, "has_overlaps": True, "unknown_children": 0})

    payload = mcp_server._tooling_payload("router", capabilities=capabilities)

    assert payload["recommended_sequence"][0] == "repo_next_action"
    assert payload["recommended_sequence"][1] == "repo_lookup"


def test_read_module_resource(monkeypatch):
    rag = type("Rag", (), {"read_module": staticmethod(lambda path: {"module": path, "symbol_count": 2})})()
    monkeypatch.setattr(mcp_server, "_rag", lambda: rag)

    payload = json.loads(mcp_server._read_resource_text("repo://module/src/core"))

    assert payload["module"] == "src/core"
    assert payload["symbol_count"] == 2


def test_read_plans_resource(monkeypatch):
    workboard = type("Workboard", (), {"list_plans": staticmethod(lambda: {"count": 1, "plans": [{"id": "plan-1", "title": "Example"}]})})()
    monkeypatch.setattr(mcp_server, "_workboard", lambda: workboard)

    payload = json.loads(mcp_server._read_resource_text("repo://plans"))

    assert payload["count"] == 1
    assert payload["plans"][0]["id"] == "plan-1"


def test_read_current_session_resource(monkeypatch):
    workboard = type(
        "Workboard",
        (),
        {
            "current_plan": staticmethod(
                lambda: {
                    "session": {"session_id": "cli-123", "current_plan_id": "plan-1"},
                    "plan": {"id": "plan-1"},
                    "summary": {"id": "plan-1"},
                    "active_sessions": [{"session_id": "codex-456", "relation": "same_workspace"}],
                    "overlaps": [{"session_id": "codex-456", "shared_files": ["tldreadme/parser.py"]}],
                }
            )
        },
    )()
    monkeypatch.setattr(mcp_server, "_workboard", lambda: workboard)

    payload = json.loads(mcp_server._read_resource_text("repo://session/current"))

    assert payload["session"]["session_id"] == "cli-123"
    assert payload["active_sessions"][0]["session_id"] == "codex-456"


def test_read_children_resource(monkeypatch):
    children = type("Children", (), {"list_children": staticmethod(lambda include_ignored=True: {"count": 1, "children": [{"path": "redocoder", "status": "unknown"}]})})()
    monkeypatch.setattr(mcp_server, "_children", lambda: children)

    payload = json.loads(mcp_server._read_resource_text("repo://children"))

    assert payload["count"] == 1
    assert payload["children"][0]["path"] == "redocoder"


def test_read_task_resource(monkeypatch):
    workboard = type(
        "Workboard",
        (),
        {"get_task": staticmethod(lambda plan_id, task_id: {"plan_id": plan_id, "id": task_id, "title": "Write tests"})},
    )()
    monkeypatch.setattr(mcp_server, "_workboard", lambda: workboard)

    payload = json.loads(mcp_server._read_resource_text("repo://task/plan-1/task-1"))

    assert payload["plan_id"] == "plan-1"
    assert payload["id"] == "task-1"


def test_build_impact_prompt(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "_read_resource_text",
        lambda uri, tool_profile="router": '{"symbol":"sample","dependents":[]}',
    )

    prompt = mcp_server._build_prompt("impact-review", {"symbol": "sample"})

    assert prompt.description
    assert len(prompt.messages) == 2
    assert prompt.messages[1].content.resource.text == '{"symbol":"sample","dependents":[]}'


def test_list_resource_templates_contains_semantic():
    templates = mcp_server._list_resource_templates()

    assert any(template.name == "semantic" for template in templates)
    assert any(template.name == "plan" for template in templates)


def test_list_prompts_contains_semantic_investigation():
    prompts = mcp_server._list_prompt_definitions()

    assert any(prompt.name == "semantic-investigation" for prompt in prompts)
    assert any(prompt.name == "resume-session" for prompt in prompts)


def test_build_resume_session_prompt(monkeypatch):
    workboard = type(
        "Workboard",
        (),
        {
            "current_plan": staticmethod(
                lambda: {
                    "session": {"current_plan_id": "plan-1", "notes": []},
                    "plan": {"id": "plan-1"},
                    "summary": {"id": "plan-1"},
                }
            )
        },
    )()
    monkeypatch.setattr(mcp_server, "_workboard", lambda: workboard)
    monkeypatch.setattr(
        mcp_server,
        "_read_resource_text",
        lambda uri, tool_profile="router": '{"id":"plan-1"}' if uri.startswith("repo://plan/") else '{"session":true}',
    )

    prompt = mcp_server._build_prompt("resume-session", {})

    assert prompt.description
    assert len(prompt.messages) >= 2
    assert "Resume this repository session" in prompt.messages[0].content.text


def test_build_done_check_prompt(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "_read_resource_text",
        lambda uri, tool_profile="router": '{"id":"task-1","acceptance_criteria":["done"]}',
    )

    prompt = mcp_server._build_prompt("done-check", {"plan_id": "plan-1", "task_id": "task-1"})

    assert prompt.description
    assert prompt.messages[1].content.resource.text == '{"id":"task-1","acceptance_criteria":["done"]}'
