"""Dependency manifest extraction."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Dependency:
    """An external dependency declared in a manifest file."""

    name: str
    version: str
    source_file: str
    kind: str
    features: list[str] = field(default_factory=list)
    registry: str = ""


@dataclass
class ProjectDeps:
    """All dependencies for a project, extracted from manifest files."""

    manifest_file: str
    project_name: str
    project_version: str
    dependencies: list[Dependency]


def extract_deps_from_directory(root: Path) -> list[ProjectDeps]:
    """Find supported manifest files and extract dependencies."""

    all_deps: list[ProjectDeps] = []

    for cargo in root.rglob("Cargo.toml"):
        if any(skip in cargo.parts for skip in ("target", ".git", "node_modules", "retired")):
            continue
        result = _parse_cargo_toml(cargo)
        if result:
            all_deps.append(result)

    for pkg in root.rglob("package.json"):
        if any(skip in pkg.parts for skip in ("node_modules", ".git", "target", "dist")):
            continue
        result = _parse_package_json(pkg)
        if result:
            all_deps.append(result)

    for gomod in root.rglob("go.mod"):
        if any(skip in gomod.parts for skip in (".git", "vendor")):
            continue
        result = _parse_go_mod(gomod)
        if result:
            all_deps.append(result)

    for pyproj in root.rglob("pyproject.toml"):
        if any(skip in pyproj.parts for skip in (".git", ".venv", "venv")):
            continue
        result = _parse_pyproject_toml(pyproj)
        if result:
            all_deps.append(result)

    for reqs in root.rglob("requirements.txt"):
        if any(skip in reqs.parts for skip in (".git", ".venv", "venv")):
            continue
        result = _parse_requirements_txt(reqs)
        if result:
            all_deps.append(result)

    return all_deps


def _parse_cargo_toml(path: Path) -> Optional[ProjectDeps]:
    """Extract deps from Cargo.toml."""

    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return None

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return None

    pkg = data.get("package", {})
    project_name = pkg.get("name", path.parent.name)
    project_version = pkg.get("version", "0.0.0")
    deps: list[Dependency] = []

    for name, spec in data.get("dependencies", {}).items():
        version, features, optional = _parse_cargo_dep_spec(spec)
        deps.append(
            Dependency(
                name=name,
                version=version,
                source_file=str(path),
                kind="optional" if optional else "runtime",
                features=features,
                registry="crates.io",
            )
        )

    for name, spec in data.get("dev-dependencies", {}).items():
        version, features, _ = _parse_cargo_dep_spec(spec)
        deps.append(
            Dependency(
                name=name,
                version=version,
                source_file=str(path),
                kind="dev",
                features=features,
                registry="crates.io",
            )
        )

    for name, spec in data.get("build-dependencies", {}).items():
        version, features, _ = _parse_cargo_dep_spec(spec)
        deps.append(
            Dependency(
                name=name,
                version=version,
                source_file=str(path),
                kind="build",
                features=features,
                registry="crates.io",
            )
        )

    return ProjectDeps(
        manifest_file=str(path),
        project_name=project_name,
        project_version=project_version,
        dependencies=deps,
    )


def _parse_cargo_dep_spec(spec) -> tuple:
    """Parse a Cargo dependency spec (string or table)."""

    if isinstance(spec, str):
        return spec, [], False
    if isinstance(spec, dict):
        version = spec.get("version", spec.get("path", "path"))
        features = spec.get("features", [])
        optional = spec.get("optional", False)
        return version, features, optional
    return "unknown", [], False


def _parse_package_json(path: Path) -> Optional[ProjectDeps]:
    """Extract deps from package.json."""

    import json

    try:
        data = json.loads(path.read_text())
    except Exception:
        return None

    project_name = data.get("name", path.parent.name)
    project_version = data.get("version", "0.0.0")
    deps: list[Dependency] = []

    for name, version in data.get("dependencies", {}).items():
        deps.append(
            Dependency(
                name=name,
                version=version,
                source_file=str(path),
                kind="runtime",
                registry="npm",
            )
        )

    for name, version in data.get("devDependencies", {}).items():
        deps.append(
            Dependency(
                name=name,
                version=version,
                source_file=str(path),
                kind="dev",
                registry="npm",
            )
        )

    for name, version in data.get("peerDependencies", {}).items():
        deps.append(
            Dependency(
                name=name,
                version=version,
                source_file=str(path),
                kind="peer",
                registry="npm",
            )
        )

    return ProjectDeps(
        manifest_file=str(path),
        project_name=project_name,
        project_version=project_version,
        dependencies=deps,
    )


def _parse_go_mod(path: Path) -> Optional[ProjectDeps]:
    """Extract deps from go.mod."""

    try:
        text = path.read_text()
    except Exception:
        return None

    project_name = ""
    deps: list[Dependency] = []
    in_require = False

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("module "):
            project_name = line.split("module ", 1)[1].strip()
        elif line == "require (":
            in_require = True
        elif line == ")" and in_require:
            in_require = False
        elif in_require and line and not line.startswith("//"):
            parts = line.split()
            if len(parts) >= 2:
                name, version = parts[0], parts[1]
                indirect = "// indirect" in line
                deps.append(
                    Dependency(
                        name=name,
                        version=version,
                        source_file=str(path),
                        kind="indirect" if indirect else "runtime",
                        registry="go",
                    )
                )
        elif line.startswith("require ") and "(" not in line:
            parts = line.split()
            if len(parts) >= 3:
                deps.append(
                    Dependency(
                        name=parts[1],
                        version=parts[2],
                        source_file=str(path),
                        kind="runtime",
                        registry="go",
                    )
                )

    return ProjectDeps(
        manifest_file=str(path),
        project_name=project_name,
        project_version="",
        dependencies=deps,
    )


def _parse_pyproject_toml(path: Path) -> Optional[ProjectDeps]:
    """Extract deps from pyproject.toml."""

    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return None

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return None

    project = data.get("project", {})
    project_name = project.get("name", path.parent.name)
    project_version = project.get("version", "0.0.0")
    deps: list[Dependency] = []

    for dep_str in project.get("dependencies", []):
        name, version = _parse_pep508(dep_str)
        deps.append(
            Dependency(
                name=name,
                version=version,
                source_file=str(path),
                kind="runtime",
                registry="pypi",
            )
        )

    for group_name, group_deps in project.get("optional-dependencies", {}).items():
        for dep_str in group_deps:
            name, version = _parse_pep508(dep_str)
            deps.append(
                Dependency(
                    name=name,
                    version=version,
                    source_file=str(path),
                    kind=f"optional-{group_name}",
                    registry="pypi",
                )
            )

    return ProjectDeps(
        manifest_file=str(path),
        project_name=project_name,
        project_version=project_version,
        dependencies=deps,
    )


def _parse_requirements_txt(path: Path) -> Optional[ProjectDeps]:
    """Extract deps from requirements.txt."""

    try:
        text = path.read_text()
    except Exception:
        return None

    deps: list[Dependency] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name, version = _parse_pep508(line)
        deps.append(
            Dependency(
                name=name,
                version=version,
                source_file=str(path),
                kind="runtime",
                registry="pypi",
            )
        )

    return ProjectDeps(
        manifest_file=str(path),
        project_name=path.parent.name,
        project_version="",
        dependencies=deps,
    )


def _parse_pep508(dep_str: str) -> tuple:
    """Parse a dependency string into (name, version)."""

    import re

    match = re.match(r"^([A-Za-z0-9_.-]+)\s*(.*)", dep_str)
    if match:
        return match.group(1), match.group(2).strip() or "*"
    return dep_str, "*"
