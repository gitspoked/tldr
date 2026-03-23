"""Tests for nested child-project detection and acknowledgments."""

from tldreadme import children
import yaml


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
    registry_path = tmp_path / ".tldr" / "work" / "children.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))

    assert result["count"] == 1
    assert result["children"][0]["path"] == "redocoder"
    assert result["children"][0]["status"] == "unknown"
    assert result["children"][0]["manifests"] == ["package.json"]
    assert registry["schema_version"] == children.SCHEMA_VERSION
    assert registry["document_type"] == children.CHILDREN_DOCUMENT_TYPE


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


def test_list_children_upgrades_legacy_registry_metadata(tmp_path):
    work_root = tmp_path / ".tldr" / "work"
    work_root.mkdir(parents=True)
    registry_path = work_root / "children.yaml"
    registry_path.write_text(
        yaml.safe_dump(
            {
                "children": [
                    {
                        "path": "redocoder",
                        "status": "merged",
                        "detected_at": "2026-03-23T00:00:00+00:00",
                        "updated_at": "2026-03-23T00:00:00+00:00",
                        "manifests": ["package.json"],
                        "context_docs": ["README.md"],
                        "has_git": False,
                        "code_file_count": 12,
                        "note": "Imported subtree",
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    listing = children.list_children(root=tmp_path, refresh=False, include_ignored=True)
    rewritten = yaml.safe_load(registry_path.read_text(encoding="utf-8"))

    assert listing["count"] == 1
    assert rewritten["schema_version"] == children.SCHEMA_VERSION
    assert rewritten["document_type"] == children.CHILDREN_DOCUMENT_TYPE
