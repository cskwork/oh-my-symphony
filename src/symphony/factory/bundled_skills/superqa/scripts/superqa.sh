#!/usr/bin/env sh
# SuperQA launcher - runs the TUI (no args) or forwards CLI subcommands.
#   bash scripts/superqa.sh                 # TUI
#   bash scripts/superqa.sh run --all       # CLI passthrough
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SK_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${SUPERQA_PYTHON:-python3}"

if ! "$PYTHON" -c "import textual, playwright, yaml, PIL" 2>/dev/null; then
  echo "SuperQA: installing dependencies (textual, playwright, pyyaml, pillow)..."
  "$PYTHON" -m pip install --quiet textual playwright pyyaml pillow || {
    echo "SuperQA: pip install failed - install manually: pip3 install textual playwright pyyaml pillow" >&2
    exit 1
  }
fi

if ! "$PYTHON" -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
  echo "SuperQA: playwright import failed" >&2
  exit 1
fi

# Chromium binary (one-time)
if ! "$PYTHON" - <<'EOF' 2>/dev/null
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    path = p.chromium.executable_path
import os, sys
sys.exit(0 if os.path.exists(path) else 1)
EOF
then
  echo "SuperQA: installing Chromium (one-time)..."
  "$PYTHON" -m playwright install chromium
fi

cd "$SK_ROOT" && exec "$PYTHON" -m superqa_tui "$@"
