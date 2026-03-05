#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${OPENCLAW_WORKSPACE:-}" ]]; then
  WORKSPACE_DIR="$OPENCLAW_WORKSPACE"
else
  WORKSPACE_DIR="$(cd "$ROOT_DIR/.." && pwd)"
fi

TARGET_DIR="$WORKSPACE_DIR/skills/opensea"

if [[ ! -d "$TARGET_DIR" ]]; then
  echo "[INFO] Nothing to uninstall — $TARGET_DIR not found."
  exit 0
fi

rm -rf "$TARGET_DIR"
echo "OpenSea skill uninstalled from: $TARGET_DIR"
