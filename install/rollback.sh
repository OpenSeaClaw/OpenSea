#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${OPENCLAW_WORKSPACE:-}" ]]; then
  WORKSPACE_DIR="$OPENCLAW_WORKSPACE"
else
  WORKSPACE_DIR="$(cd "$ROOT_DIR/.." && pwd)"
fi

SKILLS_DIR="$WORKSPACE_DIR/skills"
TARGET_DIR="$SKILLS_DIR/opensea"

# Find the most recent backup
LATEST_BACKUP="$(ls -dt "$SKILLS_DIR"/.opensea.backup.* 2>/dev/null | head -n 1 || true)"

if [[ -z "$LATEST_BACKUP" ]]; then
  echo "ERROR: No backup found in $SKILLS_DIR" >&2
  exit 1
fi

echo "Rolling back to: $LATEST_BACKUP"

if [[ -d "$TARGET_DIR" ]]; then
  rm -rf "$TARGET_DIR"
fi

cp -R "$LATEST_BACKUP" "$TARGET_DIR"
echo "Rollback complete. Restored to: $TARGET_DIR"
echo "Backup used: $LATEST_BACKUP"
