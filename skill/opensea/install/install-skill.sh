#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# install-skill.sh (inside skill/opensea/install/)
# Copies THIS skill directory into the OpenClaw workspace.
# Designed to work whether invoked from the repo root or from skill/opensea/.
# ---------------------------------------------------------------------------

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${OPENCLAW_WORKSPACE:-}" ]]; then
  WORKSPACE_DIR="$OPENCLAW_WORKSPACE"
else
  WORKSPACE_DIR="$(cd "$SKILL_DIR/../.." && pwd)"
fi

TARGET_DIR="$WORKSPACE_DIR/skills/opensea"
BACKUP_DIR="$WORKSPACE_DIR/skills/.opensea.backup.$(date +%Y%m%d-%H%M%S)"

mkdir -p "$WORKSPACE_DIR/skills"

if [[ -d "$TARGET_DIR" ]]; then
  echo "Existing install found at $TARGET_DIR"
  cp -R "$TARGET_DIR" "$BACKUP_DIR"
  echo "Backup created: $BACKUP_DIR"
  rm -rf "$TARGET_DIR"
fi

cp -R "$SKILL_DIR" "$TARGET_DIR"

echo "Installed skill to: $TARGET_DIR"
echo "Installed SKILL.md: $TARGET_DIR/SKILL.md"
