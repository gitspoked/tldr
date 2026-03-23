"""Tests for nested child-project detection and acknowledgments."""

from tldreadme import children


def test_list_children_detects_shallow_nested_project(tmp_path):
    child = tmp_path / "redocoder"
    child.mkdir()
    (child / "package.json").write_text('{"name":"redocoder"}\n')
    (child / "README.md").write_text("# Redocoder\n")
    src = child / "src"
    src.mkdir()
    (src / "index.ts").write_text("export const value = 1;\n")

    nested = child / "apps" / "web"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text('{"name":"web"}\n')
    (nested / "index.ts").write_text("export const web = true;\n")

    result = children.list_children(root=tmp_path)

    assert result["count"] == 1
    assert result["children"][0]["path"] == "redocoder"
    assert result["children"][0]["status"] == "unknown"
    assert result["children"][0]["manifests"] == ["package.json"]


def test_merge_and_ignore_child_persist_registry(tmp_path):
    child = tmp_path / "redocoder"
    child.mkdir()
    (child / "pyproject.toml").write_text("[project]\nname='redocoder'\n")
    (child / "main.py").write_text("def main():\n    return 1\n")

    merged = children.merge_child("redocoder", root=tmp_path, note="Imported intentionally")
    ignored = children.ignore_child("redocoder", root=tmp_path)
    listing = children.list_children(root=tmp_path, include_ignored=True)

    assert merged["status"] == "merged"
    assert merged["note"] == "Imported intentionally"
    assert ignored["status"] == "ignored"
    assert listing["ignored_count"] == 1
    assert (tmp_path / ".tldr" / "work" / "children.yaml").exists()
