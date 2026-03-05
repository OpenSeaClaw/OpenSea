#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${OPENCLAW_WORKSPACE:-}" ]]; then
  WORKSPACE_DIR="$OPENCLAW_WORKSPACE"
else
  WORKSPACE_DIR="$(cd "$ROOT_DIR/.." && pwd)"
fi

TARGET_DIR="$WORKSPACE_DIR/skills/opensea"

# Check SKILL.md is present
if [[ ! -f "$TARGET_DIR/SKILL.md" ]]; then
  echo "VERIFY FAILED: missing $TARGET_DIR/SKILL.md" >&2
  exit 1
fi

# Check skill name matches
if ! grep -q "^name: opensea$" "$TARGET_DIR/SKILL.md"; then
  echo "VERIFY FAILED: SKILL.md does not look like the OpenSea skill" >&2
  exit 1
fi

# Check search.py is present
if [[ ! -f "$TARGET_DIR/scripts/search.py" ]]; then
  echo "VERIFY FAILED: missing $TARGET_DIR/scripts/search.py" >&2
  exit 1
fi

echo "VERIFY PASSED"
echo "skill_path=$TARGET_DIR"
echo "skill_name=$(grep -m1 '^name:' "$TARGET_DIR/SKILL.md" | awk '{print $2}')"
