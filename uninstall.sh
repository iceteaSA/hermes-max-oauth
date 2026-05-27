#!/usr/bin/env bash
# Remove Hermes Max OAuth patch from a hermes-agent installation.
#
# Usage:
#   ./uninstall.sh /path/to/hermes-agent
#   ./uninstall.sh                          # auto-detect
set -euo pipefail

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
        exit 1
    fi
fi

if [[ ! -f "$HERMES_DIR/agent/anthropic_adapter.py" ]]; then
    echo "Error: $HERMES_DIR does not look like a hermes-agent installation."
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
echo ""

# ── Remove agent modules ──────────────────────────────────────────
echo "Removing agent modules..."
for mod in cch.py claude_identity.py prompt_sanitizer.py; do
    if [[ -f "$HERMES_DIR/agent/$mod" ]]; then
        rm -f "$HERMES_DIR/agent/$mod"
        echo "  removed agent/$mod"
        # Restore backup if exists
        if [[ -f "$HERMES_DIR/agent/$mod.bak" ]]; then
            mv "$HERMES_DIR/agent/$mod.bak" "$HERMES_DIR/agent/$mod"
            echo "  restored agent/$mod from backup"
        fi
    fi
done

# ── Remove patcher + .pth ─────────────────────────────────────────
echo ""
echo "Removing runtime patcher..."
rm -f "$SITE_PACKAGES/hermes_max_patch.py"
rm -f "$SITE_PACKAGES/hermes-max-oauth.pth"
echo "  removed hermes_max_patch.py + hermes-max-oauth.pth"

# ── Note: xxhash and config left in place ─────────────────────────
echo ""
echo "Done. Restart hermes to deactivate."
echo "Note: xxhash and ~/.hermes/hermes_max_config.json left in place."
