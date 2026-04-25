#!/usr/bin/env bash
# =============================================================================
# MLX Task Router — Disable (Temporary Shutdown)
# =============================================================================
# Stops the service and prevents it from auto-starting at boot.
# The LaunchDaemon plist is NOT deleted — just disabled.
#
# Usage:
#   sudo ./disable.sh           # Stop service and disable auto-start
#   sudo ./disable.sh --quiet   # Same, no prompts
#
# Restore with: sudo ./restart.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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
    echo -e "${RED}This script must be run with sudo:${NC} sudo ./disable.sh"
    exit 1
fi

REAL_USER="${SUDO_USER:-$USER}"

echo ""
echo -e "${BOLD}MLX Task Router — Disable${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Check if already disabled ────────────────────────────────────────────────

if [[ -f "$DISABLED_MARKER" ]]; then
    echo -e "  ${YELLOW}Already disabled${NC} (since $(cat "$DISABLED_MARKER"))"
    echo "  Restore with: sudo ./restart.sh"
    echo ""
    exit 0
fi

# ── Stop the service ─────────────────────────────────────────────────────────

echo -n "  Stopping service... "
if launchctl list "$LABEL" &>/dev/null 2>&1; then
    launchctl bootout system/"$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
    sleep 2
    if ! launchctl list "$LABEL" &>/dev/null 2>&1; then
        echo -e "${GREEN}stopped${NC}"
    else
        echo -e "${YELLOW}may still be shutting down${NC}"
    fi
else
    echo -e "already stopped"
fi

# ── Disable auto-start ──────────────────────────────────────────────────────

echo -n "  Disabling auto-start... "
if [[ -f "$PLIST" ]]; then
    # Overwrite the Disabled key in the plist to prevent launchd from loading it
    /usr/libexec/PlistBuddy -c "Delete :Disabled" "$PLIST" 2>/dev/null || true
    /usr/libexec/PlistBuddy -c "Add :Disabled bool true" "$PLIST"
    echo -e "${GREEN}done${NC}"
else
    echo -e "${YELLOW}plist not found at $PLIST${NC}"
fi

# ── Write disabled marker ───────────────────────────────────────────────────

date -u "+%Y-%m-%dT%H:%M:%SZ" > "$DISABLED_MARKER"
chown "$REAL_USER:staff" "$DISABLED_MARKER"

# ── Verify ───────────────────────────────────────────────────────────────────

echo ""
echo -n "  Port $PORT: "
if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then
    echo -e "${YELLOW}still responding (may take a moment to fully stop)${NC}"
else
    echo -e "${GREEN}not listening${NC}"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}${BOLD}Service disabled.${NC}"
echo ""
echo "  The router will NOT start on next boot."
echo "  All traffic will go directly to the Claude API."
echo ""
echo -e "  Restore with: ${BOLD}sudo ./restart.sh${NC}"
echo ""
