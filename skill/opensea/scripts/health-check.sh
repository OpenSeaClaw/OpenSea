#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ok=1

check_bin() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "[OK] $1"
  else
    echo "[MISSING] $1"
    ok=0
  fi
}

echo "=== OpenSea health check ==="
echo ""
echo "Checking system tools..."
check_bin bash
check_bin curl
check_bin python3
check_bin jq

echo ""
echo "Checking config files..."
for f in "$SKILL_DIR/config/defaults.json" "$SKILL_DIR/config/providers.json" "$SKILL_DIR/SKILL.md" "$SKILL_DIR/scripts/search.py"; do
  if [[ -f "$f" ]]; then
    echo "[OK] $(basename "$f")"
  else
    echo "[MISSING] $f"
    ok=0
  fi
done

echo ""
echo "Checking flock.io API key..."
if [[ -n "${FLOCK_API_KEY:-}" ]]; then
  echo "[OK] FLOCK_API_KEY is set (env var)"
else
  echo "[WARN] FLOCK_API_KEY is not set — required for flock.io API calls"
fi

echo ""
if [[ "$ok" -eq 1 ]]; then
  echo "Health check PASSED"
  exit 0
else
  echo "Health check FAILED"
  exit 1
fi
