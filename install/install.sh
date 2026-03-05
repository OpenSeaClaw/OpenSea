#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Installing OpenSea skill..."
chmod +x "$ROOT_DIR"/scripts/health-check.sh \
         "$ROOT_DIR"/install/*.sh

mkdir -p "$ROOT_DIR/runtime"

# Install Python dependencies if pip3 is available
if command -v pip3 >/dev/null 2>&1; then
  echo "Installing Python dependencies..."
  pip3 install -q -r "$ROOT_DIR/requirements.txt"
  echo "[OK] Python dependencies installed"
else
  echo "[WARN] pip3 not found — install manually: pip install -r requirements.txt"
fi

echo ""
echo "Done. Run: bash install/verify.sh"
