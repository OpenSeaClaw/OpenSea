#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
for f in \
  "$ROOT_DIR/config/defaults.json" \
  "$ROOT_DIR/config/providers.json" \
  "$ROOT_DIR/SKILL.md" \
  "$ROOT_DIR/scripts/search.py"; do
  if [[ -f "$f" ]]; then
    echo "[OK] $(basename "$f")"
  else
    echo "[MISSING] $f"
    ok=0
  fi
done

echo ""
echo "Checking Python dependencies..."
if python3 -c "import requests, dotenv" 2>/dev/null; then
  echo "[OK] requests + python-dotenv importable"
else
  echo "[WARN] Missing Python packages — run: pip install -r requirements.txt"
fi

echo ""
echo "Checking provider API keys..."
if [[ -n "${ZAI_API_KEY:-}" ]]; then
  echo "[OK] ZAI_API_KEY is set (env var)"
elif [[ -f "$ROOT_DIR/runtime/.env" ]] && grep -q "ZAI_API_KEY" "$ROOT_DIR/runtime/.env" 2>/dev/null; then
  echo "[OK] ZAI_API_KEY found in runtime/.env"
else
  echo "[WARN] ZAI_API_KEY is not set"
fi

if [[ -n "${FLOCK_API_KEY:-}" ]]; then
  echo "[OK] FLOCK_API_KEY is set (env var)"
elif [[ -f "$ROOT_DIR/runtime/.env" ]] && grep -q "FLOCK_API_KEY" "$ROOT_DIR/runtime/.env" 2>/dev/null; then
  echo "[OK] FLOCK_API_KEY found in runtime/.env"
else
  echo "[WARN] FLOCK_API_KEY is not set"
fi

if [[ -z "${ZAI_API_KEY:-}" && -z "${FLOCK_API_KEY:-}" ]] && [[ ! -f "$ROOT_DIR/runtime/.env" || ! grep -Eq "ZAI_API_KEY|FLOCK_API_KEY" "$ROOT_DIR/runtime/.env" 2>/dev/null ]]; then
  echo "       One provider key is required to run searches."
  echo "       Set via: export ZAI_API_KEY=<key> or export FLOCK_API_KEY=<key>"
  echo "       Or run:  python3 scripts/search.py --provider z.ai --save-key <key>"
fi

echo ""
if [[ "$ok" -eq 1 ]]; then
  echo "Health check PASSED"
  exit 0
else
  echo "Health check FAILED — resolve missing items above"
  exit 1
fi
