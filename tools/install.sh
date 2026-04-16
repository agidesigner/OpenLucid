#!/usr/bin/env bash
set -euo pipefail

# OpenLucid CLI installer
# Usage: bash tools/install.sh  (run from the project root)

INSTALL_DIR="${HOME}/.local/bin"
CLI_NAME="openlucid-cli"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLI_SRC="${SCRIPT_DIR}/openlucid-cli"
SKILL_SRC="${SCRIPT_DIR}/../skills/openlucid-cli/SKILL.md"

echo "=== OpenLucid CLI Installer ==="
echo ""

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is required but not found."
    echo "Install Python 3.8+ from https://www.python.org/downloads/"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Found Python ${PY_VERSION}"

if [ ! -f "${CLI_SRC}" ]; then
    echo "Error: ${CLI_SRC} not found."
    echo "Please run this script from the project root: bash tools/install.sh"
    exit 1
fi

if [ ! -f "${SKILL_SRC}" ]; then
    echo "Error: ${SKILL_SRC} not found."
    echo "Expected skill source at skills/openlucid-cli/SKILL.md"
    exit 1
fi

mkdir -p "${INSTALL_DIR}"
cp "${CLI_SRC}" "${INSTALL_DIR}/${CLI_NAME}"
chmod +x "${INSTALL_DIR}/${CLI_NAME}"
echo "Copied CLI from ${CLI_SRC}"

add_to_path() {
    local rc_file="$1"
    local line='export PATH="${HOME}/.local/bin:${PATH}"'
    if [ -f "${rc_file}" ] && grep -qF '.local/bin' "${rc_file}" 2>/dev/null; then
        return 0
    fi
    echo "" >> "${rc_file}"
    echo "# OpenLucid CLI" >> "${rc_file}"
    echo "${line}" >> "${rc_file}"
    echo "Added ~/.local/bin to PATH in ${rc_file}"
}

if ! echo "${PATH}" | tr ':' '\n' | grep -qF "${INSTALL_DIR}"; then
    SHELL_NAME=$(basename "${SHELL:-/bin/bash}")
    case "${SHELL_NAME}" in
        zsh)  add_to_path "${HOME}/.zshrc" ;;
        bash)
            if [ -f "${HOME}/.bash_profile" ]; then
                add_to_path "${HOME}/.bash_profile"
            else
                add_to_path "${HOME}/.bashrc"
            fi
            ;;
        *)    add_to_path "${HOME}/.profile" ;;
    esac
    export PATH="${INSTALL_DIR}:${PATH}"
fi

install_skill() {
    local skill_root="$1"
    local skill_dir="${skill_root}/openlucid-cli"
    mkdir -p "${skill_dir}"
    cp "${SKILL_SRC}" "${skill_dir}/SKILL.md"
    echo "Installed skill: ${skill_dir}/SKILL.md"
}

remove_marked_block() {
    local filepath="$1"
    local begin_marker="<!-- BEGIN openlucid-cli -->"
    local end_marker="<!-- END openlucid-cli -->"
    if [ ! -f "${filepath}" ] || ! grep -qF "${begin_marker}" "${filepath}" 2>/dev/null; then
        return 0
    fi
    local tmp="${filepath}.tmp.$$"
    awk -v bm="${begin_marker}" -v em="${end_marker}" '
        $0 == bm { skip=1; next }
        $0 == em { skip=0; next }
        !skip { print }
    ' "${filepath}" > "${tmp}"
    mv "${tmp}" "${filepath}"
    echo "Cleaned legacy config block: ${filepath}"
}

cleanup_legacy_configs() {
    remove_marked_block "${HOME}/.claude/CLAUDE.md"
    remove_marked_block "${HOME}/.agents/AGENTS.md"
    if [ -f "${HOME}/.cursor/rules/openlucid-cli.mdc" ]; then
        rm -f "${HOME}/.cursor/rules/openlucid-cli.mdc"
        echo "Removed legacy rule: ~/.cursor/rules/openlucid-cli.mdc"
    fi
    if [ -f "${HOME}/.claude/CLAUDE.md" ] && ! grep -q '[^[:space:]]' "${HOME}/.claude/CLAUDE.md"; then
        rm -f "${HOME}/.claude/CLAUDE.md"
        echo "Removed empty legacy file: ~/.claude/CLAUDE.md"
    fi
    if [ -f "${HOME}/.agents/AGENTS.md" ] && ! grep -q '[^[:space:]]' "${HOME}/.agents/AGENTS.md"; then
        rm -f "${HOME}/.agents/AGENTS.md"
        echo "Removed empty legacy file: ~/.agents/AGENTS.md"
    fi
}

cleanup_legacy_configs

install_skill "${HOME}/.claude/skills"
install_skill "${HOME}/.agents/skills"
install_skill "${HOME}/.cursor/skills"

if command -v "${CLI_NAME}" &>/dev/null; then
    echo ""
    echo "Installed: $(which ${CLI_NAME})"
else
    echo ""
    echo "Installed to: ${INSTALL_DIR}/${CLI_NAME}"
    echo "Restart your terminal or run: export PATH=\"\${HOME}/.local/bin:\${PATH}\""
fi

echo ""
echo "=== Next steps ==="
echo "  openlucid-cli setup           # Configure server URL and authenticate"
echo "  openlucid-cli list-merchants  # Verify it works"
echo ""
echo "Skills installed:"
echo "  Claude Code : ~/.claude/skills/openlucid-cli/SKILL.md"
echo "  Shared      : ~/.agents/skills/openlucid-cli/SKILL.md"
echo "  Cursor      : ~/.cursor/skills/openlucid-cli/SKILL.md"
echo ""
echo "OpenLucid now uses skill-based agent discovery."
echo ""
