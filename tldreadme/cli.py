"""CLI entry point - tldr init|watch|serve|ask"""

import json
import os
import shlex
import sys
from pathlib import Path

import click

from .config import get_setting

AUDIT_PROFILE_CHOICES = ["owasp-web", "owasp-api", "owasp-llm", "owasp-mcp"]


class _PathFallbackGroup(click.Group):
    """click.Group that falls through to `peek` when the first arg is an existing path."""

    def parse_args(self, ctx, args):
        # If the first argument looks like an existing path and is not a known
        # subcommand, rewrite the argv so Click sees "peek <path>" instead.
        if args and os.path.exists(args[0]) and args[0] not in self.commands:
            args = ["peek"] + list(args)
        return super().parse_args(ctx, args)


@click.group(
    cls=_PathFallbackGroup,
    invoke_without_command=True,
)
@click.pass_context
def main(ctx):
    """TLDREADME - TL;DR for any codebase."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
@click.option(
    "--provider",
    type=click.Choice(["ollama", "litellm"], case_sensitive=False),
    help="Model provider. Ollama is local-first; LiteLLM uses a configured proxy.",
)
@click.option("--ollama-url", default=None, help="Direct Ollama endpoint.")
@click.option("--litellm-url", default=None, help="LiteLLM proxy endpoint.")
@click.option("--embed-model", default=None, help="Embedding model or LiteLLM model alias.")
@click.option("--chat-model", default=None, help="Chat model or LiteLLM model alias.")
@click.option("--qdrant-url", default=None, help="Qdrant endpoint.")
@click.option("--falkordb-url", default=None, help="FalkorDB Redis URL.")
@click.option(
    "--tool-profile",
    type=click.Choice(["router", "full"], case_sensitive=False),
    default=None,
    help="Default MCP exposure: four router tools or the full specialist surface.",
)
@click.option(
    "--allow-cloud-non-code",
    is_flag=True,
    help="Allow the selected host subscriptions to expand non-code context.",
)
@click.option(
    "--cloud-subscription",
    "cloud_subscriptions",
    type=click.Choice(["codex", "claude", "gemini"], case_sensitive=False),
    multiple=True,
    help="Host subscription allowed for expanded inference. Repeat to select more than one.",
)
@click.option(
    "--allow-cloud-code",
    is_flag=True,
    help="Allow the selected host subscriptions to expand code-level context.",
)
@click.option(
    "--acknowledge-cloud-warning",
    is_flag=True,
    help="Acknowledge that selected context may leave the local machine.",
)
@click.option(
    "--check", is_flag=True, help="Report whether setup has been completed without writing."
)
@click.option("--json-output", is_flag=True, help="Print the configuration result as JSON.")
def setup(
    provider: str | None,
    ollama_url: str | None,
    litellm_url: str | None,
    embed_model: str | None,
    chat_model: str | None,
    qdrant_url: str | None,
    falkordb_url: str | None,
    tool_profile: str | None,
    allow_cloud_non_code: bool,
    cloud_subscriptions: tuple[str, ...],
    allow_cloud_code: bool,
    acknowledge_cloud_warning: bool,
    check: bool,
    json_output: bool,
):
    """Configure the provider used by CLI and Codex/Claude plugin runtimes."""

    from .config import (
        DEFAULT_SETTINGS,
        PROVIDER_MODEL_DEFAULTS,
        configuration_status,
        write_configuration,
    )

    if check:
        status = configuration_status()
        click.echo(json.dumps(status, indent=2) if json_output else status["reason"])
        if not status["configured"]:
            raise click.ClickException("Must run Configuration - Setup first.")
        return

    interactive = provider is None
    if interactive:
        provider = click.prompt(
            "Model provider",
            type=click.Choice(["ollama", "litellm"], case_sensitive=False),
            default="ollama",
            show_choices=True,
        )

    provider = provider.lower()
    if provider == "litellm" and not litellm_url:
        litellm_url = click.prompt(
            "LiteLLM proxy URL",
            default="http://localhost:4000",
        )

    if interactive:
        tool_profile = click.prompt(
            "MCP tool profile",
            type=click.Choice(["router", "full"], case_sensitive=False),
            default=get_setting("TLDREADME_TOOL_PROFILE", "router"),
            show_choices=True,
        )
        click.echo()
        click.echo("WARNING - SUBSCRIPTION / NON-CODE CONTEXT / EXPANDED INFERENCE")
        click.echo("Codex [ ]  Claude [ ]  Gemini [ ]")
        allow_cloud_non_code = click.confirm(
            "ALLOW CLOUD NON-LOCAL INFERENCE FOR NON-CODE CONTEXT?",
            default=False,
        )
        allow_cloud_code = click.confirm(
            "EXPAND CODE-LEVEL INFERENCE WITH A CLOUD SUBSCRIPTION?",
            default=False,
        )
        if allow_cloud_non_code or allow_cloud_code:
            selected = click.prompt(
                "Subscriptions (comma-separated: codex, claude, gemini)",
                default="",
                show_default=False,
            )
            cloud_subscriptions = tuple(
                item.strip().lower() for item in selected.split(",") if item.strip()
            )
            acknowledge_cloud_warning = click.confirm(
                "I understand selected context may leave this machine",
                default=False,
            )

    tool_profile = tool_profile or get_setting("TLDREADME_TOOL_PROFILE", "router")
    model_defaults = PROVIDER_MODEL_DEFAULTS[provider]
    try:
        result = write_configuration(
            provider=provider,
            ollama_url=ollama_url or DEFAULT_SETTINGS["OLLAMA_URL"],
            litellm_url=litellm_url,
            embed_model=embed_model or model_defaults["TLDREADME_EMBED_MODEL"],
            chat_model=chat_model or model_defaults["TLDREADME_CHAT_MODEL"],
            qdrant_url=qdrant_url or DEFAULT_SETTINGS["QDRANT_URL"],
            falkordb_url=falkordb_url or DEFAULT_SETTINGS["FALKORDB_URL"],
            allow_cloud_non_code=allow_cloud_non_code,
            cloud_subscriptions=cloud_subscriptions,
            allow_cloud_code=allow_cloud_code,
            acknowledge_cloud_warning=acknowledge_cloud_warning,
            tool_profile=tool_profile,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if json_output:
        click.echo(json.dumps(result, indent=2))
        return

    click.echo(f"Configuration complete: {provider}")
    click.echo(f"Saved: {result['path']}")
    click.echo(f"Embedding model: {result['settings']['TLDREADME_EMBED_MODEL']}")
    click.echo(f"Chat model: {result['settings']['TLDREADME_CHAT_MODEL']}")
    click.echo(f"MCP tool profile: {result['settings']['TLDREADME_TOOL_PROFILE']}")
    policy = result["inference_policy"]
    subscriptions = ", ".join(policy.get("cloud_subscriptions", [])) or "none"
    click.echo(
        f"Cloud non-code inference: {'allowed' if policy.get('allow_cloud_non_code') else 'not allowed'} ({subscriptions})"
    )
    click.echo(
        f"Cloud code-level inference: {'allowed' if policy.get('allow_cloud_code') else 'not allowed'}"
    )
    click.echo(
        "Subscription choices are host routing preferences; they do not grant "
        "API access or reuse consumer credentials."
    )
    click.echo("Run `tldr doctor` to verify services and exact model readiness.")
    click.echo("Restart Codex or Claude Code after changing plugin configuration.")


@main.command()
@click.argument("directory", type=click.Path(exists=True))
@click.option("--output", "-o", default=".claude", help="Output dir for generated context files")
def init(directory: str, output: str):
    """Scan a directory, index everything, generate TLDR.md.

    Parses all code via tree-sitter, embeds into Qdrant, builds
    call/import/data-flow graphs in FalkorDB, then generates
    context files that make any LLM immediately understand the codebase.
    """
    from .pipeline import run_init
    from .runtime import ensure_tree_sitter_runtime

    try:
        ensure_tree_sitter_runtime()
        run_init(Path(directory), output_dir=output)
    except Exception as exc:
        if os.getenv("TLDREADME_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
            raise
        detail = " ".join(str(exc).split())[:500] or type(exc).__name__
        click.echo()
        click.echo("TLDREADME could not complete initialization.")
        click.echo(f"{type(exc).__name__}: {detail}")
        click.echo(
            "Run `tldr doctor` for dependency checks or set TLDREADME_DEBUG=1 for a traceback."
        )
        raise click.exceptions.Exit(1) from None


@main.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--json-output", is_flag=True, help="Print the raw peek payload as JSON.")
@click.option("--markdown", is_flag=True, help="Render output as markdown.")
def peek(path: str, json_output: bool, markdown: bool):
    """Quick reconnaissance of a directory or file - no indexing required."""
    from .peek import peek_target, render_peek, render_peek_markdown

    result = peek_target(path)
    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
    elif markdown:
        click.echo(render_peek_markdown(result))
    else:
        click.echo(render_peek(result))


@main.command()
@click.argument("directories", nargs=-1, type=click.Path(exists=True))
def watch(directories: tuple[str, ...]):
    """Watch directories for changes and re-index incrementally.

    On file save: re-parse changed file's AST, update embeddings
    in Qdrant, update graph edges in FalkorDB, regenerate affected
    TLDR.md sections.
    """
    from .runtime import ensure_tree_sitter_runtime
    from .watcher import start_watcher

    ensure_tree_sitter_runtime()
    start_watcher([Path(d) for d in directories])


@main.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse"], case_sensitive=False),
    default="stdio",
    show_default=True,
    help="MCP transport. Use stdio for Claude Code subprocesses or sse for network clients.",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host for SSE transport")
@click.option("--port", "-p", default=8900, show_default=True, help="Bind port for SSE transport")
@click.option(
    "--tool-profile",
    type=click.Choice(["router", "full"], case_sensitive=False),
    default=lambda: get_setting("TLDREADME_TOOL_PROFILE", "router"),
    show_default=True,
    help="Expose the smaller router-first tool set or the full specialist surface.",
)
def serve(transport: str, host: str, port: int, tool_profile: str):
    """Start the MCP server over stdin/stdout or SSE."""
    from .mcp_server import start_server

    start_server(transport=transport, host=host, port=port, tool_profile=tool_profile)


@main.command()
@click.argument("question")
@click.option(
    "--directory",
    "-d",
    type=click.Path(exists=True, file_okay=False),
    help="Repository to search; defaults to the current directory.",
)
@click.option(
    "--cross-repository",
    is_flag=True,
    help="Explicitly include indexed code from other repositories for shared-code ideation.",
)
def ask(question: str, directory: str | None, cross_repository: bool):
    """Ask a question about the indexed codebase. RAG-powered answer."""
    from .rag import ask_question

    try:
        answer = ask_question(
            question,
            scope=directory,
            cross_repository=cross_repository,
        )
    except Exception as exc:
        if os.getenv("TLDREADME_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
            raise
        detail = " ".join(str(exc).split())[:500] or type(exc).__name__
        raise click.ClickException(
            f"Indexed query unavailable ({type(exc).__name__}): {detail}. "
            "Run `tldr doctor` to check Qdrant and the configured models."
        ) from None
    click.echo(answer)


@main.group()
def audit():
    """Run local dependency, code, secrets, or LLM/adversarial audit checks."""
    pass


def _run_audit_cli(
    category: str,
    *,
    root: str,
    dry_run: bool,
    json_output: bool,
    garak_config: str | None = None,
    offline: bool = False,
    download_offline_db: bool = False,
    kev_catalog: str | None = None,
    profile: str | None = None,
    prefer_snyk: bool = False,
    save_report: bool = False,
):
    """Shared handler for human-facing audit commands."""

    from .audit import render_audit_report, run_audit, save_audit_report

    report = run_audit(
        category,
        root=root,
        dry_run=dry_run,
        garak_config=garak_config,
        offline=offline,
        download_offline_db=download_offline_db,
        kev_catalog_path=kev_catalog,
        profile=profile,
        prefer_snyk=prefer_snyk,
    )
    saved = save_audit_report(report, root=root) if save_report else None
    if json_output:
        payload = dict(report)
        if saved:
            payload["saved_report"] = saved
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    click.echo(render_audit_report(report))
    if saved:
        click.echo(f"Saved report: {saved['path']}")


@audit.command("deps")
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".", required=False)
@click.option(
    "--offline",
    is_flag=True,
    help="Prefer OSV-Scanner offline mode for dependency vulnerabilities.",
)
@click.option(
    "--download-offline-db",
    is_flag=True,
    help="Ask OSV-Scanner to download or refresh its offline vulnerability databases.",
)
@click.option(
    "--kev-catalog",
    type=click.Path(exists=True, dir_okay=False),
    help="Optional local CISA KEV JSON catalog to prioritize known exploited CVEs.",
)
@click.option(
    "--profile",
    type=click.Choice(AUDIT_PROFILE_CHOICES, case_sensitive=False),
    help="Optional OWASP profile to shape follow-up guidance.",
)
@click.option(
    "--prefer-snyk",
    is_flag=True,
    help="Prefer the authenticated Snyk CLI over the local default scanner when available.",
)
@click.option(
    "--save-report",
    is_flag=True,
    help="Persist the audit JSON report under .tldr/security/reports.",
)
@click.option(
    "--dry-run", is_flag=True, help="Show the selected scanner command without executing it."
)
@click.option("--json-output", is_flag=True, help="Print the raw audit payload as JSON.")
def audit_deps(
    root: str,
    offline: bool,
    download_offline_db: bool,
    kev_catalog: str | None,
    profile: str | None,
    prefer_snyk: bool,
    save_report: bool,
    dry_run: bool,
    json_output: bool,
):
    """Audit local dependencies with OSV-Scanner or pip-audit."""
    _run_audit_cli(
        "deps",
        root=root,
        dry_run=dry_run,
        json_output=json_output,
        offline=offline,
        download_offline_db=download_offline_db,
        kev_catalog=kev_catalog,
        profile=profile,
        prefer_snyk=prefer_snyk,
        save_report=save_report,
    )


@audit.command("code")
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".", required=False)
@click.option(
    "--profile",
    type=click.Choice(AUDIT_PROFILE_CHOICES, case_sensitive=False),
    help="Optional OWASP profile to shape follow-up guidance.",
)
@click.option(
    "--prefer-snyk",
    is_flag=True,
    help="Prefer the authenticated Snyk CLI over the local default scanner when available.",
)
@click.option(
    "--save-report",
    is_flag=True,
    help="Persist the audit JSON report under .tldr/security/reports.",
)
@click.option(
    "--dry-run", is_flag=True, help="Show the selected scanner command without executing it."
)
@click.option("--json-output", is_flag=True, help="Print the raw audit payload as JSON.")
def audit_code(
    root: str,
    profile: str | None,
    prefer_snyk: bool,
    save_report: bool,
    dry_run: bool,
    json_output: bool,
):
    """Audit first-party code with Semgrep or Bandit."""
    _run_audit_cli(
        "code",
        root=root,
        dry_run=dry_run,
        json_output=json_output,
        profile=profile,
        prefer_snyk=prefer_snyk,
        save_report=save_report,
    )


@audit.command("secrets")
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".", required=False)
@click.option(
    "--profile",
    type=click.Choice(AUDIT_PROFILE_CHOICES, case_sensitive=False),
    help="Optional OWASP profile to shape follow-up guidance.",
)
@click.option(
    "--save-report",
    is_flag=True,
    help="Persist the audit JSON report under .tldr/security/reports.",
)
@click.option(
    "--dry-run", is_flag=True, help="Show the selected scanner command without executing it."
)
@click.option("--json-output", is_flag=True, help="Print the raw audit payload as JSON.")
def audit_secrets(
    root: str, profile: str | None, save_report: bool, dry_run: bool, json_output: bool
):
    """Audit for committed secrets with Gitleaks."""
    _run_audit_cli(
        "secrets",
        root=root,
        dry_run=dry_run,
        json_output=json_output,
        profile=profile,
        save_report=save_report,
    )


@audit.command("llm")
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".", required=False)
@click.option(
    "--garak-config",
    type=click.Path(exists=True, dir_okay=False),
    help="Explicit Garak config file for the target model and probes.",
)
@click.option(
    "--profile",
    type=click.Choice(AUDIT_PROFILE_CHOICES, case_sensitive=False),
    help="Optional OWASP profile to shape follow-up guidance.",
)
@click.option(
    "--save-report",
    is_flag=True,
    help="Persist the audit JSON report under .tldr/security/reports.",
)
@click.option(
    "--dry-run", is_flag=True, help="Show the selected scanner command without executing it."
)
@click.option("--json-output", is_flag=True, help="Print the raw audit payload as JSON.")
def audit_llm(
    root: str,
    garak_config: str | None,
    profile: str | None,
    save_report: bool,
    dry_run: bool,
    json_output: bool,
):
    """Audit an LLM target with Garak when an explicit config is provided."""
    _run_audit_cli(
        "llm",
        root=root,
        dry_run=dry_run,
        json_output=json_output,
        garak_config=garak_config,
        profile=profile,
        save_report=save_report,
    )


@audit.command("all")
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".", required=False)
@click.option(
    "--garak-config",
    type=click.Path(exists=True, dir_okay=False),
    help="Optional Garak config file to include LLM probes in the full audit.",
)
@click.option(
    "--offline",
    is_flag=True,
    help="Prefer OSV-Scanner offline mode for dependency vulnerabilities.",
)
@click.option(
    "--download-offline-db",
    is_flag=True,
    help="Ask OSV-Scanner to download or refresh its offline vulnerability databases.",
)
@click.option(
    "--kev-catalog",
    type=click.Path(exists=True, dir_okay=False),
    help="Optional local CISA KEV JSON catalog to prioritize known exploited CVEs.",
)
@click.option(
    "--profile",
    type=click.Choice(AUDIT_PROFILE_CHOICES, case_sensitive=False),
    help="Optional OWASP profile to shape follow-up guidance.",
)
@click.option(
    "--prefer-snyk",
    is_flag=True,
    help="Prefer the authenticated Snyk CLI over the local default scanners where supported.",
)
@click.option(
    "--save-report",
    is_flag=True,
    help="Persist the audit JSON report under .tldr/security/reports.",
)
@click.option(
    "--dry-run", is_flag=True, help="Show the selected scanner command without executing it."
)
@click.option("--json-output", is_flag=True, help="Print the raw audit payload as JSON.")
def audit_all(
    root: str,
    garak_config: str | None,
    offline: bool,
    download_offline_db: bool,
    kev_catalog: str | None,
    profile: str | None,
    prefer_snyk: bool,
    save_report: bool,
    dry_run: bool,
    json_output: bool,
):
    """Run the full local audit sweep across deps, code, secrets, and optional LLM probes."""
    _run_audit_cli(
        "all",
        root=root,
        dry_run=dry_run,
        json_output=json_output,
        garak_config=garak_config,
        offline=offline,
        download_offline_db=download_offline_db,
        kev_catalog=kev_catalog,
        profile=profile,
        prefer_snyk=prefer_snyk,
        save_report=save_report,
    )


@audit.command("profiles")
@click.option("--json-output", is_flag=True, help="Print the raw profile payload as JSON.")
def audit_profiles(json_output: bool):
    """List the built-in OWASP-oriented audit profiles."""

    from .audit import list_policy_profiles, render_policy_profiles

    profiles = list_policy_profiles()
    click.echo(
        json.dumps(profiles, indent=2, default=str) if json_output else render_policy_profiles()
    )


@audit.command("kev-refresh")
@click.option(
    "--output",
    type=click.Path(dir_okay=False),
    default=".tldr/security/known_exploited_vulnerabilities.json",
    show_default=True,
    help="Write the CISA KEV JSON catalog to this local path.",
)
@click.option(
    "--url",
    default="https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
    show_default=True,
    help="Official KEV JSON feed URL. Override only if you mirror the catalog locally.",
)
@click.option("--json-output", is_flag=True, help="Print the raw refresh payload as JSON.")
def audit_kev_refresh(output: str, url: str, json_output: bool):
    """Download and cache the CISA Known Exploited Vulnerabilities catalog."""

    from .audit import refresh_kev_catalog

    result = refresh_kev_catalog(output_path=output, url=url)
    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    click.echo(f"Wrote KEV catalog: {result['path']}")
    click.echo(f"Entries: {result['count']}")


@main.command(name="lsp", hidden=True)
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.argument("line", type=int)
@click.option(
    "--column", type=int, help="1-based column. Inferred from the nearest identifier if omitted."
)
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False),
    help="Workspace root for LSP initialization",
)
@click.option("--no-references", is_flag=True, help="Skip the references request")
def lsp_inspect(path: str, line: int, column: int | None, root: str | None, no_references: bool):
    """Query semantic info from the language server for a file position."""
    from .lsp import semantic_inspect

    result = semantic_inspect(
        path,
        line,
        column,
        root=root,
        include_references=not no_references,
    )
    click.echo(json.dumps(result, indent=2, default=str))


@main.command(name="lsp-symbols", hidden=True)
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.argument("query")
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False),
    help="Workspace root for LSP initialization",
)
@click.option("--limit", type=int, default=20, show_default=True, help="Max symbols to return")
def lsp_symbols(path: str, query: str, root: str | None, limit: int):
    """Query workspace symbols from the language server."""
    from .lsp import workspace_symbols

    result = workspace_symbols(path, query, root=root, limit=limit)
    click.echo(json.dumps(result, indent=2, default=str))


@main.command()
@click.option(
    "--fix",
    is_flag=True,
    help="Select install/start commands and write them to an executable script.",
)
@click.option(
    "--fix-output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("start.sh"),
    show_default=True,
    help="Script written by interactive --fix.",
)
@click.option(
    "--diagnostics",
    "diagnostics_path",
    type=click.Path(exists=True, dir_okay=False),
    help="Also inspect LSP diagnostics for a source file.",
)
@click.option("--line", type=int, help="1-based line for --diagnostics")
@click.option("--column", type=int, help="1-based column for --diagnostics")
def doctor(
    fix: bool,
    fix_output: Path,
    diagnostics_path: str | None,
    line: int | None,
    column: int | None,
):
    """Check required runtime dependencies and optional local capabilities."""
    from .runtime import runtime_report

    report = runtime_report()
    for check in report["checks"]:
        label = {
            "ok": "OK",
            "error": "MISSING",
            "warn": "WARN",
            "skip": "SKIP",
        }.get(check["status"], check["status"].upper())
        click.echo(f"{label}: {check['name']} [{check['category']}] - {check['details']}")

    fixable_checks = [check for check in report["checks"] if check["install_options"]]
    fix_script = None
    if fix and fixable_checks:
        fix_script = _run_doctor_fix_flow(fixable_checks, output_path=fix_output)
    elif fixable_checks:
        click.echo()
        click.echo(
            "Tip: run `tldr doctor --fix` to choose install/start commands for non-OK checks."
        )

    if diagnostics_path:
        from .coding_tools import diagnostics_here

        click.echo()
        click.echo("Diagnostics:")
        _render_diagnostics_report(diagnostics_here(diagnostics_path, line=line, column=column))

    if not report["ok"]:
        if fix_script:
            click.echo()
            click.echo("Run the selected commands, then rerun `tldr doctor`.")
            return
        raise click.ClickException("Runtime dependency check failed.")


@main.command()
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--since", help="ISO timestamp override instead of the stored summary checkpoint.")
@click.option(
    "--no-mark-checked", is_flag=True, help="Do not advance the summary checkpoint after printing."
)
@click.option("--limit", type=int, default=10, show_default=True, help="Max items per section.")
@click.option("--json-output", is_flag=True, help="Print the raw summary payload as JSON.")
def summary(root: str, since: str | None, no_mark_checked: bool, limit: int, json_output: bool):
    """Show what changed since the last summary checkpoint."""
    from .summary import build_summary, render_summary

    result = build_summary(root=root, since=since, mark_checked=not no_mark_checked, limit=limit)
    click.echo(json.dumps(result, indent=2, default=str) if json_output else render_summary(result))


@main.command(name="plans-capture")
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--json-output", is_flag=True, help="Print the raw capture payload as JSON.")
def plans_capture(root: str, json_output: bool):
    """Capture pasted planning notes into .tldr/roadmap/TLDRPLANS.<timestamp>.md and refresh the digest."""
    from .roadmap import capture_plan_input

    click.echo(
        "Paste planning notes, links, and examples. Ctrl-D saves to "
        f"{Path(root) / '.tldr/roadmap/TLDRPLANS.<timestamp>.md'}"
    )
    text = sys.stdin.read()
    if not text.strip():
        raise click.ClickException("No planning input received on stdin.")

    result = capture_plan_input(text, root=root)
    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    click.echo(f"Saved capture: {Path(result['capture_path']).name}")
    click.echo(f"Updated plans digest: {Path(result['plans_path']).name}")
    click.echo(f"Captured notes tracked: {result['captures_count']}")


def _run_whats_next(root: str, json_output: bool, cross_repository_ideas: bool = False):
    """Shared handler for the human-facing whats-next report."""

    from .roadmap import render_whats_next_vibe
    from .roadmap import whats_next_vibe as build_whats_next_vibe

    kwargs = {"root": root}
    if cross_repository_ideas:
        kwargs["cross_repository_ideas"] = True
    result = build_whats_next_vibe(**kwargs)
    click.echo(
        json.dumps(result, indent=2, default=str) if json_output else render_whats_next_vibe(result)
    )


@main.command(name="whats-next")
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--json-output", is_flag=True, help="Print the raw roadmap payload as JSON.")
@click.option(
    "--cross-repository-ideas",
    is_flag=True,
    help="Use other repositories only as shared-code idea evidence; tasks remain local.",
)
def whats_next(root: str, json_output: bool, cross_repository_ideas: bool):
    """Show the next strategic question and grounded options for the repository."""
    _run_whats_next(root, json_output, cross_repository_ideas)


@main.command(name="whats-next-vibe", hidden=True)
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".")
@click.option("--json-output", is_flag=True, help="Print the raw roadmap payload as JSON.")
@click.option("--cross-repository-ideas", is_flag=True, hidden=True)
def whats_next_vibe_legacy(root: str, json_output: bool, cross_repository_ideas: bool):
    """Compatibility alias for the previous whats-next command name."""
    _run_whats_next(root, json_output, cross_repository_ideas)


def _run_current_roadmap(
    root: str,
    no_write: bool,
    json_output: bool,
    cross_repository_ideas: bool = False,
):
    """Shared handler for the human-facing roadmap writer."""

    from .roadmap import build_current_vibe_roadmap, render_whats_next_vibe

    kwargs = {"root": root, "write": not no_write}
    if cross_repository_ideas:
        kwargs["cross_repository_ideas"] = True
    result = build_current_vibe_roadmap(**kwargs)
    if json_output:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    if not no_write:
        click.echo(f"Wrote {Path(result['path']).name}")
    click.echo(render_whats_next_vibe(result))


@main.command(name="current-roadmap")
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".")
@click.option(
    "--no-write",
    is_flag=True,
    help="Do not write TLDROADMAP.md or refresh .tldr/roadmap/TLDRPLANS.md.",
)
@click.option("--json-output", is_flag=True, help="Print the raw roadmap payload as JSON.")
@click.option(
    "--cross-repository-ideas",
    is_flag=True,
    help="Use other repositories only as shared-code idea evidence; tasks remain local.",
)
def current_roadmap(
    root: str,
    no_write: bool,
    json_output: bool,
    cross_repository_ideas: bool,
):
    """Build the current roadmap snapshot and optionally write TLDROADMAP.md."""
    _run_current_roadmap(root, no_write, json_output, cross_repository_ideas)


@main.command(name="current-vibe-roadmap", hidden=True)
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".")
@click.option(
    "--no-write",
    is_flag=True,
    help="Do not write TLDROADMAP.md or refresh .tldr/roadmap/TLDRPLANS.md.",
)
@click.option("--json-output", is_flag=True, help="Print the raw roadmap payload as JSON.")
@click.option("--cross-repository-ideas", is_flag=True, hidden=True)
def current_vibe_roadmap_legacy(
    root: str,
    no_write: bool,
    json_output: bool,
    cross_repository_ideas: bool,
):
    """Compatibility alias for the previous current-roadmap command name."""
    _run_current_roadmap(root, no_write, json_output, cross_repository_ideas)


@main.group()
def children():
    """List and acknowledge nested child projects under the current repository."""
    pass


@children.command(name="list")
@click.argument("root", type=click.Path(exists=True, file_okay=False), default=".", required=False)
@click.option(
    "--status", type=click.Choice(["unknown", "merged", "ignored"]), help="Optional status filter."
)
@click.option(
    "--all", "include_ignored", is_flag=True, help="Include ignored children in the output."
)
@click.option("--json-output", is_flag=True, help="Print the raw child payload as JSON.")
def children_list(root: str, status: str | None, include_ignored: bool, json_output: bool):
    """List detected child subtrees and their acknowledgment status."""
    from .children import list_children

    result = list_children(root=root, status=status, include_ignored=include_ignored)
    click.echo(
        json.dumps(result, indent=2, default=str)
        if json_output
        else _render_children_listing(result)
    )


@children.command("merge")
@click.argument("path")
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False),
    default=".",
    show_default=True,
    help="Repository root.",
)
@click.option("--note", help="Optional note explaining why the child is merged.")
def children_merge(path: str, root: str, note: str | None):
    """Mark a child subtree as intentionally merged into this repository."""
    from .children import describe_child, merge_child

    result = merge_child(path, root=root, note=note)
    click.echo(f"MERGED: {result['path']} - {describe_child(result)}")


@children.command("ignore")
@click.argument("path")
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False),
    default=".",
    show_default=True,
    help="Repository root.",
)
@click.option("--note", help="Optional note explaining why the child is ignored.")
def children_ignore(path: str, root: str, note: str | None):
    """Mark a child subtree as intentionally ignored."""
    from .children import describe_child, ignore_child

    result = ignore_child(path, root=root, note=note)
    click.echo(f"IGNORED: {result['path']} - {describe_child(result)}")


def _run_doctor_fix_flow(
    checks: list[dict[str, object]],
    *,
    output_path: Path = Path("start.sh"),
) -> Path | None:
    """Render install/start guidance and write selected commands to a script."""

    click.echo()
    click.echo("Install / start options:")

    for idx, check in enumerate(checks, start=1):
        click.echo(f"[ ] {idx}. {check['name']} [{check['category']}]")
        for option_idx, option in enumerate(check["install_options"]):
            prefix = "Recommended" if option_idx == 0 else "Option"
            click.echo(f"    {prefix}: {option['label']}")
            click.echo(f"      {option['command']}")

    if not sys.stdin.isatty():
        click.echo()
        click.echo("Printed all available options because this session is non-interactive.")
        return None

    selected = _select_doctor_fix_items(checks, script_name=output_path.name)
    if not selected:
        return None

    click.echo()
    click.echo("Selected:")
    for check in selected:
        click.echo(f"[x] {check['name']} [{check['category']}]")
        if check["install_options"]:
            click.echo(f"    {check['install_options'][0]['command']}")

    script_path = _write_doctor_fix_script(selected, output_path=output_path)
    run_path = str(output_path) if output_path.is_absolute() else f"./{output_path}"
    click.echo()
    click.echo(f"Wrote executable script: {script_path}")
    click.echo(f"Run these commands individually or run {run_path} for faster start.")
    return script_path


def _write_doctor_fix_script(
    checks: list[dict[str, object]],
    *,
    output_path: Path = Path("start.sh"),
) -> Path:
    """Write selected recommended commands to a reviewable executable script."""

    target = output_path.expanduser().resolve()
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Review these commands before running the script.",
        f"cd {shlex.quote(str(Path.cwd().resolve()))}",
    ]
    for check in checks:
        options = list(check.get("install_options", []))
        if not options:
            continue
        label = f"{check.get('name', 'Dependency')} [{check.get('category', 'runtime')}]"
        lines.extend(
            [
                "",
                f"echo {shlex.quote(f'==> {label}')}",
                str(options[0]["command"]),
            ]
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8") as script:
            script.write("\n".join(lines) + "\n")
    except FileExistsError as exc:
        raise click.ClickException(
            f"Refusing to overwrite existing script: {target}. "
            "Choose another path with `--fix-output`."
        ) from exc
    target.chmod(0o755)
    return target


def _render_children_listing(result: dict) -> str:
    """Render a concise human-facing child listing."""

    from .children import describe_child

    lines = [
        "Children: "
        f"{result.get('count', 0)} listed "
        f"({result.get('unknown_count', 0)} unknown, "
        f"{result.get('merged_count', 0)} merged, "
        f"{result.get('ignored_count', 0)} ignored)"
    ]

    entries = result.get("children", [])
    if not entries:
        lines.append("No child projects detected.")
        return "\n".join(lines)

    for child in entries:
        lines.append(f"{child['status'].upper()}: {child['path']} - {describe_child(child)}")

    return "\n".join(lines)


def _render_diagnostics_report(report: dict) -> None:
    """Render diagnostics in a doctor-style human format."""

    diagnostics = report.get("diagnostics", [])
    if diagnostics:
        for item in diagnostics:
            label = str(item.get("severity", "info")).upper()
            click.echo(f"{label}: {item.get('path')}:{item.get('line')} - {item.get('message')}")
    else:
        click.echo(f"OK: no diagnostics reported for {report.get('path')}")

    likely_fix_area = report.get("likely_fix_area")
    if likely_fix_area:
        click.echo(
            "Likely fix area: "
            f"{likely_fix_area.get('path')}:{likely_fix_area.get('line')} "
            f"({likely_fix_area.get('severity')})"
        )

    impacted_symbols = report.get("impacted_symbols") or []
    if impacted_symbols:
        click.echo(f"Impacted symbols: {', '.join(impacted_symbols)}")

    verification_commands = report.get("verification_commands") or []
    if verification_commands:
        click.echo(f"Verification: {verification_commands[0]}")

    fallback_used = report.get("fallback_used") or []
    if fallback_used:
        click.echo(f"Fallbacks: {', '.join(fallback_used)}")


def _select_doctor_fix_items(
    checks: list[dict[str, object]],
    *,
    script_name: str = "start.sh",
) -> list[dict[str, object]]:
    """Select fixable checks with a checkbox prompt."""

    questionary = _load_questionary()
    choices = []
    for check in checks:
        title = f"{check['name']} [{check['category']}]"
        if check["install_options"]:
            title += f" -> {check['install_options'][0]['command']}"
        choices.append(questionary.Choice(title=title, value=check))

    selected = questionary.checkbox(
        f"Select commands to add to {script_name}",
        choices=choices,
        qmark="",
        instruction="Use arrows, space to toggle, enter to confirm",
    ).ask()

    return selected or []


def _load_questionary():
    """Import questionary lazily for the interactive doctor flow."""

    try:
        import questionary
    except ImportError as exc:
        raise click.ClickException(
            "Interactive doctor fixes require `questionary`. Run `python3.12 -m pip install questionary` "
            "or reinstall the project dependencies."
        ) from exc

    return questionary


if __name__ == "__main__":
    main(prog_name="tldr")
