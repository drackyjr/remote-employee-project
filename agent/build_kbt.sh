#!/usr/bin/env bash
# ==============================================================================
# KBT Executable — Linux Build Script
# Produces: agent/dist/KBT  (single ELF binary, no install required)
#
# Usage:
#   cd /home/kali/Desktop/MACHINE
#   bash agent/build_kbt.sh
#
# Prerequisites:
#   pip install pyinstaller PyQt6 psutil pillow watchdog requests
# ==============================================================================

set -euo pipefail

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="$AGENT_DIR/dist"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
step() { echo -e "\n${GREEN}══ $* ${NC}"; }
fail() { echo -e "${RED}✗${NC} $*"; exit 1; }

cd "$AGENT_DIR"
mkdir -p "$DIST_DIR"

# ── Detect Python / pip ────────────────────────────────────────────────────────
PYTHON="${PYTHON:-python3}"
PIP="${PIP:-pip3}"

# Check for a virtualenv in common locations
for venv in /venv .venv venv; do
    if [[ -f "$venv/bin/python" ]]; then
        PYTHON="$venv/bin/python"
        PIP="$venv/bin/pip"
        break
    fi
done

ok "Using Python: $($PYTHON --version 2>&1)"

# ── Install / verify build deps ────────────────────────────────────────────────
step "Installing build dependencies"
$PIP install pyinstaller PyQt6 psutil pillow watchdog requests \
    pynput scapy --quiet --exists-action i || warn "Some packages may be missing"
ok "Dependencies ready"

# ── Create a placeholder identity for the base build ──────────────────────────
step "Creating base kbt_identity.json placeholder"
cat > "$AGENT_DIR/kbt_identity.json" <<'EOF'
{
  "_note": "BASE BUILD — replace with generate_kbt.py for per-employee packaging",
  "employee_id": "PLACEHOLDER",
  "token": "PLACEHOLDER",
  "api_url": "",
  "generated_at": "1970-01-01T00:00:00Z",
  "expires_at": "1970-01-01T00:00:00Z"
}
EOF
ok "Placeholder identity written"

# ── Build Linux binary ─────────────────────────────────────────────────────────
step "Building KBT Linux binary (--onefile)"
$PYTHON -m PyInstaller \
    --onefile \
    --clean \
    --noconfirm \
    --name KBT \
    --add-data "collectors:collectors" \
    --add-data "core:core" \
    --add-data "gui:gui" \
    --add-data "transparency:transparency" \
    --add-data "kbt_identity.json:." \
    --hidden-import=psutil \
    --hidden-import=PIL \
    --hidden-import=PIL.Image \
    --hidden-import=PIL.ImageGrab \
    --hidden-import=watchdog \
    --hidden-import=watchdog.observers \
    --hidden-import=watchdog.events \
    --hidden-import=watchdog.observers.inotify \
    --hidden-import=pynput \
    --hidden-import=pynput.keyboard \
    --hidden-import=pynput.mouse \
    --hidden-import=PyQt6 \
    --hidden-import=PyQt6.QtWidgets \
    --hidden-import=PyQt6.QtCore \
    --hidden-import=PyQt6.QtGui \
    --hidden-import=requests \
    --hidden-import=sqlite3 \
    --hidden-import=jose \
    --exclude-module=tkinter \
    kbt_main.py 2>&1 | tail -10

if [[ -f "$DIST_DIR/KBT" ]]; then
    chmod +x "$DIST_DIR/KBT"
    SIZE=$(du -sh "$DIST_DIR/KBT" | cut -f1)
    ok "Linux binary: $DIST_DIR/KBT ($SIZE)"
else
    fail "Linux binary build FAILED — check output above"
fi

echo ""
ok "BUILD COMPLETE"
echo ""
echo "  Base binary:  $DIST_DIR/KBT"
echo ""
echo "  Next steps:"
echo "  1. Run the packager to create per-employee binaries:"
echo "     python scripts/generate_kbt.py \\"
echo "       --employee-id <EMP_ID> \\"
echo "       --api-url <API_URL> \\"
echo "       --admin-token <JWT> \\"
echo "       --base-binary $DIST_DIR/KBT \\"
echo "       --output dist/employees/KBT_<name>"
echo ""
echo "  2. Distribute the output binary + kbt_identity.json to the employee"
echo ""
