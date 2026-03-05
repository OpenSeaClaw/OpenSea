#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# install-skill.sh
# Copies skill/opensea/ into the OpenClaw workspace skills directory.
# Default target: <workspace>/skills/opensea
#
# Override workspace root:  export OPENCLAW_WORKSPACE=/path/to/workspace
# ---------------------------------------------------------------------------

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_SRC="$ROOT_DIR/skill/opensea"

if [[ ! -f "$SKILL_SRC/SKILL.md" ]]; then
  echo "ERROR: skill source missing at $SKILL_SRC/SKILL.md" >&2
  exit 1
fi

# Resolve workspace root
if [[ -n "${OPENCLAW_WORKSPACE:-}" ]]; then
  WORKSPACE_DIR="$OPENCLAW_WORKSPACE"
else
  WORKSPACE_DIR="$(cd "$ROOT_DIR/.." && pwd)"
fi

TARGET_DIR="$WORKSPACE_DIR/skills/opensea"
BACKUP_DIR="$WORKSPACE_DIR/skills/.opensea.backup.$(date +%Y%m%d-%H%M%S)"

mkdir -p "$WORKSPACE_DIR/skills"

# Backup existing install if present
if [[ -d "$TARGET_DIR" ]]; then
  echo "Existing install found at $TARGET_DIR"
  cp -R "$TARGET_DIR" "$BACKUP_DIR"
  echo "Backup created: $BACKUP_DIR"
  rm -rf "$TARGET_DIR"
fi

cp -R "$SKILL_SRC" "$TARGET_DIR"

echo "Installed skill to: $TARGET_DIR"
echo "Installed SKILL.md: $TARGET_DIR/SKILL.md"
echo "Git revision: $(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo 'n/a')"
