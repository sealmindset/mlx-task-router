#!/usr/bin/env bash
# =============================================================================
# MLX Task Router — Restart / Re-enable
# =============================================================================
# Restores the service after it was disabled with disable.sh.
# Re-enables auto-start at boot and starts the service immediately.
#
# Usage:
#   sudo ./restart.sh           # Re-enable and start
#   sudo ./restart.sh --quiet   # Same, no prompts
#
# Disable with: sudo ./disable.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

LABEL="com.sealmindset.mlx-task-router"
PLIST="/Library/LaunchDaemons/${LABEL}.plist"
DISABLED_MARKER="/opt/mlx-task-router/.disabled"
LOG_DIR="/var/log/mlx-task-router"
PORT="8888"

# Read port from config
ENV_FILE="${HOME}/.config/mlx-task-router/.env"
if [[ -f "$ENV_FILE" ]]; then
    CONFIGURED_PORT=$(grep -E '^PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
    [[ -n "$CONFIGURED_PORT" ]] && PORT="$CONFIGURED_PORT"
fi

QUIET=false
[[ "${1:-}" == "--quiet" || "${1:-}" == "-q" ]] && QUIET=true

# ── Require sudo ─────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}This script must be run with sudo:${NC} sudo ./restart.sh"
    exit 1
fi

echo ""
echo -e "${BOLD}MLX Task Router — Restart / Re-enable${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Preflight ────────────────────────────────────────────────────────────────

if [[ ! -f "$PLIST" ]]; then
    echo -e "  ${RED}LaunchDaemon plist not found at $PLIST${NC}"
    echo "  Run install.sh first: sudo ./install.sh"
    exit 1
fi

# ── Show disabled status ────────────────────────────────────────────────────

if [[ -f "$DISABLED_MARKER" ]]; then
    DISABLED_SINCE=$(cat "$DISABLED_MARKER" 2>/dev/null || echo "unknown")
    echo -e "  ${YELLOW}Service was disabled since $DISABLED_SINCE${NC}"
else
    echo -e "  ${DIM}Service was not disabled via disable.sh — performing restart${NC}"
fi

# ── Stop if currently running ────────────────────────────────────────────────

if launchctl list "$LABEL" &>/dev/null 2>&1; then
    echo -n "  Stopping current instance... "
    launchctl bootout system/"$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
    sleep 2
    echo -e "${GREEN}stopped${NC}"
fi

# ── Re-enable auto-start ────────────────────────────────────────────────────

echo -n "  Re-enabling auto-start... "
/usr/libexec/PlistBuddy -c "Delete :Disabled" "$PLIST" 2>/dev/null || true
echo -e "${GREEN}done${NC}"

# ── Remove disabled marker ──────────────────────────────────────────────────

if [[ -f "$DISABLED_MARKER" ]]; then
    rm -f "$DISABLED_MARKER"
fi

# ── Start the service ───────────────────────────────────────────────────────

echo -n "  Starting service... "
launchctl bootstrap system "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null || true
sleep 2

if launchctl list "$LABEL" &>/dev/null 2>&1; then
    echo -e "${GREEN}started${NC}"
else
    echo -e "${RED}failed to start${NC}"
    echo "  Check logs: tail -50 $LOG_DIR/stderr.log"
    exit 1
fi

# ── Wait for health ─────────────────────────────────────────────────────────

echo -e "  ${BLUE}▸${NC} Waiting for server to become healthy..."

HEALTHY=false
for i in $(seq 1 45); do
    if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then
        HEALTHY=true
        break
    fi
    sleep 2
    echo -ne "\r  Waiting... ${i}s"
done
echo -ne "\r                          \r"

if [[ "$HEALTHY" == true ]]; then
    HEALTH=$(curl -sf "http://localhost:$PORT/health" 2>/dev/null || echo "{}")
    MODEL=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('model','unknown'))" 2>/dev/null || echo "unknown")
    STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
    echo -e "  ${GREEN}✓${NC} Server healthy"
    echo "    Status: $STATUS"
    echo "    Model:  $MODEL"
    echo "    URL:    http://localhost:$PORT"
else
    echo -e "  ${YELLOW}⚠${NC} Server started but not yet healthy"
    echo "    Model may still be loading (~30-60s for large models)"
    echo "    Monitor: tail -f $LOG_DIR/stdout.log"
fi

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}${BOLD}Service restored.${NC}"
echo ""
echo "  Auto-start at boot: enabled"
echo "  Proxy URL: http://localhost:$PORT"
echo ""
echo -e "  Disable again with: ${BOLD}sudo ./disable.sh${NC}"
echo ""
