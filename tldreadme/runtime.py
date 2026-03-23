"""Runtime dependency checks for Python packages and external tools."""

from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from shutil import which
from urllib.parse import urlparse
import os
import platform
import socket
import subprocess
import sys

TREE_SITTER_VERSION = "0.21.3"
TREE_SITTER_LANGUAGES_VERSION = "1.10.2"
MIN_PYTHON = (3, 11)
RECOMMENDED_PYTHON = (3, 12)

OPTIONAL_SERVICE_URLS = (
    ("Qdrant", "QDRANT_URL", "http://localhost:6333"),
    ("FalkorDB", "FALKORDB_URL", "redis://localhost:6379"),
    ("Ollama", "OLLAMA_URL", "http://localhost:11434"),
    ("LiteLLM", "LITELLM_URL", ""),
)

OPTIONAL_LSPS = (
    ("Python LSP", ("basedpyright-langserver", "basedpyright", "pyright-langserver", "pyright", "pylsp")),
    ("TypeScript/JavaScript LSP", ("typescript-language-server", "tsserver")),
    ("Rust LSP", ("rust-analyzer",)),
    ("Go LSP", ("gopls",)),
    ("C/C++ LSP", ("clangd",)),
    ("Java LSP", ("jdtls",)),
)

AUDIT_TOOL_SPECS = (
    {
        "id": "osv-scanner",
        "name": "OSV-Scanner",
        "kind": "binary",
        "binary": "osv-scanner",
        "categories": ("deps",),
    },
    {
        "id": "pip-audit",
        "name": "pip-audit",
        "kind": "python",
        "module": "pip_audit",
        "package": "pip-audit",
        "categories": ("deps",),
    },
    {
        "id": "semgrep",
        "name": "Semgrep",
        "kind": "python",
        "module": "semgrep",
        "package": "semgrep",
        "categories": ("code",),
    },
    {
        "id": "bandit",
        "name": "Bandit",
        "kind": "python",
        "module": "bandit",
        "package": "bandit",
        "categories": ("code",),
    },
    {
        "id": "gitleaks",
        "name": "Gitleaks",
        "kind": "binary",
        "binary": "gitleaks",
        "categories": ("secrets",),
    },
    {
        "id": "snyk-oss",
        "name": "Snyk Open Source",
        "kind": "binary",
        "binary": "snyk",
        "categories": ("deps",),
    },
    {
        "id": "snyk-code",
        "name": "Snyk Code",
        "kind": "binary",
        "binary": "snyk",
        "categories": ("code",),
    },
    {
        "id": "garak",
        "name": "Garak",
        "kind": "python",
        "module": "garak",
        "package": "garak",
        "categories": ("llm",),
    },
)


def _check(name: str, status: str, details: str, *, category: str, required: bool = False) -> dict[str, object]:
    """Build a runtime check entry."""

    return {
        "name": name,
        "status": status,
        "ok": status == "ok",
        "details": details,
        "category": category,
        "required": required,
        "install_options": [],
    }


def _version_string(parts: tuple[int, ...]) -> str:
    """Render a tuple version into dotted form."""

    return ".".join(str(part) for part in parts)


def _python_cmd() -> str:
    """Return the recommended Python command for install guidance."""

    return f"python{RECOMMENDED_PYTHON[0]}.{RECOMMENDED_PYTHON[1]}"


def _install_option(label: str, command: str) -> dict[str, str]:
    """Build a single install/start option."""

    return {"label": label, "command": command}


def _dedupe_options(options: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep install options in order without duplicate commands."""

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for option in options:
        key = option["command"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
    return deduped


def _module_check_details(module: str, package: str) -> str | None:
    """Return a versioned module detail string when import metadata is available."""

    if find_spec(module) is None:
        return None

    try:
        package_version = version(package)
    except PackageNotFoundError:
        package_version = "installed"

    return f"{sys.executable} -m {module} ({package_version})"


def install_options_for_check(check: dict[str, object]) -> list[dict[str, str]]:
    """Return install or start commands for a non-OK check."""

    if check["status"] == "ok":
        return []

    brew = which("brew")
    npm = which("npm")
    rustup = which("rustup")
    go = which("go")
    docker = which("docker")
    is_macos = platform.system() == "Darwin"

    name = str(check["name"])
    category = str(check["category"])
    options: list[dict[str, str]] = []

    if name == "python":
        if brew and is_macos:
            options.append(_install_option("Install Python 3.12 with Homebrew", "brew install python@3.12"))
        options.append(_install_option("Create the project virtualenv with Python 3.12", "python3.12 -m venv .venv"))
        return _dedupe_options(options)

    if name == "tree-sitter":
        options.append(
            _install_option(
                "Install the pinned parser runtime in the active environment",
                f"{_python_cmd()} -m pip install tree-sitter=={TREE_SITTER_VERSION} tree-sitter-languages=={TREE_SITTER_LANGUAGES_VERSION}",
            )
        )
        return options

    if name == "ripgrep":
        if brew and is_macos:
            options.append(_install_option("Install ripgrep with Homebrew", "brew install ripgrep"))
        options.append(_install_option("Install ripgrep with apt", "sudo apt install ripgrep"))
        return _dedupe_options(options)

    if name == "Qdrant":
        if docker:
            options.append(_install_option("Start the bundled Qdrant service", "docker compose up -d qdrant"))
        if brew and is_macos:
            options.append(_install_option("Install Docker Desktop", "brew install --cask docker"))
        return _dedupe_options(options)

    if name == "FalkorDB":
        if docker:
            options.append(_install_option("Start the bundled FalkorDB service", "docker compose up -d falkordb"))
        if brew and is_macos:
            options.append(_install_option("Install Docker Desktop", "brew install --cask docker"))
        return _dedupe_options(options)

    if name == "Ollama":
        if brew and is_macos:
            options.append(_install_option("Install Ollama with Homebrew", "brew install ollama"))
        options.append(_install_option("Start the Ollama server", "ollama serve"))
        options.append(_install_option("Pull the default embed model", "ollama pull nomic-embed-text"))
        options.append(_install_option("Pull the default chat model", "ollama pull qwen2.5-coder:3b-instruct"))
        return _dedupe_options(options)

    if name == "LiteLLM":
        options.append(_install_option("Start the bundled LiteLLM stack", "docker compose -f docker-compose.llm.yml up -d"))
        options.append(_install_option("Configure the LiteLLM proxy URL", "set LITELLM_URL=http://localhost:4000 in .env"))
        return options

    if category == "lsp":
        if name == "Python LSP":
            if npm:
                options.append(_install_option("Install basedpyright via npm", "npm install -g basedpyright"))
            options.append(_install_option("Install basedpyright via pip", f"{_python_cmd()} -m pip install basedpyright"))
            options.append(_install_option("Install python-lsp-server via pip", f"{_python_cmd()} -m pip install python-lsp-server"))
            return _dedupe_options(options)

        if name == "TypeScript/JavaScript LSP":
            if npm:
                options.append(
                    _install_option(
                        "Install TypeScript and its language server via npm",
                        "npm install -g typescript typescript-language-server",
                    )
                )
            return _dedupe_options(options)

        if name == "Rust LSP":
            if rustup:
                options.append(_install_option("Install rust-analyzer with rustup", "rustup component add rust-analyzer"))
            if brew and is_macos:
                options.append(_install_option("Install rust-analyzer with Homebrew", "brew install rust-analyzer"))
            return _dedupe_options(options)

        if name == "Go LSP":
            if go:
                options.append(_install_option("Install gopls with Go", "go install golang.org/x/tools/gopls@latest"))
            if brew and is_macos:
                options.append(_install_option("Install gopls with Homebrew", "brew install gopls"))
            return _dedupe_options(options)

        if name == "C/C++ LSP":
            if is_macos:
                options.append(_install_option("Install the Xcode command line tools", "xcode-select --install"))
            if brew and is_macos:
                options.append(_install_option("Install LLVM with Homebrew", "brew install llvm"))
            return _dedupe_options(options)

        if name == "Java LSP":
            if brew and is_macos:
                options.append(_install_option("Install jdtls with Homebrew", "brew install jdtls"))
            return _dedupe_options(options)

    if category == "audit":
        if name == "OSV-Scanner":
            if brew and is_macos:
                options.append(_install_option("Install OSV-Scanner with Homebrew", "brew install osv-scanner"))
            return _dedupe_options(options)

        if name == "pip-audit":
            options.append(_install_option("Install pip-audit in the active environment", f"{_python_cmd()} -m pip install pip-audit"))
            return _dedupe_options(options)

        if name == "Semgrep":
            options.append(_install_option("Install Semgrep in the active environment", f"{_python_cmd()} -m pip install semgrep"))
            return _dedupe_options(options)

        if name == "Bandit":
            options.append(_install_option("Install Bandit in the active environment", f"{_python_cmd()} -m pip install bandit"))
            return _dedupe_options(options)

        if name == "Gitleaks":
            if brew and is_macos:
                options.append(_install_option("Install Gitleaks with Homebrew", "brew install gitleaks"))
            return _dedupe_options(options)

        if name in {"Snyk Open Source", "Snyk Code"}:
            if brew and is_macos:
                options.append(_install_option("Install the Snyk CLI with Homebrew", "brew tap snyk/tap && brew install snyk-cli"))
            if npm:
                options.append(_install_option("Install the Snyk CLI with npm", "npm install -g snyk"))
            options.append(_install_option("Authenticate the Snyk CLI", "snyk auth"))
            return _dedupe_options(options)

        if name == "Garak":
            options.append(_install_option("Install Garak in the active environment", f"{_python_cmd()} -m pip install garak"))
            options.append(_install_option("Create a local Garak config stub", "mkdir -p .tldr && printf '# add garak target config here\\n' > .tldr/garak.yml"))
            return _dedupe_options(options)

    return options


def python_runtime_check() -> dict[str, object]:
    """Validate the current Python interpreter."""

    current = sys.version_info[:3]
    current_str = _version_string(current)

    if current[:2] < MIN_PYTHON:
        return _check(
            "python",
            "error",
            f"{current_str} is unsupported; use Python >= {_version_string(MIN_PYTHON)}.",
            category="runtime",
            required=True,
        )

    details = current_str
    if current[:2] != RECOMMENDED_PYTHON:
        details += f" (supported; {_version_string(RECOMMENDED_PYTHON)} is the recommended default)"

    return _check("python", "ok", details, category="runtime", required=True)


def ensure_tree_sitter_runtime() -> dict[str, str]:
    """Verify tree-sitter runtime dependencies are importable and version-compatible."""

    if find_spec("tree_sitter") is None or find_spec("tree_sitter_languages") is None:
        raise RuntimeError(
            "tree-sitter runtime is missing. Install the pinned pair with "
            "`pip install tree-sitter==0.21.3 tree-sitter-languages==1.10.2`."
        )

    try:
        tree_sitter_version = version("tree-sitter")
        tree_sitter_languages_version = version("tree-sitter-languages")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "tree-sitter runtime metadata is missing. Reinstall with "
            "`pip install tree-sitter==0.21.3 tree-sitter-languages==1.10.2`."
        ) from exc

    if tree_sitter_version != TREE_SITTER_VERSION or tree_sitter_languages_version != TREE_SITTER_LANGUAGES_VERSION:
        raise RuntimeError(
            "Unsupported tree-sitter runtime versions. Expected "
            f"`tree-sitter=={TREE_SITTER_VERSION}` and "
            f"`tree-sitter-languages=={TREE_SITTER_LANGUAGES_VERSION}`, got "
            f"`tree-sitter=={tree_sitter_version}` and "
            f"`tree-sitter-languages=={tree_sitter_languages_version}`."
        )

    return {
        "tree-sitter": tree_sitter_version,
        "tree-sitter-languages": tree_sitter_languages_version,
    }


def get_tree_sitter_languages():
    """Import tree_sitter_languages only after runtime validation."""

    ensure_tree_sitter_runtime()
    import tree_sitter_languages as tsl

    return tsl


def ensure_rg_runtime() -> str:
    """Verify ripgrep is installed and return the executable path."""

    path = which("rg")
    if not path:
        raise RuntimeError(
            "ripgrep (`rg`) is required at runtime. Install it with "
            "`brew install ripgrep` or your platform package manager."
        )
    return path


def get_rg_version() -> str:
    """Return the installed ripgrep version string."""

    rg = ensure_rg_runtime()
    result = subprocess.run([rg, "--version"], capture_output=True, text=True, timeout=5)
    return result.stdout.splitlines()[0].strip() if result.stdout else "unknown"


def _parse_host_port(url: str) -> tuple[str, int] | None:
    """Extract a host/port pair from a URL or host:port string."""

    target = url if "://" in url else f"tcp://{url}"
    parsed = urlparse(target)
    host = parsed.hostname
    port = parsed.port

    if not host:
        return None

    if port is None:
        default_ports = {
            "http": 80,
            "https": 443,
            "redis": 6379,
            "tcp": None,
        }
        port = default_ports.get(parsed.scheme)

    if port is None:
        return None

    return host, port


def _check_socket(url: str, *, timeout: float = 0.5) -> None:
    """Open a short TCP connection to verify a service is reachable."""

    endpoint = _parse_host_port(url)
    if endpoint is None:
        raise RuntimeError(f"could not parse endpoint from {url!r}")

    host, port = endpoint
    with socket.create_connection((host, port), timeout=timeout):
        return


def optional_service_checks() -> list[dict[str, object]]:
    """Check optional service endpoints configured for local-first mode."""

    checks: list[dict[str, object]] = []

    for name, env_var, default in OPTIONAL_SERVICE_URLS:
        url = os.getenv(env_var, default).strip()
        if not url:
            checks.append(
                _check(name, "skip", f"{env_var} is not configured.", category="service")
            )
            continue

        try:
            _check_socket(url)
        except PermissionError as exc:
            checks.append(
                _check(
                    name,
                    "skip",
                    f"{url} could not be probed from this environment ({exc}).",
                    category="service",
                )
            )
            continue
        except OSError as exc:
            checks.append(
                _check(name, "warn", f"{url} is not reachable ({exc}).", category="service")
            )
            continue
        except RuntimeError as exc:
            checks.append(
                _check(name, "warn", f"{url} is not reachable ({exc}).", category="service")
            )
            continue

        checks.append(_check(name, "ok", f"{url} is reachable.", category="service"))

    return checks


def optional_lsp_checks() -> list[dict[str, object]]:
    """Check whether common language servers are available on PATH."""

    checks: list[dict[str, object]] = []

    for name, commands in OPTIONAL_LSPS:
        for command in commands:
            path = which(command)
            if path:
                checks.append(_check(name, "ok", f"{command} at {path}", category="lsp"))
                break
        else:
            checks.append(
                _check(
                    name,
                    "warn",
                    "not found on PATH; checked " + ", ".join(commands),
                    category="lsp",
                )
            )

    return checks


def audit_tool_checks(categories: tuple[str, ...] | None = None) -> list[dict[str, object]]:
    """Check whether supported local audit scanners are available."""

    allowed = set(categories or ())
    checks: list[dict[str, object]] = []

    for spec in AUDIT_TOOL_SPECS:
        spec_categories = tuple(spec["categories"])
        if allowed and not allowed.intersection(spec_categories):
            continue

        if spec["kind"] == "binary":
            path = which(spec["binary"])
            if path:
                check = _check(spec["name"], "ok", f"{spec['binary']} at {path}", category="audit")
            else:
                check = _check(
                    spec["name"],
                    "warn",
                    f"not found on PATH; checked {spec['binary']}",
                    category="audit",
                )
        else:
            details = _module_check_details(spec["module"], spec["package"])
            if details:
                check = _check(spec["name"], "ok", details, category="audit")
            else:
                check = _check(
                    spec["name"],
                    "warn",
                    f"{spec['module']} is not importable from {sys.executable}",
                    category="audit",
                )

        check["tool_id"] = spec["id"]
        check["audit_categories"] = list(spec_categories)
        check["install_options"] = install_options_for_check(check)
        checks.append(check)

    return checks


def runtime_report() -> dict[str, object]:
    """Collect runtime dependency status for CLI diagnostics."""

    report: dict[str, object] = {"ok": True, "checks": []}

    report["checks"].append(python_runtime_check())
    if not report["checks"][-1]["ok"]:
        report["ok"] = False

    try:
        versions = ensure_tree_sitter_runtime()
        report["checks"].append(
            _check(
                "tree-sitter",
                "ok",
                ", ".join(f"{name}=={value}" for name, value in versions.items()),
                category="runtime",
                required=True,
            )
        )
    except RuntimeError as exc:
        report["ok"] = False
        report["checks"].append(
            _check("tree-sitter", "error", str(exc), category="runtime", required=True)
        )

    try:
        report["checks"].append(
            _check("ripgrep", "ok", get_rg_version(), category="runtime", required=True)
        )
    except RuntimeError as exc:
        report["ok"] = False
        report["checks"].append(
            _check("ripgrep", "error", str(exc), category="runtime", required=True)
        )

    report["checks"].extend(optional_service_checks())
    report["checks"].extend(optional_lsp_checks())
    for check in report["checks"]:
        check["install_options"] = install_options_for_check(check)

    return report


def capability_report(report: dict[str, object] | None = None) -> dict[str, object]:
    """Summarize tool-relevant backend capabilities from the runtime report."""

    report = report or runtime_report()
    checks = {
        str(check.get("name")): check
        for check in report.get("checks", [])
        if isinstance(check, dict) and check.get("name")
    }

    lsp_available = any(
        check.get("category") == "lsp" and check.get("status") == "ok"
        for check in report.get("checks", [])
        if isinstance(check, dict)
    )

    backends = {
        "asts": bool(checks.get("tree-sitter", {}).get("ok")),
        "rg": bool(checks.get("ripgrep", {}).get("ok")),
        "vector": bool(checks.get("Qdrant", {}).get("ok")),
        "graph": bool(checks.get("FalkorDB", {}).get("ok")),
        "llm": bool(checks.get("LiteLLM", {}).get("ok") or checks.get("Ollama", {}).get("ok")),
        "lsp": lsp_available,
        "git": which("git") is not None,
        "filesystem": True,
        "docs": True,
        "summary": True,
        "workboard": True,
        "children": True,
        "tests": True,
        "subprocess": True,
        "hot_index": True,
    }

    return {
        "report_ok": bool(report.get("ok")),
        "backends": backends,
    }
