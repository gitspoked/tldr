#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TLDR_BIN="${SCRIPT_DIR}/.venv/bin/tldr"

if [[ ! -x "${TLDR_BIN}" ]]; then
    if command -v tldr >/dev/null 2>&1; then
        TLDR_BIN="$(command -v tldr)"
    else
        printf 'TLDREADME is not installed.\n'
        printf "Run: python3.12 -m venv .venv && .venv/bin/pip install -e '.'\n"
        exit 1
    fi
fi

section() {
    printf '\n== %s ==\n' "$1"
}

run() {
    printf '$'
    printf ' %q' "$@"
    printf '\n'
    "$@"
}

section "Zero-infrastructure repository overview"
run "${TLDR_BIN}" peek "${SCRIPT_DIR}"

section "Saved configuration"
if ! run "${TLDR_BIN}" setup --check; then
    printf '\nProvider-backed commands remain disabled until setup is complete.\n'
    printf 'Run: %q setup\n' "${TLDR_BIN}"
    printf 'The MCP server will expose only configuration_setup in this state.\n'
    exit 0
fi

section "Runtime readiness"
run "${TLDR_BIN}" doctor

section "MCP profiles"
printf '%s\n' \
    'router: repo_next_action, repo_lookup, change_plan, verify_change' \
    'full: direct search, graph, language-server, planning, workboard, and audit tools' \
    'Use repo://tooling from an MCP client to inspect the active capability surface.'

if [[ "${TLDREADME_DEMO_INDEX:-0}" == "1" ]]; then
    section "Index this repository"
    run "${TLDR_BIN}" init "${SCRIPT_DIR}"
else
    printf '\nSet TLDREADME_DEMO_INDEX=1 to run the provider-backed indexing demo.\n'
fi

section "Marketplace installation"
printf '%s\n' \
    'Codex:  codex plugin marketplace add gitspoked/tldr' \
    '        codex plugin add tldreadme@gitspoked' \
    'Claude: /plugin marketplace add gitspoked/tldr' \
    '        /plugin install tldreadme@gitspoked'
