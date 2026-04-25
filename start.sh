#!/usr/bin/env bash
# =============================================================================
# MLX Task Router — Service Management Script
# =============================================================================
# Manages the mlx-task-router LaunchDaemon (system-level service).
#
# Usage:
#   ./start.sh                  # Start the service (default)
#   ./start.sh stop             # Stop the service
#   ./start.sh restart          # Restart the service
#   ./start.sh status           # Show detailed status
#   ./start.sh logs             # Tail live log output
#   ./start.sh health           # Quick health check (JSON)
#   ./start.sh test             # Smoke test all endpoints
#   ./start.sh foreground       # Run in foreground (dev/debug mode)
#   ./start.sh install-check    # Verify installation is correct
#
# Requires: sudo for start/stop/restart (LaunchDaemon operations)
# =============================================================================

set -euo pipefail

# ── Colors ───────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── Constants ────────────────────────────────────────────────────────────────

LABEL="com.sealmindset.mlx-task-router"
PLIST="/Library/LaunchDaemons/${LABEL}.plist"
LOG_DIR="/var/log/mlx-task-router"
INSTALL_DIR="/opt/mlx-task-router"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${HOME}/.config/mlx-task-router"
ENV_FILE="${CONFIG_DIR}/.env"

# Resolve port from config
PORT="8888"
if [[ -f "$ENV_FILE" ]]; then
    CONFIGURED_PORT=$(grep -E '^PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
    [[ -n "$CONFIGURED_PORT" ]] && PORT="$CONFIGURED_PORT"
fi

# Determine which Python / project dir to use
if [[ -d "$INSTALL_DIR/.venv" ]]; then
    PROJECT_DIR="$INSTALL_DIR"
    PYTHON="$INSTALL_DIR/.venv/bin/python3"
elif [[ -d "$SCRIPT_DIR/.venv" ]]; then
    PROJECT_DIR="$SCRIPT_DIR"
    PYTHON="$SCRIPT_DIR/.venv/bin/python3"
else
    echo -e "${RED}No .venv found at $INSTALL_DIR or $SCRIPT_DIR${NC}"
    echo "Run install.sh first, or create a venv: python3 -m venv .venv && .venv/bin/pip install -e ."
    exit 1
fi

# ── Helpers ──────────────────────────────────────────────────────────────────

is_daemon_running() {
    launchctl list "$LABEL" &>/dev/null 2>&1
}

is_port_listening() {
    curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1
}

require_sudo() {
    if [[ $EUID -ne 0 ]]; then
        echo -e "${YELLOW}LaunchDaemon operations require sudo.${NC}"
        echo "  Run: sudo $0 $1"
        exit 1
    fi
}

wait_for_health() {
    local timeout="${1:-60}"
    local elapsed=0
    while [[ $elapsed -lt $timeout ]]; do
        if is_port_listening; then
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
        echo -ne "\r  Waiting for health... ${elapsed}s / ${timeout}s"
    done
    echo ""
    return 1
}

# ── Commands ─────────────────────────────────────────────────────────────────

cmd_start() {
    echo -e "${BOLD}Starting MLX Task Router${NC}"
    echo ""

    if is_daemon_running; then
        echo -e "  ${YELLOW}Service already running${NC}"
        echo "  Use '$0 restart' to restart, or '$0 status' for details"
        return 0
    fi

    if [[ ! -f "$PLIST" ]]; then
        echo -e "  ${RED}LaunchDaemon plist not found at $PLIST${NC}"
        echo "  Run install.sh first: sudo ./install.sh"
        exit 1
    fi

    require_sudo "start"

    echo -e "  ${BLUE}▸${NC} Loading LaunchDaemon..."
    launchctl bootstrap system "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null || true
    sleep 2

    if is_daemon_running; then
        echo -e "  ${GREEN}✓${NC} Service started"
    else
        echo -e "  ${RED}✗${NC} Service failed to start"
        echo "  Check logs: $0 logs"
        exit 1
    fi

    echo -e "  ${BLUE}▸${NC} Waiting for server to become healthy..."
    if wait_for_health 90; then
        echo ""
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
        echo "    Monitor: $0 logs"
    fi
    echo ""
}

cmd_stop() {
    echo -e "${BOLD}Stopping MLX Task Router${NC}"
    echo ""

    if ! is_daemon_running; then
        echo -e "  ${YELLOW}Service is not running${NC}"
        return 0
    fi

    require_sudo "stop"

    echo -e "  ${BLUE}▸${NC} Unloading LaunchDaemon..."
    launchctl bootout system/"$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
    sleep 2

    if ! is_daemon_running; then
        echo -e "  ${GREEN}✓${NC} Service stopped"
    else
        echo -e "  ${YELLOW}⚠${NC} Service may still be shutting down"
        echo "    Check: $0 status"
    fi
    echo ""
}

cmd_restart() {
    echo -e "${BOLD}Restarting MLX Task Router${NC}"
    echo ""

    require_sudo "restart"

    if is_daemon_running; then
        echo -e "  ${BLUE}▸${NC} Stopping..."
        launchctl bootout system/"$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true
        sleep 3
        echo -e "  ${GREEN}✓${NC} Stopped"
    fi

    echo -e "  ${BLUE}▸${NC} Starting..."
    launchctl bootstrap system "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null || true
    sleep 2

    if is_daemon_running; then
        echo -e "  ${GREEN}✓${NC} Service started"
    else
        echo -e "  ${RED}✗${NC} Failed to start"
        echo "    Check: $0 logs"
        exit 1
    fi

    echo -e "  ${BLUE}▸${NC} Waiting for health..."
    if wait_for_health 90; then
        echo ""
        echo -e "  ${GREEN}✓${NC} Server healthy (http://localhost:$PORT)"
    else
        echo ""
        echo -e "  ${YELLOW}⚠${NC} Model still loading — monitor with: $0 logs"
    fi
    echo ""
}

cmd_status() {
    echo ""
    echo -e "${BOLD}MLX Task Router — Status${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # Service
    echo -e "${BOLD}  Service${NC}"
    if is_daemon_running; then
        PID=$(launchctl list "$LABEL" 2>/dev/null | awk 'NR==2{print $1}' || echo "?")
        echo -e "    State:     ${GREEN}running${NC} (PID: $PID)"
    else
        echo -e "    State:     ${RED}stopped${NC}"
    fi
    echo "    Label:     $LABEL"
    echo "    Plist:     $PLIST"
    echo "    Install:   $PROJECT_DIR"
    echo "    Python:    $PYTHON"
    echo ""

    # Config
    echo -e "${BOLD}  Config${NC}"
    if [[ -f "$ENV_FILE" ]]; then
        echo "    File:      $ENV_FILE"
        echo "    Port:      $PORT"
        MODEL=$(grep -E '^MLX_MODEL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "not set")
        echo "    Model:     $MODEL"
    else
        echo -e "    ${YELLOW}No config found at $ENV_FILE${NC}"
    fi
    echo ""

    # Health
    echo -e "${BOLD}  Health${NC}"
    if is_port_listening; then
        HEALTH=$(curl -sf "http://localhost:$PORT/health" 2>/dev/null || echo "{}")
        H_STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
        H_MODEL=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('model','?'))" 2>/dev/null || echo "?")
        H_LOADED=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('model_loaded',False))" 2>/dev/null || echo "?")

        if [[ "$H_STATUS" == "healthy" ]]; then
            echo -e "    Status:    ${GREEN}$H_STATUS${NC}"
        else
            echo -e "    Status:    ${YELLOW}$H_STATUS${NC}"
        fi
        echo "    Model:     $H_MODEL"
        echo "    Loaded:    $H_LOADED"
    else
        echo -e "    Endpoint:  ${RED}not reachable${NC} (http://localhost:$PORT/health)"
    fi
    echo ""

    # Stats
    if is_port_listening && curl -sf "http://localhost:$PORT/stats" >/dev/null 2>&1; then
        STATS=$(curl -sf "http://localhost:$PORT/stats" 2>/dev/null)
        echo -e "${BOLD}  Stats${NC}"
        echo "    Total:     $(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('requests_total',0))" 2>/dev/null) requests"
        echo "    Local:     $(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('requests_local',0))" 2>/dev/null)"
        echo "    Forward:   $(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('requests_forward',0))" 2>/dev/null)"
        echo "    Savings:   $(echo "$STATS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cost_saved_display','\$0.0000'))" 2>/dev/null)"
        echo ""
    fi

    # Logs
    echo -e "${BOLD}  Logs${NC}"
    if [[ -d "$LOG_DIR" ]]; then
        STDOUT_SIZE=$(du -h "$LOG_DIR/stdout.log" 2>/dev/null | cut -f1 || echo "?")
        STDERR_SIZE=$(du -h "$LOG_DIR/stderr.log" 2>/dev/null | cut -f1 || echo "?")
        echo "    stdout:    $LOG_DIR/stdout.log ($STDOUT_SIZE)"
        echo "    stderr:    $LOG_DIR/stderr.log ($STDERR_SIZE)"
        echo ""
        echo -e "  ${DIM}Last 5 log lines:${NC}"
        tail -5 "$LOG_DIR/stdout.log" 2>/dev/null | sed 's/^/    /'
    else
        echo "    Directory: $LOG_DIR (not found)"
    fi
    echo ""
}

cmd_logs() {
    echo -e "${BOLD}MLX Task Router — Live Logs${NC}"
    echo -e "${DIM}(Ctrl+C to exit)${NC}"
    echo ""

    if [[ ! -d "$LOG_DIR" ]]; then
        echo -e "${RED}Log directory not found: $LOG_DIR${NC}"
        echo "Run install.sh first."
        exit 1
    fi

    tail -f "$LOG_DIR/stdout.log" "$LOG_DIR/stderr.log"
}

cmd_health() {
    if is_port_listening; then
        curl -sf "http://localhost:$PORT/health" | python3 -m json.tool 2>/dev/null
    else
        echo -e "${RED}Not reachable${NC} — http://localhost:$PORT"
        exit 1
    fi
}

cmd_test() {
    echo ""
    echo -e "${BOLD}MLX Task Router — Endpoint Smoke Tests${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    PASS=0
    FAIL=0

    _test() {
        local name="$1"
        local url="$2"
        printf "  %-25s " "$name"
        if curl -sf "$url" >/dev/null 2>&1; then
            echo -e "${GREEN}OK${NC}"
            PASS=$((PASS + 1))
        else
            echo -e "${RED}FAIL${NC}"
            FAIL=$((FAIL + 1))
        fi
    }

    _test "Root (/)" "http://localhost:$PORT/"
    _test "Health" "http://localhost:$PORT/health"
    _test "Stats" "http://localhost:$PORT/stats"
    _test "Config" "http://localhost:$PORT/config"
    _test "Perf metrics" "http://localhost:$PORT/perf"
    _test "Cache stats" "http://localhost:$PORT/cache"
    _test "Semantic cache" "http://localhost:$PORT/semantic-cache"
    _test "Feedback" "http://localhost:$PORT/feedback"
    _test "Routing history" "http://localhost:$PORT/routing/history"
    _test "Routing summary" "http://localhost:$PORT/routing/summary"
    _test "Annealing" "http://localhost:$PORT/annealing"
    _test "Watchdog" "http://localhost:$PORT/watchdog"

    echo ""
    echo -e "${BOLD}Message routing test${NC}"
    echo ""

    printf "  %-25s " "POST /v1/messages"
    RESPONSE=$(curl -sf -X POST "http://localhost:$PORT/v1/messages" \
        -H "Content-Type: application/json" \
        -H "x-api-key: test" \
        -H "anthropic-version: 2023-06-01" \
        -d '{"model":"claude-sonnet-4-20250514","max_tokens":50,"messages":[{"role":"user","content":"echo hello"}]}' 2>/dev/null || true)

    if [[ -n "$RESPONSE" ]]; then
        HAS_CONTENT=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if d.get('content') else 'no')" 2>/dev/null || echo "no")
        if [[ "$HAS_CONTENT" == "yes" ]]; then
            echo -e "${GREEN}OK${NC}"
            PASS=$((PASS + 1))
        else
            echo -e "${YELLOW}RESPONSE (no content)${NC}"
            FAIL=$((FAIL + 1))
        fi
    else
        echo -e "${RED}FAIL${NC}"
        FAIL=$((FAIL + 1))
    fi

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    TOTAL=$((PASS + FAIL))
    if [[ $FAIL -eq 0 ]]; then
        echo -e "  ${GREEN}${BOLD}All $TOTAL tests passed${NC}"
    else
        echo -e "  ${YELLOW}${BOLD}$PASS/$TOTAL passed, $FAIL failed${NC}"
    fi
    echo ""
}

cmd_foreground() {
    echo -e "${BOLD}MLX Task Router — Foreground Mode${NC}"
    echo -e "${DIM}(Ctrl+C to stop)${NC}"
    echo ""

    if is_daemon_running; then
        echo -e "${YELLOW}Warning: LaunchDaemon is also running.${NC}"
        echo "This may cause port conflicts. Stop it first: sudo $0 stop"
        echo ""
    fi

    if is_port_listening; then
        echo -e "${RED}Port $PORT is already in use.${NC}"
        exit 1
    fi

    echo "  Python:  $PYTHON"
    echo "  Project: $PROJECT_DIR"
    echo "  Port:    $PORT"
    echo ""

    cd "$PROJECT_DIR"
    exec "$PYTHON" -m mlx_task_router.cli serve
}

cmd_install_check() {
    echo ""
    echo -e "${BOLD}MLX Task Router — Installation Check${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    ERRORS=0

    _check() {
        local name="$1"
        local condition="$2"
        printf "  %-35s " "$name"
        if eval "$condition"; then
            echo -e "${GREEN}OK${NC}"
        else
            echo -e "${RED}MISSING${NC}"
            ERRORS=$((ERRORS + 1))
        fi
    }

    _check "Install directory" "[[ -d '$INSTALL_DIR' ]]"
    _check "Python venv" "[[ -x '$INSTALL_DIR/.venv/bin/python3' ]]"
    _check "mlx-lm in venv" "'$INSTALL_DIR/.venv/bin/python3' -c 'import mlx_lm' 2>/dev/null"
    _check "mlx_task_router in venv" "'$INSTALL_DIR/.venv/bin/python3' -c 'import mlx_task_router' 2>/dev/null"
    _check "Config directory" "[[ -d '$CONFIG_DIR' ]]"
    _check "Config .env file" "[[ -f '$ENV_FILE' ]]"
    _check "ANTHROPIC_API_KEY set" "grep -qE '^ANTHROPIC_API_KEY=sk-ant-' '$ENV_FILE' 2>/dev/null"
    _check "Log directory" "[[ -d '$LOG_DIR' ]]"
    _check "LaunchDaemon plist" "[[ -f '$PLIST' ]]"
    _check "Plist owned by root:wheel" "[[ \$(stat -f '%Su:%Sg' '$PLIST' 2>/dev/null) == 'root:wheel' ]]"
    _check "mlx-router-ctl command" "command -v mlx-router-ctl >/dev/null 2>&1"
    _check "ANTHROPIC_BASE_URL in .zshrc" "grep -qF 'ANTHROPIC_BASE_URL' '$HOME/.zshrc' 2>/dev/null"

    echo ""
    if [[ $ERRORS -eq 0 ]]; then
        echo -e "  ${GREEN}${BOLD}All checks passed${NC}"
    else
        echo -e "  ${YELLOW}${BOLD}$ERRORS issue(s) found${NC}"
        echo "  Run: sudo ./install.sh --upgrade"
    fi
    echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────

case "${1:-start}" in
    start)          cmd_start ;;
    stop)           cmd_stop ;;
    restart)        cmd_restart ;;
    status)         cmd_status ;;
    logs)           cmd_logs ;;
    health)         cmd_health ;;
    test)           cmd_test ;;
    foreground|fg)  cmd_foreground ;;
    install-check)  cmd_install_check ;;
    -h|--help|help)
        echo ""
        echo -e "${BOLD}MLX Task Router — Service Management${NC}"
        echo ""
        echo "Usage: ./start.sh [command]"
        echo ""
        echo "Commands:"
        echo "  start          Start the service (default)"
        echo "  stop           Stop the service"
        echo "  restart        Stop then start the service"
        echo "  status         Show detailed status, health, and stats"
        echo "  logs           Tail live log output (Ctrl+C to exit)"
        echo "  health         Quick health check (JSON)"
        echo "  test           Smoke test all endpoints"
        echo "  foreground     Run in foreground (dev/debug mode)"
        echo "  install-check  Verify installation is correct"
        echo "  help           Show this help message"
        echo ""
        echo "Examples:"
        echo "  sudo ./start.sh              # Start the service"
        echo "  sudo ./start.sh restart      # Restart after config change"
        echo "  ./start.sh status            # Check if running"
        echo "  ./start.sh test              # Test all endpoints"
        echo "  ./start.sh foreground        # Run without launchd (for debugging)"
        echo ""
        ;;
    *)
        echo -e "${RED}Unknown command: $1${NC}"
        echo "Run: ./start.sh help"
        exit 1
        ;;
esac
