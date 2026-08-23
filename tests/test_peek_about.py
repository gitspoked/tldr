"""Tests for the peek `about` field and _extract_purpose (model-free repo purpose).

`about` answers "what is this repo about" without a model: manifest description
first, then a readme-like doc's first paragraph, skipping the "how to work here"
boilerplate that opens agent-instruction files.
"""

import json

from tldreadme import deps
from tldreadme.peek import _extract_purpose, peek_target


# --------------------------------------------------------------------------- #
# _extract_purpose priority
# --------------------------------------------------------------------------- #

def test_purpose_prefers_manifest_description():
    project = {"name": "demo", "description": "A curated one-liner"}
    docs = [{"kind": "readme", "summary": "README first paragraph"}]
    assert _extract_purpose(project, docs) == "A curated one-liner"


def test_purpose_falls_back_to_readme():
    docs = [
        {"kind": "claude", "summary": "This file provides guidance to Claude Code"},
        {"kind": "readme", "summary": "What this project actually is"},
    ]
    assert _extract_purpose({"name": "demo"}, docs) == "What this project actually is"


def test_purpose_skips_agent_boilerplate():
    docs = [{"kind": "claude", "summary": "This file provides guidance to Claude Code"}]
    assert _extract_purpose(None, docs) == ""


def test_purpose_uses_nonagent_doc_over_agent_doc():
    docs = [
        {"kind": "agents", "summary": "This file provides guidance to agents"},
        {"kind": "architecture", "summary": "Event-driven ingestion pipeline"},
    ]
    assert _extract_purpose(None, docs) == "Event-driven ingestion pipeline"


def test_purpose_empty_when_no_signal():
    assert _extract_purpose(None, []) == ""
    assert _extract_purpose({"name": "x", "description": ""}, []) == ""


# --------------------------------------------------------------------------- #
# deps.py description extraction
# --------------------------------------------------------------------------- #

def test_deps_pyproject_description(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.0.0"\ndescription = "Widget toolkit"\n'
    )
    projects = deps.extract_deps_from_directory(tmp_path)
    assert projects
    assert projects[0].project_description == "Widget toolkit"


def test_deps_package_json_description(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "version": "1.0.0", "description": "A JS thing"})
    )
    projects = deps.extract_deps_from_directory(tmp_path)
    assert projects
    assert projects[0].project_description == "A JS thing"


def test_deps_description_defaults_empty(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.32.0\n")
    projects = deps.extract_deps_from_directory(tmp_path)
    assert projects
    assert projects[0].project_description == ""


# --------------------------------------------------------------------------- #
# peek integration (cold path: no .tldr, no services)
# --------------------------------------------------------------------------- #

def test_peek_about_from_manifest(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.0.0"\ndescription = "A demo widget library"\n'
    )
    result = peek_target(tmp_path)
    assert result["about"] == "A demo widget library"
    assert (result["project"] or {}).get("description") == "A demo widget library"


def test_peek_about_readme_when_no_manifest_description(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1.0.0"\n')
    (tmp_path / "README.md").write_text("# Demo\n\nA tiny tool that does one thing well.\n")
    result = peek_target(tmp_path)
    assert result["about"] == "A tiny tool that does one thing well."


def test_peek_about_always_present(tmp_path):
    # A bare directory with no docs and no manifest still returns the key, empty.
    (tmp_path / "x.py").write_text("def f():\n    return 1\n")
    result = peek_target(tmp_path)
    assert "about" in result
    assert result["about"] == ""
