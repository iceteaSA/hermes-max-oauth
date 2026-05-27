#!/usr/bin/env bash
set -euo pipefail

# One-line installer for hermes-max-oauth.
# Usage: curl -fsSL https://raw.githubusercontent.com/iceteaSA/hermes-max-oauth/main/install-remote.sh | bash

REPO="https://github.com/iceteaSA/hermes-max-oauth.git"
TMPDIR="$(mktemp -d)"

cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

git clone --depth 1 "$REPO" "$TMPDIR" 2>/dev/null
bash "$TMPDIR/install.sh" "$@"
