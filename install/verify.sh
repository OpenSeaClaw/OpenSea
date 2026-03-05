#!/usr/bin/env bash
set -euo pipefail

ok=1

check_bin() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "[OK] $1 found: $(command -v "$1")"
  else
    echo "[MISSING] $1 — please install it"
    ok=0
  fi
}

echo "=== OpenSea dependency check ==="
check_bin bash
check_bin curl
check_bin jq
check_bin python3
check_bin pip3

echo ""
if [[ "$ok" -eq 1 ]]; then
  echo "All dependencies satisfied."
  exit 0
else
  echo "Some dependencies are missing. Please install them and re-run."
  exit 1
fi
