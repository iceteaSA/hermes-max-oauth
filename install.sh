#!/usr/bin/env bash
# Install Hermes Max OAuth patch on a vanilla hermes-agent installation.
#
# Usage:
#   ./install.sh /path/to/hermes-agent
#   ./install.sh                          # auto-detect
#
# What it does:
#   1. Copies agent/cch.py, agent/claude_identity.py, agent/prompt_sanitizer.py
#      into hermes's agent/ directory
#   2. Copies hermes_max_config.json to ~/.hermes/
#   3. Installs hermes_max_patch.py into the venv's site-packages
#   4. Creates a .pth file to activate the patcher at Python startup
#   5. Installs xxhash dependency
#
# No hermes-agent source files are modified.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$SCRIPT_DIR"

# ── Detect hermes-agent directory ──────────────────────────────────
HERMES_DIR="${1:-}"

if [[ -z "$HERMES_DIR" ]]; then
    if [[ -d "$HOME/.hermes/hermes-agent" ]]; then
        HERMES_DIR="$HOME/.hermes/hermes-agent"
    elif command -v hermes &>/dev/null; then
        HERMES_BIN="$(command -v hermes)"
        HERMES_BIN="$(readlink -f "$HERMES_BIN" 2>/dev/null || echo "$HERMES_BIN")"
        HERMES_DIR="$(dirname "$(dirname "$HERMES_BIN")")"
    else
        echo "Usage: $0 /path/to/hermes-agent"
        echo "Could not auto-detect hermes-agent installation."
        exit 1
    fi
fi

if [[ ! -f "$HERMES_DIR/agent/anthropic_adapter.py" ]]; then
    echo "Error: $HERMES_DIR does not look like a hermes-agent installation."
    echo "Expected to find agent/anthropic_adapter.py"
    exit 1
fi

echo "hermes-agent: $HERMES_DIR"

# ── Detect venv ────────────────────────────────────────────────────
VENV=""
for candidate in ".venv" "venv"; do
    if [[ -d "$HERMES_DIR/$candidate" ]]; then
        VENV="$HERMES_DIR/$candidate"
        break
    fi
done

if [[ -z "$VENV" ]]; then
    echo "Error: No .venv or venv found in $HERMES_DIR"
    exit 1
fi

PYTHON="$VENV/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
    PYTHON="$VENV/bin/python"
fi

SITE_PACKAGES="$("$PYTHON" -c 'import site; print(site.getsitepackages()[0])')"

echo "venv: $VENV"
echo "site-packages: $SITE_PACKAGES"
echo ""

# ── 1. Copy agent modules ─────────────────────────────────────────
echo "Installing agent modules..."
for mod in cch.py claude_identity.py prompt_sanitizer.py; do
    if [[ -f "$HERMES_DIR/agent/$mod" ]]; then
        echo "  agent/$mod already exists — backing up to agent/$mod.bak"
        cp "$HERMES_DIR/agent/$mod" "$HERMES_DIR/agent/$mod.bak"
    fi
    cp "$REPO_DIR/agent/$mod" "$HERMES_DIR/agent/$mod"
    echo "  -> agent/$mod"
done

# ── 2. Copy config ────────────────────────────────────────────────
echo ""
echo "Installing config..."
mkdir -p "$HOME/.hermes"
if [[ ! -f "$HOME/.hermes/hermes_max_config.json" ]]; then
    cp "$REPO_DIR/hermes_max_config.json" "$HOME/.hermes/hermes_max_config.json"
    echo "  -> ~/.hermes/hermes_max_config.json"
else
    echo "  ~/.hermes/hermes_max_config.json exists, skipping"
fi

# ── 3. Install runtime patcher ────────────────────────────────────
echo ""
echo "Installing runtime patcher..."
cp "$SCRIPT_DIR/hermes_max_patch.py" "$SITE_PACKAGES/hermes_max_patch.py"
echo "  -> $SITE_PACKAGES/hermes_max_patch.py"

# ── 4. Create .pth activation file ────────────────────────────────
echo "import hermes_max_patch; hermes_max_patch.activate()" \
    > "$SITE_PACKAGES/hermes-max-oauth.pth"
echo "  -> $SITE_PACKAGES/hermes-max-oauth.pth"

# ── 5. Install xxhash ─────────────────────────────────────────────
echo ""
echo "Installing xxhash..."
if "$PYTHON" -c "import xxhash" 2>/dev/null; then
    echo "  xxhash already installed"
else
    PIP="$VENV/bin/pip"
    if [[ -x "$VENV/bin/uv" ]]; then
        "$VENV/bin/uv" pip install -q "xxhash>=3.4.0,<4"
    else
        "$PIP" install -q "xxhash>=3.4.0,<4"
    fi
    echo "  xxhash installed"
fi

echo ""
echo "Done. Restart hermes to activate Max OAuth routing."
echo "To uninstall: ./uninstall.sh $HERMES_DIR"
